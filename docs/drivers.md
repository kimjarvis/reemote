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
mapping of `host.name` to that host's value (mirroring pyinfra's `get_facts`,
which returns `{host: value}`). Facts stay single-host — only the driver owns
the concurrency — so `process`, `FactCache` and the `SimulatedHost` seams are
untouched.

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
