# memcached — Memcached cache role

| | |
| --- | --- |
| Role | [`geerlingguy.memcached`](https://github.com/geerlingguy/ansible-role-memcached) |
| Module | [`reemote.roles.memcached`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/memcached.py) |
| Runner | [`examples/roles/memcached.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/memcached.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | `memcached_`-prefixed inventory keys → `MemcachedVars` |

<!---
| Waves | `2` on a changing pass (requests, then handler), `1` once converged — one checkpoint, no facts |
| Outputs | none — the deploy returns `None`; no other role reads memcached's results |
| Extra deps | **`reemote[templates]`** (jinja2 renders `memcached-Debian.conf.j2`) |
| Offline test | [`tests/roles/test_memcached.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_memcached.py) |
| Factory | plain (non-`async`) — the runner calls `memcached(v)`; the render is control-side Python inside the body, not a barrier |

`memcached` installs the `memcached` package on Ubuntu hosts, writes
`/etc/memcached.conf` from the role's Debian Jinja2 template (root:root, mode
`0644`), and starts and enables the service; a config change queues the role's
one handler, `restart memcached`. The role has no loops, no command/shell tasks
and no facts at all.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with `yield from scoped("memcached", memcached(v))(host)`
and a run is one `run_waves` pass. The Ansible role's whole task list *and* its
handler are that single deploy, and its `defaults/main.yml` is `MemcachedVars`.

This is the corpus's **checkpoint-only role** (design doc §18.13): the barrier
that reads the handler notification is the only barrier in the stream, so the
whole role is two waves — one for the requests, one for the handler — and a
converged run collapses to one. The old example's two-`run()` split and its
per-host `plans` dict are gone: the notification is an in-stream checkpoint
read. The stream is:

```python
yield from configure(memcached_plan(hv))(host)   # wave 0 — five requests
wave_report = yield checkpoint("configure done") # the role's only barrier
notified = changed_hosts(wave_report, NOTIFY_RESTART)
yield from handler(notified)(host)               # wave 1 — the restart, or nothing
```

## 1. What it is, and where it came from

The role's tasks — and its handler — map onto reemote requests and one barrier:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `include_vars: "{{ ansible_facts.os_family }}.yml"` | frozen `vars/Debian.yml` constants (`CONFIG_FILE`, `DEFAULT_USER`), the single-OS collapse needs no gather | recipe |
| `set_fact: memcached_user={{ __memcached_user }}` when not defined | `MemcachedVars.user` defaults to `DEFAULT_USER`; an explicit `memcached_user` key wins, as the extra key won over the `set_fact` | recipe |
| `apt: update_cache=yes cache_valid_time=86400` | `apt.update(cache_valid_time=86400)`, gated on the `AptUpdateStamp` fact | covered |
| `package: name=memcached state=present` (`register: memcached_install`) | `apt.packages(packages=("memcached",))`; the register is never consumed → dropped (D2) | covered |
| `template: memcached-Debian.conf.j2 dest=… mode=0644` `notify: restart memcached` | control-side Jinja2 render → `files.install(mode="0644", user="root", group="root")` + `files.file` metadata companion; both names notify | recipe |
| `service: name=memcached state=started enabled=yes` | `systemd.service(service="memcached", running=True, enabled=True)` | covered |
| `handlers/main.yml: restart memcached` (`service: state=restarted`) | `wave_report = yield checkpoint("configure done")` → `changed_hosts(…, NOTIFY_RESTART)` → `restart_handler(notified, "memcached")` | barrier |

No library gap blocks this migration — the audit filed zero tickets. The role
reads nothing mid-play, so there is no `fact`/`act` barrier to place and the
checkpoint is the entire barrier surface.

## 2. Install

```bash
pip install "reemote[templates]"   # or: uv add "reemote[templates]"
```

