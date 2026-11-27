# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the uv module: the ``Info``, ``Packages``, ``Pythons`` and
``Tools`` facts and the six ``uv`` operations.

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
``gather_fact`` fan-out, the exports, and the ``venv``-scoped listing (uv's
``--python``, shell-quoted) that keeps the fact reading the very environment the
pip commands mutate.

The operation side -- the six pyinfra ``uv`` ports ``packages`` (uv pip),
``pythons``, ``tools``, ``tools_upgrade_all``, ``tools_update_shell`` and
``venv`` -- is exercised over their thirteen command descriptors (rendering
byte-exact to pyinfra's own command builders, elevation and shell quoting),
the version/case-aware idempotence (exact ``==`` pins, lowercased name
matching, uv's dotted-component interpreter version match, the versionless
``Tools`` convergence fix), the stateless ``requirements`` and
``tools_upgrade_all`` directions, the ``server.Env``-based ``PATH`` check of
``tools_update_shell``, the private ``_VenvExists`` probe and its guarded
command, the option validation (``allow_existing``/``clear_existing`` mutual
exclusion, and ``packages`` rejecting an empty ``venv`` that uv would silently
treat as unset), the environment threading (``venv`` onto the ``Packages`` read
and every pip command, ``python``/``with_packages`` onto the tool install and
upgrade but never the uninstall, and the deliberate rule that a changed tool
environment alone does not reopen idempotence), the ``SimulatedHost`` seam
dispatch of every descriptor (including the requirements file read from
``remote_files``, the update-shell PATH write and the flat store a venv-scoped
install shares with an unscoped one) and end-to-end through ``run_prepare`` +
``run_apply`` (sudo elevation, per-step fresh-cache no-ops, the
install-then-remove cycle, the create-then-noop venv, the scoped-venv and
isolated-tool logs and dry runs).

``asyncio_mode = "auto"`` lets the async tests run without decorators.
"""

import shlex

from reemote.core import FactCache, OpContext, op
from reemote.drivers import (
    command_shell,
    format_log,
    format_plan,
    gather_fact,
    run,
    run_apply,
    run_prepare,
    send_command,
)
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
    UvPipInstallCommand,
    UvPipRemoveCommand,
    UvPipRequirementsCommand,
    UvPipUpgradeCommand,
    UvPythonInstallCommand,
    UvPythonRemoveCommand,
    UvToolInstallCommand,
    UvToolRemoveCommand,
    UvToolUpdateShellCommand,
    UvToolUpgradeAllCommand,
    UvToolUpgradeCommand,
    UvVenvCreateCommand,
    UvVenvRemoveCommand,
    _VenvExists,
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
    """The bare shell is pyinfra's command verbatim, and the default
    ``command()`` reproduces it exactly -- no selector, uv's own resolution."""
    assert Packages.COMMAND == "uv pip list --format json"
    assert Packages.command() == "uv pip list --format json"
    assert Packages.command(None) == Packages.COMMAND


def test_uv_packages_command_scopes_to_a_named_venv():
    """``venv`` reaches the listing through uv's ``--python``, which accepts a
    virtual environment's root directory as readily as an interpreter path."""
    assert Packages.command("/srv/app/.venv") == (
        "uv pip list --format json --python /srv/app/.venv"
    )


def test_uv_packages_command_quotes_the_venv():
    """A metacharacter in the environment path cannot break out of the
    argument."""
    hostile = "/tmp/v x;rm -rf /"
    assert Packages.command(hostile) == (
        f"uv pip list --format json --python {shlex.quote(hostile)}"
    )


async def test_uv_packages_process_runs_the_scoped_command():
    """The real transport issues the scoped listing, so the fact reports the
    environment the operation's commands mutate."""
    host = CannedShellHost(ONE_PACKAGE_OUTPUT)
    assert await Packages.process(host, "/srv/app/.venv") == [
        _entry("pydash", "3.48")
    ]
    assert host.last_shell == "uv pip list --format json --python /srv/app/.venv"


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


async def test_uv_info_reduces_the_version_to_its_number():
    """Where pyinfra's ``UvVersion`` kept the whole line, the record holds the
    clean version string the design asks for: uv's ``uv `` prefix and the build
    parenthetical release and packager binaries append are both dropped."""
    assert Info.parse(["uv 0.9.22"])["version"] == "0.9.22"
    assert Info.parse(["uv 0.8.5 (Homebrew 2025-08-05)"])["version"] == "0.8.5"
    # The current stable snap's real output, captured live on the demo host.
    assert Info.parse(
        ["uv 0.12.21 (7af826859 2026-09-29 x86_64-unknown-linux-gnu)"]
    )["version"] == "0.12.21"


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

# pyinfra's ``tests/facts/uv.UvInstalledPythons*`` shape in one document: the
# ``uv python dir`` answer, the ``pwd`` the chained shell prints next, then three
# installed cpython rows -- a uv-managed download plus the distro interpreter
# found at two paths -- printed as uv really prints it, pretty across lines and
# carrying keys (``os``, ``arch``, ``url``) the parse ignores. The rows use
# absolute paths here; the cwd-relative spelling uv really prints under the
# working directory has its own test below.
PYTHONS_OUTPUT = [
    "/root/.local/share/uv/python",
    "/root",
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
    """The chain asks for the directory, the working directory it anchors the
    JSON paths on, and the installed interpreters: the ``--only-installed``
    filter keeps the download catalogue out, and ``--managed-python`` is
    deliberately absent so system interpreters stay visible for the ``managed``
    split."""
    assert Pythons.COMMAND == (
        "uv python dir && pwd"
        " && uv python list --only-installed --output-format=json"
    )


async def test_uv_pythons_splits_the_directory_from_the_document():
    """The first line is the managed Python directory, the second the working
    directory, the rest the JSON array; entries come back sorted on
    ``(key, path)`` -- lexicographically, so uv's 3.13 keys order before its
    3.14 one."""
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
        "/root",
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


async def test_uv_pythons_anchors_a_cwd_relative_managed_path():
    """The real-host case the absolute fixtures miss: current uv prints a
    managed download relative to the shell's directory when that directory is an
    ancestor (the SSH chain runs from the login home, so
    ``.local/share/uv/python/...``), and the captured ``pwd`` anchors it -- the
    entry resolves to an absolute path and reads ``managed=True``, where
    comparing the bare relative path against the absolute directory read
    ``False``."""
    output = [
        "/home/supervisor/.local/share/uv/python",
        "/home/supervisor",
        _rows(".local/share/uv/python/cpython-3.11.16-linux-x86_64-gnu/bin/python3.11"),
    ]
    assert Pythons.parse(output) == [
        _python(
            "cpython-3.13.1-linux-x86_64-gnu",
            "3.13.1",
            "/home/supervisor/.local/share/uv/python/"
            "cpython-3.11.16-linux-x86_64-gnu/bin/python3.11",
            managed=True,
        )
    ]


async def test_uv_pythons_keeps_one_key_at_several_paths():
    """pyinfra keys its answer by uv's ``key``, which one interpreter found at
    two paths silently overwrites; both paths survive here."""
    entries = Pythons.parse(
        ["/uv", "/", _rows("/usr/bin/python3.13", "/usr/bin/python3")]
    )
    assert [entry["path"] for entry in entries] == ["/usr/bin/python3", "/usr/bin/python3.13"]
    assert {entry["key"] for entry in entries} == {"cpython-3.13.1-linux-x86_64-gnu"}


async def test_uv_pythons_empty_state_reads_as_no_interpreters():
    """pyinfra's ``empty`` fixture: the bare array -- a brand-new host, not the
    dozens of downloadable versions -- and a chain that printed no array at all
    both read as ``[]``."""
    assert Pythons.parse(["/root/.local/share/uv/python", "/root", "[]"]) == []
    assert Pythons.parse(["/root/.local/share/uv/python"]) == []
    assert Pythons.parse([]) == []


async def test_uv_pythons_parse_is_total():
    """Unparsable JSON, a document that is not an array, non-mapping rows and
    rows missing any of the four strings contribute nothing -- where pyinfra's
    ``process_json`` warns or raises -- while a good row alongside lands."""
    assert Pythons.parse(["/uv", "/", "no array here"]) == []
    assert Pythons.parse(['/uv', '/', '{"key": "not a list"}']) == []
    assert Pythons.parse(['/uv', '/', '["a string", {"key": "k"}]']) == []
    assert Pythons.parse(
        ["/uv", "/", '7, ' + _rows("/usr/bin/python3.13")]
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
    """The package re-exports the four facts and their entry types, the six
    operations and the thirteen descriptors, so ``uv.Tools``, ``uv.packages``
    and ``uv.UvPipInstallCommand`` resolve; the top-level package makes
    ``from reemote import Info, Packages, Pythons, Tools`` and the descriptors
    work, exposes the ``uv`` namespace, but keeps the operation functions
    namespace-only (no top-level ``packages``)."""
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
        ("packages", uv.packages),
        ("pythons", uv.pythons),
        ("tools", uv.tools),
        ("tools_upgrade_all", uv.tools_upgrade_all),
        ("tools_update_shell", uv.tools_update_shell),
        ("venv", uv.venv),
        ("UvPipInstallCommand", UvPipInstallCommand),
        ("UvPipUpgradeCommand", UvPipUpgradeCommand),
        ("UvPipRemoveCommand", UvPipRemoveCommand),
        ("UvPipRequirementsCommand", UvPipRequirementsCommand),
        ("UvPythonInstallCommand", UvPythonInstallCommand),
        ("UvPythonRemoveCommand", UvPythonRemoveCommand),
        ("UvToolInstallCommand", UvToolInstallCommand),
        ("UvToolUpgradeCommand", UvToolUpgradeCommand),
        ("UvToolRemoveCommand", UvToolRemoveCommand),
        ("UvToolUpgradeAllCommand", UvToolUpgradeAllCommand),
        ("UvToolUpdateShellCommand", UvToolUpdateShellCommand),
        ("UvVenvCreateCommand", UvVenvCreateCommand),
        ("UvVenvRemoveCommand", UvVenvRemoveCommand),
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
        "UvPipInstallCommand",
        "UvToolUpdateShellCommand",
        "UvVenvCreateCommand",
    ):
        assert name in reemote.__all__
        assert getattr(reemote, name) is not None
    # Operations are reached through the namespace, never the top level.
    for name in ("packages", "pythons", "tools", "venv"):
        assert name not in reemote.__all__


# --- Operation: uv pip command descriptors -------------------------------------


def test_uv_pip_install_command_renders_uv_pip_install():
    """pyinfra's install builder verbatim (single-spaced): ``uv pip install
    --no-progress`` plus the package tokens, ``==`` pins included."""
    cmd = UvPipInstallCommand(packages=("six==1.16.0", "pyinfra"))
    assert cmd.shell() == "uv pip install --no-progress six==1.16.0 pyinfra"


def test_uv_pip_install_command_inserts_extra_args_unquoted():
    """``extra_args`` rides right after ``--no-progress``, appended unquoted
    (the dnf/pip convention: a raw shell fragment the caller keeps safe)."""
    cmd = UvPipInstallCommand(
        packages=("six",), extra_args="--index-url https://example/simple"
    )
    assert cmd.shell() == (
        "uv pip install --no-progress --index-url https://example/simple six"
    )


def test_uv_pip_upgrade_command_renders_install_with_upgrade():
    """The upgrade command is the install command plus ``--upgrade``, with
    uv's own token order (the flag before ``--no-progress``)."""
    cmd = UvPipUpgradeCommand(packages=("six",))
    assert cmd.shell() == "uv pip install --upgrade --no-progress six"
    extra = UvPipUpgradeCommand(packages=("six",), extra_args="--offline")
    assert extra.shell() == "uv pip install --upgrade --no-progress --offline six"


def test_uv_pip_remove_command_needs_no_yes():
    """Unlike ``pip uninstall``, ``uv pip uninstall`` is non-interactive, so
    no ``--yes`` is emitted; the bare names carry ``extra_args`` too."""
    cmd = UvPipRemoveCommand(packages=("six", "requests"))
    assert cmd.shell() == "uv pip uninstall --no-progress six requests"
    assert (
        UvPipRemoveCommand(packages=("six",), extra_args="--broken").shell()
        == "uv pip uninstall --no-progress --broken six"
    )


def test_uv_pip_requirements_command_renders_both_directions():
    """``-r <file>`` install (with ``--upgrade`` when ``latest``), uninstall
    otherwise -- one descriptor, the PipRequirementsCommand precedent."""
    install = UvPipRequirementsCommand(path="/tmp/reqs.txt")
    assert install.shell() == "uv pip install --no-progress -r /tmp/reqs.txt"
    latest = UvPipRequirementsCommand(path="/tmp/reqs.txt", latest=True)
    assert latest.shell() == "uv pip install --upgrade --no-progress -r /tmp/reqs.txt"
    remove = UvPipRequirementsCommand(path="/tmp/reqs.txt", present=False)
    assert remove.shell() == "uv pip uninstall --no-progress -r /tmp/reqs.txt"
    extra = UvPipRequirementsCommand(
        path="/tmp/reqs.txt", present=False, extra_args="--break-system"
    )
    assert extra.shell() == (
        "uv pip uninstall --no-progress --break-system -r /tmp/reqs.txt"
    )


def test_uv_pip_commands_shell_quote_names_and_paths():
    """Package tokens and the requirements path are shell-quoted so a
    metacharacter never reaches the remote shell unescaped."""
    package = "six;touch /tmp/pwned"
    quoted = shlex.quote(package)
    assert quoted != package
    assert (
        UvPipInstallCommand(packages=(package,)).shell()
        == f"uv pip install --no-progress {quoted}"
    )
    path = "/tmp/re qs;rm"
    assert (
        UvPipRequirementsCommand(path=path).shell()
        == f"uv pip install --no-progress -r {shlex.quote(path)}"
    )


def test_uv_pip_commands_scope_to_a_venv():
    """``venv`` renders uv's ``--python`` after ``--no-progress`` -- and, on the
    requirements descriptor, before ``-r`` -- on all four commands."""
    assert (
        UvPipInstallCommand(packages=("six",), venv="/srv/app/.venv").shell()
        == "uv pip install --no-progress --python /srv/app/.venv six"
    )
    assert (
        UvPipUpgradeCommand(packages=("six",), venv="/srv/app/.venv").shell()
        == "uv pip install --upgrade --no-progress --python /srv/app/.venv six"
    )
    assert (
        UvPipRemoveCommand(packages=("six",), venv="/srv/app/.venv").shell()
        == "uv pip uninstall --no-progress --python /srv/app/.venv six"
    )
    assert (
        UvPipRequirementsCommand(path="/tmp/reqs.txt", venv="/srv/app/.venv").shell()
        == "uv pip install --no-progress --python /srv/app/.venv -r /tmp/reqs.txt"
    )
    assert (
        UvPipRequirementsCommand(
            path="/tmp/reqs.txt", present=False, venv="/srv/app/.venv"
        ).shell()
        == "uv pip uninstall --no-progress --python /srv/app/.venv -r /tmp/reqs.txt"
    )


def test_uv_pip_venv_precedes_extra_args_and_quotes_the_path():
    """A real flag comes before the raw ``extra_args`` fragment, and the
    environment is shell-quoted like every other user string."""
    cmd = UvPipInstallCommand(
        packages=("six",),
        extra_args="--index-url https://x/simple",
        venv="/srv/my venv;rm",
    )
    assert cmd.shell() == (
        "uv pip install --no-progress --python '/srv/my venv;rm' "
        "--index-url https://x/simple six"
    )


def test_uv_pip_commands_without_a_venv_render_the_parity_shells():
    """``venv`` is a defaulted trailing field, so an unscoped command is
    byte-identical to pyinfra's builder output -- no stray ``--python``."""
    assert (
        UvPipInstallCommand(packages=("six",), extra_args=None, venv=None).shell()
        == "uv pip install --no-progress six"
    )
    for cmd in (
        UvPipInstallCommand(packages=("six",)),
        UvPipUpgradeCommand(packages=("six",)),
        UvPipRemoveCommand(packages=("six",)),
        UvPipRequirementsCommand(path="/tmp/reqs.txt"),
    ):
        assert "--python" not in cmd.shell()


def test_uv_pip_commands_are_elevated_by_sudo():
    """A system-interpreter install needs root, so ``sudo=True`` prefixes the
    rendered shell; the run-as user comes from the inventory, never here."""
    cmd = UvPipInstallCommand(packages=("six",))
    assert command_shell(cmd) == "uv pip install --no-progress six"
    assert command_shell(cmd, sudo=True) == "sudo uv pip install --no-progress six"
    remove = UvPipRemoveCommand(packages=("six",))
    assert command_shell(remove, su=True) == f"su -c {shlex.quote(remove.shell())}"


# --- Operation: python interpreter descriptors ---------------------------------


def test_uv_python_install_command_renders_one_version():
    """uv installs one version per call, so the descriptor carries a single
    ``version`` token shell-quoted, ``extra_args`` appended unquoted."""
    cmd = UvPythonInstallCommand(version="3.13")
    assert cmd.shell() == "uv python install --no-progress 3.13"
    assert (
        UvPythonInstallCommand(version="3.13.4", extra_args="--offline").shell()
        == "uv python install --no-progress --offline 3.13.4"
    )


def test_uv_python_remove_command_renders_uninstall():
    """The ``present=False`` direction; a partial request rides through
    verbatim so uv decides which installs match."""
    cmd = UvPythonRemoveCommand(version="3.13")
    assert cmd.shell() == "uv python uninstall --no-progress 3.13"


def test_uv_python_command_quotes_the_version():
    hostile = "3.13;rm -rf /"
    assert (
        UvPythonInstallCommand(version=hostile).shell()
        == f"uv python install --no-progress {shlex.quote(hostile)}"
    )


# --- Operation: tool descriptors ------------------------------------------------


def test_uv_tool_install_command_renders_one_tool():
    """uv installs one tool per call: the token (a name or a ``==`` pin) is
    shell-quoted, ``extra_args`` appended unquoted."""
    cmd = UvToolInstallCommand(tool="pyinfra==3.5.1")
    assert cmd.shell() == "uv tool install --no-progress pyinfra==3.5.1"
    assert (
        UvToolInstallCommand(tool="ruff", extra_args="--python 3.12").shell()
        == "uv tool install --no-progress --python 3.12 ruff"
    )


def test_uv_tool_install_command_renders_python_and_with():
    """``python`` renders one ``--python`` and ``with_packages`` one ``--with``
    per entry -- uv's repeatable flag -- both ahead of ``extra_args`` and the
    tool token."""
    cmd = UvToolInstallCommand(
        tool="pyinfra==3.5.1",
        python="3.12",
        with_packages=("ansible-core", "sqlalchemy"),
    )
    assert cmd.shell() == (
        "uv tool install --no-progress --python 3.12 "
        "--with ansible-core --with sqlalchemy pyinfra==3.5.1"
    )


def test_uv_tool_upgrade_command_carries_python_and_with():
    """The upgrade re-creates the tool's environment, so uv accepts (and the
    operation passes) the same two flags on it."""
    cmd = UvToolUpgradeCommand(tool="ruff", python="3.13", with_packages=("uv",))
    assert cmd.shell() == (
        "uv tool install --upgrade --no-progress --python 3.13 --with uv ruff"
    )


def test_uv_tool_commands_quote_python_and_with():
    hostile = "3.12;rm -rf /"
    cmd = UvToolInstallCommand(tool="ruff", python=hostile, with_packages=(hostile,))
    assert cmd.shell() == (
        f"uv tool install --no-progress --python {shlex.quote(hostile)} "
        f"--with {shlex.quote(hostile)} ruff"
    )


def test_uv_tool_remove_command_takes_no_environment_flags():
    """Neither ``--python`` nor ``--with`` exists on ``uv tool uninstall``, so the
    removal descriptor keeps no such field at all."""
    assert "python" not in UvToolRemoveCommand.__dataclass_fields__
    assert "with_packages" not in UvToolRemoveCommand.__dataclass_fields__
    cmd = UvToolRemoveCommand(name="pyinfra")
    assert cmd.shell() == "uv tool uninstall --no-progress pyinfra"


def test_uv_tool_upgrade_command_adds_upgrade_flag():
    """uv upgrades a tool by reinstalling it with ``--upgrade``."""
    cmd = UvToolUpgradeCommand(tool="ruff")
    assert cmd.shell() == "uv tool install --upgrade --no-progress ruff"


def test_uv_tool_remove_command_renders_uninstall():
    """uv's tool uninstall takes a bare name, not a pinned requirement."""
    cmd = UvToolRemoveCommand(name="pyinfra")
    assert cmd.shell() == "uv tool uninstall --no-progress pyinfra"


def test_uv_tool_upgrade_all_command_renders_all():
    """pyinfra's ``uv tool upgrade --all`` verbatim, extra args at the end."""
    assert UvToolUpgradeAllCommand().shell() == "uv tool upgrade --all"
    assert (
        UvToolUpgradeAllCommand(extra_args="--offline").shell()
        == "uv tool upgrade --all --offline"
    )


def test_uv_tool_update_shell_command_is_a_fixed_shell():
    """``uv tool update-shell`` takes no path -- uv infers its own bin dir."""
    cmd = UvToolUpdateShellCommand()
    assert cmd.shell() == "uv tool update-shell"
    assert command_shell(cmd, sudo=True) == "sudo uv tool update-shell"


def test_uv_tool_commands_shell_quote_the_token():
    hostile = "ruff;rm -rf /"
    assert (
        UvToolInstallCommand(tool=hostile).shell()
        == f"uv tool install --no-progress {shlex.quote(hostile)}"
    )
    assert (
        UvToolRemoveCommand(name=hostile).shell()
        == f"uv tool uninstall --no-progress {shlex.quote(hostile)}"
    )


# --- Operation: venv descriptors and the private probe -------------------------


def test_uv_venv_create_command_renders_bare_path():
    """pyinfra's uv venv builder: ``uv venv`` with just the path when no
    option is set."""
    assert UvVenvCreateCommand(path="/srv/app/.venv").shell() == (
        "uv venv /srv/app/.venv"
    )


def test_uv_venv_create_command_renders_flags_in_pyinfra_order():
    """The flags render in pyinfra's own order -- ``--allow-existing`` /
    ``--clear``, ``--seed``, ``--link-mode``, ``--site-packages``,
    ``--python`` -- then extra args, then the path."""
    cmd = UvVenvCreateCommand(
        path="/srv/app/.venv",
        python="python3.12",
        seed=True,
        link_mode="symlink",
        site_packages=True,
    )
    assert cmd.shell() == (
        "uv venv --seed --link-mode symlink --site-packages"
        " --python python3.12 /srv/app/.venv"
    )
    clear = UvVenvCreateCommand(path="/v", clear_existing=True)
    assert clear.shell() == "uv venv --clear /v"
    allow = UvVenvCreateCommand(path="/v", allow_existing=True)
    assert allow.shell() == "uv venv --allow-existing /v"


def test_uv_venv_create_command_shell_quotes_every_user_string():
    hostile_path = "/srv/my venv;rm"
    hostile_python = "py;rm"
    hostile_mode = "copy;rm"
    cmd = UvVenvCreateCommand(
        path=hostile_path, python=hostile_python, link_mode=hostile_mode
    )
    assert cmd.shell() == (
        f"uv venv --link-mode {shlex.quote(hostile_mode)}"
        f" --python {shlex.quote(hostile_python)} {shlex.quote(hostile_path)}"
    )


def test_uv_venv_remove_command_renders_rm_rf():
    """The whole-tree removal, uv-local (pyinfra delegates it to
    ``files.directory(present=False)``; reemote stays in one namespace)."""
    assert UvVenvRemoveCommand(path="/srv/app/.venv").shell() == (
        "rm -rf /srv/app/.venv"
    )
    hostile = UvVenvRemoveCommand(path="/srv/v;rm")
    assert hostile.shell() == f"rm -rf {shlex.quote('/srv/v;rm')}"


def test_venv_exists_probe_command_is_guarded():
    """One ``|| true`` group: the probe always exits 0 and prints ``yes``
    only when ``<path>/bin/activate`` is there; the path is shell-quoted."""
    assert _VenvExists.command("/srv/app/.venv") == (
        "test -e /srv/app/.venv/bin/activate && echo yes || true"
    )
    assert "'/srv/my venv'/bin/activate" in _VenvExists.command("/srv/my venv")


def test_venv_exists_parse_reads_the_yes_token():
    assert _VenvExists.parse(["yes"]) is True
    assert _VenvExists.parse(["  yes  "]) is True
    assert _VenvExists.parse([""]) is False
    assert _VenvExists.parse([]) is False
    assert _VenvExists.parse(["nope"]) is False


async def test_venv_exists_reads_the_simulated_seam():
    """A SimulatedHost answers membership of its ``uv_venvs`` set; an absent
    path is simply no venv, not an error."""
    host = SimulatedHost(name="h", uv_venvs={"/srv/app/.venv"})
    assert await _VenvExists.process(host, "/srv/app/.venv") is True
    assert await _VenvExists.process(host, "/srv/other") is False


# --- Operation: packages idempotence -------------------------------------------


def _prepare_ctx(host: SimulatedHost) -> OpContext:
    """A prepare-phase context reading the host's simulated facts."""
    return OpContext(host=host, facts=FactCache(host, phase="prepare"))


async def test_uv_packages_installs_missing_packages():
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.packages(ctx, packages=("pyinfra", "six"))]
    assert ctx.changed
    assert ctx.reason == "2 package(s) to install"
    assert commands == [UvPipInstallCommand(packages=("pyinfra", "six"))]


async def test_uv_packages_no_change_when_present():
    host = SimulatedHost(name="h", uv_packages={"six": "1.16.0"})
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.packages(ctx, packages=("six",))]
    assert not ctx.changed
    assert ctx.reason == "all packages present"
    assert commands == []


