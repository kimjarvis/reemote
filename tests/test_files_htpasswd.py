# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for ``files.htpasswd``: the htpasswd entry operation (issue #74).

Ansible's ``community.general.htpasswd`` mirrored: manage one ``name:hash``
line of an Apache/Nginx htpasswd file. Three things are tested here, in the
order the operation does them.

* The **hashes**. Every scheme is pure Python, control-side, and each one is
  pinned against a reference implementation's real output: ``openssl passwd``
  for ``$apr1$``/``$1$``, ``perl``'s ``crypt()`` for traditional DES, the
  OpenBSD ``crypt_blowfish`` output (as the C ``bcrypt`` package reports it)
  for ``$2y$``, and the documented ``{SHA}`` vector. These are the vectors the
  implementations were developed against, not self-consistency checks.
* The **entry edit** (``htpasswd_entry``), which is where idempotence lives.
  A fresh hash draws a random salt every time, so the file can only converge if
  the edit *verifies* the stored hash and keeps a proving line byte-for-byte.
  Every edit test therefore asserts the fixpoint as well as the first result.
* The **operation**: ``FileContents`` read -> pure edit -> ``files.install``
  delegation, so the write spools over unprivileged scp and installs elevated,
  and a converged file yields zero commands. The end-to-end tests additionally
  pin that neither the password nor a hash ever reaches a plan or log line.
