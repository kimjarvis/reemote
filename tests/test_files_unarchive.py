# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for ``files.unarchive``: extract an archive into a remote directory.

``files.unarchive`` is the pyinfra ``files.unarchive`` migration that closes
issue #21 (Ansible ``unarchive`` parity -- the php-xdebug source tarball).
``src`` is a remote path extracted in place, or an ``http(s)`` URL fetched
*control-side* as binary bytes (the ``_fetch_url_bytes`` seam, without the
text-only decode), spooled to a URL-derived ``/tmp`` name over unprivileged
scp, extracted and cleaned up. ``creates`` is Ansible's idempotence gate,
resolved in-operation through ``FileExists`` so a converged host reports
``changed=False``; without it the extraction is stateless, pyinfra's
behaviour. ``dest`` is created when absent (the issue's requirement --
pyinfra raises instead), and ``user``/``group`` chown the extracted tree
recursively.

The single network read (``_fetch_url_bytes``) is monkeypatched to a
canned-bytes stub so every test stays offline. Covers the descriptor
rendering (every tar-family flag, zip, extra_opts placement, quoting,
elevation asymmetry), the format validation, the creates gate, the
remote-path validation, the dest creation, the binary-safe URL fetch with
checksum verification, the ownership step, the SimulatedHost seam
(``archive_members`` -> extracted tree), and end-to-end ``run``/plan/log
behaviour including the two-pass no-op and the dry run.
"""

import hashlib
from collections.abc import Generator

import pytest

from reemote.core import FactCache, OpContext, OperationRequest, op
from reemote.drivers import command_shell, format_log, format_plan, run, send_command
from reemote.hosts import Host
from reemote.modules import files
from reemote.modules.files import files as files_module
from reemote.modules.files.files import (
    ChownCommand,
    MkdirCommand,
    RemoveTreeCommand,
    UnarchiveCommand,
    UnarchiveSpoolCommand,
    _archive_format,
    _unarchive_spool_path,
)
from tests.simulated_host import SimulatedHost

INVENTORY = [
    {
        "host": "web1",
        "family": "Ubuntu",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
]

URL = "https://xdebug.org/files/xdebug-3.3.2.tgz"
DEST = "/opt/xdebug"
CREATES = "/opt/xdebug/xdebug-3.3.2"
# A gzip-magic binary body: never valid UTF-8, so any text decode would raise.
DATA = b"\x1f\x8b\x08\x00\xff\xfe\x00binary-tarball"
CHECKSUM = "sha256:" + hashlib.sha256(DATA).hexdigest()
SPOOL = _unarchive_spool_path(URL)
MEMBERS = ("xdebug-3.3.2/", "xdebug-3.3.2/config.m4", "xdebug-3.3.2/php_xdebug.h")
REMOTE_SRC = "/tmp/xdebug-3.3.2.tgz"
REMOTE_STATS = {"mode": 644, "user": "root", "group": "root"}


@pytest.fixture
def canned_fetch(monkeypatch):
    """Replace the control-side network read with a canned-bytes stub.

    ``files.unarchive`` looks ``_fetch_url_bytes`` up as a module global at
    call time (through ``asyncio.to_thread``), so patching the module
    attribute redirects the fetch. Returns the list of URLs the stub was
    called with, so a test can assert the fetch happened -- and how many
    times, since the body runs once per phase but the ``creates`` gate
    skips it on a converged host.
    """
    fetched: list[str] = []

    def fake_fetch(url: str) -> bytes:
        fetched.append(url)
        return DATA

    monkeypatch.setattr(files_module, "_fetch_url_bytes", fake_fetch)
    return fetched


def make_host(**state) -> SimulatedHost:
    """A simulated host with the URL spool's member listing seeded."""
    state.setdefault("archive_members", {SPOOL: MEMBERS})
    return SimulatedHost(name="h", **state)


def make_ctx(host: SimulatedHost) -> OpContext:
    return OpContext(host=host, facts=FactCache(host, phase="prepare"))


# --- The format table and descriptor rendering --------------------------------


@pytest.mark.parametrize(
    ("name", "tool", "flags"),
    [
        ("app.tar", "tar", "-x"),
        ("app.tar.gz", "tar", "-xz"),
        ("app.tgz", "tar", "-xz"),
        ("app.tar.bz2", "tar", "-xj"),
        ("app.tbz2", "tar", "-xj"),
        ("app.tar.xz", "tar", "-xJ"),
        ("app.txz", "tar", "-xJ"),
        ("app.tar.zst", "tar", "-x --zstd"),
        ("app.zip", "unzip", "-o"),
        ("APP.TGZ", "tar", "-xz"),
    ],
)
def test_archive_format_matches_the_baseline_table(name, tool, flags):
    assert _archive_format(name) == (tool, tuple(flags.split()))


