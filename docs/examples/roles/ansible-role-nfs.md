# Migrating the NFS Ansible role to reemote

This example migrates the [geerlingguy.nfs](https://github.com/geerlingguy/ansible-role-nfs)
Ansible role to a Python program built on the reemote library
([`ansible-role-nfs.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/ansible-role-nfs/ansible-role-nfs.py)).

The role installs the NFS server packages, creates the directories to
export, renders `/etc/exports` from a template, starts and enables the NFS
server when any export is configured, and re-exports the filesystems
(`exportfs -ra`) whenever the exports file changes. On the Ubuntu path
that is: the `vars/Debian.yml` service name, the `apt` install, the
mode-less directory loop, the `exports.j2` template with its
`notify: reload nfs`, the gated `service` task, and one **command**
handler.

**This migration targets Ubuntu only.** Hosts in the `"Ubuntu"` OS family execute the deploy; every request carries the static `when`
guard `is_ubuntu`, so the other hosts in the shared `inventory.json`
(Alpine, RedHat) are skipped without being contacted. The RedHat and
Fedora branches are scoped out (see §8). The role's vars split only by OS
family, so the single-OS scope collapses the `include_vars` dispatch to
frozen module constants — no fact gather is needed: the template consumes
only the static `nfs_exports` list.

The migration is complete: every task on the Ubuntu default path maps to
a covered operation or a documented recipe; no default-path feature was
dropped.

## 1. Prerequisites

### 1.1 Packages that must be loaded before executing

| Package | Where | Why |
| --- | --- | --- |
| `reemote` | control machine | the library itself; a repo checkout installed by `uv sync` provides it |
| `nfs-common`, `nfs-kernel-server` | target host | installed **by the program itself** via `apt.packages` — nothing to pre-load |

**No extra control-side package is needed.** Unlike the DataDog migration
(PyYAML for `to_nice_yaml`) or the Apache migration (Jinja2 for
`vhosts.conf.j2`), this role's only template is a static comment header
plus one line per export — a plain Python join renders it byte-for-byte
(verified against Jinja2 with Ansible's `trim_blocks=True`,
`lstrip_blocks=False`, `keep_trailing_newline=True` environment). The
program runs with plain `uv run python …`, no `--with`.

### 1.2 Control machine

- [uv](https://docs.astral.sh/uv/) and Python ≥ 3.13 (the project's
  `requires-python`).
- This repository, with dependencies synced: `uv sync`.
- No internet egress is needed by the program itself; the *target* needs
  its usual apt mirrors to fetch the NFS packages.

### 1.3 Target host

- Ubuntu (the Debian family path the role ships `vars/Debian.yml` and
  `tasks/setup-Debian.yml` for), reachable over SSH as a user that can
  `sudo` (the inventory's `privilege` section carries any run-as
  user/password — never the program).
- `apt` and `systemd` — every supported Ubuntu release. The handler's
  `exportfs` binary arrives with `nfs-common`, installed by the program
  before the handler can fire.

### 1.4 Inventory: what you must add

The program loads the project-root `inventory.json`. A target entry needs
`"group": "ubuntu"`; the role variable rides beside `host`/`connection`
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
  "nfs_exports": [
    "/srv/nfs 192.0.2.0/24(rw,sync,no_subtree_check)"
  ]
}
```

With no extra keys the role defaults apply (§3) — the entry above only
illustrates the shape. The shared `inventory.json` already carries a
matching `ubuntu` entry (without `nfs_exports`, so the exports file is
written header-only and the service task is skipped, as in the role).

## 2. Running it against `inventory.json` with uv

From the repository root:

```bash
uv sync                                       # once: install reemote + dev deps
uv run python examples/roles/ansible-role-nfs/ansible-role-nfs.py
```

The program:

1. loads and validates `inventory.json`, prints the hosts, and names the
   hosts the `is_ubuntu` guard skips;
2. resolves each target's role variables (defaults overlaid with the
   entry's extra keys) and fails fast on an invalid value — before any
   host is contacted;
3. freezes the per-host plan: the exports tuple, the deduplicated export
   directories, the rendered `/etc/exports` content and the service gate
   (no gather — every input is static inventory data);
4. runs the single deploy — `apt.packages` (with the folded cache
   refresh, §6 D1), one `files.directory` per export directory, the
   exports install + mode/owner convergence, and the gated service
   start/enable;
5. prints the deploy twice: the `dry=True` plan (Ansible's `--check` as a
   return value — nothing is mutated) followed by the real apply log;
6. reads the notification set (`changed_hosts`) and runs the handler —
   `exportfs -ra` via `gather_action(Shell)` — on the notified hosts only.

**Run the whole program a second time** to see the idempotence claim: the
second pass reports no changes (the apt cache stamp is fresh, the packages
are installed, the exports content and metadata match) and queues no
handler — `exportfs -ra` does not run again.

### Offline verification (no hosts, no approval needed)

```bash
uv run pytest tests/test_roles_ansible_role_nfs.py -q
```

The test replays the program's whole orchestration against the test
framework's `SimulatedHost` and proves plan → apply → **second apply is a
no-op** with an empty handler set (§7).

## 3. Role variables

Every `defaults/main.yml` variable on the Ubuntu path is honoured under
its original name, read from the inventory entry's extra keys.

| Variable | Default | Meaning |
| --- | --- | --- |
| `nfs_exports` | `[]` | export lines, each beginning with the directory to export (e.g. `"/srv/nfs 192.0.2.0/24(rw,sync,no_subtree_check)"`). The directories are created (mode untouched), the lines are rendered into `/etc/exports`, and a non-empty list starts and enables `nfs-kernel-server` |

The frozen Debian-family constants (the role's `vars/Debian.yml` and
`tasks/setup-Debian.yml`): service `nfs-kernel-server`, packages
`nfs-common` + `nfs-kernel-server`.

Invalid values (a non-list `nfs_exports`, a non-string, empty or
path-less entry) raise before any host is contacted — Ansible's
`map('first')` on an empty split yields Undefined and fails the `file`
task the same way. The `nfs_rpcbind_state` / `nfs_rpcbind_enabled`
defaults are consumed only by `setup-RedHat.yml` and are scoped out (§8).

## 4. Program shape: one deploy, one action gather

reemote runs a deploy twice (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence. Every
input this role reads is static inventory data, so the plan freezes
control-side with no fact gather at all. The role's one handler is a
**command** (`exportfs -ra`), not a systemd service action — a bare
command cannot live inside a deploy (`ActionInDeployError`), so the
handler is the two-deploy outcome split composed with the command-task
recipe:

```
main()
 ├─ load + validate inventory.json; is_ubuntu partition
 ├─ nfs_plan(host) per target       freeze {exports, directories,
 │                                  exports_content, start_service}
 ├─ build_nfs(plans) — one deploy:
 │    apt.packages(NFS_PACKAGES, update=True, cache_valid_time=600)
 │    files.directory(dir) per unique export directory   [mode unset]
 │    files.install(/etc/exports, 0644 root:root)        ─┐ notify
 │    files.file(/etc/exports, 0644 root:root)           ─┘ reload nfs
 │    systemd.service(nfs-kernel-server, running=True, enabled=True)
 │                                  [frozen gate: exports non-empty]
 ├─ changed_hosts(report, NOTIFYING)   the notification read between runs
 └─ reload_nfs(targets, changed)       gather_action(Shell,
                                       command="exportfs -ra", sudo=True)
                                       on notified hosts only
```

Two mapping decisions are load-bearing:

- **The handler is a `Shell` gather, not `reload_handler`.**
  `systemd.service(reloaded=True)` reloads only an *already-running*
  unit — a stopped one is started instead (the default `running=True`)
  and the reload verb is skipped. On the role's default path
  (`nfs_exports: []`) the service-start task is skipped, so
  `reload_handler` would start the unit the gate left stopped and never
  run `exportfs` — yet Ansible's `command: exportfs -ra` handler still
  runs whenever the exports file changes, with the unit stopped.
  `changed_hosts` freezes the notified hosts between runs and
  `gather_action` runs the command on exactly those hosts, once each,
  after every notifying request has landed (Ansible's end-of-play flush).
  A converged re-run freezes an empty set and gathers nothing. A non-zero
  exit is data in the `Result` TypedDict, re-raised as the typed
  `CommandError`. (The skill's non-systemd-handler guidance says to
  "yield your requests" from a hand-built handler deploy — impossible for
  a bare command; this gather is the faithful shape. Reported as skill
  feedback; every piece is public API.)
- **The template task becomes two notifying requests.**
  `files.install`'s idempotence is content-only, so the paired
  `files.file` converges the `mode: 0644 owner: root group: root` the
  template task enforces on every run (the Apache migration's
  `ports.conf` precedent). Both names sit in `NOTIFYING`: Ansible reports
  the template task changed on a content *or* metadata drift, and either
  queues `reload nfs`.

## 5. Coverage table

Status vocabulary: **covered** (direct operation), **recipe** (documented
shape change).

### `tasks/main.yml`

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 1 | `include_vars: "{{ ansible_facts.os_family }}.yml"` | frozen `NFS_SERVER_DAEMON` / `NFS_PACKAGES` constants (Ubuntu is Debian-family; single-OS collapse, no gather) | recipe |
| 2 | `include_vars: Fedora.yml` when RedHat+Fedora | scoped out (§8) | — |
| 3 | `include_tasks: setup-RedHat.yml` when RedHat | scoped out (§8) | — |
| 4 | `include_tasks: setup-Debian.yml` when Debian | the Ubuntu path's install (row 8) | covered |
| 5 | `file: path={{ item }} state=directory` over `nfs_exports \| map('split') \| map('first') \| unique` | `files.directory(path=dir)` per directory, mode deliberately unset (the role's ansible-lint noqa 208 exemption): create when absent, leave existing permissions untouched | covered |
| 6 | `template: exports.j2 dest=/etc/exports owner=root group=root mode=0644 notify: reload nfs` | `render_exports` join + `files.install(mode="0644", user="root", group="root")` + `files.file` metadata companion; both notify | recipe |
| 7 | `service: name={{ nfs_server_daemon }} state=started enabled=yes` when `nfs_exports\|length > 0` | `systemd.service(running=True, enabled=True)` gated on the frozen `start_service` bool | covered |

### `tasks/setup-Debian.yml`

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 8 | `apt: name=[nfs-common, nfs-kernel-server] state=present` | `apt.packages(packages=NFS_PACKAGES, sudo=True)` (+ folded cache refresh, §6 D1) | covered |

### `handlers/main.yml`

| # | Ansible handler | reemote target | Status |
| --- | --- | --- | --- |
| 9 | `reload nfs: command: 'exportfs -ra'` | `changed_hosts(report, NOTIFYING)` → `gather_action(Shell, command="exportfs -ra", sudo=True)` on notified hosts only (§4) | recipe |

## 6. Deliberate deviations

- **D1 — the apt cache refresh is folded into the install request.** The
  role's `apt` task carries no `update_cache`; the role's molecule
  converge playbook refreshes the index in a `pre_task`
  (`update_cache=yes cache_valid_time=600 changed_when=false`) before
  the role runs. The program folds that pre-task into
  `apt.packages(update=True, cache_valid_time=600)` so the example is
  self-contained on a fresh host. The refresh is gated on the
  `AptUpdateStamp` fact, so a converged host with a fresh cache yields no
  update command. Two nuances: the stamp age is computed against the
  *controller's* UTC clock (reemote's documented approximation of
  Ansible's target-side check), and a stale-cache refresh reports
  `changed=True` on the install request where the molecule pre-task
  forced `changed_when: false` — the refresh itself is identical.
- **D2 — the template is rendered with a Python join, not Jinja2.** The
  template body is a ten-line static comment header plus
  `{% for export in nfs_exports %}{{ export }}{% endfor %}`; with
  Ansible's `trim_blocks=True` the block tags' own newlines vanish, so
  the render is the header plus one newline-terminated line per export.
  The join reproduces it byte-for-byte (verified against Jinja2 with
  Ansible's environment, including `keep_trailing_newline=True`), with
  no dependency.

The handler mapping (§4) is *not* a deviation from Ansible semantics —
it runs the same command under the same condition. It is a deviation
from the skill's suggested shape for non-systemd handlers, reported as
guidance feedback.

## 7. Offline verification

`tests/test_roles_ansible_role_nfs.py` imports the example by path and
replays its orchestration barrier-for-barrier against persistent
`SimulatedHost` objects: the plan comes from static `host_vars`, the
handler's `exportfs -ra` is answered by the seeded `shell_results` seam
(keyed by its exact `sudo` rendering), and **no between-pass bridge is
needed** — the simulation's own mutators maintain every seam the second
pass reads (`install_apt_packages`, the `AptUpdateCommand` stamp refresh,
`make_directory`, `install_file`'s content and stat rows, the systemctl
arms). The load-bearing assertions: the second pass reports
`changed=False` everywhere, the notification set is empty, and
`ran_shells` holds exactly one `sudo exportfs -ra` across both passes.
Also covered: the default path's handler-fires-while-service-stopped
case, the configured-exports branch (directory dedup/order, mode-less
existing directory untouched, service started+enabled), the metadata
drift convergence (install no-op, companion changes, handler queues),
a failed `exportfs` raising `CommandError`, the skipped non-Ubuntu host,
the dry preview's zero mutation, and the pure helpers' fail-fast gates.

## 8. Scoped out

- **RedHat / CentOS / Fedora branches** (`setup-RedHat.yml`,
  `vars/RedHat.yml`, `vars/Fedora.yml`, the Fedora override include, and
  the `nfs_rpcbind_state` / `nfs_rpcbind_enabled` defaults they
  consume): a Phase 0 single-OS decision — the target is Ubuntu.

Reemote's `dnf` and `systemd` modules cover the package/service operations
these branches use; they are simply outside the agreed Ubuntu target.