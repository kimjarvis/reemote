# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the go module: the six facts and ``go.packages``.

Covers ``reemote.modules.go.go.GoPackages`` -- the ``go install`` binaries
as ``GoPackageInfo`` entries -- both over the real-transport path (a
``CannedShellHost`` returning canned ``go version -m`` output, including
pyinfra's own fact fixture verbatim: the compound command string, the
``(devel)`` skip, the ``dep``-row and header-line ignores) and through the
``SimulatedHost`` seam, plus the ``gather_fact`` fan-out and the exports.

Covers the five toolchain facts -- ``GoVersion``, ``GoEnv``,
``GoInstalledSDKs``, ``GoModuleInfo`` and ``GoBinaryBuildInfo`` -- the same
way: the exact rendered shell (each self-guarded so a host without go reads
empty output rather than exit 127), pure ``parse`` fixtures taken from real
go output, the parameterized command builders, and each seam store
(``go_version``/``go_env``/``go_sdks``/``go_modules``/``go_binaries``),
including copying and the empty/unseeded projections.

The ``go.packages`` operation -- a pyinfra port reading ``GoPackages`` -- is
exercised over its two command descriptors (``GoInstallCommand`` /
``GoRemoveCommand``), their rendering (``go install <pkg>@<version>`` and
the BINDIR-resolving ``rm -f "$BINDIR"/<binary>`` with the Semantic-Import-
Versioning ``/vN`` strip), the shared ``<pkg>=<version>`` pin translated to
go's ``@`` token, pyinfra's eleven operation fixtures verbatim, the
name-based (pin-agnostic) removal, the documented non-idempotent ``latest``
path, the ``SimulatedHost`` seam dispatch, and end to end through ``run`` /
``run_prepare`` + ``run_apply``.

``asyncio_mode = "auto"`` lets the async tests run without decorators.
"""

import json
import shlex

from reemote.core import FactCache, OpContext, op
from reemote.drivers import (
    command_shell,
    format_log,
    gather_fact,
    run,
    run_apply,
    run_prepare,
    send_command,
)
from reemote.hosts import Host
from reemote.modules import go
from reemote.modules.go.go import (
    GoBinaryBuildInfo,
    GoBuildCommand,
    GoBuildInfo,
    GoCleanCommand,
    GoEnv,
    GoEnvSetCommand,
    GoEnvUnsetCommand,
    GoInstallCommand,
    GoInstalledSDKs,
    GoModInfo,
    GoModuleInfo,
    GoPackageInfo,
    GoPackages,
    GoRemoveCommand,
    GoVersion,
    GoVersionInfo,
    _binary_name,
    _go_package_spec,
    _go_split_spec,
    _split_version,
)
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


# --- Operation: shared helpers -------------------------------------------------


def test_split_version_splits_at_the_last_equals():
    """reemote's one user-facing pin spelling, shared with apt/dnf/apk/npm/
    gem: the split is at the *last* ``=``, so a version carrying one stays
    whole."""
    assert _split_version("github.com/foo/bar") == ("github.com/foo/bar", None)
    assert _split_version("github.com/foo/bar=v1.2.3") == ("github.com/foo/bar", "v1.2.3")
    assert _split_version("odd=1=2") == ("odd=1", "2")


def test_go_package_spec_translates_the_pin_to_go_grammar():
    """The request's ``=`` becomes go's ``@``; an unpinned name and the
    special ``latest`` pin both become go's ``@latest`` token (pyinfra never
    installs a bare path)."""
    assert _go_package_spec("github.com/foo/bar=v1.2.3") == "github.com/foo/bar@v1.2.3"
    assert _go_package_spec("github.com/foo/bar") == "github.com/foo/bar@latest"
    assert _go_package_spec("github.com/foo/bar=latest") == "github.com/foo/bar@latest"


def test_go_split_spec_inverts_the_translation():
    """The seam's reader splits at the *first* ``@`` -- pyinfra's
    ``partition("@")`` rule -- because a go import path carries no ``@``;
    a bare path yields no version."""
    assert _go_split_spec("github.com/foo/bar@v1.2.3") == ("github.com/foo/bar", "v1.2.3")
    assert _go_split_spec("github.com/foo/bar") == ("github.com/foo/bar", None)


def test_binary_name_strips_the_major_version_suffix():
    """Go's Semantic Import Versioning: a trailing ``/v<N>`` is not part of
    the binary name, and the command-less ``cmd/...`` shape just takes the
    last segment (pyinfra's builder, verbatim)."""
    assert _binary_name("github.com/foo/bar/cmd/mytool") == "mytool"
    assert _binary_name("honnef.co/go/tools/cmd/staticcheck") == "staticcheck"
    assert _binary_name("github.com/foo/bar/v2") == "bar"
    assert _binary_name("tool") == "tool"
    # A lone ``v2`` segment is not a suffix *of another segment*: len < 2
    # keeps the last part as the name.
    assert _binary_name("v2") == "v2"


# --- Operation: descriptor rendering -------------------------------------------


def test_go_install_command_shells_as_go_install():
    """Tokens pass through ``go install`` in order; safe module paths need no
    quoting, so the render matches pyinfra's command verbatim."""
    cmd = GoInstallCommand(
        packages=("github.com/bitmagnet-io/bitmagnet@latest",)
    )
    assert cmd.shell() == "go install github.com/bitmagnet-io/bitmagnet@latest"
    batched = GoInstallCommand(packages=("a.co/x@v1.0.0", "b.co/y@latest"))
    assert batched.shell() == "go install a.co/x@v1.0.0 b.co/y@latest"


def test_go_install_command_omits_empty_lists():
    """A command with no tokens stays bare, so no trailing space reaches the
    log."""
    assert GoInstallCommand(packages=()).shell() == "go install"


def test_go_install_command_quotes_a_hostile_token():
    """A path carrying a metacharacter is ``shlex``-quoted, so it never
    reaches the remote shell unescaped."""
    hostile = "evil.co/x; rm -rf /"
    assert GoInstallCommand(packages=(hostile,)).shell() == (
        f"go install {shlex.quote(hostile)}"
    )


def test_go_remove_command_shells_as_the_bindir_removal():
    """The removal is pyinfra's exact compound shape: resolve the go bin
    directory, then ``rm -f`` each binary under it."""
    cmd = GoRemoveCommand(packages=("github.com/foo/bar/cmd/mytool",))
    assert cmd.shell() == (
        'BINDIR=$(go env GOBIN); [ -z "$BINDIR" ] && '
        'BINDIR="$(go env GOPATH)/bin"; rm -f "$BINDIR"/mytool'
    )
    # A major-version suffix is stripped from the file, not kept.
    assert GoRemoveCommand(packages=("github.com/foo/bar/v2",)).shell() == (
        'BINDIR=$(go env GOBIN); [ -z "$BINDIR" ] && '
        'BINDIR="$(go env GOPATH)/bin"; rm -f "$BINDIR"/bar'
    )


def test_go_remove_command_omits_empty_lists():
    """With no paths the command still stands bare (``rm -f`` alone), never
    with a dangling space."""
    assert GoRemoveCommand(packages=()).shell() == (
        'BINDIR=$(go env GOBIN); [ -z "$BINDIR" ] && '
        'BINDIR="$(go env GOPATH)/bin"; rm -f'
    )


def test_go_commands_are_elevated_by_sudo():
    """The commands need no root (they act on the connecting user's go
    tree), but the driver's elevation plumbing works the same when a request
    is flagged (the run-as user comes from the inventory ``privilege``,
    never the operation)."""
    cmd = GoInstallCommand(packages=("a.co/x@latest",))
    assert command_shell(cmd) == "go install a.co/x@latest"
    assert command_shell(cmd, sudo=True) == "sudo go install a.co/x@latest"
    remove = GoRemoveCommand(packages=("a.co/x",))
    assert command_shell(remove, su=True) == f"su -c {shlex.quote(remove.shell())}"


# --- Operation: idempotence (pyinfra's operation fixtures verbatim) ------------


def _ctx(host: SimulatedHost) -> OpContext:
    """A prepare-phase context over a simulated host."""
    return OpContext(host=host, facts=FactCache(host, phase="prepare"))


async def test_go_packages_installs_missing_package():
    """pyinfra's ``add_packages``: an empty store turns the request into one
    ``go install`` with go's ``@latest`` token appended."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.packages(
            ctx, packages=("github.com/bitmagnet-io/bitmagnet",)
        )
    ]
    assert ctx.changed
    assert ctx.reason == "1 package(s) to install"
    assert commands == [
        GoInstallCommand(packages=("github.com/bitmagnet-io/bitmagnet@latest",))
    ]


async def test_go_packages_installs_a_pinned_package():
    """pyinfra's ``add_versioned_packages``: the pin is translated to go's
    ``@<version>`` token."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.packages(
            ctx, packages=("github.com/bitmagnet-io/bitmagnet=v0.10.0",)
        )
    ]
    assert commands == [
        GoInstallCommand(packages=("github.com/bitmagnet-io/bitmagnet@v0.10.0",))
    ]


