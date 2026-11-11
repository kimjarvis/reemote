# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The ``files`` module: facts and operations in one source.
#
# Fact side (design doc section 5): ``FileLink`` (the cheap ``readlink``
# probe for the current target of a symlink), ``FileExists``
# (the fact read by ``files.file`` to learn whether a regular file exists),
# ``RemoteFileSha1`` (the remote digest ``files.put`` and ``files.get``
# compare against a locally computed sha1 to decide whether a transfer is
# needed), ``FileContents`` (the
# raw text of a remote file, or None when absent, that ``files.edit`` reads to
# transform it), ``FileFlags`` (the list of ext2-family attribute letters
# ``lsattr -d`` reports for a path, the read behind ``files.flags``), and
# ``File`` (the baseline's stat-level metadata for a regular
# file -- owner, group, mode, timestamps, size -- False for a non-file path,
# None when absent) with its ``Directory``, ``Link`` and ``Socket`` siblings
# (the same metadata for a directory, symlink or socket, False for a
# wrong-typed path; ``Link``'s dict additionally carries the ``link_target``,
# and is the fact ``files.link`` compares against) and ``SameFile`` (whether
# two paths name one and the same inode, the hard-link half of ``files.link``'s
# idempotence). The enumeration side is ``FindFiles``/``FindDirectories``/
# ``FindLinks`` (the baseline's find family -- the paths under a start path, the
# read that discovers names the deploy author does not know), ``FindInFile``
# (grep's matching lines, [] for no match vs None for absent), and the
# originals ``FindFilesInfo`` (find -printf metadata per hit) and
# ``DirectoryEntries`` (a shallow name/type listing). The digest side adds
# ``Sha256File``/``Sha384File``/``Md5File`` beside ``RemoteFileSha1``. Like
# every fact they are phase-unaware: each ``process`` coroutine just gathers
# the raw value.
#
# Operation side (design doc section 6): ``files.link``, ``files.file``,
# ``files.put``, ``files.get``, ``files.edit``, ``files.install``,
# ``files.move``, ``files.copy``,
# ``files.directory`` and ``files.flags`` plus the typed command descriptors
# they yield (``LinkCommand``/``FileCommand``/``PutCommand``/``GetCommand``/
# ``InstallSpoolCommand``/``InstallCommand``/
# ``MoveCommand``/``RemoveTreeCommand``/``CopyCommand``/``MkdirCommand``/
# ``ChmodCommand``/``ChownCommand``/``ChattrCommand``). Operations
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
import os
import posixpath
import re
import shlex
import stat
from collections.abc import AsyncGenerator, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import IO, Any, Literal, TypedDict

from ...commands import Command, TransferCommand
from ...core import OpContext
from ...hosts import Host

# --- Facts -------------------------------------------------------------------


class FileLink:
    """The target of a symlink, or None if the link does not exist.

    **Commands:**

    ```bash
    readlink <path>
    ```

    **Returns:**

    ```python
    str | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
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

    **Commands:**

    ```bash
    test -e <path> && echo yes
    ```

    **Returns:**

    ```python
    bool
    ```

    **Raises:**

    ```python
    HostConnectionError
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
    hash computation and the transport's temp-file materialization so both
    always see identical content: the stream is rewound first (an operation
    may be materialized more than once -- prepare, then apply -- and each read
    must start from the beginning), and a text stream's ``str`` payload is
    encoded as UTF-8. The source must therefore be seekable; a missing local
    file is impossible by construction (there is no path), and a non-seekable
    stream fails fast with the IO error ``seek`` raises.
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


class RemoteFileSha1:
    """The sha1 hex digest of a file on the remote host, or None if absent.

    **Commands:**

    ```bash
    sha1sum <path>
    ```

    **Returns:**

    ```python
    str | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
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
            # derived here exactly as _sha1_of_source derives it from a stream.
            data = remote_files.get(path)
            return hashlib.sha1(data).hexdigest() if data is not None else None
        # Real transport: `sha1sum` over asyncssh. A missing file exits
        # non-zero with empty stdout (check is off in run_shell), which maps
        # to None just like the simulated absent-file case.
        stdout = await host.run_shell(f"sha1sum {shlex.quote(path)}")
        fields = stdout.split()
        return fields[0] if fields else None


class _HashFileBase:
    """Shared command/parse/process for the baseline hash-fact family."""

    # Mirrors the baseline's ``HashFileFactBase``: the file is read through stdin
    # (``< path``) so the checksum tool never sees the filename, avoiding
    # GNU coreutils' backslash/newline escaping of names in its output (the
    # upstream #1911 fix). The ``test -e`` guard plus the trailing
    # ``|| true`` mean a missing file -- or a host with none of the tool
    # spellings installed -- exits zero with no stdout, which parses to
    # None; there is no missing-binary case to report as
    # ``CommandNotFoundError``. Two output spellings are accepted: the GNU
    # coreutils / shasum stdin form ``<hash>  -`` and the BSD digest form
    # (a bare ``<hash>``). Unlike the ``File`` family there is no leading
    # ``~/`` exception -- the whole path is quoted, the baseline's behaviour.
    # ``RemoteFileSha1`` predates this family and deliberately keeps its
    # simpler ``sha1sum <path>`` probe: it backs ``files.put``/``files.get``
    # idempotence, where the argv form and the 127-as-CommandNotFoundError
    # surface are already locked in by the transfer operations.

    #: Hex digits the digest must carry (40 for sha1, 64 for sha256, ...).
    DIGITS: int
    #: The tool spellings tried in order (GNU sum, shasum, BSD digest).
    CMDS: tuple[str, ...]
    #: The ``hashlib`` algorithm name the simulated seam derives with.
    ALGO: str

    @classmethod
    def command(cls, path: str) -> str:
        """Shell printing the digest of ``path`` via stdin, if it exists."""
        quoted = shlex.quote(path)
        chain = " || ".join(f"{cmd} < {quoted} 2> /dev/null" for cmd in cls.CMDS)
        return f"test -e {quoted} && ( {chain} ) || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> str | None:
        """Parse the first line into the hex digest, or None if unmatched.

        No output (absent file, no tool installed) reads as None, as does
        a line carrying neither the ``<hash>  -`` nor the bare-hash form
        with the exact expected digit count.
        """
        line = next(iter(output), None)
        if line is None:
            return None
        for regex in (
            rf"^([a-fA-F0-9]{{{cls.DIGITS}}})\s+-\s*$",
            rf"^([a-fA-F0-9]{{{cls.DIGITS}}})$",
        ):
            match = re.match(regex, line)
            if match:
                return match.group(1)
        return None

    @classmethod
    async def process(cls, host: Host, path: str) -> str | None:
        remote_files = getattr(host, "remote_files", None)
        if remote_files is not None:
            # SimulatedHost seam: the command addresses a caller-supplied
            # literal path, so the shared content store answers at zero
            # wiring cost (the RemoteFileSha1 precedent) -- the digest is
            # derived from the stored bytes exactly as the real tool would
            # read them, None when the path is absent.
            data = remote_files.get(path)
            return hashlib.new(cls.ALGO, data).hexdigest() if data is not None else None
        # Real transport: the guarded stdin hash chain over asyncssh.
        stdout = await host.run_shell(cls.command(path))
        return cls.parse(stdout.splitlines())


class Sha256File(_HashFileBase):
    """The sha256 hex digest of a file on the remote host, or None if absent.

    **Commands:**

    ```bash
    test -e <path> && ( sha256sum < <path> 2> /dev/null || shasum -a 256 < <path> 2> /dev/null || sha256 < <path> 2> /dev/null ) || true
    ```

    **Returns:**

    ```python
    str | None
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_sha256.py``.
    """

    # Mirrors the baseline's ``files.Sha256File`` -- the hash family's SHA-256
    # member, the digest Ansible's ``stat`` module defaults to
    # (``checksum_algorithm: sha256``) and the one repository/keyring
    # verification recipes compare. Command/parse/process are the shared
    # base's; only the digit count, tool spellings and seam algorithm differ.

    DIGITS = 64
    CMDS = ("sha256sum", "shasum -a 256", "sha256")
    ALGO = "sha256"


class Sha384File(_HashFileBase):
    """The sha384 hex digest of a file on the remote host, or None if absent.

    **Commands:**

    ```bash
    test -e <path> && ( sha384sum < <path> 2> /dev/null || shasum -a 384 < <path> 2> /dev/null || sha384 < <path> 2> /dev/null ) || true
    ```

    **Returns:**

    ```python
    str | None
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_sha384.py``.
    """

    # Mirrors the baseline's ``files.Sha384File`` -- the hash family's SHA-384
    # member; the shared base carries the mechanics.

    DIGITS = 96
    CMDS = ("sha384sum", "shasum -a 384", "sha384")
    ALGO = "sha384"


class Md5File(_HashFileBase):
    """The md5 hex digest of a file on the remote host, or None if absent.

    **Commands:**

    ```bash
    test -e <path> && ( md5sum < <path> 2> /dev/null || md5 < <path> 2> /dev/null ) || true
    ```

    **Returns:**

    ```python
    str | None
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_md5.py``.
    """

    # Mirrors the baseline's ``files.Md5File`` -- the hash family's MD5 member
    # (two tool spellings, no shasum variant); the shared base carries the
    # mechanics. MD5 is for legacy checksum manifests (Repomd/Release
    # files, vendor download pages), not a security boundary.

    DIGITS = 32
    CMDS = ("md5sum", "md5")
    ALGO = "md5"


class FileContents:
    """The text content of a file on the remote host, or None when absent.

    **Commands:**

    ```bash
    test -e <path> && { printf '#reemote-filecontents\\n'; cat -- <path>; }
    ```

    **Returns:**

    ```python
    str | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_edit.py``.
    """

    # Mirrors the baseline's ``files.FileContents`` (gathered with ``cat``), with one
    # deliberate deviation: reemote returns the content as a single ``str``
    # rather than the baseline's list of lines, so a content transform (``files.edit``)
    # can treat the file as one blob and re-split it however it likes. Like
    # ``dnf.DnfRepoFile`` -- the same read-content-or-None shape -- absence is
    # reported as ``None``, never the empty string, so a file that exists but is
    # empty stays distinguishable from one that is missing.

    #: Marker printed before the ``cat``'d content, only when ``test -e``
    #: passes, so ``process`` can tell an existing (possibly empty) file from
    #: a missing one (no marker on stdout).
    MARKER = "#reemote-filecontents"

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


#: The leading ``ls``/``stat`` mode character mapped to the baseline's path type;
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

#: Per-position mode character -> permission bit (the baseline's
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

#: ``ls`` month abbreviations (the baseline's explicit map -- locale-independent,
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
#: (the baseline's ``LINUX_STAT_COMMAND`` format string).
_LINUX_STAT_FORMAT = "user=%U group=%G mode=%A atime=%X mtime=%Y ctime=%Z size=%s %N"

#: BSD ``stat -f`` format printing the same keyed row (the baseline's
#: ``BSD_STAT_COMMAND`` format string).
_BSD_STAT_FORMAT = (
    "user=%Su group=%Sg mode=%Sp atime=%a mtime=%m ctime=%c size=%z %N%SY"
)


def _unquote_stat_name(name: str) -> str:
    """Strip the quoting ``stat %N`` (or ``ls -ld``) wraps a path in.

    The baseline's ``_unquote_stat_name``: ``'name'`` (GNU coreutils), ```name'``
    (older GNU/RHEL 6), or ``"name"`` (uutils-coreutils).
    """
    for opener, closer in (("'", "'"), ("`", "'"), ('"', '"')):
        if name.startswith(opener) and name.endswith(closer) and len(name) >= 2:
            return name[len(opener) : -len(closer)]
    return name


def _try_int(value: str) -> int | str:
    """The baseline's ``try_int``: the int a string denotes, else the string itself."""
    try:
        return int(value)
    except ValueError:
        return value


