# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``firewalld`` module: facts and operations in one source.
#
# Every fact and operation here is a reemote5 original answering the
# comprehensive-firewalld proposal: full control over and visibility of
# the firewalld daemon
# through its ``firewall-cmd`` CLI (the shell-only fact pattern -- no
# D-Bus dependency).
#
# Fact side: ``FirewalldStatus`` (running flag and daemon version),
# ``FirewalldDefaultZone`` (the zone unbound connections land in),
# ``FirewalldZones`` (every zone's complete configuration -- target,
# interfaces, sources, services, ports, protocols, masquerade,
# forward-ports, source-ports, icmp-blocks, icmp-block-inversion and
# rich rules -- with a ``permanent`` argument selecting the runtime or
# the on-disk configuration), ``FirewalldServices`` (the permanent
# service definitions and their port/protocol bundles),
# ``FirewalldIpSets`` (the ipset containers, their options and entries,
# runtime or permanent) and ``FirewalldPolicies`` (the ingress/egress
# zone-pair policies newer daemons support; an older daemon reads {}).
# Two private probes back the runtime-only operations:
# ``_FirewalldLockdown`` and ``_FirewalldPanicMode``.
#
# Privilege note: ``firewall-cmd`` queries need root or a polkit
# authorization on most deployments. Following the iptables precedent
# (itself the hardware.SmbiosInfo precedent), every fact read retries
# its command through passwordless ``sudo -n`` at the shell level
# (never the elevation layer -- facts stay side-effect free and
# prompt-free): the contract is full data for a root login or a
# NOPASSWD sudoers rule, and the empty default otherwise,
# indistinguishable from genuinely-absent state. The operations below
# flag their commands ``sudo=True`` at the ``op()`` call site; their
# fact reads ride the same ``sudo -n`` retry, so idempotence reporting
# works on a sudoers fleet.
#
# Operation side: firewalld keeps TWO configurations -- runtime (live,
# lost on reload/reboot) and permanent (on disk, applied by reload).
# The developer chose the dual-apply model: every zone-membership
# operation defaults ``permanent=True`` and manages BOTH scopes, each
# idempotent through the ``FirewalldZones`` fact's ``permanent``
# argument; ``permanent=False`` opts into runtime-only (ephemeral)
# changes. The permanent scope is yielded first so a failed runtime
# command still leaves the change persisted. Three concepts are
# permanent-only by firewall-cmd's own design and say so in their
# docstrings: zone existence (``--new-zone``/``--delete-zone`` accept
# only ``--permanent``), service definitions (``--new-service``) and
# ipset existence (``--new-ipset``); their runtime copies appear after
# ``firewalld.reload``. ``default_zone`` is inherently both scopes
# (``--set-default-zone`` applies immediately and persists).
# ``lockdown`` and ``panic`` are runtime-only switches (the permanent
# lockdown setting lives in firewalld.conf's Lockdown= key, a
# ``files.edit`` recipe). ``reload`` is the stateless utility applying
# permanent to runtime (``--reload``), dropping state
# (``--complete-reload``) or saving runtime to permanent
# (``--runtime-to-permanent``).
#
# Zone-membership operations skip a scope whose fact shows no such
# zone, with an explicit reason, rather than yielding a command that
# would certainly fail: create the zone with ``firewalld.zone`` first
# (and ``firewalld.reload`` to materialize it at runtime). An
# unprivileged fact read ({} without root/NOPASSWD) therefore reports
# "zone not found" no-ops -- the same degradation class as the
# iptables facts' empty defaults.
#
# Deliberately absent (decided with the developer): a ``config``
# operation bundling default_zone/lockdown -- the standalone
# ``default_zone`` and ``lockdown`` operations cover it, and
# firewalld.conf edits are a ``files.edit`` recipe; direct-rule
# management (the deprecated ``--direct`` interface) -- rich rules
# cover the same ground without the iptables-syntax escape hatch;
# ipset *option* mutation on an existing set -- options apply at
# creation only, changing them needs a destroy/recreate that would
# drop members and break referencing rules; helper (custom ipset
# type) management -- stock kernels ship the hash:* types the ipset
# operation defaults to.
#
# Operations are async generators that receive an ``OpContext``, await
# facts, set ``ctx.changed``/``ctx.reason``, and yield command
# descriptors; they never touch the host directly.


import re
import shlex
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass
from typing import Any, TypedDict

from ...commands import Command
from ...core import OpContext
from ...hosts import Host

# --- Shared helpers ----------------------------------------------------------


def _privileged_read(command: str) -> str:
    """Wrap a root-read command with the passwordless ``sudo -n`` retry.

    The iptables/hardware.SmbiosInfo precedent for facts that need root
    or a polkit authorization: try unprivileged first, retry through
    ``sudo -n`` (never prompts, creates no askpass file, safe under the
    across-host fan-out), and mask every failure with ``|| true`` so a
    host that grants neither reads the fact's empty default instead of
    raising.
    """
    return f"{command} 2> /dev/null || sudo -n {command} 2> /dev/null || true"


def _permanent_flag(permanent: bool) -> str:
    """The ``--permanent `` infix (with trailing space) or the empty string."""
    return "--permanent " if permanent else ""


def _info_listing_command(
    list_option: str, info_option: str, marker: str, permanent: bool = False
) -> str:
    """The shell enumerating definitions and printing each one's info block.

    ``firewall-cmd`` has no whole-table detail verb for services, ipsets
    or policies, so one POSIX-sh loop lists the names (``--get-...``)
    and prints a ``<marker>: <name>`` separator plus the per-name
    ``--info-...`` block for each. Both firewall-cmd invocations ride
    the passwordless ``sudo -n`` retry; an older daemon that does not
    know the list option (policies before firewalld 0.9) prints nothing
    and the loop body never runs, reading as {}.
    """
    flag = _permanent_flag(permanent)
    names = _privileged_read(f"firewall-cmd {flag}{list_option}")
    info = _privileged_read(f'firewall-cmd {flag}{info_option} "$s"')
    inner = f'for s in $({names}); do echo "{marker}: $s"; {info}; done'
    return f"sh -c {shlex.quote(inner)}"


def _info_blocks(output: Iterable[str], marker: str) -> dict[str, list[str]]:
    """Split an info listing into name -> stripped detail lines. Pure.

    A ``<marker>: <name>`` line opens a block; the indented lines that
    follow belong to it. The unindented name echo ``--info-...`` prints
    as its first output line is skipped (the marker already carries the
    name).
    """
    prefix = f"{marker}: "
    blocks: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in output:
        if line.startswith(prefix):
            current = []
            blocks[line[len(prefix) :].strip()] = current
        elif current is not None and line.startswith((" ", "\t")):
            current.append(line.strip())
    return blocks


def _keyed_fields(lines: Iterable[str]) -> dict[str, list[str]]:
    """Parse ``key: value`` detail lines into key -> value tokens. Pure.

    Values split on whitespace (firewall-cmd prints lists space-
    separated); a line without a colon is ignored, except that ``rule``
    lines following the ``rich rules:`` key collect whole into that
    key's list -- rich rules are one indented line each and may carry
    colons of their own.
    """
    fields: dict[str, list[str]] = {}
    key: str | None = None
    for line in lines:
        if line.startswith("rule ") and key == "rich rules":
            fields.setdefault("rich rules", []).append(line)
            continue
        name, sep, value = line.partition(":")
        if not sep:
            continue
        key = name.strip()
        fields[key] = value.split()
    return fields


def _yes(tokens: list[str]) -> bool:
    """firewall-cmd prints booleans as ``yes``/``no``; absent reads False."""
    return tokens[:1] == ["yes"]


def _copy_record(record: dict) -> dict:
    """Copy one fact entry including its nested lists and dicts.

    The seam returns copies so callers never mutate simulated state;
    every entry field is a scalar, a list of strings or a flat dict of
    strings, one level deep.
    """
    return {
        key: list(value)
        if isinstance(value, list)
        else dict(value)
        if isinstance(value, dict)
        else value
        for key, value in record.items()
    }


# The zone-membership kinds the generic descriptor manages, mapped to
# the FirewalldZone field each compares against. ``interface`` is the
# one special case at render time: present uses --change-interface
# (rebinding an interface already bound elsewhere) instead of
# --add-interface (which errors on a bound interface).
_ZONE_MEMBER_FIELDS = {
    "service": "services",
    "port": "ports",
    "interface": "interfaces",
    "source": "sources",
    "protocol": "protocols",
    "icmp-block": "icmp_blocks",
    "rich-rule": "rich_rules",
    "forward-port": "forward_ports",
}

# A port opening: a single port or a range, over one of firewalld's
# four transport protocols. Static validation -- it never depends on
# fact state, so an unprivileged (empty) fact read still plans the
# change rather than raising.
_PORT_RE = re.compile(r"^\d+(-\d+)?/(tcp|udp|sctp|dccp)$")

# The zone targets firewall-cmd accepts for --set-target.
_ZONE_TARGETS = ("default", "ACCEPT", "DROP", "REJECT")


# --- Facts -------------------------------------------------------------------


class FirewalldStatusInfo(TypedDict):
    """
    Attributes:
        running: Whether the daemon answered ``running`` to
            ``firewall-cmd --state``; False for a stopped daemon, a
            missing binary or an unprivileged read.
        version: The daemon release string (e.g. ``2.1.1``), ``""``
            when ``firewall-cmd --version`` printed nothing.
    """

    # The single record ``FirewalldStatus`` returns. Both fields are
    # always present; the empty defaults make a stopped or unreadable
    # daemon indistinguishable from a missing one, the module header's
    # privilege contract.

    running: bool
    version: str


