# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The ``files`` module: facts and operations in one source.
#
# Fact side (design doc section 5): ``FileLink`` (the fact read by
# ``files.link`` to learn the current target of a symlink), ``FileExists``
# (the fact read by ``files.file`` to learn whether a regular file exists),
# the ``LocalSha1``/``RemoteFileSha1`` pair ``files.put`` and ``files.get``
# compare to decide whether a transfer is needed, ``FileContents`` (the
# raw text of a remote file, or None when absent, that ``files.edit`` reads to
# transform it), and ``File`` (pyinfra's stat-level metadata for a regular
# file -- owner, group, mode, timestamps, size -- False for a non-file path,
# None when absent) with its ``Directory`` and ``Link`` siblings (the same
# metadata for a directory or a symlink, False for a non-directory/non-link
# path; ``Link``'s dict additionally carries the ``link_target``). Like every
# fact they are phase-unaware: each ``process`` coroutine just gathers the raw
# value.
#
# Operation side (design doc section 6): ``files.link``, ``files.file``,
# ``files.put``, ``files.get``, ``files.edit`` and ``files.move`` plus the typed
# command descriptors they yield
# (``LinkCommand``/``FileCommand``/``PutCommand``/``GetCommand``/
# ``MoveCommand``/``RemoveTreeCommand``). Operations
# are async generators that receive an ``OpContext``, await facts, set
# ``ctx.changed``/``ctx.reason``, and yield command descriptors; they never
# touch the host directly. ``PutCommand`` and ``GetCommand`` are the *non-shell*
# descriptors: they transfer bytes over the host's asyncssh connection (scp)
# instead of running shell text -- ``PutCommand`` uploads an IO-like source to a
# remote path, ``GetCommand`` downloads a remote path into an IO-like
# destination.
# """

import asyncio
import hashlib
import io
import posixpath
import re
import shlex
import stat
from collections.abc import AsyncGenerator, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import IO, Any, Literal

from ...commands import Command, TransferCommand
from ...core import OpContext
from ...hosts import Host

# --- Facts -------------------------------------------------------------------


class FileLink:
    """The target of a symlink, or None if the link does not exist.

    command:

    ```bash
    readlink <path>
    ```

    return:

    ```python
    str | None
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_link.py``.
    """

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
    """Whether a regular file exists at the path.

    command:

    ```bash
    test -e <path> && echo yes
    ```

    return:

    ```python
    bool
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_file.py``.
    """

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

    command:

    ```text
    none -- the digest is computed locally with hashlib.sha1, no host command is issued
    ```

    return:

    ```python
    str
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_put.py`` and
        ``examples/modules/files/files_get.py``.
    """

    # The one fact whose input is not the host: ``files.put`` compares this
    # against ``RemoteFileSha1`` to decide whether an upload is needed. The
    # digest is computed in a worker thread so the read never blocks the event
    # loop, and the stream is rewound first so repeated materializations (the
    # prepare and apply phases read the same object) hash identical content.

    @classmethod
    async def process(cls, host: Host, src: IO[Any]) -> str:
        return await asyncio.to_thread(_sha1_of_source, src)


class RemoteFileSha1:
    """The sha1 hex digest of a file on the remote host, or None if absent.

    command:

    ```bash
    sha1sum <path>
    ```

    return:

    ```python
    str | None
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_put.py`` and
        ``examples/modules/files/files_get.py``.
    """

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

    command:

    ```bash
    test -e <path> && { printf '#reemote5-filecontents\\n'; cat -- <path>; }
    ```

    return:

    ```python
    str | None
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_edit.py``.
    """

    # Mirrors pyinfra's ``files.FileContents`` (gathered with ``cat``), with one
    # deliberate deviation: reemote5 returns the content as a single ``str``
    # rather than pyinfra's list of lines, so a content transform (``files.edit``)
    # can treat the file as one blob and re-split it however it likes. Like
    # ``dnf.DnfRepoFile`` -- the same read-content-or-None shape -- absence is
    # reported as ``None``, never the empty string, so a file that exists but is
    # empty stays distinguishable from one that is missing.

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


