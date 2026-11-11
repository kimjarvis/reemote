# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the files module: the ``File``, ``Directory``, ``Link`` and
``SameFile`` facts.

Covers ``reemote.modules.files.files.File``: the baseline's own thirteen
``files.File`` fixtures over the real-transport path (GNU stat rows, the
``ls -ld`` fallback with its SELinux marker and HH:MM current-year stamps,
setuid/setgid/sticky modes, directory/link False answers, tilde expansion
and path quoting), stat/ls parsing edge cases, and the ``SimulatedHost``
``file_stats`` seam (dict/False/None answers, copy-on-read, and
``gather_fact`` argument forwarding).

Covers ``reemote.modules.files.files.Directory`` the same way: the baseline's
six ``files.Directory`` fixtures (stat and ls rows for directories,
file/link False answers, ACL/extended-attr markers, DD-MMM date order),
the shared command/parse chain, sticky/setgid directory modes, and the
separate ``directory_stats`` seam.

Covers ``reemote.modules.files.files.Link`` the same way: the baseline's six
``files.Link`` fixtures (GNU stat rows with single-quote, backtick and
double-quote ``%N`` quoting, the ls fallback naming the target, file/
directory False answers), ``link_target`` extraction edge cases (dangling
links, arrow-bearing targets, rows without an arrow), and the separate
``link_stats`` seam.

