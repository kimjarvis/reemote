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
(``send_command``) and the facts (each ``process``) will use to run over
asyncssh once a real transport exists.

The model is currently restricted to generating commands: there is no SSH.
Hosts that stand in for a real machine -- holding the state facts read and
the mutations commands apply -- belong to the *test framework* and live in
``tests/``, never in this package. The library does not import or construct
them; it only forwards to the seams such a host provides
(``execute_command`` for commands, ``packages``/``links`` for facts) and
raises ``NotImplementedError`` where a real asyncssh transport is missing.
"""

from dataclasses import dataclass, field


@dataclass
class Host:
    """One target host, materialized from an inventory entry.

    ``connection`` and ``session`` hold the SSH information that will be
    passed to the asyncssh transport (and therefore reaches both operations
    and facts); ``sudo`` holds optional per-host sudo information. Any
    unrecognised inventory keys are preserved in ``extra``, so the inventory
    format is extensible without library changes.
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


__all__ = ["Host"]