async def test_uv_packages_accepts_a_bare_string():
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.packages(ctx, packages="pyinfra")]
    assert commands == [UvPipInstallCommand(packages=("pyinfra",))]


async def test_uv_packages_none_is_a_noop():
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.packages(ctx)]
    assert not ctx.changed
    assert ctx.reason == "no packages requested"
    assert commands == []


async def test_uv_packages_version_pin_present_only_on_exact_version():
    host = SimulatedHost(name="h", uv_packages={"six": "1.16.0"})
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.packages(ctx, packages=("six==1.16.0",))]
    assert not ctx.changed
    assert commands == []


async def test_uv_packages_version_pin_installs_when_version_differs():
    host = SimulatedHost(name="h", uv_packages={"six": "1.16.0"})
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.packages(ctx, packages=("six==1.17.0",))]
    assert ctx.changed
    assert commands == [UvPipInstallCommand(packages=("six==1.17.0",))]


async def test_uv_packages_pin_splits_on_the_equals_not_the_name():
    """The pin splits at ``==``, so a version that itself contains ``=``
    stays whole and reads present."""
    host = SimulatedHost(name="h", uv_packages={"foo": "1=2"})
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.packages(ctx, packages=("foo==1=2",))]
    assert not ctx.changed
    assert commands == []


