# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``selinux`` module: facts and operations in one source.
#
# Fact side: ``SEBoolean``, ``FileContext``, ``FileContextMapping``,
# ``SEPorts`` and ``SEPort`` are ports of five established SELinux facts --
# the on/off string of one boolean (``getsebool``), the four-component
# context of one path (``stat -c %C``), the persistent fcontext mapping of
# one target expression (``semanage fcontext -n -l`` grepped), the
# protocol -> port -> type label map (``semanage port -ln``, ranges
# expanded) and the type of one protocol/port pair (``sepolicy network``).
# Seven reemote originals widen the visibility that set leaves missing:
# ``Booleans`` (every boolean at once, ``getsebool -a``), ``Mode`` (the
# runtime mode plus the config-file mode and loaded policy, ``sestatus``),
# ``SELogin`` (Linux user -> SELinux user mappings, ``semanage login -ln``),
# ``SEUser`` (SELinux user definitions, ``semanage user -ln``), ``Modules``
# (the installed policy modules with priority, format and enabled state,
# ``semodule -lfull``), ``PermissiveDomains`` (the domains exempted from
# enforcement, ``semanage permissive -l``) and ``SelinuxConfigFile`` (the
# verbatim text of ``/etc/selinux/config``, the raw-content fact
# ``selinux.config`` computes its spooled content from). Like every fact
# they are phase-unaware: their ``process`` coroutine just gathers the raw
# value.
#
# Operation side: ``selinux.boolean``, ``selinux.file_context``,
# ``selinux.file_context_mapping`` and ``selinux.port`` port four
# SELinux operations through typed command descriptors, reading the facts
# above for idempotence; ``file_context`` gains optional full-context
# components (user, role, level beside the type) and ``port`` keeps
# the ``-m`` modify branch. Seven reemote originals complete the
# toolchain: ``selinux.mode`` (runtime ``setenforce``, idempotent on
# ``Mode``), ``selinux.login`` (``semanage login -a/-m/-d``), ``selinux.module``
# (``semodule -i/-r/-e/-d`` with present/absent/enabled/disabled states),
# ``selinux.permissive_domain`` (``semanage permissive -a/-d``),
# ``selinux.restorecon`` (stateless relabel of a path tree),
# ``selinux.autorelabel`` (the ``/.autorelabel`` boot flag, idempotent on
# the selinux-local ``_AutorelabelPending`` existence probe -- the
# zero-cross-module-import invariant forbids reaching into ``files`` for
# its ``FileExists`` fact, the ``git._DirectoryExists`` pattern) and
# ``selinux.config`` (the persistent ``SELINUX``/``SELINUXTYPE`` boot
# settings, a spool-and-install rewrite of ``/etc/selinux/config``).
#
# Deliberately absent: an ``audit2allow`` operation -- generating policy
# from the audit log is a stateless, non-idempotent workflow whose output
# cannot be compared against a fact, so it stays a ``Shell`` action escape
# hatch.
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
from typing import Any, TypedDict

from ...commands import Command, TransferCommand
from ...core import OpContext
from ...hosts import Host

# --- Shared helpers ----------------------------------------------------------

# The context fields: the four context components in
# their canonical order. Shared by ``FileContext`` and ``FileContextMapping``
# so both facts build the same ``SeContext`` shape.
_CONTEXT_FIELDS = ("user", "role", "type", "level")

# The ``SEPorts`` row regex: a type name, a protocol word and
# the comma/space separated port list (single ports and ``start-stop``
# ranges) of one ``semanage port -ln`` row.
_PORT_LINE_REGEX = re.compile(r"^([\w_]+)\s+(\w+)\s+([\w\-,\s]+)$")

# The ``FileContextMapping`` context regex: the trailing
# ``user:role:type:level`` of a mapping row (the leading path expression and
# file-type columns are consumed by ``.*\s+``).
_MAPPING_CONTEXT_REGEX = re.compile(r"^.*\s+(\w+):(\w+):(\w+):(\w+)")


def _filter_ports(
    labels: dict[str, dict[int, str]],
    protocol: str | None,
    se_type: str | None,
) -> dict[str, dict[int, str]]:
    """Project a port-label map through the optional ``SEPorts`` filters.

    ``protocol`` keeps only that protocol's entry; ``se_type`` keeps only
    the ports carrying that type. A protocol left empty by the type filter
    is dropped, so a filtered read never reports an empty protocol dict.
    Pure; shared by the parse path and the SimulatedHost seam projection.
    """
    result: dict[str, dict[int, str]] = {}
    for proto, ports in labels.items():
        if protocol is not None and proto != protocol:
            continue
        kept = {
            port: label
            for port, label in ports.items()
            if se_type is None or label == se_type
        }
        if kept:
            result[proto] = kept
    return result


# --- Facts -------------------------------------------------------------------


class SEBoolean:
    """The status of one SELinux boolean as ``on`` or ``off``.

    **Commands:**

    ```bash
    getsebool <boolean>
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
        Examples can be found in ``examples/modules/selinux/selinux_se_boolean.py``.
    """

    # ``selinux.SEBoolean``: ``getsebool <boolean>``
    # prints one ``name --> on|off`` row and the fact keeps the value word.
    # An unknown boolean makes getsebool exit non-zero with empty stdout,
    # which reads as the empty string (an empty-string default); the
    # parser stays total where a naive ``output[0]`` split would raise.
    # The boolean name is ``shlex``-quoted (shell quoting in the
    # sibling operation), so a name carrying shell metacharacters is looked
    # up literally. There is no command-existence gate: a host
    # without getsebool exits 127 and surfaces as ``CommandNotFoundError``.
    #
    # SimulatedHost seam note: the ``selinux_booleans`` field stores every
    # boolean as a name -> bool mapping (``Booleans``' shape); this fact
    # projects the one requested name to "on"/"off", an absent name to "".

    @classmethod
    def command(cls, boolean: str) -> str:
        """Shell reporting one boolean's value over SSH."""
        return f"getsebool {shlex.quote(boolean)}"

    @classmethod
    def parse(cls, output: Iterable[str]) -> str:
        """Parse the ``getsebool`` row into its value word.

        The first non-blank line's ``--> `` suffix is the answer ("on" or
        "off"); empty or malformed output reads as the empty string. Pure
        and testable.
        """
        for raw in output:
            line = raw.strip()
            if line and " --> " in line:
                return line.split(" --> ", 1)[1].strip()
        return ""

    @classmethod
    async def process(cls, host: Host, boolean: str) -> str:
        booleans = getattr(host, "selinux_booleans", None)
        if booleans is not None:
            # SimulatedHost seam: project the one name from the bool map.
            # An unknown boolean reads as "", like the failed getsebool.
            if boolean not in booleans:
                return ""
            return "on" if booleans[boolean] else "off"
        # Real transport: query the boolean over asyncssh.
        stdout = await host.run_shell(cls.command(boolean))
        return cls.parse(stdout.splitlines())


class Booleans:
    """Every SELinux boolean and its on/off state.

    **Commands:**

    ```bash
    getsebool -a
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
        Examples can be found in ``examples/modules/selinux/selinux_booleans.py``.
    """

    # A reemote original: the single-boolean ``SEBoolean`` alone leaves
    # the whole boolean surface unaudited, which would otherwise need one
    # fact per name. ``getsebool -a`` prints every boolean as a
    # ``name --> on|off`` row; the fact keeps them all as name -> bool
    # entries (True for "on"), the lossless view a boolean audit reads.
    # A host with SELinux disabled prints its error to stderr and exits
    # non-zero with empty stdout, which reads as {} (check is off in
    # run_shell); a host without getsebool exits 127 and surfaces as
    # ``CommandNotFoundError``.
    #
    # SimulatedHost seam note: the ``selinux_booleans`` field stores this
    # exact shape and is copied on read; ``SEBoolean`` projects from the
    # same store, so one lossless field backs both facts.

    #: Shell listing every boolean and its value over SSH.
    COMMAND = "getsebool -a"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, bool]:
        """Parse ``getsebool -a`` rows into name -> on-bool entries.

        Every ``name --> on|off`` row contributes one entry (True only for
        "on"); blank or malformed lines contribute nothing. Pure and
        testable.
        """
        booleans: dict[str, bool] = {}
        for raw in output:
            line = raw.strip()
            if not line or " --> " not in line:
                continue
            name, value = line.split(" --> ", 1)
            booleans[name.strip()] = value.strip() == "on"
        return booleans

    @classmethod
    async def process(cls, host: Host) -> dict[str, bool]:
        booleans = getattr(host, "selinux_booleans", None)
        if booleans is not None:
            # SimulatedHost seam: read the in-memory mapping directly.
            # Copy so callers never mutate simulated state.
            return dict(booleans)
        # Real transport: list every boolean over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class SeContext(TypedDict):
    """
    Attributes:
        user (str): The SELinux user component (e.g. ``system_u``).
        role (str): The SELinux role component (e.g. ``object_r``).
        type (str): The SELinux type component (e.g. ``httpd_sys_content_t``).
        level (str): The MLS/MCS level component (e.g. ``s0``).
    """

    user: str
    role: str
    type: str
    level: str


# The shared entry type of ``FileContext`` and ``FileContextMapping``:
# Both facts return the same four-key dict, so one TypedDict
# backs the pair (the ``ApkPackageVersion`` shared-entry precedent). The
# ``type`` key shadows the builtin only as a dict key, never as a name.


