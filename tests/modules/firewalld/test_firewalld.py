# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the firewalld module: ``FirewalldStatus``, ``FirewalldDefaultZone``,
``FirewalldZones``, ``FirewalldServices``, ``FirewalldIpSets`` and
``FirewalldPolicies``, plus all sixteen operations.

pyinfra ships no firewalld module, so every fact and operation here is a
reemote5 original with no upstream fixtures to replay. The facts are fed
realistic canned ``firewall-cmd`` output instead (the marked state/version
echo, ``--list-all-zones`` blocks including the tab-indented rich-rule
lines real daemons print, and the marker-separated ``--info-service`` /
``--info-ipset`` / ``--info-policy`` loops), plus the ``SimulatedHost``
seam projections and their copy-on-read semantics.

The operations are exercised over their descriptor rendering (including
the ``shlex`` quoting of rich rules and the ``--change-interface`` special
case), their fact-backed idempotence in both configuration scopes
(permanent-first dual-apply, runtime-only, missing-zone skip-with-reason),
the static ``ValueError`` guards, the ``SimulatedHost`` seam dispatch
(including the reload scope copies and the interface rebind), and end to
end through ``run_prepare`` + ``run_apply`` and ``run`` (change-then-noop,
the zone-then-reload composition, lockstep across hosts, the persistent
prepare cache versus the fresh apply cache, a dry run and a
``DeterminismError`` guard).

``asyncio_mode = "auto"`` lets the async tests below run without decorators.
"""

import shlex
from dataclasses import dataclass

import pytest

from reemote5.commands import Command
from reemote5.core import FactCache, OpContext, op
from reemote5.drivers import (
    DeterminismError,
    command_shell,
    format_log,
    format_plan,
    gather_fact,
    run,
    run_apply,
    run_prepare,
    send_command,
)
from reemote5.hosts import Host
from reemote5.modules import firewalld
from reemote5.modules.firewalld.firewalld import (
    FirewalldDefaultZone,
    FirewalldDefaultZoneCommand,
    FirewalldIpSet,
    FirewalldIpsetCommand,
    FirewalldIpsetEntryCommand,
    FirewalldIpSets,
    FirewalldLockdownCommand,
    FirewalldMasqueradeCommand,
    FirewalldPanicCommand,
    FirewalldPolicies,
    FirewalldPolicy,
    FirewalldReloadCommand,
    FirewalldService,
    FirewalldServiceCommand,
    FirewalldServiceEntryCommand,
    FirewalldServices,
    FirewalldServiceTextCommand,
    FirewalldStatus,
    FirewalldStatusInfo,
    FirewalldZone,
    FirewalldZoneCommand,
    FirewalldZoneEntryCommand,
    FirewalldZones,
    FirewalldZoneTargetCommand,
    FirewalldZoneTextCommand,
    _FirewalldLockdown,
    _FirewalldPanicMode,
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


def zone_record(**overrides) -> FirewalldZone:
    """A fully-shaped zone record with the defaults a fresh zone carries."""
    record = FirewalldZone(
        active=False,
        target="default",
        short="",
        description="",
        icmp_block_inversion=False,
        interfaces=[],
        sources=[],
        services=[],
        ports=[],
        protocols=[],
        forward=False,
        masquerade=False,
        forward_ports=[],
        source_ports=[],
        icmp_blocks=[],
        rich_rules=[],
    )
    record.update(overrides)
    return record


def seeded_host(**kwargs) -> SimulatedHost:
    """A host with a ``public`` zone in BOTH configuration scopes."""
    return SimulatedHost(
        name="h",
        firewalld_zones={
            "public": zone_record(active=True, interfaces=["eth0"], services=["ssh"])
        },
        firewalld_permanent_zones={"public": zone_record(services=["ssh"])},
        **kwargs,
    )


async def materialize_op(host, operation, **kwargs):
    """Run one operation generator against a fresh prepare-phase cache."""
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in operation(ctx, **kwargs)]
    return ctx, commands


# --- FirewalldStatus -----------------------------------------------------------

STATUS_OUTPUT = "state: running\nversion: 2.1.1\n"


async def test_status_parses_the_marked_output():
    """One shell round-trip gathers both answers behind the echo markers."""
    host = CannedShellHost(STATUS_OUTPUT)
    assert await FirewalldStatus.process(host) == FirewalldStatusInfo(
        running=True, version="2.1.1"
    )
    assert host.last_shell == FirewalldStatus.COMMAND


async def test_status_stopped_daemon():
    """``not running`` reads False; an empty guarded read reads the defaults."""
    assert await FirewalldStatus.process(
        CannedShellHost("state: not running\nversion: \n")
    ) == FirewalldStatusInfo(running=False, version="")
    assert await FirewalldStatus.process(CannedShellHost("")) == FirewalldStatusInfo(
        running=False, version=""
    )
    assert FirewalldStatus.parse(["garbage", "state: running"]) == FirewalldStatusInfo(
        running=True, version=""
    )


async def test_status_seam_is_copied_on_read():
    """The default seam reads a stopped daemon; mutating the returned dict
    never mutates the simulated state."""
    host = SimulatedHost(name="h")
    assert await FirewalldStatus.process(host) == {"running": False, "version": ""}
    host.firewalld_status = FirewalldStatusInfo(running=True, version="2.1.1")
    result = await FirewalldStatus.process(host)
    result["running"] = False
    assert host.firewalld_status == {"running": True, "version": "2.1.1"}


# --- FirewalldDefaultZone --------------------------------------------------------


async def test_default_zone_parses_the_first_non_empty_line():
    host = CannedShellHost("\npublic\n")
    assert await FirewalldDefaultZone.process(host) == "public"
    assert host.last_shell == FirewalldDefaultZone.COMMAND


async def test_default_zone_empty_output_reads_as_empty_string():
    """A stopped daemon or an unprivileged read prints nothing: ""."""
    assert await FirewalldDefaultZone.process(CannedShellHost("")) == ""
    assert FirewalldDefaultZone.parse(["", "  "]) == ""


async def test_default_zone_seam():
    host = SimulatedHost(name="h", firewalld_default_zone="dmz")
    assert await FirewalldDefaultZone.process(host) == "dmz"
    assert await FirewalldDefaultZone.process(SimulatedHost(name="h")) == ""


# --- FirewalldZones ---------------------------------------------------------------

# Realistic --list-all-zones output: an active public zone with a
# tab-indented rich rule (real firewall-cmd prints rich rules with a tab)
# and an inactive custom dmz zone with a DROP target and display text.
RICH_RULE = 'rule family="ipv4" source address="10.0.0.0/8" accept'

ZONES_OUTPUT = (
    "public (active)\n"
    "  target: default\n"
    "  icmp-block-inversion: no\n"
    "  interfaces: eth0\n"
    "  sources:\n"
    "  services: ssh dhcpv6-client\n"
    "  ports: 80/tcp 443/tcp\n"
    "  protocols:\n"
    "  forward: yes\n"
    "  masquerade: no\n"
    "  forward-ports: port=80:proto=tcp:toport=8080:toaddr=\n"
    "  source-ports:\n"
    "  icmp-blocks:\n"
    "  rich rules:\n"
    f"\t{RICH_RULE}\n"
    "dmz\n"
    "  target: DROP\n"
    "  summary: DMZ\n"
    "  description: Demilitarized zone\n"
    "  icmp-block-inversion: no\n"
    "  interfaces:\n"
    "  sources: 192.168.100.0/24\n"
    "  services:\n"
    "  ports:\n"
    "  protocols:\n"
    "  forward: no\n"
    "  masquerade: yes\n"
    "  forward-ports:\n"
    "  source-ports:\n"
    "  icmp-blocks: echo-request\n"
    "  rich rules:\n"
)

ZONES_FACT = {
    "public": zone_record(
        active=True,
        interfaces=["eth0"],
        services=["ssh", "dhcpv6-client"],
        ports=["80/tcp", "443/tcp"],
        forward=True,
        forward_ports=["port=80:proto=tcp:toport=8080:toaddr="],
        rich_rules=[RICH_RULE],
    ),
    "dmz": zone_record(
        target="DROP",
        short="DMZ",
        description="Demilitarized zone",
        sources=["192.168.100.0/24"],
        masquerade=True,
        icmp_blocks=["echo-request"],
    ),
}


def test_zones_command_scopes():
    """The runtime shell drops the placeholder; the permanent one fills it."""
    assert FirewalldZones.command() == (
        "firewall-cmd --list-all-zones 2> /dev/null "
        "|| sudo -n firewall-cmd --list-all-zones 2> /dev/null || true"
    )
    assert FirewalldZones.command(True) == FirewalldZones.COMMAND.replace(
        "<permanent>", "--permanent "
    )
    assert FirewalldZones.command(False) == FirewalldZones.COMMAND.replace(
        "<permanent>", ""
    )


async def test_zones_parses_the_blocks():
    """Every detail field lands in the record; the ``(active)`` marker and
    the tab-indented rich rule survive."""
    host = CannedShellHost(ZONES_OUTPUT)
    assert await FirewalldZones.process(host) == ZONES_FACT
    assert host.last_shell == FirewalldZones.command()


async def test_zones_permanent_scope_uses_the_permanent_shell():
    host = CannedShellHost(ZONES_OUTPUT)
    await FirewalldZones.process(host, permanent=True)
    assert host.last_shell == FirewalldZones.command(True)


async def test_zones_empty_output_reads_as_empty_dict():
    assert await FirewalldZones.process(CannedShellHost("")) == {}
    assert FirewalldZones.parse(["", "  "]) == {}


async def test_zones_seam_is_copied_on_read():
    """Mutating a returned record's member lists never mutates the state."""
    host = seeded_host()
    result = await FirewalldZones.process(host)
    result["public"]["services"].append("http")
    result["public"]["target"] = "DROP"
    assert host.firewalld_zones["public"]["services"] == ["ssh"]
    assert host.firewalld_zones["public"]["target"] == "default"
    perm = await FirewalldZones.process(host, permanent=True)
    assert perm["public"]["active"] is False


