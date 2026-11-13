# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the files module's ``FindInFiles`` fact.

Covers ``reemote.modules.files.files.FindInFiles`` -- the reemote-original
recursive content search (``grep -rlE <pattern> <path> || true``) that
returns the *paths* of the files whose content matches. Exercised over the
real-transport path (matching paths, ``$VAR`` interpolation on/off, a
double-quote-bearing pattern, the empty ``[]`` answer for no match), the
``grep -rlE`` command construction, the parser's verbatim/empty behavior,
and the ``(path, pattern)``-keyed ``grep_files`` ``SimulatedHost`` seam
([] for an absent key rather than None, copy-on-read, ``gather_fact``
argument forwarding).
"""

from reemote.drivers import gather_fact
from reemote.hosts import Host
from reemote.modules.files.files import FindInFiles
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


# A start path and the two files under it whose content matches the pattern.
PATH = "/etc/php/conf.d"
PATTERN = "opcache.enable"
MATCHES = ["/etc/php/conf.d/10-opcache.ini", "/etc/php/conf.d/99-local.ini"]
COMMAND = f"grep -rlE {PATTERN} {PATH} || true"


# --- Real-transport path --------------------------------------------------------------


async def test_find_in_files_parses_matching_paths():
    # grep -l prints one path per matching file; parse keeps them verbatim.
    host = CannedShellHost("\n".join(MATCHES) + "\n")
    assert await FindInFiles.process(host, PATH, PATTERN) == MATCHES
    assert host.shells == [COMMAND]


async def test_find_in_files_empty_output_reads_empty_list():
    # The || true guard masks no-match (grep exit 1) to empty stdout -> [].
    host = CannedShellHost("")
    assert await FindInFiles.process(host, PATH, PATTERN) == []
    assert host.shells == [COMMAND]


async def test_find_in_files_interpolate_double_quotes_the_pattern():
    # interpolate_variables=True lets the remote shell expand $VAR references.
    host = CannedShellHost("\n".join(MATCHES) + "\n")
    assert await FindInFiles.process(
        host, PATH, "$USER", interpolate_variables=True
    ) == MATCHES
    assert host.shells == [f'grep -rlE "$USER" {PATH} || true']


async def test_find_in_files_without_interpolate_quotes_literally():
    # The default single-quotes the pattern, so $USER stays literal.
    host = CannedShellHost("\n".join(MATCHES) + "\n")
    assert await FindInFiles.process(host, PATH, "$USER") == MATCHES
    assert host.shells == [f"grep -rlE '$USER' {PATH} || true"]


# --- Command construction and parser edge cases ---------------------------------------


def test_find_in_files_command_quotes_path_and_pattern():
    assert FindInFiles.command("/a dir", "a|b") == "grep -rlE 'a|b' '/a dir' || true"


def test_find_in_files_command_interpolation_escapes_embedded_double_quotes():
    assert FindInFiles.command("/d", 'say "hi"', interpolate_variables=True) == (
        'grep -rlE "say \\"hi\\"" /d || true'
    )


def test_find_in_files_parse_returns_lines_verbatim():
    assert FindInFiles.parse(["/a", "/b"]) == ["/a", "/b"]


def test_find_in_files_parse_empty_reads_empty_list():
    assert FindInFiles.parse([]) == []


# --- FindInFiles SimulatedHost seam ---------------------------------------------------


async def test_find_in_files_reads_the_path_pattern_keyed_seam():
    host = SimulatedHost(name="h", grep_files={(PATH, PATTERN): MATCHES})
    assert await FindInFiles.process(host, PATH, PATTERN) == MATCHES
    # The pattern is part of the question: another pattern is unseeded -> [].
    assert await FindInFiles.process(host, PATH, "other") == []


async def test_find_in_files_seam_absent_key_reads_empty_not_none():
    # Unlike FindInFile, a recursive search always answers with a list.
    host = SimulatedHost(name="h")
    assert await FindInFiles.process(host, "/no/such/dir", PATTERN) == []


async def test_find_in_files_seam_returns_a_copy():
    host = SimulatedHost(name="h", grep_files={(PATH, PATTERN): MATCHES})
    paths = await FindInFiles.process(host, PATH, PATTERN)
    paths.append("/injected")
    assert host.grep_files[(PATH, PATTERN)] == MATCHES


async def test_find_in_files_gather_fact_forwards_path_and_pattern():
    hosts = [
        SimulatedHost(name="web1", grep_files={(PATH, PATTERN): MATCHES}),
        SimulatedHost(name="web2"),
    ]
    assert await gather_fact(hosts, FindInFiles, PATH, PATTERN) == {
        "web1": MATCHES,
        "web2": [],
    }
