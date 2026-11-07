# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``openrc`` module: facts and operations in one source.
#
# Fact side: ``OpenrcStatus`` and ``OpenrcEnabled`` are ports of pyinfra's
# two OpenRC facts -- the per-runlevel name -> running bool parsed from
# ``rc-status <runlevel>`` rows, and the name -> enabled-in-runlevel bool
# parsed from ``rc-update show -v``'s ``name | runlevels`` columns. Three
# reemote5 originals widen the visibility pyinfra's pair leaves missing:
# ``OpenrcServices``, the same ``rc-status`` rows kept as their *raw* state
# words (``started``, ``stopped``, ``crashed``, ...) instead of the running
# bool, so a crashed service is distinguishable from a cleanly stopped one;
# ``OpenrcRunlevels``, the runlevel -> assigned-names mapping parsed from
# ``ls -R /etc/runlevels`` (a runlevel is a directory of symlinks, and a
# stacked runlevel appears as an entry in its target's directory); and
# ``OpenrcVersion``, the installed OpenRC release parsed from
# ``openrc --version`` (``None`` on a host without OpenRC). Like every fact
# they are phase-unaware: their ``process`` coroutine just gathers the raw
# value.
#
# Operation side: ``openrc.service`` ports pyinfra's ``openrc.service``
# (running/restarted/reloaded/enabled/runlevel) through the
# ``RcServiceCommand``/``RcUpdateCommand`` descriptors, reading
# ``OpenrcStatus``/``OpenrcEnabled`` for idempotence; pyinfra's arbitrary
# ``command`` option is deliberately dropped (an escape hatch for any
# rc-service verb defeats the typed-descriptor model -- ``server.shell``
# covers the rare custom verb). ``openrc.runlevel`` and
# ``openrc.custom_runlevel`` are reemote5 originals with no pyinfra
# counterpart: the first manages one service's membership in one runlevel
# (``rc-update add``/``del``) as a standalone operation, the second manages
# the runlevel directories themselves (``mkdir -p``/``rm -rf`` under
# ``/etc/runlevels``) including stacking one runlevel onto another
# (``rc-update -s add``) and unstacking before removal -- rc-update(8)
# documents no verb that deletes a runlevel, so removal is the directory
# delete plus fact-derived ``rc-update -s delete`` unstacking.
#
# Deliberately absent: an ``openrc.mask`` operation -- OpenRC has no
# masking mechanism (no systemd-style symlink-to-/dev/null), so masking
# cannot be honestly emulated; disable the service in every runlevel
# instead. Global/service configuration (``/etc/rc.conf``,
# ``/etc/conf.d/<service>``) is managed with the generic ``files.edit``
# primitive rather than a bespoke ``openrc.config`` operation -- see the
# spool-and-install recipe in ``examples/modules/openrc/openrc_config.py``.
# A combined install-and-enable ``openrc.ensure`` is likewise not an
# operation: ``apk.packages`` followed by ``openrc.service`` composes in a
# deploy (``examples/modules/openrc/openrc_ensure.py``).
# Operations are async generators that receive an ``OpContext``, await
# facts, set ``ctx.changed``/``ctx.reason``, and yield command descriptors;
# they never touch the host directly.


import re
import shlex
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass
from typing import Any

from ...commands import Command
from ...core import OpContext
from ...hosts import Host

# --- Shared helpers ----------------------------------------------------------

# pyinfra's ``OpenrcStatus.regex`` verbatim: leading whitespace, the service
# name (letters, digits, dash, underscore, dot -- ``nftables.default`` and
# ``podman-long-running`` both match), then a bracketed state word with an
# optional uptime suffix (``started 00:34:14 (10)`` or
# ``started 15 day(s) 00:34:14 (10)``). Shared by ``OpenrcStatus`` (which
# projects the state word to a running bool) and ``OpenrcServices`` (which
# keeps it raw), so both facts parse exactly the same rows.
_SERVICE_LINE_REGEX = re.compile(
    r"\s+([a-zA-Z0-9\-_.]+)"
    r"\s+\[\s+"
    r"([a-z]+)"
    r"(?:\s(?:[0-9]+\sday\(s\)\s)?"
    r"[0-9]+\:[0-9]+\:[0-9]+\s\([0-9]+\))?"
    r"\s+\]"
)


