# Managing Ubuntu from Python: Reemote's `apt` and `systemd` modules

*Ansible manages Ubuntu through a CLI, a YAML playbook, and a Jinja2 layer that fights your IDE. Reemote is a Python **library** that configures Ubuntu over `sshd` and a plain shell — `apt` and `systemd` included — and hands you the result as data you can `await`, inspect, and test. The whole thing runs in a throwaway container in under a minute.*

## The problem: configuration management on Ubuntu

Ubuntu is what most cloud VMs, most CI runners and a large share of internal fleets boot into. Its shape is well known and Ansible handles it cleanly — but the pain is not "can the tool talk to the box". It is everything the tool-shaped-like-a-CLI costs you once you want to embed deployment in a Python program.

That is the gap: **there is no serious configuration-management library you can `import` and point at an Ubuntu box.** Reemote is that library, and Ubuntu is where it feels most at home — `apt` and `systemd` ship as first-class namespaces with facts, operations, and the same two-phase prepare-then-apply runner everything else uses.

## Try it in under a minute (docker connector)

Reemote ships a **docker connector** that runs your deploy inside a local container, so you need neither a remote host nor an SSH key to see it work. **You do need Docker or Podman installed locally** — the connector shells out to whichever CLI you name, and there is no default. Three commands:

```bash
# 1. install reemote into a fresh venv (Python 3.13+; pip works just as well as uv)
uv venv .venv && source .venv/bin/activate && uv pip install reemote
# 2. save the script below as ubuntu_demo.py
# 3. run it — starts a throwaway ubuntu:24.04 container, configures it, cleans up
python ubuntu_demo.py
```

```python
# ubuntu_demo.py
import asyncio
from reemote import apt, format_log, format_plan, op, run

INVENTORY = [{
    "host": "ubuntu-demo",
    "connection": {
        "connector": "docker",
        "identifier": "ubuntu:24.04",
        "docker_cmd": "podman",   # or "docker"
    },
}]

def deploy(host):
    # The most common first task after dropping onto a fresh Ubuntu server:
    # refresh the package lists and install nginx.
    yield op(apt.packages,
             name="Install nginx",
             packages=("nginx",),
             update=True)

async def main():
    plans, report, _ = await run(INVENTORY, deploy)
    print("== Plan ==")
    print(format_plan(plans))
    print("== Applied ==")
    if report is not None:
        print(format_log(report))

asyncio.run(main())
```

Verified output:

```text
== Plan ==
ubuntu-demo: Install nginx [changed: 1 package(s) to install]
== Applied ==
ubuntu-demo: Install nginx [changed: 1 package(s) to install]
    $ apt-get update
    $ DEBIAN_FRONTEND=noninteractive apt-get -y install nginx
```

Three things worth noticing, because they are exactly where Ubuntu + Reemote differ from Ubuntu + Ansible:

- **No YAML, no Jinja2, no `ansible.builtin.apt`.** `apt.packages` is a Python function your editor knows: go-to-definition, autocomplete, type check. The `packages=` argument is a tuple because operation kwargs must stay hashable — the same determinism rule that lets reemote run your deploy twice (once to plan, once to apply) and prove both passes agree.
- **No `-y` to remember, no `DEBIAN_FRONTEND` to export.** The apt namespace knows its own non-interactive vocabulary and renders it for you. Reemote exposes apt's own idioms rather than flattening them into a lowest-common-denominator package API — the same shape `apk.no_cache` takes on Alpine.
- **`update=True` is a stateless prefix.** It always runs `apt-get update` before the install, exactly like the baseline's `apt.update`; there is no `cache_time` stamp file, deliberately, because the stamp would drift between the prepare and apply phases and break the determinism guard.

One honesty note, the same one the throwaway-container workflow always carries: every run reports `[changed: ...]`, even the second time, because the image target starts a *fresh* `ubuntu:24.04` container on each invocation, so nginx genuinely is absent each time. Point reemote at a stable target — an SSH host, or an existing container ID — and the second run correctly reports `[no change: all packages present]`. You will see exactly that below.

## Why Python has real advantages over Ansible

Ansible chose YAML because it is readable and does not require knowing a programming language. That is a fair trade when your team does not write Python. When it does — and most automation teams today do — the trade costs more than it saves:

