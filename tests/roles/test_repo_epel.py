# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``repo_epel`` role (§17.5).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, repo_epel(v), host_factory=...)``. There is no gather ->
snapshot -> factory harness any more and no ``importlib`` loader -- the barriers
inside the role do that work, and the driver runs them.

The load-bearing assertion is unchanged from the old example test: run the role,
then run it *again* against the same persistent hosts, and the second pass must
report no change anywhere. It gets stronger here -- the rerun re-derives every
barrier outcome through ``run_waves`` rather than reusing a frozen plan dict, so
convergence proves the barriers themselves are honest.

Two simulation limits are bridged explicitly, because the in-memory host models
no real subsystem (each is a *simulation* limit, not a library bug):

* ``rpm_files[SPOOL]`` seeds the staged RPM's name/version metadata -- the
  simulation parses no rpm binary, so ``install_rpm`` reads the seam instead of
  the downloaded bytes.
* the RPM *payload*: a real ``epel-release`` install writes
  ``/etc/yum.repos.d/epel.repo``; ``install_rpm`` only records the package. The
  ``rpm_payload`` fixture teaches it to lay the file down too, which is what the
  same wave's ``files.ini_option`` read (``create=False`` raises on an absent
  file) needs. Under the old two-deploy split this bridge sat between the
  deploys; with install and configure in one stream it has to happen at the
  moment the install command runs.
* ``import_rpm_key`` appends a content-hash stub keyid (no OpenPGP parsing); the
  keyid-gate test seeds ``rpm_gpg_keys`` with the real EPEL 9 short id instead,
  the shape a live host's ``gpg-pubkey-3228467c-613798eb`` reports.

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
from reemote.roles import EpelVars, repo_epel
from reemote.roles.repo_epel import EPEL_KEY_IDS, EpelPlan, resolve_epel
from reemote.rolevars import RoleVarsError, resolve_vars
from tests.simulated_host import SimulatedHost

MAJOR = 9
RPM_URL = (
    f"https://dl.fedoraproject.org/pub/epel/epel-release-latest-{MAJOR}.noarch.rpm"
)
KEY_URL = f"https://dl.fedoraproject.org/pub/epel/RPM-GPG-KEY-EPEL-{MAJOR}"
REPO_FILE = "/etc/yum.repos.d/epel.repo"
SPOOL = "/tmp/" + posixpath.basename(RPM_URL)
KEYID = EPEL_KEY_IDS[MAJOR]
RPM_INFO = {"name": "epel-release", "version": "9-11.el9"}

