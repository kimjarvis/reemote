"""The ``apt`` operation namespace (design doc section 6).

Exposes ``apt.packages``. Operations are async generators that receive an
``OpContext``, await facts, set ``ctx.changed``/``ctx.reason``, and yield
command descriptors; they never touch the host directly.
"""

from collections.abc import AsyncGenerator

from ..commands import AptInstallCommand, AptRemoveCommand, Command
from ..core import OpContext
from ..facts import AptPackages


async def packages(
    ctx: OpContext, packages: tuple[str, ...], present: bool = True
) -> AsyncGenerator[Command]:
    installed = await ctx.facts.get(AptPackages)

    if present:
        to_install = tuple(p for p in packages if p not in installed)
        if to_install:
            ctx.changed = True
            ctx.reason = f"{len(to_install)} package(s) to install"
            yield AptInstallCommand(packages=to_install)
        else:
            ctx.reason = "all packages present"
    else:
        to_remove = tuple(p for p in packages if p in installed)
        if to_remove:
            ctx.changed = True
            ctx.reason = f"{len(to_remove)} package(s) to remove"
            yield AptRemoveCommand(packages=to_remove)
        else:
            ctx.reason = "no packages to remove"


__all__ = ["packages"]
