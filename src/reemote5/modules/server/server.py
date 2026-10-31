"""The ``server`` module: systemd facts, ``Arch``, the ``service`` operation and waiting.

Fact side (design doc section 5): ``Arch`` (the machine architecture from
``uname -m``), ``AuthorizedKeys`` (the SSH public keys listed in a user's
``~/.ssh/authorized_keys`` file), ``Date`` (the current datetime on the
server, parsed from an ISO 8601 ``date`` timestamp), ``Groups`` (the group
names listed in ``/etc/group``), ``HasGui`` (whether a known desktop-session
binary is installed), ``Home`` (a user's home directory, resolved by shell
tilde expansion), ``Hostname`` (the server's hostname from ``uname -n``),
``Kernel`` (the kernel name from ``uname -s``), ``KernelModules`` (the
loaded kernel modules from ``/proc/modules``, or ``kldstat`` on FreeBSD),
``KernelVersion`` (the kernel release from ``uname -r``),
``Last`` (the login records parsed from ``last``), ``LinuxDistribution``
(the distribution name and version parsed from the ``/etc/*-release``
files), ``LinuxGui`` (the installed desktop sessions' GUI names),
``LinuxName`` (the distribution's pretty name, a shortcut over
``LinuxDistribution``), ``Locales`` (the installed locales from
``locale -a``, normalized to ``UTF-8`` spellings), ``LsbRelease`` (the
release information parsed from ``lsb_release -ca``), ``Mounts`` (the
mounted filesystems, keyed by mount point, read from
``/proc/self/mountinfo`` or the BSD ``mount`` variants), ``Path`` (the
current user's ``PATH`` environment variable, as the raw colon-separated
string), ``Processes`` (the running processes keyed by PID, parsed from
``ps``), ``SystemdServiceState`` (a unit's
``ActiveState`` -- ``active``, ``inactive``, ``activating``, ... -- read by
``server.service`` to decide whether to start or stop, and polled by
``server.wait_service``) and ``SystemdServiceEnabled`` (whether a unit is
enabled on boot), ``Timezone`` (the system timezone resolved from
``/etc/localtime``) and ``Uptime`` (the seconds since boot, from
``/proc/uptime`` or the BSD boot-time difference), ``User`` (the
current user's name from ``echo $USER``), ``Users`` (the system users
keyed by name, joined from ``/etc/passwd``, ``id``, ``lastlog`` and the
shadow password files) and ``Which`` (the path of a given command from
``command -v``, or None when unavailable). Like every fact they are
phase-unaware: each ``process`` coroutine just gathers the raw value.

Operation side (design doc section 6): ``server.service`` plus the
``SystemctlCommand`` descriptor it yields, and ``server.wait_service`` plus the
``WaitForFactCommand`` descriptor it yields. ``server.service`` mirrors
pyinfra's ``systemd.service`` for ``service``, ``running``, ``restarted``,
``enabled`` and ``daemon_reload``; pyinfra's ``reloaded``, ``command``,
``user_mode``, ``machine`` and ``user_name`` options are deliberately dropped.

``WaitForFactCommand`` is the *non-shell* wait descriptor: it carries a fact,
its arguments and a predicate, and its ``wait`` polls the fact (bypassing
the per-step ``FactCache``) until the predicate holds or the timeout
expires. It is generic -- any fact and predicate -- and lives here beside its
first consumer, ``server.wait_service``.
"""

import asyncio
import json
import pathlib
import re
import shlex
import shutil
from collections.abc import AsyncGenerator, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from tempfile import mkdtemp
from typing import Any, ClassVar

import distro

from ...commands import Command, WaitCommand, WaitTimeoutError
from ...core import OpContext
from ...hosts import Host

#: Unit suffixes systemd recognises; a bare name gets ``.service`` appended
#: (pyinfra's ``systemd.service`` normalization).
_UNIT_SUFFIXES = (
    ".service",
    ".socket",
    ".device",
    ".mount",
    ".automount",
    ".swap",
    ".target",
    ".path",
    ".timer",
    ".slice",
    ".scope",
)


def unit_name(service: str) -> str:
    """Normalize a service name to a full systemd unit name.

    ``httpd`` becomes ``httpd.service``; a name already carrying a systemd
    unit suffix (``logrotate.timer``, ``httpd.service``) is returned as is.
    Both the facts and the operations key on the normalized name, so the
    test framework's simulated state must use full unit names too.
    """
    if service.endswith(_UNIT_SUFFIXES):
        return service
    return f"{service}.service"


# --- Facts -------------------------------------------------------------------


class Arch:
    """The system architecture according to ``uname`` (e.g. ``x86_64``).

    Mirrors pyinfra's ``server.Arch``: the first line of ``uname -m`` output
    is the architecture string. ``uname -m`` is used rather than ``uname -p``
    because the latter is not portable and reports ``unknown`` on Debian.
    Empty output (the command failed) maps to ``""``.
    """

    #: Shell used to gather the architecture over SSH.
    COMMAND = "uname -m"

    @classmethod
    def parse(cls, output: Iterable[str]) -> str:
        """Parse ``uname -m`` output into the architecture string.

        The first non-blank line is the answer; anything after it is ignored.
        """
        for line in output:
            stripped = line.strip()
            if stripped:
                return stripped
        return ""

    @classmethod
    async def process(cls, host: Host) -> str:
        arch = getattr(host, "arch", None)
        if arch is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return arch
        # Real transport: `uname -m` over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


# Usernames used in shell tilde expansion (``~user``) cannot be quoted without
# disabling the expansion, so the value must be a plain, shell-safe word
# (pyinfra's ``_SAFE_USERNAME_RE``).
_SAFE_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._][a-zA-Z0-9._-]*$")


def _check_tilde_username(user: str) -> None:
    if user and not _SAFE_USERNAME_RE.match(user):
        raise ValueError(f"Unsafe username for shell tilde expansion: {user!r}")


class AuthorizedKeys:
    """The SSH public keys listed in a user's ``~/.ssh/authorized_keys`` file.

    Mirrors pyinfra's ``server.AuthorizedKeys``: the full key strings are
    returned as a list in the file's order; empty lines and lines starting
    with ``#`` are skipped. A missing or empty file reads as ``[]`` (the
    command swallows ``cat``'s error), for example::

        [
            "ssh-ed25519 AAAAC3Nz... user@host",
            "ssh-rsa AAAAB3Nz... other@host",
        ]

    ``user`` selects whose file is read via shell tilde expansion
    (``~user/.ssh/authorized_keys``); an empty ``user`` means the login
    user's own ``~``. Because ``~user`` must stay an unquoted bare word, an
    unsafe username raises ``ValueError`` instead of reaching the shell.
    An explicit ``path`` reads that file instead (``shlex``-quoted), which
    is how pyinfra's ``server.authorized_keys`` operation supports
    non-default key files.
    """

    @classmethod
    def command(cls, user: str, path: str | None = None) -> str:
        """Shell cats the key file, mapping a missing/unreadable one to no output.

        Tilde expansion resolves the user's home without another fact
        round-trip, so the username is validated rather than quoted; an
        explicit path is quoted normally.
        """
        if path is not None:
            return f"cat {shlex.quote(path)} 2>/dev/null || true"
        _check_tilde_username(user)
        return f"cat ~{user}/.ssh/authorized_keys 2>/dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[str]:
        """Parse ``cat`` output into the list of full key strings.

        Lines are stripped; blank lines and ``#`` comments are skipped and
        the file's order is preserved (no dedup, no sorting).
        """
        keys: list[str] = []
        for raw in output:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            keys.append(line)
        return keys

    @classmethod
    async def process(cls, host: Host, user: str, path: str | None = None) -> list[str]:
        # Build the shell first, even on the simulated path, so an unsafe
        # tilde username is rejected identically on both transports.
        shell = cls.command(user, path)
        authorized = getattr(host, "authorized_keys", None)
        if authorized is not None:
            # SimulatedHost seam: the state is keyed by username for the
            # default path and by the explicit path otherwise. Return a copy
            # so callers never mutate the simulated state.
            key = user if path is None else path
            return list(authorized.get(key, []))
        # Real transport: cat over asyncssh. A missing file produces empty
        # stdout (the error is swallowed), which parses to [].
        stdout = await host.run_shell(shell)
        return cls.parse(stdout.splitlines())


#: ``date`` format string producing an ISO 8601 timestamp with a numeric
#: UTC offset (pyinfra's ``ISO_DATE_FORMAT``); used both to build the shell
#: and to parse its output back into a ``datetime``.
_ISO_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


class Date:
    """The current datetime on the server, as a timezone-aware ``datetime``.

    Mirrors pyinfra's ``server.Date``: ``date +'%Y-%m-%dT%H:%M:%S%z'`` prints
    an ISO 8601 timestamp with a numeric UTC offset (e.g.
    ``2019-01-01T00:00:00+0000``), which is parsed with ``datetime.strptime``
    into an aware ``datetime``, for example::

        datetime(2019, 1, 1, 0, 0, tzinfo=timezone.utc)

    The first non-blank line is the answer; anything after it is ignored.
    Empty output (the command failed) maps to ``None`` -- pyinfra's
    ``default = datetime.now`` fallback to the *control* machine's clock is
    deliberately dropped, so a failed read never masquerades as a host time.
    """

    #: Shell used to gather the server's clock over SSH.
    COMMAND = f"date +'{_ISO_DATE_FORMAT}'"

    @classmethod
    def parse(cls, output: Iterable[str]) -> datetime | None:
        """Parse ``date`` output into an aware ``datetime``, or None if empty.

        The first non-blank line is parsed with ``strptime``; a line that is
        not an ISO 8601 timestamp raises ``ValueError``.
        """
        for line in output:
            stripped = line.strip()
            if stripped:
                return datetime.strptime(stripped, _ISO_DATE_FORMAT)
        return None

    @classmethod
    async def process(cls, host: Host) -> datetime | None:
        date = getattr(host, "date", None)
        if date is not None:
            # SimulatedHost seam: read the in-memory clock directly.
            # datetime is immutable, so no copy is needed.
            return date
        # Real transport: `date` over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class Groups:
    """The list of group names on the system, from ``/etc/group``.

    Mirrors pyinfra's ``server.Groups``: ``cat /etc/group`` is parsed by
    taking the first ``:``-separated field of every line that contains a
    colon; lines without one (blank lines, stray output) are skipped. The
    file's order is preserved -- no dedup, no sorting -- for example::

        ["lpadmin", "sambashare", "vboxsf"]

    Empty output (the command failed or the file is empty) reads as ``[]``.
    """

    #: Shell used to gather the group list over SSH.
    COMMAND = "cat /etc/group"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[str]:
        """Parse ``/etc/group`` rows into the list of group names.

        Only the first field of each colon-bearing line is kept; lines
        without a colon are ignored and the file's order is preserved.
        """
        groups: list[str] = []
        for line in output:
            if ":" in line:
                groups.append(line.split(":")[0])
        return groups

    @classmethod
    async def process(cls, host: Host) -> list[str]:
        groups = getattr(host, "os_groups", None)
        if groups is not None:
            # SimulatedHost seam: read the in-memory list directly. Return a
            # copy so callers never mutate the simulated state.
            return list(groups)
        # Real transport: cat /etc/group over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


