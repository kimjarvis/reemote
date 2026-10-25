"""Operation namespaces (design doc section 6).

Operations are grouped into namespace modules mirroring pyinfra's API so a
deploy reads ``dnf.packages`` / ``files.link`` rather than ``dnf_packages``
/ ``files_link``.

Each operation is an async generator with the signature::

    async def operation(
        ctx: OpContext, *args, **kwargs
    ) -> AsyncGenerator[Command, None]

An operation:

- Awaits facts via ``await ctx.facts.get(...)``.
- Sets ``ctx.changed = True`` and ``ctx.reason = "..."`` at the point
  where it decides that a change is needed.
- Yields command descriptors for each command that will actually mutate
  the host.
- Never executes anything; never touches the host directly.
"""

from . import dnf, files

__all__ = ["dnf", "files"]
