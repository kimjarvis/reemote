# Managing Alpine Linux from Python: Reemote's `apk` and `openrc` modules

*Ansible manages Alpine through a CLI, a YAML playbook, and a Python-bootstrap dance the target never asked for. Reemote is a Python **library** that configures Alpine over a plain shell — `apk` and OpenRC included — and hands you the result as data you can `await`, inspect, and test. The whole thing runs in a throwaway container in under a minute.*

## The problem: configuration management on Alpine

Alpine runs the world's containers, a lot of its edge boxes, and every minimal VM someone cared enough to slim down. It is not Debian and not RHEL, and three things make it its own animal:

- **apk** (Alpine Package Keeper), not `apt` or `dnf`.
- **OpenRC**, not systemd. A service is an `/etc/init.d` script managed at runtime with `rc-service`; a *runlevel* is a directory of symlinks under `/etc/runlevels` managed with `rc-update`.
- **musl libc** and a deliberately minimal userland — a fresh Alpine ships **no Python at all**.

That last point is where Ansible hurts first. Ansible executes by pushing modules to a **Python interpreter on the managed node**. Every mainstream distro already has one; Alpine does not. So the very first task of every Alpine playbook is a bootstrap you would never need anywhere else — the one module that does *not* require Python, `raw`, sneaking an interpreter onto the box so Ansible can then talk to it:

```yaml
- name: Install Python 3 on Alpine          # a whole task just to make Ansible work
  ansible.builtin.raw: test -x /usr/bin/python3 || apk add --no-cache python3
```

Everything after that is a translation layer. `community.general.apk` for packages. `ansible.builtin.service` quietly becoming `rc-service`/`rc-update` behind your back. `lineinfile` edits to `/etc/apk/repositories`. It works — but you are describing a Python program's worth of logic in YAML, for a target you first had to bootstrap into having Python, driven by a CLI whose result is terminal output you scrape with a regex.

That is the gap: **there is no serious configuration-management library you can `import` and point at an Alpine box.**

## Try it in under a minute (docker connector)

Reemote ships a **docker connector** that runs your deploy inside a local container, so you need neither a remote host nor an SSH key to see it work. **You do need Docker or Podman installed locally** — the connector shells out to whichever CLI you name, and there is no default. Three commands:

```bash
# 1. install reemote into a fresh venv (Python 3.13+; pip works just as well as uv)
uv venv .venv && source .venv/bin/activate && uv pip install reemote
# 2. save the script below as alpine_demo.py
# 3. run it — starts a throwaway alpine:3.20 container, configures it, cleans up
python alpine_demo.py
```

```python
# alpine_demo.py
import asyncio
from reemote import apk, format_log, format_plan, op, run

INVENTORY = [{
    "host": "alpine-demo",
    "connection": {
        "connector": "docker",
        "identifier": "alpine:3.20",
        "docker_cmd": "podman",   # or "docker"
    },
}]

def deploy(host):
    # The first thing you always do on a fresh Alpine: install packages.
    yield op(apk.packages,
             name="Install baseline packages",
             packages=("chrony", "curl"),
             no_cache=True)

async def main():
    plans, report, _ = await run(INVENTORY, deploy)
    print("== Plan ==")
    print(format_plan(plans))
    print("== Applied ==")
    if report is not None:
        print(format_log(report))

asyncio.run(main())
```

The most common first task after dropping onto a fresh Alpine is the one Ansible itself reaches for first: **install packages with `apk`**. Verified output:

```text
== Plan ==
alpine-demo: Install baseline packages [changed: 2 package(s) to install]
== Applied ==
alpine-demo: Install baseline packages [changed: 2 package(s) to install]
    $ apk add --no-cache chrony curl
```

Three things worth noticing, because they are exactly where Alpine + Reemote differ from Alpine + Ansible:

