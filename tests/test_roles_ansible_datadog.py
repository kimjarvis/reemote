# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end test for the DataDog Ansible-role migration example.

``examples/roles/ansible-datadog/ansible-datadog.py`` connects for real when
run as a script, so the suite never executes its ``main()``. What it *can*
do offline is replay the same deploy sequence against ``SimulatedHost``:
the example is imported as a module, its factories are fed snapshots
gathered from simulated state, and every deploy goes through the real
driver (prepare + apply, plus a ``dry=True`` preview).

The load-bearing assertion is the migration's idempotence claim: run the
whole eight-deploy sequence, then run it *again* against the same persistent
hosts, and the second pass must report ``changed=False`` everywhere and
queue no restart handler. That is what proves the GAP workarounds converge
rather than churn -- in particular the additive per-key import (GAP-1, now
closed), the ``AptUpgradable``-gated ``latest`` (GAP-3), the snapshot-gated
``apt.update`` (GAP-4) and the per-file legacy repo fan-out (GAP-5).

Three simulation limits are bridged explicitly between the passes, each
because the in-memory host models no real subsystem:

* ``apt_upgradable`` is not cleared by a simulated ``apt-get install``, so
  the pending upgrade is retired by hand once it has been applied;
* ``gpg_keyrings`` entries hold content-hash stub keys (the simulation
  parses no OpenPGP), so the real imported fingerprints are seeded by hand;
* ``systemd_unit_files`` is a static table, so a unit whose file the deploy
  removed is forgotten by hand -- what ``daemon-reload`` does for real.

The ``find``/``grep`` seams are recomputed from the simulated filesystem
after every pass, so the second pass really does observe the first pass's
removals instead of a stale fixture.
"""

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest

from reemote import (
    AptPackages,
    AptSources,
    AptUpgradable,
    FindFiles,
    FindInFiles,
    SystemdServiceState,
    SystemdUnitFiles,
    compose,
    gather_fact,
    restart_handler,
    run,
)
from tests.simulated_host import SimulatedHost

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = REPO_ROOT / "examples" / "roles" / "ansible-datadog" / "ansible-datadog.py"


def load_example() -> Any:
    """Import the migration example by path (its name is not a valid identifier)."""
    spec = importlib.util.spec_from_file_location("ansible_datadog_example", EXAMPLE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


datadog = load_example()

#: The five armored key bodies ``fetch_keys`` would download, one per URL.
#: The test never reaches the network: the bytes are handed to the repository
#: snapshot directly, exactly as main() hands them over after its control-side
#: fetch. Each body imports additively into the shared keyring, so the file
#: ends up holding their concatenation.
KEYS = tuple(
    b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n\n"
    + f"simulated key body {index}, not real key material\n".encode()
    + b"-----END PGP PUBLIC KEY BLOCK-----\n"
    for index in range(len(datadog.APT_KEY_URLS))
)

API_KEY = "0123456789abcdef0123456789abcdef"
LEGACY_LIST = "/etc/apt/sources.list.d/datadog.list"
LEGACY_LINE = "deb https://apt.datadoghq.com/ stable 7"
INSTALLER_UNIT_FILE = "/etc/systemd/system/datadog-installer.service"
AGENT_YAML = "/etc/datadog-agent/datadog.yaml"
DISK_CONF = "/etc/datadog-agent/conf.d/disk.d/conf.yaml"
DEFAULT_CONF = "/etc/datadog-agent/conf.d/network.d/conf.yaml.default"
EXAMPLE_CONF = "/etc/datadog-agent/conf.d/ntp.d/conf.yaml.example"
CUSTOM_CHECK = "mycheck"


def make_inventory(custom_check_source: Path) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride as extra keys -- reemote's ``host_vars`` -- so
    ``role_settings`` reads them straight off ``host.extra``.
    """
    return [
        {
            "host": "ubuntu",
            "connection": {
                "address": "192.0.2.10",
                "username": "kjarvis",
                "key_path": "~/.ssh/id_ed25519",
            },
            "group": "ubuntu",
            "tags": ["ubuntu_host", "apt_package_manager"],
            "datadog_api_key": API_KEY,
            "datadog_site": "datadoghq.eu",
            "datadog_checks": {"disk": {"instances": [{"use_mount": True}]}},
            "datadog_custom_checks": {CUSTOM_CHECK: str(custom_check_source)},
            "datadog_disable_untracked_checks": True,
            "datadog_disable_default_checks": True,
            "datadog_disable_example_checks": True,
        },
        {
            "host": "web1",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
        },
    ]