async def test_go_packages_no_change_when_installed():
    """pyinfra's ``already_installed``: an unpinned request is present when
    the path has *any* installed version."""
    host = SimulatedHost(name="h", go_packages={"github.com/foo/bar": ["v1.2.3"]})
    ctx = _ctx(host)
    commands = [c async for c in go.packages(ctx, packages=("github.com/foo/bar",))]
    assert not ctx.changed
    assert ctx.reason == "all packages present"  # recorded even when unchanged
    assert commands == []


async def test_go_packages_latest_pin_counts_as_unpinned():
    """pyinfra's ``explicit_latest_already_installed``: ``@latest`` is the
    install-time pseudo-version, so an explicit ``=latest`` pin is present
    like no pin at all."""
    host = SimulatedHost(name="h", go_packages={"github.com/foo/bar": ["v1.2.3"]})
    ctx = _ctx(host)
    commands = [
        c async for c in go.packages(ctx, packages=("github.com/foo/bar=latest",))
    ]
    assert not ctx.changed
    assert commands == []


async def test_go_packages_pin_present_only_on_exact_version():
    """A pinned ``<pkg>=<version>`` is present only when that *exact*
    version is installed (the ``ensure_packages`` rule apt/npm/gem follow);
    the other pin installs as its own token."""
    host = SimulatedHost(name="h", go_packages={"github.com/foo/bar": ["v1.2.3"]})
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.packages(ctx, packages=("github.com/foo/bar=v1.2.3",))
    ]
    assert not ctx.changed
    assert commands == []
    commands = [
        c
        async for c in go.packages(ctx, packages=("github.com/foo/bar=v2.0.0",))
    ]
    assert commands == [
        GoInstallCommand(packages=("github.com/foo/bar@v2.0.0",))
    ]


async def test_go_packages_accepts_a_bare_string():
    """pyinfra's ``single_package_as_string``: ``packages="<path>"`` is
    shorthand for a one-item list."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.packages(ctx, packages="github.com/foo/bar")
    ]
    assert commands == [GoInstallCommand(packages=("github.com/foo/bar@latest",))]


async def test_go_packages_none_is_a_noop():
    """pyinfra returns without a command when ``packages`` is None."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [c async for c in go.packages(ctx)]
    assert not ctx.changed
    assert ctx.reason == "no packages requested"
    assert commands == []


# --- Operation: latest (pyinfra's documented non-idempotence) -------------------


async def test_go_packages_latest_not_installed_installs():
    """pyinfra's ``latest_not_installed``: with nothing installed the
    request is an install, not an upgrade -- one command."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.packages(
            ctx, packages=("github.com/foo/bar",), latest=True
        )
    ]
    assert commands == [GoInstallCommand(packages=("github.com/foo/bar@latest",))]
    assert ctx.reason == "1 package(s) to install"


async def test_go_packages_latest_upgrades_installed_unpinned():
    """pyinfra's ``upgrade_packages``: an installed, unversioned package
    re-runs ``go install <pkg>@latest`` even at the newest version -- the
    documented non-idempotence (no registry to compare against)."""
    host = SimulatedHost(name="h", go_packages={"github.com/foo/bar": ["v1.2.3"]})
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.packages(
            ctx, packages=("github.com/foo/bar",), latest=True
        )
    ]
    assert ctx.changed
    assert ctx.reason == "1 package(s) to upgrade"
    assert commands == [GoInstallCommand(packages=("github.com/foo/bar@latest",))]


async def test_go_packages_latest_batches_install_before_upgrade():
    """pyinfra emits the install and upgrade commands separately, in that
    order; a pinned request joins the install batch when missing but is
    never an upgrade candidate."""
    host = SimulatedHost(name="h", go_packages={"github.com/foo/bar": ["v1.2.3"]})
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.packages(
            ctx,
            packages=("github.com/foo/bar", "github.com/new/tool", "pin/co/x=v1"),
            latest=True,
        )
    ]
    assert commands == [
        GoInstallCommand(packages=("github.com/new/tool@latest", "pin/co/x@v1")),
        GoInstallCommand(packages=("github.com/foo/bar@latest",)),
    ]
    assert ctx.reason == "2 package(s) to install; 1 package(s) to upgrade"


# --- Operation: removal ----------------------------------------------------------


async def test_go_packages_removes_installed_packages():
    """pyinfra's ``remove_packages``/``remove_multiple_packages``: one
    ``rm -f`` command over the binary names, in request order."""
    host = SimulatedHost(
        name="h",
        go_packages={
            "github.com/foo/bar/cmd/mytool": ["v1.2.3"],
            "honnef.co/go/tools/cmd/staticcheck": ["v0.4.0"],
        },
    )
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.packages(
            ctx,
            packages=("github.com/foo/bar/cmd/mytool", "honnef.co/go/tools/cmd/staticcheck"),
            present=False,
        )
    ]
    assert ctx.changed
    assert ctx.reason == "2 package(s) to remove"
    assert commands[0].shell() == (
        'BINDIR=$(go env GOBIN); [ -z "$BINDIR" ] && '
        'BINDIR="$(go env GOPATH)/bin"; '
        'rm -f "$BINDIR"/mytool "$BINDIR"/staticcheck'
    )


async def test_go_packages_removal_is_name_based_not_version_aware():
    """pyinfra removes on *name* presence: a binary carries no per-version
    state, so a pin cannot select or spare a version (deliberately unlike
    the version-aware apt/gem removal)."""
    host = SimulatedHost(name="h", go_packages={"github.com/foo/bar": ["v1.0.0"]})
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.packages(
            ctx, packages=("github.com/foo/bar=v9.9.9",), present=False
        )
    ]
    assert ctx.changed
    assert commands == [GoRemoveCommand(packages=("github.com/foo/bar",))]


async def test_go_packages_removal_of_an_absent_package_is_a_noop():
    """pyinfra's ``noop_remove_missing``."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [
        c async for c in go.packages(ctx, packages=("github.com/foo/bar",), present=False)
    ]
    assert not ctx.changed
    assert ctx.reason == "no packages to remove"
    assert commands == []