async def test_uv_packages_compares_at_lowercased_names():
    """The uv fact keeps verbatim (canonical) names and the operation matches
    pyinfra's lowercased comparison, so a differently-cased request meets the
    stored spelling -- but a ``_``/``.`` spelling (no PEP 503 fold) does not."""
    host = SimulatedHost(name="h", uv_packages={"PyYAML": "6.0.2"})
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.packages(ctx, packages=("pyyaml",))]
    assert not ctx.changed
    assert ctx.reason == "all packages present"
    assert commands == []
    # A re-spelling uv treats as a distinct distribution is not matched.
    ctx2 = _prepare_ctx(SimulatedHost(name="h", uv_packages={"zope.interface": "7.2"}))
    commands2 = [
        c async for c in uv.packages(ctx2, packages=("zope-interface",))
    ]
    assert commands2 == [UvPipInstallCommand(packages=("zope-interface",))]


async def test_uv_packages_other_specifiers_are_name_only():
    host = SimulatedHost(name="h", uv_packages={"requests": "2.31.0"})
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.packages(ctx, packages=("requests>=2.28",))]
    assert not ctx.changed
    assert commands == []


# --- Operation: packages latest ------------------------------------------------


async def test_uv_packages_latest_upgrades_installed_unpinned():
    """``latest`` upgrades present, unpinned packages; missing ones still
    install (the install command comes first), the pip legacy behavior."""
    host = SimulatedHost(name="h", uv_packages={"six": "1.16.0"})
    ctx = _prepare_ctx(host)
    commands = [
        c async for c in uv.packages(ctx, packages=("six", "pyinfra"), latest=True)
    ]
    assert commands == [
        UvPipInstallCommand(packages=("pyinfra",)),
        UvPipUpgradeCommand(packages=("six",)),
    ]
    assert ctx.reason == "1 package(s) to install; 1 package(s) to upgrade"


