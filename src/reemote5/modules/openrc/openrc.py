# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``openrc`` module: facts and operations in one source.
#
# Fact side: ``OpenrcStatus`` and ``OpenrcEnabled`` are the two
# OpenRC facts -- the per-runlevel name -> running bool parsed from
# ``rc-status <runlevel>`` rows, and the name -> enabled-in-runlevel bool
# parsed from ``rc-update show -v``'s ``name | runlevels`` columns. Three
# reemote5 originals widen the visibility that pair leaves missing:
# ``OpenrcServices``, the same ``rc-status`` rows kept as their *raw* state
# words (``started``, ``stopped``, ``crashed``, ...) instead of the running
# bool, so a crashed service is distinguishable from a cleanly stopped one;
# ``OpenrcRunlevels``, the runlevel -> assigned-names mapping parsed from
# ``ls -R /etc/runlevels`` (a runlevel is a directory of symlinks, and a
# stacked runlevel appears as an entry in its target's directory); and
# ``OpenrcVersion``, the installed OpenRC release parsed from
# ``openrc --version`` (``None`` on a host without OpenRC); and
# ``OpenrcConfigFile``, the raw text of one configuration file
# (``/etc/rc.conf`` or ``/etc/conf.d/<service>``) the ``openrc.config``
# operation rewrites. Like every fact they are phase-unaware: their
# ``process`` coroutine just gathers the raw value.
#
# Operation side: ``openrc.service`` manages a service
# (running/restarted/reloaded/enabled/runlevel) through the
# ``RcServiceCommand``/``RcUpdateCommand`` descriptors, reading
# ``OpenrcStatus``/``OpenrcEnabled`` for idempotence; an arbitrary
# ``command`` option is deliberately dropped (an escape hatch for any
# rc-service verb defeats the typed-descriptor model -- ``server.shell``
# covers the rare custom verb). ``openrc.runlevel`` and
# ``openrc.custom_runlevel`` are reemote5 originals: the first manages one
# service's membership in one runlevel
# (``rc-update add``/``del``) as a standalone operation, the second manages
# the runlevel directories themselves (``mkdir -p``/``rm -rf`` under
# ``/etc/runlevels``) including stacking one runlevel onto another
# (``rc-update -s add``) and unstacking before removal -- rc-update(8)
# documents no verb that deletes a runlevel, so removal is the directory
# delete plus fact-derived ``rc-update -s delete`` unstacking.
# ``openrc.config`` is the third original: it sets or unsets one shell
# variable in ``/etc/rc.conf`` (or a service's ``/etc/conf.d/<service>``)
# through the spool-and-install write family -- the ``OpenrcConfigFile``
# content fact feeds a pure transform whose result is spooled to ``/tmp``
# over scp and installed over the root-owned file by an elevated ``sh -c``
# (``OpenrcConfigUploadCommand``/``OpenrcConfigInstallCommand``).
#
# Deliberately absent: an ``openrc.mask`` operation -- OpenRC has no
# masking mechanism (no systemd-style symlink-to-/dev/null), so masking
# cannot be honestly emulated; disable the service in every runlevel
# instead. A combined install-and-enable ``openrc.ensure`` is likewise not
# an operation: ``apk.packages`` followed by ``openrc.service`` composes in
# a deploy (``examples/modules/openrc/openrc_ensure.py``).
# Operations are async generators that receive an ``OpContext``, await
# facts, set ``ctx.changed``/``ctx.reason``, and yield command descriptors;
# they never touch the host directly.


import hashlib
import io
import re
import shlex
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from ...commands import Command, TransferCommand
from ...core import OpContext
from ...hosts import Host

# --- Shared helpers ----------------------------------------------------------

# The service-line regex: leading whitespace, the service
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