async def test_go_packages_removal_dedups_a_repeated_path():
    """A path requested twice contributes one ``rm -f`` token, in the order
    the request spelled it."""
    host = SimulatedHost(name="h", go_packages={"github.com/foo/bar": ["v1.0.0"]})
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.packages(
            ctx,
            packages=("github.com/foo/bar", "github.com/foo/bar=v1.0.0"),
            present=False,
        )
    ]
    assert commands == [GoRemoveCommand(packages=("github.com/foo/bar",))]
    assert ctx.reason == "1 package(s) to remove"


async def test_go_packages_absorbs_the_pyinfra_globals():
    """``**kwargs`` takes pyinfra's global arguments and any unknown keyword
    with no effect, like every sibling module."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.packages(
            ctx, packages=("a.co/x",), untar=False, clean=False, quote=True
        )
    ]
    assert commands == [GoInstallCommand(packages=("a.co/x@latest",))]


# --- Operation: SimulatedHost seam dispatch ---------------------------------------


async def test_go_install_seam_records_versions():
    """``send_command`` maps ``GoInstallCommand`` onto the store: a pin
    records exactly that version (replacing any older one, as go re-links
    the single binary) and ``@latest`` gets the ``[""]`` placeholder."""
    host = SimulatedHost(name="h", go_packages={"github.com/foo/bar": ["v1.0.0"]})
    await send_command(
        host,
        GoInstallCommand(
            packages=("github.com/foo/bar@v2.0.0", "github.com/new/tool@latest")
        ),
    )
    assert host.go_packages == {
        "github.com/foo/bar": ["v2.0.0"],
        "github.com/new/tool": [""],
    }


async def test_go_install_seam_latest_keeps_recorded_version():
    """An installed path re-served ``@latest`` keeps its recorded version:
    the simulation has no registry to resolve a newer one."""
    host = SimulatedHost(name="h", go_packages={"github.com/foo/bar": ["v1.0.0"]})
    await send_command(
        host, GoInstallCommand(packages=("github.com/foo/bar@latest",))
    )
    assert host.go_packages == {"github.com/foo/bar": ["v1.0.0"]}


async def test_go_remove_seam_drops_the_paths():
    """A removal pops each requested path; an absent path is tolerated."""
    host = SimulatedHost(
        name="h",
        go_packages={"github.com/foo/bar": ["v1.0.0"], "a.co/tool": ["v2"]},
    )
    await send_command(host, GoRemoveCommand(packages=("github.com/foo/bar",)))
    assert host.go_packages == {"a.co/tool": ["v2"]}
    await send_command(host, GoRemoveCommand(packages=("absent.co/x",)))


# --- Operation: end to end ----------------------------------------------------------

# One inventory entry, so ``run`` materializes one simulated host through the
# injected factory and hands it back for the state assertions.
INVENTORY = [
    {"host": "h", "family": "Ubuntu", "connection": {"address": "127.0.0.1", "username": "u"}}
]


def install_deploy(host):
    yield op(
        go.packages,
        name="Install go tools",
        packages=("github.com/foo/bar=v1.2.3", "honnef.co/go/tools/cmd/staticcheck"),
    )


async def test_go_packages_end_to_end_install_then_noop():
    """The dry run plans the batched install and sends nothing, the applied
    run converges the host, and the second pass reports no change."""
    host = SimulatedHost(name="h")

    def factory(entry: dict) -> SimulatedHost:
        return host

    plans, dry_report, _hosts = await run(
        INVENTORY, install_deploy, host_factory=factory, dry=True
    )
    assert dry_report is None
    assert plans["h"][0].changed
    assert plans["h"][0].reason == "2 package(s) to install"
    assert host.go_packages == {}  # a dry run sends nothing

    _plans, report, _hosts = await run(INVENTORY, install_deploy, host_factory=factory)
    log = format_log(report)
    assert (
        "$ go install github.com/foo/bar@v1.2.3 "
        "honnef.co/go/tools/cmd/staticcheck@latest" in log
    )
    assert host.go_packages == {
        "github.com/foo/bar": ["v1.2.3"],
        "honnef.co/go/tools/cmd/staticcheck": [""],
    }

    plans2, report2, _hosts = await run(INVENTORY, install_deploy, host_factory=factory)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "all packages present"
    log2 = format_log(report2)
    assert "[no change: all packages present]" in log2
    assert "$ " not in log2


async def test_go_packages_end_to_end_removal_cycle():
    """One deploy installs then removes: the apply log shows both shells and
    the removal -- a fresh step with its own fact cache -- sees the install's
    mutation."""

    def cycle_deploy(host):
        yield op(go.packages, name="Install tool", packages=("github.com/foo/bar",))
        yield op(
            go.packages,
            name="Remove tool",
            packages=("github.com/foo/bar",),
            present=False,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], cycle_deploy)
    assert plans["h"][0].changed
    # Prepare reads pre-deployment state only: the still-empty store has
    # nothing to remove.
    assert not plans["h"][1].changed
    assert plans["h"][1].reason == "no packages to remove"
    report = await run_apply(runtimes, plans=plans)
    log = format_log(report)
    assert "$ go install github.com/foo/bar@latest" in log
    assert (
        '$ BINDIR=$(go env GOBIN); [ -z "$BINDIR" ] && '
        'BINDIR="$(go env GOPATH)/bin"; rm -f "$BINDIR"/bar' in log
    )
    assert host.go_packages == {}


async def test_go_packages_latest_stays_changed_across_runs():
    """The documented non-idempotence through the driver: ``latest=True``
    re-plans the upgrade even after the host converged, because nothing
    tells the operation whether a newer module version exists."""

    def update_deploy(host):
        yield op(
            go.packages,
            name="Update tools",
            packages=("github.com/foo/bar",),
            latest=True,
        )

    host = SimulatedHost(name="h", go_packages={"github.com/foo/bar": ["v1.0.0"]})

    def factory(entry: dict) -> SimulatedHost:
        return host

    plans1, _report, _hosts = await run(
        INVENTORY, update_deploy, host_factory=factory
    )
    assert plans1["h"][0].changed
    assert plans1["h"][0].reason == "1 package(s) to upgrade"
    plans2, _report2, _hosts2 = await run(
        INVENTORY, update_deploy, host_factory=factory
    )
    assert plans2["h"][0].changed


# --- Fact: GoVersion ------------------------------------------------------------


def test_go_version_command_is_self_guarded():
    """The guard replaces the design's ``requires_command`` gate: a host
    without go exits 0 with empty output -- never 127 -- and the fact reads
    absence from it."""
    assert GoVersion.COMMAND == (
        "command -v go >/dev/null 2>&1 || exit 0; "
        "go version && go env GOOS GOARCH"
    )


async def test_go_version_parses_the_real_output():
    """Fixture shaped like real ``go version``/``go env`` output (verified
    against a go 1.24 toolchain): the header's ``go``-prefixed third field
    is the stripped version, the two bare env lines are the target."""
    host = CannedShellHost(
        "go version go1.23.4 linux/amd64\nlinux\namd64\n"
    )
    assert await GoVersion.process(host) == {
        "version": "1.23.4",
        "raw": "go version go1.23.4 linux/amd64",
        "goos": "linux",
        "goarch": "amd64",
    }
    assert host.last_shell == GoVersion.COMMAND


async def test_go_version_short_output_reads_absent():
    """Fewer than three non-blank lines is no answer: the guarded empty
    read of a host without go, and stray noise, both give None (the design
    proposed ``{}``; a single-valued fact answers None, the ``Which``
    precedent)."""
    assert GoVersion.parse([]) is None
    assert await GoVersion.process(CannedShellHost("")) is None
    assert (
        await GoVersion.process(CannedShellHost("go version go1.23.4\n"))
        is None
    )


async def test_go_version_devel_build_reports_empty_version():
    """A ``devel`` toolchain header carries no ``go``-prefixed version
    field: the answer keeps the raw line and the target with an empty
    ``version``."""
    assert GoVersion.parse(
        ["go version devel linux/arm64", "linux", "arm64"]
    ) == {
        "version": "",
        "raw": "go version devel linux/arm64",
        "goos": "linux",
        "goarch": "arm64",
    }


async def test_go_version_seam_projects_the_store():
    """A seeded store copies straight out (no shell); the default empty
    store projects None -- the simulated host without go."""
    info: GoVersionInfo = {
        "version": "1.22.10",
        "raw": "go version go1.22.10 linux/amd64",
        "goos": "linux",
        "goarch": "amd64",
    }
    host = SimulatedHost(name="h", go_version=info)
    assert await GoVersion.process(host) == info
    answer = await GoVersion.process(host)
    answer["version"] = "tampered"
    assert await GoVersion.process(host) == info
    assert await GoVersion.process(SimulatedHost(name="h")) is None


# --- Fact: GoEnv ----------------------------------------------------------------


def test_go_env_command_is_the_guarded_full_listing():
    """Always the plain ``go env`` listing: the design's filtered
    ``go env <KEY>...`` form prints bare values with no ``KEY=`` prefix and
    cannot be parsed back, so a filtered read narrows the full listing in
    ``process`` instead."""
    assert GoEnv.COMMAND == "command -v go >/dev/null 2>&1 || exit 0; go env"


async def test_go_env_parses_quoted_rows():
    """Fixture shaped like real ``go env`` output: every value is
    single-quoted and the one matched outer pair is stripped; GOFLAGS
    spaces and GOPROXY commas survive untouched."""
    host = CannedShellHost(
        "GOBIN=''\n"
        "GOCACHE='/root/.cache/go-build'\n"
        "GOFLAGS='-mod=vendor -trimpath'\n"
        "GOPATH='/root/go'\n"
        "GOPROXY='https://proxy.golang.org,direct'\n"
    )
    env = await GoEnv.process(host)
    assert env == {
        "GOBIN": "",
        "GOCACHE": "/root/.cache/go-build",
        "GOFLAGS": "-mod=vendor -trimpath",
        "GOPATH": "/root/go",
        "GOPROXY": "https://proxy.golang.org,direct",
    }
    assert host.last_shell == GoEnv.COMMAND


async def test_go_env_process_filters_keys():
    """A filtered read keeps the requested order and drops unknown or unset
    keys -- an empty answer when nothing requested is configured."""
    host = CannedShellHost(
        "GOBIN=''\nGOMODCACHE='/root/go/pkg/mod'\nGOPATH='/root/go'\n"
    )
    assert await GoEnv.process(host, keys=["GOPATH", "GOBIN", "NOPE"]) == {
        "GOPATH": "/root/go",
        "GOBIN": "",
    }
    assert await GoEnv.process(host, keys=["NOPE"]) == {}


async def test_go_env_skips_noise_lines():
    """A line without ``=`` contributes nothing; an unquoted value (go
    varies its quoting) is kept verbatim."""
    assert GoEnv.parse(["# go env output", "GOVERSION=go1.24.4"]) == {
        "GOVERSION": "go1.24.4"
    }


async def test_go_env_seam_filters_and_copies():
    """The seam reads the ``go_env`` store (no shell), the default empty
    store answers {} like a host without go, a filter narrows the copy, and
    mutation never reaches simulated state."""
    host = SimulatedHost(
        name="h",
        go_env={"GOPATH": "/home/dev/go", "GOPROXY": "https://proxy.golang.org,direct"},
    )
    assert await GoEnv.process(host, keys=["GOPATH"]) == {"GOPATH": "/home/dev/go"}
    env = await GoEnv.process(host)
    env["GOPATH"] = "tampered"
    assert await GoEnv.process(host) == {
        "GOPATH": "/home/dev/go",
        "GOPROXY": "https://proxy.golang.org,direct",
    }
    assert await GoEnv.process(SimulatedHost(name="h")) == {}


# --- Fact: GoInstalledSDKs --------------------------------------------------------


def test_go_installed_sdks_command_loops_the_quoted_dirs():
    """The POSIX scan of the design's search directories -- each directory
    ``shlex``-quoted, both the ``go*/bin/go`` and ``*/bin/go`` shapes
    probed, every executable printing ``<path>TAB<version line>``."""
    command = GoInstalledSDKs.command()
    assert command.startswith(
        'for d in /usr/local /opt; do for b in "$d"/go*/bin/go "$d"/*/bin/go; do '
    )
    # The literal ``\t``/``\n`` escape pair belongs to printf's format.
    assert "printf '%s\\t%s\\n'" in command
    assert '"$("$b" version 2>/dev/null)"' in command
    assert command.endswith("done; done")
    hostile = GoInstalledSDKs.command(["/srv/weird $dir"])
    assert shlex.quote("/srv/weird $dir") in hostile


async def test_go_installed_sdks_parses_version_rows():
    """Rows map version -> path (the ``GoVersion`` strip rule on the
    printed header); a path matched by both glob shapes lands twice and
    collapses, and a row without a tab or a ``go``-prefixed version
    contributes nothing."""
    host = CannedShellHost(
        "/usr/local/go/bin/go\tgo version go1.23.4 linux/amd64\n"
        "/usr/local/go1.22.10/bin/go\tgo version go1.22.10 linux/amd64\n"
        "/opt/go/bin/go\tgo version go1.23.4 linux/amd64\n"
        "/usr/local/notgo/bin/go\tgo version devel linux/amd64\n"
        "garbage without a tab"
    )
    assert await GoInstalledSDKs.process(host) == {
        "1.22.10": "/usr/local/go1.22.10/bin/go",
        "1.23.4": "/opt/go/bin/go",  # the later scan wins the shared key
    }
    assert host.last_shell == GoInstalledSDKs.command()


async def test_go_installed_sdks_seam_whole_host():
    """The store models the whole host, so ``search_dirs`` does not filter
    it; the default empty store answers {} -- no SDKs found."""
    host = SimulatedHost(
        name="h", go_sdks={"1.22.10": "/usr/local/go1.22.10/bin/go"}
    )
    assert await GoInstalledSDKs.process(host, search_dirs=("/nowhere",)) == {
        "1.22.10": "/usr/local/go1.22.10/bin/go"
    }
    sdks = await GoInstalledSDKs.process(host)
    sdks["1.0.0"] = "tampered"
    assert await GoInstalledSDKs.process(host) == {
        "1.22.10": "/usr/local/go1.22.10/bin/go"
    }
    assert await GoInstalledSDKs.process(SimulatedHost(name="h")) == {}


# --- Fact: GoModuleInfo -----------------------------------------------------------


def test_go_module_info_command_reads_one_directory():
    """Guarded, ``shlex``-quoted ``cd`` plus ``go mod edit -json`` with the
    stderr hidden -- every failure mode (missing go, missing directory, no
    or unparsable go.mod) reaches the same empty output."""
    assert GoModuleInfo.command("/srv/app") == (
        "command -v go >/dev/null 2>&1 || exit 0; "
        "cd /srv/app 2>/dev/null && go mod edit -json 2>/dev/null"
    )
    hostile = GoModuleInfo.command("/srv/weird $dir")
    assert shlex.quote("/srv/weird $dir") in hostile


async def test_go_module_info_parses_go_mod_edit_json():
    """Fixture shaped like real ``go mod edit -json`` output: the module
    path and go directive lift out of the JSON wrapper names, and only
    direct requirements are mapped -- ``Indirect`` entries dropped."""
    host = CannedShellHost(
        json.dumps(
            {
                "Module": {"Path": "example.com/app"},
                "Go": "1.23",
                "Require": [
                    {"Path": "github.com/gin-gonic/gin", "Version": "v1.10.0"},
                    {
                        "Path": "github.com/go-playground/validator",
                        "Version": "v10.20.0",
                        "Indirect": True,
                    },
                ],
            }
        )
        + "\n"
    )
    assert await GoModuleInfo.process(host, "/srv/app") == {
        "module": "example.com/app",
        "go": "1.23",
        "require": {"github.com/gin-gonic/gin": "v1.10.0"},
    }
    assert host.last_shell == GoModuleInfo.command("/srv/app")


def test_go_module_info_parse_edge_cases():
    """Empty output, a non-JSON error line, a JSON non-object and a
    wrapper with no module path all read None; a missing go directive
    reads ``""``, and a keyless require entry contributes nothing."""
    assert GoModuleInfo.parse([]) is None
    assert GoModuleInfo.parse(["not json"]) is None
    assert GoModuleInfo.parse(["[]"]) is None
    assert GoModuleInfo.parse([json.dumps({"Go": "1.23"})]) is None
    assert GoModuleInfo.parse([json.dumps({"Module": {"Path": "m"}})]) == {
        "module": "m",
        "go": "",
        "require": {},
    }
    assert GoModuleInfo.parse(
        [json.dumps({"Module": {"Path": "m"}, "Require": [{"Version": "v1"}]})]
    ) == {"module": "m", "go": "", "require": {}}


async def test_go_module_info_seam_keys_by_directory():
    """The store keys by the same path the caller asks for; an unseeded
    directory reads None and the returned entry is freshly built."""
    info: GoModInfo = {
        "module": "example.com/app",
        "go": "1.23",
        "require": {"github.com/gin-gonic/gin": "v1.10.0"},
    }
    host = SimulatedHost(name="h", go_modules={"/srv/app": info})
    assert await GoModuleInfo.process(host, "/srv/app") == info
    assert await GoModuleInfo.process(host, "/srv/other") is None
    answer = await GoModuleInfo.process(host, "/srv/app")
    answer["require"]["evil.co/x"] = "v0"
    assert await GoModuleInfo.process(host, "/srv/app") == info


# --- Fact: GoBinaryBuildInfo -------------------------------------------------------


def test_go_binary_build_info_command_reads_one_binary():
    """Guarded ``go version -m`` with go's own errors hidden: a missing
    toolchain, a missing file and a non-Go binary all read the same empty
    output."""
    assert GoBinaryBuildInfo.command("/usr/local/go/bin/go") == (
        "command -v go >/dev/null 2>&1 || exit 0; "
        "go version -m /usr/local/go/bin/go 2>/dev/null"
    )
    hostile = GoBinaryBuildInfo.command("/srv/weird $dir")
    assert shlex.quote("/srv/weird $dir") in hostile


async def test_go_binary_build_info_parses_real_output():
    """Fixture shaped like real ``go version -m`` output (verified against
    a go 1.24 toolchain): the header's last field is the compiler version
    (the ``go`` prefix KEPT here), the ``path``/``mod`` rows fill the module
    pair, and ``build`` rows map into settings -- both printed forms."""
    host = CannedShellHost(
        "/usr/local/bin/api: go1.23.4\n"
        "\tpath\texample.com/app/cmd/api\n"
        "\tmod\texample.com/app\tv1.4.2\th1:zzz\n"
        "\tbuild\t-buildmode=exe\n"
        "\tbuild\t-compiler=gc\n"
        "\tbuild\tCGO_ENABLED=0\n"
        "\tbuild\tGOARCH=amd64\n"
        "\tbuild\tGOOS=linux\n"
        "\tbuild\tvcs.revision\tabc123def456\n"
        "\tbuild\tvcs.time\t2026-01-02T03:04:05Z\n"
        "\tdep\tgithub.com/gin-gonic/gin\tv1.10.0\th1:ggg\n"
    )
    assert await GoBinaryBuildInfo.process(host, "/usr/local/bin/api") == {
        "go_version": "go1.23.4",
        "path": "example.com/app/cmd/api",
        "module_path": "example.com/app",
        "module_version": "v1.4.2",
        "settings": {
            "buildmode": "exe",
            "compiler": "gc",
            "CGO_ENABLED": "0",
            "GOARCH": "amd64",
            "GOOS": "linux",
            "vcs.revision": "abc123def456",
            "vcs.time": "2026-01-02T03:04:05Z",
        },
    }
    assert host.last_shell == GoBinaryBuildInfo.command("/usr/local/bin/api")


async def test_go_binary_build_info_keeps_devel_and_noises():
    """Unlike the ``GoPackages`` install listing a ``(devel)`` module
    version is KEPT -- a ``go build`` artifact still reports its
    provenance; a header-only print leaves every field empty, and a tab row
    before any header reads None."""
    assert GoBinaryBuildInfo.parse(
        ["/out/tool: go1.24.4", "\tmod\texample.com/tool\t(devel)\t"]
    ) == {
        "go_version": "go1.24.4",
        "path": "",
        "module_path": "example.com/tool",
        "module_version": "(devel)",
        "settings": {},
    }
    assert GoBinaryBuildInfo.parse(["/out/tool: go1.24.4"]) == {
        "go_version": "go1.24.4",
        "path": "",
        "module_path": "",
        "module_version": "",
        "settings": {},
    }
    assert GoBinaryBuildInfo.parse([]) is None
    assert GoBinaryBuildInfo.parse(["\tpath\texample.com/orphan"]) is None
    assert await GoBinaryBuildInfo.process(CannedShellHost(""), "/nope") is None


async def test_go_binary_build_info_seam_keys_by_path():
    """The store keys by the same binary path the caller asks for; an
    unseeded path reads None and the returned entry is freshly built."""
    info: GoBuildInfo = {
        "go_version": "go1.23.4",
        "path": "example.com/app/cmd/api",
        "module_path": "example.com/app",
        "module_version": "v1.4.2",
        "settings": {"GOOS": "linux"},
    }
    host = SimulatedHost(name="h", go_binaries={"/usr/local/bin/api": info})
    assert await GoBinaryBuildInfo.process(host, "/usr/local/bin/api") == info
    assert await GoBinaryBuildInfo.process(host, "/usr/local/bin/other") is None
    answer = await GoBinaryBuildInfo.process(host, "/usr/local/bin/api")
    answer["settings"]["GOOS"] = "tampered"
    assert await GoBinaryBuildInfo.process(host, "/usr/local/bin/api") == info


# --- gather_fact: parameterized caching --------------------------------------------


async def test_gather_fact_keys_the_parameterized_facts():
    """``gather_fact`` forwards the fact's arguments and keys its cache by
    them: two directories answer separately, a repeat read is served from
    the cache without re-gathering, and a fleet fans out per host."""
    host1 = SimulatedHost(
        name="web1",
        go_modules={"/srv/a": {"module": "a", "go": "1.23", "require": {}}},
        go_version={"version": "1.23.4", "raw": "go version", "goos": "linux", "goarch": "amd64"},
    )
    host2 = SimulatedHost(name="web2")
    cache = FactCache(host1, phase="prepare")
    first = await cache.get(GoModuleInfo, "/srv/a")
    assert first == {"module": "a", "go": "1.23", "require": {}}
    assert await cache.get(GoModuleInfo, "/srv/a") is first  # cached, no re-gather
    assert await cache.get(GoModuleInfo, "/srv/b") is None
    answers = await gather_fact([host1, host2], GoVersion)
    assert answers == {
        "web1": {
            "version": "1.23.4",
            "raw": "go version",
            "goos": "linux",
            "goarch": "amd64",
        },
        "web2": None,
    }


# --- Operation descriptors: go.env / go.cache_clean / go.build rendering ---------


def test_go_env_set_command_renders_sorted_pairs():
    """One ``go env -w`` batch, every ``KEY=VAL`` a single ``shlex``-quoted
    token in sorted key order -- a value with spaces or metacharacters
    reaches go whole, and the request order never changes the shell."""
    cmd = GoEnvSetCommand(
        assignments=(("GOPROXY", "off"), ("GOFLAGS", "-mod=vendor -trimpath"))
    )
    assert cmd.shell() == "go env -w 'GOFLAGS=-mod=vendor -trimpath' GOPROXY=off"
    # Same pairs, other spelling order: identical shell (sorted rendering).
    same = GoEnvSetCommand(
        assignments=(("GOFLAGS", "-mod=vendor"), ("GOPROXY", "off"))
    )
    assert same.shell() == "go env -w GOFLAGS=-mod=vendor GOPROXY=off"


def test_go_env_unset_command_renders_sorted_keys():
    """One ``go env -u`` batch of the removed keys, sorted and quoted."""
    cmd = GoEnvUnsetCommand(keys=("GONOSUMCHECK", "GOFLAGS"))
    assert cmd.shell() == "go env -u GOFLAGS GONOSUMCHECK"


def test_go_clean_command_flags_follow_selection():
    """``go clean`` carries ``-cache`` for the build cache and ``-modcache``
    for the module cache; the operation never emits it with neither."""
    assert GoCleanCommand(build_cache=True, mod_cache=False).shell() == (
        "go clean -cache"
    )
    assert GoCleanCommand(build_cache=True, mod_cache=True).shell() == (
        "go clean -cache -modcache"
    )
    assert GoCleanCommand(build_cache=False, mod_cache=True).shell() == (
        "go clean -modcache"
    )


def test_go_build_command_wraps_the_chain_in_sh_c():
    """The whole step is one ``sh -c``-wrapped compound -- ``mkdir -p`` of the
    output's parent, ``cd`` into the source dir, then ``go build`` -- so the
    chain runs together and an ``op(env=...)`` prefix reaches ``go build``.
    ``tags`` join into one comma ``-tags`` list, ``ldflags`` is one argument,
    and every path/value is quoted."""
    cmd = GoBuildCommand(
        src_dir="/srv/app",
        output_path="/out/bin/app",
        pkg="./cmd/server",
        ldflags="-s -w -X main.version=1.2.3",
        tags=("netgo", "osusergo"),
    )
    assert cmd.shell() == (
        """sh -c 'mkdir -p /out/bin && cd /srv/app && go build """
        """-tags netgo,osusergo -ldflags '"'"'-s -w -X main.version=1.2.3'"'"' """
        """-o /out/bin/app ./cmd/server'"""
    )


