# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``sysctl`` module: facts and operations in one source.
#
# This namespace is the canonical home for everything sysctl. The ``Sysctl``
# runtime fact was relocated here from ``server`` (which no longer exports
# it -- the top-level ``reemote.Sysctl`` binding now points at this class),
# keeping its ``server.Sysctl`` parse verbatim. Two originals widen
# the visibility onto the *persistent* configuration: ``SysctlFile`` (the raw
# text of one sysctl config file, or None when absent) and ``SysctlFiles``
# (every ``/etc/sysctl.conf`` + ``/etc/sysctl.d/*.conf`` file parsed into
# key -> value dicts), so runtime-vs-config drift is observable.
#
# Operation side: ``setting`` manages one kernel parameter end to end --
# the runtime apply (``sysctl -w``, gated by the ``Sysctl`` fact) and the
# persistence edit (a keyed line replacement in the config file, gated by
# ``SysctlFile``) are independent halves, each switchable; ``conf`` manages a
# whole drop-in file from a tuple of settings pairs. Both write root-owned
# ``/etc`` paths through the spool-transfer + elevated-install pattern
# (``systemd.dropin`` / ``dnf.repo``): an unprivileged scp spools the content
# to a fixed ``/tmp`` path, then an elevated ``sh -c`` installs it ``0644``
# and removes the spool. A scoped ``sysctl -p <file>`` reload follows a
# changed write when ``reload`` is set. These operations are reemote
# originals superseding the example-only ``server_op_sysctl.py`` recipe
# (which stays as the ``server.sysctl`` demonstration).
#
# Like every namespace this one imports nothing from its siblings: the
# content facts read the same ``remote_files`` SimulatedHost seam
# ``files.FileContents`` does, and the runtime fact reads the same
# ``sysctls`` seam the relocated ``server.Sysctl`` always did.

import hashlib
import io
import re
import shlex
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from ...commands import Command, TransferCommand
from ...core import OpContext
from ...hosts import Host