class FileContext:
    """The SELinux context of one path, or ``None`` when unreadable.

    **Commands:**

    ```bash
    stat -c %C <path> || exit 0
    ```

    **Returns:**

    ```python
    SeContext | None
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/selinux/selinux_file_context.py``.
    """

    # ``selinux.FileContext``: ``stat -c %C`` prints the
    # path's context as ``user:role:type:level`` and the fact splits it
    # into the four components. A path on a filesystem without SELinux
    # labels prints ``?`` (fewer than four components) and a missing path
    # prints nothing (the ``|| exit 0`` guard swallows stat's error), both
    # reading as None -- the documented None-on-absent convention. The guard
    # masks every non-zero exit including 127, so only
    # ``HostConnectionError`` can escape the gather. The path is
    # ``shlex``-quoted (a deliberate hardening over bare
    # interpolation; the fixture paths are unaffected).
    #
    # SimulatedHost seam note: the ``selinux_file_contexts`` field stores
    # path -> SeContext entries; an absent path reads as None. The chcon
    # mutator edits the same store, so a relabel is observable through this
    # fact on the next step.

    @classmethod
    def command(cls, path: str) -> str:
        """Shell printing the path's context, tolerating its absence."""
        return f"stat -c %C {shlex.quote(path)} || exit 0"

    @classmethod
    def parse(cls, output: Iterable[str]) -> SeContext | None:
        """Parse the ``stat -c %C`` line into a context, or None.

        The first line is split on ``:``; fewer than four components (the
        ``?`` of an unlabelled filesystem, or empty output for a missing
        path) reads as None. Pure and testable.
        """
        for raw in output:
            components = raw.strip().split(":")
            if len(components) < 4:
                return None
            return SeContext(
                user=components[0],
                role=components[1],
                type=components[2],
                level=components[3],
            )
        return None

    @classmethod
    async def process(cls, host: Host, path: str) -> SeContext | None:
        contexts = getattr(host, "selinux_file_contexts", None)
        if contexts is not None:
            # SimulatedHost seam: read the path's entry directly. Copy so
            # callers never mutate simulated state; absent reads as None.
            context = contexts.get(path)
            return dict(context) if context is not None else None  # type: ignore[return-value]
        # Real transport: stat the context over asyncssh.
        stdout = await host.run_shell(cls.command(path))
        return cls.parse(stdout.splitlines())