def test_go_build_command_minimal_and_bare_output():
    """With no tags/ldflags and the default ``.`` package only ``-o`` and the
    package appear; a bare (no-directory) output builds into the current
    directory (``mkdir -p .``), never ``mkdir`` of the binary's own name."""
    minimal = GoBuildCommand(src_dir="/srv/app", output_path="/out/app")
    assert minimal.shell() == (
        "sh -c 'mkdir -p /out && cd /srv/app && go build -o /out/app .'"
    )
    bare = GoBuildCommand(src_dir="/srv/app", output_path="app")
    assert bare.shell() == (
        "sh -c 'mkdir -p . && cd /srv/app && go build -o app .'"
    )


def test_go_build_command_env_and_sudo_wrap_the_shell():
    """The driver renders ``op(env=...)`` as an ``env K=V`` prefix ahead of the
    ``sh -c`` wrapper (so it applies to the whole build, not just the leading
    ``mkdir``) and ``sudo`` elevates the entire command."""
    cmd = GoBuildCommand(src_dir="/srv/app", output_path="/out/bin/app")
    plain = (
        "sh -c 'mkdir -p /out/bin && cd /srv/app && go build -o /out/bin/app .'"
    )
    assert command_shell(cmd) == plain
    assert command_shell(cmd, sudo=True, env={"GOOS": "linux"}) == (
        f"sudo env GOOS=linux {plain}"
    )


