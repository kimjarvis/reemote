# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the server module: systemd facts, ``Arch``, ``service`` and ``wait_service``.

Covers ``reemote5.modules.server.server``: the ``Arch`` fact, the
``AuthorizedKeys`` fact (pyinfra's own fixtures over the real-transport
path, tilde-username validation, and the ``SimulatedHost`` seam), the
``Date`` fact (pyinfra's own fixture over the real-transport path, offset
parsing, and the ``SimulatedHost`` seam), the ``Groups`` fact (pyinfra's own
fixture over the real-transport path, ``/etc/group`` parsing edge cases, and
the ``SimulatedHost`` seam), the ``HasGui`` fact (pyinfra's own fixtures over
the real-transport path, session-binary parsing edge cases, and the
``SimulatedHost`` seam), the ``Home`` fact (pyinfra's own fixtures over the
real-transport path, unexpanded-tilde handling, tilde-username validation,
and the ``SimulatedHost`` seam), the ``Hostname`` fact (pyinfra's own
fixture over the real-transport path, parsing edge cases, and the
``SimulatedHost`` seam), the ``Kernel`` fact (pyinfra's own fixture over
the real-transport path, parsing edge cases, and the ``SimulatedHost``
seam), the ``KernelModules`` fact (pyinfra's own Linux and FreeBSD fixtures
over the real-transport path, kernel-dispatched command and parser edge
cases, and the ``SimulatedHost`` seam), the ``KernelVersion`` fact
(pyinfra's own fixture over the real-transport path, parsing edge cases,
and the ``SimulatedHost`` seam), the ``Last`` fact (pyinfra's own
fixtures over the real-transport path, login-record parsing edge cases,
and the ``SimulatedHost`` seam), the ``LinuxDistribution`` fact (pyinfra's
own nine fixtures over the real-transport path, release-file parsing edge
cases, and the ``SimulatedHost`` seam), the ``LinuxGui`` fact (pyinfra's own
fixture over the real-transport path, session-binary parsing edge cases,
agreement with ``HasGui``, and the ``SimulatedHost`` seam), the
``LinuxName`` fact (pyinfra's own fixture and the nine
``LinuxDistribution`` fixtures over the real-transport path, agreement
with ``LinuxDistribution``, and the shared ``SimulatedHost`` seam), the
``Locales`` fact (pyinfra's documented example over the real-transport
path, ``utf8`` normalization edge cases, and the ``SimulatedHost`` seam),
the ``LsbRelease`` fact (pyinfra's own fixture over the real-transport
path, key-normalization parsing edge cases, and the ``SimulatedHost``
seam), the ``Mounts`` fact (pyinfra's own Linux and OpenBSD fixtures over
the real-transport path, kernel-dispatched command and parser edge cases
including the FreeBSD JSON branch, and the ``SimulatedHost`` seam), the
``Path`` fact (canned ``echo $PATH`` output over the real-transport path,
parsing edge cases, and the ``SimulatedHost`` seam), the ``Processes`` fact
(pyinfra's own eleven Linux, FreeBSD, BusyBox and Darwin fixtures over the
real-transport path, kernel/busybox-dispatched command and parser edge
cases, and the ``SimulatedHost`` seam), the ``Timezone`` fact (pyinfra's own
fixture over the real-transport path, parsing edge cases, and the
``SimulatedHost`` seam), the ``Uptime`` fact (pyinfra's own Linux and macOS
fixtures over the real-transport path, kernel-dispatched command and parser
edge cases, and the ``SimulatedHost`` seam), the ``Users`` fact (pyinfra's
own mixed fixture over the real-transport path, passwd/lastlog parsing edge
cases, and the ``SimulatedHost`` seam), the ``Which`` fact (pyinfra's own
fixtures over the real-transport path, name quoting and parsing edge cases,
and the ``SimulatedHost`` seam) and the
``SystemdServiceState`` and ``SystemdServiceEnabled`` facts (over the
real-transport path, parsing canned shell output, and through the
``SimulatedHost`` seams), the ``group`` operation's option set and its
``GroupAddCommand``/``GroupDelCommand`` descriptors (pyinfra's own eight
operation fixtures replayed over the ``SimulatedHost`` seams, idempotence,
the kernel dispatch and the end-to-end lockstep run), the ``hostname``
operation and its ``SetHostnameCommand`` descriptor (pyinfra's own six
operation fixtures replayed over the ``SimulatedHost`` seams with the
dropped ``hostname_file`` upload step, the ``Which`` dispatch between
``hostnamectl`` and the bare ``hostname`` command, idempotence and the
end-to-end lockstep run), the ``kill`` operation and its ``KillCommand``
descriptor (pyinfra's own three operation fixtures replayed over the
``SimulatedHost`` seam, the stateless always-changed behaviour, the
``processes`` seam mutation, the snapshot-factory pattern, the determinism
guard against live-state branching and the end-to-end lockstep run), the
``modprobe`` operation and its ``ModprobeCommand`` descriptor (pyinfra's own
nine operation fixtures replayed over the ``SimulatedHost`` seam, the
load/unload partition against ``KernelModules``, the ``kernel_modules`` seam
mutation, the determinism guard against live-state branching and the
end-to-end lockstep run), the ``mount`` operation and its ``MountCommand``
descriptor (pyinfra's own fifteen operation fixtures replayed over the
``SimulatedHost`` seam, the lookup-by-device fallback and the BSD remount
dispatch against ``Mounts``/``Kernel``, the ``mounts`` seam mutation, the
determinism guard against live-state branching and the end-to-end lockstep
run), the ``timezone`` operation and its ``SetTimezoneCommand`` descriptor
(pyinfra's own noop and set_timedatectl fixtures replayed over the
``SimulatedHost`` seams, the dropped non-systemd fallback raising
``OperationError`` instead, idempotence and the end-to-end lockstep run),
the
``service``
operation's option set and its ``SystemctlCommand`` descriptors,
and the waiting design: the ``WaitCommand`` base, the generic
``WaitForFactCommand`` poll loop (already met, met after several polls, and
timeout -- all without real sleeping), the ``send_command`` dispatch onto
``wait``, and the end-to-end ``wait_service`` behaviour through ``run`` with
``SimulatedHost``: the wait is planned unconditionally so the request
sequence is identical in both phases (determinism), it passes when the unit
is active and aborts the run with ``WaitTimeoutError`` when it is not.
"""

from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from reemote5.commands import WaitCommand, WaitTimeoutError
from reemote5.core import FactCache, OpContext, OperationError, OperationRequest, op
from reemote5.drivers import (
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
from reemote5.hosts import CommandError, CommandNotFoundError, Host
from reemote5.modules import server
from reemote5.modules.server.server import (
    Arch,
    AuthorizedKeys,
    Date,
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
    Locales,
    LsbRelease,
    ModprobeCommand,
    MountCommand,
    Mounts,
    Path,
    Processes,
    SetHostnameCommand,
    SetTimezoneCommand,
    Sysctl,
    SystemctlCommand,
    SystemdServiceEnabled,
    SystemdServiceState,
    Timezone,
    Uptime,
    User,
    Users,
    WaitForFactCommand,
    Which,
    is_active,
    unit_name,
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


class CannedKernelHost(CannedShellHost):
    """A CannedShellHost that answers ``uname -s`` with a fixed kernel name.

    ``KernelModules`` first reads the ``Kernel`` fact, so the canned host
    must answer two different shells: the kernel name for ``uname -s`` and
    the module listing for the command that kernel selects.
    """

    def __init__(self, kernel: str, stdout: str):
        super().__init__(stdout)
        self._kernel = kernel

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        if shell == Kernel.COMMAND:
            self.shells.append(shell)
            return self._kernel
        return await super().run_shell(shell)


class CannedProcessesHost(CannedKernelHost):
    """A CannedKernelHost that also answers the BusyBox probe.

    ``Processes`` reads the ``Kernel`` fact, probes ``command -v busybox``
    through the ``Which`` fact on non-BSD kernels and then runs the ps
    listing those answers select, so the canned host must answer three
    different shells: the kernel name for ``uname -s``, the probe's
    path-or-empty answer, and the ps output.
    """

    def __init__(self, kernel: str, busybox: str, stdout: str):
        super().__init__(kernel, stdout)
        self._busybox = busybox

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        if shell == Which.command("busybox"):
            self.shells.append(shell)
            return self._busybox
        return await super().run_shell(shell)


async def materialize(host: Host, request: OperationRequest):
    """Run an operation against a fresh cache; return ``(ctx, commands)``."""
    ctx = OpContext(host=host, facts=FactCache(host, phase="test"))
    commands = [cmd async for cmd in request.func(ctx, **request.kwargs)]
    return ctx, commands


# --- Unit names and facts ----------------------------------------------------


def test_unit_name_appends_service_to_a_bare_name():
    assert unit_name("httpd") == "httpd.service"


def test_unit_name_keeps_a_known_suffix():
    assert unit_name("httpd.service") == "httpd.service"
    assert unit_name("logrotate.timer") == "logrotate.timer"


async def test_arch_parses_uname_m():
    # pyinfra's own fact fixture (tests/facts/server.Arch/arch.json):
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


async def test_systemd_service_state_parses_systemctl_show():
    host = CannedShellHost("active\n")
    assert await SystemdServiceState.process(host, "httpd") == "active"
    assert host.shells == ["systemctl show -p ActiveState --value httpd.service"]


async def test_systemd_service_state_is_none_on_empty_output():
    assert await SystemdServiceState.process(CannedShellHost(""), "httpd") is None


async def test_systemd_service_state_reads_the_simulated_seam():
    host = SimulatedHost(name="h", services={"httpd.service": "activating"})
    assert await SystemdServiceState.process(host, "httpd") == "activating"
    assert await SystemdServiceState.process(host, "nginx") is None


async def test_systemd_service_enabled_parses_unit_file_state():
    assert await SystemdServiceEnabled.process(CannedShellHost("enabled\n"), "httpd")
    assert await SystemdServiceEnabled.process(CannedShellHost("static\n"), "httpd")
    assert not await SystemdServiceEnabled.process(
        CannedShellHost("disabled\n"), "httpd"
    )
    assert not await SystemdServiceEnabled.process(CannedShellHost(""), "httpd")


async def test_systemd_service_enabled_reads_the_simulated_seam():
    host = SimulatedHost(name="h", enabled_services={"httpd.service"})
    assert await SystemdServiceEnabled.process(host, "httpd")
    assert not await SystemdServiceEnabled.process(host, "nginx")


# --- AuthorizedKeys ----------------------------------------------------------

# pyinfra's documented example keys (tests/facts/server.AuthorizedKeys/
# default_path.json), reused verbatim for parity.
KEY1 = "ssh-ed25519 AAAAC3Nz...key1 user@host"
KEY2 = "ssh-rsa AAAAB3Nz...key2 other@host"


async def test_authorized_keys_parses_the_default_path():
    # pyinfra's default_path.json fixture: comment, blank and whitespace-only
    # lines are skipped; the file's order is preserved.
    host = CannedShellHost(f"# some comment\n\n{KEY1}\n   \n{KEY2}\n")
    assert await AuthorizedKeys.process(host, "alice") == [KEY1, KEY2]
    assert host.shells == ["cat ~alice/.ssh/authorized_keys 2>/dev/null || true"]


async def test_authorized_keys_custom_path_with_empty_output():
    # pyinfra's custom_path.json fixture: output [] -> fact [].
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
    # consulted (pyinfra only validates in the tilde branch).
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


async def test_date_parses_pyintras_fixture():
    # pyinfra's own fact fixture (tests/facts/server.Date/date.json):
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
    # pyinfra's ``default = datetime.now`` fallback is deliberately dropped:
    # a failed command reads as None, never as the control machine's clock.
    assert await Date.process(CannedShellHost("")) is None


async def test_date_reads_the_simulated_seam():
    clock = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
    host = SimulatedHost(name="h", date=clock)
    assert await Date.process(host) == clock
    # The seam defaults to pyinfra's fixture timestamp: a simulated machine
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


# --- Groups --------------------------------------------------------------------


async def test_groups_parses_pyintras_fixture():
    # pyinfra's own fact fixture (tests/facts/server.Groups/groups.json):
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
    # file's order is preserved, with no dedup (pyinfra's process).
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


async def test_has_gui_parses_pyintras_gui_present_fixture():
    # pyinfra's own fact fixture (tests/facts/server.HasGui/gui_present.json):
    # command "ls /usr/bin/*session || true", output ["/usr/bin/gnome-session"]
    # -> fact True.
    host = CannedShellHost("/usr/bin/gnome-session\n")
    assert await HasGui.process(host) is True
    assert host.shells == ["ls /usr/bin/*session || true"]


async def test_has_gui_parses_pyintras_gui_absent_fixture():
    # pyinfra's gui_absent.json fixture: output [] -> fact False (the ls
    # error is swallowed by ``|| true``, so a headless host lists nothing).
    host = CannedShellHost("")
    assert await HasGui.process(host) is False
    assert host.shells == ["ls /usr/bin/*session || true"]


def test_has_gui_parse_matches_every_known_session_binary():
    # All five binaries from pyinfra's LinuxGui.known_gui_binaries count.
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
    # Lines are matched exactly (pyinfra does not strip them): an unknown
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


async def test_home_parses_pyintras_home_fixture():
    # pyinfra's own fact fixture (tests/facts/server.Home/home.json):
    # command "echo ~", output ["/home/pyinfra"] -> fact "/home/pyinfra".
    host = CannedShellHost("/home/pyinfra\n")
    assert await Home.process(host) == "/home/pyinfra"
    assert host.shells == ["echo ~"]


async def test_home_parses_pyintras_root_fixture():
    # pyinfra's root.json fixture: arg ["root"], command "echo ~root",
    # output ["/root"] -> fact "/root".
    host = CannedShellHost("/root\n")
    assert await Home.process(host, "root") == "/root"
    assert host.shells == ["echo ~root"]


async def test_home_parses_pyintras_some_user_fixture():
    # pyinfra's some_user.json fixture: arg ["some_user"], command
    # "echo ~some_user", output ["/home/some_user"] -> fact "/home/some_user".
    host = CannedShellHost("/home/some_user\n")
    assert await Home.process(host, "some_user") == "/home/some_user"
    assert host.shells == ["echo ~some_user"]


def test_home_parse_takes_the_first_non_blank_line():
    assert Home.parse(["", "  /home/pyinfra  ", "ignored"]) == "/home/pyinfra"
    assert Home.parse([]) is None


def test_home_parse_maps_an_unexpanded_tilde_to_none():
    # The shell echoes ``~user`` back verbatim for an unknown user; pyinfra
    # returns that literal string, reemote5 deliberately reads it as None.
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


async def test_hostname_parses_pyintras_fixture():
    # pyinfra's own fact fixture (tests/facts/server.Hostname/hostname.json):
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
    # The seam defaults to pyinfra's fixture hostname: a simulated machine
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


async def test_kernel_parses_pyintras_fixture():
    # pyinfra's own fact fixture (tests/facts/server.Os/os.json -- Kernel is
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


async def test_kernel_version_parses_pyintras_fixture():
    # pyinfra's own fact fixture (tests/facts/server.OsVersion/os_version.json
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


async def test_kernel_modules_parses_pyintras_linux_fixture():
    # pyinfra's own fact fixture (tests/facts/server.KernelModules/
    # linux_modules.json): Kernel "Linux", command
    # "! test -f /proc/modules || cat /proc/modules", five rows.
    host = CannedKernelHost(
        "Linux",
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
    assert host.shells == ["uname -s", "! test -f /proc/modules || cat /proc/modules"]


async def test_kernel_modules_parses_pyintras_freebsd_fixture():
    # pyinfra's freebsd_modules.json fixture: Kernel "FreeBSD", command
    # "kldstat | tail +2" (the header row already dropped), five rows.
    host = CannedKernelHost(
        "FreeBSD",
        """ 1   46 0xffffffff80200000  1f4dad0 kernel
 2    1 0xffffffff8214f000   620c10 zfs.ko
 3    1 0xffffffff82770000     8808 cryptodev.ko
 4    1 0xffffffff83018000     3570 fdescfs.ko
 5    1 0xffffffff8301c000     21e8 hms.ko