class OpenrcConfigFile:
    """The current content of an OpenRC configuration file, or None when absent.

    **Commands:**

    ```bash
    test -e <path> && { printf '#reemote5-openrcconfig\\n'; cat -- <path>; }
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
        Examples can be found in ``examples/modules/openrc/openrc_config.py``.
    """

    # The raw-content fact of this namespace (the ``ApkRepositoriesFile``/
    # ``AptSourcesFile``/``DnfRepoFile`` pattern): ``openrc.config`` reads the
    # managed file's *text* through it to compute the content it spools back,
    # and the fact never interprets that text. rc.conf and conf.d files are
    # shell-sourced and mostly comments -- rebuilding one from parsed
    # key/value pairs would destroy the comments, blank lines and ordering
    # the distro ships -- so the operation rewrites the verbatim text instead.
    # Absence is reported as ``None`` -- never the empty string -- so a file
    # that exists but is empty stays distinguishable from one that is missing,
    # which is what lets an unset delete an empty leftover.
    #
    # The path is an argument, not a constant, like ``ApkRepositoriesFile``:
    # the operation passes ``/etc/rc.conf`` or ``/etc/conf.d/<service>``. No
    # ``|| true`` fallback is needed -- the ``test -e`` guard already prints
    # nothing at all for an absent file (and an empty stdout reads as ``None``,
    # not an error: ``run_shell`` only raises on exit 127).

    #: Marker printed before the ``cat``'d content, only when ``test -e``
    #: passes, so ``process`` can tell an existing (possibly empty) file from
    #: a missing one (no marker on stdout).
    MARKER = "#reemote5-openrcconfig"

    @classmethod
    def command(cls, path: str) -> str:
        """Shell printing ``MARKER`` then the file's content, only if it exists.

        The ``test -e`` guard means a missing file produces no output at all
        (not even the marker), while an existing one -- empty or not -- always
        leads with the marker line (``ApkRepositoriesFile.command``'s form).
        """
        quoted = shlex.quote(path)
        return f"test -e {quoted} && {{ printf '{cls.MARKER}\\n'; cat -- {quoted}; }}"

    @classmethod
    async def process(cls, host: Host, path: str) -> str | None:
        remote_files = getattr(host, "remote_files", None)
        if remote_files is not None:
            # SimulatedHost seam: decode the content the simulated remote
            # filesystem holds at the path (None when absent). The same store
            # backs files.put/files.get and the ``openrc.config`` install
            # mutator, so a simulated write reads back through this fact.
            data = remote_files.get(path)
            return data.decode("utf-8") if data is not None else None
        # Real transport: cat the file over asyncssh behind the marker, so a
        # missing file (no marker) reads as None rather than empty content.
        stdout = await host.run_shell(cls.command(path))
        prefix = f"{cls.MARKER}\n"
        if not stdout.startswith(prefix):
            return None
        return stdout[len(prefix) :]


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

    # ``rc-update show -v``
    # prints every service as a ``name | runlevel ...`` row (the ``-v``
    # includes services assigned to no runlevel at all, whose right-hand
    # side is empty), and the fact answers membership in the requested
    # runlevel -- ``crond | default`` reads True for "default", False for
    # "boot". The ``runlevel`` argument selects the membership test, never
    # the shell: one command serves every runlevel.
    #
    # Deliberate design choices: a line carrying no ``|`` is
    # skipped instead of crashing the split (a bare ``line.split("|", 1)``
    # raises on a stray header or blank line; the parser stays total).
    # No command-existence gate: a host without
    # rc-update exits 127 and surfaces as ``CommandNotFoundError``.
    #
    # SimulatedHost seam note: the ``openrc_runlevels`` field stores the
    # runlevel -> names mapping (``OpenrcRunlevels``' shape), and this fact
    # projects membership from it. Only services assigned to *some*
    # runlevel appear in the projection -- an unassigned service (the
    # ``cgroups |`` row, False everywhere) is absent rather than False, so
    # consumers read ``.get(service, False)`` exactly as the
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

    # A reemote5 original: neither ``OpenrcStatus`` nor ``OpenrcEnabled``
    # reports the runlevel inventory itself. A runlevel *is* a directory
    # under
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
    # states ``OpenrcStatus`` collapses remain distinguishable --
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
    # Deliberate design choice: there is no command-existence gate -- a
    # host without rc-status
    # exits 127 and surfaces as ``CommandNotFoundError``.

    @classmethod
    def command(cls, runlevel: str = "default") -> str:
        """Shell reporting the runlevel's service states over SSH."""
        # The runlevel is ``shlex``-quoted so a name carrying shell
        # metacharacters is passed literally. No ``|| true`` guard: a
        # missing rc-status must surface as ``CommandNotFoundError``, the
        # ``OpenrcStatus`` behaviour.
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

    # ``rc-status <runlevel>``
    # prints one ``name [ state ]`` row per service assigned to the
    # runlevel, and the fact projects the state word to a running bool --
    # only ``started`` reads True, so ``stopped``, ``crashed`` and every
    # other state read False (``group(2) == "started"``). The
    # optional uptime suffix (``started 00:34:14 (10)``, ``started 15
    # day(s) 00:34:14 (10)``) is matched and ignored; the ``Runlevel:``
    # header and blank lines match no row. The regex is shared
    # with ``OpenrcServices``, which keeps the state word raw.
    #
    # Deliberate design choices: the runlevel is ``shlex``-quoted
    # in the shell rather than interpolated bare; there is no
    # command-existence gate -- a host without rc-status exits 127 and surfaces
    # as ``CommandNotFoundError``.
    #
    # SimulatedHost seam note: the ``openrc_service_states`` field stores
    # runlevel -> service -> raw state word (``OpenrcServices``' shape);
    # this fact projects it to the running bool, so one lossless store
    # backs both facts and a ``crashed`` entry reads False here.

    @classmethod
    def command(cls, runlevel: str = "default") -> str:
        """Shell reporting the runlevel's service states over SSH."""
        # ``rc-status {runlevel}`` with the runlevel quoted; for
        # the fixture runlevels ("default", "boot") the shell is the plain
        # ``rc-status <runlevel>`` form.
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

    # A reemote5 original: without a version fact an operation cannot adapt
    # to the OpenRC release present.
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
    ``action`` is ``start``, ``stop``, ``restart`` or ``reload`` (the
    ``rc-service {0} {1}`` formatter). The service name is ``shlex``-quoted,
    so a name carrying shell metacharacters
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
    (``rc-update add``/``rc-update del``, both arguments ``shlex``-quoted).
    ``stack=True`` inserts ``-s``: the name is then
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

    # Manage an OpenRC service's runtime and boot state (the
    # ``rc-service {0} {1}`` runtime direction plus the ``enabled``
    # boot branch). ``running`` compares the ``OpenrcStatus`` fact (only
    # ``started`` counts as running, so a crashed service is started);
    # ``restarted``/``reloaded`` act only on an already-running service --
    # a stopped one was just started above, or is meant to stay stopped;
    # ``enabled`` compares the ``OpenrcEnabled`` fact and yields
    # ``rc-update add``/``del`` for the runlevel. The no-op reasons are
    # the fixed wordings ("service X is running/stopped/enabled/
    # disabled"). ``running=None`` leaves the runtime state alone
    # (accepted even though the
    # operation's annotation says bool).
    #
    # Deliberate design choice: the arbitrary ``command`` option
    # (any rc-service verb, always executed) is dropped -- an untyped
    # escape hatch defeats the typed-descriptor model; ``server.shell``
    # covers the rare custom verb. ``**kwargs`` absorbs it and any
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

    # Only restart/reload a service that is already running (the
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

    # A reemote5 original: ``openrc.service`` folds the
    # boot assignment into its ``enabled`` option, so
    # managing an assignment *without* touching the runtime state needs
    # the whole service operation with ``running=None``. This standalone
    # operation is the proposal's dedicated runlevel-membership verb.
    # Idempotent on ``OpenrcEnabled``: membership in the requested
    # runlevel is compared and one ``RcUpdateCommand`` is yielded only on
    # a difference. No existence validation -- the enabled branch
    # does not check the init script either; a service without an
    # ``/etc/init.d`` script fails in ``rc-update`` itself at apply time
    # (``CommandError``), and an unknown runlevel likewise.
    #
    # The command needs root: flag the request ``sudo=True``, ``doas=True``
    # or ``su=True`` -- stock Alpine ships no ``sudo``, so ``doas`` is
    # usually the escalator on an OpenRC host. ``**kwargs`` absorbs
    # any further global arguments (``op()`` already strips ``name`` and the
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

    # A reemote5 original: the proposal's
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
    # any further global arguments (``op()`` already strips ``name`` and the
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


