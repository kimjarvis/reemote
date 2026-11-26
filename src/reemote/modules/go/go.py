# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``go`` module: facts and operations in one source.
#
# Fact side: ``GoPackages`` reports the Go binaries installed via
# ``go install`` as ``GoPackageInfo`` entries (the binary's main-package
# import path plus one module version), parsed from the ``go version -m``
# rows of the executable listing of the go bin directory. Like every fact it
# is phase-unaware: its ``process`` coroutine just gathers the raw value.
#
# Operation side: ``go.packages`` installs, re-installs or removes those
# binaries -- bare import paths or ``<pkg>=<version>`` pins -- through its
# ``GoInstallCommand``/``GoRemoveCommand`` descriptors. Operations are async
# generators that receive an ``OpContext``, await facts, set
# ``ctx.changed``/``ctx.reason``, and yield command descriptors; they never
# touch the host directly.

import re
import shlex
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass
from typing import Any, TypedDict

from ...commands import Command
from ...core import OpContext
from ...hosts import Host

# --- Shared helpers ----------------------------------------------------------


def _split_version(package: str) -> tuple[str, str | None]:
    """Split ``pkg=version`` into ``(name, version)``; version is None if absent.

    The spelling is reemote's one user-facing pin format, shared with
    ``apt``/``dnf``/``apk``/``npm``/``gem`` (whose ``_split_version`` this is
    the copy of): split at the *last* ``=`` so a version string carrying
    ``=`` stays whole, and return both halves verbatim. A go import path
    carries no ``=``, so the split is unambiguous. Pure and testable.
    """
    name, sep, version = package.rpartition("=")
    return (name, version) if sep else (package, None)


def _go_package_spec(package: str) -> str:
    """Translate a ``<pkg>=<version>`` request into go's ``<pkg>@<version>``.

    The user-facing pin is reemote's shared ``=`` spelling; go's own
    requirement token joins path and version with ``@`` -- the ``npm``
    translation precedent. An unpinned name and the special ``latest`` pin
    both request the newest module and render as ``<pkg>@latest``, the token
    pyinfra builds for them (a bare ``go install <path>`` refuses to run
    outside a module, so ``@latest`` is never omitted). Pure and testable.
    """
    name, version = _split_version(package)
    if version is None or version == "latest":
        return f"{name}@latest"
    return f"{name}@{version}"


def _go_split_spec(token: str) -> tuple[str, str | None]:
    """Split go's ``<pkg>@<version>`` token into ``(path, version)``.

    The inverse of ``_go_package_spec`` for the ``SimulatedHost`` seam,
    splitting at the *first* ``@`` -- pyinfra's own ``partition("@")`` rule --
    because a go import path carries no ``@``, unlike npm where a scoped name
    begins with one. A bare path yields no version. Pure and testable.
    """
    name, sep, version = token.partition("@")
    return (name, version) if sep else (token, None)


def _binary_name(pkg_path: str) -> str:
    """Return the basename of the binary produced by ``go install <pkg_path>``.

    Under Go's Semantic Import Versioning, modules at major version v2+ carry
    a ``/v<N>`` suffix on their import path that is not part of the binary
    name -- ``github.com/foo/bar/v2`` installs a binary named ``bar``. Two
    distinct import paths can still map to the same binary name (e.g.
    ``a/b/cmd/tool`` and ``x/y/cmd/tool``), mirroring how ``go install``
    itself clobbers one binary with the other; on removal ``rm -f`` cannot
    tell them apart either. Pyinfra's builder copied verbatim.
    """
    parts = pkg_path.split("/")
    if len(parts) >= 2 and re.fullmatch(r"v\d+", parts[-1]):
        return parts[-2]
    return parts[-1]


# --- Facts -------------------------------------------------------------------


class GoPackageInfo(TypedDict):
    """
    Attributes:
        path: The binary's main-package import path exactly as the module
            records it (``"github.com/bitmagnet-io/bitmagnet"``).
        version: One module version the binary was built at (``"v0.9.0"``).
            A path rebuilt at several versions -- or shared by several
            binaries -- contributes one entry per version.
    """

    # One installed Go binary's module at one version -- the entry of the
    # ``GoPackages`` fact. A TypedDict rather than a class instance: the
    # answer is an ordinary path/version pair, so equality against a literal
    # dict works and ``json.dumps`` serializes it directly -- the
    # ``GemPackageInfo``/``NpmPackageInfo`` convention of every package-list
    # fact in this codebase. ``path`` rather than ``name`` because go keys
    # tools by their full import path, not a short name. A path can hold
    # several versions (``gem``'s precedent, not npm's one-per-name) when
    # two binaries in the go bin directory carry the same main module built
    # at different versions.

    path: str
    version: str


