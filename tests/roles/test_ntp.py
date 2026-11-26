# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``ntp`` role (§17.5, §18.10).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, ntp(v), host_factory=...)``. The factory is plain
(non-``async``) -- every read ntp does is a ``fact`` barrier inside the body (the
``LinuxDistribution`` release read and the post-install ``SystemdUnitFiles``
probe), not inventory-wide factory IO -- so it is called, not awaited.

The load-bearing assertion is the migration's shape claim from §18.10: the stream
is **five waves** -- ``fact(LinuxDistribution)`` then ``install`` closed by
``checkpoint("install done")`` then ``fact(SystemdUnitFiles)`` then ``configure``
closed by ``checkpoint("configure done")`` then the handlers -- with **two**
checkpoints that the two handler channels read separately (``cron_notified`` from
the install report, ``ntp_notified`` from the configure report, so unlike nginx
they cannot share one). That shape is read straight out of ``report.records``:
each barrier record carries its request type, a fact one naming its ``fact_cls``.

One between-pass bridge is needed and it is a *simulation* limit, not a library
bug: a simulated apt install records package names only, never the unit files a
package ships or removes. The role's ``SystemdUnitFiles`` barrier runs *after* the
install precisely because installing ntpsec can remove ``systemd-timesyncd`` (both
conflict through the virtual package ``time-daemon``), so ``seed`` plants the
post-install ``systemd_unit_files`` state by hand -- the daemon's own unit and
cron present, timesyncd present or gone per the scenario. That single pre-seed
stands in for the old example's mid-orchestration ``bridge_unit_files`` because
the whole run is now one ``run_waves`` call. The ``systemd.service`` runtime state
(``services``/``enabled_services``) needs no bridge -- the simulation's systemctl
arms maintain it across passes, which is what makes the second pass a no-op.

Seeding choices: ``os_timezone`` defaults to ``Etc/UTC`` so the role's default
``ntp_timezone`` is already set (the timezone task no-ops and queues no
``restart cron``); a test that wants the timezone change overrides the seam, and
``command_paths`` carries ``timedatectl`` so the change-path ``Which`` probe
resolves. The config file needs no pre-seed -- ``files.install``'s ``mkdir -p``
creates its parent and the content-idempotent write converges on the second pass.
"""

import pytest

from reemote import run_waves
from reemote.modules.server import LinuxDistribution
from reemote.modules.systemd import SystemdUnitFiles
from reemote.roles import NtpVars, ntp
from reemote.roles.ntp import (
    DEBIAN_11_VARS,
    NTPSEC_VARS,
    TIMESYNCD_UNIT,
    default_servers,
    ntp_plan,
    render_chrony_conf,
    render_ntp_conf,
    select_vars_table,
)
from reemote.rolevars import resolve_vars
from reemote.stages import CheckpointRequest, FactRequest
from tests.simulated_host import SimulatedHost

NTP_CONF = "/etc/ntpsec/ntp.conf"
DAEMON_UNIT = "ntpsec.service"
TIMESYNCD = TIMESYNCD_UNIT
CRON_UNIT = "cron.service"
TIMESYNCD_TASK = "Disable systemd-timesyncd if it's running but ntp is enabled."

UBUNTU_2404 = {
    "name": "Ubuntu",
    "family": "Debian",
    "major": "24",
    "minor": "04",
    "release_meta": {
        "ID": "ubuntu",
        "VERSION_ID": "24.04",
        "VERSION_CODENAME": "noble",
    },
}


def make_inventory(**overrides) -> list[dict]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride in the entry's ``roles`` mapping under ``"ntp"``,
    bound by ``resolve_vars``. The base entry turns on ``manage_config`` so the
    config template and the ``restart ntp`` handler are exercised; the overrides
    drive the non-default branches.
    """
    entry = {
        "host": "ubuntu",
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.10",
            "username": "supervisor",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "ubuntu",
        "tags": ["ubuntu_host", "apt_package_manager"],
        "roles": {
            "ntp": {
                "manage_config": True,
            },
        },
    }
    entry["roles"]["ntp"].update(overrides)
    return [
        entry,
        {
            "host": "web1",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
        },
    ]