# --- Facts -------------------------------------------------------------------


class OpenrcEnabled:
    """Whether each OpenRC service is enabled in a runlevel.

    **Commands:**

    ```bash
    rc-update show -v
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
        Examples can be found in ``examples/modules/openrc/openrc_openrc_enabled.py``.
    """

    # Mirrors pyinfra's ``openrc.OpenrcEnabled``: ``rc-update show -v``
    # prints every service as a ``name | runlevel ...`` row (the ``-v``
    # includes services assigned to no runlevel at all, whose right-hand
    # side is empty), and the fact answers membership in the requested
    # runlevel -- ``crond | default`` reads True for "default", False for
    # "boot". The ``runlevel`` argument selects the membership test, never
    # the shell: one command serves every runlevel.
    #
    # Deliberate deviations from pyinfra: a line carrying no ``|`` is
    # skipped instead of crashing the split (pyinfra's ``line.split("|", 1)``
    # raises on a stray header or blank line; the parser stays total).
    # pyinfra's ``requires_command`` gating is dropped: a host without
    # rc-update exits 127 and surfaces as ``CommandNotFoundError``.
    #
    # SimulatedHost seam note: the ``openrc_runlevels`` field stores the
    # runlevel -> names mapping (``OpenrcRunlevels``' shape), and this fact
    # projects membership from it. Only services assigned to *some*
    # runlevel appear in the projection -- an unassigned service (pyinfra's
    # ``cgroups |`` row, False everywhere) is absent rather than False, so
    # consumers read ``.get(service, False)`` exactly as pyinfra's own
    # ``openrc.service`` does.

    #: Shell listing every service and its runlevels over SSH.
    COMMAND = "rc-update show -v"

    @classmethod
    def parse(cls, output: Iterable[str], runlevel: str = "default") -> dict[str, bool]:
        """Parse ``rc-update show -v`` rows into runlevel-membership bools.

        Each ``name | runlevels`` row becomes one entry: True when
        ``runlevel`` appears among the whitespace-separated runlevel names,
        False otherwise (including the empty right-hand side of an
        unassigned service). Lines without a ``|`` contribute nothing. Pure
        and testable.
        """
        services: dict[str, bool] = {}
        for line in output:
            if "|" not in line:
                continue
            name, levels = line.split("|", 1)
            services[name.strip()] = runlevel in levels.split()
        return services

    @classmethod
    async def process(cls, host: Host, runlevel: str = "default") -> dict[str, bool]:
        openrc_runlevels = getattr(host, "openrc_runlevels", None)
        if openrc_runlevels is not None:
            # SimulatedHost seam: project membership from the runlevel ->
            # names mapping. A service listed in several runlevels stays
            # True only for the requested one; the union covers every
            # assigned service (unassigned ones are absent, see above).
            services: dict[str, bool] = {}
            for level, names in openrc_runlevels.items():
                for name in names:
                    services[name] = services.get(name, False) or level == runlevel
            return services
        # Real transport: list the runlevel assignments over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines(), runlevel)