#: Desktop-session binaries pyinfra's ``LinuxGui.known_gui_binaries``
#: recognises, mapped to their GUI names. Shared by ``LinuxGui`` (which
#: returns the names) and ``HasGui`` (any one of them means a GUI).
_KNOWN_GUI_BINARIES = {
    "/usr/bin/gnome-session": "GNOME",
    "/usr/bin/mate-session": "MATE",
    "/usr/bin/lxsession": "LXDE",
    "/usr/bin/plasma_session": "KDE Plasma",
    "/usr/bin/xfce4-session": "XFCE 4",
}


class HasGui:
    """Whether the host has GUI capabilities (Linux only).

    Mirrors pyinfra's ``server.HasGui``: ``ls /usr/bin/*session || true``
    lists the desktop-session binaries under ``/usr/bin``, and the host has
    a GUI when any listed path is a known session binary (GNOME, MATE,
    LXDE, KDE Plasma, XFCE 4). Empty output -- no ``*session`` match, the
    ``ls`` error swallowed by ``|| true`` -- reads as ``False``. pyinfra
    implements this as a ``ShortFactBase`` over its ``LinuxGui`` fact; here
    the two are separate facts sharing the same shell and known-binary
    map, so ``HasGui`` is the boolean projection of ``LinuxGui``'s list of
    GUI names.
    """

    #: Shell used to look for desktop-session binaries over SSH.
    COMMAND = "ls /usr/bin/*session || true"

    #: Session binaries pyinfra's ``LinuxGui.known_gui_binaries`` recognises;
    #: any one of them present means the host has a GUI. The keys of the map
    #: ``LinuxGui`` parses names from.
    GUI_SESSION_BINARIES = frozenset(_KNOWN_GUI_BINARIES)

    @classmethod
    def parse(cls, output: Iterable[str]) -> bool:
        """Parse ``ls`` rows into whether a known session binary is listed.

        Lines are matched exactly (pyinfra does not strip them); anything
        else -- other ``*session`` files, stray output -- is ignored.
        """
        return any(line in cls.GUI_SESSION_BINARIES for line in output)

    @classmethod
    async def process(cls, host: Host) -> bool:
        has_gui = getattr(host, "has_gui", None)
        if has_gui is not None:
            # SimulatedHost seam: read the in-memory flag directly.
            # bool is immutable, so no copy is needed.
            return has_gui
        # Real transport: `ls /usr/bin/*session` over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class Home:
    """A user's home directory (e.g. ``/home/pyinfra``), or None.

    Mirrors pyinfra's ``server.Home``: ``echo ~user`` lets the shell resolve
    the home directory; an empty ``user`` means the login user's own ``~``.
    Because ``~user`` must stay an unquoted bare word, an unsafe username
    raises ``ValueError`` instead of reaching the shell. The first non-blank
    line is the answer; anything after it is ignored. Empty output (the
    command failed) reads as ``None``, and so does an unexpanded ``~user``
    echo -- the shell's answer for an unknown user, which pyinfra returns as
    the literal string; reemote5 maps it to ``None`` so a missing user never
    masquerades as a path.
    """

    @classmethod
    def command(cls, user: str = "") -> str:
        """Shell echoes ``~user`` so tilde expansion resolves the home.

        The username is validated rather than quoted: quoting ``~user``
        would disable the expansion (pyinfra's ``_check_tilde_username``).
        """
        _check_tilde_username(user)
        return f"echo ~{user}"

    @classmethod
    def parse(cls, output: Iterable[str]) -> str | None:
        """Parse ``echo ~user`` output into the home directory, or None.

        The first non-blank line is the answer; a line still starting with
        ``~`` means the shell could not expand the user, and empty output
        means the command failed -- both read as ``None``.
        """
        for line in output:
            stripped = line.strip()
            if stripped:
                return None if stripped.startswith("~") else stripped
        return None

    @classmethod
    async def process(cls, host: Host, user: str = "") -> str | None:
        # Build the shell first, even on the simulated path, so an unsafe
        # tilde username is rejected identically on both transports.
        shell = cls.command(user)
        homes = getattr(host, "home_directories", None)
        if homes is not None:
            # SimulatedHost seam: username -> home directory ("" keys the
            # login user). str is immutable, so no copy is needed; an
            # unknown user reads as None, like an unexpanded tilde.
            return homes.get(user)
        # Real transport: `echo ~user` over asyncssh.
        stdout = await host.run_shell(shell)
        return cls.parse(stdout.splitlines())


