# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the go module: the ``GoPackages`` fact.

Covers ``reemote.modules.go.go.GoPackages`` -- the ``go install`` binaries
as ``GoPackageInfo`` entries -- both over the real-transport path (a
``CannedShellHost`` returning canned ``go version -m`` output, including
pyinfra's own fact fixture verbatim: the compound command string, the
``(devel)`` skip, the ``dep``-row and header-line ignores) and through the
``SimulatedHost`` seam, plus the ``gather_fact`` fan-out and the exports.

``asyncio_mode = "auto"`` lets the async tests run without decorators.
"""

from reemote.drivers import gather_fact
from reemote.hosts import Host
from reemote.modules import go
from reemote.modules.go.go import GoPackageInfo, GoPackages
from tests.simulated_host import SimulatedHost


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell``, recording the shell it was
    given so the command parity check can assert on it."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout
        self.last_shell: str | None = None

    async def run_shell(self, shell: str) -> str:
        self.last_shell = shell
        return self._stdout


def _entry(path: str, version: str) -> GoPackageInfo:
    """One installed ``GoPackageInfo`` entry, spelled once per test."""
    return {"path": path, "version": version}


# --- Fact: command parity ------------------------------------------------------

# pyinfra's own fact fixture (tests/facts/go.GoPackages/packages.yaml): the
# command string it asserts on, verbatim.
PYINFRA_COMMAND = (
    'BINDIR=$(go env GOBIN); [ -z "$BINDIR" ] && '
    'BINDIR="$(go env GOPATH)/bin"; ! test -d "$BINDIR" || '
    'find "$BINDIR" -maxdepth 1 -type f -perm -u+x '
    "-exec go version -m {} + 2>/dev/null"
)


def test_go_packages_command_is_pyinfra_verbatim():
    """The fact issues pyinfra's compound command unchanged: resolve the go
    bin directory (GOBIN, else $GOPATH/bin), list its executables through
    ``go version -m`` only when the directory exists."""
    assert GoPackages.COMMAND == PYINFRA_COMMAND


# --- Fact: real-transport parsing (pyinfra fixture parity) ---------------------

# pyinfra's fact fixture output verbatim: a versioned ``go install`` binary,
# a ``dep`` row that must not contribute, and a ``(devel)`` ``go build``
# binary that must be skipped.
PYINFRA_OUTPUT = (
    "/home/user/go/bin/bitmagnet: go1.22.0\n"
    "\tpath\tgithub.com/bitmagnet-io/bitmagnet\n"
    "\tmod\tgithub.com/bitmagnet-io/bitmagnet\tv0.9.0\th1:aaaa\n"
    "\tdep\tgithub.com/example/lib\tv0.1.0\th1:bbbb\n"
    "/home/user/go/bin/devbin: go1.22.0\n"
    "\tpath\tcommand-line-arguments\n"
    "\tmod\tgithub.com/example/dev\t(devel)"
)


async def test_go_packages_parses_pyinfras_fixture():
    """Over the real-transport path pyinfra's own fixture yields its
    documented example result: the one versioned install, with the ``dep``
    row, the ``(devel)`` binary and the per-binary headers all contributing
    nothing -- the answer pyinfra models as ``{"github.com/...": {"v0.9.0"}}``
    is here one ``GoPackageInfo`` entry."""
    host = CannedShellHost(PYINFRA_OUTPUT + "\n")
    assert await GoPackages.process(host) == [
        _entry("github.com/bitmagnet-io/bitmagnet", "v0.9.0")
    ]
    assert host.last_shell == GoPackages.COMMAND


async def test_go_packages_sorts_dedups_and_keeps_every_version():
    """Several binaries sort by ``(path, version)``, duplicate rows collapse,
    and one path built at several versions contributes one entry per
    version (the ``gem`` version-list precedent, not npm's one-per-name)."""
    output = (
        "/go/bin/z: go1.22.0\n"
        "\tpath\texample.com/z\n"
        "\tmod\texample.com/z\tv1.0.0\th1:z\n"
        "/go/bin/a-old: go1.22.0\n"
        "\tpath\texample.com/a\n"
        "\tmod\texample.com/a\tv0.10.0\th1:a2\n"
        "/go/bin/a: go1.22.0\n"
        "\tpath\texample.com/a\n"
        "\tmod\texample.com/a\tv0.9.0\th1:a1\n"
        "/go/bin/a-copy: go1.22.0\n"
        "\tpath\texample.com/a\n"
        "\tmod\texample.com/a\tv0.9.0\th1:a1"
    )
    assert await GoPackages.process(CannedShellHost(output)) == [
        _entry("example.com/a", "v0.10.0"),
        _entry("example.com/a", "v0.9.0"),
        _entry("example.com/z", "v1.0.0"),
    ]


async def test_go_packages_skips_devel_and_short_mod_rows():
    """A ``(devel)`` version is ``go build`` output, not an install, and a
    ``mod`` row with no version column cannot report one; neither
    contributes."""
    output = (
        "/go/bin/dev: go1.22.0\n"
        "\tpath\texample.com/dev\n"
        "\tmod\texample.com/dev\t(devel)\t\n"
        "/go/bin/odd: go1.22.0\n"
        "\tpath\texample.com/odd\n"
        "\tmod\texample.com/odd\n"
        "/go/bin/kept: go1.22.0\n"
        "\tpath\texample.com/kept\n"
        "\tmod\texample.com/kept\tv1.2.3\th1:k"
    )
    # A trailing empty column still skips the ``(devel)`` row by its version
    # value, and a complete ``mod`` row records normally.
    assert await GoPackages.process(CannedShellHost(output)) == [
        _entry("example.com/kept", "v1.2.3")
    ]


async def test_go_packages_mod_row_needs_a_path_row_first():
    """A ``mod`` row attaches only to the ``path`` row of the same binary: a
    ``mod`` with no preceding ``path`` records nothing, and a per-binary
    header line closes the current path, so a ``mod`` never attaches to
    the previous binary's ``path``."""
    orphan_mod = "/go/bin/a: go1.22.0\n\tmod\texample.com/a\tv1.0.0\th1:a"
    assert await GoPackages.process(CannedShellHost(orphan_mod)) == []
    split_across_binaries = (
        "/go/bin/a: go1.22.0\n"
        "\tpath\texample.com/a\n"
        "/go/bin/b: go1.22.0\n"
        "\tmod\texample.com/b\tv1.0.0\th1:b"
    )
    assert await GoPackages.process(CannedShellHost(split_across_binaries)) == []


async def test_go_packages_ignores_build_rows_blank_lines_and_noise():
    """``build``/``buildInformation`` rows, blank lines and a stray warning
    line contribute nothing; the one real install still reads out."""
    output = (
        "\n"
        "/go/bin/tool: go1.22.0\n"
        "\tpath\texample.com/tool\n"
        "\tmod\texample.com/tool\tv0.1.0\th1:t\n"
        "\tbuild\tvcs=git\n"
        "\tbuildInformation\t\n"
        "\n"
        "find: 'go': No such file or directory"
    )
    assert await GoPackages.process(CannedShellHost(output)) == [
        _entry("example.com/tool", "v0.1.0")
    ]


async def test_go_packages_unparsable_output_reads_empty():
    """The empty bin directory (no output at all), a bare header and output
    with no tab rows all read as ``[]`` -- the counterpart of pyinfra's
    ``default = dict`` the dropped ``requires_command`` gate produced."""
    assert GoPackages.parse([]) == []
    assert await GoPackages.process(CannedShellHost("")) == []
    assert await GoPackages.process(CannedShellHost("/go/bin/x: go1.22.0\n")) == []
    assert await GoPackages.process(CannedShellHost("go: command not found\n")) == []


# --- Fact: SimulatedHost seam ---------------------------------------------------


async def test_go_packages_reads_the_simulated_seam():
    """A SimulatedHost answers from its ``go_packages`` store, bypassing the
    shell; the default empty store reads as ``[]``."""
    host = SimulatedHost(
        name="h", go_packages={"github.com/bitmagnet-io/bitmagnet": ["v0.9.0"]}
    )
    assert await GoPackages.process(host) == [
        _entry("github.com/bitmagnet-io/bitmagnet", "v0.9.0")
    ]
    assert await GoPackages.process(SimulatedHost(name="h")) == []


async def test_go_packages_seam_reports_every_version():
    """One path installed at several versions projects one entry per
    version, sorted and de-duplicated."""
    host = SimulatedHost(
        name="h",
        go_packages={"example.com/a": ["v0.9.0", "v0.10.0", "v0.9.0"]},
    )
    assert await GoPackages.process(host) == [
        _entry("example.com/a", "v0.10.0"),
        _entry("example.com/a", "v0.9.0"),
    ]


async def test_go_packages_seam_returns_copies():
    """Mutating a returned entry (or the list) never touches the simulated
    state, so the seam hands back freshly built dicts."""
    host = SimulatedHost(
        name="h",
        go_packages={"example.com/a": ["v1.0.0"], "example.com/b": ["v2.0.0"]},
    )
    entries = await GoPackages.process(host)
    entries[0]["version"] = "tampered"
    entries.append(_entry("example.com/tampered", "v0"))
    assert await GoPackages.process(host) == [
        _entry("example.com/a", "v1.0.0"),
        _entry("example.com/b", "v2.0.0"),
    ]
    assert host.go_packages == {
        "example.com/a": ["v1.0.0"],
        "example.com/b": ["v2.0.0"],
    }


async def test_gather_fact_go_packages_fans_out():
    """Across a simulated fleet each host answers from its own seam store,
    keyed by host name."""
    host1 = SimulatedHost(
        name="web1", go_packages={"example.com/tool": ["v1.0.0"]}
    )
    host2 = SimulatedHost(name="web2")
    answers = await gather_fact([host1, host2], GoPackages)
    assert answers == {
        "web1": [_entry("example.com/tool", "v1.0.0")],
        "web2": [],
    }


# --- Exports --------------------------------------------------------------------


def test_go_module_exports():
    """The package re-exports the fact and its entry type, and the
    top-level package makes ``from reemote import GoPackages`` work."""
    assert go.GoPackages is GoPackages
    assert go.GoPackageInfo is GoPackageInfo
    import reemote

    assert reemote.GoPackages is GoPackages
    assert reemote.GoPackageInfo is GoPackageInfo
