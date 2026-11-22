# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end test for the NTP Ansible-role migration example.

``examples/roles/ansible-role-ntp/ansible-role-ntp.py`` connects for real when
run as a script, so the suite never executes its ``main()``. What it *can* do
offline is replay the same orchestration against ``SimulatedHost``: the example
is imported as a module, its factories are fed snapshots gathered from
simulated state, and every deploy goes through the real driver (prepare +
apply, plus a ``dry=True`` preview).

The load-bearing assertion is the migration's idempotence claim: run the whole
LinuxDistribution gather -> install -> SystemdUnitFiles gather -> configure ->
handler sequence, then run it *again* against the same persistent hosts, and
the second pass must report ``changed=False`` everywhere and queue no handler.

One between-pass bridge is needed, and it is a *simulation* limit, not a library
bug: a simulated apt install records package names only, never the unit files a
package ships or removes. The role's ``service_facts`` probe runs *after* the
install precisely because installing ntpsec can remove ``systemd-timesyncd``
(both conflict through the virtual package ``time-daemon``, upstream #135), so
``bridge_unit_files`` seeds the post-install ``SystemdUnitFiles`` state by hand:
the daemon's own unit appears, and timesyncd is present or gone per the scenario
the test selects. The ``systemd.service`` runtime state (``services`` /
``enabled_services``) needs no bridge -- the simulation's systemctl arms maintain
it across passes, which is what makes the second pass a no-op.

Seeding choices: ``os_timezone`` defaults to ``Etc/UTC`` so the role's default
``ntp_timezone`` is already set (the timezone task no-ops and probes no
``timedatectl``); a test that wants the timezone change overrides the seam, and
``command_paths`` carries ``timedatectl`` so the change-path ``Which`` probe
resolves. The config file needs no pre-seed -- ``files.install``'s ``mkdir -p``
creates its parent and the content-idempotent write converges on the second pass.
"""

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from reemote import (
    LinuxDistribution,
    SystemdUnitFiles,
    changed_hosts,
    gather_fact,
    run,
)
from tests.simulated_host import SimulatedHost

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = (
    REPO_ROOT / "examples" / "roles" / "ansible-role-ntp" / "ansible-role-ntp.py"
)


def load_example() -> Any:
    """Import the migration example by path (its name is not a valid identifier)."""
    spec = importlib.util.spec_from_file_location(
        "ansible_role_ntp_example", EXAMPLE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ntp = load_example()

NTP_CONF = "/etc/ntpsec/ntp.conf"
DAEMON_UNIT = "ntpsec.service"
TIMESYNCD = "systemd-timesyncd.service"
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


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride as extra keys -- reemote's ``host_vars`` -- so
    ``role_settings`` reads them straight off ``host.extra``. The base entry
    turns on ``ntp_manage_config`` so the config template and the ``restart ntp``
    handler are exercised; the overrides drive the non-default branches.
    """
    entry: dict[str, Any] = {
        "host": "ubuntu",
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.10",
            "username": "supervisor",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "ubuntu",
        "tags": ["ubuntu_host", "apt_package_manager"],
        "ntp_manage_config": True,
    }
    entry.update(overrides)
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
    timesyncd_present: bool = False,
    ntpsec_running: bool = False,
) -> None:
    """Pre-state: a stock Ubuntu 24.04 machine with ntpsec not yet installed.

    ``systemd_unit_files`` carries the stock ``systemd-timesyncd`` unit (the
    default time daemon); the post-install bridge decides whether it survives.
    ``timesyncd_present`` additionally seeds it running/enabled so the stop/
    disable task has something to change. ``ntpsec_running`` models an already
    installed, running daemon (for the ``ntp_enabled: false`` stop/disable path).
    """
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
    host.systemd_unit_files = {TIMESYNCD: {"state": "enabled", "preset": "enabled"}}
    if timesyncd_present:
        host.services[TIMESYNCD] = "active"
        host.enabled_services.add(TIMESYNCD)
    if ntpsec_running:
        host.apt_packages["ntpsec"] = [""]
        host.services[DAEMON_UNIT] = "active"
        host.enabled_services.add(DAEMON_UNIT)


def make_hosts(
    inventory: list[dict[str, Any]],
    *,
    timesyncd_present: bool = False,
    ntpsec_running: bool = False,
) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if ntp.is_ubuntu(host):
            seed(
                host, timesyncd_present=timesyncd_present, ntpsec_running=ntpsec_running
            )
        hosts[host.name] = host
    return hosts