INSTALL_NAMES = (
    "Import EPEL GPG key.",
    "Stage the EPEL release RPM.",
    "Install the EPEL release RPM.",
    "Remove the staged EPEL release RPM.",
)
CONFIGURE_NAMES = (
    "Ensure EPEL repo is enabled / disabled.",
    "Ensure EPEL repo file mode is 0644.",
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
    """Model the file payload a real ``epel-release`` install lays down.

    ``install_rpm`` records the package but models no RPM content, so the repo
    file the package ships has to be planted at the moment the install command
    runs -- install and configure are one stream now, so configure's read of the
    repo file (``create=False`` raises on an absent file) happens in the same
    wave, right after. What the seeded RPM ships comes from the ``rpm_payload``
    attribute ``make_hosts`` sets, and an already-present file is left alone, so
    a seeded drift (a flipped ``enabled`` line, a 600 mode) stays visible to the
    run that converges it.
    """
    original = SimulatedHost.install_rpm

    def install_rpm(self: SimulatedHost, src: str) -> None:
        original(self, src)
        if self.rpm_files.get(src, {}).get("name") != "epel-release":
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

    The role variables ride on the entry's ``roles`` mapping under ``"epel"``,
    written with the model's bare field names, which is what
    ``resolve_vars(EpelVars, "epel", inventory)`` binds, so a test changes a
    variable exactly the way an operator does. With no overrides the
    role defaults apply (EPEL enabled, stock repofile path).
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
            "epel": {
            },
        },
    }
    entry["roles"]["epel"].update(overrides)
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
    repo_file: str = REPO_FILE,
    repo_present: bool = False,
    key_imported: bool = False,
    repo_text: str = EPEL_REPO,
    repo_mode: int = 644,
) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets.

    Only the declared-RedHat host gets the CentOS seeding: the Ubuntu entry keeps
    its stock simulated state, so a role that reached it would be caught by the
    untouched-store assertions. ``repo_file`` is where the shipped repo file
    lands -- it must match the role's ``repofile_path`` variable for a test to
    see a converged install -- and ``repo_present`` pre-installs it.
    """
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "RedHat":
            host.linux_distribution = dict(CENTOS_DISTRO)
            host.rpm_files = {SPOOL: dict(RPM_INFO)}
            host.rpm_payload = {repo_file: repo_text}
            if repo_present:
                _regular_file(host, repo_file, repo_text.encode("utf-8"), repo_mode)
                host.rpm_packages["epel-release"] = RPM_INFO["version"]
            if key_imported:
                host.rpm_gpg_keys.append(
                    {
                        "keyid": KEYID,
                        "release": "613798eb",
                        "summary": "Fedora (epel9) <epel@fedoraproject.org> public key",
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
    v = resolve_vars(EpelVars, "epel", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(
        inventory, repo_epel(v), host_factory=factory, dry=dry
    )
    return report


def changed_entries(report: Any) -> list[Any]:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def entry_named(report: Any, name: str, host: str = "web1") -> Any:
    """The one log entry with *name* on *host*."""
    return next(
        entry for entry in report.merged().log if entry.name == name and entry.host == host
    )


def host_records(report: Any, host: str) -> list[Any]:
    """Every record (request step or barrier) attributed to *host*."""
    return [record for record in report.records if record.host == host]


# --- the first pass really installs EPEL --------------------------------------


async def test_first_pass_installs_epel(canned_fetch, rpm_payload):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["web1"]

    # Every install request ran and reported a change; the RPM's own payload is
    # already converged, so the enabled toggle no-ops on it.
    for name in INSTALL_NAMES:
        assert entry_named(report, name).changed, name
    assert not entry_named(report, CONFIGURE_NAMES[0]).changed
    assert not entry_named(report, CONFIGURE_NAMES[1]).changed

    # Both bodies were fetched control-side, in this order: the key for the
    # import, the RPM for the staging download.
    assert canned_fetch == [KEY_URL, RPM_URL]
    assert host.rpm_gpg_keys  # import_rpm_key appended its stub entry
    assert host.rpm_packages["epel-release"] == RPM_INFO["version"]
    assert host.remote_files[REPO_FILE] == EPEL_REPO.encode("utf-8")
    # The cleanup request removed the spool the download staged.
    assert SPOOL not in host.remote_files


async def test_one_stream_replaces_the_two_deploy_split(canned_fetch, rpm_payload):
    """Install and configure share one pass: the barriers, then six requests.

    Three barrier waves (``LinuxDistribution``, ``FileExists``, the control
    fetch) then one request wave carrying all six of the role's tasks in order.
    The old example needed two ``run()`` calls and a payload bridge between
    them; here the driver applies ``dnf.rpm`` before it pulls the ``ini_option``
    that reads the file that install just shipped.
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
    assert ordered == list(INSTALL_NAMES + CONFIGURE_NAMES)
    assert report.merged().log[0].host == "web1"


