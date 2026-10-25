# Hosts and the inventory

Hosts and their SSH information are specified as a simple *inventory*: a list
of dictionaries, each describing one host. Every entry must carry at least a
`host` (its name) and a `connection` (the SSH connection data); `session` and
`sudo` are optional and any extra keys are preserved. The inventory is the
parameter the library functions accept; [`Host.from_inventory`][reemote5.hosts.Host.from_inventory]
materializes each entry into a [`Host`][reemote5.hosts.Host] whose
`connection`/`session` data reaches both operations
([`send_command`][reemote5.drivers.send_command]) and facts (each `process`).

The inventory is validated with pydantic before any host is materialized.
Each entry is checked against the [`InventoryEntry`][reemote5.hosts.InventoryEntry]
schema — `host` and `connection` are required, `session`/`sudo` optional, and
unrecognised keys are allowed (they land in `Host.extra`).
[`validate_inventory`][reemote5.hosts.validate_inventory] checks a whole
inventory up front (it must be a list of dictionaries), and
[`run`][reemote5.drivers.run] calls it before building any host, so a
malformed inventory raises [`InventoryError`][reemote5.hosts.InventoryError]
rather than failing later over SSH.

`SimulatedHost` is part of the *test framework*, not of the inventory or the
library: it lives in `tests/simulated_host.py` and is the in-memory stand-in
holding the state facts read (`packages`, `links`) and the mutations commands
apply (`execute_command`), which is what makes the "fresh fact cache per
lockstep step" property observable. The library never imports or constructs
it; tests inject it via `run`'s `host_factory` argument and it overrides the
seams [`send_command`][reemote5.drivers.send_command] and the facts otherwise
use to run over a real asyncssh connection. A plain
[`Host`][reemote5.hosts.Host] built from a real inventory entry connects over
asyncssh instead: it opens a connection lazily from its `connection` data
(`address`, `username`, `key_path`, optional `port`), runs each command with
`run_shell`, and `run` closes the connection when it finishes.

## Reference

::: reemote5.hosts
    options:
      members:
        - Host
        - InventoryEntry
        - InventoryError
        - validate_inventory
