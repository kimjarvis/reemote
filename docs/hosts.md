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
asyncssh instead: it opens a connection lazily from its `connection` data,
runs each command with `run_shell`, and `run` closes the connection when it
finishes.

When a host cannot be reached, [`Host.connect`][reemote5.hosts.Host.connect]
wraps the underlying transport failure — an `OSError` (reset, timeout,
unreachable) or an `asyncssh.Error` (host-key or authentication rejection) —
in a single [`HostConnectionError`][reemote5.hosts.HostConnectionError] naming
that host, so callers never have to catch asyncssh internals or read a raw
transport traceback. The message stays generic (which host, or hosts, could
not be reached); the original error is still available as `__cause__` for
diagnosis. Because [`run_prepare`][reemote5.drivers.run_prepare] connects the
whole inventory concurrently, it collects the per-host failures and merges them
into one aggregate naming every unreachable host, rather than surfacing only
the first to fail.

## SSH connection options

The `connection` mapping keeps asyncssh's own key names, so a curated set of
`asyncssh.connect` options is forwarded verbatim. The target address may be
given as `address` (or `host`/`hostname`), the login name as `username` (or
`user`), and a single client key as the `key_path` shorthand for
`client_keys`. Path-valued options have `~` expanded.

| Option | Purpose |
|---|---|
| `port` | TCP port to connect to |
| `username` / `user` | Login name |
| `password` | Password authentication |
| `key_path` | Single client private key (shorthand for `client_keys`) |
| `client_keys` | Client private keys (list) |
| `passphrase` | Passphrase for encrypted keys |
| `identities` | Certificates/identities to present |
| `agent_path` | SSH agent socket (`None` uses the default) |
| `agent_forwarding` | Forward the agent to the remote host |
| `config` | OpenSSH config file path(s), or `True` for the default |
| `known_hosts` | Known-hosts file path(s); `None` skips host key checking |
| `proxy_command` | Jump-host `ProxyCommand` string |
| `tunnel` | Bastion tunnel, as `"[user@]host[:port]"` or a connection |
| `kex_algs` / `encryption_algs` / `mac_algs` / `server_host_key_algs` | Algorithm pinning for compliance or legacy servers |
| `preferred_auth` | Auth method order, e.g. `["publickey", "password"]` |
| `kbdint_auth` | Enable keyboard-interactive auth (MFA prompts) |
| `login_timeout` | Auth window in seconds |
| `keepalive_interval` / `keepalive_count_max` / `tcp_keepalive` | Dead-peer detection |

Anything asyncssh supports that is not first-class above — GSSAPI, X.509,
socket plumbing, rekeying and the like — can still be passed through the
`asyncssh_connect_kwargs` mapping, which is merged last and may override the
options above, for example:

```python
{
    "host": "web1",
    "connection": {
        "address": "192.168.0.240",
        "username": "kjarvis",
        "proxy_command": "ssh -W %h:%p bastion",
        "known_hosts": None,
        "asyncssh_connect_kwargs": {"family": 0, "local_addr": ("10.0.0.5", 0)},
    },
}
```

::: reemote5.hosts
    options:
      members:
        - Host
        - HostConnectionError
        - InventoryEntry
        - InventoryError
        - validate_inventory