class FileContextMapping:
    """The persistent fcontext mapping of one target, or ``None``.

    **Commands:**

    ```bash
    set -o pipefail && semanage fcontext -n -l | (grep <target> || true)
    ```

    **Returns:**

    ```python
    SeContext | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/selinux/selinux_file_context_mapping.py``.
    """

    # ``selinux.FileContextMapping``: the local-only
    # fcontext database (``semanage fcontext -n -l``) is grepped for rows
    # starting with the target expression and the trailing
    # ``user:role:type:level`` of the single matching row is split into the
    # same ``SeContext`` shape ``FileContext`` returns. Equivalence rows
    # (``/etc/systemd/system = /usr/lib/systemd/system``) carry no context
    # and match nothing. ``set -o pipefail`` propagates semanage's exit
    # status through the pipeline (the ``|| true`` guards only grep), so a
    # host without semanage exits 127 and surfaces as
    # ``CommandNotFoundError``; the pipefail option needs a bash/zsh login
    # shell, the stock shell on every SELinux distribution. The grep
    # pattern is ``shlex``-quoted (rather than wrapped in bare single quotes;
    # the fixture targets render identically).
    #
    # Deliberate design choice: no mapping reads as ``None``
    # rather than ``{}`` -- the house None-on-absent convention
    # (``ApkPackage``, ``FileContext``), so an absent mapping stays
    # distinguishable from a context with empty components.
    #
    # SimulatedHost seam note: the ``selinux_fcontext_mappings`` field
    # stores target -> SeContext entries keyed by the exact target
    # expression; the semanage fcontext mutator edits the same store.

    @classmethod
    def command(cls, target: str) -> str:
        """Shell grepping the local fcontext database for the target."""
        return (
            "set -o pipefail && semanage fcontext -n -l | "
            f"(grep {shlex.quote('^' + target)} || true)"
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> SeContext | None:
        """Parse the single grepped mapping row into a context, or None.

        Exactly one output line carrying a trailing four-component context
        is a mapping; zero lines (no match) or several (a prefix shared by
        longer targets) read as None, the ``len(output) != 1`` gate.
        Pure and testable.
        """
        lines = list(output)
        if len(lines) != 1:
            return None
        match = _MAPPING_CONTEXT_REGEX.match(lines[0])
        if match is None:
            return None
        return SeContext(
            user=match.group(1),
            role=match.group(2),
            type=match.group(3),
            level=match.group(4),
        )

    @classmethod
    async def process(cls, host: Host, target: str) -> SeContext | None:
        mappings = getattr(host, "selinux_fcontext_mappings", None)
        if mappings is not None:
            # SimulatedHost seam: read the target's entry directly. Copy so
            # callers never mutate simulated state; absent reads as None.
            context = mappings.get(target)
            return dict(context) if context is not None else None  # type: ignore[return-value]
        # Real transport: grep the fcontext database over asyncssh.
        stdout = await host.run_shell(cls.command(target))
        return cls.parse(stdout.splitlines())


class SEPorts:
    """The SELinux type labels of every defined port, by protocol.

    **Commands:**

    ```bash
    semanage port -ln
    ```

    **Returns:**

    ```python
    dict[str, dict[int, str]]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/selinux/selinux_se_ports.py``.
    """

    # ``selinux.SEPorts``: ``semanage port -ln`` prints
    # one ``type protocol ports`` row per label, the port list mixing
    # single ports and ``start-stop`` ranges; every range is expanded into
    # its individual port numbers under the protocol key. Rows of the
    # ``unreserved_port_t`` type are skipped (they cover the whole
    # unreserved space and would drown the specific labels), and lines
    # matching nothing (headers, noise) are ignored -- the parser stays
    # total. There is no command-existence gate: a host
    # without semanage exits 127 and surfaces as ``CommandNotFoundError``.
    #
    # reemote extension: the optional ``protocol`` and ``se_type``
    # arguments filter the parsed map (one protocol's ports, or only the
    # ports carrying one type), for port-audit scenarios; the shell is
    # unchanged -- one command serves every filter, applied after parsing
    # on both transports. A protocol left empty by the type filter is
    # dropped from the result.
    #
    # SimulatedHost seam note: the ``selinux_ports`` field stores this
    # exact protocol -> port -> type shape (deep-copied on read); the
    # semanage port mutator edits the same store and ``SEPort`` projects
    # from it.

    #: Shell listing every port label over SSH.
    COMMAND = "semanage port -ln"

    @classmethod
    def parse(
        cls,
        output: Iterable[str],
        protocol: str | None = None,
        se_type: str | None = None,
    ) -> dict[str, dict[int, str]]:
        """Parse ``semanage port -ln`` rows into protocol -> port -> type.

        Each matching row expands its comma-separated port list (single
        ports and ``start-stop`` ranges) into individual int keys;
        ``unreserved_port_t`` rows and non-matching lines contribute
        nothing. The optional filters keep one protocol and/or one type.
        Pure and testable.
        """
        labels: dict[str, dict[int, str]] = {}
        for line in output:
            match = _PORT_LINE_REGEX.match(line)
            if match is None:
                continue
            if match.group(1) == "unreserved_port_t":
                continue
            for item in match.group(3).split(","):
                item = item.strip()
                if "-" in item:
                    start, stop = item.split("-", 1)
                    ports = range(int(start), int(stop) + 1)
                else:
                    ports = range(int(item), int(item) + 1)
                labels.setdefault(match.group(2), {}).update(
                    dict.fromkeys(ports, match.group(1))
                )
        return _filter_ports(labels, protocol, se_type)

    @classmethod
    async def process(
        cls,
        host: Host,
        protocol: str | None = None,
        se_type: str | None = None,
    ) -> dict[str, dict[int, str]]:
        ports = getattr(host, "selinux_ports", None)
        if ports is not None:
            # SimulatedHost seam: deep-copy the in-memory label map, then
            # apply the same filters as the parse path.
            copied = {proto: dict(entries) for proto, entries in ports.items()}
            return _filter_ports(copied, protocol, se_type)
        # Real transport: list the port labels over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines(), protocol, se_type)


class SEPort:
    """The SELinux type of one protocol/port pair, or the empty string.

    **Commands:**

    ```bash
    (sepolicy network -p <port> 2>/dev/null || true) | grep <protocol>
    ```

    **Returns:**

    ```python
    str
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/selinux/selinux_se_port.py``.
    """

    # ``selinux.SEPort``: ``sepolicy network -p <port>``
    # prints the specific label row first and the generic range row second
    # (``22: tcp ssh_port_t 22`` then ``22: tcp reserved_port_t 1-511``);
    # the fact keeps the type of the first row only when BOTH rows are
    # present -- a port covered by a generic range alone reads as the
    # empty string, the ``len(output) > 1`` gate. The ``|| true``
    # inside the subshell swallows sepolicy's failure (including its
    # absence: policycoreutils-dev is not stock), and the outer pipeline
    # reports grep's exit, so only ``HostConnectionError`` can escape the
    # gather. The protocol is ``shlex``-quoted and the port rendered
    # through ``str`` (a name like "portname" is accepted in the
    # fixtures, which reads as "").
    #
    # SimulatedHost seam note: the ``selinux_ports`` field stores the
    # semanage view (specific labels only), so the seam projection cannot
    # distinguish a generic-range-only port from an unlabelled one -- both
    # read as "", which is the answer the operation's idempotence needs.

    @classmethod
    def command(cls, protocol: str, port: int | str) -> str:
        """Shell querying one port's labels, filtered to the protocol."""
        return (
            f"(sepolicy network -p {shlex.quote(str(port))} 2>/dev/null || true)"
            f" | grep {shlex.quote(protocol)}"
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> str:
        """Parse the ``sepolicy network`` rows into the specific type.

        Two or more rows mean the first is the specific label and its
        third whitespace-separated field is the type; fewer rows (no
        label, or the generic range alone) read as the empty string. Pure
        and testable.
        """
        lines = list(output)
        if len(lines) > 1:
            return lines[0].split(" ")[2]
        return ""

    @classmethod
    async def process(cls, host: Host, protocol: str, port: int | str) -> str:
        ports = getattr(host, "selinux_ports", None)
        if ports is not None:
            # SimulatedHost seam: project the one port from the label map.
            # A non-numeric port (the "portname" fixture) reads as "".
            try:
                number = int(port)
            except ValueError:
                return ""
            return ports.get(protocol, {}).get(number, "")
        # Real transport: query sepolicy over asyncssh.
        stdout = await host.run_shell(cls.command(protocol, port))
        return cls.parse(stdout.splitlines())


class ModeInfo(TypedDict):
    """
    Attributes:
        mode (str): The runtime mode -- ``enforcing``, ``permissive`` or
            ``disabled`` (empty when sestatus reports nothing).
        config_mode (str): The mode configured in ``/etc/selinux/config``
            for the next boot (empty when SELinux is disabled, which
            sestatus then reports alone).
        policy (str): The loaded policy name, e.g. ``targeted`` or ``mls``
            (empty when SELinux is disabled).
    """

    mode: str
    config_mode: str
    policy: str


# The entry type of ``Mode``: one record carrying the runtime mode beside
# the persistent config-file mode and the loaded policy name, so a deploy
# can tell "enforcing now but permissive after reboot" apart from a fully
# enforcing host.


class Mode:
    """The SELinux runtime mode, config-file mode and loaded policy.

    **Commands:**

    ```bash
    sestatus
    ```

    **Returns:**

    ```python
    ModeInfo
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/selinux/selinux_mode.py``.
    """

    # A reemote original: the ``selinux`` namespace ships no mode
    # fact, so every SELinux decision starts with a manual getenforce.
    # ``sestatus`` answers the whole question in one command -- the
    # ``Current mode`` line is the runtime state, ``Mode from config file``
    # the persistent one and ``Loaded policy name`` the policy type -- so
    # the fact needs no second read of /etc/selinux/config. A disabled
    # subsystem prints only ``SELinux status: disabled``: the mode reads
    # "disabled" and the config/policy fields stay empty (sestatus reports
    # neither in that state; read the config file with ``SelinuxConfigFile``
    # when the boot setting matters on a disabled host). Empty output reads
    # as an empty mode rather than guessing. There is no
    # command-existence gate: a host without
    # sestatus (policycoreutils absent, or a non-SELinux distribution)
    # exits 127 and surfaces as ``CommandNotFoundError``.
    #
    # SimulatedHost seam note: the ``selinux_mode`` field stores this exact
    # ModeInfo shape (defaulting to an enforcing/targeted host) and is
    # copied on read; the setenforce mutator edits the runtime ``mode``
    # key only, mirroring setenforce's runtime-only effect, while the
    # ``selinux.config`` install mutator edits ``config_mode`` from the
    # installed file's SELINUX assignment (sestatus reads the file live).

    #: Shell reporting the SELinux status summary over SSH.
    COMMAND = "sestatus"

    @classmethod
    def parse(cls, output: Iterable[str]) -> ModeInfo:
        """Parse ``sestatus`` key/value lines into the mode record.

        The ``Current mode`` and ``Mode from config file`` values are
        lowercased; ``Loaded policy name`` is kept verbatim. A disabled
        subsystem (or empty output) yields empty config/policy fields.
        Pure and testable.
        """
        fields: dict[str, str] = {}
        for raw in output:
            line = raw.strip()
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
        if not fields or fields.get("SELinux status", "").lower() == "disabled":
            return ModeInfo(
                mode="disabled" if fields else "",
                config_mode="",
                policy="",
            )
        return ModeInfo(
            mode=fields.get("Current mode", "").lower(),
            config_mode=fields.get("Mode from config file", "").lower(),
            policy=fields.get("Loaded policy name", ""),
        )

    @classmethod
    async def process(cls, host: Host) -> ModeInfo:
        mode = getattr(host, "selinux_mode", None)
        if mode is not None:
            # SimulatedHost seam: read the in-memory record directly.
            # Copy so callers never mutate simulated state.
            return dict(mode)  # type: ignore[return-value]
        # Real transport: query sestatus over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class SelinuxConfigFile:
    """The current content of the SELinux config file, or None when absent.

    **Commands:**

    ```bash
    test -e <path> && { printf '#reemote-selinuxconfig\\n'; cat -- <path>; }
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
        Examples can be found in ``examples/modules/selinux/selinux_config.py``.
    """

    # The raw-content fact of this namespace (the ``OpenrcConfigFile``/
    # ``ApkRepositoriesFile``/``AptSourcesFile`` pattern): ``selinux.config``
    # reads the managed file's *text* through it to compute the content it
    # spools back, and the fact never interprets that text. The stock
    # /etc/selinux/config is mostly comments -- rebuilding it from a parsed
    # setting (``Mode``'s config_mode reports only the SELINUX value, and
    # nothing at all on a disabled host) would destroy the comments, blank
    # lines and ordering the distro ships -- so the operation rewrites the
    # verbatim text instead. Absence is reported as ``None`` -- never the
    # empty string -- so a file that exists but is empty stays
    # distinguishable from one that is missing.
    #
    # The path is an argument, not a constant, like ``OpenrcConfigFile``:
    # the operation passes the fixed ``/etc/selinux/config``. No ``|| true``
    # fallback is needed -- the ``test -e`` guard already prints nothing at
    # all for an absent file (and an empty stdout reads as ``None``, not an
    # error: ``run_shell`` only raises on exit 127).

    #: Marker printed before the ``cat``'d content, only when ``test -e``
    #: passes, so ``process`` can tell an existing (possibly empty) file from
    #: a missing one (no marker on stdout).
    MARKER = "#reemote-selinuxconfig"

    @classmethod
    def command(cls, path: str) -> str:
        """Shell printing ``MARKER`` then the file's content, only if it exists.

        The ``test -e`` guard means a missing file produces no output at all
        (not even the marker), while an existing one -- empty or not -- always
        leads with the marker line (``OpenrcConfigFile.command``'s form).
        """
        quoted = shlex.quote(path)
        return f"test -e {quoted} && {{ printf '{cls.MARKER}\\n'; cat -- {quoted}; }}"

    @classmethod
    async def process(cls, host: Host, path: str) -> str | None:
        remote_files = getattr(host, "remote_files", None)
        if remote_files is not None:
            # SimulatedHost seam: decode the content the simulated remote
            # filesystem holds at the path (None when absent). The same
            # store backs files.put/files.get and the ``selinux.config``
            # install mutator, so a simulated write reads back through
            # this fact.
            data = remote_files.get(path)
            return data.decode("utf-8") if data is not None else None
        # Real transport: cat the file over asyncssh behind the marker, so a
        # missing file (no marker) reads as None rather than empty content.
        stdout = await host.run_shell(cls.command(path))
        prefix = f"{cls.MARKER}\n"
        if not stdout.startswith(prefix):
            return None
        return stdout[len(prefix) :]


class SeLogin(TypedDict):
    """
    Attributes:
        seuser (str): The SELinux user the login maps to (e.g.
            ``staff_u``).
        mls_range (str): The MLS/MCS range of the mapping (e.g. ``s0`` or
            ``s0-s0:c0.c1023``).
        service (str): The PAM service column of newer semanage releases;
            ``*`` when the column is absent (older releases) or universal.
    """

    seuser: str
    mls_range: str
    service: str


# The entry type of ``SELogin``, keyed by the Linux login name in the
# fact's outer dict (the ``__default__`` row is the fallback mapping every
# unlisted login inherits).


class SELogin:
    """The SELinux user and range each Linux login maps to.

    **Commands:**

    ```bash
    semanage login -ln
    ```

    **Returns:**

    ```python
    dict[str, SeLogin]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/selinux/selinux_se_login.py``.
    """

    # A reemote original: the ``selinux`` namespace ships no login
    # fact, so confined-user management has no visibility. ``semanage
    # login -ln`` prints one ``login seuser range [service]`` row per
    # mapping (the ``-n`` drops the header); the fact keys them by the
    # Linux login name, keeping the SELinux user, the MLS/MCS range and
    # the Service column newer releases add (``*`` when the column is
    # absent). The ``__default__`` row is kept like any other -- it is the
    # fallback mapping unlisted logins inherit. Rows with fewer than three
    # fields contribute nothing (the parser stays total). A host without
    # semanage exits 127 and surfaces as ``CommandNotFoundError``.
    #
    # SimulatedHost seam note: the ``selinux_logins`` field stores this
    # exact login -> SeLogin shape (per-entry copied on read); the
    # semanage login mutator edits the same store.

    #: Shell listing the login mappings without headings over SSH.
    COMMAND = "semanage login -ln"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, SeLogin]:
        """Parse ``semanage login -ln`` rows into login-keyed entries.

        Each row's first three fields are the login name, SELinux user and
        MLS/MCS range; a fourth field is the Service column (``*`` when
        absent). Short or blank lines contribute nothing. Pure and
        testable.
        """
        logins: dict[str, SeLogin] = {}
        for raw in output:
            fields = raw.split()
            if len(fields) < 3:
                continue
            logins[fields[0]] = SeLogin(
                seuser=fields[1],
                mls_range=fields[2],
                service=fields[3] if len(fields) > 3 else "*",
            )
        return logins

    @classmethod
    async def process(cls, host: Host) -> dict[str, SeLogin]:
        logins = getattr(host, "selinux_logins", None)
        if logins is not None:
            # SimulatedHost seam: read the in-memory mapping directly.
            # Copy each entry so callers never mutate simulated state.
            return {name: dict(entry) for name, entry in logins.items()}  # type: ignore[misc]
        # Real transport: list the login mappings over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class SeUser(TypedDict):
    """
    Attributes:
        prefix (str): The labeling prefix SELinux user files are created
            with (e.g. ``user``).
        mls_level (str): The MLS/MCS level of the user (e.g. ``s0``).
        mls_range (str): The MLS/MCS range the user may enter (e.g.
            ``s0-s0:c0.c1023``).
        roles (list[str]): The SELinux roles the user may assume, in
            listing order (e.g. ``["staff_r", "sysadm_r"]``).
    """

    prefix: str
    mls_level: str
    mls_range: str
    roles: list[str]


# The entry type of ``SEUser``, keyed by the SELinux user name in the
# fact's outer dict.


class SEUser:
    """The SELinux user definitions: prefix, levels and allowed roles.

    **Commands:**

    ```bash
    semanage user -ln
    ```

    **Returns:**

    ```python
    dict[str, SeUser]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/selinux/selinux_se_user.py``.
    """

    # A reemote original complementing ``SELogin``: the login mapping
    # names a SELinux user, and this fact reports what that user *is* --
    # ``semanage user -ln`` prints one ``user prefix level range roles...``
    # row per definition (the ``-n`` drops the two header lines), the
    # roles being the variable-length tail. The fact keys the rows by the
    # SELinux user name and splits the tail into a roles list. Rows with
    # fewer than five fields contribute nothing (the parser stays total).
    # A host without semanage exits 127 and surfaces as
    # ``CommandNotFoundError``.
    #
    # SimulatedHost seam note: the ``selinux_users`` field stores this
    # exact user -> SeUser shape; entries are copied on read with their
    # roles lists rebuilt, so callers never mutate simulated state. This
    # seam is read-only -- no migrated operation edits SELinux user
    # definitions (``semanage user`` management is left to the ``Shell``
    # action).

    #: Shell listing the SELinux user definitions without headings.
    COMMAND = "semanage user -ln"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, SeUser]:
        """Parse ``semanage user -ln`` rows into user-keyed entries.

        Each row's first four fields are the user name, labeling prefix,
        MLS level and MLS range; the remaining fields are the allowed
        roles. Short or blank lines contribute nothing. Pure and testable.
        """
        users: dict[str, SeUser] = {}
        for raw in output:
            fields = raw.split(None, 4)
            if len(fields) < 5:
                continue
            users[fields[0]] = SeUser(
                prefix=fields[1],
                mls_level=fields[2],
                mls_range=fields[3],
                roles=fields[4].split(),
            )
        return users

    @classmethod
    async def process(cls, host: Host) -> dict[str, SeUser]:
        users = getattr(host, "selinux_users", None)
        if users is not None:
            # SimulatedHost seam: read the in-memory mapping directly.
            # Copy each entry and its roles list so callers never mutate
            # simulated state.
            return {
                name: SeUser(
                    prefix=entry["prefix"],
                    mls_level=entry["mls_level"],
                    mls_range=entry["mls_range"],
                    roles=list(entry["roles"]),
                )
                for name, entry in users.items()
            }
        # Real transport: list the user definitions over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class Module(TypedDict):
    """
    Attributes:
        name (str): The policy module name (e.g. ``myhttpd``).
        priority (int): The module's priority in the store (400 is the
            default; higher priorities win when names collide).
        format (str): The module's language format -- ``pp`` (HLL policy
            package) or ``cil``.
        enabled (bool): Whether the module is enabled; ``semodule -lfull``
            marks disabled instances with a trailing ``disabled`` word.
    """

    name: str
    priority: int
    format: str
    enabled: bool