def user_row(name: str, group: str, groups: list[str], uid: int) -> dict[str, Any]:
    """One ``Users`` entry, in the shape the fact's parser produces."""
    return {
        "home": f"/home/{name}",
        "comment": None,
        "shell": "/bin/bash",
        "group": group,
        "groups": groups,
        "uid": uid,
        "gid": uid,
        "lastlog": None,
        "login_time": None,
        "password": "",
    }


def refresh_search_seams(host: SimulatedHost) -> None:
    """Re-derive the ``find``/``grep`` answers from the simulated filesystem.

    Both seams are keyed by the question (the rendered ``find`` command, the
    ``(path, pattern)`` pair), so the keys are built with the facts' own
    ``command`` helpers rather than spelled out. The ERE the migration greps
    for is plain enough that Python's ``re`` answers it identically.
    """
    sources_list_d = Path(datadog.SOURCES_LIST_D)
    conf_d = Path(datadog.CONF_D)
    host.find_results = {
        FindFiles.command(datadog.SOURCES_LIST_D, fname="*.list", maxdepth=1): sorted(
            path
            for path in host.remote_files
            if Path(path).parent == sources_list_d and path.endswith(".list")
        ),
        FindFiles.command(datadog.CONF_D, fname="conf.yaml*", maxdepth=2): sorted(
            path
            for path in host.remote_files
            if Path(path).parent.parent == conf_d
            and Path(path).name.startswith("conf.yaml")
        ),
    }
    host.grep_files = {
        ("/etc/apt", datadog.LEGACY_REPO_PATTERN): sorted(
            path
            for path, data in host.remote_files.items()
            if path.startswith("/etc/apt/")
            and re.search(datadog.LEGACY_REPO_PATTERN, data.decode(), re.MULTILINE)
        ),
    }


def seed(host: SimulatedHost) -> None:
    """Pre-state: an older agent from the legacy repo, and a legacy installer."""
    host.apt_packages = {
        "datadog-agent": ["1:7.50.0-1"],
        "datadog-signing-keys": ["1.0.0-1"],
        "datadog-installer": ["0.1.0-1"],
    }
    host.apt_upgradable = [
        {
            "name": "datadog-agent",
            "installed": "1:7.50.0-1",
            "candidate": "1:7.60.0-1",
        }
    ]
    host.apt_sources = [
        {
            "type": "deb",
            "url": "https://apt.datadoghq.com/",
            "distribution": "stable",
            "components": ["7"],
            "options": {},
        },
        {
            "type": "deb",
            "url": "http://archive.ubuntu.com/ubuntu",
            "distribution": "noble",
            "components": ["main", "restricted"],
            "options": {},
        },
    ]
    host.remote_files = {
        LEGACY_LIST: f"{LEGACY_LINE}\n".encode(),
        INSTALLER_UNIT_FILE: b"[Unit]\nDescription=legacy installer\n",
        DISK_CONF: b"# a stale, hand-edited check config\n",
        DEFAULT_CONF: b"instances: []\n",
        EXAMPLE_CONF: b"instances: []\n",
    }
    host.existing_files = set(host.remote_files)
    # files.file reads the stat-shaped ``File`` fact, not the existence probe,
    # so a seeded file needs a metadata row too or its removal reads as a no-op.
    host.file_stats = {
        path: {"user": "root", "group": "root", "mode": 644, "size": len(data)}
        for path, data in host.remote_files.items()
    }
    host.os_groups = ["kjarvis", "sudo", "dd-agent"]
    host.os_users = {
        "kjarvis": user_row("kjarvis", "kjarvis", ["sudo"], 1000),
        "dd-agent": user_row("dd-agent", "dd-agent", [], 999),
    }
    host.systemd_unit_files = {
        "datadog-agent.service": {"state": "disabled", "preset": "enabled"},
        "datadog-agent-process.service": {"state": "disabled", "preset": "enabled"},
        "datadog-agent-trace.service": {"state": "disabled", "preset": "enabled"},
        "datadog-installer.service": {"state": "enabled", "preset": "enabled"},
    }
    host.services = {"datadog-installer.service": "active"}
    host.enabled_services = {"datadog-installer.service"}
    host.directory_stats = {
        datadog.CONFIG_DIR: {"mode": 755, "user": "root", "group": "root"}
    }
    refresh_search_seams(host)


