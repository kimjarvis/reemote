# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for ``files.download``: fetch a URL control-side, install it at a path.

``files.download`` is the general download-to-path operation the package-manager
roles keep needing (a GPG key + a ``.repo``/``.sources`` file pulled from a
vendor URL). It retires the hand-rolled ``urllib.request`` + ``io.BytesIO``
workaround ``dnf.key``/``apt.key``/``gpg.key`` force, since each *rejects* a URL
``src``. The body fetches the ``http(s)`` URL on the *control* machine (never the
target host, so no host-side ``curl``/``wget``), verifies an optional
Ansible-style ``"<algorithm>:<hexdigest>"`` checksum, decodes the bytes to text,
and delegates the write to ``files.install`` -- so a download inherits install's
content-only idempotence, its dest-derived ``/tmp`` spool, its owner/mode
handling, its optional ``validate`` hook and its two-privilege-level signature
(unprivileged scp transfer + elevated ``sh -c`` install) with no new command
descriptor.

The single network read (``_fetch_url_bytes``) is monkeypatched to a canned-bytes
stub so every test stays offline. Covers the checksum-spec parser, the http(s)
scheme guard (rejected before any fetch), the fetch-then-delegate behaviour
(create, rewrite, converged no-op), checksum match/mismatch, the binary-body
rejection, the forwarding of ``mode``/``user``/``group``/``validate`` and
``**kwargs`` to install, the control-side fetch seam, and end-to-end
``run``/plan/log behaviour including the once-per-phase fetch, the second-run
no-op and the dry run.
"""

import hashlib
from collections.abc import Generator

import pytest

from reemote.core import FactCache, OpContext, OperationRequest, op
from reemote.drivers import command_shell, format_log, format_plan, run
from reemote.hosts import Host
from reemote.modules import files
from reemote.modules.files import files as files_module
from reemote.modules.files.files import (
    InstallCommand,
    InstallSpoolCommand,
    InstallValidateCommand,
    _install_spool_path,
    _parse_checksum,
)
from tests.simulated_host import SimulatedHost

INVENTORY = [
    {
        "host": "web1",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
]

URL = "https://download.example.com/linux/centos/docker-ce.repo"
DEST = "/etc/yum.repos.d/docker-ce.repo"
CONTENT = "[docker-ce-stable]\nname=Docker CE Stable\nenabled=1\n"
DATA = CONTENT.encode()
CHECKSUM = "sha256:" + hashlib.sha256(DATA).hexdigest()
SPOOL = _install_spool_path(DEST)
SPOOL_DIR = SPOOL.rsplit("/", 1)[0]


@pytest.fixture
def canned_fetch(monkeypatch):
    """Replace the control-side network read with a canned-bytes stub.

    ``files.download`` looks ``_fetch_url_bytes`` up as a module global at call
    time (through ``asyncio.to_thread``), so patching the module attribute
    redirects the fetch. Returns the list of URLs the stub was called with, so a
    test can assert the fetch happened -- and how many times, since the body
    runs once per phase.
    """
    fetched: list[str] = []

    def fake_fetch(url: str) -> bytes:
        fetched.append(url)
        return DATA

    monkeypatch.setattr(files_module, "_fetch_url_bytes", fake_fetch)
    return fetched


# --- The checksum-spec parser (pure) ------------------------------------------


def test_parse_checksum_splits_and_lowercases():
    """The Ansible-style spec lowercases both halves for a case-insensitive
    compare against the computed digest."""
    assert _parse_checksum("sha256:9F2CAB") == ("sha256", "9f2cab")


def test_parse_checksum_accepts_other_known_algorithms():
    assert _parse_checksum("md5:D41D8") == ("md5", "d41d8")
    assert _parse_checksum("sha1:abc") == ("sha1", "abc")


def test_parse_checksum_strips_surrounding_whitespace():
    assert _parse_checksum("  sha256 : 9F2C  ") == ("sha256", "9f2c")


@pytest.mark.parametrize("spec", ["9f2cab", ":9f2cab", "sha256:", "  :", ""])
def test_parse_checksum_rejects_a_malformed_spec(spec):
    """No separator, an empty algorithm or an empty digest fails fast -- before
    any network round-trip -- so a typo surfaces at the deploy line."""
    with pytest.raises(ValueError, match="algorithm>:<hexdigest"):
        _parse_checksum(spec)


def test_parse_checksum_rejects_an_unknown_algorithm():
    with pytest.raises(ValueError, match="unknown checksum algorithm"):
        _parse_checksum("rot13:abcd")


# --- The http(s) scheme guard -------------------------------------------------


@pytest.mark.parametrize(
    "url", ["ftp://example.com/f", "file:///etc/passwd", "/local/path", ""]
)
async def test_download_rejects_a_non_http_url_before_fetching(url, canned_fetch):
    """A ``file:``/``ftp:`` scheme or a bare path raises before the fetch, so
    the control machine never opens an unexpected connection."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match=r"http\(s\) URL"):
        [cmd async for cmd in files.download(ctx, url, DEST)]
    assert canned_fetch == []