def bridge_unit_files(
    host: SimulatedHost, daemon: str, *, timesyncd_present: bool
) -> None:
    """Simulation bridge: seed the post-install ``SystemdUnitFiles`` state.

    The apt install seam records package names only, so the unit files the
    ntpsec package ships (its own unit) and the timesyncd unit the conflict
    removes are planted by hand -- the role's ``service_facts`` probe reads this
    store after the install apply. The gate is presence-only (a unit absent from
    ``list-unit-files`` is exactly ``status: not-found``), so the runtime
    stop/disable still reads ``services``/``enabled_services``, which the
    systemctl arms maintain. Idempotent: re-seeding the same rows on pass 2
    changes nothing the deploys read.
    """
    unit = daemon if daemon.endswith(".service") else f"{daemon}.service"
    host.systemd_unit_files[unit] = {"state": "enabled", "preset": "enabled"}
    host.systemd_unit_files["cron.service"] = {"state": "enabled", "preset": "enabled"}
    if timesyncd_present:
        host.systemd_unit_files.setdefault(
            TIMESYNCD, {"state": "enabled", "preset": "enabled"}
        )
    else:
        host.systemd_unit_files.pop(TIMESYNCD, None)


async def migrate(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
    *,
    dry: bool = True,
    timesyncd_present: bool = False,
) -> dict[str, Any]:
    """Replay the example's whole orchestration once, against persistent hosts.

    The sequence mirrors ``main()`` barrier for barrier -- the LinuxDistribution
    gather up front, the SystemdUnitFiles gather after the install apply, the two
    ``changed_hosts`` reads before the handler deploy -- but reads the simulated
    hosts instead of opening SSH connections.
    """
    targets = [host for host in hosts.values() if ntp.is_ubuntu(host)]
    reports: list[Any] = []

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        # The *same* host object every run, so state survives the barriers.
        return hosts[entry["host"]]

    async def execute(deploy: Any) -> Any:
        if dry:
            await run(inventory, deploy, host_factory=factory, dry=True)
        _plans, report, _hosts = await run(inventory, deploy, host_factory=factory)
        assert report is not None
        reports.append(report)
        return report

    # The role's variable gates, before any host is contacted.
    for host in targets:
        ntp.role_settings(host)

    # The include_vars dispatch: gather -> snapshot -> factory. gather_fact does
    # not close its hosts, so the finally does.
    try:
        distributions = await gather_fact(targets, LinuxDistribution)
    finally:
        for host in hosts.values():
            await host.aclose()
    plans = {
        host.name: ntp.ntp_plan(host, distributions[host.name]) for host in targets
    }

    # Deploy 1: the NTP package, tzdata and the timezone (notifies restart cron).
    install_report = await execute(ntp.build_install(plans))

    # The post-install unit-file bridge, then the service_facts gather.
    for host in targets:
        bridge_unit_files(
            host, plans[host.name]["daemon"], timesyncd_present=timesyncd_present
        )
    try:
        unit_files = await gather_fact(targets, SystemdUnitFiles)
    finally:
        for host in targets:
            await host.aclose()
    for host in targets:
        plans[host.name]["timesyncd"] = ntp.TIMESYNCD_UNIT in unit_files[host.name]

    # Deploy 2: timesyncd, the daemon state, the config (notifies restart ntp).
    configure_report = await execute(ntp.build_configure(plans))

    # The handlers: changed_hosts over the two notifying name sets, then deploy 3
    # (restart ntp first, restart cron second -- handler definition order).
    cron_notified = changed_hosts(install_report, ntp.NOTIFY_CRON)
    ntp_notified = changed_hosts(configure_report, ntp.NOTIFY_NTP)
    handler_report = await execute(
        ntp.build_handlers(plans, ntp_notified, cron_notified)
    )
    return {
        "reports": reports,
        "plans": plans,
        "install_report": install_report,
        "configure_report": configure_report,
        "cron_notified": cron_notified,
        "ntp_notified": ntp_notified,
        "handler_report": handler_report,
    }


@pytest.fixture
def rig() -> tuple[list[dict[str, Any]], dict[str, SimulatedHost]]:
    """An inventory plus the persistent simulated hosts it materializes into."""
    inventory = make_inventory()
    return inventory, make_hosts(inventory)


def changed_entries(result: dict[str, Any]) -> list[Any]:
    """Every changed log entry across the whole sequence, handler deploy included."""
    return [
        entry for report in result["reports"] for entry in report.log if entry.changed
    ]