# --- Operation: go.env idempotence ----------------------------------------------


async def test_go_env_sets_new_key():
    """A key the host has no value for is written with ``go env -w``."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [c async for c in go.env(ctx, settings=(("GOPROXY", "off"),))]
    assert ctx.changed
    assert ctx.reason == "set GOPROXY"
    assert commands == [GoEnvSetCommand(assignments=(("GOPROXY", "off"),))]


async def test_go_env_noop_when_already_in_place():
    """Every key already holding its desired value yields no command at all;
    the reason is recorded even when unchanged."""
    host = SimulatedHost(name="h", go_env={"GOPROXY": "off", "GOFLAGS": "-x"})
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.env(
            ctx, settings=(("GOPROXY", "off"), ("GOFLAGS", "-x"))
        )
    ]
    assert not ctx.changed
    assert ctx.reason == "settings already in place"
    assert commands == []


async def test_go_env_sets_changed_value_only():
    """Only the keys that drift are written: a matching neighbour is left
    out of the batch."""
    host = SimulatedHost(name="h", go_env={"GOPROXY": "on", "GOFLAGS": "-x"})
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.env(
            ctx, settings=(("GOPROXY", "off"), ("GOFLAGS", "-x"))
        )
    ]
    assert ctx.changed
    assert commands == [GoEnvSetCommand(assignments=(("GOPROXY", "off"),))]


async def test_go_env_unsets_present_key():
    """A ``None`` value removes a key the host currently has a non-empty
    value for via ``go env -u``."""
    host = SimulatedHost(name="h", go_env={"GOFLAGS": "-x"})
    ctx = _ctx(host)
    commands = [c async for c in go.env(ctx, settings=(("GOFLAGS", None),))]
    assert ctx.changed
    assert ctx.reason == "unset GOFLAGS"
    assert commands == [GoEnvUnsetCommand(keys=("GOFLAGS",))]


async def test_go_env_skips_unset_of_absent_key():
    """Unsetting a key that is already empty -- or absent -- is a no-op: only
    non-empty current values fire the ``-u`` batch."""
    host = SimulatedHost(name="h", go_env={"GOFLAGS": ""})
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.env(
            ctx, settings=(("GOFLAGS", None), ("GONOSUMCHECK", None))
        )
    ]
    assert not ctx.changed
    assert ctx.reason == "settings already in place"
    assert commands == []


async def test_go_env_batches_unset_before_set():
    """A rename (unset the old, set the new) yields both batches, the unset
    first, and joins the two reasons."""
    host = SimulatedHost(name="h", go_env={"GOOLD": "keep"})
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.env(
            ctx, settings=(("GOOLD", None), ("GONEW", "v"))
        )
    ]
    assert ctx.changed
    assert ctx.reason == "unset GOOLD; set GONEW"
    assert commands == [
        GoEnvUnsetCommand(keys=("GOOLD",)),
        GoEnvSetCommand(assignments=(("GONEW", "v"),)),
    ]


async def test_go_env_none_is_a_noop():
    """An empty settings request yields nothing and says so."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [c async for c in go.env(ctx, settings=())]
    assert not ctx.changed
    assert ctx.reason == "no settings requested"
    assert commands == []


