# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the cargo module: the ``CargoPackages`` and ``CargoConfig`` facts and ``cargo.packages``.

Covers ``reemote.modules.cargo.cargo.CargoPackages`` -- the globally
installed ``cargo install`` crates as ``CargoPackageInfo`` entries -- both
over the real-transport path (a ``CannedShellHost`` returning canned
``cargo install --list`` output, including pyinfra's own fact fixture
verbatim: the command string, the ``- <path>`` binary-line and blank-line
ignores) and through the ``SimulatedHost`` seam, plus the parser's widened
name/version character classes, the ``gather_fact`` fan-out and the exports.

Also covers ``reemote.modules.cargo.cargo.CargoConfig`` -- the parsed TOML
of the host's ``$CARGO_HOME`` ``config.toml`` (legacy ``config`` fallback) --
over both paths: the file-test chain that never swallows a present-but
unreadable config, the control-side ``tomllib`` parse with its total
empty/malformed answers, and the ``cargo_config`` seam.

The ``cargo.packages`` operation -- a pyinfra port reading ``CargoPackages``
-- is exercised over its two command descriptors (``CargoInstallCommand`` /
``CargoRemoveCommand``), their rendering (``cargo install <token> ...`` and
``cargo uninstall <pkg>[@<version>] ...`` with the pins kept), the shared
``<pkg>=<version>`` pin translated to cargo's ``@`` token, pyinfra's two
operation fixtures verbatim, the version-aware removal (deliberately unlike
go's name-based one), the documented non-idempotent ``latest`` path, the
``SimulatedHost`` seam dispatch, and end to end through ``run`` /
``run_prepare`` + ``run_apply``.

``asyncio_mode = "auto"`` lets the async tests run without decorators.
"""

import shlex

import pytest

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
from reemote.modules import cargo
from reemote.modules.cargo.cargo import (
    CargoCacheRemoveCommand,
    CargoCleanCommand,
    CargoConfig,
    CargoInstallCommand,
    CargoPackageInfo,
    CargoPackages,
    CargoRemoveCommand,
    _cargo_package_spec,
    _cargo_split_spec,
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


def _entry(name: str, version: str) -> CargoPackageInfo:
    """One installed ``CargoPackageInfo`` entry, spelled once per test."""
    return {"name": name, "version": version}


# --- Fact: command parity ------------------------------------------------------

# pyinfra's own fact fixture (tests/facts/cargo.CargoPackages/packages.json):
# the command string it asserts on, verbatim.
PYINFRA_COMMAND = "cargo install --list"


def test_cargo_packages_command_is_pyinfra_verbatim():
    """The fact issues pyinfra's command unchanged: cargo's own listing of
    the globally installed crates."""
    assert CargoPackages.COMMAND == PYINFRA_COMMAND


# --- Fact: real-transport parsing (pyinfra fixture parity) ---------------------

# pyinfra's fact fixture output verbatim: two versioned crates (one with a
# git source suffix) and the indented binary lines that must not contribute.
PYINFRA_OUTPUT = (
    "vigil-server v1.25.2:\n"
    "    vigil\n"
    "zee v0.4.0 (https://github.com/zee-editor/zee#08c9f31d):\n"
    "    zee"
)


async def test_cargo_packages_parses_pyinfras_fixture():
    """Over the real-transport path pyinfra's own fixture yields its
    documented example result: both crates with their ``v``-prefixed
    versions stripped and the binary lines contributing nothing -- the
    answer pyinfra models as ``{"vigil-server": ["1.25.2"], "zee":
    ["0.4.0"]}`` is here two sorted ``CargoPackageInfo`` entries."""
    host = CannedShellHost(PYINFRA_OUTPUT + "\n")
    assert await CargoPackages.process(host) == [
        _entry("vigil-server", "1.25.2"),
        _entry("zee", "0.4.0"),
    ]
    assert host.last_shell == CargoPackages.COMMAND


async def test_cargo_packages_sorts_dedups_and_keeps_every_version():
    """Rows sort by ``(name, version)``, duplicate rows collapse, and one
    crate installed at several versions contributes one entry per version
    (the ``gem`` version-list precedent, not npm's one-per-name)."""
    output = (
        "ripgrep v14.1.0:\n"
        "    /home/u/.cargo/bin/rg\n"
        "ripgrep v13.0.0:\n"
        "    /home/u/.cargo/bin/rg\n"
        "cargo-edit v0.11.6:\n"
        "    /home/u/.cargo/bin/cargo-add\n"
        "ripgrep v14.1.0:\n"
        "    /home/u/.cargo/bin/rg"
    )
    assert await CargoPackages.process(CannedShellHost(output)) == [
        _entry("cargo-edit", "0.11.6"),
        _entry("ripgrep", "13.0.0"),
        _entry("ripgrep", "14.1.0"),
    ]


async def test_cargo_packages_keeps_underscores_and_prerelease_versions():
    """The two deliberate widenings of pyinfra's regex
    ``^([a-zA-Z0-9\\-]+)\\sv([0-9\\.]+)``: a crates.io name may carry
    ``_`` (pyinfra silently drops ``lazy_static``-style crates) and a
    version token may carry pre-release or build metadata (pyinfra's
    ``[0-9\\.]+`` truncates ``1.0.0-beta.2`` to ``1.0.0``, reporting a
    version the host does not have). reemote keeps both whole."""
    output = (
        "lazy_static v1.5.0:\n"
        "clap v4.0.0-beta.2:\n"
        "    /home/u/.cargo/bin/clap\n"
        "built v0.7.7+20240101:"
    )
    assert await CargoPackages.process(CannedShellHost(output)) == [
        _entry("built", "0.7.7+20240101"),
        _entry("clap", "4.0.0-beta.2"),
        _entry("lazy_static", "1.5.0"),
    ]


async def test_cargo_packages_ignores_binary_lines_blanks_and_noise():
    """The ``- <path>`` binary lines under each header (the real listing
    does not indent them), blank lines, a stray error line and rows that
    are not ``<crate> v<version>`` headers at all contribute nothing; the
    one real crate still reads out."""
    output = (
        "\n"
        "cargo-edit v0.11.6:\n"
        "- /home/u/.cargo/bin/cargo-add\n"
        "- /home/u/.cargo/bin/cargo-rm\n"
        "\n"
        "error: there's nothing to list!\n"
        "bare 1.0.0\n"
        "v1.0.0\n"
        "not a header line"
    )
    assert await CargoPackages.process(CannedShellHost(output)) == [
        _entry("cargo-edit", "0.11.6")
    ]


async def test_cargo_packages_unparsable_output_reads_empty():
    """A host with cargo but nothing installed (no output at all, cargo's
    own hint line) reads as ``[]`` -- the counterpart of pyinfra's
    ``default = dict``."""
    assert CargoPackages.parse([]) == []
    assert await CargoPackages.process(CannedShellHost("")) == []
    assert await CargoPackages.process(
        CannedShellHost("error: there's nothing to list!\n")
    ) == []


# --- Fact: SimulatedHost seam ---------------------------------------------------


async def test_cargo_packages_reads_the_simulated_seam():
    """A SimulatedHost answers from its ``cargo_packages`` store, bypassing
    the shell; the default empty store reads as ``[]``."""
    host = SimulatedHost(name="h", cargo_packages={"cargo-edit": ["0.11.6"]})
    assert await CargoPackages.process(host) == [_entry("cargo-edit", "0.11.6")]
    assert await CargoPackages.process(SimulatedHost(name="h")) == []


async def test_cargo_packages_seam_reports_every_version():
    """One crate installed at several versions projects one entry per
    version, sorted and de-duplicated."""
    host = SimulatedHost(
        name="h",
        cargo_packages={"ripgrep": ["14.1.0", "13.0.0", "14.1.0"]},
    )
    assert await CargoPackages.process(host) == [
        _entry("ripgrep", "13.0.0"),
        _entry("ripgrep", "14.1.0"),
    ]


async def test_cargo_packages_seam_returns_copies():
    """Mutating a returned entry (or the list) never touches the simulated
    state, so the seam hands back freshly built dicts."""
    host = SimulatedHost(
        name="h",
        cargo_packages={"a-crate": ["1.0.0"], "b-crate": ["2.0.0"]},
    )
    entries = await CargoPackages.process(host)
    entries[0]["version"] = "tampered"
    entries.append(_entry("tampered", "0"))
    assert await CargoPackages.process(host) == [
        _entry("a-crate", "1.0.0"),
        _entry("b-crate", "2.0.0"),
    ]
    assert host.cargo_packages == {"a-crate": ["1.0.0"], "b-crate": ["2.0.0"]}


async def test_gather_fact_cargo_packages_fans_out():
    """Across a simulated fleet each host answers from its own seam store,
    keyed by host name."""
    host1 = SimulatedHost(name="web1", cargo_packages={"ripgrep": ["14.1.0"]})
    host2 = SimulatedHost(name="web2")
    answers = await gather_fact([host1, host2], CargoPackages)
    assert answers == {
        "web1": [_entry("ripgrep", "14.1.0")],
        "web2": [],
    }


# --- CargoConfig fact ------------------------------------------------------------


def test_cargo_config_command_tests_the_files_it_prints():
    """The chain resolves ``$CARGO_HOME`` (falling back to ``$HOME/.cargo``)
    and prints ``config.toml`` first, the legacy ``config`` second -- each
    behind an ``[ -f ]`` test, never the ``cat ... 2>/dev/null || true``
    shape that swallows a present-but-unreadable file; and it never invokes
    the ``cargo`` binary, so no host can answer 127 here."""
    assert CargoConfig.command() == (
        'CFG="${CARGO_HOME:-$HOME/.cargo}"; '
        'if [ -f "$CFG/config.toml" ]; then cat "$CFG/config.toml"; '
        'elif [ -f "$CFG/config" ]; then cat "$CFG/config"; fi'
    )


def test_cargo_config_command_quotes_an_explicit_home():
    """An explicit ``cargo_home`` replaces the host-side resolution as one
    ``shlex``-quoted assignment."""
    assert CargoConfig.command(cargo_home="/srv/cargo home").startswith(
        "CFG='/srv/cargo home'; "
    )
    hostile = "/srv/$(rm -rf /)"
    assert CargoConfig.command(cargo_home=hostile).startswith(
        f"CFG={shlex.quote(hostile)}; "
    )


async def test_cargo_config_parses_multiline_toml():
    """The printed file is re-joined and parsed control-side: tables, string
    and boolean values come back as the mapping cargo reads."""
    output = (
        "[build]\n"
        'target-dir = "/tmp/cargo"\n'
        "jobs = 4\n"
        "\n"
        "[net]\n"
        "git-fetch-with-cli = true\n"
    )
    host = CannedShellHost(output)
    assert await CargoConfig.process(host) == {
        "build": {"target-dir": "/tmp/cargo", "jobs": 4},
        "net": {"git-fetch-with-cli": True},
    }
    assert host.last_shell == CargoConfig.command()


async def test_cargo_config_absent_and_malformed_read_empty():
    """No config file (the ``if``/``elif`` prints nothing) and unparseable
    text both read ``{}`` -- the total-parser rule, the design's
    ``default = dict``; a crash mid-gather is never the answer."""
    assert await CargoConfig.process(CannedShellHost("")) == {}
    assert CargoConfig.parse(["not toml at all ["]) == {}
    assert CargoConfig.parse([]) == {}


async def test_cargo_config_reads_the_simulated_seam():
    """A SimulatedHost answers from its ``cargo_config`` store, bypassing
    the shell; the default empty store is the fact's own absent-file
    answer, and ``cargo_home`` cannot narrow a store that models the one
    config file (the ``GoInstalledSDKs`` rule)."""
    host = SimulatedHost(name="h", cargo_config={"build": {"jobs": 8}})
    assert await CargoConfig.process(host, cargo_home="/ignored") == {
        "build": {"jobs": 8}
    }
    assert await CargoConfig.process(SimulatedHost(name="h")) == {}


async def test_cargo_config_seam_returns_a_copy():
    """Mutating the returned dict never touches the simulated state."""
    host = SimulatedHost(name="h", cargo_config={"build": {"jobs": 8}})
    config = await CargoConfig.process(host)
    config["install"] = {"force": True}
    assert await CargoConfig.process(host) == {"build": {"jobs": 8}}
    assert host.cargo_config == {"build": {"jobs": 8}}


async def test_gather_fact_cargo_config_fans_out():
    """Across a simulated fleet each host answers from its own store, keyed
    by host name."""
    host1 = SimulatedHost(name="web1", cargo_config={"net": {"offline": True}})
    host2 = SimulatedHost(name="web2")
    answers = await gather_fact([host1, host2], CargoConfig)
    assert answers == {
        "web1": {"net": {"offline": True}},
        "web2": {},
    }


# --- Operation: shared helpers -------------------------------------------------


def test_split_version_splits_at_the_last_equals():
    """reemote's one user-facing pin spelling, shared with apt/dnf/apk/npm/
    gem: the split is at the *last* ``=``, so a version carrying one stays
    whole."""
    assert _split_version("cargo-edit") == ("cargo-edit", None)
    assert _split_version("cargo-edit=0.11.6") == ("cargo-edit", "0.11.6")
    assert _split_version("odd=1=2") == ("odd=1", "2")


def test_cargo_package_spec_translates_the_pin_to_cargo_grammar():
    """The request's ``=`` becomes cargo's ``@``; an unpinned name and the
    special ``latest`` pin both render as the bare name -- unlike go, plain
    ``cargo install <crate>`` works and resolves the registry's newest."""
    assert _cargo_package_spec("cargo-edit=0.11.6") == "cargo-edit@0.11.6"
    assert _cargo_package_spec("ripgrep") == "ripgrep"
    assert _cargo_package_spec("ripgrep=latest") == "ripgrep"


def test_cargo_split_spec_inverts_the_translation():
    """The seam's reader splits at the *first* ``@`` -- pyinfra's
    ``partition("@")`` rule -- because a crate name carries no ``@``;
    a bare name yields no version."""
    assert _cargo_split_spec("cargo-edit@0.11.6") == ("cargo-edit", "0.11.6")
    assert _cargo_split_spec("ripgrep") == ("ripgrep", None)


# --- Operation: descriptor rendering -------------------------------------------


def test_cargo_install_command_shells_as_cargo_install():
    """Tokens pass through ``cargo install`` in order; safe crate names need
    no quoting, so the render matches pyinfra's command verbatim."""
    cmd = CargoInstallCommand(packages=("git@1.1", "react"))
    assert cmd.shell() == "cargo install git@1.1 react"
    batched = CargoInstallCommand(packages=("ripgrep", "cargo-edit@0.11.6"))
    assert batched.shell() == "cargo install ripgrep cargo-edit@0.11.6"


def test_cargo_install_command_omits_empty_lists():
    """A command with no tokens stays bare, so no trailing space reaches the
    log."""
    assert CargoInstallCommand(packages=()).shell() == "cargo install"


def test_cargo_install_command_quotes_a_hostile_token():
    """A name carrying a metacharacter is ``shlex``-quoted, so it never
    reaches the remote shell unescaped."""
    hostile = "evil; rm -rf /"
    assert CargoInstallCommand(packages=(hostile,)).shell() == (
        f"cargo install {shlex.quote(hostile)}"
    )


def test_cargo_remove_command_shells_as_cargo_uninstall():
    """Removal is cargo's own verb, pins kept in the package-ID tokens --
    pyinfra's ``cargo uninstall react test@1.1`` fixture shape."""
    cmd = CargoRemoveCommand(packages=("react", "test@1.1", "ansible"))
    assert cmd.shell() == "cargo uninstall react test@1.1 ansible"


def test_cargo_remove_command_omits_empty_lists():
    """With no tokens the command still stands bare, never with a dangling
    space."""
    assert CargoRemoveCommand(packages=()).shell() == "cargo uninstall"


def test_cargo_remove_command_quotes_a_hostile_token():
    """Every uninstall token is ``shlex``-quoted on top of pyinfra's
    verbatim shape."""
    hostile = "evil; rm -rf /"
    assert CargoRemoveCommand(packages=(hostile,)).shell() == (
        f"cargo uninstall {shlex.quote(hostile)}"
    )


def test_cargo_commands_are_elevated_by_sudo():
    """The commands need no root (they act on the connecting user's cargo
    tree), but the driver's elevation plumbing works the same when a request
    is flagged (the run-as user comes from the inventory ``privilege``,
    never the operation)."""
    cmd = CargoInstallCommand(packages=("ripgrep",))
    assert command_shell(cmd) == "cargo install ripgrep"
    assert command_shell(cmd, sudo=True) == "sudo cargo install ripgrep"
    remove = CargoRemoveCommand(packages=("ripgrep",))
    assert command_shell(remove, su=True) == f"su -c {shlex.quote(remove.shell())}"


# --- Operation: idempotence (pyinfra's operation fixtures verbatim) --------------


def _ctx(host: SimulatedHost) -> OpContext:
    """A prepare-phase context over a simulated host."""
    return OpContext(host=host, facts=FactCache(host, phase="prepare"))


async def test_cargo_packages_installs_per_pyinfra_add_fixture():
    """pyinfra's ``cargo.packages`` ``add_packages`` fixture verbatim (its
    ``@`` pins spelled as reemote's ``=`` pins): git sits at 1.0 and fails
    the ``git=1.1`` exact-version check, react is absent, test@1.1 is
    present -- one ``cargo install git@1.1 react`` command results, carried
    with ``--force`` because the moved git pin overwrites the binary the
    1.0 install left (a deliberate divergence: pyinfra's command would
    fail the same way on a real host)."""
    host = SimulatedHost(name="h", cargo_packages={"git": ["1.0"], "test": ["1.1"]})
    ctx = _ctx(host)
    commands = [
        c
        async for c in cargo.packages(
            ctx, packages=("git=1.1", "react", "test=1.1")
        )
    ]
    assert ctx.changed
    assert ctx.reason == "2 package(s) to install"
    assert commands == [
        CargoInstallCommand(packages=("git@1.1", "react"), force=True)
    ]


async def test_cargo_packages_no_change_when_installed():
    """An unpinned request is present when the crate has *any* installed
    version."""
    host = SimulatedHost(name="h", cargo_packages={"ripgrep": ["14.1.0"]})
    ctx = _ctx(host)
    commands = [c async for c in cargo.packages(ctx, packages=("ripgrep",))]
    assert not ctx.changed
    assert ctx.reason == "all packages present"  # recorded even when unchanged
    assert commands == []


async def test_cargo_packages_latest_pin_counts_as_unpinned():
    """``=latest`` asks for the newest crate, so an explicit pin of it is
    present like no pin at all."""
    host = SimulatedHost(name="h", cargo_packages={"ripgrep": ["14.1.0"]})
    ctx = _ctx(host)
    commands = [
        c async for c in cargo.packages(ctx, packages=("ripgrep=latest",))
    ]
    assert not ctx.changed
    assert commands == []


async def test_cargo_packages_pin_present_only_on_exact_version():
    """A pinned ``<pkg>=<version>`` is present only when that *exact*
    version is installed (the ``ensure_packages`` rule apt/npm/gem follow);
    the other pin installs as its own token, with ``--force`` because it
    moves the crate already installed at 14.1.0 onto another binary."""
    host = SimulatedHost(name="h", cargo_packages={"ripgrep": ["14.1.0"]})
    ctx = _ctx(host)
    commands = [
        c async for c in cargo.packages(ctx, packages=("ripgrep=14.1.0",))
    ]
    assert not ctx.changed
    assert commands == []
    commands = [
        c async for c in cargo.packages(ctx, packages=("ripgrep=15.0.0",))
    ]
    assert commands == [
        CargoInstallCommand(packages=("ripgrep@15.0.0",), force=True)
    ]


async def test_cargo_packages_fresh_batch_needs_no_force():
    """A batch of crates none of which is installed yet runs unprompted --
    ``--force`` only appears once a requested name would overwrite an
    existing binary."""
    host = SimulatedHost(name="h", cargo_packages={"other": ["1.0"]})
    ctx = _ctx(host)
    commands = [
        c
        async for c in cargo.packages(ctx, packages=("ripgrep", "zed=1.0"))
    ]
    assert commands == [CargoInstallCommand(packages=("ripgrep", "zed@1.0"))]


async def test_cargo_packages_accepts_a_bare_string():
    """``packages="<crate>"`` is shorthand for a one-item list, pyinfra's
    own."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [c async for c in cargo.packages(ctx, packages="ripgrep")]
    assert commands == [CargoInstallCommand(packages=("ripgrep",))]


async def test_cargo_packages_none_is_a_noop():
    """pyinfra returns without a command when ``packages`` is None."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [c async for c in cargo.packages(ctx)]
    assert not ctx.changed
    assert ctx.reason == "no packages requested"
    assert commands == []


# --- Operation: latest (pyinfra's documented non-idempotence) ---------------------


async def test_cargo_packages_latest_not_installed_installs():
    """With nothing installed the request is an install, not an upgrade --
    one command."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [
        c async for c in cargo.packages(ctx, packages=("ripgrep",), latest=True)
    ]
    assert commands == [CargoInstallCommand(packages=("ripgrep",))]
    assert ctx.reason == "1 package(s) to install"


async def test_cargo_packages_latest_upgrades_installed_unpinned():
    """An installed, unversioned crate re-runs ``cargo install`` even at the
    newest version -- the documented non-idempotence (no registry to compare
    against), through pyinfra's same-form ``upgrade_command``."""
    host = SimulatedHost(name="h", cargo_packages={"ripgrep": ["14.1.0"]})
    ctx = _ctx(host)
    commands = [
        c async for c in cargo.packages(ctx, packages=("ripgrep",), latest=True)
    ]
    assert ctx.changed
    assert ctx.reason == "1 package(s) to upgrade"
    assert commands == [CargoInstallCommand(packages=("ripgrep",), force=True)]


async def test_cargo_packages_latest_batches_install_before_upgrade():
    """pyinfra emits the install and upgrade commands separately, in that
    order (both the same ``cargo install`` form); a pinned request joins the
    install batch when missing but is never an upgrade candidate."""
    host = SimulatedHost(name="h", cargo_packages={"foo": ["1.0"]})
    ctx = _ctx(host)
    commands = [
        c
        async for c in cargo.packages(
            ctx,
            packages=("foo", "new-crate", "pin-crate=1"),
            latest=True,
        )
    ]
    assert commands == [
        CargoInstallCommand(packages=("new-crate", "pin-crate@1")),
        CargoInstallCommand(packages=("foo",), force=True),
    ]
    assert ctx.reason == "2 package(s) to install; 1 package(s) to upgrade"


# --- Operation: removal (version-aware, pyinfra's fixture verbatim) ---------------


async def test_cargo_packages_removes_per_pyinfra_remove_fixture():
    """pyinfra's ``cargo.packages`` ``remove_packages`` fixture verbatim:
    the ``git=1.1`` pin does not match git-at-1.0 and is spared, a matching
    pin keeps its ``@`` token, bare names remove on any version --
    ``cargo uninstall react test@1.1 ansible``."""
    host = SimulatedHost(
        name="h",
        cargo_packages={
            "git": ["1.0"],
            "react": ["1"],
            "test": ["1.1"],
            "ansible": ["1.2.1.0"],
        },
    )
    ctx = _ctx(host)
    commands = [
        c
        async for c in cargo.packages(
            ctx,
            packages=("git=1.1", "react", "test=1.1", "ansible"),
            present=False,
        )
    ]
    assert ctx.changed
    assert ctx.reason == "3 package(s) to remove"
    assert commands == [
        CargoRemoveCommand(packages=("react", "test@1.1", "ansible"))
    ]


async def test_cargo_packages_removal_of_an_absent_package_is_a_noop():
    """A pin that is not installed and a name never installed are both
    spared (pyinfra's ``noop`` for ``package ... is not installed``)."""
    host = SimulatedHost(name="h", cargo_packages={"git": ["1.0"]})
    ctx = _ctx(host)
    commands = [
        c
        async for c in cargo.packages(
            ctx, packages=("git=9.9.9", "absent"), present=False
        )
    ]
    assert not ctx.changed
    assert ctx.reason == "no packages to remove"
    assert commands == []


async def test_cargo_packages_removal_dedups_a_repeated_token():
    """The same pin requested twice contributes one uninstall token, in the
    order the request spelled it."""
    host = SimulatedHost(name="h", cargo_packages={"test": ["1.1"]})
    ctx = _ctx(host)
    commands = [
        c
        async for c in cargo.packages(
            ctx, packages=("test=1.1", "test=1.1"), present=False
        )
    ]
    assert commands == [CargoRemoveCommand(packages=("test@1.1",))]
    assert ctx.reason == "1 package(s) to remove"


async def test_cargo_packages_absorbs_the_pyinfra_globals():
    """``**kwargs`` takes pyinfra's global arguments and any unknown keyword
    with no effect, like every sibling module."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [
        c
        async for c in cargo.packages(
            ctx, packages=("ripgrep",), untar=False, clean=False, quote=True
        )
    ]
    assert commands == [CargoInstallCommand(packages=("ripgrep",))]


# --- Operation: SimulatedHost seam dispatch ----------------------------------------


async def test_cargo_install_seam_records_versions():
    """``send_command`` maps ``CargoInstallCommand`` onto the store: a pin
    records exactly that version (replacing any older one, as cargo re-links
    the crate) and a bare name gets the ``[""]`` placeholder."""
    host = SimulatedHost(name="h", cargo_packages={"ripgrep": ["14.1.0"]})
    await send_command(
        host,
        CargoInstallCommand(packages=("ripgrep@15.0.0", "cargo-edit")),
    )
    assert host.cargo_packages == {"ripgrep": ["15.0.0"], "cargo-edit": [""]}


async def test_cargo_install_seam_bare_keeps_recorded_version():
    """An installed crate re-served as a bare name keeps its recorded
    version: the simulation has no registry to resolve a newer one."""
    host = SimulatedHost(name="h", cargo_packages={"ripgrep": ["14.1.0"]})
    await send_command(host, CargoInstallCommand(packages=("ripgrep",)))
    assert host.cargo_packages == {"ripgrep": ["14.1.0"]}


async def test_cargo_remove_seam_is_version_aware():
    """A bare name pops the whole entry; a ``<crate>@<version>`` token drops
    only that version, keeping the crate's other installed versions (the
    counterpart of the version-aware removal gate); an absent token is
    tolerated."""
    host = SimulatedHost(
        name="h",
        cargo_packages={"ripgrep": ["14.1.0", "13.0.0"], "zee": ["0.4.0"]},
    )
    await send_command(host, CargoRemoveCommand(packages=("ripgrep@13.0.0",)))
    assert host.cargo_packages == {"ripgrep": ["14.1.0"], "zee": ["0.4.0"]}
    await send_command(host, CargoRemoveCommand(packages=("zee",)))
    assert host.cargo_packages == {"ripgrep": ["14.1.0"]}
    await send_command(host, CargoRemoveCommand(packages=("absent", "gone@1")))
    assert host.cargo_packages == {"ripgrep": ["14.1.0"]}


# --- Operation: end to end ---------------------------------------------------------

# One inventory entry, so ``run`` materializes one simulated host through the
# injected factory and hands it back for the state assertions.
INVENTORY = [
    {"host": "h", "family": "Ubuntu", "connection": {"address": "127.0.0.1", "username": "u"}}
]


def install_deploy(host):
    yield op(
        cargo.packages,
        name="Install cargo crates",
        packages=("ripgrep=14.1.0", "cargo-edit"),
    )


async def test_cargo_packages_end_to_end_install_then_noop():
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
    assert host.cargo_packages == {}  # a dry run sends nothing

    _plans, report, _hosts = await run(INVENTORY, install_deploy, host_factory=factory)
    log = format_log(report)
    assert "$ cargo install ripgrep@14.1.0 cargo-edit" in log
    assert host.cargo_packages == {"ripgrep": ["14.1.0"], "cargo-edit": [""]}

    plans2, report2, _hosts = await run(INVENTORY, install_deploy, host_factory=factory)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "all packages present"
    log2 = format_log(report2)
    assert "[no change: all packages present]" in log2
    assert "$ " not in log2


async def test_cargo_packages_end_to_end_removal_cycle():
    """One deploy installs then removes: the apply log shows both shells and
    the removal -- a fresh step with its own fact cache -- sees the install's
    mutation."""

    def cycle_deploy(host):
        yield op(cargo.packages, name="Install tool", packages=("ripgrep",))
        yield op(
            cargo.packages,
            name="Remove tool",
            packages=("ripgrep",),
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
    assert "$ cargo install ripgrep" in log
    assert "$ cargo uninstall ripgrep" in log
    assert host.cargo_packages == {}


async def test_cargo_packages_latest_stays_changed_across_runs():
    """The documented non-idempotence through the driver: ``latest=True``
    re-plans the upgrade even after the host converged, because nothing
    tells the operation whether a newer crate version exists."""

    def update_deploy(host):
        yield op(
            cargo.packages,
            name="Update crates",
            packages=("ripgrep",),
            latest=True,
        )

    host = SimulatedHost(name="h", cargo_packages={"ripgrep": ["14.1.0"]})

    def factory(entry: dict) -> SimulatedHost:
        return host

    plans1, _report, _hosts = await run(
        INVENTORY, update_deploy, host_factory=factory
    )
    assert plans1["h"][0].changed
    plans2, _report2, _hosts = await run(
        INVENTORY, update_deploy, host_factory=factory
    )
    assert plans2["h"][0].changed
    assert plans2["h"][0].reason == "1 package(s) to upgrade"


# --- Operation: CI/source option rendering --------------------------------------


def test_cargo_install_command_renders_the_lockfile_and_feature_flags():
    """``locked``/``features``/``all_features``/``no_default_features`` each
    render once, in the fixed order, joined as cargo spells them -- the
    comma list of ``--features`` and the two standalone flags."""
    cmd = CargoInstallCommand(
        packages=("ripgrep",),
        locked=True,
        features=("mmap", "pretty"),
        all_features=True,
        no_default_features=True,
    )
    assert cmd.shell() == (
        "cargo install --locked --features mmap,pretty --all-features "
        "--no-default-features ripgrep"
    )


def test_cargo_install_command_renders_a_git_source_and_ref():
    """With ``git`` the URL and one ref selector lead the flags, and the
    crate names follow -- cargo's ``cargo install --git <url> [crate...]``
    grammar, every token quoted."""
    cmd = CargoInstallCommand(
        packages=("rustup",), git="https://g/rustup.git", branch="main"
    )
    assert cmd.shell() == (
        "cargo install --git https://g/rustup.git --branch main rustup"
    )


def test_cargo_install_command_scopes_under_a_cargo_home():
    """``cargo_home`` rides as a ``shlex``-quoted ``CARGO_HOME=<q> `` env
    prefix ahead of the whole command, mirroring the fact read."""
    cmd = CargoInstallCommand(packages=("ripgrep",), cargo_home="/opt/cargo")
    assert cmd.shell() == "CARGO_HOME=/opt/cargo cargo install ripgrep"


def test_cargo_remove_command_scopes_under_a_cargo_home():
    """Uninstall reaches the same relocated tree as the install/list."""
    cmd = CargoRemoveCommand(packages=("ripgrep",), cargo_home="/opt/cargo")
    assert cmd.shell() == "CARGO_HOME=/opt/cargo cargo uninstall ripgrep"


# --- Operation: source/CI behavior -----------------------------------------------


async def test_cargo_packages_rides_ci_flags_on_every_install():
    """``locked``/``features`` reach the install command of a request -- the
    feature selection chooses how the crate is built, not the version
    comparison -- and ``cargo_home`` scopes both the fact read and command."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [
        c
        async for c in cargo.packages(
            ctx,
            packages=("ripgrep",),
            locked=True,
            features=("mmap",),
            cargo_home="/opt/cargo",
        )
    ]
    assert commands == [
        CargoInstallCommand(
            packages=("ripgrep",), locked=True, features=("mmap",),
            cargo_home="/opt/cargo",
        )
    ]
    assert commands[0].shell() == (
        "CARGO_HOME=/opt/cargo cargo install --locked --features mmap ripgrep"
    )


