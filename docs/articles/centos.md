# Managing CentOS from Python: Reemote's `dnf` and `systemd` modules

*Ansible manages CentOS through a CLI, a YAML playbook, and a Jinja2 layer that fights your IDE. Reemote is a Python **library** that configures CentOS over `sshd` and a plain shell — `dnf` and `systemd` included — and hands you the result as data you can `await`, inspect, and test. The whole thing runs in a throwaway container in under a minute.*

## The problem: configuration management on CentOS

CentOS — and the RHEL family it anchors: Rocky, Alma, Fedora Server — runs a huge share of enterprise fleets, a lot of cPanel/hosting infrastructure, and most on-prem boxes that were "standardised on Red Hat" a decade ago and never moved. Its shape is well known and Ansible handles it cleanly, but the pain is not "can the tool talk to the box". It is everything a CLI-shaped tool costs you once you want to embed deployment in a Python program.

That is the gap: **there is no serious configuration-management library you can `import` and point at a CentOS box.** Reemote is that library, and the RHEL family is where its `dnf` namespace feels most at home — application streams, repo files, and rpm's GPG keyring all ship as first-class facts and operations, alongside the same two-phase prepare-then-apply runner everything else uses.

## Try it in under a minute (docker connector)

Reemote ships a **docker connector** that runs your deploy inside a local container, so you need neither a remote host nor an SSH key to see it work. **You do need Docker or Podman installed locally** — the connector shells out to whichever CLI you name, and there is no default. Three commands:

```bash
# 1. install reemote into a fresh venv (Python 3.13+; pip works just as well as uv)
uv venv .venv && source .venv/bin/activate && uv pip install reemote
# 2. save the script below as centos_demo.py
# 3. run it — starts a throwaway CentOS Stream 9 container, configures it, cleans up
python centos_demo.py
```

```python
# centos_demo.py
import asyncio
from reemote import dnf, format_log, format_plan, op, run

INVENTORY = [{
    "host": "centos-demo",
    "connection": {
        "connector": "docker",
        "identifier": "quay.io/centos/centos:stream9",
        "docker_cmd": "podman",   # or "docker"
    },
}]

def deploy(host):
    # The most common first task after dropping onto a fresh CentOS server:
    # install the web server the RHEL family ships as its default — httpd.
    yield op(dnf.packages,
             name="Install httpd",
             packages=("httpd",))

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
centos-demo: Install httpd [changed: 1 package(s) to install]
== Applied ==
centos-demo: Install httpd [changed: 1 package(s) to install]
    $ dnf install -y httpd
```

Three things worth noticing, because they are exactly where CentOS + Reemote differ from CentOS + Ansible:

- **No YAML, no Jinja2, no `ansible.builtin.dnf`.** `dnf.packages` is a Python function your editor knows: go-to-definition, autocomplete, type check. The `packages=` argument is a tuple because operation kwargs must stay hashable — the same determinism rule that lets reemote run your deploy twice (once to plan, once to apply) and prove both passes agree.
- **No `-y` to remember.** The dnf namespace knows its own non-interactive vocabulary and renders `dnf install -y httpd` for you. Reemote exposes dnf's own idioms rather than flattening them into a lowest-common-denominator package API — the same shape `apt.packages` takes on Ubuntu and `apk.no_cache` on Alpine.
- **A full-system upgrade is a different word here.** On Ubuntu `apt-get update` just refreshes the package lists; on CentOS `dnf update` *upgrades every package*. That is why this quickstart installs one package and stops. To refresh metadata without upgrading, use `dnf.makecache`; to actually patch the box, use `dnf.update` — reemote spells each one explicitly rather than collapsing them into one ambiguous `update=` flag.

One honesty note, the same one the throwaway-container workflow always carries: every run reports `[changed: ...]`, even the second time, because the image target starts a *fresh* `centos:stream9` container on each invocation, so httpd genuinely is absent each time. Point reemote at a stable target — an SSH host, or an existing container ID — and the second run correctly reports `[no change: all packages present]`. You will see exactly that below.

## Why Python has real advantages over Ansible

