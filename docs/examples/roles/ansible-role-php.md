# PHP Ansible role → reemote

This example migrates the [geerlingguy.php](https://github.com/geerlingguy/ansible-role-php)
Ansible role to a Python program built on the reemote library
([`ansible-role-php.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/ansible-role-php/ansible-role-php.py)).

The role installs and configures PHP on RHEL/CentOS and Debian/Ubuntu: the
distribution packages, the packaged APCu/OpCache ini cleanup, the
configuration directories and a rendered `php.ini` in every conf path, the
APCu/OpCache stray-config sweep and the role's own extension ini files, the
optional PHP-FPM pools and service, and two chained handlers
(`restart webserver` → `restart php-fpm`).

**This migration targets Ubuntu only.** Hosts in the `"Ubuntu"` OS family execute the deploy; every request carries the static `when` guard
`is_ubuntu`, so the other hosts in the shared `inventory.json` (Alpine, RedHat) are skipped without being contacted. The role's `include_vars` OS
dispatch collapses to a frozen Ubuntu version table, and the RHEL/CentOS
branch and the opt-in `php_install_from_source` path are scoped out (see §8).

The migration is complete: every task on the Ubuntu default path maps to a
covered operation or a documented recipe; nothing blocks the migration and
no feature was dropped.

## 1. Prerequisites

### 1.1 Packages that must be loaded before executing

| Package | Where | Why |
| --- | --- | --- |
| `reemote` | control machine | the library itself; a repo checkout installed by `uv sync` provides it |
| PHP packages (`php8.3-*` on Ubuntu 24.04, …) | target host | installed **by the program itself** via `apt.packages` — nothing to pre-load |

**reemote is the only control-side package.** No template engine is needed:
every default-path template (`php.ini.j2`, `apc.ini.j2`, `opcache.ini.j2`,
`www.conf.j2`) is pure variable substitution plus one `join` and two
`{% if %}` blocks, so the program renders them with plain Python f-strings.
No Jinja2, no PyYAML — the inventory is read with the standard library's
`json`, and the role defaults are frozen module constants. The renders were
verified byte-identical to Jinja2 renders of the role's actual templates
under Ansible's environment defaults (`trim_blocks=True`, trailing newlines
preserved) across a 15-scenario matrix.

### 1.2 Control machine

- [uv](https://docs.astral.sh/uv/) and Python ≥ 3.13 (the project's
  `requires-python`).
- This repository, with dependencies synced: `uv sync`.

### 1.3 Target host

- Ubuntu with systemd, reachable over SSH as a user that can `sudo` (the
  inventory's `privilege` section carries any run-as user/password — never
  the program).
- The PHP packages must be available from the configured apt repositories
  (the stock Ubuntu archive is enough: 20.04 → PHP 7.4, 22.04 → 8.1,
  24.04 → 8.3; any other major falls back to the role's Debian 8.2 table).

### 1.4 Inventory: what you must add

The program loads the project-root `inventory.json`. A target entry needs
`"group": "ubuntu"`; every role variable rides beside `host`/`connection` as
an extra key — reemote's `host_vars` mechanism (`host.extra`):

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
  "php_enable_php_fpm": true,
  "php_memory_limit": "512M",
  "php_fpm_pm_max_children": 25,
  "php_fpm_pm_status_path": "/status"
}
```

With no extra keys the role defaults apply (§3) — the entry above only
illustrates the shape.

## 2. Running it against `inventory.json` with uv

From the repository root:

```bash
uv sync                                        # once: install reemote + dev deps
uv run python examples/roles/ansible-role-php/ansible-role-php.py
```

No `--with` is needed — reemote is the only dependency (§1.1). The program:

1. loads and validates `inventory.json`, prints the hosts, and names the
   hosts the `is_ubuntu` guard skips;
2. resolves each target's role variables (defaults overlaid with the entry's
   extra keys) and fails fast on an invalid value — before any host is
   contacted;
3. gathers `LinuxDistribution` (the major version selects the PHP version
   table), then runs **deploy 1** (apt cache refresh + packages), reads the
   package-install outcome with `changed_hosts`, runs **deploy 2** (the
   packaged-ini cleanup, only on hosts whose install changed), gathers the
   stray-config sweeps (`FindInFiles`) and the pool-directory existence
   (`FileExists`), renders every template control-side, runs **deploy 3**
   (configuration + FPM service state), reads `changed_hosts` over the
   notifying request names across all three reports, and runs **deploy 4**
   (the chained `restart webserver` → `restart php-fpm` handlers);
4. prints each deploy twice: the `dry=True` plan (Ansible's `--check` as a
   return value — nothing is mutated) followed by the real apply log, then
   the handler notification sets and the total changed count.

**Run the whole program a second time** to see the idempotence claim: the
second pass reports no changes and queues no handler.

### Offline verification (no hosts, no approval needed)

```bash
uv run pytest tests/test_roles_ansible_role_php.py -q
```

The test replays the program's whole orchestration against the test
framework's `SimulatedHost` and proves plan → apply → **second apply is a
no-op** (§7).

## 3. Role variables

Every `defaults/main.yml` variable is honoured under its original
`php_`-prefixed name, read from the inventory entry's extra keys. The
`vars/Debian.yml` values are resolved by `resolved_vars()`; the
`vars/Ubuntu-{20,22,24}.yml` version overrides are selected by the gathered
distribution major.

### Packages and webserver

| Variable | Default | Meaning |
| --- | --- | --- |
| `php_packages` | `null` → the version-specific list | package list; `null` reproduces the role's `__php_packages` fallback (13 packages, plus `php{v}-json` when PHP < 8.0) |
| `php_packages_extra` | `[]` | extra packages appended to the list |
| `php_packages_state` | `"present"` | `present`/`latest`/`absent`; `latest` gates on `AptUpgradable` |
| `php_install_recommends` | `true` | `false` adds `--no-install-recommends` |
| `php_default_version_debian` | `null` → per-Ubuntu table | overrides the version selection (7.4 / 8.1 / 8.3, else 8.2) |
| `php_enable_webserver` | `true` | whether the `restart webserver` handler fires |
| `php_webserver_daemon` | `null` → `"apache2"` | the webserver service the handler restarts |
| `php_enablerepo` | `""` | accepted for parity, **inert** — a RHEL-only knob (§8) |
| `php_executable` | `"php"` | accepted for parity, **inert** — a source-path knob (§8) |

### PHP-FPM

| Variable | Default | Meaning |
| --- | --- | --- |
| `php_enable_php_fpm` | `false` | render the pools and manage the FPM service |
| `php_fpm_state` | `"started"` | `started`/`stopped`/`restarted`/`reloaded` |
| `php_fpm_enabled_on_boot` | `true` | enable the FPM service on boot |
| `php_fpm_handler_state` | `"restarted"` | the chained handler's verb: `restarted`/`reloaded`/`started` (D5) |
| `php_fpm_daemon` | `null` → `"php{v}-fpm"` | the FPM service name |
| `php_fpm_conf_path` | `null` → `"/etc/php/{v}/fpm"` | FPM conf directory |
| `php_fpm_pool_conf_path` | `null` → `"{fpm_conf_path}/pool.d/www.conf"` | the default pool file; its dirname is the pool directory |
| `php_fpm_pool_user` | `null` → `"www-data"` | pool user |
| `php_fpm_pool_group` | `null` → `"www-data"` | pool group |
| `php_fpm_listen` | `"127.0.0.1:9000"` | default www pool listen address |
| `php_fpm_listen_allowed_clients` | `"127.0.0.1"` | default www pool allowed clients |
| `php_fpm_pm_max_children` | `50` | default www pool `pm.max_children` |
| `php_fpm_pm_start_servers` | `5` | default www pool `pm.start_servers` |
| `php_fpm_pm_min_spare_servers` | `5` | default www pool `pm.min_spare_servers` |
| `php_fpm_pm_max_spare_servers` | `5` | default www pool `pm.max_spare_servers` |
| `php_fpm_pm_max_requests` | `0` | default www pool `pm.max_requests`; 0 renders 500 (Jinja2's `default(500, true)`, faithfully) |
| `php_fpm_pm_status_path` | `""` | default www pool `pm.status_path`; empty omits the line |
| `php_fpm_pools` | `null` → the www pool | list of pools; each needs `pool_name` + `pool_listen`, only `pool_template: www.conf.j2` is renderable (D7) |

### php.ini (only when `php_use_managed_ini: true`)

| Variable | Default | Variable | Default |
| --- | --- | --- | --- |
| `php_use_managed_ini` | `true` | `php_expose_php` | `"On"` |
| `php_memory_limit` | `"256M"` | `php_max_execution_time` | `"60"` |
| `php_max_input_time` | `"60"` | `php_max_input_vars` | `"1000"` |
| `php_realpath_cache_size` | `"32K"` | `php_file_uploads` | `"On"` |
| `php_upload_max_filesize` | `"64M"` | `php_max_file_uploads` | `"20"` |
| `php_post_max_size` | `"32M"` | `php_date_timezone` | `"America/Chicago"` |
| `php_allow_url_fopen` | `"On"` | `php_sendmail_path` | `"/usr/sbin/sendmail -t -i"` |
| `php_output_buffering` | `"4096"` | `php_short_open_tag` | `"Off"` |
| `php_disable_functions` | `[]` | `php_precision` | `14` |
| `php_serialize_precision` | `"-1"` | `php_session_cookie_lifetime` | `0` |
| `php_session_gc_probability` | `1` | `php_session_gc_divisor` | `1000` |
| `php_session_gc_maxlifetime` | `1440` | `php_session_save_handler` | `"files"` |
| `php_session_save_path` | `""` | `php_error_reporting` | `"E_ALL & ~E_DEPRECATED & ~E_STRICT"` |
| `php_display_errors` | `"Off"` | `php_display_startup_errors` | `"Off"` |

### OpCache and APCu

| Variable | Default | Meaning |
| --- | --- | --- |
| `php_opcache_enable` | `"1"` | gates the OpCache tasks (`\| bool`) **and** renders verbatim into `opcache.ini` |
| `php_opcache_zend_extension` | `"opcache.so"` | `zend_extension` line |
| `php_opcache_enable_cli` | `"0"` | `opcache.enable_cli` |
| `php_opcache_memory_consumption` | `"96"` | `opcache.memory_consumption` |
| `php_opcache_interned_strings_buffer` | `"16"` | `opcache.interned_strings_buffer` |
| `php_opcache_max_accelerated_files` | `"4096"` | `opcache.max_accelerated_files` |
| `php_opcache_max_wasted_percentage` | `"5"` | `opcache.max_wasted_percentage` |
| `php_opcache_validate_timestamps` | `"1"` | `opcache.validate_timestamps` |
| `php_opcache_revalidate_path` | `"0"` | `opcache.revalidate_path` |
| `php_opcache_revalidate_freq` | `"2"` | `opcache.revalidate_freq` |
| `php_opcache_max_file_size` | `"0"` | `opcache.max_file_size` |
| `php_opcache_blacklist_filename` | `""` | non-empty adds the `opcache.blacklist_filename` line |
| `php_opcache_conf_filename` | `null` → `"10-opcache.ini"` | the role's OpCache ini filename |
| `php_enable_apc` | `true` | gates the APCu tasks |
| `php_apc_shm_size` | `"96M"` | `apc.shm_size` |
| `php_apc_enable_cli` | `"0"` | `apc.enable_cli` |
| `php_apc_conf_filename` | `null` → `"20-apcu.ini"` | the role's APCu ini filename |

### Paths

| Variable | Default | Meaning |
| --- | --- | --- |
| `php_conf_paths` | `null` → `("/etc/php/{v}/fpm", "/etc/php/{v}/apache2", "/etc/php/{v}/cli")` | where `php.ini` is placed |
| `php_extension_conf_paths` | `null` → each conf path + `/conf.d` | where the extension ini files live and the stray sweep searches |

Invalid values (a bad enum, a non-boolean flag, a pool without
`pool_name`/`pool_listen`, a custom `pool_template`) raise before any host is
contacted.

## 4. Program shape: four deploys and their barriers

reemote runs a deploy twice (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence. The role
reads the distribution major version (`include_vars` dispatch), registers the
package-install outcome (`php_package_install.changed`), `find`s stray
extension configs and `stat`s the FPM pool directory — mutable reads a single
deploy may not branch on. Each becomes a **gather → snapshot → factory** step
between complete runs:

```
main()
 ├─ load + validate inventory.json; resolve role_settings per target
 ├─ gather_fact(LinuxDistribution)     major → Ubuntu version table (7.4/8.1/8.3,
 │                                     else 8.2) fixes packages + paths
 │                                     (hosts closed manually afterwards)
 ├─ deploy 1: build_install            apt.update(cache_valid_time=86400)
 │                                     apt.packages(php{v}-*, …)
 ├─ changed_hosts(install report, "Ensure PHP packages are installed")
 │                                     the register+when recipe: a frozen set
 ├─ deploy 2: build_packaged_cleanup   delete the packaged APCu/OpCache ini
 │                                     symlinks, only on changed hosts
 ├─ gather_fact(FindInFiles)           one sweep per ext path per pattern
 ├─ gather_fact(FileExists)            the FPM pool directory
 │                                     (hosts closed manually afterwards)
 ├─ control-side: configure_plan       stray_configs filter + every template
 │                                     rendered to a str (pure Python)
 ├─ deploy 3: build_configure          directories, php.ini, stray removals,
 │                                     APCu/OpCache ini, FPM pools + service
 ├─ changed_hosts over WEBSERVER_NOTIFY_NAMES (union of all three reports)
 │    + FPM_NOTIFY_NAMES; apply the handlers' when guards
 └─ deploy 4: build_handler            service_handler per host: webserver
                                       first, then the chained FPM restart
```

The barriers are load-bearing: the distribution read happens **before**
deploy 1 because it fixes the package list; the cleanup has its own deploy
because it branches on the install outcome; the sweeps run **after** the
cleanup apply (the role's `find` runs after `setup-Debian`); the handler reads
the apply reports — the only notifying deploys — exactly as Ansible flushes
handlers at the end of the play.

## 5. Coverage table

Status vocabulary: **covered** (direct operation), **recipe** (documented
shape change).

### `tasks/main.yml`

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 1 | `include_vars: "{{ os_family }}.yml"` | frozen Debian/Ubuntu constants (single-OS scope) | recipe |
| 2 | `include_vars: Ubuntu-{major}.yml` `with_first_found` | `gather_fact(LinuxDistribution)` + `UBUNTU_PHP_VERSIONS` table, 8.2 fallback | recipe |
| 3 | `set_fact:` version + `__php_*` fallback chain (9 tasks) | `resolved_vars()` — pure, control-side | covered |
| 4 | `set_fact:` JSON package for PHP < 8.0 (2 tasks) | `resolved_vars()` appends `php{v}-json` when version < 8.0 | covered |
| 5 | Ubuntu 16.04 `php_opcache_conf_filename` special case | scoped out — 16.04 is EOL and not in the version table; the default `10-opcache.ini` is identical anyway | out of scope |
| 6 | `include_tasks: setup-RedHat.yml` | scoped out (§8) | out of scope |
| 7 | `include_tasks: setup-Debian.yml` | deploys 1 + 2 | covered |
| 8 | `include_tasks: install-from-source.yml` | scoped out — opt-in, default-false (§8) | out of scope |
| 9 | `include_tasks: configure*.yml` | deploy 3 | covered |

### `tasks/setup-Debian.yml` → deploys 1 + 2

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 10 | `apt: update_cache=yes cache_valid_time=86400` | `apt.update(cache_valid_time=86400)` — gates on `AptUpdateStamp`, no-op inside the window | covered |
| 11 | `apt: name={{ php_packages + php_packages_extra }} state=… install_recommends=…` `register` `notify` | `apt.packages(packages=…, present=…, latest=…, no_recommends=…)`; the outcome is read with `changed_hosts` | covered |
| 12 | `file: state=absent` packaged APCu ini, `when: php_enable_apc and php_package_install.changed` | `build_packaged_cleanup` — `files.file(present=False, force=True)` gated by the frozen changed set (D4) | recipe |
| 13 | `file: state=absent` packaged OpCache ini, `when: php_opcache_enable \| bool and …changed` | same, OpCache paths | recipe |

### `tasks/configure.yml` → deploy 3

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 14 | `file: state=directory follow=true mode=0755` conf + ext paths | `files.directory(mode="0755")` — no `follow` parameter (D3) | covered |
| 15 | `template: php.ini.j2 → {{ item }}/php.ini`, `root:root 0644`, `when: php_use_managed_ini` | control-side `render_php_ini` + `files.install(content, mode="0644", user="root", group="root")` | recipe |

### `tasks/configure-apcu.yml` / `configure-opcache.yml` → deploy 3

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 16 | `find: paths={{ item }} contains=<regex>` `register` | `gather_fact(FindInFiles, ext, pattern)` between runs + `stray_configs` filter (D2) | recipe |
| 17 | `file: state=absent` stray configs, `when: basename != role filename` | `files.file(present=False, force=True)` per filtered hit | covered |
| 18 | `template: apc.ini.j2 / opcache.ini.j2`, `force=true root:root 0644`, `when: enabled` | control-side render + `files.install` (content-idempotent; `force` is the default behaviour) | recipe |
| 19 | `file: state=absent` role ini, `when: not enabled` | `files.file(present=False, force=True)` | covered |

### `tasks/configure-fpm.yml` → deploy 3

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 20 | `set_fact:` FPM daemon/pool-path/user/group fallbacks (4 tasks) | `resolved_vars()` | covered |
| 21 | `stat: {{ pool_conf_path \| dirname }}` `register` | `gather_fact(FileExists, pool_dir)` between runs (D8) | recipe |
| 22 | `file: state=directory` pool dir, `when: stat.islnk is not defined` | `files.directory` only when the gather found nothing | recipe |
| 23 | `template: {{ item.pool_template }} → {{ pool_dir }}/{{ item.pool_name }}.conf` loop, `when: php_enable_php_fpm` | control-side `render_www_conf` + one `files.install` per pool (D7) | recipe |
| 24 | `service: name={{ php_fpm_daemon }} state={{ php_fpm_state }} enabled=…`, `when: enable and distribution != "Debian"` | `systemd.service(running/restarted/reloaded/enabled)` — the gathered distribution name keeps the Debian guard | covered |

### `handlers/main.yml` → deploy 4

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 25 | `restart webserver`: `service: state=restarted`, `when: php_enable_webserver`, `notify: restart php-fpm` | `changed_hosts` union over `WEBSERVER_NOTIFY_NAMES` across the three reports → `service_handler(action="restarted")` per host | recipe |
| 26 | `restart php-fpm`: `service: state={{ php_fpm_handler_state }}`, `when: php_enable_php_fpm and php_fpm_state == 'started'` | the chain collapses to an upstream-first service listing: the webserver set (chained) ∪ the direct pool notifications, filtered by the guards → `service_handler(action=…)` (D5) | recipe |

## 6. Deliberate deviations from Ansible semantics

- **D1 — OS dispatch frozen, version table still gathered.**
  `include_vars` per OS family becomes module constants (Ubuntu-only scope);
  the `Ubuntu-{20,22,24}.yml` version override is still selected at run time
  by `LinuxDistribution.major`, exactly as the role's `with_first_found`
  does, with the `vars/Debian.yml` 8.2 fallback for any other major.
- **D2 — `find: contains` → `FindInFiles` (recursive grep).** Ansible's
  `find` is shallow by default, skips dotfiles and anchors `contains` at the
  line start; `grep -rlE` recurses, includes dotfiles and matches anywhere in
  a line. The shallowness and dotfile differences are recovered control-side
  (`stray_configs` keeps only direct, non-hidden children); the broader line
  match is kept — it only ever widens the cleanup of *stray* files, never the
  role's own. Symlink handling is faithful: both searches skip symlinks
  (Ansible's `find` uses `lstat` + `S_ISREG`; GNU grep skips symlinks during
  recursion — verified empirically), and on a stock Ubuntu `conf.d` the
  packaged entries are symlinks, so the result is identical.
- **D3 — `files.directory` has no `follow`.** The role's `follow: true` is
  inert on stock Ubuntu, where every conf path is a real directory; a
  symlinked conf path would fail fast rather than follow.
- **D4 — symlink removal uses `force=True`.** The packaged `conf.d` ini
  files are symlinks into `mods-available`-style targets; the `File` fact
  reads a symlink as a non-file, so `files.file(present=False, force=True)`
  removes the link itself — what Ansible's `state: absent` does to a symlink
  without `force`.
- **D5 — handler verbs.** `service_handler` offers
  `restarted`/`reloaded`/`running`; `php_fpm_handler_state` accepts those
  three (`started` → `running`). Ansible's `stopped` is not a handler verb
  here — the role documents `restarted`/`reloaded` only.
- **D6 — `ansible_managed` frozen.** The `www.conf.j2` header renders the
  ansible.cfg default `"Ansible managed"`; a configurable `ansible_managed`
  has no analogue in a standalone program.
- **D7 — only the bundled pool template.** A custom `pool_template` is
  rejected before any host is contacted (it would need a template engine);
  `www.conf.j2` is rendered in pure Python. Pools omitting optional keys
  render with the template's defaults — slightly more permissive than
  Ansible, whose `|length` on an undefined `pool_pm_status_path` errors.
- **D8 — `FileExists` vs `stat`.** The pool-directory gate reads `test -e`
  instead of Ansible's `stat`: a *dangling symlink* at the pool path reads
  absent here (so the program would try to create the directory and fail
  fast) where `stat` reports `islnk` defined (so the role skips creation).
  On a stock Ubuntu the path is a real directory or absent — the edge is
  unreachable in practice.
- **D9 — stateless service verbs.** `php_fpm_state: restarted/reloaded` (and
  the handler's verbs) always report changed when reached, faithful to
  Ansible's `service` module; the default `started` is fact-gated and
  converges. The handler only runs on hosts whose notifying requests
  changed, so a converged play restarts nothing.
- **D10 — stray removals sorted.** `grep -l` traversal order is
  filesystem-dependent; the removals are sorted so the request sequence is
  deterministic. The *set* of removed files is unchanged.
- **D11 — inert knobs.** `php_enablerepo` (RHEL-only) and `php_executable`
  (source path) are accepted for inventory parity, unused on this path.
- **D12 — check mode.** The role's service tasks skip under
  `ansible_check_mode`; reemote's `run(dry=True)` is the analogue, and the
  program performs a dry preview before every apply. A dry preview of the
  handler deploy is honest (it consults the frozen outcome); a dry preview of
  a notifying deploy cannot know whether handlers will fire.

## 7. Offline verification

`tests/test_roles_ansible_role_php.py` imports the example by path (its file
name is not a valid module identifier), never calls its `main()`, and replays
the orchestration barrier-for-barrier against persistent `SimulatedHost`
objects (`host_factory` returns the *same* host every run, so pass 2 observes
pass 1). It proves:

- the first pass refreshes the apt cache, installs the version-specific
  packages, deletes the packaged APCu/OpCache ini symlinks (only because the
  install changed), creates the conf directories, installs the rendered
  `php.ini`/`apcu.ini`/`opcache.ini` with the role's `root:root 0644`, removes
  a seeded stray config, creates the FPM pool directory and www pool, starts
  and enables the FPM service, and fires both chained handlers;
- a non-Ubuntu host in the same inventory is never touched;
- **the second pass changes nothing and queues no handler** — the
  idempotence claim, including the `cache_valid_time` window, the
  content-idempotent writes and the outcome-gated cleanup;
- the `dry=True` preview mutates nothing;
- the pure helpers (the renders, `stray_configs`, `resolved_vars`,
  `role_settings` validation) are unit-tested directly.

The `find`/`grep` sweeps are answered offline by seeding the `grep_files`
seam keyed `(path, pattern)`; the distribution read by seeding
`linux_distribution`. Removals are asserted against `remote_files` directly.
Two between-pass bridges
repair *simulation* limits (not library bugs): `make_directory` records a
`directory_stats` row but not an `existing_files` entry, so the test syncs the
two stores before pass 2 (the `FileExists` pool-dir gate reads the latter); and
the question-keyed `grep_files` seam is re-derived from the simulated
filesystem after every pass so pass 2 does not read a stale fixture. The
packaged ini files are seeded as a mix of symlinks and regular files to
exercise both `files.file` removal branches. The remaining seeding choices are
documented in the test docstring.

## 8. Scoped out

- The RHEL/CentOS branch (`setup-RedHat.yml`, `vars/RedHat.yml`,
  `php_enablerepo`) — a Phase 0 single-OS decision.
- `install-from-source.yml` and its `php_source_*` variables — opt-in,
  `php_install_from_source` defaults false. Its `command:`/`shell:` steps
  would migrate as `Shell`/`Script` actions gathered outside deploys (the
  documented build-from-source recipe), a separate workstream.
- `php_executable` (a source-path knob; D11).
- Custom `pool_template` values (D7) — a template-engine workstream.
- The Ubuntu 16.04 `php_opcache_conf_filename` special case — EOL, and the
  value equals the default anyway.