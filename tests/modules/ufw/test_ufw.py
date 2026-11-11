# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the ufw module: ``UfwStatus``, ``UfwRules``, ``UfwApplications``
and the five operations (``state``, ``default_policy``, ``logging``, ``reset``,
``rule``).

ufw ships no baseline module, so every fact and operation here is a reemote
original -- there are no baseline fixtures to reuse verbatim. The real-transport
paths are instead fed realistic canned ``ufw status verbose``, ``ufw status
numbered`` and ``ufw app list`` output whose exact column format is taken from
ufw's own ``backend_iptables.get_status`` renderer (the ``[%2d] %-26s %-12s
%-26s%s%s`` row, the trailing ``(log)``/``(out)`` attribute group, the ``(v6)``
marker, the ``on <iface>`` suffix and the ``# comment`` tail). The parser edge
cases -- an inactive firewall, a protocol-only ``Anywhere/tcp`` column, an
application-profile row, a route rule's interface-on-From reporting, a v6 twin
and a comment -- are each exercised.

The operations are exercised over their descriptors (the shell rendering and
the sudo/su elevated forms), their fact-backed idempotence in both directions
(the changed/reason/no-command no-ops), the ``ValueError`` validation guards,
the ``SimulatedHost`` seam dispatch and round-trip idempotence (add then re-add
is a no-op, delete then re-delete is a no-op, the v6 twin is maintained), and
end-to-end through ``run_prepare`` + ``run_apply`` (change-then-noop, the
default-rule-enable composition, lockstep ordering and a dry run).

``asyncio_mode = "auto"`` lets the async tests below run without decorators.
"""

import shlex

import pytest

from reemote.core import FactCache, OpContext, op
from reemote.drivers import (
    command_shell,
    format_log,
    format_plan,
    gather_fact,
    run,
    run_apply,
    run_prepare,
    send_command,
)
from reemote.hosts import Host
from reemote.modules import ufw
from reemote.modules.ufw.ufw import (
    UfwApplications,
    UfwDefaultPolicyCommand,
    UfwLoggingCommand,
    UfwResetCommand,
    UfwRule,
    UfwRuleCommand,
    UfwRules,
    UfwStateCommand,
    UfwStatus,
    UfwStatusInfo,
    _parse_location,
    _parse_rule_line,
)
from tests.simulated_host import SimulatedHost


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell`` instead of connecting."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout
        self.last_shell: str | None = None

    async def run_shell(self, shell: str) -> str:
        self.last_shell = shell
        return self._stdout


def make_ctx(host: Host) -> OpContext:
    """A prepare-phase context over one host, the operation-test pattern."""
    return OpContext(host=host, facts=FactCache(host, phase="prepare"))


def rule_row(**overrides) -> UfwRule:
    """One numbered rule row with every field defaulted, for seam seeding."""
    row: UfwRule = {
        "number": 1,
        "action": "ALLOW",
        "direction": "IN",
        "log": False,
        "v6": False,
        "to_addr": "Anywhere",
        "port": "22",
        "protocol": "tcp",
        "app": "",
        "from_addr": "Anywhere",
        "interface": "",
        "comment": "",
    }
    row.update(overrides)
    return row


def status_record(**overrides) -> UfwStatusInfo:
    """An active status record with realistic defaults, for seam seeding."""
    record: UfwStatusInfo = {
        "active": True,
        "logging": "low",
        "default_incoming": "deny",
        "default_outgoing": "allow",
        "default_routed": "disabled",
    }
    record.update(overrides)
    return record


# --- UfwStatus: canned verbose output ---------------------------------------------------------

# A realistic ``ufw status verbose`` header (the rule table it also prints is
# deliberately included -- UfwStatus ignores it, UfwRules answers the ruleset).
STATUS_VERBOSE = (
    "Status: active\n"
    "Logging: on (low)\n"
    "Default: deny (incoming), allow (outgoing), disabled (routed)\n"
    "New profiles: skip\n"
    "\n"
    "To                         Action      From\n"
    "--                         ------      ----\n"
    "22/tcp                     ALLOW IN    Anywhere\n"
    "22/tcp (v6)                ALLOW IN    Anywhere (v6)\n"
)


async def test_ufw_status_real_transport():
    """The verbose header parses into the active flag, the logging level word
    and the three default policies; the rule table is ignored."""
    host = CannedShellHost(STATUS_VERBOSE)
    info = await UfwStatus.process(host)
    assert info == {
        "active": True,
        "logging": "low",
        "default_incoming": "deny",
        "default_outgoing": "allow",
        "default_routed": "disabled",
    }
    assert host.last_shell == UfwStatus.COMMAND
    assert "sudo -n ufw status verbose" in host.last_shell


async def test_ufw_status_inactive():
    """An inactive firewall prints only ``Status: inactive`` -- every other
    field keeps its empty default."""
    host = CannedShellHost("Status: inactive\n")
    info = await UfwStatus.process(host)
    assert info == {
        "active": False,
        "logging": "",
        "default_incoming": "",
        "default_outgoing": "",
        "default_routed": "",
    }


async def test_ufw_status_empty_unprivileged():
    """A host granting neither root nor passwordless sudo reads the empty
    record (the ``|| true`` guard masks the failure)."""
    host = CannedShellHost("")
    info = await UfwStatus.process(host)
    assert info["active"] is False
    assert info["logging"] == ""


@pytest.mark.parametrize(
    "line,expected",
    [
        ("Logging: on (low)", "low"),
        ("Logging: on (medium)", "medium"),
        ("Logging: on (high)", "high"),
        ("Logging: on (full)", "full"),
        ("Logging: off", "off"),
        ("Logging: unknown", "unknown"),
    ],
)
def test_ufw_status_logging_levels(line, expected):
    """``on (<level>)`` reduces to the level word; off/unknown stay verbatim."""
    info = UfwStatus.parse(["Status: active", line])
    assert info["logging"] == expected


def test_ufw_status_default_policies():
    """The Default line's three ``policy (direction)`` pairs each parse."""
    info = UfwStatus.parse(
        ["Default: reject (incoming), deny (outgoing), allow (routed)"]
    )
    assert info["default_incoming"] == "reject"
    assert info["default_outgoing"] == "deny"
    assert info["default_routed"] == "allow"


