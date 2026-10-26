# Examples

Runnable showcase scripts living in `examples/` — *usage* demonstrations
through the public API only, never part of the library. Each explanation
below is the example module's **own docstring**, rendered here with
mkdocstrings, so the script stays the single source of truth: update the
docstring and this page follows.

!!! warning "The operation examples connect for real"

    The scripts under `examples/operations/dnf/` use the *default*
    `Host.from_inventory` factory and a two-host inventory of real machines
    (web1/web2 on `192.168.0.x`) — hard-coded as `INVENTORY` in the script,
    or, for [`json_inventory`](#inventory-from-a-json-file), read from
    `inventory.json` in the project root — so running one opens real
    asyncssh connections: first read-only for the `dry=True` plan, then
    executing the apply phase. Edit the inventory (or keep only the dry
    run) to point at your own machines before executing them. The pytest
    suite never touches the network: it injects the test framework's
    `SimulatedHost` via `run`'s `host_factory`.

Read-only fact-gathering examples (`DnfRepositories`, `DnfEnabledModules`,
`DnfDisabledModules` over `gather_fact`) live in `examples/facts/dnf/`.

## The base example

The starting point for everything below: a single flat deploy generator
installing httpd and removing the welcome configuration the package drops,
run end to end over SSH in both phases.

::: dnf_packages
    options:
      members: []

## Inventory from a JSON file

The same deploy as the base example, with the target hosts kept in
`inventory.json` instead of in the source: read the file, validate it with
[`validate_inventory`][reemote5.hosts.validate_inventory], and hand the
plain list of dictionaries to `run`.

::: json_inventory
    options:
      members: []

## Composing deploys

The six scripts below demonstrate the composition helpers
[`sub_deploy`][reemote5.core.sub_deploy] and [`compose`][reemote5.core.compose]
— one scenario each, in the order the [guide](guide.md#composing-deploys)
introduces them. All six reuse the base example's inventory and
dry-run-then-apply flow.

### A shared baseline, deduplicated

::: composed_baseline_dedupe
    options:
      members: []

### Layered environments from static configuration

::: composed_environments
    options:
      members: []

### A privilege policy over a third-party role

::: composed_sudo_policy
    options:
      members: []

### Ordering: repositories before packages

::: composed_repo_ordering
    options:
      members: []

### Per-host roles from inventory data

::: composed_per_host_roles
    options:
      members: []

### Patch windows: one package list, two intents

::: composed_patch_window
    options:
      members: []
