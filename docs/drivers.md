# Drivers

`reemote.drivers` is the execution engine. [`run`][reemote.drivers.run] is
the entry point used by deploy scripts and examples; the remaining functions
are exposed so the two phases can be driven, inspected and rendered
separately.

The command-execution path is asynchronous:
[`send_command`][reemote.drivers.send_command] is a coroutine that runs each
command over asyncssh (or forwards to a `SimulatedHost` in tests), and
therefore [`run_apply`][reemote.drivers.run_apply] and
[`run`][reemote.drivers.run] are coroutines as well. Fact gathering is
asynchronous for the same reason — a fact's `process` runs over asyncssh — so
[`materialize`][reemote.drivers.materialize] and
[`run_prepare`][reemote.drivers.run_prepare] are coroutines too. The
formatters are pure local rendering and stay synchronous.

Fact gathering is per-host by default — [`FactCache.get`][reemote.core.FactCache.get]
reads one fact for one host. [`gather_fact`][reemote.drivers.gather_fact] is
the driver-level fan-out across the whole inventory: it runs a fact's `process`
for every host concurrently with `asyncio.gather` and returns a host-aligned
mapping of `host.name` to that host's value (mirroring the baseline's `get_facts`,
which returns `{host: value}`). Facts stay single-host — only the driver owns
the concurrency — so `process`, `FactCache` and the `SimulatedHost` seams are
untouched.

