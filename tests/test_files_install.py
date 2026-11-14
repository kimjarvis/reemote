# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for ``files.install``: the generic spool-and-install operation.

``files.install`` is a reemote original closing the root-owned-write gap:
``files.put``/``files.edit`` ride unprivileged scp, and the put-then-move
recipe spans two runs. This operation reads the destination's text with the
``FileContents`` fact and, only when it differs from the desired ``content``,
yields an unprivileged ``InstallSpoolCommand`` transfer to a dest-derived
``/tmp`` spool path followed by an elevated ``InstallCommand`` shell
(``mkdir -p``/``cp``/optional ``chown``/``chmod``/``rm`` wrapped in
``sh -c``) -- both from ONE request, so the whole write fits a single deploy.

Covers the descriptor renderings (including the ``sudo`` elevation of the
install half, the never-elevated transfer half and the optional
``user``/``group`` ownership step), the deterministic dest-derived spool
naming, the content-equality idempotence (install, rewrite, no-op,
empty-content boundary), mode validation, the fact read over the real
transport, the ``SimulatedHost`` seam dispatch and state mutation
(including the installed owner), and end-to-end ``run``/plan/log behaviour
including the second-install no-op and the spool cleanup.
"""

import shlex
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
from reemote.handlers import changed_hosts
from reemote.hosts import CommandError, Host
from reemote.modules import files
from reemote.modules.files.files import (
    FileContents,
    InstallCommand,
    InstallSpoolCommand,
    InstallValidateCommand,
    _install_spool_path,
)
from tests.simulated_host import SimulatedHost

INVENTORY = [
    {
        "host": "web1",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
]

DEST = "/etc/reemote-demo.conf"
CONTENT = "# managed by reemote\nkey = value\n"
SPOOL = _install_spool_path(DEST)
# The spool file lives inside a sha1-derived /tmp directory; the install
# rm -rf's the whole directory rather than the bare file.
SPOOL_DIR = SPOOL.rsplit("/", 1)[0]


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell`` instead of connecting."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        return self._stdout


class CannedFallbackHost(Host):
    """Canned stdout for both read tiers, recording the elevated calls.

    ``run_shell`` answers the unprivileged ``FileContents`` read and
    ``run_shell_privileged`` the fallback re-read an unreadable file triggers.
    """

    def __init__(self, stdout: str, privileged_stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout
        self._privileged_stdout = privileged_stdout
        self.privileged_calls: list[tuple[str, bool]] = []

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        return self._stdout

    async def run_shell_privileged(
        self, shell: str, *, sudo: bool = False, su: bool = False, doas: bool = False
    ) -> str:
        self.privileged_calls.append((shell, sudo))
        return self._privileged_stdout


# --- The spool naming: deterministic and dest-derived ------------------------


def test_spool_path_is_deterministic_and_dest_derived():
    """The same dest always yields the same /tmp spool name (both phases must
    agree), and different dests never share one spool file."""
    assert SPOOL == _install_spool_path(DEST)
    assert SPOOL.startswith("/tmp/reemote-install-")
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
        f"chmod 644 {DEST} && rm -rf {SPOOL_DIR}'"
    )
    assert cmd.shell() == expected
    elevated = command_shell(cmd, sudo=True)
    assert elevated == f"sudo {expected}"
    # The inner pipeline survives the outer quoting as one argument.
    assert shlex.split(elevated)[3] == (
        f"mkdir -p /etc && cp {SPOOL} {DEST} && "
        f"chmod 644 {DEST} && rm -rf {SPOOL_DIR}"
    )


def test_install_command_quotes_paths_with_spaces():
    dest = "/etc/my config.conf"
    cmd = InstallCommand(spool=SPOOL, dest=dest, mode=600)
    inner = shlex.split(cmd.shell())[2]
    tokens = shlex.split(inner)
    assert tokens[tokens.index("cp") + 2] == dest
    assert tokens[tokens.index("chmod") + 1] == "600"
    assert tokens[tokens.index("chmod") + 2] == dest


def test_install_command_renders_the_user_group_chown():
    """A user:group pair renders one chown step between cp and chmod --
    chown first so the chmod's mode survives the setuid/setgid clear."""
    cmd = InstallCommand(
        spool=SPOOL, dest=DEST, mode=640, user="dd-agent", group="dd-agent"
    )
    expected = (
        f"sh -c 'mkdir -p /etc && cp {SPOOL} {DEST} && "
        f"chown dd-agent:dd-agent {DEST} && "
        f"chmod 640 {DEST} && rm -rf {SPOOL_DIR}'"
    )
    assert cmd.shell() == expected
    tokens = shlex.split(shlex.split(cmd.shell())[2])
    assert tokens.index("chown") < tokens.index("chmod")