""",
    )
    assert await KernelModules.process(host) == {
        "kernel": {
            "address": "0xffffffff80200000",
            "id": "1",
            "refs": "46",
            "size": "1f4dad0",
        },
        "zfs.ko": {
            "address": "0xffffffff8214f000",
            "id": "2",
            "refs": "1",
            "size": "620c10",
        },
        "cryptodev.ko": {
            "address": "0xffffffff82770000",
            "id": "3",
            "refs": "1",
            "size": "8808",
        },
        "fdescfs.ko": {
            "address": "0xffffffff83018000",
            "id": "4",
            "refs": "1",
            "size": "3570",
        },
        "hms.ko": {
            "address": "0xffffffff8301c000",
            "id": "5",
            "refs": "1",
            "size": "21e8",
        },
    }
    assert host.shells == ["uname -s", "kldstat | tail +2"]


def test_kernel_modules_parse_linux_edge_cases():
    # A multi-dependency row with a trailing comma: empty entries are
    # dropped, only instances becomes an int, the state is kept verbatim
    # (Loading, not just Live) and the trailing address field is ignored.
    assert KernelModules.parse(
        ["bridge 245760 2 stp,llc, Loading 0x0000000000000000"], "Linux"
    ) == {
        "bridge": {
            "size": "245760",
            "instances": 2,
            "state": "Loading",
            "depends": ["stp", "llc"],
        }
    }
    assert KernelModules.parse([], "Linux") == {}


def test_kernel_modules_parse_freebsd_splits_on_whitespace():
    # kldstat rows are padded with runs of spaces; split(None) collapses
    # them, and all four info fields stay raw (hex) strings.
    assert KernelModules.parse(
        [" 2    1 0xffffffff8214f000   620c10 zfs.ko"], "FreeBSD"
    ) == {
        "zfs.ko": {
            "address": "0xffffffff8214f000",
            "id": "2",
            "refs": "1",
            "size": "620c10",
        }
    }
    assert KernelModules.parse([], "FreeBSD") == {}


def test_kernel_modules_command_dispatches_on_the_kernel():
    assert KernelModules.command("FreeBSD") == "kldstat | tail +2"
    assert KernelModules.command(" FreeBSD ") == "kldstat | tail +2"
    # Anything else -- including a failed `uname -s` ("") -- takes the
    # /proc/modules branch, exactly like pyinfra.
    for kernel in ("Linux", "Darwin", ""):
        assert (
            KernelModules.command(kernel)
            == "! test -f /proc/modules || cat /proc/modules"
        )


async def test_kernel_modules_is_empty_on_empty_output():
    # The ``! test -f`` guard short-circuits when /proc/modules is absent,
    # so a failed read maps to {} (pyinfra's ``default = dict``).
    assert await KernelModules.process(CannedKernelHost("Linux", "")) == {}


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


async def test_last_parses_pyintras_full_fixture():
    # pyinfra's own fact fixture (tests/facts/server.Last/last_full.json):
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


def test_last_command_matches_pyinfra():
    # The fixture's command field: util-linux -Fwi, FreeBSD -w, then the
    # bare busybox-compatible fallback, all errors swallowed.
    assert (
        Last.COMMAND
        == "last -Fwi 2>/dev/null || last -w 2>/dev/null || last 2>/dev/null || true"
    )


async def test_last_parses_pyintras_freebsd_fixture():
    # pyinfra's last_freebsd.json fixture: short (no -F) timestamps and a
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
    # pyinfra's last_alpine_empty.json fixture: busybox last prints only a
    # header row, which is skipped -> [].
    host = CannedShellHost(
        "USER       TTY            HOST               LOGIN        TIME\n"
    )
    assert await Last.process(host) == []


def test_last_parses_pyintras_documented_example():
    # pyinfra's docstring example result: the parser joins time tokens with
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
    # so empty stdout maps to [] (pyinfra's default = list).
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

# pyinfra's own fact fixtures (tests/facts/server.LinuxDistribution/*.json),
# reused verbatim for parity: each entry is the canned shell output (the
# ---separated /etc/*-release dump) and the fact pyinfra expects from it.
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
async def test_linux_distribution_matches_pyinfra_fixtures(output, expected):
    host = CannedShellHost(output)
    assert await LinuxDistribution.process(host) == expected
    assert host.shells == [LinuxDistribution.COMMAND]


def test_linux_distribution_command_matches_pyinfra():
    # The command every pyinfra fixture records, verbatim.
    assert LinuxDistribution.COMMAND == (
        'cd /etc/ && for file in $(ls -pdL *-release | grep -v /); '
        'do echo "/etc/${file}"; cat "/etc/${file}"; echo ---; '
        "done"
    )


def test_linux_distribution_parse_skips_empty_release_files():
    # Arch's /etc/arch-release is an empty file: a part with only a filename
    # is skipped, leaving no parts and pyinfra's default dict.
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
    # The seam defaults to pyinfra's default(): no distro claim until set.
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


async def test_linux_gui_parses_pyintras_gnome_present_fixture():
    # pyinfra's own fact fixture (tests/facts/server.LinuxGui/gnome_present.json):
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
    # All five binaries pyinfra's known_gui_binaries recognises, in listing
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
    # Lines are matched exactly (pyinfra does not strip them).
    assert LinuxGui.parse(["/usr/bin/unknown-session"]) == []
    assert LinuxGui.parse([" /usr/bin/gnome-session "]) == []
    assert LinuxGui.parse(["", "gnome-session"]) == []
    assert LinuxGui.parse([]) == []


async def test_linux_gui_is_empty_on_empty_output():
    # pyinfra's server.HasGui/gui_absent.json output ([]): no *session match,
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


async def test_linux_name_parses_pyintras_ubuntu_fixture():
    # pyinfra's own fact fixture (tests/facts/server.LinuxName/ubuntu.json):
    # the LinuxDistribution release-file dump command, output
    # ["/etc/os-release", 'NAME="Ubuntu"'] -> fact "Ubuntu".
    host = CannedShellHost('/etc/os-release\nNAME="Ubuntu"\n')
    assert await LinuxName.process(host) == "Ubuntu"
    # The delegation runs LinuxDistribution's shell, not a second command.
    assert host.shells == [LinuxDistribution.COMMAND]


@pytest.mark.parametrize(("output", "expected"), LINUX_DISTRIBUTION_FIXTURES)
async def test_linux_name_matches_pyinfra_linux_distribution_fixtures(output, expected):
    # LinuxName is the name projection of LinuxDistribution, so every one of
    # pyinfra's nine distribution fixtures doubles as a LinuxName fixture.
    host = CannedShellHost(output)
    assert await LinuxName.process(host) == expected["name"]


async def test_linux_name_is_none_on_empty_output():
    # No release files at all: pyinfra's default() name is None.
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
    # The seam defaults to pyinfra's default(): no distro claim until set.
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


async def test_locales_parses_pyintras_documented_example():
    # pyinfra's documented example result (server.Locales docstring):
    # command "locale -a", output ["C.utf8", "en_US.utf8"] -> fact
    # ["C.UTF-8", "en_US.UTF-8"]. pyinfra ships no fixture for this fact.
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
    # pyinfra maps every line verbatim: no sorting, no dedup, no stripping.
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


async def test_lsb_release_parses_pyintras_release_fixture():
    # pyinfra's own fact fixture (tests/facts/server.LsbRelease/release.json):
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


async def test_mounts_parses_pyintras_linux_fixture():
    # pyinfra's own fact fixture (tests/facts/server.Mounts/mounts.json):
    # Kernel "Linux", command "cat /proc/self/mountinfo", three rows --
    # including the octal-escaped overlay mount point, device options and
    # a "shared" optional tag, reused verbatim for parity.
    host = CannedKernelHost(
        "Linux",
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
    assert host.shells == ["uname -s", "cat /proc/self/mountinfo"]


async def test_mounts_parses_pyintras_openbsd_fixture():
    # pyinfra's openbsd.json fixture: Kernel "OpenBSD", command "mount -v";
    # the (diskname.label) part is optional, ctime= flags are dropped and
    # options after them are kept, reused verbatim for parity.
    host = CannedKernelHost(
        "OpenBSD",
        """/dev/sd0a (b486026f5c43e172.a) on / type ffs (rw, local, ctime=Thu Jan  1 12:00:00 1970)
