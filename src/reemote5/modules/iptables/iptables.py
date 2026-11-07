# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``iptables`` module: facts and operations in one source.
#
# Fact side: ``IptablesChains``/``IptablesRules`` and their ``Ip6tables``
# siblings are ports of pyinfra's four iptables facts -- the chain ->
# policy mapping and the parsed rule-definition list, both read from
# ``iptables-save -t <table>`` (``ip6tables-save`` for IPv6). Ten reemote5
# originals widen the visibility pyinfra's set leaves missing:
# ``IptablesRulesOrdered``/``Ip6tablesRulesOrdered`` (rules with their
# 1-based line numbers and exact packet/byte counters, parsed from
# ``iptables -t <table> -L -n -v -x --line-numbers`` -- the ordered-rules
# and rule-counters proposals answered by one fact),
# ``IptablesTables``/``Ip6tablesTables`` (the loaded tables, from
# ``/proc/net/ip_tables_names`` / ``ip6_tables_names``),
# ``IptablesVersion``/``Ip6tablesVersion`` (the installed release and the
# legacy-vs-nf_tables backend), ``IptablesModules`` (the loaded ``xt_*``
# match extensions), ``IpSets`` (the ipset containers and their members),
# and ``IptablesPersistence``/``Ip6tablesPersistence`` (whether the live
# rules match an on-disk ``iptables-save`` snapshot, comment lines
# ignored because the save header carries a fresh timestamp every run).
# Like every fact they are phase-unaware: their ``process`` coroutine
# just gathers the raw value.
#
# Privilege note: ``iptables-save``, ``iptables -L`` and ``ipset list``
# need CAP_NET_ADMIN on the target. Following the hardware.SmbiosInfo
# precedent, every root-read fact retries its command through
# passwordless ``sudo -n`` at the shell level (never the elevation
# layer -- facts stay side-effect free and prompt-free): the contract
# is full data for a root login or a NOPASSWD sudoers rule, and the
# empty default otherwise, indistinguishable from genuinely-absent
# state. The operations below flag their commands ``sudo=True`` at the
# ``op()`` call site; their fact reads ride the same ``sudo -n`` retry,
# so idempotence reporting works on a sudoers fleet.
#
# Operation side: ``iptables.chain`` and ``iptables.rule`` port pyinfra's
# two operations (chain create/delete/policy, rule add/insert/delete with
# the core matches, NAT target arguments and the ``extras`` escape
# hatch). ``rule`` gains first-class typed match extensions (``ctstate``,
# ``destination_ports``/``source_ports`` multiport, ``limit``/
# ``limit_burst``, ``match_set``, ``log_level``) folded into the same
# extras comparison pyinfra's shortcuts use, plus a ``position`` argument
# for insertion at an explicit line number. Six reemote5 originals
# complete the control surface: ``policy`` (standalone built-in chain
# policy management), ``flush`` (idempotent rule flush), ``zero``
# (stateless counter reset), ``save``/``restore`` (persistence to/from a
# host-side snapshot file, idempotent through the Persistence facts) and
# ``ipset`` (set and membership management, idempotent through
# ``IpSets``).
#
# Deliberately absent (decided with the developer): a ``bulk_rule``
# operation taking a list of rule dicts -- ``op()`` arguments must be
# hashable, and atomic bulk application is covered by ``restore`` reading
# a host-side iptables-restore file (upload it with ``files.put`` first);
# a standalone ``match`` helper -- a match extension mutates nothing, so
# it cannot be an operation, and the typed ``rule`` kwargs cover it;
# high-level ``nat``/``log`` wrappers -- ``rule`` already validates the
# NAT arguments and ``log_level`` covers the LOG target; ``exact``
# full-chain reordering -- flush-and-reapply semantics are destructive
# and race with live traffic; ``IptablesDefaultPolicies`` (the Chains
# facts already answer it) and ``IptablesNatRules`` (``table="nat"``
# covers it); conntrack-table visibility (needs the separate
# conntrack-tools binary). Interface/address visibility lives in
# ``hardware.NetworkDevices`` (the ``Ipv4Address``/``Ipv6Address``
# blocks), not here.
#
# Operations are async generators that receive an ``OpContext``, await
# facts, set ``ctx.changed``/``ctx.reason``, and yield command
# descriptors; they never touch the host directly.


import re
import shlex
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict

from ...commands import Command
from ...core import OpContext
from ...hosts import Host

# --- Shared helpers ----------------------------------------------------------

# pyinfra's ``IPTABLES_ARGS`` verbatim: the iptables-save flags that map
# to named definition keys. Everything else (``-m`` modules, ``--dport``,
# ``--log-level``, ...) lands in the sorted ``extras`` pair list, which
# is what makes the rule comparison order-independent.
_IPTABLES_ARGS = {
    "-A": "chain",
    "-j": "jump",
    # Boolean matches
    "-p": "protocol",
    "-s": "source",
    "-d": "destination",
    "-i": "in_interface",
    "-o": "out_interface",
    # Logging
    "--log-prefix": "log_prefix",
    # NAT exit rules
    "--to-destination": "to_destination",
    "--to-source": "to_source",
    "--to-ports": "to_ports",
}


def _parse_extras(tokens: Iterable[str]) -> list[list[str]]:
    """Parse tokens into a sorted list of ``[flag, value]`` pairs.

    pyinfra's ``parse_extras`` verbatim: tokens that don't start with
    ``-`` are joined as the value of the preceding flag, and the pairs
    are sorted so two rules differing only in argument order compare
    equal. Pure and testable.
    """
    extras: list[list[str]] = []
    current_flag: str | None = None
    current_values: list[str] = []

    for token in tokens:
        if token.startswith("-"):
            if current_flag is not None:
                extras.append([current_flag, " ".join(current_values)])
            current_flag = token
            current_values = []
        else:
            current_values.append(token)

    if current_flag is not None:
        extras.append([current_flag, " ".join(current_values)])

    return sorted(extras)


def _parse_iptables_rule(line: str) -> "IptablesRule":
    """Parse one ``iptables-save`` rule line into a definition dict.

    pyinfra's ``parse_iptables_rule`` verbatim: each flag maps through
    ``_IPTABLES_ARGS`` to a named key (a preceding ``!`` prefixes the key
    with ``not_``), and unmapped flags collect into the sorted ``extras``
    pair list. Pure and testable.
    """
    bits = shlex.split(line)

    definition: dict[str, Any] = {}
    extra_tokens: list[str] = []

    key: str | None = None
    args: list[str] = []
    not_arg = False

    def add_args() -> None:
        arg_string = " ".join(args)

        if key and key in _IPTABLES_ARGS:
            definition_key = f"not_{_IPTABLES_ARGS[key]}" if not_arg else _IPTABLES_ARGS[key]
            definition[definition_key] = arg_string
        elif key:
            extra_tokens.append(key)
            extra_tokens.extend(args)

    for bit in bits:
        if bit == "!":
            if key:
                add_args()
                args = []
                key = None

            not_arg = True

        elif bit.startswith("-"):
            if key:
                add_args()
                args = []
                not_arg = False

            key = bit

        else:
            args.append(bit)

    if key:
        add_args()

    if extra_tokens:
        definition["extras"] = _parse_extras(extra_tokens)

    return definition  # type: ignore[return-value]


def _copy_rule(rule: "IptablesRule") -> "IptablesRule":
    """Copy one rule definition including its nested extras pairs."""
    copied = dict(rule)
    if "extras" in copied:
        copied["extras"] = [list(pair) for pair in copied["extras"]]
    return copied  # type: ignore[return-value]


def _binary(version: int) -> str:
    """The iptables/ip6tables binary for a ``version`` argument.

    Raises ``ValueError`` for anything but 4 or 6 -- every operation
    validates its ``version`` through this helper before reading facts.
    """
    if version == 4:
        return "iptables"
    if version == 6:
        return "ip6tables"
    raise ValueError(f"version must be 4 or 6 (got {version!r})")


def _privileged_read(command: str) -> str:
    """Wrap a root-read command with the passwordless ``sudo -n`` retry.

    The ``hardware.SmbiosInfo`` precedent for facts that need
    CAP_NET_ADMIN: try unprivileged first, retry through ``sudo -n``
    (never prompts, creates no askpass file, safe under the across-host
    fan-out), and mask every failure with ``|| true`` so a host that
    grants neither reads the fact's empty default instead of raising.
    """
    return f"{command} 2> /dev/null || sudo -n {command} 2> /dev/null || true"


def _normalize_host(value: str, version: int) -> str:
    """Expand a bare address to its host prefix the way iptables-save prints it.

    pyinfra appends ``/32`` to a slash-less source/destination for both
    families; reemote5 uses ``/128`` for IPv6 (the correct single-host
    prefix) -- a deliberate deviation, because a ``/32`` IPv6 definition
    would never compare equal to what ``ip6tables-save`` prints.
    """
    if "/" in value:
        return value
    return f"{value}/32" if version == 4 else f"{value}/128"


def _persistence_command(binary: str, path: str) -> str:
    """The shell comparing the live rules against a saved snapshot file.

    Both sides drop ``#`` comment lines before comparing: the
    ``iptables-save`` header carries a fresh timestamp on every run, so
    a raw diff would always report drift. Command substitution keeps the
    whole probe POSIX-sh safe (no bash process substitution) and
    side-effect free. An empty live read (an unprivileged login, or a
    missing binary) reports drift rather than a false match against an
    empty file; the trailing ``|| echo drifted`` masks every exit status,
    so only ``HostConnectionError`` can surface from the transport.
    """
    inner = (
        f"live=$( ({binary}-save 2>/dev/null || sudo -n {binary}-save 2>/dev/null) "
        "| sed '/^#/d'); "
        f"saved=$(sed '/^#/d' {shlex.quote(path)} 2>/dev/null || true); "
        '[ -n "$live" ] && [ "$live" = "$saved" ] && echo synced || echo drifted'
    )
    return f"sh -c {shlex.quote(inner)}"


