# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``cargo`` module: facts and operations in one source.
#
# Fact side: ``CargoPackages`` reports the crates installed globally via
# ``cargo install`` as ``CargoPackageInfo`` entries (crate name plus one
# installed version), parsed from the ``<crate> v<version>`` header rows of
# the ``cargo install --list`` output. Like every fact it is phase-unaware:
# its ``process`` coroutine just gathers the raw value.
#
# Operation side: ``cargo.packages`` installs, re-installs or removes those
# crates -- bare names or ``<pkg>=<version>`` pins -- through its
# ``CargoInstallCommand``/``CargoRemoveCommand`` descriptors. Operations are
# async generators that receive an ``OpContext``, await facts, set
# ``ctx.changed``/``ctx.reason``, and yield command descriptors; they never
# touch the host directly.

import re
import shlex
from collections.abc import AsyncGenerator, Iterable
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
    async def process(cls, host: Host) -> list[CargoPackageInfo]:
        cargo_packages = getattr(host, "cargo_packages", None)
        if cargo_packages is not None:
            # SimulatedHost seam: the crate name -> installed-version-list
            # store, projected into sorted entries (the ``go_packages``
            # shape). Freshly built dicts so callers never mutate simulated
            # state.
            return [
                {"name": name, "version": version}
                for name, versions in sorted(cargo_packages.items())
                for version in sorted(set(versions))
            ]
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------


@dataclass(frozen=True)
class CargoInstallCommand(Command):
    """``cargo install <pkg>@<version> ...``.

    The install *and* the ``latest`` re-install direction of ``cargo.packages``
    -- cargo has no separate update verb, so pyinfra builds the same
    ``cargo install`` command twice, and so does the operation: one descriptor
    per pending batch. ``packages`` holds cargo's own tokens -- bare names
    (the newest-version request) and ``<crate>@<version>`` pins alike,
    translated from the request's ``<pkg>=<version>`` spelling by
    ``_cargo_package_spec`` -- and every token is ``shlex``-quoted, so a
    metacharacter can never reach the remote shell unescaped.
    """

    packages: tuple[str, ...]

    def shell(self) -> str:
        parts = ["cargo", "install"]
        parts.extend(shlex.quote(package) for package in self.packages)
        return " ".join(parts)


@dataclass(frozen=True)
class CargoRemoveCommand(Command):
    """``cargo uninstall <pkg>[@<version>] ...``.

    The ``present=False`` direction of ``cargo.packages`` (pyinfra's
    ``uninstall_command``). Unlike go -- where the binary *is* the
    installation and removal resolves the bin directory -- cargo tracks its
    global installs, so removal is the toolchain's own verb. ``packages``
    holds cargo's tokens, pins included: a ``<crate>@<version>`` package-ID
    spec selects exactly that installed version, pyinfra's fixture shape.
    Every token is ``shlex``-quoted.
    """

    packages: tuple[str, ...]

    def shell(self) -> str:
        parts = ["cargo", "uninstall"]
        parts.extend(shlex.quote(package) for package in self.packages)
        return " ".join(parts)


async def packages(
    ctx: OpContext,
    packages: str | list[str] | tuple[str, ...] | None = None,
    present: bool = True,
    latest: bool = False,
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
            thing an unpinned name asks for. Defaults to None.
        present (bool, optional): Whether the packages should be installed.
            Defaults to True.
        latest (bool, optional): Whether to re-install installed packages
            without a pinned version. Not idempotent: without a registry
            comparison, an installed unversioned crate re-runs
            ``cargo install`` and reports changed on every run. Defaults to
            False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated cargo package commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
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
        # (this direction is not idempotent) -> cargo install ripgrep
        yield op(
            cargo.packages,
            name="Update ripgrep",
            packages=("ripgrep",),
            latest=True,
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
    # request re-yields ``cargo install <crate>`` and reports changed. The
    # install batch and the upgrade batch are pyinfra's two separate commands
    # of the same form -- it passes ``upgrade_command`` as the very same
    # ``cargo install`` string as ``install_command`` -- emitted as two
    # ``CargoInstallCommand``s, install first.
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

    # Normalize: accept a bare string, a list, or None (pyinfra's own
    # shorthand and its no-op).
    if packages is None:
        requested: tuple[str, ...] = ()
    elif isinstance(packages, str):
        requested = (packages,)
    else:
        requested = tuple(packages)

    if requested:
        installed = await ctx.facts.get(CargoPackages)
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
                yield CargoInstallCommand(
                    packages=tuple(_cargo_package_spec(pkg) for pkg in to_install)
                )
            if to_upgrade:
                ctx.changed = True
                reasons.append(f"{len(to_upgrade)} package(s) to upgrade")
                yield CargoInstallCommand(
                    packages=tuple(_cargo_package_spec(pkg) for pkg in to_upgrade)
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
                yield CargoRemoveCommand(packages=tuple(to_remove))
            else:
                reasons.append("no packages to remove")
    else:
        reasons.append("no packages requested")

    ctx.reason = "; ".join(reasons)


__all__ = [
    "CargoInstallCommand",
    "CargoPackageInfo",
    "CargoPackages",
    "CargoRemoveCommand",
    "packages",
]
