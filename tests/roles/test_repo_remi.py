# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``repo_remi`` role (§17.5).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, repo_remi(v), host_factory=...)``. There is no gather ->
snapshot -> factory harness any more and no ``importlib`` loader -- the barriers
inside the role do that work, and the driver runs them.

The load-bearing assertion is the same as for every migrated role: run the
role, then run it *again* against the same persistent hosts, and the second
pass must report no change anywhere. Here it is even stronger than for EPEL --
the converged host yields **no request at all**, because this role has no
configure stage: the ``FileExists`` guard *is* the whole idempotence story.

Two simulation limits are bridged explicitly, because the in-memory host models
no real subsystem (each is a *simulation* limit, not a library bug):

* ``rpm_files[SPOOL]`` seeds the staged RPM's name/version metadata -- the
  simulation parses no rpm binary, so ``install_rpm`` reads the seam instead of
  the downloaded bytes.
* the RPM *payload*: a real ``remi-release`` install writes
  ``/etc/yum.repos.d/remi.repo``; ``install_rpm`` only records the package. The
  ``rpm_payload`` fixture teaches it to lay the file down too, which is what the
  *next pass's* ``FileExists`` guard needs to skip everything.
* ``import_rpm_key`` appends a content-hash stub keyid (no OpenPGP parsing); the
  keyid-gate test seeds ``rpm_gpg_keys`` with the real Remi EL9 short id, the
  shape a live host's ``gpg-pubkey-478f8947-5ff329c5`` reports.

The control-side fetches (the armored key via ``files.fetch_url``, the binary RPM
via ``files.download``) are answered by monkeypatching
``files._fetch_url_bytes`` -- the same seam the library's own download tests use.
The RPM body is deliberately non-UTF-8 so the staging download drives the binary
install branch.
"""

import posixpath
from typing import Any

import pytest
from pydantic import ValidationError

from reemote import run_waves
from reemote.modules.files import files as files_module
from reemote.roles import RemiVars, repo_remi
from reemote.roles.repo_remi import REMI_KEY_IDS, RemiPlan, resolve_remi
from reemote.rolevars import RoleVarsError, resolve_vars
from tests.simulated_host import SimulatedHost

MAJOR = 9
RPM_URL = f"https://rpms.remirepo.net/enterprise/remi-release-{MAJOR}.rpm"
KEY_URL = f"https://rpms.remirepo.net/enterprise/{MAJOR}/RPM-GPG-KEY-remi"
REPO_FILE = "/etc/yum.repos.d/remi.repo"
SPOOL = "/tmp/" + posixpath.basename(RPM_URL)
KEYID = REMI_KEY_IDS[MAJOR]
RPM_INFO = {"name": "remi-release", "version": "9.8-1.el9.remi"}

INSTALL_NAMES = (
    "Import remi GPG key.",
    "Stage the remi release RPM.",
    "Install the remi release RPM.",
    "Remove the staged remi release RPM.",
)

# The live CentOS Stream 9 host's distribution facts (the LinuxDistribution
# seam's shape): family RedHat at major 9 is what the role interpolates.
CENTOS_DISTRO = {
    "name": "CentOS Stream",
    "family": "RedHat",
    "major": MAJOR,
    "minor": None,
    "release_meta": {},
}

# An armored key body (text -- files.fetch_url UTF-8-decodes it) and a binary
# RPM body (never valid UTF-8 -- drives files.download's binary install
# branch). Both are opaque to the simulation.
FAKE_KEY = (
    "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\n"
    "mQINBFAKEremi9keymaterialnotreal0123456789\n"
    "-----END PGP PUBLIC KEY BLOCK-----\n"
)
FAKE_RPM = b"\xed\xab\xee\xdbremi-release-9.8-1.el9.remi.noarch\xff\xfe\x00"

# The RPM-shipped remi.repo, captured verbatim from the published
# remi-release-9.rpm: four sections, every one of them enabled=0 -- Remi is
# used per-transaction with --enablerepo=remi, which is exactly why this role
# (like the Ansible one) never edits the file.
REMI_REPO = """# Repository: https://rpms.remirepo.net/
# Blog:       https://blog.remirepo.net/
# Forum:      https://forum.remirepo.net/

