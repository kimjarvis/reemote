# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the files module's find-path fact family: ``FindFiles``,
``FindDirectories`` and ``FindLinks``.

Covers ``reemote5.modules.files.files.FindFiles``: the baseline's own four
``files.FindFiles`` fixtures over the real-transport path (the bare find,
the raw ``args`` escape hatch, a name glob and a wildcard name glob), the
shared filter command construction (size bounds in bytes, maxdepth,
iname/regex/path_match quoting, the baseline ``args`` guards, the
``quote_path=False`` escape hatch), ``_parse_size`` unit spellings, and
the command-keyed ``find_results`` ``SimulatedHost`` seam (per-filter
keying, default [], copy-on-read, ``gather_fact`` kwargs forwarding).

Covers ``reemote5.modules.files.files.FindDirectories`` and
``reemote5.modules.files.files.FindLinks`` the same way: the baseline's
``directories.json`` and ``links.json`` fixtures verbatim and the shared
base's ``-type d`` / ``-type l`` flag.
"""

import pytest

from reemote5.drivers import gather_fact
from reemote5.hosts import Host
from reemote5.modules.files.files import (
    FindDirectories,
    FindFiles,
    FindLinks,
    _parse_size,
)
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


# --- Real-transport parity: the baseline's own find-family fixtures ------------------


async def test_find_files_parses_baseline_files_fixture():
    # The baseline's files.json fixture, verbatim: a bare find under "mydir".
    host = CannedShellHost("myfile\n")
    assert await FindFiles.process(host, "mydir") == ["myfile"]
    assert host.shells == ["find mydir -type f || true"]


async def test_find_files_parses_baseline_files_with_args_fixture():
    # The baseline's files_with_args.json fixture, verbatim: maxdepth plus the
    # raw args escape hatch appended after the typed filters.
    host = CannedShellHost("myfile\n")
    assert await FindFiles.process(host, "/", maxdepth=1, args=["-perm", "644"]) == [
        "myfile"
    ]
    assert host.shells == ["find / -type f -maxdepth 1 -perm 644 || true"]


async def test_find_files_parses_baseline_files_with_name_fixture():
    # The baseline's files_with_name.json fixture, verbatim: a safe name glob
    # stays unquoted.
    host = CannedShellHost("myfile\n")
    assert await FindFiles.process(host, "mydir", fname="myfile") == ["myfile"]
    assert host.shells == ["find mydir -type f -name myfile || true"]


async def test_find_files_parses_baseline_files_with_name_wildcard_fixture():
    # The baseline's files_with_name_wildcard.json fixture, verbatim: the shell
    # must pass the glob literally, so find (not the shell) expands it.
    host = CannedShellHost("myfile\n")
    assert await FindFiles.process(host, "mydir", fname="myfile*") == ["myfile"]
    assert host.shells == ["find mydir -type f -name 'myfile*' || true"]


async def test_find_directories_parses_baseline_directories_fixture():
    # The baseline's directories.json fixture, verbatim: -type d.
    host = CannedShellHost("anotherdir\n")
    assert await FindDirectories.process(host, "mydir") == ["anotherdir"]
    assert host.shells == ["find mydir -type d || true"]


async def test_find_links_parses_baseline_links_fixture():
    # The baseline's links.json fixture, verbatim: -type l.
    host = CannedShellHost("mylink\n")
    assert await FindLinks.process(host, "mydir") == ["mylink"]
    assert host.shells == ["find mydir -type l || true"]


# --- Filter command construction ------------------------------------------------


def test_find_size_bounds_render_in_bytes():
    # Decimal KB and binary MiB both reduce to the c (byte) suffix -- any
    # other unit has find's implementation-specific 512-byte rounding.
    assert FindFiles.command("/logs", min_size="1KB", max_size="2MiB") == (
        "find /logs -type f -size +1000c -size -2097152c || true"
    )


def test_find_exact_size_parses_human_readable_spellings():
    # The deliberate fix over upstream: the exact size goes through
    # _parse_size too, so "1GiB" works like its bounds do.
    assert FindFiles.command("/logs", size=100) == (
        "find /logs -type f -size 100c || true"
    )
    assert FindFiles.command("/logs", size="1GiB") == (
        "find /logs -type f -size 1073741824c || true"
    )


def test_find_filter_order_and_quoting():
    # The baseline's order: sizes, maxdepth, fname, iname, regex, path_match,
    # then the raw args. Globs and metacharacters are shell-quoted.
    assert FindFiles.command(
        "/etc",
        maxdepth=2,
        fname="conf.yaml",
        iname="CONF*.YAML",
        regex=".*/conf.yaml$",
        path_match="*/conf.d/*",
        args=["-perm", "644"],
    ) == (
        "find /etc -type f -maxdepth 2 -name conf.yaml -iname 'CONF*.YAML' "
        "-regex '.*/conf.yaml$' -path '*/conf.d/*' -perm 644 || true"
    )


def test_find_args_guards_skip_duplicate_predicates():
    # The baseline's guards, quirks included: the fname guard checks "-fname"
    # (a predicate find never spells), the others check the real flags.
    assert FindFiles.command("d", min_size=1, size=2, args=["-size", "5c"]) == (
        "find d -type f -size 5c || true"
    )
    assert FindFiles.command("d", maxdepth=1, args=["-maxdepth", "3"]) == (
        "find d -type f -maxdepth 3 || true"
    )
    assert FindFiles.command("d", fname="y", args=["-fname", "x"]) == (
        "find d -type f -fname x || true"
    )
    assert FindFiles.command("d", iname="y", args=["-iname", "x"]) == (
        "find d -type f -iname x || true"
    )
    assert FindFiles.command("d", regex="y", args=["-regex", "x"]) == (
        "find d -type f -regex x || true"
    )
    assert FindFiles.command("d", path_match="y", args=["-path", "x"]) == (
        "find d -type f -path x || true"
    )


def test_find_quote_path_false_leaves_globs_to_the_shell():
    # The escape hatch: start path and name patterns reach the shell
    # unquoted; path_match stays quoted even so (find itself globs it).
    assert FindFiles.command(
        "mydir*", fname="conf*.yaml", path_match="*/conf.d/*", quote_path=False
    ) == "find mydir* -type f -name conf*.yaml -path '*/conf.d/*' || true"


def test_find_quotes_a_metacharacter_start_path():
    assert FindFiles.command("/tmp/my dir") == "find '/tmp/my dir' -type f || true"


# --- _parse_size ------------------------------------------------------------------


def test_parse_size_accepts_ints_and_unit_spellings():
    assert _parse_size(1024) == 1024
    assert _parse_size("1B") == 1
    assert _parse_size("1GB") == 10**9
    assert _parse_size("1GiB") == 2**30
    assert _parse_size("2 kb") == 2000
    assert _parse_size("0.5KB") == 500
    assert _parse_size("1TiB") == 2**40


@pytest.mark.parametrize("size", ["1PB", "abc", "", "MB", "1.2.3KB"])
def test_parse_size_rejects_bad_spellings(size):
    with pytest.raises(ValueError):
        _parse_size(size)


# --- Parser edge cases --------------------------------------------------------------


def test_find_parse_is_verbatim_line_list():
    # find's traversal order is preserved (no sorting), empty reads [].
    assert FindFiles.parse([]) == []
    assert FindFiles.parse(["b", "a", "b"]) == ["b", "a", "b"]


async def test_find_is_empty_on_empty_output():
    # A missing start path fails find but the || true guard exits zero
    # with no stdout -> [] (the baseline's default = list behaviour).
    assert await FindFiles.process(CannedShellHost(""), "/no/such/dir") == []


# --- Find family SimulatedHost seam --------------------------------------------------

CONF_D = "/etc/datadog-agent/conf.d"
CONF_YAML_FIND = FindFiles.command(CONF_D, fname="conf.yaml", maxdepth=2)
TRACKED_HITS = [
    f"{CONF_D}/cpu/conf.yaml",
    f"{CONF_D}/memory/conf.yaml",
    f"{CONF_D}/legacy_check/conf.yaml",
]


async def test_find_reads_the_command_keyed_seam():
    # The rendered command is the key: the same start path under different
    # filters asks a different question and reads independently.
    host = SimulatedHost(name="h", find_results={CONF_YAML_FIND: TRACKED_HITS})
    assert await FindFiles.process(host, CONF_D, fname="conf.yaml", maxdepth=2) == (
        TRACKED_HITS
    )
    # A different filter combination is unseeded -> [].
    assert await FindFiles.process(host, CONF_D, fname="conf.yaml") == []
    assert await FindFiles.process(host, CONF_D) == []


async def test_find_seam_defaults_to_empty_and_bypasses_the_shell():
    # An empty store answers [] without any transport: the seam is the
    # whole read path for a SimulatedHost.
    host = SimulatedHost(name="h")
    assert await FindFiles.process(host, CONF_D, fname="conf.yaml") == []
    assert await FindDirectories.process(host, CONF_D) == []
    assert await FindLinks.process(host, CONF_D) == []


async def test_find_seam_returns_a_copy():
    host = SimulatedHost(name="h", find_results={CONF_YAML_FIND: TRACKED_HITS})
    hits = await FindFiles.process(host, CONF_D, fname="conf.yaml", maxdepth=2)
    hits.append("/injected")
    assert host.find_results[CONF_YAML_FIND] == TRACKED_HITS


async def test_find_gather_fact_forwards_path_and_filters():
    hosts = [
        SimulatedHost(name="web1", find_results={CONF_YAML_FIND: TRACKED_HITS}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(
        hosts, FindFiles, CONF_D, fname="conf.yaml", maxdepth=2
    ) == {"web1": TRACKED_HITS, "web2": []}