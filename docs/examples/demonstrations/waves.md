# Wave demonstrations

Scripts under `examples/demonstrations/waves/` — one scenario each for the five
wave primitives (`fact`, `act`, `checkpoint`, `control` and `scoped`, built by
the constructors documented in [Stages](../../stages.md)) plus typed variables
([`resolve_vars`][reemote.rolevars.resolve_vars]), the
[`run_waves`][reemote.drivers.run_waves] driver, the
[`format_waves`][reemote.drivers.format_waves] tree and a narrower `dry` — the
material the [guide](../../guide.md#roles-are-resumable-deploys) introduces but
a 400-line role teaches badly. Each file runs **entirely in memory**: like
[`composed_group_tag_roles`](composition.md), it injects the test framework's
`SimulatedHost` through `run_waves`'s `host_factory`, so no SSH connection is
opened and the shared `inventory.json` is never read — every script carries its
own inline `family`-bearing inventory and asserts its own outcome, printing the
wave tree it built. They are offline-runnable (`python
examples/demonstrations/waves/<name>.py`) and swept by the pytest suite both as
a barrier-aware request build and as an end-to-end subprocess run.

## Branching on a `fact` barrier, in one stream

::: barrier_fact
    options:
      members: []

## A handler as an in-stream `checkpoint`

::: checkpoint_handler
    options:
      members: []

## An `act` probe whose result is data

::: barrier_act
    options:
      members: []

## A `control` fetch whose input comes from a fact

::: control_fetch
    options:
      members: []

## Two instances of one role, no collapse

::: scope_two_instances
    options:
      members: []

## Relaying a child role's outputs to a sibling

::: role_outputs_relay
    options:
      members: []

## Typed role variables and their precedence

::: vars_typed
    options:
      members: []

## What `dry=True` can no longer preview

::: dry_one_wave
    options:
      members: []

## The wave tree, printed

::: run_waves_report
    options:
      members: []