def test_archive_format_rejects_an_unknown_extension():
    assert _archive_format("app.rar") is None
    assert _archive_format("app") is None


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ("/tmp/app.tgz", "tar -xz -f /tmp/app.tgz -C /opt/app"),
        ("/tmp/app.tar", "tar -x -f /tmp/app.tar -C /opt/app"),
        ("/tmp/app.tar.zst", "tar -x --zstd -f /tmp/app.tar.zst -C /opt/app"),
        ("/tmp/app.zip", "unzip -o /tmp/app.zip -d /opt/app"),
    ],
)
def test_unarchive_command_renders_the_extract_shell(src, expected):
    assert UnarchiveCommand(src=src, dest="/opt/app").shell() == expected


def test_unarchive_command_places_extra_opts_before_the_archive():
    """tar keeps ``-f`` adjacent to the archive path so an extra is never
    mistaken for it -- the baseline's ordering."""
    cmd = UnarchiveCommand(
        src="/tmp/app.tgz", dest="/opt/app", extra_opts=("--strip-components=1",)
    )
    assert cmd.shell() == "tar -xz --strip-components=1 -f /tmp/app.tgz -C /opt/app"
    zip_cmd = UnarchiveCommand(
        src="/tmp/app.zip", dest="/opt/app", extra_opts=("-q",)
    )
    assert zip_cmd.shell() == "unzip -o -q /tmp/app.zip -d /opt/app"


def test_unarchive_command_quotes_paths_and_extras():
    cmd = UnarchiveCommand(
        src="/tmp/my app.tgz", dest="/opt/my app", extra_opts=("--exclude=a b",)
    )
    assert cmd.shell() == (
        "tar -xz '--exclude=a b' -f '/tmp/my app.tgz' -C '/opt/my app'"
    )


def test_unarchive_command_rejects_an_unsupported_format():
    with pytest.raises(ValueError, match="unsupported archive format"):
        UnarchiveCommand(src="/tmp/app.rar", dest="/opt/app").shell()


def test_spool_transfer_is_never_elevated_but_extraction_is():
    """The two-privilege-level signature: a sudo-flagged request leaves the
    scp spool transfer unprefixed and elevates the extraction shell."""
    spool_cmd = UnarchiveSpoolCommand(content=DATA, dest=SPOOL)
    assert not hasattr(spool_cmd, "shell")
    assert str(spool_cmd) == f"scp <files unarchive> -> {SPOOL}"
    assert command_shell(spool_cmd, sudo=True) == str(spool_cmd)

    extract_cmd = UnarchiveCommand(src=SPOOL, dest=DEST)
    assert command_shell(extract_cmd, sudo=True) == f"sudo {extract_cmd.shell()}"


def test_spool_path_is_deterministic_and_keeps_the_extension():
    assert _unarchive_spool_path(URL) == SPOOL
    assert SPOOL.startswith("/tmp/reemote-unarchive-")
    assert SPOOL.endswith("-xdebug-3.3.2.tgz")
    # A query string never leaks into the spool name or the format detection.
    assert _unarchive_spool_path(URL + "?download=1").endswith("-xdebug-3.3.2.tgz")
    assert _unarchive_spool_path(URL) != _unarchive_spool_path(
        "https://xdebug.org/files/xdebug-3.4.0.tgz"
    )


# --- The operation: validation -------------------------------------------------


async def test_unarchive_rejects_an_unsupported_format_before_any_fetch(canned_fetch):
    host = make_host()
    with pytest.raises(ValueError, match="unsupported archive format"):
        [cmd async for cmd in files.unarchive(make_ctx(host), URL + ".rar", DEST)]
    assert canned_fetch == []


async def test_unarchive_detects_the_format_through_a_url_query_string(canned_fetch):
    host = make_host(
        directory_stats={DEST: {"mode": 755, "user": "root", "group": "root"}},
        archive_members={_unarchive_spool_path(URL + "?v=1"): MEMBERS},
    )
    ctx = make_ctx(host)
    commands = [
        cmd async for cmd in files.unarchive(ctx, URL + "?v=1", DEST, creates=CREATES)
    ]
    assert ctx.changed
    _spool_cmd, extract_cmd, _cleanup_cmd = commands
    assert isinstance(extract_cmd, UnarchiveCommand)
    assert extract_cmd.shell().startswith("tar -xz ")