async def test_cargo_packages_git_always_reinstalls_by_name():
    """A ``git`` source is non-idempotent and names packages inside the repo,
    so any pin drops to its bare name; the fact is never read for a
    comparison and the command always carries ``--force``, because a
    re-install over the binary the previous git install left is exactly
    what cargo refuses unprompted. A git request with ``present=False`` is
    still an install (``git`` only means the install direction)."""
    host = SimulatedHost(name="h", cargo_packages={"rustup": ["1.0"]})
    ctx = _ctx(host)
    commands = [
        c
        async for c in cargo.packages(
            ctx, packages=("rustup=1.0",), git="https://g/rustup.git"
        )
    ]
    assert ctx.changed
    assert ctx.reason == "install rustup from git"
    assert commands == [
        CargoInstallCommand(
            packages=("rustup",), git="https://g/rustup.git", force=True
        )
    ]


async def test_cargo_packages_git_without_names_installs_repo_default():
    """A ``git`` request with no crate names installs the repository's
    default package -- one bare ``cargo install --git`` command, forced
    like every git install."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [
        c async for c in cargo.packages(ctx, git="https://g/x.git", rev="abc123")
    ]
    assert ctx.reason == "install from git"
    assert commands == [
        CargoInstallCommand(
            packages=(), git="https://g/x.git", rev="abc123", force=True
        )
    ]


async def test_cargo_packages_ref_selectors_are_mutually_exclusive():
    """cargo allows a single ref per git install, so a request carrying two
    is refused with ``ValueError`` before any command (the design note's
    ``OperationError`` transliterated to the gem/npm validation precedent)."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    with pytest.raises(ValueError, match="only one of branch/tag/rev"):
        [
            c
            async for c in cargo.packages(
                ctx, git="https://g/x.git", branch="main", tag="v1"
            )
        ]


