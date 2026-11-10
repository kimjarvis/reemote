# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the files module's ``FindInFile`` fact.

Covers ``reemote5.modules.files.files.FindInFile``: pyinfra's own six
``files.FindInFile`` fixtures over the real-transport path (matching
lines, ``$USER`` interpolation on/off, a double-quote-bearing pattern,
the missing-file None answer and the exists-sentinel [] answer), the
``grep -E`` extended-regex switch, sentinel parse edge cases, and the
``(path, pattern)``-keyed ``grep_lines`` ``SimulatedHost`` seam ([] vs
None vs absent key, copy-on-read, ``gather_fact`` argument forwarding).
"""

from reemote5.drivers import gather_fact
from reemote5.hosts import Host
from reemote5.modules.files.files import FindInFile
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


# pyinfra's fixture path and the fallback arm's sentinel for it.
PATH = "/home/pyinfra/mylink"
SENTINEL = f"__pyinfra_exists_{PATH}"
LINES_COMMAND = (
    f"grep -e test {PATH} 2> /dev/null || "
    f"( find {PATH} -type f > /dev/null && echo {SENTINEL} || true )"
)
MATCHES = ["thing test thing", "another test"]


# --- Real-transport parity: pyinfra's own files.FindInFile fixtures ---------------


async def test_find_in_file_parses_pyintras_lines_fixture():
    # pyinfra's lines.json fixture, verbatim: grep prints the matches.
    host = CannedShellHost("\n".join(MATCHES) + "\n")
    assert await FindInFile.process(host, PATH, "test") == MATCHES
    assert host.shells == [LINES_COMMAND]


async def test_find_in_file_parses_pyintras_lines_interpolate_fixture():
    # pyinfra's lines_interpolate.json fixture, verbatim: the pattern is
    # double-quoted so the remote shell expands $USER.
    host = CannedShellHost("\n".join(MATCHES) + "\n")
    assert await FindInFile.process(host, PATH, "$USER", interpolate_variables=True) == (
        MATCHES
    )
    assert host.shells == [LINES_COMMAND.replace("-e test", '-e "$USER"')]


async def test_find_in_file_parses_pyintras_lines_no_interpolate_fixture():
    # pyinfra's lines_no_interpolate.json fixture, verbatim: without the
    # switch the $USER pattern is single-quoted literally.
    host = CannedShellHost("\n".join(MATCHES) + "\n")
    assert await FindInFile.process(host, PATH, "$USER") == MATCHES
    assert host.shells == [LINES_COMMAND.replace("-e test", "-e '$USER'")]


async def test_find_in_file_parses_pyintras_lines_quote_fixture():
    # pyinfra's lines_quote.json fixture, verbatim: embedded double quotes
    # survive inside the single-quoted pattern.
    host = CannedShellHost("\n".join(MATCHES) + "\n")
    pattern = 'test with some " other shit "'
    assert await FindInFile.process(host, PATH, pattern) == MATCHES
    assert host.shells == [
        LINES_COMMAND.replace("-e test", f"-e '{pattern}'")
    ]


async def test_find_in_file_parses_pyintras_no_file_fixture():
    # pyinfra's no_file.json fixture, verbatim: the fallback arm echoes
    # nothing for a missing file -> None (not []).
    host = CannedShellHost("")
    assert await FindInFile.process(host, PATH, "test") is None
    assert host.shells == [LINES_COMMAND]


async def test_find_in_file_parses_pyintras_no_matches_fixture():
    # pyinfra's no_matches.json fixture, verbatim: the exists sentinel
    # distinguishes "file exists, nothing matched" -> [].
    host = CannedShellHost(SENTINEL + "\n")
    assert await FindInFile.process(host, PATH, "test") == []
    assert host.shells == [LINES_COMMAND]


# --- Command construction and parser edge cases -------------------------------------


def test_find_in_file_extended_regex_switches_to_grep_e():
    assert FindInFile.command("/f", "a|b", extended_regex=True) == (
        "grep -E -e 'a|b' /f 2> /dev/null || "
        "( find /f -type f > /dev/null && echo __pyinfra_exists_/f || true )"
    )


def test_find_in_file_interpolation_escapes_embedded_double_quotes():
    assert FindInFile.command("/f", 'say "hi"', interpolate_variables=True) == (
        'grep -e "say \\"hi\\"" /f 2> /dev/null || '
        "( find /f -type f > /dev/null && echo __pyinfra_exists_/f || true )"
    )


def test_find_in_file_parse_recognises_the_sentinel_only_as_first_line():
    # The sentinel is the fallback arm's whole output: as the first line
    # it reads [] (pyinfra's rule), while a grep line that merely starts
    # with the prefix -- or names another path -- is just a matching line.
    assert FindInFile.parse([SENTINEL], PATH) == []
    assert FindInFile.parse(["x", SENTINEL], PATH) == ["x", SENTINEL]
    assert FindInFile.parse([f"__pyinfra_exists_{PATH}/other"], PATH) == [
        f"__pyinfra_exists_{PATH}/other"
    ]
    # A sentinel for a different path is not this file's exists marker.
    assert FindInFile.parse(["__pyinfra_exists_/elsewhere"], PATH) == [
        "__pyinfra_exists_/elsewhere"
    ]


def test_find_in_file_parse_empty_reads_none():
    assert FindInFile.parse([], PATH) is None


# --- FindInFile SimulatedHost seam ----------------------------------------------------


async def test_find_in_file_reads_the_path_pattern_keyed_seam():
    host = SimulatedHost(name="h", grep_lines={(PATH, "test"): MATCHES})
    assert await FindInFile.process(host, PATH, "test") == MATCHES
    # The pattern is part of the question: another pattern is unseeded.
    assert await FindInFile.process(host, PATH, "other") is None


async def test_find_in_file_seam_distinguishes_empty_from_absent():
    # [] = the file exists but nothing matches; an absent key = missing.
    host = SimulatedHost(name="h", grep_lines={(PATH, "test"): []})
    assert await FindInFile.process(host, PATH, "test") == []
    assert await FindInFile.process(host, "/no/such/file", "test") is None


async def test_find_in_file_seam_returns_a_copy():
    host = SimulatedHost(name="h", grep_lines={(PATH, "test"): MATCHES})
    lines = await FindInFile.process(host, PATH, "test")
    lines.append("injected")
    assert host.grep_lines[(PATH, "test")] == MATCHES


async def test_find_in_file_gather_fact_forwards_path_and_pattern():
    hosts = [
        SimulatedHost(name="web1", grep_lines={(PATH, "test"): MATCHES}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, FindInFile, PATH, "test") == {
        "web1": MATCHES,
        "web2": None,
    }