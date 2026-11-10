# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for ``files.file``: add/remove/update a regular file.

Covers the ``FileCommand`` shell descriptor rendering (``touch``/``rm -f``,
the quoted path, the elevated ``command_shell`` forms) alongside the
``ChmodCommand``/``ChownCommand``/``MkdirCommand``/``RemoveTreeCommand``
descriptors the operation reuses from ``files.directory``, the operation's
baseline ``files.file`` fixture parity (create with mode/owner, edit against
the ``File`` fact's metadata, the always-rerunning ``touch`` option, the
``create_remote_dir`` parent flow, delete, the ``force`` ``rm -rf`` +
``touch`` pair, and the error branches), the deliberate deviations
(the baseline's timestamped ``force_backup``/``force_backup_dir`` mv and the
internal ``_no_fail_on_link`` knob are dropped), the accepted mode
spellings, the ``SimulatedHost`` seam dispatch (both the ``File`` and
``FileExists`` stores stay coherent), and end-to-end plan/log behaviour with
``sudo=True`` including the apply-phase repeat no-op.
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
from reemote5.modules.dnf.dnf import DnfInstallCommand
from reemote5.modules.files.files import (
    ChmodCommand,
    ChownCommand,
    FileCommand,
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

# The baseline's own files.file fixtures address "testfile" (add_with_spaces and
# delete_with_spaces use "test file", add_special_chars the awkward nested
# path below); the expectations reuse those command strings verbatim.
PATH = "testfile"
SPACED = "test file"
SPECIAL = "test () $-+__file/A-cZ*#*%*#__nonsense.tar.gz"
SPECIAL_PARENT = "test () $-+__file"

# delete.json's row: a present file whose owner differs from nothing
# requested (the removal ignores metadata entirely).
PRESENT_ROW = {"mode": 0, "user": "testuser", "group": "testgroup"}
# edit.json's row: wrong mode and wrong owner against the request.
WRONG_ROW = {"mode": 600, "user": "wronguser", "group": "wronggroup"}
# touch.json's row: only the mode is read back, the owner keys stay absent.
MODE_ONLY_ROW = {"mode": 600}


def make_host(stats: dict | bool | None = None) -> SimulatedHost:
    """A SimulatedHost seeded with the stat row ``file``'s fact reads.

    Mirrors the baseline's fixture shape: ``File(path)`` is the idempotence
    fact (``Directory(parent)`` is read only on the create branch, so those
    tests seed it directly). An unset row reads as None (absent), ``False``
    names an existing path of the wrong type.
    """
    host = SimulatedHost(name="h")
    if stats is not None:
        host.file_stats[PATH] = stats
    return host


async def materialize(host: SimulatedHost, **kwargs) -> tuple[OpContext, list]:
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.file(ctx, **kwargs)]
    return ctx, commands


# --- Descriptor rendering -----------------------------------------------------


def test_file_command_renders_touch_or_rm():
    assert FileCommand(path=PATH).shell() == "touch testfile"
    assert FileCommand(path=PATH, present=False).shell() == "rm -f testfile"
    assert FileCommand(path=SPACED).shell() == "touch 'test file'"
    assert FileCommand(path=SPACED, present=False).shell() == "rm -f 'test file'"
    assert (
        FileCommand(path=SPECIAL).shell()
        == "touch 'test () $-+__file/A-cZ*#*%*#__nonsense.tar.gz'"
    )


def test_file_command_defaults_to_present():
    # The dataclass default keeps the create form the short one; the
    # removal is the same descriptor with present=False.
    assert FileCommand(path=PATH).present is True
    assert FileCommand(path=PATH) == FileCommand(path=PATH, present=True)