# --- FirewalldServices -------------------------------------------------------------

SERVICES_OUTPUT = (
    "service: http\n"
    "http\n"
    "  short: WWW (HTTP)\n"
    "  description: HTTP is the protocol used to serve Web pages.\n"
    "  ports: 80/tcp\n"
    "  source-ports:\n"
    "  modules:\n"
    "  destination:\n"
    "service: my-app\n"
    "my-app\n"
    "  short: My App\n"
    "  description:\n"
    "  ports: 8080/tcp 8443/tcp\n"
    "  source-ports: 5000/udp\n"
    "  protocols: gre\n"
    "  modules: nf_conntrack_ftp\n"
    "  destination: ipv4:192.168.1.0/24 ipv6:::1\n"
)

SERVICES_FACT = {
    "http": FirewalldService(
        short="WWW (HTTP)",
        description="HTTP is the protocol used to serve Web pages.",
        ports=["80/tcp"],
        source_ports=[],
        protocols=[],
        modules=[],
        destination={},
    ),
    "my-app": FirewalldService(
        short="My App",
        description="",
        ports=["8080/tcp", "8443/tcp"],
        source_ports=["5000/udp"],
        protocols=["gre"],
        modules=["nf_conntrack_ftp"],
        destination={"ipv4": "192.168.1.0/24", "ipv6": "::1"},
    ),
}


async def test_services_parses_the_marker_loop():
    """The unindented name echo is skipped; the destination tokens split
    on the FIRST colon so an IPv6 address survives."""
    host = CannedShellHost(SERVICES_OUTPUT)
    assert await FirewalldServices.process(host) == SERVICES_FACT
    assert host.last_shell == FirewalldServices.COMMAND


async def test_services_empty_output_reads_as_empty_dict():
    assert await FirewalldServices.process(CannedShellHost("")) == {}


async def test_services_seam_is_copied_on_read():
    host = SimulatedHost(name="h", firewalld_services=dict(SERVICES_FACT))
    result = await FirewalldServices.process(host)
    result["my-app"]["ports"].append("9090/tcp")
    assert host.firewalld_services["my-app"]["ports"] == ["8080/tcp", "8443/tcp"]


# --- FirewalldIpSets -----------------------------------------------------------------

IPSETS_OUTPUT = (
    "ipset: blocked\n"
    "blocked\n"
    "  type: hash:ip\n"
    "  options: family=inet maxelem=65536\n"
    "  entries: 203.0.113.50 198.51.100.25\n"
    "ipset: nets\n"
    "nets\n"
    "  type: hash:net\n"
    "  options: family=inet6\n"
    "  entries: 2001:db8::/32\n"
)

IPSETS_FACT = {
    "blocked": FirewalldIpSet(
        type="hash:ip",
        options={"family": "inet", "maxelem": "65536"},
        entries=["203.0.113.50", "198.51.100.25"],
    ),
    "nets": FirewalldIpSet(
        type="hash:net",
        options={"family": "inet6"},
        entries=["2001:db8::/32"],
    ),
}


def test_ipsets_command_scopes():
    assert FirewalldIpSets.command() == FirewalldIpSets.COMMAND.replace(
        "<permanent>", ""
    )
    assert FirewalldIpSets.command(True) == FirewalldIpSets.COMMAND.replace(
        "<permanent>", "--permanent "
    )


async def test_ipsets_parses_the_marker_loop():
    host = CannedShellHost(IPSETS_OUTPUT)
    assert await FirewalldIpSets.process(host) == IPSETS_FACT
    assert host.last_shell == FirewalldIpSets.command()


