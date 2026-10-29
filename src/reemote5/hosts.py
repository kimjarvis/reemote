"""Hosts: the library-side ``Host``, materialized from an inventory entry.

Hosts and their SSH connection information are specified by the user as a
simple *inventory*: a list of dictionaries, each describing one host, e.g.::

    [
        {"host": "web1",
         "connection": {"key_path": "~/.ssh/id_ed25519"},
         "group": "webservers",
         "tags": ["ubuntu_host", "web"]},
        {"host": "web2",
         "connection": {"key_path": "~/.ssh/id_ed25519"},
         "privilege": {"sudo_user": "deploy"}},
    ]

``Host.from_inventory`` materializes one entry into a ``Host`` carrying the
connection, privilege (and optional extra) data, which the operations
(``send_command``) and the facts (each ``process``) use to run over asyncssh.

Every entry is validated with pydantic before it is materialized: the
inventory must be a list of dictionaries and each entry must carry at least
a ``host`` (its name) and a ``connection`` (the SSH connection data), while
``privilege``, ``group`` and ``tags`` are optional and any other keys are
preserved. ``group`` (a single label) and ``tags`` (an unordered list of
string labels) are static classifiers a deploy script reads off the host to
branch per host (``host.group``, ``host.tags``).
``Host.from_inventory`` validates a single entry; ``validate_inventory``
validates a whole inventory up front (``drivers.run`` calls it before
materializing any host). Both raise ``InventoryError`` on bad input.

A plain ``Host`` owns a lazily-established asyncssh connection built from its
``connection`` data: ``connect`` opens it, ``run_shell`` runs one
command over it, ``upload_file`` copies an IO-like source to the remote host
and ``download_file`` copies a remote file into an IO-like destination (both
over asyncssh's scp), and ``aclose`` tears it down. The library calls
those from ``send_command`` and the facts, so a real inventory entry (one whose
``connection`` carries an address) executes for real, while the test
framework's ``SimulatedHost`` overrides the ``execute_command``/``packages``/
``links``/``remote_files`` seams and never connects. Hosts that stand in for a
real machine --
holding the state facts read and the mutations commands apply -- belong to the
*test framework* and live in ``tests/``, never in this package.
"""

import asyncio
import contextlib
import os
import shlex
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

# asyncssh.connect options forwarded verbatim from an inventory ``connection``
# mapping when present. The names match asyncssh's own kwargs exactly -- the
# library keeps asyncssh key names rather than inventing reemote5 aliases --
# and cover the direct equivalents of pyinfra's ``ssh_*`` data plus the
# high-value knobs real deployments touch (jump hosts, algorithm pinning,
# keepalives and login timeouts). The rarely-used asyncssh machinery (GSSAPI,
# X.509, socket plumbing, rekeying, ...) is deliberately not first-class here;
# pass it through the ``asyncssh_connect_kwargs`` mapping instead. Options
# reemote5 refuses to support at all live in ``_FORBIDDEN_OPTIONS`` below.
_FORWARDED_OPTIONS: tuple[str, ...] = (
    "port",
    "password",
    "passphrase",
    "identities",
    "agent_path",
    "agent_forwarding",
    "config",
    "known_hosts",
    "proxy_command",
    "tunnel",
    "kex_algs",
    "encryption_algs",
    "mac_algs",
    "server_host_key_algs",
    "preferred_auth",
    "login_timeout",
    "keepalive_interval",
    "keepalive_count_max",
    "tcp_keepalive",
)

# The forwarded options whose values are filesystem paths, so ``~`` should be
# expanded before they reach asyncssh.
_PATH_OPTIONS: frozenset[str] = frozenset(
    {"agent_path", "config", "identities", "known_hosts"}
)

# asyncssh.connect options reemote5 expressly forbids: kept out of the curated
# set above AND rejected outright rather than silently ignored, so an inventory
# that requests one fails fast with a clear ``InventoryError`` instead of
# reaching asyncssh. ``kbdint_auth`` turns on keyboard-interactive
# authentication -- interactive challenge/MFA prompts -- which cannot be
# answered over asyncssh's non-interactive channels and is out of scope for the
# library. Each value is the human-readable reason carried in the error.
_FORBIDDEN_OPTIONS: dict[str, str] = {
    "kbdint_auth": (
        "keyboard-interactive authentication is not supported: asyncssh "
        "channels are non-interactive and cannot answer its prompts"
    ),
}


def _expand_paths(value: Any) -> Any:
    """Expand ``~`` in a path-valued asyncssh option.

    Handles the forms asyncssh accepts -- a single path string or a list/tuple
    of them -- and leaves any other value (e.g. ``True`` for ``config``, meaning
    "use the default", or ``None`` for ``known_hosts``, meaning "skip host key
    checking") untouched.
    """
    if isinstance(value, str):
        return str(Path(value).expanduser())
    if isinstance(value, (list, tuple)):
        return [_expand_paths(item) for item in value]
    return value


