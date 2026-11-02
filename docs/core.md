# Core objects

`reemote5.core` holds the value types that carry a deploy through both
phases. [`OperationRequest`][reemote5.core.OperationRequest] is what a deploy
script yields; [`OpContext`][reemote5.core.OpContext] is what the driver hands
back to an operation; [`FactCache`][reemote5.core.FactCache] is the boundary
that gives generator-body fact reads their sequential semantics.

[`sub_deploy`][reemote5.core.sub_deploy] and [`compose`][reemote5.core.compose]
are the composition helpers: pure request-stream transforms that wrap
sub-deploys with default meta-parameters (a name prefix, privilege elevation,
an eligibility guard) and concatenate them into one deploy-shaped callable,
optionally collapsing requests that would execute identically (dedupe by
effective identity — the plan `name` is excluded and the first occurrence
wins).

[`Privilege`][reemote5.core.Privilege] is the tri-state behind the
`doas`/`sudo`/`su` meta-parameters — `ON`, `OFF` and `UNSET` — which lets a
`sub_deploy` policy force elevation across a role while still honouring an
explicit `sudo=False` opt-out (see
[Composing deploys](guide.md#composing-deploys)). `op` and
`sub_deploy` also accept a plain bool (`True` → `ON`, `False` → `OFF`) and
default to `UNSET`; the driver collapses the tri-state to a concrete bool
before a command is sent, where only `ON` elevates.

The `when` meta-parameter is an *eligibility guard*: a
[`WhenPredicate`][reemote5.core.WhenPredicate] (or tuple of predicates) over
static inventory data, captured on the request and evaluated by the driver
at the execution boundary. A guarded request stays in every host's sequence
— one shared schedule — and is skipped on hosts that fail the guard, without
being materialized at all; the plan and run log render it as
`[skipped: when guard <name>]`. Unlike the escalation policy's inner-wins
fill, guards compose by *conjunction*: a `sub_deploy` guard and an inner
guard must both pass. Predicates must be stable module-level functions
reading only static data, so both phases reach the same decision (see
[Determinism](guide.md#determinism)).

[`OperationError`][reemote5.core.OperationError] is raised by an operation
whose prerequisites are not met on a host — `server.timezone`, for instance,
requires `timedatectl` and is supported on systemd systems only. It aborts
the run fail-fast while materializing (in a deploy run that is the prepare
phase, before anything is sent), with a message naming the host and the
missing prerequisite.

::: reemote5.core
    options:
      members:
        - OperationRequest
        - OperationError
        - Privilege
        - WhenPredicate
        - op
        - sub_deploy
        - compose
        - OpContext
        - FactCache
        - HostRuntime
