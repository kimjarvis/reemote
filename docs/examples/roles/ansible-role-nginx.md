# Nginx Ansible role → reemote

This example migrates the [geerlingguy.nginx](https://github.com/geerlingguy/ansible-role-nginx)
Ansible role to a Python program built on the reemote library
([`ansible-role-nginx.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/ansible-role-nginx/ansible-role-nginx.py)).

The role installs and configures nginx on RedHat/CentOS, Debian/Ubuntu,
Archlinux, Suse, FreeBSD and OpenBSD: the `dirmngr` prerequisite, the
optional official nginx PPA (with a reinstall-on-PPA-add split), the apt
cache refresh and the `nginx` package, per-vhost files rendered from
`vhost.j2` into `sites-enabled`, the optional default-vhost removal, the
legacy `vhosts.conf` cleanup, the rendered `nginx.conf` installed behind
`nginx -t` validation, the service's running/enabled state, and the
`restart nginx` / `reload nginx` handlers.

**This migration targets Ubuntu only.** Hosts in the inventory group
`"ubuntu"` execute the deploy; every request carries the static `when` guard
`is_ubuntu`, so the other hosts in the shared `inventory.json` (Alpine,
CentOS) are skipped without being contacted. The role's `include_vars` OS
dispatch collapses to frozen Debian constants, and the RedHat, Suse,
Archlinux, FreeBSD and OpenBSD branches are scoped out (see §8). Both Ubuntu
opt-in toggles (`nginx_ppa_use`, `nginx_remove_default_vhost`) **are**
implemented — unlike a build-from-source branch, every task they gate maps
to a covered operation.

The migration is complete: every task on the Ubuntu path maps to a covered
operation or a documented recipe; nothing blocked the migration and no
feature was dropped.

## 1. Prerequisites

### 1.1 Packages that must be loaded before executing

| Package | Where | Why |
| --- | --- | --- |
| `reemote` | control machine | the library itself; a repo checkout installed by `uv sync` provides it |
| `jinja2` | control machine | renders the role's `nginx.conf.j2` and `vhost.j2` templates. reemote deliberately ships **no** template engine, so Jinja2 is *not* a reemote dependency: the standalone run supplies it with `uv run --with jinja2`, and the repo's `dev` group declares it for the offline tests |
| `dirmngr`, `nginx` | target host | installed **by the program itself** via `apt.packages` — nothing to pre-load |

No other control-side package is needed: the program reads the inventory
with the standard library's `json`, and both templates use only built-in
Jinja2 filters (`indent`, `default`) — no PyYAML, no Ansible-specific filter
plugins.

### 1.2 Control machine

- [uv](https://docs.astral.sh/uv/) and Python ≥ 3.13 (the project's
  `requires-python`).
- This repository, with dependencies synced: `uv sync`.

### 1.3 Target host

- Ubuntu with systemd, reachable over SSH as a user that can `sudo` (the
  inventory's `privilege` section carries any run-as user/password — never
  the program).
- The `nginx` package must be available from the configured apt
  repositories (the stock Ubuntu archive is enough; with `nginx_ppa_use`
  the program adds `ppa:nginx/<version>` itself, which needs outbound
  HTTPS to `ppa.launchpad.net`).

**Shared demo host: stop apache2 first (ports 80/443).** The shared
`ubuntu` demo host (`192.168.0.181`) is also the apache role example's
target (`examples/roles/ansible-role-apache/`), and that example leaves
`apache2` enabled and active, holding ports 80 and 443. nginx cannot bind
while apache2 holds them: the rendered config still validates (`nginx -t`
passes), but deploy 3's final service task fails with
`ERROR: command failed on ubuntu (exit 1): sudo systemctl start nginx.service`
— the journal shows `bind() to 0.0.0.0:80 failed (98: Address already in
use)`. Free the ports before running this example:

```bash
ssh supervisor@192.168.0.181 'sudo systemctl stop apache2 && sudo systemctl disable apache2'
```

This is host state, not a program defect — the example correctly surfaces
the failed start as a typed `CommandError`. To check who owns the ports:
`sudo ss -tlnp | grep -E ':80\b|:443\b'`. Re-running the apache example
later re-enables apache2 and recreates the conflict: the two role
examples cannot both keep their service running on the same host.

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
  "nginx_ppa_use": true,
  "nginx_remove_default_vhost": true,
  "nginx_vhosts": [
    {
      "server_name": "example.com",
      "server_name_redirect": "www.example.com",
      "root": "/var/www/example",
      "index": "index.php index.html index.htm",
      "extra_parameters": "location / {\n    try_files $uri $uri/ =404;\n}"
    },
    {"server_name": "retired.example.com", "state": "absent"}
  ],
  "nginx_upstreams": [
    {
      "name": "myapp",
      "strategy": "ip_hash",
      "servers": ["srv1.example.com", "srv2.example.com weight=3"],
      "keepalive": 16
    }
  ]
}
```

With no extra keys the role defaults apply (§3) — the shipped
`inventory.json`'s `ubuntu` entry currently carries none, so it runs the
pure default path: stock-archive nginx, no PPA, no vhosts, the default
vhost kept. The entry above only illustrates the shape.

## 2. Running it against `inventory.json` with uv

From the repository root:

```bash
uv sync                                        # once: install reemote + dev deps
uv run --with jinja2 python examples/roles/ansible-role-nginx/ansible-role-nginx.py
```

`--with jinja2` supplies the renderer for the standalone run (it is not a
reemote runtime dependency). The program:

1. loads and validates `inventory.json`, prints the hosts, and names the
   hosts the `is_ubuntu` guard skips;
2. resolves each target's role variables (defaults overlaid with the
   entry's extra keys) and fails fast on an invalid value — before any
   host is contacted;
3. runs **deploy 1** (dirmngr + the optional PPA), reads `changed_hosts`
   over the PPA request's name (the role's `register` + `when … is
   changed`), runs **deploy 2** (the conditional reinstall removal, the
   cache refresh, the nginx package), gathers the vCPU count
   (`hardware.Cpus`) for the `worker_processes` default, renders
   `nginx.conf` and every vhost control-side, runs **deploy 3** (vhosts +
   validated `nginx.conf` + service state), reads `changed_hosts` over the
   two notifying name sets, and runs **deploy 4** (the restart/reload
   handlers, in definition order);
4. prints each deploy twice: the `dry=True` plan (Ansible's `--check` as a
   return value — nothing is mutated) followed by the real apply log.

**Run the whole program a second time** to see the idempotence claim: the
second pass reports no changes and queues no handler. A dry preview of a
*handler* deploy is honest (it consults the frozen outcome of the run
before it); a dry preview of the notifying deploy cannot know whether
handlers will fire.

### Offline verification (no hosts, no approval needed)

```bash
uv run pytest tests/test_roles_ansible_role_nginx.py -q
```

The test replays the program's whole orchestration against the test
framework's `SimulatedHost` and proves plan → apply → **second apply is a
no-op** (§7). `jinja2` comes from the `dev` group here, so no `--with` is
needed.

## 3. Role variables

Every `defaults/main.yml` variable is honoured under its original `nginx_`-
prefixed name, read from the inventory entry's extra keys. The
`vars/Debian.yml` values are frozen constants (single-OS scope). Two
variables are not plain defaults in the role and keep their resolution
shape: `nginx_user` (the `set_fact` fallback to `__nginx_user`) and
`nginx_worker_processes` (the fact-fed default).

| Variable | Default | Meaning |
| --- | --- | --- |
| `nginx_default_release` | `""` | apt release pin for the package; `""` → no pin |
| `nginx_ppa_use` | `false` | add `ppa:nginx/{{ nginx_ppa_version }}` and reinstall nginx from it |
| `nginx_ppa_version` | `"stable"` | the PPA's version channel |
| `nginx_package_name` | `"nginx"` | the package to install |
| `nginx_service_state` | `"started"` | final service state: `started`/`stopped`/`restarted`/`reloaded` |
| `nginx_service_enabled` | `true` | enable the service on boot |
| `nginx_conf_template` | `"nginx.conf.j2"` | the bundled template; any other value is a **control-side** path read at render time |
| `nginx_vhost_template` | `"vhost.j2"` | the bundled per-vhost template; a vhost's own `template` key overrides it per item |
| `nginx_user` | `null` → `"www-data"` | the `user` directive; `null` reproduces the role's `set_fact` fallback to `__nginx_user` |
| `nginx_worker_processes` | `null` → `"<vcpus>"` | `null` reproduces the fact-fed default (`ansible_facts.processor_vcpus`), **quotes included** — the role's folded scalar carries literal double quotes; an override renders verbatim (e.g. `"auto"`) |
| `nginx_worker_connections` | `"1024"` | `events.worker_connections` |
| `nginx_multi_accept` | `"off"` | `events.multi_accept` |
| `nginx_error_log` | `"/var/log/nginx/error.log warn"` | the `error_log` directive |
| `nginx_access_log` | `"/var/log/nginx/access.log main buffer=16k flush=2m"` | the `access_log` directive |
| `nginx_sendfile` | `"on"` | `sendfile` |
| `nginx_tcp_nopush` | `"on"` | `tcp_nopush` |
| `nginx_tcp_nodelay` | `"on"` | `tcp_nodelay` |
| `nginx_keepalive_timeout` | `"75"` | `keepalive_timeout` |
| `nginx_keepalive_requests` | `"600"` | `keepalive_requests` |
| `nginx_server_tokens` | `"on"` | `server_tokens` |
| `nginx_client_max_body_size` | `"64m"` | `client_max_body_size` |
| `nginx_server_names_hash_bucket_size` | `"64"` | `server_names_hash_bucket_size` |
| `nginx_proxy_cache_path` | `""` | `proxy_cache_path`; empty → the directive is omitted |
| `nginx_extra_conf_options` | `""` | raw lines inserted at main level |
| `nginx_extra_http_options` | `""` | raw lines inserted into `http {}` |
| `nginx_remove_default_vhost` | `false` | remove `sites-enabled/default` (notifies `restart nginx`) |
| `nginx_listen_ipv6` | `true` | the `[::]:80` listen lines in each vhost |
| `nginx_vhosts` | `[]` | list of vhost mappings (§3.1) |
| `nginx_upstreams` | `[]` | list of `{name, servers, strategy?, keepalive?}` upstream blocks |
| `nginx_log_format` | the role's `main` format | the `log_format main` body |
| `nginx_yum_repo_enabled` | `true` | accepted for parity, **inert** — a RedHat-only knob (§8) |
| `nginx_zypper_repo_enabled` | `true` | accepted for parity, **inert** — a Suse-only knob (§8) |

Invalid values (a bad `nginx_service_state` enum, a non-list
`nginx_vhosts`/`nginx_upstreams`, a vhost without `server_name` or
`filename`, an upstream without `name` or a non-empty `servers` list)
raise before any host is contacted. A vhost's `state` is deliberately
*not* enum-gated: the role only tests `!= 'absent'`, so any other spelling
installs, faithfully.

### 3.1 Vhost mapping fields

Each `nginx_vhosts` item supports the role's `vhost.j2` fields:
`server_name` (or an explicit `filename`), `state` (`absent` removes the
file), `template`, `listen`, `server_name_redirect` (renders the 301
redirect server block), `root`, `index`, `error_page`, `access_log`,
`error_log`, `return`, `extra_parameters`. The file name defaults to the
first `server_name` token + `.conf` — computed control-side, so Jinja2's
eager `default()` evaluation trap never fires.

## 4. Program shape: four deploys and their barriers

reemote runs a deploy twice (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence. The role
has two mutable reads a single deploy may not branch on — the PPA task's
`register` + `when … is changed`, and the fact-fed `worker_processes`
default. Each becomes a **gather → snapshot → factory** step between
complete runs:

```
main()
 ├─ load + validate inventory.json; resolve role_settings per target
 ├─ deploy 1: deploy_setup            apt.packages(dirmngr)
 │                                    apt.ppa(ppa:nginx/<version>,
 │                                      update_cache=True)  ← when nginx_ppa_use
 ├─ changed_hosts(setup report, ("Add PPA for Nginx",))
 │    └─ the register+when split: frozen set of hosts whose PPA changed
 ├─ deploy 2: build_install(ppa_added)
 │                                    conditional apt.packages(present=False)
 │                                    apt.update(cache_valid_time=86400)
 │                                    apt.packages(nginx, default_release=…)
 ├─ gather_fact(hardware.Cpus)        the worker_processes default's vCPU count
 │                                    (hosts closed manually afterwards)
 ├─ control-side: configure_plan      nginx.conf.j2 + vhost.j2 → rendered str
 │                                    (Jinja2); vhosts split install/remove
 ├─ deploy 3: build_configure(plans)  default-vhost removal, sites-enabled
 │                                    directory, vhost installs/removals,
 │                                    legacy cleanup, validated nginx.conf,
 │                                    service state
 ├─ changed_hosts(configure report, restart names / reload names)
 └─ deploy 4: build_handlers(plans, restart_notified, reload_notified)
                                      compose(service_handler restarted,
                                              service_handler reloaded)
                                      ← definition order; reload filtered by
                                        the static nginx_service_state check
```

The barriers are load-bearing: the PPA outcome read sits between the setup
and install runs (only a host whose PPA *really* changed reinstalls); the
Cpus gather runs **after** the install apply; the renders happen **before**
the configure deploy so its request sequence is a pure function of frozen
input; the handlers read the configure apply report — the only notifying
deploy — exactly as Ansible flushes handlers at the end of the play.

## 5. Coverage table

Status vocabulary: **covered** (direct operation), **recipe** (documented
shape change), **dead** (never runs in the role either).

### `tasks/main.yml`

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 1 | `include_vars: "{{ ansible_facts.os_family }}.yml"` | frozen `DEBIAN_VARS` (single-OS scope) | recipe |
| 2 | `set_fact: nginx_user={{ __nginx_user }}` when undefined | `role_settings()` `None` → `www-data` | covered |
| 3 | `include_tasks: setup-RedHat/FreeBSD/OpenBSD/Archlinux/Suse.yml` | scoped out (§8) | out of scope |
| 4 | `include_tasks: setup-Ubuntu.yml` (distribution == Ubuntu) | deploys 1 + 2 | covered |
| 5 | `include_tasks: setup-Debian.yml` (os_family == Debian) | deploy 2 | covered |
| 6 | `import_tasks: vhosts.yml` | deploy 3 | covered |
| 7 | `template: nginx.conf.j2`, `root:root 0644`, `validate: nginx -t -c %s`, notify reload | control-side Jinja2 render + `files.install(content, mode="0644", user="root", group="root", validate="nginx -t -c {spool}")` | recipe |
| 8 | `service: name=nginx state=… enabled=…` (no notify) | `systemd.service(running/restarted/reloaded/enabled)` at the end of deploy 3 | covered |

### `tasks/setup-Ubuntu.yml` → deploys 1 + 2

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 9 | `apt: name=dirmngr state=present` | `apt.packages(packages=("dirmngr",))` | covered |
| 10 | `apt_repository: ppa:nginx/{{ nginx_ppa_version }}`, `update_cache: true`, `register: nginx_ppa_added`, when `nginx_ppa_use` | `apt.ppa(src="ppa:nginx/…", update_cache=True)` behind the static flag; idempotent via `AptSources`, refreshes only on a real add | covered |
| 11 | `apt: name=nginx state=absent` when `nginx_ppa_added is changed` | `changed_hosts(setup report, (PPA_NAME,))` → `build_install(ppa_added)` conditional removal | recipe |

### `tasks/setup-Debian.yml` → deploy 2

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 12 | `apt: update_cache=yes cache_valid_time=86400`, `changed_when: false` | `apt.update(cache_valid_time=86400)` — gates on `AptUpdateStamp`, no-op inside the window; `changed_when` moot (D3) | covered |
| 13 | `apt: name=nginx state=present default_release=…` | `apt.packages(packages=(…), default_release=… or None)` | covered |

### `tasks/vhosts.yml` → deploy 3

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 14 | `file: state=absent` default vhost, when flag, notify restart | `files.file(present=False, force=True)` (D2) | covered |
| 15 | `file: state=directory mode=0755`, notify reload | `files.directory(mode="0755")` | covered |
| 16 | `template:` vhost loop, `force: true`, `root:root 0644`, notify reload | per-item control-side render + `files.install(content, mode="0644", user="root", group="root")` | recipe |
| 17 | `file: state=absent` vhost loop (`state == 'absent'`), notify reload | `files.file(present=False, force=True)` per frozen filename | covered |
| 18 | `file: state=absent` legacy `vhosts.conf`, notify reload | `files.file(present=False, force=True)` | covered |

### `handlers/main.yml` → deploy 4

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 19 | `restart nginx` (`service: state=restarted`), notified by 14 | `changed_hosts(report, restart names)` → `service_handler(action="restarted")` | recipe |
| 20 | `validate nginx configuration` (`command: nginx -t …`) | never notified by any task — dead in the role, documented not implemented (D4) | dead |
| 21 | `reload nginx` (`service: state=reloaded`, when `nginx_service_state == "started"`), notified by 7, 15–18 | `changed_hosts(report, reload names)` → static per-host filter → `service_handler(action="reloaded")` | recipe |

## 6. Deliberate deviations from Ansible semantics

- **D1 — OS dispatch frozen.** `include_vars` per OS family becomes the
  module constant `DEBIAN_VARS`; the Ubuntu target needs no run-time
  family probe.
- **D2 — `file: state=absent` needs `force=True`.** reemote's `File` fact
  reads a symlink as "exists and is not a file", and `files.file`'s default
  `force=False` raises on it — Ubuntu's default vhost **is** a symlink.
  `files.file(present=False, force=True)` reproduces Ansible's
  type-agnostic removal (`rm -rf` on a symlink removes only the link,
  never its target). The operation covers the behavior with the right
  flag.
- **D3 — `changed_when: false` has no analogue.** The cache-refresh task
  suppresses its `changed` flag in Ansible; reemote's log reports the real
  fact-resolved delta. Moot: the task notifies nothing, so the flag has no
  downstream effect (its name is in no notifying set).
- **D4 — the dead handler stays dead.** `validate nginx configuration` is
  never notified by any task in the role; Ansible never runs it, so the
  migration documents it and does not implement it. (The *live* validation
  is the `nginx.conf` template's `validate:` hook — task 7.)
- **D5 — the fact-fed `worker_processes` default.** Ansible's
  `ansible_facts.processor_vcpus` becomes a `gather_fact(hardware.Cpus)`
  between the install and configure runs; the rendered default keeps the
  role's literal double quotes (`worker_processes  "4";` — the folded
  scalar spells them). An explicit override renders verbatim.
- **D6 — control-side render.** `template:` becomes a Jinja2 render in
  `main()`'s gap between runs; `files.install` takes the rendered `str`
  (reemote has no controller filesystem paths inside a deploy). Custom
  `nginx_conf_template`/`nginx_vhost_template`/per-vhost `template` values
  are control-side paths read at render time; the bundled templates keep
  their `{% block %}` tags and a `DictLoader` maps their names, so a custom
  template can `{% extends "vhost.j2" %}` exactly as the role README
  documents. Ansible's whitespace defaults (`trim_blocks`,
  `keep_trailing_newline`, `StrictUndefined`) are reproduced.
- **D7 — handler order and fan-in.** Ansible flushes handlers in
  *definition* order: `compose(service_handler(restarted),
  service_handler(reloaded))` restarts first, reloads second, and a host
  that notified both gets both (Ansible does not deduplicate across
  distinct handlers). The reload handler's `when: nginx_service_state ==
  "started"` is static per-host data, so it filters the frozen notification
  set in the factory. Note `service_handler` keeps `running=True`: a
  restart handler **starts a stopped unit**, exactly like Ansible's
  `service: state=restarted`.
- **D8 — stateless service verbs.** `nginx_service_state:
  restarted/reloaded` (and the handlers' verbs) always report changed when
  reached, faithful to Ansible's `service` module; the default `started`
  is fact-gated and converges. The handlers only run on hosts whose
  notifying requests changed, so a converged play restarts nothing.
- **D9 — RedHat/Suse knobs inert.** `nginx_yum_repo_enabled` and
  `nginx_zypper_repo_enabled` are accepted for inventory parity, unused on
  Ubuntu (§8).

## 7. Offline verification

`tests/test_roles_ansible_role_nginx.py` imports the example by path (its
file name is not a valid module identifier), never calls its `main()`, and
replays the orchestration barrier-for-barrier against persistent
`SimulatedHost` objects (`host_factory` returns the *same* host every run,
so pass 2 observes pass 1). Eleven tests prove:

- the first pass installs dirmngr + nginx, adds the PPA and refreshes the
  cache, materializes the reinstall split, installs the rendered
  `nginx.conf` (quoted fact-fed `worker_processes`, upstream block, extra
  option blocks) and the present vhost (redirect block, extra parameters),
  removes the symlinked default vhost, the legacy `vhosts.conf` and the
  retired vhost, starts and enables the service, and fires both handlers
  in definition order;
- **the second pass changes nothing and queues no handler** — the
  idempotence claim, including the PPA presence check, the
  `cache_valid_time` window and the content-idempotent writes; no
  between-pass subsystem bridge is needed (every mutator the deploys touch
  maintains its own seams);
- a non-Ubuntu host in the same inventory is never touched (every entry
  skipped, no state mutated);
- the `dry=True` preview mutates nothing;
- the default path (no toggles) skips the PPA, the reinstall split and the
  restart handler, keeps the default vhost — and converges too;
- `nginx_service_state: stopped` suppresses the reload handler (the static
  `when` filter) while the restart handler still fires and starts the
  stopped unit (D7);
- a rejected `nginx -t` (the spool seeded into `fail_validate`) aborts the
  apply before the install — the running config is preserved;
- the pure helpers: the `worker_processes` override renders verbatim, a
  custom vhost template can `{% extends "vhost.j2" %}`, the filename
  default and the variable gates.

Seeding choices (documented in the test docstring): the simulated apt
install records package names only, so the package's own files
(`nginx.conf`, the default-vhost **symlink**, the legacy and retired
vhosts, the `/etc/nginx` tree) are seeded as its post-install state; the
Cpus gather reads the `cpus` attribute seam; the PPA codename comes from
the `lsb_release` seam. The removal assertions read `remote_files`
directly.

## 8. Scoped out

- The RedHat/Rocky/AlmaLinux, Suse, Archlinux, FreeBSD and OpenBSD branches
  (`setup-RedHat.yml`, `setup-Suse.yml`, `setup-Archlinux.yml`,
  `setup-FreeBSD.yml`, `setup-OpenBSD.yml` and their repo/key tasks) — a
  Phase 0 single-OS decision.
- `nginx_yum_repo_enabled` / `nginx_zypper_repo_enabled` (RedHat/Suse-only;
  D9).
- The dead `validate nginx configuration` handler (D4) — dead in the role
  itself.