#: The leading ``ls``/``stat`` mode character mapped to pyinfra's path type;
#: each fact class (``File``, ``Directory``, ``Link``) accepts only its own
#: ``TYPE``, every other type reads as False.
_FLAG_TO_TYPE = {
    "b": "block",
    "c": "character",
    "d": "directory",
    "l": "link",
    "s": "socket",
    "p": "fifo",
    "-": "file",
}

#: Per-position mode character -> permission bit (pyinfra's
#: ``CHAR_TO_PERMISSION``, taken from the stdlib ``stat`` table): ``s``/``S``
#: encode setuid/setgid combined with execute, ``t``/``T`` the sticky bit.
_CHAR_TO_PERMISSION = (
    {"r": stat.S_IRUSR},
    {"w": stat.S_IWUSR},
    {"x": stat.S_IXUSR, "S": stat.S_ISUID, "s": stat.S_IXUSR | stat.S_ISUID},
    {"r": stat.S_IRGRP},
    {"w": stat.S_IWGRP},
    {"x": stat.S_IXGRP, "S": stat.S_ISGID, "s": stat.S_IXGRP | stat.S_ISGID},
    {"r": stat.S_IROTH},
    {"w": stat.S_IWOTH},
    {"x": stat.S_IXOTH, "T": stat.S_ISVTX, "t": stat.S_IXOTH | stat.S_ISVTX},
)

#: The keyed ``stat`` format whose rows ``File.parse`` matches; the GNU
#: (``-c``) and BSD (``-f``) forms print the same ``user=... group=...``
#: shape, so one regex parses both.
_STAT_REGEX = (
    r"user=(.*) group=(.*) mode=(.*) "
    r"atime=(-?[0-9]*) mtime=(-?[0-9]*) ctime=(-?[0-9]*) "
    r"size=([0-9]*) (.*)"
)

#: ``ls -ld`` output: permissions links user group size month day year/time
#: path. The permissions may carry a trailing attribute marker (``.``
#: SELinux, ``@`` extended attrs, ``+`` ACL); the date supports both
#: "MMM DD" and "DD MMM" orders.
_LS_REGEX = (
    r"^([dlbcsp-][-rwxstST]{9}[.@+]?)\s+\d+\s+(\S+)\s+(\S+)\s+(\d+)"
    r"\s+(\S+)\s+(\S+)\s+(\S+)\s+(.+)$"
)

#: ``ls`` month abbreviations (pyinfra's explicit map -- locale-independent,
#: unlike ``strptime``'s ``%b``).
_LS_MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}

#: GNU ``stat -c`` format printing the keyed row ``File.parse`` matches
#: (pyinfra's ``LINUX_STAT_COMMAND`` format string).
_LINUX_STAT_FORMAT = "user=%U group=%G mode=%A atime=%X mtime=%Y ctime=%Z size=%s %N"

#: BSD ``stat -f`` format printing the same keyed row (pyinfra's
#: ``BSD_STAT_COMMAND`` format string).
_BSD_STAT_FORMAT = (
    "user=%Su group=%Sg mode=%Sp atime=%a mtime=%m ctime=%c size=%z %N%SY"
)


def _unquote_stat_name(name: str) -> str:
    """Strip the quoting ``stat %N`` (or ``ls -ld``) wraps a path in.

    pyinfra's ``_unquote_stat_name``: ``'name'`` (GNU coreutils), ```name'``
    (older GNU/RHEL 6), or ``"name"`` (uutils-coreutils).
    """
    for opener, closer in (("'", "'"), ("`", "'"), ('"', '"')):
        if name.startswith(opener) and name.endswith(closer) and len(name) >= 2:
            return name[len(opener) : -len(closer)]
    return name


def _try_int(value: str) -> int | str:
    """pyinfra's ``try_int``: the int a string denotes, else the string itself."""
    try:
        return int(value)
    except ValueError:
        return value


def _parse_mode(mode: str) -> int:
    """Convert a nine-character rwx mode string to pyinfra's octal int (755)."""
    out = 0
    for i, char in enumerate(mode):
        for c, m in _CHAR_TO_PERMISSION[i].items():
            if char == c:
                out |= m
                break
    return int(oct(out)[2:])