"""

from collections.abc import Generator

import pytest

from reemote.core import FactCache, OpContext, OperationRequest, op
from reemote.drivers import command_shell, format_log, format_plan, run
from reemote.hosts import Host
from reemote.modules import files
from reemote.modules.files.files import (
    HTPASSWD_SCHEMES,
    FileContents,
    InstallCommand,
    InstallSpoolCommand,
    InstallValidateCommand,
    _bcrypt_crypt,
    _des_crypt,
    _install_spool_path,
    _md5_crypt,
)
from tests.simulated_host import SimulatedHost

INVENTORY = [
    {
        "host": "web1",
        "family": "Ubuntu",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
]

HT_FILE = "/etc/apache2/.htpasswd"
SPOOL = _install_spool_path(HT_FILE)
SPOOL_DIR = SPOOL.rsplit("/", 1)[0]

ALICE = "alice"
PASS_ALICE = "alice-password-1"
BOB = "bob"
PASS_BOB = "bob-password-2"


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell`` instead of connecting."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        return self._stdout


# --- the reference vectors ----------------------------------------------------

# ``openssl passwd -apr1 -salt <salt> <password>`` (and the same PHK algorithm
# under ``$1$``, which ``openssl passwd -1`` spells with the glibc magic).
APR1_VECTORS = [
    ("supersecure", "XIXoX/xx", "$apr1$XIXoX/xx$zJgp/zw7/8B/BDCoEVedw1"),
    ("password", "abcdefgh", "$apr1$abcdefgh$FBwExRW4dCc8aL.OvjpIE1"),
    ("", "XIXoX/xx", "$apr1$XIXoX/xx$oEI0DiN.nBCXg/1rK2IpP/"),
    (
        "a-very-long-password-for-the-16-byte-loop-test",
        "12345678",
        "$apr1$12345678$x0StuO92WWy2rML8oDRbw.",
    ),
]
MD5_VECTORS = [
    ("supersecure", "XIXoX/xx", "$1$XIXoX/xx$1CY1jJukH7ehKKii3ecU1/"),
    ("password", "abcdefgh", "$1$abcdefgh$G//4keteveJp0qb8z2DxG/"),
]
# ``perl -e 'print crypt($ARGV[0], $ARGV[1])' <password> <salt>`` -- the system's
# own BSD des_crypt, including the 25-pass key schedule and the salt's bit
# exchanges inside the Feistel expansion.
DES_VECTORS = [
    ("password", "ab", "abJnggxhB/yWI"),
    ("hello world", "zz", "zzkintsAxIFa."),
    ("test", "./", "./H7.I.sVn7zo"),
    ("a", "Q7", "Q7LE7XVdzVS6c"),
    ("secret123", "xk", "xkFxBiOnudVes"),
    ("G3huhZtl42", "Pg", "PgphV21oboJnw"),
    ("KFTzjSkMlX5B", "vv", "vvLXSndtYlCRI"),
]
# ``bcrypt.hashpw(password, "$2b$<cost>$<salt>")`` from the reference
# OpenBSD implementation; the salt here is the all-``A`` one so the vectors stay
# readable, and the ``$2y$`` spelling of the same digest verifies too.
BCRYPT_VECTORS = [
    ("s3cr3t", 5, "$2b$05$AAAAAAAAAAAAAAAAAAAAA.hlJRVQBvqg5bt72oV44yYFsyhihiRB."),
    (
        "a-much-longer-demo-password",
        4,
        "$2b$04$AAAAAAAAAAAAAAAAAAAAA.LLQEE90uLWGZPURGN7TEsE7dhP.uN7y",
    ),
    ("", 4, "$2b$04$AAAAAAAAAAAAAAAAAAAAA.lvvkzzqrMPdnab8Xxl8zf7j6C1s84c6"),
]
# The published ``{SHA}`` vector for ``sha1("password")``.
SHA1_VECTOR = "{SHA}W6ph5Mm5Pz8GgiULbPgzG37mj9g="


def test_apr_md5_crypt_matches_openssl():
    for password, salt, expected in APR1_VECTORS:
        assert _md5_crypt(password, salt, "$apr1$") == expected
        assert files.htpasswd_verify(password, expected)
        assert not files.htpasswd_verify(f"wrong-{password}", expected)


def test_md5_crypt_matches_openssl():
    for password, salt, expected in MD5_VECTORS:
        assert _md5_crypt(password, salt, "$1$") == expected
        assert files.htpasswd_verify(password, expected)


def test_descrypt_matches_perl_crypt():
    for password, salt, expected in DES_VECTORS:
        assert _des_crypt(password, salt) == expected
        assert files.htpasswd_verify(password, expected)
        assert not files.htpasswd_verify(f"wrong-{password}", expected)


def test_bcrypt_matches_crypt_blowfish():
    for password, cost, expected in BCRYPT_VECTORS:
        salt = expected[7:29]
        assert _bcrypt_crypt(password, salt, cost, "2b") == expected
        # Apache/passlib emit the ``$2y$`` spelling of the same digest.
        as_2y = f"$2y${expected[4:]}"
        assert files.htpasswd_verify(password, as_2y)
        assert not files.htpasswd_verify(f"wrong-{password}", as_2y)


def test_sha1_matches_the_published_vector():
    assert files.htpasswd_hash("password", "apr_sha1") == SHA1_VECTOR
    assert files.htpasswd_verify("password", SHA1_VECTOR)
    assert not files.htpasswd_verify("passwordx", SHA1_VECTOR)


def test_plain_stores_the_password_verbatim():
    assert files.htpasswd_hash("hunter2", "plain") == "hunter2"
    assert files.htpasswd_verify("hunter2", "hunter2")


@pytest.mark.parametrize(
    ("spelling", "canonical"),
    [
        ("plain", "plain"),
        ("Plaintext", "plain"),
        ("apr_md5_crypt", "apr_md5_crypt"),
        ("apache_md5", "apr_md5_crypt"),
        ("apr1", "apr_md5_crypt"),
        ("md5", "md5_crypt"),
        ("md5_crypt", "md5_crypt"),
        ("sha1", "apr_sha1"),
        ("LDAP_SHA1", "apr_sha1"),
        (" bcrypt ", "bcrypt"),
        ("crypt", "descrypt"),
        ("des", "descrypt"),
        ("des_crypt", "descrypt"),
        ("descrypt", "descrypt"),
    ],
)
def test_ansible_scheme_spellings_are_accepted(spelling, canonical):
    """Every name Ansible's module documents reaches a canonical scheme."""
    assert files.htpasswd_scheme(spelling) == canonical
    assert canonical in HTPASSWD_SCHEMES


@pytest.mark.parametrize("spelling", ["sha512_crypt", "phpass", "", "bcrypt-2a"])
def test_unimplemented_scheme_raises(spelling):
    """passlib-only handlers say so, naming what *is* implemented."""
    with pytest.raises(ValueError, match="unsupported hash_scheme"):
        files.htpasswd_scheme(spelling)


@pytest.mark.parametrize("scheme", HTPASSWD_SCHEMES)
@pytest.mark.parametrize("password", ["hunter2", ""])
def test_every_scheme_round_trips(scheme, password):
    hashed = files.htpasswd_hash(password, scheme)
    assert files.htpasswd_verify(password, hashed)
    assert not files.htpasswd_verify(f"wrong-{password}", hashed)
    if scheme not in ("plain", "apr_sha1") and password:
        # ``{SHA}`` is unsalted by design (and ``plain`` stores the password),
        # but every salted scheme draws afresh -- which is why the entry edit
        # verifies instead of comparing text.
        assert hashed != files.htpasswd_hash(password, scheme)