/dev/sd0k (b486026f5c43e172.k) on /home type ffs (rw, local, nodev, nosuid, ctime=Thu Jan  1 12:00:00 1970)
/dev/sd0d (b486026f5c43e172.d) on /tmp type ffs (rw, local, nodev, nosuid, ctime=Thu Jan  1 12:00:00 1970)
/dev/sd0f (b486026f5c43e172.f) on /usr type ffs (rw, local, nodev, ctime=Thu Jan  1 12:00:00 1970)
/dev/sd0g (b486026f5c43e172.g) on /usr/X11R6 type ffs (rw, local, nodev, ctime=Thu Jan  1 12:00:00 1970)
/dev/sd0h (b486026f5c43e172.h) on /usr/local type ffs (rw, local, nodev, wxallowed, ctime=Thu Jan  1 12:00:00 1970)
/dev/sd0j (b486026f5c43e172.j) on /usr/obj type ffs (rw, local, nodev, nosuid, ctime=Thu Jan  1 12:00:00 1970)
/dev/sd0i (b486026f5c43e172.i) on /usr/src type ffs (rw, local, nodev, nosuid, ctime=Thu Jan  1 12:00:00 1970)
/dev/sd0e on /var type ffs (rw, local, nosuid, ctime=Thu Jan  1 12:00:00 1970)
skynet:/sys on /sys type nfs (rw, nodev, nosuid, ctime=Mon Aug  4 16:21:19 1997, tcp, soft, intr)
mfs:75 on /run type mfs (rw, noexec, nosuid, ctime=Thu Jan  1 12:00:00 1970)
""",
    )
    assert await Mounts.process(host) == {
        "/": {"device": "/dev/sd0a", "type": "ffs", "options": ["rw", "local"]},
        "/home": {
            "device": "/dev/sd0k",
            "type": "ffs",
            "options": ["rw", "local", "nodev", "nosuid"],
        },
        "/tmp": {
            "device": "/dev/sd0d",
            "type": "ffs",
            "options": ["rw", "local", "nodev", "nosuid"],
        },
        "/usr": {
            "device": "/dev/sd0f",
            "type": "ffs",
            "options": ["rw", "local", "nodev"],
        },
        "/usr/X11R6": {
            "device": "/dev/sd0g",
            "type": "ffs",
            "options": ["rw", "local", "nodev"],
        },
        "/usr/local": {
            "device": "/dev/sd0h",
            "type": "ffs",
            "options": ["rw", "local", "nodev", "wxallowed"],
        },
        "/usr/obj": {
            "device": "/dev/sd0j",
            "type": "ffs",
            "options": ["rw", "local", "nodev", "nosuid"],
        },
        "/usr/src": {
            "device": "/dev/sd0i",
            "type": "ffs",
            "options": ["rw", "local", "nodev", "nosuid"],
        },
        "/var": {
            "device": "/dev/sd0e",
            "type": "ffs",
            "options": ["rw", "local", "nosuid"],
        },
        "/sys": {
            "device": "skynet:/sys",
            "type": "nfs",
            "options": ["rw", "nodev", "nosuid", "tcp", "soft", "intr"],
        },
        "/run": {
            "device": "mfs:75",
            "type": "mfs",
            "options": ["rw", "noexec", "nosuid"],
        },
    }
    assert host.shells == ["uname -s", "mount -v"]


async def test_mounts_parses_freebsd_json():
    # pyinfra ships no FreeBSD fixture for Mounts; the JSON shape is its
    # parser's contract: mount.fstab entries with mntpoint/device/fstype
    # and a comma-separated opts string whose items are stripped.
    host = CannedKernelHost(
        "FreeBSD",
        """{"mount": {"fstab": [
{"device": "/dev/ada0p2", "mntpoint": "/", "fstype": "ufs", "opts": "rw, userquota, groupquota"},
{"device": "tmpfs", "mntpoint": "/tmp", "fstype": "tmpfs", "opts": "rw, nosuid, mode=01777"}
]}}
""",
    )
    assert await Mounts.process(host) == {
        "/": {
            "device": "/dev/ada0p2",
            "type": "ufs",
            "options": ["rw", "userquota", "groupquota"],
        },
        "/tmp": {
            "device": "tmpfs",
            "type": "tmpfs",
            "options": ["rw", "nosuid", "mode=01777"],
        },
    }
    assert host.shells == ["uname -s", "mount -p --libxo json"]


def test_mounts_parses_pyintras_documented_example():
    # pyinfra's docstring example result: "/" on /dev/mv2, ext4, rw+relatime.
    # The super-block "rw" is filtered so it never appears twice.
    assert Mounts.parse(
        ["29 1 253:0 / / rw,relatime - ext4 /dev/mv2 rw"], "Linux"
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
        "Linux",
    ) == {"/sys": {"device": "new", "type": "sysfs", "options": ["ro"]}}
    assert Mounts.parse([], "Linux") == {}


def test_mounts_parse_openbsd_skips_unmatched_lines():
    # Only full "device on /path type (flags)" rows count; anything else
    # (headers, blank lines) is skipped, like pyinfra's fullmatch guard.
    assert (
        Mounts.parse(["not a mount row", "", "dev on relative type x (rw)"], "OpenBSD")
        == {}
    )


def test_mounts_command_dispatches_on_the_kernel():
    assert Mounts.command("FreeBSD") == "mount -p --libxo json"
    assert Mounts.command(" FreeBSD ") == "mount -p --libxo json"
    assert Mounts.command("OpenBSD") == "mount -v"
    assert Mounts.command(" OpenBSD ") == "mount -v"
    # Anything else -- including a failed `uname -s` ("") -- takes the
    # /proc/self/mountinfo branch, exactly like pyinfra.
    for kernel in ("Linux", "Darwin", ""):
        assert Mounts.command(kernel) == "cat /proc/self/mountinfo"


async def test_mounts_is_empty_on_empty_output():
    # A failed read maps to {} on every branch (pyinfra's ``default =
    # dict``); on FreeBSD empty output is caught before json.loads.
    for kernel in ("Linux", "FreeBSD", "OpenBSD"):
        assert await Mounts.process(CannedKernelHost(kernel, "")) == {}


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


# --- Path ----------------------------------------------------------------------


async def test_path_parses_echo_path():
    # pyinfra ships no fixture for server.Path; a standard Linux search
    # path is echoed verbatim and returned as the raw colon-separated
    # string (pyinfra's FactBase default joins the single output line).
    host = CannedShellHost("/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin\n")
    assert await Path.process(host) == (
        "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin"
    )
    assert host.shells == ["echo $PATH"]


def test_path_parse_takes_the_first_non_blank_line():
    assert Path.parse(["/usr/bin:/bin"]) == "/usr/bin:/bin"
    assert Path.parse(["", "  /usr/bin:/bin  ", "ignored"]) == "/usr/bin:/bin"
    assert Path.parse([]) == ""
    # Interior characters -- colons, spaces in directory names -- are kept
    # verbatim: the string is not split into a list.
    assert Path.parse(["/opt/my dir/bin:/usr/bin"]) == "/opt/my dir/bin:/usr/bin"


async def test_path_is_empty_on_empty_output():
    # A failed command, or an unset/empty PATH (echo prints a blank line),
    # maps to "" like Arch.
    assert await Path.process(CannedShellHost("")) == ""
    assert await Path.process(CannedShellHost("\n")) == ""


async def test_path_reads_the_simulated_seam():
    host = SimulatedHost(name="h", os_path="/custom/bin:/usr/bin")
    assert await Path.process(host) == "/custom/bin:/usr/bin"
    # The seam defaults to a standard Linux search path: a simulated
    # machine always answers echo $PATH.
    assert await Path.process(SimulatedHost(name="h")) == (
        "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin"
    )


async def test_path_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", os_path="/custom/bin:/usr/bin"),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, Path) == {
        "web1": "/custom/bin:/usr/bin",
        "web2": "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin",
    }


# --- Processes -----------------------------------------------------------------


async def test_processes_parses_pyintras_linux_all_fixture():
    # pyinfra's own fact fixture (tests/facts/server.Processes/linux_all.json):
    # a non-BusyBox Linux kernel selects the --no-headers listing.
    host = CannedProcessesHost(
        "Linux",
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
    # The PID 1 record is pyinfra's documented example result, verbatim.
    assert result[1] == {
        "user": "root",
        "state": "Ss",
        "cpu_percent": 0.0,
        "mem_percent": 0.1,
        "command": "init",
        "args": "/sbin/init",
    }
    # Kernel read, BusyBox probe (empty answer), then the Linux listing.
    assert host.shells == [
        "uname -s",
        "command -v busybox || true",
        "LANG=C ps -eo pid,user,stat,%cpu,%mem,comm,args --no-headers",
    ]


async def test_processes_parses_pyintras_linux_single_pid_fixture():
    # linux_single_pid.json: ps -p keeps the header row, which is skipped.
    host = CannedProcessesHost(
        "Linux",
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


async def test_processes_parses_pyintras_bsd_all_fixture():
    # bsd_all.json: FreeBSD keeps the header row and never probes BusyBox.
    host = CannedProcessesHost(
        "FreeBSD",
        "",
        """  PID USER     STAT %CPU %MEM COMMAND         COMMAND
    1 root     SLs   0.0  0.0 init            /sbin/init --
  742 root     Ss    0.0  0.2 sshd            /usr/sbin/sshd
