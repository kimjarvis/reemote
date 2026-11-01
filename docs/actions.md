# Actions

`reemote5.actions` is the escape hatch for **bespoke shell commands** — the
pragmatic case the deploy model cannot serve: software on your servers that
reemote5 knows nothing about, with no [fact](guide.md#facts) to read it and no
[operation](guide.md#operations) to manage it. An action runs a command on the
target servers *now* and hands back what the command produced.

Actions are conceptually **orthogonal** to the prepare/apply model, and the
split is *enforced*, not conventional. They are deliberately not facts, not
operations, and not part of any `modules/` namespace: they live in their own
[`reemote5.actions`][reemote5.actions] module and are driven by their own
fan-out, [`gather_action`][reemote5.actions.gather_action].

## Where actions belong

Users call actions **before, between and after deploys — never inside one**.
The awaited [`gather_action`][reemote5.actions.gather_action] is a barrier:
every host's command has completed before the next deploy starts or the next
fact is read. Composed deploys never contain an action; composition operates on
[`OperationRequest`][reemote5.core.OperationRequest] sequences, which actions
are not.

```python
from reemote5 import Host, Shell, gather_action, run

hosts = [Host.from_inventory(entry) for entry in inventory]

# Before a deploy: probe bespoke software.
results = await gather_action(hosts, Shell, command="/opt/acme/bin/licence-check")

# A deploy runs in between, over its own connections.
plans, report, hosts = await run(inventory, deploy)

# After a deploy: read the bespoke software's state again.
results = await gather_action(hosts, Shell, command="/opt/acme/bin/status", sudo=True)
for name, result in results.items():
    print(name, result.exit_status, result.stdout)
```

## The Result object

[`Result`][reemote5.actions.Result] is a tiny frozen, library-owned record:

| Field | Meaning |
| --- | --- |
| `exit_status` | The command's own exit code (`None` when the transport reports none). |
| `stdout` | Captured standard output as text (bytes are decoded UTF-8). |
| `stderr` | Captured standard error as text. |
| `ok` | Convenience property: `exit_status == 0`. |

It is copied from the transport's own completion record (asyncssh's
`SSHCompletedProcess`), so **no asyncssh type reaches the public API** and the
test framework can produce a `Result` without importing asyncssh.

The crucial difference from a fact or an operation: **an action interprets
nothing**. A non-zero exit — including 127, command-not-found — is *data in the
Result*, not an exception. The caller, not the library, decides what a bespoke
command's failure means. (A fact read, by contrast, raises
[`CommandNotFoundError`][reemote5.hosts.CommandNotFoundError] on exit 127 so a
missing tool never parses as an empty default.)

## What still raises

A bespoke command's own outcome is data, but **library-level failures still
raise**, exactly as they do elsewhere:

- [`HostConnectionError`][reemote5.hosts.HostConnectionError] — a host that
  cannot be reached. Because hosts run concurrently, several may be unreachable
  at once; the per-host errors are merged into one aggregate naming every
  affected host, the same treatment [`run_prepare`][reemote5.drivers.run_prepare]
  gives them.
- [`PrivilegeError`][reemote5.hosts.PrivilegeError] — sudo's or su's *own*
  authentication rejection (a wrong or missing password, a forbidden run-as
  user). A rejected credential is never reported as a mere exit status; it
  raises, with the credential-free rendering and the would-be `CommandError` as
  its cause, and is aggregated across hosts like
  [`run_apply`][reemote5.drivers.run_apply] does.

Connection failures are reported first when both kinds occur. Any other
exception propagates unchanged.

## Privilege escalation

[`Shell`][reemote5.actions.Shell] honours `sudo=True` and/or `su=True` with the
host's inventory `privilege` data — the run-as users and passwords come from the
inventory, **never from the call**, exactly as for an operation. The escalations
combine in the fixed order `sudo` then `su`, and a password is supplied through
an askpass helper without ever reaching the `Result` or a log. See
[Privilege: sudo and su](hosts.md#privilege-sudo-and-su).

Actions use plain `sudo`/`su` **bools** — the execution-layer spelling — rather
than the plan layer's [`Privilege`][reemote5.core.Privilege] tri-state, because
an action is executed immediately and never composed into a plan.

The elevated path rides
[`Host.run_shell_privileged_result`][reemote5.hosts.Host.run_shell_privileged_result],
the raw-result variant of
[`Host.run_shell_privileged`][reemote5.hosts.Host.run_shell_privileged]: the
escalation is built identically, but the command's own exit status travels back
in the result instead of raising `CommandError`. The unprivileged path rides
[`Host.run_shell_result`][reemote5.hosts.Host.run_shell_result].

## Connection lifecycle

Outside a deploy, **each `gather_action` call re-connects**. The fan-out closes
every host's connection when it finishes (removing any askpass helpers), so the
next call — action or fact — opens a fresh one. This keeps actions
self-contained and leaves no long-lived state behind for future action kinds to
inherit. It also means a script needs no manual `aclose` after an action call,
and an action can follow a [`run`][reemote5.drivers.run] (which closes its own
hosts) transparently: `connect` is lazy, so the action re-opens them.

## The in-deploy guard

An action **cannot run inside a deploy**, and
[`gather_action`][reemote5.actions.gather_action] raises
[`ActionInDeployError`][reemote5.actions.ActionInDeployError] the moment it is
*called* while a deploy is being prepared or applied. Three properties of the
model demand this:

1. A deploy generator is re-invoked once per phase, so an action inside it would
   run **twice**.
2. The prepare phase must never send, so an action there would mutate the host
   mid-plan and poison every later fact read.
3. The determinism guard pairs requests by value, and an immediate action has no
   plan-phase counterpart to pair with.

The mechanism is a `ContextVar` that **only the drivers set**:
`run_prepare` and `run_apply` are wrapped so the flag holds for their duration
and propagates into the per-host tasks `asyncio.gather` spawns, then resets
cleanly even when a phase raises. This keeps phase awareness in the driver
layer, exactly as the [design invariants](guide.md#design-invariants) require —
facts, operations and actions themselves stay phase-unaware.

`gather_action` is deliberately a **synchronous** function returning the
coroutine to await, so the check fires *at the call site* — even the un-awaited
call inside a sync deploy generator (which could never have awaited it anyway)
fails immediately rather than being silently swallowed as a never-awaited
coroutine. Wrapping the action class as an operation — `op(Shell, ...)` — is
refused too: [`materialize`][reemote5.drivers.materialize] requires an
async-generator operation and raises
[`OperationError`][reemote5.core.OperationError] for anything else.

The sanctioned way to let a deploy depend on a bespoke command's outcome is the
**deploy → action → deploy** split: run the action *between* two complete
prepare/apply cycles, freeze its `Result` dict, and build the second deploy from
a factory closing over that snapshot — a pure function of static input, so both
phases resolve identically. This is the action analogue of the two-deploy fact
split; see the [example](examples/actions.md#deploy-action-deploy).

## Reference

::: reemote5.actions
    options:
      members:
        - Shell
        - Result
        - gather_action
        - ActionInDeployError