class OpenrcRunlevels:
    """The runlevels and the service names assigned to each.

    **Commands:**

    ```bash
    ls -R /etc/runlevels 2>/dev/null || true
    ```

    **Returns:**

    ```python
    dict[str, list[str]]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/openrc/openrc_openrc_runlevels.py``.
    """

    # A reemote5 original: pyinfra's ``openrc`` namespace ships only
    # ``OpenrcStatus``/``OpenrcEnabled``, neither of which reports the
    # runlevel inventory itself. A runlevel *is* a directory under
    # ``/etc/runlevels`` holding one symlink per assigned service (that is
    # what ``rc-update add``/``del`` write), so ``ls -R`` of the tree is the
    # whole mapping -- no OpenRC tooling needed, busybox-compatible, and
    # empty runlevels are visible too (``rc-update show`` would hide them).
    # The recursive listing prints a ``/etc/runlevels:`` header with the
    # runlevel directory names, then one ``/etc/runlevels/<name>:`` section
    # per runlevel with its entries, for example::
    #
    #     {
    #         "boot": ["bootmisc", "networking", "sysctl"],
    #         "default": ["crond", "sshd"],
    #         "shutdown": ["killprocs", "savecache"],
    #     }
    #
    # A *stacked* runlevel (``rc-update -s add <runlevel> <target>``) is a
    # symlink to the runlevel directory inside the target's directory, so
    # it appears as an entry in the target's list -- ``ls -R`` does not
    # follow symlinks, and the stacked runlevel keeps its own section.
    # Entries keep listing order (ls sorts), no dedup or re-sorting. The
    # ``2>/dev/null || true`` guard reads a host without OpenRC (no
    # ``/etc/runlevels``) as ``{}``, the ``DnfRepositories`` missing-file
    # tolerance, so only ``HostConnectionError`` can escape the gather.

    #: The directory tree OpenRC keeps its runlevels in.
    PATH = "/etc/runlevels"

    #: Recursive listing of the runlevel tree, tolerating its absence: the
    #: ``|| true`` keeps a non-OpenRC host from failing the gather, so the
    #: fact reads as ``{}``.
    COMMAND = f"ls -R {PATH} 2>/dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, list[str]]:
        """Parse ``ls -R /etc/runlevels`` sections into runlevel mappings.

        A ``/etc/runlevels:`` header opens the top-level listing, whose
        entries are the runlevel directory names (each seeded as an empty
        list, so a runlevel whose own section is missing still appears); a
        ``/etc/runlevels/<name>:`` header opens that runlevel's section and
        every following non-blank entry is appended to its list. Blank
        lines close a section; a header deeper than one level (a stray
        subdirectory) is ignored. Pure and testable.
        """
        runlevels: dict[str, list[str]] = {}
        current: str | None = None
        prefix = cls.PATH + "/"
        for raw in output:
            line = raw.strip()
            if not line:
                current = None
                continue
            if line.endswith(":"):
                path = line[:-1]
                if path.startswith(prefix) and "/" not in path[len(prefix) :]:
                    current = path[len(prefix) :]
                    runlevels.setdefault(current, [])
                else:
                    # The top-level header (or a deeper stray directory):
                    # entries below it are runlevel names, not assignments.
                    current = None
                continue
            if current is None:
                runlevels.setdefault(line, [])
            else:
                runlevels[current].append(line)
        return runlevels

    @classmethod
    async def process(cls, host: Host) -> dict[str, list[str]]:
        openrc_runlevels = getattr(host, "openrc_runlevels", None)
        if openrc_runlevels is not None:
            # SimulatedHost seam: read the in-memory mapping directly. Copy
            # each list so callers never mutate simulated state.
            return {level: list(names) for level, names in openrc_runlevels.items()}
        # Real transport: list the runlevel tree over asyncssh and parse.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class OpenrcServices:
    """The raw state word of each OpenRC service in a runlevel.

    **Commands:**

    ```bash
    rc-status <runlevel>
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
        Examples can be found in ``examples/modules/openrc/openrc_openrc_services.py``.
    """

    # A reemote5 original and the lossless sibling of ``OpenrcStatus``:
    # the same ``rc-status <runlevel>`` rows, but the bracketed state word
    # is kept verbatim instead of projected to a running bool, so the
    # states pyinfra's fact collapses remain distinguishable --
    # ``started``, ``stopped``, ``crashed``, ``starting``, ``stopping``,
    # ``inactive`` -- for example::
    #
    #     {
    #         "sshd": "started",
    #         "virtualbox-guest-additions": "crashed",
    #         "local": "stopped",
    #     }
    #
    # The uptime suffix a started service may carry (``started 00:34:14
    # (10)``, ``started 15 day(s) 00:34:14 (10)``) is not part of the state
    # and is dropped, exactly as ``OpenrcStatus`` drops it. Rows keep
    # command order; the ``Runlevel:`` header and any non-service line
    # match no row. A runlevel with no services reads as ``{}``.
    #
    # Deliberate deviation from pyinfra (which has no raw-state fact):
    # ``requires_command`` gating is dropped -- a host without rc-status
    # exits 127 and surfaces as ``CommandNotFoundError``.

    @classmethod
    def command(cls, runlevel: str = "default") -> str:
        """Shell reporting the runlevel's service states over SSH."""
        # The runlevel is ``shlex``-quoted so a name carrying shell
        # metacharacters is passed literally. No ``|| true`` guard: a
        # missing rc-status must surface as ``CommandNotFoundError``, the
        # ``OpenrcStatus`` parity (pyinfra gates on ``requires_command``).
        return f"rc-status {shlex.quote(runlevel)}"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, str]:
        """Parse ``rc-status`` rows into raw state words.

        Every service row contributes its name and the bracketed state
        word verbatim (the optional uptime suffix is not captured); header
        and blank lines match nothing. Pure and testable.
        """
        services: dict[str, str] = {}
        for line in output:
            match = _SERVICE_LINE_REGEX.match(line)
            if match:
                services[match.group(1)] = match.group(2)
        return services

    @classmethod
    async def process(cls, host: Host, runlevel: str = "default") -> dict[str, str]:
        openrc_service_states = getattr(host, "openrc_service_states", None)
        if openrc_service_states is not None:
            # SimulatedHost seam: the runlevel-keyed store holds the raw
            # state words; an unknown runlevel reads as {}. Copy the dict
            # so callers never mutate simulated state.
            return dict(openrc_service_states.get(runlevel, {}))
        # Real transport: query the runlevel's states over asyncssh.
        stdout = await host.run_shell(cls.command(runlevel))
        return cls.parse(stdout.splitlines())


