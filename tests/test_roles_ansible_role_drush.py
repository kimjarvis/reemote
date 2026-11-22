# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end test for the Drush Ansible-role migration example.

``examples/roles/ansible-role-drush/ansible-role-drush.py`` connects for real
when run as a script, so the suite never executes its ``main()``. What it *can*
do offline is replay the same orchestration against ``SimulatedHost``: the
example is imported as a module, its factory is fed the frozen launcher plans
gathered from simulated state, and the deploy goes through the real driver
(prepare + apply, plus a ``dry=True`` preview).

The load-bearing assertion is the migration's idempotence claim: run the
gather -> plan -> deploy sequence, then run it *again* against the same
persistent hosts, and the second pass must report ``changed=False`` everywhere
-- the directory matches, the phar's sha1 matches (issue #23's binary
read-back), the mode matches.

No between-pass bridge is needed: the ``install_file`` seam is bytes-generic
(it stores the spooled bytes at the destination and writes the ``file_stats``
row the ``File`` fact reads), so pass 2 observes pass 1 through the simulation's
own mutators. The control-side phar fetch is answered by monkeypatching
``files._fetch_url_bytes`` -- the same seam the library's own drush acceptance
test uses (``tests/test_files_download.py``). Unlike ``files.unarchive``'s
``creates`` gate, ``files.download`` fetches on *every* pass (it must read the
body to compare sha1), so the no-op claim is about changed entries, not about
fetch counts.

