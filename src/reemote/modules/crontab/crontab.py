# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``crontab`` module: facts and operations in one source.
#
# This namespace is the canonical home for everything crontab. The single
# ``Crontab`` fact reads the raw text of the invoking user's crontab
# (``crontab -l``, the empty string when they have none), its companion
# ``CrontabEntries`` parses that same text into one structured entry per
# line (pyinfra's ``crontab.Crontab`` shape), and the single ``crontab``
# operation adds, updates or removes one scheduled entry, keyed
# either by its command or by a ``cron_name`` marker comment, and reinstalls
# the whole table. Like the ``sysctl`` namespace (the closest reemote-native
# precedent) the write follows the spool-and-install pattern rather than
# pyinfra's in-place ``sed`` editing: the operation reads the current crontab
# text through the fact, applies a pure, control-side keyed line
# add/replace/remove that preserves every unrelated line, scp-spools the
# result to a deterministic ``/tmp`` path, then (when the request is
# privileged) an elevated ``sh -c`` reinstalls it with ``crontab`` and removes
# the spool.
#
# Like every namespace this one imports nothing from its siblings: the facts
# read the same ``crontab_text`` SimulatedHost seam the operations' install
# mutator writes, so a simulated install reads back immediately -- as raw
# text through ``Crontab``, structured through ``CrontabEntries``.

import hashlib
import io
import re
import shlex
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, TypedDict

from ...commands import Command, TransferCommand
from ...core import OpContext
from ...hosts import Host

#: Marker comment prefix naming an entry (pyinfra's ``# pyinfra-name=`` is
#: reemote's own ``# reemote-name=``), used as the ``cron_name`` identity.
NAME_MARKER = "# reemote-name="


