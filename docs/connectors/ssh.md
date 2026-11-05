# The ssh connector

The ssh connector is the **default transport**: a deploy runs over SSH on a
real remote machine through asyncssh, mirroring pyinfra's `@ssh` connector.
It is selected per inventory entry with `connection.connector` — or simply
by omitting the key, since `"ssh"` is the default:

```python
inventory = [
    {
        "host": "web1",
        "connection": {
            "address": "192.168.0.240",
            "username": "kjarvis",
            "key_path": "~/.ssh/id_ed25519",
        },
    },
]
```

<!---

[`Host.from_inventory`][reemote5.hosts.Host.from_inventory] materializes the
entry into a plain [`Host`][reemote5.hosts.Host], which owns a lazily
established asyncssh connection built from its `connection` data: `connect`
opens (and caches) it, `run_shell` runs one command over it, `upload_file`
and `download_file` copy through asyncssh's scp, and `aclose` tears it down.
The docker connector overrides exactly these seams, so a deploy script is
written once and runs against either kind of target.

## Controller prerequisites

Everything runs through **asyncssh in-process** — no local `ssh` binary and
no ssh-agent are required (asyncssh speaks the protocol itself and can read
OpenSSH config and key files directly). The one exception is
`proxy_command`: that string is executed as a local command on the control
machine, so a jump-host setup using `ssh -W %h:%p bastion` does need a local
`ssh` binary. Channels are non-interactive (no TTY), which is why
keyboard-interactive auth is forbidden and sudo/su passwords ride generated
askpass helpers instead of prompts.

A transport-level failure — an `OSError` (reset, timeout, unreachable) or an
`asyncssh.Error` (host-key or authentication rejection) — surfaces as
[`HostConnectionError`][reemote5.hosts.HostConnectionError] labelled `SSH`,
aggregated across the inventory by
[`run_prepare`][reemote5.drivers.run_prepare] so every unreachable host is
reported at once. The message stays generic (which hosts could not be
reached); the original error is chained as `__cause__` for diagnosis.

--->

## Connection options

Unlike the docker connector's closed key set, the ssh `connection` mapping
keeps **asyncssh's own key names** and forwards a curated set of
`asyncssh.connect` options verbatim. Forwarding is presence-based, so an
explicit `None` is meaningful (e.g. `known_hosts=None` skips host key
checking, `client_keys=None` loads no default identities). Path-valued
options (`key_path`, `client_keys`, `agent_path`, `config`, `identities`,
`known_hosts`) have `~` expanded before they reach asyncssh.

| Option | Purpose |
|---|---|
| `connector` | `"ssh"` (the default) selects this connector; may be omitted |
| `address` / `host` / `hostname` | The SSH target address — required to connect |
| `username` / `user` | Login name |
| `port` | TCP port to connect to |
| `password` | Password authentication |
| `key_path` | Single client private key (shorthand for `client_keys`) |
| `client_keys` | Client private keys (list); `None` loads no default identities |
| `passphrase` | Passphrase for encrypted keys |
| `identities` | Certificates/identities to present |
| `agent_path` | SSH agent socket (`None` uses the default) |
| `agent_forwarding` | Forward the agent to the remote host |
| `config` | OpenSSH config file path(s), or `True` for the default |
| `known_hosts` | Known-hosts file path(s); `None` skips host key checking |
| `proxy_command` | Jump-host `ProxyCommand` string, run on the controller |
| `tunnel` | Bastion tunnel, as `"[user@]host[:port]"` or a connection |
| `kex_algs` | Key-exchange algorithm pinning for compliance or legacy servers |
| `encryption_algs` | Cipher algorithm pinning |
| `mac_algs` | MAC algorithm pinning |
| `server_host_key_algs` | Host-key algorithm pinning |
| `preferred_auth` | Auth method order, e.g. `["publickey", "password"]` |
| `login_timeout` | Auth window in seconds |
| `keepalive_interval` | Seconds between SSH-level keepalive probes |
| `keepalive_count_max` | Unanswered keepalives before the connection is dropped |
| `tcp_keepalive` | Enable TCP-level keepalives |
| `asyncssh_connect_kwargs` | Passthrough mapping for any other `asyncssh.connect` option, merged last |

<!---

Anything asyncssh supports that is not first-class above — GSSAPI, X.509,
socket plumbing, rekeying and the like — can still be passed through the
`asyncssh_connect_kwargs` mapping, which is merged last and may therefore
add to or override the curated options, for example:

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

A key that is neither curated nor passed through is not forwarded — but one
option is *expressly forbidden* rather than merely omitted.

### Forbidden options

`kbdint_auth` (keyboard-interactive authentication) raises
[`InventoryError`][reemote5.hosts.InventoryError] instead of being silently
dropped or passed on to asyncssh: it produces interactive challenge/MFA
prompts, which asyncssh's non-interactive channels cannot answer, so
reemote5 does not support keyboard-interactive auth at all. The rejection
covers both the top-level `connection` mapping and the
`asyncssh_connect_kwargs` passthrough — because that mapping is merged last,
a forbidden key cannot be smuggled through it either. The check runs in
`_connect_options` before any connection is attempted.

## Lifecycle

The connection is opened lazily on first use and cached on the instance;
[`run_prepare`][reemote5.drivers.run_prepare] connects the whole inventory
concurrently, and [`run`][reemote5.drivers.run] closes every host when it
finishes. `aclose` first removes any askpass helpers created for
`sudo_password`/`su_password` (best-effort — a dropped connection must not
fail teardown), then closes the connection and waits for it to finish.

!!! note "A connection without an address"

    An entry whose `connection` carries no address (e.g. one only ever
    driven through the test framework's `SimulatedHost`) raises
    `NotImplementedError` when a real connection is attempted, rather than
    silently resolving the logical host name.

## Execution, transfers and privilege

Commands run over asyncssh's non-interactive channels (no TTY, exactly like
the docker connector's `exec -i`). Remote exit codes propagate verbatim, so
exit 127 still raises
[`CommandNotFoundError`][reemote5.hosts.CommandNotFoundError] on every path
and the apply path still fails fast with
[`CommandError`][reemote5.hosts.CommandError].

[`files.put`][reemote5.modules.files.files.put] and
[`files.get`][reemote5.modules.files.files.get] ride asyncssh's scp through a
temporary local file (scp copies between paths, not streams); a failed copy
raises `CommandError` with no exit status, since scp reports an exception
rather than a shell exit. Transfers run as the login user — no privilege
escalation applies to them.

`sudo`/`su`/`doas` elevation comes from the entry's `privilege` mapping and
combines in the fixed order `doas` then `sudo` then `su`. Passwords ride
generated askpass helpers (sudo's `SUDO_ASKPASS`, a stdin pipe for su; doas
is passwordless-only, `doas -n`), never appear in the rendered apply log,
and a rejected credential raises
[`PrivilegeError`][reemote5.hosts.PrivilegeError] rather than a generic
`CommandError` — see [Hosts and the inventory](../hosts.md) for the full
privilege contract.

::: reemote5.hosts
    options:
      members:
        - Host
        - HostConnectionError
        - InventoryError

-->