# Composing deploys

Scripts under `examples/demonstrations/composition/` — scenarios that cross
module boundaries to demonstrate the composition helpers
[`sub_deploy`][reemote5.core.sub_deploy] and [`compose`][reemote5.core.compose]
— one scenario each, in the order the [guide](../../guide.md#composing-deploys)
introduces them — plus a role-selection scenario keyed on the inventory's
`group`/`tags`. All but the last reuse the base example's real-SSH inventory;
every one follows the same dry-run-then-apply flow, and the last runs in memory
against `SimulatedHost` instead of over SSH. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

## A shared baseline, deduplicated

::: composed_baseline_dedupe
    options:
      members: []

## Layered environments from static configuration

::: composed_environments
    options:
      members: []

## A privilege policy over a third-party role

::: composed_sudo_policy
    options:
      members: []

## Ordering: repositories before packages

::: composed_repo_ordering
    options:
      members: []

## Per-host roles from inventory data

::: composed_per_host_roles
    options:
      members: []

## Group and tag roles from inventory data

::: composed_group_tag_roles
    options:
      members: []

## Patch windows: one package list, two intents

::: composed_patch_window
    options:
      members: []
