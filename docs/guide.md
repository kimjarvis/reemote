# Guide

This guide covers how to write against the model: deploy scripts first, then
the three extension points (facts, operations, command descriptors) and the
rules that keep both phases in agreement.

## Deploy scripts

A deploy script is a generator that takes a host and yields
[`OperationRequest`][reemote5.core.OperationRequest] objects. The signature is
fixed:

```python
def deploy(host: SimulatedHost) -> Generator[OperationRequest, None, None]:
```

Requests are built with the [`op`][reemote5.core.op] helper, which takes the
operation function, an optional human-readable `name` for plan output, and the
operation's parameters **as keyword arguments only**:

```python
from reemote5 import apt, files, op

yield op(apt.packages, name="Install nginx", packages=("nginx",), present=True)
yield op(
    files.link,
    name="Enable site",
    src="/etc/nginx/sites-enabled/site",
    target="/etc/nginx/sites-available/site",
)
```

Three rules follow from the model:

1. **Use immutable containers for collection parameters.** `packages=("nginx",)`
   not `packages=["nginx"]`. Requests are compared and hashed to pair "the Nth
   operation on host A" with "the Nth operation on host B"; a `list` inside
   `kwargs` breaks hashing.
2. **Yield requests; do not call operations.** Calling `apt.packages(...)`
   directly returns an unexecuted generator and bypasses the driver entirely.
