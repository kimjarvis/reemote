# Hosts and the inventory

Hosts and their SSH information are specified as a simple *inventory*: a list
of dictionaries, each describing one host. Every entry must carry at least a
`host` (its name) and a `connection` (the SSH connection data); `privilege` is
optional and any extra keys are preserved. The inventory is the
parameter the library functions accept; [`Host.from_inventory`][reemote5.hosts.Host.from_inventory]
materializes each entry into a [`Host`][reemote5.hosts.Host] whose
`connection` data reaches both operations
([`send_command`][reemote5.drivers.send_command]) and facts (each `process`).

The inventory is validated with pydantic before any host is materialized.
Each entry is checked against the [`InventoryEntry`][reemote5.hosts.InventoryEntry]
schema — `host` and `connection` are required, `privilege` optional, and
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

### Forbidden options

A few asyncssh options are *expressly forbidden* rather than merely omitted
from the curated list: an inventory that requests one raises
[`InventoryError`][reemote5.hosts.InventoryError] instead of the option being
silently dropped or passed on to asyncssh.

`kbdint_auth` (keyboard-interactive authentication) is forbidden. It produces
interactive challenge/MFA prompts, which asyncssh's non-interactive channels
cannot answer, so reemote5 does not support keyboard-interactive auth at all.
The rejection also covers the `asyncssh_connect_kwargs` passthrough — because
that mapping is merged last and could otherwise override the curated options, a
forbidden key cannot be smuggled through it either. The forbidden set is defined
by `_FORBIDDEN_OPTIONS` in [`reemote5.hosts`][reemote5.hosts] and is checked by
`_connect_options` before any connection is attempted.

## Privilege: sudo and su

An entry may carry an optional `privilege` mapping holding elevation settings.
Four keys are recognised, in two pairs:

- `sudo_user` — the run-as user the driver passes to `sudo -u` (since `sudo`
  defaults to `root`, a `sudo_user` of `root` collapses to a bare `sudo`) — and
  `sudo_password`, the sudo password. Both apply only when an operation is
  flagged `sudo=True`.
- `su_user` — the target of a *default* `su <user> -c` (since `su` defaults to
  `root`, a `su_user` of `root`, or none at all, collapses to a bare `su -c`) —
  and `su_password`, the su password. Both apply only when an operation is
  flagged `su=True`. Only default su is supported: no login shell (`su -l`), no
  environment preservation, no shell override.

```python
{
    "host": "web1",
    "connection": {"address": "192.168.0.240", "username": "kjarvis"},
    "privilege": {"sudo_user": "deploy", "sudo_password": "s3cr3t"},
}
```

Privilege parameters come from the inventory, never from a deploy script: an
operation asks to elevate with `sudo=True` and/or `su=True` (see the
[Guide](guide.md#deploy-scripts)) but cannot choose a run-as user or supply a
password.

**Combining the two.** When an operation is flagged both `sudo=True` and
`su=True`, the escalations are applied in a fixed order — `sudo` then `su` — so
`sudo` is the outer escalation and runs `su` inside it:
`sudo [-u <sudo_user>] su <su_user> -c '<command>'`.

**Passwords.** Without `sudo_password`, sudo elevation assumes passwordless
sudo (NOPASSWD or a cached credential) and runs a bare `sudo`; with one, the
real transport runs `sudo -A` against a generated askpass helper, so the
password is supplied without a TTY (asyncssh channels are non-interactive).
`su` has no askpass mechanism, so with a `su_password` the helper's output is
piped to su's stdin (`env REEMOTE5_SU_PASSWORD=… <helper> | su …`); when that
pipeline also runs under `sudo` it is wrapped in `sh -c` so the pipe stays
intact inside sudo's elevation. Passwords are plain text in the inventory — the
only supported form — and are shell-quoted into the command sent over the
encrypted channel; they never appear in the rendered apply log, which shows the
credential-free `sudo -u <user> …` / `su <user> -c …` form.

An applied command that exits non-zero raises
[`CommandError`][reemote5.hosts.CommandError] (fail-fast), carrying the host,
the credential-free command text, the exit status and the captured stderr, so a
permission error or a failed package transaction surfaces instead of being
silently logged as applied. Fact gathering does not raise it: reads tolerate a
non-zero exit (e.g. `readlink` on an absent link).

### A rejected credential

A failure sudo or su reports as its *own* authentication rejection — a wrong
password, a missing one where it is demanded, or a run-as user the login user
may not become — raises
[`PrivilegeError`][reemote5.hosts.PrivilegeError] instead: the privilege
counterpart of [`HostConnectionError`][reemote5.hosts.HostConnectionError],
reported the same way.

reemote5 invokes `sudo -A` exactly once, but sudo re-runs the askpass helper up
to its own `passwd_tries` limit (3 by default), so one bad password costs three
identical attempts and three `Sorry, try again.` lines before sudo gives up with
`sudo: 3 incorrect password attempts`. (`su` does not retry — it fails at once.)
Matching those signatures — for both Todd Miller's sudo and sudo-rs, and su's
`Authentication failure` — collapses that into a single error whose message
names the affected host(s) and nothing else, so neither the retry noise nor the
password reaches the report. The detail is preserved for diagnosis rather than
discarded: `failures` carries `(host_name, command, cause)` triples, where the
command is the credential-free rendering and the cause is the `CommandError`
holding the exit status and captured stderr (also chained as `__cause__`).
[`run_apply`][reemote5.drivers.run_apply] merges the per-host failures of a
concurrent lockstep step into one aggregate via `PrivilegeError.aggregate`, so
every host whose credentials were rejected is reported at once.

::: reemote5.hosts
    options:
      members:
        - CommandError
        - Host
        - HostConnectionError
        - InventoryEntry
        - InventoryError
        - PrivilegeError
        - validate_inventory