def test_command_shell_elevates_the_file_commands():
    # The file operation reuses the directory operation's metadata
    # descriptors, in their non-recursive (single-file) form.
    assert command_shell(FileCommand(path=PATH), sudo=True) == "sudo touch testfile"
    assert (
        command_shell(FileCommand(path=SPACED, present=False), sudo=True)
        == "sudo rm -f 'test file'"
    )
    assert (
        command_shell(ChmodCommand(path=PATH, mode=777), sudo=True)
        == "sudo chmod 777 testfile"
    )
    assert (
        command_shell(ChownCommand(path=PATH, user="u", group="g"), su=True)
        == "su -c 'chown u:g testfile'"
    )
    assert command_shell(RemoveTreeCommand(src=PATH), sudo=True) == "sudo rm -rf testfile"


# --- Operation parity with the baseline's files.file fixtures -----------------------


async def test_file_add_fixture():
    # The baseline's add.json: an absent path with user/group/mode creates the
    # file, then chmods and chowns it -- no parent probe (a bare "testfile"
    # has no dirname to check).
    host = make_host()
    ctx, commands = await materialize(
        host, path=PATH, user="testuser", group="testgroup", mode="777"
    )

    assert ctx.changed
    assert ctx.reason == "testfile missing, will create"
    assert [cmd.shell() for cmd in commands] == [
        "touch testfile",
        "chmod 777 testfile",
        "chown testuser:testgroup testfile",
    ]
    assert commands == [
        FileCommand(path=PATH),
        ChmodCommand(path=PATH, mode=777),
        ChownCommand(path=PATH, user="testuser", group="testgroup"),
    ]


async def test_file_add_with_spaces_fixture():
    # The baseline's add_with_spaces.json: only the path is quoted, never the
    # mode or the owner spec (unless the spec itself needs it).
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, path=SPACED, user="testuser", group="testgroup", mode="777"
    )

    assert ctx.changed
    assert [cmd.shell() for cmd in commands] == [
        "touch 'test file'",
        "chmod 777 'test file'",
        "chown testuser:testgroup 'test file'",
    ]


async def test_file_add_special_chars_fixture():
    # The baseline's add_special_chars.json: the parent directory exists
    # (Directory answers a row), so no mkdir runs and only the awkward path
    # -- quoted -- reaches the three commands.
    host = SimulatedHost(name="h")
    host.directory_stats[SPECIAL_PARENT] = {"mode": 777}
    ctx, commands = await materialize(
        host, path=SPECIAL, user="testuser", group="testgroup", mode="777"
    )

    assert ctx.changed
    assert [cmd.shell() for cmd in commands] == [
        f"touch '{SPECIAL}'",
        f"chmod 777 '{SPECIAL}'",
        f"chown testuser:testgroup '{SPECIAL}'",
    ]


async def test_file_add_user_group_with_metachars_fixture():
    # The baseline's add_user_group_with_metachars.json: the owner spec is
    # injection-shaped and must arrive shell-quoted as one argument.
    host = make_host()
    ctx, commands = await materialize(
        host, path=PATH, user="root; rm -rf /", group="wheel$(id)", mode="777"
    )

    assert ctx.changed
    assert [cmd.shell() for cmd in commands] == [
        "touch testfile",
        "chmod 777 testfile",
        "chown 'root; rm -rf /:wheel$(id)' testfile",
    ]


async def test_file_edit_fixture():
    # The baseline's edit.json: a symbolic mode never equals the fact's octal
    # int, so the chmod always reruns, and both owner halves differ.
    host = make_host(WRONG_ROW)
    ctx, commands = await materialize(
        host, path=PATH, user="testuser", group="testgroup", mode="+x"
    )

    assert ctx.changed
    assert ctx.reason == (
        "mode 600 differs, will chmod +x; "
        "owner wronguser:wronggroup differs, will chown"
    )
    assert [cmd.shell() for cmd in commands] == [
        "chmod +x testfile",
        "chown testuser:testgroup testfile",
    ]
    assert commands == [
        ChmodCommand(path=PATH, mode="+x"),
        ChownCommand(path=PATH, user="testuser", group="testgroup"),
    ]


