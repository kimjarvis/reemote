# Examples

Runnable showcase scripts living in `examples/` — *usage* demonstrations
through the public API only, never part of the library. The tree is split in
four: `examples/modules/<namespace>/` holds one script per fact or operation
of a library module (currently `dnf`, `files`, `hardware` and `server`),
`examples/general/`
holds general scenario scripts built on more than one module,
`examples/actions/` holds bespoke-shell [action](actions.md) demonstrations, and
`examples/demonstrations/<topic>/` holds scenario scripts — `composition/`,
`connectors/`, `inventory/` and `privilege/` — that cross module boundaries.
Each explanation
on the pages below is the example module's **own docstring**, rendered with
mkdocstrings, so the script stays the single source of truth: update the
docstring and the page follows.

The documentation mirrors that directory layout — one page per examples
sub-directory. Pick a page from the navigation, or start from this table:

| Examples directory | Documentation page |
| --- | --- |
| `examples/modules/dnf/` | [Module examples: dnf](examples/modules/dnf.md) |
| `examples/modules/files/` | [Module examples: files](examples/modules/files.md) |
| `examples/modules/hardware/` | [Module examples: hardware](examples/modules/hardware.md) |
| `examples/modules/server/` | [Module examples: server](examples/modules/server.md) |
| `examples/general/` | [General examples](examples/general.md) |
| `examples/actions/` | [Action examples](examples/actions.md) |
| `examples/demonstrations/inventory/` | [Inventory demonstrations](examples/demonstrations/inventory.md) |
| `examples/demonstrations/privilege/` | [Privilege demonstrations](examples/demonstrations/privilege.md) |
| `examples/demonstrations/composition/` | [Composing deploys](examples/demonstrations/composition.md) |
| `examples/demonstrations/connectors/` | [Connector demonstrations](examples/demonstrations/connectors.md) |

!!! warning "The operation examples connect for real"

    Every script on these pages uses the *default* `Host.from_inventory`
    factory and an inventory of real machines — hard-coded as
    `INVENTORY` in the script (most target the two hosts web1/web2 on
    `192.168.0.x`; the two [`privilege`](examples/demonstrations/privilege.md)
    demonstrations each target a single `centos9-*` host and carry a
    plain-text password), or, for
    [`json_inventory`](examples/demonstrations/inventory.md#inventory-from-a-json-file),
    read from `inventory.json` in the project root — so running one opens
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
    starts a real local container, so it needs a `docker` (or `podman`) CLI
    with a reachable daemon on the control machine, and each run commits a
    new image (the dry run included).

Each real-host script also owns its *error reporting*, because the library is
CLI-free and only raises: `main` catches the typed errors a run can produce —
[`HostConnectionError`][reemote5.hosts.HostConnectionError] for a host that
cannot be reached, [`PrivilegeError`][reemote5.hosts.PrivilegeError] for a
rejected elevation, [`CommandError`][reemote5.hosts.CommandError] for a command
that exits non-zero — including its
[`CommandNotFoundError`][reemote5.hosts.CommandNotFoundError] subclass when the
command is not installed on the host — plus
[`OperationError`][reemote5.core.OperationError] when an operation's
prerequisites are not met (the timezone example on a non-systemd host),
[`WaitTimeoutError`][reemote5.commands.WaitTimeoutError]
in the polling example and [`InventoryError`][reemote5.hosts.InventoryError] in
the JSON-inventory one — prints the message on stderr and exits non-zero. The
message already names the host and, for a failed command, the credential-free
command text and its stderr, so a failing run reports the reason instead of a
Python traceback.

The [action](actions.md) examples follow the same contract with one difference:
a bespoke command's own non-zero exit is **data in the
[`Result`][reemote5.actions.Result]**, not a raised `CommandError` — only
library-level failures (`HostConnectionError`, `PrivilegeError`) raise. The
in-deploy guard example catches
[`ActionInDeployError`][reemote5.actions.ActionInDeployError] as its *expected*
outcome and exits zero.