# --- the first pass really deploys the role ----------------------------------


async def test_first_pass_installs_and_configures_ntp(rig):
    inventory, hosts = rig
    result = await migrate(inventory, hosts)
    host = hosts["ubuntu"]

    # ntpsec + tzdata installed and the apt index refreshed (the folded D1 update).
    assert "ntpsec" in host.apt_packages
    assert "tzdata" in host.apt_packages
    assert host.apt_update_stamp is not None

    # The daemon is running and enabled.
    assert host.services[DAEMON_UNIT] == "active"
    assert DAEMON_UNIT in host.enabled_services

    # The rendered ntp.conf landed, root-owned 0644, content-equal to the plan.
    conf = host.remote_files[NTP_CONF].decode()
    assert conf == result["plans"]["ubuntu"]["config_content"]
    assert conf.startswith("# Ansible managed\n")
    assert "driftfile /var/lib/ntpsec/ntp.drift" in conf
    assert "pool 0.pool.ntp.org iburst" in conf
    assert host.file_stats[NTP_CONF]["mode"] == 644

    # The default timezone was already set, so no cron notification; the config
    # change notified restart ntp, which fired (definition order: ntp then cron).
    assert result["cron_notified"] == frozenset()
    assert result["ntp_notified"] == frozenset({"ubuntu"})
    assert [entry.name for entry in result["handler_report"].log] == ["restart ntp"]
    assert all(entry.changed for entry in result["handler_report"].log)

    # ntpsec removed timesyncd through the conflict, so the post-install gate is
    # False and the stop/disable task was never materialized.
    assert result["plans"]["ubuntu"]["timesyncd"] is False
    assert all(entry.name != TIMESYNCD_TASK for entry in result["configure_report"].log)


async def test_second_pass_is_a_noop(rig):
    inventory, hosts = rig
    await migrate(inventory, hosts)
    second = await migrate(inventory, hosts)

    # The migration's idempotence claim: nothing changed anywhere and no handler
    # was queued -- packages present, stamp fresh, timezone set, service
    # running/enabled, config content-equal.
    assert changed_entries(second) == []
    assert second["cron_notified"] == frozenset()
    assert second["ntp_notified"] == frozenset()
    assert second["handler_report"].log == []


async def test_non_ubuntu_hosts_are_untouched(rig):
    inventory, hosts = rig
    result = await migrate(inventory, hosts)
    skipped = hosts["web1"]

    # Every request naming web1 was skipped by the is_ubuntu guard; the host was
    # never contacted, so none of its stores moved.
    for report in result["reports"]:
        for entry in report.log:
            if entry.host == "web1":
                assert entry.skipped
    assert skipped.apt_packages == {}
    assert skipped.remote_files == {}
    assert skipped.services == {}
    assert skipped.enabled_services == set()


async def test_dry_preview_mutates_nothing(rig):
    inventory, hosts = rig
    host = hosts["ubuntu"]
    plans = {host.name: ntp.ntp_plan(host, UBUNTU_2404)}
    plans[host.name]["timesyncd"] = False

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    await run(inventory, ntp.build_install(plans), host_factory=factory, dry=True)
    await run(inventory, ntp.build_configure(plans), host_factory=factory, dry=True)

    assert host.apt_packages == {}
    assert host.apt_update_stamp is None
    assert host.remote_files == {}
    assert host.services == {}
    assert host.enabled_services == set()


# --- the default path (config unmanaged) --------------------------------------


async def test_default_path_leaves_config_unmanaged():
    inventory = make_inventory(ntp_manage_config=False)
    hosts = make_hosts(inventory)
    result = await migrate(inventory, hosts)
    host = hosts["ubuntu"]

    # No config request, no restart ntp notification; the daemon still comes up.
    assert result["plans"]["ubuntu"]["config_content"] is None
    assert NTP_CONF not in host.remote_files
    assert result["ntp_notified"] == frozenset()
    assert result["handler_report"].log == []
    assert host.services[DAEMON_UNIT] == "active"

    # And the default path converges too.
    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []
    assert second["handler_report"].log == []


# --- the timezone task and the restart cron handler ---------------------------