def test_install_command_renders_a_user_only_chown():
    """A user alone renders chown <user>; the group half stays root."""
    cmd = InstallCommand(spool=SPOOL, dest=DEST, mode=644, user="dd-agent")
    inner = shlex.split(cmd.shell())[2]
    assert f"chown dd-agent {DEST}" in inner
    assert "chgrp" not in inner


def test_install_command_renders_a_group_only_chgrp():
    """A group alone switches to chgrp, exactly as ChownCommand does."""
    cmd = InstallCommand(spool=SPOOL, dest=DEST, mode=644, group="dd-agent")
    inner = shlex.split(cmd.shell())[2]
    assert f"chgrp dd-agent {DEST}" in inner
    assert "chown" not in inner


def test_install_command_quotes_the_owner_spec():
    """An owner carrying shell metacharacters stays one quoted argument."""
    cmd = InstallCommand(
        spool=SPOOL, dest=DEST, mode=644, user="odd user", group="odd;group"
    )
    tokens = shlex.split(shlex.split(cmd.shell())[2])
    assert tokens[tokens.index("chown") + 1] == "odd user:odd;group"


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


async def test_install_user_and_group_reach_the_install_command():
    """The ownership params pass straight through to the elevated install
    descriptor; idempotence stays content-only (the owner is never read)."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.install(
            ctx, CONTENT, DEST, mode="0640", user="dd-agent", group="dd-agent"
        )
    ]
    _, install_cmd = commands
    assert install_cmd.user == "dd-agent"
    assert install_cmd.group == "dd-agent"
    assert f"chown dd-agent:dd-agent {DEST}" in install_cmd.shell()


async def test_install_without_an_owner_defaults_to_none():
    """No user/group keeps the historical root-owned install: the fields
    default to None and no chown step is rendered."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.install(ctx, CONTENT, DEST)]
    _, install_cmd = commands
    assert install_cmd.user is None
    assert install_cmd.group is None
    assert "chown" not in install_cmd.shell()
    assert "chgrp" not in install_cmd.shell()


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


async def test_install_unreadable_file_falls_back_to_an_elevated_read():
    """Over the real transport, a file the login user cannot read cats back
    the UNREADABLE marker; the fact's elevated re-read returns the real
    content, so a matching install is a no-op instead of a re-install."""
    host = CannedFallbackHost(
        f"{FileContents.MARKER}\n{FileContents.UNREADABLE}\n",
        f"{FileContents.MARKER}\n{CONTENT}",
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.install(ctx, CONTENT, DEST)]
    assert not ctx.changed
    assert commands == []
    assert host.privileged_calls == [(FileContents.command(DEST), True)]


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


async def test_send_command_records_the_requested_owner():
    """The install arm's file_stats row carries the requested user:group
    and mode, so a File-fact read after the install sees the new owner."""
    host = SimulatedHost.from_inventory(INVENTORY[0])
    await send_command(host, InstallSpoolCommand(content=CONTENT, dest=SPOOL))
    await send_command(
        host,
        InstallCommand(
            spool=SPOOL, dest=DEST, mode=640, user="dd-agent", group="dd-agent"
        ),
    )
    assert host.file_stats[DEST] == {
        "user": "dd-agent",
        "group": "dd-agent",
        "mode": 640,
        "size": len(CONTENT.encode()),
    }


async def test_send_command_user_only_leaves_the_group_root():
    """Only the named half changes: a user-only chown leaves group root,
    a group-only chgrp leaves user root."""
    host = SimulatedHost.from_inventory(INVENTORY[0])
    await send_command(host, InstallSpoolCommand(content=CONTENT, dest=SPOOL))
    await send_command(
        host, InstallCommand(spool=SPOOL, dest=DEST, mode=644, user="dd-agent")
    )
    assert host.file_stats[DEST]["user"] == "dd-agent"
    assert host.file_stats[DEST]["group"] == "root"

    other = "/etc/reemote-group-only.conf"
    other_spool = _install_spool_path(other)
    await send_command(host, InstallSpoolCommand(content=CONTENT, dest=other_spool))
    await send_command(
        host,
        InstallCommand(spool=other_spool, dest=other, mode=644, group="dd-agent"),
    )
    assert host.file_stats[other]["user"] == "root"
    assert host.file_stats[other]["group"] == "dd-agent"


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
    other_dest = "/etc/reemote-other.conf"
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


