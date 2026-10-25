"""The ``files`` operation namespace (design doc section 6).

Exposes ``files.link`` and ``files.file`` plus the typed command
descriptors they yield (``LinkCommand``/``FileCommand``). Operations are
async generators that receive an ``OpContext``, await facts, set
``ctx.changed``/``ctx.reason``, and yield command descriptors; they never
touch the host directly.
"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass

from ..commands import Command
from ..core import OpContext
from ..facts.files import FileExists, FileLink


@dataclass(frozen=True)
class LinkCommand(Command):
    src: str
    target: str | None = None  # None means "remove the link at src"

    def shell(self) -> str:
        if self.target is None:
            return f"rm -f {self.src}"
        return f"ln -sf {self.target} {self.src}"


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


@dataclass(frozen=True)
class FileCommand(Command):
    src: str
    present: bool = True

    def shell(self) -> str:
        if not self.present:
            return f"rm -f {self.src}"
        return f"touch {self.src}"


async def file(
    ctx: OpContext, src: str, present: bool = True
) -> AsyncGenerator[Command]:
    exists = await ctx.facts.get(FileExists, src)

    if present:
        if exists:
            ctx.reason = f"file already present ({src})"
        else:
            ctx.changed = True
            ctx.reason = f"file {src} missing"
            yield FileCommand(src=src, present=True)
    else:
        if not exists:
            ctx.reason = "file already absent"
        else:
            ctx.changed = True
            ctx.reason = "file present, will remove"
            yield FileCommand(src=src, present=False)


__all__ = ["FileCommand", "LinkCommand", "file", "link"]