class FirewalldStatus:
    """Whether the firewalld daemon is running, and its version.

    **Commands:**

    ```bash
    sh -c 'echo "state: $(firewall-cmd --state 2> /dev/null || sudo -n firewall-cmd --state 2> /dev/null || true)"; echo "version: $(firewall-cmd --version 2> /dev/null || sudo -n firewall-cmd --version 2> /dev/null || true)"'
    ```

    **Returns:**

    ```python
    FirewalldStatusInfo
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/firewalld/firewalld_firewalld_status.py``.
    """

    # A reemote5 original (the DaemonStatus/status proposal): one shell
    # round-trip gathers both answers behind ``state:``/``version:``
    # markers so the parse never confuses the two payloads (the
    # one-stdout multi-command pitfall). The proposal's capability
    # fields (IPv4/IPv6/IPSet/bridge support) are D-Bus properties with
    # no firewall-cmd equivalent and are deliberately dropped -- the
    # version string is what capability gating needs. ``--state``
    # prints ``running`` or ``not running``; both probes ride the
    # passwordless sudo -n retry and the || true guard masks exit 127,
    # so only HostConnectionError can surface.
    #
    # SimulatedHost seam note: the ``firewalld_status`` field stores the
    # whole record; an unseeded host reads {"running": False,
    # "version": ""}.

    #: Shell gathering both answers over SSH, behind echo markers.
    COMMAND = (
        "sh -c 'echo \"state: $(firewall-cmd --state 2> /dev/null "
        "|| sudo -n firewall-cmd --state 2> /dev/null || true)\"; "
        'echo "version: $(firewall-cmd --version 2> /dev/null '
        "|| sudo -n firewall-cmd --version 2> /dev/null || true)\"'"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> FirewalldStatusInfo:
        """Parse the marked state/version lines into the record. Pure."""
        running = False
        version = ""
        for line in output:
            if line.startswith("state: "):
                running = line[len("state: ") :].strip() == "running"
            elif line.startswith("version: "):
                version = line[len("version: ") :].strip()
        return FirewalldStatusInfo(running=running, version=version)

    @classmethod
    async def process(cls, host: Host) -> FirewalldStatusInfo:
        state = getattr(host, "firewalld_status", None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return dict(state)  # type: ignore[return-value]
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class FirewalldDefaultZone:
    """The zone connections and interfaces without an explicit binding land in.

    **Commands:**

    ```bash
    firewall-cmd --get-default-zone 2> /dev/null || sudo -n firewall-cmd --get-default-zone 2> /dev/null || true
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
        Examples can be found in
        ``examples/modules/firewalld/firewalld_firewalld_default_zone.py``.
    """

    # A reemote5 original (the DefaultZone proposal): the first
    # non-empty line of --get-default-zone, stripped; a stopped daemon
    # or an unprivileged read prints nothing and reads "" (the empty
    # default, not None -- the scalar-fact convention). The default
    # zone is a single global setting, identical at runtime and in the
    # permanent configuration, so the fact takes no permanent argument.
    #
    # SimulatedHost seam note: the ``firewalld_default_zone`` field
    # stores the zone name; an unseeded host reads "".

    #: Shell gathering the default zone over SSH, with the passwordless
    #: ``sudo -n`` retry for the root read.
    COMMAND = (
        "firewall-cmd --get-default-zone 2> /dev/null "
        "|| sudo -n firewall-cmd --get-default-zone 2> /dev/null || true"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> str:
        """Keep the first non-empty line, stripped. Pure and total."""
        for line in output:
            if line.strip():
                return line.strip()
        return ""

    @classmethod
    async def process(cls, host: Host) -> str:
        state = getattr(host, "firewalld_default_zone", None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return str(state)
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class FirewalldZone(TypedDict):
    """
    Attributes:
        active: Whether the runtime listing marked the zone ``(active)``
            (it has interfaces or sources bound); always False in a
            permanent listing, which carries no activity marker.
        target: The zone's default verdict (``default``, ``ACCEPT``,
            ``DROP`` or ``REJECT``); ``default`` when unset.
        short: The zone's short display name, ``""`` when unset.
        description: The zone's description text, ``""`` when unset.
        icmp_block_inversion: Whether the icmp-blocks list is inverted
            (block everything *except* the listed types).
        interfaces: The interfaces bound to the zone.
        sources: The source addresses/CIDRs/MACs/ipset references bound
            to the zone.
        services: The service names enabled in the zone.
        ports: The port openings (``80/tcp``, ``5000-5100/udp``).
        protocols: The protocols allowed outright (``ah``, ``esp``, ...).
        forward: Whether intra-zone forwarding is enabled.
        masquerade: Whether source NAT (masquerading) is enabled.
        forward_ports: The port-forwarding rules
            (``port=80:proto=tcp:toport=8080:toaddr=10.0.0.5``).
        source_ports: The source-port openings.
        icmp_blocks: The blocked ICMP type names.
        rich_rules: The rich-language rule strings, one per entry,
            exactly as firewall-cmd prints them.
    """

    # One zone's complete configuration (``FirewalldZones``' entry).
    # Every field is always present with an empty default, so a bare
    # zone (a fresh --new-zone) reads as a fully-shaped record. The
    # ingress/egress priority keys newer daemons print are ignored --
    # they order policy evaluation, not zone membership, and no
    # operation here manages them.

    active: bool
    target: str
    short: str
    description: str
    icmp_block_inversion: bool
    interfaces: list[str]
    sources: list[str]
    services: list[str]
    ports: list[str]
    protocols: list[str]
    forward: bool
    masquerade: bool
    forward_ports: list[str]
    source_ports: list[str]
    icmp_blocks: list[str]
    rich_rules: list[str]


def _zone_record(active: bool, fields: dict[str, list[str]]) -> FirewalldZone:
    """Build one zone entry from parsed detail fields. Pure."""
    return FirewalldZone(
        active=active,
        target=" ".join(fields.get("target", [])) or "default",
        short=" ".join(fields.get("summary", [])),
        description=" ".join(fields.get("description", [])),
        icmp_block_inversion=_yes(fields.get("icmp-block-inversion", [])),
        interfaces=list(fields.get("interfaces", [])),
        sources=list(fields.get("sources", [])),
        services=list(fields.get("services", [])),
        ports=list(fields.get("ports", [])),
        protocols=list(fields.get("protocols", [])),
        forward=_yes(fields.get("forward", [])),
        masquerade=_yes(fields.get("masquerade", [])),
        forward_ports=list(fields.get("forward-ports", [])),
        source_ports=list(fields.get("source-ports", [])),
        icmp_blocks=list(fields.get("icmp-blocks", [])),
        rich_rules=list(fields.get("rich rules", [])),
    )


class FirewalldZones:
    """Every firewalld zone and its complete configuration.

    **Commands:**

    ```bash
    firewall-cmd <permanent>--list-all-zones 2> /dev/null || sudo -n firewall-cmd <permanent>--list-all-zones 2> /dev/null || true
    ```

    **Returns:**

    ```python
    dict[str, FirewalldZone]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/firewalld/firewalld_firewalld_zones.py``.
    """

    # A reemote5 original (the Zones proposal): --list-all-zones prints
    # one block per zone -- an unindented name line (the runtime
    # listing appends ``(active)`` to zones with bound interfaces or
    # sources) followed by indented ``key: value`` detail lines. The
    # ``permanent`` argument selects the configuration: False (the
    # default) reads the live runtime state, True reads the on-disk
    # permanent state a reload would apply -- the dual-configuration
    # diffing the proposal asks for. ``<permanent>`` in the command
    # template is ``--permanent `` or empty. Rich rules collect whole
    # (one indented ``rule ...`` line each) into the rich_rules list;
    # the separate RichRules fact proposal is this field.
    #
    # SimulatedHost seam note: the ``firewalld_zones`` field stores the
    # runtime name -> record map and ``firewalld_permanent_zones`` the
    # permanent one; an unseeded scope reads {}.

    #: Shell template gathering all zone blocks over SSH
    #: (``<permanent>`` is ``--permanent `` or empty), with the
    #: passwordless ``sudo -n`` retry for the root read.
    COMMAND = (
        "firewall-cmd <permanent>--list-all-zones 2> /dev/null "
        "|| sudo -n firewall-cmd <permanent>--list-all-zones 2> /dev/null || true"
    )

    @classmethod
    def command(cls, permanent: bool = False) -> str:
        """The shell for one configuration scope."""
        return _privileged_read(
            f"firewall-cmd {_permanent_flag(permanent)}--list-all-zones"
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, FirewalldZone]:
        """Parse ``--list-all-zones`` blocks into name -> record. Pure."""
        blocks: list[tuple[str, bool, list[str]]] = []
        for line in output:
            if not line.strip():
                continue
            if line.startswith((" ", "\t")):
                if blocks:
                    blocks[-1][2].append(line.strip())
            else:
                parts = line.split()
                active = len(parts) > 1 and parts[1] == "(active)"
                blocks.append((parts[0], active, []))
        return {
            name: _zone_record(active, _keyed_fields(lines))
            for name, active, lines in blocks
        }

    @classmethod
    async def process(cls, host: Host, permanent: bool = False) -> dict[str, FirewalldZone]:
        seam = "firewalld_permanent_zones" if permanent else "firewalld_zones"
        state = getattr(host, seam, None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly,
            # copying each record including its member lists.
            return {
                name: _copy_record(record)  # type: ignore[misc]
                for name, record in state.items()
            }
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.command(permanent))
        return cls.parse(stdout.splitlines())


class FirewalldService(TypedDict):
    """
    Attributes:
        short: The service's short display name (e.g. ``WWW (HTTP)``),
            ``""`` when unset.
        description: The service's description text, ``""`` when unset.
        ports: The destination port openings the service bundles
            (``80/tcp``).
        source_ports: The source port openings the service bundles.
        protocols: The protocols the service allows outright.
        modules: The netfilter connection-tracking helper modules the
            service loads (e.g. ``nf_conntrack_ftp``).
        destination: The destination address limits keyed by family
            (``{"ipv4": "192.168.1.0/24", "ipv6": "::1"}``), ``{}`` when
            the service is not destination-limited.
    """

    # One service definition (``FirewalldServices``' entry). Service
    # definitions exist only in the permanent configuration --
    # firewall-cmd loads them from /usr/lib/firewalld/services and
    # /etc/firewalld/services at startup -- so the record carries no
    # runtime/permanent distinction.

    short: str
    description: str
    ports: list[str]
    source_ports: list[str]
    protocols: list[str]
    modules: list[str]
    destination: dict[str, str]


class FirewalldServices:
    """Every firewalld service definition and the ports/protocols it bundles.

    **Commands:**

    ```bash
    sh -c 'for s in $(firewall-cmd --permanent --get-services 2> /dev/null || sudo -n firewall-cmd --permanent --get-services 2> /dev/null || true); do echo "service: $s"; firewall-cmd --permanent --info-service "$s" 2> /dev/null || sudo -n firewall-cmd --permanent --info-service "$s" 2> /dev/null || true; done'
    ```

    **Returns:**

    ```python
    dict[str, FirewalldService]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/firewalld/firewalld_firewalld_services.py``.
    """

    # A reemote5 original (the Services proposal): one shell loop lists
    # the permanent service names and prints each one's --info-service
    # block behind a ``service: <name>`` marker (firewall-cmd has no
    # whole-table detail verb). Built-in and custom definitions appear
    # alike; a custom definition in /etc shadows the built-in of the
    # same name, which is exactly what --info-service reports. No
    # permanent argument: definitions are permanent-only by design.
    #
    # SimulatedHost seam note: the ``firewalld_services`` field stores
    # name -> record; an unseeded host reads {}.

    #: Shell enumerating the service definitions over SSH (one loop,
    #: marker-separated info blocks), with the passwordless ``sudo -n``
    #: retry for the root read.
    COMMAND = (
        "sh -c 'for s in $(firewall-cmd --permanent --get-services 2> /dev/null "
        "|| sudo -n firewall-cmd --permanent --get-services 2> /dev/null || true); "
        'do echo "service: $s"; '
        'firewall-cmd --permanent --info-service "$s" 2> /dev/null '
        '|| sudo -n firewall-cmd --permanent --info-service "$s" 2> /dev/null || true; '
        "done'"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, FirewalldService]:
        """Parse the marker-separated info blocks into records. Pure."""
        services: dict[str, FirewalldService] = {}
        for name, lines in _info_blocks(output, "service").items():
            fields = _keyed_fields(lines)
            destination: dict[str, str] = {}
            for token in fields.get("destination", []):
                family, sep, address = token.partition(":")
                if sep:
                    destination[family] = address
            services[name] = FirewalldService(
                short=" ".join(fields.get("short", [])),
                description=" ".join(fields.get("description", [])),
                ports=list(fields.get("ports", [])),
                source_ports=list(fields.get("source-ports", [])),
                protocols=list(fields.get("protocols", [])),
                modules=list(fields.get("modules", [])),
                destination=destination,
            )
        return services

    @classmethod
    async def process(cls, host: Host) -> dict[str, FirewalldService]:
        state = getattr(host, "firewalld_services", None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly,
            # copying each record including its member lists.
            return {
                name: _copy_record(record)  # type: ignore[misc]
                for name, record in state.items()
            }
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class FirewalldIpSet(TypedDict):
    """
    Attributes:
        type: The ipset type (``hash:ip``, ``hash:net``,
            ``hash:ip,port``, ...), ``""`` when the info block carried
            none.
        options: The set options keyed by name (``{"maxelem": "65536",
            "family": "inet"}``); the family (``inet``/``inet6``) is an
            option on modern daemons, ``{}`` when none are set.
        entries: The member addresses/networks, exactly as firewall-cmd
            prints them.
    """

    # One ipset container (``FirewalldIpSets``' entry). The proposal's
    # separate ``family`` field lives in ``options`` on modern
    # firewalld (it prints ``family=inet`` there); keeping it in the
    # options map avoids a shape that differs across daemon versions.

    type: str
    options: dict[str, str]
    entries: list[str]


class FirewalldIpSets:
    """Every firewalld ipset container, its options and its member entries.

    **Commands:**

    ```bash
    sh -c 'for s in $(firewall-cmd <permanent>--get-ipsets 2> /dev/null || sudo -n firewall-cmd <permanent>--get-ipsets 2> /dev/null || true); do echo "ipset: $s"; firewall-cmd <permanent>--info-ipset "$s" 2> /dev/null || sudo -n firewall-cmd <permanent>--info-ipset "$s" 2> /dev/null || true; done'
    ```

    **Returns:**

    ```python
    dict[str, FirewalldIpSet]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/firewalld/firewalld_firewalld_ipsets.py``.
    """

    # A reemote5 original (the Ipsets proposal): the same marker-loop
    # shape as FirewalldServices over --get-ipsets/--info-ipset, with
    # the ``permanent`` argument selecting the configuration (runtime
    # entries can differ from permanent ones -- entries added without
    # --permanent vanish on reload). Critical for rules referencing
    # ``ipset:<name>`` sources: the zone fact shows the reference, this
    # fact shows what it resolves to. Distinct from iptables.IpSets,
    # which reads the kernel's ipset tables directly: firewalld
    # prefixes and manages its own containers.
    #
    # SimulatedHost seam note: the ``firewalld_ipsets`` field stores the
    # runtime name -> record map and ``firewalld_permanent_ipsets`` the
    # permanent one; an unseeded scope reads {}.

    #: Shell template enumerating the ipset containers over SSH
    #: (``<permanent>`` is ``--permanent `` or empty), with the
    #: passwordless ``sudo -n`` retry for the root read.
    COMMAND = (
        "sh -c 'for s in $(firewall-cmd <permanent>--get-ipsets 2> /dev/null "
        "|| sudo -n firewall-cmd <permanent>--get-ipsets 2> /dev/null || true); "
        'do echo "ipset: $s"; '
        'firewall-cmd <permanent>--info-ipset "$s" 2> /dev/null '
        '|| sudo -n firewall-cmd <permanent>--info-ipset "$s" 2> /dev/null || true; '
        "done'"
    )

    @classmethod
    def command(cls, permanent: bool = False) -> str:
        """The shell for one configuration scope."""
        return _info_listing_command("--get-ipsets", "--info-ipset", "ipset", permanent)

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, FirewalldIpSet]:
        """Parse the marker-separated info blocks into records. Pure."""
        ipsets: dict[str, FirewalldIpSet] = {}
        for name, lines in _info_blocks(output, "ipset").items():
            fields = _keyed_fields(lines)
            options: dict[str, str] = {}
            for token in fields.get("options", []):
                key, sep, value = token.partition("=")
                if sep:
                    options[key] = value
            ipsets[name] = FirewalldIpSet(
                type=" ".join(fields.get("type", [])),
                options=options,
                entries=list(fields.get("entries", [])),
            )
        return ipsets

    @classmethod
    async def process(cls, host: Host, permanent: bool = False) -> dict[str, FirewalldIpSet]:
        seam = "firewalld_permanent_ipsets" if permanent else "firewalld_ipsets"
        state = getattr(host, seam, None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly,
            # copying each record including its options and entries.
            return {
                name: _copy_record(record)  # type: ignore[misc]
                for name, record in state.items()
            }
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.command(permanent))
        return cls.parse(stdout.splitlines())


class FirewalldPolicy(TypedDict):
    """
    Attributes:
        target: The policy's default verdict (``CONTINUE``, ``ACCEPT``,
            ``DROP`` or ``REJECT``); ``CONTINUE`` when unset.
        ingress_zones: The zones traffic enters the policy from
            (``ANY``/``HOST`` are the special catch-alls).
        egress_zones: The zones traffic leaves the policy toward.
        services: The service names allowed by the policy.
        ports: The port openings the policy allows.
        protocols: The protocols the policy allows outright.
        forward_ports: The policy's port-forwarding rules.
        masquerade: Whether the policy masquerades matching traffic.
        rich_rules: The policy's rich-language rule strings.
    """

    # One policy (``FirewalldPolicies``' entry). Policies are the
    # modern firewalld primitive for filtering traffic *between* zones
    # (or from/to the host itself), increasingly replacing direct zone
    # rules; the object model mirrors a zone's membership fields plus
    # the ingress/egress zone pair that gives the policy its direction.

    target: str
    ingress_zones: list[str]
    egress_zones: list[str]
    services: list[str]
    ports: list[str]
    protocols: list[str]
    forward_ports: list[str]
    masquerade: bool
    rich_rules: list[str]


class FirewalldPolicies:
    """Every firewalld policy and its ingress/egress zone pairing.

    **Commands:**

    ```bash
    sh -c 'for s in $(firewall-cmd <permanent>--get-policies 2> /dev/null || sudo -n firewall-cmd <permanent>--get-policies 2> /dev/null || true); do echo "policy: $s"; firewall-cmd <permanent>--info-policy "$s" 2> /dev/null || sudo -n firewall-cmd <permanent>--info-policy "$s" 2> /dev/null || true; done'
    ```

    **Returns:**

    ```python
    dict[str, FirewalldPolicy]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/firewalld/firewalld_firewalld_policies.py``.
    """

    # A reemote5 original (the Policies proposal): the marker-loop shape
    # over --get-policies/--info-policy, with the ``permanent`` argument
    # selecting the configuration. Version gating falls out of the
    # guarded loop for free: a daemon older than 0.9 does not know
    # --get-policies, the || true guard masks its error, the name list
    # is empty and the fact reads {} -- no capability probe needed.
    # Policy *management* operations are deliberately absent (the
    # proposal lists visibility only); the rich-rule and zone
    # operations cover the common cases.
    #
    # SimulatedHost seam note: the ``firewalld_policies`` field stores
    # the runtime name -> record map and ``firewalld_permanent_policies``
    # the permanent one; an unseeded scope reads {}.

    #: Shell template enumerating the policies over SSH
    #: (``<permanent>`` is ``--permanent `` or empty), with the
    #: passwordless ``sudo -n`` retry for the root read.
    COMMAND = (
        "sh -c 'for s in $(firewall-cmd <permanent>--get-policies 2> /dev/null "
        "|| sudo -n firewall-cmd <permanent>--get-policies 2> /dev/null || true); "
        'do echo "policy: $s"; '
        'firewall-cmd <permanent>--info-policy "$s" 2> /dev/null '
        '|| sudo -n firewall-cmd <permanent>--info-policy "$s" 2> /dev/null || true; '
        "done'"
    )

    @classmethod
    def command(cls, permanent: bool = False) -> str:
        """The shell for one configuration scope."""
        return _info_listing_command(
            "--get-policies", "--info-policy", "policy", permanent
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, FirewalldPolicy]:
        """Parse the marker-separated info blocks into records. Pure."""
        policies: dict[str, FirewalldPolicy] = {}
        for name, lines in _info_blocks(output, "policy").items():
            fields = _keyed_fields(lines)
            policies[name] = FirewalldPolicy(
                target=" ".join(fields.get("target", [])) or "CONTINUE",
                ingress_zones=list(fields.get("ingress-zones", [])),
                egress_zones=list(fields.get("egress-zones", [])),
                services=list(fields.get("services", [])),
                ports=list(fields.get("ports", [])),
                protocols=list(fields.get("protocols", [])),
                forward_ports=list(fields.get("forward-ports", [])),
                masquerade=_yes(fields.get("masquerade", [])),
                rich_rules=list(fields.get("rich rules", [])),
            )
        return policies

    @classmethod
    async def process(cls, host: Host, permanent: bool = False) -> dict[str, FirewalldPolicy]:
        seam = "firewalld_permanent_policies" if permanent else "firewalld_policies"
        state = getattr(host, seam, None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly,
            # copying each record including its member lists.
            return {
                name: _copy_record(record)  # type: ignore[misc]
                for name, record in state.items()
            }
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.command(permanent))
        return cls.parse(stdout.splitlines())


class _FirewalldLockdown:
    """Whether firewalld lockdown mode is enabled (private probe).

    Backs the ``lockdown`` operation's idempotence; not part of the
    public fact surface (the proposal lists no lockdown visibility).
    """

    # --query-lockdown prints yes/no (and exits 1 for no, which the
    # || true guard masks). Lockdown is a runtime switch here; the
    # boot-time setting is firewalld.conf's Lockdown= key.

    #: Shell querying the lockdown state over SSH.
    COMMAND = (
        "firewall-cmd --query-lockdown 2> /dev/null "
        "|| sudo -n firewall-cmd --query-lockdown 2> /dev/null || true"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> bool:
        """True only for an explicit ``yes`` line. Pure and total."""
        return any(line.strip() == "yes" for line in output)

    @classmethod
    async def process(cls, host: Host) -> bool:
        state = getattr(host, "firewalld_lockdown", None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return bool(state)
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class _FirewalldPanicMode:
    """Whether firewalld panic mode is enabled (private probe).

    Backs the ``panic`` operation's idempotence; not part of the public
    fact surface.
    """

    # --query-panic prints yes/no (exit 1 for no, masked by || true).
    # Panic mode drops ALL network traffic, including the SSH session
    # a deploy rides -- the operation's docstring warns accordingly.

    #: Shell querying the panic state over SSH.
    COMMAND = (
        "firewall-cmd --query-panic 2> /dev/null "
        "|| sudo -n firewall-cmd --query-panic 2> /dev/null || true"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> bool:
        """True only for an explicit ``yes`` line. Pure and total."""
        return any(line.strip() == "yes" for line in output)

    @classmethod
    async def process(cls, host: Host) -> bool:
        state = getattr(host, "firewalld_panic_mode", None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return bool(state)
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


# --- Operations ----------------------------------------------------------------


# --- firewalld.zone --------------------------------------------------------------


@dataclass(frozen=True)
class FirewalldZoneCommand(Command):
    """``firewall-cmd --permanent --new-zone|--delete-zone``: zone existence.

    The descriptor ``firewalld.zone`` yields for existence changes.
    Zone creation and deletion are permanent-only by firewall-cmd's
    design (both verbs reject a runtime invocation); the runtime copy
    appears after ``--reload``. The zone name is ``shlex``-quoted.
    """

    zone: str
    present: bool = True

    def shell(self) -> str:
        action = "--new-zone" if self.present else "--delete-zone"
        return f"firewall-cmd --permanent {action}={shlex.quote(self.zone)}"


@dataclass(frozen=True)
class FirewalldZoneTargetCommand(Command):
    """``firewall-cmd [--permanent] --zone --set-target``: one zone's verdict.

    The descriptor ``firewalld.zone`` yields for target changes, once
    per configuration scope that differs. The zone name and the
    validated target word are ``shlex``-quoted.
    """

    zone: str
    target: str
    permanent: bool = True

    def shell(self) -> str:
        return (
            f"firewall-cmd {_permanent_flag(self.permanent)}"
            f"--zone={shlex.quote(self.zone)} --set-target={shlex.quote(self.target)}"
        )


@dataclass(frozen=True)
class FirewalldZoneTextCommand(Command):
    """``firewall-cmd [--permanent] --zone --set-short|--set-description``.

    The descriptor ``firewalld.zone`` yields for display-text changes;
    ``kind`` selects ``short`` or ``description``. The zone name and the
    text are ``shlex``-quoted.
    """

    zone: str
    kind: str
    text: str
    permanent: bool = True

    def shell(self) -> str:
        option = "--set-short" if self.kind == "short" else "--set-description"
        return (
            f"firewall-cmd {_permanent_flag(self.permanent)}"
            f"--zone={shlex.quote(self.zone)} {option}={shlex.quote(self.text)}"
        )


async def zone(
    ctx: OpContext,
    zone: str,
    present: bool = True,
    target: str | None = None,
    short: str | None = None,
    description: str | None = None,
    permanent: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Create, delete or update a firewalld zone.

    Args:
        ctx (OpContext): Execution context for the operation.
        zone (str): The name of the zone to manage.
        present (bool, optional): Whether the zone should exist.
            Defaults to True.
        target (str | None, optional): The zone's default verdict
            (``default``, ``ACCEPT``, ``DROP`` or ``REJECT``); None
            leaves the current target alone. Defaults to None.
        short (str | None, optional): The zone's short display name;
            None leaves it alone. Defaults to None.
        description (str | None, optional): The zone's description
            text; None leaves it alone. Defaults to None.
        permanent (bool, optional): Whether to manage the zone's
            properties in the permanent configuration. Existence and
            properties are both permanent-only -- firewall-cmd has no
            runtime zone create/delete verb and rejects a runtime
            ``--set-target``/``--set-short``/``--set-description`` -- so
            ``permanent=False`` leaves the properties unmanaged (the
            runtime copy inherits them on the next ``firewalld.reload``).
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated firewalld zone commands.

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/firewalld/firewalld_zone.py``.
    """

    # A reemote5 original (the zone proposal): idempotent on the
    # FirewalldZones fact in both scopes. Existence is permanent-only
    # (--new-zone/--delete-zone reject runtime invocations); a deleted
    # zone's runtime copy lingers until firewalld.reload, and a created
    # zone's runtime copy appears only after it -- the operation says
    # so in its reasons rather than yielding a runtime command that
    # would certainly fail. Properties (target/short/description) are
    # PERMANENT-ONLY too: --set-target/--set-short/--set-description all
    # reject a runtime invocation ("Option can be used only with
    # --permanent"), so they are managed in the permanent scope alone and
    # the runtime copy inherits them on the next firewalld.reload. A None
    # property is unmanaged, never reset. The target word is validated statically
    # (ValueError) so an unprivileged empty fact read still plans the
    # change. Rules *inside* the zone are the membership operations'
    # job (port, zone_service, ...), not this one's. The commands need
    # root: flag the request ``sudo=True``. ``**kwargs`` absorbs
    # any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    if target is not None and target not in _ZONE_TARGETS:
        raise ValueError(
            f"target must be one of {', '.join(_ZONE_TARGETS)} (got {target!r})"
        )
    perm_zones = await ctx.facts.get(FirewalldZones, permanent=True)
    reasons: list[str] = []

    if not present:
        if zone in perm_zones:
            ctx.changed = True
            reasons.append(f"zone {zone} will be deleted (permanent)")
            yield FirewalldZoneCommand(zone=zone, present=False)
        else:
            reasons.append(f"zone {zone} does not exist (permanent)")
        ctx.reason = "; ".join(reasons)
        return

    if zone not in perm_zones:
        ctx.changed = True
        reasons.append(f"zone {zone} will be created (permanent)")
        yield FirewalldZoneCommand(zone=zone, present=True)
    else:
        reasons.append(f"zone {zone} exists (permanent)")

    for kind, desired in (
        ("target", target),
        ("short", short),
        ("description", description),
    ):
        if desired is None:
            continue
        # target/short/description are permanent-only firewall-cmd options
        # (--set-target/--set-short/--set-description reject a runtime
        # invocation with "Option can be used only with --permanent"), so
        # like the existence verbs they are managed in the permanent scope
        # alone; the runtime copy inherits them on the next
        # firewalld.reload. permanent=False leaves them unmanaged rather
        # than yielding a runtime command that would certainly fail.
        if not permanent:
            reasons.append(f"zone {zone} {kind} is permanent-only (permanent=False skips it)")
            continue
        # A zone being created carries the defaults the create produces.
        perm_current = perm_zones.get(zone, {}).get(kind, "default" if kind == "target" else "")
        if perm_current != desired:
            ctx.changed = True
            reasons.append(f"zone {zone} {kind} will be set to {desired} (permanent)")
            if kind == "target":
                yield FirewalldZoneTargetCommand(zone=zone, target=desired, permanent=True)
            else:
                yield FirewalldZoneTextCommand(
                    zone=zone, kind=kind, text=desired, permanent=True
                )
        else:
            reasons.append(f"zone {zone} {kind} already {desired} (permanent)")

    ctx.reason = "; ".join(reasons)


# --- firewalld zone membership (zone_service/port/interface/source/...) -----------


@dataclass(frozen=True)
class FirewalldZoneEntryCommand(Command):
    """``firewall-cmd [--permanent] --zone --add-<kind>|--remove-<kind>``.

    The one descriptor the zone-membership operations share
    (``zone_service``, ``port``, ``interface``, ``source``,
    ``rich_rule``, ``protocol``, ``icmp_block``, ``forward_port``):
    ``kind`` is the firewall-cmd option noun (``service``, ``port``,
    ``interface``, ``source``, ``rich-rule``, ``protocol``,
    ``icmp-block``, ``forward-port``) and ``present`` toggles add vs
    remove. ``interface`` is the one render special case: present uses
    ``--change-interface`` (which rebinds an interface already bound to
    another zone) instead of ``--add-interface`` (which errors on a
    bound interface). Zone and value are ``shlex``-quoted -- rich rules
    carry spaces and quotes of their own and survive as one argument.
    """

    zone: str
    kind: str
    value: str
    present: bool = True
    permanent: bool = True

    def shell(self) -> str:
        if self.present and self.kind == "interface":
            option = f"--change-interface={shlex.quote(self.value)}"
        else:
            verb = "add" if self.present else "remove"
            option = f"--{verb}-{self.kind}={shlex.quote(self.value)}"
        return (
            f"firewall-cmd {_permanent_flag(self.permanent)}"
            f"--zone={shlex.quote(self.zone)} {option}"
        )


async def _zone_scopes(
    ctx: OpContext, permanent: bool
) -> list[tuple[bool, dict[str, FirewalldZone]]]:
    """The managed configuration scopes, permanent first.

    permanent=True manages both scopes (the permanent change is
    yielded first so a failed runtime command still leaves it
    persisted); permanent=False is runtime-only. The scope list is a
    pure function of the parameter, so both phases read the same facts
    in the same order.
    """
    scopes: list[tuple[bool, dict[str, FirewalldZone]]] = []
    if permanent:
        scopes.append((True, await ctx.facts.get(FirewalldZones, permanent=True)))
    scopes.append((False, await ctx.facts.get(FirewalldZones)))
    return scopes


async def _manage_zone_member(
    ctx: OpContext,
    zone: str,
    kind: str,
    value: str,
    present: bool,
    permanent: bool,
    label: str,
    reasons: list[str],
) -> AsyncGenerator[FirewalldZoneEntryCommand]:
    """Yield the membership changes one value needs, per scope.

    A scope whose fact shows no such zone is skipped with an explicit
    reason (the module header's contract): create the zone with
    ``firewalld.zone`` first, and reload to materialize it at runtime.
    The caller sets ``ctx.changed`` per yielded descriptor and joins
    the reasons.
    """
    field = _ZONE_MEMBER_FIELDS[kind]
    for perm, zones in await _zone_scopes(ctx, permanent):
        scope = "permanent" if perm else "runtime"
        record = zones.get(zone)
        if record is None:
            reasons.append(f"zone {zone} not found in {scope} configuration")
            continue
        members = record[field]  # type: ignore[literal-required]
        if present and value not in members:
            reasons.append(f"{label} {value} will be added to zone {zone} ({scope})")
            yield FirewalldZoneEntryCommand(
                zone=zone, kind=kind, value=value, present=True, permanent=perm
            )
        elif not present and value in members:
            reasons.append(f"{label} {value} will be removed from zone {zone} ({scope})")
            yield FirewalldZoneEntryCommand(
                zone=zone, kind=kind, value=value, present=False, permanent=perm
            )
        elif present:
            reasons.append(f"{label} {value} already in zone {zone} ({scope})")
        else:
            reasons.append(f"{label} {value} not in zone {zone} ({scope})")


async def zone_service(
    ctx: OpContext,
    zone: str,
    service: str,
    present: bool = True,
    permanent: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Enable or disable a service in a firewalld zone.

    Args:
        ctx (OpContext): Execution context for the operation.
        zone (str): The zone to manage the service in.
        service (str): The service name (``http``, ``ssh``, a custom
            definition from ``firewalld.service``, ...).
        present (bool, optional): Whether the service should be enabled
            in the zone. Defaults to True.
        permanent (bool, optional): Whether the change also applies to
            the permanent configuration (False is runtime-only).
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated firewalld zone service commands.

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/firewalld/firewalld_zone_service.py``.
    """

    # A reemote5 original (the Proposal's service-in-zone semantics;
    # the Discussion's definition management is the separate
    # ``service`` operation): idempotent on the FirewalldZones fact's
    # services list, dual-apply per scope. Attaching a service by name
    # instead of raw port rules is the firewalld idiom -- the ports it
    # opens travel with the definition. The commands need root: flag
    # the request ``sudo=True``.
    reasons: list[str] = []
    async for cmd in _manage_zone_member(
        ctx, zone, "service", service, present, permanent, "service", reasons
    ):
        ctx.changed = True
        yield cmd
    ctx.reason = "; ".join(reasons)


async def port(
    ctx: OpContext,
    zone: str,
    port: str,
    present: bool = True,
    permanent: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Open or close a port (or port range) in a firewalld zone.

    Args:
        ctx (OpContext): Execution context for the operation.
        zone (str): The zone to manage the port in.
        port (str): The port opening, ``<port>/<protocol>`` or
            ``<start>-<end>/<protocol>`` (``8080/tcp``,
            ``5000-5100/udp``); the protocol is tcp, udp, sctp or dccp.
        present (bool, optional): Whether the port should be open.
            Defaults to True.
        permanent (bool, optional): Whether the change also applies to
            the permanent configuration (False is runtime-only).
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated firewalld port commands.

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/firewalld/firewalld_port.py``.
    """

    # A reemote5 original (the port proposal): idempotent on the
    # FirewalldZones fact's ports list, dual-apply per scope. The
    # port/protocol shape and the 65535 upper bound are validated
    # statically (ValueError) before any fact read, so a malformed
    # opening fails in prepare, not at apply. The commands need root:
    # flag the request ``sudo=True``.
    if not _PORT_RE.match(port) or any(
        int(number) > 65535 for number in port.split("/")[0].split("-")
    ):
        raise ValueError(
            f"port must be <port>[-<port>]/<tcp|udp|sctp|dccp> "
            f"with numbers up to 65535 (got {port!r})"
        )
    reasons: list[str] = []
    async for cmd in _manage_zone_member(
        ctx, zone, "port", port, present, permanent, "port", reasons
    ):
        ctx.changed = True
        yield cmd
    ctx.reason = "; ".join(reasons)


async def interface(
    ctx: OpContext,
    zone: str,
    interface: str,
    present: bool = True,
    permanent: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Bind or unbind a network interface to/from a firewalld zone.

    Args:
        ctx (OpContext): Execution context for the operation.
        zone (str): The zone to bind the interface to.
        interface (str): The interface name (``eth0``, ``enp3s0``, ...).
        present (bool, optional): Whether the interface should be bound
            to the zone. Defaults to True.
        permanent (bool, optional): Whether the change also applies to
            the permanent configuration (False is runtime-only).
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated firewalld interface commands.

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/firewalld/firewalld_interface.py``.
    """

    # A reemote5 original (the interface proposal): idempotent on the
    # FirewalldZones fact's interfaces list, dual-apply per scope.
    # present=True renders --change-interface, not --add-interface:
    # change rebinds an interface already bound to another zone (an
    # interface belongs to exactly one zone), while add would error --
    # the NetworkManager nuance the proposal flags is firewalld's own
    # to reconcile. present=False removes the binding only from the
    # named zone. The commands need root: flag the request
    # ``sudo=True``.
    reasons: list[str] = []
    async for cmd in _manage_zone_member(
        ctx, zone, "interface", interface, present, permanent, "interface", reasons
    ):
        ctx.changed = True
        yield cmd
    ctx.reason = "; ".join(reasons)


async def source(
    ctx: OpContext,
    zone: str,
    source: str,
    present: bool = True,
    permanent: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Bind or unbind a source address to/from a firewalld zone.

    Args:
        ctx (OpContext): Execution context for the operation.
        zone (str): The zone to bind the source to.
        source (str): The source to bind: an IP, a CIDR subnet, a MAC
            address or an ``ipset:<name>`` reference.
        present (bool, optional): Whether the source should be bound to
            the zone. Defaults to True.
        permanent (bool, optional): Whether the change also applies to
            the permanent configuration (False is runtime-only).
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated firewalld source commands.

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/firewalld/firewalld_source.py``.
    """

    # A reemote5 original (the source proposal): idempotent on the
    # FirewalldZones fact's sources list, dual-apply per scope. Source
    # bindings route traffic from specific subnets/hosts to a zone's
    # policy regardless of interface -- the zone-drifting use case.
    # The string is passed through verbatim (no address parsing):
    # firewall-cmd validates the shape at apply time. The commands
    # need root: flag the request ``sudo=True``.
    reasons: list[str] = []
    async for cmd in _manage_zone_member(
        ctx, zone, "source", source, present, permanent, "source", reasons
    ):
        ctx.changed = True
        yield cmd
    ctx.reason = "; ".join(reasons)


async def rich_rule(
    ctx: OpContext,
    zone: str,
    rule: str,
    present: bool = True,
    permanent: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Ensure a rich-language rule is present or absent in a zone.

    Args:
        ctx (OpContext): Execution context for the operation.
        zone (str): The zone to manage the rule in.
        rule (str): The complete rich rule string (``rule family="ipv4"
            source address="10.0.0.5/32" port port="5432"
            protocol="tcp" accept``), exactly as firewall-cmd prints
            it.
        present (bool, optional): Whether the rule should exist in the
            zone. Defaults to True.
        permanent (bool, optional): Whether the change also applies to
            the permanent configuration (False is runtime-only).
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated firewalld rich rule commands.

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/firewalld/firewalld_rich_rule.py``.
    """

    # A reemote5 original (the rich_rule proposal): idempotent on the
    # FirewalldZones fact's rich_rules list by EXACT string membership
    # -- the rule is never parsed or rewritten (the proposal's explicit
    # design point), so two rules differing only in attribute order
    # are different rules, and the string must match what
    # ``--list-rich-rules`` prints for the comparison to no-op. The
    # whole rule survives shlex quoting as one shell argument. The
    # commands need root: flag the request ``sudo=True``.
    reasons: list[str] = []
    async for cmd in _manage_zone_member(
        ctx, zone, "rich-rule", rule, present, permanent, "rich rule", reasons
    ):
        ctx.changed = True
        yield cmd
    ctx.reason = "; ".join(reasons)


async def protocol(
    ctx: OpContext,
    zone: str,
    protocol: str,
    present: bool = True,
    permanent: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Allow or disallow an IP protocol outright in a firewalld zone.

    Args:
        ctx (OpContext): Execution context for the operation.
        zone (str): The zone to manage the protocol in.
        protocol (str): The protocol name (``ah``, ``esp``, ``gre``,
            ... -- anything in /etc/protocols).
        present (bool, optional): Whether the protocol should be
            allowed. Defaults to True.
        permanent (bool, optional): Whether the change also applies to
            the permanent configuration (False is runtime-only).
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated firewalld protocol commands.

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/firewalld/firewalld_protocol.py``.
    """

    # A reemote5 original (the full-surface decision): idempotent on
    # the FirewalldZones fact's protocols list, dual-apply per scope.
    # A protocol rule accepts every port of the protocol -- the
    # IPsec (ah/esp) use case port rules cannot express. The name is
    # passed through verbatim; firewall-cmd validates it at apply
    # time. The commands need root: flag the request ``sudo=True``.
    reasons: list[str] = []
    async for cmd in _manage_zone_member(
        ctx, zone, "protocol", protocol, present, permanent, "protocol", reasons
    ):
        ctx.changed = True
        yield cmd
    ctx.reason = "; ".join(reasons)


async def icmp_block(
    ctx: OpContext,
    zone: str,
    icmp_type: str,
    present: bool = True,
    permanent: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Block or unblock an ICMP type in a firewalld zone.

    Args:
        ctx (OpContext): Execution context for the operation.
        zone (str): The zone to manage the ICMP block in.
        icmp_type (str): The ICMP type name (``echo-request``,
            ``router-advertisement``, ... -- ``firewall-cmd
            --get-icmptypes`` lists them).
        present (bool, optional): Whether the type should be blocked.
            Defaults to True.
        permanent (bool, optional): Whether the change also applies to
            the permanent configuration (False is runtime-only).
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated firewalld ICMP block commands.

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/firewalld/firewalld_icmp_block.py``.
    """

    # A reemote5 original (the full-surface decision): idempotent on
    # the FirewalldZones fact's icmp_blocks list, dual-apply per scope.
    # The parameter is ``icmp_type`` because ``type`` would shadow the
    # builtin and ``name`` is op()'s reserved plan-label
    # meta-parameter. Inversion (block everything *except* the listed
    # types) is zone-level state the fact exposes but no operation
    # manages -- a rare switch left to the Shell action. The commands
    # need root: flag the request ``sudo=True``.
    reasons: list[str] = []
    async for cmd in _manage_zone_member(
        ctx, zone, "icmp-block", icmp_type, present, permanent, "icmp-block", reasons
    ):
        ctx.changed = True
        yield cmd
    ctx.reason = "; ".join(reasons)


async def forward_port(
    ctx: OpContext,
    zone: str,
    port: str,
    protocol: str,
    to_port: str | None = None,
    to_addr: str | None = None,
    present: bool = True,
    permanent: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Forward a port to another port and/or address in a firewalld zone.

    Args:
        ctx (OpContext): Execution context for the operation.
        zone (str): The zone to manage the forwarding rule in.
        port (str): The source port to forward (``80``).
        protocol (str): The transport protocol (tcp, udp, sctp or
            dccp).
        to_port (str | None, optional): The destination port (``8080``);
            None forwards to the same port. Defaults to None.
        to_addr (str | None, optional): The destination address; None
            forwards to the local machine. Defaults to None.
        present (bool, optional): Whether the rule should exist.
            Defaults to True.
        permanent (bool, optional): Whether the change also applies to
            the permanent configuration (False is runtime-only).
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated firewalld forward port commands.

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/firewalld/firewalld_forward_port.py``.
    """

    # A reemote5 original (the Proposal's port_forward): idempotent on
    # the FirewalldZones fact's forward_ports list, dual-apply per
    # scope. The rule is compared as the exact
    # ``port=<p>:proto=<pr>[:toport=<tp>][:toaddr=<ta>]`` string
    # firewall-cmd prints, built from the typed arguments; at least
    # one of to_port/to_addr is required (a forward to neither is a
    # no-op rule firewall-cmd rejects). Forwarding to another address
    # needs masquerade enabled on the zone (``firewalld.masquerade``)
    # for the return path -- the operation does not enable it
    # implicitly. The commands need root: flag the request
    # ``sudo=True``.
    if not port.isdigit() or int(port) > 65535:
        raise ValueError(f"port must be a single port number up to 65535 (got {port!r})")
    if protocol not in ("tcp", "udp", "sctp", "dccp"):
        raise ValueError(f"protocol must be tcp, udp, sctp or dccp (got {protocol!r})")
    if to_port is not None and (not to_port.isdigit() or int(to_port) > 65535):
        raise ValueError(f"to_port must be a port number up to 65535 (got {to_port!r})")
    if to_port is None and to_addr is None:
        raise ValueError("forward_port needs at least one of to_port or to_addr")
    value = f"port={port}:proto={protocol}"
    if to_port is not None:
        value += f":toport={to_port}"
    if to_addr is not None:
        value += f":toaddr={to_addr}"
    reasons: list[str] = []
    async for cmd in _manage_zone_member(
        ctx, zone, "forward-port", value, present, permanent, "forward-port", reasons
    ):
        ctx.changed = True
        yield cmd
    ctx.reason = "; ".join(reasons)


# --- firewalld.masquerade ----------------------------------------------------------


@dataclass(frozen=True)
class FirewalldMasqueradeCommand(Command):
    """``firewall-cmd [--permanent] --zone --add-masquerade|--remove-masquerade``.

    The descriptor ``firewalld.masquerade`` yields, once per
    configuration scope that differs. The zone name is
    ``shlex``-quoted.
    """

    zone: str
    enabled: bool = True
    permanent: bool = True

    def shell(self) -> str:
        action = "--add-masquerade" if self.enabled else "--remove-masquerade"
        return (
            f"firewall-cmd {_permanent_flag(self.permanent)}"
            f"--zone={shlex.quote(self.zone)} {action}"
        )


async def masquerade(
    ctx: OpContext,
    zone: str,
    enabled: bool = True,
    permanent: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Enable or disable masquerading (source NAT) on a firewalld zone.

    Args:
        ctx (OpContext): Execution context for the operation.
        zone (str): The zone to manage masquerading on.
        enabled (bool, optional): Whether masquerading should be
            enabled. Defaults to True.
        permanent (bool, optional): Whether the change also applies to
            the permanent configuration (False is runtime-only).
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated firewalld masquerade commands.

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/firewalld/firewalld_masquerade.py``.
    """

    # A reemote5 original (the masquerade proposal): idempotent on the
    # FirewalldZones fact's masquerade boolean, dual-apply per scope.
    # Masquerade rewrites the source address of forwarded traffic to
    # the outgoing interface's address -- the NAT gateway use case,
    # and the return-path prerequisite for forward_port rules with a
    # to_addr. The commands need root: flag the request ``sudo=True``.
    reasons: list[str] = []
    word = "enabled" if enabled else "disabled"
    for perm, zones in await _zone_scopes(ctx, permanent):
        scope = "permanent" if perm else "runtime"
        record = zones.get(zone)
        if record is None:
            reasons.append(f"zone {zone} not found in {scope} configuration")
            continue
        if record["masquerade"] != enabled:
            ctx.changed = True
            reasons.append(f"masquerade will be {word} on zone {zone} ({scope})")
            yield FirewalldMasqueradeCommand(zone=zone, enabled=enabled, permanent=perm)
        else:
            reasons.append(f"masquerade already {word} on zone {zone} ({scope})")
    ctx.reason = "; ".join(reasons)


# --- firewalld.service -------------------------------------------------------------


@dataclass(frozen=True)
class FirewalldServiceCommand(Command):
    """``firewall-cmd --permanent --new-service|--delete-service``.

    The descriptor ``firewalld.service`` yields for definition
    existence. Service definitions are permanent-only by firewall-cmd's
    design; the daemon loads them at startup/reload. The service name
    is ``shlex``-quoted.
    """

    service: str
    present: bool = True

    def shell(self) -> str:
        action = "--new-service" if self.present else "--delete-service"
        return f"firewall-cmd --permanent {action}={shlex.quote(self.service)}"


@dataclass(frozen=True)
class FirewalldServiceEntryCommand(Command):
    """``firewall-cmd --permanent --service --add-<kind>|--remove-<kind>``.

    The descriptor ``firewalld.service`` yields for membership changes
    inside a definition; ``kind`` is ``port``, ``source-port``,
    ``protocol`` or ``module``. Service and value are ``shlex``-quoted.
    """

    service: str
    kind: str
    value: str
    present: bool = True

    def shell(self) -> str:
        verb = "add" if self.present else "remove"
        return (
            f"firewall-cmd --permanent --service={shlex.quote(self.service)} "
            f"--{verb}-{self.kind}={shlex.quote(self.value)}"
        )


@dataclass(frozen=True)
class FirewalldServiceTextCommand(Command):
    """``firewall-cmd --permanent --service --set-short|--set-description``.

    The descriptor ``firewalld.service`` yields for display-text
    changes; ``kind`` selects ``short`` or ``description``. Service and
    text are ``shlex``-quoted.
    """

    service: str
    kind: str
    text: str

    def shell(self) -> str:
        option = "--set-short" if self.kind == "short" else "--set-description"
        return (
            f"firewall-cmd --permanent --service={shlex.quote(self.service)} "
            f"{option}={shlex.quote(self.text)}"
        )


# The definition membership kinds mapped to their FirewalldService
# fields (the service-side sibling of _ZONE_MEMBER_FIELDS).
_SERVICE_MEMBER_FIELDS = {
    "port": "ports",
    "source-port": "source_ports",
    "protocol": "protocols",
    "module": "modules",
}


async def service(
    ctx: OpContext,
    service: str,
    present: bool = True,
    ports: tuple[str, ...] = (),
    source_ports: tuple[str, ...] = (),
    protocols: tuple[str, ...] = (),
    modules: tuple[str, ...] = (),
    short: str | None = None,
    description: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Manage a custom firewalld service definition (a port/protocol bundle).

    Args:
        ctx (OpContext): Execution context for the operation.
        service (str): The name of the service definition to manage
            (``my-app``); attach it to zones with ``zone_service``.
        present (bool, optional): Whether the definition should exist.
            Defaults to True.
        ports (tuple[str, ...], optional): The destination port openings
            the definition bundles (``8080/tcp``). Membership is
            additive -- entries not listed are left alone. Defaults to
            ().
        source_ports (tuple[str, ...], optional): The source port
            openings to bundle, additive. Defaults to ().
        protocols (tuple[str, ...], optional): The protocols to allow
            outright, additive. Defaults to ().
        modules (tuple[str, ...], optional): The connection-tracking
            helper modules to load (``nf_conntrack_ftp``), additive.
            Defaults to ().
        short (str | None, optional): The definition's short display
            name; None leaves it alone. Defaults to None.
        description (str | None, optional): The definition's description
            text; None leaves it alone. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated firewalld service definition commands.

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/firewalld/firewalld_service.py``.
    """

    # A reemote5 original (the Discussion's service semantics -- the
    # Proposal's attach-to-zone meaning is ``zone_service``):
    # idempotent on the FirewalldServices fact. Definitions are
    # permanent-only (--new-service and every --service subcommand
    # require --permanent), so there is no permanent flag and no
    # runtime scope; a changed definition reaches the runtime after
    # firewalld.reload. present=True creates a missing definition and
    # adds the missing members (sorted, so plan and apply report
    # identically -- the iptables.ipset additive precedent);
    # present=False deletes the definition. Deleting a *built-in*
    # definition fails at apply time (firewall-cmd protects /usr/lib);
    # shadowing one with a custom definition of the same name is the
    # supported customization path. The commands need root: flag the
    # request ``sudo=True``.
    services = await ctx.facts.get(FirewalldServices)
    reasons: list[str] = []

    if not present:
        if service in services:
            ctx.changed = True
            reasons.append(f"service {service} will be deleted")
            yield FirewalldServiceCommand(service=service, present=False)
        else:
            reasons.append(f"service {service} does not exist")
        ctx.reason = "; ".join(reasons)
        return

    if service not in services:
        ctx.changed = True
        reasons.append(f"service {service} will be created")
        yield FirewalldServiceCommand(service=service, present=True)
        current = FirewalldService(
            short="",
            description="",
            ports=[],
            source_ports=[],
            protocols=[],
            modules=[],
            destination={},
        )
    else:
        reasons.append(f"service {service} exists")
        current = services[service]

    for kind, values in (
        ("port", ports),
        ("source-port", source_ports),
        ("protocol", protocols),
        ("module", modules),
    ):
        field = _SERVICE_MEMBER_FIELDS[kind]
        adding = sorted(set(values) - set(current[field]))  # type: ignore[literal-required]
        if adding:
            ctx.changed = True
            reasons.append(
                f"service {service} {kind}s will be added: {', '.join(adding)}"
            )
            for value in adding:
                yield FirewalldServiceEntryCommand(
                    service=service, kind=kind, value=value, present=True
                )
        elif values:
            reasons.append(f"service {service} {kind}s already present")

    for kind, desired in (("short", short), ("description", description)):
        if desired is None:
            continue
        if current[kind] != desired:  # type: ignore[literal-required]
            ctx.changed = True
            reasons.append(f"service {service} {kind} will be set to {desired}")
            yield FirewalldServiceTextCommand(service=service, kind=kind, text=desired)
        else:
            reasons.append(f"service {service} {kind} already {desired}")

    ctx.reason = "; ".join(reasons)


# --- firewalld.ipset ---------------------------------------------------------------


@dataclass(frozen=True)
class FirewalldIpsetCommand(Command):
    """``firewall-cmd --permanent --new-ipset|--delete-ipset``: set existence.

    The descriptor ``firewalld.ipset`` yields for existence changes.
    Ipset creation is permanent-only by firewall-cmd's design;
    ``options`` carries ``key=value`` creation options (``maxelem``,
    ``family``, ``timeout``, ...) applied at creation only. Name, type
    and options are ``shlex``-quoted.
    """

    ipset_name: str
    ipset_type: str = "hash:ip"
    options: tuple[str, ...] = ()
    present: bool = True

    def shell(self) -> str:
        if not self.present:
            return f"firewall-cmd --permanent --delete-ipset={shlex.quote(self.ipset_name)}"
        parts = [
            "firewall-cmd",
            "--permanent",
            f"--new-ipset={shlex.quote(self.ipset_name)}",
            f"--type={shlex.quote(self.ipset_type)}",
        ]
        parts.extend(f"--option={shlex.quote(option)}" for option in self.options)
        return " ".join(parts)


@dataclass(frozen=True)
class FirewalldIpsetEntryCommand(Command):
    """``firewall-cmd [--permanent] --ipset --add-entry|--remove-entry``.

    The descriptor ``firewalld.ipset`` yields for membership changes,
    once per configuration scope that differs. Name and entry are
    ``shlex``-quoted.
    """

    ipset_name: str
    entry: str
    present: bool = True
    permanent: bool = True

    def shell(self) -> str:
        verb = "add" if self.present else "remove"
        return (
            f"firewall-cmd {_permanent_flag(self.permanent)}"
            f"--ipset={shlex.quote(self.ipset_name)} "
            f"--{verb}-entry={shlex.quote(self.entry)}"
        )


async def ipset(
    ctx: OpContext,
    ipset_name: str,
    present: bool = True,
    ipset_type: str = "hash:ip",
    entries: tuple[str, ...] = (),
    options: tuple[str, ...] = (),
    permanent: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Manage a firewalld ipset container and its member entries.

    Args:
        ctx (OpContext): Execution context for the operation.
        ipset_name (str): The name of the ipset to manage (referenced
            from zone sources as ``ipset:<name>``).
        present (bool, optional): Whether the ipset (and the listed
            entries) should exist. Defaults to True.
        ipset_type (str, optional): The set type used when creating it
            (``hash:ip``, ``hash:net``, ``hash:ip,port``, ...). An
            existing set of a different type raises ``ValueError``
            rather than silently recreating it. Defaults to "hash:ip".
        entries (tuple[str, ...], optional): The member addresses or
            networks to add (``present=True``) or remove
            (``present=False``); with ``present=False`` and no entries
            the whole definition is deleted. Membership is additive --
            entries not listed are left alone. Defaults to ().
        options (tuple[str, ...], optional): ``key=value`` creation
            options (``family=inet``, ``maxelem=65536``, ...), applied
            at creation only -- changing an existing set's options
            needs a destroy/recreate. Defaults to ().
        permanent (bool, optional): Whether the entry changes also
            apply to the permanent configuration (False is
            runtime-only; existence is always permanent -- firewall-cmd
            has no runtime ipset create/delete verb). Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated firewalld ipset commands.

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/firewalld/firewalld_ipset.py``.
    """

    # A reemote5 original (the ipset proposal): idempotent on the
    # FirewalldIpSets fact in both scopes, the firewalld-managed
    # sibling of iptables.ipset (which drives the kernel tables
    # directly). Existence is permanent-only (--new-ipset/--delete-ipset
    # require --permanent); a created set's runtime copy appears after
    # firewalld.reload, which some versions also require before a zone
    # rule can reference it -- the operation skips runtime entry
    # changes for a set the runtime fact does not show, with a reason.
    # An existing set of a different type raises ValueError (the
    # iptables.ipset precedent): recreating it would silently drop
    # members and break referencing rules. The parameter is
    # ``ipset_name`` because ``op()`` consumes ``name`` as the
    # plan-label meta-parameter. The commands need root: flag the
    # request ``sudo=True``.
    perm_sets = await ctx.facts.get(FirewalldIpSets, permanent=True)
    run_sets = await ctx.facts.get(FirewalldIpSets)
    reasons: list[str] = []

    if not present:
        if entries:
            if permanent:
                perm_members = set(perm_sets.get(ipset_name, {}).get("entries", []))
                removing = sorted(set(entries) & perm_members)
                if removing:
                    ctx.changed = True
                    reasons.append(
                        f"ipset {ipset_name} entries will be removed: {', '.join(removing)} "
                        "(permanent)"
                    )
                    for entry in removing:
                        yield FirewalldIpsetEntryCommand(
                            ipset_name=ipset_name, entry=entry, present=False, permanent=True
                        )
                else:
                    reasons.append(
                        f"ipset {ipset_name} holds none of the listed entries (permanent)"
                    )
            if ipset_name in run_sets:
                run_members = set(run_sets[ipset_name]["entries"])
                removing = sorted(set(entries) & run_members)
                if removing:
                    ctx.changed = True
                    reasons.append(
                        f"ipset {ipset_name} entries will be removed: {', '.join(removing)} "
                        "(runtime)"
                    )
                    for entry in removing:
                        yield FirewalldIpsetEntryCommand(
                            ipset_name=ipset_name, entry=entry, present=False, permanent=False
                        )
                else:
                    reasons.append(
                        f"ipset {ipset_name} holds none of the listed entries (runtime)"
                    )
            else:
                reasons.append(f"ipset {ipset_name} not found in runtime configuration")
        elif ipset_name in perm_sets:
            ctx.changed = True
            reasons.append(f"ipset {ipset_name} will be deleted (permanent)")
            yield FirewalldIpsetCommand(ipset_name=ipset_name, present=False)
        else:
            reasons.append(f"ipset {ipset_name} does not exist (permanent)")
        ctx.reason = "; ".join(reasons)
        return

    if ipset_name not in perm_sets:
        ctx.changed = True
        reasons.append(f"ipset {ipset_name} will be created ({ipset_type}, permanent)")
        yield FirewalldIpsetCommand(
            ipset_name=ipset_name, ipset_type=ipset_type, options=options, present=True
        )
        perm_members = set()
    else:
        current = perm_sets[ipset_name]
        if current["type"] != ipset_type:
            raise ValueError(
                f"ipset {ipset_name} exists with type {current['type']!r}, "
                f"not {ipset_type!r}; delete it first to change its type"
            )
        reasons.append(f"ipset {ipset_name} exists (permanent)")
        perm_members = set(current["entries"])

    if permanent:
        adding = sorted(set(entries) - perm_members)
        if adding:
            ctx.changed = True
            reasons.append(
                f"ipset {ipset_name} entries will be added: {', '.join(adding)} (permanent)"
            )
            for entry in adding:
                yield FirewalldIpsetEntryCommand(
                    ipset_name=ipset_name, entry=entry, present=True, permanent=True
                )
        elif entries:
            reasons.append(f"ipset {ipset_name} entries already present (permanent)")

    if ipset_name in run_sets:
        run_members = set(run_sets[ipset_name]["entries"])
        adding = sorted(set(entries) - run_members)
        if adding:
            ctx.changed = True
            reasons.append(
                f"ipset {ipset_name} entries will be added: {', '.join(adding)} (runtime)"
            )
            for entry in adding:
                yield FirewalldIpsetEntryCommand(
                    ipset_name=ipset_name, entry=entry, present=True, permanent=False
                )
        elif entries:
            reasons.append(f"ipset {ipset_name} entries already present (runtime)")
    elif entries:
        reasons.append(f"ipset {ipset_name} not found in runtime configuration")

    ctx.reason = "; ".join(reasons)


# --- firewalld.default_zone / lockdown / panic / reload -----------------------------


@dataclass(frozen=True)
class FirewalldDefaultZoneCommand(Command):
    """``firewall-cmd --set-default-zone``: the global default zone.

    The descriptor ``firewalld.default_zone`` yields. The verb applies
    immediately and persists -- there is no permanent/runtime split
    for the default zone. The zone name is ``shlex``-quoted.
    """

    zone: str

    def shell(self) -> str:
        return f"firewall-cmd --set-default-zone={shlex.quote(self.zone)}"


async def default_zone(
    ctx: OpContext,
    zone: str,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Set the global default firewalld zone.

    Args:
        ctx (OpContext): Execution context for the operation.
        zone (str): The zone unbound connections and interfaces should
            land in. It must already exist -- firewall-cmd rejects an
            unknown zone at apply time.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated firewalld default zone commands.

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/firewalld/firewalld_default_zone.py``.
    """

    # A reemote5 original (the default_zone proposal, the standalone
    # half of the Proposal's config): idempotent on the
    # FirewalldDefaultZone fact. --set-default-zone is inherently
    # dual-scope (it applies immediately and persists), so there is no
    # permanent flag. Zone existence is deliberately NOT validated
    # against the fact (the iptables.policy precedent): an unprivileged
    # empty read would then spuriously raise; an unknown zone fails at
    # apply time with CommandError. Only the empty string is a static
    # ValueError. The command needs root: flag the request
    # ``sudo=True``.
    if not zone:
        raise ValueError("zone must not be empty")
    current = await ctx.facts.get(FirewalldDefaultZone)
    if current != zone:
        ctx.changed = True
        ctx.reason = f"default zone will be set to {zone}"
        yield FirewalldDefaultZoneCommand(zone=zone)
    else:
        ctx.reason = f"default zone is already {zone}"


@dataclass(frozen=True)
class FirewalldLockdownCommand(Command):
    """``firewall-cmd --lockdown-on|--lockdown-off``: the lockdown switch.

    The descriptor ``firewalld.lockdown`` yields. Lockdown is a runtime
    switch here; the boot-time setting is firewalld.conf's Lockdown=
    key (a files.edit recipe).
    """

    enabled: bool = True

    def shell(self) -> str:
        return "firewall-cmd --lockdown-on" if self.enabled else "firewall-cmd --lockdown-off"


async def lockdown(
    ctx: OpContext,
    enabled: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Enable or disable firewalld lockdown mode.

    Args:
        ctx (OpContext): Execution context for the operation.
        enabled (bool, optional): Whether lockdown should be enabled.
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated firewalld lockdown commands.

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/firewalld/firewalld_lockdown.py``.
    """

    # A reemote5 original (the lockdown half of the Proposal's config):
    # idempotent on the private _FirewalldLockdown probe
    # (--query-lockdown). Lockdown restricts firewall configuration
    # changes to applications whitelisted in
    # /etc/firewalld/lockdown-whitelist.xml -- with it on, an
    # unwhitelisted firewall-cmd (including later steps of the same
    # deploy) is refused, so enable it last. Runtime-only: the
    # permanent setting is firewalld.conf's Lockdown= key, a files.edit
    # recipe the docs point at. The command needs root: flag the
    # request ``sudo=True``.
    current = await ctx.facts.get(_FirewalldLockdown)
    word = "enabled" if enabled else "disabled"
    if current != enabled:
        ctx.changed = True
        ctx.reason = f"lockdown will be {word}"
        yield FirewalldLockdownCommand(enabled=enabled)
    else:
        ctx.reason = f"lockdown already {word}"


@dataclass(frozen=True)
class FirewalldPanicCommand(Command):
    """``firewall-cmd --panic-on|--panic-off``: the panic switch.

    The descriptor ``firewalld.panic`` yields. Panic mode is
    runtime-only by design (it never persists -- a reboot clears it).
    """

    enabled: bool = True

    def shell(self) -> str:
        return "firewall-cmd --panic-on" if self.enabled else "firewall-cmd --panic-off"


async def panic(
    ctx: OpContext,
    enabled: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Enable or disable firewalld panic mode (drop ALL network traffic).

    Args:
        ctx (OpContext): Execution context for the operation.
        enabled (bool, optional): Whether panic mode should be enabled.
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated firewalld panic commands.

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/firewalld/firewalld_panic.py``.
    """

    # A reemote5 original (the full-surface decision): idempotent on
    # the private _FirewalldPanicMode probe (--query-panic). Panic mode
    # drops every packet, INCLUDING the SSH session the deploy rides:
    # enabling it severs the connection, so the apply log may end in a
    # transport error even though the command succeeded, and only
    # console/out-of-band access (or a reboot -- panic never persists)
    # can turn it off remotely. The example therefore demonstrates the
    # disable direction on real hosts. Runtime-only by design. The
    # command needs root: flag the request ``sudo=True``.
    current = await ctx.facts.get(_FirewalldPanicMode)
    word = "enabled" if enabled else "disabled"
    if current != enabled:
        ctx.changed = True
        ctx.reason = f"panic mode will be {word}"
        yield FirewalldPanicCommand(enabled=enabled)
    else:
        ctx.reason = f"panic mode already {word}"


@dataclass(frozen=True)
class FirewalldReloadCommand(Command):
    """``firewall-cmd --reload|--complete-reload|--runtime-to-permanent``.

    The descriptor ``firewalld.reload`` yields. ``mode`` selects the
    verb: ``reload`` applies permanent to runtime keeping state,
    ``complete_reload`` does the same dropping all connection state,
    ``runtime_to_permanent`` saves the live configuration to disk.
    """

    mode: str = "reload"

    def shell(self) -> str:
        option = {
            "reload": "--reload",
            "complete_reload": "--complete-reload",
            "runtime_to_permanent": "--runtime-to-permanent",
        }[self.mode]
        return f"firewall-cmd {option}"


async def reload(
    ctx: OpContext,
    mode: str = "reload",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Reload the firewall, or save the runtime configuration to permanent.

    Args:
        ctx (OpContext): Execution context for the operation.
        mode (str, optional): ``reload`` applies the permanent
            configuration to runtime (keeping connection state),
            ``complete_reload`` does the same dropping all state,
            ``runtime_to_permanent`` saves the runtime configuration to
            disk. Defaults to "reload".
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The firewall-cmd reload command, always (the operation
        is stateless).

    **Raises:**

    ```python
    ValueError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/firewalld/firewalld_reload.py``.
    """

    # A reemote5 original (the reload proposal): deliberately STATELESS
    # (the selinux.restorecon / apt.update precedent) -- no fact can
    # cheaply answer "runtime and permanent are in sync" (the zones
    # facts could be diffed, but a reload also re-reads service/ipset
    # definitions and direct rules, so a diff would under-report), so
    # every request yields and reports a change. The dual-apply
    # operations do not need it for their own changes; it is the
    # handler after permanent-only edits (zone/service/ipset existence)
    # and the escape hatch for drift. complete_reload drops connection
    # tracking state -- established connections may break. The mode is
    # validated statically (ValueError). The command needs root: flag
    # the request ``sudo=True``.
    if mode not in ("reload", "complete_reload", "runtime_to_permanent"):
        raise ValueError(
            f"mode must be reload, complete_reload or runtime_to_permanent (got {mode!r})"
        )
    ctx.changed = True
    ctx.reason = f"firewalld {mode} will run"
    yield FirewalldReloadCommand(mode=mode)


__all__ = [
    "FirewalldDefaultZone",
    "FirewalldDefaultZoneCommand",
    "FirewalldIpSet",
    "FirewalldIpSets",
    "FirewalldIpsetCommand",
    "FirewalldIpsetEntryCommand",
    "FirewalldLockdownCommand",
    "FirewalldMasqueradeCommand",
    "FirewalldPanicCommand",
    "FirewalldPolicies",
    "FirewalldPolicy",
    "FirewalldReloadCommand",
    "FirewalldService",
    "FirewalldServiceCommand",
    "FirewalldServiceEntryCommand",
    "FirewalldServiceTextCommand",
    "FirewalldServices",
    "FirewalldStatus",
    "FirewalldStatusInfo",
    "FirewalldZone",
    "FirewalldZoneCommand",
    "FirewalldZoneEntryCommand",
    "FirewalldZoneTargetCommand",
    "FirewalldZoneTextCommand",
    "FirewalldZones",
    "default_zone",
    "forward_port",
    "icmp_block",
    "interface",
    "ipset",
    "lockdown",
    "masquerade",
    "panic",
    "port",
    "protocol",
    "reload",
    "rich_rule",
    "service",
    "source",
    "zone",
    "zone_service",
]