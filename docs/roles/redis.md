# redis — Redis cache role

| | |
| --- | --- |
| Role | [`geerlingguy.redis`](https://github.com/geerlingguy/ansible-role-redis) |
| Module | [`reemote.roles.redis`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/redis.py) |
| Runner | [`examples/roles/redis.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/redis.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `redis` role mapping → `RedisVars` |

<!---
| Waves | `2` on a changing pass (requests, then handler), `1` once converged — one checkpoint, no facts |
| Outputs | none — the deploy returns `None`; no other role reads redis's results |
| Extra deps | **`reemote[templates]`** (jinja2 renders `redis.conf.j2`) |
| Offline test | [`tests/roles/test_redis.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_redis.py) |
| Factory | plain (non-`async`) — the runner calls `redis(v)`; the render is control-side Python inside the body, not a barrier |

`redis` installs the `redis-server` package on Ubuntu hosts, creates the
`02770` setgid `/etc/redis` directory (redis:redis), writes
`/etc/redis/redis.conf` from the role's Jinja2 template (redis:redis, mode
`0640`), and starts and enables the service; a config change queues the role's
one handler, `restart redis`. The role has no command/shell tasks and no facts
at all.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with `yield from scoped("redis", redis(v))(host)`
and a run is one `run_waves` pass. The Ansible role's whole task list *and* its
handler are that single deploy, and its `defaults/main.yml` is `RedisVars`.

Like memcached, this is a **checkpoint-only role** (design doc §18.13) — but it
carries one quirk memcached does not: the role's Debian ordering runs
`configure.yml` *before* `setup-Debian.yml`, because the package install starts
Redis and it must start against the managed config. The migration keeps that
sequence inside the single wave: directory, config, companion, package, service.
The stream is:

```python
yield from configure(redis_plan(hv))(host)   # wave 0 — five requests, config first
wave_report = yield checkpoint("configure done") # the role's only barrier
notified = changed_hosts(wave_report, NOTIFY_RESTART)
yield from handler(notified)(host)           # wave 1 — the restart, or nothing
```

## 1. What it is, and where it came from

The role's tasks — and its handler — map onto reemote requests and one barrier:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `include_vars: "{{ ansible_os_family }}.yml"` | frozen `vars/Debian.yml` constants (`PACKAGE_DEFAULT`, `SERVICE`, `CONF_PATH`, `CONF_DIR`, `CONF_DIR_MODE`, `CONF_MODE`), the single-OS scope needs no gather | recipe |
| `set_fact: redis_package={{ __redis_package }}` when not defined | `RedisVars.package` defaults to `PACKAGE_DEFAULT`; an explicit `package` key in the role mapping wins, as the extra variable won over the `set_fact` | recipe |
| `file: dest=/etc/redis state=directory mode=02770` | `files.directory(path="/etc/redis", user="redis", group="redis", mode="02770")` — the setgid mode normalizes to the canonical int `2770`; the task carries no `notify`, so its change never restarts the daemon | covered |
| `template: redis.conf.j2 dest=/etc/redis/redis.conf mode=0640` `notify: restart redis` | control-side Jinja2 render → `files.install(user="redis", group="redis", mode="0640")` + `files.file` metadata companion; both names notify | recipe |
| `apt: name={{ redis_package }} state=present` (`setup-Debian.yml`, no `update_cache`) | `apt.packages(packages=(plan.package,))` — and *no* `apt.update` request, unlike memcached's | covered |
| `service: name=redis-server state=started enabled={{ redis_enabled }}` | `systemd.service(service="redis-server", running=True, enabled=plan.enabled)` | covered |
| `handlers/main.yml: restart redis` (`service: state=restarted`) | `wave_report = yield checkpoint("configure done")` → `changed_hosts(…, NOTIFY_RESTART)` → `restart_handler(notified, "redis-server")` | barrier |

