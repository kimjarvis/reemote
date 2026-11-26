# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the uv module: the ``Info``, ``Packages``, ``Pythons`` and
``Tools`` facts.

Covers ``reemote.modules.uv.uv`` -- ``Packages`` (the port of pyinfra's
``uv.UvPipPackages``), ``Info`` (the consolidated install record replacing
pyinfra's scattered ``UvVersion``/``UvToolDir``/``UvPythonDir`` scalars),
``Tools`` and ``Pythons`` -- over the real-transport path (a
``CannedShellHost`` returning the fact's own shell, reusing pyinfra's own fact
fixtures verbatim: the empty array, the one-package and three-package JSON
lines, the no/one/two/missing-version tool listings, the empty and
multi-version interpreter documents) and through the ``SimulatedHost``
``uv_install``, ``uv_packages``, ``uv_pythons`` and ``uv_tools`` seams, plus
the total-parse rule (unparsable output, non-list documents and rows without
the expected string values contribute nothing), the last-seen collapse of a
repeated name or header, the sort guarantees, the verbatim name spelling, the
effective-directory and managed-vs-system splits the design asks for, the
``gather_fact`` fan-out and the exports.

``asyncio_mode = "auto"`` lets the async tests run without decorators.
"""

from reemote.drivers import gather_fact
from reemote.hosts import Host
from reemote.modules import uv
from reemote.modules.uv.uv import (
    Info,
    InstallInfo,
    PackageInfo,
    Packages,
    PythonInfo,
    Pythons,
    ToolInfo,
    Tools,
)
from tests.simulated_host import SimulatedHost


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell``, recording the shell it was
    given so the command parity checks can assert on it."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout
        self.last_shell: str | None = None

    async def run_shell(self, shell: str) -> str:
        self.last_shell = shell
        return self._stdout


def _entry(name: str, version: str) -> PackageInfo:
    """One ``PackageInfo`` entry, spelled as the fact returns it."""
    return {"name": name, "version": version}


def _tool(name: str, version: str | None, entrypoints: list[str]) -> ToolInfo:
    """One ``ToolInfo`` entry, spelled as the fact returns it."""
    return {"name": name, "version": version, "entrypoints": entrypoints}


def _python(
    key: str,
    version: str,
    path: str,
    managed: bool = False,
    implementation: str = "cpython",
) -> PythonInfo:
    """One ``PythonInfo`` entry, spelled as the fact returns it."""
    return {
        "key": key,
        "implementation": implementation,
        "version": version,
        "path": path,
        "managed": managed,
    }


def _absent_record() -> InstallInfo:
    """The record of a chain that printed nothing -- every field ``None``."""
    return {field: None for field in Info.FIELDS}


# --- Fact: command parity ------------------------------------------------------


def test_uv_packages_command():
    """The shell is pyinfra's command verbatim; there is no parameter to
    vary it."""
    assert Packages.COMMAND == "uv pip list --format json"


# --- Fact: real-transport parsing (pyinfra's fixtures verbatim) ------------------

# pyinfra's ``tests/facts/uv.UvPipPackages/one_package.json`` output, in
# pyinfra's documented dict-of-sets shape ``{"pydash": ["3.48"]}`` -- here
# projected into the reemote entry shape.
ONE_PACKAGE_OUTPUT = '[{"name": "pydash", "version": "3.48"}]'

# pyinfra's ``three_packages.json`` output: one compact JSON line naming
# three packages out of alphabetical order.
THREE_PACKAGES_OUTPUT = (
    '[{"name": "pydash", "version": "3.48"}, '
    '{"name": "pluggy", "version": "1.60"}, '
    '{"name": "pip", "version": "25.3"}]'
)


async def test_uv_packages_parses_one_package():
    """The single-package fixture line becomes one entry, and the gathered
    shell is the fact's own command."""
    host = CannedShellHost(ONE_PACKAGE_OUTPUT)
    assert await Packages.process(host) == [_entry("pydash", "3.48")]
    assert host.last_shell == "uv pip list --format json"


async def test_uv_packages_parses_three_packages_sorted():
    """The three-package fixture line comes back as sorted ``PackageInfo``
    entries however uv printed them -- pyinfra's ``itemgetter`` sort, in
    list form."""
    assert await Packages.process(CannedShellHost(THREE_PACKAGES_OUTPUT)) == [
        _entry("pip", "25.3"),
        _entry("pluggy", "1.60"),
        _entry("pydash", "3.48"),
    ]


async def test_uv_packages_empty_array_reads_as_no_packages():
    """pyinfra's ``no_packages`` fixture: the bare ``[]`` document -- an
    environment with nothing installed -- reads as ``[]``, the counterpart
    of pyinfra's ``default = dict``."""
    assert await Packages.process(CannedShellHost("[]")) == []
    assert await Packages.process(CannedShellHost("")) == []


async def test_uv_packages_keeps_names_verbatim():
    """Unlike the pip fact there is no PEP 503 normalization: uv prints the
    canonical name of each distribution, so the dotted and capitalized
    spellings survive exactly as printed."""
    output = (
        '[{"name": "zope.interface", "version": "7.2"}, '
        '{"name": "PyYAML", "version": "6.0.2"}]'
    )
    assert await Packages.process(CannedShellHost(output)) == [
        _entry("PyYAML", "6.0.2"),
        _entry("zope.interface", "7.2"),
    ]


async def test_uv_packages_ignores_leading_noise():
    """The document is read from the first ``[`` onwards, so stray stdout
    noise ahead of the JSON array (which uv itself does not normally
    produce, but a wrapper shell might) is discarded."""
    output = 'Using Python 3.12 environment\n' + ONE_PACKAGE_OUTPUT
    assert Packages.parse(output.splitlines()) == [_entry("pydash", "3.48")]


# --- Fact: the total parse --------------------------------------------------------


async def test_uv_packages_unparsable_output_reads_as_empty():
    """Where pyinfra's ``process_json`` warns on a failed load, reemote
    reads ``[]``: broken JSON, a non-list document and a document with no
    array start all contribute nothing."""
    assert Packages.parse(['[{"name": "cut']) == []
    assert Packages.parse(['{"name": "pydash", "version": "3.48"}']) == []
    assert Packages.parse(["no json here"]) == []


async def test_uv_packages_skips_rows_without_string_fields():
    """A non-mapping row, a row missing ``name`` or ``version``, and a row
    whose fields are not strings (uv always prints strings, but the parse
    does not crash on anything else) all contribute nothing; valid rows
    alongside them still land."""
    output = (
        '["a string", {"name": "noversion"}, {"version": "noname"}, '
        '{"name": "numver", "version": 3}, '
        '{"name": "pluggy", "version": "1.60"}]'
    )
    assert Packages.parse([output]) == [_entry("pluggy", "1.60")]


async def test_uv_packages_collapses_a_repeated_name():
    """A repeated name collapses into the entry seen last, keeping the
    result unique per name -- the set semantics of pyinfra's dict values,
    narrowed to one version per name exactly as the fact returns it."""
    output = (
        '[{"name": "pydash", "version": "3.47"}, '
        '{"name": "pydash", "version": "3.48"}]'
    )
    assert Packages.parse([output]) == [_entry("pydash", "3.48")]


# --- Fact: SimulatedHost seam -----------------------------------------------------


async def test_uv_packages_reads_the_simulated_seam():
    """A SimulatedHost answers from its ``uv_packages`` store, bypassing the
    shell; the default empty store reads as ``[]``, like the baseline's
    ``default = dict`` for a bare environment."""
    host = SimulatedHost(name="h", uv_packages={"pydash": "3.48"})
    assert await Packages.process(host) == [_entry("pydash", "3.48")]
    assert await Packages.process(SimulatedHost(name="h")) == []


async def test_uv_packages_seam_sorts_and_returns_copies():
    """Seam entries come out sorted by name, and mutating a returned entry
    never touches the simulated state."""
    host = SimulatedHost(name="h", uv_packages={"b": "2", "a": "1"})
    entries = await Packages.process(host)
    assert entries == [_entry("a", "1"), _entry("b", "2")]
    entries[0]["version"] = "tampered"
    assert await Packages.process(host) == [_entry("a", "1"), _entry("b", "2")]


async def test_gather_fact_uv_packages_fans_out():
    """Across a simulated fleet each host answers from its own seam store,
    keyed by host name."""
    host1 = SimulatedHost(name="web1", uv_packages={"pydash": "3.48"})
    host2 = SimulatedHost(name="web2")
    answers = await gather_fact([host1, host2], Packages)
    assert answers == {
        "web1": [_entry("pydash", "3.48")],
        "web2": [],
    }


# --- Fact: Info ---------------------------------------------------------------

# The six stdout lines the chained shell prints on a host whose uv lives under
# ``/root/.local`` -- the design's documented example, in ``FIELDS`` order.
INFO_LINES = [
    "uv 0.9.22",
    "/root/.local/share/uv/tools",
    "/root/.local/bin",
    "/root/.local/share/uv/python",
    "/root/.cache/uv",
    "/root/.local/bin/uv",
]

INFO_RECORD: InstallInfo = {
    "version": "0.9.22",
    "executable": "/root/.local/bin/uv",
    "tool_dir": "/root/.local/share/uv/tools",
    "tool_bin_dir": "/root/.local/bin",
    "python_dir": "/root/.local/share/uv/python",
    "cache_dir": "/root/.cache/uv",
}


def test_uv_info_command_is_one_chained_shell():
    """Six questions in a single ``&&`` chain, anchored on ``uv --version`` so a
    missing uv is the command that fails; the chain order is ``FIELDS`` order."""
    assert Info.COMMAND == (
        "uv --version && uv tool dir && uv tool dir --bin"
        " && uv python dir && uv cache dir && command -v uv"
    )


async def test_uv_info_gathers_the_record_in_one_round_trip():
    """Each line fills the next field, the version keeps only its number, and
    the recorded shell proves the whole record costs one command."""
    host = CannedShellHost("\n".join(INFO_LINES) + "\n")
    assert await Info.process(host) == INFO_RECORD
    assert host.last_shell == Info.COMMAND


async def test_uv_info_strips_uvs_version_prefix():
    """Where pyinfra's ``UvVersion`` kept the whole line, the record holds the
    clean version -- and a packaging suffix uv prints after it survives."""
    assert Info.parse(["uv 0.9.22"])["version"] == "0.9.22"
    assert Info.parse(["uv 0.8.5 (Homebrew 2025-08-05)"])["version"] == (
        "0.8.5 (Homebrew 2025-08-05)"
    )


async def test_uv_info_short_chain_leaves_later_fields_none():
    """An older uv without one of the dir subcommands short-circuits the chain;
    the fields past that point read ``None`` rather than raising, where
    pyinfra's separate facts answered their ``default = ""``."""
    assert Info.parse(INFO_LINES[:3]) == {
        **INFO_RECORD,
        "python_dir": None,
        "cache_dir": None,
        "executable": None,
    }


async def test_uv_info_ignores_blank_lines():
    """A wrapper shell's spare newlines do not shift the positional mapping."""
    assert Info.parse(["", INFO_LINES[0], "  ", *INFO_LINES[1:]]) == INFO_RECORD


async def test_uv_info_no_output_is_the_all_none_record():
    """A chain that printed nothing at all -- an unchecked non-127 failure --
    leaves every field ``None``; the record stays total either way."""
    assert Info.parse([]) == _absent_record()


async def test_uv_info_reads_the_simulated_seam():
    """A SimulatedHost answers from its ``uv_install`` store without touching
    the shell, and an unseeded field reads ``None`` like an unparsed line."""
    host = SimulatedHost(
        name="h",
        uv_install={"version": "0.9.22", "cache_dir": "/root/.cache/uv"},
    )
    assert await Info.process(host) == {
        **_absent_record(),
        "version": "0.9.22",
        "cache_dir": "/root/.cache/uv",
    }
    assert await Info.process(SimulatedHost(name="h")) == _absent_record()


async def test_uv_info_seam_returns_a_copy():
    """The returned record is the fact's own dict, never the simulated store."""
    host = SimulatedHost(name="h", uv_install={"version": "0.9.22"})
    record = await Info.process(host)
    record["version"] = "tampered"
    assert await Info.process(host) == {**_absent_record(), "version": "0.9.22"}


# --- Fact: Tools -------------------------------------------------------------

# pyinfra's ``tests/facts/uv.UvTools`` fixtures: ``one_tool`` is a header line
# plus its entrypoint bullet, ``two_tools`` adds colorpedia's pair of binaries,
# and ``missing_version`` is the bare name pyinfra warns away.
ONE_TOOL_OUTPUT = ["pyinfra v3.5.1", "- pyinfra"]
TWO_TOOLS_OUTPUT = [
    "colorpedia v1.2.2",
    "- color",
    "- colorpedia",
    "pyinfra v3.5.1",
    "- pyinfra",
]


def test_uv_tools_command():
    """The shell is pyinfra's listing without its ``2>/dev/null`` guard, and it
    takes no argument."""
    assert Tools.COMMAND == "uv tool list"


async def test_uv_tools_parses_one_tool():
    """pyinfra's ``one_tool`` fixture: the header names the tool and its
    version, the bullet its one entrypoint."""
    host = CannedShellHost("\n".join(ONE_TOOL_OUTPUT) + "\n")
    assert await Tools.process(host) == [_tool("pyinfra", "3.5.1", ["pyinfra"])]
    assert host.last_shell == "uv tool list"


async def test_uv_tools_parses_two_tools_with_their_entrypoints():
    """pyinfra's ``two_tools`` fixture, which answers only the versions: each
    tool comes back sorted by name with every binary it linked onto the
    PATH."""
    assert Tools.parse(TWO_TOOLS_OUTPUT) == [
        _tool("colorpedia", "1.2.2", ["color", "colorpedia"]),
        _tool("pyinfra", "3.5.1", ["pyinfra"]),
    ]


async def test_uv_tools_keeps_a_versionless_header():
    """pyinfra's ``missing_version`` fixture answers ``{}`` -- its parser warns a
    one-token row away. A tool installed from a local path or git URL keeps its
    record here, with ``version=None``."""
    assert Tools.parse(["pyinfra"]) == [_tool("pyinfra", None, [])]


async def test_uv_tools_skips_the_empty_state_hint():
    """Real uv answers an empty host with ``No tools installed`` and exit 0,
    which pyinfra files as a tool named ``No`` at version ``tools``; a row that
    is neither a header nor a bullet is noise, so the fact reads ``[]``."""
    assert Tools.parse(["No tools installed"]) == []
    assert Tools.parse([]) == []


async def test_uv_tools_entrypoints_need_a_header():
    """A bullet before any tool header belongs to nothing and is dropped."""
    assert Tools.parse(["- orphan", "ruff v0.8.4", "- ruff"]) == [
        _tool("ruff", "0.8.4", ["ruff"])
    ]


async def test_uv_tools_dedupes_and_sorts_entrypoints():
    """Bullets keep uv's ``v``-stripped tool's order-independent spelling: the
    entrypoint list is sorted and deduplicated."""
    assert Tools.parse(["black v24.10.0", "- blackd", "- black", "- black"]) == [
        _tool("black", "24.10.0", ["black", "blackd"])
    ]


async def test_uv_tools_repeated_name_keeps_the_last_header():
    """A tool listed twice restarts its record, so the last version and the
    bullets that follow it win -- the counterpart of ``Packages`` collapsing a
    repeated name."""
    assert Tools.parse(
        ["ruff v0.8.3", "- ruff", "ruff v0.8.4", "- ruff", "- ruff-lsp"]
    ) == [_tool("ruff", "0.8.4", ["ruff", "ruff-lsp"])]


async def test_uv_tools_reads_the_simulated_seam():
    """A SimulatedHost answers from its ``uv_tools`` store, keyed by the printed
    name; the empty default reads ``[]``."""
    host = SimulatedHost(
        name="h",
        uv_tools={"ruff": {"version": "0.8.4", "entrypoints": ["ruff"]}},
    )
    assert await Tools.process(host) == [_tool("ruff", "0.8.4", ["ruff"])]
    assert await Tools.process(SimulatedHost(name="h")) == []


async def test_uv_tools_seam_fills_a_partial_record():
    """A seeded record without a version or bullets stays well-typed: ``None``
    and the empty list, exactly what the parser reports for them."""
    host = SimulatedHost(name="h", uv_tools={"localtool": {}})
    assert await Tools.process(host) == [_tool("localtool", None, [])]


async def test_uv_tools_seam_sorts_and_returns_copies():
    """Seam entries come out sorted by name with sorted entrypoints, and
    mutating a returned list never touches the simulated state."""
    host = SimulatedHost(
        name="h",
        uv_tools={
            "b": {"version": "2", "entrypoints": ["z", "a"]},
            "a": {"version": "1", "entrypoints": []},
        },
    )
    assert await Tools.process(host) == [
        _tool("a", "1", []),
        _tool("b", "2", ["a", "z"]),
    ]
    entries = await Tools.process(host)
    entries[1]["entrypoints"].append("tampered")
    assert await Tools.process(host) == [
        _tool("a", "1", []),
        _tool("b", "2", ["a", "z"]),
    ]


async def test_gather_fact_uv_tools_fans_out():
    """Across a simulated fleet each host answers from its own seam store."""
    host1 = SimulatedHost(
        name="web1",
        uv_tools={"ruff": {"version": "0.8.4", "entrypoints": ["ruff"]}},
    )
    host2 = SimulatedHost(name="web2")
    assert await gather_fact([host1, host2], Tools) == {
        "web1": [_tool("ruff", "0.8.4", ["ruff"])],
        "web2": [],
    }


# --- Fact: Pythons -----------------------------------------------------------

# pyinfra's ``tests/facts/uv.UvInstalledPythons*`` shape in one document: three
# installed cpython rows -- a uv-managed download plus the distro interpreter
# found at two paths -- printed as uv really prints it, pretty across lines and
# carrying keys (``os``, ``arch``, ``url``) the parse ignores.
PYTHONS_OUTPUT = [
    "/root/.local/share/uv/python",
    "[",
    "  {",
    '    "key": "cpython-3.14.0-linux-x86_64-gnu",',
    '    "implementation": "cpython",',
    '    "version": "3.14.0",',
    '    "path": "/root/.local/share/uv/python/cpython-3.14.0-linux-x86_64-gnu/bin/python3.14",',
    '    "os": "linux",',
    '    "arch": "x86_64"',
    "  },",
    "  {",
    '    "key": "cpython-3.13.5-linux-x86_64-gnu",',
    '    "implementation": "cpython",',
    '    "version": "3.13.5",',
    '    "path": "/usr/bin/python3.13"',
    "  },",
    "  {",
    '    "key": "cpython-3.13.5-linux-x86_64-gnu",',
    '    "implementation": "cpython",',
    '    "version": "3.13.5",',
    '    "path": "/usr/bin/python3"',
    "  }",
    "]",
]


def _rows(*paths: str) -> str:
    """A compact interpreter document with one cpython row per path -- the rows
    share uv's key, exactly as uv prints one interpreter twice."""
    entries = ", ".join(
        '{"key": "cpython-3.13.1-linux-x86_64-gnu", "implementation": '
        f'"cpython", "version": "3.13.1", "path": "{path}"}}'
        for path in paths
    )
    return f"[{entries}]"


def test_uv_pythons_command_lists_only_installed():
    """The chain asks for the directory and the installed interpreters: the
    ``--only-installed`` filter keeps the download catalogue out, and
    ``--managed-python`` is deliberately absent so system interpreters stay
    visible for the ``managed`` split."""
    assert Pythons.COMMAND == (
        "uv python dir && uv python list --only-installed --output-format=json"
    )


async def test_uv_pythons_splits_the_directory_from_the_document():
    """The first line is the managed Python directory, the rest the JSON array;
    entries come back sorted on ``(key, path)`` -- lexicographically, so uv's
    3.13 keys order before its 3.14 one."""
    host = CannedShellHost("\n".join(PYTHONS_OUTPUT) + "\n")
    assert await Pythons.process(host) == [
        _python("cpython-3.13.5-linux-x86_64-gnu", "3.13.5", "/usr/bin/python3"),
        _python("cpython-3.13.5-linux-x86_64-gnu", "3.13.5", "/usr/bin/python3.13"),
        _python(
            "cpython-3.14.0-linux-x86_64-gnu",
            "3.14.0",
            "/root/.local/share/uv/python/cpython-3.14.0-linux-x86_64-gnu/bin/python3.14",
            managed=True,
        ),
    ]
    assert host.last_shell == Pythons.COMMAND


async def test_uv_pythons_flags_managed_by_directory():
    """uv's JSON carries no managed flag, so an interpreter under the directory
    the chain printed is uv-managed and one outside it is not."""
    output = [
        "/root/.local/share/uv/python",
        _rows(
            "/root/.local/share/uv/python/cpython-3.13.1/bin/python3.13",
            "/usr/bin/python3.13",
        ),
    ]
    assert Pythons.parse(output) == [
        _python(
            "cpython-3.13.1-linux-x86_64-gnu",
            "3.13.1",
            "/root/.local/share/uv/python/cpython-3.13.1/bin/python3.13",
            managed=True,
        ),
        _python("cpython-3.13.1-linux-x86_64-gnu", "3.13.1", "/usr/bin/python3.13"),
    ]


async def test_uv_pythons_keeps_one_key_at_several_paths():
    """pyinfra keys its answer by uv's ``key``, which one interpreter found at
    two paths silently overwrites; both paths survive here."""
    entries = Pythons.parse(["/uv", _rows("/usr/bin/python3.13", "/usr/bin/python3")])
    assert [entry["path"] for entry in entries] == ["/usr/bin/python3", "/usr/bin/python3.13"]
    assert {entry["key"] for entry in entries} == {"cpython-3.13.1-linux-x86_64-gnu"}


async def test_uv_pythons_empty_state_reads_as_no_interpreters():
    """pyinfra's ``empty`` fixture: the bare array -- a brand-new host, not the
    dozens of downloadable versions -- and a chain that printed no array at all
    both read as ``[]``."""
    assert Pythons.parse(["/root/.local/share/uv/python", "[]"]) == []
    assert Pythons.parse(["/root/.local/share/uv/python"]) == []
    assert Pythons.parse([]) == []


async def test_uv_pythons_parse_is_total():
    """Unparsable JSON, a document that is not an array, non-mapping rows and
    rows missing any of the four strings contribute nothing -- where pyinfra's
    ``process_json`` warns or raises -- while a good row alongside lands."""
    assert Pythons.parse(["/uv", "no array here"]) == []
    assert Pythons.parse(['/uv', '{"key": "not a list"}']) == []
    assert Pythons.parse(['/uv', '["a string", {"key": "k"}]']) == []
    assert Pythons.parse(
        ["/uv", '7, ' + _rows("/usr/bin/python3.13")]
    ) == [_python("cpython-3.13.1-linux-x86_64-gnu", "3.13.1", "/usr/bin/python3.13")]


async def test_uv_pythons_reads_the_simulated_seam():
    """A SimulatedHost answers from its ``uv_pythons`` store, keyed by
    interpreter path; the empty default reads ``[]``."""
    host = SimulatedHost(
        name="h",
        uv_pythons={
            "/usr/bin/python3.13": {
                "key": "cpython-3.13.5-linux-x86_64-gnu",
                "implementation": "cpython",
                "version": "3.13.5",
                "managed": False,
            }
        },
    )
    assert await Pythons.process(host) == [
        _python("cpython-3.13.5-linux-x86_64-gnu", "3.13.5", "/usr/bin/python3.13")
    ]
    assert await Pythons.process(SimulatedHost(name="h")) == []


async def test_uv_pythons_seam_fills_a_partial_record():
    """A record seeded with only its key keeps the path it is stored under and
    answers the empty defaults for everything else."""
    host = SimulatedHost(name="h", uv_pythons={"/usr/bin/python3": {"key": "cpython-3"}})
    assert await Pythons.process(host) == [
        _python("cpython-3", "", "/usr/bin/python3", implementation="")
    ]


async def test_gather_fact_uv_pythons_fans_out():
    """Across a simulated fleet each host answers from its own seam store."""
    host1 = SimulatedHost(
        name="web1",
        uv_pythons={"/usr/bin/python3": {"key": "cpython-3", "managed": False}},
    )
    host2 = SimulatedHost(name="web2")
    answers = await gather_fact([host1, host2], Pythons)
    assert answers == {
        "web1": [_python("cpython-3", "", "/usr/bin/python3", implementation="")],
        "web2": [],
    }


async def test_gather_fact_uv_info_fans_out():
    """The record fact fans out too: a seeded host answers its own store, an
    unseeded one the all-None record."""
    host1 = SimulatedHost(name="web1", uv_install={"version": "0.9.22"})
    host2 = SimulatedHost(name="web2")
    answers = await gather_fact([host1, host2], Info)
    assert answers == {
        "web1": {**_absent_record(), "version": "0.9.22"},
        "web2": _absent_record(),
    }


# --- Exports -----------------------------------------------------------------------


def test_uv_module_exports():
    """The package re-exports the four facts and their entry types, so
    ``uv.Tools`` and ``uv.ToolInfo`` resolve; the top-level package makes
    ``from reemote import Info, Packages, Pythons, Tools`` work and keeps the
    ``uv`` namespace importable."""
    assert uv.Packages is Packages
    assert uv.PackageInfo is PackageInfo
    assert uv.uv.Packages is Packages
    for name, value in (
        ("Info", Info),
        ("InstallInfo", InstallInfo),
        ("PythonInfo", PythonInfo),
        ("Pythons", Pythons),
        ("ToolInfo", ToolInfo),
        ("Tools", Tools),
    ):
        assert getattr(uv, name) is value
        assert getattr(uv.uv, name) is value
    import reemote

    assert reemote.uv is uv
    for name in (
        "Info",
        "InstallInfo",
        "PackageInfo",
        "Packages",
        "PythonInfo",
        "Pythons",
        "ToolInfo",
        "Tools",
        "uv",
    ):
        assert name in reemote.__all__
        assert getattr(reemote, name) is not None
