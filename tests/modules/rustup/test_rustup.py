# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the rustup module: the four rustup facts and three operations.

Covers ``reemote.modules.rustup.rustup`` -- ``RustupToolchains`` (toolchain
name -> ``RustupToolchainInfo``), ``RustupDefaultToolchain`` (the default
name projected out of it), ``RustupTargets`` and ``RustupComponents`` (one
toolchain's sorted installed triples/components) -- both over the
real-transport path (a ``CannedShellHost`` returning rustup's own output
shapes: the ``(default)``/``(active)``/``(active, default)`` markers, the
target-qualified component rows) and through the ``SimulatedHost`` seams,
plus the two parsing/grammar fixes over the original design (the trailing
marker a naive ``(default)`` substring test cannot detach, the ``+toolchain``
selector placed *before* the subcommand where rustup parses it), the
``RUSTUP_HOME=`` env-prefix quoting, the sort guarantees, the
``gather_fact`` fan-out and the exports.

The three operations -- ``rustup.toolchains``/``rustup.targets``/
``rustup.components`` -- are exercised over their seven command
descriptors' rendering, their fact-gated idempotence (install/uninstall,
add/remove, the default change), the two up-front ``ValueError`` guards, the
``rustup_home``/``toolchain`` scope forwarding, the ``SimulatedHost`` seam
dispatch (including the store reconciliation on a toolchain removal) and end
to end through ``run``.

