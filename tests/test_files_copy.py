# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for ``files.copy``: copy a remote path to a directory or file path.

Covers the ``CopyCommand`` shell descriptor (quoting, the ``-f`` overwrite
flag, elevated ``command_shell`` renderings), the operation's the baseline
``files.copy`` fixture parity (file/directory copies with and without
overwrite, the equal-content no-op, and the error branches), the issue #22
Ansible remote-copy parity (a file ``dest`` renames, ``mode``/``user``/
``group`` converge in the same request, and ``overwrite=True`` is
sha1-gated so a drifted target converges once then no-ops), the strict src/dest
checks (absent src / absent rename parent raise ``ValueError`` in an applying
materialization, where the baseline logged softly while planning) and issue
#73's planning carve-out (a never-send materialization reports a plan line for
an absent src, so a put-then-copy deploy previews in one pass),
the upstream src check that rejects a symlink source (kept verbatim, unlike
``files.move`` which accepts links), the ``SimulatedHost`` seam dispatch
(the source stays in place, both dest forms), and end-to-end plan/log
behaviour including the idempotent repeat copy, the two-pass rename no-op,
drift convergence and the put-then-copy two-run consequence.
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
from reemote.modules.files.files import ChmodCommand, ChownCommand, CopyCommand
from tests.simulated_host import SimulatedHost

