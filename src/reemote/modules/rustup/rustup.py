# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``rustup`` module: facts and operations in one source.
#
# ``RustupToolchains`` reports the toolchains installed under a ``RUSTUP_HOME``
# as a name -> ``RustupToolchainInfo`` mapping (whether each one is the default
# plus its release channel), ``RustupDefaultToolchain`` projects the single
# default name out of it, and ``RustupTargets``/``RustupComponents`` list the
# installed target triples and component names of one toolchain (the active
# default when none is named). All four read bare ``rustup`` commands, so a
# host without rustup exits 127 and the gather raises
# ``CommandNotFoundError`` -- the same deliberate drop of pyinfra's
# ``requires_command`` gate the cargo module makes. Like every fact they are
# phase-unaware: their ``process`` coroutines just gather the raw value.
#
# Operation side: ``rustup.toolchains`` installs/removes toolchains and sets
# the default (``RustupToolchainInstallCommand``/``RustupToolchainRemoveCommand``/
# ``RustupDefaultCommand``), and ``rustup.targets``/``rustup.components``
# add or remove one toolchain's target triples and components
# (``RustupTargetAddCommand``/``RustupTargetRemoveCommand``/
# ``RustupComponentAddCommand``/``RustupComponentRemoveCommand``). Operations
# are async generators that receive an ``OpContext``, await facts, set
# ``ctx.changed``/``ctx.reason`` and yield command descriptors; they never
# touch the host directly. Installing a *toolchain* downloads over the
# network -- a provisioning concern the examples still reach through
# ``apt.packages`` for the installer itself (see
# ``examples/modules/rustup/provision_rustup.py``).

import re
import shlex
from collections.abc import AsyncGenerator, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, TypedDict

from ...commands import Command
from ...core import OpContext
from ...hosts import Host

# --- Shared helpers ----------------------------------------------------------


def _toolchain_channel(name: str) -> str:
    """Classify a toolchain name as ``stable``, ``beta``, ``nightly`` or ``custom``.

    The proposal's prefix rule, tightened at the separator: a channel only
    counts when the name *is* the channel (``"stable"``) or continues it with
    its own field (``"stable-x86_64-unknown-linux-gnu"``,
    ``"nightly-2026-05-01-x86_64-unknown-linux-gnu"``), so a custom toolchain
    merely *starting* with the word -- ``"nightlyish"`` -- classifies as
    ``custom`` rather than being glued onto the channel. (A name like
    ``"nightly-builder"`` continues the ``nightly-`` field and stays
    ``nightly``, which is how rustup itself would resolve the ambiguous
    name.) Versioned names (``1.83.0-x86_64-...``) are ``custom`` here
    exactly as in the proposal, which maps only the three named channels.
    Pure and testable.
    """
    for channel in ("stable", "beta", "nightly"):
        if name == channel or name.startswith(channel + "-"):
            return channel
    return "custom"


def _rustup_shell(*subcommand: str, rustup_home: str | None = None) -> str:
    """Join one rustup invocation, optionally under an explicit ``RUSTUP_HOME``.

    ``RUSTUP_HOME=<quoted> `` is the env-assignment prefix the proposal builds
    with pyinfra's ``StringCommand``; the value is ``shlex``-quoted so a home
    carrying shell metacharacters names a directory rather than executing
    anything. Shared by the facts' reads and the operations' descriptors.
    Pure and testable.
    """
    prefix = (
        f"RUSTUP_HOME={shlex.quote(rustup_home)} " if rustup_home is not None else ""
    )
    return prefix + " ".join(subcommand)


def _toolchain_selector(toolchain: str | None) -> str:
    """Render rustup's top-level ``+<toolchain>`` selector argument.

    The ``+toolchain`` token is parsed *before* the subcommand (upstream
    ``rustup_mode.rs`` declares it on the root command), and the whole
    selector is ``shlex``-quoted as one token, so a hostile name cannot
    break out of the argument. ``None`` contributes the empty string -- the
    active toolchain needs no selector. Shared by the facts' list commands
    and the operations' add/remove descriptors. Pure and testable.
    """
    return f" {shlex.quote('+' + toolchain)}" if toolchain is not None else ""