# --- openrc.config ---------------------------------------------------------------


def _config_key_pattern(key: str) -> str:
    """The anchored regex matching every assignment line of ``key``.

    Matches an active assignment (``rc_parallel="YES"``) and a
    commented-out one (``#rc_parallel=YES``), with optional surrounding
    whitespace and spaces around the ``=`` -- the same pattern the
    ``openrc_config.py`` recipe's ``rc_conf_edit`` used. Shared by both
    content transforms below, so the set never leaves a stale assignment
    the unset would have dropped, and vice versa. ``key`` is validated as
    a shell identifier by the operation, so ``re.escape`` is belt-and-
    braces for the regex, not a security boundary.
    """
    return rf"^[ \t]*#?[ \t]*{re.escape(key)}[ \t]*="


def _config_spool_path(dest: str) -> str:
    """The deterministic ``/tmp`` spool path ``openrc.config`` uploads to.

    Derived from the managed file's sha1 (``apk.repository``'s
    ``_repo_spool_path``, itself ``files.install``'s ``_install_spool_path``
    pattern): the derived name is exactly as deterministic across the two
    deploy phases as a fixed constant, and it keeps the ``/etc/rc.conf``
    request and every ``/etc/conf.d/<service>`` request from clobbering each
    other's spool -- one operation, many managed paths. The name avoids
    *collisions*, not a hostile local user: it is predictable, so on a
    multi-tenant host the ``/tmp`` spool is still a symlink-race exposure
    (the trade-off every spool-and-install operation here makes).
    """
    digest = hashlib.sha1(dest.encode()).hexdigest()[:16]
    return f"/tmp/reemote5-openrc-config-{digest}"


