"""The ``files`` operation namespace (design doc section 6).

Exposes ``files.link``. Operations are async generators that receive an
``OpContext``, await facts, set ``ctx.changed``/``ctx.reason``, and yield
command descriptors; they never touch the host directly.
"""

from collections.abc import AsyncGenerator

from ..commands import Command, LinkCommand
from ..core import OpContext
from ..facts import FileLink


async def link(
    ctx: OpContext, src: str, target: str | None = None, present: bool = True
) -> AsyncGenerator[Command]:
    current = await ctx.facts.get(FileLink, src)

    if present:
        if current == target:
            ctx.reason = f"link already correct ({src} -> {target})"
        else:
            ctx.changed = True
            ctx.reason = f"link {src} -> {target} missing or incorrect"
            yield LinkCommand(src=src, target=target)
    else:
        if current is None:
            ctx.reason = "link already absent"
        else:
            ctx.changed = True
            ctx.reason = "link present, will remove"
            yield LinkCommand(src=src, target=None)


__all__ = ["link"]