# --- Operation: go.cache_clean (stateless) --------------------------------------


async def test_go_cache_clean_always_yields():
    """The operation is stateless: an empty cache or a full one both fire
    ``go clean -cache`` every run, like ``dnf.clean``."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [c async for c in go.cache_clean(ctx)]
    assert ctx.changed
    assert ctx.reason == "clean build cache"
    assert commands == [GoCleanCommand(build_cache=True, mod_cache=False)]


async def test_go_cache_clean_selects_module_cache():
    """``clear_mod_cache`` appends ``-modcache`` and the reason names both
    selected caches."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.cache_clean(
            ctx, clear_build_cache=True, clear_mod_cache=True
        )
    ]
    assert commands == [GoCleanCommand(build_cache=True, mod_cache=True)]
    assert ctx.reason == "clean build cache, module cache"


async def test_go_cache_clean_neither_is_a_noop():
    """Selecting neither cache is a no-op -- there is nothing to clear."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.cache_clean(
            ctx, clear_build_cache=False, clear_mod_cache=False
        )
    ]
    assert not ctx.changed
    assert ctx.reason == "no caches selected"
    assert commands == []


# --- Operation: go.build idempotence --------------------------------------------


_BUILD_VERSION = {
    "version": "1.23.4",
    "raw": "go version go1.23.4 linux/amd64",
    "goos": "linux",
    "goarch": "amd64",
}


async def test_go_build_missing_binary_builds():
    """No binary at ``output_path`` -- the build fires."""
    host = SimulatedHost(name="h", go_version=dict(_BUILD_VERSION))
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.build(
            ctx, src_dir="/srv/app", output_path="/out/bin/app"
        )
    ]
    assert ctx.changed
    assert ctx.reason == "/out/bin/app missing, will build"
    assert commands == [
        GoBuildCommand(
            src_dir="/srv/app",
            output_path="/out/bin/app",
            pkg=".",
            ldflags=None,
            tags=(),
        )
    ]


async def test_go_build_matches_current_toolchain_is_noop():
    """A binary whose embedded compiler equals the current toolchain is left
    alone."""
    host = SimulatedHost(
        name="h",
        go_version=dict(_BUILD_VERSION),
        go_binaries={
            "/out/bin/app": {
                "go_version": "go1.23.4",
                "path": "",
                "module_path": "",
                "module_version": "",
                "settings": {},
            }
        },
    )
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.build(
            ctx, src_dir="/srv/app", output_path="/out/bin/app"
        )
    ]
    assert not ctx.changed
    assert ctx.reason == "/out/bin/app already built with the current toolchain"
    assert commands == []


async def test_go_build_toolchain_mismatch_rebuilds():
    """A binary built by a different compiler version is rebuilt, and the
    reason names the toolchain that produced it."""
    host = SimulatedHost(
        name="h",
        go_version=dict(_BUILD_VERSION),
        go_binaries={
            "/out/bin/app": {
                "go_version": "go1.20.0",
                "path": "",
                "module_path": "",
                "module_version": "",
                "settings": {},
            }
        },
    )
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.build(
            ctx, src_dir="/srv/app", output_path="/out/bin/app"
        )
    ]
    assert ctx.changed
    assert ctx.reason == "/out/bin/app built by go1.20.0, will rebuild"
    assert len(commands) == 1


async def test_go_build_unknown_current_version_keeps_binary():
    """When the toolchain cannot report a version (a ``devel`` go), a present
    binary is treated as up to date rather than force-rebuilt every run."""
    host = SimulatedHost(
        name="h",
        go_binaries={
            "/out/bin/app": {
                "go_version": "go1.20.0",
                "path": "",
                "module_path": "",
                "module_version": "",
                "settings": {},
            }
        },
    )
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.build(
            ctx, src_dir="/srv/app", output_path="/out/bin/app"
        )
    ]
    assert not ctx.changed
    assert ctx.reason == "/out/bin/app already built with the current toolchain"
    assert commands == []


async def test_go_build_force_skips_the_facts():
    """``force`` rebuilds unconditionally -- the facts are never consulted, so
    an up-to-date binary still re-runs the build (the caller switches target
    platforms through ``force`` because ``go version -m`` records no GOOS."""
    host = SimulatedHost(
        name="h",
        go_version=dict(_BUILD_VERSION),
        go_binaries={
            "/out/bin/app": {
                "go_version": "go1.23.4",
                "path": "",
                "module_path": "",
                "module_version": "",
                "settings": {},
            }
        },
    )
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.build(
            ctx, src_dir="/srv/app", output_path="/out/bin/app", force=True
        )
    ]
    assert ctx.changed
    assert ctx.reason == "forced build of /out/bin/app"
    assert commands == [
        GoBuildCommand(
            src_dir="/srv/app",
            output_path="/out/bin/app",
            pkg=".",
            ldflags=None,
            tags=(),
        )
    ]


async def test_go_build_normalizes_tags_and_absorbs_globals():
    """A ``tags`` sequence becomes the tuple on the descriptor; ``**kwargs``
    takes pyinfra's global arguments with no effect."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [
        c
        async for c in go.build(
            ctx,
            src_dir="/srv/app",
            output_path="/out/app",
            tags=["netgo", "osusergo"],
            untar=False,
            quote=True,
        )
    ]
    assert commands[0].tags == ("netgo", "osusergo")


