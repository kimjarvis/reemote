# Deploying websites with Reemote — from a local container to production

*Reemote turns Python code into shell commands and runs them on many machines over SSH, in parallel. Think Ansible, but there is no CLI and no YAML: reemote is a **library** you `import`, `await`, and test. This walkthrough deploys two websites — a landing page and a Python web app — first on a local target, then on two remote servers, using only Python.*

We will stand up nginx, serve a static landing page, run a small Flask app behind it on a Unix socket, and finish with HTTPS from Let's Encrypt. Everything is ordinary Python: `for` loops, `if` statements, imports, types, and `pytest`.

This is a written walkthrough you can follow along at home. Every output
block labelled **verified** was produced by running the code shown against
a local `ubuntu:24.04` container through reemote's docker connector; the
production-only steps (systemd, Certbot) are marked as such because a plain
container cannot run them, and the article says so rather than implying
otherwise.

## What is reemote?

Reemote is a configuration-management library. You describe the state a machine should be in — packages installed, files present, services running — and reemote works out the shell commands that get it there, then runs them across your fleet concurrently.

A file full of those descriptions is a **deploy**. Here is the whole idea in a few lines:

```python
from reemote import apt, files, op, server

MOTD = "Managed by reemote\n"

def deploy(host):
    for user in ("admin", "alice", "bob"):
        yield op(server.user, user=user)

    yield op(files.install, dest="/etc/motd", content=MOTD, sudo=True)

    if host.extra.get("install_postgres"):
        yield op(apt.packages, packages=("postgresql",), sudo=True)
```

A deploy is a **generator**: it `yield`s one `op(...)` request per action. `op` records the call — it does not run it. The runner drives the generator twice (once to plan, once to apply), which is why every argument must be hashable: `packages=("postgresql",)` is a tuple, not a list. `host.extra` is arbitrary per-host data from the inventory, so the `if` is just Python.

## Why use reemote?

Reemote is **declarative** — you describe the target state, and reemote makes it so — and **idempotent** — nothing happens if the machine is already in that state. Those are the properties that make a deploy safe to re-run.

Unlike a YAML-driven tool, a deploy is Python code, so you get everything Python gives you:

- **Your editor knows your code.** `apt.packages`, `files.install`, and `systemd.service` are functions: go-to-definition, autocomplete, rename, and a type check that catches a typo before it reaches a production box.
- **No second templating language.** There is no `{{ hostvars[...] }}` stringly-typed escape hatch. A value is a Python expression, evaluated by the interpreter you already trust, and config text is rendered control-side with plain Python before you hand it to `files.install`.
- **Composition is `import`.** Roles are generator functions you call, loop over, and `yield from` — not a directory layout loaded by convention.
- **The result is data, not stdout.** `run(...)` returns structured objects; you read `report.log[i].changed` instead of regex-scraping a terminal.
- **Testing is `pytest`.** A deploy is a pure generator; you can assert on the requests it yields without a server in sight.
- **`await` is the concurrency model.** Hosts progress in parallel because the transport is [asyncssh](https://asyncssh.readthedocs.io/), not because a CLI forked workers you have to configure.

In fairness, reemote ships fewer built-in modules than a decade-old tool with a galaxy of roles. That is partly offset by how little code a custom operation takes — an async generator that reads a fact and yields a command, as the Certbot step later in this article shows.

## When to use reemote?

Reemote's use cases follow from what it does: configure machines over SSH (or inside a container). It is a good fit for **boring, cloud-agnostic architectures**, from a fleet of servers with different roles all the way down to one-computer programming, where it still makes a setup reproducible and prevents drift.

It is easy enough to learn that it works well for self-hosting and home servers, and even when you do not want full configuration management, the `Shell` action gives you a convenient way to run ad-hoc commands across a fleet.

Simplicity has edges. There is no built-in pull mode, a fully automated pipeline is your own code around `await run(...)`, and two people deploying at once need coordination. Reemote does **not** provision cloud resources — that is Terraform's job; reemote configures the machines once they exist. Staying "commands over SSH" is a deliberate scope, not a gap.

## Install reemote

Reemote runs on your **controller** — the machine running the Python — and needs **Python 3.13 or newer**. You never install it on the target servers; they only need SSH (or, for this walkthrough, a container runtime).

Install from a git checkout. That is the recommended route here because the clone also gives you the runnable `examples/` tree this article walks through:

```bash
git clone https://github.com/kimjarvis/reemote.git
cd reemote
uv sync
source .venv/bin/activate
```

`uv sync` creates `.venv`, installs reemote in editable mode and resolves its dependencies — `asyncssh`, `distro`, `pydantic` — so the SSH transport needs no separate step. [uv](https://docs.astral.sh/uv/) is what we use; `venv` and `pip` work just as well. If you only want the library and not the examples, `uv pip install reemote` from PyPI is enough.

Every stage below has a complete, runnable script under `examples/tutorials/website/`, so you can read the article and run the code side by side:

| Article section | Example | What it does |
| --- | --- | --- |
| Operations and facts · Where are the commands? | `website_0.py` | The two-phase model: read a fact, create a file |
| Inventory + nginx setup | `website_1.py` | A `Shell` action, then install and configure nginx |
| Static site + code reuse | `website_2.py` | Compose the base with a conditional landing-page site |
| Web app + secrets | `website_3.py` | Service accounts, a custom venv operation, rendered secrets |
| HTTPS + prod | `website_4.py` | A custom Certbot fact and operation, then the whole fleet |

`website_0.py` is the smallest complete program — the "basic part" the rest build on. Run it, and the others, with `uv run` from the checkout root:

```bash
uv run python examples/tutorials/website/website_0.py
```

The examples target a local container named `beta`, so start it once — the walkthrough uses `podman`, and `docker` works identically:

```bash
podman run -d --name beta ubuntu:24.04 tail -f /dev/null
```

Pointing the docker connector at a **container name** rather than an image leaves it running between runs, so its state persists like a VM — which is what makes idempotence visible. `website_4.py` is the exception: it targets two production SSH hosts and Certbot, so it builds and dry-runs offline but is meant to be pointed at your own servers.

## The demo

At the end we will have two websites — a landing page and a Python web app — deployed in two stages.

First a single local target, `beta`, running both behind one nginx:

```text
.------- beta --------.- app.beta --------.
|                    nginx                 |
|    static website   :   Python web app   |
'------------------------------------------'
```

...then two separate production servers, one per site:

```text
.-- web1 (prod) ---.  .-- web2 (prod) ----.
|      nginx       |  |      nginx        |
|  static website  |  |  Python web app   |
'------------------'  '-------------------'
```

Here is a sneak peek at the finished top-level deploy. It composes a shared nginx base with two conditional sub-deploys, then handles reloads as a second pass:

```python
def deploy(host):
    yield from nginx_base(host)                       # apt + disable default site

    if host.extra.get("home_domain"):                 # static landing page
        yield from sub_deploy(home_site, name_prefix="home")(host)

    if host.extra.get("app_domain"):                  # Flask app behind gunicorn
        yield from sub_deploy(app_site, name_prefix="app")(host)
```

The `if`s read `host.extra` — static inventory data — so both phases of the run resolve them identically. That distinction (static data versus live facts) is the single most important rule in reemote, and the next two sections are about it.

## Operations and facts

Two concepts do all the work.

### Operations change the machine

An **operation** ensures some state: a file exists, a package is installed, a service is running. Most are declarative and idempotent. Here is a deploy with one operation that says "I want `/tmp/hello.txt` to exist":

```python
# create_file.py
import asyncio
from reemote import files, format_log, format_plan, op, run

INVENTORY = [{
    "host": "beta",
    "connection": {"connector": "docker", "identifier": "beta", "docker_cmd": "podman"},
}]

def deploy(host):
    yield op(files.file, name="create hello.txt", path="/tmp/hello.txt", present=True)

async def main():
    plans, report, _ = await run(INVENTORY, deploy)
    print("== plan ==")
    print(format_plan(plans))
    if report is not None:
        print("== log ==")
        print(format_log(report))

asyncio.run(main())
```

`beta` here is a local container, so the walkthrough needs no SSH key and no VM. Start it once:

```bash
podman run -d --name beta ubuntu:24.04 tail -f /dev/null
```

Pointing the docker connector at a **container name** (rather than an image) leaves it running between runs, so state persists exactly like a VM. Run the script:

```python
python create_file.py
```

Verified output — the plan says what will change, the log says what did and shows the command:

```text
== plan ==
beta: create hello.txt         [changed: /tmp/hello.txt missing, will create]
== log ==
beta: create hello.txt         [changed: /tmp/hello.txt missing, will create]
    $ touch /tmp/hello.txt
```

Because `files.file` is idempotent, running the identical script again does nothing:

```text
== plan ==
beta: create hello.txt         [no change: file /tmp/hello.txt already exists]
== log ==
beta: create hello.txt         [no change: file /tmp/hello.txt already exists]
```

There is no "press enter to execute" prompt — reemote is a library, not an interactive CLI. To look before you leap, pass `dry=True`: it runs the **prepare** phase only (facts are gathered for real, nothing mutates) and returns `report=None`. Preview with `dry=True`, then call `run` again without it to apply.

### Facts read the machine

A **fact** gathers data about a target. Read one directly across hosts with `gather_fact`:

```python
from reemote import Host, gather_fact, server

hosts = [Host.from_inventory(entry) for entry in INVENTORY]
names = await gather_fact(hosts, server.LinuxName)
```

Verified output:

```text
gather_fact LinuxName -> {'beta': 'Ubuntu'}
```

The natural next step — pick a package name from the distribution — is where reemote differs most from a tool that lets you branch on a fact mid-deploy. **You cannot.** A deploy runs twice (prepare, then apply); if its body branched on a *live fact*, the two passes could diverge, so the runner raises a `DeterminismError` rather than silently applying a plan it did not show you. Static inventory data (`host.name`, `host.group`, `host.tags`, `host.extra`) is identical in both phases and safe to branch on; facts are not.

So fact-driven choice moves **out** of the deploy, into a gather → snapshot → factory shape:

```python
from reemote import apt, gather_fact, op, run, server

# 1. Gather the fact up front, across every host, and freeze it.
names = await gather_fact(hosts, server.LinuxName)

# 2. Build a per-host dispatch table from the snapshot (keyed by host name).
PACKAGE = {"Ubuntu": "apache2", "Debian": "apache2"}

def build_install(name):
    package = PACKAGE.get(name, "httpd")
    def install(host):
        yield op(apt.packages, name="install apache", packages=(package,), sudo=True)
    return install

# 3. Run each host against a deploy chosen from its frozen value — never live.
for entry in INVENTORY:
    await run([entry], build_install(names[entry["host"]]))
```

The branch happens in ordinary Python *before* the deploy exists, so each deploy body is branch-free and deterministic. It is a little more code than an inline `if`, and in exchange the plan reemote shows you is a plan it will actually honour.

## Where are the commands?

An operation is an **async generator** that takes an `OpContext`, reads facts to decide whether anything must change, and yields command descriptors only when it does. This is a simplified `files.file`:

```python
from reemote import OpContext, StringCommand

async def file(ctx: OpContext, path: str, present: bool = True):
    exists = await ctx.facts.get(FileExists, path)

    if not exists and present:
        ctx.changed = True
        ctx.reason = f"{path} missing, will create"
        yield StringCommand(f"touch {path}")

    elif exists and not present:
        ctx.changed = True
        ctx.reason = f"{path} present, will remove"
        yield StringCommand(f"rm -f {path}")
```

The shape is the same for every operation: read state with a fact, diff it against the desired state, set `ctx.changed`/`ctx.reason`, and yield the commands that close the gap. Yield nothing and the operation reports "no change".

Here is a deploy that says "I want `/tmp/hello.txt` to **not** exist":

```python
def deploy(host):
    yield op(files.file, name="delete hello.txt", path="/tmp/hello.txt", present=False)
```

`format_log` renders the command descriptors that were actually sent, which is where the shell text finally appears. Verified output (against the container that still has the file from earlier):

```text
== plan ==
beta: delete hello.txt         [changed: /tmp/hello.txt present, will remove]
== log ==
beta: delete hello.txt         [changed: /tmp/hello.txt present, will remove]
    $ rm -f /tmp/hello.txt
```

The **plan** comes from the prepare phase: reemote runs each operation to see what it *would* yield, gathering facts (read-only commands) but sending nothing that mutates. The **log** comes from the apply phase: the same operations run again, and this time their commands are sent. Apply re-creates the fact cache before every step, so a later operation sees the effect of an earlier one — install a package in step 1 and step 2's package fact reads the newly-installed set.


## Inventory + nginx setup

So far the target has been a local container, but we want `beta` to behave like production. The inventory is just a Python list of dicts — no separate file format to learn. Each entry names a host, its connection, and any extra data:

```python
BETA = [{
    "host": "beta",
    "connection": {"connector": "docker", "identifier": "beta", "docker_cmd": "podman"},
}]

# Production is the same shape over the default ssh connector — the
# "connector" key can be omitted, since ssh is the default:
PROD = [{
    "host": "web1",
    "connection": {"address": "web1.example.com", "username": "deploy",
                   "key_path": "~/.ssh/id_ed25519"},
    "home_domain": "www.example.com", "stage": "prod", "nginx_https": True,
}]
```

Because the inventory is Python, a group does not have to be a literal — build it from a database query or your cloud's SDK (`[i.hostname for i in ...]`). The keys a `Host` does not recognise (`home_domain`, `stage`, `nginx_https`) are preserved verbatim in `host.extra`, which is how per-host data reaches the deploy.

To run an arbitrary command across the fleet — the ad-hoc "what version is this?" move — use the `Shell` **action**. Actions run immediately, outside the deploy model, and hand you the result as data:

```python
from reemote import Host, Shell, gather_action

hosts = [Host.from_inventory(e) for e in BETA]
results = await gather_action(hosts, Shell, command='. /etc/os-release && echo "$PRETTY_NAME"')
```

Verified output:

```text
Shell beta: exit=0 stdout='Ubuntu 24.04.5 LTS'
```

An action's exit status is data, not an exception — even 127 (command not found) comes back in `exit_status` so the caller decides what it means. Actions cannot run *inside* a deploy; `gather_action` raises the moment it is called from a generator.

### Point it at a real server

The `beta` container needs no keys and no network, but the same deploys run against a real machine unchanged — only the inventory entry differs. To add a server you can SSH into, give its entry a `connection` with an `address`, a `username` and a `key_path` instead of the docker connector:

```python
REAL = [{
    "host": "web1",                                  # any name you like
    "connection": {
        "address": "web1.example.com",               # or an IP
        "username": "deploy",                        # the login user
        "key_path": "~/.ssh/id_ed25519",             # its private key
    },
    # Any other keys land in host.extra and drive the conditional deploys:
    "home_domain": "www.example.com",
    "stage": "prod",
}]
```

`connection` keeps asyncssh's own option names, so `port`, `password`, `passphrase`, `proxy_command` and the rest pass straight through; `address`, `username` and `key_path` are friendly aliases for the target, the login name and a single identity key. Add more dictionaries to the list to add more hosts — `run(REAL, deploy)` fans the same deploy across all of them in parallel.

Two things change when you leave the container:

- **Elevation.** The `beta` examples run as the container's `root`, so they omit `sudo`. A real login user is not root, so pass `sudo=True` on each request that writes a system path — exactly as `website_4.py` and the prod snippets here do. Reemote renders `sudo <command>` and never prompts: grant the login user passwordless `sudo`, and set any run-as user or password in the entry's top-level `privilege` block (`sudo_user`, `sudo_password`), never in the operation.
- **Reachability.** Host-key checking is on by default; for a first connection to a new host either pre-seed `~/.ssh/known_hosts` or pass `"known_hosts": None` in `connection` to skip the check.

Swap `INVENTORY` for `REAL` in any example and it deploys to your server instead of `beta` — the generator functions are identical, because a connector only overrides the transport, never the deploy.

### Installing nginx

Both websites run behind nginx, so install it first. This is the shared base every host gets:

```python
from reemote import apt, files, op

def nginx_base(host):
    yield op(apt.update, name="apt update")
    yield op(apt.packages, name="install nginx", packages=("nginx",))
    yield op(files.link, name="disable default site",
             path="/etc/nginx/sites-enabled/default", present=False)
```

Run it against `beta` and the plan and log line up. Verified output, first run:

```text
== plan ==
beta: apt update               [changed: apt update]
beta: install nginx            [changed: 1 package(s) to install]
== log ==
beta: apt update               [changed: apt update]
    $ apt-get update
beta: install nginx            [changed: 1 package(s) to install]
    $ DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install nginx
```

Run it again and it converges — except `apt.update`, which is deliberately stateless and always runs (there is no `cache_time` stamp file, because a timestamp would drift between the prepare and apply phases and break the determinism guard). Verified second run:

```text
== plan ==
beta: apt update               [changed: apt update]
beta: install nginx            [no change: all packages present]
== log ==
beta: apt update               [changed: apt update]
    $ apt-get update
beta: install nginx            [no change: all packages present]
```

Disabling the default "Welcome to nginx!" site removes a symlink, and its plan reports exactly that. Verified output:

```text
== plan ==
beta: disable default site     [changed: /etc/nginx/sites-enabled/default present, will remove]
== log ==
beta: disable default site     [changed: /etc/nginx/sites-enabled/default present, will remove]
    $ rm -f /etc/nginx/sites-enabled/default
```

Notice the ordering worked without any special handling. "Disable the default site" depends on nginx having created that symlink — and it did, because apply runs in **lockstep**, one request index at a time, so the install completes before the link step reads its fact. You might be tempted to gate the link on a fact instead:

```python
# Do NOT do this — it cannot work, and reemote will not let it.
if some_link_fact:            # a live fact read inside the deploy body
    yield op(files.link, ...)
```

That branch reads a fact during **prepare**, when nginx is not installed yet, so the symlink would not exist and the step would be skipped on the very first run. Reemote forecloses the mistake entirely: branching on a live fact in a deploy raises `DeterminismError`. The lockstep apply is what makes "install, then configure what the install created" safe to write as two plain steps in order.

A config change usually needs a **reload**. Reemote has no `notify`/handler keyword; a conditional reload is built from the results of a previous run, in ordinary Python, between two deploys — the **two-deploy split**:

```python
from reemote import changed_hosts, op, run, systemd

# Pass 1: converge the nginx config (the "notifying" tasks).
_, report, _ = await run(BETA, nginx_base)

# Freeze the hosts whose named operations actually changed.
changed = changed_hosts(report, {"disable default site"})

# Pass 2: reload nginx, only on those hosts.
def reload_nginx(host):
    if host.name in changed:
        yield op(systemd.service, name="reload nginx",
                 service="nginx", reloaded=True, sudo=True)

await run(BETA, reload_nginx)
```

`changed` is a fixed value by the time pass 2 builds its deploy, so the branch is on a snapshot — never a live fact — and stays deterministic. An empty `changed` yields an empty deploy: no host reloads. This is the whole notification mechanism, and it needs no handler-ordering rules to memorise.

!!! note "systemd needs a real init"

    The reload above (and the service steps later) drive `systemctl`, which a
    stock `ubuntu:24.04` container does not run as PID 1 — `systemctl` refuses
    with "System has not been booted with systemd as init system". The
    apt/files/user output in this article is verified against the container;
    the systemd steps are shown as the code and the plan/log a systemd-enabled
    host (a real VM, bare metal, or a `jrei/systemd-ubuntu` image) renders. On
    production this just works.

## Static site + code reuse

With nginx installed, the landing page is next. Keep its operations in a reusable function and include it conditionally, so a host only gets a site if its inventory says so:

```python
def deploy(host):
    yield from nginx_base(host)
    if host.extra.get("home_domain"):
        yield from sub_deploy(home_site, name_prefix="home")(host)
```

`host.extra` is static inventory data, so this `if` is deterministic — both phases resolve it the same way. `sub_deploy(..., name_prefix="home")` labels every request the sub-deploy yields with a `home:` prefix, which is how the plan keeps a composed deploy readable.

Two honest adaptations from a YAML-and-Jinja workflow:

- **There is no `files.sync` and no controller-filesystem awareness.** Reemote never resolves a local directory path on the target's behalf; content crosses as *data*. To publish a folder of static files, read them control-side and install each one:

  ```python
  from pathlib import Path
  from reemote import files, op

  def home_site(host):
      root = Path("site")                       # read on the control machine
      for path in sorted(root.rglob("*")):
          if path.is_file():
              rel = path.relative_to(root)
              yield op(files.install, name=f"install {rel}",
                       dest=f"/var/www/home/{rel}",
                       content=path.read_text(), sudo=True)
  ```

- **There is no `files.template`.** Jinja rendering happens control-side, in plain Python, and the resulting string goes to `files.install`. That keeps an unhashable template context out of the request (which must stay hashable) and needs no templating dependency.

For a single landing page, `files.install` writes the text and compares content, so a re-run is a no-op. Verified output — first run, then the identical run again:

```text
== plan ==
beta: install landing page     [changed: content /var/www/html/index.html is absent, will install]
== log ==
beta: install landing page     [changed: content /var/www/html/index.html is absent, will install]
    $ scp <files install> -> /tmp/reemote-install-0c941e79a0742ece/content
    $ sh -c 'mkdir -p /var/www/html && cp /tmp/reemote-install-0c941e79a0742ece/content /var/www/html/index.html && chmod 644 /var/www/html/index.html && rm -rf /tmp/reemote-install-0c941e79a0742ece'
```

```text
== plan ==
beta: install landing page     [no change: content already up to date (/var/www/html/index.html)]
== log ==
beta: install landing page     [no change: content already up to date (/var/www/html/index.html)]
```

The applied log shows the **spool-and-install** pattern reemote uses for root-owned files: an unprivileged `scp` of the text to a `/tmp` spool (named after the destination's hash, so it is stable across phases and never collides), then one elevated `sh -c` that copies it into place, sets the mode, and removes the spool. Nothing ever runs `sed -i` on a protected file. And now we have a static site.

## Web app + secrets

The application is a small Flask app served by gunicorn behind nginx on a Unix socket:

```text
:80, :443
   |
.-------------.
|    nginx    |  groups: app-socket, www-data
'-------------'
   |
 app.sock     groups: app-socket
   |
.- gunicorn --.
|   web app   |  groups: app-socket, app
'-------------'
```

### Users and groups

nginx and gunicorn share the socket through a group, so create the accounts first:

```python
def app_users(host):
    yield op(server.group, name="create app group", group="app")
    yield op(server.group, name="create socket group", group="app-socket")
    yield op(server.user, name="create app user", user="app", group="app",
             groups=("app-socket",), home="/var/lib/app",
             shell="/usr/sbin/nologin", system=True)
```

Verified output:

```text
== plan ==
beta: create app group         [changed: group app absent, will create]
beta: create socket group      [changed: group app-socket absent, will create]
beta: create app user          [changed: user app absent, will create; home /var/lib/app missing, will create]
== log ==
beta: create app group         [changed: group app absent, will create]
    $ groupadd app
beta: create socket group      [changed: group app-socket absent, will create]
    $ groupadd app-socket
beta: create app user          [changed: user app absent, will create; home /var/lib/app missing, will create]
    $ useradd -d /var/lib/app -s /usr/sbin/nologin -g app -G app-socket -r -M app
    $ mkdir -p /var/lib/app
    $ chown app:app /var/lib/app
```

Give nginx access to the socket by appending `www-data` to the group — `server.user(user="www-data", groups=("app-socket",), append=True)`. `groups` is a tuple, not a list, for the same hashability reason as `packages`.

### The virtual environment

Reemote ships no `pip` module, so the venv is a **custom operation** — an async generator that reads a fact and yields a command, exactly like the built-ins:

```python
from reemote import OpContext, StringCommand
from reemote.modules.files.files import FileExists

async def venv(ctx: OpContext, path: str, python: str = "python3"):
    """Ensure a virtualenv exists at ``path``."""
    if await ctx.facts.get(FileExists, f"{path}/bin/python"):
        ctx.reason = f"venv {path} already exists"
        return
    ctx.changed = True
    ctx.reason = f"venv {path} missing, will create"
    yield StringCommand(f"{python} -m venv {path}")

async def pip_install(ctx: OpContext, venv_path: str, packages: tuple[str, ...]):
    """Install packages into a venv (kept simple: always runs)."""
    ctx.changed = True
    ctx.reason = f"install {len(packages)} package(s) into {venv_path}"
    yield StringCommand(f"{venv_path}/bin/pip install {' '.join(packages)}")
```

Writing a custom operation is this little code, which is the offset reemote offers for a smaller built-in catalogue. A production `pip_install` would read `pip freeze` as a fact and skip work when the requirement is already satisfied; the version above is deliberately minimal. Use them like any built-in: `yield op(venv, name="create venv", path="/opt/app")`.

Publish the application source the same control-side way as the static files, then install it into the venv. The systemd unit is text, so it is `files.install` again, and the service is `systemd.service`:

```python
def app_service(host):
    yield op(files.install, name="write app.service",
             dest="/etc/systemd/system/app.service",
             content=render_unit(host), sudo=True)
    yield op(systemd.service, name="run app", service="app",
             running=True, enabled=True, daemon_reload=True, sudo=True)
```

On a systemd-enabled host, a first run renders (from the module's own reason strings — start is yielded before enable):

```text
== plan ==
web2: run app                  [changed: app.service not running (inactive), will start; app.service not enabled, will enable]
== log ==
web2: run app                  [changed: app.service not running (inactive), will start; app.service not enabled, will enable]
    $ sudo systemctl start app.service
    $ sudo systemctl enable app.service
```

...and converges on a re-run to `[no change: app.service already running; app.service already enabled]`. When the code changes you want a **restart**, which is the two-deploy split again — and reemote ships the helper for the common case:

```python
from reemote import changed_hosts, restart_handler, run

_, report, _ = await run(PROD, app_deploy)          # pass 1: publish code + config
changed = changed_hosts(report, {"write app config", "write app.service"})  # config or unit changed?
await run(PROD, restart_handler(changed, "app"))     # pass 2: restart only those
```

### Secrets

Flask needs a `SECRET_KEY`, and it should differ between beta and prod. A deploy is Python, so read the secret from wherever you keep it — a password manager, a hardware key, an encrypted file. To illustrate, a function that reads named secrets from a JSON file, keyed by stage:

```python
import json
from pathlib import Path

def get_secret(name, stage):
    data = json.loads(Path("secrets.json").read_text())
    if secret := data.get(stage, {}).get(name):
        return secret
    raise RuntimeError(f"secret {name!r} for stage {stage!r} not found")
```

The stage comes from `host.extra["stage"]` — set per host in the inventory, not hardcoded. Render the config control-side and install it, owned by the `app` group and mode `0640`:

```python
def app_config(host):
    stage = host.extra.get("stage", "beta")
    secret = get_secret("app_secret_key", stage)
    content = f"SECRET_KEY = {secret!r}\n"
    yield op(files.install, name="write app config",
             dest="/etc/opt/app/config.py", content=content,
             group="app", mode="0640", sudo=True)
```

!!! note "A restrictive mode is re-read elevated"

    `files.install` decides whether to write by reading the destination back —
    unprivileged, as the login user, first. A file installed `0640` and owned
    by a service account cannot be read by that login user, so the read-back
    falls back to an elevated `sudo` cat: content idempotence holds for the
    restrictive mode and the second run is a no-op. The re-read uses the same
    elevation the install already needs — the run-as user and any password
    come from the inventory's `privilege` block.

Where you store the secret is up to you; reading an unencrypted JSON file is the simple-and-wrong baseline, exactly as hardcoding it would be. Use an encrypted store in production.

## HTTPS + prod

A web app is nicer when it is not "not secure". Let's Encrypt gives free certificates and Certbot requests, installs, and renews them. Reemote has no built-in Certbot operation, so write one — a **fact** to read the current certificates and an **operation** to request a missing one:

```python
from reemote import OpContext, StringCommand

class Certificates:
    """The set of domain names certbot already has certificates for."""
    @classmethod
    async def process(cls, host):
        out = await host.run_shell("certbot certificates")
        return _parse_certbot_names(out)          # -> set[str]

async def certificate(ctx: OpContext, domain: str):
    """Request a certificate for ``domain`` if certbot does not have one."""
    if domain in await ctx.facts.get(Certificates):
        ctx.reason = f"certificate for {domain} already present"
        return
    ctx.changed = True
    ctx.reason = f"certificate for {domain} missing, will request"
    yield StringCommand(f"certbot certonly -d {domain} -n --nginx --agree-tos")
```

A fact is any class with an async `process` classmethod; an operation is any async generator taking an `OpContext`. Read state with the fact, yield a command only when it does not match — the same shape as every built-in. Install Certbot itself with `apt.packages(packages=("python3-certbot-nginx",))` first, and the site sub-deploy gains one conditional step:

```python
def nginx_site(host, template, domain, https=False):
    if https:
        yield op(certificate, name="cert", domain=domain, sudo=True)
    yield op(files.install, name="write site",
             dest=f"/etc/nginx/sites-available/{domain}",
             content=render_site(template, domain, https), sudo=True)
    yield op(files.link, name="enable site",
             path=f"/etc/nginx/sites-enabled/{domain}",
             target=f"/etc/nginx/sites-available/{domain}", sudo=True)
```

Certbot needs a **resolvable** domain name to complete the HTTP-01 challenge, so this cannot be exercised against the local `beta` container — but it can always be tested in production. The prod inventory is two SSH hosts, each with its own domain and HTTPS switched on through `host.extra`:

```python
PROD = [
    {"host": "web1",
     "connection": {"address": "web1.example.com", "username": "deploy",
                    "key_path": "~/.ssh/id_ed25519"},
     "home_domain": "www.example.com", "stage": "prod", "nginx_https": True},
    {"host": "web2",
     "connection": {"address": "web2.example.com", "username": "deploy",
                    "key_path": "~/.ssh/id_ed25519"},
     "app_domain": "app.example.com", "stage": "prod", "nginx_https": True},
]
```

There is no `--yes` flag to skip a prompt, because there is no prompt. Preview the whole fleet with `dry=True` — a real prepare pass against live facts on both servers, changing nothing — then run it for real:

```python
plans, _, _ = await run(PROD, deploy, dry=True)     # look
print(format_plan(plans))

plans, report, _ = await run(PROD, deploy)          # leap
print(format_log(report))
```

`sudo=True` on the requests is the whole elevation story on Ubuntu: reemote renders `sudo <command>` and never needs an interactive password prompt — use passwordless sudoers or a login user with `NOPASSWD`, and the run-as user and any password come from the inventory's `privilege` block, never from the operation. A minute later there are two new websites on the internet, and the identical deploy — the same generator functions — produced both, differing only in the inventory it was handed.

## Questions?

**Can I use reemote from an interactive Python shell?**
Yes — it is a library, so `await run(inventory, deploy)` works in a REPL, a notebook, or `asyncio.run(...)`. There is a little boilerplate (building the inventory, wrapping the async call), and no CLI sugar to load inventories for you; that is the trade for it being importable.

**Can I use reemote to provision cloud resources?**
No — use Terraform for that. Reemote configures machines over SSH (or inside containers); it does not create or destroy infrastructure. Staying "commands over a connection" is a deliberate scope. And if you find you need fewer resources than you thought, one well-built machine goes a long way.

**Can I use reemote with cloud inventories?**
Yes. The inventory is a Python list, so build it from your cloud's SDK directly — listing the instances in an autoscaling group is a few lines. There is no separate dynamic-inventory plugin to configure; it is just an expression that evaluates to a list of dicts.

**What about the connectors?**
This article used two: the default `ssh` connector for production, and the `docker` connector for the local `beta` container. The same deploy runs against either, because a connector only overrides the transport seams (run a command, copy a file) — you pick it per inventory entry, never in the deploy.

## Try it

- Documentation and full guide: <https://reemote.org/>
- Source: <https://github.com/kimjarvis/reemote>
- PyPI: `pip install reemote`
- License: EPL-2.0

Start a container, paste the `create_file.py` above, and watch a deploy plan and apply in two phases. Then point the same code at a real host over SSH. That is the whole onboarding.
