# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for ``files.copy``: copy a remote path into a remote directory.

Covers the ``CopyCommand`` shell descriptor (quoting, the ``-f`` overwrite
flag, elevated ``command_shell`` renderings), the operation's the baseline
``files.copy`` fixture parity (file/directory copies with and without
overwrite, the equal-content no-op, and the error branches), the deliberate
phase-unaware deviation (absent src / absent dest directory raise
``ValueError`` in *both* phases, where the baseline logs softly while planning),
the upstream src check that rejects a symlink source (kept verbatim, unlike
``files.move`` which accepts links), the ``SimulatedHost`` seam dispatch
(the source stays in place), and end-to-end plan/log behaviour including
the idempotent repeat copy and the put-then-copy two-run consequence.
"""

import io
from collections.abc import Generator

import pytest

from reemote.core import FactCache, OpContext, OperationRequest, op
from reemote.drivers import (
    command_shell,
    format_log,
    format_plan,
    run,
    send_command,
)
from reemote.hosts import Host
from reemote.modules import files
from reemote.modules.files.files import CopyCommand
from tests.simulated_host import SimulatedHost

INVENTORY = [
    {
        "host": "web1",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
]

SRC = "/tmp/testfile"
DEST = "/tmp2"
FULL_DEST = "/tmp2/testfile"


def make_host(
    *,
    src_stats: dict | bool | None = None,
    src_dir_stats: dict | bool | None = None,
    dest_stats: dict | bool | None = None,
    target_stats: dict | bool | None = None,
) -> SimulatedHost:
    """A SimulatedHost seeded with the stat rows ``copy``'s facts read.

    Mirrors the baseline's fixture shape: ``Directory(src)``/``File(src)``,
    ``Directory(dest)`` and ``File(dest/basename(src))``; an unset row
    reads as None (absent).
    """
    host = SimulatedHost(name="h")
    if src_stats is not None:
        host.file_stats[SRC] = src_stats
    if src_dir_stats is not None:
        host.directory_stats[SRC] = src_dir_stats
    if dest_stats is not None:
        host.directory_stats[DEST] = dest_stats
    if target_stats is not None:
        host.file_stats[FULL_DEST] = target_stats
    return host


async def materialize(
    host: SimulatedHost, *, phase: str = "prepare", **kwargs
) -> tuple[OpContext, list]:
    ctx = OpContext(host=host, facts=FactCache(host, phase=phase))
    commands = [cmd async for cmd in files.copy(ctx, **kwargs)]
    return ctx, commands


# --- Descriptor rendering -----------------------------------------------------


def test_copy_command_renders_quoted_cp():
    assert CopyCommand(src=SRC, dest=DEST).shell() == "cp -r /tmp/testfile /tmp2"
    assert (
        CopyCommand(src=SRC, dest=DEST, overwrite=True).shell()
        == "cp -r -f /tmp/testfile /tmp2"
    )
    assert (
        CopyCommand(src="/tmp/my file", dest="/opt/other dir").shell()
        == "cp -r '/tmp/my file' '/opt/other dir'"
    )


def test_command_shell_elevates_the_copy():
    cmd = CopyCommand(src=SRC, dest=DEST, overwrite=True)
    assert command_shell(cmd, sudo=True) == "sudo cp -r -f /tmp/testfile /tmp2"
    assert (
        command_shell(CopyCommand(src=SRC, dest=DEST), su=True)
        == "su -c 'cp -r /tmp/testfile /tmp2'"
    )


# --- Operation parity with the baseline's files.copy fixtures ----------------------


async def test_copy_file_fixture():
    # The baseline's copies_file.json: src is a regular file, dest is a
    # directory, the target path is absent -> a single cp -r.
    host = make_host(src_stats={"mode": 600}, dest_stats={"mode": 700})
    ctx, commands = await materialize(host, src=SRC, dest=DEST)

    assert ctx.changed
    assert ctx.reason == f"will copy {SRC} into {DEST}"
    assert commands == [CopyCommand(src=SRC, dest=DEST)]


async def test_copy_file_overwriting_fixture():
    # The baseline's copies_file_overwriting.json: an existing target with
    # overwrite=True yields cp -r -f -- no rm -rf prelude (unlike move).
    host = make_host(
        src_stats={"mode": 600},
        dest_stats={"mode": 700},
        target_stats={"mode": 600},
    )
    ctx, commands = await materialize(host, src=SRC, dest=DEST, overwrite=True)

    assert ctx.changed
    assert commands == [CopyCommand(src=SRC, dest=DEST, overwrite=True)]


async def test_copy_directory_fixture():
    # The baseline's copies_directory.json: File(src) is None but
    # Directory(src) is truthy, so the file-or-directory check passes.
    host = make_host(src_dir_stats={"mode": 700}, dest_stats={"mode": 700})
    ctx, commands = await materialize(host, src=SRC, dest=DEST)

    assert ctx.changed
    assert commands == [CopyCommand(src=SRC, dest=DEST)]


async def test_copy_directory_overwriting_fixture():
    # The baseline's copies_directory_overwriting.json: the same directory
    # source with overwrite=True renders cp -r -f.
    host = make_host(src_dir_stats={"mode": 700}, dest_stats={"mode": 700})
    ctx, commands = await materialize(host, src=SRC, dest=DEST, overwrite=True)

    assert ctx.changed
    assert commands == [CopyCommand(src=SRC, dest=DEST, overwrite=True)]


async def test_copy_noop_dest_file_exists_fixture():
    # The baseline's noop_dest_file_exists.json: an existing target with equal
    # sha1 and overwrite unset is a no-op (the idempotent branch move
    # does not have). The baseline's Sha1/Md5/Sha256 chain is sha1-only here.
    host = make_host(
        src_stats={"mode": 600},
        dest_stats={"mode": 700},
        target_stats={"mode": 600},
    )
    host.remote_files = {SRC: b"same\n", FULL_DEST: b"same\n"}
    ctx, commands = await materialize(host, src=SRC, dest=DEST)

    assert not ctx.changed
    assert ctx.reason == f"{FULL_DEST} already exists"
    assert commands == []


async def test_copy_invalid_overwrite_fixture_raises():
    # The baseline's invalid_overwrite.json: an existing target whose content
    # differs from src, with overwrite unset.
    host = make_host(
        src_stats={"mode": 600},
        dest_stats={"mode": 700},
        target_stats={"mode": 600},
    )
    host.remote_files = {SRC: b"new\n", FULL_DEST: b"old\n"}
    with pytest.raises(
        ValueError,
        match=r"/tmp2/testfile already exists and is different than src",
    ):
        await materialize(host, src=SRC, dest=DEST)


async def test_copy_invalid_dest_fixture_raises():
    # The baseline's invalid_dest.json: dest exists but is not a directory.
    host = make_host(src_stats={"mode": 600}, dest_stats=False)
    with pytest.raises(ValueError, match=r"dest /tmp2 is not a directory"):
        await materialize(host, src=SRC, dest=DEST)


async def test_copy_absent_src_raises_in_both_phases():
    # The baseline's invalid_src.json logs softly while planning and raises
    # only while executing; reemote is phase-unaware and deliberately
    # raises in both phases (the files.move strictness, kept).
    host = make_host(dest_stats={"mode": 700})
    for phase in ("prepare", "apply"):
        with pytest.raises(ValueError, match=r"src /tmp/testfile does not exist"):
            await materialize(host, src=SRC, dest=DEST, phase=phase)


async def test_copy_absent_dest_directory_raises_in_both_phases():
    # The baseline's missing_dest.json: same phase-aware softness, same
    # deliberate always-raise deviation.
    host = make_host(src_stats={"mode": 600})
    for phase in ("prepare", "apply"):
        with pytest.raises(
            ValueError, match=r"dest directory /tmp2 does not exist"
        ):
            await materialize(host, src=SRC, dest=DEST, phase=phase)


async def test_copy_symlink_src_raises():
    # Upstream's own check, kept verbatim: a symlink reads False for both
    # File and Directory, so copy rejects it -- even though files.move
    # accepts links (the recorded parity decision).
    host = make_host(src_stats=False, dest_stats={"mode": 700})
    host.link_stats[SRC] = {"mode": 600, "link_target": "file.txt"}
    with pytest.raises(ValueError, match=r"src /tmp/testfile does not exist"):
        await materialize(host, src=SRC, dest=DEST)


async def test_copy_non_file_target_skips_the_comparison():
    # The baseline's truthiness kept verbatim: File(target) = False (a
    # directory, say) is falsy, so neither the no-op nor the raise branch
    # fires and the copy is planned even without overwrite.
    host = make_host(
        src_stats={"mode": 600},
        dest_stats={"mode": 700},
        target_stats=False,
    )
    ctx, commands = await materialize(host, src=SRC, dest=DEST)

    assert ctx.changed
    assert commands == [CopyCommand(src=SRC, dest=DEST)]


async def test_copy_kwargs_absorb_extra_globals():
    host = make_host(src_stats={"mode": 600}, dest_stats={"mode": 700})
    ctx, commands = await materialize(
        host, src=SRC, dest=DEST, some_extra_global="ignored"
    )
    assert ctx.changed
    assert commands == [CopyCommand(src=SRC, dest=DEST)]


# --- The SimulatedHost seam ----------------------------------------------------


async def test_send_command_copy_mutates_the_stores():
    host = SimulatedHost(name="h")
    host.remote_files = {SRC: b"payload\n"}
    host.existing_files = {SRC}
    host.file_stats = {SRC: {"mode": 600}}
    await send_command(host, CopyCommand(src=SRC, dest=DEST))

    # The copy landed and the source stayed in place (the move difference).
    assert host.remote_files == {SRC: b"payload\n", FULL_DEST: b"payload\n"}
    assert host.existing_files == {SRC, FULL_DEST}
    assert host.file_stats == {SRC: {"mode": 600}, FULL_DEST: {"mode": 600}}
    # The stat rows are distinct dicts, so mutating one never leaks.
    assert host.file_stats[SRC] is not host.file_stats[FULL_DEST]


async def test_send_command_copy_seeds_directory_stats():
    host = SimulatedHost(name="h")
    host.directory_stats = {SRC: {"mode": 700}}
    await send_command(host, CopyCommand(src=SRC, dest=DEST, overwrite=True))

    assert host.directory_stats == {SRC: {"mode": 700}, FULL_DEST: {"mode": 700}}
    assert host.directory_stats[SRC] is not host.directory_stats[FULL_DEST]


# --- End to end through the driver ---------------------------------------------


def install_factory(entry: dict) -> SimulatedHost:
    """A host with a spooled ``/tmp/hosts`` and an empty ``/etc`` directory."""
    host = SimulatedHost.from_inventory(entry)
    host.remote_files = {"/tmp/hosts": b"127.0.0.1 localhost\n"}
    host.file_stats = {"/tmp/hosts": {"mode": 644}}
    host.directory_stats = {"/etc": {"mode": 755}}
    return host


async def test_copy_end_to_end_elevated_plan_and_log():
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.copy,
            name="Copy hosts file",
            src="/tmp/hosts",
            dest="/etc",
            overwrite=True,
            sudo=True,
        )

    plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=install_factory
    )
    (planned,) = plans["web1"]
    assert planned.changed
    assert "will copy /tmp/hosts into /etc" in format_plan(plans)

    assert report is not None
    log = format_log(report)
    assert "$ sudo cp -r -f /tmp/hosts /etc" in log

    # The copy landed at /etc/hosts and the spool file stayed in /tmp.
    assert hosts[0].remote_files == {
        "/tmp/hosts": b"127.0.0.1 localhost\n",
        "/etc/hosts": b"127.0.0.1 localhost\n",
    }
    assert hosts[0].file_stats == {
        "/tmp/hosts": {"mode": 644},
        "/etc/hosts": {"mode": 644},
    }


async def test_repeat_copy_is_a_no_op():
    # Unlike the stateless move, a repeat copy with overwrite unset reads
    # the equal sha1 of source and target and reports no change. The two
    # runs share one simulated host so run 1's state carries over.
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(files.copy, name="Copy hosts", src="/tmp/hosts", dest="/etc")

    plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=install_factory
    )
    assert plans["web1"][0].changed
    assert report is not None
    host = hosts[0]

    plans, report, _hosts = await run(
        INVENTORY, deploy, host_factory=lambda entry: host
    )
    (planned,) = plans["web1"]
    assert not planned.changed
    assert planned.reason == "/etc/hosts already exists"
    assert report is not None
    assert not report.changed
    assert not report.log[0].changed


PAYLOAD = io.StringIO("staged for the files.copy guard test\n")


async def test_put_then_copy_in_one_deploy_raises_at_prepare():
    # The documented consequence of the always-raise deviation: prepare
    # never sends, so the file the put would create does not exist when
    # the copy materializes -- the flow must span two runs.
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(files.put, name="Stage hosts", src=PAYLOAD, dest="/tmp/hosts")
        yield op(files.copy, name="Copy hosts", src="/tmp/hosts", dest="/etc")

    def factory(entry: dict) -> SimulatedHost:
        host = SimulatedHost.from_inventory(entry)
        host.directory_stats = {"/etc": {"mode": 755}}
        return host

    with pytest.raises(ValueError, match=r"src /tmp/hosts does not exist"):
        await run(INVENTORY, deploy, host_factory=factory)