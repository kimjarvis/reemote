"""Hosts: the library-side ``Host``, materialized from an inventory entry.

Hosts and their SSH connection information are specified by the user as a
simple *inventory*: a list of dictionaries, each describing one host, e.g.::

    [
        {"host": "web1",
         "connection": {"key_path": "~/.ssh/id_ed25519"}},
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
``privilege`` is optional and any other keys are preserved.
``Host.from_inventory`` validates a single entry; ``validate_inventory``
validates a whole inventory up front (``drivers.run`` calls it before
materializing any host). Both raise ``InventoryError`` on bad input.

A plain ``Host`` owns a lazily-established asyncssh connection built from its
``connection`` data: ``connect`` opens it, ``run_shell`` runs one
command over it, and ``aclose`` tears it down. The library calls those from
``send_command`` and the facts, so a real inventory entry (one whose
``connection`` carries an address) executes for real, while the test
framework's ``SimulatedHost`` overrides the ``execute_command``/``packages``/
``links`` seams and never connects. Hosts that stand in for a real machine --
holding the state facts read and the mutations commands apply -- belong to the
*test framework* and live in ``tests/``, never in this package.
"""

import contextlib
import shlex
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

# asyncssh.connect options forwarded verbatim from an inventory ``connection``
# mapping when present. The names match asyncssh's own kwargs exactly -- the
# library keeps asyncssh key names rather than inventing reemote5 aliases --
# and cover the direct equivalents of pyinfra's ``ssh_*`` data plus the
# high-value knobs real deployments touch (jump hosts, algorithm pinning,
# keyboard-interactive auth, keepalives and login timeouts). The rarely-used
# asyncssh machinery (GSSAPI, X.509, socket plumbing, rekeying, ...) is
# deliberately not first-class here; pass it through the
# ``asyncssh_connect_kwargs`` mapping instead.
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
    "kbdint_auth",
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