async def test_file_edit_nothing_fixture():
    # The baseline's edit_nothing.json: every requested value already matches.
    host = make_host({"mode": 777, "user": "testuser", "group": "testgroup"})
    ctx, commands = await materialize(
        host, path=PATH, user="testuser", group="testgroup", mode=777
    )

    assert not ctx.changed
    assert commands == []
    assert ctx.reason == "file testfile already exists"


async def test_file_touch_fixture():
    # The baseline's touch.json (idempotent: false): the touch option always
    # reruns the touch, and the row carries no owner keys -- the owner
    # check short-circuits because neither user nor group was requested.
    host = make_host(MODE_ONLY_ROW)
    ctx, commands = await materialize(host, path=PATH, touch=True)

    assert ctx.changed
    assert ctx.reason == "touch requested, will touch testfile"
    assert commands == [FileCommand(path=PATH)]
    assert commands[0].shell() == "touch testfile"


async def test_file_touch_runs_before_the_metadata_edits():
    # The baseline's order kept: the touch first, then chmod/chown, so the
    # timestamp update cannot be mistaken for a metadata change.
    host = make_host(WRONG_ROW)
    ctx, commands = await materialize(
        host, path=PATH, touch=True, mode=777, user="testuser"
    )

    assert ctx.changed
    assert commands == [
        FileCommand(path=PATH),
        ChmodCommand(path=PATH, mode=777),
        ChownCommand(path=PATH, user="testuser"),
    ]
    assert ctx.reason == (
        "touch requested, will touch testfile; "
        "mode 600 differs, will chmod 777; "
        "owner wronguser:wronggroup differs, will chown"
    )


async def test_file_delete_fixture():
    # The baseline's delete.json: present=False on an existing file removes it.
    host = make_host(PRESENT_ROW)
    ctx, commands = await materialize(host, path=PATH, present=False)

    assert ctx.changed
    assert ctx.reason == "testfile present, will remove"
    assert commands == [FileCommand(path=PATH, present=False)]
    assert commands[0].shell() == "rm -f testfile"


async def test_file_delete_with_spaces_fixture():
    # The baseline's delete_with_spaces.json.
    host = SimulatedHost(name="h")
    host.file_stats[SPACED] = PRESENT_ROW
    ctx, commands = await materialize(host, path=SPACED, present=False)

    assert ctx.changed
    assert commands == [FileCommand(path=SPACED, present=False)]
    assert commands[0].shell() == "rm -f 'test file'"


async def test_file_remove_absent_is_a_no_op():
    host = make_host()
    ctx, commands = await materialize(host, path=PATH, present=False)

    assert not ctx.changed
    assert commands == []
    assert ctx.reason == "file testfile does not exist"


async def test_file_create_directory_fixture():
    # The baseline's create_directory.json: the parent is absent, so mkdir -p
    # runs before the touch.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, path="/home/testfile", create_remote_dir=True
    )

    assert ctx.changed
    assert ctx.reason == (
        "/home/testfile missing, will create; parent /home missing, will create"
    )
    assert [cmd.shell() for cmd in commands] == [
        "mkdir -p /home",
        "touch /home/testfile",
    ]
    assert commands == [MkdirCommand(path="/home"), FileCommand(path="/home/testfile")]


async def test_file_create_remote_dir_choowns_a_fresh_parent():
    # The baseline's delegated files.directory runs its create branch when the
    # parent is missing, which chowns -- but never chmods -- the new
    # directory when an owner was requested.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, path="/srv/app/conf.txt", user="www", group="www", mode="640"
    )

    assert ctx.changed
    assert ctx.reason == (
        "/srv/app/conf.txt missing, will create; "
        "parent /srv/app missing, will create"
    )
    assert [cmd.shell() for cmd in commands] == [
        "mkdir -p /srv/app",
        "chown www:www /srv/app",
        "touch /srv/app/conf.txt",
        "chmod 640 /srv/app/conf.txt",
        "chown www:www /srv/app/conf.txt",
    ]