Ansible chose YAML because it is readable and does not require knowing a programming language. That is a fair trade when your team does not write Python. When it does — and most automation teams today do — the trade costs more than it saves:

- **Your editor knows your code.** `dnf.packages` and `systemd.service` are functions: go-to-definition, rename, autocomplete, and a type check that catches a typo before it reaches a production box. A misspelled YAML module name surfaces at runtime, in the middle of a rollout.
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

`run` is the single entry point: it validates the inventory, opens connections lazily, plans and applies in two phases, then closes everything in a `finally`. The **same code** runs over SSH or over the docker connector — you pick the transport in the inventory entry, not in the deploy. That is why you can develop a CentOS role against a local container and ship it to a VM without changing a line.

### A library returning structured results, not a CLI

`run(...)` returns `(plans, report, hosts)`:

- **`plans`** — `{host_label: [PlannedOperation, ...]}`, the proposed work per host.
- **`report`** — an `ApplyReport` with a `LogEntry` per applied operation, each carrying `changed`, `reason`, and the exact commands sent. `None` when `dry=True`.
- **`hosts`** — the materialised host objects.

`format_plan` and `format_log` (what you saw above) are convenience renderers for humans. Your application consumes the objects directly — no parsing stdout to figure out what happened, no `--check` mode plus a callback plugin to reconstruct the plan after the fact.

## The `dnf` module: CentOS's package manager as Python

`dnf` pairs read-only **facts** with **operations** that converge the box toward a described state. An operation reads a fact, decides whether anything must change, and yields commands only when it must — so re-running against a stable target is a no-op. The non-idempotent verbs (`update`, `makecache`, `clean`, and the key-import direction) are the deliberate exceptions, and each is documented as such.

The **operations** cover the whole package lifecycle, in dnf's own vocabulary rather than a lowest-common-denominator one: install/upgrade/remove by name (`dnf.packages`), a full-system upgrade (`dnf.update`), metadata refresh and cache cleanup (`dnf.makecache`, `dnf.clean`), repository-file management (`dnf.repo`), GPG-key import and removal on the rpm keyring (`dnf.key`), local `.rpm` file installs (`dnf.rpm`), and the RHEL-only application/module streams (`dnf.module`). Most read a fact and yield commands only when the box differs from the request, so re-running against a stable target is a no-op; a few verbs — `update`, `makecache`, `clean`, and the key-import direction — are deliberately non-idempotent, because there is no cheap fact that predicts whether they would change anything.

The **facts** are the read side of the same vocabulary: which packages (and which versions) are installed, which repositories are configured and what their `.repo` files contain, which module streams are enabled/disabled/installed, which package groups exist, the version of a local `.rpm`, and which GPG keys sit in the rpm keyring. Every enumerating fact returns sorted, de-duplicated, JSON-printable typed entries — not a scraped string — so a read is data you can diff, log, or assert on.

## The `systemd` module: CentOS's init as Python

systemd manages services along **two independent axes**: whether a unit is *running* right now, and whether it is *enabled* at boot. A unit can be enabled but stopped, or running but not enabled. `systemd.service` handles both in one operation, and the namespace goes well beyond services — drop-ins, timers, tmpfiles, sysusers, masking, presets. It is the same module Ubuntu uses; the RHEL family is where systemd was born, so nothing here is a portability compromise.

The **operations** center on `systemd.service` — one call that manages a unit along both axes (`running` and `enabled`) plus the restart/reload verbs — and extend to the surrounding unit-file machinery: an explicit `daemon_reload`, drop-ins, `tmpfiles.d`/`sysusers.d` config files, masking, presets, and clearing a failed state. The state-changing verbs read a fact first and yield a `systemctl` command only on a difference, so they are idempotent; restart, reload, and daemon-reload are the intentional non-idempotent exceptions.

The **facts** answer both the per-unit questions (is it running? enabled? what are its properties, dependencies, or unit-file path?) and the whole-system audit questions (what is failed? what timers are scheduled? what is the system state? which systemd version?). Several are reemote originals that expose the audit view the baseline never surfaced, and every `systemctl`-based fact and operation takes scope flags for the per-user manager or a named machine.

