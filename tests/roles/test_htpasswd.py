# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``htpasswd`` role (§17.5).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, htpasswd(v), host_factory=...)``. The load-bearing
assertion is the migration's whole idempotence claim, stronger here than for a
content-only role: even though every fresh hash draws a random salt, the
second pass must report **no change anywhere**, because the ``FileContents``
read-back feeds a verify-then-keep merge -- a managed line whose stored hash
already verifies against the desired password is reproduced byte-for-byte.

The hashing itself lives in the operation the role used to work around, so
these tests exercise the role's *composition* of it (``merge_credentials``
over ``files.htpasswd_entry``) and only call ``files.htpasswd_hash`` /
``files.htpasswd_verify`` to state expectations; the primitives' own vectors
are pinned in ``tests/test_files_htpasswd.py``.

The seams the role consults are the ordinary files ones:

* ``apt_packages`` -- ``install_apt_packages`` records the package the way
  ``AptPackages`` reads it (the apache role test's precedent, no seeding
  needed).
* ``remote_files`` / ``existing_files`` / ``file_stats`` -- seeded files are
  laid down through the same helper the other file-role tests use. The
  converged seed is owned ``root:www-data`` at mode ``640``, which the login
  user cannot read, so the two-tier ``FileContents`` read (unprivileged cat,
  elevated re-read) is on the exercised path in every rerun test.

No monkeypatching is needed: the role contacts no URL and runs no action.

The tree snapshots at the bottom are **pinned from real simulated runs** (a
``-k tree`` failure prints the diff). The converged tree is deterministic
despite the random-salt hashing because its seeded hashes are fixed literals
whose passwords match, so no line is ever rewritten.
"""

import base64
import hashlib
from typing import Any

import pytest
from pydantic import ValidationError

from reemote import run_waves
from reemote.modules.files import HTPASSWD_SCHEMES, htpasswd_hash, htpasswd_verify
from reemote.roles import HtpasswdCredential, HtpasswdVars, htpasswd
from reemote.roles.htpasswd import SUPPORTED_SCHEMES, merge_credentials
from reemote.rolevars import RoleVarsError, resolve_vars
from tests.simulated_host import SimulatedHost

TARGET = "ubuntu"
OFF_TARGET = "web1"
HT_FILE = "/etc/apache2/.htpasswd"
SECOND_FILE = "/etc/nginx/.htpasswd"

ALICE = "alice"
PASS_ALICE = "alice-password-1"
BOB = "bob"
PASS_BOB = "bob-password-2"

# Fixed-hash literals for the two schemes, so a converged seed is a verbatim
# string. Generated with the operation's own helpers (salt ``Qux7Yh3z`` and a
# base64 SHA-1); the algorithms are pinned against openssl/perl vectors in
# tests/test_files_htpasswd.py below.
ALICE_HASH = "$apr1$Qux7Yh3z$Y0pHsg2yeq6DVprpMeCMb0"
BOB_HASH = "{SHA}/EjZqc5FO6inlw9PI4BynEAxq9I="
SEED_TEXT = f"{ALICE}:{ALICE_HASH}\n{BOB}:{BOB_HASH}\n"


def cred(**overrides: Any) -> dict[str, Any]:
    """One credential mapping, as the inventory's role mapping spells it."""
    base: dict[str, Any] = {
        "path": HT_FILE,
        "name": ALICE,
        "password": PASS_ALICE,
        "owner": "root",
        "group": "www-data",
    }
    base.update(overrides)
    return base


def bob_cred(**overrides: Any) -> dict[str, Any]:
    """The demo's second user, in the ``sha1`` (``{SHA}``) scheme."""
    return cred(
        name=BOB, password=PASS_BOB, hash_scheme="sha1", **overrides
    )