async def test_uv_packages_latest_does_not_upgrade_a_pinned_package():
    """A pinned package that is installed at that version is neither
    reinstalled nor upgraded -- only unpinned packages take the upgrade."""
    host = SimulatedHost(name="h", uv_packages={"six": "1.16.0"})
    ctx = _prepare_ctx(host)
    commands = [
        c async for c in uv.packages(ctx, packages=("six==1.16.0",), latest=True)
    ]
    assert not ctx.changed
    assert ctx.reason == "all packages present"
    assert commands == []


# --- Operation: packages removal -----------------------------------------------


async def test_uv_packages_removal_feeds_bare_names():
    """The ``present=False`` direction is version-aware but feeds uv bare
    names, de-duplicated (pyinfra feeds the ``==`` tokens through)."""
    host = SimulatedHost(name="h", uv_packages={"six": "1.16.0", "pyinfra": ""})
    ctx = _prepare_ctx(host)
    commands = [
        c
        async for c in uv.packages(
            ctx, packages=("six==1.16.0", "pyinfra"), present=False
        )
    ]
    assert ctx.changed
    assert ctx.reason == "2 package(s) to remove"
    assert commands == [UvPipRemoveCommand(packages=("six", "pyinfra"))]


async def test_uv_packages_removal_skips_a_mismatched_pin():
    """A pinned package installed at another version is not removed (the pin
    is not present)."""
    host = SimulatedHost(name="h", uv_packages={"six": "1.16.0"})
    ctx = _prepare_ctx(host)
    commands = [
        c
        async for c in uv.packages(
            ctx, packages=("six==2.0.0",), present=False
        )
    ]
    assert not ctx.changed
    assert ctx.reason == "no packages to remove"
    assert commands == []


async def test_uv_packages_remove_noop_when_absent():
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [
        c async for c in uv.packages(ctx, packages=("six",), present=False)
    ]
    assert not ctx.changed
    assert ctx.reason == "no packages to remove"
    assert commands == []


# --- Operation: packages requirements (stateless) ------------------------------


async def test_uv_packages_requirements_always_yields():
    host = SimulatedHost(name="h", uv_packages={"six": "1.16.0"})
    ctx = _prepare_ctx(host)
    commands = [
        c async for c in uv.packages(ctx, requirements="/tmp/reqs.txt")
    ]
    assert ctx.changed
    assert ctx.reason == "requirements file /tmp/reqs.txt to install"
    assert commands == [
        UvPipRequirementsCommand(path="/tmp/reqs.txt", present=True)
    ]


