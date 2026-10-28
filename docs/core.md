# Core objects

`reemote5.core` holds the value types that carry a deploy through both
phases. [`OperationRequest`][reemote5.core.OperationRequest] is what a deploy
script yields; [`OpContext`][reemote5.core.OpContext] is what the driver hands
back to an operation; [`FactCache`][reemote5.core.FactCache] is the boundary
that gives generator-body fact reads their sequential semantics.

[`sub_deploy`][reemote5.core.sub_deploy] and [`compose`][reemote5.core.compose]
are the composition helpers: pure request-stream transforms that wrap
sub-deploys with default meta-parameters (a name prefix, privilege elevation)
and concatenate them into one deploy-shaped callable, optionally collapsing
requests that would execute identically (dedupe by effective identity — the
plan `name` is excluded and the first occurrence wins).

[`Privilege`][reemote5.core.Privilege] is the tri-state behind the `sudo`/`su`
meta-parameters — `ON`, `OFF` and `UNSET` — which lets a `sub_deploy` policy
force elevation across a role while still honouring an explicit `sudo=False`
opt-out (see [Composing deploys](guide.md#composing-deploys)). `op` and
`sub_deploy` also accept a plain bool (`True` → `ON`, `False` → `OFF`) and
default to `UNSET`; the driver collapses the tri-state to a concrete bool
before a command is sent, where only `ON` elevates.

::: reemote5.core
    options:
      members:
        - OperationRequest
        - Privilege
        - op
        - sub_deploy
        - compose
        - OpContext
        - FactCache
        - HostRuntime