def _set_config_value(content: str | None, key: str, value: str) -> str:
    """The pure set transform: ``content`` carrying exactly one ``key="value"``.

    ``openrc.config(present=True)``'s edit of the managed file's current
    text: every line assigning ``key`` is dropped -- commented-out ones
    included, the recipe's replacement semantics, because leaving
    contradictory commented assignments around invites confusion -- then
    ``key="value"`` is appended (the file is sourced by the shell, so the
    value is double-quoted; the operation has already rejected values
    carrying shell-active characters). An absent file (``None``) becomes
    the single line. The result always ends in one trailing newline, and
    the edit is a fixpoint: the appended line matches the drop pattern, so
    a second pass reproduces the same content. Idempotent
    (``f(f(x)) == f(x)``) and side-effect free, so both deploy phases
    compute the same content from the same fact value.
    """
    pattern = re.compile(_config_key_pattern(key))
    lines = [line for line in (content or "").splitlines() if not pattern.match(line)]
    lines.append(f'{key}="{value}"')
    return "\n".join(lines) + "\n"


def _unset_config_value(content: str | None, key: str) -> str:
    """The pure unset transform: ``content`` without any assignment of ``key``.

    ``openrc.config(present=False)``'s edit: every line the shared key
    pattern matches is dropped -- active and commented-out assignments
    alike -- while the remaining lines keep their order, comments and the
    file's own trailing newline. An absent file and a file with no
    assignment come back unchanged (empty), which is what makes the
    transform a fixpoint; a file holding nothing but assignments of the
    key empties to ``""`` (a legitimate spool payload), never disappears.
    """
    if not content:
        return content or ""
    pattern = re.compile(_config_key_pattern(key))
    kept = [row for row in content.splitlines() if not pattern.match(row)]
    if not kept:
        return ""
    new = "\n".join(kept)
    if content.endswith("\n"):
        new += "\n"
    return new


