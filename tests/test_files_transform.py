# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for ``files.transform``: the root-owned spool-and-install editor.

``files.transform`` is ``files.edit``'s pure-transform contract with a
different write mechanism: it reads the target's text with the ``FileContents``
fact, applies a caller-supplied *pure* ``transform``, and -- only when the
result differs -- delegates the write to ``files.install``, which spools the
text over unprivileged scp to a dest-derived ``/tmp`` path and installs it with
an elevated ``sh -c``. That closes the gap ``files.edit`` cannot: its
``PutCommand`` scp is unprivileged, so a root-owned ``/etc`` file is out of
reach in one deploy.

Covers the transform-then-delegate behaviour (create, rewrite, fixpoint no-op,
absent file), the forwarding of ``mode``/``user``/``group`` and ``**kwargs`` to
install, install's mode validation surfacing through transform, transform
purity, the fact read over the real transport, and end-to-end ``run``/plan/log
behaviour including the second-transform no-op, the two-privilege-level log and
the ``sudo`` elevation of the install half only.
"""

import shlex
from collections.abc import Generator

import pytest

from reemote.core import FactCache, OpContext, OperationRequest, op
from reemote.drivers import command_shell, format_log, format_plan, run
from reemote.hosts import Host
from reemote.modules import files
from reemote.modules.files.files import (
    FileContents,
    InstallCommand,
    InstallSpoolCommand,
    _install_spool_path,
)
from tests.simulated_host import SimulatedHost

INVENTORY = [
    {
        "host": "web1",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
]

DEST = "/etc/reemote-transform-demo.conf"
SPOOL = _install_spool_path(DEST)
SPOOL_DIR = SPOOL.rsplit("/", 1)[0]
MARKER = "# managed by reemote"


def ensure_marker(current: str | None) -> str:
    """Pure, idempotent transform: return content with ``MARKER`` present."""
    text = current or ""
    lines = text.splitlines()
    if MARKER in lines:
        return text
    lines.append(MARKER)
    return "\n".join(lines) + "\n"


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell`` instead of connecting."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        return self._stdout


# --- Transform purity ---------------------------------------------------------


def test_transform_is_idempotent():
    """``f(f(x)) == f(x)``: a second pass over the output is a fixpoint."""
    once = ensure_marker(None)
    assert once == f"{MARKER}\n"
    assert ensure_marker(once) == once


def test_transform_preserves_other_lines():
    """Untouched lines, comments and blank lines survive; the marker appends."""
    current = "# a comment\nkey = value\n"
    out = ensure_marker(current)
    assert out == f"# a comment\nkey = value\n{MARKER}\n"
    # Already present -> returned verbatim, no duplicate.
    assert ensure_marker(out) == out


# --- The operation: transform, then delegate to install -----------------------


async def test_transform_creates_an_absent_file():
    """``transform(None)`` produces the content to create the file from."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.transform(ctx, DEST, ensure_marker)]

    assert ctx.changed
    assert ctx.reason == f"content {DEST} is absent, will install"
    spool_cmd, install_cmd = commands
    assert isinstance(spool_cmd, InstallSpoolCommand)
    assert spool_cmd.content == f"{MARKER}\n"
    assert spool_cmd.dest == SPOOL
    assert isinstance(install_cmd, InstallCommand)
    assert install_cmd.spool == SPOOL
    assert install_cmd.dest == DEST
    assert install_cmd.mode == 644


async def test_transform_rewrites_when_the_content_differs():
    """An existing file whose transform output differs is spooled + installed,
    with the untouched lines preserved in the spooled text."""
    host = SimulatedHost(name="h", remote_files={DEST: b"key = value\n"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.transform(ctx, DEST, ensure_marker)]

    assert ctx.changed
    assert ctx.reason == f"content {DEST} differs, will install"
    spool_cmd, _ = commands
    assert spool_cmd.content == f"key = value\n{MARKER}\n"


async def test_transform_is_a_no_op_when_already_converged():
    """When the transform is a fixpoint of the current content, install's
    content-only comparison yields nothing."""
    host = SimulatedHost(name="h", remote_files={DEST: f"{MARKER}\n".encode()})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.transform(ctx, DEST, ensure_marker)]

    assert not ctx.changed
    assert ctx.reason == f"content already up to date ({DEST})"
    assert commands == []


async def test_transform_forwards_mode_user_and_group_to_install():
    """The ownership/mode params reach the elevated install descriptor."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.transform(
            ctx, DEST, ensure_marker, mode="0640", user="www", group="www"
        )
    ]
    _, install_cmd = commands
    assert install_cmd.mode == 640
    assert install_cmd.user == "www"
    assert install_cmd.group == "www"
    assert f"chown www:www {DEST}" in install_cmd.shell()
    assert "chmod 640 " in install_cmd.shell()


@pytest.mark.parametrize("mode", ["u+x", "888", "", "0o644"])
async def test_transform_surfaces_install_mode_validation(mode):
    """A non-octal-digit mode raises install's ValueError through transform."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="Invalid file mode"):
        [cmd async for cmd in files.transform(ctx, DEST, ensure_marker, mode=mode)]