async def test_ipsets_permanent_scope():
    host = CannedShellHost(IPSETS_OUTPUT)
    await FirewalldIpSets.process(host, permanent=True)
    assert host.last_shell == FirewalldIpSets.command(True)


async def test_ipsets_empty_output_reads_as_empty_dict():
    assert await FirewalldIpSets.process(CannedShellHost("")) == {}


async def test_ipsets_seam_is_copied_on_read():
    host = SimulatedHost(name="h", firewalld_ipsets=dict(IPSETS_FACT))
    result = await FirewalldIpSets.process(host)
    result["blocked"]["entries"].clear()
    result["blocked"]["options"]["maxelem"] = "1"
    assert host.firewalld_ipsets["blocked"]["entries"] == [
        "203.0.113.50",
        "198.51.100.25",
    ]
    assert host.firewalld_ipsets["blocked"]["options"]["maxelem"] == "65536"


# --- FirewalldPolicies ------------------------------------------------------------------

POLICIES_OUTPUT = (
    "policy: allow-internal\n"
    "allow-internal\n"
    "  target: CONTINUE\n"
    "  ingress-zones: public\n"
    "  egress-zones: internal\n"
    "  services: ssh\n"
    "  ports: 8080/tcp\n"
    "  protocols:\n"
    "  masquerade: no\n"
    "  forward-ports:\n"
    "  rich rules:\n"
)

POLICIES_FACT = {
    "allow-internal": FirewalldPolicy(
        target="CONTINUE",
        ingress_zones=["public"],
        egress_zones=["internal"],
        services=["ssh"],
        ports=["8080/tcp"],
        protocols=[],
        forward_ports=[],
        masquerade=False,
        rich_rules=[],
    )
}


async def test_policies_parses_the_marker_loop():
    host = CannedShellHost(POLICIES_OUTPUT)
    assert await FirewalldPolicies.process(host) == POLICIES_FACT
    assert host.last_shell == FirewalldPolicies.command()


async def test_policies_old_daemon_reads_as_empty_dict():
    """A daemon older than 0.9 does not know --get-policies: the || true
    guard masks its error, the loop body never runs, the fact reads {}."""
    assert await FirewalldPolicies.process(CannedShellHost("")) == {}


async def test_policies_permanent_scope():
    host = CannedShellHost(POLICIES_OUTPUT)
    await FirewalldPolicies.process(host, permanent=True)
    assert host.last_shell == FirewalldPolicies.command(True)


async def test_policies_seam_is_copied_on_read():
    host = SimulatedHost(name="h", firewalld_policies=dict(POLICIES_FACT))
    result = await FirewalldPolicies.process(host)
    result["allow-internal"]["ingress_zones"].append("dmz")
    assert host.firewalld_policies["allow-internal"]["ingress_zones"] == ["public"]


async def test_gather_fact_fans_out_over_hosts():
    """The read-only fan-out gathers both scopes independently per host."""
    h1 = SimulatedHost(name="h1", firewalld_default_zone="public")
    h2 = SimulatedHost(name="h2", firewalld_default_zone="dmz")
    assert await gather_fact([h1, h2], FirewalldDefaultZone) == {
        "h1": "public",
        "h2": "dmz",
    }


# --- Private probes -------------------------------------------------------------------


async def test_lockdown_probe_parses_yes_no():
    assert _FirewalldLockdown.parse(["yes"]) is True
    assert _FirewalldLockdown.parse(["no"]) is False
    assert _FirewalldLockdown.parse([]) is False
    host = CannedShellHost("yes\n")
    assert await _FirewalldLockdown.process(host) is True
    assert host.last_shell == _FirewalldLockdown.COMMAND


async def test_panic_probe_parses_yes_no():
    assert _FirewalldPanicMode.parse(["yes"]) is True
    assert _FirewalldPanicMode.parse(["no"]) is False
    host = SimulatedHost(name="h", firewalld_panic_mode=True)
    assert await _FirewalldPanicMode.process(host) is True
    assert await _FirewalldLockdown.process(SimulatedHost(name="h")) is False


# --- Descriptor rendering ---------------------------------------------------------------


def test_zone_command_renders_existence():
    assert FirewalldZoneCommand(zone="dmz").shell() == (
        "firewall-cmd --permanent --new-zone=dmz"
    )
    assert FirewalldZoneCommand(zone="dmz", present=False).shell() == (
        "firewall-cmd --permanent --delete-zone=dmz"
    )
    assert FirewalldZoneCommand(zone="my zone").shell() == (
        "firewall-cmd --permanent --new-zone='my zone'"
    )


def test_zone_target_and_text_commands_render_both_scopes():
    assert FirewalldZoneTargetCommand(zone="dmz", target="DROP").shell() == (
        "firewall-cmd --permanent --zone=dmz --set-target=DROP"
    )
    assert FirewalldZoneTargetCommand(
        zone="dmz", target="DROP", permanent=False
    ).shell() == "firewall-cmd --zone=dmz --set-target=DROP"
    assert FirewalldZoneTextCommand(zone="dmz", kind="short", text="DMZ").shell() == (
        "firewall-cmd --permanent --zone=dmz --set-short=DMZ"
    )
    assert FirewalldZoneTextCommand(
        zone="dmz", kind="description", text="Edge zone", permanent=False
    ).shell() == "firewall-cmd --zone=dmz --set-description='Edge zone'"


def test_zone_entry_command_renders_every_kind():
    assert FirewalldZoneEntryCommand(
        zone="public", kind="service", value="http"
    ).shell() == "firewall-cmd --permanent --zone=public --add-service=http"
    assert FirewalldZoneEntryCommand(
        zone="public", kind="port", value="8080/tcp", present=False, permanent=False
    ).shell() == "firewall-cmd --zone=public --remove-port=8080/tcp"
    # The interface special case: present rebinds via --change-interface.
    assert FirewalldZoneEntryCommand(
        zone="dmz", kind="interface", value="eth1"
    ).shell() == "firewall-cmd --permanent --zone=dmz --change-interface=eth1"
    assert FirewalldZoneEntryCommand(
        zone="dmz", kind="interface", value="eth1", present=False
    ).shell() == "firewall-cmd --permanent --zone=dmz --remove-interface=eth1"
    assert FirewalldZoneEntryCommand(
        zone="public", kind="rich-rule", value=RICH_RULE
    ).shell() == (
        f"firewall-cmd --permanent --zone=public --add-rich-rule={shlex.quote(RICH_RULE)}"
    )


def test_masquerade_command_renders_both_directions():
    assert FirewalldMasqueradeCommand(zone="public").shell() == (
        "firewall-cmd --permanent --zone=public --add-masquerade"
    )
    assert FirewalldMasqueradeCommand(
        zone="public", enabled=False, permanent=False
    ).shell() == "firewall-cmd --zone=public --remove-masquerade"