async def test_ufw_status_seam_copy():
    """The seam returns a copy -- mutating it never touches simulated state."""
    record = status_record()
    host = SimulatedHost(name="h", ufw_status=record)
    info = await UfwStatus.process(host)
    assert info == record
    info["active"] = False
    assert host.ufw_status["active"] is True


# --- UfwRules: canned numbered output ---------------------------------------------------------

# A realistic ``ufw status numbered`` listing exercising every column shape:
# a plain port, a multiport with a source CIDR, a comment, an out rule with an
# interface, a range with limit, a log attribute, an application profile, a
# route rule (interface reported on From), and the v6 twins.
STATUS_NUMBERED = (
    "Status: active\n"
    "\n"
    "     To                         Action      From\n"
    "     --                         ------      ----\n"
    "[ 1] 22/tcp                     ALLOW IN    Anywhere\n"
    "[ 2] 80,443/tcp                 ALLOW IN    192.168.1.0/24\n"
    "[ 3] 22/tcp                     ALLOW IN    Anywhere                   # Allow SSH\n"
    "[ 4] 25/tcp                     DENY OUT    Anywhere on eth1           (out)\n"
    "[ 5] 8000:8010/tcp              LIMIT IN    Anywhere                   (log)\n"
    "[ 6] Nginx Full                 ALLOW IN    Anywhere\n"
    "[ 7] Anywhere                   ALLOW FWD   192.168.1.0/24 on eth0\n"
    "[ 8] 22/tcp (v6)                ALLOW IN    Anywhere (v6)\n"
    "[ 9] 80,443/tcp (v6)            ALLOW IN    192.168.1.0/24 (v6)\n"
)


async def test_ufw_rules_real_transport():
    """Every numbered row parses into a structured entry in listing order."""
    host = CannedShellHost(STATUS_NUMBERED)
    rules = await UfwRules.process(host)
    assert host.last_shell == UfwRules.COMMAND
    assert "sudo -n ufw status numbered" in host.last_shell
    assert [r["number"] for r in rules] == list(range(1, 10))
    # A plain port rule.
    assert rules[0] == rule_row(number=1)
    # A multiport with a source CIDR.
    assert rules[1] == rule_row(number=2, port="80,443", from_addr="192.168.1.0/24")
    # A comment.
    assert rules[2] == rule_row(number=3, comment="Allow SSH")
    # An out rule with an interface and the redundant (out) attribute.
    assert rules[3] == rule_row(
        number=4, action="DENY", direction="OUT", port="25", interface="eth1"
    )
    # A range with limit and the (log) attribute.
    assert rules[4] == rule_row(
        number=5, action="LIMIT", port="8000:8010", log=True
    )
    # An application profile.
    assert rules[5] == rule_row(number=6, port="", protocol="", app="Nginx Full")
    # A route rule -- the incoming interface is reported on From.
    assert rules[6] == rule_row(
        number=7,
        direction="FWD",
        port="",
        protocol="",
        from_addr="192.168.1.0/24",
        interface="eth0",
    )
    # The v6 twins.
    assert rules[7] == rule_row(number=8, v6=True)
    assert rules[8] == rule_row(
        number=9, port="80,443", from_addr="192.168.1.0/24", v6=True
    )


async def test_ufw_rules_inactive():
    """An inactive firewall reads no rules."""
    host = CannedShellHost("Status: inactive\n")
    assert await UfwRules.process(host) == []


async def test_ufw_rules_seam_copy():
    """The seam copies each entry -- mutating one never touches state."""
    host = SimulatedHost(name="h", ufw_rules=[rule_row()])
    rules = await UfwRules.process(host)
    assert rules == [rule_row()]
    rules[0]["port"] = "9999"
    assert host.ufw_rules[0]["port"] == "22"


def test_parse_location_protocol_only():
    """An ``Anywhere/tcp`` column is a protocol-only rule -- the /proto suffix
    strips before the address/port split."""
    loc = _parse_location("Anywhere/tcp")
    assert loc["address"] == "Anywhere"
    assert loc["port"] == ""
    assert loc["protocol"] == "tcp"


def test_parse_location_cidr_keeps_prefix():
    """A CIDR's /24 (all digits) stays part of the address, not a protocol."""
    loc = _parse_location("192.168.1.0/24")
    assert loc["address"] == "192.168.1.0/24"
    assert loc["protocol"] == ""


def test_parse_location_interface_and_v6():
    """The interface suffix comes last, after the v6 marker (ufw appends
    ``on <iface>`` after ``(v6)``)."""
    loc = _parse_location("Anywhere (v6) on eth0")
    assert loc["interface"] == "eth0"
    assert loc["v6"] is True
    assert loc["address"] == "Anywhere"


def test_parse_rule_line_non_rule():
    """Header, table-header and blank lines parse to None."""
    assert _parse_rule_line("Status: active") is None
    assert _parse_rule_line("     To                         Action      From") is None
    assert _parse_rule_line("") is None


def test_parse_rule_line_log_all():
    """A ``(log-all)`` attribute reads as log=True (the fact records that the
    rule logs, not which log type)."""
    row = _parse_rule_line(
        "[ 1] 22/tcp                     ALLOW IN    Anywhere                   (log-all)"
    )
    assert row is not None
    assert row["log"] is True


# --- UfwApplications: canned app list ---------------------------------------------------------

