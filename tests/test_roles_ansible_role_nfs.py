# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end test for the NFS Ansible-role migration example.

``examples/roles/ansible-role-nfs/ansible-role-nfs.py`` connects for real
when run as a script, so the suite never executes its ``main()``. What it
*can* do offline is replay the same orchestration against ``SimulatedHost``:
the example is imported as a module, its deploy factory is fed the plan
snapshot built from static inventory data, and the deploy goes through the
real driver (prepare + apply, plus a ``dry=True`` preview).

The load-bearing assertions are the migration's idempotence claim and its
command-handler mapping: run the whole plan -> deploy -> handler sequence,
then run it *again* against the same persistent hosts, and the second pass
must report ``changed=False`` everywhere and queue no handler. The role's
``reload nfs`` handler is ``command: exportfs -ra`` -- not a systemd action
-- so it rides the first-class action seam (#16): ``seed()`` plants the
canned ``sudo exportfs -ra`` record in ``host.shell_results`` (the exact
sudo rendering is the key) and ``ran_shells`` proves it ran exactly once,
on the notified host only. On the default path (``nfs_exports: []``) the
service is never started, yet the handler still runs -- the case a
``reload_handler`` would get wrong: it starts the stopped unit instead of
reloading it, so ``exportfs -ra`` never runs.

**No between-pass bridge is needed**: the simulation's own mutators
maintain every seam the second pass reads -- ``install_apt_packages``
records the packages, the ``AptUpdateCommand`` arm refreshes
``apt_update_stamp`` to wall-clock now (so the folded cache refresh,
deviation D1, skips on the second apply), ``make_directory`` the export
directory rows, ``install_file`` the exports content and its stat row, and
the systemctl arms the service state.
"""

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from reemote import CommandError, changed_hosts, run
from tests.simulated_host import SimulatedHost

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = (
    REPO_ROOT / "examples" / "roles" / "ansible-role-nfs" / "ansible-role-nfs.py"
)


def load_example() -> Any:
    """Import the migration example by path (its name is not a valid identifier)."""
    spec = importlib.util.spec_from_file_location("ansible_role_nfs_example", EXAMPLE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


nfs = load_example()

#: The template's static header, spelled out independently of the example's
#: constant so the render assertion compares against the role's source.
EXPECTED_HEADER = (
    "# /etc/exports: the access control list for filesystems which may be exported\n"
    "#   to NFS clients.  See exports(5).\n"
    "#\n"
    "# Example for NFSv2 and NFSv3:\n"
    "# /srv/homes       hostname1(rw,sync,no_subtree_check) "
    "hostname2(ro,sync,no_subtree_check)\n"
    "#\n"
    "# Example for NFSv4:\n"
    "# /srv/nfs4        gss/krb5i(rw,sync,fsid=0,crossmnt,no_subtree_check)\n"
    "# /srv/nfs4/homes  gss/krb5i(rw,sync,no_subtree_check)\n"
    "#\n"
)

#: The exact shell the sudo=True action renders (no privilege section in the
#: inventory, so a bare ``sudo`` prefix) -- the shell_results seam key.
HANDLER_SHELL = "sudo exportfs -ra"

EXPORTS = [
    "/srv/nfs 192.0.2.0/24(rw,sync,no_subtree_check)",
    "/srv/nfs/projects 192.0.2.5(rw,sync,no_subtree_check)",
    "/srv/nfs 192.0.2.6(ro,sync,no_subtree_check)",
]


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variable rides as an extra key -- reemote's ``host_vars`` -- so
    ``role_settings`` reads it straight off ``host.extra``. With no overrides
    the role default applies (``nfs_exports: []``): the exports file is still
    written (header only) and the service task is skipped.
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
    """Pre-state: a stock minimal Ubuntu machine with no NFS packages.

    The apt cache has never been refreshed (the folded update runs), no
    exports file exists yet, and the canned ``exportfs -ra`` answer rides
    the action seam (#16) keyed by its exact sudo rendering.
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
    host.shell_results[HANDLER_SHELL] = {
        "exit_status": 0,
        "stdout": "",
        "stderr": "",
    }


def make_hosts(inventory: list[dict[str, Any]]) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if nfs.is_ubuntu(host):
            seed(host)
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

    The sequence mirrors ``main()`` barrier for barrier -- the frozen
    per-host plan, the single factory-built deploy, the ``changed_hosts``
    notification read, the ``exportfs -ra`` action gather -- but reads the
    simulated hosts instead of opening SSH connections.
    """
    targets = [host for host in hosts.values() if nfs.is_ubuntu(host)]
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

    plans = {host.name: nfs.nfs_plan(host) for host in targets}
    report = await execute(nfs.build_nfs(plans))

    # The handler: the notification read, then the command gather on the
    # notified hosts only (gather_action closes its own hosts).
    notified = changed_hosts(report, nfs.NOTIFYING)
    await nfs.reload_nfs(targets, notified)

    return {"reports": reports, "plans": plans, "notified": notified}


def changed_entries(result: dict[str, Any]) -> list[Any]:
    """Every changed log entry across the whole sequence."""
    return [entry for report in result["reports"] for entry in report.log if entry.changed]


# --- the first pass really deploys the role ----------------------------------


async def test_first_pass_default_path_installs_and_writes_exports(rig):
    inventory, hosts = rig
    result = await migrate(inventory, hosts)
    host = hosts["ubuntu"]

    # The Debian packages are installed and the folded cache refresh ran.
    assert "nfs-common" in host.apt_packages
    assert "nfs-kernel-server" in host.apt_packages
    assert host.apt_update_stamp is not None

    # The exports file: header only (nfs_exports defaults to []), root:root
    # 0644 -- the template task's owner/group/mode.
    content = host.remote_files[nfs.EXPORTS_PATH]
    assert content == EXPECTED_HEADER.encode()
    assert host.file_stats[nfs.EXPORTS_PATH] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(content),
    }

    # No export directories and no service start on the default path: the
    # `when: nfs_exports|length > 0` gate skipped the service task.
    assert result["plans"]["ubuntu"]["directories"] == ()
    assert result["plans"]["ubuntu"]["start_service"] is False
    assert host.services == {}
    assert host.enabled_services == set()

    # The template task changed, so the command handler fired -- even though
    # the service was never started. This is the case reload_handler would
    # get wrong: systemd.service reloaded=True acts only on a running unit,
    # so it would start the stopped unit (default running=True) and never
    # run exportfs; the Shell gather reproduces Ansible's `command: exportfs -ra`.
    assert result["notified"] == frozenset({"ubuntu"})
    assert host.ran_shells == [HANDLER_SHELL]


async def test_non_ubuntu_host_is_never_touched(rig):
    inventory, hosts = rig
    result = await migrate(inventory, hosts)

    skipped = hosts["web1"]
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    assert skipped.directory_stats == {}
    assert skipped.services == {}
    assert skipped.ran_shells == []
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

    # No bridge is needed: every seam the second pass reads is maintained
    # by the simulation's own mutators (see the module docstring).
    second = await migrate(inventory, hosts)

    assert changed_entries(second) == []
    assert second["notified"] == frozenset()
    # The handler ran exactly once across both passes: a converged re-run
    # freezes an empty notification set and gathers nothing.
    assert hosts["ubuntu"].ran_shells == [HANDLER_SHELL]


async def test_dry_preview_mutates_nothing(rig):
    """``dry=True`` stops after prepare, so it must not touch the target."""
    inventory, hosts = rig
    host = hosts["ubuntu"]

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    plans = {host.name: nfs.nfs_plan(host)}
    await run(inventory, nfs.build_nfs(plans), host_factory=factory, dry=True)

    assert host.remote_files == {}
    assert host.existing_files == set()
    assert host.file_stats == {}
    assert host.directory_stats == {}
    assert host.apt_packages == {}
    assert host.apt_update_stamp is None
    assert host.services == {}
    assert host.ran_shells == []


# --- the configured-exports branch --------------------------------------------


async def test_exports_create_directories_start_service_and_notify():
    """Non-empty nfs_exports: the directory loop, the render, the service gate."""
    inventory = make_inventory(nfs_exports=EXPORTS)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    # An existing export directory with a non-default mode: the role's
    # mode-less `file: state=directory` task must leave it untouched.
    host.directory_stats["/srv/nfs"] = {"mode": 700, "user": "root", "group": "root"}

    result = await migrate(inventory, hosts)

    # map('split') | map('first') | unique: deduplicated, first-occurrence
    # order -- /srv/nfs appears twice in the exports but once in the loop.
    assert result["plans"]["ubuntu"]["directories"] == ("/srv/nfs", "/srv/nfs/projects")
    assert host.directory_stats["/srv/nfs"]["mode"] == 700  # untouched
    assert host.directory_stats["/srv/nfs/projects"]["mode"] == 755  # created

    # The rendered exports file: the header plus one line per export.
    content = host.remote_files[nfs.EXPORTS_PATH].decode()
    assert content == EXPECTED_HEADER + "".join(f"{line}\n" for line in EXPORTS)

    # The service task ran: started and enabled.
    assert host.services["nfs-kernel-server.service"] == "active"
    assert "nfs-kernel-server.service" in host.enabled_services

    # The handler fired once for the changed exports file.
    assert result["notified"] == frozenset({"ubuntu"})
    assert host.ran_shells == [HANDLER_SHELL]

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []
    assert second["notified"] == frozenset()
    assert host.ran_shells == [HANDLER_SHELL]


async def test_metadata_drift_converges_and_notifies(rig):
    """A mode/owner drift re-runs the metadata companion, not the install.

    files.install is content-idempotent, so the paired files.file converges
    the template task's mode/owner enforcement -- and its change queues the
    handler, exactly as Ansible's template task would.
    """
    inventory, hosts = rig
    host = hosts["ubuntu"]
    data = EXPECTED_HEADER.encode()
    host.remote_files[nfs.EXPORTS_PATH] = data
    host.existing_files.add(nfs.EXPORTS_PATH)
    host.file_stats[nfs.EXPORTS_PATH] = {
        "user": "supervisor",
        "group": "supervisor",
        "mode": 600,
        "size": len(data),
    }

    result = await migrate(inventory, hosts)

    entries = {entry.name: entry for entry in result["reports"][0].log}
    assert not entries["Copy exports file"].changed  # content already matches
    assert entries["Set exports file mode and owner"].changed
    assert host.file_stats[nfs.EXPORTS_PATH] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(data),
    }
    assert result["notified"] == frozenset({"ubuntu"})
    assert host.ran_shells == [HANDLER_SHELL]

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []
    assert second["notified"] == frozenset()


async def test_handler_failure_raises_command_error(rig):
    """A non-zero exportfs exit is data in the Result, re-raised typed."""
    inventory, hosts = rig
    hosts["ubuntu"].shell_results[HANDLER_SHELL] = {
        "exit_status": 1,
        "stdout": "",
        "stderr": "exportfs: /etc/exports:1: syntax error\n",
    }

    with pytest.raises(CommandError, match="exportfs -ra"):
        await migrate(inventory, hosts)


# --- the pure helpers, unit-tested directly -----------------------------------


def test_render_exports_matches_the_template():
    """The join render is the template source with the loop filled in."""
    assert nfs.render_exports(()) == EXPECTED_HEADER
    assert nfs.render_exports(tuple(EXPORTS)) == (
        EXPECTED_HEADER + "".join(f"{line}\n" for line in EXPORTS)
    )
    # Every export line is newline-terminated; the header ends with "#\n".
    assert nfs.render_exports(("/a x",)).endswith("/a x\n")


def test_export_directories_splits_and_deduplicates():
    assert nfs.export_directories(()) == ()
    assert nfs.export_directories(tuple(EXPORTS)) == ("/srv/nfs", "/srv/nfs/projects")
    # Whitespace runs split like Jinja's map('split'); order is first occurrence.
    assert nfs.export_directories(("  /b\tx", "/a y", "/b z")) == ("/b", "/a")


def test_role_settings_defaults_and_overrides():
    host = SimulatedHost.from_inventory(make_inventory()[0])
    assert nfs.role_settings(host) == {"nfs_exports": ()}

    overridden = SimulatedHost.from_inventory(make_inventory(nfs_exports=EXPORTS)[0])
    assert nfs.role_settings(overridden) == {"nfs_exports": tuple(EXPORTS)}


def test_nfs_plan_freezes_the_service_gate():
    host = SimulatedHost.from_inventory(make_inventory()[0])
    plan = nfs.nfs_plan(host)
    assert plan["start_service"] is False
    assert plan["exports_content"] == EXPECTED_HEADER

    configured = SimulatedHost.from_inventory(make_inventory(nfs_exports=EXPORTS)[0])
    plan = nfs.nfs_plan(configured)
    assert plan["start_service"] is True
    assert plan["directories"] == ("/srv/nfs", "/srv/nfs/projects")


@pytest.mark.parametrize(
    ("overrides", "error", "match"),
    [
        ({"nfs_exports": "/srv/nfs x"}, TypeError, "nfs_exports"),
        ({"nfs_exports": [42]}, ValueError, "nfs_exports"),
        ({"nfs_exports": [""]}, ValueError, "nfs_exports"),
        ({"nfs_exports": ["   "]}, ValueError, "nfs_exports"),
    ],
)
def test_invalid_role_values_fail_fast(overrides, error, match):
    host = SimulatedHost.from_inventory(make_inventory(**overrides)[0])
    with pytest.raises(error, match=match):
        nfs.role_settings(host)