async def test_install_with_owner_end_to_end():
    """An owned install writes the content, records the requested owner and
    mode, logs the elevated chown step, and a second identical install in
    the same deploy reads the content back as a no-op (idempotence stays
    content-only -- the owner is applied on the write, never compared)."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.install,
            name="Install owned config",
            content=CONTENT,
            dest=DEST,
            mode="0640",
            user="dd-agent",
            group="dd-agent",
            sudo=True,
        )
        yield op(
            files.install,
            name="Re-install owned config",
            content=CONTENT,
            dest=DEST,
            mode="0640",
            user="dd-agent",
            group="dd-agent",
            sudo=True,
        )

    _plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=SimulatedHost.from_inventory
    )
    assert report is not None
    entries = {entry.name: entry for entry in report.log}
    assert entries["Install owned config"].changed
    assert not entries["Re-install owned config"].changed

    assert hosts[0].remote_files == {DEST: CONTENT.encode()}
    assert hosts[0].file_stats[DEST]["user"] == "dd-agent"
    assert hosts[0].file_stats[DEST]["group"] == "dd-agent"
    assert hosts[0].file_stats[DEST]["mode"] == 640

    log = format_log(report)
    assert f"chown dd-agent:dd-agent {DEST}" in log
    assert "chmod 640 " in log


# --- The validate hook: atomic validate-then-install ---------------------------


def test_install_validate_command_substitutes_the_spool_placeholder():
    """``{spool}`` renders the deterministic spool path (space-free, so
    ``shlex.quote`` leaves it bare) into the caller's command template."""
    cmd = InstallValidateCommand(validate="nginx -t -c {spool}", spool=SPOOL, dest=DEST)
    assert cmd.shell() == f"nginx -t -c {SPOOL}"


def test_install_validate_command_substitutes_and_quotes_both_placeholders():
    """A dest carrying a space is shell-quoted into one argument, so a
    validator that names ``{dest}`` stays safe; ``{spool}`` substitutes too."""
    dest = "/etc/my config.conf"
    cmd = InstallValidateCommand(
        validate="check {spool} for {dest}", spool=SPOOL, dest=dest
    )
    assert cmd.shell() == f"check {SPOOL} for '/etc/my config.conf'"
    assert shlex.split(cmd.shell())[-1] == dest


def test_install_validate_command_is_elevated_with_the_request():
    """A regular shell descriptor, so ``sudo`` prefixes it -- the validation
    runs elevated (as root) exactly as the install half does, matching
    Ansible's become-scoped ``validate``."""
    cmd = InstallValidateCommand(validate="nginx -t -c {spool}", spool=SPOOL, dest=DEST)
    assert command_shell(cmd, sudo=True) == f"sudo nginx -t -c {SPOOL}"


async def test_install_yields_validate_between_spool_and_install():
    """The injection point: with ``validate`` set the operation yields three
    descriptors, the validation sitting between the transfer and the install
    so a failure aborts before the destination is touched."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.install(
            ctx, CONTENT, DEST, validate="nginx -t -c {spool}"
        )
    ]
    assert [type(cmd) for cmd in commands] == [
        InstallSpoolCommand,
        InstallValidateCommand,
        InstallCommand,
    ]
    validate_cmd = commands[1]
    assert validate_cmd.validate == "nginx -t -c {spool}"
    assert validate_cmd.spool == SPOOL
    assert validate_cmd.dest == DEST


async def test_install_without_validate_yields_only_two_commands():
    """No ``validate`` keeps the historical two-descriptor write."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in files.install(ctx, CONTENT, DEST)]
    assert [type(cmd) for cmd in commands] == [
        InstallSpoolCommand,
        InstallCommand,
    ]