# --- Facts -------------------------------------------------------------------


class RustupToolchainInfo(TypedDict):
    """
    Attributes:
        default: Whether the toolchain carries the ``(default)`` marker --
            the one ``rustup default`` selects for directories with no
            override (modern rustup prints it inside an
            ``(active, default)`` pair; either spelling reports ``True``).
        channel: One of ``"stable"``, ``"beta"``, ``"nightly"`` (the name
            starts with that channel field) or ``"custom"`` for everything
            else, versioned installs like ``1.83.0-x86_64-...`` included.
    """

    # One installed toolchain's metadata -- the entry of the
    # ``RustupToolchains`` fact, the proposal's ``{"default": ...,
    # "channel": ...}`` values made explicit. A TypedDict so equality against
    # a literal dict works and ``json.dumps`` serializes it directly, the
    # ``CargoPackageInfo`` convention.

    default: bool
    channel: str


class RustupToolchains:
    """The installed rustup toolchains, as toolchain name -> ``RustupToolchainInfo``.

    **Commands:**

    ```bash
    rustup toolchain list
    ```

    **Returns:**

    ```python
    dict[str, RustupToolchainInfo]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/rustup/rustup_rustup_toolchains.py``.
    """

    # Adapts the proposed ``RustupToolchains`` fact (described in pyinfra
    # idiom; no such fact exists in any pyinfra release). Two bugs in the
    # proposal's parsing are fixed here and recorded:
    #
    # * Its ``"(default)" in line`` test and ``replace("(default)", "")``
    #   strip cannot see rustup's ``(active, default)`` marker -- rustup
    #   prints ``<name> (active, default)`` when the default toolchain is
    #   also the active one (upstream ``rustup_mode.rs`` renders exactly
    #   ``(active, default)``, ``(active)`` or ``(default)``), and the naive
    #   replace leaves ``<name> (active,`` glued to the name. This parse
    #   detaches any trailing parenthesized marker instead: the name is what
    #   precedes it, and the toolchain is the default when the marker's
    #   comma-separated flags mention ``default``.
    # * Its channel test was a bare ``startswith``, which files a custom
    #   ``nightlyish`` toolchain under ``nightly``; ``_toolchain_channel``
    #   requires the channel to be a whole leading field.
    #
    # The proposal's dict is here sorted by toolchain name -- the deterministic
    # answer shape every collection fact in this codebase uses -- and its
    # ``requires_command = "rustup"`` gate is deliberately dropped: the
    # command is bare, so a host without rustup exits 127 and the gather
    # raises ``CommandNotFoundError``, an environment without the toolchain
    # being an error the deployer hears about (the ``CargoPackages``
    # precedent). A host *with* rustup but no toolchains installed prints
    # nothing and reads ``{}``, the proposal's ``default = dict``.

    #: Trailing status marker ``rustup toolchain list`` prints after the
    #: default/active toolchain names -- ``(default)``, ``(active)`` or
    #: ``(active, default)``.
    MARKER_REGEX: ClassVar[re.Pattern[str]] = re.compile(r"\s*\(([^)]*)\)\s*$")

    @classmethod
    def command(cls, rustup_home: str | None = None) -> str:
        """Shell listing the toolchains of one rustup home."""
        return _rustup_shell("rustup toolchain list", rustup_home=rustup_home)

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, RustupToolchainInfo]:
        """Parse ``rustup toolchain list`` rows into a sorted name -> info map.

        Each non-blank line names one toolchain, optionally suffixed with a
        ``(default)``/``(active)``/``(active, default)`` marker; the marker is
        detached and the toolchain is the default when its flags mention
        ``default``. A line reduced to nothing by the marker contributes no
        entry, and empty output -- no toolchains installed -- reads ``{}``.
        Pure and testable.
        """
        toolchains: dict[str, RustupToolchainInfo] = {}
        for raw_line in output:
            line = raw_line.strip()
            if not line:
                continue
            match = cls.MARKER_REGEX.search(line)
            flags: set[str] = set()
            if match is not None:
                flags = {flag.strip() for flag in match.group(1).split(",")}
                line = line[: match.start()].strip()
            if not line:
                continue
            toolchains[line] = {
                "default": "default" in flags,
                "channel": _toolchain_channel(line),
            }
        return {name: toolchains[name] for name in sorted(toolchains)}

    @classmethod
    async def process(
        cls, host: Host, rustup_home: str | None = None
    ) -> dict[str, RustupToolchainInfo]:
        rustup_toolchains = getattr(host, "rustup_toolchains", None)
        if rustup_toolchains is not None:
            # SimulatedHost seam: the toolchain name -> is-default store;
            # the channel is derived from the name, so the store cannot
            # contradict it, and ``rustup_home`` cannot narrow a store that
            # models the whole host (the ``GoInstalledSDKs`` rule). Freshly
            # built dicts so callers never mutate simulated state.
            return {
                name: {"default": bool(is_default), "channel": _toolchain_channel(name)}
                for name, is_default in sorted(rustup_toolchains.items())
            }
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.command(rustup_home))
        return cls.parse(stdout.splitlines())


