# server module

`reemote5.modules.server.server` combines the systemd facts and the service
operations in a single source, so the read side (`SystemdServiceState`,
`SystemdServiceEnabled`) sits beside the `server.service` and
`server.wait_service` operations that consume it. It is also where reemote5's
**waiting** design lives: `server.wait_service` is the first operation that
blocks until a condition on the host holds.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a generator —
implemented as a `process` coroutine returning the raw value (over asyncssh in
the real implementation). Read facts through
[`FactCache.get`][reemote5.core.FactCache.get], which is itself a coroutine,
so results are awaited and cached within a phase step. Both facts take a
service name and normalize it with
[`unit_name`][reemote5.modules.server.server.unit_name] (`httpd` →
`httpd.service`), so the test framework's simulated state is keyed by full
unit names too. `SystemdServiceState` returns the raw `ActiveState` string
rather than a boolean so a predicate can decide what counts as ready —
`activating` is deliberately *not* `active`, which is what a wait polls
through.

::: reemote5.modules.server.server
    options:
      members:
        - unit_name
        - SystemdServiceState
        - SystemdServiceEnabled

## Operations

Operations live in namespace modules so a deploy reads `server.service` and
`server.wait_service`, mirroring pyinfra's API. Every operation is an async
generator taking an [`OpContext`][reemote5.core.OpContext], awaiting facts via
`ctx.facts`, setting `ctx.changed`/`ctx.reason` where a change is needed, and
yielding one [`Command`][reemote5.commands.Command] descriptor per mutation.

`server.service` is the idempotent half, mirroring pyinfra's `systemd.service`
for `service`, `running`, `restarted`, `enabled` and `daemon_reload`: it
compares the unit against the two facts and yields a
`SystemctlCommand` only
where the unit differs from the request (`daemon_reload` and `restarted` are
stateless and always yield when set). The `systemctl` commands need root, so
flag the request `sudo=True`. pyinfra's `reloaded`, `command`, `user_mode`,
`machine` and `user_name` options are not supported.

::: reemote5.modules.server.server
    options:
      members:
        - service

## Waiting for a condition

pyinfra waits in two ways: a remote-side shell loop (`server.wait` polls
`netstat` in a `while` loop *on the host*) or a control-side Python callback
(`server.reboot` yields a `FunctionCommand` that sleeps and re-reads the
`Uptime` fact until the host is back). reemote5 takes the second shape and
makes it a descriptor: `server.wait_service` yields a
`WaitForFactCommand`,
the second kind of *non-shell* descriptor after the transfers. It has no
`shell()`; [`send_command`][reemote5.drivers.send_command] dispatches on the
[`WaitCommand`][reemote5.commands.WaitCommand] base and awaits the
descriptor's `wait`, which polls the fact every `interval` seconds until the
predicate holds or `timeout` expires and
[`WaitTimeoutError`][reemote5.commands.WaitTimeoutError] aborts the run. Like
a transfer it is unprivileged — `sudo`/`su` do not apply to a poll.

Three constraints follow from the execution model:

- **The operation body never polls.** The prepare phase never sends, and its
  `FactCache` persists across a host's requests, so a loop inside the
  generator would spin forever against a value that never changes. The
  operation only *describes* the wait; the driver performs it at send time.
- **The wait is planned unconditionally.** `server.wait_service` reads no
  facts and always sets `changed=True`, like pyinfra's non-idempotent
  `server.wait`. Gating it on a fact would read pre-deployment state in
  prepare and post-start state in apply, and branching on that would raise
  [`DeterminismError`][reemote5.drivers.DeterminismError]. Whether the unit is
  *already* active is discovered by the first poll — a zero `timeout` still
  polls once, so an already-met condition always succeeds.
- **The poll bypasses the cache.** `WaitForFactCommand.wait` calls
  `fact_cls.process` directly, never `ctx.facts.get`: the per-step cache would
  pin the first value and the loop would never observe the change.

`WaitForFactCommand` is generic — any fact class, any arguments, any predicate
— so a module can wait for a port, a file hash or a dnf lock with the same
descriptor. Its fields end up compared by value in plans, so pass module-level
fact classes and predicates (such as
[`is_active`][reemote5.modules.server.server.is_active]), never a lambda built
inside the generator: the deploy is re-invoked per phase and a fresh lambda
per invocation would not compare equal.

In the apply phase the lockstep barrier guarantees every host has finished
the preceding step (the `systemctl start`) before any host begins polling,
and the per-host poll loops then run concurrently; a slow host delays only
the step barrier, not the other hosts' polling.

The test framework's `SimulatedHost` holds `services` (unit → `ActiveState`)
and `enabled_services` as the seams the facts read, mutates them on
`SystemctlCommand`, and evaluates a `WaitForFactCommand` *once* without
sleeping: in-memory state never changes on its own, so the condition either
already holds or the simulated wait raises `WaitTimeoutError` immediately.

::: reemote5.modules.server.server
    options:
      members:
        - wait_service
        - is_active

## Example

The deploy from
[`wait_for_httpd`](../examples.md#starting-a-service-and-waiting-for-it):

```python
def deploy(host: Host) -> Generator[OperationRequest]:
    yield op(
        server.service,
        name="Start httpd",
        service="httpd",
        running=True,
        enabled=True,
        sudo=True,
    )
    yield op(
        server.wait_service,
        name="Wait for httpd to become active",
        service="httpd",
        timeout=60.0,
        interval=2.0,
    )
```

On a host where httpd is stopped the plan reads:

```text
web1: Start httpd              [changed: httpd.service not running (inactive), will start; httpd.service not enabled, will enable]
web1: Wait for httpd to become active [changed: wait for httpd.service to be active]
```

and the apply log shows the commands that ran, the wait rendered in its
loggable form:

```text
web1: Start httpd              [changed: ...]
    $ sudo systemctl start httpd.service
    $ sudo systemctl enable httpd.service
web1: Wait for httpd to become active [changed: wait for httpd.service to be active]
    $ wait for SystemdServiceState('httpd.service') <is_active> every 2.0s up to 60.0s
```

On a second run `Start httpd` reports no change while the wait is planned
again and passes on its first poll.