class OpenrcStatus:
    """Whether each OpenRC service in a runlevel is running.

    **Commands:**

    ```bash
    rc-status <runlevel>
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
        Examples can be found in ``examples/modules/openrc/openrc_openrc_status.py``.
    """

    # Mirrors pyinfra's ``openrc.OpenrcStatus``: ``rc-status <runlevel>``
    # prints one ``name [ state ]`` row per service assigned to the
    # runlevel, and the fact projects the state word to a running bool --
    # only ``started`` reads True, so ``stopped``, ``crashed`` and every
    # other state read False (pyinfra's ``group(2) == "started"``). The
    # optional uptime suffix (``started 00:34:14 (10)``, ``started 15
    # day(s) 00:34:14 (10)``) is matched and ignored; the ``Runlevel:``
    # header and blank lines match no row. The regex is pyinfra's verbatim
    # (shared with ``OpenrcServices``, which keeps the state word raw).
    #
    # Deliberate deviations from pyinfra: the runlevel is ``shlex``-quoted
    # in the shell (pyinfra interpolates it bare); ``requires_command``
    # gating is dropped -- a host without rc-status exits 127 and surfaces
    # as ``CommandNotFoundError``.
    #
    # SimulatedHost seam note: the ``openrc_service_states`` field stores
    # runlevel -> service -> raw state word (``OpenrcServices``' shape);
    # this fact projects it to the running bool, so one lossless store
    # backs both facts and a ``crashed`` entry reads False here.

    @classmethod
    def command(cls, runlevel: str = "default") -> str:
        """Shell reporting the runlevel's service states over SSH."""
        # pyinfra's ``rc-status {runlevel}`` with the runlevel quoted; for
        # the fixture runlevels ("default", "boot") the shell is identical
        # to pyinfra's verbatim.
        return f"rc-status {shlex.quote(runlevel)}"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, bool]:
        """Parse ``rc-status`` rows into running bools.

        Every service row contributes its name and whether its state word
        is ``started``; header and blank lines match nothing. Pure and
        testable.
        """
        services: dict[str, bool] = {}
        for line in output:
            match = _SERVICE_LINE_REGEX.match(line)
            if match:
                services[match.group(1)] = match.group(2) == "started"
        return services

    @classmethod
    async def process(cls, host: Host, runlevel: str = "default") -> dict[str, bool]:
        openrc_service_states = getattr(host, "openrc_service_states", None)
        if openrc_service_states is not None:
            # SimulatedHost seam: project the raw state words to running
            # bools. An unknown runlevel reads as {}. The dict is built
            # fresh, so callers never mutate simulated state.
            return {
                name: state == "started"
                for name, state in openrc_service_states.get(runlevel, {}).items()
            }
        # Real transport: query the runlevel's states over asyncssh.
        stdout = await host.run_shell(cls.command(runlevel))
        return cls.parse(stdout.splitlines())


