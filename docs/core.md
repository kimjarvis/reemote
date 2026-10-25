# Core objects

`reemote5.core` holds the value types that carry a deploy through both
phases. [`OperationRequest`][reemote5.core.OperationRequest] is what a deploy
script yields; [`OpContext`][reemote5.core.OpContext] is what the driver hands
back to an operation; [`FactCache`][reemote5.core.FactCache] is the boundary
that gives generator-body fact reads their sequential semantics.

::: reemote5.core
    options:
      members:
        - OperationRequest
        - op
        - OpContext
        - FactCache
        - HostRuntime
