# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the files module's ``Socket`` fact.

Covers ``reemote5.modules.files.files.Socket``: the inherited ``File``
guard/stat/ls command and parse chain against socket-typed rows (GNU
stat and the ``ls -ld`` fallback), the False answers for every other
path type and the None answers for absent/unmatched output, the ``File``
regression (a socket row is not a regular file), and the dedicated
``socket_stats`` ``SimulatedHost`` seam (dict/False/None answers,
separation from ``file_stats``, copy-on-read, tilde keying and
``gather_fact`` argument forwarding).
"""

from datetime import datetime

from reemote5.drivers import gather_fact
from reemote5.hosts import Host
from reemote5.modules.files.files import File, Socket
from tests.simulated_host import SimulatedHost

EPOCH = datetime(1970, 1, 1)


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell`` instead of connecting."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout
        self.shells: list[str] = []

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        self.shells.append(shell)
        return self._stdout


SOCKET_STAT_ROW = (
    "user=root group=root mode=srwxrwxrwx atime=1594804767 "
    "mtime=1594804767 ctime=0 size=0 '/var/run/docker.sock'\n"
)
SOCKET_STAT = {
    "user": "root",
    "group": "root",
    "mode": 777,
    "atime": datetime(2020, 7, 15, 9, 19, 27),
    "mtime": datetime(2020, 7, 15, 9, 19, 27),
    "ctime": EPOCH,
    "size": 0,
}


# --- Real transport: the inherited File chain against socket rows -----------------


def test_socket_command_is_files_command():
    # pyinfra's Socket subclasses File without touching the command; the
    # same guard/stat/ls chain (tilde branch included) is inherited.
    assert Socket.command("/var/run/docker.sock") == File.command("/var/run/docker.sock")
    assert Socket.command("~/agent.sock") == File.command("~/agent.sock")


async def test_socket_parses_a_gnu_stat_socket_row():
    # The 's' type flag maps to "socket" in the shared parse, so the row
    # reads as a dict -- the same shape File returns for a regular file.
    host = CannedShellHost(SOCKET_STAT_ROW)
    assert await Socket.process(host, "/var/run/docker.sock") == SOCKET_STAT
    assert host.shells == [Socket.command("/var/run/docker.sock")]


async def test_socket_parses_the_ls_fallback_socket_row():
    # Both stat forms can fail (e.g. a BusyBox host); ls -ld describes the
    # socket itself, with ls's None atime/ctime.
    host = CannedShellHost(
        "srwxrwxrwx 1 root root 0 Jan  1  1970 /var/run/docker.sock\n"
    )
    assert await Socket.process(host, "/var/run/docker.sock") == {
        "user": "root",
        "group": "root",
        "mode": 777,
        "atime": None,
        "mtime": EPOCH,
        "ctime": None,
        "size": 0,
    }


async def test_socket_is_false_for_a_regular_file():
    host = CannedShellHost(
        "user=pyinfra group=pyinfra mode=-rw-r--r-- atime=0 mtime=0 ctime=0 "
        "size=8 '/etc/hosts'\n"
    )
    assert await Socket.process(host, "/etc/hosts") is False


async def test_socket_is_false_for_a_directory():
    host = CannedShellHost(
        "user=root group=root mode=drwxr-xr-x atime=0 mtime=0 ctime=0 "
        "size=4096 '/var/run'\n"
    )
    assert await Socket.process(host, "/var/run") is False


async def test_socket_is_false_for_a_symlink():
    # find/stat follow the link question only through test -L; a symlink
    # row ('l' flag) is not a socket -> False.
    host = CannedShellHost(
        "user=root group=root mode=lrwxrwxrwx atime=0 mtime=0 ctime=0 "
        "size=12 '/var/run/docker.sock' -> '/run/docker.sock'\n"
    )
    assert await Socket.process(host, "/var/run/docker.sock") is False


async def test_socket_is_none_on_empty_output():
    # A missing path fails the existence guard -> no stdout -> None.
    assert await Socket.process(CannedShellHost(""), "/no/such.sock") is None


async def test_socket_is_none_on_unmatched_output():
    assert await Socket.process(CannedShellHost("not-gonna-match\n"), "/x") is None


async def test_file_still_rejects_a_socket_row():
    # The typed-sibling regression: Socket accepting 's' must not widen
    # File -- a socket is not a regular file.
    assert await File.process(CannedShellHost(SOCKET_STAT_ROW), "/var/run/docker.sock") is (
        False
    )


# --- Socket SimulatedHost seam ------------------------------------------------------


async def test_socket_reads_the_simulated_seam():
    host = SimulatedHost(
        name="h",
        socket_stats={"/var/run/docker.sock": SOCKET_STAT, "/etc/hosts": False},
    )
    assert await Socket.process(host, "/var/run/docker.sock") == SOCKET_STAT
    # A non-socket path reads as False, an absent key as None -- the same
    # three answers the real transport's guard/stat chain produces.
    assert await Socket.process(host, "/etc/hosts") is False
    assert await Socket.process(host, "/no/such.sock") is None


async def test_socket_seam_is_separate_from_file_stats():
    # A metadata dict carries no type flag, so file_stats alone cannot
    # answer the socket-typed question for the same path (the
    # Directory/Link precedent).
    host = SimulatedHost(
        name="h",
        file_stats={"/var/run/docker.sock": False},
        socket_stats={"/var/run/docker.sock": SOCKET_STAT},
    )
    assert await Socket.process(host, "/var/run/docker.sock") == SOCKET_STAT
    assert await File.process(host, "/var/run/docker.sock") is False


async def test_socket_seam_is_keyed_by_the_caller_supplied_path():
    # The command leaves ~/ for the remote shell, so the seam keys on the
    # path string the caller passed, tilde prefix included.
    host = SimulatedHost(name="h", socket_stats={"~/agent.sock": SOCKET_STAT})
    assert await Socket.process(host, "~/agent.sock") == SOCKET_STAT
    assert await Socket.process(host, "/root/agent.sock") is None


async def test_socket_seam_returns_a_copy():
    host = SimulatedHost(name="h", socket_stats={"/var/run/docker.sock": SOCKET_STAT})
    stats = await Socket.process(host, "/var/run/docker.sock")
    stats["mode"] = 660
    assert host.socket_stats["/var/run/docker.sock"]["mode"] == 777


async def test_socket_gather_fact_forwards_the_path():
    hosts = [
        SimulatedHost(
            name="web1", socket_stats={"/var/run/docker.sock": SOCKET_STAT}
        ),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, Socket, "/var/run/docker.sock") == {
        "web1": SOCKET_STAT,
        "web2": None,
    }