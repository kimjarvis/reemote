# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for ``files.install``: the generic spool-and-install operation.

``files.install`` is a reemote5 original closing the root-owned-write gap:
``files.put``/``files.edit`` ride unprivileged scp, and the put-then-move
recipe spans two runs. This operation reads the destination's text with the
``FileContents`` fact and, only when it differs from the desired ``content``,
yields an unprivileged ``InstallSpoolCommand`` transfer to a dest-derived
``/tmp`` spool path followed by an elevated ``InstallCommand`` shell
(``mkdir -p``/``cp``/``chmod``/``rm`` wrapped in ``sh -c``) -- both from ONE
request, so the whole write fits a single deploy.

Covers the descriptor renderings (including the ``sudo`` elevation of the
install half and the never-elevated transfer half), the deterministic
dest-derived spool naming, the content-equality idempotence (install,
rewrite, no-op, empty-content boundary), mode validation, the fact read over
the real transport, the ``SimulatedHost`` seam dispatch and state mutation,
and end-to-end ``run``/plan/log behaviour including the second-install no-op
and the spool cleanup.
"""

import shlex
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

DEST = "/etc/reemote5-demo.conf"
CONTENT = "# managed by reemote5\nkey = value\n"
SPOOL = _install_spool_path(DEST)


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell`` instead of connecting."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        return self._stdout


# --- The spool naming: deterministic and dest-derived ------------------------


def test_spool_path_is_deterministic_and_dest_derived():
    """The same dest always yields the same /tmp spool name (both phases must
    agree), and different dests never share one spool file."""
    assert SPOOL == _install_spool_path(DEST)
    assert SPOOL.startswith("/tmp/reemote5-install-")
    assert _install_spool_path("/etc/other.conf") != SPOOL


# --- Descriptor rendering -----------------------------------------------------


def test_install_spool_command_renders_as_a_transfer():
    """The spool half is non-shell: a ``__str__`` loggable form, no shell()."""
    cmd = InstallSpoolCommand(content=CONTENT, dest=SPOOL)
    assert str(cmd) == f"scp <files install> -> {SPOOL}"
    assert not hasattr(cmd, "shell")


def test_install_spool_transfer_is_never_elevated():
    """Even a sudo-flagged request renders the transfer unprefixed -- the scp
    copy rides as the login user regardless."""
    cmd = InstallSpoolCommand(content=CONTENT, dest=SPOOL)
    assert command_shell(cmd, sudo=True) == str(cmd)


def test_install_command_renders_the_elevated_install_chain():
    """The install half wraps mkdir/cp/chmod/rm in sh -c so a sudo prefix
    elevates every step, not just the chain head."""
    cmd = InstallCommand(spool=SPOOL, dest=DEST, mode=644)
    expected = (
        f"sh -c 'mkdir -p /etc && cp {SPOOL} {DEST} && "
        f"chmod 644 {DEST} && rm -f {SPOOL}'"
    )
    assert cmd.shell() == expected
    elevated = command_shell(cmd, sudo=True)
    assert elevated == f"sudo {expected}"
    # The inner pipeline survives the outer quoting as one argument.
    assert shlex.split(elevated)[3] == (
        f"mkdir -p /etc && cp {SPOOL} {DEST} && "
        f"chmod 644 {DEST} && rm -f {SPOOL}"
    )


def test_install_command_quotes_paths_with_spaces():
    dest = "/etc/my config.conf"
    cmd = InstallCommand(spool=SPOOL, dest=dest, mode=600)
    inner = shlex.split(cmd.shell())[2]
    tokens = shlex.split(inner)
    assert tokens[tokens.index("cp") + 2] == dest
    assert tokens[tokens.index("chmod") + 1] == "600"
    assert tokens[tokens.index("chmod") + 2] == dest


# --- The operation: content-equality idempotence ------------------------------


async def test_install_creates_an_absent_file():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.install(ctx, CONTENT, DEST)]

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


async def test_install_rewrites_when_the_content_differs():
    host = SimulatedHost(name="h", remote_files={DEST: b"stale\n"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.install(ctx, CONTENT, DEST)]

    assert ctx.changed
    assert ctx.reason == f"content {DEST} differs, will install"
    assert len(commands) == 2


async def test_install_is_a_no_op_when_the_content_matches():
    host = SimulatedHost(name="h", remote_files={DEST: CONTENT.encode()})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.install(ctx, CONTENT, DEST)]

    assert not ctx.changed
    assert ctx.reason == f"content already up to date ({DEST})"
    assert commands == []


async def test_install_empty_content_creates_an_empty_file():
    """Absence (None) and empty content ("") stay distinguishable: installing
    "" over an absent file is a change, matching it afterwards is a no-op."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.install(ctx, "", DEST)]
    assert ctx.changed
    assert len(commands) == 2

    host.remote_files[DEST] = b""
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.install(ctx, "", DEST)]
    assert not ctx.changed
    assert commands == []


async def test_install_mode_reaches_the_install_command():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd async for cmd in files.install(ctx, CONTENT, DEST, mode="0600")
    ]
    _, install_cmd = commands
    assert install_cmd.mode == 600
    assert "chmod 600 " in install_cmd.shell()