- **No Python bootstrap.** Reemote talks to a **shell** over `sshd` (or `docker exec`), not to a Python interpreter on the target. The `raw: apk add python3` task simply does not exist here. Under the hood that `$ apk add ...` line ran as `podman exec -i <container> sh -c 'apk add --no-cache chrony curl'` — the transport wrapping is invisible in the log by design, so the same deploy reads identically over SSH.
- **No `-y`, no `sudo`.** apk never prompts, so reemote never adds a `-y` flag (unlike apt and dnf). And the container's exec user is already root, so nothing needed escalating. On a real Alpine host — which ships **no `sudo`** — you would flag the request `doas=True` and reemote would run a passwordless `doas -n apk add ...`.
- **`no_cache=True` is an apk-native modifier.** It renders `--no-cache`, the container-idiomatic "don't leave an index behind" flag. Reemote exposes apk's own vocabulary rather than flattening it into a lowest-common-denominator package API.

One honesty note, the same one the throwaway-container workflow always carries: every run reports `[changed: ...]`, even the second time, because the image target starts a *fresh* `alpine:3.20` container on each invocation, so the packages genuinely are absent each time. Point reemote at a stable target — an SSH host, or an existing container ID — and the second run correctly reports `[no change: all packages present]`. You will see exactly that below.

## Why Python has real advantages over Ansible

Ansible chose YAML because it is readable and does not require knowing a programming language. That is a fair trade when your team does not write Python. When it does — and most automation teams today do — the trade costs more than it saves, and on Alpine the bill is higher still:

- **The target does not need Python.** Ansible's whole execution model assumes an interpreter on the managed node. Reemote assumes a POSIX shell and `sshd`. On a minimal Alpine that is the difference between a bootstrap task and none.
- **Your editor knows your code.** `apk.packages` and `openrc.service` are functions: go-to-definition, rename, autocomplete, and a type check that catches a typo before it reaches a production box. A misspelled YAML module name surfaces at runtime.
- **No Jinja2 as a second language.** `{{ hostvars[...] }}` is a stringly-typed escape hatch that fights your IDE and your tests. In reemote the value is just a Python expression.
- **Composition is `import`, not directory layout.** Roles load by convention; reemote deploys are generators you import, call, and loop over.
- **The result is data, not stdout.** If your application needs to know what changed, you read `report.entries[i].changed` — you do not regex-scrape a terminal.
- **Testing is `pytest`.** A deploy function is a pure generator; you can inspect the requests it yields without touching a server. No molecule, no testinfra, no ansible-lint.

## The solution: Reemote is a library

Reemote is a Python package (`pip install reemote`) with two properties that separate it from every CLI-shaped tool.

### Async end to end, on asyncssh

