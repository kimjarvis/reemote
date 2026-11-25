# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``docker`` role (§17.5, §18.11).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, docker(v), host_factory=...)``. The factory is plain
(non-``async``) -- every read is a ``fact`` barrier inside the body, and the
signing-key fetch is ``apt.sources_file``'s own control-side step -- so it is
called, not awaited.

The load-bearing assertion is the migration's shape claim from §18.11: the
stream is **two checkpoints that the one handler reads as a union**, plus a
conditional ``fact(Groups)`` gate -- ``fact(LinuxDistribution)`` then
``setup+packages`` closed by ``checkpoint("packages done")`` then
``configure`` closed by ``checkpoint("configure done")`` then the restart
handler, then (users only) the getent gate and the memberships. That shape is
read straight out of ``report.records``: each barrier record carries its
request type, a fact one naming its ``fact_cls``.

Two simulation bridges are needed, and both are simulation limits, not library
bugs. The first is the ``Groups`` gate: a simulated apt install records package
names only, never the ``docker`` group the real docker-ce postinst creates, so
``seed`` pre-states ``os_groups`` with the intended post-install group list --
the same trick ntp's tests use for ``systemd_unit_files``, standing in for the
old example's mid-orchestration bridge because the whole run is now one
``run_waves`` call. The second is the signing key: the ``signed_by`` URL form
fetches the key control-side over HTTPS, so the ``key_fetch`` fixture
monkeypatches **``apt._fetch_url_bytes``** (the apt namespace's own seam, not
``files``') with canned bytes and records the URLs, keeping the suite offline.

The service runtime state (``services``/``enabled_services``) and the user
membership store (``os_users``) need no bridge -- the simulation's systemctl
and usermod arms maintain them across passes, which is what makes the second
pass a no-op.
"""

import json

import pytest

import reemote.modules.apt.apt as apt_module
from reemote import run_waves
from reemote.modules.server import Groups, LinuxDistribution
from reemote.roles import DockerVars, docker
from reemote.roles.docker import (
    DEPENDENCY_PACKAGES,
    EXEC_START_DROPIN,
    LEGACY_DOCKER_LIST,
    LEGACY_KEY_PATH,
    LEGACY_REPO_LIST,
    derive_packages,
    distribution_codename,
    docker_plan,
    notifying_names,
    render_daemon_json,
)
from reemote.rolevars import resolve_vars
from reemote.stages import CheckpointRequest, FactRequest
from tests.simulated_host import SimulatedHost

SOURCES_PATH = "/etc/apt/sources.list.d/docker.sources"
KEYRING_PATH = "/etc/apt/keyrings/docker.asc"
DAEMON_JSON_PATH = "/etc/docker/daemon.json"
DROPIN_PATH = f"/etc/systemd/system/docker.service.d/{EXEC_START_DROPIN}.conf"
START_COMMAND = "/usr/bin/dockerd -H fd:// --containerd=/run/containerd/containerd.sock"
KEY_BYTES = b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nfake\n-----END PGP PUBLIC KEY BLOCK-----\n"
DAEMON_OPTIONS = {"log-driver": "json-file", "log-opts": {"max-size": "10m"}}

EXPECTED_SOURCES = (
    "Types: deb\n"
    "URIs: https://download.docker.com/linux/ubuntu\n"
    "Suites: noble\n"
    "Components: stable\n"
    "Signed-By: /etc/apt/keyrings/docker.asc\n"
)

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


@pytest.fixture
def key_fetch(monkeypatch):
    """Make the ``signed_by`` URL fetch offline and observable.

    ``apt.sources_file`` fetches the ASCII-armored key control-side through
    the apt namespace's own ``_fetch_url_bytes`` seam (not ``files``' -- the
    namespaces never import each other); this stands in for the network, the
    same seam the old example patched, and records every URL asked for.
    """
    fetched: list[str] = []

    def fake(url: str) -> bytes:
        fetched.append(url)
        return KEY_BYTES

    monkeypatch.setattr(apt_module, "_fetch_url_bytes", fake)
    return fetched


def make_inventory(**overrides) -> list[dict]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride as ``docker_``-prefixed extra keys, bound by
    ``resolve_vars``. The base entry sets ``docker_users`` and
    ``docker_daemon_options`` so the default path exercises the getent gate,
    the memberships, and the ``restart docker`` handler the daemon.json write
    notifies; the overrides drive the non-default branches.
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
        "docker_users": ["alice"],
        "docker_daemon_options": DAEMON_OPTIONS,
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


def seed(host: SimulatedHost, *, docker_group: bool = True) -> None:
    """Pre-state: a stock Ubuntu 24.04 machine with distro Docker bits present.

    ``os_groups`` carries the post-install bridge (``docker_group=True``: the
    group a real docker-ce postinst creates after the packages wave, which a
    simulated install cannot make); ``docker_group=False`` leaves it absent for
    the getent-gate-raises scenario. The three legacy artifacts exist as files
    so the setup wave's removals are real changes, ``docker.io``/``containerd``
    are installed so the obsolete-package purge changes, and ``alice`` exists
    with one unrelated group so the membership append is observable.
    """
    host.linux_distribution = dict(UBUNTU_2404)
    host.apt_packages = {"docker.io": [""], "containerd": [""]}
    host.apt_update_stamp = None
    host.os_groups = ["sudo", "wheel", "docker"] if docker_group else ["sudo", "wheel"]
    host.os_users = {
        "alice": {"groups": ["sudo"], "home": "/home/alice", "shell": "/bin/bash"},
    }
    host.services = {}
    host.enabled_services = set()
    host.remote_files = {
        LEGACY_KEY_PATH: b"legacy armored key",
        LEGACY_REPO_LIST: b"deb https://download.docker.com/linux/ubuntu noble stable\n",
        LEGACY_DOCKER_LIST: b"deb [arch=amd64] https://download.docker.com/linux/ubuntu noble stable\n",
    }
    host.existing_files = set(host.remote_files)
    host.file_stats = {path: {"mode": 644} for path in host.remote_files}
    host.directory_stats = {}


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
    v = resolve_vars(DockerVars, "docker_", inventory)

    def factory(entry: dict) -> SimulatedHost:
        # The *same* host object every pass, so state survives the barriers.
        return hosts[entry["host"]]

    # A plain (non-async) factory: called, not awaited.
    deploy = docker(v)
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
    itself. An ``act``/``control`` barrier would fall through to a distinct
    label, so a speculatively-added barrier breaks the shape test rather than
    hiding.
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


# --- the stream shape (§18.11's falsifier) ------------------------------------


async def test_stream_is_two_checkpoints_and_a_groups_gate(key_fetch):
    """§18.11's shape: fact -> setup+packages+checkpoint -> configure+checkpoint
    -> handler -> ``fact(Groups)`` gate -> memberships.

    Exactly two checkpoints (the handler's notification set spans both reports,
    so they cannot share one, unlike nginx) and one conditional ``fact`` gate
    after the handler, closing the packages wave's simulation bridge. A single
    shared checkpoint or a whole-inventory ``names`` union reconstituted as an
    extra barrier would break this.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert _barrier_shape(report) == [
        (0, ("fact", LinuxDistribution)),
        (1, ("checkpoint", "packages done")),
        (2, ("checkpoint", "configure done")),
        (3, ("fact", Groups)),
    ]
    # Requests land in four waves: setup+packages (1), configure (2), the
    # restart handler (3), the memberships (4).
    assert _request_waves(report) == {1, 2, 3, 4}

    # The rerun re-derives every barrier, so the shape is stable across passes.
    second = await run_role(inventory, hosts)
    assert _barrier_shape(second) == _barrier_shape(report)


