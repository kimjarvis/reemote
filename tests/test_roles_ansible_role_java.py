# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end test for the Java Ansible-role migration example.

``examples/roles/ansible-role-java/ansible-role-java.py`` connects for real
when run as a script, so the suite never executes its ``main()``. What it
*can* do offline is replay the same orchestration against ``SimulatedHost``:
the example is imported as a module, its deploy factory is fed the plan
snapshot gathered from simulated state, and the deploy goes through the
real driver (prepare + apply, plus a ``dry=True`` preview).

The load-bearing assertion is the migration's idempotence claim: run the
whole gather -> plan -> deploy sequence, then run it *again* against the
same persistent hosts, and the second pass must report ``changed=False``
everywhere. The role has no handlers, so there is no notification set to
check.

Unlike the Docker migration, **no between-pass bridge is needed**: the
simulation's own mutators maintain every seam the second pass reads --
``install_apt_packages`` records the package, ``make_directory`` /
``chmod_path`` the man1 row, ``install_file`` the profile content and its
stat row, and the ``AptUpdateCommand`` arm refreshes ``apt_update_stamp``
to wall-clock now, so the folded cache refresh (deviation D1) skips on the
second apply.
"""

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from reemote import LinuxDistribution, gather_fact, run
from tests.simulated_host import SimulatedHost

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = (
    REPO_ROOT / "examples" / "roles" / "ansible-role-java" / "ansible-role-java.py"
)


def load_example() -> Any:
    """Import the migration example by path (its name is not a valid identifier)."""
    spec = importlib.util.spec_from_file_location("ansible_role_java_example", EXAMPLE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


java = load_example()

JAVA_HOME = "/usr/lib/jvm/java-21-openjdk-amd64"
PROFILE_CONTENT = f"export JAVA_HOME={JAVA_HOME}\n".encode()


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride as extra keys -- reemote's ``host_vars`` -- so
    ``role_settings`` reads them straight off ``host.extra``. The default
    overrides exercise the template branch (``java_home`` set); the package
    list stays on the vars-table default (Ubuntu 24 -> openjdk-21-jdk).
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


def seed(host: SimulatedHost, major: str = "24") -> None:
    """Pre-state: a stock minimal Ubuntu machine with no Java installed.

    The man1 directory is absent (the creation branch of the workaround),
    the apt cache has never been refreshed (the folded update runs), and
    no profile script exists yet.
    """
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
    host.linux_distribution = {
        "name": "Ubuntu",
        "family": "Debian",
        "major": major,
        "minor": "04",
        "release_meta": {
            "ID": "ubuntu",
            "VERSION_ID": f"{major}.04",
            "VERSION_CODENAME": "noble",
        },
    }


def make_hosts(
    inventory: list[dict[str, Any]], major: str = "24"
) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if java.is_ubuntu(host):
            seed(host, major)
        hosts[host.name] = host
    return hosts


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

    The sequence mirrors ``main()`` barrier for barrier -- the variable
    gates, the ``LinuxDistribution`` gather, the frozen per-host plan, the
    single factory-built deploy -- but reads the simulated hosts instead
    of opening SSH connections.
    """
    targets = [host for host in hosts.values() if java.is_ubuntu(host)]
    settings = {host.name: java.role_settings(host) for host in targets}
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

    # The include_vars dispatch (gather -> snapshot -> factory).
    # gather_fact does not close its hosts; the finally does.
    try:
        distributions = await gather_fact(targets, LinuxDistribution)
    finally:
        for host in hosts.values():
            await host.aclose()
    plans = {
        host.name: java.java_plan(host, distributions[host.name]) for host in targets
    }

    await execute(java.build_java(plans))

    return {"reports": reports, "settings": settings, "plans": plans}


def changed_entries(result: dict[str, Any]) -> list[Any]:
    """Every changed log entry across the whole sequence."""
    return [entry for report in result["reports"] for entry in report.log if entry.changed]


# --- the first pass really deploys the role ----------------------------------