""",
    )
    assert await Processes.process(host) == {
        1: {
            "user": "root",
            "state": "SLs",
            "cpu_percent": 0.0,
            "mem_percent": 0.0,
            "command": "init",
            "args": "/sbin/init --",
        },
        742: {
            "user": "root",
            "state": "Ss",
            "cpu_percent": 0.0,
            "mem_percent": 0.2,
            "command": "sshd",
            "args": "/usr/sbin/sshd",
        },
    }
    assert host.shells == [
        "uname -s",
        "LANG=C ps -eo pid,user,stat,%cpu,%mem,comm,args",
    ]


async def test_processes_parses_pyintras_bsd_single_pid_fixture():
    # bsd_single_pid.json.
    host = CannedProcessesHost(
        "FreeBSD",
        "",
        """  PID USER     STAT %CPU %MEM COMMAND         COMMAND
  742 root     Ss    0.0  0.2 sshd            /usr/sbin/sshd
""",
    )
    assert await Processes.process(host, pid=742) == {
        742: {
            "user": "root",
            "state": "Ss",
            "cpu_percent": 0.0,
            "mem_percent": 0.2,
            "command": "sshd",
            "args": "/usr/sbin/sshd",
        },
    }
    assert host.shells[-1] == "LANG=C ps -p 742 -o pid,user,stat,%cpu,%mem,comm,args"


async def test_processes_parses_pyintras_busybox_all_fixture():
    # busybox_all.json: the probe answers a path, selecting the five-column
    # listing; percents are always 0.0 and command is args' basename.
    host = CannedProcessesHost(
        "Linux",
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
        "uname -s",
        "command -v busybox || true",
        "LANG=C ps -o pid,user,vsz,stat,args",
    ]


async def test_processes_parses_pyintras_busybox_single_pid_fixture():
    # busybox_single_pid.json: BusyBox ps has no -p, so awk filters the PID.
    host = CannedProcessesHost(
        "Linux",
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


async def test_processes_parses_pyintras_darwin_all_fixture():
    # darwin_all.json: two ps snapshots joined by the marker line; comm
    # supplies the untruncated command, args the full command line.
    helper = (
        "/System/Library/PrivateFrameworks/CoreServicesInternal.framework"
        "/Versions/A/XPCServices/backupd-helper.xpc/Contents/MacOS/backupd-helper"
    )
    host = CannedProcessesHost(
        "Darwin",
        "",
        f"""  PID USER     STAT  %CPU %MEM ARGS
    1 root     Ss    0.0  0.1 /sbin/launchd
  200 root     S     0.0  0.0 /usr/libexec/opendirectoryd
  300 root     Ss    0.0  0.2 /usr/sbin/sshd -D -o PermitRootLogin=no
  500 root     Ss    0.3  1.2 {helper}
  600 karlicos S     0.1  0.4 /Applications/Some App.app/Contents/MacOS/Some App --flag value
===PYINFRA-PROCESSES-COMM===
  PID COMM
    1 /sbin/launchd
  200 /usr/libexec/opendirectoryd
  300 /usr/sbin/sshd
  500 {helper}
  600 /Applications/Some App.app/Contents/MacOS/Some App
""",
    )
    assert await Processes.process(host) == {
        1: {
            "user": "root",
            "state": "Ss",
            "cpu_percent": 0.0,
            "mem_percent": 0.1,
            "command": "/sbin/launchd",
            "args": "/sbin/launchd",
        },
        200: {
            "user": "root",
            "state": "S",
            "cpu_percent": 0.0,
            "mem_percent": 0.0,
            "command": "/usr/libexec/opendirectoryd",
            "args": "/usr/libexec/opendirectoryd",
        },
        300: {
            "user": "root",
            "state": "Ss",
            "cpu_percent": 0.0,
            "mem_percent": 0.2,
            "command": "/usr/sbin/sshd",
            "args": "/usr/sbin/sshd -D -o PermitRootLogin=no",
        },
        500: {
            "user": "root",
            "state": "Ss",
            "cpu_percent": 0.3,
            "mem_percent": 1.2,
            "command": helper,
            "args": helper,
        },
        600: {
            "user": "karlicos",
            "state": "S",
            "cpu_percent": 0.1,
            "mem_percent": 0.4,
            "command": "/Applications/Some App.app/Contents/MacOS/Some App",
            "args": "/Applications/Some App.app/Contents/MacOS/Some App --flag value",
        },
    }
    # Darwin never probes BusyBox; one marker-joined pipeline is the shell.
    assert host.shells == [
        "uname -s",
        (
            "LANG=C ps -eo pid,user,stat,%cpu,%mem,args -ww && "
            "echo ===PYINFRA-PROCESSES-COMM=== && LANG=C ps -eo pid,comm -ww"
        ),
    ]


async def test_processes_parses_pyintras_darwin_single_pid_fixture():
    # darwin_single_pid.json: both ps runs are restricted to the one PID.
    host = CannedProcessesHost(
        "Darwin",
        "",
        """  PID USER     STAT  %CPU %MEM ARGS
  742 root     Ss    0.0  0.2 /usr/sbin/sshd -D
===PYINFRA-PROCESSES-COMM===
  PID COMM
  742 /usr/sbin/sshd
""",
    )
    assert await Processes.process(host, pid=742) == {
        742: {
            "user": "root",
            "state": "Ss",
            "cpu_percent": 0.0,
            "mem_percent": 0.2,
            "command": "/usr/sbin/sshd",
            "args": "/usr/sbin/sshd -D",
        },
    }
    assert host.shells[-1] == (
        "LANG=C ps -p 742 -o pid,user,stat,%cpu,%mem,args -ww && "
        "echo ===PYINFRA-PROCESSES-COMM=== && LANG=C ps -p 742 -o pid,comm -ww"
    )


async def test_processes_parses_pyintras_darwin_pty_crlf_fixture():
    # darwin_pty_crlf.json: a PTY transport leaves \r on every line; the
    # parser strips them (and the trailing empty line is skipped).
    helper = (
        "/System/Library/PrivateFrameworks/CoreServicesInternal.framework"
        "/Versions/A/XPCServices/backupd-helper.xpc/Contents/MacOS/backupd-helper"
    )
    host = CannedProcessesHost(
        "Darwin",
        "",
        f"""  PID USER     STAT  %CPU %MEM ARGS\r
    1 root     Ss    0.0  0.1 /sbin/launchd\r
  200 root     S     0.0  0.0 /usr/libexec/opendirectoryd\r
  300 root     Ss    0.0  0.2 /usr/sbin/sshd -D -o PermitRootLogin=no\r
  500 root     Ss    0.3  1.2 {helper}\r