class Hostname:
    """The server's hostname according to ``uname`` (e.g. ``my-host.net``).

    Mirrors pyinfra's ``server.Hostname``: the first line of ``uname -n``
    output is the hostname string. The first non-blank line is the answer;
    anything after it is ignored (``uname -n`` prints exactly one line).
    Empty output (the command failed) maps to ``""``, like ``Arch``.
    """

    #: Shell used to gather the hostname over SSH.
    COMMAND = "uname -n"

    @classmethod
    def parse(cls, output: Iterable[str]) -> str:
        """Parse ``uname -n`` output into the hostname string.

        The first non-blank line is the answer; anything after it is ignored.
        """
        for line in output:
            stripped = line.strip()
            if stripped:
                return stripped
        return ""

    @classmethod
    async def process(cls, host: Host) -> str:
        hostname = getattr(host, "os_hostname", None)
        if hostname is not None:
            # SimulatedHost seam: read the in-memory state directly.
            # str is immutable, so no copy is needed.
            return hostname
        # Real transport: `uname -n` over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class Kernel:
    """The kernel name according to ``uname`` (e.g. ``Linux``).

    Mirrors pyinfra's ``server.Kernel``: the first line of ``uname -s``
    output is the kernel name string (``Linux``, ``Darwin``, ``FreeBSD``,
    ...). The first non-blank line is the answer; anything after it is
    ignored (``uname -s`` prints exactly one line). Empty output (the
    command failed) maps to ``""``, like ``Arch``. pyinfra's deprecated
    ``server.Os`` alias is deliberately dropped -- only the new name lives
    here.
    """

    #: Shell used to gather the kernel name over SSH.
    COMMAND = "uname -s"

    @classmethod
    def parse(cls, output: Iterable[str]) -> str:
        """Parse ``uname -s`` output into the kernel name string.

        The first non-blank line is the answer; anything after it is ignored.
        """
        for line in output:
            stripped = line.strip()
            if stripped:
                return stripped
        return ""

    @classmethod
    async def process(cls, host: Host) -> str:
        kernel = getattr(host, "os_kernel", None)
        if kernel is not None:
            # SimulatedHost seam: read the in-memory state directly.
            # str is immutable, so no copy is needed.
            return kernel
        # Real transport: `uname -s` over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class KernelModules:
    """The loaded kernel modules, as a dict of module name -> info dict.

    Mirrors pyinfra's ``server.KernelModules``: the shell is chosen from the
    ``Kernel`` fact -- FreeBSD lists ``kldstat | tail +2``, every other
    kernel cats ``/proc/modules`` (the ``! test -f`` guard short-circuits to
    no output when the file is absent, so a failed read maps to ``{}``,
    pyinfra's ``default = dict``).

    On Linux each ``/proc/modules`` row becomes, for example::

        {
            "libahci": {
                "size": "32768",
                "instances": 1,
                "state": "Live",
                "depends": ["ahci"],
            },
        }

    ``size`` stays the raw string and only ``instances`` is converted to an
    int (pyinfra's docstring example shows ints for both, but its parser
    converts only ``instances``); ``depends`` is present only when the row
    lists dependencies (``-`` means none). On FreeBSD each ``kldstat`` row
    becomes ``{"id", "refs", "address", "size"}`` -- all raw strings, with
    hex ``size`` and ``address``.
    """

    #: Shell listing the loaded modules on Linux (pyinfra's non-FreeBSD
    #: branch): the guard yields no output when /proc/modules is absent.
    COMMAND_LINUX = "! test -f /proc/modules || cat /proc/modules"

    #: Shell listing the loaded modules on FreeBSD; ``tail +2`` drops the
    #: ``kldstat`` header row.
    COMMAND_FREEBSD = "kldstat | tail +2"

    @classmethod
    def command(cls, kernel: str) -> str:
        """Shell for the kernel name the ``Kernel`` fact reports.

        Only ``FreeBSD`` selects ``kldstat``; every other answer (including
        a failed ``uname -s``, which reads as ``""``) takes the
        ``/proc/modules`` branch, exactly like pyinfra.
        """
        if kernel.strip() == "FreeBSD":
            return cls.COMMAND_FREEBSD
        return cls.COMMAND_LINUX

    @classmethod
    def parse(cls, output: Iterable[str], kernel: str) -> dict[str, dict[str, Any]]:
        """Parse module rows into the name -> info dict for ``kernel``.

        Dispatches on the kernel name exactly like ``command``: FreeBSD rows
        are whitespace-split ``kldstat`` lines, everything else is
        space-split ``/proc/modules`` rows. Pure and testable.
        """
        if kernel.strip() == "FreeBSD":
            return cls._parse_freebsd(output)
        return cls._parse_linux(output)

    @classmethod
    def _parse_linux(cls, output: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Parse ``/proc/modules`` rows (pyinfra's non-FreeBSD branch).

        Each row is ``name size instances depends state address``; only
        ``instances`` becomes an int, ``depends`` is kept as a list only
        when the row lists dependencies (``-`` means none, empty entries
        are dropped) and the trailing address field is ignored.
        """
        modules: dict[str, dict[str, Any]] = {}
        for line in output:
            name, size, instances, depends, state, _ = line.split(" ", 5)
            module: dict[str, Any] = {
                "size": size,
                "instances": int(instances),
                "state": state,
            }
            if depends != "-":
                module["depends"] = [value for value in depends.split(",") if value]
            modules[name] = module
        return modules

    @classmethod
    def _parse_freebsd(cls, output: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Parse ``kldstat`` rows (pyinfra's FreeBSD branch).

        Each row is whitespace-split into ``id refs address size name``;
        all four info fields stay raw strings (``size`` and ``address``
        are hex).
        """
        modules: dict[str, dict[str, Any]] = {}
        for line in output:
            module_id, refs, address, size, name = line.split(None, 4)
            modules[name] = {
                "address": address,
                "id": module_id,
                "refs": refs,
                "size": size,
            }
        return modules

    @classmethod
    async def process(cls, host: Host) -> dict[str, dict[str, Any]]:
        modules = getattr(host, "kernel_modules", None)
        if modules is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy the
            # nested dicts (and any depends list) so callers never mutate the
            # simulated state.
            return {
                name: {
                    key: list(value) if isinstance(value, list) else value
                    for key, value in info.items()
                }
                for name, info in modules.items()
            }
        # Real transport: the Kernel fact picks the shell (pyinfra reads it
        # inside command()), then its output is parsed the same way.
        kernel = await Kernel.process(host)
        stdout = await host.run_shell(cls.command(kernel))
        return cls.parse(stdout.splitlines(), kernel)


class KernelVersion:
    """The kernel version according to ``uname`` (e.g. ``6.1.0-26-amd64``).

    Mirrors pyinfra's ``server.KernelVersion`` -- the renamed
    ``server.OsVersion`` fact, whose deprecated alias is deliberately
    dropped: the first line of ``uname -r`` output is the kernel release
    string. The first non-blank line is the answer; anything after it is
    ignored (``uname -r`` prints exactly one line). Empty output (the
    command failed) maps to ``""``, like ``Arch``.
    """

    #: Shell used to gather the kernel version over SSH.
    COMMAND = "uname -r"

    @classmethod
    def parse(cls, output: Iterable[str]) -> str:
        """Parse ``uname -r`` output into the kernel release string.

        The first non-blank line is the answer; anything after it is ignored.
        """
        for line in output:
            stripped = line.strip()
            if stripped:
                return stripped
        return ""

    @classmethod
    async def process(cls, host: Host) -> str:
        version = getattr(host, "os_kernel_version", None)
        if version is not None:
            # SimulatedHost seam: read the in-memory state directly.
            # str is immutable, so no copy is needed.
            return version
        # Real transport: `uname -r` over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class Last:
    """The login records parsed from ``last``, as a list of dicts.

    Mirrors pyinfra's ``server.Last``: each record holds ``user``, ``tty``,
    ``host`` and ``time``, for example::

        [
            {
                "user": "alice",
                "tty": "pts/0",
                "host": "192.168.1.5",
                "time": "Thu Apr 17 14:00 still logged in",
            },
            {
                "user": "reboot",
                "tty": "system boot",
                "host": "6.19.10-arch1-1",
                "time": "Thu Apr 17 11:00 - 12:00 (01:00)",
            },
        ]

    Parsing is intentionally light: ``time`` holds the raw trailing string
    from the ``last`` output -- runs of whitespace collapsed to single
    spaces by the token join (pyinfra's docstring example shows the column
    padding, but its parser joins with single spaces too) -- so callers can
    re-parse the date format that matches their system. Blank lines,
    ``wtmp begins``/``btmp begins`` footers, ``user``/``username`` header
    rows and rows with fewer than three fields are skipped; a ``reboot``
    row's ``system boot`` counts as one tty, and a row whose remainder
    starts with a weekday has no host column (``""``). Empty output --
    ``last`` missing or failed, swallowed by the shell's ``|| true`` --
    reads as ``[]`` (pyinfra's ``default = list``). pyinfra's
    ``LastRecordDict`` TypedDict is represented as a plain dict, like
    ``KernelModules``' info dicts.
    """

    #: Shell used to gather the login records over SSH: ``-Fwi``
    #: (util-linux) prints full timestamps, wide columns and IPs instead of
    #: hostnames; ``-w`` alone works on FreeBSD and busybox ``last`` rejects
    #: all flags, so the bare command is the final fallback.
    COMMAND = "last -Fwi 2>/dev/null || last -w 2>/dev/null || last 2>/dev/null || true"

    #: Weekday abbreviations ``last`` prints (pyinfra's ``_WEEKDAYS``); a
    #: remainder starting with one means the row has no host column.
    WEEKDAYS = frozenset({"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"})

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[dict[str, str]]:
        """Parse ``last`` rows into the list of login-record dicts.

        Skips blank lines, ``wtmp``/``btmp begins`` footers, header rows and
        short rows; the file's order is preserved (no dedup, no sorting).
        Pure and testable.
        """
        records: list[dict[str, str]] = []
        for raw in output:
            line = raw.rstrip()
            if not line:
                continue

            lower = line.lower()
            if lower.startswith(("wtmp begins", "btmp begins")):
                continue
            # util-linux may emit a "user tty ..." header with -x; skip it.
            if lower.startswith(("user ", "username ")):
                continue

            parts = line.split()
            if len(parts) < 3:
                continue

            user = parts[0]
            if user == "reboot" and parts[1] == "system" and parts[2] == "boot":
                tty = "system boot"
                rest = parts[3:]
            else:
                tty = parts[1]
                rest = parts[2:]

            if not rest:
                continue

            if rest[0] in cls.WEEKDAYS:
                host = ""
                time_parts = rest
            else:
                host = rest[0]
                time_parts = rest[1:]

            records.append(
                {
                    "user": user,
                    "tty": tty,
                    "host": host,
                    "time": " ".join(time_parts),
                }
            )

        return records

    @classmethod
    async def process(cls, host: Host) -> list[dict[str, str]]:
        records = getattr(host, "login_records", None)
        if records is not None:
            # SimulatedHost seam: read the in-memory records directly. Copy
            # each dict so callers never mutate the simulated state.
            return [dict(record) for record in records]
        # Real transport: `last` over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


def _try_int(value: str) -> int | str:
    """``int()`` that returns its input unchanged on failure (pyinfra's ``try_int``).

    ``distro``'s ``major_version``/``minor_version`` answer ``""`` when no
    source provides the part, so a missing version comes back as a string
    rather than raising.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


class LinuxDistribution:
    """The Linux distribution's name and version, as a dict.

    Mirrors pyinfra's ``server.LinuxDistribution``: the shell cats every
    ``/etc/*-release`` file, each preceded by its path and followed by a
    ``---`` separator, and the parts are replayed into a scratch ``etc``
    directory that the ``distro`` library parses -- the same library pyinfra
    uses. ``major`` and ``minor`` are resolved from the most precise source
    available: when ``/etc/os-release`` exposes only a major version (e.g.
    CentOS 8's ``VERSION_ID="8"``), the minor is read from the
    distro-specific release file instead; ``minor`` is None when no source
    provides it (e.g. CentOS Stream). For example::

        {
            "name": "Ubuntu",
            "major": 20,
            "minor": 4,
            "release_meta": {
                "NAME": "Ubuntu",
                "VERSION": "20.04 LTS (Focal Fossa)",
                "VERSION_ID": "20.04",
                "CODENAME": "Focal Fossa",
            },
        }

    ``name`` is the pretty form of the distro id (``centos`` -> ``CentOS``,
    ``rhel`` -> ``RedHat``); an id outside the known map keeps the name
    ``distro`` reports. ``release_meta`` holds the upper-cased
    ``/etc/os-release`` key/values (distro 1.7+'s ``RELEASE_CODENAME`` is
    dropped, like pyinfra). Empty release files (Arch's empty
    ``/etc/arch-release``) are skipped, and no release files at all read as
    ``{"name": None, "major": None, "minor": None, "release_meta": {}}``
    (pyinfra's ``default``). pyinfra's ``LinuxDistributionDict`` TypedDict is
    represented as a plain dict, like ``Last``'s record dicts.
    """

    #: Shell used to dump every ``/etc/*-release`` file over SSH: each file's
    #: path, its content and a ``---`` separator (``ls -pdL`` follows
    #: symlinks and drops directories).
    COMMAND = (
        'cd /etc/ && for file in $(ls -pdL *-release | grep -v /); '
        'do echo "/etc/${file}"; cat "/etc/${file}"; echo ---; '
        "done"
    )

    #: Pretty names for the distro ids pyinfra's ``name_to_pretty_name``
    #: recognises; any other id keeps the name ``distro`` reports.
    NAME_TO_PRETTY_NAME: ClassVar[dict[str, str]] = {
        "alpine": "Alpine",
        "centos": "CentOS",
        "fedora": "Fedora",
        "gentoo": "Gentoo",
        "opensuse": "openSUSE",
        "rhel": "RedHat",
        "ubuntu": "Ubuntu",
        "debian": "Debian",
    }

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, Any]:
        """Parse the ``---``-separated release-file dump into the info dict.

        The only side effect is local scratch space: the parts are written
        into a temporary ``etc`` directory so ``distro`` can parse them with
        its own precedence rules, and the directory is removed afterwards.
        Pure and testable -- feed it the canned shell output.
        """
        parts: dict[str, str] = {}
        for part in "\n".join(output).strip().split("---"):
            if not part.strip():
                continue
            try:
                filename, content = part.strip().split("\n", 1)
            except ValueError:
                # skip empty files, e.g. Arch's empty /etc/arch-release
                continue
            parts[filename] = content

        release_info: dict[str, Any] = {
            "name": None,
            "major": None,
            "minor": None,
            "release_meta": {},
        }
        if not parts:
            return release_info

        temp_root = mkdtemp()
        try:
            # pathlib is module-qualified: the ``Path`` fact below owns the
            # bare name in this module's namespace.
            temp_etc_dir = pathlib.Path(temp_root) / "etc"
            temp_etc_dir.mkdir()
            for filename, content in parts.items():
                target = temp_etc_dir / PurePosixPath(filename).name
                target.write_text(content, encoding="utf-8")

            parsed = distro.LinuxDistribution(
                root_dir=temp_root,
                include_lsb=False,
                include_uname=False,
            )

            release_meta = {
                key.upper(): value for key, value in parsed.os_release_info().items()
            }
            # distro 1.7+ adds this; pyinfra drops it to keep the meta stable.
            release_meta.pop("RELEASE_CODENAME", None)

            # _try_int returns its input on failure, so a missing version
            # part comes back as "" and maps to None below.
            major = _try_int(parsed.major_version(best=True))
            minor = _try_int(parsed.minor_version(best=True))

            release_info.update(
                {
                    "name": cls.NAME_TO_PRETTY_NAME.get(parsed.id(), parsed.name()),
                    "major": major if isinstance(major, int) else None,
                    "minor": minor if isinstance(minor, int) else None,
                    "release_meta": release_meta,
                }
            )
        finally:
            shutil.rmtree(temp_root)

        return release_info

    @classmethod
    async def process(cls, host: Host) -> dict[str, Any]:
        info = getattr(host, "linux_distribution", None)
        if info is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy the
            # nested release_meta dict so callers never mutate the simulated
            # state.
            return {
                key: dict(value) if isinstance(value, dict) else value
                for key, value in info.items()
            }
        # Real transport: dump the /etc/*-release files over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class LinuxGui:
    """The installed Linux GUIs, as a list of names (e.g. ``["GNOME"]``).

    Mirrors pyinfra's ``server.LinuxGui``: ``ls /usr/bin/*session || true``
    lists the desktop-session binaries under ``/usr/bin``, and each path
    recognised in ``KNOWN_GUI_BINARIES`` contributes its GUI name (GNOME,
    MATE, LXDE, KDE Plasma, XFCE 4). Lines are matched exactly (pyinfra
    does not strip them); anything else -- other ``*session`` files, stray
    output -- is ignored, and the listing order is preserved (no dedup, no
    sorting). Empty output -- no ``*session`` match, the ``ls`` error
    swallowed by ``|| true`` -- reads as ``[]`` (pyinfra's
    ``default = list``). ``HasGui`` is the boolean projection of this
    list, sharing the same shell and known-binary map.
    """

    #: Shell used to look for desktop-session binaries over SSH.
    COMMAND = "ls /usr/bin/*session || true"

    #: Session-binary path -> GUI name (pyinfra's ``known_gui_binaries``);
    #: ``HasGui.GUI_SESSION_BINARIES`` is its key set.
    KNOWN_GUI_BINARIES: ClassVar[dict[str, str]] = _KNOWN_GUI_BINARIES

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[str]:
        """Parse ``ls`` rows into the list of recognised GUI names.

        Lines are matched exactly against ``KNOWN_GUI_BINARIES`` (pyinfra
        does not strip them); unknown lines are skipped and the listing
        order is preserved. Pure and testable.
        """
        gui_names: list[str] = []
        for line in output:
            gui_name = cls.KNOWN_GUI_BINARIES.get(line)
            if gui_name:
                gui_names.append(gui_name)
        return gui_names

    @classmethod
    async def process(cls, host: Host) -> list[str]:
        guis = getattr(host, "linux_gui", None)
        if guis is not None:
            # SimulatedHost seam: read the in-memory list directly. Return a
            # copy so callers never mutate the simulated state.
            return list(guis)
        # Real transport: `ls /usr/bin/*session` over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class LinuxName:
    """The Linux distribution's pretty name (e.g. ``Ubuntu``), or None.

    Mirrors pyinfra's ``server.LinuxName``: a shortcut for the ``name`` key
    of the ``LinuxDistribution`` fact -- pyinfra implements it as a
    ``ShortFactBase`` over that fact, and reemote5 likewise delegates to
    ``LinuxDistribution.process`` rather than duplicating the shell and the
    ``distro`` parsing. The delegation also means the two facts share the
    ``linux_distribution`` SimulatedHost seam: there is deliberately no
    separate state field, so a simulated name can never disagree with the
    simulated distribution info. No release files at all read as ``None``
    (the ``name`` of pyinfra's ``default()``).
    """

    @classmethod
    async def process(cls, host: Host) -> str | None:
        # Delegation picks the transport: LinuxDistribution.process reads the
        # linux_distribution SimulatedHost seam or dumps the /etc/*-release
        # files over asyncssh; this fact only projects the name out.
        info = await LinuxDistribution.process(host)
        return info["name"]


class Locales:
    """The installed locales on the system, from ``locale -a``.

    Mirrors pyinfra's ``server.Locales``: every output line is a locale
    name, with lowercase ``utf8`` replaced by ``UTF-8`` to match the names
    in ``/etc/locale.gen``. Lines are kept verbatim otherwise -- the
    command's order is preserved, with no dedup or sorting -- for example::

        ["C.UTF-8", "en_US.UTF-8"]

    Empty output (the command failed) reads as ``[]``, pyinfra's
    ``default = list``; a *missing* ``locale`` exits 127, which
    ``run_shell`` reports as ``CommandNotFoundError`` instead.
    """

    #: Shell used to gather the installed locales over SSH.
    COMMAND = "locale -a"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[str]:
        """Parse ``locale -a`` rows into the list of locale names.

        Only the ``utf8`` -> ``UTF-8`` normalization is applied (pyinfra
        does not strip lines); the command's order is preserved.
        """
        return [line.replace("utf8", "UTF-8") for line in output]

    @classmethod
    async def process(cls, host: Host) -> list[str]:
        locales = getattr(host, "locales", None)
        if locales is not None:
            # SimulatedHost seam: read the in-memory list directly. Return a
            # copy so callers never mutate the simulated state.
            return list(locales)
        # Real transport: `locale -a` over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class LsbRelease:
    """The release information reported by ``lsb_release``, as a dict.

    Mirrors pyinfra's ``server.LsbRelease``: ``lsb_release -ca`` prints
    ``Key: value`` rows, and each colon-bearing line becomes a dict entry
    with a lower-cased key whose last word is kept (``Distributor ID``
    turns into ``id``) and a stripped value, for example::

        {
            "id": "Ubuntu",
            "description": "Ubuntu 18.04.3 LTS",
            "release": "18.04",
            "codename": "bionic",
        }

    Lines without a colon -- the ``No LSB modules are available.`` hint,
    blank lines, stray output -- are skipped, and only the first colon
    splits (a value may contain colons). Empty output (a failed read) parses
    to ``{}``; pyinfra's ``requires_command`` gating is deliberately dropped.
    A *missing* ``lsb_release`` exits 127, which ``run_shell`` reports as
    ``CommandNotFoundError`` rather than an empty dict.
    """

    #: Shell used to gather the release information over SSH.
    COMMAND = "lsb_release -ca"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, str]:
        """Parse ``lsb_release -ca`` rows into the release info dict.

        Keys are stripped, lower-cased and reduced to their last word;
        values are stripped and kept verbatim otherwise. Lines without a
        colon are ignored. Pure and testable.
        """
        items: dict[str, str] = {}
        for line in output:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip().lower()
            # Turn "distributor id" into "id" (pyinfra's last-word rule).
            if " " in key:
                key = key.split(" ")[-1]
            items[key] = value.strip()
        return items

    @classmethod
    async def process(cls, host: Host) -> dict[str, str]:
        release = getattr(host, "lsb_release", None)
        if release is not None:
            # SimulatedHost seam: read the in-memory state directly. Return a
            # copy so callers never mutate the simulated state.
            return dict(release)
        # Real transport: `lsb_release -ca` over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


# OpenBSD ``mount -v`` rows (pyinfra's ``_OPENBSD_MOUNT_V_RE``): the
# ``(diskname.label)`` part after the device is not always there.
_OPENBSD_MOUNT_V_RE = re.compile(
    r"""
        (\S+) (?:\ \(.*\))?\  # the (diskname.label) part isn't always there, hence optional group
        on\ (/.*)\            # *, not +, since root path "/" will be mounted
        type\ (\w+)\          # types from /sbin/mount_*
        \((.+)\)              # flags are in this group""",
    flags=re.VERBOSE,
)


def _unescape_octal(match: re.Match[str]) -> str:
    return chr(int(match.group(0)[1:], base=8))


def _replace_octal(value: str) -> str:
    """Decode Linux's ESCAPE_OCTAL escapes (``\\040`` space, ``\\054`` comma).

    ``/proc/self/mountinfo`` encodes special characters in mount points,
    devices and super-block options as three-digit octal escapes (linux's
    ``string_escape_mem`` with the ESCAPE_OCTAL flag).
    """
    return re.sub(r"\\[0-7]{3}", _unescape_octal, value)


class Mounts:
    """The mounted filesystems, as a dict of mount point -> info dict.

    Mirrors pyinfra's ``server.Mounts``: the shell is chosen from the
    ``Kernel`` fact -- FreeBSD lists ``mount -p --libxo json``, OpenBSD
    ``mount -v``, every other kernel cats ``/proc/self/mountinfo``. Each
    mount point maps to a ``{device, type, options}`` dict, for example::

        {
            "/": {
                "device": "/dev/mv2",
                "type": "ext4",
                "options": ["rw", "relatime"],
            },
        }

    On Linux the octal escapes in mount points, devices and super-block
    options (``\\040`` space, ``\\054`` comma, ``\\134`` backslash) are
    decoded, the optional ``shared``/``master``/``propagate_from``/
    ``unbindable`` tags are skipped, and ``options`` combines the per-mount
    options with the super-block ones -- ``ro``/``rw`` are filtered from
    the super-block side because the per-mount options override them
    (pyinfra's parser). On OpenBSD the ``ctime=...`` flag is dropped and
    lines that do not match the ``mount -v`` row shape are skipped.
    Empty output reads as ``{}`` on every branch (pyinfra's
    ``default = dict``; its FreeBSD JSON branch would raise on empty
    output -- a failed read here simply reports no mounts).
    """

    #: Shell listing the mounts on Linux (pyinfra's non-BSD branch).
    COMMAND_LINUX = "cat /proc/self/mountinfo"

    #: Shell listing the mounts as JSON on FreeBSD.
    COMMAND_FREEBSD = "mount -p --libxo json"

    #: Shell listing the mounts on OpenBSD.
    COMMAND_OPENBSD = "mount -v"

    @classmethod
    def command(cls, kernel: str) -> str:
        """Shell for the kernel name the ``Kernel`` fact reports.

        Only ``FreeBSD`` and ``OpenBSD`` select the BSD ``mount`` variants;
        every other answer (including a failed ``uname -s``, which reads as
        ``""``) takes the ``/proc/self/mountinfo`` branch, exactly like
        pyinfra.
        """
        kernel = kernel.strip()
        if kernel == "FreeBSD":
            return cls.COMMAND_FREEBSD
        if kernel == "OpenBSD":
            return cls.COMMAND_OPENBSD
        return cls.COMMAND_LINUX

    @classmethod
    def parse(cls, output: Iterable[str], kernel: str) -> dict[str, dict[str, Any]]:
        """Parse mount rows into the mount point -> info dict for ``kernel``.

        Dispatches on the kernel name exactly like ``command``: FreeBSD
        output is one JSON document, OpenBSD rows are ``mount -v`` lines,
        everything else is ``/proc/self/mountinfo`` rows. Pure and testable.
        """
        kernel = kernel.strip()
        if kernel == "FreeBSD":
            return cls._parse_freebsd(output)
        if kernel == "OpenBSD":
            return cls._parse_openbsd(output)
        return cls._parse_linux(output)

    @classmethod
    def _parse_freebsd(cls, output: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Parse ``mount -p --libxo json`` output (pyinfra's FreeBSD branch).

        The ``mount.fstab`` entries carry ``mntpoint``, ``device``,
        ``fstype`` and a comma-separated ``opts`` string whose items are
        stripped. Empty output maps to ``{}`` rather than a JSON error.
        """
        lines = [line for line in output if line.strip()]
        if not lines:
            return {}
        fstab = json.loads("\n".join(lines))["mount"]["fstab"]
        devices: dict[str, dict[str, Any]] = {}
        for entry in fstab:
            devices[entry["mntpoint"]] = {
                "device": entry["device"],
                "type": entry["fstype"],
                "options": [option.strip() for option in entry["opts"].split(",")],
            }
        return devices

    @classmethod
    def _parse_openbsd(cls, output: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Parse ``mount -v`` rows (pyinfra's OpenBSD branch).

        Rows that do not match the ``device on /path type (flags)`` shape
        are skipped; the flags are comma-split, stripped and any
        ``ctime=...`` entry is dropped (pyinfra's filter).
        """
        devices: dict[str, dict[str, Any]] = {}
        for line in output:
            m = _OPENBSD_MOUNT_V_RE.fullmatch(line)
            if m is None:
                continue
            devices[m[2]] = {
                "device": m[1],
                "type": m[3],
                "options": [
                    opt.strip(" ") for opt in m[4].split(",") if "ctime=" not in opt
                ],
            }
        return devices

    @classmethod
    def _parse_linux(cls, output: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Parse ``/proc/self/mountinfo`` rows (pyinfra's non-BSD branch).

        Blank lines are skipped. The per-mount options override the
        super-block ``ro``/``rw``, which are filtered so they never appear
        twice; mount point, device and super-block options are octal-
        unescaped. A repeated mount point keeps the last row, like pyinfra.
        """
        devices: dict[str, dict[str, Any]] = {}
        for line in output:
            if not line.strip():
                continue
            # ignore mount ID, parent ID, major:minor, root
            _, _, _, _, mount_point, mount_options, rest = line.split(" ", 6)
            # ignore optional tags "shared", "master", "propagate_from" and
            # "unbindable" up to the "-" separator
            while True:
                optional, rest = rest.split(" ", 1)
                if optional == "-":
                    break
            fs_type, mount_source, super_options = rest.split(" ")
            devices[_replace_octal(mount_point)] = {
                "device": _replace_octal(mount_source),
                "type": fs_type,
                "options": mount_options.split(",")
                + [
                    _replace_octal(opt)
                    for opt in super_options.split(",")
                    if opt not in ("ro", "rw")
                ],
            }
        return devices

    @classmethod
    async def process(cls, host: Host) -> dict[str, dict[str, Any]]:
        mounts = getattr(host, "mounts", None)
        if mounts is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy the
            # nested dicts and their options lists so callers never mutate
            # the simulated state.
            return {
                point: {**info, "options": list(info["options"])}
                for point, info in mounts.items()
            }
        # Real transport: the Kernel fact picks the shell (pyinfra reads it
        # inside command()), then its output is parsed the same way.
        kernel = await Kernel.process(host)
        stdout = await host.run_shell(cls.command(kernel))
        return cls.parse(stdout.splitlines(), kernel)


class Path:
    """The current user's ``PATH`` environment variable (e.g. ``/usr/bin:/bin``).

    Mirrors pyinfra's ``server.Path``: ``echo $PATH`` prints the
    colon-separated search path of the login user's shell, returned as the
    raw string -- not split into a list, so callers keep pyinfra's shape
    and can ``split(":")`` themselves. The first non-blank line is the
    answer; anything after it is ignored (``echo $PATH`` prints exactly one
    line). Empty output -- the command failed, or ``PATH`` is unset or
    empty -- maps to ``""``, like ``Arch``.
    """

    #: Shell used to gather the PATH environment variable over SSH.
    COMMAND = "echo $PATH"

    @classmethod
    def parse(cls, output: Iterable[str]) -> str:
        """Parse ``echo $PATH`` output into the colon-separated path string.

        The first non-blank line is the answer; anything after it is ignored.
        """
        for line in output:
            stripped = line.strip()
            if stripped:
                return stripped
        return ""

    @classmethod
    async def process(cls, host: Host) -> str:
        path = getattr(host, "os_path", None)
        if path is not None:
            # SimulatedHost seam: read the in-memory state directly.
            # str is immutable, so no copy is needed.
            return path
        # Real transport: `echo $PATH` over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


# macOS ps truncates any non-last text column (comm to 16 bytes, args to 64),
# so the Darwin branch runs ps twice with each of those columns last and joins
# the snapshots by PID across this marker (pyinfra's ``_DARWIN_ARGS_FIELDS``
# and ``_DARWIN_COMM_MARKER``).
_DARWIN_ARGS_FIELDS = "pid,user,stat,%cpu,%mem,args"
_DARWIN_COMM_MARKER = "===PYINFRA-PROCESSES-COMM==="


def _check_pid(pid: int | None) -> None:
    # The BusyBox branch interpolates the pid into an awk program, where
    # shell quoting cannot protect it, so a non-int pid is rejected outright.
    if pid is not None and not isinstance(pid, int):
        raise TypeError(f"pid must be an int or None, got {pid!r}")


class Processes:
    """The running processes, as a dict of PID -> info dict.

    Mirrors pyinfra's ``server.Processes``: the shell is chosen from the
    ``Kernel`` fact and a BusyBox probe -- Linux lists
    ``ps -eo pid,user,stat,%cpu,%mem,comm,args --no-headers``, FreeBSD the
    same ``ps -eo`` without ``--no-headers``, BusyBox (Alpine) the limited
    ``ps -o pid,user,vsz,stat,args`` columns, and Darwin runs ``ps`` twice
    (``args`` last, then ``comm`` last, separated by a marker line) because
    macOS truncates any non-last text column. Each PID maps to a
    ``{user, state, cpu_percent, mem_percent, command, args}`` dict, for
    example::

        {
            1: {
                "user": "root",
                "state": "Ss",
                "cpu_percent": 0.0,
                "mem_percent": 0.1,
                "command": "init",
                "args": "/sbin/init",
            },
        }

    An optional ``pid`` restricts the listing to that single process (an
    absent PID reads as ``{}``). On BusyBox ``cpu_percent``/``mem_percent``
    are always ``0.0`` and ``command`` is the basename of ``args``' first
    word; on Darwin a PID that vanished between the two ``ps`` snapshots
    is dropped. Header rows, blank lines, short rows and non-integer PIDs
    are skipped; empty output reads as ``{}`` (pyinfra's
    ``default = dict``).

    The BusyBox detection reads the ``Which`` fact (``command -v busybox
    || true``), exactly like pyinfra. One deliberate deviation: a missing
    Darwin marker line raises ``ValueError`` rather than pyinfra's bare
    ``Exception``.
    """

    #: Shell listing every process on Linux (pyinfra's non-BSD, non-BusyBox
    #: branch): ``--no-headers`` drops the header row.
    COMMAND_LINUX = "LANG=C ps -eo pid,user,stat,%cpu,%mem,comm,args --no-headers"

    #: Shell listing every process on FreeBSD, whose ps has no
    #: ``--no-headers`` (the header row is skipped by the parser).
    COMMAND_BSD = "LANG=C ps -eo pid,user,stat,%cpu,%mem,comm,args"

    #: Shell listing every process on BusyBox (Alpine): only pid, user,
    #: vsz, stat and args are reliable columns.
    COMMAND_BUSYBOX = "LANG=C ps -o pid,user,vsz,stat,args"

    #: Shell listing every process on Darwin: two ``ps -ww`` runs joined by
    #: the marker line, so neither ``args`` nor ``comm`` is truncated.
    COMMAND_DARWIN = (
        f"LANG=C ps -eo {_DARWIN_ARGS_FIELDS} -ww && "
        f"echo {_DARWIN_COMM_MARKER} && "
        "LANG=C ps -eo pid,comm -ww"
    )

    @classmethod
    def command(cls, kernel: str, busybox: bool = False, pid: int | None = None) -> str:
        """Shell for the kernel name and BusyBox presence, optionally one PID.

        Only ``Darwin`` selects the double-``ps`` pipeline and only
        ``FreeBSD`` the header-bearing BSD listing; every other kernel
        (including a failed ``uname -s``, which reads as ``""``) takes the
        Linux or BusyBox branch, exactly like pyinfra. A ``pid`` switches
        to ``ps -p`` (BusyBox has no ``-p``, so ``awk`` keeps the header
        and the one PID row).
        """
        _check_pid(pid)
        kernel = kernel.strip()
        if kernel == "Darwin":
            if pid is None:
                return cls.COMMAND_DARWIN
            quoted = shlex.quote(str(pid))
            return (
                f"LANG=C ps -p {quoted} -o {_DARWIN_ARGS_FIELDS} -ww && "
                f"echo {_DARWIN_COMM_MARKER} && "
                f"LANG=C ps -p {quoted} -o pid,comm -ww"
            )
        if busybox:
            fields = "pid,user,vsz,stat,args"
        else:
            fields = "pid,user,stat,%cpu,%mem,comm,args"
        if pid is not None:
            if busybox:
                return f"LANG=C ps -o {fields} | awk {shlex.quote(f'NR==1 || $1=={pid}')}"
            return f"LANG=C ps -p {shlex.quote(str(pid))} -o {fields}"
        if kernel == "FreeBSD":
            return cls.COMMAND_BSD
        if busybox:
            return cls.COMMAND_BUSYBOX
        return cls.COMMAND_LINUX

    @classmethod
    def parse(
        cls, output: Iterable[str], kernel: str, busybox: bool = False
    ) -> dict[int, dict[str, Any]]:
        """Parse ``ps`` rows into the PID -> info dict for the chosen shell.

        Dispatches exactly like ``command``: Darwin output is the two
        marker-separated snapshots, BusyBox rows the five-column listing,
        everything else the seven-column Linux/FreeBSD listing. Pure and
        testable.
        """
        if kernel.strip() == "Darwin":
            return cls._parse_darwin(output)
        if busybox:
            return cls._parse_busybox(output)
        return cls._parse_posix(output)

    @classmethod
    def _parse_posix(cls, output: Iterable[str]) -> dict[int, dict[str, Any]]:
        """Parse seven-column ``ps`` rows (pyinfra's Linux/FreeBSD branch).

        Each row is ``pid user stat %cpu %mem comm args``; ``args`` keeps
        the whole remainder (spaces included) and falls back to ``comm``
        when the row carries no separate args column. Blank lines, header
        rows, rows with fewer than six fields and non-integer PIDs are
        skipped.
        """
        processes: dict[int, dict[str, Any]] = {}
        for raw in output:
            line = raw.strip()
            if not line or line.startswith("PID"):
                continue
            parts = line.split(None, 6)
            if len(parts) < 6:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            processes[pid] = {
                "user": parts[1],
                "state": parts[2],
                "cpu_percent": float(parts[3]),
                "mem_percent": float(parts[4]),
                "command": parts[5],
                "args": parts[6] if len(parts) > 6 else parts[5],
            }
        return processes

    @classmethod
    def _parse_busybox(cls, output: Iterable[str]) -> dict[int, dict[str, Any]]:
        """Parse five-column BusyBox ``ps`` rows (pyinfra's BusyBox branch).

        Each row is ``pid user vsz stat args``; ``vsz`` is ignored,
        ``cpu_percent``/``mem_percent`` are always ``0.0`` (BusyBox reports
        neither) and ``command`` is the basename of ``args``' first word.
        """
        processes: dict[int, dict[str, Any]] = {}
        for raw in output:
            line = raw.strip()
            if not line or line.startswith("PID"):
                continue
            parts = line.split(None, 4)
            if len(parts) < 5:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            args = parts[4]
            processes[pid] = {
                "user": parts[1],
                "state": parts[3],
                "cpu_percent": 0.0,
                "mem_percent": 0.0,
                "command": args.split()[0].rsplit("/", 1)[-1] if args else "",
                "args": args,
            }
        return processes

    @classmethod
    def _parse_darwin(cls, output: Iterable[str]) -> dict[int, dict[str, Any]]:
        """Parse the two marker-separated Darwin snapshots (pyinfra's branch).

        The ``args`` snapshot supplies the record, the ``comm`` snapshot
        the untruncated ``command``; a PID missing from the second snapshot
        vanished between the two ``ps`` runs and is dropped rather than
        returned with an empty command. A missing marker line raises
        ``ValueError`` (pyinfra raises a bare ``Exception``).
        """
        args_lines: list[str] = []
        comm_lines: list[str] = []
        current = args_lines
        marker_seen = False
        for line in output:
            if line.strip() == _DARWIN_COMM_MARKER:
                marker_seen = True
                current = comm_lines
                continue
            current.append(line)
        if not marker_seen:
            raise ValueError(
                f"server.Processes: did not find the {_DARWIN_COMM_MARKER!r} "
                "marker in the ps output; the ps -eo/-p pipeline may have "
                "produced unexpected output"
            )
        processes: dict[int, dict[str, Any]] = {}
        for raw in args_lines:
            line = raw.strip()
            if not line or line.startswith("PID"):
                continue
            parts = line.split(None, 5)
            if len(parts) < 6:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            processes[pid] = {
                "user": parts[1],
                "state": parts[2],
                "cpu_percent": float(parts[3]),
                "mem_percent": float(parts[4]),
                "command": "",
                "args": parts[5],
            }
        for raw in comm_lines:
            line = raw.strip()
            if not line or line.startswith("PID"):
                continue
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            if pid in processes:
                processes[pid]["command"] = parts[1]
        return {pid: proc for pid, proc in processes.items() if proc["command"]}

    @classmethod
    async def process(cls, host: Host, pid: int | None = None) -> dict[int, dict[str, Any]]:
        # Validate the pid even on the simulated path so a bad argument is
        # rejected identically on both transports.
        _check_pid(pid)
        processes = getattr(host, "processes", None)
        if processes is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # each record dict so callers never mutate the simulated state;
            # a pid filter selects the single record (or {} when absent).
            if pid is not None:
                info = processes.get(pid)
                return {pid: dict(info)} if info is not None else {}
            return {proc_pid: dict(info) for proc_pid, info in processes.items()}
        # Real transport: the Kernel and Which facts pick the shell (pyinfra
        # reads both inside command()), then the ps output is parsed the same
        # way. BSD kernels never probe.
        kernel = await Kernel.process(host)
        busybox = False
        if kernel.strip() not in ("FreeBSD", "Darwin"):
            busybox = bool(await Which.process(host, "busybox"))
        stdout = await host.run_shell(cls.command(kernel, busybox, pid))
        return cls.parse(stdout.splitlines(), kernel, busybox)


class SystemdServiceState:
    """A unit's systemd ``ActiveState`` (``active``, ``inactive``, ...), or None.

    ``None`` means systemd reported nothing for the unit (``systemctl`` is
    missing or failed); an unknown unit reports ``inactive``, as systemd
    does. The value is the raw state string so a predicate (``is_active``)
    can decide what counts as "ready" -- ``activating`` is deliberately not
    ``active``, which is exactly what ``server.wait_service`` waits through.
    """

    @classmethod
    async def process(cls, host: Host, service: str) -> str | None:
        unit = unit_name(service)
        services = getattr(host, "services", None)
        if services is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return services.get(unit)
        # Real transport: `systemctl show` over asyncssh. A failure exits
        # non-zero with empty stdout (check is off in run_shell), which maps
        # to None just like the simulated absent-unit case.
        stdout = await host.run_shell(
            f"systemctl show -p ActiveState --value {shlex.quote(unit)}"
        )
        return stdout.strip() or None


class SystemdServiceEnabled:
    """Whether a unit is enabled on boot.

    Mirrors pyinfra's ``SystemdEnabled``: the unit's ``UnitFileState`` is
    read and ``enabled`` or ``static`` count as enabled; anything else
    (``disabled``, ``masked``, an empty answer) does not.
    """

    #: ``UnitFileState`` values that count as enabled (pyinfra's
    #: ``SystemdEnabled.state_values``).
    ENABLED_STATES = ("enabled", "static")

    @classmethod
    async def process(cls, host: Host, service: str) -> bool:
        unit = unit_name(service)
        enabled_services = getattr(host, "enabled_services", None)
        if enabled_services is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return unit in enabled_services
        # Real transport: `systemctl show` over asyncssh; a failure's empty
        # stdout maps to False.
        stdout = await host.run_shell(
            f"systemctl show -p UnitFileState --value {shlex.quote(unit)}"
        )
        return stdout.strip() in cls.ENABLED_STATES


class Timezone:
    """The current system timezone (e.g. ``Europe/Amsterdam``).

    Mirrors pyinfra's ``server.Timezone``: ``readlink -f /etc/localtime``
    resolves the zoneinfo symlink and ``sed`` strips everything up to
    ``zoneinfo/``, leaving the timezone name. When ``/etc/localtime`` is a
    copied file rather than a symlink into zoneinfo, the sed matches
    nothing and the resolved path is returned verbatim, exactly like
    pyinfra. The first non-blank line is the answer; anything after it is
    ignored (the pipeline prints exactly one line). Empty output (the
    command failed) maps to ``""``, like ``Arch`` -- pyinfra's
    ``output[0]`` would raise on empty output.
    """

    #: Shell used to gather the timezone over SSH: resolve the
    #: ``/etc/localtime`` symlink and strip the ``zoneinfo/`` prefix.
    COMMAND = "readlink -f /etc/localtime | sed 's|.*/zoneinfo/||'"

    @classmethod
    def parse(cls, output: Iterable[str]) -> str:
        """Parse the ``readlink``/``sed`` output into the timezone string.

        The first non-blank line is the answer; anything after it is ignored.
        """
        for line in output:
            stripped = line.strip()
            if stripped:
                return stripped
        return ""

    @classmethod
    async def process(cls, host: Host) -> str:
        timezone = getattr(host, "os_timezone", None)
        if timezone is not None:
            # SimulatedHost seam: read the in-memory state directly.
            # str is immutable, so no copy is needed.
            return timezone
        # Real transport: `readlink -f /etc/localtime` over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class Uptime:
    """The number of seconds the system has been up, or None.

    Mirrors pyinfra's ``server.Uptime``: the shell is chosen from the
    ``Kernel`` fact -- Darwin and FreeBSD print the current epoch seconds
    followed by the boot-time epoch seconds, and the uptime is their
    difference; every other kernel cuts the integer part out of
    ``/proc/uptime``, so the answer is whole seconds either way (e.g.
    ``86400`` -- one day). Blank lines are skipped; empty output (the
    command failed) reads as ``None``, pyinfra's ``FactBase`` default, so a
    failed read never masquerades as a freshly booted host. A non-integer
    line raises ``ValueError``, and a BSD answer missing its second line
    raises ``IndexError``, exactly like pyinfra's parser.
    """

    #: Shell reading the uptime on Linux (pyinfra's non-BSD branch): the
    #: integer seconds before ``/proc/uptime``'s decimal point.
    COMMAND_LINUX = "cut -d. -f1 /proc/uptime"

    #: Shell reading the uptime on Darwin/FreeBSD: the current epoch
    #: seconds, then the boot-time epoch seconds (``awk`` keeps the
    #: ``sysctl kern.boottime`` ``sec`` field, ``tr`` drops its comma).
    COMMAND_BSD = "date +%s; sysctl -n kern.boottime | awk '{print $4}' | tr -d ','"

    @classmethod
    def command(cls, kernel: str) -> str:
        """Shell for the kernel name the ``Kernel`` fact reports.

        Only ``Darwin`` and ``FreeBSD`` select the boot-time pipeline;
        every other answer (including a failed ``uname -s``, which reads
        as ``""``) takes the ``/proc/uptime`` branch, exactly like pyinfra.
        """
        if kernel.strip() in ("Darwin", "FreeBSD"):
            return cls.COMMAND_BSD
        return cls.COMMAND_LINUX

    @classmethod
    def parse(cls, output: Iterable[str], kernel: str) -> int | None:
        """Parse the uptime rows into whole seconds for ``kernel``.

        Dispatches on the kernel name exactly like ``command``: BSD output
        is ``now`` then ``boottime`` (their difference is the uptime),
        everything else is the single ``/proc/uptime`` integer. Blank
        lines are skipped and empty output reads as None. Pure and
        testable.
        """
        lines = [line.strip() for line in output if line.strip()]
        if not lines:
            return None
        if kernel.strip() in ("Darwin", "FreeBSD"):
            return int(lines[0]) - int(lines[1])
        return int(lines[0])

    @classmethod
    async def process(cls, host: Host) -> int | None:
        uptime = getattr(host, "uptime_seconds", None)
        if uptime is not None:
            # SimulatedHost seam: read the in-memory state directly.
            # int is immutable, so no copy is needed.
            return uptime
        # Real transport: the Kernel fact picks the shell (pyinfra reads it
        # inside command()), then its output is parsed the same way.
        kernel = await Kernel.process(host)
        stdout = await host.run_shell(cls.command(kernel))
        return cls.parse(stdout.splitlines(), kernel)


class User:
    """The name of the current user (e.g. ``root``).

    Mirrors pyinfra's ``server.User``: ``echo $USER`` prints the login
    user's name from the shell environment, returned as the raw string.
    The first non-blank line is the answer; anything after it is ignored
    (``echo $USER`` prints exactly one line). Empty output -- the command
    failed, or ``USER`` is unset or empty -- maps to ``""``, like ``Path``.
    """

    #: Shell used to gather the current user's name over SSH.
    COMMAND = "echo $USER"

    @classmethod
    def parse(cls, output: Iterable[str]) -> str:
        """Parse ``echo $USER`` output into the username string.

        The first non-blank line is the answer; anything after it is ignored.
        """
        for line in output:
            stripped = line.strip()
            if stripped:
                return stripped
        return ""

    @classmethod
    async def process(cls, host: Host) -> str:
        user = getattr(host, "os_user", None)
        if user is not None:
            # SimulatedHost seam: read the in-memory state directly.
            # str is immutable, so no copy is needed.
            return user
        # Real transport: `echo $USER` over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class Users:
    """The system users, as a dict of username -> details dict.

    Mirrors pyinfra's ``server.Users``: a shell loop joins each
    ``/etc/passwd`` entry with ``id``'s primary and secondary groups, the
    ``lastlog`` (``lastlogin`` on BSD) row and the shadow password hash,
    ``|``-separated. Each row becomes, for example::

        {
            "user_name": {
                "home": "/home/user_name",
                "comment": "Full Name",
                "shell": "/bin/bash",
                "group": "main_user_group",
                "groups": ["other", "groups"],
                "uid": 1000,
                "gid": 1000,
                "lastlog": "Sat Jun 5 12:03:23 -0600 2021",
                "login_time": datetime(2021, 6, 5, 12, 3, 23, ...),
                "password": "$6$...",
            },
        }

    ``home``, ``comment`` and ``shell`` are ``None`` when the passwd field
    is empty; ``groups`` holds only the secondary groups (``id -Gn``
    repeats the primary one); ``uid``/``gid`` are ints; ``lastlog`` is the
    raw timestamp matched out of the lastlog row (``None`` when the user
    never logged in) and ``password`` is the raw hash string (``""`` when
    the shadow file cannot be read). Empty output reads as ``{}``
    (pyinfra's ``default = dict``); blank lines are skipped, and a row
    missing its four ``|`` separators raises ``ValueError``, like
    pyinfra's parser.

    Deliberate deviation: pyinfra parses ``login_time`` with dateutil's
    fuzzy ``parse_date``; reemote5 shuns that dependency and instead tries
    the two fixed ``strptime`` layouts -- Linux ``lastlog`` (numeric zone
    before the year, an aware datetime) and BSD ``lastlogin`` (no zone, a
    naive datetime). A matched ``lastlog`` string that fits neither layout
    leaves ``login_time`` as ``None`` rather than guessing.
    """

    #: Shell used to gather the user rows over SSH (pyinfra's loop,
    #: verbatim): each passwd entry joined with its groups, lastlog row
    #: and password hash, ``|``-separated.
    COMMAND = """
        for i in `cat /etc/passwd | cut -d: -f1`; do
            ENTRY=`grep ^$i: /etc/passwd`;
            LASTLOG=`(((lastlog -u $i || lastlogin $i) 2> /dev/null) | grep ^$i | tr -s ' ')`;
            PASSWORD=`(grep ^$i: /etc/shadow || grep ^$i: /etc/master.passwd) 2> /dev/null | cut -d: -f2`;
            echo "$ENTRY|`id -gn $i`|`id -Gn $i`|$LASTLOG|$PASSWORD";
        done
    """.strip()

    #: pyinfra's regex spotting the login timestamp inside a lastlog row
    #: (the surrounding columns vary between Linux and BSD).
    _LASTLOG_RE = re.compile(r"[A-Z][a-z]{2} [A-Z][a-z]{2} {1,2}\d+ .+$")

    #: The known lastlog timestamp layouts: Linux ``lastlog`` prints a
    #: numeric zone before the year, BSD ``lastlogin`` prints neither.
    _LASTLOG_FORMATS = (
        "%a %b %d %H:%M:%S %z %Y",
        "%a %b %d %H:%M:%S %Y",
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Parse the ``|``-joined user rows into the name -> details dict.

        Blank lines are skipped; each remaining row must carry the four
        ``|`` separators (a malformed row raises ``ValueError``, like
        pyinfra). Pure and testable.
        """
        users: dict[str, dict[str, Any]] = {}
        for line in output:
            if not line.strip():
                continue
            entry, group, user_groups, lastlog, password = line.rsplit("|", 4)
            if not entry:
                continue
            entries = entry.split(":")
            # Only the secondary groups: id -Gn repeats the primary one.
            groups = [
                name for name in user_groups.split(" ") if name and name != group
            ]
            raw_login_time = None
            login_time = None
            login = cls._LASTLOG_RE.search(lastlog)
            if login:
                raw_login_time = login.group()
                login_time = cls._parse_login_time(raw_login_time)
            users[entries[0]] = {
                "home": entries[5] or None,
                "comment": entries[4] or None,
                "shell": entries[6] or None,
                "group": group,
                "groups": groups,
                "uid": int(entries[2]),
                "gid": int(entries[3]),
                "lastlog": raw_login_time,
                "login_time": login_time,
                "password": password,
            }
        return users

    @classmethod
    def _parse_login_time(cls, raw: str) -> datetime | None:
        """Try the known lastlog layouts; an unmatched layout stays None."""
        for fmt in cls._LASTLOG_FORMATS:
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        return None

    @classmethod
    async def process(cls, host: Host) -> dict[str, dict[str, Any]]:
        users = getattr(host, "os_users", None)
        if users is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy the
            # nested dicts (and any groups list) so callers never mutate the
            # simulated state.
            return {
                name: {
                    key: list(value) if isinstance(value, list) else value
                    for key, value in info.items()
                }
                for name, info in users.items()
            }
        # Real transport: the passwd/id/lastlog/shadow loop over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class Which:
    """The path of a given command according to ``command -v``, or None.

    Mirrors pyinfra's ``server.Which``: ``command -v <command> || true``
    prints the path the shell would run for the name (a builtin or alias
    reads as its own name), and the ``|| true`` swallows the non-zero exit
    of an unknown command so a missing binary never fails the read. The
    name is ``shlex``-quoted (pyinfra's ``QuoteString``), so a name carrying
    shell metacharacters is looked up literally rather than executed. The
    first non-blank line is the answer; anything after it is ignored
    (``command -v`` prints at most one line). Empty output -- the command
    is unavailable -- reads as ``None``, pyinfra's ``FactBase`` default, so
    callers can branch on the answer directly.
    """

    @classmethod
    def command(cls, command: str) -> str:
        """Shell looks the name up, mapping an unknown command to no output.

        The name is quoted normally (unlike the tilde facts), so even a
        name carrying shell metacharacters is a literal lookup.
        """
        return f"command -v {shlex.quote(command)} || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> str | None:
        """Parse ``command -v`` output into the path string, or None.

        The first non-blank line is the answer; empty output means the
        command is unavailable and reads as ``None``.
        """
        for line in output:
            stripped = line.strip()
            if stripped:
                return stripped
        return None

    @classmethod
    async def process(cls, host: Host, command: str) -> str | None:
        paths = getattr(host, "command_paths", None)
        if paths is not None:
            # SimulatedHost seam: command name -> resolved path. str is
            # immutable, so no copy is needed; an unknown command reads as
            # None, like the swallowed non-zero exit.
            return paths.get(command)
        # Real transport: `command -v` over asyncssh.
        stdout = await host.run_shell(cls.command(command))
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------


@dataclass(frozen=True)
class SystemctlCommand(Command):
    """One ``systemctl <action> [<unit>]`` invocation.

    ``action`` is one of ``daemon-reload`` (no unit), ``start``, ``stop``,
    ``restart``, ``enable`` or ``disable``.
    """

    action: str
    unit: str | None = None  # None only for daemon-reload

    def shell(self) -> str:
        if self.unit is None:
            return f"systemctl {self.action}"
        return f"systemctl {self.action} {shlex.quote(self.unit)}"


async def service(
    ctx: OpContext,
    service: str,
    running: bool = True,
    restarted: bool = False,
    enabled: bool | None = None,
    daemon_reload: bool = False,
) -> AsyncGenerator[Command]:
    """Manage the state of a systemd unit (pyinfra ``systemd.service`` parity).

    + service: name of the unit (``httpd`` or ``httpd.service``; a bare name
      gets ``.service`` appended)
    + running: whether the unit should be running
    + restarted: whether to restart the unit (only if it is already running)
    + enabled: whether the unit should be enabled on boot; ``None`` leaves
      the boot setting alone
    + daemon_reload: reload the systemd daemon to read updated unit files
      before anything else

    ``daemon_reload`` and ``restarted`` are stateless (always yield when set,
    like pyinfra's non-idempotent operations); ``running`` and ``enabled``
    compare against the ``SystemdServiceState``/``SystemdServiceEnabled``
    facts and yield only when the unit differs from the request. A unit is
    considered running only when its ``ActiveState`` is ``active``, so a
    ``start`` is issued for an ``activating`` unit too. The commands need
    root: flag the request ``sudo=True``. pyinfra's ``reloaded``, ``command``,
    ``user_mode``, ``machine`` and ``user_name`` options are not supported.
    """
    unit = unit_name(service)
    reasons: list[str] = []

    if daemon_reload:
        ctx.changed = True
        reasons.append("daemon-reload")
        yield SystemctlCommand(action="daemon-reload")

    state = await ctx.facts.get(SystemdServiceState, unit)
    is_running = state == "active"

    if running and not is_running:
        ctx.changed = True
        reasons.append(f"{unit} not running ({state}), will start")
        yield SystemctlCommand(action="start", unit=unit)
    elif not running and is_running:
        ctx.changed = True
        reasons.append(f"{unit} running, will stop")
        yield SystemctlCommand(action="stop", unit=unit)
    else:
        reasons.append(f"{unit} already {'running' if running else 'stopped'}")

    # Only restart a unit that is already running (a stopped one was just
    # started above, or is meant to stay stopped).
    if restarted and is_running:
        ctx.changed = True
        reasons.append(f"{unit} will restart")
        yield SystemctlCommand(action="restart", unit=unit)

    if enabled is not None:
        is_enabled = await ctx.facts.get(SystemdServiceEnabled, unit)
        if enabled and not is_enabled:
            ctx.changed = True
            reasons.append(f"{unit} not enabled, will enable")
            yield SystemctlCommand(action="enable", unit=unit)
        elif not enabled and is_enabled:
            ctx.changed = True
            reasons.append(f"{unit} enabled, will disable")
            yield SystemctlCommand(action="disable", unit=unit)
        else:
            reasons.append(f"{unit} already {'enabled' if enabled else 'disabled'}")

    ctx.reason = "; ".join(reasons)


def is_active(state: str | None) -> bool:
    """The ``server.wait_service`` predicate: the unit's state is ``active``.

    A module-level function, not a lambda: the deploy generator is re-invoked
    per phase, and the predicate ends up in the yielded descriptor, so it
    must be the same object every time.
    """
    return state == "active"


@dataclass(frozen=True)
class WaitForFactCommand(WaitCommand):
    """Poll a fact until ``predicate(value)`` holds, or time out.

    The generic wait descriptor: ``fact_cls`` is any fact class (one with an
    ``async def process(host, ...)`` classmethod), ``args``/``kwargs`` are
    forwarded to it, and ``predicate`` decides when the gathered value counts
    as "condition met". Every field must be hashable and stable across phases
    -- pass module-level fact classes and predicates (never a lambda built
    inside a generator) and ``kwargs`` as a sorted tuple of items -- because
    the descriptor is compared by value in plans.

    Its loggable form is the ``__str__`` rendering ``command_shell`` falls
    back to; there is no ``shell()``.
    """

    fact_cls: type
    args: tuple[Any, ...] = ()
    kwargs: tuple[tuple[str, Any], ...] = ()  # sorted items, to stay hashable
    predicate: Callable[[Any], bool] = bool
    timeout: float = 300.0  # seconds before giving up
    interval: float = 5.0  # seconds between polls

    def __str__(self) -> str:
        args = ", ".join(repr(arg) for arg in self.args)
        predicate = getattr(self.predicate, "__name__", "predicate")
        return (
            f"wait for {self.fact_cls.__name__}({args}) <{predicate}> "
            f"every {self.interval}s up to {self.timeout}s"
        )

    async def wait(self, host: Host) -> str:
        """Poll ``fact_cls`` on ``host`` until the predicate holds.

        The fact is read through ``fact_cls.process`` directly, never through
        the per-step ``FactCache``: the cache would pin the first value and
        the loop would never observe a change. The deadline is checked
        *after* each poll, so a zero timeout still polls once (an
        already-met condition always succeeds) and ``asyncio.sleep`` only
        runs between attempts.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout
        kwargs = dict(self.kwargs)
        polls = 0
        while True:
            polls += 1
            value = await self.fact_cls.process(host, *self.args, **kwargs)
            if self.predicate(value):
                return f"condition met after {polls} poll(s)"
            if loop.time() >= deadline:
                raise WaitTimeoutError(
                    f"host {host.name}: {self} -- condition not met within "
                    f"{self.timeout}s (last value: {value!r})"
                )
            await asyncio.sleep(self.interval)


async def wait_service(
    ctx: OpContext,
    service: str,
    timeout: float = 300.0,
    interval: float = 5.0,
) -> AsyncGenerator[Command]:
    """Wait for a systemd unit to become ``active``.

    + service: name of the unit (``httpd`` or ``httpd.service``)
    + timeout: seconds to keep polling before raising ``WaitTimeoutError``
    + interval: seconds between polls

    Reads no facts and always yields: like pyinfra's ``server.wait``, a wait
    is not idempotent. Whether the unit is already active is discovered at
    apply time by the driver's poll loop, not at plan time -- a fact read
    here would reflect pre-deployment state in the prepare phase and
    post-start state in the apply phase, and branching on it would break the
    determinism guard. The plan therefore always shows the wait as a change;
    the apply log reports the wait descriptor that ran. A unit that never
    becomes active aborts the run with ``WaitTimeoutError``.
    """
    unit = unit_name(service)
    ctx.changed = True
    ctx.reason = f"wait for {unit} to be active"
    yield WaitForFactCommand(
        fact_cls=SystemdServiceState,
        args=(unit,),
        predicate=is_active,
        timeout=timeout,
        interval=interval,
    )


__all__ = [
    "Arch",
    "AuthorizedKeys",
    "Date",
    "Groups",
    "HasGui",
    "Home",
    "Hostname",
    "Kernel",
    "KernelModules",
    "KernelVersion",
    "Last",
    "LinuxDistribution",
    "LinuxGui",
    "LinuxName",
    "Locales",
    "LsbRelease",
    "Mounts",
    "Path",
    "Processes",
    "SystemctlCommand",
    "SystemdServiceEnabled",
    "SystemdServiceState",
    "Timezone",
    "Uptime",
    "User",
    "Users",
    "WaitForFactCommand",
    "Which",
    "is_active",
    "service",
    "unit_name",
    "wait_service",
]