The task mapping itself needed no library extension — every task landed on a
shipped operation. The role reads nothing mid-play, so there is no `fact`/`act`
barrier to place and the checkpoint is the entire barrier surface. The first
*live* two-pass run did, however, expose one probe gap — a managed file behind
a non-traversable `02770` service directory reads as absent to the unprivileged
idempotence probe, so the config re-installs on every real-host run — filed as
[issue #70](https://github.com/kimjarvis/reemote/issues/70) and now closed: an
existence read that comes back absent to the login user is checked against the
nearest existing ancestor directory's search bit, and a directory the login
cannot traverse turns the answer into an elevated re-probe — only a negative
*elevated* answer reads as absent. `SimulatedHost` models the same denial
(`login_path_visible`), so the offline suite pins it.

## 2. Install

```bash
pip install "reemote[templates]"   # or: uv add "reemote[templates]"
```

`jinja2` is the only extra: the config is rendered from the role's bundled
`redis.conf.j2` (four `if` guards and three `for` loops), which a plain f-string
cannot reproduce. The import enters only through `reemote.roles._render`,
*inside* `render_redis_conf`, so `import reemote.roles` stays clean without the
extra and a missing `jinja2` surfaces at render time rather than at import time.

SSH access to each Ubuntu host with the key named in its `connection`, and
passwordless sudo (or a `privilege` section in the inventory): every request
carries `sudo=True`, because reemote has no play-level `become`. The config
write is a spool-and-install (`files.install`), so the transfer itself never
elevates; the `0640` redis-owned file converges through the content fact's
two-tier elevated read-back.
--->

::: reemote.roles.redis
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - redis

**Variables:**

### Role variables (`defaults/main.yml`)

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `package` | `str` | `redis-server` — from `vars/Debian.yml`'s `__redis_package`, the `set_fact` default | `redis_package` |
| `enabled` | `bool` | `True` — the unit's boot-time state | `redis_enabled` |
| `port` | `int` | `6379` (`port`) | `redis_port` |
| `bind_interface` | `str` | `127.0.0.1` (`bind`) | `redis_bind_interface` |
| `unixsocket` | `str` | `""` — empty renders no `unixsocket` line, as the `{% if %}` guard does | `redis_unixsocket` |
| `timeout` | `int` | `300` (`timeout`) | `redis_timeout` |
| `loglevel` | `debug` \| `verbose` \| `notice` \| `warning` | `notice` (`loglevel`) | `redis_loglevel` |
| `logfile` | `str` | `/var/log/redis/redis-server.log` (`logfile`) | `redis_logfile` |
| `databases` | `int` | `16` (`databases`) | `redis_databases` |
| `save` | `tuple[str, …]` | `("900 1", "300 10", "60 10000")` — one `save` line each; an empty tuple disables snapshot persistence | `redis_save` |
| `rdbcompression` | `yes` \| `no` | `yes` (`rdbcompression`) | `redis_rdbcompression` |
| `dbfilename` | `str` | `dump.rdb` (`dbfilename`) | `redis_dbfilename` |
| `dbdir` | `str` | `/var/lib/redis` (`dir`) | `redis_dbdir` |
| `maxmemory` | `int` | `0` — falsy renders no maxmemory block, as the `{% if %}` gate does | `redis_maxmemory` |
| `maxmemory_policy` | `str` | `noeviction` (`maxmemory-policy`) | `redis_maxmemory_policy` |
| `maxmemory_samples` | `int` | `5` (`maxmemory-samples`) | `redis_maxmemory_samples` |
| `appendonly` | `yes` \| `no` | `no` (`appendonly`) | `redis_appendonly` |
| `appendfsync` | `always` \| `everysec` \| `no` | `everysec` (`appendfsync`) | `redis_appendfsync` |
| `includes` | `tuple[str, …]` | `()` — one `include` line each | `redis_includes` |
| `requirepass` | `str` | `""` — empty renders no `requirepass` line | `redis_requirepass` |
| `disabled_commands` | `tuple[str, …]` | `()` — one `rename-command <cmd> ""` line each | `redis_disabled_commands` |
| `extra_config` | `str` | `""` — raw lines appended at the end of the config | `redis_extra_config` |
| `enablerepo` | `str` | `epel` — RHEL-only, binds and does nothing here (D4) | `redis_enablerepo` |

<!---
Precedence is `model defaults < inventory role mapping < overrides`. Validation is
fail-fast at `resolve_vars`, before any host is contacted: `package`,
`bind_interface`, `logfile`, `dbfilename` and `dbdir` must be non-empty
strings; `loglevel`, `rdbcompression`, `appendonly` and `appendfsync` are
`Literal` enums over the values the README and the config file itself accept;
sequences are `tuple`s (a JSON list arrives normalized to one, the hashability
rule). A wrongly typed value is a pydantic `ValidationError` — a
`ValueError`, so the runner maps it. A key naming no field in the role mapping,
such as `packagez`, raises `RoleVarsError` naming the valid keys. Plain `int`
fields keep pydantic's lax coercion — `"6380"` is accepted as `6380` — the
repo-wide convention (memcached's `port` behaves the same) and the render is
identical either way (D5).

## 5. Run it

The role is library code; `examples/roles/redis.py` is the runnable
artifact — a standalone program with its own `main()`, no shared runner. It
loads and validates `inventory.json` from the current working directory,
resolves the role's variables (the defaults overlaid with the entry's
`redis` role mapping, then the call-site `overrides`), builds the deploy
from the `redis` factory and drives it with `run_waves` — a `dry=True`
preview, then the real apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import RedisVars, redis

REDIS_OVERRIDES = {"port": 6380, "save": ()}

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(RedisVars, "redis", inventory, overrides=REDIS_OVERRIDES)
    deploy = redis(vars_set)                                                 # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)            # parks at the checkpoint
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)                      # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/redis.py
```

The call-site `REDIS_OVERRIDES` move the listen port to `6380` and disable
snapshot persistence, so the demonstration writes a config that visibly
differs from the packaged default and the `restart redis` handler has
something to fire on. The `dry=True` pass is *not* empty here: the role's
first items are requests, so the dry run plans all five configure requests
and then parks on the checkpoint barrier (`barrier: configure done → unknown
until run`) — the notification read needs a real apply report, so the handler
wave stays unplanned, exactly like Ansible's `--check` skipping handlers.
Nothing is applied.

**Run the program twice.** The second offline pass must report no change: the
package is installed, the config directory is `02770`, the config is
byte-identical with redis:redis `0640` metadata, the service is running and
enabled — and the checkpoint notifies nobody, so the handler wave never
appears. That is the migration's idempotence claim, and the offline suite
asserts it. On a *live* host the second pass converges for the same reason:
`dpkg` re-owns `/etc/redis` to `redis:redis 02770`, so the unprivileged
idempotence probe cannot traverse into the directory and reads the managed file
as absent — issue #70's gate catches that and re-probes elevated, where the file
is visible. The elevated re-probe is pinned offline by
`tests/roles/test_redis.py`, not by a live run.

## 6. What it does

One deploy. The checkpoint closes the request wave, and a wave with no records
is not appended — so the shape below is what the test pins:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `configure`: `files.directory(/etc/redis, redis:redis, mode="02770")`, `files.install(/etc/redis/redis.conf, redis:redis, mode="0640")` + `files.file(mode="0640")` companion, `apt.packages(("redis-server",))`, `systemd.service(running=True, enabled=…)` — then `checkpoint("configure done")` | every input is a resolved variable or a frozen constant, so the whole task list is one pure request wave — in the role's own Debian order, config **before** package; the checkpoint reads its outcome |
| 1 | `handler`: `restart_handler(notified, "redis-server")` — a request only on the hosts the checkpoint selected | the role's one `notify:` channel; an empty notification set emits no request, so a converged pass has no wave 1 at all |

Five rules are load-bearing:

* **The checkpoint is the only barrier — and there are no facts at all.** The
  vars split by OS family, not release, so the single-OS scope collapses
  `include_vars` to frozen constants and no `LinuxDistribution` read survives
  (the memcached precedent). A `fact` or `act` barrier here would be
  speculatively added, and **three waves** on a changing pass are the same
  signal (§18.13).
* **The Debian ordering is reproduced inside the wave.** `tasks/main.yml` runs
  `configure.yml` before `setup-Debian.yml` — the package install starts Redis,
  which must start against the managed config (the README's example: hosts
  with IPv6 disabled). The request order directory → config → companion →
  package → service is pinned by the test, not left to chance.
* **Two waves total — one for the requests, one for the handler.** The
  checkpoint must immediately follow the stage it reports on (§4.3); the
  handler wave's entries would be invisible to a second checkpoint, and
  nothing after the restart reads a report.
* **The companion is a first-pass no-op by design, and it notifies.**
  `files.install` writes content *and* metadata, so `files.file` changes
  nothing until a later pass finds drift: content byte-identical but mode
  `0600` → install reports no change (its idempotence is content-only) and the
  companion re-applies `0640` — and, like Ansible's template module reporting
  a change on owner/group/mode drift, its notification fires the restart too
  (D3). Conversely the *directory* task changed nothing in the notify channel:
  a `02750` drift converges silently, because the role puts no `notify` on it.
* **The handler entry is the library's generated name.** The log line is
  `Restart redis-server (handler)` — `restart_handler`'s default label — not a
  request the role names, and `changed_hosts` matches the *notifying* names
  (`Configure Redis`, `Set Redis configuration attributes`) exactly; the
  package and service tasks carry no `notify` in the role, so their changes
  never restart the daemon.

A host whose `family` is not `"Ubuntu"` returns before its first `yield`: no
requests, no barriers, no report lines — RedHat and Archlinux entries in a
shared inventory are never contacted.

## 7. Transcript

From `tests/roles/test_redis.py`, against the simulated Ubuntu host (`port`
overridden to `6380`, `save` emptied) plus a RedHat host the role never
touches. No between-pass bridge is needed — the simulation's own mutators
maintain every seam the second pass reads. Dry preview first: every request
is planned, the checkpoint is unknown, nothing is applied.

```text
(root)
  wave 0
    ubuntu: Ensure Redis configuration directory [changed: /etc/redis missing, will create]
    ubuntu: Configure Redis          [changed: content /etc/redis/redis.conf is absent, will install]
    ubuntu: Set Redis configuration attributes [changed: /etc/redis/redis.conf missing, will create; parent /etc/redis missing, will create]
    ubuntu: Install Redis            [changed: 1 package(s) to install]
    ubuntu: Ensure Redis is started and enabled [changed: redis-server.service not running (None), will start; redis-server.service not enabled, will enable]
    barrier: configure done → unknown until run
