# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``kubectl`` role (§17.5).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, kubectl(v), host_factory=...)``. The factory is plain
(non-``async``): the role's one read between tasks is the
``fact(server.Arch)`` gate, and the binary fetch lives *inside*
``files.download`` -- the control-side ``_fetch_url_bytes`` seam the tests
stub, so the download is answered offline exactly as the composer/drush
tests do.

Because the role pins a ``sha256:`` digest *and* the stub returns canned
bytes, the end-to-end installs pass an inventory ``checksums`` override for
the canned binary's own digest (exercising the deviation-D4 override path),
while the shipped :data:`~reemote.roles.kubectl.LINUX_CHECKSUMS` table is
pinned directly by the ``resolve_checksum`` unit tests and by
``test_unpinned_download_fails_the_checksum`` -- a table-shipped version
fetched against canned bytes must raise ``Checksum mismatch``, proving the
digest the role passes to ``files.download`` really comes from the table.

The load-bearing assertion is the migration's idempotence claim: run the
role, then run it again against the same persistent hosts, and the second
pass reports no change anywhere -- the directory exists, the binary's
content matches the pin, the link already points at it. The converged pass
does re-fetch the URL control-side (deviation D3: content-only idempotence
needs the bytes to compare), which is asserted rather than hidden.
"""

import hashlib
from typing import Any

import pytest

from reemote import run_waves
from reemote.modules.files import files as files_module
from reemote.roles import KubectlChecksum, KubectlVars, kubectl
from reemote.roles.kubectl import (
    ARCH_MAP,
    LINUX_CHECKSUMS,
    arch_for,
    kubectl_url,
    resolve_checksum,
)
from reemote.rolevars import resolve_vars
from tests.simulated_host import SimulatedHost

INSTALL_DIR = "/usr/local/bin"
EXE = "/usr/local/bin/kubectl-v1.37.2"
LINK = "/usr/local/bin/kubectl"
URL = "https://dl.k8s.io/release/v1.37.2/bin/linux/amd64/kubectl"

# Non-UTF-8 bytes drive files.download down the issue-#23 binary install
# branch, exactly like the real kubectl ELF.
FAKE_BINARY = b"\x7fELF\x02\x01\x00\x00\xff\xfe kubectl-canned-bytes \x00"
FAKE_SHA = hashlib.sha256(FAKE_BINARY).hexdigest()

# The canned binary's digest, pinned via the role's ``checksums`` override so
# the control-side verification passes offline (deviation D4's override path).
OVERRIDE = [{"version": "v1.37.2", "arch": "amd64", "sha256": FAKE_SHA}]

DIR_NAME = "Ensure install directory /usr/local/bin exists."
DL_NAME = f"Download {URL}"
LINK_NAME = f"Link {EXE} to {LINK}."


@pytest.fixture
def canned_fetch(monkeypatch):
    """Replace the control-side download with a canned-bytes stub.

    ``files.download`` looks ``_fetch_url_bytes`` up as a module global at call
    time, so patching the module attribute redirects the fetch. Returns the
    list of URLs the stub was called with, so a test can assert the fetch
    happened (and that a skipped host and a ``dry`` preview fetch nothing).
    """
    fetched: list[str] = []

    def fake_fetch(url: str) -> bytes:
        fetched.append(url)
        return FAKE_BINARY

    monkeypatch.setattr(files_module, "_fetch_url_bytes", fake_fetch)
    return fetched


def seed(host: SimulatedHost) -> None:
    """Pre-state: a stock Ubuntu machine, no kubectl yet.

    Only ``/tmp`` exists besides (the download's spool lands there); the
    role's own ``files.directory`` request creates ``/usr/local/bin``. The
    ``Arch`` fact answers from the defaulted ``arch`` field (``x86_64``).
    """
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.directory_stats = {
        "/tmp": {"mode": 1777, "user": "root", "group": "root"},
    }
    host.existing_files |= set(host.directory_stats)


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride in the entry's ``roles`` mapping under
    ``"kubectl"``, bound by ``resolve_vars``.
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
            "kubectl": {},
        },
    }
    entry["roles"]["kubectl"].update(overrides)
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
    v = resolve_vars(KubectlVars, "kubectl", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        # The *same* host object every pass, so state survives the barriers.
        return hosts[entry["host"]]

    # A plain (non-async) factory: called, not awaited.
    deploy = kubectl(v)
    report, _hosts = await run_waves(
        inventory, deploy, host_factory=factory, dry=dry
    )
    return report


def changed_entries(report: Any) -> list[Any]:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def entry_names(report: Any) -> set[str]:
    """Every log entry name of one pass."""
    return {entry.name for entry in report.merged().log}


# --- the first pass really installs kubectl ------------------------------------


async def test_first_pass_installs_kubectl(canned_fetch):
    """The full stream: Arch gate, directory, checksum-pinned download, link."""
    inventory = make_inventory(checksums=OVERRIDE)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # The role fetched the x86_64 -> amd64 URL of its default version.
    assert canned_fetch == [URL]

    # All three requests ran, in task order.
    assert entry_names(report) == {DIR_NAME, DL_NAME, LINK_NAME}

    # The install directory was created at 0755; the binary landed version-
    # suffixed at 0755; the bare name is a symlink to it.
    assert host.directory_stats[INSTALL_DIR]["mode"] == 755
    assert host.remote_files[EXE] == FAKE_BINARY
    assert host.file_stats[EXE]["mode"] == 755
    assert host.links[LINK] == EXE

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


# --- the second pass is a no-op -------------------------------------------------


async def test_second_pass_changes_nothing(canned_fetch):
    inventory = make_inventory(checksums=OVERRIDE)
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    # Deviation D3, asserted: the converged pass still fetches control-side to
    # compare content (the pin makes the two fetches deterministic), but the
    # install read-back short-circuits, so nothing changes again.
    assert canned_fetch == [URL, URL]
    assert hosts["ubuntu"].remote_files[EXE] == FAKE_BINARY


async def test_dry_preview_mutates_nothing(canned_fetch):
    """``dry=True`` never applies the three requests, so nothing is written."""
    inventory = make_inventory(checksums=OVERRIDE)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    before_dirs = dict(host.directory_stats)

    await run_role(inventory, hosts, dry=True)
    assert canned_fetch == []
    assert host.remote_files == {}
    assert host.file_stats == {}
    assert host.links == {}
    assert host.directory_stats == before_dirs


# --- the architecture gate -------------------------------------------------------


async def test_aarch64_installs_arm64(canned_fetch):
    """The ``fact(server.Arch)`` result drives the mirror's arch name."""
    inventory = make_inventory(
        checksums=[{"version": "v1.37.2", "arch": "arm64", "sha256": FAKE_SHA}]
    )
    hosts = make_hosts(inventory)
    hosts["ubuntu"].arch = "aarch64"

    await run_role(inventory, hosts)
    assert canned_fetch == [
        "https://dl.k8s.io/release/v1.37.2/bin/linux/arm64/kubectl"
    ]
    assert hosts["ubuntu"].remote_files[EXE] == FAKE_BINARY

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_unmapped_architecture_fails_fast(canned_fetch):
    """A ``uname -m`` outside the map raises before any request is emitted."""
    inventory = make_inventory(checksums=OVERRIDE)
    hosts = make_hosts(inventory)
    hosts["ubuntu"].arch = "riscv64"

    with pytest.raises(ValueError, match="unsupported machine architecture"):
        await run_role(inventory, hosts)
    assert canned_fetch == []
    assert hosts["ubuntu"].remote_files == {}


# --- the checksum table (deviation D4) --------------------------------------------


async def test_unshipped_version_fails_fast_without_an_override(canned_fetch):
    """The waves-model stand-in for Ansible's undefined-key error: no fetch."""
    inventory = make_inventory(ver="v1.20.0")
    hosts = make_hosts(inventory)

    with pytest.raises(ValueError, match="no sha256 checksum for kubectl v1.20.0"):
        await run_role(inventory, hosts)
    assert canned_fetch == []
    assert hosts["ubuntu"].remote_files == {}
    assert hosts["ubuntu"].directory_stats == {
        "/tmp": {"mode": 1777, "user": "root", "group": "root"}
    }


async def test_unpinned_download_fails_the_checksum(canned_fetch):
    """The table's digest reaches ``files.download`` and rejects wrong bytes.

    No override is configured, so the role pins the *shipped* v1.37.2/amd64
    digest; the stub returns different canned bytes, and the control-side
    verification raises before anything is spooled -- proving the constant the
    tests override in other cases is consulted on the plain path.
    """
    inventory = make_inventory()  # default ver, no checksums override
    hosts = make_hosts(inventory)

    with pytest.raises(ValueError, match="checksum mismatch"):
        await run_role(inventory, hosts)
    assert hosts["ubuntu"].remote_files == {}


# --- the pure helpers, unit-tested directly ---------------------------------------


def test_kubectl_url_form():
    assert kubectl_url("https://dl.k8s.io/release", "v1.37.2", "amd64") == (
        "https://dl.k8s.io/release/v1.37.2/bin/linux/amd64/kubectl"
    )


def test_arch_for_every_mapped_machine():
    assert set(ARCH_MAP) == {
        "aarch64",
        "aarch32",
        "armv6l",
        "armv7l",
        "arm64",
        "ppc64le",
        "s390x",
        "x86_64",
    }
    assert ARCH_MAP["x86_64"] == "amd64"
    assert ARCH_MAP["aarch64"] == "arm64"
    assert ARCH_MAP["armv7l"] == "arm"
    for machine in ARCH_MAP:
        assert arch_for(machine) == ARCH_MAP[machine]


def test_arch_for_unknown_machine_raises():
    with pytest.raises(ValueError, match="riscv64"):
        arch_for("riscv64")


def test_resolve_checksum_reads_the_shipped_table():
    # Spot rows of the frozen table (defaults/main.yml, linux only).
    assert resolve_checksum(KubectlVars(), "v1.37.2", "amd64") == (
        "c04487523e7aa7d1493f7808174c899db93d7a9cf57531420cd76b9bd415f8f4"
    )
    assert resolve_checksum(KubectlVars(), "v1.36.0", "arm") == (
        "bd1ab1f3d3cb7dec7c46799355e82717a6519fa83708752c2be88fe5c378681f"
    )
    assert resolve_checksum(KubectlVars(), "v1.37.1", "ppc64le") == (
        "50a33e7a7f0d1125099abbdba3c010a3cb497142f58ed8ea198949f738e79b82"
    )


def test_resolve_checksum_override_wins():
    hv = KubectlVars(
        checksums=[KubectlChecksum(version="v1.37.2", arch="amd64", sha256="a" * 64)]
    )
    assert resolve_checksum(hv, "v1.37.2", "amd64") == "a" * 64
    # An override for another pair does not disturb the table lookup.
    assert resolve_checksum(hv, "v1.37.2", "arm64") == (
        "fcc16f63186659d9722014b7e02d5fdd2d4e669cb8c0ab66365d7d3b8ba03509"
    )


def test_resolve_checksum_missing_raises():
    with pytest.raises(ValueError, match="no sha256 checksum"):
        resolve_checksum(KubectlVars(), "v0.1.0", "amd64")
    with pytest.raises(ValueError, match="no sha256 checksum"):
        resolve_checksum(KubectlVars(), "v1.37.2", "riscv64")


def test_shipped_table_shape():
    """Every table row is a 64-hex digest, over the recent linux releases only."""
    assert set(LINUX_CHECKSUMS) == {
        "v1.36.0",
        "v1.36.1",
        "v1.36.2",
        "v1.36.3",
        "v1.36.4",
        "v1.36.5",
        "v1.36.6",
        "v1.37.0",
        "v1.37.1",
        "v1.37.2",
    }
    archs = {"amd64", "arm", "arm64", "s390x", "ppc64le"}
    for version, rows in LINUX_CHECKSUMS.items():
        assert set(rows) == archs, version
        for arch, digest in rows.items():
            assert len(digest) == 64, (version, arch)
            int(digest, 16)  # raises if not hex


async def test_unshipped_version_installs_with_an_override(canned_fetch):
    """An inventory pin fully substitutes for the table (deviation D4)."""
    inventory = make_inventory(
        ver="v1.20.0",
        checksums=[{"version": "v1.20.0", "arch": "amd64", "sha256": FAKE_SHA}],
    )
    hosts = make_hosts(inventory)

    await run_role(inventory, hosts)
    assert canned_fetch == [
        "https://dl.k8s.io/release/v1.20.0/bin/linux/amd64/kubectl"
    ]
    exe = "/usr/local/bin/kubectl-v1.20.0"
    assert hosts["ubuntu"].remote_files[exe] == FAKE_BINARY
    assert hosts["ubuntu"].links[LINK] == exe

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- variable validation ----------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"ver": ""}, "kubectl_ver"),
        ({"ver": "v1/../x"}, "plain release name"),
        ({"mirror": ""}, "kubectl_mirror"),
        ({"install_dir": "bin"}, "absolute"),
        ({"install_dir": ""}, "kubectl_install_dir"),
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
        KubectlVars(**kwargs)


def test_mirror_trailing_slash_stripped():
    assert KubectlVars(mirror="https://m/release/").mirror == "https://m/release"
    assert KubectlVars(install_dir="/opt/bin/").install_dir == "/opt/bin"