def _is_privilege_failure(*outputs: str) -> bool:
    """Whether sudo's own output identifies an authentication failure.

    Matched case-insensitively across every captured stream (sudo writes its
    prompts to stderr, but a pty-less channel is not the only shape a result
    can take). A miss is safe: the caller falls back to ``CommandError``,
    which already reports the command, exit status and stderr.
    """
    lowered = " ".join(output.lower() for output in outputs if output)
    return any(marker in lowered for marker in _SUDO_AUTH_FAILURE_MARKERS)


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

    ``Host.run_shell_sudo`` recognises sudo's *own* authentication failures --
    a wrong ``sudo_password``, a missing one where sudo demands it, or a run-as
    user the login user may not become -- and wraps them here instead of raising
    the generic ``CommandError`` any non-zero exit would otherwise produce. The
    report mirrors ``HostConnectionError``: the message stays generic, naming
    the affected host(s) and nothing else, so sudo's three "Sorry, try again"
    retries never become the user-facing error, and the password cannot reach
    it either. The detail is kept for diagnosis rather than discarded -- each
    ``failures`` entry carries the credential-free command text and the
    ``CommandError`` (exit status plus captured stderr) as its cause, and the
    first cause is chained as ``__cause__``.

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
    ever driven through the test framework's ``SimulatedHost``); ``privilege``
    is optional. Any other keys are allowed and preserved:
    ``extra="allow"`` keeps them in ``model_extra``, which
    ``Host.from_inventory`` folds into ``Host.extra``, so the inventory
    format stays extensible without schema changes.
    """

    model_config = ConfigDict(extra="allow")

    host: str
    connection: dict[str, Any]
    privilege: dict[str, Any] = Field(default_factory=dict)


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
    optional elevation settings -- currently ``sudo_user``, the run-as user
    the driver passes to ``sudo -u`` when an operation is flagged
    ``sudo=True``. Privilege parameters come from here, the inventory, and
    never from an operation: a deploy script can ask to elevate but cannot
    choose the run-as user. Any unrecognised inventory keys are preserved in
    ``extra``, so the inventory format is extensible without library changes.

    ``connection`` keys understood by the transport keep asyncssh's own names:
    ``_connect_options`` forwards a curated set of ``asyncssh.connect`` options
    verbatim (``port``, ``password``, ``passphrase``, ``client_keys``,
    ``identities``, ``agent_path``, ``agent_forwarding``, ``config``,
    ``known_hosts``, ``proxy_command``, ``tunnel``, the ``*_algs``/
    ``preferred_auth``/``kbdint_auth`` knobs and the ``keepalive_*``/
    ``login_timeout`` timeouts). Three friendly aliases are accepted for
    convenience: ``address``/``host``/``hostname`` for the SSH target,
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
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_inventory(cls, entry: dict) -> "Host":
        """Build a host from one validated inventory dictionary.

        The entry is validated with pydantic (``InventoryEntry``) first, so
        it must carry at least ``host`` and ``connection``; ``privilege`` is
        optional and every other key lands in ``extra``. Raises
        ``InventoryError`` on invalid input.
        """
        validated = _validate_entry(entry)
        return cls(
            name=validated.host,
            connection=dict(validated.connection),
            privilege=dict(validated.privilege),
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
    # execute_command/packages/links seams instead, so it never reaches here.

    def _connect_options(self) -> tuple[str, dict[str, Any]]:
        """Build ``(address, kwargs)`` for ``asyncssh.connect``.

        Recognised options keep asyncssh's own key names and are forwarded
        verbatim when present in the ``connection`` mapping (see
        ``_FORWARDED_OPTIONS``): a key that appears is passed through as-is,
        including an explicit ``None`` (e.g. ``known_hosts=None`` disables host
        key checking, ``client_keys=None`` loads no default identities). This
        covers ``port``, ``password``, ``passphrase``, ``identities``,
        ``agent_path``, ``agent_forwarding``, ``config``, ``known_hosts``,
        ``proxy_command``, ``tunnel``, the algorithm/``preferred_auth``/
        ``kbdint_auth`` knobs and the ``keepalive_*``/``login_timeout``
        timeouts. Three friendly aliases are also honoured for backward
        compatibility: ``address``/``host``/``hostname`` for the target,
        ``username``/``user`` for the login name, and ``key_path`` as a
        single-key shorthand for ``client_keys``. Path-valued options have
        ``~`` expanded. Any other asyncssh.connect kwarg can be supplied via
        the ``asyncssh_connect_kwargs`` mapping, which is merged last and may
        therefore add to or override the options above.

        Raises ``NotImplementedError`` when the connection carries no SSH
        address: that is how an inventory entry with no real target (e.g. one
        only ever driven through SimulatedHost) reports that there is nothing
        to connect to, rather than silently resolving the logical host name.
        """
        conn = self.connection
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

    async def run_shell_sudo(self, shell: str) -> str:
        """Run one shell command elevated with ``sudo`` and return its stdout.

        Elevation parameters come from this host's inventory ``privilege``
        data, never from the caller: ``sudo_user`` becomes ``sudo -u <user>``,
        and when ``sudo_password`` is set the command runs under ``sudo -A``
        with a generated askpass helper, so the password is supplied without a
        TTY and never appears in the command text the drivers log. With no
        password, plain passwordless ``sudo`` is used.

        A non-zero exit raises ``CommandError`` whose command text is the
        credential-free rendering (``sudo -u <user> ...``) -- never the askpass
        command, which carries the password. When sudo's output identifies the
        failure as its *own* authentication rejection (a wrong or missing
        password, a forbidden run-as user) a ``PrivilegeError`` is raised
        instead, so the caller gets one actionable error naming the host rather
        than the generic command failure plus sudo's three retry lines.
        """
        password = self.privilege.get("sudo_password")
        sudo_user = self.privilege.get("sudo_user")
        askpass_path = await self._ensure_sudo_askpass() if password else None
        command = _build_sudo_command(
            shell,
            sudo_user=sudo_user,
            sudo_password=password,
            askpass_path=askpass_path,
        )
        result = await self._run_shell(command)
        if result.exit_status != 0:
            # Report the credential-free rendering, not the password-bearing one.
            safe = _build_sudo_command(shell, sudo_user=sudo_user)
            failure = CommandError(self.name, safe, result.exit_status, result.stderr)
            if _is_privilege_failure(result.stderr, result.stdout):
                # sudo never got as far as running the command: the credential
                # (or the run-as target) was rejected. Report it as a privilege
                # failure, keeping the full detail reachable as the cause.
                raise PrivilegeError([(self.name, safe, failure)]) from failure
            raise failure
        return result.stdout or ""

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

    async def aclose(self) -> None:
        """Close the cached connection, removing any sudo askpass helper first."""
        conn = getattr(self, "_conn", None)
        askpass = getattr(self, "_askpass_path", None)
        self._askpass_path = None
        if askpass and conn is not None:
            # Best-effort cleanup: a dropped connection must not fail teardown.
            with contextlib.suppress(Exception):
                await conn.run(f"rm -f {shlex.quote(askpass)}", check=False)
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
