# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the files module's reemote5-original listing facts:
``FindFilesInfo`` and ``DirectoryEntries``.

Covers ``reemote5.modules.files.files.FindFilesInfo``: the ``find
-printf`` command construction (the shared filter set plus the
tab-separated metadata format), row parsing (six fields, integer
size/mode, naive-UTC mtime, malformed-row skips, deterministic path
sorting), and the command-keyed ``found_files`` ``SimulatedHost`` seam
(default [], per-dict copy-on-read, ``gather_fact`` kwargs forwarding).

Covers ``reemote5.modules.files.files.DirectoryEntries``: the shallow
``-mindepth 1 -maxdepth 1`` command, the ``%y`` type-letter mapping for
all seven path types, unknown-letter and malformed-row skips, name
sorting, and the path-keyed ``directory_entries`` seam.
"""

from datetime import datetime

from reemote5.drivers import gather_fact
from reemote5.hosts import Host
from reemote5.modules.files.files import DirectoryEntries, FindFilesInfo
from tests.simulated_host import SimulatedHost


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell`` instead of connecting."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout
        self.shells: list[str] = []

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        self.shells.append(shell)
        return self._stdout


# The -printf format as it reaches the shell: find interprets the \t and
# \n escapes itself, so the command carries them literally.
PRINTF = "-printf '%p\\t%s\\t%m\\t%u\\t%g\\t%T@\\n'"
MTIME = datetime(2020, 7, 15, 9, 19, 27)


# --- FindFilesInfo command construction ----------------------------------------------


def test_find_files_info_command_bare_and_filtered():
    assert FindFilesInfo.command("mydir") == f"find mydir -type f {PRINTF} || true"
    assert FindFilesInfo.command(
        "/etc/datadog-agent/conf.d", fname="conf.yaml", maxdepth=2
    ) == (
        "find /etc/datadog-agent/conf.d -type f -maxdepth 2 -name conf.yaml "
        f"{PRINTF} || true"
    )


# --- FindFilesInfo parser --------------------------------------------------------------


def test_find_files_info_parses_rows_sorted_by_path():
    # find's traversal order is filesystem-dependent; the fact sorts for
    # a deterministic listing. %T@ carries fractional seconds, truncated
    # to the File convention's whole-second naive UTC datetime.
    rows = [
        "mydir/b.yaml\t20\t644\troot\troot\t1594804767.0000000000",
        "mydir/a.yaml\t0\t4755\tdd-agent\tdd-agent\t0.0000000000",
    ]
    assert FindFilesInfo.parse(rows) == [
        {
            "path": "mydir/a.yaml",
            "size": 0,
            "mode": 4755,
            "user": "dd-agent",
            "group": "dd-agent",
            "mtime": datetime(1970, 1, 1),
        },
        {
            "path": "mydir/b.yaml",
            "size": 20,
            "mode": 644,
            "user": "root",
            "group": "root",
            "mtime": MTIME,
        },
    ]


def test_find_files_info_skips_malformed_rows():
    # A tab/newline-bearing path breaks its own row: wrong field counts
    # and non-integer size/mode are skipped, never misparsed.
    rows = [
        "mydir/ok\t1\t644\tu\tg\t0",
        "mydir/short\t1\t644\tu\tg",  # five fields
        "mydir/tab\tpath\t1\t644\tu\tg\t0",  # seven fields
        "mydir/badsize\tx\t644\tu\tg\t0",
        "mydir/badmode\t1\tx\tu\tg\t0",
    ]
    parsed = FindFilesInfo.parse(rows)
    assert [entry["path"] for entry in parsed] == ["mydir/ok"]


def test_find_files_info_unparseable_mtime_reads_none():
    # A bad %T@ epoch keeps the hit (size/mode/owner still answer) with a
    # None mtime rather than dropping the row.
    parsed = FindFilesInfo.parse(["mydir/f\t1\t644\tu\tg\tnot-a-time"])
    assert parsed[0]["path"] == "mydir/f"
    assert parsed[0]["mtime"] is None


def test_find_files_info_parse_empty_reads_empty():
    assert FindFilesInfo.parse([]) == []


async def test_find_files_info_process_parses_canned_output():
    host = CannedShellHost("mydir/b.yaml\t20\t644\troot\troot\t1594804767.0\n")
    assert await FindFilesInfo.process(host, "mydir") == [
        {
            "path": "mydir/b.yaml",
            "size": 20,
            "mode": 644,
            "user": "root",
            "group": "root",
            "mtime": MTIME,
        }
    ]
    assert host.shells == [FindFilesInfo.command("mydir")]


async def test_find_files_info_is_empty_on_empty_output():
    # A missing start path (or a BSD find without -printf) is masked by
    # the || true guard to [].
    assert await FindFilesInfo.process(CannedShellHost(""), "/no/such/dir") == []


# --- FindFilesInfo SimulatedHost seam ---------------------------------------------------

CONF_D = "/etc/datadog-agent/conf.d"
INFO_FIND = FindFilesInfo.command(CONF_D, fname="conf.yaml", maxdepth=2)
FOUND = [
    {
        "path": f"{CONF_D}/cpu/conf.yaml",
        "size": 20,
        "mode": 644,
        "user": "dd-agent",
        "group": "dd-agent",
        "mtime": MTIME,
    }
]


async def test_find_files_info_reads_the_command_keyed_seam():
    host = SimulatedHost(name="h", found_files={INFO_FIND: FOUND})
    assert await FindFilesInfo.process(
        host, CONF_D, fname="conf.yaml", maxdepth=2
    ) == FOUND
    # A different filter combination is a different question -> [].
    assert await FindFilesInfo.process(host, CONF_D) == []


async def test_find_files_info_seam_defaults_to_empty():
    assert await FindFilesInfo.process(SimulatedHost(name="h"), CONF_D) == []


async def test_find_files_info_seam_returns_per_dict_copies():
    host = SimulatedHost(name="h", found_files={INFO_FIND: FOUND})
    entries = await FindFilesInfo.process(host, CONF_D, fname="conf.yaml", maxdepth=2)
    entries[0]["size"] = 999
    entries.append({"path": "/injected"})
    assert host.found_files[INFO_FIND] == FOUND


async def test_find_files_info_gather_fact_forwards_path_and_filters():
    hosts = [
        SimulatedHost(name="web1", found_files={INFO_FIND: FOUND}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(
        hosts, FindFilesInfo, CONF_D, fname="conf.yaml", maxdepth=2
    ) == {"web1": FOUND, "web2": []}


# --- DirectoryEntries command and parser ------------------------------------------------


def test_directory_entries_command_is_shallow():
    # mindepth 1 excludes the directory itself; maxdepth 1 never recurses.
    assert DirectoryEntries.command("/etc/apt/sources.list.d") == (
        "find /etc/apt/sources.list.d -mindepth 1 -maxdepth 1 "
        "-printf '%f\\t%y\\n' || true"
    )


def test_directory_entries_maps_every_type_letter_and_sorts():
    rows = [
        "zsock\ts",
        "adir\td",
        "afile\tf",
        "alink\tl",
        "apipe\tp",
        "ablock\tb",
        "achar\tc",
    ]
    assert DirectoryEntries.parse(rows) == [
        {"name": "ablock", "type": "block"},
        {"name": "achar", "type": "character"},
        {"name": "adir", "type": "directory"},
        {"name": "afile", "type": "file"},
        {"name": "alink", "type": "link"},
        {"name": "apipe", "type": "fifo"},
        {"name": "zsock", "type": "socket"},
    ]


def test_directory_entries_skips_unknown_letters_and_malformed_rows():
    rows = [
        "ok\tf",
        "weird\tx",  # unknown type letter
        "onlyname",  # one field
        "tab\tname\tf",  # three fields
    ]
    assert DirectoryEntries.parse(rows) == [{"name": "ok", "type": "file"}]


def test_directory_entries_parse_empty_reads_empty():
    assert DirectoryEntries.parse([]) == []


async def test_directory_entries_process_parses_canned_output():
    host = CannedShellHost("datadog.list\tf\nsubdir\td\n.hidden\tf\n")
    # Dotfiles are included (find does not skip them), sorted by name.
    assert await DirectoryEntries.process(host, "/etc/apt/sources.list.d") == [
        {"name": ".hidden", "type": "file"},
        {"name": "datadog.list", "type": "file"},
        {"name": "subdir", "type": "directory"},
    ]
    assert host.shells == [DirectoryEntries.command("/etc/apt/sources.list.d")]


async def test_directory_entries_is_empty_on_empty_output():
    assert await DirectoryEntries.process(CannedShellHost(""), "/no/such/dir") == []


# --- DirectoryEntries SimulatedHost seam --------------------------------------------------

LIST_D = "/etc/apt/sources.list.d"
ENTRIES = [
    {"name": "datadog.list", "type": "file"},
    {"name": "legacy.list", "type": "file"},
]


async def test_directory_entries_reads_the_path_keyed_seam():
    # The command has no filters, so the path alone identifies the
    # question -- unlike the command-keyed find seams.
    host = SimulatedHost(name="h", directory_entries={LIST_D: ENTRIES})
    assert await DirectoryEntries.process(host, LIST_D) == ENTRIES
    assert await DirectoryEntries.process(host, "/elsewhere") == []


async def test_directory_entries_seam_defaults_to_empty():
    assert await DirectoryEntries.process(SimulatedHost(name="h"), LIST_D) == []


async def test_directory_entries_seam_returns_per_dict_copies():
    host = SimulatedHost(name="h", directory_entries={LIST_D: ENTRIES})
    entries = await DirectoryEntries.process(host, LIST_D)
    entries[0]["name"] = "mutated"
    entries.append({"name": "injected", "type": "file"})
    assert host.directory_entries[LIST_D] == ENTRIES


async def test_directory_entries_gather_fact_forwards_the_path():
    hosts = [
        SimulatedHost(name="web1", directory_entries={LIST_D: ENTRIES}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, DirectoryEntries, LIST_D) == {
        "web1": ENTRIES,
        "web2": [],
    }