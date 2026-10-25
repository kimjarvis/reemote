"""The ``dnf`` operation namespace (design doc section 6).

Exposes ``dnf.packages`` plus the typed command descriptors it yields
(``DnfInstallCommand``/``DnfRemoveCommand``/``DnfUpdateCommand``/
``DnfCleanCommand``). Operations are async generators that receive an
``OpContext``, await facts, set ``ctx.changed``/``ctx.reason``, and yield
command descriptors; they never touch the host directly.

``dnf.packages`` mirrors pyinfra's option set (``present``, ``latest``,
``update``, ``clean``, ``nobest``, ``extra_install_args``,
``extra_uninstall_args``) with the same names and semantics.
"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass

from ..commands import Command
from ..core import OpContext
from ..facts.dnf import DnfPackages

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
    "DnfInstallCommand",
    "DnfRemoveCommand",
    "DnfUpdateCommand",
    "packages",
]
