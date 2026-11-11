# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``ufw`` module: facts and operations in one source.
#
# Everything here is a reemote original (the comprehensive-ufw
# proposal, answered with the developer's locked decisions). Fact side:
# ``UfwStatus`` (active flag, logging level and the three default policies
# from ``ufw status verbose`` -- the standalone
# ``DefaultPolicy``/``defaults`` fact proposals are folded into this one
# record, the iptables module's IptablesDefaultPolicies precedent),
# ``UfwRules`` (the numbered ruleset from ``ufw status numbered``, parsed
# into structured ``UfwRule`` entries with port/protocol/app/address
# decomposition, v6 markers, interfaces, log flags and comments) and
# ``UfwApplications`` (the installed application profiles from
# ``ufw app list``). Like every fact they are phase-unaware: their
# ``process`` coroutine just gathers the raw value.
#
# Privilege note: ``ufw status`` needs root on the target. Following the
# hardware.SmbiosInfo / iptables precedent, every fact retries its command
# through passwordless ``sudo -n`` at the shell level (never the elevation
# layer -- facts stay side-effect free and prompt-free): the contract is
# full data for a root login or a NOPASSWD sudoers rule, and the empty
# default otherwise, indistinguishable from an inactive/uninstalled ufw.
# The operations below flag their commands ``sudo=True`` at the ``op()``
# call site; their fact reads ride the same ``sudo -n`` retry, so
# idempotence reporting works on a sudoers fleet.
#
# Operation side: ``ufw.state`` (enable/disable, idempotent on UfwStatus;
# the Discussion's separate enable/disable pair collapsed into one
# bidirectional operation), ``ufw.default_policy`` (incoming/outgoing/
# routed fallback policy, idempotent on UfwStatus), ``ufw.rule`` (the core
# add/delete surface -- ports, multiports, ranges, application profiles
# via ``app``, source/destination restrictions, interfaces, logging,
# comments, route rules and positional inserts, idempotent on UfwRules),
# ``ufw.logging`` (off/on/low/medium/high/full, idempotent on UfwStatus)
# and ``ufw.reset`` (stateless destructive reset to installation
# defaults).
#
# Deliberately absent (decided with the developer): the Proposal's
# ``ipv6`` toggle on the state operation -- ufw has no CLI for it (IPv6
# support is IPV6=yes in /etc/default/ufw, a files.edit recipe's job), and
# ufw manages both families together, so the rule operation needs no
# ``version`` argument either: an address-less rule is matched against its
# IPv4 row and ufw maintains the ``(v6)`` twin itself, while a rule with an
# IPv6 address is v6-only. A separate ``ufw.application`` operation -- the
# ``app`` parameter on ``rule`` covers profiles in one idempotence path.
# A standalone defaults fact -- UfwStatus already answers it. Bulk rule
# lists -- ``op()`` arguments must be hashable, so ``port`` accepts a
# tuple (rendered as one ufw multiport), not a list of rule dicts. A
# ``reload`` operation -- ufw applies every mutation immediately; reloading
# the backend is Shell-action territory. Route rules expose only the
# incoming interface (``interface``); the outgoing-interface and
# ``log-all`` variants stay raw-shell territory.
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

    The ``hardware.SmbiosInfo`` / iptables precedent for facts that need
    root: try unprivileged first, retry through ``sudo -n`` (never
    prompts, creates no askpass file, safe under the across-host fan-out),
    and mask every failure with ``|| true`` so a host that grants neither
    reads the fact's empty default instead of raising.
    """
    return f"{command} 2> /dev/null || sudo -n {command} 2> /dev/null || true"


# A port token as ufw prints it: a single port, a comma multiport or a
# colon range (``22``, ``80,443``, ``8000:8010``).
_PORT_PATTERN = re.compile(r"\d+(?:[,:]\d+)*")

# An address/CIDR token: hex digits, dots and colons with an optional
# prefix length. The digit/colon requirement keeps hex-letter words
# (``cafe``) from reading as addresses.
_ADDR_PATTERN = re.compile(r"[0-9A-Fa-f:.]+(?:/\d+)?")

# The numbered-row skeleton: ``[ N] <To> <ACTION> <DIR> <From>`` with the
# action words as the anchor (the To column can overflow its 26-character
# padding, so fixed-width slicing would misparse long targets).
_RULE_LINE_PATTERN = re.compile(r"^\[\s*(\d+)\]\s+(.*)$")
_ACTION_PATTERN = re.compile(r"\s+(ALLOW|DENY|REJECT|LIMIT)\s+(IN|OUT|FWD)\s+")

# The trailing attribute group ufw appends after the From column: the log
# flag and (in numbered output) a redundant ``out`` marker. ``(v6)`` is
# deliberately not matched here -- it belongs to the location text.
_ATTRIB_PATTERN = re.compile(r"\s+\((log(?:-all)?(?:, out)?|out)\)$")


def _looks_like_address(token: str) -> bool:
    """Whether a location token is an address/CIDR rather than a word."""
    return bool(_ADDR_PATTERN.fullmatch(token)) and any(
        char in token for char in "0123456789:"
    )


def _parse_location(text: str) -> dict[str, str | bool]:
    """Decompose one To/From column into its parts. Pure and testable.

    ufw builds each column as ``[address] [port|app][/protocol] [(v6)]
    [on interface]`` (the backend_iptables ``get_status`` format): the
    interface suffix comes last, then the v6 marker, then the protocol
    suffix. An ``Anywhere/tcp`` column is a protocol-only rule -- the
    ``/proto`` suffix is stripped before the address/port split, while a
    CIDR's ``/24`` (all digits) stays part of the address.
    """
    text = text.strip()
    interface = ""
    match = re.search(r" on (\S+)$", text)
    if match:
        interface = match.group(1)
        text = text[: match.start()]
    v6 = text.endswith(" (v6)")
    if v6:
        text = text[: -len(" (v6)")]
    protocol = ""
    head, slash, suffix = text.rpartition("/")
    if slash and suffix and not suffix.isdigit():
        protocol = suffix
        text = head
    tokens = text.split()
    port = ""
    if tokens and _PORT_PATTERN.fullmatch(tokens[-1]):
        port = tokens.pop()
    address = ""
    if tokens and (tokens[0] == "Anywhere" or _looks_like_address(tokens[0])):
        address = tokens.pop(0)
    app = " ".join(tokens)
    return {
        "address": address or "Anywhere",
        "port": port,
        "protocol": protocol,
        "app": app,
        "v6": v6,
        "interface": interface,
    }


def _parse_rule_line(line: str) -> "UfwRule | None":
    """Parse one ``ufw status numbered`` row into a ``UfwRule``. Pure.

    Returns None for every non-rule line (the status header, the
    To/Action/From table header, blank lines). The comment is split off
    first (``" # "``), then the action words anchor the column split, then
    the trailing ``(log)``/ ``(log-all)``/ ``(out)`` attribute group is
    stripped from the From column before both locations are decomposed.
    ``log-all`` reads as ``log=True`` -- the fact records that the rule
    logs, not which log type (the rule operation only sets ``log``).
    """
    match = _RULE_LINE_PATTERN.match(line)
    if not match:
        return None
    number = int(match.group(1))
    rest = match.group(2)
    comment = ""
    if " # " in rest:
        rest, _, comment = rest.partition(" # ")
    action_match = _ACTION_PATTERN.search(rest)
    if not action_match:
        return None
    to_text = rest[: action_match.start()]
    from_text = rest[action_match.end() :]
    log = False
    attrib = _ATTRIB_PATTERN.search(from_text)
    if attrib:
        log = "log" in attrib.group(1)
        from_text = from_text[: attrib.start()]
    to = _parse_location(to_text)
    source = _parse_location(from_text)
    return UfwRule(
        number=number,
        action=action_match.group(1),
        direction=action_match.group(2),
        log=log,
        v6=bool(to["v6"] or source["v6"]),
        to_addr=str(to["address"]),
        port=str(to["port"]),
        protocol=str(to["protocol"] or source["protocol"]),
        app=str(to["app"]),
        from_addr=str(source["address"]),
        interface=str(to["interface"] or source["interface"]),
        comment=comment.strip(),
    )


# The comparison projection: every identity field of a rule except its
# position (``number``) and address family (``v6``), which the operation
# checks separately. Shared by the rule operation and the SimulatedHost
# delete mutator so both match rules exactly the same way.
_KEY_FIELDS = (
    "action",
    "direction",
    "log",
    "to_addr",
    "port",
    "protocol",
    "app",
    "from_addr",
    "interface",
    "comment",
)


def _rule_key(rule: "UfwRule") -> dict[str, Any]:
    """Project a parsed rule onto its identity fields. Pure."""
    return {field: rule[field] for field in _KEY_FIELDS}  # type: ignore[literal-required]


def _copy_rule(rule: "UfwRule") -> "UfwRule":
    """Copy one rule entry (flat str/int/bool values)."""
    return dict(rule)  # type: ignore[return-value]


def _normalize_port(port: int | str | tuple[int | str, ...] | None) -> str:
    """Render a port argument as ufw's port token ("" when absent).

    A tuple becomes one comma multiport (``(80, 443)`` -> ``"80,443"``);
    a range is the colon string form (``"8000:8010"``). ufw prints the
    token back verbatim, so no sorting or expansion happens here.
    """
    if port is None:
        return ""
    if isinstance(port, tuple):
        return ",".join(str(item) for item in port)
    return str(port)


def _normalize_addr(value: str | None) -> str:
    """Normalize an address argument to the column form ufw prints.

    None/``any``/``0.0.0.0/0``/``::/0`` all read as ``Anywhere`` -- the
    any-address column text -- so a desired rule compares equal to the
    parsed row regardless of which spelling the deploy used.
    """
    if value is None or value in ("any", "Anywhere", "0.0.0.0/0", "::/0"):
        return "Anywhere"
    return value


def _is_v6(from_ip: str | None, to_ip: str | None) -> bool:
    """Whether a rule spec addresses IPv6 (a colon in either address)."""
    return ":" in (from_ip or "") or ":" in (to_ip or "")


# --- Facts -------------------------------------------------------------------


class UfwStatusInfo(TypedDict):
    """
    Attributes:
        active: Whether ``ufw status verbose`` reported ``Status:
            active``; False for an inactive firewall, a missing binary
            or an unprivileged read.
        logging: The logging level word (``off``, ``low``, ``medium``,
            ``high``, ``full`` or ``unknown`` when ufw cannot read its
            loglevel); ``""`` when inactive or unreadable.
        default_incoming: The default incoming policy (``deny``,
            ``allow`` or ``reject``); ``""`` when inactive or
            unreadable.
        default_outgoing: The default outgoing policy; ``""`` when
            inactive or unreadable.
        default_routed: The default routed (forwarded) policy
            (``disabled``, ``deny``, ``allow`` or ``reject``); ``""``
            when inactive or unreadable.
    """

    # The single record ``UfwStatus`` returns. An inactive ufw prints
    # only ``Status: inactive`` -- every other field keeps its empty
    # default, which is also the unprivileged/uninstalled answer (the
    # module header's privilege contract).

    active: bool
    logging: str
    default_incoming: str
    default_outgoing: str
    default_routed: str


class UfwStatus:
    """Whether ufw is active, its logging level and its default policies.

    **Commands:**

    ```bash
    ufw status verbose 2> /dev/null || sudo -n ufw status verbose 2> /dev/null || true
    ```

    **Returns:**

    ```python
    UfwStatusInfo
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/ufw/ufw_ufw_status.py``.
    """

    # A reemote original (the Status/DefaultPolicy/defaults proposals
    # answered by one record): the ``Status:``, ``Logging:`` and
    # ``Default:`` lines of ``ufw status verbose`` parse into the active
    # flag, the level word (``on (low)`` reduces to ``low``) and the three
    # policy words. The ``New profiles:`` line and the rule table the
    # verbose listing also prints are ignored -- ``UfwRules`` answers the
    # ruleset with rule numbers. The command rides the passwordless
    # ``sudo -n`` retry and the ``|| true`` guard masks exit 127, so only
    # HostConnectionError can surface.
    #
    # SimulatedHost seam note: the ``ufw_status`` field stores the whole
    # record; an unseeded host reads the inactive/empty record.

    #: Shell gathering the status over SSH, with the passwordless
    #: ``sudo -n`` retry for the root read.
    COMMAND = (
        "ufw status verbose 2> /dev/null "
        "|| sudo -n ufw status verbose 2> /dev/null || true"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> UfwStatusInfo:
        """Parse the verbose status header lines into the record. Pure."""
        info = UfwStatusInfo(
            active=False,
            logging="",
            default_incoming="",
            default_outgoing="",
            default_routed="",
        )
        for line in output:
            if line.startswith("Status:"):
                info["active"] = line.partition(":")[2].strip() == "active"
            elif line.startswith("Logging:"):
                value = line.partition(":")[2].strip()
                # "on (low)" -> "low"; "off"/"unknown" stay verbatim.
                match = re.match(r"on \((\w+)\)", value)
                info["logging"] = match.group(1) if match else value
            elif line.startswith("Default:"):
                for policy, direction in re.findall(
                    r"(\w+) \((incoming|outgoing|routed)\)", line
                ):
                    info[f"default_{direction}"] = policy  # type: ignore[literal-required]
        return info

    @classmethod
    async def process(cls, host: Host) -> UfwStatusInfo:
        state = getattr(host, "ufw_status", None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return dict(state)  # type: ignore[return-value]
        # Real transport: gather over asyncssh from the host's connection,
        # retrying the root read through passwordless sudo -n.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class UfwRule(TypedDict):
    """
    Attributes:
        number: The 1-based rule number (the ``[ N]`` column of ``ufw
            status numbered``).
        action: The verdict word (``ALLOW``, ``DENY``, ``REJECT`` or
            ``LIMIT``).
        direction: The direction word (``IN``, ``OUT`` or ``FWD`` for a
            route rule).
        log: Whether the rule logs (the trailing ``(log)``/``(log-all)``
            attribute).
        v6: Whether the row is the IPv6 family member (the ``(v6)``
            marker on either column).
        to_addr: The destination address/CIDR (``Anywhere`` when the
            rule targets any address).
        port: The destination port token (``22``, ``80,443``,
            ``8000:8010``); ``""`` when the rule names no port.
        protocol: The protocol suffix (``tcp``/``udp``); ``""`` when the
            rule covers both.
        app: The application profile name (``Nginx Full``); ``""`` when
            the rule is not profile-based.
        from_addr: The source address/CIDR (``Anywhere`` when the rule
            accepts any source).
        interface: The bound interface (the ``on <iface>`` suffix on
            either column); ``""`` when unbound.
        comment: The rule comment (the trailing ``# ...`` text); ``""``
            when uncommented.
    """

    # One parsed numbered row (``UfwRules``' entry). Every field is
    # always present with an empty default so entries stay uniformly
    # shaped and JSON-serializable. The To column is decomposed into
    # to_addr/port/protocol/app; the From column contributes from_addr
    # (a source *port* restriction is not modelled -- the rule operation
    # never sets one, and ufw prints the protocol suffix on both columns
    # for protocol-only rules, which the parse folds into ``protocol``).

    number: int
    action: str
    direction: str
    log: bool
    v6: bool
    to_addr: str
    port: str
    protocol: str
    app: str
    from_addr: str
    interface: str
    comment: str


class UfwRules:
    """The ufw ruleset as numbered, structured rule entries.

    **Commands:**

    ```bash
    ufw status numbered 2> /dev/null || sudo -n ufw status numbered 2> /dev/null || true
    ```

    **Returns:**

    ```python
    list[UfwRule]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/ufw/ufw_ufw_rules.py``.
    """

    # A reemote original (the Rules proposal): every ``[ N]`` row of
    # ``ufw status numbered`` parses through ``_parse_rule_line`` into a
    # ``UfwRule``, in listing order (v4 rows first, then their v6 twins --
    # ufw's own numbering). The numbered form is used rather than the
    # verbose one because it keeps application profiles collapsed to one
    # row per rule and carries the numbers deletes and inserts address.
    # An inactive firewall prints only ``Status: inactive`` and reads [].
    # The command rides the passwordless ``sudo -n`` retry and the
    # ``|| true`` guard masks exit 127.
    #
    # SimulatedHost seam note: the ``ufw_rules`` field stores the entry
    # list in numbered order; an unseeded host reads [].

    #: Shell gathering the numbered ruleset over SSH, with the
    #: passwordless ``sudo -n`` retry for the root read.
    COMMAND = (
        "ufw status numbered 2> /dev/null "
        "|| sudo -n ufw status numbered 2> /dev/null || true"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[UfwRule]:
        """Parse numbered status rows into rule entries. Pure and total."""
        rules = []
        for line in output:
            rule = _parse_rule_line(line)
            if rule is not None:
                rules.append(rule)
        return rules

    @classmethod
    async def process(cls, host: Host) -> list[UfwRule]:
        state = getattr(host, "ufw_rules", None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly,
            # copying each entry so callers never mutate simulated state.
            return [_copy_rule(rule) for rule in state]
        # Real transport: gather over asyncssh from the host's connection,
        # retrying the root read through passwordless sudo -n.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class UfwApplications:
    """The application profiles installed on the host.

    **Commands:**

    ```bash
    ufw app list 2> /dev/null || sudo -n ufw app list 2> /dev/null || true
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
        ``examples/modules/ufw/ufw_ufw_applications.py``.
    """

    # A reemote original (the Applications/app_profiles proposal): the
    # profile names ``ufw app list`` prints under its ``Available
    # applications:`` header, one per line, in listing order (ufw sorts
    # them alphabetically). These are the names ``ufw.rule(app=...)``
    # references. A host with no profiles prints just the header and
    # reads []. The command rides the passwordless ``sudo -n`` retry and
    # the ``|| true`` guard masks exit 127.
    #
    # SimulatedHost seam note: the ``ufw_applications`` field stores the
    # name list; an unseeded host reads [].

    #: Shell gathering the profile names over SSH, with the passwordless
    #: ``sudo -n`` retry for the root read.
    COMMAND = (
        "ufw app list 2> /dev/null || sudo -n ufw app list 2> /dev/null || true"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[str]:
        """Keep the profile names under the header, stripped. Pure."""
        names = []
        for line in output:
            name = line.strip()
            if name and name != "Available applications:":
                names.append(name)
        return names

    @classmethod
    async def process(cls, host: Host) -> list[str]:
        state = getattr(host, "ufw_applications", None)
        if state is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return list(state)
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


# --- Operations ----------------------------------------------------------------


# --- ufw.state ------------------------------------------------------------------


@dataclass(frozen=True)
class UfwStateCommand(Command):
    """``ufw --force enable`` / ``ufw disable``: toggle the firewall.

    The descriptor ``ufw.state`` yields. ``--force`` on enable skips the
    interactive "may disrupt existing ssh connections" prompt; disable
    never prompts. Enabling also makes ufw start on boot (ufw manages its
    own service enablement).
    """

    enabled: bool = True

    def shell(self) -> str:
        return "ufw --force enable" if self.enabled else "ufw disable"


async def state(
    ctx: OpContext,
    enabled: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Ensure the ufw firewall is enabled or disabled.

    Args:
        ctx (OpContext): Execution context for the operation.
        enabled (bool, optional): Whether the firewall should be active
            (and start on boot). Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated ufw state commands.

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/ufw/ufw_state.py``.
    """

    # A reemote original (the state/enable/disable proposals): idempotent
    # on the UfwStatus active flag -- ``ufw --force enable`` only when the
    # firewall is inactive, ``ufw disable`` only when active. The
    # Proposal's ``ipv6`` argument is deliberately dropped (decided with
    # the developer): ufw has no CLI toggle for IPv6 -- it is IPV6=yes in
    # /etc/default/ufw, a files.edit recipe's job -- and ufw manages both
    # address families together. An unprivileged fact read reports
    # inactive, so the plan shows the enable change (the module header's
    # privilege contract). The command needs root: flag the request
    # ``sudo=True``. ``**kwargs`` absorbs unknown arguments.
    status = await ctx.facts.get(UfwStatus)
    if status["active"] != enabled:
        ctx.changed = True
        ctx.reason = f"ufw will be {'enabled' if enabled else 'disabled'}"
        yield UfwStateCommand(enabled=enabled)
    else:
        ctx.reason = f"ufw is already {'active' if enabled else 'inactive'}"


# --- ufw.default_policy -----------------------------------------------------------


@dataclass(frozen=True)
class UfwDefaultPolicyCommand(Command):
    """``ufw default <policy> <direction>``: set one default policy.

    The descriptor ``ufw.default_policy`` yields. The policy and
    direction words are validated by the operation before the descriptor
    is built, and ``shlex``-quoted here.
    """

    policy: str
    direction: str = "incoming"

    def shell(self) -> str:
        return (
            f"ufw default {shlex.quote(self.policy)} {shlex.quote(self.direction)}"
        )


async def default_policy(
    ctx: OpContext,
    direction: str = "incoming",
    policy: str = "deny",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Set the default policy for incoming, outgoing or routed traffic.

    Args:
        ctx (OpContext): Execution context for the operation.
        direction (str, optional): Which default policy to manage:
            ``incoming``, ``outgoing`` or ``routed``. Defaults to
            "incoming".
        policy (str, optional): The fallback verdict: ``allow``, ``deny``
            or ``reject``. Defaults to "deny".
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated ufw default-policy commands.

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/ufw/ufw_default_policy.py``.
    """

    # A reemote original (the default/default_policy proposals):
    # idempotent on the UfwStatus record's matching default_* field. The
    # direction and policy words are validated statically (never against
    # fact state, so an unprivileged empty read still plans the change
    # rather than raising). ``routed`` accepts the same three verdicts;
    # ufw's ``disabled`` routed default is a reported state, not a
    # settable word. The command needs root: flag the request
    # ``sudo=True``. ``**kwargs`` absorbs unknown arguments.
    if direction not in ("incoming", "outgoing", "routed"):
        raise ValueError(
            f"direction must be incoming, outgoing or routed (got {direction!r})"
        )
    if policy not in ("allow", "deny", "reject"):
        raise ValueError(f"policy must be allow, deny or reject (got {policy!r})")
    status = await ctx.facts.get(UfwStatus)
    if status[f"default_{direction}"] != policy:  # type: ignore[literal-required]
        ctx.changed = True
        ctx.reason = f"ufw default {direction} policy will be set to {policy}"
        yield UfwDefaultPolicyCommand(policy=policy, direction=direction)
    else:
        ctx.reason = f"ufw default {direction} policy is already {policy}"


# --- ufw.logging ------------------------------------------------------------------


@dataclass(frozen=True)
class UfwLoggingCommand(Command):
    """``ufw logging <level>``: set the logging level.

    The descriptor ``ufw.logging`` yields. ``level`` is the validated
    word (``off``, ``on``, ``low``, ``medium``, ``high`` or ``full``),
    ``shlex``-quoted.
    """

    level: str

    def shell(self) -> str:
        return f"ufw logging {shlex.quote(self.level)}"


async def logging(
    ctx: OpContext,
    level: str = "low",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Set the ufw logging level.

    Args:
        ctx (OpContext): Execution context for the operation.
        level (str, optional): The logging level: ``off``, ``on``,
            ``low``, ``medium``, ``high`` or ``full``. ``on`` enables
            logging at the current level (``low`` when currently off),
            mirroring ufw's own semantics. Defaults to "low".
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated ufw logging commands.

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/ufw/ufw_logging.py``.
    """

    # A reemote original (the logging proposal): idempotent on the
    # UfwStatus logging word. A concrete level compares directly; ``on``
    # is a change only when logging is off (or unreadable) -- ufw's
    # set_loglevel keeps the current level when already logging, so
    # ``on`` against ``medium`` is a no-op, not a downgrade to low. The
    # yielded command stays ``ufw logging on`` verbatim (the descriptor
    # never depends on fact state, keeping both phases identical). The
    # command needs root: flag the request ``sudo=True``. ``**kwargs``
    # absorbs unknown arguments.
    if level not in ("off", "on", "low", "medium", "high", "full"):
        raise ValueError(
            f"level must be off, on, low, medium, high or full (got {level!r})"
        )
    status = await ctx.facts.get(UfwStatus)
    current = status["logging"]
    if level == "on":
        changed = current in ("off", "unknown", "")
    else:
        changed = current != level
    if changed:
        ctx.changed = True
        ctx.reason = f"ufw logging will be set to {level}"
        yield UfwLoggingCommand(level=level)
    else:
        ctx.reason = f"ufw logging is already {current}"


# --- ufw.reset --------------------------------------------------------------------


@dataclass(frozen=True)
class UfwResetCommand(Command):
    """``ufw --force reset``: reset to installation defaults.

    The descriptor ``ufw.reset`` yields. ``--force`` skips the interactive
    confirmation. The real command backs up the current rules, deletes
    every user rule and disables the firewall.
    """

    def shell(self) -> str:
        return "ufw --force reset"


async def reset(
    ctx: OpContext,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Reset ufw to installation defaults, deleting every rule.

    Args:
        ctx (OpContext): Execution context for the operation.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The reset command, always (the operation is stateless).

    **Raises:**

    ```python
    None
    ```

    Examples:
        Examples can be found in ``examples/modules/ufw/ufw_reset.py``.
    """

    # A reemote original (the reset proposal): deliberately STATELESS
    # (the selinux.restorecon precedent) -- a reset does not map to a
    # persistent desired state, so it always yields and always reports
    # changed. Destructive: it deletes every user rule and disables the
    # firewall (which can lock a deploy out of its own hosts), so it runs
    # only when a deploy explicitly yields it. No fact is read, so
    # nothing on the operation's own code path raises. The command needs
    # root: flag the request ``sudo=True``. ``**kwargs`` absorbs unknown
    # arguments.
    ctx.changed = True
    ctx.reason = "ufw will be reset to installation defaults (all rules deleted)"
    yield UfwResetCommand()


# --- ufw.rule ---------------------------------------------------------------------


@dataclass(frozen=True)
class UfwRuleCommand(Command):
    """``ufw [route] allow|deny|reject|limit ...``: one rule change.

    The descriptor ``ufw.rule`` yields. ``present=False`` renders
    ``ufw --force delete <spec>`` (``--force`` skips the delete
    confirmation; a ``route`` rule renders ``ufw --force route delete``);
    ``present=True`` renders the add form, with ``insert N`` when a
    position is requested. Every field is a normalized hashable string
    the operation built: ``port`` is the joined token, empty strings mean
    "unset", and user-supplied words are ``shlex``-quoted at render time.
    """

    action: str = "allow"
    direction: str = "in"
    route: bool = False
    port: str = ""
    protocol: str = ""
    app: str = ""
    from_ip: str = ""
    to_ip: str = ""
    interface: str = ""
    log: bool = False
    comment: str = ""
    insert: int | None = None
    present: bool = True

    def shell(self) -> str:
        parts = ["ufw"]
        if not self.present:
            parts.append("--force")
        if self.route:
            parts.append("route")
        if not self.present:
            parts.append("delete")
        elif self.insert is not None:
            parts.extend(("insert", str(self.insert)))
        parts.append(self.action)
        if self.route:
            # Route rules take no bare direction word -- only the
            # incoming interface clause ("in on <iface>").
            if self.interface:
                parts.extend(("in", "on", shlex.quote(self.interface)))
        else:
            parts.append(self.direction)
            if self.interface:
                parts.extend(("on", shlex.quote(self.interface)))
            if self.log:
                parts.append("log")
        if self.app:
            parts.extend(
                (
                    "from",
                    shlex.quote(self.from_ip or "any"),
                    "to",
                    shlex.quote(self.to_ip or "any"),
                    "app",
                    shlex.quote(self.app),
                )
            )
        else:
            if self.protocol:
                parts.extend(("proto", self.protocol))
            if self.port:
                parts.extend(
                    (
                        "from",
                        shlex.quote(self.from_ip or "any"),
                        "to",
                        shlex.quote(self.to_ip or "any"),
                        "port",
                        self.port,
                    )
                )
            else:
                if self.from_ip:
                    parts.extend(("from", shlex.quote(self.from_ip)))
                if self.to_ip:
                    parts.extend(("to", shlex.quote(self.to_ip)))
        if self.comment:
            parts.extend(("comment", shlex.quote(self.comment)))
        return " ".join(parts)


def _command_rule_key(cmd: UfwRuleCommand) -> dict[str, Any]:
    """Project a rule descriptor onto the identity fields. Pure.

    The SimulatedHost delete mutator matches stored rows through this
    projection so the simulation deletes exactly what the operation
    matched (the ``_rule_key`` counterpart for the command side).
    """
    return {
        "action": cmd.action.upper(),
        "direction": "FWD" if cmd.route else cmd.direction.upper(),
        "log": cmd.log,
        "to_addr": _normalize_addr(cmd.to_ip or None),
        "port": cmd.port,
        "protocol": cmd.protocol,
        "app": cmd.app,
        "from_addr": _normalize_addr(cmd.from_ip or None),
        "interface": cmd.interface,
        "comment": cmd.comment,
    }


async def rule(
    ctx: OpContext,
    port: int | str | tuple[int | str, ...] | None = None,
    protocol: str | None = None,
    action: str = "allow",
    present: bool = True,
    direction: str = "in",
    route: bool = False,
    from_ip: str | None = None,
    to_ip: str | None = None,
    app: str | None = None,
    interface: str | None = None,
    log: bool = False,
    comment: str | None = None,
    insert: int | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add or remove a ufw rule (ports, profiles, addresses, routes).

    Args:
        ctx (OpContext): Execution context for the operation.
        port (int | str | tuple[int | str, ...] | None, optional): The
            destination port: a single port (``22``), a colon range
            (``"8000:8010"``) or a tuple multiport (``(80, 443)``).
            Multiports and ranges require ``protocol``. Defaults to None.
        protocol (str | None, optional): The protocol, ``tcp`` or ``udp``;
            None covers both. Defaults to None.
        action (str, optional): The verdict: ``allow``, ``deny``,
            ``reject`` or ``limit`` (rate limiting). Defaults to "allow".
        present (bool, optional): Whether the rule should exist.
            Defaults to True.
        direction (str, optional): Traffic direction, ``in`` or ``out``.
            Defaults to "in".
        route (bool, optional): Whether this is a routed (forwarded)
            traffic rule (``ufw route ...``). Defaults to False.
        from_ip (str | None, optional): Source address/CIDR restriction;
            None means any source. Defaults to None.
        to_ip (str | None, optional): Destination address/CIDR
            restriction; None means any destination. Defaults to None.
        app (str | None, optional): An application profile name (e.g.
            ``"Nginx Full"``) instead of a port; mutually exclusive with
            ``port`` and ``protocol``. Defaults to None.
        interface (str | None, optional): Bind the rule to an interface
            (the incoming interface for ``in``/``route`` rules, the
            outgoing one for ``out`` rules). Defaults to None.
        log (bool, optional): Whether matched packets are logged.
            Defaults to False.
        comment (str | None, optional): Rule comment -- the reliable
            auditing marker, shown by ``ufw status``. Defaults to None.
        insert (int | None, optional): The 1-based position to insert a
            new rule at; an existing rule is never repositioned, and
            deletion ignores it. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated ufw rule commands.

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/ufw/ufw_rule.py``.
    """

    # A reemote original (the rule/application proposals): the desired
    # spec is normalized into the same identity projection the UfwRules
    # fact parses (``_rule_key``), and compared against the numbered
    # rows. present=True yields one add when no row matches; present=False
    # yields one ``ufw --force delete <spec>`` per matching row (ufw
    # allows duplicates, so N duplicates need N deletes; each delete
    # removes the row and its v6 twin).
    #
    # IPv6 handling (the developer's drop-the-version-arg decision): ufw
    # manages both families together. An address-less or IPv4 rule is
    # matched against its IPv4 row only -- ufw maintains the ``(v6)`` twin
    # itself on add and delete -- while a rule naming an IPv6 address is
    # v6-only and matches the ``(v6)`` rows.
    #
    # Matching is exact on every identity field including log and
    # comment, EXCEPT that a deletion with no comment matches any comment
    # (mirroring ufw's own comment-less delete, which ignores the comment
    # when none is given). ``insert`` positions a NEW rule only -- an
    # existing rule at a different position is left alone (the iptables
    # ``position`` precedent; reordering live rules is destructive).
    #
    # Deliberate limits: route rules expose only the incoming interface
    # and cannot log (ufw's route grammar has no log keyword); source
    # ports and ``log-all`` stay raw-shell territory. The commands need
    # root: flag the request ``sudo=True``. ``**kwargs`` absorbs unknown
    # arguments.
    action = action.lower()
    if action not in ("allow", "deny", "reject", "limit"):
        raise ValueError(
            f"action must be allow, deny, reject or limit (got {action!r})"
        )
    if direction not in ("in", "out"):
        raise ValueError(f"direction must be in or out (got {direction!r})")
    if protocol is not None:
        protocol = protocol.lower()
        if protocol not in ("tcp", "udp"):
            raise ValueError(f"protocol must be tcp or udp (got {protocol!r})")
    if app is not None and port is not None:
        raise ValueError("ufw rule cannot combine app with port")
    if app is not None and protocol is not None:
        raise ValueError("ufw rule cannot combine app with protocol")
    ports = _normalize_port(port)
    if ports and ("," in ports or ":" in ports) and not protocol:
        raise ValueError("ufw multiport and range rules require a protocol")
    if route and direction == "out":
        raise ValueError("ufw route rules cannot use direction out")
    if route and log:
        raise ValueError("ufw route rules cannot log")
    if insert is not None and insert < 1:
        raise ValueError(f"insert must be a positive rule number (got {insert!r})")
    if not (ports or protocol or app or from_ip or to_ip or interface):
        raise ValueError(
            "ufw rule needs at least one of port, protocol, app, from_ip, "
            "to_ip or interface"
        )

    desired = {
        "action": action.upper(),
        "direction": "FWD" if route else direction.upper(),
        "log": log,
        "to_addr": _normalize_addr(to_ip),
        "port": ports,
        "protocol": protocol or "",
        "app": app or "",
        "from_addr": _normalize_addr(from_ip),
        "interface": interface or "",
        "comment": comment or "",
    }
    v6 = _is_v6(from_ip, to_ip)
    rules = await ctx.facts.get(UfwRules)
    matches = []
    for row in rules:
        if row["v6"] != v6:
            continue
        key = _rule_key(row)
        target = dict(desired)
        if not present and comment is None:
            # ufw's comment-less delete ignores the stored comment.
            key.pop("comment")
            target.pop("comment")
        if key == target:
            matches.append(row)

    if present:
        if matches:
            ctx.reason = "ufw rule exists"
            return
        ctx.changed = True
        ctx.reason = "ufw rule will be added"
        count = 1
    else:
        if not matches:
            ctx.reason = "ufw rule does not exist"
            return
        ctx.changed = True
        count = len(matches)
        ctx.reason = "ufw rule will be deleted"
        if count > 1:
            ctx.reason += f" ({count} matches)"

    for _ in range(count):
        yield UfwRuleCommand(
            action=action,
            direction=direction,
            route=route,
            port=ports,
            protocol=protocol or "",
            app=app or "",
            from_ip=from_ip or "",
            to_ip=to_ip or "",
            interface=interface or "",
            log=log,
            comment=comment or "",
            insert=insert if present else None,
            present=present,
        )


__all__ = [
    "UfwApplications",
    "UfwDefaultPolicyCommand",
    "UfwLoggingCommand",
    "UfwResetCommand",
    "UfwRule",
    "UfwRuleCommand",
    "UfwRules",
    "UfwStateCommand",
    "UfwStatus",
    "UfwStatusInfo",
    "default_policy",
    "logging",
    "reset",
    "rule",
    "state",
]