# NTP Ansible role → reemote

This example migrates the [geerlingguy.ntp](https://github.com/geerlingguy/ansible-role-ntp)
Ansible role to a Python program built on the reemote library
([`ansible-role-ntp.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/ansible-role-ntp/ansible-role-ntp.py)).

The role installs an NTP daemon, sets the system timezone, disables the
competing `systemd-timesyncd` unit, brings the daemon up (or down) per
`ntp_enabled`, and optionally templates its configuration file. Two handlers
flush at the end of the play: `restart ntp` (notified by the config template)
and `restart cron` (notified by the timezone task).

**This migration targets Ubuntu only.** Hosts in the inventory group
`"ubuntu"` execute the deploy; every request carries the static `when` guard
`is_ubuntu`, so the other hosts in the shared `inventory.json` (the `centos`
group's `web1`/`web2` and the `alpine` host) are skipped without being
contacted. The guard reads `host.group`, not the tags — `web1`/`web2` carry an
`ubuntu_host` tag but sit in the `centos` group, so they are skipped. The
role's RedHat, Suse, Archlinux and FreeBSD branches are scoped out (§8).

The migration is **complete**: every task and handler on the Ubuntu path maps
to a covered operation or a documented recipe (§5). No feature was dropped and
no workaround was needed.

## 1. Prerequisites

### 1.1 Packages that must be loaded before executing

| Package | Where | Why |
| --- | --- | --- |
| `reemote` | control machine | the library itself; a repo checkout installed by `uv sync` provides it |
| `jinja2` | control machine | renders the role's `ntp.conf.j2` / `chrony.conf.j2` templates (loops and the `is sameas true` test). reemote deliberately ships **no** template engine, so Jinja2 is *not* a reemote dependency: the standalone run supplies it with `uv run --with jinja2`, and the repo's `dev` group declares it for the offline tests |
| `ntpsec`, `tzdata` | target host | installed **by the program itself** via `apt.packages` — nothing to pre-load |

No other control-side package is needed. The program reads the inventory with
the standard library's `json`, and the templates use only built-in Jinja2
syntax (the `sameas` test is built in), so **no PyYAML and no Ansible-specific
filter plugins are required**. The Ansible `comment` filter on the
`ansible_managed` header is avoided by freezing that header to its default
output (deviation D2).

### 1.2 Control machine

- [uv](https://docs.astral.sh/uv/) and Python ≥ 3.13 (the project's
  `requires-python`).
- This repository, with dependencies synced: `uv sync`.

### 1.3 Target host

- Ubuntu with systemd, reachable over SSH as a user that can `sudo` (the
  inventory's `privilege` section carries any run-as user/password — never the
  program). `timedatectl` must be present for the timezone task (it is on a
  stock systemd Ubuntu).
- The `ntpsec` and `tzdata` packages must be available from the configured apt
  repositories (the stock Ubuntu archive is enough).

### 1.4 Inventory: what you must add

The program loads the project-root `inventory.json`. A target entry needs
`"group": "ubuntu"`; every role variable rides beside `host`/`connection` as an
extra key — reemote's `host_vars` mechanism (`host.extra`):

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
  "ntp_manage_config": true,
  "ntp_timezone": "Etc/UTC",
  "ntp_cron_handler_enabled": true
}
```

With no extra keys the role defaults apply (§3) — the entry above only
illustrates the shape. The shared `inventory.json` already carries an `ubuntu`
entry; the role variables are optional overlays.

## 2. Running it against `inventory.json` with uv

From the repository root:

```bash
uv sync                                        # once: install reemote + dev deps
uv run --with jinja2 python examples/roles/ansible-role-ntp/ansible-role-ntp.py
```

`--with jinja2` supplies the renderer for the standalone run (it is not a
reemote runtime dependency). The program:

1. loads and validates `inventory.json`, prints the hosts, and names the hosts
   the `is_ubuntu` guard skips;
2. resolves each target's role variables (defaults overlaid with the entry's
   extra keys) and fails fast on an invalid value — before any host is
   contacted;
3. gathers `LinuxDistribution` and freezes a per-host plan (the
   `with_first_found` vars chain, the four `set_fact` overrides, the pool
   servers, and the rendered config when `ntp_manage_config` is true);
4. runs **deploy 1** (the NTP package + tzdata + the timezone task), gathers
   `SystemdUnitFiles` *after* the install (the role's `service_facts` probe),
   freezes the timesyncd-presence gate, runs **deploy 2** (timesyncd stop/disable,
   the daemon state, the config template), reads `changed_hosts` over the two
   notifying name sets, and runs **deploy 3** (the `restart ntp` / `restart cron`
   handlers);
5. prints each deploy twice: the `dry=True` plan (Ansible's `--check` as a return
   value — nothing is mutated) followed by the real apply log.

**Run the whole program a second time** to see the idempotence claim: the second
pass reports no changes and queues no handler.

### Offline verification (no hosts, no approval needed)

```bash
uv run pytest tests/test_roles_ansible_role_ntp.py -q
```

The test replays the program's whole orchestration against the test framework's
`SimulatedHost` and proves plan → apply → **second apply is a no-op** (§7).
`jinja2` comes from the `dev` group here, so no `--with` is needed.

## 3. Role variables

Every `defaults/main.yml` variable is honoured under its original `ntp_`-prefixed
name, read from the inventory entry's extra keys. The `vars/Ubuntu-24.04.yml` /
`vars/Debian.yml` values (byte-identical: the ntpsec daemon and paths) are frozen
constants selected by the gathered distribution; `vars/Debian-11.yml` (the legacy
`ntp` daemon) is reachable through the family-major chain level.

| Variable | Default | Meaning |
| --- | --- | --- |
| `ntp_enabled` | `true` | start + enable the daemon; `false` stops + disables it |
| `ntp_timezone` | `"Etc/UTC"` | system timezone (`server.timezone`); notifies `restart cron` |
| `ntp_manage_config` | `false` | render + install the config file; notifies `restart ntp` |
| `ntp_area` | `""` | pool zone prefix: `""` → worldwide, `"us"` → `us.pool.ntp.org` |
| `ntp_servers` | `null` → four pool strings | explicit server list; `null` derives them from `ntp_area` |
| `ntp_restrict` | `("127.0.0.1", "::1")` | `restrict` (ntp.conf) / `allow` (chrony.conf) lines |
| `ntp_cron_handler_enabled` | `false` | gate the `restart cron` handler |
| `ntp_tinker_panic` | `false` | ntp.conf `tinker panic 0` (reset a paused VM's clock) |
| `ntp_chrony_maxdistance` | `"3"` | chrony.conf `maxdistance` |
| `ntp_daemon` | `null` → vars table | `set_fact` override; the service unit name |
| `ntp_package` | `null` → vars table | `set_fact` override; the package name |
| `ntp_config_file` | `null` → vars table | `set_fact` override; the config dest (its basename selects the template) |
| `ntp_driftfile` | `null` → vars table | `set_fact` override; the ntp.conf `driftfile` |

`null` reproduces the role's commented-out defaults and its
`set_fact: … when: … is not defined` guard: the vars table decides unless the
inventory overrides it. `ntp_tzdata_package` (`tzdata`) and `ntp_cron_daemon`
(`cron`) come straight from `include_vars` with no `set_fact` guard, so they are
**not** overridable — faithful to the role's variable precedence.

Invalid values (a non-boolean flag, an empty string, a non-string list item)
raise `TypeError`/`ValueError` before any host is contacted.

## 4. Program shape: three deploys and two gathers

reemote runs a deploy twice (prepare against pre-state, apply against live state)
and both passes must yield the identical request sequence. The role reads facts
mid-play that a single deploy may not branch on: the distribution/version that
selects the vars file, and the post-install `service_facts` probe that decides
whether `systemd-timesyncd` is still present. Each becomes a
**gather → snapshot → factory** step between complete runs:

```
main()
 ├─ load + validate inventory.json; role_settings gates per target
 ├─ gather_fact(LinuxDistribution)     ← the include_vars with_first_found chain
 │    └─ ntp_plan: vars table + set_fact overrides + pool servers + config render
 ├─ deploy 1: build_install(plans)     apt.packages(ntpsec, update cache 600s)
 │                                     apt.packages(tzdata)
 │                                     server.timezone  → notifies restart cron
 ├─ gather_fact(SystemdUnitFiles)      ← AFTER the install (ntpsec can remove
 │    └─ freeze timesyncd presence       systemd-timesyncd, time-daemon conflict)
 ├─ deploy 2: build_configure(plans)   timesyncd stop/disable (when present)
 │                                     daemon running/enabled (or stopped/disabled)
 │                                     files.install(config) → notifies restart ntp
 ├─ changed_hosts(install report, NOTIFY_CRON)
 ├─ changed_hosts(configure report, NOTIFY_NTP)
 └─ deploy 3: build_handlers(plans, ntp_notified, cron_notified)
                                       restart ntp  (when ntp_enabled)
                                       restart cron (when ntp_cron_handler_enabled)
```

The barriers are load-bearing: the distribution gather runs **before** deploy 1
so the package/daemon names are frozen; the `SystemdUnitFiles` gather runs
**after** the install apply because installing ntpsec can remove
`systemd-timesyncd` (both conflict through the virtual package `time-daemon`,
upstream issue #135) — gathering before the install would read a stale presence.
The handler reads the apply reports of the two notifying deploys, exactly as
Ansible flushes handlers at the end of the play. The handlers are hand-built
rather than `service_handler` because the daemon names (`ntp_daemon` /
`ntp_cron_daemon`) are per-host overridable, while `service_handler` takes a fixed
service tuple.

## 5. Coverage table

Status vocabulary: **covered** (direct operation), **recipe** (documented shape
change). Every row maps.

### `tasks/main.yml`

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 1 | `include_vars` `with_first_found` (3-level chain) | `gather_fact(LinuxDistribution)` → `resolve_vars` over frozen `VARS_FILES` | recipe |
| 2 | `set_fact: ntp_driftfile` when not defined | `role_settings` `None` → vars table in `ntp_plan` | covered |
| 3 | `set_fact: ntp_package` when not defined | `role_settings` `None` → vars table in `ntp_plan` | covered |
| 4 | `set_fact: ntp_config_file` when not defined | `role_settings` `None` → vars table in `ntp_plan` | covered |
| 5 | `set_fact: ntp_daemon` when not defined | `role_settings` `None` → vars table in `ntp_plan` | covered |
| 6 | `package: name={{ ntp_package }} state=present` | `apt.packages(packages=…, update=True, cache_valid_time=600)` (D1) | covered |
| 7 | `package: name={{ ntp_tzdata_package }}` when `system == "Linux"` | `apt.packages(packages=("tzdata",))` — Linux statically true on Ubuntu | covered |
| 8 | `timezone: name={{ ntp_timezone }}` notify `restart cron` | `server.timezone(timezone=…)` named `"Set timezone."` | covered |
| 9 | `service_facts:` | `gather_fact(SystemdUnitFiles)` after the install apply (D4) | recipe |
| 10 | `service: systemd-timesyncd.service stopped/disabled` (3-condition `when`) | `systemd.service(running=False, enabled=False)` gated on frozen `enabled` + `timesyncd` (D4) | covered |
| 11 | `service: {{ ntp_daemon }} started/enabled` when `ntp_enabled`, `ignore_errors` | `systemd.service(running=True, enabled=True)` (D3) | covered |
| 12 | `service: {{ ntp_daemon }} stopped/disabled` when not `ntp_enabled` | `systemd.service(running=False, enabled=False)` | covered |
| 13 | `template: src={{ basename }}.j2 dest mode=0644` notify `restart ntp` when `ntp_manage_config` | control-side Jinja2 render + `files.install(content, mode="0644")` named `"Generate ntp configuration file."` (D2) | recipe |

### `handlers/main.yml` → deploy 3

| # | Ansible handler | reemote target | Status |
| --- | --- | --- | --- |
| 14 | `restart ntp`: `service {{ ntp_daemon }} state=restarted` when `ntp_enabled` | `changed_hosts(configure report, NOTIFY_NTP)` → `systemd.service(restarted=True)` gated on `enabled` | recipe |
| 15 | `restart cron`: `service {{ ntp_cron_daemon }} state=restarted` when `ntp_cron_handler_enabled` | `changed_hosts(install report, NOTIFY_CRON)` → `systemd.service(restarted=True)` gated on `cron_handler_enabled` | recipe |

## 6. Deliberate deviations from Ansible semantics

- **D1 — apt cache refresh folded in.** The role's `package` task carries no
  `update_cache`; its molecule converge playbook refreshes the index in a
  `pre_task` (`update_cache=true cache_valid_time=600`). Folded into the NTP
  install request so the example is self-contained on a fresh host. The refresh
  gates on the `AptUpdateStamp` fact against the controller's UTC clock, so a
  converged host with a fresh cache yields no update command.
- **D2 — managed header frozen.** `templates/*.j2` line 1 is
  `{{ ansible_managed | comment }}`. `ansible_managed` is an Ansible config
  variable and `comment` an Ansible-specific filter; reemote ships neither. The
  header is frozen to its default output `# Ansible managed`, leaving only
  built-in Jinja2 syntax. The rest of each template is verbatim, rendered with
  Ansible's whitespace defaults (`trim_blocks=True`, `lstrip_blocks=False`,
  `keep_trailing_newline=True`, `StrictUndefined`) — proven byte-identical to
  Ansible's render.
- **D3 — `ignore_errors` dropped.** The daemon service tasks carry
  `ignore_errors: "{{ ansible_check_mode }}"` (tolerate failures only in check
  mode). reemote's `dry=True` never applies commands, so the check-mode tolerance
  is moot; a real failure raises `CommandError`.
- **D4 — `service_facts` gate approximated, gathered post-install.** The role's
  three-condition `when` (`ntp_enabled` AND the unit in `ansible_facts.services`
  AND `status != "not-found"`) becomes a static gate: `ntp_enabled` is the frozen
  plan flag, and a unit's presence in `SystemdUnitFiles` (`list-unit-files`) is
  exactly "not `not-found`". The gather runs **after** the install apply, because
  ntpsec can remove `systemd-timesyncd` through the `time-daemon` conflict.
- **D5 — single-OS narrowing.** The role's `include_vars` dispatch spans many OS
  families; the migration ships only the Debian-family vars files the Ubuntu
  target can reach (`Ubuntu-24.04`, `Debian-11`, `Debian`). A host reporting
  another distribution is a hard error (`resolve_vars` raises) rather than
  selecting another OS's vars — the inventory group said `ubuntu` (the java
  migration's fail-fast precedent). The three-level `with_first_found` chain
  itself is preserved, because `Debian-11` differs from `Debian` (ntp vs ntpsec).
- **D6 — chrony template reachability.** `chrony.conf.j2` is bundled and
  dispatched on the config-file basename exactly as the role's
  `src: {{ ntp_config_file | basename }}.j2`. On the Ubuntu default path
  `ntp_config_file` is `/etc/ntpsec/ntp.conf`, so `ntp.conf.j2` is selected and
  the chrony template is unreachable; it is exercised only by an explicit
  `ntp_config_file` override to a `chrony.conf` path. An unknown basename raises,
  as Ansible's "template not found" does.

## 7. Offline verification

`tests/test_roles_ansible_role_ntp.py` imports the example by path (its file name
is not a valid module identifier), never calls its `main()`, and replays the
orchestration barrier-for-barrier against persistent `SimulatedHost` objects
(`host_factory` returns the *same* host every run, so pass 2 observes pass 1). It
proves:

- the first pass installs ntpsec + tzdata, refreshes the apt index, starts and
  enables the daemon, installs the rendered `ntp.conf` (root-owned 0644), and
  fires `restart ntp`;
- a non-Ubuntu host in the same inventory is never touched (every entry skipped,
  no state mutated);
- **the second pass changes nothing and queues no handler** — the idempotence
  claim, including the `cache_valid_time` window and the content-idempotent
  config write;
- the `dry=True` preview mutates nothing;
- the default path (`ntp_manage_config: false`) leaves the config unmanaged and
  fires no handler;
- the timezone change fires `restart cron` when `ntp_cron_handler_enabled` is
  true, and is suppressed when false;
- `ntp_enabled: false` stops + disables the daemon and suppresses `restart ntp`
  even though the config still notifies it;
- a surviving `systemd-timesyncd` is stopped + disabled, and an absent one skips
  the task;
- the chrony override renders `chrony.conf.j2`; the `set_fact` overrides win over
  the vars table; an unknown config basename raises;
- the pure helpers (`role_settings` gates, the `resolve_vars` chain,
  `default_servers`, the render functions) are unit-tested directly.

**One between-pass bridge** is needed, and it is a *simulation* limit, not a
library bug: a simulated apt install records package names only, never the unit
files a package ships or removes. `bridge_unit_files` seeds the post-install
`SystemdUnitFiles` state by hand (the daemon's unit appears; timesyncd is present
or gone per the scenario), so the role's post-install `service_facts` probe reads
what a real machine would have. The `systemd.service` runtime state needs no
bridge — the simulation's systemctl arms maintain `services`/`enabled_services`
across passes, which is what makes the second pass a no-op.

## 8. Scoped out

- The RedHat/CentOS, Suse, Archlinux and FreeBSD branches (their `vars/*.yml`
  files and any OS-specific tasks) — a Phase 0 single-OS decision. The
  `Debian-11` vars file is kept because the Ubuntu target's family-major
  chain level can reach it.
- The chrony default path: on Ubuntu the role selects ntpsec, so `chrony.conf.j2`
  is reachable only via an explicit `ntp_config_file` override (D6).
- Ansible check mode as an in-deploy concept (D3) — reemote's `dry=True` covers
  the use case.