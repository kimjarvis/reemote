"""Hosts: the library-side ``Host``, materialized from an inventory entry.

Hosts and their SSH connection information are specified by the user as a
simple *inventory*: a list of dictionaries, each describing one host, e.g.::

    [
        {"host": "web1",
         "connection": {"key_path": "~/.ssh/id_ed25519"},
         "session": {"term_type": "xterm-color"}},
        {"host": "web2",
         "connection": {"key_path": "~/.ssh/id_ed25519"},
         "session": {"term_type": "xterm-color", "term_size": 80},
         "sudo": {"password": "..."}},
    ]

``Host.from_inventory`` materializes one entry into a ``Host`` carrying the
connection/session (and optional sudo/extra) data, which the operations
(``send_command``) and the facts (each ``process``) use to run over asyncssh.

Every entry is validated with pydantic before it is materialized: the
inventory must be a list of dictionaries and each entry must carry at least
a ``host`` (its name) and a ``connection`` (the SSH connection data), while
``session`` and ``sudo`` are optional and any other keys are preserved.
``Host.from_inventory`` validates a single entry; ``validate_inventory``
validates a whole inventory up front (``drivers.run`` calls it before
materializing any host). Both raise ``InventoryError`` on bad input.

A plain ``Host`` owns a lazily-established asyncssh connection built from its
``connection``/``session`` data: ``connect`` opens it, ``run_shell`` runs one
command over it, and ``aclose`` tears it down. The library calls those from
``send_command`` and the facts, so a real inventory entry (one whose
``connection`` carries an address) executes for real, while the test
framework's ``SimulatedHost`` overrides the ``execute_command``/``packages``/
``links`` seams and never connects. Hosts that stand in for a real machine --
holding the state facts read and the mutations commands apply -- belong to the
*test framework* and live in ``tests/``, never in this package.
"""

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


class InventoryEntry(BaseModel):
    """Pydantic schema for one inventory entry.

    An entry must carry at least ``host`` (the host name) and ``connection``
    (the SSH connection data -- possibly an empty mapping for a host only
    ever driven through the test framework's ``SimulatedHost``); ``session``
    and ``sudo`` are optional. Any other keys are allowed and preserved:
    ``extra="allow"`` keeps them in ``model_extra``, which
    ``Host.from_inventory`` folds into ``Host.extra``, so the inventory
    format stays extensible without schema changes.
    """

    model_config = ConfigDict(extra="allow")

    host: str
    connection: dict[str, Any]
    session: dict[str, Any] = Field(default_factory=dict)
    sudo: dict[str, Any] = Field(default_factory=dict)


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

    ``connection`` and ``session`` hold the SSH information passed to the
    asyncssh transport (and therefore reaching both operations and facts);
    ``sudo`` holds optional per-host sudo information. Any unrecognised
    inventory keys are preserved in ``extra``, so the inventory format is
    extensible without library changes.

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
    mapping, which is merged last. ``session`` keys (e.g. ``term_type``) are
    applied per command. A connection is opened lazily on first use and
    cached on the instance; ``SimulatedHost`` never triggers this because it
    overrides the ``execute_command``/``packages``/``links`` seams instead.
    """

    name: str
    connection: dict = field(default_factory=dict)
    session: dict = field(default_factory=dict)
    sudo: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_inventory(cls, entry: dict) -> "Host":
        """Build a host from one validated inventory dictionary.

        The entry is validated with pydantic (``InventoryEntry``) first, so
        it must carry at least ``host`` and ``connection``; ``session`` and
        ``sudo`` are optional and every other key lands in ``extra``. Raises
        ``InventoryError`` on invalid input.
        """
        validated = _validate_entry(entry)
        return cls(
            name=validated.host,
            connection=dict(validated.connection),
            session=dict(validated.session),
            sudo=dict(validated.sudo),
            extra=dict(validated.model_extra or {}),
        )

    def __str__(self) -> str:
        return self.name

    # --- Real asyncssh transport -------------------------------------------
    #
    # A plain Host connects for real using its connection/session data. The
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

    def _session_options(self) -> dict[str, Any]:
        """Per-command session options (e.g. terminal type) for ``conn.run``."""
        kwargs: dict[str, Any] = {}
        term_type = self.session.get("term_type")
        if term_type:
            kwargs["term_type"] = term_type
        return kwargs

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

    async def run_shell(self, shell: str) -> str:
        """Run one shell command over SSH and return its stdout as text.

        ``check`` is left off so callers decide how to treat a non-zero exit
        (e.g. ``readlink`` on an absent link exits non-zero with empty stdout).
        """
        conn = await self.connect()
        result = await conn.run(shell, check=False, **self._session_options())
        return result.stdout or ""

    async def aclose(self) -> None:
        """Close the cached connection, if one was ever opened."""
        conn = getattr(self, "_conn", None)
        if conn is None:
            return
        self._conn = None
        conn.close()
        await conn.wait_closed()


__all__ = [
    "Host",
    "HostConnectionError",
    "InventoryEntry",
    "InventoryError",
    "validate_inventory",
]