# The entry type of ``Modules``: one installed module instance. A name may
# appear at several priorities, so the fact returns a list of instances
# rather than a name-keyed dict.


class Modules:
    """The installed SELinux policy modules, with priority and state.

    **Commands:**

    ```bash
    semodule -lfull
    ```

    **Returns:**

    ```python
    list[Module]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/selinux/selinux_modules.py``.
    """

    # A reemote original: the ``selinux`` namespace ships no module
    # fact, so custom-policy management has no visibility. ``semodule
    # -lfull`` lists *every* module instance in the store as a
    # ``priority name format [disabled]`` row -- unlike ``semodule -l``,
    # which hides disabled instances and lower-priority duplicates -- so
    # the fact can answer both "is it installed" and "is it enabled", the
    # two questions ``selinux.module``'s state machine needs.
    #
    # Deliberate deviation from the original proposal's name-and-version
    # shape: modern semodule (policycoreutils >= 2.7) no longer prints
    # module versions in its listings; the priority, format and enabled
    # state are the modern equivalents and what the module operation
    # actually compares. Rows whose first field is not a number (headers,
    # notices) contribute nothing -- the parser stays total. A host
    # without semodule exits 127 and surfaces as ``CommandNotFoundError``.
    #
    # SimulatedHost seam note: the ``selinux_modules`` field stores this
    # exact list-of-instances shape (per-entry copied on read); the
    # semodule mutators edit the same store.

    #: Shell listing every module instance with priority and state.
    COMMAND = "semodule -lfull"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[Module]:
        """Parse ``semodule -lfull`` rows into module instances.

        Each ``priority name format [disabled]`` row contributes one
        entry in listing order; a trailing ``disabled`` word reads as
        ``enabled=False``. Non-numeric-priority lines contribute nothing.
        Pure and testable.
        """
        modules: list[Module] = []
        for raw in output:
            fields = raw.split()
            if len(fields) < 3 or not fields[0].isdigit():
                continue
            modules.append(
                Module(
                    name=fields[1],
                    priority=int(fields[0]),
                    format=fields[2],
                    enabled="disabled" not in fields[3:],
                )
            )
        return modules

    @classmethod
    async def process(cls, host: Host) -> list[Module]:
        modules = getattr(host, "selinux_modules", None)
        if modules is not None:
            # SimulatedHost seam: read the in-memory list directly. Copy
            # each entry so callers never mutate simulated state.
            return [dict(entry) for entry in modules]  # type: ignore[misc]
        # Real transport: list the module store over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class PermissiveDomains:
    """The domains running in permissive mode despite global enforcement.

    **Commands:**

    ```bash
    semanage permissive -l
    ```

    **Returns:**

    ```python
    list[str]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/selinux/selinux_permissive_domains.py``.
    """

    # A reemote original: the ``selinux`` namespace ships no
    # permissive-domain fact, so troubleshooting a single denied service
    # has no visibility short of switching the whole host to permissive.
    # ``semanage permissive -l`` lists the built-in permissive types and
    # the customized ones under two section headers; the fact keeps every
    # domain name from both sections (a built-in domain is permissive too
    # -- ``selinux.permissive_domain`` reads the same list for its
    # idempotence), dropping the headers and blank lines and deduping
    # while preserving listing order. A host without semanage exits 127
    # and surfaces as ``CommandNotFoundError``.
    #
    # SimulatedHost seam note: the ``selinux_permissive_domains`` field
    # stores this exact list of domain names (copied on read); the
    # semanage permissive mutator edits the same store.

    #: Shell listing the permissive domains over SSH.
    COMMAND = "semanage permissive -l"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[str]:
        """Parse ``semanage permissive -l`` output into domain names.

        The two section headers (``Built-in Permissive Types`` and
        ``Customized Permissive Types``) and blank lines contribute
        nothing; every other line is one domain, kept in listing order
        with duplicates dropped. Pure and testable.
        """
        domains: list[str] = []
        for raw in output:
            line = raw.strip()
            if not line or line.endswith("Permissive Types"):
                continue
            if line not in domains:
                domains.append(line)
        return domains

    @classmethod
    async def process(cls, host: Host) -> list[str]:
        domains = getattr(host, "selinux_permissive_domains", None)
        if domains is not None:
            # SimulatedHost seam: read the in-memory list directly. Copy
            # so callers never mutate simulated state.
            return list(domains)
        # Real transport: list the permissive domains over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------


#: The boot flag ``selinux.autorelabel`` creates: its presence tells the
#: next boot to relabel every filesystem before enforcing begins.
AUTORELABEL_PATH = "/.autorelabel"


class _Which:
    """The path of a command per ``command -v``, or None (selinux-local ``server.Which``).

    A private, in-namespace stand-in for ``server.Which``: ``selinux.port``
    must learn whether ``sepolicy`` is installed to pick its fact (a
    ``Which`` dispatch), but the zero-cross-module-import invariant forbids
    reaching into the ``server`` namespace -- the ``git._DirectoryExists``
    pattern. It reads the *same* ``command_paths`` seam ``server.Which``
    reads (name -> resolved path, an absent name None) and runs the same
    ``command -v <name> || true`` on the real transport, whose swallowed
    failure means only ``HostConnectionError`` can escape the probe.
    """

    @classmethod
    async def process(cls, host: Host, command: str) -> str | None:
        paths = getattr(host, "command_paths", None)
        if paths is not None:
            # SimulatedHost seam (shared with server.Which): name -> path.
            # str is immutable, so no copy is needed; an unknown command
            # reads as None, like the swallowed non-zero exit.
            return paths.get(command)
        # Real transport: `command -v` over asyncssh, the failure swallowed.
        stdout = await host.run_shell(f"command -v {shlex.quote(command)} || true")
        for line in stdout.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return None


class _AutorelabelPending:
    """Whether ``/.autorelabel`` exists (selinux-local ``files.FileExists``).

    A private, in-namespace stand-in for ``files.FileExists``:
    ``selinux.autorelabel`` must learn whether the relabel boot flag is
    already set, but the zero-cross-module-import invariant forbids reaching
    into the ``files`` namespace -- the ``git._DirectoryExists`` pattern. It
    reads the *same* ``existing_files`` seam ``files.FileExists`` reads (the
    store of existing paths) and runs the same ``test -e`` probe on the real
    transport, whose non-zero exit on a missing flag maps to False, so only
    ``HostConnectionError`` can escape it.
    """

    @classmethod
    async def process(cls, host: Host) -> bool:
        existing = getattr(host, "existing_files", None)
        if existing is not None:
            # SimulatedHost seam (shared with files.FileExists): the flag's
            # presence is membership in the existing-path store.
            return AUTORELABEL_PATH in existing
        # Real transport: `test -e` over asyncssh (check is off in run_shell).
        stdout = await host.run_shell(
            f"test -e {shlex.quote(AUTORELABEL_PATH)} && echo yes"
        )
        return stdout.strip() == "yes"


# --- selinux.boolean -----------------------------------------------------------


@dataclass(frozen=True)
class SetseboolCommand(Command):
    """``setsebool [-P] <boolean> on|off``: one boolean change.

    The descriptor ``selinux.boolean`` yields. ``persistent`` inserts ``-P``
    (the ``setsebool -P`` form), which writes the value into the policy so
    it survives reboot; without it the change is runtime-only. The boolean
    name is ``shlex``-quoted; the value word is
    the validated ``on``/``off`` literal, never user text.
    """

    boolean: str
    value: str
    persistent: bool = False

    def shell(self) -> str:
        persistent = "-P " if self.persistent else ""
        return f"setsebool {persistent}{shlex.quote(self.boolean)} {self.value}"


