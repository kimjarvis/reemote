# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end test for the EPEL-repo Ansible-role migration example.

``examples/roles/ansible-role-repo-epel/ansible-role-repo-epel.py`` connects
for real when run as a script, so the suite never executes its ``main()``.
What it *can* do offline is replay the same orchestration against
``SimulatedHost``: the example is imported as a module, its two factories are
fed the frozen plans gathered from simulated state, and both deploys go
through the real driver (prepare + apply, plus a ``dry=True`` preview each).

The load-bearing assertion is the migration's idempotence claim: run the
gather -> plan -> deploy sequence, then run it *again* against the same
persistent hosts, and the second pass must report ``changed=False``
everywhere -- the repo file exists (the install guard skips), the key id is
in the rpm keyring (issue #25's gate), the ``[epel]`` enabled line matches
(issue #24's ``files.ini_option``), the mode matches.

Three simulation limits are bridged explicitly, because the in-memory host
models no real subsystem (each is a *simulation* limit, not a library bug):

* ``rpm_files[SPOOL]`` seeds the staged RPM's name/version metadata -- the
  simulation parses no rpm binary, so ``install_rpm`` reads the seam instead
  of the downloaded bytes.
* the RPM *payload*: a real ``epel-release`` install writes
  ``/etc/yum.repos.d/epel.repo``; ``install_rpm`` only records the package.
  ``bridge_rpm_payload`` seeds the file (content/existence/stat) between
  deploy 1 and deploy 2 -- what the package payload does for real -- so the
  configure deploy's ``FileContents`` read and the second pass's ``FileExists``
  gather observe it.
* ``import_rpm_key`` appends a content-hash stub keyid (no OpenPGP parsing);
  the keyid-gate test seeds ``rpm_gpg_keys`` with the real EPEL 9 short id
  instead, the shape a live host's ``gpg-pubkey-3228467c-613798eb`` reports.

The control-side fetches (the armored key via ``files.fetch_url``, the binary
RPM via ``files.download``) are answered by monkeypatching
``files._fetch_url_bytes`` -- the same seam the library's own download tests
use. The RPM body is deliberately non-UTF-8 so the staging download drives
issue #23's binary install branch.
"""

import importlib.util
import io
import posixpath
from pathlib import Path
from typing import Any

import pytest

from reemote import FileExists, LinuxDistribution, gather_fact, run
from reemote.modules.files import files as files_module
from tests.simulated_host import SimulatedHost

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = (
    REPO_ROOT
    / "examples"
    / "roles"
    / "ansible-role-repo-epel"
    / "ansible-role-repo-epel.py"
)


def load_example() -> Any:
    """Import the migration example by path (its name is not a valid identifier)."""
    spec = importlib.util.spec_from_file_location(
        "ansible_role_repo_epel_example", EXAMPLE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


epel = load_example()

MAJOR = 9
RPM_URL = f"https://dl.fedoraproject.org/pub/epel/epel-release-latest-{MAJOR}.noarch.rpm"
KEY_URL = f"https://dl.fedoraproject.org/pub/epel/RPM-GPG-KEY-EPEL-{MAJOR}"
REPO_FILE = "/etc/yum.repos.d/epel.repo"
SPOOL = "/tmp/" + posixpath.basename(RPM_URL)
KEYID = "3228467c"
RPM_INFO = {"name": "epel-release", "version": "9-11.el9"}

# The live CentOS Stream 9 host's distribution facts (the LinuxDistribution
# seam's shape): family RedHat is what main()'s RHEL-family guard reads.
CENTOS_DISTRO = {
    "name": "CentOS Stream",
    "family": "RedHat",
    "major": MAJOR,
    "minor": None,
    "release_meta": {},
}

# An armored key body (text -- files.fetch_url UTF-8-decodes it) and a binary
# RPM body (never valid UTF-8 -- drives files.download's issue-#23 binary
# install branch). Both are opaque to the simulation.
FAKE_KEY = (
    "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\n"
    "mQINBFAKEepel9keymaterialnotreal0123456789\n"
    "-----END PGP PUBLIC KEY BLOCK-----\n"
)
FAKE_RPM = b"\xed\xab\xee\xdbepel-release-9-11.el9.noarch\xff\xfe\x00"

# The RPM-shipped three-section epel.repo, captured verbatim from a live
# CentOS Stream 9 host: [epel] enabled=1, the debuginfo/source sections
# enabled=0, comments and metalink/gpgkey/countme options intact. The
# section-scoping assertion (only [epel]'s enabled line may change) rests on
# this real shape.
EPEL_REPO = """[epel]
name=Extra Packages for Enterprise Linux 9 - $basearch
# It is much more secure to use the metalink, but if you wish to use a local mirror
# place its address here.
#baseurl=https://download.example/pub/epel/9/Everything/$basearch/
metalink=https://mirrors.fedoraproject.org/metalink?repo=epel-9&arch=$basearch&infra=$infra&content=$contentdir
enabled=1
gpgcheck=1
countme=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-EPEL-9

[epel-debuginfo]
name=Extra Packages for Enterprise Linux 9 - $basearch - Debug
# It is much more secure to use the metalink, but if you wish to use a local mirror
# place its address here.
#baseurl=https://download.example/pub/epel/9/Everything/$basearch/debug/
metalink=https://mirrors.fedoraproject.org/metalink?repo=epel-debug-9&arch=$basearch&infra=$infra&content=$contentdir
enabled=0
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-EPEL-9
gpgcheck=1

[epel-source]
name=Extra Packages for Enterprise Linux 9 - $basearch - Source
# It is much more secure to use the metalink, but if you wish to use a local mirror
# place its address here.
#baseurl=https://download.example/pub/epel/9/Everything/source/tree/
metalink=https://mirrors.fedoraproject.org/metalink?repo=epel-source-9&arch=$basearch&infra=$infra&content=$contentdir
enabled=0
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-EPEL-9
gpgcheck=1
"""


@pytest.fixture
def canned_fetch(monkeypatch):
    """Replace the control-side fetches with canned-body stubs.

    Both ``files.fetch_url`` (the armored key) and ``files.download`` (the
    binary RPM) look ``_fetch_url_bytes`` up as a module global at call time,
    so patching the module attribute redirects both. Returns the list of URLs
    the stub was called with, so a test can assert a converged host never
    fetches at all.
    """
    fetched: list[str] = []

    def fake_fetch(url: str) -> bytes:
        fetched.append(url)
        if url == RPM_URL:
            return FAKE_RPM
        if url == KEY_URL:
            return FAKE_KEY.encode("utf-8")
        raise AssertionError(f"unexpected fetch URL: {url}")

    monkeypatch.setattr(files_module, "_fetch_url_bytes", fake_fetch)
    return fetched


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one CentOS target, one Ubuntu host to be skipped.

    The role variables ride as extra keys -- reemote's ``host_vars`` -- so
    ``role_settings`` reads them straight off ``host.extra``. With no
    overrides the role defaults apply (EPEL enabled, stock repofile path).
    """
    entry: dict[str, Any] = {
        "host": "web1",
        "family": "RedHat",
        "connection": {
            "address": "192.0.2.10",
            "username": "supervisor",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "centos",
    }
    entry.update(overrides)
    return [
        entry,
        {
            "host": "ubuntu",
            "family": "Ubuntu",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "ubuntu",
        },
    ]


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


def seed(
    host: SimulatedHost,
    *,
    repo_present: bool = False,
    key_imported: bool = False,
    repo_text: str = EPEL_REPO,
    repo_mode: int = 644,
) -> None:
    """Pre-state: a stock CentOS Stream 9 machine.

    ``repo_present`` seeds the converged host (the RPM payload already
    installed: repo file + package + imported key); the default fresh host
    has neither. ``rpm_files[SPOOL]`` is always seeded -- the simulation
    parses no rpm binary, so the staged RPM's metadata comes from the seam.
    """
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.rpm_packages = {}
    host.rpm_files = {SPOOL: dict(RPM_INFO)}
    host.rpm_gpg_keys = []
    host.linux_distribution = dict(CENTOS_DISTRO)
    if repo_present:
        _regular_file(host, REPO_FILE, repo_text.encode("utf-8"), repo_mode)
        host.rpm_packages["epel-release"] = RPM_INFO["version"]
    if key_imported:
        host.rpm_gpg_keys.append(
            {
                "keyid": KEYID,
                "release": "613798eb",
                "summary": "Fedora (epel9) <epel@fedoraproject.org> public key",
            }
        )


def make_hosts(
    inventory: list[dict[str, Any]],
    *,
    repo_present: bool = False,
    key_imported: bool = False,
    repo_text: str = EPEL_REPO,
    repo_mode: int = 644,
) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if epel.is_centos(host):
            seed(
                host,
                repo_present=repo_present,
                key_imported=key_imported,
                repo_text=repo_text,
                repo_mode=repo_mode,
            )
        hosts[host.name] = host
    return hosts


def bridge_rpm_payload(
    hosts: dict[str, SimulatedHost], plans: dict[str, Any]
) -> None:
    """Seed the repo file a real epel-release install writes (simulation limit).

    ``install_rpm`` records the package but models no RPM payload, so the
    ``/etc/yum.repos.d/epel.repo`` the package ships must be planted by hand
    between deploy 1 and deploy 2 -- the configure deploy's ``FileContents``
    read (``create=False`` raises on an absent file) and the second pass's
    ``FileExists`` gather both observe it.
    """
    for name, plan in plans.items():
        host = hosts[name]
        if plan["repo_exists"] or "epel-release" not in host.rpm_packages:
            continue
        path = plan["repofile"]
        if path not in host.remote_files:
            _regular_file(host, path, EPEL_REPO.encode("utf-8"), 644)


async def migrate(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
    *,
    dry: bool = True,
    apply: bool = True,
) -> dict[str, Any]:
    """Replay the example's whole orchestration once, against persistent hosts.

    The sequence mirrors ``main()`` barrier for barrier -- the variable
    resolution, the ``LinuxDistribution``/``FileExists`` reads frozen into the
    plans, the control-side key fetch, deploy 1 (install) then deploy 2
    (configure), each previewed then applied -- but reads the simulated hosts
    instead of opening SSH connections. ``apply=False`` stops after deploy 1's
    dry preview (reports is empty).
    """
    targets = [host for host in hosts.values() if epel.is_centos(host)]
    settings = {host.name: epel.role_settings(host) for host in targets}

    plans: dict[str, Any] = {}
    try:
        distributions = await gather_fact(targets, LinuxDistribution)
        for host in targets:
            info = distributions[host.name]
            assert info["family"] == "RedHat"
            rv = epel.resolved_vars(settings[host.name], info["major"])
            exists = (await gather_fact([host], FileExists, rv["repofile"]))[host.name]
            plans[host.name] = {**rv, "major": info["major"], "repo_exists": bool(exists)}
    finally:
        for host in targets:
            await host.aclose()

    for host in targets:
        plan = plans[host.name]
        plan["rpm_spool"] = "/tmp/" + posixpath.basename(plan["rpm_url"])
        if plan["repo_exists"]:
            plan["key_stream"] = None
        else:
            key_text = await files_module.fetch_url(plan["key_url"])
            plan["key_stream"] = io.BytesIO(key_text.encode("utf-8"))

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        # The *same* host object every run, so state survives the barriers.
        return hosts[entry["host"]]

    install = epel.build_install(plans)
    configure = epel.build_configure(plans)
    if dry:
        await run(inventory, install, host_factory=factory, dry=True)
    if not apply:
        return {"reports": [], "plans": plans}
    _plans, report_install, _hosts = await run(
        inventory, install, host_factory=factory
    )
    bridge_rpm_payload(hosts, plans)
    if dry:
        await run(inventory, configure, host_factory=factory, dry=True)
    _plans, report_configure, _hosts = await run(
        inventory, configure, host_factory=factory
    )
    assert report_install is not None and report_configure is not None
    return {"reports": [report_install, report_configure], "plans": plans}


def changed_entries(result: dict[str, Any]) -> list[Any]:
    """Every changed log entry across the two deploys."""
    return [
        entry for report in result["reports"] for entry in report.log if entry.changed
    ]


def entry_named(result: dict[str, Any], name: str, host: str = "web1") -> Any:
    """The one log entry with *name* on *host* (across the two deploys)."""
    return next(
        entry
        for report in result["reports"]
        for entry in report.log
        if entry.name == name and entry.host == host
    )


# --- the first pass really installs EPEL --------------------------------------


async def test_first_pass_installs_epel(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    result = await migrate(inventory, hosts)
    host = hosts["web1"]

    # The plan froze the stat read: no repo file yet.
    assert result["plans"]["web1"]["repo_exists"] is False
    assert result["plans"]["web1"]["keyid"] == KEYID

    # Both bodies were fetched control-side; the key imported (the stub
    # keyid proves the import ran), the package installed, the spool cleaned.
    assert KEY_URL in canned_fetch
    assert RPM_URL in canned_fetch
    assert host.rpm_gpg_keys  # import_rpm_key appended its stub entry
    assert host.rpm_packages["epel-release"] == RPM_INFO["version"]
    assert SPOOL not in host.remote_files
    # The bridged RPM payload: the repo file sits at the path, enabled=1.
    assert host.remote_files[REPO_FILE] == EPEL_REPO.encode("utf-8")
    assert host.file_stats[REPO_FILE]["mode"] == 644

    assert entry_named(result, "Import EPEL GPG key.").changed
    assert entry_named(result, "Stage the EPEL release RPM.").changed
    assert entry_named(result, "Install the EPEL release RPM.").changed
    assert entry_named(result, "Remove the staged EPEL release RPM.").changed
    # The converged enabled line needs no edit on the fresh payload.
    assert not entry_named(result, "Ensure EPEL repo is enabled / disabled.").changed
    assert changed_entries(result)


async def test_non_centos_host_is_never_touched(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    result = await migrate(inventory, hosts)

    skipped = hosts["ubuntu"]
    assert skipped.remote_files == {}
    assert skipped.rpm_packages == {}
    assert skipped.rpm_gpg_keys == []
    for report in result["reports"]:
        for entry in report.log:
            if entry.host == "ubuntu":
                assert entry.skipped
                assert not entry.changed


# --- the second pass is a no-op -----------------------------------------------


async def test_second_pass_changes_nothing(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await migrate(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"
    fetches_after_first = len(canned_fetch)

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []
    assert second["plans"]["web1"]["repo_exists"] is True
    # The install guard skipped everything: no key fetch, no RPM download.
    assert len(canned_fetch) == fetches_after_first
    assert (
        entry_named(second, "Ensure EPEL repo is enabled / disabled.").reason
        == f"content already up to date ({REPO_FILE})"
    )


async def test_converged_host_is_a_full_noop(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory, repo_present=True, key_imported=True)
    result = await migrate(inventory, hosts)

    assert changed_entries(result) == []
    assert canned_fetch == []  # nothing fetched at all
    # Deploy 1 yielded nothing for the converged host.
    assert [e for e in result["reports"][0].log if e.host == "web1"] == []


async def test_dry_preview_mutates_nothing(canned_fetch):
    """``dry=True`` stops after prepare, so it must not touch the target.

    The prepare phase does fetch both bodies control-side (it must read them
    to plan the sha1 comparison / the key import), but no host store may
    change.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["web1"]

    result = await migrate(inventory, hosts, apply=False)
    assert result["reports"] == []
    assert result["plans"]["web1"]["repo_exists"] is False
    assert canned_fetch  # the control-side fetches happened during prepare
    assert host.remote_files == {}
    assert host.existing_files == set()
    assert host.rpm_packages == {}
    assert host.rpm_gpg_keys == []


# --- the keyid gate (issue #25) ------------------------------------------------


async def test_keyid_gate_skips_an_already_imported_key(canned_fetch):
    """Repo file absent but the key already imported: the gate no-ops the
    import (Ansible's rpm_key state=present idempotence) while the RPM
    install still runs."""
    inventory = make_inventory()
    hosts = make_hosts(inventory, key_imported=True)
    result = await migrate(inventory, hosts)

    key = entry_named(result, "Import EPEL GPG key.")
    assert not key.changed
    assert key.reason == f"GPG key {KEYID} already in the rpm keyring"
    assert entry_named(result, "Install the EPEL release RPM.").changed
    assert hosts["web1"].rpm_packages["epel-release"] == RPM_INFO["version"]

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


# --- the enabled toggle (issue #24) --------------------------------------------


async def test_disable_flag_rewrites_only_the_epel_section(canned_fetch):
    inventory = make_inventory(epel_repo_disable=True)
    hosts = make_hosts(inventory, repo_present=True, key_imported=True)
    result = await migrate(inventory, hosts)

    toggle = entry_named(result, "Ensure EPEL repo is enabled / disabled.")
    assert toggle.changed
    content = hosts["web1"].remote_files[REPO_FILE].decode("utf-8")
    # Exactly the [epel] enabled line flipped; the debuginfo/source sections,
    # their enabled=0 lines, every comment and the metalink/gpgkey/countme
    # options are byte-identical.
    assert content == EPEL_REPO.replace("enabled=1\n", "enabled=0\n", 1)
    assert content.count("enabled=0") == 3

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


async def test_enabled_drift_converges_once(canned_fetch):
    inventory = make_inventory()
    drifted = EPEL_REPO.replace("enabled=1\n", "enabled=0\n", 1)
    hosts = make_hosts(
        inventory, repo_present=True, key_imported=True, repo_text=drifted
    )
    result = await migrate(inventory, hosts)

    assert entry_named(result, "Ensure EPEL repo is enabled / disabled.").changed
    assert hosts["web1"].remote_files[REPO_FILE] == EPEL_REPO.encode("utf-8")

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


async def test_mode_drift_converges_via_the_file_task(canned_fetch):
    """Content equal but mode drifted: ini_option no-ops (content-only
    idempotence), the companion files.file chmods -- the role's ini_file
    mode=0644 re-applied every run."""
    inventory = make_inventory()
    hosts = make_hosts(inventory, repo_present=True, key_imported=True, repo_mode=600)
    result = await migrate(inventory, hosts)

    assert not entry_named(result, "Ensure EPEL repo is enabled / disabled.").changed
    assert entry_named(result, "Ensure EPEL repo file mode is 0644.").changed
    assert hosts["web1"].file_stats[REPO_FILE]["mode"] == 644

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


# --- fail-fast gates ------------------------------------------------------------


async def test_unsupported_major_fails_fast(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["web1"].linux_distribution = {**CENTOS_DISTRO, "major": 6}
    with pytest.raises(ValueError, match="no pinned EPEL key id"):
        await migrate(inventory, hosts)
    assert canned_fetch == []


# --- the pure helpers, unit-tested directly -------------------------------------


def test_resolved_vars_defaults():
    host = SimulatedHost.from_inventory(make_inventory()[0])
    rv = epel.resolved_vars(epel.role_settings(host), MAJOR)
    assert rv["repofile"] == REPO_FILE
    assert rv["enabled"] == "1"
    assert rv["rpm_url"] == RPM_URL
    assert rv["key_url"] == KEY_URL
    assert rv["keyid"] == KEYID


def test_resolved_vars_disable_and_overrides():
    host = SimulatedHost.from_inventory(
        make_inventory(
            epel_repo_disable="yes",
            epel_repo_url="https://mirror.example/epel-release.rpm",
            epel_repo_gpg_key_url="https://mirror.example/RPM-GPG-KEY-EPEL",
        )[0]
    )
    rv = epel.resolved_vars(epel.role_settings(host), MAJOR)
    assert rv["enabled"] == "0"
    assert rv["rpm_url"] == "https://mirror.example/epel-release.rpm"
    assert rv["key_url"] == "https://mirror.example/RPM-GPG-KEY-EPEL"
    # The pinned id stays -- a custom key simply matches nothing and imports.
    assert rv["keyid"] == KEYID


def test_role_settings_rejects_bad_values():
    with pytest.raises(ValueError, match="epel_repo_disable"):
        epel.role_settings(
            SimulatedHost.from_inventory(make_inventory(epel_repo_disable="maybe")[0])
        )
    with pytest.raises(ValueError, match="epel_repofile_path"):
        epel.role_settings(
            SimulatedHost.from_inventory(
                make_inventory(epel_repofile_path="relative.repo")[0]
            )
        )
    with pytest.raises(ValueError, match="epel_repo_url"):
        epel.role_settings(
            SimulatedHost.from_inventory(make_inventory(epel_repo_url="")[0])
        )


def test_is_centos_reads_the_static_group():
    centos = SimulatedHost.from_inventory(make_inventory()[0])
    ubuntu = SimulatedHost.from_inventory(make_inventory()[1])
    assert epel.is_centos(centos)
    assert not epel.is_centos(ubuntu)