APP_LIST = (
    "Available applications:\n"
    "  Nginx Full\n"
    "  Nginx HTTP\n"
    "  Nginx HTTPS\n"
    "  OpenSSH\n"
)


async def test_ufw_applications_real_transport():
    """The profile names under the header parse, stripped, in listing order."""
    host = CannedShellHost(APP_LIST)
    names = await UfwApplications.process(host)
    assert names == ["Nginx Full", "Nginx HTTP", "Nginx HTTPS", "OpenSSH"]
    assert host.last_shell == UfwApplications.COMMAND
    assert "sudo -n ufw app list" in host.last_shell


async def test_ufw_applications_empty():
    """A host with no profiles prints just the header and reads []."""
    host = CannedShellHost("Available applications:\n")
    assert await UfwApplications.process(host) == []


async def test_ufw_applications_seam_copy():
    """The seam returns a copy of the name list."""
    host = SimulatedHost(name="h", ufw_applications=["OpenSSH"])
    names = await UfwApplications.process(host)
    assert names == ["OpenSSH"]
    names.append("Nginx Full")
    assert host.ufw_applications == ["OpenSSH"]


# --- gather_fact fan-outs -------------------------------------------------------------------


async def test_gather_fact_status_fans_out():
    """The fan-out drives every host's seam read, keyed by host name."""
    host1 = SimulatedHost(name="web1", ufw_status=status_record())
    host2 = SimulatedHost(name="web2")
    result = await gather_fact([host1, host2], UfwStatus)
    assert result["web1"]["active"] is True
    assert result["web2"]["active"] is False


async def test_gather_fact_applications_over_canned_shells():
    """The fan-out drives the real-transport path with the same app-list shell."""
    host1 = CannedShellHost(APP_LIST)
    host1.name = "web1"
    host2 = CannedShellHost("")
    host2.name = "web2"
    result = await gather_fact([host1, host2], UfwApplications)
    assert result == {
        "web1": ["Nginx Full", "Nginx HTTP", "Nginx HTTPS", "OpenSSH"],
        "web2": [],
    }
    assert host1.last_shell == UfwApplications.COMMAND


# --- Command descriptors ---------------------------------------------------------------------


def test_state_command_renders_and_elevates():
    """--force on enable skips the ssh-disruption prompt; disable never
    prompts. sudo prefixes the whole shell, su wraps it."""
    assert UfwStateCommand().shell() == "ufw --force enable"
    assert UfwStateCommand(enabled=False).shell() == "ufw disable"
    assert command_shell(UfwStateCommand(), sudo=True) == "sudo ufw --force enable"
    assert command_shell(UfwStateCommand(), su=True) == (
        f"su -c {shlex.quote('ufw --force enable')}"
    )


def test_default_policy_command_renders():
    """The policy and direction words render in ufw's argument order."""
    assert UfwDefaultPolicyCommand(policy="deny").shell() == "ufw default deny incoming"
    assert UfwDefaultPolicyCommand(policy="allow", direction="outgoing").shell() == (
        "ufw default allow outgoing"
    )
    assert UfwDefaultPolicyCommand(policy="reject", direction="routed").shell() == (
        "ufw default reject routed"
    )


def test_logging_and_reset_commands_render():
    """The level word renders verbatim; reset always carries --force."""
    assert UfwLoggingCommand(level="medium").shell() == "ufw logging medium"
    assert UfwResetCommand().shell() == "ufw --force reset"
    assert command_shell(UfwResetCommand(), sudo=True) == "sudo ufw --force reset"


def test_rule_command_renders_port_forms():
    """A port renders the full from/to grammar; multiports and ranges render
    as the one joined token; a comment is quoted."""
    assert UfwRuleCommand(port="22", protocol="tcp").shell() == (
        "ufw allow in proto tcp from any to any port 22"
    )
    assert UfwRuleCommand(
        port="80,443", protocol="tcp", from_ip="192.168.1.0/24"
    ).shell() == "ufw allow in proto tcp from 192.168.1.0/24 to any port 80,443"
    assert UfwRuleCommand(port="8000:8010", protocol="tcp", action="limit").shell() == (
        "ufw limit in proto tcp from any to any port 8000:8010"
    )
    assert UfwRuleCommand(port="22", protocol="tcp", comment="Allow SSH").shell() == (
        "ufw allow in proto tcp from any to any port 22 comment 'Allow SSH'"
    )


def test_rule_command_renders_direction_interface_log():
    """An out rule carries its direction word, the on-interface clause and
    the log keyword before the proto clause."""
    assert UfwRuleCommand(
        action="deny", direction="out", port="25", protocol="tcp",
        interface="eth1", log=True,
    ).shell() == "ufw deny out on eth1 log proto tcp from any to any port 25"


def test_rule_command_renders_app_and_address_forms():
    """An app rule renders the app grammar (quoted); an address-only rule
    renders bare from/to clauses; a protocol-only rule renders just proto."""
    assert UfwRuleCommand(app="Nginx Full").shell() == (
        "ufw allow in from any to any app 'Nginx Full'"
    )
    assert UfwRuleCommand(action="deny", from_ip="10.0.0.0/8").shell() == (
        "ufw deny in from 10.0.0.0/8"
    )
    assert UfwRuleCommand(protocol="tcp").shell() == "ufw allow in proto tcp"
    assert UfwRuleCommand(app="My;App", comment="a b").shell() == (
        "ufw allow in from any to any app 'My;App' comment 'a b'"
    )


def test_rule_command_renders_insert_and_delete():
    """insert N prefixes the add; present=False renders --force delete and
    never an insert position."""
    assert UfwRuleCommand(action="deny", port="23", protocol="tcp", insert=1).shell() == (
        "ufw insert 1 deny in proto tcp from any to any port 23"
    )
    assert UfwRuleCommand(port="22", protocol="tcp", present=False).shell() == (
        "ufw --force delete allow in proto tcp from any to any port 22"
    )
    assert UfwRuleCommand(
        port="22", protocol="tcp", present=False, insert=1
    ).shell() == "ufw --force delete allow in proto tcp from any to any port 22"


