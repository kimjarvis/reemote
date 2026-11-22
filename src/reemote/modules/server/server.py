# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The ``server`` module: host facts, ``Arch``, the ``group`` operation and waiting.
#
# Fact side (design doc section 5): ``Arch`` (the machine architecture from
# ``uname -m``), ``AuthorizedKeys`` (the SSH public keys listed in a user's
# ``~/.ssh/authorized_keys`` file), ``AuthorizedKeysFile`` (the raw text of
# one remote file, or None when it is absent), ``Date`` (the current datetime on the
# server, parsed from an ISO 8601 ``date`` timestamp), ``Env`` (the value
# of a given environment variable from ``echo $<name>``), ``EtcHosts`` (the
# hosts file parsed as a mapping of IP address to hostnames), ``Fstab`` (the
# ``/etc/fstab`` lines keyed by mount point, device for a ``none`` swap
# line), ``FipsEnabled``
# (whether the kernel is running in FIPS mode, from
# ``/proc/sys/crypto/fips_enabled``), ``Groups`` (the
# group names listed in ``/etc/group``), ``GroupDetails`` (the same file
# keyed by name with each group's ``gid`` and members, backing
# ``server.group``'s gid reconciliation), ``HasGui`` (whether a known
# desktop-session binary is installed), ``Home`` (a user's home directory, resolved by shell
# tilde expansion), ``Hostname`` (the server's hostname from ``uname -n``),
# ``Kernel`` (the kernel name from ``uname -s``), ``KernelModules`` (the
# loaded kernel modules from ``/proc/modules``),
# ``KernelVersion`` (the kernel release from ``uname -r``),
# ``Last`` (the login records parsed from ``last``), ``LinuxDistribution``
# (the distribution name and version parsed from the ``/etc/*-release``
# files), ``LinuxGui`` (the installed desktop sessions' GUI names),
# ``LinuxName`` (the distribution's pretty name, a shortcut over
# ``LinuxDistribution``), ``Locales`` (the installed locales from
# ``locale -a``, normalized to ``UTF-8`` spellings), ``LsbRelease`` (the
# release information parsed from ``lsb_release -ca``), ``Mounts`` (the
# mounted filesystems, keyed by mount point, read from
# ``/proc/self/mountinfo``), ``Port`` (whether a port has a listening socket, with the process
# and PID that own it, listed by ``ss`` or ``netstat``),
# ``Processes`` (the running processes keyed by PID, parsed from
# ``ps``), ``Timezone`` (the system timezone resolved from
# ``/etc/localtime``) and ``Uptime`` (the seconds since boot, from
# ``/proc/uptime``), ``User`` (the
# current user's name from ``echo $USER``), ``Users`` (the system users
# keyed by name, joined from ``/etc/passwd``, ``id``, ``lastlog`` and the
# shadow password files) and ``Which`` (the path of a given command from
# ``command -v``, or None when unavailable). Like every fact they are
# phase-unaware: each ``process`` coroutine just gathers the raw value.
#
# Operation side (design doc section 6): ``server.group`` plus the
# ``GroupAddCommand``/``GroupDelCommand``/``GroupModCommand`` descriptors it
# yields,
# ``server.hostname`` plus the ``SetHostnameCommand`` descriptor it yields,
# ``server.etc_hosts`` plus the ``EtcHostsUploadCommand``/
# ``EtcHostsInstallCommand`` spool-and-install pair it yields,
# ``server.fstab`` plus the ``FstabUploadCommand``/``FstabInstallCommand``
# spool-and-install pair it yields,
# ``server.kill`` plus the ``KillCommand`` descriptor it yields,
# ``server.locale`` plus the ``LocaleUploadCommand``/``LocaleInstallCommand``/
# ``LocaleGenCommand`` spool-and-install trio it yields,
# ``server.modprobe`` plus the ``ModprobeCommand`` descriptor it yields,
# ``server.mount`` plus the ``MountCommand`` descriptor it yields,
# ``server.security_limit`` plus the ``SecurityLimitUploadCommand``/
# ``SecurityLimitInstallCommand`` spool-and-install pair it yields,
# ``server.timezone`` plus the ``SetTimezoneCommand`` descriptor it yields,
# ``server.user`` plus the ``UserAddCommand``/``UserModCommand``/
# ``UserDelCommand`` descriptors it yields (with the private
# ``_HomeMkdirCommand``/``_HomeChownCommand`` home-directory pair),
# ``server.user_authorized_keys`` plus the ``AuthorizedKeysUploadCommand``/
# ``AuthorizedKeysInstallCommand`` spool-and-install pair it yields (with
# the private ``_KeysPathStat`` probe and the
# ``_KeysChmodCommand``/``_KeysTouchCommand`` stand-ins beside the reused
# home-directory pair), and
# ``server.wait`` plus the ``WaitForFactCommand`` descriptor it yields, and
# ``server.wait_service`` plus the ``WaitForFactCommand`` descriptor it yields.
# ``server.group`` mirrors the baseline's ``server.group`` for ``group``,
# ``present``, ``system`` and ``gid``: it yields ``groupadd``/``groupdel``.
# ``server.hostname`` mirrors the baseline's ``server.hostname`` for the hostname
# itself: the ``Which`` fact picks ``hostnamectl set-hostname`` when
# ``hostnamectl`` is available and the bare ``hostname`` command otherwise;
# The baseline's ``hostname_file`` upload branch is deliberately dropped.
# ``server.etc_hosts`` mirrors the baseline's ``server.etc_hosts`` for ``ip``,
# ``hostnames``, ``present`` and ``path``: the ``EtcHosts`` fact gates the
# no-op and the ``AuthorizedKeysFile`` raw-content fact feeds a pure rewrite
# that is spooled and installed over the hosts file (see the operation's
# comment block for the family details).
# ``server.fstab`` is the original Ansible-``mount``-parity operation for
# the persistent half (issue #48): the ``Fstab`` fact gates the no-op and
# the ``AuthorizedKeysFile`` raw-content fact feeds a pure six-column
# rewrite that is spooled and installed over ``/etc/fstab``, keyed by mount
# point (device for a ``none`` swap line).
# ``server.kill`` mirrors the baseline's ``server.kill`` for ``pid`` and
# ``signal``: stateless and non-idempotent, it reads no fact and always
# yields one ``kill -<signal> <pid>`` command.
# ``server.locale`` mirrors the baseline's ``server.locale`` for ``locale`` and
# ``present``: the ``Locales`` fact gates the no-op and the
# ``AuthorizedKeysFile`` raw-content fact feeds a pure definition-line edit
# that is spooled and installed over ``/etc/locale.gen``, followed by one
# ``locale-gen`` regeneration (see the operation's comment block for the
# family details).
# ``server.modprobe`` mirrors the baseline's ``server.modprobe`` for ``module``,
# ``present`` and ``force``: it compares the names against the
# ``KernelModules`` fact and yields one ``modprobe [-f] [-r] -a <modules>``
# command only for the modules that differ from the request.
# ``server.mount`` mirrors the baseline's ``server.mount`` for ``path``,
# ``mounted``, ``options``, ``device`` and ``fs_type``: it compares against
# the ``Mounts`` fact (with a lookup-by-device fallback) and yields one
# ``MountCommand`` -- a fresh ``mount``, an ``umount`` or an options remount
# (``mount -o remount,<options> <path>``).
# ``server.security_limit`` mirrors the baseline's ``server.security_limit`` for
# ``domain``, ``limit_type``, ``item`` and ``value``: the
# ``AuthorizedKeysFile`` raw-content fact feeds a pure limits.conf line edit
# that is spooled and installed over ``/etc/security/limits.conf`` (see the
# operation's comment block for the family details).
# ``server.service``, the ``SystemctlCommand`` descriptor and the
# ``SystemdServiceState``/``SystemdServiceEnabled`` facts were relocated to
# the ``systemd`` namespace and are re-exported here for backward
# compatibility (``server.service`` is a deprecated alias of
# ``systemd.service``). ``server.wait_service`` below still polls the
# relocated ``SystemdServiceState`` fact. ``Sysctl`` was relocated to the
# ``sysctl`` namespace -- the canonical home for everything sysctl -- and
# is NOT re-exported here: ``reemote.Sysctl`` now binds ``sysctl.Sysctl``.
# ``server.user`` mirrors the baseline's ``server.user`` for ``user``, ``present``,
# ``home``, ``shell``, ``group``, ``groups``, ``append``, ``ensure_home``,
# ``create_home``, ``system``, ``uid``, ``comment``, ``unique`` and
# ``password``: the requested state is compared against the ``Users`` fact
# and one ``useradd``/``usermod``/``userdel`` is yielded only where they
# differ; the
# ``ensure_home`` branch reads a private ``_HomeDirectory`` stand-in for
# ``files.Directory`` and yields the ``mkdir -p``/``chown`` pair. The baseline's
# ``public_keys``/``delete_keys`` branch and its ``Which``
# useradd/usermod/userdel existence checks are deliberately dropped -- both
# halves of that branch now live in their own operation,
# ``server.user_authorized_keys`` below (append and ``delete_keys`` rewrite
# alike, through its spool-and-install pair), but ``server.user`` itself
# still does not wire either option through it.
# ``server.timezone`` mirrors the baseline's ``server.timezone`` for ``timezone``
# on systemd systems only: it compares against the ``Timezone`` fact and
# yields ``timedatectl set-timezone`` when they differ; the baseline's non-systemd
# fallback (symlinking ``/etc/localtime`` and writing ``/etc/timezone``) is
# deliberately dropped, and a host without ``timedatectl`` raises
# ``OperationError`` during prepare instead.
#
# ``server.wait`` mirrors the baseline's ``server.wait`` for ``port``, the only
# option upstream has: the baseline loops remotely over ``netstat`` until the port
# appears, reemote describes the wait and lets the driver poll the ``Port``
# fact until ``is_listening`` holds, bounded by ``timeout``/``interval``.
#
# ``WaitForFactCommand`` is the *non-shell* wait descriptor: it carries a fact,
# its arguments and a predicate, and its ``wait`` polls the fact (bypassing
# the per-step ``FactCache``) until the predicate holds or the timeout
# expires. It is generic -- any fact and predicate -- and lives here beside its
# two consumers, ``server.wait`` and ``server.wait_service``.
# """

import asyncio
import hashlib
import io
import pathlib
import re
import shlex
import shutil
from collections.abc import AsyncGenerator, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from tempfile import mkdtemp
from typing import Any, ClassVar, Literal, TypedDict

import distro

from ...commands import Command, TransferCommand, WaitCommand, WaitTimeoutError
from ...core import OpContext, OperationError
from ...hosts import Host
from ..systemd.systemd import (
    SystemctlCommand,
    SystemdServiceEnabled,
    SystemdServiceState,
    service,
    unit_name,
)

# --- Facts -------------------------------------------------------------------


class Arch:
    """The system architecture according to ``uname`` (e.g. ``x86_64``).

    **Commands:**

    ```bash
    uname -m
    ```

    **Returns:**

    ```python
    str
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/server_arch.py``.
    """

    # Mirrors the baseline's ``server.Arch``: the first line of ``uname -m`` output
    # is the architecture string. ``uname -m`` is used rather than ``uname -p``
    # because the latter is not portable and reports ``unknown`` on Debian.
    # Empty output (the command failed) maps to ``""``.

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
# (the baseline's ``_SAFE_USERNAME_RE``).
_SAFE_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._][a-zA-Z0-9._-]*$")


def _check_tilde_username(user: str) -> None:
    if user and not _SAFE_USERNAME_RE.match(user):
        raise ValueError(f"Unsafe username for shell tilde expansion: {user!r}")


class AuthorizedKeys:
    """The SSH public keys listed in a user's ``~/.ssh/authorized_keys`` file.

    **Commands:**

    ```bash
    cat ~<user>/.ssh/authorized_keys 2>/dev/null || true
    ```

    **Returns:**

    ```python
    list[str]
    ```

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/server_authorized_keys.py``.
    """

    # Mirrors the baseline's ``server.AuthorizedKeys``: the full key strings are
    # returned as a list in the file's order; empty lines and lines starting
    # with ``#`` are skipped. A missing or empty file reads as ``[]`` (the
    # command swallows ``cat``'s error), for example::
    #
    #     [
    #         "ssh-ed25519 AAAAC3Nz... user@host",
    #         "ssh-rsa AAAAB3Nz... other@host",
    #     ]
    #
    # ``user`` selects whose file is read via shell tilde expansion
    # (``~user/.ssh/authorized_keys``); an empty ``user`` means the login
    # user's own ``~``. Because ``~user`` must stay an unquoted bare word, an
    # unsafe username raises ``ValueError`` instead of reaching the shell.
    # An explicit ``path`` reads that file instead (``shlex``-quoted:
    # ``cat <path> 2>/dev/null || true``), which
    # is how the baseline's ``server.authorized_keys`` operation supports
    # non-default key files.

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


class AuthorizedKeysFile:
    """The current content of one remote file, or None when absent.

    The namespace's path-generic raw-content fact: read by
    ``server.user_authorized_keys`` for the key file, by
    ``server.etc_hosts`` for the hosts file it rewrites, by
    ``server.locale`` for ``/etc/locale.gen`` and by
    ``server.security_limit`` for ``/etc/security/limits.conf``.

    **Commands:**

    ```bash
    test -e <path> && { printf '#reemote-authorizedkeysfile\\n'; cat -- <path>; }
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
        Examples can be found in
        ``examples/modules/server/server_user_authorized_keys.py``.
    """

    # The path-generic raw-content fact of this namespace (the
    # ``AptSourcesFile``/``DnfRepoFile`` pattern): ``server.user_authorized_keys``
    # reads it to compute the content it spools back, in the append and the
    # ``delete_keys`` rewrite direction alike, and ``server.etc_hosts``,
    # ``server.locale`` and ``server.security_limit`` are its further readers (the
    # ``AptSourcesFile``-backs-``apt.repo`` reuse) -- the fact never
    # interprets the text. Where ``AuthorizedKeys`` parses
    # the key lines into a list (stripping whitespace, skipping blanks and
    # ``#`` comments), this
    # reports the raw text of a *single* file so the operation can preserve
    # every comment, blank line and ordering the parser drops, and can tell
    # an existing file from a missing one. Absence is reported as ``None``
    # -- never the empty string -- so a file that exists but is empty stays
    # distinguishable from one that is missing. Unlike ``AuthorizedKeys``
    # there is no tilde branch: the operation resolves ``Home`` first and
    # always passes the explicit key-file path, which is also what lets the
    # simulated read ride the literal-path ``remote_files`` seam.

    #: Marker printed before the ``cat``'d content, only when ``test -e``
    #: passes, so ``process`` can tell an existing (possibly empty) file from
    #: a missing one (no marker on stdout).
    MARKER = "#reemote-authorizedkeysfile"

    @classmethod
    def command(cls, path: str) -> str:
        """Shell printing ``MARKER`` then the file's content, only if it exists.

        The ``test -e`` guard means a missing file produces no output at all
        (not even the marker), while an existing one -- empty or not -- always
        leads with the marker line (``AptSourcesFile.command``'s form).
        """
        quoted = shlex.quote(path)
        return f"test -e {quoted} && {{ printf '{cls.MARKER}\\n'; cat -- {quoted}; }}"

    @classmethod
    async def process(cls, host: Host, path: str) -> str | None:
        remote_files = getattr(host, "remote_files", None)
        if remote_files is not None:
            # SimulatedHost seam: decode the content the simulated remote
            # filesystem holds at the path (None when absent). The same store
            # the spool-and-install mutator writes the installed bytes back
            # into, so a simulated write reads back through this fact.
            data = remote_files.get(path)
            return data.decode("utf-8") if data is not None else None
        # Real transport: cat the file over asyncssh behind the marker, so a
        # missing file (no marker) reads as None rather than empty content.
        stdout = await host.run_shell(cls.command(path))
        prefix = f"{cls.MARKER}\n"
        if not stdout.startswith(prefix):
            return None
        return stdout[len(prefix) :]


#: ``date`` format string producing an ISO 8601 timestamp with a numeric
#: UTC offset (the baseline's ``ISO_DATE_FORMAT``); used both to build the shell
#: and to parse its output back into a ``datetime``.
_ISO_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


class Date:
    """The current datetime on the server, as a timezone-aware ``datetime``.

    **Commands:**

    ```bash
    date +'%Y-%m-%dT%H:%M:%S%z'
    ```

    **Returns:**

    ```python
    datetime | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/server_date.py``.
    """

    # Mirrors the baseline's ``server.Date``: ``date +'%Y-%m-%dT%H:%M:%S%z'`` prints
    # an ISO 8601 timestamp with a numeric UTC offset (e.g.
    # ``2019-01-01T00:00:00+0000``), which is parsed with ``datetime.strptime``
    # into an aware ``datetime``, for example::
    #
    #     datetime(2019, 1, 1, 0, 0, tzinfo=timezone.utc)
    #
    # The first non-blank line is the answer; anything after it is ignored.
    # Empty output (the command failed) maps to ``None`` -- the baseline's
    # ``default = datetime.now`` fallback to the *control* machine's clock is
    # deliberately dropped, so a failed read never masquerades as a host time.

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


# Environment variable names interpolated into ``echo $<name>`` cannot be
# quoted without disabling the expansion, so the value must be a plain
# shell identifier: a letter or underscore followed by letters, digits or
# underscores (the ``_check_tilde_username`` validation pattern).
_SAFE_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _check_env_name(name: str) -> None:
    if not _SAFE_ENV_NAME_RE.match(name):
        raise ValueError(
            f"Unsafe environment variable name for shell expansion: {name!r}"
        )


class Env:
    """The value of a given environment variable on the server (e.g. ``PATH``).

    **Commands:**

    ```bash
    echo $<name>
    ```

    **Returns:**

    ```python
    str
    ```

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/server_env.py``.
    """

    # Original reemote fact replacing the migrated ``server.Path``: it
    # generalizes the baseline's ``echo $PATH`` to any variable name, so
    # ``Env.process(host, "PATH")`` issues the same shell and returns the
    # same raw colon-separated string ``Path`` used to -- not split into a
    # list, so callers can ``split(":")`` themselves. The first non-blank
    # line is the answer; anything after it is ignored (``echo`` prints the
    # value on one line). Empty output -- the variable is unset or empty --
    # maps to ``""``, like ``Arch``. Because ``$<name>`` must stay an
    # unquoted bare word for the shell to expand it, an unsafe name raises
    # ``ValueError`` instead of reaching the shell (the ``Home``
    # tilde-validation pattern). ``echo`` is a shell builtin, so the read
    # never exits 127. ``User`` keeps its own dedicated ``echo $USER`` fact.

    @classmethod
    def command(cls, name: str) -> str:
        """Shell echoes the named variable, an unset one printing a blank line.

        The name is validated rather than quoted: quoting ``$name`` would
        disable the expansion the fact relies on.
        """
        _check_env_name(name)
        return f"echo ${name}"

    @classmethod
    def parse(cls, output: Iterable[str]) -> str:
        """Parse ``echo $<name>`` output into the variable's value string.

        The first non-blank line is the answer; anything after it is ignored.
        """
        for line in output:
            stripped = line.strip()
            if stripped:
                return stripped
        return ""

    @classmethod
    async def process(cls, host: Host, name: str) -> str:
        # Build the shell first, even on the simulated path, so an unsafe
        # variable name is rejected identically on both transports.
        shell = cls.command(name)
        env = getattr(host, "os_env", None)
        if env is not None:
            # SimulatedHost seam: variable name -> value. str is immutable,
            # so no copy is needed; an unlisted variable reads as "", like
            # the blank line echo prints for an unset one.
            return env.get(name, "")
        # Real transport: `echo $<name>` over asyncssh.
        stdout = await host.run_shell(shell)
        return cls.parse(stdout.splitlines())


class EtcHosts:
    """``/etc/hosts`` parsed as a mapping of IP address to hostnames.

    **Commands:**

    ```bash
    cat <path> 2>/dev/null || true
    ```

    **Returns:**

    ```python
    dict[str, list[str]]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/server_etc_hosts.py``.
    """

    # Mirrors the baseline's ``server.EtcHosts``: the hosts file at ``path``
    # (``/etc/hosts`` by default) is parsed into a mapping of IP address to
    # the hostnames declared on the matching lines, for example::
    #
    #     {
    #         "127.0.0.1": ["localhost", "localhost.localdomain"],
    #         "::1": ["localhost"],
    #         "192.168.1.10": ["db.internal"],
    #     }
    #
    # Comments (everything from ``#`` on) and blank lines are ignored, lines
    # without at least two tokens are skipped, and when the same IP is listed
    # more than once its hostnames are merged in file order without
    # duplicates. A missing file (the ``cat`` error swallowed by ``|| true``)
    # reads as ``{}``, the baseline's ``default = dict``. The simulated seam rides
    # the literal-path ``remote_files`` store (the ``sysctl.SysctlConf``
    # pattern) rather than a dedicated parsed field: the same store the
    # ``install_etc_hosts_file`` mutator writes the installed bytes back
    # into, so a simulated write always reads back consistently through this
    # fact with no second store to keep in sync.

    @classmethod
    def command(cls, path: str = "/etc/hosts") -> str:
        """Shell cats the hosts file, a missing one printing nothing.

        The path is ``shlex``-quoted (the baseline's ``QuoteString``); the
        ``|| true`` guard makes a missing file an empty read, never an error.
        """
        return f"cat {shlex.quote(path)} 2>/dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, list[str]]:
        """Parse hosts-file rows into the IP -> hostnames mapping.

        Comments and blank lines are dropped, lines with fewer than two
        tokens are skipped, and repeated IPs merge their hostnames in file
        order without duplicates (the baseline's parse verbatim).
        """
        entries: dict[str, list[str]] = {}
        for raw in output:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            ip, hostnames = parts[0], parts[1:]
            existing = entries.setdefault(ip, [])
            for name in hostnames:
                if name not in existing:
                    existing.append(name)
        return entries

    @classmethod
    async def process(cls, host: Host, path: str = "/etc/hosts") -> dict[str, list[str]]:
        remote_files = getattr(host, "remote_files", None)
        if remote_files is not None:
            # SimulatedHost seam: parse the content the simulated remote
            # filesystem holds at the path; an absent file reads as {}.
            data = remote_files.get(path)
            if data is None:
                return {}
            return cls.parse(data.decode("utf-8").splitlines())
        # Real transport: cat the hosts file over asyncssh.
        stdout = await host.run_shell(cls.command(path))
        return cls.parse(stdout.splitlines())


class FipsEnabled:
    """Whether the kernel is running in FIPS mode (Linux only).

    **Commands:**

    ```bash
    cat /proc/sys/crypto/fips_enabled 2>/dev/null || true
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
        Examples can be found in ``examples/modules/server/server_fips_enabled.py``.
    """

    # Ansible's ``ansible_facts.fips`` parity: the kernel exposes its FIPS
    # (Federal Information Processing Standards) crypto mode through
    # ``/proc/sys/crypto/fips_enabled`` -- ``1`` when the host booted with
    # ``fips=1`` and the crypto self-tests passed, ``0`` otherwise. The file
    # is absent on kernels without FIPS support (and on non-Linux hosts), so
    # the ``cat`` error is swallowed by ``|| true`` and an absent file reads
    # as ``False``, exactly like an explicit ``0``. Only a ``1`` line means
    # enabled; anything else -- ``0``, blank or stray output -- is ``False``.
    # A deploy can branch on it the way ``geerlingguy.postgresql`` does:
    # ``postgresql_auth_method: "{{ ansible_facts.fips | ternary('scram-sha-256', 'md5') }}"``
    # becomes a plain ``"scram-sha-256" if fips else "md5"`` in the program's
    # gather-then-plan step, because md5 is unavailable on a FIPS host.

    #: Shell reading the kernel's FIPS flag: a missing file prints nothing.
    COMMAND = "cat /proc/sys/crypto/fips_enabled 2>/dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> bool:
        """Parse the ``/proc/sys/crypto/fips_enabled`` row into the flag.

        The first non-blank line is the answer and only ``"1"`` reads as
        True; ``"0"``, empty output (the file is absent) and stray lines all
        read as False.
        """
        for line in output:
            stripped = line.strip()
            if stripped:
                return stripped == "1"
        return False

    @classmethod
    async def process(cls, host: Host) -> bool:
        fips_enabled = getattr(host, "fips_enabled", None)
        if fips_enabled is not None:
            # SimulatedHost seam: read the in-memory flag directly.
            # bool is immutable, so no copy is needed.
            return fips_enabled
        # Real transport: cat /proc/sys/crypto/fips_enabled over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class FstabEntry(TypedDict):
    """
    Attributes:
        device: The first column verbatim -- the device path or its
            ``UUID=``/``LABEL=``/``PARTUUID=`` spelling, exactly as written.
        mountpoint: The second column (``none`` for a swap entry).
        fs_type: The third column (``swap`` for a swap entry).
        options: The fourth column split on commas, in file order.
        dump: The fifth column as an int, or ``None`` when the row spells it
            non-numerically (a stray or malformed line the parser keeps).
        passno: The sixth column as an int, or ``None`` on a non-numeric
            spelling.
    """

    # The single record ``Fstab`` returns for one entry, the ``PortInfo``/
    # ``AptSource`` shape: ``options`` is a tuple so the record stays
    # hashable and value-equal across the two deploy phases, and the numeric
    # columns are ``None`` rather than a crash when the file spells them
    # oddly.
    device: str
    mountpoint: str
    fs_type: str
    options: tuple[str, ...]
    dump: int | None
    passno: int | None


class Fstab:
    """``/etc/fstab`` parsed as a mapping of mount point to fstab entry.

    **Commands:**

    ```bash
    cat <path> 2>/dev/null || true
    ```

    **Returns:**

    ```python
    dict[str, FstabEntry]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/server_fstab.py``.
    """

    # Original reemote fact giving Ansible's ``mount`` module its persistent
    # half (issue #48; the baseline has no fstab fact and never reads the
    # static table): each active line of the file at ``path`` (``/etc/fstab``
    # by default) becomes a ``FstabEntry`` keyed by its mount point, for
    # example::
    #
    #     {
    #         "/": {"device": "/dev/mapper/root", "mountpoint": "/",
    #               "fs_type": "ext4", "options": ("errors=remount-ro",),
    #               "dump": 0, "passno": 1},
    #         "/swapfile": {"device": "/swapfile", "mountpoint": "none",
    #                       "fs_type": "swap", "options": ("sw",),
    #                       "dump": 0, "passno": 0},
    #     }
    #
    # Comments (everything from ``#`` on) and blank lines are skipped, lines
    # with fewer than six columns are not entries, and whitespace -- tabs
    # included -- separates the columns, so an equivalent line in another
    # spacing parses identically. Swap entries all spell ``none`` as their
    # mount point -- Ansible's ``name: none`` form, where the fstab line is
    # the managed object and the mount point cannot key it -- so a ``none``
    # line is keyed by its device instead: the fact keys each entry exactly
    # the way Ansible's ``mount`` module identifies it (by ``name``, by
    # ``src`` when the name is ``none``). A repeated key keeps the later
    # line, like ``Mounts``. The file is world-readable, so the plain
    # unprivileged ``cat`` of the ``EtcHosts`` shape suffices; a missing
    # file (the ``cat`` error swallowed by ``|| true``) reads as ``{}``.

    @classmethod
    def command(cls, path: str = "/etc/fstab") -> str:
        """Shell cats the fstab file, a missing one printing nothing.

        The path is ``shlex``-quoted (the baseline's ``QuoteString``); the
        ``|| true`` guard makes a missing file an empty read, never an error
        (``EtcHosts.command``'s form).
        """
        return f"cat {shlex.quote(path)} 2>/dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, FstabEntry]:
        """Parse fstab rows into the mount point -> ``FstabEntry`` mapping.

        Comments and blank lines are dropped, lines with fewer than six
        columns are skipped, tabs and spaces separate fields alike, and a
        non-numeric ``dump``/``passno`` column reads as ``None`` rather than
        failing the whole parse. A ``none`` mount point keys the entry by
        device (the ``name: none`` swap form); anything else by mount point.
        Pure and testable.
        """
        entries: dict[str, FstabEntry] = {}
        for raw in output:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            columns = line.split()
            if len(columns) < 6:
                continue
            device, mountpoint, fs_type, options, dump, passno = columns[:6]
            entry: FstabEntry = {
                "device": device,
                "mountpoint": mountpoint,
                "fs_type": fs_type,
                "options": tuple(options.split(",")),
                "dump": int(dump) if dump.isdigit() else None,
                "passno": int(passno) if passno.isdigit() else None,
            }
            entries[device if mountpoint == "none" else mountpoint] = entry
        return entries

    @classmethod
    async def process(cls, host: Host, path: str = "/etc/fstab") -> dict[str, FstabEntry]:
        remote_files = getattr(host, "remote_files", None)
        if remote_files is not None:
            # SimulatedHost seam: parse the content the simulated remote
            # filesystem holds at the path; an absent file reads as {}. The
            # same store the ``install_fstab_file`` mutator writes the
            # installed bytes back into (the ``EtcHosts`` pattern), so a
            # simulated write always reads back consistently through this
            # fact with no second store to keep in sync.
            data = remote_files.get(path)
            if data is None:
                return {}
            return cls.parse(data.decode("utf-8").splitlines())
        # Real transport: `cat /etc/fstab` over asyncssh.
        stdout = await host.run_shell(cls.command(path))
        return cls.parse(stdout.splitlines())


