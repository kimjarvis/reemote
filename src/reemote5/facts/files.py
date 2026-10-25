"""The ``files`` fact namespace (design doc section 5).

Exposes ``FileLink`` (the fact read by ``files.link`` to learn the current
target of a symlink) and ``FileExists`` (the fact read by ``files.file`` to
learn whether a regular file exists). Like every fact they are phase-unaware:
each ``process`` coroutine just gathers the raw value.
"""

import shlex

from ..hosts import Host


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


__all__ = ["FileExists", "FileLink"]