def test_rule_command_renders_route_forms():
    """A route rule takes no bare direction word -- only the incoming
    interface clause; delete carries the global --force ahead of route."""
    assert UfwRuleCommand(
        route=True, port="80", protocol="tcp", from_ip="192.168.1.0/24",
        interface="eth1",
    ).shell() == (
        "ufw route allow in on eth1 proto tcp from 192.168.1.0/24 to any port 80"
    )
    assert UfwRuleCommand(
        route=True, port="80", protocol="tcp", from_ip="192.168.1.0/24",
        interface="eth1", present=False,
    ).shell() == (
        "ufw --force route delete allow in on eth1 proto tcp from 192.168.1.0/24 to any port 80"
    )


# --- ufw.state --------------------------------------------------------------------------------


async def test_state_enable_when_inactive():
    """An inactive firewall yields the --force enable."""
    host = SimulatedHost(name="h")
    ctx = make_ctx(host)
    commands = [c async for c in ufw.state(ctx)]
    assert ctx.changed
    assert ctx.reason == "ufw will be enabled"
    assert commands == [UfwStateCommand(enabled=True)]


async def test_state_enable_noop_when_active():
    """An active firewall is left alone."""
    host = SimulatedHost(name="h", ufw_status=status_record())
    ctx = make_ctx(host)
    commands = [c async for c in ufw.state(ctx)]
    assert not ctx.changed
    assert ctx.reason == "ufw is already active"
    assert commands == []


async def test_state_disable_when_active():
    """The bidirectional operation disables an active firewall."""
    host = SimulatedHost(name="h", ufw_status=status_record())
    ctx = make_ctx(host)
    commands = [c async for c in ufw.state(ctx, enabled=False)]
    assert ctx.changed
    assert ctx.reason == "ufw will be disabled"
    assert commands == [UfwStateCommand(enabled=False)]


async def test_state_disable_noop_when_inactive():
    host = SimulatedHost(name="h")
    ctx = make_ctx(host)
    commands = [c async for c in ufw.state(ctx, enabled=False)]
    assert not ctx.changed
    assert ctx.reason == "ufw is already inactive"
    assert commands == []


async def test_state_kwargs_absorbed():
    """An unknown keyword is absorbed by **kwargs, never a TypeError."""
    host = SimulatedHost(name="h")
    ctx = make_ctx(host)
    commands = [c async for c in ufw.state(ctx, some_global="ignored")]
    assert commands == [UfwStateCommand(enabled=True)]


# --- ufw.default_policy -------------------------------------------------------------------------


async def test_default_policy_change():
    """A differing policy yields the default command for that direction."""
    host = SimulatedHost(name="h", ufw_status=status_record())
    ctx = make_ctx(host)
    commands = [c async for c in ufw.default_policy(ctx, policy="reject")]
    assert ctx.changed
    assert ctx.reason == "ufw default incoming policy will be set to reject"
    assert commands == [UfwDefaultPolicyCommand(policy="reject", direction="incoming")]


async def test_default_policy_noop():
    """The matching policy is a no-op with the already reason."""
    host = SimulatedHost(name="h", ufw_status=status_record())
    ctx = make_ctx(host)
    commands = [c async for c in ufw.default_policy(ctx)]
    assert not ctx.changed
    assert ctx.reason == "ufw default incoming policy is already deny"
    assert commands == []


@pytest.mark.parametrize("direction", ["incoming", "outgoing", "routed"])
async def test_default_policy_each_direction(direction):
    """Each direction compares its own default_* field."""
    host = SimulatedHost(name="h", ufw_status=status_record())
    ctx = make_ctx(host)
    commands = [
        c async for c in ufw.default_policy(ctx, direction=direction, policy="reject")
    ]
    assert ctx.changed
    assert commands == [UfwDefaultPolicyCommand(policy="reject", direction=direction)]


async def test_default_policy_unprivileged_plans_change():
    """An empty (unprivileged) read still plans the change -- the words are
    validated statically, never against fact state."""
    host = SimulatedHost(name="h")
    ctx = make_ctx(host)
    commands = [c async for c in ufw.default_policy(ctx)]
    assert ctx.changed
    assert commands == [UfwDefaultPolicyCommand(policy="deny", direction="incoming")]


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"direction": "forward"}, "direction must be"),
        ({"policy": "drop"}, "policy must be"),
    ],
)
async def test_default_policy_validation(kwargs, match):
    host = SimulatedHost(name="h")
    ctx = make_ctx(host)
    with pytest.raises(ValueError, match=match):
        [c async for c in ufw.default_policy(ctx, **kwargs)]


# --- ufw.logging --------------------------------------------------------------------------------


async def test_logging_change():
    host = SimulatedHost(name="h", ufw_status=status_record(logging="low"))
    ctx = make_ctx(host)
    commands = [c async for c in ufw.logging(ctx, level="medium")]
    assert ctx.changed
    assert ctx.reason == "ufw logging will be set to medium"
    assert commands == [UfwLoggingCommand(level="medium")]


async def test_logging_noop():
    host = SimulatedHost(name="h", ufw_status=status_record(logging="medium"))
    ctx = make_ctx(host)
    commands = [c async for c in ufw.logging(ctx, level="medium")]
    assert not ctx.changed
    assert ctx.reason == "ufw logging is already medium"
    assert commands == []


async def test_logging_on_keeps_current_level():
    """``on`` against an already-logging level is a no-op -- ufw's
    set_loglevel keeps the current level, so this is not a downgrade."""
    host = SimulatedHost(name="h", ufw_status=status_record(logging="medium"))
    ctx = make_ctx(host)
    commands = [c async for c in ufw.logging(ctx, level="on")]
    assert not ctx.changed
    assert ctx.reason == "ufw logging is already medium"
    assert commands == []


