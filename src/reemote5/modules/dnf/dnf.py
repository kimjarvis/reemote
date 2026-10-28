"""The ``dnf`` module: facts and operations in one source.

Fact side (design doc section 5): ``DnfPackages``, the fact read by
``dnf.packages`` to learn which packages are already installed,
``DnfRepositories``, the configured dnf repositories,
``DnfEnabledModules``, the enabled dnf modules, and
``DnfDisabledModules``, the explicitly disabled dnf modules. Like every
fact they are phase-unaware: each ``process`` coroutine just gathers the
raw value.

Operation side (design doc section 6): ``dnf.packages`` plus the typed
command descriptors it yields (``DnfInstallCommand``/``DnfRemoveCommand``/
``DnfUpdateCommand``/``DnfCleanCommand``). Operations are async generators
that receive an ``OpContext``, await facts, set ``ctx.changed``/
``ctx.reason``, and yield command descriptors; they never touch the host
directly.

``dnf.packages`` mirrors pyinfra's option set (``present``, ``latest``,
``update``, ``clean``, ``nobest``, ``extra_install_args``,
``extra_uninstall_args``) with the same names and semantics.
"""

import re
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass

from ...commands import Command
from ...core import OpContext
from ...hosts import Host

# --- Facts -------------------------------------------------------------------


class DnfPackages:
    """The set of installed dnf package names."""

    #: Shell used to list installed package names over SSH.
    COMMAND = "rpm -qa --queryformat '%{NAME}\n'"

    @classmethod
    async def process(cls, host: Host) -> set[str]:
        packages = getattr(host, "packages", None)
        if packages is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return set(packages)
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.COMMAND)
        return {line.strip() for line in stdout.splitlines() if line.strip()}


