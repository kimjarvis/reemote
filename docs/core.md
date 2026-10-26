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

::: reemote5.core
    options:
      members:
        - OperationRequest
        - op
        - sub_deploy
        - compose
        - OpContext
        - FactCache
        - HostRuntime
