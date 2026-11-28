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
# originals ``FindInFiles`` (a recursive ``grep -rlE`` returning the paths of
# the files whose content matches), ``FindFilesInfo`` (find -printf metadata
# per hit) and ``DirectoryEntries`` (a shallow name/type listing). The digest
# side adds
# ``Sha256File``/``Sha384File``/``Md5File`` beside ``RemoteFileSha1``. Like
# every fact they are phase-unaware: each ``process`` coroutine just gathers
# the raw value.
#
# Operation side (design doc section 6): ``files.link``, ``files.file``,
# ``files.put``, ``files.get``, ``files.edit``, ``files.install``,
# ``files.move``, ``files.copy``, ``files.unarchive``,
# ``files.directory`` and ``files.flags`` plus the typed command descriptors
# they yield (``LinkCommand``/``FileCommand``/``PutCommand``/``GetCommand``/
# ``InstallSpoolCommand``/``InstallCommand``/
# ``MoveCommand``/``RemoveTreeCommand``/``CopyCommand``/``MkdirCommand``/
# ``ChmodCommand``/``ChownCommand``/``ChattrCommand``/
# ``UnarchiveSpoolCommand``/``UnarchiveCommand``). Operations
# are async generators that receive an ``OpContext``, await facts, set
# ``ctx.changed``/``ctx.reason``, and yield command descriptors; they never
# touch the host directly. ``PutCommand`` and ``GetCommand`` are the *non-shell*
# descriptors: they transfer bytes over the host's asyncssh connection (scp)
# instead of running shell text -- ``PutCommand`` uploads an IO-like source to a
# remote path, ``GetCommand`` downloads a remote path into an IO-like
# destination.
# """

import asyncio
import base64
import hashlib
import io
import os
import posixpath
import re
import secrets
import shlex
import stat
import urllib.parse
import urllib.request
from collections.abc import AsyncGenerator, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import IO, Any, Literal, TypedDict

from ...commands import Command, TransferCommand
from ...core import OpContext
from ...hosts import Host

# --- Facts -------------------------------------------------------------------


class _HiddenPath:
    """Sentinel type: absence an unprivileged probe has no right to report."""


#: The single sentinel instance the two-tier facts' first-tier reads return when
#: a path came back absent but the absence was never observable: a directory
#: above the path denies the login user traversal, so ``test -e``, ``cat`` and
#: ``stat`` fail with ``EACCES`` and the shell squashes that into a plain "does
#: not exist". ``process`` turns it into one elevated re-probe (issue #70). A
#: private object, so no parsed value can ever compare equal to it.
_HIDDEN = _HiddenPath()

#: Marker the traversal probe prints when the nearest existing directory above a
#: path denies the login user traversal, which is what makes a probe of the path
#: itself meaningless. Printed on stdout only; the probe always exits zero.
_PATH_HIDDEN = "#reemote-path-hidden"


def _ancestor_dirs(path: str) -> list[str]:
    """The path's directories, nearest first: ``/a/b/c`` -> ``["/a/b", "/a", "/"]``.

    ``posixpath``, not ``os.path``: the remote is always *nix while the
    controller might be Windows (the operations' own reasoning). The walk stops
    at the filesystem root and at the two non-slash anchors a caller-supplied
    path can carry (``~``, ``.``), so a bare relative name contributes no
    directory to ask about at all.
    """
    dirs: list[str] = []
    directory = posixpath.dirname(path)
    while directory and directory not in dirs:
        dirs.append(directory)
        if directory in ("/", "~", "."):
            break
        directory = posixpath.dirname(directory)
    return dirs


def _quote_ancestor(directory: str) -> str:
    """Quote one ancestor for the probe, leaving a leading ``~`` for the shell.

    The ``File`` family's tilde rule: ``~`` and a ``~/`` prefix stay unquoted so
    the shell expands them into the login user's home, while a later segment is
    quoted normally.
    """
    if directory == "~":
        return "~"
    if directory.startswith("~/"):
        return "~/" + shlex.quote(directory[2:])
    return shlex.quote(directory)


def _hidden_parent_command(path: str) -> str:
    """Shell printing ``_PATH_HIDDEN`` when a directory above ``path`` hides it.

    The ancestors are chained ``if``/``elif``, so the search stops at the first
    directory the login user can ``test -e``: reaching *it* needed only its own
    parent's traversal, and a traversable one makes everything below it
    nameable, so a deeper miss is genuine rather than hidden. Only that nearest
    existing directory's ``-x`` is consulted -- on a directory the execute bit
    *is* the search permission. The probe is never elevated (the question is
    what the login user can see), so it needs no ``sh -c`` wrapper, and each
    branch ends in a successful ``[ -x ]``/``printf``, so it exits zero.
    """
    dirs = _ancestor_dirs(path)
    if not dirs:
        # Nothing above the path to blame: a bare relative name is exactly as
        # visible as the working directory allows, which the probe cannot judge.
        return "true"
    branches: list[str] = []
    for index, directory in enumerate(dirs):
        quoted = _quote_ancestor(directory)
        opener = "if" if index == 0 else "elif"
        branches.append(
            f'{opener} [ -e {quoted} ]; then [ -x {quoted} ] '
            f'|| printf "{_PATH_HIDDEN}\\n";'
        )
    return " ".join(branches) + " fi"


async def _path_hidden_from_login(host: Host, path: str) -> bool:
    """Whether the login user's probe cannot see an entry at ``path`` at all.

    The gate in front of every elevated re-probe: an absent answer is only
    trusted when the login user was actually in a position to observe it.
    """
    visible = getattr(host, "login_path_visible", None)
    if visible is not None:
        # SimulatedHost seam: the directory-mode model answers the question
        # offline (seam-first, so the simulated shell never sees a probe).
        return not visible(path)
    # Real transport: one unprivileged compound per absent answer.
    stdout = await host.run_shell(_hidden_parent_command(path))
    return _PATH_HIDDEN in stdout.splitlines()


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

    A path the login user cannot see -- because a directory above it denies
    traversal -- is re-probed elevated with ``sudo``, so a hidden file is never
    reported as missing; the run-as user and any password come from the
    inventory ``privilege`` data, never from an operation.

    **Commands:**

    ```bash
    test -e <path> && echo yes
    if [ -e <dir> ]; then [ -x <dir> ] || printf "#reemote-path-hidden\\n"; elif [ -e <parent> ]; then [ -x <parent> ] || printf "#reemote-path-hidden\\n"; fi
    ```

    The second command runs only when the first stayed silent, and walks the
    path's directories outward to the nearest existing one.

    **Returns:**

    ```python
    bool
    ```

    **Raises:**

    ```python
    HostConnectionError, PrivilegeError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_file.py``.
    """

    # Mirrors the baseline's ``files.FileExists``: the bare ``test -e`` probe,
    # whose non-zero exit ``run_shell`` leaves unraised and whose empty stdout
    # reads as False. Like the ``File`` family (and for the same reason, issue
    # #70), a silent probe is re-asked elevated when a directory above the path
    # denies the login user traversal -- ``test -e`` reports that ``EACCES`` as
    # "no such file", which would otherwise re-fire every gated create. The
    # probe is never elevated, so it needs no ``sh -c`` wrapper; the elevated
    # ``test -e`` does, and masks its own exit status (``|| true``, the hash
    # family's idiom) because ``run_shell_privileged`` raises on a non-zero.

    @classmethod
    def command(cls, path: str) -> str:
        """Shell printing ``yes`` only if ``path`` is visible to the caller."""
        return f"test -e {shlex.quote(path)} && echo yes"

    @classmethod
    async def process(cls, host: Host, path: str) -> bool:
        answer = await cls._read(host, path, privileged=False)
        if answer is _HIDDEN:
            # The probe was refused, not answered: ask again as root, whose
            # traversal of any directory is unrestricted (issue #70).
            answer = await cls._read(host, path, privileged=True)
        return answer is True

    @classmethod
    async def _read(
        cls, host: Host, path: str, *, privileged: bool
    ) -> bool | _HiddenPath:
        existing = getattr(host, "existing_files", None)
        if existing is not None:
            # SimulatedHost seam: read the in-memory state directly. A path no
            # store marks is absent for root too, so only a present row needs
            # the traversal model at all.
            if path not in existing:
                return False
            if not privileged and await _path_hidden_from_login(host, path):
                return _HIDDEN
            return True
        # Real transport: `test -e` over asyncssh. A missing file exits
        # non-zero with empty stdout (check is off in run_shell), which maps
        # to False just like the simulated absent-file case.
        command = cls.command(path)
        if privileged:
            stdout = await host.run_shell_privileged(
                f"sh -c {shlex.quote(f'{command} || true')}", sudo=True
            )
        else:
            stdout = await host.run_shell(command)
        if stdout.strip() == "yes":
            return True
        if not privileged and await _path_hidden_from_login(host, path):
            return _HIDDEN
        return False


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


class _UnreadableFile:
    """Sentinel type: a file that exists but the login user cannot read."""


#: The single sentinel instance ``FileContents._read`` returns for a present
#: but unreadable file; ``process`` re-reads it elevated. A private object, so
#: no content string can ever compare equal to it.
_UNREADABLE = _UnreadableFile()


class FileContents:
    """The text content of a file on the remote host, or None when absent.

    A file the login user cannot read (eg a ``0640`` service-account config)
    is re-read elevated with ``sudo``, so restrictive modes keep content
    idempotence; the run-as user and any password come from the inventory
    ``privilege`` data, never from an operation. A file the login user cannot
    even *see* -- because a directory above it denies traversal -- is
    re-probed the same way, so an ``EACCES`` that the shell reports as "no such
    file" never reads as absence.

    **Commands:**

    ```bash
    sh -c 'test -e <path> && { printf "#reemote-filecontents\\n"; cat -- <path> || printf "#reemote-filecontents-unreadable\\n"; } || true'
    if [ -e <dir> ]; then [ -x <dir> ] || printf "#reemote-path-hidden\\n"; elif [ -e <parent> ]; then [ -x <parent> ] || printf "#reemote-path-hidden\\n"; fi
    ```

    The second command runs only when the first reported absence, and walks the
    path's directories outward to the nearest existing one.

    **Returns:**

    ```python
    str | None
    ```

    **Raises:**

    ```python
    CommandError, CommandNotFoundError, HostConnectionError, PrivilegeError
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
    #
    # The read is two-tier: the first cat rides unprivileged, and a cat that
    # fails (permission denied on a service-owned ``0640`` file) prints the
    # ``UNREADABLE`` marker instead, which ``process`` turns into one elevated
    # re-read -- the fallback that keeps ``files.install``'s content idempotence
    # honest for files the login user cannot read, without changing any
    # operation's signature. A readable file whose content is exactly the
    # ``UNREADABLE`` marker line also triggers the elevated re-read, which
    # returns the same content -- a spurious elevation, never a wrong answer.
    #
    # The absent arm gets the same treatment (issue #70): a file inside a
    # ``0770``/``02770`` service directory is invisible to the unprivileged
    # ``test -e``, which reports ``EACCES`` as "no such file", so an absent
    # answer is only trusted behind the traversal gate. Both tiers end in
    # ``|| true`` so the elevated probe of a genuinely missing file exits zero
    # instead of raising ``CommandError`` (the hash family's idiom), and the
    # marker is detected on whichever tier printed it -- a run-as user who is
    # not root can be refused the elevated read too, and the fact's contract is
    # then content-or-None.

    #: Marker printed before the ``cat``'d content, only when ``test -e``
    #: passes, so ``process`` can tell an existing (possibly empty) file from
    #: a missing one (no marker on stdout).
    MARKER = "#reemote-filecontents"

    #: Marker the ``cat``'s failure arm prints when the file exists but the
    #: login user cannot read it: stdout is then exactly the two marker lines,
    #: which ``process`` detects to trigger the elevated re-read.
    UNREADABLE = "#reemote-filecontents-unreadable"

    @classmethod
    def command(cls, path: str) -> str:
        """Shell printing ``MARKER`` then the file's content, only if it exists.

        The ``test -e`` guard means a missing file produces no output at all
        (not even the marker), while an existing one -- empty or not -- always
        leads with the marker line. A ``cat`` that fails (the login user
        cannot read the file) prints ``UNREADABLE`` instead of content, so an
        unreadable file stays distinguishable from an empty one. The trailing
        ``|| true`` keeps the whole compound exit-zero, so the elevated tier's
        ``run_shell_privileged`` reports a missing file as absence rather than
        raising ``CommandError`` on the guard's failing ``test -e``.

        The read-back is carried by an explicit ``sh -c`` composite (the
        firewalld fact idiom). ``process`` re-reads an unreadable file through
        ``run_shell_privileged(..., sudo=True)``, and ``sudo``/``doas`` exec
        their command directly rather than through a shell, so a bare
        ``test -e ... && { ...; }`` would elevate only the leading ``test`` and
        run the ``cat`` -- the entire point of the fallback -- as the login
        user, failing on exactly the file the tier exists to read. Wrapping the
        compound in ``sh -c`` makes it one simple command, so the elevated tier
        reads it as root. The ``printf`` formats are double-quoted so the
        wrapped composite stays free of nested single-quote escaping.
        """
        quoted = shlex.quote(path)
        inner = (
            f'test -e {quoted} && {{ printf "{cls.MARKER}\\n"; '
            f'cat -- {quoted} || printf "{cls.UNREADABLE}\\n"; }} || true'
        )
        return f"sh -c {shlex.quote(inner)}"

    @classmethod
    async def process(cls, host: Host, path: str) -> str | None:
        content = await cls._read(host, path, privileged=False)
        if content is _UNREADABLE:
            # Present but unreadable as the login user: re-read elevated. The
            # run-as user and any password come from the inventory privilege
            # data (run_shell_privileged), never from an operation.
            content = await cls._read(host, path, privileged=True)
        elif content is None and await _path_hidden_from_login(host, path):
            # Absent, but never observable as the login user: a directory above
            # the path denies traversal, so the probe's ``test -e`` failed with
            # ``EACCES`` and the shell reported that as "no such file". Only a
            # negative *elevated* read is trusted as absence (issue #70).
            content = await cls._read(host, path, privileged=True)
        if content is _UNREADABLE:
            # The elevated tier can print the marker too -- a run-as user who is
            # not root is refused exactly what the login user was. The fact
            # returns content or None, so an unreadable file reports as absent
            # rather than leaking the sentinel to callers.
            content = None
        return content

    @classmethod
    async def _read(
        cls, host: Host, path: str, *, privileged: bool
    ) -> str | None | _UnreadableFile:
        remote_files = getattr(host, "remote_files", None)
        if remote_files is not None:
            # SimulatedHost seam: decode the content the simulated remote
            # filesystem holds at the path (None when absent). The same store
            # backs files.put/files.get and RemoteFileSha1, so a simulated
            # upload -- which stores its bytes here -- reads back through this
            # fact, and an ``edit`` sees the content a prior ``edit`` wrote.
            if not privileged and await _path_hidden_from_login(host, path):
                # The seam models traversal denial too: an entry inside a
                # directory the login user cannot search is the one answer the
                # real probe can give, and it is "no output at all" -- so the
                # seam withholds the content and ``process``'s elevated tier
                # reads the same store as root.
                return None
            data = remote_files.get(path)
            if data is None:
                return None
            if not privileged:
                # The seam models the permission check too: a login user
                # without read access gets the same sentinel the real
                # transport's failed cat produces, so the elevated fallback
                # runs offline exactly as it does over SSH.
                can_read = getattr(host, "login_can_read", None)
                if can_read is not None and not can_read(path):
                    return _UNREADABLE
            return data.decode("utf-8")
        # Real transport: cat the file over asyncssh behind the marker, so a
        # missing file (no marker) reads as None rather than empty content.
        command = cls.command(path)
        if privileged:
            stdout = await host.run_shell_privileged(command, sudo=True)
        else:
            stdout = await host.run_shell(command)
        prefix = f"{cls.MARKER}\n"
        if not stdout.startswith(prefix):
            return None
        # The marker is detected on either tier: the compound prints it under
        # ``sudo`` too when the run-as user cannot read the file either.
        if stdout == prefix + f"{cls.UNREADABLE}\n":
            return _UNREADABLE
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

