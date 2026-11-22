# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end test for the Docker Ansible-role migration example.

``examples/roles/ansible-role-docker/ansible-role-docker.py`` connects for
real when run as a script, so the suite never executes its ``main()``. What
it *can* do offline is replay the same orchestration against
``SimulatedHost``: the example is imported as a module, its setup factory is
fed the codename snapshot gathered from simulated state, and every deploy
goes through the real driver (prepare + apply, plus a ``dry=True`` preview).

The load-bearing assertion is the migration's idempotence claim: run the
whole setup -> packages -> configure -> handler -> users sequence, then run
it *again* against the same persistent hosts, and the second pass must
report ``changed=False`` everywhere and queue no restart handler.

The signing-key fetch (``apt.sources_file``'s URL ``signed_by`` form, issue
#17) is answered offline by monkeypatching ``apt._fetch_url_bytes`` -- the
operation's documented control-side seam -- so no test touches the
network. The release codename comes from the ``linux_distribution`` seam.

One between-pass bridge is needed, and it is a *simulation* limit, not a
library bug: a simulated apt install records package names only, so the
``docker`` group the real docker-ce postinst creates is appended to
``os_groups`` by hand after the packages apply -- the ``Groups`` gather (the
role's getent gate) then sees the group a real machine would have.

One seeding note, also a simulation limit: ``remove_file`` discards the
existence marker and the stat row but leaves the content bytes in
``remote_files``, so the legacy-artifact removal assertions read
``existing_files``/``file_stats`` (the seams the ``File`` fact answers from),
not ``remote_files``.
"""

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from reemote import (
    Groups,
    LinuxDistribution,
    changed_hosts,
    compose,
    gather_fact,
    run,
    service_handler,
)
from reemote.modules.apt import apt as apt_module
from tests.simulated_host import SimulatedHost

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = (
    REPO_ROOT / "examples" / "roles" / "ansible-role-docker" / "ansible-role-docker.py"
)


def load_example() -> Any:
    """Import the migration example by path (its name is not a valid identifier)."""
    spec = importlib.util.spec_from_file_location("ansible_role_docker_example", EXAMPLE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


docker = load_example()

KEY_URL = "https://download.docker.com/linux/ubuntu/gpg"
KEY_BYTES = (
    b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
    b"\n"
    b"mQINBFit2ieBeADCgzd/knXj9B4q simulated Docker signing key\n"
    b"-----END PGP PUBLIC KEY BLOCK-----\n"
)
SOURCES_PATH = "/etc/apt/sources.list.d/docker.sources"
KEYRING_PATH = "/etc/apt/keyrings/docker.asc"
DAEMON_JSON_PATH = "/etc/docker/daemon.json"
DROPIN_PATH = "/etc/systemd/system/docker.service.d/reemote-exec-start.conf"
START_COMMAND = "/usr/bin/dockerd --config-file /etc/docker/daemon.json"
DAEMON_OPTIONS = {"log-driver": "json-file", "log-opts": {"max-size": "10m"}}


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride as extra keys -- reemote's ``host_vars`` -- so
    ``role_settings`` reads them straight off ``host.extra``. The overrides
    exercise the non-default branches: a docker_user, daemon options (the
    ``to_nice_json`` render) and an ExecStart override (the drop-in).
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
        "docker_users": ["alice"],
        "docker_daemon_options": DAEMON_OPTIONS,
        "docker_service_start_command": START_COMMAND,
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


def seed(host: SimulatedHost) -> None:
    """Pre-state: a stock Ubuntu machine with the distro Docker bits installed.

    The legacy key/source-list artifacts exist (the setup deploy removes
    them), two obsolete packages are installed, alice exists but is not in
    the docker group -- and the docker group itself does not exist yet: the
    docker-ce postinst creates it, which the migrate() bridge reproduces.
    """
    host.apt_packages = {"docker.io": ["20.10.24"], "containerd": ["1.6.20"]}
    host.apt_sources = []
    host.apt_update_stamp = None
    legacy = {
        docker.LEGACY_KEY_PATH: b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nold key\n",
        docker.LEGACY_REPO_LIST: (
            b"deb https://download.docker.com/linux/ubuntu noble stable\n"
        ),
        docker.LEGACY_DOCKER_LIST: (
            b"deb https://download.docker.com/linux/ubuntu noble stable\n"
        ),
    }
    host.remote_files = dict(legacy)
    host.existing_files = set(legacy)
    host.file_stats = {
        path: {"user": "root", "group": "root", "mode": 644, "size": len(data)}
        for path, data in legacy.items()
    }
    host.directory_stats = {}
    host.services = {}
    host.enabled_services = set()
    host.os_groups = ["root", "sudo", "alice"]
    host.os_users = {
        "alice": {
            "home": "/home/alice",
            "comment": None,
            "shell": "/bin/bash",
            "group": "alice",
            "groups": ["sudo"],
            "uid": 1001,
            "gid": 1001,
            "lastlog": None,
            "login_time": None,
            "password": None,
        }
    }
    host.linux_distribution = {
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


def make_hosts(inventory: list[dict[str, Any]]) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if docker.is_ubuntu(host):
            seed(host)
        hosts[host.name] = host
    return hosts


@pytest.fixture(autouse=True)
def offline_key_fetch(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Answer the URL signed_by form's control-side key fetch with canned bytes.

    ``apt.sources_file`` looks ``_fetch_url_bytes`` up as a module global at
    call time, so the patch covers every phase of every run. The returned
    list records each fetched URL, so a test can assert the derived key URL
    was the one requested (and that no fetch happens on the removal path).
    """
    fetched: list[str] = []

    def fake_fetch(url: str) -> bytes:
        fetched.append(url)
        return KEY_BYTES

    monkeypatch.setattr(apt_module, "_fetch_url_bytes", fake_fetch)
    return fetched


@pytest.fixture
def rig() -> tuple[list[dict[str, Any]], dict[str, SimulatedHost]]:
    """An inventory plus the persistent simulated hosts it materializes into."""
    inventory = make_inventory()
    return inventory, make_hosts(inventory)


async def migrate(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
    *,
    dry: bool = True,
) -> dict[str, Any]:
    """Replay the example's whole orchestration once, against persistent hosts.

    The sequence mirrors ``main()`` barrier for barrier -- the codename
    gather up front, the handler between the configure and user deploys, the
    ``Groups`` getent gate after the install -- but reads the simulated hosts
    instead of opening SSH connections.
    """
    targets = [host for host in hosts.values() if docker.is_ubuntu(host)]
    settings = {host.name: docker.role_settings(host) for host in targets}
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

    # The release codename for the repository's Suites (gather -> snapshot
    # -> factory). gather_fact does not close its hosts; the finally does.
    try:
        distributions = await gather_fact(targets, LinuxDistribution)
    finally:
        for host in hosts.values():
            await host.aclose()
    codenames = {
        host.name: docker.distribution_codename(distributions[host.name])
        for host in targets
    }

    # Deploys 1-2: the repository setup, then the packages.
    await execute(docker.build_setup(codenames))
    packages_report = await execute(docker.deploy_packages)

    # Simulation bridge (not a library bug): a simulated apt install records
    # package names only, so the docker group the real docker-ce postinst
    # creates is appended by hand -- the getent gate below then sees it.
    for host in targets:
        if (
            settings[host.name]["packages_state"] != "absent"
            and "docker" not in host.os_groups
        ):
            host.os_groups.append("docker")

    # Deploy 3: daemon.json, the ExecStart drop-in, the service state.
    configure_report = await execute(docker.deploy_configure)

    # Deploy 4: the restart docker handler (the role's flush_handlers).
    names = tuple(
        sorted({name for s in settings.values() for name in docker.notifying_names(s)})
    )
    notified = changed_hosts(packages_report, names) | changed_hosts(
        configure_report, names
    )
    managed = frozenset(name for name in notified if settings[name]["service_manage"])
    by_action: dict[str, list[str]] = {}
    for name in sorted(managed):
        action = settings[name]["restart_handler_state"]
        action = "running" if action == "started" else action
        by_action.setdefault(action, []).append(name)
    handlers = compose(
        *(
            service_handler(
                frozenset(action_names), ("docker",), action=action, when=docker.is_ubuntu
            )
            for action, action_names in sorted(by_action.items())
        )
    )
    handler_report = await execute(handlers)

    # The getent gate, then deploy 5: the docker group memberships.
    user_hosts = [host for host in targets if settings[host.name]["users"]]
    if user_hosts:
        try:
            groups = await gather_fact(user_hosts, Groups)
        finally:
            for host in hosts.values():
                await host.aclose()
        for host in user_hosts:
            if "docker" not in groups.get(host.name, []):
                raise ValueError(f"{host.name}: the docker group does not exist")
        await execute(docker.deploy_users)

    return {
        "reports": reports,
        "settings": settings,
        "codenames": codenames,
        "notified": notified,
        "handler_report": handler_report,
    }


def changed_entries(result: dict[str, Any]) -> list[Any]:
    """Every changed log entry across the whole sequence, handler deploy included."""
    return [entry for report in result["reports"] for entry in report.log if entry.changed]


# --- the first pass really deploys the role ----------------------------------


async def test_first_pass_installs_and_configures_docker(rig, offline_key_fetch):
    inventory, hosts = rig
    result = await migrate(inventory, hosts)
    host = hosts["ubuntu"]

    # The codename snapshot fed the repository's Suites.
    assert result["codenames"] == {"ubuntu": "noble"}

    # The legacy artifacts are gone (the File fact's seams: remove_file
    # leaves the content bytes in remote_files) and the obsolete packages
    # are removed.
    for path in (
        docker.LEGACY_KEY_PATH,
        docker.LEGACY_REPO_LIST,
        docker.LEGACY_DOCKER_LIST,
    ):
        assert path not in host.existing_files
        assert path not in host.file_stats
    assert "docker.io" not in host.apt_packages
    assert "containerd" not in host.apt_packages

    # The repository dependencies and the Docker packages are installed.
    assert "ca-certificates" in host.apt_packages
    assert "python3-debian" in host.apt_packages
    for package in (
        "docker-ce",
        "docker-ce-cli",
        "docker-ce-rootless-extras",
        "containerd.io",
        "docker-buildx-plugin",
        "docker-compose-plugin",
    ):
        assert package in host.apt_packages

    # issue #17: the key was fetched control-side from the derived URL by
    # apt.sources_file itself and installed as the armored keyring the
    # sources file references.
    assert offline_key_fetch.count(KEY_URL) >= 1
    assert host.remote_files[KEYRING_PATH] == KEY_BYTES
    assert host.file_stats[KEYRING_PATH]["mode"] == 644

    # The deb822 repository, with the frozen codename and the keyring path.
    sources = host.remote_files[SOURCES_PATH].decode()
    assert sources == (
        "Types: deb\n"
        "URIs: https://download.docker.com/linux/ubuntu\n"
        "Suites: noble\n"
        "Components: stable\n"
        f"Signed-By: {KEYRING_PATH}\n"
    )
    assert any(entry.get("filename") == SOURCES_PATH for entry in host.apt_sources)
    # The apt update handler, folded into the sources change (#14).
    assert host.apt_update_stamp is not None

    # daemon.json: the to_nice_json render, root-owned 0644, in a 0755 dir.
    content = host.remote_files[DAEMON_JSON_PATH].decode()
    assert content == json.dumps(DAEMON_OPTIONS, indent=4, sort_keys=True)
    assert host.file_stats[DAEMON_JSON_PATH] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(content.encode()),
    }
    assert host.directory_stats["/etc/docker"]["mode"] == 755

    # The ExecStart override drop-in (the role's in-place patch, reshaped).
    assert host.remote_files[DROPIN_PATH] == (
        f"[Service]\nExecStart=\nExecStart={START_COMMAND}\n"
    ).encode()

    # The service is running and enabled.
    assert host.services["docker.service"] == "active"
    assert "docker.service" in host.enabled_services

    # Every notifying task changed, so the restart handler fired.
    assert result["notified"] == frozenset({"ubuntu"})
    assert any(
        entry.name == "Restart docker (handler)" and entry.changed
        for entry in result["handler_report"].log
    )

    # alice was appended to the docker group, keeping her other memberships.
    assert host.os_users["alice"]["groups"] == ["sudo", "docker"]

    # The whole migration is operations: no shell action ever ran.
    assert host.ran_shells == []


async def test_non_ubuntu_host_is_never_touched(rig):
    inventory, hosts = rig
    result = await migrate(inventory, hosts)

    skipped = hosts["web1"]
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    assert skipped.services == {}
    assert skipped.os_users == {}
    assert skipped.ran_shells == []
    for report in result["reports"]:
        for entry in report.log:
            if entry.host == "web1":
                assert entry.skipped
                assert not entry.changed


@pytest.mark.parametrize(
    ("restart_state", "label", "expect_changed"),
    [
        ("reloaded", "Reload docker (handler)", True),
        # state: started maps to the running verb, which is fact-gated: the
        # service is already active, so the request is a logged no-op.
        ("started", "Start docker (handler)", False),
    ],
)
async def test_handler_honours_the_restart_state(restart_state, label, expect_changed):
    """docker_restart_handler_state selects the handler's verb, per host."""
    inventory = make_inventory(docker_restart_handler_state=restart_state)
    hosts = make_hosts(inventory)
    result = await migrate(inventory, hosts)

    assert result["notified"] == frozenset({"ubuntu"})
    entries = [entry for entry in result["handler_report"].log if entry.name == label]
    assert entries
    assert all(entry.changed == expect_changed for entry in entries)
    assert not any(
        entry.name == "Restart docker (handler)" for entry in result["handler_report"].log
    )


# --- the second pass is a no-op ----------------------------------------------


async def test_second_pass_changes_nothing_and_queues_no_handler(rig):
    inventory, hosts = rig
    first = await migrate(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    # The only bridge (the docker group's postinst creation) already ran in
    # pass 1; every other seam the second pass reads is maintained by the
    # simulation's own mutators.
    second = await migrate(inventory, hosts)

    assert changed_entries(second) == []
    assert second["notified"] == frozenset()
    # The handler deploy still runs, but it holds no request for any host:
    # nothing is restarted on a converged machine.
    assert not any(entry.changed for entry in second["handler_report"].log)
    assert hosts["ubuntu"].ran_shells == []


async def test_dry_preview_mutates_nothing(rig):
    """``dry=True`` stops after prepare, so it must not touch the target."""
    inventory, hosts = rig
    host = hosts["ubuntu"]
    before_files = dict(host.remote_files)
    before_existing = set(host.existing_files)
    before_stats = dict(host.file_stats)
    before_directories = dict(host.directory_stats)
    before_services = dict(host.services)
    before_users = dict(host.os_users)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    # The codename the LinuxDistribution seam answers, frozen as main() does.
    setup = docker.build_setup({"ubuntu": "noble"})
    await run(inventory, setup, host_factory=factory, dry=True)
    await run(inventory, docker.deploy_packages, host_factory=factory, dry=True)
    await run(inventory, docker.deploy_configure, host_factory=factory, dry=True)

    assert host.remote_files == before_files
    assert host.existing_files == before_existing
    assert host.file_stats == before_stats
    assert host.directory_stats == before_directories
    assert host.services == before_services
    assert host.os_users == before_users
    assert host.apt_packages == {"docker.io": ["20.10.24"], "containerd": ["1.6.20"]}
    assert host.apt_update_stamp is None


# --- the docker_add_repo=false branch -----------------------------------------


async def test_add_repo_false_removes_the_repository_and_key(offline_key_fetch):
    """state=absent removes the .sources file and the managed keyring (#17)."""
    inventory = make_inventory(docker_add_repo=False)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    # Pre-state: the repository and key a previous run configured.
    sources = (
        "Types: deb\n"
        "URIs: https://download.docker.com/linux/ubuntu\n"
        "Suites: noble\n"
        "Components: stable\n"
        f"Signed-By: {KEYRING_PATH}\n"
    ).encode()
    host.remote_files[SOURCES_PATH] = sources
    host.existing_files.add(SOURCES_PATH)
    host.file_stats[SOURCES_PATH] = {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(sources),
    }
    host.apt_sources = [
        {
            "type": "deb",
            "url": "https://download.docker.com/linux/ubuntu",
            "distribution": "noble",
            "components": ["stable"],
            "options": {"signed-by": [KEYRING_PATH]},
            "filename": SOURCES_PATH,
        }
    ]
    host.remote_files[KEYRING_PATH] = KEY_BYTES
    host.existing_files.add(KEYRING_PATH)
    host.file_stats[KEYRING_PATH] = {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(KEY_BYTES),
    }

    result = await migrate(inventory, hosts)

    # The sources file and the managed keyring are gone from every seam
    # (the same removal mutator clears both), and the removal chained the
    # apt update (#14) without any key fetch.
    assert SOURCES_PATH not in host.remote_files
    assert SOURCES_PATH not in host.existing_files
    assert SOURCES_PATH not in host.file_stats
    assert host.apt_sources == []
    assert KEYRING_PATH not in host.remote_files
    assert KEYRING_PATH not in host.existing_files
    assert KEYRING_PATH not in host.file_stats
    assert host.apt_update_stamp is not None
    assert offline_key_fetch == []
    assert any(
        entry.name == "Add or remove Docker repository" and entry.changed
        for entry in result["reports"][0].log
    )

    # And the removal branch converges on a second pass too.
    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []
    assert second["notified"] == frozenset()


# --- the pure helpers, unit-tested directly -----------------------------------


def test_role_settings_derives_the_role_defaults():
    host = SimulatedHost.from_inventory(make_inventory()[0])
    settings = docker.role_settings(host)
    assert settings["packages"] == (
        "docker-ce",
        "docker-ce-cli",
        "docker-ce-rootless-extras",
        "containerd.io",
        "docker-buildx-plugin",
    )
    assert settings["repo_url"] == "https://download.docker.com/linux/ubuntu"
    assert settings["gpg_key_url"] == KEY_URL
    assert settings["users"] == ("alice",)
    assert settings["packages_state"] == "present"
    assert settings["service_manage"] is True
    assert settings["restart_handler_state"] == "restarted"

    # A bare entry (no extras) reproduces the role's templated defaults.
    bare = SimulatedHost.from_inventory(
        {"host": "bare", "family": "Ubuntu", "connection": {"address": "192.0.2.12"}, "group": "ubuntu"}
    )
    defaults = docker.role_settings(bare)
    assert defaults["users"] == ()
    assert defaults["daemon_json"] is None
    assert defaults["start_command"] == ""


def test_role_settings_mirror_override_keeps_the_upstream_key_url():
    """docker_apt_repo_url overrides the URI; the key URL follows docker_repo_url."""
    host = SimulatedHost.from_inventory(
        make_inventory(docker_apt_repo_url="https://mirror.example/docker")[0]
    )
    settings = docker.role_settings(host)
    assert settings["repo_url"] == "https://mirror.example/docker"
    assert settings["gpg_key_url"] == KEY_URL


def test_daemon_json_matches_to_nice_json():
    """Ansible's to_nice_json is json.dumps(indent=4, sort_keys=True), verbatim."""
    host = SimulatedHost.from_inventory(make_inventory()[0])
    settings = docker.role_settings(host)
    assert settings["daemon_json"] == json.dumps(DAEMON_OPTIONS, indent=4, sort_keys=True)
    assert not settings["daemon_json"].endswith("\n")


def test_distribution_codename_reads_release_meta():
    info = {"release_meta": {"VERSION_CODENAME": "noble"}}
    assert docker.distribution_codename(info) == "noble"
    with pytest.raises(ValueError, match="VERSION_CODENAME"):
        docker.distribution_codename({"release_meta": {}})
    with pytest.raises(ValueError, match="VERSION_CODENAME"):
        docker.distribution_codename({})


def test_notifying_names_enumerates_the_notifying_requests():
    host = SimulatedHost.from_inventory(make_inventory()[0])
    settings = docker.role_settings(host)
    assert docker.notifying_names(settings) == (
        docker.NOTIFY_PACKAGE,
        docker.NOTIFY_COMPOSE_PLUGIN,
        docker.NOTIFY_DAEMON_JSON,
        docker.NOTIFY_EXEC_START,
    )
    minimal = docker.role_settings(
        SimulatedHost.from_inventory(
            {"host": "bare", "family": "Ubuntu", "connection": {"address": "192.0.2.12"}, "group": "ubuntu"}
        )
    )
    assert docker.notifying_names(minimal) == (
        docker.NOTIFY_PACKAGE,
        docker.NOTIFY_COMPOSE_PLUGIN,
    )


@pytest.mark.parametrize(
    ("overrides", "error", "match"),
    [
        ({"docker_packages_state": "purged"}, ValueError, "docker_packages_state"),
        ({"docker_service_state": "running"}, ValueError, "docker_service_state"),
        ({"docker_restart_handler_state": "stopped"}, ValueError, "docker_restart_handler_state"),
        ({"docker_packages": "docker-ce"}, TypeError, "docker_packages"),
        ({"docker_users": "alice"}, TypeError, "docker_users"),
        ({"docker_daemon_options": ["log-driver"]}, TypeError, "docker_daemon_options"),
        ({"docker_install_compose": True}, ValueError, "docker_install_compose"),
    ],
)
def test_invalid_role_values_fail_fast(overrides, error, match):
    host = SimulatedHost.from_inventory(make_inventory(**overrides)[0])
    with pytest.raises(error, match=match):
        docker.role_settings(host)