async def test_uv_packages_requirements_removal_direction():
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [
        c
        async for c in uv.packages(
            ctx, requirements="/tmp/reqs.txt", present=False
        )
    ]
    assert ctx.changed
    assert ctx.reason == "requirements file /tmp/reqs.txt to remove"
    assert commands[0].shell() == "uv pip uninstall --no-progress -r /tmp/reqs.txt"


async def test_uv_packages_requirements_yielded_before_package_commands():
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [
        c
        async for c in uv.packages(
            ctx, packages=("six",), requirements="/tmp/reqs.txt"
        )
    ]
    assert commands == [
        UvPipRequirementsCommand(path="/tmp/reqs.txt", present=True),
        UvPipInstallCommand(packages=("six",)),
    ]
    assert ctx.reason == (
        "requirements file /tmp/reqs.txt to install; 1 package(s) to install"
    )


# --- Operation: packages options threading and kwargs --------------------------


async def test_uv_packages_threads_extra_args_both_ways():
    """A single ``extra_args`` string reaches the install, upgrade, remove and
    requirements commands alike (pyinfra's uv semantics: the one fragment
    applies to every uv pip command)."""
    host = SimulatedHost(name="h", uv_packages={"six": "1.16.0"})
    ctx = _prepare_ctx(host)
    install = [
        c
        async for c in uv.packages(
            ctx, packages=("pyinfra",), extra_args="--index-url https://x/simple"
        )
    ]
    assert install[0].shell() == (
        "uv pip install --no-progress --index-url https://x/simple pyinfra"
    )
    ctx2 = _prepare_ctx(SimulatedHost(name="h", uv_packages={"six": "1.16.0"}))
    remove = [
        c
        async for c in uv.packages(
            ctx2, packages=("six",), present=False, extra_args="--break"
        )
    ]
    assert remove[0].shell() == "uv pip uninstall --no-progress --break six"


async def test_uv_packages_absorbs_unknown_kwargs():
    """pyinfra's global arguments (and any option reemote drops) ride into
    ``**kwargs`` with no effect -- ``op()`` already strips
    ``name``/``sudo``/``su``."""
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [
        c
        async for c in uv.packages(
            ctx, packages=("six",), virtualenv="/tmp/v", env={} 
        )
    ]
    assert commands == [UvPipInstallCommand(packages=("six",))]


async def test_uv_packages_threads_venv_into_the_fact_and_commands():
    """Both sides of the comparison name the environment: the listing uv checks
    against is the one the command mutates, which is why scoping the commands
    alone would never converge."""
    host = CannedShellHost('[{"name": "six", "version": "1.16.0"}]')
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in uv.packages(
            ctx, packages=("six", "pyinfra"), venv="/srv/app/.venv"
        )
    ]
    assert host.last_shell == "uv pip list --format json --python /srv/app/.venv"
    assert ctx.reason == "1 package(s) to install"
    assert commands == [
        UvPipInstallCommand(packages=("pyinfra",), venv="/srv/app/.venv")
    ]


async def test_uv_packages_scopes_removal_and_requirements():
    """The environment reaches the uninstall too (uv accepts ``--python`` on it)
    and the ``-r`` command, so nothing is removed from the wrong environment."""
    host = SimulatedHost(name="h", uv_packages={"six": "1.16.0"})
    ctx = _prepare_ctx(host)
    commands = [
        c
        async for c in uv.packages(
            ctx,
            packages=("six",),
            present=False,
            requirements="/tmp/reqs.txt",
            venv="/srv/app/.venv",
        )
    ]
    assert [c.shell() for c in commands] == [
        "uv pip uninstall --no-progress --python /srv/app/.venv -r /tmp/reqs.txt",
        "uv pip uninstall --no-progress --python /srv/app/.venv six",
    ]


async def test_uv_packages_rejects_an_empty_venv():
    """Checked against the CLI: ``uv pip list --python ''`` exits 0 and quietly
    reports the ambient environment, so a blank target is a typo rather than a
    scope -- it raises, before the fact read."""
    import pytest

    for blank in ("", "   "):
        host = CannedShellHost("[]")
        ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
        with pytest.raises(
            ValueError, match="must provide the virtual environment"
        ):
            [
                c
                async for c in uv.packages(
                    ctx,
                    packages=("six",),
                    requirements="/tmp/reqs.txt",
                    venv=blank,
                )
            ]
        assert host.last_shell is None
        assert not ctx.changed


# --- Operation: pythons idempotence --------------------------------------------


async def test_uv_pythons_installs_missing_version():
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.pythons(ctx, versions=("3.13",))]
    assert ctx.changed
    assert ctx.reason == "python 3.13 to install"
    assert commands == [UvPythonInstallCommand(version="3.13")]


async def test_uv_pythons_bare_string_and_multiple_versions():
    """One descriptor per requested version (uv takes one at a time), the
    order the request names them."""
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.pythons(ctx, versions=("3.12", "3.13"))]
    assert commands == [
        UvPythonInstallCommand(version="3.12"),
        UvPythonInstallCommand(version="3.13"),
    ]


def _managed(version: str, path: str) -> PythonInfo:
    """A ``uv_pythons`` store record for a uv-managed interpreter."""
    return {
        "key": f"cpython-{version}",
        "implementation": "cpython",
        "version": version,
        "path": path,
        "managed": True,
    }


async def test_uv_pythons_partial_request_matches_installed_micro():
    """The dotted-component match the design adds: a request ``3.13`` is
    satisfied by an installed ``3.13.4``, where pyinfra's exact-key compare
    never converged."""
    host = SimulatedHost(
        name="h", uv_pythons={"/p/3.13.4": _managed("3.13.4", "/p/3.13.4")}
    )
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.pythons(ctx, versions=("3.13",))]
    assert not ctx.changed
    assert ctx.reason == "python 3.13 already installed"
    assert commands == []


async def test_uv_pythons_managed_filter_ignores_system_interpreters():
    """By default only uv-managed interpreters count as installed; a system
    3.13 is not, so the request installs -- until ``managed=False`` widens the
    filter to every interpreter present."""
    system = {
        "/usr/bin/python3.13": {
            "key": "cpython-3.13.5",
            "implementation": "cpython",
            "version": "3.13.5",
            "managed": False,
        }
    }
    ctx = _prepare_ctx(SimulatedHost(name="h", uv_pythons=dict(system)))
    commands = [c async for c in uv.pythons(ctx, versions=("3.13",))]
    assert commands == [UvPythonInstallCommand(version="3.13")]
    ctx2 = _prepare_ctx(SimulatedHost(name="h", uv_pythons=dict(system)))
    commands2 = [
        c async for c in uv.pythons(ctx2, versions=("3.13",), managed=False)
    ]
    assert not ctx2.changed
    assert commands2 == []


async def test_uv_pythons_removal():
    host = SimulatedHost(
        name="h", uv_pythons={"/p/3.13.4": _managed("3.13.4", "/p/3.13.4")}
    )
    ctx = _prepare_ctx(host)
    commands = [
        c async for c in uv.pythons(ctx, versions=("3.13",), present=False)
    ]
    assert ctx.changed
    assert ctx.reason == "python 3.13 to remove"
    assert commands == [UvPythonRemoveCommand(version="3.13")]


async def test_uv_pythons_removal_noop_when_absent():
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [
        c async for c in uv.pythons(ctx, versions=("3.13",), present=False)
    ]
    assert not ctx.changed
    assert ctx.reason == "python 3.13 not installed"
    assert commands == []


async def test_uv_pythons_none_is_a_noop():
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.pythons(ctx)]
    assert not ctx.changed
    assert ctx.reason == "no Python versions requested"
    assert commands == []


# --- Operation: tools idempotence ----------------------------------------------


async def test_uv_tools_installs_missing_tool():
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.tools(ctx, tools=("ruff",))]
    assert ctx.changed
    assert ctx.reason == "tool ruff to install"
    assert commands == [UvToolInstallCommand(tool="ruff")]