async def test_logging_on_when_off():
    """``on`` against off yields the verbatim ``ufw logging on`` command."""
    host = SimulatedHost(name="h", ufw_status=status_record(logging="off"))
    ctx = make_ctx(host)
    commands = [c async for c in ufw.logging(ctx, level="on")]
    assert ctx.changed
    assert commands == [UfwLoggingCommand(level="on")]
    assert commands[0].shell() == "ufw logging on"


async def test_logging_off():
    host = SimulatedHost(name="h", ufw_status=status_record(logging="low"))
    ctx = make_ctx(host)
    commands = [c async for c in ufw.logging(ctx, level="off")]
    assert ctx.changed
    assert commands == [UfwLoggingCommand(level="off")]


async def test_logging_validation():
    host = SimulatedHost(name="h")
    ctx = make_ctx(host)
    with pytest.raises(ValueError, match="level must be"):
        [c async for c in ufw.logging(ctx, level="verbose")]


# --- ufw.reset ----------------------------------------------------------------------------------


async def test_reset_always_yields():
    """The stateless reset always reports changed and yields, whatever the
    stored state -- it maps to no persistent desired state."""
    for host in (
        SimulatedHost(name="h"),
        SimulatedHost(name="h", ufw_status=status_record(), ufw_rules=[rule_row()]),
    ):
        ctx = make_ctx(host)
        commands = [c async for c in ufw.reset(ctx)]
        assert ctx.changed
        assert ctx.reason == (
            "ufw will be reset to installation defaults (all rules deleted)"
        )
        assert commands == [UfwResetCommand()]


# --- ufw.rule: add / exists ----------------------------------------------------------------------


async def test_rule_add_when_missing():
    """A missing rule yields one normalized add descriptor."""
    host = SimulatedHost(name="h")
    ctx = make_ctx(host)
    commands = [
        c async for c in ufw.rule(ctx, port=22, protocol="tcp", comment="Allow SSH")
    ]
    assert ctx.changed
    assert ctx.reason == "ufw rule will be added"
    assert commands == [
        UfwRuleCommand(port="22", protocol="tcp", comment="Allow SSH")
    ]


async def test_rule_exists_noop():
    """An exactly-matching stored row is a no-op."""
    host = SimulatedHost(name="h", ufw_rules=[rule_row(comment="Allow SSH")])
    ctx = make_ctx(host)
    commands = [
        c async for c in ufw.rule(ctx, port=22, protocol="tcp", comment="Allow SSH")
    ]
    assert not ctx.changed
    assert ctx.reason == "ufw rule exists"
    assert commands == []


@pytest.mark.parametrize("from_ip", [None, "any", "Anywhere", "0.0.0.0/0"])
async def test_rule_any_address_normalization(from_ip):
    """Every IPv4 any-address spelling compares equal to the parsed Anywhere
    column, so none of them re-adds the rule. (``::/0`` is the IPv6 any and
    is v6-only -- see the v6 tests.)"""
    host = SimulatedHost(name="h", ufw_rules=[rule_row()])
    ctx = make_ctx(host)
    commands = [c async for c in ufw.rule(ctx, port="22", protocol="tcp", from_ip=from_ip)]
    assert not ctx.changed
    assert commands == []


async def test_rule_v6_twin_is_ignored_for_v4_match():
    """An address-less rule matches its IPv4 row only -- the stored (v6)
    twin ufw maintains never counts as a duplicate."""
    host = SimulatedHost(
        name="h", ufw_rules=[rule_row(number=1), rule_row(number=2, v6=True)]
    )
    ctx = make_ctx(host)
    commands = [c async for c in ufw.rule(ctx, port=22, protocol="tcp")]
    assert not ctx.changed
    assert commands == []


async def test_rule_v6_address_matches_v6_rows():
    """A rule naming an IPv6 address is v6-only and matches the (v6) rows."""
    host = SimulatedHost(
        name="h",
        ufw_rules=[rule_row(v6=True, from_addr="2001:db8::/32")],
    )
    ctx = make_ctx(host)
    commands = [
        c async for c in ufw.rule(ctx, port=22, protocol="tcp", from_ip="2001:db8::/32")
    ]
    assert not ctx.changed
    assert commands == []


async def test_rule_v6_address_absent_adds():
    """With only a v4 row stored, an IPv6-addressed rule is a new rule."""
    host = SimulatedHost(name="h", ufw_rules=[rule_row()])
    ctx = make_ctx(host)
    commands = [
        c async for c in ufw.rule(ctx, port=22, protocol="tcp", from_ip="2001:db8::/32")
    ]
    assert ctx.changed
    assert commands == [
        UfwRuleCommand(port="22", protocol="tcp", from_ip="2001:db8::/32")
    ]


async def test_rule_v6_any_address_matches_twin():
    """``::/0`` is the IPv6 any-address: it normalizes to Anywhere but stays
    v6-only, so it matches the stored (v6) twin, not the v4 row."""
    host = SimulatedHost(
        name="h", ufw_rules=[rule_row(number=1), rule_row(number=2, v6=True)]
    )
    ctx = make_ctx(host)
    commands = [c async for c in ufw.rule(ctx, port=22, protocol="tcp", from_ip="::/0")]
    assert not ctx.changed
    assert commands == []


async def test_rule_multiport_tuple():
    """A tuple port renders as one comma multiport and matches the stored
    token verbatim."""
    host = SimulatedHost(name="h", ufw_rules=[rule_row(port="80,443")])
    ctx = make_ctx(host)
    commands = [c async for c in ufw.rule(ctx, port=(80, 443), protocol="tcp")]
    assert not ctx.changed
    host = SimulatedHost(name="h")
    ctx = make_ctx(host)
    commands = [c async for c in ufw.rule(ctx, port=(80, 443), protocol="tcp")]
    assert commands == [UfwRuleCommand(port="80,443", protocol="tcp")]