# --- The operation: fetch control-side, then delegate to install ---------------


async def test_download_creates_an_absent_file(canned_fetch):
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.download(ctx, URL, DEST)]

    assert ctx.changed
    assert ctx.reason == f"content {DEST} is absent, will install"
    spool_cmd, install_cmd = commands
    assert isinstance(spool_cmd, InstallSpoolCommand)
    assert spool_cmd.content == CONTENT
    assert spool_cmd.dest == SPOOL
    assert isinstance(install_cmd, InstallCommand)
    assert install_cmd.spool == SPOOL
    assert install_cmd.dest == DEST
    assert install_cmd.mode == 644
    # The fetch is what produced the installed content.
    assert canned_fetch == [URL]


async def test_download_rewrites_when_the_content_differs(canned_fetch):
    host = SimulatedHost(name="h", remote_files={DEST: b"stale\n"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.download(ctx, URL, DEST)]

    assert ctx.changed
    assert ctx.reason == f"content {DEST} differs, will install"
    assert len(commands) == 2


async def test_download_is_a_no_op_when_the_content_matches(canned_fetch):
    """Install's content-only idempotence carries through: a converged file
    yields nothing -- but the URL is still fetched to make the comparison."""
    host = SimulatedHost(name="h", remote_files={DEST: DATA})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.download(ctx, URL, DEST)]

    assert not ctx.changed
    assert ctx.reason == f"content already up to date ({DEST})"
    assert commands == []
    assert canned_fetch == [URL]


async def test_download_fetches_control_side_not_on_the_host(canned_fetch):
    """The stubbed control-side reader is the only network path -- the host is
    never asked to curl/wget, and the yielded descriptors are exactly install's
    spool+install pair (no download-specific command type)."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.download(ctx, URL, DEST)]

    assert canned_fetch == [URL]
    assert [type(cmd) for cmd in commands] == [
        InstallSpoolCommand,
        InstallCommand,
    ]


# --- Checksum verification ----------------------------------------------------


async def test_download_accepts_a_matching_checksum(canned_fetch):
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd async for cmd in files.download(ctx, URL, DEST, checksum=CHECKSUM)
    ]
    assert len(commands) == 2
    assert commands[0].content == CONTENT


async def test_download_checksum_is_case_insensitive(canned_fetch):
    """An uppercase algorithm/hexdigest still verifies against the computed
    (lowercased) digest."""
    upper = "SHA256:" + hashlib.sha256(DATA).hexdigest().upper()
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd async for cmd in files.download(ctx, URL, DEST, checksum=upper)
    ]
    assert len(commands) == 2


async def test_download_rejects_a_checksum_mismatch(canned_fetch):
    """A digest mismatch raises after the fetch but before anything is written,
    so a tampered or truncated artifact never reaches the destination."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="checksum mismatch"):
        [
            cmd
            async for cmd in files.download(
                ctx, URL, DEST, checksum="sha256:" + "0" * 64
            )
        ]
    assert canned_fetch == [URL]
    assert host.remote_files == {}


async def test_download_surfaces_a_malformed_checksum_spec(canned_fetch):
    """The spec is parsed before the fetch, so a malformed checksum fails fast."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="algorithm>:<hexdigest"):
        [cmd async for cmd in files.download(ctx, URL, DEST, checksum="9f2cab")]
    assert canned_fetch == []


# --- The binary-body rejection ------------------------------------------------


async def test_download_rejects_a_binary_body(monkeypatch):
    """Install takes ``content: str``, so a non-UTF-8 response raises rather
    than corrupting the write -- a binary download stays ``files.put``'s job."""
    monkeypatch.setattr(
        files_module, "_fetch_url_bytes", lambda url: b"\xff\xfe\x00\x01binary"
    )
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="not valid UTF-8 text"):
        [cmd async for cmd in files.download(ctx, URL, DEST)]
    assert host.remote_files == {}


# --- Forwarding to install ----------------------------------------------------


async def test_download_forwards_mode_user_and_group_to_install(canned_fetch):
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.download(
            ctx, URL, DEST, mode="0640", user="www", group="www"
        )
    ]
    _, install_cmd = commands
    assert install_cmd.mode == 640
    assert install_cmd.user == "www"
    assert install_cmd.group == "www"
    assert f"chown www:www {DEST}" in install_cmd.shell()
    assert "chmod 640 " in install_cmd.shell()


async def test_download_forwards_validate_to_install(canned_fetch):
    """The ``validate`` hook reaches install, which injects the validation step
    between the spool transfer and the elevated install."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.download(
            ctx, URL, DEST, validate="grep -q docker {spool}"
        )
    ]
    assert [type(cmd) for cmd in commands] == [
        InstallSpoolCommand,
        InstallValidateCommand,
        InstallCommand,
    ]
    assert commands[1].validate == "grep -q docker {spool}"


@pytest.mark.parametrize("mode", ["u+x", "888", "", "0o644"])
async def test_download_surfaces_install_mode_validation(mode, canned_fetch):
    """A non-octal-digit mode raises install's ValueError through download."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="Invalid file mode"):
        [cmd async for cmd in files.download(ctx, URL, DEST, mode=mode)]