async def boolean(
    ctx: OpContext,
    bool_name: str,
    value: str,
    persistent: bool = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Set one SELinux boolean to on or off.

    Args:
        ctx (OpContext): Execution context for the operation.
        bool_name (str): Name of the SELinux boolean (e.g.
            ``httpd_can_network_connect``).
        value (str): Desired state of the boolean -- ``on`` or ``off``.
        persistent (bool, optional): Whether to write the updated policy
            (``setsebool -P``) so the value survives reboot. Defaults to
            False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated setsebool commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/selinux/selinux_boolean.py``.
    """

    # ``selinux.boolean``. Idempotent on ``SEBoolean``:
    # the requested value word is compared against the fact and a
    # ``setsebool`` is yielded only when they differ -- the "check before
    # executing" the boolean enhancement asked for falls out of the
    # fact-driven model, sparing the expensive ``-P`` policy rewrite on an
    # unchanged boolean. The no-op reason is the established wording.
    #
    # Deliberate design choice: a ``Boolean`` enum is dropped --
    # ``value`` is the plain ``on``/``off`` string already accepted
    # for compatibility, and anything else raises ``ValueError`` (the
    # established validation message). The command needs root:
    # flag the request ``sudo=True`` (or ``su=True``/``doas=True``).
    # ``**kwargs`` absorbs any further global arguments (``op()`` already
    # strips ``name`` and the escalation flags).
    if value not in ("on", "off"):
        raise ValueError(f"Invalid value '{value}' for boolean operation")
    current = await ctx.facts.get(SEBoolean, bool_name)
    if current != value:
        ctx.changed = True
        ctx.reason = f"boolean '{bool_name}' will be set to '{value}'"
        yield SetseboolCommand(boolean=bool_name, value=value, persistent=persistent)
    else:
        ctx.reason = f"boolean '{bool_name}' already had the value '{value}'"


# --- selinux.file_context --------------------------------------------------------


@dataclass(frozen=True)
class ChconCommand(Command):
    """``chcon [-u user] [-r role] [-t type] [-l level] <path>``: relabel one path.

    The descriptor ``selinux.file_context`` yields. Only the requested
    components render their flags, in chcon(1)'s ``-u -r -t -l`` order; a
    type-only request renders the exact ``chcon -t <type> <path>``.
    Every component and the path are ``shlex``-quoted, so a value
    carrying shell metacharacters reaches
    chcon literally.
    """

    path: str
    se_type: str | None = None
    se_user: str | None = None
    se_role: str | None = None
    se_level: str | None = None

    def shell(self) -> str:
        parts = ["chcon"]
        for flag, value in (
            ("-u", self.se_user),
            ("-r", self.se_role),
            ("-t", self.se_type),
            ("-l", self.se_level),
        ):
            if value is not None:
                parts.extend([flag, shlex.quote(value)])
        parts.append(shlex.quote(self.path))
        return " ".join(parts)


async def file_context(
    ctx: OpContext,
    path: str,
    se_type: str | None = None,
    se_user: str | None = None,
    se_role: str | None = None,
    se_level: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Set the SELinux context components of one existing path.

    Args:
        ctx (OpContext): Execution context for the operation.
        path (str): The existing file or directory to relabel.
        se_type (str | None, optional): The SELinux type component (e.g.
            ``httpd_sys_content_t``). Defaults to None.
        se_user (str | None, optional): The SELinux user component (e.g.
            ``system_u``). Defaults to None.
        se_role (str | None, optional): The SELinux role component (e.g.
            ``object_r``). Defaults to None.
        se_level (str | None, optional): The MLS/MCS level component (e.g.
            ``s0``). Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated chcon commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/selinux/selinux_op_file_context.py``.
    """

    # ``selinux.file_context`` (``chcon -t`` on a type
    # mismatch) and extends it to the full context: each of the four
    # components is optional, at least one must be given, and only the
    # components that differ from the ``FileContext`` fact are yielded --
    # an unreadable context (None, the ``or {}`` fallback) makes every
    # requested component differ. The type-only no-op reason is the
    # established wording; a multi-component request reports the requested
    # context as already present.
    #
    # ``chcon`` changes the *runtime* label of existing files only; the
    # persistent database rule that survives a relabel is
    # ``selinux.file_context_mapping``'s job, and ``selinux.restorecon``
    # reapplies the database to a tree. The command needs no root for files
    # the login user owns, but relabelling system paths does: flag the
    # request ``sudo=True`` when in doubt. ``**kwargs`` absorbs any
    # global arguments (``op()`` already strips ``name``/``sudo``/``su``).
    wanted = {
        field: value
        for field, value in (
            ("user", se_user),
            ("role", se_role),
            ("type", se_type),
            ("level", se_level),
        )
        if value is not None
    }
    if not wanted:
        raise ValueError(
            "file_context requires at least one of se_type, se_user, "
            "se_role or se_level"
        )
    current = await ctx.facts.get(FileContext, path)
    current_fields = dict(current) if current is not None else {}
    differing = {
        field: value
        for field, value in wanted.items()
        if value != current_fields.get(field, "")
    }
    if differing:
        ctx.changed = True
        ctx.reason = (
            f"file_context: '{path}' will get {', '.join(sorted(differing))}"
        )
        yield ChconCommand(
            path=path,
            se_type=differing.get("type"),
            se_user=differing.get("user"),
            se_role=differing.get("role"),
            se_level=differing.get("level"),
        )
    elif set(wanted) == {"type"}:
        ctx.reason = f"file_context: '{path}' already had type '{se_type}'"
    else:
        ctx.reason = f"file_context: '{path}' already had the requested context"


# --- selinux.file_context_mapping --------------------------------------------------


@dataclass(frozen=True)
class SemanageFcontextCommand(Command):
    """``semanage fcontext -a|-m|-d [-t type] <target>``: one mapping change.

    The single present-flagged descriptor ``selinux.file_context_mapping``
    yields, following the ``RcUpdateCommand`` precedent -- all three
    directions are the same tool. ``present=False`` renders ``-d`` (delete
    the rule, no type); ``present=True`` renders ``-a`` (add) or, with
    ``modify=True``, ``-m`` (change the type of an existing rule), both
    carrying ``-t <type>``. The type and target expression are
    ``shlex``-quoted.
    """

    target: str
    se_type: str = ""
    present: bool = True
    modify: bool = False

    def shell(self) -> str:
        if not self.present:
            return f"semanage fcontext -d {shlex.quote(self.target)}"
        verb = "-m" if self.modify else "-a"
        return (
            f"semanage fcontext {verb} -t {shlex.quote(self.se_type)} "
            f"{shlex.quote(self.target)}"
        )


async def file_context_mapping(
    ctx: OpContext,
    target: str,
    se_type: str | None = None,
    present: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Manage the persistent SELinux file context mapping of a target.

    Args:
        ctx (OpContext): Execution context for the operation.
        target (str): The target path expression the mapping matches (e.g.
            ``/web(/.*)?``).
        se_type (str | None, optional): The SELinux type for the target
            (e.g. ``httpd_sys_content_t``); required when ``present`` is
            True. Defaults to None.
        present (bool, optional): Whether the mapping should exist (added
            or modified) or be removed. Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated semanage fcontext commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/selinux/selinux_op_file_context_mapping.py``.
    """

    # ``selinux.file_context_mapping``: the
    # ``FileContextMapping`` fact decides between add (no mapping), modify
    # (a mapping with a different type) and no-op (the same type); removal
    # deletes an existing mapping and no-ops on an absent one. The no-op
    # reasons are the established wordings, and the ``ValueError`` guard
    # (se_type required when present) is kept. The fact's None-on-absent
    # (the house deviation from {}) replaces the ``len(current)``
    # gates.
    #
    # As documented: this writes the persistent database only --
    # existing files keep their labels until ``selinux.restorecon`` (or a
    # relabel at creation) applies the rule. The command needs root: flag
    # the request ``sudo=True``. ``**kwargs`` absorbs any further global
    # arguments (``op()`` already strips ``name``/``sudo``/``su``).
    if present and se_type is None:
        raise ValueError("se_type must have a valid value if present is set")
    current = await ctx.facts.get(FileContextMapping, target)
    if present:
        if current is None:
            ctx.changed = True
            ctx.reason = f"mapping for '{target}' absent, will add"
            yield SemanageFcontextCommand(target=target, se_type=se_type)
        elif current["type"] != se_type:
            ctx.changed = True
            ctx.reason = f"mapping for '{target}' will change type to '{se_type}'"
            yield SemanageFcontextCommand(
                target=target, se_type=se_type, modify=True
            )
        else:
            ctx.reason = f"mapping for '{target}' -> '{se_type}' already present"
    elif current is not None:
        ctx.changed = True
        ctx.reason = f"mapping for '{target}' present, will remove"
        yield SemanageFcontextCommand(target=target, present=False)
    else:
        ctx.reason = f"no existing mapping for '{target}'"


# --- selinux.port ------------------------------------------------------------------


@dataclass(frozen=True)
class SemanagePortCommand(Command):
    """``semanage port -a|-m|-d [-t type] -p <proto> <port>``: one port label.

    The single present-flagged descriptor ``selinux.port`` yields,
    following the ``RcUpdateCommand`` precedent. ``present=False`` renders
    ``-d`` (delete the label, no type); ``present=True`` renders ``-a``
    (add) or, with ``modify=True``, ``-m`` (retype an existing label --
    the update capability the port enhancement asked for, an
    established branch). The type, protocol and port are
    ``shlex``-quoted.
    """

    protocol: str
    port: int
    se_type: str = ""
    present: bool = True
    modify: bool = False

    def shell(self) -> str:
        proto = shlex.quote(self.protocol)
        number = shlex.quote(str(self.port))
        if not self.present:
            return f"semanage port -d -p {proto} {number}"
        verb = "-m" if self.modify else "-a"
        return (
            f"semanage port {verb} -t {shlex.quote(self.se_type)} "
            f"-p {proto} {number}"
        )


async def port(
    ctx: OpContext,
    protocol: str,
    port_num: int,
    se_type: str | None = None,
    present: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Manage the SELinux type label of one protocol/port pair.

    Args:
        ctx (OpContext): Execution context for the operation.
        protocol (str): The protocol -- ``tcp``, ``udp``, ``sctp`` or
            ``dccp``.
        port_num (int): The port number to label.
        se_type (str | None, optional): The SELinux port type (e.g.
            ``http_port_t``); required when ``present`` is True. Defaults
            to None.
        present (bool, optional): Whether the label should exist (added or
            modified) or be removed. Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated semanage port commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/selinux/selinux_port.py``.
    """

    # ``selinux.port`` including its fact dispatch: when
    # ``sepolicy`` is installed (the selinux-local ``_Which`` probe, since
    # the zero-cross-module-import invariant forbids ``server.Which``) the
    # ``SEPort`` fact answers the one port directly, otherwise the
    # ``SEPorts`` map is projected. Add when unlabelled, modify when the
    # label differs, delete when labelled and present=False; the no-op
    # reasons are the established wordings and the ``ValueError`` guard
    # is kept.
    #
    # Deliberate design choices: a ``Protocol`` enum is dropped
    # (a plain protocol string, which the enum's ``.value`` unwrapped to
    # anyway), and its trailing fact-cache mutation is dropped -- the
    # per-step FactCache recreation already makes the next read see the
    # applied state, so writing through the cache would only paper over a
    # failed command. The command needs root: flag the request
    # ``sudo=True``. ``**kwargs`` absorbs any further global arguments
    # (``op()`` already strips ``name``/``sudo``/``su``).
    if present and se_type is None:
        raise ValueError("se_type must have a valid value if present is set")
    direct_get = await ctx.facts.get(_Which, "sepolicy") is not None
    if direct_get:
        current = await ctx.facts.get(SEPort, protocol, port_num)
    else:
        port_info = await ctx.facts.get(SEPorts)
        current = port_info.get(protocol, {}).get(port_num, "")
    if present:
        if current == "":
            ctx.changed = True
            ctx.reason = f"setype for '{protocol}/{port_num}' unset, will add"
            yield SemanagePortCommand(
                protocol=protocol, port=port_num, se_type=se_type
            )
        elif current != se_type:
            ctx.changed = True
            ctx.reason = (
                f"setype for '{protocol}/{port_num}' will change to '{se_type}'"
            )
            yield SemanagePortCommand(
                protocol=protocol, port=port_num, se_type=se_type, modify=True
            )
        else:
            ctx.reason = f"setype for '{protocol}/{port_num}' is already '{se_type}'"
    elif current != "":
        ctx.changed = True
        ctx.reason = f"setype for '{protocol}/{port_num}' set, will remove"
        yield SemanagePortCommand(protocol=protocol, port=port_num, present=False)
    else:
        ctx.reason = f"setype for '{protocol}/{port_num}' is already unset"# --- selinux.mode ------------------------------------------------------------------


@dataclass(frozen=True)
class SetenforceCommand(Command):
    """``setenforce 1|0``: one runtime mode switch.

    The descriptor ``selinux.mode`` yields: ``enforcing`` renders ``1``,
    ``permissive`` renders ``0`` (setenforce(8)'s numeric arguments). The
    switch is runtime-only -- it never touches ``/etc/selinux/config``, so
    the boot mode is untouched and a disabled subsystem cannot be enabled
    this way.
    """

    mode: str

    def shell(self) -> str:
        return f"setenforce {1 if self.mode == 'enforcing' else 0}"


async def mode(
    ctx: OpContext,
    mode: str = "enforcing",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Set the SELinux runtime mode to enforcing or permissive.

    Args:
        ctx (OpContext): Execution context for the operation.
        mode (str, optional): The runtime mode -- ``enforcing`` or
            ``permissive``. Defaults to "enforcing".
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated setenforce commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/selinux/selinux_op_mode.py``.
    """

    # A reemote original: there is no other mode operation. Idempotent on
    # the ``Mode`` fact's runtime ``mode`` field -- the config-file mode is
    # deliberately not compared, because setenforce changes only the
    # running kernel state. A disabled subsystem raises ``ValueError``:
    # setenforce cannot enable SELinux (the kernel carries no policy), so
    # the switch needs the persistent config edit plus a relabel first --
    # ``selinux.config`` followed by ``selinux.autorelabel``. Making the
    # runtime mode survive reboot is that same operation's job, not this
    # operation's.
    #
    # The command needs root: flag the request ``sudo=True``. ``**kwargs``
    # absorbs any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    if mode not in ("enforcing", "permissive"):
        raise ValueError(
            f"Invalid mode '{mode}' -- the runtime mode must be "
            "'enforcing' or 'permissive'"
        )
    current = await ctx.facts.get(Mode)
    if current["mode"] == "disabled":
        raise ValueError(
            "SELinux is disabled; setenforce cannot enable it -- set "
            "SELINUX=enforcing (or permissive) with selinux.config, request "
            "selinux.autorelabel and reboot first"
        )
    if current["mode"] != mode:
        ctx.changed = True
        ctx.reason = f"SELinux is {current['mode']}, will switch to {mode}"
        yield SetenforceCommand(mode=mode)
    else:
        ctx.reason = f"SELinux already in {mode} mode"


# --- selinux.login -----------------------------------------------------------------


@dataclass(frozen=True)
class SemanageLoginCommand(Command):
    """``semanage login -a|-m|-d [-s seuser] [-r range] <login>``: one mapping.

    The single present-flagged descriptor ``selinux.login`` yields,
    following the ``RcUpdateCommand`` precedent. ``present=False`` renders
    ``-d`` (delete the mapping, no user or range); ``present=True`` renders
    ``-a`` (add) or, with ``modify=True``, ``-m`` (change an existing
    mapping), both carrying ``-s <seuser> -r <range>``. The login name,
    SELinux user and range are ``shlex``-quoted.
    """

    login: str
    seuser: str = ""
    mls_range: str = ""
    present: bool = True
    modify: bool = False

    def shell(self) -> str:
        if not self.present:
            return f"semanage login -d {shlex.quote(self.login)}"
        verb = "-m" if self.modify else "-a"
        return (
            f"semanage login {verb} -s {shlex.quote(self.seuser)} "
            f"-r {shlex.quote(self.mls_range)} {shlex.quote(self.login)}"
        )


async def login(
    ctx: OpContext,
    user: str,
    seuser: str | None = None,
    mls_range: str = "s0",
    present: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Map a Linux login to an SELinux user and MLS/MCS range.

    Args:
        ctx (OpContext): Execution context for the operation.
        user (str): The Linux login name to map (e.g. ``deploy``); the
            ``__default__`` fallback mapping is managed the same way.
        seuser (str | None, optional): The SELinux user to map to (e.g.
            ``staff_u``, ``sysadm_u``, ``unconfined_u``); required when
            ``present`` is True. Defaults to None.
        mls_range (str, optional): The MLS/MCS range of the mapping (e.g.
            ``s0`` or ``s0-s0:c0.c1023``). Defaults to "s0".
        present (bool, optional): Whether the mapping should exist (added
            or modified) or be removed. Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated semanage login commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/selinux/selinux_login.py``.
    """

    # A reemote original: there is no other login operation, so confined
    # user management (the least-privilege core of SELinux) has no control
    # surface. Idempotent on the ``SELogin`` fact: an absent mapping is
    # added, an existing one is modified only when its SELinux user or
    # range differs, and removal deletes an existing mapping and no-ops on
    # an absent one. The seuser/range comparison ignores the fact's
    # ``service`` column -- semanage login's Service selector is a newer
    # release's refinement this operation does not manage, so a
    # service-scoped mapping reads as its universal row.
    #
    # The command needs root: flag the request ``sudo=True``. ``**kwargs``
    # absorbs any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    if present and seuser is None:
        raise ValueError("seuser must have a valid value if present is set")
    logins = await ctx.facts.get(SELogin)
    current = logins.get(user)
    if present:
        if current is None:
            ctx.changed = True
            ctx.reason = f"login '{user}' unmapped, will map to '{seuser}'"
            yield SemanageLoginCommand(
                login=user, seuser=seuser, mls_range=mls_range
            )
        elif current["seuser"] != seuser or current["mls_range"] != mls_range:
            ctx.changed = True
            ctx.reason = (
                f"login '{user}' will map to '{seuser}' with range '{mls_range}'"
            )
            yield SemanageLoginCommand(
                login=user, seuser=seuser, mls_range=mls_range, modify=True
            )
        else:
            ctx.reason = (
                f"login '{user}' already maps to '{seuser}' "
                f"with range '{mls_range}'"
            )
    elif current is not None:
        ctx.changed = True
        ctx.reason = f"login '{user}' mapped, will remove"
        yield SemanageLoginCommand(login=user, present=False)
    else:
        ctx.reason = f"no existing login mapping for '{user}'"


# --- selinux.module ------------------------------------------------------------------


@dataclass(frozen=True)
class SemoduleInstallCommand(Command):
    """``semodule -X <priority> -i <source>``: install one policy module.

    The install direction of ``selinux.module``. ``source`` is the path of
    the compiled policy package on the target host (a ``.pp`` or ``.cil``
    file already delivered there, e.g. by ``files.put``); ``priority``
    renders ``-X`` (semodule(8)'s module priority, default 400). The source
    path is ``shlex``-quoted.
    """

    source: str
    priority: int = 400

    def shell(self) -> str:
        return f"semodule -X {self.priority} -i {shlex.quote(self.source)}"


@dataclass(frozen=True)
class SemoduleRemoveCommand(Command):
    """``semodule -X <priority> -r <name>``: remove one module instance.

    The removal direction of ``selinux.module``. ``priority`` selects which
    instance of the name to remove (semodule keeps one module per
    priority), so removing every instance yields one command per priority.
    The module name is ``shlex``-quoted.
    """

    name: str
    priority: int = 400

    def shell(self) -> str:
        return f"semodule -X {self.priority} -r {shlex.quote(self.name)}"


@dataclass(frozen=True)
class SemoduleEnableCommand(Command):
    """``semodule -e|-d <name>``: enable or disable a policy module.

    The single present-flagged descriptor the enable/disable directions of
    ``selinux.module`` yield, following the ``RcUpdateCommand`` precedent --
    both directions are the same tool. ``present=True`` renders ``-e``,
    ``present=False`` renders ``-d``; the name is ``shlex``-quoted. A
    disabled module stays installed but its rules stop applying.
    """

    name: str
    present: bool = True

    def shell(self) -> str:
        verb = "-e" if self.present else "-d"
        return f"semodule {verb} {shlex.quote(self.name)}"


async def module(
    ctx: OpContext,
    module_name: str,
    state: str = "present",
    source: str | None = None,
    priority: int = 400,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Install, remove, enable or disable an SELinux policy module.

    Args:
        ctx (OpContext): Execution context for the operation.
        module_name (str): The policy module name (e.g.
            ``my_custom_policy``). Named ``module_name`` rather than
            ``name`` because ``op()`` reserves ``name`` for the request
            label (the ``bool_name`` precedent).
        state (str, optional): The wanted state -- ``present`` (installed
            and enabled), ``absent`` (every instance removed), ``enabled``
            or ``disabled`` (installed, rules applying or not). Defaults
            to "present".
        source (str | None, optional): Path of the compiled policy package
            on the target host (``.pp``/``.cil``, delivered e.g. by
            ``files.put``); required to install an absent module. Defaults
            to None.
        priority (int, optional): The module priority for installation
            (``semodule -X``). Defaults to 400.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated semodule commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/selinux/selinux_module.py``.
    """

    # A reemote original: there is no other module operation, so custom
    # policies and third-party modules have no lifecycle management.
    # Idempotent on the ``Modules`` fact (``semodule -lfull`` lists the
    # disabled instances too, which plain ``-l`` hides): the name's
    # instances are matched in ascending priority order, so a multi-yield
    # removal plans the same command sequence in both phases.
    #
    # ``present`` installs an absent module (``source`` required -- the
    # package must already be on the host), enables an installed-but-
    # disabled one, and no-ops otherwise; it never reinstalls or upgrades
    # an enabled module, because the fact carries no package version to
    # compare (modern semodule dropped version reporting). ``absent``
    # removes every instance, one ``-X <priority> -r`` per stored priority.
    # ``enabled``/``disabled`` flip the enabled state of an installed
    # module with one name-keyed ``-e``/``-d`` and raise ``ValueError`` on
    # an absent one -- enabling nothing is a request error, not a silent
    # install.
    #
    # The commands need root: flag the request ``sudo=True``. ``**kwargs``
    # absorbs any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    if state not in ("present", "absent", "enabled", "disabled"):
        raise ValueError(
            f"Invalid state '{state}' -- must be present, absent, "
            "enabled or disabled"
        )
    modules = await ctx.facts.get(Modules)
    instances = sorted(
        (entry for entry in modules if entry["name"] == module_name),
        key=lambda entry: entry["priority"],
    )
    if state == "present":
        if not instances:
            if source is None:
                raise ValueError(
                    f"source must be given to install module '{module_name}'"
                )
            ctx.changed = True
            ctx.reason = f"module '{module_name}' absent, will install"
            yield SemoduleInstallCommand(source=source, priority=priority)
        elif all(not entry["enabled"] for entry in instances):
            ctx.changed = True
            ctx.reason = f"module '{module_name}' disabled, will enable"
            yield SemoduleEnableCommand(name=module_name, present=True)
        else:
            ctx.reason = f"module '{module_name}' already present and enabled"
    elif state == "absent":
        if instances:
            ctx.changed = True
            ctx.reason = f"module '{module_name}' present, will remove"
            for entry in instances:
                yield SemoduleRemoveCommand(
                    name=module_name, priority=entry["priority"]
                )
        else:
            ctx.reason = f"module '{module_name}' already absent"
    else:
        if not instances:
            raise ValueError(
                f"module '{module_name}' is not installed; cannot set it {state}"
            )
        wanted_enabled = state == "enabled"
        if any(entry["enabled"] != wanted_enabled for entry in instances):
            ctx.changed = True
            ctx.reason = f"module '{module_name}' will be {state}"
            yield SemoduleEnableCommand(name=module_name, present=wanted_enabled)
        else:
            ctx.reason = f"module '{module_name}' already {state}"


# --- selinux.permissive_domain ---------------------------------------------------------


@dataclass(frozen=True)
class SemanagePermissiveCommand(Command):
    """``semanage permissive -a|-d <domain>``: one permissive-domain change.

    The single present-flagged descriptor ``selinux.permissive_domain``
    yields, following the ``RcUpdateCommand`` precedent. ``present=True``
    renders ``-a`` (exempt the domain from enforcement), ``present=False``
    renders ``-d`` (enforce it again). The domain name is ``shlex``-quoted.
    """

    domain: str
    present: bool = True

    def shell(self) -> str:
        verb = "-a" if self.present else "-d"
        return f"semanage permissive {verb} {shlex.quote(self.domain)}"


async def permissive_domain(
    ctx: OpContext,
    domain: str,
    present: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add or remove a permissive domain exemption.

    Args:
        ctx (OpContext): Execution context for the operation.
        domain (str): The SELinux domain type to exempt (e.g.
            ``cupsd_t`` or ``my_custom_daemon_t``).
        present (bool, optional): Whether the domain should be permissive
            (added) or enforcing again (removed). Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated semanage permissive commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/selinux/selinux_permissive_domain.py``.
    """

    # A reemote original: there is no other permissive-domain operation.
    # Exempting one denied domain is the surgical alternative to switching
    # the whole host permissive (``selinux.mode``) while troubleshooting.
    # Idempotent on the ``PermissiveDomains`` fact, which lists built-in
    # and customized permissive types alike -- adding a built-in permissive
    # domain is a no-op, removing one yields the ``-d`` that semanage
    # records as a customization.
    #
    # The command needs root: flag the request ``sudo=True``. ``**kwargs``
    # absorbs any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    domains = await ctx.facts.get(PermissiveDomains)
    is_permissive = domain in domains
    if present and not is_permissive:
        ctx.changed = True
        ctx.reason = f"domain '{domain}' enforcing, will make permissive"
        yield SemanagePermissiveCommand(domain=domain, present=True)
    elif not present and is_permissive:
        ctx.changed = True
        ctx.reason = f"domain '{domain}' permissive, will enforce"
        yield SemanagePermissiveCommand(domain=domain, present=False)
    elif present:
        ctx.reason = f"domain '{domain}' is already permissive"
    else:
        ctx.reason = f"domain '{domain}' is already enforcing"


# --- selinux.restorecon ----------------------------------------------------------------


@dataclass(frozen=True)
class RestoreconCommand(Command):
    """``restorecon [-R] [-F] <path>``: relabel a path from the database.

    The descriptor ``selinux.restorecon`` yields. ``recurse`` renders
    ``-R`` (relabel the directory tree, the default), ``force`` renders
    ``-F`` (reset even the user-customized parts of a context, not just
    the type). The path is ``shlex``-quoted.
    """

    path: str
    recurse: bool = True
    force: bool = False

    def shell(self) -> str:
        flags = ""
        if self.recurse:
            flags += "-R "
        if self.force:
            flags += "-F "
        return f"restorecon {flags}{shlex.quote(self.path)}"


async def restorecon(
    ctx: OpContext,
    path: str,
    recurse: bool = True,
    force: bool = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Relabel a path tree from the SELinux file context database.

    Args:
        ctx (OpContext): Execution context for the operation.
        path (str): The file or directory to relabel.
        recurse (bool, optional): Relabel the whole directory tree
            (``restorecon -R``). Defaults to True.
        force (bool, optional): Reset even user-customized context parts
            (``restorecon -F``), not just the type. Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated restorecon commands.

    **Raises:**

    ```python
    None
    ```

    Examples:
        Examples can be found in
        ``examples/modules/selinux/selinux_restorecon.py``.
    """

    # A reemote original: there is no other restorecon operation, though
    # the ``file_context_mapping`` docs note existing files keep their
    # labels -- this is the remedy. Deliberately **stateless** (the chosen
    # design): restorecon is cheap, silent on an already-correct tree and
    # the natural follow-up to a mapping change, so it always reports a
    # change and always yields, like ``apt.key``'s non-idempotence. A
    # fact-driven "would anything change?" preflight (matchpathcon over
    # the tree) costs more than the relabel itself.
    #
    # Nothing raises during materialization -- no fact is read and no
    # argument is validated; a missing restorecon binary surfaces as a
    # command failure in the apply phase. The command needs root for
    # system paths: flag the request ``sudo=True``. ``**kwargs`` absorbs
    # any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    ctx.changed = True
    ctx.reason = f"restorecon will relabel '{path}'"
    yield RestoreconCommand(path=path, recurse=recurse, force=force)


# --- selinux.autorelabel -----------------------------------------------------------------


@dataclass(frozen=True)
class AutorelabelCommand(Command):
    """``touch /.autorelabel``: request a full relabel at the next boot.

    The descriptor ``selinux.autorelabel`` yields -- the selinux-local
    counterpart of ``files.file``'s touch, since the zero-cross-module-
    import invariant forbids reusing ``files.FileCommand``. The path is
    the fixed ``AUTORELABEL_PATH`` constant, never user text.
    """

    def shell(self) -> str:
        return f"touch {AUTORELABEL_PATH}"


async def autorelabel(ctx: OpContext, **kwargs: Any) -> AsyncGenerator[Command]:
    """Request a full filesystem relabel at the next boot.

    Args:
        ctx (OpContext): Execution context for the operation.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated autorelabel flag commands.

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/selinux/selinux_autorelabel.py``.
    """

    # A reemote original: there is no other autorelabel operation.
    # **Warning**: the flag makes the *next boot* relabel every mounted
    # filesystem before enforcing begins -- on a large disk this can take
    # many minutes and delays the boot. It is the required step when
    # enabling SELinux after it was disabled (the filesystems carry no
    # labels), or the remedy for severely corrupted contexts; prefer
    # ``selinux.restorecon`` for a targeted tree.
    #
    # Idempotent on the selinux-local ``_AutorelabelPending`` probe (the
    # ``git._DirectoryExists`` stand-in pattern, since ``files.FileExists``
    # cannot be imported across namespaces): an existing ``/.autorelabel``
    # means a relabel is already pending and nothing is yielded. The flag
    # is consumed by the boot itself, so a later run after the relabel
    # legitimately requests a new one.
    #
    # The command needs root (``/`` is root-owned): flag the request
    # ``sudo=True``. ``**kwargs`` absorbs any further global arguments
    # (``op()`` already strips ``name``/``sudo``/``su``).
    pending = await ctx.facts.get(_AutorelabelPending)
    if pending:
        ctx.reason = f"a full relabel is already pending ({AUTORELABEL_PATH} exists)"
    else:
        ctx.changed = True
        ctx.reason = f"will create {AUTORELABEL_PATH} to relabel at next boot"
        yield AutorelabelCommand()


# --- selinux.config ------------------------------------------------------------------


def _config_key_pattern(key: str) -> str:
    """The anchored regex matching every assignment line of ``key``.

    Matches an active assignment (``SELINUX=enforcing``) and a
    commented-out one (``# SELINUX=permissive``), with optional surrounding
    whitespace and spaces around the ``=`` -- the pattern the former
    ``selinux_config.py`` recipe's ``config_edit`` used. ``key`` is only
    ever one of the two fixed setting names, so ``re.escape`` is
    belt-and-braces for the regex, not a security boundary.
    """
    return rf"^[ \t]*#?[ \t]*{re.escape(key)}[ \t]*="


def _config_spool_path(dest: str) -> str:
    """The deterministic ``/tmp`` spool *file* path ``selinux.config`` uploads to.

    Derived from the managed file's sha1 (``openrc.config``'s
    ``_config_spool_path``, itself ``files.install``'s ``_install_spool_path``
    pattern): the derived name is exactly as deterministic across the two
    deploy phases as a fixed constant, and it keeps the house rule that
    every spool-and-install operation names its spool after the managed
    path even when it manages exactly one file (the ``apk.repository``
    precedent). The content is spooled to a fixed ``config`` file *inside*
    that sha1-derived directory rather than at the top level of ``/tmp``, and
    the elevated install ``rm -rf``s the whole directory afterwards, so no
    leftover lands in ``/tmp``. The name avoids *collisions*, not a hostile
    local user: it is predictable, so on a multi-tenant host the ``/tmp`` spool
    is still a symlink-race exposure (the trade-off every spool-and-install
    operation here makes).
    """
    digest = hashlib.sha1(dest.encode()).hexdigest()[:16]
    return f"/tmp/reemote-selinux-config-{digest}/config"


def _set_config_value(content: str | None, key: str, value: str) -> str:
    """The pure set transform: ``content`` carrying exactly one ``key=value``.

    ``selinux.config``'s edit of the file's current text, one setting at a
    time: every line assigning ``key`` is dropped -- commented-out ones
    included, the recipe's replacement semantics, because leaving
    contradictory commented assignments around invites confusion -- then
    ``key=value`` is appended (unquoted, the stock /etc/selinux/config
    convention: libselinux parses the file, no shell sources it). An
    absent file (``None``) becomes the single line. When the wanted line
    is already the file's *only* assignment of the key the content comes
    back byte-for-byte unchanged -- dropping and re-appending it would
    move the line to the end of the file and report a spurious change on
    a stock config whose values already match. A rewritten result always
    ends in one trailing newline, and the edit is a fixpoint: the appended
    line matches the drop pattern, so a second pass reproduces the same
    content. Idempotent (``f(f(x)) == f(x)``) and side-effect free, so both
    deploy phases compute the same content from the same fact value.
    """
    pattern = re.compile(_config_key_pattern(key))
    matched = [line for line in (content or "").splitlines() if pattern.match(line)]
    wanted = f"{key}={value}"
    if matched == [wanted]:
        return content or ""
    lines = [line for line in (content or "").splitlines() if not pattern.match(line)]
    lines.append(wanted)
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class SelinuxConfigUploadCommand(TransferCommand):
    """Spool the transformed SELinux config content to a ``/tmp`` path on the target.

    The first half of ``selinux.config``'s write and a *non-shell*
    descriptor: it copies the content over the connection's scp channel
    via ``Host.upload_file`` rather than running shell text, so it has no
    ``shell()`` and its loggable form is the ``__str__`` rendering
    ``command_shell`` falls back to. Like ``OpenrcConfigUploadCommand`` the
    content is not a caller-supplied stream but the whole configuration
    text the operation just derived from the file's current content, so
    the field is a plain ``str`` -- hashable and identical across phases --
    wrapped in a fresh ``io.BytesIO`` only at transfer time. The copy rides
    as the login user (transfers are unprivileged, so the request's
    ``sudo`` does not apply to it), which is why the spool path is
    world-writable ``/tmp`` rather than the root-owned ``/etc/selinux``
    directory.
    """

    content: str  # the whole transformed /etc/selinux/config text
    dest: str  # spool path on the target host

    def __str__(self) -> str:
        return f"scp <selinux config> -> {self.dest}"

    async def transfer(self, host: Host) -> None:
        # scp does not create parent directories, and the spool now lives
        # inside a sha1-derived /tmp directory rather than at the top level,
        # so mkdir -p it first. Unprivileged: /tmp is world-writable, so the
        # login user can create the directory, and a transfer never elevates.
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        await host.run_shell(f"mkdir -p {parent}")
        await host.upload_file(io.BytesIO(self.content.encode()), self.dest)


@dataclass(frozen=True)
class SelinuxConfigInstallCommand(Command):
    """Install the spooled SELinux config file ``0644``, then remove the spool file.

    The second half of ``selinux.config``'s write and the elevated one:
    writing ``/etc/selinux/config`` needs root, so the request is flagged
    ``sudo=True`` and the whole sequence is wrapped in ``sh -c`` -- a bare
    ``sudo`` prefix would elevate only the first command of the ``&&``
    chain and leave the cleanup running as the login user. The sequence
    creates the destination directory, copies the spooled content over
    the target file, chmods it ``0644`` and removes the spool -- the
    ``OpenrcConfigInstallCommand`` pattern. ``cp`` (never ``mv``) is the
    overlay verb deliberately: it preserves an existing file's root
    ownership, and a file it creates belongs to the elevated step's root
    -- a bare ``mv`` would carry the login-user spool's owner and mode
    onto the config, which breaks sestatus/semanage host-wide. All paths
    are shell-quoted.
    """

    spool: str  # where SelinuxConfigUploadCommand wrote the content
    dest: str  # the /etc/selinux/config path

    def shell(self) -> str:
        spool = shlex.quote(self.spool)
        # The spool file lives inside a sha1-derived /tmp directory; remove
        # the whole directory so no leftover lands in /tmp.
        spool_dir = shlex.quote(str(PurePosixPath(self.spool).parent))
        dest = shlex.quote(self.dest)
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        inner = (
            f"mkdir -p {parent} && "
            f"cp {spool} {dest} && "
            f"chmod 0644 {dest} && rm -rf {spool_dir}"
        )
        return f"sh -c {shlex.quote(inner)}"


async def config(
    ctx: OpContext,
    SELINUX: str | None = None,
    SELINUXTYPE: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Set the boot-time mode and policy type in /etc/selinux/config.

    Args:
        ctx (OpContext): Execution context for the operation.
        SELINUX (str | None, optional): The boot-time mode to ensure --
            ``enforcing``, ``permissive`` or ``disabled``; ``None`` leaves
            the file's ``SELINUX`` setting untouched. Defaults to None.
        SELINUXTYPE (str | None, optional): The policy type to ensure
            (e.g. ``targeted``, ``minimum``, ``mls`` -- any installed
            policy's name); ``None`` leaves the file's ``SELINUXTYPE``
            setting untouched. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated selinux config spool-and-install commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/selinux/selinux_config.py``.
    """

    # A reemote original: the ``selinux`` namespace ships boolean/
    # file_context/port/mode/login/module/
    # restorecon/autorelabel but no config operation. This operation
    # supersedes the ``files.edit`` + ``files.move`` spool-and-install
    # recipe the ``selinux_config.py`` example used to demonstrate, and is
    # a member of the spool-and-install write family: the file's current
    # text is read with the in-namespace ``SelinuxConfigFile`` content fact
    # (never rebuilt from ``Mode``'s parsed config_mode, which would
    # destroy the comments and ordering the stock file ships with), one
    # pure transform per requested setting computes the desired content,
    # and when the result differs from what is there the request yields
    # *two* descriptors: a ``SelinuxConfigUploadCommand`` transfer spooling
    # the new text to a ``/tmp`` path over scp, then a
    # ``SelinuxConfigInstallCommand`` running the elevated install that
    # overlays the protected file and removes the spool. Writing the file
    # needs root, so flag the request ``sudo=True`` -- but the escalation
    # applies to the install only: the transfer always rides the connection
    # as the login user, which is exactly why the content detours through
    # ``/tmp``. The spool name is derived from the managed path
    # (``_config_spool_path``), so it stays identical across the two deploy
    # phases.
    #
    # Idempotence is the transform's fixpoint: ``_set_config_value`` drops
    # every assignment of the key (commented-out ones included -- the
    # recipe's replacement semantics, so no contradictory commented line
    # survives) and appends ``KEY=value`` unquoted, the stock convention;
    # a key whose only assignment already is the wanted line keeps the
    # file byte-for-byte (no reorder, no spurious change on a stock
    # config). The settings apply in fixed order (SELINUX then
    # SELINUXTYPE), so both phases derive the same bytes. When the result
    # equals the file's current text the operation is a no-op; an absent
    # file reads ``None`` and gains just the requested lines (the
    # install's ``mkdir -p`` creates /etc/selinux when needed).
    #
    # Validation raises ``ValueError`` while materializing (in a deploy run
    # that is the prepare phase, before anything is sent): neither setting
    # given, a ``SELINUX`` outside enforcing/permissive/disabled (a bogus
    # mode would silently disable SELinux at boot), and a ``SELINUXTYPE``
    # that is empty or carries whitespace, ``#``, quotes or a backslash,
    # which would corrupt the line libselinux parses. The policy name is
    # otherwise free-form: custom policy packages exist, so no whitelist.
    #
    # There is no runtime half: the file is read when the system boots, so
    # the edit takes effect at the *next reboot* -- ``selinux.mode`` owns
    # the runtime ``setenforce`` switch, and ``Mode``'s ``config_mode``
    # field is how a deploy reads the persistent setting back.
    # **Warning**: switching a *disabled* subsystem to enforcing
    # additionally requires a full relabel (``selinux.autorelabel`` before
    # the reboot), and changing ``SELINUXTYPE`` requires the matching
    # policy package to be installed. ``**kwargs`` absorbs any
    # global arguments (``op()`` already strips ``name``/``sudo``/``su``).
    if SELINUX is None and SELINUXTYPE is None:
        raise ValueError("at least one of SELINUX and SELINUXTYPE must be given")
    if SELINUX is not None and SELINUX not in ("enforcing", "permissive", "disabled"):
        raise ValueError(f"not an SELinux boot mode: {SELINUX!r}")
    if SELINUXTYPE is not None and (
        not SELINUXTYPE or re.search(r"[\s#\"'\\]", SELINUXTYPE)
    ):
        raise ValueError(f"not a policy type name: {SELINUXTYPE!r}")

    path = "/etc/selinux/config"
    settings = [
        (key, value)
        for key, value in (("SELINUX", SELINUX), ("SELINUXTYPE", SELINUXTYPE))
        if value is not None
    ]
    current = await ctx.facts.get(SelinuxConfigFile, path)
    new = current or ""
    for key, value in settings:
        new = _set_config_value(new, key, value)
    wanted = ", ".join(f"{key}={value}" for key, value in settings)
    if new == (current or ""):
        # The transform is a fixpoint: the file already holds exactly the
        # desired settings, so there is nothing to spool.
        ctx.reason = f"{path} already sets {wanted}"
        return

    ctx.changed = True
    spool = _config_spool_path(path)
    ctx.reason = f"{wanted} will be set in {path}"
    yield SelinuxConfigUploadCommand(content=new, dest=spool)
    yield SelinuxConfigInstallCommand(spool=spool, dest=path)


__all__ = [
    "AutorelabelCommand",
    "Booleans",
    "ChconCommand",
    "FileContext",
    "FileContextMapping",
    "Mode",
    "ModeInfo",
    "Module",
    "Modules",
    "PermissiveDomains",
    "RestoreconCommand",
    "SEBoolean",
    "SELogin",
    "SEPort",
    "SEPorts",
    "SEUser",
    "SeContext",
    "SeLogin",
    "SeUser",
    "SelinuxConfigFile",
    "SelinuxConfigInstallCommand",
    "SelinuxConfigUploadCommand",
    "SemanageFcontextCommand",
    "SemanageLoginCommand",
    "SemanagePermissiveCommand",
    "SemanagePortCommand",
    "SemoduleEnableCommand",
    "SemoduleInstallCommand",
    "SemoduleRemoveCommand",
    "SetenforceCommand",
    "SetseboolCommand",
    "autorelabel",
    "boolean",
    "config",
    "file_context",
    "file_context_mapping",
    "login",
    "mode",
    "module",
    "permissive_domain",
    "port",
    "restorecon",
]