def test_service_commands_render():
    assert FirewalldServiceCommand(service="my-app").shell() == (
        "firewall-cmd --permanent --new-service=my-app"
    )
    assert FirewalldServiceCommand(service="my-app", present=False).shell() == (
        "firewall-cmd --permanent --delete-service=my-app"
    )
    assert FirewalldServiceEntryCommand(
        service="my-app", kind="port", value="8080/tcp"
    ).shell() == "firewall-cmd --permanent --service=my-app --add-port=8080/tcp"
    assert FirewalldServiceEntryCommand(
        service="my-app", kind="module", value="nf_conntrack_ftp", present=False
    ).shell() == (
        "firewall-cmd --permanent --service=my-app --remove-module=nf_conntrack_ftp"
    )
    assert FirewalldServiceTextCommand(
        service="my-app", kind="short", text="My App"
    ).shell() == "firewall-cmd --permanent --service=my-app --set-short='My App'"


def test_ipset_commands_render():
    assert FirewalldIpsetCommand(
        ipset_name="blocked",
        ipset_type="hash:ip",
        options=("family=inet", "maxelem=65536"),
    ).shell() == (
        "firewall-cmd --permanent --new-ipset=blocked --type=hash:ip "
        "--option=family=inet --option=maxelem=65536"
    )
    assert FirewalldIpsetCommand(ipset_name="blocked", present=False).shell() == (
        "firewall-cmd --permanent --delete-ipset=blocked"
    )
    assert FirewalldIpsetEntryCommand(
        ipset_name="blocked", entry="203.0.113.50"
    ).shell() == "firewall-cmd --permanent --ipset=blocked --add-entry=203.0.113.50"
    assert FirewalldIpsetEntryCommand(
        ipset_name="blocked", entry="203.0.113.50", present=False, permanent=False
    ).shell() == "firewall-cmd --ipset=blocked --remove-entry=203.0.113.50"


def test_scalar_commands_render():
    assert FirewalldDefaultZoneCommand(zone="dmz").shell() == (
        "firewall-cmd --set-default-zone=dmz"
    )
    assert FirewalldLockdownCommand().shell() == "firewall-cmd --lockdown-on"
    assert FirewalldLockdownCommand(enabled=False).shell() == (
        "firewall-cmd --lockdown-off"
    )
    assert FirewalldPanicCommand().shell() == "firewall-cmd --panic-on"
    assert FirewalldPanicCommand(enabled=False).shell() == "firewall-cmd --panic-off"


def test_reload_command_renders_every_mode():
    assert FirewalldReloadCommand().shell() == "firewall-cmd --reload"
    assert FirewalldReloadCommand(mode="complete_reload").shell() == (
        "firewall-cmd --complete-reload"
    )
    assert FirewalldReloadCommand(mode="runtime_to_permanent").shell() == (
        "firewall-cmd --runtime-to-permanent"
    )


def test_command_shell_elevates_the_descriptor():
    cmd = FirewalldZoneCommand(zone="dmz")
    assert command_shell(cmd) == "firewall-cmd --permanent --new-zone=dmz"
    assert command_shell(cmd, sudo=True) == (
        "sudo firewall-cmd --permanent --new-zone=dmz"
    )


# --- firewalld.zone -------------------------------------------------------------------


async def test_zone_creates_missing_zone_permanent_only():
    """Existence is permanent-only; the runtime copy appears after reload,
    so no runtime command is yielded for a zone being created."""
    ctx, commands = await materialize_op(seeded_host(), firewalld.zone, zone="dmz")
    assert ctx.changed
    assert commands == [FirewalldZoneCommand(zone="dmz")]
    assert ctx.reason == "zone dmz will be created (permanent)"


async def test_zone_create_with_properties_skips_runtime_with_reason():
    ctx, commands = await materialize_op(
        seeded_host(),
        firewalld.zone,
        zone="dmz",
        target="DROP",
        short="DMZ",
    )
    assert ctx.changed
    assert commands == [
        FirewalldZoneCommand(zone="dmz"),
        FirewalldZoneTargetCommand(zone="dmz", target="DROP", permanent=True),
        FirewalldZoneTextCommand(zone="dmz", kind="short", text="DMZ", permanent=True),
    ]
    assert "zone dmz target will be set to DROP (permanent)" in ctx.reason
    assert ctx.reason.count("zone dmz not found in runtime configuration") == 2


async def test_zone_property_change_dual_applies_permanent_first():
    ctx, commands = await materialize_op(
        seeded_host(), firewalld.zone, zone="public", target="DROP"
    )
    assert ctx.changed
    assert commands == [
        FirewalldZoneTargetCommand(zone="public", target="DROP", permanent=True),
        FirewalldZoneTargetCommand(zone="public", target="DROP", permanent=False),
    ]
    assert ctx.reason == (
        "zone public exists (permanent); "
        "zone public target will be set to DROP (permanent); "
        "zone public target will be set to DROP (runtime)"
    )


async def test_zone_already_in_state_is_a_noop():
    ctx, commands = await materialize_op(
        seeded_host(), firewalld.zone, zone="public", target="default"
    )
    assert not ctx.changed
    assert commands == []
    assert ctx.reason == (
        "zone public exists (permanent); "
        "zone public target already default (permanent); "
        "zone public target already default (runtime)"
    )


async def test_zone_runtime_only_property_change():
    ctx, commands = await materialize_op(
        seeded_host(), firewalld.zone, zone="public", target="DROP", permanent=False
    )
    assert ctx.changed
    assert commands == [
        FirewalldZoneTargetCommand(zone="public", target="DROP", permanent=False)
    ]


async def test_zone_delete_and_delete_absent():
    host = seeded_host()
    host.firewalld_permanent_zones["dmz"] = zone_record()
    ctx, commands = await materialize_op(host, firewalld.zone, zone="dmz", present=False)
    assert ctx.changed
    assert commands == [FirewalldZoneCommand(zone="dmz", present=False)]
    ctx, commands = await materialize_op(
        seeded_host(), firewalld.zone, zone="nosuch", present=False
    )
    assert not ctx.changed
    assert commands == []
    assert ctx.reason == "zone nosuch does not exist (permanent)"


async def test_zone_invalid_target_raises():
    with pytest.raises(ValueError, match="target must be one of"):
        await materialize_op(seeded_host(), firewalld.zone, zone="dmz", target="REJECT_ALL")


async def test_zone_absorbs_unknown_kwargs():
    ctx, _commands = await materialize_op(
        seeded_host(), firewalld.zone, zone="public", pyinfra_global=True
    )
    assert not ctx.changed


# --- Zone membership operations -----------------------------------------------------


