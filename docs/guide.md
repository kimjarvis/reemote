# Guide

reemote configures and deploys servers through a **pure Python API**. A deployment is ordinary Python code that describes the state you want — installed packages, configuration files, running services — and reemote reads each target, reports the work it proposes, and applies the changes that are still needed. You supply the inventory and a program that runs it; reemote supplies the facts, operations and the engine that connects them.

Allow about **one hour** to read this guide with its examples. It starts from a small, usable script and builds that script up into the patterns a larger deployment needs. You need basic Python — functions, imports, loops, dictionaries. Generators and asyncio are introduced where you first meet them; you never have to implement the execution engine yourself.

Why express server configuration in Python at all? Because a deployment then shares everything else you already write in Python: your types, your validation, your test harness, your data sources. A configuration file in a dedicated language forces a second set of tools, a second way to test, and a boundary wherever your deployment needs data from your application. reemote removes that boundary. **Ansible** is a mature alternative when you want playbooks, its module ecosystem and its surrounding tooling; reemote is the choice when server configuration should be a library call inside a Python program. Python does not make package names portable across distributions or remove the need for SSH credentials — it makes the *glue* around those facts ordinary code.

!!! note "Reading order"

    Everything below is ordered so understanding builds up. Skim
    [Core vocabulary](#core-vocabulary) and [Your first deployment](#your-first-deployment)
    to get something running, then return to the model sections
    ([The execution model](#the-execution-model)) when you need the detail. If
    you only want to find the fact or operation for a task, jump straight to
    [Finding facts, operations and examples](#finding-facts-operations-and-examples).

## How the documentation is organised

The documentation is small and deliberately layered:

| Section | Purpose |
| --- | --- |
| **Home** ([index](index.md)) | A ten-minute introduction: install, one inventory, one script, preview and apply. |
| **Guide** (this page) | The one-hour progression from a first script to the execution model and extension points. |
| **API Reference → Modules** | One page per subject area — [files](modules/files.md), [apt](modules/apt.md), [dnf](modules/dnf.md), [systemd](modules/systemd.md), [server](modules/server.md) and more. Each page lists the facts and operations of that namespace with their signatures, arguments, return types and the exceptions they raise. |
| **API Reference → Actions** ([actions](actions/actions.md)) | The immediate-execution escape hatch: bespoke commands, staged scripts, connection waits. |
| **API Reference → Connectors** ([ssh](connectors/ssh.md), [docker](connectors/docker.md)) | How a host connects — SSH by default, or a local Docker/Podman container. |

The reference pages are generated from the library's own docstrings, so they always match the code you have installed. The narrative pages (Home, Guide) explain the model; the reference pages explain the individual facts and operations.

## Finding facts, operations and examples

A **fact** reads state; an **operation** requests a change. They live together in *namespaces* grouped by subject, and both appear on the matching **API Reference → Modules** page:

- To install or remove packages on a Debian/Ubuntu host, open [apt](modules/apt.md) and look for the `apt.packages` operation. On a Fedora/RHEL host, open [dnf](modules/dnf.md) for `dnf.packages`.
- To manage a file, a directory or a link, open [files](modules/files.md): `files.install`, `files.file`, `files.directory`, `files.link`, `files.put`, `files.get`, `files.edit`.
- To start, stop or enable a systemd unit, open [systemd](modules/systemd.md) for `systemd.service`.
- To read the hostname, users, mounts or timezone, open [server](modules/server.md).

On each page, the **names tell you which is which**:

- A **capitalised class** is a fact or a returned-data type — `FileContents`, `AptPackages`, `SystemdServiceState`, `Hostname`. You rarely name these directly; operations read them for you.
- A **lowercase function** is an operation — `install`, `packages`, `service`, `file`. These are what you `yield` in a deploy.
- A `SomeCommand` **class** is the command descriptor an operation produces. You almost never construct one yourself.

Each entry documents its arguments, what it returns, the exceptions it raises, and a pointer to a runnable script under `examples/`. When you want to see a fact or operation used end to end, follow that `examples/...` path — every example is a complete, runnable program that loads an inventory, builds requests and prints the plan and the run log.

!!! tip "Start from an operation, not a fact"

    Most tasks map to one operation. Search the module page for the verb you
    want (`packages`, `install`, `service`), read its arguments, and `yield` it.
    You only touch facts directly when you want to *branch on* what a host
    reports between two deploys — covered in [Facts](#facts).

## Core vocabulary

Four words carry almost the whole model. Learn them once and the rest of the guide is detail.

- **Inventory** — a list of dictionaries naming the servers and how to reach them. This is what you hand to the runner. A minimal entry needs a `host` label and a `connection` mapping; groups, tags and privilege settings are optional and covered later in [Hosts and the inventory](#hosts-and-the-inventory).
- **Fact** — a read of some server state: a hostname, the set of installed packages, a file's current text. Facts are gathered over the connection and are cached during a run. You usually do not call them yourself; operations read the facts they need.
- **Operation** — a request to converge the server toward a described state: "ensure these packages are installed", "make this file contain this text", "ensure this service is running". An operation reads facts, decides whether anything must change, and produces the commands to make the change. Operations are **idempotent** wherever that is meaningful: running one twice leaves the same state and reports *no change* the second time.
- **Action** — something that runs *immediately*, outside a deployment, and hands back its result. Actions are the escape hatch for one-off commands reemote has no operation for, and for connectivity itself. They come last in this guide because they are defined by contrast with the deploy model — see [Actions](#actions).

A **deploy** ties these together. It is a Python generator function that takes one host and *yields* operation requests in order:

```python
def deploy(host: Host) -> Generator[OperationRequest, None, None]:
    yield op(...)   # first step
    yield op(...)   # second step
```

The runner calls your deploy once per host, collects the requests, and drives them. You describe *what state you want*; the runner works out *what to do about it*.

## Your first deployment

This is the smallest useful program: one inventory, one deploy, one file written to a safe temporary path. It needs no administrator rights and touches nothing but the file it names.

Create `inventory.json`:

```json
[
  {
    "host": "demo",
    "connection": {
      "address": "192.0.2.10",
      "username": "deploy",
      "key_path": "~/.ssh/id_ed25519"
    }
  }
]
```

Replace the address, username and key path with your own test server's. (`192.0.2.10` is a documentation placeholder from the reserved TEST-NET range — it is not a real host.)

Create `hello.py` beside it:

```python
import asyncio
import json
from collections.abc import Generator
from pathlib import Path

from reemote import Host, OperationRequest, files, format_log, format_plan, op, run


def deploy(host: Host) -> Generator[OperationRequest, None, None]:
    yield op(
        files.install,
        name="Write a greeting",
        content="Hello from reemote!\n",
        dest="/tmp/reemote-hello.txt",
        mode="0644",
    )


async def main() -> None:
    path = Path(__file__).with_name("inventory.json")
    inventory = json.loads(path.read_text(encoding="utf-8"))

    # 1. Preview: connect, read state, report proposed work, change nothing.
    plans, report, _ = await run(inventory, deploy, dry=True)
    print("== Plan ==")
    print(format_plan(plans))
    assert report is None  # a dry run never produces a report

    # 2. Apply: run the same deploy for real.
    plans, report, _ = await run(inventory, deploy)
    print("== Applied ==")
    print(format_log(report))


asyncio.run(main())
```

Run it:

```bash
python hello.py
```

The first call passes `dry=True`. It still connects and reads the current state of the destination — a preview must know what is there to say what would change — but it sends no mutating command, so `report` comes back `None`. The second call omits `dry`, so it plans *and* applies. Against a host where the file is absent, you will see:

```text
== Plan ==
demo: Write a greeting         [changed: content /tmp/reemote-hello.txt is absent, will install]
== Applied ==
demo: Write a greeting         [changed: content /tmp/reemote-hello.txt is absent, will install]
    $ scp <files install> -> /tmp/reemote-install-2e52d4f8373a608a/content
    $ sh -c 'mkdir -p /tmp && cp /tmp/reemote-install-2e52d4f8373a608a/content /tmp/reemote-hello.txt && chmod 644 /tmp/reemote-hello.txt && rm -rf /tmp/reemote-install-2e52d4f8373a608a'
```

Run the script a second time and the state is already correct:

```text
== Plan ==
demo: Write a greeting         [no change: content already up to date (/tmp/reemote-hello.txt)]
== Applied ==
demo: Write a greeting         [no change: content already up to date (/tmp/reemote-hello.txt)]
```

That is **idempotence** in action: the same request, run again, correctly reports *no change* and sends nothing. The two `$` lines are the commands apply actually sent — an unprivileged `scp` of the text to a temporary spool path, then a `sh -c` chain that copies it into place, sets the mode and removes the spool. This example passes no `sudo=True`, so the chain runs as the login user, which is fine for a `/tmp` path that user can write. For a root-owned destination such as `/etc/...`, add `sudo=True` and the chain renders with a `sudo` prefix — the `scp` stays unprivileged either way, because a transfer is never elevated. [Files and transfers](#files-and-transfers) explains the spool-and-install pattern.

Notice what the script does *not* do. There is no `reemote` command, no inventory file format it owns, no plugin to register. `run` is an ordinary coroutine you call from ordinary code; `inventory` is a plain Python list you can build however you like. That is the whole shape of using reemote: **build an inventory, write a deploy, call `run`.**

## What makes the API work

Four design decisions are worth naming up front, because the rest of the guide is their consequences. You do not need to internalise the machinery to use the library — but knowing *why* a deploy is written as a pure generator, and why you await it, prevents the common mistakes.

### Pure, functional deploys

A deploy is a **pure function of its host**: given the same host, it yields the same sequence of operation requests. It reads no mutable state of its own and performs no side effects. The operations *inside* the requests are what read the server and decide what to change — never the deploy body.

This is what lets the runner execute your deploy **twice** and get consistent results: once to build a plan, once to apply it. Because the request sequence is a pure function, both passes produce the same steps. The runner enforces this with a [determinism guard](#determinism): if a deploy body branches on state that differs between the two passes, the runner raises rather than silently applying a different plan than it showed you.

```python
# Pure: the same two requests every time, on every host.
def deploy(host):
    yield op(dnf.packages, name="Install nginx", packages=("nginx",), sudo=True)
    yield op(files.file, name="Remove default page", path="/usr/share/nginx/html/index.html",
             present=False, sudo=True)
```

### Two phases: plan, then apply

Every run answers two questions in order — *what would this change?* and then *change it*. The first pass (**prepare**) builds the plan without mutating anything; the second (**apply**) executes the work. `dry=True` stops after the first, which is how you preview a deployment safely. Both passes read real facts over the connection, so a preview is honest about the current state. Detail in [The execution model](#the-execution-model).

### Asyncio end to end

The execution path is asynchronous: `run`, the prepare and apply drivers, every fact read and every command send are coroutines, so you call them with `await` and drive them from `asyncio.run(...)`. This is not ceremony — it is what lets reemote work on **many hosts concurrently**, overlapping the network waits instead of contacting one server at a time. Within apply, hosts advance through their steps in lockstep; the concurrency is across hosts, and the commands for a single host still run in order. Your deploy stays a plain synchronous generator; only the runner is async.

```python
plans, report, hosts = await run(inventory, deploy)   # a coroutine — you must await it
```

### No awareness of the controller filesystem

reemote does not scan your local machine for files to upload, and it has no notion of a "deploy directory" or a role tree on the controller. Content crosses to the host as **data you hand it**, not as a path the library resolves:

- `files.install` takes `content` as a plain string and writes it.
- `files.put` and `files.get` take an **IO-like object** (an open file, an `io.StringIO`, an `io.BytesIO`) — never a local path string.

Every remote path in an operation refers to a path **on the target host**, not on the controller. This keeps a deploy portable: the same code runs unchanged whether your controller is a laptop, a CI runner or a container, because the library never depends on a local file layout. If you want to ship a controller-side file, your code opens it and hands reemote the stream:

```python
# Your code opens the controller file once; reemote only ever sees the stream.
def make_deploy(stream):
    def deploy(host: Host) -> Generator[OperationRequest, None, None]:
        yield op(files.put, name="Upload nginx.conf", src=stream, dest="/tmp/nginx.conf")
    return deploy

with open("nginx.conf", "rb") as stream:
    await run(inventory, make_deploy(stream))
```

An IO argument is compared by *identity*, so the stream is opened once and captured by the deploy — both phases then see the same object, and a fresh handle per phase would trip the determinism guard. Plain text needs no such care: `files.install` takes a `content` string, which compares by value. [Files and transfers](#files-and-transfers) covers both.

## Configure a small web server

Here is a realistic deploy that combines packages, a file and a service, with the privileges each needs. It targets Debian/Ubuntu hosts and is guarded by a `when` predicate so it is skipped on hosts that are not in the `debian` group.

```python
import asyncio
import json
from collections.abc import Generator
from pathlib import Path

from reemote import Host, OperationRequest, apt, files, format_log, format_plan, op, run, systemd


def is_debian(host: Host) -> bool:
    # A `when` guard reads only static inventory data (here host.group), never
    # a mutable fact, so the prepare and apply passes reach the same decision.
    return host.group == "debian"


PAGE = """<!doctype html>
<html><head><title>Example</title></head>
<body><h1>It works</h1></body></html>
"""


def deploy(host: Host) -> Generator[OperationRequest, None, None]:
    yield op(
        apt.packages,
        name="Install nginx",
        packages=("nginx",),
        sudo=True,
        when=is_debian,
    )
    yield op(
        files.install,
        name="Write the landing page",
        content=PAGE,
        dest="/var/www/html/index.html",
        mode="0644",
        sudo=True,
        when=is_debian,
    )
    yield op(
        systemd.service,
        name="Run nginx",
        service="nginx",
        running=True,
        enabled=True,
        sudo=True,
        when=is_debian,
    )


async def main() -> None:
    inventory = json.loads(
        Path(__file__).with_name("inventory.json").read_text(encoding="utf-8")
    )
    plans, report, _ = await run(inventory, deploy, dry=True)
    print(format_plan(plans))
    plans, report, _ = await run(inventory, deploy)
    print(format_log(report))


asyncio.run(main())
```

The corresponding inventory entry adds the `group` the guard reads and the privilege data the `sudo=True` requests need:

```json
[
  {
    "host": "web1",
    "connection": {"address": "192.0.2.20", "username": "deploy", "key_path": "~/.ssh/id_ed25519"},
    "group": "debian",
    "privilege": {"sudo_user": "root"}
  }
]
```

!!! warning "This example changes a real server"

    Applying it installs a package, writes a root-owned file under `/var/www`
    and starts a service. Point it only at a host you are allowed to modify,
    and preview with `dry=True` first. A passwordless-sudo login needs no
    `sudo_password`; if the account prompts for one, add `"sudo_password": "..."`
    to the `privilege` mapping (it rides an askpass helper and never appears in
    the log — see [Hosts and the inventory](#hosts-and-the-inventory)).

Three steps run in order on every `debian` host: install nginx (only if a package is missing), write the landing page (only if its content differs), and ensure the service is running and enabled (only if it is not already). On a host where everything is already in place, all three report *no change* and nothing is sent. On a host outside the `debian` group, all three are *skipped* — the guard keeps its slot in the sequence but reads no facts and sends no commands. That skip/run distinction, and why the guard must read only static data, is the subject of [Determinism](#determinism).

The rest of the guide takes each piece of this example apart: what an inventory can hold, how a deploy is written and composed, how the two phases stay in agreement, and how facts, operations and actions actually work.


## The execution model

This section is the detail behind the four design decisions. Read it once and the rules for writing deploys stop feeling arbitrary.

### The two phases

A run moves through two phases in lockstep.

**Prepare** builds the plan without changing anything. For each host it calls your deploy generator and collects the operation requests. It *does* connect and read facts — a plan has to know the current state to say what would change — but it never sends a mutating command.

**Apply** executes the work. It calls your deploy generator *again*, from the top, and this time materialises each operation into commands and sends them.

The two phases advance together, step by step. At each position *n*, every host materialises its *n*th request, runs that step's commands, and only when all hosts have finished step *n* does the runner move to step *n+1*. Commands for one host within a step run in order; the concurrency is across hosts. `asyncio.gather` is the barrier between steps.

```text
step 0:  host-a op0 ─┐
         host-b op0 ─┤── barrier (all hosts finish step 0)
         host-c op0 ─┘
step 1:  host-a op1 ─┐
         host-b op1 ─┤── barrier
         host-c op1 ─┘
```

`dry=True` stops after prepare: it returns the plan and a `None` report, having read facts but sent no mutation.

!!! warning "A plan is a snapshot of the current state, not a simulation"

    Prepare reads the state as it is *now*. It does not model the effect of
    earlier operations on later ones. If operation 1 creates a file that
    operation 2 would read, the plan shows operation 2 against the *pre-run*
    state; only apply sees the file operation 1 just made. Plans are accurate
    for "what is currently out of sync", not for "what the world will look like
    mid-run".

There is no transaction and no rollback. Apply runs each step to completion; if a host errors, `run_apply` uses `gather(return_exceptions=True)`, so the other hosts in the current step still finish before the error propagates and the run stops.

### Hosts and the inventory

The inventory is a list of dictionaries. Each entry must carry a `host` (its logical label) and a `connection` (how to reach it); `group`, `tags` and `privilege` are optional, and any extra keys are preserved on the materialised `Host`. reemote validates the whole inventory with pydantic before it builds a single host, so a malformed entry raises `InventoryError` immediately rather than failing later over the network.

```json
[
  {
    "host": "web1",
    "connection": {"address": "192.0.2.20", "username": "deploy", "key_path": "~/.ssh/id_ed25519"},
    "group": "webservers",
    "tags": ["ubuntu", "edge"],
    "privilege": {"sudo_user": "root", "sudo_password": "s3cr3t"}
  }
]
```

- **`host`** is the label used in plans and logs. It is *not* the address — a host may be reached through a jump host or a container identifier. Labels should be unique; the plan and report are keyed by them.
- **`connection`** holds the SSH options. `address` (or `host`/`hostname`) is required for SSH; `username`/`user`, `key_path`, `port`, `known_hosts`, `proxy_command`, `tunnel` and the rest are forwarded to asyncssh. `key_path` is a shorthand for `client_keys`, and path-valued options have `~` expanded. A `"connector": "docker"` key switches the transport to a local Docker/Podman container.
- **`group`** is a single string; **`tags`** is a list. Both are *static* classifiers a deploy reads off the host to branch, and both are the only data a `when` guard may consult.
- **`privilege`** holds elevation data: `sudo_user`/`sudo_password`, `su_user`/`su_password`, `doas_user`. These come from the inventory, never from a deploy — an operation asks to elevate with `sudo=True`, but it cannot choose the run-as user or supply a password.

A `sudo_password` is delivered through an askpass helper and never appears in the log or the plan. There is deliberately no `doas_password`: `doas` has no askpass mechanism, so reemote always runs `doas -n` and the host must grant a `permit nopass` rule.

`Host.from_inventory` is the default factory that turns each entry into a `Host`. The connection is opened lazily and cached; `run` closes every connection when it finishes. If a host cannot be reached, `Host.connect` wraps the transport failure in a `HostConnectionError` naming that host, and because prepare connects the whole inventory concurrently, the per-host failures are merged into one aggregate naming every unreachable host.

### Deploy scripts

A deploy is a generator function that takes a `Host` and yields `OperationRequest` objects. You build a request with `op`:

```python
def op(
    func,                       # the operation function, e.g. files.install
    *,
    name: str = "",             # a label for plans and logs
    sudo: Privilege | bool = Privilege.UNSET,
    su:   Privilege | bool = Privilege.UNSET,
    doas: Privilege | bool = Privilege.UNSET,
    when: WhenPredicate | tuple[WhenPredicate, ...] | None = None,
    **kwargs,                   # the operation's own parameters
) -> OperationRequest
```

`name`, the three privilege flags and `when` are **metadata** the runner consumes; everything else in `**kwargs` is forwarded to the operation. `name` is never passed to the operation — it is purely a label.

```python
def deploy(host: Host) -> Generator[OperationRequest, None, None]:
    yield op(files.install, name="Write config", content=CONF, dest="/etc/app.conf", sudo=True)
    yield op(systemd.service, name="Restart app", service="app", restarted=True, sudo=True)
```

A few rules keep requests well-behaved:

- **Every request argument must be hashable.** `op` checks each value with `hash()` and raises `UnhashableArgumentError`, naming the argument, if one is not. Use tuples, not lists, for collections of packages, names or paths: `packages=("nginx", "curl")`.
- **Do not define an operation inline as a lambda or closure** if you can avoid it. Requests are compared by value across the two phases; a fresh lambda in each pass is a *different* callable and the determinism guard will reject it. Define the operation function at module level.
- **Yield the requests you build.** A request built with `op` but never yielded is a programming error; the runner detects it and raises `DeployRequestError`.

The privilege flags are **tri-state**: `True`/`ON` means elevate, `False`/`OFF` means explicitly *unprivileged*, and `Privilege.UNSET` (the default) means "no preference". Sub-deploys use this to fill in defaults without overriding an operation's explicit choice — see [Composing deploys](#composing-deploys).

A `when` guard is a predicate `host -> bool` (or a tuple of them, combined with AND). When a guard is false, the request keeps its slot in the sequence but is *skipped*: no facts are read, no commands are sent. Guards may only read static inventory data (`host.name`, `host.group`, `host.tags`, `host.extra`) — reading a fact inside a guard would break determinism.

### Composing deploys

Deploys compose three ways, all operating on request sequences.

**`yield from`** delegates to another deploy generator:

```python
def web(host):
    yield op(apt.packages, name="Install nginx", packages=("nginx",), sudo=True)

def deploy(host):
    yield from base(host)
    yield from web(host)
```

**`compose(*subs, dedupe=False)`** concatenates role deploys in order:

```python
deploy = compose(base, web, monitoring)
```

**`sub_deploy()`** wraps a deploy to add a name prefix, a default privilege policy and an eligibility guard:

```python
from reemote import sub_deploy

edge_web = sub_deploy(
    web,
    name_prefix="edge",
    sudo=True,  # fills UNSET privilege on inner ops; explicit values win
    when=lambda host: host.group == "edge",
)
```

A sub-deploy's privilege policy fills any operation left `UNSET`, but honours an operation that already set `sudo`/`su`/`doas` explicitly. Guards combine by conjunction: the sub-deploy's guard AND each inner request's guard.

With `dedupe=True`, `compose` drops requests that repeat an earlier one. Dedupe compares the *resolved* elevation decision, the function, args, kwargs and guards — but **not** the display name. Full request equality (used by the determinism guard) additionally includes the name and the tri-state privilege values.

### Running a deploy

`run` is the single entry point:

```python
async def run(
    inventory: list[dict],
    deploy_func: Callable,
    *,
    host_factory: Callable[[dict], Host] = Host.from_inventory,
    dry: bool = False,
) -> tuple[dict[str, list[PlannedOperation]], ApplyReport | None, list[Host]]
```

It validates the inventory, materialises the hosts, runs prepare, and — unless `dry` — runs apply, then closes every connection in a `finally`. It returns a three-tuple:

- **`plans`** — `{host_label: [PlannedOperation, ...]}`, the proposed work. `format_plan(plans)` renders it.
- **`report`** — an `ApplyReport` with a `LogEntry` per applied operation, or `None` when `dry=True`. `format_log(report)` renders it. **`format_log` requires a report, not `None`** — guard the call.
- **`hosts`** — the materialised `Host` objects. These are the targets, *not* snapshots of remote state; do not expect them to carry the facts you read.

```python
plans, report, hosts = await run(inventory, deploy)
print(format_plan(plans))
if report is not None:
    print(format_log(report))
```

A `PlannedOperation` records `request`, `changed`, `reason`, `commands` and `skipped`. A `LogEntry` records the same plus the privilege fields and the command renderings actually sent. `ApplyReport.changed` is true if any entry changed.

For lower-level control there are `run_prepare` and `run_apply`, and `gather_fact` for reading facts across hosts. These do **not** own connection cleanup the way `run` does — if you call them directly, close your hosts in a `finally` yourself.

### Determinism

The runner executes your deploy twice — once to plan, once to apply — and requires both passes to yield the *same* request sequence. This is the **determinism** property, and it is why a deploy must be a pure function of its host.

The guard compares each pulled request against the one prepare recorded, by value, and also checks for extra requests. If a deploy body branches on something that differs between the passes — a fact, a random number, the current time, a mutable global, a freshly-created lambda — the two sequences disagree and the runner raises `DeterminismError` rather than applying a different plan than it showed you.

The practical rules that follow:

- Read state through **operations and facts**, not through the deploy body. The deploy describes *intent*; the operation reads *current state* and decides the change.
- Keep every request argument **stable and hashable** across passes: module-level constants, tuples, and the *same* IO object identity for a stream.
- A `when` guard may consult only **static inventory data**, never a fact.

!!! note "What the guard does and does not catch"

    The guard detects a request that differs by value and detects extra
    requests appended after the prepared sequence. It compares what it pulls;
    it is not a general proof of determinism. The reliable way to stay
    deterministic is to keep the deploy body pure, not to rely on the guard to
    catch every possible divergence.

Determinism does **not** mean the two phases produce identical *commands* or identical *changed* flags. Prepare reads the state before any change; apply re-reads it as it goes, and state genuinely changes between steps. What must stay identical is the **request sequence** — the steps themselves. The decisions and generated commands may legitimately differ because the world moved.

### Change notification (handlers)

reemote has **no first-class handler mechanism** — there is no `notify`/`handler` keyword and no event bus. A conditional restart — "restart the service only if the config changed" — is built from the *results of a previous run*, in ordinary Python, outside the deploy body. This is the **two-deploy outcome split**: run the notifying tasks, read their `ApplyReport` in the gap *between* two complete prepare/apply cycles, freeze the hosts that reported a change, and pass that frozen set into a second deploy as a stable input.

Because this is the single most common Ansible construct, `reemote.handlers` ships two helpers so a role migration stops copy-pasting a bespoke `notifying_hosts`/`build_restart_*` pair:

- [`changed_hosts(report, names)`][reemote.handlers.changed_hosts] — the *notification read*: one pass over a report's log, freezing the hosts whose **named** entries reported `changed` into a `frozenset[str]`.
- [`restart_handler(hosts, services)`][reemote.handlers.restart_handler] — the *handler factory*: closes over that frozen set and returns a deploy that yields `systemd.service(restarted=True)` for those hosts only.

```python
from reemote import changed_hosts, restart_handler, run

async def main() -> None:
    # Pass 1: converge config files (the notifying tasks).
    _, report, _ = await run(inventory, config_deploy)

    # The notification read, taken between the two runs.
    changed = changed_hosts(report, {"Write app config"})

    # Pass 2: restart only on the hosts whose config actually changed.
    restart = restart_handler(changed, "app", sudo=True)
    await run(inventory, restart)
```

`changed` is a fixed value by the time `restart_handler` builds the deploy, so the returned generator branches on a snapshot — never a live fact or report — and stays pure and deterministic across both of its phases. An empty `changed` yields an empty deploy: no host restarts, the converged no-op Ansible's handler gives for free. The restart happens in a *second* full prepare/apply cycle, and a `dry=True` preview of pass 1 cannot know whether the handler will fire (Ansible check mode skips handlers for the same reason); a `dry=True` preview of pass 2 *is* honest, because it consults the frozen outcome of pass 1.

`systemd.service(restarted=True)` restarts a unit that is already running; the operation's default `running=True` starts a stopped one. `restart_handler` also takes a `name` template, a `sudo` flag (default `True`) and a `when` guard, all forwarded to each request. For a handler that is not a systemd restart, build the same shape yourself — a factory closing over `changed_hosts(...)` — and see the [handlers reference](handlers.md) plus the runnable [handler example](examples/demonstrations/composition.md#restarting-a-service-only-when-a-notifying-operation-changed).

## Facts

A **fact** reads state from a host. It is any class with an async `process` classmethod — there is no base class to subclass:

```python
class Hostname:                       # the real server.Hostname, simplified
    COMMAND = "uname -n"

    @classmethod
    async def process(cls, host: Host) -> str:
        stdout = await host.run_shell(cls.COMMAND)   # run_shell returns text
        for line in stdout.splitlines():
            if line.strip():
                return line.strip()
        return ""
```

You rarely call a fact directly. Operations read the facts they need through a `FactCache`, which awaits each fact once per host and caches the result, so a fact used by several operations in one step runs once:

```python
class FactCache:
    async def get(self, fact_cls, *args, **kwargs):
        key = (fact_cls, args, tuple(sorted(kwargs.items())))
        if key not in self._data:
            self._data[key] = await fact_cls.process(self.host, *args, **kwargs)
        return self._data[key]
```

Apply **recreates the cache before each request**, which is what lets a later operation see the effect of an earlier one: install a package in step 1, and step 2's `AptPackages` fact reads the newly-installed set rather than a stale cache.

When you *do* want a fact's value directly — to branch between two deploys, or to report it — read it across hosts with `gather_fact`:

```python
from reemote import gather_fact, server

hostnames = await gather_fact(hosts, server.Hostname)
# {"web1": "web1.example.com", "web2": "web2.example.com"}
```

`gather_fact` returns a `{host_label: value}` mapping and does **not** close the hosts. It aggregates `CommandNotFoundError` across hosts; other exceptions propagate. Filter the host list yourself to select a subset — `gather_fact` passes its extra keyword arguments to the fact, not to a group/tag selector.

!!! warning "Not every fact failure raises"

    `run_shell` defaults to `check=False`, so a non-zero exit does not
    automatically raise. Exit status 127 (command not found) always raises
    `CommandNotFoundError` unless the remote shell masks it. Built-in facts
    sometimes deliberately mask errors or interpret empty output — for example
    `FileContents` distinguishes "absent" from "empty" with a marker rather than
    raising. Do not assume every fact failure surfaces as an exception.

## Operations

An **operation** is an async generator that takes an `OpContext` and yields command descriptors. It reads facts to decide whether anything must change, and yields commands only when it does — that is what makes it idempotent.

```python
async def install(ctx: OpContext, content: str, dest: str, mode: str = "0644", ...) -> AsyncGenerator[Command]:
    current = await ctx.facts.get(FileContents, dest)
    if current == content:
        return                      # nothing to do — reports "no change"
    yield InstallSpoolCommand(...)  # upload to a /tmp spool
    yield InstallCommand(...)       # elevated: cp spool dest && chmod && rm spool
```

You never call an operation yourself. You reference it in `op`, which records the call; the runner materialises it during prepare (to see what it *would* yield) and again during apply (to actually send). An operation's parameters are the `**kwargs` you pass to `op`.

The `OpContext` an operation receives carries the host, its `FactCache`, and the resolved privilege decision for this request. Operations are **phase-unaware**: the same code runs in prepare and apply; the context is what differs.

Operations return a *declared result*. A `changed` flag means the operation did the work it set out to do; for the built-in operations that converge a described state, that is a genuine convergence. Avoid assuming an arbitrary custom operation's `changed` flag proves every postcondition you care about — it proves what the operation checked.

### Files and transfers

The files namespace shows the design boundaries most clearly.

- **`files.install(content, dest, mode=..., ...)`** writes *text* to a remote path. `content` is a plain string; `dest` is a path on the target. It compares content only — if the text already matches, it yields nothing. Metadata drift (mode, owner) is **not** reconciled by `install`; use `files.file` for that. A root-owned destination needs `sudo=True`.
- **`files.file(path, present=True, mode=..., user=..., group=..., touch=False, ...)`** manages a file's existence and metadata. `present=False` removes it. `touch=True` deliberately runs every time. `force=True` can recursively remove a conflicting type — use it knowingly.
- **`files.put(src, dest)`** and **`files.get(src, dest)`** move bytes between an **IO-like object** and a remote path. `put` takes an IO source and a remote destination; `get` takes a remote source and an IO destination. Neither takes a local path string. The transfer is **unprivileged** — `sudo` does not elevate an SCP. Both compare a SHA-1 digest to skip a needless transfer, and both rewind the stream.
- **`files.edit(path, transform)`** applies a pure function `str | None -> str` to a remote file's current text and uploads the result only if it differs. The transform must be idempotent and stable — it runs in both phases.

**Protected text files** (root-owned `/etc/...` paths) use a *spool-and-install* pattern rather than an in-place edit: `files.install` uploads the text to a deterministic `/tmp` spool path derived from the destination, then runs an elevated `sh -c` chain that copies the spool into place, sets ownership and mode, and removes the spool. The upload itself is unprivileged; only the install chain is elevated. The `/tmp` spool path is predictable and is **not** a security boundary — it is a staging location, not a secret.

Because the transfer is unprivileged, an existing file whose content the login user cannot read may appear empty to a generic read-back, which can undermine idempotence for restrictive permissions. Prefer `files.install` for a single-cycle protected text write; it does not have the "create then move" two-run problem that `files.move` has (a `move` validates its source during materialisation, so a source created in the same run is not yet present at plan time).

## Command descriptors

A command descriptor is the unit an operation yields. It is a small object describing *what to send* — a shell string, a transfer, a wait. You almost never construct one directly; operations build them. The common kinds:

- **`StringCommand`** — a shell command line, rendered and sent through `host.run_shell`.
- **`PutCommand`** / **`GetCommand`** — an SCP transfer between an IO object and a remote path.
- **`InstallCommand`** — the elevated install chain for protected text files.

Descriptors are what the plan shows and what apply sends. `format_log` renders the descriptors that were actually sent, which is why the applied log shows the concrete `scp` and `sh -c` lines rather than the abstract operation.

## Design invariants

A handful of invariants hold across the whole library. They are the reason the model is predictable:

1. **Deploys are pure.** A deploy is a pure function of its host yielding the same requests in both phases. The determinism guard enforces the request sequence.
2. **Facts and operations are phase-unaware.** The same fact and operation code runs in prepare and apply; only the `OpContext` differs. Nothing in a fact or operation asks "which phase am I in?".
3. **Phase awareness lives in the driver.** Only the drivers know about prepare vs apply. They set a context flag that makes the in-deploy guard work; facts, operations and actions stay oblivious.
4. **Actions cannot run inside a deploy.** `gather_action` raises `ActionInDeployError` the moment it is *called* during a deploy — the check is synchronous, so even an un-awaited call in a sync generator fails immediately.
5. **Privilege data comes from the inventory.** An operation requests elevation with a bool; it never chooses a run-as user or supplies a password.
6. **No controller-filesystem awareness.** Content crosses as data (strings, IO streams), never as a local path the library resolves. Every remote path refers to the target.
7. **Requests are compared by value.** Every argument is hashable; streams must keep object identity across phases.

These invariants are what let the runner execute a deploy twice, concurrently across hosts, and still show you a plan it will honour.

## Actions

An **action** runs *immediately*, outside the deploy model, and returns what happened. Actions exist for the cases the library deliberately does not model: software reemote knows nothing about, a script already staged on the host, and connectivity itself.

You call an action with `await gather_action(...)`:

```python
from reemote import Shell, gather_action

results = await gather_action(hosts, Shell, command="df -h /")
# {"web1": Result(exit_status=0, stdout="...", stderr=""), ...}
```

`gather_action` is a *synchronous* function that returns a coroutine, so the in-deploy guard fires at the call site even before you await it. It selects hosts by `group`/`tags` (combined with AND), runs concurrently, and closes the selected hosts when it finishes.

Three actions ship with the library:

- **`Shell(command=...)`** runs a bespoke command and returns a `Result` (`exit_status`, `stdout`, `stderr`). The command's own exit status — even 127 — travels back as *data*; the caller decides what a failure means. Library-level failures still raise: `HostConnectionError` for an unreachable host, `PrivilegeError` for an elevation rejection.
- **`Script(path=...)`** runs a script file *already present on the remote host*, with an explicit `interpreter=` or the file's shebang. It does not upload the script; stage it first (with `files.put` or `files.install`) if it is not there.
- **`WaitForConnection(timeout=..., retry=...)`** discards any cached connection and polls `connect` until the host answers, returning `None` per successful host. It is the barrier between one deploy and the next — typically after a reboot. If a host never comes back it raises `ConnectionTimeoutError`, aggregated across hosts.

!!! warning "A connection is not readiness"

    `WaitForConnection` proves the SSH port answers. It does not prove your
    application is up, that a reboot actually completed, or that a service is
    healthy. For readiness, poll the thing you care about — a `Shell` action
    checking a health endpoint, or a fact — in a loop of your own.

Actions are the pragmatic edge of the model. Everything inside a deploy is pure, planned and idempotent; an action is none of those things, which is exactly why it lives outside the deploy and why the runner refuses to let one slip into a generator.

## Where to go next

- Browse the **API Reference → Modules** pages for the operations your tasks need; each lists arguments, return types, exceptions and a runnable example.
- Read the connector pages ([ssh](connectors/ssh.md), [docker](connectors/docker.md)) for the connection options your inventory can carry.
- Study the runnable programs under `examples/` — every one loads an inventory, builds requests and prints the plan and log, and they cover composition, privilege and each module's operations.
