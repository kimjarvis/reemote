"""The ``files`` module: facts and operations in one source.

Fact side (design doc section 5): ``FileLink`` (the fact read by
``files.link`` to learn the current target of a symlink) and ``FileExists``
(the fact read by ``files.file`` to learn whether a regular file exists).
Like every fact they are phase-unaware: each ``process`` coroutine just
gathers the raw value.

Operation side (design doc section 6): ``files.link`` and ``files.file``
plus the typed command descriptors they yield (``LinkCommand``/
``FileCommand``). Operations are async generators that receive an
``OpContext``, await facts, set ``ctx.changed``/``ctx.reason``, and yield
command descriptors; they never touch the host directly.
"""

import shlex
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from ...commands import Command
from ...core import OpContext
from ...hosts import Host

# --- Facts -------------------------------------------------------------------


class FileLink:
    """The target of a symlink, or None if the link does not exist."""

    @classmethod
    async def process(cls, host: Host, path: str) -> str | None:
        links = getattr(host, "links", None)
        if links is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return links.get(path)
        # Real transport: `readlink` over asyncssh. A missing link exits
        # non-zero with empty stdout (check is off in run_shell), which maps
        # to None just like the simulated absent-link case.
        stdout = await host.run_shell(f"readlink {shlex.quote(path)}")
        target = stdout.strip()
        return target or None


class FileExists:
    """Whether a regular file exists at the path."""

    @classmethod
    async def process(cls, host: Host, path: str) -> bool:
        existing = getattr(host, "existing_files", None)
        if existing is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return path in existing
        # Real transport: `test -e` over asyncssh. A missing file exits
        # non-zero with empty stdout (check is off in run_shell), which maps
        # to False just like the simulated absent-file case.
        stdout = await host.run_shell(f"test -e {shlex.quote(path)} && echo yes")
        return stdout.strip() == "yes"


# --- Operations --------------------------------------------------------------


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


__all__ = ["FileCommand", "FileExists", "FileLink", "LinkCommand", "file", "link"]
