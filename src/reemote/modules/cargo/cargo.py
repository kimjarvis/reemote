# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``cargo`` module: facts and operations in one source.
#
# Fact side: ``CargoPackages`` reports the crates installed globally via
# ``cargo install`` as ``CargoPackageInfo`` entries (crate name plus one
# installed version), parsed from the ``<crate> v<version>`` header rows of
# the ``cargo install --list`` output, and ``CargoConfig`` reports the parsed
# TOML of one ``CARGO_HOME``'s ``config.toml`` (legacy ``config`` fallback).
# Like every fact they are phase-unaware: their ``process`` coroutines just
# gather the raw value.
#
# Operation side: ``cargo.packages`` installs, re-installs or removes those
# crates -- bare names or ``<pkg>=<version>`` pins, optionally from a git
# source and with feature/lockfile selection -- through its
# ``CargoInstallCommand``/``CargoRemoveCommand`` descriptors, and
# ``cargo.cache_clean`` empties the cargo caches (``CargoCacheRemoveCommand``)
# or one project's build directory (``CargoCleanCommand``). Operations are
# async generators that receive an ``OpContext``, await facts, set
# ``ctx.changed``/``ctx.reason``, and yield command descriptors; they never
# touch the host directly.

import re
import shlex
import tomllib
from collections.abc import AsyncGenerator, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, TypedDict

from ...commands import Command
from ...core import OpContext
from ...hosts import Host

# --- Shared helpers ----------------------------------------------------------


def _split_version(package: str) -> tuple[str, str | None]:
    """Split ``pkg=version`` into ``(name, version)``; version is None if absent.

    The spelling is reemote's one user-facing pin format, shared with
    ``apt``/``dnf``/``apk``/``npm``/``gem`` (whose ``_split_version`` this is
    the copy of): split at the *last* ``=`` so a version string carrying
    ``=`` stays whole, and return both halves verbatim. A crate name carries
    no ``=``, so the split is unambiguous. Pure and testable.
    """
    name, sep, version = package.rpartition("=")
    return (name, version) if sep else (package, None)


def _cargo_package_spec(package: str) -> str:
    """Translate a ``<pkg>=<version>`` request into cargo's ``<pkg>@<version>``.

    The user-facing pin is reemote's shared ``=`` spelling; cargo's own
    package-ID token joins name and version with ``@`` -- the ``npm``
    translation precedent, and pyinfra's own ``version_join = "@"``. An
    unpinned name, and the special ``latest`` pin, both ask for the newest
    crate and render as the bare name: unlike go, ``cargo install <crate>``
    runs fine without a version and resolves the registry's newest itself,
    and crates.io has no ``latest`` version tag for a ``<crate>@latest``
    token to name. Pure and testable.
    """
    name, version = _split_version(package)
    if version is None or version == "latest":
        return name
    return f"{name}@{version}"


def _cargo_split_spec(token: str) -> tuple[str, str | None]:
    """Split cargo's ``<pkg>@<version>`` token into ``(name, version)``.

    The inverse of ``_cargo_package_spec`` for the ``SimulatedHost`` seam,
    splitting at the *first* ``@`` -- pyinfra's own ``partition("@")`` rule --
    because a crate name carries no ``@``. A bare name yields no version.
    Pure and testable.
    """
    name, sep, version = token.partition("@")
    return (name, version) if sep else (token, None)


# --- Facts -------------------------------------------------------------------


class CargoPackageInfo(TypedDict):
    """
    Attributes:
        name: The crate name exactly as ``cargo install --list`` spells it
            (``"cargo-edit"``, ``"rust-analyzer"``).
        version: One installed version with its ``v`` prefix stripped
            (``"0.11.6"``). A crate installed at several versions
            contributes one entry per version.
    """

    # One crate at one version -- the entry of the ``CargoPackages`` fact. A
    # TypedDict rather than a class instance: the answer is an ordinary
    # name/version pair, so equality against a literal dict works and
    # ``json.dumps`` serializes it directly -- the ``GemPackageInfo``/
    # ``NpmPackageInfo`` convention of every package-list fact in this
    # codebase. Unlike npm there can be several entries per name:
    # ``cargo install --list`` prints one header row per installed version
    # (a ``--locked``/``--force`` re-install keeps the old binary), which is
    # exactly what pyinfra's ``dict[str, set[str]]`` return models. Crate
    # names are compared verbatim -- cargo prints them as the registry
    # spells them -- so there is no normalization to share.

    name: str
    version: str