async def test_first_pass_installs_java_and_configures_profile(rig):
    inventory, hosts = rig
    result = await migrate(inventory, hosts)
    host = hosts["ubuntu"]

    # The vars table selected Ubuntu 24's package list.
    assert result["plans"]["ubuntu"]["packages"] == ("openjdk-21-jdk",)
    assert result["plans"]["ubuntu"]["man_dir"] is True
    assert "openjdk-21-jdk" in host.apt_packages

    # The man1 workaround directory exists with the task's mode.
    assert host.directory_stats[java.MAN_DIR]["mode"] == 755

    # The folded cache refresh ran (the stamp was absent).
    assert host.apt_update_stamp is not None

    # The JAVA_HOME profile: the f-string render, root-owned 0644.
    assert host.remote_files[java.JAVA_HOME_PROFILE] == PROFILE_CONTENT
    assert host.file_stats[java.JAVA_HOME_PROFILE] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(PROFILE_CONTENT),
    }

    # The install request really changed something.
    assert any(
        entry.name == "Ensure Java is installed" and entry.changed
        for entry in result["reports"][0].log
    )

    # The whole migration is operations: no shell action ever ran.
    assert host.ran_shells == []


async def test_non_ubuntu_host_is_never_touched(rig):
    inventory, hosts = rig
    result = await migrate(inventory, hosts)

    skipped = hosts["web1"]
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    assert skipped.directory_stats == {}
    assert skipped.ran_shells == []
    # The factory-built deploy yields no request at all for a host without
    # a plan; any entry that did reach the log must be a skipped one.
    for report in result["reports"]:
        for entry in report.log:
            if entry.host == "web1":
                assert entry.skipped
                assert not entry.changed


# --- the second pass is a no-op ----------------------------------------------