# --- Facts -------------------------------------------------------------------


class IptablesChains:
    """The iptables chains and their policies in one table.

    **Commands:**

    ```bash
    iptables-save -t <table> 2> /dev/null || sudo -n iptables-save -t <table> 2> /dev/null || true
    ```

    **Returns:**

    ```python
    dict[str, str]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/iptables/iptables_iptables_chains.py``.
    """

    # Mirrors pyinfra's ``iptables.IptablesChains``: the ``:NAME POLICY
    # [pkts:bytes]`` lines of ``iptables-save -t <table>`` map each chain
    # to its policy word. A user-defined chain's policy is ``-`` (it has
    # none); the built-in chains carry ACCEPT/DROP. The packet/byte
    # counters on the same lines are ignored, pyinfra verbatim --
    # ``IptablesRulesOrdered`` answers per-rule counters instead.
    #
    # Deliberate deviations from pyinfra: the ``table`` argument is
    # shell-quoted (pyinfra interpolates it raw), the
    # ``requires_command`` gating is dropped, and the bare
    # ``iptables-save -t <table>`` gains the passwordless ``sudo -n``
    # retry (the hardware.SmbiosInfo precedent) because reading the
    # tables needs CAP_NET_ADMIN -- full data for a root login or a
    # NOPASSWD sudoers rule, {} otherwise (the module header's
    # privilege note). The ``|| true`` guard masks exit 127, so only
    # ``HostConnectionError`` can surface.
    #
    # SimulatedHost seam note: the ``iptables_chains`` field stores
    # table -> {chain: policy}; an unseeded table reads as {}.

    #: Shell template gathering the chains over SSH (``<table>`` is the
    #: requested table, default ``filter``), with the passwordless
    #: ``sudo -n`` retry for the CAP_NET_ADMIN read.
    COMMAND = (
        "iptables-save -t <table> 2> /dev/null "
        "|| sudo -n iptables-save -t <table> 2> /dev/null || true"
    )

    #: The save binary this fact family runs (the IPv6 sibling flips it).
    BINARY = "iptables-save"

    #: The SimulatedHost state field this fact family reads.
    SEAM = "iptables_chains"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, str]:
        """Parse ``iptables-save`` chain lines into chain -> policy. Pure."""
        chains: dict[str, str] = {}
        for line in output:
            if line.startswith(":"):
                name, policy, _ = line[1:].split()
                chains[name] = policy
        return chains

    @classmethod
    async def process(cls, host: Host, table: str = "filter") -> dict[str, str]:
        state = getattr(host, cls.SEAM, None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return dict(state.get(table, {}))
        # Real transport: gather over asyncssh from the host's connection,
        # retrying the CAP_NET_ADMIN read through passwordless sudo -n.
        stdout = await host.run_shell(
            _privileged_read(f"{cls.BINARY} -t {shlex.quote(table)}")
        )
        return cls.parse(stdout.splitlines())


class Ip6tablesChains(IptablesChains):
    """The ip6tables chains and their policies in one table.

    **Commands:**

    ```bash
    ip6tables-save -t <table> 2> /dev/null || sudo -n ip6tables-save -t <table> 2> /dev/null || true
    ```

    **Returns:**

    ```python
    dict[str, str]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/iptables/iptables_ip6tables_chains.py``.
    """

    # The IPv6 sibling of ``IptablesChains``, mirroring pyinfra's
    # ``iptables.Ip6tablesChains``: the parse is inherited verbatim and
    # only the save binary and the SimulatedHost seam field flip.

    #: Shell template gathering the chains over SSH (IPv6), with the
    #: passwordless ``sudo -n`` retry.
    COMMAND = (
        "ip6tables-save -t <table> 2> /dev/null "
        "|| sudo -n ip6tables-save -t <table> 2> /dev/null || true"
    )

    #: The save binary this fact family runs (IPv6).
    BINARY = "ip6tables-save"

    #: The SimulatedHost state field this fact family reads (IPv6).
    SEAM = "ip6tables_chains"


class IptablesRule(TypedDict):
    """
    Attributes:
        chain: The chain the rule lives in (the ``-A`` argument).
        jump: The rule's target (the ``-j`` argument), when the line
            carried one.
        protocol: The protocol match (``tcp``, ``udp``, ...), when set.
        source: The source address/CIDR match, when set.
        destination: The destination address/CIDR match, when set.
        in_interface: The incoming interface match, when set.
        out_interface: The outgoing interface match, when set.
        not_protocol: The negated protocol match (``! -p``), when set.
        not_source: The negated source match (``! -s``), when set.
        not_destination: The negated destination match (``! -d``), when
            set.
        not_in_interface: The negated incoming interface match, when set.
        not_out_interface: The negated outgoing interface match, when
            set.
        log_prefix: The LOG target's ``--log-prefix`` value, when set.
        to_destination: The DNAT target's ``--to-destination`` value,
            when set.
        to_source: The SNAT target's ``--to-source`` value, when set.
        to_ports: The REDIRECT target's ``--to-ports`` value, when set.
        extras: Every unmapped flag as sorted ``[flag, value]`` pairs
            (``-m`` modules, ``--dport``, ``--comment``, ``--log-level``,
            ...), absent when the rule carried none.
    """

    # One parsed ``iptables-save`` rule line (``IptablesRules``' entry),
    # as a plain dict -- pyinfra's ``parse_iptables_rule`` shape verbatim.
    # Every key is NotRequired because the parser only records the flags
    # the line actually carried; even ``chain``/``jump`` are absent on a
    # degenerate line. The ``extras`` pairs are lists (not tuples) so the
    # entries stay JSON-serializable and compare equal to pyinfra's
    # fixture dicts.

    chain: NotRequired[str]
    jump: NotRequired[str]
    protocol: NotRequired[str]
    source: NotRequired[str]
    destination: NotRequired[str]
    in_interface: NotRequired[str]
    out_interface: NotRequired[str]
    not_protocol: NotRequired[str]
    not_source: NotRequired[str]
    not_destination: NotRequired[str]
    not_in_interface: NotRequired[str]
    not_out_interface: NotRequired[str]
    log_prefix: NotRequired[str]
    to_destination: NotRequired[str]
    to_source: NotRequired[str]
    to_ports: NotRequired[str]
    extras: NotRequired[list[list[str]]]


class IptablesRules:
    """The iptables rules of one table, as parsed rule definitions.

    **Commands:**

    ```bash
    iptables-save -t <table> 2> /dev/null || sudo -n iptables-save -t <table> 2> /dev/null || true
    ```

    **Returns:**

    ```python
    list[IptablesRule]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/iptables/iptables_iptables_rules.py``.
    """

    # Mirrors pyinfra's ``iptables.IptablesRules``: every ``-``-prefixed
    # line of ``iptables-save -t <table>`` parses through
    # ``_parse_iptables_rule`` into an ``IptablesRule`` dict, in listing
    # order (which is chain order, then rule order -- but the *line
    # number* within a chain is not recorded; ``IptablesRulesOrdered``
    # answers order and counters together). The table defaults to
    # ``filter``; pass ``table="nat"``/``"mangle"``/``"raw"`` for the
    # others (the convenience ``IptablesNatRules`` proposal is this
    # argument).
    #
    # Deliberate deviations from pyinfra: the ``table`` argument is
    # shell-quoted, ``requires_command`` gating is dropped, and the save
    # command gains the passwordless ``sudo -n`` retry (the module
    # header's privilege note); the ``|| true`` guard masks exit 127.
    #
    # SimulatedHost seam note: the ``iptables_rules`` field stores
    # table -> [rule definitions]; an unseeded table reads as [].

    #: Shell template gathering the rules over SSH (``<table>`` is the
    #: requested table, default ``filter``), with the passwordless
    #: ``sudo -n`` retry for the CAP_NET_ADMIN read.
    COMMAND = (
        "iptables-save -t <table> 2> /dev/null "
        "|| sudo -n iptables-save -t <table> 2> /dev/null || true"
    )

    #: The save binary this fact family runs (the IPv6 sibling flips it).
    BINARY = "iptables-save"

    #: The SimulatedHost state field this fact family reads.
    SEAM = "iptables_rules"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[IptablesRule]:
        """Parse ``iptables-save`` rule lines into definitions. Pure."""
        return [_parse_iptables_rule(line) for line in output if line.startswith("-")]

    @classmethod
    async def process(cls, host: Host, table: str = "filter") -> list[IptablesRule]:
        state = getattr(host, cls.SEAM, None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly,
            # copying each definition including its extras pairs.
            return [_copy_rule(rule) for rule in state.get(table, [])]
        # Real transport: gather over asyncssh from the host's connection,
        # retrying the CAP_NET_ADMIN read through passwordless sudo -n.
        stdout = await host.run_shell(
            _privileged_read(f"{cls.BINARY} -t {shlex.quote(table)}")
        )
        return cls.parse(stdout.splitlines())


class Ip6tablesRules(IptablesRules):
    """The ip6tables rules of one table, as parsed rule definitions.

    **Commands:**

    ```bash
    ip6tables-save -t <table> 2> /dev/null || sudo -n ip6tables-save -t <table> 2> /dev/null || true
    ```

    **Returns:**

    ```python
    list[IptablesRule]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/iptables/iptables_ip6tables_rules.py``.
    """

    # The IPv6 sibling of ``IptablesRules``, mirroring pyinfra's
    # ``iptables.Ip6tablesRules``: the parse is inherited verbatim and
    # only the save binary and the SimulatedHost seam field flip.

    #: Shell template gathering the rules over SSH (IPv6), with the
    #: passwordless ``sudo -n`` retry.
    COMMAND = (
        "ip6tables-save -t <table> 2> /dev/null "
        "|| sudo -n ip6tables-save -t <table> 2> /dev/null || true"
    )

    #: The save binary this fact family runs (IPv6).
    BINARY = "ip6tables-save"

    #: The SimulatedHost state field this fact family reads (IPv6).
    SEAM = "ip6tables_rules"


class IptablesOrderedRule(TypedDict):
    """
    Attributes:
        chain: The chain the rule lives in (from the ``Chain`` header).
        number: The 1-based rule number within its chain (the
            ``--line-numbers`` column).
        packets: The exact packets counter (``-x``, never K/M-scaled).
        bytes: The exact bytes counter (``-x``, never K/M-scaled).
        target: The rule's target column (``ACCEPT``, ``DROP``, a user
            chain, ...).
        protocol: The protocol column as printed (``tcp``, ``udp``,
            ``all``, or a numeric protocol on some releases).
        opt: The options column, usually ``--``.
        in_interface: The incoming interface column (``*`` for any).
        out_interface: The outgoing interface column (``*`` for any).
        source: The source address/CIDR column (``0.0.0.0/0`` for any).
        destination: The destination address/CIDR column.
        options: The trailing match/target detail text (``tcp dpt:22``,
            ``ctstate RELATED,ESTABLISHED``, ...), ``""`` when the row
            carried none.
    """

    # One numbered, counted rule row (``IptablesRulesOrdered``' entry),
    # as a plain dict. Unlike ``IptablesRule`` (the iptables-save
    # definition shape) every column is always present, because the
    # ``-L -n -v -x --line-numbers`` listing is columnar; the counters
    # are ints because ``-x`` disables the K/M suffix scaling.

    chain: str
    number: int
    packets: int
    bytes: int
    target: str
    protocol: str
    opt: str
    in_interface: str
    out_interface: str
    source: str
    destination: str
    options: str


class IptablesRulesOrdered:
    """The iptables rules with line numbers and packet/byte counters.

    **Commands:**

    ```bash
    iptables -t <table> -L <chain> -n -v -x --line-numbers 2> /dev/null || sudo -n iptables -t <table> -L <chain> -n -v -x --line-numbers 2> /dev/null || true
    ```

    **Returns:**

    ```python
    list[IptablesOrderedRule]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/iptables/iptables_iptables_rules_ordered.py``.
    """

    # A reemote5 original answering the ordered-rules *and* rule-counters
    # proposals with one fact: ``-L`` is columnar, so one parse yields the
    # 1-based ``number`` (what ``iptables -I <chain> <number>`` and
    # ``-D <chain> <number>`` address) beside the exact ``packets``/
    # ``bytes`` counters (``-x`` defeats the K/M scaling that would make
    # auditing lossy). ``chain`` narrows the listing to one chain; the
    # default None lists every chain of the table and the ``Chain NAME
    # (...)`` headers attribute each row. ``-n`` keeps addresses numeric
    # (no DNS lookups -- a fact must not block on resolution).
    #
    # The ``protocol`` column stays as printed: releases disagree on
    # numeric-vs-named protocol rendering under ``-n``, so no
    # normalization is attempted. Chain policy/counter headers are not
    # returned (``IptablesChains`` answers policies).
    #
    # Reading the tables needs CAP_NET_ADMIN, so the listing rides the
    # passwordless ``sudo -n`` retry (the module header's privilege
    # note); a login granted neither answers [].
    #
    # SimulatedHost seam note: the ``iptables_ordered_rules`` field
    # stores table -> [entries across all chains]; the ``chain`` argument
    # filters that list, and the rule/flush/zero mutators keep the
    # numbers renumbered per chain.

    #: Shell template gathering the numbered rows over SSH (``<chain>``
    #: is omitted when the whole table is listed), with the passwordless
    #: ``sudo -n`` retry for the CAP_NET_ADMIN read.
    COMMAND = (
        "iptables -t <table> -L <chain> -n -v -x --line-numbers 2> /dev/null "
        "|| sudo -n iptables -t <table> -L <chain> -n -v -x --line-numbers "
        "2> /dev/null || true"
    )

    #: The binary this fact family runs (the IPv6 sibling flips it).
    BINARY = "iptables"

    #: The SimulatedHost state field this fact family reads.
    SEAM = "iptables_ordered_rules"

    @classmethod
    def parse(cls, output: Iterable[str], chain: str | None = None) -> list[IptablesOrderedRule]:
        """Parse ``-L -n -v -x --line-numbers`` rows into entries. Pure.

        ``Chain NAME (...)`` headers set the current chain; a row whose
        first token is an integer is a rule (the column header line and
        blank lines contribute nothing). The first ten whitespace
        columns are fixed; everything after the destination column is
        the free-form ``options`` text.
        """
        rules: list[IptablesOrderedRule] = []
        current: str | None = None
        for line in output:
            if line.startswith("Chain "):
                parts = line.split()
                current = parts[1] if len(parts) > 1 else None
                continue
            columns = line.split(None, 9)
            if current is None or len(columns) < 10 or not columns[0].isdigit():
                continue
            if chain is not None and current != chain:
                continue
            # split(None, 9) caps at ten tokens, so the destination and
            # the trailing options share the last token; re-split it
            # once to separate the two columns.
            tail = columns[9].split(None, 1)
            rules.append(
                {
                    "chain": current,
                    "number": int(columns[0]),
                    "packets": int(columns[1]),
                    "bytes": int(columns[2]),
                    "target": columns[3],
                    "protocol": columns[4],
                    "opt": columns[5],
                    "in_interface": columns[6],
                    "out_interface": columns[7],
                    "source": columns[8],
                    "destination": tail[0],
                    "options": tail[1] if len(tail) > 1 else "",
                }
            )
        return rules

    @classmethod
    async def process(
        cls, host: Host, table: str = "filter", chain: str | None = None
    ) -> list[IptablesOrderedRule]:
        state = getattr(host, cls.SEAM, None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly,
            # filtering by chain and copying each flat entry dict.
            return [
                dict(rule)
                for rule in state.get(table, [])
                if chain is None or rule["chain"] == chain
            ]
        # Real transport: gather over asyncssh from the host's connection,
        # retrying the CAP_NET_ADMIN read through passwordless sudo -n.
        target = f" {shlex.quote(chain)}" if chain is not None else ""
        stdout = await host.run_shell(
            _privileged_read(
                f"{cls.BINARY} -t {shlex.quote(table)} -L{target} -n -v -x --line-numbers"
            )
        )
        return cls.parse(stdout.splitlines(), chain)


class Ip6tablesRulesOrdered(IptablesRulesOrdered):
    """The ip6tables rules with line numbers and packet/byte counters.

    **Commands:**

    ```bash
    ip6tables -t <table> -L <chain> -n -v -x --line-numbers 2> /dev/null || sudo -n ip6tables -t <table> -L <chain> -n -v -x --line-numbers 2> /dev/null || true
    ```

    **Returns:**

    ```python
    list[IptablesOrderedRule]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/iptables/iptables_ip6tables_rules_ordered.py``.
    """

    # The IPv6 sibling of ``IptablesRulesOrdered``: the columnar parse is
    # inherited verbatim (the address column simply holds IPv6 CIDRs)
    # and only the binary and the SimulatedHost seam field flip.

    #: Shell template gathering the numbered rows over SSH (IPv6), with
    #: the passwordless ``sudo -n`` retry.
    COMMAND = (
        "ip6tables -t <table> -L <chain> -n -v -x --line-numbers 2> /dev/null "
        "|| sudo -n ip6tables -t <table> -L <chain> -n -v -x --line-numbers "
        "2> /dev/null || true"
    )

    #: The binary this fact family runs (IPv6).
    BINARY = "ip6tables"

    #: The SimulatedHost state field this fact family reads (IPv6).
    SEAM = "ip6tables_ordered_rules"


class IptablesTables:
    """The iptables tables currently loaded in the kernel.

    **Commands:**

    ```bash
    cat /proc/net/ip_tables_names 2>/dev/null || true
    ```

    **Returns:**

    ```python
    list[str]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/iptables/iptables_iptables_tables.py``.
    """

    # A reemote5 original: pyinfra has no table-inventory fact, yet
    # conditional logic ("only manage nat when the module is loaded")
    # needs one. ``/proc/net/ip_tables_names`` lists exactly the tables
    # whose kernel modules are loaded (a table appears the first time
    # something references it), in load order -- which is what the fact
    # returns, unsorted, one name per line. The proc file is
    # world-readable, so unlike the save/list facts this one needs no
    # privileged login.
    #
    # The ``|| true`` guard masks a missing proc file (a kernel without
    # the tables loaded) and a missing ``cat`` alike, so only
    # ``HostConnectionError`` can surface.
    #
    # SimulatedHost seam note: the ``iptables_tables`` field stores the
    # name list directly; the default [] models a host with no tables
    # loaded.

    #: Shell gathering the loaded table names over SSH.
    COMMAND = "cat /proc/net/ip_tables_names 2>/dev/null || true"

    #: The proc file this fact family reads (the IPv6 sibling flips it).
    PROC = "/proc/net/ip_tables_names"

    #: The SimulatedHost state field this fact family reads.
    SEAM = "iptables_tables"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[str]:
        """Parse proc-file lines into table names, in load order. Pure."""
        return [line.strip() for line in output if line.strip()]

    @classmethod
    async def process(cls, host: Host) -> list[str]:
        state = getattr(host, cls.SEAM, None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return list(state)
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(f"cat {cls.PROC} 2>/dev/null || true")
        return cls.parse(stdout.splitlines())


class Ip6tablesTables(IptablesTables):
    """The ip6tables tables currently loaded in the kernel.

    **Commands:**

    ```bash
    cat /proc/net/ip6_tables_names 2>/dev/null || true
    ```

    **Returns:**

    ```python
    list[str]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/iptables/iptables_ip6tables_tables.py``.
    """

    # The IPv6 sibling of ``IptablesTables``: the IPv6 tables register in
    # their own proc file, and only the path and the SimulatedHost seam
    # field flip.

    #: Shell gathering the loaded table names over SSH (IPv6).
    COMMAND = "cat /proc/net/ip6_tables_names 2>/dev/null || true"

    #: The proc file this fact family reads (IPv6).
    PROC = "/proc/net/ip6_tables_names"

    #: The SimulatedHost state field this fact family reads (IPv6).
    SEAM = "ip6tables_tables"


class IptablesVersionInfo(TypedDict):
    """
    Attributes:
        version: The iptables release string, as printed after the ``v``
            (e.g. ``1.8.9``).
        backend: The packet-filtering backend the binary drives:
            ``nf_tables`` or ``legacy``. A release too old to print a
            parenthesized backend reads ``legacy`` (the nft backend
            arrived in 1.8).
    """

    # The ``IptablesVersion`` fact's return record, as a plain dict. The
    # backend matters because iptables-nft and iptables-legacy differ in
    # extension behaviour and cannot share rule state -- a deploy can
    # branch on it (gathered before the run) to pick the right tooling.

    version: str
    backend: str


class IptablesVersion:
    """The installed iptables release and its legacy-vs-nft backend.

    **Commands:**

    ```bash
    iptables --version
    ```

    **Returns:**

    ```python
    IptablesVersionInfo | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/iptables/iptables_iptables_version.py``.
    """

    # A reemote5 original: pyinfra has no version fact, yet the backend
    # question (``iptables v1.8.9 (nf_tables)`` vs ``(legacy)``) decides
    # whether a host's rules are even visible to nft tooling. The
    # ``--version`` probe needs no privileged login. Unparseable or
    # empty output reads as None; a host without the binary raises
    # ``CommandNotFoundError`` (exit 127, no guard).
    #
    # SimulatedHost seam note: the ``iptables_version`` field stores the
    # record dict; the empty-dict default projects to None -- a
    # simulated host whose version has not been seeded.

    #: Shell gathering the version banner over SSH.
    COMMAND = "iptables --version"

    #: The binary this fact family probes (the IPv6 sibling flips it).
    BINARY = "iptables"

    #: The SimulatedHost state field this fact family reads.
    SEAM = "iptables_version"

    #: The banner shape: ``iptables v<release>`` with an optional
    #: parenthesized backend word.
    _REGEX = re.compile(r"ip6?tables v(\S+)(?: \((\w+)\))?")

    @classmethod
    def parse(cls, output: Iterable[str]) -> IptablesVersionInfo | None:
        """Parse the ``--version`` banner into a version record. Pure."""
        match = cls._REGEX.search("\n".join(output))
        if match is None:
            return None
        return {
            "version": match.group(1),
            "backend": match.group(2) or "legacy",
        }

    @classmethod
    async def process(cls, host: Host) -> IptablesVersionInfo | None:
        state = getattr(host, cls.SEAM, None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly; the
            # empty dict models an unseeded (None-reading) host.
            return dict(state) if state else None  # type: ignore[return-value]
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(f"{cls.BINARY} --version")
        return cls.parse(stdout.splitlines())


class Ip6tablesVersion(IptablesVersion):
    """The installed ip6tables release and its legacy-vs-nft backend.

    **Commands:**

    ```bash
    ip6tables --version
    ```

    **Returns:**

    ```python
    IptablesVersionInfo | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/iptables/iptables_ip6tables_version.py``.
    """

    # The IPv6 sibling of ``IptablesVersion``: the banner regex already
    # matches both binaries, and only the probed binary and the
    # SimulatedHost seam field flip. The two families can be driven by
    # different backends on a misconfigured host, so each is probed
    # separately.

    #: Shell gathering the version banner over SSH (IPv6).
    COMMAND = "ip6tables --version"

    #: The binary this fact family probes (IPv6).
    BINARY = "ip6tables"

    #: The SimulatedHost state field this fact family reads (IPv6).
    SEAM = "ip6tables_version"


class IptablesModules:
    """The loaded ``xt_*`` iptables match/target kernel modules.

    **Commands:**

    ```bash
    lsmod | grep '^xt_' || true
    ```

    **Returns:**

    ```python
    list[str]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/iptables/iptables_iptables_modules.py``.
    """

    # A reemote5 original: which ``-m`` extensions are usable on a host
    # is decided by the loaded ``xt_*`` modules (``xt_conntrack``,
    # ``xt_multiport``, ``xt_set``, ...). ``lsmod`` is world-readable;
    # the grep narrows to the netfilter extension family (the ``nf_*``
    # core modules are deliberately out of scope -- the proposal asked
    # for match availability). Only the module-name column is kept, in
    # listing order. The ``|| true`` guard masks both an empty grep
    # (exit 1) and a missing lsmod, so only ``HostConnectionError`` can
    # surface. Loading a module is ``server.modprobe``'s job, not this
    # namespace's.
    #
    # SimulatedHost seam note: the ``iptables_modules`` field stores the
    # name list directly; the default [] models a host with no extension
    # modules loaded.

    #: Shell gathering the loaded extension module names over SSH.
    COMMAND = "lsmod | grep '^xt_' || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[str]:
        """Parse ``lsmod`` rows into module names, in listing order. Pure."""
        modules: list[str] = []
        for line in output:
            columns = line.split()
            if columns and columns[0].startswith("xt_"):
                modules.append(columns[0])
        return modules

    @classmethod
    async def process(cls, host: Host) -> list[str]:
        state = getattr(host, "iptables_modules", None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return list(state)
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class IpSet(TypedDict):
    """
    Attributes:
        type: The set's type (``hash:ip``, ``hash:net``, ``bitmap:port``,
            ...).
        revision: The kernel set-type revision number.
        header: The ``Header:`` line's creation parameters (``family
            inet hashsize 1024 maxelem 65536``), as printed.
        size_in_memory: The set's in-memory size in bytes.
        references: How many iptables rules reference the set.
        members: The set's entries, one string per member (``10.0.0.5``,
            ``192.168.0.0/24``, ``10.0.0.5,tcp:80``); per-member
            counters/timeout suffixes are dropped, and an empty set
            reads [].
    """

    # One ipset container (``IpSets``' entry), as a plain dict. The
    # member strings keep only the entry itself (the first whitespace
    # token of each Members: row) so ``ipset add``/``del`` arguments
    # compare equal to the listed membership regardless of the
    # timeout/counter options a row may carry.

    type: str
    revision: int
    header: str
    size_in_memory: int
    references: int
    members: list[str]


class IpSets:
    """The ipset containers, their types and their members.

    **Commands:**

    ```bash
    ipset list 2> /dev/null || sudo -n ipset list 2> /dev/null || true
    ```

    **Returns:**

    ```python
    dict[str, IpSet]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/iptables/iptables_ip_sets.py``.
    """

    # A reemote5 original (pyinfra has no ipset support at all): the
    # ``ipset list`` blocks -- ``Name:`` opens a set, the labeled fields
    # carry its metadata, and the rows after ``Members:`` are its
    # entries. Sets are the performance companion to ``-m set
    # --match-set`` rules: one hash lookup replaces thousands of linear
    # rules. ``ipset list`` needs CAP_NET_ADMIN, so the listing rides
    # the passwordless ``sudo -n`` retry (the module header's privilege
    # note); the ``|| true`` guard masks a missing ipset binary (exit
    # 127) and a login granted neither reads {}.
    #
    # SimulatedHost seam note: the ``ipsets`` field stores name ->
    # ``IpSet`` record; the create/destroy/entry mutators maintain it.

    #: Shell gathering every set's metadata and membership over SSH,
    #: with the passwordless ``sudo -n`` retry for the CAP_NET_ADMIN read.
    COMMAND = "ipset list 2> /dev/null || sudo -n ipset list 2> /dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, IpSet]:
        """Parse ``ipset list`` blocks into name -> set records. Pure."""
        sets: dict[str, IpSet] = {}
        current: IpSet | None = None
        in_members = False
        for line in output:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("Name:"):
                current = {
                    "type": "",
                    "revision": 0,
                    "header": "",
                    "size_in_memory": 0,
                    "references": 0,
                    "members": [],
                }
                sets[stripped.split(":", 1)[1].strip()] = current
                in_members = False
                continue
            if current is None:
                continue
            if stripped == "Members:":
                in_members = True
                continue
            if in_members:
                # Keep only the entry token; timeout/packets/bytes
                # suffixes are per-member options, not membership.
                current["members"].append(stripped.split()[0])
                continue
            label, _, value = stripped.partition(":")
            value = value.strip()
            if label == "Type":
                current["type"] = value
            elif label == "Revision":
                current["revision"] = int(value) if value.isdigit() else 0
            elif label == "Header":
                current["header"] = value
            elif label == "Size in memory":
                current["size_in_memory"] = int(value) if value.isdigit() else 0
            elif label == "References":
                current["references"] = int(value) if value.isdigit() else 0
        return sets

    @classmethod
    async def process(cls, host: Host) -> dict[str, IpSet]:
        state = getattr(host, "ipsets", None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly,
            # copying each record including its member list.
            return {
                name: {**info, "members": list(info["members"])}
                for name, info in state.items()
            }
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class IptablesPersistence:
    """Whether the live iptables rules match a saved snapshot file.

    **Commands:**

    ```bash
    sh -c 'live=$( (iptables-save 2>/dev/null || sudo -n iptables-save 2>/dev/null) | sed '"'"'/^#/d'"'"'); saved=$(sed '"'"'/^#/d'"'"' <path> 2>/dev/null || true); [ -n "$live" ] && [ "$live" = "$saved" ] && echo synced || echo drifted'
    ```

    **Returns:**

    ```python
    bool
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/iptables/iptables_iptables_persistence.py``.
    """

    # A reemote5 original answering the persistence-status proposal:
    # True means the on-disk snapshot (default
    # ``/etc/iptables/rules.v4``, the iptables-persistent location)
    # reproduces the live rules, so ``iptables.save`` can skip the
    # write. Comment lines are dropped on both sides before comparing
    # because the save header's timestamp changes every run; an absent
    # file, an empty live read (a login granted neither root nor
    # passwordless sudo) or any difference reads False. The probe is
    # read-only and fully exit-masked, so only ``HostConnectionError``
    # can surface. A meaningful True needs a root login or a NOPASSWD
    # sudoers rule (the module header's privilege note).
    #
    # SimulatedHost seam note: the ``iptables_persistence`` field stores
    # path -> synced bool; an unseeded path reads False (like an absent
    # file). The save/restore mutators set the path True, and every
    # rule/chain/flush mutation flips the stored paths back to False.

    #: Shell template comparing the live rules against ``<path>``.
    COMMAND = (
        "sh -c 'live=$( (iptables-save 2>/dev/null || sudo -n iptables-save "
        "2>/dev/null) | sed '/^#/d'); "
        "saved=$(sed '/^#/d' <path> 2>/dev/null || true); "
        '[ -n "$live" ] && [ "$live" = "$saved" ] && echo synced || echo drifted\''
    )

    #: The save binary this fact family compares with.
    BINARY = "iptables"

    #: The snapshot path used when the caller passes none.
    DEFAULT_PATH = "/etc/iptables/rules.v4"

    #: The SimulatedHost state field this fact family reads.
    SEAM = "iptables_persistence"

    @classmethod
    async def process(cls, host: Host, path: str | None = None) -> bool:
        path = cls.DEFAULT_PATH if path is None else path
        state = getattr(host, cls.SEAM, None)
        if state is not None:
            # SimulatedHost seam: an unseeded path reads False, like an
            # absent snapshot file.
            return bool(state.get(path, False))
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(_persistence_command(cls.BINARY, path))
        return stdout.strip() == "synced"


class Ip6tablesPersistence(IptablesPersistence):
    """Whether the live ip6tables rules match a saved snapshot file.

    **Commands:**

    ```bash
    sh -c 'live=$( (ip6tables-save 2>/dev/null || sudo -n ip6tables-save 2>/dev/null) | sed '"'"'/^#/d'"'"'); saved=$(sed '"'"'/^#/d'"'"' <path> 2>/dev/null || true); [ -n "$live" ] && [ "$live" = "$saved" ] && echo synced || echo drifted'
    ```

    **Returns:**

    ```python
    bool
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/iptables/iptables_ip6tables_persistence.py``.
    """

    # The IPv6 sibling of ``IptablesPersistence``: only the save binary,
    # the default snapshot path (``rules.v6``) and the SimulatedHost seam
    # field flip; the comment-stripping comparison is inherited.

    #: Shell template comparing the live rules against ``<path>`` (IPv6).
    COMMAND = (
        "sh -c 'live=$( (ip6tables-save 2>/dev/null || sudo -n ip6tables-save "
        "2>/dev/null) | sed '/^#/d'); "
        "saved=$(sed '/^#/d' <path> 2>/dev/null || true); "
        '[ -n "$live" ] && [ "$live" = "$saved" ] && echo synced || echo drifted\''
    )

    #: The save binary this fact family compares with (IPv6).
    BINARY = "ip6tables"

    #: The snapshot path used when the caller passes none (IPv6).
    DEFAULT_PATH = "/etc/iptables/rules.v6"

    #: The SimulatedHost state field this fact family reads (IPv6).
    SEAM = "ip6tables_persistence"


# --- Operations ----------------------------------------------------------------


# --- iptables.chain / iptables.policy -------------------------------------------


@dataclass(frozen=True)
class IptablesChainCommand(Command):
    """``iptables -t <table> -N|-X <chain>``: create or delete one chain.

    The descriptor ``iptables.chain`` yields for existence changes. The
    ``present`` flag toggles ``-N`` (new chain) vs ``-X`` (delete); a
    user chain created with ``-N`` has policy ``-`` until referenced.
    Chain and table are ``shlex``-quoted.
    """

    chain: str
    table: str = "filter"
    version: int = 4
    present: bool = True

    def shell(self) -> str:
        action = "-N" if self.present else "-X"
        return (
            f"{_binary(self.version)} -t {shlex.quote(self.table)} "
            f"{action} {shlex.quote(self.chain)}"
        )


@dataclass(frozen=True)
class IptablesPolicyCommand(Command):
    """``iptables -t <table> -P <chain> <policy>``: set one chain's policy.

    The descriptor both ``iptables.chain`` (its ``policy`` argument) and
    the standalone ``iptables.policy`` yield. Only built-in chains
    accept a policy on a real host; the kernel rejects ``-P`` on a user
    chain. Chain, table and the validated policy word are
    ``shlex``-quoted.
    """

    chain: str
    policy: str
    table: str = "filter"
    version: int = 4

    def shell(self) -> str:
        return (
            f"{_binary(self.version)} -t {shlex.quote(self.table)} "
            f"-P {shlex.quote(self.chain)} {shlex.quote(self.policy)}"
        )


async def chain(
    ctx: OpContext,
    chain: str,
    present: bool = True,
    table: str = "filter",
    policy: str | None = None,
    version: int = 4,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add, remove or update an iptables chain.

    Args:
        ctx (OpContext): Execution context for the operation.
        chain (str): The name of the chain to manage.
        present (bool, optional): Whether the chain should exist.
            Defaults to True.
        table (str, optional): The iptables table the chain belongs to.
            Defaults to "filter".
        policy (str | None, optional): The policy the chain should have
            (built-in chains only -- the kernel rejects ``-P`` on user
            chains). Defaults to None.
        version (int, optional): Whether to target iptables (4) or
            ip6tables (6). Defaults to 4.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated iptables chain commands.

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/iptables/iptables_chain.py``.
    """

    # Mirrors pyinfra's ``iptables.chain``: idempotent on the Chains
    # fact. present=False deletes an existing chain (-X) and no-ops an
    # absent one; present=True creates a missing chain (-N) and sets the
    # policy (-P) when one is requested and differs (or the chain is
    # being created). The no-op reasons are pyinfra's verbatim wording.
    # pyinfra does not validate that ``policy`` targets a built-in chain
    # (its docs say system chains only, its code does not enforce it) --
    # parity keeps that behaviour here; the standalone ``policy``
    # operation is the validated alternative. The commands need root:
    # flag the request ``sudo=True``. ``**kwargs`` absorbs pyinfra's
    # global arguments (``op()`` already strips ``name``/``sudo``/``su``).
    _binary(version)
    fact = IptablesChains if version == 4 else Ip6tablesChains
    chains = await ctx.facts.get(fact, table)
    reasons: list[str] = []

    if not present:
        if chain in chains:
            ctx.changed = True
            reasons.append(f"iptables chain {chain} will be deleted")
            yield IptablesChainCommand(chain=chain, table=table, version=version, present=False)
        else:
            reasons.append(f"iptables chain {chain} does not exist")
        ctx.reason = "; ".join(reasons)
        return

    if chain not in chains:
        ctx.changed = True
        reasons.append(f"iptables chain {chain} will be created")
        yield IptablesChainCommand(chain=chain, table=table, version=version, present=True)
    else:
        reasons.append(f"iptables chain {chain} exists")

    if policy and (chain not in chains or chains[chain] != policy):
        ctx.changed = True
        reasons.append(f"iptables chain {chain} policy will be set to {policy}")
        yield IptablesPolicyCommand(chain=chain, policy=policy, table=table, version=version)
    elif policy:
        reasons.append(f"iptables chain {chain} policy is already {policy}")

    ctx.reason = "; ".join(reasons)


async def policy(
    ctx: OpContext,
    chain: str,
    policy: str,
    table: str = "filter",
    version: int = 4,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Set the default policy of a built-in iptables chain.

    Args:
        ctx (OpContext): Execution context for the operation.
        chain (str): The built-in chain whose policy to set (``INPUT``,
            ``FORWARD``, ``OUTPUT``, ``PREROUTING``, ``POSTROUTING`` --
            which exist depends on the table).
        policy (str): The policy target: ``ACCEPT``, ``DROP``, ``QUEUE``
            or ``RETURN``.
        table (str, optional): The iptables table the chain belongs to.
            Defaults to "filter".
        version (int, optional): Whether to target iptables (4) or
            ip6tables (6). Defaults to 4.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated iptables policy commands.

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/iptables/iptables_policy.py``.
    """

    # A reemote5 original (the standalone-policy proposal): pyinfra
    # couples policy management to chain existence inside ``chain``;
    # this operation manages the policy alone, idempotent on the Chains
    # fact. Unlike ``chain``'s unvalidated ``policy`` argument it
    # validates the policy word against iptables' built-in targets
    # (ACCEPT/DROP/QUEUE/RETURN) -- a static check that never depends on
    # fact state, so an unprivileged (empty) fact read still plans the
    # change rather than raising. Chain *existence* is deliberately not
    # validated against the fact for the same reason; a -P on a missing
    # chain fails at apply time with CommandError. The command needs
    # root: flag the request ``sudo=True``.
    _binary(version)
    if policy not in ("ACCEPT", "DROP", "QUEUE", "RETURN"):
        raise ValueError(
            f"policy must be ACCEPT, DROP, QUEUE or RETURN (got {policy!r})"
        )
    fact = IptablesChains if version == 4 else Ip6tablesChains
    chains = await ctx.facts.get(fact, table)
    if chains.get(chain) != policy:
        ctx.changed = True
        ctx.reason = f"iptables chain {chain} policy will be set to {policy}"
        yield IptablesPolicyCommand(chain=chain, policy=policy, table=table, version=version)
    else:
        ctx.reason = f"iptables chain {chain} policy is already {policy}"


# --- iptables.rule ---------------------------------------------------------------


@dataclass(frozen=True)
class IptablesRuleCommand(Command):
    """``iptables -t <table> -A|-I|-D <chain> [n] <spec>``: one rule change.

    The descriptor ``iptables.rule`` yields. ``action`` is the iptables
    verb (``-A`` append, ``-I`` insert, ``-D`` delete); ``position`` is
    the 1-based line number an insert addresses (None appends at the
    top). ``spec`` carries the pre-rendered rule arguments -- matches,
    the ``extras`` text, the jump and the target options -- as a
    hashable token tuple, so the descriptor stays frozen and stable
    across phases. Chain and table are ``shlex``-quoted; the spec tokens
    were quoted where user-supplied when the operation built them.
    """

    action: str
    chain: str
    spec: tuple[str, ...]
    table: str = "filter"
    version: int = 4
    position: int | None = None

    def shell(self) -> str:
        parts = [
            _binary(self.version),
            "-t",
            shlex.quote(self.table),
            self.action,
            shlex.quote(self.chain),
        ]
        if self.action == "-I" and self.position is not None:
            parts.append(str(self.position))
        parts.extend(self.spec)
        return " ".join(parts)


async def rule(
    ctx: OpContext,
    chain: str,
    jump: str,
    present: bool = True,
    table: str = "filter",
    append: bool = True,
    version: int = 4,
    position: int | None = None,
    protocol: str | None = None,
    not_protocol: str | None = None,
    source: str | None = None,
    not_source: str | None = None,
    destination: str | None = None,
    not_destination: str | None = None,
    in_interface: str | None = None,
    not_in_interface: str | None = None,
    out_interface: str | None = None,
    not_out_interface: str | None = None,
    to_destination: str | None = None,
    to_source: str | None = None,
    to_ports: int | str | None = None,
    log_prefix: str | None = None,
    log_level: int | str | None = None,
    destination_port: int | None = None,
    source_port: int | None = None,
    destination_ports: tuple[int | str, ...] = (),
    source_ports: tuple[int | str, ...] = (),
    ctstate: tuple[str, ...] = (),
    limit: str | None = None,
    limit_burst: int | None = None,
    match_set: tuple[str, str] | None = None,
    comment: str | None = None,
    extras: str = "",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add or remove an iptables rule, with typed match extensions.

    Args:
        ctx (OpContext): Execution context for the operation.
        chain (str): The chain this rule should live in.
        jump (str): The target of the rule (``ACCEPT``, ``DROP``,
            ``LOG``, ``DNAT``, a user chain, ...).
        present (bool, optional): Whether the rule should exist.
            Defaults to True.
        table (str, optional): The iptables table this rule belongs to.
            Defaults to "filter".
        append (bool, optional): Whether to append (True) or insert at
            the top (False) when adding the rule. Defaults to True.
        version (int, optional): Whether to target iptables (4) or
            ip6tables (6). Defaults to 4.
        position (int | None, optional): The 1-based line number to
            insert a new rule at (overrides ``append``); an existing
            rule is never repositioned. Defaults to None.
        protocol (str | None, optional): Filter by protocol (``tcp``,
            ``udp``, ...; case-insensitive). Defaults to None.
        not_protocol (str | None, optional): Negated protocol filter.
            Defaults to None.
        source (str | None, optional): Filter by source address/CIDR.
            Defaults to None.
        not_source (str | None, optional): Negated source filter.
            Defaults to None.
        destination (str | None, optional): Filter by destination
            address/CIDR. Defaults to None.
        not_destination (str | None, optional): Negated destination
            filter. Defaults to None.
        in_interface (str | None, optional): Filter by incoming
            interface. Defaults to None.
        not_in_interface (str | None, optional): Negated incoming
            interface filter. Defaults to None.
        out_interface (str | None, optional): Filter by outgoing
            interface. Defaults to None.
        not_out_interface (str | None, optional): Negated outgoing
            interface filter. Defaults to None.
        to_destination (str | None, optional): Where to route when
            ``jump="DNAT"`` (requires ``table="nat"``). Defaults to
            None.
        to_source (str | None, optional): Where to route when
            ``jump="SNAT"`` (requires ``table="nat"``). Defaults to
            None.
        to_ports (int | str | None, optional): Where to route when
            ``jump="REDIRECT"`` (requires ``table="nat"``). Defaults to
            None.
        log_prefix (str | None, optional): Log prefix when
            ``jump="LOG"``. Defaults to None.
        log_level (int | str | None, optional): Syslog level when
            ``jump="LOG"`` (0-7 or a level name). Defaults to None.
        destination_port (int | None, optional): Destination port
            shortcut (requires ``protocol``). Defaults to None.
        source_port (int | None, optional): Source port shortcut
            (requires ``protocol``). Defaults to None.
        destination_ports (tuple[int | str, ...], optional): Multiport
            destination match, e.g. ``(80, 443)`` or ``("8000:9000",)``
            (requires ``protocol``). Defaults to ().
        source_ports (tuple[int | str, ...], optional): Multiport
            source match (requires ``protocol``). Defaults to ().
        ctstate (tuple[str, ...], optional): Conntrack state match, e.g.
            ``("ESTABLISHED", "RELATED")``. Defaults to ().
        limit (str | None, optional): Rate limit match, e.g.
            ``"50/sec"`` or ``"10/min"``. Defaults to None.
        limit_burst (int | None, optional): Burst allowance for
            ``limit`` (requires ``limit``). Defaults to None.
        match_set (tuple[str, str] | None, optional): An ipset match as
            ``(set_name, direction)`` where direction is ``src``,
            ``dst`` or a comma combination (e.g. ``("blacklist",
            "src")``); manage the set with ``iptables.ipset``. Defaults
            to None.
        comment (str | None, optional): Rule comment (the ``comment``
            match -- the reliable idempotency/auditing marker).
            Defaults to None.
        extras (str, optional): Raw iptables extension arguments for
            anything the typed parameters do not cover (e.g.
            ``"-m physdev --physdev-in eth0"``). Defaults to "".
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated iptables rule commands.

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/iptables/iptables_rule.py``.
    """

    # Mirrors pyinfra's ``iptables.rule``: the rule definition (named
    # matches + the sorted extras pair list) is compared against the
    # Rules fact; a missing wanted rule yields -A (or -I), an existing
    # unwanted rule yields -D, and the no-op reasons are pyinfra's
    # verbatim wording (including its "does not exists" grammar).
    #
    # Extensions over pyinfra (the typed-matcher decision): ``ctstate``,
    # ``destination_ports``/``source_ports`` (multiport), ``limit``/
    # ``limit_burst``, ``match_set`` and ``log_level`` are first-class
    # parameters folded into the same extras comparison pyinfra's
    # port/comment shortcuts use, so idempotence works unchanged --
    # iptables-save prints the extensions as ordinary flags and the
    # sorted pair list compares order-independently. ``log_level``
    # renders AFTER the jump (LOG target options must follow -j LOG)
    # but compares through extras, because the fact has no named
    # log_level key. ``position`` inserts a new rule at an explicit line
    # number; an existing rule at a different position is left alone
    # (the destructive ``exact`` reordering proposal was dropped).
    #
    # Deliberate deviations from pyinfra: validation errors raise
    # ValueError (reemote5's convention) with pyinfra's message text; a
    # bare IPv6 source/destination normalizes to /128 (pyinfra appends
    # /32 for both families, which never matches ip6tables-save output);
    # ``not_protocol`` is lowercased in the definition too (pyinfra
    # lowercases only the command, so an uppercase not_protocol never
    # compares equal to the fact -- an idempotence bug); user-supplied
    # spec values are shlex-quoted (safe tokens render unchanged, so
    # pyinfra's fixture commands still match verbatim).
    #
    # The commands need root: flag the request ``sudo=True``.
    # ``**kwargs`` absorbs pyinfra's global arguments (``op()`` already
    # strips ``name``/``sudo``/``su``).
    _binary(version)
    if isinstance(to_ports, int):
        to_ports = f"{to_ports}"

    # The typed matchers and pyinfra's shortcuts all extend the extras
    # string; the comparison parses it back into sorted pairs, exactly
    # as pyinfra does for destination_port/source_port/comment.
    extra_bits = extras
    if destination_port:
        extra_bits = f"{extra_bits} --dport {destination_port}"
    if source_port:
        extra_bits = f"{extra_bits} --sport {source_port}"
    if destination_ports:
        ports = ",".join(str(port) for port in destination_ports)
        extra_bits = f"{extra_bits} -m multiport --dports {ports}"
    if source_ports:
        ports = ",".join(str(port) for port in source_ports)
        extra_bits = f"{extra_bits} -m multiport --sports {ports}"
    if ctstate:
        extra_bits = f"{extra_bits} -m conntrack --ctstate {','.join(ctstate)}"
    if limit:
        extra_bits = f"{extra_bits} -m limit --limit {limit}"
    if limit_burst is not None:
        if not limit:
            raise ValueError("iptables limit_burst requires limit")
        extra_bits = f"{extra_bits} --limit-burst {limit_burst}"
    if match_set:
        extra_bits = f"{extra_bits} -m set --match-set {match_set[0]} {match_set[1]}"
    if comment:
        extra_bits = f"{extra_bits} -m comment --comment {shlex.quote(comment)}"

    extras_list = _parse_extras(shlex.split(extra_bits))

    # When protocol is set, iptables automagically adds the matching
    # extension module (which shows in iptables-save as ``-m tcp``).
    if protocol:
        protocol = protocol.lower()
        extras_list.append(["-m", protocol])
        extras_list.sort()
    elif destination_port or source_port or destination_ports or source_ports:
        # --dport/--sport and multiport need -m [tcp|udp|...], which
        # only -p supplies.
        raise ValueError(
            "iptables cannot filter by destination_port/source_port without a protocol"
        )

    # NAT target arguments are only valid on the nat table with their
    # matching jump (pyinfra's validation, message text verbatim).
    if to_destination and (table != "nat" or jump != "DNAT"):
        raise ValueError(
            "iptables only supports to_destination on the nat table and the DNAT jump "
            f"(table={table}, jump={jump})"
        )
    if to_source and (table != "nat" or jump != "SNAT"):
        raise ValueError(
            "iptables only supports to_source on the nat table and the SNAT jump "
            f"(table={table}, jump={jump})"
        )
    if to_ports and (table != "nat" or jump != "REDIRECT"):
        raise ValueError(
            "iptables only supports to_ports on the nat table and the REDIRECT jump "
            f"(table={table}, jump={jump})"
        )
    if log_prefix and jump != "LOG":
        raise ValueError(
            f"iptables only supports log_prefix with the LOG jump (jump={jump})"
        )
    if log_level is not None:
        if jump != "LOG":
            raise ValueError(
                f"iptables only supports log_level with the LOG jump (jump={jump})"
            )
        # The fact parses --log-level into extras (no named key), so the
        # comparison joins it there; the rendering places it after -j.
        extras_list.append(["--log-level", str(log_level)])
        extras_list.sort()
    if position is not None and position < 1:
        raise ValueError(f"position must be a positive rule number (got {position!r})")

    definition: dict[str, Any] = {
        "chain": chain,
        "jump": jump,
        "protocol": protocol,
        "source": source,
        "destination": destination,
        "in_interface": in_interface,
        "out_interface": out_interface,
        "not_protocol": not_protocol.lower() if not_protocol else None,
        "not_source": not_source,
        "not_destination": not_destination,
        "not_in_interface": not_in_interface,
        "not_out_interface": not_out_interface,
        # These go *after* the jump argument when rendered.
        "log_prefix": log_prefix,
        "to_destination": to_destination,
        "to_source": to_source,
        "to_ports": to_ports,
        "extras": sorted(extras_list),
    }
    definition = {
        key: (
            _normalize_host(value, version)
            if key in ("source", "not_source", "destination", "not_destination")
            else value
        )
        for key, value in definition.items()
        if value
    }

    fact = IptablesRules if version == 4 else Ip6tablesRules
    rules = await ctx.facts.get(fact, table)

    if present:
        if definition in rules:
            ctx.reason = f"iptables {chain} rule exists"
            return
        action = "-I" if position is not None else ("-A" if append else "-I")
        ctx.reason = f"iptables {chain} rule will be added"
    else:
        if definition not in rules:
            ctx.reason = f"iptables {chain} rule does not exists"
            return
        action = "-D"
        ctx.reason = f"iptables {chain} rule will be deleted"

    # Build the command spec in pyinfra's argument order: positive
    # matches, negated matches, the extras text, the jump, then the
    # target options.
    spec: list[str] = []
    if protocol:
        spec.extend(("-p", protocol))
    if source:
        spec.extend(("-s", shlex.quote(source)))
    if destination:
        spec.extend(("-d", shlex.quote(destination)))
    if in_interface:
        spec.extend(("-i", shlex.quote(in_interface)))
    if out_interface:
        spec.extend(("-o", shlex.quote(out_interface)))
    if not_protocol:
        spec.extend(("!", "-p", not_protocol.lower()))
    if not_source:
        spec.extend(("!", "-s", shlex.quote(not_source)))
    if not_destination:
        spec.extend(("!", "-d", shlex.quote(not_destination)))
    if not_in_interface:
        spec.extend(("!", "-i", shlex.quote(not_in_interface)))
    if not_out_interface:
        spec.extend(("!", "-o", shlex.quote(not_out_interface)))
    if extra_bits.strip():
        spec.append(extra_bits.strip())
    spec.extend(("-j", shlex.quote(jump)))
    if log_prefix:
        spec.extend(("--log-prefix", shlex.quote(log_prefix)))
    if log_level is not None:
        spec.extend(("--log-level", shlex.quote(str(log_level))))
    if to_destination:
        spec.extend(("--to-destination", shlex.quote(to_destination)))
    if to_source:
        spec.extend(("--to-source", shlex.quote(to_source)))
    if to_ports:
        spec.extend(("--to-ports", shlex.quote(to_ports)))

    ctx.changed = True
    yield IptablesRuleCommand(
        action=action,
        chain=chain,
        spec=tuple(spec),
        table=table,
        version=version,
        position=position if action == "-I" else None,
    )


# --- iptables.flush ---------------------------------------------------------------


@dataclass(frozen=True)
class IptablesFlushCommand(Command):
    """``iptables -t <table> -F [chain]``: flush rules from one chain or all.

    The descriptor ``iptables.flush`` yields. A ``chain`` of None omits
    the argument, flushing every chain of the table; chain policies and
    user-defined chains survive a flush (only ``-X``/``-P`` touch
    those). Chain and table are ``shlex``-quoted.
    """

    table: str = "filter"
    version: int = 4
    chain: str | None = None

    def shell(self) -> str:
        parts = [_binary(self.version), "-t", shlex.quote(self.table), "-F"]
        if self.chain is not None:
            parts.append(shlex.quote(self.chain))
        return " ".join(parts)


async def flush(
    ctx: OpContext,
    chain: str | None = None,
    table: str = "filter",
    version: int = 4,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Flush the rules of one chain, or of every chain in a table.

    Args:
        ctx (OpContext): Execution context for the operation.
        chain (str | None, optional): The chain to flush; None flushes
            every chain of the table. Defaults to None.
        table (str, optional): The iptables table to flush. Defaults to
            "filter".
        version (int, optional): Whether to target iptables (4) or
            ip6tables (6). Defaults to 4.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated iptables flush commands.

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/iptables/iptables_flush.py``.
    """

    # A reemote5 original (the flush proposal): idempotent on the Rules
    # fact -- a chain (or table) that already holds no rules is a no-op,
    # so a flush never shows changed twice. Only rules are flushed:
    # chain policies survive (``iptables.policy`` manages those) and
    # user-defined chains survive (``iptables.chain`` deletes those).
    # Destructive on a live firewall: the example is compile-checked,
    # not executed against the shared demo hosts. The command needs
    # root: flag the request ``sudo=True``.
    _binary(version)
    fact = IptablesRules if version == 4 else Ip6tablesRules
    rules = await ctx.facts.get(fact, table)
    scope = f"chain {chain}" if chain is not None else f"table {table}"
    relevant = [r for r in rules if chain is None or r.get("chain") == chain]
    if relevant:
        ctx.changed = True
        ctx.reason = f"iptables {scope} holds {len(relevant)} rule(s), will flush"
        yield IptablesFlushCommand(table=table, version=version, chain=chain)
    else:
        ctx.reason = f"iptables {scope} holds no rules"


# --- iptables.zero ----------------------------------------------------------------


@dataclass(frozen=True)
class IptablesZeroCommand(Command):
    """``iptables -t <table> -Z [chain]``: zero counters in one chain or all.

    The descriptor ``iptables.zero`` yields. A ``chain`` of None omits
    the argument, zeroing every chain of the table. Chain and table are
    ``shlex``-quoted.
    """

    table: str = "filter"
    version: int = 4
    chain: str | None = None

    def shell(self) -> str:
        parts = [_binary(self.version), "-t", shlex.quote(self.table), "-Z"]
        if self.chain is not None:
            parts.append(shlex.quote(self.chain))
        return " ".join(parts)


async def zero(
    ctx: OpContext,
    chain: str | None = None,
    table: str = "filter",
    version: int = 4,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Zero the packet and byte counters of one chain, or of a whole table.

    Args:
        ctx (OpContext): Execution context for the operation.
        chain (str | None, optional): The chain whose counters to zero;
            None zeroes every chain of the table. Defaults to None.
        table (str, optional): The iptables table to zero. Defaults to
            "filter".
        version (int, optional): Whether to target iptables (4) or
            ip6tables (6). Defaults to 4.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The iptables zero command, always (the operation is
        stateless).

    **Raises:**

    ```python
    ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/iptables/iptables_zero.py``.
    """

    # A reemote5 original (the zero_counters proposal), deliberately
    # STATELESS like selinux.restorecon: counters grow with every
    # packet, so any fact comparison is stale before the command runs
    # and "already zero" is never a stable state on a live firewall.
    # The audit workflow is: read IptablesRulesOrdered, then zero, then
    # read again. Zeroing does not change the rule set, so the
    # Persistence facts (which compare rules, not counters --
    # iptables-save without -c omits them) stay synced across a zero.
    # The command needs root: flag the request ``sudo=True``.
    _binary(version)
    ctx.changed = True
    scope = f"chain {chain}" if chain is not None else f"table {table}"
    ctx.reason = f"iptables {scope} counters will be zeroed"
    yield IptablesZeroCommand(table=table, version=version, chain=chain)


# --- iptables.save ----------------------------------------------------------------


@dataclass(frozen=True)
class IptablesSaveCommand(Command):
    """``sh -c 'iptables-save > <path>'``: snapshot the live rules to a file.

    The descriptor ``iptables.save`` yields. The ``>`` redirect is
    wrapped in ``sh -c`` so the whole write runs elevated under
    ``sudo sh -c '...'`` -- a bare ``sudo iptables-save > /etc/...``
    would open the redirect as the login user (the DnfRepoCommand
    precedent). The path is ``shlex``-quoted inside the wrapper.
    """

    path: str
    version: int = 4

    def shell(self) -> str:
        inner = f"{_binary(self.version)}-save > {shlex.quote(self.path)}"
        return f"sh -c {shlex.quote(inner)}"


async def save(
    ctx: OpContext,
    path: str | None = None,
    version: int = 4,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Persist the live iptables rules to a snapshot file on the host.

    Args:
        ctx (OpContext): Execution context for the operation.
        path (str | None, optional): The host-side file to write;
            None picks ``/etc/iptables/rules.v4`` (version 4) or
            ``/etc/iptables/rules.v6`` (version 6), the
            iptables-persistent locations. Defaults to None.
        version (int, optional): Whether to save iptables (4) or
            ip6tables (6) rules. Defaults to 4.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated iptables-save commands.

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/iptables/iptables_save.py``.
    """

    # A reemote5 original (the save/persist proposal): idempotent on the
    # Persistence fact -- when the snapshot already reproduces the live
    # rules (comment lines ignored, the save header's timestamp changes
    # every run) the write is skipped. The path is HOST-side; reemote5
    # transfers are IO-only, so uploading a snapshot from the controller
    # is files.put's job, not an option here. Distro auto-detection of
    # the persistence location (the Proposal's aside) is deliberately
    # dropped: the iptables-persistent default is explicit and a
    # deploy can pass any path. Writing under /etc needs root: flag the
    # request ``sudo=True`` (the descriptor wraps the redirect in
    # sh -c so the elevation covers the write). Reloading the snapshot
    # at boot is the distro's iptables-persistent/netfilter-persistent
    # service, not this operation's job.
    _binary(version)
    fact = IptablesPersistence if version == 4 else Ip6tablesPersistence
    path = fact.DEFAULT_PATH if path is None else path
    synced = await ctx.facts.get(fact, path)
    if not synced:
        ctx.changed = True
        ctx.reason = f"iptables rules drifted from {path}, will save"
        yield IptablesSaveCommand(path=path, version=version)
    else:
        ctx.reason = f"iptables rules already match {path}"


# --- iptables.restore ---------------------------------------------------------------


@dataclass(frozen=True)
class IptablesRestoreCommand(Command):
    """``sh -c 'iptables-restore [-n] < <path>'``: load rules from a file.

    The descriptor ``iptables.restore`` yields. ``noflush`` adds ``-n``
    (append the file's rules without flushing the existing ones). The
    ``<`` redirect is wrapped in ``sh -c`` so the read runs elevated
    under ``sudo sh -c '...'`` even for a root-only snapshot file. The
    path is ``shlex``-quoted inside the wrapper.
    """

    path: str
    version: int = 4
    noflush: bool = False

    def shell(self) -> str:
        flag = "-n " if self.noflush else ""
        inner = f"{_binary(self.version)}-restore {flag}< {shlex.quote(self.path)}"
        return f"sh -c {shlex.quote(inner)}"


async def restore(
    ctx: OpContext,
    path: str,
    version: int = 4,
    noflush: bool = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Atomically load iptables rules from a snapshot file on the host.

    Args:
        ctx (OpContext): Execution context for the operation.
        path (str): The host-side iptables-restore format file to load
            (upload it with ``files.put`` first when it lives on the
            controller).
        version (int, optional): Whether to restore iptables (4) or
            ip6tables (6) rules. Defaults to 4.
        noflush (bool, optional): Whether to append the file's rules
            without flushing the existing ones (``iptables-restore
            -n``). Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated iptables-restore commands.

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/iptables/iptables_restore.py``.
    """

    # A reemote5 original (the restore/bulk_rule proposal): the atomic
    # bulk-apply surface -- iptables-restore applies the whole file or
    # fails without partial state, which is why the hashability-blocked
    # bulk_rule(rules=[...]) proposal resolves to this operation plus a
    # files.put upload. Idempotent on the Persistence fact: when the
    # file already reproduces the live rules, restoring it changes
    # nothing and is skipped (sound for noflush too -- a merge of
    # already-present rules is a no-op). The path is HOST-side
    # (IO-only transfers: no controller path strings). Destructive on a
    # live firewall with noflush=False: the example is compile-checked,
    # not executed against the shared demo hosts. The command needs
    # root: flag the request ``sudo=True``.
    _binary(version)
    fact = IptablesPersistence if version == 4 else Ip6tablesPersistence
    synced = await ctx.facts.get(fact, path)
    if not synced:
        ctx.changed = True
        ctx.reason = f"iptables rules drifted from {path}, will restore"
        yield IptablesRestoreCommand(path=path, version=version, noflush=noflush)
    else:
        ctx.reason = f"iptables rules already match {path}"


# --- iptables.ipset -----------------------------------------------------------------


@dataclass(frozen=True)
class IpsetCreateCommand(Command):
    """``ipset create <name> <type> -exist``: ensure one set exists.

    The descriptor ``iptables.ipset`` yields for a missing set. The
    ``-exist`` flag makes a concurrent re-create a no-op rather than an
    error (the fact read may be stale, or unprivileged and empty). Set
    name and type are ``shlex``-quoted.
    """

    set_name: str
    set_type: str = "hash:ip"

    def shell(self) -> str:
        return f"ipset create {shlex.quote(self.set_name)} {shlex.quote(self.set_type)} -exist"


@dataclass(frozen=True)
class IpsetDestroyCommand(Command):
    """``ipset destroy <name>``: delete one set.

    The descriptor ``iptables.ipset`` yields for ``present=False`` with
    no entries. A set still referenced by a rule cannot be destroyed;
    the kernel rejects it and the apply fails with ``CommandError``.
    """

    set_name: str

    def shell(self) -> str:
        return f"ipset destroy {shlex.quote(self.set_name)}"


@dataclass(frozen=True)
class IpsetEntryCommand(Command):
    """``ipset add|del <name> <entry>``: one membership change.

    The descriptor ``iptables.ipset`` yields per differing entry. Adds
    carry ``-exist`` so a stale fact read cannot turn a re-add into an
    error; deletes carry no tolerance flag (the operation only yields
    them for entries the fact saw). Set name and entry are
    ``shlex``-quoted.
    """

    set_name: str
    entry: str
    present: bool = True

    def shell(self) -> str:
        if self.present:
            return f"ipset add {shlex.quote(self.set_name)} {shlex.quote(self.entry)} -exist"
        return f"ipset del {shlex.quote(self.set_name)} {shlex.quote(self.entry)}"


async def ipset(
    ctx: OpContext,
    set_name: str,
    set_type: str = "hash:ip",
    entries: tuple[str, ...] = (),
    present: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Manage an ipset container and its members.

    Args:
        ctx (OpContext): Execution context for the operation.
        set_name (str): The name of the set to manage.
        set_type (str, optional): The set type used when creating it
            (``hash:ip``, ``hash:net``, ``hash:ip,port``, ...). An
            existing set of a different type raises ``ValueError``
            rather than silently recreating it. Defaults to "hash:ip".
        entries (tuple[str, ...], optional): The member entries to add
            (``present=True``) or remove (``present=False``); with
            ``present=False`` and no entries the whole set is
            destroyed. Membership is additive -- entries not listed
            are left alone. Defaults to ().
        present (bool, optional): Whether the set (and the listed
            entries) should exist. Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated ipset commands.

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/iptables/iptables_ipset.py``.
    """

    # A reemote5 original (the ipset proposal -- pyinfra has no ipset
    # support): idempotent on the IpSets fact. present=True creates a
    # missing set with the requested type and adds the missing entries
    # (sorted, so plan and apply report identically); an existing set
    # of a different type raises ValueError -- recreating it would
    # silently drop members and break referencing rules. present=False
    # removes the listed entries (the set survives) or destroys the
    # whole set when no entries are given. The parameter is ``set_name``
    # because ``op()`` consumes ``name`` as the plan-label
    # meta-parameter. Adds ride ``-exist`` so a stale or unprivileged
    # (empty) fact read cannot turn a re-add into an apply error. The
    # commands need root: flag the request ``sudo=True``.
    sets = await ctx.facts.get(IpSets)
    reasons: list[str] = []

    if not present:
        if entries:
            members = set(sets.get(set_name, {}).get("members", []))
            removing = sorted(set(entries) & members)
            if removing:
                ctx.changed = True
                reasons.append(f"ipset {set_name} entries will be removed: {', '.join(removing)}")
                for entry in removing:
                    yield IpsetEntryCommand(set_name=set_name, entry=entry, present=False)
            else:
                reasons.append(f"ipset {set_name} holds none of the listed entries")
        elif set_name in sets:
            ctx.changed = True
            reasons.append(f"ipset {set_name} will be destroyed")
            yield IpsetDestroyCommand(set_name=set_name)
        else:
            reasons.append(f"ipset {set_name} does not exist")
        ctx.reason = "; ".join(reasons)
        return

    if set_name not in sets:
        ctx.changed = True
        reasons.append(f"ipset {set_name} will be created ({set_type})")
        yield IpsetCreateCommand(set_name=set_name, set_type=set_type)
        members = set()
    else:
        current = sets[set_name]
        if current["type"] != set_type:
            raise ValueError(
                f"ipset {set_name} exists with type {current['type']!r}, "
                f"not {set_type!r}; destroy it first to change its type"
            )
        reasons.append(f"ipset {set_name} exists")
        members = set(current["members"])

    adding = sorted(set(entries) - members)
    if adding:
        ctx.changed = True
        reasons.append(f"ipset {set_name} entries will be added: {', '.join(adding)}")
        for entry in adding:
            yield IpsetEntryCommand(set_name=set_name, entry=entry, present=True)
    elif entries:
        reasons.append(f"ipset {set_name} entries already present")

    ctx.reason = "; ".join(reasons)


__all__ = [
    "Ip6tablesChains",
    "Ip6tablesPersistence",
    "Ip6tablesRules",
    "Ip6tablesRulesOrdered",
    "Ip6tablesTables",
    "Ip6tablesVersion",
    "IpSet",
    "IpSets",
    "IpsetCreateCommand",
    "IpsetDestroyCommand",
    "IpsetEntryCommand",
    "IptablesChainCommand",
    "IptablesChains",
    "IptablesFlushCommand",
    "IptablesModules",
    "IptablesOrderedRule",
    "IptablesPersistence",
    "IptablesPolicyCommand",
    "IptablesRestoreCommand",
    "IptablesRule",
    "IptablesRuleCommand",
    "IptablesRules",
    "IptablesRulesOrdered",
    "IptablesSaveCommand",
    "IptablesTables",
    "IptablesVersion",
    "IptablesVersionInfo",
    "IptablesZeroCommand",
    "chain",
    "flush",
    "ipset",
    "policy",
    "restore",
    "rule",
    "save",
    "zero",
]