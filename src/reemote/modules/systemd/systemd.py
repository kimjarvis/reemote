# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``systemd`` module: facts and operations in one source.
#
# This namespace is the canonical home for systemd visibility and control.
# The per-unit ``SystemdServiceState``/``SystemdServiceEnabled`` facts, the
# ``SystemctlCommand`` descriptor and the ``service`` operation were
# relocated here from ``server`` (which re-exports them for backward
# compatibility and keeps ``server.wait_service``, polling the relocated
# ``SystemdServiceState``). ``unit_name`` is the shared normalization helper
# both sides key on.
#
# Fact side: ``SystemdServiceState`` (a unit's raw ``ActiveState`` string, or
# None when systemd answers nothing) and ``SystemdServiceEnabled`` (whether a
# unit's ``UnitFileState`` counts as enabled) are the per-unit probes the
# ``service`` operation reads for idempotence. ``SystemdStatus`` and
# ``SystemdEnabled`` are the two fleet-wide facts (the unit -> active
# bool and unit -> enabled bool parsed from ``systemctl show --all``).
# Seven reemote originals widen the visibility: ``SystemdUnitProperties``
# (a unit's curated ``systemctl show`` properties as raw strings),
# ``SystemdFailedUnits`` (the ``systemctl --failed`` table),
# ``SystemdTimers`` (the ``*.timer`` schedule and last/next trigger),
# ``SystemdUnitFiles`` (the ``list-unit-files`` state/preset table),
# ``SystemdVersion`` (the ``systemctl --version`` release and features),
# ``SystemdSystemState`` (the ``is-system-running`` health word) and
# ``SystemdDependencies`` (a unit's Requires/Wants/After/Before/Conflicts).
# Like every fact they are phase-unaware: their ``process`` coroutine just
# gathers the raw value.
#
# Operation side: ``service`` manages a unit through
# the ``SystemctlCommand`` descriptor, reading
# ``SystemdServiceState``/``SystemdServiceEnabled`` for idempotence, and
# ``daemon_reload`` runs a stateless reload. Six reemote originals
# widen the control surface: ``mask``/``reset_failed``/``preset`` (systemctl
# verbs through ``SystemctlCommand``) and ``dropin``/``tmpfiles``/``sysusers``
# (root-owned ``/etc`` config writes through the spool-transfer +
# elevated-install ``SystemdFileUploadCommand``/``SystemdFileInstallCommand``/
# ``SystemdFileRemoveCommand`` descriptors plus the ``SystemdTmpfilesCommand``/
# ``SystemdSysusersCommand`` apply steps, made idempotent by the private
# in-namespace ``_SystemdConfFile`` content fact). Operations are async
# generators that receive an ``OpContext``, await facts, set
# ``ctx.changed``/``ctx.reason`` and yield command descriptors; they never touch
# the host directly.


import hashlib
import io
import re
import shlex
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, TypedDict, cast

from ...commands import Command, TransferCommand
from ...core import OpContext
from ...hosts import Host

#: Unit suffixes systemd recognises; a bare name gets ``.service`` appended
#: (the ``service`` operation's name normalization).
_UNIT_SUFFIXES = (
    ".service",
    ".socket",
    ".device",
    ".mount",
    ".automount",
    ".swap",
    ".target",
    ".path",
    ".timer",
    ".slice",
    ".scope",
)


def unit_name(service: str) -> str:
    """Normalize a service name to a full systemd unit name.

    ``httpd`` becomes ``httpd.service``; a name already carrying a systemd
    unit suffix (``logrotate.timer``, ``httpd.service``) is returned as is.
    Both the facts and the operations key on the normalized name, so the
    test framework's simulated state must use full unit names too.
    """
    if service.endswith(_UNIT_SUFFIXES):
        return service
    return f"{service}.service"


def _systemctl_cmd(
    user_mode: bool = False,
    machine: str | None = None,
    user_name: str | None = None,
) -> str:
    """Render the ``systemctl`` base command for a scope.

    ``systemctl`` normally, ``systemctl --user`` for a per-user manager. A
    ``machine`` adds ``--machine=<machine>`` (or ``--machine=<user>@<machine>``
    when a ``user_name`` is also given); a lone ``user_name`` targets that
    user's session on the local host via ``--machine=<user>@.host``. The
    machine/user values are interpolated verbatim.
    """
    cmd = "systemctl --user" if user_mode else "systemctl"
    if machine is not None:
        if user_name is not None:
            cmd += f" --machine={user_name}@{machine}"
        else:
            cmd += f" --machine={machine}"
    elif user_name is not None:
        cmd += f" --machine={user_name}@.host"
    return cmd


# The unit-name validation regex: a name prefix of ASCII
# letters, digits, ``: - _ . \ @`` then a dot and a recognised unit suffix.
# ``SystemdStatus``/``SystemdEnabled`` validate each ``Id=`` value against it
# so a stray line never becomes a unit key.
_UNIT_NAME_RE = re.compile(
    r"[a-zA-Z0-9\:\-\_\.\\\@]+\."
    r"(?:service|socket|device|mount|automount|swap|target|path|timer|slice|scope)"
)

#: The curated ``systemctl show`` properties ``SystemdUnitProperties`` asks
#: for -- the identity, load/active/sub states, boot setting, the unit file
#: path and the main PID. A fixed, useful subset rather than the hundreds of
#: properties a bare ``systemctl show`` dumps; the values stay raw strings.
_UNIT_PROPERTIES = (
    "Id",
    "Description",
    "LoadState",
    "ActiveState",
    "SubState",
    "UnitFileState",
    "FragmentPath",
    "MainPID",
)

#: The dependency properties ``SystemdDependencies`` reads. Each value is a
#: whitespace-separated list of unit names systemd prints for the property.
_DEPENDENCY_PROPERTIES = ("Requires", "Wants", "After", "Before", "Conflicts")

# ``systemctl --version``'s first line: ``systemd 255 (255.4-1ubuntu8.6)`` --
# the upstream integer version and the distribution release in parentheses.
_SYSTEMD_VERSION_RE = re.compile(r"systemd (\d+) \((.*)\)")


# --- Facts -------------------------------------------------------------------


class SystemdServiceState:
    """A unit's systemd ``ActiveState`` (``active``, ``inactive``, ...), or None.

    **Commands:**

    ```bash
    systemctl show -p ActiveState --value <unit>
    ```

    **Returns:**

    ```python
    str | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/wait_for_httpd.py``.
    """

    # ``None`` means systemd reported nothing for the unit (``systemctl`` is
    # missing or failed); an unknown unit reports ``inactive``, as systemd
    # does. The value is the raw state string so a predicate (``is_active``)
    # can decide what counts as "ready" -- ``activating`` is deliberately not
    # ``active``, which is exactly what ``server.wait_service`` waits through.
    # The scope flags (``user_mode``/``machine``/``user_name``) select which
    # systemd manager to query; the SimulatedHost seam keeps a single
    # system-scope ``services`` store, so a simulated read ignores them (a
    # documented approximation -- the real transport renders them faithfully).

    @classmethod
    def command(
        cls,
        service: str,
        user_mode: bool = False,
        machine: str | None = None,
        user_name: str | None = None,
    ) -> str:
        """The ``systemctl show`` command for a unit's ``ActiveState``."""
        unit = unit_name(service)
        base = _systemctl_cmd(user_mode, machine, user_name)
        return f"{base} show -p ActiveState --value {shlex.quote(unit)}"

    @classmethod
    async def process(
        cls,
        host: Host,
        service: str,
        user_mode: bool = False,
        machine: str | None = None,
        user_name: str | None = None,
    ) -> str | None:
        unit = unit_name(service)
        services = getattr(host, "services", None)
        if services is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return services.get(unit)
        # Real transport: `systemctl show` over asyncssh. A failure exits
        # non-zero with empty stdout (check is off in run_shell), which maps
        # to None just like the simulated absent-unit case.
        stdout = await host.run_shell(cls.command(service, user_mode, machine, user_name))
        return stdout.strip() or None


