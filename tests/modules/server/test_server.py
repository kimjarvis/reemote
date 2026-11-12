# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the server module: host facts, ``Arch``, ``Port`` and the waits.

Covers ``reemote.modules.server.server``: the ``Arch`` fact, the
``AuthorizedKeys`` fact (the baseline's own fixtures over the real-transport
path, tilde-username validation, and the ``SimulatedHost`` seam), the
``AuthorizedKeysFile`` raw-content fact (command quoting, the
``remote_files`` seam and the marker parse over the real-transport path),
the ``Date`` fact (the baseline's own fixture over the real-transport path, offset
parsing, and the ``SimulatedHost`` seam), the ``Env`` fact (canned
``echo $<name>`` output over the real-transport path, variable-name
validation, parsing edge cases, and the ``SimulatedHost`` seam), the
``EtcHosts`` fact (the baseline's own fixture over the real-transport path,
comment/duplicate-IP parsing edge cases, and the shared ``remote_files``
``SimulatedHost`` seam), the
``Groups`` fact (the baseline's own
fixture over the real-transport path, ``/etc/group`` parsing edge cases, and
the ``SimulatedHost`` seam), the ``HasGui`` fact (the baseline's own fixtures over
the real-transport path, session-binary parsing edge cases, and the
``SimulatedHost`` seam), the ``Home`` fact (the baseline's own fixtures over the
real-transport path, unexpanded-tilde handling, tilde-username validation,
and the ``SimulatedHost`` seam), the ``Hostname`` fact (the baseline's own
fixture over the real-transport path, parsing edge cases, and the
``SimulatedHost`` seam), the ``Kernel`` fact (the baseline's own fixture over
the real-transport path, parsing edge cases, and the ``SimulatedHost``
seam), the ``KernelModules`` fact (the baseline's own Linux fixture over the
real-transport path, the ``/proc/modules`` command and parser edge cases, and
the ``SimulatedHost`` seam), the ``KernelVersion`` fact
(the baseline's own fixture over the real-transport path, parsing edge cases,
and the ``SimulatedHost`` seam), the ``Last`` fact (the baseline's own
fixtures over the real-transport path, login-record parsing edge cases,
and the ``SimulatedHost`` seam), the ``LinuxDistribution`` fact (the baseline's
own nine fixtures over the real-transport path, release-file parsing edge
cases, and the ``SimulatedHost`` seam), the ``LinuxGui`` fact (the baseline's own
fixture over the real-transport path, session-binary parsing edge cases,
agreement with ``HasGui``, and the ``SimulatedHost`` seam), the
``LinuxName`` fact (the baseline's own fixture and the nine
``LinuxDistribution`` fixtures over the real-transport path, agreement
with ``LinuxDistribution``, and the shared ``SimulatedHost`` seam), the
``Locales`` fact (the baseline's documented example over the real-transport
path, ``utf8`` normalization edge cases, and the ``SimulatedHost`` seam),
the ``LsbRelease`` fact (the baseline's own fixture over the real-transport
path, key-normalization parsing edge cases, and the ``SimulatedHost``
seam), the ``Mounts`` fact (the baseline's own Linux fixture over the
real-transport path, the ``/proc/self/mountinfo`` command and parser edge
cases, and the ``SimulatedHost`` seam), the
``Port`` fact (the baseline's own thirteen fixtures over the real-transport
path, the ``Which``-dispatched tool selection, the
port/protocol validation and the parser edge cases -- including the
owner-less rows an unprivileged read produces, which is where reemote's
record deliberately differs from the baseline's tuple -- and the
``listening_ports`` ``SimulatedHost`` seam), the
``Processes`` fact
(the baseline's own Linux and BusyBox fixtures over the real-transport path,
busybox-dispatched command and parser edge cases, and the ``SimulatedHost``
seam), the ``Timezone`` fact (the baseline's own
fixture over the real-transport path, parsing edge cases, and the
``SimulatedHost`` seam), the ``Uptime`` fact (the baseline's own Linux fixture
over the real-transport path, the ``/proc/uptime`` command and parser edge
cases, and the ``SimulatedHost`` seam), the ``Users`` fact (the baseline's
own mixed fixture over the real-transport path, passwd/lastlog parsing edge
cases, and the ``SimulatedHost`` seam), the ``Which`` fact (the baseline's own
fixtures over the real-transport path, name quoting and parsing edge cases,
and the ``SimulatedHost`` seam), the ``group`` operation's option set and its
``GroupAddCommand``/``GroupDelCommand`` descriptors (the baseline's own Linux
operation fixtures replayed over the ``SimulatedHost`` seams, idempotence and
the end-to-end lockstep run), the ``user`` operation
and its ``UserAddCommand``/``UserModCommand``/``UserDelCommand`` descriptors
with the private ``_HomeMkdirCommand``/``_HomeChownCommand`` pair (the baseline's
own Linux operation fixtures replayed over the ``SimulatedHost`` seams minus the
dropped ``public_keys``/``delete_keys`` and ``Which`` guard cases, per-field
update idempotence, the
``directory_stats``/``link_stats`` home branch, the argument-hashability
boundary and the end-to-end lockstep run), the ``user_authorized_keys``
operation and its ``AuthorizedKeysUploadCommand``/
``AuthorizedKeysInstallCommand`` spool-and-install pair (the baseline's own
operation fixtures, the in-place state verbs, the ``delete_keys``
whole-file rewrite, both no-op levels and the end-to-end lockstep run),
the ``etc_hosts`` operation and its ``EtcHostsUploadCommand``/
``EtcHostsInstallCommand`` spool-and-install pair (the baseline's own nine
operation fixtures, the pure hosts-file rewrite, both no-op levels and
the end-to-end lockstep run),
the ``hostname``
operation and its ``SetHostnameCommand`` descriptor (the baseline's own six
operation fixtures replayed over the ``SimulatedHost`` seams with the
dropped ``hostname_file`` upload step, the ``Which`` dispatch between
``hostnamectl`` and the bare ``hostname`` command, idempotence and the
end-to-end lockstep run), the ``kill`` operation and its ``KillCommand``
descriptor (the baseline's own three operation fixtures replayed over the
``SimulatedHost`` seam, the stateless always-changed behaviour, the
``processes`` seam mutation, the snapshot-factory pattern, the determinism
guard against live-state branching and the end-to-end lockstep run), the
``modprobe`` operation and its ``ModprobeCommand`` descriptor (the baseline's own
nine operation fixtures replayed over the ``SimulatedHost`` seam, the
load/unload partition against ``KernelModules``, the ``kernel_modules`` seam
mutation, the determinism guard against live-state branching and the
end-to-end lockstep run), the ``mount`` operation and its ``MountCommand``
descriptor (the baseline's own Linux operation fixtures replayed over the
``SimulatedHost`` seam, the lookup-by-device fallback and the remount against
``Mounts``, the ``mounts`` seam mutation, the
determinism guard against live-state branching and the end-to-end lockstep
run), the ``timezone`` operation and its ``SetTimezoneCommand`` descriptor
(the baseline's own noop and set_timedatectl fixtures replayed over the
``SimulatedHost`` seams, the dropped non-systemd fallback raising
``OperationError`` instead, idempotence and the end-to-end lockstep run),
the ``wait`` operation (the baseline's ``server.wait`` port argument, the
protocol/timeout/interval options it adds, the port/protocol validation at
plan time and the end-to-end lockstep run through ``run``),
and the waiting design: the ``WaitCommand`` base, the generic
``WaitForFactCommand`` poll loop (already met, met after several polls, and
timeout -- all without real sleeping), the ``send_command`` dispatch onto
``wait``, and the end-to-end ``wait_service`` behaviour through ``run`` with
``SimulatedHost``: the wait is planned unconditionally so the request
sequence is identical in both phases (determinism), it passes when the unit
is active (or the port is listening) and aborts the run with
``WaitTimeoutError`` when it is not.
"""

import hashlib
import shlex
from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from reemote.commands import WaitCommand, WaitTimeoutError
from reemote.core import (
    FactCache,
    OpContext,
    OperationError,
    OperationRequest,
    UnhashableArgumentError,
    op,
)
from reemote.drivers import (
    DeterminismError,
    command_shell,
    format_log,
    format_plan,
    gather_fact,
    run,
    run_apply,
    run_prepare,
    send_command,
)
from reemote.hosts import CommandError, CommandNotFoundError, Host
from reemote.modules import server
from reemote.modules.server.server import (
    Arch,
    AuthorizedKeys,
    AuthorizedKeysFile,
    AuthorizedKeysInstallCommand,
    AuthorizedKeysUploadCommand,
    Date,
    Env,
    EtcHosts,
    EtcHostsInstallCommand,
    EtcHostsUploadCommand,
    GroupAddCommand,
    GroupDelCommand,
    Groups,
    HasGui,
    Home,
    Hostname,
    Kernel,
    KernelModules,
    KernelVersion,
    KillCommand,
    Last,
    LinuxDistribution,
    LinuxGui,
    LinuxName,
    LocaleGenCommand,
    LocaleInstallCommand,
    Locales,
    LocaleUploadCommand,
    LsbRelease,
    ModprobeCommand,
    MountCommand,
    Mounts,
    Port,
    PortInfo,
    Processes,
    SecurityLimitInstallCommand,
    SecurityLimitUploadCommand,
    SetHostnameCommand,
    SetTimezoneCommand,
    SystemdServiceEnabled,
    SystemdServiceState,
    Timezone,
    Uptime,
    User,
    UserAddCommand,
    UserDelCommand,
    UserModCommand,
    Users,
    WaitForFactCommand,
    Which,
    _add_keys,
    _HomeChownCommand,
    _HomeDirectory,
    _HomeIsLink,
    _HomeMkdirCommand,
    _hosts_content,
    _hosts_spool_path,
    _keys_spool_path,
    _KeysChmodCommand,
    _KeysTouchCommand,
    _locale_edit,
    _locale_spool_path,
    _rewrite_keys,
    _security_limit_edit,
    _security_limit_spool_path,
    is_active,
    is_listening,
)
from tests.simulated_host import SimulatedHost

INVENTORY = [
    {"host": "web1", "connection": {"key_path": "~/.ssh/id_ed25519"}},
    {"host": "web2", "connection": {"key_path": "~/.ssh/id_ed25519"}},
]


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell`` instead of connecting."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout
        self.shells: list[str] = []

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        self.shells.append(shell)
        return self._stdout


class CannedProcessesHost(CannedShellHost):
    """A CannedShellHost that also answers the BusyBox probe.

    ``Processes`` probes ``command -v busybox`` through the ``Which`` fact
    and then runs the ps listing that answer selects, so the canned host
    must answer two different shells: the probe's path-or-empty answer and
    the ps output.
    """

    def __init__(self, busybox: str, stdout: str):
        super().__init__(stdout)
        self._busybox = busybox

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        if shell == Which.command("busybox"):
            self.shells.append(shell)
            return self._busybox
        return await super().run_shell(shell)


class CannedPortHost(CannedShellHost):
    """A CannedShellHost that also answers the ``ss`` probe.

    ``Port`` probes ``command -v ss`` through the ``Which`` fact and then
    runs the socket listing that answer selects, so the canned host must
    answer two different shells: the probe's path-or-empty answer and the
    ``ss``/``netstat`` output.
    """

    def __init__(self, ss: str, stdout: str):
        super().__init__(stdout)
        self._ss = ss

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        if shell == Which.command("ss"):
            self.shells.append(shell)
            return self._ss
        return await super().run_shell(shell)


async def materialize(host: Host, request: OperationRequest):
    """Run an operation against a fresh cache; return ``(ctx, commands)``."""
    ctx = OpContext(host=host, facts=FactCache(host, phase="test"))
    commands = [cmd async for cmd in request.func(ctx, **request.kwargs)]
    return ctx, commands


# --- Arch --------------------------------------------------------------------


async def test_arch_parses_uname_m():
    # The baseline's own fact fixture (tests/facts/server.Arch/arch.json):
    # command "uname -m", output ["x86_64"] -> fact "x86_64".
    host = CannedShellHost("x86_64\n")
    assert await Arch.process(host) == "x86_64"
    assert host.shells == ["uname -m"]


def test_arch_parse_takes_the_first_non_blank_line():
    assert Arch.parse(["aarch64"]) == "aarch64"
    assert Arch.parse(["", "  x86_64  ", "ignored"]) == "x86_64"
    assert Arch.parse([]) == ""


async def test_arch_is_empty_on_empty_output():
    assert await Arch.process(CannedShellHost("")) == ""


async def test_arch_reads_the_simulated_seam():
    host = SimulatedHost(name="h", arch="aarch64")
    assert await Arch.process(host) == "aarch64"
    # The seam defaults to x86_64: a simulated machine always answers uname.
    assert await Arch.process(SimulatedHost(name="h")) == "x86_64"


# --- AuthorizedKeys ----------------------------------------------------------

# The baseline's documented example keys (tests/facts/server.AuthorizedKeys/
# default_path.json), reused verbatim for parity.
KEY1 = "ssh-ed25519 AAAAC3Nz...key1 user@host"
KEY2 = "ssh-rsa AAAAB3Nz...key2 other@host"


async def test_authorized_keys_parses_the_default_path():
    # The baseline's default_path.json fixture: comment, blank and whitespace-only
    # lines are skipped; the file's order is preserved.
    host = CannedShellHost(f"# some comment\n\n{KEY1}\n   \n{KEY2}\n")
    assert await AuthorizedKeys.process(host, "alice") == [KEY1, KEY2]
    assert host.shells == ["cat ~alice/.ssh/authorized_keys 2>/dev/null || true"]


async def test_authorized_keys_custom_path_with_empty_output():
    # The baseline's custom_path.json fixture: output [] -> fact [].
    host = CannedShellHost("")
    assert await AuthorizedKeys.process(host, "alice", path="/etc/ssh/keys/alice") == []
    assert host.shells == ["cat /etc/ssh/keys/alice 2>/dev/null || true"]


def test_authorized_keys_parse_strips_and_preserves_order():
    assert AuthorizedKeys.parse([f"  {KEY2}  ", "# c", "", KEY1, "   "]) == [
        KEY2,
        KEY1,
    ]
    assert AuthorizedKeys.parse([]) == []


def test_authorized_keys_command_quotes_an_explicit_path():
    assert (
        AuthorizedKeys.command("alice", path="/etc/ssh/my keys")
        == "cat '/etc/ssh/my keys' 2>/dev/null || true"
    )


def test_authorized_keys_empty_user_means_the_login_user():
    assert (
        AuthorizedKeys.command("") == "cat ~/.ssh/authorized_keys 2>/dev/null || true"
    )


async def test_authorized_keys_rejects_unsafe_tilde_usernames():
    # ``~user`` cannot be quoted without disabling the expansion, so an
    # unsafe username must raise instead of reaching the shell -- on both
    # the real-transport and the simulated path.
    for user in ("bad;rm", "a b", "$(whoami)", "-x"):
        with pytest.raises(ValueError, match="Unsafe username"):
            await AuthorizedKeys.process(CannedShellHost(""), user)
        with pytest.raises(ValueError, match="Unsafe username"):
            await AuthorizedKeys.process(SimulatedHost(name="h"), user)
    # An explicit path never uses tilde expansion, so the username is not
    # consulted (the baseline only validates in the tilde branch).
    host = CannedShellHost("")
    assert await AuthorizedKeys.process(host, "bad;rm", path="/keys") == []
    assert host.shells == ["cat /keys 2>/dev/null || true"]


async def test_authorized_keys_reads_the_simulated_seam():
    host = SimulatedHost(
        name="h",
        authorized_keys={"alice": [KEY1, KEY2], "/etc/ssh/keys/alice": [KEY2]},
    )
    assert await AuthorizedKeys.process(host, "alice") == [KEY1, KEY2]
    assert await AuthorizedKeys.process(host, "alice", path="/etc/ssh/keys/alice") == [
        KEY2
    ]
    # An unknown user (or path) reads as [], like a missing file.
    assert await AuthorizedKeys.process(host, "bob") == []


async def test_authorized_keys_seam_returns_a_copy():
    host = SimulatedHost(name="h", authorized_keys={"alice": [KEY1]})
    keys = await AuthorizedKeys.process(host, "alice")
    keys.append(KEY2)
    assert host.authorized_keys["alice"] == [KEY1]


async def test_authorized_keys_gather_fact_forwards_the_user():
    hosts = [
        SimulatedHost(name="web1", authorized_keys={"kjarvis": [KEY1]}),
        SimulatedHost(name="web2", authorized_keys={"kjarvis": [KEY2]}),
    ]
    assert await gather_fact(hosts, AuthorizedKeys, "kjarvis") == {
        "web1": [KEY1],
        "web2": [KEY2],
    }


# --- Date --------------------------------------------------------------------


async def test_date_parses_baseline_fixture():
    # The baseline's own fact fixture (tests/facts/server.Date/date.json):
    # command "date +'%Y-%m-%dT%H:%M:%S%z'", output ["2019-01-01T00:00:00+0000"]
    # -> fact 2019-01-01T00:00:00+00:00.
    host = CannedShellHost("2019-01-01T00:00:00+0000\n")
    assert await Date.process(host) == datetime(2019, 1, 1, tzinfo=UTC)
    assert host.shells == ["date +'%Y-%m-%dT%H:%M:%S%z'"]


def test_date_parse_keeps_the_numeric_utc_offset():
    offset = timezone(timedelta(hours=5, minutes=30))
    parsed = Date.parse(["2024-06-01T12:34:56+0530"])
    assert parsed == datetime(2024, 6, 1, 12, 34, 56, tzinfo=offset)
    assert parsed.utcoffset() == timedelta(hours=5, minutes=30)


def test_date_parse_takes_the_first_non_blank_line():
    assert Date.parse(["", "  2019-01-01T00:00:00+0000  ", "ignored"]) == datetime(
        2019, 1, 1, tzinfo=UTC
    )
    assert Date.parse([]) is None


def test_date_parse_rejects_a_non_timestamp():
    with pytest.raises(ValueError):
        Date.parse(["date: invalid option"])


async def test_date_is_none_on_empty_output():
    # The baseline's ``default = datetime.now`` fallback is deliberately dropped:
    # a failed command reads as None, never as the control machine's clock.
    assert await Date.process(CannedShellHost("")) is None


async def test_date_reads_the_simulated_seam():
    clock = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
    host = SimulatedHost(name="h", date=clock)
    assert await Date.process(host) == clock
    # The seam defaults to the baseline's fixture timestamp: a simulated machine
    # always answers `date`, deterministically.
    assert await Date.process(SimulatedHost(name="h")) == datetime(
        2019, 1, 1, tzinfo=UTC
    )


async def test_date_gather_fact_across_hosts():
    clock1 = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
    clock2 = datetime(2026, 10, 30, 9, 5, tzinfo=timezone(timedelta(hours=-5)))
    hosts = [
        SimulatedHost(name="web1", date=clock1),
        SimulatedHost(name="web2", date=clock2),
    ]
    assert await gather_fact(hosts, Date) == {"web1": clock1, "web2": clock2}


# --- Env -----------------------------------------------------------------------


async def test_env_parses_echo_path():
    # Env replaces the migrated server.Path: the name "PATH" issues the
    # exact shell Path used and returns the raw colon-separated string
    # (the baseline's FactBase default joins the single output line).
    host = CannedShellHost("/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin\n")
    assert await Env.process(host, "PATH") == (
        "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin"
    )
    assert host.shells == ["echo $PATH"]


async def test_env_reads_any_named_variable():
    host = CannedShellHost("/home/kjarvis\n")
    assert await Env.process(host, "HOME") == "/home/kjarvis"
    assert host.shells == ["echo $HOME"]


def test_env_parse_takes_the_first_non_blank_line():
    assert Env.parse(["/usr/bin:/bin"]) == "/usr/bin:/bin"
    assert Env.parse(["", "  /usr/bin:/bin  ", "ignored"]) == "/usr/bin:/bin"
    assert Env.parse([]) == ""
    # Interior characters -- colons, spaces in directory names -- are kept
    # verbatim: the string is not split into a list.
    assert Env.parse(["/opt/my dir/bin:/usr/bin"]) == "/opt/my dir/bin:/usr/bin"


async def test_env_is_empty_on_empty_output():
    # An unset/empty variable (echo prints a blank line) maps to "" like Arch.
    assert await Env.process(CannedShellHost(""), "PATH") == ""
    assert await Env.process(CannedShellHost("\n"), "PATH") == ""


async def test_env_rejects_unsafe_names_on_both_transports():
    # $<name> must stay an unquoted bare word for the shell to expand it, so
    # a name that is not a plain shell identifier never reaches the shell --
    # identically on the canned and the simulated transport.
    for name in ("", "1BAD", "BAD-NAME", "PATH; rm -rf /", "$(id)", "A B"):
        with pytest.raises(ValueError):
            await Env.process(CannedShellHost(""), name)
        with pytest.raises(ValueError):
            await Env.process(SimulatedHost(name="h"), name)


async def test_env_reads_the_simulated_seam():
    host = SimulatedHost(
        name="h", os_env={"PATH": "/custom/bin:/usr/bin", "HOME": "/root"}
    )
    assert await Env.process(host, "PATH") == "/custom/bin:/usr/bin"
    assert await Env.process(host, "HOME") == "/root"
    # An unlisted variable reads as "", like an unset one on a real machine.
    assert await Env.process(host, "LANG") == ""
    # The seam defaults to a standard Linux PATH: a simulated machine
    # always answers echo $PATH.
    assert await Env.process(SimulatedHost(name="h"), "PATH") == (
        "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin"
    )


async def test_env_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", os_env={"PATH": "/custom/bin:/usr/bin"}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, Env, "PATH") == {
        "web1": "/custom/bin:/usr/bin",
        "web2": "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin",
    }


# --- Groups --------------------------------------------------------------------


async def test_groups_parses_baseline_fixture():
    # The baseline's own fact fixture (tests/facts/server.Groups/groups.json):
    # command "cat /etc/group", three rows -> the first field of each.
    host = CannedShellHost(
        """lpadmin:x:114:vagrant
sambashare:x:115:vagrant
vboxsf:x:999:
"""
    )
    assert await Groups.process(host) == ["lpadmin", "sambashare", "vboxsf"]
    assert host.shells == ["cat /etc/group"]


def test_groups_parse_skips_lines_without_a_colon():
    # Blank lines and stray output carry no colon and are ignored; the
    # file's order is preserved, with no dedup (the baseline's process).
    assert Groups.parse(
        ["", "wheel:x:10:", "no-colon-here", "root:x:0:", "wheel:x:10:"]
    ) == ["wheel", "root", "wheel"]
    assert Groups.parse([]) == []


async def test_groups_is_empty_on_empty_output():
    assert await Groups.process(CannedShellHost("")) == []


async def test_groups_reads_the_simulated_seam():
    host = SimulatedHost(name="h", os_groups=["wheel", "kjarvis"])
    assert await Groups.process(host) == ["wheel", "kjarvis"]
    # The seam defaults to []: a simulated machine with no groups listed.
    assert await Groups.process(SimulatedHost(name="h")) == []


async def test_groups_seam_returns_a_copy():
    host = SimulatedHost(name="h", os_groups=["wheel"])
    groups = await Groups.process(host)
    groups.append("kjarvis")
    assert host.os_groups == ["wheel"]


async def test_groups_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", os_groups=["wheel", "kjarvis"]),
        SimulatedHost(name="web2", os_groups=["kjarvis"]),
    ]
    assert await gather_fact(hosts, Groups) == {
        "web1": ["wheel", "kjarvis"],
        "web2": ["kjarvis"],
    }


# --- HasGui --------------------------------------------------------------------


async def test_has_gui_parses_baseline_gui_present_fixture():
    # The baseline's own fact fixture (tests/facts/server.HasGui/gui_present.json):
    # command "ls /usr/bin/*session || true", output ["/usr/bin/gnome-session"]
    # -> fact True.
    host = CannedShellHost("/usr/bin/gnome-session\n")
    assert await HasGui.process(host) is True
    assert host.shells == ["ls /usr/bin/*session || true"]


async def test_has_gui_parses_baseline_gui_absent_fixture():
    # The baseline's gui_absent.json fixture: output [] -> fact False (the ls
    # error is swallowed by ``|| true``, so a headless host lists nothing).
    host = CannedShellHost("")
    assert await HasGui.process(host) is False
    assert host.shells == ["ls /usr/bin/*session || true"]


def test_has_gui_parse_matches_every_known_session_binary():
    # All five binaries from the baseline's LinuxGui.known_gui_binaries count.
    for binary in (
        "/usr/bin/gnome-session",
        "/usr/bin/mate-session",
        "/usr/bin/lxsession",
        "/usr/bin/plasma_session",
        "/usr/bin/xfce4-session",
    ):
        assert HasGui.parse([binary]) is True
    assert HasGui.parse(["/usr/bin/gnome-session", "/usr/bin/xfce4-session"]) is True


def test_has_gui_parse_ignores_unknown_lines():
    # Lines are matched exactly (the baseline does not strip them): an unknown
    # *session file, a padded path or stray output is not a known GUI.
    assert HasGui.parse(["/usr/bin/unknown-session"]) is False
    assert HasGui.parse([" /usr/bin/gnome-session "]) is False
    assert HasGui.parse(["", "gnome-session"]) is False
    assert HasGui.parse([]) is False


async def test_has_gui_reads_the_simulated_seam():
    assert await HasGui.process(SimulatedHost(name="h", has_gui=True)) is True
    # The seam defaults to False: a simulated server is headless.
    assert await HasGui.process(SimulatedHost(name="h")) is False


async def test_has_gui_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", has_gui=True),
        SimulatedHost(name="web2", has_gui=False),
    ]
    assert await gather_fact(hosts, HasGui) == {"web1": True, "web2": False}


# --- Home ----------------------------------------------------------------------


async def test_home_parses_baseline_home_fixture():
    # The baseline's own fact fixture (tests/facts/server.Home/home.json):
    # command "echo ~", output ["/home/reemote"] -> fact "/home/reemote".
    host = CannedShellHost("/home/reemote\n")
    assert await Home.process(host) == "/home/reemote"
    assert host.shells == ["echo ~"]


async def test_home_parses_baseline_root_fixture():
    # The baseline's root.json fixture: arg ["root"], command "echo ~root",
    # output ["/root"] -> fact "/root".
    host = CannedShellHost("/root\n")
    assert await Home.process(host, "root") == "/root"
    assert host.shells == ["echo ~root"]


async def test_home_parses_baseline_some_user_fixture():
    # The baseline's some_user.json fixture: arg ["some_user"], command
    # "echo ~some_user", output ["/home/some_user"] -> fact "/home/some_user".
    host = CannedShellHost("/home/some_user\n")
    assert await Home.process(host, "some_user") == "/home/some_user"
    assert host.shells == ["echo ~some_user"]


def test_home_parse_takes_the_first_non_blank_line():
    assert Home.parse(["", "  /home/reemote  ", "ignored"]) == "/home/reemote"
    assert Home.parse([]) is None


def test_home_parse_maps_an_unexpanded_tilde_to_none():
    # The shell echoes ``~user`` back verbatim for an unknown user; the baseline
    # returns that literal string, reemote deliberately reads it as None.
    assert Home.parse(["~nosuchuser"]) is None


async def test_home_is_none_on_empty_output():
    assert await Home.process(CannedShellHost("")) is None


async def test_home_rejects_unsafe_tilde_usernames():
    # ``~user`` cannot be quoted without disabling the expansion, so an
    # unsafe username must raise instead of reaching the shell -- on both
    # the real-transport and the simulated path.
    for user in ("bad;rm", "a b", "$(whoami)", "-x"):
        with pytest.raises(ValueError, match="Unsafe username"):
            await Home.process(CannedShellHost(""), user)
        with pytest.raises(ValueError, match="Unsafe username"):
            await Home.process(SimulatedHost(name="h"), user)


async def test_home_reads_the_simulated_seam():
    host = SimulatedHost(
        name="h", home_directories={"": "/home/kjarvis", "root": "/root"}
    )
    assert await Home.process(host) == "/home/kjarvis"
    assert await Home.process(host, "root") == "/root"
    # An unknown user reads as None, like an unexpanded tilde.
    assert await Home.process(host, "bob") is None


async def test_home_gather_fact_forwards_the_user():
    hosts = [
        SimulatedHost(name="web1", home_directories={"kjarvis": "/home/kjarvis"}),
        SimulatedHost(name="web2", home_directories={"kjarvis": "/Users/kjarvis"}),
    ]
    assert await gather_fact(hosts, Home, "kjarvis") == {
        "web1": "/home/kjarvis",
        "web2": "/Users/kjarvis",
    }


# --- Hostname --------------------------------------------------------------------


async def test_hostname_parses_baseline_fixture():
    # The baseline's own fact fixture (tests/facts/server.Hostname/hostname.json):
    # command "uname -n", output ["my-host.net"] -> fact "my-host.net".
    host = CannedShellHost("my-host.net\n")
    assert await Hostname.process(host) == "my-host.net"
    assert host.shells == ["uname -n"]


def test_hostname_parse_takes_the_first_non_blank_line():
    assert Hostname.parse(["web1.example.net"]) == "web1.example.net"
    assert Hostname.parse(["", "  my-host.net  ", "ignored"]) == "my-host.net"
    assert Hostname.parse([]) == ""


async def test_hostname_is_empty_on_empty_output():
    assert await Hostname.process(CannedShellHost("")) == ""


async def test_hostname_reads_the_simulated_seam():
    host = SimulatedHost(name="h", os_hostname="db1.example.net")
    assert await Hostname.process(host) == "db1.example.net"
    # The seam defaults to the baseline's fixture hostname: a simulated machine
    # always answers `uname -n`, deterministically.
    assert await Hostname.process(SimulatedHost(name="h")) == "my-host.net"


async def test_hostname_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", os_hostname="web1.example.net"),
        SimulatedHost(name="web2", os_hostname="web2.example.net"),
    ]
    assert await gather_fact(hosts, Hostname) == {
        "web1": "web1.example.net",
        "web2": "web2.example.net",
    }


# --- Kernel ------------------------------------------------------------------


async def test_kernel_parses_baseline_fixture():
    # The baseline's own fact fixture (tests/facts/server.Os/os.json -- Kernel is
    # the renamed Os fact): command "uname -s", output ["os"] -> fact "os".
    host = CannedShellHost("os\n")
    assert await Kernel.process(host) == "os"
    assert host.shells == ["uname -s"]


def test_kernel_parse_takes_the_first_non_blank_line():
    assert Kernel.parse(["Linux"]) == "Linux"
    assert Kernel.parse(["", "  Darwin  ", "ignored"]) == "Darwin"
    assert Kernel.parse([]) == ""


async def test_kernel_is_empty_on_empty_output():
    assert await Kernel.process(CannedShellHost("")) == ""


async def test_kernel_reads_the_simulated_seam():
    host = SimulatedHost(name="h", os_kernel="FreeBSD")
    assert await Kernel.process(host) == "FreeBSD"
    # The seam defaults to Linux: a simulated machine always answers
    # `uname -s`, deterministically.
    assert await Kernel.process(SimulatedHost(name="h")) == "Linux"


async def test_kernel_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", os_kernel="Linux"),
        SimulatedHost(name="web2", os_kernel="Darwin"),
    ]
    assert await gather_fact(hosts, Kernel) == {
        "web1": "Linux",
        "web2": "Darwin",
    }


# --- KernelVersion -------------------------------------------------------------


async def test_kernel_version_parses_baseline_fixture():
    # The baseline's own fact fixture (tests/facts/server.OsVersion/os_version.json
    # -- KernelVersion is the renamed OsVersion fact): command "uname -r",
    # output ["os_version"] -> fact "os_version".
    host = CannedShellHost("os_version\n")
    assert await KernelVersion.process(host) == "os_version"
    assert host.shells == ["uname -r"]


def test_kernel_version_parse_takes_the_first_non_blank_line():
    assert KernelVersion.parse(["6.1.0-26-amd64"]) == "6.1.0-26-amd64"
    assert (
        KernelVersion.parse(["", "  6.11.4-301.fc41.x86_64  ", "ignored"])
        == "6.11.4-301.fc41.x86_64"
    )
    assert KernelVersion.parse([]) == ""


async def test_kernel_version_is_empty_on_empty_output():
    assert await KernelVersion.process(CannedShellHost("")) == ""


async def test_kernel_version_reads_the_simulated_seam():
    host = SimulatedHost(name="h", os_kernel_version="6.1.0-26-amd64")
    assert await KernelVersion.process(host) == "6.1.0-26-amd64"
    # The seam defaults to a real Fedora kernel release: a simulated machine
    # always answers `uname -r`, deterministically.
    assert (
        await KernelVersion.process(SimulatedHost(name="h"))
        == "6.11.4-301.fc41.x86_64"
    )


async def test_kernel_version_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", os_kernel_version="6.11.4-301.fc41.x86_64"),
        SimulatedHost(name="web2", os_kernel_version="6.1.0-26-amd64"),
    ]
    assert await gather_fact(hosts, KernelVersion) == {
        "web1": "6.11.4-301.fc41.x86_64",
        "web2": "6.1.0-26-amd64",
    }


# --- KernelModules -----------------------------------------------------------


async def test_kernel_modules_parses_baseline_linux_fixture():
    # The baseline's own fact fixture (tests/facts/server.KernelModules/
    # linux_modules.json): command
    # "! test -f /proc/modules || cat /proc/modules", five rows.
    host = CannedShellHost(
        """libahci 32768 1 ahci, Live 0x0000000000000000
i2c_piix4 24576 0 - Live 0x0000000000000000
e1000 143360 0 - Live 0x0000000000000000
pata_acpi 16384 0 - Live 0x0000000000000000
video 45056 0 - Live 0x0000000000000000
""",
    )
    assert await KernelModules.process(host) == {
        "libahci": {
            "size": "32768",
            "instances": 1,
            "state": "Live",
            "depends": ["ahci"],
        },
        "i2c_piix4": {"size": "24576", "instances": 0, "state": "Live"},
        "e1000": {"size": "143360", "instances": 0, "state": "Live"},
        "pata_acpi": {"size": "16384", "instances": 0, "state": "Live"},
        "video": {"size": "45056", "instances": 0, "state": "Live"},
    }
    assert host.shells == ["! test -f /proc/modules || cat /proc/modules"]


def test_kernel_modules_parse_linux_edge_cases():
    # A multi-dependency row with a trailing comma: empty entries are
    # dropped, only instances becomes an int, the state is kept verbatim
    # (Loading, not just Live) and the trailing address field is ignored.
    assert KernelModules.parse(
        ["bridge 245760 2 stp,llc, Loading 0x0000000000000000"]
    ) == {
        "bridge": {
            "size": "245760",
            "instances": 2,
            "state": "Loading",
            "depends": ["stp", "llc"],
        }
    }
    assert KernelModules.parse([]) == {}


def test_kernel_modules_command_is_the_proc_modules_guard():
    assert KernelModules.COMMAND == "! test -f /proc/modules || cat /proc/modules"


async def test_kernel_modules_is_empty_on_empty_output():
    # The ``! test -f`` guard short-circuits when /proc/modules is absent,
    # so a failed read maps to {} (the baseline's ``default = dict``).
    assert await KernelModules.process(CannedShellHost("")) == {}


async def test_kernel_modules_reads_the_simulated_seam():
    modules = {"e1000": {"size": "143360", "instances": 0, "state": "Live"}}
    host = SimulatedHost(name="h", kernel_modules=modules)
    assert await KernelModules.process(host) == modules
    # The seam defaults to {}: a simulated machine with no modules listed.
    assert await KernelModules.process(SimulatedHost(name="h")) == {}


async def test_kernel_modules_seam_returns_a_copy():
    host = SimulatedHost(
        name="h",
        kernel_modules={
            "libahci": {
                "size": "32768",
                "instances": 1,
                "state": "Live",
                "depends": ["ahci"],
            }
        },
    )
    modules = await KernelModules.process(host)
    modules["libahci"]["depends"].append("extra")
    modules["libahci"]["state"] = "Unloading"
    modules["video"] = {"size": "45056", "instances": 0, "state": "Live"}
    assert host.kernel_modules == {
        "libahci": {
            "size": "32768",
            "instances": 1,
            "state": "Live",
            "depends": ["ahci"],
        }
    }


async def test_kernel_modules_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(
            name="web1",
            kernel_modules={"e1000": {"size": "143360", "instances": 0, "state": "Live"}},
        ),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, KernelModules) == {
        "web1": {"e1000": {"size": "143360", "instances": 0, "state": "Live"}},
        "web2": {},
    }


# --- Last ----------------------------------------------------------------------


async def test_last_parses_baseline_full_fixture():
    # The baseline's own fact fixture (tests/facts/server.Last/last_full.json):
    # full -Fwi timestamps, a reboot row, an empty host column and the
    # wtmp footer, reused verbatim for parity.
    host = CannedShellHost(
        """alice    pts/0        192.168.1.5      Thu Apr 17 14:00:00 2026   still logged in
bob      pts/1        10.0.0.12        Thu Apr 17 12:00:00 2026 - Thu Apr 17 13:00:00 2026  (01:00)
reboot   system boot  6.19.10-arch1-1  Thu Apr 17 11:00:00 2026 - Thu Apr 17 12:00:00 2026  (01:00)
carol    pts/2                         Thu Apr 17 09:00:00 2026 - Thu Apr 17 09:30:00 2026  (00:30)

