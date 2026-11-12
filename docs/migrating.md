# Migrating Ansible roles

## Migrating a handler

A handler is the single most common Ansible construct: a task carries
`notify: restart nginx`, and the named handler in `handlers/` runs once at the
end of the play, on the hosts where a notifying task actually *changed*
something. reemote has **no first-class handler** — there is no `notify:`
keyword and no event bus — because a deploy body cannot observe its own earlier
outcomes: `ctx.changed` flows one way (operation → driver →
[`ApplyReport`][reemote.drivers.ApplyReport]), and branching on it inside a
single deploy would make the prepare and apply phases yield different request
sequences, tripping [`DeterminismError`][reemote.drivers.DeterminismError].

The migration is therefore not a keyword swap but a **shape** change: the
notification read moves out of the deploy and into the gap *between* two
complete prepare/apply cycles. [`reemote.handlers`](handlers.md) packages that
shape into two helpers so you stop hand-rolling a bespoke
`notifying_hosts`/`build_restart_*` pair.

### The mapping

| Ansible | reemote |
| --- | --- |
| `notify: restart nginx` on a task | `name=` the request; collect those names into a notifying set |
| the handler in `handlers/` | [`restart_handler(hosts, services)`][reemote.handlers.restart_handler] |
| "did this task change?" | [`changed_hosts(report, names)`][reemote.handlers.changed_hosts] over the run's `ApplyReport` |
| flush once, at end of play, per changed host | free: the second deploy runs after pass 1's apply join, at most one restart per notified host |
| a converged play queues nothing | an empty frozen set yields an empty deploy — no request at all |

### The process

1. **Split the play into two deploys.** Pass 1 is the notifying tasks (installs,
   config writes); pass 2 is the handler. Keep pass 1 branch-free so both of its
   phases agree.
2. **Name every notifying request.** The `name=` you give each [`op`][reemote.core.op]
   is what selects it later — the analogue of the string in `notify:`. Gather
   those names into a module-level `frozenset`.
3. **Read the report between the two runs.**
   [`changed_hosts(report, NOTIFYING)`][reemote.handlers.changed_hosts] makes one
   pass over pass 1's [`ApplyReport`][reemote.drivers.ApplyReport] and freezes the
   hosts whose *named* entries reported `changed` into a `frozenset[str]`. A
   `changed` entry is trustworthy — the driver is fail-fast, so a non-zero exit
   raises [`CommandError`][reemote.hosts.CommandError] rather than logging a
   change — and a skipped entry (a `when` guard failed) never queues.
4. **Build the handler deploy from the frozen set.**
   [`restart_handler(changed, "nginx", ...)`][reemote.handlers.restart_handler]
   closes over that set and returns a deploy-shaped callable yielding one
   [`systemd.service`][reemote.modules.systemd.systemd.service]`(restarted=True)`
   per service, for the notified hosts only. Because its branch consults a
   snapshot taken *before* its own prepare phase, both phases resolve it
   identically and the determinism guard holds.
5. **Run pass 2.** The restart happens in a second full prepare/apply cycle.

### Before / after

```yaml
# Ansible -------------------------------------------------------------
# tasks/main.yml
- name: Install nginx
  package: { name: nginx }
  notify: restart nginx
- name: Write nginx config
  copy: { src: app.conf, dest: /etc/nginx/conf.d/app.conf }
  notify: restart nginx

# handlers/main.yml
- name: restart nginx
  service: { name: nginx, state: restarted }
```

```python
# reemote -------------------------------------------------------------
from reemote import changed_hosts, dnf, files, op, restart_handler, run

NOTIFYING = frozenset({"Install nginx", "Write nginx config"})

def configure_nginx(host):
    yield op(dnf.packages, name="Install nginx", packages=("nginx",), sudo=True)
    yield op(files.install, name="Write nginx config",
             content=APP_CONF, dest="/etc/nginx/conf.d/app.conf", sudo=True)

async def main() -> None:
    _, report, _ = await run(inventory, configure_nginx)   # pass 1: notifying tasks
    changed = changed_hosts(report, NOTIFYING)             # the notification read
    restart = restart_handler(changed, "nginx")            # pass 2: the handler
    await run(inventory, restart)                          # restart on changed hosts
```