class SystemdServiceEnabled:
    """Whether a unit is enabled on boot.

    **Commands:**

    ```bash
    systemctl show -p UnitFileState --value <unit>
    ```

    **Returns:**

    ```python
    bool
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/wait_for_httpd.py``.
    """

    # The unit's ``UnitFileState`` is read and ``enabled`` or ``static`` count
    # as enabled; anything else (``disabled``, ``masked``, an empty answer)
    # does not.

    #: ``UnitFileState`` values that count as enabled.
    ENABLED_STATES = ("enabled", "static")

    @classmethod
    def command(
        cls,
        service: str,
        user_mode: bool = False,
        machine: str | None = None,
        user_name: str | None = None,
    ) -> str:
        """The ``systemctl show`` command for a unit's ``UnitFileState``."""
        unit = unit_name(service)
        base = _systemctl_cmd(user_mode, machine, user_name)
        return f"{base} show -p UnitFileState --value {shlex.quote(unit)}"

    @classmethod
    async def process(
        cls,
        host: Host,
        service: str,
        user_mode: bool = False,
        machine: str | None = None,
        user_name: str | None = None,
    ) -> bool:
        unit = unit_name(service)
        enabled_services = getattr(host, "enabled_services", None)
        if enabled_services is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return unit in enabled_services
        # Real transport: `systemctl show` over asyncssh; a failure's empty
        # stdout maps to False.
        stdout = await host.run_shell(cls.command(service, user_mode, machine, user_name))
        return stdout.strip() in cls.ENABLED_STATES


class SystemdStatus:
    """Whether each systemd unit is active, as a dict of unit name -> bool.

    **Commands:**

    ```bash
    systemctl show --all --property Id --property SubState '*'
    ```

    **Returns:**

    ```python
    dict[str, bool]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/systemd/systemd_systemd_status.py``.
    """

    # ``systemctl show --all`` prints ``Id=<unit>`` / ``SubState=<state>``
    # pairs (blank-line separated),
    # and a unit reads True when its SubState is one of ``state_values``
    # (``running``, ``waiting``, ``exited``, ``listening``, ``mounted``,
    # ``active``, ``plugged``) -- so a oneshot service that ``exited`` cleanly
    # counts as active, while ``dead``/``failed`` do not. Each ``Id`` value is
    # validated against ``_UNIT_NAME_RE``; a line with no ``=`` resets the
    # current unit, so a stray
    # banner can never be paired with the next unit's state.
    #
    # The ``services`` argument narrows the query (a name, a list of names, or
    # None for the ``'*'`` glob); ``user_mode`` wraps the command in
    # ``( ... 2>/dev/null || true )`` so a not-yet-available user manager bus
    # does not fail the prepare phase. The ``machine``/``user_name`` scope
    # flags render exactly as the ``_systemctl_cmd`` helper does.
    #
    # SimulatedHost seam note: the ``services`` field stores each unit's raw
    # ``ActiveState`` string (the ``SystemdServiceState`` shape), and this fact
    # projects it to the active bool (``ActiveState == "active"``). That is a
    # documented simulation approximation -- the real transport parses the
    # richer ``SubState`` faithfully, so an ``exited`` oneshot reads True over
    # SSH but a simulated ``inactive`` reads False. The projection ignores the
    # scope flags (one system-scope store).

    #: ``SubState`` values that count as active.
    state_key = "SubState"
    state_values = (
        "running",
        "waiting",
        "exited",
        "listening",
        "mounted",
        "active",
        "plugged",
    )

    @classmethod
    def command(
        cls,
        user_mode: bool = False,
        machine: str | None = None,
        user_name: str | None = None,
        services: str | list[str] | tuple[str, ...] | None = None,
    ) -> str:
        """The ``systemctl show --all`` command for the active-state probe."""
        base = _systemctl_cmd(user_mode, machine, user_name)
        if services is None:
            patterns = [shlex.quote("*")]
        elif isinstance(services, str):
            patterns = [shlex.quote(services)]
        else:
            patterns = [shlex.quote(s) for s in services]
        cmd = (
            f"{base} show --all --property Id --property {cls.state_key} "
            + " ".join(patterns)
        )
        if user_mode:
            return f"( {cmd} 2>/dev/null || true )"
        return cmd

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, bool]:
        """Parse ``systemctl show`` Id/state pairs into unit -> bool. Pure/testable.

        An ``Id=`` line whose value matches the unit-name regex starts a new
        unit; a ``<state_key>=`` line records its bool. A line with no ``=``
        resets the current unit so a stray line cannot be mispaired.
        """
        services: dict[str, bool] = {}
        current_unit: str | None = None
        for line in output:
            key, sep, value = line.strip().partition("=")
            if not sep:
                current_unit = None
                continue
            if key == "Id" and _UNIT_NAME_RE.match(value):
                current_unit = value
                continue
            if key == cls.state_key and current_unit:
                services[current_unit] = value in cls.state_values
        return services

    @classmethod
    async def process(
        cls,
        host: Host,
        user_mode: bool = False,
        machine: str | None = None,
        user_name: str | None = None,
        services: str | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, bool]:
        state = getattr(host, "services", None)
        if state is not None:
            # SimulatedHost seam: project the raw ActiveState store to bools.
            return {unit: active == "active" for unit, active in state.items()}
        # Real transport: `systemctl show --all` over asyncssh.
        stdout = await host.run_shell(cls.command(user_mode, machine, user_name, services))
        return cls.parse(stdout.splitlines())


class SystemdEnabled(SystemdStatus):
    """Whether each systemd unit is enabled on boot, as a dict of unit -> bool.

    **Commands:**

    ```bash
    systemctl show --all --property Id --property UnitFileState '*'
    ```

    **Returns:**

    ```python
    dict[str, bool]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/systemd/systemd_systemd_enabled.py``.
    """

    # A subclass of ``SystemdStatus`` flipping only ``state_key`` to
    # ``UnitFileState`` and ``state_values`` to (``enabled``, ``static``) -- a
    # ``static`` unit has no
    # [Install] section but is still pulled in by its dependents, so it
    # counts as enabled. ``command``/``parse`` are inherited verbatim.
    #
    # SimulatedHost seam note: the ``enabled_services`` set holds the unit
    # names enabled on boot; this fact projects membership over the union of
    # that set and the ``services`` store's keys (the simulated universe of
    # known units), a documented approximation of the real ``UnitFileState``
    # parse. The scope flags are ignored by the projection.

    state_key = "UnitFileState"
    state_values = ("enabled", "static")

    @classmethod
    async def process(
        cls,
        host: Host,
        user_mode: bool = False,
        machine: str | None = None,
        user_name: str | None = None,
        services: str | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, bool]:
        enabled = getattr(host, "enabled_services", None)
        if enabled is not None:
            # SimulatedHost seam: project membership over the known units.
            known = getattr(host, "services", None) or {}
            units = set(known) | set(enabled)
            return {unit: unit in enabled for unit in units}
        # Real transport: `systemctl show --all` over asyncssh.
        stdout = await host.run_shell(cls.command(user_mode, machine, user_name, services))
        return cls.parse(stdout.splitlines())


class SystemdUnitProperties:
    """A unit's curated ``systemctl show`` properties, as raw strings.

    **Commands:**

    ```bash
    systemctl show <unit> --property=Id,Description,LoadState,ActiveState,SubState,UnitFileState,FragmentPath,MainPID
    ```

    **Returns:**

    ```python
    dict[str, str]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/systemd/systemd_systemd_unit_properties.py``.
    """

    # A reemote original: where ``SystemdServiceState`` reads one property,
    # this reads a curated, useful subset (``_UNIT_PROPERTIES``) so a caller
    # can see a unit's description, load/active/sub states, boot setting,
    # fragment path and main PID in one shot. ``systemctl show`` is preferred
    # over parsing ``systemctl status`` text: it prints stable ``Key=Value``
    # lines. Every value stays the raw string systemd printed (``MainPID`` is
    # ``"0"`` for a stopped unit, not an int) -- the dict keeps plain string
    # keys because the property set is a fixed tool vocabulary, the agreed
    # dynamic-key exception to the "facts return TypedDicts" rule (like
    # ``CpuInfo``). A bare name is normalized with ``unit_name``.
    #
    # SimulatedHost seam: ``systemd_unit_properties`` maps a full unit name to
    # its property dict; an absent unit reads as {} (the real command prints
    # nothing for a unit systemd cannot load). The scope flags are ignored by
    # the single system-scope store -- a documented simulation approximation;
    # the real transport renders them faithfully.

    @classmethod
    def command(
        cls,
        unit: str,
        user_mode: bool = False,
        machine: str | None = None,
        user_name: str | None = None,
    ) -> str:
        """The ``systemctl show`` command for a unit's curated properties."""
        base = _systemctl_cmd(user_mode, machine, user_name)
        props = ",".join(_UNIT_PROPERTIES)
        return f"{base} show {shlex.quote(unit_name(unit))} --property={props}"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, str]:
        """Parse ``Key=Value`` property lines into a raw dict. Pure/testable.

        Each line splits on the first ``=``; a line without one is skipped.
        Empty output (a unit systemd cannot load) reads as {}.
        """
        properties: dict[str, str] = {}
        for line in output:
            key, sep, value = line.strip().partition("=")
            if sep:
                properties[key] = value
        return properties

    @classmethod
    async def process(
        cls,
        host: Host,
        unit: str,
        user_mode: bool = False,
        machine: str | None = None,
        user_name: str | None = None,
    ) -> dict[str, str]:
        state = getattr(host, "systemd_unit_properties", None)
        if state is not None:
            # SimulatedHost seam: copy the unit's property dict so callers
            # never mutate the simulated state.
            return dict(state.get(unit_name(unit), {}))
        # Real transport: `systemctl show` over asyncssh.
        stdout = await host.run_shell(cls.command(unit, user_mode, machine, user_name))
        return cls.parse(stdout.splitlines())


