"""The ``files`` module: facts and operations in one source.

Fact side (design doc section 5): ``FileLink`` (the fact read by
``files.link`` to learn the current target of a symlink), ``FileExists``
(the fact read by ``files.file`` to learn whether a regular file exists),
and the ``LocalSha1``/``RemoteFileSha1`` pair ``files.put`` and ``files.get``
compare to decide whether a transfer is needed. Like every fact they are
phase-unaware: each ``process`` coroutine just gathers the raw value.

Operation side (design doc section 6): ``files.link``, ``files.file``,
``files.put`` and ``files.get`` plus the typed command descriptors they yield
(``LinkCommand``/``FileCommand``/``PutCommand``/``GetCommand``). Operations
are async generators that receive an ``OpContext``, await facts, set
``ctx.changed``/``ctx.reason``, and yield command descriptors; they never
touch the host directly. ``PutCommand`` and ``GetCommand`` are the *non-shell*
descriptors: they transfer bytes over the host's asyncssh connection (scp)
instead of running shell text -- ``PutCommand`` uploads an IO-like source to a
remote path, ``GetCommand`` downloads a remote path into an IO-like
destination.
"""

import asyncio
import hashlib
import shlex
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import IO, Any

from ...commands import Command, TransferCommand
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


def read_source_bytes(src: IO[Any]) -> bytes:
    """Read an IO-like source from position 0 and normalize it to bytes.

    The single reading rule for ``files.put`` sources, shared by the local
    hash fact and the transport's temp-file materialization so both always
    see identical content: the stream is rewound first (an operation may be
    materialized more than once -- prepare, then apply -- and each read must
    start from the beginning), and a text stream's ``str`` payload is encoded
    as UTF-8. The source must therefore be seekable; a missing local file is
    impossible by construction (there is no path), and a non-seekable stream
    fails fast with the IO error ``seek`` raises.
    """
    src.seek(0)
    data = src.read()
    return data.encode("utf-8") if isinstance(data, str) else data


def _sha1_of_source(src: IO[Any]) -> str:
    """The sha1 hex digest of an IO-like source (blocking; run in a thread)."""
    return hashlib.sha1(read_source_bytes(src)).hexdigest()


def write_dest_bytes(dest: IO[Any], data: bytes) -> None:
    """Write ``data`` into an IO-like destination, replacing its content.

    The inverse of ``read_source_bytes`` and the single writing rule for
    ``files.get`` destinations, shared by the transport's download and the
    test framework's simulated get so a download always stores exactly the
    bytes the remote sha1 describes. The stream is rewound and truncated first
    (so re-getting into a populated stream replaces rather than appends), the
    bytes are written directly to a binary stream or decoded as UTF-8 for a
    text stream, and the stream is rewound again so the caller reads the fresh
    content from position 0. The destination must therefore be seekable and
    writable.
    """
    dest.seek(0)
    dest.truncate()
    try:
        dest.write(data)
    except TypeError:
        # A text stream (io.StringIO, a file opened "w") rejects bytes; mirror
        # read_source_bytes' UTF-8 rule in reverse.
        dest.write(data.decode("utf-8"))
    dest.seek(0)


class LocalSha1:
    """The sha1 hex digest of an IO-like source on the *control* machine.

    The one fact whose input is not the host: ``files.put`` compares this
    against ``RemoteFileSha1`` to decide whether an upload is needed. The
    digest is computed in a worker thread so the read never blocks the event
    loop, and the stream is rewound first so repeated materializations (the
    prepare and apply phases read the same object) hash identical content.
    """

    @classmethod
    async def process(cls, host: Host, src: IO[Any]) -> str:
        return await asyncio.to_thread(_sha1_of_source, src)


class RemoteFileSha1:
    """The sha1 hex digest of a file on the remote host, or None if absent."""

    @classmethod
    async def process(cls, host: Host, path: str) -> str | None:
        remote_files = getattr(host, "remote_files", None)
        if remote_files is not None:
            # SimulatedHost seam: hash the content the simulated remote
            # filesystem holds at the path (None when absent). One content
            # store backs both files.put and files.get, so the digest is
            # derived here exactly as LocalSha1 derives it from a stream.
            data = remote_files.get(path)
            return hashlib.sha1(data).hexdigest() if data is not None else None
        # Real transport: `sha1sum` over asyncssh. A missing file exits
        # non-zero with empty stdout (check is off in run_shell), which maps
        # to None just like the simulated absent-file case.
        stdout = await host.run_shell(f"sha1sum {shlex.quote(path)}")
        fields = stdout.split()
        return fields[0] if fields else None


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


