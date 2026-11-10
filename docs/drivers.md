# Drivers

`reemote5.drivers` is the execution engine. [`run`][reemote5.drivers.run] is
the entry point used by deploy scripts and examples; the remaining functions
are exposed so the two phases can be driven, inspected and rendered
separately.

The command-execution path is asynchronous:
[`send_command`][reemote5.drivers.send_command] is a coroutine that runs each
command over asyncssh (or forwards to a `SimulatedHost` in tests), and
therefore [`run_apply`][reemote5.drivers.run_apply] and
[`run`][reemote5.drivers.run] are coroutines as well. Fact gathering is
asynchronous for the same reason — a fact's `process` runs over asyncssh — so
[`materialize`][reemote5.drivers.materialize] and
[`run_prepare`][reemote5.drivers.run_prepare] are coroutines too. The
formatters are pure local rendering and stay synchronous.

Fact gathering is per-host by default — [`FactCache.get`][reemote5.core.FactCache.get]
reads one fact for one host. [`gather_fact`][reemote5.drivers.gather_fact] is
the driver-level fan-out across the whole inventory: it runs a fact's `process`
for every host concurrently with `asyncio.gather` and returns a host-aligned
mapping of `host.name` to that host's value (mirroring the baseline's `get_facts`,
which returns `{host: value}`). Facts stay single-host — only the driver owns
the concurrency — so `process`, `FactCache` and the `SimulatedHost` seams are
untouched.

A request whose `doas`/`sudo`/`su` tri-state resolves to elevation
([`Privilege.ON`][reemote5.core.Privilege], i.e. `sudo=True`; see
[Deploy scripts](guide.md#deploy-scripts)) is elevated by the driver, which
collapses the tri-state to a concrete bool before sending.
[`command_shell`][reemote5.drivers.command_shell] renders the shell text for a
command descriptor — prefixing `doas -n` (or `doas -n -u <user>`) and `sudo`
(or `sudo -u <user>`), and wrapping `su <user> -c …`, combining them in the
fixed order `doas` then `sudo` then `su` (doas is the outermost escalation, su
the innermost payload). The run-as users come from the
host's inventory `privilege` section, never from the operation; `root`, being
the default target of all three, collapses to a bare `doas -n` / `sudo` / `su -c`.
This rendering is what [`format_log`][reemote5.drivers.format_log] shows and is
deliberately credential-free: when `privilege` carries a password,
[`send_command`][reemote5.drivers.send_command] elevates through
[`Host.run_shell_privileged`][reemote5.hosts.Host.run_shell_privileged], which
runs `sudo -A` against a generated askpass helper (or pipes a `su_password` to
su's stdin) so the password is supplied without a TTY and never reaches the
log (`doas` is passwordless, so it carries no credential at all). On the real
transport an applied command that exits non-zero raises
[`CommandError`][reemote5.hosts.CommandError] (fail-fast) instead of being
silently logged as applied — unless doas, sudo or su reports the failure as its
*own* authentication rejection (a wrong or missing password, or a denied doas
rule), which raises
[`PrivilegeError`][reemote5.hosts.PrivilegeError] so a rejected credential is
reported as a privilege problem rather than a broken command. See
[Privilege: sudo, su and doas](hosts.md#privilege-sudo-su-and-doas).

A request whose `when` guard fails on a host is skipped by the driver in both
phases: prepare records a `skipped`
[`PlannedOperation`][reemote5.drivers.PlannedOperation] without materializing the
operation at all (no facts are read for it — the guard may exist precisely
because the host cannot provide them), and apply logs a `skipped`
[`LogEntry`][reemote5.drivers.LogEntry] without sending anything. The request
still consumes its lockstep slot, so the shared schedule stays aligned across
hosts; [`format_plan`][reemote5.drivers.format_plan] and
[`format_log`][reemote5.drivers.format_log] render it as
`[skipped: when guard <predicate name>]` — the reason names the failing
predicate, not the request — a third outcome beside *changed* and
*no change*. See [Deploy scripts](guide.md#deploy-scripts) for the guard's
static-data and stable-predicate rules.

## Phase awareness

Only the drivers know which phase they are running in. Facts, operations and
command descriptors are phase-unaware: the driver expresses the difference
between *prepare* and *apply* entirely through the lifetime of the
[`FactCache`][reemote5.core.FactCache] it hands out, and through whether it
forwards a command to [`send_command`][reemote5.drivers.send_command].

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
the other side: [`materialize`][reemote5.drivers.materialize] requires an
async-generator operation, so wrapping an action class as `op(Shell, ...)` is
refused with [`OperationError`][reemote5.core.OperationError] rather than being
run as a deploy step.

The same two invocation points enforce the *yield requests* rule. Both
[`run_prepare`][reemote5.drivers.run_prepare] (through `_prepare_host`) and the
lockstep pull in [`run_apply`][reemote5.drivers.run_apply] type-check every item
the deploy body hands over and tally the requests it builds, so a body that
returns no request stream (an `op(...)` call with no `yield` anywhere), yields
something that is not an
[`OperationRequest`][reemote5.core.OperationRequest], or builds a request and
drops it raises [`DeployRequestError`][reemote5.core.DeployRequestError] — in a
normal run always during prepare, before a command is sent, and with the hosts of
an apply step aggregated into one message. The tally lives on each
[`HostRuntime`][reemote5.core.HostRuntime], so it is per host and per phase, and
[`compose`][reemote5.core.compose]`(..., dedupe=True)` charges its own collapses
so a deliberate duplicate is not mistaken for a lost request.

## Concurrency across hosts

Hosts are independent — each owns its own connection, deploy generator and
[`FactCache`][reemote5.core.FactCache] — so the driver issues the per-host work
for a phase or lockstep step concurrently with `asyncio.gather`, then joins:

- [`run_prepare`][reemote5.drivers.run_prepare] gathers each host's whole
  prepare (collect its requests, materialize the plan).
- [`run_apply`][reemote5.drivers.run_apply] gathers step 2 (materialize +
  `send_command`) for every active host; the request pull and the determinism
  check stay synchronous and complete *before* anything is sent.

The `gather` join is the barrier that preserves cross-host lockstep ordering,
and because `asyncio.gather` returns results in input order, the plans and the
run log stay deterministic regardless of the order the hosts finish. Commands
*within* a single host still run sequentially, since some command sequences are
order-dependent. [`gather_fact`][reemote5.drivers.gather_fact] applies the same
fan-out to a one-off fact read outside the two-phase flow.

Both gathers also report failures the same way, since concurrency means several
hosts can fail at once. `run_prepare` collects the per-host
[`HostConnectionError`][reemote5.hosts.HostConnectionError] instances of an
inventory, and `run_apply` the per-host
[`PrivilegeError`][reemote5.hosts.PrivilegeError] instances of a step, each
merging them into a single aggregate naming every failed host instead of only
the first to lose the race. [`gather_fact`][reemote5.drivers.gather_fact] gives
the same treatment to
[`CommandNotFoundError`][reemote5.hosts.CommandNotFoundError]: every host
missing the command the fact runs is named in one aggregate. Any other
exception propagates unchanged, so a `CommandError` or a genuine bug still
aborts the run.

## Reference

::: reemote5.drivers
    options:
      members:
        - run
        - run_prepare
        - run_apply
        - materialize
        - gather_fact
        - command_shell
        - send_command
        - format_plan
        - format_log
        - ApplyReport
        - PlannedOperation
        - LogEntry
        - DeterminismError