def _reject_forbidden_options(conn: dict[str, Any]) -> None:
    """Raise ``InventoryError`` if a forbidden asyncssh option is requested.

    Both the top-level ``connection`` mapping and the ``asyncssh_connect_kwargs``
    passthrough are checked, since the passthrough is merged last and could
    otherwise smuggle a forbidden option through to ``asyncssh.connect``. See
    ``_FORBIDDEN_OPTIONS``.
    """
    sources: list[tuple[str, Any]] = [("connection", conn)]
    passthrough = conn.get("asyncssh_connect_kwargs")
    if isinstance(passthrough, dict):
        sources.append(("asyncssh_connect_kwargs", passthrough))
    for source, mapping in sources:
        for name, reason in _FORBIDDEN_OPTIONS.items():
            if name in mapping:
                raise InventoryError(
                    f"{source} option {name!r} is forbidden: {reason}"
                )


# sudo askpass plumbing (mirrors pyinfra's connectors/util.py). ``sudo -A``
# reads the password from the program named by ``SUDO_ASKPASS`` instead of a
# TTY, which asyncssh's non-interactive channels do not provide. The helper is
# a tiny static script that echoes ``$REEMOTE5_SUDO_PASSWORD`` -- supplied
# through the environment at run time -- so the password itself is never
# written to the file. The helper is created once per host and removed by
# ``Host.aclose``.
_SUDO_ASKPASS_ENV_VAR = "REEMOTE5_SUDO_PASSWORD"

# Shell snippet that creates the askpass helper and prints its path on stdout.
# The heredoc delimiter is quoted so the body (the password env var and the
# ``\n``) is written literally and only expanded when sudo runs the helper. It
# must echo the env var named by ``_SUDO_ASKPASS_ENV_VAR``.
_ASKPASS_SETUP = """\
f=$(mktemp "${TMPDIR:=/tmp}/reemote5-askpass-XXXXXXXX") || exit 1
cat >"$f" <<'__REEMOTE5_ASKPASS_EOF__'
#!/bin/sh
printf '%s\\n' "$REEMOTE5_SUDO_PASSWORD"
__REEMOTE5_ASKPASS_EOF__
chmod 700 "$f" || exit 1
printf '%s' "$f"
"""


def _build_sudo_command(
    shell: str,
    *,
    sudo_user: str | None = None,
    sudo_password: str | None = None,
    askpass_path: str | None = None,
) -> str:
    """Build the elevated shell command for ``shell`` (pure; performs no I/O).

    With a ``sudo_password``, runs ``sudo -A -k`` under
    ``env SUDO_ASKPASS=<helper> REEMOTE5_SUDO_PASSWORD=<password>`` so sudo
    reads the password from the askpass helper without a TTY; both the helper
    path and the password are shell-quoted. The password lives only in this
    execution string -- the drivers log the credential-free ``command_shell``
    rendering instead, so it never reaches ``format_log``. Without a password,
    plain passwordless ``sudo`` is used. ``sudo_user`` becomes ``sudo -u
    <user>`` (shell-quoted), except that ``root`` -- ``sudo``'s own default
    run-as target -- collapses to a bare ``sudo`` rather than ``-u root``.
    """
    bits: list[str] = []
    sudo = ["sudo"]
    if sudo_password:
        if not askpass_path:
            raise ValueError("askpass_path is required when sudo_password is set")
        bits += [
            "env",
            f"SUDO_ASKPASS={shlex.quote(askpass_path)}",
            f"{_SUDO_ASKPASS_ENV_VAR}={shlex.quote(sudo_password)}",
        ]
        sudo += ["-A", "-k"]
    # ``sudo`` already defaults to running as root, so an explicit
    # ``sudo_user="root"`` collapses to a bare ``sudo`` instead of ``-u root``.
    if sudo_user and sudo_user != "root":
        sudo += ["-u", shlex.quote(sudo_user)]
    bits += sudo
    bits.append(shell)
    return " ".join(bits)


# su askpass plumbing. Unlike sudo, su has no ``SUDO_ASKPASS``/``-A``
# mechanism, so the password is fed to su's stdin through a pipe from a
# generated helper (mirroring pyinfra's connectors/util.py). The helper prints
# ``$REEMOTE5_SU_PASSWORD`` -- supplied through the environment at run time --
# so the password itself is never written to the file. The helper is created
# once per host and removed by ``Host.aclose``. Only *default* su is supported
# here: no login shell (``su -l``), no environment preservation, no ``-s``
# shell override.
_SU_ASKPASS_ENV_VAR = "REEMOTE5_SU_PASSWORD"

# Shell snippet that creates the su askpass helper and prints its path on
# stdout. Same shape as ``_ASKPASS_SETUP`` but echoes the su password env var.
# The heredoc delimiter is quoted so the body is written literally and only
# expanded when the helper runs. It must echo the env var named by
# ``_SU_ASKPASS_ENV_VAR``.
_SU_ASKPASS_SETUP = """\
f=$(mktemp "${TMPDIR:=/tmp}/reemote5-su-askpass-XXXXXXXX") || exit 1
cat >"$f" <<'__REEMOTE5_SU_ASKPASS_EOF__'
#!/bin/sh
printf '%s\\n' "$REEMOTE5_SU_PASSWORD"
__REEMOTE5_SU_ASKPASS_EOF__
chmod 700 "$f" || exit 1
printf '%s' "$f"
"""