async def test_cargo_packages_ref_selector_without_git_is_refused():
    """A ``branch``/``tag``/``rev`` without a ``git`` source selects nothing,
    so it raises ``ValueError`` -- cargo's ref flags pair with ``--git``."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    with pytest.raises(ValueError, match="only together with git"):
        [c async for c in cargo.packages(ctx, packages=("ripgrep",), rev="abc")]


async def test_cargo_packages_ignores_git_on_removal():
    """``present=False`` uninstalls by name alone; ``git`` only means the
    install direction, so a removal ignores it and the fact gate decides."""
    host = SimulatedHost(name="h", cargo_packages={"ripgrep": ["14.1.0"]})
    ctx = _ctx(host)
    commands = [
        c
        async for c in cargo.packages(
            ctx, packages=("ripgrep",), present=False, git="https://g/x.git"
        )
    ]
    assert commands == [CargoRemoveCommand(packages=("ripgrep",))]


# --- Fact: cargo_home parity ------------------------------------------------------


async def test_cargo_packages_command_scopes_the_read():
    """``CargoPackages.command`` renders the ``CARGO_HOME=<q> `` prefix, and
    the real-transport read issues it so the listing covers one tree."""
    assert CargoPackages.command() == CargoPackages.COMMAND
    assert CargoPackages.command("/opt/cargo") == (
        "CARGO_HOME=/opt/cargo cargo install --list"
    )
    host = CannedShellHost("ripgrep v14.1.0:\n    /x/rg\n")
    assert await CargoPackages.process(host, cargo_home="/opt/cargo") == [
        _entry("ripgrep", "14.1.0")
    ]
    assert host.last_shell == "CARGO_HOME=/opt/cargo cargo install --list"


# --- Operation: cargo.cache_clean --------------------------------------------------


def test_cargo_clean_command_shells_cargo_clean():
    """A project clean is cargo's own verb against the manifest path; a
    trailing slash on ``path`` is normalized away."""
    assert CargoCleanCommand(path="/srv/app").shell() == (
        "cargo clean --manifest-path /srv/app/Cargo.toml"
    )
    assert CargoCleanCommand(path="/srv/app/", cargo_home="/opt/cargo").shell() == (
        "CARGO_HOME=/opt/cargo cargo clean --manifest-path /srv/app/Cargo.toml"
    )


def test_cargo_cache_remove_command_guards_each_directory():
    """The default removes both caches, each behind an ``if [ -d ]`` test so
    the compound exits 0 on a host that never populated one; the resolution
    honors ``$CARGO_HOME``/``~/.cargo``."""
    assert CargoCacheRemoveCommand().shell() == (
        'CFG="${CARGO_HOME:-$HOME/.cargo}"; '
        'if [ -d "$CFG/registry" ]; then rm -rf "$CFG/registry"; fi; '
        'if [ -d "$CFG/git" ]; then rm -rf "$CFG/git"; fi'
    )


def test_cargo_cache_remove_command_touches_only_selected_caches():
    """Selecting one cache renders one ``rm``; an explicit ``cargo_home``
    replaces the resolution."""
    assert CargoCacheRemoveCommand(registry=False, git=True, cargo_home="/o").shell() == (
        'CFG=/o; if [ -d "$CFG/git" ]; then rm -rf "$CFG/git"; fi'
    )


async def test_cargo_cache_clean_path_direction():
    """A ``path`` asks for a project clean, always (stateless) -- one
    ``CargoCleanCommand`` and a reason."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [c async for c in cargo.cache_clean(ctx, path="/srv/app")]
    assert ctx.changed
    assert ctx.reason == "clean build artifacts of /srv/app"
    assert commands == [CargoCleanCommand(path="/srv/app")]