def _parse_epoch(value: str) -> datetime | None:
    """Parse epoch seconds into a *naive UTC* datetime (pyinfra's convention).

    A non-numeric value (an empty or unsupported timestamp field) reads as
    None rather than raising.
    """
    try:
        seconds = int(value)
    except ValueError:
        return None
    return datetime.fromtimestamp(seconds, UTC).replace(tzinfo=None)


def _parse_ls_time(month: str, day: str, year_or_time: str) -> datetime | None:
    """Parse ``ls``'s "MMM DD YYYY" / "MMM DD HH:MM" stamp (naive, as printed).

    ``ls`` shows a HH:MM time for recently modified files and a year for
    older ones; the time form assumes the current (UTC) year. An
    unparseable stamp reads as None rather than raising.
    """
    month_num = _LS_MONTHS.get(month)
    if month_num is None:
        return None
    try:
        day_num = int(day)
        if ":" in year_or_time:
            hour, minute = map(int, year_or_time.split(":"))
            return datetime(datetime.now(UTC).year, month_num, day_num, hour, minute)
        return datetime(int(year_or_time), month_num, day_num)
    except ValueError:
        return None


def _parse_ls_line(line: str) -> tuple[dict[str, Any], str] | None:
    """Parse one ``ls -ld`` row into (metadata, path type), or None if unmatched.

    ``ls`` provides no atime/ctime, so those read as None; the mode may
    carry a trailing ``.``/``@``/``+`` attribute marker, which is stripped.
    """
    match = re.match(_LS_REGEX, line.strip())
    if not match:
        return None
    permissions = match.group(1)
    # Some locales print "DD MMM" instead of "MMM DD".
    date_part1, date_part2 = match.group(5), match.group(6)
    day, month = (
        (date_part1, date_part2) if date_part1.isdigit() else (date_part2, date_part1)
    )
    data: dict[str, Any] = {
        "user": match.group(2),
        "group": match.group(3),
        "mode": _parse_mode(permissions[1:10]),
        "atime": None,  # ls doesn't provide atime
        "mtime": _parse_ls_time(month, day, match.group(7)),
        "ctime": None,  # ls doesn't provide ctime
        "size": _try_int(match.group(4)),
    }
    path_type = _FLAG_TO_TYPE[permissions[0]]
    # A symlink row names its target after " -> "; keep it (unquoted) so
    # the link-accepting fact can report where the link points.
    if path_type == "link" and " -> " in match.group(8):
        _, target = match.group(8).split(" -> ", 1)
        data["link_target"] = _unquote_stat_name(target)
    return data, path_type