def seed(
    host: SimulatedHost,
    *,
    daemon: str = "ntpsec",
    timesyncd_present: bool = False,
    running: bool = False,
) -> None:
    """Pre-state: a stock Ubuntu 24.04 machine with the NTP daemon not yet running.

    ``systemd_unit_files`` carries the post-install bridge (the daemon's own unit
    and cron present; ``timesyncd_present`` also keeps ``systemd-timesyncd`` so the
    stop/disable gate is true). ``running`` models an already-installed, running
    daemon (for the ``ntp_enabled: false`` stop/disable path).
    """
    package = daemon if daemon.endswith(".service") else f"{daemon}"
    unit = daemon if daemon.endswith(".service") else f"{daemon}.service"
    host.linux_distribution = dict(UBUNTU_2404)
    host.apt_packages = {}
    host.apt_update_stamp = None
    host.os_timezone = "Etc/UTC"
    host.command_paths = {"timedatectl": "/usr/bin/timedatectl"}
    host.services = {}
    host.enabled_services = set()
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.directory_stats = {}
    unit_files = {
        unit: {"state": "enabled", "preset": "enabled"},
        CRON_UNIT: {"state": "enabled", "preset": "enabled"},
    }
    if timesyncd_present:
        unit_files[TIMESYNCD] = {"state": "enabled", "preset": "enabled"}
        host.services[TIMESYNCD] = "active"
        host.enabled_services.add(TIMESYNCD)
    host.systemd_unit_files = unit_files
    if running:
        host.apt_packages[package] = [""]
        host.services[unit] = "active"
        host.enabled_services.add(unit)


def make_hosts(inventory: list[dict], **seed_kwargs) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(host, **seed_kwargs)
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict], hosts: dict[str, SimulatedHost], *, dry: bool = False
):
    """Resolve variables, build the sync factory's deploy, and run one wave pass."""
    v = resolve_vars(NtpVars, "ntp", inventory)

    def factory(entry: dict) -> SimulatedHost:
        # The *same* host object every pass, so state survives the barriers.
        return hosts[entry["host"]]

    # A plain (non-async) factory: called, not awaited.
    deploy = ntp(v)
    report, _hosts = await run_waves(inventory, deploy, host_factory=factory, dry=dry)
    return report


def changed_entries(report) -> list:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def ubuntu_names(report) -> set[str]:
    """Every Ubuntu log entry name of one pass."""
    return {entry.name for entry in report.merged().log if entry.host == "ubuntu"}


def ubuntu_log(report) -> dict[str, object]:
    """The Ubuntu host's log entries of one pass, keyed by name."""
    return {
        entry.name: entry for entry in report.merged().log if entry.host == "ubuntu"
    }


def _kind(barrier) -> tuple:
    """Classify one barrier record for the shape assertion.

    A ``fact`` barrier names the fact class it reads; a ``checkpoint`` names
    itself. An ``act``/``control`` barrier would fall through to a distinct label,
    so a speculatively-added barrier breaks the shape test rather than hiding.
    """
    if isinstance(barrier, FactRequest):
        return ("fact", barrier.fact_cls)
    if isinstance(barrier, CheckpointRequest):
        return ("checkpoint", barrier.name)
    return ("other", type(barrier).__name__)


def _barrier_shape(report):
    """The ubuntu host's ordered ``(wave, kind)`` barrier list for one pass."""
    ubuntu = [r for r in report.records if r.host == "ubuntu"]
    return [(r.wave, _kind(r.barrier)) for r in ubuntu if r.barrier is not None]


def _request_waves(report) -> set[int]:
    """The set of wave numbers that carried at least one ubuntu request."""
    return {
        r.wave
        for r in report.records
        if r.host == "ubuntu" and r.log is not None
    }


# --- the stream shape (§18.10's falsifier) ------------------------------------