async def test_second_pass_changes_nothing(rig):
    inventory, hosts = rig
    first = await migrate(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    # No bridge is needed: every seam the second pass reads is maintained
    # by the simulation's own mutators (see the module docstring).
    second = await migrate(inventory, hosts)

    assert changed_entries(second) == []
    assert hosts["ubuntu"].ran_shells == []


async def test_dry_preview_mutates_nothing(rig):
    """``dry=True`` stops after prepare, so it must not touch the target."""
    inventory, hosts = rig
    host = hosts["ubuntu"]

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    # The plan the LinuxDistribution seam answers, frozen as main() does.
    deploy = java.build_java(
        {
            "ubuntu": {
                "packages": ("openjdk-21-jdk",),
                "man_dir": True,
                "java_home_content": PROFILE_CONTENT.decode(),
            }
        }
    )
    await run(inventory, deploy, host_factory=factory, dry=True)

    assert host.remote_files == {}
    assert host.existing_files == set()
    assert host.file_stats == {}
    assert host.directory_stats == {}
    assert host.apt_packages == {}
    assert host.apt_update_stamp is None


# --- the override and gate branches -------------------------------------------


async def test_java_packages_override_wins_and_empty_java_home_skips_template():
    """An explicit java_packages list beats the vars table; java_home='' skips."""
    inventory = make_inventory(java_packages=["openjdk-17-jdk"], java_home="")
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    result = await migrate(inventory, hosts)

    assert result["plans"]["ubuntu"]["packages"] == ("openjdk-17-jdk",)
    assert result["plans"]["ubuntu"]["java_home_content"] is None
    assert "openjdk-17-jdk" in host.apt_packages
    assert "openjdk-21-jdk" not in host.apt_packages
    # The template task's `when: java_home != ""` skipped the profile.
    assert java.JAVA_HOME_PROFILE not in host.remote_files
    assert java.JAVA_HOME_PROFILE not in host.existing_files

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


async def test_empty_java_packages_installs_nothing():
    """java_packages=[] is a valid Ansible override: apt installs nothing."""
    inventory = make_inventory(java_packages=[], java_home="")
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    result = await migrate(inventory, hosts)

    assert result["plans"]["ubuntu"]["packages"] == ()
    assert host.apt_packages == {}
    # The folded cache refresh still runs (the molecule pre_task is
    # unconditional), and the second pass skips it on the fresh stamp.
    assert host.apt_update_stamp is not None
    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


async def test_ubuntu_16_skips_the_man_directory():
    """The man1 workaround is gated on Ubuntu >= 18; 16 gets openjdk-8-jdk."""
    inventory = make_inventory(java_home="")
    hosts = make_hosts(inventory, major="16")
    host = hosts["ubuntu"]

    result = await migrate(inventory, hosts)

    assert result["plans"]["ubuntu"]["packages"] == ("openjdk-8-jdk",)
    assert result["plans"]["ubuntu"]["man_dir"] is False
    assert java.MAN_DIR not in host.directory_stats
    assert "openjdk-8-jdk" in host.apt_packages

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


# --- the pure helpers, unit-tested directly -----------------------------------


def test_render_java_home_matches_the_template():
    """The f-string render is the template source with the variable filled."""
    assert java.render_java_home(JAVA_HOME) == PROFILE_CONTENT.decode()
    assert java.render_java_home("/x").endswith("\n")


def test_role_settings_defaults_and_overrides():
    host = SimulatedHost.from_inventory(make_inventory()[0])
    settings = java.role_settings(host)
    assert settings["java_packages"] is None
    assert settings["java_home"] == JAVA_HOME

    bare = SimulatedHost.from_inventory(
        {"host": "bare", "family": "Ubuntu", "connection": {"address": "192.0.2.12"}, "group": "ubuntu"}
    )
    defaults = java.role_settings(bare)
    assert defaults["java_packages"] is None
    assert defaults["java_home"] == ""

    overridden = SimulatedHost.from_inventory(
        make_inventory(java_packages=["openjdk-17-jdk", "openjdk-21-jdk"])[0]
    )
    assert java.role_settings(overridden)["java_packages"] == (
        "openjdk-17-jdk",
        "openjdk-21-jdk",
    )


def test_ubuntu_major_normalizes_and_gates():
    assert java.ubuntu_major({"name": "Ubuntu", "major": "24"}) == 24
    assert java.ubuntu_major({"name": "Ubuntu", "major": 24}) == 24
    with pytest.raises(ValueError, match="targets Ubuntu"):
        java.ubuntu_major({"name": "Debian", "major": "12"})
    with pytest.raises(ValueError, match="major version"):
        java.ubuntu_major({"name": "Ubuntu"})
    with pytest.raises(ValueError, match="major version"):
        java.ubuntu_major({"name": "Ubuntu", "major": "noble"})


def test_java_plan_selects_the_vars_table_and_gates():
    host = SimulatedHost.from_inventory(make_inventory()[0])
    plan = java.java_plan(host, {"name": "Ubuntu", "major": "24"})
    assert plan == {
        "packages": ("openjdk-21-jdk",),
        "man_dir": True,
        "java_home_content": PROFILE_CONTENT.decode(),
    }

    # The >= 18 man-directory boundary, both sides.
    for major, expected in (("16", False), ("18", True)):
        plan = java.java_plan(host, {"name": "Ubuntu", "major": major})
        assert plan["man_dir"] is expected

    # An unsupported major raises as the role's include_vars would -- even
    # when java_packages is overridden (include_vars runs unconditionally).
    with pytest.raises(ValueError, match="vars/Ubuntu-25.yml"):
        java.java_plan(host, {"name": "Ubuntu", "major": "25"})
    override = SimulatedHost.from_inventory(
        make_inventory(java_packages=["openjdk-21-jdk"])[0]
    )
    with pytest.raises(ValueError, match="vars/Ubuntu-25.yml"):
        java.java_plan(override, {"name": "Ubuntu", "major": "25"})

    # The override wins on a supported release; empty java_home skips.
    plan = java.java_plan(override, {"name": "Ubuntu", "major": "22"})
    assert plan["packages"] == ("openjdk-21-jdk",)
    bare = SimulatedHost.from_inventory(
        {"host": "bare", "family": "Ubuntu", "connection": {"address": "192.0.2.12"}, "group": "ubuntu"}
    )
    plan = java.java_plan(bare, {"name": "Ubuntu", "major": "24"})
    assert plan["java_home_content"] is None


@pytest.mark.parametrize(
    ("overrides", "error", "match"),
    [
        ({"java_packages": "openjdk-21-jdk"}, TypeError, "java_packages"),
        ({"java_packages": [42]}, ValueError, "java_packages"),
        ({"java_packages": [""]}, ValueError, "java_packages"),
        ({"java_home": 42}, TypeError, "java_home"),
    ],
)
def test_invalid_role_values_fail_fast(overrides, error, match):
    host = SimulatedHost.from_inventory(make_inventory(**overrides)[0])
    with pytest.raises(error, match=match):
        java.role_settings(host)