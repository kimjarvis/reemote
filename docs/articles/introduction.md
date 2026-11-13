# Reemote: configuration management as a Python library

*Ansible is a CLI. Pyinfra is a CLI. Salt is a CLI. Chef and Puppet have their own DSLs. There is no serious configuration-management library you can `import` from Python — until now.*

## The problem

Python is the language of automation glue: CI, dashboards, data pipelines, deploy orchestration. But the moment you want to describe the state of a server, you leave Python behind. You write YAML for Ansible. You write Ruby for Chef. You write Puppet DSL for Puppet. Then you invoke a binary that parses, plans, and executes for you.

There is no serious configuration-management **library** for Python. Nothing you can `import`. Nothing that returns typed results you can inspect. Nothing you can embed inside an application, unit-test with `pytest`, or `await` from an async handler. The mainstream tools are all command-line binaries wrapped around their own task language, and if you want to trigger a deploy from Python, you end up shelling out to that binary and scraping its terminal output.

That is the gap.

## Try it in under a minute (docker connector)

Reemote ships a **docker connector** that runs your deploy inside a local container, so you do not need a remote host or an SSH key to see it work. **You do need Docker or Podman installed locally** — the connector shells out to whichever CLI you name in the inventory, and there is no default. Three commands:

```bash
# 1. Install (Python 3.13+; pip works just as well as uv)
uv venv .venv && source .venv/bin/activate && uv pip install reemote

# 2. Save the script below as hello.py, then run it
python hello.py
```

```python
# hello.py
import asyncio
from reemote import files, format_log, format_plan, op, run

INVENTORY = [{
    "host": "demo",
    "connection": {
        "connector": "docker",
        "identifier": "alpine:3.20",
        "docker_cmd": "podman",   # or "docker"
    },
}]

def deploy(host):
    yield op(files.install,
             name="Write a greeting",
             content="Hello from reemote!\n",
             dest="/tmp/reemote-hello.txt",
             mode="0644")

async def main():
    plans, report, _ = await run(INVENTORY, deploy)
    print("== Plan ==")
    print(format_plan(plans))
    print("== Applied ==")
    if report is not None:
        print(format_log(report))

asyncio.run(main())
```

Reemote starts a throwaway `alpine:3.20` container, writes the file inside it, prints the plan and the apply log, then commits the container into a new image and removes it. Verified output:

```text
== Plan ==
demo: Write a greeting         [changed: content /tmp/reemote-hello.txt is absent, will install]
== Applied ==
demo: Write a greeting         [changed: content /tmp/reemote-hello.txt is absent, will install]
    $ scp <files install> -> /tmp/reemote-install-2e52d4f8373a608a/content
    $ sh -c 'mkdir -p /tmp && cp /tmp/reemote-install-2e52d4f8373a608a/content /tmp/reemote-hello.txt && chmod 644 /tmp/reemote-hello.txt && rm -rf /tmp/reemote-install-2e52d4f8373a608a'
```

Two things worth noticing:

- The `$ scp` and `$ sh -c` lines are **command descriptors rendered canonically** — they look the same whether the transport is SSH, docker, or podman. Under the hood reemote is running `podman cp` and `podman exec -i demo sh -c '...'`; the transport wrapping is invisible in the log by design, so the same deploy reads the same on any target.
- Every run reports `[changed: ... is absent, will install]`, even the second time. That is not a bug — the image workflow starts a *fresh throwaway container* from `alpine:3.20` on every invocation, so the file genuinely is absent each time. Point reemote at a stable target (an SSH host, or an existing container ID) and the second run correctly reports `[no change: content already up to date]`. Idempotence is a property of the operations against a persistent state, and the quickstart deliberately trades persistence for a zero-setup demo.

That is the whole loop. Describe the state you want, hand over an inventory, `await` the result. No CLI, no playbook, no YAML.

## Why Python has real advantages over Ansible

Ansible chose YAML because it is readable and it does not require knowing a programming language. That is a fair trade-off when your team does not write Python. When your team does — and most automation teams today do — the trade-off costs you more than it saves:

- **Your editor does not know your YAML.** No go-to-definition, no rename, no reliable type check. A typo in a module name surfaces at runtime, in production.
- **Jinja2 is a second language inside the first.** `{{ hostvars[...] }}` is a stringly-typed escape hatch that fights your IDE and your test suite.
- **Roles load by convention, not by import.** Composition becomes a directory-layout puzzle rather than a `from ... import ...`.
- **The result is terminal output.** If your application needs to know what changed, you parse stdout with a regex and hope.
- **Testing means testinfra, molecule, ansible-lint.** Not `pytest`. You cannot `import` a task and inspect it.

Python already solves all of these. `import` gives you composition. Types give you editor support. `pytest` gives you tests. `await` gives you concurrency. Data structures give you results you can consume programmatically. The reason configuration management did not use Python is that nobody shipped the library.