class RustupDefaultToolchain:
    """The name of the default rustup toolchain, or ``None`` when none is set.

    **Commands:**

    ```bash
    rustup toolchain list
    ```

    **Returns:**

    ```python
    str | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/rustup/rustup_rustup_default_toolchain.py``.
    """

    # The proposal's convenience ``ShortFactBase``: callers get the default
    # toolchain's name directly instead of scanning ``RustupToolchains`` for
    # ``"default": True`` themselves. In reemote a short fact is simply a
    # fact that composes its sibling -- ``process`` delegates to
    # ``RustupToolchains.process`` (the ``hardware.DefaultIpv4Address`` /
    # ``NetworkDevices`` precedent) and projects the marked entry. Because it
    # issues no shell of its own it needs no ``COMMAND`` and no seam field of
    # its own: on a ``SimulatedHost`` the answer is projected from the same
    # ``rustup_toolchains`` store, so the two facts can never disagree the
    # way two seeded stores could. The Commands block shows the shell the
    # delegated fact runs.
    #
    # Exactly one toolchain can carry the marker (rustup stores one default),
    # so no tie-break is needed; iterating the already-sorted mapping keeps
    # even that degenerate case deterministic.

    @classmethod
    async def process(cls, host: Host, rustup_home: str | None = None) -> str | None:
        toolchains = await RustupToolchains.process(host, rustup_home)
        for name, info in toolchains.items():
            if info["default"]:
                return name
        return None


class RustupTargets:
    """The target triples installed for one rustup toolchain.

    **Commands:**

    ```bash
    rustup +stable target list --installed
    ```

    **Returns:**

    ```python
    list[str]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/rustup/rustup_rustup_targets.py``.
    """

    # Adapts the proposed ``RustupTargets`` fact. The proposal appended
    # ``+<toolchain>`` *after* the subcommand (``rustup target list +stable
    # --installed``), which rustup rejects: the ``+toolchain`` token is a
    # top-level argument parsed before the subcommand (upstream
    # ``rustup_mode.rs`` declares ``+toolchain`` on the root command and
    # feeds it to every toolchain-aware verb), so the builder below renders
    # ``rustup +stable target list --installed``. Its ``process`` also
    # promised "sorted target triples" while returning lines in the tool's
    # own order; the sort is actually applied here.
    #
    # Without a ``toolchain`` the listing covers the *active* toolchain --
    # the default, or a directory override -- exactly what plain
    # ``rustup target list --installed`` reports. A toolchain with no extra
    # targets still lists its host triple; a toolchain that is not installed
    # (or none installed at all) makes rustup exit 1 with its error on
    # stderr, which the unchecked fact read tolerates as empty output and
    # parses to ``[]`` -- the proposal's ``default = list``. A missing
    # ``rustup`` binary still exits 127 and raises ``CommandNotFoundError``
    # (the bare-command ``CargoPackages`` precedent, replacing the proposal's
    # dropped ``requires_command`` gate).

    @classmethod
    def command(
        cls, toolchain: str | None = None, rustup_home: str | None = None
    ) -> str:
        """Shell listing the installed targets of one toolchain.

        The ``+<toolchain>`` selector is ``shlex``-quoted as one token and
        placed before the subcommand, where rustup parses it.
        """
        return _rustup_shell(
            f"rustup{_toolchain_selector(toolchain)} target list --installed",
            rustup_home=rustup_home,
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[str]:
        """Parse ``rustup target list --installed`` rows into sorted triples.

        Every non-blank line is one installed target triple; the proposal's
        strip-and-drop rule, with the promised sort applied. Pure and
        testable.
        """
        return sorted({line.strip() for line in output if line.strip()})

    @classmethod
    async def process(
        cls,
        host: Host,
        toolchain: str | None = None,
        rustup_home: str | None = None,
    ) -> list[str]:
        rustup_targets = getattr(host, "rustup_targets", None)
        if rustup_targets is not None:
            # SimulatedHost seam: the toolchain name -> installed-target-list
            # store; the empty string keys the *active* toolchain's targets,
            # what a request without ``toolchain`` asks for. ``rustup_home``
            # cannot narrow a store that models the whole host (the
            # ``GoInstalledSDKs`` rule). A fresh list so callers never mutate
            # simulated state.
            return sorted(rustup_targets.get(toolchain or "", []))
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.command(toolchain, rustup_home))
        return cls.parse(stdout.splitlines())