async def test_zone_service_add_dual_applies():
    ctx, commands = await materialize_op(
        seeded_host(), firewalld.zone_service, zone="public", service="http"
    )
    assert ctx.changed
    assert commands == [
        FirewalldZoneEntryCommand(
            zone="public", kind="service", value="http", permanent=True
        ),
        FirewalldZoneEntryCommand(
            zone="public", kind="service", value="http", permanent=False
        ),
    ]
    assert ctx.reason == (
        "service http will be added to zone public (permanent); "
        "service http will be added to zone public (runtime)"
    )


async def test_zone_service_already_present_is_a_noop():
    ctx, commands = await materialize_op(
        seeded_host(), firewalld.zone_service, zone="public", service="ssh"
    )
    assert not ctx.changed
    assert commands == []
    assert ctx.reason == (
        "service ssh already in zone public (permanent); "
        "service ssh already in zone public (runtime)"
    )


async def test_zone_service_remove():
    ctx, commands = await materialize_op(
        seeded_host(),
        firewalld.zone_service,
        zone="public",
        service="ssh",
        present=False,
    )
    assert ctx.changed
    assert commands == [
        FirewalldZoneEntryCommand(
            zone="public", kind="service", value="ssh", present=False, permanent=True
        ),
        FirewalldZoneEntryCommand(
            zone="public", kind="service", value="ssh", present=False, permanent=False
        ),
    ]


async def test_membership_missing_zone_skips_with_reason():
    """A zone absent from a scope is skipped with an explicit reason, not
    a guaranteed-fail command -- create it with firewalld.zone first."""
    ctx, commands = await materialize_op(
        seeded_host(), firewalld.port, zone="nosuch", port="8080/tcp"
    )
    assert not ctx.changed
    assert commands == []
    assert ctx.reason == (
        "zone nosuch not found in permanent configuration; "
        "zone nosuch not found in runtime configuration"
    )


async def test_membership_zone_only_at_runtime_skips_permanent():
    host = SimulatedHost(
        name="h", firewalld_zones={"public": zone_record(active=True)}
    )
    ctx, commands = await materialize_op(
        host, firewalld.zone_service, zone="public", service="http"
    )
    assert ctx.changed
    assert commands == [
        FirewalldZoneEntryCommand(
            zone="public", kind="service", value="http", permanent=False
        )
    ]
    assert "zone public not found in permanent configuration" in ctx.reason


@pytest.mark.parametrize("bad_port", ["8080", "8080/icmp", "tcp/80", "70000/tcp", ""])
async def test_port_invalid_shape_raises(bad_port):
    with pytest.raises(ValueError, match="port must be"):
        await materialize_op(seeded_host(), firewalld.port, zone="public", port=bad_port)


async def test_port_range_is_valid():
    ctx, commands = await materialize_op(
        seeded_host(), firewalld.port, zone="public", port="5000-5100/udp"
    )
    assert ctx.changed
    assert commands[0] == FirewalldZoneEntryCommand(
        zone="public", kind="port", value="5000-5100/udp", permanent=True
    )


async def test_interface_renders_change_interface():
    ctx, commands = await materialize_op(
        seeded_host(), firewalld.interface, zone="public", interface="eth1"
    )
    assert ctx.changed
    assert commands[0].shell() == (
        "firewall-cmd --permanent --zone=public --change-interface=eth1"
    )


async def test_source_accepts_an_ipset_reference():
    ctx, commands = await materialize_op(
        seeded_host(), firewalld.source, zone="public", source="ipset:blocked"
    )
    assert ctx.changed
    assert commands[0] == FirewalldZoneEntryCommand(
        zone="public", kind="source", value="ipset:blocked", permanent=True
    )


async def test_rich_rule_exact_string_membership():
    """The rule is never parsed or rewritten: the exact string already in
    the fact no-ops, a differently-ordered rule is a different rule."""
    host = seeded_host()
    host.firewalld_zones["public"]["rich_rules"] = [RICH_RULE]
    host.firewalld_permanent_zones["public"]["rich_rules"] = [RICH_RULE]
    ctx, commands = await materialize_op(
        host, firewalld.rich_rule, zone="public", rule=RICH_RULE
    )
    assert not ctx.changed
    assert commands == []
    other = 'rule family="ipv4" accept source address="10.0.0.0/8"'
    ctx, commands = await materialize_op(
        host, firewalld.rich_rule, zone="public", rule=other
    )
    assert ctx.changed
    assert commands[0].value == other


async def test_protocol_and_icmp_block_membership():
    ctx, commands = await materialize_op(
        seeded_host(), firewalld.protocol, zone="public", protocol="esp"
    )
    assert ctx.changed
    assert commands[0].kind == "protocol"
    ctx, commands = await materialize_op(
        seeded_host(), firewalld.icmp_block, zone="public", icmp_type="echo-request"
    )
    assert ctx.changed
    assert commands[0] == FirewalldZoneEntryCommand(
        zone="public", kind="icmp-block", value="echo-request", permanent=True
    )


async def test_forward_port_builds_the_firewall_cmd_string():
    ctx, commands = await materialize_op(
        seeded_host(),
        firewalld.forward_port,
        zone="public",
        port="80",
        protocol="tcp",
        to_port="8080",
        to_addr="10.0.0.5",
    )
    assert ctx.changed
    assert commands[0].value == "port=80:proto=tcp:toport=8080:toaddr=10.0.0.5"
    ctx, commands = await materialize_op(
        seeded_host(),
        firewalld.forward_port,
        zone="public",
        port="80",
        protocol="tcp",
        to_addr="10.0.0.5",
    )
    assert commands[0].value == "port=80:proto=tcp:toaddr=10.0.0.5"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"port": "80/tcp", "protocol": "tcp", "to_port": "8080"}, "port must be"),
        ({"port": "80", "protocol": "icmp", "to_port": "8080"}, "protocol must be"),
        ({"port": "80", "protocol": "tcp", "to_port": "http"}, "to_port must be"),
        ({"port": "80", "protocol": "tcp"}, "at least one of to_port or to_addr"),
    ],
)
async def test_forward_port_validation(kwargs, message):
    with pytest.raises(ValueError, match=message):
        await materialize_op(
            seeded_host(), firewalld.forward_port, zone="public", **kwargs
        )


async def test_membership_runtime_only():
    ctx, commands = await materialize_op(
        seeded_host(),
        firewalld.port,
        zone="public",
        port="8080/tcp",
        permanent=False,
    )
    assert ctx.changed
    assert commands == [
        FirewalldZoneEntryCommand(
            zone="public", kind="port", value="8080/tcp", permanent=False
        )
    ]
    assert ctx.reason == "port 8080/tcp will be added to zone public (runtime)"# --- firewalld.masquerade -------------------------------------------------------------