[remi]
name=Remi's RPM repository for Enterprise Linux $releasever_major - $basearch
#baseurl=http://rpms.remirepo.net/enterprise/$releasever_major/remi/$basearch/
#mirrorlist=https://rpms.remirepo.net/enterprise/$releasever_major/remi/$basearch/httpsmirror
mirrorlist=http://cdn.remirepo.net/enterprise/$releasever_major/remi/$basearch/mirror
enabled=0
gpgcheck=1
# can be enabled if not behind a proxy because of possible cache issue
repo_gpgcheck=0
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-remi.el$releasever_major

[remi-test]
name=Remi's test RPM repository for Enterprise Linux $releasever_major - $basearch
#baseurl=http://rpms.remirepo.net/enterprise/$releasever_major/test/$basearch/
#mirrorlist=https://rpms.remirepo.net/enterprise/$releasever_major/test/$basearch/mirror
mirrorlist=http://cdn.remirepo.net/enterprise/$releasever_major/test/$basearch/mirror
# WARNING: If you enable this repository, you must also enable "remi"
enabled=0
gpgcheck=1
repo_gpgcheck=0
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-remi.el$releasever_major

[remi-debuginfo]
name=Remi's RPM repository for Enterprise Linux $releasever_major - $basearch - debuginfo
baseurl=http://rpms.remirepo.net/enterprise/$releasever_major/debug-remi/$basearch/
enabled=0
gpgcheck=1
repo_gpgcheck=0
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-remi.el$releasever_major

[remi-test-debuginfo]
name=Remi's test RPM repository for Enterprise Linux $releasever_major - $basearch - debuginfo
baseurl=http://rpms.remirepo.net/enterprise/$releasever_major/debug-test/$basearch/
enabled=0
gpgcheck=1
repo_gpgcheck=0
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-remi.el$releasever_major
"""


@pytest.fixture
def canned_fetch(monkeypatch):
    """Replace the control-side fetches with canned-body stubs.

    Both ``files.fetch_url`` (the armored key) and ``files.download`` (the
    binary RPM) look ``_fetch_url_bytes`` up as a module global at call time, so
    patching the module attribute redirects both. Returns the list of URLs the
    stub was called with, so a test can assert a converged host never fetches at
    all -- and, under ``run_waves``, that a dry run fetches nothing either.
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


@pytest.fixture
def rpm_payload(monkeypatch):
    """Model the file payload a real ``remi-release`` install lays down.

    ``install_rpm`` records the package but models no RPM content, so the repo
    file the package ships has to be planted at the moment the install command
    runs -- the *next pass's* ``FileExists`` guard reads it, and without this
    bridge a converged host would re-run the whole install chain forever. What
    the seeded RPM ships comes from the ``rpm_payload`` attribute ``make_hosts``
    sets, and an already-present file is left alone, so a seeded repo file stays
    visible to the run that observes it.
    """
    original = SimulatedHost.install_rpm

    def install_rpm(self: SimulatedHost, src: str) -> None:
        original(self, src)
        if self.rpm_files.get(src, {}).get("name") != "remi-release":
            return
        for path, text in getattr(self, "rpm_payload", {}).items():
            if path not in self.remote_files:
                _regular_file(self, path, text.encode("utf-8"), 644)

    monkeypatch.setattr(SimulatedHost, "install_rpm", install_rpm)


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


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one CentOS target, one Ubuntu host to be skipped.

    The role variables ride on the entry's ``roles`` mapping under ``"remi"``,
    written with the model's bare field names, which is what
    ``resolve_vars(RemiVars, "remi", inventory)`` binds, so a test changes a
    variable exactly the way an operator does. With no overrides the role
    defaults apply (the stock vendor URLs for the host's major version).
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
        "roles": {
            "remi": {
            },
        },
    }
    entry["roles"]["remi"].update(overrides)
    return [
        entry,
        {
            "host": "ubuntu",
            "family": "Ubuntu",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "ubuntu",
        },
    ]