class GroupEntry(TypedDict):
    """
    Attributes:
        gid: The group's numeric id (the third ``/etc/group`` column) as an
            int, or ``None`` when the column spells it non-numerically (a
            malformed row the parser keeps rather than fails).
        members: The fourth column split on commas, in file order, with any
            empty member dropped (an empty column is a group with no
            members, not a member named by the empty string).
    """

    # The single record ``GroupDetails`` returns for one group, the
    # ``FstabEntry``/``PortInfo`` shape: ``members`` is a tuple so the record
    # stays hashable and value-equal across the two deploy phases, and a
    # non-numeric gid reads as ``None`` rather than crashing the parse.
    gid: int | None
    members: tuple[str, ...]


class GroupDetails:
    """``/etc/group`` parsed as a mapping of group name to group details.

    **Commands:**

    ```bash
    cat /etc/group
    ```

    **Returns:**

    ```python
    dict[str, GroupEntry]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/server_group_details.py``.
    """

    # An original reemote fact giving Ansible's ``group`` module its
    # reconciliation half (issue #62; the baseline's own ``server.Groups``
    # fact, which reemote mirrors as ``Groups``, reports only names). Each
    # colon-bearing line of ``/etc/group`` becomes a ``GroupEntry`` keyed by
    # its group name, for example::
    #
    #     {
    #         "developers": {"gid": 10000, "members": ("alice", "bob")},
    #         "nogroup": {"gid": 65534, "members": ()},
    #     }
    #
    # Lines without a colon (blank lines, stray output) are skipped, the
    # file's order is preserved -- no dedup, no sorting, like ``Groups`` --
    # and a repeated name keeps the later line, like ``Fstab``. The second
    # (password) column is never read; it is an ``x`` placeholder on modern
    # hosts and carries no information here. The third column is the group's
    # numeric id, parsed as an int when it spells one and as ``None`` when it
    # does not, rather than failing the whole parse. The fourth column is the
    # comma-separated member list, kept as a tuple so the record stays
    # hashable, and an empty column reads as ``()`` rather than a one-member
    # list holding ``""``. The file is world-readable, so the plain
    # unprivileged ``cat`` of the ``Groups`` shape suffices; empty output (the
    # command failed or the file is empty) reads as ``{}``.

    #: Shell reading the group table: the same ``cat /etc/group`` the
    #: ``Groups`` fact runs, parsed further here.
    COMMAND = "cat /etc/group"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, GroupEntry]:
        """Parse ``/etc/group`` rows into the name -> ``GroupEntry`` mapping.

        Lines without a colon are dropped, the second (password) column is
        skipped, a non-numeric third column reads as ``None``, and the
        comma-separated fourth column becomes a tuple with any empty member
        dropped. Pure and testable.
        """
        entries: dict[str, GroupEntry] = {}
        for line in output:
            if ":" not in line:
                continue
            fields = line.split(":")
            raw_gid = fields[2] if len(fields) > 2 else ""
            raw_members = fields[3] if len(fields) > 3 else ""
            entry: GroupEntry = {
                "gid": int(raw_gid) if raw_gid.isdigit() else None,
                "members": tuple(member for member in raw_members.split(",") if member),
            }
            entries[fields[0]] = entry
        return entries

    @classmethod
    async def process(cls, host: Host) -> dict[str, GroupEntry]:
        groups = getattr(host, "os_groups", None)
        if groups is not None:
            # SimulatedHost seam: the two stores keep one truth. ``os_groups``
            # holds the names (file order) that the ``Groups`` fact reports;
            # ``os_group_details`` holds the gid/members a test or a simulated
            # ``groupadd``/``groupmod``/``usermod`` recorded for each name. A
            # name the details store does not carry reads as an unknown gid
            # (``None``) with no members, exactly like a test that seeded only
            # ``os_groups`` before the gid dimension existed. Return copies so
            # callers never mutate the simulated state.
            details = getattr(host, "os_group_details", None) or {}
            return {
                name: dict(details.get(name) or {"gid": None, "members": ()})
                for name in groups
            }
        # Real transport: cat /etc/group over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class Groups:
    """The list of group names on the system, from ``/etc/group``.

    **Commands:**

    ```bash
    cat /etc/group
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
        Examples can be found in ``examples/modules/server/server_groups.py``.
    """

    # Mirrors the baseline's ``server.Groups``: ``cat /etc/group`` is parsed by
    # taking the first ``:``-separated field of every line that contains a
    # colon; lines without one (blank lines, stray output) are skipped. The
    # file's order is preserved -- no dedup, no sorting -- for example::
    #
    #     ["lpadmin", "sambashare", "vboxsf"]
    #
    # Empty output (the command failed or the file is empty) reads as ``[]``.

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