def settle(host: SimulatedHost) -> None:
    """Bridge the three subsystems the simulation does not model, between passes."""
    # apt-get install applied the pending upgrade; the simulation keeps the
    # candidate list, so retire it (a real `apt list --upgradable` is empty).
    host.apt_upgradable = []
    # The keyring import stored content-hash stub keys; the simulation parses
    # no OpenPGP, so replace them with the real fingerprints the keys carry.
    for keyring in host.gpg_keyrings:
        if keyring["path"] == datadog.USR_SHARE_KEYRING:
            keyring["keys"] = [
                {
                    "id": fingerprint[-16:],
                    "fingerprint": fingerprint,
                    "validity": "-",
                    "length": 4096,
                    "subkeys": [],
                    "uid": "Datadog Packages <package@datadoghq.com>",
                }
                for fingerprint in datadog.APT_KEY_FINGERPRINTS
            ]
    # daemon-reload after the unit-file removals: systemd forgets a unit
    # whose file is gone. The agent's own units came from the package and
    # have no file in the simulated store, so they are left alone.
    for unit in datadog.LEGACY_INSTALLER_UNITS:
        paths = [
            path
            for path in datadog.LEGACY_INSTALLER_PATHS
            if path.endswith(f"/{unit}")
        ]
        if not any(path in host.existing_files for path in paths):
            host.systemd_unit_files.pop(unit, None)
            host.services.pop(unit, None)
            host.enabled_services.discard(unit)
    refresh_search_seams(host)


async def migrate(
    inventory: list[dict[str, Any]], hosts: dict[str, SimulatedHost], *, dry: bool = True
) -> dict[str, Any]:
    """Replay the example's whole deploy sequence once, against persistent hosts.

    The orchestration mirrors ``main()`` barrier for barrier -- the gathers
    sit in the same gaps between the same runs -- but reads the simulated
    hosts instead of opening SSH connections, and never fetches a key.
    """
    targets = [host for host in hosts.values() if datadog.is_ubuntu(host)]
    settings = {host.name: datadog.role_settings(host) for host in targets}
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

    installed = await gather_fact(targets, AptPackages)
    sources = await gather_fact(targets, AptSources)
    hits = await gather_fact(targets, FindInFiles, "/etc/apt", datadog.LEGACY_REPO_PATTERN)
    repo_files = await gather_fact(
        targets, FindFiles, datadog.SOURCES_LIST_D, fname="*.list", maxdepth=1
    )
    units_before = await gather_fact(targets, SystemdUnitFiles)

    await execute(
        datadog.build_pre_cleanup(
            datadog.pre_cleanup_snapshot(installed, units_before, settings)
        )
    )
    await execute(datadog.deploy_bootstrap)
    await execute(
        datadog.build_repository(
            datadog.repository_snapshot(KEYS, sources, hits, repo_files, settings)
        )
    )

    upgradable = await gather_fact(targets, AptUpgradable)
    await execute(
        datadog.build_agent(
            {
                name: frozenset(entry["name"] for entry in entries)
                for name, entries in upgradable.items()
            }
        )
    )
    await execute(datadog.deploy_identity)

    units_after = await gather_fact(targets, SystemdUnitFiles)
    exp_state = await gather_fact(
        targets, SystemdServiceState, "datadog-agent-exp.service"
    )
    plans = {
        host.name: datadog.configure_plan(
            settings[host.name],
            frozenset(units_after.get(host.name, {})),
            exp_state.get(host.name),
            datadog.reemote_version(),
        )
        for host in targets
    }
    await execute(datadog.build_configure(plans))

    found = await gather_fact(
        targets, FindFiles, datadog.CONF_D, fname="conf.yaml*", maxdepth=2
    )
    await execute(datadog.build_cleanup_conf_d(found))

    names = datadog.notifying_names(plans, found)
    notified = datadog.notified_hosts(reports, names)
    probe_notified = datadog.notified_hosts(reports, datadog.PROBE_NOTIFY_NAMES) & frozenset(
        name for name in plans if plans[name]["before_7180"]
    )
    sysprobe_restart = frozenset(
        name
        for name in probe_notified
        if plans[name]["enabled"]
        and plans[name]["sysprobe_enabled"]
        and "datadog-agent-sysprobe.service" in plans[name]["units"]
    )
    agent_restart = frozenset(
        name
        for name in notified - probe_notified
        if plans[name]["enabled"]
        and not plans[name]["exp_running"]
        and "datadog-agent.service" in plans[name]["units"]
    )
    handler_report = await execute(
        compose(
            restart_handler(
                sysprobe_restart, ("datadog-agent-sysprobe",), when=datadog.is_ubuntu
            ),
            restart_handler(agent_restart, ("datadog-agent",), when=datadog.is_ubuntu),
        )
    )
    return {
        "reports": reports,
        "plans": plans,
        "found": found,
        "sysprobe_restart": sysprobe_restart,
        "agent_restart": agent_restart,
        "handler_report": handler_report,
    }