```

Fresh host, real apply:

```text
(root)
  wave 0
    ubuntu: Ensure Redis configuration directory [changed: /etc/redis missing, will create]
        $ sudo mkdir -p /etc/redis
        $ sudo chmod 2770 /etc/redis
        $ sudo chown redis:redis /etc/redis
    ubuntu: Configure Redis          [changed: content /etc/redis/redis.conf is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-862d2769988f5147/content
        $ sudo sh -c 'mkdir -p /etc/redis && cp /tmp/reemote-install-862d2769988f5147/content /etc/redis/redis.conf && chown redis:redis /etc/redis/redis.conf && chmod 640 /etc/redis/redis.conf && rm -rf /tmp/reemote-install-862d2769988f5147'
    ubuntu: Set Redis configuration attributes [no change: file /etc/redis/redis.conf already exists]
    ubuntu: Install Redis            [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install redis-server
    ubuntu: Ensure Redis is started and enabled [changed: redis-server.service not running (None), will start; redis-server.service not enabled, will enable]
        $ sudo systemctl start redis-server.service
        $ sudo systemctl enable redis-server.service
    barrier: configure done → 1 host notified
  wave 1
    ubuntu: Restart redis-server (handler) [changed: redis-server.service already running; redis-server.service will restart]
        $ sudo systemctl restart redis-server.service
```

The rerun — the checkpoint re-runs (it reads the wave, not a cached plan),
every request is a no-op, and **the handler wave disappears**:

```text
(root)
  wave 0
    ubuntu: Ensure Redis configuration directory [no change: directory /etc/redis already exists]
    ubuntu: Configure Redis          [no change: content already up to date (/etc/redis/redis.conf)]
    ubuntu: Set Redis configuration attributes [no change: file /etc/redis/redis.conf already exists]
    ubuntu: Install Redis            [no change: all packages present]
    ubuntu: Ensure Redis is started and enabled [no change: redis-server.service already running; redis-server.service already enabled]
    barrier: configure done → 0 hosts notified
```

Note the install order in the apply tree: the config (and its metadata) is on
disk *before* `apt-get install redis-server` runs — when the package's postinst
starts the daemon, it starts against the managed config. The mode-drift pass —
content untouched, stat mode pushed to `600` — isolates the companion's
purpose: `Configure Redis` stays `[no change: content already up to date]`
while `Set Redis configuration attributes [changed: …]` converges `0640` and
notifies the handler.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | `ansible_managed` is frozen to the literal `Ansible managed` | Ansible's ansible.cfg default is a *timestamped* string ("Ansible managed: `<file>` modified on `<date>`…"), which would rewrite the config on every run and fire the restart handler forever; the frozen header keeps it byte-stable (the php/memcached migration's precedent) |
| D2 | The `include_vars`/`set_fact` pair collapses to constants and one field | `__redis_package` becomes `RedisVars.package`'s Debian default — the README documents `redis_package` as user-overridable — while `redis_daemon`, the two paths and the two modes are `vars/Debian.yml` entries the role never documents as overrides, so they are frozen module constants |
| D3 | A `files.file` metadata companion is **added**, and its name **notifies** | `files.install`'s idempotence is content-only, so an owner/mode `0640` drift on unchanged content would never converge; Ansible's template module reports a change on owner/group/mode drift too, so the companion joins `NOTIFY_RESTART` (the java/memcached companion precedent) |
| D4 | The RedHat **and Archlinux** paths are scoped out | a Phase 0 single-OS decision: the target is Ubuntu (§9). `redis_enablerepo` is carried on the model — so an existing inventory binds — but is inert here; the repo knob belongs to the RHEL branch |
| D5 | Type gates move to `RedisVars`, and the family guard to the body's opening `if` | a wrongly typed variable is a pydantic rejection at `resolve_vars` time, before any host is contacted — but lax-mode int coercion (`"6380"` → `6380`) is *wider* than a strict `isinstance` gate, the repo-wide convention (memcached's `port` behaves the same); an off-target host emits nothing at all, no `when:` and no `[skipped]` lines |
| D6 | The config file **and directory** are managed as `redis:redis`, not the template task's implicit `root:root` | the packaged unit runs `User=redis`, so a root-owned `0640` config is unreadable to the daemon — proven on the live demo host, where a re-`chown` to root made the next handler restart fail with `Fatal error, can't open config file '/etc/redis/redis.conf': Permission denied`. `redis:redis` is also the end state the Ubuntu packaging itself converges to (`dpkg` re-owns `/etc/redis` on install), and the upstream role's owner-less template task never reverts it once the package has set it |

## 9. Scoped out

* **RedHat / CentOS branches** (`vars/RedHat.yml`: the `redis` user,
  `/etc/redis.conf`, `mode=0640`; `tasks/setup-RedHat.yml` with its
  `redis_enablerepo` dnf install): a Phase 0 single-OS decision — the target is
  Ubuntu. reemote's `dnf` and `systemd` modules cover the operations the RedHat
  path uses; it is simply outside the agreed target.
* **Archlinux branch** (`vars/Archlinux.yml`, `tasks/setup-Archlinux.yml`): the
  same decision; reemote ships no pacman module.
* **`redis_ver` / build-from-source** and the role's galaxy CI scaffolding:
  never part of the default task path.
--->
