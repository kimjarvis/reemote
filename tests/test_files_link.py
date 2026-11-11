# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for ``files.link``: add/update/remove a symbolic or hard link.

Covers the ``LinkCommand`` shell descriptor rendering (``ln [-s]``/``rm -f``,
the quoted target and path, the elevated ``command_shell`` forms) alongside
the ``ChownCommand`` (in its ``-h`` non-dereferencing form), ``MkdirCommand``
and ``RemoveTreeCommand`` descriptors the operation reuses from
``files.file``/``files.directory``, the operation's baseline ``files.link``
fixture parity (create with owner, edit against the ``Link`` fact's metadata,
the target-mismatch ``rm -f`` + ``ln`` relink pair, the
``create_remote_dir`` parent flow, delete, both hard-link branches read
through ``File`` and ``SameFile``, the ``force`` ``rm -rf`` + ``ln`` pair,
and the error branches), the deliberate deviations (the baseline's timestamped
``force_backup``/``force_backup_dir`` mv and the internal ``_no_fail_on_link``
knob are dropped), the ``SimulatedHost`` seam dispatch (both link stores and
``same_files`` stay coherent), and end-to-end plan/log behaviour with
``sudo=True`` including the apply-phase repeat no-op.
"""

from collections.abc import Generator
from pathlib import Path

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
from reemote.modules.dnf.dnf import DnfInstallCommand
from reemote.modules.files.files import (
    ChownCommand,
    LinkCommand,
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

# The baseline's own files.link fixtures address "testlink" pointing at
# "/etc/init.d/nginx" (add_with_spaces uses "test link" and a spaced
# target; edit_nothing the relative target below); the expectations reuse
# those command strings verbatim.
PATH = "testlink"
TARGET = "/etc/init.d/nginx"
SPACED = "test link"
SPACED_TARGET = "/etc/init.d/path with spaces"

# delete.json's row: an existing link whose target differs from the
# (absent) request -- the removal ignores the metadata entirely.
PRESENT_ROW = {
    "link_target": "/etc/init.d/not-nginx",
    "user": "wronguser",
    "group": "wronggroup",
}
# edit.json's row: both the target and both owner halves differ.
WRONG_ROW = PRESENT_ROW
# edit_nothing.json's row: everything already matches the request.
OK_ROW = {"link_target": TARGET, "user": "testuser", "group": "testgroup"}
# The hard-link fixtures' File row: a plain file name that -- together with
# SameFile answering True -- reports as the requested hard link.
HARD_FILE_ROW = {"mode": 644, "user": "testuser", "group": "testgroup"}


def make_host(stats: dict | bool | None = None) -> SimulatedHost:
    """A SimulatedHost seeded with the stat row ``link``'s fact reads.

    Mirrors the baseline's fixture shape: ``Link(path)`` is the idempotence fact
    (``Directory(parent)`` is read only on the create branch, so those tests
    seed it directly). An unset row reads as None (absent), ``False`` names
    an existing path of the wrong type.
    """
    host = SimulatedHost(name="h")
    if stats is not None:
        host.link_stats[PATH] = stats
    return host


def make_hard_link_host(same_file: bool = True) -> SimulatedHost:
    """Seed the three stores the hard-link branch reads: ``Link`` answers
    False (a hard link is not a symlink), ``File`` a row, and ``SameFile``
    whatever the test asks for."""
    host = SimulatedHost(name="h")
    host.link_stats[PATH] = False
    host.file_stats[PATH] = dict(HARD_FILE_ROW)
    if same_file:
        host.same_files = {(PATH, TARGET)}
    return host


async def materialize(host: SimulatedHost, **kwargs) -> tuple[OpContext, list]:
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.link(ctx, **kwargs)]
    return ctx, commands


# --- Descriptor rendering -----------------------------------------------------


def test_link_command_renders_ln_or_rm():
    assert LinkCommand(path=PATH, target=TARGET).shell() == f"ln -s {TARGET} testlink"
    assert (
        LinkCommand(path=PATH, target=TARGET, symbolic=False).shell()
        == f"ln {TARGET} testlink"
    )
    assert LinkCommand(path=PATH, target=None).shell() == "rm -f testlink"
    # The removal is the same descriptor with no target, not a flag.
    assert LinkCommand(path=PATH).target is None


def test_link_command_quotes_paths_but_never_the_flags():
    # The baseline's add_with_spaces.json: both arguments are quoted when they
    # need it; a safe path stays bare.
    assert LinkCommand(path=SPACED, target=TARGET).shell() == (
        f"ln -s {TARGET} 'test link'"
    )
    assert LinkCommand(path=PATH, target=SPACED_TARGET).shell() == (
        "ln -s '/etc/init.d/path with spaces' testlink"
    )
    assert LinkCommand(path=SPACED, target=None).shell() == "rm -f 'test link'"


def test_command_shell_elevates_the_link_commands():
    # The link operation reuses the directory operation's metadata
    # descriptors; ``dereference=False`` is the link's own ``chown -h`` form.
    assert command_shell(LinkCommand(path=PATH, target=TARGET), sudo=True) == (
        f"sudo ln -s {TARGET} testlink"
    )
    assert command_shell(LinkCommand(path=PATH), sudo=True) == "sudo rm -f testlink"
    assert (
        ChownCommand(
            path=PATH, user="testuser", group="testgroup", dereference=False
        ).shell()
        == "chown -h testuser:testgroup testlink"
    )
    assert command_shell(
        ChownCommand(path=PATH, user="testuser", group="testgroup", dereference=False),
        sudo=True,
    ) == "sudo chown -h testuser:testgroup testlink"
    assert command_shell(
        ChownCommand(path=PATH, user="testuser"), su=True
    ) == "su -c 'chown testuser testlink'"
    assert command_shell(MkdirCommand(path="/home"), sudo=True) == "sudo mkdir -p /home"
    assert command_shell(RemoveTreeCommand(src=PATH), sudo=True) == "sudo rm -rf testlink"


def test_link_command_defaults_to_symbolic():
    # The dataclass default keeps the symlink form the short one.
    assert LinkCommand(path=PATH, target=TARGET).symbolic is True
    assert LinkCommand(path=PATH, target=TARGET) != LinkCommand(
        path=PATH, target=TARGET, symbolic=False
    )


# --- Operation parity with the baseline's files.link fixtures -----------------------


async def test_link_add_fixture():
    # The baseline's add.json: an absent path with user/group creates the link
    # and chowns it with -h -- no parent probe (a bare "testlink" has no
    # dirname to check) and no plain chown of the target.
    host = make_host()
    ctx, commands = await materialize(
        host, path=PATH, target=TARGET, user="testuser", group="testgroup"
    )

    assert ctx.changed
    assert ctx.reason == "testlink missing, will create"
    assert [cmd.shell() for cmd in commands] == [
        "ln -s /etc/init.d/nginx testlink",
        "chown -h testuser:testgroup testlink",
    ]
    assert commands == [
        LinkCommand(path=PATH, target=TARGET),
        ChownCommand(path=PATH, user="testuser", group="testgroup", dereference=False),
    ]


async def test_link_add_no_chown_without_owner_request():
    # No user and no group requested: the create branch yields the ln only.
    host = make_host()
    ctx, commands = await materialize(host, path=PATH, target=TARGET)

    assert ctx.changed
    assert commands == [LinkCommand(path=PATH, target=TARGET)]


async def test_link_add_with_spaces_fixture():
    # The baseline's add_with_spaces.json: both arguments are quoted as needed.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, path=SPACED, target=SPACED_TARGET, user="testuser", group="testgroup"
    )

    assert ctx.changed
    assert [cmd.shell() for cmd in commands] == [
        "ln -s '/etc/init.d/path with spaces' 'test link'",
        "chown -h testuser:testgroup 'test link'",
    ]


async def test_link_add_no_target_raises():
    # The baseline's add_no_target.json: present=True without a target is a
    # validation error before any fact is read (the baseline's OperationError
    # spells as ValueError here).
    host = make_host()
    with pytest.raises(ValueError, match="If present is True target must be provided"):
        await materialize(host, path=PATH, user="testuser", group="testgroup")


async def test_link_create_directory_fixture():
    # The baseline's create_directory.json: the parent directory is absent, so
    # mkdir -p runs before the ln (the fixture's single fact read is the
    # default create_remote_dir=True).
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, path="/home/testlink", target=TARGET, create_remote_dir=True
    )

    assert ctx.changed
    assert ctx.reason == (
        "/home/testlink missing, will create; parent /home missing, will create"
    )
    assert [cmd.shell() for cmd in commands] == [
        "mkdir -p /home",
        "ln -s /etc/init.d/nginx /home/testlink",
    ]
    assert commands == [
        MkdirCommand(path="/home"),
        LinkCommand(path="/home/testlink", target=TARGET),
    ]


async def test_link_create_remote_dir_chowns_a_fresh_parent():
    # The baseline's delegated files.directory runs its create branch when the
    # parent is missing, which chowns -- but never chmods -- the new
    # directory when an owner was requested. The parent is a directory, so
    # that chown dereferences; only the link's own chown carries -h.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, path="/srv/app/current", target="/srv/app/releases/v2", user="www", group="www"
    )

    assert ctx.changed
    assert ctx.reason == (
        "/srv/app/current missing, will create; parent /srv/app missing, will create"
    )
    assert [cmd.shell() for cmd in commands] == [
        "mkdir -p /srv/app",
        "chown www:www /srv/app",
        "ln -s /srv/app/releases/v2 /srv/app/current",
        "chown -h www:www /srv/app/current",
    ]


async def test_link_create_remote_dir_false_skips_the_probe():
    # The baseline's create_remote_dir=False: no parent read, no mkdir -- the ln
    # simply fails on a missing parent.
    host = make_host()
    ctx, commands = await materialize(
        host, path="/home/testlink", target=TARGET, create_remote_dir=False
    )

    assert ctx.changed
    assert ctx.reason == "/home/testlink missing, will create"
    assert commands == [LinkCommand(path="/home/testlink", target=TARGET)]


async def test_link_existing_parent_is_left_alone():
    # The baseline delegates with _no_check_owner_mode: an existing parent is
    # not mkdir'd, not chmod'd and not chown'd -- only the link is. Call
    # files.directory separately to manage the parent.
    host = SimulatedHost(name="h")
    host.directory_stats["/home"] = {"mode": 700, "user": "root", "group": "root"}
    ctx, commands = await materialize(
        host, path="/home/testlink", target=TARGET, user="www", group="www"
    )

    assert ctx.changed
    assert ctx.reason == "/home/testlink missing, will create"
    assert [cmd.shell() for cmd in commands] == [
        "ln -s /etc/init.d/nginx /home/testlink",
        "chown -h www:www /home/testlink",
    ]


async def test_link_parent_that_is_not_a_directory_raises():
    # The baseline's internal _no_fail_on_link knob is deliberately dropped, so
    # a non-directory parent raises rather than being tolerated.
    host = SimulatedHost(name="h")
    host.directory_stats["/home"] = False
    with pytest.raises(ValueError, match=r"/home exists and is not a directory"):
        await materialize(host, path="/home/testlink", target=TARGET)


async def test_link_delete_fixture():
    # The baseline's delete.json: present=False on an existing link removes it
    # with rm -f, whatever target and owner the row carries.
    host = make_host(PRESENT_ROW)
    ctx, commands = await materialize(host, path=PATH, present=False)

    assert ctx.changed
    assert ctx.reason == "testlink present, will remove"
    assert commands == [LinkCommand(path=PATH, target=None)]
    assert commands[0].shell() == "rm -f testlink"


async def test_link_remove_absent_is_a_no_op():
    # The baseline's noop branch: present=False on an absent path reports the
    # The baseline wording, unchanged and with no command.
    host = make_host()
    ctx, commands = await materialize(host, path=PATH, present=False)

    assert not ctx.changed
    assert commands == []
    assert ctx.reason == "link testlink does not exist"


async def test_link_edit_fixture():
    # The baseline's edit.json: the target differs, so the link is removed and
    # recreated (the baseline's "remove it and recreate a new one" rule -- never
    # ``ln -sf``), then the owner is edited with chown -h.
    host = make_host(WRONG_ROW)
    ctx, commands = await materialize(
        host, path=PATH, target=TARGET, user="testuser", group="testgroup"
    )

    assert ctx.changed
    assert ctx.reason == (
        "target /etc/init.d/not-nginx differs, will relink; "
        "owner wronguser:wronggroup differs, will chown"
    )
    assert [cmd.shell() for cmd in commands] == [
        "rm -f testlink",
        "ln -s /etc/init.d/nginx testlink",
        "chown -h testuser:testgroup testlink",
    ]
    assert commands == [
        LinkCommand(path=PATH, target=None),
        LinkCommand(path=PATH, target=TARGET),
        ChownCommand(path=PATH, user="testuser", group="testgroup", dereference=False),
    ]


async def test_link_edit_nothing_fixture():
    # The baseline's edit_nothing.json: target and both owner halves already
    # match, so nothing is yielded and the no-op wording is the baseline's.
    host = SimulatedHost(name="h")
    host.link_stats["/etc/rc.d/nginx"] = {
        "link_target": "../init.d/nginx",
        "user": "testuser",
        "group": "testgroup",
    }
    ctx, commands = await materialize(
        host,
        path="/etc/rc.d/nginx",
        target="../init.d/nginx",
        user="testuser",
        group="testgroup",
    )

    assert not ctx.changed
    assert commands == []
    assert ctx.reason == "link /etc/rc.d/nginx already exists"


async def test_link_edit_owner_only_fixture():
    # The owner branch alone: the target matches, so no relink pair runs
    # and only the chown -h does.
    host = make_host(OK_ROW)
    ctx, commands = await materialize(
        host, path=PATH, target=TARGET, user="other", group="testgroup"
    )

    assert ctx.changed
    assert ctx.reason == "owner testuser:testgroup differs, will chown"
    assert commands == [
        ChownCommand(path=PATH, user="other", group="testgroup", dereference=False)
    ]


async def test_link_edit_trailing_slash_fixture():
    # The baseline's edit_trailing_slash.json: "target/" is not "target", so the
    # link is relinked -- the comparison is verbatim, never normalized.
    host = make_host({"link_target": "target/", "user": "root", "group": "root"})
    ctx, commands = await materialize(host, path=PATH, target="target")

    assert ctx.changed
    assert [cmd.shell() for cmd in commands] == ["rm -f testlink", "ln -s target testlink"]


async def test_link_dangling_target_row_still_relinks():
    # The ls-fallback row of a dangling link carries no link_target; the
    # .get comparison treats that as a difference rather than raising.
    host = make_host({"user": "root", "group": "root"})
    ctx, commands = await materialize(host, path=PATH, target=TARGET)

    assert ctx.changed
    assert ctx.reason == "target None differs, will relink"
    assert commands == [
        LinkCommand(path=PATH, target=None),
        LinkCommand(path=PATH, target=TARGET),
    ]


async def test_link_delete_hard_link_fixture():
    # The baseline's delete_hard_link.json: Link answers False for a hard link,
    # so the path is re-read through File and matched to the target with
    # SameFile -- present=False then removes the name with rm -f.
    host = make_hard_link_host()
    ctx, commands = await materialize(
        host, path=PATH, target=TARGET, symbolic=False, present=False
    )

    assert ctx.changed
    assert ctx.reason == "testlink present, will remove"
    assert commands == [LinkCommand(path=PATH, target=None)]
    assert commands[0].shell() == "rm -f testlink"


async def test_link_edit_hard_link_nothing_fixture():
    # The baseline's edit_hard_link_nothing.json: File's row plus SameFile's
    # True means the hard link already is exactly what was asked for --
    # the same-inode reading is what the readlink probe alone cannot make.
    host = make_hard_link_host()
    ctx, commands = await materialize(
        host, path=PATH, target=TARGET, symbolic=False, user="testuser", group="testgroup"
    )

    assert not ctx.changed
    assert commands == []
    assert ctx.reason == "link testlink already exists"


async def test_link_hard_link_metadata_edit_chowns_the_file_name():
    # The hard-link info is File's row, so an owner difference chowns it --
    # without -h semantics that would be meaningless: there is no symlink,
    # and the simulated chown lands in the file stores (see the seam test).
    host = make_hard_link_host()
    host.file_stats[PATH] = {"mode": 644, "user": "other", "group": "other"}
    ctx, commands = await materialize(
        host, path=PATH, target=TARGET, symbolic=False, user="testuser", group="testgroup"
    )

    assert ctx.changed
    assert ctx.reason == "owner other:other differs, will chown"
    assert commands == [
        ChownCommand(path=PATH, user="testuser", group="testgroup", dereference=False)
    ]


async def test_link_symbolic_true_never_reads_the_hard_link_pair():
    # The hard-link branch requires symbolic=False: with the default, a
    # False Link row means "not a link" and raises -- File and SameFile
    # are never consulted.
    host = make_hard_link_host()
    with pytest.raises(ValueError, match=r"testlink exists and is not a link"):
        await materialize(host, path=PATH, target=TARGET)


async def test_link_invalid_fixture_raises():
    # The baseline's invalid.json: an existing non-link without force raises
    # (the baseline's OperationError spells as ValueError here).
    host = make_host(False)
    with pytest.raises(ValueError, match=r"testlink exists and is not a link"):
        await materialize(
            host, path=PATH, target=TARGET, user="testuser", group="testgroup"
        )


async def test_link_invalid_hard_link_fixture_raises():
    # The baseline's invalid_hard_link.json: a regular file that is NOT the
    # target's inode raises the same error even with symbolic=False --
    # SameFile answering False keeps the "is not a link" verdict.
    host = make_hard_link_host(same_file=False)
    with pytest.raises(ValueError, match=r"testlink exists and is not a link"):
        await materialize(
            host, path=PATH, target=TARGET, symbolic=False, user="testuser",
            group="testgroup",
        )


async def test_link_invalid_type_fixture():
    # The baseline's invalid_type.json: a non-string, non-PathLike path.
    host = make_host()
    with pytest.raises(
        TypeError, match=r"`path` must be a string or `os.PathLike` object"
    ):
        await materialize(host, path=["testlink", "anotherlink"], target=TARGET)


async def test_link_pathlib_edit_fixture():
    # The baseline's pathlib_edit.json: a PathLike path is fspath'd, the fact
    # keyed on the string form, and the edit branch behaves identically.
    host = make_host(WRONG_ROW)
    ctx, commands = await materialize(
        host, path=Path(PATH), target=TARGET, user="testuser", group="testgroup"
    )

    assert ctx.changed
    assert [cmd.shell() for cmd in commands] == [
        "rm -f testlink",
        "ln -s /etc/init.d/nginx testlink",
        "chown -h testuser:testgroup testlink",
    ]


async def test_link_pathlib_target_is_fspathd_too():
    # The `target` argument is os.fspath-normalized exactly like `path`:
    # a PathLike target flows into the issued commands as its string form.
    host = make_host(WRONG_ROW)
    ctx, commands = await materialize(
        host, path=PATH, target=Path(TARGET), user="testuser", group="testgroup"
    )

    assert ctx.changed
    assert [cmd.shell() for cmd in commands] == [
        "rm -f testlink",
        "ln -s /etc/init.d/nginx testlink",
        "chown -h testuser:testgroup testlink",
    ]


async def test_link_invalid_target_type_raises():
    # A non-string, non-PathLike target is rejected by the operation up
    # front, not by an opaque TypeError from shlex.quote's depths.
    host = make_host()
    with pytest.raises(
        TypeError, match=r"`target` must be a string or `os.PathLike` object"
    ):
        await materialize(
            host, path=PATH, target=["/etc/init.d/nginx", "other"]
        )


async def test_link_add_force_no_backup_fixture():
    # The baseline's add_force_no_backup.json -- and, deliberately, exactly what
    # reemote's plain ``force=True`` does: the baseline's default backup form
    # ``mv``s to a datetime.now()-stamped name, which would differ between
    # the prepare and apply phases and break the determinism guard, so the
    # timestamped force_backup/force_backup_dir are dropped and force
    # always removes (the recorded deviation).
    host = make_host(False)
    ctx, commands = await materialize(host, path=PATH, target=TARGET, force=True)

    assert ctx.changed
    assert ctx.reason == (
        "testlink is not a link, will remove first; testlink missing, will create"
    )
    assert commands == [RemoveTreeCommand(src=PATH), LinkCommand(path=PATH, target=TARGET)]
    assert [cmd.shell() for cmd in commands] == [
        "rm -rf testlink",
        "ln -s /etc/init.d/nginx testlink",
    ]


async def test_link_force_absorbs_the_dropped_backup_knobs():
    # add_force.json/add_force_backup_dir.json assert the timestamped mv
    # that reemote never emits; the options are absorbed by **kwargs and
    # force still takes the deterministic rm -rf form.
    host = make_host(False)
    ctx, commands = await materialize(
        host,
        path=PATH,
        target=TARGET,
        force=True,
        force_backup=True,
        force_backup_dir="/tmp/elsewhere",
    )

    assert ctx.changed
    assert commands == [RemoveTreeCommand(src=PATH), LinkCommand(path=PATH, target=TARGET)]
    assert [cmd.shell() for cmd in commands] == [
        "rm -rf testlink",
        "ln -s /etc/init.d/nginx testlink",
    ]


async def test_link_remove_after_force_still_removes():
    # The baseline's interaction kept: force on a non-link with present=False
    # yields the rm -rf and then reports nothing left to do.
    host = make_host(False)
    ctx, commands = await materialize(host, path=PATH, present=False, force=True)

    assert ctx.changed
    assert commands == [RemoveTreeCommand(src=PATH)]
    assert ctx.reason == (
        "testlink is not a link, will remove first; link testlink does not exist"
    )


async def test_link_kwargs_absorb_extra_globals():
    # **kwargs swallows the baseline's global arguments and the dropped backup
    # knobs alike.
    host = make_host()
    ctx, commands = await materialize(
        host,
        path=PATH,
        target=TARGET,
        some_extra_global="ignored",
        force_backup=True,
    )

    assert ctx.changed
    assert commands == [LinkCommand(path=PATH, target=TARGET)]


# --- The SimulatedHost seam ----------------------------------------------------


async def test_send_command_symlink_seeds_both_link_stores():
    # The stat-level Link fact and the cheap FileLink readlink probe must
    # agree, or a repeat ensure would read contradictory state.
    host = SimulatedHost(name="h")
    await send_command(host, LinkCommand(path="/etc/foo", target="/etc/bar"))

    assert host.links == {"/etc/foo": "/etc/bar"}
    assert host.link_stats == {
        "/etc/foo": {"mode": 777, "user": "root", "group": "root", "link_target": "/etc/bar"}
    }
    assert host.same_files == set()


async def test_send_command_hard_link_moves_the_state_to_the_file_stores():
    # ln without -s adds a name for the target's inode: no symlink store,
    # the same_files pair, a copied file_stats row -- and the typed
    # Link/Directory answers a real stat of a hard link reports ("-", so
    # False: exists, wrong type), which is exactly what files.link's
    # hard-link idempotence branch keys on.
    host = SimulatedHost(name="h")
    host.file_stats["/etc/bar"] = {"mode": 600, "user": "www", "group": "www"}
    await send_command(
        host, LinkCommand(path="/etc/foo", target="/etc/bar", symbolic=False)
    )

    assert host.links == {}
    assert host.link_stats == {"/etc/foo": False}
    assert host.directory_stats == {"/etc/foo": False}
    assert host.same_files == {("/etc/foo", "/etc/bar")}
    assert "/etc/foo" in host.existing_files
    assert host.file_stats["/etc/foo"] == {"mode": 600, "user": "www", "group": "www"}


async def test_send_command_rm_f_link_clears_every_link_store():
    host = SimulatedHost(name="h")
    host.links = {"/etc/foo": "/etc/bar"}
    host.link_stats = {
        "/etc/foo": {"mode": 777, "user": "root", "group": "root", "link_target": "/etc/bar"}
    }
    await send_command(host, LinkCommand(path="/etc/foo"))

    assert host.links == {}
    assert host.link_stats == {}


async def test_send_command_rm_f_hard_link_clears_the_file_side():
    # rm -f drops one name of the inode, never the inode itself: the path's
    # own stores go while the target keeps its row.
    host = SimulatedHost(name="h")
    host.same_files = {("/etc/foo", "/etc/bar")}
    host.existing_files = {"/etc/foo"}
    host.file_stats = {
        "/etc/foo": {"mode": 600, "user": "www", "group": "www"},
        "/etc/bar": {"mode": 600, "user": "www", "group": "www"},
    }
    await send_command(host, LinkCommand(path="/etc/foo", target=None))

    assert host.same_files == set()
    assert host.existing_files == set()
    assert host.file_stats == {"/etc/bar": {"mode": 600, "user": "www", "group": "www"}}


async def test_send_command_force_remove_clears_every_store():
    # force removes through the shared RemoveTreeCommand, which clears
    # every typed store for the path -- link stores included.
    host = SimulatedHost(name="h")
    host.links = {"/srv/web": "/elsewhere"}
    host.link_stats = {"/srv/web": {"mode": 777}}
    host.file_stats = {"/srv/web": {"mode": 644}}
    host.existing_files = {"/srv/web"}
    host.same_files = {("/srv/web", "/elsewhere")}
    await send_command(host, RemoveTreeCommand(src="/srv/web"))

    assert host.links == {}
    assert host.link_stats == {}
    assert host.file_stats == {}
    assert host.existing_files == set()
    assert host.same_files == set()


async def test_send_command_chown_h_edits_only_the_link_row():
    # chown -h owns the link, never its target: the symlink's own row
    # changes while the target file's row stays untouched.
    host = SimulatedHost(name="h")
    host.link_stats["/etc/foo"] = {"mode": 777, "user": "root", "group": "root", "link_target": "/etc/bar"}
    host.file_stats["/etc/bar"] = {"mode": 600, "user": "root", "group": "root"}
    await send_command(
        host, ChownCommand(path="/etc/foo", user="www", group="www", dereference=False)
    )

    assert host.link_stats["/etc/foo"]["user"] == "www"
    assert host.link_stats["/etc/foo"]["group"] == "www"
    assert host.file_stats["/etc/bar"] == {"mode": 600, "user": "root", "group": "root"}


async def test_send_command_chown_hard_link_falls_to_the_file_stores():
    # A hard link's link_stats row is the False wrong-type answer, not a
    # dict, so -h ownership lives with the file stores after all -- exactly
    # what the operation's hard-link info will re-read through File.
    host = SimulatedHost(name="h")
    host.link_stats["/etc/foo"] = False
    host.file_stats["/etc/foo"] = {"mode": 644, "user": "root", "group": "root"}
    await send_command(
        host, ChownCommand(path="/etc/foo", user="www", dereference=False)
    )

    assert host.link_stats["/etc/foo"] is False
    assert host.file_stats["/etc/foo"] == {"mode": 644, "user": "www", "group": "root"}


async def test_install_packages_seeds_the_link_row():
    # A package that drops a link must seed the Link seam too -- files.link
    # now stats the path rather than only readlinking it.
    host = SimulatedHost(
        name="h", installs_links={"nginx": {"/etc/nginx/sites-enabled/default": "/t"}}
    )
    await send_command(host, DnfInstallCommand(packages=("nginx",)))

    assert host.links["/etc/nginx/sites-enabled/default"] == "/t"
    assert host.link_stats["/etc/nginx/sites-enabled/default"] == {
        "mode": 777,
        "user": "root",
        "group": "root",
        "link_target": "/t",
    }


# --- End to end through the driver ---------------------------------------------


def empty_factory(entry: dict) -> SimulatedHost:
    """A host with no /srv at all (neither the directory nor the link)."""
    return SimulatedHost.from_inventory(entry)


async def test_link_end_to_end_elevated_plan_and_log():
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.link,
            name="Ensure link",
            path="/srv/link",
            target="/srv/target",
            user="www",
            group="www",
            sudo=True,
        )

    plans, report, hosts = await run(INVENTORY, deploy, host_factory=empty_factory)
    (planned,) = plans["web1"]
    assert planned.changed
    assert "will create" in format_plan(plans)

    assert report is not None
    log = format_log(report)
    assert "$ sudo mkdir -p /srv" in log
    assert "$ sudo chown www:www /srv" in log
    assert "$ sudo ln -s /srv/target /srv/link" in log
    assert "$ sudo chown -h www:www /srv/link" in log

    # The whole command landed coherently in the link seams: the second
    # ensure reads the row back and finds nothing to do.
    assert hosts[0].link_stats == {
        "/srv/link": {"mode": 777, "user": "www", "group": "www", "link_target": "/srv/target"}
    }
    assert hosts[0].links == {"/srv/link": "/srv/target"}
    assert hosts[0].directory_stats["/srv"] == {
        "mode": 755,
        "user": "www",
        "group": "www",
    }

    # The row the run left behind answers the same request as a no-op.
    ctx, second = await materialize(
        hosts[0], path="/srv/link", target="/srv/target", user="www", group="www"
    )
    assert not ctx.changed
    assert second == []
    assert ctx.reason == "link /srv/link already exists"


async def test_link_repeat_is_a_no_op_in_the_apply_log():
    # The prepare cache persists across the host's requests and prepare
    # never sends, so BOTH identical requests show changed in the plan;
    # only the apply log (fresh per-step cache) shows the second as a
    # no-op that sent nothing.
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(files.link, name="Ensure link", path="/srv/link", target="/srv/target")
        yield op(
            files.link,
            name="Ensure link again",
            path="/srv/link",
            target="/srv/target",
        )

    plans, report, _hosts = await run(INVENTORY, deploy, host_factory=empty_factory)
    assert all(planned.changed for planned in plans["web1"])

    assert report is not None
    log = format_log(report)
    assert log.count("$ ln -s /srv/target /srv/link") == 1
    assert not report.log[1].changed


async def test_link_hard_link_repeat_is_a_no_op_in_the_apply_log():
    # Hard-link idempotence end to end: make_link leaves the False typed
    # answers for the path behind, so the second ensure walks the
    # Link -> File + SameFile branch and sends nothing.
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.link,
            name="Ensure hard link",
            path="/srv/link",
            target="/srv/target",
            symbolic=False,
        )
        yield op(
            files.link,
            name="Ensure hard link again",
            path="/srv/link",
            target="/srv/target",
            symbolic=False,
        )

    plans, report, hosts = await run(INVENTORY, deploy, host_factory=empty_factory)
    # The prepare cache persists across requests and never sends, so both
    # identical requests show changed in the plan.
    assert all(planned.changed for planned in plans["web1"])

    assert report is not None
    log = format_log(report)
    # Step one creates the missing parent and links once; step two is the
    # no-op the wrong-type False answers make possible.
    assert log.count("$ mkdir -p /srv") == 1
    assert log.count("$ ln /srv/target /srv/link") == 1
    assert not report.log[1].changed

    # The state the run left behind is what the no-op keyed on: no symlink
    # store, the recorded same-inode pair, and the False Link/Directory rows.
    assert hosts[0].links == {}
    assert hosts[0].link_stats == {"/srv/link": False}
    assert hosts[0].same_files == {("/srv/link", "/srv/target")}


async def test_link_repoint_end_to_end():
    # An existing link to the wrong target is removed and recreated in one
    # step, and the second ensure (fresh cache over the new state) no-ops.
    def factory(entry: dict) -> SimulatedHost:
        host = SimulatedHost.from_inventory(entry)
        host.link_stats["/srv/link"] = {
            "mode": 777,
            "user": "root",
            "group": "root",
            "link_target": "/srv/old",
        }
        host.links["/srv/link"] = "/srv/old"
        return host

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(files.link, name="Repoint link", path="/srv/link", target="/srv/new")

    plans, report, hosts = await run(INVENTORY, deploy, host_factory=factory)
    assert plans["web1"][0].changed

    assert report is not None
    log = format_log(report)
    assert "$ rm -f /srv/link" in log
    assert "$ ln -s /srv/new /srv/link" in log
    assert hosts[0].link_stats["/srv/link"]["link_target"] == "/srv/new"
    assert hosts[0].links == {"/srv/link": "/srv/new"}


async def test_link_remove_end_to_end():
    def factory(entry: dict) -> SimulatedHost:
        host = SimulatedHost.from_inventory(entry)
        host.links["/etc/nginx/sites-enabled/default"] = "/etc/nginx/sites-available/default"
        host.link_stats["/etc/nginx/sites-enabled/default"] = {
            "mode": 777,
            "user": "root",
            "group": "root",
            "link_target": "/etc/nginx/sites-available/default",
        }
        return host

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.link,
            name="Remove default site",
            path="/etc/nginx/sites-enabled/default",
            present=False,
            sudo=True,
        )

    plans, report, hosts = await run(INVENTORY, deploy, host_factory=factory)
    assert plans["web1"][0].changed

    assert report is not None
    assert "$ sudo rm -f /etc/nginx/sites-enabled/default" in format_log(report)
    assert hosts[0].links == {}
    assert hosts[0].link_stats == {}


async def test_link_dry_run_sends_nothing():
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(files.link, name="Ensure link", path="/srv/link", target="/srv/target")

    plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=empty_factory, dry=True
    )
    assert plans["web1"][0].changed
    assert report is None
    assert hosts[0].link_stats == {}
    assert hosts[0].links == {}