@pytest.mark.parametrize("mode", ["u+x", "888", "", "0o644"])
async def test_install_rejects_non_octal_digit_modes(mode):
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="Invalid file mode"):
        [cmd async for cmd in files.install(ctx, CONTENT, DEST, mode=mode)]


async def test_install_absorbs_unknown_kwargs():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.install(ctx, CONTENT, DEST, some_global_arg=True)
    ]
    assert len(commands) == 2


# --- The fact read over the real transport ------------------------------------


async def test_install_reads_current_content_over_the_real_transport():
    """The idempotence read goes through FileContents' marker/cat form when no
    simulated seam is present."""
    host = CannedShellHost(f"{FileContents.MARKER}\n{CONTENT}")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.install(ctx, CONTENT, DEST)]
    assert not ctx.changed
    assert commands == []

    host = CannedShellHost(f"{FileContents.MARKER}\nstale\n")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.install(ctx, CONTENT, DEST)]
    assert ctx.changed
    assert len(commands) == 2


# --- The SimulatedHost seam: dispatch and mutation -----------------------------


async def test_send_command_dispatches_the_spool_transfer():
    host = SimulatedHost.from_inventory(INVENTORY[0])
    await send_command(host, InstallSpoolCommand(content=CONTENT, dest=SPOOL))
    assert host.remote_files == {SPOOL: CONTENT.encode()}


async def test_send_command_dispatches_the_install():
    """The install arm moves the spooled bytes to dest, records the root-owned
    mode row and the parent directory, and clears the spool from every store."""
    host = SimulatedHost.from_inventory(INVENTORY[0])
    await send_command(host, InstallSpoolCommand(content=CONTENT, dest=SPOOL))
    await send_command(host, InstallCommand(spool=SPOOL, dest=DEST, mode=644))

    assert host.remote_files == {DEST: CONTENT.encode()}
    assert DEST in host.existing_files
    assert SPOOL not in host.existing_files
    assert host.file_stats[DEST] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(CONTENT.encode()),
    }
    assert "/etc" in host.directory_stats


async def test_install_with_an_absent_spool_installs_nothing():
    host = SimulatedHost.from_inventory(INVENTORY[0])
    await send_command(host, InstallCommand(spool=SPOOL, dest=DEST, mode=644))
    assert host.remote_files == {}
    assert DEST not in host.existing_files


# --- End to end through the driver ---------------------------------------------


def install_deploy():
    """One install, then a second install of the same content at the same
    path. ``content`` is a plain str -- hashable and equal across phases --
    so no identity-stable object is needed at the call site."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.install,
            name="Install config",
            content=CONTENT,
            dest=DEST,
            sudo=True,
        )
        yield op(
            files.install,
            name="Re-install config",
            content=CONTENT,
            dest=DEST,
            sudo=True,
        )

    return deploy


async def test_install_end_to_end_plan_log_and_second_no_op():
    """The prepare plan materializes both installs against pre-deployment
    state (both changed); only the apply log -- with its per-step FactCache
    recreation -- shows the second reading back what the first installed.
    The log carries the unprivileged scp line and the sudo sh -c install."""
    plans, report, hosts = await run(
        INVENTORY, install_deploy(), host_factory=SimulatedHost.from_inventory
    )

    first, second = plans["web1"]
    assert first.changed
    assert second.changed  # prepare never sends: the file was absent for both
    assert f"content {DEST} is absent, will install" in format_plan(plans)

    assert report is not None
    entries = {entry.name: entry for entry in report.log}
    assert entries["Install config"].changed
    assert not entries["Re-install config"].changed
    assert entries["Re-install config"].reason == (
        f"content already up to date ({DEST})"
    )

    # The content landed at dest and the spool was cleaned up.
    assert hosts[0].remote_files == {DEST: CONTENT.encode()}

    log = format_log(report)
    assert f"$ scp <files install> -> {SPOOL}" in log
    assert "$ sudo sh -c 'mkdir -p /etc && cp" in log


async def test_install_dry_run_sends_nothing():
    plans, report, hosts = await run(
        INVENTORY,
        install_deploy(),
        host_factory=SimulatedHost.from_inventory,
        dry=True,
    )
    assert report is None
    assert plans["web1"][0].changed
    assert hosts[0].remote_files == {}


async def test_two_installs_to_different_dests_use_distinct_spools():
    """Two installs in one deploy never clobber each other's spool file."""
    other_dest = "/etc/reemote5-other.conf"
    other_content = "other = true\n"

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.install,
            name="Install first",
            content=CONTENT,
            dest=DEST,
            sudo=True,
        )
        yield op(
            files.install,
            name="Install second",
            content=other_content,
            dest=other_dest,
            sudo=True,
        )

    _plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=SimulatedHost.from_inventory
    )
    assert report is not None
    entries = {entry.name: entry for entry in report.log}
    assert entries["Install first"].changed
    assert entries["Install second"].changed
    assert hosts[0].remote_files == {
        DEST: CONTENT.encode(),
        other_dest: other_content.encode(),
    }