async def test_masquerade_enable_dual_applies():
    ctx, commands = await materialize_op(
        seeded_host(), firewalld.masquerade, zone="public"
    )
    assert ctx.changed
    assert commands == [
        FirewalldMasqueradeCommand(zone="public", enabled=True, permanent=True),
        FirewalldMasqueradeCommand(zone="public", enabled=True, permanent=False),
    ]
    assert ctx.reason == (
        "masquerade will be enabled on zone public (permanent); "
        "masquerade will be enabled on zone public (runtime)"
    )


async def test_masquerade_disable_and_noop():
    host = seeded_host()
    host.firewalld_zones["public"]["masquerade"] = True
    host.firewalld_permanent_zones["public"]["masquerade"] = True
    ctx, commands = await materialize_op(host, firewalld.masquerade, zone="public")
    assert not ctx.changed
    assert commands == []
    assert ctx.reason == (
        "masquerade already enabled on zone public (permanent); "
        "masquerade already enabled on zone public (runtime)"
    )
    ctx, commands = await materialize_op(
        host, firewalld.masquerade, zone="public", enabled=False
    )
    assert ctx.changed
    assert commands[0] == FirewalldMasqueradeCommand(
        zone="public", enabled=False, permanent=True
    )


async def test_masquerade_missing_zone_skips():
    ctx, commands = await materialize_op(
        seeded_host(), firewalld.masquerade, zone="nosuch"
    )
    assert not ctx.changed
    assert commands == []
    assert "zone nosuch not found in permanent configuration" in ctx.reason


# --- firewalld.service ------------------------------------------------------------------


def service_record(**overrides) -> FirewalldService:
    record = FirewalldService(
        short="",
        description="",
        ports=[],
        source_ports=[],
        protocols=[],
        modules=[],
        destination={},
    )
    record.update(overrides)
    return record


async def test_service_creates_definition_with_sorted_members():
    ctx, commands = await materialize_op(
        seeded_host(),
        firewalld.service,
        service="my-app",
        ports=("8443/tcp", "8080/tcp"),
        short="My App",
    )
    assert ctx.changed
    assert commands == [
        FirewalldServiceCommand(service="my-app"),
        FirewalldServiceEntryCommand(service="my-app", kind="port", value="8080/tcp"),
        FirewalldServiceEntryCommand(service="my-app", kind="port", value="8443/tcp"),
        FirewalldServiceTextCommand(service="my-app", kind="short", text="My App"),
    ]
    assert ctx.reason == (
        "service my-app will be created; "
        "service my-app ports will be added: 8080/tcp, 8443/tcp; "
        "service my-app short will be set to My App"
    )


async def test_service_membership_is_additive():
    """Entries not listed are left alone; only the missing ones yield."""
    host = seeded_host(
        firewalld_services={"my-app": service_record(ports=["8080/tcp"])}
    )
    ctx, commands = await materialize_op(
        host,
        firewalld.service,
        service="my-app",
        ports=("8080/tcp", "9090/tcp"),
    )
    assert ctx.changed
    assert commands == [
        FirewalldServiceEntryCommand(service="my-app", kind="port", value="9090/tcp")
    ]
    assert ctx.reason == (
        "service my-app exists; service my-app ports will be added: 9090/tcp"
    )


async def test_service_already_in_state_is_a_noop():
    host = seeded_host(
        firewalld_services={
            "my-app": service_record(ports=["8080/tcp"], short="My App")
        }
    )
    ctx, commands = await materialize_op(
        host,
        firewalld.service,
        service="my-app",
        ports=("8080/tcp",),
        short="My App",
    )
    assert not ctx.changed
    assert commands == []
    assert ctx.reason == (
        "service my-app exists; service my-app ports already present; "
        "service my-app short already My App"
    )


async def test_service_delete_and_delete_absent():
    host = seeded_host(firewalld_services={"my-app": service_record()})
    ctx, commands = await materialize_op(
        host, firewalld.service, service="my-app", present=False
    )
    assert ctx.changed
    assert commands == [FirewalldServiceCommand(service="my-app", present=False)]
    ctx, commands = await materialize_op(
        seeded_host(), firewalld.service, service="my-app", present=False
    )
    assert not ctx.changed
    assert commands == []
    assert ctx.reason == "service my-app does not exist"


# --- firewalld.ipset ---------------------------------------------------------------------


async def test_ipset_creates_with_entries_permanent_first():
    """A created set's runtime copy appears after reload, so the runtime
    entry change is skipped with a reason."""
    ctx, commands = await materialize_op(
        seeded_host(),
        firewalld.ipset,
        ipset_name="blocked",
        entries=("203.0.113.50",),
        options=("family=inet",),
    )
    assert ctx.changed
    assert commands == [
        FirewalldIpsetCommand(
            ipset_name="blocked",
            ipset_type="hash:ip",
            options=("family=inet",),
        ),
        FirewalldIpsetEntryCommand(
            ipset_name="blocked", entry="203.0.113.50", permanent=True
        ),
    ]
    assert ctx.reason == (
        "ipset blocked will be created (hash:ip, permanent); "
        "ipset blocked entries will be added: 203.0.113.50 (permanent); "
        "ipset blocked not found in runtime configuration"
    )


async def test_ipset_entries_dual_apply_when_both_scopes_have_it():
    host = seeded_host(
        firewalld_ipsets={"blocked": FirewalldIpSet(type="hash:ip", options={}, entries=[])},
        firewalld_permanent_ipsets={
            "blocked": FirewalldIpSet(type="hash:ip", options={}, entries=[])
        },
    )
    ctx, commands = await materialize_op(
        host,
        firewalld.ipset,
        ipset_name="blocked",
        entries=("203.0.113.50", "198.51.100.25"),
    )
    assert ctx.changed
    assert commands == [
        FirewalldIpsetEntryCommand(
            ipset_name="blocked", entry="198.51.100.25", permanent=True
        ),
        FirewalldIpsetEntryCommand(
            ipset_name="blocked", entry="203.0.113.50", permanent=True
        ),
        FirewalldIpsetEntryCommand(
            ipset_name="blocked", entry="198.51.100.25", permanent=False
        ),
        FirewalldIpsetEntryCommand(
            ipset_name="blocked", entry="203.0.113.50", permanent=False
        ),
    ]


async def test_ipset_type_mismatch_raises():
    host = seeded_host(
        firewalld_permanent_ipsets={
            "blocked": FirewalldIpSet(type="hash:net", options={}, entries=[])
        }
    )
    with pytest.raises(ValueError, match="exists with type 'hash:net'"):
        await materialize_op(
            host, firewalld.ipset, ipset_name="blocked", ipset_type="hash:ip"
        )