3. **Do not branch on mutable state** in the deploy body. See
   [Determinism](#determinism) below.

The deploy body receives only the host — it does not get an
[`OpContext`][reemote5.core.OpContext], and facts are read by *operations*, not
by the deploy script. Keeping the request sequence unconditional and letting
operations decide *changed vs. no change* is what makes a deploy work in both
phases:

```python
def deploy(host):
    # Same two requests in prepare and apply. Whether the link exists at the
    # moment the second operation runs is the operation's business.
    yield op(apt.packages, name="Install nginx", packages=("nginx",))
    yield op(
        files.link,
        name="Remove default site",
        src="/etc/nginx/sites-enabled/default",
        present=False,
    )
```

## Running a deploy

[`run`][reemote5.drivers.run] is the one-call entry point:

```python
plans, report = run(hosts, deploy)  # prepare + apply
plans, report = run(hosts, deploy, dry=True)  # prepare only, report is None
```

It returns the plan keyed by host name plus the
[`ApplyReport`][reemote5.drivers.ApplyReport]. Render either with
[`format_plan`][reemote5.drivers.format_plan] /
[`format_log`][reemote5.drivers.format_log], or inspect the structures
directly:

```python
if report is not None and report.changed:
    for entry in report.log:
        print(entry.host, entry.name, entry.changed, entry.reason)
```

To drive the phases yourself — for example to review a plan before committing
to it — use [`run_prepare`][reemote5.drivers.run_prepare] and
[`run_apply`][reemote5.drivers.run_apply]:

```python
runtimes, plans = run_prepare(hosts, deploy)
print(format_plan(plans))
report = run_apply(runtimes, plans=plans)  # plans= enables the determinism guard
```

## Hosts

[`SimulatedHost`][reemote5.hosts.SimulatedHost] is the in-memory stand-in for a
machine. It carries the state facts read — `packages` and `links` — plus an
optional `installs_links` map describing the side effects of installing a
package:

```python
SimulatedHost(
    name="web1",
    packages=set(),
    links={},
    installs_links={
        "nginx": {
            "/etc/nginx/sites-enabled/default": "/etc/nginx/sites-available/default"
        }
    },
)
```

`installs_links` is what lets the example reproduce the interesting case: on a
fresh host, installing nginx *creates* the default-site link, so the next
operation sees state that did not exist during prepare.

Host state is mutated only by
[`send_command`][reemote5.drivers.send_command]; deploy scripts and operations
never touch it.

## Facts

A fact is a class with an `async` `process` classmethod returning the raw
value. Facts are asynchronous, phase-unaware, and never yielded by a
generator:

```python
from reemote5 import SimulatedHost


class EnabledSites:
    """The set of nginx sites currently enabled on the host."""

    @classmethod
    async def process(cls, host: SimulatedHost) -> set[str]:
        # A real implementation would list /etc/nginx/sites-enabled over asyncssh.
        prefix = "/etc/nginx/sites-enabled/"
        return {src for src in host.links if src.startswith(prefix)}
```

Always read facts through the cache, never by calling `process` directly — the
cache is the boundary whose lifetime the driver controls, and its `get` is a
coroutine, so it must be awaited:

```python
enabled = await ctx.facts.get(EnabledSites)
```

Cache keys include the fact class and its arguments, so repeated reads within
one step are free.

## Operations

An operation is an async generator receiving the
[`OpContext`][reemote5.core.OpContext] as its first argument:

```python
async def operation(ctx: OpContext, *args, **kwargs) -> AsyncGenerator[Command, None]:
```

Its contract:

- await facts via `await ctx.facts.get(...)`;
- set `ctx.changed = True` and a short `ctx.reason` at the point where it
  decides a change is needed (leave `changed` false and still set `reason`
  for the "no change" explanation shown in plans);
- yield one command descriptor per command that would mutate the host;
- never execute anything and never touch the host directly.

The built-in [`files.link`][reemote5.operations.files.link] in full:

```python
async def link(
    ctx: OpContext, src: str, target: str | None = None, present: bool = True
) -> AsyncGenerator[Command, None]:
    current = await ctx.facts.get(FileLink, src)

    if present:
        if current == target:
            ctx.reason = f"link already correct ({src} -> {target})"
        else:
            ctx.changed = True
            ctx.reason = f"link {src} -> {target} missing or incorrect"
            yield LinkCommand(src=src, target=target)
    else:
        if current is None:
            ctx.reason = "link already absent"
        else:
            ctx.changed = True
            ctx.reason = "link present, will remove"
            yield LinkCommand(src=src, target=None)
```

Note that `reason` is set on both branches: it is the explanation printed in
plans and run logs even when nothing changes.

Operations live in namespace modules under `reemote5.operations` so deploy
scripts read `apt.packages` rather than `apt_packages`. The driver calls
`request.func(ctx, *request.args, **request.kwargs)`, and materializes the
whole command generator to learn `changed` — even during prepare.

## Command descriptors

Commands are frozen dataclasses subclassing
[`Command`][reemote5.commands.Command]. Two things make a descriptor useful in
this model:

1. A `shell()` method returning the equivalent command line, used by
   [`format_log`][reemote5.drivers.format_log].
2. A `match` arm in [`send_command`][reemote5.drivers.send_command] applying the
   equivalent mutation to the simulated host.

```python
@dataclass(frozen=True)
class ServiceRestartCommand(Command):
    name: str

    def shell(self) -> str:
        return f"systemctl restart {self.name}"
```

Without a `match` arm, `send_command` raises `TypeError` for an unknown
descriptor. A plain [`StringCommand`][reemote5.commands.StringCommand] is
accepted but has **no simulated effect** — it is the escape hatch for commands
the POC does not model.

## Determinism

Lockstep apply pairs requests by position, so the prepare and apply phases must
produce the same request sequence for a given host. The driver enforces this:
when `run_apply` receives the plans, every pulled request is compared against
the accumulated plan, and divergence raises
[`DeterminismError`][reemote5.drivers.DeterminismError].

```python
SITE = "/etc/nginx/sites-enabled/default"


# ✗ Breaks determinism.
def deploy(host):
    yield op(apt.packages, name="Install nginx", packages=("nginx",))
    if host.links.get(SITE) is None:  # reads mutable host state directly
        return
    yield op(files.link, name="Remove default site", src=SITE, present=False)
```

During prepare nothing has run, so `host.links` is empty and the plan holds one
request. During apply, installing nginx created the link (via the host's
`installs_links` side effects), so the generator yields a second request — and
`run_apply` raises `DeterminismError`.

Safe patterns:

- keep the request sequence unconditional, and let operations decide
  *changed vs. no change* internally (this is what
  [`apt.packages`][reemote5.operations.apt.packages] and
  [`files.link`][reemote5.operations.files.link] do);
- branch only on **immutable** inputs — host name, inventory data, deploy
  arguments — which read identically in both phases.

## Design invariants

These are the properties the implementation preserves; they matter when
extending the model:

1. **Phase awareness lives only in the drivers.** Facts and operations must not
   inspect a phase flag; the driver expresses the difference through cache
   lifetime and whether it forwards commands.
2. **The cache is recreated before each `next(generator)` in apply**, and the
   same cache is used to materialize that step's request — so an operation's
   fact reads observe the effects of all previously executed operations.
3. **Generators cannot be replayed.** `run_prepare` returns a fresh
   `deploy(host)` generator inside each [`HostRuntime`][reemote5.core.HostRuntime].
4. **Requests must be hashable and comparable**, hence the explicit
   `__hash__` on `OperationRequest` and the immutable-container rule.
5. **Operations yield typed descriptors**, not strings, so `send_command` can
   simulate their effects.