async def test_uv_tools_yields_one_descriptor_per_tool():
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.tools(ctx, tools=("ruff", "black"))]
    assert commands == [
        UvToolInstallCommand(tool="ruff"),
        UvToolInstallCommand(tool="black"),
    ]


async def test_uv_tools_no_change_when_present():
    host = SimulatedHost(name="h", uv_tools={"ruff": {"version": "0.8.4"}})
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.tools(ctx, tools=("ruff",))]
    assert not ctx.changed
    assert ctx.reason == "tool ruff already installed"
    assert commands == []


async def test_uv_tools_versionless_local_tool_converges():
    """The convergence fix over pyinfra: a tool installed from a local path
    or git URL carries ``version=None`` yet is genuinely present, so a bare
    name matching it is *not* reinstalled forever."""
    host = SimulatedHost(name="h", uv_tools={"localtool": {"version": None}})
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.tools(ctx, tools=("localtool",))]
    assert not ctx.changed
    assert commands == []


async def test_uv_tools_pin_present_only_on_exact_version():
    host = SimulatedHost(name="h", uv_tools={"ruff": {"version": "0.8.4"}})
    ctx = _prepare_ctx(host)
    assert [c async for c in uv.tools(ctx, tools=("ruff==0.8.4",))] == []
    ctx2 = _prepare_ctx(
        SimulatedHost(name="h", uv_tools={"ruff": {"version": "0.8.4"}})
    )
    changed = [c async for c in uv.tools(ctx2, tools=("ruff==0.9.0",))]
    assert ctx2.changed
    assert changed == [UvToolInstallCommand(tool="ruff==0.9.0")]


async def test_uv_tools_matches_case_insensitively():
    host = SimulatedHost(name="h", uv_tools={"Ruff": {"version": "0.8.4"}})
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.tools(ctx, tools=("ruff",))]
    assert not ctx.changed
    assert commands == []


async def test_uv_tools_latest_upgrades_installed_unpinned():
    """``latest`` upgrades an installed, unpinned tool via ``uv tool install
    --upgrade``; a missing tool still installs."""
    host = SimulatedHost(name="h", uv_tools={"ruff": {"version": "0.8.4"}})
    ctx = _prepare_ctx(host)
    commands = [
        c async for c in uv.tools(ctx, tools=("ruff", "black"), latest=True)
    ]
    # Each tool yields its own descriptor in request order, so the installed
    # ``ruff`` upgrade precedes the missing ``black`` install.
    assert ctx.reason == "tool ruff to upgrade; tool black to install"
    assert commands == [
        UvToolUpgradeCommand(tool="ruff"),
        UvToolInstallCommand(tool="black"),
    ]


async def test_uv_tools_removal():
    host = SimulatedHost(name="h", uv_tools={"ruff": {"version": "0.8.4"}})
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.tools(ctx, tools=("ruff",), present=False)]
    assert ctx.changed
    assert ctx.reason == "tool ruff to remove"
    assert commands == [UvToolRemoveCommand(name="ruff")]


async def test_uv_tools_removal_noop_when_absent():
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.tools(ctx, tools=("ruff",), present=False)]
    assert not ctx.changed
    assert ctx.reason == "tool ruff not installed"
    assert commands == []


async def test_uv_tools_none_is_a_noop():
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.tools(ctx)]
    assert not ctx.changed
    assert ctx.reason == "no tools requested"
    assert commands == []


async def test_uv_tools_threads_python_and_with():
    """``python`` and ``with_packages`` reach the install descriptor, where uv
    accepts them; a bare ``with_packages`` string is one ``--with``."""
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [
        c
        async for c in uv.tools(
            ctx, tools=("pyinfra==3.5.1",), python="3.12", with_packages="ansible-core"
        )
    ]
    assert commands == [
        UvToolInstallCommand(
            tool="pyinfra==3.5.1", python="3.12", with_packages=("ansible-core",)
        )
    ]
    assert commands[0].shell() == (
        "uv tool install --no-progress --python 3.12 --with ansible-core "
        "pyinfra==3.5.1"
    )


async def test_uv_tools_normalizes_a_list_of_with_packages():
    """A sequence normalizes to the descriptor's tuple, one ``--with`` per
    entry; ``op()`` still wants a tuple, since request kwargs must hash."""
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [
        c async for c in uv.tools(ctx, tools=("ruff",), with_packages=["a", "b"])
    ]
    assert commands == [UvToolInstallCommand(tool="ruff", with_packages=("a", "b"))]


async def test_uv_tools_upgrade_carries_python_and_with():
    """uv upgrades by re-creating the tool environment, so the flags ride on the
    upgrade too -- the documented way to converge on environment drift."""
    host = SimulatedHost(name="h", uv_tools={"ruff": {"version": "0.8.4"}})
    ctx = _prepare_ctx(host)
    commands = [
        c
        async for c in uv.tools(
            ctx, tools=("ruff",), latest=True, python="3.13", with_packages=("uv",)
        )
    ]
    assert commands == [
        UvToolUpgradeCommand(tool="ruff", python="3.13", with_packages=("uv",))
    ]


async def test_uv_tools_python_and_with_do_not_break_idempotence():
    """The deliberate limitation: the gathered fact is the plain ``uv tool
    list``, which reports neither flag, so a tool already at the requested
    version is not reinstalled because its declared environment changed."""
    host = SimulatedHost(name="h", uv_tools={"ruff": {"version": "0.8.4"}})
    ctx = _prepare_ctx(host)
    commands = [
        c
        async for c in uv.tools(
            ctx, tools=("ruff",), python="3.13", with_packages=("uv",)
        )
    ]
    assert not ctx.changed
    assert ctx.reason == "tool ruff already installed"
    assert commands == []


async def test_uv_tools_removal_ignores_the_environment_flags():
    """Neither flag exists on ``uv tool uninstall``, so the removal direction
    stays name-only whatever ``python``/``with_packages`` ask for."""
    host = SimulatedHost(name="h", uv_tools={"ruff": {"version": "0.8.4"}})
    ctx = _prepare_ctx(host)
    commands = [
        c
        async for c in uv.tools(
            ctx,
            tools=("ruff",),
            present=False,
            python="3.12",
            with_packages=("uv",),
        )
    ]
    assert commands == [UvToolRemoveCommand(name="ruff")]


# --- Operation: tools_upgrade_all (stateless) ----------------------------------


async def test_uv_tools_upgrade_all_always_yields():
    """A non-idempotent, fact-free operation: it always reports a change and
    yields the one command."""
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.tools_upgrade_all(ctx)]
    assert ctx.changed
    assert ctx.reason == "upgrade all uv tools"
    assert commands == [UvToolUpgradeAllCommand()]


async def test_uv_tools_upgrade_all_threads_extra_args():
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [
        c async for c in uv.tools_upgrade_all(ctx, extra_args="--offline")
    ]
    assert commands == [UvToolUpgradeAllCommand(extra_args="--offline")]


# --- Operation: tools_update_shell (idempotent on the facts) -------------------


async def test_uv_tools_update_shell_yields_when_missing_from_path():
    """The uv tool bin dir is resolved from ``Info`` and tested against the
    colon-split ``server.Env`` PATH; absent, the operation yields."""
    host = SimulatedHost(
        name="h",
        uv_install={"tool_bin_dir": "/home/u/.local/bin"},
        os_env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
    )
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.tools_update_shell(ctx)]
    assert ctx.changed
    assert ctx.reason == "/home/u/.local/bin is not on the PATH, will update it"
    assert commands == [UvToolUpdateShellCommand()]


async def test_uv_tools_update_shell_noop_when_on_path():
    host = SimulatedHost(
        name="h",
        uv_install={"tool_bin_dir": "/home/u/.local/bin"},
        os_env={"PATH": "/usr/local/bin:/home/u/.local/bin:/bin"},
    )
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.tools_update_shell(ctx)]
    assert not ctx.changed
    assert ctx.reason == "/home/u/.local/bin is already on the PATH"
    assert commands == []