async def test_ipset_remove_entries_keeps_the_definition():
    host = seeded_host(
        firewalld_ipsets={
            "blocked": FirewalldIpSet(
                type="hash:ip", options={}, entries=["203.0.113.50", "198.51.100.25"]
            )
        },
        firewalld_permanent_ipsets={
            "blocked": FirewalldIpSet(
                type="hash:ip", options={}, entries=["203.0.113.50", "198.51.100.25"]
            )
        },
    )
    ctx, commands = await materialize_op(
        host,
        firewalld.ipset,
        ipset_name="blocked",
        present=False,
        entries=("203.0.113.50",),
    )
    assert ctx.changed
    assert commands == [
        FirewalldIpsetEntryCommand(
            ipset_name="blocked", entry="203.0.113.50", present=False, permanent=True
        ),
        FirewalldIpsetEntryCommand(
            ipset_name="blocked", entry="203.0.113.50", present=False, permanent=False
        ),
    ]


async def test_ipset_delete_definition_and_delete_absent():
    host = seeded_host(
        firewalld_permanent_ipsets={
            "blocked": FirewalldIpSet(type="hash:ip", options={}, entries=[])
        }
    )
    ctx, commands = await materialize_op(
        host, firewalld.ipset, ipset_name="blocked", present=False
    )
    assert ctx.changed
    assert commands == [FirewalldIpsetCommand(ipset_name="blocked", present=False)]
    ctx, commands = await materialize_op(
        seeded_host(), firewalld.ipset, ipset_name="blocked", present=False
    )
    assert not ctx.changed
    assert commands == []
    assert ctx.reason == "ipset blocked does not exist (permanent)"


async def test_ipset_already_in_state_is_a_noop():
    host = seeded_host(
        firewalld_ipsets={
            "blocked": FirewalldIpSet(
                type="hash:ip", options={}, entries=["203.0.113.50"]
            )
        },
        firewalld_permanent_ipsets={
            "blocked": FirewalldIpSet(
                type="hash:ip", options={}, entries=["203.0.113.50"]
            )
        },
    )
    ctx, commands = await materialize_op(
        host, firewalld.ipset, ipset_name="blocked", entries=("203.0.113.50",)
    )
    assert not ctx.changed
    assert commands == []


# --- firewalld.default_zone / lockdown / panic / reload ------------------------------------


async def test_default_zone_set_and_noop():
    host = seeded_host(firewalld_default_zone="public")
    ctx, commands = await materialize_op(host, firewalld.default_zone, zone="dmz")
    assert ctx.changed
    assert commands == [FirewalldDefaultZoneCommand(zone="dmz")]
    assert ctx.reason == "default zone will be set to dmz"
    ctx, commands = await materialize_op(host, firewalld.default_zone, zone="public")
    assert not ctx.changed
    assert commands == []
    assert ctx.reason == "default zone is already public"


async def test_default_zone_empty_raises():
    with pytest.raises(ValueError, match="zone must not be empty"):
        await materialize_op(seeded_host(), firewalld.default_zone, zone="")


async def test_lockdown_on_off_and_noop():
    host = seeded_host()
    ctx, commands = await materialize_op(host, firewalld.lockdown)
    assert ctx.changed
    assert commands == [FirewalldLockdownCommand(enabled=True)]
    assert ctx.reason == "lockdown will be enabled"
    host.firewalld_lockdown = True
    ctx, commands = await materialize_op(host, firewalld.lockdown)
    assert not ctx.changed
    assert commands == []
    assert ctx.reason == "lockdown already enabled"
    ctx, commands = await materialize_op(host, firewalld.lockdown, enabled=False)
    assert ctx.changed
    assert commands == [FirewalldLockdownCommand(enabled=False)]


async def test_panic_on_off_and_noop():
    host = seeded_host()
    ctx, commands = await materialize_op(host, firewalld.panic)
    assert ctx.changed
    assert commands == [FirewalldPanicCommand(enabled=True)]
    assert ctx.reason == "panic mode will be enabled"
    host.firewalld_panic_mode = True
    ctx, commands = await materialize_op(host, firewalld.panic)
    assert not ctx.changed
    assert ctx.reason == "panic mode already enabled"
    ctx, commands = await materialize_op(host, firewalld.panic, enabled=False)
    assert ctx.changed
    assert commands == [FirewalldPanicCommand(enabled=False)]


@pytest.mark.parametrize("mode", ["reload", "complete_reload", "runtime_to_permanent"])
async def test_reload_is_stateless(mode):
    """Every request yields and reports a change -- no fact can cheaply
    answer "runtime and permanent are in sync"."""
    ctx, commands = await materialize_op(seeded_host(), firewalld.reload, mode=mode)
    assert ctx.changed
    assert commands == [FirewalldReloadCommand(mode=mode)]
    assert ctx.reason == f"firewalld {mode} will run"


async def test_reload_invalid_mode_raises():
    with pytest.raises(ValueError, match="mode must be"):
        await materialize_op(seeded_host(), firewalld.reload, mode="restart")


# --- SimulatedHost seam dispatch -------------------------------------------------------


async def test_seam_zone_create_lands_permanent_only():
    host = SimulatedHost(name="h")
    await send_command(host, FirewalldZoneCommand(zone="dmz"))
    assert "dmz" in host.firewalld_permanent_zones
    assert "dmz" not in host.firewalld_zones
    assert host.firewalld_permanent_zones["dmz"]["target"] == "default"


async def test_seam_interface_rebind_removes_from_other_zones():
    host = seeded_host()
    host.firewalld_zones["dmz"] = zone_record()
    await send_command(
        host,
        FirewalldZoneEntryCommand(
            zone="dmz", kind="interface", value="eth0", permanent=False
        ),
    )
    assert host.firewalld_zones["dmz"]["interfaces"] == ["eth0"]
    assert host.firewalld_zones["public"]["interfaces"] == []


async def test_seam_reload_copies_permanent_to_runtime_with_active_marker():
    host = SimulatedHost(name="h")
    await send_command(host, FirewalldZoneCommand(zone="dmz"))
    await send_command(
        host,
        FirewalldZoneEntryCommand(
            zone="dmz", kind="source", value="10.0.0.0/8", permanent=True
        ),
    )
    await send_command(host, FirewalldReloadCommand())
    assert host.firewalld_zones["dmz"]["active"] is True
    assert host.firewalld_zones["dmz"]["sources"] == ["10.0.0.0/8"]
    # The scopes never share mutable lists after the copy.
    host.firewalld_zones["dmz"]["sources"].append("192.168.0.0/16")
    assert host.firewalld_permanent_zones["dmz"]["sources"] == ["10.0.0.0/8"]


async def test_seam_runtime_to_permanent_copies_the_other_way():
    host = seeded_host()
    await send_command(
        host,
        FirewalldZoneEntryCommand(
            zone="public", kind="port", value="8080/tcp", permanent=False
        ),
    )
    await send_command(host, FirewalldReloadCommand(mode="runtime_to_permanent"))
    assert host.firewalld_permanent_zones["public"]["ports"] == ["8080/tcp"]
    assert host.firewalld_permanent_zones["public"]["active"] is False


