# Migrating the Java Ansible role to reemote

This example migrates the [geerlingguy.java](https://github.com/geerlingguy/ansible-role-java)
Ansible role to a Python program built on the reemote library
([`ansible-role-java.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/ansible-role-java/ansible-role-java.py)).

The role installs the distribution's default OpenJDK package and
optionally exports `JAVA_HOME` through a `/etc/profile.d` script: the
version-keyed `vars/Ubuntu-<major>.yml` package selection, the `man1`
directory workaround (Debian bug 863199 / role issue #64), the `apt`
install, and the optional `java_home.sh` profile template. The role has
no handlers, no loops and no command/shell tasks.

**This migration targets Ubuntu only.** Hosts in the inventory group
`"ubuntu"` execute the deploy; every request carries the static `when`
guard `is_ubuntu`, so the other hosts in the shared `inventory.json`
(Alpine, CentOS) are skipped without being contacted. The RedHat, Amazon
and FreeBSD branches are scoped out (see §8). Unlike a role whose vars
split only by OS family, this role's Ubuntu vars split by *major version*
(`vars/Ubuntu-12.yml` through `vars/Ubuntu-26.yml`), so the single-OS
scope still uses the gather → snapshot → factory recipe: the major
version is gathered once and selects a frozen package table.

The migration is complete: every task on the Ubuntu default path maps to
a covered operation or a documented recipe; no default-path feature was
dropped.

## 1. Prerequisites

### 1.1 Packages that must be loaded before executing

| Package | Where | Why |
| --- | --- | --- |
| `reemote` | control machine | the library itself; a repo checkout installed by `uv sync` provides it |
| OpenJDK package | target host | installed **by the program itself** via `apt.packages` — nothing to pre-load |

**No extra control-side package is needed.** Unlike the DataDog migration
(PyYAML for `to_nice_yaml`) or the Apache migration (Jinja2 for
`vhosts.conf.j2`), this role's only template is a single
`export JAVA_HOME={{ java_home }}` line with no other Jinja2 syntax — a
plain f-string renders it byte-for-byte (trailing newline kept, the
Apache precedent's `keep_trailing_newline=True`). The program runs with
plain `uv run python …`, no `--with`.

### 1.2 Control machine

- [uv](https://docs.astral.sh/uv/) and Python ≥ 3.13 (the project's
  `requires-python`).
- This repository, with dependencies synced: `uv sync`.
- No internet egress is needed by the program itself; the *target* needs
  its usual apt mirrors to fetch the OpenJDK package.

### 1.3 Target host

- Ubuntu 12.04 through 26.04 (the releases the role ships vars for),
  reachable over SSH as a user that can `sudo` (the inventory's
  `privilege` section carries any run-as user/password — never the
  program). A release without a `vars/Ubuntu-<major>.yml` file fails fast
  with a clear error, exactly as the role's `include_vars` would.
- `apt` — every supported Ubuntu release. On a minimal image without
  `/usr/share/man/man1` the program creates it first (the role's
  openjdk-postinst workaround, Ubuntu ≥ 18).

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
  "java_home": "/usr/lib/jvm/java-21-openjdk-amd64"
}
```

With no extra keys the role defaults apply (§3) — the entry above only
illustrates the shape. The shared `inventory.json` already carries a
matching `ubuntu` entry (without `java_home`, so the profile template is
skipped, as in the role).

## 2. Running it against `inventory.json` with uv

From the repository root:

```bash
uv sync                                       # once: install reemote + dev deps
uv run python examples/roles/ansible-role-java/ansible-role-java.py
```

The program:

1. loads and validates `inventory.json`, prints the hosts, and names the
   hosts the `is_ubuntu` guard skips;
2. resolves each target's role variables (defaults overlaid with the
   entry's extra keys) and fails fast on an invalid value — before any
   host is contacted;
3. gathers `LinuxDistribution` once, selects the `vars/Ubuntu-<major>.yml`
   package list, freezes the ≥ 18 man-directory gate and renders the
   JAVA_HOME profile content (gather → snapshot → factory);
4. runs the single deploy — man1 directory (when gated), `apt.packages`
   (with the folded cache refresh, §6 D1), and the JAVA_HOME profile
   install + mode convergence (when `java_home` is set);
5. prints the deploy twice: the `dry=True` plan (Ansible's `--check` as a
   return value — nothing is mutated) followed by the real apply log.

**Run the whole program a second time** to see the idempotence claim: the
second pass reports no changes (the apt cache stamp is fresh, the package
is installed, the profile content and mode match).

### Offline verification (no hosts, no approval needed)

```bash
uv run pytest tests/test_roles_ansible_role_java.py -q
```

The test replays the program's whole orchestration against the test
framework's `SimulatedHost` and proves plan → apply → **second apply is a
no-op** (§7).

## 3. Role variables

Every `defaults/main.yml` variable on the Ubuntu path is honoured under
its original name, read from the inventory entry's extra keys.

| Variable | Default | Meaning |
| --- | --- | --- |
| `java_packages` | `null` → derived from the release | package list; `null` reproduces the role's `set_fact: java_packages={{ __java_packages \| list }} when: java_packages is not defined`. An explicit list (even `[]`) wins over the table |
| `java_home` | `""` | when non-empty, rendered into `/etc/profile.d/java_home.sh` (`export JAVA_HOME=<value>`); the empty default skips the template task, as the role's `when: java_home is defined and java_home != ""` does |

The frozen per-release defaults (the role's `vars/Ubuntu-<major>.yml`
`__java_packages` lists):

| Ubuntu major | Package |
| --- | --- |
| 12, 14 | `openjdk-7-jdk` |
| 16 | `openjdk-8-jdk` |
| 18, 20 | `openjdk-11-jdk` |
| 22 | `openjdk-17-jdk` |
| 24 | `openjdk-21-jdk` |
| 26 | `openjdk-25-jdk` |

Invalid values (a non-list `java_packages`, a non-string `java_home`, an
empty or non-string package name) raise before any host is contacted. An
unsupported Ubuntu major raises after the gather — the role's
`include_vars` fails with "file not found" the same way, even when
`java_packages` is overridden.

## 4. Program shape: one deploy, one barrier

reemote runs a deploy twice (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence. The
role's only mid-play read is the `include_vars` major-version dispatch,
which happens before any task; it has no handlers and no outcome reads.
So a single **gather → snapshot → factory** barrier suffices — no
multi-deploy split, no `changed_hosts` read:

```
main()
 ├─ load + validate inventory.json; role_settings gates per target
 ├─ gather_fact(LinuxDistribution)    major version → vars table selection
 │                                    (hosts closed manually afterwards)
 ├─ java_plan(host, info) per host    freeze {packages, man_dir,
 │                                    java_home_content}
 └─ build_java(plans) — one deploy:
      files.directory(/usr/share/man/man1, mode="0755")   [man_dir: major >= 18]
      apt.packages(packages, update=True, cache_valid_time=600)
      files.install(java_home.sh, mode="0644")            [java_home set]
      files.file(java_home.sh, mode="0644")               [java_home set]
```

The barrier is load-bearing: the major version is frozen **before** the
deploy so its request sequence is a pure function of frozen input. The
`files.file` companion converges the mode the template task enforces on
every run — `files.install`'s idempotence is content-only, so a mode-only
drift would not re-install (the Apache migration's `ports.conf`
precedent).

## 5. Coverage table

Status vocabulary: **covered** (direct operation), **recipe** (documented
shape change).

### `tasks/main.yml`

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 1 | `include_vars: Ubuntu-{{ major }}.yml` when `distribution == 'Ubuntu'` (the `os_family == 'Debian'` include re-loads the same file on Ubuntu — `"Ubuntu" \| title == "Ubuntu"`) | `LinuxDistribution` gather → `ubuntu_major` → frozen `UBUNTU_JAVA_PACKAGES` table → factory | recipe |
| 2 | `set_fact: java_packages={{ __java_packages \| list }}` when `java_packages is not defined` | `host.extra["java_packages"]` override merged in `role_settings`; `None` selects the table | recipe |
| 3 | `template: java_home.sh.j2 dest=/etc/profile.d/java_home.sh mode=0644` when `java_home != ""` | f-string render + `files.install(mode="0644")` + `files.file(mode="0644")` mode companion | recipe |

### `tasks/setup-Debian.yml`

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 4 | `file: path=/usr/share/man/man1 state=directory mode=0755` when Ubuntu ≥ 18 | `files.directory(mode="0755")`; the gate frozen into the plan | covered |
| 5 | `apt: name={{ java_packages }} state=present` | `apt.packages(packages=…, sudo=True)` (+ folded cache refresh, §6 D1) | covered |

The Fedora/FreeBSD, Amazon and RedHat `include_vars` branches and the
`setup-RedHat.yml` / `setup-Amazon.yml` / `setup-FreeBSD.yml` task files
are scoped out (§8). The role has no `handlers/main.yml`.

## 6. Deliberate deviations

- **D1 — the apt cache refresh is folded into the install request.** The
  role's `apt` task carries no `update_cache`; the role's molecule
  converge playbook refreshes the index in a `pre_task`
  (`update_cache=true cache_valid_time=600 changed_when=false`) before
  the role runs. The program folds that pre-task into
  `apt.packages(update=True, cache_valid_time=600)` so the example is
  self-contained on a fresh host. The refresh is gated on the
  `AptUpdateStamp` fact, so a converged host with a fresh cache yields no
  update command. Two nuances: the stamp age is computed against the
  *controller's* UTC clock (reemote's documented approximation of
  Ansible's target-side check), and a stale-cache refresh reports
  `changed=True` on the install request where the molecule pre-task
  forced `changed_when: false` — the refresh itself is identical.
- **D2 — the template is rendered with an f-string, not Jinja2.** The
  template body is exactly `export JAVA_HOME={{ java_home }}` plus a
  trailing newline; a plain f-string reproduces it byte-for-byte with no
  dependency. The trailing newline is kept (the Apache precedent's
  `keep_trailing_newline=True`); a renderer with Jinja2's *library*
  default (`keep_trailing_newline=False`) would strip it.
- **D3 — a non-Ubuntu host in the `ubuntu` group fails fast.** The role
  would dispatch to another OS's vars and tasks; this migration targets
  Ubuntu only, so `ubuntu_major` raises a clear error naming the reported
  distribution instead of silently deploying the wrong package list.
- **D4 — an unsupported Ubuntu major fails fast, even with an override.**
  Faithful to the role: `include_vars: Ubuntu-<major>.yml` runs
  unconditionally and fails with "file not found" before the
  `java_packages` override is ever consulted. The error names the
  supported majors.

## 7. Offline verification

`tests/test_roles_ansible_role_java.py` imports the example by path and
replays its orchestration barrier-for-barrier against persistent
`SimulatedHost` objects: the major version comes from the
`linux_distribution` seam, and **no between-pass bridge is needed** — the
simulation's own mutators maintain every seam the second pass reads
(`install_apt_packages` records the package, `make_directory`/`chmod_path`
the man1 row, `install_file` the profile content and stat row, and the
`AptUpdateCommand` arm refreshes `apt_update_stamp` to wall-clock now).
The load-bearing assertion: the second pass reports `changed=False`
everywhere. Also covered: the `java_packages` override, the empty-list
override, the Ubuntu 16 man1-gate branch, the skipped non-Ubuntu host,
the dry preview's zero mutation, and the pure helpers' fail-fast gates.

## 8. Scoped out

- **RedHat / CentOS / Rocky / Alma / OracleLinux branches**
  (`setup-RedHat.yml`, `vars/RedHat-*.yml`): a Phase 0 single-OS decision
  — the target is Ubuntu.
- **Amazon Linux branches** (`setup-Amazon.yml`, `vars/Amazon-*.yml`):
  same decision.
- **FreeBSD branches** (`setup-FreeBSD.yml`, `vars/FreeBSD.yml`: pkgng,
  `/proc` and `/dev/fd` mounts): same decision.
- **Fedora vars** (`vars/Fedora.yml`): same decision.

Reemote's `dnf`, `apk` and `server` modules cover the package/mount
operations these branches use; they are simply outside the agreed Ubuntu
target.