`jinja2` is the only extra: the config is rendered from the role's bundled
`memcached-Debian.conf.j2` (an `is string` test and a for-loop), which a plain
f-string cannot reproduce. The import enters only through
`reemote.roles._render`, *inside* `render_memcached_conf`, so
`import reemote.roles` stays clean without the extra and a missing `jinja2`
surfaces at render time rather than at import time.

SSH access to each Ubuntu host with the key named in its `connection`, and
passwordless sudo (or a `privilege` section in the inventory): every request
carries `sudo=True`, because reemote has no play-level `become`. The config
write is a spool-and-install (`files.install`), so the transfer itself never
elevates.
--->

::: reemote.roles.memcached
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - memcached

**Variables:**

### Role variables (`defaults/main.yml`)

| Field | Type | Default | Inventory key |
| --- | --- | --- | --- |
| `user` | `str` | `memcache` — from `vars/Debian.yml`'s `__memcached_user`, the `set_fact` default | `memcached_user` |
| `port` | `int` | `11211` | `memcached_port` |
| `listen_ip` | `str \| tuple[str, …]` | `127.0.0.1` — a string renders one `-l` line; a sequence renders one per address; an empty one none | `memcached_listen_ip` |
| `memory_limit` | `int` | `64` (MB, `-m`) | `memcached_memory_limit` |
| `connections` | `int` | `1024` (`-c`) | `memcached_connections` |
| `log_file` | `str` | `/var/log/memcached.log` | `memcached_log_file` |
| `log_verbosity` | `str` | `""` — `-v`/`-vv` enable logging; the empty default renders a blank line, as the template does | `memcached_log_verbosity` |
| `max_item_size` | `str` | `1m` (`-I`) | `memcached_max_item_size` |
| `threads` | `int` | `4` (`-t`) | `memcached_threads` |

<!---
Precedence is `model defaults < inventory extra < overrides`. Validation is
fail-fast at `resolve_vars`, before any host is contacted: `user`, `log_file`
and `max_item_size` must be non-empty strings; `listen_ip` accepts a string or
a list/tuple (normalized to a `tuple`, the hashability rule) of non-empty
strings; a wrongly typed value is a pydantic `ValidationError` — a
`ValueError`, so the runner maps it. An unprefixed misspelling such as
`memcached_packagez` raises `RoleVarsError` naming the valid keys. Plain `int`
fields keep pydantic's lax coercion — `"11211"` is accepted as `11211` — the
repo-wide convention (apache's `listen_port` behaves the same) and the render
is identical either way (D5).

## 5. Run it

The role is library code; `examples/roles/memcached.py` is the runnable
artifact — a standalone program with its own `main()`, no shared runner. It
loads and validates `inventory.json` from the current working directory,
resolves the role's variables (the defaults overlaid with the entry's
`memcached_`-prefixed keys, then the call-site `overrides`), builds the deploy
from the `memcached` factory and drives it with `run_waves` — a `dry=True`
preview, then the real apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import MemcachedVars, memcached