async def test_rule_app_profile():
    """An app rule matches the profile row and adds with the app grammar."""
    host = SimulatedHost(
        name="h", ufw_rules=[rule_row(port="", protocol="", app="Nginx Full")]
    )
    ctx = make_ctx(host)
    commands = [c async for c in ufw.rule(ctx, app="Nginx Full")]
    assert not ctx.changed
    ctx = make_ctx(host)
    commands = [c async for c in ufw.rule(ctx, app="Nginx HTTP")]
    assert ctx.changed
    assert commands == [UfwRuleCommand(app="Nginx HTTP")]


async def test_rule_route_matches_fwd_row():
    """A route rule matches the FWD row with its incoming interface."""
    host = SimulatedHost(
        name="h",
        ufw_rules=[
            rule_row(
                direction="FWD", port="80", from_addr="192.168.1.0/24",
                interface="eth1",
            )
        ],
    )
    ctx = make_ctx(host)
    commands = [
        c async for c in ufw.rule(
            ctx, port=80, protocol="tcp", route=True,
            from_ip="192.168.1.0/24", interface="eth1",
        )
    ]
    assert not ctx.changed
    assert commands == []


async def test_rule_log_and_comment_are_identity_fields():
    """A log or comment difference is a different rule -- the add yields."""
    host = SimulatedHost(name="h", ufw_rules=[rule_row()])
    ctx = make_ctx(host)
    commands = [c async for c in ufw.rule(ctx, port=22, protocol="tcp", log=True)]
    assert ctx.changed
    assert len(commands) == 1
    ctx = make_ctx(host)
    commands = [
        c async for c in ufw.rule(ctx, port=22, protocol="tcp", comment="different")
    ]
    assert ctx.changed
    assert len(commands) == 1


async def test_rule_protocol_only_and_out_direction():
    """A protocol-only rule and an out rule with an interface each match
    their parsed rows."""
    host = SimulatedHost(name="h", ufw_rules=[rule_row(port="")])
    ctx = make_ctx(host)
    commands = [c async for c in ufw.rule(ctx, protocol="tcp")]
    assert not ctx.changed
    assert commands == []
    host = SimulatedHost(
        name="h",
        ufw_rules=[rule_row(direction="OUT", port="25", interface="eth1")],
    )
    ctx = make_ctx(host)
    commands = [
        c async for c in ufw.rule(
            ctx, port=25, protocol="tcp", direction="out", interface="eth1"
        )
    ]
    assert not ctx.changed
    assert commands == []


async def test_rule_kwargs_absorbed():
    host = SimulatedHost(name="h")
    ctx = make_ctx(host)
    commands = [
        c async for c in ufw.rule(ctx, port=22, protocol="tcp", some_global="ignored")
    ]
    assert commands == [UfwRuleCommand(port="22", protocol="tcp")]


# --- ufw.rule: delete ----------------------------------------------------------------------------


async def test_rule_delete_matching():
    """A matching row yields one --force delete descriptor (no insert)."""
    host = SimulatedHost(name="h", ufw_rules=[rule_row(comment="Allow SSH")])
    ctx = make_ctx(host)
    commands = [c async for c in ufw.rule(ctx, port=22, protocol="tcp", present=False)]
    assert ctx.changed
    assert ctx.reason == "ufw rule will be deleted"
    assert commands == [UfwRuleCommand(port="22", protocol="tcp", present=False)]


async def test_rule_delete_absent_noop():
    host = SimulatedHost(name="h")
    ctx = make_ctx(host)
    commands = [c async for c in ufw.rule(ctx, port=22, protocol="tcp", present=False)]
    assert not ctx.changed
    assert ctx.reason == "ufw rule does not exist"
    assert commands == []


async def test_rule_delete_comment_agnostic():
    """A deletion with no comment matches any stored comment (ufw's own
    comment-less delete ignores the comment)."""
    host = SimulatedHost(name="h", ufw_rules=[rule_row(comment="Allow SSH")])
    ctx = make_ctx(host)
    commands = [c async for c in ufw.rule(ctx, port=22, protocol="tcp", present=False)]
    assert ctx.changed
    assert len(commands) == 1


async def test_rule_delete_comment_exact():
    """A named comment must match exactly."""
    host = SimulatedHost(name="h", ufw_rules=[rule_row(comment="Allow SSH")])
    ctx = make_ctx(host)
    commands = [
        c async for c in ufw.rule(
            ctx, port=22, protocol="tcp", present=False, comment="other"
        )
    ]
    assert not ctx.changed
    assert ctx.reason == "ufw rule does not exist"
    ctx = make_ctx(host)
    commands = [
        c async for c in ufw.rule(
            ctx, port=22, protocol="tcp", present=False, comment="Allow SSH"
        )
    ]
    assert ctx.changed
    assert commands == [
        UfwRuleCommand(port="22", protocol="tcp", comment="Allow SSH", present=False)
    ]


async def test_rule_delete_duplicates_yield_one_per_match():
    """ufw allows duplicates: N matching rows yield N delete descriptors
    (all sent within the one lockstep step)."""
    host = SimulatedHost(
        name="h",
        ufw_rules=[
            rule_row(number=1),
            rule_row(number=2),
            rule_row(number=3, v6=True),
            rule_row(number=4, v6=True),
        ],
    )
    ctx = make_ctx(host)
    commands = [c async for c in ufw.rule(ctx, port=22, protocol="tcp", present=False)]
    assert ctx.changed
    assert ctx.reason == "ufw rule will be deleted (2 matches)"
    assert len(commands) == 2
    assert all(c.present is False for c in commands)


# --- ufw.rule: insert ----------------------------------------------------------------------------