class File:
    """Metadata for a regular file, False for a non-file path, None if absent.

    command:

    ```bash
    ! (test -e <path> || test -L <path> ) || ( stat -c 'user=%U group=%G mode=%A atime=%X mtime=%Y ctime=%Z size=%s %N' <path> 2> /dev/null || stat -f 'user=%Su group=%Sg mode=%Sp atime=%a mtime=%m ctime=%c size=%z %N%SY' <path> || ls -ld <path> )
    ```

    return:

    ```python
    dict[str, Any] | Literal[False] | None
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_file.py``.
    """

    # Mirrors pyinfra's ``files.File``: the path is guarded with
    # ``test -e``/``test -L`` (so a dangling symlink is still probed), then
    # described by GNU ``stat -c``, falling back to BSD ``stat -f`` and
    # finally ``ls -ld``. The parsed result is a dict, for example::
    #
    #     {"user": "pyinfra", "group": "pyinfra", "mode": 644, "size": 3928}
    #
    # plus ``atime``/``mtime``/``ctime`` -- epoch timestamps parsed into
    # *naive UTC* ``datetime``s, pyinfra's convention (the ``ls -ld`` fallback
    # provides only an mtime, so its atime/ctime read as None). ``mode`` is
    # the octal-looking int pyinfra returns (644, 4755, ...), including the
    # setuid/setgid/sticky bits. A path that does not exist (no output) reads
    # as ``None``; one that exists but is not a regular file (a directory,
    # symlink, socket, ...) reads as ``False`` -- ``Directory`` and ``Link``
    # are the directory- and link-accepting siblings, and only ``Link``'s
    # dict carries the ``link_target`` key. Like pyinfra,
    # a leading ``~/`` stays unquoted so the remote shell expands it; the
    # rest of the path is quoted.

    #: pyinfra's ``type`` field: only a regular file is accepted; every other
    #: path type (directory, link, socket, ...) reads as False.
    TYPE = "file"

    @classmethod
    def command(cls, path: str) -> str:
        """Shell printing the stat (or ls) description of ``path``, if it exists.

        The ``test -e || test -L`` guard means a missing path produces no
        output at all; ``test -L`` additionally lets a dangling symlink
        through to ``ls -ld`` (both ``stat`` forms follow the link and
        fail), which reports it as a non-file.
        """
        # A leading ~/ must stay unquoted so the shell expands it (pyinfra's
        # tilde branch); everything after it -- or the whole path -- is quoted.
        if path.startswith("~/"):
            quoted = "~/" + shlex.quote(path[2:])
        else:
            quoted = shlex.quote(path)
        return (
            f"! (test -e {quoted} || test -L {quoted} ) || "
            f"( stat -c '{_LINUX_STAT_FORMAT}' {quoted} 2> /dev/null || "
            f"stat -f '{_BSD_STAT_FORMAT}' {quoted} || ls -ld {quoted} )"
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, Any] | Literal[False] | None:
        """Parse the stat (or ``ls -ld``) row into metadata, False or None.

        The first line is tried against the keyed stat format, then the
        ``ls -ld`` format; a path type other than ``TYPE`` reads as False,
        and output that matches neither (or no output at all) reads as None.
        A symlink row accepted by ``TYPE = "link"`` additionally carries the
        unquoted ``link_target``.
        """
        line = next(iter(output), None)
        if line is None:
            return None
        match = re.match(_STAT_REGEX, line)
        if match:
            mode = match.group(3)
            data: dict[str, Any] = {
                "user": match.group(1),
                "group": match.group(2),
                "mode": _parse_mode(mode[1:10]),
                "atime": _parse_epoch(match.group(4)),
                "mtime": _parse_epoch(match.group(5)),
                "ctime": _parse_epoch(match.group(6)),
                "size": _try_int(match.group(7)),
            }
            path_type = _FLAG_TO_TYPE[mode[0]]
            if path_type != cls.TYPE:
                return False
            if path_type == "link":
                # stat %N prints 'link' -> 'target'; keep the target,
                # unquoted (pyinfra's link_target extraction).
                _, target = match.group(8).split(" -> ", 1)
                data["link_target"] = _unquote_stat_name(target)
            return data
        ls_result = _parse_ls_line(line)
        if ls_result is not None:
            data, path_type = ls_result
            return data if path_type == cls.TYPE else False
        return None

    @classmethod
    async def process(
        cls, host: Host, path: str
    ) -> dict[str, Any] | Literal[False] | None:
        stats = getattr(host, "file_stats", None)
        if stats is not None:
            # SimulatedHost seam: metadata keyed by the caller-supplied path
            # string (any ~/ prefix included, since the shell resolves it).
            # Return a copy so callers never mutate simulated state; the
            # False (non-file) and None (absent) answers pass through.
            value = stats.get(path)
            return dict(value) if isinstance(value, dict) else value
        # Real transport: stat/ls over asyncssh. A missing path produces
        # empty stdout (the guard short-circuits), which parses to None.
        stdout = await host.run_shell(cls.command(path))
        return cls.parse(stdout.splitlines())