class DnfRepositories:
    """The configured dnf repositories, as a list of repo-option dicts.

    Mirrors pyinfra's ``dnf.DnfRepositories``: every ``[section]`` of the dnf
    config and ``.repo`` files becomes one dict keyed by ``repoid``/``name``
    (both defaulting to the section id), the source ``filename``, and each
    ``key=value`` option.
    """

    #: Marker prefixing each cat'd file in ``COMMAND``'s output, naming the
    #: source file so ``parse`` can attribute a repository to its ``.repo``
    #: file (pyinfra's ``REPO_FILENAME_MARKER``).
    FILENAME_MARKER = "#reemote5-filename:"

    #: Cat the dnf config and every ``.repo`` file, each prefixed by a marker
    #: line naming the source file. The shell expands the globs and skips
    #: missing files (including unexpanded globs); the trailing ``|| true``
    #: keeps a non-zero exit from failing the gather.
    COMMAND = (
        "for f in /etc/dnf.conf /etc/dnf.repos.d/*.repo /etc/yum.repos.d/*.repo; do "
        f'[ -f "$f" ] && {{ printf "{FILENAME_MARKER} %s\\n" "$f"; cat "$f"; }}; '
        "done || true"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[dict[str, str]]:
        """Parse INI-style repo config lines into a list of repository dicts.

        A ``[section]`` header starts a new repository whose ``repoid`` and
        ``name`` default to the section id; a preceding filename-marker line
        supplies its ``filename``; ``key=value`` lines fill in the remaining
        options (a ``name=`` line overrides the default); ``#`` comments and
        blank lines are ignored.
        """
        repos: list[dict[str, str]] = []
        current: dict[str, str] = {}
        current_file: str | None = None

        for raw in output:
            line = raw.strip()
            if not line:
                continue

            if line.startswith(cls.FILENAME_MARKER):
                if current:
                    repos.append(current)
                    current = {}
                current_file = line[len(cls.FILENAME_MARKER) :].strip() or None
                continue

            if line.startswith("#"):
                continue

            if line.startswith("["):
                if current:
                    repos.append(current)
                    current = {}
                current["repoid"] = line[1:-1]
                current["name"] = line[1:-1]
                if current_file is not None:
                    current["filename"] = current_file

            if current and "=" in line:
                key, value = re.split(r"\s*=\s*", line, maxsplit=1)
                current[key] = value

        if current:
            repos.append(current)

        return repos

    @classmethod
    async def process(cls, host: Host) -> list[dict[str, str]]:
        repositories = getattr(host, "repositories", None)
        if repositories is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return list(repositories)
        # Real transport: cat the repo files over asyncssh and parse the INI.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class DnfEnabledModules:
    """The enabled dnf modules, mapping module name to its enabled stream.

    Mirrors pyinfra's ``dnf.DnfEnabledModules``: every row of the
    ``dnf module list --enabled`` table carrying an ``[e]`` flag becomes a
    ``{name: stream}`` entry; headers, blank lines and the trailing hint
    line are ignored.
    """

    #: Shell used to list the enabled modules over SSH.
    COMMAND = "dnf module list --enabled"

    #: Matches the ``[e]`` enabled flag on a stream but not the ``[e]nabled``
    #: word in the table's trailing ``Hint:`` line (pyinfra's
    #: ``_ENABLED_FLAG``).
    ENABLED_FLAG = re.compile(r"\[e\](?!\w)")

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, str]:
        """Parse ``dnf module list --enabled`` rows into a name-to-stream map.

        Only lines carrying the ``[e]`` flag are kept; the first two
        whitespace-separated tokens of such a line are the module name and
        its enabled stream.
        """
        modules: dict[str, str] = {}
        for line in output:
            if not cls.ENABLED_FLAG.search(line):
                continue
            tokens = line.split()
            if len(tokens) < 2:
                continue
            name, stream = tokens[0], tokens[1]
            modules[name] = stream
        return modules

    @classmethod
    async def process(cls, host: Host) -> dict[str, str]:
        enabled_modules = getattr(host, "enabled_modules", None)
        if enabled_modules is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return dict(enabled_modules)
        # Real transport: list the enabled modules over asyncssh and parse.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class DnfDisabledModules:
    """The sorted names of the explicitly disabled dnf modules.

    Mirrors pyinfra's ``dnf.DnfDisabledModules``: every row of the
    ``dnf module list --disabled`` table carrying an ``[x]`` flag contributes
    its module name; a module disabled across several streams is listed once,
    and the names are returned sorted. Headers, blank lines and the trailing
    hint line are ignored.
    """

    #: Shell used to list the disabled modules over SSH.
    COMMAND = "dnf module list --disabled"

    #: Matches the ``[x]`` disabled flag on a stream but not the ``[x]disabled``
    #: word in the table's trailing ``Hint:`` line (pyinfra's
    #: ``_DISABLED_FLAG``).
    DISABLED_FLAG = re.compile(r"\[x\](?!\w)")

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[str]:
        """Parse ``dnf module list --disabled`` rows into a sorted name list.

        Only lines carrying the ``[x]`` flag are kept; the first
        whitespace-separated token of such a line is the module name. Names
        are deduplicated across streams and returned sorted.
        """
        seen: set[str] = set()
        for line in output:
            if not cls.DISABLED_FLAG.search(line):
                continue
            tokens = line.split()
            if not tokens:
                continue
            seen.add(tokens[0])
        return sorted(seen)

    @classmethod
    async def process(cls, host: Host) -> list[str]:
        disabled_modules = getattr(host, "disabled_modules", None)
        if disabled_modules is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return list(disabled_modules)
        # Real transport: list the disabled modules over asyncssh and parse.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------

#: Package versions can be pinned as ``<pkg>=<version>`` (pyinfra's dnf
#: ``version_join``).
_VERSION_JOIN = "="


def _split_version(package: str) -> tuple[str, str | None]:
    """Split ``pkg=version`` into ``(name, version)``; version is None if absent."""
    name, sep, version = package.partition(_VERSION_JOIN)
    return name, (version if sep else None)


@dataclass(frozen=True)
class DnfInstallCommand(Command):
    packages: tuple[str, ...]
    nobest: bool = False
    extra_args: str | None = None

    def shell(self) -> str:
        parts = ["dnf", "install", "-y"]
        if self.nobest:
            parts.append("--nobest")
        if self.extra_args:
            parts.append(self.extra_args)
        parts.extend(self.packages)
        return " ".join(parts)


@dataclass(frozen=True)
class DnfRemoveCommand(Command):
    packages: tuple[str, ...]
    extra_args: str | None = None

    def shell(self) -> str:
        parts = ["dnf", "remove", "-y"]
        if self.extra_args:
            parts.append(self.extra_args)
        parts.extend(self.packages)
        return " ".join(parts)