class GoPackages:
    """The ``go install`` binaries, as ``GoPackageInfo`` entries.

    **Commands:**

    ```bash
    BINDIR=$(go env GOBIN); [ -z "$BINDIR" ] && BINDIR="$(go env GOPATH)/bin"; ! test -d "$BINDIR" || find "$BINDIR" -maxdepth 1 -type f -perm -u+x -exec go version -m {} + 2>/dev/null
    ```

    **Returns:**

    ```python
    list[GoPackageInfo]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/go/go_go_packages.py``.
    """

    # Mirrors pyinfra's ``go.GoPackages``: the same compound command resolves
    # the go bin directory (``GOBIN``, else ``$GOPATH/bin``), and -- only when
    # that directory exists -- runs ``go version -m`` over every regular
    # executable in it; ``parse`` then keeps each binary's ``path`` row paired
    # with its ``mod`` row's version. ``dep``/``build`` rows, the
    # ``<binary>: go1.x`` header lines and blank lines are ignored, and a
    # ``(devel)`` version (``go build`` output, not ``go install``) is skipped
    # so only versioned installs are reported. pyinfra returns a
    # ``dict[str, set[str]]`` keyed by import path; reemote reports sorted,
    # unique ``GoPackageInfo`` entries -- the deterministic, JSON-printable
    # answer shape every package-list fact here uses (the ``GemPackages``
    # precedent).
    #
    # pyinfra's ``requires_command = "go"`` gate is deliberately dropped, but
    # unlike the bare-command facts (``gem list``, where a missing tool exits
    # 127 as ``CommandNotFoundError``) there is nothing to hear about here:
    # the command is self-guarding end to end. A missing ``go`` fails only
    # inside command substitutions and ``find -exec`` (whose stderr the
    # ``2>/dev/null`` hides and whose failure ``find`` reports as exit 1,
    # never 127), and a missing bin directory short-circuits the listing to
    # exit 0 -- so fact gathering, which tolerates non-zero exits, reads
    # unparsable silence as ``[]``. That is exactly the answer pyinfra's
    # ``default = dict`` gate produces, reached without the gate.

    #: Shell used to gather the value over SSH (pyinfra's command verbatim).
    COMMAND = (
        'BINDIR=$(go env GOBIN); [ -z "$BINDIR" ] && '
        'BINDIR="$(go env GOPATH)/bin"; ! test -d "$BINDIR" || '
        'find "$BINDIR" -maxdepth 1 -type f -perm -u+x '
        "-exec go version -m {} + 2>/dev/null"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[GoPackageInfo]:
        """Parse ``go version -m`` rows into sorted, unique entries.

        Tab-indented rows carry one record each: ``path`` names the binary's
        main package and the versioned ``mod`` row that follows contributes
        the ``(path, version)`` pair (``dep`` and ``build`` rows match no key
        and are ignored). A row not starting with a tab -- the per-binary
        ``<file>: go1.x`` header -- closes the current path, so a ``mod`` row
        never attaches to the previous binary's ``path``; a ``mod`` row whose
        version is ``(devel)`` (a ``go build`` artifact) is skipped, and
        output with no complete pair reads as ``[]``. Entries are
        de-duplicated and sorted by ``(path, version)``. Pure and testable.
        """
        rows: set[tuple[str, str]] = set()
        current_path: str | None = None
        for line in output:
            if not line.startswith("\t"):
                current_path = None
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            key = parts[1]
            if key == "path":
                current_path = parts[2]
            elif key == "mod" and current_path is not None and len(parts) >= 4:
                version = parts[3]
                # ``(devel)`` is ``go build`` output, not ``go install`` --
                # skip it so the fact only reports versioned installs.
                if version != "(devel)":
                    rows.add((current_path, version))
                current_path = None
        return [{"path": path, "version": version} for path, version in sorted(rows)]

    @classmethod
    async def process(cls, host: Host) -> list[GoPackageInfo]:
        go_packages = getattr(host, "go_packages", None)
        if go_packages is not None:
            # SimulatedHost seam: the import path -> installed-version-list
            # store, projected into sorted entries. Freshly built dicts so
            # callers never mutate simulated state.
            return [
                {"path": path, "version": version}
                for path, versions in sorted(go_packages.items())
                for version in sorted(set(versions))
            ]
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------

#: The go bin directory resolver both the fact and the removal command run
#: first: ``GOBIN`` when go reports one, else ``$GOPATH/bin``.
_GO_BINDIR_GUARD = (
    'BINDIR=$(go env GOBIN); [ -z "$BINDIR" ] && '
    'BINDIR="$(go env GOPATH)/bin"; '
)


@dataclass(frozen=True)
class GoInstallCommand(Command):
    """``go install <pkg>@<version> ...``.

    The install *and* the ``latest`` re-install direction of ``go.packages``
    -- go has no ``update`` verb, so pyinfra builds the same ``go install``
    command twice, and so does the operation: one descriptor per pending
    batch. ``packages`` holds go's own tokens -- ``<path>@<version>`` pins and
    ``<path>@latest`` alike, translated from the request's
    ``<path>=<version>`` spelling by ``_go_package_spec`` -- and every token
    is ``shlex``-quoted, so a metacharacter can never reach the remote shell
    unescaped.
    """

    packages: tuple[str, ...]

    def shell(self) -> str:
        parts = ["go", "install"]
        parts.extend(shlex.quote(package) for package in self.packages)
        return " ".join(parts)


@dataclass(frozen=True)
class GoRemoveCommand(Command):
    """Resolve the go bin directory, then ``rm -f "$BINDIR"/<binary> ...``.

    The ``present=False`` direction of ``go.packages`` (pyinfra's removal
    command). go keeps no install database -- the binary *is* the
    installation -- so removal resolves the bin directory exactly as the
    ``GoPackages`` fact does and deletes the files. ``packages`` holds the
    import paths; each renders as ``_binary_name`` spells the executable go
    linked it as (the last path segment, minus a Semantic-Import-Versioning
    ``/vN`` suffix). The guard prefix and the ``"$BINDIR"`` quoting are
    pyinfra's verbatim; each binary name is ``shlex``-quoted on top.
    """

    packages: tuple[str, ...]

    def shell(self) -> str:
        command = f"{_GO_BINDIR_GUARD}rm -f"
        if self.packages:
            targets = " ".join(
                f'"$BINDIR"/{shlex.quote(_binary_name(package))}'
                for package in self.packages
            )
            command = f"{command} {targets}"
        return command


async def packages(
    ctx: OpContext,
    packages: str | list[str] | tuple[str, ...] | None = None,
    present: bool = True,
    latest: bool = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Install or remove Go binaries installed via ``go install``.

    Args:
        ctx (OpContext): Execution context for the operation.
        packages (str | list[str] | tuple[str, ...] | None, optional): Go
            module binaries to ensure, each spelled as its full import path
            (``"honnef.co/go/tools/cmd/staticcheck"``). Prefer tuples when
            building requests via ``op()``, since ``OperationRequest`` kwargs
            must stay hashable. Versions can be pinned as
            ``<pkg>=<version>``, where the special ``=latest`` asks for the
            newest module version -- the same thing an unpinned path asks
            for. Defaults to None.
        present (bool, optional): Whether the packages should be installed.
            Defaults to True.
        latest (bool, optional): Whether to re-install installed packages
            without a pinned version. Not idempotent: without a registry to
            compare against, an installed unversioned package re-runs
            ``go install`` and reports changed on every run. Defaults to
            False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated go package commands.

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        ```python
        from reemote import go, op

        # Install the newest version of a module binary. go writes the
        # connecting user's go bin directory, so no escalation.
        # -> go install honnef.co/go/tools/cmd/staticcheck@latest
        yield op(
            go.packages,
            name="Install staticcheck",
            packages=("honnef.co/go/tools/cmd/staticcheck",),
        )

        # A `<pkg>=<version>` pin is translated to go's own `<pkg>@<version>`
        # token -> go install github.com/bitmagnet-io/bitmagnet@v0.10.0
        yield op(
            go.packages,
            name="Install a pinned binary",
            packages=("github.com/bitmagnet-io/bitmagnet=v0.10.0",),
        )

        # Remove a binary (present=False): the command deletes the
        # executable from the go bin directory
        # -> ... rm -f "$BINDIR"/staticcheck
        yield op(
            go.packages,
            name="Remove staticcheck",
            packages=("honnef.co/go/tools/cmd/staticcheck",),
            present=False,
        )

        # Re-install installed, unpinned binaries with the newest version
        # (this direction is not idempotent) ->
        # go install golang.org/x/tools/cmd/stringer@latest
        yield op(
            go.packages,
            name="Update stringer",
            packages=("golang.org/x/tools/cmd/stringer",),
            latest=True,
        )
        ```

        Examples can be found in ``examples/modules/go/go_packages.py``.
    """

    # pyinfra ``go.packages`` parity.
    #
    # The ``GoPackages`` fact supplies the installed versions, so an unpinned
    # request is present when the import path has *any* installed version
    # (there is no requested version to compare -- pyinfra installs
    # ``<pkg>@latest``) and a pinned ``<pkg>=<version>`` is present only when
    # that *exact* version is installed (the ``ensure_packages`` rule
    # ``apt.packages``, ``npm.packages`` and ``gem.packages`` follow). The
    # user-facing pin is reemote's shared ``<pkg>=<version>`` spelling,
    # translated to go's own ``<pkg>@<version>`` token by
    # ``_go_package_spec`` -- the npm precedent -- so pyinfra's ``@`` input
    # spelling is not accepted; its special ``@latest`` survives as the
    # ``=latest`` pin, treated exactly like no version at all in both the
    # comparison and the token.
    #
    # ``latest`` keeps pyinfra's documented non-idempotence (unlike apt, go
    # has no pending-upgrade view to gate on): every installed, unversioned
    # request re-yields ``go install <pkg>@latest`` and reports changed, the
    # npm/gem legacy-``latest`` behavior. The install batch and the upgrade
    # batch are pyinfra's two separate commands of the same form -- emitted
    # as two ``GoInstallCommand``s, install first -- because go has no
    # ``update`` verb.
    #
    # Removal is name-based, exactly pyinfra's and deliberately unlike the
    # version-aware apt/gem removal: a binary carries no per-version state to
    # match, so a path with any installed version is removed whatever pin
    # rode with it, and a path requested twice contributes one ``rm -f``
    # token in request order -- the request sequence must stay deterministic.
    #
    # Nothing here needs root: ``go install`` writes the *connecting user's*
    # go bin directory and the fact reads that same directory, so an
    # escalated request would install into root's tree instead -- leave the
    # request unprivileged. ``**kwargs`` absorbs pyinfra's global arguments
    # (``op()`` already strips ``name``/``sudo``/``su``/``doas``).
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
        installed = await ctx.facts.get(GoPackages)
        # The fact reports one entry per installed path/version pair; group
        # the versions by path so the lookups below read as the version-list
        # comparison ``ensure_packages`` does.
        versions_by_path: dict[str, list[str]] = {}
        for entry in installed:
            versions_by_path.setdefault(entry["path"], []).append(entry["version"])

        def is_installed(spec: str) -> bool:
            name, version = _split_version(spec)
            versions = versions_by_path.get(name)
            if not versions:
                return False
            # An unpinned request -- and go's special ``latest`` pin -- has
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
                yield GoInstallCommand(
                    packages=tuple(_go_package_spec(pkg) for pkg in to_install)
                )
            if to_upgrade:
                ctx.changed = True
                reasons.append(f"{len(to_upgrade)} package(s) to upgrade")
                yield GoInstallCommand(
                    packages=tuple(_go_package_spec(pkg) for pkg in to_upgrade)
                )
            if not to_install and not to_upgrade:
                reasons.append("all packages present")
        else:
            to_remove: list[str] = []
            for package in requested:
                name = _split_version(package)[0]
                if versions_by_path.get(name) and name not in to_remove:
                    to_remove.append(name)
            if to_remove:
                ctx.changed = True
                reasons.append(f"{len(to_remove)} package(s) to remove")
                yield GoRemoveCommand(packages=tuple(to_remove))
            else:
                reasons.append("no packages to remove")
    else:
        reasons.append("no packages requested")

    ctx.reason = "; ".join(reasons)


__all__ = [
    "GoInstallCommand",
    "GoPackageInfo",
    "GoPackages",
    "GoRemoveCommand",
    "packages",
]