async def test_file_create_remote_dir_false_skips_the_probe():
    # The baseline's create_remote_dir=False: no parent read, no mkdir -- the
    # touch simply fails on a missing parent.
    host = make_host()
    ctx, commands = await materialize(
        host, path="/home/testfile", create_remote_dir=False
    )

    assert ctx.changed
    assert ctx.reason == "/home/testfile missing, will create"
    assert commands == [FileCommand(path="/home/testfile")]


async def test_file_existing_parent_is_left_alone():
    # The baseline delegates with _no_check_owner_mode: an existing parent is
    # not mkdir'd, not chmod'd and not chown'd -- only the file is. Call
    # files.directory separately to manage the parent.
    host = SimulatedHost(name="h")
    host.directory_stats["/home"] = {"mode": 700, "user": "root", "group": "root"}
    ctx, commands = await materialize(
        host, path="/home/testfile", user="www", group="www", mode="640"
    )

    assert ctx.changed
    assert ctx.reason == "/home/testfile missing, will create"
    assert [cmd.shell() for cmd in commands] == [
        "touch /home/testfile",
        "chmod 640 /home/testfile",
        "chown www:www /home/testfile",
    ]


async def test_file_parent_that_is_not_a_directory_raises():
    # The baseline's internal _no_fail_on_link knob is deliberately dropped, so
    # a non-directory parent raises rather than being tolerated.
    host = SimulatedHost(name="h")
    host.directory_stats["/home"] = False
    with pytest.raises(ValueError, match=r"/home exists and is not a directory"):
        await materialize(host, path="/home/testfile")


async def test_file_add_force_no_backup_fixture():
    # The baseline's add_force_no_backup.json -- and, deliberately, exactly what
    # reemote5's plain ``force=True`` does: the baseline's default backup form
    # ``mv``s to a datetime.now()-stamped name, which would differ between
    # the prepare and apply phases and break the determinism guard, so the
    # timestamped force_backup/force_backup_dir are dropped and force
    # always removes (the recorded deviation).
    host = make_host(False)
    ctx, commands = await materialize(host, path=PATH, force=True)

    assert ctx.changed
    assert ctx.reason == (
        "testfile is not a file, will remove first; testfile missing, will create"
    )
    assert commands == [RemoveTreeCommand(src=PATH), FileCommand(path=PATH)]
    assert [cmd.shell() for cmd in commands] == ["rm -rf testfile", "touch testfile"]


async def test_file_force_absorbs_the_dropped_backup_knobs():
    # add_force.json/add_force_backup_dir*.json assert the timestamped mv
    # that reemote5 never emits; the options are absorbed by **kwargs and
    # force still takes the deterministic rm -rf form.
    host = make_host(False)
    ctx, commands = await materialize(
        host, path=PATH, force=True, force_backup=True, force_backup_dir="/tmp/elsewhere"
    )

    assert ctx.changed
    assert commands == [RemoveTreeCommand(src=PATH), FileCommand(path=PATH)]
    assert [cmd.shell() for cmd in commands] == ["rm -rf testfile", "touch testfile"]


async def test_file_remove_after_force_still_removes():
    # The baseline's interaction kept verbatim: force on a non-file with
    # present=False yields the rm -rf and then reports nothing left to do.
    host = make_host(False)
    ctx, commands = await materialize(host, path=PATH, present=False, force=True)

    assert ctx.changed
    assert commands == [RemoveTreeCommand(src=PATH)]
    assert ctx.reason == (
        "testfile is not a file, will remove first; file testfile does not exist"
    )


async def test_file_invalid_fixture_raises():
    # The baseline's invalid.json: a non-file without force raises (the baseline's
    # OperationError spells as ValueError here).
    host = make_host(False)
    with pytest.raises(ValueError, match=r"testfile exists and is not a file"):
        await materialize(host, path=PATH, user="testuser", group="testgroup", mode=777)


async def test_file_invalid_type_fixture():
    # The baseline's invalid_type.json: a non-string, non-PathLike path.
    host = make_host()
    with pytest.raises(
        TypeError, match=r"`path` must be a string or `os.PathLike` object"
    ):
        await materialize(host, path=["testfile", "anotherfile"])