#: The keyed ``stat`` format whose rows ``File.parse`` matches: GNU
#: ``stat -c`` prints the ``user=... group=...`` shape this regex parses.
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

    A path the login user cannot see -- because a directory above it denies
    traversal -- is re-probed elevated with ``sudo``, so an ``EACCES`` that the
    shell reports as "no such file" never reads as absence; the run-as user and
    any password come from the inventory ``privilege`` data, never from an
    operation.

    **Commands:**

    ```bash
    ! (test -e <path> || test -L <path> ) || ( stat -c 'user=%U group=%G mode=%A atime=%X mtime=%Y ctime=%Z size=%s %N' <path> 2> /dev/null || ls -ld <path> )
    if [ -e <dir> ]; then [ -x <dir> ] || printf "#reemote-path-hidden\\n"; elif [ -e <parent> ]; then [ -x <parent> ] || printf "#reemote-path-hidden\\n"; fi
    ```

    The second command runs only when the first produced no row, and walks the
    path's directories outward to the nearest existing one.

    **Returns:**

    ```python
    dict[str, Any] | Literal[False] | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, PrivilegeError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_file.py``.
    """

    # Mirrors the baseline's ``files.File``: the path is guarded with
    # ``test -e``/``test -L`` (so a dangling symlink is still probed), then
    # described by GNU ``stat -c``, falling back to ``ls -ld``. The parsed
    # result is a dict, for example::
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
    #
    # Unlike the baseline, the read is two-tier: an empty answer is re-probed
    # elevated behind the traversal gate, because ``test -e`` cannot tell "absent"
    # from "hidden by a parent directory the login user may not search" (issue
    # #70, the sibling of ``FileContents``' unreadable fallback). The gate costs
    # one extra unprivileged compound per missing path and never changes the
    # baseline's own command, which stays verbatim for parity.

    #: The baseline's ``type`` field: only a regular file is accepted; every other
    #: path type (directory, link, socket, ...) reads as False.
    TYPE = "file"

    #: The ``SimulatedHost`` store backing the seam read. Each sibling points at
    #: its own typed store, because one undiscriminated metadata dict cannot
    #: answer two different type questions for the same path.
    _SEAM = "file_stats"

    @classmethod
    def command(cls, path: str) -> str:
        """Shell printing the stat (or ls) description of ``path``, if it exists.

        The ``test -e || test -L`` guard means a missing path produces no
        output at all; ``test -L`` additionally lets a dangling symlink
        through to ``ls -ld`` (``stat`` follows the link and fails), which
        reports it as a non-file.
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
            f"ls -ld {quoted} )"
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
        value = await cls._read(host, path, privileged=False)
        if value is _HIDDEN:
            # Not proven absent: a directory above the path denies the login user
            # traversal, so the guard's ``test -e`` failed with ``EACCES`` and
            # printed nothing at all. Only the elevated probe can answer, and it
            # reads the path as root (issue #70).
            value = await cls._read(host, path, privileged=True)
        return value

    @classmethod
    async def _read(
        cls, host: Host, path: str, *, privileged: bool
    ) -> dict[str, Any] | Literal[False] | None | _HiddenPath:
        # The sentinel is a first-tier answer only: the traversal gate asks what
        # the *login user* can see, which is settled once and never re-asked of
        # the elevated tier.
        stats = getattr(host, cls._SEAM, None)
        if stats is not None:
            # SimulatedHost seam: metadata keyed by the caller-supplied path
            # string (any ~/ prefix included, since the shell resolves it).
            # Return a copy so callers never mutate simulated state; the
            # False (non-file) and None (absent) answers pass through.
            value = stats.get(path)
            result = dict(value) if isinstance(value, dict) else value
            if (
                result is not None
                and not privileged
                and await _path_hidden_from_login(host, path)
            ):
                # The seam models traversal denial too: the row exists, but the
                # login user's probe could never have reached it, so the
                # unprivileged tier withholds it and the elevated tier reads the
                # same store as root.
                return _HIDDEN
            return result
        command = cls.command(path)
        if privileged:
            # ``sudo`` execs its argument directly rather than through a shell,
            # so the compound chain needs the ``sh -c`` wrapper to be elevated
            # whole (the ``FileContents`` precedent). The chain always exits
            # zero -- the leading ``!`` guard short-circuits on a missing path,
            # and ``stat`` falls back to ``ls`` -- so an elevated probe of a
            # genuinely absent path reads as absent instead of raising
            # ``CommandError``.
            stdout = await host.run_shell_privileged(
                f"sh -c {shlex.quote(command)}", sudo=True
            )
        else:
            # Real transport: stat/ls over asyncssh. A missing path produces
            # empty stdout (the guard short-circuits), which parses to None.
            stdout = await host.run_shell(command)
        parsed = cls.parse(stdout.splitlines())
        if (
            parsed is None
            and not privileged
            and await _path_hidden_from_login(host, path)
        ):
            # No row is either absence or invisibility; the gate says which
            # before the answer is trusted.
            return _HIDDEN
        return parsed


class Directory(File):
    """Metadata for a directory, False for a non-directory path, None if absent.

    Inherited from ``File`` unchanged -- including the elevated re-probe for a
    path a non-traversable directory above it hides.

    **Commands:**

    ```bash
    ! (test -e <path> || test -L <path> ) || ( stat -c 'user=%U group=%G mode=%A atime=%X mtime=%Y ctime=%Z size=%s %N' <path> 2> /dev/null || ls -ld <path> )
    if [ -e <dir> ]; then [ -x <dir> ] || printf "#reemote-path-hidden\\n"; elif [ -e <parent> ]; then [ -x <parent> ] || printf "#reemote-path-hidden\\n"; fi
    ```

    **Returns:**

    ```python
    dict[str, Any] | Literal[False] | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, PrivilegeError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_directory.py``.
    """

    # Mirrors the baseline's ``files.Directory`` -- its ``File`` subclass with
    # ``type = "directory"``: the same ``test -e``/``test -L`` guard and
    # stat/ls chain (``command``, ``parse`` and the two-tier ``process`` are
    # inherited), but the type flag must be ``d``, so a regular file, symlink,
    # socket, ... reads as ``False`` instead. The dict shape, *naive UTC*
    # timestamps and octal-looking ``mode`` int are exactly ``File``'s, and a
    # leading ``~/`` likewise stays unquoted for the remote shell to expand.
    # This fact describes the directory *itself*, never the names inside it --
    # for the listing see ``DirectoryEntries`` (shallow, with types) or
    # ``FindDirectories`` (recursive paths).

    #: The baseline's ``type`` field: only a directory is accepted; every other
    #: path type (file, link, socket, ...) reads as False.
    TYPE = "directory"

    # The seam override is all: a metadata dict carries no type flag, so
    # ``file_stats`` alone cannot say whether its path is a directory, and the
    # two-tier read is inherited from ``File``.
    _SEAM = "directory_stats"


class Link(File):
    """Metadata for a symlink plus its target, False for non-link, None if absent.

    Inherited from ``File`` unchanged -- including the elevated re-probe for a
    link a non-traversable directory above it hides.

    **Commands:**

    ```bash
    ! (test -e <path> || test -L <path> ) || ( stat -c 'user=%U group=%G mode=%A atime=%X mtime=%Y ctime=%Z size=%s %N' <path> 2> /dev/null || ls -ld <path> )
    if [ -e <dir> ]; then [ -x <dir> ] || printf "#reemote-path-hidden\\n"; elif [ -e <parent> ]; then [ -x <parent> ] || printf "#reemote-path-hidden\\n"; fi
    ```

    **Returns:**

    ```python
    dict[str, Any] | Literal[False] | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, PrivilegeError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_link.py``.
    """

    # Mirrors the baseline's ``files.Link`` -- its ``File`` subclass with
    # ``type = "link"``: the same ``test -e``/``test -L`` guard and stat/ls
    # chain (``command``, ``parse`` and the two-tier ``process`` are inherited),
    # but the type flag must be ``l``, so a regular file, directory, socket,
    # ... reads as ``False`` instead. The dict is ``File``'s shape plus a
    # ``link_target`` key -- the target path with the stat/ls quoting stripped,
    # exactly as the link records it (a relative target stays relative), for
    # example::
    #
    #     {"user": "reemote", "group": "reemote", "link_target": "/path/to/link/target"}
    #
    # The ``test -L`` guard lets a *dangling* symlink through to the
    # ``ls -ld`` fallback (``stat`` follows the link and fails), so
    # a broken link is still reported with its target. ``files.link``
    # consumes this stat-level read for its idempotence (the target alone is
    # not enough to tell "correct" from "wrong target" from "not a link");
    # ``FileLink`` is the cheaper
    # ``readlink`` probe for the callers that only want the target.

    #: The baseline's ``type`` field: only a symlink is accepted; every other
    #: path type (file, directory, socket, ...) reads as False.
    TYPE = "link"

    # The seam override is all: a metadata dict carries no type flag, so
    # ``file_stats`` alone cannot say whether its path is a symlink, and the
    # two-tier read is inherited from ``File``.
    _SEAM = "link_stats"


class Socket(File):
    """Metadata for a socket, False for a non-socket path, None if absent.

    Inherited from ``File`` unchanged -- including the elevated re-probe for a
    socket a non-traversable directory above it hides.

    **Commands:**

    ```bash
    ! (test -e <path> || test -L <path> ) || ( stat -c 'user=%U group=%G mode=%A atime=%X mtime=%Y ctime=%Z size=%s %N' <path> 2> /dev/null || ls -ld <path> )
    if [ -e <dir> ]; then [ -x <dir> ] || printf "#reemote-path-hidden\\n"; elif [ -e <parent> ]; then [ -x <parent> ] || printf "#reemote-path-hidden\\n"; fi
    ```

    **Returns:**

    ```python
    dict[str, Any] | Literal[False] | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, PrivilegeError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_socket.py``.
    """

    # Mirrors the baseline's ``files.Socket`` -- its ``File`` subclass with
    # ``type = "socket"``: pure inheritance, the same ``test -e``/``test -L``
    # guard and stat/ls chain (``command``, ``parse`` and the two-tier
    # ``process`` are inherited unchanged -- the shared parse already maps the
    # ``s`` type flag), so a regular file, directory, symlink, ... reads as
    # ``False`` instead. The dict shape, *naive UTC* timestamps and
    # octal-looking ``mode`` int are exactly ``File``'s, and a leading ``~/``
    # likewise stays unquoted for the remote shell to expand. The one override
    # is the ``socket_stats`` seam: ``file_stats`` stores undiscriminated
    # metadata payloads and can never answer the socket-typed question for the
    # same path (the Directory/Link precedent). Use it to audit a daemon's
    # listening socket -- ``/var/run/docker.sock``, a database or agent IPC
    # socket -- owner and mode decide which users may talk to the daemon.

    #: The baseline's ``type`` field: only a socket is accepted; every other
    #: path type (file, directory, link, ...) reads as False.
    TYPE = "socket"

    # The seam override is all: a metadata dict carries no type flag, so
    # ``file_stats`` alone cannot say whether its path is a socket, and the
    # two-tier read is inherited from ``File``.
    _SEAM = "socket_stats"


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


class FindInFiles:
    """Paths of the files under a start path whose content matches a pattern.

    **Commands:**

    ```bash
    grep -rlE <pattern> <path> || true
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
        Examples can be found in ``examples/modules/files/files_find_in_files.py``.
    """

    # A reemote ORIGINAL closing a genuine gap the baseline's find-path
    # family and the sibling ``FindInFile`` leave open: ``FindFiles``/
    # ``FindDirectories``/``FindLinks`` select on *name* metadata and
    # ``FindInFile`` greps the content of *one* known file, so neither can
    # answer "which files under this tree contain this content?" -- the very
    # common "delete unmanaged files matching content" idiom Ansible roles
    # lean on (an APCu/OpCache stale-cache sweep, a legacy-endpoint config
    # cleanup). One recursive ``grep -rlE`` returns just the matching *paths*
    # (``-l`` prints one path per matching file, never their lines), so a
    # whole tree is scanned in a single round-trip and the result feeds the
    # read side of the same gather-then-branch two-deploy cleanup
    # ``FindFiles`` does: gather the hits in one deploy, compute the closure
    # (found minus managed), and remove the surplus in the next.
    # ``-r`` recurses, ``-l`` lists files-with-matches and ``-E`` selects the
    # extended regex -- the spec's fixed spelling, so there is no BRE toggle
    # unlike ``FindInFile``. ``interpolate_variables`` mirrors ``FindInFile``:
    # it wraps the pattern in double quotes (embedded double quotes escaped)
    # so the remote shell expands ``$VAR`` references, otherwise the pattern
    # is single-quoted literally. ``|| true`` guards every failure mode to
    # [] -- no match (grep exits 1), an absent or unreadable path and a
    # missing grep binary all read as an empty list -- so unlike
    # ``FindInFile`` this never returns None: a recursive search over a tree
    # has no single "the file is missing" answer to distinguish. The
    # SimulatedHost seam is the dedicated ``grep_files`` store keyed by
    # ``(path, pattern)``, in the same style as ``FindInFile``'s
    # ``grep_lines``: grep's ERE semantics cannot be faithfully re-derived
    # from the ``remote_files`` content bytes with Python's ``re``, so a test
    # seeds the matching paths directly (an absent key reads as []).

    @classmethod
    def command(
        cls,
        path: str,
        pattern: str,
        *,
        interpolate_variables: bool = False,
    ) -> str:
        """Shell printing one path per file whose content matches, guarded."""
        if interpolate_variables:
            pattern_arg = '"{}"'.format(pattern.replace('"', '\\"'))
        else:
            pattern_arg = shlex.quote(pattern)
        quoted = shlex.quote(path)
        return f"grep -rlE {pattern_arg} {quoted} || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[str]:
        """The grep's stdout lines verbatim -- one path per matching file.

        grep's ``-l`` traversal order is kept as-is; no output reads as [].
        """
        return list(output)

    @classmethod
    async def process(
        cls,
        host: Host,
        path: str,
        pattern: str,
        *,
        interpolate_variables: bool = False,
    ) -> list[str]:
        matches = getattr(host, "grep_files", None)
        if matches is not None:
            # SimulatedHost seam: (path, pattern) -> the matching file paths a
            # test seeds; copy so callers never mutate simulated state, and an
            # absent key reads as [] (never None).
            value = matches.get((path, pattern))
            return list(value) if value is not None else []
        # Real transport: the guarded recursive grep over asyncssh.
        stdout = await host.run_shell(
            cls.command(path, pattern, interpolate_variables=interpolate_variables)
        )
        return cls.parse(stdout.splitlines())


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
        ```python
        from reemote import op, files

        # Create a symlink (default ``symbolic=True``) -> ln -s /usr/share/doc/nginx /etc/nginx/docs
        yield op(
            files.link,
            name="Expose the nginx docs",
            path="/etc/nginx/docs",
            target="/usr/share/doc/nginx",
            sudo=True,
        )

        # Create a hard link and set its owner -> ln + chown -h
        yield op(
            files.link,
            name="Hard-link the release into the cache",
            path="/var/cache/app.tar",
            target="/data/releases/app.tar",
            symbolic=False,
            user="www-data",
            group="www-data",
            sudo=True,
        )

        # Remove the link at ``path`` (``target`` is unused) -> rm -f
        yield op(
            files.link,
            name="Drop the stale symlink",
            path="/etc/nginx/docs",
            present=False,
            sudo=True,
        )
        ```

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
        ```python
        from reemote import op, files

        # Create a missing file with the given owner and mode (the parent
        # directory is ``mkdir -p``'d by default).
        yield op(
            files.file,
            name="Create the app log",
            path="/var/log/app/access.log",
            user="www-data",
            group="adm",
            mode="0640",
            sudo=True,
        )

        # Refresh an existing file's mtime -- ``touch=True`` is deliberately
        # non-idempotent and always re-runs.
        yield op(
            files.file,
            name="Refresh the deploy marker",
            path="/etc/deploy/marker",
            touch=True,
            sudo=True,
        )

        # Remove the file -> rm -f
        yield op(
            files.file,
            name="Drop the old default site",
            path="/etc/nginx/sites-enabled/default",
            present=False,
            sudo=True,
        )
        ```

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
        ```python
        from io import BytesIO

        from reemote import op, files

        # ``src`` is compared by identity across the two deploy phases, so
        # construct it once at module level -- never inside the generator.
        payload = BytesIO(b"max_connections = 200\\n")

        # Upload the stream to a login-writable path; the transfer rides
        # unprivileged scp (``sudo``/``su`` do not apply) and a repeat against
        # identical content is a no-op.
        yield op(
            files.put,
            name="Push the app config",
            src=payload,
            dest="/srv/app/config.ini",
        )
        ```

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
    # it and ``dest`` must be writable by that user (a root-owned target is
    # ``files.install``'s job -- it takes binary ``content`` as bytes and
    # spools-and-installs in one deploy -- or ``files.download``'s for a URL
    # source).
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
        ```python
        from io import BytesIO

        from reemote import op, files

        # ``dest`` must be the same writable stream across phases -- build it
        # once at module level, not inside the generator.
        backup = BytesIO()

        # Download a login-readable remote file into the buffer; the transfer
        # rides unprivileged scp and is sha1-gated, so a second ``get`` of the
        # same content is a no-op.
        yield op(
            files.get,
            name="Back up the current config",
            src="/srv/app/config.ini",
            dest=backup,
        )
        ```

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
        ```python
        from reemote import op, files

        # ``transform`` must be a stable, hashable callable -- define it at
        # module level, never as a lambda or a closure built inside the
        # deploy generator, so both phases compare equal.
        def set_max_connections(current: str | None) -> str:
            lines = (current or "").splitlines()
            for i, line in enumerate(lines):
                if line.strip().startswith("max_connections"):
                    lines[i] = "max_connections = 200"
                    break
            else:
                lines.append("max_connections = 200")
            return "\\n".join(lines) + "\\n"

        # Rewrite a login-writable file through the pure transform; yields an
        # scp ``PutCommand`` only when the transformed text differs.
        yield op(
            files.edit,
            name="Set max_connections",
            path="/srv/app/config.ini",
            transform=set_max_connections,
        )
        ```

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
class InstallBinarySpoolCommand(TransferCommand):
    """Spool ``files.install``'s binary content to a ``/tmp`` path on the target.

    The binary sibling of ``InstallSpoolCommand`` (issue #23) and a *non-shell*
    descriptor: it copies the content over the connection's scp channel via
    ``Host.upload_file`` rather than running shell text, so it has no
    ``shell()`` and its loggable form is the ``__str__`` rendering
    ``command_shell`` falls back to. The content is raw ``bytes`` -- hashable
    and equal across phases, the ``UnarchiveSpoolCommand`` pattern -- wrapped
    in a fresh ``io.BytesIO`` only at transfer time, so no identity-stable
    stream is needed at the call site. The copy rides as the login user
    (transfers are unprivileged, so the request's ``sudo`` does not apply to
    it), which is why the spool path is world-writable ``/tmp`` rather than
    the root-owned destination directory; the paired ``InstallCommand`` is
    the same elevated install the text path uses.
    """

    content: bytes  # the binary files.install writes (never decoded)
    dest: str  # spool file path on the target host (inside a sha1-derived /tmp dir)

    def __str__(self) -> str:
        return f"scp <files install> -> {self.dest}"

    async def transfer(self, host: Host) -> None:
        # scp does not create parent directories, and the spool lives inside
        # a sha1-derived /tmp directory, so mkdir -p it first -- unprivileged,
        # exactly as the text InstallSpoolCommand does.
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        await host.run_shell(f"mkdir -p {parent}")
        await host.upload_file(io.BytesIO(self.content), self.dest)


@dataclass(frozen=True)
class InstallValidateCommand(Command):
    """Validate the spooled file before the elevated install lands it.

    The optional middle step of ``files.install`` (and so of
    ``files.transform``, which delegates its write here): a plain shell
    descriptor yielded *between* the ``InstallSpoolCommand`` transfer and the
    ``InstallCommand`` install, running the caller's ``validate`` command
    against the just-spooled ``/tmp`` file. The driver runs every shell
    descriptor fail-fast, so a non-zero exit raises ``CommandError`` out of
    ``send_command`` and the ``InstallCommand`` that follows never runs -- the
    destination keeps whatever it held before, which is exactly the
    all-or-nothing guarantee Ansible's ``template``/``copy`` ``validate:``
    gives (``nginx -t -c %s`` on the staged file, a bad config never reaching
    ``/etc/nginx/nginx.conf``). Validating the spool rather than a separate
    copy is what makes it atomic: the bytes checked are the very bytes the
    install is about to ``cp`` into place, in one request and one deploy.

    The ``validate`` template's ``{spool}`` and ``{dest}`` placeholders are
    substituted with the shell-quoted spool and destination paths, so the
    caller writes them bare (``"nginx -t -c {spool}"``) and a destination
    carrying spaces or metacharacters still renders as one safe argument.
    ``{dest}`` is offered for a validator that must know the target name (a
    tool that infers its config type from the path) even though the file it
    reads is the spool. The command inherits the request's privilege flags --
    a ``sudo=True`` install runs the validation elevated, matching Ansible's
    become-scoped ``validate`` and letting a checker read every file the
    staged config references -- so it is a regular ``Command``, never a
    ``TransferCommand`` (which could not elevate).
    """

    validate: str  # the command template with {spool}/{dest} placeholders
    spool: str  # the /tmp spool file the content was staged at
    dest: str  # the install target, for the {dest} placeholder

    def shell(self) -> str:
        return self.validate.replace("{spool}", shlex.quote(self.spool)).replace(
            "{dest}", shlex.quote(self.dest)
        )


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
    content: str | bytes,
    dest: str,
    mode: str = "0644",
    user: str | None = None,
    group: str | None = None,
    validate: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Install text or binary content at a root-owned path via spool-and-install.

    Args:
        ctx (OpContext): Execution context for the operation.
        content (str | bytes): The desired content of the file: ``str`` for
            text (compared against ``FileContents``) or ``bytes`` for binary
            (compared against ``RemoteFileSha1``, no UTF-8 decode).
        dest (str): The remote path to install the content at.
        mode (str, optional): The installed file's permissions as
            octal-digit text (``"0644"``, ``"600"``). Defaults to "0644".
        user (str | None, optional): The user to own the installed file.
            Defaults to None (root, the elevated install's owner).
        group (str | None, optional): The group to own the installed file.
            Defaults to None (root).
        validate (str | None, optional): A command template run on the spooled
            ``/tmp`` file between the transfer and the install; a non-zero exit
            aborts the install and leaves the destination untouched. The
            ``{spool}`` and ``{dest}`` placeholders are substituted with the
            shell-quoted spool and destination paths (eg
            ``"nginx -t -c {spool}"``). Defaults to None (no validation).
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The spool transfer, the optional validation of the spooled
            file, and the elevated install command, only when the content
            differs.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        ```python
        from reemote import op, files

        # Install text content at a root-owned path -- the /tmp spool rides
        # unprivileged scp, then an elevated ``sh -c`` does mkdir/cp/chmod/rm.
        yield op(
            files.install,
            name="Pin the nginx package",
            content="Package: nginx\\nPin: release o=nginx\\nPin-Priority: 1001\\n",
            dest="/etc/apt/preferences.d/nginx",
            mode="0644",
            sudo=True,
        )

        # Binary content skips the UTF-8 decode; ``validate`` runs on the
        # spooled bytes between the transfer and the install, so a bad file
        # never reaches the destination.
        yield op(
            files.install,
            name="Ship the composer launcher",
            content=open("./build/composer.phar", "rb").read(),
            dest="/usr/local/bin/composer.phar",
            mode="0755",
            validate="php -l {spool}",
            sudo=True,
        )
        ```

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
    # Idempotent on content: ``FileContents`` for a ``str``, the binary-safe
    # ``RemoteFileSha1`` for ``bytes``. When the destination already holds
    # exactly ``content`` nothing is yielded; otherwise an
    # ``InstallSpoolCommand`` (text) or ``InstallBinarySpoolCommand`` (bytes)
    # transfer spools the content to a dest-derived ``/tmp`` path over scp
    # (unprivileged regardless of the request's ``sudo``), then an
    # ``InstallCommand`` runs the elevated
    # ``mkdir -p``/``cp``/``chmod``/``rm`` install. Installing under
    # ``/etc`` needs root, so flag the request ``sudo=True`` (or
    # ``su=True``); the run-as user and any password come from the
    # inventory ``privilege`` data, never the operation.
    #
    # ``content`` is a plain ``str`` or ``bytes``, not an IO stream (the
    # ``systemd.dropin`` shape, developer-chosen): both are hashable and
    # equal across phases, so no identity-stable module-level stream is
    # needed. The bytes path (issue #23) is the binary spool-and-install
    # ``files.unarchive``'s ``UnarchiveSpoolCommand`` pioneered, generalized
    # from "extract an archive" to "install a file": a ``.phar`` launcher
    # lands at ``/usr/local/bin`` in ONE request exactly as a text config
    # lands under ``/etc``, idempotent on the sha1 digest ``files.put``
    # already compares -- no UTF-8 decode, so any bytes work. The sha1
    # read-back is unprivileged (``sha1sum`` as the login user), so a
    # login-unreadable binary destination reads as absent and re-installs
    # every deploy -- give a binary install a login-readable mode (the
    # ``0755`` launcher shape). The spool name is the destination's sha1
    # prefix, so different targets never collide on one spool file while
    # the name stays deterministic across phases; it is collision
    # avoidance, not a security boundary (the name is predictable on
    # world-writable ``/tmp``).
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
    #
    # ``validate`` is the atomic validate-then-install hook Ansible's
    # ``template``/``copy`` ``validate:`` provides and the reason a bad config
    # never reaches a protected path. When set, an ``InstallValidateCommand``
    # is yielded *between* the spool transfer and the install, running the
    # caller's command template against the just-spooled ``/tmp`` bytes -- the
    # very bytes the install is about to ``cp`` into place, so the check is of
    # the content actually installed, not a separate copy. The driver runs
    # every shell descriptor fail-fast, so a non-zero exit raises
    # ``CommandError`` and the ``InstallCommand`` never runs: the destination
    # keeps its previous content and the whole write is all-or-nothing within
    # ONE request and ONE deploy. This is the narrow, safe slice of "a command
    # inside a deploy" -- scoped to validating the staged content at a single
    # well-defined injection point, not a generic shell operation (the
    # action/deploy boundary stays intact). The template's ``{spool}``/``{dest}``
    # placeholders are substituted with shell-quoted paths, so it stays a
    # hashable ``str`` and deterministic across phases; the command inherits
    # the request's privilege flags, so a ``sudo=True`` install validates
    # elevated (as root), matching Ansible's become-scoped ``validate`` and
    # letting a checker like ``nginx -t`` read every file the config
    # references. An empty/whitespace-only template raises ``ValueError``.
    # The text read-back is two-tier: ``FileContents`` cats unprivileged
    # first and, when the login user cannot read the file (eg a ``0640``
    # config owned by a service account), re-reads it elevated with ``sudo``
    # -- so content idempotence holds for restrictive modes too, and the
    # re-read's run-as user and any password come from the inventory
    # ``privilege`` data. There is deliberately no ``present=False`` removal
    # branch -- ``files.file(present=False)`` already owns removal.
    # ``**kwargs`` absorbs any further global arguments (``op()`` already
    # strips ``name``/``sudo``/``su``).
    if not mode or any(c not in "01234567" for c in mode):
        raise ValueError(
            f"Invalid file mode: {mode!r} expected octal digits "
            "(eg '0644' or '600')"
        )
    mode_int = int(mode)
    if validate is not None and not validate.strip():
        raise ValueError("validate must be a non-empty command template")

    spool = _install_spool_path(dest)
    if isinstance(content, bytes):
        # The binary path: compare the content's sha1 (a plain control-side
        # hashlib call, the files.put precedent) against the remote file's
        # binary-safe digest -- never a UTF-8 decode.
        local_sha1 = hashlib.sha1(content).hexdigest()
        current_sha1 = await ctx.facts.get(RemoteFileSha1, dest)
        if current_sha1 == local_sha1:
            ctx.reason = f"content already up to date ({dest})"
            return
        ctx.changed = True
        ctx.reason = (
            f"content {dest} differs, will install"
            if current_sha1 is not None
            else f"content {dest} is absent, will install"
        )
        yield InstallBinarySpoolCommand(content=content, dest=spool)
    else:
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
        yield InstallSpoolCommand(content=content, dest=spool)
    if validate is not None:
        yield InstallValidateCommand(validate=validate, spool=spool, dest=dest)
    yield InstallCommand(
        spool=spool, dest=dest, mode=mode_int, user=user, group=group
    )


async def transform(
    ctx: OpContext,
    path: str,
    transform: Callable[[str | None], str],
    mode: str = "0644",
    user: str | None = None,
    group: str | None = None,
    validate: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Edit a root-owned remote file through a pure content transform.

    Args:
        ctx (OpContext): Execution context for the operation.
        path (str): The remote file to edit.
        transform (Callable[[str | None], str]): A pure, idempotent callable
            taking the current content (``str``, or ``None`` when the file is
            absent) and returning the desired content as a ``str``.
        mode (str, optional): The installed file's permissions as octal-digit
            text (``"0644"``, ``"600"``). Defaults to "0644".
        user (str | None, optional): The user to own the file after the
            install. Defaults to None (leaves the file's owner unchanged).
        group (str | None, optional): The group to own the file after the
            install. Defaults to None (leaves the file's group unchanged).
        validate (str | None, optional): A command template run on the spooled
            ``/tmp`` file between the transfer and the install, forwarded
            straight to ``files.install``; a non-zero exit aborts the install
            and leaves the file untouched. The ``{spool}``/``{dest}``
            placeholders are substituted with the shell-quoted paths.
            Defaults to None (no validation).
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The spool transfer and the elevated install command, when the
            transformed content differs from the file's current text.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        ```python
        from reemote import op, files

        # ``transform`` must be a stable, hashable callable defined at module
        # level, idempotent (``transform(transform(x)) == transform(x)``) and
        # side-effect free.
        def ensure_ip_forward(current: str | None) -> str:
            body = current or ""
            if "net.ipv4.ip_forward" in body:
                return body
            return body + "net.ipv4.ip_forward = 1\\n"

        # Edit a root-owned file through the same pure transform as
        # ``files.edit``, but spool-and-install the result -- ``sudo=True``
        # elevates only the final cp; the /tmp transfer stays unprivileged.
        yield op(
            files.transform,
            name="Enable ip_forward",
            path="/etc/sysctl.d/99-forward.conf",
            transform=ensure_ip_forward,
            mode="0644",
            sudo=True,
        )
        ```

        Examples can be found in ``examples/modules/files/files_transform.py``.
    """

    # The root-owned sibling of ``files.edit``: the same contract for the
    # caller-supplied *pure* ``transform`` -- read the file's current text with
    # the ``FileContents`` fact, hand it (``None`` when the file is absent) to
    # ``transform`` -- but a different *write mechanism*. ``files.edit`` writes
    # back through ``files.put``'s unprivileged scp (``PutCommand``), so its
    # ``sudo``/``su`` do not apply and ``path`` must be writable by the login
    # user; it therefore cannot converge a root-owned target such as
    # ``/etc/httpd/conf/httpd.conf`` in one deploy. ``files.transform`` closes
    # that gap by delegating the write to ``files.install``: the transformed
    # text is spooled over scp to a dest-derived ``/tmp`` path (unprivileged
    # regardless of ``sudo``) and an elevated ``sh -c`` overlays ``path`` with
    # it, so a single ``sudo=True`` request both preserves the rest of the
    # protected file and lands the edit. This is the generic form of the
    # pattern ``server.etc_hosts`` hard-wires to ``/etc/hosts``.
    #
    # The transform must be *idempotent* -- ``transform(transform(x)) ==
    # transform(x)`` -- and side-effect free, since it runs once per phase
    # (prepare, then each apply step); it must also be a *stable, hashable*
    # object -- a module-level function, not a lambda or closure built inside
    # the deploy generator -- because it is stored in the ``OperationRequest``
    # kwargs the determinism guard compares by value across phases (a fresh
    # callable per phase would diverge and raise ``DeterminismError``). See
    # ``docs/migrating.md`` for how to write one.
    #
    # Idempotence and every write detail are ``files.install``'s, inherited by
    # delegation (the project's rule for a derived content-producing files
    # operation): install re-reads ``FileContents`` -- served from the same
    # phase ``FactCache``, so no second host round-trip -- yields nothing when
    # the transformed text already equals the file's content, validates
    # ``mode`` (raising ``ValueError`` on a non-octal-digit string), derives
    # the ``/tmp`` spool name from ``path``, and applies the optional
    # ``user``/``group`` chown inside the elevated chain. An absent file is a
    # change: ``transform(None)`` produces the content to create it from.
    # Content-only idempotence means a mode- or owner-only drift does not
    # re-install (``files.file`` converges metadata), and the read-back is
    # install's too -- a file the login user cannot read is re-read elevated,
    # so a restrictive mode does not re-install every deploy. ``validate`` is
    # forwarded straight through, so a transform inherits the same atomic
    # validate-then-install hook on the spooled bytes (a non-zero exit aborts
    # before the target is touched). ``/etc`` targets are root-owned,
    # so flag the request ``sudo=True`` (or ``su=True``). ``**kwargs`` absorbs
    # any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    current = await ctx.facts.get(FileContents, path)
    new = transform(current)
    async for command in install(
        ctx,
        new,
        path,
        mode=mode,
        user=user,
        group=group,
        validate=validate,
        **kwargs,
    ):
        yield command


def _ini_set_option(
    current: str | None,
    section: str,
    option: str,
    value: str,
    no_extra_spaces: bool,
    create: bool,
) -> str:
    """Pure section-scoped INI edit behind ``ini_option`` (no host contact)."""
    if not section.strip() or any(c in section for c in "[]\r\n"):
        raise ValueError(f"Invalid INI section name: {section!r}")
    if not option.strip() or any(c in option for c in "=:[]\r\n"):
        raise ValueError(f"Invalid INI option name: {option!r}")
    if any(c in value for c in "\r\n"):
        raise ValueError(f"Invalid INI value: {value!r} (a single line expected)")
    rendered = f"{option}={value}" if no_extra_spaces else f"{option} = {value}"
    if current is None:
        if not create:
            raise ValueError(
                f"INI file is absent and create=False: cannot set "
                f"[{section}] {option}"
            )
        return f"[{section}]\n{rendered}\n"

    lines = current.splitlines()
    trailing_newline = "\n" if current.endswith("\n") else ""
    header = re.compile(r"^\s*\[([^]]+)\]\s*(?:[;#].*)?$")
    key = re.compile(r"^\s*([^=:]+?)\s*[:=]")

    # Locate the section's bounds: its header line, then the next header
    # (any section) or EOF. Everything outside stays untouched.
    start: int | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        match = header.match(line)
        if match is None:
            continue
        if start is not None:
            end = index
            break
        if match.group(1).strip() == section:
            start = index

    if start is None:
        # Missing section: append it (blank-line separated, configparser's
        # own section spacing) with the option as its first line.
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend((f"[{section}]", rendered))
        return "\n".join(lines) + "\n"

    # An active option line inside the section: not a comment/blank, keyed
    # by the text before the first '=' or ':' (configparser's separators).
    for index in range(start + 1, end):
        stripped = lines[index].strip()
        if not stripped or stripped[0] in "#;":
            continue
        match = key.match(lines[index])
        if match is not None and match.group(1) == option:
            if lines[index] == rendered:
                return current
            lines[index] = rendered
            return "\n".join(lines) + trailing_newline

    # Missing option: insert after the section's last non-blank line, so
    # the blank lines separating it from the next section stay in place.
    insert = end
    while insert > start + 1 and not lines[insert - 1].strip():
        insert -= 1
    lines.insert(insert, rendered)
    return "\n".join(lines) + trailing_newline


async def ini_option(
    ctx: OpContext,
    path: str,
    section: str,
    option: str,
    value: str,
    no_extra_spaces: bool = False,
    create: bool = True,
    mode: str = "0644",
    user: str | None = None,
    group: str | None = None,
    validate: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Set one option in one section of a root-owned INI file.

    Args:
        ctx (OpContext): Execution context for the operation.
        path (str): The remote INI file to edit (eg ``/etc/yum.repos.d/epel.repo``).
        section (str): The section holding the option, without brackets
            (eg ``"epel"``). Appended as a new section when missing.
        option (str): The option name to set (eg ``"enabled"``). Inserted
            into the section when missing.
        value (str): The option's desired value as text (eg ``"1"``).
        no_extra_spaces (bool, optional): Render the line as ``option=value``
            instead of ``option = value``. Defaults to False.
        create (bool, optional): Create the file (holding just the section
            and option) when it is absent; when False an absent file raises
            ``ValueError``. Defaults to True.
        mode (str, optional): The installed file's permissions as octal-digit
            text (``"0644"``, ``"600"``). Defaults to "0644".
        user (str | None, optional): The user to own the file after the
            install. Defaults to None (leaves the file's owner unchanged).
        group (str | None, optional): The group to own the file after the
            install. Defaults to None (leaves the file's group unchanged).
        validate (str | None, optional): A command template run on the spooled
            ``/tmp`` file between the transfer and the install, forwarded
            straight to ``files.install``; a non-zero exit aborts the install
            and leaves the file untouched. The ``{spool}``/``{dest}``
            placeholders are substituted with the shell-quoted paths.
            Defaults to None (no validation).
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The spool transfer and the elevated install command, when the
            edited content differs from the file's current text.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        ```python
        from reemote import op, files

        # Set one option in one section of a root-owned INI file, preserving
        # every other section, option, comment and blank line (Ansible
        # ``ini_file`` parity).
        yield op(
            files.ini_option,
            name="Enable the epel repo",
            path="/etc/yum.repos.d/epel.repo",
            section="epel",
            option="enabled",
            value="1",
            sudo=True,
        )

        # Render as ``option=value`` (no spaces around ``=``), create the file
        # when absent, and set ownership/mode on the installed file.
        yield op(
            files.ini_option,
            name="Author the app worker setting",
            path="/etc/app/config.ini",
            section="worker",
            option="concurrency",
            value="4",
            no_extra_spaces=True,
            create=True,
            mode="0640",
            user="www-data",
            sudo=True,
        )
        ```

        Examples can be found in ``examples/modules/files/files_ini_option.py``.
    """

    # Ansible ``ini_file`` parity: one option in one section, preserving every
    # other section, option, comment and blank line. Neither neighbour fits
    # that shape: ``dnf.repo`` constructs a whole SINGLE-section ``.repo`` file
    # from its arguments (it would clobber the RPM-shipped ``epel.repo``'s
    # ``[epel-debuginfo]``/``[epel-source]`` sections), and ``files.edit`` is
    # line-scoped -- a bare ``^enabled=`` replace would also hit the other
    # sections' ``enabled=0`` lines. This operation is ``files.transform`` with
    # the section-scoped edit built in: read the file's text with the
    # ``FileContents`` fact, apply the pure ``_ini_set_option`` helper, and
    # delegate the write to ``files.install`` (spool over unprivileged scp to a
    # dest-derived ``/tmp`` path, then an elevated ``sh -c`` install), so one
    # ``sudo=True`` request converges a root-owned config in ONE deploy.
    #
    # The edit is hand-rolled line surgery rather than a stdlib
    # ``configparser`` round-trip on purpose: configparser rewrites the whole
    # file (comments dropped, spacing normalized, options reordered), which
    # breaks both the preservation contract and content idempotence. The
    # helper rewrites only the one active option line (``=`` and ``:``
    # separators both recognized, matching is case-sensitive, the first
    # occurrence wins when a section duplicates an option), inserts a missing
    # option after the section's last non-blank line, and appends a missing
    # section blank-line-separated at EOF. A line already rendered exactly as
    # requested returns the content verbatim, so the edit is a fixpoint and
    # install's content comparison reports no change on the second request.
    # The file's trailing-newline state is preserved.
    #
    # ``no_extra_spaces`` and ``create`` are Ansible's own option names and
    # defaults (``ini_file`` renders ``option = value`` unless
    # ``no_extra_spaces: true``, and creates an absent file unless
    # ``create: no``). Deliberately unshipped: ``state: absent`` removal (a
    # ``files.transform`` drop-line transform owns it), ``allow_no_value``,
    # ``backup`` (non-deterministic, the project-wide drop) and interpolation
    # (values are literal text). ``mode``/``user``/``group``/``validate`` ride
    # install's contract: applied on every write, but idempotence is
    # content-only -- a mode- or owner-only drift does not re-install (use
    # ``files.file`` to converge metadata). The read-back is install's too: a
    # file the login user cannot read is re-read elevated, so a restrictive
    # mode does not re-install every deploy. ``/etc`` targets are root-owned,
    # so flag the request ``sudo=True`` (or ``su=True``); the run-as user and
    # any password come from the inventory ``privilege`` data, never the
    # operation. ``**kwargs`` absorbs any further global arguments (``op()``
    # already strips ``name``/``sudo``/``su``).
    current = await ctx.facts.get(FileContents, path)
    new = _ini_set_option(current, section, option, value, no_extra_spaces, create)
    async for command in install(
        ctx,
        new,
        path,
        mode=mode,
        user=user,
        group=group,
        validate=validate,
        **kwargs,
    ):
        yield command


# --- htpasswd support: the control-side hash schemes ---------------------------
#
# ``files.htpasswd`` manages ``name:hash`` lines, and every hash below is
# computed on the *control* machine in pure Python. Three reasons conspire to
# make that the only workable place: the library ships no third-party
# dependency (asyncssh/distro/pydantic only), the stdlib ``crypt`` module is
# gone in Python 3.13+, and an htpasswd file stores plain hash *text* -- the
# target never runs a hashing program, it just serves the bytes. Each scheme is
# pinned against a reference implementation in ``tests/test_files_htpasswd.py``:
# ``$apr1$``/``$1$`` against ``openssl passwd``, ``{SHA}`` against
# ``base64(sha1)``, ``$2y$`` against the C ``bcrypt`` package's output, and
# traditional DES against ``perl -e 'print crypt($p, $s)'``. The tables are
# transcribed from libxcrypt's ``gen-des-tables.c`` and the algorithms from
# libxcrypt's ``crypt-des.c``/``alg-des.c`` and OpenBSD's ``bcrypt.c``, and the
# Blowfish initial tables are derived -- they *are* the hex digits of pi, so
# ``_pi_hex_digits`` reproduces them instead of embedding 8 KB of constants.

_MASK32 = 0xFFFFFFFF

#: The htpasswd/htdigest base-64 alphabet (``./``, then digits, then upper- and
#: lowercase): MD5-crypt, traditional DES and their salts all encode through it.
_H64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

#: OpenBSD bcrypt's variant base-64 (``./``, then upper- and lowercase, then
#: digits) -- the same characters, a different value for each symbol.
_BCRYPT_B64 = "./ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

#: The MD5-crypt byte transposition applied to the final digest before the h64
#: encoding (PHK's and Apache's, not the natural order).
_MD5_TRANSPOSE = (12, 6, 0, 13, 7, 1, 14, 8, 2, 15, 9, 3, 5, 10, 4, 11)

#: The schemes ``htpasswd_hash`` implements, in canonical spelling.
HTPASSWD_SCHEMES: tuple[str, ...] = (
    "plain",
    "apr_md5_crypt",
    "md5_crypt",
    "apr_sha1",
    "bcrypt",
    "descrypt",
)

#: Alternate spellings accepted for those schemes: Ansible's/passlib's handler
#: names, ``htpasswd(1)``'s flag letters and the role's historical ``sha1``.
_HTPASSWD_SCHEME_ALIASES: dict[str, str] = {
    "apache_md5": "apr_md5_crypt",
    "apr1": "apr_md5_crypt",
    "bsdi": "descrypt",
    "crypt": "descrypt",
    "des": "descrypt",
    "des_crypt": "descrypt",
    "ldap_sha1": "apr_sha1",
    "md5": "md5_crypt",
    "plaintext": "plain",
    "sha1": "apr_sha1",
}

#: The default cost for ``bcrypt``, matching ``htpasswd -B`` and passlib's
#: ``htpasswd_context`` bcrypt handler (whose own default rounds are 5).
HTPASSWD_BCRYPT_COST = 5


def _h64_salt(length: int) -> str:
    """A fresh salt of ``length`` characters from the htpasswd h64 alphabet."""
    return "".join(secrets.choice(_H64) for _ in range(length))


def _md5_crypt(password: str, salt: str, magic: str = "$apr1$") -> str:
    """PHK's MD5-crypt: Apache's ``$apr1$`` and glibc's ``$1$`` form.

    Pure ``hashlib`` implementation of the 1000-round mixing, verified against
    ``openssl passwd -apr1 -salt``/``-1 -salt`` vectors and randomized passlib
    cross-checks. ``magic`` selects the variant, ``salt`` is 1-8 h64 characters
    (``htpasswd -m`` draws 8).
    """
    pwd = password.encode("utf-8")
    salt_b = salt.encode("ascii")
    magic_b = magic.encode("ascii")

    # Alternate digest: MD5(pwd || salt || pwd), repeated over the password.
    db = hashlib.md5(pwd + salt_b + pwd).digest()

    ctx = hashlib.md5()
    ctx.update(pwd)
    ctx.update(magic_b)
    ctx.update(salt_b)
    n = len(pwd)
    ctx.update((db * (n // 16 + 1))[:n])
    i = n
    while i:
        # One bit of the length per round: odd -> NUL, even -> first byte.
        ctx.update(b"\x00" if i & 1 else pwd[:1])
        i >>= 1
    dc = ctx.digest()

    for r in range(1000):
        t = hashlib.md5(pwd if r & 1 else dc)
        if r % 3:
            t.update(salt_b)
        if r % 7:
            t.update(pwd)
        t.update(dc if r & 1 else pwd)
        dc = t.digest()

    # h64 encoding of the transposed digest, little-endian per 3-byte group.
    reordered = bytes(dc[off] for off in _MD5_TRANSPOSE)
    out = []
    for group in range(0, 15, 3):
        v1, v2, v3 = reordered[group], reordered[group + 1], reordered[group + 2]
        out.append(_H64[v1 & 0x3F])
        out.append(_H64[((v2 & 0x0F) << 2) | (v1 >> 6)])
        out.append(_H64[((v3 & 0x03) << 4) | (v2 >> 4)])
        out.append(_H64[v3 >> 2])
    v1 = reordered[15]
    out.append(_H64[v1 & 0x3F])
    out.append(_H64[v1 >> 6])
    return magic + salt + "$" + "".join(out)


def _sha1_crypt(password: str) -> str:
    """Apache's ``{SHA}`` form: base64 of the password's raw SHA-1 digest."""
    digest = hashlib.sha1(password.encode("utf-8")).digest()
    return "{SHA}" + base64.b64encode(digest).decode("ascii")


# --- traditional BSD DES-crypt -------------------------------------------------

_DES_IP: tuple[int, ...] = (
    58, 50, 42, 34, 26, 18, 10, 2,
    60, 52, 44, 36, 28, 20, 12, 4,
    62, 54, 46, 38, 30, 22, 14, 6,
    64, 56, 48, 40, 32, 24, 16, 8,
    57, 49, 41, 33, 25, 17, 9, 1,
    59, 51, 43, 35, 27, 19, 11, 3,
    61, 53, 45, 37, 29, 21, 13, 5,
    63, 55, 47, 39, 31, 23, 15, 7,
)
_DES_FP: tuple[int, ...] = (
    40, 8, 48, 16, 56, 24, 64, 32,
    39, 7, 47, 15, 55, 23, 63, 31,
    38, 6, 46, 14, 54, 22, 62, 30,
    37, 5, 45, 13, 53, 21, 61, 29,
    36, 4, 44, 12, 52, 20, 60, 28,
    35, 3, 43, 11, 51, 19, 59, 27,
    34, 2, 42, 10, 50, 18, 58, 26,
    33, 1, 41, 9, 49, 17, 57, 25,
)
_DES_PC1: tuple[int, ...] = (
    57, 49, 41, 33, 25, 17, 9,
    1, 58, 50, 42, 34, 26, 18,
    10, 2, 59, 51, 43, 35, 27,
    19, 11, 3, 60, 52, 44, 36,
    63, 55, 47, 39, 31, 23, 15,
    7, 62, 54, 46, 38, 30, 22,
    14, 6, 61, 53, 45, 37, 29,
    21, 13, 5, 28, 20, 12, 4,
)
_DES_PC2: tuple[int, ...] = (
    14, 17, 11, 24, 1, 5,
    3, 28, 15, 6, 21, 10,
    23, 19, 12, 4, 26, 8,
    16, 7, 27, 20, 13, 2,
    41, 52, 31, 37, 47, 55,
    30, 40, 51, 45, 33, 48,
    44, 49, 39, 56, 34, 53,
    46, 42, 50, 36, 29, 32,
)
_DES_EXPANSION: tuple[int, ...] = (
    32, 1, 2, 3, 4, 5,
    4, 5, 6, 7, 8, 9,
    8, 9, 10, 11, 12, 13,
    12, 13, 14, 15, 16, 17,
    16, 17, 18, 19, 20, 21,
    20, 21, 22, 23, 24, 25,
    24, 25, 26, 27, 28, 29,
    28, 29, 30, 31, 32, 1,
)
_DES_P: tuple[int, ...] = (
    16, 7, 20, 21,
    29, 12, 28, 17,
    1, 15, 23, 26,
    5, 18, 31, 10,
    2, 8, 24, 14,
    32, 27, 3, 9,
    19, 13, 30, 6,
    22, 11, 4, 25,
)
_DES_SHIFTS: tuple[int, ...] = (1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1)
_DES_SBOXES: tuple[tuple[int, ...], ...] = (
    (
        14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7,
        0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8,
        4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0,
        15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13,
    ),
    (
        15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10,
        3, 13, 4, 7, 15, 2, 8, 14, 12, 0, 1, 10, 6, 9, 11, 5,
        0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15,
        13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9,
    ),
    (
        10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8,
        13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1,
        13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7,
        1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12,
    ),
    (
        7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15,
        13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9,
        10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4,
        3, 15, 0, 6, 10, 1, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14,
    ),
    (
        2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9,
        14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6,
        4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14,
        11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3,
    ),
    (
        12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11,
        10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8,
        9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6,
        4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13,
    ),
    (
        4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1,
        13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6,
        1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2,
        6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12,
    ),
    (
        13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7,
        1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2,
        7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8,
        2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11,
    ),
)


def _des_bits(value: int, width: int) -> list[int]:
    """``value`` as ``width`` most-significant-first bits."""
    return [(value >> (width - 1 - index)) & 1 for index in range(width)]


def _des_perm(bits: list[int], table: tuple[int, ...]) -> int:
    """Permute ``bits`` through a 1-based DES table, packing MSB-first."""
    out = 0
    for position in table:
        out = (out << 1) | bits[position - 1]
    return out


def _des_rotate28(word: int, count: int) -> int:
    """Rotate a 28-bit half-key left by ``count`` (the key schedule's step)."""
    return ((word << count) | (word >> (28 - count))) & 0x0FFFFFFF


def _des_subkeys(key_bytes: bytes) -> list[int]:
    """The 16 48-bit DES subkeys for ``key_bytes`` (PC1, shifts, PC2)."""
    cd = _des_perm(_des_bits(int.from_bytes(key_bytes[:8], "big"), 64), _DES_PC1)
    c, d = cd >> 28, cd & 0x0FFFFFFF
    keys: list[int] = []
    for shift in _DES_SHIFTS:
        c = _des_rotate28(c, shift)
        d = _des_rotate28(d, shift)
        combined = (c << 28) | d
        keys.append(_des_perm(_des_bits(combined, 56), _DES_PC2))
    return keys


def _des_feistel(right: int, subkey: int, pairs: tuple[tuple[int, int], ...]) -> int:
    """Expand ``right``, XOR the subkey, run the eight S-boxes and P.

    ``pairs`` are the salt's bit-exchanges, applied to the 48-bit expansion
    *after* expansion and *before* the XOR -- libxcrypt folds its
    ``r48l ^ r48r`` swap into the same positions.
    """
    x = _des_perm(_des_bits(right, 32), _DES_EXPANSION)
    for first, second in pairs:
        if ((x >> first) ^ (x >> second)) & 1:
            x ^= (1 << first) | (1 << second)
    x ^= subkey
    out = 0
    for index in range(8):
        block = (x >> (42 - 6 * index)) & 0x3F
        row = ((block & 0x20) >> 4) | (block & 0x01)
        column = (block >> 1) & 0x0F
        out = (out << 4) | _DES_SBOXES[index][row * 16 + column]
    return _des_perm(_des_bits(out, 32), _DES_P)


def _des_block(
    key_bytes: bytes,
    block_bytes: bytes,
    pairs: tuple[tuple[int, int], ...] = (),
    rounds: int = 1,
) -> bytes:
    """DES on one 64-bit block: IP, ``rounds`` x 16 Feistel rounds, FP.

    ``rounds > 1`` repeats the *same* 16 subkeys, swapping the halves once more
    per pass and never rotating the key -- that is BSD crypt's iteration, not
    iterated DES. ``pairs`` carries the salt's bit-exchanges.
    """
    keys = _des_subkeys(key_bytes)
    pre = _des_perm(_des_bits(int.from_bytes(block_bytes, "big"), 64), _DES_IP)
    left, right = pre >> 32, pre & 0xFFFFFFFF
    for _ in range(rounds):
        for key in keys:
            left, right = right, (left ^ _des_feistel(right, key, pairs)) & 0xFFFFFFFF
        left, right = right, left
    return (
        _des_perm(_des_bits((left << 32) | right, 64), _DES_FP).to_bytes(8, "big")
    )


def _des_salt_pairs(salt12: int) -> tuple[tuple[int, int], ...]:
    """The 12 salt bits as (high, low) bit-index pairs of the 48-bit expansion.

    Salt bit ``i`` exchanges the expansion's most-significant-first positions
    ``i`` and ``i + 24``; in the integer the S-boxes read, position ``p`` is bit
    ``47 - p``.
    """
    return tuple(
        (47 - index, 23 - index) for index in range(12) if (salt12 >> index) & 1
    )


def _h64_encode(block: bytes) -> str:
    """Pack ``block`` MSB-first into h64 characters (6 bits each)."""
    out: list[str] = []
    acc = 0
    bits = 0
    for byte in block:
        acc = (acc << 8) | byte
        bits += 8
        while bits >= 6:
            bits -= 6
            out.append(_H64[(acc >> bits) & 0x3F])
    if bits:
        out.append(_H64[(acc << (6 - bits)) & 0x3F])
    return "".join(out)


def _des_crypt(password: str, salt: str) -> str:
    """Classic BSD DES-crypt: the 13-character ``crypt(3)`` password hash.

    The first two characters of ``salt`` are the stored salt. ``password``
    contributes its first 8 bytes, each shifted left by one (the 64-bit DES
    key), the block encrypted is eight zero bytes, and 25 passes of one fixed
    subkey schedule run with the salt exchanging expansion bits -- verified
    byte-for-byte against ``perl``'s ``crypt()``.
    """
    pwd = password.encode("utf-8")[:8].ljust(8, b"\x00")
    key = bytes((char << 1) & 0xFF for char in pwd)
    salt12 = _H64.index(salt[0]) | (_H64.index(salt[1]) << 6)
    cipher = _des_block(key, b"\x00" * 8, _des_salt_pairs(salt12), 25)
    return salt[:2] + _h64_encode(cipher)


# --- OpenBSD bcrypt ------------------------------------------------------------


def _pi_hex_digits(digits: int) -> str:
    """The first ``digits`` fractional hex digits of pi (Machin's formula).

    Blowfish's initial P-array and S-boxes *are* the hexadecimal digits of pi,
    so deriving them keeps 1042 constants out of the source. Ten extra digits
    are computed and discarded so the truncation cannot round the last word.
    """
    scale = 16 ** (digits + 10)

    def arctan_inverse(k: int) -> int:
        total = 0
        power = scale // k
        n = 0
        while power:
            term = power // (2 * n + 1)
            total = total - term if n % 2 else total + term
            power //= k * k
            n += 1
        return total

    pi_scaled = 16 * arctan_inverse(5) - 4 * arctan_inverse(239)
    text = f"{pi_scaled:x}"
    if text[0] != "3":  # pragma: no cover - guards the fixed-point scaling
        raise RuntimeError("failed to derive the hex digits of pi")
    return text[1 : 1 + digits]


_BLOWFISH_TABLES: tuple[list[int], list[list[int]]] | None = None


def _blowfish_tables() -> tuple[list[int], list[list[int]]]:
    """The 18 P-array words and four 256-entry S-boxes from pi's hex digits."""
    global _BLOWFISH_TABLES
    if _BLOWFISH_TABLES is None:
        hexs = _pi_hex_digits((18 + 4 * 256) * 8)
        words = [int(hexs[i : i + 8], 16) for i in range(0, len(hexs), 8)]
        _BLOWFISH_TABLES = (
            words[:18],
            [words[18 + 256 * i : 18 + 256 * (i + 1)] for i in range(4)],
        )
    return _BLOWFISH_TABLES


def _bcrypt_b64_encode(data: bytes) -> str:
    """OpenBSD bcrypt's base-64: 6-bit groups, MSB-first, no padding."""
    out: list[str] = []
    index = 0
    while index < len(data):
        chunk = 0
        used = 0
        for offset in range(3):
            if index + offset < len(data):
                chunk = (chunk << 8) | data[index + offset]
                used += 1
            else:
                chunk <<= 8
        for group in range((used * 8 + 5) // 6):
            out.append(_BCRYPT_B64[(chunk >> (24 - (group + 1) * 6)) & 0x3F])
        index += used
    return "".join(out)


def _bcrypt_b64_decode(text: str) -> bytes:
    """Inverse of ``_bcrypt_b64_encode``, dropping the trailing bits."""
    out = bytearray()
    acc = 0
    bits = 0
    for char in text:
        acc = (acc << 6) | _BCRYPT_B64.index(char)
        bits += 6
        if bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    return bytes(out)


def _bf_stream_word(data: bytes, offset: int) -> int:
    """The 4 big-endian key/data bytes bcrypt XORs in, cycling past the end."""
    if not data:
        return 0
    word = 0
    for step in range(4):
        word = ((word << 8) | data[(offset + step) % len(data)]) & _MASK32
    return word


class _Blowfish:
    """Blowfish subkeys plus the two salted expansions bcrypt needs.

    A minimal port of OpenBSD's ``blowfish`` block cipher: 16 Feistel rounds
    over the P-array and S-boxes, and the two key schedules bcrypt uses --
    ``expandstate`` (XOR the key into P, then walk the whole subkey array
    self-encrypting with the *data* stream XORed into the running block) and
    ``expand0state`` (the same walk with no data stream).
    """

    def __init__(self, p_init: list[int], s_init: list[list[int]]):
        self._p_init = p_init
        self._s_init = s_init
        self.p = list(p_init)
        self.s = [list(box) for box in s_init]

    def reset(self) -> None:
        """Restore the pi-derived tables so one engine can hash repeatedly."""
        self.p = list(self._p_init)
        self.s = [list(box) for box in self._s_init]

    def _f(self, x: int) -> int:
        s = self.s
        v = (s[0][(x >> 24) & 0xFF] + s[1][(x >> 16) & 0xFF]) & _MASK32
        v ^= s[2][(x >> 8) & 0xFF]
        return (v + s[3][x & 0xFF]) & _MASK32

    def encipher(self, left: int, right: int) -> tuple[int, int]:
        """One 64-bit Blowfish block, as the pair of 32-bit halves."""
        p = self.p
        for index in range(16):
            left ^= p[index]
            right = (self._f(left) ^ right) & _MASK32
            left, right = right, left
        left, right = right, left
        right ^= p[16]
        left ^= p[17]
        return left & _MASK32, right & _MASK32

    def _xor_key(self, key: bytes) -> None:
        j = 0
        for index in range(18):
            self.p[index] ^= _bf_stream_word(key, j)
            j += 4

    def _walk(self, data: bytes | None) -> None:
        """Self-encrypt the whole subkey array, optionally XORing ``data`` in."""
        datal = datar = 0
        j = 0
        cycle = max(len(data), 1) if data else 1
        for index in range(0, 18, 2):
            if data:
                datal ^= _bf_stream_word(data, j)
                j = (j + 4) % cycle
                datar ^= _bf_stream_word(data, j)
                j = (j + 4) % cycle
            datal, datar = self.encipher(datal, datar)
            self.p[index], self.p[index + 1] = datal, datar
        for box in range(4):
            for pair in range(0, 256, 2):
                if data:
                    datal ^= _bf_stream_word(data, j)
                    j = (j + 4) % cycle
                    datar ^= _bf_stream_word(data, j)
                    j = (j + 4) % cycle
                datal, datar = self.encipher(datal, datar)
                self.s[box][pair], self.s[box][pair + 1] = datal, datar

    def expand0state(self, key: bytes) -> None:
        """``Blowfish_expand0state``: key into P, then walk with no data."""
        self._xor_key(key)
        self._walk(None)

    def expandstate(self, data: bytes, key: bytes) -> None:
        """``Blowfish_expandstate``: key into P, walk with the data stream."""
        self._xor_key(key)
        self._walk(data)


#: The fixed plaintext bcrypt encrypts 64 times: "OrpheanBeholderScryDoubt".
_BCRYPT_CTEXT = (
    0x4F727068, 0x65616E42, 0x65686F6C, 0x64657253, 0x63727944, 0x6F756274
)


def _bcrypt_crypt(password: str, salt: str, cost: int, ident: str = "2y") -> str:
    """OpenBSD bcrypt in the Apache-compatible ``$2y$`` form.

    ``salt`` is the 22-character bcrypt base-64 of 16 salt bytes, ``cost`` the
    log2 iteration count rendered as two digits. The password (including its
    terminating NUL, truncated to 72 bytes as the format mandates) drives
    ``expandstate`` against the salt, then ``2**cost`` pairs of
    ``expand0state`` -- password, then salt -- and finally the fixed ciphertext
    through 64 passes of three blocks; the digest's first 23 bytes encode in
    bcrypt base-64. Verified byte-for-byte against the reference C
    implementation.
    """
    engine = _Blowfish(*_blowfish_tables())
    salt_bytes = _bcrypt_b64_decode(salt)[:16]
    key = password.encode("utf-8")[:72] + b"\x00"
    engine.reset()
    engine.expandstate(salt_bytes, key)
    for _ in range(1 << cost):
        engine.expand0state(key)
        engine.expand0state(salt_bytes)
    data = list(_BCRYPT_CTEXT)
    for _ in range(64):
        for index in range(0, 6, 2):
            data[index], data[index + 1] = engine.encipher(data[index], data[index + 1])
    digest = b"".join(word.to_bytes(4, "big") for word in data)
    return f"${ident}${cost:02d}${salt}{_bcrypt_b64_encode(digest[:23])}"


def _bcrypt_salt() -> str:
    """A fresh 16-byte salt, encoded in the 22-character bcrypt base-64."""
    return _bcrypt_b64_encode(secrets.token_bytes(16))


def _bcrypt_parse(stored: str) -> tuple[str, int, str] | None:
    """``(ident, cost, salt)`` from a stored ``$2[a-y]$`` hash, None if not one.

    The layout is ``$``, the two-character ident, ``$``, the two-digit cost,
    ``$`` and the 22-character salt, so 60 characters in total. A value that
    merely *looks* like bcrypt (a shorter line, a non-digit cost, a character
    outside the bcrypt alphabet) is rejected here rather than raising from the
    decoder, so ``htpasswd_verify`` can fall through to the next handler.
    """
    if len(stored) < 29 or not stored.startswith("$2"):
        return None
    if stored[3] != "$" or stored[6] != "$":
        return None
    ident = f"2{stored[2]}"
    cost = stored[4:6]
    salt = stored[7:29]
    if ident not in ("2a", "2b", "2x", "2y") or not cost.isdigit():
        return None
    if any(char not in _BCRYPT_B64 for char in salt):
        return None
    return ident, int(cost), salt


# --- the scheme dispatch -------------------------------------------------------


def htpasswd_scheme(hash_scheme: str) -> str:
    """The canonical ``hash_scheme`` name, raising on an unimplemented one.

    The single source of truth for what a caller may ask for: the six
    ``HTPASSWD_SCHEMES`` spellings plus the Ansible-side aliases they are
    reached through (``"sha1"``, ``"crypt"``, ...). Callers that store or
    display the scheme can validate through this without hashing.
    """
    scheme = hash_scheme.strip().lower()
    scheme = _HTPASSWD_SCHEME_ALIASES.get(scheme, scheme)
    if scheme not in HTPASSWD_SCHEMES:
        raise ValueError(
            f"unsupported hash_scheme {hash_scheme!r}; files.htpasswd implements "
            f"{', '.join(HTPASSWD_SCHEMES)} (sha256_crypt, sha512_crypt and the "
            "other passlib handlers are not htpasswd-portable and are not "
            "implemented)"
        )
    return scheme


def htpasswd_hash(password: str, hash_scheme: str = "apr_md5_crypt") -> str:
    """Hash ``password`` for an htpasswd line in ``hash_scheme``.

    Every hashed scheme draws a fresh salt from the CSPRNG, so two calls never
    return the same text -- which is why the entry edit verifies a stored hash
    instead of comparing it (``htpasswd_verify``). ``bcrypt`` hashes at
    ``HTPASSWD_BCRYPT_COST`` and ``descrypt`` uses only the password's
    first 8 characters, as the format itself dictates.
    """
    scheme = htpasswd_scheme(hash_scheme)
    if scheme == "plain":
        return password
    if scheme == "apr_md5_crypt":
        return _md5_crypt(password, _h64_salt(8), "$apr1$")
    if scheme == "md5_crypt":
        return _md5_crypt(password, _h64_salt(8), "$1$")
    if scheme == "apr_sha1":
        return _sha1_crypt(password)
    if scheme == "bcrypt":
        return _bcrypt_crypt(password, _bcrypt_salt(), HTPASSWD_BCRYPT_COST, "2y")
    return _des_crypt(password, _h64_salt(2))


def htpasswd_verify(password: str, stored: str) -> bool:
    """Whether ``password`` verifies against one line's stored ``hash`` field.

    The mirror of the Ansible module's verify-then-keep: it dispatches on the
    stored form, not on the requested scheme, so a managed line whose hash
    already proves the desired password is reproduced byte-for-byte. A stored
    field equal to the password verifies outright -- passlib's htpasswd context
    carries a ``plaintext`` handler for exactly that case, and checking it first
    keeps a 13-character plaintext password (which is also a well-formed DES
    hash shape) from churning on every deploy.
    """
    if stored == password:
        return True
    if stored.startswith(("$apr1$", "$1$")):
        parts = stored.split("$")
        if len(parts) != 4:
            return False
        return _md5_crypt(password, parts[2], f"${parts[1]}$") == stored
    if stored.startswith("{SHA}"):
        return stored == _sha1_crypt(password)
    bcrypt = _bcrypt_parse(stored)
    if bcrypt is not None:
        ident, cost, salt = bcrypt
        return _bcrypt_crypt(password, salt, cost, ident) == stored
    if len(stored) == 13 and all(char in _H64 for char in stored):
        return _des_crypt(password, stored[:2]) == stored
    return False


def htpasswd_entry(
    current: str | None,
    username: str,
    password: str | None = None,
    hash_scheme: str = "apr_md5_crypt",
    present: bool = True,
) -> str:
    """The desired file text for one ``name:hash`` entry of an htpasswd file.

    Pure, control-side and a fixpoint: the managed line is *verified* rather
    than compared, so a stored hash that already proves ``password`` is
    reproduced byte-for-byte (no fresh salt) and applying the edit to its own
    output changes nothing. A new or stale line is appended or rewritten with a
    freshly hashed one, ``present=False`` drops the line, and every other line
    -- comment, blank, another user -- keeps its place and content.

    Args:
        current (str | None): The file's present text, ``None`` when absent.
        username (str): The credential's user name (no colon).
        password (str | None, optional): The password to hash; required when
            ``present``. Defaults to None.
        hash_scheme (str, optional): The scheme to hash a changed entry with;
            see ``htpasswd_hash``. Defaults to "apr_md5_crypt".
        present (bool, optional): Ensure the entry, or remove it. Defaults
            to True.

    Returns:
        str: The whole file's desired text, newline-terminated.
    """
    if not username:
        raise ValueError("htpasswd username must be a non-empty string")
    if ":" in username:
        raise ValueError(
            f"htpasswd username must not contain a colon, got {username!r}"
        )
    lines = current.split("\n") if current else []
    if lines and lines[-1] == "":
        # The trailing newline is a line terminator, not an entry; the join
        # below restores it, so a file converges to exactly one of them.
        lines.pop()
    prefix = f"{username}:"
    index = next(
        (position for position, line in enumerate(lines) if line.startswith(prefix)),
        None,
    )
    if present:
        if password is None:
            raise ValueError(
                f"a password is required to manage the htpasswd entry {username!r} "
                "(pass present=False to remove it)"
            )
        # The scheme is only paid for when the line actually changes, so a
        # converged file costs one verification pass, not a re-hash.
        stored = lines[index].split(":", 1)[1] if index is not None else None
        if stored is None:
            lines.append(f"{username}:{htpasswd_hash(password, hash_scheme)}")
        elif not htpasswd_verify(password, stored):
            lines[index] = f"{username}:{htpasswd_hash(password, hash_scheme)}"
    elif index is not None:
        lines.pop(index)
    return "".join(f"{line}\n" for line in lines)


async def htpasswd(
    ctx: OpContext,
    path: str,
    username: str,
    password: str | None = None,
    hash_scheme: str = "apr_md5_crypt",
    owner: str | None = None,
    group: str | None = None,
    mode: str = "0640",
    present: bool = True,
    validate: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add, update or remove one entry in an Apache htpasswd file.

    Args:
        ctx (OpContext): Execution context for the operation.
        path (str): The remote htpasswd file to manage (eg
            ``/etc/apache2/.htpasswd``). Created with its parent directory
            when absent.
        username (str): The credential's user name -- Ansible's ``name``, which
            cannot be spelled here because ``op()`` consumes ``name`` for the
            request label (``username`` is that parameter's own alias).
        password (str | None, optional): The password to hash for ``username``;
            required when ``present``. Defaults to None.
        hash_scheme (str, optional): How to hash a new or changed entry: the
            Apache default ``"apr_md5_crypt"`` (``$apr1$``), ``"md5_crypt"``
            (``$1$``), ``"apr_sha1"`` (``{SHA}``, also accepted as ``"sha1"`` /
            ``"ldap_sha1"``), ``"bcrypt"`` (``$2y$``), ``"descrypt"``
            (traditional DES, also ``"crypt"``/``"des_crypt"``) or ``"plain"``.
            Defaults to "apr_md5_crypt".
        owner (str | None, optional): The user to own the file after the
            install. Defaults to None (leaves the file's owner unchanged).
        group (str | None, optional): The group to own the file after the
            install. Defaults to None (leaves the file's group unchanged).
        mode (str, optional): The installed file's permissions as octal-digit
            text (``"0640"``, ``"600"``). Defaults to "0640" -- stored hashes
            are readable by any local user at ``0644``.
        present (bool, optional): Ensure the entry exists, or remove it
            (Ansible's ``state: absent``). Defaults to True.
        validate (str | None, optional): A command template run on the spooled
            ``/tmp`` file between the transfer and the install, forwarded
            straight to ``files.install``. Defaults to None (no validation).
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The spool transfer and the elevated install command, only
            when the managed line changes.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        ```python
        from reemote import op, files

        # Add or update one user in a root-owned file only the web server can
        # read; the hash is computed on the control machine, so the target
        # needs no htpasswd(1) and no password ever reaches a command line.
        yield op(
            files.htpasswd,
            name="Allow the deploy user",
            path="/etc/apache2/.htpasswd",
            username="deploy",
            password="s3cr3t",
            owner="root",
            group="www-data",
            sudo=True,
        )

        # ``bcrypt`` for a stronger stored hash; ``present=False`` removes a
        # departed user and leaves every other line untouched.
        yield op(
            files.htpasswd,
            name="Allow the admin user",
            path="/etc/nginx/passwdfile",
            username="admin",
            password="hunter2",
            hash_scheme="bcrypt",
            group="www-data",
            sudo=True,
        )
        yield op(
            files.htpasswd,
            name="Drop the intern user",
            path="/etc/nginx/passwdfile",
            username="intern",
            present=False,
            sudo=True,
        )
        ```

        Examples can be found in ``examples/modules/files/files_htpasswd.py``.
    """

    # Ansible ``community.general.htpasswd`` parity (issue #74), and the shape
    # of the two neighbours this file already ships: read the file's text with
    # the ``FileContents`` fact, apply a pure edit, and delegate the write to
    # ``files.install`` (spool over unprivileged scp to a dest-derived ``/tmp``
    # path, then an elevated ``sh -c`` install). One ``sudo=True`` request
    # therefore converges the usual ``0640 root:www-data`` file in a SINGLE
    # deploy -- ``files.edit``/``files.put`` cannot, because their scp runs
    # unprivileged -- and the two-tier read re-reads the file elevated, so a
    # restrictive mode does not re-install every deploy.
    #
    # The hashing is control-side and pure Python (see the scheme helpers
    # above): the library has no crypto dependency, ``crypt()`` is gone from
    # Python 3.13+, and an htpasswd file only stores hash *text*, so the target
    # never runs a hashing program. Because every fresh hash draws a random
    # salt, idempotence cannot come from comparing the line's text: it comes
    # from ``htpasswd_entry`` verifying the stored hash against the desired
    # password and keeping the line verbatim when it proves. That is also why
    # changing ``hash_scheme`` alone does not rewrite an entry -- the Ansible
    # module documents the same blind spot, since a stored ``$apr1$`` line and a
    # stored ``{SHA}`` line both verify and neither reveals which scheme the
    # caller asked for.
    #
    # ``username`` is Ansible's ``name``/``username`` parameter renamed, the
    # ``crontab.crontab`` ``cron_name`` precedent: ``op()`` reads ``name`` as the
    # request label, so no operation can take it as an argument. ``owner`` keeps
    # Ansible's spelling (``username`` already took the credential), and maps to
    # install's ``user``.
    #
    # ``mode`` defaults to ``"0640"`` rather than the files family's ``"0644"``:
    # every line of this file is a password hash, and a world-readable one is
    # offline-crackable by any local account. ``**kwargs`` absorbs any further
    # global arguments (``op()`` already strips ``name``/``sudo``/``su``).
    # Deliberately unshipped, like ``ini_option``'s removal branch: ``create``
    # (an absent file is created, as Ansible's default does) and per-entry
    # ``backup`` (non-deterministic, the project-wide drop). Idempotence is
    # content-only, as install's: an owner/group/mode-only drift does not
    # re-install -- ``files.file`` converges metadata.
    current = await ctx.facts.get(FileContents, path)
    if not present and current is None:
        # Nothing to remove: creating an empty file would be a change Ansible
        # itself refuses to make (it warns and reports no change).
        ctx.reason = f"htpasswd file {path} is absent, nothing to remove"
        return
    new = htpasswd_entry(current, username, password, hash_scheme, present)
    async for command in install(
        ctx,
        new,
        path,
        mode=mode,
        user=owner,
        group=group,
        validate=validate,
        **kwargs,
    ):
        yield command


def _fetch_url_bytes(url: str) -> bytes:
    """Fetch an ``http(s)`` ``url`` control-side and return its body bytes.

    The single network read behind ``files.fetch_url`` (and so
    ``files.download``), run in a worker thread
    (``asyncio.to_thread``) because ``urllib`` blocks the event loop. The
    *control* machine -- not the target host -- opens the connection, so the
    download works when only the control side has internet egress and the host
    never needs ``curl``/``wget`` (the same control-side-fetch stance the
    ``dnf.key``/``apt.key``/``gpg.key`` stream workaround already takes, now
    behind a first-class operation). The caller validates the scheme, so this
    only ever sees an ``http(s)`` URL. An unreachable URL or a non-2xx
    response raises ``urllib.error.URLError``/``HTTPError`` (both ``OSError``
    subclasses), which propagates out of the deploy exactly as a host
    connection error would. Tests replace this function to stay offline.
    """
    with urllib.request.urlopen(url) as response:
        return response.read()


def _parse_checksum(checksum: str) -> tuple[str, str]:
    """Split an Ansible-style ``"<algorithm>:<hexdigest>"`` checksum spec.

    Returns the lowercased algorithm name and the lowercased expected digest,
    validating that the algorithm is one ``hashlib`` knows. A malformed spec
    (no ``:`` separator, an empty algorithm or digest) or an unknown algorithm
    raises ``ValueError`` before any fetch, so a typo fails fast at the deploy
    line rather than after a network round-trip. The spelling mirrors
    Ansible's ``get_url`` ``checksum`` (``"sha256:9f2c..."``).
    """
    algorithm, sep, expected = checksum.partition(":")
    algorithm = algorithm.strip().lower()
    expected = expected.strip().lower()
    if not sep or not algorithm or not expected:
        raise ValueError(
            f"checksum must be '<algorithm>:<hexdigest>' "
            f"(eg 'sha256:9f2c...'), got: {checksum!r}"
        )
    if algorithm not in hashlib.algorithms_available:
        raise ValueError(f"unknown checksum algorithm {algorithm!r} in {checksum!r}")
    return algorithm, expected


async def _fetch_url_verified(url: str, checksum: str | None) -> bytes:
    """Scheme-guarded, checksum-verified control-side fetch of a URL's bytes.

    The shared fetch half of ``files.fetch_url`` and ``files.download``:
    reject a non-http(s) scheme and parse the optional checksum *before* any
    fetch, so a typo fails fast at the call line rather than after a network
    round-trip, then verify the digest against the raw bytes. The body is
    returned undecoded -- ``fetch_url`` applies its text-only UTF-8 decode on
    top, while ``download`` falls back to install's binary path when the
    decode fails. Tests replace ``_fetch_url_bytes`` to stay offline.
    """
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"url must be an http(s) URL, got: {url!r}")
    expected = _parse_checksum(checksum) if checksum is not None else None

    data = await asyncio.to_thread(_fetch_url_bytes, url)
    if expected is not None:
        algorithm, digest = expected
        actual = hashlib.new(algorithm, data).hexdigest().lower()
        if actual != digest:
            raise ValueError(
                f"checksum mismatch for {url}: expected {algorithm} {digest}, "
                f"got {actual}"
            )
    return data


async def fetch_url(url: str, *, checksum: str | None = None) -> str:
    """Fetch an ``http(s)`` URL control-side and return its body as text.

    The control-side GET helper -- Ansible's ``uri`` with ``return_content:
    true`` mapped to a plain coroutine. It is **not an operation**: call it
    from ``main()`` (or any control-side code), never through ``op()``, and
    freeze the returned text into the per-host plan the deploy consumes --
    the fetch-once-then-freeze recipe that keeps both deploy phases
    deterministic. ``files.download`` is the install-at-a-path sibling and
    shares the scheme guard and checksum contract through
    ``_fetch_url_verified``; unlike this helper it also installs a binary
    body (issue #23), so a binary URL belongs there, not here.

    Args:
        url (str): The ``http(s)`` URL to fetch control-side.
        checksum (str | None, optional): An Ansible-style
            ``"<algorithm>:<hexdigest>"`` (eg ``"sha384:9f2c..."``) the fetched
            bytes must match; a mismatch raises ``ValueError`` before the
            caller sees the content. Defaults to None (fetch without
            verification).

    Returns:
        str: The response body decoded as UTF-8.

    **Raises:**

    ```python
    OSError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/files/files_fetch_url.py``.
    """

    # The uri ``return_content: true`` GET shape: the *control* machine opens
    # the connection (``urllib`` in a worker thread, the same seam
    # ``files.download`` rides), so the fetch works when only the control side
    # has internet egress and the target never needs ``curl``/``wget``. Unlike
    # an operation body -- which runs once per phase per host -- this helper
    # runs exactly when the caller awaits it, so the determinism recipe is to
    # fetch once in ``main()`` and freeze the text into the plan (a checksum
    # pin then makes any per-phase re-fetch byte-identical). On-target HTTP
    # stays a ``Shell`` action: no command execution inside deploys.
    #
    # The guard order is download's, shared through ``_fetch_url_verified``:
    # reject a non-http(s) scheme and parse the optional checksum *before*
    # any fetch, so a typo fails fast at the call line rather than after a
    # network round-trip; verify the digest against the raw bytes before
    # decoding, so a mismatch raises before the caller sees the content.
    # This helper stays text only -- a binary body (a ``.phar``, a ``.rpm``,
    # a tarball) raises ``ValueError``; installing one at a path is
    # ``files.download``'s or ``files.install(content=<bytes>)``'s job
    # (issue #23). An unreachable URL or a non-2xx response raises
    # ``urllib.error.URLError``/``HTTPError`` (both ``OSError`` subclasses).
    # Tests replace ``_fetch_url_bytes`` to stay offline.
    data = await _fetch_url_verified(url, checksum)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"{url} is not valid UTF-8 text; files.fetch_url returns text "
            "content only (a binary body -- a .phar, a .rpm, a tarball -- "
            "installs through files.download or files.install with bytes "
            "content)"
        ) from exc


async def download(
    ctx: OpContext,
    url: str,
    dest: str,
    mode: str = "0644",
    checksum: str | None = None,
    user: str | None = None,
    group: str | None = None,
    validate: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Download a text or binary file from a URL to a (root-owned) path.

    Args:
        ctx (OpContext): Execution context for the operation.
        url (str): The ``http(s)`` URL to fetch control-side.
        dest (str): The remote path to install the downloaded content at.
        mode (str, optional): The installed file's permissions as octal-digit
            text (``"0644"``, ``"600"``). Defaults to "0644".
        checksum (str | None, optional): An Ansible-style
            ``"<algorithm>:<hexdigest>"`` (eg ``"sha256:9f2c..."``) the fetched
            bytes must match; a mismatch raises ``ValueError`` before anything
            is written. Pinning it is what makes the two-phase fetch
            deterministic -- both phases verify the same digest, so the plan
            and the apply install byte-identical content. Defaults to None
            (fetch without verification).
        user (str | None, optional): The user to own the installed file.
            Defaults to None (root, the elevated install's owner).
        group (str | None, optional): The group to own the installed file.
            Defaults to None (root).
        validate (str | None, optional): A command template run on the spooled
            ``/tmp`` file between the transfer and the install, forwarded
            straight to ``files.install``; a non-zero exit aborts the install
            and leaves the destination untouched. The ``{spool}``/``{dest}``
            placeholders are substituted with the shell-quoted paths.
            Defaults to None (no validation).
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The spool transfer and the elevated install command, only when
            the downloaded content differs from the file's current content.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, OSError, ValueError
    ```

    Examples:
        ```python
        from reemote import op, files

        # Download a text artifact control-side and install it at a root-owned
        # path; ``checksum`` pins both deploy phases to the same bytes.
        yield op(
            files.download,
            name="Fetch the repo signing key",
            url="https://example.com/keys/repo.gpg",
            dest="/etc/apt/trusted.gpg.d/repo.gpg",
            checksum=(
                "sha256:5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
            ),
            mode="0644",
            sudo=True,
        )

        # A binary body (``.phar``, ``.rpm``, tarball) rides install's sha1
        # path; give it a login-readable mode so the read-back does not
        # re-download every deploy.
        yield op(
            files.download,
            name="Install the composer launcher",
            url="https://getcomposer.org/composer.phar",
            dest="/usr/local/bin/composer.phar",
            mode="0755",
            sudo=True,
        )
        ```

        Examples can be found in ``examples/modules/files/files_download.py``.
    """

    # The general download-to-path operation, and the answer to the friction
    # every package-repository role hits twice (a GPG key + a ``.repo``/
    # ``.sources`` file pulled from a vendor URL): instead of pushing the fetch
    # onto the caller -- the hand-rolled ``urllib.request`` + ``io.BytesIO``
    # recipe ``dnf.key``/``apt.key``/``gpg.key`` force, since each *rejects* a
    # URL ``src`` -- this takes the URL and lands it at a root-owned path in
    # ONE ``sudo=True`` request. It is the general URL fetch the package
    # modules point a text ``.repo``/key-file download at: ``dnf.repo``/
    # ``dnf.rpm``/``apt.deb`` reject a URL ``src`` because they build or
    # install a host-side (binary) file, not because reemote cannot download.
    #
    # The write is delegated to ``files.install`` (the project's rule for a
    # derived content-producing files operation) and the fetch half to
    # ``_fetch_url_verified`` (the scheme guard and checksum contract
    # ``files.fetch_url`` also rides, so they are shared, not duplicated):
    # the URL is fetched *control-side*, the optional checksum is verified
    # against the raw bytes, and the body is decoded to text when it is
    # UTF-8 -- otherwise the raw bytes ride install's binary path (issue
    # #23). install re-reads the destination (``FileContents`` for text,
    # ``RemoteFileSha1`` for bytes -- served from the same phase
    # ``FactCache``, no second host round-trip), yields nothing when the
    # content already matches, validates ``mode`` (raising ``ValueError``
    # on a non-octal-digit string), derives the ``/tmp`` spool name from
    # ``dest``, spools over *unprivileged* scp and installs with an elevated
    # ``sh -c`` (``mkdir -p``/``cp``/optional ``chown``/``chmod``/``rm -rf``),
    # and applies the optional ``validate`` hook on the staged bytes. So a
    # download inherits install's content-only idempotence (a converged file
    # yields zero commands), its owner/mode handling and, for text, its
    # two-tier read-back (a file the login user cannot read is re-read
    # elevated, so a restrictive mode does not re-download every deploy).
    # The binary path adds one descriptor (``InstallBinarySpoolCommand``),
    # handled by the same ``SimulatedHost`` install seam.
    #
    # The fetch is control-side, not host-side: the *control* machine opens the
    # connection (``urllib`` in a worker thread), so the download works when
    # only the control side has internet egress and the target never needs
    # ``curl``/``wget``. The cost is that the body runs once per phase (prepare,
    # then apply) per host, so the URL is fetched twice per host. That is
    # harmless for the small text artifacts this targets -- and *deterministic*
    # when ``checksum`` is passed, because both phases verify the same pinned
    # digest and so install the same bytes. Without a checksum the request
    # sequence still matches across phases (``url``/``dest``/``mode``/
    # ``checksum`` are hashable scalars, so no ``DeterminismError``), but a URL
    # whose body changes mid-run would plan one content and install another:
    # pass ``checksum`` to pin it. ``url`` must be ``http(s)`` -- a ``file:``/
    # ``ftp:`` scheme raises ``ValueError`` before any fetch.
    #
    # Text or binary (issue #23, full Ansible ``get_url`` parity): a UTF-8
    # body is decoded and rides install's text path (``FileContents``
    # idempotence); a binary body (a ``.phar`` launcher, a ``.rpm``) is
    # handed to install as ``bytes`` and rides its binary path
    # (``RemoteFileSha1`` idempotence, ``InstallBinarySpoolCommand`` spool)
    # -- never the text-decode ``ValueError``. A binary install's sha1
    # read-back is unprivileged, so give it a login-readable mode (the
    # ``0755`` launcher shape). ``/etc`` and ``/usr/local/bin`` targets are
    # root-owned, so flag the request ``sudo=True`` (or ``su=True``); the
    # spool transfer stays unprivileged regardless. ``**kwargs`` absorbs
    # any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    data = await _fetch_url_verified(url, checksum)
    try:
        content: str | bytes = data.decode("utf-8")
    except UnicodeDecodeError:
        # A binary body: install's bytes path spools and installs it
        # without any decode, idempotent on the remote file's sha1.
        content = data

    async for command in install(
        ctx,
        content,
        dest,
        mode=mode,
        user=user,
        group=group,
        validate=validate,
        **kwargs,
    ):
        yield command