def make_hosts(
    inventory: list[dict[str, Any]],
    *,
    repo_present: bool = False,
    key_imported: bool = False,
    rpm_installed: bool = False,
    staged_rpm: bool = True,
) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets.

    Only the declared-RedHat host gets the CentOS seeding: the Ubuntu entry keeps
    its stock simulated state, so a role that reached it would be caught by the
    untouched-store assertions. ``repo_present`` pre-installs the shipped repo
    file (the converged case), ``key_imported`` seeds the real Remi EL9 key
    entry, and ``rpm_installed``/``staged_rpm`` toggle the package database and
    the staged artifact behind the identity-hint path.
    """
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "RedHat":
            host.linux_distribution = dict(CENTOS_DISTRO)
            host.rpm_files = {SPOOL: dict(RPM_INFO)} if staged_rpm else {}
            host.rpm_payload = {REPO_FILE: REMI_REPO}
            if repo_present:
                _regular_file(host, REPO_FILE, REMI_REPO.encode("utf-8"), 644)
                host.rpm_packages["remi-release"] = RPM_INFO["version"]
            if rpm_installed and not repo_present:
                host.rpm_packages["remi-release"] = RPM_INFO["version"]
            if key_imported:
                host.rpm_gpg_keys.append(
                    {
                        "keyid": KEYID,
                        "release": "5ff329c5",
                        "summary": (
                            "Remi's RPM repository (https://rpms.remirepo.net/) "
                            "<dev@remirepo.fr> public key"
                        ),
                    }
                )
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
    *,
    dry: bool = False,
) -> Any:
    """Resolve the role's variables and run one wave pass against persistent hosts.

    The same ``Host`` objects come back for every entry, so state survives from
    one pass to the next -- which is the whole point of the second run.
    """
    v = resolve_vars(RemiVars, "remi", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(
        inventory, repo_remi(v), host_factory=factory, dry=dry
    )
    return report


def changed_entries(report: Any) -> list[Any]:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def entry_named(report: Any, name: str, host: str = "web1") -> Any:
    """The one log entry with *name* on *host*."""
    return next(
        entry
        for entry in report.merged().log
        if entry.name == name and entry.host == host
    )


def host_records(report: Any, host: str) -> list[Any]:
    """Every record (request step or barrier) attributed to *host*."""
    return [record for record in report.records if record.host == host]


# --- the first pass really installs Remi ---------------------------------------


async def test_first_pass_installs_remi(canned_fetch, rpm_payload):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["web1"]

    # Every request ran and reported a change -- this role is all install.
    for name in INSTALL_NAMES:
        assert entry_named(report, name).changed, name

    # Both bodies were fetched control-side, in this order: the key for the
    # import, the RPM for the staging download.
    assert canned_fetch == [KEY_URL, RPM_URL]
    assert host.rpm_gpg_keys  # import_rpm_key appended its stub entry
    assert host.rpm_packages["remi-release"] == RPM_INFO["version"]
    assert host.remote_files[REPO_FILE] == REMI_REPO.encode("utf-8")
    # The cleanup request removed the spool the download staged.
    assert SPOOL not in host.remote_files


async def test_barriers_then_one_request_wave(canned_fetch, rpm_payload):
    """The whole stream: three barrier waves, then one wave of four requests.

    ``LinuxDistribution``, ``FileExists`` and the control fetch each close a
    wave; the install tasks share the last one -- the driver applies each
    request before pulling the next, so the spool exists when ``dnf.rpm``
    reads it and the cleanup runs last.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    barriers = [
        record.barrier for record in host_records(report, "web1") if record.barrier
    ]
    assert len(barriers) == 3
    assert [type(b).__name__ for b in barriers] == [
        "FactRequest",
        "FactRequest",
        "ControlRequest",
    ]

    ordered = [entry.name for entry in report.merged().log if entry.host == "web1"]
    assert ordered == list(INSTALL_NAMES)


async def test_non_redhat_host_emits_nothing(canned_fetch, rpm_payload):
    """The family guard is the body's opening ``if``, not a ``when`` on each op.

    A host outside the declared family returns before it yields anything, so it
    records no request and no barrier -- Remi has nothing to install there.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    skipped = hosts["ubuntu"]
    assert host_records(report, "ubuntu") == []
    assert [e for e in report.merged().log if e.host == "ubuntu"] == []
    assert skipped.remote_files == {}
    assert skipped.rpm_packages == {}
    assert skipped.rpm_gpg_keys == []


# --- the second pass is a no-op -------------------------------------------------


async def test_second_pass_changes_nothing(canned_fetch, rpm_payload):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    # The guard skipped everything: no second key fetch, no RPM download...
    assert canned_fetch == [KEY_URL, RPM_URL]
    # ... and, unlike EPEL (whose ini toggle still runs), no request at all.
    assert [e.name for e in second.merged().log if e.host == "web1"] == []


async def test_converged_host_is_a_full_noop(canned_fetch, rpm_payload):
    """Repo file present and key imported: nothing fetched, nothing changed."""
    inventory = make_inventory()
    hosts = make_hosts(inventory, repo_present=True, key_imported=True)
    report = await run_role(inventory, hosts)

    assert changed_entries(report) == []
    assert canned_fetch == []
    # The install stage yielded nothing, and there is no configure stage.
    assert [e.name for e in report.merged().log if e.host == "web1"] == []
    # ... and the control barrier never fired: two barrier waves, not three.
    assert len([r for r in host_records(report, "web1") if r.barrier]) == 2


async def test_dry_preview_fetches_nothing_and_applies_nothing(canned_fetch, rpm_payload):
    """``dry`` is a whole-run preview that stops at the first barrier (§6.3).

    The role's first item is a fact, so the preview parks there: no barrier runs,
    nothing is fetched control-side and no request is applied. A converged rerun
    is the real preview.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["web1"]

    report = await run_role(inventory, hosts, dry=True)

    assert canned_fetch == []
    assert report.merged().log == []
    assert host.remote_files == {}
    assert host.existing_files == set()
    assert host.rpm_packages == {}
    assert host.rpm_gpg_keys == []
    assert "barrier: LinuxDistribution \u2192 unknown until run" in report.tree()