def _build_su_command(
    shell: str,
    *,
    su_user: str | None = None,
    su_password: str | None = None,
    askpass_path: str | None = None,
) -> str:
    """Build the default-``su`` wrapped form of ``shell`` (pure; performs no I/O).

    Runs ``su <user> -c <shell>``, the user and the shell both shell-quoted.
    ``su``'s own default target is root, so a ``su_user`` of ``None`` or
    ``root`` collapses to ``su -c <shell>`` rather than naming root explicitly.
    No login shell is used.

    ``su`` has no askpass mechanism, so with a ``su_password`` the password is
    piped to su's stdin: the command becomes ``env
    REEMOTE5_SU_PASSWORD=<password> <helper> | su ...``, where the generated
    helper prints the password; both the helper path and the password are
    shell-quoted. The password lives only in this execution string -- the
    drivers log the credential-free ``command_shell`` rendering instead, so it
    never reaches ``format_log``. Without a password, plain ``su`` is used
    (which assumes the caller may already become the target user, e.g. because
    it is running under ``sudo``).
    """
    su = ["su"]
    # ``su`` already defaults to running as root, so an explicit
    # ``su_user="root"`` collapses to a bare ``su`` instead of naming it.
    if su_user and su_user != "root":
        su.append(shlex.quote(su_user))
    su += ["-c", shlex.quote(shell)]
    if su_password:
        if not askpass_path:
            raise ValueError("askpass_path is required when su_password is set")
        return (
            f"env {_SU_ASKPASS_ENV_VAR}={shlex.quote(su_password)} "
            f"{shlex.quote(askpass_path)} | {' '.join(su)}"
        )
    return " ".join(su)


# stderr signatures identifying sudo's *own* authentication failure, as distinct
# from a failure of the command sudo successfully ran. ``sudo -A`` is invoked
# once, but sudo re-runs the askpass helper up to its ``passwd_tries`` limit
# (3 by default), emitting "Sorry, try again." between attempts before giving
# up -- so one wrong ``sudo_password`` costs three identical attempts and a
# wall of noise. Recognising these lines lets ``Host.run_shell_sudo`` report a
# single actionable ``PrivilegeError`` instead. The set covers both Todd
# Miller's sudo and sudo-rs (Trifecta Tech, the default on Ubuntu 25.10+),
# mirroring the signatures pyinfra matches in its connectors/util.py.
_SUDO_AUTH_FAILURE_MARKERS: tuple[str, ...] = (
    "incorrect password attempts",
    "incorrect authentication attempt",
    "sorry, try again",
    "a password is required",
    "a terminal is required to read the password",
    "no askpass program specified",
    "interactive authentication is required",
)

# stderr signatures identifying su's *own* authentication failure. Unlike sudo,
# su does not retry up to a limit -- a wrong password fails at once -- so there
# is no retry noise to collapse, but the rejection is still classified so it
# surfaces as ``PrivilegeError`` rather than a generic ``CommandError``. The set
# covers shadow's su and util-linux's su.
_SU_AUTH_FAILURE_MARKERS: tuple[str, ...] = (
    "authentication failure",
    "su: incorrect password",
    "cannot make/remove an entry for the specified session",
)


def _is_privilege_failure(*outputs: str) -> bool:
    """Whether sudo's or su's own output identifies an authentication failure.

    Matched case-insensitively across every captured stream (sudo/su write
    their prompts to stderr, but a pty-less channel is not the only shape a
    result can take). A miss is safe: the caller falls back to ``CommandError``,
    which already reports the command, exit status and stderr.
    """
    lowered = " ".join(output.lower() for output in outputs if output)
    markers = _SUDO_AUTH_FAILURE_MARKERS + _SU_AUTH_FAILURE_MARKERS
    return any(marker in lowered for marker in markers)


class InventoryError(ValueError):
    """Raised when an inventory or one of its entries fails validation.

    Wraps pydantic's ``ValidationError`` -- and the structural checks that
    the inventory is a list and each entry a dictionary -- so callers have a
    single, library-specific exception to catch, with a message naming the
    offending entry.
    """


class HostConnectionError(RuntimeError):
    """Raised when an SSH connection to one or more hosts cannot be established.

    ``Host.connect`` wraps the underlying transport failure -- an ``OSError``
    such as a reset or timeout, or an ``asyncssh.Error`` such as a host-key or
    authentication rejection -- in this single, library-specific exception, so
    callers never have to catch asyncssh internals. The message stays generic:
    it names the unreachable host(s) and nothing else, while the original
    exception is preserved as ``__cause__`` (and per host in ``failures``) for
    diagnosis. ``drivers.run_prepare`` merges the per-host failures from a
    concurrent inventory into one aggregate via ``aggregate``, so every
    unreachable host is reported at once instead of only the first to fail.

    ``failures`` is a tuple of ``(host_name, address, cause)`` triples.
    """

    def __init__(self, failures: Iterable[tuple[str, str, BaseException]]) -> None:
        self.failures: tuple[tuple[str, str, BaseException], ...] = tuple(failures)
        hosts = ", ".join(f"{name} ({address})" for name, address, _ in self.failures)
        label = "host" if len(self.failures) == 1 else "hosts"
        super().__init__(
            f"SSH connectivity error on {len(self.failures)} {label}: {hosts}"
        )

    @property
    def host_names(self) -> tuple[str, ...]:
        """Names of the hosts that could not be reached."""
        return tuple(name for name, _, _ in self.failures)

    @classmethod
    def aggregate(
        cls, errors: Iterable["HostConnectionError"]
    ) -> "HostConnectionError":
        """Merge several per-host errors into one naming every failed host."""
        failures = [failure for error in errors for failure in error.failures]
        merged = cls(failures)
        # Keep the first underlying transport error reachable for diagnosis.
        if failures:
            merged.__cause__ = failures[0][2]
        return merged


