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

A request flagged `sudo=True` (see [Deploy scripts](guide.md#deploy-scripts))
is elevated by the driver: [`command_shell`][reemote5.drivers.command_shell] is
the single mapping from a command descriptor to the shell text a host receives,
prefixing `sudo` when the operation asked for it. Both `send_command` and
[`format_log`][reemote5.drivers.format_log] go through it, so the real transport
and the rendered log agree on what runs.

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

## Reference

::: reemote5.drivers
    options:
      members:
        - run
        - run_prepare
        - run_apply
        - materialize
        - command_shell
        - send_command
        - format_plan
        - format_log
        - ApplyReport
        - PlannedOperation
        - LogEntry
        - DeterminismError