# The archive formats ``files.unarchive`` extracts, ported from the baseline's
# ``_TAR_FORMATS``/``_ZIP_FORMATS`` tables: the extension -> tar extraction
# flags mapping (unzip always rides its ``-o`` overwrite flag). Matching is on
# the archive's *basename*, case-insensitively, so a URL's query string never
# hides the format.
_TAR_FORMATS: dict[str, tuple[str, ...]] = {
    ".tar": ("-x",),
    ".tar.gz": ("-xz",),
    ".tgz": ("-xz",),
    ".tar.bz2": ("-xj",),
    ".tbz2": ("-xj",),
    ".tar.xz": ("-xJ",),
    ".txz": ("-xJ",),
    ".tar.zst": ("-x", "--zstd"),
}
_ZIP_FORMATS = (".zip",)
_ARCHIVE_EXTENSIONS = tuple(_TAR_FORMATS) + _ZIP_FORMATS


def _archive_format(name: str) -> tuple[str, tuple[str, ...]] | None:
    """The extraction tool and flags for an archive basename, or None.

    Ports the baseline's ``_get_archive_format``: a ``.tar``-family extension
    selects ``tar`` with the compression flag the suffix names, ``.zip``
    selects ``unzip -o``, and anything else is unsupported (the operation
    raises ``ValueError`` before any fetch or fact read).
    """
    lower = name.lower()
    for ext, flags in _TAR_FORMATS.items():
        if lower.endswith(ext):
            return "tar", flags
    for ext in _ZIP_FORMATS:
        if lower.endswith(ext):
            return "unzip", ("-o",)
    return None


