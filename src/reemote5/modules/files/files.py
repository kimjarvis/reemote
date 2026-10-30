"""The ``files`` module: facts and operations in one source.

Fact side (design doc section 5): ``FileLink`` (the fact read by
``files.link`` to learn the current target of a symlink), ``FileExists``
(the fact read by ``files.file`` to learn whether a regular file exists),
the ``LocalSha1``/``RemoteFileSha1`` pair ``files.put`` and ``files.get``
compare to decide whether a transfer is needed, and ``FileContents`` (the
raw text of a remote file, or None when absent, that ``files.edit`` reads to
transform it). Like every fact they are phase-unaware: each ``process``
coroutine just gathers the raw value.

Operation side (design doc section 6): ``files.link``, ``files.file``,
``files.put``, ``files.get`` and ``files.edit`` plus the typed command
descriptors they yield
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
import io
import shlex
from collections.abc import AsyncGenerator, Callable
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


class FileContents:
    """The text content of a file on the remote host, or None when absent.

    Mirrors pyinfra's ``files.FileContents`` (gathered with ``cat``), with one
    deliberate deviation: reemote5 returns the content as a single ``str``
    rather than pyinfra's list of lines, so a content transform (``files.edit``)
    can treat the file as one blob and re-split it however it likes. Like
    ``dnf.DnfRepoFile`` -- the same read-content-or-None shape -- absence is
    reported as ``None``, never the empty string, so a file that exists but is
    empty stays distinguishable from one that is missing.
    """

    #: Marker printed before the ``cat``'d content, only when ``test -e``
    #: passes, so ``process`` can tell an existing (possibly empty) file from
    #: a missing one (no marker on stdout).
    MARKER = "#reemote5-filecontents"

    @classmethod
    def command(cls, path: str) -> str:
        """Shell printing ``MARKER`` then the file's content, only if it exists.

        The ``test -e`` guard means a missing file produces no output at all
        (not even the marker), while an existing one -- empty or not -- always
        leads with the marker line.
        """
        quoted = shlex.quote(path)
        return f"test -e {quoted} && {{ printf '{cls.MARKER}\\n'; cat -- {quoted}; }}"

    @classmethod
    async def process(cls, host: Host, path: str) -> str | None:
        remote_files = getattr(host, "remote_files", None)
        if remote_files is not None:
            # SimulatedHost seam: decode the content the simulated remote
            # filesystem holds at the path (None when absent). The same store
            # backs files.put/files.get and RemoteFileSha1, so a simulated
            # upload -- which stores its bytes here -- reads back through this
            # fact, and an ``edit`` sees the content a prior ``edit`` wrote.
            data = remote_files.get(path)
            return data.decode("utf-8") if data is not None else None
        # Real transport: cat the file over asyncssh behind the marker, so a
        # missing file (no marker) reads as None rather than empty content.
        stdout = await host.run_shell(cls.command(path))
        prefix = f"{cls.MARKER}\n"
        if not stdout.startswith(prefix):
            return None
        return stdout[len(prefix) :]


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


async def edit(
    ctx: OpContext, path: str, transform: Callable[[str | None], str]
) -> AsyncGenerator[Command]:
    """Ensure the remote file ``path`` matches ``transform`` of its current content.

    The generic content-editing primitive: instead of a bespoke operation per
    config-file format (pyinfra's ``server.etc_hosts``, ``files.line``,
    ``files.replace`` and friends each hard-code one shape of edit), this reads
    the file's current text with the ``FileContents`` fact, applies a
    caller-supplied *pure* ``transform``, and writes the result back with a
    ``PutCommand`` only when it differs. Any "edit a file in place" task --
    adding an ``/etc/hosts`` entry, setting a ``sysctl`` key, appending a
    managed block -- is then just a transform, with no new operation needed.

    + path: the remote file to edit.
    + transform: a pure callable taking the current content (``str``, or
      ``None`` when the file is absent) and returning the desired content as a
      ``str``. It must be *idempotent* -- ``transform(transform(x)) ==
      transform(x)`` -- so a second run is a no-op, and side-effect free, since
      it runs once per phase (prepare, then each apply step).

    Idempotence is content-equality based: the transform's result is compared
    against the current content, and a ``PutCommand`` is yielded only when they
    differ, so a repeat edit whose transform is a fixpoint reports no change.
    An absent file always counts as a change (``transform(None)`` produces the
    content to create it from).

    ``transform`` must be a *stable, hashable* object -- a module-level
    function, not a lambda or closure built inside the deploy generator. It is
    stored in the ``OperationRequest`` kwargs, which the determinism guard
    compares by value across phases; a fresh callable per phase would diverge
    and raise ``DeterminismError``, exactly as a fresh IO stream would for
    ``files.put``/``files.get``.

    The write rides ``files.put``'s unprivileged scp transfer, so ``sudo``/``su``
    do not apply to it and ``path`` must be writable by the login user. A
    root-owned target (``/etc/hosts``, a file under ``/etc``) must instead be
    transformed into a writable path and moved into place with an elevated
    shell operation, since a transfer cannot be escalated.
    """
    current = await ctx.facts.get(FileContents, path)
    new = transform(current)

    if new == current:
        ctx.reason = f"content already up to date ({path})"
    else:
        ctx.changed = True
        ctx.reason = (
            f"content {path} differs from the transform, will rewrite"
            if current is not None
            else f"content {path} is absent, will create from the transform"
        )
        yield PutCommand(src=io.StringIO(new), dest=path)


__all__ = [
    "FileCommand",
    "FileContents",
    "FileExists",
    "FileLink",
    "GetCommand",
    "LinkCommand",
    "LocalSha1",
    "PutCommand",
    "RemoteFileSha1",
    "edit",
    "file",
    "get",
    "link",
    "put",
    "read_source_bytes",
    "write_dest_bytes",
]