class Directory(File):
    """Metadata for a directory, False for a non-directory path, None if absent.

    command:

    ```bash
    ! (test -e <path> || test -L <path> ) || ( stat -c 'user=%U group=%G mode=%A atime=%X mtime=%Y ctime=%Z size=%s %N' <path> 2> /dev/null || stat -f 'user=%Su group=%Sg mode=%Sp atime=%a mtime=%m ctime=%c size=%z %N%SY' <path> || ls -ld <path> )
    ```

    return:

    ```python
    dict[str, Any] | Literal[False] | None
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_directory.py``.
    """

    # Mirrors pyinfra's ``files.Directory`` -- its ``File`` subclass with
    # ``type = "directory"``: the same ``test -e``/``test -L`` guard and
    # stat/ls chain (``command`` and ``parse`` are inherited), but the type
    # flag must be ``d``, so a regular file, symlink, socket, ... reads as
    # ``False`` instead. The dict shape, *naive UTC* timestamps and
    # octal-looking ``mode`` int are exactly ``File``'s, and a leading ``~/``
    # likewise stays unquoted for the remote shell to expand.

    #: pyinfra's ``type`` field: only a directory is accepted; every other
    #: path type (file, link, socket, ...) reads as False.
    TYPE = "directory"

    @classmethod
    async def process(
        cls, host: Host, path: str
    ) -> dict[str, Any] | Literal[False] | None:
        stats = getattr(host, "directory_stats", None)
        if stats is not None:
            # SimulatedHost seam: the file_stats shape in a separate store,
            # because a metadata dict carries no type flag -- file_stats
            # alone cannot say whether its path is a directory. Keys are
            # the caller-supplied path strings verbatim (any ~/ prefix
            # included); a dict is a directory's metadata, False an
            # existing non-directory, an absent key None. Return a copy so
            # callers never mutate simulated state.
            value = stats.get(path)
            return dict(value) if isinstance(value, dict) else value
        # Real transport: the inherited stat/ls chain over asyncssh, parsed
        # against TYPE = "directory". A missing path produces empty stdout
        # (the guard short-circuits), which parses to None.
        stdout = await host.run_shell(cls.command(path))
        return cls.parse(stdout.splitlines())


class Link(File):
    """Metadata for a symlink plus its target, False for non-link, None if absent.

    command:

    ```bash
    ! (test -e <path> || test -L <path> ) || ( stat -c 'user=%U group=%G mode=%A atime=%X mtime=%Y ctime=%Z size=%s %N' <path> 2> /dev/null || stat -f 'user=%Su group=%Sg mode=%Sp atime=%a mtime=%m ctime=%c size=%z %N%SY' <path> || ls -ld <path> )
    ```

    return:

    ```python
    dict[str, Any] | Literal[False] | None
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_link.py``.
    """

    # Mirrors pyinfra's ``files.Link`` -- its ``File`` subclass with
    # ``type = "link"``: the same ``test -e``/``test -L`` guard and stat/ls
    # chain (``command`` and ``parse`` are inherited), but the type flag must
    # be ``l``, so a regular file, directory, socket, ... reads as ``False``
    # instead. The dict is ``File``'s shape plus a ``link_target`` key -- the
    # target path with the stat/ls quoting stripped, exactly as the link
    # records it (a relative target stays relative), for example::
    #
    #     {"user": "pyinfra", "group": "pyinfra", "link_target": "/path/to/link/target"}
    #
    # The ``test -L`` guard lets a *dangling* symlink through to the
    # ``ls -ld`` fallback (both ``stat`` forms follow the link and fail), so
    # a broken link is still reported with its target. Where ``FileLink`` is
    # the cheap ``readlink`` probe ``files.link`` consumes, ``Link`` is the
    # richer stat-level read for auditing the link itself -- ownership, mode,
    # timestamps -- alongside where it points.

    #: pyinfra's ``type`` field: only a symlink is accepted; every other
    #: path type (file, directory, socket, ...) reads as False.
    TYPE = "link"

    @classmethod
    async def process(
        cls, host: Host, path: str
    ) -> dict[str, Any] | Literal[False] | None:
        stats = getattr(host, "link_stats", None)
        if stats is not None:
            # SimulatedHost seam: the file_stats shape (plus link_target) in
            # a separate store, because a metadata dict carries no type flag
            # -- file_stats alone cannot say whether its path is a symlink.
            # Keys are the caller-supplied path strings verbatim (any ~/
            # prefix included); a dict is a symlink's metadata, False an
            # existing non-link, an absent key None. Return a copy so
            # callers never mutate simulated state.
            value = stats.get(path)
            return dict(value) if isinstance(value, dict) else value
        # Real transport: the inherited stat/ls chain over asyncssh, parsed
        # against TYPE = "link". A missing path produces empty stdout (the
        # guard short-circuits), which parses to None.
        stdout = await host.run_shell(cls.command(path))
        return cls.parse(stdout.splitlines())


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
    first and move it with ``files.move`` flagged ``sudo=True`` when it must
    end up root-owned).
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
    transformed into a writable path and moved into place with ``files.move``
    flagged ``sudo=True``, since a transfer cannot be escalated.
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