async def test_file_accepts_all_mode_spellings():
    # The baseline's ensure_mode_int contract: 640, "640", "0640" and "0o640"
    # all read as the octal-digit int the fact's metadata carries.
    for spelling in (640, "640", "0640", "0o640"):
        host = make_host({"mode": 640, "user": "www", "group": "www"})
        ctx, commands = await materialize(
            host, path=PATH, mode=spelling, user="www", group="www"
        )
        assert not ctx.changed, spelling
        assert commands == [], spelling


async def test_file_rejects_non_octal_mode():
    host = make_host()
    with pytest.raises(ValueError, match="non-octal digits"):
        await materialize(host, path=PATH, mode="778")


async def test_file_symbolic_mode_always_chmods():
    # A symbolic mode never equals the fact's octal int, so the baseline always
    # re-runs the chmod -- and the same spelling is deterministic across
    # the phases.
    host = make_host({"mode": 640, "user": "www", "group": "www"})
    ctx, commands = await materialize(host, path=PATH, mode="u+rwX")

    assert ctx.changed
    assert commands == [ChmodCommand(path=PATH, mode="u+rwX")]


async def test_file_kwargs_absorb_extra_globals():
    # **kwargs swallows the baseline's global arguments and the dropped backup
    # knobs alike.
    host = make_host()
    ctx, commands = await materialize(
        host, path=PATH, some_extra_global="ignored", force_backup=True
    )

    assert ctx.changed
    assert commands == [FileCommand(path=PATH)]


# --- The SimulatedHost seam ----------------------------------------------------


async def test_send_command_touch_seeds_both_file_stores():
    # The File fact and the cheaper FileExists probe must agree, or a
    # repeat ensure would read contradictory state.
    host = SimulatedHost(name="h")
    await send_command(host, FileCommand(path="/etc/motd"))

    assert host.existing_files == {"/etc/motd"}
    assert host.file_stats == {
        "/etc/motd": {"mode": 644, "user": "root", "group": "root"}
    }


async def test_send_command_touch_keeps_an_existing_row():
    # touch updates timestamps only, so seeded metadata survives.
    host = SimulatedHost(name="h")
    host.file_stats = {"/etc/motd": {"mode": 600, "user": "root", "group": "root"}}
    await send_command(host, FileCommand(path="/etc/motd"))

    assert host.file_stats == {"/etc/motd": {"mode": 600, "user": "root", "group": "root"}}


async def test_send_command_rm_f_drops_both_file_stores():
    host = SimulatedHost(name="h")
    host.existing_files = {"/etc/motd"}
    host.file_stats = {"/etc/motd": {"mode": 644}}
    await send_command(host, FileCommand(path="/etc/motd", present=False))

    assert host.existing_files == set()
    assert host.file_stats == {}


async def test_send_command_rm_rf_drops_the_file_row():
    # force removes through the shared RemoveTreeCommand, which clears
    # every typed store for the path.
    host = SimulatedHost(name="h")
    host.file_stats = {"/srv/web": {"mode": 644}}
    host.existing_files = {"/srv/web"}
    await send_command(host, RemoveTreeCommand(src="/srv/web"))

    assert host.file_stats == {}
    assert host.existing_files == set()


async def test_install_packages_seeds_the_file_row():
    # A package that drops a config file must seed the File seam too --
    # files.file now stats the path rather than only probing existence.
    host = SimulatedHost(name="h", installs_files={"httpd": {"/etc/httpd/welcome.conf"}})
    await send_command(host, DnfInstallCommand(packages=("httpd",)))

    assert "/etc/httpd/welcome.conf" in host.existing_files
    assert host.file_stats["/etc/httpd/welcome.conf"] == {
        "mode": 644,
        "user": "root",
        "group": "root",
    }


# --- End to end through the driver ---------------------------------------------