async def test_unarchive_surfaces_a_malformed_checksum_before_fetching(canned_fetch):
    host = make_host()
    with pytest.raises(ValueError, match="algorithm>:<hexdigest"):
        [
            cmd
            async for cmd in files.unarchive(make_ctx(host), URL, DEST, checksum="9f2c")
        ]
    assert canned_fetch == []


# --- The creates gate ----------------------------------------------------------


async def test_unarchive_is_a_no_op_when_creates_exists(canned_fetch):
    """Ansible's ``creates:`` idempotence, resolved in-operation: a converged
    host reports no change, yields nothing and never fetches."""
    host = make_host(existing_files={CREATES})
    ctx = make_ctx(host)
    commands = [
        cmd async for cmd in files.unarchive(ctx, URL, DEST, creates=CREATES)
    ]
    assert not ctx.changed
    assert ctx.reason == f"archive already extracted ({CREATES} exists)"
    assert commands == []
    assert canned_fetch == []


async def test_unarchive_extracts_when_creates_is_absent(canned_fetch):
    host = make_host()
    ctx = make_ctx(host)
    commands = [
        cmd async for cmd in files.unarchive(ctx, URL, DEST, creates=CREATES)
    ]
    assert ctx.changed
    assert canned_fetch == [URL]
    assert len(commands) == 4


async def test_unarchive_without_creates_is_stateless(canned_fetch):
    """No gate, no idempotence: pyinfra's behaviour -- the extraction always
    yields and always reports changed, even with the tree already present."""
    host = make_host(existing_files={CREATES})
    ctx = make_ctx(host)
    commands = [cmd async for cmd in files.unarchive(ctx, URL, DEST)]
    assert ctx.changed
    assert len(commands) == 4


# --- The URL branch: binary-safe fetch, spool, extract, cleanup -----------------


async def test_unarchive_url_yields_spool_extract_cleanup(canned_fetch):
    host = make_host(directory_stats={DEST: {"mode": 755, "user": "root", "group": "root"}})
    ctx = make_ctx(host)
    commands = [
        cmd async for cmd in files.unarchive(ctx, URL, DEST, creates=CREATES)
    ]
    assert ctx.changed
    assert ctx.reason == f"will extract {URL} into {DEST}"
    spool_cmd, extract_cmd, cleanup_cmd = commands
    assert isinstance(spool_cmd, UnarchiveSpoolCommand)
    assert spool_cmd.content == DATA  # binary bytes, never decoded
    assert spool_cmd.dest == SPOOL
    assert isinstance(extract_cmd, UnarchiveCommand)
    assert extract_cmd.src == SPOOL
    assert extract_cmd.dest == DEST
    assert isinstance(cleanup_cmd, RemoveTreeCommand)
    assert cleanup_cmd.src == SPOOL


async def test_unarchive_accepts_a_matching_checksum(canned_fetch):
    host = make_host(
        directory_stats={DEST: {"mode": 755, "user": "root", "group": "root"}}
    )
    commands = [
        cmd
        async for cmd in files.unarchive(make_ctx(host), URL, DEST, checksum=CHECKSUM)
    ]
    assert commands[0].content == DATA


async def test_unarchive_rejects_a_checksum_mismatch(canned_fetch):
    """A digest mismatch raises after the fetch but before anything is staged,
    so a tampered or truncated archive never reaches the destination."""
    host = make_host()
    with pytest.raises(ValueError, match="checksum mismatch"):
        [
            cmd
            async for cmd in files.unarchive(
                make_ctx(host), URL, DEST, checksum="sha256:" + "0" * 64
            )
        ]
    assert canned_fetch == [URL]
    assert host.remote_files == {}


async def test_unarchive_creates_an_absent_dest(canned_fetch):
    """The issue's requirement (pyinfra raises instead): a missing dest is
    created with its parents before the extraction."""
    host = make_host()
    ctx = make_ctx(host)
    commands = [
        cmd async for cmd in files.unarchive(ctx, URL, DEST, creates=CREATES)
    ]
    assert ctx.reason == (
        f"{DEST} missing, will create; will extract {URL} into {DEST}"
    )
    mkdir_cmd, spool_cmd, extract_cmd, cleanup_cmd = commands
    assert isinstance(mkdir_cmd, MkdirCommand)
    assert mkdir_cmd.path == DEST
    assert isinstance(spool_cmd, UnarchiveSpoolCommand)
    assert isinstance(extract_cmd, UnarchiveCommand)
    assert isinstance(cleanup_cmd, RemoveTreeCommand)


async def test_unarchive_rejects_a_non_directory_dest(canned_fetch):
    host = make_host(directory_stats={DEST: False})
    with pytest.raises(ValueError, match="is not a directory"):
        [cmd async for cmd in files.unarchive(make_ctx(host), URL, DEST)]