class SystemdFailedUnit(TypedDict):
    """
    Attributes:
        unit: The failed unit's full name (e.g. ``httpd.service``).
        load: The unit's load state (``loaded``, ``not-found``, ...).
        active: The unit's active state (``failed`` for a failed unit).
        sub: The unit's sub-state (``failed``, ``dead``, ...).
        description: The unit's description, the table's trailing column.
    """

    # One row of ``systemctl --failed`` (``SystemdFailedUnits``' entry), as a
    # plain dict so equality against a literal and ``json.dumps`` both work.
    # Every value stays the raw string the table printed.

    unit: str
    load: str
    active: str
    sub: str
    description: str


class SystemdFailedUnits:
    """The failed systemd units, as a list of ``SystemdFailedUnit`` dicts.

    **Commands:**

    ```bash
    systemctl --failed --no-legend --no-pager
    ```

    **Returns:**

    ```python
    list[SystemdFailedUnit]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/systemd/systemd_systemd_failed_units.py``.
    """

    # A reemote original: ``systemctl --failed --no-legend --no-pager`` prints
    # one row per failed unit -- ``UNIT LOAD ACTIVE SUB DESCRIPTION`` -- with
    # a leading ``\u25cf`` bullet on the unit name. ``--no-legend`` drops the
    # header and the footer count, so every non-blank line is a row. Each row
    # splits into at most five fields (``split(None, 4)``) so the description
    # keeps its internal spaces; a row with fewer than four columns is
    # skipped. A healthy host prints nothing and reads as [].
    #
    # SimulatedHost seam: ``systemd_failed_units`` holds the already-parsed
    # list of entries; the read copies each dict so callers never mutate the
    # simulated state. The scope flags are ignored by the single store -- a
    # documented approximation; the real transport renders them faithfully.

    @classmethod
    def command(
        cls,
        user_mode: bool = False,
        machine: str | None = None,
        user_name: str | None = None,
    ) -> str:
        """The ``systemctl --failed`` command for the failed-unit table."""
        base = _systemctl_cmd(user_mode, machine, user_name)
        return f"{base} --failed --no-legend --no-pager"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[SystemdFailedUnit]:
        """Parse ``systemctl --failed`` rows into entry dicts. Pure/testable.

        The leading bullet is stripped, then the row splits into five fields
        (the description keeps its spaces); a short row is skipped.
        """
        units: list[SystemdFailedUnit] = []
        for line in output:
            row = line.strip()
            if not row:
                continue
            if row.startswith("\u25cf"):
                row = row[1:].strip()
            parts = row.split(None, 4)
            if len(parts) < 4:
                continue
            units.append(
                {
                    "unit": parts[0],
                    "load": parts[1],
                    "active": parts[2],
                    "sub": parts[3],
                    "description": parts[4] if len(parts) > 4 else "",
                }
            )
        return units

    @classmethod
    async def process(
        cls,
        host: Host,
        user_mode: bool = False,
        machine: str | None = None,
        user_name: str | None = None,
    ) -> list[SystemdFailedUnit]:
        state = getattr(host, "systemd_failed_units", None)
        if state is not None:
            # SimulatedHost seam: copy each entry dict (a TypedDict IS a plain
            # dict at runtime) so callers never mutate the simulated state.
            return [cast("SystemdFailedUnit", dict(entry)) for entry in state]
        # Real transport: `systemctl --failed` over asyncssh.
        stdout = await host.run_shell(cls.command(user_mode, machine, user_name))
        return cls.parse(stdout.splitlines())


class SystemdTimer(TypedDict):
    """
    Attributes:
        active_state: The timer's ``ActiveState`` (``active``, ``inactive``).
        unit_file_state: The timer's ``UnitFileState`` (``enabled``, ...).
        next_elapse_usec: The next elapse time in microseconds since the
            epoch, or None when systemd reports ``0``/empty (no next run) or
            a non-integer timestamp string.
        last_trigger_usec: The last trigger time in microseconds since the
            epoch, or None when the timer has never fired (``0``/empty) or
            systemd renders the property as a date string.
    """

    # One ``*.timer`` unit (``SystemdTimers``' entry). The two timestamps are
    # the only converted values: systemd's ``...USec`` properties are integer
    # microseconds, and ``0``/empty means "never"/"not scheduled", mapped to
    # None so a caller can test it directly. Some systemd versions render a
    # timestamp property as a locale-formatted date string instead of the raw
    # integer; that non-machine-readable form maps to None too (never raises).

    active_state: str
    unit_file_state: str
    next_elapse_usec: int | None
    last_trigger_usec: int | None


class SystemdTimers:
    """The ``*.timer`` units and their schedule, as a dict of unit -> ``SystemdTimer``.

    **Commands:**

    ```bash
    systemctl show '*.timer' --property=Id,ActiveState,UnitFileState,NextElapseUSecRealtime,LastTriggerUSec
    ```

    **Returns:**

    ```python
    dict[str, SystemdTimer]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/systemd/systemd_systemd_timers.py``.
    """

    # A reemote original. The proposal suggested ``systemctl list-timers``
    # but that prints a human-readable table whose ``NEXT``/``LEFT`` columns
    # are relative, locale-formatted strings -- fragile to parse. This uses
    # the machine-readable ``systemctl show '*.timer'`` instead (a documented
    # deviation): it prints blank-line-separated ``Key=Value`` blocks, one per
    # timer, with the absolute ``...USec`` microsecond timestamps. A block's
    # ``Id`` keys the entry; ``0``/empty timestamps map to None, as does a
    # locale-formatted date string some systemd versions print instead of the
    # raw integer (e.g. ``LastTriggerUSec=Sat 2026-11-07 05:52:09 CET``).
    #
    # SimulatedHost seam: ``systemd_timers`` maps a timer unit name to its
    # entry dict; the read copies each dict. The scope flags are ignored by
    # the single store -- a documented approximation.

    @classmethod
    def command(
        cls,
        user_mode: bool = False,
        machine: str | None = None,
        user_name: str | None = None,
    ) -> str:
        """The ``systemctl show '*.timer'`` command for the timer schedule."""
        base = _systemctl_cmd(user_mode, machine, user_name)
        props = "Id,ActiveState,UnitFileState,NextElapseUSecRealtime,LastTriggerUSec"
        return f"{base} show {shlex.quote('*.timer')} --property={props}"

    @staticmethod
    def _usec(value: str | None) -> int | None:
        """Convert a ``...USec`` property to an int, or None when it carries none.

        ``0``/empty map to None. Some systemd versions render a timestamp
        property as a locale-formatted date string (e.g.
        ``LastTriggerUSec=Sat 2026-11-07 05:52:09 CET``) instead of the raw
        microsecond integer; that human-readable form is not machine-readable,
        so it maps to None too rather than raising ``ValueError``.
        """
        if not value or not value.isdigit():
            return None
        return int(value) or None

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, SystemdTimer]:
        """Parse blank-line-separated ``show`` blocks into unit entries. Pure.

        Each block's ``Id`` keys the entry; a block without one is skipped.
        The two ``...USec`` timestamps convert to int (``0``/empty/non-integer
        -> None).
        """
        timers: dict[str, SystemdTimer] = {}
        block: dict[str, str] = {}

        def flush() -> None:
            unit = block.get("Id")
            if unit:
                timers[unit] = {
                    "active_state": block.get("ActiveState", ""),
                    "unit_file_state": block.get("UnitFileState", ""),
                    "next_elapse_usec": cls._usec(block.get("NextElapseUSecRealtime")),
                    "last_trigger_usec": cls._usec(block.get("LastTriggerUSec")),
                }
            block.clear()

        for line in output:
            stripped = line.strip()
            if not stripped:
                flush()
                continue
            key, sep, value = stripped.partition("=")
            if sep:
                block[key] = value
        flush()
        return timers

    @classmethod
    async def process(
        cls,
        host: Host,
        user_mode: bool = False,
        machine: str | None = None,
        user_name: str | None = None,
    ) -> dict[str, SystemdTimer]:
        state = getattr(host, "systemd_timers", None)
        if state is not None:
            # SimulatedHost seam: copy each entry dict so callers never mutate
            # the simulated state.
            return {unit: cast("SystemdTimer", dict(info)) for unit, info in state.items()}
        # Real transport: `systemctl show '*.timer'` over asyncssh.
        stdout = await host.run_shell(cls.command(user_mode, machine, user_name))
        return cls.parse(stdout.splitlines())