#: Desktop-session binaries the baseline's ``LinuxGui.known_gui_binaries``
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

    **Commands:**

    ```bash
    ls /usr/bin/*session || true
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
        Examples can be found in ``examples/modules/server/server_has_gui.py``.
    """

    # Mirrors the baseline's ``server.HasGui``: ``ls /usr/bin/*session || true``
    # lists the desktop-session binaries under ``/usr/bin``, and the host has
    # a GUI when any listed path is a known session binary (GNOME, MATE,
    # LXDE, KDE Plasma, XFCE 4). Empty output -- no ``*session`` match, the
    # ``ls`` error swallowed by ``|| true`` -- reads as ``False``. The baseline
    # implements this as a ``ShortFactBase`` over its ``LinuxGui`` fact; here
    # the two are separate facts sharing the same shell and known-binary
    # map, so ``HasGui`` is the boolean projection of ``LinuxGui``'s list of
    # GUI names.

    #: Shell used to look for desktop-session binaries over SSH.
    COMMAND = "ls /usr/bin/*session || true"

    #: Session binaries the baseline's ``LinuxGui.known_gui_binaries`` recognises;
    #: any one of them present means the host has a GUI. The keys of the map
    #: ``LinuxGui`` parses names from.
    GUI_SESSION_BINARIES = frozenset(_KNOWN_GUI_BINARIES)

    @classmethod
    def parse(cls, output: Iterable[str]) -> bool:
        """Parse ``ls`` rows into whether a known session binary is listed.

        Lines are matched exactly (the baseline does not strip them); anything
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
    """A user's home directory (e.g. ``/home/reemote``), or None.

    **Commands:**

    ```bash
    echo ~<user>
    ```

    **Returns:**

    ```python
    str | None
    ```

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/server_home.py``.
    """

    # Mirrors the baseline's ``server.Home``: ``echo ~user`` lets the shell resolve
    # the home directory; an empty ``user`` means the login user's own ``~``.
    # Because ``~user`` must stay an unquoted bare word, an unsafe username
    # raises ``ValueError`` instead of reaching the shell. The first non-blank
    # line is the answer; anything after it is ignored. Empty output (the
    # command failed) reads as ``None``, and so does an unexpanded ``~user``
    # echo -- the shell's answer for an unknown user, which the baseline returns as
    # the literal string; reemote maps it to ``None`` so a missing user never
    # masquerades as a path.

    @classmethod
    def command(cls, user: str = "") -> str:
        """Shell echoes ``~user`` so tilde expansion resolves the home.

        The username is validated rather than quoted: quoting ``~user``
        would disable the expansion (the baseline's ``_check_tilde_username``).
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

    **Commands:**

    ```bash
    uname -n
    ```

    **Returns:**

    ```python
    str
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/server_hostname.py``.
    """

    # Mirrors the baseline's ``server.Hostname``: the first line of ``uname -n``
    # output is the hostname string. The first non-blank line is the answer;
    # anything after it is ignored (``uname -n`` prints exactly one line).
    # Empty output (the command failed) maps to ``""``, like ``Arch``.

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

    **Commands:**

    ```bash
    uname -s
    ```

    **Returns:**

    ```python
    str
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/server_kernel.py``.
    """

    # Mirrors the baseline's ``server.Kernel``: the first line of ``uname -s``
    # output is the kernel name string (``Linux``, ``Darwin``, ``FreeBSD``,
    # ...). The first non-blank line is the answer; anything after it is
    # ignored (``uname -s`` prints exactly one line). Empty output (the
    # command failed) maps to ``""``, like ``Arch``. The baseline's deprecated
    # ``server.Os`` alias is deliberately dropped -- only the new name lives
    # here.

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

    **Commands:**

    ```bash
    ! test -f /proc/modules || cat /proc/modules
    ```

    **Returns:**

    ```python
    dict[str, dict[str, Any]]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/server_kernel_modules.py``.
    """

    # Mirrors the baseline's ``server.KernelModules`` on Linux: ``/proc/modules``
    # is cat'd (the ``! test -f`` guard short-circuits to no output when the
    # file is absent, so a failed read maps to ``{}``, the baseline's
    # ``default = dict``).
    #
    # Each ``/proc/modules`` row becomes, for example::
    #
    #     {
    #         "libahci": {
    #             "size": "32768",
    #             "instances": 1,
    #             "state": "Live",
    #             "depends": ["ahci"],
    #         },
    #     }
    #
    # ``size`` stays the raw string and only ``instances`` is converted to an
    # int (the baseline's docstring example shows ints for both, but its parser
    # converts only ``instances``); ``depends`` is present only when the row
    # lists dependencies (``-`` means none).

    #: Shell listing the loaded modules: the guard yields no output when
    #: /proc/modules is absent.
    COMMAND = "! test -f /proc/modules || cat /proc/modules"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Parse ``/proc/modules`` rows into the name -> info dict. Pure.

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
        # Real transport: `cat /proc/modules` over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class KernelVersion:
    """The kernel version according to ``uname`` (e.g. ``6.1.0-26-amd64``).

    **Commands:**

    ```bash
    uname -r
    ```

    **Returns:**

    ```python
    str
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/server_kernel_version.py``.
    """

    # Mirrors the baseline's ``server.KernelVersion`` -- the renamed
    # ``server.OsVersion`` fact, whose deprecated alias is deliberately
    # dropped: the first line of ``uname -r`` output is the kernel release
    # string. The first non-blank line is the answer; anything after it is
    # ignored (``uname -r`` prints exactly one line). Empty output (the
    # command failed) maps to ``""``, like ``Arch``.

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

    **Commands:**

    ```bash
    last -Fwi 2>/dev/null || last -w 2>/dev/null || last 2>/dev/null || true
    ```

    **Returns:**

    ```python
    list[dict[str, str]]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/server_last.py``.
    """

    # Mirrors the baseline's ``server.Last``: each record holds ``user``, ``tty``,
    # ``host`` and ``time``, for example::
    #
    #     [
    #         {
    #             "user": "alice",
    #             "tty": "pts/0",
    #             "host": "192.168.1.5",
    #             "time": "Thu Apr 17 14:00 still logged in",
    #         },
    #         {
    #             "user": "reboot",
    #             "tty": "system boot",
    #             "host": "6.19.10-arch1-1",
    #             "time": "Thu Apr 17 11:00 - 12:00 (01:00)",
    #         },
    #     ]
    #
    # Parsing is intentionally light: ``time`` holds the raw trailing string
    # from the ``last`` output -- runs of whitespace collapsed to single
    # spaces by the token join (the baseline's docstring example shows the column
    # padding, but its parser joins with single spaces too) -- so callers can
    # re-parse the date format that matches their system. Blank lines,
    # ``wtmp begins``/``btmp begins`` footers, ``user``/``username`` header
    # rows and rows with fewer than three fields are skipped; a ``reboot``
    # row's ``system boot`` counts as one tty, and a row whose remainder
    # starts with a weekday has no host column (``""``). Empty output --
    # ``last`` missing or failed, swallowed by the shell's ``|| true`` --
    # reads as ``[]`` (the baseline's ``default = list``). The baseline's
    # ``LastRecordDict`` TypedDict is represented as a plain dict, like
    # ``KernelModules``' info dicts.

    #: Shell used to gather the login records over SSH: ``-Fwi``
    #: (util-linux) prints full timestamps, wide columns and IPs instead of
    #: hostnames; ``-w`` alone works on FreeBSD and busybox ``last`` rejects
    #: all flags, so the bare command is the final fallback.
    COMMAND = "last -Fwi 2>/dev/null || last -w 2>/dev/null || last 2>/dev/null || true"

    #: Weekday abbreviations ``last`` prints (the baseline's ``_WEEKDAYS``); a
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
    """``int()`` that returns its input unchanged on failure (the baseline's ``try_int``).

    ``distro``'s ``major_version``/``minor_version`` answer ``""`` when no
    source provides the part, so a missing version comes back as a string
    rather than raising.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


class LinuxDistribution:
    """The Linux distribution's name, family and version, as a dict.

    **Commands:**

    ```bash
    cd /etc/ && for file in $(ls -pdL *-release | grep -v /); do echo "/etc/${file}"; cat "/etc/${file}"; echo ---; done
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
        Examples can be found in ``examples/modules/server/server_linux_distribution.py``.
    """

    # Mirrors the baseline's ``server.LinuxDistribution``: the shell cats every
    # ``/etc/*-release`` file, each preceded by its path and followed by a
    # ``---`` separator, and the parts are replayed into a scratch ``etc``
    # directory that the ``distro`` library parses -- the same library the baseline
    # uses. ``major`` and ``minor`` are resolved from the most precise source
    # available: when ``/etc/os-release`` exposes only a major version (e.g.
    # CentOS 8's ``VERSION_ID="8"``), the minor is read from the
    # distro-specific release file instead; ``minor`` is None when no source
    # provides it (e.g. CentOS Stream). For example::
    #
    #     {
    #         "name": "Ubuntu",
    #         "family": "Debian",
    #         "major": 20,
    #         "minor": 4,
    #         "release_meta": {
    #             "NAME": "Ubuntu",
    #             "VERSION": "20.04 LTS (Focal Fossa)",
    #             "VERSION_ID": "20.04",
    #             "CODENAME": "Focal Fossa",
    #         },
    #     }
    #
    # ``name`` is the pretty form of the distro id (``centos`` -> ``CentOS``,
    # ``rhel`` -> ``RedHat``); an id outside the known map keeps the name
    # ``distro`` reports. ``family`` is the Ansible-style OS family the distro
    # groups under (``centos``/``rhel``/``fedora``/``rocky``/``almalinux`` ->
    # ``RedHat``, ``ubuntu``/``debian`` -> ``Debian``, ``arch`` ->
    # ``Archlinux``, ``alpine`` -> ``Alpine``, ``opensuse-leap`` -> ``Suse``),
    # so an ``include_vars: "{{ os_family }}.yml"`` dispatch becomes a
    # single-key family lookup instead of an enumeration of every rebuild's
    # name. It is resolved from ``ID_TO_FAMILY`` on the id, then on the
    # ``ID_LIKE`` tokens, then on the pretty name, and falls back to the name
    # itself for an unrecognised distro -- Ansible's own ``os_family``
    # fallback. ``release_meta`` holds the upper-cased
    # ``/etc/os-release`` key/values (distro 1.7+'s ``RELEASE_CODENAME`` is
    # dropped, like the baseline). Empty release files (Arch's empty
    # ``/etc/arch-release``) are skipped, and no release files at all read as
    # ``{"name": None, "family": None, "major": None, "minor": None,
    # "release_meta": {}}`` (the baseline's ``default``, plus reemote's
    # ``family`` extension). The baseline's ``LinuxDistributionDict`` TypedDict is
    # represented as a plain dict, like ``Last``'s record dicts.

    #: Shell used to dump every ``/etc/*-release`` file over SSH: each file's
    #: path, its content and a ``---`` separator (``ls -pdL`` follows
    #: symlinks and drops directories).
    COMMAND = (
        'cd /etc/ && for file in $(ls -pdL *-release | grep -v /); '
        'do echo "/etc/${file}"; cat "/etc/${file}"; echo ---; '
        "done"
    )

    #: Pretty names for the distro ids the baseline's ``name_to_pretty_name``
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

    #: The ``distro`` id -> Ansible-style OS family, mirroring ansible-core's
    #: ``OS_FAMILY_MAP`` but keyed on the lower-case id ``distro.id()`` reports
    #: rather than the pretty distribution name: reemote's ``name`` is its own
    #: pretty-form map and does not enumerate every rebuild (``rocky`` and
    #: ``almalinux`` fall through to ``distro``'s name), so the id is the
    #: stable single key a family dispatch needs. An id outside the map is
    #: resolved through its ``ID_LIKE`` tokens, then through the pretty name,
    #: and finally falls back to the name itself (see ``_family``).
    ID_TO_FAMILY: ClassVar[dict[str, str]] = {
        # RedHat -- RHEL, its rebuilds, Fedora and the RPM-based derivatives.
        "rhel": "RedHat",
        "centos": "RedHat",
        "fedora": "RedHat",
        "rocky": "RedHat",
        "almalinux": "RedHat",
        "ol": "RedHat",
        "amzn": "RedHat",
        "scientific": "RedHat",
        "cloudlinux": "RedHat",
        "virtuozzo": "RedHat",
        "xenserver": "RedHat",
        "alinux": "RedHat",
        "euleros": "RedHat",
        "openeuler": "RedHat",
        "kylin": "RedHat",
        "redos": "RedHat",
        # Debian -- Debian, Ubuntu and the dpkg-based derivatives.
        "debian": "Debian",
        "ubuntu": "Debian",
        "raspbian": "Debian",
        "linuxmint": "Debian",
        "lmde": "Debian",
        "devuan": "Debian",
        "kali": "Debian",
        "deepin": "Debian",
        "elementary": "Debian",
        "neon": "Debian",
        "pop": "Debian",
        "parrot": "Debian",
        "steamos": "Debian",
        "uos": "Debian",
        # Suse -- SLES, openSUSE and their variants.
        "suse": "Suse",
        "sles": "Suse",
        "sled": "Suse",
        "sles_sap": "Suse",
        "opensuse": "Suse",
        "opensuse-leap": "Suse",
        "opensuse-tumbleweed": "Suse",
        "opensuse-microos": "Suse",
        "sl-micro": "Suse",
        # Archlinux -- Arch and its derivatives.
        "arch": "Archlinux",
        "antergos": "Archlinux",
        "manjaro": "Archlinux",
        "artix": "Archlinux",
        "endeavouros": "Archlinux",
        # Gentoo, Alpine, Slackware, Altlinux and Mandrake -- the remaining
        # single-family distributions ``distro`` reports on Linux.
        "gentoo": "Gentoo",
        "funtoo": "Gentoo",
        "alpine": "Alpine",
        "slackware": "Slackware",
        "altlinux": "Altlinux",
        "mandrake": "Mandrake",
        "mandriva": "Mandrake",
    }

    @classmethod
    def _family(cls, distro_id: str, like: str, name: str | None) -> str | None:
        """Resolve the Ansible-style OS family for a parsed distribution.

        ``ID_TO_FAMILY`` is consulted on the ``distro`` id first, then on each
        ``ID_LIKE`` token -- so an unenumerated rebuild declaring
        ``ID_LIKE="rhel fedora"`` still groups under ``RedHat`` -- then on the
        pretty name lower-cased, which equals the id for the single-word
        distributions and rescues a minimal ``os-release`` that omits ``ID=``
        (the baseline's ubuntu fixture, ``NAME="Ubuntu"`` -> ``Debian``). A
        distro with no recognised family falls back to ``name`` itself,
        mirroring Ansible's ``os_family`` fallback for an unlisted
        distribution; ``None`` (no release files at all) stays ``None``. Pure
        and testable.
        """
        family = cls.ID_TO_FAMILY.get(distro_id)
        if family is not None:
            return family
        for token in like.split():
            family = cls.ID_TO_FAMILY.get(token)
            if family is not None:
                return family
        if name is not None:
            family = cls.ID_TO_FAMILY.get(name.lower())
            if family is not None:
                return family
        return name

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
            "family": None,
            "major": None,
            "minor": None,
            "release_meta": {},
        }
        if not parts:
            return release_info

        temp_root = mkdtemp()
        try:
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
            # distro 1.7+ adds this; the baseline drops it to keep the meta stable.
            release_meta.pop("RELEASE_CODENAME", None)

            # _try_int returns its input on failure, so a missing version
            # part comes back as "" and maps to None below.
            major = _try_int(parsed.major_version(best=True))
            minor = _try_int(parsed.minor_version(best=True))

            distro_id = parsed.id()
            name = cls.NAME_TO_PRETTY_NAME.get(distro_id, parsed.name())
            release_info.update(
                {
                    "name": name,
                    "family": cls._family(distro_id, parsed.like(), name),
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

    **Commands:**

    ```bash
    ls /usr/bin/*session || true
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
        Examples can be found in ``examples/modules/server/server_linux_gui.py``.
    """

    # Mirrors the baseline's ``server.LinuxGui``: ``ls /usr/bin/*session || true``
    # lists the desktop-session binaries under ``/usr/bin``, and each path
    # recognised in ``KNOWN_GUI_BINARIES`` contributes its GUI name (GNOME,
    # MATE, LXDE, KDE Plasma, XFCE 4). Lines are matched exactly (the baseline
    # does not strip them); anything else -- other ``*session`` files, stray
    # output -- is ignored, and the listing order is preserved (no dedup, no
    # sorting). Empty output -- no ``*session`` match, the ``ls`` error
    # swallowed by ``|| true`` -- reads as ``[]`` (the baseline's
    # ``default = list``). ``HasGui`` is the boolean projection of this
    # list, sharing the same shell and known-binary map.

    #: Shell used to look for desktop-session binaries over SSH.
    COMMAND = "ls /usr/bin/*session || true"

    #: Session-binary path -> GUI name (the baseline's ``known_gui_binaries``);
    #: ``HasGui.GUI_SESSION_BINARIES`` is its key set.
    KNOWN_GUI_BINARIES: ClassVar[dict[str, str]] = _KNOWN_GUI_BINARIES

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[str]:
        """Parse ``ls`` rows into the list of recognised GUI names.

        Lines are matched exactly against ``KNOWN_GUI_BINARIES`` (the baseline
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

    **Commands:**

    ```bash
    cd /etc/ && for file in $(ls -pdL *-release | grep -v /); do echo "/etc/${file}"; cat "/etc/${file}"; echo ---; done
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
        Examples can be found in ``examples/modules/server/server_linux_name.py``.
    """

    # Mirrors the baseline's ``server.LinuxName``: a shortcut for the ``name`` key
    # of the ``LinuxDistribution`` fact -- the baseline implements it as a
    # ``ShortFactBase`` over that fact, and reemote likewise delegates to
    # ``LinuxDistribution.process`` rather than duplicating the shell and the
    # ``distro`` parsing (the command shown is ``LinuxDistribution``'s). The
    # delegation also means the two facts share the
    # ``linux_distribution`` SimulatedHost seam: there is deliberately no
    # separate state field, so a simulated name can never disagree with the
    # simulated distribution info. No release files at all read as ``None``
    # (the ``name`` of the baseline's ``default()``).

    @classmethod
    async def process(cls, host: Host) -> str | None:
        # Delegation picks the transport: LinuxDistribution.process reads the
        # linux_distribution SimulatedHost seam or dumps the /etc/*-release
        # files over asyncssh; this fact only projects the name out.
        info = await LinuxDistribution.process(host)
        return info["name"]


class Locales:
    """The installed locales on the system, from ``locale -a``.

    **Commands:**

    ```bash
    locale -a
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
        Examples can be found in ``examples/modules/server/server_locales.py``.
    """

    # Mirrors the baseline's ``server.Locales``: every output line is a locale
    # name, with lowercase ``utf8`` replaced by ``UTF-8`` to match the names
    # in ``/etc/locale.gen``. Lines are kept verbatim otherwise -- the
    # command's order is preserved, with no dedup or sorting -- for example::
    #
    #     ["C.UTF-8", "en_US.UTF-8"]
    #
    # Empty output (the command failed) reads as ``[]``, the baseline's
    # ``default = list``; a *missing* ``locale`` exits 127, which
    # ``run_shell`` reports as ``CommandNotFoundError`` instead.

    #: Shell used to gather the installed locales over SSH.
    COMMAND = "locale -a"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[str]:
        """Parse ``locale -a`` rows into the list of locale names.

        Only the ``utf8`` -> ``UTF-8`` normalization is applied (the baseline
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

    **Commands:**

    ```bash
    lsb_release -ca
    ```

    **Returns:**

    ```python
    dict[str, str]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/server_lsb_release.py``.
    """

    # Mirrors the baseline's ``server.LsbRelease``: ``lsb_release -ca`` prints
    # ``Key: value`` rows, and each colon-bearing line becomes a dict entry
    # with a lower-cased key whose last word is kept (``Distributor ID``
    # turns into ``id``) and a stripped value, for example::
    #
    #     {
    #         "id": "Ubuntu",
    #         "description": "Ubuntu 18.04.3 LTS",
    #         "release": "18.04",
    #         "codename": "bionic",
    #     }
    #
    # Lines without a colon -- the ``No LSB modules are available.`` hint,
    # blank lines, stray output -- are skipped, and only the first colon
    # splits (a value may contain colons). Empty output (a failed read) parses
    # to ``{}``; the baseline's ``requires_command`` gating is deliberately dropped.
    # A *missing* ``lsb_release`` exits 127, which ``run_shell`` reports as
    # ``CommandNotFoundError`` rather than an empty dict.

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
            # Turn "distributor id" into "id" (the baseline's last-word rule).
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

    **Commands:**

    ```bash
    cat /proc/self/mountinfo
    ```

    **Returns:**

    ```python
    dict[str, dict[str, Any]]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/server_mounts.py``.
    """

    # Mirrors the baseline's ``server.Mounts`` on Linux: ``/proc/self/mountinfo``
    # is cat'd. Each mount point maps to a ``{device, type, options}`` dict,
    # for example::
    #
    #     {
    #         "/": {
    #             "device": "/dev/mv2",
    #             "type": "ext4",
    #             "options": ["rw", "relatime"],
    #         },
    #     }
    #
    # The octal escapes in mount points, devices and super-block options
    # (``\\040`` space, ``\\054`` comma, ``\\134`` backslash) are decoded, the
    # optional ``shared``/``master``/``propagate_from``/``unbindable`` tags are
    # skipped, and ``options`` combines the per-mount options with the
    # super-block ones -- ``ro``/``rw`` are filtered from the super-block side
    # because the per-mount options override them (the baseline's parser).
    # Empty output reads as ``{}`` (the baseline's ``default = dict``; a failed
    # read simply reports no mounts).

    #: Shell listing the mounts.
    COMMAND = "cat /proc/self/mountinfo"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Parse ``/proc/self/mountinfo`` rows into the mount point -> info dict.

        Blank lines are skipped. The per-mount options override the
        super-block ``ro``/``rw``, which are filtered so they never appear
        twice; mount point, device and super-block options are octal-
        unescaped. A repeated mount point keeps the last row, like the
        baseline. Pure and testable.
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
            # the simulated state. A partial info dict (the baseline's operation
            # fixtures carry bare {} entries) reads as no options.
            return {
                point: {**info, "options": list(info.get("options", []))}
                for point, info in mounts.items()
            }
        # Real transport: `cat /proc/self/mountinfo` over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class PortInfo(TypedDict):
    """
    Attributes:
        listening: Whether the port has at least one listening socket; the
            answer ``server.wait`` polls.
        process: Name of the process owning one of those sockets, or ``None``
            when the tool reports no owner -- an unprivileged read, or a
            socket with no process information.
        pid: PID behind ``process``, or ``None`` under the same conditions.
    """

    # The single record ``Port`` returns. ``listening`` is decided by any
    # socket row that survives the port filter, so it is tool- and
    # privilege-independent; the owner pair is best-effort. The baseline has no
    # equivalent record: its ``(process, pid)`` tuple cannot tell "absent"
    # from "present but unowned", which is why it reports no port for the
    # two LISTEN rows of its own ``ss-tcp-no-process-info`` fixture.

    listening: bool
    process: str | None
    pid: int | None


#: Protocol -> the one-letter ``ss``/``netstat`` flag (the baseline's
#: ``proto_flag``).
_PROTOCOL_FLAGS = {"tcp": "t", "udp": "u"}


def _check_port(port: int, protocol: str) -> None:
    # Both values are interpolated into a grep pattern or an awk program, so
    # they are validated up front (the baseline validates neither) and a bad
    # argument is rejected identically on both transports.
    if not isinstance(port, int):
        raise TypeError(f"port must be an int, got {port!r}")
    if not 1 <= port <= 65535:
        raise ValueError(f"port must be between 1 and 65535, got {port!r}")
    if protocol not in _PROTOCOL_FLAGS:
        raise ValueError(
            f"protocol must be one of {sorted(_PROTOCOL_FLAGS)}, got {protocol!r}"
        )


class Port:
    """Whether a port has a listening socket, and what owns it.

    **Commands:**

    ```bash
    ss -lptn | grep ':<port> ' || true
    ```

    **Returns:**

    ```python
    PortInfo
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, TypeError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/server_port.py``.
    """

    # Mirrors the baseline's ``server.Port`` on Linux: the tool prefers ``ss``
    # and falls back to ``netstat`` when the ``Which`` fact reports no ``ss``;
    # one port's socket rows are the answer. Two deliberate deviations:
    #
    # 1. The value is a ``PortInfo`` record, not the baseline's ``(process, pid)``
    #    tuple. Presence is decided by any surviving row, because the tools
    #    print rows without owner information to an unprivileged caller and
    #    ``server.wait`` (like every wait) runs unprivileged -- so the baseline's
    #    shape could not answer the one question the wait asks. The owner
    #    pair keeps the baseline's best-effort meaning.
    # 2. ``_check_port`` rejects a non-int or out-of-range port and an unknown
    #    protocol, and a malformed PID reads as no PID rather than raising.

    #: Shell listing one port's sockets with ``ss`` (the preferred branch):
    #: ``{flag}`` is the protocol letter, ``{pattern}`` the ``:<port> `` match,
    #: and the ``|| true`` swallows grep's no-match exit.
    COMMAND_SS = "ss -lp{flag}n | grep {pattern} || true"

    #: Shell listing one port's sockets with ``netstat`` (the fallback when
    #: ``Which`` finds no ``ss``): ``{flag}`` is the protocol letter and
    #: ``{program}`` the awk filter on the local-address column.
    COMMAND_NETSTAT = "netstat -{flag}lnp 2>/dev/null | awk {program}"

    @classmethod
    def _tool(cls, has_ss: bool) -> str:
        """Tool name for whether ``ss`` is installed.

        Prefers ``ss`` and falls back to ``netstat``, exactly like the baseline.
        """
        return "ss" if has_ss else "netstat"

    @classmethod
    def command(cls, tool: str, port: int, protocol: str = "tcp") -> str:
        """Shell listing the port's sockets, as one tool prints them.

        The port and protocol are quoted (the baseline's ``QuoteString``) after
        ``_check_port`` has rejected anything out of range, so a name
        carrying shell metacharacters can never reach the command line.
        """
        _check_port(port, protocol)
        flag = _PROTOCOL_FLAGS[protocol]
        if tool == "netstat":
            return cls.COMMAND_NETSTAT.format(
                flag=flag, program=shlex.quote(f"$4 ~ /:{port}$/")
            )
        return cls.COMMAND_SS.format(flag=flag, pattern=shlex.quote(f":{port} "))

    @classmethod
    def parse(cls, output: Iterable[str], tool: str) -> PortInfo:
        """Parse the tool's rows into the ``PortInfo`` record. Pure.

        Dispatches on the tool name exactly like ``command``. Blank lines are
        never rows, and the ``ss``/``netstat`` shells have already filtered by
        port, so empty output reads as "nothing listening" (the baseline's
        ``(None, None)`` default). The owner pair comes from the first row
        that carries one.
        """
        rows = [line.strip() for line in output if line.strip()]
        if tool == "netstat":
            process, pid = cls._owner_netstat(rows)
        else:
            process, pid = cls._owner_ss(rows)
        return PortInfo(listening=bool(rows), process=process, pid=pid)

    @classmethod
    def _owner_ss(cls, rows: list[str]) -> tuple[str | None, int | None]:
        """Owner from the first ``users:(("name",pid=N,fd=F))`` row.

        A row without process information -- what an unprivileged ``ss -p``
        prints -- is skipped and no owner is reported (the baseline's
        ``_process_ss``); the caller still counts the row as listening.
        """
        for line in rows:
            if '"' not in line or "pid=" not in line:
                continue
            process = line.split('"')[1]
            try:
                return process, int(line.split("pid=")[1].split(",")[0])
            except ValueError:
                return process, None
        return None, None

    @classmethod
    def _owner_netstat(cls, rows: list[str]) -> tuple[str | None, int | None]:
        """Owner from the first ``PID/Program name`` cell (``1234/nginx``).

        An unprivileged netstat prints ``-`` or ``?`` there, and a cell whose
        PID is not a number is no owner either (the baseline's
        ``_process_netstat``).
        """
        for line in rows:
            for part in line.split():
                if "/" in part:
                    pid_str, process = part.split("/", 1)
                    if pid_str.isdigit():
                        return process, int(pid_str)
                    break
        return None, None

    @classmethod
    async def process(
        cls, host: Host, port: int, protocol: str = "tcp"
    ) -> PortInfo:
        # Validate even on the simulated path so a bad argument is rejected
        # identically on both transports (like ``Processes.process``).
        _check_port(port, protocol)
        ports = getattr(host, "listening_ports", None)
        if ports is not None:
            # SimulatedHost seam: (protocol, port) -> PortInfo. Copy the record
            # so callers never mutate the simulated state; an unlisted port
            # reads as "nothing listening", like empty command output.
            info = ports.get((protocol, port))
            if info is not None:
                return dict(info)  # type: ignore[return-value]
            return PortInfo(listening=False, process=None, pid=None)
        # Real transport: the Which fact picks the tool (the baseline reads it
        # inside command()); the output is then parsed the same way.
        has_ss = bool(await Which.process(host, "ss"))
        tool = cls._tool(has_ss)
        stdout = await host.run_shell(cls.command(tool, port, protocol))
        return cls.parse(stdout.splitlines(), tool)


def _check_url(url: str) -> None:
    # The URL is interpolated into a curl invocation, so it is validated up
    # front and ``shlex``-quoted in ``HttpEndpoint.command``, and the scheme is
    # pinned to ``http``/``https``: a readiness poll should never reach a
    # ``file://`` or another local scheme through the host's curl. Rejected
    # identically on both transports.
    if not isinstance(url, str):
        raise TypeError(f"url must be a str, got {url!r}")
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"url must be an http or https endpoint, got {url!r}")


class HttpEndpointInfo(TypedDict):
    """
    Attributes:
        status: The HTTP status code the endpoint returned, or ``None`` when it
            could not be reached (curl exits non-zero and prints the ``000``
            write-out for "no response") -- the answer
            ``server.wait_url`` polls through ``EndpointReady``.
        body: The response body as text, empty when the endpoint sent none or
            was unreachable. ``EndpointReady`` matches ``contains``/``absent``
            substrings against it.
    """

    # The single record ``HttpEndpoint`` returns. ``status`` separates "answered
    # with a code" from "reached nothing" (the ``000`` curl prints for a
    # refused connection or a failed transfer is normalised to ``None``), so a
    # wait can tell a not-yet-up server from one answering 503; ``body`` is the
    # verbatim text, which the readiness slice checks for markers such as
    # Jenkins' "Please wait while" upgrade page.

    status: int | None
    body: str


class HttpEndpoint:
    """The HTTP status code and body of a URL, read on the host with ``curl``.

    **Commands:**

    ```bash
    curl -s -w '\\n%{http_code}' <url>
    ```

    **Returns:**

    ```python
    HttpEndpointInfo
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, TypeError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/server_wait_url.py``.
    """

    # The host-local half of the Ansible ``uri`` + ``until`` readiness slice
    # (#43): exactly one GET, whose status code and body are read back so a
    # module-level predicate can decide. Deliberately *not* a control-side
    # ``urllib`` read like ``files.fetch_url`` (#20): running curl on the target
    # keeps a ``localhost`` URL meaning the host's own loopback -- what a role
    # like ``geerlingguy.jenkins`` checks against its freshly started daemon --
    # where the controller could not reach it. Unlike ``fetch_url`` a non-2xx
    # response is no error here: the status code is the answer, so the accepted
    # states (``200`` *or* ``403`` for Jenkins' ``/cli/``) are observable rather
    # than raised. Only a missing curl (exit 127) raises,
    # ``CommandNotFoundError`` -- like ``Port`` needing ``ss``/``netstat``.
    #
    # The status rides the write-out: curl appends ``\n%{http_code}`` after the
    # body, so the last newline is the separator and the body keeps any of its
    # own. ``-s`` silences the progress meter (it would otherwise corrupt the
    # body) but not fatal errors, which go to stderr and leave stdout as the
    # ``\n000`` write-out of a failed transfer.

    #: Shell reading one URL's body and status code on the host; the URL is
    #: appended ``shlex``-quoted by ``command``. Kept as a concatenation prefix
    #: rather than a ``{url}`` template because ``str.format`` would choke on
    #: curl's own ``%{http_code}`` braces.
    COMMAND = "curl -s -w '\\n%{http_code}' "

    @classmethod
    def command(cls, url: str) -> str:
        """Shell reading the endpoint's body and status code.

        The URL is validated by ``_check_url`` and quoted, so a URL carrying
        shell metacharacters can never reach the command line.
        """
        _check_url(url)
        return cls.COMMAND + shlex.quote(url)

    @classmethod
    def parse(cls, output: str) -> HttpEndpointInfo:
        """Split curl's body-plus-write-out into the record. Pure.

        The status is whatever follows the last newline; a non-numeric trailing
        line (no write-out) or the ``000`` curl prints for "no response" reads
        as ``None``. Everything before the last newline is the body, verbatim.
        """
        body, _, code = output.rpartition("\n")
        code = code.strip()
        status = int(code) if code.isdigit() else None
        if status == 0:
            status = None
        return HttpEndpointInfo(status=status, body=body)

    @classmethod
    async def process(cls, host: Host, url: str) -> HttpEndpointInfo:
        # Validate even on the simulated path so a bad URL is rejected
        # identically on both transports (like ``Port.process``).
        _check_url(url)
        endpoints = getattr(host, "http_endpoints", None)
        if endpoints is not None:
            # SimulatedHost seam: url -> HttpEndpointInfo. Copy the record so
            # callers never mutate the simulated state; an unlisted url reads as
            # "no answer", like a curl that reached nothing.
            info = endpoints.get(url)
            if info is not None:
                return dict(info)  # type: ignore[return-value]
            return HttpEndpointInfo(status=None, body="")
        # Real transport: curl runs on the host, so a localhost URL means the
        # host's own loopback; the output is parsed the same way.
        stdout = await host.run_shell(cls.command(url))
        return cls.parse(stdout)


def _check_pid(pid: int | None) -> None:
    # The BusyBox branch interpolates the pid into an awk program, where
    # shell quoting cannot protect it, so a non-int pid is rejected outright.
    if pid is not None and not isinstance(pid, int):
        raise TypeError(f"pid must be an int or None, got {pid!r}")


class Processes:
    """The running processes, as a dict of PID -> info dict.

    **Commands:**

    ```bash
    LANG=C ps -eo pid,user,stat,%cpu,%mem,comm,args --no-headers
    ```

    **Returns:**

    ```python
    dict[int, dict[str, Any]]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, TypeError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/server_processes.py``.
    """

    # Mirrors the baseline's ``server.Processes`` on Linux: the shell is chosen
    # from a BusyBox probe -- a full Linux lists
    # ``ps -eo pid,user,stat,%cpu,%mem,comm,args --no-headers``, BusyBox
    # (Alpine) the limited ``ps -o pid,user,vsz,stat,args`` columns. Each PID
    # maps to a ``{user, state, cpu_percent, mem_percent, command, args}`` dict,
    # for example::
    #
    #     {
    #         1: {
    #             "user": "root",
    #             "state": "Ss",
    #             "cpu_percent": 0.0,
    #             "mem_percent": 0.1,
    #             "command": "init",
    #             "args": "/sbin/init",
    #         },
    #     }
    #
    # An optional ``pid`` restricts the listing to that single process (an
    # absent PID reads as ``{}``). On BusyBox ``cpu_percent``/``mem_percent``
    # are always ``0.0`` and ``command`` is the basename of ``args``' first
    # word. Header rows, blank lines, short rows and non-integer PIDs are
    # skipped; empty output reads as ``{}`` (the baseline's ``default = dict``).
    #
    # The BusyBox detection reads the ``Which`` fact (``command -v busybox
    # || true``), exactly like the baseline.

    #: Shell listing every process on a full Linux ``ps`` (the baseline's
    #: non-BusyBox branch): ``--no-headers`` drops the header row.
    COMMAND_LINUX = "LANG=C ps -eo pid,user,stat,%cpu,%mem,comm,args --no-headers"

    #: Shell listing every process on BusyBox (Alpine): only pid, user,
    #: vsz, stat and args are reliable columns.
    COMMAND_BUSYBOX = "LANG=C ps -o pid,user,vsz,stat,args"

    @classmethod
    def command(cls, busybox: bool = False, pid: int | None = None) -> str:
        """Shell for the BusyBox presence, optionally restricted to one PID.

        A ``pid`` switches to ``ps -p`` (BusyBox has no ``-p``, so ``awk``
        keeps the header and the one PID row); otherwise BusyBox takes the
        limited-column listing and every other Linux the full one, exactly
        like the baseline.
        """
        _check_pid(pid)
        if busybox:
            fields = "pid,user,vsz,stat,args"
        else:
            fields = "pid,user,stat,%cpu,%mem,comm,args"
        if pid is not None:
            if busybox:
                return f"LANG=C ps -o {fields} | awk {shlex.quote(f'NR==1 || $1=={pid}')}"
            return f"LANG=C ps -p {shlex.quote(str(pid))} -o {fields}"
        if busybox:
            return cls.COMMAND_BUSYBOX
        return cls.COMMAND_LINUX

    @classmethod
    def parse(
        cls, output: Iterable[str], busybox: bool = False
    ) -> dict[int, dict[str, Any]]:
        """Parse ``ps`` rows into the PID -> info dict for the chosen shell.

        Dispatches exactly like ``command``: BusyBox rows the five-column
        listing, everything else the seven-column Linux listing. Pure and
        testable.
        """
        if busybox:
            return cls._parse_busybox(output)
        return cls._parse_posix(output)

    @classmethod
    def _parse_posix(cls, output: Iterable[str]) -> dict[int, dict[str, Any]]:
        """Parse seven-column ``ps`` rows (the baseline's Linux branch).

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
        """Parse five-column BusyBox ``ps`` rows (the baseline's BusyBox branch).

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
        # Real transport: the Which fact picks the shell (the baseline reads it
        # inside command()), then the ps output is parsed the same way.
        busybox = bool(await Which.process(host, "busybox"))
        stdout = await host.run_shell(cls.command(busybox, pid))
        return cls.parse(stdout.splitlines(), busybox)


class PythonVersionInfo(TypedDict):
    """
    Attributes:
        major: The interpreter's major version number (``3``).
        minor: The interpreter's minor version number (``12`` for
            ``python3.12``).
        micro: The interpreter's micro/patch version number (``1`` for
            ``3.12.1``).
    """

    # One Python interpreter's version -- the entry of the ``PythonVersion``
    # fact. A TypedDict rather than a class instance: the answer is an
    # ordinary triple of ints, so equality against a literal dict works and
    # ``json.dumps`` serializes it directly -- the ``PipPackageInfo`` /
    # ``DebPackageInfo`` convention of every structured version entry here.

    major: int
    minor: int
    micro: int


class PythonVersion:
    """The version of a Python interpreter, as a ``PythonVersionInfo`` entry.

    **Commands:**

    ```bash
    <command> -c 'import sys; print("{}.{}.{}".format(*sys.version_info[:3]))'
    ```

    **Returns:**

    ```python
    PythonVersionInfo
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/server_python_version.py``.
    """

    # Closes the missing ``ansible_facts['python']['version']`` parity (the
    # pip role's EXTERNALLY-MANAGED path, the same interpreter question
    # ``pip.virtualenv``/``pip.venv`` must ask to reconcile a requested base
    # interpreter): the interpreter's own ``sys.version_info`` triple is the
    # only reliable source, since a distro's ``python3`` symlink chain
    # (``python3`` -> ``python3.12`` -> ``/usr/bin/...``) says nothing about
    # the version the running interpreter actually reports, and a stale
    # ``pyvenv.cfg`` says even less (see the ``pip`` namespace's own
    # deviation note). The ``command`` argument selects which interpreter
    # answers -- a bare ``python``/``python3`` on the PATH, a versioned name
    # (``python3.12``) or an absolute path (a virtualenv's ``bin/python``)
    # -- and defaults to ``"python3"``, the spelling the Ansible fact and
    # the pip role's path interpolation both assume.
    #
    # Like ``PipPackages`` and ``KernelVersion`` the command runs bare: a
    # missing interpreter exits 127 and surfaces as ``CommandNotFoundError``,
    # not a silently ``None`` answer -- the caller named that interpreter and
    # an environment without it is an error the deployer hears about, not a
    # state to branch on. A *tolerant*, may-be-absent probe -- an
    # already-created virtualenv's own interpreter -- is a different
    # question, answered by a different, guarded read (the ``pip``
    # namespace's private ``_VirtualenvPython`` fact), which is why this
    # fact stays the strict one.

    #: The ``sys.version_info`` triple the ``-c`` snippet prints, dotted.
    VERSION_SNIPPET = "import sys; print('{}.{}.{}'.format(*sys.version_info[:3]))"

    @classmethod
    def command(cls, command: str = "python3") -> str:
        """Shell asking ``command`` to print its ``major.minor.micro``.

        The interpreter is ``shlex``-quoted, so a path carrying a
        metacharacter cannot break out of the argument; the ``-c`` snippet
        is quoted as one whole argument.
        """
        return f"{shlex.quote(command)} -c {shlex.quote(cls.VERSION_SNIPPET)}"

    @classmethod
    def parse(cls, output: Iterable[str]) -> PythonVersionInfo:
        """Parse the printed ``major.minor.micro`` into a ``PythonVersionInfo``.

        The first non-blank line is split on ``.`` into three ints. Empty
        output, a non-dotted line, a wrong number of parts or a non-integer
        part raises ``ValueError`` -- an interpreter that got this far
        printed something, and anything unreadable is a broken interpreter
        or a broken snippet, never a state to guess at. Pure and testable.
        """
        for line in output:
            stripped = line.strip()
            if not stripped:
                continue
            parts = stripped.split(".")
            if len(parts) != 3:
                raise ValueError(f"unparsable Python version: {stripped!r}")
            try:
                major, minor, micro = (int(part) for part in parts)
            except ValueError:
                raise ValueError(
                    f"unparsable Python version: {stripped!r}"
                ) from None
            return {"major": major, "minor": minor, "micro": micro}
        raise ValueError("no Python version printed")

    @classmethod
    async def process(cls, host: Host, command: str = "python3") -> PythonVersionInfo:
        python_versions = getattr(host, "python_versions", None)
        if python_versions is not None:
            # SimulatedHost seam: the store maps an interpreter command to
            # the version it prints, keyed exactly as ``command`` names it.
            # A KeyError is deliberate -- a test that reads an interpreter it
            # has not seeded has asked a question the simulation cannot
            # answer, and must not silently get one. Copy the entry so
            # callers never mutate simulated state.
            return dict(python_versions[command])
        # Real transport: ask the interpreter itself over asyncssh. A missing
        # interpreter exits 127, which ``run_shell`` raises as
        # CommandNotFoundError before ``parse`` is ever reached.
        stdout = await host.run_shell(cls.command(command))
        return cls.parse(stdout.splitlines())


class _UnreadableKeyPair:
    """Sentinel type: a public key file the login user cannot read."""


#: The single sentinel instance ``SshKeyPair._read`` returns for a present
#: but unreadable public key; ``process`` re-reads it elevated (the
#: ``FileContents`` two-tier read pattern). A private object, so no
#: fingerprint string can ever compare equal to it.
_UNREADABLE_KEY_PAIR = _UnreadableKeyPair()


class SshKeyPair:
    """The fingerprint of a user's SSH key pair, or None when it is absent.

    Reads the public half of the pair with ``ssh-keygen -lf`` and reports
    its ``SHA256:...`` fingerprint. A public key the login user cannot read
    is re-read elevated with ``sudo``, so restrictive ``.ssh`` modes keep
    key-pair idempotence; the run-as user and any password come from the
    inventory ``privilege`` data, never from a fact or operation.

    **Commands:**

    ```bash
    test -e ~<user>/.ssh/id_rsa.pub && { printf '#reemote-sshkeypair\\n'; ssh-keygen -lf ~<user>/.ssh/id_rsa.pub 2>/dev/null || printf '#reemote-sshkeypair-unreadable\\n'; }
    ```

    **Returns:**

    ```python
    str | None
    ```

    **Raises:**

    ```python
    HostConnectionError, PrivilegeError, ValueError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/server/server_ssh_keypair.py``.
    """

    # Closes the missing idempotence half of the Ansible ``user`` module's
    # ``generate_ssh_key`` support (issue #60): existence plus fingerprint of
    # the pair's public half is exactly the state ``server.ssh_keypair``
    # compares against, and the ``ssh-keygen -lf`` fingerprint is the
    # human-readable identity a deployer diffuses or verifies. The read is
    # two-tier (the ``files.FileContents`` precedent): the first read rides
    # unprivileged, and a file that is present but unreadable -- another
    # user's ``700`` ``.ssh`` directory, the normal case under ``sudo`` --
    # prints the ``UNREADABLE`` marker line instead of the row, which
    # ``process`` turns into one elevated re-read. A missing file produces
    # no output at all and reads as ``None`` -- never the empty string --
    # so absence stays distinguishable from a present key. The guarded
    # shell also masks a missing ``ssh-keygen`` (exit 127 lands on the
    # ``UNREADABLE`` arm), so this fact never raises
    # ``CommandNotFoundError``.

    #: Marker printed before the ``ssh-keygen`` row, only when ``test -e``
    #: passes, so ``_read`` can tell an existing key file from a missing one
    #: (no marker on stdout).
    MARKER = "#reemote-sshkeypair"

    #: Marker printed instead of the row when the fingerprint read fails
    #: (unreadable as the login user, or no ``ssh-keygen``): stdout is then
    #: exactly the two marker lines, which ``process`` detects to trigger
    #: the elevated re-read.
    UNREADABLE = "#reemote-sshkeypair-unreadable"

    @classmethod
    def command(cls, user: str, path: str | None = None) -> str:
        """Shell printing ``MARKER`` then the ``ssh-keygen -lf`` row, if the key exists.

        The ``test -e`` guard means a missing file produces no output at all
        (not even the marker), while an existing one always leads with the
        marker line; a failing read prints ``UNREADABLE`` instead of the row.
        Tilde expansion resolves the user's home without another fact
        round-trip, so the username is validated rather than quoted; the
        default file is the conventional ``id_rsa.pub``. An explicit path
        (what ``server.ssh_keypair`` always passes, since it knows the key
        type) is quoted normally.
        """
        if path is None:
            _check_tilde_username(user)
            target = f"~{user}/.ssh/id_rsa.pub"
        else:
            target = shlex.quote(path)
        return (
            f"test -e {target} && {{ printf '{cls.MARKER}\\n'; "
            f"ssh-keygen -lf {target} 2>/dev/null "
            f"|| printf '{cls.UNREADABLE}\\n'; }}"
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> str | None:
        """Parse the ``ssh-keygen -lf`` row into the bare fingerprint, or None.

        The row reads ``<bits> <fingerprint> <comment> (<type>)``; the second
        token is the answer -- a modern ``SHA256:...`` digest or the legacy
        colon-separated ``MD5`` one -- and is returned verbatim. Blank output
        reads as ``None``; a non-blank row without a colon-bearing second
        token raises ``ValueError`` -- the tool answered with something this
        parser does not know, which is a format change, never a state to
        guess at. Pure and testable.
        """
        for line in output:
            stripped = line.strip()
            if not stripped:
                continue
            tokens = stripped.split()
            if len(tokens) < 2 or ":" not in tokens[1]:
                raise ValueError(f"unparsable ssh-keygen output: {stripped!r}")
            return tokens[1]
        return None

    @classmethod
    async def process(cls, host: Host, user: str, path: str | None = None) -> str | None:
        # Build the shell first, even on the simulated path, so an unsafe
        # tilde username is rejected identically on both transports.
        cls.command(user, path)
        key_pairs = getattr(host, "ssh_key_pairs", None)
        if key_pairs is not None:
            # SimulatedHost seam: the state is keyed by username for the
            # default path and by the explicit public-key path otherwise; an
            # absent key reads as None, like a missing file. str is
            # immutable, so no copy is needed.
            return key_pairs.get(user if path is None else path)
        fingerprint = await cls._read(host, user, path, privileged=False)
        if fingerprint is _UNREADABLE_KEY_PAIR:
            # Present but unreadable as the login user: re-read elevated.
            # The run-as user and any password come from the inventory
            # privilege data (run_shell_privileged), never from here.
            fingerprint = await cls._read(host, user, path, privileged=True)
            if fingerprint is _UNREADABLE_KEY_PAIR:
                # Even elevated the row failed to read -- in practice no
                # ``ssh-keygen`` on the host, which the guarded shell hides.
                # Reads as absent: an operation can only plan a generation,
                # and that command then fails loudly at apply rather than
                # the fact guessing an answer.
                return None
        return fingerprint

    @classmethod
    async def _read(
        cls, host: Host, user: str, path: str | None, *, privileged: bool
    ) -> str | None | _UnreadableKeyPair:
        command = cls.command(user, path)
        if privileged:
            stdout = await host.run_shell_privileged(command, sudo=True)
        else:
            stdout = await host.run_shell(command)
        prefix = f"{cls.MARKER}\n"
        if not stdout.startswith(prefix):
            # The test -e guard failed: no public key file, no fingerprint.
            return None
        body = stdout[len(prefix) :]
        if body.startswith(cls.UNREADABLE):
            # Present but the row did not read: unprivileged, this is the
            # signal to retry elevated; elevated, it means there is no
            # ``ssh-keygen`` at all and ``process`` reads the pair as absent.
            return _UNREADABLE_KEY_PAIR
        return cls.parse(body.splitlines())


class Timezone:
    """The current system timezone (e.g. ``Europe/Amsterdam``).

    **Commands:**

    ```bash
    readlink -f /etc/localtime | sed 's|.*/zoneinfo/||'
    ```

    **Returns:**

    ```python
    str
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/server_timezone.py``.
    """

    # Mirrors the baseline's ``server.Timezone``: ``readlink -f /etc/localtime``
    # resolves the zoneinfo symlink and ``sed`` strips everything up to
    # ``zoneinfo/``, leaving the timezone name. When ``/etc/localtime`` is a
    # copied file rather than a symlink into zoneinfo, the sed matches
    # nothing and the resolved path is returned verbatim, exactly like
    # The baseline. The first non-blank line is the answer; anything after it is
    # ignored (the pipeline prints exactly one line). Empty output (the
    # command failed) maps to ``""``, like ``Arch`` -- the baseline's
    # ``output[0]`` would raise on empty output.

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

    **Commands:**

    ```bash
    cut -d. -f1 /proc/uptime
    ```

    **Returns:**

    ```python
    int | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/server_uptime.py``.
    """

    # Mirrors the baseline's ``server.Uptime`` on Linux: the integer part is
    # cut out of ``/proc/uptime``, so the answer is whole seconds (e.g.
    # ``86400`` -- one day). Blank lines are skipped; empty output (the
    # command failed) reads as ``None``, the baseline's ``FactBase`` default,
    # so a failed read never masquerades as a freshly booted host. A
    # non-integer line raises ``ValueError``, exactly like the baseline's
    # parser.

    #: Shell reading the uptime: the integer seconds before ``/proc/uptime``'s
    #: decimal point.
    COMMAND = "cut -d. -f1 /proc/uptime"

    @classmethod
    def parse(cls, output: Iterable[str]) -> int | None:
        """Parse the ``/proc/uptime`` row into whole seconds. Pure.

        Blank lines are skipped and empty output reads as None; the first
        surviving line is the integer uptime.
        """
        lines = [line.strip() for line in output if line.strip()]
        if not lines:
            return None
        return int(lines[0])

    @classmethod
    async def process(cls, host: Host) -> int | None:
        uptime = getattr(host, "uptime_seconds", None)
        if uptime is not None:
            # SimulatedHost seam: read the in-memory state directly.
            # int is immutable, so no copy is needed.
            return uptime
        # Real transport: `cut -d. -f1 /proc/uptime` over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class User:
    """The name of the current user (e.g. ``root``).

    **Commands:**

    ```bash
    echo $USER
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
        Examples can be found in ``examples/modules/server/server_user.py``.
    """

    # Mirrors the baseline's ``server.User``: ``echo $USER`` prints the login
    # user's name from the shell environment, returned as the raw string.
    # The first non-blank line is the answer; anything after it is ignored
    # (``echo $USER`` prints exactly one line). Empty output -- the command
    # failed, or ``USER`` is unset or empty -- maps to ``""``, like ``Env``.

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

    **Commands:**

    ```bash
    for i in `cat /etc/passwd | cut -d: -f1`; do
        ENTRY=`grep ^$i: /etc/passwd`;
        LASTLOG=`(((lastlog -u $i || lastlogin $i) 2> /dev/null) | grep ^$i | tr -s ' ')`;
        PASSWORD=`(grep ^$i: /etc/shadow || grep ^$i: /etc/master.passwd) 2> /dev/null | cut -d: -f2`;
        echo "$ENTRY|`id -gn $i`|`id -Gn $i`|$LASTLOG|$PASSWORD";
    done
    ```

    **Returns:**

    ```python
    dict[str, dict[str, Any]]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/server/server_users.py``.
    """

    # Mirrors the baseline's ``server.Users``: a shell loop joins each
    # ``/etc/passwd`` entry with ``id``'s primary and secondary groups, the
    # ``lastlog`` (``lastlogin`` on BSD) row and the shadow password hash,
    # ``|``-separated. Each row becomes, for example::
    #
    #     {
    #         "user_name": {
    #             "home": "/home/user_name",
    #             "comment": "Full Name",
    #             "shell": "/bin/bash",
    #             "group": "main_user_group",
    #             "groups": ["other", "groups"],
    #             "uid": 1000,
    #             "gid": 1000,
    #             "lastlog": "Sat Jun 5 12:03:23 -0600 2021",
    #             "login_time": datetime(2021, 6, 5, 12, 3, 23, ...),
    #             "password": "$6$...",
    #         },
    #     }
    #
    # ``home``, ``comment`` and ``shell`` are ``None`` when the passwd field
    # is empty; ``groups`` holds only the secondary groups (``id -Gn``
    # repeats the primary one); ``uid``/``gid`` are ints; ``lastlog`` is the
    # raw timestamp matched out of the lastlog row (``None`` when the user
    # never logged in) and ``password`` is the raw hash string (``""`` when
    # the shadow file cannot be read). Empty output reads as ``{}``
    # (the baseline's ``default = dict``); blank lines are skipped, and a row
    # missing its four ``|`` separators raises ``ValueError``, like
    # The baseline's parser.
    #
    # Deliberate deviation: the baseline parses ``login_time`` with dateutil's
    # fuzzy ``parse_date``; reemote shuns that dependency and instead tries
    # the two fixed ``strptime`` layouts -- Linux ``lastlog`` (numeric zone
    # before the year, an aware datetime) and BSD ``lastlogin`` (no zone, a
    # naive datetime). A matched ``lastlog`` string that fits neither layout
    # leaves ``login_time`` as ``None`` rather than guessing.

    #: Shell used to gather the user rows over SSH (the baseline's loop,
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

    #: The baseline's regex spotting the login timestamp inside a lastlog row
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
        the baseline). Pure and testable.
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

    **Commands:**

    ```bash
    command -v <command> || true
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
        Examples can be found in ``examples/modules/server/server_which.py``.
    """

    # Mirrors the baseline's ``server.Which``: ``command -v <command> || true``
    # prints the path the shell would run for the name (a builtin or alias
    # reads as its own name), and the ``|| true`` swallows the non-zero exit
    # of an unknown command so a missing binary never fails the read. The
    # name is ``shlex``-quoted (the baseline's ``QuoteString``), so a name carrying
    # shell metacharacters is looked up literally rather than executed. The
    # first non-blank line is the answer; anything after it is ignored
    # (``command -v`` prints at most one line). Empty output -- the command
    # is unavailable -- reads as ``None``, the baseline's ``FactBase`` default, so
    # callers can branch on the answer directly.

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
class GroupAddCommand(Command):
    """One ``groupadd`` invocation.

    ``system`` renders ``-r`` and ``gid`` renders ``--gid``.
    """

    group: str
    system: bool = False
    gid: int | str | None = None

    def shell(self) -> str:
        parts = ["groupadd"]
        if self.system:
            parts.append("-r")
        parts.append(shlex.quote(self.group))
        # The baseline's truthiness check: a gid of 0 is skipped, not rendered.
        if self.gid:
            parts.extend(["--gid", shlex.quote(str(self.gid))])
        return " ".join(parts)


@dataclass(frozen=True)
class GroupDelCommand(Command):
    """One ``groupdel`` invocation."""

    group: str

    def shell(self) -> str:
        return f"groupdel {shlex.quote(self.group)}"


@dataclass(frozen=True)
class GroupModCommand(Command):
    """One ``groupmod -g`` invocation.

    ``gid`` renders ``-g``; the group name is ``shlex``-quoted.
    """

    group: str
    gid: int | str

    def shell(self) -> str:
        return (
            f"groupmod -g {shlex.quote(str(self.gid))} {shlex.quote(self.group)}"
        )


async def group(
    ctx: OpContext,
    group: str,
    present: bool = True,
    system: bool = False,
    gid: int | str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add/remove/reconcile a system group.

    Args:
        ctx (OpContext): Execution context for the operation.
        group (str): Name of the group to ensure.
        present (bool, optional): Whether the group should be present or not.
            Defaults to True.
        system (bool, optional): Whether to create a system group
            (``groupadd -r``). Defaults to False.
        gid (int | str | None, optional): Use a specific group id number; a
            falsy gid (``None`` or ``0``) is skipped, exactly like the baseline's
            ``if gid:`` check. On an already-present group a requested gid is
            reconciled (``groupmod -g``) when the ``GroupDetails`` fact reports
            a different one; a group whose current gid is unknown (a fact read
            predating the gid dimension, or a malformed ``/etc/group`` row)
            is left alone rather than guessed at. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated group add/remove/modify commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        ```python
        from reemote import op, server

        # Create a group (the groupadd needs root, so elevate with sudo)
        yield op(
            server.group,
            name="Ensure the developers group",
            group="developers",
            sudo=True,
        )

        # Create a system group with a fixed gid
        yield op(
            server.group,
            name="Ensure the app system group",
            group="app",
            system=True,
            gid=4000,
            sudo=True,
        )

        # Reconcile a drifted gid on an existing group (groupmod -g)
        yield op(
            server.group,
            name="Pin the developers gid",
            group="developers",
            gid=10000,
            sudo=True,
        )

        # Remove a group
        yield op(
            server.group,
            name="Remove the developers group",
            group="developers",
            present=False,
            sudo=True,
        )
        ```

        Examples can be found in ``examples/modules/server/server_group.py``.
    """

    # Mirrors the baseline's ``server.group``, extended with Ansible's
    # ``group`` module ``gid`` reconciliation (issue #62; the baseline's
    # ``server.group`` only ever passes ``gid`` to ``groupadd``, never
    # repairs a drifted one). Idempotent: the group name is compared against
    # the ``Groups`` fact and a ``groupadd``/``groupdel`` descriptor is
    # yielded only when the group differs from the request. ``system`` still
    # only applies at creation -- a present group is never turned into (or
    # out of) a system account. ``gid`` now reconciles too: when the group is
    # already present and the ``GroupDetails`` fact reports a *different*
    # numeric id than requested, a ``GroupModCommand`` (``groupmod -g``) is
    # planned; an unknown current gid (the fact has no numeric column, or
    # the entry is missing) is left alone rather than repaired on a guess,
    # so a converged host -- and a group whose request carries no ``gid``
    # at all -- stays a no-op, exactly like the baseline's own
    # creation-only behaviour for callers that never pass the option.
    # Repairing a gid does not ``chown`` files still owned by the old
    # numeric id -- the same limitation Ansible's ``group`` module has; the
    # ownership half, if needed, is a separate pass over the filesystem.
    # The commands need root: flag the request ``sudo=True``. ``**kwargs``
    # absorbs the baseline's global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    groups = await ctx.facts.get(Groups)
    is_present = group in groups

    if not present and is_present:
        ctx.changed = True
        ctx.reason = f"group {group} present, will remove"
        yield GroupDelCommand(group=group)
    elif present and not is_present:
        ctx.changed = True
        ctx.reason = f"group {group} absent, will create"
        yield GroupAddCommand(group=group, system=system, gid=gid)
    elif present and is_present and gid:
        # Ansible-parity gid reconciliation: compare the requested id against
        # the present group's own id, as reported by the ``GroupDetails``
        # fact. An unknown current gid (``None``, never a real number in
        # ``/etc/group``) or a match leaves the group alone. A non-numeric
        # requested ``gid`` (a str spelling a caller never meant as an id)
        # is skipped rather than crashing the plan, keeping the render-only
        # behaviour of ``GroupAddCommand`` for that edge case too.
        try:
            requested_gid = int(gid)
        except (TypeError, ValueError):
            requested_gid = None
        details = await ctx.facts.get(GroupDetails)
        current_gid = details.get(group, {}).get("gid")
        if (
            requested_gid is not None
            and current_gid is not None
            and current_gid != requested_gid
        ):
            ctx.changed = True
            ctx.reason = (
                f"group {group} gid {current_gid} differs from requested {gid}, will modify"
            )
            yield GroupModCommand(group=group, gid=gid)
        else:
            ctx.reason = f"group {group} already present with gid {current_gid}"
    else:
        ctx.reason = f"group {group} already {'present' if present else 'absent'}"