class CargoPackages:
    """The crates installed globally via ``cargo install``, as ``CargoPackageInfo`` entries.

    **Commands:**

    ```bash
    cargo install --list
    CARGO_HOME='<cargo home>' cargo install --list
    ```

    **Returns:**

    ```python
    list[CargoPackageInfo]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/cargo/cargo_cargo_packages.py``.
    """

    # Mirrors pyinfra's ``cargo.CargoPackages``: the same
    # ``cargo install --list`` command, keeping only its
    # ``<crate> v<version>`` header rows -- the indented ``- <path>``
    # binary lines, blank lines and any stray noise contribute nothing.
    # The regex widens pyinfra's ``^([a-zA-Z0-9\-]+)\sv([0-9\.]+)`` in two
    # ways: a crates.io name may also carry ``_`` (pyinfra silently drops
    # ``lazy_static``-style crates), and a version token may carry
    # pre-release or build metadata (``1.0.0-beta.2``) -- pyinfra's
    # ``[0-9\.]+`` truncates that to ``"1.0.0"`` and reports a version the
    # host does not have, while reemote captures the whole token.
    #
    # pyinfra returns a ``dict[str, set[str]]`` keyed by crate name; reemote
    # reports sorted, unique ``CargoPackageInfo`` entries -- the
    # deterministic, JSON-printable answer shape every package-list fact
    # here uses (the ``GemPackages`` precedent). And pyinfra's
    # ``requires_command = "cargo"`` gate silently yields its
    # ``default = dict`` when cargo is missing; reemote runs the command
    # bare, so a missing cargo exits 127 and surfaces as
    # ``CommandNotFoundError`` -- an environment without cargo is an error
    # the deployer hears about, not a silently empty fact (the npm/gem
    # precedent).
    #
    # The design-note extension adds one optional argument, ``cargo_home``:
    # the listing then runs under a ``CARGO_HOME=<quoted> `` env prefix, and
    # ``cargo.packages`` forwards its own ``cargo_home`` here so the fact
    # read and the install/uninstall commands operate on one tree -- the
    # ``gem.packages`` scope invariant (its ``GEM_HOME`` rides through to
    # ``GemPackages`` the same way). On a ``SimulatedHost`` the single
    # ``cargo_packages`` store models the host's install tree whatever it is
    # mounted at, so the argument does not filter it.

    #: Shell used to gather the value over SSH (pyinfra's command verbatim).
    COMMAND = "cargo install --list"

    #: One ``cargo install --list`` header row: ``<crate> v<version>``, the
    #: crate name over the characters crates.io allows (letters, digits and
    #: the ``_``/``-`` punctuation) and the version a bare semver token
    #: (digits, letters and the ``.``, ``-``, ``+`` punctuation) -- a
    #: trailing ``:`` or a parenthesized source suffix (``(https://git...
    #: #rev)``) is not part of the token and drops out of the match. The
    #: version is captured without its ``v`` prefix, pyinfra's rule.
    PACKAGE_REGEX: ClassVar[re.Pattern[str]] = re.compile(
        r"^([A-Za-z0-9_-]+)\sv([0-9][0-9A-Za-z.+-]*)"
    )

    @classmethod
    def command(cls, cargo_home: str | None = None) -> str:
        """Shell listing the crates installed under one cargo home.

        Without ``cargo_home`` the host resolves ``$CARGO_HOME`` (default
        ``$HOME/.cargo``) itself and ``COMMAND`` runs bare; with it, the path
        rides as a ``shlex``-quoted ``CARGO_HOME=<quoted> `` env prefix --
        the ``CargoConfig`` resolution and the ``rustup`` facts' ``RUSTUP_HOME``
        prefix shape, so a relocated install tree is listable too.
        """
        if cargo_home is None:
            return cls.COMMAND
        return f"CARGO_HOME={shlex.quote(cargo_home)} {cls.COMMAND}"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[CargoPackageInfo]:
        """Parse ``cargo install --list`` rows into sorted, unique entries.

        Only lines matching ``PACKAGE_REGEX`` contribute -- the ``- <path>``
        binary lines under each header, blank lines and warnings fall out.
        Each matched row contributes one ``(name, version)`` pair; the
        result is de-duplicated and sorted by ``(name, version)``, and one
        crate installed at several versions keeps one entry per version.
        Empty output -- a host with cargo but nothing installed -- reads as
        ``[]``, the counterpart of pyinfra's ``default = dict``. Pure and
        testable.
        """
        rows: set[tuple[str, str]] = set()
        for line in output:
            match = cls.PACKAGE_REGEX.match(line.strip())
            if match:
                rows.add((match.group(1), match.group(2)))
        return [{"name": name, "version": version} for name, version in sorted(rows)]

    @classmethod
    async def process(
        cls, host: Host, cargo_home: str | None = None
    ) -> list[CargoPackageInfo]:
        cargo_packages = getattr(host, "cargo_packages", None)
        if cargo_packages is not None:
            # SimulatedHost seam: the crate name -> installed-version-list
            # store, projected into sorted entries (the ``go_packages``
            # shape). ``cargo_home`` cannot narrow a store that models the
            # host's single install tree (the ``GoInstalledSDKs`` rule).
            # Freshly built dicts so callers never mutate simulated state.
            return [
                {"name": name, "version": version}
                for name, versions in sorted(cargo_packages.items())
                for version in sorted(set(versions))
            ]
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.command(cargo_home))
        return cls.parse(stdout.splitlines())