MEMCACHED_OVERRIDES = {"memory_limit": 128}

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(
        MemcachedVars, "memcached_", inventory, overrides=MEMCACHED_OVERRIDES
    )
    deploy = memcached(vars_set)                                            # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)           # parks at the checkpoint
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)                     # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/memcached.py
```

The call-site `MEMCACHED_OVERRIDES` raises `memory_limit` to `128`, so the
demonstration writes a config that visibly differs from the packaged default
and the `restart memcached` handler has something to fire on. The `dry=True`
pass is *not* empty here: the role's first items are requests, so the dry run
plans all five configure requests and then parks on the checkpoint barrier
(`barrier: configure done → unknown until run`) — the notification read needs
a real apply report, so the handler wave stays unplanned, exactly like
Ansible's `--check` skipping handlers. Nothing is applied.

**Run the program twice.** The second run must report no change: the apt cache
stamp is fresh, the package is installed, the config is byte-identical with
root:root `0644` metadata, the service is running and enabled — and the
checkpoint notifies nobody, so the handler wave never appears. That is the
migration's idempotence claim, and the offline suite asserts it.

## 6. What it does

One deploy. The checkpoint closes the request wave, and a wave with no records
is not appended — so the shape below is what the test pins:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `configure`: `apt.update(cache_valid_time=86400)`, `apt.packages(("memcached",))`, `files.install(/etc/memcached.conf, mode="0644")` + `files.file(mode="0644")` companion, `systemd.service(running=True, enabled=True)` — then `checkpoint("configure done")` | every input is a resolved variable or a frozen constant, so the whole task list is one pure request wave; the checkpoint reads its outcome |
| 1 | `handler`: `restart_handler(notified, "memcached")` — a request only on the hosts the checkpoint selected | the role's one `notify:` channel; an empty notification set emits no request, so a converged pass has no wave 1 at all |

Four rules are load-bearing:

* **The checkpoint is the only barrier — and there are no facts at all.** The
  vars split by OS family, not release, so the single-OS scope collapses
  `include_vars` to frozen constants and no `LinuxDistribution` read survives
  (the nginx precedent, unlike java). A `fact` or `act` barrier here would be
  speculatively added, and **three waves** on a changing pass are the same
  signal (§18.13).
* **Two waves total — one for the requests, one for the handler.** The
  checkpoint must immediately follow the stage it reports on (§4.3); the
  handler wave's entries would be invisible to a second checkpoint, and
  nothing after the restart reads a report.
* **The companion is a first-pass no-op by design, and it notifies.**
  `files.install` writes content *and* metadata, so `files.file` changes
  nothing until a later pass finds drift: content byte-identical but mode
  `0600` → install reports no change (its idempotence is content-only) and the
  companion re-applies `0644` — and, like Ansible's template module reporting
  a change on owner/group/mode drift, its notification fires the restart too
  (D3). The offline test pins both directions of the drift pair.
* **The handler entry is the library's generated name.** The log line is
  `Restart memcached (handler)` — `restart_handler`'s default label — not a
  request the role names, and `changed_hosts` matches the *notifying* names
  (`Copy Memcached configuration`, `Set Memcached configuration attributes`)
  exactly; the package and service tasks carry no `notify` in the role, so
  their changes never restart the daemon.

A host whose `family` is not `"Ubuntu"` returns before its first `yield`: no
requests, no barriers, no report lines — Alpine and RedHat entries in a shared
inventory are never contacted.

## 7. Transcript

From `tests/roles/test_memcached.py`, against the simulated Ubuntu host
(`memory_limit` overridden to `128`) plus a RedHat host the role never
touches. No between-pass bridge is needed — the simulation's own mutators
maintain every seam the second pass reads. Fresh host, first pass:

```text
(root)
  wave 0
    ubuntu: Update apt cache         [changed: apt update]
    ubuntu: Install Memcached        [changed: 1 package(s) to install]
    ubuntu: Copy Memcached configuration [changed: content /etc/memcached.conf is absent, will install]
    ubuntu: Set Memcached configuration attributes [no change: file /etc/memcached.conf already exists]
    ubuntu: Ensure Memcached is started and enabled [changed: memcached.service not running (None), will start; memcached.service not enabled, will enable]
    barrier: configure done → 1 host notified
  wave 1
    ubuntu: Restart memcached (handler) [changed: memcached.service already running; memcached.service will restart]
```

The rerun — the checkpoint re-runs (it reads the wave, not a cached plan),
every request is a no-op, and **the handler wave disappears**:

```text
(root)
  wave 0
    ubuntu: Update apt cache         [no change: apt update skipped, cache is fresh]
    ubuntu: Install Memcached        [no change: all packages present]
    ubuntu: Copy Memcached configuration [no change: content already up to date (/etc/memcached.conf)]
    ubuntu: Set Memcached configuration attributes [no change: file /etc/memcached.conf already exists]
    ubuntu: Ensure Memcached is started and enabled [no change: memcached.service already running; memcached.service already enabled]
    barrier: configure done → 0 hosts notified