INVENTORY = [
    {
        "host": "web1",
        "family": "Ubuntu",
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
    host: SimulatedHost, *, phase: str = "prepare", planning: bool = False, **kwargs
) -> tuple[OpContext, list]:
    """Materialize ``files.copy`` against ``host``.

    ``planning=True`` builds the context a dry step threads (issue #73): the
    never-send marker an operation trades its absent-src ``ValueError`` for.
    """
    ctx = OpContext(
        host=host, facts=FactCache(host, phase=phase), planning=planning
    )
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
    # The baseline's invalid_dest.json shape, re-read through the issue #22
    # rename semantics: a dest that exists as a non-directory is now a
    # rename target, so the error comes from its absent parent ("/" is
    # unseeded here) instead of the old "is not a directory" rejection.
    host = make_host(src_stats={"mode": 600}, dest_stats=False)
    with pytest.raises(ValueError, match=r"dest directory / does not exist"):
        await materialize(host, src=SRC, dest=DEST)


async def test_copy_absent_src_raises_when_applying():
    # The baseline's invalid_src.json logs softly while planning and raises
    # only while executing; reemote raises in every materialization that can
    # send (the files.move strictness, kept).
    host = make_host(dest_stats={"mode": 700})
    for phase in ("prepare", "apply"):
        with pytest.raises(ValueError, match=r"src /tmp/testfile does not exist"):
            await materialize(host, src=SRC, dest=DEST, phase=phase)


async def test_copy_absent_src_plans_when_planning():
    """Issue #73: a never-send materialization reports a plan line for an absent
    ``src`` instead of raising -- the source may be staged by an earlier
    request of the same deploy."""
    host = make_host(dest_stats={"mode": 700})
    ctx, commands = await materialize(host, planning=True, src=SRC, dest=DEST)

    assert commands == []
    assert ctx.changed
    assert ctx.reason == (
        f"src {SRC} absent at plan time; copy planned once it is staged"
    )


async def test_copy_symlink_src_raises_when_planning():
    """The carve-out keys on *absence* (both reads ``None``), not on falsiness:
    a symlink source reads ``False`` for both facts and stays the error
    upstream's check made it."""
    host = make_host(src_stats=False, dest_stats={"mode": 700})
    host.link_stats[SRC] = {"mode": 600, "link_target": "file.txt"}
    with pytest.raises(ValueError, match=r"src /tmp/testfile does not exist"):
        await materialize(host, planning=True, src=SRC, dest=DEST)


async def test_copy_absent_dest_is_a_rename_target_gated_on_its_parent():
    # The baseline's missing_dest.json raised on an absent dest directory;
    # with issue #22 an absent dest is the rename form, and the strictness
    # moves to the target's parent: "/" is unseeded, so both phases raise.
    host = make_host(src_stats={"mode": 600})
    for phase in ("prepare", "apply"):
        with pytest.raises(
            ValueError, match=r"dest directory / does not exist"
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


# --- Issue #22: rename, mode/owner, sha1-gated overwrite ------------------------

RENAMED = "/tmp2/renamed.txt"


async def test_copy_rename_to_file_dest():
    # A dest that is not an existing directory is the full target path:
    # the basename changes and the cp addresses the file directly.
    host = make_host(src_stats={"mode": 600}, dest_stats={"mode": 700})
    ctx, commands = await materialize(host, src=SRC, dest=RENAMED)

    assert ctx.changed
    assert ctx.reason == f"will copy {SRC} to {RENAMED}"
    assert commands == [CopyCommand(src=SRC, dest=RENAMED)]


async def test_copy_rename_absent_parent_raises_in_both_phases():
    host = make_host(src_stats={"mode": 600})
    for phase in ("prepare", "apply"):
        with pytest.raises(
            ValueError, match=r"dest directory /nowhere does not exist"
        ):
            await materialize(
                host, src=SRC, dest="/nowhere/renamed.txt", phase=phase
            )


async def test_copy_rename_non_directory_parent_raises():
    host = make_host(src_stats={"mode": 600})
    host.directory_stats["/tmp2"] = False
    with pytest.raises(
        ValueError, match=r"dest parent /tmp2 is not a directory"
    ):
        await materialize(host, src=SRC, dest=RENAMED)


async def test_copy_rename_existing_target_without_overwrite_raises():
    host = make_host(src_stats={"mode": 600}, dest_stats={"mode": 700})
    host.file_stats[RENAMED] = {"mode": 600}
    host.remote_files = {SRC: b"new\n", RENAMED: b"old\n"}
    with pytest.raises(
        ValueError, match=r"/tmp2/renamed.txt already exists and is different than src"
    ):
        await materialize(host, src=SRC, dest=RENAMED)


async def test_copy_overwrite_equal_content_is_a_no_op():
    # The sha1 gate extends to overwrite=True: equal content skips the
    # copy, so a repeat reports no change (Ansible force:yes idempotence).
    host = make_host(
        src_stats={"mode": 600},
        dest_stats={"mode": 700},
        target_stats={"mode": 600},
    )
    host.remote_files = {SRC: b"same\n", FULL_DEST: b"same\n"}
    ctx, commands = await materialize(host, src=SRC, dest=DEST, overwrite=True)

    assert not ctx.changed
    assert ctx.reason == f"{FULL_DEST} already exists"
    assert commands == []


async def test_copy_overwrite_drift_converges():
    host = make_host(
        src_stats={"mode": 600},
        dest_stats={"mode": 700},
        target_stats={"mode": 600},
    )
    host.remote_files = {SRC: b"new\n", FULL_DEST: b"old\n"}
    ctx, commands = await materialize(host, src=SRC, dest=DEST, overwrite=True)

    assert ctx.changed
    assert commands == [CopyCommand(src=SRC, dest=DEST, overwrite=True)]


async def test_copy_sets_mode_and_owner_after_the_copy():
    # The create-branch contract: a planned copy yields the requested
    # chown/chmod unconditionally (chown first -- it clears setuid bits).
    host = make_host(src_stats={"mode": 600}, dest_stats={"mode": 700})
    ctx, commands = await materialize(
        host, src=SRC, dest=RENAMED, mode="0644", user="root", group="root"
    )

    assert ctx.changed
    assert commands == [
        CopyCommand(src=SRC, dest=RENAMED),
        ChownCommand(path=RENAMED, user="root", group="root"),
        ChmodCommand(path=RENAMED, mode=644),
    ]


async def test_copy_noop_converges_metadata_drift():
    # Equal content but drifted mode/owner: only the differing metadata
    # commands are yielded (the files.file update branch), no re-copy.
    host = make_host(
        src_stats={"mode": 600},
        dest_stats={"mode": 700},
        target_stats={"mode": 600, "user": "nobody", "group": "nogroup"},
    )
    host.remote_files = {SRC: b"same\n", FULL_DEST: b"same\n"}
    ctx, commands = await materialize(
        host, src=SRC, dest=DEST, mode="0644", user="root", group="root"
    )

    assert ctx.changed
    assert commands == [
        ChownCommand(path=FULL_DEST, user="root", group="root"),
        ChmodCommand(path=FULL_DEST, mode=644),
    ]


async def test_copy_noop_equal_metadata_stays_a_no_op():
    host = make_host(
        src_stats={"mode": 644},
        dest_stats={"mode": 700},
        target_stats={"mode": 644, "user": "root", "group": "root"},
    )
    host.remote_files = {SRC: b"same\n", FULL_DEST: b"same\n"}
    ctx, commands = await materialize(
        host, src=SRC, dest=DEST, mode="0644", user="root", group="root"
    )

    assert not ctx.changed
    assert ctx.reason == f"{FULL_DEST} already exists"
    assert commands == []


async def test_copy_symbolic_mode_reruns_on_equal_content():
    # A symbolic mode never equals the fact's octal int, so its chmod
    # re-runs on every pass (the files.file precedent).
    host = make_host(
        src_stats={"mode": 644},
        dest_stats={"mode": 700},
        target_stats={"mode": 644},
    )
    host.remote_files = {SRC: b"same\n", FULL_DEST: b"same\n"}
    ctx, commands = await materialize(host, src=SRC, dest=DEST, mode="u+x")

    assert ctx.changed
    assert commands == [ChmodCommand(path=FULL_DEST, mode="u+x")]


async def test_copy_invalid_mode_raises():
    host = make_host(src_stats={"mode": 600}, dest_stats={"mode": 700})
    with pytest.raises(ValueError, match=r"Invalid file mode"):
        await materialize(host, src=SRC, dest=DEST, mode="0999")


# --- The SimulatedHost seam ----------------------------------------------------


async def test_send_command_copy_mutates_the_stores():
    host = SimulatedHost(name="h")
    host.remote_files = {SRC: b"payload\n"}
    host.existing_files = {SRC}
    host.file_stats = {SRC: {"mode": 600}}
    host.directory_stats = {DEST: {"mode": 700}}
    await send_command(host, CopyCommand(src=SRC, dest=DEST))

    # The copy landed and the source stayed in place (the move difference).
    assert host.remote_files == {SRC: b"payload\n", FULL_DEST: b"payload\n"}
    assert host.existing_files == {SRC, FULL_DEST}
    assert host.file_stats == {SRC: {"mode": 600}, FULL_DEST: {"mode": 600}}
    # The stat rows are distinct dicts, so mutating one never leaks.
    assert host.file_stats[SRC] is not host.file_stats[FULL_DEST]


async def test_send_command_copy_seeds_directory_stats():
    host = SimulatedHost(name="h")
    host.directory_stats = {SRC: {"mode": 700}, DEST: {"mode": 700}}
    await send_command(host, CopyCommand(src=SRC, dest=DEST, overwrite=True))

    assert host.directory_stats == {
        SRC: {"mode": 700},
        DEST: {"mode": 700},
        FULL_DEST: {"mode": 700},
    }
    assert host.directory_stats[SRC] is not host.directory_stats[FULL_DEST]


async def test_send_command_copy_rename_lands_at_the_full_dest():
    # No directory row for the dest: the mutator resolves it as the full
    # target path, exactly as the operation does (the rename form).
    host = SimulatedHost(name="h")
    host.remote_files = {SRC: b"payload\n"}
    host.existing_files = {SRC}
    host.file_stats = {SRC: {"mode": 600}}
    await send_command(host, CopyCommand(src=SRC, dest=RENAMED, overwrite=True))

    assert host.remote_files == {SRC: b"payload\n", RENAMED: b"payload\n"}
    assert host.existing_files == {SRC, RENAMED}
    assert host.file_stats == {SRC: {"mode": 600}, RENAMED: {"mode": 600}}


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


# --- Issue #22 end to end: rename + metadata + drift convergence ----------------

MODULE_SRC = "/tmp/xdebug.so"
MODULE_DEST = "/usr/lib/php/modules/xdebug-3.3.2.so"


def module_factory(entry: dict) -> SimulatedHost:
    """A host with a built module spooled in /tmp and the root-owned target dir."""
    host = SimulatedHost.from_inventory(entry)
    host.remote_files = {MODULE_SRC: b"built module v1\n"}
    host.file_stats = {MODULE_SRC: {"mode": 755, "user": "root", "group": "root"}}
    host.directory_stats = {"/usr/lib/php/modules": {"mode": 755}}
    return host


def module_deploy(host: Host) -> Generator[OperationRequest]:
    # The Ansible one-task `copy remote_src` shape: rename, mode/owner and
    # idempotent overwrite in a single elevated request.
    yield op(
        files.copy,
        name="Install module",
        src=MODULE_SRC,
        dest=MODULE_DEST,
        mode="0644",
        user="root",
        group="root",
        overwrite=True,
        sudo=True,
    )


async def test_rename_copy_converges_in_one_request_then_no_ops():
    plans, report, hosts = await run(
        INVENTORY, module_deploy, host_factory=module_factory
    )
    (planned,) = plans["web1"]
    assert planned.changed
    assert f"will copy {MODULE_SRC} to {MODULE_DEST}" in format_plan(plans)

    assert report is not None
    log = format_log(report)
    assert f"$ sudo cp -r -f {MODULE_SRC} {MODULE_DEST}" in log
    assert f"$ sudo chown root:root {MODULE_DEST}" in log
    assert f"$ sudo chmod 644 {MODULE_DEST}" in log

    host = hosts[0]
    # The renamed target landed with the requested metadata; the source stayed.
    assert host.remote_files[MODULE_DEST] == b"built module v1\n"
    assert host.file_stats[MODULE_DEST] == {
        "mode": 644,
        "user": "root",
        "group": "root",
    }
    assert MODULE_SRC in host.remote_files

    # Second pass: equal sha1 and converged metadata -> a literal no-op.
    plans, report, _hosts = await run(
        INVENTORY, module_deploy, host_factory=lambda entry: host
    )
    (planned,) = plans["web1"]
    assert not planned.changed
    assert planned.reason == f"{MODULE_DEST} already exists"
    assert report is not None
    assert not report.changed
    assert not report.log[0].changed


async def test_overwrite_copy_converges_content_drift_then_no_ops():
    _plans, _report, hosts = await run(
        INVENTORY, module_deploy, host_factory=module_factory
    )
    host = hosts[0]

    # The target drifts (a hand-edited or stale module): overwrite=True
    # must converge it once, reporting changed, not raise or churn.
    host.remote_files[MODULE_DEST] = b"drifted module\n"
    plans, report, _hosts = await run(
        INVENTORY, module_deploy, host_factory=lambda entry: host
    )
    (planned,) = plans["web1"]
    assert planned.changed
    assert report is not None
    assert f"$ sudo cp -r -f {MODULE_SRC} {MODULE_DEST}" in format_log(report)
    assert host.remote_files[MODULE_DEST] == b"built module v1\n"

    # Third pass: converged again -> no-op.
    plans, report, _hosts = await run(
        INVENTORY, module_deploy, host_factory=lambda entry: host
    )
    (planned,) = plans["web1"]
    assert not planned.changed
    assert report is not None
    assert not report.changed


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


async def test_put_then_copy_in_one_deploy_previews_when_dry():
    # Issue #73's other half: that same deploy previews. A dry run never sends,
    # so the put's file is absent when the copy materializes -- and a
    # materialization that can never send says so as a plan line.
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(files.put, name="Stage hosts", src=PAYLOAD, dest="/tmp/hosts")
        yield op(files.copy, name="Copy hosts", src="/tmp/hosts", dest="/etc")

    def factory(entry: dict) -> SimulatedHost:
        host = SimulatedHost.from_inventory(entry)
        host.directory_stats = {"/etc": {"mode": 755}}
        return host

    plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=factory, dry=True
    )
    assert report is None
    _put_plan, copy_plan = plans["web1"]
    assert copy_plan.changed
    assert copy_plan.reason == (
        "src /tmp/hosts absent at plan time; copy planned once it is staged"
    )
    assert "/tmp/hosts" not in hosts[0].remote_files