class CargoConfig:
    """The cargo configuration of one ``CARGO_HOME``, as a parsed TOML dict.

    **Commands:**

    ```bash
    CFG="${CARGO_HOME:-$HOME/.cargo}"; if [ -f "$CFG/config.toml" ]; then cat "$CFG/config.toml"; elif [ -f "$CFG/config" ]; then cat "$CFG/config"; fi
    ```

    **Returns:**

    ```python
    dict[str, Any]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/cargo/cargo_cargo_config.py``.
    """

    # Adapts the proposed ``CargoConfig`` fact (described in pyinfra idiom;
    # no such fact exists in any pyinfra release, so there is no baseline
    # parser to mirror -- the design note is the whole specification). It
    # reads the file cargo itself consults: ``config.toml`` inside
    # ``$CARGO_HOME`` (default ``$HOME/.cargo`` on the target host, or the
    # caller's explicit ``cargo_home``), falling back to the legacy ``config``
    # name, and prints contents only when a file exists -- the proposal's
    # rule, kept as an ``if``/``elif`` test rather than the
    # ``cat ... 2>/dev/null || true`` shape: a ``cat`` that fails on a
    # *present but unreadable* config keeps its non-zero exit and its stderr
    # instead of the shell lying success about a read it did not perform
    # (the unchecked fact read still tolerates that failed print as empty
    # output, which parses to the same ``{}`` absence reads -- the form
    # records the difference rather than erasing it). The chain never
    # invokes ``cargo``, so no host can
    # return 127 here: a missing ``CARGO_HOME`` is an absent config file and
    # reads ``{}`` (the proposal's ``default = dict``), which is also why no
    # ``CommandNotFoundError`` is raised and the dropped
    # ``requires_command = "cargo"`` gate costs nothing.
    #
    # Parsing is control-side ``tomllib`` over the whole printed file (both
    # cargo config names are TOML; the legacy file differs only in name).
    # The proposal is silent on malformed content; like every fact parser
    # here ``parse`` is total, so unparseable text reads ``{}`` too -- a
    # deployer writing configuration via an operation checks their own
    # syntax, and an empty answer merely means "no readable configuration
    # to compare against", never a crash mid-gather.

    @classmethod
    def command(cls, cargo_home: str | None = None) -> str:
        """Shell printing the cargo config of one Cargo home.

        Without ``cargo_home`` the host resolves ``$CARGO_HOME`` and falls
        back to ``$HOME/.cargo`` itself; with it, the path is
        ``shlex``-quoted so a home carrying shell metacharacters names a
        directory rather than executing anything.
        """
        assignment = (
            f"CFG={shlex.quote(cargo_home)}"
            if cargo_home is not None
            else 'CFG="${CARGO_HOME:-$HOME/.cargo}"'
        )
        return (
            f"{assignment}; "
            'if [ -f "$CFG/config.toml" ]; then cat "$CFG/config.toml"; '
            'elif [ -f "$CFG/config" ]; then cat "$CFG/config"; fi'
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, Any]:
        """Parse printed cargo config text into a TOML mapping.

        Empty output -- no config file on the host -- reads ``{}``, and so
        does text ``tomllib`` rejects (the total-parser rule above). A
        multi-line document is re-joined before parsing because
        ``splitlines`` dropped the newlines. Pure and testable.
        """
        text = "\n".join(output)
        if not text.strip():
            return {}
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    @classmethod
    async def process(
        cls, host: Host, cargo_home: str | None = None
    ) -> dict[str, Any]:
        cargo_config = getattr(host, "cargo_config", None)
        if cargo_config is not None:
            # SimulatedHost seam: the whole config mapping; ``cargo_home``
            # cannot narrow a store that models the host's single config
            # file (the ``GoInstalledSDKs`` rule). A fresh copy so callers
            # never mutate simulated state.
            return dict(cargo_config)
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.command(cargo_home))
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------