def test_a_plaintext_that_looks_like_a_des_hash_does_not_churn():
    """A 13-character h64 password is also a valid DES hash *shape*.

    Checking plain equality first (passlib's ``plaintext`` handler does the
    same) keeps such an entry from being re-hashed -- and the file from never
    converging -- when the caller asked for the ``plain`` scheme.
    """
    password = "abJnggxhB/yWI"
    once = files.htpasswd_entry(None, ALICE, password, "plain")
    assert once == f"{ALICE}:{password}\n"
    assert files.htpasswd_entry(once, ALICE, password, "plain") == once


def test_descrypt_only_uses_the_first_eight_characters():
    """The format's own limit, so the verifier must agree with the hasher."""
    hashed = files.htpasswd_hash("0123456789", "descrypt")
    assert files.htpasswd_verify("01234567", hashed)


# --- the pure entry edit ------------------------------------------------------


def test_entry_creates_the_file_when_absent():
    line = files.htpasswd_entry(None, ALICE, PASS_ALICE)
    assert line.startswith(f"{ALICE}:$apr1$") and line.endswith("\n")
    assert files.htpasswd_verify(PASS_ALICE, line.split(":", 1)[1].strip())


def test_entry_keeps_a_verifying_line_byte_for_byte():
    """The idempotence claim: the random salt survives a rerun."""
    seed = f"{ALICE}:$apr1$Qux7Yh3z$Y0pHsg2yeq6DVprpMeCMb0\n"
    assert files.htpasswd_entry(seed, ALICE, PASS_ALICE) == seed


def test_entry_rewrites_only_a_stale_line_and_preserves_the_rest():
    current = f"# comment\n{ALICE}:$apr1$aaaaaaaa$bbbbbbbbbbbbbbbbbbbbbb\nlegacy:keep-me\n"
    merged = files.htpasswd_entry(current, ALICE, PASS_ALICE)
    lines = merged.splitlines()
    assert lines[0] == "# comment"
    assert lines[2] == "legacy:keep-me"
    assert not lines[1].startswith(f"{ALICE}:$apr1$aaaaaaaa$")
    assert files.htpasswd_verify(PASS_ALICE, lines[1].split(":", 1)[1])
    assert files.htpasswd_entry(merged, ALICE, PASS_ALICE) == merged


def test_entry_appends_a_new_user_in_order():
    current = f"{ALICE}:{PASS_ALICE}\n"
    merged = files.htpasswd_entry(current, BOB, PASS_BOB, "plain")
    assert merged == f"{ALICE}:{PASS_ALICE}\n{BOB}:{PASS_BOB}\n"


def test_entry_normalizes_the_trailing_newline():
    once = files.htpasswd_entry(f"{ALICE}:x", ALICE, "x", "plain")
    assert once == f"{ALICE}:x\n"
    assert files.htpasswd_entry(once, ALICE, "x", "plain") == once


def test_entry_removes_only_its_own_line():
    current = f"{ALICE}:{PASS_ALICE}\n{BOB}:{PASS_BOB}\n"
    merged = files.htpasswd_entry(current, ALICE, present=False)
    assert merged == f"{BOB}:{PASS_BOB}\n"
    # Removing an absent entry is a no-op, and needs no password.
    assert files.htpasswd_entry(merged, ALICE, present=False) == merged


@pytest.mark.parametrize("username", ["", "bad:name"])
def test_entry_rejects_an_unusable_username(username):
    with pytest.raises(ValueError, match="username"):
        files.htpasswd_entry(None, username, PASS_ALICE)


def test_entry_requires_a_password_to_present_one():
    with pytest.raises(ValueError, match="password is required"):
        files.htpasswd_entry(None, ALICE)


def test_entry_rejects_an_unknown_scheme():
    with pytest.raises(ValueError, match="unsupported hash_scheme"):
        files.htpasswd_entry(None, ALICE, PASS_ALICE, "sha512_crypt")


def test_entry_defers_the_hashing_until_it_is_needed(monkeypatch):
    """A converged file costs one verification, not a bcrypt run."""

    def no_hashing(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("htpasswd_hash must not run for a verifying line")

    seed = f"{ALICE}:$apr1$Qux7Yh3z$Y0pHsg2yeq6DVprpMeCMb0\n"
    monkeypatch.setattr(files.files, "htpasswd_hash", no_hashing)
    assert files.htpasswd_entry(seed, ALICE, PASS_ALICE, "bcrypt") == seed


# --- the operation: read, edit, delegate to install ---------------------------


async def test_htpasswd_creates_an_absent_file():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.htpasswd(
            ctx, HT_FILE, ALICE, PASS_ALICE, owner="root", group="www-data"
        )
    ]

    assert ctx.changed
    assert ctx.reason == f"content {HT_FILE} is absent, will install"
    spool_cmd, install_cmd = commands
    assert isinstance(spool_cmd, InstallSpoolCommand)
    assert spool_cmd.content.startswith(f"{ALICE}:$apr1$")
    assert spool_cmd.dest == SPOOL
    assert isinstance(install_cmd, InstallCommand)
    assert install_cmd.dest == HT_FILE
    # The default mode is the restrictive one, not install's 0644 (issue #74).
    assert install_cmd.mode == 640
    assert install_cmd.user == "root"
    assert install_cmd.group == "www-data"