# --- The remote-path branch -----------------------------------------------------


async def test_unarchive_remote_src_extracts_in_place():
    host = SimulatedHost(
        name="h",
        file_stats={REMOTE_SRC: REMOTE_STATS},
        directory_stats={DEST: {"mode": 755, "user": "root", "group": "root"}},
        archive_members={REMOTE_SRC: MEMBERS},
    )
    ctx = make_ctx(host)
    commands = [
        cmd async for cmd in files.unarchive(ctx, REMOTE_SRC, DEST, creates=CREATES)
    ]
    assert ctx.changed
    assert ctx.reason == f"will extract {REMOTE_SRC} into {DEST}"
    # One command only: no spool, no cleanup -- the remote archive stays.
    (extract_cmd,) = commands
    assert isinstance(extract_cmd, UnarchiveCommand)
    assert extract_cmd.src == REMOTE_SRC


async def test_unarchive_rejects_an_absent_remote_src():
    host = SimulatedHost(name="h")
    with pytest.raises(ValueError, match="does not exist"):
        [cmd async for cmd in files.unarchive(make_ctx(host), REMOTE_SRC, DEST)]


async def test_unarchive_rejects_a_non_file_remote_src():
    host = SimulatedHost(name="h", file_stats={REMOTE_SRC: False})
    with pytest.raises(ValueError, match="is not a regular file"):
        [cmd async for cmd in files.unarchive(make_ctx(host), REMOTE_SRC, DEST)]


# --- Ownership and option parity -------------------------------------------------


async def test_unarchive_chowns_the_tree_recursively(canned_fetch):
    host = make_host()
    commands = [
        cmd
        async for cmd in files.unarchive(
            make_ctx(host), URL, DEST, user="www", group="www"
        )
    ]
    chown_cmd = commands[-1]
    assert isinstance(chown_cmd, ChownCommand)
    assert chown_cmd.recursive
    assert chown_cmd.shell() == f"chown -R www:www {DEST}"


async def test_unarchive_group_alone_renders_chgrp(canned_fetch):
    host = make_host()
    commands = [
        cmd async for cmd in files.unarchive(make_ctx(host), URL, DEST, group="www")
    ]
    assert commands[-1].shell() == f"chgrp -R www {DEST}"


async def test_unarchive_forwards_extra_opts(canned_fetch):
    host = make_host(
        directory_stats={DEST: {"mode": 755, "user": "root", "group": "root"}}
    )
    commands = [
        cmd
        async for cmd in files.unarchive(
            make_ctx(host), URL, DEST, extra_opts=("--strip-components=1",)
        )
    ]
    extract_cmd = commands[1]
    assert extract_cmd.extra_opts == ("--strip-components=1",)
    assert "--strip-components=1 -f" in extract_cmd.shell()


async def test_unarchive_absorbs_unknown_kwargs(canned_fetch):
    host = make_host()
    commands = [
        cmd async for cmd in files.unarchive(make_ctx(host), URL, DEST, some_global_arg=True)
    ]
    assert len(commands) == 4


# --- The SimulatedHost seam -------------------------------------------------------


async def test_seam_spool_stores_the_binary_bytes():
    host = SimulatedHost(name="h")
    await send_command(host, UnarchiveSpoolCommand(content=DATA, dest=SPOOL))
    assert host.remote_files == {SPOOL: DATA}


async def test_seam_extraction_materializes_the_seeded_members():
    host = SimulatedHost(name="h", archive_members={SPOOL: MEMBERS})
    await send_command(host, UnarchiveCommand(src=SPOOL, dest=DEST))
    # The directory member is visible to FileExists (test -e sees directories)
    # -- what makes the creates gate converge -- and to Directory.
    assert CREATES in host.existing_files
    assert CREATES in host.directory_stats
    # The file members carry existence markers and File-fact metadata rows.
    assert f"{CREATES}/config.m4" in host.existing_files
    assert host.file_stats[f"{CREATES}/config.m4"]["mode"] == 644
    # The archive itself is left in place (the paired cleanup removes a spool).
    assert host.remote_files == {}


async def test_seam_unseeded_archive_extracts_nothing():
    host = SimulatedHost(name="h")
    await send_command(host, UnarchiveCommand(src=SPOOL, dest=DEST))
    assert host.existing_files == set()


# --- End to end through the driver -------------------------------------------------