def _try_int(value: str | int) -> int | str:
    """``int()`` that returns its input unchanged on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _normalize_value(value: str | int | list[str | int]) -> tuple[str, Any]:
    """The ``server.sysctl`` value normalization.

    Returns ``(string_value, normalized)``: ``string_value`` is the shell
    spelling (a list joins its items with single spaces), ``normalized`` is
    the comparison shape for the ``Sysctl`` fact -- each item ``try_int``-ed,
    a single-item list collapsing to a scalar, a whitespace-separated string
    splitting into a list, anything else a lone ``try_int``.
    """
    string_value = (
        " ".join(f"{v}" for v in value) if isinstance(value, list) else str(value)
    )
    if isinstance(value, list):
        normalized: Any = [_try_int(v) for v in value]
        if len(normalized) == 1:
            normalized = normalized[0]
    elif isinstance(value, str) and len(value.split()) > 1:
        normalized = [_try_int(v) for v in value.split()]
    else:
        normalized = _try_int(value)
    return string_value, normalized


def _parse_conf(text: str) -> dict[str, str]:
    """Parse sysctl config text into a key -> value dict (last assignment wins).

    Comment lines (``#`` or ``;``), blank lines and rows without a ``=`` are
    skipped; the value stays the stripped raw string -- unlike the runtime
    ``Sysctl`` fact there is no int coercion, because a config file records
    text and drift comparison against it is textual.
    """
    settings: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")) or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key:
            settings[key] = value.strip()
    return settings


def _edit_conf(content: str | None, key: str, string_value: str | None) -> str | None:
    """Keyed replacement/removal of one assignment in sysctl config text.

    ``string_value`` set: the first active ``key = ...`` line is replaced
    with ``key = string_value`` in place, any further active lines for the
    same key are dropped (duplicates collapse), and the line is appended
    when no active assignment exists. ``string_value`` None: every active
    ``key = ...`` line is removed. Commented lines are never touched --
    unlike the append-only ``files.line`` approach in the
    ``server_op_sysctl.py`` recipe, this is Ansible-style keyed editing,
    which is what makes ``present=False`` expressible. The result always
    ends in one trailing newline (a file missing it is rewritten once)
    and the edit is a fixpoint. An absent file (None) with nothing to remove stays None; an
    edit that empties the file returns "" (the file remains, empty).
    """
    if content is None and string_value is None:
        return None
    pattern = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=")
    result: list[str] = []
    replaced = False
    for line in (content or "").splitlines():
        if pattern.match(line):
            if string_value is None or replaced:
                continue
            result.append(f"{key} = {string_value}")
            replaced = True
        else:
            result.append(line)
    if string_value is not None and not replaced:
        result.append(f"{key} = {string_value}")
    return "\n".join(result) + "\n" if result else ""


# --- Facts -------------------------------------------------------------------


class Sysctl:
    """The sysctl settings, as a dict of key -> value.

    **Commands:**

    ```bash
    sysctl -a 2>/dev/null || true
    ```

    **Returns:**

    ```python
    dict[str, Any]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/sysctl/sysctl_sysctl.py``.
    """

    # ``sysctl -a`` dumps every setting
    # (or ``sysctl <key> ...`` when ``keys`` is given), with stderr discarded
    # and the exit swallowed (``2>/dev/null || true``) so an unknown key or a
    # missing binary never fails the read. Each row is ``key = value``
    # (Linux) or ``key: value`` (BSD); a row carrying neither separator is
    # skipped. A value made only of alphanumeric, ``_``, ``-``, ``.`` and
    # whitespace characters is split on whitespace and each token
    # int-converted when possible, a single token collapsing to a scalar;
    # anything else stays the stripped raw string. For example::
    #
    #     {
    #         "fs.inotify.max_queued_events": 16384,
    #         "fs.inode-state": [44565, 360],
    #     }
    #
    # A value that is only whitespace keeps ``""`` (it is truthy before the
    # strip); a row with nothing at all after the separator is skipped.
    # Empty output reads as ``{}`` (the empty-dict default).
    #
    # Relocated verbatim from ``server`` (the canonical-home decision): the
    # ``sysctls`` SimulatedHost seam is unchanged, and the ``setting``
    # operation's ``SysctlSetCommand`` arm now mutates it.

    #: Shell used to dump every sysctl setting over SSH.
    COMMAND = "sysctl -a 2>/dev/null || true"

    #: Values matching this pattern are whitespace-split and int-converted;
    #: anything else stays a raw string.
    _SIMPLE_VALUE_RE = re.compile(r"^[a-zA-Z0-9_\-\.\s]+$")

    @classmethod
    def command(cls, keys: list[str] | None = None) -> str:
        """Shell reads the named keys, or dumps every setting when None.

        Each key is ``shlex``-quoted, so a key carrying shell
        metacharacters is a literal lookup, never executed.
        """
        if keys is None:
            return cls.COMMAND
        quoted = " ".join(shlex.quote(key) for key in keys)
        return f"sysctl {quoted} 2>/dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, Any]:
        """Parse ``sysctl`` rows into the key -> value dict.

        Rows without a ``=`` or ``:`` separator are skipped; simple values
        become ints (or lists of ints/strings), everything else stays the
        stripped raw string. Pure and testable.
        """
        sysctls: dict[str, Any] = {}
        for line in output:
            if "=" in line:
                key, value = line.split("=", 1)
            elif ":" in line:
                key, value = line.split(":", 1)
            else:
                continue
            # Truthiness is tested BEFORE stripping: "key = " (a
            # whitespace-only value) keeps "", "key =" is skipped.
            if not (key and value):
                continue
            key = key.strip()
            value = value.strip()
            if cls._SIMPLE_VALUE_RE.match(value):
                tokens = [_try_int(token) for token in value.split()]
                sysctls[key] = tokens[0] if len(tokens) == 1 else tokens
            else:
                sysctls[key] = value
        return sysctls

    @classmethod
    async def process(
        cls, host: Host, keys: list[str] | None = None
    ) -> dict[str, Any]:
        sysctls = getattr(host, "sysctls", None)
        if sysctls is not None:
            # SimulatedHost seam: key -> parsed value. Copy any list value so
            # callers never mutate the simulated state; a keys filter selects
            # the named settings, and an unknown key reads as absent, like
            # the swallowed sysctl error.
            wanted = list(sysctls) if keys is None else keys
            return {
                key: list(sysctls[key])
                if isinstance(sysctls[key], list)
                else sysctls[key]
                for key in wanted
                if key in sysctls
            }
        # Real transport: the sysctl dump (or named keys) over asyncssh.
        stdout = await host.run_shell(cls.command(keys))
        return cls.parse(stdout.splitlines())


class SysctlFile:
    """The raw text of one sysctl config file, or None when it is absent.

    **Commands:**

    ```bash
    test -e <path> && { printf '#reemote-sysctl-file\\n'; cat -- <path>; }
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
        Examples can be found in ``examples/modules/sysctl/sysctl_sysctl_file.py``.
    """

    # A reemote original, the ``_SystemdConfFile`` / ``files.FileContents``
    # marker-then-cat pattern in-namespace (the zero-cross-module-import
    # invariant): the marker distinguishes an existing-but-empty file ("")
    # from a missing one (None). Reads the same ``remote_files`` SimulatedHost
    # seam the files namespace's content facts do, so a simulated install
    # reads back here. The idempotence fact both operations compare their
    # desired content against, and a public debugging/templating read for
    # any single sysctl config path (not restricted to the standard ones).

    #: Marker printed before the ``cat``'d content, only when ``test -e``
    #: passes, so ``process`` can tell an existing (possibly empty) file from
    #: a missing one (no marker on stdout).
    MARKER = "#reemote-sysctl-file"

    @classmethod
    def command(cls, path: str) -> str:
        """Shell printing ``MARKER`` then the file's content, only if it exists."""
        quoted = shlex.quote(path)
        return f"test -e {quoted} && {{ printf '{cls.MARKER}\\n'; cat -- {quoted}; }}"

    @classmethod
    async def process(cls, host: Host, path: str) -> str | None:
        remote_files = getattr(host, "remote_files", None)
        if remote_files is not None:
            # SimulatedHost seam (shared with files.FileContents): decode the
            # content the simulated remote filesystem holds at the path.
            data = remote_files.get(path)
            return data.decode("utf-8") if data is not None else None
        # Real transport: cat the file over asyncssh behind the marker, so a
        # missing file (no marker) reads as None rather than empty content.
        stdout = await host.run_shell(cls.command(path))
        prefix = f"{cls.MARKER}\n"
        if not stdout.startswith(prefix):
            return None
        return stdout[len(prefix) :]


class SysctlFiles:
    """The persistent sysctl configuration, as a dict of file path -> settings.

    **Commands:**

    ```bash
    for f in /etc/sysctl.conf /etc/sysctl.d/*.conf; do test -f "$f" && { printf '#reemote-sysctl-files %s\\n' "$f"; cat -- "$f"; }; done
    ```

    **Returns:**

    ```python
    dict[str, dict[str, str]]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/sysctl/sysctl_sysctl_files.py``.
    """

    # A reemote original. The shell walks the two standard persistence
    # locations -- the legacy ``/etc/sysctl.conf`` and the ``/etc/sysctl.d``
    # drop-in directory -- printing a per-file marker line before each
    # file's content, so one round-trip gathers every file. An unmatched
    # glob stays literal and fails ``test -f``, so a host without the
    # drop-in directory simply contributes no entries; vendor directories
    # (``/usr/lib/sysctl.d``, ``/run/sysctl.d``) are deliberately out of
    # scope -- this fact reports the *admin-managed* configuration, the
    # files the operations write. Each file parses with ``_parse_conf``:
    # comments (``#``/``;``), blanks and separator-less rows skipped, values
    # kept as stripped strings (no int coercion -- config text is textual),
    # last assignment wins within a file. A present file with no assignments
    # still appears, mapped to {}. Keys are sorted by path for a stable
    # ordering; empty output reads as {}. The SimulatedHost seam projects
    # the shared ``remote_files`` store filtered to the standard paths, so
    # a simulated install/removal is visible here immediately.

    #: Marker prefix printed before each file's content, followed by the
    #: file path, so ``parse`` can attribute rows to their file.
    MARKER = "#reemote-sysctl-files"

    #: Shell used to gather every standard sysctl config file over SSH.
    COMMAND = (
        'for f in /etc/sysctl.conf /etc/sysctl.d/*.conf; do '
        'test -f "$f" && { printf \'#reemote-sysctl-files %s\\n\' "$f"; '
        'cat -- "$f"; }; done'
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, dict[str, str]]:
        """Split marker-delimited file sections and parse each one's settings.

        Rows before the first marker are ignored; a file section with no
        assignment rows still yields an empty dict. Pure and testable.
        """
        files: dict[str, dict[str, str]] = {}
        path: str | None = None
        lines: list[str] = []
        prefix = f"{cls.MARKER} "
        for line in output:
            if line.startswith(prefix):
                if path is not None:
                    files[path] = _parse_conf("\n".join(lines))
                path = line[len(prefix) :]
                lines = []
            elif path is not None:
                lines.append(line)
        if path is not None:
            files[path] = _parse_conf("\n".join(lines))
        return files

    @classmethod
    async def process(cls, host: Host) -> dict[str, dict[str, str]]:
        remote_files = getattr(host, "remote_files", None)
        if remote_files is not None:
            # SimulatedHost seam: project the shared content store onto the
            # standard sysctl paths, sorted for a stable ordering. Each file
            # parses exactly as the real transport's section would.
            paths = sorted(
                path
                for path in remote_files
                if path == "/etc/sysctl.conf"
                or (path.startswith("/etc/sysctl.d/") and path.endswith(".conf"))
            )
            return {
                path: _parse_conf(remote_files[path].decode("utf-8")) for path in paths
            }
        # Real transport: the marker-delimited walk over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------


def _spool_path(dest: str) -> str:
    """The deterministic ``/tmp`` spool path the sysctl operations upload to.

    Both ``setting`` and ``conf`` spool through the same
    ``SysctlFileUploadCommand``/``SysctlFileInstallCommand`` pair, so they
    share this helper -- one spool per managed file, never two requests
    clobbering one another's upload. The name is derived from the managed
    path (``sha1(dest)[:16]``) rather than fixed, so it stays identical
    across the two deploy phases (a per-materialization temp name would
    diverge and trip the determinism guard) while two different files get
    two different spools. The content lands *inside* a sha1-derived ``/tmp``
    directory rather than at the top level of ``/tmp``, and the elevated
    install ``rm -rf``s the whole directory afterwards, so no leftover file
    lands in ``/tmp``. The directory is still world-writable ``/tmp`` so the
    *unprivileged* scp can write it; on a multi-tenant host the ``/tmp``
    spool is still a symlink-race exposure (the trade-off every
    spool-and-install operation here makes).
    """

    digest = hashlib.sha1(dest.encode()).hexdigest()[:16]
    return f"/tmp/reemote-sysctl-{digest}/sysctl.conf"


@dataclass(frozen=True)
class SysctlSetCommand(Command):
    """``sysctl -w <key>=<value>``: apply one setting to the running kernel.

    The runtime half of ``setting``, yielded only when the ``Sysctl`` fact
    differs from the normalized desired value. Both sides of the ``=`` are
    shell-quoted, so a multi-word value reaches the kernel as one argument.
    Needs root: flag the request ``sudo=True``. ``ignore_errors`` appends
    ``|| true`` so an unknown or read-only key does not abort the run (the
    cross-platform escape hatch); stderr stays visible either way.
    """

    key: str
    value: str
    ignore_errors: bool = False

    def shell(self) -> str:
        rendered = f"sysctl -w {shlex.quote(self.key)}={shlex.quote(self.value)}"
        return f"{rendered} || true" if self.ignore_errors else rendered


@dataclass(frozen=True)
class SysctlFileUploadCommand(TransferCommand):
    """Spool sysctl config content to a ``/tmp`` path on the target.

    The first half of the operations' write direction and a *non-shell*
    descriptor: it copies the content over the connection's scp channel via
    ``Host.upload_file`` rather than running shell text, so it has no
    ``shell()`` and its loggable form is the ``__str__`` rendering
    ``command_shell`` falls back to. Like ``SystemdFileUploadCommand`` the
    content is the text the operation writes (a plain ``str`` -- hashable and
    identical across phases), wrapped in a fresh ``io.BytesIO`` only at
    transfer time. The copy rides as the login user (transfers are
    unprivileged, so the request's ``sudo`` does not apply), which is why the
    spool path is world-writable ``/tmp`` rather than the root-owned ``/etc``
    directory.
    """

    content: str  # the config text the operation writes
    dest: str  # spool path on the target host

    def __str__(self) -> str:
        return f"scp <sysctl conf> -> {self.dest}"

    async def transfer(self, host: Host) -> None:
        # scp does not create parent directories, and the spool now lives
        # inside a sha1-derived /tmp directory rather than at the top level,
        # so mkdir -p it first. Unprivileged: /tmp is world-writable, so the
        # login user can create the directory, and a transfer never elevates.
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        await host.run_shell(f"mkdir -p {parent}")
        await host.upload_file(io.BytesIO(self.content.encode()), self.dest)


@dataclass(frozen=True)
class SysctlFileInstallCommand(Command):
    """Install the spooled config file ``0644``, then remove the spool dir.

    The second half of the operations' write direction and the elevated one:
    writing under ``/etc`` needs root, so the request is flagged ``sudo=True``
    and the whole sequence is wrapped in ``sh -c`` -- a bare ``sudo`` prefix
    would elevate only the first command of the ``&&`` chain and leave the
    cleanup running as the login user, whereas ``sudo sh -c '...'`` runs
    every step as root. The sequence creates the destination directory
    (``mkdir -p`` -- ``/etc/sysctl.d`` may not exist yet), copies the spooled
    content over the target file, chmods it ``0644`` and removes the whole
    sha1-derived ``/tmp`` spool directory -- the ``SystemdFileInstallCommand``
    pattern. All paths are shell-quoted.
    """

    spool: str  # where SysctlFileUploadCommand wrote the content
    dest: str  # the /etc/... config path

    def shell(self) -> str:
        spool = shlex.quote(self.spool)
        dest = shlex.quote(self.dest)
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        # The spool file lives inside a sha1-derived /tmp directory; remove
        # the whole directory so no leftover lands in /tmp.
        spool_dir = shlex.quote(str(PurePosixPath(self.spool).parent))
        inner = (
            f"mkdir -p {parent} && "
            f"cp {spool} {dest} && "
            f"chmod 0644 {dest} && rm -rf {spool_dir}"
        )
        return f"sh -c {shlex.quote(inner)}"


@dataclass(frozen=True)
class SysctlFileRemoveCommand(Command):
    """``rm -f <path>``: delete a sysctl config file.

    The removal mutation ``conf(present=False)`` yields when the ``SysctlFile``
    fact reports the file present. ``rm -f`` tolerates an absent path, and
    deleting under ``/etc`` needs root, so flag the request ``sudo=True`` (or
    ``su=True``). The path is shell-quoted.
    """

    path: str

    def shell(self) -> str:
        return f"rm -f {shlex.quote(self.path)}"


@dataclass(frozen=True)
class SysctlReloadCommand(Command):
    """``sysctl -p <path>``: re-apply one config file to the running kernel.

    The apply step both operations yield after a changed write when
    ``reload`` is set. Scoped to the managed file rather than
    ``sysctl --system`` (the developer-chosen deviation from the proposal):
    reloading every drop-in on the host would re-apply unrelated
    configuration too. Needs root: flag the request ``sudo=True``. The path
    is shell-quoted.
    """

    path: str

    def shell(self) -> str:
        return f"sysctl -p {shlex.quote(self.path)}"


async def setting(
    ctx: OpContext,
    key: str,
    value: str | int | list[str | int],
    present: bool = True,
    persist: bool = True,
    sysctl_file: str = "/etc/sysctl.conf",
    sysctl_set: bool = True,
    reload: bool = True,
    ignore_errors: bool = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Ensure one kernel parameter is set (or removed from persistence).

    Args:
        ctx (OpContext): Execution context for the operation.
        key (str): The sysctl parameter name (e.g. ``vm.swappiness``).
        value (str | int | list[str | int]): The desired value; a list joins
            with spaces, digits compare as ints against the runtime fact.
        present (bool, optional): Whether the setting should exist.
            ``False`` removes the key's lines from the persistence file
            (the runtime value is left alone -- a kernel parameter cannot
            be "unset"). Defaults to True.
        persist (bool, optional): Whether to manage the persistence file.
            Defaults to True.
        sysctl_file (str): The config file to edit when ``persist`` is set;
            a ``/etc/sysctl.d/*.conf`` drop-in keeps the legacy file
            untouched. Defaults to "/etc/sysctl.conf".
        sysctl_set (bool, optional): Whether to apply the value to the
            running kernel (``sysctl -w``) when it differs. ``False``
            manages only the file. Defaults to True.
        reload (bool, optional): Run ``sysctl -p <sysctl_file>`` after a
            changed file write. Defaults to True.
        ignore_errors (bool, optional): Append ``|| true`` to the runtime
            command so an unknown or read-only key does not abort the run.
            Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The runtime apply when the live value differs, and/or the
            spool transfer + elevated install (+ reload) when the file
            content differs.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/sysctl/sysctl_setting.py``.
    """

    # A reemote original. It supersedes the runtime + ``files.line``
    # halves of the example-only ``server.sysctl`` approach -- see the
    # ``server_op_sysctl.py`` recipe for that demonstration.
    # Two independent idempotent halves, each gated by its own fact:
    # the runtime half normalizes the desired value (join ->
    # split-if-multi-word -> try_int) and compares the one
    # parameter's entry in the ``Sysctl`` fact (the full dump is read with no
    # ``keys`` kwarg -- a list argument would be unhashable in the FactCache
    # key); the persist half performs
    # a keyed line replacement (``_edit_conf`` -- Ansible-style, a
    # developer-chosen deviation from append-only ``files.line`` editing,
    # because ``present=False`` must remove the key) and compares
    # the whole desired content against ``SysctlFile``. The proposal's
    # ``state="present"/"absent"`` string is the house ``present`` bool;
    # ``name`` is an ``op()`` meta-parameter, never an operation parameter.
    # The write yields a ``SysctlFileUploadCommand`` transfer spooling the
    # text to ``/tmp`` over scp, then a ``SysctlFileInstallCommand`` running
    # the elevated install (the ``systemd.dropin`` pattern), then a scoped
    # ``SysctlReloadCommand``. Reload re-applies the file's *remaining*
    # lines -- a removed key keeps its runtime value until reboot. Writing
    # and applying need root: flag the request ``sudo=True`` (the transfer
    # stays unprivileged regardless).
    string_value, normalized = _normalize_value(value)
    reasons: list[str] = []

    if present and sysctl_set:
        live = await ctx.facts.get(Sysctl)
        if live.get(key) != normalized:
            ctx.changed = True
            reasons.append(f"{key} not set to {string_value} at runtime, will apply")
            yield SysctlSetCommand(
                key=key, value=string_value, ignore_errors=ignore_errors
            )
        else:
            reasons.append(f"{key} already set to {string_value} at runtime")
    elif present:
        reasons.append("runtime apply skipped (sysctl_set=False)")
    else:
        reasons.append("runtime value left unchanged (present=False)")

    if persist:
        current = await ctx.facts.get(SysctlFile, sysctl_file)
        desired = _edit_conf(current, key, string_value if present else None)
        if desired != current:
            ctx.changed = True
            if present:
                reasons.append(f"{sysctl_file} missing or differs for {key}, will write")
            else:
                reasons.append(f"{key} present in {sysctl_file}, will remove")
            spool = _spool_path(sysctl_file)
            yield SysctlFileUploadCommand(content=desired, dest=spool)
            yield SysctlFileInstallCommand(spool=spool, dest=sysctl_file)
            if reload:
                yield SysctlReloadCommand(path=sysctl_file)
        else:
            reasons.append(f"{sysctl_file} already up to date for {key}")
    else:
        reasons.append("persistence skipped (persist=False)")

    ctx.reason = "; ".join(reasons)


async def conf(
    ctx: OpContext,
    settings: tuple[tuple[str, str | int], ...],
    sysctl_file: str = "/etc/sysctl.d/99-reemote.conf",
    present: bool = True,
    reload: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Ensure a whole sysctl config file matches the given settings.

    Args:
        ctx (OpContext): Execution context for the operation.
        settings (tuple[tuple[str, str | int], ...]): The ``(key, value)``
            pairs to write, one ``key = value`` line each, in the given
            order. A tuple, not a dict: ``op()`` rejects unhashable
            arguments.
        sysctl_file (str): The config file to manage. Defaults to
            "/etc/sysctl.d/99-reemote.conf".
        present (bool, optional): Whether the file should exist. ``False``
            removes it (runtime values are left alone). Defaults to True.
        reload (bool, optional): Run ``sysctl -p <sysctl_file>`` after a
            changed write so the settings apply immediately. Defaults to
            True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The spool transfer + elevated install (+ reload) when the
            content differs, or a removal when ``present=False`` and the
            file exists.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/sysctl/sysctl_conf.py``.
    """

    # A reemote original. Persistence-only bulk management: the file's
    # entire content is the rendered settings (the ``systemd.dropin``
    # whole-file model, not ``setting``'s keyed edit), made idempotent by
    # the ``SysctlFile`` comparison; the runtime effect comes from the
    # scoped ``sysctl -p`` reload rather than per-key ``sysctl -w`` steps.
    # The default target is a ``/etc/sysctl.d`` drop-in so the legacy
    # ``/etc/sysctl.conf`` stays untouched. Values render through ``str``
    # verbatim (a multi-word string is written as-is; use ``setting`` for
    # the list normalization). An empty ``settings`` tuple writes an
    # empty file. ``present=False`` yields ``SysctlFileRemoveCommand`` only
    # when the file exists. Writing/removing/applying need root: flag the
    # request ``sudo=True`` (the transfer stays unprivileged regardless).
    current = await ctx.facts.get(SysctlFile, sysctl_file)

    if not present:
        if current is None:
            ctx.reason = f"sysctl config already absent ({sysctl_file})"
            return
        ctx.changed = True
        ctx.reason = f"sysctl config {sysctl_file} present, will remove"
        yield SysctlFileRemoveCommand(path=sysctl_file)
        return

    content = "".join(f"{key} = {value}\n" for key, value in settings)
    if content == current:
        ctx.reason = f"sysctl config already up to date ({sysctl_file})"
        return

    ctx.changed = True
    ctx.reason = f"sysctl config {sysctl_file} missing or differs from the settings"
    spool = _spool_path(sysctl_file)
    yield SysctlFileUploadCommand(content=content, dest=spool)
    yield SysctlFileInstallCommand(spool=spool, dest=sysctl_file)
    if reload:
        yield SysctlReloadCommand(path=sysctl_file)


__all__ = [
    "Sysctl",
    "SysctlFile",
    "SysctlFileInstallCommand",
    "SysctlFileRemoveCommand",
    "SysctlFileUploadCommand",
    "SysctlFiles",
    "SysctlReloadCommand",
    "SysctlSetCommand",
    "conf",
    "setting",
]