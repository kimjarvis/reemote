# Configure servers with Python

**Reemote is a Python library for configuring servers, specifically built to support the migration of Ansible-based workflows into pure Python.**

Every deploy runs twice, first as a dry run and then as an apply, and the dry run is as clear as the apply step. Same code, same inventory, same operations — the only difference is a `dry=True` flag. The dry run gathers real facts from real hosts and reports exactly what the apply is about to do, so nothing is executed until you have read the plan.

## Try it in under a minute (docker connector)

Reemote ships a **docker connector** that runs your deploy inside a local container, so you need neither a remote host nor an SSH key to see the two phases work. **Docker or Podman must be installed locally** — the connector shells out to whichever CLI you name in the inventory, and there is no default. Two steps:

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
    "family": "Alpine",
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
    # Dry run: facts are gathered for real, nothing is mutated.
    plans, _, _ = await run(INVENTORY, deploy, dry=True)
    print("== Dry run: what would change ==")
    print(format_plan(plans))

    # Apply: the same deploy, executed against a fresh container.
    _, report, _ = await run(INVENTORY, deploy)
    print("\n== Applied: what changed ==")
    assert report is not None
    print(format_log(report))


asyncio.run(main())
```

Reemote starts a throwaway `alpine:3.20` container, previews the change without touching it, then runs the same deploy for real against a second container, prints the resulting command log, and finally commits each container into a new image and removes it. Verified output:

```text
== Dry run: what would change ==
demo: Write a greeting         [changed: content /tmp/reemote-hello.txt is absent, will install]

== Applied: what changed ==
demo: Write a greeting         [changed: content /tmp/reemote-hello.txt is absent, will install]
    $ scp <files install> -> /tmp/reemote-install-2e52d4f8373a608a/content
    $ sh -c 'mkdir -p /tmp && cp /tmp/reemote-install-2e52d4f8373a608a/content /tmp/reemote-hello.txt && chmod 644 /tmp/reemote-hello.txt && rm -rf /tmp/reemote-install-2e52d4f8373a608a'
