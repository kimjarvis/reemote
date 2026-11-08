# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for ``files.directory``: add/remove/update directories.

Covers the ``MkdirCommand``/``ChmodCommand``/``ChownCommand`` shell
descriptor renderings (quoting, the unquoted mode and owner specs,
elevated ``command_shell`` forms), the operation's pyinfra
``files.directory`` fixture parity (create with mode/owner, edit against
the ``Directory`` fact's metadata, delete, the ``force`` ``rm -rf`` +
``mkdir -p`` pair, and the error branches), the deliberate deviations
(pyinfra's timestamped ``force_backup``/``force_backup_dir`` mv and the
internal ``_no_fail_on_link``/``_no_check_owner_mode`` knobs are
dropped), the accepted mode spellings, the ``SimulatedHost`` seam
dispatch, and end-to-end plan/log behaviour with ``sudo=True`` including
the apply-phase repeat no-op.
"""

from collections.abc import Generator

import pytest

from reemote5.core import FactCache, OpContext, OperationRequest, op
from reemote5.drivers import (
    command_shell,
    format_log,
    format_plan,
    run,
    send_command,
)
from reemote5.hosts import Host
from reemote5.modules import files
from reemote5.modules.files.files import (
    ChmodCommand,
    ChownCommand,
    MkdirCommand,
    RemoveTreeCommand,
)
from tests.simulated_host import SimulatedHost

INVENTORY = [
    {
        "host": "web1",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
]

# pyinfra's own files.directory fixtures address "testdir" (and the
# add_with_spaces/edit_with_spaces pair addresses "test dir"); the
# expectations below reuse those command strings verbatim.
PATH = "testdir"
SPACED = "test dir"

OWNER_ROW = {"mode": 0, "user": "testuser", "group": "testgroup"}


def make_host(stats: dict | bool | None = None) -> SimulatedHost:
    """A SimulatedHost seeded with the stat row ``directory``'s fact reads.

    Mirrors pyinfra's fixture shape: ``Directory(path)`` is the one idempotence
    fact; an unset row reads as None (absent), ``False`` names an existing
    non-directory.
    """
    host = SimulatedHost(name="h")
    if stats is not None:
        host.directory_stats[PATH] = stats
    return host


async def materialize(host: SimulatedHost, **kwargs) -> tuple[OpContext, list]:
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.directory(ctx, **kwargs)]
    return ctx, commands


# --- Descriptor rendering -----------------------------------------------------


def test_mkdir_command_renders_quoted_path():
    assert MkdirCommand(path=PATH).shell() == "mkdir -p testdir"
    assert MkdirCommand(path=SPACED).shell() == "mkdir -p 'test dir'"


def test_chmod_command_renders_mode_unquoted():
    # pyinfra's chmod helper passes the mode through unquoted (it is the
    # normalised octal-digit int, or a symbolic string); only the target
    # is quoted.
    assert ChmodCommand(path=PATH, mode=777).shell() == "chmod 777 testdir"
    assert (
        ChmodCommand(path=PATH, mode=777, recursive=True).shell()
        == "chmod -R 777 testdir"
    )
    assert (
        ChmodCommand(path=SPACED, mode=777, recursive=True).shell()
        == "chmod -R 777 'test dir'"
    )
    assert ChmodCommand(path="/tmp/d", mode="u+x").shell() == "chmod u+x /tmp/d"


def test_chown_command_renders_chown_or_chgrp():
    # user+group -> "chown user:group", user alone -> "chown user",
    # group alone -> "chgrp group" (pyinfra's chown helper exactly).
    assert (
        ChownCommand(
            path=PATH, user="testuser", group="testgroup", recursive=True
        ).shell()
        == "chown -R testuser:testgroup testdir"
    )
    assert ChownCommand(path=PATH, user="testuser").shell() == "chown testuser testdir"
    assert (
        ChownCommand(path=PATH, group="otheruser", recursive=True).shell()
        == "chgrp -R otheruser testdir"
    )
    assert ChownCommand(path=SPACED, user="u", group="g").shell() == "chown u:g 'test dir'"


def test_chown_command_without_owner_raises():
    with pytest.raises(ValueError, match="at least one of user or group"):
        ChownCommand(path=PATH).shell()


def test_command_shell_elevates_the_directory_commands():
    assert command_shell(MkdirCommand(path=PATH), sudo=True) == "sudo mkdir -p testdir"
    assert (
        command_shell(ChmodCommand(path=PATH, mode=777, recursive=True), sudo=True)
        == "sudo chmod -R 777 testdir"
    )
    assert (
        command_shell(ChownCommand(path=PATH, user="u", group="g"), su=True)
        == "su -c 'chown u:g testdir'"
    )
    assert (
        command_shell(RemoveTreeCommand(src=PATH), sudo=True) == "sudo rm -rf testdir"
    )


# --- Operation parity with pyinfra's files.directory fixtures ------------------


async def test_directory_add_fixture():
    # pyinfra's add.json: an absent path with user/group/mode/recursive
    # creates the tree, then chmods and chowns it recursively.
    host = make_host()
    ctx, commands = await materialize(
        host, path=PATH, user="testuser", group="testgroup", mode=777, recursive=True
    )

    assert ctx.changed
    assert ctx.reason == "testdir missing, will create"
    assert [cmd.shell() for cmd in commands] == [
        "mkdir -p testdir",
        "chmod -R 777 testdir",
        "chown -R testuser:testgroup testdir",
    ]
    assert commands == [
        MkdirCommand(path=PATH),
        ChmodCommand(path=PATH, mode=777, recursive=True),
        ChownCommand(path=PATH, user="testuser", group="testgroup", recursive=True),
    ]


async def test_directory_add_with_spaces_fixture():
    # pyinfra's add_with_spaces.json: only the path is quoted.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, path=SPACED, user="testuser", group="testgroup", mode=777, recursive=True
    )

    assert ctx.changed
    assert [cmd.shell() for cmd in commands] == [
        "mkdir -p 'test dir'",
        "chmod -R 777 'test dir'",
        "chown -R testuser:testgroup 'test dir'",
    ]


async def test_directory_edit_fixture():
    # pyinfra's edit.json: the base directory misses both the requested
    # mode and the requested group, so chmod -R and chgrp -R both run --
    # no user was requested, so the user half never blocks the check.
    host = make_host(OWNER_ROW)
    ctx, commands = await materialize(
        host, path=PATH, mode=777, recursive=True, group="otheruser"
    )

    assert ctx.changed
    assert commands == [
        ChmodCommand(path=PATH, mode=777, recursive=True),
        ChownCommand(path=PATH, group="otheruser", recursive=True),
    ]
    assert [cmd.shell() for cmd in commands] == [
        "chmod -R 777 testdir",
        "chgrp -R otheruser testdir",
    ]


async def test_directory_edit_with_spaces_fixture():
    # pyinfra's edit_with_spaces.json.
    host = SimulatedHost(name="h")
    host.directory_stats[SPACED] = OWNER_ROW
    ctx, commands = await materialize(
        host, path=SPACED, mode=777, recursive=True, group="otheruser"
    )

    assert ctx.changed
    assert [cmd.shell() for cmd in commands] == [
        "chmod -R 777 'test dir'",
        "chgrp -R otheruser 'test dir'",
    ]


async def test_directory_delete_fixture():
    # pyinfra's delete.json: a present directory with present=False is
    # removed recursively (the shared RemoveTreeCommand).
    host = make_host(OWNER_ROW)
    ctx, commands = await materialize(host, path=PATH, present=False)

    assert ctx.changed
    assert ctx.reason == "testdir present, will remove"
    assert commands == [RemoveTreeCommand(src=PATH)]
    assert commands[0].shell() == "rm -rf testdir"


async def test_directory_unchanged_yields_nothing():
    # All three requested values match the fact's metadata: pyinfra's
    # "directory {path} already exists" noop.
    host = make_host({"mode": 777, "user": "testuser", "group": "testgroup"})
    ctx, commands = await materialize(
        host, path=PATH, user="testuser", group="testgroup", mode=777
    )

    assert not ctx.changed
    assert commands == []
    assert ctx.reason == "directory testdir already exists"


async def test_directory_remove_absent_is_a_no_op():
    host = make_host()
    ctx, commands = await materialize(host, path=PATH, present=False)

    assert not ctx.changed
    assert commands == []
    assert ctx.reason == "directory testdir does not exist"


async def test_directory_add_force_no_backup_fixture():
    # pyinfra's add_force_no_backup.json -- and, deliberately, exactly what
    # reemote5's plain ``force=True`` does: pyinfra's default backup form
    # ``mv``s to a datetime.now()-stamped name, which would differ between
    # the prepare and apply phases and break the determinism guard, so the
    # timestamped force_backup/force_backup_dir are dropped and force
    # always removes (the recorded deviation).
    host = make_host(False)
    ctx, commands = await materialize(host, path=PATH, force=True)

    assert ctx.changed
    assert ctx.reason == (
        "testdir is not a directory, will remove first; "
        "testdir missing, will create"
    )
    assert commands == [RemoveTreeCommand(src=PATH), MkdirCommand(path=PATH)]


async def test_directory_remove_after_force_still_removes():
    # pyinfra's interaction kept verbatim: force on a non-directory with
    # present=False yields the rm -rf and then reports nothing left to do.
    host = make_host(False)
    ctx, commands = await materialize(host, path=PATH, present=False, force=True)

    assert ctx.changed
    assert commands == [RemoveTreeCommand(src=PATH)]
    assert ctx.reason == (
        "testdir is not a directory, will remove first; "
        "directory testdir does not exist"
    )


async def test_directory_invalid_fixture_raises():
    # pyinfra's invalid.json: a non-directory without force raises
    # (pyinfra's OperationError spells as ValueError here).
    host = make_host(False)
    with pytest.raises(ValueError, match=r"testdir exists and is not a directory"):
        await materialize(host, path=PATH, mode=777, recursive=True, group="otheruser")


async def test_directory_drops_no_fail_on_link():
    # pyinfra's ignore_link_type.json relied on the internal
    # _no_fail_on_link knob; it is deliberately dropped, so a path held by
    # a link raises instead of no-opping (and the knob is absorbed by
    # **kwargs).
    host = make_host(False)
    host.link_stats[PATH] = {"mode": 777, "link_target": "somedir"}
    with pytest.raises(ValueError, match=r"testdir exists and is not a directory"):
        await materialize(host, path=PATH, _no_fail_on_link=True)


async def test_directory_invalid_type_fixture():
    # pyinfra's invalid_type.json: a non-string, non-PathLike path.
    host = make_host()
    with pytest.raises(
        TypeError, match=r"`path` must be a string or `os.PathLike` object"
    ):
        await materialize(host, path=["testdir", "anotherdir"])


async def test_directory_accepts_all_mode_spellings():
    # pyinfra's ensure_mode_int contract: 750, "750", "0750" and "0o750"
    # all read as the octal-digit int the fact's metadata carries.
    for spelling in (750, "750", "0750", "0o750"):
        host = make_host({"mode": 750, "user": "u", "group": "g"})
        ctx, commands = await materialize(
            host, path=PATH, mode=spelling, user="u", group="g"
        )
        assert not ctx.changed, spelling
        assert commands == [], spelling


async def test_directory_rejects_non_octal_mode():
    host = make_host()
    with pytest.raises(ValueError, match="non-octal digits"):
        await materialize(host, path=PATH, mode="778")


async def test_directory_symbolic_mode_always_chmods():
    # A symbolic mode never equals the fact's octal int, so pyinfra always
    # re-runs the chmod -- and the same spelling is deterministic across
    # the phases.
    host = make_host({"mode": 750, "user": "u", "group": "g"})
    ctx, commands = await materialize(host, path=PATH, mode="u+x")

    assert ctx.changed
    assert commands == [ChmodCommand(path=PATH, mode="u+x")]


async def test_directory_kwargs_absorb_pyinfra_globals():
    # **kwargs swallows pyinfra's global arguments and the dropped
    # _no_check_owner_mode knob alike.
    host = make_host()
    ctx, commands = await materialize(
        host, path=PATH, some_pyinfra_global="ignored", _no_check_owner_mode=True
    )

    assert ctx.changed
    assert commands == [MkdirCommand(path=PATH)]


# --- The SimulatedHost seam ----------------------------------------------------


async def test_send_command_mkdir_seeds_directory_stats():
    # The seeded row is complete (mode and the root:root owner a fresh
    # mkdir leaves) so later owner comparisons never subscribe a KeyError.
    host = SimulatedHost(name="h")
    await send_command(host, MkdirCommand(path="/srv/web"))

    assert host.directory_stats == {
        "/srv/web": {"mode": 755, "user": "root", "group": "root"}
    }


async def test_send_command_mkdir_keeps_existing_row():
    # mkdir -p succeeds silently on an existing directory.
    host = SimulatedHost(name="h")
    host.directory_stats = {"/srv/web": {"mode": 700}}
    await send_command(host, MkdirCommand(path="/srv/web"))

    assert host.directory_stats == {"/srv/web": {"mode": 700}}


async def test_send_command_chmod_rewrites_mode_in_both_stores():
    # chmod addresses files and directories alike.
    host = SimulatedHost(name="h")
    host.file_stats = {"/etc/conf": {"mode": 644}}
    host.directory_stats = {"/srv/web": {"mode": 755}}
    await send_command(host, ChmodCommand(path="/srv/web", mode=750))
    await send_command(host, ChmodCommand(path="/etc/conf", mode="u+x"))

    assert host.directory_stats == {"/srv/web": {"mode": 750}}
    assert host.file_stats == {"/etc/conf": {"mode": "u+x"}}


async def test_send_command_chown_sets_only_requested_halves():
    host = SimulatedHost(name="h")
    host.directory_stats = {"/srv/web": {"mode": 750, "user": "root", "group": "root"}}
    await send_command(host, ChownCommand(path="/srv/web", group="www"))
    assert host.directory_stats["/srv/web"] == {
        "mode": 750,
        "user": "root",
        "group": "www",
    }
    await send_command(host, ChownCommand(path="/srv/web", user="www", group="www"))
    assert host.directory_stats["/srv/web"] == {
        "mode": 750,
        "user": "www",
        "group": "www",
    }


async def test_send_command_remove_tree_drops_directory_and_link_rows():
    # rm -rf clears a file, directory or symlink alike, so every typed
    # stats store must stop answering for the path.
    host = SimulatedHost(name="h")
    host.directory_stats = {"/srv/web": {"mode": 750}}
    host.link_stats = {"/srv/web": False}
    await send_command(host, RemoveTreeCommand(src="/srv/web"))

    assert host.directory_stats == {}
    assert host.link_stats == {}


# --- End to end through the driver ---------------------------------------------


def empty_factory(entry: dict) -> SimulatedHost:
    """A host with no /srv at all (the directory is absent)."""
    return SimulatedHost.from_inventory(entry)


async def test_directory_end_to_end_elevated_plan_and_log():
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.directory,
            name="Ensure web dir",
            path="/srv/web",
            mode="750",
            user="www",
            group="www",
            sudo=True,
        )

    plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=empty_factory
    )
    (planned,) = plans["web1"]
    assert planned.changed
    assert "will create" in format_plan(plans)

    assert report is not None
    log = format_log(report)
    assert "$ sudo mkdir -p /srv/web" in log
    assert "$ sudo chmod 750 /srv/web" in log
    assert "$ sudo chown www:www /srv/web" in log

    # The three simulated commands landed coherently in the Directory seam.
    assert hosts[0].directory_stats == {
        "/srv/web": {"mode": 750, "user": "www", "group": "www"}
    }


async def test_directory_repeat_is_a_no_op_in_the_apply_log():
    # The prepare cache persists across the host's requests and prepare
    # never sends, so BOTH identical requests show changed in the plan;
    # only the apply log (fresh per-step cache) shows the second as a
    # no-op that sent nothing.
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(files.directory, name="Ensure web dir", path="/srv/web", mode="750")
        yield op(files.directory, name="Ensure web dir again", path="/srv/web",
                 mode="750")

    plans, report, _hosts = await run(
        INVENTORY, deploy, host_factory=empty_factory
    )
    assert all(planned.changed for planned in plans["web1"])

    assert report is not None
    log = format_log(report)
    assert log.count("$ mkdir -p /srv/web") == 1
    assert not report.log[1].changed


async def test_directory_force_end_to_end():
    # /srv/web exists as a regular file (the Directory seam answers False);
    # force removes the tree first, then creates -- all elevated.
    def factory(entry: dict) -> SimulatedHost:
        host = SimulatedHost.from_inventory(entry)
        host.directory_stats = {"/srv/web": False}
        host.file_stats = {"/srv/web": {"mode": 644}}
        return host

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.directory,
            name="Force web dir",
            path="/srv/web",
            force=True,
            sudo=True,
        )

    plans, report, hosts = await run(INVENTORY, deploy, host_factory=factory)
    assert plans["web1"][0].changed

    assert report is not None
    log = format_log(report)
    assert "$ sudo rm -rf /srv/web" in log
    assert "$ sudo mkdir -p /srv/web" in log
    assert hosts[0].file_stats == {}
    assert hosts[0].directory_stats == {
        "/srv/web": {"mode": 755, "user": "root", "group": "root"}
    }


async def test_directory_dry_run_sends_nothing():
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(files.directory, name="Ensure web dir", path="/srv/web")

    plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=empty_factory, dry=True
    )
    assert plans["web1"][0].changed
    assert report is None
    assert hosts[0].directory_stats == {}
