# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``helm`` role (§17.5).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, helm(v), host_factory=...)``. The factory is plain
(non-``async``): the role's reads between tasks are the ``fact(FileExists)``
gate and the ``act(Shell)`` version probe, and the tarball fetch lives
*inside* ``files.unarchive`` -- the control-side ``_fetch_url_bytes`` seam the
tests stub, so the download is answered offline exactly as the kubectl test
does.

Because the role pins a ``sha256:`` digest *and* the stub returns canned bytes,
the end-to-end installs pass an inventory ``checksums`` override for the canned
tarball's own digest (exercising the deviation-D5 override path), while the
shipped :data:`~reemote.roles.helm.HELM_CHECKSUMS` table is pinned directly by
the ``resolve_checksum`` unit tests and by ``test_unpinned_download_fails_the_
checksum`` -- a table-shipped version fetched against canned bytes must raise
``checksum mismatch``, proving the digest the role hands ``files.unarchive``
really comes from the table.

The load-bearing assertion is the migration's idempotence claim: run the role,
then run it again against the same persistent host, and the second pass reports
no change anywhere -- the binary now exists and ``helm version`` answers the
pinned release, so the body returns before emitting an install request, and the
control-side fetch does not happen a second time.
"""

import hashlib
from typing import Any

import pytest

from reemote import run_waves
from reemote.modules.files import files as files_module
from reemote.roles import HelmChecksum, HelmVars, helm
from reemote.roles.helm import (
    HELM_CHECKSUMS,
    helm_url,
    resolve_checksum,
    staged_binary,
    staging_for,
)
from reemote.rolevars import resolve_vars
from tests.simulated_host import SimulatedHost

VERSION = "v4.3.2"
PLATFORM = "linux"
ARCH = "amd64"
REPO_PATH = "https://get.helm.sh"
BIN_PATH = "/usr/local/bin/helm"
STAGING = f"/tmp/helm-{VERSION}"
STAGED = f"{STAGING}/{PLATFORM}-{ARCH}/helm"
URL = helm_url(REPO_PATH, VERSION, PLATFORM, ARCH)

# The URL spool ``files.unarchive`` stages the fetched tarball into, computed
# with the module's own helper so the ``archive_members`` seed cannot drift.
SPOOL = files_module._unarchive_spool_path(URL)

# Non-UTF-8 bytes stand in for a real ``.tar.gz`` body (``files.unarchive``
# treats ``src`` as binary and never decodes it).
FAKE_TARBALL = b"\x1f\x8b\x08\x00 helm-canned-tarball-bytes \x00\xff"
FAKE_SHA = hashlib.sha256(FAKE_TARBALL).hexdigest()

# The canned tarball's digest, pinned via the role's ``checksums`` override so
# the control-side verification passes offline (deviation D5's override path).
OVERRIDE = [{"version": VERSION, "arch": ARCH, "sha256": FAKE_SHA}]

# The ``helm version`` long-form output, which carries the version as a
# substring exactly as a real build does.
VERSION_SHELL = f"{BIN_PATH} version"
CURRENT_STDOUT = f'version.BuildInfo{{Version:"{VERSION}", GitCommit:"8f4502a"}}\n'
STALE_STDOUT = 'version.BuildInfo{Version:"v3.0.0", GitCommit:"deadbeef"}\n'

UNARCH_NAME = f"Unpack {URL} into {STAGING}."
COPY_NAME = f"Copy {STAGED} to {BIN_PATH}."


@pytest.fixture
def canned_fetch(monkeypatch):
    """Replace the control-side download with a canned-bytes stub.

    ``files.unarchive`` looks ``_fetch_url_bytes`` up as a module global at call
    time, so patching the module attribute redirects the fetch. Returns the list
    of URLs the stub was called with, so a test can assert the fetch happened
    (and that a skipped host, a converged host and a ``dry`` preview fetch
    nothing).
    """
    fetched: list[str] = []

    def fake_fetch(url: str) -> bytes:
        fetched.append(url)
        return FAKE_TARBALL

    monkeypatch.setattr(files_module, "_fetch_url_bytes", fake_fetch)
    return fetched


def seed(host: SimulatedHost) -> None:
    """Pre-state: a stock Ubuntu machine, no helm yet.

    Only ``/tmp`` (the spool and the versioned staging directory land there)
    and ``/usr/local/bin`` (the copy's rename parent) exist besides; the
    binary path is absent, so the first pass installs it. The archive-member
    seed reproduces the release tarball's ``linux-amd64/helm`` layout, and the
    version probe answers the pinned release so a converged host short-circuits.
    """
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.directory_stats = {
        "/tmp": {"mode": 1777, "user": "root", "group": "root"},
        "/usr/local/bin": {"mode": 755, "user": "root", "group": "root"},
    }
    host.existing_files |= set(host.directory_stats)
    host.archive_members = {SPOOL: (f"{PLATFORM}-{ARCH}/", f"{PLATFORM}-{ARCH}/helm")}
    host.shell_results[VERSION_SHELL] = {
        "exit_status": 0,
        "stdout": CURRENT_STDOUT,
        "stderr": "",
    }


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride in the entry's ``roles`` mapping under ``"helm"``,
    bound by ``resolve_vars``.
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
        "roles": {
            "helm": {},
        },
    }
    entry["roles"]["helm"].update(overrides)
    return [
        entry,
        {
            "host": "web1",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
        },
    ]


def make_hosts(inventory: list[dict[str, Any]]) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(host)
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
    *,
    dry: bool = False,
) -> Any:
    """Resolve variables, build the sync factory's deploy, and run one wave pass."""
    v = resolve_vars(HelmVars, "helm", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        # The *same* host object every pass, so state survives the barriers.
        return hosts[entry["host"]]

    # A plain (non-async) factory: called, not awaited.
    deploy = helm(v)
    report, _hosts = await run_waves(inventory, deploy, host_factory=factory, dry=dry)
    return report


def changed_entries(report: Any) -> list[Any]:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def entry_names(report: Any) -> set[str]:
    """Every log entry name of one pass."""
    return {entry.name for entry in report.merged().log}


# --- the first pass really installs helm -------------------------------------


async def test_first_pass_installs_helm(canned_fetch):
    """The full stream: FileExists gate, extract into staging, copy into place."""
    inventory = make_inventory(checksums=OVERRIDE)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # The role fetched the default version's amd64 URL once.
    assert canned_fetch == [URL]

    # Both install requests ran, in task order.
    assert entry_names(report) == {UNARCH_NAME, COPY_NAME}

    # The binary was extracted into the versioned staging directory (deviation
    # D4) and copied to the managed path at 0755.
    assert STAGED in host.existing_files
    assert BIN_PATH in host.existing_files
    assert host.file_stats[BIN_PATH]["mode"] == 755

    assert changed_entries(report)


async def test_non_ubuntu_host_is_never_touched(canned_fetch):
    inventory = make_inventory(checksums=OVERRIDE)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    skipped = hosts["web1"]
    assert skipped.remote_files == {}
    assert skipped.directory_stats == {}
    # The family guard returns before the first yield, so the off-target host
    # emits no log entry and reads no fact.
    assert not any(entry.host == "web1" for entry in report.merged().log)


# --- the second pass is a no-op ----------------------------------------------


async def test_second_pass_changes_nothing(canned_fetch):
    inventory = make_inventory(checksums=OVERRIDE)
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    # Deviation D2, asserted: the converged host's ``helm version`` answers the
    # pinned release, so the body returns before the install -- no second fetch.
    assert canned_fetch == [URL]
    assert entry_names(second) == set()


# --- the version probe feeds the branch (deviation D2) -----------------------


async def test_stale_version_reinstalls(canned_fetch):
    """A present-but-wrong-version binary still triggers the install."""
    inventory = make_inventory(checksums=OVERRIDE)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    # The binary is there but reports an old release, and the staging
    # directory is empty -- so the extract and the copy both run.
    host.existing_files.add(BIN_PATH)
    host.file_stats[BIN_PATH] = {"mode": 755, "user": "root", "group": "root"}
    host.shell_results[VERSION_SHELL] = {
        "exit_status": 0,
        "stdout": STALE_STDOUT,
        "stderr": "",
    }

    report = await run_role(inventory, hosts)
    assert canned_fetch == [URL]
    assert entry_names(report) == {UNARCH_NAME, COPY_NAME}
    assert changed_entries(report)


# --- the dry preview parks at the FileExists gate ----------------------------


async def test_dry_preview_mutates_nothing(canned_fetch):
    """``dry=True`` never runs the gate or the install, so nothing is written."""
    inventory = make_inventory(checksums=OVERRIDE)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    before_dirs = dict(host.directory_stats)

    await run_role(inventory, hosts, dry=True)
    assert canned_fetch == []
    assert host.remote_files == {}
    assert STAGED not in host.existing_files
    assert BIN_PATH not in host.existing_files
    assert host.directory_stats == before_dirs


# --- the checksum table (deviation D5) ---------------------------------------


async def test_unshipped_version_fails_fast_without_an_override(canned_fetch):
    """The waves-model stand-in for a missing pin: no fetch, no host contact."""
    inventory = make_inventory(version="v3.1.0")
    hosts = make_hosts(inventory)

    with pytest.raises(ValueError, match="no sha256 checksum for helm v3.1.0"):
        await run_role(inventory, hosts)
    assert canned_fetch == []
    host = hosts["ubuntu"]
    assert host.remote_files == {}
    # The gate never ran either -- the fail-fast precedes the first barrier.
    assert BIN_PATH not in host.existing_files


async def test_unpinned_download_fails_the_checksum(canned_fetch):
    """The table's digest reaches ``files.unarchive`` and rejects wrong bytes.

    No override is configured, so the role pins the *shipped* v4.3.2/amd64
    digest; the stub returns different canned bytes, and the control-side
    verification raises before anything is spooled -- proving the constant the
    other tests override is consulted on the plain path.
    """
    inventory = make_inventory()  # default version, no checksums override
    hosts = make_hosts(inventory)

    with pytest.raises(ValueError, match="checksum mismatch"):
        await run_role(inventory, hosts)
    assert hosts["ubuntu"].existing_files.isdisjoint({STAGED, BIN_PATH})


async def test_unshipped_version_installs_with_an_override(canned_fetch):
    """An inventory pin fully substitutes for the table (deviation D5)."""
    inventory = make_inventory(
        version="v3.0.0",
        checksums=[{"version": "v3.0.0", "arch": ARCH, "sha256": FAKE_SHA}],
    )
    hosts = make_hosts(inventory)
    # The staging layout keys on the version, so reseed the member listing.
    url = helm_url(REPO_PATH, "v3.0.0", PLATFORM, ARCH)
    spool = files_module._unarchive_spool_path(url)
    hosts["ubuntu"].archive_members = {
        spool: (f"{PLATFORM}-{ARCH}/", f"{PLATFORM}-{ARCH}/helm")
    }
    hosts["ubuntu"].shell_results[f"{BIN_PATH} version"] = {
        "exit_status": 0,
        "stdout": CURRENT_STDOUT.replace(VERSION, "v3.0.0"),
        "stderr": "",
    }

    report = await run_role(inventory, hosts)
    assert canned_fetch == [url]
    assert BIN_PATH in hosts["ubuntu"].existing_files
    assert changed_entries(report)


# --- the pure helpers, unit-tested directly ----------------------------------


def test_helm_url_form():
    assert helm_url("https://get.helm.sh", "v4.3.2", "linux", "amd64") == (
        "https://get.helm.sh/helm-v4.3.2-linux-amd64.tar.gz"
    )


def test_staging_paths():
    staging = staging_for("/tmp", "v4.3.2")
    assert staging == "/tmp/helm-v4.3.2"
    assert staged_binary(staging, "linux", "amd64") == (
        "/tmp/helm-v4.3.2/linux-amd64/helm"
    )


def test_resolve_checksum_reads_the_shipped_table():
    # Spot rows of the frozen table (get.helm.sh digests, linux archs).
    assert resolve_checksum(HelmVars(), "v4.3.2", "amd64") == (
        "d1c0278d46beab79582d7e50f5b3dc206944ba8e577cde618513fd07daa53983"
    )
    assert resolve_checksum(HelmVars(), "v3.22.1", "arm64") == (
        "4c29e286b279d170fa048ddfc3e4e83dcd069ca9c52102766ba672d7149e694d"
    )
    assert resolve_checksum(HelmVars(), "v4.2.4", "ppc64le") == (
        "5c00073e9d493de201384bb7eb19d60615bd7c39db52148473e8ce6da84bc70a"
    )


def test_resolve_checksum_override_wins():
    hv = HelmVars(
        checksums=[HelmChecksum(version="v4.3.2", arch="amd64", sha256="a" * 64)]
    )
    assert resolve_checksum(hv, "v4.3.2", "amd64") == "a" * 64
    # An override for another pair does not disturb the table lookup.
    assert resolve_checksum(hv, "v4.3.2", "arm64") == (
        "df31cb61680ef728f82c770e5280dd565617bee6b039d37e85c677288be4bbb0"
    )


def test_resolve_checksum_missing_raises():
    with pytest.raises(ValueError, match="no sha256 checksum"):
        resolve_checksum(HelmVars(), "v0.1.0", "amd64")
    with pytest.raises(ValueError, match="no sha256 checksum"):
        resolve_checksum(HelmVars(), "v4.3.2", "riscv64")


def test_shipped_table_shape():
    """Every table row is a 64-hex digest, over the recent linux releases."""
    assert set(HELM_CHECKSUMS) == {
        "v4.3.2",
        "v4.3.1",
        "v4.3.0",
        "v4.2.4",
        "v3.22.1",
        "v3.22.0",
    }
    archs = {"amd64", "arm", "arm64", "s390x", "ppc64le"}
    for version, rows in HELM_CHECKSUMS.items():
        assert set(rows) == archs, version
        for arch, digest in rows.items():
            assert len(digest) == 64, (version, arch)
            int(digest, 16)  # raises if not hex


# --- variable validation -----------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"version": ""}, "non-empty"),
        ({"version": "v4/../x"}, "plain"),
        ({"arch": ""}, "non-empty"),
        ({"platform": ""}, "non-empty"),
        ({"repo_path": ""}, "helm_repo_path"),
        ({"bin_path": "bin/helm"}, "absolute"),
        ({"bin_path": "/"}, "must name a file"),
        ({"staging_dir": "tmp"}, "absolute"),
        ({"privilege_escalate": "maybe"}, "privilege_escalate"),
        ({"unknown_knob": True}, "unknown_knob"),
        ({"checksums": [{"version": "", "arch": "amd64", "sha256": "a" * 64}]}, "version"),
        ({"checksums": [{"version": "v1", "arch": "amd64", "sha256": "nothex"}]}, "hex digest"),
        ({"checksums": [{"version": "v1", "arch": "amd64"}]}, "sha256"),
    ],
)
def test_vars_reject_invalid_values(kwargs, match):
    # pydantic's ValidationError is a ValueError; so is every validator raise.
    with pytest.raises(ValueError, match=match):
        HelmVars(**kwargs)


def test_paths_normalized():
    assert HelmVars(repo_path="https://m/helm/").repo_path == "https://m/helm"
    assert HelmVars(bin_path="/opt/bin/helm").bin_path == "/opt/bin/helm"
    assert HelmVars(staging_dir="/var/tmp/").staging_dir == "/var/tmp"