@pytest.fixture
def rig(tmp_path: Path) -> tuple[list[dict[str, Any]], dict[str, SimulatedHost]]:
    """An inventory plus the persistent simulated hosts it materializes into."""
    source = tmp_path / f"{CUSTOM_CHECK}.py"
    source.write_text("from datadog_checks.base import AgentCheck\n", encoding="utf-8")
    inventory = make_inventory(source)
    hosts = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if datadog.is_ubuntu(host):
            seed(host)
        hosts[host.name] = host
    return inventory, hosts


def changed_entries(result: dict[str, Any]) -> list[Any]:
    """Every changed log entry across the whole sequence, handler deploy included."""
    return [
        entry
        for report in result["reports"]
        for entry in report.log
        if entry.changed
    ]


# --- the first pass really deploys the role ----------------------------------


async def test_first_pass_installs_and_configures_the_agent(rig):
    inventory, hosts = rig
    result = await migrate(inventory, hosts)
    host = hosts["ubuntu"]

    # The legacy installer is gone: package, unit state and unit files.
    assert "datadog-installer" not in host.apt_packages
    assert "datadog-installer.service" not in host.enabled_services
    assert INSTALLER_UNIT_FILE not in host.existing_files
    # Bootstrap, agent and signing keys are installed.
    for package in (*datadog.BOOTSTRAP_PACKAGES, "datadog-agent", "datadog-signing-keys"):
        assert package in host.apt_packages

    # The five per-key imports accumulated additively in the shared,
    # non-default keyring (gpg.import_key dest=), and never touched the
    # default keychain.
    assert host.remote_files[datadog.USR_SHARE_KEYRING] == b"".join(KEYS)
    assert host.file_stats[datadog.USR_SHARE_KEYRING]["mode"] == 644
    assert host.gpg_keys == []
    (keyring_entry,) = host.gpg_keyrings
    assert keyring_entry["path"] == datadog.USR_SHARE_KEYRING
    assert len(keyring_entry["keys"]) == len(KEYS)

    # GAP-5: the legacy line is gone from the file that held it, and the
    # signed-by repo for the chosen major replaced it.
    assert LEGACY_LINE not in host.remote_files[LEGACY_LIST].decode()
    assert datadog.repo_entry("7") in host.apt_sources
    assert not any(datadog.is_legacy_datadog_source(s) for s in host.apt_sources)
    repo_file = f"{datadog.SOURCES_LIST_D}/ansible_datadog_7.list"
    assert datadog.AGENT_REPOS["7"] in host.remote_files[repo_file].decode()

    # The rendered configuration, with the role's ownership and mode.
    config = host.remote_files[AGENT_YAML].decode()
    assert f"api_key: {API_KEY}" in config
    assert "site: datadoghq.eu" in config
    assert "remote_updates: True" in config
    assert host.file_stats[AGENT_YAML] == {
        "user": "dd-agent",
        "group": "dd-agent",
        "mode": 640,
        "size": len(config.encode()),
    }
    assert host.remote_files[DISK_CONF].decode().startswith(datadog.MANAGED_HEADER)
    assert "use_mount: true" in host.remote_files[DISK_CONF].decode()
    assert host.remote_files[f"{datadog.CHECKS_D}/{CUSTOM_CHECK}.py"].startswith(
        b"from datadog_checks.base"
    )
    install_info = host.remote_files[f"{datadog.CONFIG_DIR}/install_info"].decode()
    assert "tool: reemote" in install_info
    assert f"installer_version: datadog_role-{datadog.ROLE_VERSION}" in install_info

    # The conf.d sweep removed the untracked default/example configs and kept
    # the tracked one.
    assert DEFAULT_CONF not in host.existing_files
    assert EXAMPLE_CONF not in host.existing_files
    assert DISK_CONF in host.existing_files

    # GAP-2: the login user can now group-read the 0640 configs.
    assert "dd-agent" in host.os_users["kjarvis"]["groups"]
    assert "sudo" in host.os_users["kjarvis"]["groups"]

    # The agent is running and enabled, and the restart handler fired.
    assert host.services["datadog-agent.service"] == "active"
    assert "datadog-agent.service" in host.enabled_services
    assert result["agent_restart"] == frozenset({"ubuntu"})
    assert result["sysprobe_restart"] == frozenset()