wtmp begins Thu Apr 17 09:00:00 2026
"""
    )
    assert await Last.process(host) == [
        {
            "user": "alice",
            "tty": "pts/0",
            "host": "192.168.1.5",
            "time": "Thu Apr 17 14:00:00 2026 still logged in",
        },
        {
            "user": "bob",
            "tty": "pts/1",
            "host": "10.0.0.12",
            "time": "Thu Apr 17 12:00:00 2026 - Thu Apr 17 13:00:00 2026 (01:00)",
        },
        {
            "user": "reboot",
            "tty": "system boot",
            "host": "6.19.10-arch1-1",
            "time": "Thu Apr 17 11:00:00 2026 - Thu Apr 17 12:00:00 2026 (01:00)",
        },
        {
            "user": "carol",
            "tty": "pts/2",
            "host": "",
            "time": "Thu Apr 17 09:00:00 2026 - Thu Apr 17 09:30:00 2026 (00:30)",
        },
    ]
    assert host.shells == [Last.COMMAND]


def test_last_command_matches_baseline():
    # The fixture's command field: util-linux -Fwi, FreeBSD -w, then the
    # bare busybox-compatible fallback, all errors swallowed.
    assert (
        Last.COMMAND
        == "last -Fwi 2>/dev/null || last -w 2>/dev/null || last 2>/dev/null || true"
    )


async def test_last_parses_baseline_freebsd_fixture():
    # The baseline's last_freebsd.json fixture: short (no -F) timestamps and a
    # hostname instead of an IP, reused verbatim.
    host = CannedShellHost(
        """root       pts/1    golgoth.tosser.interna Fri Apr 17 11:56   still logged in
root       pts/1    golgoth.tosser.interna Fri Apr 17 11:52 - 11:52  (00:00:08)

wtmp begins Sat Apr 12 10:00:00 EDT 2026
"""
    )
    assert await Last.process(host) == [
        {
            "user": "root",
            "tty": "pts/1",
            "host": "golgoth.tosser.interna",
            "time": "Fri Apr 17 11:56 still logged in",
        },
        {
            "user": "root",
            "tty": "pts/1",
            "host": "golgoth.tosser.interna",
            "time": "Fri Apr 17 11:52 - 11:52 (00:00:08)",
        },
    ]


async def test_last_alpine_header_only_fixture_reads_empty():
    # The baseline's last_alpine_empty.json fixture: busybox last prints only a
    # header row, which is skipped -> [].
    host = CannedShellHost(
        "USER       TTY            HOST               LOGIN        TIME\n"
    )
    assert await Last.process(host) == []


def test_last_parses_baseline_documented_example():
    # The baseline's docstring example result: the parser joins time tokens with
    # single spaces, collapsing the output's column padding.
    assert Last.parse(
        [
            "alice    pts/0        192.168.1.5      Thu Apr 17 14:00   still logged in",
            "reboot   system boot  6.19.10-arch1-1  Thu Apr 17 11:00 - 12:00  (01:00)",
        ]
    ) == [
        {
            "user": "alice",
            "tty": "pts/0",
            "host": "192.168.1.5",
            "time": "Thu Apr 17 14:00 still logged in",
        },
        {
            "user": "reboot",
            "tty": "system boot",
            "host": "6.19.10-arch1-1",
            "time": "Thu Apr 17 11:00 - 12:00 (01:00)",
        },
    ]


def test_last_parse_edge_cases():
    # btmp footers and username headers are skipped case-insensitively;
    # rows with fewer than three fields and a bare "reboot system boot"
    # (no remainder) are dropped.
    assert (
        Last.parse(
            [
                "USERNAME   TTY      FROM             LOGIN@   IDLE",
                "Btmp begins Thu Apr 17 09:00:00 2026",
                "reboot system boot",
                "root pts/1",
                "x",
                "",
            ]
        )
        == []
    )
    # A weekday right after the tty means no host column; a non-weekday
    # remainder is the host even when no time tokens follow it.
    assert Last.parse(
        [
            "carol pts/2 Thu Apr 17 09:00 - 09:30 (00:30)",
            "root pts/1 somehost",
        ]
    ) == [
        {
            "user": "carol",
            "tty": "pts/2",
            "host": "",
            "time": "Thu Apr 17 09:00 - 09:30 (00:30)",
        },
        {"user": "root", "tty": "pts/1", "host": "somehost", "time": ""},
    ]


async def test_last_is_empty_on_empty_output():
    # `last` missing or failed: the shell's `|| true` swallows the error,
    # so empty stdout maps to [] (the baseline's default = list).
    assert await Last.process(CannedShellHost("")) == []


async def test_last_reads_the_simulated_seam():
    records = [
        {
            "user": "alice",
            "tty": "pts/0",
            "host": "192.168.1.5",
            "time": "Thu Apr 17 14:00 still logged in",
        }
    ]
    host = SimulatedHost(name="h", login_records=records)
    assert await Last.process(host) == records
    # The seam defaults to []: a simulated machine with no recorded logins.
    assert await Last.process(SimulatedHost(name="h")) == []


async def test_last_seam_returns_a_copy():
    host = SimulatedHost(
        name="h",
        login_records=[
            {"user": "root", "tty": "pts/1", "host": "10.0.0.12", "time": "Fri 11:56"}
        ],
    )
    records = await Last.process(host)
    records[0]["user"] = "mallory"
    records.append({"user": "bob", "tty": "pts/2", "host": "", "time": ""})
    assert host.login_records == [
        {"user": "root", "tty": "pts/1", "host": "10.0.0.12", "time": "Fri 11:56"}
    ]


async def test_last_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(
            name="web1",
            login_records=[
                {"user": "root", "tty": "pts/1", "host": "10.0.0.12", "time": "Fri 11:56"}
            ],
        ),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, Last) == {
        "web1": [
            {"user": "root", "tty": "pts/1", "host": "10.0.0.12", "time": "Fri 11:56"}
        ],
        "web2": [],
    }


# --- LinuxDistribution ---------------------------------------------------------

# The baseline's own fact fixtures (tests/facts/server.LinuxDistribution/*.json),
# reused verbatim for parity: each entry is the canned shell output (the
# ---separated /etc/*-release dump) and the fact the baseline expects from it.
LINUX_DISTRIBUTION_FIXTURES = [
    pytest.param(
        """/etc/arch-release
---
/etc/os-release
NAME="Arch Linux"
PRETTY_NAME="Arch Linux"
ID=arch
BUILD_ID=rolling
ANSI_COLOR="38;2;23;147;209"
HOME_URL="https://archlinux.org/"
DOCUMENTATION_URL="https://wiki.archlinux.org/"
SUPPORT_URL="https://bbs.archlinux.org/"
BUG_REPORT_URL="https://bugs.archlinux.org/"
LOGO=archlinux
---
""",
        {
            "name": "Arch Linux",
            "major": None,
            "minor": None,
            "release_meta": {
                "NAME": "Arch Linux",
                "PRETTY_NAME": "Arch Linux",
                "ID": "arch",
                "BUILD_ID": "rolling",
                "ANSI_COLOR": "38;2;23;147;209",
                "HOME_URL": "https://archlinux.org/",
                "DOCUMENTATION_URL": "https://wiki.archlinux.org/",
                "SUPPORT_URL": "https://bbs.archlinux.org/",
                "BUG_REPORT_URL": "https://bugs.archlinux.org/",
                "LOGO": "archlinux",
            },
        },
        id="archlinux",
    ),
    pytest.param(
        """/etc/centos-release
CentOS release 6.9 (Final)
---
/etc/os-release
LSB_VERSION=base-4.0-amd64:base-4.0-noarch:core-4.0-amd64:core-4.0-noarch
---
""",
        {
            "name": "CentOS",
            "major": 6,
            "minor": 9,
            "release_meta": {
                "LSB_VERSION": "base-4.0-amd64:base-4.0-noarch:core-4.0-amd64:core-4.0-noarch"
            },
        },
        id="centos-6",
    ),
    pytest.param(
        """/etc/centos-release
CentOS Linux release 7.9.2009 (Core)
---
/etc/os-release
NAME="CentOS Linux"
VERSION="7 (Core)"
ID="centos"
ID_LIKE="rhel fedora"
VERSION_ID="7"
PRETTY_NAME="CentOS Linux 7 (Core)"
ANSI_COLOR="0;31"
CPE_NAME="cpe:/o:centos:centos:7"
HOME_URL="https://www.centos.org/"
BUG_REPORT_URL="https://bugs.centos.org/"

CENTOS_MANTISBT_PROJECT="CentOS-7"
CENTOS_MANTISBT_PROJECT_VERSION="7"
REDHAT_SUPPORT_PRODUCT="centos"
REDHAT_SUPPORT_PRODUCT_VERSION="7"

---
/etc/redhat-release
CentOS Linux release 7.9.2009 (Core)
---
/etc/system-release
CentOS Linux release 7.9.2009 (Core)
---
""",
        {
            "name": "CentOS",
            "major": 7,
            "minor": 9,
            "release_meta": {
                "NAME": "CentOS Linux",
                "VERSION": "7 (Core)",
                "ID": "centos",
                "ID_LIKE": "rhel fedora",
                "VERSION_ID": "7",
                "PRETTY_NAME": "CentOS Linux 7 (Core)",
                "ANSI_COLOR": "0;31",
                "CPE_NAME": "cpe:/o:centos:centos:7",
                "HOME_URL": "https://www.centos.org/",
                "BUG_REPORT_URL": "https://bugs.centos.org/",
                "CENTOS_MANTISBT_PROJECT": "CentOS-7",
                "CENTOS_MANTISBT_PROJECT_VERSION": "7",
                "REDHAT_SUPPORT_PRODUCT": "centos",
                "REDHAT_SUPPORT_PRODUCT_VERSION": "7",
                "CODENAME": "Core",
            },
        },
        id="centos-7",
    ),
    pytest.param(
        """/etc/centos-release
CentOS Linux release 8.4.2105
---
/etc/os-release
NAME="CentOS Linux"
VERSION="8"
ID="centos"
ID_LIKE="rhel fedora"
VERSION_ID="8"
PLATFORM_ID="platform:el8"
PRETTY_NAME="CentOS Linux 8"
ANSI_COLOR="0;31"
CPE_NAME="cpe:/o:centos:centos:8"
HOME_URL="https://centos.org/"
BUG_REPORT_URL="https://bugs.centos.org/"
CENTOS_MANTISBT_PROJECT="CentOS-8"
CENTOS_MANTISBT_PROJECT_VERSION="8"
---
/etc/redhat-release
CentOS Linux release 8.4.2105
---
/etc/system-release
CentOS Linux release 8.4.2105
---
""",
        {
            "name": "CentOS",
            "major": 8,
            "minor": 4,
            "release_meta": {
                "NAME": "CentOS Linux",
                "VERSION": "8",
                "ID": "centos",
                "ID_LIKE": "rhel fedora",
                "VERSION_ID": "8",
                "PLATFORM_ID": "platform:el8",
                "PRETTY_NAME": "CentOS Linux 8",
                "ANSI_COLOR": "0;31",
                "CPE_NAME": "cpe:/o:centos:centos:8",
                "HOME_URL": "https://centos.org/",
                "BUG_REPORT_URL": "https://bugs.centos.org/",
                "CENTOS_MANTISBT_PROJECT": "CentOS-8",
                "CENTOS_MANTISBT_PROJECT_VERSION": "8",
            },
        },
        id="centos-8",
    ),
    pytest.param(
        """/etc/centos-release
CentOS Stream release 9
---
/etc/os-release
NAME="CentOS Stream"
VERSION="9"
ID="centos"
ID_LIKE="rhel fedora"
VERSION_ID="9"
PLATFORM_ID="platform:el9"
PRETTY_NAME="CentOS Stream 9"
ANSI_COLOR="0;31"
LOGO="fedora-logo-icon"
CPE_NAME="cpe:/o:centos:centos:9"
HOME_URL="https://centos.org/"
BUG_REPORT_URL="https://issues.redhat.com/"
REDHAT_SUPPORT_PRODUCT="Red Hat Enterprise Linux 9"
REDHAT_SUPPORT_PRODUCT_VERSION="CentOS Stream"
---
/etc/redhat-release
CentOS Stream release 9
---
/etc/system-release
CentOS Stream release 9
---
""",
        {
            "name": "CentOS",
            "major": 9,
            "minor": None,
            "release_meta": {
                "NAME": "CentOS Stream",
                "VERSION": "9",
                "ID": "centos",
                "ID_LIKE": "rhel fedora",
                "VERSION_ID": "9",
                "PLATFORM_ID": "platform:el9",
                "PRETTY_NAME": "CentOS Stream 9",
                "ANSI_COLOR": "0;31",
                "LOGO": "fedora-logo-icon",
                "CPE_NAME": "cpe:/o:centos:centos:9",
                "HOME_URL": "https://centos.org/",
                "BUG_REPORT_URL": "https://issues.redhat.com/",
                "REDHAT_SUPPORT_PRODUCT": "Red Hat Enterprise Linux 9",
                "REDHAT_SUPPORT_PRODUCT_VERSION": "CentOS Stream",
            },
        },
        id="centos-stream-9",
    ),
    pytest.param(
        """/etc/fedora-release
Fedora release 40 (Forty)
---
/etc/os-release
NAME="Fedora Linux"
VERSION="40 (Forty)"
ID=fedora
VERSION_ID=40
VERSION_CODENAME=""
PLATFORM_ID="platform:f40"
PRETTY_NAME="Fedora Linux 40 (Forty)"
ANSI_COLOR="0;38;2;60;110;180"
LOGO=fedora-logo-icon
CPE_NAME="cpe:/o:fedoraproject:fedora:40"
DEFAULT_HOSTNAME="fedora"
HOME_URL="https://fedoraproject.org/"
DOCUMENTATION_URL="https://docs.fedoraproject.org/en-US/fedora/f40/system-administrators-guide/"
SUPPORT_URL="https://ask.fedoraproject.org/"
BUG_REPORT_URL="https://bugzilla.redhat.com/"
REDHAT_BUGZILLA_PRODUCT="Fedora"
REDHAT_BUGZILLA_PRODUCT_VERSION=40
REDHAT_SUPPORT_PRODUCT="Fedora"
REDHAT_SUPPORT_PRODUCT_VERSION=40
SUPPORT_END=2025-05-13
---
""",
        {
            "name": "Fedora",
            "major": 40,
            "minor": None,
            "release_meta": {
                "NAME": "Fedora Linux",
                "VERSION": "40 (Forty)",
                "ID": "fedora",
                "VERSION_ID": "40",
                "VERSION_CODENAME": "",
                "PLATFORM_ID": "platform:f40",
                "PRETTY_NAME": "Fedora Linux 40 (Forty)",
                "ANSI_COLOR": "0;38;2;60;110;180",
                "LOGO": "fedora-logo-icon",
                "CPE_NAME": "cpe:/o:fedoraproject:fedora:40",
                "DEFAULT_HOSTNAME": "fedora",
                "HOME_URL": "https://fedoraproject.org/",
                "DOCUMENTATION_URL": "https://docs.fedoraproject.org/en-US/fedora/f40/system-administrators-guide/",
                "SUPPORT_URL": "https://ask.fedoraproject.org/",
                "BUG_REPORT_URL": "https://bugzilla.redhat.com/",
                "REDHAT_BUGZILLA_PRODUCT": "Fedora",
                "REDHAT_BUGZILLA_PRODUCT_VERSION": "40",
                "REDHAT_SUPPORT_PRODUCT": "Fedora",
                "REDHAT_SUPPORT_PRODUCT_VERSION": "40",
                "SUPPORT_END": "2025-05-13",
                "CODENAME": "",
            },
        },
        id="fedora-40",
    ),
    pytest.param(
        "",
        {"name": None, "major": None, "minor": None, "release_meta": {}},
        id="no_match",
    ),
    pytest.param(
        """/etc/os-release
NAME="RED OS"
VERSION="8.0"
PLATFORM_ID="platform:red80"
ID="redos"
ID_LIKE="rhel centos fedora"
VERSION_ID="8.0"
PRETTY_NAME="RED OS 8.0"
ANSI_COLOR="0;31"
CPE_NAME="cpe:/o:redos:redos:8"
HOME_URL="https://redos.red-soft.ru"
BUG_REPORT_URL="https://support.red-soft.ru"
EDITION="Standard"

---
/etc/redhat-release
RED OS release (8.0) MINIMAL
---
/etc/redos-release
RED OS release (8.0) MINIMAL
---
/etc/system-release
RED OS release (8.0) MINIMAL
---
""",
        {
            "name": "RED OS",
            "major": 8,
            "minor": 0,
            "release_meta": {
                "NAME": "RED OS",
                "VERSION": "8.0",
                "PLATFORM_ID": "platform:red80",
                "ID": "redos",
                "ID_LIKE": "rhel centos fedora",
                "VERSION_ID": "8.0",
                "PRETTY_NAME": "RED OS 8.0",
                "ANSI_COLOR": "0;31",
                "CPE_NAME": "cpe:/o:redos:redos:8",
                "HOME_URL": "https://redos.red-soft.ru",
                "BUG_REPORT_URL": "https://support.red-soft.ru",
                "EDITION": "Standard",
            },
        },
        id="redos-8-0",
    ),
    pytest.param(
        """/etc/os-release
NAME="Ubuntu"
VERSION="20.04 LTS (Focal Fossa)"
VERSION_ID="20.04"
---
""",
        {
            "name": "Ubuntu",
            "major": 20,
            "minor": 4,
            "release_meta": {
                "NAME": "Ubuntu",
                "VERSION": "20.04 LTS (Focal Fossa)",
                "VERSION_ID": "20.04",
                "CODENAME": "Focal Fossa",
            },
        },
        id="ubuntu",
    ),
]


@pytest.mark.parametrize(("output", "expected"), LINUX_DISTRIBUTION_FIXTURES)
async def test_linux_distribution_matches_baseline_fixtures(output, expected):
    host = CannedShellHost(output)
    assert await LinuxDistribution.process(host) == expected
    assert host.shells == [LinuxDistribution.COMMAND]


def test_linux_distribution_command_matches_baseline():
    # The command every baseline fixture records, verbatim.
    assert LinuxDistribution.COMMAND == (
        'cd /etc/ && for file in $(ls -pdL *-release | grep -v /); '
        'do echo "/etc/${file}"; cat "/etc/${file}"; echo ---; '
        "done"
    )


def test_linux_distribution_parse_skips_empty_release_files():
    # Arch's /etc/arch-release is an empty file: a part with only a filename
    # is skipped, leaving no parts and the baseline's default dict.
    assert LinuxDistribution.parse(["/etc/arch-release", "---"]) == {
        "name": None,
        "major": None,
        "minor": None,
        "release_meta": {},
    }


def test_linux_distribution_parse_empty_output():
    assert LinuxDistribution.parse([]) == {
        "name": None,
        "major": None,
        "minor": None,
        "release_meta": {},
    }


async def test_linux_distribution_reads_the_simulated_seam():
    info = {
        "name": "Fedora",
        "major": 40,
        "minor": None,
        "release_meta": {"ID": "fedora", "VERSION_ID": "40"},
    }
    host = SimulatedHost(name="h", linux_distribution=info)
    assert await LinuxDistribution.process(host) == info
    # The seam defaults to the baseline's default(): no distro claim until set.
    assert await LinuxDistribution.process(SimulatedHost(name="h")) == {
        "name": None,
        "major": None,
        "minor": None,
        "release_meta": {},
    }


async def test_linux_distribution_seam_returns_a_copy():
    host = SimulatedHost(
        name="h",
        linux_distribution={
            "name": "CentOS",
            "major": 9,
            "minor": None,
            "release_meta": {"ID": "centos"},
        },
    )
    info = await LinuxDistribution.process(host)
    info["name"] = "MalloryOS"
    info["release_meta"]["ID"] = "mallory"
    assert host.linux_distribution == {
        "name": "CentOS",
        "major": 9,
        "minor": None,
        "release_meta": {"ID": "centos"},
    }


async def test_linux_distribution_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(
            name="web1",
            linux_distribution={
                "name": "Fedora",
                "major": 40,
                "minor": None,
                "release_meta": {},
            },
        ),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, LinuxDistribution) == {
        "web1": {"name": "Fedora", "major": 40, "minor": None, "release_meta": {}},
        "web2": {"name": None, "major": None, "minor": None, "release_meta": {}},
    }


# --- LinuxGui ------------------------------------------------------------------


async def test_linux_gui_parses_baseline_gnome_present_fixture():
    # The baseline's own fact fixture (tests/facts/server.LinuxGui/gnome_present.json):
    # command "ls /usr/bin/*session || true", output ["/usr/bin/gnome-session"]
    # -> fact ["GNOME"].
    host = CannedShellHost("/usr/bin/gnome-session\n")
    assert await LinuxGui.process(host) == ["GNOME"]
    assert host.shells == ["ls /usr/bin/*session || true"]


def test_linux_gui_command_and_binaries_match_has_gui():
    # The two facts share the shell and the known-binary map: HasGui is the
    # boolean projection of LinuxGui's list.
    assert LinuxGui.COMMAND == HasGui.COMMAND == "ls /usr/bin/*session || true"
    assert frozenset(LinuxGui.KNOWN_GUI_BINARIES) == HasGui.GUI_SESSION_BINARIES


def test_linux_gui_parse_maps_every_known_session_binary():
    # All five binaries the baseline's known_gui_binaries recognises, in listing
    # order -- the names come back in the same order (no sorting).
    assert LinuxGui.parse(
        [
            "/usr/bin/gnome-session",
            "/usr/bin/lxsession",
            "/usr/bin/mate-session",
            "/usr/bin/plasma_session",
            "/usr/bin/xfce4-session",
        ]
    ) == ["GNOME", "LXDE", "MATE", "KDE Plasma", "XFCE 4"]


def test_linux_gui_parse_ignores_unknown_lines():
    # Lines are matched exactly (the baseline does not strip them).
    assert LinuxGui.parse(["/usr/bin/unknown-session"]) == []
    assert LinuxGui.parse([" /usr/bin/gnome-session "]) == []
    assert LinuxGui.parse(["", "gnome-session"]) == []
    assert LinuxGui.parse([]) == []


async def test_linux_gui_is_empty_on_empty_output():
    # The baseline's server.HasGui/gui_absent.json output ([]): no *session match,
    # the ls error swallowed by || true -- reads as [] (default = list).
    assert await LinuxGui.process(CannedShellHost("")) == []


async def test_linux_gui_agrees_with_has_gui_over_the_same_output():
    present = "/usr/bin/xfce4-session\n"
    assert await LinuxGui.process(CannedShellHost(present)) == ["XFCE 4"]
    assert await HasGui.process(CannedShellHost(present)) is True
    assert await LinuxGui.process(CannedShellHost("")) == []
    assert await HasGui.process(CannedShellHost("")) is False


async def test_linux_gui_reads_the_simulated_seam():
    host = SimulatedHost(name="h", linux_gui=["GNOME", "MATE"])
    assert await LinuxGui.process(host) == ["GNOME", "MATE"]
    # The seam defaults to []: a simulated server is headless.
    assert await LinuxGui.process(SimulatedHost(name="h")) == []


async def test_linux_gui_seam_returns_a_copy():
    host = SimulatedHost(name="h", linux_gui=["GNOME"])
    guis = await LinuxGui.process(host)
    guis.append("MATE")
    assert host.linux_gui == ["GNOME"]


async def test_linux_gui_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", linux_gui=["KDE Plasma"], has_gui=True),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, LinuxGui) == {
        "web1": ["KDE Plasma"],
        "web2": [],
    }


# --- LinuxName -----------------------------------------------------------------


async def test_linux_name_parses_baseline_ubuntu_fixture():
    # The baseline's own fact fixture (tests/facts/server.LinuxName/ubuntu.json):
    # the LinuxDistribution release-file dump command, output
    # ["/etc/os-release", 'NAME="Ubuntu"'] -> fact "Ubuntu".
    host = CannedShellHost('/etc/os-release\nNAME="Ubuntu"\n')
    assert await LinuxName.process(host) == "Ubuntu"
    # The delegation runs LinuxDistribution's shell, not a second command.
    assert host.shells == [LinuxDistribution.COMMAND]


@pytest.mark.parametrize(("output", "expected"), LINUX_DISTRIBUTION_FIXTURES)
async def test_linux_name_matches_baseline_linux_distribution_fixtures(output, expected):
    # LinuxName is the name projection of LinuxDistribution, so every one of
    # The baseline's nine distribution fixtures doubles as a LinuxName fixture.
    host = CannedShellHost(output)
    assert await LinuxName.process(host) == expected["name"]


async def test_linux_name_is_none_on_empty_output():
    # No release files at all: the baseline's default() name is None.
    assert await LinuxName.process(CannedShellHost("")) is None


async def test_linux_name_agrees_with_linux_distribution():
    for output in ('/etc/os-release\nNAME="Ubuntu"\n', ""):
        info = await LinuxDistribution.process(CannedShellHost(output))
        assert await LinuxName.process(CannedShellHost(output)) == info["name"]


async def test_linux_name_reads_the_simulated_seam():
    # The delegation shares LinuxDistribution's linux_distribution seam:
    # there is no separate name field to keep consistent.
    host = SimulatedHost(
        name="h",
        linux_distribution={
            "name": "Fedora",
            "major": 40,
            "minor": None,
            "release_meta": {},
        },
    )
    assert await LinuxName.process(host) == "Fedora"
    # The seam defaults to the baseline's default(): no distro claim until set.
    assert await LinuxName.process(SimulatedHost(name="h")) is None


async def test_linux_name_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(
            name="web1",
            linux_distribution={
                "name": "Fedora",
                "major": 40,
                "minor": None,
                "release_meta": {},
            },
        ),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, LinuxName) == {"web1": "Fedora", "web2": None}


# --- Locales -------------------------------------------------------------------


async def test_locales_parses_baseline_documented_example():
    # The baseline's documented example result (server.Locales docstring):
    # command "locale -a", output ["C.utf8", "en_US.utf8"] -> fact
    # ["C.UTF-8", "en_US.UTF-8"]. The baseline ships no fixture for this fact.
    host = CannedShellHost("C.utf8\nen_US.utf8\n")
    assert await Locales.process(host) == ["C.UTF-8", "en_US.UTF-8"]
    assert host.shells == ["locale -a"]


def test_locales_parse_normalizes_only_lowercase_utf8():
    # Only the lowercase "utf8" spelling is replaced (to match the names in
    # /etc/locale.gen); already-normalized and non-UTF-8 names pass through.
    assert Locales.parse(
        ["C", "POSIX", "en_GB.utf8", "de_DE.UTF-8", "fr_FR.iso88591"]
    ) == ["C", "POSIX", "en_GB.UTF-8", "de_DE.UTF-8", "fr_FR.iso88591"]


def test_locales_parse_preserves_order_without_dedup():
    # The baseline maps every line verbatim: no sorting, no dedup, no stripping.
    assert Locales.parse(["en_US.utf8", "C.utf8", "en_US.utf8"]) == [
        "en_US.UTF-8",
        "C.UTF-8",
        "en_US.UTF-8",
    ]
    assert Locales.parse([]) == []


async def test_locales_is_empty_on_empty_output():
    # locale missing or failed: empty stdout reads as [] (default = list).
    assert await Locales.process(CannedShellHost("")) == []


async def test_locales_reads_the_simulated_seam():
    host = SimulatedHost(name="h", locales=["C.UTF-8", "en_US.UTF-8"])
    assert await Locales.process(host) == ["C.UTF-8", "en_US.UTF-8"]
    # The seam defaults to []: no locales listed until a test sets them.
    assert await Locales.process(SimulatedHost(name="h")) == []


async def test_locales_seam_returns_a_copy():
    host = SimulatedHost(name="h", locales=["C.UTF-8"])
    locales = await Locales.process(host)
    locales.append("en_US.UTF-8")
    assert host.locales == ["C.UTF-8"]


async def test_locales_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", locales=["C.UTF-8", "en_US.UTF-8"]),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, Locales) == {
        "web1": ["C.UTF-8", "en_US.UTF-8"],
        "web2": [],
    }


# --- LsbRelease ----------------------------------------------------------------


class MissingLsbReleaseHost(Host):
    """Answers the shell with exit 127: lsb_release is not installed."""

    async def _run_shell(self, shell: str):
        return SimpleNamespace(
            exit_status=127,
            stdout="",
            stderr="-bash: lsb_release: command not found",
        )


async def test_lsb_release_parses_baseline_release_fixture():
    # The baseline's own fact fixture (tests/facts/server.LsbRelease/release.json):
    # command "lsb_release -ca", the Ubuntu 18.04.3 listing below -> the
    # four-key dict, with the "No LSB modules" hint line skipped.
    host = CannedShellHost(
        """No LSB modules are available.
Distributor ID: Ubuntu
Description:    Ubuntu 18.04.3 LTS
Release:    18.04
Codename:   bionic
"""
    )
    assert await LsbRelease.process(host) == {
        "id": "Ubuntu",
        "description": "Ubuntu 18.04.3 LTS",
        "release": "18.04",
        "codename": "bionic",
    }
    assert host.shells == ["lsb_release -ca"]


def test_lsb_release_parse_skips_lines_without_a_colon():
    # The hint line, blank lines and stray output carry no key/value pair.
    assert LsbRelease.parse(
        ["No LSB modules are available.", "", "Release: 18.04", "stray output"]
    ) == {"release": "18.04"}


def test_lsb_release_parse_normalizes_keys_and_splits_once():
    # Keys are stripped, lower-cased and reduced to their last word
    # ("Distributor ID" -> "id"); only the first colon splits, so a value
    # may contain colons, and values are stripped but kept verbatim.
    assert LsbRelease.parse(
        ["Distributor ID: Ubuntu", "  Some Long Key :  a: b: c  "]
    ) == {"id": "Ubuntu", "key": "a: b: c"}


async def test_lsb_release_is_empty_on_empty_output():
    # A failed lsb_release read (an exit other than 127): empty stdout
    # parses to {}.
    assert await LsbRelease.process(CannedShellHost("")) == {}


async def test_lsb_release_raises_when_the_command_is_not_installed():
    # lsb_release missing (e.g. a minimal CentOS install): the shell exits
    # 127, which run_shell reports as CommandNotFoundError instead of the
    # fact silently reading as {}.
    host = MissingLsbReleaseHost(name="web1")
    with pytest.raises(CommandNotFoundError) as excinfo:
        await LsbRelease.process(host)
    assert excinfo.value.command == "lsb_release -ca"
    assert "command not found on web1" in str(excinfo.value)


async def test_lsb_release_gather_fact_propagates_the_missing_command_error():
    # gather_fact lets the typed error through, and it IS a CommandError --
    # the guard the example scripts already carry catches it and prints the
    # message without a traceback.
    with pytest.raises(CommandError):
        await gather_fact([MissingLsbReleaseHost(name="web1")], LsbRelease)


async def test_lsb_release_gather_fact_names_every_host_missing_the_command():
    # Both hosts lack lsb_release: the aggregate names them both (in input
    # order), not just the first to fail, and keeps the first per-host error
    # reachable as __cause__.
    hosts = [MissingLsbReleaseHost(name="web1"), MissingLsbReleaseHost(name="web2")]
    with pytest.raises(CommandNotFoundError) as excinfo:
        await gather_fact(hosts, LsbRelease)
    assert "command not found on web1, web2 (exit 127)" in str(excinfo.value)
    assert excinfo.value.command == "lsb_release -ca"
    assert isinstance(excinfo.value.__cause__, CommandNotFoundError)
    assert excinfo.value.__cause__.host == "web1"


async def test_lsb_release_gather_fact_single_missing_host_among_healthy_ones():
    # One healthy host does not mask the other's missing command, and a lone
    # error passes through unchanged (no aggregate wrapper, no partial map).
    hosts = [
        SimulatedHost(name="web1", lsb_release={"id": "Ubuntu"}),
        MissingLsbReleaseHost(name="web2"),
    ]
    with pytest.raises(CommandNotFoundError) as excinfo:
        await gather_fact(hosts, LsbRelease)
    assert excinfo.value.host == "web2"
    assert "command not found on web2 (exit 127)" in str(excinfo.value)


async def test_lsb_release_gather_fact_other_exceptions_propagate_unchanged():
    # A genuine error is not swallowed by the missing-command aggregation,
    # even when another host is missing the command in the same fan-out.
    class BrokenShellHost(Host):
        async def _run_shell(self, shell: str):
            raise ValueError("transport bug")

    hosts = [MissingLsbReleaseHost(name="web1"), BrokenShellHost(name="web2")]
    with pytest.raises(ValueError, match="transport bug"):
        await gather_fact(hosts, LsbRelease)


async def test_lsb_release_reads_the_simulated_seam():
    host = SimulatedHost(name="h", lsb_release={"id": "Ubuntu", "release": "18.04"})
    assert await LsbRelease.process(host) == {"id": "Ubuntu", "release": "18.04"}
    # The seam defaults to {}: no LSB claim until a test sets one.
    assert await LsbRelease.process(SimulatedHost(name="h")) == {}


async def test_lsb_release_seam_returns_a_copy():
    host = SimulatedHost(name="h", lsb_release={"id": "Ubuntu"})
    release = await LsbRelease.process(host)
    release["codename"] = "bionic"
    assert host.lsb_release == {"id": "Ubuntu"}


async def test_lsb_release_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", lsb_release={"id": "Ubuntu", "release": "18.04"}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, LsbRelease) == {
        "web1": {"id": "Ubuntu", "release": "18.04"},
        "web2": {},
    }


# --- Mounts --------------------------------------------------------------------


async def test_mounts_parses_baseline_linux_fixture():
    # The baseline's own fact fixture (tests/facts/server.Mounts/mounts.json):
    # command "cat /proc/self/mountinfo", three rows --
    # including the octal-escaped overlay mount point, device options and
    # a "shared" optional tag, reused verbatim for parity.
    host = CannedShellHost(
        r"""2 1 0:26 / / ro,noatime - ext4 /dev/sdb1 rw