class SystemdUnitFile(TypedDict):
    """
    Attributes:
        state: The unit file's state (``enabled``, ``disabled``, ``static``,
            ``masked``, ``generated``, ``transient``, ...).
        preset: The unit file's preset policy (``enabled``, ``disabled``), or
            the empty string when the table prints no preset column.
    """

    # One row of ``systemctl list-unit-files`` (``SystemdUnitFiles``' entry).
    # Both values stay raw strings; ``preset`` is "" when the row carries only
    # a state (older systemd, or a unit with no preset policy).

    state: str
    preset: str


class SystemdUnitFiles:
    """The installed unit files and their state/preset, as a dict of unit -> ``SystemdUnitFile``.

    **Commands:**

    ```bash
    systemctl list-unit-files --no-legend --no-pager
    ```

    **Returns:**

    ```python
    dict[str, SystemdUnitFile]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/systemd/systemd_systemd_unit_files.py``.
    """

    # A reemote original: ``list-unit-files --no-legend --no-pager`` prints
    # ``UNIT FILE STATE PRESET`` rows (the header/footer legend suppressed).
    # Each row's first token is validated against ``_UNIT_NAME_RE`` so a
    # stray summary line (``2 unit files listed.``) never becomes a key; the
    # second token is the state and the optional third the preset ("" when
    # absent). This is the boot-time view -- unlike ``SystemdEnabled`` (which
    # collapses to a bool) it keeps the raw state, so ``masked``/``static``/
    # ``generated`` stay distinguishable. The ``preset`` operation reads it.
    #
    # SimulatedHost seam: ``systemd_unit_files`` maps a unit name to its entry
    # dict; the read copies each dict. The scope flags are ignored by the
    # single store -- a documented approximation.

    @classmethod
    def command(
        cls,
        user_mode: bool = False,
        machine: str | None = None,
        user_name: str | None = None,
    ) -> str:
        """The ``systemctl list-unit-files`` command for the state/preset table."""
        base = _systemctl_cmd(user_mode, machine, user_name)
        return f"{base} list-unit-files --no-legend --no-pager"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, SystemdUnitFile]:
        """Parse ``list-unit-files`` rows into unit -> state/preset. Pure.

        A row whose first token is not a unit name (the footer count) is
        skipped; the preset column is "" when the row carries only a state.
        """
        files: dict[str, SystemdUnitFile] = {}
        for line in output:
            parts = line.split()
            if not parts or not _UNIT_NAME_RE.match(parts[0]):
                continue
            files[parts[0]] = {
                "state": parts[1] if len(parts) > 1 else "",
                "preset": parts[2] if len(parts) > 2 else "",
            }
        return files

    @classmethod
    async def process(
        cls,
        host: Host,
        user_mode: bool = False,
        machine: str | None = None,
        user_name: str | None = None,
    ) -> dict[str, SystemdUnitFile]:
        state = getattr(host, "systemd_unit_files", None)
        if state is not None:
            # SimulatedHost seam: copy each entry dict so callers never mutate
            # the simulated state.
            return {unit: cast("SystemdUnitFile", dict(info)) for unit, info in state.items()}
        # Real transport: `systemctl list-unit-files` over asyncssh.
        stdout = await host.run_shell(cls.command(user_mode, machine, user_name))
        return cls.parse(stdout.splitlines())


class SystemdVersionInfo(TypedDict):
    """
    Attributes:
        version: The upstream systemd integer version (e.g. ``255``), or None
            when ``systemctl --version`` could not be read or parsed.
        release: The distribution release string in parentheses (e.g.
            ``255.4-1ubuntu8.6``), or the empty string when absent.
        features: The compiled-in feature flags line (e.g.
            ``+PAM +AUDIT +SELINUX ...``), or the empty string when absent.
    """

    # The parsed ``systemctl --version`` banner (``SystemdVersion``'s entry).
    # ``version`` is the only converted value (the upstream integer); the
    # release and features stay raw strings.

    version: int | None
    release: str
    features: str


class SystemdVersion:
    """The systemd release and compiled-in features, as a ``SystemdVersionInfo`` dict.

    **Commands:**

    ```bash
    systemctl --version
    ```

    **Returns:**

    ```python
    SystemdVersionInfo
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/systemd/systemd_systemd_version.py``.
    """

    # A reemote original: ``systemctl --version`` prints ``systemd 255
    # (255.4-1ubuntu8.6)`` on the first line and the ``+PAM +AUDIT ...``
    # feature flags on the second. The first line is matched by
    # ``_SYSTEMD_VERSION_RE`` (the integer version and the parenthesised
    # release); the second line is kept verbatim as ``features``. Empty or
    # unparseable output reads as {version: None, release: "", features: ""}.
    # The version is global to the machine, so this fact takes no scope flags.
    #
    # SimulatedHost seam: ``systemd_version`` holds the entry dict (default
    # {} -> version None, a simulated host whose systemctl could not be read);
    # the read normalizes a partial dict to the full three-key shape.

    #: Shell used to gather the systemd version banner over SSH.
    COMMAND = "systemctl --version"

    @classmethod
    def parse(cls, output: Iterable[str]) -> SystemdVersionInfo:
        """Parse the ``systemctl --version`` banner into the info dict. Pure.

        The first line yields the integer version and release; the second the
        features. Empty/unmatched output reads as version None.
        """
        lines = [line.strip() for line in output]
        version: int | None = None
        release = ""
        features = ""
        if lines:
            matches = _SYSTEMD_VERSION_RE.match(lines[0])
            if matches:
                version = int(matches.group(1))
                release = matches.group(2)
            if len(lines) > 1:
                features = lines[1]
        return {"version": version, "release": release, "features": features}

    @classmethod
    async def process(cls, host: Host) -> SystemdVersionInfo:
        state = getattr(host, "systemd_version", None)
        if state is not None:
            # SimulatedHost seam: normalize a (possibly partial/empty) dict to
            # the full shape so an empty default reads as version None.
            return {
                "version": state.get("version"),
                "release": state.get("release", ""),
                "features": state.get("features", ""),
            }
        # Real transport: `systemctl --version` over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class SystemdSystemState:
    """The system manager's overall health word (``running``, ``degraded``, ...).

    **Commands:**

    ```bash
    systemctl is-system-running || true
    ```

    **Returns:**

    ```python
    str
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/systemd/systemd_systemd_system_state.py``.
    """

    # A reemote original: ``systemctl is-system-running`` prints one word --
    # ``running``, ``degraded`` (a unit failed), ``maintenance``, ``starting``
    # or ``stopping`` -- and exits non-zero for anything but ``running``. The
    # ``|| true`` swallows that exit so a degraded host still answers in the
    # prepare phase rather than failing it. The first non-blank line is the
    # answer; empty output (no systemd, or a failed read) reads as ``unknown``.
    # This scalar fact deliberately maps empty to ``unknown`` rather than the
    # house scalar template's "" -- the word is a health verdict and
    # ``unknown`` is the meaningful "could not tell" answer (a documented
    # deviation from the ``Hostname``/``Kernel`` empty-string default).
    #
    # SimulatedHost seam: ``systemd_system_state`` holds the word (default ""
    # -> ``unknown``). The scope flags are ignored by the single store -- a
    # documented approximation.

    @classmethod
    def command(
        cls,
        user_mode: bool = False,
        machine: str | None = None,
        user_name: str | None = None,
    ) -> str:
        """The ``systemctl is-system-running`` command for the health word."""
        base = _systemctl_cmd(user_mode, machine, user_name)
        return f"{base} is-system-running || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> str:
        """Return the first non-blank health word, or ``unknown``. Pure."""
        for line in output:
            word = line.strip()
            if word:
                return word
        return "unknown"

    @classmethod
    async def process(
        cls,
        host: Host,
        user_mode: bool = False,
        machine: str | None = None,
        user_name: str | None = None,
    ) -> str:
        state = getattr(host, "systemd_system_state", None)
        if state is not None:
            # SimulatedHost seam: the empty default reads as ``unknown``.
            return state or "unknown"
        # Real transport: `systemctl is-system-running` over asyncssh.
        stdout = await host.run_shell(cls.command(user_mode, machine, user_name))
        return cls.parse(stdout.splitlines())


