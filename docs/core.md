# Core objects

`reemote.core` holds the value types that carry a deploy through both
phases. [`OperationRequest`][reemote.core.OperationRequest] is what a deploy
script yields; [`OpContext`][reemote.core.OpContext] is what the driver hands
back to an operation; [`FactCache`][reemote.core.FactCache] is the boundary
that gives generator-body fact reads their sequential semantics.

[`sub_deploy`][reemote.core.sub_deploy] and [`compose`][reemote.core.compose]
are the composition helpers: pure request-stream transforms that wrap
sub-deploys with default meta-parameters (a name prefix, privilege elevation,
an eligibility guard) and concatenate them into one deploy-shaped callable,
optionally collapsing requests that would execute identically (dedupe by
effective identity — the plan `name` is excluded and the first occurrence
wins).

Every [`OperationRequest`][reemote.core.OperationRequest] also carries a
`scope` — a report path set by [`scoped`](stages.md) on the wave
path — which is report-only and excluded from the effective identity above.
These two helpers sequence requests **only**: they belong to the barrier-free
two-phase [`run`][reemote.drivers.run] path. A deploy that reads state back into
its own control flow yields a [barrier](stages.md) and runs under
[`run_waves`][reemote.drivers.run_waves] instead, and a barrier handed to
[`compose`][reemote.core.compose] or [`sub_deploy`][reemote.core.sub_deploy]
raises [`DeployRequestError`][reemote.core.DeployRequestError].

[`Privilege`][reemote.core.Privilege] is the tri-state behind the
`doas`/`sudo`/`su` meta-parameters — `ON`, `OFF` and `UNSET` — which lets a
`sub_deploy` policy force elevation across a role while still honouring an
explicit `sudo=False` opt-out (see
[Composing deploys](guide.md#composing-deploys)). `op` and
`sub_deploy` also accept a plain bool (`True` → `ON`, `False` → `OFF`) and
default to `UNSET`; the driver collapses the tri-state to a concrete bool
before a command is sent, where only `ON` elevates.

The `when` meta-parameter is an *eligibility guard*: a
[`WhenPredicate`][reemote.core.WhenPredicate] (or tuple of predicates) over
static inventory data, captured on the request and evaluated by the driver
at the execution boundary. A guarded request stays in every host's sequence
— one shared schedule — and is skipped on hosts that fail the guard, without
being materialized at all; the plan and run log render it as
`[skipped: when guard <name>]`. Unlike the escalation policy's inner-wins
fill, guards compose by *conjunction*: a `sub_deploy` guard and an inner
guard must both pass. Predicates must be stable module-level functions
reading only static data, so both phases reach the same decision (see
[Determinism](guide.md#determinism)).

The `env` meta-parameter is the environment of the command the operation runs
on the target — Ansible's task-level `environment:`, and the request-level
counterpart of the `env` keyword the [actions](actions/actions.md) take. A
mapping or a sequence of `(key, value)` pairs is accepted and normalised at
construction to a sorted tuple of pairs, which is what keeps a request built
with a plain `dict` hashable for
[`compose`][reemote.core.compose]`(..., dedupe=True)` (the value participates in
equality, hashing and the dedupe key, and never reaches `kwargs`). Both the name
and the value must be a `str` — the pair is spliced into a shell word, so
anything else raises `TypeError` at the call site rather than failing at the
transport. [`command_shell`][reemote.drivers.command_shell] renders it as an
`env K=V …` prefix on the descriptor's own text, *innermost* — inside the
`doas`/`sudo`/`su` wrappers, as in
`sudo env http_proxy=… dnf install -y httpd` — since `sudo` resets the caller's
environment; a [`TransferCommand`][reemote.commands.TransferCommand] or
[`WaitCommand`][reemote.commands.WaitCommand] descriptor ignores it, because
neither starts a process. It is not tri-state like the privilege flags: there is
no inheritance policy, so [`sub_deploy`][reemote.core.sub_deploy] leaves an
inner request's own `env` untouched.

[`OperationError`][reemote.core.OperationError] is raised by an operation
whose prerequisites are not met on a host — `server.timezone`, for instance,
requires `timedatectl` and is supported on systemd systems only. It aborts
the run fail-fast while materializing (in a deploy run that is the prepare
phase, before anything is sent), with a message naming the host and the
missing prerequisite.

[`UnhashableArgumentError`][reemote.core.UnhashableArgumentError] is raised
by the request model itself, while an `OperationRequest` is being built. A
request's `args`/`kwargs` values are hashed by
[`compose`][reemote.core.compose]`(..., dedupe=True)`, so a mutable container
there — `packages=["nginx"]`, or a list nested inside a tuple — is rejected on
the spot (a `TypeError` subclass) naming the offending field, rather than
failing later from inside a set insertion in a composition helper.

[`DeployRequestError`][reemote.core.DeployRequestError] enforces the deploy
body's other rule: *yield* the requests, do not merely build them. It is raised
(also a `TypeError` subclass) when the body returns no iterable at all, when it
hands over something that is not an `OperationRequest`, or when it builds a
request with `op()` and drops it instead of yielding it — the last of these is
caught by a per-invocation tally of requests built versus requests yielded,
maintained through a `ContextVar` that `op()` charges on each construction.
`compose`'s dedupe collapses are charged as dropped on purpose, so a legal
duplicate is never read as a lost request. Both phases run the checks —
see [Drivers](drivers.md#phase-awareness) and
[Deploy scripts](guide.md#deploy-scripts).

::: reemote.core
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - OperationRequest
        - OperationError
        - UnhashableArgumentError
        - DeployRequestError
        - Privilege
        - WhenPredicate
        - op
        - sub_deploy
        - compose
        - OpContext
        - FactCache
        - HostRuntime
