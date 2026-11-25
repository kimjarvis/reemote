# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``java`` role (§17.5, §18.12).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, java(v), host_factory=...)``. The factory is plain
(non-``async``) -- the one read is a ``fact`` barrier inside the body -- so it
is called, not awaited.

The load-bearing assertion is the migration's shape claim from §18.12: **one
barrier and one request wave** -- ``fact(LinuxDistribution)`` closes wave 0 and
the whole role (man1 directory, apt install, JAVA_HOME profile) lands in wave
1. No checkpoint and no second read exist: the role has no handlers and no
outcome reads, and a speculatively-added barrier breaks the shape test rather
than hiding.

No simulation bridge is needed, unlike the datadog/docker tests: the
simulation's own mutators maintain every seam the second pass reads --
``install_apt_packages`` records the package, ``make_directory``/``chmod_path``
the man1 row, ``install_file`` the profile content and its stat row, and the
``AptUpdateCommand`` arm refreshes ``apt_update_stamp`` to wall-clock now, so
the folded cache refresh (deviation D1) skips on the second apply.
"""

import pytest

from reemote import run_waves
from reemote.modules.server import LinuxDistribution
from reemote.roles import JavaVars, java
from reemote.roles.java import (
    JAVA_HOME_PROFILE,
    MAN_DIR,
    UBUNTU_JAVA_PACKAGES,
    java_plan,
    render_java_home,
    ubuntu_major,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from reemote.stages import CheckpointRequest, FactRequest
from tests.simulated_host import SimulatedHost

JAVA_HOME = "/usr/lib/jvm/java-21-openjdk-amd64"
PROFILE_CONTENT = f"export JAVA_HOME={JAVA_HOME}\n".encode()

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

    The role variables ride as ``java_``-prefixed extra keys, bound by
    ``resolve_vars``. The base entry sets ``java_home`` so the default path
    exercises the profile template branch; the overrides drive the
    non-default branches (``java_packages``, the empty-home skip).
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
        "java_home": JAVA_HOME,
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


def seed(host: SimulatedHost, major: str = "24", name: str = "Ubuntu") -> None:
    """Pre-state: a stock minimal Ubuntu machine with no Java installed.

    The man1 directory is absent (the creation branch of the workaround), the
    apt cache has never been refreshed (the folded update runs), and no
    profile script exists yet. ``major``/``name`` drive the vars-table
    selection and the fail-fast gates.
    """
    host.linux_distribution = {**UBUNTU_2404, "name": name, "major": major}
    host.apt_packages = {}
    host.apt_sources = []
    host.apt_update_stamp = None
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.directory_stats = {}
    host.services = {}
    host.enabled_services = set()
    host.os_groups = ["root", "sudo"]
    host.os_users = {}


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
    v = resolve_vars(JavaVars, "java_", inventory)

    def factory(entry: dict) -> SimulatedHost:
        # The *same* host object every pass, so state survives the barrier.
        return hosts[entry["host"]]

    # A plain (non-async) factory: called, not awaited.
    deploy = java(v)
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
    hiding -- §18.12: "if a checkpoint appears, one was added speculatively".
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


# --- the stream shape (§18.12's falsifier) ------------------------------------


async def test_stream_is_one_barrier_and_one_request_wave():
    """§18.12's shape: fact(LinuxDistribution) -> the whole role, one wave.

    Exactly one barrier and no checkpoint: the role has no handler and no
    outcome read, so a checkpoint would close a wave nobody reads. The rerun
    re-derives the same shape, so it is stable across passes.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert _barrier_shape(report) == [(0, ("fact", LinuxDistribution))]
    assert _request_waves(report) == {1}

    second = await run_role(inventory, hosts)
    assert _barrier_shape(second) == _barrier_shape(report)
    assert _request_waves(second) == {1}


# --- the first pass really deploys the role ------------------------------------


async def test_first_pass_installs_java_and_configures_profile():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    # The vars table selected Ubuntu 24's package list.
    assert "openjdk-21-jdk" in host.apt_packages

    # The man1 workaround directory exists with the task's mode.
    assert host.directory_stats[MAN_DIR]["mode"] == 755

    # The folded cache refresh ran (the stamp was absent).
    assert host.apt_update_stamp is not None

    # The JAVA_HOME profile: the f-string render, root-owned 0644.
    assert host.remote_files[JAVA_HOME_PROFILE] == PROFILE_CONTENT
    assert host.file_stats[JAVA_HOME_PROFILE] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(PROFILE_CONTENT),
    }

    # Every request changed on a virgin host; the whole role is operations.
    log = ubuntu_log(report)
    assert log["Ensure 'man' directory exists"].changed
    assert log["Ensure Java is installed"].changed
    assert log["Set JAVA_HOME if configured"].changed
    # The mode companion is a no-op on the first pass: files.install writes
    # with the task's mode, so there is no drift to converge yet.
    assert log["Set JAVA_HOME profile script mode"].changed is False
    assert host.ran_shells == []