```

The mode-drift pass — content untouched, stat mode pushed to `600` — isolates
the companion's purpose: `Copy Memcached configuration` stays
`[no change: content already up to date]` while
`Set Memcached configuration attributes [changed: …]` converges `0644` and
notifies the handler.

On the live Ubuntu demo host — which already ran memcached, with a config that
differed from the rendered one — the apply took exactly the changing path, and
the second program run collapsed to one wave:

```text
(root)
  wave 0
    ubuntu: Update apt cache         [no change: apt update skipped, cache is fresh]
    ubuntu: Install Memcached        [no change: all packages present]
    ubuntu: Copy Memcached configuration [changed: content /etc/memcached.conf differs, will install]
        $ scp <files install> -> /tmp/reemote-install-2b9c5365d0f781d7/content
        $ sudo sh -c 'mkdir -p /etc && cp /tmp/reemote-install-2b9c5365d0f781d7/content /etc/memcached.conf && chown root:root /etc/memcached.conf && chmod 644 /etc/memcached.conf && rm -rf /tmp/reemote-install-2b9c5365d0f781d7'
    ubuntu: Set Memcached configuration attributes [no change: file /etc/memcached.conf already exists]
    ubuntu: Ensure Memcached is started and enabled [no change: memcached.service already running; memcached.service already enabled]
    barrier: configure done → 1 host notified
  wave 1
    ubuntu: Restart memcached (handler) [changed: memcached.service already running; memcached.service will restart]
        $ sudo systemctl restart memcached.service
```

```text
(root)
  wave 0
    ubuntu: Update apt cache         [no change: apt update skipped, cache is fresh]
    ubuntu: Install Memcached        [no change: all packages present]
    ubuntu: Copy Memcached configuration [no change: content already up to date (/etc/memcached.conf)]
    ubuntu: Set Memcached configuration attributes [no change: file /etc/memcached.conf already exists]
    ubuntu: Ensure Memcached is started and enabled [no change: memcached.service already running; memcached.service already enabled]
    barrier: configure done → 0 hosts notified
```

The RedHat host appears in neither tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | `ansible_managed` is frozen to the literal `Ansible managed` | Ansible's ansible.cfg default is a *timestamped* string ("Ansible managed: `<file>` modified on `<date>`…"), which would rewrite the config on every run and fire the restart handler forever; the frozen header keeps it byte-stable (the php migration's precedent) |
| D2 | The package task's `register: memcached_install` is dropped | no later task or `when:` reads it; reemote reads outcomes at the checkpoint, so there is nothing to register |
| D3 | A `files.file` metadata companion is **added**, and its name **notifies** | `files.install`'s idempotence is content-only, so a root:root `0644` drift on unchanged content would never converge; Ansible's template module reports a change on owner/group/mode drift too, so the companion joins `NOTIFY_RESTART` (the java/apache companion precedent) |
| D4 | The RedHat path is **scoped out** | a Phase 0 single-OS decision: the target is Ubuntu (§9) |
| D5 | Type gates move to `MemcachedVars`, and the family guard to the body's opening `if` | a wrongly typed variable is a pydantic rejection at `resolve_vars` time, before any host is contacted — but lax-mode int coercion (`"11211"` → `11211`) is *wider* than the old example's `isinstance` gate, the repo-wide convention (apache's `listen_port` behaves the same); an off-target host emits nothing at all, no `when:` and no `[skipped]` lines |

## 9. Scoped out

* **RedHat / CentOS branches** (`vars/RedHat.yml`: the `memcached` user, the
  `/etc/sysconfig/memcached` destination;
  `templates/memcached-RedHat.conf.j2`): a Phase 0 single-OS decision — the
  target is Ubuntu. reemote's `dnf` and `systemd` modules cover the operations
  the RedHat path uses; it is simply outside the agreed target.
--->