class _HomeDirectory:
    """Whether a path is a directory, with its owner pair (server-local probe).

    A private, in-namespace stand-in for ``files.Directory``: ``server.user``
    must learn whether a home directory exists and who owns it, but the
    zero-cross-module-import invariant forbids reaching into the ``files``
    namespace for its fact -- the ``git._DirectoryExists`` pattern of an
    in-namespace probe instead. Only the ownership pair matters here, not
    ``Directory``'s full metadata, so this reads the *same*
    ``directory_stats`` seam and projects it: a dict (a directory's
    metadata) answers a ``(user, group)`` pair, ``False`` (an existing
    non-directory) and an absent key (a missing path) answer ``False`` and
    ``None`` exactly like ``Directory``. On the real transport a
    ``test -d`` guard short-circuits a missing path to no output, and the
    GNU ``stat`` behind it prints only the owner fields.
    """

    # The GNU stat owner-only format, the half of the baseline's ``Directory``
    # command that ``server.user`` actually consumes.
    _GNU_STAT_FORMAT = "user=%U group=%G"
    _STAT_RE = re.compile(r"user=(\S+) group=(\S+)")

    @classmethod
    def command(cls, path: str) -> str:
        """Shell describing ``path``'s owners only when it is a directory.

        A missing path produces no output at all (the ``test -d`` guard
        short-circuits), like the baseline's ``Directory`` chain.
        """
        quoted = shlex.quote(path)
        return f"test -d {quoted} && stat -c '{cls._GNU_STAT_FORMAT}' {quoted}"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, Any] | Literal[False] | None:
        """Parse the owner row into a ``{"user", "group"}`` dict, False or None.

        The first line decides: a ``user= group=`` row is a directory's
        pair, any other output means the path exists but is not a
        directory (``False``), and empty output means absent (``None``).
        """
        line = next(iter(output), None)
        if line is None or not line.strip():
            return None
        match = cls._STAT_RE.search(line)
        if match:
            return {"user": match.group(1), "group": match.group(2)}
        return False

    @classmethod
    async def process(
        cls, host: Host, path: str
    ) -> dict[str, Any] | Literal[False] | None:
        stats = getattr(host, "directory_stats", None)
        if stats is not None:
            # SimulatedHost seam (shared with files.Directory): a dict is a
            # directory's metadata -- projected to the owner pair, copied --
            # False an existing non-directory, an absent key None.
            value = stats.get(path)
            if isinstance(value, dict):
                return {"user": value.get("user"), "group": value.get("group")}
            return value
        # Real transport: the guarded stat chain over asyncssh.
        stdout = await host.run_shell(cls.command(path))
        return cls.parse(stdout.splitlines())


class _HomeIsLink:
    """Whether a path is a symlink (server-local ``files.Link`` stand-in).

    ``server.user``'s home check mirrors the baseline's ``files.directory``
    ``_no_fail_on_link`` knob: a home that exists as a symlink is accepted
    as-is instead of raising. The boolean answer reads the *same*
    ``link_stats`` seam ``files.Link`` uses (a dict row means a symlink is
    there), and on the real transport runs ``test -L``, whose non-zero exit
    on anything else maps to ``False`` exactly like the seam.
    """

    @classmethod
    def command(cls, path: str) -> str:
        """Shell echoing ``yes`` only when the path is a symlink."""
        return f"test -L {shlex.quote(path)} && echo yes"

    @classmethod
    async def process(cls, host: Host, path: str) -> bool:
        stats = getattr(host, "link_stats", None)
        if stats is not None:
            # SimulatedHost seam: a dict row is a symlink's metadata; False
            # (a non-link) and an absent key (missing path) are both no.
            return isinstance(stats.get(path), dict)
        # Real transport: `test -L` over asyncssh (check is off in run_shell).
        stdout = await host.run_shell(cls.command(path))
        return stdout.strip() == "yes"


@dataclass(frozen=True)
class _HomeMkdirCommand(Command):
    """One ``mkdir -p <path>`` home-directory creation.

    A server-local twin of ``files``' mkdir descriptor (the
    zero-cross-module-import invariant forbids reusing it): it renders the
    same ``mkdir -p`` shell the baseline's ``files.directory`` emits behind
    ``server.user``, and the simulated host maps it onto the same
    ``make_directory`` mutation.
    """

    path: str

    def shell(self) -> str:
        return f"mkdir -p {shlex.quote(self.path)}"


@dataclass(frozen=True)
class _HomeChownCommand(Command):
    """One ``chown <user>:<group> <path>`` home-ownership change.

    A server-local twin of ``files``' chown descriptor: ``server.user``
    always requests a full ``user:group`` pair (the baseline passes both, the
    group defaulting to the user name), so this renders only that form --
    both halves ``shlex``-quoted inside the spec (the baseline's
    ``QuoteString``), like ``chown 'some user; whoami':'grp' /home/x``.
    """

    path: str
    user: str
    group: str

    def shell(self) -> str:
        spec = f"{shlex.quote(self.user)}:{shlex.quote(self.group)}"
        return f"chown {spec} {shlex.quote(self.path)}"


@dataclass(frozen=True)
class UserAddCommand(Command):
    """One ``useradd`` invocation.

    Renders the baseline's argument order verbatim: ``-d`` home, ``-s`` shell,
    ``-g`` group, ``-G`` the comma-joined secondary groups, ``-r`` for a
    system account, ``--uid`` for the uid, ``-c`` comment, ``-o`` for a
    non-unique uid, then ``-m``/``-M`` for ``create_home`` and ``-p`` for the
    encrypted password. Falsy ``uid`` renders nothing (the baseline's
    ``if uid:`` truthiness check).
    """

    user: str
    home: str | None = None
    shell_path: str | None = None
    group: str | None = None
    groups: tuple[str, ...] = ()
    system: bool = False
    uid: int | None = None
    comment: str | None = None
    unique: bool = True
    create_home: bool = False
    password: str | None = None

    def shell(self) -> str:
        args: list[str] = []
        if self.home:
            args.extend(["-d", shlex.quote(self.home)])
        if self.shell_path:
            args.extend(["-s", shlex.quote(self.shell_path)])
        if self.group:
            args.extend(["-g", shlex.quote(self.group)])
        if self.groups:
            args.extend(["-G", ",".join(shlex.quote(g) for g in self.groups)])
        if self.system:
            args.append("-r")
        # The baseline's truthiness check: a uid of 0 is skipped, not rendered.
        if self.uid:
            args.extend(["--uid", shlex.quote(str(self.uid))])
        if self.comment:
            args.extend(["-c", shlex.quote(self.comment)])
        if not self.unique:
            args.append("-o")
        if self.create_home:
            args.append("-m")
        else:
            args.append("-M")
        if self.password:
            args.extend(["-p", shlex.quote(self.password)])
        return " ".join(["useradd", *args, shlex.quote(self.user)])


@dataclass(frozen=True)
class UserModCommand(Command):
    """One ``usermod`` invocation.

    The operation fills only the fields that differ from the ``Users``
    fact, in the baseline's argument order: ``-d`` home, ``-s`` shell, ``-g``
    primary group, ``-a -G``/``-G`` secondary groups, ``-c`` comment, and
    ``-p`` password. The user name trails the flags.

    ``groups`` and ``comment`` distinguish "unset" from "set but empty":
    ``groups=None`` renders no group argument (membership left alone) while
    an explicit empty tuple renders ``-G ''`` -- removal from every secondary
    group, the Ansible ``groups: ""`` state; ``comment=None`` renders no
    ``-c`` while ``comment=""`` renders ``-c ''``, clearing the GECOS field.
    An empty ``groups`` never rides ``-a`` (appending nothing must not clear).
    """

    user: str
    home: str | None = None
    shell_path: str | None = None
    group: str | None = None
    groups: tuple[str, ...] | None = None
    append: bool = False
    comment: str | None = None
    password: str | None = None

    def shell(self) -> str:
        args: list[str] = []
        if self.home:
            args.extend(["-d", shlex.quote(self.home)])
        if self.shell_path:
            args.extend(["-s", shlex.quote(self.shell_path)])
        if self.group:
            args.extend(["-g", shlex.quote(self.group)])
        if self.groups is not None:
            joined = ",".join(shlex.quote(g) for g in self.groups)
            if self.append and self.groups:
                args.extend(["-a", "-G", joined])
            else:
                args.extend(["-G", joined or "''"])
        if self.comment is not None:
            args.extend(["-c", shlex.quote(self.comment)])
        if self.password:
            args.extend(["-p", shlex.quote(self.password)])
        return " ".join(["usermod", *args, shlex.quote(self.user)])


@dataclass(frozen=True)
class UserDelCommand(Command):
    """One ``userdel`` invocation.

    The name is ``shlex``-quoted (the baseline's ``QuoteString``), so a username
    carrying shell metacharacters is removed literally rather than
    executed. ``remove_home`` renders ``-r`` (delete the home directory and
    mail spool) and ``force`` renders ``-f`` (force removal even while the
    user is logged in); the flags combine into one short-option cluster in
    ``userdel(8)`` order (``-rf``). Both default ``False``, and with neither
    set the rendering is a byte-identical bare ``userdel <user>``.
    """

    user: str
    remove_home: bool = False
    force: bool = False

    def shell(self) -> str:
        flags = "".join(
            letter
            for letter, enabled in (("r", self.remove_home), ("f", self.force))
            if enabled
        )
        options = f" -{flags}" if flags else ""
        return f"userdel{options} {shlex.quote(self.user)}"