class OpenrcVersion:
    """The installed OpenRC version, or ``None`` without OpenRC.

    **Commands:**

    ```bash
    openrc --version 2>/dev/null || true
    ```

    **Returns:**

    ```python
    str | None
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/openrc/openrc_openrc_version.py``.
    """

    # A reemote5 original: pyinfra's ``openrc`` namespace ships no version
    # fact, so an operation cannot adapt to the OpenRC release present.
    # The ``openrc`` binary itself answers ``--version`` with one line --
    # ``openrc (OpenRC) 0.54.1`` -- whose last token is the release; the
    # fact returns that token (``"0.54.1"``), not the banner. A host
    # without OpenRC prints nothing (the ``2>/dev/null || true`` guard
    # swallows the 127), so the fact reads ``None``, the
    # ``ApkPackage``/``server.Which`` None-on-absent convention. Only
    # ``HostConnectionError`` can escape the gather.
    #
    # SimulatedHost seam note: the ``openrc_version`` field is a plain
    # string whose empty default projects to ``None`` -- ``None`` cannot
    # be the field default because it is the seam-absent sentinel every
    # fact's ``getattr`` check relies on.

    #: Shell printing the OpenRC release banner, tolerating its absence.
    COMMAND = "openrc --version 2>/dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> str | None:
        """Parse the ``openrc --version`` banner into a release string.

        The first non-blank line's last whitespace-separated token is the
        version (``openrc (OpenRC) 0.54.1`` -> ``"0.54.1"``); no output at
        all reads as ``None``. Pure and testable.
        """
        for raw in output:
            line = raw.strip()
            if line:
                return line.split()[-1]
        return None

    @classmethod
    async def process(cls, host: Host) -> str | None:
        openrc_version = getattr(host, "openrc_version", None)
        if openrc_version is not None:
            # SimulatedHost seam: the empty-string default projects to
            # None (a host without OpenRC).
            return openrc_version or None
        # Real transport: query the version banner over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------


@dataclass(frozen=True)
class RcServiceCommand(Command):
    """``rc-service <service> <action>``: one runtime-state invocation.

    The descriptor ``openrc.service`` yields for the runtime direction:
    ``action`` is ``start``, ``stop``, ``restart`` or ``reload`` (pyinfra's
    ``rc-service {0} {1}`` formatter). The service name is ``shlex``-quoted
    (pyinfra's ``QuoteString``), so a name carrying shell metacharacters
    reaches rc-service literally rather than being executed.
    """

    service: str
    action: str

    def shell(self) -> str:
        return f"rc-service {shlex.quote(self.service)} {self.action}"


@dataclass(frozen=True)
class RcUpdateCommand(Command):
    """``rc-update [-s] add|del <name> <runlevel>``: one assignment change.

    The single present-flagged descriptor the runlevel directions yield,
    following the ``ApkHoldCommand``/``server.ModprobeCommand`` precedent --
    both directions are the same tool, so one type covers them.
    ``present=True`` renders ``add``, ``present=False`` renders ``del``
    (pyinfra's ``rc-update add``/``rc-update del`` with ``QuoteString`` on
    both arguments). ``stack=True`` inserts ``-s``: the name is then
    another *runlevel* being stacked onto (or unstacked from) the target
    runlevel, rc-update(8)'s inheritance mechanism. Both names are
    ``shlex``-quoted.
    """

    service: str
    runlevel: str = "default"
    present: bool = True
    stack: bool = False

    def shell(self) -> str:
        verb = "add" if self.present else "del"
        stack = "-s " if self.stack else ""
        return (
            f"rc-update {stack}{verb} {shlex.quote(self.service)} "
            f"{shlex.quote(self.runlevel)}"
        )


