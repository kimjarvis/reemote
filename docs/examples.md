# Examples

Runnable showcase scripts living in `examples/` — *usage* demonstrations
through the public API only, never part of the library. The tree is split in
two: `examples/modules/<namespace>/` holds one script per fact or operation
of a library module (currently `dnf`), and `examples/demonstrations/<topic>/`
holds scenario scripts — `composition/`, `inventory/` and `privilege/` — that
cross module boundaries. Each explanation below is the example module's
**own docstring**, rendered here with mkdocstrings, so the script stays the
single source of truth: update the docstring and this page follows.

!!! warning "The operation examples connect for real"

    Every script below uses the *default* `Host.from_inventory`
    factory and an inventory of real machines — hard-coded as
    `INVENTORY` in the script (most target the two hosts web1/web2 on
    `192.168.0.x`; the two [`privilege`](#privilege) demonstrations each
    target a single `centos9-*` host and carry a plain-text password), or,
    for [`json_inventory`](#inventory-from-a-json-file), read from
    `inventory.json` in the project root — so running one opens real
    asyncssh connections: first read-only for the `dry=True` plan, then
    executing the apply phase. Edit the inventory (or keep only the dry
    run) to point at your own machines before executing them. The pytest
    suite never touches the network: it injects the test framework's
    `SimulatedHost` via `run`'s `host_factory`.

    The one exception below is
    [`composed_group_tag_roles`](#group-and-tag-roles-from-inventory-data),
    which does the same injection — it runs entirely in memory against
    `SimulatedHost`, so it opens no connection and needs no real hosts.

The read-only fact-gathering examples (`DnfRepositories`,
`DnfEnabledModules`, `DnfDisabledModules` over `gather_fact`) live alongside
the base example in `examples/modules/dnf/`.

## Module examples: dnf

### The base example

The starting point for everything below: a single flat deploy generator
installing httpd and removing the welcome configuration the package drops,
run end to end over SSH in both phases.

::: dnf_packages
    options:
      members: []

## Demonstrations

### Inventory from a JSON file

The same deploy as the base example, with the target hosts kept in
`inventory.json` instead of in the source: read the file, validate it with
[`validate_inventory`][reemote5.hosts.validate_inventory], and hand the
plain list of dictionaries to `run`.

::: json_inventory
    options:
      members: []

### Privilege

#### Elevating with a sudo password

The base example's deploy, unchanged, run against a single host whose
inventory carries a `privilege` section — the run-as user and a plain-text
`sudo_password`. The operations still only *ask* to elevate with `sudo=True`;
the credential comes from the inventory alone, and the driver supplies it
through a generated askpass helper (`sudo -A`, no TTY) without ever logging it.
It also shows how a rejected credential is reported: `main` catches
[`PrivilegeError`][reemote5.hosts.PrivilegeError] and prints one line naming the
host, instead of surfacing sudo's three password retries as a traceback.

::: privilege_sudo_pw
    options:
      members: []

#### Elevating with su

The `su` counterpart of the sudo-password example: the same deploy shape, run
against a single host whose inventory `privilege` section carries a `su_user`
and a plain-text `su_password`. The operations only *ask* to elevate with
`su=True`; reemote5 runs a *default* `su <user> -c` (no login shell) and, since
`su` has no askpass mechanism, pipes the password to su's stdin through a
generated helper. The credential comes from the inventory alone and never
reaches the log, and a rejected password surfaces as a single
[`PrivilegeError`][reemote5.hosts.PrivilegeError] naming the host.

::: privilege_su
    options:
      members: []

### Composing deploys

The seven scripts below demonstrate the composition helpers
[`sub_deploy`][reemote5.core.sub_deploy] and [`compose`][reemote5.core.compose]
— one scenario each, in the order the [guide](guide.md#composing-deploys)
introduces them — plus a role-selection scenario keyed on the inventory's
`group`/`tags`. All but the last reuse the base example's real-SSH inventory;
every one follows the same dry-run-then-apply flow, and the last runs in memory
against `SimulatedHost` instead of over SSH.

#### A shared baseline, deduplicated

::: composed_baseline_dedupe
    options:
      members: []

#### Layered environments from static configuration

::: composed_environments
    options:
      members: []

#### A privilege policy over a third-party role

::: composed_sudo_policy
    options:
      members: []

#### Ordering: repositories before packages

::: composed_repo_ordering
    options:
      members: []

#### Per-host roles from inventory data

::: composed_per_host_roles
    options:
      members: []

#### Group and tag roles from inventory data

::: composed_group_tag_roles
    options:
      members: []

#### Patch windows: one package list, two intents

::: composed_patch_window
    options:
      members: []