async def test_uv_tools_update_shell_noop_when_bin_dir_unknown():
    """No uv installed: the ``Info`` chain short-circuits, ``tool_bin_dir``
    is ``None``, and an unknown directory is nothing to add -- not a
    perpetual re-yield."""
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.tools_update_shell(ctx)]
    assert not ctx.changed
    assert ctx.reason == "uv tool bin directory unknown, shell config left alone"
    assert commands == []


# --- Operation: venv idempotence and validation --------------------------------


async def test_uv_venv_creates_when_missing():
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.venv(ctx, path="/srv/app/.venv")]
    assert ctx.changed
    assert ctx.reason == "venv /srv/app/.venv missing, will create"
    assert commands == [UvVenvCreateCommand(path="/srv/app/.venv")]


async def test_uv_venv_threads_options_into_create():
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [
        c
        async for c in uv.venv(
            ctx,
            path="/srv/app/.venv",
            python="3.12",
            seed=True,
            link_mode="symlink",
        )
    ]
    assert commands == [
        UvVenvCreateCommand(
            path="/srv/app/.venv", python="3.12", seed=True, link_mode="symlink"
        )
    ]
    assert commands[0].shell() == (
        "uv venv --seed --link-mode symlink --python 3.12 /srv/app/.venv"
    )


async def test_uv_venv_noop_when_present():
    host = SimulatedHost(name="h", uv_venvs={"/srv/app/.venv"})
    ctx = _prepare_ctx(host)
    commands = [c async for c in uv.venv(ctx, path="/srv/app/.venv")]
    assert not ctx.changed
    assert ctx.reason == "venv already exists at /srv/app/.venv"
    assert commands == []


async def test_uv_venv_removal():
    host = SimulatedHost(name="h", uv_venvs={"/srv/app/.venv"})
    ctx = _prepare_ctx(host)
    commands = [
        c async for c in uv.venv(ctx, path="/srv/app/.venv", present=False)
    ]
    assert ctx.changed
    assert ctx.reason == "venv /srv/app/.venv present, will remove"
    assert commands == [UvVenvRemoveCommand(path="/srv/app/.venv")]


async def test_uv_venv_removal_noop_when_absent():
    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    commands = [
        c async for c in uv.venv(ctx, path="/srv/app/.venv", present=False)
    ]
    assert not ctx.changed
    assert ctx.reason == "venv does not exist at /srv/app/.venv"
    assert commands == []


async def test_uv_venv_rejects_allow_and_clear_together():
    """Both mutually-exclusive flags rejected before the fact read, so a bad
    request fails without an SSH round trip."""
    import pytest

    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    with pytest.raises(ValueError, match="cannot both be set"):
        [
            c
            async for c in uv.venv(
                ctx, path="/v", allow_existing=True, clear_existing=True
            )
        ]


async def test_uv_venv_rejects_empty_path():
    import pytest

    host = SimulatedHost(name="h")
    ctx = _prepare_ctx(host)
    with pytest.raises(ValueError, match="must provide the path"):
        [c async for c in uv.venv(ctx, path="   ")]


# --- Operation: SimulatedHost seam dispatch ------------------------------------


async def test_uv_pip_install_seam_records_versions():
    """``send_command`` maps ``UvPipInstallCommand`` onto ``uv_packages``: a
    pin records exactly that version (overwriting an older one), an unpinned
    new name gets the empty-string placeholder, an installed name keeps its
    version, and names are stored verbatim (no PEP 503 fold)."""
    host = SimulatedHost(name="h", uv_packages={"six": "1.16.0"})
    await send_command(host, UvPipInstallCommand(packages=("six==2.0.0", "Zope.Interface")))
    assert host.uv_packages == {"six": "2.0.0", "Zope.Interface": ""}
    await send_command(host, UvPipInstallCommand(packages=("Zope.Interface",)))
    assert host.uv_packages == {"six": "2.0.0", "Zope.Interface": ""}


async def test_uv_pip_upgrade_seam_leaves_versions():
    """The ``latest``-upgrade path has no index metadata to resolve newer
    versions against, so the simulated upgrade leaves versions untouched."""
    host = SimulatedHost(name="h", uv_packages={"six": "1.16.0"})
    await send_command(host, UvPipUpgradeCommand(packages=("six",)))
    assert host.uv_packages == {"six": "1.16.0"}


async def test_uv_pip_remove_seam_drops_case_insensitively():
    """Removal pops the entry case-insensitively -- the shared rule with
    ``uv.packages``, which lowercases both sides; an absent name is tolerated."""
    host = SimulatedHost(name="h", uv_packages={"PyYAML": "6.0.2"})
    await send_command(host, UvPipRemoveCommand(packages=("pyyaml",)))
    assert host.uv_packages == {}
    await send_command(host, UvPipRemoveCommand(packages=("absent",)))


async def test_uv_pip_requirements_seam_applies_the_file():
    """The requirements descriptor reads the file from the shared
    ``remote_files`` store -- pins record versions, bare names the
    placeholder, comments and blank lines skip -- an unseeded path nothing."""
    host = SimulatedHost(name="h")
    host.remote_files["/tmp/reqs.txt"] = b"six==1.16.0  # pinned\npyinfra\n\n# c\n"
    await send_command(host, UvPipRequirementsCommand(path="/tmp/reqs.txt"))
    assert host.uv_packages == {"six": "1.16.0", "pyinfra": ""}
    await send_command(
        host, UvPipRequirementsCommand(path="/tmp/missing.txt", present=False)
    )
    assert host.uv_packages == {"six": "1.16.0", "pyinfra": ""}


async def test_uv_pip_requirements_seam_removal_direction():
    host = SimulatedHost(name="h", uv_packages={"six": "1.16.0"})
    host.remote_files["/tmp/reqs.txt"] = b"six==1.16.0\n"
    await send_command(
        host, UvPipRequirementsCommand(path="/tmp/reqs.txt", present=False)
    )
    assert host.uv_packages == {}


async def test_uv_pip_seam_mutates_the_one_flat_store_whatever_the_venv():
    """The seam binds ``packages`` only, so a venv-scoped install lands in the
    single store the ``Packages`` fact serves -- whatever ``venv`` the read
    names. Two environments differing only by path are out of scope here."""
    host = SimulatedHost(name="h")
    await send_command(
        host, UvPipInstallCommand(packages=("six==1.16.0",), venv="/srv/app/.venv")
    )
    assert host.uv_packages == {"six": "1.16.0"}
    assert await Packages.process(host, "/srv/app/.venv") == [_entry("six", "1.16.0")]
    assert await Packages.process(host) == [_entry("six", "1.16.0")]


async def test_uv_python_install_seam_adds_a_managed_interpreter():
    """The install mutator lands a ``managed=True`` record so the ``Pythons``
    fact flags it and ``uv.pythons`` counts it installed by default."""
    host = SimulatedHost(name="h")
    await send_command(host, UvPythonInstallCommand(version="3.13"))
    entries = await Pythons.process(host)
    assert any(e["version"] == "3.13" and e["managed"] for e in entries)


async def test_uv_python_remove_seam_drops_matching_versions():
    """A partial request drops every interpreter it satisfies through the
    operation's own dotted-component rule."""
    host = SimulatedHost(
        name="h",
        uv_pythons={
            "/p/a": _managed("3.13.4", "/p/a"),
            "/p/b": _managed("3.13.5", "/p/b"),
            "/p/c": _managed("3.12.8", "/p/c"),
        },
    )
    await send_command(host, UvPythonRemoveCommand(version="3.13"))
    assert await Pythons.process(host) == [_python("cpython-3.12.8", "3.12.8", "/p/c", managed=True)]


async def test_uv_tool_install_and_remove_seam():
    """The install mutator records the tool (pin records a version, bare name
    ``None``), removal drops it case-insensitively."""
    host = SimulatedHost(name="h")
    await send_command(host, UvToolInstallCommand(tool="ruff==0.8.4"))
    assert host.uv_tools["ruff"]["version"] == "0.8.4"
    await send_command(host, UvToolInstallCommand(tool="LocalTool"))
    assert host.uv_tools["LocalTool"]["version"] is None
    await send_command(host, UvToolRemoveCommand(name="RUFF"))
    assert "ruff" not in host.uv_tools
    assert "LocalTool" in host.uv_tools