The transport is [asyncssh](https://asyncssh.readthedocs.io/), and the entire execution path — connecting, gathering facts, sending commands, closing — is coroutines. You `await` a deploy and hosts progress in parallel; network waits overlap across hosts while each host's commands stay in order. Your deploy script remains a plain synchronous generator, so you never write `async` inside an operation.

```python
plans, report, hosts = await run(inventory, deploy)
```

`run` is the single entry point: it validates the inventory, opens connections lazily, plans and applies in two phases, then closes everything in a `finally`. The **same code** runs over SSH or over the docker connector — you pick the transport in the inventory entry, not in the deploy. That is why you can develop an Alpine role against a local container and ship it to a VM without changing a line.

### A library returning structured results, not a CLI

`run(...)` returns `(plans, report, hosts)`:

- **`plans`** — `{host_label: [PlannedOperation, ...]}`, the proposed work per host.
- **`report`** — an `ApplyReport` with a `LogEntry` per applied operation, each carrying `changed`, `reason`, and the exact commands sent. `None` when `dry=True`.
- **`hosts`** — the materialised host objects.

`format_plan` and `format_log` (what you saw above) are convenience renderers for humans. Your application consumes the objects directly — no parsing stdout to figure out what happened.

## The `apk` module: Alpine's package manager as Python

`apk` pairs read-only **facts** with **operations** that converge the box toward a described state. An operation reads a fact, decides whether anything must change, and yields commands only when it must — so re-running is a no-op.

**Operations**

- **`apk.packages`** — install, upgrade, or remove by name. A name can be pinned `<pkg>=<version>`, and a pin counts as present only when that *exact* version is installed. `present=False` removes; `latest=True` upgrades unpinned names; `no_cache`/`no_scripts` are the apk-native `--no-cache`/`--no-scripts` modifiers. Idempotence reads `ApkInstalledPackages`.
- **`apk.repository`** — ensure one line in `/etc/apk/repositories`. This is the day-one "enable the community repo" task, done as a **spool-and-install rewrite** of the file (never an in-place `sed -i`), so the other lines, comments, and ordering survive verbatim. A `path` parameter manages a non-standard repositories file.
- **`apk.virtual`** — manage an apk *virtual package* group (`apk add -t <tag> ...`), the build-dependency pattern a compile step wraps itself in so its toolchain does not linger on the finished image; tear it down with one `apk del <tag>`.
- **`apk.hold`** — pin packages back from upgrades. apk has no `apt-mark hold`; it holds by writing a version *constraint* into `/etc/apk/world`, and that is exactly what this does, with version-aware idempotence.
- **`apk.autoremove`** — purge the orphaned packages nothing in world needs any more (`apk add --purge`). apk has no `autoremove` verb, so reemote splits the `--purge` flag into a standalone, separately-plannable step.

**Facts** — `ApkInstalledPackages`, `ApkExplicitPackages` (the world), `ApkHeldPackages`, `ApkAutoremovablePackages`, `ApkRepositories`, `ApkRepositoriesFile`, and `ApkPackage` (one package's version, size, dependencies, provides). Every list fact returns sorted, de-duplicated, JSON-printable typed entries — not a scraped string.

## The `openrc` module: Alpine's init as Python

OpenRC manages services along **two independent axes**: whether a service is *running* right now (`rc-service`), and whether it is *enabled* in a runlevel at boot (`rc-update`). A service can be enabled but stopped, or running but not enabled. `openrc.service` handles both in one operation.

**Operations**

- **`openrc.service`** — `running=True/False/None` compares `OpenrcStatus` and starts/stops only on a difference; `enabled=True/False/None` compares `OpenrcEnabled` for a `runlevel` (default `"default"`) and yields `rc-update add`/`del` only on a difference; `restarted=True`/`reloaded=True` are the non-idempotent verbs, each gated on the service actually running.
- **`openrc.runlevel`** — the dedicated runlevel-membership verb: assign or unassign a service *without* touching its runtime state.
- **`openrc.custom_runlevel`** — manage the runlevel directories themselves, including **stacking** (`rc-update -s add`). Removal unstacks from every target first, then deletes the directory, so no dangling symlink survives.
- **`openrc.config`** — set or unset one shell-variable assignment in `/etc/rc.conf` or `/etc/conf.d/<service>`, via the same spool-and-install rewrite as `apk.repository`.

**Facts** — `OpenrcStatus` (name → running bool), `OpenrcEnabled` (name → enabled-in-runlevel bool), `OpenrcServices` (the *raw* state word, so a `crashed` service is distinguishable from a `stopped` one), `OpenrcRunlevels`, `OpenrcVersion`, and `OpenrcConfigFile`.

One honest boundary: there is **no `openrc.mask`**. OpenRC has no masking mechanism (no systemd-style symlink-to-`/dev/null`), so reemote does not pretend to emulate one — disable the service in every runlevel instead.

### Install OpenRC, then use it — self-contained and verified

The classic Alpine post-install move is *install the daemon, then enable and start it* — chrony for time sync, say. In reemote that is `apk.packages` followed by `openrc.service`. There is one wrinkle worth understanding first, because it is the two-phase model showing through:

**You cannot install OpenRC and read OpenRC facts in the same deploy.** `run()` gathers facts for *every* operation during the prepare pass, before the apply pass sends a single command. So a one-deploy `apk.packages(("openrc", …))` → `openrc.service(…)` dies at plan time: `openrc.service` runs `rc-status` / `rc-update show -v` while OpenRC is not installed yet → exit 127 → `CommandNotFoundError`.

The fix is two `run()` calls — and the docker connector keeps it **fully self-contained**: no manual `podman run`, no custom Dockerfile. Run 1 installs OpenRC into a throwaway `alpine:3.20` container; on close the connector **commits that container into a new image** and hands you its short ID as `hosts[0].image_id`. Run 2 targets that committed image, so OpenRC already exists when its prepare pass reads the facts:

```python
# alpine_openrc.py
import asyncio
from reemote import apk, format_log, format_plan, openrc, op, run

DOCKER_CMD = "podman"   # or "docker"


def conn(identifier):
    return {"host": "alpine-demo",
            "connection": {"connector": "docker",
                           "identifier": identifier,
                           "docker_cmd": DOCKER_CMD}}


def bootstrap(host):
    # Run 1 -- install OpenRC itself. apk is in the stock image, so this
    # deploy's prepare stage gathers facts fine. On close the docker connector
    # commits the container into a new image, OpenRC baked in.
    yield op(apk.packages, name="Install openrc",
             packages=("openrc",), no_cache=True)


def deploy(host):
    # Run 2 -- targets the committed image, so OpenRC already exists and this
    # prepare stage can read rc-status/rc-update without a CommandNotFoundError.
    yield op(apk.packages, name="Install chrony",
             packages=("chrony",), no_cache=True)
    yield op(openrc.service, name="Enable chronyd at boot",
             service="chronyd", running=None, enabled=True)


async def main():
    # Run 1: bootstrap OpenRC into a throwaway alpine:3.20 container.
    plans, report, hosts = await run([conn("alpine:3.20")], bootstrap)
    print("== Run 1: install OpenRC ==")
    print(format_plan(plans))
    if report is not None:
        print(format_log(report))

    # Chain: run 2 starts from the image run 1 committed.
    image_id = hosts[0].image_id
    print(f"(committed image: {image_id})")
    plans, report, _ = await run([conn(image_id)], deploy)
    print("== Run 2: install chrony + enable chronyd ==")
    print(format_plan(plans))
    if report is not None:
        print(format_log(report))

asyncio.run(main())
```

Verified output (the committed image ID is ephemeral — yours will differ):

```text
== Run 1: install OpenRC ==
alpine-demo: Install openrc           [changed: 1 package(s) to install]
alpine-demo: Install openrc           [changed: 1 package(s) to install]
    $ apk add --no-cache openrc
(committed image: 37a2428674df)
== Run 2: install chrony + enable chronyd ==
alpine-demo: Install chrony           [changed: 1 package(s) to install]
alpine-demo: Enable chronyd at boot   [changed: service chronyd is not enabled, will enable]
alpine-demo: Install chrony           [changed: 1 package(s) to install]
    $ apk add --no-cache chrony
alpine-demo: Enable chronyd at boot   [changed: service chronyd is not enabled, will enable]
    $ rc-update add chronyd default
```

Each operation prints twice — once from `format_plan` (the proposal), once from the `format_log` apply report (with the `$ …` commands actually sent). Within run 2 the ordering is the point: the apply phase runs in **lockstep**, one request index at a time across all hosts, so every host finishes the `chrony` install before any host begins the service step. `openrc.service` therefore always sees the `/etc/init.d/chronyd` script the install just delivered. There is no combined "ensure" operation to maintain — the halves compose, and each is idempotent against its own facts.

A throwaway image target starts fresh every time, so re-running the whole script always reports `[changed: …]`. Point run 2 at a **stable** target instead — an SSH host, or an existing container ID — and re-running it converges, which is the `[no change: …]` the quickstart could not show:

```text
alpine-demo: Install chrony           [no change: all packages present]
alpine-demo: Enable chronyd at boot   [no change: service chronyd is enabled]
```

Two Alpine-specific honesty notes:

- **The stock `alpine` image ships no OpenRC** — `rc-service`/`rc-update` are absent until you `apk add openrc`, which is exactly what run 1 does. On a real Alpine VM or bare-metal box OpenRC *is* the init system and is always present, so there you skip the bootstrap run and keep only run 2.
- **`running=True` (an actual `rc-service chronyd start`) needs OpenRC to be PID 1**, which a plain `docker run ... tail -f /dev/null` container is not. That is why this example uses `running=None, enabled=True` — the boot-assignment half (`rc-update`) is init-independent and works anywhere. On a real Alpine host, add `running=True` and reemote will start the daemon too.

On a real host both halves write root-owned state, so flag them `doas=True`: Alpine has no `sudo`, and reemote runs `doas -n`, which needs a `permit nopass` rule for your login user in `/etc/doas.conf`.

## A day-one Alpine playbook, in Python

Here is the routine post-install set — the tasks an Alpine playbook always opens with — as one reemote deploy. No Python bootstrap, no YAML, no Jinja2:

```python
def deploy(host):
    # (1) There is no "install Python so the tool works" step: reemote needs
    #     only a shell + sshd on the target.

    # (2) Enable the community repository (spool-and-install rewrite of the file).
    yield op(apk.repository, name="Enable community repo",
             src="http://dl-cdn.alpinelinux.org/alpine/v3.20/community",
             doas=True)

    # (3) Time sync: install chrony, then enable and start chronyd.
    yield op(apk.packages, name="Install chrony",
             packages=("chrony",), doas=True)
    yield op(openrc.service, name="Run chronyd", service="chronyd",
             running=True, enabled=True, doas=True)

    # (5) Baseline daemons every Alpine box wants up and enabled.
    for svc in ("crond", "networking"):
        yield op(openrc.service, name=f"Ensure {svc}", service=svc,
                 running=True, enabled=True, doas=True)

    # (6) Resolvers: a plain content install, mode enforced.
    yield op(files.install, name="Write resolv.conf",
             content="nameserver 1.1.1.1\nnameserver 8.8.8.8\n",
             dest="/etc/resolv.conf", mode="0644", doas=True)
```

Task **(4), hardening `sshd_config`**, is the one that shows off the library shape most. In Ansible it is a `lineinfile` plus a `notify: restart sshd` handler. In reemote it is `files.edit` (or `files.transform`) rewriting the `PermitRootLogin` line, and the "handler" is ordinary Python: run the config deploy, read its `report`, freeze the set of hosts that actually changed with `reemote.handlers.changed_hosts`, and feed that set into a second deploy that calls `openrc.service("sshd", restarted=True)`. No `notify` keyword, no handler-ordering rules to memorise — just the structured result you already have.

## What follows: Reemote can manage Alpine instead of Ansible

Once configuration management is a library, everything Python already has becomes available to your Alpine deploys:

- **Deploy from your application.** `await run(inventory, deploy)` inside a FastAPI handler, a Celery task, a Python GitHub Action, or a REPL.
- **Compose with real code.** Build the inventory from a database query; generate config text from a Pydantic model; share validation between the app and the deploy.
- **Test with `pytest`.** Deploy functions are pure generators — assert on the requests they yield without a server in sight.
- **Consume structured results.** `report.entries[i].changed`, `.reason`, `.commands` are data you can log, chart, feed to a webhook, or gate a rollout on.
- **Two-phase execution for free.** Every `run` prepares then applies; `dry=True` is a real preview against live facts, not a simulation.
- **Same code over SSH or Docker.** Flip the `connector` key and the identical deploy runs against an Alpine VM or a local container — test the role before you point it at production, or build an image from the same code that configures a host.
- **Handlers as ordinary Python.** Read the first deploy's report, freeze the changed hosts, feed them into a second deploy. `changed_hosts` and `restart_handler` ship for the common case.

You can use reemote instead of Ansible whenever managing Alpine is part of a Python program rather than a shell command in a CI job — which, for most teams building products today, is the more natural shape. And on Alpine specifically, you drop the Python-bootstrap task entirely.

## What Reemote is not

- **Not a deployment CLI.** There is no `reemote` command; you write the script.
- **Not an inventory format.** Inventory is a Python list of dicts — load it from JSON, YAML, a database, or write it inline.
- **Not a role ecosystem.** Reemote ships modules for `apk`, `openrc`, `apt`, `dnf`, `files`, `systemd`, `server`, `git`, `mysql`, `postgresql`, `iptables`, `firewalld`, `ufw`, `selinux`, `lxd`, `snap`, `flatpak`, `sysctl`, `gpg`, and `hardware` — but there is no Ansible Galaxy equivalent.
- **Not a translation layer between OSes.** Python does not make `nginx` installable by the same name on Alpine and Debian. You still pick the right module for the target — and on Alpine that means `apk` and `openrc`, which reemote ships as first-class citizens.

## Try it

- Documentation and full guide: <https://reemote.org/>
- Source: <https://github.com/kimjarvis/reemote>
- PyPI: `pip install reemote`
- License: EPL-2.0

If you have ever wanted to describe an Alpine box's state in the same language as the rest of your stack — with imports, types, tests, and `await`, and without bootstrapping Python onto the target just to configure it — the docker quickstart at the top of this article is the whole onboarding. Give it a minute.