async def user(
    ctx: OpContext,
    user: str,
    present: bool = True,
    remove_home: bool = False,
    force: bool = False,
    home: str | None = None,
    shell: str | None = None,
    group: str | None = None,
    groups: tuple[str, ...] | None = None,
    append: bool = False,
    ensure_home: bool = True,
    create_home: bool = False,
    system: bool = False,
    uid: int | None = None,
    comment: str | None = None,
    unique: bool = True,
    password: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add, update, or remove a system user.

    Args:
        ctx (OpContext): Execution context for the operation.
        user (str): Name of the user to ensure.
        present (bool, optional): Whether the user should exist.
            Defaults to True.
        remove_home (bool, optional): On removal (``present=False``), delete
            the user's home directory and mail spool too (``userdel -r``, the
            Ansible ``remove`` option). Ignored when the user is kept. Only
            honoured when there is actually a user to remove, so a converged
            host plans nothing. Defaults to False.
        force (bool, optional): On removal (``present=False``), force the
            removal even while the user is logged in or owned files cannot be
            found (``userdel -f``, the Ansible ``force`` option). Ignored when
            the user is kept. Defaults to False.
        home (str | None, optional): The user's home directory; defaults
            to ``/home/<user>`` for a new user, or the recorded home for a
            present one. Defaults to None.
        shell (str | None, optional): The user's login shell. Defaults to
            None.
        group (str | None, optional): The user's primary group. Defaults
            to None.
        groups (tuple[str, ...] | None, optional): The user's secondary
            groups. ``None`` (the default) leaves an existing user's
            membership alone; an explicit empty tuple ``()`` reconciles it
            to *no* secondary groups (``usermod -G ''``, the Ansible
            ``groups: ""`` state), a no-op with ``append=True`` since
            appending never removes. A tuple, never a list:
            ``OperationRequest`` hashes every argument for
            ``compose(dedupe=True)``, so a list would be rejected with
            ``UnhashableArgumentError``. Defaults to None.
        append (bool, optional): Whether to add the user to ``groups``
            without losing membership of other groups. Defaults to False.
        ensure_home (bool, optional): Whether to ensure the ``home``
            directory exists and belongs to the user. Defaults to True.
        create_home (bool, optional): Whether a new user's home directory
            is built from the system skeleton (``useradd -m``). Defaults
            to False.
        system (bool, optional): Whether to create a system account
            (``useradd -r``). Defaults to False.
        uid (int | None, optional): Use a specific user id number; a
            falsy uid (``None`` or ``0``) is skipped, exactly like
            the baseline's ``if uid:`` check. Defaults to None.
        comment (str | None, optional): The user's GECOS comment. ``None``
            (the default) leaves an existing comment alone; an explicit
            empty string ``""`` clears it (``usermod -c ''``, the Ansible
            ``comment: ""`` state). Defaults to None.
        unique (bool, optional): Whether to prevent creating a user with
            a duplicate UID (``False`` renders ``-o``). Defaults to True.
        password (str | None, optional): The *encrypted* password hash to
            set. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated user add/update/remove and home-directory
            commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, OperationError
    ```

    Examples:
        ```python
        from reemote import op, server

        # Create a user with a login shell, secondary groups and a home built from the skeleton
        yield op(
            server.user,
            name="Ensure deploy user",
            user="deploy",
            shell="/bin/bash",
            groups=("sudo", "docker"),
            create_home=True,
            sudo=True,
        )

        # Append the user to a group without dropping its other memberships
        yield op(
            server.user,
            name="Add deploy to www-data",
            user="deploy",
            groups=("www-data",),
            append=True,
            sudo=True,
        )

        # Reconcile to no secondary groups and clear the GECOS comment: the
        # explicit empty values are states, not "leave alone" (that is None)
        yield op(
            server.user,
            name="Strip deploy of secondary groups and comment",
            user="deploy",
            groups=(),
            comment="",
            sudo=True,
        )

        # Remove a user
        yield op(
            server.user,
            name="Remove deploy user",
            user="deploy",
            present=False,
            sudo=True,
        )

        # Remove a user, its home directory and mail spool too (userdel -r),
        # forcing past a logged-in session (userdel -f) -- one descriptor
        yield op(
            server.user,
            name="Remove legacy user with its home",
            user="legacy",
            present=False,
            remove_home=True,
            force=True,
            sudo=True,
        )
        ```

        Examples can be found in ``examples/modules/server/server_op_user.py``.
    """

    # Mirrors the baseline's ``server.user``. Idempotent: the requested state is
    # compared against the ``Users`` fact and a descriptor is yielded only
    # where they differ -- ``userdel`` (an existing user, ``present=False``),
    # ``useradd`` (an absent one), or ``usermod`` (a field-by-field update
    # of a present one). A creation with no primary ``group`` reuses an
    # existing group of the user's name when there is one (the baseline's
    # tell-useradd-to-use-it fix). ``home`` defaults to ``/home/<user>`` for
    # an absent user and to the recorded home for a present one. The
    # ``ensure_home`` branch (on by default) mirrors the baseline's delegation to
    # ``files.directory`` with ``_no_fail_on_link=True`` through the private
    # ``_HomeDirectory``/``_HomeIsLink`` stand-ins and the
    # ``_HomeMkdirCommand``/``_HomeChownCommand`` pair: an absent home is
    # created and owned, a wrong owner is ``chown``ed, a symlinked home is
    # accepted, and a non-directory home raises ``OperationError`` (the baseline's
    # message). The commands need root: flag the request ``sudo=True``.
    #
    # The baseline's ``public_keys``/``delete_keys`` options are deliberately
    # dropped (developer decision): that branch delegates to
    # ``server.user_authorized_keys``, which reemote keeps as its own
    # explicit operation -- a spool-and-install write covering both the
    # append and the ``delete_keys`` rewrite -- rather than an option on
    # ``user``; manage key files with that operation directly.
    # The baseline's plan-time ``Which`` guards ("useradd is not installed", with
    # the Alpine shadow-package note) are deliberately dropped too: a missing
    # binary surfaces at apply as ``CommandNotFoundError`` (exit 127), the
    # convention every other reemote operation follows. ``**kwargs``
    # absorbs any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    # ``groups``/``comment`` distinguish "unset" (``None``, leave alone) from
    # "set but empty" (``()``/``""``, reconcile to none -- the Ansible
    # ``groups: ""``/``comment: ""`` states), so the gates below compare by
    # identity, never truthiness.
    users = await ctx.facts.get(Users)
    existing_groups = await ctx.facts.get(Groups)
    existing_user = users.get(user)
    requested_groups = tuple(groups) if groups is not None else None

    if home is None:
        home = f"/home/{user}"
        if existing_user:
            home = existing_user.get("home", home)

    reasons: list[str] = []

    # User not wanted?: remove an existing one, then stop. ``remove_home``
    # (``userdel -r``) and ``force`` (``userdel -f``) render onto the one
    # deletion descriptor -- both are honoured only when there is actually a
    # user to remove, so a converged host still plans nothing; the baseline's
    # bare deletion never touches the home directory or keys.
    if not present:
        if existing_user:
            ctx.changed = True
            reasons.append(f"user {user} present, will remove")
            yield UserDelCommand(user=user, remove_home=remove_home, force=force)
        else:
            reasons.append(f"user {user} already absent")
        ctx.reason = "; ".join(reasons)
        return

    if existing_user is None:
        # Creation. Fix the case where a group of the same name already
        # exists: tell useradd to use it rather than failing (the baseline).
        if not group and user in existing_groups:
            group = user
        ctx.changed = True
        reasons.append(f"user {user} absent, will create")
        yield UserAddCommand(
            user=user,
            home=home,
            shell_path=shell,
            group=group,
            groups=requested_groups if requested_groups is not None else (),
            system=system,
            uid=uid,
            comment=comment,
            unique=unique,
            create_home=create_home,
            password=password,
        )
    else:
        # Update: each requested field is compared against the fact and only
        # the differing ones ride the usermod (the baseline's mod_args).
        mod_home = home if home != existing_user.get("home") else None
        mod_shell = (
            shell if shell and shell != existing_user.get("shell") else None
        )
        mod_group = (
            group if group and group != existing_user.get("group") else None
        )
        # The fact reads an empty passwd comment field as None; normalize it
        # to "" so a cleared GECOS converges instead of re-clearing every run.
        mod_comment = (
            comment
            if comment is not None
            and comment != (existing_user.get("comment") or "")
            else None
        )
        password_changed = bool(
            password and password != existing_user.get("password")
        )
        mod_password = password if password_changed else None
        current_groups = list(existing_user.get("groups") or [])
        mod_groups: tuple[str, ...] | None = None
        groups_change = False
        if requested_groups is not None:
            if append:
                # Only membership the user lacks; append never removes, so
                # an empty request is always already a subset (a no-op).
                if not set(requested_groups).issubset(current_groups):
                    groups_change = True
                    mod_groups = requested_groups
            elif set(current_groups) != set(requested_groups):
                groups_change = True
                mod_groups = requested_groups
        other_changes = bool(
            mod_home
            or mod_shell
            or mod_group
            or groups_change
            or mod_comment is not None
        )
        if not (other_changes or password_changed):
            reasons.append(f"user {user} already as requested")
        else:
            ctx.changed = True
            reasons.append(f"user {user} differs, will update")
            if other_changes or mod_password:
                yield UserModCommand(
                    user=user,
                    home=mod_home,
                    shell_path=mod_shell,
                    group=mod_group,
                    groups=mod_groups,
                    append=append,
                    comment=mod_comment,
                    password=mod_password,
                )

    # Ensure home directory ownership (the baseline's files.directory
    # delegation, inlined via the stand-in probes and twin descriptors).
    if ensure_home and home:
        info = await ctx.facts.get(_HomeDirectory, home)
        if info is False:
            if await ctx.facts.get(_HomeIsLink, home):
                reasons.append(f"home {home} already exists (as a link)")
            else:
                raise OperationError(f"{home} exists and is not a directory")
        elif info is None:
            ctx.changed = True
            reasons.append(f"home {home} missing, will create")
            yield _HomeMkdirCommand(path=home)
            yield _HomeChownCommand(path=home, user=user, group=group or user)
        elif info.get("user") != user or info.get("group") != (group or user):
            ctx.changed = True
            reasons.append(f"home {home} owner differs, will chown")
            yield _HomeChownCommand(path=home, user=user, group=group or user)
        else:
            reasons.append(f"directory {home} already exists")

    ctx.reason = "; ".join(reasons)


# --- server.user_authorized_keys -------------------------------------------

#: The baseline's fixed modes for ``user_authorized_keys``: the ``.ssh`` directory
#: is mode ``700`` and the ``authorized_keys`` file itself ``600``, never
#: caller-configurable.
_SSH_DIR_MODE = 700
_AUTHORIZED_KEYS_MODE = 600


class _KeysPathStat:
    """Existence, ownership and mode of one ``user_authorized_keys`` path.

    A private, in-namespace stand-in for ``files.File``/``files.Directory``:
    ``server.user_authorized_keys`` must know whether the ``.ssh`` directory
    and the ``authorized_keys`` file already carry the requested owner and
    mode, but the zero-cross-module-import invariant forbids reaching into
    the ``files`` namespace for either fact -- the ``_HomeDirectory`` pattern
    of an in-namespace probe instead, extended to also project ``mode``
    (which ``_HomeDirectory`` drops since ``server.user`` never requests one)
    and to accept a ``directory`` flag picking which of the two shared seams
    to read. Only the ``user``/``group``/``mode`` fields matter here, not the
    full stat metadata, so this reads the *same* ``directory_stats``
    (``directory=True``) or ``file_stats`` (``directory=False``) seam and
    projects the three fields, or ``False``/``None`` for an existing path of
    the wrong type or a missing one. Like ``_HomeDirectory``, the real
    transport's ``test -d``/``test -f`` guard cannot distinguish "missing"
    from "wrong type" (both short-circuit to no output and read as ``None``)
    -- the same simplification ``_HomeDirectory`` already accepts, since
    only a test seeds a deliberately wrong-typed path.
    """

    # GNU stat owner/mode format. ``%a`` prints the mode as its octal digits
    # spelled out (e.g. ``700``), which is exactly the octal-looking int
    # convention ``files.File``/``files.Directory`` store and compare against
    # (``int(oct(bits)[2:])``, not the raw bit value).
    _GNU_STAT_FORMAT = "user=%U group=%G mode=%a"
    _STAT_RE = re.compile(r"user=(\S+) group=(\S+) mode=(\S+)")

    @classmethod
    def command(cls, path: str, directory: bool) -> str:
        """Shell describing ``path``'s owner/mode only for the wanted type.

        A missing or wrong-typed path produces no output at all (the
        ``test -d``/``test -f`` guard short-circuits), like
        the baseline's ``Directory``/``File`` chains.
        """
        test = "-d" if directory else "-f"
        quoted = shlex.quote(path)
        return f"test {test} {quoted} && stat -c '{cls._GNU_STAT_FORMAT}' {quoted}"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, Any] | Literal[False] | None:
        """Parse the stat row into a ``{"user", "group", "mode"}`` dict, False or None.

        The first line decides: a ``user= group= mode=`` row is the wanted
        type's metadata, any other output means the path exists but is not
        the wanted type (``False``), and empty output means absent
        (``None``, unreachable on the real transport since the guard already
        short-circuits both cases there -- only a seeded seam can produce a
        literal ``False`` row).
        """
        line = next(iter(output), None)
        if line is None or not line.strip():
            return None
        match = cls._STAT_RE.search(line)
        if match:
            return {
                "user": match.group(1),
                "group": match.group(2),
                "mode": int(match.group(3)),
            }
        return False

    @classmethod
    async def process(
        cls, host: Host, path: str, directory: bool = True
    ) -> dict[str, Any] | Literal[False] | None:
        stats = getattr(host, "directory_stats" if directory else "file_stats", None)
        if stats is not None:
            # SimulatedHost seam (shared with files.Directory/files.File): a
            # dict is the wanted type's metadata -- projected to the
            # owner/mode triple, copied -- False an existing wrong-typed
            # path, an absent key None.
            value = stats.get(path)
            if isinstance(value, dict):
                return {
                    "user": value.get("user"),
                    "group": value.get("group"),
                    "mode": value.get("mode"),
                }
            return value
        # Real transport: the guarded stat chain over asyncssh.
        stdout = await host.run_shell(cls.command(path, directory))
        return cls.parse(stdout.splitlines())


@dataclass(frozen=True)
class _KeysChmodCommand(Command):
    """One ``chmod <mode> <path>`` for a ``user_authorized_keys`` path.

    A server-local twin of ``files``' chmod descriptor (the
    zero-cross-module-import invariant forbids reusing it): it renders the
    same bare, non-recursive ``chmod`` shell the baseline's ``files.file``/
    ``files.directory`` emit behind ``server.user_authorized_keys``, and the
    simulated host maps it onto the same ``chmod_path`` mutation. The mode
    is always one of the two fixed, caller-invisible constants above, never
    a user-supplied value, so it renders unquoted exactly as the baseline's own
    chmod helper does; only the target is shell-quoted.
    """

    path: str
    mode: int

    def shell(self) -> str:
        return f"chmod {self.mode} {shlex.quote(self.path)}"


@dataclass(frozen=True)
class _KeysTouchCommand(Command):
    """One ``touch <path>`` creating a missing ``authorized_keys`` file.

    A server-local twin of ``files``' create-file descriptor (the
    zero-cross-module-import invariant forbids reusing it): it renders the
    same ``touch`` shell the baseline's ``files.file`` emits behind
    ``server.user_authorized_keys`` to bring a key file into existence
    before its mode/ownership are fixed up, and the simulated host maps it
    onto the same ``create_file`` mutation.
    """

    path: str

    def shell(self) -> str:
        return f"touch {shlex.quote(self.path)}"


def _keys_spool_path(dest: str) -> str:
    """The deterministic ``/tmp`` spool *file* path ``user_authorized_keys`` uploads to.

    The content is spooled to a fixed ``authorized_keys`` file *inside* a
    sha1-derived ``/tmp`` directory rather than at the top level of ``/tmp``;
    the elevated install ``rm -rf``s the whole directory afterwards.

    Derived from the managed key file's sha1 (``files.install``'s
    ``_install_spool_path`` / ``apt.repo``'s ``_repo_spool_path`` pattern),
    even though each request edits exactly one file: the
    ``authorized_key_directory``/``authorized_key_filename`` options mean two
    requests in one deploy can target different key files, and they must not
    clobber each other's spool. The same ``dest`` always yields the same name
    in both phases, so the descriptors stay deterministic. The name avoids
    *collisions*, not a hostile local user: it is predictable, so on a
    multi-tenant host the ``/tmp`` spool is still a symlink-race exposure
    (the trade-off every spool-and-install operation here makes).
    """
    digest = hashlib.sha1(dest.encode()).hexdigest()[:16]
    return f"/tmp/reemote-server-user-authorized-keys-{digest}/authorized_keys"


def _add_keys(content: str | None, keys: tuple[str, ...]) -> str:
    """The pure append transform: ``content`` plus each of ``keys`` it lacks.

    ``user_authorized_keys(delete_keys=False)``'s edit of the key file's
    current text: an absent file (``None``) becomes just the keys, a key the
    text already carries as a whole line is never duplicated -- presence is
    the ``AuthorizedKeys`` parser's own stripped whole-line comparison, so a
    commented-out ``# <key>`` line does *not* suppress the append of the live
    key (the deliberate difference from ``apt.repo``'s substring line
    pattern) -- and repeated entries in ``keys`` land once, in the caller's
    own order. Untouched lines, comments, blank lines and the file's own
    trailing-newline state are preserved; a newline-less last line gets its
    separator here, functionally, replacing the old descriptor's
    ``tail -c1`` shell guard. Idempotent (``f(f(x)) == f(x)``) and
    side-effect free, so both deploy phases compute the same content from
    the same fact value.
    """
    rows = content.splitlines() if content else []
    present = {row.strip() for row in rows}
    appended = [key for key in dict.fromkeys(keys) if key not in present]
    if not appended:
        return content or ""
    base = content or ""
    separator = "" if not base or base.endswith("\n") else "\n"
    return base + separator + "".join(f"{key}\n" for key in appended)


def _rewrite_keys(keys: tuple[str, ...]) -> str:
    """The pure whole-file rewrite transform ``delete_keys=True`` spools.

    The baseline's ``"{}\\n".format("\\n".join(public_keys))`` verbatim: the file
    becomes exactly the requested keys in the caller's order, one per line,
    with a trailing newline -- every other key, comment and blank line is
    discarded (that is what ``delete_keys`` means upstream). An empty key
    tuple therefore yields a lone ``"\\n"``, the baseline's own quirk, kept for
    parity. The current text is deliberately not an input: the rewrite is a
    function of the request alone, so it is trivially deterministic across
    phases.
    """
    return "\n".join(keys) + "\n"


@dataclass(frozen=True)
class AuthorizedKeysUploadCommand(TransferCommand):
    """Spool the transformed key-file content to a ``/tmp`` path on the target.

    The first half of ``user_authorized_keys``' write and a *non-shell*
    descriptor: it copies the content over the connection's scp channel via
    ``Host.upload_file`` rather than running shell text, so it has no
    ``shell()`` and its loggable form is the ``__str__`` rendering
    ``command_shell`` falls back to. Like ``AptRepoUploadCommand`` the
    content is not a caller-supplied stream but the whole key-file text the
    operation just derived from the file's current content, so the field is
    a plain ``str`` -- hashable and identical across phases -- wrapped in a
    fresh ``io.BytesIO`` only at transfer time. The copy rides as the login
    user (transfers are unprivileged, so the request's ``sudo`` does not
    apply to it), which is why the spool path is world-writable ``/tmp``
    rather than the other user's ``700``-mode ``.ssh`` directory.
    """

    content: str  # the whole transformed authorized_keys text
    dest: str  # spool path on the target host

    def __str__(self) -> str:
        return f"scp <authorized keys> -> {self.dest}"

    async def transfer(self, host: Host) -> None:
        # scp does not create parent directories, and the spool now lives
        # inside a sha1-derived /tmp directory rather than at the top level,
        # so mkdir -p it first. Unprivileged: /tmp is world-writable, so the
        # login user can create the directory, and a transfer never elevates.
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        await host.run_shell(f"mkdir -p {parent}")
        await host.upload_file(io.BytesIO(self.content.encode()), self.dest)


@dataclass(frozen=True)
class AuthorizedKeysInstallCommand(Command):
    """Install the spooled key file ``600``, then remove the spool file.

    The second half of ``user_authorized_keys``' write and the elevated one
    when the keys belong to another user: the whole sequence is wrapped in
    ``sh -c`` -- a bare ``sudo`` prefix would elevate only the first command
    of the ``&&`` chain and leave the cleanup running as the login user,
    whereas ``sudo sh -c '...'`` runs every step elevated, which can always
    remove the login-user spool file. The sequence ensures the destination
    directory, copies the spooled content over the key file, chmods it
    ``600`` (this operation's fixed mode, the ``AptRepoInstallCommand``
    pattern with apt's ``0644`` replaced) and removes the spool. No chown is
    needed: the operation's state block has already touched/chowned the key
    file earlier in the same request, and ``cp`` over an existing file
    preserves its owner -- the ``user``/``group`` ownership stays with the
    ``_HomeChownCommand`` that set it. For the login user's own keys the
    whole chain also runs unprivileged. All paths are shell-quoted.
    """

    spool: str  # where AuthorizedKeysUploadCommand wrote the content
    dest: str  # the authorized_keys path being managed

    def shell(self) -> str:
        spool = shlex.quote(self.spool)
        # The spool file lives inside a sha1-derived /tmp directory; remove
        # the whole directory so no leftover lands in /tmp.
        spool_dir = shlex.quote(str(PurePosixPath(self.spool).parent))
        dest = shlex.quote(self.dest)
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        inner = (
            f"mkdir -p {parent} && "
            f"cp {spool} {dest} && "
            f"chmod {_AUTHORIZED_KEYS_MODE} {dest} && rm -rf {spool_dir}"
        )
        return f"sh -c {shlex.quote(inner)}"


async def user_authorized_keys(
    ctx: OpContext,
    user: str,
    public_keys: str | list[str] | tuple[str, ...],
    group: str | None = None,
    delete_keys: bool = False,
    authorized_key_directory: str | None = None,
    authorized_key_filename: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add SSH public keys to a user's ``authorized_keys`` file.

    Args:
        ctx (OpContext): Execution context for the operation.
        user (str): Name of the user whose keys to manage.
        public_keys (str | list[str] | tuple[str, ...]): Public key strings
            to attach to the user. Prefer tuples when building requests via
            ``op()``, since ``OperationRequest`` kwargs must stay hashable.
            Every entry is always a literal key, never a path to a key file.
        group (str | None, optional): The group to own the ``.ssh``
            directory and key file; defaults to the user's own name.
            Defaults to None.
        delete_keys (bool, optional): Whether to remove any keys not
            specified in ``public_keys``: the file is rewritten to exactly
            the requested keys (comments and blank lines included in the
            discard), instead of only appending the missing ones.
            Defaults to False.
        authorized_key_directory (str | None, optional): The directory to
            write into; defaults to the user's ``Home`` fact resolved to
            ``<home>/.ssh`` -- a user with no resolvable home directory
            (unknown to the host) raises ``OperationError``. Defaults to None.
        authorized_key_filename (str | None, optional): The file name to
            write inside ``authorized_key_directory``; defaults to
            ``authorized_keys``. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated ``.ssh`` directory/key-file setup commands and
            the spool-and-install pair carrying the new key-file content.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, OperationError, ValueError
    ```

    Examples:
        ```python
        from reemote import op, server

        # Append a key to the user's authorized_keys (writes another user's ~/.ssh, so elevate)
        yield op(
            server.user_authorized_keys,
            name="Authorize the deploy key",
            user="deploy",
            public_keys=("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5 deploy@laptop",),
            sudo=True,
        )

        # Manage the file exactly: drop every key not listed here
        yield op(
            server.user_authorized_keys,
            name="Pin root's authorized_keys",
            user="root",
            public_keys=("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5 admin@laptop",),
            delete_keys=True,
            sudo=True,
        )
        ```

        Examples can be found in
        ``examples/modules/server/server_user_authorized_keys.py``.
    """

    # Mirrors the baseline's ``server.user_authorized_keys`` for ``user``,
    # ``public_keys``, ``group``, ``delete_keys``,
    # ``authorized_key_directory`` and ``authorized_key_filename``.
    # Idempotent. The ``.ssh`` directory is
    # ensured first (mode ``700``, owner ``user``/``group``, the ``group``
    # option defaulting to the user's own name exactly as the baseline's
    # ``group or user`` does) and the ``authorized_keys`` file second (mode
    # ``600``), each compared against a ``_KeysPathStat`` in-namespace probe
    # of the shared ``directory_stats``/``file_stats`` seams: a missing
    # directory is ``mkdir -p``'d, chmod'd and chown'd, a wrong mode or
    # owner re-runs just that half, and a wrong-typed path raises
    # ``OperationError`` (the baseline's ``files.directory`` message shape). The
    # ``_HomeMkdirCommand``/``_HomeChownCommand`` pair already renders
    # exactly the mkdir/chown verbs needed, so it is reused rather than
    # duplicated; only the chmod and touch verbs, which that pair has no
    # twin for, get the new ``_KeysChmodCommand``/``_KeysTouchCommand``
    # stand-ins. Those state verbs stay in-place (a touch/mkdir/chmod/chown
    # payload is not file text); only the key-content write below moved to
    # the spool-and-install family.
    #
    # The ``Home`` fact answers ``None`` for a user the host does not know
    # (its ``echo ~user`` stays unexpanded), and that reads as an
    # ``OperationError`` naming the user before any command is planned --
    # never a literal ``None/.ssh`` path that would mkdir/chmod a junk
    # directory and only fail later at ``chown`` (developer decision: the
    # baseline builds the bogus path; the fact's own no-masquerade rule is
    # enforced here). An explicit ``authorized_key_directory`` bypasses the
    # Home read entirely, so the guard never fires for it.
    #
    # Key idempotence rides the ``AuthorizedKeys`` fact (read with an
    # explicit ``path``, never tilde-resolved): with ``delete_keys=False``
    # every requested key not already listed is missing (the baseline's set
    # membership); with ``delete_keys=True`` the parsed list must equal the
    # requested keys exactly -- the baseline's order-sensitive list comparison,
    # so a mere reordering rewrites the file. When a change is due, the
    # ``AuthorizedKeysFile`` raw-content fact supplies the file's current
    # text and a pure transform computes the new whole content
    # (``_add_keys`` appends only the missing keys, preserving comments,
    # blank lines and order; ``_rewrite_keys`` replaces everything with
    # exactly the requested keys). One ``AuthorizedKeysUploadCommand`` scp's
    # that text to a ``/tmp`` spool whose name is derived from the managed
    # path (so two requests targeting different key files never share one
    # spool), then one ``AuthorizedKeysInstallCommand`` overlays the key
    # file with it under ``sh -c``. The transfer stays unprivileged
    # regardless of ``sudo`` -- only the install elevates -- which is the
    # two-privilege-level signature of the family. The old in-place
    # ``echo <key> >>`` append descriptor (the baseline's ``files.line`` idiom
    # with its ``tail -c1`` newline guard) is gone: the transform handles
    # newline endings functionally on the controller. Writing into another
    # user's ``~/.ssh`` needs root: flag the request ``sudo=True``.
    #
    # ``delete_keys`` was originally dropped because the baseline implements it
    # through an *elevated* ``files.put`` and reemote transfers never
    # elevate; the spool-and-install conversion removed that obstacle, so
    # the option is implemented now (developer decision). Its rewrite
    # discards comments and blank lines along with the unrequested keys --
    # The baseline's own whole-file semantics, deliberately not softened. The
    # controller-side ``public_keys`` file paths stay dropped (developer
    # decision, matching reemote's IO-only/no-controller-path rule
    # everywhere else -- ``files.put``/``files.get``/``apt.key``/``dnf.key``
    # all reject a path string for the same reason): every entry here is
    # always a literal key string, and reading a controller file twice
    # across the prepare and apply phases would risk a ``DeterminismError``
    # anyway if it changed in between. ``**kwargs`` absorbs the baseline's
    # global arguments (``op()`` already strips ``name``/``sudo``/``su``).
    if isinstance(public_keys, str):
        requested = (public_keys,)
    else:
        requested = tuple(public_keys)

    if authorized_key_directory is None:
        home = await ctx.facts.get(Home, user=user)
        if home is None:
            raise OperationError(f"cannot resolve a home directory for user {user!r}")
        authorized_key_directory = f"{home}/.ssh"

    if authorized_key_filename is None:
        authorized_key_filename = "authorized_keys"

    key_file = f"{authorized_key_directory}/{authorized_key_filename}"
    key_group = group or user

    reasons: list[str] = []

    dir_info = await ctx.facts.get(_KeysPathStat, authorized_key_directory)
    if dir_info is False:
        raise OperationError(f"{authorized_key_directory} exists and is not a directory")
    if dir_info is None:
        ctx.changed = True
        reasons.append(f"{authorized_key_directory} missing, will create")
        yield _HomeMkdirCommand(path=authorized_key_directory)
        yield _KeysChmodCommand(path=authorized_key_directory, mode=_SSH_DIR_MODE)
        yield _HomeChownCommand(path=authorized_key_directory, user=user, group=key_group)
    else:
        if dir_info["mode"] != _SSH_DIR_MODE:
            ctx.changed = True
            reasons.append(
                f"mode {dir_info['mode']} differs, will chmod {_SSH_DIR_MODE}"
            )
            yield _KeysChmodCommand(path=authorized_key_directory, mode=_SSH_DIR_MODE)
        if dir_info["user"] != user or dir_info["group"] != key_group:
            ctx.changed = True
            reasons.append(
                f"owner {dir_info['user']}:{dir_info['group']} differs, will chown"
            )
            yield _HomeChownCommand(path=authorized_key_directory, user=user, group=key_group)
        if not ctx.changed:
            reasons.append(f"directory {authorized_key_directory} already exists")

    file_info = await ctx.facts.get(_KeysPathStat, key_file, directory=False)
    if file_info is False:
        raise OperationError(f"{key_file} exists and is not a file")
    if file_info is None:
        ctx.changed = True
        reasons.append(f"{key_file} missing, will create")
        yield _KeysTouchCommand(path=key_file)
        yield _KeysChmodCommand(path=key_file, mode=_AUTHORIZED_KEYS_MODE)
        yield _HomeChownCommand(path=key_file, user=user, group=key_group)
    else:
        if file_info["mode"] != _AUTHORIZED_KEYS_MODE:
            ctx.changed = True
            reasons.append(
                f"mode {file_info['mode']} differs, will chmod {_AUTHORIZED_KEYS_MODE}"
            )
            yield _KeysChmodCommand(path=key_file, mode=_AUTHORIZED_KEYS_MODE)
        if file_info["user"] != user or file_info["group"] != key_group:
            ctx.changed = True
            reasons.append(
                f"owner {file_info['user']}:{file_info['group']} differs, will chown"
            )
            yield _HomeChownCommand(path=key_file, user=user, group=key_group)

    current_keys = await ctx.facts.get(AuthorizedKeys, user, path=key_file)
    missing: tuple[str, ...] = ()
    if delete_keys:
        keys_differ = current_keys != list(requested)
    else:
        current_key_set = set(current_keys)
        missing = tuple(key for key in requested if key not in current_key_set)
        keys_differ = bool(missing)

    if not keys_differ:
        if not ctx.changed and file_info is not None:
            reasons.append(f"file {key_file} already exists")
        ctx.reason = "; ".join(reasons)
        return

    current = await ctx.facts.get(AuthorizedKeysFile, key_file)
    new = _rewrite_keys(requested) if delete_keys else _add_keys(current, missing)
    if new == (current or ""):
        # The fact wanted an edit the file's own text says is unnecessary.
        reasons.append(f"keys already written in {key_file}")
        ctx.reason = "; ".join(reasons)
        return

    ctx.changed = True
    spool = _keys_spool_path(key_file)
    reasons.append(
        f"keys differ, will rewrite {key_file}"
        if delete_keys
        else f"{len(missing)} key(s) missing, will append"
    )
    yield AuthorizedKeysUploadCommand(content=new, dest=spool)
    yield AuthorizedKeysInstallCommand(spool=spool, dest=key_file)

    ctx.reason = "; ".join(reasons)


# --- server.ssh_keypair -------------------------------------------------------

#: Key types ``ssh-keygen`` accepts a free ``-b`` bit length for. ``ed25519``
#: and ``ed448`` carry no size at all, ``ecdsa`` takes only the fixed curve
#: sizes (256/384/521) and ``dsa`` only 2048, so ``-b`` is rendered with the
#: default ``size=4096`` only for these types -- everywhere else it would
#: make the generation command fail.
_SIZEFUL_KEY_TYPES = ("rsa", "rsa1")


@dataclass(frozen=True)
class SshKeyPairGenerateCommand(Command):
    """One ``ssh-keygen`` key-pair generation, run as the target user.

    The elevated half of ``ssh_keypair``, wrapped in ``sh -c`` -- a bare
    ``sudo`` prefix would elevate only the first command of the ``&&``
    chain, whereas ``sudo sh -c '...'`` runs every step elevated (the
    ``AuthorizedKeysInstallCommand`` precedent). Inside the chain,
    ``su -s /bin/sh <user> -c '<ssh-keygen ...>'`` drops to the target user
    so the pair is created with the right owner outright -- no chown
    follow-up, where the key file itself needs none of the
    ``user_authorized_keys`` fix-ups. ``-q`` silences the banner,
    ``-t``/``-f`` pass the key type and private-key path, and ``-b`` is
    rendered only for the types that take a free bit length
    (``_SIZEFUL_KEY_TYPES``). ``-N ''`` always writes an unprotected key --
    ``ssh_keypair`` refuses a passphrase, so no secret ever rides a plan or
    log (the ``gpg.gen_key`` ``%no-protection`` precedent). With ``force`` an
    ``rm -f`` of both halves runs first (the operation decides regeneration
    from the ``SshKeyPair`` fact; the prefix keeps the command
    self-contained). Usernames and all paths are shell-quoted.
    """

    user: str  # the account the pair is generated for (run as via su)
    private_path: str  # ssh-keygen -f target; the public half is + ".pub"
    public_path: str  # removed alongside on force, never passed to ssh-keygen
    key_type: str  # rendered as -t
    size: int  # rendered as -b only for _SIZEFUL_KEY_TYPES
    comment: str | None = None  # rendered as -C when given
    force: bool = False  # set -> rm -f both halves first

    def shell(self) -> str:
        private = shlex.quote(self.private_path)
        public = shlex.quote(self.public_path)
        parts = ["ssh-keygen", "-q", "-t", shlex.quote(self.key_type)]
        if self.key_type in _SIZEFUL_KEY_TYPES:
            parts += ["-b", str(self.size)]
        parts += ["-f", private, "-N", "''"]
        if self.comment:
            parts += ["-C", shlex.quote(self.comment)]
        keygen = " ".join(parts)
        run_as = f"su -s /bin/sh {shlex.quote(self.user)} -c {shlex.quote(keygen)}"
        inner = f"rm -f {private} {public} && " if self.force else ""
        inner += run_as
        return f"sh -c {shlex.quote(inner)}"


async def ssh_keypair(
    ctx: OpContext,
    user: str,
    type: str = "rsa",
    size: int = 4096,
    path: str | None = None,
    comment: str | None = None,
    passphrase: str | None = None,
    force: bool = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Generate an SSH key pair for a user, idempotently.

    The Ansible ``user`` module's ``generate_ssh_key`` parity: the pair is
    created only where its public half has no fingerprint yet, and the
    generation runs as the target user so the files land owned correctly.

    Args:
        ctx (OpContext): Execution context for the operation.
        user (str): Name of the user the key pair belongs to.
        type (str, optional): Key type passed to ``ssh-keygen -t``; the
            default private-key name embeds it (``id_<type>``). Only
            ``rsa``/``rsa1`` receive the ``size`` as ``-b``. Defaults to "rsa".
        size (int, optional): Bit length passed to ``ssh-keygen -b`` for the
            key types that take a free size; skipped for ``ed25519``,
            ``ecdsa`` and ``dsa``, where the tool rejects or fixes it.
            Defaults to 4096.
        path (str | None, optional): The *private* key file path, mirroring
            Ansible's ``generate_ssh_key_file``; the public half is
            ``<path>.pub``. Defaults to the user's ``Home`` fact resolved to
            ``<home>/.ssh/id_<type>`` -- a user with no resolvable home
            directory (unknown to the host) raises ``OperationError``.
            Defaults to None.
        comment (str | None, optional): Key comment passed to
            ``ssh-keygen -C`` (typically ``user@host``). Defaults to None.
        passphrase (str | None, optional): Accepted for Ansible
            ``ssh_key_passphrase`` call-site parity, but passphrase-protected
            generation is out of scope: any non-empty value raises
            ``ValueError`` while materializing, because no secret may ride a
            plan or a command log (the ``gpg.gen_key`` ``%no-protection``
            precedent). ``None`` or ``""`` generates an unprotected key.
            Defaults to None.
        force (bool, optional): Regenerate the pair when one already exists
            (both halves are removed first). Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The ``.ssh`` directory setup commands and the one
            ``ssh-keygen`` generation command -- only where the facts show a
            delta.

    **Raises:**

    ```python
    HostConnectionError, OperationError, PrivilegeError, ValueError
    ```

    Examples:
        ```python
        from reemote import op, server

        # Give the deploy user an unprotected ed25519 pair (writes another user's ~/.ssh, so elevate)
        yield op(
            server.ssh_keypair,
            name="Create the deploy key pair",
            user="deploy",
            type="ed25519",
            sudo=True,
        )

        # The rsa default with a comment; the key stays unprotected, because
        # a passphrase is refused rather than spooled or passed on a command
        yield op(
            server.ssh_keypair,
            name="Create the backup key pair",
            user="backup",
            comment="backup@web01.example.com",
            sudo=True,
        )
        ```

        Examples can be found in
        ``examples/modules/server/server_ssh_keypair.py``.
    """

    # Mirrors the Ansible ``user`` module's ``generate_ssh_key``/
    # ``generate_ssh_key_file``/``ssh_key_type``/``ssh_key_bits``/
    # ``ssh_key_comment``/``ssh_key_passphrase``/``ssh_key_force`` set, as its
    # reemote port asked for in issue #60 (the ansible-users migration's
    # GAP-2). Idempotence rides the ``SshKeyPair`` fact: a public half with a
    # fingerprint needs nothing (unless ``force``), and the re-read is
    # elevated-fault-tolerant, so a ``700`` ``.ssh`` owned by the target user
    # -- the state this operation itself creates -- still reads. The
    # directory half is the ``user_authorized_keys`` ensure block verbatim
    # (``_KeysPathStat`` probe, ``_HomeMkdirCommand``/``_KeysChmodCommand``
    # 700/``_HomeChownCommand`` pair, wrong-typed path raises
    # ``OperationError``); the key files themselves need no state verbs here,
    # because ``ssh-keygen`` is run as the target user through the elevated
    # path -- ``su -s /bin/sh <user>`` inside one ``sudo sh -c`` chain -- so
    # the pair is born with the right owner and mode (``-f`` writes 600, the
    # public half 644 under the user's umask) instead of being fixed up.
    #
    # The ``Home`` fact answers ``None`` for a user the host does not know,
    # which reads as an ``OperationError`` naming the user before any command
    # is planned (the ``user_authorized_keys`` guard, enforced identically);
    # an explicit ``path`` bypasses the Home read entirely.
    #
    # ``passphrase`` follows the ``gpg.gen_key`` precedent instead of the
    # spool-and-install road: passphrase-protected generation is deliberately
    # out of scope, because a secret must never ride a plan, a command log or
    # a ``/tmp`` file on a multi-tenant host. Rather than silently ignoring the
    # option -- which would leave the caller believing the key is protected --
    # a non-empty value raises here, before any fact is read or command
    # planned, and the message never quotes the secret itself. Ansible call
    # sites that pass ``ssh_key_passphrase`` therefore fail fast and visibly;
    # unprotected generation (``None`` or ``""``, and everything the Ansible
    # ``user`` role examples do) is unaffected.
    #
    # A key pair with an unreadable private half is never detected here -- the
    # public half's fingerprint is the identity Ansible itself checks
    # (``os.path.exists`` on the public path).
    #
    # ``force`` is passed through to the descriptor unconditionally: the
    # ``rm -f`` prefix is harmless when nothing exists, so the request never
    # branches on the fact for the command shape -- only for whether a
    # generation is planned at all. ``**kwargs`` absorbs the baseline's
    # global arguments (``op()`` already strips ``name``/``sudo``/``su``).
    if passphrase:
        raise ValueError(
            "ssh_keypair cannot generate a passphrase-protected key: no secret "
            "may ride a plan or a command log, so passphrase support is out of "
            "scope (drop the passphrase argument for an unprotected pair)"
        )
    if path is None:
        home = await ctx.facts.get(Home, user=user)
        if home is None:
            raise OperationError(f"cannot resolve a home directory for user {user!r}")
        private_path = f"{home}/.ssh/id_{type}"
    else:
        private_path = path

    public_path = f"{private_path}.pub"
    key_directory = str(PurePosixPath(private_path).parent)

    reasons: list[str] = []

    dir_info = await ctx.facts.get(_KeysPathStat, key_directory)
    if dir_info is False:
        raise OperationError(f"{key_directory} exists and is not a directory")
    if dir_info is None:
        ctx.changed = True
        reasons.append(f"{key_directory} missing, will create")
        yield _HomeMkdirCommand(path=key_directory)
        yield _KeysChmodCommand(path=key_directory, mode=_SSH_DIR_MODE)
        yield _HomeChownCommand(path=key_directory, user=user, group=user)
    else:
        if dir_info["mode"] != _SSH_DIR_MODE:
            ctx.changed = True
            reasons.append(
                f"mode {dir_info['mode']} differs, will chmod {_SSH_DIR_MODE}"
            )
            yield _KeysChmodCommand(path=key_directory, mode=_SSH_DIR_MODE)
        if dir_info["user"] != user or dir_info["group"] != user:
            ctx.changed = True
            reasons.append(
                f"owner {dir_info['user']}:{dir_info['group']} differs, will chown"
            )
            yield _HomeChownCommand(path=key_directory, user=user, group=user)
        if not ctx.changed:
            reasons.append(f"directory {key_directory} already exists")

    fingerprint = await ctx.facts.get(SshKeyPair, user, path=public_path)
    if fingerprint is not None and not force:
        if not ctx.changed:
            reasons.append(f"key pair {public_path} already exists")
        ctx.reason = "; ".join(reasons)
        return

    if fingerprint is None:
        reasons.append(f"{public_path} missing, will generate")
    else:
        reasons.append(f"key pair {public_path} exists, will regenerate")
    ctx.changed = True

    yield SshKeyPairGenerateCommand(
        user=user,
        private_path=private_path,
        public_path=public_path,
        key_type=type,
        size=size,
        comment=comment,
        force=force,
    )

    ctx.reason = "; ".join(reasons)