Seeding choices: ``/usr/local/bin`` exists on a stock Ubuntu host (the
directory task is a no-op there; a dedicated test removes it to exercise the
create branch), and the symlink pre-state seeds ``links``/``link_stats`` plus
a ``False`` ``File``-fact row -- the fact reads a symlink as a non-file, which
is what makes ``files.file(present=False, force=True)`` remove the link itself.
"""

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from reemote import File, FileLink, gather_fact, run
from reemote.modules.files import files as files_module
from reemote.modules.files.files import _install_spool_path
from tests.simulated_host import SimulatedHost

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = (
    REPO_ROOT / "examples" / "roles" / "ansible-role-drush" / "ansible-role-drush.py"
)


def load_example() -> Any:
    """Import the migration example by path (its name is not a valid identifier)."""
    spec = importlib.util.spec_from_file_location(
        "ansible_role_drush_example", EXAMPLE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


drush = load_example()

VERSION = "0.10.1"
URL = f"https://github.com/drush-ops/drush-launcher/releases/download/{VERSION}/drush.phar"
DEST = "/usr/local/bin/drush"
DEST_DIR = "/usr/local/bin"
SPOOL = _install_spool_path(DEST)

# A binary phar body (never valid UTF-8): the launcher is a PHP archive with
# binary sections, opaque to the simulation. The non-UTF-8 bytes are what
# drive files.download down the issue-#23 binary install branch.
FAKE_PHAR = b"#!/usr/bin/env php\n<?php /* \xff\xfe\x00drush-launcher-phar */"


@pytest.fixture
def canned_fetch(monkeypatch):
    """Replace the control-side phar fetch with a canned-bytes stub.

    ``files.download`` looks ``_fetch_url_bytes`` up as a module global at call
    time, so patching the module attribute redirects the fetch. Returns the list
    of URLs the stub was called with, so a test can assert the fetch happened
    (and that a disabled launcher never fetches at all).
    """
    fetched: list[str] = []

    def fake_fetch(url: str) -> bytes:
        fetched.append(url)
        return FAKE_PHAR

    monkeypatch.setattr(files_module, "_fetch_url_bytes", fake_fetch)
    return fetched


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride as extra keys -- reemote's ``host_vars`` -- so
    ``role_settings`` reads them straight off ``host.extra``. With no overrides
    the role defaults apply (launcher install enabled, version 0.10.1).
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


def seed(host: SimulatedHost, *, with_dest_dir: bool = True) -> None:
    """Pre-state: a stock Ubuntu machine, no drush yet.

    ``/usr/local/bin`` exists on a stock host (the directory task no-ops);
    ``with_dest_dir=False`` removes it to exercise the create branch.
    """
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.links = {}
    host.link_stats = {}
    host.directory_stats = {}
    if with_dest_dir:
        host.directory_stats[DEST_DIR] = {"mode": 755, "user": "root", "group": "root"}
    host.existing_files |= set(host.directory_stats)


def _symlink(host: SimulatedHost, path: str, target: str) -> None:
    """Seed a symlink: ``links``/``link_stats`` plus a ``False`` File-fact row.

    The ``File`` fact reads ``file_stats`` only, so ``False`` is what makes
    ``files.file(present=False, force=True)`` take the remove-the-link branch.
    """
    host.links[path] = target
    host.link_stats[path] = {
        "mode": 777,
        "user": "root",
        "group": "root",
        "link_target": target,
    }
    host.file_stats[path] = False


def _regular_file(host: SimulatedHost, path: str, data: bytes, mode: int) -> None:
    """Seed an existing regular file: content, existence and stat metadata."""
    host.remote_files[path] = data
    host.existing_files.add(path)
    host.file_stats[path] = {
        "user": "root",
        "group": "root",
        "mode": mode,
        "size": len(data),
    }


def make_hosts(
    inventory: list[dict[str, Any]], *, with_dest_dir: bool = True
) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if drush.is_ubuntu(host):
            seed(host, with_dest_dir=with_dest_dir)
        hosts[host.name] = host
    return hosts


async def migrate(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
    *,
    dry: bool = True,
    apply: bool = True,
) -> dict[str, Any]:
    """Replay the example's whole orchestration once, against persistent hosts.

    The sequence mirrors ``main()`` barrier for barrier -- the variable
    resolution, the ``FileLink``/``File`` stat reads frozen into the plans, the
    one deploy -- but reads the simulated hosts instead of opening SSH
    connections. ``apply=False`` stops after the dry preview (report is None).
    """
    targets = [host for host in hosts.values() if drush.is_ubuntu(host)]
    resolved = {
        host.name: drush.resolved_vars(drush.role_settings(host)) for host in targets
    }
    active = [host for host in targets if resolved[host.name]["launcher_install"]]

    plans: dict[str, Any] = {}
    try:
        for host in active:
            rv = resolved[host.name]
            link = (await gather_fact([host], FileLink, rv["path"]))[host.name]
            info = (await gather_fact([host], File, rv["path"]))[host.name]
            plans[host.name] = {
                **rv,
                "is_symlink": link is not None,
                "dest_exists": isinstance(info, dict),
            }
    finally:
        for host in active:
            await host.aclose()

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        # The *same* host object every run, so state survives the barriers.
        return hosts[entry["host"]]

    deploy = drush.build_launcher(plans)
    if dry:
        await run(inventory, deploy, host_factory=factory, dry=True)
    if not apply:
        return {"report": None, "plans": plans, "resolved": resolved}
    _plans, report, _hosts = await run(inventory, deploy, host_factory=factory)
    assert report is not None
    return {"report": report, "plans": plans, "resolved": resolved}


def changed_entries(result: dict[str, Any]) -> list[Any]:
    """Every changed log entry of the one deploy."""
    return [entry for entry in result["report"].log if entry.changed]


# --- the first pass really installs the launcher ------------------------------


async def test_first_pass_installs_the_launcher(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    result = await migrate(inventory, hosts)
    host = hosts["ubuntu"]

    # The plan froze the stat reads: no symlink, no regular file yet.
    assert result["plans"]["ubuntu"]["is_symlink"] is False
    assert result["plans"]["ubuntu"]["dest_exists"] is False

    # The phar was fetched control-side and installed binary (issue #23):
    # root:root 0755 at the destination, the /tmp spool cleaned up.
    assert URL in canned_fetch
    assert host.remote_files[DEST] == FAKE_PHAR
    assert host.file_stats[DEST]["mode"] == 755
    assert host.file_stats[DEST]["user"] == "root"
    assert host.file_stats[DEST]["group"] == "root"
    assert SPOOL not in host.remote_files

    # The directory task was a no-op on the stock host; the executable-bit
    # task was not even requested (the destination did not exist pre-deploy).
    assert host.directory_stats[DEST_DIR]["mode"] == 755
    names = {entry.name for entry in result["report"].log if entry.host == "ubuntu"}
    assert "Install Drush." in names
    assert "Ensure Drush is executable." not in names
    assert "Perform cleanup of old symlink." not in names
    assert changed_entries(result)


async def test_non_ubuntu_host_is_never_touched(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    result = await migrate(inventory, hosts)

    skipped = hosts["web1"]
    assert skipped.remote_files == {}
    assert skipped.directory_stats == {}
    assert skipped.links == {}
    for entry in result["report"].log:
        if entry.host == "web1":
            assert entry.skipped
            assert not entry.changed


# --- the second pass is a no-op -----------------------------------------------


async def test_second_pass_changes_nothing(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await migrate(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    # No bridge: install_file stored the bytes and the stat row, so pass 2's
    # FileLink/File reads and the download's sha1 read-back all converge.
    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []
    assert second["plans"]["ubuntu"]["dest_exists"] is True
    install = next(
        entry
        for entry in second["report"].log
        if entry.name == "Install Drush." and entry.host == "ubuntu"
    )
    assert install.reason == f"content already up to date ({DEST})"


async def test_dry_preview_mutates_nothing(canned_fetch):
    """``dry=True`` stops after prepare, so it must not touch the target.

    The prepare phase does fetch the phar control-side (it must read the body
    to plan the sha1 comparison), but no host store may change.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    before_dirs = dict(host.directory_stats)

    result = await migrate(inventory, hosts, apply=False)
    assert result["report"] is None
    assert result["plans"]["ubuntu"]["dest_exists"] is False
    assert canned_fetch  # the control-side fetch happened during prepare
    assert host.remote_files == {}
    assert host.existing_files == set(before_dirs)
    assert host.file_stats == {}
    assert host.directory_stats == before_dirs