async def test_mode_drift_is_converged_by_the_companion():
    """The files.file companion's whole purpose: content-only install.

    With the content byte-identical but the mode drifted, files.install
    reports no change (its idempotence is content-only) while the paired
    files.file re-applies 0644 -- the behaviour the role's template task
    ``mode=0644`` enforces on every run (the apache ports.conf precedent).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    host = hosts["ubuntu"]
    host.file_stats[JAVA_HOME_PROFILE]["mode"] = 640

    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)
    assert log["Set JAVA_HOME if configured"].changed is False
    assert log["Set JAVA_HOME profile script mode"].changed
    assert host.file_stats[JAVA_HOME_PROFILE]["mode"] == 644


async def test_second_pass_is_a_noop():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    # The migration's idempotence claim, with no bridge: the package is
    # installed, the stamp is fresh, the man1 row and the profile content and
    # mode all match.
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert hosts["ubuntu"].ran_shells == []


async def test_non_ubuntu_host_is_never_touched():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    skipped = hosts["web1"]

    # The family guard returns before the first yield, so the off-target host
    # is in no wave, emits no log line, and never even gathers the fact.
    assert not any(entry.host == "web1" for entry in report.merged().log)
    assert not any(r.host == "web1" for r in report.records)
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    assert skipped.directory_stats == {}
    assert skipped.ran_shells == []


async def test_dry_preview_stops_at_the_distribution_barrier():
    """``dry=True`` never runs a barrier, so it parks on the first item.

    java's first yielded item is the ``LinuxDistribution`` ``fact`` barrier, so
    the dry preview plans no request and applies nothing -- the whole stream
    stays unexecuted.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts, dry=True)

    assert _barrier_shape(report) == [(0, ("fact", LinuxDistribution))]
    assert _request_waves(report) == set()
    assert changed_entries(report) == []
    assert host.apt_packages == {}
    assert host.apt_update_stamp is None
    assert host.remote_files == {}
    assert host.directory_stats == {}


# --- the override and gate branches -------------------------------------------


async def test_packages_override_wins_and_empty_home_skips_profile():
    """An explicit java_packages list beats the vars table; java_home='' skips."""
    inventory = make_inventory(java_packages=["openjdk-17-jdk"], java_home="")
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    report = await run_role(inventory, hosts)

    assert "openjdk-17-jdk" in host.apt_packages
    assert "openjdk-21-jdk" not in host.apt_packages
    # The template task's `when: java_home != ""` skipped the profile.
    assert JAVA_HOME_PROFILE not in host.remote_files
    assert JAVA_HOME_PROFILE not in host.existing_files
    # The gate-skipped requests were never emitted at all.
    names = ubuntu_names(report)
    assert "Set JAVA_HOME if configured" not in names

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_empty_packages_still_refreshes_the_cache():
    """java_packages=[] is a valid Ansible override: apt installs nothing."""
    inventory = make_inventory(java_packages=[], java_home="")
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    await run_role(inventory, hosts)

    assert host.apt_packages == {}
    # The folded cache refresh still runs (the molecule pre_task is
    # unconditional), and the second pass skips it on the fresh stamp.
    assert host.apt_update_stamp is not None
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_ubuntu_16_skips_the_man_directory():
    """The man1 workaround is gated on Ubuntu >= 18; 16 gets openjdk-8-jdk."""
    inventory = make_inventory(java_home="")
    hosts = make_hosts(inventory, major="16")
    host = hosts["ubuntu"]

    report = await run_role(inventory, hosts)

    assert "openjdk-8-jdk" in host.apt_packages
    assert MAN_DIR not in host.directory_stats
    # The gated request never appears in the log at all.
    assert "Ensure 'man' directory exists" not in ubuntu_names(report)


# --- the fail-fast gates, through the driver ------------------------------------


