# Apache Ansible role → reemote

This example migrates the [geerlingguy.apache](https://github.com/geerlingguy/ansible-role-apache)
Ansible role to a Python program built on the reemote library
([`ansible-role-apache.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/ansible-role-apache/ansible-role-apache.py)).

The role installs and configures Apache 2.x on RHEL/CentOS, Debian/Ubuntu,
SLES and Solaris: the distribution packages, the `ports.conf` listen lines,
the `mods-enabled`/`conf-enabled` symlinks, a rendered
`sites-available/vhosts.conf` (with optional SSL virtual hosts), the optional
default-vhost removal, the service's running/enabled state, and a single
`restart apache` handler notified by every configuration task.

**This migration targets Ubuntu only.** Hosts in the inventory group
`"ubuntu"` execute the deploy; every request carries the static `when` guard
`is_ubuntu`, so the other hosts in the shared `inventory.json` (Alpine,
CentOS) are skipped without being contacted. The role's `include_vars` OS
dispatch collapses to frozen Debian constants, and the RHEL/SLES/Solaris/
Amazon Linux branches are scoped out (see §8).

The migration is complete: every task on the Ubuntu default path maps to a
covered operation or a documented recipe; no feature was dropped.

## 1. Prerequisites

### 1.1 Packages that must be loaded before executing

| Package | Where | Why |
| --- | --- | --- |
| `reemote` | control machine | the library itself; a repo checkout installed by `uv sync` provides it |
| `jinja2` | control machine | renders the role's `vhosts.conf.j2` template. reemote deliberately ships **no** template engine, so Jinja2 is *not* a reemote dependency: the standalone run supplies it with `uv run --with jinja2`, and the repo's `dev` group declares it for the offline tests |
| `apache2`, `apache2-utils` | target host | installed **by the program itself** via `apt.packages` — nothing to pre-load |

No other control-side package is needed: the program reads the inventory with
the standard library's `json`, and the template uses only built-in Jinja2
filters (`indent`, `default`), so no Ansible-specific filter plugins are
required.

### 1.2 Control machine

- [uv](https://docs.astral.sh/uv/) and Python ≥ 3.13 (the project's
  `requires-python`).
- This repository, with dependencies synced: `uv sync`.

### 1.3 Target host

- Ubuntu with systemd, reachable over SSH as a user that can `sudo` (the
  inventory's `privilege` section carries any run-as user/password — never
  the program).
- The `apache2` packages must be available from the configured apt
  repositories (the stock Ubuntu archive is enough).

### 1.4 Inventory: what you must add

The program loads the project-root `inventory.json`. A target entry needs
`"group": "ubuntu"`; every role variable rides beside `host`/`connection` as
an extra key — reemote's `host_vars` mechanism (`host.extra`):

```json
{
  "host": "ubuntu",
  "family": "Ubuntu",
  "connection": {
    "address": "192.168.0.203",
    "username": "supervisor",
    "key_path": "~/.ssh/id_ed25519"
  },
  "group": "ubuntu",
  "tags": ["ubuntu_host", "apt_package_manager"],
  "apache_listen_port": 8080,
  "apache_remove_default_vhost": true,
  "apache_vhosts": [
    {"servername": "example.com", "documentroot": "/var/www/example"}
  ]
}
```

With no extra keys the role defaults apply (§3) — the entry above only
illustrates the shape.

## 2. Running it against `inventory.json` with uv

From the repository root:

```bash
uv sync                                        # once: install reemote + dev deps
uv run --with jinja2 python examples/roles/ansible-role-apache/ansible-role-apache.py
```

`--with jinja2` supplies the renderer for the standalone run (it is not a
reemote runtime dependency). The program:

1. loads and validates `inventory.json`, prints the hosts, and names the
   hosts the `is_ubuntu` guard skips;
2. resolves each target's role variables (defaults overlaid with the entry's
   extra keys) and fails fast on an invalid value — before any host is
   contacted;
3. runs **deploy 1** (apt cache refresh + packages), then gathers the
   `apache2 -v` version probe and the SSL certificate existence reads,
   renders the vhosts file control-side, runs **deploy 2** (configuration +
   service state), reads `changed_hosts` over the notifying request names,
   and runs **deploy 3** (the `restart apache` handler);
4. prints each deploy twice: the `dry=True` plan (Ansible's `--check` as a
   return value — nothing is mutated) followed by the real apply log.

**Run the whole program a second time** to see the idempotence claim: the
second pass reports no changes and queues no handler.

### Offline verification (no hosts, no approval needed)

```bash
uv run pytest tests/test_roles_ansible_role_apache.py -q
```

The test replays the program's whole orchestration against the test
framework's `SimulatedHost` and proves plan → apply → **second apply is a
no-op** (§7). `jinja2` comes from the `dev` group here, so no `--with` is
needed.

## 3. Role variables

Every `defaults/main.yml` variable is honoured under its original
`apache_`-prefixed name, read from the inventory entry's extra keys. The
`vars/Debian.yml` values are frozen constants (single-OS scope); the
`apache-22.yml`/`apache-24.yml` tables are selected by the probed version.

| Variable | Default | Meaning |
| --- | --- | --- |
| `apache_listen_ip` | `"*"` | listen address; `*` drops the IP prefix on the 2.4 `Listen` line |
| `apache_listen_port` | `80` | HTTP port (`ports.conf` + vhosts) |
| `apache_listen_port_ssl` | `443` | HTTPS port (SSL vhosts) |
| `apache_create_vhosts` | `true` | render and enable the vhosts file |
| `apache_vhosts_filename` | `"vhosts.conf"` | file name under `sites-available`/`sites-enabled` |
| `apache_vhosts_template` | `"vhosts.conf.j2"` | the bundled template; any other value is a **control-side** path read at render time |
| `apache_remove_default_vhost` | `false` | remove `sites-enabled/000-default.conf` (2.4) / `000-default` (2.2) |
| `apache_global_vhost_settings` | `"DirectoryIndex index.php index.html\n"` | first line(s) of the vhosts file |
| `apache_vhosts` | one `local.dev` vhost | list of `{servername, documentroot?, serveralias?, serveradmin?, allow_override?, options?, extra_parameters?}` |
| `apache_allow_override` | `"All"` | default `AllowOverride` in each `<Directory>` |
| `apache_options` | `"-Indexes +FollowSymLinks"` | default `Options` in each `<Directory>` |
| `apache_vhosts_ssl` | `[]` | SSL vhosts; each needs `certificate_file` + `certificate_key_file` (`certificate_chain_file` optional) |
| `apache_ignore_missing_ssl_certificate` | `true` | render an SSL vhost even when its certificate file is absent |
| `apache_ssl_protocol` | `"All -SSLv2 -SSLv3"` | `SSLProtocol` |
| `apache_ssl_cipher_suite` | `"AES256+EECDH:AES256+EDH"` | `SSLCipherSuite` |
| `apache_mods_enabled` | `["rewrite", "ssl"]` | mods to symlink into `mods-enabled` |
| `apache_mods_disabled` | `[]` | mods to remove from `mods-enabled` |
| `apache_conf_enabled` | `[]` | confs to symlink into `conf-enabled` |
| `apache_conf_disabled` | `[]` | confs to remove from `conf-enabled` |
| `apache_state` | `"started"` | final service state: `started`/`stopped`/`restarted`/`reloaded` |
| `apache_enabled` | `true` | enable the service on boot |
| `apache_restart_state` | `"restarted"` | the handler's verb: `restarted`/`reloaded`/`started` |
| `apache_packages_state` | `"present"` | `present`/`latest`/`absent` for the packages |
| `apache_packages` | `null` → `("apache2", "apache2-utils")` | package list; `null` reproduces the role's `__apache_packages` fallback |
| `apache_enablerepo` | `""` | accepted for parity, **inert** — a RHEL-only knob (§8) |
| `apache_ssl_no_log` | `true` | accepted for parity, **inert** — reemote has no log masking (§6, D3) |

Invalid values (a bad enum, a vhost without `servername`, an SSL vhost
without its certificate paths) raise before any host is contacted.

## 4. Program shape: three deploys and their barriers

reemote runs a deploy twice (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence. The role
reads the installed Apache version with a `command:` probe and `stat`s the
SSL certificates, then feeds both into the vhosts template — mutable reads a
single deploy may not branch on. Each becomes a **gather → snapshot →
factory** step between complete runs:

```
main()
 ├─ load + validate inventory.json; resolve role_settings per target
 ├─ deploy 1: deploy_install          apt.update(cache_valid_time=3600)
 │                                    apt.packages(apache2, apache2-utils)
 ├─ gather_action(Shell)              /usr/sbin/apache2 -v   ← after the install
 │    └─ parse version → select apache-22 / apache-24 vars table
 ├─ gather_fact(FileExists)           one read per SSL certificate path
 │                                    (hosts closed manually afterwards)
 ├─ control-side: configure_plan      ports lineinfile → pure transforms,
 │                                    vhosts.conf.j2 → rendered str (Jinja2)
 ├─ deploy 2: build_configure(plans)  ports.conf edits + mode, mod/conf
 │                                    symlinks, vhosts install + symlink,
 │                                    default-vhost removal, service state
 ├─ changed_hosts(configure report, notifying names)
 └─ deploy 3: build_handler(plans, notified)
                                      service_handler per apache_restart_state
                                      partition ("restarted"/"reloaded"/"running")
```

The barriers are load-bearing: the probe runs **after** the install apply so
the binary exists; the certificate reads and the render happen **before**
the configure deploy so its request sequence is a pure function of frozen
input; the handler reads the configure apply report — the only notifying
deploy — exactly as Ansible flushes handlers at the end of the play.

## 5. Coverage table

Status vocabulary: **covered** (direct operation), **recipe** (documented
shape change).

### `tasks/main.yml`

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 1 | `include_vars: "{{ ansible_os_family }}.yml"` | frozen `DEBIAN_VARS` (single-OS scope) | recipe |
| 2 | Amazon Linux `include_vars` override | scoped out (§8) | out of scope |
| 3 | `set_fact: apache_packages` fallback | `role_settings()` `None` → `DEBIAN_PACKAGES` | covered |
| 4 | `include_tasks: setup-Debian.yml` | deploy 1 | covered |
| 5 | `command: apache2 -v` + `register` + `set_fact` version | `gather_action(Shell)` between runs + `parse_apache_version` | recipe |
| 6 | `include_vars: apache-22/24.yml` on version minor | `configure_plan` table selection | recipe |
| 7 | `include_tasks: configure-Debian.yml` | deploy 2 | covered |
| 8 | `service: name=apache2 state=… enabled=…` (no notify) | `systemd.service(running/restarted/reloaded/enabled)` at the end of deploy 2 | covered |

### `tasks/setup-Debian.yml` → deploy 1

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 9 | `apt: update_cache=yes cache_valid_time=3600` | `apt.update(cache_valid_time=3600)` — gates on `AptUpdateStamp`, no-op inside the window | covered |
| 10 | `apt: name={{ apache_packages }} state={{ apache_packages_state }}` | `apt.packages(packages=…, present=…, latest=…)` — `latest` gates on `AptUpgradable` | covered |

### `tasks/configure-Debian.yml` → deploy 2 (every task notifies `restart apache`)

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 11 | `lineinfile: ports.conf` loop, `mode: 0644` | `files.transform` with a factory-built pure `lineinfile_transform` + paired `files.file(mode="0644")` for metadata convergence | recipe |
| 12 | `file: state=link` mods-enabled `*.load`, `mode: 0644` | `files.link(path, target)` — symlink mode dropped (D2) | covered |
| 13 | `file: state=absent` disabled mods | `files.link(present=False, force=True)` | covered |
| 14 | `file: state=link` conf-enabled `*.conf` | `files.link(path, target)` | covered |
| 15 | `file: state=absent` disabled confs | `files.link(present=False, force=True)` | covered |
| 16 | `stat:` certificate loop + `register` (`no_log`) | `gather_fact(FileExists)` per path between runs; `no_log` boundary (D3) | recipe |
| 17 | `template: vhosts.conf.j2 → sites-available`, `root:root 0644` | control-side Jinja2 render + `files.install(content, mode="0644", user="root", group="root")` | recipe |
| 18 | `file: state=link` sites-enabled vhost, `force={{ ansible_check_mode }}` | `files.link` with default `force=False`; check mode → `run(dry=True)` (D6) | covered |
| 19 | `file: state=absent` default vhost (when flag) | `files.link(present=False, force=True)` | covered |

### `handlers/main.yml` → deploy 3

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 20 | `service: name=apache2 state={{ apache_restart_state }}` notified by 11–19 | `changed_hosts(report, notifying_names)` → `service_handler(action=…)` partitioned per host's `apache_restart_state` | recipe |

## 6. Deliberate deviations from Ansible semantics

- **D1 — OS dispatch frozen.** `include_vars` per OS family becomes module
  constants (`DEBIAN_VARS`, `DEBIAN_PACKAGES`); the 2.2/2.4 tables are still
  selected at run time by the probed version, exactly as the role does.
- **D2 — symlink modes dropped.** The role sets `mode: 0644` on the
  mod/conf/vhost symlinks; `files.link` owns no mode parameter, and a Linux
  symlink's own mode is always 0777 and meaningless — Ansible cannot set it
  either. No behavioural difference on the target.
- **D3 — `no_log` boundary.** reemote has no log masking; the role's
  `no_log: true` on the certificate `stat` task has no analogue. The stat
  reads only file *existence* (no secret material), and `apache_ssl_no_log`
  is accepted but inert.
- **D4 — control-side render.** `template:` becomes a Jinja2 render in
  `main()`'s gap between runs; `files.install` takes the rendered `str`
  (reemote has no controller filesystem paths inside a deploy). A custom
  `apache_vhosts_template` is read as a control-side path at render time.
  Ansible's whitespace defaults (`trim_blocks`, `keep_trailing_newline`,
  `StrictUndefined`) are reproduced.
- **D5 — `lineinfile` semantics reproduced, metadata split.** The transform
  matches the module: exact line present → no change; else replace the
  *last* `regexp` match; else append; one trailing newline. Because
  `files.transform` idempotence is content-only, a paired `files.file`
  converges the `mode: 0644` the `lineinfile` task also sets.
- **D6 — check mode.** `force={{ ansible_check_mode }}` on the vhost symlink
  is `False` in a real run, so the migration uses the default `force=False`;
  Ansible's `--check` maps to `run(..., dry=True)`, which the program
  performs before every apply.
- **D7 — the version probe is an action.** `command:` cannot live inside a
  deploy (`ActionInDeployError`); it runs via `gather_action` between the
  install and configure runs, and a non-zero exit is a hard error (the role
  would fail on the unexpected stdout too).
- **D8 — stateless service verbs.** `apache_state: restarted/reloaded` (and
  the handler's verbs) always report changed when reached, faithful to
  Ansible's `service` module; the default `started` is fact-gated and
  converges. The handler only runs on hosts whose notifying requests
  changed, so a converged play restarts nothing.
- **D9 — `apache_enablerepo` inert.** A RHEL-only knob (the role uses it in
  the RedHat setup branch); accepted for inventory parity, unused on Ubuntu.
- **D10 — `apache_packages_state: absent` still configures.** The role
  guards nothing on the package state: with `absent` it removes the packages
  and then runs the configure tasks anyway. The migration is faithful to
  that (unusual) shape rather than silently improving it.

## 7. Offline verification

`tests/test_roles_ansible_role_apache.py` imports the example by path (its
file name is not a valid module identifier), never calls its `main()`, and
replays the orchestration barrier-for-barrier against persistent
`SimulatedHost` objects (`host_factory` returns the *same* host every run, so
pass 2 observes pass 1). It proves:

- the first pass installs the packages, rewrites `ports.conf`, creates the
  mod/conf/vhost symlinks, installs the rendered vhosts file with the role's
  `root:root 0644`, removes the default vhost, starts and enables the
  service, and fires the restart handler;
- a non-Ubuntu host in the same inventory is never touched (every entry
  skipped, no state mutated);
- **the second pass changes nothing and queues no handler** — the
  idempotence claim, including the `cache_valid_time` window and the
  content-idempotent writes;
- the `dry=True` preview mutates nothing;
- the SSL branch: certificate existence gates the rendered SSL vhosts
  (`apache_ignore_missing_ssl_certificate: false`), and the pure helpers
  (`lineinfile_transform` fixpoint, `parse_apache_version`, the 2.2/2.4
  ports items) are unit-tested directly.

The version probe runs offline through the first-class `SimulatedHost`
action seam: `seed()` plants the canned `apache2 -v` record in
`shell_results`; the remaining seeding choices are documented in the test
docstring.

## 8. Scoped out

- The RHEL/CentOS, SLES, Solaris and Amazon Linux branches (`setup-RedHat`,
  `configure-RedHat`, `setup-Suse`, `configure-Suse`, `setup-Solaris`,
  `configure-Solaris`, the Amazon `include_vars` override) — a Phase 0
  single-OS decision.
- `apache_enablerepo` (RHEL-only; D9).
- Ansible check mode as an in-deploy concept (D6) — reemote's `dry=True`
  covers the use case.