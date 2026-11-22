# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``gem`` module: facts and operations in one source.
#
# Fact side: ``GemPackages`` reports the installed RubyGems packages as
# ``GemPackageInfo`` entries (gem name plus one installed version), parsed
# from the ``name (versions)`` rows of ``gem list --local`` -- one entry per
# name/version pair, because gem keeps every installed version side by side.
# Like every fact it is phase-unaware: its ``process`` coroutine just
# gathers the raw value.
#
# Operation side: ``gem.packages`` installs, updates or removes gems --
# bare names or ``<pkg>=<version>`` pins -- system-wide or into the invoking
# user's gem directory, through its ``GemInstallCommand``/``GemUpdateCommand``/
# ``GemRemoveCommand`` descriptors. Operations are async generators that
# receive an ``OpContext``, await facts, set ``ctx.changed``/``ctx.reason``,
# and yield command descriptors; they never touch the host directly.


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
    ``apt``/``dnf``/``apk``/``npm`` (whose ``_split_version`` this is the copy
    of): split at the *last* ``=`` so a version string carrying ``=`` stays
    whole, and return both halves verbatim. Pure and testable.
    """
    name, sep, version = package.rpartition("=")
    return (name, version) if sep else (package, None)


def _gem_package_spec(package: str) -> str:
    """Translate a ``<pkg>=<version>`` request into gem's ``<pkg>:<version>``.

    The user-facing pin is reemote's shared ``=`` spelling; ``gem install``
    accepts a version requirement appended to the name with ``:``, which is
    also pyinfra's ``version_join`` for this operation. An unpinned name is
    returned verbatim. Pure and testable.
    """
    name, version = _split_version(package)
    return f"{name}:{version}" if version is not None else name


def _gem_split_spec(token: str) -> tuple[str, str | None]:
    """Split gem's ``<pkg>:<version>`` token into ``(name, version)``.

    The exact inverse of ``_gem_package_spec``, splitting at the *last* ``:``.
    A gem name never carries a colon (the character class RubyGems validates a
    name against excludes it), so the split is unambiguous.
    ``SimulatedHost``'s gem seam uses it to record which name and version a
    ``gem install`` token touches; the library's own comparison goes the other
    way, from the user-facing ``=`` pin.
    """
    name, sep, version = token.rpartition(":")
    if not sep or not name:
        return token, None
    return name, version


def _gem_env(gem_home: str | None, gem_path: str | None) -> str:
    """The ``VAR=value `` assignments to place in front of a ``gem`` call.

    ``gem_home`` renders as ``GEM_HOME`` (the tree an install writes into and
    a listing reads) and ``gem_path`` as ``GEM_PATH`` (the lookup path) -- the
    two environment variables the ``gem`` CLI itself reads, which is how a
    non-default Ruby installation (rbenv, rvm, a relocated prefix) is
    addressed, the ``NPM_CONFIG_PREFIX`` rendering npm uses. Each value is
    ``shlex``-quoted, so a path carrying a metacharacter cannot break out of
    the assignment. Returns the empty string when neither is requested, so
    callers can concatenate it unconditionally. Because an assignment only
    covers the command it precedes, this fragment must sit directly before
    the gem executable.
    """
    parts = []
    if gem_home:
        parts.append(f"GEM_HOME={shlex.quote(gem_home)}")
    if gem_path:
        parts.append(f"GEM_PATH={shlex.quote(gem_path)}")
    return "".join(f"{part} " for part in parts)


def _scope_key(
    user_install: bool = False,
    gem_home: str | None = None,
    gem_path: str | None = None,
) -> str:
    """Key one gem store in the simulated package state.

    gem keeps a separate tree per scope: the system ``GEM_HOME``, the
    invoking user's ``Gem.user_dir`` (what ``--user-install`` selects), and
    any tree an explicit ``GEM_HOME``/``GEM_PATH`` override relocates to. The
    key names the scope the fact reports and the commands mutate, so a
    simulated host can hold several independent trees and the fact seam, the
    mutators and ``gem.packages`` all agree on which one a request touches.
    An explicit override wins over the user scope -- the operation refuses
    the combination anyway, but the fact's own command renders the same
    precedence. Pure and testable.
    """
    if gem_home is not None:
        return f"gem_home={gem_home}"
    if gem_path is not None:
        return f"gem_path={gem_path}"
    return "user-install" if user_install else "system"


# --- Facts -------------------------------------------------------------------


class GemPackageInfo(TypedDict):
    """
    Attributes:
        name: The gem name exactly as ``gem list`` spells it (``"rake"``,
            ``"io-console"``).
        version: One installed version, platform tag and ``default:``
            annotation stripped (``"13.0.6"``). A gem installed at several
            versions contributes one entry per version.
    """

    # One gem at one version -- the entry of the ``GemPackages`` fact. A
    # TypedDict rather than a class instance: the answer is an ordinary
    # name/version pair, so equality against a literal dict works and
    # ``json.dumps`` serializes it directly -- the
    # ``NpmPackageInfo``/``PipPackageInfo`` convention of every package-list
    # fact in this codebase. Unlike npm there can be several entries per
    # name: gem keeps every installed version side by side, which is exactly
    # what pyinfra's ``dict[str, list[str]]`` return models. Gem names are
    # compared verbatim -- RubyGems names are case-sensitive -- so, unlike
    # pip's PEP 503 folding, there is no normalization to share.

    name: str
    version: str


class GemPackages:
    """The installed gems, as ``GemPackageInfo`` entries.

    **Commands:**

    ```bash
    gem list --local
    GEM_HOME="$(gem env user_gemhome)" GEM_PATH="$(gem env user_gemhome)" gem list --local
    ```

    **Returns:**

    ```python
    list[GemPackageInfo]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/gem/gem_gem_packages.py``.
    """

    # The reemote shape of pyinfra's ``gem.GemPackages`` fact, reading the
    # same ``gem list --local`` command. pyinfra parses each line with
    # ``^([a-zA-Z0-9\-\+\_]+)\s\(([0-9\.]+)\)$``, a regex that matches only a
    # *single* bare-numeric version: a multi-version row (``json (2.6.3,
    # 2.6.1)``), a platform-suffixed one (``nokogiri (1.16.5
    # x86_64-linux)``), a default-gem annotation (``stringio (default:
    # 3.0.1)``) and a name carrying a ``.`` all fall out of pyinfra's answer
    # silently. reemote's ``parse`` keeps every row gem prints: the versions
    # inside the parentheses are split on ``,``, a leading ``default:`` or
    # trailing `` default`` annotation is dropped (a default gem IS installed
    # at that version, which matters when an operation must not re-install
    # it), and a platform tag is dropped by taking the first whitespace token
    # of each version.
    #
    # pyinfra returns a ``dict[str, list[str]]``; reemote reports sorted,
    # unique ``GemPackageInfo`` entries -- the deterministic, JSON-printable
    # answer shape every package-list fact here uses (the ``NpmPackages``
    # precedent), widened to one entry per name/version pair because gem
    # genuinely keeps several versions installed at once. And pyinfra's
    # ``requires_command = "gem"`` silently yields its ``default = dict``
    # when gem is missing; reemote runs the command bare, so a missing gem
    # exits 127 and surfaces as ``CommandNotFoundError`` -- an environment
    # without gem is an error the deployer hears about, not a silently empty
    # fact (npm's precedent).
    #
    # ``user_install`` cannot ride on the listing as a flag: ``gem list`` has
    # no ``--user-install`` option (``gem install``/``uninstall``/``update``
    # do, ``gem uninstall`` even defaults it on). The user store is therefore
    # addressed the way gem itself exposes it -- ``gem env user_gemhome``
    # prints "the path where gems are installed when --user-install is
    # given" -- pinned into both ``GEM_HOME`` and ``GEM_PATH`` so the listing
    # reads exactly the tree a ``--user-install`` command writes. An explicit
    # ``gem_home``/``gem_path`` override wins: it relocates the whole store,
    # user flag or not (``gem.packages`` refuses the ambiguous combination).

    #: One ``gem list`` row: ``name (version[, version]...)``, the name over
    #: the characters RubyGems allows in a gem name (letters, digits and the
    #: ``.``, ``_``, ``+``, ``-`` punctuation) and the parenthesized part
    #: taken whole, since a version token may carry a platform tag or a
    #: ``default:`` annotation ``parse`` strips per token. The
    #: ``*** LOCAL GEMS ***`` header, blank lines and any warning row match
    #: nothing and are skipped.
    PACKAGE_REGEX: ClassVar[re.Pattern[str]] = re.compile(
        r"^([A-Za-z0-9._+\-]+)\s\(([^)]+)\)$"
    )

    @classmethod
    def command(
        cls,
        user_install: bool = False,
        gem_home: str | None = None,
        gem_path: str | None = None,
        gem_bin: str = "gem",
    ) -> str:
        """Shell listing one gem store over SSH.

        ``gem list --local`` prints the ``name (versions)`` rows ``parse``
        reads. The ``user_install`` scope pins ``GEM_HOME``/``GEM_PATH`` to
        ``gem env user_gemhome`` -- the user tree a ``--user-install``
        install writes -- unless an explicit ``gem_home``/``gem_path``
        override relocates the store instead. The executable and every
        override are ``shlex``-quoted, so a path carrying a metacharacter
        cannot break out of the argument.
        """
        binary = shlex.quote(gem_bin)
        if user_install and gem_home is None and gem_path is None:
            user_dir = f"$({binary} env user_gemhome)"
            env = f'GEM_HOME="{user_dir}" GEM_PATH="{user_dir}" '
        else:
            env = _gem_env(gem_home, gem_path)
        return f"{env}{binary} list --local"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[GemPackageInfo]:
        """Parse ``gem list`` rows into sorted, unique entries.

        Only lines matching ``PACKAGE_REGEX`` contribute -- the header, blank
        lines and warnings fall out. Each version token inside the
        parentheses is stripped of its ``default:``/``default`` annotation and
        its platform tag, and contributes one ``(name, version)`` pair; the
        result is de-duplicated and sorted by ``(name, version)``. Empty
        output -- a bare store -- reads as ``[]``, the counterpart of
        pyinfra's ``default = dict``. Pure and testable.
        """
        rows: set[tuple[str, str]] = set()
        for line in output:
            match = cls.PACKAGE_REGEX.match(line.strip())
            if not match:
                continue
            name = match.group(1)
            for token in match.group(2).split(","):
                token = token.strip()
                if token.startswith("default:"):
                    token = token[len("default:") :].strip()
                elif token.endswith(" default"):
                    token = token[: -len(" default")].strip()
                version = token.split(" ", 1)[0]
                if version:
                    rows.add((name, version))
        return [{"name": name, "version": version} for name, version in sorted(rows)]

    @classmethod
    async def process(
        cls,
        host: Host,
        user_install: bool = False,
        gem_home: str | None = None,
        gem_path: str | None = None,
        gem_bin: str = "gem",
    ) -> list[GemPackageInfo]:
        gem_packages = getattr(host, "gem_packages", None)
        if gem_packages is not None:
            # SimulatedHost seam: the store maps a gem scope named by
            # ``_scope_key`` to that scope's name -> version-list mapping, so
            # a simulated host can hold a system tree, a user tree and any
            # number of relocated trees at once. An absent scope is simply
            # nothing installed there. Copy each entry so callers never
            # mutate simulated state.
            store = gem_packages.get(_scope_key(user_install, gem_home, gem_path), {})
            return [
                {"name": name, "version": version}
                for name, versions in sorted(store.items())
                for version in sorted(set(versions))
            ]
        # Real transport: the listing over asyncssh.
        stdout = await host.run_shell(
            cls.command(user_install, gem_home, gem_path, gem_bin)
        )
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------


@dataclass(frozen=True)
class GemInstallCommand(Command):
    """``gem install [--user-install|--no-user-install] <packages>``.

    The ``present=True`` direction of ``gem.packages`` (pyinfra's
    ``install_command``). ``packages`` holds gem's own tokens -- bare names
    and ``<pkg>:<version>`` pins alike, translated from the request's
    ``<pkg>=<version>`` spelling by ``_gem_package_spec`` -- and every token,
    like the bindir and the environment overrides, is ``shlex``-quoted, so a
    metacharacter can never reach the remote shell unescaped. The scope flag
    is rendered in *both* directions -- ``--user-install`` when asked,
    ``--no-user-install`` otherwise -- so the tree written is always the one
    the fact read, never the invoking user's home by gem's own writable-GEM_HOME
    heuristic. ``bindir`` is Ansible's ``bindir``: the directory gem places
    the gems' executables in.
    """

    packages: tuple[str, ...]
    user_install: bool = False
    bindir: str | None = None
    gem_home: str | None = None
    gem_path: str | None = None
    gem_bin: str = "gem"

    def shell(self) -> str:
        parts = [
            f"{_gem_env(self.gem_home, self.gem_path)}{shlex.quote(self.gem_bin)}",
            "install",
            "--user-install" if self.user_install else "--no-user-install",
        ]
        if self.bindir is not None:
            parts += ["--bindir", shlex.quote(self.bindir)]
        parts.extend(shlex.quote(package) for package in self.packages)
        return " ".join(parts)


@dataclass(frozen=True)
class GemUpdateCommand(Command):
    """``gem update [--user-install|--no-user-install] <packages>``.

    The ``latest`` path of ``gem.packages`` (pyinfra's ``upgrade_command``).
    gem's ``update`` verb, not ``install --no-lock``, is the verb pyinfra
    builds here. ``packages`` carries bare names only -- a pinned request is
    never an upgrade candidate, exactly as in pyinfra's ``ensure_packages``.
    Flag order, quoting and the environment rendering are exactly as on
    ``GemInstallCommand``.
    """

    packages: tuple[str, ...]
    user_install: bool = False
    bindir: str | None = None
    gem_home: str | None = None
    gem_path: str | None = None
    gem_bin: str = "gem"

    def shell(self) -> str:
        parts = [
            f"{_gem_env(self.gem_home, self.gem_path)}{shlex.quote(self.gem_bin)}",
            "update",
            "--user-install" if self.user_install else "--no-user-install",
        ]
        if self.bindir is not None:
            parts += ["--bindir", shlex.quote(self.bindir)]
        parts.extend(shlex.quote(package) for package in self.packages)
        return " ".join(parts)


@dataclass(frozen=True)
class GemRemoveCommand(Command):
    """``gem uninstall [--user-install|--no-user-install] ... <names>``.

    The ``present=False`` direction of ``gem.packages`` (pyinfra's
    ``uninstall_command``), rendered non-interactive -- a deliberate
    deviation from pyinfra's bare ``gem uninstall <pkg>``, which stops at up
    to three prompts an unattended deploy cannot answer: a version choice
    when several versions are installed, the executables confirmation and the
    dependent-gem confirmation. An unpinned removal therefore carries ``-a``
    (all installed versions of each name), a pinned one ``--version <v>``
    (that exact version), and both carry ``-x`` (drop the executables without
    asking) and ``-I`` (ignore gems that depend on the removed one, the check
    whose prompt the reference documents). ``names`` holds bare names: unlike
    npm's uninstall, ``gem uninstall`` has no ``name:version`` token, so a
    pinned removal is its own command with the version as a flag.
    """

    names: tuple[str, ...]
    version: str | None = None
    user_install: bool = False
    bindir: str | None = None
    gem_home: str | None = None
    gem_path: str | None = None
    gem_bin: str = "gem"

    def shell(self) -> str:
        parts = [
            f"{_gem_env(self.gem_home, self.gem_path)}{shlex.quote(self.gem_bin)}",
            "uninstall",
            "--user-install" if self.user_install else "--no-user-install",
        ]
        if self.bindir is not None:
            parts += ["--bindir", shlex.quote(self.bindir)]
        if self.version is None:
            parts.append("-a")
        else:
            parts += ["--version", shlex.quote(self.version)]
        parts += ["-x", "-I"]
        parts.extend(shlex.quote(name) for name in self.names)
        return " ".join(parts)


async def packages(
    ctx: OpContext,
    packages: str | list[str] | tuple[str, ...] | None = None,
    present: bool = True,
    latest: bool = False,
    user_install: bool = False,
    bindir: str | None = None,
    gem_home: str | None = None,
    gem_path: str | None = None,
    gem_bin: str = "gem",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Install, update or remove RubyGems packages.

    Args:
        ctx (OpContext): Execution context for the operation.
        packages (str | list[str] | tuple[str, ...] | None, optional): Gems to
            ensure. Prefer tuples when building requests via ``op()``, since
            ``OperationRequest`` kwargs must stay hashable. Versions can be
            pinned as ``<pkg>=<version>``. Defaults to None.
        present (bool, optional): Whether the gems should be installed.
            Defaults to True.
        latest (bool, optional): Whether to update installed gems without a
            pinned version. Defaults to False.
        user_install (bool, optional): Whether the gems live in the invoking
            user's gem directory (``--user-install``, Ansible's
            ``user_install``) rather than the system store. Mutually exclusive
            with ``gem_home``/``gem_path``. Defaults to False.
        bindir (str | None, optional): The directory gem places executables
            in, rendered as ``--bindir <dir>`` (Ansible's ``bindir``).
            Defaults to None.
        gem_home (str | None, optional): ``GEM_HOME`` override, rendered as a
            command-environment assignment so the fact and the commands
            address a relocated store. Defaults to None.
        gem_path (str | None, optional): ``GEM_PATH`` override, rendered as a
            command-environment assignment. Defaults to None.
        gem_bin (str | None, optional): Path or name of the ``gem``
            executable, Ansible's ``executable``. Defaults to ``"gem"``.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated gem package commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        ```python
        from reemote import gem, op

        # Install gems system-wide; a `<pkg>=<version>` pin is translated to
        # gem's own `<pkg>:<version>` token. A system store is root-owned, so
        # the request needs escalation.
        # -> gem install --no-user-install rspec
        yield op(
            gem.packages,
            name="Install rspec",
            packages=("rspec",),
            sudo=True,
        )

        # The ruby role's Bundler task: user_install=false maps to the
        # system-wide scope, and the run-as user comes from the inventory
        # `privilege`, never the operation.
        # -> gem install --no-user-install bundler
        yield op(
            gem.packages,
            name="Install Bundler",
            packages=("bundler",),
            sudo=True,
        )

        # Remove gems (present=False), all installed versions of each name
        # -> gem uninstall --no-user-install -a -x -I bower
        yield op(
            gem.packages,
            name="Remove bower",
            packages=("bower",),
            present=False,
            sudo=True,
        )

        # Update installed, unpinned gems -> gem update --no-user-install rails
        yield op(
            gem.packages,
            name="Update rails",
            packages=("rails",),
            latest=True,
            sudo=True,
        )
        ```

        Examples can be found in ``examples/modules/gem/gem_packages.py``.
    """

    # pyinfra ``gem.packages`` parity, extended to the surface the Ansible
    # ``gem`` module needs (issue #57).
    #
    # The ``GemPackages`` fact supplies the installed versions, so a pinned
    # ``<pkg>=<version>`` is present only when that *exact* version is
    # installed (the ``ensure_packages`` version match, the same rule
    # ``apt.packages``, ``npm.packages`` and ``pip.packages`` follow), and
    # ``latest`` follows pyinfra's legacy behavior: every installed, unpinned
    # gem is updated with ``gem update`` without checking whether an update
    # actually exists. Removal is version-aware like pyinfra's -- a pinned
    # request is uninstalled only when that version is the installed one, so
    # ``packages=("rake=13.2",)`` against rake 13.0.6 is a no-op.
    #
    # The user-facing pin is reemote's shared ``<pkg>=<version>`` spelling,
    # translated to gem's own ``<pkg>:<version>`` install token by
    # ``_gem_package_spec`` -- pyinfra's ``version_join=":"`` -- while a
    # removal renders the version as ``gem uninstall --version``, the only
    # grammar uninstall understands. pyinfra's ``:`` input spelling is
    # therefore not accepted.
    #
    # ``user_install``, ``bindir`` and the ``GEM_HOME``/``GEM_PATH``/gem-bin
    # overrides are the Ansible-module surface the namespace was added to
    # cover; pyinfra has none of them. ``user_install`` and an explicit
    # ``gem_home``/``gem_path`` are two ways of choosing the store, so the
    # combination is refused up front with ``ValueError`` -- both are static
    # request arguments, so raising here cannot make the two phases disagree
    # (npm's ``global_``/``directory`` validation precedent).
    #
    # Installing into the system store writes root-owned state, so flag such
    # requests ``sudo=True``; a ``user_install`` request into the login
    # user's own directory does not. ``**kwargs`` absorbs pyinfra's global
    # arguments (``op()`` already strips ``name``/``sudo``/``su``/``doas``).
    if user_install and (gem_home is not None or gem_path is not None):
        raise ValueError(
            "gem.packages takes gem_home/gem_path only when user_install=False: "
            "--user-install selects the invoking user's gem directory, while "
            "GEM_HOME/GEM_PATH relocate the store -- one scope choice, not both"
        )

    reasons: list[str] = []

    # Normalize: accept a bare string, a list, or None.
    if packages is None:
        requested: tuple[str, ...] = ()
    elif isinstance(packages, str):
        requested = (packages,)
    else:
        requested = tuple(packages)

    if requested:
        # The scope the fact reads and the commands mutate are one scope:
        # the system store, the user store, or an explicitly relocated one.
        installed = await ctx.facts.get(
            GemPackages,
            user_install=user_install,
            gem_home=gem_home,
            gem_path=gem_path,
            gem_bin=gem_bin,
        )
        # The fact reports one entry per installed name/version pair; group
        # the versions by name so the lookups below read as the version-list
        # comparison ``ensure_packages`` does.
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
            # ``latest``: installed, unpinned gems are update candidates.
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
                yield GemInstallCommand(
                    packages=tuple(_gem_package_spec(pkg) for pkg in to_install),
                    user_install=user_install,
                    bindir=bindir,
                    gem_home=gem_home,
                    gem_path=gem_path,
                    gem_bin=gem_bin,
                )
            if to_upgrade:
                ctx.changed = True
                reasons.append(f"{len(to_upgrade)} package(s) to upgrade")
                yield GemUpdateCommand(
                    packages=to_upgrade,
                    user_install=user_install,
                    bindir=bindir,
                    gem_home=gem_home,
                    gem_path=gem_path,
                    gem_bin=gem_bin,
                )
            if not to_install and not to_upgrade:
                reasons.append("all packages present")
        else:
            # Version-aware like pyinfra: a pinned request is removed only
            # when that version is installed. A bare name removes *all*
            # versions of that name (the ``-a`` flag), so it subsumes a pin
            # on the same name; pinned removals are one command each, since
            # ``--version`` is a whole-command flag. One entry per distinct
            # name (or name/version pair), in the order the request spelled
            # them -- the request sequence must stay deterministic.
            bare: list[str] = []
            pinned: list[tuple[str, str]] = []
            seen: set[str | tuple[str, str]] = set()
            for package in requested:
                name, version = _split_version(package)
                if not is_installed(package):
                    continue
                if version is None:
                    if name not in bare:
                        bare.append(name)
                    seen.add(name)
                elif name not in seen and (name, version) not in seen:
                    pinned.append((name, version))
                    seen.add((name, version))
            # A pin whose name is also requested bare rides the ``-a``
            # removal of the whole name.
            pinned = [pair for pair in pinned if pair[0] not in bare]
            removals = len(bare) + len(pinned)
            if removals:
                ctx.changed = True
                reasons.append(f"{removals} package(s) to remove")
                for name, version in pinned:
                    yield GemRemoveCommand(
                        names=(name,),
                        version=version,
                        user_install=user_install,
                        bindir=bindir,
                        gem_home=gem_home,
                        gem_path=gem_path,
                        gem_bin=gem_bin,
                    )
                if bare:
                    yield GemRemoveCommand(
                        names=tuple(bare),
                        user_install=user_install,
                        bindir=bindir,
                        gem_home=gem_home,
                        gem_path=gem_path,
                        gem_bin=gem_bin,
                    )
            else:
                reasons.append("no packages to remove")
    else:
        reasons.append("no packages requested")

    ctx.reason = "; ".join(reasons)


__all__ = [
    "GemInstallCommand",
    "GemPackageInfo",
    "GemPackages",
    "GemRemoveCommand",
    "GemUpdateCommand",
    "packages",
]
