# Composing deploys

Scripts under `examples/demonstrations/composition/` — scenarios that cross
module boundaries to demonstrate the composition helpers
[`sub_deploy`][reemote5.core.sub_deploy] and [`compose`][reemote5.core.compose]
— one scenario each, in the order the [guide](../../guide.md#composing-deploys)
introduces them — plus a role-selection scenario keyed on the inventory's
`group`/`tags`, a `when`-guard scenario that keeps one shared schedule
across hosts, and a two-deploy split that moves a mutable-fact read into
the gap between two complete prepare/apply cycles. All read the shared
project-root `inventory.json` (validated up front) and guard their `dnf` work
to the `centos` group with a stable `is_centos` predicate, so the Alpine and
Ubuntu hosts are skipped without their facts being read; every one follows the
same dry-run-then-apply flow, and
`composed_group_tag_roles` runs in memory against `SimulatedHost` instead of
over SSH, selecting `dnf`/`apt`/`apk` roles per group. See the
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

## A shared schedule with when guards

::: composed_when_guard
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

## Splitting a fact-dependent deploy in two

A task the determinism rule forbids as a single deploy — "install nginx,
then configure it only if the install actually put it on the box" — made
legal by moving the mutable-fact read into the gap between two complete
prepare/apply cycles. Deploy 1 (`install_nginx`) is branch-free; a
`gather_fact` snapshot of `dnf.DnfPackages` taken after its apply-phase join
captures the real post-install state per host; deploy 2 is built by a
factory closing over that frozen dict, so its branch is a pure function of
static input and both phases yield the identical request sequence. The
second run's `dry=True` plan is an honest preview of the configure step,
computed against the state the install actually left behind. The config is
staged with `files.put` to `/tmp` (transfers are never elevated); the
trade-off — a frozen snapshot, no mid-run replanning — is spelled out in the
script's own docstring below.

::: two_deploy_split
    options:
      members: []