class CommandError(RuntimeError):
    """Raised when an applied command exits non-zero on the real transport.

    Carries the host name, the credential-free command text (the same rendering
    ``format_log`` shows -- for an elevated command this is ``sudo -u <user>
    ...``, never the askpass string that carries the password), the exit status
    and the captured stderr, so a failed apply is reported instead of being
    silently logged as applied. Fact gathering does not raise this: reads
    tolerate a non-zero exit (e.g. ``readlink`` on an absent link).
    """

    def __init__(
        self, host: str, command: str, exit_status: int | None, stderr: str
    ) -> None:
        self.host = host
        self.command = command
        self.exit_status = exit_status
        self.stderr = stderr or ""
        message = f"command failed on {host} (exit {exit_status}): {command}"
        detail = self.stderr.strip()
        if detail:
            message = f"{message}\n{detail}"
        super().__init__(message)


class PrivilegeError(RuntimeError):
    """Raised when a host rejects the privilege escalation it was asked to perform.

    ``Host.run_shell_privileged`` recognises sudo's and su's *own*
    authentication failures -- a wrong password, a missing one where the tool
    demands it, or a run-as user the login user may not become -- and wraps them
    here instead of raising the generic ``CommandError`` any non-zero exit would
    otherwise produce. The report mirrors ``HostConnectionError``: the message
    stays generic, naming the affected host(s) and nothing else, so sudo's three
    "Sorry, try again" retries never become the user-facing error, and the
    password cannot reach it either. The detail is kept for diagnosis rather
    than discarded -- each ``failures`` entry carries the credential-free
    command text and the ``CommandError`` (exit status plus captured stderr) as
    its cause, and the first cause is chained as ``__cause__``.

    ``drivers.run_apply`` merges the per-host failures from a concurrent
    lockstep step into one aggregate via ``aggregate``, so every host whose
    elevation was rejected is reported at once instead of only the first to
    fail.

    ``failures`` is a tuple of ``(host_name, command, cause)`` triples.
    """

    def __init__(self, failures: Iterable[tuple[str, str, BaseException]]) -> None:
        self.failures: tuple[tuple[str, str, BaseException], ...] = tuple(failures)
        hosts = ", ".join(name for name, _, _ in self.failures)
        label = "host" if len(self.failures) == 1 else "hosts"
        super().__init__(
            f"privilege escalation failed on {len(self.failures)} {label}: {hosts}"
        )

    @property
    def host_names(self) -> tuple[str, ...]:
        """Names of the hosts that rejected the configured elevation."""
        return tuple(name for name, _, _ in self.failures)

    @classmethod
    def aggregate(cls, errors: Iterable["PrivilegeError"]) -> "PrivilegeError":
        """Merge several per-host errors into one naming every failed host."""
        failures = [failure for error in errors for failure in error.failures]
        merged = cls(failures)
        # Keep the first underlying failure reachable for diagnosis.
        if failures:
            merged.__cause__ = failures[0][2]
        return merged


class InventoryEntry(BaseModel):
    """Pydantic schema for one inventory entry.

    An entry must carry at least ``host`` (the host name) and ``connection``
    (the SSH connection data -- possibly an empty mapping for a host only
    ever driven through the test framework's ``SimulatedHost``); ``privilege``,
    ``group`` and ``tags`` are optional. ``group`` is a single label naming the
    group the host belongs to; ``tags`` is an unordered list of string labels.
    Both are static, inventory-supplied values a deploy script can branch on
    (``host.group``, ``host.tags``). Any other keys are allowed and preserved:
    ``extra="allow"`` keeps them in ``model_extra``, which
    ``Host.from_inventory`` folds into ``Host.extra``, so the inventory
    format stays extensible without schema changes.
    """

    model_config = ConfigDict(extra="allow")

    host: str
    connection: dict[str, Any]
    privilege: dict[str, Any] = Field(default_factory=dict)
    group: str | None = None
    tags: list[str] = Field(default_factory=list)


# Reusable adapter: validating the whole inventory in one call is clearer and
# faster than looping per entry, and pydantic reports every bad entry -- each
# identified by its list index in the error ``loc`` -- instead of stopping at
# the first.
InventoryListAdapter = TypeAdapter(list[InventoryEntry])


def _validate_entry(entry: Any) -> InventoryEntry:
    """Validate one inventory entry, raising ``InventoryError`` if invalid.

    Used by ``Host.from_inventory`` for the single-entry path; whole
    inventories go through ``validate_inventory`` (and its ``TypeAdapter``).
    """
    if not isinstance(entry, dict):
        raise InventoryError(
            f"inventory entry must be a dictionary, got {type(entry).__name__}"
        )
    try:
        return InventoryEntry.model_validate(entry)
    except ValidationError as exc:
        raise InventoryError(f"invalid inventory entry: {exc}") from exc