@dataclass(frozen=True)
class CargoInstallCommand(Command):
    """``cargo install [--git <url> [--branch|--tag|--rev <ref>]] [--force] [--locked] [--features ...] <pkg> ...``.

    The install, the ``latest`` re-install and (with ``git``) the source
    direction of ``cargo.packages`` -- cargo has no separate update verb, so
    pyinfra builds the same ``cargo install`` command twice, and so does the
    operation: one descriptor per pending batch. ``packages`` holds cargo's
    own tokens -- bare names (the newest-version request) and
    ``<crate>@<version>`` pins alike, translated from the request's
    ``<pkg>=<version>`` spelling by ``_cargo_package_spec`` -- and every
    token, git URL and ref selector is ``shlex``-quoted, so a metacharacter
    can never reach the remote shell unescaped. The feature/lockfile flags
    and ``force`` render once each when selected; ``cargo_home`` rides as a
    ``CARGO_HOME=<quoted> `` env prefix mirroring the fact read, and all
    fields default to cargo's own behavior when unset.
    """

    packages: tuple[str, ...]
    #: Re-install even though the crate is installed and up to date
    #: (``--force``; cargo otherwise refuses to overwrite its own binary).
    force: bool = False
    #: Enforce the packaged ``Cargo.lock`` (``--locked``, CI reproducibility).
    locked: bool = False
    #: Feature names joined into one comma-separated ``--features`` argument.
    features: tuple[str, ...] = ()
    #: Activate every available feature (``--all-features``).
    all_features: bool = False
    #: Skip the default features (``--no-default-features``).
    no_default_features: bool = False
    #: Git URL to install from (``--git``); the ref selectors below pair
    #: with it (cargo's own grammar: ``cargo install --git <url> [crate...]``).
    git: str | None = None
    #: Branch to install from when ``git`` is set (``--branch``).
    branch: str | None = None
    #: Tag to install from when ``git`` is set (``--tag``).
    tag: str | None = None
    #: Revision to install from when ``git`` is set (``--rev``).
    rev: str | None = None
    #: Explicit cargo home (``CARGO_HOME=<quoted> `` env prefix).
    cargo_home: str | None = None

    def shell(self) -> str:
        prefix = (
            f"CARGO_HOME={shlex.quote(self.cargo_home)} "
            if self.cargo_home is not None
            else ""
        )
        parts = ["cargo", "install"]
        if self.git is not None:
            parts += ["--git", shlex.quote(self.git)]
            for flag, value in (
                ("branch", self.branch),
                ("tag", self.tag),
                ("rev", self.rev),
            ):
                if value is not None:
                    parts += [f"--{flag}", shlex.quote(value)]
        if self.force:
            parts.append("--force")
        if self.locked:
            parts.append("--locked")
        if self.features:
            parts += ["--features", shlex.quote(",".join(self.features))]
        if self.all_features:
            parts.append("--all-features")
        if self.no_default_features:
            parts.append("--no-default-features")
        parts.extend(shlex.quote(package) for package in self.packages)
        return prefix + " ".join(parts)


@dataclass(frozen=True)
class CargoRemoveCommand(Command):
    """``cargo uninstall <pkg>[@<version>] ...``.

    The ``present=False`` direction of ``cargo.packages`` (pyinfra's
    ``uninstall_command``). Unlike go -- where the binary *is* the
    installation and removal resolves the bin directory -- cargo tracks its
    global installs, so removal is the toolchain's own verb. ``packages``
    holds cargo's tokens, pins included: a ``<crate>@<version>`` package-ID
    spec selects exactly that installed version, pyinfra's fixture shape.
    Every token is ``shlex``-quoted, and ``cargo_home`` rides as the same
    ``CARGO_HOME=<quoted> `` env prefix the install and the fact read use,
    so a relocated tree is uninstalled from the tree it was listed in.
    """

    packages: tuple[str, ...]
    #: Explicit cargo home (``CARGO_HOME=<quoted> `` env prefix).
    cargo_home: str | None = None

    def shell(self) -> str:
        prefix = (
            f"CARGO_HOME={shlex.quote(self.cargo_home)} "
            if self.cargo_home is not None
            else ""
        )
        parts = ["cargo", "uninstall"]
        parts.extend(shlex.quote(package) for package in self.packages)
        return prefix + " ".join(parts)


