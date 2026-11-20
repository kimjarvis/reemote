# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``npm`` module: facts and operations in one source.
#
# Fact side: ``NpmPackages`` reports the installed npm packages as
# ``NpmPackageInfo`` entries (package name plus the version npm reports),
# parsed from the JSON tree of ``npm ls -g --depth=0 --json`` -- or, when a
# ``directory`` is given, of that project's own ``npm ls --depth=0 --json``.
# Only the top-level (direct) dependencies are reported, because that is the
# tree depth ``npm install``/``npm uninstall`` mutate. Like every fact it is
# phase-unaware: its ``process`` coroutine just gathers the raw value.
#
# Operation side: ``npm.packages`` installs, updates or removes npm
# packages -- bare names or ``<pkg>=<version>`` pins -- globally or inside
# one project directory, through its ``NpmInstallCommand``/
# ``NpmUpdateCommand``/``NpmRemoveCommand`` descriptors, and installs the
# dependencies declared in a manifest through ``NpmPackageJsonCommand``.
# Operations are async generators that receive an ``OpContext``, await facts,
# set ``ctx.changed``/``ctx.reason``, and yield command descriptors; they
# never touch the host directly.


import json
import posixpath
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
    ``apt``/``dnf``/``apk`` (whose ``_split_version`` this is the copy of):
    split at the *last* ``=`` so a version string carrying ``=`` stays whole,
    and return both halves verbatim. A scoped name is unaffected --
    ``@scope/name=1.2.3`` splits at the ``=``, not the ``/``. Pure and
    testable.
    """
    name, sep, version = package.rpartition("=")
    return (name, version) if sep else (package, None)


def _npm_package_spec(package: str) -> str:
    """Translate a ``<pkg>=<version>`` request into npm's ``<pkg>@<version>``.

    The user-facing pin is reemote's shared ``=`` spelling; npm's own
    requirement token joins name and version with ``@``, which is also
    pyinfra's ``version_join``. An unpinned name is returned verbatim, so
    ``@scope/name`` survives the translation untouched (it carries no ``=``)
    even though it carries an ``@``. Pure and testable.
    """
    name, version = _split_version(package)
    return f"{name}@{version}" if version is not None else name


def _npm_split_spec(token: str) -> tuple[str, str | None]:
    """Split npm's ``<pkg>@<version>`` token into ``(name, version)``.

    The exact inverse of ``_npm_package_spec``, splitting at the *last* ``@``
    the way npm itself reads a requirement argument -- and only when one
    follows the token's first character, because a scoped name begins with
    ``@``: ``@scope/name`` carries no version, ``@scope/name@1.2.3`` carries
    ``1.2.3``. ``SimulatedHost``'s npm seam uses it to record which name an
    ``npm install``/``npm uninstall`` token touches; the library's own
    comparison goes the other way, from the user-facing ``=`` pin.
    """
    name, sep, version = token.rpartition("@")
    if not sep or not name:
        return token, None
    return name, version


def _npm_env(prefix: str | None, unsafe_perm: bool) -> str:
    """The ``VAR=value `` assignments to place in front of an ``npm`` call.

    ``prefix`` renders as ``NPM_CONFIG_PREFIX``, ``unsafe_perm`` as
    ``NPM_CONFIG_UNSAFE_PERM=true`` -- the two environment variables Ansible's
    ``npm`` module task sets, which is how npm itself reads them. The value is
    ``shlex``-quoted, so a prefix path carrying a metacharacter cannot break
    out of the assignment. Returns the empty string when neither is requested,
    so callers can concatenate it unconditionally. Because an assignment only
    covers the command it precedes, this fragment must sit directly before
    ``npm`` -- after the ``cd`` in the project-directory form, not before it.
    """
    parts = []
    if prefix:
        parts.append(f"NPM_CONFIG_PREFIX={shlex.quote(prefix)}")
    if unsafe_perm:
        parts.append("NPM_CONFIG_UNSAFE_PERM=true")
    return "".join(f"{part} " for part in parts)


def _npm_invocation(
    verb: str,
    packages: tuple[str, ...] = (),
    directory: str | None = None,
    prefix: str | None = None,
    unsafe_perm: bool = False,
) -> str:
    """Build one ``npm`` invocation in the two scopes npm understands.

    ``directory=None`` is the global scope -- ``npm <verb> -g <packages>``,
    pyinfra's global command builder -- and a directory is the project scope,
    ``cd <directory> && npm <verb> <packages>``, the form pyinfra builds with
    ``StringCommand("cd", QuoteString(directory), "&&", "npm", verb)``. The
    directory and every package token are ``shlex``-quoted; the environment
    fragment from ``_npm_env`` rides directly before ``npm``. Trailing package
    tokens are omitted when there are none, so a bare ``npm update`` stays
    bare.
    """
    env = _npm_env(prefix, unsafe_perm)
    if directory is None:
        invocation = f"{env}npm {verb} -g"
    else:
        invocation = f"cd {shlex.quote(directory)} && {env}npm {verb}"
    if not packages:
        return invocation
    return f"{invocation} {' '.join(shlex.quote(package) for package in packages)}"


def _environment_key(directory: str | None, prefix: str | None) -> str:
    """Key one npm scope in the simulated package store.

    npm keeps a separate tree per scope: the default global prefix, a global
    prefix relocated by ``NPM_CONFIG_PREFIX``, and each project directory. The
    key names the scope the fact reports and the commands mutate, so a
    simulated host can hold several independent trees and the fact seam, the
    mutators and ``npm.packages`` all agree on which one a request touches.
    Pure and testable.
    """
    if directory is not None:
        return f"directory={directory}"
    return "global" if not prefix else f"global@{prefix}"


# --- Facts -------------------------------------------------------------------


class NpmPackageInfo(TypedDict):
    """
    Attributes:
        name: The package name exactly as npm spells it, scoped names
            included (``"@qwen-code/qwen-code"``, ``"node-sass"``).
        version: The installed version as npm reports it (``"0.8.2"``,
            ``"1.1.0-beta.4"``).
    """

    # One npm package at one version -- the entry of the ``NpmPackages``
    # fact. A TypedDict rather than a class instance: the answer is an
    # ordinary name/version pair, so equality against a literal dict works
    # and ``json.dumps`` serializes it directly -- the
    # ``PipPackageInfo``/``FlatpakPackageInfo`` convention of every
    # package-list fact in this codebase. Unlike pip there is no name to
    # normalize: npm package names are already lowercase and case-sensitive
    # on the registry, so the reported name is the key npm printed.

    name: str
    version: str


class NpmPackages:
    """The installed npm packages, as ``NpmPackageInfo`` entries.

    **Commands:**

    ```bash
    npm ls -g --depth=0 --json
    ! test -d <directory> || (cd <directory> && npm ls --depth=0 --json)
    ```

    **Returns:**

    ```python
    list[NpmPackageInfo]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/npm/npm_packages.py``.
    """

    # The reemote shape of pyinfra's ``npm.NpmPackages`` fact, with a
    # different reader. pyinfra scrapes the box-drawing tree
    # ``npm list -g --depth=0`` prints with
    # ``^[└├]\─\─\s([a-zA-Z0-9\-]+)@([0-9\.]+)$``; reemote asks npm for
    # ``--json`` and walks the dependency map, because the tree regex cannot
    # express a scoped name (no ``@`` or ``/`` in its character class), an
    # npm version outside its ``[0-9.]+`` class (a prerelease such as
    # ``1.1.0-beta.4``, or ``next``), or a package name with a digit-only
    # segment, and the tree characters are locale- and version-dependent. The
    # JSON answer is the same one the issue's own workaround probes.
    #
    # Two deliberate deviations from pyinfra beyond the parser. First,
    # pyinfra's ``directory`` variant still runs ``npm list -g --depth=0``
    # inside the directory, so it reports the *global* tree while
    # ``npm install`` in that directory mutates the *project* tree -- the
    # fact and the operation never describe the same store. reemote drops the
    # stray ``-g`` so the fact always reports the environment the operation
    # mutates, the rule the pip fact follows. Second, pyinfra's
    # ``requires_command = "npm"`` silently yields its ``default = dict`` when
    # npm is missing; reemote runs the command bare, so a missing npm exits
    # 127 and surfaces as ``CommandNotFoundError`` -- an environment without
    # npm is an error the deployer hears about, not a silently empty fact.
    #
    # The ``! test -d ... ||`` guard is kept from pyinfra: a project
    # directory that does not exist yet answers ``[]`` (npm's ``cd`` failure
    # is swallowed) instead of a nonzero exit, which is the "nothing
    # installed here yet" answer the operation needs in order to install into
    # it. ``npm ls`` also exits 1 when the tree has unmet dependencies;
    # ``Host.run_shell`` is called with ``check=False``, so the exit status is
    # ignored and the valid JSON on stdout is still parsed.

    @classmethod
    def command(
        cls,
        directory: str | None = None,
        prefix: str | None = None,
    ) -> str:
        """Shell listing one npm scope's direct dependencies over SSH.

        ``--depth=0`` limits the tree to the packages installed *at* the
        scope -- transitive dependencies are npm's own business and no
        ``npm install``/``npm uninstall`` a deploy issues asks about -- and
        ``--json`` asks for the machine-readable answer ``parse`` walks. The
        ``prefix`` argument relocates the global tree through
        ``NPM_CONFIG_PREFIX``, so a host with several npm prefixes can be
        queried about the one the operation will mutate; it has no effect on
        the project-directory form, where the manifest fixes the tree. The
        directory is ``shlex``-quoted.
        """
        env = _npm_env(prefix, False)
        if directory is None:
            return f"{env}npm ls -g --depth=0 --json"
        quoted = shlex.quote(directory)
        return f"! test -d {quoted} || (cd {quoted} && {env}npm ls --depth=0 --json)"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[NpmPackageInfo]:
        """Parse npm's JSON dependency tree into sorted, unique entries.

        The JSON document is the text from the first ``{`` onwards, so an npm
        warning line printed ahead of it is discarded; unparsable output reads
        as ``[]``. Only the top-level ``dependencies`` map contributes, since
        that is depth 0. An entry contributes its name and ``version`` unless
        it is not a mapping, is flagged ``missing`` (a declared dependency
        that is not installed, which npm reports with only ``required``), or
        carries no string version. A repeated name collapses into the entry
        seen last and the result is sorted by name. Empty output -- a bare
        prefix, a directory without a manifest, or a tree with no direct
        dependencies -- reads as ``[]``, the counterpart of pyinfra's
        ``default = dict``. Pure and testable.
        """
        text = "\n".join(output)
        start = text.find("{")
        if start == -1:
            return []
        try:
            decoded = json.loads(text[start:])
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError, RecursionError):
            return []
        if not isinstance(decoded, dict):
            return []
        dependencies = decoded.get("dependencies")
        if not isinstance(dependencies, dict):
            return []
        by_name: dict[str, str] = {}
        for name, entry in dependencies.items():
            if not isinstance(entry, dict) or entry.get("missing"):
                continue
            version = entry.get("version")
            if isinstance(version, str):
                by_name[str(name)] = version
        return [{"name": name, "version": by_name[name]} for name in sorted(by_name)]

    @classmethod
    async def process(
        cls,
        host: Host,
        directory: str | None = None,
        prefix: str | None = None,
    ) -> list[NpmPackageInfo]:
        npm_packages = getattr(host, "npm_packages", None)
        if npm_packages is not None:
            # SimulatedHost seam: the store maps an npm scope named by
            # ``_environment_key`` to that scope's name/version mapping, so a
            # simulated host can hold a global tree, a relocated-prefix tree
            # and any number of project trees at once. An absent scope is
            # simply nothing installed there. Copy each entry so callers never
            # mutate simulated state.
            store = npm_packages.get(_environment_key(directory, prefix), {})
            return [
                {"name": name, "version": version}
                for name, version in sorted(store.items())
            ]
        # Real transport: the JSON listing over asyncssh. ``check=False`` is
        # the default, so npm's exit 1 on an unmet dependency still hands
        # back its stdout.
        stdout = await host.run_shell(cls.command(directory, prefix))
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------


@dataclass(frozen=True)
class NpmInstallCommand(Command):
    """``npm install -g <packages>`` / ``cd <directory> && npm install <packages>``.

    The ``present=True`` direction of ``npm.packages`` (pyinfra's
    ``install_command``). ``packages`` holds npm's own tokens -- bare names
    and ``<pkg>@<version>`` pins alike, translated from the request's
    ``<pkg>=<version>`` spelling by ``_npm_package_spec`` -- and every token,
    like the project directory, is ``shlex``-quoted, so a metacharacter can
    never reach the remote shell unescaped. ``prefix`` and ``unsafe_perm``
    render as the ``NPM_CONFIG_PREFIX``/``NPM_CONFIG_UNSAFE_PERM``
    assignments immediately before ``npm``, the environment form Ansible's
    ``npm`` module uses.
    """

    packages: tuple[str, ...]
    directory: str | None = None
    prefix: str | None = None
    unsafe_perm: bool = False

    def shell(self) -> str:
        return _npm_invocation(
            "install",
            self.packages,
            directory=self.directory,
            prefix=self.prefix,
            unsafe_perm=self.unsafe_perm,
        )


@dataclass(frozen=True)
class NpmUpdateCommand(Command):
    """``npm update -g <packages>`` / ``cd <directory> && npm update <packages>``.

    The ``latest`` path of ``npm.packages`` (pyinfra's ``upgrade_command``).
    npm's ``update`` verb, not ``install --upgrade``, is the verb pyinfra
    builds here and the one that respects a manifest's semver ranges. Token
    order, quoting and the environment rendering are exactly as on
    ``NpmInstallCommand``.
    """

    packages: tuple[str, ...]
    directory: str | None = None
    prefix: str | None = None
    unsafe_perm: bool = False

    def shell(self) -> str:
        return _npm_invocation(
            "update",
            self.packages,
            directory=self.directory,
            prefix=self.prefix,
            unsafe_perm=self.unsafe_perm,
        )


@dataclass(frozen=True)
class NpmRemoveCommand(Command):
    """``npm uninstall -g <packages>`` / ``cd <directory> && npm uninstall <packages>``.

    The ``present=False`` direction of ``npm.packages`` (pyinfra's
    ``uninstall_command``). ``packages`` carries the *verbatim* request token
    -- name, or ``<pkg>@<version>`` for a pinned request -- because npm's
    ``uninstall`` accepts a versioned token (``npm uninstall left-pad@1.3.0``
    exits 0 whether or not that version is the installed one), so pyinfra's
    token can pass through unchanged; unlike pip there is no requirement
    syntax to strip. npm's ``uninstall`` is non-interactive, so no answer flag
    is needed. Quoting and the environment rendering match
    ``NpmInstallCommand``.
    """

    packages: tuple[str, ...]
    directory: str | None = None
    prefix: str | None = None
    unsafe_perm: bool = False

    def shell(self) -> str:
        return _npm_invocation(
            "uninstall",
            self.packages,
            directory=self.directory,
            prefix=self.prefix,
            unsafe_perm=self.unsafe_perm,
        )


@dataclass(frozen=True)
class NpmPackageJsonCommand(Command):
    """``cd <dirname(path)> && npm install``: install a manifest's dependencies.

    The ``package_json`` direction of ``npm.packages``, Ansible's
    ``npm: path=`` form: npm reads the manifest itself, so the command names
    no packages and no fact gates it. The install runs in the directory that
    holds the manifest -- the parent of ``path`` as npm's own resolution
    finds it -- and the project scope means no ``-g``. A bare relative
    filename has no parent to change into, so its command is the plain
    ``npm install``. The manifest path, the prefix and both environment
    variables are ``shlex``-quoted where they are user data.
    """

    path: str
    prefix: str | None = None
    unsafe_perm: bool = False

    def shell(self) -> str:
        env = _npm_env(self.prefix, self.unsafe_perm)
        directory = posixpath.dirname(self.path)
        if not directory:
            return f"{env}npm install"
        return f"cd {shlex.quote(directory)} && {env}npm install"


async def packages(
    ctx: OpContext,
    packages: str | list[str] | tuple[str, ...] | None = None,
    present: bool = True,
    latest: bool = False,
    global_: bool = True,
    directory: str | None = None,
    prefix: str | None = None,
    unsafe_perm: bool = False,
    package_json: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Install, update or remove npm packages.

    Args:
        ctx (OpContext): Execution context for the operation.
        packages (str | list[str] | tuple[str, ...] | None, optional): Packages
            to ensure. Prefer tuples when building requests via ``op()``, since
            ``OperationRequest`` kwargs must stay hashable. Versions can be
            pinned as ``<pkg>=<version>``, scoped names spelled
            ``@scope/name``. Defaults to None.
        present (bool, optional): Whether the packages should be installed.
            Defaults to True.
        latest (bool, optional): Whether to update installed packages without a
            pinned version. Defaults to False.
        global_ (bool, optional): Whether the packages live in npm's global
            prefix (``npm <verb> -g``) rather than one project directory. Named
            with a trailing underscore because ``global`` is a Python keyword.
            Defaults to True.
        directory (str | None, optional): The project directory to manage
            packages for; required when ``global_`` is false, forbidden when it
            is true. Defaults to None.
        prefix (str | None, optional): npm's global prefix, rendered as the
            ``NPM_CONFIG_PREFIX`` command environment variable, so the fact and
            the commands address a relocated global tree. Defaults to None.
        unsafe_perm (bool, optional): Add ``NPM_CONFIG_UNSAFE_PERM=true`` to the
            command environment, letting npm run lifecycle scripts while
            running as root. Defaults to False.
        package_json (str | None, optional): Path of a package.json whose
            dependencies to install; the manifest is applied on every run, the
            operation is stateless for it. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated npm package commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        ```python
        from reemote import op, npm

        # Install packages globally; a `<pkg>=<version>` pin is translated to
        # npm's own `<pkg>@<version>`. A root-owned global prefix needs
        # escalation.
        # -> npm install -g eslint typescript@5.3.3
        yield op(
            npm.packages,
            name="Install linting tools",
            packages=("eslint", "typescript=5.3.3"),
            sudo=True,
        )

        # Remove packages (present=False) -> npm uninstall -g bower
        yield op(
            npm.packages,
            name="Remove bower",
            packages=("bower",),
            present=False,
            sudo=True,
        )

        # Update installed, unpinned packages -> npm update -g webpack
        yield op(
            npm.packages,
            name="Update webpack",
            packages=("webpack",),
            latest=True,
            sudo=True,
        )

        # Manage a project directory instead of the global prefix; owned by the
        # login user, so no escalation.
        # -> cd /srv/app && npm install @vue/cli
        yield op(
            npm.packages,
            name="Install a project dependency",
            packages=("@vue/cli",),
            global_=False,
            directory="/srv/app",
        )

        # Install the dependencies declared in a manifest (stateless, runs every
        # time) -> cd /srv/app && npm install
        yield op(
            npm.packages,
            name="Install manifest dependencies",
            package_json="/srv/app/package.json",
        )
        ```

        Examples can be found in ``examples/modules/npm/npm_packages.py``.
    """

    # pyinfra ``npm.packages`` parity, extended to the surface the Ansible
    # ``npm`` module needs (issue #40).
    #
    # The ``NpmPackages`` fact supplies the installed versions, so a pinned
    # ``<pkg>=<version>`` is present only when that *exact* version is
    # installed (the ``ensure_packages`` version match, the same rule
    # ``apt.packages``, ``apk.packages`` and ``pip.packages`` follow), and
    # ``latest`` follows pyinfra's legacy behavior: every installed, unpinned
    # package is updated with ``npm update`` without checking whether an
    # update actually exists. Removal is version-aware like pyinfra's -- a
    # pinned request is uninstalled only when that version is the installed
    # one, so ``packages=("git@1.1",)`` against git 1.0 is a no-op -- and the
    # matched tokens pass through to ``npm uninstall`` verbatim.
    #
    # The user-facing pin is reemote's shared ``<pkg>=<version>`` spelling,
    # translated to npm's ``<pkg>@<version>`` for the shell and for the
    # removal token by ``_npm_package_spec`` -- the ``dnf`` ``pkg-version``
    # translation precedent. pyinfra's own ``<pkg>@<version>`` spelling is
    # therefore not accepted as input.
    #
    # ``package_json`` is stateless: like Ansible's ``npm: path=`` form it
    # always yields its command and never compares against installed state,
    # because resolving a manifest's semver ranges would need npm's own
    # solver, and it is yielded *before* any package command, mirroring pip's
    # ``requirements`` position.
    #
    # ``prefix`` and ``unsafe_perm`` are the two environment variables
    # Ansible's task sets that npm itself reads. Ansible's third, ``NODE_PATH``,
    # is deliberately out of scope: it steers Node's *runtime* module
    # resolution, not what ``npm install`` records as installed, so it changes
    # neither the fact's answer nor any command's effect. npm's ``--userconfig``
    # and ``--cache`` knobs are dropped for the same reason -- no package is
    # installed or removed by them.
    #
    # ``global_`` and ``directory`` are one mutually-exclusive scope choice,
    # validated up front because both are static request arguments -- raising
    # ``ValueError`` here cannot make the two phases disagree. Root-owning a
    # global prefix needs escalation, so flag such requests ``sudo=True``; a
    # project directory owned by the login user does not. ``**kwargs`` absorbs
    # pyinfra's global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``/``doas``).
    if global_ and directory is not None:
        raise ValueError(
            "npm.packages takes directory only when global_=False: a global "
            "install goes into npm's prefix, not a project directory"
        )
    if not global_ and directory is None:
        raise ValueError(
            "npm.packages with global_=False needs the directory whose "
            "package.json to manage"
        )

    reasons: list[str] = []

    # The manifest direction first, always: it is stateless, so the command is
    # yielded on every run.
    if package_json is not None:
        if not present:
            raise ValueError(
                "npm.packages cannot remove a package_json: npm installs a "
                "manifest but has no uninstall-from-manifest form"
            )
        ctx.changed = True
        reasons.append(f"package.json {package_json} to install")
        yield NpmPackageJsonCommand(
            path=package_json, prefix=prefix, unsafe_perm=unsafe_perm
        )

    # Normalize: accept a bare string, a list, or None.
    if packages is None:
        requested: tuple[str, ...] = ()
    elif isinstance(packages, str):
        requested = (packages,)
    else:
        requested = tuple(packages)

    if requested:
        # The scope the fact reads and the commands mutate are one scope:
        # npm's global prefix (optionally relocated by ``prefix``) or the
        # named project directory.
        scope = None if global_ else directory
        installed = await ctx.facts.get(
            NpmPackages, directory=scope, prefix=prefix
        )
        # The fact reports one entry per installed name; group the versions
        # by name so the lookups below read as the version-list comparison
        # ``ensure_packages`` does.
        versions_by_name: dict[str, list[str]] = {}
        for entry in installed:
            versions_by_name.setdefault(entry["name"], []).append(entry["version"])

        def is_installed(spec: str) -> bool:
            name, version = _split_version(spec)
            versions = versions_by_name.get(name)
            if versions is None:
                return False
            return version is None or version in versions

        if present:
            to_install = tuple(pkg for pkg in requested if not is_installed(pkg))
            # ``latest``: installed, unpinned packages are update candidates.
            to_upgrade = (
                tuple(
                    pkg
                    for pkg in requested
                    if _split_version(pkg)[1] is None and is_installed(pkg)
                )
                if latest
                else ()
            )
            if to_install:
                ctx.changed = True
                reasons.append(f"{len(to_install)} package(s) to install")
                yield NpmInstallCommand(
                    packages=tuple(_npm_package_spec(pkg) for pkg in to_install),
                    directory=scope,
                    prefix=prefix,
                    unsafe_perm=unsafe_perm,
                )
            if to_upgrade:
                ctx.changed = True
                reasons.append(f"{len(to_upgrade)} package(s) to upgrade")
                yield NpmUpdateCommand(
                    packages=tuple(_npm_package_spec(pkg) for pkg in to_upgrade),
                    directory=scope,
                    prefix=prefix,
                    unsafe_perm=unsafe_perm,
                )
            if not to_install and not to_upgrade:
                reasons.append("all packages present")
        else:
            # Version-aware like pyinfra (a pin is removed only when that
            # version is installed) and fed to npm verbatim, since npm's
            # uninstall accepts a ``<pkg>@<version>`` token. One token per
            # name: npm drops the whole package whatever selector rode with
            # it, so a request naming one package twice contributes once --
            # the first token the request spelled.
            removals: dict[str, str] = {}
            for package in requested:
                if is_installed(package):
                    token = _npm_package_spec(package)
                    removals.setdefault(_npm_split_spec(token)[0], token)
            to_remove = tuple(removals.values())
            if to_remove:
                ctx.changed = True
                reasons.append(f"{len(to_remove)} package(s) to remove")
                yield NpmRemoveCommand(
                    packages=to_remove,
                    directory=scope,
                    prefix=prefix,
                    unsafe_perm=unsafe_perm,
                )
            else:
                reasons.append("no packages to remove")
    else:
        if not reasons:
            reasons.append("no packages requested")

    ctx.reason = "; ".join(reasons)


__all__ = [
    "NpmInstallCommand",
    "NpmPackageInfo",
    "NpmPackageJsonCommand",
    "NpmPackages",
    "NpmRemoveCommand",
    "NpmUpdateCommand",
    "packages",
]