33 29 8:1 / /boot rw,relatime - vfat /dev/sda1 rw,lazytime,fmask=0022,dmask=0022,codepage=437,iocharset=iso8859-1,shortname=mixed,utf8,errors=remount-ro
408 35 0:352 / /mnt/overlay/overlay,dir\040with\040spaces\040and\040\134backslashes ro,relatime shared:27 - overlay none ro,lowerdir=lower\054dir,upperdir=upper\054dir,workdir=work\054dir
""",
    )
    assert await Mounts.process(host) == {
        "/": {
            "device": "/dev/sdb1",
            "type": "ext4",
            "options": ["ro", "noatime"],
        },
        "/boot": {
            "device": "/dev/sda1",
            "type": "vfat",
            "options": [
                "rw",
                "relatime",
                "lazytime",
                "fmask=0022",
                "dmask=0022",
                "codepage=437",
                "iocharset=iso8859-1",
                "shortname=mixed",
                "utf8",
                "errors=remount-ro",
            ],
        },
        "/mnt/overlay/overlay,dir with spaces and \\backslashes": {
            "device": "none",
            "type": "overlay",
            "options": [
                "ro",
                "relatime",
                "lowerdir=lower,dir",
                "upperdir=upper,dir",
                "workdir=work,dir",
            ],
        },
    }
    assert host.shells == ["cat /proc/self/mountinfo"]


def test_mounts_parses_baseline_documented_example():
    # The baseline's docstring example result: "/" on /dev/mv2, ext4, rw+relatime.
    # The super-block "rw" is filtered so it never appears twice.
    assert Mounts.parse(
        ["29 1 253:0 / / rw,relatime - ext4 /dev/mv2 rw"]
    ) == {"/": {"device": "/dev/mv2", "type": "ext4", "options": ["rw", "relatime"]}}


def test_mounts_parse_linux_edge_cases():
    # Several optional tags before the "-" separator are all skipped, blank
    # lines are ignored, and a repeated mount point keeps the last row.
    assert Mounts.parse(
        [
            "25 1 0:23 / /sys rw,nosuid shared:7 master:2 - sysfs sysfs rw",
            "",
            "30 1 0:23 / /sys rw - sysfs old rw",
            "31 1 0:23 / /sys ro - sysfs new ro",
        ],
    ) == {"/sys": {"device": "new", "type": "sysfs", "options": ["ro"]}}
    assert Mounts.parse([]) == {}


def test_mounts_command_is_the_mountinfo_read():
    assert Mounts.COMMAND == "cat /proc/self/mountinfo"


async def test_mounts_is_empty_on_empty_output():
    # A failed read maps to {} (the baseline's ``default = dict``).
    assert await Mounts.process(CannedShellHost("")) == {}


async def test_mounts_reads_the_simulated_seam():
    mounts = {"/": {"device": "/dev/sdb1", "type": "ext4", "options": ["rw"]}}
    host = SimulatedHost(name="h", mounts=mounts)
    assert await Mounts.process(host) == mounts
    # The seam defaults to {}: a simulated machine with no mounts listed.
    assert await Mounts.process(SimulatedHost(name="h")) == {}


async def test_mounts_seam_returns_a_copy():
    host = SimulatedHost(
        name="h",
        mounts={"/": {"device": "/dev/sdb1", "type": "ext4", "options": ["rw"]}},
    )
    mounts = await Mounts.process(host)
    mounts["/"]["options"].append("noatime")
    mounts["/"]["device"] = "/dev/sdc1"
    mounts["/boot"] = {"device": "/dev/sda1", "type": "vfat", "options": ["rw"]}
    assert host.mounts == {
        "/": {"device": "/dev/sdb1", "type": "ext4", "options": ["rw"]}
    }


async def test_mounts_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(
            name="web1",
            mounts={"/": {"device": "/dev/sdb1", "type": "ext4", "options": ["rw"]}},
        ),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, Mounts) == {
        "web1": {"/": {"device": "/dev/sdb1", "type": "ext4", "options": ["rw"]}},
        "web2": {},
    }


# --- Port ----------------------------------------------------------------------

# The not-listening answer: what every tool reports when its port filter
# matches no row, and what an unseeded ``listening_ports`` seam reads as.
NOT_LISTENING = PortInfo(listening=False, process=None, pid=None)


def _listening(process: str | None, pid: int | None) -> PortInfo:
    """A listening record, with (or without, as ``None``) an owner."""
    return PortInfo(listening=True, process=process, pid=pid)


# The baseline's own fact fixtures (tests/facts/server.Port/*.json), captured
# verbatim: the ``command -v ss`` answer its ``facts`` block seeds, the port
# and protocol arguments, the command string the fixture records, its raw
# output lines and the record reemote derives. The last column is where
# reemote deliberately departs from the baseline's ``(process, pid)`` tuple:
# the ``ss_no_process_info`` rows are a listening port with no owner to name,
# where the baseline answers ``(None, None)`` and its ``server.wait`` would
# keep looping.
PORT_FIXTURES: list[tuple[str, str, int, str, str, list[str], PortInfo]] = [
    ("ss_empty", "/usr/sbin/ss", 53, "tcp", "ss -lptn | grep ':53 ' || true", [], NOT_LISTENING),
    (
        "ss_one_process",
        "/usr/sbin/ss",
        10025,
        "tcp",
        "ss -lptn | grep ':10025 ' || true",
        [
            'LISTEN 0      100        127.0.0.1:10025      0.0.0.0:*    users:(("smtpd",pid=3927036,fd=6),("master",pid=1997758,fd=97))'
        ],
        _listening("smtpd", 3927036),
    ),
    (
        "ss_two_processes",
        "/usr/sbin/ss",
        443,
        "tcp",
        "ss -lptn | grep ':443 ' || true",
        [
            'LISTEN 0      511          0.0.0.0:443       0.0.0.0:*    users:(("nginx",pid=3759498,fd=3),("nginx",pid=3759496,fd=3),("nginx",pid=2274336,fd=3))',
            'LISTEN 0      511             [::]:443          [::]:*    users:(("nginx",pid=3759498,fd=4),("nginx",pid=3759496,fd=4),("nginx",pid=2274336,fd=4))',
        ],
        _listening("nginx", 3759498),
    ),
    (
        "ss_no_process_info",
        "/usr/sbin/ss",
        22,
        "tcp",
        "ss -lptn | grep ':22 ' || true",
        [
            "LISTEN 0      128          0.0.0.0:22        0.0.0.0:*",
            "LISTEN 0      128             [::]:22           [::]:*",
        ],
        _listening(None, None),
    ),
    ("ss_udp_empty", "/usr/sbin/ss", 53, "udp", "ss -lpun | grep ':53 ' || true", [], NOT_LISTENING),
    (
        "ss_udp_one_process",
        "/usr/sbin/ss",
        53,
        "udp",
        "ss -lpun | grep ':53 ' || true",
        ['UNCONN 0      0          0.0.0.0:53        0.0.0.0:*    users:(("named",pid=1234,fd=512))'],
        _listening("named", 1234),
    ),
    (
        "netstat_tcp_empty",
        "",
        80,
        "tcp",
        "netstat -tlnp 2>/dev/null | awk '$4 ~ /:80$/'",
        [],
        NOT_LISTENING,
    ),
    (
        "netstat_tcp_one_process",
        "",
        80,
        "tcp",
        "netstat -tlnp 2>/dev/null | awk '$4 ~ /:80$/'",
        ["tcp        0      0 0.0.0.0:80              0.0.0.0:*               LISTEN      1234/nginx"],
        _listening("nginx", 1234),
    ),
    (
        "netstat_tcp_truncated_name",
        "",
        22,
        "tcp",
        "netstat -tlnp 2>/dev/null | awk '$4 ~ /:22$/'",
        ["tcp        0      0 0.0.0.0:22              0.0.0.0:*               LISTEN      2316/sshd [listener"],
        _listening("sshd", 2316),
    ),
    (
        "netstat_udp_one_process",
        "",
        53,
        "udp",
        "netstat -ulnp 2>/dev/null | awk '$4 ~ /:53$/'",
        ["udp        0      0 0.0.0.0:53              0.0.0.0:*                           1234/named"],
        _listening("named", 1234),
    ),
]


@pytest.mark.parametrize(
    ("name", "ss", "port", "protocol", "command", "output", "expected"),
    PORT_FIXTURES,
    ids=[row[0] for row in PORT_FIXTURES],
)
async def test_port_matches_baseline_fixtures(
    name, ss, port, protocol, command, output, expected
):
    host = CannedPortHost(ss, "\n".join(output))
    assert await Port.process(host, port, protocol) == expected
    # The ss probe first, then the listing that answer selects -- the
    # fixture's own command string.
    assert host.shells == ["command -v ss || true", command]


def test_port_tool_prefers_ss_then_netstat():
    assert Port._tool(has_ss=True) == "ss"
    # No ``ss`` on the box falls back to netstat, like the baseline.
    assert Port._tool(has_ss=False) == "netstat"


def test_port_parse_owner_is_optional_but_the_row_still_listens():
    # The deviation from the baseline in three lines: an unprivileged read sees
    # the socket without its owner, and that is a listening port.
    assert Port.parse(["LISTEN 0 128 0.0.0.0:22 0.0.0.0:*"], "ss") == _listening(None, None)
    assert Port.parse(["tcp 0 0 0.0.0.0:22 0.0.0.0:* LISTEN -"], "netstat") == _listening(
        None, None
    )
    assert Port.parse(["udp 0 0 0.0.0.0:53 0.0.0.0:* ?"], "netstat") == _listening(None, None)


def test_port_parse_survives_a_malformed_owner_cell():
    # A PID column that is not a number reports the name without a number
    # (ss) or no owner at all (netstat) -- never an exception.
    assert Port.parse(['LISTEN users:(("nginx",pid=oops,fd=3))'], "ss") == _listening(
        "nginx", None
    )
    assert Port.parse(["www nginx notapid 3 tcp4 *:80 *:*"], "ss") == _listening(
        None, None
    )
    # netstat's unprivileged "-/nginx" cell has a separator but no PID, and
    # the scan stops at the first slashed cell (the baseline's `break`).
    assert Port.parse(["tcp 0 0 0.0.0.0:80 0.0.0.0:* LISTEN -/nginx"], "netstat") == (
        _listening(None, None)
    )
    # A short ss row carries no owner columns to read.
    assert Port.parse(["www nginx"], "ss") == _listening(None, None)


async def test_port_is_not_listening_on_empty_output():
    # A failed or unmatched read maps to "nothing listening" on every branch
    # (the baseline's ``(None, None)`` default).
    assert await Port.process(CannedPortHost("/usr/sbin/ss", ""), 80) == NOT_LISTENING
    assert await Port.process(CannedPortHost("", ""), 80) == NOT_LISTENING


async def test_port_rejects_a_bad_port_or_protocol():
    # Both values are interpolated into a grep pattern or awk program, so a
    # bad one is rejected identically on both transports and from command().
    for host in (CannedPortHost("/usr/sbin/ss", ""), SimulatedHost(name="h")):
        with pytest.raises(TypeError, match="port must be an int"):
            await Port.process(host, "80 || 1")
    with pytest.raises(ValueError, match="protocol must be one of"):
        await Port.process(SimulatedHost(name="h"), 80, "sctp")
    with pytest.raises(ValueError, match="port must be between 1 and 65535"):
        Port.command("ss", 70000)
    with pytest.raises(ValueError, match="protocol must be one of"):
        Port.command("ss", 80, "sctp")


async def test_port_reads_the_simulated_seam():
    record = _listening("nginx", 1234)
    host = SimulatedHost(name="h", listening_ports={("tcp", 80): record})
    assert await Port.process(host, 80) == record
    # The other protocol and an unlisted port read as nothing listening.
    assert await Port.process(host, 80, "udp") == NOT_LISTENING
    assert await Port.process(host, 443) == NOT_LISTENING
    # An owner-less entry is still a listening port (the unprivileged answer).
    host = SimulatedHost(name="h", listening_ports={("tcp", 22): _listening(None, None)})
    assert await Port.process(host, 22) == _listening(None, None)
    # The seam defaults to {} -- a simulated machine with no sockets listed.
    assert await Port.process(SimulatedHost(name="h"), 80) == NOT_LISTENING


async def test_port_seam_returns_a_copy():
    host = SimulatedHost(name="h", listening_ports={("tcp", 80): _listening("nginx", 1234)})
    info = await Port.process(host, 80)
    info["listening"] = False
    info["process"] = "mutated"
    assert host.listening_ports == {("tcp", 80): _listening("nginx", 1234)}


async def test_port_gather_fact_across_hosts():
    record = _listening("nginx", 1234)
    hosts = [
        SimulatedHost(name="web1", listening_ports={("tcp", 80): record}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, Port, 80) == {"web1": record, "web2": NOT_LISTENING}
    # The protocol argument is forwarded to process verbatim.
    assert await gather_fact(hosts, Port, 80, protocol="udp") == {
        "web1": NOT_LISTENING,
        "web2": NOT_LISTENING,
    }


# --- Processes -----------------------------------------------------------------


async def test_processes_parses_baseline_linux_all_fixture():
    # The baseline's own fact fixture (tests/facts/server.Processes/linux_all.json):
    # a non-BusyBox Linux host selects the --no-headers listing.
    host = CannedProcessesHost(
        "",
        """    1 root     Ss    0.0  0.1 init            /sbin/init
  200 www-data S     1.2  3.4 nginx           nginx: worker process
 1500 postgres Ss    0.5  2.0 postgres        /usr/lib/postgresql/14/bin/postgres -D /var/lib/postgresql/14/main
""",
    )
    result = await Processes.process(host)
    assert result == {
        1: {
            "user": "root",
            "state": "Ss",
            "cpu_percent": 0.0,
            "mem_percent": 0.1,
            "command": "init",
            "args": "/sbin/init",
        },
        200: {
            "user": "www-data",
            "state": "S",
            "cpu_percent": 1.2,
            "mem_percent": 3.4,
            "command": "nginx",
            "args": "nginx: worker process",
        },
        1500: {
            "user": "postgres",
            "state": "Ss",
            "cpu_percent": 0.5,
            "mem_percent": 2.0,
            "command": "postgres",
            "args": "/usr/lib/postgresql/14/bin/postgres -D /var/lib/postgresql/14/main",
        },
    }
    # The PID 1 record is the baseline's documented example result, verbatim.
    assert result[1] == {
        "user": "root",
        "state": "Ss",
        "cpu_percent": 0.0,
        "mem_percent": 0.1,
        "command": "init",
        "args": "/sbin/init",
    }
    # BusyBox probe (empty answer), then the Linux listing.
    assert host.shells == [
        "command -v busybox || true",
        "LANG=C ps -eo pid,user,stat,%cpu,%mem,comm,args --no-headers",
    ]


async def test_processes_parses_baseline_linux_single_pid_fixture():
    # linux_single_pid.json: ps -p keeps the header row, which is skipped.
    host = CannedProcessesHost(
        "",
        """  PID USER     STAT %CPU %MEM COMMAND         COMMAND
    1 root     Ss    0.0  0.1 systemd         /lib/systemd/systemd --system
""",
    )
    assert await Processes.process(host, pid=1) == {
        1: {
            "user": "root",
            "state": "Ss",
            "cpu_percent": 0.0,
            "mem_percent": 0.1,
            "command": "systemd",
            "args": "/lib/systemd/systemd --system",
        },
    }
    assert host.shells[-1] == "LANG=C ps -p 1 -o pid,user,stat,%cpu,%mem,comm,args"


async def test_processes_parses_baseline_busybox_all_fixture():
    # busybox_all.json: the probe answers a path, selecting the five-column
    # listing; percents are always 0.0 and command is args' basename.
    host = CannedProcessesHost(
        "/bin/busybox\n",
        """  PID USER       VSZ STAT ARGS
    1 root      2108 S    /sbin/init
  200 root      1536 S    /usr/sbin/sshd
  310 root      1488 S    /usr/sbin/crond -c /etc/crontabs
""",
    )
    assert await Processes.process(host) == {
        1: {
            "user": "root",
            "state": "S",
            "cpu_percent": 0.0,
            "mem_percent": 0.0,
            "command": "init",
            "args": "/sbin/init",
        },
        200: {
            "user": "root",
            "state": "S",
            "cpu_percent": 0.0,
            "mem_percent": 0.0,
            "command": "sshd",
            "args": "/usr/sbin/sshd",
        },
        310: {
            "user": "root",
            "state": "S",
            "cpu_percent": 0.0,
            "mem_percent": 0.0,
            "command": "crond",
            "args": "/usr/sbin/crond -c /etc/crontabs",
        },
    }
    assert host.shells == [
        "command -v busybox || true",
        "LANG=C ps -o pid,user,vsz,stat,args",
    ]


async def test_processes_parses_baseline_busybox_single_pid_fixture():
    # busybox_single_pid.json: BusyBox ps has no -p, so awk filters the PID.
    host = CannedProcessesHost(
        "/bin/busybox\n",
        """  PID USER       VSZ STAT ARGS
    1 root      2108 S    /sbin/init