async def test_download_absorbs_unknown_kwargs(canned_fetch):
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd async for cmd in files.download(ctx, URL, DEST, some_global_arg=True)
    ]
    assert len(commands) == 2


# --- Descriptor elevation asymmetry (inherited from install) ------------------


def test_spool_transfer_is_never_elevated_but_install_is():
    """The two-privilege-level signature a download inherits: a sudo-flagged
    request leaves the scp transfer unprefixed and elevates only the install."""
    spool_cmd = InstallSpoolCommand(content=CONTENT, dest=SPOOL)
    assert command_shell(spool_cmd, sudo=True) == str(spool_cmd)

    install_cmd = InstallCommand(spool=SPOOL, dest=DEST, mode=644)
    assert command_shell(install_cmd, sudo=True).startswith("sudo sh -c ")


# --- End to end through the driver ---------------------------------------------


def download_deploy():
    """One download, then a second download of the same URL to the same path:
    the second reads back what the first installed and is a guaranteed no-op.
    ``url``/``dest``/``checksum`` are hashable scalars, so the request sequence
    is identical across the prepare and apply phases (no DeterminismError)."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.download,
            name="Download repo",
            url=URL,
            dest=DEST,
            checksum=CHECKSUM,
            sudo=True,
        )
        yield op(
            files.download,
            name="Re-download repo",
            url=URL,
            dest=DEST,
            checksum=CHECKSUM,
            sudo=True,
        )

    return deploy


async def test_download_end_to_end_plan_log_and_second_no_op(canned_fetch):
    """The prepare plan materializes both downloads against pre-deployment
    state (both changed); only the apply log -- with its per-step FactCache
    recreation -- shows the second reading back what the first installed."""
    plans, report, hosts = await run(
        INVENTORY, download_deploy(), host_factory=SimulatedHost.from_inventory
    )

    first, second = plans["web1"]
    assert first.changed
    assert second.changed  # prepare never sends: the file was absent for both
    assert f"content {DEST} is absent, will install" in format_plan(plans)

    assert report is not None
    entries = {entry.name: entry for entry in report.log}
    assert entries["Download repo"].changed
    assert not entries["Re-download repo"].changed
    assert entries["Re-download repo"].reason == (
        f"content already up to date ({DEST})"
    )

    # The fetched content landed at dest and the spool was cleaned up.
    assert hosts[0].remote_files == {DEST: DATA}

    log = format_log(report)
    assert f"$ scp <files install> -> {SPOOL}" in log
    assert "$ sudo sh -c 'mkdir -p /etc/yum.repos.d && cp" in log

    # 2 requests x 2 phases: the body always fetches, even for the no-op.
    assert canned_fetch.count(URL) == 4


async def test_download_fetches_once_per_phase(canned_fetch):
    """A single-request deploy fetches the URL twice per host -- once in
    prepare, once in apply -- and the checksum pins the bytes so both phases
    install identical content."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.download,
            name="Download repo",
            url=URL,
            dest=DEST,
            checksum=CHECKSUM,
            sudo=True,
        )

    _plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=SimulatedHost.from_inventory
    )
    assert canned_fetch == [URL, URL]
    assert report is not None
    assert report.log[0].changed
    assert hosts[0].remote_files == {DEST: DATA}


async def test_download_dry_run_sends_nothing_but_still_fetches(canned_fetch):
    """A dry run materializes the plan (so the body -- and the fetch -- runs in
    prepare) but never applies, leaving the host untouched."""
    plans, report, hosts = await run(
        INVENTORY,
        download_deploy(),
        host_factory=SimulatedHost.from_inventory,
        dry=True,
    )
    assert report is None
    assert plans["web1"][0].changed
    assert hosts[0].remote_files == {}
    # prepare materializes both requests; dry skips the apply phase entirely.
    assert canned_fetch.count(URL) == 2


async def test_download_with_owner_end_to_end(canned_fetch):
    """An owned download writes the fetched content, records the requested owner
    and mode, and a second identical download reads it back as a no-op."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.download,
            name="Download owned repo",
            url=URL,
            dest=DEST,
            mode="0644",
            user="www",
            group="www",
            sudo=True,
        )
        yield op(
            files.download,
            name="Re-download owned repo",
            url=URL,
            dest=DEST,
            mode="0644",
            user="www",
            group="www",
            sudo=True,
        )

    _plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=SimulatedHost.from_inventory
    )
    assert report is not None
    entries = {entry.name: entry for entry in report.log}
    assert entries["Download owned repo"].changed
    assert not entries["Re-download owned repo"].changed

    assert hosts[0].remote_files == {DEST: DATA}
    assert hosts[0].file_stats[DEST]["user"] == "www"
    assert hosts[0].file_stats[DEST]["group"] == "www"
    assert hosts[0].file_stats[DEST]["mode"] == 644

    log = format_log(report)
    assert f"chown www:www {DEST}" in log