async def test_timezone_change_fires_cron_handler():
    inventory = make_inventory(ntp_cron_handler_enabled=True)
    hosts = make_hosts(inventory)
    hosts["ubuntu"].os_timezone = "Europe/Amsterdam"  # differs from Etc/UTC
    result = await migrate(inventory, hosts)
    host = hosts["ubuntu"]

    # The timezone changed (the Which probe resolved timedatectl), notifying
    # restart cron, which fired because ntp_cron_handler_enabled is true.
    assert host.os_timezone == "Etc/UTC"
    assert result["cron_notified"] == frozenset({"ubuntu"})
    assert "restart cron" in [entry.name for entry in result["handler_report"].log]
    assert host.services["cron.service"] == "active"

    # The second pass no-ops: the zone is set, so no cron notification.
    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []
    assert second["cron_notified"] == frozenset()


async def test_cron_handler_flag_gates_the_restart():
    inventory = make_inventory(ntp_cron_handler_enabled=False)
    hosts = make_hosts(inventory)
    hosts["ubuntu"].os_timezone = "Europe/Amsterdam"
    result = await migrate(inventory, hosts)

    # The timezone change notified cron, but the handler's static flag is false,
    # so restart cron is suppressed (restart ntp still fires for the config).
    assert result["cron_notified"] == frozenset({"ubuntu"})
    assert [entry.name for entry in result["handler_report"].log] == ["restart ntp"]


# --- ntp_enabled: false -------------------------------------------------------


async def test_ntp_disabled_stops_disables_and_suppresses_restart():
    inventory = make_inventory(ntp_enabled=False)
    hosts = make_hosts(inventory, ntpsec_running=True)
    result = await migrate(inventory, hosts)
    host = hosts["ubuntu"]

    # The running daemon was stopped and disabled.
    assert host.services[DAEMON_UNIT] == "inactive"
    assert DAEMON_UNIT not in host.enabled_services
    assert any(
        entry.name == "Ensure NTP is stopped and disabled as configured."
        for entry in result["configure_report"].log
    )

    # The config still installs (the role templates regardless of ntp_enabled),
    # so restart ntp is notified -- but the handler's ntp_enabled gate suppresses
    # it, exactly as handlers/main.yml's `when: ntp_enabled` does.
    assert NTP_CONF in host.remote_files
    assert result["ntp_notified"] == frozenset({"ubuntu"})
    assert result["handler_report"].log == []

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


# --- the timesyncd stop/disable task ------------------------------------------


async def test_timesyncd_present_is_stopped_and_disabled():
    inventory = make_inventory()
    hosts = make_hosts(inventory, timesyncd_present=True)
    result = await migrate(inventory, hosts, timesyncd_present=True)
    host = hosts["ubuntu"]

    # The unit survived the install, so the three-condition when fired: the
    # running/enabled timesyncd was stopped and disabled.
    assert result["plans"]["ubuntu"]["timesyncd"] is True
    assert host.services[TIMESYNCD] == "inactive"
    assert TIMESYNCD not in host.enabled_services
    assert any(
        entry.name == TIMESYNCD_TASK and entry.changed
        for entry in result["configure_report"].log
    )

    # Pass 2: the unit is still present (the gate stays True) but already
    # stopped/disabled, so the request is a no-op.
    second = await migrate(inventory, hosts, timesyncd_present=True)
    assert changed_entries(second) == []
    assert second["handler_report"].log == []


# --- the chrony override and the set_fact precedence --------------------------


async def test_chrony_override_renders_chrony_conf():
    inventory = make_inventory(
        ntp_daemon="chrony",
        ntp_package="chrony",
        ntp_config_file="/etc/chrony/chrony.conf",
    )
    hosts = make_hosts(inventory)
    result = await migrate(inventory, hosts)
    host = hosts["ubuntu"]

    # The set_fact overrides won: chrony installed, its unit managed, and the
    # basename dispatch selected chrony.conf.j2 (maxdistance, server lines).
    assert "chrony" in host.apt_packages
    assert host.services["chrony.service"] == "active"
    conf = host.remote_files["/etc/chrony/chrony.conf"].decode()
    assert conf == result["plans"]["ubuntu"]["config_content"]
    assert "maxdistance 3" in conf
    assert "server 0.pool.ntp.org iburst" in conf
    assert "driftfile /var/lib/chrony/drift" in conf