def _try_int(value: str | int) -> int | str:
    """``int()`` that returns its input unchanged on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _comma_sep(value: Any) -> Any:
    """Join a list/tuple schedule field with commas; leave a scalar alone.

    pyinfra's ``minute``/``hour``/... accept an int, a cron string or a list
    of either; a list becomes the comma-separated cron field.
    """
    if isinstance(value, (list, tuple)):
        return ",".join(f"{v}" for v in value)
    return value


def _schedule_value(value: Any) -> str:
    """One schedule field as its file spelling, ``*`` for an unset field."""
    if value is None or value == "":
        return "*"
    return str(_comma_sep(value))


def _desired_fields(
    minute: Any,
    hour: Any,
    day_of_month: Any,
    month: Any,
    day_of_week: Any,
    special_time: str | None,
) -> dict[str, str | None]:
    """Normalize the desired schedule into the comparison shape.

    With ``special_time`` set the five fields are ``None`` (the nickname
    overrides them, exactly as pyinfra's ``special_time`` does); otherwise
    every unset field defaults to ``*``.
    """
    if special_time:
        return {
            "special_time": str(special_time),
            "minute": None,
            "hour": None,
            "day_of_month": None,
            "month": None,
            "day_of_week": None,
        }
    return {
        "special_time": None,
        "minute": _schedule_value(minute),
        "hour": _schedule_value(hour),
        "day_of_month": _schedule_value(day_of_month),
        "month": _schedule_value(month),
        "day_of_week": _schedule_value(day_of_week),
    }


def _render(fields: dict[str, str | None], command: str) -> str:
    """Render one crontab schedule line (without any name-comment prefix)."""
    if fields["special_time"]:
        return f"{fields['special_time']} {command}"
    return (
        f"{fields['minute']} {fields['hour']} {fields['day_of_month']} "
        f"{fields['month']} {fields['day_of_week']} {command}"
    )


def _parse_line(line: str) -> dict[str, str | None] | None:
    """Parse one crontab line into its schedule fields, or None.

    Blank lines, comments and env assignments (``NAME=value`` with no fifth
    field) are not schedule entries and return None -- the operation preserves
    them untouched. A ``@``-prefixed line is a special-time entry; anything
    else needs the five schedule fields plus a command.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("@"):
        parts = stripped.split(None, 1)
        if len(parts) < 2:
            return None
        return {
            "special_time": parts[0],
            "minute": None,
            "hour": None,
            "day_of_month": None,
            "month": None,
            "day_of_week": None,
            "command": parts[1],
        }
    parts = stripped.split(None, 5)
    if len(parts) < 6:
        return None
    minute, hour, day_of_month, month, day_of_week, command = parts
    return {
        "special_time": None,
        "minute": minute,
        "hour": hour,
        "day_of_month": day_of_month,
        "month": month,
        "day_of_week": day_of_week,
        "command": command,
    }


def _fields_differ(existing: dict[str, str | None], desired: dict[str, str | None]) -> bool:
    """Whether an entry's schedule differs from the desired one.

    Fields compare through ``_try_int`` (so a stored ``5`` equals a desired
    ``05``/``5``) and the special-time nickname compares as text -- the same
    normalization pyinfra's replace-gate uses, which keeps a differently
    spaced but equivalent entry from churning.
    """
    for key in ("special_time", "minute", "hour", "day_of_month", "month", "day_of_week"):
        existing_value = existing.get(key)
        desired_value = desired.get(key)
        left = _try_int(existing_value) if existing_value is not None else None
        right = _try_int(desired_value) if desired_value is not None else None
        if left != right:
            return True
    return False


def _find_entry(
    lines: list[str], command: str, cron_name: str | None
) -> tuple[int, int | None, dict[str, str | None]] | None:
    """Locate the entry this operation manages, or None when it is absent.

    With a ``cron_name`` the entry is matched by its marker comment (a line
    ``NAME_MARKER + cron_name`` preceding it); without one, by the exact
    command -- pyinfra's identity rule. Returns the entry line index, the
    index of its marker comment (None when it has one), and the parsed fields.
    """
    name_comment = f"{NAME_MARKER}{cron_name}" if cron_name is not None else None
    pending: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            pending.append((idx, stripped))
            continue
        parsed = _parse_line(line)
        if parsed is not None:
            if name_comment is not None:
                match = next((ci for ci, text in pending if text == name_comment), None)
                if match is not None:
                    return idx, match, parsed
            elif parsed["command"] == command:
                return idx, None, parsed
        pending = []
    return None


def _join(lines: list[str]) -> str:
    """Rejoin entry lines, ending in exactly one trailing newline (or empty)."""
    return "\n".join(lines) + "\n" if lines else ""


# crontab(5) environment line: ``NAME[ ]=[ ]value`` -- the name may be a bare
# identifier or a quoted one, and pyinfra's parser (which this mirrors)
# accepts optional spaces around ``=`` and lowercase names, so an env line is
# told apart from a schedule line by this shape, not by a name whitelist.
ENV_REGEX = re.compile(
    r"""
    ^\s*
    (
        "[^"]*"                         # "quoted name"
      | '[^']*'                         # 'quoted name'
      | [A-Za-z_][A-Za-z0-9_]*          # bare identifier
    )
    \s*=
    """,
    re.VERBOSE,
)


def _edit_crontab(
    text: str,
    *,
    command: str,
    present: bool,
    cron_name: str | None,
    desired: dict[str, str | None],
) -> tuple[str, bool, str]:
    """Add, update or remove one entry in the crontab ``text``.

    The pure, control-side transform the operation runs: it returns
    ``(new_text, changed, reason)``. Every unrelated line (blank lines,
    comments, other entries and env assignments) is preserved, the same
    keyed-edit guarantee as ``sysctl._edit_conf``. When the entry is already
    in the desired state the text is returned unchanged with ``changed``
    False, so the operation is idempotent.
    """
    lines = text.splitlines()
    found = _find_entry(lines, command, cron_name)

    if not present:
        if found is None:
            return text, False, f"crontab entry {command} already absent"
        idx, comment_idx, _parsed = found
        for i in sorted({idx, comment_idx} - {None}, reverse=True):
            del lines[i]
        return _join(lines), True, f"crontab entry {command} present, will remove"

    line_text = _render(desired, command)
    if found is None:
        if lines and lines[-1].strip():
            lines.append("")
        if cron_name is not None:
            lines.append(f"{NAME_MARKER}{cron_name}")
        lines.append(line_text)
        return _join(lines), True, f"crontab entry {command} absent, will add"

    idx, comment_idx, parsed = found
    command_differs = parsed["command"] != command
    comment_differs = (comment_idx is not None) != (cron_name is not None)
    if not (_fields_differ(parsed, desired) or command_differs or comment_differs):
        return text, False, f"crontab entry {command} already up to date"

    lines[idx] = line_text
    if cron_name is not None and comment_idx is None:
        lines.insert(idx, f"{NAME_MARKER}{cron_name}")
    elif cron_name is None and comment_idx is not None:
        del lines[comment_idx]
    return _join(lines), True, f"crontab entry {command} schedule differs, will update"


# --- Facts -------------------------------------------------------------------


class Crontab:
    """The raw text of the invoking user's crontab (``""`` when they have none).

    **Commands:**

    ```bash
    crontab -l 2>/dev/null || true
    ```

    **Returns:**

    ```python
    str
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/crontab/crontab_crontab.py``.
    """

    # The single read of the namespace, mirroring ``sysctl.SysctlFile``'s
    # raw-text model (rather than pyinfra's structured ``CrontabFile``): the
    # operation parses the text itself, so an unpreservable reformat is
    # impossible and env/comment lines round-trip untouched. ``2>/dev/null ||
    # true`` swallows both a missing crontab (``crontab -l`` exits 1 and prints
    # to stderr) and a missing binary, so an empty table reads as ``""``.
    #
    # The ``crontab_text`` SimulatedHost seam is the whole-table store: the
    # fact reads it, the ``crontab`` operation's install mutator writes it, so
    # a simulated install reads back immediately and a repeat is a no-op.

    #: Shell listing the invoking user's crontab over SSH (empty output for a
    #: user with no crontab, since the exit is swallowed).
    COMMAND = "crontab -l 2>/dev/null || true"

    @classmethod
    def command(cls) -> str:
        """Shell dumping the invoking user's crontab (the empty string if none)."""
        return cls.COMMAND

    @classmethod
    def parse(cls, output: Iterable[str]) -> str:
        """Join the listed lines back into the whole-table text.

        Pure and testable; a trailing newline is added when there is content.
        """
        lines = list(output)
        return _join(lines)

    @classmethod
    async def process(cls, host: Host) -> str:
        text = getattr(host, "crontab_text", None)
        if text is not None:
            # SimulatedHost seam: the whole-table text as a plain str.
            return text
        # Real transport: the crontab listing over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return stdout


class CrontabEntry(TypedDict):
    """
    Attributes:
        command: The command the entry runs, rest of the line verbatim;
            ``""`` for an environment line.
        env: The whole ``NAME=value`` line verbatim (spaces around ``=``
            included) for an environment line; ``""`` otherwise.
        minute: The minute field, digits int-normalized (``0`` not ``"00"``);
            ``""`` for a special-time entry or an environment line.
        hour: The hour field, digits int-normalized; ``""`` for a
            special-time entry or an environment line.
        day_of_month: The day-of-month field, digits int-normalized; ``""``
            for a special-time entry or an environment line.
        month: The month field, digits int-normalized; ``""`` for a
            special-time entry or an environment line.
        day_of_week: The day-of-week field, digits int-normalized; ``""`` for
            a special-time entry or an environment line.
        special_time: The ``@``-prefixed nickname (``"@reboot"``, ``"@daily"``,
            ...) for a special-time entry; ``""`` otherwise.
        comments: The comment (and blank) lines immediately preceding the
            entry, in file order; ``[]`` when the entry has none.
    """

    # One parsed crontab line -- the shared entry of ``CrontabEntries``. A
    # TypedDict rather than a class instance (the ``ApkPackageVersion``
    # rationale): entries are ordinary dicts, so equality against a literal
    # dict works and ``json.dumps`` serializes them directly. pyinfra's
    # ``CrontabDict`` marks every field ``NotRequired``; the total form here
    # spells an inapplicable field as the empty value (``""`` / ``[]``)
    # instead of omitting the key, so callers never guess which keys a line
    # kind carried.

    command: str
    env: str
    minute: str | int
    hour: str | int
    day_of_month: str | int
    month: str | int
    day_of_week: str | int
    special_time: str
    comments: list[str]


# The total-entry base every line kind starts from: all nine fields at their
# empty value, with the kind's own fields overridden on top.
EMPTY_ENTRY: CrontabEntry = {
    "command": "",
    "env": "",
    "minute": "",
    "hour": "",
    "day_of_month": "",
    "month": "",
    "day_of_week": "",
    "special_time": "",
    "comments": [],
}


class CrontabEntries:
    """The invoking user's crontab, parsed into one entry per line.

    **Commands:**

    ```bash
    crontab -l 2>/dev/null || true
    ```

    **Returns:**

    ```python
    list[CrontabEntry]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/crontab/crontab_crontab_entries.py``.
    """

    # Mirrors pyinfra's ``crontab.Crontab`` (whose documented example result
    # its pytests reuse verbatim): every table line becomes one
    # ``CrontabEntry`` -- schedule lines carry the five fields with digits
    # int-normalized through ``_try_int`` (pyinfra's own ``try_int``),
    # ``@nickname`` lines carry ``special_time``, ``NAME=value`` lines carry
    # ``env`` (issue #979's crash fix), and the comment/blank lines
    # immediately preceding an entry ride in its ``comments``. Unlike
    # pyinfra's structured fact this one issues no second command: it reads
    # through the ``Crontab`` raw-text fact -- the
    # ``DefaultIpv4Address``/``NetworkDevices`` composition precedent -- so
    # the same ``crontab_text`` SimulatedHost seam serves both facts and a
    # simulated install reads back structured immediately. pyinfra's
    # ``user=`` argument is deliberately dropped, following the operation:
    # which table is read follows the request's privilege, never a fact
    # parameter.
    #
    # A malformed short line (fewer than five fields plus a command, no
    # ``@``/env shape) is skipped where pyinfra's unpack would raise;
    # ``crontab -l`` never prints one, but a hand-edited table can.
    # An empty or absent crontab reads as ``[]``.

    @classmethod
    def parse(cls, text: str) -> list[CrontabEntry]:
        """Parse whole-table text into one entry per schedule line.

        Blank and comment lines accumulate into the next entry's
        ``comments``; a ``@``-prefixed line becomes a special-time entry;
        a ``NAME=value`` line (``ENV_REGEX``) becomes an environment entry;
        any other line with five fields plus a command becomes a schedule
        entry whose digits are int-normalized. A short, unclassifiable line
        is skipped. Pure and testable.
        """
        entries: list[CrontabEntry] = []
        comments: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                comments.append(stripped)
                continue
            if stripped.startswith("@"):
                parts = stripped.split(None, 1)
                if len(parts) == 2:
                    special_time, command = parts
                    entries.append(
                        {
                            **EMPTY_ENTRY,
                            "command": command,
                            "special_time": special_time,
                            "comments": comments,
                        }
                    )
            elif ENV_REGEX.match(stripped):
                entries.append(
                    {**EMPTY_ENTRY, "env": stripped, "comments": comments}
                )
            else:
                parts = stripped.split(None, 5)
                if len(parts) == 6:
                    minute, hour, day_of_month, month, day_of_week, command = parts
                    entries.append(
                        {
                            **EMPTY_ENTRY,
                            "command": command,
                            "minute": _try_int(minute),
                            "hour": _try_int(hour),
                            "day_of_month": _try_int(day_of_month),
                            "month": _try_int(month),
                            "day_of_week": _try_int(day_of_week),
                            "comments": comments,
                        }
                    )
            comments = []
        return entries

    @classmethod
    async def process(cls, host: Host) -> list[CrontabEntry]:
        # Read the whole table through the raw-text fact: its seam and its
        # ``crontab -l 2>/dev/null || true`` transport both serve this fact
        # unchanged, so there is no second command and no second seam here.
        return cls.parse(await Crontab.process(host))


# --- Operations --------------------------------------------------------------


def _spool_path(command: str, cron_name: str | None) -> str:
    """The deterministic ``/tmp`` spool path the crontab operation uploads to.

    Named from the managed entry (``cron_name`` when set, else the command) so
    the name is identical across the two deploy phases (a per-materialization
    temp name would diverge and trip the determinism guard) while two distinct
    entries get two distinct spools and never clobber one another's upload. The
    content lands inside a sha1-derived ``/tmp`` directory rather than at the
    top level, and the install ``rm -rf``s the whole directory afterwards. The
    directory stays world-writable ``/tmp`` so the *unprivileged* scp can write
    it -- the trade-off every spool-and-install operation makes.
    """
    identity = f"{cron_name if cron_name is not None else ''}\0{command}"
    digest = hashlib.sha1(identity.encode()).hexdigest()[:16]
    return f"/tmp/reemote-crontab-{digest}/crontab"


@dataclass(frozen=True)
class CrontabUploadCommand(TransferCommand):
    """Spool the new crontab text to a ``/tmp`` path on the target.

    The first half of the write direction and a *non-shell* descriptor: it
    copies the content over the connection's scp channel via
    ``Host.upload_file`` rather than running shell text, so it has no
    ``shell()`` and its loggable form is the ``__str__`` rendering
    ``command_shell`` falls back to. Like ``SysctlFileUploadCommand`` the
    content is the whole-table text the operation computed (a plain ``str`` --
    hashable and identical across phases), wrapped in a fresh ``io.BytesIO``
    only at transfer time. The copy rides as the login user (transfers are
    unprivileged, so the request's ``sudo`` does not apply), which is why the
    spool lives in world-writable ``/tmp``.
    """

    content: str  # the whole crontab text the operation installs
    dest: str  # spool path on the target host

    def __str__(self) -> str:
        return f"scp <crontab> -> {self.dest}"

    async def transfer(self, host: Host) -> None:
        # scp does not create parent directories, and the spool lives inside a
        # sha1-derived /tmp directory, so mkdir -p it first. Unprivileged: /tmp
        # is world-writable, so the login user can create the directory.
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        await host.run_shell(f"mkdir -p {parent}")
        await host.upload_file(io.BytesIO(self.content.encode()), self.dest)


@dataclass(frozen=True)
class CrontabInstallCommand(Command):
    """``crontab <spool>``: install the spooled text, then remove the spool dir.

    The second half of the write direction. There is no destination file to
    write -- the table lands in the user's crontab database -- so this is a
    plain ``crontab`` install rather than the ``cp``+``chmod`` of the
    sysctl/systemd install descriptors. The whole sequence is wrapped in
    ``sh -c`` so a privileged request elevates the install and the spool
    cleanup together (a bare ``sudo`` prefix would elevate only the first
    command of the ``&&`` chain). ``crontab`` acts on the invoking identity:
    flagged ``sudo=True`` (or run as another user through the inventory
    ``privilege``) it installs that user's crontab, so the operation carries no
    run-as parameter of its own. All paths are shell-quoted.
    """

    spool: str  # where CrontabUploadCommand wrote the content

    def shell(self) -> str:
        spool = shlex.quote(self.spool)
        spool_dir = shlex.quote(str(PurePosixPath(self.spool).parent))
        inner = f"crontab {spool} && rm -rf {spool_dir}"
        return f"sh -c {shlex.quote(inner)}"


async def crontab(
    ctx: OpContext,
    command: str,
    present: bool = True,
    cron_name: str | None = None,
    minute: str | int | list[str | int] | None = None,
    hour: str | int | list[str | int] | None = None,
    month: str | int | list[str | int] | None = None,
    day_of_week: str | int | list[str | int] | None = None,
    day_of_month: str | int | list[str | int] | None = None,
    special_time: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add, update or remove one crontab entry.

    Args:
        ctx (OpContext): Execution context for the operation.
        command (str): The command the cron runs; also its identity unless a
            ``cron_name`` is given.
        present (bool, optional): Whether the entry should exist. ``False``
            removes it. Defaults to True.
        cron_name (str | None, optional): Name the entry with a marker comment
            so later changes to ``command`` overwrite the same entry; lets two
            identical commands coexist under different names. Defaults to None.
        minute (str | int | list[str | int] | None, optional): Which minutes to
            run; a list is comma-joined. Defaults to None (``*``).
        hour (str | int | list[str | int] | None, optional): Which hours to run;
            a list is comma-joined. Defaults to None (``*``).
        month (str | int | list[str | int] | None, optional): Which months to
            run; a list is comma-joined. Defaults to None (``*``).
        day_of_week (str | int | list[str | int] | None, optional): Which days
            of the week to run; a list is comma-joined. Defaults to None (``*``).
        day_of_month (str | int | list[str | int] | None, optional): Which days
            of the month to run; a list is comma-joined. Defaults to None (``*``).
        special_time (str | None, optional): A cron nickname (``@reboot``,
            ``@daily``, ...) used instead of the five schedule fields.
            Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The spool transfer + install when the entry is added, updated
            or removed; nothing when it is already in the desired state.

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        ```python
        from reemote import op, crontab

        # Run a backup at 02:00 every day (the entry is keyed by its command;
        # unset schedule fields stay ``*`` -> ``0 2 * * * ...``)
        yield op(
            crontab.crontab,
            name="Nightly backup",
            command="/usr/local/bin/backup.sh",
            minute=0,
            hour=2,
        )

        # Every half hour on weekdays (a sequence of values is comma-joined
        # -> ``0,30 * * * 1,2,3,4,5 ...``)
        yield op(
            crontab.crontab,
            command="/usr/local/bin/sync.sh",
            minute=(0, 30),
            day_of_week=(1, 2, 3, 4, 5),
        )

        # A cron nickname instead of the five fields, named with a marker
        # comment (``# reemote-name=daily-report``) so later changes to the
        # command overwrite the same entry
        yield op(
            crontab.crontab,
            command="/usr/local/bin/report.sh",
            cron_name="daily-report",
            special_time="@daily",
        )

        # Remove an entry (its marker comment, if any, goes with it)
        yield op(
            crontab.crontab,
            command="/usr/local/bin/backup.sh",
            present=False,
        )
        ```

        Examples can be found in ``examples/modules/crontab/crontab_crontab.py``.
    """

    # pyinfra ``crontab.crontab`` parity.
    #
    # ``command``/``present``/``cron_name`` and the five schedule fields plus
    # ``special_time`` map straight across. The idempotence gate is the whole
    # table read through the ``Crontab`` fact and edited in memory: an entry
    # already in the desired state yields nothing, a change yields the spool
    # transfer then the install. ``cron_name`` is stored as a
    # ``# reemote-name=`` marker comment (reemote's own marker, not pyinfra's
    # ``# pyinfra-name=``).
    #
    # Deliberately dropped from pyinfra:
    #   * ``interpolate_variables`` -- the command is always treated as a
    #     literal (never re-expanded by the shell), because reemote computes
    #     the whole table in Python instead of rendering ``sed`` scripts, so
    #     there is no second shell pass to interpolate into.
    #   * ``user`` -- which table to edit follows the request's privilege
    #     (the inventory run-as / ``sudo``), not an operation parameter.
    #
    # The command does not touch a root-owned file; ``crontab`` writes the
    # invoking user's own table, so ``sudo`` is only needed to manage another
    # user's crontab. The spool transfer itself is always unprivileged.
    # ``**kwargs`` absorbs pyinfra's global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    current = await ctx.facts.get(Crontab)
    desired = _desired_fields(
        minute, hour, day_of_month, month, day_of_week, special_time
    )
    new_text, changed, reason = _edit_crontab(
        current, command=command, present=present, cron_name=cron_name, desired=desired
    )
    ctx.changed = changed
    ctx.reason = reason
    if changed:
        spool = _spool_path(command, cron_name)
        yield CrontabUploadCommand(content=new_text, dest=spool)
        yield CrontabInstallCommand(spool=spool)


__all__ = [
    "Crontab",
    "CrontabEntries",
    "CrontabEntry",
    "CrontabInstallCommand",
    "CrontabUploadCommand",
    "crontab",
]
