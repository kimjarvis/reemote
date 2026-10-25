# reemote5

**reemote5** is a proof-of-concept execution model for configuration
management. It explores an alternative to [pyinfra]'s two-phase
*prepare → execute* design: **deploy scripts are Python generators** that
yield operation descriptors instead of calling operations directly.

That single change lets the driver pause a deploy script at every operation
boundary, which in turn makes sequential *"act, then read"* logic produce the
correct answer in both phases — while keeping the properties that the
two-phase split exists to provide: cross-host lockstep ordering and dry-run
reporting.

!!! warning "POC scope"

    reemote5 is a **library**, not a deployment tool. It is deliberately
    restricted to *generating commands*:

    - no SSH or any other connection transport,
    - no CLI, inventory files or plugin system,
    - hosts are in-memory [`SimulatedHost`][reemote5.hosts.SimulatedHost] objects.

    Nothing here connects to a real machine. The point of the project is the
    execution model, demonstrated end to end and covered by tests.

    The command-execution path *is* asynchronous:
    [`send_command`][reemote5.drivers.send_command] — and therefore
    [`run_apply`][reemote5.drivers.run_apply] and
    [`run`][reemote5.drivers.run] — are coroutines, preparing for a real
    [asyncssh]-based transport (pyinfra uses Paramiko + gevent instead).
    Fact gathering is async for the same reason: a fact's `process`,
    [`FactCache.get`][reemote5.core.FactCache.get], the operations that await
    it, and [`run_prepare`][reemote5.drivers.run_prepare] are all coroutines,
    so the whole prepare/apply path is awaited end to end.

## Why generators?

A deploy is naturally written as a sequence of steps where later steps depend
on the effect of earlier ones. Consider "install nginx, then remove its
default site": the site link only exists *after* the package is installed.

In a two-phase model the prepare phase runs before anything is executed, so a
naive prepare phase cannot see that link:

```text
prepare: install nginx → changed        (nginx not installed yet)
         remove link   → no change      (link does not exist yet — wrong!)
apply:   install nginx → changed        (link now created as a side effect)
         remove link   → ???            (script already decided "no change")
```

pyinfra solves this with its own machinery. reemote5 solves it with the
generator itself:

1. The deploy body is a generator, so the driver controls *when* the next
   step is computed.
2. In apply, the driver recreates the
   [`FactCache`][reemote5.core.FactCache] **before** pulling each request, and
   uses that cache to materialize it.
3. Fact reads made by an operation therefore observe everything the previous
   operations did — sequential semantics, no phase flag required.

The prepare phase still yields a full plan, and the apply phase still runs
every host in lockstep. Neither facts nor operations know which phase they are
in; only the drivers do.

## Installation

The project is managed with [uv] and targets Python 3.13+:

```bash
uv sync                    # library + dev (pytest)
uv sync --group docs       # + mkdocs-material and mkdocstrings
```

## Quick start

A deploy script is a generator with a fixed signature, yielding requests built
with the [`op`][reemote5.core.op] helper:

```python
from collections.abc import Generator

from reemote5 import OperationRequest, SimulatedHost, apt, files, op, run

SITE = "/etc/nginx/sites-enabled/default"
TARGET = "/etc/nginx/sites-available/default"


def deploy(host: SimulatedHost) -> Generator[OperationRequest, None, None]:
    yield op(apt.packages, name="Install nginx", packages=("nginx",))
    yield op(files.link, name="Remove default site", src=SITE, present=False)
```

Hosts are then passed to [`run`][reemote5.drivers.run], which prepares and
applies in one call. `run` is a coroutine, so it must be awaited (here from
an `asyncio.run` entry point):

```python
import asyncio

from reemote5 import format_log, format_plan

hosts = [
    # web1 is fresh: installing nginx creates the default-site link.
    SimulatedHost(name="web1", installs_links={"nginx": {SITE: TARGET}}),
    # web2 already has nginx installed, and still has the default-site link.
    SimulatedHost(name="web2", packages={"nginx"}, links={SITE: TARGET}),
]


async def main() -> None:
    plans, report = await run(hosts, deploy)
    print(format_plan(plans))
    print(format_log(report))


asyncio.run(main())
```

Pass `dry=True` to stop after the prepare phase — nothing is sent to the hosts
and `report` is `None`.

A complete, runnable version of this example — including the final host state
— lives in
[`examples/install_nginx.py`](https://github.com/kimjarvis/reemote5/blob/main/examples/install_nginx.py):

```bash
uv run python examples/install_nginx.py
```

```text
== Plan (prepare phase, --dry) ==
web1: Install nginx            [changed: 1 package(s) to install]
web1: Remove default site      [no change: link already absent]
web2: Install nginx            [no change: all packages present]
web2: Remove default site      [changed: link present, will remove]

== Run log (apply phase) ==
web1: Install nginx            [changed: 1 package(s) to install]
    $ apt-get install -y nginx
web2: Install nginx            [no change: all packages present]
web1: Remove default site      [changed: link present, will remove]
    $ rm -f /etc/nginx/sites-enabled/default
web2: Remove default site      [changed: link present, will remove]
    $ rm -f /etc/nginx/sites-enabled/default

== Final host state ==
web1: packages=['nginx'] links={}
web2: packages=['nginx'] links={}
```

The interesting line is `web1: Remove default site`. The plan says *no change*
because nginx is not installed yet during prepare; the apply log says *changed*
because installing nginx created the link and the fresh fact cache saw it. Same
deploy script, same request sequence, correct outcome in both phases.

## The two phases

**Prepare** — [`run_prepare`][reemote5.drivers.run_prepare] exhausts each
host's generator, materializes every operation to learn its
`(changed, reason, commands)`, and returns the plan. No host is mutated. It
also builds a *fresh* generator per host for the apply phase, since a
generator cannot be replayed once exhausted.

**Apply** — [`run_apply`][reemote5.drivers.run_apply] advances all hosts in
lockstep: every host executes its Nth request before any host pulls request
N+1. For each step it recreates the fact cache, materializes the request, and
if `ctx.changed` is true, awaits
[`send_command`][reemote5.drivers.send_command] for each command to mutate
the simulated host.
Every step is recorded as a [`LogEntry`][reemote5.drivers.LogEntry], including
the commands that were actually sent.

**Determinism guard** — apply re-checks each pulled request against the plan.
If a deploy body branches on mutable state and the two phases disagree, the
driver raises [`DeterminismError`][reemote5.drivers.DeterminismError] instead
of silently applying a different plan.

See the [Guide](guide.md) for the rules this imposes on deploy scripts, and the
[API Reference](api.md) for every object involved.

## Project layout

```text
src/reemote5/
├── core.py            # OperationRequest, OpContext, FactCache, HostRuntime, op()
├── commands.py        # Command descriptors (StringCommand, Apt*, LinkCommand)
├── drivers.py         # run_prepare, run_apply, send_command, run, formatters
├── facts.py           # AptPackages, FileLink
├── hosts.py           # SimulatedHost
└── operations/        # operation namespaces
    ├── apt.py         #   apt.packages
    └── files.py       #   files.link

examples/install_nginx.py   # end-to-end usage example (not part of the library)
tests/test_model.py         # model tests, incl. the nginx trace and determinism
docs/                       # this documentation
```

## Development

```bash
uv run pytest                        # run the tests
uv run --group docs mkdocs serve     # live-reloading docs on localhost:8000
uv run --group docs mkdocs build     # build the static site into site/
```

[pyinfra]: https://pyinfra.com/
[uv]: https://docs.astral.sh/uv/
[asyncssh]: https://asyncssh.readthedocs.io/