async def test_rule_insert_positions_new_rule():
    host = SimulatedHost(name="h")
    ctx = make_ctx(host)
    commands = [
        c async for c in ufw.rule(ctx, port=23, protocol="tcp", action="deny", insert=1)
    ]
    assert ctx.changed
    assert commands == [
        UfwRuleCommand(action="deny", port="23", protocol="tcp", insert=1)
    ]


async def test_rule_insert_never_repositions_existing():
    """An existing rule at a different position is left alone -- reordering
    live rules is destructive, so insert positions a NEW rule only."""
    host = SimulatedHost(name="h", ufw_rules=[rule_row(number=2)])
    ctx = make_ctx(host)
    commands = [
        c async for c in ufw.rule(ctx, port=22, protocol="tcp", insert=1)
    ]
    assert not ctx.changed
    assert ctx.reason == "ufw rule exists"
    assert commands == []


# --- ufw.rule: validation ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"port": 22, "action": "drop"}, "action must be"),
        ({"port": 22, "direction": "forward"}, "direction must be"),
        ({"port": 22, "protocol": "icmp"}, "protocol must be"),
        ({"app": "Nginx Full", "port": 80}, "cannot combine app with port"),
        ({"app": "Nginx Full", "protocol": "tcp"}, "cannot combine app with protocol"),
        ({"port": (80, 443)}, "require a protocol"),
        ({"port": "8000:8010"}, "require a protocol"),
        ({"port": 80, "route": True, "direction": "out"}, "cannot use direction out"),
        ({"port": 80, "route": True, "log": True}, "cannot log"),
        ({"port": 22, "insert": 0}, "positive rule number"),
        ({}, "needs at least one"),
    ],
)
async def test_rule_validation(kwargs, match):
    host = SimulatedHost(name="h")
    ctx = make_ctx(host)
    with pytest.raises(ValueError, match=match):
        [c async for c in ufw.rule(ctx, **kwargs)]


# --- SimulatedHost seam dispatch ----------------------------------------------------------------


async def test_state_seam_enable_fills_installation_defaults():
    """The enable arm activates and fills the empty fields with the
    installation defaults a freshly enabled ufw reports."""
    host = SimulatedHost(name="h")
    await send_command(host, UfwStateCommand(enabled=True))
    assert host.ufw_status == status_record()
    await send_command(host, UfwStateCommand(enabled=False))
    assert host.ufw_status["active"] is False
    assert host.ufw_status["default_incoming"] == ""


async def test_state_seam_enable_keeps_seeded_policy():
    """A policy set before enabling survives the enable."""
    host = SimulatedHost(name="h")
    await send_command(host, UfwDefaultPolicyCommand(policy="reject"))
    await send_command(host, UfwStateCommand(enabled=True))
    assert host.ufw_status["default_incoming"] == "reject"
    assert host.ufw_status["default_outgoing"] == "allow"


async def test_default_policy_seam_each_direction():
    host = SimulatedHost(name="h", ufw_status=status_record())
    await send_command(host, UfwDefaultPolicyCommand(policy="reject", direction="routed"))
    assert host.ufw_status["default_routed"] == "reject"
    assert host.ufw_status["default_incoming"] == "deny"


async def test_logging_seam_on_semantics():
    """``on`` reads as low when off and keeps the current level otherwise."""
    host = SimulatedHost(name="h", ufw_status=status_record(logging="off"))
    await send_command(host, UfwLoggingCommand(level="on"))
    assert host.ufw_status["logging"] == "low"
    await send_command(host, UfwLoggingCommand(level="high"))
    assert host.ufw_status["logging"] == "high"
    await send_command(host, UfwLoggingCommand(level="on"))
    assert host.ufw_status["logging"] == "high"


async def test_reset_seam_clears_rules_and_status():
    host = SimulatedHost(
        name="h", ufw_status=status_record(), ufw_rules=[rule_row()]
    )
    await send_command(host, UfwResetCommand())
    assert host.ufw_rules == []
    assert host.ufw_status["active"] is False


async def test_rule_seam_add_then_readd_is_noop():
    """The add lands with its v6 twin; a fresh operation read sees the row
    and plans no change (the per-step cache property)."""
    host = SimulatedHost(name="h")
    ctx = make_ctx(host)
    commands = [c async for c in ufw.rule(ctx, port=22, protocol="tcp")]
    for cmd in commands:
        await send_command(host, cmd)
    assert len(host.ufw_rules) == 2
    assert [r["number"] for r in host.ufw_rules] == [1, 2]
    assert host.ufw_rules[0]["v6"] is False
    assert host.ufw_rules[1]["v6"] is True
    ctx = make_ctx(host)
    commands = [c async for c in ufw.rule(ctx, port=22, protocol="tcp")]
    assert commands == []


async def test_rule_seam_delete_removes_row_and_twin():
    """One delete descriptor removes the v4 row and its v6 twin, and the
    remaining rows renumber."""
    host = SimulatedHost(name="h")
    for port in ("22", "80"):
        await send_command(host, UfwRuleCommand(port=port, protocol="tcp"))
    assert len(host.ufw_rules) == 4
    await send_command(host, UfwRuleCommand(port="22", protocol="tcp", present=False))
    assert [r["port"] for r in host.ufw_rules] == ["80", "80"]
    assert [r["number"] for r in host.ufw_rules] == [1, 2]


async def test_rule_seam_insert_renumbers():
    """An insert at position 1 lands first and renumbers the rest."""
    host = SimulatedHost(name="h")
    await send_command(host, UfwRuleCommand(port="22", protocol="tcp"))
    await send_command(
        host, UfwRuleCommand(action="deny", port="23", protocol="tcp", insert=1)
    )
    assert host.ufw_rules[0]["port"] == "23"
    assert host.ufw_rules[0]["action"] == "DENY"
    assert [r["number"] for r in host.ufw_rules] == [1, 2, 3, 4]