class SystemdDependencies:
    """A unit's dependency edges, as a dict of property -> list of unit names.

    **Commands:**

    ```bash
    systemctl show <unit> --property=Requires,Wants,After,Before,Conflicts
    ```

    **Returns:**

    ```python
    dict[str, list[str]]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/systemd/systemd_systemd_dependencies.py``.
    """

    # A reemote original: ``systemctl show`` prints the five dependency
    # properties (``_DEPENDENCY_PROPERTIES``) as whitespace-separated unit
    # lists -- ``Requires=network.target`` etc. Each value splits into a list
    # (an empty value yields []). Only the five known keys are kept, so a
    # stray line never adds an edge. The dict stays plain (``dict[str,
    # list[str]]``): the keys are a fixed property vocabulary and the values
    # are name lists, the agreed scalar-list-projection exception (like
    # ``Ipv4Addrs``). A bare name is normalized with ``unit_name``.
    #
    # SimulatedHost seam: ``systemd_dependencies`` maps a full unit name to its
    # property -> list dict; an absent unit reads as {}. The read copies each
    # list. The scope flags are ignored by the single store -- a documented
    # approximation.

    @classmethod
    def command(
        cls,
        unit: str,
        user_mode: bool = False,
        machine: str | None = None,
        user_name: str | None = None,
    ) -> str:
        """The ``systemctl show`` command for a unit's dependency properties."""
        base = _systemctl_cmd(user_mode, machine, user_name)
        props = ",".join(_DEPENDENCY_PROPERTIES)
        return f"{base} show {shlex.quote(unit_name(unit))} --property={props}"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, list[str]]:
        """Parse the dependency ``Key=Value`` lines into unit lists. Pure.

        Only the five ``_DEPENDENCY_PROPERTIES`` keys are kept; each value
        splits on whitespace (an empty value yields []).
        """
        deps: dict[str, list[str]] = {}
        for line in output:
            key, sep, value = line.strip().partition("=")
            if sep and key in _DEPENDENCY_PROPERTIES:
                deps[key] = value.split()
        return deps

    @classmethod
    async def process(
        cls,
        host: Host,
        unit: str,
        user_mode: bool = False,
        machine: str | None = None,
        user_name: str | None = None,
    ) -> dict[str, list[str]]:
        state = getattr(host, "systemd_dependencies", None)
        if state is not None:
            # SimulatedHost seam: copy each unit-name list so callers never
            # mutate the simulated state.
            return {key: list(units) for key, units in state.get(unit_name(unit), {}).items()}
        # Real transport: `systemctl show` over asyncssh.
        stdout = await host.run_shell(cls.command(unit, user_mode, machine, user_name))
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------


@dataclass(frozen=True)
class SystemctlCommand(Command):
    """One ``systemctl <action> [<unit>]`` invocation, with an optional scope.

    ``action`` is one of ``daemon-reload`` (no unit), ``start``, ``stop``,
    ``restart``, ``reload``, ``reload-or-try-restart``, ``reset-failed``,
    ``enable``, ``disable``, ``mask``, ``unmask``, ``preset`` or ``preset-all``.
    The scope flags (``user_mode``/``machine``/``user_name``) render the
    ``systemctl`` base exactly as the ``_systemctl_cmd`` helper does, and
    ``now`` adds ``--now`` to an ``enable``/``disable`` so the boot setting
    change also starts/stops the unit immediately. Every flag defaults to the
    plain system-scope rendering, so existing descriptors are unchanged.
    """

    action: str
    unit: str | None = None  # None for daemon-reload / preset-all
    user_mode: bool = False
    machine: str | None = None
    user_name: str | None = None
    now: bool = False

    def shell(self) -> str:
        parts = [_systemctl_cmd(self.user_mode, self.machine, self.user_name), self.action]
        if self.now:
            parts.append("--now")
        if self.unit is not None:
            parts.append(shlex.quote(self.unit))
        return " ".join(parts)