async def test_seam_scalar_switches():
    host = SimulatedHost(name="h")
    await send_command(host, FirewalldDefaultZoneCommand(zone="dmz"))
    await send_command(host, FirewalldLockdownCommand(enabled=True))
    await send_command(host, FirewalldPanicCommand(enabled=True))
    assert host.firewalld_default_zone == "dmz"
    assert host.firewalld_lockdown is True
    assert host.firewalld_panic_mode is True


async def test_seam_unknown_descriptor_stays_a_hard_error():
    @dataclass(frozen=True)
    class _UnknownCommand(Command):
        def shell(self) -> str:
            return "true"

    with pytest.raises(TypeError, match="unknown command descriptor"):
        await send_command(SimulatedHost(name="h"), _UnknownCommand())


# --- End to end: run_prepare + run_apply ---------------------------------------------------


async def test_port_end_to_end_add_then_noop():
    """Through the driver: both scope commands execute under sudo and
    render in the log; a second prepare sees the port in both scopes."""

    def port_deploy(host):
        yield op(
            firewalld.port,
            name="Open 8080",
            zone="public",
            port="8080/tcp",
            sudo=True,
        )

    host = seeded_host()
    runtimes, plans = await run_prepare([host], port_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (
        FirewalldZoneEntryCommand(
            zone="public", kind="port", value="8080/tcp", permanent=True
        ),
        FirewalldZoneEntryCommand(
            zone="public", kind="port", value="8080/tcp", permanent=False
        ),
    )
    log = format_log(report)
    assert "$ sudo firewall-cmd --permanent --zone=public --add-port=8080/tcp" in log
    assert "$ sudo firewall-cmd --zone=public --add-port=8080/tcp" in log
    assert host.firewalld_permanent_zones["public"]["ports"] == ["8080/tcp"]
    assert host.firewalld_zones["public"]["ports"] == ["8080/tcp"]
    _runtimes, plans2 = await run_prepare([host], port_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == (
        "port 8080/tcp already in zone public (permanent); "
        "port 8080/tcp already in zone public (runtime)"
    )


async def test_zone_then_reload_composition():
    """The documented pairing: create the permanent zone, reload to
    materialize it at runtime. A second prepare no-ops the zone but the
    reload stays changed -- its deliberate statelessness."""

    def zone_deploy(host):
        yield op(
            firewalld.zone,
            name="Create dmz",
            zone="dmz",
            target="DROP",
            sudo=True,
        )
        yield op(firewalld.reload, name="Apply permanent config", sudo=True)

    host = seeded_host()
    runtimes, plans = await run_prepare([host], zone_deploy)
    assert plans["h"][0].changed
    assert plans["h"][1].changed
    report = await run_apply(runtimes, plans=plans)
    log = format_log(report)
    assert "$ sudo firewall-cmd --permanent --new-zone=dmz" in log
    assert "$ sudo firewall-cmd --permanent --zone=dmz --set-target=DROP" in log
    assert "$ sudo firewall-cmd --reload" in log
    assert host.firewalld_zones["dmz"]["target"] == "DROP"
    assert host.firewalld_zones["dmz"]["active"] is False
    _runtimes, plans2 = await run_prepare([host], zone_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][1].changed


async def test_prepare_cache_persists_apply_cache_is_fresh():
    """Two identical port requests both show changed in the PLAN (the
    prepare FactCache persists and prepare never sends); only the apply
    log, with its per-step cache recreation, shows the second as a no-op."""

    def twice_deploy(host):
        yield op(
            firewalld.port, name="Open 8080 (a)", zone="public", port="8080/tcp", sudo=True
        )
        yield op(
            firewalld.port, name="Open 8080 (b)", zone="public", port="8080/tcp", sudo=True
        )

    host = seeded_host()
    runtimes, plans = await run_prepare([host], twice_deploy)
    assert [p.changed for p in plans["h"]] == [True, True]
    report = await run_apply(runtimes, plans=plans)
    assert [e.changed for e in report.log] == [True, False]
    assert host.firewalld_zones["public"]["ports"] == ["8080/tcp"]


INVENTORY = [
    {"host": "web1", "connection": {}},
    {"host": "web2", "connection": {}},
]


def web_factory(entry: dict) -> Host:
    """``host_factory`` building SimulatedHosts with a public zone."""
    host = SimulatedHost.from_inventory(entry)
    host.firewalld_zones = {
        "public": zone_record(active=True, interfaces=["eth0"], services=["ssh"])
    }
    host.firewalld_permanent_zones = {"public": zone_record(services=["ssh"])}
    return host


async def test_membership_runs_across_hosts_in_lockstep():
    def web_deploy(host):
        yield op(
            firewalld.zone_service,
            name="Enable http",
            zone="public",
            service="http",
            sudo=True,
        )

    plans, report, hosts = await run(INVENTORY, web_deploy, host_factory=web_factory)
    assert format_plan(plans).splitlines() == [
        "web1: Enable http              [changed: service http will be added to zone public (permanent); service http will be added to zone public (runtime)]",
        "web2: Enable http              [changed: service http will be added to zone public (permanent); service http will be added to zone public (runtime)]",
    ]
    assert report is not None
    log = format_log(report)
    assert log.count("$ sudo firewall-cmd --permanent --zone=public --add-service=http") == 2
    assert log.count("$ sudo firewall-cmd --zone=public --add-service=http") == 2
    for host in hosts:
        assert host.firewalld_zones["public"]["services"] == ["ssh", "http"]
        assert host.firewalld_permanent_zones["public"]["services"] == ["ssh", "http"]


async def test_dry_run_plans_without_sending():
    def port_deploy(host):
        yield op(
            firewalld.port, name="Open 8080", zone="public", port="8080/tcp", sudo=True
        )

    host = seeded_host()
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        port_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert host.firewalld_zones["public"]["ports"] == []
    assert host.firewalld_permanent_zones["public"]["ports"] == []


async def test_branching_on_live_state_raises_determinism_error():
    """The guard: a deploy generator must not branch on state an earlier
    request mutates (fact branching INSIDE an operation is fine)."""

    def live_branch(host):
        yield op(firewalld.default_zone, name="Set dmz", zone="dmz", sudo=True)
        # The illegal branch: consult live state the first request mutates.
        if host.firewalld_default_zone == "dmz":
            yield op(firewalld.lockdown, name="Lock down", sudo=True)

    host = seeded_host(firewalld_default_zone="public")
    runtimes, plans = await run_prepare([host], live_branch)
    assert [p.request.name for p in plans["h"]] == ["Set dmz"]
    with pytest.raises(DeterminismError):
        await run_apply(runtimes, plans=plans)