# --- the symlink cleanup branch ------------------------------------------------


async def test_symlink_pre_state_is_removed_then_installed(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    _symlink(host, DEST, "/opt/drush/drush")

    result = await migrate(inventory, hosts)
    assert result["plans"]["ubuntu"]["is_symlink"] is True
    assert result["plans"]["ubuntu"]["dest_exists"] is False

    # The link itself is gone (force=True removes the link, not the target),
    # and the launcher phar sits at the path as a regular file.
    assert DEST not in host.links
    assert host.remote_files[DEST] == FAKE_PHAR
    assert host.file_stats[DEST]["mode"] == 755
    cleanup = next(
        entry
        for entry in result["report"].log
        if entry.name == "Perform cleanup of old symlink." and entry.host == "ubuntu"
    )
    assert cleanup.changed

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


# --- drift convergence ----------------------------------------------------------


async def test_content_drift_reinstalls_once(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    _regular_file(host, DEST, b"stale launcher bytes", 755)

    result = await migrate(inventory, hosts)
    assert result["plans"]["ubuntu"]["dest_exists"] is True
    # sha1-idempotent, not exists-idempotent: the drifted binary converges.
    assert host.remote_files[DEST] == FAKE_PHAR
    install = next(
        entry
        for entry in result["report"].log
        if entry.name == "Install Drush." and entry.host == "ubuntu"
    )
    assert install.changed

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


async def test_mode_drift_converges_via_the_executable_task(canned_fetch):
    """Content equal but mode drifted: the download no-ops, files.file chmods.

    Install/download idempotence is content-only -- the role's final
    "Ensure Drush is executable." task is what converges the metadata.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    _regular_file(host, DEST, FAKE_PHAR, 644)

    result = await migrate(inventory, hosts)
    assert host.file_stats[DEST]["mode"] == 755
    by_name = {
        entry.name: entry
        for entry in result["report"].log
        if entry.host == "ubuntu"
    }
    assert not by_name["Install Drush."].changed
    assert by_name["Ensure Drush is executable."].changed

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


# --- the directory create branch -------------------------------------------------


async def test_missing_directory_is_created_0755(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory, with_dest_dir=False)
    host = hosts["ubuntu"]

    result = await migrate(inventory, hosts)
    assert DEST_DIR in host.directory_stats
    assert host.directory_stats[DEST_DIR]["mode"] == 755
    assert host.remote_files[DEST] == FAKE_PHAR
    directory = next(
        entry
        for entry in result["report"].log
        if entry.name == "Ensure Drush path directory exists."
        and entry.host == "ubuntu"
    )
    assert directory.changed

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


# --- the launcher_install gate and custom paths -----------------------------------


async def test_launcher_install_false_deploys_nothing(canned_fetch):
    inventory = make_inventory(drush_launcher_install=False)
    hosts = make_hosts(inventory)
    result = await migrate(inventory, hosts)

    assert result["plans"] == {}
    assert canned_fetch == []
    assert result["report"].log == []
    assert hosts["ubuntu"].remote_files == {}


async def test_custom_path_and_version(canned_fetch):
    inventory = make_inventory(
        drush_launcher_version="0.10.0", drush_launcher_path="/opt/bin/drush"
    )
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    result = await migrate(inventory, hosts)

    plan = result["plans"]["ubuntu"]
    assert plan["path"] == "/opt/bin/drush"
    assert plan["path_dir"] == "/opt/bin"
    assert plan["phar_url"].endswith("/0.10.0/drush.phar")
    assert "/opt/bin/drush" in host.remote_files
    assert host.directory_stats["/opt/bin"]["mode"] == 755
    assert DEST not in host.remote_files

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


# --- the pure helpers, unit-tested directly ----------------------------------------


def test_resolved_vars_defaults():
    settings = drush.role_settings(SimulatedHost.from_inventory(make_inventory()[0]))
    rv = drush.resolved_vars(settings)
    assert rv["launcher_install"] is True
    assert rv["version"] == VERSION
    assert rv["phar_url"] == URL
    assert rv["path"] == DEST
    assert rv["path_dir"] == DEST_DIR


def test_resolved_vars_version_flows_into_the_url():
    entry = make_inventory(drush_launcher_version="0.9.0")[0]
    rv = drush.resolved_vars(drush.role_settings(SimulatedHost.from_inventory(entry)))
    assert rv["phar_url"] == (
        "https://github.com/drush-ops/drush-launcher/releases/download/0.9.0/drush.phar"
    )


def test_resolved_vars_explicit_url_wins_over_the_version():
    entry = make_inventory(
        drush_launcher_version="0.9.0",
        drush_launcher_phar_url="https://example.com/drush.phar",
    )[0]
    rv = drush.resolved_vars(drush.role_settings(SimulatedHost.from_inventory(entry)))
    assert rv["phar_url"] == "https://example.com/drush.phar"
    assert rv["version"] == "0.9.0"


@pytest.mark.parametrize(
    ("overrides", "error", "match"),
    [
        ({"drush_launcher_install": "maybe"}, ValueError, "drush_launcher_install"),
        ({"drush_launcher_version": ""}, ValueError, "drush_launcher_version"),
        ({"drush_launcher_version": 42}, ValueError, "drush_launcher_version"),
        ({"drush_launcher_path": ""}, ValueError, "drush_launcher_path"),
        ({"drush_launcher_path": "bin/drush"}, ValueError, "drush_launcher_path"),
        ({"drush_launcher_phar_url": ""}, ValueError, "drush_launcher_phar_url"),
        ({"drush_launcher_phar_url": 42}, ValueError, "drush_launcher_phar_url"),
    ],
)
def test_role_settings_rejects_invalid_values(overrides, error, match):
    host = SimulatedHost.from_inventory(make_inventory(**overrides)[0])
    with pytest.raises(error, match=match):
        drush.role_settings(host)


@pytest.mark.parametrize("value", ["yes", "no", "true", "false", "on", "off", 1, 0])
def test_launcher_install_accepts_yaml_booleans(value):
    entry = make_inventory(drush_launcher_install=value)[0]
    settings = drush.role_settings(SimulatedHost.from_inventory(entry))
    assert isinstance(settings["drush_launcher_install"], bool)