### Install a package, then ensure its service — verified

The classic CentOS post-install move is *install the daemon, then enable and start it* — httpd for a web tier. In reemote that is `dnf.packages` followed by `systemd.service`, composed in one deploy:

```python
def deploy(host):
    # 1. Install httpd (ships /usr/lib/systemd/system/httpd.service).
    yield op(dnf.packages,
             name="Install httpd",
             packages=("httpd",),
             sudo=True)
    # 2. Enable and start its systemd unit.
    yield op(systemd.service,
             name="Run httpd",
             service="httpd",
             running=True,
             enabled=True,
             sudo=True)
```

The ordering is the point. The apply phase runs in **lockstep** — one request index at a time across all hosts — so every host finishes the install before any host begins the service step. The `systemd.service` request therefore always sees the `httpd.service` unit file the install just delivered. There is no combined "ensure" operation to maintain; the two halves compose, and each is idempotent against its own facts.

First run against a CentOS host with systemd as PID 1:

```text
== Plan ==
centos-demo: Install httpd [changed: 1 package(s) to install]
centos-demo: Run httpd     [changed: httpd.service not running (inactive), will start; httpd.service not enabled, will enable]
== Applied ==
centos-demo: Install httpd [changed: 1 package(s) to install]
    $ sudo dnf install -y httpd
centos-demo: Run httpd     [changed: httpd.service not running (inactive), will start; httpd.service not enabled, will enable]
    $ sudo systemctl start httpd.service
    $ sudo systemctl enable httpd.service
```

Run the identical script again against the *same* host and it converges — this is the `[no change: ...]` the throwaway quickstart could not show:

```text
== Plan ==
centos-demo: Install httpd [no change: all packages present]
centos-demo: Run httpd     [no change: httpd.service already running; httpd.service already enabled]
== Applied ==
centos-demo: Install httpd [no change: all packages present]
centos-demo: Run httpd     [no change: httpd.service already running; httpd.service already enabled]
```

Two CentOS-specific honesty notes:

- **The stock `centos:stream9` container image does not run systemd as PID 1** — `systemctl start`/`enable` refuse to operate with "System has not been booted with systemd as init system". The quickstart at the top deliberately sticks to `dnf.packages` for exactly that reason. To exercise the systemd half locally, use a systemd-capable image or your own `docker run --privileged ... /sbin/init` recipe; on a real CentOS VM or bare-metal host systemd is PID 1 and everything above just works.
- **`sudo` is uniform on CentOS.** The RHEL family ships `sudo` preinstalled, so `sudo=True` on a request is the whole elevation story. Reemote renders `sudo <command>` on the descriptor and never needs a password prompt: use passwordless sudoers, or an SSH key for a login user with `NOPASSWD`, or `su=True` if you would rather reemote escalate through `su` (root logins are common on legacy CentOS boxes).

## A day-one CentOS playbook, in Python

Here is the routine post-install set — the tasks a CentOS playbook always opens with — as one reemote deploy. No YAML, no Jinja2, no handlers section:

```python
def deploy(host):
    # (1) Patch the box. On the RHEL family `dnf update` is a full-system
    #     upgrade — the literal "yum update" every CentOS admin runs first.
    yield op(dnf.update, name="Apply pending updates", sudo=True)

    # (2) Baseline tooling every CentOS server wants.
    yield op(dnf.packages, name="Install baseline tools",
             packages=("curl", "git", "htop"),
             sudo=True)

    # (3) Web tier: install httpd, then enable and start it.
    yield op(dnf.packages, name="Install httpd",
             packages=("httpd",), sudo=True)
    yield op(systemd.service, name="Run httpd", service="httpd",
             running=True, enabled=True, sudo=True)

    # (4) Time sync — chrony is the RHEL-family default NTP client, and its
    #     unit is chronyd (not Debian's chrony).
    yield op(dnf.packages, name="Install chrony",
             packages=("chrony",), sudo=True)
    yield op(systemd.service, name="Run chronyd", service="chronyd",
             running=True, enabled=True, sudo=True)

    # (5) Application streams — a RHEL-only concept: pin nginx to the 1.24
    #     stream so a later `dnf install nginx` resolves that version.
    yield op(dnf.module, name="Enable nginx:1.24 stream",
             module="nginx", stream="1.24", sudo=True)

    # (6) Hardening: a drop-in that tightens sshd, then a reload — the
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

Task **(6)** is the one that shows off the library shape most. In Ansible the drop-in is a `template` plus a `notify: restart sshd` handler, and the handler fires only if the template reported `changed`. In reemote the "handler" is ordinary Python: run the config deploy, read its `report`, freeze the set of hosts that actually changed with `reemote.handlers.changed_hosts`, and feed that set into a second deploy that calls `systemd.service("sshd", restarted=True)`. No `notify` keyword, no handler-ordering rules to memorise — just the structured result you already have.

## What follows: Reemote can manage CentOS instead of Ansible

Once configuration management is a library, everything Python already has becomes available to your CentOS deploys:

- **Deploy from your application.** `await run(inventory, deploy)` inside a FastAPI handler, a Celery task, a Python GitHub Action, or a REPL.
- **Compose with real code.** Build the inventory from a database query; generate `.repo` file text or `daemon.json` from a Pydantic model; share validation between the app and the deploy.
- **Test with `pytest`.** Deploy functions are pure generators — assert on the requests they yield without a server in sight.
- **Consume structured results.** `report.entries[i].changed`, `.reason`, `.commands` are data you can log, chart, feed to a webhook, or gate a rollout on.
- **Two-phase execution for free.** Every `run` prepares then applies; `dry=True` is a real preview against live facts, not a simulation.
- **Same code over SSH or Docker.** Flip the `connector` key and the identical deploy runs against a CentOS VM or a local container — test the role before you point it at production, or build an image from the same code that configures a host.
- **Handlers as ordinary Python.** Read the first deploy's report, freeze the changed hosts, feed them into a second deploy. `changed_hosts` and `restart_handler` ship for the common case.

You can use reemote instead of Ansible whenever managing CentOS is part of a Python program rather than a shell command in a CI job — which, for most teams building products today, is the more natural shape. And on the RHEL family specifically, you keep `dnf`'s own vocabulary intact — module streams, repo files, the rpm keyring — rather than flattening it through a lowest-common-denominator module API.

## What Reemote is not

- **Not a deployment CLI.** There is no `reemote` command; you write the script.
- **Not an inventory format.** Inventory is a Python list of dicts — load it from JSON, YAML, a database, or write it inline.
- **Not a role ecosystem.** Reemote ships modules for `dnf`, `systemd`, `apt`, `apk`, `openrc`, `files`, `server`, `git`, `mysql`, `postgresql`, `iptables`, `firewalld`, `ufw`, `selinux`, `lxd`, `snap`, `flatpak`, `sysctl`, `gpg`, and `hardware` — but there is no Ansible Galaxy equivalent.
- **Not a target-side downloader.** Reemote fetches URLs *control-side*: `files.download` pulls an `http(s)` body (optionally checksum-verified) onto the control machine and installs it at a root-owned path, so a host with no internet egress still receives the file. No operation makes the *target* fetch a URL mid-run — `dnf.repo`, `dnf.rpm`, and `dnf.key` all reject a URL `src` by design; for a binary a host needs, fetch control-side and stream the bytes in.
- **Not a translation layer between OSes.** Python does not make `httpd` installable by the same name on CentOS and Ubuntu, nor make `dnf update` mean the same as `apt-get update`. You still pick the right module for the target — and on CentOS that means `dnf` and `systemd`, which reemote ships as first-class citizens.

## Try it

- Documentation and full guide: <https://reemote.org/>
- Source: <https://github.com/kimjarvis/reemote>
- PyPI: `pip install reemote`
- License: EPL-2.0

If you have ever wanted to describe a CentOS box's state in the same language as the rest of your stack — with imports, types, tests, and `await`, and without translating `dnf`'s and `systemd`'s vocabularies through YAML — the docker quickstart at the top of this article is the whole onboarding. Give it a minute.
