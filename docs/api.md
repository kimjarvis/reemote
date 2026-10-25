# API Reference

Everything documented here is re-exported from the top-level package, so a
deploy script only ever needs one import line:

```python
from reemote5 import OperationRequest, SimulatedHost, apt, files, op, run
```

The API has four layers, and each one is documented below in the order a
request travels through them:

| Layer | Objects | Source module |
| --- | --- | --- |
| **Drivers** | [`run`][reemote5.drivers.run], [`run_prepare`][reemote5.drivers.run_prepare], [`run_apply`][reemote5.drivers.run_apply], [`send_command`][reemote5.drivers.send_command], [`format_plan`][reemote5.drivers.format_plan], [`format_log`][reemote5.drivers.format_log] | `reemote5.drivers` |
| **Core objects** | [`OperationRequest`][reemote5.core.OperationRequest], [`op`][reemote5.core.op], [`OpContext`][reemote5.core.OpContext], [`FactCache`][reemote5.core.FactCache], [`HostRuntime`][reemote5.core.HostRuntime] | `reemote5.core` |
| **Commands** | [`Command`][reemote5.commands.Command] and its typed subclasses | `reemote5.commands` |
| **Hosts, facts, operations** | [`SimulatedHost`][reemote5.hosts.SimulatedHost], [`AptPackages`][reemote5.facts.AptPackages], [`FileLink`][reemote5.facts.FileLink], [`apt.packages`][reemote5.operations.apt.packages], [`files.link`][reemote5.operations.files.link] | `reemote5.hosts`, `reemote5.facts`, `reemote5.operations` |

!!! note "Design-doc references"

    Several docstrings cite *design doc section N*. Those are section numbers
    from the original design notes for this POC; they are informational only
    and are not published here.

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

## Drivers

`reemote5.drivers` is the execution engine. [`run`][reemote5.drivers.run] is
the entry point used by deploy scripts and examples; the remaining functions
are exposed so the two phases can be driven, inspected and rendered
separately.

The command-execution path is asynchronous:
[`send_command`][reemote5.drivers.send_command] is a coroutine (a stand-in
for the asyncssh-based SSH execution to come), and therefore
[`run_apply`][reemote5.drivers.run_apply] and [`run`][reemote5.drivers.run]
are coroutines as well. Fact gathering is asynchronous for the same reason —
a fact's `process` will run over asyncssh — so
[`materialize`][reemote5.drivers.materialize] and
[`run_prepare`][reemote5.drivers.run_prepare] are coroutines too. The
formatters are pure local rendering and stay synchronous.

::: reemote5.drivers
    options:
      members:
        - run
        - run_prepare
        - run_apply
        - materialize
        - send_command
        - format_plan
        - format_log
        - ApplyReport
        - PlannedOperation
        - LogEntry
        - DeterminismError

## Core objects

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

## Command descriptors

`reemote5.commands` defines the descriptors operations yield. They are dumb,
frozen value objects: an operation never executes anything, it only describes
what would have to run. The typed descriptors exist so
[`send_command`][reemote5.drivers.send_command] can dispatch on them and apply
the equivalent mutation to a [`SimulatedHost`][reemote5.hosts.SimulatedHost].

| Descriptor | Shell form | Simulated effect |
| --- | --- | --- |
| [`AptInstallCommand`][reemote5.commands.AptInstallCommand] | `apt-get install -y <pkgs>` | adds packages (plus their `installs_links` side effects) |
| [`AptRemoveCommand`][reemote5.commands.AptRemoveCommand] | `apt-get remove -y <pkgs>` | removes packages |
| [`LinkCommand`][reemote5.commands.LinkCommand] | `ln -sf <target> <src>` | sets `links[src] = target` |
| [`LinkCommand`][reemote5.commands.LinkCommand] with `target=None` | `rm -f <src>` | removes `links[src]` |
| [`StringCommand`][reemote5.commands.StringCommand] | the raw string | none (accepted, not simulated) |

::: reemote5.commands
    options:
      members:
        - Command
        - StringCommand
        - AptInstallCommand
        - AptRemoveCommand
        - LinkCommand

## Hosts

`reemote5.hosts` provides the in-memory stand-in for a real machine. It holds
the state facts read and the mutations the driver applies, which is what makes
the "fresh fact cache per lockstep step" property observable.

::: reemote5.hosts
    options:
      members:
        - SimulatedHost

## Facts

`reemote5.facts` holds the fact classes. A fact is an asynchronous, queryable
resource — never yielded by a generator — implemented as a `process`
coroutine returning the raw value (over asyncssh in the real implementation).
Read facts through [`FactCache.get`][reemote5.core.FactCache.get], which is
itself a coroutine, so results are awaited and cached within a phase step.

::: reemote5.facts
    options:
      members:
        - AptPackages
        - FileLink

## Operations

Operations live in namespace modules so a deploy reads `apt.packages` and
`files.link`, mirroring pyinfra's API. Every operation is an async generator
with the same fixed signature:

```python
async def operation(ctx: OpContext, *args, **kwargs) -> AsyncGenerator[Command, None]:
```

It awaits facts via `ctx.facts`, sets `ctx.changed` and `ctx.reason` where it
decides a change is needed, and yields one descriptor per command that would
mutate the host.

::: reemote5.operations.apt
    options:
      members:
        - packages

::: reemote5.operations.files
    options:
      members:
        - link
