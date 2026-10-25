"""The ``dnf`` operation namespace (design doc section 6).

Exposes ``dnf.packages`` plus the typed command descriptors it yields
(``DnfInstallCommand``/``DnfRemoveCommand``). Operations are async
generators that receive an ``OpContext``, await facts, set
``ctx.changed``/``ctx.reason``, and yield command descriptors; they never
touch the host directly.
"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass

from ..commands import Command
from ..core import OpContext
from ..facts.dnf import DnfPackages


@dataclass(frozen=True)
class DnfInstallCommand(Command):
    packages: tuple[str, ...]

    def shell(self) -> str:
        return f"dnf install -y {' '.join(self.packages)}"


@dataclass(frozen=True)
class DnfRemoveCommand(Command):
    packages: tuple[str, ...]

    def shell(self) -> str:
        return f"dnf remove -y {' '.join(self.packages)}"


async def packages(
    ctx: OpContext, packages: tuple[str, ...], present: bool = True
) -> AsyncGenerator[Command]:
    installed = await ctx.facts.get(DnfPackages)

    if present:
        to_install = tuple(p for p in packages if p not in installed)
        if to_install:
            ctx.changed = True
            ctx.reason = f"{len(to_install)} package(s) to install"
            yield DnfInstallCommand(packages=to_install)
        else:
            ctx.reason = "all packages present"
    else:
        to_remove = tuple(p for p in packages if p in installed)
        if to_remove:
            ctx.changed = True
            ctx.reason = f"{len(to_remove)} package(s) to remove"
            yield DnfRemoveCommand(packages=to_remove)
        else:
            ctx.reason = "no packages to remove"


__all__ = ["DnfInstallCommand", "DnfRemoveCommand", "packages"]