# --- the keyid gate --------------------------------------------------------------


async def test_keyid_gate_skips_an_already_imported_key(canned_fetch, rpm_payload):
    """Repo file absent but the key already imported: the gate no-ops the import
    (Ansible's ``rpm_key state=present`` idempotence) while the RPM install
    still runs."""
    inventory = make_inventory()
    hosts = make_hosts(inventory, key_imported=True)
    report = await run_role(inventory, hosts)

    key = entry_named(report, INSTALL_NAMES[0])
    assert not key.changed
    assert key.reason == f"GPG key {KEYID} already in the rpm keyring"
    assert entry_named(report, INSTALL_NAMES[2]).changed
    assert hosts["web1"].rpm_packages["remi-release"] == RPM_INFO["version"]

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the identity hint -----------------------------------------------------------


async def test_package_hint_converges_without_the_staged_rpm(canned_fetch, rpm_payload):
    """Installed database says Remi is there, but the staged file answers nothing
    (never downloaded here) and the repo file is missing: ``package=`` names the
    hint path (issue #55), so the install request no-ops against the rpm
    database instead of planning ``rpm -i`` on an absent path."""
    inventory = make_inventory()
    hosts = make_hosts(inventory, rpm_installed=True, staged_rpm=False)
    report = await run_role(inventory, hosts)

    install_entry = entry_named(report, INSTALL_NAMES[2])
    assert not install_entry.changed
    assert install_entry.reason == (
        f"rpm remi-release is installed (staged file {SPOOL} absent)"
    )
    # The guard still saw no repo file, so the chain ran: key import and the
    # (re-)staging download report their own changes.
    assert entry_named(report, INSTALL_NAMES[0]).changed


# --- fail-fast gates -------------------------------------------------------------


async def test_unsupported_major_fails_fast(canned_fetch, rpm_payload):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["web1"].linux_distribution = {**CENTOS_DISTRO, "major": 6}

    with pytest.raises(ValueError, match="no pinned Remi key id"):
        await run_role(inventory, hosts)

    assert canned_fetch == []
    assert hosts["web1"].remote_files == {}
    assert hosts["web1"].rpm_packages == {}