async def test_no_users_skips_the_groups_gate(key_fetch):
    # Without docker_users there is no getent task in the role, so the body
    # ends at the handler: the gate barrier and the membership wave vanish.
    inventory = make_inventory(docker_users=[])
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert _barrier_shape(report) == [
        (0, ("fact", LinuxDistribution)),
        (1, ("checkpoint", "packages done")),
        (2, ("checkpoint", "configure done")),
    ]
    assert _request_waves(report) == {1, 2, 3}


# --- the first pass really deploys the role -----------------------------------


async def test_first_pass_installs_and_configures_docker(key_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    # The three legacy artifacts are gone and the distro packages purged.
    assert LEGACY_KEY_PATH not in host.existing_files
    assert LEGACY_REPO_LIST not in host.existing_files
    assert LEGACY_DOCKER_LIST not in host.existing_files
    assert "docker.io" not in host.apt_packages
    assert "containerd" not in host.apt_packages

    # The deb822 sources landed exactly as the role's deb822_repository task
    # wrote it, and the keyring under the repo's own filename (issue #17).
    assert host.remote_files[SOURCES_PATH].decode() == EXPECTED_SOURCES
    assert host.remote_files[KEYRING_PATH] == KEY_BYTES
    assert host.file_stats[KEYRING_PATH]["mode"] == 644
    assert key_fetch == ["https://download.docker.com/linux/ubuntu/gpg"]

    # Dependencies plus the derived edition's five packages and the plugin.
    for package in (*DEPENDENCY_PACKAGES, *derive_packages("ce"), "docker-compose-plugin"):
        assert package in host.apt_packages

    # daemon.json landed with to_nice_json semantics: indent 4, sorted keys,
    # no trailing newline, root-owned 0644.
    assert host.remote_files[DAEMON_JSON_PATH] == json.dumps(
        DAEMON_OPTIONS, indent=4, sort_keys=True
    ).encode()
    assert host.file_stats[DAEMON_JSON_PATH]["mode"] == 644

    # The service is running and enabled.
    assert host.services["docker.service"] == "active"
    assert "docker.service" in host.enabled_services

    # Both notifying deploys changed, so the handler fired in its own wave.
    log = ubuntu_log(report)
    assert log["Install Docker packages"].changed
    assert log["Configure Docker daemon options"].changed
    assert log["Restart docker (handler)"].changed

    # The membership landed as a pure append: alice keeps sudo, gains docker,
    # and her home was never touched (D5's ensure_home=False).
    assert host.os_users["alice"]["groups"] == ["sudo", "docker"]


async def test_second_pass_is_a_noop(key_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    # The migration's idempotence claim: nothing changed anywhere and no
    # handler ran -- obsolete purge done, repo and key in place, packages
    # present, daemon.json byte-identical, service running/enabled, alice
    # already appended -- so neither checkpoint notified.
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert "Restart docker (handler)" not in ubuntu_names(second)


async def test_non_ubuntu_host_is_never_touched(key_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    skipped = hosts["web1"]

    # The family guard returns before the first yield, so the off-target host
    # is in no wave, emits no log line and its stores never move.
    assert not any(entry.host == "web1" for entry in report.merged().log)
    assert not any(r.host == "web1" for r in report.records)
    assert skipped.remote_files == {}
    assert skipped.services == {}
    assert skipped.enabled_services == set()


async def test_dry_preview_stops_at_the_distribution_barrier(key_fetch):
    """``dry=True`` never runs a barrier, so it parks on the first item.

    docker's first yielded item is the ``LinuxDistribution`` ``fact`` barrier,
    so the dry preview plans no request and applies nothing -- the whole stream
    stays unexecuted.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts, dry=True)

    assert host.apt_update_stamp is None
    assert SOURCES_PATH not in host.remote_files
    assert DAEMON_JSON_PATH not in host.remote_files
    assert host.services == {}
    assert changed_entries(report) == []
    # Exactly one barrier record, no request records, and no key was fetched.
    assert _barrier_shape(report) == [(0, ("fact", LinuxDistribution))]
    assert _request_waves(report) == set()
    assert key_fetch == []


# --- the two-checkpoint union --------------------------------------------------


async def test_handler_reads_the_packages_checkpoint_too(key_fetch):
    """The notification set spans *both* reports (the php shape, §18.6).

    Pass two here is rigged from a converged pass one by uninstalling only the
    Docker packages: the configure wave stays byte-identical (no notification
    from checkpoint two), but the reinstall notifies through checkpoint one and
    the restart handler must still fire. A role that read only the configure
    report -- a single shared checkpoint -- would silently skip it.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    host = hosts["ubuntu"]
    for package in derive_packages("ce"):
        host.apt_packages.pop(package, None)

    second = await run_role(inventory, hosts)
    log = ubuntu_log(second)
    assert log["Install Docker packages"].changed
    assert log["Configure Docker daemon options"].changed is False
    assert log["Restart docker (handler)"].changed


# --- the handler verb and its gates -------------------------------------------


@pytest.mark.parametrize(
    ("state", "entry"),
    [
        ("reloaded", "Reload docker (handler)"),
        ("started", "Start docker (handler)"),
    ],
)
async def test_restart_handler_state_selects_the_verb(key_fetch, state, entry):
    # restart_handler_state maps through HANDLER_ACTIONS to the service_handler
    # verb, and each host reads its own -- the old by_action partition is gone.
    inventory = make_inventory(docker_restart_handler_state=state)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)

    assert log["Configure Docker daemon options"].changed
    assert entry in log
    assert "Restart docker (handler)" not in log


async def test_service_manage_false_suppresses_service_and_handler(key_fetch):
    # The role gates BOTH the service task and the handler on
    # docker_service_manage; the install and daemon.json still apply.
    inventory = make_inventory(docker_service_manage=False)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)

    assert "Ensure Docker is started and enabled at boot" not in log
    assert "Restart docker (handler)" not in log
    assert host.services == {}
    assert log["Install Docker packages"].changed
    assert host.remote_files[DAEMON_JSON_PATH]


# --- the repository branch ------------------------------------------------------


async def test_add_repo_false_removes_the_repo(key_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    await run_role(inventory, hosts)  # the repo starts out managed
    assert SOURCES_PATH in host.existing_files
    assert len(key_fetch) == 1

    # Flipping docker_add_repo removes the sources file and the managed
    # keyring, and the removal fetches nothing -- a key is only needed to sign.
    host.apt_update_stamp = None  # so the chained refresh below is observable
    inventory[0]["docker_add_repo"] = False
    await run_role(inventory, hosts)
    assert SOURCES_PATH not in host.existing_files
    assert KEYRING_PATH not in host.remote_files
    assert len(key_fetch) == 1

    # The removal still chains the apt index refresh (D1, issue #14): the only
    # request that could have set the stamp is the sources removal.
    assert host.apt_update_stamp is not None

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the ExecStart override ------------------------------------------------------


async def test_start_command_lands_as_a_dropin(key_fetch):
    # D2: the override is a drop-in with the empty ExecStart= line first, not
    # an in-place patch of the packaged unit.
    inventory = make_inventory(docker_service_start_command=START_COMMAND)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    assert host.remote_files[DROPIN_PATH] == (
        f"[Service]\nExecStart=\nExecStart={START_COMMAND}\n".encode()
    )
    assert ubuntu_log(report)["Override docker.service ExecStart"].changed

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the getent gate ---------------------------------------------------------------


async def test_missing_docker_group_fails_the_run(key_fetch):
    # The role's getent task fails the play when the docker group is absent;
    # here the simulated install never created it (no post-install bridge),
    # so the body raises at its own barrier, after the waves that did apply.
    inventory = make_inventory()
    hosts = make_hosts(inventory, docker_group=False)
    host = hosts["ubuntu"]

    with pytest.raises(ValueError, match="docker group does not exist"):
        await run_role(inventory, hosts)

    # The install itself is not rolled back, but no membership was attempted.
    assert "alice" in host.os_users
    assert host.os_users["alice"]["groups"] == ["sudo"]


# --- the pure helpers, unit-tested directly -----------------------------------------


def test_derive_packages_follows_the_edition():
    assert derive_packages("ce") == (
        "docker-ce",
        "docker-ce-cli",
        "docker-ce-rootless-extras",
        "containerd.io",
        "docker-buildx-plugin",
    )
    assert derive_packages("ee") == (
        "docker-ee",
        "docker-ee-cli",
        "docker-ee-rootless-extras",
        "containerd.io",
        "docker-buildx-plugin",
    )


def test_render_daemon_json_is_to_nice_json():
    assert render_daemon_json({}) is None
    rendered = render_daemon_json({"z-option": 1, "a-option": 2})
    assert rendered == '{\n    "a-option": 2,\n    "z-option": 1\n}'
    assert not rendered.endswith("\n")


def test_distribution_codename_reads_release_meta():
    assert distribution_codename(UBUNTU_2404) == "noble"
    with pytest.raises(ValueError, match="VERSION_CODENAME"):
        distribution_codename({"name": "Ubuntu"})
    with pytest.raises(ValueError, match="VERSION_CODENAME"):
        distribution_codename({"release_meta": {"VERSION_CODENAME": ""}})


def test_notifying_names_follows_the_plan_fields():
    base = {
        "install_compose_plugin": False,
        "daemon_json": None,
        "start_command": "",
    }
    assert notifying_names(base) == ("Install Docker packages",)
    assert notifying_names({**base, "install_compose_plugin": True}) == (
        "Install Docker packages",
        "Install docker-compose-plugin",
    )
    assert notifying_names({**base, "daemon_json": "{}"}) == (
        "Install Docker packages",
        "Configure Docker daemon options",
    )
    assert notifying_names({**base, "start_command": "/usr/bin/dockerd"}) == (
        "Install Docker packages",
        "Override docker.service ExecStart",
    )


def test_docker_plan_derivations():
    info = dict(UBUNTU_2404)
    plan = docker_plan(DockerVars(), info)
    # Packages from the edition, the repo/key URLs from repo_url.
    assert plan.packages == derive_packages("ce")
    assert plan.repo_url == "https://download.docker.com/linux/ubuntu"
    assert plan.gpg_key_url == "https://download.docker.com/linux/ubuntu/gpg"
    assert plan.codename == "noble"
    assert plan.packages_present is True and plan.packages_latest is False
    assert plan.handler_action == "restarted"
    assert plan.daemon_json is None
    assert plan.notify_names == ("Install Docker packages", "Install docker-compose-plugin")

    # A mirror override changes the URI only: the key URL follows the
    # *upstream* repo, exactly as the role's defaults template it.
    mirrored = docker_plan(
        DockerVars(apt_repo_url="https://mirror.example.com/docker-ce/linux/ubuntu"),
        info,
    )
    assert mirrored.repo_url == "https://mirror.example.com/docker-ce/linux/ubuntu"
    assert mirrored.gpg_key_url == "https://download.docker.com/linux/ubuntu/gpg"

    # The state enums split into the operation's present/latest booleans.
    latest = docker_plan(DockerVars(packages_state="latest", service_state="stopped"), info)
    assert latest.packages_present is True and latest.packages_latest is True
    assert latest.service_running is False
    removed = docker_plan(DockerVars(packages_state="absent"), info)
    assert removed.packages_present is False and removed.packages_latest is False


# --- the typed variables reject bad input ------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"packages_state": "foo"}, "packages_state"),
        ({"service_state": "bogus"}, "service_state"),
        ({"restart_handler_state": "bogus"}, "restart_handler_state"),
        ({"compose_package_state": "bogus"}, "compose_package_state"),
        ({"install_compose": True}, "scoped out"),
        ({"repo_url": "https://download.docker.com/linux/"}, "slash"),
        ({"edition": ""}, "non-empty"),
        ({"apt_filename": ""}, "non-empty"),
        ({"users": [""]}, "non-empty strings"),
        ({"obsolete_packages": ["docker", ""]}, "non-empty strings"),
    ],
)
def test_vars_reject_invalid_values(kwargs, match):
    # pydantic's ValidationError is a ValueError; so is every validator raise.
    # The message names the (unprefixed) field. The old example raised
    # TypeError for several of these gates -- now all are ValueError.
    with pytest.raises(ValueError, match=match):
        DockerVars(**kwargs)


def test_users_list_becomes_a_tuple():
    # A list from JSON is normalized to a tuple so every value that reaches
    # op() stays hashable (daemon_options is the one mapping, and it only
    # reaches an operation already rendered to a str).
    hv = DockerVars(users=["alice", "bob"], packages=["docker-ce"])
    assert hv.users == ("alice", "bob")
    assert hv.packages == ("docker-ce",)
