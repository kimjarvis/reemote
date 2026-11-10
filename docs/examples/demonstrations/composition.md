# Composing deploys

Scripts under `examples/demonstrations/composition/` — scenarios that cross
module boundaries to demonstrate the composition helpers
[`sub_deploy`][reemote5.core.sub_deploy] and [`compose`][reemote5.core.compose]
— one scenario each, in the order the [guide](../../guide.md#composing-deploys)
introduces them — plus a role-selection scenario keyed on the inventory's
`group`/`tags`, a `when`-guard scenario that keeps one shared schedule
across hosts, and three two-deploy scenarios that move a mutable read — a
fact snapshot or the apply report — into the gap between two complete
prepare/apply cycles. All read the shared
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

## Cleaning up untracked files: the conf.d recipe

The directory-listing facts' reason to exist: the DataDog Ansible role's
cleanup question — "delete every `conf.d/*/conf.yaml` that is NOT one of
the tracked checks" — where the names to remove are unknown to the deploy
author, so no static deploy can enumerate them. The same two-deploy split
as above, with an enumeration fact instead of a package fact: deploy 1
(`ensure_tracked_checks`) installs the tracked checks' configs with
`files.install` (branch-free); a `gather_fact` snapshot of `files.FindFiles`
(`fname="conf.yaml"`, `maxdepth=2`) taken after its apply-phase join lists
what is really on each box; deploy 2 is built by a factory that subtracts
the tracked paths from the frozen snapshot and yields one `files.file`
`present=False` removal per stale path. The `dry=True` plan previews
exactly what the cleanup would remove, and re-running the whole script
converges to zero removals. The same shape answers the apt legacy-`.list`
cleanup — swap the directory, the filter and the tracked set.

::: conf_d_cleanup
    options:
      members: []

## Restarting a service only when a notifying operation changed

The Ansible `notify:`/handler pattern — "restart the agent only when a
config write actually changed something" — built from the apply report
instead of a fact snapshot. `systemd.service(restarted=True)` is
stateless: it restarts a running unit on every deploy that reaches it,
and neither `when` (static inventory data only) nor the deploy body (no
access to earlier outcomes) can condition it on a change. Deploy 1
(`configure_nginx`) installs nginx and writes a content-idempotent
`conf.d` drop-in with `files.install`, both requests named after the
script's `NOTIFYING` set; between the runs, `notifying_hosts` freezes the
hosts whose named entries report `changed` in run 1's `ApplyReport`;
deploy 2 is built by a factory closing over that frozen set and yields
`systemd.service(restarted=True)` only for those hosts. Deduplication and
end-of-run flushing come free — at most one restart per host, after every
config write landed — and a converged re-run queues no restart at all.
The guide's [Change notification](../../guide.md#change-notification-handlers)
section spells out why this is the sanctioned shape.

::: handler_restart
    options:
      members: []