Covers ``reemote.modules.files.files.SameFile``: the baseline's two
``files.SameFile`` fixtures verbatim (the ``test -ef`` probe answering true
and false), the shell quoting of both arguments, and the ``same_files``
``SimulatedHost`` seam (symmetric pair lookup, copy-on-read semantics for a
frozen pair set, and ``gather_fact`` argument forwarding).
"""

from datetime import UTC, datetime

from reemote.drivers import gather_fact
from reemote.hosts import Host
from reemote.modules.files.files import Directory, File, Link, SameFile
from tests.simulated_host import SimulatedHost

# The baseline's ls fallback assumes the *current* year for an "MMM DD HH:MM"
# stamp (its own fixtures hard-code the year the test was written in), so
# the expected mtime for those two fixtures is computed, not literal.
CURRENT_YEAR = datetime.now(UTC).year

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


# --- Real-transport parity: the baseline's own files.File fixtures ------------------


async def test_file_parses_baseline_valid_fixture():
    # The baseline's valid.json fixture: a GNU stat row with a space-bearing
    # group name -> the metadata dict (naive UTC datetimes, octal int mode).
    host = CannedShellHost(
        "user=reemote group=domain users mode=-rwxrwx--- atime=1594804767 "
        "mtime=1594804767 ctime=0 size=8 '/home/reemote/fil-@_e.txt'\n"
    )
    assert await File.process(host, "/home/reemote/fil-@_e.txt") == {
        "user": "reemote",
        "group": "domain users",
        "mode": 770,
        "atime": datetime(2020, 7, 15, 9, 19, 27),
        "mtime": datetime(2020, 7, 15, 9, 19, 27),
        "ctime": EPOCH,
        "size": 8,
    }
    assert host.shells == [File.command("/home/reemote/fil-@_e.txt")]


def test_file_command_guards_then_stat_then_ls():
    # The full the baseline command shape: existence guard (test -e || test -L),
    # GNU stat, BSD stat fallback, then the ls -ld fallback.
    assert File.command("/etc/hosts") == (
        "! (test -e /etc/hosts || test -L /etc/hosts ) || "
        "( stat -c 'user=%U group=%G mode=%A atime=%X mtime=%Y ctime=%Z size=%s %N' "
        "/etc/hosts 2> /dev/null || "
        "stat -f 'user=%Su group=%Sg mode=%Sp atime=%a mtime=%m ctime=%c size=%z %N%SY' "
        "/etc/hosts || ls -ld /etc/hosts )"
    )


async def test_file_quotes_baseline_injection_fixture():
    # The baseline's valid_needs_quotes.json fixture: shell metacharacters in the
    # path are quoted, and the stat row still parses.
    host = CannedShellHost(
        "user=reemote group=domain users mode=-rwxrwx--- atime=1594804767 "
        "mtime=1594804767 ctime=0 size=8 'fil () &&-@_e.txt'\n"
    )
    assert await File.process(host, "fil () &&-@_e.txt") == {
        "user": "reemote",
        "group": "domain users",
        "mode": 770,
        "atime": datetime(2020, 7, 15, 9, 19, 27),
        "mtime": datetime(2020, 7, 15, 9, 19, 27),
        "ctime": EPOCH,
        "size": 8,
    }
    assert "test -e 'fil () &&-@_e.txt'" in host.shells[0]
    assert "ls -ld 'fil () &&-@_e.txt'" in host.shells[0]


async def test_file_parses_baseline_valid_with_space_fixture():
    # The baseline's valid_with_space.json fixture: a space-bearing path is
    # quoted and a zero size stays the int 0.
    host = CannedShellHost(
        "user=reemote group=reemote mode=-rw-r--r-- atime=1594804348 "
        "mtime=1594804348 ctime=0 size=0 '/home/reemote dir/fil-@_e.txt'\n"
    )
    assert await File.process(host, "/home/reemote dir/fil-@_e.txt") == {
        "user": "reemote",
        "group": "reemote",
        "mode": 644,
        "atime": datetime(2020, 7, 15, 9, 12, 28),
        "mtime": datetime(2020, 7, 15, 9, 12, 28),
        "ctime": EPOCH,
        "size": 0,
    }
    assert "test -e '/home/reemote dir/fil-@_e.txt'" in host.shells[0]


async def test_file_keeps_a_leading_tilde_unquoted():
    # The baseline's tilde.json fixture: ~/ must reach the shell unquoted so it
    # expands; the row's timestamps parse into naive UTC datetimes.
    host = CannedShellHost(
        "user=reemote group=reemote mode=-rw-r--r-- atime=1723483091 "
        "mtime=1722760187 ctime=1722760187 size=4310 '/home/reemote/.bashrc'\n"
    )
    assert await File.process(host, "~/.bashrc") == {
        "user": "reemote",
        "group": "reemote",
        "mode": 644,
        "atime": datetime(2024, 8, 12, 17, 18, 11),
        "mtime": datetime(2024, 8, 4, 8, 29, 47),
        "ctime": datetime(2024, 8, 4, 8, 29, 47),
        "size": 4310,
    }
    assert "test -e ~/.bashrc" in host.shells[0]
    assert "'~/.bashrc'" not in host.shells[0]


async def test_file_quotes_the_rest_of_a_tilde_path():
    # The baseline's tilde_with_space.json fixture: only the leading ~/ stays
    # bare; the space-bearing remainder is quoted.
    host = CannedShellHost(
        "user=reemote group=reemote mode=-rw-r--r-- atime=1723484281 "
        "mtime=1723484281 ctime=1723484281 size=0 "
        "'/home/reemote/My Documents/file'\n"
    )
    assert await File.process(host, "~/My Documents/file") == {
        "user": "reemote",
        "group": "reemote",
        "mode": 644,
        "atime": datetime(2024, 8, 12, 17, 38, 1),
        "mtime": datetime(2024, 8, 12, 17, 38, 1),
        "ctime": datetime(2024, 8, 12, 17, 38, 1),
        "size": 0,
    }
    assert "test -e ~/'My Documents/file'" in host.shells[0]


async def test_file_is_false_for_a_directory():
    # The baseline's directory.json fixture: the path exists but its type flag
    # is 'd', so the fact reads False, not a dict.
    host = CannedShellHost(
        "user=root group=root mode=drw-r--r-- atime=1594804583 "
        "mtime=1594804583 ctime=0 size=0 '/home/reemote'\n"
    )
    assert await File.process(host, "/home/reemote") is False


async def test_file_is_false_for_a_link():
    # The baseline's link.json fixture: a symlink row ('l' type flag) is not a
    # regular file -> False (Link is the link-accepting sibling).
    host = CannedShellHost(
        "user=root group=root mode=lrwxrwxrwx atime=1594804774 "
        "mtime=1594804770 ctime=0 size=6 '/home/reemote/mylink' -> 'file.txt'\n"
    )
    assert await File.process(host, "/home/reemote/mylink") is False


async def test_file_is_none_on_unmatched_output():
    # The baseline's invalid_output.json fixture: a row matching neither the
    # stat nor the ls format reads as None.
    host = CannedShellHost("not-gonna-match\n")
    assert await File.process(host, "/home/reemote/fil-@_e.txt") is None


async def test_file_is_none_on_empty_output():
    # A missing path fails the existence guard, so the command prints
    # nothing at all -> None.
    assert await File.process(CannedShellHost(""), "/no/such/file") is None


async def test_file_parses_setuid_setgid_modes():
    # The baseline's mode_setgid_setuid.json fixture: -rwsr-Sr-- -> 6744 (setuid
    # + setgid, the group's S meaning setgid without execute).
    host = CannedShellHost(
        "user=reemote group=reemote mode=-rwsr-Sr-- atime=0 mtime=0 ctime=0 "
        "size=8 'file.txt'\n"
    )
    assert await File.process(host, "file.txt") == {
        "user": "reemote",
        "group": "reemote",
        "mode": 6744,
        "atime": EPOCH,
        "mtime": EPOCH,
        "ctime": EPOCH,
        "size": 8,
    }


async def test_file_parses_the_sticky_mode():
    # The baseline's mode_sticky.json fixture: -rw-r--r-T -> 1644 (sticky bit
    # without execute).
    host = CannedShellHost(
        "user=reemote group=reemote mode=-rw-r--r-T atime=0 mtime=0 ctime=0 "
        "size=8 'file.txt'\n"
    )
    assert await File.process(host, "file.txt") == {
        "user": "reemote",
        "group": "reemote",
        "mode": 1644,
        "atime": EPOCH,
        "mtime": EPOCH,
        "ctime": EPOCH,
        "size": 8,
    }


async def test_file_parses_the_ls_fallback_fixture():
    # The baseline's ls_fallback.json fixture: an ls -ld row with a year stamp;
    # ls provides no atime/ctime, so those read as None.
    host = CannedShellHost(
        "-rw-r--r--    1 root     root           127 Jan  1  1970 /etc/hosts\n"
    )
    assert await File.process(host, "/etc/hosts") == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "atime": None,
        "mtime": EPOCH,
        "ctime": None,
        "size": 127,
    }


async def test_file_parses_the_ls_selinux_marker_fixture():
    # The baseline's ls_fallback_selinux.json fixture: the trailing '.' (SELinux)
    # marker is stripped from the mode, and an "MMM DD HH:MM" stamp assumes
    # the current year.
    host = CannedShellHost("-rw-r--r--. 1 src src 1024 Nov  7 19:21 src\n")
    assert await File.process(host, "src") == {
        "user": "src",
        "group": "src",
        "mode": 644,
        "atime": None,
        "mtime": datetime(CURRENT_YEAR, 11, 7, 19, 21),
        "ctime": None,
        "size": 1024,
    }


async def test_file_parses_the_ls_recent_time_fixture():
    # The baseline's ls_fallback_time.json fixture: a recent file's HH:MM stamp
    # resolves against the current year, like the baseline's own parser.
    host = CannedShellHost(
        "-rw-r--r--    1 user     user           1024 Jun 26 14:30 /tmp/recent_file\n"
    )
    assert await File.process(host, "/tmp/recent_file") == {
        "user": "user",
        "group": "user",
        "mode": 644,
        "atime": None,
        "mtime": datetime(CURRENT_YEAR, 6, 26, 14, 30),
        "ctime": None,
        "size": 1024,
    }


# --- Parser edge cases ---------------------------------------------------------


def test_file_parse_accepts_a_bsd_stat_row():
    # BSD stat -f prints the same keyed shape, so the one regex parses it.
    assert File.parse(
        [
            (
                "user=root group=wheel mode=-rw-r--r-- atime=1594804767 "
                "mtime=1594804767 ctime=1594804767 size=8 'file.txt'"
            )
        ]
    ) == {
        "user": "root",
        "group": "wheel",
        "mode": 644,
        "atime": datetime(2020, 7, 15, 9, 19, 27),
        "mtime": datetime(2020, 7, 15, 9, 19, 27),
        "ctime": datetime(2020, 7, 15, 9, 19, 27),
        "size": 8,
    }


def test_file_parse_handles_a_negative_epoch():
    # The stat regex allows negative timestamps (pre-1970 mtimes).
    parsed = File.parse(
        ["user=r group=r mode=-rw-r--r-- atime=-1 mtime=0 ctime=0 size=0 'f'"]
    )
    assert parsed["atime"] == datetime(1969, 12, 31, 23, 59, 59)


def test_file_parse_accepts_dd_mmm_date_order():
    # Some locales print "DD MMM" instead of "MMM DD".
    parsed = File.parse(["-rw-r--r-- 1 user user 1024 26 Jun 2025 /tmp/f"])
    assert parsed["mtime"] == datetime(2025, 6, 26)


def test_file_parse_unknown_month_reads_as_none_mtime():
    parsed = File.parse(["-rw-r--r-- 1 user user 1024 Xxx 26 2025 /tmp/f"])
    assert parsed["mtime"] is None
    assert parsed["mode"] == 644


def test_file_parse_ls_fallback_reports_non_files_as_false():
    assert File.parse(["drwxr-xr-x 2 root root 4096 Jan  1  1970 /etc"]) is False
    # A dangling symlink: both stat forms follow it and fail, so ls -ld
    # describes the link itself -> False.
    assert (
        File.parse(["lrwxrwxrwx 1 root root 8 Jan  1  1970 /link -> missing.txt"])
        is False
    )


def test_file_parse_empty_and_garbage_read_as_none():
    assert File.parse([]) is None
    assert File.parse(["not-gonna-match"]) is None


# --- SimulatedHost seam --------------------------------------------------------

STAT = {
    "user": "root",
    "group": "root",
    "mode": 644,
    "atime": datetime(2020, 7, 15, 9, 19, 27),
    "mtime": datetime(2020, 7, 15, 9, 19, 27),
    "ctime": EPOCH,
    "size": 127,
}


async def test_file_reads_the_simulated_seam():
    host = SimulatedHost(name="h", file_stats={"/etc/hosts": STAT, "/etc": False})
    assert await File.process(host, "/etc/hosts") == STAT
    # A non-file path reads as False, an absent key as None -- the same
    # three answers the real transport's guard/stat chain produces.
    assert await File.process(host, "/etc") is False
    assert await File.process(host, "/no/such/file") is None


async def test_file_seam_is_keyed_by_the_caller_supplied_path():
    # The command leaves ~/ for the remote shell, so the seam keys on the
    # path string the caller passed, tilde prefix included.
    host = SimulatedHost(name="h", file_stats={"~/.bashrc": STAT})
    assert await File.process(host, "~/.bashrc") == STAT
    assert await File.process(host, "/root/.bashrc") is None


async def test_file_seam_returns_a_copy():
    host = SimulatedHost(name="h", file_stats={"/etc/hosts": STAT})
    stats = await File.process(host, "/etc/hosts")
    stats["mode"] = 777
    assert host.file_stats["/etc/hosts"]["mode"] == 644


async def test_file_gather_fact_forwards_the_path():
    hosts = [
        SimulatedHost(name="web1", file_stats={"/etc/hosts": STAT}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, File, "/etc/hosts") == {
        "web1": STAT,
        "web2": None,
    }


# --- Real-transport parity: the baseline's own files.Directory fixtures --------------


async def test_directory_parses_baseline_valid_fixture():
    # The baseline's valid.json fixture: a GNU stat row whose type flag is 'd'
    # -> the metadata dict (the same shape File returns for a regular file).
    host = CannedShellHost(
        "user=reemote group=reemote mode=drw-r--r-- atime=1594804583 "
        "mtime=1594804583 ctime=0 size=0 '/home/reemote/myd@-_ir'\n"
    )
    assert await Directory.process(host, "/home/reemote/myd@-_ir") == {
        "user": "reemote",
        "group": "reemote",
        "mode": 644,
        "atime": datetime(2020, 7, 15, 9, 16, 23),
        "mtime": datetime(2020, 7, 15, 9, 16, 23),
        "ctime": EPOCH,
        "size": 0,
    }
    # The command is File's, verbatim -- the baseline's Directory only flips the
    # accepted type, so the fixture's command string is shared.
    assert host.shells == [
        (
            "! (test -e /home/reemote/myd@-_ir || test -L /home/reemote/myd@-_ir ) || "
            "( stat -c 'user=%U group=%G mode=%A atime=%X mtime=%Y ctime=%Z size=%s %N' "
            "/home/reemote/myd@-_ir 2> /dev/null || "
            "stat -f 'user=%Su group=%Sg mode=%Sp atime=%a mtime=%m ctime=%c size=%z %N%SY' "
            "/home/reemote/myd@-_ir || ls -ld /home/reemote/myd@-_ir )"
        )
    ]


async def test_directory_is_false_for_a_regular_file():
    # The baseline's file.json fixture: the path exists but its type flag is
    # '-', so the fact reads False, not a dict.
    host = CannedShellHost(
        "user=reemote group=reemote mode=-rwxrwxrwx atime=1594804767 "
        "mtime=1594804767 ctime=0 size=8 '/path/to/a/file'\n"
    )
    assert await Directory.process(host, "/path/to/a/file") is False


async def test_directory_is_false_for_a_link():
    # The baseline's link.json fixture: a symlink row ('l' type flag) is not a
    # directory -> False (Link is the link-accepting sibling).
    host = CannedShellHost(
        "user=root group=root mode=lrwxrwxrwx atime=1594804774 "
        "mtime=1594804770 ctime=0 size=6 '/home/reemote/mylink' -> 'file.txt'\n"
    )
    assert await Directory.process(host, "/home/reemote/mylink") is False


async def test_directory_parses_the_ls_fallback_fixture():
    # The baseline's ls_fallback.json fixture: an ls -ld row for / with a year
    # stamp; ls provides no atime/ctime, so those read as None.
    host = CannedShellHost(
        "drwxr-xr-x    1 root     root           416 Jan  1  1970 /\n"
    )
    assert await Directory.process(host, "/") == {
        "user": "root",
        "group": "root",
        "mode": 755,
        "atime": None,
        "mtime": EPOCH,
        "ctime": None,
        "size": 416,
    }


async def test_directory_parses_the_freebsd_acl_marker_fixture():
    # The baseline's ls_fallback_freebsd.json fixture: the trailing '+' (ACL)
    # marker is stripped from the mode, and an "MMM DD HH:MM" stamp assumes
    # the current year.
    host = CannedShellHost(
        "drwxr-xr-x+ 11 root wheel 420 Mar 28 16:00 /compat/devuan01/var/hostlog\n"
    )
    assert await Directory.process(host, "/compat/devuan01/var/hostlog") == {
        "user": "root",
        "group": "wheel",
        "mode": 755,
        "atime": None,
        "mtime": datetime(CURRENT_YEAR, 3, 28, 16, 0),
        "ctime": None,
        "size": 420,
    }


async def test_directory_parses_the_macos_attr_marker_fixture():
    # The baseline's ls_fallback_macos.json fixture: the trailing '@' (extended
    # attrs) marker is stripped, and the "DD MMM HH:MM" date order parses
    # against the current year.
    host = CannedShellHost("drwxr-xr-x@ 12 xonic staff 408 22 Jun 19:00 .\n")
    assert await Directory.process(host, ".") == {
        "user": "xonic",
        "group": "staff",
        "mode": 755,
        "atime": None,
        "mtime": datetime(CURRENT_YEAR, 6, 22, 19, 0),
        "ctime": None,
        "size": 408,
    }


# --- Directory parser edge cases -----------------------------------------------


def test_directory_command_is_files_command():
    # The baseline's Directory subclasses File without touching the command;
    # the same guard/stat/ls chain (tilde branch included) is inherited.
    assert Directory.command("/etc") == File.command("/etc")
    assert Directory.command("~/My Documents") == File.command("~/My Documents")


def test_directory_parse_parses_the_sticky_mode():
    # /tmp's classic drwxrwxrwt -> 1777 (sticky bit with execute).
    parsed = Directory.parse(["drwxrwxrwt 10 root root 4096 Jan  1  1970 /tmp"])
    assert parsed["mode"] == 1777


def test_directory_parse_parses_the_setgid_mode():
    # A setgid collaborative directory: drwxrwsr-x -> 2775.
    parsed = Directory.parse(["drwxrwsr-x 2 root staff 4096 Jan  1  1970 /srv/share"])
    assert parsed["mode"] == 2775


def test_directory_parse_reports_non_directories_as_false():
    # The stat and ls branches both flip on the type flag: a regular file
    # and a dangling symlink read as False, mirroring File's inverted answers.
    assert (
        Directory.parse(
            ["user=r group=r mode=-rw-r--r-- atime=0 mtime=0 ctime=0 size=0 'f'"]
        )
        is False
    )
    assert Directory.parse(["-rw-r--r-- 1 root root 127 Jan  1  1970 /etc/hosts"]) is False
    assert (
        Directory.parse(["lrwxrwxrwx 1 root root 8 Jan  1  1970 /link -> missing.txt"])
        is False
    )


def test_directory_parse_empty_and_garbage_read_as_none():
    assert Directory.parse([]) is None
    assert Directory.parse(["not-gonna-match"]) is None


# --- Directory SimulatedHost seam ----------------------------------------------

DIR_STAT = {
    "user": "root",
    "group": "root",
    "mode": 755,
    "atime": datetime(2020, 7, 15, 9, 16, 23),
    "mtime": datetime(2020, 7, 15, 9, 16, 23),
    "ctime": EPOCH,
    "size": 4096,
}


async def test_directory_reads_the_simulated_seam():
    host = SimulatedHost(
        name="h", directory_stats={"/etc": DIR_STAT, "/etc/hosts": False}
    )
    assert await Directory.process(host, "/etc") == DIR_STAT
    # A non-directory path reads as False, an absent key as None -- the
    # same three answers the real transport's guard/stat chain produces.
    assert await Directory.process(host, "/etc/hosts") is False
    assert await Directory.process(host, "/no/such/dir") is None


async def test_directory_seam_is_separate_from_file_stats():
    # A metadata dict carries no type flag, so the two typed facts read
    # separate stores: the same path can be a directory to Directory and
    # a non-file (False) to File.
    host = SimulatedHost(
        name="h",
        file_stats={"/etc": False},
        directory_stats={"/etc": DIR_STAT},
    )
    assert await Directory.process(host, "/etc") == DIR_STAT
    assert await File.process(host, "/etc") is False


async def test_directory_seam_is_keyed_by_the_caller_supplied_path():
    # The command leaves ~/ for the remote shell, so the seam keys on the
    # path string the caller passed, tilde prefix included.
    host = SimulatedHost(name="h", directory_stats={"~/Projects": DIR_STAT})
    assert await Directory.process(host, "~/Projects") == DIR_STAT
    assert await Directory.process(host, "/root/Projects") is None


async def test_directory_seam_returns_a_copy():
    host = SimulatedHost(name="h", directory_stats={"/etc": DIR_STAT})
    stats = await Directory.process(host, "/etc")
    stats["mode"] = 777
    assert host.directory_stats["/etc"]["mode"] == 755


async def test_directory_gather_fact_forwards_the_path():
    hosts = [
        SimulatedHost(name="web1", directory_stats={"/etc": DIR_STAT}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, Directory, "/etc") == {
        "web1": DIR_STAT,
        "web2": None,
    }


# --- Real-transport parity: the baseline's own files.Link fixtures ------------------


async def test_link_parses_baseline_valid_fixture():
    # The baseline's valid.json fixture: a GNU stat row whose %N names the link
    # and its single-quoted target -> the metadata dict plus link_target.
    host = CannedShellHost(
        "user=reemote group=reemote mode=lrwxrwxrwx atime=1594804774 "
        "mtime=1594804770 ctime=0 size=6 "
        "'/home/reemote/my@-_link' -> 'my_f@-ile.txt'\n"
    )
    assert await Link.process(host, "/home/reemote/my@-_link") == {
        "user": "reemote",
        "group": "reemote",
        "mode": 777,
        "atime": datetime(2020, 7, 15, 9, 19, 34),
        "mtime": datetime(2020, 7, 15, 9, 19, 30),
        "ctime": EPOCH,
        "size": 6,
        "link_target": "my_f@-ile.txt",
    }
    # The command is File's, verbatim -- the baseline's Link only flips the
    # accepted type, so the fixture's command string is shared.
    assert host.shells == [
        (
            "! (test -e /home/reemote/my@-_link || test -L /home/reemote/my@-_link ) || "
            "( stat -c 'user=%U group=%G mode=%A atime=%X mtime=%Y ctime=%Z size=%s %N' "
            "/home/reemote/my@-_link 2> /dev/null || "
            "stat -f 'user=%Su group=%Sg mode=%Sp atime=%a mtime=%m ctime=%c size=%z %N%SY' "
            "/home/reemote/my@-_link || ls -ld /home/reemote/my@-_link )"
        )
    ]


async def test_link_parses_baseline_centos6_backtick_fixture():
    # The baseline's valid_centos6.json fixture: older GNU/RHEL 6 stat quotes %N
    # as `name' -> the backtick quoting is stripped from the target too.
    host = CannedShellHost(
        "user=root group=root mode=lrw-r--r-- atime=1594804774 "
        "mtime=1594804770 ctime=0 size=11 `link' -> `install.log'\n"
    )
    assert await Link.process(host, "link") == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "atime": datetime(2020, 7, 15, 9, 19, 34),
        "mtime": datetime(2020, 7, 15, 9, 19, 30),
        "ctime": EPOCH,
        "size": 11,
        "link_target": "install.log",
    }


async def test_link_parses_baseline_uutils_double_quote_fixture():
    # The baseline's valid_uutils.json fixture: uutils-coreutils stat quotes %N
    # in double quotes -> that quoting is stripped as well.
    host = CannedShellHost(
        "user=reemote group=reemote mode=lrwxrwxrwx atime=1594804774 "
        "mtime=1594804770 ctime=0 size=8 "
        '"/home/reemote/mylink" -> "file.txt"\n'
    )
    assert await Link.process(host, "/home/reemote/mylink") == {
        "user": "reemote",
        "group": "reemote",
        "mode": 777,
        "atime": datetime(2020, 7, 15, 9, 19, 34),
        "mtime": datetime(2020, 7, 15, 9, 19, 30),
        "ctime": EPOCH,
        "size": 8,
        "link_target": "file.txt",
    }


async def test_link_parses_the_ls_fallback_fixture():
    # The baseline's ls_fallback.json fixture: an ls -ld row naming the link's
    # unquoted target after " -> "; ls provides no atime/ctime (None), and
    # the year stamp is literal (2025), not the current year.
    host = CannedShellHost(
        "lrwxrwxrwx    1 root     root            12 Apr  2  2025 "
        "/usr/bin/vi -> /bin/busybox\n"
    )
    assert await Link.process(host, "/usr/bin/vi") == {
        "user": "root",
        "group": "root",
        "mode": 777,
        "atime": None,
        "mtime": datetime(2025, 4, 2),
        "ctime": None,
        "size": 12,
        "link_target": "/bin/busybox",
    }


async def test_link_is_false_for_a_directory():
    # The baseline's directory.json fixture: the path exists but its type flag
    # is 'd', so the fact reads False, not a dict.
    host = CannedShellHost(
        "user=root group=root mode=drw-r--r-- atime=1594804583 "
        "mtime=1594804583 ctime=0 size=0 '/home/reemote/myd@-_ir'\n"
    )
    assert await Link.process(host, "/home/reemote/myd@-_ir") is False


async def test_link_is_false_for_a_regular_file():
    # The baseline's file.json fixture: the path exists but its type flag is
    # '-', so the fact reads False, not a dict.
    host = CannedShellHost(
        "user=reemote group=reemote mode=-rwxrwxrwx atime=1594804767 "
        "mtime=1594804767 ctime=0 size=8 '/home/reemote/fil-@_e.txt'\n"
    )
    assert await Link.process(host, "/home/reemote/fil-@_e.txt") is False


# --- Link parser edge cases ----------------------------------------------------


def test_link_command_is_files_command():
    # The baseline's Link subclasses File without touching the command; the
    # same guard/stat/ls chain (tilde branch included) is inherited.
    assert Link.command("/usr/bin/vi") == File.command("/usr/bin/vi")
    assert Link.command("~/My Documents") == File.command("~/My Documents")


def test_link_parse_reports_a_dangling_symlink_with_its_target():
    # Both stat forms follow a dangling link and fail, so ls -ld describes
    # the link itself: still a dict (test -L let it through the guard),
    # with the missing target named and ls's None atime/ctime.
    parsed = Link.parse(["lrwxrwxrwx 1 root root 8 Jan  1  1970 /link -> missing.txt"])
    assert parsed["link_target"] == "missing.txt"
    assert parsed["mode"] == 777
    assert parsed["atime"] is None
    assert parsed["ctime"] is None
    assert parsed["mtime"] == EPOCH


def test_link_parse_keeps_an_arrow_inside_the_target():
    # Only the first " -> " separates link from target, so a target that
    # itself contains an arrow stays intact.
    parsed = Link.parse(["lrwxrwxrwx 1 root root 9 Jan  1  1970 /link -> b -> c"])
    assert parsed["link_target"] == "b -> c"


def test_link_parse_ls_row_without_arrow_has_no_link_target():
    # An ls row that names no target still parses as a link, just without
    # the link_target key (the baseline only adds it when " -> " is present).
    parsed = Link.parse(["lrwxrwxrwx 1 root root 8 Jan  1  1970 /link"])
    assert parsed["mode"] == 777
    assert "link_target" not in parsed


def test_link_parse_reports_non_links_as_false():
    # The stat and ls branches both flip on the type flag: a regular file
    # and a directory read as False, mirroring File's inverted answers.
    assert (
        Link.parse(["user=r group=r mode=-rw-r--r-- atime=0 mtime=0 ctime=0 size=0 'f'"])
        is False
    )
    assert Link.parse(["drwxr-xr-x 2 root root 4096 Jan  1  1970 /etc"]) is False


def test_link_parse_empty_and_garbage_read_as_none():
    assert Link.parse([]) is None
    assert Link.parse(["not-gonna-match"]) is None


async def test_link_is_none_on_empty_output():
    # A missing path fails the existence guard (test -e and test -L both
    # fail), so the command prints nothing at all -> None.
    assert await Link.process(CannedShellHost(""), "/no/such/link") is None


# --- Link SimulatedHost seam ---------------------------------------------------

LINK_STAT = {
    "user": "root",
    "group": "root",
    "mode": 777,
    "atime": datetime(2020, 7, 15, 9, 19, 34),
    "mtime": datetime(2020, 7, 15, 9, 19, 30),
    "ctime": EPOCH,
    "size": 12,
    "link_target": "/bin/busybox",
}


async def test_link_reads_the_simulated_seam():
    host = SimulatedHost(
        name="h", link_stats={"/usr/bin/vi": LINK_STAT, "/etc/hosts": False}
    )
    assert await Link.process(host, "/usr/bin/vi") == LINK_STAT
    # A non-link path reads as False, an absent key as None -- the same
    # three answers the real transport's guard/stat chain produces.
    assert await Link.process(host, "/etc/hosts") is False
    assert await Link.process(host, "/no/such/link") is None


async def test_link_seam_is_separate_from_file_and_directory_stats():
    # A metadata dict carries no type flag, so the three typed facts read
    # separate stores: the same path can be a link to Link and a non-file
    # (False) to File, without either store answering for the other.
    host = SimulatedHost(
        name="h",
        file_stats={"/usr/bin/vi": False},
        link_stats={"/usr/bin/vi": LINK_STAT},
    )
    assert await Link.process(host, "/usr/bin/vi") == LINK_STAT
    assert await File.process(host, "/usr/bin/vi") is False
    assert await Directory.process(host, "/usr/bin/vi") is None


async def test_link_seam_is_keyed_by_the_caller_supplied_path():
    # The command leaves ~/ for the remote shell, so the seam keys on the
    # path string the caller passed, tilde prefix included.
    host = SimulatedHost(name="h", link_stats={"~/current": LINK_STAT})
    assert await Link.process(host, "~/current") == LINK_STAT
    assert await Link.process(host, "/root/current") is None


async def test_link_seam_returns_a_copy():
    host = SimulatedHost(name="h", link_stats={"/usr/bin/vi": LINK_STAT})
    stats = await Link.process(host, "/usr/bin/vi")
    stats["link_target"] = "/elsewhere"
    assert host.link_stats["/usr/bin/vi"]["link_target"] == "/bin/busybox"


async def test_link_gather_fact_forwards_the_path():
    hosts = [
        SimulatedHost(name="web1", link_stats={"/usr/bin/vi": LINK_STAT}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, Link, "/usr/bin/vi") == {
        "web1": LINK_STAT,
        "web2": None,
    }


# --- Real-transport parity: the baseline's own files.SameFile fixtures ---------------


async def test_same_file_parses_baseline_same_file_fixture():
    # The baseline's same_file.json fixture, verbatim: /foo/hardlink and
    # /foo/file name one inode, so the ``test -ef`` probe prints true.
    host = CannedShellHost("true\n")
    assert await SameFile.process(host, "/foo/hardlink", "/foo/file") is True
    assert host.shells == [
        "test /foo/hardlink -ef /foo/file && echo true || echo false"
    ]


async def test_same_file_parses_baseline_different_file_fixture():
    # The baseline's different_file.json fixture, verbatim: two distinct inodes
    # print false -- the || branch exits zero, so a difference is never a
    # failed command (the reason the fact raises only HostConnectionError).
    host = CannedShellHost("false\n")
    assert await SameFile.process(host, "/foo/other", "/foo/file") is False
    assert host.shells == [
        "test /foo/other -ef /foo/file && echo true || echo false"
    ]


def test_same_file_command_quotes_both_arguments():
    # shlex quoting on both paths: safe paths stay bare (the fixtures
    # above) and a space-bearing path is quoted. Unlike the ``File``
    # family there is no ``~/`` exception -- the fact quotes verbatim
    # (the baseline's QuoteString shape), so a tilde path reaches the shell as
    # one quoted word and simply reads as an absent file.
    assert SameFile.command("/foo a", "/foo b") == (
        "test '/foo a' -ef '/foo b' && echo true || echo false"
    )
    assert SameFile.command("~/file", "/foo/file") == (
        "test '~/file' -ef /foo/file && echo true || echo false"
    )


def test_same_file_parse_is_line_list_equality():
    # Only the exact single "true" line is True; empty, noisy and near-miss
    # output all read False (the baseline's ``output == ["true"]`` rule).
    assert SameFile.parse(["true"]) is True
    assert SameFile.parse(["false"]) is False
    assert SameFile.parse([]) is False
    assert SameFile.parse(["true", "true"]) is False
    assert SameFile.parse(["truetrue"]) is False


async def test_same_file_is_false_on_empty_output():
    # A missing path exits zero through the || guard and prints nothing --
    # read as False, not as an error.
    assert await SameFile.process(CannedShellHost(""), "/no/such", "/foo/file") is False


# --- SameFile SimulatedHost seam ------------------------------------------------


async def test_same_file_reads_the_simulated_seam():
    host = SimulatedHost(name="h", same_files={("/foo/hardlink", "/foo/file")})
    assert await SameFile.process(host, "/foo/hardlink", "/foo/file") is True
    assert await SameFile.process(host, "/foo/other", "/foo/file") is False


async def test_same_file_seam_is_symmetric():
    # ``-ef`` asks a symmetric question, so the seam answers whichever way
    # the pair is stored -- a hard link is no more "of" one name than the
    # other, and the simulated ln makes no ordering promise.
    host = SimulatedHost(name="h", same_files={("/foo/hardlink", "/foo/file")})
    assert await SameFile.process(host, "/foo/file", "/foo/hardlink") is True


async def test_same_file_seam_bypasses_the_shell():
    # A SimulatedHost never shells out: an empty store answers False
    # without any command recorded (the seam is the whole read path).
    host = SimulatedHost(name="h")
    assert await SameFile.process(host, "/foo/hardlink", "/foo/file") is False


async def test_same_file_gather_fact_forwards_both_arguments():
    hosts = [
        SimulatedHost(name="web1", same_files={("/foo/hardlink", "/foo/file")}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, SameFile, "/foo/hardlink", "/foo/file") == {
        "web1": True,
        "web2": False,
    }