async def test_non_ubuntu_distribution_raises_at_the_barrier():
    """D3: a host declared Ubuntu that reports Debian fails before any request."""
    inventory = make_inventory()
    hosts = make_hosts(inventory, name="Debian")
    host = hosts["ubuntu"]

    with pytest.raises(ValueError, match="targets Ubuntu"):
        await run_role(inventory, hosts)

    # The raise is at the barrier, before the request wave is built.
    assert host.apt_packages == {}
    assert host.remote_files == {}


async def test_unsupported_major_raises_even_with_override():
    """D4: include_vars runs unconditionally, so the override cannot rescue 25."""
    inventory = make_inventory(java_packages=["openjdk-21-jdk"])
    hosts = make_hosts(inventory, major="25")
    host = hosts["ubuntu"]

    with pytest.raises(ValueError, match="vars/Ubuntu-25.yml"):
        await run_role(inventory, hosts)

    assert host.apt_packages == {}


# --- the pure helpers, unit-tested directly -------------------------------------


def test_render_java_home_matches_the_template():
    """The f-string render is the template source with the variable filled."""
    assert render_java_home(JAVA_HOME) == PROFILE_CONTENT.decode()
    assert render_java_home("/x").endswith("\n")


def test_ubuntu_major_normalizes_and_gates():
    assert ubuntu_major({"name": "Ubuntu", "major": "24"}) == 24
    assert ubuntu_major({"name": "Ubuntu", "major": 24}) == 24
    with pytest.raises(ValueError, match="targets Ubuntu"):
        ubuntu_major({"name": "Debian", "major": "12"})
    with pytest.raises(ValueError, match="major version"):
        ubuntu_major({"name": "Ubuntu"})
    with pytest.raises(ValueError, match="major version"):
        ubuntu_major({"name": "Ubuntu", "major": "noble"})


def test_java_plan_selects_the_vars_table_and_gates():
    default = JavaVars()
    plan = java_plan(default, {"name": "Ubuntu", "major": "24"})
    assert plan.packages == UBUNTU_JAVA_PACKAGES[24]
    assert plan.man_dir is True
    assert plan.java_home_content is None  # the empty-home default skips

    # The full table, and the >= 18 man-directory boundary on both sides.
    for major, packages in UBUNTU_JAVA_PACKAGES.items():
        plan = java_plan(default, {"name": "Ubuntu", "major": str(major)})
        assert plan.packages == packages
        assert plan.man_dir is (major >= 18)

    # An override wins on a supported release; the render follows home.
    overridden = JavaVars(packages=("openjdk-17-jdk",), home=JAVA_HOME)
    plan = java_plan(overridden, {"name": "Ubuntu", "major": "22"})
    assert plan.packages == ("openjdk-17-jdk",)
    assert plan.java_home_content == PROFILE_CONTENT.decode()

    # An unsupported major raises as the role's include_vars would -- even
    # with an override (include_vars runs unconditionally).
    with pytest.raises(ValueError, match="vars/Ubuntu-25.yml"):
        java_plan(overridden, {"name": "Ubuntu", "major": "25"})


# --- the variables model ---------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "error", "match"),
    [
        ({"packages": "openjdk-21-jdk"}, ValueError, "packages"),
        ({"packages": [42]}, ValueError, "packages"),
        ({"packages": [""]}, ValueError, "packages"),
        ({"home": 42}, ValueError, "home"),
        ({"unknown": "x"}, ValueError, "unknown"),  # extra="forbid"
    ],
)
def test_invalid_role_values_fail_fast(overrides, error, match):
    with pytest.raises(error, match=match):
        JavaVars.model_validate(overrides)


def test_prefixed_inventory_keys_bind_and_typo_raises():
    """resolve_vars strips ``java_`` and reports a prefixed misspelling."""
    inventory = make_inventory(java_packages=["openjdk-17-jdk"])
    v = resolve_vars(JavaVars, "java_", inventory)
    hv = v.for_host(SimulatedHost.from_inventory(inventory[0]))
    assert hv.packages == ("openjdk-17-jdk",)  # a list normalized to a tuple
    assert hv.home == JAVA_HOME

    typo = make_inventory(java_packagez=["openjdk-17-jdk"])
    with pytest.raises(RoleVarsError, match="java_packagez"):
        resolve_vars(JavaVars, "java_", typo)


def test_model_is_frozen_and_hashable():
    hv = JavaVars(packages=("openjdk-21-jdk",), home=JAVA_HOME)
    assert hash(hv) is not None
    with pytest.raises(ValueError):  # pydantic frozen-instance error
        hv.home = "/other"
