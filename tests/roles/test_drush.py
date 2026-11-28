# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``drush`` role (§17.5, §18.9).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, drush(v), host_factory=...)``. Unlike composer, drush
needs no ``SimulatedHost`` subclass -- every mutator it reaches
(``install_file``/``make_directory``/``remove_tree``) maintains the file stores
itself, and the role runs no ``Shell`` action whose side effect would have to be
bridged in-stream. The factory is plain (non-``async``), because the phar's fetch
lives *inside* ``files.download`` (the control-side ``_fetch_url_bytes`` seam the
library's own ``tests/test_files_download.py`` patches), not as a barrier.

The load-bearing assertion is the migration's shape claim from §18.9: **two
``fact`` barriers then ``yield from`` -- and nothing else**, so every pass is
exactly three waves (wave 0 ``fact(FileLink)``, wave 1 ``fact(File)``, wave 2 the
launcher requests) and there is no ``act``/``control``/checkpoint. That claim is
checked directly by reading the barrier records out of ``report.records`` (a
fact-only wave never appears in ``wave_ids``). The second wave-shaped
claim is idempotence: run the role, then run it *again* against the same
persistent hosts, and the second pass must report no change anywhere -- the
directory matches, the phar's sha1 matches (``content already up to date``), the
mode matches. ``run_waves`` re-derives both barriers on the rerun rather than
reusing a frozen plan, so convergence proves the barriers themselves are honest.

Like every ``files.download`` caller, the role fetches the phar on *every* real
pass (the sha1 comparison must read the body), so the no-change claim is about
changed entries, not fetch counts. A ``dry=True`` pass is one wave deep: the
role's first item is the ``FileLink`` fact barrier, so the run parks there before
reading any state, fetching nothing and applying no request -- this differs from
the old two-phase script, whose ``dry`` prepare pass fetched the phar control-side
to plan the sha1 comparison.

The family guard is exercised on both sides of the RedHat extension: the
``web1`` CentOS target runs the *identical* stream (every launcher request is a
family-agnostic ``files`` operation, so the same waves install the same bytes),
while the ``db1`` Alpine host returns before its first barrier and records
nothing at all.
"""

import pytest

from reemote import run_waves
from reemote.modules.files import File, FileLink
from reemote.modules.files import files as files_module
from reemote.modules.files.files import _install_spool_path
from reemote.roles import DrushVars, drush
from reemote.roles.drush import DEFAULT_LAUNCHER_VERSION, PHAR_URL_TEMPLATE, phar_url
from reemote.rolevars import resolve_vars
from tests.simulated_host import SimulatedHost

VERSION = DEFAULT_LAUNCHER_VERSION
URL = PHAR_URL_TEMPLATE.format(version=VERSION)
DEST = "/usr/local/bin/drush"
DEST_DIR = "/usr/local/bin"
SPOOL = _install_spool_path(DEST)

# A binary phar body (never valid UTF-8): the launcher is a PHP archive with
# binary sections, opaque to the simulation. The non-UTF-8 bytes are what drive
# files.download down the issue-#23 binary install branch.
FAKE_PHAR = b"#!/usr/bin/env php\n<?php /* \xff\xfe\x00drush-launcher-phar */"


@pytest.fixture
def canned_fetch(monkeypatch):
    """Replace the control-side phar fetch with a canned-bytes stub.

    ``files.download`` looks ``_fetch_url_bytes`` up as a module global at call
    time, so patching the module attribute redirects the fetch. Returns the list
    of URLs the stub was called with, so a test can assert the fetch happened
    (and that a disabled launcher, a skipped host and a ``dry`` preview fetch
    nothing).
    """
    fetched: list[str] = []

    def fake_fetch(url: str) -> bytes:
        fetched.append(url)
        return FAKE_PHAR

    monkeypatch.setattr(files_module, "_fetch_url_bytes", fake_fetch)
    return fetched


def make_inventory(**overrides) -> list[dict]:
    """A three-host inventory: Ubuntu and RedHat targets, one Alpine host to skip.

    The role variables ride in the entries' ``roles`` mapping under ``"drush"``,
    bound by ``resolve_vars``; the overrides apply to *both* target families, so
    a gate or path change is seen on each. With no overrides the role defaults
    apply (launcher install enabled, version 0.10.1, ``/usr/local/bin/drush``).
    """
    drush_vars: dict = dict(overrides)
    return [
        {
            "host": "ubuntu",
            "family": "Ubuntu",
            "connection": {
                "address": "192.0.2.10",
                "username": "supervisor",
                "key_path": "~/.ssh/id_ed25519",
            },
            "group": "ubuntu",
            "tags": ["ubuntu_host", "apt_package_manager"],
            "roles": {"drush": drush_vars},
        },
        {
            "host": "web1",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
            "roles": {"drush": drush_vars},
        },
        {
            "host": "db1",
            "family": "Alpine",
            "connection": {"address": "192.0.2.12", "username": "root"},
            "group": "alpine",
        },
    ]


def seed(host: SimulatedHost, *, with_dest_dir: bool = True) -> None:
    """Pre-state: a stock target machine (either family), no drush yet.

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


def _regular_file(
    host: SimulatedHost, path: str, data: bytes, mode: int
) -> None:
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
    inventory: list[dict], *, with_dest_dir: bool = True
) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") in ("Ubuntu", "RedHat"):
            seed(host, with_dest_dir=with_dest_dir)
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict], hosts: dict[str, SimulatedHost], *, dry: bool = False
):
    """Resolve variables, build the sync factory's deploy, and run one wave pass."""
    v = resolve_vars(DrushVars, "drush", inventory)

    def factory(entry: dict) -> SimulatedHost:
        # The *same* host object every pass, so state survives the barriers.
        return hosts[entry["host"]]

    # A plain (non-async) factory: called, not awaited.
    deploy = drush(v)
    report, _hosts = await run_waves(
        inventory, deploy, host_factory=factory, dry=dry
    )
    return report