===PYINFRA-PROCESSES-COMM===\r
  PID COMM\r
    1 /sbin/launchd\r
  200 /usr/libexec/opendirectoryd\r
  300 /usr/sbin/sshd\r
  500 {helper}\r""",
    )
    result = await Processes.process(host)
    assert set(result) == {1, 200, 300, 500}
    assert result[300]["args"] == "/usr/sbin/sshd -D -o PermitRootLogin=no"
    assert result[500]["command"] == helper


async def test_processes_parses_pyintras_darwin_spaced_executable_fixture():
    # darwin_spaced_executable.json: an executable path with spaces stays
    # whole because comm is the last column of its own ps run.
    host = CannedProcessesHost(
        "Darwin",
        "",
        """  PID USER             STAT  %CPU %MEM ARGS
36470 karlicos         S      0.0  0.0 ./Example Application 120
===PYINFRA-PROCESSES-COMM===
  PID COMM
36470 ./Example Application
""",
    )
    assert await Processes.process(host) == {
        36470: {
            "user": "karlicos",
            "state": "S",
            "cpu_percent": 0.0,
            "mem_percent": 0.0,
            "command": "./Example Application",
            "args": "./Example Application 120",
        },
    }


async def test_processes_parses_pyintras_darwin_vanished_pid_fixture():
    # darwin_vanished_pid.json: PID 200 appears only in the args snapshot,
    # so it vanished between the two ps runs and is dropped.
    host = CannedProcessesHost(
        "Darwin",
        "",
        """  PID USER     STAT  %CPU %MEM ARGS
    1 root     Ss    0.0  0.1 /sbin/launchd
  200 root     S     0.0  0.0 /usr/libexec/opendirectoryd
  300 root     Ss    0.0  0.2 /usr/sbin/sshd -D
===PYINFRA-PROCESSES-COMM===
  PID COMM
    1 /sbin/launchd
  300 /usr/sbin/sshd