class RustupComponents:
    """The components installed for one rustup toolchain.

    **Commands:**

    ```bash
    rustup +stable component list --installed
    ```

    **Returns:**

    ```python
    list[str]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/rustup/rustup_rustup_components.py``.
    """

    # The component sibling of ``RustupTargets``, the same two proposal fixes
    # applied (the ``+<toolchain>`` selector precedes the subcommand, the
    # listing is sorted). Rows carry the target-qualified component names
    # rustup prints -- ``rustfmt-x86_64-unknown-linux-gnu``, ``clippy-...``,
    # ``llvm-tools-preview`` -- verbatim, with no channel/toolchain
    # normalization: the triple suffix is part of a component's identity
    # here, unlike the toolchain names ``RustupToolchains`` keys.

    @classmethod
    def command(
        cls, toolchain: str | None = None, rustup_home: str | None = None
    ) -> str:
        """Shell listing the installed components of one toolchain.

        The ``+<toolchain>`` selector is ``shlex``-quoted as one token and
        placed before the subcommand, where rustup parses it.
        """
        return _rustup_shell(
            f"rustup{_toolchain_selector(toolchain)} component list --installed",
            rustup_home=rustup_home,
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[str]:
        """Parse ``rustup component list --installed`` rows into sorted names.

        Every non-blank line is one installed component; strip, drop blanks,
        sort -- the deterministic list shape the sibling fact uses. Pure and
        testable.
        """
        return sorted({line.strip() for line in output if line.strip()})

    @classmethod
    async def process(
        cls,
        host: Host,
        toolchain: str | None = None,
        rustup_home: str | None = None,
    ) -> list[str]:
        rustup_components = getattr(host, "rustup_components", None)
        if rustup_components is not None:
            # SimulatedHost seam: the toolchain name -> installed-component-
            # list store; the empty string keys the *active* toolchain, what
            # a request without ``toolchain`` asks for (the
            # ``rustup_targets`` shape). A fresh list so callers never mutate
            # simulated state.
            return sorted(rustup_components.get(toolchain or "", []))
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.command(toolchain, rustup_home))
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------


def _normalize_request(names: str | Sequence[str] | None) -> tuple[str, ...]:
    """Collapse a request argument into a deduplicated tuple in order.

    Each operation's name list -- toolchain specs, target triples, component
    names -- is normalized the ``cargo.packages`` way: a bare string counts as
    one item, an iterable keeps its request order, and repeats collapse to
    their first occurrence so the yielded command sequence is deterministic
    across phases. Pure and testable.
    """
    if names is None:
        return ()
    if isinstance(names, str):
        names = (names,)
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return tuple(seen)