@dataclass(frozen=True)
class OpenrcConfigUploadCommand(TransferCommand):
    """Spool the transformed OpenRC config content to a ``/tmp`` path on the target.

    The first half of ``openrc.config``'s write and a *non-shell* descriptor:
    it copies the content over the connection's scp channel via
    ``Host.upload_file`` rather than running shell text, so it has no
    ``shell()`` and its loggable form is the ``__str__`` rendering
    ``command_shell`` falls back to. Like ``ApkRepoUploadCommand`` the
    content is not a caller-supplied stream but the whole configuration
    text the operation just derived from the file's current content, so
    the field is a plain ``str`` -- hashable and identical across phases --
    wrapped in a fresh ``io.BytesIO`` only at transfer time. The copy rides
    as the login user (transfers are unprivileged, so the request's
    ``sudo``/``doas`` does not apply to it), which is why the spool path is
    world-writable ``/tmp`` rather than the root-owned ``/etc`` directory.
    """

    content: str  # the whole transformed rc.conf / conf.d text
    dest: str  # spool path on the target host

    def __str__(self) -> str:
        return f"scp <openrc config> -> {self.dest}"

    async def transfer(self, host: Host) -> None:
        await host.upload_file(io.BytesIO(self.content.encode()), self.dest)


@dataclass(frozen=True)
class OpenrcConfigInstallCommand(Command):
    """Install the spooled OpenRC config file ``0644``, then remove the spool file.

    The second half of ``openrc.config``'s write and the elevated one:
    writing ``/etc/rc.conf`` or ``/etc/conf.d/<service>`` needs root, so the
    request is flagged ``sudo=True``/``doas=True`` and the whole sequence is
    wrapped in ``sh -c`` -- a bare escalator prefix would elevate only the
    first command of the ``&&`` chain and leave the cleanup running as the
    login user, whereas ``doas sh -c '...'`` runs every step as root, which
    can always remove the login-user spool file. The sequence creates the
    destination directory (a service's ``/etc/conf.d`` entry may not exist
    yet), copies the spooled content over the target file, chmods it
    ``0644`` (the reset the overlay needs, because a ``cp`` would otherwise
    leave the login-user spool's mode on the root-owned file) and removes
    the spool -- the ``ApkRepoInstallCommand`` pattern. All paths are
    shell-quoted.
    """

    spool: str  # where OpenrcConfigUploadCommand wrote the content
    dest: str  # the /etc/rc.conf or /etc/conf.d/<service> path

    def shell(self) -> str:
        spool = shlex.quote(self.spool)
        dest = shlex.quote(self.dest)
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        inner = (
            f"mkdir -p {parent} && "
            f"cp {spool} {dest} && "
            f"chmod 0644 {dest} && rm -f {spool}"
        )
        return f"sh -c {shlex.quote(inner)}"


