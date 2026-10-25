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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Host:
    """One target host, materialized from an inventory entry.

    ``connection`` and ``session`` hold the SSH information passed to the
    asyncssh transport (and therefore reaching both operations and facts);
    ``sudo`` holds optional per-host sudo information. Any unrecognised
    inventory keys are preserved in ``extra``, so the inventory format is
    extensible without library changes.

    ``connection`` keys understood by the transport: ``address`` (or
    ``host``/``hostname``) is the SSH address to connect to, ``username``
    (or ``user``) the login name, ``key_path`` the client private key and
    ``port`` an optional port. ``session`` keys (e.g. ``term_type``) are
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
        """Build a host from one inventory dictionary.

        Recognised keys are ``host``, ``connection``, ``session`` and
        ``sudo``; everything else lands in ``extra``.
        """
        known = {"host", "connection", "session", "sudo"}
        return cls(
            name=entry["host"],
            connection=dict(entry.get("connection", {})),
            session=dict(entry.get("session", {})),
            sudo=dict(entry.get("sudo", {})),
            extra={key: value for key, value in entry.items() if key not in known},
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
        key_path = conn.get("key_path")
        if key_path:
            kwargs["client_keys"] = [str(Path(key_path).expanduser())]
        if conn.get("port") is not None:
            kwargs["port"] = conn["port"]
        return address, kwargs

    def _session_options(self) -> dict[str, Any]:
        """Per-command session options (e.g. terminal type) for ``conn.run``."""
        kwargs: dict[str, Any] = {}
        term_type = self.session.get("term_type")
        if term_type:
            kwargs["term_type"] = term_type
        return kwargs

    async def connect(self) -> Any:
        """Lazily open (and cache) this host's asyncssh connection."""
        conn = getattr(self, "_conn", None)
        if conn is not None:
            return conn
        import asyncssh  # imported lazily: pytest never connects, so never imports it

        address, kwargs = self._connect_options()
        self._conn = await asyncssh.connect(address, **kwargs)
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


__all__ = ["Host"]