async def test_transform_absorbs_unknown_kwargs():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.transform(
            ctx, DEST, ensure_marker, some_global_arg=True
        )
    ]
    assert len(commands) == 2


# --- The fact read over the real transport ------------------------------------


async def test_transform_reads_current_content_over_the_real_transport():
    """The transform input comes from FileContents' marker/cat form when no
    simulated seam is present."""
    # Converged: the marker is already a line, so the transform is a fixpoint.
    host = CannedShellHost(f"{FileContents.MARKER}\n{MARKER}\n")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.transform(ctx, DEST, ensure_marker)]
    assert not ctx.changed
    assert commands == []

    # Not converged: the marker is absent, so the transform appends it.
    host = CannedShellHost(f"{FileContents.MARKER}\nkey = value\n")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.transform(ctx, DEST, ensure_marker)]
    assert ctx.changed
    spool_cmd, _ = commands
    assert spool_cmd.content == f"key = value\n{MARKER}\n"


# --- Descriptor elevation asymmetry (inherited from install) ------------------


def test_spool_transfer_is_never_elevated_but_install_is():
    """The two-privilege-level signature: a sudo-flagged request leaves the scp
    transfer unprefixed and elevates only the install shell."""
    spool_cmd = InstallSpoolCommand(content=f"{MARKER}\n", dest=SPOOL)
    assert command_shell(spool_cmd, sudo=True) == str(spool_cmd)

    install_cmd = InstallCommand(spool=SPOOL, dest=DEST, mode=644)
    inner = (
        f"mkdir -p /etc && cp {SPOOL} {DEST} && "
        f"chmod 644 {DEST} && rm -rf {SPOOL_DIR}"
    )
    assert install_cmd.shell() == f"sh -c {shlex.quote(inner)}"
    assert command_shell(install_cmd, sudo=True) == f"sudo sh -c {shlex.quote(inner)}"


# --- End to end through the driver ---------------------------------------------


def transform_deploy():
    """One transform, then a second transform of the same file: the second
    reads back what the first installed and is a guaranteed no-op."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.transform,
            name="Ensure marker",
            path=DEST,
            transform=ensure_marker,
            sudo=True,
        )
        yield op(
            files.transform,
            name="Re-ensure marker",
            path=DEST,
            transform=ensure_marker,
            sudo=True,
        )

    return deploy


async def test_transform_end_to_end_plan_log_and_second_no_op():
    """The prepare plan materializes both transforms against pre-deployment
    state (both changed); only the apply log -- with its per-step FactCache
    recreation -- shows the second reading back what the first installed. The
    log carries the unprivileged scp line and the sudo sh -c install."""
    plans, report, hosts = await run(
        INVENTORY, transform_deploy(), host_factory=SimulatedHost.from_inventory
    )

    first, second = plans["web1"]
    assert first.changed
    assert second.changed  # prepare never sends: the file was absent for both
    assert f"content {DEST} is absent, will install" in format_plan(plans)

    assert report is not None
    entries = {entry.name: entry for entry in report.log}
    assert entries["Ensure marker"].changed
    assert not entries["Re-ensure marker"].changed
    assert entries["Re-ensure marker"].reason == (
        f"content already up to date ({DEST})"
    )

    # The transformed content landed at dest and the spool was cleaned up.
    assert hosts[0].remote_files == {DEST: f"{MARKER}\n".encode()}

    log = format_log(report)
    assert f"$ scp <files install> -> {SPOOL}" in log
    assert "$ sudo sh -c 'mkdir -p /etc && cp" in log


async def test_transform_dry_run_sends_nothing():
    plans, report, hosts = await run(
        INVENTORY,
        transform_deploy(),
        host_factory=SimulatedHost.from_inventory,
        dry=True,
    )
    assert report is None
    assert plans["web1"][0].changed
    assert hosts[0].remote_files == {}


async def test_transform_with_owner_end_to_end():
    """An owned transform writes the transformed content, records the requested
    owner and mode, logs the elevated chown step, and a second identical
    transform in the same deploy reads the content back as a no-op."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.transform,
            name="Transform owned config",
            path=DEST,
            transform=ensure_marker,
            mode="0640",
            user="www",
            group="www",
            sudo=True,
        )
        yield op(
            files.transform,
            name="Re-transform owned config",
            path=DEST,
            transform=ensure_marker,
            mode="0640",
            user="www",
            group="www",
            sudo=True,
        )

    _plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=SimulatedHost.from_inventory
    )
    assert report is not None
    entries = {entry.name: entry for entry in report.log}
    assert entries["Transform owned config"].changed
    assert not entries["Re-transform owned config"].changed

    assert hosts[0].remote_files == {DEST: f"{MARKER}\n".encode()}
    assert hosts[0].file_stats[DEST]["user"] == "www"
    assert hosts[0].file_stats[DEST]["group"] == "www"
    assert hosts[0].file_stats[DEST]["mode"] == 640

    log = format_log(report)
    assert f"chown www:www {DEST}" in log
    assert "chmod 640 " in log