```

The dry-run line and the first line of the apply log are the same line — that is the point. The plan the dry-run showed you is the plan the apply executed, verbatim, with the shell commands appended. Each `run(...)` starts its own throwaway container from `alpine:3.20`, so both phases observe the same absent file; the demo is about the two-phase pattern, not idempotence.

The inventory is a plain Python list of host dictionaries carrying connection information. Reemote is agentless: for SSH targets the connection block is passed to [asyncssh](https://asyncssh.readthedocs.io/).

A deploy is a generator that yields operation requests in order — here, a single `files.install`. In general these operations map closely to their Ansible counterparts, so the vocabulary will be familiar.

The following deploy installs and starts nginx:

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

Point that deploy at your staging inventory with `dry=True` first and you get a per-host, per-operation diff of what staging is about to look like — the packages that will be installed, the file that will change, the service that will be started or left alone — before any of it happens. Read it, then run the same code without `dry=True` to apply.

The program is asynchronous. Operations are dispatched to every host in the inventory concurrently, and the controller waits for every host to finish an operation before advancing to the next.

`run(...)` returns the plans and the report as structured objects your program can inspect, so the dry-run diff is not just printable text — it is data you can gate a rollout on, log to a dashboard, or attach to a pull request.

Reemote is a library: **there is no `reemote` command to invoke and no DSL to learn.** You write a Python script like the one above and run it with your usual interpreter. The script runs on the *controller* — the machine you deploy from — and reemote is not installed on the target.

## Four words to know

- An **inventory** identifies the servers and how to connect to them.
- A **fact** reads information: a hostname, installed packages or a file's contents.
- An **operation** describes a requested change, such as ensuring a file contains some text. Operations use facts to decide what work is needed.
- An **action** runs immediately, outside a deployment, and returns its result. Use actions for one-off commands or checks that do not have a suitable operation.

A **deploy** is a Python generator that yields operation requests in order, as in the example. You do not need to implement facts or operations to use the library.

## What just happened?

`run(inventory, deploy, ...)` validates the inventory and runs two phases:

1. **Prepare:** build the requests and query the current host state to produce a plan. `dry=True` stops here — but it still connects to each host and runs fact queries, so a preview needs working credentials and remote tools. It does not send the operation's upload or install commands.
2. **Apply:** build the requests again, recheck the state before each operation and execute the work that is then needed.

A plan is a preview against current state, not a simulation of every consequence of earlier operations. For example, installing a package can create a file that a later operation should remove. That removal might appear unnecessary during prepare but become necessary during apply. The requests remain the same; their change decisions can differ.

The return value is `(plans, report, hosts)`. `plans` contains the per-host preview; `report` contains the apply log, or is `None` for a dry run. The host objects represent your targets, not a complete snapshot of their files and services. `run` closes the connections it used when it finishes.

Deploy functions are deliberately **pure descriptions of work**: the same fixed inputs should produce the same requests. Keep file reads, API calls, time-dependent decisions and immediate actions outside them. The guide explains how to combine those activities with deployments safely.

The demo above is not idempotence: each `run(...)` starts from a fresh container, so the greeting file is always absent. Against a host that keeps its state — a real server, for example — a second apply of the same deploy reports:

```text
demo: Write a greeting         [no change: content already up to date (/tmp/reemote-hello.txt)]
```

This is **idempotence**: asking for the same state again does not rewrite an already-correct file. Do not assume every operation is idempotent; an explicit restart, for example, deliberately performs work again.

## Why a Python API?

Ansible is a common choice for server configuration. If you prefer to express that work in Python, reemote keeps the configuration logic in the language you already use: functions instead of a separate task language, ordinary imports instead of a special role loader, and Python data structures instead of expressions embedded in configuration files.

That is especially useful when deployment is part of an application. You can obtain an inventory from your own service, generate configuration with a Python library, share validation code with your application and await a deployment from an existing async workflow. Your editor can navigate the code, your tests can inspect the requested operations, and your application can consume structured results rather than scrape terminal output.

You still need to understand the servers you manage. Python does not make package names portable or remove the need for SSH permissions. reemote supplies a configuration API, not an automatic translation between operating systems.

## Pointing at real servers

The docker connector is how this page demos the two phases; real targets usually connect over SSH. To point the example at one, replace its connection block:

```json
[
  {
    "host": "demo",
    "family": "Ubuntu",
    "connection": {
      "address": "192.0.2.10",
      "username": "deploy",
      "key_path": "~/.ssh/id_ed25519"
    }
  }
]
```

Replace `192.0.2.10` with your server's address, `deploy` with your login account, and the key path if necessary. The address shown is a documentation placeholder, not a public test server.

`host` is a label used in results; `connection.address` is the actual SSH destination. They need not be the same. Use a different label for every server. `family` declares which OS family the target belongs to — one of `RedHat`, `Ubuntu` or `Alpine` — so your script can reach for the right module (`apt`, `dnf`, `apk`) without connecting first. JSON is just a convenient way to load a Python list of dictionaries; reemote does not read the file itself — the script above keeps its inventory inline.

That is enough inventory for a first deployment. Groups, tags, passwords, ports and other options can wait until you need them.

An SSH target needs a working login, ordinary Linux shell utilities and SCP support; reemote itself is not installed there. Before deploying, check that you can SSH into your server with the account and key you intend to use. Verify its host-key fingerprint through a trusted source and add the verified key to your known-hosts file. Keep host-key checking enabled.

**Installing from source is the recommended route for real work**, because the checkout also gives you the runnable `examples/` tree that the reference pages link to — over 400 usage examples — and lets you pin the exact revision you deploy from:

```bash
git clone https://github.com/kimjarvis/reemote.git
cd reemote
uv sync
source .venv/bin/activate
```

`uv sync` creates `.venv`, installs reemote in editable mode and resolves its declared dependencies — `asyncssh`, `distro` and `pydantic` — so the SSH transport needs no separate install step. If you would rather not activate the environment, prefix commands with `uv run`. Inside a project of your own, use `uv add reemote` instead so it lands in your `pyproject.toml`. Check that the PyPI release you install is the version these documents describe; the source checkout is always current.

## Grow the example

To target another server, add a second inventory entry with a unique `host` label. The same deploy is used for both. Start with a small test inventory, since an apply affects every eligible target you supply.

To remove the greeting when you are finished, replace the request in `deploy` with:

```python
    yield op(
        files.file,
        name="Remove the greeting",
        path="/tmp/reemote-hello.txt",
        present=False,
    )
```

Preview and apply as before. This removes only the named regular file. For packages and services, choose operations matching the target: `apt.packages` for an APT system, `dnf.packages` for a DNF system, and `systemd.service` for systemd. The [guide's server example](guide.md#configure-a-small-web-server) combines packages, files and a service with the required privileges.

## Where to go next

- Read the [Guide](guide.md) for a roughly one-hour progression through inventories, useful deployments, facts, actions, composition and the execution model.
- Use **API Reference → Modules** to find facts and operations by subject. Start with [files](modules/files.md), [apt](modules/apt.md), [dnf](modules/dnf.md), [server](modules/server.md) or [systemd](modules/systemd.md). Class-style names such as `FileContents` are facts; lowercase functions such as `install` are operations. Entry types describe returned data, not additional commands to run.
- Use the [Actions reference](actions/actions.md) for immediate commands, and the [SSH](connectors/ssh.md) or [Docker/Podman](connectors/docker.md) pages for connection settings.
- To package work as reusable **roles** that read facts and reports back into their own logic, see [Stages](stages.md), [Role variables](rolevars.md) and the wave-loop driver [`run_waves`](drivers.md#wave-loop).
- The [Roles catalogue](roles/index.md) is the index of the forty-four roles the library ships, grouped by type — web servers, databases, caches and shared storage, language runtimes and tooling, vendor repositories and platforms, base system, security and certificates, accounts, and time synchronisation. Each entry says what the role migrates, which OS family it targets, how many waves it runs and what it hands back to a composing deploy.

The guide also explains [how to read the reference and find examples](guide.md#finding-facts-operations-and-examples), so you can extend your deployment without reading the whole API first.