async def test_stream_is_two_facts_and_two_checkpoints():
    """§18.10's shape: fact -> install+checkpoint -> fact -> configure+checkpoint.

    Exactly two ``fact`` barriers and two ``checkpoint`` barriers, in order, and a
    third request wave (the handlers) after the second checkpoint. A single shared
    checkpoint (as nginx uses) or an extra ``act``/``control`` would break this.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert _barrier_shape(report) == [
        (0, ("fact", LinuxDistribution)),
        (1, ("checkpoint", "install done")),
        (2, ("fact", SystemdUnitFiles)),
        (3, ("checkpoint", "configure done")),
    ]
    # Requests land in three waves: install (1), configure (3), handler (4).
    assert _request_waves(report) == {1, 3, 4}

    # The rerun re-derives every barrier, so the shape is stable across passes.
    second = await run_role(inventory, hosts)
    assert _barrier_shape(second) == _barrier_shape(report)


# --- the first pass really deploys the role ----------------------------------


async def test_first_pass_installs_and_configures_ntp():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    # ntpsec + tzdata installed and the apt index refreshed (the folded D2 update).
    assert "ntpsec" in host.apt_packages
    assert "tzdata" in host.apt_packages
    assert host.apt_update_stamp is not None

    # The daemon is running and enabled.
    assert host.services[DAEMON_UNIT] == "active"
    assert DAEMON_UNIT in host.enabled_services

    # The rendered ntp.conf landed, root-owned 0644.
    conf = host.remote_files[NTP_CONF].decode()
    assert conf.startswith("# Ansible managed\n")
    assert "driftfile /var/lib/ntpsec/ntp.drift" in conf
    assert "pool 0.pool.ntp.org iburst" in conf
    assert host.file_stats[NTP_CONF]["mode"] == 644

    # The default timezone was already set, so the timezone task no-ops and queues
    # no restart cron; the config change notified restart ntp, which fired.
    log = ubuntu_log(report)
    assert log["Set timezone."].changed is False
    assert log["Generate ntp configuration file."].changed
    assert log["restart ntp"].changed
    assert "restart cron" not in log

    # ntpsec removed timesyncd through the conflict, so the post-install gate is
    # false and the stop/disable task was never materialized.
    assert TIMESYNCD_TASK not in log

    # The two handler channels read their own checkpoints: ntp fired, cron empty.
    assert changed_entries(report)


async def test_second_pass_is_a_noop():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    # The migration's idempotence claim: nothing changed anywhere and no handler
    # ran -- packages present, stamp fresh, timezone set, service running/enabled,
    # config content-equal, so neither channel notified and the handler wave is
    # empty (restart ntp never appears).
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert "restart ntp" not in ubuntu_names(second)


async def test_non_ubuntu_host_is_never_touched():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    skipped = hosts["web1"]

    # The family guard returns before the first yield, so the off-target host is
    # in no wave, emits no log line and its stores never move.
    assert not any(entry.host == "web1" for entry in report.merged().log)
    assert not any(r.host == "web1" for r in report.records)
    assert skipped.remote_files == {}
    assert skipped.services == {}
    assert skipped.enabled_services == set()


async def test_dry_preview_stops_at_the_distribution_barrier():
    """``dry=True`` never runs a barrier, so it parks on the first item.

    ntp's first yielded item is the ``LinuxDistribution`` ``fact`` barrier, so the
    dry preview plans no request and applies nothing -- the whole stream stays
    unexecuted (there is no request ahead of the first barrier to plan, unlike a
    role whose install precedes its probe).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts, dry=True)

    assert host.apt_packages == {}
    assert host.apt_update_stamp is None
    assert host.remote_files == {}
    assert host.services == {}
    assert host.enabled_services == set()
    assert changed_entries(report) == []
    # Exactly one barrier record, no request records.
    ubuntu = [r for r in report.records if r.host == "ubuntu"]
    assert [r.wave for r in ubuntu if r.barrier is not None] == [0]
    assert _request_waves(report) == set()


# --- the default path (config unmanaged) --------------------------------------


async def test_default_path_leaves_config_unmanaged():
    inventory = make_inventory(manage_config=False)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    # No config request, no restart ntp notification; the daemon still comes up.
    assert NTP_CONF not in host.remote_files
    assert "Generate ntp configuration file." not in ubuntu_names(report)
    assert "restart ntp" not in ubuntu_names(report)
    assert host.services[DAEMON_UNIT] == "active"

    # And the default path converges too.
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the timezone task and the restart cron handler ---------------------------