``asyncio_mode = "auto"`` lets the async tests run without decorators.
"""

import shlex

import pytest

from reemote.core import FactCache, OpContext, op
from reemote.drivers import command_shell, format_log, gather_fact, run, send_command
from reemote.hosts import Host
from reemote.modules import rustup
from reemote.modules.rustup.rustup import (
    RustupComponentAddCommand,
    RustupComponentRemoveCommand,
    RustupComponents,
    RustupDefaultCommand,
    RustupDefaultToolchain,
    RustupTargetAddCommand,
    RustupTargetRemoveCommand,
    RustupTargets,
    RustupToolchainInfo,
    RustupToolchainInstallCommand,
    RustupToolchainRemoveCommand,
    RustupToolchains,
    _rustup_shell,
    _toolchain_channel,
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


class CountingShellHost(CannedShellHost):
    """Like ``CannedShellHost`` but records every shell, so a composing fact
    can be shown to issue exactly one."""

    def __init__(self, stdout: str):
        super().__init__(stdout)
        self.shells: list[str] = []

    async def run_shell(self, shell: str) -> str:
        self.shells.append(shell)
        return self._stdout


# --- Shared helpers ------------------------------------------------------------


def test_toolchain_channel_requires_a_whole_leading_field():
    """The three named channels classify from the bare name or the
    ``<channel>-`` field prefix; a custom name that merely *starts* with the
    word stays ``custom`` (the tightened version of the design's bare
    ``startswith``), and versioned installs are ``custom`` too."""
    assert _toolchain_channel("stable") == "stable"
    assert _toolchain_channel("beta-x86_64-unknown-linux-gnu") == "beta"
    assert (
        _toolchain_channel("nightly-2026-05-01-x86_64-unknown-linux-gnu")
        == "nightly"
    )
    # ``nightly-builder`` continues the ``nightly-`` field, which is how
    # rustup itself would resolve the ambiguous name.
    assert _toolchain_channel("nightly-builder") == "nightly"
    assert _toolchain_channel("nightlyish") == "custom"
    assert _toolchain_channel("1.83.0-x86_64-unknown-linux-gnu") == "custom"
    assert _toolchain_channel("my-toolchain") == "custom"


def test_rustup_shell_prefixes_a_quoted_rustup_home():
    """The env-assignment prefix is ``shlex``-quoted, so a home carrying
    shell metacharacters names a directory rather than executing anything;
    without it the command stays bare."""
    assert _rustup_shell("rustup toolchain list") == "rustup toolchain list"
    assert (
        _rustup_shell("rustup toolchain list", rustup_home="/srv/rust")
        == "RUSTUP_HOME=/srv/rust rustup toolchain list"
    )
    hostile = "/srv/$(rm -rf /)"
    assert _rustup_shell("rustup", rustup_home=hostile) == (
        f"RUSTUP_HOME={shlex.quote(hostile)} rustup"
    )


# --- RustupToolchains: command parity -------------------------------------------


def test_rustup_toolchains_command():
    """The bare listing is rustup's own verb; an explicit home prefixes it
    as an env assignment."""
    assert RustupToolchains.command() == "rustup toolchain list"
    assert RustupToolchains.command(rustup_home="/srv/rust") == (
        "RUSTUP_HOME=/srv/rust rustup toolchain list"
    )


# --- RustupToolchains: real-transport parsing ------------------------------------

# rustup's own marker spellings (upstream ``rustup_mode.rs`` renders exactly
# ``(active, default)``, ``(active)`` or ``(default)``): here a directory
# override makes nightly active while stable stays the default.
RUSTUP_OUTPUT = (
    "1.83.0-x86_64-unknown-linux-gnu\n"
    "nightly-x86_64-unknown-linux-gnu (active)\n"
    "stable-x86_64-unknown-linux-gnu (default)\n"
)


async def test_rustup_toolchains_parses_real_markers():
    """Each row names one toolchain; the trailing marker is detached, its
    comma-separated flags decide ``default``, the channel comes from the
    name, and the mapping is sorted by name."""
    host = CannedShellHost(RUSTUP_OUTPUT)
    assert await RustupToolchains.process(host) == {
        "1.83.0-x86_64-unknown-linux-gnu": {
            "default": False,
            "channel": "custom",
        },
        "nightly-x86_64-unknown-linux-gnu": {
            "default": False,
            "channel": "nightly",
        },
        "stable-x86_64-unknown-linux-gnu": {
            "default": True,
            "channel": "stable",
        },
    }
    assert host.last_shell == "rustup toolchain list"


async def test_rustup_toolchains_parses_the_paired_marker():
    """The ``(active, default)`` pair -- modern rustup's spelling when the
    default toolchain answers in its own directory -- reports one default,
    and the name keeps no marker fragment: the naive substring/replace
    parse would glue ``(active,`` onto it."""
    parsed = RustupToolchains.parse(
        ["stable-x86_64-unknown-linux-gnu (active, default)"]
    )
    assert parsed == {
        "stable-x86_64-unknown-linux-gnu": {
            "default": True,
            "channel": "stable",
        }
    }


async def test_rustup_toolchains_sorts_and_skips_noise():
    """Blank lines contribute nothing, a marker-only line leaves no name
    behind, and the keys come out sorted however rustup printed them."""
    parsed = RustupToolchains.parse(
        [
            "",
            "  nightly-x86_64-unknown-linux-gnu  ",
            "(default)",
            "1.75.0-aarch64-unknown-linux-gnu (default)",
        ]
    )
    assert list(parsed) == [
        "1.75.0-aarch64-unknown-linux-gnu",
        "nightly-x86_64-unknown-linux-gnu",
    ]
    assert parsed["1.75.0-aarch64-unknown-linux-gnu"]["default"] is True
    assert parsed["nightly-x86_64-unknown-linux-gnu"]["default"] is False


async def test_rustup_toolchains_no_toolchains_reads_empty():
    """A host with rustup but nothing installed prints no rows -- the
    design's ``default = dict`` answer."""
    assert RustupToolchains.parse([]) == {}
    assert await RustupToolchains.process(CannedShellHost("")) == {}


async def test_rustup_toolchains_rustup_home_reaches_the_shell():
    """The ``rustup_home`` argument renders into the issued command, quoted."""
    host = CannedShellHost(RUSTUP_OUTPUT)
    await RustupToolchains.process(host, rustup_home="/srv/rust home")
    assert host.last_shell == (
        "RUSTUP_HOME='/srv/rust home' rustup toolchain list"
    )


# --- RustupToolchains: SimulatedHost seam ---------------------------------------


async def test_rustup_toolchains_reads_the_simulated_seam():
    """A SimulatedHost answers from its ``rustup_toolchains`` name ->
    is-default store, deriving the channel from the name so the store
    cannot contradict it; the default empty store reads ``{}``."""
    host = SimulatedHost(
        name="h",
        rustup_toolchains={"stable": True, "1.83.0-x86_64-unknown-linux-gnu": False},
    )
    assert await RustupToolchains.process(host) == {
        "1.83.0-x86_64-unknown-linux-gnu": {
            "default": False,
            "channel": "custom",
        },
        "stable": {"default": True, "channel": "stable"},
    }
    assert await RustupToolchains.process(SimulatedHost(name="h")) == {}


async def test_rustup_toolchains_seam_returns_copies():
    """Mutating a returned entry never touches the simulated state, so the
    seam hands back freshly built dicts; ``rustup_home`` cannot narrow a
    store that models the whole host (the ``GoInstalledSDKs`` rule)."""
    host = SimulatedHost(name="h", rustup_toolchains={"stable": True})
    toolchains = await RustupToolchains.process(host, rustup_home="/ignored")
    toolchains["stable"]["default"] = False
    toolchains["bogus"] = {"default": False, "channel": "custom"}
    assert await RustupToolchains.process(host) == {
        "stable": {"default": True, "channel": "stable"}
    }
    assert host.rustup_toolchains == {"stable": True}


# --- RustupDefaultToolchain -------------------------------------------------------


async def test_rustup_default_toolchain_issues_one_listing():
    """The convenience fact composes ``RustupToolchains`` -- exactly one
    shell on the real transport, the project's ``ShortFactBase`` equivalent
    with no command of its own."""
    assert not hasattr(RustupDefaultToolchain, "COMMAND")
    host = CountingShellHost(RUSTUP_OUTPUT)
    assert await RustupDefaultToolchain.process(host) == (
        "stable-x86_64-unknown-linux-gnu"
    )
    assert host.shells == ["rustup toolchain list"]


async def test_rustup_default_toolchain_reads_paired_marker():
    """The ``(active, default)`` spelling resolves the same name."""
    host = CannedShellHost("nightly-2026-05-01-x86_64-unknown-linux-gnu (active, default)\n")
    assert await RustupDefaultToolchain.process(host) == (
        "nightly-2026-05-01-x86_64-unknown-linux-gnu"
    )


async def test_rustup_default_toolchain_absent_reads_none():
    """An active-only marker is not a default, and an empty listing has no
    default either -- both answer ``None`` rather than a bare toolchain."""
    assert await RustupDefaultToolchain.process(
        CannedShellHost("stable-x86_64-unknown-linux-gnu (active)\n")
    ) is None
    assert await RustupDefaultToolchain.process(CannedShellHost("")) is None


async def test_rustup_default_toolchain_projects_the_seam():
    """On a SimulatedHost the answer is projected from the same
    ``rustup_toolchains`` store the sibling fact reads, so the two can
    never disagree; an unseeded host has no default."""
    host = SimulatedHost(
        name="h",
        rustup_toolchains={"nightly": False, "stable": True},
    )
    assert await RustupDefaultToolchain.process(host) == "stable"
    assert await RustupDefaultToolchain.process(SimulatedHost(name="h")) is None


async def test_rustup_default_toolchain_agrees_with_the_listing():
    """The composition property, stated directly: the projected name is
    always the listing's one marked entry, over the same shell output."""
    host = CannedShellHost(RUSTUP_OUTPUT)
    default = await RustupDefaultToolchain.process(host)
    marked = [
        name
        for name, info in (await RustupToolchains.process(host)).items()
        if info["default"]
    ]
    assert default == marked[0]


# --- RustupTargets -----------------------------------------------------------------


def test_rustup_targets_command_places_the_selector_first():
    """The ``+<toolchain>`` token is rustup's *top-level* argument, so it
    precedes the subcommand (the design's post-subcommand placement is
    grammar rustup rejects); without one the listing covers the active
    toolchain."""
    assert RustupTargets.command() == "rustup target list --installed"
    assert RustupTargets.command(toolchain="stable") == (
        "rustup +stable target list --installed"
    )
    assert RustupTargets.command(rustup_home="/srv/rust") == (
        "RUSTUP_HOME=/srv/rust rustup target list --installed"
    )
    assert RustupTargets.command(toolchain="stable", rustup_home="/srv/rust") == (
        "RUSTUP_HOME=/srv/rust rustup +stable target list --installed"
    )


def test_rustup_targets_command_quotes_a_hostile_toolchain():
    """A toolchain name is user-supplied, so the whole ``+``-selector is
    ``shlex``-quoted into one token."""
    hostile = "stable; rm -rf /"
    assert RustupTargets.command(toolchain=hostile) == (
        f"rustup {shlex.quote('+' + hostile)} target list --installed"
    )


async def test_rustup_targets_parses_sorted_deduped_triples():
    """Every non-blank row is one installed triple; the promised sort (and
    dedup) are actually applied, blanks stripped away."""
    output = (
        "x86_64-unknown-linux-gnu\n"
        "\n"
        "  wasm32-unknown-unknown  \n"
        "aarch64-unknown-linux-gnu\n"
        "wasm32-unknown-unknown"
    )
    assert RustupTargets.parse(output.splitlines()) == [
        "aarch64-unknown-linux-gnu",
        "wasm32-unknown-unknown",
        "x86_64-unknown-linux-gnu",
    ]
    assert RustupTargets.parse([]) == []


async def test_rustup_targets_real_transport():
    """The canned host sees the built command verbatim and answers parsed
    triples; an uninstalled toolchain's error output reads ``[]``."""
    host = CannedShellHost("wasm32-unknown-unknown\nx86_64-unknown-linux-gnu\n")
    assert await RustupTargets.process(host, toolchain="nightly") == [
        "wasm32-unknown-unknown",
        "x86_64-unknown-linux-gnu",
    ]
    assert host.last_shell == "rustup +nightly target list --installed"
    assert await RustupTargets.process(CannedShellHost("")) == []


async def test_rustup_targets_reads_the_simulated_seam():
    """The store is keyed by toolchain name, the empty string holding the
    *active* toolchain's targets -- what a request without ``toolchain``
    asks for; an unseeded toolchain reads ``[]``."""
    host = SimulatedHost(
        name="h",
        rustup_targets={
            "": ["wasm32-unknown-unknown"],
            "nightly": ["wasm32-unknown-unknown", "x86_64-unknown-linux-gnu"],
        },
    )
    assert await RustupTargets.process(host) == ["wasm32-unknown-unknown"]
    assert await RustupTargets.process(host, toolchain="nightly") == [
        "wasm32-unknown-unknown",
        "x86_64-unknown-linux-gnu",
    ]
    assert await RustupTargets.process(host, toolchain="beta") == []


async def test_rustup_targets_seam_returns_copies():
    """Mutating the returned list never touches the simulated store;
    ``rustup_home`` cannot narrow it (the ``GoInstalledSDKs`` rule)."""
    host = SimulatedHost(name="h", rustup_targets={"": ["x86_64-unknown-linux-gnu"]})
    targets = await RustupTargets.process(host, rustup_home="/ignored")
    targets.append("tampered")
    assert await RustupTargets.process(host) == ["x86_64-unknown-linux-gnu"]
    assert host.rustup_targets == {"": ["x86_64-unknown-linux-gnu"]}


# --- RustupComponents ---------------------------------------------------------------


def test_rustup_components_command_matches_the_targets_rule():
    """Same grammar as ``RustupTargets`` around rustup's own ``component
    list --installed`` verb."""
    assert RustupComponents.command() == "rustup component list --installed"
    assert RustupComponents.command(toolchain="nightly", rustup_home="/srv/rust") == (
        "RUSTUP_HOME=/srv/rust rustup +nightly component list --installed"
    )


async def test_rustup_components_keeps_target_qualified_names():
    """Rows carry rustup's target-qualified component names verbatim --
    the triple suffix is part of a component's identity -- stripped,
    de-duplicated and sorted."""
    output = (
        "rustfmt-x86_64-unknown-linux-gnu\n"
        "llvm-tools-preview\n"
        "  clippy-x86_64-unknown-linux-gnu\n"
        "rustfmt-x86_64-unknown-linux-gnu"
    )
    assert RustupComponents.parse(output.splitlines()) == [
        "clippy-x86_64-unknown-linux-gnu",
        "llvm-tools-preview",
        "rustfmt-x86_64-unknown-linux-gnu",
    ]


async def test_rustup_components_real_transport():
    """The canned host sees the built command verbatim and answers sorted
    component names."""
    host = CannedShellHost("cargo-x86_64-unknown-linux-gnu\nrust-std-wasm32-unknown-unknown\n")
    assert await RustupComponents.process(host, toolchain="stable") == [
        "cargo-x86_64-unknown-linux-gnu",
        "rust-std-wasm32-unknown-unknown",
    ]
    assert host.last_shell == "rustup +stable component list --installed"


async def test_rustup_components_reads_the_simulated_seam():
    """The ``rustup_targets`` keying, one store level down: toolchain name
    (empty string = active) -> installed component names; unseeded reads
    ``[]`` and the returned list is a copy."""
    host = SimulatedHost(
        name="h",
        rustup_components={"": ["clippy", "rustfmt"], "beta": ["llvm-tools-preview"]},
    )
    assert await RustupComponents.process(host) == ["clippy", "rustfmt"]
    assert await RustupComponents.process(host, toolchain="beta") == [
        "llvm-tools-preview"
    ]
    assert await RustupComponents.process(host, toolchain="nightly") == []
    components = await RustupComponents.process(host)
    components.append("tampered")
    assert await RustupComponents.process(host) == ["clippy", "rustfmt"]
    assert host.rustup_components == {"": ["clippy", "rustfmt"], "beta": ["llvm-tools-preview"]}


# --- gather_fact fan-out -------------------------------------------------------------


async def test_gather_fact_rustup_toolchains_fans_out():
    """Across a simulated fleet each host answers from its own seam store,
    keyed by host name."""
    host1 = SimulatedHost(name="web1", rustup_toolchains={"stable": True})
    host2 = SimulatedHost(name="web2")
    answers = await gather_fact([host1, host2], RustupToolchains)
    assert answers == {
        "web1": {"stable": {"default": True, "channel": "stable"}},
        "web2": {},
    }


async def test_gather_fact_default_toolchain_fans_out():
    """The composing fact fans out the same way, projecting each host's own
    default (or ``None``)."""
    host1 = SimulatedHost(name="web1", rustup_toolchains={"nightly": False, "stable": True})
    host2 = SimulatedHost(name="web2", rustup_toolchains={"1.83.0": True})
    answers = await gather_fact([host1, host2], RustupDefaultToolchain)
    assert answers == {"web1": "stable", "web2": "1.83.0"}


async def test_gather_fact_rustup_targets_forwards_arguments():
    """``gather_fact`` passes the ``toolchain`` through to every host's
    ``process``, the ``FactCache.get`` forwarding rule."""
    host1 = SimulatedHost(name="web1", rustup_targets={"nightly": ["wasm32-unknown-unknown"]})
    host2 = SimulatedHost(name="web2", rustup_targets={"nightly": ["wasm32-unknown-unknown"]})
    answers = await gather_fact([host1, host2], RustupTargets, "nightly")
    assert answers == {
        "web1": ["wasm32-unknown-unknown"],
        "web2": ["wasm32-unknown-unknown"],
    }


# --- Operation descriptors: rendering -------------------------------------------


def test_rustup_toolchain_install_command_renders_flags():
    """One install command carries the whole batch, then ``--profile`` and a
    repeated ``--component``/``--target`` per value -- rustup's own flag
    spellings, every token quoted, under an optional ``RUSTUP_HOME``."""
    cmd = RustupToolchainInstallCommand(
        toolchains=("stable", "nightly"),
        profile="minimal",
        components=("clippy",),
        targets=("wasm32-unknown-unknown",),
    )
    assert cmd.shell() == (
        "rustup toolchain install stable nightly --profile=minimal "
        "--component=clippy --target=wasm32-unknown-unknown"
    )
    assert RustupToolchainInstallCommand(toolchains=("stable",)).shell() == (
        "rustup toolchain install stable"
    )
    scoped = RustupToolchainInstallCommand(
        toolchains=("stable",), rustup_home="/srv/rust"
    )
    assert scoped.shell() == "RUSTUP_HOME=/srv/rust rustup toolchain install stable"


def test_rustup_toolchain_remove_and_default_commands():
    """Uninstall is rustup's verb, and the default marker is the bare
    ``rustup default <toolchain>``; both quote and scope."""
    assert RustupToolchainRemoveCommand(toolchains=("nightly",)).shell() == (
        "rustup toolchain uninstall nightly"
    )
    assert RustupDefaultCommand(toolchain="stable").shell() == "rustup default stable"
    assert RustupDefaultCommand(
        toolchain="stable", rustup_home="/srv/rust"
    ).shell() == "RUSTUP_HOME=/srv/rust rustup default stable"


def test_rustup_target_and_component_commands_place_the_selector():
    """The ``+<toolchain>`` selector precedes the subcommand where rustup
    parses it; a bare command (no toolchain) has none, and a batch is one
    add/remove command."""
    assert RustupTargetAddCommand(
        targets=("x86_64-unknown-linux-musl",), toolchain="stable"
    ).shell() == "rustup +stable target add x86_64-unknown-linux-musl"
    assert RustupTargetRemoveCommand(
        targets=("a", "b")
    ).shell() == "rustup target remove a b"
    assert RustupComponentAddCommand(components=("clippy",)).shell() == (
        "rustup component add clippy"
    )
    assert RustupComponentRemoveCommand(
        components=("llvm-tools-preview",), toolchain="nightly"
    ).shell() == "rustup +nightly component remove llvm-tools-preview"


def test_rustup_commands_elevate_via_the_driver():
    """The commands need no root (they act on the connecting user's rustup
    tree), but the elevation plumbing renders when a request is flagged."""
    cmd = RustupToolchainInstallCommand(toolchains=("stable",))
    assert command_shell(cmd) == "rustup toolchain install stable"
    assert command_shell(cmd, sudo=True) == "sudo rustup toolchain install stable"


# --- Operation: rustup.toolchains ------------------------------------------------


def _ctx(host: SimulatedHost) -> OpContext:
    """A prepare-phase context over a simulated host."""
    return OpContext(host=host, facts=FactCache(host, phase="prepare"))


async def test_rustup_toolchains_installs_missing():
    """Only the toolchains absent from the fact are installed, in one batch;
    the default is left alone when it already matches. The store carries the
    qualified names a real ``rustup toolchain list`` prints, so the
    comparison must resolve the request's short aliases against them."""
    host = SimulatedHost(name="h", rustup_toolchains={"stable-x86_64-unknown-linux-gnu": True})
    ctx = _ctx(host)
    commands = [
        c
        async for c in rustup.toolchains(
            ctx, toolchains=("stable", "nightly"), profile="minimal", default="stable"
        )
    ]
    assert ctx.changed
    assert ctx.reason == (
        "toolchain nightly not installed, will install; default already stable"
    )
    assert commands == [
        RustupToolchainInstallCommand(toolchains=("nightly",), profile="minimal")
    ]


async def test_rustup_toolchains_sets_a_differing_default():
    """When the requested default differs from the current one, a
    ``rustup default`` command follows the install batch; the reason names
    the current default exactly as the qualified fact key spells it."""
    host = SimulatedHost(name="h", rustup_toolchains={"stable-x86_64-unknown-linux-gnu": True})
    ctx = _ctx(host)
    commands = [
        c
        async for c in rustup.toolchains(
            ctx, toolchains=("nightly",), default="nightly"
        )
    ]
    assert ctx.changed
    assert ctx.reason == (
        "toolchain nightly not installed, will install; default is "
        "stable-x86_64-unknown-linux-gnu, will set nightly"
    )
    assert commands == [
        RustupToolchainInstallCommand(toolchains=("nightly",)),
        RustupDefaultCommand(toolchain="nightly"),
    ]


async def test_rustup_toolchains_noop_when_present():
    """Installed toolchains and a matching default yield nothing -- a short
    alias counts as installed through its qualified (triple-qualified) fact
    key, the way rustup itself resolves it."""
    host = SimulatedHost(name="h", rustup_toolchains={"stable-x86_64-unknown-linux-gnu": True})
    ctx = _ctx(host)
    commands = [
        c
        async for c in rustup.toolchains(ctx, toolchains=("stable",), default="stable")
    ]
    assert not ctx.changed
    assert ctx.reason == (
        "toolchains stable already installed; default already stable"
    )
    assert commands == []


async def test_rustup_toolchains_removes_installed():
    """``present=False`` uninstalls only the installed subset -- an alias in
    the request selects its qualified store key, and the command keeps
    carrying the alias rustup resolves."""
    host = SimulatedHost(name="h", rustup_toolchains={"stable-x86_64-unknown-linux-gnu": True})
    ctx = _ctx(host)
    commands = [
        c
        async for c in rustup.toolchains(
            ctx, toolchains=("stable", "nightly"), present=False
        )
    ]
    assert ctx.changed
    assert ctx.reason == "toolchain stable installed, will uninstall"
    assert commands == [RustupToolchainRemoveCommand(toolchains=("stable",))]


async def test_rustup_toolchains_alias_matches_only_qualifier_remainders():
    """The leading-field rule is guarded at the separator: a short alias is
    satisfied only by a host-triple remainder, so the bare ``nightly``
    request is NOT satisfied by the dated ``nightly-2026-05-01-x86_64-...``
    install -- rustup treats those as distinct toolchains -- while
    ``stable`` is satisfied by its qualified name."""
    host = SimulatedHost(
        name="h",
        rustup_toolchains={
            "stable-x86_64-unknown-linux-gnu": True,
            "nightly-2026-05-01-x86_64-unknown-linux-gnu": False,
        },
    )
    ctx = _ctx(host)
    commands = [
        c
        async for c in rustup.toolchains(ctx, toolchains=("stable", "nightly"))
    ]
    assert ctx.changed
    assert ctx.reason == "toolchain nightly not installed, will install"
    assert commands == [RustupToolchainInstallCommand(toolchains=("nightly",))]


async def test_rustup_toolchains_scopes_rustup_home():
    """``rustup_home`` forwards to the fact read and every descriptor as one
    env prefix (the scope invariant)."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [
        c
        async for c in rustup.toolchains(
            ctx, toolchains=("stable",), rustup_home="/srv/rust"
        )
    ]
    assert commands == [
        RustupToolchainInstallCommand(
            toolchains=("stable",), rustup_home="/srv/rust"
        )
    ]
    assert commands[0].shell() == (
        "RUSTUP_HOME=/srv/rust rustup toolchain install stable"
    )


async def test_rustup_toolchains_rejects_a_bad_profile():
    """``profile`` is a static argument validated up front with
    ``ValueError`` -- rustup allows only minimal/default/complete."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    with pytest.raises(ValueError, match="minimal/default/complete"):
        [
            c
            async for c in rustup.toolchains(
                ctx, toolchains=("stable",), profile="turbo"
            )
        ]


async def test_rustup_toolchains_rejects_default_with_absent():
    """Marking a default while removing toolchains is refused: the operation
    never installs what it marks."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    with pytest.raises(ValueError, match="mark a default while removing"):
        [
            c
            async for c in rustup.toolchains(
                ctx, toolchains=("stable",), default="stable", present=False
            )
        ]


# --- Operation: rustup.targets / rustup.components -------------------------------


async def test_rustup_targets_adds_only_missing():
    """The add is gated on ``RustupTargets``: an installed triple is skipped,
    a missing one joins the one batch command, under the request's toolchain
    scope."""
    host = SimulatedHost(
        name="h", rustup_targets={"stable": ["x86_64-unknown-linux-gnu"]}
    )
    ctx = _ctx(host)
    commands = [
        c
        async for c in rustup.targets(
            ctx,
            targets=("x86_64-unknown-linux-gnu", "aarch64-unknown-linux-musl"),
            toolchain="stable",
        )
    ]
    assert ctx.changed
    assert ctx.reason == "target aarch64-unknown-linux-musl missing, will add"
    assert commands == [
        RustupTargetAddCommand(
            targets=("aarch64-unknown-linux-musl",), toolchain="stable"
        )
    ]


async def test_rustup_targets_noop_and_remove():
    """All-present is a no-op for the add direction; ``present=False``
    removes only the installed subset of the active toolchain."""
    host = SimulatedHost(name="h", rustup_targets={"": ["wasm32-unknown-unknown"]})
    ctx = _ctx(host)
    commands = [
        c
        async for c in rustup.targets(ctx, targets=("wasm32-unknown-unknown",))
    ]
    assert not ctx.changed
    assert ctx.reason == "all targets already installed"
    assert commands == []

    ctx = _ctx(host)
    commands = [
        c
        async for c in rustup.targets(
            ctx, targets=("wasm32-unknown-unknown", "absent"), present=False
        )
    ]
    assert ctx.changed
    assert commands == [
        RustupTargetRemoveCommand(targets=("wasm32-unknown-unknown",))
    ]


async def test_rustup_components_adds_and_removes():
    """The component sibling gates the same way: add the missing, remove the
    installed, in the named toolchain's scope."""
    host = SimulatedHost(name="h", rustup_components={"nightly": ["rustfmt"]})
    ctx = _ctx(host)
    commands = [
        c
        async for c in rustup.components(
            ctx, components=("clippy", "rustfmt"), toolchain="nightly"
        )
    ]
    assert ctx.reason == "component clippy missing, will add"
    assert commands == [
        RustupComponentAddCommand(components=("clippy",), toolchain="nightly")
    ]

    ctx = _ctx(host)
    commands = [
        c
        async for c in rustup.components(
            ctx, components=("clippy", "rustfmt"), toolchain="nightly", present=False
        )
    ]
    assert ctx.reason == "component rustfmt installed, will remove"
    assert commands == [
        RustupComponentRemoveCommand(components=("rustfmt",), toolchain="nightly")
    ]


async def test_rustup_operations_accept_a_bare_string_and_absorb_kwargs():
    """A single target/component name is shorthand for a one-item list, and
    ``**kwargs`` takes any unknown keyword with no effect."""
    host = SimulatedHost(name="h")
    ctx = _ctx(host)
    commands = [c async for c in rustup.targets(ctx, targets="wasm32-unknown-unknown")]
    assert commands == [RustupTargetAddCommand(targets=("wasm32-unknown-unknown",))]
    ctx = _ctx(host)
    commands = [
        c
        async for c in rustup.components(
            ctx, components="clippy", ignore=True, quote=False
        )
    ]
    assert commands == [RustupComponentAddCommand(components=("clippy",))]


# --- Operation: SimulatedHost seam dispatch --------------------------------------


async def test_rustup_seam_mutates_the_stores():
    """Each descriptor maps onto its store through ``send_command``: an
    install adds a non-default entry, ``rustup default`` moves the flag,
    target/component add seed the toolchain's row."""
    host = SimulatedHost(
        name="h", rustup_toolchains={"stable-x86_64-unknown-linux-gnu": True}
    )
    await send_command(
        host, RustupToolchainInstallCommand(toolchains=("nightly",))
    )
    assert host.rustup_toolchains == {
        "stable-x86_64-unknown-linux-gnu": True,
        "nightly-x86_64-unknown-linux-gnu": False,
    }
    await send_command(host, RustupDefaultCommand(toolchain="nightly"))
    assert host.rustup_toolchains == {
        "stable-x86_64-unknown-linux-gnu": False,
        "nightly-x86_64-unknown-linux-gnu": True,
    }
    await send_command(
        host, RustupTargetAddCommand(targets=("wasm32-unknown-unknown",), toolchain="nightly")
    )
    assert host.rustup_targets == {"nightly": ["wasm32-unknown-unknown"]}
    await send_command(host, RustupComponentAddCommand(components=("clippy",)))
    assert host.rustup_components == {"": ["clippy"]}


async def test_rustup_removal_seam_reconciles_stores():
    """Uninstalling a toolchain drops its targets and components rows too;
    a target/component remove shrinks just the named entry."""
    host = SimulatedHost(
        name="h",
        rustup_toolchains={"nightly-x86_64-unknown-linux-gnu": False},
        rustup_targets={"nightly": ["wasm32-unknown-unknown"]},
        rustup_components={"nightly": ["rustfmt"]},
    )
    await send_command(
        host, RustupTargetRemoveCommand(targets=("wasm32-unknown-unknown",), toolchain="nightly")
    )
    assert host.rustup_targets["nightly"] == []
    await send_command(
        host, RustupComponentRemoveCommand(components=("rustfmt",), toolchain="nightly")
    )
    assert host.rustup_components["nightly"] == []
    await send_command(
        host, RustupToolchainRemoveCommand(toolchains=("nightly",))
    )
    assert "nightly-x86_64-unknown-linux-gnu" not in host.rustup_toolchains
    assert "nightly" not in host.rustup_targets
    assert "nightly" not in host.rustup_components


# --- Operation: end to end -------------------------------------------------------

INVENTORY = [
    {"host": "h", "family": "Ubuntu", "connection": {"address": "127.0.0.1", "username": "u"}}
]


def toolchains_deploy(host):
    yield op(
        rustup.toolchains,
        name="Install toolchains",
        toolchains=("stable", "nightly"),
        profile="minimal",
        default="stable",
    )


async def test_rustup_toolchains_end_to_end_converges():
    """The dry run plans without sending, the applied run installs and marks
    the default, and the second pass reports no change."""
    host = SimulatedHost(name="h")

    def factory(entry: dict) -> SimulatedHost:
        return host

    plans, dry_report, _hosts = await run(
        INVENTORY, toolchains_deploy, host_factory=factory, dry=True
    )
    assert dry_report is None
    assert plans["h"][0].changed
    assert host.rustup_toolchains == {}  # a dry run sends nothing

    _plans, report, _hosts = await run(
        INVENTORY, toolchains_deploy, host_factory=factory
    )
    log = format_log(report)
    assert "$ rustup toolchain install stable nightly --profile=minimal" in log
    assert "$ rustup default stable" in log
    assert host.rustup_toolchains == {
        "stable-x86_64-unknown-linux-gnu": True,
        "nightly-x86_64-unknown-linux-gnu": False,
    }

    plans2, _report2, _hosts = await run(
        INVENTORY, toolchains_deploy, host_factory=factory
    )
    assert not plans2["h"][0].changed


# --- Exports -------------------------------------------------------------------------


def test_rustup_module_exports():
    """The package re-exports the facts, the three operations and their
    descriptors, so ``rustup.RustupToolchains`` and ``rustup.toolchains``
    resolve; the top-level package makes ``from reemote import
    RustupToolchains`` and the descriptor names work and keeps the
    ``rustup`` namespace importable, but no operation function leaks to the
    top level."""
    assert rustup.RustupToolchains is RustupToolchains
    assert rustup.RustupToolchainInfo is RustupToolchainInfo
    assert rustup.RustupDefaultToolchain is RustupDefaultToolchain
    assert rustup.RustupTargets is RustupTargets
    assert rustup.RustupComponents is RustupComponents
    assert rustup.toolchains.__name__ == "toolchains"
    assert rustup.targets.__name__ == "targets"
    assert rustup.components.__name__ == "components"
    assert rustup.RustupToolchainInstallCommand is RustupToolchainInstallCommand
    assert rustup.RustupDefaultCommand is RustupDefaultCommand
    assert rustup.rustup.RustupToolchains is RustupToolchains
    import reemote

    assert reemote.rustup is rustup
    for name in (
        "RustupComponentAddCommand",
        "RustupComponentRemoveCommand",
        "RustupComponents",
        "RustupDefaultCommand",
        "RustupDefaultToolchain",
        "RustupTargetAddCommand",
        "RustupTargetRemoveCommand",
        "RustupTargets",
        "RustupToolchainInfo",
        "RustupToolchainInstallCommand",
        "RustupToolchainRemoveCommand",
        "RustupToolchains",
        "rustup",
    ):
        assert name in reemote.__all__
        assert getattr(reemote, name) is not None
    # The operation functions are reached through their namespace only.
    for name in ("toolchains", "targets", "components"):
        assert name not in reemote.__all__