async def service(
    ctx: OpContext,
    service: str,
    running: bool = True,
    restarted: bool = False,
    reloaded: bool = False,
    enabled: bool | None = None,
    daemon_reload: bool = False,
    reset_failed: bool = False,
    soft_reload: bool = False,
    now: bool = False,
    user_mode: bool = False,
    machine: str | None = None,
    user_name: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Manage the state of a systemd unit.

    Args:
        ctx (OpContext): Execution context for the operation.
        service (str): Name of the unit (``httpd`` or ``httpd.service``; a
            bare name gets ``.service`` appended, any other systemd suffix
            such as ``.timer`` is kept).
        running (bool, optional): Whether the unit should be running.
            Defaults to True.
        restarted (bool, optional): Whether to restart the unit (only if it
            is already running). Defaults to False.
        reloaded (bool, optional): Whether to reload the unit (only if it is
            already running). Defaults to False.
        enabled (bool | None, optional): Whether the unit should be enabled
            on boot; ``None`` leaves the boot setting alone. Defaults to None.
        daemon_reload (bool, optional): Reload the systemd daemon to read
            updated unit files before anything else. Defaults to False.
        reset_failed (bool, optional): Clear a ``failed`` state with
            ``reset-failed`` before starting/restarting. Defaults to False.
        soft_reload (bool, optional): When ``reloaded`` is set, use
            ``reload-or-try-restart`` instead of ``reload`` so a unit without
            a reload handler is restarted instead. Defaults to False.
        now (bool, optional): When ``enabled`` changes, add ``--now`` so the
            enable/disable also starts/stops the unit immediately.
            Defaults to False.
        user_mode (bool, optional): Use the per-user systemd manager
            (``systemctl --user``). Defaults to False.
        machine (str | None, optional): Connect to a named machine's systemd
            (``--machine=<machine>``). Defaults to None.
        user_name (str | None, optional): Connect to a specific user's
            systemd session (``--machine=<user>@<machine>`` or
            ``<user>@.host``). Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated systemctl commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/systemd/systemd_service.py``.
    """

    # Manages a unit's lifecycle (``running``/``restarted``/
    # ``reloaded``/``enabled``/``daemon_reload`` plus the ``user_mode``/
    # ``machine``/``user_name`` scope), with three reemote extensions:
    # ``reset_failed`` clears a failed state first, ``soft_reload`` swaps
    # ``reload`` for ``reload-or-try-restart``, and ``now`` adds ``--now`` to
    # the enable/disable. An arbitrary ``command`` verb is
    # deliberately dropped -- ``service`` already accepts any unit suffix and
    # the dedicated operations cover the rest -- so that keyword is absorbed
    # by ``**kwargs`` with no effect.
    #
    # ``daemon_reload`` and ``restarted``/``reloaded`` are stateless (always
    # yield when set, like any non-idempotent operation);
    # ``running`` and ``enabled`` compare against the
    # ``SystemdServiceState``/``SystemdServiceEnabled`` facts and yield only
    # when the unit differs from the request. A unit counts as running only
    # when its ``ActiveState`` is ``active``, so a ``start`` is issued for an
    # ``activating`` unit too. The scope flags are forwarded to both the fact
    # reads and every descriptor. The commands need root: flag the request
    # ``sudo=True`` (a ``--user`` manager runs as the login user, so user-mode
    # requests usually need no escalation).
    unit = unit_name(service)
    reasons: list[str] = []
    scope = {"user_mode": user_mode, "machine": machine, "user_name": user_name}

    if daemon_reload:
        ctx.changed = True
        reasons.append("daemon-reload")
        yield SystemctlCommand(action="daemon-reload", **scope)

    state = await ctx.facts.get(SystemdServiceState, unit, **scope)
    is_running = state == "active"

    # Clear a failed state before any start/restart (a failed unit otherwise
    # refuses to start until reset-failed runs).
    if reset_failed and state == "failed":
        ctx.changed = True
        reasons.append(f"{unit} failed, will reset-failed")
        yield SystemctlCommand(action="reset-failed", unit=unit, **scope)

    if running and not is_running:
        ctx.changed = True
        reasons.append(f"{unit} not running ({state}), will start")
        yield SystemctlCommand(action="start", unit=unit, **scope)
    elif not running and is_running:
        ctx.changed = True
        reasons.append(f"{unit} running, will stop")
        yield SystemctlCommand(action="stop", unit=unit, **scope)
    else:
        reasons.append(f"{unit} already {'running' if running else 'stopped'}")

    # Only restart/reload a unit that is already running (a stopped one was
    # just started above, or is meant to stay stopped).
    if restarted and is_running:
        ctx.changed = True
        reasons.append(f"{unit} will restart")
        yield SystemctlCommand(action="restart", unit=unit, **scope)

    if reloaded and is_running:
        ctx.changed = True
        action = "reload-or-try-restart" if soft_reload else "reload"
        reasons.append(f"{unit} will {action}")
        yield SystemctlCommand(action=action, unit=unit, **scope)

    if enabled is not None:
        is_enabled = await ctx.facts.get(SystemdServiceEnabled, unit, **scope)
        if enabled and not is_enabled:
            ctx.changed = True
            reasons.append(f"{unit} not enabled, will enable")
            yield SystemctlCommand(action="enable", unit=unit, now=now, **scope)
        elif not enabled and is_enabled:
            ctx.changed = True
            reasons.append(f"{unit} enabled, will disable")
            yield SystemctlCommand(action="disable", unit=unit, now=now, **scope)
        else:
            reasons.append(f"{unit} already {'enabled' if enabled else 'disabled'}")

    ctx.reason = "; ".join(reasons)


async def daemon_reload(
    ctx: OpContext,
    user_mode: bool = False,
    machine: str | None = None,
    user_name: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Reload the systemd daemon to read unit file changes.

    Args:
        ctx (OpContext): Execution context for the operation.
        user_mode (bool, optional): Use the per-user systemd manager
            (``systemctl --user``). Defaults to False.
        machine (str | None, optional): Connect to a named machine's systemd
            (``--machine=<machine>``). Defaults to None.
        user_name (str | None, optional): Connect to a specific user's
            systemd session. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: A single ``systemctl daemon-reload`` command.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/systemd/systemd_daemon_reload.py``.
    """

    # Stateless and non-idempotent, it reads no fact and always yields one
    # ``daemon-reload`` command. Needs root for a
    # system manager: flag the request ``sudo=True``.
    ctx.changed = True
    ctx.reason = "daemon-reload"
    yield SystemctlCommand(
        action="daemon-reload",
        user_mode=user_mode,
        machine=machine,
        user_name=user_name,
    )


# --- systemd.mask / systemd.reset_failed -------------------------------------


async def mask(
    ctx: OpContext,
    unit: str,
    masked: bool = True,
    user_mode: bool = False,
    machine: str | None = None,
    user_name: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Mask or unmask a systemd unit so it cannot be started.

    Args:
        ctx (OpContext): Execution context for the operation.
        unit (str): Name of the unit (``httpd`` or ``httpd.service``; a bare
            name gets ``.service`` appended, any other systemd suffix is
            kept).
        masked (bool, optional): Whether the unit should be masked
            (``True``) or unmasked (``False``). Defaults to True.
        user_mode (bool, optional): Use the per-user systemd manager
            (``systemctl --user``). Defaults to False.
        machine (str | None, optional): Connect to a named machine's systemd
            (``--machine=<machine>``). Defaults to None.
        user_name (str | None, optional): Connect to a specific user's
            systemd session. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: A single ``systemctl mask``/``unmask`` command, only when the
            unit's mask state differs from the request.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/systemd/systemd_mask.py``.
    """

    # A reemote original. Bidirectional and
    # idempotent through one present-flagged path: read the unit's
    # ``UnitFileState`` from ``SystemdUnitProperties`` and yield ``mask`` only
    # when the unit is not already masked, ``unmask`` only when it is. A unit
    # systemd cannot load reports no ``UnitFileState`` ({}), so it counts as
    # unmasked -- ``mask`` still applies (systemctl creates the /dev/null
    # symlink for an unknown unit). The scope flags are forwarded to both the
    # fact read and the descriptor. Masking needs root for a system manager:
    # flag the request ``sudo=True``.
    unit = unit_name(unit)
    scope = {"user_mode": user_mode, "machine": machine, "user_name": user_name}
    properties = await ctx.facts.get(SystemdUnitProperties, unit, **scope)
    is_masked = properties.get("UnitFileState") == "masked"

    if masked and not is_masked:
        ctx.changed = True
        ctx.reason = f"{unit} not masked, will mask"
        yield SystemctlCommand(action="mask", unit=unit, **scope)
    elif not masked and is_masked:
        ctx.changed = True
        ctx.reason = f"{unit} masked, will unmask"
        yield SystemctlCommand(action="unmask", unit=unit, **scope)
    else:
        ctx.reason = f"{unit} already {'masked' if masked else 'unmasked'}"


async def reset_failed(
    ctx: OpContext,
    unit: str,
    user_mode: bool = False,
    machine: str | None = None,
    user_name: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Clear a systemd unit's ``failed`` state with ``reset-failed``.

    Args:
        ctx (OpContext): Execution context for the operation.
        unit (str): Name of the unit (a bare name gets ``.service`` appended,
            any other systemd suffix is kept).
        user_mode (bool, optional): Use the per-user systemd manager
            (``systemctl --user``). Defaults to False.
        machine (str | None, optional): Connect to a named machine's systemd
            (``--machine=<machine>``). Defaults to None.
        user_name (str | None, optional): Connect to a specific user's
            systemd session. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: A single ``systemctl reset-failed`` command, only when the
            unit's ``ActiveState`` is ``failed``.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/systemd/systemd_reset_failed.py``.
    """

    # A reemote original. The enhanced ``service`` keeps a ``reset_failed``
    # flag, and this standalone operation covers the reset-only case.
    # Idempotent: read
    # ``SystemdServiceState`` and yield ``reset-failed`` only when the unit is
    # ``failed`` -- any other state is left alone (the real command is a no-op
    # then). The scope flags are forwarded to both the fact read and the
    # descriptor. Needs root for a system manager: flag the request
    # ``sudo=True``.
    unit = unit_name(unit)
    scope = {"user_mode": user_mode, "machine": machine, "user_name": user_name}
    state = await ctx.facts.get(SystemdServiceState, unit, **scope)

    if state == "failed":
        ctx.changed = True
        ctx.reason = f"{unit} failed, will reset-failed"
        yield SystemctlCommand(action="reset-failed", unit=unit, **scope)
    else:
        ctx.reason = f"{unit} not failed ({state}), nothing to reset"


# --- systemd file operations -------------------------------------------------
#
# ``dropin``/``tmpfiles``/``sysusers`` write root-owned config under ``/etc``.
# They share the spool-transfer + elevated-install pattern (``dnf.repo``):
# an unprivileged scp spools the content to a dest-derived ``/tmp`` path, then
# elevated ``sh -c`` installs it ``0644`` and removes the spool. These are
# *system-scope only* -- there is no ``user_mode``/``machine``/``user_name``
# parameter, because ``/etc`` paths are the system manager's; user-scope
# config is a ``files.edit`` recipe (documented deviation, avoiding the
# tilde-expansion seam pitfall). The config file's base name is the
# ``filename`` parameter, not ``name``: ``name`` is an ``op()`` meta-parameter
# (the plan label) the driver consumes and never forwards, so an operation
# parameter called ``name`` would be unreachable through ``op()`` -- a
# necessary deviation from the proposal's ``name`` wording.


def _spool_path(dest: str) -> str:
    """The deterministic ``/tmp`` spool path the systemd file ops upload to.

    ``dropin``, ``tmpfiles`` and ``sysusers`` all spool through the same
    ``SystemdFileUploadCommand``/``SystemdFileInstallCommand`` pair, so they
    share this helper -- one spool per managed file, never two requests
    clobbering one another's upload. The name is derived from the managed
    path (``sha1(dest)[:16]``) rather than fixed, so it stays identical
    across the two deploy phases (a per-materialization temp name would
    diverge and trip the determinism guard) while two different files get
    two different spools. The content lands *inside* a sha1-derived ``/tmp``
    directory rather than at the top level of ``/tmp``, and the elevated
    install ``rm -rf``s the whole directory afterwards, so no leftover file
    lands in ``/tmp``. The directory is still world-writable ``/tmp`` so the
    *unprivileged* scp can write it; on a multi-tenant host the ``/tmp``
    spool is still a symlink-race exposure (the trade-off every
    spool-and-install operation here makes).
    """

    digest = hashlib.sha1(dest.encode()).hexdigest()[:16]
    return f"/tmp/reemote-systemd-{digest}/systemd.conf"


class _SystemdConfFile:
    """The current content of one systemd config file, or None when absent.

    A private, in-namespace stand-in for ``files.FileContents``: the file
    operations must compare the content they would write against what is
    already on disk, but the zero-cross-module-import invariant forbids
    reaching into the ``files`` namespace for its ``FileContents`` fact -- the
    ``git._DirectoryExists`` / ``dnf.DnfRepoFile`` pattern of an in-namespace
    fact instead of a cross-module import. It reads the *same* ``remote_files``
    seam ``files.FileContents`` does (so a simulated write reads back here),
    and on the real transport runs the same marker-then-``cat`` probe, so an
    existing-but-empty file stays distinguishable from a missing one (None).
    Not exported: it is an idempotence helper for the three file operations,
    not part of the public fact surface.
    """

    #: Marker printed before the ``cat``'d content, only when ``test -e``
    #: passes, so ``process`` can tell an existing (possibly empty) file from
    #: a missing one (no marker on stdout).
    MARKER = "#reemote-systemd-conf"

    @classmethod
    def command(cls, path: str) -> str:
        """Shell printing ``MARKER`` then the file's content, only if it exists."""
        quoted = shlex.quote(path)
        return f"test -e {quoted} && {{ printf '{cls.MARKER}\\n'; cat -- {quoted}; }}"

    @classmethod
    async def process(cls, host: Host, path: str) -> str | None:
        remote_files = getattr(host, "remote_files", None)
        if remote_files is not None:
            # SimulatedHost seam (shared with files.FileContents): decode the
            # content the simulated remote filesystem holds at the path.
            data = remote_files.get(path)
            return data.decode("utf-8") if data is not None else None
        # Real transport: cat the file over asyncssh behind the marker, so a
        # missing file (no marker) reads as None rather than empty content.
        stdout = await host.run_shell(cls.command(path))
        prefix = f"{cls.MARKER}\n"
        if not stdout.startswith(prefix):
            return None
        return stdout[len(prefix) :]


@dataclass(frozen=True)
class SystemdFileUploadCommand(TransferCommand):
    """Spool systemd config content to a ``/tmp`` path on the target.

    The first half of the file operations' write direction and a *non-shell*
    descriptor: it copies the content over the connection's scp channel via
    ``Host.upload_file`` rather than running shell text, so it has no
    ``shell()`` and its loggable form is the ``__str__`` rendering
    ``command_shell`` falls back to. Like ``DnfRepoUploadCommand`` the content
    is the text the operation writes (a plain ``str`` -- hashable and identical
    across phases), wrapped in a fresh ``io.BytesIO`` only at transfer time.
    The copy rides as the login user (transfers are unprivileged, so the
    request's ``sudo`` does not apply), which is why the spool path is
    world-writable ``/tmp`` rather than the root-owned ``/etc`` directory.
    """

    content: str  # the config text the operation writes
    dest: str  # spool path on the target host

    def __str__(self) -> str:
        return f"scp <systemd conf> -> {self.dest}"

    async def transfer(self, host: Host) -> None:
        # scp does not create parent directories, and the spool now lives
        # inside a sha1-derived /tmp directory rather than at the top level,
        # so mkdir -p it first. Unprivileged: /tmp is world-writable, so the
        # login user can create the directory, and a transfer never elevates.
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        await host.run_shell(f"mkdir -p {parent}")
        await host.upload_file(io.BytesIO(self.content.encode()), self.dest)


@dataclass(frozen=True)
class SystemdFileInstallCommand(Command):
    """Install the spooled config file ``0644``, then remove the spool dir.

    The second half of the file operations' write direction and the elevated
    one: writing under ``/etc`` needs root, so the request is flagged
    ``sudo=True`` and the whole sequence is wrapped in ``sh -c`` -- a bare
    ``sudo`` prefix would elevate only the first command of the ``&&`` chain
    and leave the cleanup running as the login user, whereas ``sudo sh -c
    '...'`` runs every step as root. The sequence creates the destination
    directory (``mkdir -p`` -- a drop-in's ``<unit>.d`` may not exist yet),
    copies the spooled content over the target file, chmods it ``0644`` and
    removes the whole sha1-derived ``/tmp`` spool directory -- the
    ``DnfRepoInstallCommand`` pattern. All paths are shell-quoted.
    """

    spool: str  # where SystemdFileUploadCommand wrote the content
    dest: str  # the /etc/... config path

    def shell(self) -> str:
        spool = shlex.quote(self.spool)
        dest = shlex.quote(self.dest)
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        # The spool file lives inside a sha1-derived /tmp directory; remove
        # the whole directory so no leftover lands in /tmp.
        spool_dir = shlex.quote(str(PurePosixPath(self.spool).parent))
        inner = (
            f"mkdir -p {parent} && "
            f"cp {spool} {dest} && "
            f"chmod 0644 {dest} && rm -rf {spool_dir}"
        )
        return f"sh -c {shlex.quote(inner)}"


@dataclass(frozen=True)
class SystemdFileRemoveCommand(Command):
    """``rm -f <path>``: delete a systemd config file.

    The removal mutation ``dropin``/``tmpfiles``/``sysusers`` yield for
    ``present=False``, only when the ``_SystemdConfFile`` fact reports the file
    present. ``rm -f`` tolerates an absent path, and deleting under ``/etc``
    needs root, so flag the request ``sudo=True`` (or ``su=True``). The path is
    shell-quoted.
    """

    path: str

    def shell(self) -> str:
        return f"rm -f {shlex.quote(self.path)}"


@dataclass(frozen=True)
class SystemdTmpfilesCommand(Command):
    """``systemd-tmpfiles --create <path>``: apply a tmpfiles.d config.

    The apply step ``tmpfiles(present=True)`` yields after writing the config,
    so the declared files/directories are created immediately rather than at
    the next boot. Needs root: flag the request ``sudo=True``. The path is
    shell-quoted.
    """

    path: str

    def shell(self) -> str:
        return f"systemd-tmpfiles --create {shlex.quote(self.path)}"


@dataclass(frozen=True)
class SystemdSysusersCommand(Command):
    """``systemd-sysusers <path>``: apply a sysusers.d config.

    The apply step ``sysusers(present=True)`` yields after writing the config,
    so the declared users/groups are created immediately rather than at the
    next boot. Needs root: flag the request ``sudo=True``. The path is
    shell-quoted.
    """

    path: str

    def shell(self) -> str:
        return f"systemd-sysusers {shlex.quote(self.path)}"


async def dropin(
    ctx: OpContext,
    unit: str,
    filename: str,
    content: str,
    present: bool = True,
    daemon_reload: bool = True,
    restart: bool = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add/remove/update a systemd unit drop-in configuration file.

    Args:
        ctx (OpContext): Execution context for the operation.
        unit (str): The unit the drop-in extends (a bare name gets
            ``.service`` appended, any other systemd suffix is kept).
        filename (str): The drop-in name; the file is
            ``/etc/systemd/system/<unit>.d/<filename>.conf`` and ``.conf`` is
            appended when missing.
        content (str): The drop-in's text content (the ``[Service]``/...
            override sections).
        present (bool, optional): Whether the drop-in file should exist.
            Defaults to True.
        daemon_reload (bool, optional): Reload the systemd daemon after a
            write/removal so the change takes effect. Defaults to True.
        restart (bool, optional): Restart the unit after a write so the new
            drop-in is applied to the running process. Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The spool transfer + elevated install (and daemon-reload /
            restart) when the content differs, or a removal (+ daemon-reload)
            when ``present=False`` and the file exists.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/systemd/systemd_dropin.py``.
    """

    # A reemote original. Idempotent through ``_SystemdConfFile`` (the
    # in-namespace content fact -- namespaces never import each other's
    # operations/facts): ``present=True`` writes only when the content differs
    # from what is already there, then chains ``daemon-reload`` (so systemd
    # re-reads the unit) and an optional ``restart`` (so the running process
    # picks up the override); ``present=False`` removes the file when it exists
    # and reloads. The write yields a ``SystemdFileUploadCommand`` transfer
    # spooling the text to ``/tmp`` over scp, then a ``SystemdFileInstallCommand``
    # running the elevated ``mkdir -p``/``cp``/``chmod 0644``/``rm`` install (the
    # ``dnf.repo`` pattern). System-scope only -- ``/etc/systemd/system`` is the
    # system manager's path; user-scope drop-ins are a ``files.edit`` recipe
    # (documented deviation). Writing/removing needs root: flag the request
    # ``sudo=True`` (the transfer stays unprivileged regardless).
    unit = unit_name(unit)
    if not filename.endswith(".conf"):
        filename = f"{filename}.conf"
    path = f"/etc/systemd/system/{unit}.d/{filename}"
    current = await ctx.facts.get(_SystemdConfFile, path)

    if not present:
        if current is None:
            ctx.reason = f"drop-in already absent ({path})"
            return
        ctx.changed = True
        ctx.reason = f"drop-in {path} present, will remove"
        yield SystemdFileRemoveCommand(path=path)
        if daemon_reload:
            yield SystemctlCommand(action="daemon-reload")
        return

    if current == content:
        ctx.reason = f"drop-in already up to date ({path})"
        return

    ctx.changed = True
    ctx.reason = f"drop-in {path} missing or differs from the desired content"
    spool = _spool_path(path)
    yield SystemdFileUploadCommand(content=content, dest=spool)
    yield SystemdFileInstallCommand(spool=spool, dest=path)
    if daemon_reload:
        yield SystemctlCommand(action="daemon-reload")
    if restart:
        yield SystemctlCommand(action="restart", unit=unit)


async def tmpfiles(
    ctx: OpContext,
    filename: str,
    content: str,
    present: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add/remove/update a ``tmpfiles.d`` configuration file.

    Args:
        ctx (OpContext): Execution context for the operation.
        filename (str): The config name; the file is ``/etc/tmpfiles.d/<filename>.conf``
            and ``.conf`` is appended when missing.
        content (str): The tmpfiles.d text content (the type/path/mode/... rows).
        present (bool, optional): Whether the config file should exist.
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The spool transfer + elevated install + ``systemd-tmpfiles
            --create`` when the content differs, or a removal when
            ``present=False`` and the file exists.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/systemd/systemd_tmpfiles.py``.
    """

    # A reemote original. Idempotent through ``_SystemdConfFile``:
    # ``present=True`` writes only when the content differs, then applies it
    # with ``systemd-tmpfiles --create <path>`` so the declared files and
    # directories are created immediately rather than at the next boot;
    # ``present=False`` removes the config file only (already-created runtime
    # files are not cleaned up -- ``systemd-tmpfiles --remove`` is a separate
    # concern). The write uses the same spool-transfer + elevated-install
    # pattern as ``dropin``. System-scope only (``/etc/tmpfiles.d``); writing
    # and applying need root: flag the request ``sudo=True``.
    if not filename.endswith(".conf"):
        filename = f"{filename}.conf"
    path = f"/etc/tmpfiles.d/{filename}"
    current = await ctx.facts.get(_SystemdConfFile, path)

    if not present:
        if current is None:
            ctx.reason = f"tmpfiles config already absent ({path})"
            return
        ctx.changed = True
        ctx.reason = f"tmpfiles config {path} present, will remove"
        yield SystemdFileRemoveCommand(path=path)
        return

    if current == content:
        ctx.reason = f"tmpfiles config already up to date ({path})"
        return

    ctx.changed = True
    ctx.reason = f"tmpfiles config {path} missing or differs from the desired content"
    spool = _spool_path(path)
    yield SystemdFileUploadCommand(content=content, dest=spool)
    yield SystemdFileInstallCommand(spool=spool, dest=path)
    yield SystemdTmpfilesCommand(path=path)


async def sysusers(
    ctx: OpContext,
    filename: str,
    content: str,
    present: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add/remove/update a ``sysusers.d`` configuration file.

    Args:
        ctx (OpContext): Execution context for the operation.
        filename (str): The config name; the file is ``/etc/sysusers.d/<filename>.conf``
            and ``.conf`` is appended when missing.
        content (str): The sysusers.d text content (the type/name/id/... rows).
        present (bool, optional): Whether the config file should exist.
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The spool transfer + elevated install + ``systemd-sysusers``
            when the content differs, or a removal when ``present=False`` and
            the file exists.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/systemd/systemd_sysusers.py``.
    """

    # A reemote original. Idempotent through ``_SystemdConfFile``:
    # ``present=True`` writes only when the content differs, then applies it
    # with ``systemd-sysusers <path>`` so the declared users and groups are
    # created immediately rather than at the next boot; ``present=False``
    # removes the config file only -- it does NOT delete the users/groups it
    # created (``systemd-sysusers`` has no removal verb; deleting accounts is a
    # ``server.user``/``userdel`` concern, documented deviation). The write uses
    # the same spool-transfer + elevated-install pattern as ``dropin``.
    # System-scope only (``/etc/sysusers.d``); writing and applying need root:
    # flag the request ``sudo=True``.
    if not filename.endswith(".conf"):
        filename = f"{filename}.conf"
    path = f"/etc/sysusers.d/{filename}"
    current = await ctx.facts.get(_SystemdConfFile, path)

    if not present:
        if current is None:
            ctx.reason = f"sysusers config already absent ({path})"
            return
        ctx.changed = True
        ctx.reason = f"sysusers config {path} present, will remove"
        yield SystemdFileRemoveCommand(path=path)
        return

    if current == content:
        ctx.reason = f"sysusers config already up to date ({path})"
        return

    ctx.changed = True
    ctx.reason = f"sysusers config {path} missing or differs from the desired content"
    spool = _spool_path(path)
    yield SystemdFileUploadCommand(content=content, dest=spool)
    yield SystemdFileInstallCommand(spool=spool, dest=path)
    yield SystemdSysusersCommand(path=path)


# --- systemd.preset ----------------------------------------------------------


async def preset(
    ctx: OpContext,
    unit: str | None = None,
    all_units: bool = False,
    user_mode: bool = False,
    machine: str | None = None,
    user_name: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Apply a unit's preset policy (enable/disable per the vendor preset).

    Args:
        ctx (OpContext): Execution context for the operation.
        unit (str | None, optional): The unit to preset (a bare name gets
            ``.service`` appended). Required unless ``all_units=True``.
            Defaults to None.
        all_units (bool, optional): Apply presets to every unit
            (``systemctl preset-all``) instead of a single unit. Defaults to
            False.
        user_mode (bool, optional): Use the per-user systemd manager
            (``systemctl --user``). Defaults to False.
        machine (str | None, optional): Connect to a named machine's systemd
            (``--machine=<machine>``). Defaults to None.
        user_name (str | None, optional): Connect to a specific user's
            systemd session. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: A ``systemctl preset <unit>`` command only when the unit's
            state differs from its preset policy, or a stateless
            ``systemctl preset-all`` when ``all_units=True``.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/systemd/systemd_preset.py``.
    """

    # A reemote original. ``all_units=True`` is stateless and non-idempotent
    # (like ``daemon_reload``): it always yields ``preset-all``. Otherwise the
    # operation reads ``SystemdUnitFiles`` and yields ``preset <unit>`` only
    # when the unit's current ``state`` differs from its ``preset`` policy
    # (``enabled``/``disabled``); a unit whose state already matches, or whose
    # preset is empty (no policy), is a no-op, and a unit absent from the table
    # is reported rather than preset blindly. A request with neither a unit nor
    # ``all_units`` raises ``ValueError``. The scope flags are forwarded to both
    # the fact read and the descriptor. Needs root for a system manager: flag
    # the request ``sudo=True``.
    scope = {"user_mode": user_mode, "machine": machine, "user_name": user_name}

    if all_units:
        ctx.changed = True
        ctx.reason = "preset-all"
        yield SystemctlCommand(action="preset-all", **scope)
        return

    if unit is None:
        raise ValueError("systemd.preset requires a unit unless all_units=True")

    unit = unit_name(unit)
    files = await ctx.facts.get(SystemdUnitFiles, **scope)
    entry = files.get(unit)
    if entry is None:
        ctx.reason = f"{unit} not found in unit files"
        return

    policy = entry["preset"]
    state = entry["state"]
    if policy in ("enabled", "disabled") and state != policy:
        ctx.changed = True
        ctx.reason = f"{unit} state {state} differs from preset {policy}, will preset"
        yield SystemctlCommand(action="preset", unit=unit, **scope)
    else:
        ctx.reason = f"{unit} already matches its preset ({policy or 'none'})"


__all__ = [
    "SystemctlCommand",
    "SystemdDependencies",
    "SystemdEnabled",
    "SystemdFailedUnit",
    "SystemdFailedUnits",
    "SystemdFileInstallCommand",
    "SystemdFileRemoveCommand",
    "SystemdFileUploadCommand",
    "SystemdServiceEnabled",
    "SystemdServiceState",
    "SystemdStatus",
    "SystemdSystemState",
    "SystemdSysusersCommand",
    "SystemdTimer",
    "SystemdTimers",
    "SystemdTmpfilesCommand",
    "SystemdUnitFile",
    "SystemdUnitFiles",
    "SystemdUnitProperties",
    "SystemdVersion",
    "SystemdVersionInfo",
    "daemon_reload",
    "dropin",
    "mask",
    "preset",
    "reset_failed",
    "service",
    "sysusers",
    "tmpfiles",
    "unit_name",
]