The runnable version of this — with a `when=is_centos` guard, `--dry` handler
preview and inventory loading — is
[`handler_restart.py`](examples/demonstrations/composition.md#restarting-a-service-only-when-a-notifying-operation-changed).

### What to watch

- **`restarted=True` restarts a running unit only.** The operation's default
  `running=True` starts a stopped one, so a first pass over a freshly-installed,
  stopped unit starts it — matching `state: restarted` after an install.
- **A `dry=True` preview of pass 1 cannot know whether the handler will fire**
  (Ansible check mode skips handlers for the same reason). A `dry=True` preview
  of pass 2 *is* honest: its branch consults pass 1's frozen outcome, not
  pre-deployment state.
- **A handler that is not a systemd restart** — a reload, a script, a custom
  command — has no factory yet. Build the same shape by hand: a function closing
  over `changed_hosts(...)` that `return`s for hosts outside the set and
  `yield`s your requests for those inside it.
- For the full rationale and the reference signatures, see the
  [handlers guide section](guide.md#change-notification-handlers) and the
  [handlers reference](handlers.md).

## Migrating host_vars

Ansible's `host_vars/<name>.yml` (and its sibling `group_vars/`) let an
inventory carry arbitrary per-host data — a `java_home`, a package list, a
config knob — that a role consumes with `{{ java_home }}`. reemote has **no
separate vars tree**: the inventory entry *is* the vars file. Any key beyond
the five the schema recognises (`host`, `connection`, `privilege`, `group`,
`tags`) is preserved verbatim on the materialised
[`Host`][reemote.hosts.Host] as `host.extra`, because
[`InventoryEntry`][reemote.hosts.InventoryEntry] is declared with
`extra="allow"`. A deploy reads those keys the same way it reads `host.name`,
`host.group` or `host.tags` — as static inventory data a `when` guard may
also consult.

### The mapping

| Ansible | reemote |
| --- | --- |
| `host_vars/web1.yml: java_home: /opt/java` | a `java_home` key on the inventory entry — reaches the deploy as `host.extra["java_home"]` |
| `{{ java_home }}` in a task or template | `host.extra["java_home"]` (or `host.extra.get("java_home", DEFAULT)`) inside the deploy generator |
| `group_vars/webservers.yml` | no first-class analogue; a module-level `dict` keyed by `host.group` (or a `when=…` guard reading `host.extra`) fills the role |
| role `defaults/main.yml` | module-level Python constants the deploy falls back to when `host.extra` is silent |

### The process

1. **Put the variable on the inventory entry.** Any key that is not one of
   the five the [`InventoryEntry`][reemote.hosts.InventoryEntry] schema
   claims is preserved by `extra="allow"` and folded into `Host.extra` by
   [`Host.from_inventory`][reemote.hosts.Host.from_inventory] — no schema
   change, no `vars:` sub-mapping needed.
2. **Read it in the deploy body.** `host.extra["java_home"]` is the analogue
   of `{{ java_home }}`. Use `host.extra.get("java_home", DEFAULT)` when the
   role carries a default (Ansible's `defaults/main.yml` shape).
3. **Keep it hashable if it reaches an `op()` argument.** A list of packages
   in `host.extra` must be converted to a tuple before being passed as
   `packages=` — every request argument is `hash()`-checked, and a list is
   not hashable (see [Guide — Deploy scripts](guide.md#deploy-scripts)).
4. **Guard with it when the role is conditional.** `host.extra` is static
   inventory data, so `when=lambda host: host.extra.get("install_java", False)`
   is legal — reading a fact inside a guard is not.

### Before / after

```yaml
# Ansible -------------------------------------------------------------
# inventory/host_vars/web1.yml
java_home: /opt/java-21
java_packages:
  - java-21-openjdk
  - java-21-openjdk-devel

# roles/java/tasks/main.yml
- name: Install Java packages
  package: { name: "{{ item }}" }
  loop: "{{ java_packages }}"
- name: Set JAVA_HOME
  lineinfile:
    path: /etc/profile.d/java.sh
    line: 'export JAVA_HOME={{ java_home }}'
    create: yes
```

```python
# reemote -------------------------------------------------------------
# inventory.json (an entry carrying the vars inline)
# {
#   "host": "web1",
#   "connection": {"address": "192.0.2.20", "username": "deploy"},
#   "group": "webservers",
#   "java_home": "/opt/java-21",
#   "java_packages": ["java-21-openjdk", "java-21-openjdk-devel"]
# }

from reemote import dnf, files, op

JAVA_HOME_DEFAULT = "/opt/java"

def java_role(host):
    packages = tuple(host.extra.get("java_packages", ("java-21-openjdk",)))
    java_home = host.extra.get("java_home", JAVA_HOME_DEFAULT)
    yield op(dnf.packages, name="Install Java packages",
             packages=packages, sudo=True)
    yield op(files.install, name="Set JAVA_HOME",
             content=f"export JAVA_HOME={java_home}\n",
             dest="/etc/profile.d/java.sh", sudo=True)
```

### What to watch

- **`host.extra` is a plain `dict[str, Any]` with no schema.** A typo'd key
  returns `None` from `.get()`, not an error. Where a role needs a guarantee,
  validate the mapping yourself — a module-level pydantic model built from
  `host.extra` at the top of the deploy is one shape; the current inventory
  schema deliberately leaves that decision to the caller.
- **Lists must become tuples before reaching `op()`.** Every request argument
  is hash-checked; `packages=host.extra["java_packages"]` raises
  [`UnhashableArgumentError`][reemote.core.UnhashableArgumentError] when the
  value is a `list`. Wrap the read in `tuple(...)`.
- **`group_vars` has no first-class analogue.** The inventory entry is
  per-host, so shared defaults live in Python (a module-level dict keyed by
  `host.group`, a `sub_deploy(...)` guard, or plain constants the deploy
  falls back to). The static classifier `host.group` is what the deploy
  branches on to select them.
- **`host.extra` is safe inside a `when` guard.** It is static inventory
  data — it never changes between the prepare and apply passes — so the
  determinism guard is happy. Reading a fact inside a guard is not.
- **A typed `vars` sub-mapping is a design choice, not a limitation.** The
  inventory could grow a `vars: dict[str, Any]` key (or a
  `RoleVars(BaseModel)` schema) if a project wants the vars grouped under one
  namespace instead of sitting alongside `connection`/`privilege`. Today,
  `extra="allow"` already carries the data; the shape of the read is up to
  the deploy.

## Migrating include_vars OS dispatch

A role that supports several distributions usually carries one var file per
OS — `vars/RedHat.yml`, `vars/Debian.yml` — and pulls the right one in at run
time with `include_vars: "{{ ansible_os_family }}.yml"`, keyed on a **gathered
fact**. Every later task then references `{{ nginx_package }}`, which resolves
to a different value on each host. reemote has no `include_vars`, and the
deeper problem is that a deploy **cannot read a fact and branch on it**: the
runner executes the deploy twice (prepare, then apply), so a generator body
that reads [`server.LinuxDistribution`][reemote.modules.server.server.LinuxDistribution]
and picks a package from it yields a different request sequence per phase and
trips [`DeterminismError`][reemote.drivers.DeterminismError] (see
[Guide — Determinism](guide.md#determinism)).

The migration is the same **shape** change the handler and the two-deploy split
use: the fact-driven selection moves *out* of the deploy and into the gap
*before* it. Gather the distribution once, freeze a per-host package map, and
close the deploy over that map — the **gather → snapshot → factory** recipe.
The deploy then branches on an ordinary `dict` lookup of data captured before
its own prepare phase, so both phases resolve it identically and the
determinism guard holds.

### The mapping

| Ansible | reemote |
| --- | --- |
| `vars/RedHat.yml`, `vars/Debian.yml` | a module-level table keyed by distribution name |
| `include_vars: "{{ ansible_os_family }}.yml"` | [`gather_fact(hosts, LinuxDistribution)`][reemote.drivers.gather_fact] before the run, mapped to that table |
| `{{ nginx_package }}` in a task | `packages_by_host[host.name]` read inside the factory-built deploy |
| dispatch resolved at play time from a gathered fact | dispatch resolved once, frozen into a `dict`, then closed over — both phases agree |
| a converged host with no matching var file | a host absent from the frozen map yields nothing — the deploy `return`s early |

### The process

1. **Gather the fact up front.** Materialize the inventory with
   [`Host.from_inventory`][reemote.hosts.Host.from_inventory] and read the
   distribution across all hosts at once with
   [`gather_fact(hosts, server.LinuxDistribution)`][reemote.drivers.gather_fact]
   — it fans `LinuxDistribution.process` out concurrently and returns a
   `{host.name: {"name", "major", "minor", "release_meta"}}` mapping. Filter
   the host list yourself first if only a subset should be gathered; the
   `gather_fact` fan-out has no `when` parameter of its own.
2. **Snapshot it into a per-host map.** Translate each host's gathered
   `name` (or `major`) through a module-level table into the concrete request
   data — the package manager and the package tuple. Freeze the result into a
   plain `dict[str, ...]`. Keep every value **hashable**: a package list must
   become a `tuple` before it can reach `op()`.
3. **Close the deploy over the map.** A factory `build_install(packages_by_host)`
   returns a deploy generator whose branch reads `packages_by_host.get(host.name)`
   — a lookup on data captured *before* this deploy's prepare phase, never a
   live fact. Hosts the snapshot does not cover `return` early and queue
   nothing.
4. **Run it.** [`run(inventory, deploy)`][reemote.drivers.run] builds its own
   fresh hosts and closes them; the gather's hosts are separate, so close them
   yourself once the snapshot is taken.

### Before / after

```yaml
# Ansible -------------------------------------------------------------
# roles/web/vars/RedHat.yml
nginx_package: nginx
# roles/web/vars/Debian.yml
nginx_package: nginx-full

# roles/web/tasks/main.yml
- name: Load OS-specific vars
  include_vars: "{{ ansible_os_family }}.yml"
- name: Install nginx
  package: { name: "{{ nginx_package }}" }
```

```python
# reemote -------------------------------------------------------------
import asyncio

from reemote import Host, apt, dnf, gather_fact, op, run, server, validate_inventory

# The OS-specific vars, as a module-level table keyed by the distribution
# name LinuxDistribution reports -- one vars/<family>.yml per OS, collapsed
# into data the factory can read. The value carries the package manager too,
# so the dispatch swaps apt for dnf as well as the package name.
WEB_BY_DISTRO = {
    "CentOS": (dnf, ("nginx",)),
    "RedHat": (dnf, ("nginx",)),
    "Ubuntu": (apt, ("nginx-full",)),
    "Debian": (apt, ("nginx-full",)),
}

def build_install(web_by_host):
    """Factory: close over the frozen per-host dispatch map."""
    def install_web(host):
        entry = web_by_host.get(host.name)
        if entry is None:                 # no matching distro -> queue nothing
            return
        pkg_mod, packages = entry
        yield op(pkg_mod.packages, name="Install nginx",
                 packages=packages, sudo=True)
    return install_web

async def main() -> None:
    inventory = ...                        # loaded + validate_inventory(...)
    # 1. Gather the distribution across all hosts, concurrently.
    hosts = [Host.from_inventory(entry) for entry in inventory]
    try:
        distro = await gather_fact(hosts, server.LinuxDistribution)
    finally:
        for host in hosts:
            await host.aclose()            # gather_fact does not close them
    # 2. Snapshot: freeze the per-host dispatch map from the gathered fact.
    web_by_host = {
        name: WEB_BY_DISTRO[info["name"]]
        for name, info in distro.items()
        if info["name"] in WEB_BY_DISTRO
    }
    # 3+4. Close the deploy over the frozen map and run it.
    await run(inventory, build_install(web_by_host))

asyncio.run(main())
```

The runnable version of this shape — a `gather_fact` snapshot frozen into a
factory-built second deploy — is
[`two_deploy_split.py`](examples/demonstrations/composition.md#splitting-a-fact-dependent-deploy-in-two),
which branches on `dnf.DnfPackages` instead of `LinuxDistribution` but uses the
identical gather → snapshot → factory sequence.

### What to watch

- **The dispatch data must be hashable.** Every `op()` argument is
  `hash()`-checked, so a package list from a var table must be a `tuple`, not
  a `list`, or the request raises
  [`UnhashableArgumentError`][reemote.core.UnhashableArgumentError].
- **The snapshot is frozen.** If a host's distribution changed between the
  gather and the run — it will not, mid-script — the deploy would still plan
  against the snapshot. That is the same no-mid-run-replanning guarantee the
  determinism rule always makes; here the boundary is a point the script
  controls.
- **Gather and run use different host objects.** `gather_fact` opens the
  connections of the hosts you hand it and does **not** close them;
  [`run`][reemote.drivers.run] builds its own hosts from the inventory and
  closes those. Close the gather's hosts yourself once the snapshot is taken.
- **A `when` guard cannot read the fact.** `host.group` and `host.tags` are
  static inventory data a guard may consult; `LinuxDistribution` is not. If
  the OS is already known statically — carried on the inventory entry — you do
  not need to gather at all: that is the
  [host_vars migration](#migrating-host_vars), reading `host.extra` directly.
- **`LinuxDistribution.name` is the pretty form.** It reports `CentOS`,
  `RedHat`, `Ubuntu`, `Debian`, … (the `distro` id mapped through a known
  table); key your dispatch on that, or on `major`/`release_meta` when a role
  splits by version. See the
  [server reference](modules/server.md) for the full return shape.

## Migrating Jinja2 templates

Ansible's `template:` module is one of the most common ways to write a config
file: a `.j2` file on the control node, rendered with the play's variables —
including facts `register`ed from earlier tasks — and copied to a `dest`.
reemote has **no `files.template` operation**, and cannot grow one that behaves
like Ansible's, for two structural reasons that fall straight out of the
determinism model.

**1. A render context is unhashable, and every request argument is
`hash()`-checked.** A template's `variables` would travel through
[`op(...)`][reemote.core.op] into an
[`OperationRequest`][reemote.core.OperationRequest], which must stay *hashable*
so the runner can compare the prepare-phase and apply-phase request sequences
for equality. A Jinja2 context is naturally a `dict` — and a realistic one is
nested (a list of upstream maps, a mapping of vhosts) — but a `dict`/`list` is
unhashable and raises
[`UnhashableArgumentError`][reemote.core.UnhashableArgumentError] at the `op()`
line. The only legal spelling would be a flat tuple of `(key, value)` pairs with
immutable values, which cannot express the structures a real template loops
over. The example suite enforces this statically: `tests/test_examples.py`
rejects any mutable container literal handed to `op(...)`.

**2. There is no way to inject a host fact into a render inside a deploy.** The
Ansible idiom `stat` → `register: cert` → `template: … vars: {has_cert:
cert.stat.exists}` feeds a *gathered fact* into the render. A reemote deploy
cannot: the runner executes the deploy generator twice (prepare against
pre-deployment state, apply against live state), so a body that reads
[`files.FileExists`][reemote.modules.files.files.FileExists] and renders
differently on the answer yields a different request sequence per phase and
trips [`DeterminismError`][reemote.drivers.DeterminismError] (see
[Guide — Determinism](guide.md#determinism)). reemote renders control-side from
*static* data only; a live fact can never reach a render that happens inside the
deploy.

The migration is the same **shape** change the handler and include_vars
migrations use, with a render step dropped into the gap: **pre-render
control-side in `main()`, between deploys.** Gather the facts you need into a
frozen snapshot, render the template yourself against that snapshot plus static
inventory data, and hand the resulting `str` to
[`files.install`][reemote.modules.files.files.install]`(content=…)`. The
rendered string is an ordinary hashable value computed *before* the deploy
generator ever runs, so both phases see the identical text and the determinism
guard holds — and the fact's influence enters through the snapshot, never a live
read inside the deploy. This solves both problems at once: the unhashable `dict`
context stays in `main()`, where no request hashes it, and the host fact is
captured by [`gather_fact`][reemote.drivers.gather_fact], where a deploy *is*
allowed to use it.

### The mapping

| Ansible | reemote |
| --- | --- |
| `template: {src: example.conf.j2, dest: /etc/…}` | render the text yourself in `main()`, then `files.install(content=rendered, dest=/etc/…)` |
| `vars:` / `host_vars` fed to the render | static inventory data (`entry["host"]`, `host.extra`) read while rendering |
| `register: cert` + `{{ cert.stat.exists }}` in the template | [`gather_fact(hosts, files.FileExists, path)`][reemote.drivers.gather_fact] frozen into a snapshot before the render |
| Jinja2 `{{ }}`/`{% %}` resolved on the control node at task time | the same — but *you* own the Jinja2 `Environment`; reemote ships no template engine |
| one rendered file per host | a `{host.name: rendered_str}` map the deploy closes over |

### The process

1. **Gather the fact up front.** Materialize the inventory with
   [`Host.from_inventory`][reemote.hosts.Host.from_inventory] and read the
   cert's existence across all hosts at once with
   [`gather_fact(hosts, files.FileExists, CERT)`][reemote.drivers.gather_fact] —
   it fans `FileExists.process` out concurrently and returns a
   `{host.name: bool}` snapshot. Filter the host list yourself first if only a
   subset should be gathered; `gather_fact` has no `when` parameter of its own.
2. **Render control-side.** Build each host's Jinja2 context from *static*
   inventory data (`entry["host"]`, `entry.get("port")`, `host.extra`) plus the
   frozen snapshot, render the template text with your own `Environment`, and
   freeze the results into a plain `dict[str, str]` keyed by `host.name`. The
   fact enters here — in `main()`, before any deploy — never inside a generator.
3. **Close the deploy over the rendered map.** A factory
   `build_install(rendered_by_host)` returns a deploy that yields
   [`op(files.install, content=rendered_by_host[host.name], …)`][reemote.core.op]
   — a lookup on data captured *before* this deploy's prepare phase. Hosts the
   map does not cover `return` early and queue nothing.
4. **Run it.** [`run(inventory, deploy)`][reemote.drivers.run] builds its own
   fresh hosts and closes them; the gather's hosts are separate, so close them
   yourself once the snapshot is taken.

### Before / after

```yaml
# Ansible -------------------------------------------------------------
# roles/nginx/tasks/main.yml
- name: Check for a TLS certificate
  stat: { path: /etc/ssl/private/example.pem }
  register: cert
- name: Write nginx config
  template:
    src: example.conf.j2
    dest: /etc/nginx/conf.d/example.conf
    mode: "0644"
  vars:
    has_cert: cert.stat.exists

# roles/nginx/templates/example.conf.j2
# server {
#     listen {{ port }};
#     server_name {{ server_name }};
# {% if has_cert %}
#     listen 443 ssl;
#     ssl_certificate /etc/ssl/private/example.pem;
# {% endif %}
# }
```

```python
# reemote -------------------------------------------------------------
import asyncio

from jinja2 import Environment, StrictUndefined  # your own dependency now

from reemote import Host, files, gather_fact, op, run, validate_inventory

CERT = "/etc/ssl/private/example.pem"
DEST = "/etc/nginx/conf.d/example.conf"

# The template text (Ansible's templates/example.conf.j2), held on the control
# machine as a plain string. You render it yourself, before the deploy runs.
NGINX_CONF = """\
server {
    listen {{ port }};
    server_name {{ server_name }};
{% if has_cert %}
    listen 443 ssl;
    ssl_certificate {{ cert }};
{% endif %}
}
"""

# StrictUndefined / keep_trailing_newline are now YOUR renderer's choices;
# reemote only ever sees the finished string.
ENV = Environment(undefined=StrictUndefined, keep_trailing_newline=True)


def build_install(rendered_by_host):
    """Factory: close over the per-host text rendered before the run."""
    def install_conf(host):
        content = rendered_by_host.get(host.name)
        if content is None:                 # nothing rendered for this host
            return
        yield op(files.install, name="Write nginx config",
                 content=content, dest=DEST, mode="0644", sudo=True)
    return install_conf


async def main() -> None:
    inventory = ...                        # loaded + validate_inventory(...)
    # 1. Gather the cert-existence fact across all hosts, concurrently.
    hosts = [Host.from_inventory(entry) for entry in inventory]
    try:
        has_cert = await gather_fact(hosts, files.FileExists, CERT)
    finally:
        for host in hosts:
            await host.aclose()            # gather_fact does not close them
    # 2. Render control-side: static inventory data + the frozen snapshot. The
    #    fact (has_cert) enters HERE, in main(), never inside the deploy.
    rendered_by_host = {
        entry["host"]: ENV.from_string(NGINX_CONF).render(
            port=entry.get("port", 80),
            server_name=entry["host"],
            cert=CERT,
            has_cert=has_cert[entry["host"]],
        )
        for entry in inventory
    }
    # 3+4. Close the deploy over the rendered map and run it.
    await run(inventory, build_install(rendered_by_host))


asyncio.run(main())
```

The runnable version of the gather → snapshot → factory shape this builds on —
frozen between two deploys — is
[`two_deploy_split.py`](examples/demonstrations/composition.md#splitting-a-fact-dependent-deploy-in-two);
swap its `DnfPackages` snapshot for a `FileExists` snapshot and its second
deploy for a `files.install` of your own rendered text.

### What to watch

- **The rendered `content` is a plain `str`.** It is hashable, compares by value
  across the two phases, and needs no identity-stable module-level stream — the
  `files.put`-style stream contract does not apply to `files.install`. This is
  exactly why the render belongs in `main()`: the unhashable `dict` context
  never reaches a request.
- **The snapshot is frozen.** A cert provisioned between the gather and the run
  is not seen — the same no-mid-run-replanning guarantee the determinism rule
  always makes. When an *earlier deploy* creates the cert, gather *after* that
  deploy's apply join (the two-deploy split), so the render reads the state run 1
  actually left behind.
- **Gather and run use different host objects.** `gather_fact` opens the
  connections of the hosts you hand it and does **not** close them;
  [`run`][reemote.drivers.run] builds and closes its own. Close the gather's
  hosts once the snapshot is taken.
- **A root-owned `dest` needs `sudo=True`.** `files.install` spools the text to a
  `/tmp` path over unprivileged scp, then installs it elevated, so it converges
  `/etc/...` from one request; the transfer stays unprivileged regardless.
- **Per-host rendering is a dict keyed by `host.name`.** A host the rendered map
  does not cover queues nothing — the deploy `return`s early — so a subset gather
  and a `when`-guarded deploy compose the same way they do in the
  [include_vars migration](#migrating-include_vars-os-dispatch).

## Migrating a protected-file line edit (lineinfile)

Ansible edits a line inside an existing config file with `lineinfile` (one
line, keyed by a `regexp` or an exact `line`), `replace` (every match of a
regex) or `blockinfile` (a marked region). The target is almost always
**root-owned** — `/etc/httpd/conf/httpd.conf`, `/etc/ssh/sshd_config`,
`/etc/sysctl.conf` — and the edit must *preserve the rest of the file*: every
other directive, comment and blank line stays exactly as it was.

reemote's answer is [`files.transform`][reemote.modules.files.files.transform]:
you supply a **pure transform function** that maps the file's whole current
text to its whole desired text, and the operation reads the file with the
[`FileContents`][reemote.modules.files.files.FileContents] fact, applies your
transform, and — only when the result differs — writes it back through the
spool-and-install path it shares with
[`files.install`][reemote.modules.files.files.install]: the transformed text is
spooled over *unprivileged* scp to a `/tmp` path, then an elevated `sh -c`
overlays the protected file with it. A single `sudo=True` request therefore
converges a root-owned config in one deploy.

This is the piece [`files.edit`][reemote.modules.files.files.edit] cannot do.
`files.edit` applies the *same* transform contract but writes back over
`files.put`'s unprivileged scp, so `sudo`/`su` do not apply to it and its
target must be writable by the login user — it cannot converge
`/etc/httpd/conf/httpd.conf`. `files.transform` is `files.edit`'s transform
with `files.install`'s write. For the one high-traffic format that has a
dedicated operation — the hosts file — `server.etc_hosts` already spells this
out; `files.transform` is the generic form for every other protected file.

### The transform: format and structure

A transform is an ordinary module-level Python function with a fixed shape:

```python
def my_transform(current: str | None) -> str:
    ...
```

- **Input** — `current` is the file's *whole* text as one `str`, exactly as
  [`FileContents`][reemote.modules.files.files.FileContents] read it (a single
  blob, not a list of lines, so you re-split it however you like), or `None`
  when the file does not exist. Absence (`None`) and an empty file (`""`) are
  kept distinct.
- **Output** — the *whole* desired text as a `str`. Return the complete file,
  not a diff or a single line: the operation installs exactly what you return.

A transform must obey **three laws**, all enforced by how the operation runs:

1. **Pure.** It reads only its `current` argument and returns a string. No
   host access, no file or network IO, no `datetime.now()`, no randomness, no
   mutation of a global. It runs once per phase (prepare, then each apply
   step) and every run must derive the same output from the same input.
2. **Idempotent — a fixpoint.** `my_transform(my_transform(x)) ==
   my_transform(x)` for every `x`. The operation compares your output against
   the file's current text and writes only when they differ, so a second run
   over a file your transform already produced must be a no-op. In practice:
   *check before you add* — never append a line that is already present, never
   duplicate a block that already exists.
3. **Stable and hashable.** Pass a module-level function (or an object with a
   stable `__hash__`), never a `lambda` or a closure built *inside* the deploy
   generator. The transform travels through [`op(...)`][reemote.core.op] into
   an [`OperationRequest`][reemote.core.OperationRequest], which the runner
   compares by value between the prepare and apply phases; a fresh callable
   per phase diverges and raises
   [`DeterminismError`][reemote.drivers.DeterminismError] — exactly as a fresh
   IO stream would for `files.put`.

The near-universal body shape is **split, edit only the matching lines,
rejoin**, so every untouched line survives verbatim:

```python
lines = (current or "").splitlines()   # None-safe: an absent file starts empty
# ... edit / insert / remove only the lines you own ...
return "\n".join(lines) + "\n"         # restore the trailing newline
```

### The mapping

| Ansible | reemote |
| --- | --- |
| `lineinfile: {path, line}` (ensure one line) | a transform that appends `line` only when it is not already a line |
| `lineinfile: {path, regexp, line}` (set the line matching a regex) | a transform that rewrites every `regexp`-matching line to `line`, appending when none matched |
| `lineinfile: {path, regexp, state: absent}` | a transform that drops every `regexp`-matching line |
| `replace: {path, regexp, replace}` | a transform running `re.sub(regexp, replace, current)` over the whole text |
| `blockinfile: {path, block, marker}` | a transform that keeps the marked region between the `marker` lines and replaces its contents — see the [`block_transform` factory](examples/modules/files.md#managing-a-marked-block-baseline-filesblock-parity) |
| `create: yes` | free: an absent file reaches the transform as `None`, so `transform(None)` produces the content to create it from |
| `backup: yes` | dropped — the timestamped `cp` is non-deterministic across phases; snapshot with your own tooling if you need one |
| the module's own privilege escalation | `sudo=True` on the [`op(...)`][reemote.core.op]: the spool transfer stays unprivileged, only the install elevates |

### The process

1. **Write the transform as a module-level function.** Take
   `current: str | None`, return the whole desired `str`. Make it a fixpoint:
   check for the line/block before adding, and match on an anchored, escaped
   pattern so the "add" and "remove" directions agree on what the line *is*.
2. **Parametrize with a factory, called once.** When the edit needs arguments
   (the port, the marker text, the regex), build the transform with a factory
   *outside* the deploy generator and pass the resulting function in. The
   closure must be one stable object across both phases, so construct it at
   module level or once in `main()` — never per-yield inside the generator.
3. **Yield the `files.transform` request.** Pass `path` (the root-owned
   target) and `transform` (your function). Flag `sudo=True`: an `/etc` file
   needs root for the install half.
4. **Optionally set `mode`/`user`/`group`.** They are forwarded to the
   install, so the same request that lands the edit can also fix permissions
   or ownership. Idempotence stays **content-only**: a mode- or owner-only
   drift does not re-install (use
   [`files.file`][reemote.modules.files.files.file] to converge metadata).

### Before / after

```yaml
# Ansible -------------------------------------------------------------
# roles/httpd/tasks/main.yml
- name: Set the Listen port
  lineinfile:
    path: /etc/httpd/conf/httpd.conf
    regexp: '^Listen\s'
    line: 'Listen 8080'
  notify: restart httpd
```

```python
# reemote -------------------------------------------------------------
import re

from reemote import files, op

LISTEN_PORT = 8080

def set_listen_port(current: str | None) -> str:
    """Pure, idempotent: rewrite the Listen directive to LISTEN_PORT.

    Every non-Listen line -- directive, comment, blank -- is preserved
    verbatim. When the file already listens on the port, the output equals
    the input, so the second run is a fixpoint and files.transform reports
    no change.
    """
    wanted = f"Listen {LISTEN_PORT}"
    lines = (current or "").splitlines()
    replaced = False
    for index, line in enumerate(lines):
        if re.match(r"^\s*Listen\b", line):
            lines[index] = wanted
            replaced = True
    if not replaced:                       # no Listen line yet -> append one
        lines.append(wanted)
    return "\n".join(lines) + "\n"

def configure_httpd(host):
    yield op(files.transform, name="Set the Listen port",
             path="/etc/httpd/conf/httpd.conf",
             transform=set_listen_port, sudo=True)
```

The `notify: restart httpd` half is a separate concern — see the
[handler migration](#migrating-a-handler) for the two-deploy shape that
restarts only when this request actually changed the file.

The runnable version of this — an `ensure_listen_directive` transform applied
twice to prove the second is a no-op — is the
[`files.transform` example](examples/modules/files.md#editing-a-root-owned-file-with-a-pure-transform).

### What to watch

- **Return the whole file, not a fragment.** The operation installs exactly
  the string you return, so a transform that returns only the edited line
  would truncate the file. Split, edit, rejoin.
- **Idempotence is content-equality, not intent.** A transform that always
  appends (without checking) is not a fixpoint: every run grows the file. Gate
  the add on the same anchored pattern the remove would use, so appending a
  line that already appears is a no-op.
- **Restore the trailing newline.** `splitlines()` drops it; `"\n".join(lines)
  + "\n"` puts it back. A transform that silently strips the final newline
  rewrites the file on the first run and then converges — but the churn is
  avoidable.
- **The transform must be a stable object.** A `lambda`, or a factory called
  inside the deploy generator, produces a fresh callable per phase and trips
  [`DeterminismError`][reemote.drivers.DeterminismError]. Build it once at
  module level (or once in `main()`) and pass that one function.
- **A file the login user cannot read re-installs every deploy.**
  `FileContents` cats *unprivileged*, so a target with a mode the login user
  cannot read (a `0640` service-account config) reads back empty and never
  matches the transform's output. Keep the target login-readable, or converge
  it with a fact that can read it.
- **`sudo=True` elevates the install, not the transfer.** The scp spool copy
  always rides as the login user into world-writable `/tmp`; only the
  `sh -c` install that overlays the protected file elevates. The predictable
  `/tmp` spool name avoids collisions, not a hostile local user — the same
  trade-off every spool-and-install operation makes.