## The solution: Reemote is a library

Reemote is a Python package (`pip install reemote`) with two properties that separate it from every CLI-shaped tool:

### Async end to end, on asyncssh

The transport is [asyncssh](https://asyncssh.readthedocs.io/), and the entire execution path — connecting, gathering facts, sending commands, closing — is coroutines. You `await` a deploy and hosts progress in parallel. Within a deploy, hosts step through operations in lockstep; commands for one host run in order; network waits overlap across hosts. Your deploy script stays a plain synchronous generator, so you never write `async` inside an operation.

```python
plans, report, hosts = await run(inventory, deploy)
```

`run` is the single entry point. It validates the inventory, opens connections lazily, plans and applies in two phases, then closes everything in a `finally`. Same code over SSH or over the docker connector — pick the transport in the inventory entry, not in the deploy.

### A library returning structured results, not a CLI

`run(...)` returns `(plans, report, hosts)`:

- `plans` — `{host_label: [PlannedOperation, ...]}`, the proposed work per host.
- `report` — an `ApplyReport` with a `LogEntry` per applied operation, each carrying `changed`, `reason`, and the exact commands that were sent. `None` when `dry=True`.
- `hosts` — the materialised host objects.

No parsing stdout to figure out what happened. You already have the objects. `format_plan` and `format_log` are convenience renderers for humans; your application consumes the data directly.

### Four words to know

- **Inventory** — a list of dicts naming your targets and how to reach them.
- **Fact** — a read of remote state (hostname, installed packages, file contents).
- **Operation** — a request to converge the server toward a described state. Operations read facts, decide whether anything must change, and yield commands only when it must.
- **Action** — an immediate, non-deploy escape hatch (`Shell`, `Script`, `WaitForConnection`) for one-off commands.

A deploy is a generator that yields operation requests in order:

```python
def deploy(host):
    yield op(apt.packages, name="Install nginx",
             packages=("nginx",), sudo=True)
    yield op(files.install, name="Write landing page",
             content=PAGE, dest="/var/www/html/index.html",
             mode="0644", sudo=True)
    yield op(systemd.service, name="Run nginx",
             service="nginx", running=True, enabled=True, sudo=True)
```

Every request argument is hashable, every deploy is a pure function of its host, and the runner executes it twice — once to plan, once to apply — with a determinism guard that raises if the two passes disagree. `dry=True` stops after the plan and gives you a real preview against live facts.

## What follows: Reemote can replace Ansible

Once configuration management is a library, everything Python already has becomes available to your deploys:

- **Deploy from your application.** `await run(inventory, deploy)` inside a FastAPI handler, a Celery task, a GitHub Action written in Python, or a REPL.
- **Compose with real code.** Build the inventory from a database query. Generate config text from a Pydantic model. Share validation logic between the app and the deploy.
- **Test with `pytest`.** Deploy functions are pure generators — inspect the requests they yield without touching a server.
- **Consume structured results.** `report.entries[i].changed`, `.reason`, `.commands` are data you can log, chart, feed to a webhook, or gate a rollout on.
- **Two-phase execution for free.** Every `run` prepares then applies. `dry=True` is a real preview against live state, not a simulation.
- **Same code over SSH or Docker.** Flip the `connector` key and the identical deploy runs against a remote server or a local container — useful for testing a role before you point it at production, or for building an image from the same code that configures a host.
- **Handlers as ordinary Python.** No `notify`/`handler` keyword. Read the first deploy's report, freeze the hosts that changed, feed that set into a second deploy. `reemote.handlers.changed_hosts` and `restart_handler` ship as helpers for the common case.

You can use Reemote instead of Ansible whenever deployment is part of a Python program rather than a shell command in a CI job — which, for most teams building products today, is the more natural shape.

## What Reemote is not

It is worth naming the boundaries:

- **Not a deployment tool.** There is no `reemote` command. You write the script.
- **Not an inventory format.** Inventory is a Python list of dicts. Load it from JSON, YAML, a database, or write it inline.
- **Not a role ecosystem.** Reemote ships modules for `apt`, `dnf`, `apk`, `files`, `systemd`, `server`, `git`, `mysql`, `postgresql`, `iptables`, `firewalld`, `ufw`, `selinux`, `lxd`, `snap`, `flatpak`, `sysctl`, `gpg`, `openrc`, and `hardware` — but there is no Ansible Galaxy equivalent.
- **Not a translation layer between OSes.** Python does not make `nginx` installable by the same name on Alpine and Debian. You still pick the right module for the target.

What it is, is the missing library.

## Try it

- Documentation and full guide: <https://reemote.org/>
- Source: <https://github.com/kimjarvis/reemote>
- PyPI: `pip install reemote`
- License: EPL-2.0

If you have ever wanted to describe server state in the same language as the rest of your stack — with imports, types, tests, and `await` — the docker quickstart at the top of this article is the whole onboarding. Give it a minute.


