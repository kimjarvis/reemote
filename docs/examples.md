# Examples

<!---

Runnable showcase scripts living in `examples/` — *usage* demonstrations
through the public API only, never part of the library. The tree is split in
three: `examples/modules/<namespace>/` holds one script per fact or operation
of a library module (currently `apk`, `apt`, `dnf`, `files`, `firewalld`,
`flatpak`, `git`, `gpg`, `hardware`, `iptables`, `lxd`, `mysql`, `openrc`, `postgresql`, `selinux`,
`server`, `snap`, `sysctl`, `systemd` and `ufw`), plus the cross-module `dnf_put_get`
health-check scenario under `files/`,
`examples/actions/` holds [action](actions.md) demonstrations (bespoke shell,
staged scripts, connection waits), and
`examples/demonstrations/<topic>/` holds scenario scripts — `composition/`,
`connectors/` and `privilege/` — that cross module boundaries.
Each explanation
on the pages below is the example module's **own docstring**, rendered with
mkdocstrings, so the script stays the single source of truth: update the
docstring and the page follows.

The documentation mirrors that directory layout — one page per examples
sub-directory. Pick a page from the navigation, or start from this table:

| Examples directory | Documentation page |
| --- | --- |
| `examples/modules/apk/` | [Module examples: apk](examples/modules/apk.md) |
| `examples/modules/apt/` | [Module examples: apt](examples/modules/apt.md) |
| `examples/modules/dnf/` | [Module examples: dnf](examples/modules/dnf.md) |
| `examples/modules/files/` | [Module examples: files](examples/modules/files.md) |
| `examples/modules/firewalld/` | [Module examples: firewalld](examples/modules/firewalld.md) |
| `examples/modules/flatpak/` | [Module examples: flatpak](examples/modules/flatpak.md) |
| `examples/modules/git/` | [Module examples: git](examples/modules/git.md) |
| `examples/modules/gpg/` | [Module examples: gpg](examples/modules/gpg.md) |
| `examples/modules/hardware/` | [Module examples: hardware](examples/modules/hardware.md) |
| `examples/modules/iptables/` | [Module examples: iptables](examples/modules/iptables.md) |
| `examples/modules/lxd/` | [Module examples: lxd](examples/modules/lxd.md) |
| `examples/modules/mysql/` | [Module examples: mysql](examples/modules/mysql.md) |
| `examples/modules/openrc/` | [Module examples: openrc](examples/modules/openrc.md) |
| `examples/modules/postgresql/` | [Module examples: postgresql](examples/modules/postgresql.md) |
| `examples/modules/selinux/` | [Module examples: selinux](examples/modules/selinux.md) |
| `examples/modules/server/` | [Module examples: server](examples/modules/server.md) |
| `examples/modules/snap/` | [Module examples: snap](examples/modules/snap.md) |
| `examples/modules/sysctl/` | [Module examples: sysctl](examples/modules/sysctl.md) |
| `examples/modules/systemd/` | [Module examples: systemd](examples/modules/systemd.md) |
| `examples/modules/ufw/` | [Module examples: ufw](examples/modules/ufw.md) |
| `examples/actions/` | [Action examples](examples/actions.md) |
| `examples/demonstrations/privilege/` | [Privilege demonstrations](examples/demonstrations/privilege.md) |
| `examples/demonstrations/composition/` | [Composing deploys](examples/demonstrations/composition.md) |
| `examples/demonstrations/connectors/` | [Connector demonstrations](examples/demonstrations/connectors.md) |