A request whose `doas`/`sudo`/`su` tri-state resolves to elevation
([`Privilege.ON`][reemote.core.Privilege], i.e. `sudo=True`; see
[Deploy scripts](guide.md#deploy-scripts)) is elevated by the driver, which
collapses the tri-state to a concrete bool before sending.
[`command_shell`][reemote.drivers.command_shell] renders the shell text for a
command descriptor — prefixing `doas -n` (or `doas -n -u <user>`) and `sudo`
(or `sudo -u <user>`), and wrapping `su <user> -c …`, combining them in the
fixed order `doas` then `sudo` then `su` (doas is the outermost escalation, su
the innermost payload). The run-as users come from the
host's inventory `privilege` section, never from the operation; `root`, being
the default target of all three, collapses to a bare `doas -n` / `sudo` / `su -c`.
This rendering is what [`format_log`][reemote.drivers.format_log] shows and is
deliberately credential-free: when `privilege` carries a password,
[`send_command`][reemote.drivers.send_command] elevates through
[`Host.run_shell_privileged`][reemote.hosts.Host.run_shell_privileged], which
runs `sudo -A` against a generated askpass helper (or pipes a `su_password` to
su's stdin) so the password is supplied without a TTY and never reaches the
log (`doas` is passwordless, so it carries no credential at all). On the real
transport an applied command that exits non-zero raises
[`CommandError`][reemote.hosts.CommandError] (fail-fast) instead of being
silently logged as applied — unless doas, sudo or su reports the failure as its
*own* authentication rejection (a wrong or missing password, or a denied doas
rule), which raises
[`PrivilegeError`][reemote.hosts.PrivilegeError] so a rejected credential is
reported as a privilege problem rather than a broken command. See
[Privilege: sudo, su and doas](hosts.md#privilege-sudo-su-and-doas).

A request's `env` (see [`op`][reemote.core.op]) rides the same rendering.
[`send_command`][reemote.drivers.send_command] threads it into the
[`command_shell`][reemote.drivers.command_shell] call that produces the plain
descriptor text, so the prefix is on the wire and in the log line rather than in
one of them alone, and `command_shell` builds it with the same `_env_prefix`
helper the `Shell`/`Script` actions use — one place quoting names and
values for both the deploy path and the action path. The prefix goes
*innermost*, immediately around the descriptor's command text and inside every
`doas`/`sudo`/`su` wrapper
(`sudo env http_proxy=http://proxy.example:3128 apt-get update`), because sudo
resets the caller's environment: applied outside the escalation, the variables
would be dropped before the operation's command ran. The two non-shell
descriptor kinds ignore it — a [`TransferCommand`][reemote.commands.TransferCommand]
rides the scp channel and a [`WaitCommand`][reemote.commands.WaitCommand] polls a
fact through its own coroutine, so neither has a process to give an environment
to. An `env` value, unlike a password, *is* command text, so
[`format_log`][reemote.drivers.format_log] prints it: the [`LogEntry`][reemote.drivers.LogEntry]
carries the request's normalised pairs and renders the same prefix the transport
received. The plan is the exception — [`format_plan`][reemote.drivers.format_plan]
names operations, never commands, so no environment appears in it.

A request whose `when` guard fails on a host is skipped by the driver in both
phases: prepare records a `skipped`
[`PlannedOperation`][reemote.drivers.PlannedOperation] without materializing the
operation at all (no facts are read for it — the guard may exist precisely
because the host cannot provide them), and apply logs a `skipped`
[`LogEntry`][reemote.drivers.LogEntry] without sending anything. The request
still consumes its lockstep slot, so the shared schedule stays aligned across
hosts; [`format_plan`][reemote.drivers.format_plan] and
[`format_log`][reemote.drivers.format_log] render it as
`[skipped: when guard <predicate name>]` — the reason names the failing
predicate, not the request — a third outcome beside *changed* and
*no change*. See [Deploy scripts](guide.md#deploy-scripts) for the guard's
static-data and stable-predicate rules.

## Phase awareness

Only the drivers know which phase they are running in. Facts, operations and
command descriptors are phase-unaware: the driver expresses the difference
between *prepare* and *apply* entirely through the lifetime of the
[`FactCache`][reemote.core.FactCache] it hands out, and through whether it
forwards a command to [`send_command`][reemote.drivers.send_command].

The one thing an operation may ask about is the `planning` field of
[`OpContext`][reemote.core.OpContext] (issue #73) — a flag the driver
sets for the two materializations that can never be sent, the prepare of
`run(dry=True)` and a `dry=True` step of
[`run_waves`][reemote.drivers.run_waves]. It exists so an operation whose
*input* a later request of the same deploy stages can report a plan line instead
of raising on state only the apply creates; `files.unarchive`, `files.move` and
`files.copy` are the users. It defaults to `False` — every materialization
followed by `send_command` keeps its strict missing-input check, and a
hand-built context (a test, say) stays strict unless it says otherwise.

```text
deploy(host) generator
      │  yield OperationRequest
      ▼
run_prepare ──► materialize() ──► (changed, reason, commands)   [plan / dry run]
run_apply   ──► materialize() ──► await send_command() per cmd  [host mutated]
```

This invariant also powers the **in-deploy guard** for [actions](actions/actions.md).
`run_prepare` and `run_apply` are wrapped by `_deploy_phase`, which sets a
`ContextVar` for their duration (propagating into the per-host tasks
`asyncio.gather` spawns) and resets it cleanly even when a phase raises. Only
the drivers toggle it, so facts, operations and actions stay phase-unaware;
`gather_action` merely reads the flag and
raises `ActionInDeployError` at the call
site if an action is attempted inside a deploy. The guard is reinforced from
the other side: [`materialize`][reemote.drivers.materialize] requires an
async-generator operation, so wrapping an action class as `op(Shell, ...)` is
refused with [`OperationError`][reemote.core.OperationError] rather than being
run as a deploy step.

The same two invocation points enforce the *yield requests* rule. Both
[`run_prepare`][reemote.drivers.run_prepare] (through `_prepare_host`) and the
lockstep pull in [`run_apply`][reemote.drivers.run_apply] type-check every item
the deploy body hands over and tally the requests it builds, so a body that
returns no request stream (an `op(...)` call with no `yield` anywhere), yields
something that is not an
[`OperationRequest`][reemote.core.OperationRequest], or builds a request and
drops it raises [`DeployRequestError`][reemote.core.DeployRequestError] — in a
normal run always during prepare, before a command is sent, and with the hosts of
an apply step aggregated into one message. The tally lives on each
[`HostRuntime`][reemote.core.HostRuntime], so it is per host and per phase, and
[`compose`][reemote.core.compose]`(..., dedupe=True)` charges its own collapses
so a deliberate duplicate is not mistaken for a lost request.

## Concurrency across hosts

Hosts are independent — each owns its own connection, deploy generator and
[`FactCache`][reemote.core.FactCache] — so the driver issues the per-host work
for a phase or lockstep step concurrently with `asyncio.gather`, then joins:

- [`run_prepare`][reemote.drivers.run_prepare] gathers each host's whole
  prepare (collect its requests, materialize the plan).
- [`run_apply`][reemote.drivers.run_apply] gathers step 2 (materialize +
  `send_command`) for every active host; the request pull and the determinism
  check stay synchronous and complete *before* anything is sent.

The `gather` join is the barrier that preserves cross-host lockstep ordering,
and because `asyncio.gather` returns results in input order, the plans and the
run log stay deterministic regardless of the order the hosts finish. Commands
*within* a single host still run sequentially, since some command sequences are
order-dependent. [`gather_fact`][reemote.drivers.gather_fact] applies the same
fan-out to a one-off fact read outside the two-phase flow.

Both gathers also report failures the same way, since concurrency means several
hosts can fail at once. `run_prepare` collects the per-host
[`HostConnectionError`][reemote.hosts.HostConnectionError] instances of an
inventory, and `run_apply` the per-host
[`PrivilegeError`][reemote.hosts.PrivilegeError] instances of a step, each
merging them into a single aggregate naming every failed host instead of only
the first to lose the race. [`gather_fact`][reemote.drivers.gather_fact] gives
the same treatment to
[`CommandNotFoundError`][reemote.hosts.CommandNotFoundError]: every host
missing the command the fact runs is named in one aggregate. Any other
exception propagates unchanged, so a `CommandError` or a genuine bug still
aborts the run.

## Wave loop

[`run`][reemote.drivers.run] is the **barrier-free** two-phase path: it executes
a deploy twice and enforces determinism. [`run_waves`][reemote.drivers.run_waves]
is the resumable-deploy driver for *roles* — deploy factories whose per-host
generator may yield a [barrier](stages.md) beside its
[`OperationRequest`][reemote.core.OperationRequest]s. It runs **one pass per
request**: each request is [`materialize`][reemote.drivers.materialize]d and
applied in the same step, before the next is pulled, so there is no second
invocation and no [`DeterminismError`][reemote.drivers.DeterminismError]
comparison — no plan/apply window in which the body could diverge.

The loop drives every host's generator together:

1. **Pull** one item per free host (a host is free unless it is parked on a
   barrier or finished).
2. **Step** the pulled requests, one per host, in lockstep — plan and apply each
   in a single `asyncio.gather`, with a fresh `FactCache` per step so an
   operation sees the effect of the requests already applied.
3. **Rendezvous** once every active host is parked or done: close the wave, then
   execute every parked barrier concurrently under one `asyncio.gather` — the
   rendezvous *is* the fan-out — and resume each generator with its own result
   via `gen.send(value)`.

[`run_waves`][reemote.drivers.run_waves] owns the hosts exactly as `run` does:
it validates the inventory, materializes the hosts through `host_factory`, and
closes every connection once in its own `finally`, so a barrier or a role must
never close one. `dry=True` is a **one-wave** preview — no barrier runs, and the
first parked barrier is recorded `unknown` and stops the loop, because a
barrier's outcome may depend on requests that were planned but never applied.
The same never-send reason is why a dry step materializes in planning mode: an
operation whose input an earlier request of the deploy stages reports a plan line
rather than raising, so a spool-then-extract deploy previews in one wave (see
[`files.unarchive`](modules/files.md)). A `dry=False` step is strict, and needs
to be — requests apply one per step, so on the wave path the spool has already
landed when the extraction reads it.

The result is a [`WaveReport`][reemote.drivers.WaveReport]: a flat list of
[`StageRecord`][reemote.drivers.StageRecord]s (one per step or barrier, each
carrying its `wave` number and `scope` path) plus the `wave_ids` that actually
recorded something. A step record holds the
[`PlannedOperation`][reemote.drivers.PlannedOperation] it computed and the
[`LogEntry`][reemote.drivers.LogEntry] it produced (`None` in dry mode); a
barrier record holds the barrier and its `outcome`.
`WaveReport.apply_report(wave, scope)` slices one wave's applied entries to a
scope prefix — the value a [`checkpoint`](stages.md) sends back,
so [`changed_hosts`][reemote.handlers.changed_hosts] reads only the entries that
role emitted. `WaveReport.merged()` folds every applied entry back into a plain
[`ApplyReport`][reemote.drivers.ApplyReport] — the analogue of `run`'s report —
so existing consumers work unchanged, and
[`format_waves`][reemote.drivers.format_waves] (aliased as `WaveReport.tree()`)
renders the scope/wave tree, each request line byte-identical to what
[`format_plan`][reemote.drivers.format_plan] and
[`format_log`][reemote.drivers.format_log] print today. Barrier failures reuse
the action aggregator, so a connection, timeout or privilege error across
several parked hosts merges into one aggregate naming every affected host.

## Reference

::: reemote.drivers
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - run
        - run_prepare
        - run_apply
        - run_waves
        - materialize
        - gather_fact
        - command_shell
        - send_command
        - format_plan
        - format_log
        - format_waves
        - ApplyReport
        - WaveReport
        - StageRecord
        - PlannedOperation
        - LogEntry
        - DeterminismError