async def service(
    ctx: OpContext,
    service: str,
    running: bool | None = True,
    restarted: bool = False,
    reloaded: bool = False,
    enabled: bool | None = None,
    runlevel: str = "default",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Manage the state of an OpenRC service.

    Args:
        ctx (OpContext): Execution context for the operation.
        service (str): Name of the service to manage (its ``/etc/init.d``
            script name).
        running (bool | None, optional): Whether the service should be
            running (``True``), stopped (``False``), or left alone
            (``None``). Defaults to True.
        restarted (bool, optional): Whether to restart the service (only
            if it is already running). Defaults to False.
        reloaded (bool, optional): Whether to reload the service (only if
            it is already running). Defaults to False.
        enabled (bool | None, optional): Whether the service should be
            enabled in ``runlevel`` at boot; ``None`` leaves the boot
            setting alone. Defaults to None.
        runlevel (str, optional): The runlevel the status and boot
            settings are managed for. Defaults to "default".
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated rc-service and rc-update commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/openrc/openrc_service.py``.
    """

    # pyinfra ``openrc.service`` parity (its ``handle_service_control``
    # with the ``rc-service {0} {1}`` formatter, plus the ``enabled``
    # branch). ``running`` compares the ``OpenrcStatus`` fact (only
    # ``started`` counts as running, so a crashed service is started);
    # ``restarted``/``reloaded`` act only on an already-running service --
    # a stopped one was just started above, or is meant to stay stopped;
    # ``enabled`` compares the ``OpenrcEnabled`` fact and yields
    # ``rc-update add``/``del`` for the runlevel. The no-op reasons are
    # pyinfra's verbatim wordings ("service X is running/stopped/enabled/
    # disabled"). ``running=None`` leaves the runtime state alone
    # (pyinfra's ``handle_service_control`` accepts it even though the
    # operation's annotation says bool).
    #
    # Deliberate deviation from pyinfra: the arbitrary ``command`` option
    # (any rc-service verb, always executed) is dropped -- an untyped
    # escape hatch defeats the typed-descriptor model; ``server.shell``
    # covers the rare custom verb. ``**kwargs`` absorbs it and pyinfra's
    # global arguments (``op()`` already strips ``name`` and the
    # ``sudo``/``su``/``doas`` escalation flags).
    #
    # The commands need root: flag the request ``sudo=True``, ``doas=True``
    # or ``su=True`` -- stock Alpine ships no ``sudo``, so ``doas`` is
    # usually the escalator on an OpenRC host (passwordless ``doas -n``
    # needs a ``permit nopass`` rule in ``/etc/doas.conf``).
    reasons: list[str] = []
    statuses = await ctx.facts.get(OpenrcStatus, runlevel)
    is_running = statuses.get(service, False)

    if running is False:
        if is_running:
            ctx.changed = True
            reasons.append(f"service {service} is running, will stop")
            yield RcServiceCommand(service=service, action="stop")
        else:
            reasons.append(f"service {service} is stopped")
    elif running is True:
        if not is_running:
            ctx.changed = True
            reasons.append(f"service {service} is not running, will start")
            yield RcServiceCommand(service=service, action="start")
        else:
            reasons.append(f"service {service} is running")

    # Only restart/reload a service that is already running (pyinfra's
    # ``restarted and is_running`` / ``reloaded and is_running`` gates).
    if restarted and is_running:
        ctx.changed = True
        reasons.append(f"service {service} will restart")
        yield RcServiceCommand(service=service, action="restart")

    if reloaded and is_running:
        ctx.changed = True
        reasons.append(f"service {service} will reload")
        yield RcServiceCommand(service=service, action="reload")

    if enabled is not None:
        openrc_enabled = await ctx.facts.get(OpenrcEnabled, runlevel)
        is_enabled = openrc_enabled.get(service, False)
        if enabled and not is_enabled:
            ctx.changed = True
            reasons.append(f"service {service} is not enabled, will enable")
            yield RcUpdateCommand(service=service, runlevel=runlevel, present=True)
        elif not enabled and is_enabled:
            ctx.changed = True
            reasons.append(f"service {service} is enabled, will disable")
            yield RcUpdateCommand(service=service, runlevel=runlevel, present=False)
        else:
            reasons.append(
                f"service {service} is {'enabled' if enabled else 'disabled'}"
            )

    ctx.reason = "; ".join(reasons) or f"service {service} left alone"


# --- openrc.runlevel -----------------------------------------------------------


@dataclass(frozen=True)
class RunlevelCommand(Command):
    """``mkdir -p``/``rm -rf`` of one ``/etc/runlevels/<runlevel>`` directory.

    The single present-flagged descriptor ``openrc.custom_runlevel``
    yields: ``present=True`` creates the runlevel directory (a runlevel
    *is* a directory of service symlinks -- rc-update(8) ships no verb
    that creates one), ``present=False`` removes it. rc-update(8) also
    documents no verb that *deletes* a runlevel, so removal is the bare
    directory delete; ``openrc.custom_runlevel`` unstacks the runlevel
    from every target first (fact-derived ``rc-update -s del`` commands)
    so no dangling stack symlink survives it. The runlevel name is
    ``shlex``-quoted in both directions.
    """

    runlevel: str
    present: bool = True

    def shell(self) -> str:
        path = f"/etc/runlevels/{shlex.quote(self.runlevel)}"
        return f"mkdir -p {path}" if self.present else f"rm -rf {path}"


async def runlevel(
    ctx: OpContext,
    service: str,
    runlevel: str = "default",
    enabled: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add or remove a service from an OpenRC runlevel.

    Args:
        ctx (OpContext): Execution context for the operation.
        service (str): Name of the service (its ``/etc/init.d`` script
            name) to assign or unassign.
        runlevel (str, optional): The runlevel to manage the assignment
            in. Defaults to "default".
        enabled (bool, optional): Whether the service should be assigned
            to the runlevel (``True``, ``rc-update add``) or removed from
            it (``False``, ``rc-update del``). Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated rc-update commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/openrc/openrc_runlevel.py``.
    """

    # A reemote5 original with no pyinfra counterpart: pyinfra folds the
    # boot assignment into ``openrc.service``'s ``enabled`` option, so
    # managing an assignment *without* touching the runtime state needs
    # the whole service operation with ``running=None``. This standalone
    # operation is the proposal's dedicated runlevel-membership verb.
    # Idempotent on ``OpenrcEnabled``: membership in the requested
    # runlevel is compared and one ``RcUpdateCommand`` is yielded only on
    # a difference. No existence validation -- pyinfra's enabled branch
    # does not check the init script either; a service without an
    # ``/etc/init.d`` script fails in ``rc-update`` itself at apply time
    # (``CommandError``), and an unknown runlevel likewise.
    #
    # The command needs root: flag the request ``sudo=True``, ``doas=True``
    # or ``su=True`` -- stock Alpine ships no ``sudo``, so ``doas`` is
    # usually the escalator on an OpenRC host. ``**kwargs`` absorbs
    # pyinfra's global arguments (``op()`` already strips ``name`` and the
    # ``sudo``/``su``/``doas`` escalation flags).
    openrc_enabled = await ctx.facts.get(OpenrcEnabled, runlevel)
    is_enabled = openrc_enabled.get(service, False)

    if enabled and not is_enabled:
        ctx.changed = True
        ctx.reason = f"service {service} is not enabled in {runlevel}, will add"
        yield RcUpdateCommand(service=service, runlevel=runlevel, present=True)
    elif not enabled and is_enabled:
        ctx.changed = True
        ctx.reason = f"service {service} is enabled in {runlevel}, will remove"
        yield RcUpdateCommand(service=service, runlevel=runlevel, present=False)
    else:
        state = "enabled" if enabled else "not enabled"
        ctx.reason = f"service {service} is {state} in {runlevel}"


# --- openrc.custom_runlevel ------------------------------------------------------


async def custom_runlevel(
    ctx: OpContext,
    runlevel: str,
    present: bool = True,
    stacked_to: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Create or remove a custom OpenRC runlevel, optionally stacked.

    Args:
        ctx (OpContext): Execution context for the operation.
        runlevel (str): Name of the custom runlevel to create or remove
            (a directory under ``/etc/runlevels``).
        present (bool, optional): Whether the runlevel should exist
            (``True``) or be removed (``False``). Defaults to True.
        stacked_to (str | None, optional): An existing runlevel to stack
            this one onto (``rc-update -s add``, so entering the target
            also enters this runlevel); ``None`` leaves stacking alone.
            Ignored on the ``present=False`` direction -- removal always
            unstacks from every target the facts report. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated runlevel-directory and rc-update commands.

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/openrc/openrc_custom_runlevel.py``.
    """

    # A reemote5 original with no pyinfra counterpart: the proposal's
    # runlevel-management verb. Idempotent on ``OpenrcRunlevels`` (the
    # ``ls -R /etc/runlevels`` mapping): creation yields one
    # ``RunlevelCommand`` (``mkdir -p``) only when the directory is
    # absent, and stacking one ``RcUpdateCommand(stack=True)`` only when
    # the runlevel name is not already an entry of the target's list (a
    # stacked runlevel is a symlink inside the target directory, so the
    # fact sees it). Removal unstacks first -- one
    # ``RcUpdateCommand(stack=True, present=False)`` per target whose
    # list names the runlevel, in sorted target order so the request
    # sequence is stable -- then the ``RunlevelCommand`` ``rm -rf``:
    # rc-update(8) documents no runlevel-deletion verb, and deleting the
    # directory alone would leave dangling stack symlinks behind.
    #
    # Validation raises ``ValueError`` while materializing (in a deploy
    # run that is the prepare phase, before anything is sent): an empty
    # runlevel name, and stacking a runlevel onto itself. ``stacked_to``
    # must name an *existing* runlevel -- rc-update fails at apply time
    # (``CommandError``) otherwise; create the target first. The standard
    # runlevels (``boot``, ``default``, ``shutdown``, ``sysinit``) are not
    # special-cased: removing one is legal OpenRC-wise and this operation
    # does not guard against it.
    #
    # The commands need root: flag the request ``sudo=True``, ``doas=True``
    # or ``su=True`` -- stock Alpine ships no ``sudo``, so ``doas`` is
    # usually the escalator on an OpenRC host. ``**kwargs`` absorbs
    # pyinfra's global arguments (``op()`` already strips ``name`` and the
    # ``sudo``/``su``/``doas`` escalation flags).
    if not runlevel:
        raise ValueError("runlevel name must not be empty")
    if stacked_to == runlevel:
        raise ValueError(f"runlevel {runlevel} cannot be stacked onto itself")

    runlevels = await ctx.facts.get(OpenrcRunlevels)
    reasons: list[str] = []

    if present:
        if runlevel not in runlevels:
            ctx.changed = True
            reasons.append(f"runlevel {runlevel} does not exist, will create")
            yield RunlevelCommand(runlevel=runlevel, present=True)
        else:
            reasons.append(f"runlevel {runlevel} exists")
        if stacked_to is not None:
            if runlevel in runlevels.get(stacked_to, []):
                reasons.append(f"runlevel {runlevel} is stacked onto {stacked_to}")
            else:
                ctx.changed = True
                reasons.append(
                    f"runlevel {runlevel} is not stacked onto {stacked_to}, will stack"
                )
                yield RcUpdateCommand(
                    service=runlevel, runlevel=stacked_to, present=True, stack=True
                )
    elif runlevel in runlevels:
        # Unstack from every target before deleting the directory, so no
        # stack symlink is left dangling. Sorted target order keeps the
        # request sequence identical across phases.
        for target, names in sorted(runlevels.items()):
            if target != runlevel and runlevel in names:
                ctx.changed = True
                reasons.append(f"runlevel {runlevel} will be unstacked from {target}")
                yield RcUpdateCommand(
                    service=runlevel, runlevel=target, present=False, stack=True
                )
        ctx.changed = True
        reasons.append(f"runlevel {runlevel} exists, will remove")
        yield RunlevelCommand(runlevel=runlevel, present=False)
    else:
        reasons.append(f"runlevel {runlevel} does not exist")

    ctx.reason = "; ".join(reasons)


__all__ = [
    "OpenrcEnabled",
    "OpenrcRunlevels",
    "OpenrcServices",
    "OpenrcStatus",
    "OpenrcVersion",
    "RcServiceCommand",
    "RcUpdateCommand",
    "RunlevelCommand",
    "custom_runlevel",
    "runlevel",
    "service",
]