async def test_non_ubuntu_host_is_never_touched(rig):
    inventory, hosts = rig
    result = await migrate(inventory, hosts)

    skipped = hosts["web1"]
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    assert skipped.os_users == {}
    for report in result["reports"]:
        for entry in report.log:
            if entry.host == "web1":
                assert entry.skipped
                assert not entry.changed


# --- the second pass is a no-op ----------------------------------------------


async def test_second_pass_changes_nothing_and_queues_no_handler(rig):
    inventory, hosts = rig
    first = await migrate(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    for host in hosts.values():
        settle(host)
    second = await migrate(inventory, hosts)

    assert changed_entries(second) == []
    assert second["agent_restart"] == frozenset()
    assert second["sysprobe_restart"] == frozenset()
    # The handler deploy still runs, but every request it holds is skipped or
    # absent: nothing is restarted on a converged host.
    assert not any(entry.changed for entry in second["handler_report"].log)


async def test_dry_preview_matches_the_apply_for_every_deploy(rig):
    """``dry=True`` stops after prepare, so it must not mutate the target."""
    inventory, hosts = rig
    before = dict(hosts["ubuntu"].remote_files)
    await migrate(inventory, hosts, dry=True)
    # The preview ran every deploy twice (dry + real); only the real half may
    # have written, and the dry half alone would have left the host untouched.
    assert hosts["ubuntu"].remote_files != before

    untouched = SimulatedHost.from_inventory(inventory[0])
    seed(untouched)
    snapshot = dict(untouched.remote_files)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return untouched

    await run(
        inventory[:1],
        datadog.build_repository(
            datadog.repository_snapshot(
                KEYS,
                await gather_fact([untouched], AptSources),
                await gather_fact(
                    [untouched], FindInFiles, "/etc/apt", datadog.LEGACY_REPO_PATTERN
                ),
                await gather_fact(
                    [untouched],
                    FindFiles,
                    datadog.SOURCES_LIST_D,
                    fname="*.list",
                    maxdepth=1,
                ),
                {"ubuntu": datadog.role_settings(untouched)},
            )
        ),
        host_factory=factory,
        dry=True,
    )
    assert untouched.remote_files == snapshot