# --- Operation: SimulatedHost seam dispatch (env/clean/build) -------------------


async def test_go_env_set_unset_seam_mutates_store():
    """``send_command`` maps the two env descriptors onto the ``go_env``
    store: a set persists the pair, an unset drops the key."""
    host = SimulatedHost(name="h", go_env={"GOFLAGS": "-x"})
    await send_command(
        host, GoEnvSetCommand(assignments=(("GOPROXY", "off"),))
    )
    assert host.go_env == {"GOFLAGS": "-x", "GOPROXY": "off"}
    await send_command(host, GoEnvUnsetCommand(keys=("GOFLAGS",)))
    assert host.go_env == {"GOPROXY": "off"}


async def test_go_clean_seam_is_a_noop():
    """The simulation models no cache contents, so a ``go clean`` command
    dispatches without touching state (and without error)."""
    host = SimulatedHost(name="h")
    await send_command(host, GoCleanCommand(build_cache=True, mod_cache=True))
    assert host.go_env == {}


async def test_go_build_seam_registers_binary():
    """A build records the output binary embedding ``go`` + the current
    toolchain version -- the exact half ``go.build`` compares -- so a repeat
    build with an unchanged toolchain converges."""
    host = SimulatedHost(name="h", go_version=dict(_BUILD_VERSION))
    await send_command(
        host, GoBuildCommand(src_dir="/srv/app", output_path="/out/bin/app")
    )
    assert host.go_binaries["/out/bin/app"]["go_version"] == "go1.23.4"


