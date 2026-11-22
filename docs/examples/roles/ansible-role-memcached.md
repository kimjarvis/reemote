# Migrating the Memcached Ansible role to reemote

This example migrates the [geerlingguy.memcached](https://github.com/geerlingguy/ansible-role-memcached)
Ansible role to a Python program built on the reemote library
([`ansible-role-memcached.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/ansible-role-memcached/ansible-role-memcached.py)).

The role installs Memcached, writes `/etc/memcached.conf` from an
OS-specific Jinja2 template (root:root, mode `0644`), and starts and
enables the service. A config change notifies the role's single handler,
`restart memcached`. The role has no loops, no command/shell tasks and no
consumed `register:` outcomes.

**This migration targets Ubuntu only.** Hosts in the `"Ubuntu"` OS family execute the deploy; every request carries the static `when`
guard `is_ubuntu`, so the other hosts in the shared `inventory.json`
(Alpine, RedHat) are skipped without being contacted. The RedHat branch
(`vars/RedHat.yml`, `memcached-RedHat.conf.j2`, the sysconfig destination)
is scoped out (§8). The vars split only by OS family, so the single-OS
scope collapses the `include_vars` dispatch to frozen module constants —
no `LinuxDistribution` gather is needed (the nginx precedent).

The migration is complete: every task on the Ubuntu default path maps to
a covered operation or a documented recipe; no default-path feature was
dropped.

## 1. Prerequisites

### 1.1 Packages that must be loaded before executing

| Package | Where | Why |
| --- | --- | --- |
| `reemote` | control machine | the library itself; a repo checkout installed by `uv sync` provides it |
| `jinja2` | control machine | renders the role's `memcached-Debian.conf.j2` template (an `is string` test and a for-loop). reemote deliberately ships **no** template engine, so Jinja2 is *not* a reemote dependency: the standalone run supplies it with `uv run --with jinja2`, and the repo's `dev` group declares it for the offline tests |
| `memcached` | target host | installed **by the program itself** via `apt.packages` — nothing to pre-load |

The render happens control-side in `main()` before any host is contacted;
Jinja2 is imported inside the render function, so importing the example
never requires it.

### 1.2 Control machine

- [uv](https://docs.astral.sh/uv/) and Python ≥ 3.13 (the project's
  `requires-python`).
- This repository, with dependencies synced: `uv sync` (the `dev` group
  carries `jinja2`).
- No internet egress is needed by the program itself; the *target* needs
  its usual apt mirrors to fetch the `memcached` package.

### 1.3 Target host

- Ubuntu (the role's meta lists precise through bionic; the Debian path
  is unchanged on newer releases), reachable over SSH as a user that can
  `sudo` (the inventory's `privilege` section carries any run-as
  user/password — never the program).
- `apt` and `systemd` — every supported Ubuntu release. The `memcache`
  user the daemon runs as is created by the package's own postinst, as
  in the role.

### 1.4 Inventory: what you must add

The program loads the project-root `inventory.json`. A target entry needs
`"group": "ubuntu"`; every role variable rides beside `host`/`connection`
as an extra key — reemote's `host_vars` mechanism (`host.extra`):

```json
{
  "host": "ubuntu",
  "family": "Ubuntu",
  "connection": {
    "address": "192.168.0.181",
    "username": "supervisor",
    "key_path": "~/.ssh/id_ed25519"
  },
  "group": "ubuntu",
  "tags": ["ubuntu_host", "apt_package_manager"],
  "memcached_memory_limit": 128
}
```

With no extra keys the role defaults apply (§3) — the entry above only
illustrates the shape. The shared `inventory.json` already carries a
matching `ubuntu` entry (without role variables, so every default
applies).

## 2. Running it against `inventory.json` with uv

From the repository root:

```bash
uv sync                                       # once: install reemote + dev deps
uv run python examples/roles/ansible-role-memcached/ansible-role-memcached.py
```

Standalone (outside a synced checkout), supply the renderer explicitly:

```bash
uv run --with jinja2 python examples/roles/ansible-role-memcached/ansible-role-memcached.py
```

`--with jinja2` supplies the renderer for the standalone run (it is not a
reemote dependency); inside the repo the `dev` group already provides it,
so plain `uv run python …` works.

The program:

1. loads and validates `inventory.json`, prints the hosts, and names the
   hosts the `is_ubuntu` guard skips;
2. resolves each target's role variables (defaults overlaid with the
   entry's extra keys) and fails fast on an invalid value — before any
   host is contacted;
3. renders `memcached-Debian.conf.j2` control-side with Ansible's Jinja2
   environment and freezes the content into a per-host plan;
4. runs the main deploy — the apt cache refresh, the `memcached`
   package, the config install plus its metadata companion (§6 D3), and
   the started-and-enabled service;
5. reads the apply report between the runs (`changed_hosts`) and runs
   the `restart memcached` handler deploy on the notified hosts only;
6. prints each deploy twice: the `dry=True` plan (Ansible's `--check` as
   a return value — nothing is mutated) followed by the real apply log.

**Run the whole program a second time** to see the idempotence claim: the
second pass reports no changes (the apt cache stamp is fresh, the package
is installed, the config content and metadata match, the service is
running and enabled) and the handler notification set is empty.

### Offline verification (no hosts, no approval needed)

```bash
uv run pytest tests/test_roles_ansible_role_memcached.py -q
```

The test replays the program's whole orchestration against the test
framework's `SimulatedHost` and proves plan → apply → **second apply is a
no-op with an empty handler set** (§7). `jinja2` comes from the `dev`
group here, so no `--with` is needed.

## 3. Role variables

Every `defaults/main.yml` variable is honoured under its original name,
read from the inventory entry's extra keys, plus the `memcached_user`
default the role's `set_fact` task derives from `vars/Debian.yml`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `memcached_user` | `memcache` (from `vars/Debian.yml`'s `__memcached_user`) | the user the daemon runs as (`-u`); reproduces `set_fact: memcached_user={{ __memcached_user }} when: memcached_user is not defined` — an explicit value wins |
| `memcached_port` | `11211` | listen port (`-p`) |
| `memcached_listen_ip` | `127.0.0.1` | a string renders one `-l` line; a list renders one `-l` line per address (the template's `is string` branch); an empty list renders none — memcached then listens on every interface |
| `memcached_memory_limit` | `64` | memory cap in MB (`-m`) |
| `memcached_connections` | `1024` | simultaneous connection cap (`-c`) |
| `memcached_log_file` | `/var/log/memcached.log` | daemon log file (`logfile`) |
| `memcached_log_verbosity` | `""` | `"-v"` / `"-vv"` enable logging; the empty default renders a blank line, exactly as the template does |
| `memcached_max_item_size` | `1m` | per-item memory limit (`-I`) |
| `memcached_threads` | `4` | thread count (`-t`) |

The config destination `/etc/memcached.conf` is the frozen
`vars/Debian.yml` value (`memcached_config_file`), not a variable the
Ubuntu path overrides.

Invalid values (a non-integer limit, a non-string log field, a
`memcached_listen_ip` that is neither a string nor a list of strings)
raise `TypeError` before any host is contacted.

## 4. Program shape: two deploys, one notification read

reemote runs a deploy twice (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence. The
template task's `notify: restart memcached` is an outcome read, which a
deploy body cannot take — so the handler migrates as the documented
two-deploy outcome split. No fact gather is needed at all: every value
the role consumes is static (defaults + `host.extra`), and the single-OS
scope freezes the `include_vars` dispatch to constants.

```
main()
 ├─ load + validate inventory.json; role_settings gates per target
 ├─ render_memcached_conf per host     Jinja2, Ansible's environment;
 │                                     frozen into {config_content}
 ├─ build_memcached(plans) — deploy 1:
 │    apt.update(cache_valid_time=86400)
 │    apt.packages(packages=("memcached",))
 │    files.install(config, mode="0644", user="root", group="root")   [notifies]
 │    files.file(config, mode="0644", user="root", group="root")      [notifies]
 │    systemd.service("memcached", running=True, enabled=True)
 ├─ changed_hosts(report, NOTIFY_RESTART)   the notification read,
 │                                          between the two runs
 └─ restart_handler(frozen_set, "memcached", when=is_ubuntu) — deploy 2:
      systemd.service("memcached", restarted=True)   [notified hosts only]
```

The barrier is load-bearing: the notification set is frozen **between**
the runs, so the handler deploy is a pure function of static input in
both of its phases. An empty set yields an empty deploy — the converged
no-op Ansible's handler gives for free. Deduplication and end-of-run
flushing come free too: at most one restart per host, after every
notifying operation has landed.

## 5. Coverage table

Status vocabulary: **covered** (direct operation), **recipe** (documented
shape change).

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 1 | `include_vars: "{{ ansible_facts.os_family }}.yml"` | frozen `DEBIAN_VARS` constants (single-OS collapse — no gather needed) | recipe |
| 2 | `set_fact: memcached_user={{ __memcached_user }}` when `memcached_user is not defined` | `role_settings` merges the vars-table default under `host.extra` | recipe |
| 3 | `apt: update_cache=yes cache_valid_time=86400` when `os_family == 'Debian'` | `apt.update(cache_valid_time=86400, sudo=True, when=is_ubuntu)` — gated on the `AptUpdateStamp` fact | covered |
| 4 | `package: name=memcached state=present` (`register: memcached_install`) | `apt.packages(packages=("memcached",), sudo=True, when=is_ubuntu)`; the register is never consumed → dropped (§6 D2) | covered |
| 5 | `template: memcached-Debian.conf.j2 dest={{ memcached_config_file }} owner=root group=root mode=0644` `notify: restart memcached` | control-side Jinja2 render + `files.install(mode="0644", user="root", group="root")` + `files.file` metadata companion; both names notify | recipe |
| 6 | `service: name=memcached state=started enabled=yes` | `systemd.service("memcached", running=True, enabled=True, sudo=True, when=is_ubuntu)` | covered |
| H | handler `restart memcached`: `service: name=memcached state=restarted` | `changed_hosts(report, NOTIFY_RESTART)` between runs → `restart_handler(frozen_set, "memcached", when=is_ubuntu)` | recipe |

## 6. Deliberate deviations

- **D1 — `ansible_managed` is frozen to `"Ansible managed"`.** Ansible's
  ansible.cfg default is a *timestamped* string ("Ansible managed:
  `<file>` modified on `<date>` by `<uid>` on `<host>`"), which would
  change the rendered content on every run — re-installing the config
  and firing the restart handler forever. The frozen default keeps the
  header byte-stable (the php migration's precedent).
- **D2 — the dead `register: memcached_install` is dropped.** No later
  task or `when:` in the role reads it; reemote reads outcomes between
  runs (`changed_hosts`), so there is nothing to register.
- **D3 — a `files.file` metadata companion is added, and it notifies.**
  `files.install`'s idempotence is content-only: mode/owner drift on an
  unchanged file would never converge. The companion enforces the
  template task's root:root `0644` on every run (the java/apache
  precedent), and its name joins `NOTIFY_RESTART` because Ansible's
  template module reports a change on owner/group/mode drift too.
- **D4 — the RedHat path is scoped out.** A Phase 0 single-OS decision:
  the target is Ubuntu (§8).
- **D5 — type gates fail fast.** Ansible renders whatever YAML gives it;
  the program raises `TypeError` on a wrongly typed variable before any
  host is contacted (reemote's hashability and render-sanity convention,
  the java precedent).

## 7. Offline verification

`tests/test_roles_ansible_role_memcached.py` imports the example by path
and replays its orchestration barrier-for-barrier against persistent
`SimulatedHost` objects. **No between-pass bridge is needed** — the
simulation's own mutators maintain every seam the second pass reads
(`install_apt_packages` records the package, `install_file` the config
content and its full stat row, `chmod_path`/`chown_path` the metadata
convergence, the `SystemctlCommand` arms the service state and
enablement, and the `AptUpdateCommand` arm refreshes `apt_update_stamp`
to wall-clock now). The load-bearing assertions: the second pass reports
`changed=False` everywhere **and** an empty handler notification set.
Also covered: the first pass's handler firing on the Ubuntu target only,
the golden default config bytes (including the blank line the empty
`memcached_log_verbosity` leaves), the `listen_ip` list and empty-list
branches, the `memcached_user`/`memcached_log_verbosity` overrides, the
metadata-drift companion convergence *with* notification, content-drift
re-install, the skipped non-Ubuntu host, the dry preview's zero mutation,
and the pure helpers' fail-fast gates.

## 8. Scoped out

- **RedHat / CentOS branches** (`vars/RedHat.yml`: the `memcached` user,
  the `/etc/sysconfig/memcached` destination;
  `templates/memcached-RedHat.conf.j2`): a Phase 0 single-OS decision —
  the target is Ubuntu. Reemote's `dnf` and `systemd` modules cover the
  operations the RedHat path uses; it is simply outside the agreed target.