async def test_htpasswd_is_a_no_op_when_the_line_verifies():
    """Even with a random-salt scheme requested, nothing is written."""
    seed = f"{ALICE}:$apr1$Qux7Yh3z$Y0pHsg2yeq6DVprpMeCMb0\n"
    host = SimulatedHost(name="h", remote_files={HT_FILE: seed.encode()})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.htpasswd(
            ctx, HT_FILE, ALICE, PASS_ALICE, hash_scheme="bcrypt"
        )
    ]

    assert not ctx.changed
    assert ctx.reason == f"content already up to date ({HT_FILE})"
    assert commands == []


async def test_htpasswd_rewrites_only_the_stale_entry_of_a_shared_file():
    current = f"{BOB}:{PASS_BOB}\nlegacy:keep-me\n"
    host = SimulatedHost(name="h", remote_files={HT_FILE: current.encode()})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.htpasswd(
            ctx, HT_FILE, ALICE, PASS_ALICE, hash_scheme="plain"
        )
    ]

    assert ctx.changed
    spool_cmd, _ = commands
    assert spool_cmd.content == f"{BOB}:{PASS_BOB}\nlegacy:keep-me\n{ALICE}:{PASS_ALICE}\n"


async def test_htpasswd_removes_an_entry():
    current = f"{ALICE}:{PASS_ALICE}\n{BOB}:{PASS_BOB}\n"
    host = SimulatedHost(name="h", remote_files={HT_FILE: current.encode()})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.htpasswd(ctx, HT_FILE, ALICE, present=False)]

    assert ctx.changed
    spool_cmd, _ = commands
    assert spool_cmd.content == f"{BOB}:{PASS_BOB}\n"


async def test_htpasswd_on_an_absent_file_with_present_false_writes_nothing():
    """Creating an empty file to have removed a user from it is a change, not a
    convergence -- so the operation stops before the install."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.htpasswd(ctx, HT_FILE, ALICE, present=False)]

    assert not ctx.changed
    assert ctx.reason == f"htpasswd file {HT_FILE} is absent, nothing to remove"
    assert commands == []


async def test_htpasswd_forwards_validate():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.htpasswd(
            ctx, HT_FILE, ALICE, PASS_ALICE, validate="grep -q : {spool}"
        )
    ]

    assert [type(c) for c in commands] == [
        InstallSpoolCommand,
        InstallValidateCommand,
        InstallCommand,
    ]
    assert commands[1].validate == "grep -q : {spool}"


@pytest.mark.parametrize("mode", ["u+rw,g+r", "888", "0o640"])
async def test_htpasswd_surfaces_install_mode_validation(mode):
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="Invalid file mode"):
        [cmd async for cmd in files.htpasswd(ctx, HT_FILE, ALICE, PASS_ALICE, mode=mode)]


async def test_htpasswd_reads_a_login_unreadable_file_elevated():
    """The two-tier ``FileContents`` read is what makes a 0640 file converge."""
    seed = f"{ALICE}:$apr1$Qux7Yh3z$Y0pHsg2yeq6DVprpMeCMb0\n"
    host = SimulatedHost(name="h", remote_files={HT_FILE: seed.encode()})
    host.existing_files.add(HT_FILE)
    host.file_stats[HT_FILE] = {
        "user": "root",
        "group": "www-data",
        "mode": 640,
        "size": len(seed),
    }
    host.login_can_read = lambda path: path != HT_FILE
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.htpasswd(ctx, HT_FILE, ALICE, PASS_ALICE)]

    assert commands == []
    assert ctx.reason == f"content already up to date ({HT_FILE})"


async def test_htpasswd_reads_the_content_over_the_real_transport():
    """Without the simulated seam the edit runs on ``FileContents``' cat form."""
    host = CannedShellHost(f"{FileContents.MARKER}\n{BOB}:{PASS_BOB}\n")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.htpasswd(
            ctx, HT_FILE, ALICE, PASS_ALICE, hash_scheme="plain"
        )
    ]

    assert ctx.changed
    spool_cmd, _ = commands
    assert spool_cmd.content == f"{BOB}:{PASS_BOB}\n{ALICE}:{PASS_ALICE}\n"