def _unarchive_spool_path(url: str) -> str:
    """The deterministic ``/tmp`` spool file a URL-sourced unarchive uploads to.

    The ``_install_spool_path`` collision rule applied to an archive URL: the
    sha1 prefix of the URL keeps two different sources from sharing (and
    clobbering) one spool while the name stays identical in both phases, and
    the URL path's basename is appended so the spool keeps its extension --
    the extraction derives its tool and flags from the *spool* path's suffix.
    A top-level ``/tmp`` file (no subdirectory): the paired
    ``RemoveTreeCommand`` removes it completely after the extraction. Like
    install's spool, the name avoids collisions, not a hostile local user.
    """
    digest = hashlib.sha1(url.encode()).hexdigest()[:16]
    name = PurePosixPath(urllib.parse.urlparse(url).path).name
    return f"/tmp/reemote-unarchive-{digest}-{name}"


@dataclass(frozen=True)
class UnarchiveSpoolCommand(TransferCommand):
    """Spool a URL-fetched archive's bytes to a ``/tmp`` path on the target.

    The first step of a URL-sourced ``files.unarchive`` and a *non-shell*
    descriptor: the copy rides the connection's scp channel via
    ``Host.upload_file``, so it has no ``shell()`` and its loggable form is
    the ``__str__`` rendering ``command_shell`` falls back to. Unlike
    ``InstallSpoolCommand`` the content is raw ``bytes`` -- an archive is
    binary, and bytes are hashable and equal across phases -- wrapped in a
    fresh ``io.BytesIO`` only at transfer time, so no identity-stable stream
    is needed at the call site. The copy rides as the login user (transfers
    are unprivileged, so the request's ``sudo`` does not apply to it), which
    is why the spool lives in world-writable ``/tmp`` rather than the
    possibly root-owned destination directory.
    """

    content: bytes  # the fetched archive body (binary-safe, never decoded)
    dest: str  # spool file path on the target host (a sha1-derived /tmp name)

    def __str__(self) -> str:
        return f"scp <files unarchive> -> {self.dest}"

    async def transfer(self, host: Host) -> None:
        await host.upload_file(io.BytesIO(self.content), self.dest)


