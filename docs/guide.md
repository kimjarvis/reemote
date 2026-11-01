# Guide

This guide covers how to write against the model: deploy scripts first, then
the three extension points (facts, operations, command descriptors), the
orthogonal [actions](#actions) escape hatch, and the rules that keep both
phases in agreement.

## Deploy scripts

A deploy script is a generator that takes a host and yields
[`OperationRequest`][reemote5.core.OperationRequest] objects. The signature is
fixed:

```python
def deploy(host: Host) -> Generator[OperationRequest, None, None]:
```

Requests are built with the [`op`][reemote5.core.op] helper, which takes the
operation function, an optional human-readable `name` for plan output, optional
`sudo` and `su` elevation flags, an optional `when` eligibility guard, and the
operation's parameters **as keyword arguments only**:

```python
from reemote5 import dnf, files, op

yield op(dnf.packages, name="Install nginx", packages=("nginx",), present=True, sudo=True)
yield op(
    files.link,
    name="Enable site",
    src="/etc/nginx/sites-enabled/site",
    target="/etc/nginx/sites-available/site",
)
```

`name`, `sudo`, `su` and `when` are *meta-parameters*: they tell the driver how
to run (and label, or skip) the operation rather than being forwarded to it, so
`sudo=True` does not require the operation function to accept a `sudo` argument.
When set, the driver elevates every command the operation yields -- the real
asyncssh
transport wraps its shell with `sudo` and/or `su`, and
[`format_log`][reemote5.drivers.format_log] renders the elevated command. The
test framework's `SimulatedHost` seam ignores them, since privilege has no
in-memory effect. Elevation is per operation, so a privileged package install
and an unprivileged link change can sit side by side in one deploy.

`sudo=True`/`su=True` only *ask* to elevate -- they never say as whom, or with
what credential. The run-as users and passwords come from the host's inventory
`privilege` section (see
[Hosts and the inventory](#hosts-and-the-inventory)), so a deploy script cannot
choose its own privilege parameters; when a `sudo_user` other than `root` is
configured the driver emits `sudo -u <user>` instead of a bare `sudo`, and a
`su_user` becomes `su <user> -c` (`root`, the default target of both, collapses
to a bare `sudo` / `su -c`). A password is supplied through an askpass helper
without ever appearing in the log. When an operation is flagged both `sudo=True`
and `su=True`, the escalations combine in a fixed order -- `sudo` then `su` --
so sudo is the outer escalation and runs su inside it. Only *default* su is
supported (no login shell). A credential the host rejects is reported as a
[`PrivilegeError`][reemote5.hosts.PrivilegeError] naming the host, not as a
failed command — see [Privilege: sudo and su](hosts.md#privilege-sudo-and-su).

`sudo` and `su` are a *tri-state* ([`Privilege`][reemote5.core.Privilege]), not
a plain bool, so a request can say three distinct things: `ON` (`sudo=True`)
elevates, `OFF` (`sudo=False`) deliberately runs unprivileged, and `UNSET` (the
default, when the flag is omitted) means “no opinion”. Only `ON` elevates — the
driver collapses the tri-state to a concrete bool before a command is sent. The
distinction matters when composing: because `UNSET` and `OFF` are different, a
[`sub_deploy`][reemote5.core.sub_deploy] policy can *force* elevation across a
role while still honouring a request that explicitly opted out with
`sudo=False` — something a plain bool cannot express, since it cannot tell “run
this one unprivileged” from “never flagged”. (`op` and `sub_deploy` still accept
a plain bool for convenience: `True` → `ON`, `False` → `OFF`.)

`when` is the *eligibility* meta-parameter: a predicate over static host data
that decides **whether** a request runs on a host, orthogonally to `sudo`/`su`
deciding **how** it runs. A request whose guard fails on a host is skipped there
— never materialized, no facts read, nothing sent — but keeps its slot in the
sequence, so every host shares one schedule and the plan and run log show
`[skipped: when guard <name>]` side by side with the hosts that run it:

```python
def is_canary(host: Host) -> bool:
    return "canary" in host.tags

yield op(dnf.packages, name="Install canary tooling",
         packages=("strace", "tcpdump"), sudo=True, when=is_canary)
```

This is the declarative alternative to `if host.name == ...` branching in the
deploy body (which changes the request *sequence* per host). Two rules keep it
deterministic: the predicate reads only **static** inventory data (`host.name`,
`host.group`, `host.tags`, ...), never mutable facts, so both phases reach the
same skip decision; and it is a **stable function object** defined at module or
composition level — an inline `lambda` in the deploy body is a fresh object on
each phase invocation, which the determinism guard correctly reports as
divergence. A skipped request is a third outcome beside *changed* and
*no change*: ineligible regardless of state, versus nothing to do.

Three rules follow from the model:

1. **Use immutable containers for collection parameters.** `packages=("nginx",)`
   not `packages=["nginx"]`. Requests are compared and hashed to pair "the Nth
   operation on host A" with "the Nth operation on host B"; a `list` inside
   `kwargs` breaks hashing.
2. **Yield requests; do not call operations.** Calling `dnf.packages(...)`
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
    yield op(dnf.packages, name="Install nginx", packages=("nginx",))
    yield op(
        files.link,
        name="Remove default site",
        src="/etc/nginx/sites-enabled/default",
        present=False,
    )
```

## Composing deploys

Deploys are plain iterables of frozen requests, so they nest and combine
without driver involvement. A sub-deploy is just another
`(host) -> Generator[OperationRequest]` callable, and the simplest composition
is delegation inside the deploy body:

```python
def deploy(host):
    yield from role_baseline(host)
    yield from role_httpd(host)
```

Two helpers in [`reemote5.core`][reemote5.core] cover what delegation cannot.
[`sub_deploy`][reemote5.core.sub_deploy] wraps a sub-deploy with default
meta-parameters: `name_prefix` labels every request it produces for readable
plans, and `sudo=True`/`su=True` set an escalation *policy* that elevates every
request that did not deliberately opt out — the inner request wins whenever it
has an opinion (`sudo=True`/`sudo=False`), and the policy only fills the
unflagged (`UNSET`) ones, so a forced-elevation policy still respects an
explicit `sudo=False` (see the [`Privilege`][reemote5.core.Privilege] tri-state
above). `when=...` sets a role-level *eligibility guard* — the analogue of
Ansible's block-level `when` — which, unlike the escalation policy, composes by
conjunction: the wrapper's predicates and each request's own `when` must all
pass, so a wrapper can only restrict eligibility, never widen it.
[`compose`][reemote5.core.compose] concatenates sub-deploys into one
deploy-shaped callable in argument order — an explicit cross-role ordering
statement — and with `dedupe=True` collapses requests sharing the same
*effective identity* (`func`, args, kwargs, `sudo`, `su` and `when` — everything
that decides whether and how a request executes, but not the plan `name`) to
their first occurrence, so
independently written roles can each declare the same prerequisite under
their own label:

```python
from reemote5 import compose, dnf, files, op, sub_deploy

def role_baseline(host):
    yield op(dnf.packages, name="Install baseline tools",
             packages=("curl",), sudo=True)

def role_httpd(host):
    # Re-declares its prerequisite: dedupe collapses the duplicate.
    yield op(dnf.packages, name="Install baseline tools",
             packages=("curl",), sudo=True)
    yield op(dnf.packages, name="Install httpd", packages=("httpd",), sudo=True)
    yield op(files.file, name="Remove welcome configuration",
             src="/etc/httpd/conf.d/welcome.conf", present=False, sudo=True)

def role_httpd_tools(host):
    # Written for a root login: no sudo flags anywhere.
    yield op(dnf.packages, name="Install httpd tools", packages=("httpd-tools",))

deploy = compose(
    role_baseline,
    role_httpd,                                  # shares the baseline request
    sub_deploy(role_httpd_tools, name_prefix="httpd", sudo=True),
    dedupe=True,
)
```

Rules that keep composition deterministic:

- Wrappers must be **pure stream transforms** — no state shared between
  invocations, no reads of mutable host state. Both helpers create any
  internal state (the dedupe set) per call, so the prepare and apply phases
  flatten identically and the determinism guard holds.
- Dedupe collapses by *effective identity*: `name` only labels a request in
  plans and logs and is excluded from the dedupe key, while `sudo`/`su` (they
  change what executes) and `when` (it changes whether a request executes) are
  part of it. The first occurrence wins and
  keeps its name, so identically-parameterized requests collapse even under
  different labels or `name_prefix`es — at the cost of silently dropping the
  later label. `OperationRequest` equality itself stays maximal (name
  included), because the determinism guard pairs full values.
- Sub-deploys stay **sync generators** and inherit every deploy-body rule
  above — in particular, do not branch on mutable facts inside them.
- Branch on **static** data instead: assemble the deploy from roles at call
  time (per environment, or per host from inventory data such as `host.name`),
  so the same composed callable is used in both phases.

Runnable demonstrations of each pattern — deduplication, environment layering,
privilege policies, role ordering, per-host branching, eligibility guards and
patch windows — live
in [Examples](examples/demonstrations/composition.md).

## Running a deploy

[`run`][reemote5.drivers.run] is the one-call entry point. It takes the
*inventory* — the list of host dictionaries described under
[Hosts and the inventory](#hosts-and-the-inventory) — and is a coroutine, so
it must be awaited:

```python
plans, report, hosts = await run(inventory, deploy)  # prepare + apply
plans, report, hosts = await run(inventory, deploy, dry=True)  # prepare only, report is None
```

It returns the plan keyed by host name, the
[`ApplyReport`][reemote5.drivers.ApplyReport] and the materialized hosts (so
callers can inspect their final state). Render either with
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
[`run_apply`][reemote5.drivers.run_apply], which take materialized
[`Host`][reemote5.hosts.Host] objects:

```python
hosts = [Host.from_inventory(entry) for entry in inventory]
runtimes, plans = await run_prepare(hosts, deploy)
print(format_plan(plans))
report = await run_apply(runtimes, plans=plans)  # plans= enables the determinism guard
```

## Actions

Not everything on a server is a fact reemote5 can read or an operation it can
manage. For the pragmatic case — bespoke software with no fact and no
operation — an **action** runs a shell command *now* and hands back what it
produced. [`Shell`][reemote5.actions.Shell] is the first action kind, and
[`gather_action`][reemote5.actions.gather_action] fans it out across the
inventory exactly as [`gather_fact`][reemote5.drivers.gather_fact] does a fact,
returning a `{host.name: Result}` mapping:

```python
from reemote5 import Host, Shell, gather_action

hosts = [Host.from_inventory(entry) for entry in inventory]
results = await gather_action(hosts, Shell, command="/opt/acme/bin/status", sudo=True)
for name, result in results.items():
    print(name, result.exit_status, result.stdout)
```

Actions are **orthogonal** to the prepare/apply model, and the split is
enforced, not conventional:

- **Call them before, between or after deploys — never inside one.** The
  awaited `gather_action` is a barrier: every host's command has completed
  before the next deploy or fact read. Calling it inside a deploy generator
  raises [`ActionInDeployError`][reemote5.actions.ActionInDeployError] at the
  call site (a deploy runs once per phase, so an immediate action would run
  twice and mutate the host during prepare).
- **The outcome is data, not an exception.** A non-zero exit — including 127,
  command-not-found — travels back in the frozen
  [`Result`][reemote5.actions.Result] (`exit_status`, `stdout`, `stderr`, `.ok`);
  the caller decides what a bespoke command's failure means. Library-level
  failures still raise: [`HostConnectionError`][reemote5.hosts.HostConnectionError]
  for an unreachable host and [`PrivilegeError`][reemote5.hosts.PrivilegeError]
  for a rejected credential, each aggregated across hosts.
- **Privilege is honoured, but asked for only.** `sudo=True`/`su=True` are plain
  bools (the execution-layer spelling, not the plan layer's
  [`Privilege`][reemote5.core.Privilege] tri-state); the run-as users and
  passwords still come from the inventory `privilege` section, never the call.
- **Each call re-connects.** Outside a deploy the fan-out closes every host's
  connection when it finishes, so the next call opens a fresh one — an action
  can follow a [`run`][reemote5.drivers.run] transparently.

To let a deploy depend on a bespoke command's outcome, use the **deploy →
action → deploy** split: run the action *between* two complete prepare/apply
cycles, freeze its `Result` dict, and build the second deploy from a factory
closing over that snapshot — a pure function of static input, so both phases
resolve identically and the [determinism](#determinism) guard holds. This is
the action analogue of the two-deploy fact split. See the
[Actions reference](actions.md) for the full contract and the
[worked examples](examples/actions.md).

## Hosts and the inventory

Hosts and their SSH connection information are specified as a simple
*inventory*: a list of dictionaries, each describing one host. This is the
parameter the library functions accept:

```python
inventory = [
    {
        "host": "web1",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
    },
    {
        "host": "web2",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
        "privilege": {"sudo_user": "deploy"},  # optional elevation settings
    },
]
```

[`Host.from_inventory`][reemote5.hosts.Host.from_inventory] materializes each
entry into a [`Host`][reemote5.hosts.Host] carrying `connection`,
`privilege` and any unrecognised keys (preserved in `extra`, so the format is
extensible). That data reaches both operations
([`send_command`][reemote5.drivers.send_command]) and facts (each `process`),
which is where the real asyncssh transport will use it.

Every entry must carry at least `host` and `connection`. The inventory is
validated with pydantic (against
[`InventoryEntry`][reemote5.hosts.InventoryEntry]) before any host is
materialized: [`run`][reemote5.drivers.run] checks the whole inventory up front
via [`validate_inventory`][reemote5.hosts.validate_inventory], so a malformed
one — not a list, an entry that is not a dictionary, or a missing required key
— raises [`InventoryError`][reemote5.hosts.InventoryError] instead of failing
later over SSH.

The `connection` mapping may also carry a `connector` key selecting the
transport: the default `"ssh"` builds the asyncssh-backed `Host` shown here,
while `"docker"` builds a
[`DockerHost`][reemote5.connectors.docker.DockerHost] that runs the very same
deploy inside a local Docker/Podman container (see
[the docker connector](connectors/docker.md)).

`SimulatedHost` is part of the *test framework*, not of the inventory or the
library API: it lives in `tests/simulated_host.py`. It subclasses `Host` and
adds the in-memory state facts read — `packages` and `links` — plus an
optional `installs_links` map describing the side effects of installing a
package:

```python
SimulatedHost(
    name="web1",
    connection={"key_path": "~/.ssh/id_ed25519"},
    packages=set(),
    links={},
    installs_links={
        "nginx": {
            "/etc/nginx/sites-enabled/default": "/etc/nginx/sites-available/default"
        }
    },
)
```

It is injected from outside via `run`'s `host_factory` keyword — the pytest
suite passes a factory building `SimulatedHost`s (see `tests/test_model.py`),
so the tests run without SSH while the library itself never constructs one
(the `examples/` scripts instead use the default factory and connect for
real):

```python
plans, report, hosts = await run(inventory, deploy, host_factory=my_simulated_factory)
```

`installs_links` is what lets the example reproduce the interesting case: on a
fresh host, installing nginx *creates* the default-site link, so the next
operation sees state that did not exist during prepare.

Host state is mutated only through the
[`send_command`][reemote5.drivers.send_command] → `execute_command` seam;
deploy scripts and operations never touch it.

## Facts

A fact is a class with an `async` `process` classmethod returning the raw
value. Facts are asynchronous, phase-unaware, and never yielded by a
generator:

```python
from reemote5 import Host


class EnabledSites:
    """The set of nginx sites currently enabled on the host."""

    @classmethod
    async def process(cls, host: Host) -> set[str]:
        # A real implementation would list /etc/nginx/sites-enabled over
        # asyncssh, using host.connection; the POC reads the
        # simulated state provided by the test framework's SimulatedHost.
        links = getattr(host, "links", None)
        if links is None:
            raise NotImplementedError(
                "real SSH fact gathering is not implemented"
            )
        prefix = "/etc/nginx/sites-enabled/"
        return {src for src in links if src.startswith(prefix)}
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

The built-in [`files.link`][reemote5.modules.files.files.link] in full:

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

Operations live in namespace modules under `reemote5.modules` so deploy
scripts read `dnf.packages` rather than `dnf_packages`. The driver calls
`request.func(ctx, *request.args, **request.kwargs)`, and materializes the
whole command generator to learn `changed` — even during prepare.

## Command descriptors

Commands are frozen dataclasses subclassing
[`Command`][reemote5.commands.Command]; typed descriptors live beside the
operations that yield them (`reemote5.modules.dnf.dnf`,
`reemote5.modules.files.files`).
Two things make a descriptor useful in
this model:

1. A `shell()` method returning the equivalent command line, used by
   [`format_log`][reemote5.drivers.format_log].
2. A `match` arm in the simulated host's `execute_command` (test framework,
   `tests/simulated_host.py`) applying the equivalent mutation; the library's
   [`send_command`][reemote5.drivers.send_command] forwards to it.

```python
@dataclass(frozen=True)
class ServiceRestartCommand(Command):
    name: str

    def shell(self) -> str:
        return f"systemctl restart {self.name}"
```

Without a `match` arm, `execute_command` raises `TypeError` for an unknown
descriptor, and on a host without the seam (a plain
[`Host`][reemote5.hosts.Host]) [`send_command`][reemote5.drivers.send_command]
raises `NotImplementedError`. A plain
[`StringCommand`][reemote5.commands.StringCommand] is accepted but has **no
simulated effect** — it is the escape hatch for commands the POC does not
model.

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
    yield op(dnf.packages, name="Install nginx", packages=("nginx",))
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
  [`dnf.packages`][reemote5.modules.dnf.dnf.packages] and
  [`files.link`][reemote5.modules.files.files.link] do);
- branch only on **immutable** inputs — host name, inventory data, deploy
  arguments — which read identically in both phases;
- guard per-host eligibility with a `when` predicate over static data instead
  of an `if` in the deploy body — the request sequence stays identical on every
  host and the driver skips ineligible ones (see
  [Deploy scripts](#deploy-scripts)). Like the operation functions themselves,
  predicates must be stable function objects: an inline `lambda` is recreated
  on each phase invocation and compares unequal, raising `DeterminismError`.

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
5. **Operations yield typed descriptors**, not strings, so the simulated
   host's `execute_command` can apply their effects.