!!! warning "The operation examples connect for real"

    Every script on these pages uses the *default* `Host.from_inventory`
    factory and an inventory of real machines — read from the shared
    `inventory.json` in the project root (each script loads it with
    `load_inventory`, validated up front with `validate_inventory`). The
    default guard is `is_centos` (`host.group == "centos"`), keeping every
    gather and request on the two CentOS hosts (`web1`, `web2`) and
    skipping hosts in other groups with a visible reason. The
    [`apk` examples](examples/modules/apk.md) and the
    [`openrc` examples](examples/modules/openrc.md) guard on `is_alpine`
    instead (the single `alpine` host), the
    [`apt` examples](examples/modules/apt.md) and the
    [`flatpak` examples](examples/modules/flatpak.md) on `is_ubuntu` (the
    single
    `ubuntu` host), and the `server_op_locale.py` example on the
    [server page](examples/modules/server.md) likewise on `is_ubuntu`
    because `/etc/locale.gen` + `locale-gen` are Debian-family only. The
    [`composition` demonstrations](examples/demonstrations/composition.md)
    (whose `is_centos` guard keeps the `dnf` work on the `centos` hosts), the
    [`privilege` demonstrations](examples/demonstrations/privilege.md)
    (whose `is_centos` guard likewise keeps the `dnf` work on the `centos`
    hosts, each attaching its own `privilege` section — a plain-text password
    for sudo and su, passwordless doas — to the loaded `centos` entries) and
    the [`action` examples](examples/actions.md) (whose `is_centos` guard is
    composed onto the deploys and filters the host list before each action
    fan-out) follow the same convention — so running one opens
    real asyncssh connections: first read-only for the `dry=True` plan,
    then executing the apply phase. Edit the inventory (or keep only the
    dry run) to point at your own machines before executing them. The
    pytest suite never touches the network: it injects the test
    framework's `SimulatedHost` via `run`'s `host_factory`.

    The one exception is
    [`composed_group_tag_roles`](examples/demonstrations/composition.md#group-and-tag-roles-from-inventory-data),
    which does the same injection — it runs entirely in memory against
    `SimulatedHost`, so it opens no connection and needs no real hosts.

    The [`action_in_deploy_guard`](examples/actions.md#the-in-deploy-guard)
    example also opens no connection, though it uses the default factory and a
    real-looking inventory: the in-deploy guard fires during prepare, before
    any fact is gathered, so it runs offline and treats the
    `ActionInDeployError` as the expected outcome.

    The [`docker_files`](examples/demonstrations/connectors.md) connector
    demonstration also needs no SSH host, but it is *not* in-memory: it
    keeps its own inline inventory of local containers instead of reading
    the shared `inventory.json`, and starts a real local container, so it
    needs a `docker` (or `podman`) CLI
    with a reachable daemon on the control machine, and each run commits a
    new image (the dry run included).

Each real-host script also owns its *error reporting*, because the library is
CLI-free and only raises: `main` catches the typed errors a run can produce —
[`HostConnectionError`][reemote.hosts.HostConnectionError] for a host that
cannot be reached, [`PrivilegeError`][reemote.hosts.PrivilegeError] for a
rejected elevation, [`CommandError`][reemote.hosts.CommandError] for a command
that exits non-zero — including its
[`CommandNotFoundError`][reemote.hosts.CommandNotFoundError] subclass when the
command is not installed on the host — plus
[`OperationError`][reemote.core.OperationError] when an operation's
prerequisites are not met (the timezone example on a non-systemd host),
[`WaitTimeoutError`][reemote.commands.WaitTimeoutError]
in the polling example and [`InventoryError`][reemote.hosts.InventoryError]
(together with Python's own `OSError`/`json.JSONDecodeError` for the file
itself) in every SSH example — they all read their inventory from a JSON
file — prints the
message on stderr and exits non-zero. The
message already names the host and, for a failed command, the credential-free
command text and its stderr, so a failing run reports the reason instead of a
Python traceback.

The [action](actions.md) examples follow the same contract with one difference:
a bespoke command's own non-zero exit is **data in the
[`Result`][reemote.actions.Result]**, not a raised `CommandError` — only
library-level failures (`HostConnectionError`, `PrivilegeError`,
`ConnectionTimeoutError`) raise. The in-deploy guard example catches
[`ActionInDeployError`][reemote.actions.ActionInDeployError] as its *expected*
outcome and exits zero, and the connection-wait example likewise treats the
[`ConnectionTimeoutError`][reemote.actions.ConnectionTimeoutError] from a
deliberately unreachable TEST-NET address as its expected demonstration.

--->