async def test_non_redhat_host_emits_nothing(canned_fetch, rpm_payload):
    """The family guard is the body's opening ``if``, not a ``when`` on each op.

    A host outside the declared family returns before it yields anything, so it
    records no request and no barrier -- where the old example logged each
    request as ``[skipped: when guard ...]``.
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


# --- the second pass is a no-op -----------------------------------------------


async def test_second_pass_changes_nothing(canned_fetch, rpm_payload):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    # The install guard skipped everything: no second key fetch, no RPM download.
    assert canned_fetch == [KEY_URL, RPM_URL]
    assert (
        entry_named(second, CONFIGURE_NAMES[0]).reason
        == f"content already up to date ({REPO_FILE})"
    )


async def test_converged_host_is_a_full_noop(canned_fetch, rpm_payload):
    """Repo file present and key imported: nothing fetched, nothing changed."""
    inventory = make_inventory()
    hosts = make_hosts(inventory, repo_present=True, key_imported=True)
    report = await run_role(inventory, hosts)

    assert changed_entries(report) == []
    assert canned_fetch == []
    # The install stage yielded nothing, so only configure's two entries run.
    assert [e.name for e in report.merged().log if e.host == "web1"] == list(
        CONFIGURE_NAMES
    )
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


# --- the keyid gate ------------------------------------------------------------


async def test_keyid_gate_skips_an_already_imported_key(canned_fetch, rpm_payload):
    """Repo file absent but the key already imported: the gate no-ops the import
    (Ansible's ``rpm_key state=present`` idempotence) while the RPM install still
    runs."""
    inventory = make_inventory()
    hosts = make_hosts(inventory, key_imported=True)
    report = await run_role(inventory, hosts)

    key = entry_named(report, INSTALL_NAMES[0])
    assert not key.changed
    assert key.reason == f"GPG key {KEYID} already in the rpm keyring"
    assert entry_named(report, INSTALL_NAMES[2]).changed
    assert hosts["web1"].rpm_packages["epel-release"] == RPM_INFO["version"]

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the enabled toggle ---------------------------------------------------------


async def test_disable_flag_rewrites_only_the_epel_section(canned_fetch, rpm_payload):
    inventory = make_inventory(repo_disable=True)
    hosts = make_hosts(inventory, repo_present=True, key_imported=True)
    report = await run_role(inventory, hosts)

    assert entry_named(report, CONFIGURE_NAMES[0]).changed
    content = hosts["web1"].remote_files[REPO_FILE].decode("utf-8")
    # Exactly the [epel] enabled line flipped; the debuginfo/source sections,
    # their enabled=0 lines, every comment and the metalink/gpgkey/countme
    # options are byte-identical.
    assert content == EPEL_REPO.replace("enabled=1\n", "enabled=0\n", 1)
    assert content.count("enabled=0") == 3

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_enabled_drift_converges_once(canned_fetch, rpm_payload):
    inventory = make_inventory()
    drifted = EPEL_REPO.replace("enabled=1\n", "enabled=0\n", 1)
    hosts = make_hosts(inventory, repo_present=True, key_imported=True, repo_text=drifted)
    report = await run_role(inventory, hosts)

    assert entry_named(report, CONFIGURE_NAMES[0]).changed
    assert hosts["web1"].remote_files[REPO_FILE] == EPEL_REPO.encode("utf-8")

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_mode_drift_converges_via_the_file_task(canned_fetch, rpm_payload):
    """Content equal but mode drifted: ini_option no-ops (content-only
    idempotence), the companion files.file chmods -- the role's ``ini_file
    mode=0644`` re-applied every run."""
    inventory = make_inventory()
    hosts = make_hosts(inventory, repo_present=True, key_imported=True, repo_mode=600)
    report = await run_role(inventory, hosts)

    assert not entry_named(report, CONFIGURE_NAMES[0]).changed
    assert entry_named(report, CONFIGURE_NAMES[1]).changed
    assert hosts["web1"].file_stats[REPO_FILE]["mode"] == 644

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_custom_repofile_path_is_used_verbatim(canned_fetch, rpm_payload):
    """``epel_repofile_path`` redirects the guard, the toggle and the chmod."""
    other = "/etc/yum.repos.d/epel-local.repo"
    inventory = make_inventory(repofile_path=other)
    hosts = make_hosts(inventory, repo_file=other, key_imported=True)
    report = await run_role(inventory, hosts)

    # The guard looked for the configured path, so the install stage ran even
    # though a file at the stock path would have suppressed nothing here.
    assert entry_named(report, INSTALL_NAMES[2]).changed
    assert hosts["web1"].remote_files[other] == EPEL_REPO.encode("utf-8")
    assert REPO_FILE not in hosts["web1"].remote_files
    # Both configure requests target that path, so both no-op on its payload.
    assert not entry_named(report, CONFIGURE_NAMES[0]).changed
    assert not entry_named(report, CONFIGURE_NAMES[1]).changed

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert [e.name for e in second.merged().log if e.host == "web1"] == list(
        CONFIGURE_NAMES
    )


# --- fail-fast gates ------------------------------------------------------------


async def test_unsupported_major_fails_fast(canned_fetch, rpm_payload):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["web1"].linux_distribution = {**CENTOS_DISTRO, "major": 6}

    with pytest.raises(ValueError, match="no pinned EPEL key id"):
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


# --- the pure half, unit-tested directly ----------------------------------------


def test_resolve_epel_defaults():
    plan = resolve_epel(EpelVars(), MAJOR)

    assert plan == EpelPlan(
        repofile=REPO_FILE,
        enabled="1",
        rpm_url=RPM_URL,
        key_url=KEY_URL,
        rpm_spool=SPOOL,
    )
    assert hash(plan)  # frozen and hashable, as a deploy body's local must be


def test_resolve_epel_disable_and_overrides():
    plan = resolve_epel(
        EpelVars(
            repo_disable=True,
            repo_url="https://mirror.example/epel-release.rpm",
            repo_gpg_key_url="https://mirror.example/RPM-GPG-KEY-EPEL",
        ),
        MAJOR,
    )

    assert plan.enabled == "0"
    assert plan.rpm_url == "https://mirror.example/epel-release.rpm"
    assert plan.key_url == "https://mirror.example/RPM-GPG-KEY-EPEL"
    # The spool still tracks the URL's basename.
    assert plan.rpm_spool == "/tmp/epel-release.rpm"


def test_inventory_binds_the_prefixed_variables():
    inventory = make_inventory(repo_disable="yes")
    v = resolve_vars(EpelVars, "epel", inventory)

    assert v["web1"].repo_disable is True
    # The other role's host keeps the defaults: no epel_ keys, nothing bound.
    assert v["ubuntu"] == EpelVars()


def test_vars_reject_bad_values():
    with pytest.raises(ValidationError, match="repo_disable"):
        EpelVars.model_validate({"repo_disable": "maybe"})
    with pytest.raises(ValidationError, match="repofile_path"):
        EpelVars(repofile_path="relative.repo")
    with pytest.raises(ValidationError, match="non-empty"):
        EpelVars(repo_url="")
    with pytest.raises(ValidationError, match="non-empty"):
        EpelVars(repo_gpg_key_url="")


def test_vars_are_frozen_and_reject_unknown_fields():
    v = EpelVars()
    with pytest.raises(ValidationError):
        v.repo_disable = True  # type: ignore[misc]
    with pytest.raises(ValidationError):
        EpelVars(enable_epel=True)  # the Ansible name, not the field


def test_typoed_role_variable_is_reported_not_ignored():
    inventory = make_inventory(repo_disble=True)
    hosts = make_hosts(inventory)

    with pytest.raises(RoleVarsError, match="repo_disble"):
        resolve_vars(EpelVars, "epel", inventory)
    assert hosts["web1"].remote_files == {}


# --- the wave tree (§7.2) -------------------------------------------------------

# Pinned from a real simulated run (``pytest tests/roles/test_repo_epel.py -k
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

mQINBFAKEepel9keymaterialnotreal0123456789
-----END PGP PUBLIC KEY BLOCK-----

  wave 3
    web1: Import EPEL GPG key.     [changed: import GPG key from <stream>]
        $ scp <stream> -> /tmp/reemote-dnf-key-1ded2fb6ffd2b7f2/key.gpg
        $ sudo sh -c 'rpm --import /tmp/reemote-dnf-key-1ded2fb6ffd2b7f2/key.gpg && rm -rf /tmp/reemote-dnf-key-1ded2fb6ffd2b7f2'
    web1: Stage the EPEL release RPM. [changed: content /tmp/epel-release-latest-9.noarch.rpm is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-8092ad521b8deba3/content
        $ sudo sh -c 'mkdir -p /tmp && cp /tmp/reemote-install-8092ad521b8deba3/content /tmp/epel-release-latest-9.noarch.rpm && chmod 644 /tmp/epel-release-latest-9.noarch.rpm && rm -rf /tmp/reemote-install-8092ad521b8deba3'
    web1: Install the EPEL release RPM. [changed: rpm /tmp/epel-release-latest-9.noarch.rpm not installed, will install]
        $ sudo rpm -i /tmp/epel-release-latest-9.noarch.rpm
    web1: Remove the staged EPEL release RPM. [changed: /tmp/epel-release-latest-9.noarch.rpm present, will remove]
        $ sudo rm -f /tmp/epel-release-latest-9.noarch.rpm
    web1: Ensure EPEL repo is enabled / disabled. [no change: content already up to date (/etc/yum.repos.d/epel.repo)]
    web1: Ensure EPEL repo file mode is 0644. [no change: file /etc/yum.repos.d/epel.repo already exists]"""

CONVERGED_TREE = """(root)
  wave 0
    barrier: LinuxDistribution → {'name': 'CentOS Stream', 'family': 'RedHat', 'major': 9, 'minor': None, 'release_meta': {}}
  wave 1
    barrier: FileExists → True
  wave 2
    web1: Ensure EPEL repo is enabled / disabled. [no change: content already up to date (/etc/yum.repos.d/epel.repo)]
    web1: Ensure EPEL repo file mode is 0644. [no change: file /etc/yum.repos.d/epel.repo already exists]"""


async def test_tree_snapshot(canned_fetch, rpm_payload):
    """The whole run as one tree: three barrier waves, then the requests (§7.2).

    The second tree is what a rerun prints -- two barrier waves, no control
    fetch, no changed line -- and it is the preview ``dry`` deliberately refuses
    to fake (§16.6).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)

    assert first.tree() == FIRST_PASS_TREE

    second = await run_role(inventory, hosts)
    assert second.tree() == CONVERGED_TREE