async def packages(
    ctx: OpContext,
    packages: str | list[str] | tuple[str, ...] | None = None,
    present: bool = True,
    latest: bool = False,
    locked: bool = False,
    features: Sequence[str] | None = None,
    all_features: bool = False,
    no_default_features: bool = False,
    git: str | None = None,
    branch: str | None = None,
    tag: str | None = None,
    rev: str | None = None,
    cargo_home: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Install or remove crates installed via ``cargo install``.

    Args:
        ctx (OpContext): Execution context for the operation.
        packages (str | list[str] | tuple[str, ...] | None, optional): Crate
            names to ensure, each spelled as crates.io writes it
            (``"cargo-edit"``, ``"ripgrep"``). Prefer tuples when building
            requests via ``op()``, since ``OperationRequest`` kwargs must stay
            hashable. Versions can be pinned as ``<pkg>=<version>``, where the
            special ``=latest`` asks for the newest crate version -- the same
            thing an unpinned name asks for. With ``git`` the names select
            packages inside the repository instead, so pins drop to bare
            names. Defaults to None.
        present (bool, optional): Whether the packages should be installed.
            Defaults to True.
        latest (bool, optional): Whether to re-install installed packages
            without a pinned version, forcing the overwrite cargo otherwise
            refuses (``--force``). Not idempotent: without a registry
            comparison, an installed unversioned crate re-runs
            ``cargo install`` and reports changed on every run. Defaults to
            False.
        locked (bool, optional): Pass ``--locked`` so cargo asserts the
            packaged ``Cargo.lock`` is current -- CI reproducibility.
            Defaults to False.
        features (Sequence[str] | None, optional): Feature names to activate,
            joined into one comma-separated ``--features`` argument. Prefer a
            tuple when building requests via ``op()``, which rejects
            unhashable arguments. Defaults to None.
        all_features (bool, optional): Pass ``--all-features`` to activate
            every available feature. Defaults to False.
        no_default_features (bool, optional): Pass ``--no-default-features``
            to skip the default feature. Defaults to False.
        git (str | None, optional): Git repository URL to install from
            (``--git``). A git install is not idempotent: there is no
            installed-source revision view to compare against, so it
            re-installs (with ``--force``, since cargo refuses to overwrite
            the binary its previous install left) and reports changed on
            every run. Only meaningful with ``present=True``.
            Defaults to None.
        branch (str | None, optional): Branch to install from when ``git``
            is set (``--branch``). Defaults to None.
        tag (str | None, optional): Tag to install from when ``git`` is set
            (``--tag``). Defaults to None.
        rev (str | None, optional): Commit to install from when ``git`` is
            set (``--rev``). Defaults to None.
        cargo_home (str | None, optional): Cargo home to operate on: the
            comparison read and every command run under a
            ``CARGO_HOME=<quoted> `` env prefix, so fact and commands reach
            one tree. Defaults to None (the host's own resolution).
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated cargo package commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        ```python
        from reemote import cargo, op

        # Install the newest version of a crate. cargo writes the connecting
        # user's install tree, so no escalation.
        # -> cargo install ripgrep
        yield op(cargo.packages, name="Install ripgrep", packages=("ripgrep",))

        # A `<pkg>=<version>` pin is translated to cargo's own
        # `<pkg>@<version>` token -> cargo install cargo-edit@0.11.6
        yield op(
            cargo.packages,
            name="Install a pinned crate",
            packages=("cargo-edit=0.11.6",),
        )

        # Remove a crate (present=False) -> cargo uninstall ripgrep
        yield op(
            cargo.packages,
            name="Remove ripgrep",
            packages=("ripgrep",),
            present=False,
        )

        # Re-install installed, unpinned crates with the newest version
        # (this direction is not idempotent)
        # -> cargo install --force ripgrep
        yield op(
            cargo.packages,
            name="Update ripgrep",
            packages=("ripgrep",),
            latest=True,
        )

        # Install from a git source -- cargo's own grammar picks the
        # repository's packages by name, forced like every git install
        # (not idempotent either)
        # -> cargo install --git https://github.com/rust-cli/rustup.git --force
        yield op(
            cargo.packages,
            name="Install rustup from git",
            git="https://github.com/rust-cli/rustup.git",
        )
        ```

        Examples can be found in ``examples/modules/cargo/cargo_packages.py``.
    """

    # pyinfra ``cargo.packages`` parity.
    #
    # The ``CargoPackages`` fact supplies the installed versions, so an
    # unpinned request is present when the crate has *any* installed version
    # (there is no requested version to compare -- cargo resolves the newest
    # from the registry) and a pinned ``<pkg>=<version>`` is present only when
    # that *exact* version is installed (the ``ensure_packages`` rule
    # ``apt.packages``, ``npm.packages`` and ``gem.packages`` follow). The
    # user-facing pin is reemote's shared ``<pkg>=<version>`` spelling,
    # translated to cargo's own ``<pkg>@<version>`` token by
    # ``_cargo_package_spec`` -- pyinfra spells its pins with the ``@`` joiner
    # directly, so its ``git@1.1`` input is here requested as ``git=1.1``;
    # pyinfra has no special ``latest`` pin at all, and reemote's
    # ``=latest`` behaves exactly like no version at all in both the
    # comparison and the bare-name token (crates.io has no ``latest``
    # version to name).
    #
    # ``latest`` keeps pyinfra's documented non-idempotence (like go, cargo
    # has no pending-upgrade view to gate on): every installed, unversioned
    # request re-yields ``cargo install`` and reports changed. The design
    # note adds cargo's ``--force`` to that re-install -- a deviation from
    # pyinfra's literal command string and a fix: without it cargo refuses
    # to overwrite its own binary, so the baseline's upgrade batch could
    # never actually re-install on a host where the crate is current. The
    # install batch and the upgrade batch are pyinfra's two separate commands
    # of the same form -- it passes ``upgrade_command`` as the very same
    # ``cargo install`` string as ``install_command`` -- emitted as two
    # ``CargoInstallCommand``s, install first.
    #
    # The design note's CI/source options are the module's first non-pyinfra
    # surface (pyinfra's cargo has none of them). ``locked``, ``features``,
    # ``all_features`` and ``no_default_features`` ride on every install
    # command of the request -- they choose *how* the crate is built, so the
    # comparison stays version-only and a changed feature selection on an
    # installed crate is what ``latest`` re-installs. ``git`` makes the
    # install non-idempotent like ``latest``: with no installed-source
    # revision view to compare, a git request always re-yields (cargo's
    # ``--git <url> [crate...]`` grammar takes *names*, so pins drop to bare
    # names there) and always carries ``--force``, because re-running against
    # the crate binary the previous install left behind is exactly what
    # cargo refuses unprompted; it only means the install direction -- a
    # ``present=False`` request uninstalls by name alone, mirroring how
    # ``latest`` is ignored there. The same refusal shapes ``--force`` on a
    # registry install batch that *moves* an installed crate to another
    # pinned version (a version-gated reinstall), while a batch of purely
    # new crates stays unprompted. ``branch``/``tag``/``rev`` are cargo's
    # mutually exclusive ref selectors for ``--git``; bad combinations are
    # static request arguments, refused up front with ``ValueError`` (the
    # design note's pyinfra-idiomatic ``OperationError`` transliterated to
    # the gem/npm combination-validation precedent) so the plan fails rather
    # than the SSH shell. ``cargo_home`` scopes the whole request: it
    # forwards to the ``CargoPackages`` read *and* both descriptors as one
    # ``CARGO_HOME=<quoted> `` env prefix, the ``gem.packages`` invariant
    # that the fact reads and the commands mutate one tree.
    #
    # Removal is version-aware, exactly pyinfra's and like apt/gem (deliberately
    # *not* go's name-based removal): a bare name is removed when the crate has
    # any installed version, a pin only when that exact version is installed --
    # pyinfra's ``add_packages``-fixture twin ``remove_packages`` leaves a
    # ``git@1.1`` request untouched while git sits at 1.0 -- and each token
    # keeps its pin in the command (``cargo uninstall react test@1.1``), cargo
    # selecting the version through its package-ID spec. Exact duplicate tokens
    # collapse to one, in request order: the request sequence must stay
    # deterministic.
    #
    # Nothing here needs root: ``cargo install`` writes the *connecting user's*
    # ``~/.cargo`` tree and the fact reads that same tree, so an escalated
    # request would install into root's tree instead -- leave the request
    # unprivileged. ``**kwargs`` absorbs pyinfra's global arguments (``op()``
    # already strips ``name``/``sudo``/``su``/``doas``).
    reasons: list[str] = []

    # Static-argument validation, up front and phase-neutral (see the parity
    # note above): cargo itself allows one ref selector per git install and
    # rejects the selectors without a source.
    ref_flags = [
        flag
        for flag, value in (("branch", branch), ("tag", tag), ("rev", rev))
        if value is not None
    ]
    if len(ref_flags) > 1:
        raise ValueError(
            "cargo.packages takes only one of branch/tag/rev: "
            f"{', '.join(ref_flags)} given -- cargo accepts a single ref "
            "selector per git install"
        )
    if ref_flags and git is None:
        raise ValueError(
            "cargo.packages takes branch/tag/rev only together with git: "
            f"{', '.join(ref_flags)} set without a git URL -- cargo's ref "
            "selectors choose a revision of a git source"
        )

    # Normalize: accept a bare string, a list, or None (pyinfra's own
    # shorthand and its no-op).
    if packages is None:
        requested: tuple[str, ...] = ()
    elif isinstance(packages, str):
        requested = (packages,)
    else:
        requested = tuple(packages)

    # How every install command of this request is built: the design's
    # feature/lockfile selection plus the cargo home scoping read and write.
    install_kwargs: dict[str, Any] = {
        "locked": locked,
        "features": tuple(features) if features else (),
        "all_features": all_features,
        "no_default_features": no_default_features,
        "cargo_home": cargo_home,
    }

    if present and git is not None:
        # Non-registry source: always (re-)install, the same documented
        # non-idempotence as ``latest`` (see the parity note). Git installs
        # name packages inside the cloned repository, so any pin drops to
        # its bare name; no names means cargo's repository-default package.
        # Duplicate names collapse to one, in request order -- the request
        # sequence must stay deterministic.
        names: list[str] = []
        for package in requested:
            name = _split_version(package)[0]
            if name and name not in names:
                names.append(name)
        ctx.changed = True
        if names:
            reasons.append(f"install {', '.join(names)} from git")
        else:
            reasons.append("install from git")
        yield CargoInstallCommand(
            packages=tuple(names),
            git=git,
            branch=branch,
            tag=tag,
            rev=rev,
            # ``--force``: a git install always re-runs, and cargo refuses
            # to overwrite the binary its previous install of the same
            # crate left in ``$CARGO_HOME/bin`` (``error: binary ... already
            # exists``), so without it the second deploy of one git request
            # would fail the shell.
            force=True,
            **install_kwargs,
        )
        ctx.reason = "; ".join(reasons)
        return

    if requested:
        # The comparison read and the commands below mutate one tree: the
        # request's ``cargo_home`` forwards to the fact (the ``gem.packages``
        # scope invariant above).
        installed = await ctx.facts.get(CargoPackages, cargo_home=cargo_home)
        # The fact reports one entry per installed crate/version pair; group
        # the versions by name so the lookups below read as the version-list
        # comparison ``ensure_packages`` does.
        versions_by_name: dict[str, list[str]] = {}
        for entry in installed:
            versions_by_name.setdefault(entry["name"], []).append(entry["version"])

        def is_installed(spec: str) -> bool:
            name, version = _split_version(spec)
            versions = versions_by_name.get(name)
            if not versions:
                return False
            # An unpinned request -- and cargo's special ``latest`` pin -- has
            # no version to compare: any installed version satisfies it.
            return version is None or version == "latest" or version in versions

        if present:
            to_install = tuple(pkg for pkg in requested if not is_installed(pkg))
            # ``latest``: installed, unversioned requests are re-install
            # candidates; a pinned request never is.
            to_upgrade = (
                tuple(
                    pkg
                    for pkg in requested
                    if _split_version(pkg)[1] in (None, "latest")
                    and is_installed(pkg)
                )
                if latest
                else ()
            )
            if to_install:
                ctx.changed = True
                reasons.append(f"{len(to_install)} package(s) to install")
                # A pin moved onto a crate installed at another version
                # overwrites that crate's binary, which cargo refuses
                # unprompted; ``--force`` covers the whole batch and is
                # inert for the genuinely new crates alongside it (pyinfra
                # omits it -- a divergence, since its command would fail
                # the same way on a real host).
                install_force = any(
                    _split_version(pkg)[0] in versions_by_name for pkg in to_install
                )
                yield CargoInstallCommand(
                    packages=tuple(_cargo_package_spec(pkg) for pkg in to_install),
                    force=install_force,
                    **install_kwargs,
                )
            if to_upgrade:
                ctx.changed = True
                reasons.append(f"{len(to_upgrade)} package(s) to upgrade")
                yield CargoInstallCommand(
                    packages=tuple(_cargo_package_spec(pkg) for pkg in to_upgrade),
                    # ``--force``: the crate is installed and current, which
                    # is exactly what cargo refuses to overwrite unprompted
                    # (see the parity note above).
                    force=True,
                    **install_kwargs,
                )
            if not to_install and not to_upgrade:
                reasons.append("all packages present")
        else:
            to_remove: list[str] = []
            for package in requested:
                if not is_installed(package):
                    continue
                token = _cargo_package_spec(package)
                if token not in to_remove:
                    to_remove.append(token)
            if to_remove:
                ctx.changed = True
                reasons.append(f"{len(to_remove)} package(s) to remove")
                yield CargoRemoveCommand(
                    packages=tuple(to_remove), cargo_home=cargo_home
                )
            else:
                reasons.append("no packages to remove")
    else:
        reasons.append("no packages requested")

    ctx.reason = "; ".join(reasons)


# --- cargo.cache_clean -------------------------------------------------------


@dataclass(frozen=True)
class CargoCleanCommand(Command):
    """``cargo clean --manifest-path <project>/Cargo.toml``.

    The ``path`` direction of ``cargo.cache_clean``: the project's own
    build-directory removal, cargo's verb for it. ``cargo_home`` rides as
    the same ``CARGO_HOME=<quoted> `` env prefix ``cargo.packages`` uses
    (it also decides where cargo's caches live, so a scoped clean stays
    consistent with a scoped install); every path is ``shlex``-quoted.
    """

    #: Project directory holding the ``Cargo.toml`` to clean (the design's
    #: ``--manifest-path <path>/Cargo.toml``).
    path: str
    #: Explicit cargo home (``CARGO_HOME=<quoted> `` env prefix).
    cargo_home: str | None = None

    def shell(self) -> str:
        prefix = (
            f"CARGO_HOME={shlex.quote(self.cargo_home)} "
            if self.cargo_home is not None
            else ""
        )
        manifest = f"{self.path.rstrip('/')}/Cargo.toml"
        return f"{prefix}cargo clean --manifest-path {shlex.quote(manifest)}"


@dataclass(frozen=True)
class CargoCacheRemoveCommand(Command):
    """Guarded ``rm -rf`` of the selected ``$CARGO_HOME`` cache subdirectories.

    The registry/git direction of ``cargo.cache_clean``. Unlike go -- where
    ``go clean -modcache`` is the tool's own verb -- cargo has no command to
    purge ``$CARGO_HOME/registry`` or ``$CARGO_HOME/git``, so the descriptor
    removes the directories itself, under the ``CFG="${CARGO_HOME:-
    $HOME/.cargo}"`` resolution (or one quoted ``cargo_home``) that the
    ``CargoConfig`` read uses. Each removal sits behind an ``if [ -d ]``
    test -- the same record-the-difference shell shape, which also keeps
    the compound exiting 0 when a cache was never populated -- and only the
    selected subdirectories are touched. cargo stores nothing regenerable
    in these trees, so no elevation: cleaning root's cache instead of the
    connecting user's would need ``sudo`` on purpose.
    """

    #: Remove ``$CARGO_HOME/registry`` (downloaded crate sources/archives).
    registry: bool = True
    #: Remove ``$CARGO_HOME/git`` (cached git checkouts and databases).
    git: bool = True
    #: Explicit cargo home; None lets the host resolve ``$CARGO_HOME``.
    cargo_home: str | None = None

    def shell(self) -> str:
        assignment = (
            f"CFG={shlex.quote(self.cargo_home)}"
            if self.cargo_home is not None
            else 'CFG="${CARGO_HOME:-$HOME/.cargo}"'
        )
        steps = []
        for selected, name in ((self.registry, "registry"), (self.git, "git")):
            if selected:
                steps.append(
                    f'if [ -d "$CFG/{name}" ]; then rm -rf "$CFG/{name}"; fi'
                )
        return "; ".join([assignment, *steps])


async def cache_clean(
    ctx: OpContext,
    path: str | None = None,
    cargo_home: str | None = None,
    clean_registry: bool = True,
    clean_git: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Clean cargo build artifacts or the registry/git caches.

    Args:
        ctx (OpContext): Execution context for the operation.
        path (str | None, optional): A project directory to clean with
            ``cargo clean --manifest-path <path>/Cargo.toml`` instead of the
            cache removal below. Defaults to None.
        cargo_home (str | None, optional): Cargo home to clean, passed as a
            ``CARGO_HOME=<quoted> `` prefix/env resolution. Defaults to
            None (the host's own ``$CARGO_HOME`` or ``~/.cargo``).
        clean_registry (bool, optional): With no ``path``, remove
            ``$CARGO_HOME/registry`` (cached crate sources and archives).
            Defaults to True.
        clean_git (bool, optional): With no ``path``, remove
            ``$CARGO_HOME/git`` (cached git checkouts and databases).
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The clean command, always when a direction is selected
            (the operation is stateless); nothing when neither is.

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/cargo/cargo_cache_clean.py``.
    """

    # The design's ``cargo.cache_clean``, ported *without* its ``min_bytes``
    # size gate -- the exact decision ``go.cache_clean`` made for its
    # ``min_total_mb``: the gate reads a ``CargoCacheSize`` fact that does
    # not exist here, and rather than add a fact for a threshold no caller
    # asked for, the operation is stateless like ``dnf.clean``/``go.cache_clean``,
    # leaving the caller to gate it in the deploy (a ``gather_fact`` of disk
    # usage plus a ``when`` predicate reaches the same decision). The
    # design's either/or splits into the two descriptors: ``path`` requests
    # a project clean (``cargo clean`` is the tool's own verb there), while
    # with no ``path`` the selected ``$CARGO_HOME`` cache subdirectories are
    # removed -- cargo offers no cache-purge command of its own, so the
    # guarded ``rm -rf`` is the operation's shape, never an unqualified
    # ``rm -rf $CARGO_HOME`` (the config file and bin/ tree are untouched).
    # Both trees belong to the connecting user, so the request stays
    # unprivileged; a ``sudo`` clean would empty root's caches instead.
    # Selecting neither cache and giving no path is a no-op. ``**kwargs``
    # absorbs pyinfra's global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``/``doas``).
    reasons: list[str] = []

    if path is not None:
        ctx.changed = True
        reasons.append(f"clean build artifacts of {path}")
        yield CargoCleanCommand(path=path, cargo_home=cargo_home)
    elif clean_registry or clean_git:
        ctx.changed = True
        selected = []
        if clean_registry:
            selected.append("registry")
        if clean_git:
            selected.append("git")
        reasons.append(f"clean {' and '.join(selected)} cache")
        yield CargoCacheRemoveCommand(
            registry=clean_registry, git=clean_git, cargo_home=cargo_home
        )
    else:
        reasons.append("no caches or project selected")

    ctx.reason = "; ".join(reasons)


__all__ = [
    "CargoCacheRemoveCommand",
    "CargoCleanCommand",
    "CargoConfig",
    "CargoInstallCommand",
    "CargoPackageInfo",
    "CargoPackages",
    "CargoRemoveCommand",
    "cache_clean",
    "packages",
]