@dataclass(frozen=True)
class SetHostnameCommand(Command):
    """One hostname-setting invocation.

    With ``hostnamectl`` it renders ``hostnamectl set-hostname <hostname>``
    (the systemd form, which also persists the name); without it the bare
    ``hostname <hostname>`` command for older systems. The hostname is
    ``shlex``-quoted (the baseline's ``QuoteString``), so a name carrying shell
    metacharacters is set literally rather than executed.
    """

    hostname: str
    hostnamectl: bool = False

    def shell(self) -> str:
        if self.hostnamectl:
            return f"hostnamectl set-hostname {shlex.quote(self.hostname)}"
        return f"hostname {shlex.quote(self.hostname)}"


async def hostname(
    ctx: OpContext,
    hostname: str,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Set the system hostname.

    Args:
        ctx (OpContext): Execution context for the operation.
        hostname (str): The hostname that should be set.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The hostname-setting command, when the name differs.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        ```python
        from reemote import op, server

        # Set the system hostname (needs root, so elevate with sudo)
        yield op(
            server.hostname,
            name="Set the web server hostname",
            hostname="web01.example.com",
            sudo=True,
        )
        ```

        Examples can be found in ``examples/modules/server/server_op_hostname.py``.
    """

    # Mirrors the baseline's ``server.hostname``. Idempotent: the requested name is
    # compared against the ``Hostname`` fact and a ``SetHostnameCommand`` is
    # yielded only when they differ. The ``Which`` fact picks the command form
    # -- ``hostnamectl set-hostname`` when ``hostnamectl`` is available (it
    # persists the name itself), the bare ``hostname`` command on older systems
    # without it. The command needs root: flag the request ``sudo=True``.
    #
    # The baseline's ``hostname_file`` option is deliberately dropped: on systems
    # without ``hostnamectl`` the baseline also uploads the name to a persistent
    # file (``/etc/hostname`` on Linux, ``/etc/myname`` on OpenBSD) through
    # ``files.put``, which the baseline can elevate -- reemote's transfers are
    # always unprivileged, so that root-owned write cannot ride a transfer
    # and the whole file branch (option included) is omitted rather than
    # silently run as the login user. On a systemd host ``hostnamectl``
    # already persists the name, so nothing is lost there. ``**kwargs``
    # absorbs any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    current = await ctx.facts.get(Hostname)
    has_hostnamectl = await ctx.facts.get(Which, "hostnamectl") is not None

    if current == hostname:
        ctx.reason = "hostname is set"
    else:
        ctx.changed = True
        ctx.reason = f"hostname is {current!r}, will set {hostname!r}"
        yield SetHostnameCommand(hostname=hostname, hostnamectl=has_hostnamectl)


def _hosts_spool_path(dest: str) -> str:
    """The deterministic ``/tmp`` spool *file* path ``etc_hosts`` uploads to.

    The content is spooled to a fixed ``hosts`` file *inside* a sha1-derived
    ``/tmp`` directory rather than at the top level of ``/tmp``; the elevated
    install ``rm -rf``s the whole directory afterwards.

    Derived from the managed hosts file's sha1 (``files.install``'s
    ``_install_spool_path`` / ``_keys_spool_path`` pattern), even though most
    requests edit the one ``/etc/hosts``: the ``path`` option means two
    requests in one deploy can target different hosts files, and they must
    not clobber each other's spool. The same ``dest`` always yields the same
    name in both phases, so the descriptors stay deterministic. The name
    avoids *collisions*, not a hostile local user: it is predictable, so on
    a multi-tenant host the ``/tmp`` spool is still a symlink-race exposure
    (the trade-off every spool-and-install operation here makes).
    """
    digest = hashlib.sha1(dest.encode()).hexdigest()[:16]
    return f"/tmp/reemote-server-etc-hosts-{digest}/hosts"


def _hosts_content(
    current: str | None, ip: str, hostnames: tuple[str, ...], present: bool
) -> str:
    """The pure hosts-file rewrite: ensure or drop the line keyed by ``ip``.

    The baseline's ``server.etc_hosts`` line loop verbatim, functional on the
    controller: with ``present=True`` every line whose first token is ``ip``
    becomes exactly ``<ip> <hostnames...>`` (appended when the file has no
    such line); with ``present=False`` and ``hostnames`` given only those
    names are dropped from the IP's lines (a line with no remaining names is
    removed entirely), and with no ``hostnames`` every line for ``ip`` goes.
    Comments on an *edited* line are not preserved (baseline parity); every
    other line, comment and blank line survives in order. Line endings are
    normalised to ``\\n`` and the result carries one trailing newline unless
    it is empty -- the baseline's own ``"\\n".join`` + conditional suffix, so a
    removal that empties the file yields ``""``. Idempotent
    (``f(f(x)) == f(x)``) and side-effect free, so both deploy phases compute
    the same content from the same fact value.
    """
    new_lines: list[str] = []
    found = False
    for line in current.splitlines() if current else []:
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            new_lines.append(line)
            continue
        tokens = stripped.split()
        if tokens[0] != ip:
            new_lines.append(line)
            continue
        found = True
        line_names = tokens[1:]
        if present:
            new_lines.append(f"{ip} {' '.join(hostnames)}")
        elif hostnames:
            remaining = [name for name in line_names if name not in hostnames]
            if remaining:
                new_lines.append(f"{ip} {' '.join(remaining)}")
            # else: no names remain, drop the line entirely
        # else: full removal, drop the line
    if present and not found:
        new_lines.append(f"{ip} {' '.join(hostnames)}")
    new_content = "\n".join(new_lines)
    if new_content:
        new_content += "\n"
    return new_content


@dataclass(frozen=True)
class EtcHostsUploadCommand(TransferCommand):
    """Spool the transformed hosts-file content to a ``/tmp`` path on the target.

    The first half of ``etc_hosts``' write and a *non-shell* descriptor: it
    copies the content over the connection's scp channel via
    ``Host.upload_file`` rather than running shell text, so it has no
    ``shell()`` and its loggable form is the ``__str__`` rendering
    ``command_shell`` falls back to. Like ``AuthorizedKeysUploadCommand`` the
    content is not a caller-supplied stream but the whole hosts-file text
    the operation just derived from the file's current content, so the
    field is a plain ``str`` -- hashable and identical across phases --
    wrapped in a fresh ``io.BytesIO`` only at transfer time. The copy rides
    as the login user (transfers are unprivileged, so the request's ``sudo``
    does not apply to it), which is why the spool path is world-writable
    ``/tmp`` rather than root-owned ``/etc``.
    """

    content: str  # the whole transformed hosts-file text
    dest: str  # spool path on the target host

    def __str__(self) -> str:
        return f"scp <hosts file> -> {self.dest}"

    async def transfer(self, host: Host) -> None:
        # scp does not create parent directories, and the spool now lives
        # inside a sha1-derived /tmp directory rather than at the top level,
        # so mkdir -p it first. Unprivileged: /tmp is world-writable, so the
        # login user can create the directory, and a transfer never elevates.
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        await host.run_shell(f"mkdir -p {parent}")
        await host.upload_file(io.BytesIO(self.content.encode()), self.dest)


@dataclass(frozen=True)
class EtcHostsInstallCommand(Command):
    """Install the spooled hosts file ``0644``, then remove the spool file.

    The second half of ``etc_hosts``' write and the elevated one: the whole
    sequence is wrapped in ``sh -c`` -- a bare ``sudo`` prefix would elevate
    only the first command of the ``&&`` chain and leave the cleanup running
    as the login user, whereas ``sudo sh -c '...'`` runs every step elevated,
    which can always remove the login-user spool file. The sequence ensures
    the destination directory, copies the spooled content over the hosts
    file, chmods it ``0644`` (the ``AptRepoInstallCommand`` pattern) and
    removes the spool. No chown is needed: ``/etc/hosts`` is root-owned and
    ``cp`` over an existing file preserves its owner; a freshly created file
    is root-owned already because the whole chain runs under ``sudo``. An
    empty ``content`` upstream is a legitimate payload (a removal that
    empties the file), so nothing here special-cases it. All paths are
    shell-quoted.
    """

    spool: str  # where EtcHostsUploadCommand wrote the content
    dest: str  # the hosts-file path being managed

    def shell(self) -> str:
        spool = shlex.quote(self.spool)
        # The spool file lives inside a sha1-derived /tmp directory; remove
        # the whole directory so no leftover lands in /tmp.
        spool_dir = shlex.quote(str(PurePosixPath(self.spool).parent))
        dest = shlex.quote(self.dest)
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        inner = (
            f"mkdir -p {parent} && "
            f"cp {spool} {dest} && "
            f"chmod 0644 {dest} && rm -rf {spool_dir}"
        )
        return f"sh -c {shlex.quote(inner)}"


async def etc_hosts(
    ctx: OpContext,
    ip: str,
    hostnames: str | list[str] | tuple[str, ...] | None = None,
    present: bool = True,
    path: str = "/etc/hosts",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add, update or remove an entry in ``/etc/hosts`` keyed by IP address.

    Args:
        ctx (OpContext): Execution context for the operation.
        ip (str): The IP address the entry is keyed by.
        hostnames (str | list[str] | tuple[str, ...] | None, optional): The
            hostname(s) to associate with ``ip``; a single string (split on
            whitespace) or a list/tuple of names. Prefer tuples when
            building requests via ``op()``, since ``OperationRequest``
            kwargs must stay hashable. Required when ``present=True``;
            with ``present=False`` only the named hostnames are dropped,
            and omitting it removes every line for ``ip``. Defaults to None.
        present (bool, optional): Whether the entry should be present or
            absent. Defaults to True.
        path (str, optional): Path to the hosts file. Defaults to
            ``/etc/hosts``.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The spool-and-install pair carrying the new hosts-file
            content, when the entry differs from the request.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        ```python
        from reemote import op, server

        # Add/update the /etc/hosts line keyed by an IP (root-owned file, so elevate with sudo)
        yield op(
            server.etc_hosts,
            name="Map the db host",
            ip="10.0.0.5",
            hostnames=("db.internal", "db"),
            sudo=True,
        )

        # Drop just one hostname from the IP's line
        yield op(
            server.etc_hosts,
            name="Retire the old alias",
            ip="10.0.0.5",
            hostnames=("db-old",),
            present=False,
            sudo=True,
        )
        ```

        Examples can be found in ``examples/modules/server/server_etc_hosts.py``.
    """

    # Mirrors the baseline's ``server.etc_hosts`` for ``ip``, ``hostnames``,
    # ``present`` and ``path``. Idempotent: the parsed ``EtcHosts`` fact is
    # compared first and the baseline's three no-op reasons survive verbatim
    # ("already present in", "already absent from", "does not reference any
    # of:"); the present comparison is order-sensitive, so a mere reordering
    # of the hostnames rewrites the line. When a change is due, the
    # namespace's raw-content fact ``AuthorizedKeysFile`` supplies the file's
    # current text (reused, never a cross-namespace ``files.FileContents``
    # import) and the pure ``_hosts_content`` transform computes the new
    # whole content, preserving every untouched line, comment and blank
    # line; comments on the edited line are dropped, baseline parity. A
    # content-level fixpoint guard no-ops when the transform's output equals
    # the current text even though the fact stage wanted an edit.
    #
    # The write is spool-and-install, not the baseline's elevated ``files.put``:
    # reemote transfers never elevate, so one ``EtcHostsUploadCommand``
    # scp's the text to a ``/tmp`` spool whose name is derived from the
    # managed path (two requests targeting different hosts files never share
    # one spool), then one ``EtcHostsInstallCommand`` overlays the hosts file
    # with it under ``sh -c`` with a ``chmod 0644`` reset. The transfer stays
    # unprivileged regardless of ``sudo`` -- only the install elevates --
    # which is the two-privilege-level signature of the family. ``/etc/hosts``
    # is root-owned: flag the request ``sudo=True``. This operation replaces
    # the earlier two-run ``files.edit`` -> ``files.move`` example recipe
    # (developer decision): the fact read, transform and install now live in
    # one operation, so a single deploy suffices.
    #
    # The baseline's ``OperationError`` for a missing ``hostnames`` becomes
    # ``ValueError`` with the same message (the house parameter-validation
    # style). ``**kwargs`` absorbs any further global arguments (``op()``
    # already strips ``name``/``sudo``/``su``).
    if isinstance(hostnames, str):
        hostnames_list = hostnames.split()
    elif hostnames is None:
        hostnames_list = []
    else:
        hostnames_list = list(hostnames)

    if present and not hostnames_list:
        raise ValueError("hostnames must be provided when present=True")

    current_entries = await ctx.facts.get(EtcHosts, path)
    current_names = current_entries.get(ip)

    if present:
        if current_names == hostnames_list:
            ctx.reason = f"{ip} -> {' '.join(hostnames_list)} already present in {path}"
            return
    else:
        if current_names is None:
            ctx.reason = f"{ip} already absent from {path}"
            return
        if hostnames_list and not any(name in current_names for name in hostnames_list):
            ctx.reason = (
                f"{ip} in {path} does not reference any of: {' '.join(hostnames_list)}"
            )
            return

    current = await ctx.facts.get(AuthorizedKeysFile, path)
    new = _hosts_content(current, ip, tuple(hostnames_list), present)
    if new == (current or ""):
        # The fact wanted an edit the file's own text says is unnecessary.
        ctx.reason = f"{ip} entry already written in {path}"
        return

    ctx.changed = True
    spool = _hosts_spool_path(path)
    ctx.reason = (
        f"{ip} -> {' '.join(hostnames_list)} will be written to {path}"
        if present
        else f"{ip} will be removed from {path}"
    )
    yield EtcHostsUploadCommand(content=new, dest=spool)
    yield EtcHostsInstallCommand(spool=spool, dest=path)


@dataclass(frozen=True)
class KillCommand(Command):
    """One ``kill -<signal> <pid>`` invocation.

    Both the signal flag and the PID are ``shlex``-quoted (the baseline's
    ``QuoteString``), so a signal string carrying shell metacharacters is
    passed to ``kill`` literally rather than executed by the shell.
    """

    pid: int
    signal: str = "TERM"

    def shell(self) -> str:
        return f"kill {shlex.quote(f'-{self.signal}')} {shlex.quote(str(self.pid))}"


