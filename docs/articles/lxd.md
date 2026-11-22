# Managing LXD from Python: Reemote's `lxd` module

*LXD turns one Ubuntu server into a fleet of system containers and VMs, and Canonical's first-steps tutorial shows how easily you can drive it by hand. This article walks the same first steps from Python: Reemote is a **library** that manages LXD over `sshd` and the `lxc` client, plans before it applies, and hands you every result as data you can `await`, inspect, and test.*

## The problem: configuration management with LXD on Ubuntu

Ubuntu plus LXD is a compelling shape. One box, LXD snap-installed and initialized in two commands, and suddenly you have a fleet of containers that behave like lightweight virtual machines — full Linux init systems you can launch, constrain, snapshot and roll back in seconds. Unlike Docker's single-application containers, an LXD container runs a whole userland: systemd, apt, sshd, the lot.

[Canonical's first-steps tutorial](https://canonical.com/lxd/docs/latest/tutorial/first_steps/) covers the CLI moves: install and init, launch, exec, file push/pull, resource limits, snapshots. By hand, each is a one-liner. The pain starts when those moves need to live *inside a program* — provisioning a container per tenant from an API, rebuilding a staging fleet from a database query, gating a rollout on what actually changed, rolling back automatically when a health check fails.

That is the gap: **there is no serious configuration-management library you can `import` and point at an LXD host.** Ansible can drive LXD, but it is a CLI-shaped tool — a YAML playbook interpreted by an executable you shell out to, with results you scrape from stdout. Reemote is that library, and `lxd` ships as a first-class namespace with facts, operations, and the same two-phase prepare-then-apply runner everything else uses.

## Why Python has real advantages over Ansible

Ansible chose YAML because it is readable and does not require knowing a programming language. That is a fair trade when your team does not write Python. When it does — and most automation teams today do — the trade costs more than it saves:

- **Your editor knows your code.** `lxd.instance` and `lxd.instance_snapshot` are functions: go-to-definition, rename, autocomplete, and a type check that catches a typo before it reaches a production host. A misspelled YAML module name surfaces at runtime, in the middle of a rollout.
- **No Jinja2 as a second language.** A container's CPU limit, a device property, an image reference — in reemote each is just a Python expression, evaluated by the interpreter you already trust, not a `{{ hostvars[...] }}` string your IDE cannot see through.
- **Composition is `import`, not directory layout.** A fleet is a loop over a list of tenants; a reusable chunk of provisioning is a generator you import and call. No `tasks/main.yml`, `defaults/main.yml`, `handlers/main.yml` convention to memorise.
- **The result is data, not stdout.** If your application needs to know whether the snapshot was created, you read `report.entries[i].changed` — you do not regex-scrape a terminal or depend on callback plugins to reshape the log.
- **Testing is `pytest`.** A deploy function is a pure generator; you can inspect the requests it yields without touching a server. No molecule, no testinfra, no ansible-lint.
- **`await` is your concurrency model.** Hosts progress in parallel because the transport is asyncssh, not because a CLI forked worker processes you have to configure.

## The solution: Reemote is a library

Reemote is a Python package (`pip install reemote`) with two properties that separate it from every CLI-shaped tool.

### Async end to end, on asyncssh

The transport is [asyncssh](https://asyncssh.readthedocs.io/), and the entire execution path — connecting, gathering facts, sending commands, closing — is coroutines. You `await` a deploy and hosts progress in parallel; network waits overlap across hosts while each host's commands stay in order. Your deploy script remains a plain synchronous generator, so you never write `async` inside an operation.

```python
plans, report, hosts = await run(inventory, deploy)
```

`run` is the single entry point: it validates the inventory, opens connections lazily, plans and applies in two phases, then closes everything in a `finally`.

### A library returning structured results, not a CLI

`run(...)` returns `(plans, report, hosts)`:

- **`plans`** — `{host_label: [PlannedOperation, ...]}`, the proposed work per host.
- **`report`** — an `ApplyReport` with a `LogEntry` per applied operation, each carrying `changed`, `reason`, and the exact commands sent. `None` when `dry=True`.
- **`hosts`** — the materialised host objects.

The read side is data too. Facts return typed Python structures — the instance listing is a list of dicts with config, devices and profiles, not a parsed `lxc list` table — and one-off commands run as *actions* that return a `Result` with `exit_status`, `stdout` and `stderr` as separate fields. `format_plan` and `format_log` are convenience renderers for humans; your application consumes the objects directly.

## The `lxd` module: LXD's own vocabulary as Python

`lxd` pairs read-only **facts** with **operations** that converge the daemon toward a described state. An operation reads a fact, decides whether anything must change, and yields commands only when it must — so re-running against a stable host is a no-op.

The operations cover the whole control surface: **instance lifecycle** (create containers or VMs with profiles, config and devices, reconcile an existing instance non-destructively, drive started/stopped/frozen, delete), **configuration reconciliation** (set only the config keys, devices and profiles that drifted), **images and aliases** (cache from a remote, pin an alias), **snapshots and copies** (create, delete, restore, copy locally or cross-remote), the **surrounding resources** (projects, profiles, networks, storage pools and custom volumes), and **background-operation control** (cancel or wait on a registered daemon operation).

The facts cover the audit view: full instance listings, per-instance config, runtime state and snapshots, the image store and its aliases, networks, profiles, projects, pools and volumes, plus daemon metadata, hardware resources and the operations currently in flight. Everything runs through the `lxc` client on the target host — JSON listings and `lxc query` REST reads — and the CLI itself waits on the daemon's asynchronous mutations, so the library models no REST polling. Collections are project-scoped (default `"default"`) and can address any remote in the host's `lxc remote` list. The exhaustive API lives in the [lxd module reference](../modules/lxd.md).

One naming note the tutorial also makes: `lxd` is the daemon, `lxc` is the client. Reemote drives `lxc` on the host — the same client you would type at — so every command below is one you could run yourself.

## First steps with LXD, in Python

The rest of this article follows Canonical's tutorial step by step, CLI on the left, Reemote on the right. Every output block was produced live against a real Ubuntu host running LXD 6.10.1 from the `6/stable` snap channel.

One structural difference from a container-based quickstart: **LXD needs a real Linux kernel** — it does not nest inside a Docker container — so the SSH connector is the natural transport. Install reemote (Python 3.13+) and point an inventory entry at your Ubuntu host:

```bash
uv venv .venv && source .venv/bin/activate && uv pip install reemote
```

```python
INVENTORY = [{
    "host": "ubuntu",
    "family": "Ubuntu",
    "connection": {
        "address": "192.168.0.181",   # your Ubuntu host
        "username": "supervisor",
        "key_path": "~/.ssh/id_ed25519",
    },
}]
```

### A. Installation and initialization

The tutorial installs LXD from the snap store, adds your user to the `lxd` group, and initializes the daemon:

```bash
sudo snap install lxd
sudo usermod -aG lxd "$USER" && newgrp lxd
lxd init --minimal   # default storage pool and bridge networking
```

In reemote the first two are ordinary operations — `snap.package` from the snap namespace, `server.user` from the server namespace — composed in one deploy:

```python
from reemote import op, run, server, snap

def bootstrap(host):
    yield op(snap.package, name="Install LXD",
             packages=("lxd",), channel="6/stable", sudo=True)
    yield op(server.user, name="Login user in the lxd group",
             user="supervisor", groups=("lxd",), append=True, sudo=True)
```

Verified output against the demo host, where both are already in place:

```text
ubuntu: Install LXD              [no change: all packages present]
ubuntu: Login user in the lxd group [no change: user supervisor already as requested; directory /home/supervisor already exists]
```

Two honesty notes. First, `channel="6/stable"` pins the LXD 6 LTS track — the tutorial's own advice for production; omit it for `latest/stable`. Second, the `lxd` group grants root-equivalent access to the daemon, so treat membership as the security decision the tutorial says it is. Operations ride `sudo=True` and do not need the group, but unprivileged *fact reads* do — and group membership applies to new login sessions, which reemote's next connection picks up automatically.

Initialization is the one step with no dedicated operation: `lxd init` is a one-time bootstrap, not a desired state. The library-shaped answer is to gate it on a fact — ask the daemon what storage pools exist, and run the init only where the answer is empty:

```python
import asyncio
from reemote import Host, LxdStoragePools, Shell, gather_action, gather_fact

hosts = [Host.from_inventory(entry) for entry in INVENTORY]
pools = await gather_fact(hosts, LxdStoragePools)
await asyncio.gather(*(host.aclose() for host in hosts))

uninitialized = [host for host in hosts if not pools[host.name]]
if uninitialized:
    await gather_action(uninitialized, Shell,
                        command="lxd init --minimal", sudo=True)
```

On the demo host the fact returns `['default', 'fast']`, so the init never runs. This is the structured-results property doing real work: the decision is a Python `if` over typed data, not a shell guard scraped from `lxc` output.

### B. Launching your first container

The tutorial launches an Ubuntu container named `web1`:

```bash
lxc launch ubuntu:24.04 web1
```

In reemote that is `lxd.instance`, with the desired configuration attached:

```python
from reemote import lxd, op

def deploy(host):
    yield op(lxd.instance, name="Ensure web1",
             instance="web1", image="ubuntu:24.04",
             config=("limits.cpu=2", "limits.memory=1GiB"),
             sudo=True)
```

On a fresh host the operation plans `[changed: lxd instance web1 will be created]` and the apply sends one command:

```bash
sudo lxc launch ubuntu:24.04 web1 -c limits.cpu=2 -c limits.memory=1GiB --project default < /dev/null
```

Three details worth noticing. The `--project default` is explicit because the CLI's *current* project can be switched interactively — a deploy never relies on it. The trailing `< /dev/null` keeps a non-interactive SSH session from blocking on launch's prompts. And the image reference is the tutorial's `ubuntu:` remote — the official Ubuntu cloud images — which LXD caches locally after the first download.

An existing instance is reconciled **non-destructively**: profiles assigned, drifted config keys set, drifted devices re-attached. Base-image drift is *reported* in the reason, never recreated — destroying a container stays an explicit `present=False` choice. The same operation launches VMs with `instance_type="vm"`; a container/VM type mismatch raises rather than silently converting.

### C. Interacting with the container

The tutorial runs a one-off command inside the container, or opens a shell:

```bash
lxc exec web1 -- apt update
lxc shell web1
```

Reemote is a library, so the one-off command is an **action**: `Shell` runs it on the host and returns the whole outcome as a `Result`, fanned out across hosts with `gather_action`:

```python
from reemote import Host, Shell, gather_action

hosts = [Host.from_inventory(entry) for entry in INVENTORY]
results = await gather_action(hosts, Shell,
                              command="lxc exec web1 -- apt update")
for name, result in results.items():
    print(name, "exit:", result["exit_status"])
    print(result["stdout"])
```

Verified output (excerpt):

```text
ubuntu exit: 0
Ign:1 http://security.ubuntu.com/ubuntu noble-security InRelease
Ign:2 http://archive.ubuntu.com/ubuntu noble InRelease
Get:1 http://security.ubuntu.com/ubuntu noble-security InRelease [126 kB]
Hit:2 http://archive.ubuntu.com/ubuntu noble InRelease
...
```

The `Result` keeps `stderr` separate, and on this run it carried a familiar warning: `WARNING: apt does not have a stable CLI interface. Use with caution in scripts.` That is the library shape paying off — the warning is a field you can inspect, not noise interleaved into a log you parse. (For scripted package work inside the container, prefer `apt-get`, or point reemote's own `apt` module at the container — more on that below.)

The interactive `lxc shell` stays what it is: a human tool. Reemote does not wrap it, and a deploy should not need it. But there is a deeper move the tutorial hints at. An LXD container is a full Linux system with its own IP on the bridge — the demo `web1` holds `10.189.152.166` on `eth0`, and the stock image ships OpenSSH installed (though not running by default). Start sshd, inject an authorized key — cloud-init at launch or a one-off `lxc exec` — and the container becomes **just another inventory entry**. The same `apt`, `systemd` and `files` modules that manage the host then manage the guest, over the same asyncssh transport. One library, both layers of the stack.

### D. Moving files in and out

The tutorial pushes a file into the container and pulls one out:

```bash
lxc file push ./index.html web1/var/www/html/index.html
lxc file pull web1/etc/hostname ./hostname.txt
```

There is no dedicated `lxd` file operation, deliberately — transfers compose from primitives you already have. A file move from your control machine into a container is two hops: `files.put` uploads control → host over the connection's scp channel (content-idempotent: it compares sha1 digests and transfers only on a difference), then a `Shell` action runs `lxc file push` host → container. Pulling is the mirror, with `files.get` downloading host → control into a stream:

```python
import io
from reemote import Host, Shell, files, gather_action, op, run

# Module level: the same stream object must reach both deploy phases.
INDEX = io.StringIO("<h1>Hello from reemote</h1>\n")
HOSTNAME = io.BytesIO()

def stage(host):
    yield op(files.put, name="Stage index.html on the host",
             src=INDEX, dest="/tmp/index.html")

def fetch(host):
    yield op(files.get, name="Fetch web1 hostname",
             src="/tmp/web1-hostname.txt", dest=HOSTNAME)

async def main():
    await run(INVENTORY, stage)                    # control -> host
    await gather_action([Host.from_inventory(e) for e in INVENTORY], Shell,
        command="lxc file push /tmp/index.html web1/var/www/html/index.html")
    await gather_action([Host.from_inventory(e) for e in INVENTORY], Shell,
        command="lxc file pull web1/etc/hostname /tmp/web1-hostname.txt")
    await run(INVENTORY, fetch)                    # host -> control
    print(HOSTNAME.getvalue())                     # b'web1\n'
```

Verified output of the two transfers and the round-trip check:

```text
ubuntu: Stage index.html on the host [changed: file /tmp/index.html missing or differs from the local source]
    $ scp <stream> -> /tmp/index.html
ubuntu: Fetch web1 hostname      [changed: local copy missing or differs from remote /tmp/web1-hostname.txt]
    $ scp /tmp/web1-hostname.txt -> <stream>
```

The push exited 0, and `lxc exec web1 -- cat /var/www/html/index.html` returned exactly `<h1>Hello from reemote</h1>`. Re-running the staging deploy converges:

```text
ubuntu: Stage index.html on the host [no change: file already up to date (/tmp/index.html)]
```

Two practical notes. The tutorial's `/var/www/html` path assumes a web-tier container; create it first if needed (`lxc exec web1 -- mkdir -p /var/www/html`, itself a `Shell` action). And the streams are defined at module level on purpose: reemote runs your deploy twice (prepare, then apply) and compares requests by identity, so a fresh `StringIO` per phase would break the determinism guard.

### E. Setting resource limits (CPU & RAM)

The tutorial constrains the container like a VM:

```bash
lxc config set web1 limits.cpu 2
lxc config set web1 limits.memory 1GiB
```

In reemote the limits are already part of the `lxd.instance` request above — creation carries them, and every later run reconciles them. The demo host's `web1` had `limits.cpu=2` but `limits.memory=2GB`, so the verified plan flagged exactly the one drifted key:

```text
ubuntu: Ensure web1              [changed: lxd instance web1 will be updated (config keys will be set: limits.memory)]
```

and the apply sent exactly one command — the CPU limit, already correct, was left alone:

```text
    $ sudo lxc config set web1 limits.memory 1GiB --project default
```

When you want the reconciliation half on its own — an instance that must already exist, no creation path — `lxd.instance_config` does the same drift-only comparison of config keys, devices and profiles. Either way, limits are data in a tuple, not two shell lines you hope ran in order.

### F. Snapshots and safety

The tutorial takes a snapshot before a risky change and restores it if things break:

```bash
lxc snapshot web1 before-upgrade
lxc restore web1 before-upgrade
```

Creating the safety net is one more request in the same deploy:

```python
    yield op(lxd.instance_snapshot, name="Safety net before changes",
             instance="web1", snapshot="before-upgrade", sudo=True)
```

Verified plan and apply on a host where the snapshot did not exist yet:

```text
ubuntu: Safety net before changes [changed: lxd snapshot web1/before-upgrade will be created]
    $ sudo lxc snapshot web1 before-upgrade --project default
```

The operation is idempotent against the snapshot listing: once `before-upgrade` exists, every later run reports `[no change: lxd snapshot web1/before-upgrade already exists]` — a stable restore point, not a rolling one. `present=False` deletes it; `stateful=True` captures runtime state too.

Restore is an **action**, not a desired state — it always runs when you ask for it — and a non-stateful restore requires the instance to be stopped first. In reemote that sequence is ordinary composition:

```python
def rollback(host):
    yield op(lxd.instance_state, name="Stop web1",
             instance="web1", state="stopped", sudo=True)
    yield op(lxd.instance_snapshot, name="Roll back web1",
             instance="web1", snapshot="before-upgrade",
             restore=True, sudo=True)
    yield op(lxd.instance_state, name="Start web1 again",
             instance="web1", state="started", sudo=True)
```

Verified apply log:

```text
ubuntu: Stop web1                [changed: lxd instance web1 will be stopped]
    $ sudo lxc stop web1 --project default
ubuntu: Roll back web1           [changed: lxd instance web1 will be restored to before-upgrade]
    $ sudo lxc restore web1 before-upgrade --project default
ubuntu: Start web1 again         [changed: lxd instance web1 will be started]
    $ sudo lxc start web1 --project default
```

The dry-run plan of this same deploy shows the two-phase model earning its keep: `Stop web1` and `Roll back web1` plan as changes, but `Start web1 again` plans as `[no change: lxd instance web1 already started]` — because at plan time the container is still running. The apply phase advances all hosts in lockstep with a fresh fact cache per step, so by the time the start request is evaluated the stop has really happened, and it starts. Plan and apply agree on the *requests*; the facts each phase sees are the honest ones.

### Run it twice: convergence, verified

Here is the whole first-steps deploy — launch-or-reconcile plus the safety net — as one script:

```python
import asyncio
from reemote import format_log, format_plan, lxd, op, run

def deploy(host):
    yield op(lxd.instance, name="Ensure web1",
             instance="web1", image="ubuntu:24.04",
             config=("limits.cpu=2", "limits.memory=1GiB"), sudo=True)
    yield op(lxd.instance_snapshot, name="Safety net before changes",
             instance="web1", snapshot="before-upgrade", sudo=True)

async def main():
    plans, report, _ = await run(INVENTORY, deploy)
    print(format_plan(plans))
    if report is not None:
        print(format_log(report))

asyncio.run(main())
```

The first run produced the drift and snapshot output shown in E and F. Run the identical script again against the same host and it converges — verified:

```text
ubuntu: Ensure web1              [no change: lxd instance web1 already exists as configured]
ubuntu: Safety net before changes [no change: lxd snapshot web1/before-upgrade already exists]
```

That is the property the CLI tutorial cannot give you: `lxc launch` fails on an existing name, `lxc snapshot` fails on an existing snapshot, and a shell script accumulates `|| true` guards. A reemote deploy describes the state you want and reports, per request, whether anything had to move.

## What follows: Reemote can manage LXD instead of Ansible

Once configuration management is a library, everything Python already has becomes available to your LXD deploys:

- **Deploy from your application.** `await run(inventory, deploy)` inside a FastAPI handler, a Celery task, or a REPL — provision a container per tenant without shelling out to a playbook runner.
- **Compose with real code.** Build the inventory from a database query; generate per-container limits from a Pydantic model; loop a fleet into existence with a `for`.
- **Test with `pytest`.** Deploy functions are pure generators — assert on the requests they yield without a server in sight.
- **Consume structured results.** `report.entries[i].changed`, `.reason`, `.commands` are data you can log, chart, feed to a webhook, or gate a rollout on. Snapshot → upgrade → restore-on-failure is an `if`, not a handler syntax.
- **Two-phase execution for free.** Every `run` prepares then applies; `dry=True` is a real preview against live facts, not a simulation.
- **One library for host and guest.** Manage the LXD host with `lxd`, then point the same `apt`/`systemd`/`files` modules at the containers it runs. The transport, the determinism guard and the report shape do not change.
- **Handlers as ordinary Python.** Read the first deploy's report, freeze the changed hosts with `reemote.handlers.changed_hosts`, feed them into a second deploy.

You can use reemote instead of Ansible whenever managing LXD is part of a Python program rather than a shell command in a CI job — and you keep LXD's own vocabulary (instances, profiles, projects, devices, snapshots) intact instead of flattening it through a lowest-common-denominator container module.

## What Reemote is not

- **Not a deployment CLI.** There is no `reemote` command; you write the script.
- **Not an inventory format.** Inventory is a Python list of dicts — load it from JSON, YAML, a database, or write it inline.
- **Not an LXD API client.** Reemote drives the `lxc` CLI on the host, exactly as the tutorial does; it does not embed a REST SDK or poll the daemon's operations endpoint.
- **Not a role ecosystem.** Reemote ships modules for `apt`, `systemd`, `apk`, `openrc`, `dnf`, `files`, `server`, `git`, `mysql`, `postgresql`, `iptables`, `firewalld`, `ufw`, `selinux`, `lxd`, `snap`, `flatpak`, `sysctl`, `gpg`, and `hardware` — but there is no Ansible Galaxy equivalent.
- **Not a replacement for `lxc shell`.** Interactive debugging stays interactive; reemote is for the programmable half.

## Try it

- Documentation and full guide: <https://reemote.org/>
- The `lxd` module reference: <https://reemote.org/modules/lxd/>
- Source and runnable examples: <https://github.com/kimjarvis/reemote>
- PyPI: `pip install reemote`
- License: EPL-2.0

If you have ever wanted Canonical's first-steps tutorial to be a program you can import, test and `await` — with the launch, the limits, the snapshot and the rollback all reporting themselves as data — the walkthrough above is the whole onboarding. Give it an Ubuntu host and a minute.