@dataclass(frozen=True)
class RustupToolchainInstallCommand(Command):
    """Shell installing one or more rustup toolchains."""

    # ``rustup toolchain install`` takes several toolchain tildes at once --
    # one descriptor covers the whole missing batch, the
    # ``CargoInstallCommand``/``DnfInstallCommand`` shape.

    toolchains: tuple[str, ...]
    profile: str | None = None
    components: tuple[str, ...] = ()
    targets: tuple[str, ...] = ()
    rustup_home: str | None = None

    def shell(self) -> str:
        parts = ["rustup", "toolchain", "install"]
        parts.extend(shlex.quote(toolchain) for toolchain in self.toolchains)
        if self.profile is not None:
            parts.append(f"--profile={shlex.quote(self.profile)}")
        for component in self.components:
            parts.append(f"--component={shlex.quote(component)}")
        for target in self.targets:
            parts.append(f"--target={shlex.quote(target)}")
        return _rustup_shell(" ".join(parts), rustup_home=self.rustup_home)


@dataclass(frozen=True)
class RustupToolchainRemoveCommand(Command):
    """Shell uninstalling one or more rustup toolchains."""

    toolchains: tuple[str, ...]
    rustup_home: str | None = None

    def shell(self) -> str:
        parts = ["rustup", "toolchain", "uninstall"]
        parts.extend(shlex.quote(toolchain) for toolchain in self.toolchains)
        return _rustup_shell(" ".join(parts), rustup_home=self.rustup_home)


@dataclass(frozen=True)
class RustupDefaultCommand(Command):
    """Shell marking one rustup toolchain the default."""

    toolchain: str
    rustup_home: str | None = None

    def shell(self) -> str:
        shell = f"rustup default {shlex.quote(self.toolchain)}"
        return _rustup_shell(shell, rustup_home=self.rustup_home)


@dataclass(frozen=True)
class RustupTargetAddCommand(Command):
    """Shell adding target triples to one rustup toolchain."""

    targets: tuple[str, ...]
    toolchain: str | None = None
    rustup_home: str | None = None

    def shell(self) -> str:
        parts = [f"rustup{_toolchain_selector(self.toolchain)}", "target", "add"]
        parts.extend(shlex.quote(target) for target in self.targets)
        return _rustup_shell(" ".join(parts), rustup_home=self.rustup_home)


@dataclass(frozen=True)
class RustupTargetRemoveCommand(Command):
    """Shell removing target triples from one rustup toolchain."""

    targets: tuple[str, ...]
    toolchain: str | None = None
    rustup_home: str | None = None

    def shell(self) -> str:
        parts = [f"rustup{_toolchain_selector(self.toolchain)}", "target", "remove"]
        parts.extend(shlex.quote(target) for target in self.targets)
        return _rustup_shell(" ".join(parts), rustup_home=self.rustup_home)


@dataclass(frozen=True)
class RustupComponentAddCommand(Command):
    """Shell adding components to one rustup toolchain."""

    components: tuple[str, ...]
    toolchain: str | None = None
    rustup_home: str | None = None

    def shell(self) -> str:
        parts = [f"rustup{_toolchain_selector(self.toolchain)}", "component", "add"]
        parts.extend(shlex.quote(component) for component in self.components)
        return _rustup_shell(" ".join(parts), rustup_home=self.rustup_home)


@dataclass(frozen=True)
class RustupComponentRemoveCommand(Command):
    """Shell removing components from one rustup toolchain."""

    components: tuple[str, ...]
    toolchain: str | None = None
    rustup_home: str | None = None

    def shell(self) -> str:
        parts = [f"rustup{_toolchain_selector(self.toolchain)}", "component", "remove"]
        parts.extend(shlex.quote(component) for component in self.components)
        return _rustup_shell(" ".join(parts), rustup_home=self.rustup_home)


