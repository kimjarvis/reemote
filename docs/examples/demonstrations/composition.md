# Composing deploys

Scripts under `examples/demonstrations/composition/` — scenarios that cross
module boundaries to demonstrate the composition helpers
[`sub_deploy`][reemote.core.sub_deploy] and [`compose`][reemote.core.compose]
— one scenario each, in the order the [guide](../../guide.md#composing-deploys)
introduces them. Both helpers sequence **request streams only**: `compose` and
`sub_deploy` reject a barrier with `DeployRequestError`, so a deploy that needs
a mid-run read composes with `yield from` under
[`run_waves`][reemote.drivers.run_waves] instead — the
[wave demonstrations](waves.md). Alongside the pure-composition scenarios sit
a role-selection scenario keyed on the inventory's `group`/`tags`, a
`when`-guard scenario that keeps one shared schedule across hosts, and three
two-deploy scenarios. Those three move a mutable read — a fact snapshot or the
apply report — into the gap between two complete `run()` prepare/apply cycles:
the **barrier-free** idiom, legitimate precisely because `run()` still gives a
whole-deploy `dry=True` preview a mid-stream barrier cannot. Each has a
one-stream `run_waves` twin under [examples/demonstrations/waves/](waves.md).
All read the shared
project-root `inventory.json` (validated up front) and guard their `dnf` work
to the RedHat family with a stable `is_redhat` predicate, so the Alpine and
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

On the barrier path this same scenario is one stream rather than two runs: the
`DnfPackages` read becomes a `fact` barrier and the configure branch moves inside
the body. Its runnable twin is
[Branching on a `fact` barrier](waves.md#branching-on-a-fact-barrier-in-one-stream)
(`examples/demonstrations/waves/barrier_fact.py`), which trades this script's
whole-deploy `dry=True` preview for a live mid-run read. The two-phase rule the
docstring below states is a `run()` rule, not an absolute one.

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

The cleanup has the same one-stream twin: under `run_waves` the `FindFiles`
enumeration is a single `fact` barrier and the subtract-and-remove loop follows it
in the body — see
[Branching on a `fact` barrier](waves.md#branching-on-a-fact-barrier-in-one-stream)
(`examples/demonstrations/waves/barrier_fact.py`). This script keeps the split for
the `dry=True` preview of the removal list, which a mid-stream barrier marks
`unknown until run`; it is also the shape the `datadog` role reaches for the same
question on the barrier-free path.

::: conf_d_cleanup
    options:
      members: []

## Restarting a service only when a notifying operation changed

The Ansible `notify:`/handler pattern — "restart the agent only when a
config write actually changed something" — built from the apply report
instead of a fact snapshot, with the reusable helpers
[`changed_hosts`][reemote.handlers.changed_hosts] and
[`restart_handler`][reemote.handlers.restart_handler] shipped in
`reemote.handlers` so a role migration does not hand-roll them.
`systemd.service(restarted=True)` is
stateless: it restarts a running unit on every deploy that reaches it,
and neither `when` (static inventory data only) nor the deploy body (no
access to earlier outcomes) can condition it on a change. Deploy 1
(`configure_nginx`) installs nginx and writes a content-idempotent
`conf.d` drop-in with `files.install`, both requests named after the
script's `NOTIFYING` set; between the runs, `changed_hosts(report, NOTIFYING)`
freezes the hosts whose named entries report `changed` in run 1's
`ApplyReport`; deploy 2 is built by
`restart_handler(changed, "nginx", when=is_redhat)` closing over that
frozen set and yields `systemd.service(restarted=True)` only for those
hosts. Deduplication and
end-of-run flushing come free — at most one restart per host, after every
config write landed — and a converged re-run queues no restart at all.
The guide's [Change notification](../../guide.md#change-notification-handlers)
section spells out why this is the sanctioned shape.

The handler has an in-stream twin: under `run_waves` the notification read is
`changed_hosts(yield checkpoint(...), NOTIFYING)`, so no frozen snapshot or second
run is needed — see
[A handler as an in-stream `checkpoint`](waves.md#a-handler-as-an-in-stream-checkpoint)
(`examples/demonstrations/waves/checkpoint_handler.py`). This two-`run()` split
stays the sanctioned shape on the barrier-free path, where a plain deploy body
cannot observe its own earlier outcomes.

::: handler_restart
    options:
      members: []