async def test_rule_seam_v6_specific_add_is_single_row():
    """An IPv6-addressed rule adds no v4 twin."""
    host = SimulatedHost(name="h")
    await send_command(
        host, UfwRuleCommand(port="22", protocol="tcp", from_ip="2001:db8::/32")
    )
    assert len(host.ufw_rules) == 1
    assert host.ufw_rules[0]["v6"] is True
    assert host.ufw_rules[0]["from_addr"] == "2001:db8::/32"


async def test_rule_seam_duplicates_all_deleted():
    """The operation yields one delete per duplicate; sending them all
    clears every matching row and twin."""
    host = SimulatedHost(name="h")
    await send_command(host, UfwRuleCommand(port="22", protocol="tcp"))
    await send_command(host, UfwRuleCommand(port="22", protocol="tcp"))
    assert len(host.ufw_rules) == 4
    ctx = make_ctx(host)
    commands = [c async for c in ufw.rule(ctx, port=22, protocol="tcp", present=False)]
    assert len(commands) == 2
    for cmd in commands:
        await send_command(host, cmd)
    assert host.ufw_rules == []


# --- End to end: run_prepare + run_apply --------------------------------------------------------


async def test_ufw_baseline_end_to_end():
    """The documented baseline: enable, deny incoming, allow SSH, allow the
    web multiport from a subnet. The plan shows every step changed (the
    prepare cache persists); the apply log shows the default policy no-op
    once the enable step has run; a second prepare no-ops everything."""

    def baseline_deploy(host):
        yield op(ufw.state, name="Enable ufw", enabled=True, sudo=True)
        yield op(
            ufw.default_policy,
            name="Deny incoming",
            direction="incoming",
            policy="deny",
            sudo=True,
        )
        yield op(
            ufw.rule,
            name="Allow SSH",
            port=22,
            protocol="tcp",
            comment="Allow SSH",
            sudo=True,
        )
        yield op(
            ufw.rule,
            name="Allow web from the LAN",
            port=(80, 443),
            protocol="tcp",
            from_ip="192.168.1.0/24",
            sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], baseline_deploy)
    assert [p.changed for p in plans["h"]] == [True, True, True, True]
    report = await run_apply(runtimes, plans=plans)
    assert [entry.changed for entry in report.log] == [True, False, True, True]
    log = format_log(report)
    assert "$ sudo ufw --force enable" in log
    assert (
        "$ sudo ufw allow in proto tcp from any to any port 22 comment 'Allow SSH'"
        in log
    )
    assert (
        "$ sudo ufw allow in proto tcp from 192.168.1.0/24 to any port 80,443" in log
    )
    assert host.ufw_status["active"] is True
    assert len(host.ufw_rules) == 4
    _runtimes, plans2 = await run_prepare([host], baseline_deploy)
    assert [p.changed for p in plans2["h"]] == [False, False, False, False]
    assert plans2["h"][0].reason == "ufw is already active"
    assert plans2["h"][1].reason == "ufw default incoming policy is already deny"
    assert plans2["h"][2].reason == "ufw rule exists"
    assert plans2["h"][3].reason == "ufw rule exists"


async def test_rule_delete_end_to_end():
    """A stored commented rule deletes through the driver (comment-less
    delete ignores the comment); a second prepare reads it gone."""

    def cleanup_deploy(host):
        yield op(
            ufw.rule,
            name="Drop the SSH rule",
            port=22,
            protocol="tcp",
            present=False,
            sudo=True,
        )

    host = SimulatedHost(
        name="h",
        ufw_rules=[rule_row(comment="Allow SSH"), rule_row(number=2, v6=True)],
    )
    runtimes, plans = await run_prepare([host], cleanup_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.changed
    assert "$ sudo ufw --force delete allow in proto tcp from any to any port 22" in (
        format_log(report)
    )
    assert host.ufw_rules == []
    _runtimes, plans2 = await run_prepare([host], cleanup_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "ufw rule does not exist"


async def test_reset_end_to_end_stays_stateless():
    """The reset applies and clears the seam; a second prepare STILL plans
    it (the operation maps to no persistent state)."""

    def reset_deploy(host):
        yield op(ufw.reset, name="Reset ufw", sudo=True)

    host = SimulatedHost(
        name="h", ufw_status=status_record(), ufw_rules=[rule_row()]
    )
    runtimes, plans = await run_prepare([host], reset_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    assert "$ sudo ufw --force reset" in format_log(report)
    assert host.ufw_rules == []
    assert host.ufw_status["active"] is False
    _runtimes, plans2 = await run_prepare([host], reset_deploy)
    assert plans2["h"][0].changed


async def test_two_host_lockstep_enable():
    """Across two hosts the driver pairs the Nth request: both firewalls
    enable in the same step."""

    def enable_deploy(host):
        yield op(ufw.state, name="Enable ufw", enabled=True, sudo=True)

    hosts = [SimulatedHost(name="web1"), SimulatedHost(name="web2")]
    runtimes, plans = await run_prepare(hosts, enable_deploy)
    assert plans["web1"][0].changed
    assert plans["web2"][0].changed
    report = await run_apply(runtimes, plans=plans)
    assert len(report.log) == 2
    assert all(entry.changed for entry in report.log)
    for host in hosts:
        assert host.ufw_status["active"] is True


async def test_dry_run_plans_without_sending():
    """``dry=True`` stops after prepare: the change is planned (and renders
    in format_plan) but nothing runs, so the seam stays untouched."""

    def ssh_deploy(host):
        yield op(
            ufw.rule,
            name="Allow SSH",
            port=22,
            protocol="tcp",
            sudo=True,
        )

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        ssh_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert planned.commands == (UfwRuleCommand(port="22", protocol="tcp"),)
    assert "Allow SSH" in format_plan(plans)
    assert host.ufw_rules == []