async def toolchains(
    ctx: OpContext,
    toolchains: str | Sequence[str] | None = None,
    default: str | None = None,
    present: bool = True,
    profile: str | None = None,
    components: Sequence[str] | None = None,
    targets: Sequence[str] | None = None,
    rustup_home: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Installs, updates or removes rustup toolchains and selects the default.

    Args:
        ctx (OpContext): Execution context for the operation.
        toolchains (str | Sequence[str] | None): Toolchain specs to manage
            (``"stable"``, ``"nightly-2026-05-01"``). Prefer a tuple when
            building requests via ``op()``, which rejects unhashable
            arguments. Defaults to None.
        default (str | None, optional): Toolchain to mark as the default
            (``rustup default <toolchain>``) when it is not the current one.
            Only meaningful with ``present=True``. Defaults to None.
        present (bool, optional): Whether the toolchains should be installed
            (``rustup toolchain install``) or removed (``rustup toolchain
            uninstall``). Defaults to True.
        profile (str | None, optional): Install profile, one of ``"minimal"``,
            ``"default"`` or ``"complete"`` (``--profile``). Defaults to None
            (rustup's own profile choice).
        components (Sequence[str] | None, optional): Components to install
            alongside each toolchain (one ``--component`` flag each). Prefer a
            tuple when building requests via ``op()``. Defaults to None.
        targets (Sequence[str] | None, optional): Target triples to install
            alongside each toolchain (one ``--target`` flag each). Prefer a
            tuple when building requests via ``op()``. Defaults to None.
        rustup_home (str | None, optional): Rustup home to operate on: the
            comparison read and every command run under a
            ``RUSTUP_HOME=<quoted> `` env prefix, so fact and commands reach
            one tree. Defaults to None (the host's own resolution).
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated rustup toolchain commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        ```python
        from reemote import op, rustup

        # Install toolchains missing from the host and keep stable default
        # -> rustup toolchain install stable nightly --profile minimal
        # -> rustup default stable   (only while stable is not the default)
        yield op(
            rustup.toolchains,
            name="Install toolchains",
            toolchains=("stable", "nightly"),
            profile="minimal",
            default="stable",
        )

        # Remove a toolchain (present=False) -> rustup toolchain uninstall nightly
        yield op(
            rustup.toolchains,
            name="Remove nightly",
            toolchains=("nightly",),
            present=False,
        )
        ```

        Examples can be found in ``examples/modules/rustup/rustup_toolchains.py``.
"""

    # The design note's operation (no pyinfra ``rustup`` exists; reemote is
    # the origin of this surface). Idempotence comes from the shipped
    # ``RustupToolchains`` fact: when ``present=True`` every requested name
    # already installed is skipped -- unless ``default`` alone still differs,
    # and when ``present=False`` every absent name is skipped. The install
    # batch is one command carrying the optional ``--profile``/``--component``
    # /``--target`` flags; ``profile``, ``components`` and ``targets`` choose
    # how *new* toolchains are built and never re-run against an installed
    # one -- refreshing an existing toolchain's components is
    # ``rustup.components``' job.
    #
    # The current default is derived from the same ``RustupToolchains`` read
    # (the entry whose info says ``default``) rather than awaiting
    # ``RustupDefaultToolchain``: the short fact composes at *process* level,
    # so awaiting it would issue a second shell outside the fact cache for an
    # answer this one already holds.
    #
    # Static-argument validation raises ``ValueError`` up front -- the
    # gem/npm combination-validation precedent, the design note's
    # pyinfra-idiomatic ``OperationError`` transliterated -- so the plan
    # fails rather than the SSH shell: ``profile`` is one of rustup's three
    # documented values, and ``default`` is refused with ``present=False``
    # because the operation never installs what it marks.
    #
    # Nothing here needs root: ``rustup`` writes the *connecting user's*
    # ``~/.rustup`` tree and the facts read that same tree, so leave the
    # request unprivileged. ``rustup_home`` scopes the whole request -- the
    # ``gem.packages`` invariant that the fact read and the commands mutate
    # one tree. ``**kwargs`` absorbs pyinfra-style global arguments (``op()``
    # already strips ``name``/``sudo``/``su``/``doas``).
    reasons: list[str] = []

    if profile is not None and profile not in ("minimal", "default", "complete"):
        raise ValueError(
            f"rustup.toolchains profile must be one of minimal/default/"
            f"complete: {profile!r} given"
        )
    if default is not None and not present:
        raise ValueError(
            "rustup.toolchains cannot mark a default while removing "
            "toolchains: unset default or present=True"
        )

    requested = _normalize_request(toolchains)
    installed = await ctx.facts.get(RustupToolchains, rustup_home=rustup_home)
    current_default = next(
        (name for name, info in installed.items() if info["default"]), None
    )

    if present:
        missing = [name for name in requested if name not in installed]
        if missing:
            ctx.changed = True
            reasons.append(
                f"toolchain{'s' if len(missing) > 1 else ''} "
                f"{', '.join(missing)} not installed, will install"
            )
            yield RustupToolchainInstallCommand(
                toolchains=tuple(missing),
                profile=profile,
                components=_normalize_request(components),
                targets=_normalize_request(targets),
                rustup_home=rustup_home,
            )
        elif requested:
            reasons.append(f"toolchains {', '.join(requested)} already installed")
        # The default selection rides on the same read: mark it only when it
        # differs from the current one, and ``rustup default`` installs an
        # as-yet-unselected toolchain itself, so no separate install reason.
        if default is not None:
            if default != current_default:
                ctx.changed = True
                reasons.append(
                    f"default is {current_default or 'unset'}, will set {default}"
                )
                yield RustupDefaultCommand(toolchain=default, rustup_home=rustup_home)
            else:
                reasons.append(f"default already {default}")
    else:
        installed_requested = [name for name in requested if name in installed]
        if installed_requested:
            ctx.changed = True
            reasons.append(
                f"toolchain{'s' if len(installed_requested) > 1 else ''} "
                f"{', '.join(installed_requested)} installed, will uninstall"
            )
            yield RustupToolchainRemoveCommand(
                toolchains=tuple(installed_requested), rustup_home=rustup_home
            )
        else:
            reasons.append(f"toolchains {', '.join(requested)} already absent")

    ctx.reason = "; ".join(reasons)


async def targets(
    ctx: OpContext,
    targets: str | Sequence[str],
    toolchain: str | None = None,
    present: bool = True,
    rustup_home: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Adds or removes cross-compilation target triples of one rustup toolchain.

    Args:
        ctx (OpContext): Execution context for the operation.
        targets (str | Sequence[str]): Target triples to manage
            (``"wasm32-unknown-unknown"``,
            ``("x86_64-unknown-linux-musl", "aarch64-unknown-linux-gnu")``).
            Prefer a tuple when building requests via ``op()``, which rejects
            unhashable arguments.
        toolchain (str | None, optional): Toolchain to operate on
            (rustup's ``+<toolchain>`` selector). Defaults to None (the
            active toolchain).
        present (bool, optional): Whether the targets should be added
            (``rustup target add``) or removed (``rustup target remove``).
            Defaults to True.
        rustup_home (str | None, optional): Rustup home to operate on: the
            comparison read and every command run under a
            ``RUSTUP_HOME=<quoted> `` env prefix, so fact and commands reach
            one tree. Defaults to None (the host's own resolution).
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated rustup target commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        ```python
        from reemote import op, rustup

        # Add musl targets to stable -> rustup +stable target add \\
        #     x86_64-unknown-linux-musl
        yield op(
            rustup.targets,
            name="Add musl target",
            targets=("x86_64-unknown-linux-musl",),
            toolchain="stable",
        )

        # Remove a target from the active toolchain (present=False)
        # -> rustup target remove wasm32-unknown-unknown
        yield op(
            rustup.targets,
            name="Remove wasm target",
            targets=("wasm32-unknown-unknown",),
            present=False,
        )
        ```

        Examples can be found in ``examples/modules/rustup/rustup_targets.py``.
"""

    # The design note's operation, idempotent through the shipped
    # ``RustupTargets`` fact: the read covers exactly the toolchain the
    # commands will mutate (the same ``toolchain``/``rustup_home`` scope),
    # ``present=True`` adds only the requested triples the fact lacks and
    # ``present=False`` removes only the ones it reports -- both in one batch
    # command. An empty request yields nothing and says so.
    #
    # No root: ``rustup target`` writes the connecting user's ``~/.rustup``
    # tree the fact reads. ``**kwargs`` absorbs pyinfra-style globals.
    reasons: list[str] = []
    requested = _normalize_request(targets)
    installed = await ctx.facts.get(
        RustupTargets, toolchain=toolchain, rustup_home=rustup_home
    )

    if present:
        to_add = [target for target in requested if target not in installed]
        if to_add:
            ctx.changed = True
            reasons.append(
                f"target{'s' if len(to_add) > 1 else ''} "
                f"{', '.join(to_add)} missing, will add"
            )
            yield RustupTargetAddCommand(
                targets=tuple(to_add), toolchain=toolchain, rustup_home=rustup_home
            )
        else:
            reasons.append("all targets already installed")
    else:
        to_remove = [target for target in requested if target in installed]
        if to_remove:
            ctx.changed = True
            reasons.append(
                f"target{'s' if len(to_remove) > 1 else ''} "
                f"{', '.join(to_remove)} installed, will remove"
            )
            yield RustupTargetRemoveCommand(
                targets=tuple(to_remove), toolchain=toolchain, rustup_home=rustup_home
            )
        else:
            reasons.append("no matching targets installed")

    ctx.reason = "; ".join(reasons)


async def components(
    ctx: OpContext,
    components: str | Sequence[str],
    toolchain: str | None = None,
    present: bool = True,
    rustup_home: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Adds or removes components of one rustup toolchain.

    Args:
        ctx (OpContext): Execution context for the operation.
        components (str | Sequence[str]): Component names to manage
            (``"clippy"``, ``"llvm-tools-preview"``,
            ``"rust-src"``). Prefer a tuple when building requests via
            ``op()``, which rejects unhashable arguments.
        toolchain (str | None, optional): Toolchain to operate on (rustup's
            ``+<toolchain>`` selector). Defaults to None (the active
            toolchain).
        present (bool, optional): Whether the components should be added
            (``rustup component add``) or removed (``rustup component
            remove``). Defaults to True.
        rustup_home (str | None, optional): Rustup home to operate on: the
            comparison read and every command run under a
            ``RUSTUP_HOME=<quoted> `` env prefix, so fact and commands reach
            one tree. Defaults to None (the host's own resolution).
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated rustup component commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        ```python
        from reemote import op, rustup

        # Add clippy and rustfmt to the active toolchain
        # -> rustup component add clippy rustfmt
        yield op(
            rustup.components,
            name="Add lint components",
            components=("clippy", "rustfmt"),
        )

        # Remove a component from nightly (present=False)
        # -> rustup +nightly component remove llvm-tools-preview
        yield op(
            rustup.components,
            name="Remove llvm-tools-preview",
            components=("llvm-tools-preview",),
            toolchain="nightly",
            present=False,
        )
        ```

        Examples can be found in ``examples/modules/rustup/rustup_components.py``.
"""

    # The component sibling of ``rustup.targets``, the same design and the
    # same fact gate: ``RustupComponents`` reads exactly the toolchain scope
    # the commands mutate, add takes the set difference and remove the
    # intersection, one batch command each. Component names carry their
    # target suffix verbatim (``rustfmt-x86_64-unknown-linux-gnu``) -- the
    # fact reports what rustup prints and the comparison is name-for-name,
    # the ``RustupComponents`` parity note. No root; unprivileged request.
    # ``**kwargs`` absorbs pyinfra-style globals.
    reasons: list[str] = []
    requested = _normalize_request(components)
    installed = await ctx.facts.get(
        RustupComponents, toolchain=toolchain, rustup_home=rustup_home
    )

    if present:
        to_add = [component for component in requested if component not in installed]
        if to_add:
            ctx.changed = True
            reasons.append(
                f"component{'s' if len(to_add) > 1 else ''} "
                f"{', '.join(to_add)} missing, will add"
            )
            yield RustupComponentAddCommand(
                components=tuple(to_add), toolchain=toolchain, rustup_home=rustup_home
            )
        else:
            reasons.append("all components already installed")
    else:
        to_remove = [component for component in requested if component in installed]
        if to_remove:
            ctx.changed = True
            reasons.append(
                f"component{'s' if len(to_remove) > 1 else ''} "
                f"{', '.join(to_remove)} installed, will remove"
            )
            yield RustupComponentRemoveCommand(
                components=tuple(to_remove),
                toolchain=toolchain,
                rustup_home=rustup_home,
            )
        else:
            reasons.append("no matching components installed")

    ctx.reason = "; ".join(reasons)


__all__ = [
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
    "components",
    "targets",
    "toolchains",
]