async def config(
    ctx: OpContext,
    key: str,
    value: str,
    service: str | None = None,
    present: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Set or unset a variable in /etc/rc.conf or a service's /etc/conf.d file.

    Args:
        ctx (OpContext): Execution context for the operation.
        key (str): Name of the shell variable to manage -- a shell
            identifier such as ``rc_parallel``.
        value (str): The value to assign, rendered as ``key="value"`` (the
            file is sourced by the shell). Must not contain ``"``, ``\\``,
            ``$``, a backtick or a newline. Ignored when ``present=False``.
        service (str | None, optional): The service whose
            ``/etc/conf.d/<service>`` file to edit; ``None`` edits the
            global ``/etc/rc.conf``. Defaults to None.
        present (bool, optional): Whether the variable should be set
            (``True``) or unset (``False`` -- every assignment of ``key``
            is dropped, commented-out ones included). Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated openrc config spool-and-install commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/openrc/openrc_config.py``.
    """

    # A reemote5 original: the ``openrc`` namespace otherwise ships only
    # ``service``. This operation supersedes the
    # ``files.edit`` + ``files.move`` spool-and-install recipe the
    # ``openrc_config.py`` example used to demonstrate, and is a member of
    # the spool-and-install write family: the managed file's current text is
    # read with the in-namespace ``OpenrcConfigFile`` content fact (the
    # ``ApkRepositoriesFile``/``AptSourcesFile`` pattern -- never rebuilt
    # from parsed key/value pairs, which would destroy the comments and
    # ordering these shell-sourced files ship with), one pure transform
    # computes the desired content, and when the result differs from what
    # is there the request yields *two* descriptors: an
    # ``OpenrcConfigUploadCommand`` transfer spooling the new text to a
    # ``/tmp`` path over scp, then an ``OpenrcConfigInstallCommand`` running
    # the elevated install that overlays the protected file and removes the
    # spool. Writing the file needs root, so flag the request ``sudo=True``,
    # ``doas=True`` or ``su=True`` -- stock Alpine ships no ``sudo``, so
    # ``doas`` is usually the escalator on an OpenRC host -- but the
    # escalation applies to the install only: the transfer always rides the
    # connection as the login user, which is exactly why the content detours
    # through ``/tmp``. The spool name is derived from the managed path
    # (``_config_spool_path``), so it stays identical across the two deploy
    # phases and the rc.conf request never shares a spool with a conf.d one.
    #
    # Idempotence is the transform's fixpoint: ``_set_config_value`` drops
    # every assignment of ``key`` (commented-out ones included -- the
    # recipe's replacement semantics, so no contradictory commented line
    # survives) and appends ``key="value"``; ``_unset_config_value`` drops
    # the same lines and keeps everything else. When the result equals the
    # file's current text the operation is a no-op -- a set whose value is
    # already the file's only assignment of the key, or an unset of a key
    # the file does not assign. An absent file reads ``None``: a set spools
    # the single line (the install's ``mkdir -p`` creates ``/etc/conf.d``
    # when needed), an unset no-ops.
    #
    # Validation raises ``ValueError`` while materializing (in a deploy run
    # that is the prepare phase, before anything is sent): a ``key`` that is
    # not a shell identifier, a ``service`` name that is empty, ``.``/``..``
    # or carries characters outside OpenRC's service-name charset (so the
    # path can never escape ``/etc/conf.d``), and -- on the set direction
    # only -- a ``value`` containing ``"``, ``\\``, ``$``, a backtick or a
    # newline, which would break out of the double-quoted rendering when
    # OpenRC sources the file. Variable references and other shell-active
    # values stay a ``files.edit`` recipe concern. There is no runtime half:
    # rc.conf is read when a runlevel is entered and conf.d when a service
    # starts, so the edit takes effect at the next boot / service restart.
    # ``**kwargs`` absorbs any further global arguments (``op()`` already
    # strips ``name`` and the ``sudo``/``su``/``doas`` escalation flags).
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        raise ValueError(f"not a shell variable name: {key!r}")
    if service is not None and (
        service in {".", ".."} or not re.fullmatch(r"[a-zA-Z0-9\-_.]+", service)
    ):
        raise ValueError(f"not an OpenRC service name: {service!r}")
    if present and any(char in value for char in '"\\$`\n\r'):
        raise ValueError(f"value carries shell-active characters: {value!r}")

    path = "/etc/rc.conf" if service is None else f"/etc/conf.d/{service}"
    current = await ctx.facts.get(OpenrcConfigFile, path)
    new = (
        _set_config_value(current, key, value)
        if present
        else _unset_config_value(current, key)
    )
    if new == (current or ""):
        # The transform is a fixpoint: the file already holds exactly the
        # desired state, so there is nothing to spool.
        ctx.reason = (
            f'{key}="{value}" is already set in {path}'
            if present
            else f"{key} is not set in {path}"
        )
        return

    ctx.changed = True
    spool = _config_spool_path(path)
    ctx.reason = (
        f'{key}="{value}" will be set in {path}'
        if present
        else f"{key} will be unset in {path}"
    )
    yield OpenrcConfigUploadCommand(content=new, dest=spool)
    yield OpenrcConfigInstallCommand(spool=spool, dest=path)


__all__ = [
    "OpenrcConfigFile",
    "OpenrcConfigInstallCommand",
    "OpenrcConfigUploadCommand",
    "OpenrcEnabled",
    "OpenrcRunlevels",
    "OpenrcServices",
    "OpenrcStatus",
    "OpenrcVersion",
    "RcServiceCommand",
    "RcUpdateCommand",
    "RunlevelCommand",
    "config",
    "custom_runlevel",
    "runlevel",
    "service",
]