def changed_entries(report) -> list:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def ubuntu_names(report) -> set[str]:
    """Every Ubuntu log entry name of one pass."""
    return {entry.name for entry in report.merged().log if entry.host == "ubuntu"}


# --- the shape: two fact barriers then the requests, nothing else --------------


def _shape(report, hostname: str = "ubuntu"):
    """One host's (barrier waves, request waves) for one pass.

    ``run_waves`` counts only waves that applied a request in ``wave_ids``, so
    the two fact-only waves are read from ``report.records`` instead -- each
    barrier record carries its ``fact_cls`` (an ``act``/``control``/checkpoint
    barrier carries none), which is what encodes §18.9's shape.
    """
    records = [r for r in report.records if r.host == hostname]
    barriers = [r for r in records if r.barrier is not None]
    requests = [r for r in records if r.log is not None]
    return barriers, [r.wave for r in requests]


async def test_stream_is_two_fact_barriers_then_requests(canned_fetch):
    """§18.9's falsifier: two ``fact`` barriers then one request wave.

    No ``act``, no ``control``, no checkpoint -- a third barrier or a fourth wave
    would mean a barrier had been added speculatively.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    barriers, request_waves = _shape(report)
    # Exactly two barriers, both fact reads, in the role's order, before the
    # requests (a non-fact barrier would carry no fact_cls and break this).
    assert [getattr(b.barrier, "fact_cls", None) for b in barriers] == [
        FileLink,
        File,
    ]
    assert [b.wave for b in barriers] == [0, 1]
    # Every request collapses into the single wave after the two barriers.
    assert set(request_waves) == {2}

    # The rerun re-derives both barriers, so the shape is stable across passes.
    second = await run_role(inventory, hosts)
    second_barriers, second_waves = _shape(second)
    assert [b.wave for b in second_barriers] == [0, 1]
    assert set(second_waves) == {2}


# --- the first pass really installs the launcher ------------------------------


async def test_first_pass_installs_the_launcher(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # The phar was fetched control-side and installed binary (issue #23):
    # root:root 0755 at the destination, the /tmp spool cleaned up. Both
    # target hosts fetched, in inventory order.
    assert canned_fetch == [URL, URL]
    assert host.remote_files[DEST] == FAKE_PHAR
    assert host.file_stats[DEST]["mode"] == 755
    assert host.file_stats[DEST]["user"] == "root"
    assert host.file_stats[DEST]["group"] == "root"
    assert SPOOL not in host.remote_files

    # The directory task was a no-op on the stock host; the executable-bit task
    # was not even requested, because the File barrier answered "absent".
    assert host.directory_stats[DEST_DIR]["mode"] == 755
    assert "Install Drush." in ubuntu_names(report)
    assert "Ensure Drush is executable." not in ubuntu_names(report)
    assert "Perform cleanup of old symlink." not in ubuntu_names(report)
    assert changed_entries(report)


async def test_off_target_alpine_host_is_never_touched(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    skipped = hosts["db1"]
    assert skipped.remote_files == {}
    assert skipped.directory_stats == {}
    assert skipped.links == {}
    # The family guard returns before the first yield, so the off-target host
    # emits no log entry and is never contacted or fetched for.
    assert not any(entry.host == "db1" for entry in report.merged().log)
    # Only the two target hosts fetched.
    assert canned_fetch == [URL, URL]


async def test_redhat_target_runs_the_identical_stream(canned_fetch):
    """The family-guard extension: the RedHat target converges like Ubuntu's.

    Every launcher request is a family-agnostic ``files`` operation, so the
    CentOS host shows the same three waves, installs the same bytes at the
    same mode, and reports no change on the rerun.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["web1"]

    barriers, request_waves = _shape(report, "web1")
    assert [getattr(b.barrier, "fact_cls", None) for b in barriers] == [
        FileLink,
        File,
    ]
    assert [b.wave for b in barriers] == [0, 1]
    assert set(request_waves) == {2}

    assert host.remote_files[DEST] == FAKE_PHAR
    assert host.file_stats[DEST]["mode"] == 755
    assert host.file_stats[DEST]["user"] == "root"
    assert SPOOL not in host.remote_files
    assert "Install Drush." in {
        entry.name for entry in report.merged().log if entry.host == "web1"
    }

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the second pass is a no-op -----------------------------------------------