# --- end to end through the driver --------------------------------------------


def htpasswd_deploy():
    """Two users in one file, then a removal: the apply phase chains them."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.htpasswd,
            name="Allow alice",
            path=HT_FILE,
            username=ALICE,
            password=PASS_ALICE,
            owner="root",
            group="www-data",
            sudo=True,
        )
        yield op(
            files.htpasswd,
            name="Allow bob",
            path=HT_FILE,
            username=BOB,
            password=PASS_BOB,
            hash_scheme="plain",
            owner="root",
            group="www-data",
            sudo=True,
        )
        yield op(
            files.htpasswd,
            name="Drop carol",
            path=HT_FILE,
            username="carol",
            present=False,
            sudo=True,
        )

    return deploy


async def test_htpasswd_end_to_end_plan_log_and_second_no_op():
    plans, report, hosts = await run(
        INVENTORY, htpasswd_deploy(), host_factory=SimulatedHost.from_inventory
    )

    # Prepare never sends, so all three plans see an absent file.
    assert [plan.changed for plan in plans["web1"]] == [True, True, False]
    assert "content /etc/apache2/.htpasswd is absent, will install" in format_plan(plans)

    assert report is not None
    entries = {entry.name: entry for entry in report.log}
    assert entries["Allow alice"].changed
    # The apply-phase re-read sees alice's line, so bob appends to it.
    assert entries["Allow bob"].changed
    assert not entries["Drop carol"].changed
    # The file exists by now, so the removal runs the ordinary content
    # comparison rather than the absent-file short circuit: carol is simply not
    # in it, and the text is unchanged.
    assert entries["Drop carol"].reason == f"content already up to date ({HT_FILE})"

    text = hosts[0].remote_files[HT_FILE].decode("utf-8")
    lines = text.splitlines()
    assert [line.split(":", 1)[0] for line in lines] == [ALICE, BOB]
    assert files.htpasswd_verify(PASS_ALICE, lines[0].split(":", 1)[1])
    assert lines[1] == f"{BOB}:{PASS_BOB}"
    assert hosts[0].file_stats[HT_FILE]["mode"] == 640
    assert hosts[0].file_stats[HT_FILE]["user"] == "root"
    assert not [p for p in hosts[0].remote_files if p.startswith("/tmp/reemote-install")]

    log = format_log(report)
    assert f"$ scp <files install> -> {SPOOL}" in log
    assert f"$ sudo sh -c 'mkdir -p /etc/apache2 && cp {SPOOL} {HT_FILE}" in log
    assert f"chown root:www-data {HT_FILE} && chmod 640 {HT_FILE}" in log
    assert f"rm -rf {SPOOL_DIR}'" in log


async def test_htpasswd_never_renders_a_secret():
    """The spool descriptor stands in for the content, so neither a password nor
    a hash appears in any plan or log line."""
    plans, report, _hosts = await run(
        INVENTORY, htpasswd_deploy(), host_factory=SimulatedHost.from_inventory
    )
    rendered = format_plan(plans) + format_log(report)
    for secret in (PASS_ALICE, PASS_BOB, "$apr1$"):
        assert secret not in rendered


async def test_htpasswd_converges_on_a_second_run():
    """The migration's whole claim across two deploys: the random salts drawn in
    run one are all verified in run two, so nothing changes."""
    hosts = [SimulatedHost.from_inventory(INVENTORY[0])]

    def factory(entry):
        return hosts[0]

    _plans, first, _ = await run(INVENTORY, htpasswd_deploy(), host_factory=factory)
    assert [entry.changed for entry in first.log] == [True, True, False]

    text = hosts[0].remote_files[HT_FILE].decode("utf-8")
    _plans, second, _ = await run(INVENTORY, htpasswd_deploy(), host_factory=factory)
    assert [entry.changed for entry in second.log] == [False, False, False]
    assert hosts[0].remote_files[HT_FILE].decode("utf-8") == text


async def test_htpasswd_dry_run_sends_nothing():
    plans, report, hosts = await run(
        INVENTORY,
        htpasswd_deploy(),
        host_factory=SimulatedHost.from_inventory,
        dry=True,
    )
    assert report is None
    assert plans["web1"][0].changed
    assert hosts[0].remote_files == {}


async def test_htpasswd_shells_carry_no_elevation_without_the_flag():
    """The elevation is the request's flag, not the operation's: the same
    descriptors are yielded unprefixed, so a login-writable target works."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.htpasswd(ctx, HT_FILE, ALICE, PASS_ALICE)]
    assert not any("sudo" in command_shell(cmd) for cmd in commands)