async def test_timezone_change_fires_cron_handler():
    inventory = make_inventory(cron_handler_enabled=True)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    host.os_timezone = "Europe/Amsterdam"  # differs from the default Etc/UTC
    report = await run_role(inventory, hosts)

    # The timezone changed (the Which probe resolved timedatectl), notifying
    # restart cron through the install checkpoint, which fired because the cron
    # handler flag is true.
    log = ubuntu_log(report)
    assert log["Set timezone."].changed
    assert host.os_timezone == "Etc/UTC"
    assert log["restart cron"].changed
    assert host.services[CRON_UNIT] == "active"

    # The second pass no-ops: the zone is set, so no cron notification.
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert "restart cron" not in ubuntu_names(second)


async def test_cron_handler_flag_gates_the_restart():
    inventory = make_inventory(cron_handler_enabled=False)
    hosts = make_hosts(inventory)
    hosts["ubuntu"].os_timezone = "Europe/Amsterdam"
    report = await run_role(inventory, hosts)

    # The timezone change notified cron, but the handler's static flag is false,
    # so restart cron is suppressed (restart ntp still fires for the config).
    log = ubuntu_log(report)
    assert log["Set timezone."].changed
    assert "restart cron" not in log
    assert "restart ntp" in log


# --- ntp_enabled: false -------------------------------------------------------


async def test_ntp_disabled_stops_disables_and_suppresses_restart():
    inventory = make_inventory(enabled=False)
    hosts = make_hosts(inventory, running=True)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]
    log = ubuntu_log(report)

    # The running daemon was stopped and disabled.
    assert host.services[DAEMON_UNIT] == "inactive"
    assert DAEMON_UNIT not in host.enabled_services
    assert "Ensure NTP is stopped and disabled as configured." in log

    # The config still installs (the role templates regardless of ntp_enabled), so
    # restart ntp is notified -- but the handler's ntp_enabled gate suppresses it,
    # exactly as handlers/main.yml's ``when: ntp_enabled`` does.
    assert NTP_CONF in host.remote_files
    assert log["Generate ntp configuration file."].changed
    assert "restart ntp" not in log

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the timesyncd stop/disable task ------------------------------------------


async def test_timesyncd_present_is_stopped_and_disabled():
    inventory = make_inventory()
    hosts = make_hosts(inventory, timesyncd_present=True)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # The unit survived the install, so the two-condition gate fired: the running/
    # enabled timesyncd was stopped and disabled.
    assert TIMESYNCD in host.systemd_unit_files
    assert host.services[TIMESYNCD] == "inactive"
    assert TIMESYNCD not in host.enabled_services
    log = ubuntu_log(report)
    assert log[TIMESYNCD_TASK].changed

    # Pass 2: the unit is still present (the gate stays true) but already
    # stopped/disabled, so the request is a no-op.
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the chrony override ------------------------------------------------------


async def test_chrony_override_renders_chrony_conf():
    inventory = make_inventory(
        daemon="chrony",
        package="chrony",
        config_file="/etc/chrony/chrony.conf",
    )
    hosts = make_hosts(inventory, daemon="chrony")
    host = hosts["ubuntu"]
    await run_role(inventory, hosts)

    # The set_fact overrides won: chrony installed, its unit managed, and the
    # basename dispatch selected chrony.conf.j2 (maxdistance, server lines).
    assert "chrony" in host.apt_packages
    assert host.services["chrony.service"] == "active"
    conf = host.remote_files["/etc/chrony/chrony.conf"].decode()
    assert "maxdistance 3" in conf
    assert "server 0.pool.ntp.org iburst" in conf
    assert "driftfile /var/lib/chrony/drift" in conf


# --- the pure helpers, unit-tested directly -----------------------------------


def test_set_fact_overrides_win_over_the_vars_table():
    hv = NtpVars(
        daemon="ntpsec",
        package="ntpsec-ntpdate",
        config_file="/etc/ntpsec/ntp.conf",
        driftfile="/var/lib/ntpsec/custom.drift",
    )
    plan = ntp_plan(hv, UBUNTU_2404)
    assert plan.packages == ("ntpsec-ntpdate",)
    assert plan.daemon == "ntpsec"
    assert plan.config_file == "/etc/ntpsec/ntp.conf"
    # tzdata/cron come straight from the vars table (no set_fact guard).
    assert plan.tzdata_packages == ("tzdata",)
    assert plan.cron_daemon == "cron"