""",
    )
    assert await Processes.process(host, pid=1) == {
        1: {
            "user": "root",
            "state": "S",
            "cpu_percent": 0.0,
            "mem_percent": 0.0,
            "command": "init",
            "args": "/sbin/init",
        },
    }
    assert host.shells[-1] == (
        "LANG=C ps -o pid,user,vsz,stat,args | awk 'NR==1 || $1==1'"
    )


def test_processes_command_matches_baseline_fixture_commands():
    # Every command string is the "command" field of the matching the baseline
    # fixture, verbatim.
    assert Processes.command() == (
        "LANG=C ps -eo pid,user,stat,%cpu,%mem,comm,args --no-headers"
    )
    assert Processes.command(pid=1) == (
        "LANG=C ps -p 1 -o pid,user,stat,%cpu,%mem,comm,args"
    )
    assert Processes.command(busybox=True) == "LANG=C ps -o pid,user,vsz,stat,args"
    assert Processes.command(busybox=True, pid=1) == (
        "LANG=C ps -o pid,user,vsz,stat,args | awk 'NR==1 || $1==1'"
    )
    assert Processes.command() == Processes.COMMAND_LINUX
    assert Processes.command(busybox=True) == Processes.COMMAND_BUSYBOX


def test_processes_parse_skips_headers_and_malformed_rows():
    rows = [
        "  PID USER     STAT %CPU %MEM COMMAND         COMMAND",
        "",
        "short row",
        "not-a-pid root Ss 0.0 0.1 init /sbin/init",
        "    1 root     Ss    0.0  0.1 init            /sbin/init",
    ]
    assert Processes.parse(rows) == {
        1: {
            "user": "root",
            "state": "Ss",
            "cpu_percent": 0.0,
            "mem_percent": 0.1,
            "command": "init",
            "args": "/sbin/init",
        },
    }
    # Empty output reads as {} on every branch (the baseline's default = dict).
    assert Processes.parse([]) == {}
    assert Processes.parse([], busybox=True) == {}


def test_processes_parse_args_falls_back_to_comm():
    # A row with no separate args column reuses comm (the baseline's fallback).
    result = Processes.parse(["1 root Ss 0.0 0.1 init"])
    assert result[1]["command"] == "init"
    assert result[1]["args"] == "init"


def test_processes_parse_busybox_edge_cases():
    rows = [
        "  PID USER       VSZ STAT ARGS",
        "",
        "short row",
        "not-a-pid root 2108 S /sbin/init",
        "  310 root      1488 S    /usr/sbin/crond -c /etc/crontabs",
    ]
    assert Processes.parse(rows, busybox=True) == {
        310: {
            "user": "root",
            "state": "S",
            "cpu_percent": 0.0,
            "mem_percent": 0.0,
            "command": "crond",
            "args": "/usr/sbin/crond -c /etc/crontabs",
        },
    }


async def test_processes_is_empty_on_empty_output():
    # A failed ps read maps to {} (the baseline's default = dict) on both the
    # plain Linux listing and the BusyBox listing.
    assert await Processes.process(CannedProcessesHost("", "")) == {}
    assert await Processes.process(CannedProcessesHost("/bin/busybox\n", "")) == {}


async def test_processes_rejects_a_non_int_pid():
    # The BusyBox branch interpolates the pid into an awk program, so a
    # non-int pid is rejected identically on both transports.
    for host in (CannedProcessesHost("", ""), SimulatedHost(name="h")):
        with pytest.raises(TypeError, match="pid must be an int"):
            await Processes.process(host, pid="1")
    with pytest.raises(TypeError, match="pid must be an int"):
        Processes.command(busybox=True, pid="1 || 1")


async def test_processes_reads_the_simulated_seam():
    record = {
        "user": "root",
        "state": "Ss",
        "cpu_percent": 0.0,
        "mem_percent": 0.1,
        "command": "init",
        "args": "/sbin/init",
    }
    host = SimulatedHost(name="h", processes={1: record})
    assert await Processes.process(host) == {1: record}
    # A pid filter selects the single record; an absent PID reads as {}.
    assert await Processes.process(host, pid=1) == {1: record}
    assert await Processes.process(host, pid=999) == {}
    # The seam defaults to {} -- a simulated machine lists no processes.
    assert await Processes.process(SimulatedHost(name="h")) == {}


async def test_processes_seam_returns_a_copy():
    host = SimulatedHost(
        name="h",
        processes={
            1: {
                "user": "root",
                "state": "Ss",
                "cpu_percent": 0.0,
                "mem_percent": 0.1,
                "command": "init",
                "args": "/sbin/init",
            },
        },
    )
    result = await Processes.process(host)
    result[1]["args"] = "mutated"
    result[2] = {}
    single = await Processes.process(host, pid=1)
    single[1]["user"] = "nobody"
    assert host.processes == {
        1: {
            "user": "root",
            "state": "Ss",
            "cpu_percent": 0.0,
            "mem_percent": 0.1,
            "command": "init",
            "args": "/sbin/init",
        },
    }


async def test_processes_gather_fact_across_hosts():
    record = {
        "user": "root",
        "state": "Ss",
        "cpu_percent": 0.0,
        "mem_percent": 0.1,
        "command": "init",
        "args": "/sbin/init",
    }
    hosts = [
        SimulatedHost(name="web1", processes={1: record}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, Processes) == {"web1": {1: record}, "web2": {}}
    # The pid argument is forwarded to process verbatim.
    assert await gather_fact(hosts, Processes, pid=1) == {
        "web1": {1: record},
        "web2": {},
    }


# The Sysctl fact's tests were relocated to tests/modules/sysctl/test_sysctl.py
# alongside the class itself (the sysctl namespace is the canonical home for
# everything sysctl; server no longer exports the fact).


# --- Timezone ----------------------------------------------------------------


async def test_timezone_parses_baseline_fixture():
    # The baseline's own fact fixture (tests/facts/server.Timezone/timezone.json):
    # command "readlink -f /etc/localtime | sed 's|.*/zoneinfo/||'",
    # output ["Europe/Amsterdam"] -> fact "Europe/Amsterdam".
    host = CannedShellHost("Europe/Amsterdam\n")
    assert await Timezone.process(host) == "Europe/Amsterdam"
    assert host.shells == ["readlink -f /etc/localtime | sed 's|.*/zoneinfo/||'"]


def test_timezone_command_matches_baseline():
    assert Timezone.COMMAND == "readlink -f /etc/localtime | sed 's|.*/zoneinfo/||'"


def test_timezone_parse_takes_the_first_non_blank_line():
    assert Timezone.parse(["UTC"]) == "UTC"
    assert Timezone.parse(["", "  Europe/Amsterdam  ", "ignored"]) == "Europe/Amsterdam"
    assert Timezone.parse([]) == ""


def test_timezone_parse_keeps_a_non_zoneinfo_path_verbatim():
    # /etc/localtime copied instead of symlinked: the sed matches nothing
    # and the resolved path comes back as is, exactly like the baseline.
    assert Timezone.parse(["/etc/localtime"]) == "/etc/localtime"


async def test_timezone_is_empty_on_empty_output():
    assert await Timezone.process(CannedShellHost("")) == ""


async def test_timezone_reads_the_simulated_seam():
    host = SimulatedHost(name="h", os_timezone="Australia/Sydney")
    assert await Timezone.process(host) == "Australia/Sydney"
    # The seam defaults to the baseline's fixture timezone: a simulated machine
    # always resolves /etc/localtime, deterministically.
    assert await Timezone.process(SimulatedHost(name="h")) == "Europe/Amsterdam"


async def test_timezone_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", os_timezone="Europe/London"),
        SimulatedHost(name="web2", os_timezone="America/New_York"),
    ]
    assert await gather_fact(hosts, Timezone) == {
        "web1": "Europe/London",
        "web2": "America/New_York",
    }


# --- Uptime --------------------------------------------------------------------


async def test_uptime_parses_baseline_linux_fixture():
    # The baseline's own fact fixture (tests/facts/server.Uptime/uptime_linux.json):
    # command "cut -d. -f1 /proc/uptime", output ["86400"] -> fact 86400.
    host = CannedShellHost("86400\n")
    assert await Uptime.process(host) == 86400
    assert host.shells == ["cut -d. -f1 /proc/uptime"]


def test_uptime_command_is_the_proc_uptime_read():
    assert Uptime.COMMAND == "cut -d. -f1 /proc/uptime"


def test_uptime_parse_edge_cases():
    # Blank lines are skipped and the answer is the first integer; anything
    # after it is ignored.
    assert Uptime.parse(["", "  3600  ", "ignored"]) == 3600


def test_uptime_parse_rejects_bad_rows_like_baseline():
    # A non-integer line raises ValueError -- the baseline's parser does no
    # guard, so int() surfaces the failure.
    with pytest.raises(ValueError):
        Uptime.parse(["oops"])


async def test_uptime_is_none_on_empty_output():
    # A failed read maps to None (the baseline's FactBase default), never to a
    # freshly booted host's 0.
    assert await Uptime.process(CannedShellHost("")) is None


async def test_uptime_reads_the_simulated_seam():
    host = SimulatedHost(name="h", uptime_seconds=123)
    assert await Uptime.process(host) == 123
    # The seam defaults to the baseline's fixture uptime: a simulated machine
    # always answers with an uptime, deterministically.
    assert await Uptime.process(SimulatedHost(name="h")) == 86400


async def test_uptime_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", uptime_seconds=60),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, Uptime) == {"web1": 60, "web2": 86400}


# --- User ----------------------------------------------------------------------


async def test_user_parses_echo_user():
    # The baseline ships no fixture for server.User; a plain username is echoed
    # verbatim and returned as the raw string (the baseline's FactBase default
    # joins the single output line).
    host = CannedShellHost("kjarvis\n")
    assert await User.process(host) == "kjarvis"
    assert host.shells == ["echo $USER"]


def test_user_parse_takes_the_first_non_blank_line():
    assert User.parse(["root"]) == "root"
    assert User.parse(["", "  kjarvis  ", "ignored"]) == "kjarvis"
    assert User.parse([]) == ""


async def test_user_is_empty_on_empty_output():
    # A failed command, or an unset/empty USER (echo prints a blank line),
    # maps to "" like Env.
    assert await User.process(CannedShellHost("")) == ""
    assert await User.process(CannedShellHost("\n")) == ""


async def test_user_reads_the_simulated_seam():
    host = SimulatedHost(name="h", os_user="alice")
    assert await User.process(host) == "alice"
    # The seam defaults to root: a simulated machine always answers
    # echo $USER.
    assert await User.process(SimulatedHost(name="h")) == "root"


async def test_user_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", os_user="alice"),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, User) == {"web1": "alice", "web2": "root"}


# --- Users ---------------------------------------------------------------------

# The baseline's own fact fixture (tests/facts/server.Users/mixed.json), reused
# verbatim: passwd entries joined with id/lastlog/shadow answers, covering
# secondary groups, empty passwd fields, "Never logged in" rows and the
# BSD lastlogin timestamp layout.
USERS_MIXED_OUTPUT = """\
root:x:0:0:root:/root:/bin/ks|wheel|wheel kmem sys tty operator staff guest|root pts/0 Sat Jun 5 12:03:23 -0600 2021|$rootpw$
_tesTy.test:x:1004:1004::/home/_tesTy.test:/bin/ksh|_tesTy.test|_tesTy.test reemote|_tesTy.test pts/0 host-ip Sat Jun 12 13:43:42 -0600 2021|$testpw$
test.testy:x:1003:1003:Testy,,,:/home/test.testy:/bin/ksh|test.testy||test.testy Fri Jun 11 22:26:04 -0600 2021|$testpw$
noshell:x:1002:1002:noshell comment with spaces:/home/noshell:|noshell||noshell **Never logged in**|
nohome:x:1002:1002:nohome comment::/bin/bash|nohome||**Never logged in**|
freebsd:*:1001:1001:FreeBSD:/home/freebsd:/bin/sh|freebsd|freebsd|freebsd pts/0 10.1.10.10 Thu Aug 31 05:37:58 2023|$bsdpw$
freebsdnologin:*:1001:1001:FreeBSD NoLogin:/home/freebsdnologin:/bin/sh|freebsdnologin|freebsdnologin||
"""

# The fixture's expected fact, with the JSON login_time strings read as
# datetimes (the baseline's parser returns datetime objects; its test harness
# serializes them to ISO strings).
USERS_MIXED_FACT = {
    "root": {
        "home": "/root",
        "comment": "root",
        "shell": "/bin/ks",
        "group": "wheel",
        "groups": ["kmem", "sys", "tty", "operator", "staff", "guest"],
        "uid": 0,
        "gid": 0,
        "lastlog": "Sat Jun 5 12:03:23 -0600 2021",
        "login_time": datetime.fromisoformat("2021-06-05T12:03:23-06:00"),
        "password": "$rootpw$",
    },
    "_tesTy.test": {
        "home": "/home/_tesTy.test",
        "comment": None,
        "shell": "/bin/ksh",
        "group": "_tesTy.test",
        "groups": ["reemote"],
        "uid": 1004,
        "gid": 1004,
        "lastlog": "Sat Jun 12 13:43:42 -0600 2021",
        "login_time": datetime.fromisoformat("2021-06-12T13:43:42-06:00"),
        "password": "$testpw$",
    },
    "test.testy": {
        "home": "/home/test.testy",
        "comment": "Testy,,,",
        "shell": "/bin/ksh",
        "group": "test.testy",
        "groups": [],
        "uid": 1003,
        "gid": 1003,
        "lastlog": "Fri Jun 11 22:26:04 -0600 2021",
        "login_time": datetime.fromisoformat("2021-06-11T22:26:04-06:00"),
        "password": "$testpw$",
    },
    "noshell": {
        "home": "/home/noshell",
        "comment": "noshell comment with spaces",
        "shell": None,
        "group": "noshell",
        "groups": [],
        "uid": 1002,
        "gid": 1002,
        "lastlog": None,
        "login_time": None,
        "password": "",
    },
    "nohome": {
        "home": None,
        "comment": "nohome comment",
        "shell": "/bin/bash",
        "group": "nohome",
        "groups": [],
        "uid": 1002,
        "gid": 1002,
        "lastlog": None,
        "login_time": None,
        "password": "",
    },
    "freebsd": {
        "home": "/home/freebsd",
        "comment": "FreeBSD",
        "shell": "/bin/sh",
        "group": "freebsd",
        "groups": [],
        "uid": 1001,
        "gid": 1001,
        "lastlog": "Thu Aug 31 05:37:58 2023",
        "login_time": datetime.fromisoformat("2023-08-31T05:37:58"),
        "password": "$bsdpw$",
    },
    "freebsdnologin": {
        "home": "/home/freebsdnologin",
        "comment": "FreeBSD NoLogin",
        "shell": "/bin/sh",
        "group": "freebsdnologin",
        "groups": [],
        "uid": 1001,
        "gid": 1001,
        "lastlog": None,
        "login_time": None,
        "password": "",
    },
}


async def test_users_parses_baseline_mixed_fixture():
    host = CannedShellHost(USERS_MIXED_OUTPUT)
    assert await Users.process(host) == USERS_MIXED_FACT
    assert host.shells == [Users.COMMAND]


def test_users_command_matches_baseline():
    # The command field of the baseline's mixed.json fixture, verbatim.
    assert Users.COMMAND == """for i in `cat /etc/passwd | cut -d: -f1`; do
            ENTRY=`grep ^$i: /etc/passwd`;
            LASTLOG=`(((lastlog -u $i || lastlogin $i) 2> /dev/null) | grep ^$i | tr -s ' ')`;
            PASSWORD=`(grep ^$i: /etc/shadow || grep ^$i: /etc/master.passwd) 2> /dev/null | cut -d: -f2`;
            echo "$ENTRY|`id -gn $i`|`id -Gn $i`|$LASTLOG|$PASSWORD";
        done"""


def test_users_parse_skips_blank_lines_and_empty_entries():
    # A row whose passwd entry is empty (the grep failed) is skipped, like
    # The baseline's ``if entry:`` guard; blank lines never reach the splitter.
    assert Users.parse(["", "   ", "|nobody|nobody||", "\t"]) == {}


def test_users_parse_rejects_malformed_rows():
    # A row without the four ``|`` separators cannot be unpacked, exactly
    # like the baseline's rsplit.
    with pytest.raises(ValueError, match="not enough values to unpack"):
        Users.parse(["root:x:0:0:root:/root:/bin/bash"])


def test_users_parse_unmatched_login_layout_stays_none():
    # A timestamp the regex matches but neither strptime layout knows
    # (a named zone) keeps the raw lastlog and leaves login_time None --
    # the documented deviation from the baseline's fuzzy dateutil parse.
    row = "root:x:0:0:root:/root:/bin/bash|root|root|root pts/0 Sat Jun 5 12:03:23 EST 2021|$rootpw$"
    (info,) = Users.parse([row]).values()
    assert info["lastlog"] == "Sat Jun 5 12:03:23 EST 2021"
    assert info["login_time"] is None


async def test_users_is_empty_on_empty_output():
    # A failed loop reads as {} -- the baseline's ``default = dict``.
    assert await Users.process(CannedShellHost("")) == {}
    assert await Users.process(CannedShellHost("\n")) == {}


async def test_users_reads_the_simulated_seam():
    host = SimulatedHost(name="h", os_users={"alice": USERS_MIXED_FACT["_tesTy.test"]})
    assert await Users.process(host) == {"alice": USERS_MIXED_FACT["_tesTy.test"]}
    # The seam defaults to {}: a simulated machine lists no users until a
    # test sets one.
    assert await Users.process(SimulatedHost(name="h")) == {}


async def test_users_seam_returns_a_copy():
    host = SimulatedHost(name="h", os_users={"root": USERS_MIXED_FACT["root"]})
    users = await Users.process(host)
    users["root"]["groups"].append("intruder")
    users["root"]["shell"] = "/bin/zsh"
    users["mallory"] = {}
    assert host.os_users == {"root": USERS_MIXED_FACT["root"]}
    assert "intruder" not in host.os_users["root"]["groups"]


async def test_users_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", os_users={"root": USERS_MIXED_FACT["root"]}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, Users) == {
        "web1": {"root": USERS_MIXED_FACT["root"]},
        "web2": {},
    }


# --- Which ---------------------------------------------------------------------


async def test_which_parses_baseline_which_fixture():
    # The baseline's own fact fixture (tests/facts/server.Which/which.json):
    # arg "python", command "command -v python || true",
    # output ["/usr/local/bin/python"] -> fact "/usr/local/bin/python".
    host = CannedShellHost("/usr/local/bin/python\n")
    assert await Which.process(host, "python") == "/usr/local/bin/python"
    assert host.shells == ["command -v python || true"]


async def test_which_quotes_baseline_injection_fixture():
    # The baseline's quotes_injection.json fixture: arg "py; reboot", command
    # "command -v 'py; reboot' || true", output [] -- the quoted name is
    # looked up literally, never executed, and an unavailable command reads
    # as None (the baseline's runtime default; its raw parser joins lines to "").
    host = CannedShellHost("")
    assert await Which.process(host, "py; reboot") is None
    assert host.shells == ["command -v 'py; reboot' || true"]


def test_which_command_quotes_unsafe_names():
    assert Which.command("python") == "command -v python || true"
    assert Which.command("py; reboot") == "command -v 'py; reboot' || true"
    assert Which.command("$(whoami)") == "command -v '$(whoami)' || true"


def test_which_parse_takes_the_first_non_blank_line():
    assert Which.parse(["/usr/bin/python"]) == "/usr/bin/python"
    assert Which.parse(["", "  /usr/bin/python  ", "ignored"]) == "/usr/bin/python"
    assert Which.parse([]) is None


def test_which_parse_keeps_a_builtin_name():
    # `command -v` answers a shell builtin or alias with its own name, not
    # a path; the string is returned verbatim, like the baseline.
    assert Which.parse(["cd"]) == "cd"


async def test_which_is_none_on_empty_output():
    # The `|| true` swallows the non-zero exit of an unknown command, so no
    # output means unavailable -- None, the baseline's FactBase default.
    assert await Which.process(CannedShellHost(""), "nosuchcommand") is None
    assert await Which.process(CannedShellHost("\n"), "nosuchcommand") is None


async def test_which_reads_the_simulated_seam():
    host = SimulatedHost(name="h", command_paths={"python": "/usr/bin/python"})
    assert await Which.process(host, "python") == "/usr/bin/python"
    # An unknown command reads as None, and the seam is never shelled out to.
    assert await Which.process(host, "nosuchcommand") is None
    # The seam defaults to {}: a simulated machine resolves no commands
    # until a test names them.
    assert await Which.process(SimulatedHost(name="h"), "python") is None


async def test_which_gather_fact_forwards_the_command():
    hosts = [
        SimulatedHost(name="web1", command_paths={"python3": "/usr/bin/python3"}),
        SimulatedHost(name="web2", command_paths={"python3": "/usr/local/bin/python3"}),
    ]
    assert await gather_fact(hosts, Which, "python3") == {
        "web1": "/usr/bin/python3",
        "web2": "/usr/local/bin/python3",
    }


# --- server.group ------------------------------------------------------------


def test_group_add_command_shell_rendering():
    assert GroupAddCommand(group="docker").shell() == "groupadd docker"
    assert GroupAddCommand(group="docker", system=True).shell() == "groupadd -r docker"
    assert (
        GroupAddCommand(group="somegroup", system=True, gid=1000).shell()
        == "groupadd -r somegroup --gid 1000"
    )
    assert (
        GroupAddCommand(group="my group; whoami").shell()
        == "groupadd 'my group; whoami'"
    )
    assert command_shell(GroupAddCommand(group="docker"), sudo=True) == (
        "sudo groupadd docker"
    )


def test_group_del_command_shell_rendering():
    assert GroupDelCommand(group="docker").shell() == "groupdel docker"
    assert (
        GroupDelCommand(group="my group; whoami").shell()
        == "groupdel 'my group; whoami'"
    )
    assert command_shell(GroupDelCommand(group="docker"), sudo=True) == (
        "sudo groupdel docker"
    )


@pytest.mark.parametrize(
    ("group", "groups", "kwargs", "expected"),
    [
        # The baseline's own Linux operation fixtures
        # (tests/operations/server.group/): add.json, add_needs_quotes.json,
        # delete.json, delete_needs_quotes.json.
        ("somegroup", [], {"gid": 1000, "system": True},
         ["groupadd -r somegroup --gid 1000"]),
        ("my group; whoami", [], {},
         ["groupadd 'my group; whoami'"]),
        ("somegroup", ["somegroup"], {"present": False},
         ["groupdel somegroup"]),
        ("my group; whoami", ["my group; whoami"], {"present": False},
         ["groupdel 'my group; whoami'"]),
    ],
)
async def test_group_matches_baseline_operation_fixtures(
    group: str, groups: list[str], kwargs: dict, expected: list[str]
):
    host = SimulatedHost(name="h", os_groups=groups)
    ctx, commands = await materialize(host, op(server.group, group=group, **kwargs))
    assert ctx.changed
    assert [cmd.shell() for cmd in commands] == expected


async def test_group_creates_an_absent_group():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(host, op(server.group, group="docker"))
    assert ctx.changed
    assert ctx.reason == "group docker absent, will create"
    assert commands == [GroupAddCommand(group="docker")]


async def test_group_removes_a_present_group():
    host = SimulatedHost(name="h", os_groups=["docker"])
    ctx, commands = await materialize(
        host, op(server.group, group="docker", present=False)
    )
    assert ctx.changed
    assert ctx.reason == "group docker present, will remove"
    assert commands == [GroupDelCommand(group="docker")]


async def test_group_no_change_when_already_present():
    host = SimulatedHost(name="h", os_groups=["docker"])
    ctx, commands = await materialize(host, op(server.group, group="docker"))
    assert not ctx.changed
    assert ctx.reason == "group docker already present"
    assert commands == []


async def test_group_no_change_when_already_absent():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, op(server.group, group="docker", present=False)
    )
    assert not ctx.changed
    assert ctx.reason == "group docker already absent"
    assert commands == []


async def test_group_gid_accepts_a_string_and_zero_is_skipped():
    host = SimulatedHost(name="h")
    _, commands = await materialize(host, op(server.group, group="docker", gid="1500"))
    assert commands == [GroupAddCommand(group="docker", gid="1500")]
    assert commands[0].shell() == "groupadd docker --gid 1500"
    # The baseline's `if gid:` truthiness check: a gid of 0 renders no flag.
    _, commands = await materialize(host, op(server.group, group="docker", gid=0))
    assert commands[0].shell() == "groupadd docker"


async def test_group_existing_group_is_never_modified():
    # system/gid only apply at creation; a present group plans no command
    # even when the request's gid or system flag differs (baseline parity).
    host = SimulatedHost(name="h", os_groups=["docker"])
    ctx, commands = await materialize(
        host, op(server.group, group="docker", system=True, gid=1500)
    )
    assert not ctx.changed
    assert commands == []


async def test_group_absorbs_unknown_keyword_arguments():
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize(
        host, op(server.group, group="docker", _extra_global=True)
    )
    assert commands == [GroupAddCommand(group="docker")]


async def test_group_commands_mutate_the_simulated_host():
    host = SimulatedHost(name="h", os_groups=["wheel"])
    await send_command(host, GroupAddCommand(group="docker"))
    assert host.os_groups == ["wheel", "docker"]
    # A repeat add leaves the list alone (the operation's fact check would
    # not yield it, but the seam stays safe regardless).
    await send_command(host, GroupAddCommand(group="docker"))
    assert host.os_groups == ["wheel", "docker"]
    await send_command(host, GroupDelCommand(group="docker"))
    assert host.os_groups == ["wheel"]


def ensure_groups(host: Host) -> Generator[OperationRequest]:
    """The example deploy: create the docker group, then a system group."""
    yield op(server.group, name="Create docker group", group="docker", sudo=True)
    yield op(
        server.group,
        name="Create lusers system group",
        group="lusers",
        system=True,
        gid=1500,
        sudo=True,
    )


def wheel_only_factory(entry: dict) -> Host:
    """``host_factory`` building SimulatedHosts whose only group is wheel."""
    host = SimulatedHost.from_inventory(entry)
    host.os_groups = ["wheel"]  # fresh list per host
    return host


async def test_group_runs_across_hosts_in_lockstep():
    plans, report, hosts = await run(
        INVENTORY, ensure_groups, host_factory=wheel_only_factory
    )
    assert format_plan(plans).splitlines() == [
        "web1: Create docker group      [changed: group docker absent, will create]",
        "web1: Create lusers system group [changed: group lusers absent, will create]",
        "web2: Create docker group      [changed: group docker absent, will create]",
        "web2: Create lusers system group [changed: group lusers absent, will create]",
    ]
    assert report is not None
    # Lockstep: every host finishes the docker step before either creates
    # lusers, and the elevated shells land in the log.
    assert [e.name for e in report.log] == [
        "Create docker group",
        "Create docker group",
        "Create lusers system group",
        "Create lusers system group",
    ]
    log = format_log(report)
    assert "$ sudo groupadd docker" in log
    assert "$ sudo groupadd -r lusers --gid 1500" in log
    for host in hosts:
        assert host.os_groups == ["wheel", "docker", "lusers"]


async def test_group_second_prepare_plans_no_change():
    host = SimulatedHost(name="h", os_groups=["wheel"])
    runtimes, plans = await run_prepare([host], ensure_groups)
    assert [p.changed for p in plans["h"]] == [True, True]
    report = await run_apply(runtimes, plans=plans)
    assert [e.changed for e in report.log] == [True, True]
    assert host.os_groups == ["wheel", "docker", "lusers"]
    # The apply mutated the seam, so a fresh prepare sees both groups and
    # plans no change -- the operation is idempotent across runs.
    runtimes, plans = await run_prepare([host], ensure_groups)
    assert [p.changed for p in plans["h"]] == [False, False]
    assert [p.reason for p in plans["h"]] == [
        "group docker already present",
        "group lusers already present",
    ]


async def test_group_duplicate_requests_no_op_only_in_apply():
    def twice(host: Host) -> Generator[OperationRequest]:
        yield op(server.group, name="Create docker group", group="docker", sudo=True)
        yield op(
            server.group, name="Create docker group again", group="docker", sudo=True
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], twice)
    # The prepare cache persists across a host's requests and prepare never
    # sends, so both identical requests plan a change...
    assert [p.changed for p in plans["h"]] == [True, True]
    report = await run_apply(runtimes, plans=plans)
    # ...while the per-step cache recreation makes the second a no-op.
    assert [e.changed for e in report.log] == [True, False]
    assert host.os_groups == ["docker"]


async def test_group_dry_run_sends_nothing():
    _plans, report, hosts = await run(
        INVENTORY, ensure_groups, host_factory=wheel_only_factory, dry=True
    )
    assert report is None
    for host in hosts:
        assert host.os_groups == ["wheel"]


# --- server.user ------------------------------------------------------------


def test_user_add_command_shell_rendering():
    assert UserAddCommand(user="someuser").shell() == "useradd -M someuser"
    # The baseline's add.json argument order, verbatim.
    assert (
        UserAddCommand(
            user="someuser",
            home="homedir",
            shell_path="shellbin",
            group="mygroup",
            groups=("secondary_group", "another"),
            system=True,
            uid=1000,
            comment="Full Name",
            create_home=True,
            password="$somecryptedpassword$",
        ).shell()
        == "useradd -d homedir -s shellbin -g mygroup"
        " -G secondary_group,another -r --uid 1000 -c 'Full Name' -m"
        " -p '$somecryptedpassword$' someuser"
    )
    # unique=False renders -o; a uid of 0 is skipped (the baseline's `if uid:`).
    assert (
        UserAddCommand(user="someuser", uid=0, unique=False).shell()
        == "useradd -o -M someuser"
    )
    assert (
        UserAddCommand(user="someuser", home="/home/someuser", uid=0, unique=False).shell()
        == "useradd -d /home/someuser -o -M someuser"
    )
    # shlex leaves a safe name alone and quotes an unsafe one.
    assert (
        UserAddCommand(user="some user; whoami", comment="Full Name; rm -rf /").shell()
        == "useradd -c 'Full Name; rm -rf /' -M 'some user; whoami'"
    )
    assert command_shell(UserAddCommand(user="someuser"), sudo=True) == (
        "sudo useradd -M someuser"
    )


def test_user_mod_command_shell_rendering():
    assert UserModCommand(user="someuser", shell_path="/bin/false").shell() == (
        "usermod -s /bin/false someuser"
    )
    assert (
        UserModCommand(
            user="someuser",
            home="/home/someotheruser",
            shell_path="/bin/false",
            group="somegroup",
            groups=("group1", "group2", "group3"),
            comment="New Full Name",
        ).shell()
        == "usermod -d /home/someotheruser -s /bin/false -g somegroup"
        " -G group1,group2,group3 -c 'New Full Name' someuser"
    )
    # append=True is the -a -G pair (the baseline's append_secondary_groups).
    assert (
        UserModCommand(user="someuser", groups=("group3", "group4"), append=True).shell()
        == "usermod -a -G group3,group4 someuser"
    )
    # Group names are quoted individually, so a separator never merges them.
    assert (
        UserModCommand(user="someuser", groups=("group one", "group; two")).shell()
        == "usermod -G 'group one','group; two' someuser"
    )
    assert (
        UserModCommand(user="someuser", password="$pw$").shell()
        == "usermod -p '$pw$' someuser"
    )
    assert command_shell(UserModCommand(user="someuser"), sudo=True) == (
        "sudo usermod someuser"
    )


def test_user_del_command_shell_rendering():
    assert UserDelCommand(user="someuser").shell() == "userdel someuser"
    assert UserDelCommand(user="evil; rm -rf /").shell() == "userdel 'evil; rm -rf /'"
    assert command_shell(UserDelCommand(user="someuser"), sudo=True) == (
        "sudo userdel someuser"
    )


def test_home_descriptor_shell_rendering():
    assert _HomeMkdirCommand(path="/srv/deploy").shell() == "mkdir -p /srv/deploy"
    assert _HomeMkdirCommand(path="/home/evil $(id)").shell() == (
        "mkdir -p '/home/evil $(id)'"
    )
    assert _HomeChownCommand(path="homedir", user="someuser", group="mygroup").shell() == (
        "chown someuser:mygroup homedir"
    )
    assert _HomeChownCommand(path="homedir", user="some user", group="grp").shell() == (
        "chown 'some user':grp homedir"
    )
    assert command_shell(_HomeChownCommand(path="homedir", user="u", group="g"), sudo=True) == (
        "sudo chown u:g homedir"
    )


# The baseline's own Linux operation fixtures (tests/operations/server.user/),
# replayed verbatim: (fixture, user, os_users, os_groups, directory_stats,
# link_stats, kwargs, expected shells). A fixture's files.Directory row of
# null/False/dict maps onto an absent, False or dict directory_stats entry,
# and files.Link onto link_stats.
# The keys* and *_missing_* fixtures are deliberately not replayed: the
# public_keys/delete_keys branch and the plan-time Which guards were dropped.
BASELINE_USER_FIXTURES = [
    ("add.json", "someuser", {}, [], {}, {}, {
        "home": "homedir", "shell": "shellbin", "group": "mygroup",
        "groups": ("secondary_group", "another"), "uid": 1000, "system": True,
        "comment": "Full Name", "create_home": True,
        "password": "$somecryptedpassword$",
    }, [
        (
            "useradd -d homedir -s shellbin -g mygroup -G secondary_group,another"
            " -r --uid 1000 -c 'Full Name' -m -p '$somecryptedpassword$' someuser"
        ),
        "mkdir -p homedir",
        "chown someuser:mygroup homedir",
    ]),
    ("add_needs_quotes.json", "some user; whoami", {}, [], {}, {}, {
        "home": "/home/evil $(id)", "shell": "/bin/bash",
        "comment": "Full Name; rm -rf /", "ensure_home": False,
    }, [
        (
            "useradd -d '/home/evil $(id)' -s /bin/bash -c 'Full Name; rm -rf /'"
            " -M 'some user; whoami'"
        ),
    ]),
    ("add_groups_needs_quotes.json", "testuser", {}, [], {}, {}, {
        "groups": ("group one", "group; two"), "ensure_home": False,
    }, [
        "useradd -d /home/testuser -G 'group one','group; two' -M testuser",
    ]),
    # A group of the user's name already exists: useradd is told to use it.
    ("add_group_exists.json", "someuser", {}, ["someuser"], {}, {}, {}, [
        "useradd -d /home/someuser -g someuser -M someuser",
        "mkdir -p /home/someuser",
        "chown someuser:someuser /home/someuser",
    ]),
    ("add_non_unique.json", "someuser", {}, [], {}, {}, {
        "unique": False, "ensure_home": False,
    }, ["useradd -d /home/someuser -o -M someuser"]),
    # A symlinked home is accepted as-is (the baseline's _no_fail_on_link).
    ("add_home_is_link.json", "someuser", {}, [], {"homedir": False},
     {"homedir": {"mode": 777}}, {"home": "homedir"},
     ["useradd -d homedir -M someuser"]),
    ("delete.json", "someuser", {"someuser": {"home": "/home",
     "shell": "/bin/bash"}}, [], {}, {}, {"present": False},
     ["userdel someuser"]),
    ("delete_needs_quotes.json", "evil; rm -rf /",
     {"evil; rm -rf /": {"home": "/home", "shell": "/bin/bash"}}, [], {}, {},
     {"present": False}, ["userdel 'evil; rm -rf /'"]),
    ("edit.json", "someuser", {"someuser": {"comment": "Full Name",
     "home": "/home/someuser", "shell": "/bin/bash", "group": "nowt",
     "groups": ["group1", "group2"], "password": "$somepw$"}}, [],
     {"/home/someotheruser": {"user": "someuser", "group": "somegroup"}}, {}, {
        "shell": "/bin/false", "group": "somegroup",
        "groups": ("group1", "group2", "group3"), "home": "/home/someotheruser",
        "comment": "New Full Name",
    }, [
        (
            "usermod -d /home/someotheruser -s /bin/false -g somegroup"
            " -G group1,group2,group3 -c 'New Full Name' someuser"
        ),
    ]),
    ("edit_needs_quotes.json", "some user; whoami",
     {"some user; whoami": {"comment": "Old Name", "home": "/home/old",
      "shell": "/bin/sh", "group": "oldgroup", "groups": [], "password": ""}},
     [], {}, {}, {
        "home": "/home/evil $(id)", "shell": "/bin/bash; rm -rf /",
        "group": "mygroup; inject", "comment": "Full Name; rm -rf /",
        "ensure_home": False,
    }, [
        (
            "usermod -d '/home/evil $(id)' -s '/bin/bash; rm -rf /'"
            " -g 'mygroup; inject' -c 'Full Name; rm -rf /' 'some user; whoami'"
        ),
    ]),
    ("append_secondary_groups.json", "someuser", {"someuser":
     {"home": "/home/someuser", "group": "somegroup",
      "groups": ["group1", "group2"]}}, [],
     {"/home/someuser": {"user": "someuser", "group": "somegroup"}}, {}, {
        "group": "somegroup", "groups": ("group3", "group4"), "append": True,
    }, ["usermod -a -G group3,group4 someuser"]),
    # append never removes, and membership the user already has is a no-op.
    ("append_secondary_existing_groups.json", "someuser", {"someuser":
     {"home": "/home/someuser", "group": "somegroup",
      "groups": ["group1", "group2", "group3"]}}, [],
     {"/home/someuser": {"user": "someuser", "group": "somegroup"}}, {}, {
        "group": "somegroup", "groups": ("group1", "group2"), "append": True,
    }, []),
    # exists_noop.json: the recorded user already matches, home included.
    ("exists_noop.json", "someuser", {"someuser": {"home": "/home/someuser",
     "shell": "/bin/bash", "group": "someuser", "groups": [], "comment": "",
     "password": ""}}, ["someuser"],
     {"/home/someuser": {"user": "someuser", "group": "someuser"}}, {}, {}, []),
]


@pytest.mark.parametrize(
    ("fixture", "user", "users", "groups", "directories", "links",
     "kwargs", "expected"),
    BASELINE_USER_FIXTURES,
    ids=[row[0] for row in BASELINE_USER_FIXTURES],
)
async def test_user_matches_baseline_operation_fixtures(
    fixture: str,
    user: str,
    users: dict,
    groups: list,
    directories: dict,
    links: dict,
    kwargs: dict,
    expected: list[str],
):
    host = SimulatedHost(
        name="h",
        os_users=users,
        os_groups=groups,
        directory_stats=directories,
        link_stats=links,
    )
    ctx, commands = await materialize(host, op(server.user, user=user, **kwargs))
    assert [cmd.shell() for cmd in commands] == expected, fixture
    assert ctx.changed == bool(expected)


async def test_user_creates_an_absent_user():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(host, op(server.user, user="deploy"))
    assert ctx.changed
    assert ctx.reason == "user deploy absent, will create; home /home/deploy missing, will create"
    assert commands == [
        UserAddCommand(user="deploy", home="/home/deploy", groups=()),
        _HomeMkdirCommand(path="/home/deploy"),
        _HomeChownCommand(path="/home/deploy", user="deploy", group="deploy"),
    ]


async def test_user_removes_a_present_user():
    host = SimulatedHost(name="h", os_users={"deploy": {"home": "/home/deploy"}})
    ctx, commands = await materialize(
        host, op(server.user, user="deploy", present=False)
    )
    assert ctx.changed
    assert ctx.reason == "user deploy present, will remove"
    # The deletion branch never touches the home directory (baseline parity).
    assert commands == [UserDelCommand(user="deploy")]


async def test_user_no_change_when_already_absent():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, op(server.user, user="deploy", present=False)
    )
    assert not ctx.changed
    assert ctx.reason == "user deploy already absent"
    assert commands == []


async def test_user_home_already_owned_needs_no_command():
    host = SimulatedHost(
        name="h",
        os_users={"deploy": {"home": "/srv/deploy", "group": "deploy"}},
        directory_stats={"/srv/deploy": {"user": "deploy", "group": "deploy", "mode": 755}},
    )
    ctx, commands = await materialize(host, op(server.user, user="deploy"))
    assert not ctx.changed
    assert ctx.reason == "user deploy already as requested; directory /srv/deploy already exists"
    assert commands == []


async def test_user_wrong_home_owner_only_chowns():
    host = SimulatedHost(
        name="h",
        os_users={"deploy": {"home": "/srv/deploy", "group": "deploy"}},
        directory_stats={"/srv/deploy": {"user": "root", "group": "root", "mode": 755}},
    )
    ctx, commands = await materialize(host, op(server.user, user="deploy"))
    assert ctx.changed
    assert ctx.reason == (
        "user deploy already as requested; home /srv/deploy owner differs, will chown"
    )
    assert commands == [
        _HomeChownCommand(path="/srv/deploy", user="deploy", group="deploy")
    ]


async def test_user_rejects_a_non_directory_home():
    # The baseline's files.directory raises when the path exists as a plain file
    # and is not a symlink either (_no_fail_on_link only covers links).
    host = SimulatedHost(
        name="h",
        directory_stats={"homedir": False},
        link_stats={"homedir": False},
    )
    with pytest.raises(OperationError, match="homedir exists and is not a directory"):
        await materialize(host, op(server.user, user="someuser", home="homedir"))


async def test_user_update_carries_only_the_differing_fields():
    host = SimulatedHost(
        name="h",
        os_users={
            "deploy": {
                "home": "/home/deploy",
                "shell": "/bin/bash",
                "group": "web",
                "groups": ["wheel"],
                "comment": "Deploy User",
            }
        },
    )
    ctx, commands = await materialize(
        host,
        op(
            server.user,
            user="deploy",
            shell="/bin/bash",
            group="web",
            comment="Deploy User",
            home="/srv/deploy",
            ensure_home=False,
        ),
    )
    assert ctx.changed
    assert ctx.reason == "user deploy differs, will update"
    # Only the home differs: shell/group/comment already match the fact.
    assert commands == [UserModCommand(user="deploy", home="/srv/deploy", groups=())]


async def test_user_uid_zero_is_skipped():
    # The baseline's `if uid:` truthiness check: a uid of 0 renders no flag.
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize(
        host, op(server.user, user="deploy", uid=0, ensure_home=False)
    )
    assert commands[0].shell() == "useradd -d /home/deploy -M deploy"


async def test_user_absorbs_unknown_keyword_arguments():
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize(
        host, op(server.user, user="deploy", _extra_global=True, ensure_home=False)
    )
    assert commands == [UserAddCommand(user="deploy", home="/home/deploy", groups=())]


async def test_user_groups_kwarg_must_stay_hashable():
    # op() hashes every argument for compose(dedupe=True), so a list is
    # rejected at request construction -- the option is tuple-only.
    with pytest.raises(UnhashableArgumentError):
        op(server.user, user="deploy", groups=["wheel", "web"])
    request = op(server.user, user="deploy", groups=("wheel", "web"), ensure_home=False)
    assert request.kwargs["groups"] == ("wheel", "web")


async def test_user_commands_mutate_the_simulated_host():
    host = SimulatedHost(name="h", os_groups=["web", "wheel", "admins"])
    await send_command(
        host,
        UserAddCommand(
            user="deploy",
            home="/srv/deploy",
            shell_path="/bin/bash",
            group="web",
            groups=("wheel",),
            comment="Deploy User",
        ),
    )
    row = host.os_users["deploy"]
    assert row["home"] == "/srv/deploy"
    assert row["shell"] == "/bin/bash"
    assert row["group"] == "web"
    assert row["groups"] == ["wheel"]
    assert row["comment"] == "Deploy User"
    # The home pair rides the files.directory seams: mkdir then chown.
    await send_command(host, _HomeMkdirCommand(path="/srv/deploy"))
    assert host.directory_stats["/srv/deploy"] == {
        "mode": 755,
        "user": "root",
        "group": "root",
    }
    await send_command(
        host, _HomeChownCommand(path="/srv/deploy", user="deploy", group="web")
    )
    assert host.directory_stats["/srv/deploy"] == {
        "mode": 755,
        "user": "deploy",
        "group": "web",
    }
    # A modify writes only the fields the descriptor carries, and -a unions.
    await send_command(
        host, UserModCommand(user="deploy", shell_path="/bin/sh", groups=("admins",), append=True)
    )
    assert host.os_users["deploy"]["shell"] == "/bin/sh"
    assert host.os_users["deploy"]["groups"] == ["wheel", "admins"]
    await send_command(host, UserModCommand(user="deploy", groups=("web",)))
    assert host.os_users["deploy"]["groups"] == ["web"]
    # userdel leaves the home directory alone (no -r is ever emitted).
    await send_command(host, UserDelCommand(user="deploy"))
    assert "deploy" not in host.os_users
    assert "/srv/deploy" in host.directory_stats


def ensure_deploy_users(host: Host) -> Generator[OperationRequest]:
    """The example deploy: create the deploy user, then drop the legacy one."""
    yield op(
        server.user,
        name="Create the deploy user",
        user="deploy",
        home="/srv/deploy",
        shell="/bin/bash",
        group="web",
        groups=("wheel",),
        sudo=True,
    )
    yield op(
        server.user,
        name="Remove the legacy user",
        user="legacy",
        present=False,
        sudo=True,
    )


def users_only_factory(entry: dict) -> Host:
    """``host_factory`` building hosts whose only user is the legacy one."""
    host = SimulatedHost.from_inventory(entry)
    host.os_users = {"legacy": {"home": "/home/legacy", "group": "legacy"}}
    host.os_groups = ["web", "wheel"]
    return host


async def test_user_runs_across_hosts_in_lockstep():
    plans, report, hosts = await run(
        INVENTORY, ensure_deploy_users, host_factory=users_only_factory
    )
    assert [[p.request.name, p.changed] for p in plans["web1"]] == [
        ["Create the deploy user", True],
        ["Remove the legacy user", True],
    ]
    assert report is not None
    # Lockstep: every host creates the user before either removes one.
    assert [e.name for e in report.log] == [
        "Create the deploy user",
        "Create the deploy user",
        "Remove the legacy user",
        "Remove the legacy user",
    ]
    log = format_log(report)
    assert "$ sudo useradd -d /srv/deploy -s /bin/bash -g web -G wheel -M deploy" in log
    assert "$ sudo mkdir -p /srv/deploy" in log
    assert "$ sudo chown deploy:web /srv/deploy" in log
    assert "$ sudo userdel legacy" in log
    for host in hosts:
        assert sorted(host.os_users) == ["deploy"]
        assert host.directory_stats["/srv/deploy"]["user"] == "deploy"


async def test_user_second_prepare_plans_no_change():
    host = SimulatedHost(name="h", os_groups=["web", "wheel"])
    host.os_users = {"legacy": {"home": "/home/legacy", "group": "legacy"}}
    runtimes, plans = await run_prepare([host], ensure_deploy_users)
    assert [p.changed for p in plans["h"]] == [True, True]
    report = await run_apply(runtimes, plans=plans)
    assert [e.changed for e in report.log] == [True, True]
    # The apply mutated the seams, so a fresh prepare sees the requested
    # state and plans no change -- the operation is idempotent across runs.
    runtimes, plans = await run_prepare([host], ensure_deploy_users)
    assert [p.changed for p in plans["h"]] == [False, False]
    assert [p.reason for p in plans["h"]] == [
        "user deploy already as requested; directory /srv/deploy already exists",
        "user legacy already absent",
    ]


async def test_user_duplicate_requests_no_op_only_in_apply():
    def twice(host: Host) -> Generator[OperationRequest]:
        yield op(
            server.user, name="Create deploy", user="deploy", ensure_home=False, sudo=True
        )
        yield op(
            server.user,
            name="Create deploy again",
            user="deploy",
            ensure_home=False,
            sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], twice)
    # The prepare cache persists across a host's requests and prepare never
    # sends, so both identical requests plan a change...
    assert [p.changed for p in plans["h"]] == [True, True]
    report = await run_apply(runtimes, plans=plans)
    # ...while the per-step cache recreation makes the second a no-op.
    assert [e.changed for e in report.log] == [True, False]
    assert sorted(host.os_users) == ["deploy"]


async def test_user_dry_run_sends_nothing():
    _plans, report, hosts = await run(
        INVENTORY, ensure_deploy_users, host_factory=users_only_factory, dry=True
    )
    assert report is None
    for host in hosts:
        assert sorted(host.os_users) == ["legacy"]
        assert host.directory_stats == {}


async def test_user_never_branches_on_the_home_state():
    # The ensure_home probes are awaited on every pass, so the request
    # sequence cannot depend on what they return: an owned directory, a
    # symlinked path and an absent one all take the same fact reads and
    # plan the identical (empty) command list for an up-to-date user.
    for directories, links in (
        ({"homedir": {"user": "someuser", "group": "someuser"}}, {}),
        ({"homedir": False}, {"homedir": {"mode": 777}}),
    ):
        host = SimulatedHost(
            name="h",
            os_users={"someuser": {"home": "homedir", "group": "someuser"}},
            directory_stats=directories,
            link_stats=links,
        )
        ctx, commands = await materialize(
            host, op(server.user, user="someuser", home="homedir")
        )
        assert not ctx.changed
        assert commands == []


async def test_user_home_probe_facts_are_the_server_local_stand_ins():
    # The zero-cross-module-import invariant keeps the home probes private
    # to the server namespace, but they must read exactly the seams the
    # files facts use and shell out the same guarded forms.
    assert _HomeDirectory.command("/srv/deploy").startswith("test -d /srv/deploy && ")
    assert "stat -c 'user=%U group=%G'" in _HomeDirectory.command("/srv/deploy")
    assert _HomeIsLink.command("/srv/deploy") == "test -L /srv/deploy && echo yes"
    host = CannedShellHost("")
    assert await _HomeDirectory.process(host, "/srv/deploy") is None
    assert host.shells == [_HomeDirectory.command("/srv/deploy")]
    host = CannedShellHost("user=someuser group=mygroup")
    assert await _HomeDirectory.process(host, "homedir") == {
        "user": "someuser",
        "group": "mygroup",
    }
    host = CannedShellHost("/srv/deploy: not a directory")
    assert await _HomeDirectory.process(host, "/srv/deploy") is False
    assert await _HomeIsLink.process(CannedShellHost("yes"), "homedir") is True


# --- hostname operation -------------------------------------------------------


def test_set_hostname_command_shell_rendering():
    assert (
        SetHostnameCommand(hostname="web1.example.com").shell()
        == "hostname web1.example.com"
    )
    assert (
        SetHostnameCommand(hostname="web1.example.com", hostnamectl=True).shell()
        == "hostnamectl set-hostname web1.example.com"
    )
    # The baseline's QuoteString: a name carrying shell metacharacters is quoted.
    assert (
        SetHostnameCommand(hostname="my host; whoami", hostnamectl=True).shell()
        == "hostnamectl set-hostname 'my host; whoami'"
    )
    assert (
        SetHostnameCommand(hostname="my host; whoami").shell()
        == "hostname 'my host; whoami'"
    )
    assert command_shell(SetHostnameCommand(hostname="web1"), sudo=True) == (
        "sudo hostname web1"
    )
    assert command_shell(
        SetHostnameCommand(hostname="web1", hostnamectl=True), sudo=True
    ) == "sudo hostnamectl set-hostname web1"
    assert command_shell(
        SetHostnameCommand(hostname="web1", hostnamectl=True), su=True
    ) == "su -c 'hostnamectl set-hostname web1'"


@pytest.mark.parametrize(
    ("name", "current", "hostnamectl", "expected"),
    [
        # The baseline's own operation fixtures (tests/operations/server.hostname/).
        # The upload step of set_hostname_bsd.json and set_hostname_linux.json
        # is the deliberately dropped hostname_file branch, so only the shell
        # command remains; the fixtures' server.Os value fed that branch's
        # file auto-detection and is no longer read.
        pytest.param(
            "my-hostname.net", "my-hostname.net", "hostnamectl", [],
            id="hostnamectl_noop",
        ),
        pytest.param(
            "my-hostname.net", "my-hostname.net", None, [],
            id="hostname_noop",
        ),
        pytest.param(
            "my-hostname.net", "nope", None, ["hostname my-hostname.net"],
            id="set_hostname_bsd",
        ),
        pytest.param(
            "my-hostname.net", "nope", "hostnamectl",
            ["hostnamectl set-hostname my-hostname.net"],
            id="set_hostnamectl_linux",
        ),
        pytest.param(
            "my host; whoami", "nope", "hostnamectl",
            ["hostnamectl set-hostname 'my host; whoami'"],
            id="set_hostnamectl_needs_quotes",
        ),
        pytest.param(
            "my-hostname.net", "nope", None, ["hostname my-hostname.net"],
            id="set_hostname_linux",
        ),
    ],
)
async def test_hostname_matches_baseline_operation_fixtures(
    name: str, current: str, hostnamectl: str | None, expected: list[str]
):
    host = SimulatedHost(name="h", os_hostname=current)
    if hostnamectl is not None:
        host.command_paths = {"hostnamectl": hostnamectl}
    ctx, commands = await materialize(host, op(server.hostname, hostname=name))
    assert ctx.changed == bool(expected)
    assert [cmd.shell() for cmd in commands] == expected
    if not expected:
        # The baseline's noop_description, verbatim.
        assert ctx.reason == "hostname is set"


async def test_hostname_sets_via_hostnamectl_when_available():
    host = SimulatedHost(
        name="h",
        os_hostname="old-host.net",
        command_paths={"hostnamectl": "/usr/bin/hostnamectl"},
    )
    ctx, commands = await materialize(
        host, op(server.hostname, hostname="web1.example.com")
    )
    assert ctx.changed
    assert ctx.reason == "hostname is 'old-host.net', will set 'web1.example.com'"
    assert commands == [
        SetHostnameCommand(hostname="web1.example.com", hostnamectl=True)
    ]


async def test_hostname_falls_back_to_the_bare_hostname_command():
    # No hostnamectl resolvable (the default empty command_paths): the
    # legacy form is yielded, with no file write (the dropped branch).
    host = SimulatedHost(name="h", os_hostname="old-host.net")
    ctx, commands = await materialize(
        host, op(server.hostname, hostname="web1.example.com")
    )
    assert ctx.changed
    assert commands == [SetHostnameCommand(hostname="web1.example.com")]


async def test_hostname_no_change_when_already_set():
    for paths in ({}, {"hostnamectl": "/usr/bin/hostnamectl"}):
        host = SimulatedHost(
            name="h", os_hostname="web1.example.com", command_paths=paths
        )
        ctx, commands = await materialize(
            host, op(server.hostname, hostname="web1.example.com")
        )
        assert not ctx.changed
        assert ctx.reason == "hostname is set"
        assert commands == []


async def test_hostname_absorbs_unknown_keyword_arguments():
    host = SimulatedHost(name="h", os_hostname="old-host.net")
    _ctx, commands = await materialize(
        host, op(server.hostname, hostname="web1.example.com", _extra_global=True)
    )
    assert commands == [SetHostnameCommand(hostname="web1.example.com")]


async def test_hostname_command_mutates_the_simulated_host():
    host = SimulatedHost(name="h")
    await send_command(
        host, SetHostnameCommand(hostname="web1.example.com", hostnamectl=True)
    )
    assert host.os_hostname == "web1.example.com"
    # Both command forms set the same running hostname in the simulation.
    await send_command(host, SetHostnameCommand(hostname="web2.example.com"))
    assert host.os_hostname == "web2.example.com"


def set_hostnames(host: Host) -> Generator[OperationRequest]:
    """The example deploy: name each host after its inventory label."""
    yield op(
        server.hostname,
        name="Set hostname",
        hostname=f"{host.name}.example.com",
        sudo=True,
    )


def old_hostname_factory(entry: dict) -> Host:
    """``host_factory`` building SimulatedHosts with hostnamectl available."""
    host = SimulatedHost.from_inventory(entry)
    host.os_hostname = "old-host.net"
    host.command_paths = {"hostnamectl": "/usr/bin/hostnamectl"}
    return host


async def test_hostname_runs_across_hosts_in_lockstep():
    plans, report, hosts = await run(
        INVENTORY, set_hostnames, host_factory=old_hostname_factory
    )
    assert format_plan(plans).splitlines() == [
        (
            "web1: Set hostname             [changed: hostname is 'old-host.net', "
            "will set 'web1.example.com']"
        ),
        (
            "web2: Set hostname             [changed: hostname is 'old-host.net', "
            "will set 'web2.example.com']"
        ),
    ]
    assert report is not None
    assert [e.name for e in report.log] == ["Set hostname", "Set hostname"]
    log = format_log(report)
    # The elevated shells land in the log, one per host.
    assert "$ sudo hostnamectl set-hostname web1.example.com" in log
    assert "$ sudo hostnamectl set-hostname web2.example.com" in log
    for host in hosts:
        assert host.os_hostname == f"{host.name}.example.com"


async def test_hostname_without_hostnamectl_runs_the_bare_command():
    def legacy_factory(entry: dict) -> Host:
        host = SimulatedHost.from_inventory(entry)
        host.os_hostname = "old-host.net"
        return host

    _plans, report, hosts = await run(
        INVENTORY, set_hostnames, host_factory=legacy_factory
    )
    assert report is not None
    log = format_log(report)
    assert "$ sudo hostname web1.example.com" in log
    assert "$ sudo hostname web2.example.com" in log
    for host in hosts:
        assert host.os_hostname == f"{host.name}.example.com"


async def test_hostname_second_prepare_plans_no_change():
    host = old_hostname_factory({"host": "h", "connection": {}})
    runtimes, plans = await run_prepare([host], set_hostnames)
    assert [p.changed for p in plans["h"]] == [True]
    report = await run_apply(runtimes, plans=plans)
    assert [e.changed for e in report.log] == [True]
    assert host.os_hostname == "h.example.com"
    # The apply mutated the seam, so a fresh prepare sees the new name and
    # plans no change -- the operation is idempotent across runs.
    runtimes, plans = await run_prepare([host], set_hostnames)
    assert [p.changed for p in plans["h"]] == [False]
    assert [p.reason for p in plans["h"]] == ["hostname is set"]


async def test_hostname_duplicate_requests_no_op_only_in_apply():
    def twice(host: Host) -> Generator[OperationRequest]:
        yield op(
            server.hostname, name="Set hostname", hostname="web1.example.com",
            sudo=True,
        )
        yield op(
            server.hostname, name="Set hostname again", hostname="web1.example.com",
            sudo=True,
        )

    host = SimulatedHost(name="h", os_hostname="old-host.net")
    runtimes, plans = await run_prepare([host], twice)
    # The prepare cache persists across a host's requests and prepare never
    # sends, so both identical requests plan a change...
    assert [p.changed for p in plans["h"]] == [True, True]
    report = await run_apply(runtimes, plans=plans)
    # ...while the per-step cache recreation makes the second a no-op.
    assert [e.changed for e in report.log] == [True, False]
    assert host.os_hostname == "web1.example.com"


async def test_hostname_dry_run_sends_nothing():
    _plans, report, hosts = await run(
        INVENTORY, set_hostnames, host_factory=old_hostname_factory, dry=True
    )
    assert report is None
    for host in hosts:
        assert host.os_hostname == "old-host.net"


# --- kill operation -----------------------------------------------------------


NGINX_RECORD = {
    "user": "root",
    "state": "Ss",
    "cpu_percent": 0.0,
    "mem_percent": 0.1,
    "command": "nginx",
    "args": "nginx: master process /usr/sbin/nginx",
}
SSHD_RECORD = {
    "user": "root",
    "state": "Ss",
    "cpu_percent": 0.0,
    "mem_percent": 0.0,
    "command": "sshd",
    "args": "/usr/sbin/sshd -D",
}


def test_kill_command_shell_rendering():
    assert KillCommand(pid=1234).shell() == "kill -TERM 1234"
    assert KillCommand(pid=5678, signal="KILL").shell() == "kill -KILL 5678"
    # The baseline's QuoteString: a signal carrying shell metacharacters is
    # quoted, so it reaches kill literally instead of being executed.
    assert (
        KillCommand(pid=1234, signal="9; rm -rf /").shell()
        == "kill '-9; rm -rf /' 1234"
    )
    assert command_shell(KillCommand(pid=1234), sudo=True) == "sudo kill -TERM 1234"
    assert command_shell(KillCommand(pid=1234), su=True) == "su -c 'kill -TERM 1234'"


@pytest.mark.parametrize(
    ("pid", "kwargs", "expected"),
    [
        # The baseline's own operation fixtures (tests/operations/server.kill/),
        # all marked "idempotent": false -- the kill always yields.
        pytest.param(1234, {}, ["kill -TERM 1234"], id="kill_default"),
        pytest.param(
            5678, {"signal": "KILL"}, ["kill -KILL 5678"], id="kill_signal"
        ),
        pytest.param(
            1234,
            {"signal": "9; rm -rf /"},
            ["kill '-9; rm -rf /' 1234"],
            id="kill_quotes_injection",
        ),
    ],
)
async def test_kill_matches_baseline_operation_fixtures(
    pid: int, kwargs: dict, expected: list[str]
):
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(host, op(server.kill, pid=pid, **kwargs))
    assert ctx.changed
    assert [cmd.shell() for cmd in commands] == expected


async def test_kill_is_stateless_and_sets_the_reason():
    # No processes seeded at all: the operation reads no fact and still
    # plans the kill (the baseline's non-idempotent behaviour).
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(host, op(server.kill, pid=1234))
    assert ctx.changed
    assert ctx.reason == "kill process 1234 with signal TERM"
    assert commands == [KillCommand(pid=1234)]


async def test_kill_reads_no_fact():
    host = CannedShellHost("")
    _ctx, commands = await materialize(host, op(server.kill, pid=1234))
    assert commands == [KillCommand(pid=1234)]
    # Not a single shell was gathered -- the PID is the caller's input.
    assert host.shells == []


async def test_kill_absorbs_unknown_keyword_arguments():
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize(
        host, op(server.kill, pid=1234, _extra_global=True)
    )
    assert commands == [KillCommand(pid=1234)]


async def test_kill_command_mutates_the_simulated_host():
    host = SimulatedHost(
        name="h", processes={1234: dict(NGINX_RECORD), 5678: dict(SSHD_RECORD)}
    )
    await send_command(host, KillCommand(pid=1234))
    assert 1234 not in host.processes
    # The Processes fact's next read no longer lists the killed PID.
    assert await Processes.process(host) == {5678: SSHD_RECORD}
    # An absent PID is tolerated by the simulation (the real kill would
    # exit non-zero and fail the apply step).
    await send_command(host, KillCommand(pid=9999))
    assert 5678 in host.processes


def kill_workers(host: Host) -> Generator[OperationRequest]:
    """The example deploy: kill one known PID, elevated."""
    yield op(server.kill, name="Kill worker", pid=1234, sudo=True)


def worker_factory(entry: dict) -> Host:
    """``host_factory`` building SimulatedHosts running one nginx PID."""
    host = SimulatedHost.from_inventory(entry)
    host.processes = {1234: dict(NGINX_RECORD)}
    return host


async def test_kill_runs_across_hosts_in_lockstep():
    plans, report, hosts = await run(
        INVENTORY, kill_workers, host_factory=worker_factory
    )
    assert format_plan(plans).splitlines() == [
        f"web1: {'Kill worker':<24} [changed: kill process 1234 with signal TERM]",
        f"web2: {'Kill worker':<24} [changed: kill process 1234 with signal TERM]",
    ]
    assert report is not None
    assert [e.name for e in report.log] == ["Kill worker", "Kill worker"]
    log = format_log(report)
    # The elevated shells land in the log, one per host.
    assert log.count("$ sudo kill -TERM 1234") == 2
    for host in hosts:
        assert host.processes == {}


async def test_kill_is_not_idempotent_across_runs():
    host = worker_factory({"host": "h", "connection": {}})
    runtimes, plans = await run_prepare([host], kill_workers)
    assert [p.changed for p in plans["h"]] == [True]
    report = await run_apply(runtimes, plans=plans)
    assert [e.changed for e in report.log] == [True]
    assert host.processes == {}
    # Stateless: even with the PID gone a fresh prepare still plans the
    # kill -- unlike server.hostname, there is no fact to compare against.
    _runtimes, plans = await run_prepare([host], kill_workers)
    assert [p.changed for p in plans["h"]] == [True]


async def test_kill_duplicate_requests_both_change_in_apply():
    def twice(host: Host) -> Generator[OperationRequest]:
        yield op(server.kill, name="Kill worker", pid=1234, sudo=True)
        yield op(server.kill, name="Kill worker again", pid=1234, sudo=True)

    host = worker_factory({"host": "h", "connection": {}})
    runtimes, plans = await run_prepare([host], twice)
    assert [p.changed for p in plans["h"]] == [True, True]
    report = await run_apply(runtimes, plans=plans)
    # No fact check makes the second a no-op: both steps report a change.
    # On a real host the second kill of the dead PID would exit non-zero
    # (CommandError); the simulation tolerates the absent PID.
    assert [e.changed for e in report.log] == [True, True]


async def test_kill_dry_run_sends_nothing():
    _plans, report, hosts = await run(
        INVENTORY, kill_workers, host_factory=worker_factory, dry=True
    )
    assert report is None
    for host in hosts:
        assert host.processes == {1234: NGINX_RECORD}


def build_kill_nginx(
    snapshot: dict[str, dict[int, dict]],
) -> Callable[[Host], Generator[OperationRequest]]:
    """The example factory: close over a frozen Processes snapshot.

    The returned deploy branches only on the snapshot taken BEFORE either
    phase, so both phases yield the identical request sequence -- the
    determinism-safe way to turn "find the PID" into "kill it" in one run.
    """

    def deploy(host: Host) -> Generator[OperationRequest]:
        for pid, info in sorted(snapshot[host.name].items()):
            if info["command"] == "nginx":
                yield op(
                    server.kill, name=f"Kill nginx ({pid})", pid=pid, sudo=True
                )

    return deploy


async def test_kill_snapshot_factory_runs_deterministically():
    def factory(entry: dict) -> Host:
        host = SimulatedHost.from_inventory(entry)
        host.processes = {1234: dict(NGINX_RECORD), 5678: dict(SSHD_RECORD)}
        return host

    # The snapshot half of the pattern: one concurrent Processes read
    # across the fleet, keyed by host name, before any deploy runs.
    snapshot = await gather_fact([factory(e) for e in INVENTORY], Processes)
    deploy = build_kill_nginx(snapshot)

    plans, report, _hosts = await run(INVENTORY, deploy, host_factory=factory, dry=True)
    assert report is None
    assert [p.request.name for p in plans["web1"]] == ["Kill nginx (1234)"]

    plans, report, hosts = await run(INVENTORY, deploy, host_factory=factory)
    assert report is not None
    # Same plan from the same frozen snapshot -- no DeterminismError.
    assert [p.request.name for p in plans["web2"]] == ["Kill nginx (1234)"]
    for host in hosts:
        assert host.processes == {5678: SSHD_RECORD}


async def test_kill_branching_on_live_state_raises_determinism_error():
    def live_branch(host: Host) -> Generator[OperationRequest]:
        yield op(server.kill, name="Kill parent", pid=1234, sudo=True)
        # The illegal branch: consult live state the first kill mutates.
        # Prepare sees the parent alive and plans one request; apply's
        # second pull sees it dead and yields a second -- divergence.
        if 1234 not in host.processes:
            yield op(server.kill, name="Kill child", pid=5678, sudo=True)

    host = SimulatedHost(
        name="h", processes={1234: dict(NGINX_RECORD), 5678: dict(SSHD_RECORD)}
    )
    runtimes, plans = await run_prepare([host], live_branch)
    assert [p.request.name for p in plans["h"]] == ["Kill parent"]
    with pytest.raises(DeterminismError):
        await run_apply(runtimes, plans=plans)


# --- server.modprobe ---------------------------------------------------------


def test_modprobe_command_shell_rendering():
    assert ModprobeCommand(modules=("floppy",)).shell() == "modprobe -a floppy"
    assert (
        ModprobeCommand(modules=("floppy",), force=True).shell()
        == "modprobe -f -a floppy"
    )
    assert (
        ModprobeCommand(modules=("floppy",), present=False).shell()
        == "modprobe -r -a floppy"
    )
    assert (
        ModprobeCommand(modules=("floppy",), present=False, force=True).shell()
        == "modprobe -f -r -a floppy"
    )
    assert ModprobeCommand(modules=("mod1", "mod3")).shell() == "modprobe -a mod1 mod3"
    assert (
        ModprobeCommand(modules=("evil; rm -rf /",)).shell()
        == "modprobe -a 'evil; rm -rf /'"
    )
    assert command_shell(ModprobeCommand(modules=("floppy",)), sudo=True) == (
        "sudo modprobe -a floppy"
    )


@pytest.mark.parametrize(
    ("module", "loaded", "kwargs", "expected", "changed"),
    [
        # The baseline's own operation fixtures (tests/operations/server.modprobe/):
        # add.json, add_exists.json, add_force.json, add-list.json,
        # add_multiple_exists.json, add_needs_quotes.json, remove.json,
        # remove-list.json, remove_needs_quotes.json. Only membership in the
        # KernelModules dict matters, so the fixtures' empty info dicts feed
        # the kernel_modules seam verbatim. The *request* arguments are tuples
        # where the baseline's JSON has lists: OperationRequest rejects an
        # unhashable argument value (UnhashableArgumentError).
        ("somemodule", [], {}, ["modprobe -a somemodule"], True),
        ("somemodule", ["somemodule"], {}, [], False),
        ("somemodule", [], {"force": True}, ["modprobe -f -a somemodule"], True),
        (("mod1", "mod2", "mod3"), ["mod2"], {}, ["modprobe -a mod1 mod3"], True),
        (("somemodule", "someothermodule"), ["somemodule", "someothermodule"],
         {}, [], False),
        ("evil; rm -rf /", [], {}, ["modprobe -a 'evil; rm -rf /'"], True),
        ("somemodule", ["somemodule"], {"present": False},
         ["modprobe -r -a somemodule"], True),
        (("mod1", "mod2", "mod3"), ["mod1"], {"present": False},
         ["modprobe -r -a mod1"], True),
        ("evil; rm -rf /", ["evil; rm -rf /"], {"present": False},
         ["modprobe -r -a 'evil; rm -rf /'"], True),
    ],
)
async def test_modprobe_matches_baseline_operation_fixtures(
    module, loaded: list[str], kwargs: dict, expected: list[str], changed: bool
):
    host = SimulatedHost(name="h", kernel_modules={name: {} for name in loaded})
    ctx, commands = await materialize(
        host, op(server.modprobe, module=module, **kwargs)
    )
    assert ctx.changed == changed
    assert [cmd.shell() for cmd in commands] == expected


async def test_modprobe_loads_a_missing_module():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(host, op(server.modprobe, module="floppy"))
    assert ctx.changed
    assert ctx.reason == "module floppy not loaded, will load"
    assert commands == [ModprobeCommand(modules=("floppy",))]


async def test_modprobe_unloads_a_loaded_module():
    host = SimulatedHost(name="h", kernel_modules={"floppy": {}})
    ctx, commands = await materialize(
        host, op(server.modprobe, module="floppy", present=False)
    )
    assert ctx.changed
    assert ctx.reason == "module floppy loaded, will remove"
    assert commands == [ModprobeCommand(modules=("floppy",), present=False)]


async def test_modprobe_no_change_when_already_loaded():
    # The baseline's add_exists.json noop_description, verbatim.
    host = SimulatedHost(name="h", kernel_modules={"somemodule": {}})
    ctx, commands = await materialize(host, op(server.modprobe, module="somemodule"))
    assert not ctx.changed
    assert ctx.reason == "module somemodule is loaded"
    assert commands == []


async def test_modprobe_no_change_when_already_absent():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, op(server.modprobe, module="floppy", present=False)
    )
    assert not ctx.changed
    assert ctx.reason == "module floppy is not loaded"
    assert commands == []


async def test_modprobe_noop_reason_plural_wording():
    # The baseline's add_multiple_exists.json noop_description, verbatim.
    host = SimulatedHost(
        name="h", kernel_modules={"somemodule": {}, "someothermodule": {}}
    )
    ctx, commands = await materialize(
        host, op(server.modprobe, module=("somemodule", "someothermodule"))
    )
    assert not ctx.changed
    assert ctx.reason == "modules somemodule/someothermodule are loaded"
    assert commands == []


async def test_modprobe_partial_list_reason_names_only_the_difference():
    host = SimulatedHost(name="h", kernel_modules={"mod2": {}})
    ctx, commands = await materialize(
        host, op(server.modprobe, module=("mod1", "mod2", "mod3"))
    )
    assert ctx.changed
    assert ctx.reason == "modules mod1/mod3 not loaded, will load"
    assert commands == [ModprobeCommand(modules=("mod1", "mod3"))]


async def test_modprobe_force_does_not_reload_an_in_state_module():
    # Like the baseline, force only flags the command -- it never turns an
    # in-state request into a reload, in either direction.
    host = SimulatedHost(name="h", kernel_modules={"floppy": {}})
    ctx, commands = await materialize(
        host, op(server.modprobe, module="floppy", force=True)
    )
    assert not ctx.changed
    assert commands == []

    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, op(server.modprobe, module="floppy", present=False, force=True)
    )
    assert not ctx.changed
    assert commands == []


async def test_modprobe_absorbs_unknown_keyword_arguments():
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize(
        host, op(server.modprobe, module="floppy", _extra_global=True)
    )
    assert commands == [ModprobeCommand(modules=("floppy",))]


async def test_modprobe_commands_mutate_the_simulated_host():
    host = SimulatedHost(name="h")
    await send_command(host, ModprobeCommand(modules=("floppy", "loop")))
    assert set(host.kernel_modules) == {"floppy", "loop"}
    assert host.kernel_modules["floppy"]["state"] == "Live"
    # A repeat load leaves an existing info dict alone (the operation's fact
    # check would not yield it, but the seam stays safe regardless).
    host.kernel_modules["floppy"]["instances"] = 3
    await send_command(host, ModprobeCommand(modules=("floppy",)))
    assert host.kernel_modules["floppy"]["instances"] == 3
    await send_command(host, ModprobeCommand(modules=("floppy",), present=False))
    assert set(host.kernel_modules) == {"loop"}
    # An absent name is tolerated on unload.
    await send_command(host, ModprobeCommand(modules=("nosuch",), present=False))
    assert set(host.kernel_modules) == {"loop"}


def ensure_modules(host: Host) -> Generator[OperationRequest]:
    """The example deploy: load floppy, then ensure loop and brd are loaded."""
    yield op(server.modprobe, name="Load floppy module", module="floppy", sudo=True)
    yield op(
        server.modprobe,
        name="Load loop and brd modules",
        module=("loop", "brd"),
        sudo=True,
    )


def loop_only_factory(entry: dict) -> Host:
    """``host_factory`` building SimulatedHosts whose only module is loop."""
    host = SimulatedHost.from_inventory(entry)
    host.kernel_modules = {"loop": {"size": "28672", "instances": 0, "state": "Live"}}
    return host


async def test_modprobe_runs_across_hosts_in_lockstep():
    plans, report, hosts = await run(
        INVENTORY, ensure_modules, host_factory=loop_only_factory
    )
    assert format_plan(plans).splitlines() == [
        "web1: Load floppy module       [changed: module floppy not loaded, will load]",
        "web1: Load loop and brd modules [changed: module brd not loaded, will load]",
        "web2: Load floppy module       [changed: module floppy not loaded, will load]",
        "web2: Load loop and brd modules [changed: module brd not loaded, will load]",
    ]
    assert report is not None
    # Lockstep: every host finishes the floppy step before either loads brd,
    # and the elevated shells land in the log -- loop is never re-loaded.
    assert [e.name for e in report.log] == [
        "Load floppy module",
        "Load floppy module",
        "Load loop and brd modules",
        "Load loop and brd modules",
    ]
    log = format_log(report)
    assert "$ sudo modprobe -a floppy" in log
    assert "$ sudo modprobe -a brd" in log
    assert "modprobe -a loop" not in log  # the loaded module is never re-loaded
    for host in hosts:
        assert set(host.kernel_modules) == {"loop", "floppy", "brd"}


async def test_modprobe_second_prepare_plans_no_change():
    host = SimulatedHost(
        name="h", kernel_modules={"loop": {"size": "28672", "instances": 0, "state": "Live"}}
    )
    runtimes, plans = await run_prepare([host], ensure_modules)
    assert [p.changed for p in plans["h"]] == [True, True]
    report = await run_apply(runtimes, plans=plans)
    assert [e.changed for e in report.log] == [True, True]
    assert set(host.kernel_modules) == {"loop", "floppy", "brd"}
    # The apply mutated the seam, so a fresh prepare sees every module and
    # plans no change -- the operation is idempotent across runs.
    runtimes, plans = await run_prepare([host], ensure_modules)
    assert [p.changed for p in plans["h"]] == [False, False]
    assert [p.reason for p in plans["h"]] == [
        "module floppy is loaded",
        "modules loop/brd are loaded",
    ]


async def test_modprobe_duplicate_requests_no_op_only_in_apply():
    def twice(host: Host) -> Generator[OperationRequest]:
        yield op(server.modprobe, name="Load floppy module", module="floppy", sudo=True)
        yield op(
            server.modprobe, name="Load floppy module again", module="floppy", sudo=True
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], twice)
    # The prepare cache persists across a host's requests and prepare never
    # sends, so both identical requests plan a change...
    assert [p.changed for p in plans["h"]] == [True, True]
    report = await run_apply(runtimes, plans=plans)
    # ...while the per-step cache recreation makes the second a no-op.
    assert [e.changed for e in report.log] == [True, False]
    assert set(host.kernel_modules) == {"floppy"}


async def test_modprobe_dry_run_sends_nothing():
    _plans, report, hosts = await run(
        INVENTORY, ensure_modules, host_factory=loop_only_factory, dry=True
    )
    assert report is None
    for host in hosts:
        assert set(host.kernel_modules) == {"loop"}


async def test_modprobe_branching_on_live_state_raises_determinism_error():
    def live_branch(host: Host) -> Generator[OperationRequest]:
        yield op(server.modprobe, name="Load floppy module", module="floppy", sudo=True)
        # The illegal branch: consult live state the first load mutates.
        # Prepare sees floppy absent and plans one request; apply's second
        # pull sees it loaded and yields a second -- divergence.
        if "floppy" in host.kernel_modules:
            yield op(
                server.modprobe,
                name="Unload floppy module",
                module="floppy",
                present=False,
                sudo=True,
            )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], live_branch)
    assert [p.request.name for p in plans["h"]] == ["Load floppy module"]
    with pytest.raises(DeterminismError):
        await run_apply(runtimes, plans=plans)


# --- server.mount ------------------------------------------------------------


def test_mount_command_shell_rendering():
    assert MountCommand(path="/data").shell() == "mount /data"
    assert (
        MountCommand(path="/data", device="/dev/sdb1").shell()
        == "mount /dev/sdb1 /data"
    )
    assert (
        MountCommand(path="/data", device="/dev/sdc3", fs_type="xfs").shell()
        == "mount -t xfs /dev/sdc3 /data"
    )
    assert (
        MountCommand(path="/data", options=("rw", "noatime")).shell()
        == "mount -o rw,noatime /data"
    )
    assert MountCommand(path="/data", mounted=False).shell() == "umount /data"
    assert (
        MountCommand(path="/data", options=("rw", "noatime"), remount=True).shell()
        == "mount -o remount,rw,noatime /data"
    )
    assert (
        MountCommand(path="/data dir; rm -rf /", mounted=False).shell()
        == "umount '/data dir; rm -rf /'"
    )
    assert (
        MountCommand(path="/data dir; rm -rf /", options=("rw",), remount=True).shell()
        == "mount -o remount,rw '/data dir; rm -rf /'"
    )
    assert command_shell(MountCommand(path="/data"), sudo=True) == "sudo mount /data"


@pytest.mark.parametrize(
    ("path", "mounts", "kwargs", "expected", "changed"),
    [
        # The baseline's own Linux operation fixtures
        # (tests/operations/server.mount/), replayed verbatim over the
        # SimulatedHost seams. The bare {} info dicts (mount_exists.json,
        # unmount.json) feed the mounts seam as-is: only membership matters.
        # The *request* arguments are tuples where the baseline's JSON has lists,
        # because OperationRequest rejects an unhashable argument value
        # (UnhashableArgumentError); the seam entries keep the baseline's lists.
        # mount.json
        ("/data", {}, {}, ["mount /data"], True),
        # mount_device.json
        ("/data", {}, {"device": "/dev/sdb1"},
         ["mount /dev/sdb1 /data"], True),
        # mount_device_fstype.json
        ("/data", {}, {"device": "/dev/sdc3", "fs_type": "xfs"},
         ["mount -t xfs /dev/sdc3 /data"], True),
        # mount_options.json
        ("/data", {}, {"options": ("rw",)}, ["mount -o rw /data"], True),
        # mount_exists.json
        ("/data", {"/data": {}}, {}, [], False),
        # mount_exists_device_match.json
        ("/data",
         {"/data": {"device": "/dev/sdb1", "type": "ext4",
                    "options": ["rw", "relatime"]}},
         {"device": "/dev/sdb1"}, [], False),
        # mount_exists_device_fallback.json
        ("/data",
         {"/mnt/data": {"device": "/dev/sdb1", "type": "ext4",
                        "options": ["rw", "relatime"]}},
         {"device": "/dev/sdb1"}, [], False),
        # unmount.json
        ("/data", {"/data": {}}, {"mounted": False},
         ["umount /data"], True),
        # unmount_needs_quotes.json
        ("/data dir; rm -rf /", {"/data dir; rm -rf /": {}},
         {"mounted": False}, ["umount '/data dir; rm -rf /'"], True),
        # unmount_device_fallback.json
        ("/data",
         {"/mnt/data": {"device": "/dev/sdb1", "type": "ext4",
                        "options": ["rw", "relatime"]}},
         {"mounted": False, "device": "/dev/sdb1"},
         ["umount /mnt/data"], True),
        # unmount_device_fallback_needs_quotes.json
        ("/data",
         {"/mnt/my data; rm -rf /": {"device": "/dev/sdb1", "type": "ext4",
                                    "options": ["rw", "relatime"]}},
         {"mounted": False, "device": "/dev/sdb1"},
         ["umount '/mnt/my data; rm -rf /'"], True),
        # remount_options.json
        ("/data", {"/data": {"options": ["rw"]}},
         {"options": ("rw", "noatime")},
         ["mount -o remount,rw,noatime /data"], True),
        # remount_needs_quotes.json
        ("/data dir; rm -rf /", {"/data dir; rm -rf /": {"options": ["rw"]}},
         {"options": ("rw", "noatime")},
         ["mount -o remount,rw,noatime '/data dir; rm -rf /'"], True),
        # remount_options_device_fallback.json
        ("/data",
         {"/mnt/data": {"device": "/dev/sdb1", "type": "ext4",
                        "options": ["rw"]}},
         {"device": "/dev/sdb1", "options": ("rw", "noatime")},
         ["mount -o remount,rw,noatime /mnt/data"], True),
    ],
)
async def test_mount_matches_baseline_operation_fixtures(
    path, mounts: dict, kwargs: dict, expected: list[str], changed: bool
):
    host = SimulatedHost(name="h", mounts=mounts)
    ctx, commands = await materialize(host, op(server.mount, path=path, **kwargs))
    assert ctx.changed == changed
    assert [cmd.shell() for cmd in commands] == expected


async def test_mount_mounts_a_missing_filesystem():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host,
        op(
            server.mount,
            path="/data",
            device="/dev/sdb1",
            fs_type="ext4",
            options=("rw",),
        ),
    )
    assert ctx.changed
    assert ctx.reason == "filesystem /data is not mounted, will mount"
    assert commands == [
        MountCommand(path="/data", device="/dev/sdb1", fs_type="ext4", options=("rw",))
    ]


async def test_mount_unmount_reason_names_the_found_mount_point():
    # The device fallback finds /mnt/data; the unmount reason and command
    # act on the found mount point, not the requested path (the baseline).
    host = SimulatedHost(
        name="h",
        mounts={"/mnt/data": {"device": "/dev/sdb1", "type": "ext4", "options": ["rw"]}},
    )
    ctx, commands = await materialize(
        host, op(server.mount, path="/data", device="/dev/sdb1", mounted=False)
    )
    assert ctx.changed
    assert ctx.reason == "filesystem /mnt/data is mounted, will unmount"
    assert commands == [MountCommand(path="/mnt/data", mounted=False)]


async def test_mount_no_change_when_already_mounted():
    # The baseline's mount_exists.json noop_description, verbatim.
    host = SimulatedHost(name="h", mounts={"/data": {}})
    ctx, commands = await materialize(host, op(server.mount, path="/data"))
    assert not ctx.changed
    assert ctx.reason == "filesystem /data is mounted"
    assert commands == []


async def test_mount_no_change_when_already_unmounted():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, op(server.mount, path="/data", mounted=False)
    )
    assert not ctx.changed
    assert ctx.reason == "filesystem /data is not mounted"
    assert commands == []


async def test_mount_no_change_when_options_already_present():
    # The options check is a subset test: extra options on the mount never
    # trigger a remount, and the noop wording stays the baseline's "is mounted".
    host = SimulatedHost(
        name="h", mounts={"/data": {"options": ["rw", "relatime", "noatime"]}}
    )
    ctx, commands = await materialize(
        host, op(server.mount, path="/data", options=("rw", "noatime"))
    )
    assert not ctx.changed
    assert ctx.reason == "filesystem /data is mounted"
    assert commands == []


async def test_mount_remount_reason_lists_missing_options_sorted():
    host = SimulatedHost(name="h", mounts={"/data": {"options": ["rw"]}})
    ctx, commands = await materialize(
        host, op(server.mount, path="/data", options=("rw", "noatime", "nodiratime"))
    )
    assert ctx.changed
    assert ctx.reason == (
        "filesystem /data is missing options noatime, nodiratime, will remount"
    )
    assert commands == [
        MountCommand(path="/data", options=("rw", "noatime", "nodiratime"), remount=True)
    ]


async def test_mount_absorbs_unknown_keyword_arguments():
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize(
        host, op(server.mount, path="/data", _extra_global=True)
    )
    assert commands == [MountCommand(path="/data")]


async def test_mount_commands_mutate_the_simulated_host():
    host = SimulatedHost(name="h")
    await send_command(
        host,
        MountCommand(path="/data", device="/dev/sdb1", fs_type="ext4", options=("rw",)),
    )
    assert host.mounts["/data"] == {
        "device": "/dev/sdb1",
        "type": "ext4",
        "options": ["rw"],
    }
    # A remount merges the requested options into the entry, keeping order.
    await send_command(
        host, MountCommand(path="/data", options=("rw", "noatime"), remount=True)
    )
    assert host.mounts["/data"]["options"] == ["rw", "noatime"]
    await send_command(host, MountCommand(path="/data", mounted=False))
    assert "/data" not in host.mounts
    # An absent mount point is tolerated on unmount, and a remount of one
    # gains a minimal entry (the seam stays safe regardless).
    await send_command(host, MountCommand(path="/nosuch", mounted=False))
    await send_command(host, MountCommand(path="/nosuch", options=("rw",), remount=True))
    assert host.mounts["/nosuch"]["options"] == ["rw"]


def ensure_mounts(host: Host) -> Generator[OperationRequest]:
    """The example deploy: mount /data, then unmount the scratch filesystem."""
    yield op(
        server.mount,
        name="Mount data filesystem",
        path="/data",
        device="/dev/sdb1",
        fs_type="ext4",
        options=("rw",),
        sudo=True,
    )
    yield op(
        server.mount,
        name="Unmount scratch filesystem",
        path="/scratch",
        mounted=False,
        sudo=True,
    )


def scratch_only_factory(entry: dict) -> Host:
    """``host_factory`` building SimulatedHosts whose only mount is /scratch."""
    host = SimulatedHost.from_inventory(entry)
    host.mounts = {
        "/scratch": {"device": "/dev/sdb2", "type": "ext4", "options": ["rw"]}
    }
    return host


async def test_mount_runs_across_hosts_in_lockstep():
    plans, report, hosts = await run(
        INVENTORY, ensure_mounts, host_factory=scratch_only_factory
    )
    assert format_plan(plans).splitlines() == [
        "web1: Mount data filesystem    [changed: filesystem /data is not mounted, will mount]",
        "web1: Unmount scratch filesystem [changed: filesystem /scratch is mounted, will unmount]",
        "web2: Mount data filesystem    [changed: filesystem /data is not mounted, will mount]",
        "web2: Unmount scratch filesystem [changed: filesystem /scratch is mounted, will unmount]",
    ]
    assert report is not None
    # Lockstep: every host finishes the mount step before either unmounts,
    # and the elevated shells land in the log.
    assert [e.name for e in report.log] == [
        "Mount data filesystem",
        "Mount data filesystem",
        "Unmount scratch filesystem",
        "Unmount scratch filesystem",
    ]
    log = format_log(report)
    assert "$ sudo mount -t ext4 -o rw /dev/sdb1 /data" in log
    assert "$ sudo umount /scratch" in log
    for host in hosts:
        assert set(host.mounts) == {"/data"}
        assert host.mounts["/data"]["device"] == "/dev/sdb1"


async def test_mount_second_prepare_plans_no_change():
    host = SimulatedHost(
        name="h",
        mounts={"/scratch": {"device": "/dev/sdb2", "type": "ext4", "options": ["rw"]}},
    )
    runtimes, plans = await run_prepare([host], ensure_mounts)
    assert [p.changed for p in plans["h"]] == [True, True]
    report = await run_apply(runtimes, plans=plans)
    assert [e.changed for e in report.log] == [True, True]
    assert set(host.mounts) == {"/data"}
    # The apply mutated the seam, so a fresh prepare sees /data mounted and
    # /scratch gone -- the operation is idempotent across runs.
    runtimes, plans = await run_prepare([host], ensure_mounts)
    assert [p.changed for p in plans["h"]] == [False, False]
    assert [p.reason for p in plans["h"]] == [
        "filesystem /data is mounted",
        "filesystem /scratch is not mounted",
    ]


async def test_mount_then_remount_sees_the_fresh_per_step_cache():
    def mount_then_tighten(host: Host) -> Generator[OperationRequest]:
        yield op(
            server.mount,
            name="Mount data filesystem",
            path="/data",
            device="/dev/sdb1",
            fs_type="ext4",
            options=("rw",),
            sudo=True,
        )
        yield op(
            server.mount,
            name="Tighten data options",
            path="/data",
            options=("rw", "noatime"),
            sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], mount_then_tighten)
    # The prepare cache persists across a host's requests and prepare never
    # sends, so the second request also plans a fresh mount against the
    # pre-deployment state.
    assert [p.changed for p in plans["h"]] == [True, True]
    report = await run_apply(runtimes, plans=plans)
    log = format_log(report)
    assert "$ sudo mount -t ext4 -o rw /dev/sdb1 /data" in log
    # Apply's per-step cache sees the first mount, so the second request
    # remounts with the missing option instead of mounting again.
    assert "$ sudo mount -o remount,rw,noatime /data" in log
    assert host.mounts["/data"]["options"] == ["rw", "noatime"]


async def test_mount_duplicate_requests_no_op_only_in_apply():
    def twice(host: Host) -> Generator[OperationRequest]:
        yield op(
            server.mount, name="Mount data filesystem", path="/data", sudo=True
        )
        yield op(
            server.mount, name="Mount data filesystem again", path="/data", sudo=True
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], twice)
    # The prepare cache persists across a host's requests and prepare never
    # sends, so both identical requests plan a change...
    assert [p.changed for p in plans["h"]] == [True, True]
    report = await run_apply(runtimes, plans=plans)
    # ...while the per-step cache recreation makes the second a no-op.
    assert [e.changed for e in report.log] == [True, False]
    assert set(host.mounts) == {"/data"}


async def test_mount_dry_run_sends_nothing():
    _plans, report, hosts = await run(
        INVENTORY, ensure_mounts, host_factory=scratch_only_factory, dry=True
    )
    assert report is None
    for host in hosts:
        assert set(host.mounts) == {"/scratch"}


async def test_mount_branching_on_live_state_raises_determinism_error():
    def live_branch(host: Host) -> Generator[OperationRequest]:
        yield op(
            server.mount,
            name="Mount data filesystem",
            path="/data",
            device="/dev/sdb1",
            sudo=True,
        )
        # The illegal branch: consult live state the mount mutates. Prepare
        # sees /data absent and plans one request; apply's second pull sees
        # it mounted and yields a second -- divergence.
        if "/data" in host.mounts:
            yield op(
                server.mount,
                name="Unmount data filesystem",
                path="/data",
                mounted=False,
                sudo=True,
            )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], live_branch)
    assert [p.request.name for p in plans["h"]] == ["Mount data filesystem"]
    with pytest.raises(DeterminismError):
        await run_apply(runtimes, plans=plans)


# --- timezone operation -------------------------------------------------------


def test_set_timezone_command_shell_rendering():
    assert (
        SetTimezoneCommand(timezone="Europe/Amsterdam").shell()
        == "timedatectl set-timezone Europe/Amsterdam"
    )
    # The baseline's QuoteString: a name carrying shell metacharacters is quoted
    # (the argument of the baseline's own set_quotes_injection.json fixture).
    assert (
        SetTimezoneCommand(timezone="x; reboot").shell()
        == "timedatectl set-timezone 'x; reboot'"
    )
    assert command_shell(SetTimezoneCommand(timezone="UTC"), sudo=True) == (
        "sudo timedatectl set-timezone UTC"
    )
    assert command_shell(SetTimezoneCommand(timezone="UTC"), su=True) == (
        "su -c 'timedatectl set-timezone UTC'"
    )


@pytest.mark.parametrize(
    ("zone", "current", "timedatectl", "expected"),
    [
        # The baseline's own operation fixtures (tests/operations/server.timezone/).
        # set_no_timedatectl.json and set_quotes_injection.json exercise the
        # deliberately dropped non-systemd fallback (ln -sf /etc/localtime
        # plus echo > /etc/timezone); a host without timedatectl now raises
        # OperationError instead (test_timezone_raises_without_timedatectl).
        pytest.param(
            "Europe/Amsterdam", "Europe/Amsterdam", None, [],
            id="noop",
        ),
        pytest.param(
            "Europe/Amsterdam", "UTC", "/usr/bin/timedatectl",
            ["timedatectl set-timezone Europe/Amsterdam"],
            id="set_timedatectl",
        ),
    ],
)
async def test_timezone_matches_baseline_operation_fixtures(
    zone: str, current: str, timedatectl: str | None, expected: list[str]
):
    host = SimulatedHost(name="h", os_timezone=current)
    if timedatectl is not None:
        host.command_paths = {"timedatectl": timedatectl}
    ctx, commands = await materialize(host, op(server.timezone, timezone=zone))
    assert ctx.changed == bool(expected)
    assert [cmd.shell() for cmd in commands] == expected
    if not expected:
        # The baseline's noop_description, verbatim.
        assert ctx.reason == "timezone is set"


async def test_timezone_sets_via_timedatectl():
    host = SimulatedHost(
        name="h",
        os_timezone="UTC",
        command_paths={"timedatectl": "/usr/bin/timedatectl"},
    )
    ctx, commands = await materialize(
        host, op(server.timezone, timezone="Europe/Amsterdam")
    )
    assert ctx.changed
    assert ctx.reason == "timezone is 'UTC', will set 'Europe/Amsterdam'"
    assert commands == [SetTimezoneCommand(timezone="Europe/Amsterdam")]


async def test_timezone_raises_without_timedatectl():
    # No timedatectl resolvable (the default empty command_paths): the
    # dropped fallback is an OperationError, not a symlink/edit command.
    host = SimulatedHost(name="h", os_timezone="UTC")
    with pytest.raises(OperationError) as excinfo:
        await materialize(host, op(server.timezone, timezone="Europe/Amsterdam"))
    assert str(excinfo.value) == (
        "server.timezone requires timedatectl (systemd only), not found on h"
    )
    assert isinstance(excinfo.value, RuntimeError)


async def test_timezone_no_change_when_already_set():
    # Like the baseline, the fact comparison comes first: an already-set zone is
    # a no-op even on a host without timedatectl (no probe, no raise).
    for paths in ({}, {"timedatectl": "/usr/bin/timedatectl"}):
        host = SimulatedHost(
            name="h", os_timezone="Europe/Amsterdam", command_paths=paths
        )
        ctx, commands = await materialize(
            host, op(server.timezone, timezone="Europe/Amsterdam")
        )
        assert not ctx.changed
        assert ctx.reason == "timezone is set"
        assert commands == []


async def test_timezone_absorbs_unknown_keyword_arguments():
    host = SimulatedHost(
        name="h",
        os_timezone="UTC",
        command_paths={"timedatectl": "/usr/bin/timedatectl"},
    )
    _ctx, commands = await materialize(
        host, op(server.timezone, timezone="Europe/Amsterdam", _extra_global=True)
    )
    assert commands == [SetTimezoneCommand(timezone="Europe/Amsterdam")]


async def test_timezone_command_mutates_the_simulated_host():
    host = SimulatedHost(name="h")
    await send_command(host, SetTimezoneCommand(timezone="UTC"))
    assert host.os_timezone == "UTC"
    await send_command(host, SetTimezoneCommand(timezone="Europe/Amsterdam"))
    assert host.os_timezone == "Europe/Amsterdam"


def set_timezones(host: Host) -> Generator[OperationRequest]:
    """The example deploy: every host keeps Europe/Amsterdam."""
    yield op(
        server.timezone,
        name="Set timezone",
        timezone="Europe/Amsterdam",
        sudo=True,
    )


def utc_timezone_factory(entry: dict) -> Host:
    """``host_factory`` building SimulatedHosts on UTC with timedatectl."""
    host = SimulatedHost.from_inventory(entry)
    host.os_timezone = "UTC"
    host.command_paths = {"timedatectl": "/usr/bin/timedatectl"}
    return host


async def test_timezone_runs_across_hosts_in_lockstep():
    plans, report, hosts = await run(
        INVENTORY, set_timezones, host_factory=utc_timezone_factory
    )
    assert format_plan(plans).splitlines() == [
        (
            "web1: Set timezone             [changed: timezone is 'UTC', "
            "will set 'Europe/Amsterdam']"
        ),
        (
            "web2: Set timezone             [changed: timezone is 'UTC', "
            "will set 'Europe/Amsterdam']"
        ),
    ]
    assert report is not None
    assert [e.name for e in report.log] == ["Set timezone", "Set timezone"]
    log = format_log(report)
    # The elevated shell lands in the log, one per host.
    assert "$ sudo timedatectl set-timezone Europe/Amsterdam" in log
    for host in hosts:
        assert host.os_timezone == "Europe/Amsterdam"


async def test_timezone_without_timedatectl_aborts_the_prepare():
    # The Which probe raises while materializing, which in a deploy run is
    # the prepare phase -- before anything is sent (fail-fast, no fallback).
    host = SimulatedHost(name="h", os_timezone="UTC")
    with pytest.raises(OperationError, match="not found on h"):
        await run_prepare([host], set_timezones)
    assert host.os_timezone == "UTC"


async def test_timezone_second_prepare_plans_no_change():
    host = utc_timezone_factory({"host": "h", "connection": {}})
    runtimes, plans = await run_prepare([host], set_timezones)
    assert [p.changed for p in plans["h"]] == [True]
    report = await run_apply(runtimes, plans=plans)
    assert [e.changed for e in report.log] == [True]
    assert host.os_timezone == "Europe/Amsterdam"
    # The apply mutated the seam, so a fresh prepare sees the new zone and
    # plans no change -- the operation is idempotent across runs.
    runtimes, plans = await run_prepare([host], set_timezones)
    assert [p.changed for p in plans["h"]] == [False]
    assert [p.reason for p in plans["h"]] == ["timezone is set"]


async def test_timezone_duplicate_requests_no_op_only_in_apply():
    def twice(host: Host) -> Generator[OperationRequest]:
        yield op(
            server.timezone, name="Set timezone", timezone="Europe/Amsterdam",
            sudo=True,
        )
        yield op(
            server.timezone, name="Set timezone again", timezone="Europe/Amsterdam",
            sudo=True,
        )

    host = SimulatedHost(
        name="h",
        os_timezone="UTC",
        command_paths={"timedatectl": "/usr/bin/timedatectl"},
    )
    runtimes, plans = await run_prepare([host], twice)
    # The prepare cache persists across a host's requests and prepare never
    # sends, so both identical requests plan a change...
    assert [p.changed for p in plans["h"]] == [True, True]
    report = await run_apply(runtimes, plans=plans)
    # ...while the per-step cache recreation makes the second a no-op.
    assert [e.changed for e in report.log] == [True, False]
    assert host.os_timezone == "Europe/Amsterdam"


async def test_timezone_dry_run_sends_nothing():
    _plans, report, hosts = await run(
        INVENTORY, set_timezones, host_factory=utc_timezone_factory, dry=True
    )
    assert report is None
    for host in hosts:
        assert host.os_timezone == "UTC"


# --- WaitCommand / WaitForFactCommand ----------------------------------------


class FlippingHost(Host):
    """A host whose simulated unit becomes active after ``flip_after`` reads.

    Drives the real poll loop without a real transport: ``SystemdServiceState``
    reads the ``services`` seam, and each read is counted so the test can
    assert how many polls the loop made.
    """

    def __init__(self, flip_after: int):
        super().__init__(name="flipping")
        self.flip_after = flip_after
        self.reads = 0

    @property
    def services(self) -> dict[str, str]:
        self.reads += 1
        state = "active" if self.reads >= self.flip_after else "activating"
        return {"httpd.service": state}


def wait_cmd(timeout: float = 0.0, interval: float = 0.0) -> WaitForFactCommand:
    return WaitForFactCommand(
        fact_cls=SystemdServiceState,
        args=("httpd.service",),
        predicate=is_active,
        timeout=timeout,
        interval=interval,
    )


async def test_wait_command_base_is_abstract():
    with pytest.raises(NotImplementedError, match="wait()"):
        await WaitCommand().wait(Host(name="h"))


def test_wait_for_fact_command_renders_without_shell():
    cmd = wait_cmd(timeout=60.0, interval=2.0)
    assert not hasattr(cmd, "shell")
    assert (
        command_shell(cmd)
        == "wait for SystemdServiceState('httpd.service') <is_active> every 2.0s up to 60.0s"
    )


def test_wait_for_fact_command_is_hashable_and_value_equal():
    assert wait_cmd() == wait_cmd()
    assert hash(wait_cmd()) == hash(wait_cmd())


async def test_wait_returns_on_the_first_poll_when_already_met():
    host = FlippingHost(flip_after=1)
    assert await wait_cmd().wait(host) == "condition met after 1 poll(s)"
    assert host.reads == 1


async def test_wait_polls_until_the_condition_is_met():
    # A generous deadline with a zero interval: the loop yields to the event
    # loop between polls but never actually sleeps.
    host = FlippingHost(flip_after=3)
    assert await wait_cmd(timeout=60.0).wait(host) == "condition met after 3 poll(s)"
    assert host.reads == 3


async def test_wait_times_out_with_the_last_value():
    # A zero timeout still polls once (an already-met condition would pass),
    # then the deadline has passed and the loop raises instead of sleeping.
    host = FlippingHost(flip_after=99)
    with pytest.raises(WaitTimeoutError, match=r"not met within 0.0s.*'activating'"):
        await wait_cmd().wait(host)
    assert host.reads == 1


async def test_wait_bypasses_the_fact_cache():
    # Reading through a FactCache would pin the first value; the poll loop
    # must observe the flip even though a cache holding 'activating' exists.
    host = FlippingHost(flip_after=2)
    cache = FactCache(host, phase="apply")
    assert await cache.get(SystemdServiceState, "httpd.service") == "activating"
    await wait_cmd(timeout=60.0).wait(host)
    assert await cache.get(SystemdServiceState, "httpd.service") == "activating"


@dataclass(frozen=True)
class RecordingWait(WaitCommand):
    """A WaitCommand recording the host ``send_command`` hands it."""

    calls: list = None  # type: ignore[assignment]

    async def wait(self, host: Host) -> str:
        self.calls.append(host)
        return "recorded"


async def test_send_command_dispatches_wait_commands_on_the_real_path():
    # A plain Host has no execute_command seam, so send_command takes the
    # real-transport branch; a WaitCommand must reach ``wait`` and never a
    # shell, even when the request is flagged sudo/su.
    host = Host(name="real")
    calls: list = []
    await send_command(host, RecordingWait(calls=calls), sudo=True, su=True)
    assert calls == [host]


async def test_simulated_host_evaluates_a_wait_once():
    met = SimulatedHost(name="h", services={"httpd.service": "active"})
    await send_command(met, wait_cmd(timeout=60.0))

    unmet = SimulatedHost(name="h", services={"httpd.service": "inactive"})
    with pytest.raises(WaitTimeoutError, match="'inactive'"):
        await send_command(unmet, wait_cmd(timeout=60.0))


async def test_simulated_host_rejects_a_wait_on_an_unmodelled_fact():
    host = SimulatedHost(name="h")
    with pytest.raises(TypeError, match="unsupported simulated wait fact"):
        await send_command(
            host, WaitForFactCommand(fact_cls=SystemdServiceEnabled, args=("x",))
        )


# --- server.wait_service end to end ------------------------------------------


async def test_wait_service_always_yields_and_reads_no_facts():
    host = SimulatedHost(name="h", services={"httpd.service": "active"})
    cache = FactCache(host, phase="test")
    ctx = OpContext(host=host, facts=cache)
    request = op(server.wait_service, service="httpd", timeout=60.0, interval=2.0)
    commands = [cmd async for cmd in request.func(ctx, **request.kwargs)]
    assert ctx.changed
    assert ctx.reason == "wait for httpd.service to be active"
    assert commands == [wait_cmd(timeout=60.0, interval=2.0)]
    assert cache._data == {}


def start_and_wait(host: Host) -> Generator[OperationRequest]:
    """The example deploy: start httpd (elevated), then wait for it."""
    yield op(
        server.service,
        name="Start httpd",
        service="httpd",
        running=True,
        enabled=True,
        sudo=True,
    )
    yield op(
        server.wait_service,
        name="Wait for httpd to become active",
        service="httpd",
        timeout=60.0,
        interval=2.0,
    )


def stopped_factory(entry: dict) -> Host:
    """``host_factory`` building SimulatedHosts whose httpd is stopped."""
    host = SimulatedHost.from_inventory(entry)
    host.services = {"httpd.service": "inactive"}  # fresh dict per host
    return host


async def test_wait_service_runs_after_start_across_hosts():
    plans, report, hosts = await run(
        INVENTORY, start_and_wait, host_factory=stopped_factory
    )
    # The plan always shows the wait as a change, on every host.
    assert format_plan(plans).splitlines() == [
        "web1: Start httpd              [changed: httpd.service not running (inactive), will start; httpd.service not enabled, will enable]",
        "web1: Wait for httpd to become active [changed: wait for httpd.service to be active]",
        "web2: Start httpd              [changed: httpd.service not running (inactive), will start; httpd.service not enabled, will enable]",
        "web2: Wait for httpd to become active [changed: wait for httpd.service to be active]",
    ]
    # The apply phase started the unit on both hosts, then the wait passed.
    assert report is not None
    for host in hosts:
        assert host.services == {"httpd.service": "active"}
    log = format_log(report)
    assert "$ sudo systemctl start httpd.service" in log
    assert (
        "$ wait for SystemdServiceState('httpd.service') <is_active> "
        "every 2.0s up to 60.0s"
    ) in log
    assert [e.name for e in report.log] == [
        "Start httpd",
        "Start httpd",
        "Wait for httpd to become active",
        "Wait for httpd to become active",
    ]


async def test_wait_service_is_deterministic_across_phases():
    # The wait is planned unconditionally, so even when the unit is already
    # active (and the service op is a no-op) the apply-phase request sequence
    # matches the plan and the determinism guard stays quiet.
    host = SimulatedHost(
        name="h",
        services={"httpd.service": "active"},
        enabled_services={"httpd.service"},
    )
    runtimes, plans = await run_prepare([host], start_and_wait)
    assert [p.changed for p in plans["h"]] == [False, True]
    report = await run_apply(runtimes, plans=plans)
    assert [e.changed for e in report.log] == [False, True]


async def test_wait_service_dry_run_sends_nothing():
    _plans, report, hosts = await run(
        INVENTORY, start_and_wait, host_factory=stopped_factory, dry=True
    )
    assert report is None
    for host in hosts:
        assert host.services == {"httpd.service": "inactive"}


async def test_wait_service_times_out_and_aborts_the_run():
    def wait_only(host: Host) -> Generator[OperationRequest]:
        yield op(server.wait_service, name="Wait", service="httpd", timeout=1.0)

    with pytest.raises(WaitTimeoutError, match=r"host web1: .*'inactive'"):
        await run(INVENTORY, wait_only, host_factory=stopped_factory)


# --- server.wait (port) -------------------------------------------------------

# The seam record every port-wait test seeds: an owned listener on :8080.
_LISTENING_8080 = PortInfo(listening=True, process="nginx", pid=4242)


class FlippingPortHost(Host):
    """A host whose simulated port opens after ``flip_after`` reads.

    Drives the real poll loop over the ``Port`` fact without a transport:
    ``Port`` reads the ``listening_ports`` seam, and each read is counted so
    the test can assert how many polls the loop made.
    """

    def __init__(self, flip_after: int):
        super().__init__(name="flipping-port")
        self.flip_after = flip_after
        self.reads = 0

    @property
    def listening_ports(self) -> dict[tuple[str, int], PortInfo]:
        self.reads += 1
        if self.reads < self.flip_after:
            return {}
        return {("tcp", 8080): _LISTENING_8080}


def port_wait_cmd(
    port: int = 8080,
    protocol: str = "tcp",
    timeout: float = 0.0,
    interval: float = 0.0,
) -> WaitForFactCommand:
    return WaitForFactCommand(
        fact_cls=Port,
        args=(port, protocol),
        predicate=is_listening,
        timeout=timeout,
        interval=interval,
    )


def test_port_wait_descriptor_renders_without_shell():
    cmd = port_wait_cmd(protocol="udp", timeout=30.0, interval=0.5)
    assert not hasattr(cmd, "shell")
    assert (
        command_shell(cmd)
        == "wait for Port(8080, 'udp') <is_listening> every 0.5s up to 30.0s"
    )


def test_port_wait_descriptor_is_hashable_and_value_equal():
    assert port_wait_cmd() == port_wait_cmd()
    assert hash(port_wait_cmd()) == hash(port_wait_cmd())


async def test_port_wait_polls_until_the_port_opens():
    # A generous deadline with a zero interval: the loop yields to the event
    # loop between polls but never actually sleeps.
    host = FlippingPortHost(flip_after=3)
    assert await port_wait_cmd(timeout=60.0).wait(host) == "condition met after 3 poll(s)"
    assert host.reads == 3


async def test_port_wait_times_out_with_the_last_record():
    # The deadline is checked after the first poll, so the message carries the
    # record that failed -- listening False, owner and all.
    host = FlippingPortHost(flip_after=99)
    with pytest.raises(WaitTimeoutError, match=r"not met within 0.0s.*'listening': False"):
        await port_wait_cmd().wait(host)
    assert host.reads == 1


async def test_simulated_host_evaluates_a_port_wait_once():
    met = SimulatedHost(name="h", listening_ports={("tcp", 8080): _LISTENING_8080})
    await send_command(met, port_wait_cmd(timeout=60.0))

    unmet = SimulatedHost(name="h")
    with pytest.raises(WaitTimeoutError, match="'listening': False"):
        await send_command(unmet, port_wait_cmd(timeout=60.0))


async def test_wait_always_yields_and_reads_no_facts():
    host = SimulatedHost(name="h", listening_ports={("tcp", 8080): _LISTENING_8080})
    cache = FactCache(host, phase="test")
    ctx = OpContext(host=host, facts=cache)
    request = op(server.wait, port=8080, timeout=30.0, interval=0.5)
    commands = [cmd async for cmd in request.func(ctx, **request.kwargs)]
    assert ctx.changed
    assert ctx.reason == "wait for tcp port 8080 to be listening"
    assert commands == [port_wait_cmd(timeout=30.0, interval=0.5)]
    assert cache._data == {}


async def test_wait_defaults_to_tcp_and_a_one_second_interval():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="test"))
    request = op(server.wait, port=8080)
    (cmd,) = [cmd async for cmd in request.func(ctx, **request.kwargs)]
    assert cmd.args == (8080, "tcp")
    assert cmd.predicate is is_listening
    assert cmd.timeout == 300.0
    assert cmd.interval == 1.0


async def test_wait_rejects_a_bad_port_or_protocol_at_plan_time():
    # The check is in the generator body, so the deploy fails on the request
    # rather than deep inside the driver's poll loop.
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="test"))
    with pytest.raises(TypeError, match="port must be an int"):
        async for _ in server.wait(ctx, port="80 || 1"):
            pass
    with pytest.raises(ValueError, match="protocol must be one of"):
        async for _ in server.wait(ctx, port=8080, protocol="sctp"):
            pass


async def test_wait_absorbs_unknown_keyword_arguments():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="test"))
    request = op(server.wait, port=8080, ignore_errors=True)
    commands = [cmd async for cmd in request.func(ctx, **request.kwargs)]
    assert commands == [port_wait_cmd(timeout=300.0, interval=1.0)]


def wait_for_port(host: Host) -> Generator[OperationRequest]:
    """The example deploy: wait for the service port to start listening."""
    yield op(server.wait, name="Wait for :8080", port=8080, timeout=5.0, interval=1.0)


def listening_factory(entry: dict) -> Host:
    """``host_factory`` building SimulatedHosts whose :8080 is listening."""
    host = SimulatedHost.from_inventory(entry)
    host.listening_ports = {("tcp", 8080): _LISTENING_8080}  # fresh dict per host
    return host


def closed_factory(entry: dict) -> Host:
    """``host_factory`` building SimulatedHosts with nothing listening."""
    host = SimulatedHost.from_inventory(entry)
    host.listening_ports = {}  # fresh dict per host
    return host


async def test_wait_runs_across_hosts_in_lockstep():
    plans, report, _hosts = await run(
        INVENTORY, wait_for_port, host_factory=listening_factory
    )
    # The wait is always a change, on every host, and it passed on both.
    assert [p.changed for p in plans["web1"]] == [True]
    assert [p.changed for p in plans["web2"]] == [True]
    assert report is not None
    assert [e.name for e in report.log] == ["Wait for :8080", "Wait for :8080"]
    assert (
        "$ wait for Port(8080, 'tcp') <is_listening> every 1.0s up to 5.0s"
        in format_log(report)
    )


async def test_wait_is_deterministic_across_phases():
    # The wait is planned unconditionally, so even with the port already
    # listening the apply-phase request sequence matches the plan and the
    # determinism guard stays quiet.
    host = SimulatedHost(name="h", listening_ports={("tcp", 8080): _LISTENING_8080})
    runtimes, plans = await run_prepare([host], wait_for_port)
    assert [p.changed for p in plans["h"]] == [True]
    report = await run_apply(runtimes, plans=plans)
    assert [e.changed for e in report.log] == [True]


async def test_wait_never_converges():
    # Unlike a state operation, a wait plans as a change every time: the port
    # is discovered at apply time, so a second prepare is no less a change.
    hosts = [SimulatedHost(name="h", listening_ports={("tcp", 8080): _LISTENING_8080})]
    for _ in range(2):
        _runtimes, plans = await run_prepare(hosts, wait_for_port)
        assert [p.changed for p in plans["h"]] == [True]


async def test_wait_dry_run_sends_nothing():
    # These hosts' port is closed, so a clean dry run proves the plan never
    # evaluates the predicate -- a real run below aborts on it.
    _plans, report, hosts = await run(
        INVENTORY, wait_for_port, host_factory=closed_factory, dry=True
    )
    assert report is None
    for host in hosts:
        assert host.listening_ports == {}


async def test_wait_times_out_and_aborts_the_run():
    with pytest.raises(WaitTimeoutError, match=r"host web1: .*'listening': False"):
        await run(INVENTORY, wait_for_port, host_factory=closed_factory)


# --- server.user_authorized_keys --------------------------------------------

# The baseline's own operation fixtures (tests/operations/server.user_authorized_
# keys/) use these two keys; reused verbatim here for parity.
UA_KEY_A = "ssh-ed25519 AAAAkey1 alice"
UA_KEY_B = "ssh-rsa AAAAkey2 bob"
UA_USER = "someuser"
UA_HOME = "/home/someuser"
UA_SSH_DIR = "/home/someuser/.ssh"
UA_KEYS_FILE = "/home/someuser/.ssh/authorized_keys"
# The dest-derived spool and the install inner script -- the text inside the
# ``sh -c '...'`` wrapper the install descriptor emits.
UA_SPOOL = _keys_spool_path(UA_KEYS_FILE)
UA_SPOOL_DIR = UA_SPOOL.rsplit("/", 1)[0]
UA_INSTALL_INNER = (
    f"mkdir -p {UA_SSH_DIR} && cp {UA_SPOOL} {UA_KEYS_FILE} && "
    f"chmod 600 {UA_KEYS_FILE} && rm -rf {UA_SPOOL_DIR}"
)


def test_keys_chmod_touch_shell_rendering():
    # Fixed-mode chmod and the plain touch mirror the baseline's own chmod/touch
    # helper shape: unquoted mode digit string, shell-quoted path.
    assert _KeysChmodCommand(path=UA_SSH_DIR, mode=700).shell() == f"chmod 700 {UA_SSH_DIR}"
    assert _KeysChmodCommand(path="/home/evil $(id)", mode=600).shell() == (
        "chmod 600 '/home/evil $(id)'"
    )
    assert _KeysTouchCommand(path=UA_KEYS_FILE).shell() == f"touch {UA_KEYS_FILE}"
    # Elevation prepends sudo to the whole shell (unprivileged touch/chmod
    # would otherwise fail on another user's ~/.ssh).
    assert command_shell(_KeysChmodCommand(path=UA_SSH_DIR, mode=700), sudo=True) == (
        f"sudo chmod 700 {UA_SSH_DIR}"
    )
    assert command_shell(_KeysTouchCommand(path=UA_KEYS_FILE), sudo=True) == (
        f"sudo touch {UA_KEYS_FILE}"
    )


def test_authorized_keys_upload_command_rendering():
    upload = AuthorizedKeysUploadCommand(content=f"{UA_KEY_A}\n", dest=UA_SPOOL)
    # The transfer is a non-shell descriptor: its loggable form is __str__,
    # and command_shell never prefixes it with sudo/su -- transfers ride the
    # login user's scp channel regardless of the request's elevation.
    assert str(upload) == f"scp <authorized keys> -> {UA_SPOOL}"
    assert command_shell(upload, sudo=True) == f"scp <authorized keys> -> {UA_SPOOL}"
    assert command_shell(upload, su=True) == f"scp <authorized keys> -> {UA_SPOOL}"
    # The content field is a plain str: hashable and value-equal across the
    # prepare/apply phases (the determinism guard compares descriptors).
    assert hash(upload) == hash(
        AuthorizedKeysUploadCommand(content=f"{UA_KEY_A}\n", dest=UA_SPOOL)
    )


def test_authorized_keys_install_command_shell_rendering():
    install = AuthorizedKeysInstallCommand(spool=UA_SPOOL, dest=UA_KEYS_FILE)
    assert install.shell() == f"sh -c {shlex.quote(UA_INSTALL_INNER)}"
    # Sudo elevates the whole sh -c, so every step of the && chain -- cp,
    # chmod and the spool cleanup -- runs as the elevated shell.
    assert command_shell(install, sudo=True) == f"sudo {install.shell()}"
    assert command_shell(install, su=True) == f"su -c {shlex.quote(install.shell())}"
    # Quoting proof: a space in the DIRECTORY (shlex.quote leaves space-free
    # paths bare). Recover the inner script with shlex.split, never a raw
    # substring of the quoted text.
    path = "/home/some user/.s sh/authorized_keys"
    spool = _keys_spool_path(path)
    spool_dir = spool.rsplit("/", 1)[0]
    inner = shlex.split(
        AuthorizedKeysInstallCommand(spool=spool, dest=path).shell()
    )[2]
    assert inner.startswith("mkdir -p '/home/some user/.s sh' && ")
    assert inner.endswith(f"&& rm -rf {spool_dir}")


def test_keys_spool_path_is_dest_derived_and_deterministic():
    # Same dest -> same spool in both phases; different dest -> different
    # spool, so two requests never clobber one another's upload.
    assert _keys_spool_path(UA_KEYS_FILE) == UA_SPOOL
    assert _keys_spool_path(UA_KEYS_FILE) == _keys_spool_path(UA_KEYS_FILE)
    assert _keys_spool_path("/etc/ssh/keys/alice") != UA_SPOOL
    digest = hashlib.sha1(UA_KEYS_FILE.encode()).hexdigest()[:16]
    assert UA_SPOOL == f"/tmp/reemote-server-user-authorized-keys-{digest}/authorized_keys"


def test_authorized_keys_file_command_quotes_the_path():
    assert AuthorizedKeysFile.command(UA_KEYS_FILE) == (
        f"test -e {UA_KEYS_FILE} && "
        f"{{ printf '#reemote-authorizedkeysfile\\n'; cat -- {UA_KEYS_FILE}; }}"
    )
    quoted = shlex.quote("/home/some user/.ssh/authorized keys")
    assert AuthorizedKeysFile.command("/home/some user/.ssh/authorized keys") == (
        f"test -e {quoted} && "
        f"{{ printf '#reemote-authorizedkeysfile\\n'; cat -- {quoted}; }}"
    )


async def test_authorized_keys_file_reads_the_simulated_seam():
    host = SimulatedHost(
        name="h",
        remote_files={
            UA_KEYS_FILE: f"# comment\n{UA_KEY_A}\n".encode(),
            "/etc/ssh/keys/empty": b"",
        },
    )
    # Raw text, comments intact -- never the parsed key list.
    assert (
        await AuthorizedKeysFile.process(host, UA_KEYS_FILE)
        == f"# comment\n{UA_KEY_A}\n"
    )
    # Absent path -> None; present but empty -> "" (never collapsed).
    assert await AuthorizedKeysFile.process(host, "/etc/ssh/keys/empty") == ""
    assert await AuthorizedKeysFile.process(host, "/no/such/file") is None


async def test_authorized_keys_file_parses_the_marker_over_real_transport():
    host = CannedShellHost(f"#reemote-authorizedkeysfile\n{UA_KEY_A}\n")
    assert await AuthorizedKeysFile.process(host, UA_KEYS_FILE) == f"{UA_KEY_A}\n"
    assert host.shells == [AuthorizedKeysFile.command(UA_KEYS_FILE)]
    # Marker only: an existing empty file reads "", not None.
    assert (
        await AuthorizedKeysFile.process(
            CannedShellHost("#reemote-authorizedkeysfile\n"), UA_KEYS_FILE
        )
        == ""
    )
    # No marker (the test -e guard failed): a missing file reads None.
    assert (
        await AuthorizedKeysFile.process(CannedShellHost(""), UA_KEYS_FILE) is None
    )


def test_add_keys_transform_purity_and_fixpoint():
    # Absent file becomes just the keys; a repeat application is a fixpoint.
    once = _add_keys(None, (UA_KEY_A, UA_KEY_B))
    assert once == f"{UA_KEY_A}\n{UA_KEY_B}\n"
    assert _add_keys(once, (UA_KEY_A, UA_KEY_B)) == once
    # Comments, blank lines, ordering and the trailing newline survive.
    content = f"# managed\n\n{UA_KEY_A}\n"
    assert _add_keys(content, (UA_KEY_B,)) == f"{content}{UA_KEY_B}\n"
    # A newline-less last line gets its separator functionally (the old
    # tail -c1 shell guard, now controller-side).
    assert _add_keys("trailing", (UA_KEY_A,)) == f"trailing\n{UA_KEY_A}\n"
    # Presence is the parser's stripped whole-line comparison: surrounding
    # whitespace counts as present, a commented-out key does NOT.
    assert _add_keys(f"  {UA_KEY_A}  \n", (UA_KEY_A,)) == f"  {UA_KEY_A}  \n"
    assert _add_keys(f"# {UA_KEY_A}\n", (UA_KEY_A,)) == (
        f"# {UA_KEY_A}\n{UA_KEY_A}\n"
    )
    # Repeated requested keys land once, in the caller's own order.
    assert _add_keys("", (UA_KEY_B, UA_KEY_A, UA_KEY_B)) == (
        f"{UA_KEY_B}\n{UA_KEY_A}\n"
    )
    # No keys to add: the content comes back verbatim ("" for None).
    assert _add_keys(content, ()) == content
    assert _add_keys(None, ()) == ""


def test_rewrite_keys_transform_is_baseline_whole_file_semantics():
    # Exactly the requested keys, caller order, trailing newline -- current
    # text is not an input, everything else is discarded.
    assert _rewrite_keys((UA_KEY_B, UA_KEY_A)) == f"{UA_KEY_B}\n{UA_KEY_A}\n"
    # The baseline's "{}\n".format quirk: an empty key list writes a lone newline.
    assert _rewrite_keys(()) == "\n"


@pytest.mark.parametrize(
    ("seed", "kwargs", "expected"),
    [
        # The baseline's all_keys_present.json: a fully-correct directory/file
        # pair plus every requested key already listed -> no commands.
        (
            {
                "directory_stats": {UA_SSH_DIR: {"user": UA_USER, "group": UA_USER, "mode": 700}},
                "file_stats": {UA_KEYS_FILE: {"user": UA_USER, "group": UA_USER, "mode": 600}},
                "authorized_keys": {UA_KEYS_FILE: [UA_KEY_A, UA_KEY_B]},
                "remote_files": {UA_KEYS_FILE: f"{UA_KEY_A}\n{UA_KEY_B}\n".encode()},
            },
            {"public_keys": (UA_KEY_A, UA_KEY_B)},
            [],
        ),
        # The baseline's append_missing_only.json: one key already listed, the
        # other absent -> one spool-and-install pair carrying the whole new
        # text (the append is now a whole-content write).
        (
            {
                "directory_stats": {UA_SSH_DIR: {"user": UA_USER, "group": UA_USER, "mode": 700}},
                "file_stats": {UA_KEYS_FILE: {"user": UA_USER, "group": UA_USER, "mode": 600}},
                "authorized_keys": {UA_KEYS_FILE: [UA_KEY_A]},
                "remote_files": {UA_KEYS_FILE: f"{UA_KEY_A}\n".encode()},
            },
            {"public_keys": (UA_KEY_A, UA_KEY_B)},
            [
                AuthorizedKeysUploadCommand(
                    content=f"{UA_KEY_A}\n{UA_KEY_B}\n", dest=UA_SPOOL
                ),
                AuthorizedKeysInstallCommand(spool=UA_SPOOL, dest=UA_KEYS_FILE),
            ],
        ),
        # The baseline's long_inline_key_not_a_file.yaml: an inline key longer
        # than PATH_MAX still lands because reemote never treats
        # public_keys entries as controller-side paths (dropped branch).
        (
            {
                "directory_stats": {UA_SSH_DIR: {"user": UA_USER, "group": UA_USER, "mode": 700}},
                "file_stats": {UA_KEYS_FILE: {"user": UA_USER, "group": UA_USER, "mode": 600}},
                "authorized_keys": {UA_KEYS_FILE: []},
                "remote_files": {UA_KEYS_FILE: b""},
            },
            {"public_keys": ("ssh-rsa " + "A" * 2000 + " bob",)},
            [
                AuthorizedKeysUploadCommand(
                    content="ssh-rsa " + "A" * 2000 + " bob\n", dest=UA_SPOOL
                ),
                AuthorizedKeysInstallCommand(spool=UA_SPOOL, dest=UA_KEYS_FILE),
            ],
        ),
    ],
)
async def test_user_authorized_keys_matches_baseline_operation_fixtures(
    seed: dict, kwargs: dict, expected: list
):
    host = SimulatedHost(
        name="h", home_directories={UA_USER: UA_HOME}, **seed
    )
    ctx, commands = await materialize(
        host, op(server.user_authorized_keys, user=UA_USER, **kwargs)
    )
    assert [cmd for cmd in commands] == expected
    assert ctx.changed == bool(expected)


async def test_user_authorized_keys_creates_missing_dir_and_file_and_appends():
    # Fresh host with the user's home known: mkdir -p + chmod 700 + chown
    # for the .ssh directory, then touch + chmod 600 + chown for the key
    # file, then one spool-and-install pair carrying both keys in caller
    # order.
    host = SimulatedHost(name="h", home_directories={UA_USER: UA_HOME})
    ctx, commands = await materialize(
        host,
        op(
            server.user_authorized_keys,
            user=UA_USER,
            public_keys=(UA_KEY_A, UA_KEY_B),
        ),
    )
    assert ctx.changed
    assert ctx.reason == (
        f"{UA_SSH_DIR} missing, will create; {UA_KEYS_FILE} missing, "
        "will create; 2 key(s) missing, will append"
    )
    assert commands == [
        _HomeMkdirCommand(path=UA_SSH_DIR),
        _KeysChmodCommand(path=UA_SSH_DIR, mode=700),
        _HomeChownCommand(path=UA_SSH_DIR, user=UA_USER, group=UA_USER),
        _KeysTouchCommand(path=UA_KEYS_FILE),
        _KeysChmodCommand(path=UA_KEYS_FILE, mode=600),
        _HomeChownCommand(path=UA_KEYS_FILE, user=UA_USER, group=UA_USER),
        AuthorizedKeysUploadCommand(
            content=f"{UA_KEY_A}\n{UA_KEY_B}\n", dest=UA_SPOOL
        ),
        AuthorizedKeysInstallCommand(spool=UA_SPOOL, dest=UA_KEYS_FILE),
    ]


async def test_user_authorized_keys_noop_when_everything_matches():
    host = SimulatedHost(
        name="h",
        home_directories={UA_USER: UA_HOME},
        directory_stats={UA_SSH_DIR: {"user": UA_USER, "group": UA_USER, "mode": 700}},
        file_stats={UA_KEYS_FILE: {"user": UA_USER, "group": UA_USER, "mode": 600}},
        authorized_keys={UA_KEYS_FILE: [UA_KEY_A]},
    )
    ctx, commands = await materialize(
        host, op(server.user_authorized_keys, user=UA_USER, public_keys=UA_KEY_A)
    )
    assert not ctx.changed
    assert ctx.reason == (
        f"directory {UA_SSH_DIR} already exists; file {UA_KEYS_FILE} already exists"
    )
    assert commands == []


async def test_user_authorized_keys_fixes_directory_mode_only():
    host = SimulatedHost(
        name="h",
        home_directories={UA_USER: UA_HOME},
        directory_stats={UA_SSH_DIR: {"user": UA_USER, "group": UA_USER, "mode": 755}},
        file_stats={UA_KEYS_FILE: {"user": UA_USER, "group": UA_USER, "mode": 600}},
        authorized_keys={UA_KEYS_FILE: [UA_KEY_A]},
    )
    ctx, commands = await materialize(
        host, op(server.user_authorized_keys, user=UA_USER, public_keys=UA_KEY_A)
    )
    assert ctx.changed
    assert commands == [_KeysChmodCommand(path=UA_SSH_DIR, mode=700)]


async def test_user_authorized_keys_fixes_directory_owner_only():
    # A ``group`` option overrides the chown target (the baseline's ``group or
    # user`` shape): a mismatch against the requested group yields a chown
    # while the mode already matches.
    host = SimulatedHost(
        name="h",
        home_directories={UA_USER: UA_HOME},
        directory_stats={UA_SSH_DIR: {"user": UA_USER, "group": "root", "mode": 700}},
        file_stats={UA_KEYS_FILE: {"user": UA_USER, "group": "root", "mode": 600}},
        authorized_keys={UA_KEYS_FILE: [UA_KEY_A]},
    )
    ctx, commands = await materialize(
        host,
        op(
            server.user_authorized_keys,
            user=UA_USER,
            public_keys=UA_KEY_A,
            group="sshusers",
        ),
    )
    assert ctx.changed
    assert commands == [
        _HomeChownCommand(path=UA_SSH_DIR, user=UA_USER, group="sshusers"),
        _HomeChownCommand(path=UA_KEYS_FILE, user=UA_USER, group="sshusers"),
    ]


async def test_user_authorized_keys_fixes_file_mode_and_appends():
    host = SimulatedHost(
        name="h",
        home_directories={UA_USER: UA_HOME},
        directory_stats={UA_SSH_DIR: {"user": UA_USER, "group": UA_USER, "mode": 700}},
        file_stats={UA_KEYS_FILE: {"user": UA_USER, "group": UA_USER, "mode": 644}},
        authorized_keys={UA_KEYS_FILE: []},
        remote_files={UA_KEYS_FILE: b""},
    )
    ctx, commands = await materialize(
        host, op(server.user_authorized_keys, user=UA_USER, public_keys=(UA_KEY_A,))
    )
    assert ctx.changed
    assert commands == [
        _KeysChmodCommand(path=UA_KEYS_FILE, mode=600),
        AuthorizedKeysUploadCommand(content=f"{UA_KEY_A}\n", dest=UA_SPOOL),
        AuthorizedKeysInstallCommand(spool=UA_SPOOL, dest=UA_KEYS_FILE),
    ]


async def test_user_authorized_keys_raises_on_wrong_typed_directory():
    # A regular file where the .ssh directory must live: the baseline's
    # files.directory raises its OperationError with the same wording.
    host = SimulatedHost(
        name="h",
        home_directories={UA_USER: UA_HOME},
        directory_stats={UA_SSH_DIR: False},
    )
    with pytest.raises(OperationError, match="exists and is not a directory"):
        await materialize(
            host, op(server.user_authorized_keys, user=UA_USER, public_keys=UA_KEY_A)
        )


async def test_user_authorized_keys_raises_on_wrong_typed_file():
    host = SimulatedHost(
        name="h",
        home_directories={UA_USER: UA_HOME},
        directory_stats={UA_SSH_DIR: {"user": UA_USER, "group": UA_USER, "mode": 700}},
        file_stats={UA_KEYS_FILE: False},
    )
    with pytest.raises(OperationError, match="exists and is not a file"):
        await materialize(
            host, op(server.user_authorized_keys, user=UA_USER, public_keys=UA_KEY_A)
        )


async def test_user_authorized_keys_honours_custom_directory_and_filename():
    # The two directory/filename options bypass the Home read entirely,
    # so an unseeded home_directories seam never causes a "None/.ssh" path.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host,
        op(
            server.user_authorized_keys,
            user=UA_USER,
            public_keys=(UA_KEY_A,),
            authorized_key_directory="/etc/ssh/keys",
            authorized_key_filename="alice",
        ),
    )
    assert ctx.changed
    assert commands == [
        _HomeMkdirCommand(path="/etc/ssh/keys"),
        _KeysChmodCommand(path="/etc/ssh/keys", mode=700),
        _HomeChownCommand(path="/etc/ssh/keys", user=UA_USER, group=UA_USER),
        _KeysTouchCommand(path="/etc/ssh/keys/alice"),
        _KeysChmodCommand(path="/etc/ssh/keys/alice", mode=600),
        _HomeChownCommand(path="/etc/ssh/keys/alice", user=UA_USER, group=UA_USER),
        AuthorizedKeysUploadCommand(
            content=f"{UA_KEY_A}\n", dest=_keys_spool_path("/etc/ssh/keys/alice")
        ),
        AuthorizedKeysInstallCommand(
            spool=_keys_spool_path("/etc/ssh/keys/alice"), dest="/etc/ssh/keys/alice"
        ),
    ]


async def test_user_authorized_keys_absorbs_unknown_kwargs():
    # ``**kwargs`` mirrors the baseline's global-arguments slot; op() already
    # strips name/sudo/su, the rest is ignored.
    host = SimulatedHost(
        name="h",
        home_directories={UA_USER: UA_HOME},
        directory_stats={UA_SSH_DIR: {"user": UA_USER, "group": UA_USER, "mode": 700}},
        file_stats={UA_KEYS_FILE: {"user": UA_USER, "group": UA_USER, "mode": 600}},
        authorized_keys={UA_KEYS_FILE: [UA_KEY_A]},
    )
    ctx, commands = await materialize(
        host,
        op(
            server.user_authorized_keys,
            user=UA_USER,
            public_keys=(UA_KEY_A,),
            future_flag=True,
        ),
    )
    assert commands == []
    assert not ctx.changed


async def test_user_authorized_keys_delete_keys_rewrites_to_exactly_the_request():
    # delete_keys=True is the baseline's whole-file rewrite: comments and every
    # unrequested key are discarded, only the requested keys survive, in
    # the caller's order.
    host = SimulatedHost(
        name="h",
        home_directories={UA_USER: UA_HOME},
        directory_stats={UA_SSH_DIR: {"user": UA_USER, "group": UA_USER, "mode": 700}},
        file_stats={UA_KEYS_FILE: {"user": UA_USER, "group": UA_USER, "mode": 600}},
        authorized_keys={UA_KEYS_FILE: [UA_KEY_A, UA_KEY_B]},
        remote_files={UA_KEYS_FILE: f"# managed\n{UA_KEY_A}\n{UA_KEY_B}\n".encode()},
    )
    ctx, commands = await materialize(
        host,
        op(
            server.user_authorized_keys,
            user=UA_USER,
            public_keys=(UA_KEY_B,),
            delete_keys=True,
        ),
    )
    assert ctx.changed
    assert ctx.reason == (
        f"directory {UA_SSH_DIR} already exists; "
        f"keys differ, will rewrite {UA_KEYS_FILE}"
    )
    assert commands == [
        AuthorizedKeysUploadCommand(content=f"{UA_KEY_B}\n", dest=UA_SPOOL),
        AuthorizedKeysInstallCommand(spool=UA_SPOOL, dest=UA_KEYS_FILE),
    ]


async def test_user_authorized_keys_delete_keys_noop_when_lists_equal():
    host = SimulatedHost(
        name="h",
        home_directories={UA_USER: UA_HOME},
        directory_stats={UA_SSH_DIR: {"user": UA_USER, "group": UA_USER, "mode": 700}},
        file_stats={UA_KEYS_FILE: {"user": UA_USER, "group": UA_USER, "mode": 600}},
        authorized_keys={UA_KEYS_FILE: [UA_KEY_A, UA_KEY_B]},
        remote_files={UA_KEYS_FILE: f"{UA_KEY_A}\n{UA_KEY_B}\n".encode()},
    )
    ctx, commands = await materialize(
        host,
        op(
            server.user_authorized_keys,
            user=UA_USER,
            public_keys=(UA_KEY_A, UA_KEY_B),
            delete_keys=True,
        ),
    )
    assert not ctx.changed
    assert ctx.reason == (
        f"directory {UA_SSH_DIR} already exists; file {UA_KEYS_FILE} already exists"
    )
    assert commands == []


async def test_user_authorized_keys_delete_keys_is_order_sensitive():
    # The baseline compares LISTS, not sets: a pure reorder is a rewrite.
    host = SimulatedHost(
        name="h",
        home_directories={UA_USER: UA_HOME},
        directory_stats={UA_SSH_DIR: {"user": UA_USER, "group": UA_USER, "mode": 700}},
        file_stats={UA_KEYS_FILE: {"user": UA_USER, "group": UA_USER, "mode": 600}},
        authorized_keys={UA_KEYS_FILE: [UA_KEY_A, UA_KEY_B]},
        remote_files={UA_KEYS_FILE: f"{UA_KEY_A}\n{UA_KEY_B}\n".encode()},
    )
    ctx, commands = await materialize(
        host,
        op(
            server.user_authorized_keys,
            user=UA_USER,
            public_keys=(UA_KEY_B, UA_KEY_A),
            delete_keys=True,
        ),
    )
    assert ctx.changed
    assert commands == [
        AuthorizedKeysUploadCommand(
            content=f"{UA_KEY_B}\n{UA_KEY_A}\n", dest=UA_SPOOL
        ),
        AuthorizedKeysInstallCommand(spool=UA_SPOOL, dest=UA_KEYS_FILE),
    ]


async def test_user_authorized_keys_delete_keys_empty_request_writes_lone_newline():
    # The baseline's "{}\n".format quirk: an empty key list empties the file to
    # a lone newline, not zero bytes.
    host = SimulatedHost(
        name="h",
        home_directories={UA_USER: UA_HOME},
        directory_stats={UA_SSH_DIR: {"user": UA_USER, "group": UA_USER, "mode": 700}},
        file_stats={UA_KEYS_FILE: {"user": UA_USER, "group": UA_USER, "mode": 600}},
        authorized_keys={UA_KEYS_FILE: [UA_KEY_A]},
        remote_files={UA_KEYS_FILE: f"{UA_KEY_A}\n".encode()},
    )
    ctx, commands = await materialize(
        host,
        op(
            server.user_authorized_keys,
            user=UA_USER,
            public_keys=(),
            delete_keys=True,
        ),
    )
    assert ctx.changed
    assert commands == [
        AuthorizedKeysUploadCommand(content="\n", dest=UA_SPOOL),
        AuthorizedKeysInstallCommand(spool=UA_SPOOL, dest=UA_KEYS_FILE),
    ]


async def test_user_authorized_keys_delete_keys_creates_from_scratch():
    host = SimulatedHost(name="h", home_directories={UA_USER: UA_HOME})
    ctx, commands = await materialize(
        host,
        op(
            server.user_authorized_keys,
            user=UA_USER,
            public_keys=(UA_KEY_A,),
            delete_keys=True,
        ),
    )
    assert ctx.changed
    assert ctx.reason == (
        f"{UA_SSH_DIR} missing, will create; {UA_KEYS_FILE} missing, "
        f"will create; keys differ, will rewrite {UA_KEYS_FILE}"
    )
    assert commands[-2:] == [
        AuthorizedKeysUploadCommand(content=f"{UA_KEY_A}\n", dest=UA_SPOOL),
        AuthorizedKeysInstallCommand(spool=UA_SPOOL, dest=UA_KEYS_FILE),
    ]


async def test_user_authorized_keys_content_noop_when_text_already_holds_keys():
    # Deliberate seam divergence: the parsed store reports no keys but the
    # raw text already holds the requested one -- the content stage sees
    # the transform is a fixpoint and plans nothing (the old in-place form
    # would have appended a duplicate).
    host = SimulatedHost(
        name="h",
        home_directories={UA_USER: UA_HOME},
        directory_stats={UA_SSH_DIR: {"user": UA_USER, "group": UA_USER, "mode": 700}},
        file_stats={UA_KEYS_FILE: {"user": UA_USER, "group": UA_USER, "mode": 600}},
        authorized_keys={UA_KEYS_FILE: []},
        remote_files={UA_KEYS_FILE: f"{UA_KEY_A}\n".encode()},
    )
    ctx, commands = await materialize(
        host, op(server.user_authorized_keys, user=UA_USER, public_keys=(UA_KEY_A,))
    )
    assert not ctx.changed
    assert ctx.reason == (
        f"directory {UA_SSH_DIR} already exists; "
        f"keys already written in {UA_KEYS_FILE}"
    )
    assert commands == []


async def test_user_authorized_keys_delete_keys_content_noop_on_matching_text():
    # The same fixpoint guard in the rewrite direction: the parsed list
    # says a change is due but the raw text already equals the rewrite.
    host = SimulatedHost(
        name="h",
        home_directories={UA_USER: UA_HOME},
        directory_stats={UA_SSH_DIR: {"user": UA_USER, "group": UA_USER, "mode": 700}},
        file_stats={UA_KEYS_FILE: {"user": UA_USER, "group": UA_USER, "mode": 600}},
        authorized_keys={UA_KEYS_FILE: [UA_KEY_B]},
        remote_files={UA_KEYS_FILE: f"{UA_KEY_A}\n".encode()},
    )
    ctx, commands = await materialize(
        host,
        op(
            server.user_authorized_keys,
            user=UA_USER,
            public_keys=(UA_KEY_A,),
            delete_keys=True,
        ),
    )
    assert not ctx.changed
    assert ctx.reason == (
        f"directory {UA_SSH_DIR} already exists; "
        f"keys already written in {UA_KEYS_FILE}"
    )
    assert commands == []


async def test_keys_upload_seam_writes_only_the_spool():
    # The transfer half stores bytes at the spool and touches nothing else.
    host = SimulatedHost(name="h")
    await send_command(
        host, AuthorizedKeysUploadCommand(content=f"{UA_KEY_A}\n", dest=UA_SPOOL)
    )
    assert host.remote_files == {UA_SPOOL: f"{UA_KEY_A}\n".encode()}
    assert UA_KEYS_FILE not in host.existing_files
    assert host.file_stats == {}
    assert host.authorized_keys == {}


async def test_keys_install_seam_updates_every_store():
    # send_command must keep remote_files, existing_files, file_stats and
    # the authorized_keys path-keyed seam in lockstep so the next step's
    # fact cache sees the installed text and the operation becomes a no-op.
    host = SimulatedHost(
        name="h",
        file_stats={UA_KEYS_FILE: {"user": UA_USER, "group": UA_USER, "mode": 644}},
        remote_files={UA_KEYS_FILE: f"# old\n{UA_KEY_B}\n".encode()},
        authorized_keys={UA_KEYS_FILE: [UA_KEY_B]},
    )
    await send_command(
        host, AuthorizedKeysUploadCommand(content=f"{UA_KEY_A}\n", dest=UA_SPOOL)
    )
    await send_command(
        host, AuthorizedKeysInstallCommand(spool=UA_SPOOL, dest=UA_KEYS_FILE)
    )
    # The spool is consumed; the dest holds the new bytes.
    assert host.remote_files == {UA_KEYS_FILE: f"{UA_KEY_A}\n".encode()}
    assert UA_KEYS_FILE in host.existing_files
    # cp over an existing file preserves its owner; chmod 600 + size refresh.
    assert host.file_stats[UA_KEYS_FILE] == {
        "user": UA_USER,
        "group": UA_USER,
        "mode": 600,
        "size": len(f"{UA_KEY_A}\n"),
    }
    # The parsed store is recomputed from the installed content, so a
    # delete_keys rewrite drops the unrequested key.
    assert host.authorized_keys[UA_KEYS_FILE] == [UA_KEY_A]
    # Install without its spool changes nothing (the tolerance arm).
    await send_command(
        host, AuthorizedKeysInstallCommand(spool=UA_SPOOL, dest=UA_KEYS_FILE)
    )
    assert host.remote_files == {UA_KEYS_FILE: f"{UA_KEY_A}\n".encode()}
    assert host.authorized_keys[UA_KEYS_FILE] == [UA_KEY_A]


async def test_keys_install_seam_without_prior_stats_seeds_root_row():
    # Descriptor-only install (the paired _KeysTouchCommand never ran): a
    # minimal root-owned mode-600 row, content-based so a repeat install
    # never duplicates an entry.
    host = SimulatedHost(name="h")
    await send_command(
        host, AuthorizedKeysUploadCommand(content=f"{UA_KEY_A}\n", dest=UA_SPOOL)
    )
    await send_command(
        host, AuthorizedKeysInstallCommand(spool=UA_SPOOL, dest=UA_KEYS_FILE)
    )
    assert host.file_stats[UA_KEYS_FILE] == {
        "user": "root",
        "group": "root",
        "mode": 600,
        "size": len(f"{UA_KEY_A}\n"),
    }
    assert host.authorized_keys[UA_KEYS_FILE] == [UA_KEY_A]


async def test_keys_chmod_and_touch_seam_rides_files_mutators():
    # _KeysChmodCommand/_KeysTouchCommand dispatch to the same chmod_path/
    # create_file mutators files.ChmodCommand/FileCommand use, so an
    # existing 644 row flips to 600 and a fresh touch seeds one.
    host = SimulatedHost(name="h", file_stats={UA_KEYS_FILE: {"mode": 644, "user": "root", "group": "root"}})
    await send_command(host, _KeysChmodCommand(path=UA_KEYS_FILE, mode=600))
    assert host.file_stats[UA_KEYS_FILE]["mode"] == 600
    host2 = SimulatedHost(name="h")
    await send_command(host2, _KeysTouchCommand(path=UA_KEYS_FILE))
    assert host2.file_stats[UA_KEYS_FILE] == {
        "mode": 644,
        "user": "root",
        "group": "root",
    }
    assert UA_KEYS_FILE in host2.existing_files


def keys_deploy(host: Host) -> Generator[OperationRequest]:
    """The example deploy: attach one key to someuser (elevated)."""
    yield op(
        server.user_authorized_keys,
        name=f"Authorise {UA_USER}",
        user=UA_USER,
        public_keys=(UA_KEY_A,),
        sudo=True,
    )


def ua_seed_factory(entry: dict) -> Host:
    """A SimulatedHost whose user has a home but no .ssh yet."""
    host = SimulatedHost.from_inventory(entry)
    host.home_directories = {UA_USER: UA_HOME}
    return host


async def test_user_authorized_keys_end_to_end_creates_then_noops():
    plans, report, hosts = await run(
        INVENTORY, keys_deploy, host_factory=ua_seed_factory
    )
    # Both hosts planned the whole state block plus the spool-and-install
    # pair.
    assert [p.changed for p in plans["web1"]] == [True]
    (entry,) = report.log[0:1]
    assert entry.changed
    log = format_log(report)
    assert "$ sudo mkdir -p /home/someuser/.ssh" in log
    assert "$ sudo chmod 700 /home/someuser/.ssh" in log
    assert f"sudo chown {UA_USER}:{UA_USER} /home/someuser/.ssh" in log
    assert "$ sudo touch /home/someuser/.ssh/authorized_keys" in log
    assert "$ sudo chmod 600 /home/someuser/.ssh/authorized_keys" in log
    # Two privilege levels in one request: the scp half stays unprivileged
    # even under sudo=True, only the install shell elevates. The key text
    # rides the scp payload, so it never appears in the shell log.
    assert f"    $ scp <authorized keys> -> {UA_SPOOL}" in log
    install = AuthorizedKeysInstallCommand(spool=UA_SPOOL, dest=UA_KEYS_FILE)
    assert f"    $ sudo {install.shell()}" in log
    assert UA_KEY_A not in log
    for host in hosts:
        assert host.authorized_keys[UA_KEYS_FILE] == [UA_KEY_A]
    # The apply mutated the seams; a second run is a no-op.
    plans2, _report2, _hosts2 = await run(
        INVENTORY, keys_deploy, host_factory=ua_seed_factory
    )
    # Fresh host_factory -> fresh seams, so this second run creates again.
    assert [p.changed for p in plans2["web1"]] == [True]


async def test_user_authorized_keys_second_run_on_same_hosts_is_noop():
    # Reusing the mutated hosts (not a fresh factory) proves the install
    # left every seam coherent: the second run plans nothing.
    hosts_by_name: dict[str, Host] = {}

    def factory(entry: dict) -> Host:
        host = ua_seed_factory(entry)
        hosts_by_name[entry["host"]] = host
        return host

    await run(INVENTORY, keys_deploy, host_factory=factory)
    plans2, _report2, _hosts2 = await run(
        INVENTORY,
        keys_deploy,
        host_factory=lambda entry: hosts_by_name[entry["host"]],
    )
    assert [p.changed for p in plans2["web1"]] == [False]


async def test_user_authorized_keys_repeat_request_is_noop_in_apply():
    def twice(host: Host) -> Generator[OperationRequest]:
        yield op(
            server.user_authorized_keys,
            name="Authorise first",
            user=UA_USER,
            public_keys=(UA_KEY_A,),
            sudo=True,
        )
        yield op(
            server.user_authorized_keys,
            name="Authorise again",
            user=UA_USER,
            public_keys=(UA_KEY_A,),
            sudo=True,
        )

    host = SimulatedHost(name="h", home_directories={UA_USER: UA_HOME})
    runtimes, plans = await run_prepare([host], twice)
    # Prepare shares one cache across the two requests and never sends, so
    # both identical requests plan a change...
    assert [p.changed for p in plans["h"]] == [True, True]
    report = await run_apply(runtimes, plans=plans)
    # ...while the per-step cache recreation makes the second a no-op.
    assert [e.changed for e in report.log] == [True, False]
    assert host.authorized_keys[UA_KEYS_FILE] == [UA_KEY_A]


async def test_user_authorized_keys_delete_keys_end_to_end():
    # Append both keys, then rewrite to just one in the same deploy: the
    # apply-phase per-step cache sees the first install, so the rewrite
    # drops the unrequested key and the spool is consumed.
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            server.user_authorized_keys,
            name="Authorise both keys",
            user=UA_USER,
            public_keys=(UA_KEY_A, UA_KEY_B),
            sudo=True,
        )
        yield op(
            server.user_authorized_keys,
            name="Keep only the first key",
            user=UA_USER,
            public_keys=(UA_KEY_A,),
            delete_keys=True,
            sudo=True,
        )

    host = SimulatedHost(name="h", home_directories={UA_USER: UA_HOME})
    runtimes, plans = await run_prepare([host], deploy)
    # Prepare shares one cache, so both requests plan a change...
    assert [p.changed for p in plans["h"]] == [True, True]
    report = await run_apply(runtimes, plans=plans)
    # ...and apply really rewrites: only the requested key survives.
    assert [e.changed for e in report.log] == [True, True]
    assert host.authorized_keys[UA_KEYS_FILE] == [UA_KEY_A]
    assert host.remote_files[UA_KEYS_FILE] == f"{UA_KEY_A}\n".encode()
    assert UA_SPOOL not in host.remote_files


async def test_user_authorized_keys_dry_run_sends_nothing():
    _plans, report, hosts = await run(
        INVENTORY, keys_deploy, host_factory=ua_seed_factory, dry=True
    )
    assert report is None
    for host in hosts:
        assert host.authorized_keys == {}
        assert host.remote_files == {}
        assert UA_SSH_DIR not in host.directory_stats
        assert UA_KEYS_FILE not in host.file_stats


# --- server.etc_hosts --------------------------------------------------------

# The baseline's own operation fixtures (tests/operations/server.etc_hosts/) key
# their entries by this IP; reused verbatim here for parity.
EH_IP = "192.168.1.10"
EH_NAMES = ("db.internal", "db")
EH_PATH = "/etc/hosts"
# The dest-derived spool and the install inner script -- the text inside the
# ``sh -c '...'`` wrapper the install descriptor emits.
EH_SPOOL = _hosts_spool_path(EH_PATH)
EH_SPOOL_DIR = EH_SPOOL.rsplit("/", 1)[0]
EH_INSTALL_INNER = (
    f"mkdir -p /etc && cp {EH_SPOOL} {EH_PATH} && "
    f"chmod 0644 {EH_PATH} && rm -rf {EH_SPOOL_DIR}"
)
# The baseline's own fact fixture (tests/facts/server.EtcHosts/etc_hosts.json):
# comments, tabs, a duplicate IP and a one-token line, reused verbatim.
EH_FIXTURE_OUTPUT = (
    "# This is /etc/hosts\n"
    "\n"
    "127.0.0.1\tlocalhost localhost.localdomain\n"
    "::1\t\tlocalhost # IPv6 loopback\n"
    "192.168.1.10  db.internal\n"
    "192.168.1.10  db   # duplicate IP, extra alias\n"
    "nohostname\n"
    "   \n"
)
EH_FIXTURE_FACT = {
    "127.0.0.1": ["localhost", "localhost.localdomain"],
    "::1": ["localhost"],
    "192.168.1.10": ["db.internal", "db"],
}


def test_etc_hosts_command_quotes_the_path():
    assert EtcHosts.command() == "cat /etc/hosts 2>/dev/null || true"
    assert EtcHosts.command("/etc dir/my hosts") == (
        "cat '/etc dir/my hosts' 2>/dev/null || true"
    )


async def test_etc_hosts_parses_the_baseline_fact_fixture():
    # Comments and blank lines dropped, the duplicate IP's hostnames merged
    # in file order without duplicates, the one-token "nohostname" skipped.
    host = CannedShellHost(EH_FIXTURE_OUTPUT)
    assert await EtcHosts.process(host) == EH_FIXTURE_FACT
    assert host.shells == ["cat /etc/hosts 2>/dev/null || true"]
    # A missing file: the || true guard turns the cat error into empty
    # output, which reads as {} (the baseline's default = dict).
    assert await EtcHosts.process(CannedShellHost("")) == {}


async def test_etc_hosts_reads_the_simulated_seam():
    # The seam parses the shared remote_files store directly (the
    # sysctl.SysctlConf pattern), so a simulated install reads back through
    # this fact with no second parsed store to keep in sync.
    host = SimulatedHost(
        name="h",
        remote_files={EH_PATH: EH_FIXTURE_OUTPUT.encode(), "/etc/empty-hosts": b""},
    )
    assert await EtcHosts.process(host) == EH_FIXTURE_FACT
    assert await EtcHosts.process(host, "/etc/empty-hosts") == {}
    assert await EtcHosts.process(host, "/no/such/hosts") == {}


async def test_etc_hosts_gather_fact_forwards_the_path():
    hosts = [
        SimulatedHost(name="web1", remote_files={"/custom/hosts": b"10.0.0.5 db\n"}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, EtcHosts, "/custom/hosts") == {
        "web1": {"10.0.0.5": ["db"]},
        "web2": {},
    }


def test_hosts_content_transform_purity_and_fixpoint():
    # Absent file becomes just the entry; a repeat application is a fixpoint.
    once = _hosts_content(None, EH_IP, EH_NAMES, True)
    assert once == "192.168.1.10 db.internal db\n"
    assert _hosts_content(once, EH_IP, EH_NAMES, True) == once
    # Untouched lines, comments, blank lines and their order survive; the
    # edited line keeps its position mid-file (the baseline's update_entry).
    current = "127.0.0.1 localhost\n192.168.1.10 old.name\n::1 localhost\n"
    updated = _hosts_content(current, EH_IP, EH_NAMES, True)
    assert updated == "127.0.0.1 localhost\n192.168.1.10 db.internal db\n::1 localhost\n"
    assert _hosts_content(updated, EH_IP, EH_NAMES, True) == updated
    content = "# managed\n\n127.0.0.1 localhost\n"
    assert _hosts_content(content, EH_IP, EH_NAMES, True) == (
        f"{content}192.168.1.10 db.internal db\n"
    )
    # A comment on the edited line is dropped (baseline parity); a comment
    # on any other line survives verbatim.
    assert _hosts_content(f"{EH_IP} old # note\n", EH_IP, EH_NAMES, True) == (
        "192.168.1.10 db.internal db\n"
    )
    # CRLF is normalised and a missing trailing newline is restored.
    assert _hosts_content("127.0.0.1 localhost", EH_IP, EH_NAMES, True) == (
        "127.0.0.1 localhost\n192.168.1.10 db.internal db\n"
    )
    assert _hosts_content("127.0.0.1 localhost\r\n", EH_IP, EH_NAMES, True) == (
        "127.0.0.1 localhost\n192.168.1.10 db.internal db\n"
    )


def test_hosts_content_removal_directions():
    current = "127.0.0.1 localhost\n192.168.1.10 db.internal db\n::1 localhost\n"
    # No hostnames: every line for the IP goes, the others stay in order.
    removed = _hosts_content(current, EH_IP, (), False)
    assert removed == "127.0.0.1 localhost\n::1 localhost\n"
    assert _hosts_content(removed, EH_IP, (), False) == removed
    # Named hostnames: only those names drop; the line survives with the rest.
    assert _hosts_content(current, EH_IP, ("db",), False) == (
        "127.0.0.1 localhost\n192.168.1.10 db.internal\n::1 localhost\n"
    )
    # Removing every name on the line removes the line entirely...
    assert _hosts_content(current, EH_IP, EH_NAMES, False) == removed
    # ...and a removal that empties the file yields "" (a legitimate payload).
    assert _hosts_content("192.168.1.10 db.internal db\n", EH_IP, (), False) == ""
    assert _hosts_content(None, EH_IP, (), False) == ""


def test_etc_hosts_upload_command_rendering():
    upload = EtcHostsUploadCommand(content="192.168.1.10 db.internal db\n", dest=EH_SPOOL)
    # The transfer is a non-shell descriptor: its loggable form is __str__,
    # and command_shell never prefixes it with sudo/su -- transfers ride the
    # login user's scp channel regardless of the request's elevation.
    assert str(upload) == f"scp <hosts file> -> {EH_SPOOL}"
    assert command_shell(upload, sudo=True) == f"scp <hosts file> -> {EH_SPOOL}"
    assert command_shell(upload, su=True) == f"scp <hosts file> -> {EH_SPOOL}"
    # The content field is a plain str: hashable and value-equal across the
    # prepare/apply phases (the determinism guard compares descriptors). An
    # empty payload (a removal that empties the file) is legitimate too.
    assert hash(upload) == hash(
        EtcHostsUploadCommand(content="192.168.1.10 db.internal db\n", dest=EH_SPOOL)
    )
    assert str(EtcHostsUploadCommand(content="", dest=EH_SPOOL)) == (
        f"scp <hosts file> -> {EH_SPOOL}"
    )


def test_etc_hosts_install_command_shell_rendering():
    install = EtcHostsInstallCommand(spool=EH_SPOOL, dest=EH_PATH)
    assert install.shell() == f"sh -c {shlex.quote(EH_INSTALL_INNER)}"
    # Sudo elevates the whole sh -c, so every step of the && chain -- cp,
    # chmod and the spool cleanup -- runs as the elevated shell.
    assert command_shell(install, sudo=True) == f"sudo {install.shell()}"
    assert command_shell(install, su=True) == f"su -c {shlex.quote(install.shell())}"
    # Quoting proof: a space in the DIRECTORY (shlex.quote leaves space-free
    # paths bare). Recover the inner script with shlex.split, never a raw
    # substring of the quoted text.
    path = "/etc dir/hosts file"
    spool = _hosts_spool_path(path)
    spool_dir = spool.rsplit("/", 1)[0]
    inner = shlex.split(EtcHostsInstallCommand(spool=spool, dest=path).shell())[2]
    assert inner.startswith("mkdir -p '/etc dir' && ")
    assert inner.endswith(f"&& rm -rf {spool_dir}")


def test_hosts_spool_path_is_dest_derived_and_deterministic():
    # Same dest -> same spool in both phases; different dest -> different
    # spool, so two path-scoped requests never clobber one another's upload.
    assert _hosts_spool_path(EH_PATH) == EH_SPOOL
    assert _hosts_spool_path(EH_PATH) == _hosts_spool_path(EH_PATH)
    assert _hosts_spool_path("/etc/hosts.custom") != EH_SPOOL
    digest = hashlib.sha1(EH_PATH.encode()).hexdigest()[:16]
    assert EH_SPOOL == f"/tmp/reemote-server-etc-hosts-{digest}/hosts"


@pytest.mark.parametrize(
    ("seed", "ip", "kwargs", "content", "reason"),
    [
        # The baseline's add_new_entry.json: the IP is unknown -> the entry is
        # appended and the whole new text rides one spool-and-install pair.
        (
            b"127.0.0.1 localhost\n",
            EH_IP,
            {"hostnames": EH_NAMES},
            "127.0.0.1 localhost\n192.168.1.10 db.internal db\n",
            "192.168.1.10 -> db.internal db will be written to /etc/hosts",
        ),
        # create_file.json: no hosts file at all -> it is created holding
        # just the entry (a string hostnames splits on whitespace).
        (
            None,
            "127.0.0.1",
            {"hostnames": "localhost"},
            "127.0.0.1 localhost\n",
            "127.0.0.1 -> localhost will be written to /etc/hosts",
        ),
        # missing_file_absent_noop.json: removal against a missing file.
        (
            None,
            EH_IP,
            {"present": False},
            None,
            "192.168.1.10 already absent from /etc/hosts",
        ),
        # noop_absent.json: other entries exist, the IP does not.
        (
            b"127.0.0.1 localhost\n192.168.1.10 db.internal\n",
            "192.168.1.99",
            {"present": False},
            None,
            "192.168.1.99 already absent from /etc/hosts",
        ),
        # noop_hostname_absent.json: the IP exists but references none of
        # the named hostnames.
        (
            b"192.168.1.10 db.internal db\n",
            EH_IP,
            {"hostnames": "not-here", "present": False},
            None,
            "192.168.1.10 in /etc/hosts does not reference any of: not-here",
        ),
        # noop_present.json: the exact names in the exact order.
        (
            b"127.0.0.1 localhost\n192.168.1.10 db.internal db\n",
            EH_IP,
            {"hostnames": EH_NAMES},
            None,
            "192.168.1.10 -> db.internal db already present in /etc/hosts",
        ),
        # remove_entry.json: no hostnames -> the whole line goes, the rest
        # of the file survives.
        (
            b"127.0.0.1 localhost\n192.168.1.10 db.internal db\n::1 localhost\n",
            EH_IP,
            {"present": False},
            "127.0.0.1 localhost\n::1 localhost\n",
            "192.168.1.10 will be removed from /etc/hosts",
        ),
        # remove_specific_hostname.json: only "db" drops, "db.internal" stays.
        (
            b"127.0.0.1 localhost\n192.168.1.10 db.internal db\n",
            EH_IP,
            {"hostnames": "db", "present": False},
            "127.0.0.1 localhost\n192.168.1.10 db.internal\n",
            "192.168.1.10 will be removed from /etc/hosts",
        ),
        # update_entry.json: the IP's names differ -> its line is rewritten
        # in place, mid-file.
        (
            b"127.0.0.1 localhost\n192.168.1.10 old.name\n::1 localhost\n",
            EH_IP,
            {"hostnames": EH_NAMES},
            "127.0.0.1 localhost\n192.168.1.10 db.internal db\n::1 localhost\n",
            "192.168.1.10 -> db.internal db will be written to /etc/hosts",
        ),
    ],
)
async def test_etc_hosts_matches_baseline_operation_fixtures(
    seed: bytes | None, ip: str, kwargs: dict, content: str | None, reason: str
):
    # Every removal fixture seeds remote_files too: EtcHosts parses that
    # store directly, so a parsed-only seed would no-op at the fact stage
    # and silently stop exercising the install.
    host = SimulatedHost(
        name="h", remote_files={EH_PATH: seed} if seed is not None else {}
    )
    ctx, commands = await materialize(host, op(server.etc_hosts, ip=ip, **kwargs))
    expected = (
        []
        if content is None
        else [
            EtcHostsUploadCommand(content=content, dest=EH_SPOOL),
            EtcHostsInstallCommand(spool=EH_SPOOL, dest=EH_PATH),
        ]
    )
    assert commands == expected
    assert ctx.changed == bool(expected)
    assert ctx.reason == reason


async def test_etc_hosts_requires_hostnames_when_present():
    # The baseline's OperationError message, raised as the house ValueError.
    host = SimulatedHost(name="h")
    with pytest.raises(ValueError, match="hostnames must be provided when present=True"):
        await materialize(host, op(server.etc_hosts, ip=EH_IP))


async def test_etc_hosts_content_noop_when_transform_is_a_fixpoint():
    # The parsed fact dedups repeated names, so a request repeating one
    # ("db", "db") never equals the fact's ["db"] and always wants an edit;
    # the file's own text already holds the rewritten line, so the content
    # guard no-ops instead of spooling an identical file.
    host = SimulatedHost(name="h", remote_files={EH_PATH: b"192.168.1.10 db db\n"})
    ctx, commands = await materialize(
        host, op(server.etc_hosts, ip=EH_IP, hostnames=("db", "db"))
    )
    assert not ctx.changed
    assert ctx.reason == "192.168.1.10 entry already written in /etc/hosts"
    assert commands == []


async def test_etc_hosts_reorders_hostnames_with_a_rewrite():
    # The present comparison is order-sensitive (baseline parity): a mere
    # reordering of the names rewrites the line.
    host = SimulatedHost(
        name="h", remote_files={EH_PATH: b"192.168.1.10 db db.internal\n"}
    )
    ctx, commands = await materialize(
        host, op(server.etc_hosts, ip=EH_IP, hostnames=EH_NAMES)
    )
    assert ctx.changed
    assert commands == [
        EtcHostsUploadCommand(content="192.168.1.10 db.internal db\n", dest=EH_SPOOL),
        EtcHostsInstallCommand(spool=EH_SPOOL, dest=EH_PATH),
    ]


async def test_etc_hosts_honours_custom_path():
    path = "/etc/hosts.custom"
    host = SimulatedHost(name="h", remote_files={path: b"127.0.0.1 localhost\n"})
    ctx, commands = await materialize(
        host, op(server.etc_hosts, ip=EH_IP, hostnames=EH_NAMES, path=path)
    )
    assert ctx.changed
    assert commands == [
        EtcHostsUploadCommand(
            content="127.0.0.1 localhost\n192.168.1.10 db.internal db\n",
            dest=_hosts_spool_path(path),
        ),
        EtcHostsInstallCommand(spool=_hosts_spool_path(path), dest=path),
    ]


async def test_etc_hosts_absorbs_unknown_kwargs():
    # ``**kwargs`` mirrors the baseline's global-arguments slot; op() already
    # strips name/sudo/su, the rest is ignored.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, op(server.etc_hosts, ip=EH_IP, hostnames=EH_NAMES, future_flag=True)
    )
    assert ctx.changed
    assert commands[0] == EtcHostsUploadCommand(
        content="192.168.1.10 db.internal db\n", dest=EH_SPOOL
    )


async def test_etc_hosts_removal_can_empty_the_file():
    # The only entry goes -> the spool carries "" (a legitimate payload the
    # descriptors never special-case) and the installed file is empty.
    host = SimulatedHost(
        name="h", remote_files={EH_PATH: b"192.168.1.10 db.internal db\n"}
    )
    ctx, commands = await materialize(
        host, op(server.etc_hosts, ip=EH_IP, present=False)
    )
    assert ctx.changed
    assert commands == [
        EtcHostsUploadCommand(content="", dest=EH_SPOOL),
        EtcHostsInstallCommand(spool=EH_SPOOL, dest=EH_PATH),
    ]
    for cmd in commands:
        await send_command(host, cmd)
    assert host.remote_files == {EH_PATH: b""}


async def test_hosts_upload_seam_writes_only_the_spool():
    # The transfer half stores bytes at the spool and touches nothing else.
    host = SimulatedHost(name="h")
    await send_command(
        host,
        EtcHostsUploadCommand(content="192.168.1.10 db.internal db\n", dest=EH_SPOOL),
    )
    assert host.remote_files == {EH_SPOOL: b"192.168.1.10 db.internal db\n"}
    assert EH_PATH not in host.existing_files
    assert host.file_stats == {}


async def test_hosts_install_seam_updates_every_store():
    # send_command must keep remote_files, existing_files and file_stats in
    # lockstep; EtcHosts parses remote_files directly, so no separate parsed
    # store exists to refresh.
    host = SimulatedHost(
        name="h",
        file_stats={EH_PATH: {"user": "root", "group": "root", "mode": 600}},
        remote_files={EH_PATH: b"192.168.1.10 old.name\n"},
    )
    await send_command(
        host,
        EtcHostsUploadCommand(content="192.168.1.10 db.internal db\n", dest=EH_SPOOL),
    )
    await send_command(host, EtcHostsInstallCommand(spool=EH_SPOOL, dest=EH_PATH))
    # The spool is consumed; the dest holds the new bytes.
    assert host.remote_files == {EH_PATH: b"192.168.1.10 db.internal db\n"}
    assert EH_PATH in host.existing_files
    # cp over an existing file preserves its owner; chmod 0644 + size refresh.
    assert host.file_stats[EH_PATH] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len("192.168.1.10 db.internal db\n"),
    }
    # The parsed fact reads the installed bytes back -- content-based, so a
    # repeat upload+install never duplicates the entry.
    assert await EtcHosts.process(host) == {EH_IP: ["db.internal", "db"]}
    await send_command(
        host,
        EtcHostsUploadCommand(content="192.168.1.10 db.internal db\n", dest=EH_SPOOL),
    )
    await send_command(host, EtcHostsInstallCommand(spool=EH_SPOOL, dest=EH_PATH))
    assert host.remote_files == {EH_PATH: b"192.168.1.10 db.internal db\n"}
    # Install without its spool changes nothing (the tolerance arm).
    await send_command(host, EtcHostsInstallCommand(spool=EH_SPOOL, dest=EH_PATH))
    assert host.remote_files == {EH_PATH: b"192.168.1.10 db.internal db\n"}


async def test_hosts_install_seam_without_prior_stats_seeds_root_row():
    # Descriptor-only install against a fresh host: a minimal root-owned
    # mode-644 row (the file the elevated cp created).
    host = SimulatedHost(name="h")
    await send_command(
        host,
        EtcHostsUploadCommand(content="192.168.1.10 db.internal db\n", dest=EH_SPOOL),
    )
    await send_command(host, EtcHostsInstallCommand(spool=EH_SPOOL, dest=EH_PATH))
    assert host.file_stats[EH_PATH] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len("192.168.1.10 db.internal db\n"),
    }
    assert EH_PATH in host.existing_files


def hosts_deploy(host: Host) -> Generator[OperationRequest]:
    """The example deploy: add the database entry to /etc/hosts (elevated)."""
    yield op(
        server.etc_hosts,
        name="Add the database host entry",
        ip=EH_IP,
        hostnames=EH_NAMES,
        sudo=True,
    )


async def test_etc_hosts_end_to_end_creates_then_noops():
    hosts_by_name: dict[str, Host] = {}

    def factory(entry: dict) -> Host:
        host = SimulatedHost.from_inventory(entry)
        hosts_by_name[entry["host"]] = host
        return host

    plans, report, hosts = await run(INVENTORY, hosts_deploy, host_factory=factory)
    assert [p.changed for p in plans["web1"]] == [True]
    (entry,) = report.log[0:1]
    assert entry.changed
    log = format_log(report)
    # Two privilege levels in one request: the scp half stays unprivileged
    # even under sudo=True, only the install shell elevates. The hosts text
    # rides the scp payload, so it never appears as a shell command.
    assert f"    $ scp <hosts file> -> {EH_SPOOL}" in log
    install = EtcHostsInstallCommand(spool=EH_SPOOL, dest=EH_PATH)
    assert f"    $ sudo {install.shell()}" in log
    for host in hosts:
        assert host.remote_files[EH_PATH] == b"192.168.1.10 db.internal db\n"
        assert EH_SPOOL not in host.remote_files
    # Reusing the mutated hosts (not a fresh factory) proves the install
    # left every seam coherent: the second run plans nothing.
    plans2, _report2, _hosts2 = await run(
        INVENTORY,
        hosts_deploy,
        host_factory=lambda entry: hosts_by_name[entry["host"]],
    )
    assert [p.changed for p in plans2["web1"]] == [False]


async def test_etc_hosts_repeat_request_is_noop_in_apply():
    def twice(host: Host) -> Generator[OperationRequest]:
        yield op(
            server.etc_hosts,
            name="Add entry first",
            ip=EH_IP,
            hostnames=EH_NAMES,
            sudo=True,
        )
        yield op(
            server.etc_hosts,
            name="Add entry again",
            ip=EH_IP,
            hostnames=EH_NAMES,
            sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], twice)
    # Prepare shares one cache across the two requests and never sends, so
    # both identical requests plan a change...
    assert [p.changed for p in plans["h"]] == [True, True]
    report = await run_apply(runtimes, plans=plans)
    # ...while the per-step cache recreation makes the second a no-op.
    assert [e.changed for e in report.log] == [True, False]
    assert host.remote_files[EH_PATH] == b"192.168.1.10 db.internal db\n"


async def test_etc_hosts_add_then_remove_in_one_deploy():
    # Rewrite the entry, then remove it in the same deploy: the apply-phase
    # per-step cache sees the first install, so the removal empties the
    # file (the "" spool payload) and the spool is consumed.
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            server.etc_hosts, name="Add entry", ip=EH_IP, hostnames=EH_NAMES, sudo=True
        )
        yield op(
            server.etc_hosts, name="Remove entry", ip=EH_IP, present=False, sudo=True
        )

    host = SimulatedHost(name="h", remote_files={EH_PATH: b"192.168.1.10 old.name\n"})
    runtimes, plans = await run_prepare([host], deploy)
    assert [p.changed for p in plans["h"]] == [True, True]
    report = await run_apply(runtimes, plans=plans)
    assert [e.changed for e in report.log] == [True, True]
    assert host.remote_files == {EH_PATH: b""}
    assert EH_PATH in host.existing_files


async def test_etc_hosts_dry_run_sends_nothing():
    _plans, report, hosts = await run(
        INVENTORY, hosts_deploy, host_factory=SimulatedHost.from_inventory, dry=True
    )
    assert report is None
    for host in hosts:
        assert host.remote_files == {}
        assert host.file_stats == {}


# --- server.locale -----------------------------------------------------------

LG_PATH = "/etc/locale.gen"
LG_LOCALE = "fr_FR.UTF-8"
LG_SPOOL = _locale_spool_path(LG_PATH)
LG_SPOOL_DIR = LG_SPOOL.rsplit("/", 1)[0]
LG_INSTALL_INNER = (
    f"mkdir -p /etc && cp {LG_SPOOL} {LG_PATH} && "
    f"chmod 0644 {LG_PATH} && rm -rf {LG_SPOOL_DIR}"
)
# A miniature locale.gen: header comment, an unrelated commented definition,
# an uncommented one, the commented target and a near-miss @euro variant the
# whitespace-anchored pattern must not match.
LG_FILE = (
    "# This file lists locales that you wish to have built.\n"
    "# en_US.UTF-8 UTF-8\n"
    "de_DE.UTF-8 UTF-8\n"
    "# fr_FR.UTF-8 UTF-8\n"
    "# fr_FR.UTF-8@euro UTF-8\n"
)
LG_ENABLED_FILE = LG_FILE.replace("# fr_FR.UTF-8 UTF-8", "fr_FR.UTF-8 UTF-8")


def test_locale_edit_enable_purity_and_fixpoint():
    new = _locale_edit(LG_FILE, LG_LOCALE, True)
    assert new == LG_ENABLED_FILE
    # Idempotent, and every other line (header, other definitions, the
    # @euro near-miss) survives in order with the trailing newline.
    assert _locale_edit(new, LG_LOCALE, True) == new


def test_locale_edit_enable_strips_every_comment_marker():
    # The baseline's str.replace verbatim: a doubly commented line is fully
    # enabled in one edit, not just unpeeled one layer.
    assert _locale_edit("# # fr_FR.UTF-8 UTF-8\n", LG_LOCALE, True) == (
        "fr_FR.UTF-8 UTF-8\n"
    )


def test_locale_edit_disable_and_fixpoint_guard():
    off = _locale_edit(LG_ENABLED_FILE, LG_LOCALE, False)
    assert off == LG_FILE
    # The guard the baseline's unconditional prepend lacks: an already commented
    # line is never double-commented, so the transform is a fixpoint.
    assert _locale_edit(off, LG_LOCALE, False) == off


def test_locale_edit_preserves_trailing_newline_state():
    assert _locale_edit("# fr_FR.UTF-8 UTF-8", LG_LOCALE, True) == "fr_FR.UTF-8 UTF-8"
    assert _locale_edit("# fr_FR.UTF-8 UTF-8\n", LG_LOCALE, True) == (
        "fr_FR.UTF-8 UTF-8\n"
    )


def test_locale_edit_raises_on_zero_and_multiple_matches():
    # The baseline's verbatim messages, including the absent file (None content
    # -- a RHEL-family host with no /etc/locale.gen at all).
    with pytest.raises(OperationError, match="not found in /etc/locale.gen"):
        _locale_edit("# en_US.UTF-8 UTF-8\n", LG_LOCALE, True)
    with pytest.raises(OperationError, match="not found in /etc/locale.gen"):
        _locale_edit(None, LG_LOCALE, True)
    with pytest.raises(OperationError, match="Multiple locales matches"):
        _locale_edit("# fr_FR.UTF-8 UTF-8\nfr_FR.UTF-8 UTF-8\n", LG_LOCALE, True)


def test_locale_edit_escapes_the_locale_literally():
    # The baseline interpolates the locale raw into its grep pattern, so the dot
    # in fr_FR.UTF-8 would match any character; re.escape hardens that.
    with pytest.raises(OperationError, match="not found"):
        _locale_edit("# fr_FRXUTF-8 UTF-8\n", LG_LOCALE, True)


def test_locale_upload_command_rendering():
    upload = LocaleUploadCommand(content=LG_ENABLED_FILE, dest=LG_SPOOL)
    # A transfer has no shell(): command_shell falls back to __str__ and
    # never prefixes it with sudo -- the scp rides the login user even
    # under sudo=True (the family's two privilege levels).
    assert str(upload) == f"scp <locale.gen> -> {LG_SPOOL}"
    assert command_shell(upload, sudo=True) == f"scp <locale.gen> -> {LG_SPOOL}"
    assert command_shell(upload, su=True) == f"scp <locale.gen> -> {LG_SPOOL}"
    # The content field is a plain str: hashable and value-equal across the
    # prepare/apply phases (the determinism guard compares descriptors).
    assert hash(upload) == hash(
        LocaleUploadCommand(content=LG_ENABLED_FILE, dest=LG_SPOOL)
    )


def test_locale_install_command_shell_rendering():
    install = LocaleInstallCommand(spool=LG_SPOOL, dest=LG_PATH)
    assert install.shell() == f"sh -c {shlex.quote(LG_INSTALL_INNER)}"
    # Sudo elevates the whole sh -c, so every step of the && chain -- cp,
    # chmod and the spool cleanup -- runs as the elevated shell.
    assert command_shell(install, sudo=True) == f"sudo {install.shell()}"
    assert command_shell(install, su=True) == f"su -c {shlex.quote(install.shell())}"
    # Quoting proof: a space in the DIRECTORY (shlex.quote leaves space-free
    # paths bare). Recover the inner script with shlex.split, never a raw
    # substring of the quoted text.
    path = "/etc dir/locale gen"
    spool = _locale_spool_path(path)
    spool_dir = spool.rsplit("/", 1)[0]
    inner = shlex.split(LocaleInstallCommand(spool=spool, dest=path).shell())[2]
    assert inner.startswith("mkdir -p '/etc dir' && ")
    assert inner.endswith(f"&& rm -rf {spool_dir}")


def test_locale_gen_command_rendering():
    gen = LocaleGenCommand(locale=LG_LOCALE, present=True)
    # The constant rendering: locale-gen rebuilds the archive from every
    # uncommented definition, so the fields never reach the shell.
    assert gen.shell() == "locale-gen"
    assert command_shell(gen, sudo=True) == "sudo locale-gen"
    assert command_shell(gen, su=True) == f"su -c {shlex.quote('locale-gen')}"
    assert hash(gen) == hash(LocaleGenCommand(locale=LG_LOCALE, present=True))


def test_locale_spool_path_is_dest_derived_and_deterministic():
    # Same dest -> same spool in both phases; different dest -> different
    # spool, and the name keeps the family prefix.
    assert _locale_spool_path(LG_PATH) == LG_SPOOL
    assert _locale_spool_path(LG_PATH) == _locale_spool_path(LG_PATH)
    assert _locale_spool_path("/etc/locale.gen.custom") != LG_SPOOL
    digest = hashlib.sha1(LG_PATH.encode()).hexdigest()[:16]
    assert LG_SPOOL == f"/tmp/reemote-server-locale-{digest}/locale"


async def test_locale_enable_plans_spool_install_and_gen():
    host = SimulatedHost(
        name="h",
        locales=["C.UTF-8"],
        remote_files={LG_PATH: LG_FILE.encode()},
    )
    ctx, commands = await materialize(
        host, op(server.locale, name="Enable French", locale=LG_LOCALE, sudo=True)
    )
    assert ctx.changed
    assert commands == [
        LocaleUploadCommand(content=LG_ENABLED_FILE, dest=LG_SPOOL),
        LocaleInstallCommand(spool=LG_SPOOL, dest=LG_PATH),
        LocaleGenCommand(locale=LG_LOCALE, present=True),
    ]
    assert ctx.reason == (
        f"will enable {LG_LOCALE} in {LG_PATH}; "
        "will regenerate the installed locales with locale-gen"
    )


async def test_locale_disable_plans_comment_out():
    host = SimulatedHost(
        name="h",
        locales=["C.UTF-8", LG_LOCALE],
        remote_files={LG_PATH: LG_ENABLED_FILE.encode()},
    )
    ctx, commands = await materialize(
        host,
        op(server.locale, name="Disable French", locale=LG_LOCALE, present=False, sudo=True),
    )
    assert ctx.changed
    assert commands == [
        LocaleUploadCommand(content=LG_FILE, dest=LG_SPOOL),
        LocaleInstallCommand(spool=LG_SPOOL, dest=LG_PATH),
        LocaleGenCommand(locale=LG_LOCALE, present=False),
    ]
    assert ctx.reason == (
        f"will disable {LG_LOCALE} in {LG_PATH}; "
        "will regenerate the installed locales with locale-gen"
    )


async def test_locale_noop_when_already_enabled_or_disabled():
    # The fact-level no-op: the Locales listing already agrees with the
    # request, so nothing is planned even though the definition line's own
    # state differs (the baseline gates on locale -a, not on the file).
    host = SimulatedHost(
        name="h", locales=[LG_LOCALE], remote_files={LG_PATH: LG_FILE.encode()}
    )
    ctx, commands = await materialize(host, op(server.locale, locale=LG_LOCALE))
    assert not ctx.changed
    assert commands == []
    assert ctx.reason == f"locale {LG_LOCALE} already enabled"
    host2 = SimulatedHost(
        name="h", locales=[], remote_files={LG_PATH: LG_ENABLED_FILE.encode()}
    )
    ctx2, commands2 = await materialize(
        host2, op(server.locale, locale=LG_LOCALE, present=False)
    )
    assert not ctx2.changed
    assert commands2 == []
    assert ctx2.reason == f"locale {LG_LOCALE} already disabled"


async def test_locale_missing_definition_raises_even_when_in_state():
    # The baseline's FindInFile read runs BEFORE the presence gate, so a missing
    # definition raises even for an already-enabled request -- and an
    # absent /etc/locale.gen (the RHEL-family case) reads as no matches.
    host = SimulatedHost(name="h", locales=[LG_LOCALE], remote_files={})
    with pytest.raises(OperationError, match="not found in /etc/locale.gen"):
        await materialize(host, op(server.locale, locale=LG_LOCALE))
    host2 = SimulatedHost(
        name="h",
        locales=[],
        remote_files={LG_PATH: b"# fr_FR.UTF-8 UTF-8\nfr_FR.UTF-8 UTF-8\n"},
    )
    with pytest.raises(OperationError, match="Multiple locales matches"):
        await materialize(host2, op(server.locale, locale=LG_LOCALE))


async def test_locale_content_fixpoint_still_regenerates():
    # The content-level no-op the in-place form could never reach: the
    # definition line is already in the desired state, so only the
    # regeneration is planned ("locale-gen is always executed").
    host = SimulatedHost(
        name="h", locales=[], remote_files={LG_PATH: LG_ENABLED_FILE.encode()}
    )
    ctx, commands = await materialize(host, op(server.locale, locale=LG_LOCALE))
    assert ctx.changed
    assert commands == [LocaleGenCommand(locale=LG_LOCALE, present=True)]
    assert ctx.reason == (
        f"{LG_LOCALE} definition line already uncommented in {LG_PATH}; "
        "will regenerate the installed locales with locale-gen"
    )
    # Disable direction: commented line while the archive still lists it.
    host2 = SimulatedHost(
        name="h", locales=[LG_LOCALE], remote_files={LG_PATH: LG_FILE.encode()}
    )
    ctx2, commands2 = await materialize(
        host2, op(server.locale, locale=LG_LOCALE, present=False)
    )
    assert ctx2.changed
    assert commands2 == [LocaleGenCommand(locale=LG_LOCALE, present=False)]
    assert ctx2.reason == (
        f"{LG_LOCALE} definition line already commented in {LG_PATH}; "
        "will regenerate the installed locales with locale-gen"
    )


async def test_locale_upload_seam_writes_only_the_spool():
    host = SimulatedHost(name="h")
    await send_command(host, LocaleUploadCommand(content=LG_ENABLED_FILE, dest=LG_SPOOL))
    assert host.remote_files == {LG_SPOOL: LG_ENABLED_FILE.encode()}
    assert LG_PATH not in host.existing_files
    assert LG_PATH not in host.file_stats


async def test_locale_install_seam_moves_spool_and_refreshes_stats():
    host = SimulatedHost(
        name="h",
        file_stats={LG_PATH: {"user": "root", "group": "root", "mode": 600}},
    )
    await send_command(host, LocaleUploadCommand(content=LG_FILE, dest=LG_SPOOL))
    await send_command(host, LocaleInstallCommand(spool=LG_SPOOL, dest=LG_PATH))
    # The spool is consumed; the dest holds the new bytes.
    assert host.remote_files == {LG_PATH: LG_FILE.encode()}
    assert LG_PATH in host.existing_files
    # cp over an existing file preserves its owner; chmod 0644 + size refresh.
    assert host.file_stats[LG_PATH] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(LG_FILE.encode()),
    }
    # Install without its spool changes nothing (the tolerance arm).
    await send_command(host, LocaleInstallCommand(spool=LG_SPOOL, dest=LG_PATH))
    assert host.remote_files == {LG_PATH: LG_FILE.encode()}


async def test_locale_gen_seam_flips_the_locales_listing():
    host = SimulatedHost(name="h", locales=["C.UTF-8"])
    await send_command(host, LocaleGenCommand(locale=LG_LOCALE, present=True))
    assert host.locales == ["C.UTF-8", LG_LOCALE]
    # Content-based: a repeat regeneration never duplicates the name.
    await send_command(host, LocaleGenCommand(locale=LG_LOCALE, present=True))
    assert host.locales == ["C.UTF-8", LG_LOCALE]
    await send_command(host, LocaleGenCommand(locale=LG_LOCALE, present=False))
    assert host.locales == ["C.UTF-8"]


def locale_deploy(host: Host) -> Generator[OperationRequest]:
    """The example deploy: enable one locale (elevated)."""
    yield op(server.locale, name="Enable French", locale=LG_LOCALE, sudo=True)


def locale_seed_factory(entry: dict) -> Host:
    """A SimulatedHost with a Debian-style locale.gen and no French yet."""
    host = SimulatedHost.from_inventory(entry)
    host.locales = ["C.UTF-8"]
    host.remote_files = {LG_PATH: LG_FILE.encode()}
    return host


async def test_locale_runs_across_hosts_in_lockstep():
    hosts_by_name: dict[str, Host] = {}

    def factory(entry: dict) -> Host:
        host = locale_seed_factory(entry)
        hosts_by_name[entry["host"]] = host
        return host

    plans, report, hosts = await run(INVENTORY, locale_deploy, host_factory=factory)
    assert [p.changed for p in plans["web1"]] == [True]
    assert [p.changed for p in plans["web2"]] == [True]
    assert report is not None
    assert [e.name for e in report.log] == ["Enable French", "Enable French"]
    log = format_log(report)
    # Two privilege levels in one request, one of each per host: the scp
    # half stays unprivileged even under sudo=True, only the install shell
    # and the regeneration elevate. The file text rides the scp payload,
    # so it never appears in the shell log.
    assert log.count(f"    $ scp <locale.gen> -> {LG_SPOOL}") == 2
    install = LocaleInstallCommand(spool=LG_SPOOL, dest=LG_PATH)
    assert log.count(f"    $ sudo {install.shell()}") == 2
    assert log.count("$ sudo locale-gen") == 2
    for host in hosts:
        assert host.locales == ["C.UTF-8", LG_LOCALE]
        assert host.remote_files[LG_PATH] == LG_ENABLED_FILE.encode()
        assert LG_SPOOL not in host.remote_files
    # Reusing the mutated hosts (not a fresh factory) proves the install
    # left every seam coherent: the second run plans nothing.
    plans2, _report2, _hosts2 = await run(
        INVENTORY,
        locale_deploy,
        host_factory=lambda entry: hosts_by_name[entry["host"]],
    )
    assert [p.changed for p in plans2["web1"]] == [False]
    assert [p.changed for p in plans2["web2"]] == [False]


async def test_locale_repeat_request_is_noop_in_apply():
    def twice(host: Host) -> Generator[OperationRequest]:
        yield op(server.locale, name="Enable first", locale=LG_LOCALE, sudo=True)
        yield op(server.locale, name="Enable again", locale=LG_LOCALE, sudo=True)

    host = locale_seed_factory({"host": "h", "connection": {}})
    runtimes, plans = await run_prepare([host], twice)
    # Prepare shares one cache across the two requests and never sends, so
    # both identical requests plan a change...
    assert [p.changed for p in plans["h"]] == [True, True]
    report = await run_apply(runtimes, plans=plans)
    # ...while the per-step cache recreation makes the second a no-op.
    assert [e.changed for e in report.log] == [True, False]
    assert host.locales == ["C.UTF-8", LG_LOCALE]


async def test_locale_enable_then_disable_in_one_deploy():
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(server.locale, name="Enable", locale=LG_LOCALE, sudo=True)
        yield op(
            server.locale, name="Disable", locale=LG_LOCALE, present=False, sudo=True
        )

    host = locale_seed_factory({"host": "h", "connection": {}})
    runtimes, plans = await run_prepare([host], deploy)
    # Prepare shares one cache and never sends, so the disable still sees
    # the pre-enable Locales listing and plans "already disabled"...
    assert [p.changed for p in plans["h"]] == [True, False]
    report = await run_apply(runtimes, plans=plans)
    # ...while apply's per-step cache sees the enable's mutation and really
    # round-trips: the disable comments the line the enable just
    # uncommented, restoring the original text byte for byte.
    assert [e.changed for e in report.log] == [True, True]
    assert host.locales == ["C.UTF-8"]
    assert host.remote_files[LG_PATH] == LG_FILE.encode()
    assert LG_SPOOL not in host.remote_files


async def test_locale_dry_run_sends_nothing():
    _plans, report, hosts = await run(
        INVENTORY, locale_deploy, host_factory=locale_seed_factory, dry=True
    )
    assert report is None
    for host in hosts:
        assert host.locales == ["C.UTF-8"]
        assert host.remote_files == {LG_PATH: LG_FILE.encode()}
        assert LG_PATH not in host.file_stats


# --- server.security_limit ---------------------------------------------------

# The baseline's documented example ("Set nofile limit for all users") supplies
# these fields; its own operation fixture appends root/hard/memlock/unlimited.
SL_DOMAIN = "*"
SL_TYPE = "soft"
SL_ITEM = "nofile"
SL_VALUE = 1024
SL_PATH = "/etc/security/limits.conf"
# The dest-derived spool and the install inner script -- the text inside the
# ``sh -c '...'`` wrapper the install descriptor emits.
SL_SPOOL = _security_limit_spool_path(SL_PATH)
SL_SPOOL_DIR = SL_SPOOL.rsplit("/", 1)[0]
SL_INSTALL_INNER = (
    f"mkdir -p /etc/security && cp {SL_SPOOL} {SL_PATH} && "
    f"chmod 0644 {SL_PATH} && rm -rf {SL_SPOOL_DIR}"
)
# A stock limits.conf: header comment plus a hard limit that never matches
# the anchored soft/nofile/* pattern, so the desired line appends.
SL_FILE = "# /etc/security/limits.conf\nroot hard nofile 65535\n"
SL_LINE = "*\tsoft\tnofile\t1024\n"
SL_DESIRED = SL_FILE + SL_LINE


def test_security_limit_edit_matches_the_baseline_memlock_fixture():
    # The baseline's own operation fixture: domain root, type hard, item memlock,
    # value "unlimited" (a string, as in the fixture), no matching line ->
    # the tab-formatted append. An absent file (None) reads as empty too.
    assert _security_limit_edit("", "root", "hard", "memlock", "unlimited") == (
        "root\thard\tmemlock\tunlimited\n"
    )
    assert _security_limit_edit(None, SL_DOMAIN, SL_TYPE, SL_ITEM, SL_VALUE) == SL_LINE


def test_security_limit_edit_appends_when_nothing_matches():
    assert _security_limit_edit(SL_FILE, SL_DOMAIN, SL_TYPE, SL_ITEM, SL_VALUE) == (
        SL_DESIRED
    )
    # Untouched lines, comments and their order survive; a repeat
    # application is a fixpoint.
    assert _security_limit_edit(SL_DESIRED, SL_DOMAIN, SL_TYPE, SL_ITEM, SL_VALUE) == (
        SL_DESIRED
    )


def test_security_limit_edit_normalizes_stale_and_valueless_lines():
    # The value is NOT part of the pattern (baseline parity): a stale value
    # line MATCHES and is replaced wholesale, not kept beside an append...
    assert _security_limit_edit(
        "* soft nofile 65535\n", SL_DOMAIN, SL_TYPE, SL_ITEM, SL_VALUE
    ) == SL_LINE
    # ...and the ``.*$`` tail means a line without a value still matches.
    assert _security_limit_edit(
        "*   soft\tnofile\n", SL_DOMAIN, SL_TYPE, SL_ITEM, SL_VALUE
    ) == SL_LINE
    # The baseline's sed replaces ALL matching lines, not just the first.
    current = "* soft nofile 65535\n*\tsoft\tnofile\t999\n"
    assert _security_limit_edit(current, SL_DOMAIN, SL_TYPE, SL_ITEM, SL_VALUE) == (
        "*\tsoft\tnofile\t1024\n*\tsoft\tnofile\t1024\n"
    )


def test_security_limit_edit_anchor_and_escape_boundaries():
    # The anchored pattern does not match a commented-out line; the baseline
    # appends beside it.
    assert _security_limit_edit(
        "# * soft nofile 1024\n", SL_DOMAIN, SL_TYPE, SL_ITEM, SL_VALUE
    ) == ("# * soft nofile 1024\n" + SL_LINE)
    # The domain is part of the pattern: another domain's line survives.
    assert _security_limit_edit(
        "root\tsoft\tnofile\t1024\n", SL_DOMAIN, SL_TYPE, SL_ITEM, SL_VALUE
    ) == ("root\tsoft\tnofile\t1024\n" + SL_LINE)
    # The baseline's ``.*$`` tail: an item prefix (nofilex) matches too.
    assert _security_limit_edit(
        "* soft nofilex 5\n", SL_DOMAIN, SL_TYPE, SL_ITEM, SL_VALUE
    ) == SL_LINE
    # The documented hardening: re.escape keeps the ``*`` domain literal,
    # where the baseline's raw BRE interpolation makes it a quantifier -- the
    # near-miss line survives and the exact line appends.
    assert _security_limit_edit(
        ". soft nofile 5\n", SL_DOMAIN, SL_TYPE, SL_ITEM, SL_VALUE
    ) == (". soft nofile 5\n" + SL_LINE)


def test_security_limit_edit_fixpoint_and_trailing_newline():
    # The result always ends in one trailing newline, so a file missing its
    # final newline is rewritten once even when the line itself is correct.
    once = _security_limit_edit(
        SL_DESIRED.rstrip("\n"), SL_DOMAIN, SL_TYPE, SL_ITEM, SL_VALUE
    )
    assert once == SL_DESIRED
    assert _security_limit_edit(once, SL_DOMAIN, SL_TYPE, SL_ITEM, SL_VALUE) == once


def test_security_limit_upload_command_rendering():
    upload = SecurityLimitUploadCommand(content=SL_DESIRED, dest=SL_SPOOL)
    # The transfer is a non-shell descriptor: its loggable form is __str__,
    # and command_shell never prefixes it with sudo/su -- transfers ride the
    # login user's scp channel regardless of the request's elevation.
    assert str(upload) == f"scp <limits.conf> -> {SL_SPOOL}"
    assert command_shell(upload, sudo=True) == f"scp <limits.conf> -> {SL_SPOOL}"
    assert command_shell(upload, su=True) == f"scp <limits.conf> -> {SL_SPOOL}"
    # The content field is a plain str: hashable and value-equal across the
    # prepare/apply phases (the determinism guard compares descriptors).
    assert hash(upload) == hash(SecurityLimitUploadCommand(content=SL_DESIRED, dest=SL_SPOOL))


def test_security_limit_install_command_shell_rendering():
    install = SecurityLimitInstallCommand(spool=SL_SPOOL, dest=SL_PATH)
    assert install.shell() == f"sh -c {shlex.quote(SL_INSTALL_INNER)}"
    # Sudo elevates the whole sh -c, so every step of the && chain -- cp,
    # chmod and the spool cleanup -- runs as the elevated shell.
    assert command_shell(install, sudo=True) == f"sudo {install.shell()}"
    assert command_shell(install, su=True) == f"su -c {shlex.quote(install.shell())}"
    # Quoting proof: a space in the DIRECTORY (shlex.quote leaves space-free
    # paths bare). Recover the inner script with shlex.split, never a raw
    # substring of the quoted text.
    path = "/etc dir/limits conf"
    spool = _security_limit_spool_path(path)
    spool_dir = spool.rsplit("/", 1)[0]
    inner = shlex.split(SecurityLimitInstallCommand(spool=spool, dest=path).shell())[2]
    assert inner.startswith("mkdir -p '/etc dir' && ")
    assert inner.endswith(f"&& rm -rf {spool_dir}")


def test_security_limit_spool_path_is_dest_derived_and_deterministic():
    # Same dest -> same spool in both phases; different dest -> different
    # spool, and the name keeps the family prefix.
    assert _security_limit_spool_path(SL_PATH) == SL_SPOOL
    assert _security_limit_spool_path(SL_PATH) == _security_limit_spool_path(SL_PATH)
    assert _security_limit_spool_path("/etc/security/limits.conf.custom") != SL_SPOOL
    digest = hashlib.sha1(SL_PATH.encode()).hexdigest()[:16]
    assert SL_SPOOL == f"/tmp/reemote-server-security-limit-{digest}/security-limit"


async def test_security_limit_appends_and_plans_the_pair():
    host = SimulatedHost(name="h", remote_files={SL_PATH: SL_FILE.encode()})
    ctx, commands = await materialize(
        host,
        op(
            server.security_limit,
            name="Set nofile limit for all users",
            domain=SL_DOMAIN,
            limit_type=SL_TYPE,
            item=SL_ITEM,
            value=SL_VALUE,
            sudo=True,
        ),
    )
    assert ctx.changed
    assert commands == [
        SecurityLimitUploadCommand(content=SL_DESIRED, dest=SL_SPOOL),
        SecurityLimitInstallCommand(spool=SL_SPOOL, dest=SL_PATH),
    ]
    assert ctx.reason == (
        f"security limit {SL_DOMAIN} {SL_TYPE} {SL_ITEM} "
        f"will be set to {SL_VALUE} in {SL_PATH}"
    )


async def test_security_limit_creates_an_absent_file():
    # The baseline's echo append creates the file; None content reads as empty.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host,
        op(
            server.security_limit,
            domain=SL_DOMAIN,
            limit_type=SL_TYPE,
            item=SL_ITEM,
            value=SL_VALUE,
        ),
    )
    assert ctx.changed
    assert commands == [
        SecurityLimitUploadCommand(content=SL_LINE, dest=SL_SPOOL),
        SecurityLimitInstallCommand(spool=SL_SPOOL, dest=SL_PATH),
    ]


async def test_security_limit_noop_when_the_line_is_already_set():
    # The content-level fixpoint -- the baseline's files.line no-op when a
    # matching line already reads exactly the replacement.
    host = SimulatedHost(name="h", remote_files={SL_PATH: SL_DESIRED.encode()})
    ctx, commands = await materialize(
        host,
        op(
            server.security_limit,
            domain=SL_DOMAIN,
            limit_type=SL_TYPE,
            item=SL_ITEM,
            value=SL_VALUE,
        ),
    )
    assert not ctx.changed
    assert commands == []
    assert ctx.reason == (
        f"security limit {SL_DOMAIN} {SL_TYPE} {SL_ITEM} "
        f"already set to {SL_VALUE} in {SL_PATH}"
    )


async def test_security_limit_normalizes_a_stale_value():
    # The value is not in the pattern: a stale matching line is rewritten
    # in place through the same spool pair, not appended beside.
    host = SimulatedHost(name="h", remote_files={SL_PATH: b"* soft nofile 65535\n"})
    ctx, commands = await materialize(
        host,
        op(
            server.security_limit,
            domain=SL_DOMAIN,
            limit_type=SL_TYPE,
            item=SL_ITEM,
            value=SL_VALUE,
        ),
    )
    assert ctx.changed
    assert commands == [
        SecurityLimitUploadCommand(content=SL_LINE, dest=SL_SPOOL),
        SecurityLimitInstallCommand(spool=SL_SPOOL, dest=SL_PATH),
    ]


async def test_security_limit_rewrites_once_for_the_missing_newline():
    # The documented deviation: the desired content always ends in one
    # trailing newline, so a correct line in a newline-less file is still
    # rewritten once -- and the second pass is the fixpoint no-op.
    host = SimulatedHost(
        name="h", remote_files={SL_PATH: SL_DESIRED.rstrip("\n").encode()}
    )
    ctx, commands = await materialize(
        host,
        op(
            server.security_limit,
            domain=SL_DOMAIN,
            limit_type=SL_TYPE,
            item=SL_ITEM,
            value=SL_VALUE,
        ),
    )
    assert ctx.changed
    assert commands[0] == SecurityLimitUploadCommand(content=SL_DESIRED, dest=SL_SPOOL)


async def test_security_limit_absorbs_unknown_kwargs():
    # ``**kwargs`` mirrors the baseline's global-arguments slot; op() already
    # strips name/sudo/su, the rest is ignored.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host,
        op(
            server.security_limit,
            domain=SL_DOMAIN,
            limit_type=SL_TYPE,
            item=SL_ITEM,
            value=SL_VALUE,
            future_flag=True,
        ),
    )
    assert ctx.changed
    assert commands[0] == SecurityLimitUploadCommand(content=SL_LINE, dest=SL_SPOOL)


async def test_security_limit_reads_the_fact_over_the_real_transport():
    # The seam short-circuit means the rest of the suite never executes the
    # marker parse: feed the real test -e/marker/cat stdout through a canned
    # host and assert the operation reads exactly AuthorizedKeysFile's
    # command for the fixed limits path.
    host = CannedShellHost(f"{AuthorizedKeysFile.MARKER}\n{SL_FILE}")
    ctx, commands = await materialize(
        host,
        op(
            server.security_limit,
            domain=SL_DOMAIN,
            limit_type=SL_TYPE,
            item=SL_ITEM,
            value=SL_VALUE,
        ),
    )
    assert ctx.changed
    assert commands[0] == SecurityLimitUploadCommand(content=SL_DESIRED, dest=SL_SPOOL)
    assert host.shells == [AuthorizedKeysFile.command(SL_PATH)]
    # No marker on stdout: the file is absent (None), so the line is created.
    absent = CannedShellHost("")
    ctx2, commands2 = await materialize(
        absent,
        op(
            server.security_limit,
            domain=SL_DOMAIN,
            limit_type=SL_TYPE,
            item=SL_ITEM,
            value=SL_VALUE,
        ),
    )
    assert ctx2.changed
    assert commands2[0] == SecurityLimitUploadCommand(content=SL_LINE, dest=SL_SPOOL)


async def test_security_limit_upload_seam_writes_only_the_spool():
    host = SimulatedHost(name="h")
    await send_command(
        host, SecurityLimitUploadCommand(content=SL_DESIRED, dest=SL_SPOOL)
    )
    assert host.remote_files == {SL_SPOOL: SL_DESIRED.encode()}
    assert SL_PATH not in host.existing_files
    assert SL_PATH not in host.file_stats


async def test_security_limit_install_seam_moves_spool_and_refreshes_stats():
    host = SimulatedHost(
        name="h",
        file_stats={SL_PATH: {"user": "root", "group": "root", "mode": 600}},
        remote_files={SL_PATH: SL_FILE.encode()},
    )
    await send_command(
        host, SecurityLimitUploadCommand(content=SL_DESIRED, dest=SL_SPOOL)
    )
    await send_command(host, SecurityLimitInstallCommand(spool=SL_SPOOL, dest=SL_PATH))
    # The spool is consumed; the dest holds the new bytes.
    assert host.remote_files == {SL_PATH: SL_DESIRED.encode()}
    assert SL_PATH in host.existing_files
    # cp over an existing file preserves its owner; chmod 0644 + size refresh.
    assert host.file_stats[SL_PATH] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(SL_DESIRED.encode()),
    }
    # Content-based: a repeat upload+install never duplicates the line.
    await send_command(
        host, SecurityLimitUploadCommand(content=SL_DESIRED, dest=SL_SPOOL)
    )
    await send_command(host, SecurityLimitInstallCommand(spool=SL_SPOOL, dest=SL_PATH))
    assert host.remote_files == {SL_PATH: SL_DESIRED.encode()}
    # Install without its spool changes nothing (the tolerance arm).
    await send_command(host, SecurityLimitInstallCommand(spool=SL_SPOOL, dest=SL_PATH))
    assert host.remote_files == {SL_PATH: SL_DESIRED.encode()}


async def test_security_limit_install_seam_without_prior_stats_seeds_root_row():
    # Descriptor-only install against a fresh host: a minimal root-owned
    # mode-644 row (the file the elevated cp created).
    host = SimulatedHost(name="h")
    await send_command(host, SecurityLimitUploadCommand(content=SL_LINE, dest=SL_SPOOL))
    await send_command(host, SecurityLimitInstallCommand(spool=SL_SPOOL, dest=SL_PATH))
    assert host.file_stats[SL_PATH] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(SL_LINE.encode()),
    }
    assert SL_PATH in host.existing_files


def security_limit_deploy(host: Host) -> Generator[OperationRequest]:
    """The example deploy: set the nofile limit for all users (elevated)."""
    yield op(
        server.security_limit,
        name="Set nofile limit for all users",
        domain=SL_DOMAIN,
        limit_type=SL_TYPE,
        item=SL_ITEM,
        value=SL_VALUE,
        sudo=True,
    )


def security_limit_seed_factory(entry: dict) -> Host:
    """A SimulatedHost with a stock limits.conf and no wildcard limit yet."""
    host = SimulatedHost.from_inventory(entry)
    host.remote_files = {SL_PATH: SL_FILE.encode()}
    return host


async def test_security_limit_runs_across_hosts_in_lockstep():
    hosts_by_name: dict[str, Host] = {}

    def factory(entry: dict) -> Host:
        host = security_limit_seed_factory(entry)
        hosts_by_name[entry["host"]] = host
        return host

    plans, report, hosts = await run(
        INVENTORY, security_limit_deploy, host_factory=factory
    )
    assert [p.changed for p in plans["web1"]] == [True]
    assert [p.changed for p in plans["web2"]] == [True]
    assert report is not None
    assert [e.name for e in report.log] == [
        "Set nofile limit for all users",
        "Set nofile limit for all users",
    ]
    log = format_log(report)
    # Two privilege levels in one request, one of each per host: the scp
    # half stays unprivileged even under sudo=True, only the install shell
    # elevates. The file text rides the scp payload, so it never appears in
    # the shell log.
    assert log.count(f"    $ scp <limits.conf> -> {SL_SPOOL}") == 2
    install = SecurityLimitInstallCommand(spool=SL_SPOOL, dest=SL_PATH)
    assert log.count(f"    $ sudo {install.shell()}") == 2
    for host in hosts:
        assert host.remote_files[SL_PATH] == SL_DESIRED.encode()
        assert SL_SPOOL not in host.remote_files
    # Reusing the mutated hosts (not a fresh factory) proves the install
    # left every seam coherent: the second run plans nothing.
    plans2, _report2, _hosts2 = await run(
        INVENTORY,
        security_limit_deploy,
        host_factory=lambda entry: hosts_by_name[entry["host"]],
    )
    assert [p.changed for p in plans2["web1"]] == [False]
    assert [p.changed for p in plans2["web2"]] == [False]


async def test_security_limit_repeat_request_is_noop_in_apply():
    def twice(host: Host) -> Generator[OperationRequest]:
        yield op(
            server.security_limit,
            name="Set first",
            domain=SL_DOMAIN,
            limit_type=SL_TYPE,
            item=SL_ITEM,
            value=SL_VALUE,
            sudo=True,
        )
        yield op(
            server.security_limit,
            name="Set again",
            domain=SL_DOMAIN,
            limit_type=SL_TYPE,
            item=SL_ITEM,
            value=SL_VALUE,
            sudo=True,
        )

    host = security_limit_seed_factory({"host": "h", "connection": {}})
    runtimes, plans = await run_prepare([host], twice)
    # Prepare shares one cache across the two requests and never sends, so
    # both identical requests plan a change...
    assert [p.changed for p in plans["h"]] == [True, True]
    report = await run_apply(runtimes, plans=plans)
    # ...while the per-step cache recreation makes the second a no-op.
    assert [e.changed for e in report.log] == [True, False]
    assert host.remote_files[SL_PATH] == SL_DESIRED.encode()


async def test_security_limit_set_then_change_value_in_one_deploy():
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            server.security_limit,
            name="Set 1024",
            domain=SL_DOMAIN,
            limit_type=SL_TYPE,
            item=SL_ITEM,
            value=SL_VALUE,
            sudo=True,
        )
        yield op(
            server.security_limit,
            name="Raise to 2048",
            domain=SL_DOMAIN,
            limit_type=SL_TYPE,
            item=SL_ITEM,
            value=2048,
            sudo=True,
        )

    host = security_limit_seed_factory({"host": "h", "connection": {}})
    runtimes, plans = await run_prepare([host], deploy)
    # Prepare shares one cache and never sends, so both requests see the
    # stock file and plan a change...
    assert [p.changed for p in plans["h"]] == [True, True]
    report = await run_apply(runtimes, plans=plans)
    # ...while apply's per-step cache sees the first install and really
    # normalizes the just-written line to the new value (the value is not
    # part of the match pattern).
    assert [e.changed for e in report.log] == [True, True]
    assert host.remote_files[SL_PATH] == (SL_FILE + "*\tsoft\tnofile\t2048\n").encode()
    assert SL_SPOOL not in host.remote_files


async def test_security_limit_dry_run_sends_nothing():
    _plans, report, hosts = await run(
        INVENTORY, security_limit_deploy, host_factory=security_limit_seed_factory, dry=True
    )
    assert report is None
    for host in hosts:
        assert host.remote_files == {SL_PATH: SL_FILE.encode()}
        assert SL_PATH not in host.file_stats