def empty_factory(entry: dict) -> SimulatedHost:
    """A host with no /srv at all (neither the directory nor the file)."""
    return SimulatedHost.from_inventory(entry)


async def test_file_end_to_end_elevated_plan_and_log():
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.file,
            name="Ensure marker",
            path="/srv/app/marker",
            mode="640",
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
    assert "$ sudo mkdir -p /srv/app" in log
    assert "$ sudo chown www:www /srv/app" in log
    assert "$ sudo touch /srv/app/marker" in log
    assert "$ sudo chmod 640 /srv/app/marker" in log
    assert "$ sudo chown www:www /srv/app/marker" in log

    # The whole command landed coherently in the File seam: the second
    # ensure reads the row back and finds nothing to do.
    assert hosts[0].file_stats == {
        "/srv/app/marker": {"mode": 640, "user": "www", "group": "www"}
    }
    assert hosts[0].directory_stats["/srv/app"] == {
        "mode": 755,
        "user": "www",
        "group": "www",
    }

    # The row the run left behind answers the same request as a no-op:
    # touch, mkdir and both chowns all landed in the simulated stores.
    ctx, second = await materialize(
        hosts[0],
        path="/srv/app/marker",
        mode="640",
        user="www",
        group="www",
    )
    assert not ctx.changed
    assert second == []


async def test_file_repeat_is_a_no_op_in_the_apply_log():
    # The prepare cache persists across the host's requests and prepare
    # never sends, so BOTH identical requests show changed in the plan;
    # only the apply log (fresh per-step cache) shows the second as a
    # no-op that sent nothing.
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(files.file, name="Ensure marker", path="/srv/marker", mode="640")
        yield op(
            files.file, name="Ensure marker again", path="/srv/marker", mode="640"
        )

    plans, report, _hosts = await run(
        INVENTORY, deploy, host_factory=empty_factory
    )
    assert all(planned.changed for planned in plans["web1"])

    assert report is not None
    log = format_log(report)
    assert log.count("$ touch /srv/marker") == 1
    assert log.count("$ chmod 640 /srv/marker") == 1
    assert not report.log[1].changed


async def test_file_touch_option_repeats_in_the_apply_log():
    # touch=True is deliberately non-idempotent, so the repeat request
    # re-sends the touch even though the file is exactly as asked -- and
    # the metadata half of the second request still no-ops.
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(files.file, name="Touch marker", path="/srv/marker", touch=True)
        yield op(
            files.file, name="Touch marker again", path="/srv/marker", touch=True
        )

    _plans, report, hosts = await run(INVENTORY, deploy, host_factory=empty_factory)

    assert report is not None
    log = format_log(report)
    assert log.count("$ touch /srv/marker") == 2
    assert report.log[1].changed
    assert hosts[0].file_stats == {
        "/srv/marker": {"mode": 644, "user": "root", "group": "root"}
    }


async def test_file_remove_end_to_end():
    def factory(entry: dict) -> SimulatedHost:
        host = SimulatedHost.from_inventory(entry)
        host.existing_files = {"/etc/httpd/conf.d/welcome.conf"}
        host.file_stats = {
            "/etc/httpd/conf.d/welcome.conf": {
                "mode": 644,
                "user": "root",
                "group": "root",
            }
        }
        return host

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.file,
            name="Remove welcome configuration",
            path="/etc/httpd/conf.d/welcome.conf",
            present=False,
            sudo=True,
        )

    plans, report, hosts = await run(INVENTORY, deploy, host_factory=factory)
    assert plans["web1"][0].changed

    assert report is not None
    assert "$ sudo rm -f /etc/httpd/conf.d/welcome.conf" in format_log(report)
    assert hosts[0].file_stats == {}
    assert hosts[0].existing_files == set()


async def test_file_dry_run_sends_nothing():
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(files.file, name="Ensure marker", path="/srv/marker")

    plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=empty_factory, dry=True
    )
    assert plans["web1"][0].changed
    assert report is None
    assert hosts[0].file_stats == {}
    assert hosts[0].existing_files == set()