@dataclass(frozen=True)
class MoveCommand(Command):
    """Move a remote path into a remote directory (``mv src dest``)."""

    src: str
    dest: str

    def shell(self) -> str:
        return f"mv {shlex.quote(self.src)} {shlex.quote(self.dest)}"


@dataclass(frozen=True)
class RemoveTreeCommand(Command):
    """Recursively remove a remote path (``rm -rf src``).

    The overwrite branch of ``files.move``: unlike ``FileCommand``'s
    ``rm -f`` (a regular-file probe), the move target may be a directory,
    so the removal must be recursive.
    """

    src: str

    def shell(self) -> str:
        return f"rm -rf {shlex.quote(self.src)}"


async def move(
    ctx: OpContext,
    src: str,
    dest: str,
    overwrite: bool = False,
    **kwargs,
) -> AsyncGenerator[Command]:
    """Move a remote file/directory/link into a remote directory
    (pyinfra ``files.move`` parity).

    + src: the remote path to move.
    + dest: the remote *directory* to move ``src`` into; the moved path
      becomes ``dest/basename(src)``.
    + overwrite: when the destination path already exists, remove it
      first (``rm -rf``) instead of raising.

    Stateless like pyinfra: there is no no-op branch -- a valid move
    always yields a ``MoveCommand`` (preceded by a ``RemoveTreeCommand``
    when ``overwrite`` clears an existing target), so ``ctx.changed`` is
    always True. Repeating the same move fails at apply when ``mv`` exits
    non-zero on the now-absent source (``CommandError``, fail-fast).

    Reads ``File(src)`` (a directory or symlink reads False, which pyinfra
    accepts -- only absence is rejected), ``Directory(dest)`` and
    ``File(dest/basename(src))``. An absent ``src``, a ``dest`` that is not
    a directory or does not exist, and an existing target without
    ``overwrite`` all raise ``ValueError``. pyinfra's phase-aware handling
    of the two absence cases (a soft log while planning, an error only
    while executing) is deliberately dropped because reemote5 operations
    are phase-unaware: they raise in *both* phases. A consequence is that
    a "create a file then move it" flow cannot share one deploy -- the
    prepare phase would raise on the not-yet-created source -- so it spans
    two runs (see the ``files.edit`` + ``files.move`` ``/etc/hosts``
    recipe example).

    Both commands are shell text, so flagging the request ``sudo=True``
    elevates the whole move -- the vehicle for installing a login-user
    spool file into a root-owned directory such as ``/etc``. ``**kwargs``
    absorbs pyinfra's global arguments (``op()`` already strips
    ``name``/``sudo``/``su``).
    """
    if await ctx.facts.get(File, src) is None:
        raise ValueError(f"src {src} does not exist")

    dest_info = await ctx.facts.get(Directory, dest)
    if dest_info is False:
        raise ValueError(f"dest {dest} is not a directory")
    if dest_info is None:
        raise ValueError(f"dest directory {dest} does not exist")

    full_dest = posixpath.join(dest, PurePosixPath(src).name)
    reasons: list[str] = []
    if await ctx.facts.get(File, full_dest) is not None:
        if not overwrite:
            raise ValueError(
                f"dest {full_dest} already exists and `overwrite` is unset"
            )
        reasons.append(f"{full_dest} present, will remove first")
        yield RemoveTreeCommand(src=full_dest)

    reasons.append(f"will move {src} into {dest}")
    ctx.changed = True
    ctx.reason = "; ".join(reasons)
    yield MoveCommand(src=src, dest=dest)


__all__ = [
    "Directory",
    "File",
    "FileCommand",
    "FileContents",
    "FileExists",
    "FileLink",
    "GetCommand",
    "Link",
    "LinkCommand",
    "LocalSha1",
    "MoveCommand",
    "PutCommand",
    "RemoteFileSha1",
    "RemoveTreeCommand",
    "edit",
    "file",
    "get",
    "link",
    "move",
    "put",
    "read_source_bytes",
    "write_dest_bytes",
]