""",
    )
    result = await Processes.process(host)
    assert set(result) == {1, 300}
    assert result[300]["command"] == "/usr/sbin/sshd"


def test_processes_command_matches_pyintras_fixture_commands():
    # Every command string is the "command" field of the matching pyinfra
    # fixture, verbatim.
    assert Processes.command("Linux") == (
        "LANG=C ps -eo pid,user,stat,%cpu,%mem,comm,args --no-headers"
    )
    assert Processes.command("Linux", pid=1) == (
        "LANG=C ps -p 1 -o pid,user,stat,%cpu,%mem,comm,args"
    )
    assert Processes.command("FreeBSD") == (
        "LANG=C ps -eo pid,user,stat,%cpu,%mem,comm,args"
    )
    assert Processes.command("FreeBSD", pid=742) == (
        "LANG=C ps -p 742 -o pid,user,stat,%cpu,%mem,comm,args"
    )
    assert Processes.command("Linux", busybox=True) == (
        "LANG=C ps -o pid,user,vsz,stat,args"
    )
    assert Processes.command("Linux", busybox=True, pid=1) == (
        "LANG=C ps -o pid,user,vsz,stat,args | awk 'NR==1 || $1==1'"
    )
    assert Processes.command("Darwin") == (
        "LANG=C ps -eo pid,user,stat,%cpu,%mem,args -ww && "
        "echo ===PYINFRA-PROCESSES-COMM=== && LANG=C ps -eo pid,comm -ww"
    )
    assert Processes.command("Darwin", pid=742) == (
        "LANG=C ps -p 742 -o pid,user,stat,%cpu,%mem,args -ww && "
        "echo ===PYINFRA-PROCESSES-COMM=== && LANG=C ps -p 742 -o pid,comm -ww"
    )
    # The kernel name is stripped; an unknown kernel (or a failed uname -s,
    # which reads as "") takes the Linux branch, exactly like pyinfra.
    assert Processes.command(" Linux ") == Processes.COMMAND_LINUX
    assert Processes.command("") == Processes.COMMAND_LINUX
    assert Processes.command(" FreeBSD ") == Processes.COMMAND_BSD


def test_processes_parse_skips_headers_and_malformed_rows():
    rows = [
        "  PID USER     STAT %CPU %MEM COMMAND         COMMAND",
        "",
        "short row",
        "not-a-pid root Ss 0.0 0.1 init /sbin/init",
        "    1 root     Ss    0.0  0.1 init            /sbin/init",
    ]
    assert Processes.parse(rows, "Linux") == {
        1: {
            "user": "root",
            "state": "Ss",
            "cpu_percent": 0.0,
            "mem_percent": 0.1,
            "command": "init",
            "args": "/sbin/init",
        },
    }
    # Empty output reads as {} on every branch (pyinfra's default = dict).
    assert Processes.parse([], "Linux") == {}
    assert Processes.parse([], "FreeBSD") == {}
    assert Processes.parse([], "Linux", busybox=True) == {}


def test_processes_parse_args_falls_back_to_comm():
    # A row with no separate args column reuses comm (pyinfra's fallback).
    result = Processes.parse(["1 root Ss 0.0 0.1 init"], "Linux")
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
    assert Processes.parse(rows, "Linux", busybox=True) == {
        310: {
            "user": "root",
            "state": "S",
            "cpu_percent": 0.0,
            "mem_percent": 0.0,
            "command": "crond",
            "args": "/usr/sbin/crond -c /etc/crontabs",
        },
    }


def test_processes_darwin_parse_requires_the_marker():
    # A missing marker means the double-ps pipeline produced unexpected
    # output; reemote5 raises ValueError (pyinfra a bare Exception).
    with pytest.raises(ValueError, match="PYINFRA-PROCESSES-COMM"):
        Processes.parse(["    1 root     Ss    0.0  0.1 /sbin/launchd"], "Darwin")
    assert Processes.parse(["===PYINFRA-PROCESSES-COMM==="], "Darwin") == {}


async def test_processes_is_empty_on_empty_output():
    # A failed ps read maps to {} (pyinfra's default = dict); the Darwin
    # branch never sees empty output here because the marker echo always
    # runs, but a bare empty stdout still reads as {} on Linux/BSD/BusyBox.
    assert await Processes.process(CannedProcessesHost("Linux", "", "")) == {}
    assert await Processes.process(CannedProcessesHost("FreeBSD", "", "")) == {}
    assert await Processes.process(
        CannedProcessesHost("Linux", "/bin/busybox\n", "")
    ) == {}


async def test_processes_rejects_a_non_int_pid():
    # The BusyBox branch interpolates the pid into an awk program, so a
    # non-int pid is rejected identically on both transports.
    for host in (CannedProcessesHost("Linux", "", ""), SimulatedHost(name="h")):
        with pytest.raises(TypeError, match="pid must be an int"):
            await Processes.process(host, pid="1")
    with pytest.raises(TypeError, match="pid must be an int"):
        Processes.command("Linux", busybox=True, pid="1 || 1")


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


# --- Sysctl --------------------------------------------------------------------


async def test_sysctl_parses_pyintras_linux_fixture():
    # pyinfra's own fact fixture (tests/facts/server.Sysctl/sysctl_linux.json):
    # command "sysctl -a 2>/dev/null || true", int values including a negative
    # one, and a row without a separator ("nope what") skipped.
    host = CannedShellHost(
        "net.ipv4.tcp_adv_win_scale = -2\n"
        "vm.nr_hugepages_mempolicy = 0\n"
        "nope what\n"
        "vm.nr_overcommit_hugepages = 0\n"
    )
    assert await Sysctl.process(host) == {
        "net.ipv4.tcp_adv_win_scale": -2,
        "vm.nr_hugepages_mempolicy": 0,
        "vm.nr_overcommit_hugepages": 0,
    }
    assert host.shells == ["sysctl -a 2>/dev/null || true"]


async def test_sysctl_parses_pyintras_macos_fixture():
    # pyinfra's sysctl_macos.json fixture: BSD rows separate with ":".
    host = CannedShellHost(
        "security.mac.sysvmsg_enforce: 1\n"
        "security.mac.sysvsem_enforce: 1\n"
        "security.mac.sysvshm_enforce: 1\n"
    )
    assert await Sysctl.process(host) == {
        "security.mac.sysvmsg_enforce": 1,
        "security.mac.sysvsem_enforce": 1,
        "security.mac.sysvshm_enforce": 1,
    }


async def test_sysctl_parses_pyintras_port_range_fixture():
    # pyinfra's sysctl_port_range.json fixture: a tab-separated pair becomes
    # a list of ints, a whitespace-only value keeps "", and a simple word
    # stays a string.
    host = CannedShellHost(
        "net.ipv4.ip_local_port_range = 1024\t65535\n"
        "net.ipv4.ip_local_reserved_ports = \n"
        "kern.hostname: example\n"
    )
    assert await Sysctl.process(host) == {
        "net.ipv4.ip_local_port_range": [1024, 65535],
        "net.ipv4.ip_local_reserved_ports": "",
        "kern.hostname": "example",
    }


def test_sysctl_command_quotes_the_named_keys():
    assert Sysctl.command() == "sysctl -a 2>/dev/null || true"
    # Safe tokens stay unquoted (pyinfra's QuoteString), multiple keys join
    # with spaces, and a key carrying metacharacters is a literal lookup.
    assert (
        Sysctl.command(["kern.max-files"])
        == "sysctl kern.max-files 2>/dev/null || true"
    )
    assert (
        Sysctl.command(["a.b", "c.d"]) == "sysctl a.b c.d 2>/dev/null || true"
    )
    assert (
        Sysctl.command(["kern.evil; whoami"])
        == "sysctl 'kern.evil; whoami' 2>/dev/null || true"
    )


async def test_sysctl_keyed_read_over_the_shell():
    host = CannedShellHost("kern.max-files = 2000\n")
    assert await Sysctl.process(host, keys=["kern.max-files"]) == {
        "kern.max-files": 2000
    }
    assert host.shells == ["sysctl kern.max-files 2>/dev/null || true"]


def test_sysctl_parse_edge_cases():
    # A value outside the simple character set stays the stripped raw
    # string -- no int conversion, no list split.
    assert Sysctl.parse(["kernel.core_pattern = |/usr/sbin/coredump %p"]) == {
        "kernel.core_pattern": "|/usr/sbin/coredump %p"
    }
    # Mixed tokens int-convert individually; a single token collapses.
    assert Sysctl.parse(["kern.mix = 1 two"]) == {"kern.mix": [1, "two"]}
    assert Sysctl.parse(["kern.one = 42"]) == {"kern.one": 42}
    # Truthiness is tested BEFORE stripping: "key =" (nothing after the
    # separator) is skipped, "key = " (whitespace) keeps "".
    assert Sysctl.parse(["kern.none =", "kern.blank = "]) == {"kern.blank": ""}
    # "=" wins over ":" when both appear (the first split is on "=").
    assert Sysctl.parse(["kern.both: x = 5"]) == {"kern.both: x": 5}


async def test_sysctl_is_empty_on_empty_output():
    # An unknown key or a failed read maps to {} (pyinfra's default = dict).
    assert await Sysctl.process(CannedShellHost("")) == {}


async def test_sysctl_reads_the_simulated_seam():
    host = SimulatedHost(
        name="h",
        sysctls={"fs.file-max": 100000, "net.ipv4.ip_local_port_range": [1024, 65535]},
    )
    assert await Sysctl.process(host) == {
        "fs.file-max": 100000,
        "net.ipv4.ip_local_port_range": [1024, 65535],
    }
    # A keys filter selects the named settings; an unknown key reads as
    # absent, like the swallowed sysctl error.
    assert await Sysctl.process(host, keys=["fs.file-max"]) == {"fs.file-max": 100000}
    assert await Sysctl.process(host, keys=["fs.file-max", "no.such"]) == {
        "fs.file-max": 100000
    }
    assert await Sysctl.process(host, keys=["no.such"]) == {}
    # The seam defaults to {} -- a simulated machine with no settings listed.
    assert await Sysctl.process(SimulatedHost(name="h")) == {}


async def test_sysctl_seam_returns_a_copy():
    host = SimulatedHost(name="h", sysctls={"kern.pair": [1, 2]})
    result = await Sysctl.process(host)
    result["kern.pair"].append(3)
    result["kern.extra"] = 0
    assert host.sysctls == {"kern.pair": [1, 2]}


async def test_sysctl_gather_fact_across_hosts():
    hosts = [
        SimulatedHost(name="web1", sysctls={"fs.file-max": 100000}),
        SimulatedHost(name="web2", sysctls={"fs.file-max": 200000}),
    ]
    assert await gather_fact(hosts, Sysctl) == {
        "web1": {"fs.file-max": 100000},
        "web2": {"fs.file-max": 200000},
    }
    # The keys argument is forwarded to process verbatim.
    assert await gather_fact(hosts, Sysctl, keys=["fs.file-max"]) == {
        "web1": {"fs.file-max": 100000},
        "web2": {"fs.file-max": 200000},
    }
    assert await gather_fact(hosts, Sysctl, keys=["no.such"]) == {
        "web1": {},
        "web2": {},
    }


# --- Timezone ----------------------------------------------------------------


async def test_timezone_parses_pyintras_fixture():
    # pyinfra's own fact fixture (tests/facts/server.Timezone/timezone.json):
    # command "readlink -f /etc/localtime | sed 's|.*/zoneinfo/||'",
    # output ["Europe/Amsterdam"] -> fact "Europe/Amsterdam".
    host = CannedShellHost("Europe/Amsterdam\n")
    assert await Timezone.process(host) == "Europe/Amsterdam"
    assert host.shells == ["readlink -f /etc/localtime | sed 's|.*/zoneinfo/||'"]


def test_timezone_command_matches_pyinfra():
    assert Timezone.COMMAND == "readlink -f /etc/localtime | sed 's|.*/zoneinfo/||'"


def test_timezone_parse_takes_the_first_non_blank_line():
    assert Timezone.parse(["UTC"]) == "UTC"
    assert Timezone.parse(["", "  Europe/Amsterdam  ", "ignored"]) == "Europe/Amsterdam"
    assert Timezone.parse([]) == ""


def test_timezone_parse_keeps_a_non_zoneinfo_path_verbatim():
    # /etc/localtime copied instead of symlinked: the sed matches nothing
    # and the resolved path comes back as is, exactly like pyinfra.
    assert Timezone.parse(["/etc/localtime"]) == "/etc/localtime"


async def test_timezone_is_empty_on_empty_output():
    assert await Timezone.process(CannedShellHost("")) == ""


async def test_timezone_reads_the_simulated_seam():
    host = SimulatedHost(name="h", os_timezone="Australia/Sydney")
    assert await Timezone.process(host) == "Australia/Sydney"
    # The seam defaults to pyinfra's fixture timezone: a simulated machine
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


async def test_uptime_parses_pyintras_linux_fixture():
    # pyinfra's own fact fixture (tests/facts/server.Uptime/uptime_linux.json):
    # Kernel "Linux", command "cut -d. -f1 /proc/uptime",
    # output ["86400"] -> fact 86400.
    host = CannedKernelHost("Linux", "86400\n")
    assert await Uptime.process(host) == 86400
    assert host.shells == ["uname -s", "cut -d. -f1 /proc/uptime"]


async def test_uptime_parses_pyintras_macos_fixture():
    # pyinfra's uptime_macos.json fixture: Kernel "Darwin", command
    # "date +%s; sysctl -n kern.boottime | awk '{print $4}' | tr -d ','",
    # output ["1746748800", "1746662400"] -> fact 86400 (now - boottime).
    host = CannedKernelHost("Darwin", "1746748800\n1746662400\n")
    assert await Uptime.process(host) == 86400
    assert host.shells == [
        "uname -s",
        "date +%s; sysctl -n kern.boottime | awk '{print $4}' | tr -d ','",
    ]


def test_uptime_command_dispatches_on_the_kernel():
    bsd = "date +%s; sysctl -n kern.boottime | awk '{print $4}' | tr -d ','"
    assert Uptime.command("Darwin") == bsd
    assert Uptime.command("FreeBSD") == bsd
    assert Uptime.command(" FreeBSD ") == bsd
    # Anything else -- including a failed `uname -s` ("") -- takes the
    # /proc/uptime branch, exactly like pyinfra.
    for kernel in ("Linux", "OpenBSD", ""):
        assert Uptime.command(kernel) == "cut -d. -f1 /proc/uptime"


def test_uptime_parse_edge_cases():
    # Blank lines are skipped and the Linux answer is the first integer;
    # anything after it is ignored.
    assert Uptime.parse(["", "  3600  ", "ignored"], "Linux") == 3600
    # The BSD branch subtracts boottime from now, whichever BSD kernel.
    assert Uptime.parse(["1746748800", "1746662400"], "FreeBSD") == 86400
    assert Uptime.parse(["100", "40"], "Darwin") == 60


def test_uptime_parse_rejects_bad_rows_like_pyinfra():
    # A non-integer line raises ValueError, and a BSD answer missing its
    # second line raises IndexError -- pyinfra's parser does neither guard.
    with pytest.raises(ValueError):
        Uptime.parse(["oops"], "Linux")
    with pytest.raises(IndexError):
        Uptime.parse(["1746748800"], "Darwin")


async def test_uptime_is_none_on_empty_output():
    # A failed read maps to None (pyinfra's FactBase default), never to a
    # freshly booted host's 0.
    assert await Uptime.process(CannedKernelHost("Linux", "")) is None
    assert await Uptime.process(CannedKernelHost("Darwin", "")) is None


async def test_uptime_reads_the_simulated_seam():
    host = SimulatedHost(name="h", uptime_seconds=123)
    assert await Uptime.process(host) == 123
    # The seam defaults to pyinfra's fixture uptime: a simulated machine
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
    # pyinfra ships no fixture for server.User; a plain username is echoed
    # verbatim and returned as the raw string (pyinfra's FactBase default
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
    # maps to "" like Path.
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

# pyinfra's own fact fixture (tests/facts/server.Users/mixed.json), reused
# verbatim: passwd entries joined with id/lastlog/shadow answers, covering
# secondary groups, empty passwd fields, "Never logged in" rows and the
# BSD lastlogin timestamp layout.
USERS_MIXED_OUTPUT = """\
root:x:0:0:root:/root:/bin/ks|wheel|wheel kmem sys tty operator staff guest|root pts/0 Sat Jun 5 12:03:23 -0600 2021|$rootpw$
_tesTy.test:x:1004:1004::/home/_tesTy.test:/bin/ksh|_tesTy.test|_tesTy.test pyinfra|_tesTy.test pts/0 host-ip Sat Jun 12 13:43:42 -0600 2021|$testpw$
test.testy:x:1003:1003:Testy,,,:/home/test.testy:/bin/ksh|test.testy||test.testy Fri Jun 11 22:26:04 -0600 2021|$testpw$
noshell:x:1002:1002:noshell comment with spaces:/home/noshell:|noshell||noshell **Never logged in**|
nohome:x:1002:1002:nohome comment::/bin/bash|nohome||**Never logged in**|
freebsd:*:1001:1001:FreeBSD:/home/freebsd:/bin/sh|freebsd|freebsd|freebsd pts/0 10.1.10.10 Thu Aug 31 05:37:58 2023|$bsdpw$
freebsdnologin:*:1001:1001:FreeBSD NoLogin:/home/freebsdnologin:/bin/sh|freebsdnologin|freebsdnologin||
"""

# The fixture's expected fact, with the JSON login_time strings read as
# datetimes (pyinfra's parser returns datetime objects; its test harness
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
        "groups": ["pyinfra"],
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


async def test_users_parses_pyintras_mixed_fixture():
    host = CannedShellHost(USERS_MIXED_OUTPUT)
    assert await Users.process(host) == USERS_MIXED_FACT
    assert host.shells == [Users.COMMAND]


def test_users_command_matches_pyinfra():
    # The command field of pyinfra's mixed.json fixture, verbatim.
    assert Users.COMMAND == """for i in `cat /etc/passwd | cut -d: -f1`; do
            ENTRY=`grep ^$i: /etc/passwd`;
            LASTLOG=`(((lastlog -u $i || lastlogin $i) 2> /dev/null) | grep ^$i | tr -s ' ')`;
            PASSWORD=`(grep ^$i: /etc/shadow || grep ^$i: /etc/master.passwd) 2> /dev/null | cut -d: -f2`;
            echo "$ENTRY|`id -gn $i`|`id -Gn $i`|$LASTLOG|$PASSWORD";
        done"""


def test_users_parse_skips_blank_lines_and_empty_entries():
    # A row whose passwd entry is empty (the grep failed) is skipped, like
    # pyinfra's ``if entry:`` guard; blank lines never reach the splitter.
    assert Users.parse(["", "   ", "|nobody|nobody||", "\t"]) == {}


def test_users_parse_rejects_malformed_rows():
    # A row without the four ``|`` separators cannot be unpacked, exactly
    # like pyinfra's rsplit.
    with pytest.raises(ValueError, match="not enough values to unpack"):
        Users.parse(["root:x:0:0:root:/root:/bin/bash"])


def test_users_parse_unmatched_login_layout_stays_none():
    # A timestamp the regex matches but neither strptime layout knows
    # (a named zone) keeps the raw lastlog and leaves login_time None --
    # the documented deviation from pyinfra's fuzzy dateutil parse.
    row = "root:x:0:0:root:/root:/bin/bash|root|root|root pts/0 Sat Jun 5 12:03:23 EST 2021|$rootpw$"
    (info,) = Users.parse([row]).values()
    assert info["lastlog"] == "Sat Jun 5 12:03:23 EST 2021"
    assert info["login_time"] is None


async def test_users_is_empty_on_empty_output():
    # A failed loop reads as {} -- pyinfra's ``default = dict``.
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


async def test_which_parses_pyintras_which_fixture():
    # pyinfra's own fact fixture (tests/facts/server.Which/which.json):
    # arg "python", command "command -v python || true",
    # output ["/usr/local/bin/python"] -> fact "/usr/local/bin/python".
    host = CannedShellHost("/usr/local/bin/python\n")
    assert await Which.process(host, "python") == "/usr/local/bin/python"
    assert host.shells == ["command -v python || true"]


async def test_which_quotes_pyintras_injection_fixture():
    # pyinfra's quotes_injection.json fixture: arg "py; reboot", command
    # "command -v 'py; reboot' || true", output [] -- the quoted name is
    # looked up literally, never executed, and an unavailable command reads
    # as None (pyinfra's runtime default; its raw parser joins lines to "").
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
    # a path; the string is returned verbatim, like pyinfra.
    assert Which.parse(["cd"]) == "cd"


async def test_which_is_none_on_empty_output():
    # The `|| true` swallows the non-zero exit of an unknown command, so no
    # output means unavailable -- None, pyinfra's FactBase default.
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


# --- server.service ----------------------------------------------------------


def test_systemctl_command_shell_rendering():
    assert SystemctlCommand(action="daemon-reload").shell() == "systemctl daemon-reload"
    assert (
        SystemctlCommand(action="start", unit="httpd.service").shell()
        == "systemctl start httpd.service"
    )
    assert command_shell(
        SystemctlCommand(action="enable", unit="httpd.service"), sudo=True
    ) == ("sudo systemctl enable httpd.service")


async def test_service_starts_an_inactive_unit():
    host = SimulatedHost(name="h", services={"httpd.service": "inactive"})
    ctx, commands = await materialize(host, op(server.service, service="httpd"))
    assert ctx.changed
    assert "will start" in ctx.reason
    assert commands == [SystemctlCommand(action="start", unit="httpd.service")]


async def test_service_treats_activating_as_not_running():
    host = SimulatedHost(name="h", services={"httpd.service": "activating"})
    _ctx, commands = await materialize(host, op(server.service, service="httpd"))
    assert commands == [SystemctlCommand(action="start", unit="httpd.service")]


async def test_service_no_change_when_already_running():
    host = SimulatedHost(name="h", services={"httpd.service": "active"})
    ctx, commands = await materialize(host, op(server.service, service="httpd"))
    assert not ctx.changed
    assert ctx.reason == "httpd.service already running"
    assert commands == []


async def test_service_stops_a_running_unit():
    host = SimulatedHost(name="h", services={"httpd.service": "active"})
    ctx, commands = await materialize(
        host, op(server.service, service="httpd", running=False)
    )
    assert ctx.changed
    assert commands == [SystemctlCommand(action="stop", unit="httpd.service")]


async def test_service_restart_only_when_running():
    running = SimulatedHost(name="h", services={"httpd.service": "active"})
    ctx, commands = await materialize(
        running, op(server.service, service="httpd", restarted=True)
    )
    assert ctx.changed
    assert commands == [SystemctlCommand(action="restart", unit="httpd.service")]

    stopped = SimulatedHost(name="h", services={"httpd.service": "inactive"})
    _, commands = await materialize(
        stopped, op(server.service, service="httpd", restarted=True)
    )
    # A stopped unit is started, not restarted (pyinfra's handle_service_control).
    assert commands == [SystemctlCommand(action="start", unit="httpd.service")]


async def test_service_enables_and_disables_on_boot():
    host = SimulatedHost(name="h", services={"httpd.service": "active"})
    ctx, commands = await materialize(
        host, op(server.service, service="httpd", enabled=True)
    )
    assert ctx.changed
    assert commands == [SystemctlCommand(action="enable", unit="httpd.service")]

    host.enabled_services.add("httpd.service")
    ctx, commands = await materialize(
        host, op(server.service, service="httpd", enabled=True)
    )
    assert not ctx.changed
    assert commands == []

    ctx, commands = await materialize(
        host, op(server.service, service="httpd", enabled=False)
    )
    assert commands == [SystemctlCommand(action="disable", unit="httpd.service")]


async def test_service_enabled_none_leaves_boot_setting_alone():
    host = SimulatedHost(name="h", services={"httpd.service": "active"})
    ctx, commands = await materialize(host, op(server.service, service="httpd"))
    assert commands == []
    assert "enabled" not in ctx.reason


async def test_service_daemon_reload_always_yields_first():
    host = SimulatedHost(
        name="h", services={"httpd.service": "inactive"}, enabled_services=set()
    )
    ctx, commands = await materialize(
        host,
        op(server.service, service="httpd", enabled=True, daemon_reload=True),
    )
    assert ctx.changed
    assert commands == [
        SystemctlCommand(action="daemon-reload"),
        SystemctlCommand(action="start", unit="httpd.service"),
        SystemctlCommand(action="enable", unit="httpd.service"),
    ]


async def test_service_commands_mutate_the_simulated_host():
    host = SimulatedHost(name="h", services={"httpd.service": "inactive"})
    for cmd in (
        SystemctlCommand(action="start", unit="httpd.service"),
        SystemctlCommand(action="enable", unit="httpd.service"),
    ):
        await send_command(host, cmd)
    assert host.services == {"httpd.service": "active"}
    assert host.enabled_services == {"httpd.service"}
    await send_command(host, SystemctlCommand(action="stop", unit="httpd.service"))
    await send_command(host, SystemctlCommand(action="disable", unit="httpd.service"))
    assert host.services == {"httpd.service": "inactive"}
    assert host.enabled_services == set()


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
    assert (
        GroupAddCommand(group="somegroup", gid=1000, freebsd=True).shell()
        == "pw groupadd -n somegroup -g 1000"
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
    assert (
        GroupDelCommand(group="somegroup", freebsd=True).shell()
        == "pw groupdel -n somegroup"
    )
    assert command_shell(GroupDelCommand(group="docker"), sudo=True) == (
        "sudo groupdel docker"
    )


@pytest.mark.parametrize(
    ("group", "kernel", "groups", "kwargs", "expected"),
    [
        # pyinfra's own operation fixtures (tests/operations/server.group/):
        # add.json, add_needs_quotes.json, add.freebsd.json,
        # add_freebsd_needs_quotes.json, delete.json, delete_needs_quotes.json,
        # delete.freebsd.json, delete_freebsd_needs_quotes.json. Its server.Os
        # fact is the deprecated alias of server.Kernel, so the fixture's Os
        # value feeds the os_kernel seam.
        ("somegroup", "Linux", [], {"gid": 1000, "system": True},
         ["groupadd -r somegroup --gid 1000"]),
        ("my group; whoami", "Linux", [], {},
         ["groupadd 'my group; whoami'"]),
        # system=True is ignored on FreeBSD ("BSD" in the kernel name).
        ("somegroup", "FreeBSD", [], {"gid": 1000, "system": True},
         ["pw groupadd -n somegroup -g 1000"]),
        ("my group; whoami", "FreeBSD", [], {"gid": 1000},
         ["pw groupadd -n 'my group; whoami' -g 1000"]),
        ("somegroup", "Linux", ["somegroup"], {"present": False},
         ["groupdel somegroup"]),
        ("my group; whoami", "Linux", ["my group; whoami"], {"present": False},
         ["groupdel 'my group; whoami'"]),
        ("somegroup", "FreeBSD", ["somegroup"], {"present": False},
         ["pw groupdel -n somegroup"]),
        ("my group; whoami", "FreeBSD", ["my group; whoami"], {"present": False},
         ["pw groupdel -n 'my group; whoami'"]),
    ],
)
async def test_group_matches_pyinfra_operation_fixtures(
    group: str, kernel: str, groups: list[str], kwargs: dict, expected: list[str]
):
    host = SimulatedHost(name="h", os_groups=groups, os_kernel=kernel)
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
    # pyinfra's `if gid:` truthiness check: a gid of 0 renders no flag.
    _, commands = await materialize(host, op(server.group, group="docker", gid=0))
    assert commands[0].shell() == "groupadd docker"


async def test_group_existing_group_is_never_modified():
    # system/gid only apply at creation; a present group plans no command
    # even when the request's gid or system flag differs (pyinfra parity).
    host = SimulatedHost(name="h", os_groups=["docker"])
    ctx, commands = await materialize(
        host, op(server.group, group="docker", system=True, gid=1500)
    )
    assert not ctx.changed
    assert commands == []


async def test_group_absorbs_unknown_keyword_arguments():
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize(
        host, op(server.group, group="docker", _pyinfra_global=True)
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
    # pyinfra's QuoteString: a name carrying shell metacharacters is quoted.
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
        # pyinfra's own operation fixtures (tests/operations/server.hostname/).
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
async def test_hostname_matches_pyinfra_operation_fixtures(
    name: str, current: str, hostnamectl: str | None, expected: list[str]
):
    host = SimulatedHost(name="h", os_hostname=current)
    if hostnamectl is not None:
        host.command_paths = {"hostnamectl": hostnamectl}
    ctx, commands = await materialize(host, op(server.hostname, hostname=name))
    assert ctx.changed == bool(expected)
    assert [cmd.shell() for cmd in commands] == expected
    if not expected:
        # pyinfra's noop_description, verbatim.
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
        host, op(server.hostname, hostname="web1.example.com", _pyinfra_global=True)
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
    # pyinfra's QuoteString: a signal carrying shell metacharacters is
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
        # pyinfra's own operation fixtures (tests/operations/server.kill/),
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
async def test_kill_matches_pyinfra_operation_fixtures(
    pid: int, kwargs: dict, expected: list[str]
):
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(host, op(server.kill, pid=pid, **kwargs))
    assert ctx.changed
    assert [cmd.shell() for cmd in commands] == expected


async def test_kill_is_stateless_and_sets_the_reason():
    # No processes seeded at all: the operation reads no fact and still
    # plans the kill (pyinfra's non-idempotent behaviour).
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
        host, op(server.kill, pid=1234, _pyinfra_global=True)
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
        # pyinfra's own operation fixtures (tests/operations/server.modprobe/):
        # add.json, add_exists.json, add_force.json, add-list.json,
        # add_multiple_exists.json, add_needs_quotes.json, remove.json,
        # remove-list.json, remove_needs_quotes.json. Only membership in the
        # KernelModules dict matters, so the fixtures' empty info dicts feed
        # the kernel_modules seam verbatim.
        ("somemodule", [], {}, ["modprobe -a somemodule"], True),
        ("somemodule", ["somemodule"], {}, [], False),
        ("somemodule", [], {"force": True}, ["modprobe -f -a somemodule"], True),
        (["mod1", "mod2", "mod3"], ["mod2"], {}, ["modprobe -a mod1 mod3"], True),
        (["somemodule", "someothermodule"], ["somemodule", "someothermodule"],
         {}, [], False),
        ("evil; rm -rf /", [], {}, ["modprobe -a 'evil; rm -rf /'"], True),
        ("somemodule", ["somemodule"], {"present": False},
         ["modprobe -r -a somemodule"], True),
        (["mod1", "mod2", "mod3"], ["mod1"], {"present": False},
         ["modprobe -r -a mod1"], True),
        ("evil; rm -rf /", ["evil; rm -rf /"], {"present": False},
         ["modprobe -r -a 'evil; rm -rf /'"], True),
    ],
)
async def test_modprobe_matches_pyinfra_operation_fixtures(
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
    # pyinfra's add_exists.json noop_description, verbatim.
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
    # pyinfra's add_multiple_exists.json noop_description, verbatim.
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
    # Like pyinfra, force only flags the command -- it never turns an
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
        host, op(server.modprobe, module="floppy", _pyinfra_global=True)
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
        MountCommand(
            path="/data",
            options=("rw", "noatime"),
            device="/dev/sd0a",
            fs_type="ufs",
            remount=True,
            bsd=True,
        ).shell()
        == "mount -uo rw,noatime -t ufs /dev/sd0a /data"
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
    ("path", "mounts", "kernel", "kwargs", "expected", "changed"),
    [
        # pyinfra's own fifteen operation fixtures
        # (tests/operations/server.mount/), replayed verbatim over the
        # SimulatedHost seams. The bare {} info dicts (mount_exists.json,
        # unmount.json) feed the mounts seam as-is: only membership matters.
        # mount.json
        ("/data", {}, "Linux", {}, ["mount /data"], True),
        # mount_device.json
        ("/data", {}, "Linux", {"device": "/dev/sdb1"},
         ["mount /dev/sdb1 /data"], True),
        # mount_device_fstype.json
        ("/data", {}, "Linux", {"device": "/dev/sdc3", "fs_type": "xfs"},
         ["mount -t xfs /dev/sdc3 /data"], True),
        # mount_options.json
        ("/data", {}, "Linux", {"options": ["rw"]}, ["mount -o rw /data"], True),
        # mount_exists.json
        ("/data", {"/data": {}}, "Linux", {}, [], False),
        # mount_exists_device_match.json
        ("/data",
         {"/data": {"device": "/dev/sdb1", "type": "ext4",
                    "options": ["rw", "relatime"]}},
         "Linux", {"device": "/dev/sdb1"}, [], False),
        # mount_exists_device_fallback.json
        ("/data",
         {"/mnt/data": {"device": "/dev/sdb1", "type": "ext4",
                        "options": ["rw", "relatime"]}},
         "Linux", {"device": "/dev/sdb1"}, [], False),
        # unmount.json
        ("/data", {"/data": {}}, "Linux", {"mounted": False},
         ["umount /data"], True),
        # unmount_needs_quotes.json
        ("/data dir; rm -rf /", {"/data dir; rm -rf /": {}}, "Linux",
         {"mounted": False}, ["umount '/data dir; rm -rf /'"], True),
        # unmount_device_fallback.json
        ("/data",
         {"/mnt/data": {"device": "/dev/sdb1", "type": "ext4",
                        "options": ["rw", "relatime"]}},
         "Linux", {"mounted": False, "device": "/dev/sdb1"},
         ["umount /mnt/data"], True),
        # unmount_device_fallback_needs_quotes.json
        ("/data",
         {"/mnt/my data; rm -rf /": {"device": "/dev/sdb1", "type": "ext4",
                                    "options": ["rw", "relatime"]}},
         "Linux", {"mounted": False, "device": "/dev/sdb1"},
         ["umount '/mnt/my data; rm -rf /'"], True),
        # remount_options.json
        ("/data", {"/data": {"options": ["rw"]}}, "Linux",
         {"options": ["rw", "noatime"]},
         ["mount -o remount,rw,noatime /data"], True),
        # remount_needs_quotes.json
        ("/data dir; rm -rf /", {"/data dir; rm -rf /": {"options": ["rw"]}},
         "Linux", {"options": ["rw", "noatime"]},
         ["mount -o remount,rw,noatime '/data dir; rm -rf /'"], True),
        # remount_options_device_fallback.json
        ("/data",
         {"/mnt/data": {"device": "/dev/sdb1", "type": "ext4",
                        "options": ["rw"]}},
         "Linux", {"device": "/dev/sdb1", "options": ["rw", "noatime"]},
         ["mount -o remount,rw,noatime /mnt/data"], True),
        # remount_options_bsd.json
        ("/data",
         {"/data": {"options": ["rw"], "type": "ufs", "device": "/dev/sd0a"}},
         "*BSD", {"options": ["rw", "noatime"]},
         ["mount -uo rw,noatime -t ufs /dev/sd0a /data"], True),
    ],
)
async def test_mount_matches_pyinfra_operation_fixtures(
    path, mounts: dict, kernel: str, kwargs: dict, expected: list[str], changed: bool
):
    host = SimulatedHost(name="h", mounts=mounts, os_kernel=kernel)
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
    # act on the found mount point, not the requested path (pyinfra).
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
    # pyinfra's mount_exists.json noop_description, verbatim.
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
    # trigger a remount, and the noop wording stays pyinfra's "is mounted".
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


async def test_mount_remounts_on_bsd_with_fact_device_and_type():
    # The Kernel fact picks the -uo form; device/type come from the Mounts
    # entry, not the request (pyinfra's BSD remount branch).
    host = SimulatedHost(
        name="h",
        os_kernel="FreeBSD",
        mounts={"/data": {"options": ["rw"], "type": "ufs", "device": "/dev/sd0a"}},
    )
    ctx, commands = await materialize(
        host, op(server.mount, path="/data", options=("rw", "noatime"))
    )
    assert ctx.changed
    assert commands == [
        MountCommand(
            path="/data",
            options=("rw", "noatime"),
            device="/dev/sd0a",
            fs_type="ufs",
            remount=True,
            bsd=True,
        )
    ]


async def test_mount_absorbs_unknown_keyword_arguments():
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize(
        host, op(server.mount, path="/data", _pyinfra_global=True)
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
    # pyinfra's QuoteString: a name carrying shell metacharacters is quoted
    # (the argument of pyinfra's own set_quotes_injection.json fixture).
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
        # pyinfra's own operation fixtures (tests/operations/server.timezone/).
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
async def test_timezone_matches_pyinfra_operation_fixtures(
    zone: str, current: str, timedatectl: str | None, expected: list[str]
):
    host = SimulatedHost(name="h", os_timezone=current)
    if timedatectl is not None:
        host.command_paths = {"timedatectl": timedatectl}
    ctx, commands = await materialize(host, op(server.timezone, timezone=zone))
    assert ctx.changed == bool(expected)
    assert [cmd.shell() for cmd in commands] == expected
    if not expected:
        # pyinfra's noop_description, verbatim.
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
    # Like pyinfra, the fact comparison comes first: an already-set zone is
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
        host, op(server.timezone, timezone="Europe/Amsterdam", _pyinfra_global=True)
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
