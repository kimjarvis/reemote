# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for ``files.move``: move a remote path into a remote directory.

Covers the ``MoveCommand``/``RemoveTreeCommand`` shell descriptors (quoting,
elevated ``command_shell`` renderings), the operation's the baseline
``files.move`` fixture parity (no-conflict move, directory/link sources,
overwrite's ``rm -rf`` + ``mv`` pair, and the three error branches), the
deliberate phase-unaware deviation (absent src / absent dest directory raise
``ValueError`` in *both* phases, where the baseline logs softly while planning),
the ``SimulatedHost`` seam dispatch, end-to-end plan/log behaviour with
``sudo=True``, and the ``/etc/hosts`` recipe: ``files.edit``
spools the transformed content into a writable path in one run, then
``files.move`` installs it into ``/etc`` elevated in a second run -- the
two-run split the always-raise deviation makes necessary. The hosts case
itself now has a dedicated one-deploy operation (``server.etc_hosts``);
the recipe tests stay as generic edit+move coverage.
"""

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
from reemote.modules.files.files import MoveCommand, RemoveTreeCommand
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
    dest_stats: dict | bool | None = None,
    target_stats: dict | bool | None = None,
) -> SimulatedHost:
    """A SimulatedHost seeded with the stat rows ``move``'s facts read.

    Mirrors the baseline's fixture shape: ``File(src)``, ``Directory(dest)`` and
    ``File(dest/basename(src))``; an unset row reads as None (absent).
    """
    host = SimulatedHost(name="h")
    if src_stats is not None:
        host.file_stats[SRC] = src_stats
    if dest_stats is not None:
        host.directory_stats[DEST] = dest_stats
    if target_stats is not None:
        host.file_stats[FULL_DEST] = target_stats
    return host


async def materialize(host: SimulatedHost, **kwargs) -> tuple[OpContext, list]:
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.move(ctx, **kwargs)]
    return ctx, commands


# --- Descriptor rendering -----------------------------------------------------


def test_move_command_renders_quoted_mv():
    assert MoveCommand(src=SRC, dest=DEST).shell() == "mv /tmp/testfile /tmp2"
    assert (
        MoveCommand(src="/tmp/my file", dest="/opt/other dir").shell()
        == "mv '/tmp/my file' '/opt/other dir'"
    )


def test_remove_tree_command_renders_quoted_rm_rf():
    assert RemoveTreeCommand(src=FULL_DEST).shell() == "rm -rf /tmp2/testfile"
    assert (
        RemoveTreeCommand(src="/tmp/my file").shell() == "rm -rf '/tmp/my file'"
    )


def test_command_shell_elevates_the_move():
    cmd = MoveCommand(src=SRC, dest=DEST)
    assert command_shell(cmd, sudo=True) == "sudo mv /tmp/testfile /tmp2"
    assert command_shell(cmd, su=True) == "su -c 'mv /tmp/testfile /tmp2'"
    assert (
        command_shell(RemoveTreeCommand(src=FULL_DEST), sudo=True)
        == "sudo rm -rf /tmp2/testfile"
    )


# --- Operation parity with the baseline's files.move fixtures ----------------------


async def test_move_no_conflict_fixture():
    # The baseline's no_conflict.json: src exists, dest is a directory, the
    # target path is absent -> a single mv, always changed (stateless).
    host = make_host(src_stats={"mode": 600}, dest_stats={"mode": 700})
    ctx, commands = await materialize(host, src=SRC, dest=DEST)

    assert ctx.changed
    assert ctx.reason == f"will move {SRC} into {DEST}"
    assert commands == [MoveCommand(src=SRC, dest=DEST)]


async def test_move_directory_fixture():
    # The baseline's directory.json: File(src) is False (a directory, not a
    # regular file) -- only absence is rejected, so the move is planned.
    host = make_host(src_stats=False, dest_stats={"mode": 700})
    host.directory_stats[SRC] = {"mode": 700}
    ctx, commands = await materialize(host, src=SRC, dest=DEST)

    assert ctx.changed
    assert commands == [MoveCommand(src=SRC, dest=DEST)]


async def test_move_link_fixture():
    # The baseline's link.json: a symlink source also reads File(src) = False
    # and is accepted.
    host = make_host(src_stats=False, dest_stats={"mode": 700})
    host.link_stats[SRC] = {"mode": 600, "link_target": "file.txt"}
    ctx, commands = await materialize(host, src=SRC, dest=DEST)

    assert ctx.changed
    assert commands == [MoveCommand(src=SRC, dest=DEST)]


async def test_move_overwrite_fixture():
    # The baseline's overwrite.json: an existing target with overwrite=True
    # yields rm -rf of the full destination path, then the mv.
    host = make_host(
        src_stats={"mode": 600},
        dest_stats={"mode": 700},
        target_stats={"mode": 600},
    )
    ctx, commands = await materialize(host, src=SRC, dest=DEST, overwrite=True)

    assert ctx.changed
    assert ctx.reason == (
        f"{FULL_DEST} present, will remove first; will move {SRC} into {DEST}"
    )
    assert commands == [
        RemoveTreeCommand(src=FULL_DEST),
        MoveCommand(src=SRC, dest=DEST),
    ]


async def test_move_no_overwrite_fixture_raises():
    # The baseline's no_overwrite.json: an existing target without overwrite.
    host = make_host(
        src_stats={"mode": 600},
        dest_stats={"mode": 700},
        target_stats={"mode": 600},
    )
    with pytest.raises(
        ValueError, match=r"dest /tmp2/testfile already exists and `overwrite` is unset"
    ):
        await materialize(host, src=SRC, dest=DEST)


async def test_move_invalid_dest_fixture_raises():
    # The baseline's invalid_dest.json: dest exists but is not a directory.
    host = make_host(src_stats={"mode": 600}, dest_stats=False)
    with pytest.raises(ValueError, match=r"dest /tmp2 is not a directory"):
        await materialize(host, src=SRC, dest=DEST)


async def test_move_absent_src_raises_in_both_phases():
    # The baseline's invalid_src.json logs softly while planning and raises only
    # while executing; reemote is phase-unaware and deliberately raises in
    # both phases (the recorded deviation).
    host = make_host(dest_stats={"mode": 700})
    with pytest.raises(ValueError, match=r"src /tmp/testfile does not exist"):
        await materialize(host, src=SRC, dest=DEST)


async def test_move_absent_dest_directory_raises_in_both_phases():
    # The baseline's missing_dest.json: same phase-aware softness, same
    # deliberate always-raise deviation.
    host = make_host(src_stats={"mode": 600})
    with pytest.raises(
        ValueError, match=r"dest directory /tmp2 does not exist"
    ):
        await materialize(host, src=SRC, dest=DEST)


async def test_move_kwargs_absorb_extra_globals():
    host = make_host(src_stats={"mode": 600}, dest_stats={"mode": 700})
    ctx, commands = await materialize(
        host, src=SRC, dest=DEST, some_extra_global="ignored"
    )
    assert ctx.changed
    assert commands == [MoveCommand(src=SRC, dest=DEST)]


# --- The SimulatedHost seam ----------------------------------------------------


async def test_send_command_move_mutates_the_stores():
    host = SimulatedHost(name="h")
    host.remote_files = {SRC: b"payload\n"}
    host.existing_files = {SRC}
    host.file_stats = {SRC: {"mode": 600}}
    await send_command(host, MoveCommand(src=SRC, dest=DEST))

    assert host.remote_files == {FULL_DEST: b"payload\n"}
    assert host.existing_files == {FULL_DEST}
    assert host.file_stats == {FULL_DEST: {"mode": 600}}


async def test_send_command_remove_tree_mutates_the_stores():
    host = SimulatedHost(name="h")
    host.remote_files = {FULL_DEST: b"old\n"}
    host.existing_files = {FULL_DEST}
    host.file_stats = {FULL_DEST: {"mode": 600}}
    await send_command(host, RemoveTreeCommand(src=FULL_DEST))

    assert host.remote_files == {}
    assert host.existing_files == set()
    assert host.file_stats == {}


# --- End to end through the driver ---------------------------------------------


def install_factory(entry: dict) -> SimulatedHost:
    """A host with a spooled ``/tmp/hosts`` and an existing ``/etc/hosts``."""
    host = SimulatedHost.from_inventory(entry)
    host.remote_files = {
        "/tmp/hosts": b"127.0.0.1 localhost\n10.0.0.5 db.internal db\n",
        "/etc/hosts": b"127.0.0.1 localhost\n",
    }
    host.file_stats = {
        "/tmp/hosts": {"mode": 644},
        "/etc/hosts": {"mode": 644},
    }
    host.directory_stats = {"/etc": {"mode": 755}}
    return host


async def test_move_end_to_end_elevated_plan_and_log():
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.move,
            name="Install hosts file",
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
    assert "will remove first" in format_plan(plans)

    assert report is not None
    log = format_log(report)
    assert "$ sudo rm -rf /etc/hosts" in log
    assert "$ sudo mv /tmp/hosts /etc" in log

    # The spool file landed at /etc/hosts and left no trace at /tmp.
    assert hosts[0].remote_files == {
        "/etc/hosts": b"127.0.0.1 localhost\n10.0.0.5 db.internal db\n"
    }
    assert hosts[0].file_stats == {"/etc/hosts": {"mode": 644}}


async def test_repeating_a_move_is_not_a_no_op():
    # Stateless like the baseline: the second move re-validates against the
    # fresh per-step fact cache, finds the source gone and raises --
    # there is deliberately no idempotent no-op branch.
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(files.move, name="Move", src="/tmp/hosts", dest="/etc",
                 overwrite=True, sudo=True)
        yield op(files.move, name="Re-move", src="/tmp/hosts", dest="/etc",
                 overwrite=True, sudo=True)

    with pytest.raises(ValueError, match=r"src /tmp/hosts does not exist"):
        await run(INVENTORY, deploy, host_factory=install_factory)


# --- The /etc/hosts recipe: edit spools, move installs -------------------------


def desired_hosts(current: str | None) -> str:
    """A fixpoint transform producing the desired hosts-file content.

    The controller-side recipe computes the new content from what
    ``files.get`` fetched; here the desired text is simply a constant,
    which is trivially idempotent.
    """
    return "127.0.0.1 localhost\n10.0.0.5 db.internal db\n"


async def test_edit_then_move_recipe_spans_two_runs():
    def spool_deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.edit,
            name="Spool hosts",
            path="/tmp/hosts",
            transform=desired_hosts,
        )

    def install_deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.move,
            name="Install hosts",
            src="/tmp/hosts",
            dest="/etc",
            overwrite=True,
            sudo=True,
        )

    def factory(entry: dict) -> SimulatedHost:
        host = SimulatedHost.from_inventory(entry)
        host.remote_files = {"/etc/hosts": b"127.0.0.1 localhost\n"}
        host.file_stats = {"/etc/hosts": {"mode": 644}}
        host.directory_stats = {"/etc": {"mode": 755}}
        return host

    # Run 1: the edit creates the writable spool file (unprivileged scp).
    _plans, report, hosts = await run(
        INVENTORY, spool_deploy, host_factory=factory
    )
    assert report is not None
    assert hosts[0].remote_files["/tmp/hosts"] == desired_hosts(None).encode()
    host = hosts[0]

    # The simulated put stores content only; seed the stat row the real
    # filesystem would now have for the spool file.
    host.file_stats["/tmp/hosts"] = {"mode": 644}

    # Run 2: the elevated move installs it over the root-owned target. The
    # factory returns the *same* simulated host so run 1's state carries over.
    _plans, report, hosts = await run(
        INVENTORY, install_deploy, host_factory=lambda entry: host
    )
    assert report is not None
    assert "$ sudo mv /tmp/hosts /etc" in format_log(report)
    assert host.remote_files == {"/etc/hosts": desired_hosts(None).encode()}
    assert "/tmp/hosts" not in host.file_stats


async def test_edit_then_move_in_one_deploy_raises_at_prepare():
    # The documented consequence of the always-raise deviation: prepare
    # never sends, so the spool file the edit would create does not exist
    # when the move materializes -- the recipe must span two runs.
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.edit,
            name="Spool hosts",
            path="/tmp/hosts",
            transform=desired_hosts,
        )
        yield op(
            files.move,
            name="Install hosts",
            src="/tmp/hosts",
            dest="/etc",
            overwrite=True,
            sudo=True,
        )

    def factory(entry: dict) -> SimulatedHost:
        host = SimulatedHost.from_inventory(entry)
        host.directory_stats = {"/etc": {"mode": 755}}
        return host

    with pytest.raises(ValueError, match=r"src /tmp/hosts does not exist"):
        await run(INVENTORY, deploy, host_factory=factory)