def validate_inventory(inventory: Any) -> list[InventoryEntry]:
    """Validate a whole inventory: a list of dictionaries, each carrying at
    least a ``host`` and a ``connection`` key.

    Returns the validated ``InventoryEntry`` models. Raises ``InventoryError``
    when ``inventory`` is not a list or an entry fails the ``InventoryEntry``
    schema (not a dictionary, missing a required key, or a wrong type); the
    wrapped pydantic error identifies each offending entry by its list index.
    ``drivers.run`` calls this up front so a malformed inventory fails before
    any host is materialized or connected.
    """
    if not isinstance(inventory, list):
        raise InventoryError(
            f"inventory must be a list of dictionaries, got {type(inventory).__name__}"
        )
    try:
        return InventoryListAdapter.validate_python(inventory)
    except ValidationError as exc:
        raise InventoryError(f"invalid inventory: {exc}") from exc


@dataclass
class Host:
    """One target host, materialized from an inventory entry.

    ``connection`` holds the SSH information passed to the asyncssh transport
    (and therefore reaching both operations and facts); ``privilege`` holds
    optional elevation settings: ``sudo_user``/``sudo_password`` (applied when
    an operation is flagged ``sudo=True``) and ``su_user``/``su_password``
    (applied when flagged ``su=True``). ``sudo_user`` is the run-as user the
    driver passes to ``sudo -u``; ``su_user`` is the target of a default
    ``su <user> -c``. When both flags are set the escalations combine in the
    fixed order ``sudo`` then ``su``. Privilege parameters come from here, the
    inventory, and never from an operation: a deploy script can ask to elevate
    but cannot choose the run-as user. ``group`` (an optional single label)
    and ``tags`` (an optional unordered list of string labels) are static,
    inventory-supplied classifiers a deploy script reads to branch per host --
    e.g. ``if host.group == "webservers":`` or ``if "web" in host.tags:``.
    Any unrecognised inventory keys are
    preserved in ``extra``, so the inventory format is extensible without
    library changes.

    ``connection`` keys understood by the transport keep asyncssh's own names:
    ``_connect_options`` forwards a curated set of ``asyncssh.connect`` options
    verbatim (``port``, ``password``, ``passphrase``, ``client_keys``,
    ``identities``, ``agent_path``, ``agent_forwarding``, ``config``,
    ``known_hosts``, ``proxy_command``, ``tunnel``, the ``*_algs``/
    ``preferred_auth`` knobs and the ``keepalive_*``/``login_timeout``
    timeouts). Keyboard-interactive auth (``kbdint_auth``) is expressly
    forbidden rather than forwarded -- see ``_FORBIDDEN_OPTIONS``. Three
    friendly aliases are accepted for convenience:
    ``address``/``host``/``hostname`` for the SSH target,
    ``username``/``user`` for the login name and ``key_path`` as a single-key
    shorthand for ``client_keys``. Anything asyncssh supports that is not
    first-class can still be supplied through the ``asyncssh_connect_kwargs``
    mapping, which is merged last. A connection is opened lazily on first
    use and cached on the instance; ``SimulatedHost`` never triggers this
    because it overrides the ``execute_command``/``packages``/``links``
    seams instead.
    """

    name: str
    connection: dict = field(default_factory=dict)
    privilege: dict = field(default_factory=dict)
    group: str | None = None
    tags: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_inventory(cls, entry: dict) -> "Host":
        """Build a host from one validated inventory dictionary.

        The entry is validated with pydantic (``InventoryEntry``) first, so
        it must carry at least ``host`` and ``connection``; ``privilege``,
        ``group`` and ``tags`` are optional and every other key lands in
        ``extra``. Raises ``InventoryError`` on invalid input.
        """
        validated = _validate_entry(entry)
        return cls(
            name=validated.host,
            connection=dict(validated.connection),
            privilege=dict(validated.privilege),
            group=validated.group,
            tags=list(validated.tags),
            extra=dict(validated.model_extra or {}),
        )

    def __str__(self) -> str:
        return self.name

    # --- Real asyncssh transport -------------------------------------------
    #
    # A plain Host connects for real using its connection data. The
    # connection is opened lazily on first use and cached in ``_conn`` (a
    # non-field attribute, so it stays out of the dataclass constructor,
    # equality and repr). The test framework's SimulatedHost provides the
    # execute_command/packages/links/remote_files seams instead, so it never
    # reaches here.

    def _connect_options(self) -> tuple[str, dict[str, Any]]:
        """Build ``(address, kwargs)`` for ``asyncssh.connect``.

        Recognised options keep asyncssh's own key names and are forwarded
        verbatim when present in the ``connection`` mapping (see
        ``_FORWARDED_OPTIONS``): a key that appears is passed through as-is,
        including an explicit ``None`` (e.g. ``known_hosts=None`` disables host
        key checking, ``client_keys=None`` loads no default identities). This
        covers ``port``, ``password``, ``passphrase``, ``identities``,
        ``agent_path``, ``agent_forwarding``, ``config``, ``known_hosts``,
        ``proxy_command``, ``tunnel``, the algorithm/``preferred_auth`` knobs
        and the ``keepalive_*``/``login_timeout`` timeouts. Three friendly
        aliases are also honoured for backward compatibility:
        ``address``/``host``/``hostname`` for the target, ``username``/``user``
        for the login name, and ``key_path`` as a single-key shorthand for
        ``client_keys``. Path-valued options have ``~`` expanded. Any other
        asyncssh.connect kwarg can be supplied via the
        ``asyncssh_connect_kwargs`` mapping, which is merged last and may
        therefore add to or override the options above.

        Raises ``InventoryError`` when the connection -- or its
        ``asyncssh_connect_kwargs`` passthrough -- requests an expressly
        forbidden option such as ``kbdint_auth`` (see ``_FORBIDDEN_OPTIONS``).
        Raises ``NotImplementedError`` when the connection carries no SSH
        address: that is how an inventory entry with no real target (e.g. one
        only ever driven through SimulatedHost) reports that there is nothing
        to connect to, rather than silently resolving the logical host name.
        """
        conn = self.connection
        _reject_forbidden_options(conn)
        address = conn.get("address") or conn.get("host") or conn.get("hostname")
        if not address:
            raise NotImplementedError(
                "cannot establish an SSH connection: the inventory connection "
                "carries no address to connect to"
            )
        kwargs: dict[str, Any] = {}

        username = conn.get("username") or conn.get("user")
        if username:
            kwargs["username"] = username

        # ``client_keys`` under its asyncssh name, or the legacy single-key
        # ``key_path`` shorthand; either way ``~`` is expanded. An explicit
        # ``client_keys=None`` is forwarded verbatim so asyncssh loads no
        # default identities.
        if "client_keys" in conn:
            kwargs["client_keys"] = _expand_paths(conn["client_keys"])
        elif conn.get("key_path"):
            kwargs["client_keys"] = _expand_paths([conn["key_path"]])

        # Curated asyncssh options: a key present in the mapping is forwarded
        # as-is (``~`` expanded for the path-valued ones), including when its
        # value is ``None``.
        for name in _FORWARDED_OPTIONS:
            if name in conn:
                value = conn[name]
                kwargs[name] = _expand_paths(value) if name in _PATH_OPTIONS else value

        # Escape hatch for anything asyncssh supports that is not first-class
        # above; merged last so it can add to or override those options.
        passthrough = conn.get("asyncssh_connect_kwargs")
        if passthrough:
            kwargs.update(passthrough)

        return address, kwargs

    async def connect(self) -> Any:
        """Lazily open (and cache) this host's asyncssh connection.

        A transport-level failure -- an ``OSError`` (reset, timeout, host
        unreachable) or an ``asyncssh.Error`` (host-key or authentication
        rejection) -- is wrapped in ``HostConnectionError`` naming this host,
        so callers get one library-specific exception instead of a raw
        asyncssh traceback. The original error is chained as ``__cause__``.
        """
        conn = getattr(self, "_conn", None)
        if conn is not None:
            return conn
        import asyncssh  # imported lazily: pytest never connects, so never imports it

        address, kwargs = self._connect_options()
        try:
            self._conn = await asyncssh.connect(address, **kwargs)
        except (OSError, asyncssh.Error) as exc:
            raise HostConnectionError([(self.name, address, exc)]) from exc
        return self._conn

    async def _run_shell(self, shell: str) -> Any:
        """Run one shell command and return the raw asyncssh result.

        The single transport seam ``run_shell`` and ``run_shell_sudo`` build on.
        ``check`` is left off so the *caller* decides how to treat a non-zero
        exit: fact gathering tolerates it, the apply path raises ``CommandError``.
        The result carries ``exit_status``, ``stdout`` and ``stderr``.
        """
        conn = await self.connect()
        return await conn.run(shell, check=False)

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        """Run one shell command over SSH and return its stdout as text.

        ``check`` defaults off so fact gathering tolerates a non-zero exit
        (e.g. ``readlink`` on an absent link exits non-zero with empty stdout).
        The apply path passes ``check=True``, so a command that exits non-zero
        raises ``CommandError`` instead of being silently treated as applied.
        """
        result = await self._run_shell(shell)
        if check and result.exit_status != 0:
            raise CommandError(self.name, shell, result.exit_status, result.stderr)
        return result.stdout or ""

    async def run_shell_privileged(
        self, shell: str, *, sudo: bool = False, su: bool = False
    ) -> str:
        """Run one shell command elevated with ``sudo`` and/or ``su``.

        Elevation parameters come from this host's inventory ``privilege``
        data, never from the caller. The two escalations combine in a fixed
        order -- ``sudo`` then ``su`` -- so ``sudo`` is the outer escalation
        and runs ``su`` inside it (``sudo ... su <user> -c ...``), matching the
        order in which the flags are applied.

        ``sudo`` uses ``sudo_user``/``sudo_password`` (see ``run_shell_sudo``);
        ``su`` uses ``su_user``/``su_password`` and runs a *default* (non-login)
        ``su <user> -c``. ``su`` has no askpass mechanism, so with an
        ``su_password`` the generated helper pipes the password to su's stdin;
        when that pipeline also runs under ``sudo`` it is wrapped in ``sh -c``
        so the pipe stays intact inside sudo's elevation (sudo execs its
        command directly rather than through a shell).

        A non-zero exit raises ``CommandError`` whose command text is the
        credential-free rendering -- never the askpass command, which carries a
        password. When sudo's or su's output identifies the failure as its
        *own* authentication rejection (a wrong or missing password, a
        forbidden run-as user) a ``PrivilegeError`` is raised instead, so the
        caller gets one actionable error naming the host.
        """
        sudo_user = self.privilege.get("sudo_user") if sudo else None
        sudo_password = self.privilege.get("sudo_password") if sudo else None
        su_user = self.privilege.get("su_user") if su else None
        su_password = self.privilege.get("su_password") if su else None

        # Build the innermost escalation first (su), then wrap it with sudo so
        # the applied order is sudo -> su.
        payload = shell
        if su:
            su_askpass = await self._ensure_su_askpass() if su_password else None
            payload = _build_su_command(
                shell,
                su_user=su_user,
                su_password=su_password,
                askpass_path=su_askpass,
            )
        if sudo:
            # An su password turns the payload into a shell pipeline
            # (``... | su ...``). sudo execs its command directly rather than
            # through a shell, so wrap the pipeline in ``sh -c`` to keep it
            # intact and running under sudo's elevation.
            sudo_payload = payload
            if su and su_password:
                sudo_payload = f"sh -c {shlex.quote(payload)}"
            sudo_askpass = (
                await self._ensure_sudo_askpass() if sudo_password else None
            )
            command = _build_sudo_command(
                sudo_payload,
                sudo_user=sudo_user,
                sudo_password=sudo_password,
                askpass_path=sudo_askpass,
            )
        else:
            command = payload

        result = await self._run_shell(command)
        if result.exit_status != 0:
            # Report the credential-free rendering, not the password-bearing one.
            safe_payload = _build_su_command(shell, su_user=su_user) if su else shell
            safe = (
                _build_sudo_command(safe_payload, sudo_user=sudo_user)
                if sudo
                else safe_payload
            )
            failure = CommandError(self.name, safe, result.exit_status, result.stderr)
            if _is_privilege_failure(result.stderr, result.stdout):
                # The escalation never got as far as running the command: the
                # credential (or the run-as target) was rejected. Report it as
                # a privilege failure, keeping the detail reachable as the cause.
                raise PrivilegeError([(self.name, safe, failure)]) from failure
            raise failure
        return result.stdout or ""

    async def run_shell_sudo(self, shell: str) -> str:
        """Run one shell command elevated with ``sudo`` and return its stdout.

        A thin wrapper over ``run_shell_privileged`` for the sudo-only case.
        Elevation parameters come from this host's inventory ``privilege``
        data, never from the caller: ``sudo_user`` becomes ``sudo -u <user>``
        (with ``root`` collapsing to a bare ``sudo``), and when
        ``sudo_password`` is set the command runs under ``sudo -A`` with a
        generated askpass helper, so the password is supplied without a TTY and
        never appears in the command text the drivers log. With no password,
        plain passwordless ``sudo`` is used. A non-zero exit raises
        ``CommandError`` (credential-free) or, when sudo reports its *own*
        authentication rejection, ``PrivilegeError``.
        """
        return await self.run_shell_privileged(shell, sudo=True)

    async def run_shell_su(self, shell: str) -> str:
        """Run one shell command elevated with default ``su`` and return stdout.

        A thin wrapper over ``run_shell_privileged`` for the su-only case.
        ``su_user`` becomes ``su <user> -c`` (with ``None``/``root`` collapsing
        to a bare ``su -c``); when ``su_password`` is set the password is piped
        to su's stdin through a generated askpass helper, since su has no
        ``-A`` mechanism. Login shells are not supported. A non-zero exit
        raises ``CommandError`` (credential-free) or, when su reports its *own*
        authentication rejection, ``PrivilegeError``.
        """
        return await self.run_shell_privileged(shell, su=True)

    async def upload_file(self, src: IO[Any], remote: str) -> None:
        """Copy an IO-like source to ``remote`` on this host via asyncssh's scp.

        The non-shell transport seam ``files.put`` rides: instead of running
        shell text, ``send_command`` dispatches a ``PutCommand`` here, and the
        copy goes over the cached connection's scp protocol as the login user
        (no privilege escalation applies to it).

        ``src`` is a seekable IO-like object, never a path -- reemote5
        deliberately supports only the stream form pyinfra also accepts. The
        stream is rewound and read once (a text stream's payload is encoded
        as UTF-8), and because asyncssh's ``scp`` copies between *paths* --
        its source may be a local path or an ``(conn, path)`` tuple, not a
        file-like object -- the bytes are materialized into a temporary local
        file for the copy and removed afterwards. A connection failure still
        surfaces as ``HostConnectionError`` from ``connect``; a failed copy --
        an unwritable destination, a dropped transfer -- raises ``CommandError``
        whose command text is the ``scp <source> -> <remote>`` rendering, with
        no exit status (scp reports an exception, not a shell exit).
        """
        conn = await self.connect()
        import asyncssh  # imported lazily: pytest never connects, so never imports it

        src.seek(0)
        data = src.read()
        if isinstance(data, str):
            data = data.encode("utf-8")
        fd, local = tempfile.mkstemp(prefix="reemote5-upload-")
        try:
            with os.fdopen(fd, "wb") as tmp:
                tmp.write(data)
            await asyncssh.scp(local, (conn, remote))
        except (OSError, asyncssh.Error) as exc:
            raise CommandError(
                self.name,
                f"scp {getattr(src, 'name', '<stream>')} -> {remote}",
                None,
                str(exc),
            ) from exc
        finally:
            with contextlib.suppress(OSError):
                os.unlink(local)

    async def download_file(self, remote: str, dest: IO[Any]) -> None:
        """Copy ``remote`` on this host into an IO-like destination via scp.

        The mirror of ``upload_file`` and the non-shell transport seam
        ``files.get`` rides: instead of running shell text, ``send_command``
        dispatches a ``GetCommand`` here, and the copy goes over the cached
        connection's scp protocol as the login user (no privilege escalation
        applies to it).

        ``dest`` is a seekable, writable IO-like object, never a path -- the
        mirror of ``upload_file``'s source. Because asyncssh's ``scp`` copies
        between *paths*, the remote file is first fetched into a temporary
        local file, then its bytes are written into ``dest`` (decoded as UTF-8
        for a text stream) and the stream rewound, and the temporary file is
        removed. A connection failure still surfaces as ``HostConnectionError``
        from ``connect``; a failed copy -- an absent or unreadable remote
        source, a dropped transfer -- raises ``CommandError`` whose command
        text is the ``scp <remote> -> <dest>`` rendering, with no exit status
        (scp reports an exception, not a shell exit).
        """
        conn = await self.connect()
        import asyncssh  # imported lazily: pytest never connects, so never imports it

        fd, local = tempfile.mkstemp(prefix="reemote5-download-")
        os.close(fd)  # scp writes the destination file itself; only the path matters
        try:
            await asyncssh.scp((conn, remote), local)
            # Read the fetched bytes off the event loop: opening a file is
            # blocking, and Path.read_bytes opens+reads+closes in one call.
            data = await asyncio.to_thread(Path(local).read_bytes)
            dest.seek(0)
            dest.truncate()
            try:
                dest.write(data)
            except TypeError:
                # A text stream rejects bytes; decode as UTF-8 (write_dest_bytes).
                dest.write(data.decode("utf-8"))
            dest.seek(0)
        except (OSError, asyncssh.Error) as exc:
            raise CommandError(
                self.name,
                f"scp {remote} -> {getattr(dest, 'name', '<stream>')}",
                None,
                str(exc),
            ) from exc
        finally:
            with contextlib.suppress(OSError):
                os.unlink(local)

    async def _ensure_sudo_askpass(self) -> str:
        """Create (once) and return the path of this host's sudo askpass helper.

        Writes the static ``_ASKPASS_SETUP`` helper over the connection, caches
        its remote path on the instance (a non-field attribute, like ``_conn``),
        and reuses it for every subsequent elevated command. ``aclose`` removes
        the file. Raises ``RuntimeError`` if the helper could not be created.
        """
        path = getattr(self, "_askpass_path", None)
        if path:
            return path
        path = (await self.run_shell(_ASKPASS_SETUP)).strip()
        if not path:
            raise RuntimeError(f"{self.name}: could not create a sudo askpass helper")
        self._askpass_path = path
        return path

    async def _ensure_su_askpass(self) -> str:
        """Create (once) and return the path of this host's su askpass helper.

        Same shape as ``_ensure_sudo_askpass`` but writes ``_SU_ASKPASS_SETUP``
        (which echoes ``$REEMOTE5_SU_PASSWORD``) and caches the path in the
        separate ``_su_askpass_path`` attribute. ``aclose`` removes the file.
        Raises ``RuntimeError`` if the helper could not be created.
        """
        path = getattr(self, "_su_askpass_path", None)
        if path:
            return path
        path = (await self.run_shell(_SU_ASKPASS_SETUP)).strip()
        if not path:
            raise RuntimeError(f"{self.name}: could not create a su askpass helper")
        self._su_askpass_path = path
        return path

    async def aclose(self) -> None:
        """Close the cached connection, removing any askpass helpers first."""
        conn = getattr(self, "_conn", None)
        helpers = (
            getattr(self, "_askpass_path", None),
            getattr(self, "_su_askpass_path", None),
        )
        self._askpass_path = None
        self._su_askpass_path = None
        if conn is not None:
            for path in helpers:
                if not path:
                    continue
                # Best-effort cleanup: a dropped connection must not fail teardown.
                with contextlib.suppress(Exception):
                    await conn.run(f"rm -f {shlex.quote(path)}", check=False)
        if conn is None:
            return
        self._conn = None
        conn.close()
        await conn.wait_closed()


__all__ = [
    "CommandError",
    "Host",
    "HostConnectionError",
    "InventoryEntry",
    "InventoryError",
    "PrivilegeError",
    "validate_inventory",
]
