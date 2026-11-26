# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``datadog`` role (§17.5).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, await datadog(v), host_factory=...)``. The factory is
``async def`` -- it fetches the five signing keys control-side before returning
the deploy -- so the test stubs ``files._fetch_url_bytes`` (autouse) and awaits
the factory.

The load-bearing assertion is unchanged: run the role, then run it *again*
against the same persistent hosts, and the second pass must report no change
anywhere and queue no restart handler. Under ``run_waves`` the rerun re-derives
every barrier outcome rather than reusing a frozen plan dict, so convergence
proves the barriers themselves are honest.

Two simulation limits are bridged explicitly between the passes:

* ``gpg_keyrings`` entries hold content-hash stub keys (the simulation parses no
  OpenPGP), so the real imported fingerprints are seeded by hand;
* ``systemd_unit_files`` is a static table, so a unit whose file the deploy
  removed is forgotten by hand -- what ``daemon-reload`` does for real.

The ``find`` seam is recomputed from the simulated filesystem after every pass,
so the second pass really does observe the first pass's removals.
"""

from pathlib import Path
from typing import Any

import pytest

from reemote import run_waves
from reemote.modules.files import FindFiles
from reemote.modules.files import files as files_module
from reemote.roles import DatadogVars, datadog
from reemote.roles.datadog import (
    APT_KEY_FINGERPRINTS,
    APT_KEY_URLS,
    BOOTSTRAP_PACKAGES,
    CHECKS_D,
    CONF_D,
    CONFIG_DIR,
    LEGACY_INSTALLER_PATHS,
    LEGACY_INSTALLER_UNITS,
    MANAGED_HEADER,
    ROLE_VERSION,
    SOURCES_LIST_D,
    USR_SHARE_KEYRING,
)
from reemote.rolevars import resolve_vars
from tests.simulated_host import SimulatedHost

#: The five armored key bodies the async factory fetches, one per URL.
KEYS = tuple(
    (
        "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\n"
        f"simulated key body {index}, not real key material\n"
        "-----END PGP PUBLIC KEY BLOCK-----\n"
    )
    for index in range(len(APT_KEY_URLS))
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


@pytest.fixture
def canned_fetch(monkeypatch):
    """Stub the control-side key fetches the async factory always performs.

    ``files.fetch_url`` looks ``_fetch_url_bytes`` up as a module global at call
    time, so patching it redirects the factory's five awaits. Every test that
    drives ``run_role`` requests this fixture explicitly -- the factory fetches
    unconditionally, even under ``dry``.
    """
    fetched: list[str] = []

    def fake_fetch(url: str) -> bytes:
        fetched.append(url)
        index = APT_KEY_URLS.index(url)
        return KEYS[index].encode("utf-8")

    monkeypatch.setattr(files_module, "_fetch_url_bytes", fake_fetch)
    return fetched


def make_inventory(custom_check_source: Path) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped."""
    return [
        {
            "host": "ubuntu",
            "family": "Ubuntu",
            "connection": {
                "address": "192.0.2.10",
                "username": "kjarvis",
                "key_path": "~/.ssh/id_ed25519",
            },
            "group": "ubuntu",
            "tags": ["ubuntu_host", "apt_package_manager"],
            "roles": {
                "datadog": {
                    "api_key": API_KEY,
                    "site": "datadoghq.eu",
                    "checks": {"disk": {"instances": [{"use_mount": True}]}},
                    "custom_checks": {CUSTOM_CHECK: str(custom_check_source)},
                    "disable_untracked_checks": True,
                    "disable_default_checks": True,
                    "disable_example_checks": True,
                },
            },
        },
        {
            "host": "web1",
            "family": "RedHat",
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
    """Re-derive the ``find`` answer from the simulated filesystem."""
    conf_d = Path(CONF_D)
    host.find_results = {
        FindFiles.command(CONF_D, fname="conf.yaml*", maxdepth=2): sorted(
            path
            for path in host.remote_files
            if Path(path).parent.parent == conf_d
            and Path(path).name.startswith("conf.yaml")
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
            "filename": LEGACY_LIST,
        },
        {
            "type": "deb",
            "url": "http://archive.ubuntu.com/ubuntu",
            "distribution": "noble",
            "components": ["main", "restricted"],
            "options": {},
            "filename": "/etc/apt/sources.list",
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
        CONFIG_DIR: {"mode": 755, "user": "root", "group": "root"}
    }
    refresh_search_seams(host)


def settle(host: SimulatedHost) -> None:
    """Bridge the two subsystems the simulation does not model, between passes."""
    for keyring in host.gpg_keyrings:
        if keyring["path"] == USR_SHARE_KEYRING:
            keyring["keys"] = [
                {
                    "id": fingerprint[-16:],
                    "fingerprint": fingerprint,
                    "validity": "-",
                    "length": 4096,
                    "subkeys": [],
                    "uid": "Datadog Packages <package@datadoghq.com>",
                }
                for fingerprint in APT_KEY_FINGERPRINTS
            ]
    for unit in LEGACY_INSTALLER_UNITS:
        paths = [
            path for path in LEGACY_INSTALLER_PATHS if path.endswith(f"/{unit}")
        ]
        if not any(path in host.existing_files for path in paths):
            host.systemd_unit_files.pop(unit, None)
            host.services.pop(unit, None)
            host.enabled_services.discard(unit)
    # Sync existing_files with directory_stats (files.directory writes the
    # stat row but not the existence seam).
    host.existing_files |= set(host.directory_stats)
    refresh_search_seams(host)


@pytest.fixture
def rig(tmp_path: Path) -> tuple[list[dict[str, Any]], dict[str, SimulatedHost]]:
    """An inventory plus the persistent simulated hosts it materializes into."""
    source = tmp_path / f"{CUSTOM_CHECK}.py"
    source.write_text("from datadog_checks.base import AgentCheck\n", encoding="utf-8")
    inventory = make_inventory(source)
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(host)
        hosts[host.name] = host
    return inventory, hosts


async def run_role(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
    *,
    dry: bool = False,
) -> Any:
    """Resolve variables, await the async factory, and run one wave pass."""
    v = resolve_vars(DatadogVars, "datadog", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    deploy = await datadog(v)
    report, _hosts = await run_waves(
        inventory, deploy, host_factory=factory, dry=dry
    )
    return report


def changed_entries(report: Any) -> list[Any]:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


# --- the first pass really deploys the role ----------------------------------


async def test_first_pass_installs_and_configures_the_agent(rig, canned_fetch):
    inventory, hosts = rig
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # The async factory fetched all five keys control-side.
    assert canned_fetch == list(APT_KEY_URLS)

    # The legacy installer is gone: package, unit state and unit files.
    assert "datadog-installer" not in host.apt_packages
    assert "datadog-installer.service" not in host.enabled_services
    assert INSTALLER_UNIT_FILE not in host.existing_files

    # Bootstrap, agent and signing keys are installed.
    for package in (*BOOTSTRAP_PACKAGES, "datadog-agent", "datadog-signing-keys"):
        assert package in host.apt_packages

    # latest=True upgraded the agent to its candidate version.
    assert host.apt_packages["datadog-agent"] == ["1:7.60.0-1"]
    assert host.apt_upgradable == []

    # The five per-key imports accumulated additively in the shared keyring.
    assert host.remote_files[USR_SHARE_KEYRING] == "".join(KEYS).encode()
    assert host.file_stats[USR_SHARE_KEYRING]["mode"] == 644
    assert host.gpg_keys == []
    (keyring_entry,) = host.gpg_keyrings
    assert keyring_entry["path"] == USR_SHARE_KEYRING
    assert len(keyring_entry["keys"]) == len(KEYS)

    # The legacy line is removed; the signed-by repo for major 7 is present.
    assert LEGACY_LINE not in host.remote_files[LEGACY_LIST].decode()
    repo_file = f"{SOURCES_LIST_D}/ansible_datadog_7.list"
    assert repo_file in host.remote_files

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
    assert host.remote_files[DISK_CONF].decode().startswith(MANAGED_HEADER)
    assert "use_mount: true" in host.remote_files[DISK_CONF].decode()
    assert host.remote_files[f"{CHECKS_D}/{CUSTOM_CHECK}.py"].startswith(
        b"from datadog_checks.base"
    )
    install_info = host.remote_files[f"{CONFIG_DIR}/install_info"].decode()
    assert "tool: reemote" in install_info
    assert f"installer_version: datadog_role-{ROLE_VERSION}" in install_info

    # The conf.d sweep removed the untracked default/example configs.
    assert DEFAULT_CONF not in host.existing_files
    assert EXAMPLE_CONF not in host.existing_files
    assert DISK_CONF in host.existing_files

    # The login user is never added to the agent group.
    assert "dd-agent" not in host.os_users["kjarvis"]["groups"]
    assert host.os_users["kjarvis"]["groups"] == ["sudo"]

    # The agent is running and enabled.
    assert host.services["datadog-agent.service"] == "active"
    assert "datadog-agent.service" in host.enabled_services

    # The restart handler fired for the agent, not sysprobe.
    log = report.merged().log
    agent_restarts = [
        e for e in log if "Restart datadog-agent" in e.name and e.changed
    ]
    assert agent_restarts
    sysprobe_restarts = [
        e for e in log if "sysprobe" in e.name and e.changed
    ]
    assert not sysprobe_restarts


async def test_non_ubuntu_host_is_never_touched(rig, canned_fetch):
    inventory, hosts = rig
    report = await run_role(inventory, hosts)

    skipped = hosts["web1"]
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    assert skipped.os_users == {}
    # The non-Ubuntu host emits no log entries at all (family guard returns
    # before the first yield).
    log = report.merged().log
    assert not any(entry.host == "web1" for entry in log)


# --- the second pass is a no-op ----------------------------------------------


async def test_second_pass_changes_nothing_and_queues_no_handler(rig, canned_fetch):
    inventory, hosts = rig
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    for host in hosts.values():
        settle(host)
    second = await run_role(inventory, hosts)

    assert changed_entries(second) == []
    # No restart handler fires on a converged host.
    log = second.merged().log
    assert not any("Restart" in e.name and e.changed for e in log)


async def test_dry_preview_does_not_mutate(rig, canned_fetch):
    """``dry=True`` stops after prepare, so it must not mutate the target."""
    inventory, hosts = rig
    before = dict(hosts["ubuntu"].remote_files)
    await run_role(inventory, hosts, dry=True)
    assert hosts["ubuntu"].remote_files == before
