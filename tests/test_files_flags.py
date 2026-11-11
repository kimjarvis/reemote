# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for ``files.flags``: set or clear ext2-family attribute flags.

Covers the ``FileFlags`` fact (the Linux stand-in for the baseline's BSD
``Flags``: ``lsattr -d`` command shape, tilde/path quoting, the flags-field
parse with its padding dashes dropped, and the ``SimulatedHost``
``file_flags`` seam answering ``[]`` for an unseeded path), the
``ChattrCommand`` descriptor (the ``+``/``-`` comma-joined spec, path
quoting, elevated ``command_shell`` renderings), the operation's the baseline
``files.flags`` parity (set/clear no-ops, scalar and tuple flags, the empty
noop branch, the validation raises, ``**kwargs`` absorption), the
``SimulatedHost`` seam dispatch (merge on set, intersect-only removal on
clear), and end-to-end plan/log behaviour including the idempotent repeat
and a ``dry=True`` run that sends nothing.
"""

import importlib.util
import pathlib
from collections.abc import Generator

import pytest

from reemote.core import FactCache, OpContext, OperationRequest, op
from reemote.drivers import (
    command_shell,
    format_log,
    format_plan,
    gather_fact,
    run,
    send_command,
)
from reemote.hosts import Host
from reemote.modules import files
from reemote.modules.files.files import ChattrCommand, FileFlags
from tests.modules.files.test_files import CannedShellHost
from tests.simulated_host import SimulatedHost

INVENTORY = [
    {
        "host": "web1",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
]

PATH = "/etc/logrotate.d/httpd"


# --- The FileFlags fact --------------------------------------------------------


def test_fileflags_command_guards_existence():
    # The baseline-shaped existence guard (``! test -e ... ||``) short-circuits
    # a missing path to empty output; stderr noise goes to /dev/null so an
    # unsupported filesystem answers [] instead of failing the read.
    assert FileFlags.command(PATH) == (
        f"! test -e {PATH} || lsattr -d {PATH} 2> /dev/null"
    )
    assert FileFlags.command("/tmp/my file").startswith(
        "! test -e '/tmp/my file' || lsattr -d '/tmp/my file'"
    )


def test_fileflags_keeps_a_leading_tilde_unquoted():
    # The File.command convention: ~/ must reach the remote shell unquoted
    # so it expands; the rest of the path is quoted.
    assert FileFlags.command("~/.bashrc") == (
        "! test -e ~/.bashrc || lsattr -d ~/.bashrc 2> /dev/null"
    )
    assert FileFlags.command("~/My Documents/file") == (
        "! test -e ~/'My Documents/file' "
        "|| lsattr -d ~/'My Documents/file' 2> /dev/null"
    )


def test_fileflags_parses_the_flags_field():
    # A real ``lsattr -d`` row: the fixed-width flags field first, the path
    # second; padding dashes drop and the letters come out sorted.
    assert FileFlags.parse(["--------------i-------e-- /etc/logrotate.d/httpd"]) == [
        "e",
        "i",
    ]
    # An all-dashes row means "nothing set".
    assert FileFlags.parse(["----------------------- /tmp/plain"]) == []
    # No output (the existence guard or a masked unsupported filesystem).
    assert FileFlags.parse([]) == []
    assert FileFlags.parse([""]) == []
    # A row without a path field still parses its flags field.
    assert FileFlags.parse(["--i--------e--"]) == ["e", "i"]


async def test_fileflags_processes_over_the_transport():
    # The real-transport path: the fact runs its own guarded command and
    # parses the row.
    host = CannedShellHost("----A-------i--------e-- /etc/logrotate.d/httpd\n")
    assert await FileFlags.process(host, PATH) == ["A", "e", "i"]
    assert host.shells == [FileFlags.command(PATH)]


async def test_fileflags_reads_the_simulated_seam():
    host = SimulatedHost(name="h")
    # An unseeded path answers [] -- the baseline's Flags answer for absence.
    assert await FileFlags.process(host, PATH) == []
    host.file_flags[PATH] = {"i", "a"}
    result = await FileFlags.process(host, PATH)
    assert result == ["a", "i"]
    # Copy-on-read: the caller never sees (or can mutate) the stored set.
    result.append("z")
    host.file_flags[PATH].add("j")
    assert await FileFlags.process(host, PATH) == ["a", "i", "j"]


async def test_fileflags_gather_fact_forwards_the_path():
    host = SimulatedHost(name="h")
    host.file_flags[PATH] = {"i"}
    gathered = await gather_fact([host], FileFlags, PATH)
    assert gathered == {"h": ["i"]}


# --- Descriptor rendering ------------------------------------------------------


def test_chattr_command_renders_the_spec():
    assert ChattrCommand(path=PATH, flags=("i",)).shell() == (
        f"chattr +i {PATH}"
    )
    assert ChattrCommand(path=PATH, flags=("a", "i"), present=False).shell() == (
        f"chattr -a,i {PATH}"
    )
    assert ChattrCommand(path="/tmp/my file", flags=("i",)).shell() == (
        "chattr +i '/tmp/my file'"
    )


def test_command_shell_elevates_the_chattr():
    cmd = ChattrCommand(path=PATH, flags=("a", "i"))
    assert command_shell(cmd, sudo=True) == f"sudo chattr +a,i {PATH}"
    assert (
        command_shell(ChattrCommand(path=PATH, flags=("i",), present=False), su=True)
        == f"su -c 'chattr -i {PATH}'"
    )


# --- Operation parity with the baseline's files.flags ---------------------------------


def make_host(*, flags: set[str] | None = None) -> SimulatedHost:
    """A SimulatedHost with the ``FileFlags`` seam seeded at ``PATH``."""
    host = SimulatedHost(name="h")
    if flags is not None:
        host.file_flags[PATH] = set(flags)
    return host


async def materialize(
    host: SimulatedHost, *, phase: str = "prepare", **kwargs
) -> tuple[OpContext, list]:
    ctx = OpContext(host=host, facts=FactCache(host, phase=phase))
    commands = [cmd async for cmd in files.flags(ctx, **kwargs)]
    return ctx, commands


async def test_flags_set_missing_flag_yields_chattr():
    host = make_host(flags={"a"})
    ctx, commands = await materialize(host, path=PATH, flags=("i",))

    assert ctx.changed
    assert ctx.reason == f"flags i not set, will set on {PATH}"
    assert commands == [ChattrCommand(path=PATH, flags=("i",))]


async def test_flags_set_already_set_is_a_noop():
    # The baseline's noop text over the Linux letter spelling.
    host = make_host(flags={"i", "a"})
    ctx, commands = await materialize(host, path=PATH, flags=("i",))

    assert not ctx.changed
    assert ctx.reason == f"'{PATH}' already has 'i' set"
    assert commands == []


async def test_flags_set_only_computes_the_difference():
    # A partial match yields only the missing letters, sorted into the spec.
    host = make_host(flags={"a"})
    ctx, commands = await materialize(host, path=PATH, flags=("i", "a", "e"))

    assert ctx.changed
    assert ctx.reason == f"flags e,i not set, will set on {PATH}"
    assert commands == [ChattrCommand(path=PATH, flags=("e", "i"))]


async def test_flags_clear_present_flag_yields_chattr():
    host = make_host(flags={"i", "a"})
    ctx, commands = await materialize(
        host, path=PATH, flags=("i",), present=False
    )

    assert ctx.changed
    assert ctx.reason == f"flags i set, will clear on {PATH}"
    assert commands == [ChattrCommand(path=PATH, flags=("i",), present=False)]


async def test_flags_clear_absent_flag_is_a_noop():
    # The clear intersects current with requested: an unset requested flag
    # changes nothing, and the baseline's noop verb mirrors the direction.
    host = make_host(flags={"a"})
    ctx, commands = await materialize(
        host, path=PATH, flags=("i",), present=False
    )

    assert not ctx.changed
    assert ctx.reason == f"'{PATH}' already has 'i' clear"
    assert commands == []


async def test_flags_clear_computes_the_intersection_only():
    # Requesting ("i", "a", "s") against current {"a", "e"} clears only "a":
    # the other current letter stays untouched, exactly as ``chattr -a``.
    host = make_host(flags={"a", "e"})
    ctx, commands = await materialize(
        host, path=PATH, flags=("i", "a", "s"), present=False
    )

    assert ctx.changed
    assert ctx.reason == f"flags a set, will clear on {PATH}"
    assert commands == [ChattrCommand(path=PATH, flags=("a",), present=False)]


async def test_flags_scalar_string_normalizes_to_one_flag():
    # The baseline accepts a scalar flags string; here it is one ext2 letter.
    host = make_host()
    ctx, commands = await materialize(host, path=PATH, flags="i")

    assert ctx.changed
    assert commands == [ChattrCommand(path=PATH, flags=("i",))]


async def test_flags_accepts_a_list_when_called_directly():
    # The list half of the union works through the direct generator call;
    # op() rejects it (UnhashableArgumentError) because requests hash
    # their arguments, which is why the example uses a tuple.
    host = make_host()
    _ctx, commands = await materialize(host, path=PATH, flags=["i", "a"])
    assert commands == [ChattrCommand(path=PATH, flags=("a", "i"))]


async def test_flags_empty_requests_no_change():
    for empty in (None, ()):
        host = make_host(flags={"i"})
        ctx, commands = await materialize(host, path=PATH, flags=empty)

        assert not ctx.changed
        assert ctx.reason == f"no changes requested to flags for '{PATH}'"
        assert commands == []


async def test_flags_rejects_a_bsd_flag_name():
    # The developer-chosen Linux vocabulary: the baseline's BSD chflags names
    # ("hidden") are not valid ext2 letters and never reach the host.
    host = make_host()
    with pytest.raises(ValueError, match="invalid ext2 attribute flag 'hidden'"):
        await materialize(host, path=PATH, flags=("hidden",))


async def test_flags_rejects_a_signed_letter():
    host = make_host()
    with pytest.raises(ValueError, match=r"invalid ext2 attribute flag '\+i'"):
        await materialize(host, path=PATH, flags=("+i",))


async def test_flags_rejects_a_non_string_container():
    host = make_host()
    with pytest.raises(
        TypeError, match="`flags` must be a string or a tuple/list of strings"
    ):
        await materialize(host, path=PATH, flags=42)


async def test_flags_kwargs_absorb_extra_globals():
    host = make_host()
    ctx, commands = await materialize(
        host, path=PATH, flags=("i",), some_extra_global="ignored"
    )
    assert ctx.changed
    assert commands == [ChattrCommand(path=PATH, flags=("i",))]


async def test_flags_request_sequence_is_deterministic_across_phases():
    # The same host state read in both phases must produce the identical
    # command and reason -- the set-difference spellings are sorted, so the
    # plan and the apply log agree.
    host = make_host(flags={"a"})
    prepare_ctx, prepare = await materialize(host, path=PATH, flags=("i", "a"))
    apply_ctx, apply_ = await materialize(
        host, path=PATH, flags=("i", "a"), phase="apply"
    )
    assert prepare == apply_
    assert prepare_ctx.reason == apply_ctx.reason


# --- The SimulatedHost seam ------------------------------------------------------


async def test_send_command_chattr_sets_the_flags():
    host = SimulatedHost(name="h")
    host.file_flags[PATH] = {"a"}
    await send_command(host, ChattrCommand(path=PATH, flags=("i", "e")))

    # Set merges into whatever was already there.
    assert host.file_flags[PATH] == {"a", "e", "i"}


async def test_send_command_chattr_clears_only_the_requested():
    host = SimulatedHost(name="h")
    host.file_flags[PATH] = {"a", "e", "i"}
    await send_command(
        host, ChattrCommand(path=PATH, flags=("i",), present=False)
    )

    assert host.file_flags[PATH] == {"a", "e"}


async def test_send_command_chattr_tolerates_an_absent_path():
    host = SimulatedHost(name="h")
    await send_command(host, ChattrCommand(path=PATH, flags=("i",), present=False))
    assert host.file_flags[PATH] == set()


# --- End to end through the driver -------------------------------------------------


def immutable_factory(entry: dict) -> SimulatedHost:
    """A host whose logrotate file carries the append-only flag."""
    host = SimulatedHost.from_inventory(entry)
    host.file_flags[PATH] = {"a"}
    return host


async def test_flags_end_to_end_elevated_plan_and_log():
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.flags,
            name="Make logrotate immutable",
            path=PATH,
            flags=("i",),
            sudo=True,
        )

    plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=immutable_factory
    )
    (planned,) = plans["web1"]
    assert planned.changed
    assert f"flags i not set, will set on {PATH}" in format_plan(plans)

    assert report is not None
    log = format_log(report)
    assert f"$ sudo chattr +i {PATH}" in log

    assert hosts[0].file_flags[PATH] == {"a", "i"}


async def test_repeat_flags_request_is_a_no_op():
    # The apply-phase fresh fact cache reads back the mutation: the second
    # run plans and logs the no-op.
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.flags,
            name="Make logrotate immutable",
            path=PATH,
            flags=("i",),
            sudo=True,
        )

    plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=immutable_factory
    )
    assert plans["web1"][0].changed
    host = hosts[0]

    plans, report, _hosts = await run(
        INVENTORY, deploy, host_factory=lambda entry: host
    )
    (planned,) = plans["web1"]
    assert not planned.changed
    assert planned.reason == f"'{PATH}' already has 'i' set"
    assert report is not None
    assert not report.changed


async def test_flags_dry_run_sends_nothing():
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(files.flags, name="Set immutable", path=PATH, flags=("i",))

    plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=immutable_factory, dry=True
    )
    assert report is None
    assert plans["web1"][0].changed
    # Prepare never applies, so the simulated state is untouched.
    assert hosts[0].file_flags[PATH] == {"a"}


# --- The converge-style example: literal no-op on rerun --------------------------

# The example under test ships under examples/ and is not a package, so it is
# loaded by file path (module-level code opens no connection). The demo hosts
# are never contacted: run() is driven against a SimulatedHost factory.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_converge_example():
    path = _REPO_ROOT / "examples/modules/files/files_flags_converge.py"
    spec = importlib.util.spec_from_file_location("files_flags_converge", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def converge_factory(entry: dict) -> SimulatedHost:
    """A host modelling the example's /tmp parent so only chattr lands."""
    host = SimulatedHost.from_inventory(entry)
    host.directory_stats = {"/tmp": {"mode": 1777}}
    return host