async def kill(
    ctx: OpContext,
    pid: int,
    signal: str = "TERM",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Kill a running process.

    Args:
        ctx (OpContext): Execution context for the operation.
        pid (int): PID of the process to kill.
        signal (str, optional): Signal to send. Defaults to ``TERM``.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The kill command, always (the operation is stateless).

    **Raises:**

    ```python
    None
    ```

    Examples:
        ```python
        from reemote import op, server

        # Send SIGTERM to a PID (discover it first via gather_fact(Processes); killing another user's process needs sudo)
        yield op(
            server.kill,
            name="Stop the runaway worker",
            pid=4321,
            sudo=True,
        )

        # Force-kill with SIGKILL (stateless: a repeat request kills again)
        yield op(
            server.kill,
            name="Force-kill the worker",
            pid=4321,
            signal="KILL",
            sudo=True,
        )
        ```

        Examples can be found in ``examples/modules/server/server_kill.py``.
    """

    # Mirrors the baseline's ``server.kill``. Stateless and non-idempotent, exactly
    # like the baseline: no fact is read and a ``KillCommand`` is always yielded, so
    # the plan always shows a change and a repeat request kills again.
    # Discovering the PID is the caller's job -- read the ``Processes`` fact
    # via ``gather_fact`` BEFORE the run and close over the frozen snapshot in
    # a deploy factory (see ``examples/modules/server/server_kill.py``);
    # branching the deploy generator itself on live process state would break
    # the determinism guard. Killing another user's process needs root: flag
    # the request ``sudo=True``. A PID that exited between the snapshot and the
    # apply makes ``kill`` exit non-zero, which fails the run with
    # ``CommandError`` (the baseline behaves the same). ``**kwargs`` absorbs
    # any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    ctx.changed = True
    ctx.reason = f"kill process {pid} with signal {signal}"
    yield KillCommand(pid=pid, signal=signal)


#: The baseline's hard-coded locale definitions file: the ``locales`` package's
#: ``/etc/locale.gen``, which exists only on Debian-family hosts. RHEL-family
#: systems install locales through ``glibc-langpack-*``/``localedef`` and have
#: no such file, so a request there raises ``OperationError`` (the baseline's own
#: behaviour -- its ``FindInFile`` grep finds no matching line either).
_LOCALE_GEN_PATH = "/etc/locale.gen"


def _locale_spool_path(dest: str) -> str:
    """The deterministic ``/tmp`` spool *file* path ``locale`` uploads to.

    The content is spooled to a fixed ``locale`` file *inside* a sha1-derived
    ``/tmp`` directory rather than at the top level of ``/tmp``; the elevated
    install ``rm -rf``s the whole directory afterwards.

    Derived from the managed definitions file's sha1 (``files.install``'s
    ``_install_spool_path`` / ``_keys_spool_path`` pattern), even though the
    operation always edits the one ``/etc/locale.gen``: the derived name is
    deterministic either way and matches every sibling conversion. The same
    ``dest`` always yields the same name in both phases, so the descriptors
    stay deterministic. The name avoids *collisions*, not a hostile local
    user: it is predictable, so on a multi-tenant host the ``/tmp`` spool is
    still a symlink-race exposure (the trade-off every spool-and-install
    operation here makes).
    """
    digest = hashlib.sha1(dest.encode()).hexdigest()[:16]
    return f"/tmp/reemote-server-locale-{digest}/locale"


def _locale_edit(content: str | None, locale: str, present: bool) -> str:
    """The pure locale.gen edit: comment out or uncomment the locale's line.

    Mirrors the baseline's ``server.locale`` body: the definition line is the one
    matching ``<locale>`` followed by whitespace (the baseline's grep
    ``^.*{locale}[[:space:]]\\+.*$``, with the locale escaped literally here
    -- ``re.escape`` -- where the baseline interpolates it raw and a ``.`` would
    match any character; the POSIX ``[[:space:]]`` class is translated
    verbatim), every other line is preserved in order, and zero or multiple
    matches raise ``OperationError`` with the baseline's messages -- including an
    absent file (``None`` content), which the baseline's ``FindInFile`` reports as
    no matches.

    ``present=True`` strips every ``"# "`` occurrence from the line
    (the baseline's ``str.replace`` verbatim, so a doubly commented line is fully
    enabled in one edit); ``present=False`` prepends ``"# "`` unless the line
    is already commented -- the fixpoint guard the baseline's unconditional
    prepend lacks, so a stale locale archive can never double the comment.
    The file's own trailing-newline state is preserved. Idempotent
    (``f(f(x)) == f(x)``) and side-effect free, so both deploy phases compute
    the same content from the same fact value.
    """
    pattern = re.compile(rf"{re.escape(locale)}[ \t\r\f\v]+")
    lines = (content or "").splitlines()
    matches = [i for i, line in enumerate(lines) if pattern.search(line)]
    if not matches:
        raise OperationError(f"Locale {locale} not found in {_LOCALE_GEN_PATH}")
    if len(matches) > 1:
        raise OperationError(
            f"Multiple locales matches for {locale} in {_LOCALE_GEN_PATH}"
        )
    index = matches[0]
    line = lines[index]
    if present:
        lines[index] = line.replace("# ", "")
    elif not line.lstrip().startswith("#"):
        lines[index] = f"# {line}"
    text = "\n".join(lines)
    if content and content.endswith("\n"):
        text += "\n"
    return text


@dataclass(frozen=True)
class LocaleUploadCommand(TransferCommand):
    """Spool the transformed locale.gen content to a ``/tmp`` path on the target.

    The first half of ``locale``'s write and a *non-shell* descriptor: it
    copies the content over the connection's scp channel via
    ``Host.upload_file`` rather than running shell text, so it has no
    ``shell()`` and its loggable form is the ``__str__`` rendering
    ``command_shell`` falls back to. Like ``EtcHostsUploadCommand`` the
    content is not a caller-supplied stream but the whole definitions-file
    text the operation just derived from the file's current content, so the
    field is a plain ``str`` -- hashable and identical across phases --
    wrapped in a fresh ``io.BytesIO`` only at transfer time. The copy rides
    as the login user (transfers are unprivileged, so the request's ``sudo``
    does not apply to it), which is why the spool path is world-writable
    ``/tmp`` rather than root-owned ``/etc``.
    """

    content: str  # the whole transformed locale.gen text
    dest: str  # spool path on the target host

    def __str__(self) -> str:
        return f"scp <locale.gen> -> {self.dest}"

    async def transfer(self, host: Host) -> None:
        # scp does not create parent directories, and the spool now lives
        # inside a sha1-derived /tmp directory rather than at the top level,
        # so mkdir -p it first. Unprivileged: /tmp is world-writable, so the
        # login user can create the directory, and a transfer never elevates.
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        await host.run_shell(f"mkdir -p {parent}")
        await host.upload_file(io.BytesIO(self.content.encode()), self.dest)


@dataclass(frozen=True)
class LocaleInstallCommand(Command):
    """Install the spooled locale.gen ``0644``, then remove the spool file.

    The second half of ``locale``'s write and the elevated one: the whole
    sequence is wrapped in ``sh -c`` -- a bare ``sudo`` prefix would elevate
    only the first command of the ``&&`` chain and leave the cleanup running
    as the login user, whereas ``sudo sh -c '...'`` runs every step elevated,
    which can always remove the login-user spool file. The sequence ensures
    the destination directory, copies the spooled content over the
    definitions file, chmods it ``0644`` (the ``EtcHostsInstallCommand``
    pattern) and removes the spool. No chown is needed: ``/etc/locale.gen``
    is root-owned and ``cp`` over an existing file preserves its owner; a
    freshly created file is root-owned already because the whole chain runs
    under ``sudo``. All paths are shell-quoted.
    """

    spool: str  # where LocaleUploadCommand wrote the content
    dest: str  # the locale.gen path being managed

    def shell(self) -> str:
        spool = shlex.quote(self.spool)
        # The spool file lives inside a sha1-derived /tmp directory; remove
        # the whole directory so no leftover lands in /tmp.
        spool_dir = shlex.quote(str(PurePosixPath(self.spool).parent))
        dest = shlex.quote(self.dest)
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        inner = (
            f"mkdir -p {parent} && "
            f"cp {spool} {dest} && "
            f"chmod 0644 {dest} && rm -rf {spool_dir}"
        )
        return f"sh -c {shlex.quote(inner)}"


@dataclass(frozen=True)
class LocaleGenCommand(Command):
    """One ``locale-gen`` regeneration of the installed locales.

    The third descriptor ``locale`` yields, the baseline's bare ``locale-gen``
    step after either edit: the tool rebuilds the system locale archive from
    every uncommented definition line, so it takes no argument and the
    rendering is the constant ``locale-gen``. The ``locale``/``present``
    fields carry the request's own delta for the simulated seam (which
    models only that locale's addition/removal, not a full archive rebuild)
    and for plan readability; they never reach the shell. The command needs
    root, so it rides the request's ``sudo`` like the install does.
    """

    locale: str  # the locale whose definition line was just edited
    present: bool  # whether it was enabled (True) or disabled (False)

    def shell(self) -> str:
        return "locale-gen"


async def locale(
    ctx: OpContext,
    locale: str,
    present: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Enable or disable a locale in ``/etc/locale.gen`` (Debian-family).

    Args:
        ctx (OpContext): Execution context for the operation.
        locale (str): Name of the locale to enable/disable (e.g.
            ``fr_FR.UTF-8``), as spelled in ``/etc/locale.gen``.
        present (bool, optional): Whether the locale should be enabled.
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The spool-and-install pair carrying the new locale.gen
            content (when the definition line changes), followed by one
            ``locale-gen`` regeneration.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, OperationError
    ```

    Examples:
        ```python
        from reemote import op, server

        # Enable a locale in /etc/locale.gen and regenerate (root-owned, so elevate with sudo)
        yield op(
            server.locale,
            name="Enable French (France)",
            locale="fr_FR.UTF-8",
            sudo=True,
        )

        # Disable a locale
        yield op(
            server.locale,
            name="Disable German (Germany)",
            locale="de_DE.UTF-8",
            present=False,
            sudo=True,
        )
        ```

        Examples can be found in ``examples/modules/server/server_op_locale.py``.
    """

    # Mirrors the baseline's ``server.locale`` for ``locale`` and ``present``.
    # Idempotent: the ``Locales`` fact (``locale -a``, normalized to ``UTF-8``
    # spellings) is the baseline's change condition -- enable only when the locale
    # is absent, disable only when it is present. The definition-line lookup
    # runs BEFORE that gate, exactly like the baseline's ``FindInFile`` read, so a
    # missing or ambiguous line raises ``OperationError`` with the baseline's
    # messages even for an already-in-state request (and on a RHEL-family
    # host with no ``/etc/locale.gen`` at all). The lookup is a Python regex
    # over the ``AuthorizedKeysFile`` raw-content fact -- the namespace's
    # path-generic reader, reused never a cross-namespace ``files.FileContents``
    # import -- with the locale escaped literally where the baseline interpolates
    # it raw into a grep pattern.
    #
    # The write is spool-and-install, not the baseline's elevated sed
    # (``files.line``/``files.replace``): reemote transfers never elevate,
    # so one ``LocaleUploadCommand`` scp's the transformed text to a ``/tmp``
    # spool whose name is derived from the managed path, then one
    # ``LocaleInstallCommand`` overlays ``/etc/locale.gen`` with it under
    # ``sh -c`` with a ``chmod 0644`` reset. The transfer stays unprivileged
    # regardless of ``sudo`` -- only the install and the regeneration elevate
    # -- which is the two-privilege-level signature of the family. The baseline's
    # timestamped sed backup is gone with the sed (its timestamp is
    # non-deterministic across phases). This operation replaces the earlier
    # two-run ``files.edit`` -> ``files.move`` example recipe (developer
    # decision): the fact reads, transform, install and regeneration now live
    # in one operation, so a single deploy suffices.
    #
    # ``LocaleGenCommand`` always follows when the gate wants a change --
    # The baseline's "locale-gen is always executed" semantics. The content-level
    # fixpoint guard only skips the spool pair: a definition line already in
    # the desired state (uncommented but not yet generated, or commented
    # while the archive still lists the locale) needs no write, but the
    # regeneration is still planned. The disable direction's ``"# "`` prepend
    # is guarded against double-commenting (the fixpoint the baseline's
    # unconditional prepend lacks); the enable direction's ``str.replace``
    # of every ``"# "`` is the baseline verbatim. ``/etc/locale.gen`` is root-owned
    # and ``locale-gen`` writes the system archive: flag the request
    # ``sudo=True``. ``**kwargs`` absorbs any further global arguments (``op()``
    # already strips ``name``/``sudo``/``su``).
    installed = await ctx.facts.get(Locales)
    current = await ctx.facts.get(AuthorizedKeysFile, _LOCALE_GEN_PATH)
    new = _locale_edit(current, locale, present)

    if (locale in installed) == present:
        ctx.reason = f"locale {locale} already {'enabled' if present else 'disabled'}"
        return

    ctx.changed = True
    reasons: list[str] = []
    if new != (current or ""):
        spool = _locale_spool_path(_LOCALE_GEN_PATH)
        yield LocaleUploadCommand(content=new, dest=spool)
        yield LocaleInstallCommand(spool=spool, dest=_LOCALE_GEN_PATH)
        reasons.append(
            f"will {'enable' if present else 'disable'} {locale} in {_LOCALE_GEN_PATH}"
        )
    else:
        # The fact wanted a change the file's own text says is unnecessary:
        # the definition line is already in the desired state, so only the
        # regeneration below is due.
        reasons.append(
            f"{locale} definition line already "
            f"{'uncommented' if present else 'commented'} in {_LOCALE_GEN_PATH}"
        )
    yield LocaleGenCommand(locale=locale, present=present)
    reasons.append("will regenerate the installed locales with locale-gen")
    ctx.reason = "; ".join(reasons)


#: The baseline's hard-coded limits file: ``pam_limits`` reads
#: ``/etc/security/limits.conf`` as every user, so it must stay root-owned
#: ``0644`` -- the reason the install resets the mode rather than moving the
#: login-user spool over it.
_SECURITY_LIMITS_PATH = "/etc/security/limits.conf"


def _security_limit_spool_path(dest: str) -> str:
    """The deterministic ``/tmp`` spool *file* path ``security_limit`` uploads to.

    The content is spooled to a fixed ``security-limit`` file *inside* a
    sha1-derived ``/tmp`` directory rather than at the top level of ``/tmp``;
    the elevated install ``rm -rf``s the whole directory afterwards.

    Derived from the managed limits file's sha1 (``files.install``'s
    ``_install_spool_path`` / ``_keys_spool_path`` pattern), even though the
    operation always edits the one ``/etc/security/limits.conf``: the derived
    name is deterministic either way and matches every sibling conversion.
    The same ``dest`` always yields the same name in both phases, so the
    descriptors stay deterministic. The name avoids *collisions*, not a
    hostile local user: it is predictable, so on a multi-tenant host the
    ``/tmp`` spool is still a symlink-race exposure (the trade-off every
    spool-and-install operation here makes).
    """
    digest = hashlib.sha1(dest.encode()).hexdigest()[:16]
    return f"/tmp/reemote-server-security-limit-{digest}/security-limit"


def _security_limit_edit(
    content: str | None,
    domain: str,
    limit_type: str,
    item: str,
    value: int | str,
) -> str:
    """Pure limits.conf edit: ensure the ``domain type item value`` line.

    Mirrors the ``files.line`` call baseline's ``server.security_limit``
    makes: search ``^{domain}[[:space:]]+{limit_type}[[:space:]]+{item}.*$``
    (anchored, value NOT included), replacement the tab-formatted
    ``domain\\tlimit_type\\titem\\tvalue``. Every matching line is
    normalized in place -- a stale value line MATCHES and is corrected,
    and an item prefix like ``nofilex`` matches too (the baseline's ``.*$``); a
    commented-out line does not (the anchor). With no match the line is
    appended (an absent file, ``None`` content, reads as empty, the baseline's
    echo creating it). The fields are escaped literally (``re.escape``),
    the documented hardening over the baseline's raw BRE interpolation; the
    POSIX ``[[:space:]]`` class is translated verbatim minus the newline
    (matching is per line). The result always ends in one trailing
    newline and the edit is a fixpoint (``f(f(x)) == f(x)``), side-effect
    free, so both deploy phases compute the same content from the same
    fact value.
    """
    pattern = re.compile(
        rf"^{re.escape(domain)}[ \t\r\f\v]+{re.escape(limit_type)}"
        rf"[ \t\r\f\v]+{re.escape(item)}.*$"
    )
    replace = f"{domain}\t{limit_type}\t{item}\t{value}"
    lines = (content or "").splitlines()
    matches = [i for i, line in enumerate(lines) if pattern.search(line)]
    if not matches:
        lines.append(replace)
    else:
        for i in matches:
            lines[i] = replace
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class SecurityLimitUploadCommand(TransferCommand):
    """Spool the transformed limits.conf content to a ``/tmp`` path on the target.

    The first half of ``security_limit``'s write and a *non-shell* descriptor:
    it copies the content over the connection's scp channel via
    ``Host.upload_file`` rather than running shell text, so it has no
    ``shell()`` and its loggable form is the ``__str__`` rendering
    ``command_shell`` falls back to. Like ``LocaleUploadCommand`` the content
    is not a caller-supplied stream but the whole limits-file text the
    operation just derived from the file's current content, so the field is
    a plain ``str`` -- hashable and identical across phases -- wrapped in a
    fresh ``io.BytesIO`` only at transfer time. The copy rides as the login
    user (transfers are unprivileged, so the request's ``sudo`` does not
    apply to it), which is why the spool path is world-writable ``/tmp``
    rather than root-owned ``/etc/security``.
    """

    content: str  # the whole transformed limits.conf text
    dest: str  # spool path on the target host

    def __str__(self) -> str:
        return f"scp <limits.conf> -> {self.dest}"

    async def transfer(self, host: Host) -> None:
        # scp does not create parent directories, and the spool now lives
        # inside a sha1-derived /tmp directory rather than at the top level,
        # so mkdir -p it first. Unprivileged: /tmp is world-writable, so the
        # login user can create the directory, and a transfer never elevates.
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        await host.run_shell(f"mkdir -p {parent}")
        await host.upload_file(io.BytesIO(self.content.encode()), self.dest)


@dataclass(frozen=True)
class SecurityLimitInstallCommand(Command):
    """Install the spooled limits.conf ``0644``, then remove the spool file.

    The second half of ``security_limit``'s write and the elevated one: the
    whole sequence is wrapped in ``sh -c`` -- a bare ``sudo`` prefix would
    elevate only the first command of the ``&&`` chain and leave the cleanup
    running as the login user, whereas ``sudo sh -c '...'`` runs every step
    elevated, which can always remove the login-user spool file. The
    sequence ensures the destination directory, copies the spooled content
    over the limits file, chmods it ``0644`` (the ``LocaleInstallCommand``
    pattern) and removes the spool. No chown is needed:
    ``/etc/security/limits.conf`` is root-owned and ``cp`` over an existing
    file preserves its owner; a freshly created file is root-owned already
    because the whole chain runs under ``sudo``. The mode reset matters:
    ``pam_limits`` reads the file as every user, so a login-user mode would
    silently stop the limits applying. All paths are shell-quoted.
    """

    spool: str  # where SecurityLimitUploadCommand wrote the content
    dest: str  # the limits.conf path being managed

    def shell(self) -> str:
        spool = shlex.quote(self.spool)
        # The spool file lives inside a sha1-derived /tmp directory; remove
        # the whole directory so no leftover lands in /tmp.
        spool_dir = shlex.quote(str(PurePosixPath(self.spool).parent))
        dest = shlex.quote(self.dest)
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        inner = (
            f"mkdir -p {parent} && "
            f"cp {spool} {dest} && "
            f"chmod 0644 {dest} && rm -rf {spool_dir}"
        )
        return f"sh -c {shlex.quote(inner)}"