def test_set_fact_overrides_win_over_the_vars_table():
    entry = make_inventory(
        ntp_daemon="ntpsec",
        ntp_package="ntpsec-ntpdate",
        ntp_config_file="/etc/ntpsec/ntp.conf",
        ntp_driftfile="/var/lib/ntpsec/custom.drift",
    )[0]
    host = SimulatedHost.from_inventory(entry)
    plan = ntp.ntp_plan(host, UBUNTU_2404)
    assert plan["packages"] == ("ntpsec-ntpdate",)
    assert plan["daemon"] == "ntpsec"
    assert plan["config_file"] == "/etc/ntpsec/ntp.conf"
    # tzdata/cron come straight from include_vars (no set_fact guard).
    assert plan["tzdata_packages"] == ("tzdata",)
    assert plan["cron_daemon"] == "cron"


def test_unknown_config_basename_raises():
    entry = make_inventory(ntp_config_file="/etc/ntpsec/clock.conf")[0]
    host = SimulatedHost.from_inventory(entry)
    with pytest.raises(ValueError, match="no bundled template for clock.conf.j2"):
        ntp.ntp_plan(host, UBUNTU_2404)


# --- the pure helpers ---------------------------------------------------------


def test_role_settings_gates_fail_before_any_host_is_contacted():
    def settings_with(**extra: Any) -> Any:
        entry = make_inventory()[0]
        entry.update(extra)
        return ntp.role_settings(SimulatedHost.from_inventory(entry))

    with pytest.raises(TypeError, match="ntp_enabled"):
        settings_with(ntp_enabled="yes")
    with pytest.raises(ValueError, match="ntp_timezone"):
        settings_with(ntp_timezone="")
    with pytest.raises(TypeError, match="ntp_servers"):
        settings_with(ntp_servers="not-a-list")
    with pytest.raises(ValueError, match="ntp_restrict"):
        settings_with(ntp_restrict=[""])
    # The defaults flow through when nothing is overridden.
    settings = settings_with()
    assert settings["ntp_enabled"] is True
    assert settings["ntp_timezone"] == "Etc/UTC"
    assert settings["ntp_servers"] is None


def test_resolve_vars_first_found_chain():
    # Ubuntu-24.04 hits the distribution-version level (ntpsec).
    assert ntp.resolve_vars(UBUNTU_2404) is ntp.NTPSEC_VARS
    # An Ubuntu with no exact-version file falls through family-major to family.
    ubuntu_2204 = {
        **UBUNTU_2404,
        "major": "22",
        "release_meta": {"VERSION_ID": "22.04"},
    }
    assert ntp.resolve_vars(ubuntu_2204) is ntp.NTPSEC_VARS
    # Debian-11 hits the family-major level (the legacy ntp daemon).
    debian_11 = {
        "name": "Ubuntu",
        "family": "Debian",
        "major": "11",
        "minor": None,
        "release_meta": {"VERSION_ID": "11"},
    }
    assert ntp.resolve_vars(debian_11) is ntp.DEBIAN_11_VARS
    assert ntp.DEBIAN_11_VARS["daemon"] == "ntp"
    # A non-Ubuntu distribution is a hard error (the inventory group said ubuntu).
    with pytest.raises(ValueError, match="targets Ubuntu"):
        ntp.resolve_vars({**UBUNTU_2404, "name": "CentOS"})
    # An unreadable major version fails the chain.
    with pytest.raises(ValueError, match="major version"):
        ntp.resolve_vars({**UBUNTU_2404, "major": None})


def test_default_servers_interpolates_area():
    assert ntp.default_servers("") == tuple(
        f"{i}.pool.ntp.org iburst" for i in range(4)
    )
    assert ntp.default_servers("us") == tuple(
        f"{i}.us.pool.ntp.org iburst" for i in range(4)
    )


def test_render_ntp_conf_tinker_panic():
    servers = ntp.default_servers("")
    restrict = ("127.0.0.1", "::1")
    off = ntp.render_ntp_conf("/var/lib/ntpsec/ntp.drift", servers, restrict, False)
    on = ntp.render_ntp_conf("/var/lib/ntpsec/ntp.drift", servers, restrict, True)
    assert "tinker panic 0" not in off
    assert "tinker panic 0" in on
    assert "restrict 127.0.0.1" in off
    assert "restrict ::1" in off
    assert off.startswith("# Ansible managed\n")


def test_render_chrony_conf_maxdistance():
    servers = ntp.default_servers("")
    conf = ntp.render_chrony_conf(servers, ("127.0.0.1",), "5")
    assert "maxdistance 5" in conf
    assert "allow 127.0.0.1" in conf
    assert "server 0.pool.ntp.org iburst" in conf