async def test_declared_family_mismatch_raises(canned_fetch, rpm_payload):
    """The second guard is the only proof that a declared family matches the
    machine: a RedHat inventory entry backed by a Debian distribution aborts
    before a single request."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["web1"].linux_distribution = {
        "name": "Ubuntu",
        "family": "Debian",
        "major": 24,
        "minor": 4,
        "release_meta": {},
    }

    with pytest.raises(ValueError, match="declared RedHat family"):
        await run_role(inventory, hosts)

    assert canned_fetch == []
    assert hosts["web1"].remote_files == {}


# --- the pure half, unit-tested directly ------------------------------------------


def test_resolve_remi_defaults():
    plan = resolve_remi(RemiVars(), MAJOR)

    assert plan == RemiPlan(
        rpm_url=RPM_URL,
        key_url=KEY_URL,
        rpm_spool=SPOOL,
    )
    assert hash(plan)  # frozen and hashable, as a deploy body's local must be


def test_resolve_remi_url_overrides():
    plan = resolve_remi(
        RemiVars(
            repo_url="https://mirror.example/remi-release.rpm",
            repo_gpg_key_url="https://mirror.example/RPM-GPG-KEY-remi",
        ),
        MAJOR,
    )

    assert plan.rpm_url == "https://mirror.example/remi-release.rpm"
    assert plan.key_url == "https://mirror.example/RPM-GPG-KEY-remi"
    # The spool still tracks the URL's basename.
    assert plan.rpm_spool == "/tmp/remi-release.rpm"


def test_inventory_binds_the_prefixed_variables():
    inventory = make_inventory(repo_url="https://mirror.example/remi-release.rpm")
    v = resolve_vars(RemiVars, "remi", inventory)

    assert v["web1"].repo_url == "https://mirror.example/remi-release.rpm"
    # The other role's host keeps the defaults: no remi_ keys, nothing bound.
    assert v["ubuntu"] == RemiVars()


def test_vars_reject_bad_values():
    with pytest.raises(ValidationError, match="non-empty"):
        RemiVars(repo_url="")
    with pytest.raises(ValidationError, match="non-empty"):
        RemiVars(repo_gpg_key_url="")


def test_vars_are_frozen_and_reject_unknown_fields():
    v = RemiVars()
    with pytest.raises(ValidationError):
        v.repo_url = "x"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        RemiVars(remi_repo_url="x")  # the Ansible name, not the field


def test_typoed_role_variable_is_reported_not_ignored():
    inventory = make_inventory(repo_urll="https://mirror.example/remi.rpm")
    hosts = make_hosts(inventory)

    with pytest.raises(RoleVarsError, match="repo_urll"):
        resolve_vars(RemiVars, "remi", inventory)
    assert hosts["web1"].remote_files == {}


# --- the wave tree (§7.2) ----------------------------------------------------------

# Pinned from a real simulated run (``pytest tests/roles/test_repo_remi.py -k
# tree`` prints the diff against it). The ``fetch_url`` line carries the
# barrier's raw ``str`` outcome, which is why it runs over several lines -- a
# rendering wrinkle of ``format_waves``, not of this role.
FIRST_PASS_TREE = """(root)
  wave 0
    barrier: LinuxDistribution → {'name': 'CentOS Stream', 'family': 'RedHat', 'major': 9, 'minor': None, 'release_meta': {}}
  wave 1
    barrier: FileExists → False
  wave 2
    barrier: fetch_url → -----BEGIN PGP PUBLIC KEY BLOCK-----

mQINBFAKEremi9keymaterialnotreal0123456789
-----END PGP PUBLIC KEY BLOCK-----

  wave 3
    web1: Import remi GPG key.     [changed: import GPG key from <stream>]
        $ scp <stream> -> /tmp/reemote-dnf-key-1ded2fb6ffd2b7f2/key.gpg
        $ sudo sh -c 'rpm --import /tmp/reemote-dnf-key-1ded2fb6ffd2b7f2/key.gpg && rm -rf /tmp/reemote-dnf-key-1ded2fb6ffd2b7f2'
    web1: Stage the remi release RPM. [changed: content /tmp/remi-release-9.rpm is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-c552a640c348b09b/content
        $ sudo sh -c 'mkdir -p /tmp && cp /tmp/reemote-install-c552a640c348b09b/content /tmp/remi-release-9.rpm && chmod 644 /tmp/remi-release-9.rpm && rm -rf /tmp/reemote-install-c552a640c348b09b'
    web1: Install the remi release RPM. [changed: rpm /tmp/remi-release-9.rpm not installed, will install]
        $ sudo rpm -i /tmp/remi-release-9.rpm
    web1: Remove the staged remi release RPM. [changed: /tmp/remi-release-9.rpm present, will remove]
        $ sudo rm -f /tmp/remi-release-9.rpm"""

CONVERGED_TREE = """(root)
  wave 0
    barrier: LinuxDistribution → {'name': 'CentOS Stream', 'family': 'RedHat', 'major': 9, 'minor': None, 'release_meta': {}}
  wave 1
    barrier: FileExists → True"""


async def test_tree_snapshot(canned_fetch, rpm_payload):
    """The whole run as one tree: three barrier waves, then the requests (§7.2).

    The second tree is what a rerun prints -- two barrier waves, no control
    fetch, no request wave at all -- and it is the preview ``dry`` deliberately
    refuses to fake (§16.6).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)

    assert first.tree() == FIRST_PASS_TREE

    second = await run_role(inventory, hosts)
    assert second.tree() == CONVERGED_TREE
