"""Operation namespaces (design doc section 6).

Operations are grouped into namespace modules mirroring pyinfra's API so a
deploy reads ``apt.packages`` / ``files.link`` rather than ``apt_packages``
/ ``files_link``.

Each operation is a generator with the signature::

    def operation(ctx: OpContext, *args, **kwargs) -> Generator[Command, None, None]

An operation:

- Reads facts via ``ctx.facts.get(...)``.
- Sets ``ctx.changed = True`` and ``ctx.reason = "..."`` at the point
  where it decides that a change is needed.
- Yields command descriptors for each command that will actually mutate
  the host.
- Never executes anything; never touches the host directly.
"""

from . import apt, files

__all__ = ["apt", "files"]
