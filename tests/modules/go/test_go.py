# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the go module: the ``GoPackages`` fact and ``go.packages``.

Covers ``reemote.modules.go.go.GoPackages`` -- the ``go install`` binaries
as ``GoPackageInfo`` entries -- both over the real-transport path (a
``CannedShellHost`` returning canned ``go version -m`` output, including
pyinfra's own fact fixture verbatim: the compound command string, the
``(devel)`` skip, the ``dep``-row and header-line ignores) and through the
``SimulatedHost`` seam, plus the ``gather_fact`` fan-out and the exports.

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
    GoInstallCommand,
    GoPackageInfo,
    GoPackages,
    GoRemoveCommand,
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


# --- Exports ----------------------------------------------------------------------


def test_go_module_exports():
    """The package re-exports the fact, its entry type, the operation and
    the descriptors, so both ``go.packages`` and ``go.GoPackages`` resolve,
    and the top-level package makes ``from reemote import GoPackages``
    work."""
    assert go.GoPackages is GoPackages
    assert go.GoPackageInfo is GoPackageInfo
    assert go.packages is not None
    assert go.GoInstallCommand is GoInstallCommand
    assert go.GoRemoveCommand is GoRemoveCommand
    import reemote

    assert reemote.GoPackages is GoPackages
    assert reemote.GoPackageInfo is GoPackageInfo
    assert reemote.GoInstallCommand is GoInstallCommand
    assert reemote.GoRemoveCommand is GoRemoveCommand