def make_inventory(credentials: list[dict[str, Any]] | None = None, **extra: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one RedHat host to be skipped.

    The role variables ride on the target entry's ``roles`` mapping under
    ``"htpasswd"``, written with the model's bare field names, which is what
    ``resolve_vars(HtpasswdVars, "htpasswd", inventory)`` binds. With no
    ``credentials`` the model default (the empty tuple) applies.
    """
    mapping: dict[str, Any] = {}
    if credentials is not None:
        mapping["credentials"] = credentials
    mapping.update(extra)
    entry: dict[str, Any] = {
        "host": TARGET,
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.11",
            "username": "supervisor",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "web",
        "roles": {"htpasswd": mapping},
    }
    return [
        entry,
        {
            "host": OFF_TARGET,
            "family": "RedHat",
            "connection": {"address": "192.0.2.10", "username": "supervisor"},
            "group": "centos",
        },
    ]


def seed_htpasswd_file(
    host: SimulatedHost,
    text: str,
    *,
    path: str = HT_FILE,
    mode: int = 640,
    user: str = "root",
    group: str = "www-data",
) -> None:
    """Lay an existing htpasswd file down: content, existence and stat row."""
    data = text.encode("utf-8")
    host.remote_files[path] = data
    host.existing_files.add(path)
    host.file_stats[path] = {
        "user": user,
        "group": group,
        "mode": mode,
        "size": len(data),
    }


def make_hosts(
    inventory: list[dict[str, Any]],
    *,
    seed: str | None = None,
    seed_mode: int = 640,
    seed_user: str = "root",
    seed_group: str = "www-data",
) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts.

    Only the declared-Ubuntu host is ever seeded or touched; the RedHat entry
    keeps its stock simulated state, so a role that reached it would be caught
    by the untouched-store assertions. ``seed`` is the existing file's text.
    """
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu" and seed is not None:
            seed_htpasswd_file(
                host,
                seed,
                mode=seed_mode,
                user=seed_user,
                group=seed_group,
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
    v = resolve_vars(HtpasswdVars, "htpasswd", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(
        inventory, htpasswd(v), host_factory=factory, dry=dry
    )
    return report


def changed_entries(report: Any) -> list[Any]:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def entry_named(report: Any, name: str, host: str = TARGET) -> Any:
    """The one log entry with *name* on *host*."""
    return next(
        entry for entry in report.merged().log if entry.name == name and entry.host == host
    )


def host_records(report: Any, host: str) -> list[Any]:
    """Every record (request step or barrier) attributed to *host*."""
    return [record for record in report.records if record.host == host]


PACKAGES_NAME = "Install the Apache utilities."


def manage_name(path: str = HT_FILE) -> str:
    return f"Manage htpasswd entries in {path}."


def owner_name(path: str = HT_FILE) -> str:
    return f"Ensure {path} ownership and mode."


def stored_hash(host: SimulatedHost, name: str, path: str = HT_FILE) -> str:
    """The stored hash field of one managed line of the simulated file."""
    for line in host.remote_files[path].decode("utf-8").splitlines():
        if line.startswith(f"{name}:"):
            return line.split(":", 1)[1]
    raise AssertionError(f"no line for {name} in {path}")


def verify_or_rehash(
    host: SimulatedHost, name: str, password: str, prefix: str
) -> bool:
    """Is *name*'s stored line in *prefix* and does it verify *password*?"""
    stored = stored_hash(host, name)
    return stored.startswith(prefix) and htpasswd_verify(password, stored)


# --- the pure hashing half, as the role composes it -------------------------------


def test_stored_forms_verify():
    """The verify-then-keep merge consults the operation's verifier."""
    assert htpasswd_verify(PASS_ALICE, ALICE_HASH)
    assert not htpasswd_verify("wrong", ALICE_HASH)
    assert htpasswd_verify(PASS_BOB, BOB_HASH)
    assert not htpasswd_verify("wrong", BOB_HASH)
    assert htpasswd_verify("clear-text", "clear-text")
    assert not htpasswd_verify("clear-text", "other")


def test_hash_shapes():
    apr = htpasswd_hash(PASS_ALICE, "apr_md5_crypt")
    assert apr.startswith("$apr1$") and htpasswd_verify(PASS_ALICE, apr)
    assert apr != htpasswd_hash(PASS_ALICE, "apr_md5_crypt")  # fresh salt
    for scheme in ("sha1", "apr_sha1"):
        sha = htpasswd_hash(PASS_BOB, scheme)
        assert sha == "{SHA}" + base64.b64encode(
            hashlib.sha1(PASS_BOB.encode("utf-8")).digest()
        ).decode("ascii")
    assert htpasswd_hash("x", "plain") == "x"


def test_merge_is_a_fixpoint_and_preserves_unmanaged_lines():
    creds = (
        HtpasswdCredential(**cred()),
        HtpasswdCredential(**bob_cred()),
    )
    unmanaged = "legacy:whatever-is-stored"
    current = f"# comment\n{unmanaged}\n{SEED_TEXT}"
    merged = merge_credentials(current, creds)
    # Managed lines verify, so they are byte-identical; the unmanaged text is
    # untouched; the result is a fixpoint.
    assert merged == current
    assert merge_credentials(merged, creds) == merged


def test_merge_replaces_only_the_stale_line():
    creds = (HtpasswdCredential(**cred()),)
    stale = f"{ALICE}:$apr1$zzzzzzzz$0000000000000000000000\nlegacy:keep-me\n"
    merged = merge_credentials(stale, creds)
    assert "legacy:keep-me" in merged
    new_hash = merged.splitlines()[0].split(":", 1)[1]
    assert new_hash != ALICE_HASH
    assert htpasswd_verify(PASS_ALICE, new_hash)


def test_merge_appends_new_names_and_last_duplicate_wins():
    creds = (
        HtpasswdCredential(**cred()),
        HtpasswdCredential(**cred(password="first")),
        HtpasswdCredential(**cred(password="second", hash_scheme="plain")),
    )
    merged = merge_credentials(None, creds)
    assert merged == f"{ALICE}:second\n"  # appended once, last credential wins


def test_merge_absent_file_and_newline_normalization():
    creds = (HtpasswdCredential(**cred()),)
    created = merge_credentials(None, creds)
    assert created.startswith(f"{ALICE}:$apr1$") and created.endswith("\n")
    # A file lacking the trailing newline normalizes once, then converges.
    once = merge_credentials(
        f"{ALICE}:second",
        (HtpasswdCredential(**cred(password="second", hash_scheme="plain")),),
    )
    assert once == f"{ALICE}:second\n"
    assert (
        merge_credentials(
            once, (HtpasswdCredential(**cred(password="second", hash_scheme="plain")),)
        )
        == once
    )


# --- the typed variables ----------------------------------------------------------


def test_credential_defaults():
    c = HtpasswdCredential(
        path=HT_FILE, name=ALICE, password=PASS_ALICE, owner="root", group="www-data"
    )
    assert c.mode == "0640"
    assert c.hash_scheme == "apr_md5_crypt"
    assert hash(c)  # frozen and hashable, as an op() argument's source must be


def test_credential_validation():
    with pytest.raises(ValidationError, match="absolute"):
        HtpasswdCredential(**cred(path="relative/.htpasswd"))
    with pytest.raises(ValidationError, match="colon"):
        HtpasswdCredential(**cred(name="bad:name"))
    with pytest.raises(ValidationError, match="octal"):
        HtpasswdCredential(**cred(mode="u+rw,g+r"))
    with pytest.raises(ValidationError, match="octal"):
        HtpasswdCredential(**cred(mode="0999"))


def test_the_widened_schemes_are_accepted():
    """D4 is closed: the role accepts the operation's whole scheme set (#74)."""
    assert SUPPORTED_SCHEMES == HTPASSWD_SCHEMES
    for scheme in ("bcrypt", "descrypt", "crypt", "sha1", "plaintext"):
        assert HtpasswdCredential(**cred(hash_scheme=scheme)).hash_scheme == scheme
    bcrypt = merge_credentials(
        None, (HtpasswdCredential(**cred(hash_scheme="bcrypt")),)
    )
    assert bcrypt.startswith(f"{ALICE}:$2y$")
    assert htpasswd_verify(PASS_ALICE, bcrypt.split(":", 1)[1].strip())
    des = merge_credentials(None, (HtpasswdCredential(**cred(hash_scheme="crypt")),))
    assert htpasswd_verify(PASS_ALICE, des.split(":", 1)[1].strip())


def test_unknown_scheme_is_rejected():
    with pytest.raises(ValidationError, match="unsupported hash_scheme"):
        HtpasswdCredential(**cred(hash_scheme="sha512_crypt"))
    assert "apr_md5_crypt" in SUPPORTED_SCHEMES


def test_vars_are_frozen_and_reject_unknown_fields():
    with pytest.raises(ValidationError):
        HtpasswdVars().nolog = False  # type: ignore[misc]
    with pytest.raises(ValidationError):
        HtpasswdVars(htpasswd_credentials=[])  # the Ansible name, not the field


def test_required_packages_accepts_a_bare_string():
    v = HtpasswdVars.model_validate({"required_packages": "apache2-utils"})
    assert v.required_packages == ("apache2-utils",)


def test_typoed_role_variable_is_reported_not_ignored():
    inventory = make_inventory([], nologgg=True)
    with pytest.raises(RoleVarsError, match="nologgg"):
        resolve_vars(HtpasswdVars, "htpasswd", inventory)


# --- the wave runs -----------------------------------------------------------------


async def test_first_pass_creates_the_file():
    inventory = make_inventory([cred(), bob_cred()])
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts[TARGET]

    assert entry_named(report, PACKAGES_NAME).changed
    assert entry_named(report, manage_name()).changed
    assert host.apt_packages["apache2-utils"] == [""]

    lines = host.remote_files[HT_FILE].decode("utf-8").splitlines()
    assert [line.split(":", 1)[0] for line in lines] == [ALICE, BOB]
    assert verify_or_rehash(host, ALICE, PASS_ALICE, "$apr1$")
    assert verify_or_rehash(host, BOB, PASS_BOB, "{SHA}")
    # The install's elevated chain applied owner, group and the octal mode.
    assert host.file_stats[HT_FILE]["mode"] == 640
    assert host.file_stats[HT_FILE]["user"] == "root"
    assert host.file_stats[HT_FILE]["group"] == "www-data"
    # The spool was cleaned up by the install chain itself.
    assert not [p for p in host.remote_files if p.startswith("/tmp/reemote-install")]

    second = await run_role(inventory, hosts)
    # The random salts did not churn: verify-then-keep reproduced every line.
    assert changed_entries(second) == []
    assert second.tree().count("barrier: FileContents") == 1


async def test_second_pass_after_a_drifted_hash_converges_once():
    inventory = make_inventory([cred(), bob_cred()])
    hosts = make_hosts(inventory)
    drifted = f"{ALICE}:$apr1$aaaaaaaa$bbbbbbbbbbbbbbbbbbbbbb\nlegacy:keep\n"
    seed_htpasswd_file(hosts[TARGET], drifted)
    report = await run_role(inventory, hosts)
    host = hosts[TARGET]

    assert entry_named(report, manage_name()).changed
    lines = host.remote_files[HT_FILE].decode("utf-8").splitlines()
    # alice rewritten (verifies now), the unmanaged line preserved in place,
    # bob appended.
    assert lines[1] == "legacy:keep"
    assert htpasswd_verify(PASS_ALICE, stored_hash(host, ALICE))
    assert htpasswd_verify(PASS_BOB, stored_hash(host, BOB))

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_metadata_drift_converges_via_the_file_task():
    """Content equal but mode and owner drifted: install no-ops (content-only
    idempotence), the companion files.file re-owns and chmods -- the Ansible
    task's per-run ``owner``/``group``/``mode`` re-applied."""
    inventory = make_inventory([cred(), bob_cred()])
    hosts = make_hosts(inventory, seed=SEED_TEXT, seed_mode=600, seed_user="daemon")
    report = await run_role(inventory, hosts)
    host = hosts[TARGET]

    assert not entry_named(report, manage_name()).changed
    assert entry_named(report, owner_name()).changed
    assert host.file_stats[HT_FILE]["mode"] == 640
    assert host.file_stats[HT_FILE]["user"] == "root"

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_multiple_paths_get_one_barrier_each():
    inventory = make_inventory(
        [cred(), cred(path=SECOND_FILE, name="carol", password="carol-pass")]
    )
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts[TARGET]

    barriers = [
        r.barrier
        for r in host_records(report, TARGET)
        if r.barrier and type(r.barrier).__name__ == "FactRequest"
    ]
    assert len(barriers) == 2
    assert entry_named(report, manage_name(HT_FILE)).changed
    assert entry_named(report, manage_name(SECOND_FILE)).changed
    assert host.remote_files[HT_FILE].decode("utf-8").startswith(f"{ALICE}:")
    assert host.remote_files[SECOND_FILE].decode("utf-8").startswith("carol:")

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_no_credentials_still_installs_packages():
    """The role's ``package:`` task runs unconditionally, like Ansible's."""
    inventory = make_inventory([])
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert entry_named(report, PACKAGES_NAME).changed
    assert [e.name for e in report.merged().log if e.host == TARGET] == [PACKAGES_NAME]
    # No credentials, so no barrier at all.
    assert [r for r in host_records(report, TARGET) if r.barrier] == []

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_off_family_host_emits_nothing():
    """The family guard is the body's opening ``if``, not a ``when`` per op.

    A host outside the declared family returns before it yields anything, so
    it records no request and no barrier -- and its stores stay untouched.
    """
    inventory = make_inventory([cred()])
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    skipped = hosts[OFF_TARGET]
    assert host_records(report, OFF_TARGET) == []
    assert [e for e in report.merged().log if e.host == OFF_TARGET] == []
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}


async def test_dry_preview_reads_nothing_and_applies_nothing():
    """``dry`` is a whole-run preview that stops at the first barrier (§6.3).

    The package request precedes the first ``FileContents`` barrier, so the
    preview parks there: no file is read, no request is applied, no state
    moves. A converged rerun is the honest preview.
    """
    inventory = make_inventory([cred()])
    hosts = make_hosts(inventory)
    host = hosts[TARGET]

    report = await run_role(inventory, hosts, dry=True)

    assert host.apt_packages == {}
    assert host.remote_files == {}
    assert HT_FILE not in host.existing_files
    assert "barrier: FileContents \u2192 unknown until run" in report.tree()


async def test_secrets_never_render_in_request_lines():
    """D5: the composed file rides the spool descriptor, so no request line
    and no command text contains a password or a hash."""
    inventory = make_inventory([cred(), cred(name="plainuser", hash_scheme="plain", password="plainword")])
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    request_lines = [
        line
        for entry in report.merged().log
        for line in getattr(entry, "commands", []) or []
    ]
    joined = "\n".join(str(item) for item in request_lines)
    assert PASS_ALICE not in joined
    assert "plainword" not in joined


# --- the wave tree (§7.2) ----------------------------------------------------------

# Pinned from a real simulated run (``uv run pytest tests/roles/test_htpasswd.py
# -k tree`` prints the diff against them). The first tree is a fresh host: the
# package request shares wave 0 with the barrier that closes it, the file does
# not exist yet (``None``), and the write pair lands in wave 1.
FIRST_PASS_TREE = """(root)
  wave 0
    ubuntu: Install the Apache utilities. [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install apache2-utils
    barrier: FileContents → None
  wave 1
    ubuntu: Manage htpasswd entries in /etc/apache2/.htpasswd. [changed: content /etc/apache2/.htpasswd is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-43de96e5cafcfdcb/content
        $ sudo sh -c 'mkdir -p /etc/apache2 && cp /tmp/reemote-install-43de96e5cafcfdcb/content /etc/apache2/.htpasswd && chown root:www-data /etc/apache2/.htpasswd && chmod 640 /etc/apache2/.htpasswd && rm -rf /tmp/reemote-install-43de96e5cafcfdcb'
    ubuntu: Ensure /etc/apache2/.htpasswd ownership and mode. [no change: file /etc/apache2/.htpasswd already exists]"""

# The converged host's tree: the FileContents barrier echoes the seeded text
# (the one visible surface, D5), the package request still installs on the
# fresh host, and the write pair no-ops -- no line changed despite the random
# salts, which is the migration's idempotence claim in one tree.
CONVERGED_TREE = """(root)
  wave 0
    ubuntu: Install the Apache utilities. [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install apache2-utils
    barrier: FileContents → alice:$apr1$Qux7Yh3z$Y0pHsg2yeq6DVprpMeCMb0
bob:{SHA}/EjZqc5FO6inlw9PI4BynEAxq9I=

  wave 1
    ubuntu: Manage htpasswd entries in /etc/apache2/.htpasswd. [no change: content already up to date (/etc/apache2/.htpasswd)]
    ubuntu: Ensure /etc/apache2/.htpasswd ownership and mode. [no change: file /etc/apache2/.htpasswd already exists]"""


async def test_tree_snapshot():
    inventory = make_inventory([cred(), bob_cred()])
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)

    assert first.tree() == FIRST_PASS_TREE

    rerun_inventory = make_inventory([cred(), bob_cred()])
    rerun_hosts = make_hosts(rerun_inventory, seed=SEED_TEXT)
    converged = await run_role(rerun_inventory, rerun_hosts)
    assert converged.tree() == CONVERGED_TREE