@dataclass(frozen=True)
class DnfUpdateCommand(Command):
    """``dnf update``: all packages when bare, just ``packages`` when given
    (the ``latest`` upgrade path)."""

    packages: tuple[str, ...] = ()

    def shell(self) -> str:
        parts = ["dnf", "update", "-y"]
        parts.extend(self.packages)
        return " ".join(parts)


@dataclass(frozen=True)
class DnfCleanCommand(Command):
    """``dnf clean all`` (the ``clean`` option)."""

    def shell(self) -> str:
        return "dnf clean all"


async def packages(
    ctx: OpContext,
    packages: str | list[str] | tuple[str, ...] | None = None,
    present: bool = True,
    latest: bool = False,
    update: bool = False,
    clean: bool = False,
    nobest: bool = False,
    extra_install_args: str | None = None,
    extra_uninstall_args: str | None = None,
    **kwargs,
) -> AsyncGenerator[Command]:
    """Install/remove/update dnf packages (pyinfra ``dnf.packages`` parity).

    + packages: packages to ensure; a single string, a list/tuple, or None.
      Prefer tuples when building requests via ``op()``, since
      ``OperationRequest`` kwargs must stay hashable. Versions can be
      pinned as ``<pkg>=<version>``.
    + present: whether the packages should be installed
    + latest: whether to upgrade installed packages without a pinned version
    + update: run ``dnf update`` before installing packages
    + clean: run ``dnf clean all`` before installing packages
    + nobest: add the ``--nobest`` option to the install command
    + extra_install_args: additional arguments to the dnf install command
    + extra_uninstall_args: additional arguments to the dnf remove command

    ``clean`` and ``update`` are stateless (always yield, like pyinfra's
    non-idempotent operations). The ``DnfPackages`` fact reports names
    only, so a pinned ``<pkg>=<version>`` is considered present when
    *any* version of ``pkg`` is installed, and ``latest`` follows
    pyinfra's name-only behavior: every installed, unpinned package is
    upgraded without checking whether an upgrade actually exists.
    ``kwargs`` absorbs any further keyword arguments (pyinfra's global
    arguments are meta-parameters handled by ``op()`` here).
    """
    reasons: list[str] = []

    if clean:
        ctx.changed = True
        reasons.append("dnf clean")
        yield DnfCleanCommand()

    if update:
        ctx.changed = True
        reasons.append("dnf update")
        yield DnfUpdateCommand()

    # Normalize: pyinfra accepts a bare string, a list, or None.
    if packages is None:
        requested: tuple[str, ...] = ()
    elif isinstance(packages, str):
        requested = (packages,)
    else:
        requested = tuple(packages)

    if requested:
        installed = await ctx.facts.get(DnfPackages)

        if present:
            to_install = tuple(
                pkg for pkg in requested if _split_version(pkg)[0] not in installed
            )
            # ``latest``: installed, unpinned packages are upgrade candidates.
            to_upgrade = (
                tuple(
                    pkg
                    for pkg in requested
                    if _split_version(pkg)[1] is None and pkg in installed
                )
                if latest
                else ()
            )
            if to_install:
                ctx.changed = True
                reasons.append(f"{len(to_install)} package(s) to install")
                yield DnfInstallCommand(
                    packages=to_install,
                    nobest=nobest,
                    extra_args=extra_install_args,
                )
            if to_upgrade:
                ctx.changed = True
                reasons.append(f"{len(to_upgrade)} package(s) to upgrade")
                yield DnfUpdateCommand(packages=to_upgrade)
            if not to_install and not to_upgrade:
                reasons.append("all packages present")
        else:
            to_remove = tuple(
                pkg for pkg in requested if _split_version(pkg)[0] in installed
            )
            if to_remove:
                ctx.changed = True
                reasons.append(f"{len(to_remove)} package(s) to remove")
                yield DnfRemoveCommand(
                    packages=to_remove, extra_args=extra_uninstall_args
                )
            else:
                reasons.append("no packages to remove")
    elif not reasons:
        reasons.append("no packages requested")

    ctx.reason = "; ".join(reasons)


__all__ = [
    "DnfCleanCommand",
    "DnfDisabledModules",
    "DnfEnabledModules",
    "DnfInstallCommand",
    "DnfPackages",
    "DnfRemoveCommand",
    "DnfRepositories",
    "DnfUpdateCommand",
    "packages",
]