@pytest.mark.parametrize("validate", ["", "   ", "\t\n"])
async def test_install_rejects_an_empty_validate_template(validate):
    """An empty/whitespace-only template is a request error, raised before the
    idempotence read just as the mode validation is."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="non-empty command template"):
        [cmd async for cmd in files.install(ctx, CONTENT, DEST, validate=validate)]


async def test_install_validate_is_a_no_op_when_the_content_matches():
    """Idempotence still short-circuits first: a converged file yields nothing,
    so the validator never runs on an unchanged destination."""
    host = SimulatedHost(name="h", remote_files={DEST: CONTENT.encode()})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        cmd
        async for cmd in files.install(
            ctx, CONTENT, DEST, validate="nginx -t -c {spool}"
        )
    ]
    assert not ctx.changed
    assert commands == []


async def test_send_command_dispatches_the_validate_as_a_no_op():
    """With no seeded failure the simulated validation passes and mutates
    nothing -- the spool stays staged for the install that follows."""
    host = SimulatedHost.from_inventory(INVENTORY[0])
    await send_command(host, InstallSpoolCommand(content=CONTENT, dest=SPOOL))
    await send_command(
        host, InstallValidateCommand(validate="nginx -t -c {spool}", spool=SPOOL, dest=DEST)
    )
    assert host.remote_files == {SPOOL: CONTENT.encode()}


async def test_send_command_raises_when_the_validate_fails():
    """A spool seeded into ``fail_validate`` makes the simulated validator
    raise ``CommandError`` -- the fail-fast the real non-zero exit produces."""
    host = SimulatedHost.from_inventory(INVENTORY[0])
    host.fail_validate.add(SPOOL)
    await send_command(host, InstallSpoolCommand(content=CONTENT, dest=SPOOL))
    with pytest.raises(CommandError):
        await send_command(
            host,
            InstallValidateCommand(
                validate="nginx -t -c {spool}", spool=SPOOL, dest=DEST
            ),
        )


async def test_install_validate_end_to_end_lands_and_logs_in_order():
    """A passing validation installs the content and the log carries the three
    steps in order: the unprivileged scp, the elevated validator, the elevated
    install."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.install,
            name="Install validated config",
            content=CONTENT,
            dest=DEST,
            validate="nginx -t -c {spool}",
            sudo=True,
        )

    _plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=SimulatedHost.from_inventory
    )
    assert report is not None
    entry = report.log[0]
    assert entry.changed
    assert [type(cmd) for cmd in entry.commands] == [
        InstallSpoolCommand,
        InstallValidateCommand,
        InstallCommand,
    ]
    assert hosts[0].remote_files == {DEST: CONTENT.encode()}

    log = format_log(report)
    assert f"$ scp <files install> -> {SPOOL}" in log
    assert f"$ sudo nginx -t -c {SPOOL}" in log
    assert "$ sudo sh -c 'mkdir -p /etc && cp" in log


async def test_install_validate_failure_aborts_and_preserves_the_original():
    """The atomic guarantee end to end: a validator that fails raises out of the
    apply, so the ``InstallCommand`` never runs and the destination keeps the
    content it held before -- only the ``/tmp`` spool was staged."""
    built: list[SimulatedHost] = []

    def failing_factory(entry: dict) -> SimulatedHost:
        host = SimulatedHost.from_inventory(entry)
        host.remote_files[DEST] = b"old = true\n"
        host.fail_validate.add(SPOOL)
        built.append(host)
        return host

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.install,
            name="Install rejected config",
            content=CONTENT,
            dest=DEST,
            validate="nginx -t -c {spool}",
            sudo=True,
        )

    with pytest.raises(CommandError):
        await run(INVENTORY, deploy, host_factory=failing_factory)

    # The bad config never landed: dest still holds the original bytes, and
    # only the spool the transfer staged before the validator ran is present.
    assert built[0].remote_files[DEST] == b"old = true\n"
    assert built[0].remote_files[SPOOL] == CONTENT.encode()
    assert DEST not in built[0].file_stats


# --- The unreadable-file fallback: restrictive modes stay idempotent ---------


UNREADABLE_INVENTORY = [
    {
        "host": "web1",
        "connection": {"key_path": "~/.ssh/id_ed25519", "username": "kim"},
    },
]


def unreadable_install_deploy():
    """One install of a 0640 root:dd-agent config the login user (kim, outside
    the dd-agent group) cannot read back unprivileged."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            files.install,
            name="Install service config",
            content=CONTENT,
            dest=DEST,
            mode="0640",
            user="root",
            group="dd-agent",
            sudo=True,
        )

    return deploy


async def test_unreadable_install_is_idempotent_across_deploys():
    """The acceptance: installing 0640 root:dd-agent content twice with a login
    user outside that group reports changed=False on pass 2 -- FileContents
    re-reads the unreadable file elevated -- and queues no handler."""
    _plans, first, hosts = await run(
        UNREADABLE_INVENTORY,
        unreadable_install_deploy(),
        host_factory=SimulatedHost.from_inventory,
    )
    assert first is not None
    assert first.log[0].changed
    assert changed_hosts(first, {"Install service config"}) == frozenset({"web1"})

    # The installed file is 0640 root:dd-agent, and kim cannot read it.
    host = hosts[0]
    assert host.file_stats[DEST] == {
        "user": "root",
        "group": "dd-agent",
        "mode": 640,
        "size": len(CONTENT.encode()),
    }
    assert not host.login_can_read(DEST)

    # The second deploy shares run 1's host state: the read-back falls back
    # to the elevated tier and the install is a no-op.
    _plans, second, _hosts = await run(
        UNREADABLE_INVENTORY,
        unreadable_install_deploy(),
        host_factory=lambda entry: host,
    )
    assert second is not None
    assert not second.log[0].changed
    assert second.log[0].reason == f"content already up to date ({DEST})"
    assert changed_hosts(second, {"Install service config"}) == frozenset()