# Guide

This guide follows the dependency order of the model itself: the two-phase
execution cycle first, then the hosts and inventory it runs against, then how to
write and compose deploy scripts and how to run them, then the rules that keep
the two phases in agreement, then the three extension points (facts, operations,
command descriptors) and the invariants they must respect, and finally the
orthogonal [actions](#actions) escape hatch — which is defined *against* all of
the above, and so comes last.

## The two phases

Every deploy runs in two phases because a run has to answer two questions in a
fixed order: *what would this change* — so the answer can be reviewed, reported,
or simply stopped there as a dry run — and *then actually change it*, across many
hosts at once without any host racing ahead of another. Prepare computes the
first answer, apply produces the second.

**Prepare** — [`run_prepare`][reemote5.drivers.run_prepare] exhausts each
host's generator, materializes every operation to learn its
`(changed, reason, commands)`, and returns the plan. No host is mutated and no
command is sent. It also builds a *fresh* generator per host for the apply phase,
since a generator cannot be replayed once exhausted.

**Apply** — [`run_apply`][reemote5.drivers.run_apply] advances all hosts in
lockstep: every host executes its Nth request before any host pulls request
N+1. For each step it recreates the fact cache, materializes the request, and
if `ctx.changed` is true, awaits
[`send_command`][reemote5.drivers.send_command] for each command. Every step is
recorded as a [`LogEntry`][reemote5.drivers.LogEntry], including the commands
that were actually sent.

**Determinism guard** — apply re-checks each pulled request against the plan.
If a deploy body branches on mutable state and the two phases disagree, the
driver raises [`DeterminismError`][reemote5.drivers.DeterminismError] instead of
silently applying a different plan. The rules this imposes on a deploy body are
in [Determinism](#determinism).

Recreating the cache at every apply step is what makes sequential *"act, then
read"* logic correct: an operation's fact reads observe everything the earlier
operations changed. Neither facts nor operations know which phase they run in —
phase awareness lives in the drivers alone, as [Design
invariants](#design-invariants) spells out — so one generator body yields both a
correct plan and a correct run.

## Hosts and the inventory

Hosts and their SSH connection information are specified as a simple
*inventory*: a list of dictionaries, each describing one host. This is the
parameter the library functions accept. `host` and `connection` are required;
`privilege`, `group` and `tags` are optional:

```python
inventory = [
    {
        "host": "web1",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
        "group": "centos",            # static classifier
        "tags": ["web", "canary"],    # static classifier
    },
    {
        "host": "web2",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
        "group": "centos",
        "tags": ["web"],
        "privilege": {"sudo_user": "deploy"},  # optional elevation settings
    },
]
```

[`Host.from_inventory`][reemote5.hosts.Host.from_inventory] materializes each
entry into a [`Host`][reemote5.hosts.Host] carrying `connection`, `privilege`,
the `group`/`tags` classifiers, and any unrecognised keys (preserved in `extra`,
so the format is extensible). That data reaches both operations
([`send_command`][reemote5.drivers.send_command]) and facts (each `process`) —
and it is exactly what the real asyncssh transport uses to connect and to
elevate. `group` and `tags` are the *static* values a `when` guard or a deploy
body reads to branch per host (see [Deploy scripts](#deploy-scripts)).

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
`DockerHost` that runs the very same
deploy inside a local Docker/Podman container (see
[the docker connector](connectors/docker.md)).

`SimulatedHost` is part of the *test framework*, not of the inventory or the
library API: it lives in `tests/simulated_host.py`. It subclasses `Host` and
adds the in-memory state the facts read — `execute_command` plus some 180 state
fields, one family per module: `packages` (the dnf name → installed-versions
store), `links`, `existing_files`, `remote_files`, `apt_packages`,
`systemd_unit_properties`, `lxd_instances` and the rest:

```python
SimulatedHost(
    name="web1",
    connection={"key_path": "~/.ssh/id_ed25519"},
    packages={"nginx": ["1.24.0-1.el9"]},
    links={},
    installs_links={
        "nginx": {
            "/etc/nginx/sites-enabled/default": "/etc/nginx/sites-available/default"
        }
    },
)
```

It is injected from outside via `run`'s `host_factory` keyword — the pytest
suites pass a factory building `SimulatedHost`s (e.g.
`tests/modules/dnf/test_dnf.py`), so the tests run without SSH while the library
itself never constructs one. The `examples/` scripts use the default factory and
connect for real; only their request *building* is covered offline, by
`tests/test_examples.py`:

```python
plans, report, hosts = await run(inventory, deploy, host_factory=my_simulated_factory)
```

Two scripts are the deliberate exceptions to "connect for real":
`examples/demonstrations/composition/composed_group_tag_roles.py` injects a
simulated factory and runs entirely in memory, and
`examples/actions/action_in_deploy_guard.py` never opens a transport because its
guard aborts during prepare — see [Examples](examples.md).

`installs_links` is what lets an in-memory run reproduce the interesting case:
on a fresh host, installing nginx *creates* the default-site link, so the next
operation sees state that did not exist during prepare. Over SSH the same effect
comes from the package itself — which is why the real-host examples need no such
state to demonstrate the sequencing.

Nothing in a deploy body or an operation touches host state directly. In the
test framework the only mutation path is the
[`send_command`][reemote5.drivers.send_command] → `execute_command` seam; on a
real host the commands themselves change the machine over SSH, and the next
step's fact reads observe that.

## Deploy scripts

A deploy script is a generator that takes a host and yields
[`OperationRequest`][reemote5.core.OperationRequest] objects. The signature is
fixed:

```python
def deploy(host: Host) -> Generator[OperationRequest, None, None]:
```

Requests are built with the [`op`][reemote5.core.op] helper, which takes the
operation function, an optional human-readable `name` for plan output, optional
`doas`, `sudo` and `su` elevation flags, an optional `when` eligibility guard,
and the operation's parameters **as keyword arguments only**:

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

`name`, `doas`, `sudo`, `su` and `when` are *meta-parameters*: they tell the
driver how to run (and label, or skip) the operation rather than being forwarded
to it, so `sudo=True` does not require the operation function to accept a `sudo`
argument. When set, the driver elevates every command the operation yields -- the
real asyncssh
transport wraps its shell with `doas`, `sudo` and/or `su`, and
[`format_log`][reemote5.drivers.format_log] renders the elevated command. The
test framework's `SimulatedHost` seam ignores them, since privilege has no
in-memory effect. Elevation is per operation, so a privileged package install
and an unprivileged link change can sit side by side in one deploy.

`doas=True`/`sudo=True`/`su=True` only *ask* to elevate -- they never say as
whom, or with what credential. The run-as users and passwords come from the
host's inventory `privilege` section (see
[Hosts and the inventory](#hosts-and-the-inventory)), so a deploy script cannot
choose its own privilege parameters; when a `sudo_user` other than `root` is
configured the driver emits `sudo -u <user>` instead of a bare `sudo`, a
`su_user` becomes `su <user> -c`, and a `doas_user` becomes `doas -n -u <user>`
(`root`, the default target of all three, collapses to a bare `sudo` / `su -c` /
`doas -n`). A sudo or su password is supplied through an askpass helper without
ever appearing in the log; `doas` is *passwordless* -- reemote5 always runs
`doas -n` (non-interactive), so the host must grant the login user a
`permit nopass` rule in `/etc/doas.conf`. When an operation carries several
flags, the escalations combine in a fixed order -- `doas` then `sudo` then
`su` -- so `doas` is the outermost escalation and `su` the innermost payload. Only
*default* su is supported (no login shell). A credential the host rejects -- or a
doas rule it denies -- is reported as a
[`PrivilegeError`][reemote5.hosts.PrivilegeError] naming the host, not as a
failed command — see
[Privilege: sudo, su and doas](hosts.md#privilege-sudo-su-and-doas).

`doas`, `sudo` and `su` are a *tri-state* ([`Privilege`][reemote5.core.Privilege]),
not a plain bool, so a request can say three distinct things: `ON` (`sudo=True`)
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
that decides **whether** a request runs on a host, orthogonally to
`doas`/`sudo`/`su` deciding **how** it runs. A request whose guard fails on a host is skipped there
— never materialized, no facts read, nothing sent — but keeps its slot in the
sequence, so every host shares one schedule and the plan and run log show
`[skipped: when guard is_canary]` side by side with the hosts that run it (the
reason names the failing *predicate*, not the request):

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
   not `packages=["nginx"]`. Lockstep pairing — and the determinism guard —
   compare requests by *value*, so a list is not detected there; what the model
   cannot carry is an *unhashable* value, since `compose(dedupe=True)` hashes
   every argument value to collapse duplicates. `OperationRequest` therefore
   rejects an unhashable `args`/`kwargs` value as soon as the request is built,
   raising
   [`UnhashableArgumentError`][reemote5.core.UnhashableArgumentError] at the
   deploy line instead of a bare `TypeError` from inside a set insertion.
2. **Yield requests; do not call operations.** `op(...)` only *builds* a
   request — the driver sees one when the body *yields* it. Calling
   `dnf.packages(...)` directly cannot work anyway, because an operation takes
   the [`OpContext`][reemote5.core.OpContext] only the driver holds, so the call
   fails on argument binding; and building a request then dropping it would lose
   the operation in silence, since the shortened sequence is equally short in
   both phases and so never trips the determinism guard. The driver therefore
   enforces the shape at both points where it invokes a deploy body, raising
   [`DeployRequestError`][reemote5.core.DeployRequestError] when the body returns
   no request stream at all, when it hands over something that is not an
   [`OperationRequest`][reemote5.core.OperationRequest], or when it builds more
   requests than it yields (a
   [`compose`][reemote5.core.compose]`(..., dedupe=True)` collapse is accounted
   for, so a deliberate duplicate is not a failure). In a normal run that means
   failing during prepare, before a single command is sent.
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
plans, and `doas=True`/`sudo=True`/`su=True` set an escalation *policy* that elevates every
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
*effective identity* (`func`, args, kwargs, `when` and the *resolved*
`doas`/`sudo`/`su` decision — everything that decides whether and how a request
executes, but not the plan `name`) to their first occurrence, so
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
             path="/etc/httpd/conf.d/welcome.conf", present=False, sudo=True)

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
  plans and logs and is excluded from the dedupe key, while `doas`/`sudo`/`su`
  contribute their **resolved** elevate-or-not decision (they change what
  executes, and because only `ON` elevates, a deliberate `OFF` and an unflagged
  `UNSET` are the same request here), and `when` participates as given (it
  changes whether a request executes). The first occurrence wins and
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

Here `host.links` is the test framework's in-memory seam; the same mistake on a
real host is a fact read written into the deploy body. Either way, during
prepare nothing has run, so the store is empty and the plan holds one request.
During apply, installing nginx created the link (via the host's `installs_links`
side effects), so the generator yields a second request — and `run_apply` raises
`DeterminismError`.

Safe patterns:

- keep the request sequence unconditional, and let operations decide
  *changed vs. no change* internally (this is what
  [`dnf.packages`][reemote5.modules.dnf.dnf.packages] and
  [`files.link`][reemote5.modules.files.files.link] do; a deliberately
  *non-idempotent* operation such as
  [`dnf.update`][reemote5.modules.dnf.dnf.update] simply reports a change
  unconditionally, which is equally phase-stable);
- branch only on **immutable** inputs — host name, inventory data, deploy
  arguments — which read identically in both phases;
- guard per-host eligibility with a `when` predicate over static data instead
  of an `if` in the deploy body — the request sequence stays identical on every
  host and the driver skips ineligible ones (see
  [Deploy scripts](#deploy-scripts)). Like the operation functions themselves,
  predicates must be stable function objects: an inline `lambda` is recreated
  on each phase invocation and compares unequal, raising `DeterminismError`.

## Facts

A fact is a class with an `async` `process` classmethod returning the raw
value. Facts are asynchronous, phase-unaware, and never yielded by a
generator. `process` takes the fact's own arguments after the host, and reads
state the same way whether or not a transport is present: through a
`SimulatedHost` seam when the host provides one, over asyncssh when it does not.

```python
from reemote5 import Host


class EnabledSites:
    """The set of nginx sites currently enabled on the host."""

    @classmethod
    async def process(cls, host: Host) -> set[str]:
        links = getattr(host, "links", None)
        if links is not None:
            # SimulatedHost seam: read the in-memory state directly.
            prefix = "/etc/nginx/sites-enabled/"
            return {src for src in links if src.startswith(prefix)}
        # Real transport: list the site directory over asyncssh. The `|| true`
        # guard keeps a missing directory an empty answer rather than a failed
        # command, exactly as the seam's empty mapping reads.
        stdout = await host.run_shell(
            "ls -1 /etc/nginx/sites-enabled 2>/dev/null || true"
        )
        return {f"/etc/nginx/sites-enabled/{name}" for name in stdout.split()}
```

That is the shape of every library fact: a `process` that runs one (or a few)
shell commands and parses the output. Facts live in the namespace modules beside
the operations that consume them —
[`FileLink`][reemote5.modules.files.files.FileLink] in
`reemote5.modules.files.files`, `DnfPackages` in `reemote5.modules.dnf.dnf` —
and their docstrings record the commands they run and the shape they return. An
enumerating fact returns a `TypedDict` or a list of shared entry `TypedDict`s, so
reads stay deterministic and JSON-printable.

Always read facts inside a deploy through the cache, never by calling `process`
directly — the cache is the boundary whose lifetime the driver controls, and its
`get` is a coroutine, so it must be awaited:

```python
enabled = await ctx.facts.get(EnabledSites)
```

Cache keys include the fact class and its arguments, so repeated reads within
one step are free.

Outside a deploy, [`gather_fact`][reemote5.drivers.gather_fact] fans one fact
out across the hosts concurrently and returns a `{host.name: value}` mapping:

```python
values = await gather_fact(hosts, EnabledSites)
```

A deploy cannot use that value directly — a fact read inside the body would see
different state in prepare and apply — but a *snapshot* of it can: run
`gather_fact`, build the deploy from a factory closing over the returned
mapping, then run that deploy. The factory is a pure function of static input,
so both phases resolve identically and the [determinism](#determinism) guard
holds. This is the **two-deploy fact split**, demonstrated end to end by
`examples/demonstrations/composition/two_deploy_split.py` and collected on the
[composition examples page](examples/demonstrations/composition.md).

## Operations

An operation is an async generator receiving the
[`OpContext`][reemote5.core.OpContext] as its first argument:

```python
async def operation(ctx: OpContext, *args, **kwargs) -> AsyncGenerator[Command]:
```

Its contract:

- await facts via `await ctx.facts.get(...)`;
- set `ctx.changed = True` and a short `ctx.reason` at the point where it
  decides a change is needed (leave `changed` false and still set `reason`
  for the "no change" explanation shown in plans);
- yield one command descriptor per command that would mutate the host;
- never execute anything and never touch the host directly.

Two deliberate relaxations of that contract exist, and neither breaks phase
agreement because both stay independent of host state:

- a **non-idempotent** operation reads no fact and always reports a change,
  yielding its command on every materialization —
  [`dnf.update`][reemote5.modules.dnf.dnf.update] (and `apt.key`, which imports a
  key stream) are the library's examples;
- an operation whose **prerequisite is missing on the host** raises
  [`OperationError`][reemote5.core.OperationError] while materializing —
  `server.timezone` needs `timedatectl` and is systemd-only — which in a deploy
  run aborts during prepare, before anything is sent.

The built-in [`files.link`][reemote5.modules.files.files.link] in full:

```python
async def link(
    ctx: OpContext, src: str, target: str | None = None, present: bool = True
) -> AsyncGenerator[Command]:
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
`reemote5.modules.files.files`). There are three kinds.
[`send_command`][reemote5.drivers.send_command] checks for a
`SimulatedHost` seam first, so a test host applies its in-memory effect and
nothing else is considered; on a real host it then dispatches on the
descriptor kind, `TransferCommand` and `WaitCommand` before the shell case:

1. **Shell descriptors** — the ordinary kind, and the fallback case: anything
   that is neither a transfer nor a wait runs its command line. A `shell()`
   method returning that line is what makes a descriptor useful twice over: it
   is the text the real transport *runs* (`host.run_shell` /
   `host.run_shell_privileged`) *and* the text
   [`format_log`][reemote5.drivers.format_log] prints, prefixed with the
   resolved `doas`/`sudo`/`su`. A descriptor without `shell()` falls back to its
   dataclass `repr`, so every shell descriptor must define it.
2. **`TransferCommand`** ([`reemote5.commands.TransferCommand`][reemote5.commands.TransferCommand]) —
   a descriptor that copies bytes instead of running shell text: `files.put`'s
   and `files.get`'s transfers, and the `/tmp` spools `dnf.repo`, `dnf.key` and
   their apt counterparts write before installing them. It has no `shell()`; the
   driver awaits its `transfer`, which rides the connection's own scp channel as
   the *login* user, so the `doas`/`sudo`/`su` flags do not apply to it — and the
   log says so rather than printing a misleading `sudo scp ...`.
3. **`WaitCommand`** ([`reemote5.commands.WaitCommand`][reemote5.commands.WaitCommand]) —
   a descriptor that polls the host until a condition holds, used by
   `server.wait` and `server.wait_service`. Likewise unprivileged; on timeout
   it raises `WaitTimeoutError`. The wait lives in a descriptor rather than in
   the operation body because prepare never sends: an operation that polled
   inside its own generator would spin against the prepare cache forever.

```python
@dataclass(frozen=True)
class ServiceRestartCommand(Command):
    name: str

    def shell(self) -> str:
        return f"systemctl restart {self.name}"
```

How an unknown or unmodelled descriptor behaves depends on the host, not on the
descriptor: a `SimulatedHost` whose `execute_command` has no `match` arm for it
raises `TypeError(f"unknown command descriptor: ...")`, while a plain
[`Host`][reemote5.hosts.Host] simply runs its `shell()` over SSH. A plain
[`StringCommand`][reemote5.commands.StringCommand] is accepted — its `shell()`
returns the raw text verbatim, so it runs and elevates exactly like a typed
shell descriptor — but it has **no simulated effect**, which makes it the
vehicle for a one-off command a demonstration needs and the library does not
model.

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
4. **Requests must be comparable by value, and hashable where they are
   deduplicated.** Value equality is what pairs hosts in lockstep and what the
   determinism guard checks; hashing happens only in `compose(dedupe=True)`,
   hence the explicit `__hash__` on `OperationRequest`, the immutable-container
   rule, and the `UnhashableArgumentError` raised in `__post_init__`.
5. **Operations yield typed descriptors**, not strings, so the simulated host's
   `execute_command` can apply their effects and the real driver can dispatch on
   the descriptor *kind* (shell, transfer, wait).

## Actions

Not everything on a server is a fact reemote5 can read or an operation it can
manage. For the pragmatic cases — bespoke software with no fact and no
operation, or connectivity itself — an **action** acts *now* and hands back
what happened. [`Shell`][reemote5.actions.Shell] runs a shell command,
[`Script`][reemote5.actions.Script] (also reachable as `server.script`) runs a
script file already staged on the host,
[`WaitForConnection`][reemote5.actions.WaitForConnection] waits for hosts to
become reachable again (e.g. after a reboot), and
`gather_action` fans any of them out across the
hosts exactly as [`gather_fact`][reemote5.drivers.gather_fact] does a fact,
returning a `{host.name: value}` mapping (a `Result` per host for `Shell` and
`Script`, `None` per host for `WaitForConnection`, where the key's presence is
the success signal):

```python
from reemote5 import Host, Shell, gather_action

hosts = [Host.from_inventory(entry) for entry in inventory]
results = await gather_action(hosts, Shell, command="/opt/acme/bin/status", sudo=True)
for name, result in results.items():
    print(name, result["exit_status"], result["stdout"])
```

Actions are **orthogonal** to the prepare/apply model, and the split is
enforced, not conventional:

- **Call them before, between or after deploys — never inside one.** The
  awaited `gather_action` is a barrier: every host's command has completed
  before the next deploy or fact read. Calling it inside a deploy generator
  raises `ActionInDeployError` at the
  call site (a deploy runs once per phase, so an immediate action would run
  twice and mutate the host during prepare).
- **The outcome is data, not an exception.** A non-zero exit — including 127,
  command-not-found — travels back in the
  [`Result`][reemote5.actions.Result] `TypedDict` (`exit_status`, `stdout`,
  `stderr`); the caller decides what a bespoke command's failure means.
  Library-level
  failures still raise: [`HostConnectionError`][reemote5.hosts.HostConnectionError]
  for an unreachable host and [`PrivilegeError`][reemote5.hosts.PrivilegeError]
  for a rejected credential, each aggregated across hosts.
- **Privilege is honoured, but asked for only.** `doas=True`/`sudo=True`/`su=True`
  are plain bools (the execution-layer spelling, not the plan layer's
  [`Privilege`][reemote5.core.Privilege] tri-state); the run-as users and
  passwords still come from the inventory `privilege` section, never the call.
- **Each call re-connects.** Outside a deploy the fan-out closes every host's
  connection when it finishes, so the next call opens a fresh one — an action
  can follow a [`run`][reemote5.drivers.run] transparently.
- **Hosts can be selected.** The fan-out's `group`/`tags` keywords filter the
  inventory's static classifiers before anything runs (`group` matches
  `host.group`, `tags` requires every listed tag), so an action can target a
  subset of the inventory.

[`WaitForConnection`][reemote5.actions.WaitForConnection] is the reboot
companion: after a deploy configures hosts and a `Shell` action reboots them,
`await gather_action(hosts, WaitForConnection, timeout=300, retry=5)` polls
each selected host's SSH connect — retrying every `retry` seconds — until all
have come back, so the next deploy starts only against reachable hosts. A host
still down when its `timeout` expires raises the fatal
`ConnectionTimeoutError`, aggregated
across hosts.

[`Script`][reemote5.actions.Script] is the `server.script` companion for
staged scripts: a deploy transfers the file (`files.put`), then
`await gather_action(hosts, server.script, path="/tmp/setup.sh")` runs it --
the action never transfers anything itself. An executable file runs directly
(the kernel honours its shebang); a non-executable file runs through `sh`;
`interpreter="python3 -u"` overrides both branches. A missing file is an
error `Result` (exit 127 with a `server.script: no such file` message on
stderr), never an exception.

To let a deploy depend on a bespoke command's outcome, use the **deploy →
action → deploy** split: run the action *between* two complete prepare/apply
cycles, freeze its `Result` dict, and build the second deploy from a factory
closing over that snapshot — a pure function of static input, so both phases
resolve identically and the [determinism](#determinism) guard holds. This is the
action analogue of the [two-deploy fact split](#facts). See the
[Actions reference](actions/actions.md) for the full contract and the
[worked examples](examples/actions.md).