async def test_cargo_cache_clean_cache_direction():
    """With no path the selected caches are emptied in one command; the
    ``path`` branch wins when both would apply."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [c async for c in cargo.cache_clean(ctx, clean_registry=False)]
    assert ctx.changed
    assert ctx.reason == "clean git cache"
    assert commands == [CargoCacheRemoveCommand(registry=False, git=True)]


async def test_cargo_cache_clean_nothing_selected_is_a_noop():
    """No path and neither cache selected yields nothing and stays
    unchanged."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [
        c
        async for c in cargo.cache_clean(
            ctx, clean_registry=False, clean_git=False
        )
    ]
    assert not ctx.changed
    assert ctx.reason == "no caches or project selected"
    assert commands == []


async def test_cargo_cache_clean_seam_is_a_noop():
    """The simulation models no cache contents, so both clean descriptors
    dispatch without touching any store (like ``GoCleanCommand``)."""
    host = SimulatedHost(name="h", cargo_packages={"ripgrep": ["14.1.0"]})
    await send_command(host, CargoCleanCommand(path="/srv/app"))
    await send_command(host, CargoCacheRemoveCommand())
    assert host.cargo_packages == {"ripgrep": ["14.1.0"]}


# --- Exports ----------------------------------------------------------------------


def test_cargo_module_exports():
    """The package re-exports the fact, its entry type, the operation and
    both descriptors, so ``cargo.CargoPackages`` and ``cargo.packages``
    resolve, and the top-level package makes ``from reemote import
    CargoPackages`` work, exports the descriptors and keeps the ``cargo``
    namespace importable."""
    assert cargo.CargoPackages is CargoPackages
    assert cargo.CargoPackageInfo is CargoPackageInfo
    assert cargo.CargoConfig is CargoConfig
    assert cargo.packages.__name__ == "packages"
    assert cargo.CargoInstallCommand is CargoInstallCommand
    assert cargo.CargoRemoveCommand is CargoRemoveCommand
    assert cargo.cache_clean.__name__ == "cache_clean"
    assert cargo.CargoCleanCommand is CargoCleanCommand
    assert cargo.CargoCacheRemoveCommand is CargoCacheRemoveCommand
    assert cargo.cargo.CargoPackages is CargoPackages
    import reemote

    assert reemote.CargoPackages is CargoPackages
    assert reemote.CargoPackageInfo is CargoPackageInfo
    assert reemote.CargoConfig is CargoConfig
    assert reemote.CargoInstallCommand is CargoInstallCommand
    assert reemote.CargoRemoveCommand is CargoRemoveCommand
    assert reemote.CargoCleanCommand is CargoCleanCommand
    assert reemote.CargoCacheRemoveCommand is CargoCacheRemoveCommand
    assert reemote.cargo is cargo
    for name in (
        "CargoCacheRemoveCommand",
        "CargoCleanCommand",
        "CargoConfig",
        "CargoInstallCommand",
        "CargoPackageInfo",
        "CargoPackages",
        "CargoRemoveCommand",
        "cargo",
    ):
        assert name in reemote.__all__
    # The operation functions are reached through their namespace, never the
    # top level (the dnf.packages rule).
    assert "packages" not in reemote.__all__
    assert "cache_clean" not in reemote.__all__