async def test_second_pass_changes_nothing(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    # No bridge: install_file stored the bytes and the stat row, so pass 2's
    # FileLink/File reads and the download's sha1 read-back all converge.
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    # On pass 2 the File barrier now answers "regular file", so the executable
    # bit IS requested -- but it is a no-op, because the mode already matches.
    assert "Ensure Drush is executable." in ubuntu_names(second)
    install = next(
        entry
        for entry in second.merged().log
        if entry.name == "Install Drush." and entry.host == "ubuntu"
    )
    assert install.reason == f"content already up to date ({DEST})"


async def test_dry_preview_mutates_nothing(canned_fetch):
    """``dry=True`` is one wave deep, so it parks at the FileLink barrier.

    Unlike the old two-phase script -- whose prepare pass fetched the phar
    control-side -- the wave driver's dry pass never reaches a request or the
    second barrier, so it fetches nothing and mutates no store.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    before_dirs = dict(host.directory_stats)

    await run_role(inventory, hosts, dry=True)
    assert canned_fetch == []
    assert host.remote_files == {}
    assert host.file_stats == {}
    assert host.links == {}
    assert host.directory_stats == before_dirs
    # The RedHat target parks on its own first barrier too, untouched.
    assert hosts["web1"].remote_files == {}


# --- the symlink cleanup branch ------------------------------------------------


async def test_symlink_pre_state_is_removed_then_installed(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    _symlink(host, DEST, "/opt/drush/drush")

    report = await run_role(inventory, hosts)

    # The link itself is gone (force=True removes the link, not the target),
    # and the launcher phar sits at the path as a regular file.
    assert DEST not in host.links
    assert host.remote_files[DEST] == FAKE_PHAR
    assert host.file_stats[DEST]["mode"] == 755
    cleanup = next(
        entry
        for entry in report.merged().log
        if entry.name == "Perform cleanup of old symlink." and entry.host == "ubuntu"
    )
    assert cleanup.changed

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- drift convergence ----------------------------------------------------------


async def test_content_drift_reinstalls_once(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    _regular_file(host, DEST, b"stale launcher bytes", 755)

    report = await run_role(inventory, hosts)
    # sha1-idempotent, not exists-idempotent: the drifted binary converges.
    assert host.remote_files[DEST] == FAKE_PHAR
    install = next(
        entry
        for entry in report.merged().log
        if entry.name == "Install Drush." and entry.host == "ubuntu"
    )
    assert install.changed

    second = await run_role(inventory, hosts)
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

    report = await run_role(inventory, hosts)
    assert host.file_stats[DEST]["mode"] == 755
    by_name = {
        entry.name: entry for entry in report.merged().log if entry.host == "ubuntu"
    }
    assert not by_name["Install Drush."].changed
    assert by_name["Ensure Drush is executable."].changed

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the directory create branch -------------------------------------------------


async def test_missing_directory_is_created_0755(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory, with_dest_dir=False)
    host = hosts["ubuntu"]

    report = await run_role(inventory, hosts)
    assert DEST_DIR in host.directory_stats
    assert host.directory_stats[DEST_DIR]["mode"] == 755
    assert host.remote_files[DEST] == FAKE_PHAR
    directory = next(
        entry
        for entry in report.merged().log
        if entry.name == "Ensure Drush path directory exists."
        and entry.host == "ubuntu"
    )
    assert directory.changed

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the launcher_install gate and custom paths -----------------------------------


async def test_launcher_install_false_deploys_nothing(canned_fetch):
    inventory = make_inventory(launcher_install=False)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    # The include-level gate is the body's opening return, so no barrier, no
    # request, no log line and no fetch for either target host.
    assert changed_entries(report) == []
    assert not any(entry.host == "ubuntu" for entry in report.merged().log)
    assert not any(entry.host == "web1" for entry in report.merged().log)
    assert canned_fetch == []
    assert hosts["ubuntu"].remote_files == {}
    assert hosts["web1"].remote_files == {}


async def test_custom_path_and_version(canned_fetch):
    inventory = make_inventory(
        launcher_version="0.10.0", launcher_path="/opt/bin/drush"
    )
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    # files.directory is what creates /opt/bin here (the download needs it gone
    # first), so no pre-seed -- the role's own directory task handles it.
    custom_url = PHAR_URL_TEMPLATE.format(version="0.10.0")

    await run_role(inventory, hosts)
    assert canned_fetch == [custom_url, custom_url]
    assert host.remote_files["/opt/bin/drush"] == FAKE_PHAR
    assert host.directory_stats["/opt/bin"]["mode"] == 755
    assert DEST not in host.remote_files
    # The same override bound on the RedHat target installs the same way.
    assert hosts["web1"].remote_files["/opt/bin/drush"] == FAKE_PHAR

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the pure helpers, unit-tested directly ---------------------------------------


def test_phar_url_default():
    assert phar_url(DrushVars()) == URL


def test_phar_url_version_flows_in():
    assert phar_url(DrushVars(launcher_version="0.9.0")) == (
        "https://github.com/drush-ops/drush-launcher/releases/download/0.9.0/"
        "drush.phar"
    )


def test_phar_url_explicit_wins_over_version():
    hv = DrushVars(
        launcher_version="0.9.0",
        launcher_phar_url="https://example.com/drush.phar",
    )
    assert phar_url(hv) == "https://example.com/drush.phar"


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"launcher_path": "bin/drush"}, "drush_launcher_path"),
        ({"launcher_path": ""}, "drush_launcher_path"),
        ({"launcher_version": ""}, "drush_launcher_version"),
        ({"launcher_version": 42}, "launcher_version"),
        ({"launcher_phar_url": ""}, "drush_launcher_phar_url"),
        ({"launcher_phar_url": 42}, "launcher_phar_url"),
        ({"composer_global_install": True}, "scoped out"),
        ({"install_from_source": True}, "scoped out"),
    ],
)
def test_vars_reject_invalid_values(kwargs, match):
    # pydantic's ValidationError is a ValueError; so is every validator raise.
    # A non-string for a ``str`` field is rejected by pydantic's own type check,
    # which names the (unprefixed) field, hence "launcher_version" not
    # "drush_launcher_version" for the ``42`` cases.
    with pytest.raises(ValueError, match=match):
        DrushVars(**kwargs)


@pytest.mark.parametrize("value", ["yes", "no", "true", "false", "on", "off", 1, 0])
def test_launcher_install_accepts_yaml_booleans(value):
    assert isinstance(DrushVars(launcher_install=value).launcher_install, bool)