async def security_limit(
    ctx: OpContext,
    domain: str,
    limit_type: str,
    item: str,
    value: int | str,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Ensure a resource limit line in ``/etc/security/limits.conf``.

    Args:
        ctx (OpContext): Execution context for the operation.
        domain (str): The user, group (``@group``) or wildcard ``*`` the
            limit applies to.
        limit_type (str): ``soft`` or ``hard``.
        item (str): The resource being limited (e.g. ``nofile``, ``memlock``).
        value (int | str): The limit's value; a number or a symbolic string
            such as ``unlimited`` (the baseline types it ``int`` but its own
            fixture passes ``"unlimited"``).
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The spool-and-install pair carrying the new limits.conf
            content, or nothing when the file already reads as desired.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        ```python
        from reemote import op, server

        # Raise a numeric limit for all users in /etc/security/limits.conf (root-owned, so elevate)
        yield op(
            server.security_limit,
            name="Raise the open-file hard limit",
            domain="*",
            limit_type="hard",
            item="nofile",
            value=65536,
            sudo=True,
        )

        # Set a symbolic limit for a group
        yield op(
            server.security_limit,
            name="Lock memory for the app group",
            domain="@app",
            limit_type="hard",
            item="memlock",
            value="unlimited",
            sudo=True,
        )
        ```

        Examples can be found in ``examples/modules/server/server_security_limit.py``.
    """

    # Mirrors the baseline's ``server.security_limit`` for ``domain``,
    # ``limit_type``, ``item`` and ``value``: a thin wrapper over
    # ``files.line`` on ``/etc/security/limits.conf`` whose pattern
    # ``^{domain}[[:space:]]+{limit_type}[[:space:]]+{item}.*$`` excludes
    # the value, so a stale value is normalized in place and an item prefix
    # (``nofilex``) matches while a commented line does not. The lookup is
    # a Python regex over the ``AuthorizedKeysFile`` raw-content fact -- the
    # namespace's path-generic reader, reused never a cross-namespace
    # ``files.FileContents`` import -- with the fields escaped literally
    # (``re.escape``) where the baseline interpolates them raw into a BRE.
    #
    # The write is spool-and-install, not the baseline's elevated sed/echo
    # (``files.line``): reemote transfers never elevate, so one
    # ``SecurityLimitUploadCommand`` scp's the transformed text to a
    # ``/tmp`` spool whose name is derived from the managed path, then one
    # ``SecurityLimitInstallCommand`` overlays ``/etc/security/limits.conf``
    # with it under ``sh -c`` with a ``chmod 0644`` reset. The transfer
    # stays unprivileged regardless of ``sudo`` -- only the install
    # elevates -- which is the two-privilege-level signature of the family.
    # The baseline's timestamped sed backup is gone with the sed (its timestamp
    # is non-deterministic across phases). This operation replaces the
    # earlier ``files.edit`` + demo-side install example recipe (developer
    # decision): the fact read, transform and install now live in one
    # operation, so a single deploy suffices.
    #
    # Idempotence is the content-level fixpoint -- the baseline's ``files.line``
    # no-op when a matching line already reads exactly the replacement:
    # the transform's result compared against the file's current text,
    # with no parsed semantic fact behind limits.conf. One deviation from
    # the file's own bytes: the result always ends in one trailing newline,
    # so a file missing its final newline is rewritten once even when the
    # line itself is correct. ``/etc/security/limits.conf`` is root-owned:
    # flag the request ``sudo=True``. ``**kwargs`` absorbs any further global
    # arguments (``op()`` already strips ``name``/``sudo``/``su``).
    current = await ctx.facts.get(AuthorizedKeysFile, _SECURITY_LIMITS_PATH)
    new = _security_limit_edit(current, domain, limit_type, item, value)
    if new == (current or ""):
        ctx.reason = (
            f"security limit {domain} {limit_type} {item} "
            f"already set to {value} in {_SECURITY_LIMITS_PATH}"
        )
        return

    ctx.changed = True
    spool = _security_limit_spool_path(_SECURITY_LIMITS_PATH)
    ctx.reason = (
        f"security limit {domain} {limit_type} {item} "
        f"will be set to {value} in {_SECURITY_LIMITS_PATH}"
    )
    yield SecurityLimitUploadCommand(content=new, dest=spool)
    yield SecurityLimitInstallCommand(spool=spool, dest=_SECURITY_LIMITS_PATH)


def _module_label(modules: tuple[str, ...]) -> str:
    """The baseline's modprobe noop wording: ``module x`` or ``modules x/y``."""
    noun = "modules" if len(modules) > 1 else "module"
    return f"{noun} {'/'.join(modules)}"


@dataclass(frozen=True)
class ModprobeCommand(Command):
    """One ``modprobe [-f] [-r] -a <modules>`` invocation.

    ``present=False`` renders ``-r`` (unload the named modules) and
    ``force`` renders ``-f``. Each module name is ``shlex``-quoted
    (the baseline's ``QuoteString``), so a name carrying shell metacharacters
    is passed to ``modprobe`` literally rather than executed.
    """

    modules: tuple[str, ...]
    present: bool = True
    force: bool = False

    def shell(self) -> str:
        parts = ["modprobe"]
        if self.force:
            parts.append("-f")
        if not self.present:
            parts.append("-r")
        parts.append("-a")
        parts.extend(shlex.quote(module) for module in self.modules)
        return " ".join(parts)


async def modprobe(
    ctx: OpContext,
    module: str | list[str] | tuple[str, ...],
    present: bool = True,
    force: bool = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Load/unload kernel modules.

    Args:
        ctx (OpContext): Execution context for the operation.
        module (str | list[str] | tuple[str, ...]): Name of the module to
            manage; a single string or a list/tuple of names. Prefer tuples
            when building requests via ``op()``, since ``OperationRequest``
            kwargs must stay hashable.
        present (bool, optional): Whether the module(s) should be loaded or
            not. Defaults to True.
        force (bool, optional): Whether to force any add/remove modules
            (``modprobe -f``). Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The modprobe command, when modules differ from the request.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        ```python
        from reemote import op, server

        # Load a kernel module (modprobe needs root, so elevate with sudo)
        yield op(
            server.modprobe,
            name="Load the br_netfilter module",
            module="br_netfilter",
            sudo=True,
        )

        # Unload several modules (pass a tuple, never a list)
        yield op(
            server.modprobe,
            name="Unload the old sound modules",
            module=("snd_pcsp", "snd_ac97_codec"),
            present=False,
            sudo=True,
        )
        ```

        Examples can be found in ``examples/modules/server/server_modprobe.py``.
    """

    # Mirrors the baseline's ``server.modprobe``. Idempotent: the names are compared
    # against the ``KernelModules`` fact and one ``ModprobeCommand`` is yielded
    # only for the modules that differ from the request -- ``modprobe -a``
    # loads just the missing ones, ``modprobe -r -a`` unloads just the loaded
    # ones (the baseline's partition). ``present`` picks which subset is acted on,
    # so the two directions never mix in one request, and ``force`` does not
    # force a reload of an already-loaded module -- like the baseline, an in-state
    # request is a no-op whatever the flag. Names are compared verbatim: the
    # kernel treats ``-`` and ``_`` as equivalent in module names, but neither
    # The baseline nor reemote normalizes the spelling. The unchanged reason
    # mirrors the baseline's noop wording (``module floppy is loaded``). The
    # commands need root: flag the request ``sudo=True``. ``**kwargs`` absorbs
    # any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    requested = (module,) if isinstance(module, str) else tuple(module)
    loaded = await ctx.facts.get(KernelModules)
    present_mods = tuple(name for name in requested if name in loaded)
    missing_mods = tuple(name for name in requested if name not in loaded)

    # Module is loaded and we don't want it?
    if not present and present_mods:
        ctx.changed = True
        ctx.reason = f"{_module_label(present_mods)} loaded, will remove"
        yield ModprobeCommand(modules=present_mods, present=False, force=force)
    # Module isn't loaded and we want it?
    elif present and missing_mods:
        ctx.changed = True
        ctx.reason = f"{_module_label(missing_mods)} not loaded, will load"
        yield ModprobeCommand(modules=missing_mods, force=force)
    else:
        verb = "are" if len(requested) > 1 else "is"
        state = "loaded" if present else "not loaded"
        ctx.reason = f"{_module_label(requested)} {verb} {state}"


@dataclass(frozen=True)
class MountCommand(Command):
    """One ``mount``/``umount`` invocation.

    ``mounted=False`` renders ``umount <path>``. ``remount=True`` renders
    the options-update form ``mount -o remount,<options> <path>``. Otherwise
    it renders a fresh mount
    ``mount [-t <fs_type>] [-o <options>] [<device>] <path>``. Every
    user-supplied string is ``shlex``-quoted: the baseline quotes only some of
    them, and quoting the rest leaves simple values unchanged while a path
    or device carrying shell metacharacters reaches ``mount`` literally.
    """

    path: str
    mounted: bool = True
    options: tuple[str, ...] = ()
    device: str | None = None
    fs_type: str | None = None
    remount: bool = False

    def shell(self) -> str:
        if not self.mounted:
            return f"umount {shlex.quote(self.path)}"
        options_string = ",".join(self.options)
        if self.remount:
            return (
                f"mount -o {shlex.quote(f'remount,{options_string}')} "
                f"{shlex.quote(self.path)}"
            )
        parts = ["mount"]
        if self.fs_type:
            parts.extend(["-t", shlex.quote(self.fs_type)])
        if options_string:
            parts.extend(["-o", shlex.quote(options_string)])
        if self.device:
            parts.append(shlex.quote(self.device))
        parts.append(shlex.quote(self.path))
        return " ".join(parts)


async def mount(
    ctx: OpContext,
    path: str,
    mounted: bool = True,
    options: list[str] | tuple[str, ...] | None = None,
    device: str | None = None,
    fs_type: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Manage mounted filesystems.

    Args:
        ctx (OpContext): Execution context for the operation.
        path (str): The path of the mounted filesystem.
        mounted (bool, optional): Whether the filesystem should be mounted.
            Defaults to True.
        options (list[str] | tuple[str, ...] | None, optional): The mount
            options; a list/tuple of option strings (prefer tuples in
            ``op()`` requests, which must stay hashable). Defaults to None.
        device (str | None, optional): The device behind the mount.
            Defaults to None.
        fs_type (str | None, optional): The filesystem type. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The mount/unmount/remount command, when state or options
            differ from the request.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        ```python
        from reemote import op, server

        # Mount a device at a path with options (mount needs root, so elevate with sudo)
        yield op(
            server.mount,
            name="Mount the data volume",
            path="/data",
            device="/dev/sdb1",
            fs_type="ext4",
            options=("noatime",),
            sudo=True,
        )

        # Unmount it
        yield op(
            server.mount,
            name="Unmount the data volume",
            path="/data",
            mounted=False,
            sudo=True,
        )
        ```

        Examples can be found in ``examples/modules/server/server_mount.py``.
    """

    # Mirrors the baseline's ``server.mount``. Idempotent: the ``Mounts`` fact is
    # keyed by mount point and ``path`` is looked up directly first; when it is
    # absent and ``device`` is given, the mount is found by device instead
    # (the baseline's fallback for paths whose spelling differs from
    # ``/proc/self/mountinfo``), and that found mount point is what an unmount
    # or remount acts on. A missing mount yields one ``MountCommand`` rendering
    # ``mount [-t fs_type] [-o options] [device] path``; ``mounted=False`` on a
    # present mount yields ``umount``; an already-mounted filesystem whose
    # options do not include all of ``options`` is remounted --
    # ``mount -o remount,<options> <path>``.
    # The options check is a subset test, not equality: extra options already
    # on the mount never trigger a remount, and a remount always carries the
    # full requested option set. Like the baseline, ``/etc/fstab`` is never touched
    # -- persistence across reboots is a ``files.edit`` recipe. The unchanged
    # reason mirrors the baseline's noop wording (``filesystem /data is mounted``).
    # The commands need root: flag the request ``sudo=True``. ``**kwargs``
    # absorbs any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    opts = tuple(options) if options else ()
    mounts = await ctx.facts.get(Mounts)
    is_mounted = path in mounts
    mounted_path = path

    # If path not found directly, check by device as fallback (the baseline):
    # the path representation can differ between user input and
    # /proc/self/mountinfo.
    if not is_mounted and device:
        for point, info in mounts.items():
            if info.get("device") == device:
                is_mounted = True
                mounted_path = point
                break

    # Want mount but don't have?
    if mounted and not is_mounted:
        ctx.changed = True
        ctx.reason = f"filesystem {path} is not mounted, will mount"
        yield MountCommand(path=path, options=opts, device=device, fs_type=fs_type)
    # Want no mount but mounted?
    elif mounted is False and is_mounted:
        ctx.changed = True
        ctx.reason = f"filesystem {mounted_path} is mounted, will unmount"
        yield MountCommand(path=mounted_path, mounted=False)
    # Want mount and is mounted! Check the options (the baseline).
    elif is_mounted and mounted and opts:
        info = mounts[mounted_path]
        needed = set(opts) - set(info.get("options", []))
        if needed:
            ctx.changed = True
            missing = ", ".join(sorted(needed))
            ctx.reason = (
                f"filesystem {mounted_path} is missing options {missing}, "
                "will remount"
            )
            yield MountCommand(path=mounted_path, options=opts, remount=True)
        else:
            ctx.reason = f"filesystem {path} is mounted"
    else:
        ctx.reason = f"filesystem {path} is {'mounted' if mounted else 'not mounted'}"


def _fstab_spool_path(dest: str) -> str:
    """The deterministic ``/tmp`` spool *file* path ``fstab`` uploads to.

    The content is spooled to a fixed ``fstab`` file *inside* a sha1-derived
    ``/tmp`` directory rather than at the top level of ``/tmp``; the elevated
    install ``rm -rf``s the whole directory afterwards.

    Derived from the managed fstab file's sha1 (``_hosts_spool_path`` /
    ``_keys_spool_path`` pattern), even though most requests edit the one
    ``/etc/fstab``: the ``path`` option means two requests in one deploy can
    target different fstab files, and they must not clobber each other's
    spool. The same ``dest`` always yields the same name in both phases, so
    the descriptors stay deterministic. The name avoids *collisions*, not a
    hostile local user: it is predictable, so on a multi-tenant host the
    ``/tmp`` spool is still a symlink-race exposure (the trade-off every
    spool-and-install operation here makes).
    """
    digest = hashlib.sha1(dest.encode()).hexdigest()[:16]
    return f"/tmp/reemote-server-fstab-{digest}/fstab"


def _fstab_content(
    current: str | None,
    key: str,
    columns: tuple[str, str, str, str, str, str],
    present: bool,
) -> str:
    """The pure fstab rewrite: ensure or drop every line keyed by ``key``.

    The ``_hosts_content`` loop transposed to six-column rows, functional on
    the controller: a line is an entry when it is active (not a ``#``
    comment, not blank) and carries at least six whitespace-separated
    columns, and its key is the ``Fstab`` fact's own rule -- the mount point
    column, the device column for a ``none`` (swap) mount point. With
    ``present=True`` every matching line becomes the rendered ``columns``
    and additional duplicates collapse into that one rewritten line rather
    than surviving side by side; when no line matches, the rendered line is
    appended. With
    ``present=False`` every matching line is dropped. Comments on an
    *edited* line are not preserved (the ``_hosts_content`` parity); every
    other line, comment and blank line survives in order and byte-for-byte.
    Line endings are normalised to ``\\n`` and the result carries one
    trailing newline unless it is empty, so a removal that empties the file
    yields ``""``. Idempotent (``f(f(x)) == f(x)``) and side-effect free, so
    both deploy phases compute the same content from the same fact value.
    """
    desired = " ".join(columns)
    new_lines: list[str] = []
    found = False
    for line in current.splitlines() if current else []:
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            new_lines.append(line)
            continue
        tokens = stripped.split()
        if len(tokens) < 6:
            new_lines.append(line)
            continue
        line_key = tokens[0] if tokens[1] == "none" else tokens[1]
        if line_key != key:
            new_lines.append(line)
            continue
        if present and not found:
            new_lines.append(desired)
        # A further duplicate line collapses into the rewritten one above;
        # a removal drops the line entirely.
        found = True
    if present and not found:
        new_lines.append(desired)
    new_content = "\n".join(new_lines)
    if new_content:
        new_content += "\n"
    return new_content


@dataclass(frozen=True)
class FstabUploadCommand(TransferCommand):
    """Spool the transformed fstab content to a ``/tmp`` path on the target.

    The first half of ``fstab``'s write and a *non-shell* descriptor: it
    copies the content over the connection's scp channel via
    ``Host.upload_file`` rather than running shell text, so it has no
    ``shell()`` and its loggable form is the ``__str__`` rendering
    ``command_shell`` falls back to. Like ``EtcHostsUploadCommand`` the
    content is not a caller-supplied stream but the whole fstab-file text
    the operation just derived from the file's current content, so the
    field is a plain ``str`` -- hashable and identical across phases --
    wrapped in a fresh ``io.BytesIO`` only at transfer time. The copy rides
    as the login user (transfers are unprivileged, so the request's ``sudo``
    does not apply to it), which is why the spool path is world-writable
    ``/tmp`` rather than root-owned ``/etc``.
    """

    content: str  # the whole transformed fstab-file text
    dest: str  # spool path on the target host

    def __str__(self) -> str:
        return f"scp <fstab file> -> {self.dest}"

    async def transfer(self, host: Host) -> None:
        # scp does not create parent directories, and the spool lives inside
        # a sha1-derived /tmp directory rather than at the top level, so
        # mkdir -p it first. Unprivileged: /tmp is world-writable, so the
        # login user can create the directory, and a transfer never elevates.
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        await host.run_shell(f"mkdir -p {parent}")
        await host.upload_file(io.BytesIO(self.content.encode()), self.dest)


@dataclass(frozen=True)
class FstabInstallCommand(Command):
    """Install the spooled fstab file ``0644``, then remove the spool.

    The second half of ``fstab``'s write and the elevated one: the whole
    sequence is wrapped in ``sh -c`` -- a bare ``sudo`` prefix would elevate
    only the first command of the ``&&`` chain and leave the cleanup running
    as the login user, whereas ``sudo sh -c '...'`` runs every step elevated,
    which can always remove the login-user spool file. The sequence ensures
    the destination directory, copies the spooled content over the fstab
    file, chmods it ``0644`` (the ``EtcHostsInstallCommand`` pattern) and
    removes the spool directory. No chown is needed: ``/etc/fstab`` is
    root-owned and ``cp`` over an existing file preserves its owner; a
    freshly created file is root-owned already because the whole chain runs
    under ``sudo``. All paths are shell-quoted.
    """

    spool: str  # where FstabUploadCommand wrote the content
    dest: str  # the fstab-file path being managed

    def shell(self) -> str:
        spool = shlex.quote(self.spool)
        # The spool file lives inside a sha1-derived /tmp directory; remove
        # the whole directory so no leftover lands in /tmp.
        spool_dir = shlex.quote(str(PurePosixPath(self.spool).parent))
        dest = shlex.quote(self.dest)
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        inner = (
            f"mkdir -p {parent} && "
            f"cp {spool} {dest} && "
            f"chmod 0644 {dest} && rm -rf {spool_dir}"
        )
        return f"sh -c {shlex.quote(inner)}"


async def fstab(
    ctx: OpContext,
    device: str | None = None,
    mountpoint: str | None = None,
    fs_type: str | None = None,
    options: list[str] | tuple[str, ...] | str | None = None,
    dump: int | None = None,
    passno: int | None = None,
    present: bool = True,
    path: str = "/etc/fstab",
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add, update or remove an entry in ``/etc/fstab``, Ansible ``mount`` parity.

    Args:
        ctx (OpContext): Execution context for the operation.
        device (str | None, optional): The device (or ``UUID=``/``LABEL=``
            spelling) in the entry's first column. Required when
            ``present=True``; for ``present=False`` it is the removal key
            when no ``mountpoint`` is given (the ``name: none`` swap form).
            Defaults to None.
        mountpoint (str | None, optional): The mount point in the entry's
            second column -- ``none`` for a swap entry, where the entry is
            keyed by ``device`` exactly like Ansible's ``mount`` module with
            ``name: none``. Required when ``present=True``; for
            ``present=False`` it is the removal key. Defaults to None.
        fs_type (str | None, optional): The filesystem type (``swap`` for a
            swap entry). Required when ``present=True``; on an update,
            ``None`` keeps the entry's current value. Defaults to None.
        options (list[str] | tuple[str, ...] | str | None, optional): The
            option columns; a comma-separated string or a list/tuple of
            option strings (prefer tuples when building requests via
            ``op()``, which must stay hashable). On an update ``None``
            keeps the entry's current options; on an append it defaults to
            ``("defaults",)``, the fstab(5) spelling. Defaults to None.
        dump (int | None, optional): The dump column. On an update ``None``
            keeps the current value; on an append it defaults to ``0``.
            Defaults to None.
        passno (int | None, optional): The fsck passno column. On an update
            ``None`` keeps the current value; on an append it defaults to
            ``0``. Defaults to None.
        present (bool, optional): Whether the entry should be present or
            absent. Defaults to True.
        path (str, optional): Path to the fstab file. Defaults to
            ``/etc/fstab``.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The spool-and-install pair carrying the new fstab-file
            content, when the entry differs from the request.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        ```python
        from reemote import op, server

        # Persist a swap file in /etc/fstab (root-owned file, so elevate with sudo)
        yield op(
            server.fstab,
            name="Persist the swap file",
            device="/swapfile",
            mountpoint="none",
            fs_type="swap",
            options=("sw",),
            dump=0,
            passno=0,
            sudo=True,
        )

        # Remove the entry again (the swap role's state=absent path)
        yield op(
            server.fstab,
            name="Unpersist the swap file",
            device="/swapfile",
            present=False,
            sudo=True,
        )
        ```

        Examples can be found in ``examples/modules/server/server_fstab.py``.
    """

    # Original reemote operation giving Ansible's ``mount`` module its
    # fstab half for geerlingguy.swap's central declaration (``name: none``,
    # ``src``, ``fstype: swap``, ``opts``, ``state: present|absent``; issue
    # #48), built in-repo as the keyed editors of root-owned files are --
    # ``server.etc_hosts`` above and ``sysctl._edit_conf``. Idempotent: the
    # parsed ``Fstab`` fact gates the no-op ("already present in", "already
    # absent from", the etc_hosts reason shapes), the key is the fact's own
    # rule (mount point, device for a ``none`` swap line), and the
    # comparison is per-field: ``fs_type``/``options``/``dump``/``passno``
    # given in the request must equal the entry's, ``None`` options/dump/
    # passno keep the current entry's values on an update (Ansible rewrites
    # only the differing fields of the parsed line), ``device``/``mountpoint``
    # are the key so a differing device on a mount-point-keyed entry is a
    # rewrite like Ansible's. Whitespace is normalized by the parse, so an
    # equivalent entry in another spelling (tab vs space) reads as no
    # change, and ``options`` compares order-sensitively like etc_hosts'
    # hostnames. When a change is due, the namespace's raw-content fact
    # ``AuthorizedKeysFile`` supplies the file's current text and the pure
    # ``_fstab_content`` transform rewrites just the matching lines
    # (appending when absent, dropping on removal) while every other line,
    # comment and blank line survives verbatim; a content-level fixpoint
    # guard no-ops when the transform's output equals the current text even
    # though the fact stage wanted an edit.
    #
    # The write is spool-and-install, the ``etc_hosts`` family shape: one
    # ``FstabUploadCommand`` scp's the text to a ``/tmp`` spool whose name
    # is derived from the managed path (two requests targeting different
    # fstab files never share one spool), then one ``FstabInstallCommand``
    # overlays the fstab file with it under ``sh -c`` with a ``chmod 0644``
    # reset. The transfer stays unprivileged regardless of ``sudo`` -- only
    # the install elevates -- which is the two-privilege-level signature of
    # the family. ``/etc/fstab`` is root-owned: flag the request
    # ``sudo=True``. This is the persistent half ``server.mount``
    # deliberately does not touch: ``mount`` keeps managing the live table
    # (``/proc/self/mountinfo``) and composes with this operation -- the
    # earlier docs recipe pointing at ``files.edit`` for persistence could
    # not work, since a transfer never elevates and cannot write the
    # root-owned file.
    #
    # Deviations from Ansible's ``mount`` (developer decisions): a matched
    # line is rewritten wholesale from the merged columns rather than
    # field-spliced, so a hand-tuned option not carried in the request is
    # silently overwritten on an update that touches any field; a commented
    # entry is not reactivated (Ansible uncomments a matching entry under
    # some states) and is left verbatim; ``state: mounted``/``unmounted``
    # do not belong here -- the live table stays ``server.mount``'s (swap
    # activation is ``swapon``, see #47). ``options`` accepts the
    # comma-separated string Ansible spells, split on commas. Parameter
    # validation raises the house ``ValueError``; ``**kwargs`` absorbs any
    # further global arguments (``op()`` already strips ``name``/``sudo``/
    # ``su``).
    if isinstance(options, str):
        opts: tuple[str, ...] | None = tuple(options.split(","))
    elif options is None:
        opts = None
    else:
        opts = tuple(options)

    if present:
        if device is None:
            raise ValueError("device must be provided when present=True")
        if mountpoint is None:
            raise ValueError("mountpoint must be provided when present=True")
        if fs_type is None:
            raise ValueError("fs_type must be provided when present=True")
    elif device is None and mountpoint is None:
        raise ValueError("device or mountpoint must be provided when present=False")

    key = device if mountpoint in (None, "none") else mountpoint
    if key is None:
        # A ``none`` mountpoint keys nothing -- every swap line spells it --
        # so Ansible's ``name: none`` entries are addressed by their src.
        raise ValueError("device must be provided for a 'none' mountpoint")
    entries = await ctx.facts.get(Fstab, path)
    current_entry = entries.get(key)

    if present:
        assert mountpoint is not None and fs_type is not None
        if current_entry is None:
            # Fresh append: the fstab(5)/Ansible defaults stand in for the
            # columns the request leaves out.
            desired = (
                device or "",
                mountpoint,
                fs_type,
                opts if opts is not None else ("defaults",),
                dump if dump is not None else 0,
                passno if passno is not None else 0,
            )
        else:
            wanted_options = opts if opts is not None else current_entry["options"]
            wanted_dump = dump if dump is not None else current_entry["dump"]
            wanted_passno = passno if passno is not None else current_entry["passno"]
            if (
                current_entry["device"] == device
                and current_entry["mountpoint"] == mountpoint
                and current_entry["fs_type"] == fs_type
                and current_entry["options"] == wanted_options
                and current_entry["dump"] == wanted_dump
                and current_entry["passno"] == wanted_passno
            ):
                ctx.reason = f"{key} entry already present in {path}"
                return
            desired = (
                device or "",
                mountpoint,
                fs_type,
                wanted_options,
                # A malformed current column (parsed as None) is normalized
                # to the fstab(5) default rather than re-planned every run.
                wanted_dump if wanted_dump is not None else 0,
                wanted_passno if wanted_passno is not None else 0,
            )
    else:
        if current_entry is None:
            ctx.reason = f"{key} already absent from {path}"
            return
        # Removal renders only the key columns; the rest of the line goes.
        desired = (device or "", mountpoint or "", "", (), 0, 0)

    current = await ctx.facts.get(AuthorizedKeysFile, path)
    rendered = (
        desired[0],
        desired[1],
        desired[2],
        ",".join(desired[3]),
        str(desired[4]),
        str(desired[5]),
    )
    new = _fstab_content(current, key, rendered, present)
    if new == (current or ""):
        # The fact wanted an edit the file's own text says is unnecessary.
        ctx.reason = f"{key} entry already written in {path}"
        return

    ctx.changed = True
    spool = _fstab_spool_path(path)
    ctx.reason = (
        f"{key} entry will be written to {path}" if present else f"{key} will be removed from {path}"
    )
    yield FstabUploadCommand(content=new, dest=spool)
    yield FstabInstallCommand(spool=spool, dest=path)


# ``SystemctlCommand`` and the ``service`` operation relocated to the
# ``systemd`` namespace; both are re-exported above for backward
# compatibility (``server.service`` is a deprecated alias of
# ``systemd.service``). ``server.wait_service`` below still polls the
# relocated ``SystemdServiceState`` fact.


@dataclass(frozen=True)
class SetTimezoneCommand(Command):
    """One ``timedatectl set-timezone <timezone>`` invocation.

    The systemd form, which also persists the zone across reboots. The
    timezone is ``shlex``-quoted (the baseline's ``QuoteString``), so a name
    carrying shell metacharacters is set literally rather than executed.
    """

    timezone: str

    def shell(self) -> str:
        return f"timedatectl set-timezone {shlex.quote(self.timezone)}"


async def timezone(
    ctx: OpContext,
    timezone: str,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Set the system timezone.

    Args:
        ctx (OpContext): Execution context for the operation.
        timezone (str): The timezone to set (e.g. ``Europe/Amsterdam``,
            ``UTC``).
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The timezone-setting command, when the zone differs.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, OperationError
    ```

    Examples:
        ```python
        from reemote import op, server

        # Set the system timezone via timedatectl (systemd only; needs root, so elevate with sudo)
        yield op(
            server.timezone,
            name="Set timezone to UTC",
            timezone="UTC",
            sudo=True,
        )
        ```

        Examples can be found in ``examples/modules/server/server_op_timezone.py``.
    """

    # Mirrors the baseline's ``server.timezone``, systemd only. Idempotent: the
    # requested zone is compared against the ``Timezone`` fact and a
    # ``SetTimezoneCommand`` (``timedatectl set-timezone``) is yielded only
    # when they differ. Supported on systemd systems only: when a change is
    # needed the ``Which`` fact probes ``timedatectl``, and a host without it
    # raises ``OperationError`` while materializing -- in a deploy run that is
    # the prepare phase, before anything is sent. An already-set timezone is a
    # no-op on any host, like the baseline, which also compares the fact before
    # probing for the tool. The command needs root: flag the request
    # ``sudo=True``.
    #
    # The baseline's non-systemd fallback -- ``ln -sf
    # /usr/share/zoneinfo/<timezone> /etc/localtime`` plus ``echo <timezone>
    # > /etc/timezone`` -- is deliberately dropped: reemote supports this
    # operation on systemd systems only, where ``timedatectl`` already
    # persists the zone. ``**kwargs`` absorbs any further global arguments
    # (``op()`` already strips ``name``/``sudo``/``su``).
    current = await ctx.facts.get(Timezone)
    if current == timezone:
        ctx.reason = "timezone is set"
        return

    if await ctx.facts.get(Which, "timedatectl") is None:
        raise OperationError(
            f"server.timezone requires timedatectl (systemd only), "
            f"not found on {ctx.host.name}"
        )
    ctx.changed = True
    ctx.reason = f"timezone is {current!r}, will set {timezone!r}"
    yield SetTimezoneCommand(timezone=timezone)


def is_active(state: str | None) -> bool:
    """The ``server.wait_service`` predicate: the unit's state is ``active``.

    A module-level function, not a lambda: the deploy generator is re-invoked
    per phase, and the predicate ends up in the yielded descriptor, so it
    must be the same object every time.
    """
    return state == "active"


def is_listening(info: PortInfo) -> bool:
    """The ``server.wait`` predicate: the port has a listening socket.

    Reads only the ``listening`` flag, never the owner pair -- an
    unprivileged poll sees a socket without its process, and the wait is
    about the port, not about who owns it.

    A module-level function, not a lambda: the deploy generator is re-invoked
    per phase, and the predicate ends up in the yielded descriptor, so it
    must be the same object every time.
    """
    return info["listening"]


@dataclass(frozen=True)
class EndpointReady:
    """The HTTP readiness predicate: the endpoint answered as requested.

    A module-level *class*, instantiated with the accepted status codes and the
    body markers to match, so it stays hashable and value-equal across the two
    deploy phases -- like :func:`is_active` and :func:`is_listening` it must be
    the same object each phase, and a frozen dataclass compares by value where
    a freshly built lambda would not. Ready means:

    * ``status`` is one of ``statuses`` (a refused/unreachable endpoint reports
      ``None`` and so is never ready), and
    * every ``contains`` substring is present in the body, and
    * no ``absent`` substring is present in the body.

    The ``geerlingguy.jenkins`` ``uri`` + ``until`` task is
    ``EndpointReady(statuses=(200, 403), absent="Please wait while")``: the
    port answers with 200 or 403 once Jenkins is up, but keeps serving
    "Please wait while" while it upgrades.
    """

    #: Status codes that count as ready. Defaults to a plain success.
    statuses: tuple[int, ...] = (200,)

    #: Substrings that must each appear in the body, or ``None`` (unchecked).
    contains: str | None = None

    #: Substrings that must each be gone from the body, or ``None`` (unchecked).
    absent: str | None = None

    def __call__(self, info: HttpEndpointInfo) -> bool:
        if info["status"] not in self.statuses:
            return False
        body = info["body"]
        if self.contains is not None and self.contains not in body:
            return False
        if self.absent is not None:
            return self.absent not in body
        return True


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
        # Function predicates (``is_listening``, ``is_active``) carry a
        # ``__name__``; a predicate object (the ``EndpointReady`` dataclass)
        # does not, so fall back to its class name -- mirroring how the fact
        # above is named by its class.
        predicate = getattr(self.predicate, "__name__", type(self.predicate).__name__)
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


async def wait(
    ctx: OpContext,
    port: int,
    protocol: Literal["tcp", "udp"] = "tcp",
    timeout: float = 300.0,
    interval: float = 1.0,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Wait for a port to start listening.

    Args:
        ctx (OpContext): Execution context for the operation.
        port (int): Number of the port to wait for.
        protocol (Literal["tcp", "udp"], optional): Protocol family whose
            sockets count as listening. Defaults to "tcp".
        timeout (float, optional): Seconds to keep polling before raising
            ``WaitTimeoutError``. Defaults to 300.0.
        interval (float, optional): Seconds between polls. Defaults to 1.0.
        **kwargs (Any): Additional keyword arguments.

    Yields:
        Command: The wait command, always (a wait is not idempotent).

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, TypeError, ValueError, WaitTimeoutError
    ```

    Examples:
        ```python
        from reemote import op, server

        # Wait for a TCP port to start listening (unprivileged poll; a wait always reports changed)
        yield op(
            server.wait,
            name="Wait for nginx on 443",
            port=443,
            timeout=60,
        )

        # Wait on a UDP port instead
        yield op(
            server.wait,
            name="Wait for DNS on 53",
            port=53,
            protocol="udp",
        )
        ```

        Examples can be found in ``examples/modules/server/server_op_wait.py``.
    """

    # Mirrors the baseline's ``server.wait`` for ``port``, the only option upstream
    # has. The baseline polls remotely -- a ``while true`` shell loop over
    # ``netstat`` with a fixed sleep and no timeout at all -- where reemote
    # describes the wait and lets the driver poll the ``Port`` fact, bounded by
    # ``timeout`` (the baseline waits forever) and with an adjustable ``interval``.
    # The interval defaults to 1s rather than ``wait_service``'s 5s: a port on
    # its way up usually appears within a second, where a unit's start takes
    # longer. Like every wait this one reads no facts and always yields: a
    # plan-time fact read would reflect pre-deployment state in the prepare
    # phase and post-deployment state in the apply phase, and branching on it
    # would break the determinism guard. The plan therefore always shows the
    # wait as a change. ``_check_port`` rejects a bad port or protocol here, at
    # plan time, rather than from inside the descriptor deep in the apply
    # phase. The wait is unprivileged -- so an owner-less socket (the only kind
    # a restricted user may see) still satisfies it.
    _check_port(port, protocol)
    ctx.changed = True
    ctx.reason = f"wait for {protocol} port {port} to be listening"
    yield WaitForFactCommand(
        fact_cls=Port,
        args=(port, protocol),
        predicate=is_listening,
        timeout=timeout,
        interval=interval,
    )


async def wait_service(
    ctx: OpContext,
    service: str,
    timeout: float = 300.0,
    interval: float = 5.0,
) -> AsyncGenerator[Command]:
    """Wait for a systemd unit to become ``active``.

    Args:
        ctx (OpContext): Execution context for the operation.
        service (str): Name of the unit (``httpd`` or ``httpd.service``).
        timeout (float, optional): Seconds to keep polling before raising
            ``WaitTimeoutError``. Defaults to 300.0.
        interval (float, optional): Seconds between polls. Defaults to 5.0.

    Yields:
        Command: The wait command, always (a wait is not idempotent).

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, WaitTimeoutError
    ```

    Examples:
        ```python
        from reemote import op, server

        # Wait for a systemd unit to become active (unprivileged poll; a wait always reports changed)
        yield op(
            server.wait_service,
            name="Wait for httpd to be active",
            service="httpd",
        )
        ```

        Examples can be found in ``examples/modules/server/wait_for_httpd.py``.
    """

    # Reads no facts and always yields: like the baseline's ``server.wait``, a wait
    # is not idempotent. Whether the unit is already active is discovered at
    # apply time by the driver's poll loop, not at plan time -- a fact read
    # here would reflect pre-deployment state in the prepare phase and
    # post-start state in the apply phase, and branching on it would break the
    # determinism guard. The plan therefore always shows the wait as a change;
    # the apply log reports the wait descriptor that ran. A unit that never
    # becomes active aborts the run with ``WaitTimeoutError``.
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


async def wait_url(
    ctx: OpContext,
    url: str,
    status: tuple[int, ...] = (200,),
    contains: str | None = None,
    absent: str | None = None,
    timeout: float = 300.0,
    interval: float = 5.0,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Wait for an HTTP endpoint to become ready, polling it on the host.

    The application-readiness half of the wait family, the HTTP counterpart to
    ``server.wait`` (a port) and ``server.wait_service`` (a unit): it replaces
    an Ansible ``uri`` + ``until`` readiness task (issue #43). It is a thin
    composition of the generic :class:`WaitForFactCommand` over the
    :class:`HttpEndpoint` fact and the :class:`EndpointReady` predicate, so a
    single named request polls the URL with ``curl`` every ``interval`` seconds
    until the status and body answer as requested or ``timeout`` expires
    (raising ``WaitTimeoutError``). The check runs **host-locally**, so a
    ``localhost`` URL means the target's own loopback -- the freshly started
    daemon a role such as ``geerlingguy.jenkins`` gates on -- which the
    controller could not reach.

    Args:
        ctx (OpContext): Execution context for the operation.
        url (str): The ``http``/``https`` endpoint to poll; a bad scheme or
            type is rejected at plan time.
        status (tuple[int, ...], optional): Status codes that count as ready.
            ``geerlingguy.jenkins`` accepts ``(200, 403)``. Defaults to
            ``(200,)``. Pass a tuple, never a list (the request must hash).
        contains (str, optional): A substring that must be present in the body
            for the endpoint to count as ready. Defaults to ``None`` (unchecked).
        absent (str, optional): A substring that must be gone from the body for
            the endpoint to count as ready -- Jenkins' ``"Please wait while"``
            upgrade page. Defaults to ``None`` (unchecked).
        timeout (float, optional): Seconds to keep polling before raising
            ``WaitTimeoutError``. Defaults to 300.0.
        interval (float, optional): Seconds between polls. Defaults to 5.0.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The wait command, always (a wait is not idempotent).

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, TypeError, ValueError, WaitTimeoutError
    ```

    Examples:
        ```python
        from reemote import op, server

        # Wait for a host-local endpoint to return HTTP 200 (polled on the target with curl; unprivileged)
        yield op(
            server.wait_url,
            name="Wait for the app to come up",
            url="http://localhost:8080/health",
            timeout=60,
        )

        # Accept 200 or 403 and require an upgrade page to clear (the geerlingguy.jenkins shape)
        yield op(
            server.wait_url,
            name="Wait for Jenkins",
            url="http://localhost:8080/login",
            status=(200, 403),
            absent="Please wait while",
        )
        ```

        Examples can be found in ``examples/modules/server/server_wait_url.py``.
    """

    # Like every wait this reads no facts and always yields: a plan-time fact
    # read would reflect pre-deployment state in the prepare phase and
    # post-deployment state in the apply phase, and branching on it would break
    # the determinism guard, so the plan always shows the wait as a change and
    # the driver performs the polling. The condition is discovered by the first
    # poll -- a zero ``timeout`` still polls once, so an already-ready endpoint
    # always succeeds. ``_check_url`` rejects a bad URL here, at plan time,
    # rather than from inside the descriptor deep in the apply phase. The
    # :class:`EndpointReady` predicate is rebuilt from these arguments in both
    # phases; because it is a frozen dataclass it compares by value, so the
    # yielded :class:`WaitForFactCommand` is identical across them (a lambda
    # would not be). ``**kwargs`` absorbs any further global arguments (``op()``
    # already strips ``name``/``sudo``/``su``).
    _check_url(url)
    predicate = EndpointReady(statuses=tuple(status), contains=contains, absent=absent)
    ctx.changed = True
    ctx.reason = f"wait for {url} to be ready"
    yield WaitForFactCommand(
        fact_cls=HttpEndpoint,
        args=(url,),
        predicate=predicate,
        timeout=timeout,
        interval=interval,
    )


__all__ = [
    "Arch",
    "AuthorizedKeys",
    "AuthorizedKeysFile",
    "AuthorizedKeysInstallCommand",
    "AuthorizedKeysUploadCommand",
    "Date",
    "EndpointReady",
    "Env",
    "EtcHosts",
    "EtcHostsInstallCommand",
    "EtcHostsUploadCommand",
    "Fstab",
    "FstabEntry",
    "FstabInstallCommand",
    "FstabUploadCommand",
    "GroupAddCommand",
    "GroupDelCommand",
    "GroupDetails",
    "GroupEntry",
    "GroupModCommand",
    "Groups",
    "HasGui",
    "Home",
    "Hostname",
    "HttpEndpoint",
    "HttpEndpointInfo",
    "Kernel",
    "KernelModules",
    "KernelVersion",
    "KillCommand",
    "Last",
    "LinuxDistribution",
    "LinuxGui",
    "LinuxName",
    "LocaleGenCommand",
    "LocaleInstallCommand",
    "LocaleUploadCommand",
    "Locales",
    "LsbRelease",
    "ModprobeCommand",
    "MountCommand",
    "Mounts",
    "Port",
    "PortInfo",
    "Processes",
    "PythonVersion",
    "PythonVersionInfo",
    "SecurityLimitInstallCommand",
    "SecurityLimitUploadCommand",
    "SetHostnameCommand",
    "SetTimezoneCommand",
    "SshKeyPair",
    "SshKeyPairGenerateCommand",
    "SystemctlCommand",
    "SystemdServiceEnabled",
    "SystemdServiceState",
    "Timezone",
    "Uptime",
    "User",
    "UserAddCommand",
    "UserDelCommand",
    "UserModCommand",
    "Users",
    "WaitForFactCommand",
    "Which",
    "etc_hosts",
    "fstab",
    "group",
    "hostname",
    "is_active",
    "is_listening",
    "kill",
    "locale",
    "modprobe",
    "mount",
    "security_limit",
    "service",
    "ssh_keypair",
    "timezone",
    "unit_name",
    "user",
    "user_authorized_keys",
    "wait",
    "wait_service",
    "wait_url",
]