- **Your editor knows your code.** `apt.packages` and `systemd.service` are functions: go-to-definition, rename, autocomplete, and a type check that catches a typo before it reaches a production box. A misspelled YAML module name surfaces at runtime, in the middle of a rollout.
- **No Jinja2 as a second language.** `{{ hostvars[...] }}` is a stringly-typed escape hatch that fights your IDE and your tests. In reemote the value is just a Python expression, evaluated by the interpreter you already trust.
- **Composition is `import`, not directory layout.** Ansible roles load by convention (`tasks/main.yml`, `defaults/main.yml`, `handlers/main.yml`); reemote deploys are generators you import, call, and loop over.
- **The result is data, not stdout.** If your application needs to know what changed, you read `report.entries[i].changed` — you do not regex-scrape a terminal, and you do not depend on `callback_plugins` to reshape the log for you.
- **Testing is `pytest`.** A deploy function is a pure generator; you can inspect the requests it yields without touching a server. No molecule, no testinfra, no ansible-lint.
- **`await` is your concurrency model.** Hosts progress in parallel because the transport is asyncssh, not because a CLI forked worker processes you have to configure.

## The solution: Reemote is a library

Reemote is a Python package (`pip install reemote`) with two properties that separate it from every CLI-shaped tool.

### Async end to end, on asyncssh