async def test_converge_example_rerun_is_all_no_op():
    example = _load_converge_example()
    marker = example.MARKER
    # The example loads the shared inventory.json (it keeps no module-level
    # INVENTORY), so the test supplies its own single entry -- in the "centos"
    # group, or the deploy's is_centos guard would skip every request. One host
    # suffices for the fixpoint.
    inventory = [{**INVENTORY[0], "group": "centos"}]

    # Run 1: fresh host. Both steps converge a not-yet-existing marker.
    plans, report, hosts = await run(inventory, example.deploy, host_factory=converge_factory)
    host = hosts[0]
    assert report is not None
    assert [p.changed for p in plans["web1"]] == [True, True]
    log1 = format_log(report)
    assert f"$ chmod {example.DESIRED_MODE} {marker}" in log1
    assert f"$ sudo chattr +i {marker}" in log1
    # The converged end state is readable straight off the seams.
    assert host.file_stats[marker]["mode"] == example.DESIRED_MODE
    assert host.file_flags[marker] == {"i"}

    # Run 2: the *same* deploy over the state run 1 left behind. Every step
    # reads back exactly what it requested, so the whole run is a no-op --
    # nothing is planned, sent or logged as a command line.
    plans, report, _hosts = await run(
        inventory, example.deploy, host_factory=lambda entry: host
    )
    assert [p.changed for p in plans["web1"]] == [False, False]
    assert report is not None
    assert not report.changed
    log2 = format_log(report)
    assert "chmod" not in log2
    assert "chattr" not in log2
    assert f"'{marker}' already has 'i' set" in log2
    # Net state is untouched by the second run.
    assert host.file_stats[marker]["mode"] == example.DESIRED_MODE
    assert host.file_flags[marker] == {"i"}