def test_default_path_does_not_render_or_dispatch():
    # manage_config is off, so no template renders -- an unknown basename is
    # harmless here, exactly as the role's template task is skipped.
    hv = NtpVars(config_file="/etc/ntpsec/clock.conf")
    plan = ntp_plan(hv, UBUNTU_2404)
    assert plan.config_content is None
    assert plan.manage_config is False


def test_unknown_config_basename_raises():
    hv = NtpVars(manage_config=True, config_file="/etc/ntpsec/clock.conf")
    with pytest.raises(ValueError, match="no bundled template for clock.conf"):
        ntp_plan(hv, UBUNTU_2404)


def test_select_vars_table_chain():
    # Ubuntu-24.04 hits the distribution-version level (ntpsec).
    assert select_vars_table(UBUNTU_2404) is NTPSEC_VARS
    # An Ubuntu with no exact-version file falls through family-major to family.
    ubuntu_2204 = {
        **UBUNTU_2404,
        "major": "22",
        "release_meta": {"VERSION_ID": "22.04"},
    }
    assert select_vars_table(ubuntu_2204) is NTPSEC_VARS
    # Debian-11 hits the family-major level (the legacy ntp daemon).
    debian_11 = {
        "name": "Ubuntu",
        "family": "Debian",
        "major": "11",
        "minor": None,
        "release_meta": {"VERSION_ID": "11"},
    }
    assert select_vars_table(debian_11) is DEBIAN_11_VARS
    assert DEBIAN_11_VARS["daemon"] == "ntp"
    # A non-Ubuntu distribution is a hard error (the inventory family said Ubuntu).
    with pytest.raises(ValueError, match="targets Ubuntu"):
        select_vars_table({**UBUNTU_2404, "name": "CentOS"})
    # An unreadable major version fails the chain.
    with pytest.raises(ValueError, match="major version"):
        select_vars_table({**UBUNTU_2404, "major": None})


def test_default_servers_interpolates_area():
    assert default_servers("") == tuple(
        f"{i}.pool.ntp.org iburst" for i in range(4)
    )
    assert default_servers("us") == tuple(
        f"{i}.us.pool.ntp.org iburst" for i in range(4)
    )


def test_render_ntp_conf_tinker_panic():
    servers = default_servers("")
    restrict = ("127.0.0.1", "::1")
    off = render_ntp_conf("/var/lib/ntpsec/ntp.drift", servers, restrict, False)
    on = render_ntp_conf("/var/lib/ntpsec/ntp.drift", servers, restrict, True)
    assert "tinker panic 0" not in off
    assert "tinker panic 0" in on
    assert "restrict 127.0.0.1" in off
    assert "restrict ::1" in off
    assert off.startswith("# Ansible managed\n")


def test_render_chrony_conf_maxdistance():
    servers = default_servers("")
    conf = render_chrony_conf(servers, ("127.0.0.1",), "5")
    assert "maxdistance 5" in conf
    assert "allow 127.0.0.1" in conf
    assert "server 0.pool.ntp.org iburst" in conf


# --- the typed variables reject bad input -------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"timezone": ""}, "timezone"),
        ({"chrony_maxdistance": ""}, "chrony_maxdistance"),
        ({"restrict": [""]}, "restrict"),
        ({"servers": [""]}, "servers"),
        ({"daemon": ""}, "daemon"),
        ({"package": ""}, "package"),
        ({"config_file": ""}, "config_file"),
        ({"driftfile": ""}, "driftfile"),
    ],
)
def test_vars_reject_invalid_values(kwargs, match):
    # pydantic's ValidationError is a ValueError; so is every validator raise. The
    # message names the (unprefixed) field.
    with pytest.raises(ValueError, match=match):
        NtpVars(**kwargs)


def test_servers_list_becomes_a_tuple():
    # A list from JSON is normalized to a tuple so the model stays hashable.
    hv = NtpVars(servers=["0.example.org iburst"], restrict=("a", "b"))
    assert hv.servers == ("0.example.org iburst",)
    assert hv.restrict == ("a", "b")
    assert hash(hv) is not None


@pytest.mark.parametrize("value", ["yes", "no", "true", "false", "on", "off", 1, 0])
def test_enabled_accepts_yaml_booleans(value):
    assert isinstance(NtpVars(enabled=value).enabled, bool)