The transport is [asyncssh](https://asyncssh.readthedocs.io/), and the entire execution path — connecting, gathering facts, sending commands, closing — is coroutines. You `await` a deploy and hosts progress in parallel; network waits overlap across hosts while each host's commands stay in order. Your deploy script remains a plain synchronous generator, so you never write `async` inside an operation.

```python
plans, report, hosts = await run(inventory, deploy)
```

`run` is the single entry point: it validates the inventory, opens connections lazily, plans and applies in two phases, then closes everything in a `finally`. The **same code** runs over SSH or over the docker connector — you pick the transport in the inventory entry, not in the deploy. That is why you can develop an Ubuntu role against a local container and ship it to a VM without changing a line.

### A library returning structured results, not a CLI

`run(...)` returns `(plans, report, hosts)`:

- **`plans`** — `{host_label: [PlannedOperation, ...]}`, the proposed work per host.
- **`report`** — an `ApplyReport` with a `LogEntry` per applied operation, each carrying `changed`, `reason`, and the exact commands sent. `None` when `dry=True`.
- **`hosts`** — the materialised host objects.

`format_plan` and `format_log` (what you saw above) are convenience renderers for humans. Your application consumes the objects directly — no parsing stdout to figure out what happened, no `--check` mode plus a callback plugin to reconstruct the plan after the fact.

## The `apt` module: Ubuntu's package manager as Python

`apt` pairs read-only **facts** with **operations** that converge the box toward a described state. An operation reads a fact, decides whether anything must change, and yields commands only when it must — so re-running against a stable target is a no-op.

The operations cover four kinds of apt work:

- **Package lifecycle** — install, upgrade, remove, autoremove, and hold by name, with exact-version pinning. Every mutation is idempotent against the installed-package fact; the whole-box upgrade verbs are additionally gated on a dry-run simulation, so a fully patched host is a genuine no-op rather than a shell-out that quietly does nothing.
- **Repository configuration** — legacy `sources.list` lines, modern deb822 `.sources` files, GPG keyrings under `/etc/apt/keyrings/` for `signed-by=`, and Ubuntu PPAs. The file-managing operations all use a **spool-and-install rewrite** — read the current text, apply a pure transform, scp the result to `/tmp`, install it with one elevated `sh -c` — so comments, ordering, and unrelated lines survive verbatim and nothing ever runs `sed -i` on a protected file.
- **Standalone `.deb` files** — install or remove a package archive already on the target (upload one with `files.put` first), version-aware and idempotent.
- **The stateless refresh** — `apt-get update` as its own step, so the list-refresh is separately plannable from anything that consumes it.

The facts cover the same ground from the read side: what is installed, what would upgrade, what would autoremove, what is held back, which version `apt install` would pick and from which repository, which sources are configured (parsed from both the legacy and the deb822 formats), which keyrings are present, and what a dry-run `apt-get` proposes to change. Every list fact returns sorted, de-duplicated, JSON-printable typed entries — not a scraped string — so your application can consume them directly.

## The `systemd` module: Ubuntu's init as Python

systemd manages services along **two independent axes**: whether a unit is *running* right now, and whether it is *enabled* at boot. A unit can be enabled but stopped, or running but not enabled. The `systemd` namespace handles both in one operation, and goes well beyond services.

The operations cover three kinds of systemd work:

- **Unit state** — one service operation converges `running` and `enabled` from their facts and yields `systemctl start`/`stop`/`enable`/`disable` only on a difference, alongside the non-idempotent verbs (`restart`, `reload`, `reload-or-try-restart`, `reset-failed`) for when you mean it. A separate `daemon-reload` operation keeps the reload step plannable on its own.
- **Unit-file configuration** — drop-ins under `/etc/systemd/system/<unit>.d/`, plus `/etc/tmpfiles.d/*.conf` and `/etc/sysusers.d/*.conf`. All three ride the same **spool-and-install** rewrite `apt.repo` uses, so the stock unit file stays pristine and every write is one unprivileged scp followed by one elevated `sh -c`.
- **Unit policy** — mask a unit to `/dev/null` so nothing can start it, apply the systemd preset policy, or clear failed state without also starting the unit.

The facts cover the audit view: per-unit running and enabled state, unit-file presence, unit properties and dependencies, failed units, scheduled timers, and the systemd version. Every `systemctl`-based fact and operation also accepts scope flags (`user_mode`, `machine`, `user_name`) for the per-user manager or a named machine, so the same code drives a system unit or a user unit.

### Install a package, then ensure its service — verified

The classic Ubuntu post-install move is *install the daemon, then enable and start it* — nginx for a web tier, say. In reemote that is `apt.packages` followed by `systemd.service`, composed in one deploy:

```python
def deploy(host):
    # 1. Install nginx (ships /lib/systemd/system/nginx.service).
    yield op(apt.packages,
             name="Install nginx",
             packages=("nginx",),
             update=True,
             sudo=True)
    # 2. Enable and start its systemd unit.
    yield op(systemd.service,
             name="Run nginx",
             service="nginx",
             running=True,
             enabled=True,
             sudo=True)
```

The ordering is the point. The apply phase runs in **lockstep** — one request index at a time across all hosts — so every host finishes the install before any host begins the service step. The `systemd.service` request therefore always sees the `nginx.service` unit file the install just delivered. There is no combined "ensure" operation to maintain; the two halves compose, and each is idempotent against its own facts.

First run against an Ubuntu host with systemd as PID 1:

```text
== Plan ==
ubuntu-demo: Install nginx [changed: 1 package(s) to install]
ubuntu-demo: Run nginx     [changed: nginx.service not running (inactive), will start; nginx.service not enabled, will enable]
== Applied ==
ubuntu-demo: Install nginx [changed: 1 package(s) to install]
    $ sudo apt-get update
    $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y install nginx
ubuntu-demo: Run nginx     [changed: nginx.service not running (inactive), will start; nginx.service not enabled, will enable]
    $ sudo systemctl start nginx.service
    $ sudo systemctl enable nginx.service
```

Run the identical script again against the *same* host and it converges — this is the `[no change: ...]` the throwaway quickstart could not show:

```text
== Plan ==
ubuntu-demo: Install nginx [no change: all packages present]
ubuntu-demo: Run nginx     [no change: nginx.service already running; nginx.service already enabled]
== Applied ==
ubuntu-demo: Install nginx [no change: all packages present]
ubuntu-demo: Run nginx     [no change: nginx.service already running; nginx.service already enabled]
```

Two Ubuntu-specific honesty notes:

- **The stock `ubuntu:24.04` container image does not run systemd as PID 1** — `systemctl start`/`enable` refuse to operate with "System has not been booted with systemd as init system". The quickstart at the top deliberately sticks to `apt.packages` for exactly that reason. To exercise the systemd half locally, use a systemd-capable image (`jrei/systemd-ubuntu:24.04` or your own `docker run --privileged ... /sbin/init` recipe); on a real Ubuntu VM or bare-metal host systemd is PID 1 and everything above just works.
- **`sudo` is uniform on Ubuntu.** Unlike Alpine (no `sudo`, only `doas`), Ubuntu ships `sudo` preinstalled and the first user is in the `sudo` group, so `sudo=True` on a request is the whole elevation story. Reemote renders `sudo <command>` on the descriptor and never needs a password prompt: use passwordless sudoers, or an SSH key for a login user with `NOPASSWD`, or `su=True` if you would rather reemote escalate through `su`.

## A day-one Ubuntu playbook, in Python

Here is the routine post-install set — the tasks an Ubuntu playbook always opens with — as one reemote deploy. No YAML, no Jinja2, no handlers section:

```python
def deploy(host):
    # (1) Patch the box: refresh lists, then upgrade everything the
    #     simulation says would move.
    yield op(apt.update, name="Refresh package lists", sudo=True)
    yield op(apt.upgrade, name="Apply pending upgrades", sudo=True)

    # (2) Baseline tooling every Ubuntu server wants.
    yield op(apt.packages, name="Install baseline tools",
             packages=("curl", "git", "htop", "unattended-upgrades"),
             sudo=True)

    # (3) Web tier: install nginx, then enable and start it.
    yield op(apt.packages, name="Install nginx",
             packages=("nginx",), sudo=True)
    yield op(systemd.service, name="Run nginx", service="nginx",
             running=True, enabled=True, sudo=True)

    # (4) Time sync — Ubuntu ships systemd-timesyncd already enabled;
    #     chrony is the drop-in when you need NTP-server behaviour.
    yield op(apt.packages, name="Install chrony",
             packages=("chrony",), sudo=True)
    yield op(systemd.service, name="Run chrony", service="chrony",
             running=True, enabled=True, sudo=True)

    # (5) Hardening: a drop-in that tightens sshd, then a reload — the
    #     "handler" is just the next request in the same generator.
    yield op(systemd.dropin, name="Harden sshd",
             filename="10-reemote.conf", unit="sshd.service",
             content="[Service]\nPermitRootLogin=no\n",
             sudo=True)
    yield op(systemd.daemon_reload, name="Reload systemd after drop-in",
             sudo=True)
    yield op(systemd.service, name="Restart sshd", service="sshd",
             restarted=True, sudo=True)
```

Task **(5)** is the one that shows off the library shape most. In Ansible the drop-in is a `template` plus a `notify: restart sshd` handler, and the handler fires only if the template reported `changed`. In reemote the "handler" is ordinary Python: run the config deploy, read its `report`, freeze the set of hosts that actually changed with `reemote.handlers.changed_hosts`, and feed that set into a second deploy that calls `systemd.service("sshd", restarted=True)`. No `notify` keyword, no handler-ordering rules to memorise — just the structured result you already have.

## What follows: Reemote can manage Ubuntu instead of Ansible

Once configuration management is a library, everything Python already has becomes available to your Ubuntu deploys:

- **Deploy from your application.** `await run(inventory, deploy)` inside a FastAPI handler, a Celery task, a Python GitHub Action, or a REPL.
- **Compose with real code.** Build the inventory from a database query; generate config text from a Pydantic model; share validation between the app and the deploy.
- **Test with `pytest`.** Deploy functions are pure generators — assert on the requests they yield without a server in sight.
- **Consume structured results.** `report.entries[i].changed`, `.reason`, `.commands` are data you can log, chart, feed to a webhook, or gate a rollout on.
- **Two-phase execution for free.** Every `run` prepares then applies; `dry=True` is a real preview against live facts, not a simulation.
- **Same code over SSH or Docker.** Flip the `connector` key and the identical deploy runs against an Ubuntu VM or a local container — test the role before you point it at production, or build an image from the same code that configures a host.
- **Handlers as ordinary Python.** Read the first deploy's report, freeze the changed hosts, feed them into a second deploy. `changed_hosts` and `restart_handler` ship for the common case.

You can use reemote instead of Ansible whenever managing Ubuntu is part of a Python program rather than a shell command in a CI job — which, for most teams building products today, is the more natural shape. And on Ubuntu specifically, you keep `apt`'s and `systemd`'s own vocabularies intact rather than flattening them through a lowest-common-denominator module API.

## What Reemote is not

- **Not a deployment CLI.** There is no `reemote` command; you write the script.
- **Not an inventory format.** Inventory is a Python list of dicts — load it from JSON, YAML, a database, or write it inline.
- **Not a role ecosystem.** Reemote ships modules for `apt`, `systemd`, `apk`, `openrc`, `dnf`, `files`, `server`, `git`, `mysql`, `postgresql`, `iptables`, `firewalld`, `ufw`, `selinux`, `lxd`, `snap`, `flatpak`, `sysctl`, `gpg`, and `hardware` — but there is no Ansible Galaxy equivalent.
- **Not a translation layer between OSes.** Python does not make `nginx` installable by the same name on Ubuntu and Alpine. You still pick the right module for the target — and on Ubuntu that means `apt` and `systemd`, which reemote ships as first-class citizens.

## Try it

- Documentation and full guide: <https://reemote.org/>
- Source: <https://github.com/kimjarvis/reemote>
- PyPI: `pip install reemote`
- License: EPL-2.0

If you have ever wanted to describe an Ubuntu box's state in the same language as the rest of your stack — with imports, types, tests, and `await`, and without translating `apt`'s and `systemd`'s vocabularies through YAML — the docker quickstart at the top of this article is the whole onboarding. Give it a minute.