async def test_uv_tool_seam_records_presence_not_the_environment():
    """``--python``/``--with`` describe the tool environment, which the seam does
    not model: the mutator still just records the tool at its pinned version."""
    host = SimulatedHost(name="h")
    await send_command(
        host,
        UvToolInstallCommand(
            tool="pyinfra==3.5.1", python="3.12", with_packages=("ansible-core",)
        ),
    )
    assert host.uv_tools["pyinfra"]["version"] == "3.5.1"
    assert [entry["name"] for entry in await Tools.process(host)] == ["pyinfra"]


async def test_uv_tool_upgrade_seams_leave_state():
    """Upgrade has no newer-version metadata to apply, so both the per-tool
    upgrade and the upgrade-all no-op leave the tools store untouched."""
    host = SimulatedHost(name="h", uv_tools={"ruff": {"version": "0.8.4"}})
    await send_command(host, UvToolUpgradeCommand(tool="ruff"))
    await send_command(host, UvToolUpgradeAllCommand())
    assert host.uv_tools == {"ruff": {"version": "0.8.4"}}


async def test_uv_tool_update_shell_seam_extends_path():
    """The update-shell mutator appends the resolved ``tool_bin_dir`` to the
    login ``PATH`` (no duplicate) so ``uv.tools_update_shell`` converges."""
    host = SimulatedHost(
        name="h",
        uv_install={"tool_bin_dir": "/home/u/.local/bin"},
        os_env={"PATH": "/usr/bin:/bin"},
    )
    await send_command(host, UvToolUpdateShellCommand())
    assert host.os_env["PATH"] == "/usr/bin:/bin:/home/u/.local/bin"
    await send_command(host, UvToolUpdateShellCommand())
    assert host.os_env["PATH"].count("/home/u/.local/bin") == 1


async def test_uv_venv_seam_adds_and_removes_path():
    """``send_command`` maintains the ``uv_venvs`` set the ``_VenvExists``
    probe reads, so a repeat ``uv.venv`` is a no-op."""
    host = SimulatedHost(name="h")
    await send_command(host, UvVenvCreateCommand(path="/srv/app/.venv"))
    assert host.uv_venvs == {"/srv/app/.venv"}
    await send_command(host, UvVenvRemoveCommand(path="/srv/app/.venv"))
    assert host.uv_venvs == set()
    await send_command(host, UvVenvRemoveCommand(path="/srv/gone"))


# --- Operation: end to end -----------------------------------------------------


async def test_uv_packages_end_to_end_install_then_noop():
    """Through run_prepare/run_apply: the pinned install runs under sudo and
    renders in the log; a second prepare sees it installed and plans no
    change."""

    def install_deploy(host):
        yield op(
            uv.packages,
            name="Install six",
            packages=("six==1.16.0",),
            sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], install_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (UvPipInstallCommand(packages=("six==1.16.0",)),)
    assert "$ sudo uv pip install --no-progress six==1.16.0" in format_log(report)
    assert host.uv_packages == {"six": "1.16.0"}
    _runtimes, plans2 = await run_prepare([host], install_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "all packages present"


async def test_uv_packages_end_to_end_install_then_remove_cycle():
    """One deploy installs then removes: the removal re-materializes against
    the mutated state and runs, emptying the store."""

    def cycle_deploy(host):
        yield op(uv.packages, name="Install six", packages=("six==1.16.0",))
        yield op(
            uv.packages,
            name="Remove six",
            packages=("six==1.16.0",),
            present=False,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], cycle_deploy)
    assert plans["h"][0].changed
    assert not plans["h"][1].changed  # prepare reads pre-deployment state
    report = await run_apply(runtimes, plans=plans)
    log = format_log(report)
    assert "$ uv pip install --no-progress six==1.16.0" in log
    assert "$ uv pip uninstall --no-progress six" in log
    assert host.uv_packages == {}


async def test_uv_venv_end_to_end_create_then_noop():
    """A venv is created on apply and a second prepare observes it via the
    ``_VenvExists`` seam and plans no change."""

    def venv_deploy(host):
        yield op(uv.venv, name="App venv", path="/srv/app/.venv")

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], venv_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    assert "$ uv venv /srv/app/.venv" in format_log(report)
    assert host.uv_venvs == {"/srv/app/.venv"}
    _runtimes, plans2 = await run_prepare([host], venv_deploy)
    assert not plans2["h"][0].changed


async def test_uv_tools_update_shell_end_to_end_converges():
    """After the apply runs update-shell, the seam has put the bin dir on the
    PATH, so a second prepare is a no-op -- the operation converges."""

    def shell_deploy(host):
        yield op(uv.tools_update_shell, name="Expose uv tools")

    host = SimulatedHost(
        name="h",
        uv_install={"tool_bin_dir": "/home/u/.local/bin"},
        os_env={"PATH": "/usr/bin:/bin"},
    )
    runtimes, plans = await run_prepare([host], shell_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    assert "$ uv tool update-shell" in format_log(report)
    _runtimes, plans2 = await run_prepare([host], shell_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "/home/u/.local/bin is already on the PATH"


async def test_uv_tools_end_to_end_install_then_remove_cycle():
    """A per-tool install/remove cycle leaves no tool behind, and the tools
    log shows one ``uv tool`` shell per request."""

    def cycle_deploy(host):
        yield op(uv.tools, name="Install ruff", tools=("ruff",))
        yield op(uv.tools, name="Remove ruff", tools=("ruff",), present=False)

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], cycle_deploy)
    report = await run_apply(runtimes, plans=plans)
    log = format_log(report)
    assert "$ uv tool install --no-progress ruff" in log
    assert "$ uv tool uninstall --no-progress ruff" in log
    assert host.uv_tools == {}


async def test_uv_dry_run_plans_without_sending():
    """``dry=True`` stops after prepare: the plan renders but nothing runs,
    so the host's simulated state stays untouched."""

    def install_deploy(host):
        yield op(uv.packages, name="Install six", packages=("six",))

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "family": "Ubuntu", "connection": {}}],
        install_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    # The plan renders the request name and reason, not the shell; the
    # command descriptor carries the byte-exact shell.
    assert "Install six" in format_plan(plans)
    assert planned.commands == (UvPipInstallCommand(packages=("six",)),)
    assert planned.commands[0].shell() == "uv pip install --no-progress six"
    assert host.uv_packages == {}


async def test_uv_packages_end_to_end_scopes_to_a_named_venv():
    """The environment names the planned shell and the applied log, and the run
    converges: the apply mutates the store the second prepare reads back through
    the same ``--python``."""

    def scoped_deploy(host):
        yield op(
            uv.packages,
            name="Install six in the app venv",
            packages=("six==1.16.0",),
            venv="/srv/app/.venv",
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], scoped_deploy)
    assert plans["h"][0].commands == (
        UvPipInstallCommand(packages=("six==1.16.0",), venv="/srv/app/.venv"),
    )
    report = await run_apply(runtimes, plans=plans)
    assert (
        "$ uv pip install --no-progress --python /srv/app/.venv six==1.16.0"
        in format_log(report)
    )
    _runtimes, plans2 = await run_prepare([host], scoped_deploy)
    assert not plans2["h"][0].changed


async def test_uv_tools_end_to_end_declare_an_isolated_environment():
    """A tool's interpreter and extra requirements reach the log, so the run
    states which environment it built for the tool."""

    def isolated_deploy(host):
        yield op(
            uv.tools,
            name="Install pyinfra",
            tools=("pyinfra==3.5.1",),
            python="3.12",
            with_packages=("ansible-core",),
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], isolated_deploy)
    report = await run_apply(runtimes, plans=plans)
    assert (
        "$ uv tool install --no-progress --python 3.12 --with ansible-core "
        "pyinfra==3.5.1" in format_log(report)
    )
    assert host.uv_tools["pyinfra"]["version"] == "3.5.1"
