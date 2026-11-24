# Barriers

<!---

`reemote.stages` holds the stream types the wave-loop driver
[`run_waves`][reemote.drivers.run_waves] consumes. A *role* is a deploy
factory: an ordinary function that closes over resolved
[variables](rolevars.md) and returns a per-host generator. That generator
yields two kinds of item — an
[`OperationRequest`][reemote.core.OperationRequest] (exactly what a
[`run`][reemote.drivers.run] deploy yields today) and a *barrier*.

A barrier parks the generator until the driver executes it and resumes the body
with the result via `gen.send(value)`. There are four, because they are the four
things a **synchronous** generator body cannot do for itself: read a fact, run an
action, close a wave and read its report, and await control-side IO.
[`Barrier`][reemote.stages.Barrier] is the union of the four — a PEP 604 union,
so the driver classifies an item with one `isinstance` — and
[`StageItem`][reemote.stages.StageItem] widens it with `OperationRequest`.
[`Deploy`][reemote.stages.Deploy] is the per-host generator signature a factory
returns; an `async def` factory returns exactly this type once awaited.

| Barrier | Constructor | Sends back | Host contact |
|---|---|---|---|
| [`FactRequest`][reemote.stages.FactRequest] | [`fact`][reemote.stages.fact] | the fact's value | one host |
| [`ActionRequest`][reemote.stages.ActionRequest] | [`act`][reemote.stages.act] | a [`Result`][reemote.actions.Result] | one host |
| [`CheckpointRequest`][reemote.stages.CheckpointRequest] | [`checkpoint`][reemote.stages.checkpoint] | the wave's [`ApplyReport`][reemote.drivers.ApplyReport], sliced to scope | none |
| [`ControlRequest`][reemote.stages.ControlRequest] | [`control`][reemote.stages.control] | the coroutine's return value | controller |

## The four barriers

- **`fact(FileExists, "/etc/motd")`** reads remote state into the body's control
  flow. The driver runs `await fact_cls.process(host, *args, **kwargs)` for the
  parked host and sends the value back; every parked host runs its own fact
  concurrently at the rendezvous, reproducing
  [`gather_fact`][reemote.drivers.gather_fact]'s fan-out without a host set. A
  fact is a *read* — executed, never simulated — and involves no
  [`FactCache`][reemote.core.FactCache]: a barrier is an explicit, fresh read,
  not an operation's own materialization-time read.
- **`act(Shell, command="apache2 -v")`** runs an action now and sends the
  outcome back. A non-zero exit is **data** in the
  [`Result`][reemote.actions.Result], not an exception, matching the
  [action contract](actions/actions.md); transport and privilege failures still
  raise. Because a barrier is executed by the driver *outside* the pull, a
  `gather_action(...)` call written directly in the body still raises
  `ActionInDeployError` — the in-deploy guard is held during pulls only.
- **`checkpoint("configure done")`** touches no host. It closes the current wave
  and sends back an [`ApplyReport`][reemote.drivers.ApplyReport] holding the log
  entries of the wave that just ended, filtered to entries whose `scope` starts
  with the checkpoint's own scope. The `yield` expression *is* an `ApplyReport`
  — the same type [`run`][reemote.drivers.run] returns — so
  [`changed_hosts`][reemote.handlers.changed_hosts] reads it unchanged. A
  checkpoint must immediately follow the stage it reports on: two checkpoints in
  a row mean the second closes a wave with no requests and reads an empty report.
- **`control(files.fetch_url, url)`** awaits an async callable on the
  **controller**, not on a host, and sends its return value back. It exists for
  control-side IO whose input is per-host state — a variable entry, a fact, a
  report — and so cannot be hoisted out of the per-host generator. It runs once
  per *host* parked on it, which is exactly why an inventory-wide fetch belongs
  in an `async def` factory instead.

## Scope, not name

[`scoped`][reemote.stages.scoped] tags every item a deploy yields with a report
path:

```python
yield from scoped("apache", apache(v))(host)
```

It stamps `("apache",)` onto the front of each item's `scope` tuple, so the
report tree and a [`checkpoint`][reemote.stages.checkpoint] slice can tell which
role emitted an item — **without** rewriting
[`OperationRequest.name`][reemote.core.OperationRequest], which
[`changed_hosts`][reemote.handlers.changed_hosts] matches on. `scope` is
report-only: it is excluded from the dedupe/effective identity a request is
compared by. The wrapper forwards `send()` values inward and propagates the
inner generator's return value outward, so barriers inside a scoped deploy
receive their results and a parent captures the child's outputs with
`yield from`.

## Contract rules

- **Roles compose with `yield from`, not the request helpers.**
  [`compose`][reemote.core.compose] and [`sub_deploy`][reemote.core.sub_deploy]
  sequence `OperationRequest`s only; a barrier handed to either raises
  [`DeployRequestError`][reemote.core.DeployRequestError] naming the wrapper and
  the barrier type.
- **A built barrier must be yielded.** Each constructor charges the deploy body's
  built-versus-yielded counter, the same one [`op`][reemote.core.op] charges, so
  a barrier built and then dropped is reported as a lost request when the
  generator runs out.
- **A role must not depend on itself.** There is no de-duplication and no static
  dependency graph, so a `yield from` cycle degrades to a `RecursionError` that
  the driver catches at the pull site and re-raises as
  [`RoleRecursionError`][reemote.stages.RoleRecursionError], naming the host and
  the mistake.
- **`run_waves` owns the hosts.** Barriers never close a connection, and a role
  must not either; the driver closes every host once in its own `finally`.

A `dry=True` run is a **one-wave** preview: no barrier ever runs, and the first
parked barrier is recorded `unknown` and stops the loop, because a barrier's
outcome may depend on requests that were planned but never applied. See
[`run_waves`][reemote.drivers.run_waves] for the loop, the
[`WaveReport`][reemote.drivers.WaveReport] it returns and
[`format_waves`][reemote.drivers.format_waves] that renders it, and the guide's
[Roles are resumable deploys](guide.md#roles-are-resumable-deploys) for the
narrative.

--->

::: reemote.stages
    options:
      # Skip the ``reemote.stages`` module root heading; the four barrier
      # classes render directly under the page title.
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - FactRequest
        - ActionRequest
        - CheckpointRequest
        - ControlRequest
      # Render each barrier's signature and docstring only; the frozen dataclass
      # fields (fact_cls, action_cls, func, scope, name, args, kwargs) are already
      # described in the class signature and are not part of the documented API.
      filters:
        - "!^_"
        - "!^(fact_cls|action_cls|func|scope|name|args|kwargs)$"