# --- Operation: end to end (env / cache_clean / build) --------------------------


def env_deploy(host):
    yield op(go.env, name="Tune go env", settings=(("GOPROXY", "off"),))


def env_unset_deploy(host):
    yield op(go.env, name="Clear go flag", settings=(("GOPROXY", None),))


def clean_deploy(host):
    yield op(go.cache_clean, name="Clean go cache")


def build_deploy(host):
    yield op(
        go.build,
        name="Build app",
        src_dir="/srv/app",
        output_path="/out/bin/app",
    )


async def test_go_env_end_to_end_set_then_unset_cycle():
    """The dry run plans the set and sends nothing, the applied run persists
    the value, a repeat set is a no-op, and the unset pass then clears it."""
    host = SimulatedHost(name="h")

    def factory(entry: dict) -> SimulatedHost:
        return host

    plans, dry_report, _hosts = await run(
        INVENTORY, env_deploy, host_factory=factory, dry=True
    )
    assert dry_report is None
    assert plans["h"][0].changed
    assert plans["h"][0].reason == "set GOPROXY"
    assert host.go_env == {}  # a dry run sends nothing

    _plans, report, _hosts = await run(
        INVENTORY, env_deploy, host_factory=factory
    )
    assert "$ go env -w GOPROXY=off" in format_log(report)
    assert host.go_env == {"GOPROXY": "off"}

    plans2, report2, _hosts2 = await run(
        INVENTORY, env_deploy, host_factory=factory
    )
    assert not plans2["h"][0].changed
    assert "[no change: settings already in place]" in format_log(report2)

    _plans3, report3, _hosts3 = await run(
        INVENTORY, env_unset_deploy, host_factory=factory
    )
    assert "$ go env -u GOPROXY" in format_log(report3)
    assert host.go_env == {}


async def test_go_cache_clean_end_to_end_always_fires():
    """The stateless clean sends ``go clean -cache`` on every applied run --
    it re-plans even after a run already cleaned -- and the dry run sends
    nothing."""
    host = SimulatedHost(name="h")

    def factory(entry: dict) -> SimulatedHost:
        return host

    plans, _report, _hosts = await run(
        INVENTORY, clean_deploy, host_factory=factory, dry=True
    )
    assert plans["h"][0].changed
    assert plans["h"][0].reason == "clean build cache"

    _plans, report, _hosts = await run(
        INVENTORY, clean_deploy, host_factory=factory
    )
    assert "$ go clean -cache" in format_log(report)
    plans2, report2, _hosts2 = await run(
        INVENTORY, clean_deploy, host_factory=factory
    )
    assert plans2["h"][0].changed
    assert "$ go clean -cache" in format_log(report2)


async def test_go_build_end_to_end_build_then_noop():
    """The dry run plans the build and writes nothing, the applied run
    converges the binary into ``go_binaries``, and the second pass is a
    no-op against the current toolchain."""
    host = SimulatedHost(name="h", go_version=dict(_BUILD_VERSION))

    def factory(entry: dict) -> SimulatedHost:
        return host

    plans, dry_report, _hosts = await run(
        INVENTORY, build_deploy, host_factory=factory, dry=True
    )
    assert dry_report is None
    assert plans["h"][0].changed
    assert plans["h"][0].reason == "/out/bin/app missing, will build"
    assert host.go_binaries == {}  # a dry run sends nothing

    _plans, report, _hosts = await run(
        INVENTORY, build_deploy, host_factory=factory
    )
    assert (
        "$ sh -c 'mkdir -p /out/bin && cd /srv/app && "
        "go build -o /out/bin/app .'" in format_log(report)
    )
    assert host.go_binaries["/out/bin/app"]["go_version"] == "go1.23.4"

    plans2, report2, _hosts2 = await run(
        INVENTORY, build_deploy, host_factory=factory
    )
    assert not plans2["h"][0].changed
    assert "[no change: /out/bin/app already built with the current toolchain]" in (
        format_log(report2)
    )


# --- Exports ----------------------------------------------------------------------


def test_go_module_exports():
    """The package re-exports every fact, entry type, the operation and the
    descriptors, so both ``go.packages`` and ``go.GoPackages`` resolve, and
    the top-level package makes ``from reemote import GoPackages`` (and
    each new fact) work."""
    assert go.GoPackages is GoPackages
    assert go.GoPackageInfo is GoPackageInfo
    assert go.packages is not None
    assert go.GoInstallCommand is GoInstallCommand
    assert go.GoRemoveCommand is GoRemoveCommand
    assert go.env is not None and go.cache_clean is not None and go.build is not None
    assert go.GoEnvSetCommand is GoEnvSetCommand
    assert go.GoEnvUnsetCommand is GoEnvUnsetCommand
    assert go.GoCleanCommand is GoCleanCommand
    assert go.GoBuildCommand is GoBuildCommand
    for name in (
        "GoVersion",
        "GoVersionInfo",
        "GoEnv",
        "GoInstalledSDKs",
        "GoModuleInfo",
        "GoModInfo",
        "GoBinaryBuildInfo",
        "GoBuildInfo",
    ):
        assert getattr(go, name) is globals()[name]
    import reemote

    assert reemote.GoPackages is GoPackages
    assert reemote.GoPackageInfo is GoPackageInfo
    assert reemote.GoInstallCommand is GoInstallCommand
    assert reemote.GoRemoveCommand is GoRemoveCommand
    for name in (
        "GoVersion",
        "GoVersionInfo",
        "GoEnv",
        "GoInstalledSDKs",
        "GoModuleInfo",
        "GoModInfo",
        "GoBinaryBuildInfo",
        "GoBuildInfo",
        "GoEnvSetCommand",
        "GoEnvUnsetCommand",
        "GoCleanCommand",
        "GoBuildCommand",
    ):
        assert getattr(reemote, name) is globals()[name]
        assert name in reemote.__all__
    # The operation functions are reached through the namespace, never the
    # top level.
    assert not hasattr(reemote, "cache_clean")