@dataclass(frozen=True)
class UnarchiveCommand(Command):
    """Extract an archive into a directory (``tar``/``unzip`` shell text).

    The tool and its flags derive from ``src``'s extension through
    ``_archive_format`` (``tar -xz ... -f src -C dest`` for the tar family,
    ``unzip -o ... src -d dest`` for a zip). ``extra_opts`` ride between the
    flags and the archive path; tar keeps ``-f`` adjacent to the path so an
    extra is never mistaken for it, the baseline's ordering kept. Paths and
    extras are shell-quoted. An unsupported extension raises ``ValueError``
    (the operation validates first, so this is a direct-construction guard).
    """

    src: str  # the archive path on the target (remote src or the /tmp spool)
    dest: str  # the directory to extract into
    extra_opts: tuple[str, ...] = ()  # additional extract-command arguments

    def shell(self) -> str:
        fmt = _archive_format(PurePosixPath(self.src).name)
        if fmt is None:
            raise ValueError(
                f"unsupported archive format: {self.src!r} "
                f"(supported: {', '.join(_ARCHIVE_EXTENSIONS)})"
            )
        tool, flags = fmt
        parts = [tool, *flags, *(shlex.quote(opt) for opt in self.extra_opts)]
        src = shlex.quote(self.src)
        dest = shlex.quote(self.dest)
        if tool == "tar":
            parts.extend(["-f", src, "-C", dest])
        else:
            parts.extend([src, "-d", dest])
        return " ".join(parts)