def unarchive_deploy():
    """One URL unarchive, then the same request again: the second reads the
    ``creates`` path the first extraction materialized and is a guaranteed
    no-op in the apply log. All arguments are hashable scalars/tuples, so the
    request sequence is identical across phases (no DeterminismError)."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.unarchive,
            name="Untar Xdebug",
            src=URL,
            dest=DEST,
            creates=CREATES,
            checksum=CHECKSUM,
            sudo=True,
        )
        yield op(
            files.unarchive,
            name="Untar Xdebug again",
            src=URL,
            dest=DEST,
            creates=CREATES,
            checksum=CHECKSUM,
            sudo=True,
        )

    return deploy


def simulated_factory(entry: dict) -> SimulatedHost:
    return SimulatedHost(name=entry["host"], archive_members={SPOOL: MEMBERS})


async def test_unarchive_end_to_end_plan_log_and_second_no_op(canned_fetch):
    """The prepare plan materializes both requests against pre-deployment
    state (both changed); only the apply log -- with its per-step FactCache
    recreation -- shows the second gated by the first's extraction."""
    plans, report, hosts = await run(
        INVENTORY, unarchive_deploy(), host_factory=simulated_factory
    )

    first, second = plans["web1"]
    assert first.changed
    assert second.changed  # prepare never sends: creates was absent for both
    assert f"will extract {URL} into {DEST}" in format_plan(plans)

    assert report is not None
    entries = {entry.name: entry for entry in report.log}
    assert entries["Untar Xdebug"].changed
    assert not entries["Untar Xdebug again"].changed
    assert entries["Untar Xdebug again"].reason == (
        f"archive already extracted ({CREATES} exists)"
    )

    # The extracted tree landed and the spool was cleaned up.
    assert CREATES in hosts[0].existing_files
    assert SPOOL not in hosts[0].remote_files

    log = format_log(report)
    assert f"$ scp <files unarchive> -> {SPOOL}" in log
    assert f"$ sudo mkdir -p {DEST}" in log
    assert f"$ sudo tar -xz -f {SPOOL} -C {DEST}" in log
    assert f"$ sudo rm -rf {SPOOL}" in log

    # prepare fetches for both requests; apply fetches only for the first
    # (the second's creates gate skips the fetch entirely).
    assert canned_fetch.count(URL) == 3


async def test_unarchive_two_runs_second_is_a_no_op(canned_fetch):
    """The issue's acceptance proof: plan -> apply -> a SECOND apply on the
    same host reports changed=False and sends nothing."""
    host = SimulatedHost(name="web1", archive_members={SPOOL: MEMBERS})

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.unarchive,
            name="Untar Xdebug",
            src=URL,
            dest=DEST,
            creates=CREATES,
            sudo=True,
        )

    _plans, report, _hosts = await run(
        INVENTORY, deploy, host_factory=lambda entry: host
    )
    assert report is not None
    assert report.log[0].changed

    plans, report, _hosts = await run(
        INVENTORY, deploy, host_factory=lambda entry: host
    )
    assert report is not None
    assert not report.log[0].changed
    assert report.log[0].reason == f"archive already extracted ({CREATES} exists)"
    assert not plans["web1"][0].changed
    # Run 1 fetched once per phase; run 2's gate skipped the fetch entirely.
    assert canned_fetch == [URL, URL]


async def test_unarchive_dry_run_sends_nothing_but_still_fetches(canned_fetch):
    plans, report, hosts = await run(
        INVENTORY,
        unarchive_deploy(),
        host_factory=simulated_factory,
        dry=True,
    )
    assert report is None
    assert plans["web1"][0].changed
    assert hosts[0].existing_files == set()
    assert hosts[0].remote_files == {}
    # prepare materializes both requests; dry skips the apply phase entirely.
    assert canned_fetch.count(URL) == 2


async def test_unarchive_remote_src_end_to_end():
    """A remote-path extraction with ownership: one elevated tar plus the
    recursive chown, and the archive stays on the host."""
    host = SimulatedHost(
        name="web1",
        file_stats={REMOTE_SRC: REMOTE_STATS},
        archive_members={REMOTE_SRC: MEMBERS},
    )

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.unarchive,
            name="Extract staged tarball",
            src=REMOTE_SRC,
            dest=DEST,
            creates=CREATES,
            user="www",
            group="www",
            sudo=True,
        )

    _plans, report, _hosts = await run(
        INVENTORY, deploy, host_factory=lambda entry: host
    )
    assert report is not None
    assert report.log[0].changed
    log = format_log(report)
    assert f"$ sudo tar -xz -f {REMOTE_SRC} -C {DEST}" in log
    assert f"$ sudo chown -R www:www {DEST}" in log
    assert host.file_stats[REMOTE_SRC] == REMOTE_STATS  # the archive stays
    assert host.directory_stats[DEST]["user"] == "www"