@dataclass(frozen=True)
class PutCommand(TransferCommand):
    """Upload an IO-like source to a remote path over the host's connection.

    The first non-shell descriptor: it has no ``shell()`` -- the copy rides
    the asyncssh connection's scp protocol via ``Host.upload_file``, not a
    shell command -- so its loggable form is the ``__str__`` rendering
    ``command_shell`` falls back to. ``src`` is the IO-like object itself
    (never a path): ``OperationRequest`` equality compares it by identity, so
    a deploy must pass the *same* stream object in every phase -- define it
    once (e.g. at module level), not inside the generator.
    """

    src: IO[Any]  # IO-like source on the control machine
    dest: str  # remote path on the host

    def __str__(self) -> str:
        return f"scp {getattr(self.src, 'name', '<stream>')} -> {self.dest}"

    async def transfer(self, host: Host) -> None:
        await host.upload_file(self.src, self.dest)


async def put(ctx: OpContext, src: IO[Any], dest: str) -> AsyncGenerator[Command]:
    """Ensure the remote ``dest`` matches the IO-like source ``src``, uploading
    if not.

    ``src`` is a seekable IO-like object on the control machine (a file
    opened with ``open()``, an ``io.BytesIO``/``io.StringIO``, ...); unlike
    pyinfra's ``files.put``, which accepts ``str | IO[Any]``, a *path string
    is deliberately not supported* -- the content source is always a stream,
    rewound to position 0 before every read. Idempotence is content-based:
    the source's sha1 (``LocalSha1``) is compared against the remote file's
    (``RemoteFileSha1``, gathered over SSH), and a ``PutCommand`` is yielded
    only when they differ.

    The transfer is unprivileged: it rides the connection's scp channel as
    the login user, so the ``sudo``/``su`` escalation flags do not apply to
    it and ``dest`` must be writable by that user (put into a writable path
    first and move it with an elevated shell operation when it must end up
    root-owned).
    """
    local_sha1 = await ctx.facts.get(LocalSha1, src)
    remote_sha1 = await ctx.facts.get(RemoteFileSha1, dest)

    if remote_sha1 == local_sha1:
        ctx.reason = f"file already up to date ({dest})"
    else:
        ctx.changed = True
        ctx.reason = f"file {dest} missing or differs from the local source"
        yield PutCommand(src=src, dest=dest)


@dataclass(frozen=True)
class GetCommand(TransferCommand):
    """Download a remote path into an IO-like destination over the connection.

    The mirror of ``PutCommand`` and the second non-shell descriptor: it has
    no ``shell()`` -- the copy rides the asyncssh connection's scp protocol
    via ``Host.download_file``, not a shell command -- so its loggable form is
    the ``__str__`` rendering ``command_shell`` falls back to. ``src`` is the
    remote path; ``dest`` is the IO-like object itself (never a path):
    ``OperationRequest`` equality compares it by identity, so a deploy must
    pass the *same* stream object in every phase -- define it once (e.g. at
    module level), not inside the generator.
    """

    src: str  # remote path on the host
    dest: IO[Any]  # IO-like destination on the control machine

    def __str__(self) -> str:
        return f"scp {self.src} -> {getattr(self.dest, 'name', '<stream>')}"

    async def transfer(self, host: Host) -> None:
        await host.download_file(self.src, self.dest)


async def get(ctx: OpContext, src: str, dest: IO[Any]) -> AsyncGenerator[Command]:
    """Ensure the IO-like destination ``dest`` holds the remote file ``src``.

    The mirror of ``files.put``: ``src`` is the remote path to download and
    ``dest`` is a seekable, writable IO-like object on the control machine (an
    ``io.BytesIO``/``io.StringIO``, a file opened for writing, ...) that
    receives the content -- *not* a local filename. Idempotence is
    content-based: the remote file's sha1 (``RemoteFileSha1``, gathered over
    SSH) is compared against the destination's current content sha1
    (``LocalSha1``), and a ``GetCommand`` is yielded only when they differ, so
    a fresh or empty destination always downloads and a second get of the same
    file is a no-op. pyinfra's ``add_deploy_dir``, ``create_local_dir`` and
    ``force`` options are deliberately dropped: there is no local path to
    place, create or force. Like pyinfra, an absent remote source still plans
    a download (which fails at scp if the file truly is not there).

    The transfer is unprivileged: it rides the connection's scp channel as the
    login user, so the ``sudo``/``su`` escalation flags do not apply to it and
    ``src`` must be readable by that user.
    """
    remote_sha1 = await ctx.facts.get(RemoteFileSha1, src)
    local_sha1 = await ctx.facts.get(LocalSha1, dest)

    if remote_sha1 == local_sha1:
        ctx.reason = f"local copy already up to date (from {src})"
    else:
        ctx.changed = True
        ctx.reason = f"local copy missing or differs from remote {src}"
        yield GetCommand(src=src, dest=dest)


__all__ = [
    "FileCommand",
    "FileExists",
    "FileLink",
    "GetCommand",
    "LinkCommand",
    "LocalSha1",
    "PutCommand",
    "RemoteFileSha1",
    "file",
    "get",
    "link",
    "put",
    "read_source_bytes",
    "write_dest_bytes",
]