async def unarchive(
    ctx: OpContext,
    src: str,
    dest: str,
    creates: str | None = None,
    extra_opts: tuple[str, ...] = (),
    user: str | None = None,
    group: str | None = None,
    checksum: str | None = None,
    mode: int | str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Extract an archive into a remote directory, gated by ``creates``.

    Args:
        ctx (OpContext): Execution context for the operation.
        src (str): The archive to extract: a remote path already on the
            target, or an ``http(s)`` URL fetched control-side as binary
            bytes and spooled to ``/tmp`` first.
        dest (str): The remote directory to extract into, created (with its
            parents) when absent.
        creates (str | None, optional): A path whose existence makes the
            operation a no-op -- Ansible's ``creates:`` idempotence, resolved
            in-operation through the ``FileExists`` fact (which sees files
            and directories alike). Defaults to None (always extract, the
            baseline's stateless behaviour).
        extra_opts (tuple[str, ...], optional): Additional arguments for the
            extract command (eg ``("--strip-components=1",)``), placed
            between the tool's flags and the archive path. Defaults to ().
        user (str | None, optional): The user to own the extracted tree
            (a recursive ``chown`` of ``dest`` after the extraction).
            Defaults to None (the archive's stored owners are kept).
        group (str | None, optional): The group to own the extracted tree.
            Defaults to None.
        checksum (str | None, optional): An Ansible-style
            ``"<algorithm>:<hexdigest>"`` (eg ``"sha256:9f2c..."``) a
            URL-fetched body must match; a mismatch raises ``ValueError``
            before anything is staged. Pinning it makes the two-phase fetch
            deterministic. Ignored for a remote-path ``src``. Defaults to
            None (fetch without verification).
        mode (int | str | None, optional): The extracted tree's permissions --
            an octal-digit int or string (``755``, ``"0755"``, ``"0o755"``) or
            a symbolic mode -- applied with a recursive ``chmod`` of ``dest``
            after the extraction (Ansible's ``unarchive`` ``mode:``). Runs
            after the ownership step, because a chown clears mode bits.
            Defaults to None (the archive's stored modes are kept).
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The directory creation, spool transfer, extraction, spool
            cleanup, ownership and mode commands, only when ``creates`` does
            not already exist.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, OSError, ValueError
    ```

    Examples:
        ```python
        from reemote import op, files

        # Extract a remote archive into a directory (created when absent),
        # gated by ``creates`` so a converged host reports no change (Ansible
        # ``unarchive`` parity).
        yield op(
            files.unarchive,
            name="Unpack the release tarball",
            src="/srv/releases/app-1.2.3.tgz",
            dest="/srv/app",
            creates="/srv/app/RELEASE",
            sudo=True,
        )

        # Fetch an http(s) archive control-side, spool it to ``/tmp``, extract
        # with extra tar options and chown the extracted tree; ``checksum``
        # pins both phases to identical bytes.
        yield op(
            files.unarchive,
            name="Unpack the upstream source",
            src="https://example.com/src/app-1.2.3.tar.gz",
            dest="/opt/app",
            creates="/opt/app/README.md",
            extra_opts=("--strip-components=1",),
            user="www-data",
            group="www-data",
            checksum=(
                "sha256:5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
            ),
            sudo=True,
        )

        # Extract a vendor tarball and hand the extracted tree a fixed mode --
        # Ansible's ``unarchive`` ``mode:`` (the geerlingguy.helm install).
        # ``chmod -R`` covers everything under ``dest``, so extract into a
        # dedicated directory rather than a shared one such as ``/tmp``.
        yield op(
            files.unarchive,
            name="Unpack the helm release",
            src="https://get.helm.sh/helm-v3.2.1-linux-amd64.tar.gz",
            dest="/usr/local/src/helm",
            creates="/usr/local/src/helm/linux-amd64",
            mode="0755",
            sudo=True,
        )
        ```

        Examples can be found in ``examples/modules/files/files_unarchive.py``.
    """

    # pyinfra ``files.unarchive`` parity, with the issue #21 resolution of the
    # controller-path conflict. pyinfra's ``remote_src=False`` default uploads
    # a *controller-side path* to a temp file first -- a shape reemote's
    # IO-only transfer decision deliberately does not have. Instead ``src`` is
    # auto-detected: an ``http(s)`` URL is fetched *control-side* as binary
    # bytes (the ``_fetch_url_bytes`` seam ``files.fetch_url`` rides, without
    # its UTF-8 decode -- a ``.tgz``/``.zip`` body never raises the text-only
    # ``ValueError``), spooled to a URL-derived ``/tmp`` name over unprivileged
    # scp, extracted, and the spool removed; any other ``src`` is a remote path
    # extracted in place and left alone (pyinfra's ``remote_src=True``). The
    # ``remote_src`` option itself is therefore dropped -- the scheme decides.
    #
    # ``creates`` is Ansible's ``unarchive`` ``creates:`` gate (the
    # php-xdebug source-tarball shape): the ``FileExists`` fact -- ``test -e``,
    # so it sees the extracted *directory* Ansible names, not just regular
    # files -- makes a converged host report ``changed=False`` with zero
    # commands, and skips the fetch entirely. Without it the operation is
    # stateless like pyinfra's: extraction always yields and always reports
    # changed (there is no fact that can compare an extracted tree).
    #
    # ``dest`` is created when absent (a ``MkdirCommand``, the issue's
    # requirement -- pyinfra instead raises on a non-existing directory); a
    # ``dest`` held by a non-directory raises ``ValueError``. A remote ``src``
    # that is not a regular file raises in every phase. An *absent* ``src``
    # raises in an applying phase, and reports a plan line when the context is
    # planning (issue #73): the same deploy may stage it first -- a spooled
    # controller asset, the Ansible ``unarchive`` shape -- and a preview sends
    # nothing, so strictness there only made the flow unpreviewable. The
    # siblings ``files.move``/``files.copy`` degrade the same way. The format
    # is validated from the basename before any fetch or fact read, and the
    # optional ``checksum`` (a reemote addition, the ``files.download``
    # contract) is parsed before the fetch
    # and verified against the raw bytes before anything is staged -- the
    # body runs once per phase, so the pin is what keeps both phases'
    # ``UnarchiveSpoolCommand`` bytes identical.
    #
    # ``extra_opts`` is a tuple, not pyinfra's list (``OperationRequest``
    # arguments must be hashable). ``user``/``group`` yield a recursive
    # ``ChownCommand`` on ``dest`` after the extraction, pyinfra's ownership
    # step; the named account must already exist. ``mode`` (issue #58, the
    # Ansible ``unarchive`` ``mode:``) yields a recursive ``ChmodCommand`` on
    # the extracted tree, normalised through ``_ensure_mode_int`` and -- like
    # the ``files.copy``/``files.move`` metadata half -- emitted
    # unconditionally whenever the extraction runs, so both phases yield the
    # identical request sequence and convergence stays ``creates``-scoped: a
    # mode-only drift on an already-extracted tree is *not* re-chmodded, because
    # no fact enumerates the extracted members. Like ``files.directory``'s
    # ``recursive``, the mode covers everything under ``dest``, not only the
    # archive's members -- extract into a dedicated directory. The chmod runs
    # after the chown (a chown clears setuid/setgid bits, the ``files.install``
    # ordering). The extraction, mkdir,
    # cleanup, chown and chmod are shell text, so flag the request ``sudo=True``
    # for a root-owned ``dest``; the spool transfer stays unprivileged regardless.
    # ``**kwargs`` absorbs any further global arguments (``op()`` already
    # strips ``name``/``sudo``/``su``).
    mode = _ensure_mode_int(mode)
    is_url = src.startswith(("http://", "https://"))
    name = (
        PurePosixPath(urllib.parse.urlparse(src).path).name
        if is_url
        else PurePosixPath(src).name
    )
    if _archive_format(name) is None:
        raise ValueError(
            f"unsupported archive format for {src!r} "
            f"(supported: {', '.join(_ARCHIVE_EXTENSIONS)})"
        )
    expected = _parse_checksum(checksum) if checksum is not None else None

    if creates is not None and await ctx.facts.get(FileExists, creates):
        ctx.reason = f"archive already extracted ({creates} exists)"
        return

    reasons: list[str] = []
    if is_url:
        data = await asyncio.to_thread(_fetch_url_bytes, src)
        if expected is not None:
            algorithm, digest = expected
            actual = hashlib.new(algorithm, data).hexdigest().lower()
            if actual != digest:
                raise ValueError(
                    f"checksum mismatch for {src}: expected {algorithm} {digest}, "
                    f"got {actual}"
                )
        archive = _unarchive_spool_path(src)
    else:
        info = await ctx.facts.get(File, src)
        if info is None:
            if ctx.planning:
                ctx.changed = True
                ctx.reason = (
                    f"src {src} absent at plan time; extraction planned "
                    "once it is staged"
                )
                return
            raise ValueError(f"src {src} does not exist")
        if info is False:
            raise ValueError(f"src {src} is not a regular file")
        archive = src

    dest_info = await ctx.facts.get(Directory, dest)
    if dest_info is False:
        raise ValueError(f"dest {dest} is not a directory")
    if dest_info is None:
        reasons.append(f"{dest} missing, will create")
        yield MkdirCommand(path=dest)

    if is_url:
        yield UnarchiveSpoolCommand(content=data, dest=archive)
    reasons.append(f"will extract {src} into {dest}")
    yield UnarchiveCommand(src=archive, dest=dest, extra_opts=tuple(extra_opts))
    if is_url:
        yield RemoveTreeCommand(src=archive)
    if user or group:
        reasons.append(f"will set ownership of {dest} recursively")
        yield ChownCommand(path=dest, user=user, group=group, recursive=True)
    if mode:
        reasons.append(f"will set mode {mode} of {dest} recursively")
        yield ChmodCommand(path=dest, mode=mode, recursive=True)

    ctx.changed = True
    ctx.reason = "; ".join(reasons)


@dataclass(frozen=True)
class MoveCommand(Command):
    """Move a remote path to a remote directory or file path (``mv src dest``).

    ``dest`` is passed verbatim: an existing directory receives
    ``basename(src)``, any other path is the full target path (the rename
    form ``files.move`` gained with issue #22) -- ``mv`` itself draws the
    same distinction.
    """

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
    mode: int | str | None = None,
    user: str | None = None,
    group: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Move a remote file/directory/link to a remote directory or file path.

    Args:
        ctx (OpContext): Execution context for the operation.
        src (str): The remote path to move.
        dest (str): Where to move ``src``: an existing remote *directory*
            (the moved path becomes ``dest/basename(src)``), or any other
            path, treated as the full target path -- a **rename**
            (``mv src dest``, the Ansible ``copy remote_src`` shape).
            The target's parent directory must exist.
        overwrite (bool, optional): When the destination path already exists,
            remove it first (``rm -rf``) instead of raising. Defaults to False.
        mode (int | str | None, optional): The moved path's permissions --
            an octal-digit int or string (``750``, ``"0750"``, ``"0o750"``)
            or a symbolic mode -- applied with ``chmod`` after the move.
            Defaults to None (the source's mode is kept).
        user (str | None, optional): The user to own the moved path
            (a ``chown`` after the move, before the ``chmod``).
            Defaults to None (the source's owner is kept).
        group (str | None, optional): The group to own the moved path.
            Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The move command, always, preceded by a removal when
            ``overwrite`` clears an existing target and followed by the
            requested ``chown``/``chmod``.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        ```python
        from reemote import op, files

        # Move into an existing directory -- the moved path becomes
        # ``dest/basename(src)``.
        yield op(
            files.move,
            name="Rotate the build artifact",
            src="/tmp/build/app.tar.gz",
            dest="/srv/releases",
            sudo=True,
        )

        # Rename (the target's parent must exist): clear an existing
        # destination with ``overwrite=True`` and adjust owner/mode after the
        # ``mv`` (chown runs before chmod so setuid/setgid survive).
        yield op(
            files.move,
            name="Publish the release",
            src="/srv/releases/app-1.2.3.tar.gz",
            dest="/srv/releases/current.tar.gz",
            overwrite=True,
            user="www-data",
            group="www-data",
            mode="0644",
            sudo=True,
        )
        ```

        Examples can be found in ``examples/modules/files/files_move.py``.
    """

    # Mirrors the baseline's ``files.move``, extended with the issue #22
    # rename/metadata parity. Stateless like the baseline: there is no
    # no-op branch -- a valid move always yields a ``MoveCommand`` (preceded
    # by a ``RemoveTreeCommand`` when ``overwrite`` clears an existing
    # target), so ``ctx.changed`` is always True. Repeating the same move
    # fails at apply when ``mv`` exits non-zero on the now-absent source
    # (``CommandError``, fail-fast); for an idempotent remote-to-remote
    # install use ``files.copy`` with ``overwrite=True``.
    #
    # ``dest`` is auto-detected (issue #22): an existing *directory*
    # (``Directory(dest)`` truthy) receives ``basename(src)``, the baseline's
    # only form; any other path is the full target path, so ``mv`` renames.
    # The rename form validates the target's *parent* directory instead --
    # an absent or non-directory parent raises ``ValueError``, keeping the
    # validate-in-both-phases strictness the directory form always had.
    #
    # Reads ``File(src)`` (a directory or symlink reads False, which the baseline
    # accepts -- only absence is rejected), ``Directory(dest)`` and
    # ``File(full_dest)``. An existing target without ``overwrite`` raises
    # ``ValueError`` in every phase. An absent ``src`` raises in an applying
    # phase and reports a plan line when the context is planning, the
    # baseline's phase-aware soft handling of that one case (issue #73), so a
    # "create a file then move it" flow previews in one deploy; a full
    # ``run()`` prepare stays strict, so such a flow still spans two runs when
    # it might be applied (see the ``files.edit`` + ``files.move``
    # ``/etc/hosts`` recipe example).
    #
    # ``mode``/``user``/``group`` (issue #22) yield a ``ChownCommand`` and/or
    # ``ChmodCommand`` on the moved path after the ``mv`` -- unconditionally,
    # because the stateless move always reports changed. The chown precedes
    # the chmod (a chown clears setuid/setgid bits, the ``files.install``
    # ordering); the mode is normalised through ``_ensure_mode_int`` and the
    # named account must already exist.
    #
    # All commands are shell text, so flagging the request ``sudo=True``
    # elevates the whole move -- the vehicle for installing a login-user
    # spool file into a root-owned directory such as ``/etc``. ``**kwargs``
    # absorbs any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    mode = _ensure_mode_int(mode)
    src_info = await ctx.facts.get(File, src)
    if src_info is None:
        if ctx.planning:
            ctx.changed = True
            ctx.reason = (
                f"src {src} absent at plan time; move planned once it is staged"
            )
            return
        raise ValueError(f"src {src} does not exist")

    dest_info = await ctx.facts.get(Directory, dest)
    if dest_info:
        full_dest = posixpath.join(dest, PurePosixPath(src).name)
    else:
        full_dest = dest
        parent = posixpath.dirname(dest)
        if parent:
            parent_info = await ctx.facts.get(Directory, parent)
            if parent_info is False:
                raise ValueError(f"dest parent {parent} is not a directory")
            if parent_info is None:
                raise ValueError(f"dest directory {parent} does not exist")

    reasons: list[str] = []
    if await ctx.facts.get(File, full_dest) is not None:
        if not overwrite:
            raise ValueError(
                f"dest {full_dest} already exists and `overwrite` is unset"
            )
        reasons.append(f"{full_dest} present, will remove first")
        yield RemoveTreeCommand(src=full_dest)

    if dest_info:
        reasons.append(f"will move {src} into {dest}")
    else:
        reasons.append(f"will move {src} to {full_dest}")
    ctx.changed = True
    ctx.reason = "; ".join(reasons)
    yield MoveCommand(src=src, dest=dest)
    if user or group:
        yield ChownCommand(path=full_dest, user=user, group=group)
    if mode:
        yield ChmodCommand(path=full_dest, mode=mode)


@dataclass(frozen=True)
class CopyCommand(Command):
    """Copy a remote path into a directory or onto a file path (``cp -r [-f] src dest``).

    ``dest`` is passed verbatim: an existing directory receives
    ``basename(src)``, any other path is the full target path (the rename
    form ``files.copy`` gained with issue #22) -- ``cp`` itself draws the
    same distinction. Unlike ``files.move``'s overwrite branch there is no
    ``rm -rf`` prelude: the baseline's copy expresses overwrite as ``cp``'s
    own ``-f`` flag, so one descriptor with an ``overwrite`` field covers
    both forms.
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
    mode: int | str | None = None,
    user: str | None = None,
    group: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Copy a remote file/directory to a remote directory or file path.

    Ansible's ``copy`` with ``remote_src: true``: the copy renames, sets
    metadata and converges idempotently on content drift, in one request.

    Args:
        ctx (OpContext): Execution context for the operation.
        src (str): The remote path to copy; the source stays in place.
        dest (str): Where to copy ``src``: an existing remote *directory*
            (the copied path becomes ``dest/basename(src)``), or any other
            path, treated as the full target path -- a **rename**
            (``cp src dest``). The target's parent directory must exist.
        overwrite (bool, optional): When the destination path already exists
            with *different* content, force the copy (``cp -r -f``) instead
            of raising. Sha1-gated: equal content is still a no-op, so a
            drifted target converges once and a repeat reports no change
            (Ansible's ``force: yes`` idempotence). Defaults to False.
        mode (int | str | None, optional): The target's permissions --
            an octal-digit int or string (``644``, ``"0644"``, ``"0o644"``)
            or a symbolic mode. Applied with ``chmod`` after the copy, and
            converged against the ``File`` fact's metadata when the content
            is already equal. Defaults to None (the source's mode is kept).
        user (str | None, optional): The user to own the target (a ``chown``
            after the copy, before the ``chmod``; converged on a content
            no-op too). Defaults to None (the source's owner is kept).
        group (str | None, optional): The group to own the target.
            Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The copy command when the content differs (or no comparison
            is possible), followed by the requested ``chown``/``chmod``; on
            equal content only the metadata commands whose values differ.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        ```python
        from reemote import op, files

        # Copy into an existing directory; the sha1 comparison skips a no-op
        # copy, so a repeat against identical content reports no change.
        yield op(
            files.copy,
            name="Snapshot the release",
            src="/srv/releases/current.tar.gz",
            dest="/srv/releases/archive",
            sudo=True,
        )

        # Rename-copy with Ansible's ``force: yes`` semantics: a drifted
        # target converges once, then reports no change; mode/user/group
        # converge even on a content no-op.
        yield op(
            files.copy,
            name="Publish the site root",
            src="/srv/releases/app-1.2.3",
            dest="/var/www/html",
            overwrite=True,
            user="www-data",
            group="www-data",
            mode="0755",
            sudo=True,
        )
        ```

        Examples can be found in ``examples/modules/files/files_copy.py``.
    """

    # Mirrors the baseline's ``files.copy``, extended with the issue #22
    # Ansible remote-copy parity (rename, mode/owner, sha1-gated overwrite).
    # Unlike ``files.move`` there is no ``rm -rf`` prelude -- the baseline's
    # copy expresses overwrite as ``cp``'s own ``-f`` flag (which merges into
    # an existing directory target rather than replacing it).
    #
    # Reads ``Directory(src)`` and ``File(src)`` and requires one of them to
    # be truthy, exactly the baseline's check: a *symlink* source reads False for
    # both and raises, even though ``files.move`` accepts links (upstream's
    # own asymmetry, kept).
    #
    # ``dest`` is auto-detected (issue #22): an existing *directory*
    # (``Directory(dest)`` truthy) receives ``basename(src)``, the baseline's
    # only form; any other path is the full target path, so ``cp`` renames --
    # Ansible's ``copy remote_src`` with a file ``dest``. The rename form
    # validates the target's *parent* directory instead (absent or
    # non-directory raises ``ValueError``), keeping the validate-in-both-
    # phases strictness. ``File(full_dest)`` probes the target either way.
    #
    # Idempotence is sha1-gated in *both* overwrite modes (issue #22): an
    # existing regular-file target's ``RemoteFileSha1`` is compared against
    # the source's -- equal content skips the copy, different content raises
    # ``ValueError`` with ``overwrite`` unset and yields ``cp -r -f`` with it
    # set. So ``overwrite=True`` is Ansible's ``force: yes``: a drifted
    # target converges once, then reports no change (the baseline's
    # stateless always-changed overwrite is deliberately dropped). The
    # baseline's ``Sha1File`` -> ``Md5File`` -> ``Sha256File`` fallback chain
    # is deliberately reduced to the sha1-only ``RemoteFileSha1``, the same
    # single-hash precedent as ``files.put``/``files.get``. A target that is
    # not a regular file (``File`` reads False/None) or a hash that cannot be
    # read (a directory source, say) skips the comparison and the copy is
    # planned, the baseline's truthiness kept verbatim.
    #
    # ``mode``/``user``/``group`` (issue #22) converge the target's metadata
    # in the same request: after a planned copy they yield an unconditional
    # ``ChownCommand``/``ChmodCommand`` (the ``files.file`` create branch --
    # the copy inherits the source's metadata, so the request is the
    # authority); on an equal-content no-op they are compared against the
    # ``File`` fact's row and only the differing commands are yielded (the
    # ``files.file`` update branch), so a mode- or owner-only drift converges
    # without re-copying. The chown precedes the chmod (a chown clears
    # setuid/setgid bits, the ``files.install`` ordering); the mode is
    # normalised through ``_ensure_mode_int`` (a symbolic mode never equals
    # the fact's octal int, so its chmod re-runs every pass, the
    # ``files.file`` precedent) and the named account must already exist.
    #
    # An absent/non-directory rename parent and an existing *different* target
    # without ``overwrite`` raise ``ValueError`` in every phase. An absent
    # ``src`` raises in an applying phase and reports a plan line when the
    # context is planning -- the baseline's phase-aware soft handling of that
    # one case, the same trade made for ``files.move`` and
    # ``files.unarchive`` (issue #73) -- so a "create a file then copy it"
    # flow previews in one deploy. A full ``run()`` prepare stays strict, so
    # such a flow still spans two runs when it might be applied.
    #
    # The commands are shell text, so flagging the request ``sudo=True``
    # elevates the whole copy. ``**kwargs`` absorbs any further global
    # arguments (``op()`` already strips ``name``/``sudo``/``su``).
    mode = _ensure_mode_int(mode)
    src_is_dir = await ctx.facts.get(Directory, src)
    src_is_file = await ctx.facts.get(File, src)
    if not src_is_file and not src_is_dir:
        if ctx.planning and src_is_file is None and src_is_dir is None:
            # Both reads say "no such path", the absence case (a symlink reads
            # ``False`` for both and stays an error, upstream's asymmetry).
            ctx.changed = True
            ctx.reason = (
                f"src {src} absent at plan time; copy planned once it is staged"
            )
            return
        raise ValueError(f"src {src} does not exist")

    dest_info = await ctx.facts.get(Directory, dest)
    if dest_info:
        full_dest = posixpath.join(dest, PurePosixPath(src).name)
    else:
        full_dest = dest
        parent = posixpath.dirname(dest)
        if parent:
            parent_info = await ctx.facts.get(Directory, parent)
            if parent_info is False:
                raise ValueError(f"dest parent {parent} is not a directory")
            if parent_info is None:
                raise ValueError(f"dest directory {parent} does not exist")

    target_info = await ctx.facts.get(File, full_dest)
    copy_needed = True
    if target_info:
        src_sha1 = await ctx.facts.get(RemoteFileSha1, src)
        dest_sha1 = await ctx.facts.get(RemoteFileSha1, full_dest)
        if src_sha1 and dest_sha1 and src_sha1 == dest_sha1:
            copy_needed = False
        elif not overwrite:
            raise ValueError(
                f"{full_dest} already exists and is different than src"
            )

    reasons: list[str] = []
    changed = False
    if copy_needed:
        changed = True
        if dest_info:
            reasons.append(f"will copy {src} into {dest}")
        else:
            reasons.append(f"will copy {src} to {full_dest}")
        yield CopyCommand(src=src, dest=dest, overwrite=overwrite)
        if user or group:
            yield ChownCommand(path=full_dest, user=user, group=group)
        if mode:
            yield ChmodCommand(path=full_dest, mode=mode)
    else:
        # Equal content: converge only the metadata that differs, the
        # files.file update branch over the target's fact row.
        if (user and target_info["user"] != user) or (
            group and target_info["group"] != group
        ):
            changed = True
            reasons.append(
                f"owner {target_info['user']}:{target_info['group']} "
                "differs, will chown"
            )
            yield ChownCommand(path=full_dest, user=user, group=group)
        if mode and target_info["mode"] != mode:
            changed = True
            reasons.append(f"mode {target_info['mode']} differs, will chmod {mode}")
            yield ChmodCommand(path=full_dest, mode=mode)
        if not changed:
            reasons.append(f"{full_dest} already exists")

    ctx.changed = changed
    ctx.reason = "; ".join(reasons)


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
        ```python
        from reemote import op, files

        # Create a directory (parents included) with a specific owner and mode.
        yield op(
            files.directory,
            name="Prepare the deploy target",
            path="/srv/app/releases",
            user="deploy",
            group="deploy",
            mode="0755",
            sudo=True,
        )

        # Apply ownership and mode recursively across the whole tree.
        yield op(
            files.directory,
            name="Reharden the upload tree",
            path="/var/www/uploads",
            user="www-data",
            group="www-data",
            mode="0750",
            recursive=True,
            sudo=True,
        )

        # Remove the directory and everything under it -> rm -rf.
        yield op(
            files.directory,
            name="Drop the old build tree",
            path="/srv/app/old-releases",
            present=False,
            sudo=True,
        )
        ```

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
        ```python
        from reemote import op, files

        # Set ext2 attribute flags -> chattr +a,i /etc/passwd.daily
        # (idempotent on the ``lsattr -d`` read; a repeat where both letters
        # are already set is a no-op).
        yield op(
            files.flags,
            name="Freeze the backup target",
            path="/etc/passwd.daily",
            flags=("a", "i"),
            sudo=True,
        )

        # Clear the same flags -> chattr -a,i /etc/passwd.daily
        yield op(
            files.flags,
            name="Unfreeze the backup target",
            path="/etc/passwd.daily",
            flags=("a", "i"),
            present=False,
            sudo=True,
        )
        ```

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
    "HTPASSWD_SCHEMES",
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
    "FindInFiles",
    "FindLinks",
    "FoundFile",
    "GetCommand",
    "InstallBinarySpoolCommand",
    "InstallCommand",
    "InstallSpoolCommand",
    "InstallValidateCommand",
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
    "UnarchiveCommand",
    "UnarchiveSpoolCommand",
    "copy",
    "directory",
    "download",
    "edit",
    "fetch_url",
    "file",
    "flags",
    "get",
    "htpasswd",
    "htpasswd_entry",
    "htpasswd_hash",
    "htpasswd_scheme",
    "htpasswd_verify",
    "ini_option",
    "install",
    "link",
    "move",
    "put",
    "read_source_bytes",
    "transform",
    "unarchive",
    "write_dest_bytes",
]