def _parse_mode(mode: str) -> int:
    """Convert a nine-character rwx mode string to the baseline's octal int (755)."""
    out = 0
    for i, char in enumerate(mode):
        for c, m in _CHAR_TO_PERMISSION[i].items():
            if char == c:
                out |= m
                break
    return int(oct(out)[2:])


def _parse_epoch(value: str) -> datetime | None:
    """Parse epoch seconds into a *naive UTC* datetime (the baseline's convention).

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

    **Commands:**

    ```bash
    ! (test -e <path> || test -L <path> ) || ( stat -c 'user=%U group=%G mode=%A atime=%X mtime=%Y ctime=%Z size=%s %N' <path> 2> /dev/null || stat -f 'user=%Su group=%Sg mode=%Sp atime=%a mtime=%m ctime=%c size=%z %N%SY' <path> || ls -ld <path> )
    ```

    **Returns:**

    ```python
    dict[str, Any] | Literal[False] | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_file.py``.
    """

    # Mirrors the baseline's ``files.File``: the path is guarded with
    # ``test -e``/``test -L`` (so a dangling symlink is still probed), then
    # described by GNU ``stat -c``, falling back to BSD ``stat -f`` and
    # finally ``ls -ld``. The parsed result is a dict, for example::
    #
    #     {"user": "reemote", "group": "reemote", "mode": 644, "size": 3928}
    #
    # plus ``atime``/``mtime``/``ctime`` -- epoch timestamps parsed into
    # *naive UTC* ``datetime``s, the baseline's convention (the ``ls -ld`` fallback
    # provides only an mtime, so its atime/ctime read as None). ``mode`` is
    # the octal-looking int the baseline returns (644, 4755, ...), including the
    # setuid/setgid/sticky bits. A path that does not exist (no output) reads
    # as ``None``; one that exists but is not a regular file (a directory,
    # symlink, socket, ...) reads as ``False`` -- ``Directory``, ``Link`` and
    # ``Socket`` are the directory-, link- and socket-accepting siblings,
    # and only ``Link``'s dict carries the ``link_target`` key. Like the baseline,
    # a leading ``~/`` stays unquoted so the remote shell expands it; the
    # rest of the path is quoted.

    #: The baseline's ``type`` field: only a regular file is accepted; every other
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
        # A leading ~/ must stay unquoted so the shell expands it (the baseline's
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
                # unquoted (the baseline's link_target extraction).
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

    **Commands:**

    ```bash
    ! (test -e <path> || test -L <path> ) || ( stat -c 'user=%U group=%G mode=%A atime=%X mtime=%Y ctime=%Z size=%s %N' <path> 2> /dev/null || stat -f 'user=%Su group=%Sg mode=%Sp atime=%a mtime=%m ctime=%c size=%z %N%SY' <path> || ls -ld <path> )
    ```

    **Returns:**

    ```python
    dict[str, Any] | Literal[False] | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_directory.py``.
    """

    # Mirrors the baseline's ``files.Directory`` -- its ``File`` subclass with
    # ``type = "directory"``: the same ``test -e``/``test -L`` guard and
    # stat/ls chain (``command`` and ``parse`` are inherited), but the type
    # flag must be ``d``, so a regular file, symlink, socket, ... reads as
    # ``False`` instead. The dict shape, *naive UTC* timestamps and
    # octal-looking ``mode`` int are exactly ``File``'s, and a leading ``~/``
    # likewise stays unquoted for the remote shell to expand. This fact
    # describes the directory *itself*, never the names inside it -- for
    # the listing see ``DirectoryEntries`` (shallow, with types) or
    # ``FindDirectories`` (recursive paths).

    #: The baseline's ``type`` field: only a directory is accepted; every other
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

    **Commands:**

    ```bash
    ! (test -e <path> || test -L <path> ) || ( stat -c 'user=%U group=%G mode=%A atime=%X mtime=%Y ctime=%Z size=%s %N' <path> 2> /dev/null || stat -f 'user=%Su group=%Sg mode=%Sp atime=%a mtime=%m ctime=%c size=%z %N%SY' <path> || ls -ld <path> )
    ```

    **Returns:**

    ```python
    dict[str, Any] | Literal[False] | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_link.py``.
    """

    # Mirrors the baseline's ``files.Link`` -- its ``File`` subclass with
    # ``type = "link"``: the same ``test -e``/``test -L`` guard and stat/ls
    # chain (``command`` and ``parse`` are inherited), but the type flag must
    # be ``l``, so a regular file, directory, socket, ... reads as ``False``
    # instead. The dict is ``File``'s shape plus a ``link_target`` key -- the
    # target path with the stat/ls quoting stripped, exactly as the link
    # records it (a relative target stays relative), for example::
    #
    #     {"user": "reemote", "group": "reemote", "link_target": "/path/to/link/target"}
    #
    # The ``test -L`` guard lets a *dangling* symlink through to the
    # ``ls -ld`` fallback (both ``stat`` forms follow the link and fail), so
    # a broken link is still reported with its target. ``files.link``
    # consumes this stat-level read for its idempotence (the target alone is
    # not enough to tell "correct" from "wrong target" from "not a link");
    # ``FileLink`` is the cheaper
    # ``readlink`` probe for the callers that only want the target.

    #: The baseline's ``type`` field: only a symlink is accepted; every other
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


class Socket(File):
    """Metadata for a socket, False for a non-socket path, None if absent.

    **Commands:**

    ```bash
    ! (test -e <path> || test -L <path> ) || ( stat -c 'user=%U group=%G mode=%A atime=%X mtime=%Y ctime=%Z size=%s %N' <path> 2> /dev/null || stat -f 'user=%Su group=%Sg mode=%Sp atime=%a mtime=%m ctime=%c size=%z %N%SY' <path> || ls -ld <path> )
    ```

    **Returns:**

    ```python
    dict[str, Any] | Literal[False] | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_socket.py``.
    """

    # Mirrors the baseline's ``files.Socket`` -- its ``File`` subclass with
    # ``type = "socket"``: pure inheritance, the same ``test -e``/``test -L``
    # guard and stat/ls chain (``command`` and ``parse`` are inherited
    # unchanged -- the shared parse already maps the ``s`` type flag), so a
    # regular file, directory, symlink, ... reads as ``False`` instead. The
    # dict shape, *naive UTC* timestamps and octal-looking ``mode`` int are
    # exactly ``File``'s, and a leading ``~/`` likewise stays unquoted for
    # the remote shell to expand. The one override is ``process``, reading
    # the dedicated ``socket_stats`` seam: ``file_stats`` stores
    # undiscriminated metadata payloads and can never answer the
    # socket-typed question for the same path (the Directory/Link
    # precedent). Use it to audit a daemon's listening socket --
    # ``/var/run/docker.sock``, a database or agent IPC socket -- owner and
    # mode decide which users may talk to the daemon.

    #: The baseline's ``type`` field: only a socket is accepted; every other
    #: path type (file, directory, link, ...) reads as False.
    TYPE = "socket"

    @classmethod
    async def process(
        cls, host: Host, path: str
    ) -> dict[str, Any] | Literal[False] | None:
        stats = getattr(host, "socket_stats", None)
        if stats is not None:
            # SimulatedHost seam: the file_stats shape in a separate store,
            # because a metadata dict carries no type flag -- file_stats
            # alone cannot say whether its path is a socket. Keys are the
            # caller-supplied path strings verbatim (any ~/ prefix
            # included); a dict is a socket's metadata, False an existing
            # non-socket, an absent key None. Return a copy so callers
            # never mutate simulated state.
            value = stats.get(path)
            return dict(value) if isinstance(value, dict) else value
        # Real transport: the inherited stat/ls chain over asyncssh, parsed
        # against TYPE = "socket". A missing path produces empty stdout
        # (the guard short-circuits), which parses to None.
        stdout = await host.run_shell(cls.command(path))
        return cls.parse(stdout.splitlines())


class SameFile:
    """Whether two paths name one and the same file (a hard link pair).

    **Commands:**

    ```bash
    test <path> -ef <target> && echo true || echo false
    ```

    **Returns:**

    ```python
    bool
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_same_file.py``.
    """

    # Mirrors the baseline's ``files.SameFile``: the shell ``-ef`` operator reduced
    # to the bool the fact returns. ``test`` ``stat``s the *resolved* paths
    # (symlinks are dereferenced), so the verdict is about the inodes the two
    # arguments resolve to: a hard link answers True for the file it is
    # another name of, a symlink answers True for its own target, and
    # anything resolving to a different inode -- an unrelated file, two
    # different targets -- or a missing path answers False.
    # ``test`` is a shell builtin and the ``|| echo false`` arm always runs,
    # so the command exits zero whatever it compares: a nonexistent path is
    # the plain False answer rather than a transport error, and there is no
    # missing-binary case to report as ``CommandNotFoundError``. Both paths
    # are quoted (the baseline's ``QuoteString`` behaviour, which ``shlex.quote``
    # matches for a safe path); unlike the ``File`` family this fact has no
    # leading ``~/`` exception, because the operator compares inodes and a
    # quoted tilde path simply reads as an absent one -- the baseline's own
    # fixture command, ``test /foo/hardlink -ef /foo/file``, stays bare.

    @classmethod
    def command(cls, path: str, target: str) -> str:
        """Shell testing whether ``path`` and ``target`` are one file."""
        return (
            f"test {shlex.quote(path)} -ef {shlex.quote(target)} "
            "&& echo true || echo false"
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> bool:
        """Parse the ``echo``ed verdict; anything but a lone ``true`` is False.

        The baseline's ``process`` compares the whole output list, so empty (a
        short-circuited or failed read) or unexpected output reports False.
        """
        return list(output) == ["true"]

    @classmethod
    async def process(cls, host: Host, path: str, target: str) -> bool:
        pairs = getattr(host, "same_files", None)
        if pairs is not None:
            # SimulatedHost seam: the set of path pairs recorded as sharing
            # one inode (``ln`` without ``-s`` adds the pair, and a test can
            # seed it directly). The operator is symmetric, so the pair is
            # looked up in either order. The seam models literally recorded
            # pairs only -- no symlink resolution -- which is sound for the
            # only consumer, ``files.link``'s hard-link branch, that is
            # reached just after ``Link`` answered the path is not a symlink.
            return (path, target) in pairs or (target, path) in pairs
        # Real transport: the ``test -ef`` shell over asyncssh.
        stdout = await host.run_shell(cls.command(path, target))
        return cls.parse(stdout.splitlines())


class FileFlags:
    """The ext2-family attribute flags set on a path, empty or absent alike.

    **Commands:**

    ```bash
    ! test -e <path> || lsattr -d <path> 2> /dev/null
    ```

    **Returns:**

    ```python
    list[str]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_flags.py``.
    """

    # Replaces the baseline's ``files.Flags``, which read BSD ``stat -f %Sf``
    # (comma-joined flag *names*) behind a ``requires_command("chflags")``
    # gate: the Linux reading of the same capability (the developer-chosen
    # adaptation) is ``lsattr -d``, whose flags field is a fixed-width string
    # of one-letter attribute codes -- the fact returns those letters (``i``
    # for immutable, ``a`` for append-only, ...) with the padding dashes and
    # the trailing path dropped. Absence answers ``[]`` exactly as the baseline's
    # Flags process does: the ``! test -e`` guard short-circuits a missing
    # path to empty output (no exit status reaches the transport, so the
    # 127-as-CommandNotFoundError rule stays truthful -- ``lsattr`` is not
    # even run), and a filesystem that does not support the attributes exits
    # non-zero with only stderr noise, which ``2> /dev/null`` masks into the
    # same empty answer rather than aborting the read. A *present* file on
    # such a filesystem therefore also reads ``[]`` and the operation plans a
    # change the ``chattr`` then fails on -- the documented consequence of
    # the stand-in gate the baseline expresses through ``requires_command``. The
    # command guards existence but not a missing binary: no ``lsattr``
    # installed raises ``CommandNotFoundError``. Like ``File.command``, a
    # leading ``~/`` stays unquoted so the remote shell expands it; the rest
    # of the path is quoted.

    @classmethod
    def command(cls, path: str) -> str:
        """Shell printing the ``lsattr -d`` row for ``path``, if it exists."""
        if path.startswith("~/"):
            quoted = "~/" + shlex.quote(path[2:])
        else:
            quoted = shlex.quote(path)
        return f"! test -e {quoted} || lsattr -d {quoted} 2> /dev/null"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[str]:
        """Parse the ``lsattr -d`` row into its sorted one-letter flags.

        The whitespace-separated fields are ``<flags> <path>``; the flags
        field keeps only the attribute letters, dropping the ``-`` padding.
        No output (absent path, unsupported filesystem) or a row without a
        flags field parses to ``[]``, the baseline's Flags answer for "nothing
        set".
        """
        line = next(iter(output), "")
        fields = line.split()
        if not fields:
            return []
        return sorted(char for char in fields[0] if char != "-")

    @classmethod
    async def process(cls, host: Host, path: str) -> list[str]:
        file_flags = getattr(host, "file_flags", None)
        if file_flags is not None:
            # SimulatedHost seam: the set of letters seeded or mutated per
            # path, as a sorted list so callers never see set iteration order
            # and never mutate simulated state.
            return sorted(file_flags.get(path, set()))
        # Real transport: lsattr over asyncssh. A missing path short-circuits
        # the guard to empty stdout, which parses to [] like the baseline's.
        stdout = await host.run_shell(cls.command(path))
        return cls.parse(stdout.splitlines())


#: Byte multipliers for the find family's human-readable size filters
#: (the baseline's ``facts.util.units`` table: decimal KB/MB/GB/TB, binary
#: KiB/MiB/GiB/TiB).
_SIZE_UNITS = {
    "B": 1,
    "KB": 10**3,
    "MB": 10**6,
    "GB": 10**9,
    "TB": 10**12,
    "KIB": 2**10,
    "MIB": 2**20,
    "GIB": 2**30,
    "TIB": 2**40,
}


def _parse_size(size: str | int) -> int:
    """Bytes denoted by an int or a human-readable size string (the baseline's
    ``parse_size``): ``"1GB"`` is 1e9 bytes, ``"1GiB"`` is 2**30. An
    unrecognised spelling raises ``ValueError`` before anything reaches the
    host.
    """
    if isinstance(size, int):
        return size
    match = re.match(r"^\s*([0-9]*\.?[0-9]+)\s*([KMGT]?I?B)\s*$", size.upper())
    if not match:
        raise ValueError(f"invalid size {size!r}: expected bytes or a KB/MiB/... spelling")
    return int(float(match.group(1)) * _SIZE_UNITS[match.group(2)])


def _find_filter_args(
    *,
    size: str | int | None = None,
    min_size: str | int | None = None,
    max_size: str | int | None = None,
    maxdepth: int | None = None,
    fname: str | None = None,
    iname: str | None = None,
    regex: str | None = None,
    path_match: str | None = None,
    args: list[str] | None = None,
    quote_path: bool = True,
) -> list[str]:
    """The filter arguments of the baseline's ``FindFilesBase.command``, in its
    order: size bounds, maxdepth, name/iname globs, regex, path glob, then
    the raw ``args`` escape hatch. Each typed filter is skipped when ``args``
    already spells the same find predicate (the baseline's guards). Sizes always
    render in bytes (the ``c`` suffix) -- any other unit has
    implementation-specific 512-byte-block rounding.
    """
    extra = list(args) if args is not None else []

    def maybe_quote(value: str) -> str:
        return shlex.quote(value) if quote_path else value

    parts: list[str] = []
    if "-size" not in extra:
        if min_size is not None:
            parts += ["-size", f"+{_parse_size(min_size)}c"]
        if max_size is not None:
            parts += ["-size", f"-{_parse_size(max_size)}c"]
        if size is not None:
            # Upstream renders the exact size raw (``{size}c``); parsing it
            # through _parse_size is the deliberate fix so a human-readable
            # exact size works like its bounds do.
            parts += ["-size", f"{_parse_size(size)}c"]
    if maxdepth is not None and "-maxdepth" not in extra:
        parts += ["-maxdepth", str(maxdepth)]
    if fname is not None and "-fname" not in extra:
        parts += ["-name", maybe_quote(fname)]
    if iname is not None and "-iname" not in extra:
        parts += ["-iname", maybe_quote(iname)]
    if regex is not None and "-regex" not in extra:
        parts += ["-regex", maybe_quote(regex)]
    if path_match is not None and "-path" not in extra:
        # Always quoted, even under quote_path=False: find itself does the
        # globbing, the shell must pass the pattern literally (upstream).
        parts += ["-path", shlex.quote(path_match)]
    parts += extra
    return parts


class _FindPathsBase:
    """Shared command/parse/process for the baseline's find-path fact family."""

    # Mirrors the baseline's ``FindFilesBase``: ``find <path> -type <flag>`` plus
    # the typed filters, always ending ``|| true`` so a missing start path
    # (or an unreadable subtree) reads as the empty list rather than a
    # failed read -- the baseline's ``default = list`` behaviour, and the reason
    # there is no missing-binary ``CommandNotFoundError`` case. The bare
    # ``list[str]`` return is an agreed exception to the TypedDict-or-list-
    # of-TypedDicts guideline: a path string carries all the information.
    # The SimulatedHost seam is keyed by the *rendered command string*
    # (build the key with ``<Fact>.command(...)``): the answer depends on
    # every filter, so a path-only key could silently answer a different
    # question than the one asked. An unseeded command reads [].

    #: The ``-type`` flag selecting which paths the find reports.
    TYPE_FLAG: str

    @classmethod
    def command(
        cls,
        path: str,
        *,
        size: str | int | None = None,
        min_size: str | int | None = None,
        max_size: str | int | None = None,
        maxdepth: int | None = None,
        fname: str | None = None,
        iname: str | None = None,
        regex: str | None = None,
        path_match: str | None = None,
        args: list[str] | None = None,
        quote_path: bool = True,
    ) -> str:
        """Shell listing the matching paths, guarded with ``|| true``.

        ``quote_path=False`` passes the start path and name patterns to the
        shell unquoted (the baseline's escape hatch for shell-expanded globs);
        the default quotes everything.
        """
        quoted = shlex.quote(path) if quote_path else path
        parts = ["find", quoted, "-type", cls.TYPE_FLAG]
        parts += _find_filter_args(
            size=size,
            min_size=min_size,
            max_size=max_size,
            maxdepth=maxdepth,
            fname=fname,
            iname=iname,
            regex=regex,
            path_match=path_match,
            args=args,
            quote_path=quote_path,
        )
        return " ".join(parts) + " || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[str]:
        """The find's stdout lines verbatim, in find's traversal order."""
        return list(output)

    @classmethod
    async def process(
        cls,
        host: Host,
        path: str,
        *,
        size: str | int | None = None,
        min_size: str | int | None = None,
        max_size: str | int | None = None,
        maxdepth: int | None = None,
        fname: str | None = None,
        iname: str | None = None,
        regex: str | None = None,
        path_match: str | None = None,
        args: list[str] | None = None,
        quote_path: bool = True,
    ) -> list[str]:
        command = cls.command(
            path,
            size=size,
            min_size=min_size,
            max_size=max_size,
            maxdepth=maxdepth,
            fname=fname,
            iname=iname,
            regex=regex,
            path_match=path_match,
            args=args,
            quote_path=quote_path,
        )
        results = getattr(host, "find_results", None)
        if results is not None:
            # SimulatedHost seam: the rendered command is the key, so every
            # filter combination answers independently; copy the list so
            # callers never mutate simulated state.
            return list(results.get(command, []))
        # Real transport: the guarded find over asyncssh.
        stdout = await host.run_shell(command)
        return cls.parse(stdout.splitlines())


class FindFiles(_FindPathsBase):
    """Paths of the regular files under a start path, recursively via find.

    **Commands:**

    ```bash
    find <path> -type f [filters] || true
    ```

    **Returns:**

    ```python
    list[str]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_find_files.py``.
    """

    # Mirrors the baseline's ``files.FindFiles`` -- the enumeration fact the
    # stat-only ``File``/``Directory`` metadata facts cannot replace: it
    # discovers names the deploy author does not know ("every
    # conf.d/*/conf.yaml", "every legacy .list file"), the read side of a
    # gather-then-branch two-deploy cleanup. Filters are the shared base's
    # (fname/iname globs, maxdepth, size bounds, regex, path_match, raw
    # args). For per-hit metadata (size/mode/owner/mtime) in the same read,
    # see ``FindFilesInfo``; for a shallow one-level listing with types,
    # see ``DirectoryEntries``.

    TYPE_FLAG = "f"


class FindDirectories(_FindPathsBase):
    """Paths of the directories under a start path, recursively via find.

    **Commands:**

    ```bash
    find <path> -type d [filters] || true
    ```

    **Returns:**

    ```python
    list[str]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_find_directories.py``.
    """

    # Mirrors the baseline's ``files.FindDirectories`` -- the shared base with
    # ``-type d``. The start path itself is always reported when it exists
    # (find matches it too); ``maxdepth=1`` lists its immediate
    # subdirectories, the enumeration behind "which check directories
    # exist under conf.d".

    TYPE_FLAG = "d"


class FindLinks(_FindPathsBase):
    """Paths of the symlinks under a start path, recursively via find.

    **Commands:**

    ```bash
    find <path> -type l [filters] || true
    ```

    **Returns:**

    ```python
    list[str]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_find_links.py``.
    """

    # Mirrors the baseline's ``files.FindLinks`` -- the shared base with
    # ``-type l``. Dangling symlinks are reported too (find does not follow
    # links); read each hit's target with ``FileLink`` or ``Link``.

    TYPE_FLAG = "l"


class FindInFile:
    """Lines of a file matching a grep pattern; [] if none, None if absent.

    **Commands:**

    ```bash
    grep -e <pattern> <path> 2> /dev/null || ( find <path> -type f > /dev/null && echo __reemote_exists_<path> || true )
    ```

    **Returns:**

    ```python
    list[str] | None
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_find_in_file.py``.
    """

    # Mirrors the baseline's ``files.FindInFile``: grep prints the matching
    # lines; when it fails (no match *or* no file), the fallback arm
    # distinguishes the two -- an existing regular file echoes the
    # ``__reemote_exists_<path>`` sentinel (kept verbatim, prefix included,
    # for fixture parity), which parses to [], while a missing file echoes
    # nothing and reads as None. ``extended_regex`` selects ``grep -E``;
    # ``interpolate_variables`` wraps the pattern in double quotes (with
    # embedded double quotes escaped) so the remote shell expands ``$VAR``
    # references, otherwise the pattern is single-quoted literally. Every
    # arm exits zero, so a missing grep/find binary is masked too. The
    # SimulatedHost seam is the dedicated ``grep_lines`` store keyed by
    # ``(path, pattern)``: grep's BRE semantics cannot be faithfully
    # re-derived from the ``remote_files`` content bytes with Python's
    # ``re``, so a test seeds the matching lines directly ([] = the file
    # exists but nothing matches, an absent key = the file is missing).

    #: Sentinel prefix the fallback arm echoes for an existing file with
    #: no matches (the baseline's spelling, kept for fixture parity).
    EXISTS_PREFIX = "__reemote_exists_"

    @classmethod
    def command(
        cls,
        path: str,
        pattern: str,
        *,
        interpolate_variables: bool = False,
        extended_regex: bool = False,
    ) -> str:
        """Shell printing the matching lines, or the exists sentinel."""
        if interpolate_variables:
            pattern_arg = '"{}"'.format(pattern.replace('"', '\\"'))
        else:
            pattern_arg = shlex.quote(pattern)
        quoted = shlex.quote(path)
        flag = shlex.quote(cls.EXISTS_PREFIX + path)
        grep = "grep -E" if extended_regex else "grep"
        return (
            f"{grep} -e {pattern_arg} {quoted} 2> /dev/null || "
            f"( find {quoted} -type f > /dev/null && echo {flag} || true )"
        )

    @classmethod
    def parse(cls, output: Iterable[str], path: str) -> list[str] | None:
        """Parse grep's lines into matches, [] (sentinel) or None (absent).

        The sentinel is recognised only as the *first* line, exactly the
        fallback arm's shape; any other output is grep's matching lines.
        """
        lines = list(output)
        if not lines:
            return None
        if lines[0] == cls.EXISTS_PREFIX + path:
            return []
        return lines

    @classmethod
    async def process(
        cls,
        host: Host,
        path: str,
        pattern: str,
        *,
        interpolate_variables: bool = False,
        extended_regex: bool = False,
    ) -> list[str] | None:
        matches = getattr(host, "grep_lines", None)
        if matches is not None:
            # SimulatedHost seam: (path, pattern) -> the matching lines a
            # test seeds; copy so callers never mutate simulated state.
            value = matches.get((path, pattern))
            return list(value) if value is not None else None
        # Real transport: the grep-plus-sentinel shell over asyncssh.
        stdout = await host.run_shell(
            cls.command(
                path,
                pattern,
                interpolate_variables=interpolate_variables,
                extended_regex=extended_regex,
            )
        )
        return cls.parse(stdout.splitlines(), path)


class FoundFile(TypedDict):
    """
    Attributes:
        path: The file's path exactly as find printed it (the start path's
            spelling carried through, relative if the start was relative).
        size: The file's size in bytes.
        mode: The octal-looking permission int (644, 4755, ...), including
            the setuid/setgid/sticky bits -- the same convention as File.
        user: The owning user's name (find prints the numeric uid when the
            name cannot be resolved).
        group: The owning group's name (numeric gid when unresolvable).
        mtime: The last-modification time as a *naive UTC* datetime at
            whole-second precision (the File convention), or None when
            find printed no parseable timestamp.
    """

    path: str
    size: int
    mode: int
    user: str
    group: str
    mtime: datetime | None


class FindFilesInfo:
    """Regular files under a start path with per-hit metadata, via find -printf.

    **Commands:**

    ```bash
    find <path> -type f [filters] -printf '%p\t%s\t%m\t%u\t%g\t%T@\n' || true
    ```

    **Returns:**

    ```python
    list[FoundFile]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_find_files_info.py``.
    """

    # A reemote ORIGINAL: the baseline ships no counterpart -- its FindFiles
    # returns bare paths, so filtering hits by size/mtime/owner (Ansible's
    # ``find`` module returns that metadata per hit, and roles branch on
    # it: "delete logs older than X") would need one stat-level File read
    # per hit, N sequential shell round-trips. One GNU ``find -printf``
    # gathers path, size, mode, user, group and mtime in a single command.
    # The filters are the find family's shared set. Deliberate choices:
    # GNU-only (``-printf`` does not exist on BSD find -- the ``|| true``
    # guard masks that to [], the FileFlags Linux-adaptation precedent);
    # entries are sorted by path for a deterministic listing (find's
    # traversal order is filesystem-dependent); tab-separated fields mean
    # a path containing a tab or newline breaks its row, which is skipped
    # rather than misparsed. The ``|| true`` guard also masks a missing
    # start path to []. The SimulatedHost seam is ``found_files``, keyed by
    # the rendered command string like the find family's ``find_results``.
    # ``FindFiles`` is the bare-path sibling; ``DirectoryEntries`` the
    # shallow one-level listing.

    #: The ``-printf`` format: path, size, mode, user, group, epoch mtime,
    #: tab-separated (find interprets the \t and \n escapes itself).
    PRINTF_FORMAT = "%p\\t%s\\t%m\\t%u\\t%g\\t%T@\\n"

    @classmethod
    def command(
        cls,
        path: str,
        *,
        size: str | int | None = None,
        min_size: str | int | None = None,
        max_size: str | int | None = None,
        maxdepth: int | None = None,
        fname: str | None = None,
        iname: str | None = None,
        regex: str | None = None,
        path_match: str | None = None,
        args: list[str] | None = None,
        quote_path: bool = True,
    ) -> str:
        """Shell printing one metadata row per regular file, guarded."""
        quoted = shlex.quote(path) if quote_path else path
        parts = ["find", quoted, "-type", "f"]
        parts += _find_filter_args(
            size=size,
            min_size=min_size,
            max_size=max_size,
            maxdepth=maxdepth,
            fname=fname,
            iname=iname,
            regex=regex,
            path_match=path_match,
            args=args,
            quote_path=quote_path,
        )
        parts += ["-printf", f"'{cls.PRINTF_FORMAT}'"]
        return " ".join(parts) + " || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[FoundFile]:
        """Parse the ``-printf`` rows into FoundFile entries sorted by path.

        A row without exactly six tab-separated fields, or with a
        non-integer size/mode, is skipped (a tab/newline-bearing path
        breaks its own row); an unparseable ``%T@`` epoch reads as a None
        mtime rather than dropping the hit. No output reads as [].
        """
        entries: list[FoundFile] = []
        for line in output:
            fields = line.split("\t")
            if len(fields) != 6:
                continue
            try:
                size = int(fields[1])
                mode = int(fields[2])
            except ValueError:
                continue
            try:
                mtime: datetime | None = (
                    datetime.fromtimestamp(int(float(fields[5])), UTC).replace(
                        tzinfo=None
                    )
                )
            except (ValueError, OverflowError, OSError):
                mtime = None
            entries.append(
                FoundFile(
                    path=fields[0],
                    size=size,
                    mode=mode,
                    user=fields[3],
                    group=fields[4],
                    mtime=mtime,
                )
            )
        return sorted(entries, key=lambda entry: entry["path"])

    @classmethod
    async def process(
        cls,
        host: Host,
        path: str,
        *,
        size: str | int | None = None,
        min_size: str | int | None = None,
        max_size: str | int | None = None,
        maxdepth: int | None = None,
        fname: str | None = None,
        iname: str | None = None,
        regex: str | None = None,
        path_match: str | None = None,
        args: list[str] | None = None,
        quote_path: bool = True,
    ) -> list[FoundFile]:
        command = cls.command(
            path,
            size=size,
            min_size=min_size,
            max_size=max_size,
            maxdepth=maxdepth,
            fname=fname,
            iname=iname,
            regex=regex,
            path_match=path_match,
            args=args,
            quote_path=quote_path,
        )
        found = getattr(host, "found_files", None)
        if found is not None:
            # SimulatedHost seam: command-keyed like find_results; per-dict
            # copies so callers never mutate simulated state (the entries
            # are flat -- datetime values are immutable).
            return [dict(entry) for entry in found.get(command, [])]
        # Real transport: the guarded -printf find over asyncssh.
        stdout = await host.run_shell(command)
        return cls.parse(stdout.splitlines())


class DirectoryEntry(TypedDict):
    """
    Attributes:
        name: The entry's basename (no directory component); join it with
            the listed path to address the entry.
        type: The entry's path type as a word -- "file", "directory",
            "link", "socket", "fifo", "block" or "character" -- the same
            vocabulary the File family's type flags use.
    """

    name: str
    type: str


#: The ``%y`` type letters GNU find -printf prints, mapped to the File
#: family's type words (find spells a regular file ``f``, not ``-``).
_PRINTF_TYPE_TO_TYPE = {
    "f": "file",
    "d": "directory",
    "l": "link",
    "s": "socket",
    "p": "fifo",
    "b": "block",
    "c": "character",
}


class DirectoryEntries:
    """One directory's immediate entries with their types, never recursive.

    **Commands:**

    ```bash
    find <path> -mindepth 1 -maxdepth 1 -printf '%f\t%y\n' || true
    ```

    **Returns:**

    ```python
    list[DirectoryEntry]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_directory_entries.py``.
    """

    # A reemote ORIGINAL: the baseline ships no counterpart. This is the
    # listing the stat-only ``Directory`` fact cannot give -- that fact
    # describes the directory *itself* (owner, mode, timestamps), never
    # the names inside it -- and the cheap shallow form of the find
    # family: one ``-mindepth 1 -maxdepth 1`` sweep reports each immediate
    # entry's basename and type without recursing (Ansible's ``find``
    # with ``recurse: false``, the ``with_fileglob`` parent-dir pattern).
    # Dotfiles are included (find does not skip them); the directory
    # itself is not (mindepth 1). Entries are sorted by name for a
    # deterministic listing. GNU-only like FindFilesInfo: BSD find has no
    # ``-printf``, and the ``|| true`` guard masks that -- and a missing
    # start path -- to []. A row whose type letter is unknown or whose
    # name contains a tab/newline is skipped rather than misparsed. The
    # SimulatedHost seam is ``directory_entries``, keyed by the
    # caller-supplied path string verbatim (the command has no filters,
    # so the path alone identifies the question); an absent key reads [].
    # For recursive enumeration see FindFiles/FindDirectories/FindLinks;
    # for per-file metadata see FindFilesInfo.

    @classmethod
    def command(cls, path: str) -> str:
        """Shell printing one name/type row per immediate entry, guarded."""
        return (
            f"find {shlex.quote(path)} -mindepth 1 -maxdepth 1 "
            "-printf '%f\\t%y\\n' || true"
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[DirectoryEntry]:
        """Parse the name/type rows into DirectoryEntry entries by name.

        A row without exactly two tab-separated fields, or with an
        unknown type letter, is skipped; no output reads as [].
        """
        entries: list[DirectoryEntry] = []
        for line in output:
            fields = line.split("\t")
            if len(fields) != 2:
                continue
            entry_type = _PRINTF_TYPE_TO_TYPE.get(fields[1])
            if entry_type is None:
                continue
            entries.append(DirectoryEntry(name=fields[0], type=entry_type))
        return sorted(entries, key=lambda entry: entry["name"])

    @classmethod
    async def process(cls, host: Host, path: str) -> list[DirectoryEntry]:
        listing = getattr(host, "directory_entries", None)
        if listing is not None:
            # SimulatedHost seam: path -> the entry rows a test seeds;
            # per-dict copies so callers never mutate simulated state.
            return [dict(entry) for entry in listing.get(path, [])]
        # Real transport: the guarded shallow -printf find over asyncssh.
        stdout = await host.run_shell(cls.command(path))
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------

#: The ext2-family attribute letters ``lsattr`` prints in its flags field --
#: the vocabulary ``files.flags`` accepts (the baseline's BSD ``chflags`` verb
#: names, like ``hidden``, are not valid here; the developer-chosen Linux
#: adaptation of ``files.flags``). ``e`` (extent) is real ext4 state and
#: readable, though ``chattr`` refuses to set or clear it -- the operation
#: deliberately does not special-case it, so requesting it plans a
#: ``chattr`` that fails at apply, exactly the baseline's shape for a
#: no-op-protection verb on a wrong-flavoured host.
_LINUX_FLAG_LETTERS = "AaCcDeEFfiJlmPSsTtuXxz"


@dataclass(frozen=True)
class LinkCommand(Command):
    """Create a link (``ln [-s] target path``), or remove the link at
    ``path`` (``rm -f path``) when ``target`` is None.

    Both paths are shell-quoted, so a name carrying spaces or shell
    metacharacters stays a single argument -- matching the baseline's quoting of
    the link helpers. ``symbolic`` selects ``ln -s``; the command never
    carries ``-f``: the operation removes an existing link before creating
    the new one instead of forcing the write over it.
    """

    path: str
    target: str | None = None  # None means "remove the link at path"
    symbolic: bool = True

    def shell(self) -> str:
        if self.target is None:
            return f"rm -f {shlex.quote(self.path)}"
        flag = " -s" if self.symbolic else ""
        return f"ln{flag} {shlex.quote(self.target)} {shlex.quote(self.path)}"


async def link(
    ctx: OpContext,
    path: str,
    target: str | None = None,
    present: bool = True,
    user: str | None = None,
    group: str | None = None,
    symbolic: bool = True,
    create_remote_dir: bool = True,
    force: bool = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add, update or remove a link.

    Args:
        ctx (OpContext): Execution context for the operation.
        path (str): The path of the link to manage.
        target (str | None, optional): The path the link points to.
            Required when ``present`` is True. Defaults to None.
        present (bool, optional): Whether the link should exist.
            Defaults to True.
        user (str | None, optional): The user to own the link. Defaults to
            None.
        group (str | None, optional): The group to own the link. Defaults
            to None.
        symbolic (bool, optional): Whether to make a symbolic link rather
            than a hard link. Defaults to True.
        create_remote_dir (bool, optional): Whether to ``mkdir -p`` the
            link's parent directory when it is missing. Defaults to True.
        force (bool, optional): Replace an existing non-link at ``path``
            (``rm -rf``) instead of raising. Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The link commands, only when the link differs from the
            request.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, TypeError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_op_link.py``.
    """

    # baseline ``files.link`` parity -- the ``files.file``/``files.directory``
    # shape with ``touch``/``mkdir`` traded for ``ln``. The ``Link`` fact is
    # read once and only the differing commands are yielded: ``ln -s`` (or
    # plain ``ln``, hard) creates a missing link, ``rm -f`` plus a second
    # ``ln`` re-points an existing one whose target differs (the baseline's
    # "remove it and recreate a new one" rule), ``chown -h`` edits the
    # ownership, and ``rm -f`` (the same ``LinkCommand`` with no target)
    # removes it when ``present=False``. The link's own metadata is read
    # through ``Link``, the stat-level fact also used for auditing, rather
    # than through the cheaper ``FileLink`` ``readlink`` probe: an existing
    # row is the only way to tell "correct" from "wrong target" from "not a
    # link", and the owner comparison needs the row's user/group. ``-h``
    # (``dereference=False``) chowns the link, never its target.
    #
    # Hard links (``symbolic=False``) need a second reading: ``Link``
    # answers ``False`` for one because a hard link is not a symlink, so the
    # path is re-read through ``File`` and compared to the target with
    # ``SameFile`` -- same inode means the link already is exactly what was
    # asked for. Where ``FileLink`` alone could not tell a hard link from an
    # unrelated file, the pair of reads can.
    #
    # Known asymmetry, disclosed as upstream parity rather than
    # changed: ``symbolic`` is not compared on the update branch, so an
    # existing symlink whose target matches is never converted into a hard
    # link (nor the reverse) -- the ensure no-ops on the target and owner
    # alone. The baseline's ``files.link`` has the same gap.
    #
    # The baseline's ``create_remote_dir=True`` delegates to ``files.directory``
    # with ``_no_check_owner_mode``: a missing parent is ``mkdir -p``'d (and
    # ``chown``'d to the requested user/group, as the baseline's create branch
    # does), an existing parent is left alone -- its mode is deliberately
    # *not* copied over, the baseline's doc note ("call ``files.directory``
    # separately" for that). ``create_remote_dir=False`` skips the probe and
    # lets the ``ln`` fail on a missing parent.
    #
    # The baseline's ``force_backup``/``force_backup_dir`` are deliberately
    # dropped (developer decision, same as ``files.file`` and
    # ``files.directory``): the backup form ``mv``s the invalid path to a
    # ``datetime.now()``-stamped name, which would differ between the
    # prepare and apply phases and break the request-sequence determinism
    # the driver guards. ``force=True`` therefore always removes the
    # non-link first (``rm -rf``, exactly the baseline's ``force`` with
    # ``force_backup=False``), and ``force=False`` raises ``ValueError``
    # (the baseline's ``OperationError``). The baseline's tolerance of a *symlink*
    # parent (its internal ``_no_fail_on_link`` knob) is dropped with the
    # knob, so a parent that is a link reads as "exists and is not a
    # directory" and raises. The commands touch arbitrary paths, so for
    # root-owned locations flag the request ``sudo=True``; ``**kwargs``
    # absorbs any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    try:
        path = os.fspath(path)
    except TypeError:
        raise TypeError(
            "`path` must be a string or `os.PathLike` object"
        ) from None

    if target is not None:
        # Normalized like ``path``: shlex.quote on a non-str would otherwise
        # surface an opaque TypeError from deep inside the descriptor.
        try:
            target = os.fspath(target)
        except TypeError:
            raise TypeError(
                "`target` must be a string or `os.PathLike` object"
            ) from None

    if present and not target:
        raise ValueError("If present is True target must be provided")

    info = await ctx.facts.get(Link, path)

    if info is False and not symbolic and target is not None:
        # The path exists but is not a symlink -- it may already be a hard
        # link to the target, which only the file/inode reads can tell.
        file_info = await ctx.facts.get(File, path)
        same_file = await ctx.facts.get(SameFile, path, target)
        if file_info and same_file:
            info = {**file_info, "link_target": target}

    reasons: list[str] = []
    changed = False

    if info is False:  # the path exists but is not a link
        if not force:
            raise ValueError(f"{path} exists and is not a link")
        changed = True
        reasons.append(f"{path} is not a link, will remove first")
        yield RemoveTreeCommand(src=path)
        info = None

    if not present:
        if info:
            changed = True
            reasons.append(f"{path} present, will remove")
            yield LinkCommand(path=path)
        else:
            reasons.append(f"link {path} does not exist")
        ctx.changed = changed
        ctx.reason = "; ".join(reasons)
        return

    if info is None:  # create
        changed = True
        reasons.append(f"{path} missing, will create")
        if create_remote_dir:
            # posixpath, not os.path: the remote is always *nix while the
            # controller might be Windows (the baseline's own reasoning).
            parent = posixpath.dirname(path)
            if parent:
                parent_info = await ctx.facts.get(Directory, parent)
                if parent_info is False:
                    raise ValueError(f"{parent} exists and is not a directory")
                if parent_info is None:
                    reasons.append(f"parent {parent} missing, will create")
                    yield MkdirCommand(path=parent)
                    if user or group:
                        # The baseline's delegated directory chowns a freshly
                        # created parent (its create branch) but never
                        # chmods it -- the mode is not copied over. The
                        # parent is a directory, so the chown dereferences.
                        yield ChownCommand(path=parent, user=user, group=group)
        yield LinkCommand(path=path, target=target, symbolic=symbolic)
        if user or group:
            yield ChownCommand(
                path=path, user=user, group=group, dereference=False
            )
        ctx.changed = changed
        ctx.reason = "; ".join(reasons)
        return

    # update: compare the requested target/owner against the fact's metadata
    # (.get: a dangling link read through the ls fallback carries no
    # link_target at all, which the baseline's subscription would KeyError on).
    if info.get("link_target") != target:
        changed = True
        reasons.append(f"target {info.get('link_target')} differs, will relink")
        yield LinkCommand(path=path)
        yield LinkCommand(path=path, target=target, symbolic=symbolic)

    if (user and info["user"] != user) or (group and info["group"] != group):
        changed = True
        reasons.append(f"owner {info['user']}:{info['group']} differs, will chown")
        yield ChownCommand(path=path, user=user, group=group, dereference=False)

    if not changed:
        reasons.append(f"link {path} already exists")

    ctx.changed = changed
    ctx.reason = "; ".join(reasons)


@dataclass(frozen=True)
class FileCommand(Command):
    """Create or refresh a regular file (``touch path``), or remove it
    (``rm -f path``) when ``present`` is False.

    The path is shell-quoted, so a filename carrying spaces or shell
    metacharacters stays a single argument -- matching the baseline's quoting of
    the touch/rm helpers.
    """

    path: str
    present: bool = True

    def shell(self) -> str:
        if not self.present:
            return f"rm -f {shlex.quote(self.path)}"
        return f"touch {shlex.quote(self.path)}"


async def file(
    ctx: OpContext,
    path: str,
    present: bool = True,
    user: str | None = None,
    group: str | None = None,
    mode: int | str | None = None,
    touch: bool = False,
    create_remote_dir: bool = True,
    force: bool = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add, update or remove a regular file.

    Args:
        ctx (OpContext): Execution context for the operation.
        path (str): The remote file path to manage.
        present (bool, optional): Whether the file should exist.
            Defaults to True.
        user (str | None, optional): The user to own the file.
            Defaults to None.
        group (str | None, optional): The group to own the file.
            Defaults to None.
        mode (int | str | None, optional): The file's permissions --
            an octal-digit int or string (``750``, ``"0750"``, ``"0o750"``)
            or a symbolic mode. Defaults to None.
        touch (bool, optional): Whether to ``touch`` an existing file.
            The option is not idempotent: the ``touch`` always re-runs.
            Defaults to False.
        create_remote_dir (bool, optional): Whether to ``mkdir -p`` the
            file's parent directory when it is missing. Defaults to True.
        force (bool, optional): Replace an existing non-file at ``path``
            (``rm -rf``) instead of raising. Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The file commands, only when the file differs from the
            request.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, TypeError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_op_file.py``.
    """

    # baseline ``files.file`` parity -- the ``files.directory`` shape with
    # ``mkdir -p``/``rm -rf`` traded for ``touch``/``rm -f``. The ``File``
    # fact is read once and only the differing commands are yielded:
    # ``touch`` (plus ``chmod``/``chown`` when requested) creates a missing
    # file, the metadata comparison edits an existing one, and ``rm -f``
    # (the same ``FileCommand`` with ``present=False``) removes it. Like
    # The baseline's ``touch`` option, ``touch=True`` always re-runs the
    # ``touch`` on an existing file (a deliberately non-idempotent option),
    # and a symbolic mode never equals the fact's octal int, so it re-runs
    # the ``chmod`` on every materialization. The path is validated and the
    # mode normalised exactly as in ``files.directory`` (``os.fspath``,
    # ``_ensure_mode_int``). The commands touch arbitrary paths, so for
    # root-owned locations flag the request ``sudo=True``.
    #
    # The baseline's ``create_remote_dir=True`` delegates to ``files.directory``
    # with ``_no_check_owner_mode``: a missing parent is ``mkdir -p``'d
    # (and ``chown``'d to the requested user/group, as the baseline's create
    # branch does), an existing parent is left alone -- its mode is
    # deliberately *not* copied over, the baseline's doc note ("call
    # ``files.directory`` separately" for that). ``create_remote_dir=False``
    # skips the probe and lets the ``touch`` fail on a missing parent.
    #
    # The baseline's ``force_backup``/``force_backup_dir`` are deliberately
    # dropped (developer decision, same as ``files.directory``): the backup
    # form ``mv``s the invalid path to a ``datetime.now()``-stamped name,
    # which would differ between the prepare and apply phases and break the
    # request-sequence determinism the driver guards. ``force=True``
    # therefore always removes the non-file first (``rm -rf``, exactly
    # The baseline's ``force`` with ``force_backup=False``), and ``force=False``
    # raises ``ValueError`` (the baseline's ``OperationError``). The baseline's
    # tolerance of a *symlink* path or parent (its internal
    # ``_no_fail_on_link`` knob) is dropped with the knob, so either one
    # reads as "exists and is not a ..." and raises; ``**kwargs`` absorbs
    # any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    try:
        path = os.fspath(path)
    except TypeError:
        raise TypeError(
            "`path` must be a string or `os.PathLike` object"
        ) from None

    mode = _ensure_mode_int(mode)
    info = await ctx.facts.get(File, path)

    reasons: list[str] = []
    changed = False

    if info is False:  # the path exists but is not a regular file
        if not force:
            raise ValueError(f"{path} exists and is not a file")
        changed = True
        reasons.append(f"{path} is not a file, will remove first")
        yield RemoveTreeCommand(src=path)
        info = None

    if not present:
        if info:
            changed = True
            reasons.append(f"{path} present, will remove")
            yield FileCommand(path=path, present=False)
        else:
            reasons.append(f"file {path} does not exist")
        ctx.changed = changed
        ctx.reason = "; ".join(reasons)
        return

    if info is None:  # create
        changed = True
        reasons.append(f"{path} missing, will create")
        if create_remote_dir:
            # posixpath, not os.path: the remote is always *nix while the
            # controller might be Windows (the baseline's own reasoning).
            parent = posixpath.dirname(path)
            if parent:
                parent_info = await ctx.facts.get(Directory, parent)
                if parent_info is False:
                    raise ValueError(f"{parent} exists and is not a directory")
                if parent_info is None:
                    reasons.append(f"parent {parent} missing, will create")
                    yield MkdirCommand(path=parent)
                    if user or group:
                        # The baseline's delegated directory chowns a freshly
                        # created parent (its create branch) but never
                        # chmods it -- the mode is not copied over.
                        yield ChownCommand(path=parent, user=user, group=group)
        yield FileCommand(path=path, present=True)
        if mode:
            yield ChmodCommand(path=path, mode=mode)
        if user or group:
            yield ChownCommand(path=path, user=user, group=group)
        ctx.changed = changed
        ctx.reason = "; ".join(reasons)
        return

    # update: compare the requested mode/owner against the fact's metadata
    if touch:
        changed = True
        reasons.append(f"touch requested, will touch {path}")
        yield FileCommand(path=path, present=True)

    if mode and info["mode"] != mode:
        changed = True
        reasons.append(f"mode {info['mode']} differs, will chmod {mode}")
        yield ChmodCommand(path=path, mode=mode)

    if (user and info["user"] != user) or (group and info["group"] != group):
        changed = True
        reasons.append(f"owner {info['user']}:{info['group']} differs, will chown")
        yield ChownCommand(path=path, user=user, group=group)

    if not changed:
        reasons.append(f"file {path} already exists")

    ctx.changed = changed
    ctx.reason = "; ".join(reasons)


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
    """Upload an IO-like source to a remote path.

    Args:
        ctx (OpContext): Execution context for the operation.
        src (IO[Any]): The seekable IO-like source on the control machine.
        dest (str): The remote path to write.

    Yields:
        Command: The upload transfer, when the content differs.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, OSError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_put.py``.
    """

    # ``src`` is a seekable IO-like object on the control machine (a file
    # opened with ``open()``, an ``io.BytesIO``/``io.StringIO``, ...); unlike
    # The baseline's ``files.put``, which accepts ``str | IO[Any]``, a *path string
    # is deliberately not supported* -- the content source is always a stream,
    # rewound to position 0 before every read. Idempotence is content-based:
    # the source's sha1 (hashed locally with ``hashlib`` in a worker thread,
    # never a fact -- a control-machine digest is a plain library call) is
    # compared against the remote file's (``RemoteFileSha1``, gathered over
    # SSH), and a ``PutCommand`` is yielded only when they differ.
    #
    # The transfer is unprivileged: it rides the connection's scp channel as
    # the login user, so the ``sudo``/``su`` escalation flags do not apply to
    # it and ``dest`` must be writable by that user (put into a writable path
    # first and move it with ``files.move`` flagged ``sudo=True`` when it must
    # end up root-owned).
    local_sha1 = await asyncio.to_thread(_sha1_of_source, src)
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
    """Download a remote file into an IO-like destination.

    Args:
        ctx (OpContext): Execution context for the operation.
        src (str): The remote path to download.
        dest (IO[Any]): The seekable, writable IO-like destination on the
            control machine.

    Yields:
        Command: The download transfer, when the content differs.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, OSError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_get.py``.
    """

    # The mirror of ``files.put``: ``dest`` is a seekable, writable IO-like
    # object on the control machine (an ``io.BytesIO``/``io.StringIO``, a file
    # opened for writing, ...) that receives the content -- *not* a local
    # filename. Idempotence is content-based: the remote file's sha1
    # (``RemoteFileSha1``, gathered over SSH) is compared against the
    # destination's current content sha1 (hashed locally with ``hashlib`` in a
    # worker thread), and a ``GetCommand`` is yielded only when they differ,
    # so a fresh or empty destination always
    # downloads and a second get of the same file is a no-op. The baseline's
    # ``add_deploy_dir``, ``create_local_dir`` and ``force`` options are
    # deliberately dropped: there is no local path to place, create or force.
    # Like the baseline, an absent remote source still plans a download (which
    # fails at scp if the file truly is not there).
    #
    # The transfer is unprivileged: it rides the connection's scp channel as
    # the login user, so the ``sudo``/``su`` escalation flags do not apply to
    # it and ``src`` must be readable by that user.
    remote_sha1 = await ctx.facts.get(RemoteFileSha1, src)
    local_sha1 = await asyncio.to_thread(_sha1_of_source, dest)

    if remote_sha1 == local_sha1:
        ctx.reason = f"local copy already up to date (from {src})"
    else:
        ctx.changed = True
        ctx.reason = f"local copy missing or differs from remote {src}"
        yield GetCommand(src=src, dest=dest)


async def edit(
    ctx: OpContext, path: str, transform: Callable[[str | None], str]
) -> AsyncGenerator[Command]:
    """Edit a remote file through a pure content transform.

    Args:
        ctx (OpContext): Execution context for the operation.
        path (str): The remote file to edit.
        transform (Callable[[str | None], str]): A pure, idempotent callable
            taking the current content (``str``, or ``None`` when the file is
            absent) and returning the desired content as a ``str``.

    Yields:
        Command: The rewrite upload, when the transformed content differs.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_edit.py``.
    """

    # The generic content-editing primitive: instead of a bespoke operation
    # per config-file format (the baseline's ``files.line``, ``files.replace``
    # and friends each hard-code one shape of edit), this reads the file's
    # current text with the ``FileContents`` fact, applies a caller-supplied
    # *pure* ``transform``, and writes the result back with a ``PutCommand``
    # only when it differs. Any "edit a file in place" task -- setting a
    # ``sysctl`` key, appending a managed block -- is then just a transform,
    # with no new operation needed. A few high-traffic formats do get a
    # dedicated spool-and-install operation (``server.etc_hosts`` ports
    # The baseline's ``/etc/hosts`` editor); this primitive covers the rest.
    #
    # The transform must be *idempotent* -- ``transform(transform(x)) ==
    # transform(x)`` -- so a second run is a no-op, and side-effect free,
    # since it runs once per phase (prepare, then each apply step).
    # Idempotence is content-equality based: the transform's result is
    # compared against the current content, and a ``PutCommand`` is yielded
    # only when they differ, so a repeat edit whose transform is a fixpoint
    # reports no change. An absent file always counts as a change
    # (``transform(None)`` produces the content to create it from).
    #
    # ``transform`` must be a *stable, hashable* object -- a module-level
    # function, not a lambda or closure built inside the deploy generator. It
    # is stored in the ``OperationRequest`` kwargs, which the determinism
    # guard compares by value across phases; a fresh callable per phase would
    # diverge and raise ``DeterminismError``, exactly as a fresh IO stream
    # would for ``files.put``/``files.get``.
    #
    # The write rides ``files.put``'s unprivileged scp transfer, so
    # ``sudo``/``su`` do not apply to it and ``path`` must be writable by the
    # login user. A root-owned target (``/etc/hosts``, a file under ``/etc``)
    # must instead be transformed into a writable path and moved into place
    # with ``files.move`` flagged ``sudo=True``, since a transfer cannot be
    # escalated -- or written in one deploy by ``files.install`` or a
    # dedicated spool-and-install operation such as ``server.etc_hosts``.
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


def _install_spool_path(dest: str) -> str:
    """The deterministic ``/tmp`` spool *file* path ``files.install`` uploads to.

    The spool lives inside a per-install directory rather than at the top
    level of ``/tmp``: the directory name is derived from the destination's
    sha1 so two installs targeting different paths never share (and clobber)
    one spool, while the same dest always yields the same name in both
    phases -- the request sequence stays deterministic. The content is
    spooled to a fixed ``content`` file inside that directory, and the
    elevated install ``rm -rf``s the whole directory afterwards, so no
    leftover lands in ``/tmp``. The name avoids *collisions*, not a hostile
    local user: it is predictable, so on a multi-tenant host the ``/tmp``
    spool is still a symlink-race exposure (the same trade-off the
    namespace-specific spool-and-install operations make).
    """
    digest = hashlib.sha1(dest.encode()).hexdigest()[:16]
    return f"/tmp/reemote-install-{digest}/content"


@dataclass(frozen=True)
class InstallSpoolCommand(TransferCommand):
    """Spool ``files.install``'s content to a ``/tmp`` path on the target.

    The first half of ``files.install`` and a *non-shell* descriptor: it
    copies the content over the connection's scp channel via
    ``Host.upload_file`` rather than running shell text, so it has no
    ``shell()`` and its loggable form is the ``__str__`` rendering
    ``command_shell`` falls back to. Like ``SystemdFileUploadCommand`` the
    content is a plain ``str`` -- hashable and identical across phases --
    wrapped in a fresh ``io.BytesIO`` only at transfer time, so no
    identity-stable stream is needed at the call site. The copy rides as the
    login user (transfers are unprivileged, so the request's ``sudo`` does
    not apply to it), which is why the spool path is world-writable ``/tmp``
    rather than the root-owned destination directory.
    """

    content: str  # the text files.install writes
    dest: str  # spool file path on the target host (inside a sha1-derived /tmp dir)

    def __str__(self) -> str:
        return f"scp <files install> -> {self.dest}"

    async def transfer(self, host: Host) -> None:
        # scp does not create parent directories, and the spool now lives
        # inside a sha1-derived /tmp directory rather than at the top level,
        # so mkdir -p it first. Unprivileged: /tmp is world-writable, so the
        # login user can create the directory, and a transfer never elevates.
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        await host.run_shell(f"mkdir -p {parent}")
        await host.upload_file(io.BytesIO(self.content.encode()), self.dest)


@dataclass(frozen=True)
class InstallCommand(Command):
    """Install the spooled file with ``mode`` (and optional owner), then
    remove the spool file.

    The second half of ``files.install`` and the elevated one: writing a
    root-owned destination needs root, so the request is flagged
    ``sudo=True`` and the whole sequence is wrapped in ``sh -c`` -- a bare
    ``sudo`` prefix would elevate only the first command of the ``&&``
    chain and leave the cleanup running as the login user, whereas
    ``sudo sh -c '...'`` runs every step as root, which can always remove
    the login-user spool file. The sequence creates the destination's
    parent directory (``mkdir -p``), copies the spooled content over the
    target file, optionally chowns it to the requested ``user``/``group``,
    chmods it to the requested mode and removes the spool file -- the
    ``SystemdFileInstallCommand`` pattern with a caller-chosen mode and
    owner. The chown precedes the chmod on purpose: a ``chown`` clears a
    file's setuid/setgid bits, so the requested mode must be applied last
    to stick. The owner step mirrors ``ChownCommand`` -- a user alone or a
    ``user:group`` pair render ``chown``, a group alone switches to
    ``chgrp``, and neither set skips the step entirely (the file stays
    root-owned, the historical behaviour). All paths and the owner spec
    are shell-quoted; the mode is the canonical octal-digit int the
    operation normalised it to and renders unquoted, exactly as
    ``ChmodCommand`` does.
    """

    spool: str  # where InstallSpoolCommand wrote the content
    dest: str  # the root-owned target path
    mode: int  # canonical octal-digit mode (644 for "0644")
    user: str | None = None  # owner to chown to (None leaves root)
    group: str | None = None  # group to chown/chgrp to (None leaves root)

    def shell(self) -> str:
        spool = shlex.quote(self.spool)
        # The spool file lives inside a sha1-derived /tmp directory; remove
        # the whole directory so no leftover lands in /tmp.
        spool_dir = shlex.quote(str(PurePosixPath(self.spool).parent))
        dest = shlex.quote(self.dest)
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        steps = [f"mkdir -p {parent}", f"cp {spool} {dest}"]
        if self.user and self.group:
            spec = shlex.quote(f"{self.user}:{self.group}")
            steps.append(f"chown {spec} {dest}")
        elif self.user:
            steps.append(f"chown {shlex.quote(self.user)} {dest}")
        elif self.group:
            steps.append(f"chgrp {shlex.quote(self.group)} {dest}")
        steps.append(f"chmod {self.mode} {dest}")
        steps.append(f"rm -rf {spool_dir}")
        inner = " && ".join(steps)
        return f"sh -c {shlex.quote(inner)}"


async def install(
    ctx: OpContext,
    content: str,
    dest: str,
    mode: str = "0644",
    user: str | None = None,
    group: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Install text content at a root-owned path via spool-and-install.

    Args:
        ctx (OpContext): Execution context for the operation.
        content (str): The desired text content of the file.
        dest (str): The remote path to install the content at.
        mode (str, optional): The installed file's permissions as
            octal-digit text (``"0644"``, ``"600"``). Defaults to "0644".
        user (str | None, optional): The user to own the installed file.
            Defaults to None (root, the elevated install's owner).
        group (str | None, optional): The group to own the installed file.
            Defaults to None (root).
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The spool transfer and the elevated install command, only
            when the content differs.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_install.py``.
    """

    # A reemote original: the generic "write content to a root-owned path"
    # operation the namespace-specific spool-and-install operations
    # (``apt.sources_file``, ``dnf.repo``, ``systemd.dropin``) each spell
    # out for their own config paths. ``files.put``/``files.edit`` ride
    # unprivileged scp and cannot write a root-owned target directly, and
    # the put-then-``files.move`` recipe spans TWO runs (``files.move``
    # raises on the not-yet-created spool in the prepare phase). This
    # operation yields the transfer and the elevated install from ONE
    # request -- prepare records both, apply runs them in order -- so the
    # whole write fits a single deploy.
    #
    # Idempotent on ``FileContents``: when the destination already holds
    # exactly ``content`` nothing is yielded; otherwise an
    # ``InstallSpoolCommand`` transfer spools the text to a dest-derived
    # ``/tmp`` path over scp (unprivileged regardless of the request's
    # ``sudo``), then an ``InstallCommand`` runs the elevated
    # ``mkdir -p``/``cp``/``chmod``/``rm`` install. Installing under
    # ``/etc`` needs root, so flag the request ``sudo=True`` (or
    # ``su=True``); the run-as user and any password come from the
    # inventory ``privilege`` data, never the operation.
    #
    # ``content`` is a plain ``str``, not an IO stream (the
    # ``systemd.dropin`` shape, developer-chosen): text is what
    # ``FileContents`` compares, a str is hashable and equal across
    # phases, and no identity-stable module-level stream is needed. Binary
    # content stays ``files.put``'s job (into a login-user-writable path).
    # The spool name is the destination's sha1 prefix, so different targets
    # never collide on one spool file while the name stays deterministic
    # across phases; it is collision avoidance, not a security boundary
    # (the name is predictable on world-writable ``/tmp``).
    #
    # ``mode`` accepts octal-digit text only (``"0644"``, ``"600"``);
    # a symbolic mode raises ``ValueError`` -- ``files.file``/``chmod``
    # own relative edits. The mode is applied on every write, but
    # idempotence is content-only: a mode-only drift does not re-install
    # (use ``files.file`` to converge metadata). ``user``/``group`` ride
    # the same contract: an optional ``chown``/``chgrp`` step inside the
    # elevated install chain (before the ``chmod``, which a chown would
    # otherwise strip of setuid/setgid bits), applied on every write but
    # never compared -- an owner-only drift is ``files.file``'s job too.
    # The named owner must already exist on the host (provision it with
    # ``server.user``/``server.group`` first) or the chown fails at apply.
    # One read-back caveat: ``FileContents`` cats unprivileged, so a file
    # installed with a mode the login user cannot read (eg ``0640`` owned
    # by a service account) reads back as empty content and re-installs on
    # every deploy -- content idempotence needs a login-readable file.
    # There is deliberately no ``present=False`` removal branch --
    # ``files.file(present=False)`` already owns removal. ``**kwargs``
    # absorbs any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    if not mode or any(c not in "01234567" for c in mode):
        raise ValueError(
            f"Invalid file mode: {mode!r} expected octal digits "
            "(eg '0644' or '600')"
        )
    mode_int = int(mode)

    current = await ctx.facts.get(FileContents, dest)
    if current == content:
        ctx.reason = f"content already up to date ({dest})"
        return

    ctx.changed = True
    ctx.reason = (
        f"content {dest} differs, will install"
        if current is not None
        else f"content {dest} is absent, will install"
    )
    spool = _install_spool_path(dest)
    yield InstallSpoolCommand(content=content, dest=spool)
    yield InstallCommand(
        spool=spool, dest=dest, mode=mode_int, user=user, group=group
    )


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
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Move a remote file/directory/link into a remote directory.

    Args:
        ctx (OpContext): Execution context for the operation.
        src (str): The remote path to move.
        dest (str): The remote directory to move ``src`` into; the moved path
            becomes ``dest/basename(src)``.
        overwrite (bool, optional): When the destination path already exists,
            remove it first (``rm -rf``) instead of raising. Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The move command, always, preceded by a removal when
            ``overwrite`` clears an existing target.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_move.py``.
    """

    # Mirrors the baseline's ``files.move``. Stateless like the baseline: there is no
    # no-op branch -- a valid move always yields a ``MoveCommand`` (preceded
    # by a ``RemoveTreeCommand`` when ``overwrite`` clears an existing
    # target), so ``ctx.changed`` is always True. Repeating the same move
    # fails at apply when ``mv`` exits non-zero on the now-absent source
    # (``CommandError``, fail-fast).
    #
    # Reads ``File(src)`` (a directory or symlink reads False, which the baseline
    # accepts -- only absence is rejected), ``Directory(dest)`` and
    # ``File(dest/basename(src))``. An absent ``src``, a ``dest`` that is not
    # a directory or does not exist, and an existing target without
    # ``overwrite`` all raise ``ValueError``. The baseline's phase-aware handling
    # of the two absence cases (a soft log while planning, an error only
    # while executing) is deliberately dropped because reemote operations
    # are phase-unaware: they raise in *both* phases. A consequence is that
    # a "create a file then move it" flow cannot share one deploy -- the
    # prepare phase would raise on the not-yet-created source -- so it spans
    # two runs (see the ``files.edit`` + ``files.move`` ``/etc/hosts``
    # recipe example).
    #
    # Both commands are shell text, so flagging the request ``sudo=True``
    # elevates the whole move -- the vehicle for installing a login-user
    # spool file into a root-owned directory such as ``/etc``. ``**kwargs``
    # absorbs any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
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


@dataclass(frozen=True)
class CopyCommand(Command):
    """Copy a remote path into a remote directory (``cp -r [-f] src dest``).

    Unlike ``files.move``'s overwrite branch there is no ``rm -rf`` prelude:
    The baseline's copy expresses overwrite as ``cp``'s own ``-f`` flag, so one
    descriptor with an ``overwrite`` field covers both forms.
    """

    src: str
    dest: str
    overwrite: bool = False

    def shell(self) -> str:
        force = " -f" if self.overwrite else ""
        return f"cp -r{force} {shlex.quote(self.src)} {shlex.quote(self.dest)}"


async def copy(
    ctx: OpContext,
    src: str,
    dest: str,
    overwrite: bool = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Copy a remote file/directory into a remote directory.

    Args:
        ctx (OpContext): Execution context for the operation.
        src (str): The remote path to copy.
        dest (str): The remote directory to copy ``src`` into; the copied path
            becomes ``dest/basename(src)`` and the source stays in place.
        overwrite (bool, optional): When the destination path already exists,
            force the copy (``cp -r -f``) instead of comparing or raising.
            Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The copy command, when a copy is needed.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_copy.py``.
    """

    # Mirrors the baseline's ``files.copy``. Unlike ``files.move`` there is no
    # ``rm -rf`` prelude -- the baseline's copy expresses overwrite as ``cp``'s own
    # ``-f`` flag (which merges into an existing directory target rather than
    # replacing it).
    #
    # Reads ``Directory(src)`` and ``File(src)`` and requires one of them to
    # be truthy, exactly the baseline's check: a *symlink* source reads False for
    # both and raises, even though ``files.move`` accepts links (upstream's
    # own asymmetry, kept). ``Directory(dest)`` must be a present directory,
    # and ``File(dest/basename(src))`` probes the target.
    #
    # Idempotence differs from the stateless ``files.move``: with an existing
    # regular-file target and ``overwrite`` unset, the source's and target's
    # ``RemoteFileSha1`` are compared -- equal content is a no-op
    # ("<target> already exists"), different content raises ``ValueError``.
    # The baseline's ``Sha1File`` -> ``Md5File`` -> ``Sha256File`` fallback chain
    # is deliberately reduced to the sha1-only ``RemoteFileSha1``, the same
    # single-hash precedent as ``files.put``/``files.get``. A target that is
    # not a regular file (``File`` reads False/None) skips the comparison
    # and the copy is planned, the baseline's truthiness kept verbatim.
    #
    # An absent ``src``, a ``dest`` that is not a directory or does not
    # exist, and an existing *different* target without ``overwrite`` all
    # raise ``ValueError``. The baseline's phase-aware handling of the two
    # absence cases (a soft log while planning, an error only while
    # executing) is deliberately dropped because reemote operations are
    # phase-unaware: they raise in *both* phases, the same strictness chosen
    # for ``files.move``. A consequence is that a "create a file then copy
    # it" flow cannot share one deploy -- the prepare phase would raise on
    # the not-yet-created source -- so it spans two runs.
    #
    # The command is shell text, so flagging the request ``sudo=True``
    # elevates the whole copy. ``**kwargs`` absorbs any further global
    # arguments (``op()`` already strips ``name``/``sudo``/``su``).
    src_is_dir = await ctx.facts.get(Directory, src)
    src_is_file = await ctx.facts.get(File, src)
    if not src_is_file and not src_is_dir:
        raise ValueError(f"src {src} does not exist")

    dest_info = await ctx.facts.get(Directory, dest)
    if dest_info is False:
        raise ValueError(f"dest {dest} is not a directory")
    if dest_info is None:
        raise ValueError(f"dest directory {dest} does not exist")

    full_dest = posixpath.join(dest, PurePosixPath(src).name)
    dest_file_exists = await ctx.facts.get(File, full_dest)
    if dest_file_exists and not overwrite:
        src_sha1 = await ctx.facts.get(RemoteFileSha1, src)
        dest_sha1 = await ctx.facts.get(RemoteFileSha1, full_dest)
        if src_sha1 and dest_sha1 and src_sha1 == dest_sha1:
            ctx.reason = f"{full_dest} already exists"
            return
        raise ValueError(f"{full_dest} already exists and is different than src")

    ctx.changed = True
    ctx.reason = f"will copy {src} into {dest}"
    yield CopyCommand(src=src, dest=dest, overwrite=overwrite)


def _ensure_mode_int(mode: int | str | None) -> int | str | None:
    """Normalise a requested mode to the baseline's canonical octal-digit int.

    ``644``, ``"644"``, ``"0644"`` and ``"0o644"`` all mean ``rw-r--r--``;
    symbolic modes (``"u+x"``) and ``None`` pass through unchanged. An
    integer is read as its octal *digit representation* (``644`` means
    ``rw-r--r--``): a true octal literal is indistinguishable from its
    decimal value at runtime, so use the ``"0o644"`` string form instead.
    """
    # Ports the baseline's ``operations.util.files.ensure_mode_int`` verbatim,
    # including the non-octal-digit ``ValueError``.
    if mode is None or isinstance(mode, bool):
        return mode

    if isinstance(mode, int):
        digits = str(mode)
    else:
        digits = mode.strip().lower().removeprefix("0o")
        if not digits.isdigit():
            # Symbolic mode; return as-is (it always gets run).
            return mode

    if any(c not in "01234567" for c in digits):
        raise ValueError(
            f"Invalid file mode: {mode!r} contains non-octal digits "
            "(expected octal digits 0-7, eg 644, '0644' or '0o644')",
        )

    # Drop any leading zeros ('0644' -> 644) for the canonical digit int.
    return int(digits)


@dataclass(frozen=True)
class MkdirCommand(Command):
    """Create a directory and its missing parents (``mkdir -p path``)."""

    path: str

    def shell(self) -> str:
        return f"mkdir -p {shlex.quote(self.path)}"


@dataclass(frozen=True)
class ChmodCommand(Command):
    """Set a path's mode (``chmod [-R] mode path``).

    The mode is the canonical spelling ``directory`` normalised it to --
    an octal-digit int, or the symbolic string verbatim -- and renders
    unquoted exactly as the baseline's chmod helper does; only the target is
    shell-quoted.
    """

    path: str
    mode: int | str
    recursive: bool = False

    def shell(self) -> str:
        flags = " -R" if self.recursive else ""
        return f"chmod{flags} {self.mode} {shlex.quote(self.path)}"


@dataclass(frozen=True)
class ChownCommand(Command):
    """Set a path's owner and/or group (``chown``/``chgrp [-R] [-h] spec path``).

    Mirrors the baseline's chown helper: a user alone or a ``user:group`` pair
    render ``chown``, a group alone switches to ``chgrp``. At least one of
    the two must be set. ``recursive`` adds ``-R``, and ``dereference``
    (False only for ``files.link``, which owns the link and not its target)
    adds ``-h``.
    """

    path: str
    user: str | None = None
    group: str | None = None
    recursive: bool = False
    dereference: bool = True

    def shell(self) -> str:
        if self.user and self.group:
            verb, spec = "chown", f"{self.user}:{self.group}"
        elif self.user:
            verb, spec = "chown", self.user
        elif self.group:
            verb, spec = "chgrp", self.group
        else:
            raise ValueError("chown requires at least one of user or group")
        flags = ""
        if self.recursive:
            flags += " -R"
        if not self.dereference:
            flags += " -h"
        return f"{verb}{flags} {shlex.quote(spec)} {shlex.quote(self.path)}"


async def directory(
    ctx: OpContext,
    path: str,
    present: bool = True,
    user: str | None = None,
    group: str | None = None,
    mode: int | str | None = None,
    recursive: bool = False,
    force: bool = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add, update or remove a directory.

    Args:
        ctx (OpContext): Execution context for the operation.
        path (str): The remote directory path to manage.
        present (bool, optional): Whether the directory should exist.
            Defaults to True.
        user (str | None, optional): The user to own the directory.
            Defaults to None.
        group (str | None, optional): The group to own the directory.
            Defaults to None.
        mode (int | str | None, optional): The directory's permissions --
            an octal-digit int or string (``750``, ``"0750"``, ``"0o750"``)
            or a symbolic mode. Defaults to None.
        recursive (bool, optional): Whether to apply ``mode``, ``user``
            and ``group`` recursively to the directory's contents.
            Defaults to False.
        force (bool, optional): Replace an existing non-directory at
            ``path`` (``rm -rf``) instead of raising. Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The directory commands, only when the directory differs
            from the request.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, TypeError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_op_directory.py``.
    """

    # baseline ``files.directory`` parity. The parameter is named ``path``
    # exactly as upstream spells it (``files.file`` and ``files.link`` spell
    # their own ``path`` too). Reads the ``Directory`` fact
    # once and yields only on a difference: ``mkdir -p`` when the path is
    # absent, ``chmod``/``chown`` when the requested mode or owner differs
    # from the fact's metadata,
    # and ``rm -rf`` (the shared ``RemoveTreeCommand``) when
    # ``present=False``. Like the baseline's ``recursive`` contract, mode is only
    # applied recursively when the base directory's own mode does not
    # match, and user/group only when the base directory misses either
    # owner -- the ``-R`` flag then covers the whole tree. Symbolic modes
    # never equal the fact's octal int, so they re-run the ``chmod`` on
    # every materialization, the baseline's behaviour kept. The commands touch
    # arbitrary paths, so for root-owned locations flag the request
    # ``sudo=True``.
    #
    # The baseline's ``force_backup``/``force_backup_dir`` are deliberately
    # dropped (developer decision): the backup form ``mv``s the invalid
    # path to a name built from ``datetime.now()``, and a timestamp inside
    # a yielded command would differ between the prepare and apply phases,
    # breaking the request-sequence determinism the driver guards.
    # ``force=True`` therefore always removes the non-directory first
    # (``rm -rf``, exactly the baseline's ``force`` with ``force_backup=False``),
    # and ``force=False`` raises ``ValueError`` (the baseline's
    # ``OperationError``). The baseline's internal ``_no_check_owner_mode`` and
    # ``_no_fail_on_link`` knobs are deliberately dropped too; ``**kwargs``
    # absorbs any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    try:
        path = os.fspath(path)
    except TypeError:
        raise TypeError(
            "`path` must be a string or `os.PathLike` object"
        ) from None

    mode = _ensure_mode_int(mode)
    info = await ctx.facts.get(Directory, path)

    reasons: list[str] = []
    changed = False

    if info is False:  # the path exists but is not a directory
        if not force:
            raise ValueError(f"{path} exists and is not a directory")
        changed = True
        reasons.append(f"{path} is not a directory, will remove first")
        yield RemoveTreeCommand(src=path)
        info = None

    if not present:
        if info:
            changed = True
            reasons.append(f"{path} present, will remove")
            yield RemoveTreeCommand(src=path)
        else:
            reasons.append(f"directory {path} does not exist")
        ctx.changed = changed
        ctx.reason = "; ".join(reasons)
        return

    if info is None:  # create
        changed = True
        reasons.append(f"{path} missing, will create")
        yield MkdirCommand(path=path)
        if mode:
            yield ChmodCommand(path=path, mode=mode, recursive=recursive)
        if user or group:
            yield ChownCommand(
                path=path, user=user, group=group, recursive=recursive
            )
        ctx.changed = changed
        ctx.reason = "; ".join(reasons)
        return

    # update: compare the requested mode/owner against the fact's metadata
    if mode and info["mode"] != mode:
        changed = True
        reasons.append(f"mode {info['mode']} differs, will chmod {mode}")
        yield ChmodCommand(path=path, mode=mode, recursive=recursive)

    if (user and info["user"] != user) or (group and info["group"] != group):
        changed = True
        reasons.append(f"owner {info['user']}:{info['group']} differs, will chown")
        yield ChownCommand(path=path, user=user, group=group, recursive=recursive)

    if not changed:
        reasons.append(f"directory {path} already exists")

    ctx.changed = changed
    ctx.reason = "; ".join(reasons)


@dataclass(frozen=True)
class ChattrCommand(Command):
    """Set (``+``) or clear (``-``) ext2-family attribute flags (``chattr``).

    One present-flagged descriptor covers both directions, mirroring
    ``server.ModprobeCommand``: the flags render as a single comma-joined
    ``+a,i``-style spec in the requested order, and only the path is
    shell-quoted -- the spec carries no character ``shlex`` would mangle,
    exactly as ``ChmodCommand`` renders its mode bare.
    """

    path: str
    flags: tuple[str, ...]
    present: bool = True

    def shell(self) -> str:
        prefix = "+" if self.present else "-"
        spec = f"{prefix}{','.join(self.flags)}"
        return f"chattr {spec} {shlex.quote(self.path)}"


async def flags(
    ctx: OpContext,
    path: str,
    flags: str | list[str] | tuple[str, ...] | None = None,
    present: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Set or clear a path's ext2-family attribute flags.

    Args:
        ctx (OpContext): Execution context for the operation.
        path (str): The remote path whose attribute flags to manage.
        flags (str | list[str] | tuple[str, ...] | None, optional): The
            one-letter ext2 attribute flags to set or clear (``"i"``
            immutable, ``"a"`` append-only, ...); a bare string names one
            flag. Prefer a tuple when building requests via ``op()``,
            because request arguments must stay hashable. Defaults to None.
        present (bool, optional): Whether the flags should be set
            (``True``) or cleared (``False``). Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The ``chattr`` command, only when some requested flag
            differs from the current set.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, TypeError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_flags.py``.
    """

    # baseline ``files.flags`` parity, in the developer-chosen Linux
    # adaptation: the baseline's verb pair (``chflags`` over BSD ``stat -f %Sf``
    # flag names like ``hidden``) does not exist on Linux, so the same
    # set/clear capability is expressed over ``lsattr -d`` letters (the
    # ``FileFlags`` fact) and ``chattr +flag``/``-flag``. The idempotence
    # logic is kept verbatim: requested flags minus the current set decide
    # a set, the current set intersected with the requested flags decide a
    # clear, and anything else is a no-op whose reason mirrors the baseline's
    # noop text (``already has 'a,b' set``/``clear``) over the sorted
    # requested spelling, so plan and apply phase report identically.
    # The baseline accepts a scalar ``flags`` string as its own spelling of the
    # one-flag list (``flags="hidden"``), kept here as the same
    # normalisation; empty or absent flags are the baseline's empty-list noop
    # branch. A list stays valid for direct generator calls but ``op()``
    # rejects it (UnhashableArgumentError), hence the tuple preference.
    # ``chattr`` addresses only the ``_LINUX_FLAG_LETTERS`` vocabulary -- a
    # BSD flag name or a signed letter (``"+i"``) is rejected with
    # ``ValueError`` rather than reaching the host, and ``e`` (extent) is
    # left unfiltered: it is readable ext4 state ``chattr`` refuses to
    # change, so requesting it plans a command that fails at apply.
    # An absent path reads no flags (``FileFlags`` answers ``[]``), so the
    # request plans a ``chattr`` that fails at apply -- the baseline's Flags
    # answers ``[]`` for absence identically. Setting or clearing
    # attributes needs root in practice, so for the common protected paths
    # flag the request ``sudo=True``. ``**kwargs`` absorbs any further global
    # arguments (``op()`` already strips ``name``/``sudo``/``su``).
    if flags is None:
        requested: tuple[str, ...] = ()
    elif isinstance(flags, str):
        requested = (flags,)
    elif isinstance(flags, (list, tuple)):
        requested = tuple(flags)
    else:
        raise TypeError("`flags` must be a string or a tuple/list of strings")
    for flag in requested:
        if len(flag) != 1 or flag not in _LINUX_FLAG_LETTERS:
            raise ValueError(
                f"invalid ext2 attribute flag {flag!r}: each flag must be one "
                f"letter of {_LINUX_FLAG_LETTERS}"
            )

    if not requested:
        ctx.reason = f"no changes requested to flags for '{path}'"
        return

    current = set(await ctx.facts.get(FileFlags, path))
    requested_set = set(requested)
    to_change = (
        requested_set - current if present else current & requested_set
    )

    if not to_change:
        verb = "set" if present else "clear"
        ctx.reason = f"'{path}' already has '{','.join(sorted(requested))}' {verb}"
        return

    ctx.changed = True
    ctx.reason = (
        f"flags {','.join(sorted(to_change))} "
        f"{'not set' if present else 'set'}, will "
        f"{'set' if present else 'clear'} on {path}"
    )
    # Sorted so the spec, the request hash and both phases' logs agree
    # regardless of the requested order (set iteration order is not one).
    yield ChattrCommand(path=path, flags=tuple(sorted(to_change)), present=present)


__all__ = [
    "ChattrCommand",
    "ChmodCommand",
    "ChownCommand",
    "CopyCommand",
    "Directory",
    "DirectoryEntries",
    "DirectoryEntry",
    "File",
    "FileCommand",
    "FileContents",
    "FileExists",
    "FileFlags",
    "FileLink",
    "FindDirectories",
    "FindFiles",
    "FindFilesInfo",
    "FindInFile",
    "FindLinks",
    "FoundFile",
    "GetCommand",
    "InstallCommand",
    "InstallSpoolCommand",
    "Link",
    "LinkCommand",
    "Md5File",
    "MkdirCommand",
    "MoveCommand",
    "PutCommand",
    "RemoteFileSha1",
    "RemoveTreeCommand",
    "SameFile",
    "Sha256File",
    "Sha384File",
    "Socket",
    "copy",
    "directory",
    "edit",
    "file",
    "flags",
    "get",
    "install",
    "link",
    "move",
    "put",
    "read_source_bytes",
    "write_dest_bytes",
]
