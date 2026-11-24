# php — PHP role

| | |
| --- | --- |
| Role | [`geerlingguy.php`](https://github.com/geerlingguy/ansible-role-php) |
| Module | [`reemote.roles.php`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/php.py) |
| Runner | [`examples/roles/php.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/php.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | `php_`-prefixed inventory keys → [`PhpVars`](#3-variables) |
| Waves | 12 fresh, 11 converged — eleven barriers plus the handler wave |
| Outputs | `PhpOutputs(version, extension_conf_paths, webserver_notified)` — read by a composing role |
| Extra deps | none — the role's four templates render in pure Python |
| Offline test | [`tests/roles/test_php.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_php.py) |

`php` installs and configures PHP on Debian-family hosts: the apt cache refresh
and the version-specific packages, the packaged APCu/OpCache ini cleanup, the
configuration directories and a rendered `php.ini` in every conf path, the
stray-extension-config sweep and the role's own ini files, the optional PHP-FPM
pools and service, and the role's two chained handlers (`restart webserver` →
`restart php-fpm`).

This is **the gate of the migration sequence**: it is the role that proves the
barrier model can carry Ansible's handler semantics. The example needed four
`run()` calls and five reads between them; the library role is one per-host
generator with **three checkpoints, one directly after each notifying stage**, and
the webserver notification set is the union of the three reports:

```python
yield from install(install_plan(hv, rv))(host)               # wave 1
install_report = yield checkpoint("install done")
package_changed = changed_hosts(install_report, PACKAGE_GATE_NAMES)
webserver_notified = changed_hosts(install_report, INSTALL_HANDLER_NAMES)
yield from packaged_cleanup(cleanup_plan, package_changed)(host)   # wave 2
cleanup_report = yield checkpoint("cleanup done")
webserver_notified |= changed_hosts(cleanup_report, CLEANUP_HANDLER_NAMES)
for ext_path in rv.ext_paths:                                # waves 3..8
    apcu_hits += yield fact(FindInFiles, ext_path, APCU_PATTERN)
    opcache_hits += yield fact(FindInFiles, ext_path, OPCACHE_PATTERN)
pool_dir_exists = yield fact(FileExists, pool_dir)           # wave 9
yield from configure(configure_plan(hv, rv, ...))(host)      # wave 10
configure_report = yield checkpoint("configure done")
webserver_notified |= changed_hosts(configure_report, CONFIGURE_HANDLER_NAMES)
fpm_direct = changed_hosts(configure_report, FPM_HANDLER_NAMES)
yield from handler(hv, rv, webserver_hosts, fpm_hosts)(host)  # wave 11
return PhpOutputs(rv.version, rv.ext_paths, webserver_notified)
```

Nothing is gathered into a module-level snapshot dict first, and no name set is
unioned across the inventory: the per-host body *is* the orchestration, and what
it publishes is a return value.

## 1. What it is, and where it came from

| Ansible task | reemote request | Status |
| --- | --- | --- |
| `include_vars: vars/Debian.yml`, `include_vars: Ubuntu-{{ major }}.yml` | `yield fact(LinuxDistribution)` → `UBUNTU_PHP_VERSIONS`, 8.2 fallback | recipe |
| the nine `set_fact: __php_*` fallback tasks, incl. `php{v}-json` when PHP < 8.0 | `resolve_php(hv, distribution)` — pure, control-side | covered |
| `apt: update_cache=yes cache_valid_time=86400` | `apt.update(cache_valid_time=86400)` | covered |
| `apt: name=… state=… install_recommends=…` + `register` + `notify` | `apt.packages(packages=…, present=…, latest=…, no_recommends=…)` | covered |
| `file: state=absent` packaged APCu/OpCache ini, `when: … and php_package_install.changed` | `packaged_cleanup(plan, package_changed)` → `files.file(present=False, force=True)` | recipe (D4) |
| `file: state=directory follow=true mode=0755` conf + ext paths | `files.directory(mode="0755")` | deviation (D3) |
| `template: php.ini.j2` per conf path, `when: php_use_managed_ini` | control-side `render_php_ini` → `files.install(content=…)` | recipe |
| `find: paths={{ item }} contains=<regex>` + `register` | `yield fact(FindInFiles, ext, pattern)` + `stray_configs` | deviation (D2) |
| `file: state=absent` stray configs, `when: basename != role filename` | `files.file(present=False, force=True)` per filtered hit | covered |
| `template: apc.ini.j2` / `opcache.ini.j2` `force=true` | control-side render → `files.install` | recipe |
| `file: state=absent` role ini, `when: not enabled` | `files.file(present=False, force=True)` | covered |
| `stat: {{ php_fpm_pool_conf_path \| dirname }}` (no `when`) | `yield fact(FileExists, pool_dir)` | deviation (D8) |
| `file: state=directory` pool dir, `when: stat.islnk is not defined` | `files.directory`, only when the read answered absent | recipe |
| `template: {{ item.pool_template }}` loop, `when: php_enable_php_fpm`, `notify: restart php-fpm` | control-side `render_www_conf` → one `files.install` per pool | deviation (D7) |
| `service: name={{ php_fpm_daemon }} state=… enabled=…` | `systemd.service(running/restarted/reloaded/enabled)` | covered |
| `handlers/main.yml`: `restart webserver` → `restart php-fpm` | three checkpoints → union → `handler()` = two `service_handler`s | deviation (D5, D6) |
| `include_tasks: setup-RedHat.yml`, `install-from-source.yml` | the frozen Ubuntu constants and one family guard | scoped out (§9) |
| the role's value gates (`\| bool`, `\| mandatory`, the state strings) | pydantic validation on `PhpVars` / `PhpFpmPool` | deviation (D10) |

No library gap blocked this migration, so none was filed: `FindInFiles` carries
the `find: contains` sweep, `FileExists` the `stat`, and the checkpoint
primitive (issue [#16](https://github.com/kimjarvis/reemote/issues/16)) reads the
wave it closes. What the role contributed back to the design is the placement
rule in §6 — the one thing here that fails silently if it is tidied away.

## 2. Install

```bash
pip install reemote        # or: uv add reemote
```

No extra at all, unlike [`apache`](apache.md), which needs `reemote[templates]`
to render `vhosts.conf.j2`. All four of this role's templates — `php.ini.j2`,
`apc.ini.j2`, `opcache.ini.j2`, `www.conf.j2` — are pure variable substitution
plus one `join` and two `{% if %}` blocks, so `render_php_ini` and friends are
plain f-strings. They were checked byte-identical against Jinja2 renders of the
role's own templates under Ansible's environment defaults (`trim_blocks=True`,
trailing newlines preserved, `{{ var }}` native-type passthrough) across a
15-scenario matrix; that is what lets the role's dual-use `php_opcache_enable`
render `1` rather than `True`.

SSH access to each Ubuntu host with the key named in its `connection`, and
passwordless sudo (or a `privilege` section in the inventory): every request
carries `sudo=True`, because reemote has no play-level `become`. The PHP packages
must be available from the configured repositories — the stock Ubuntu archive is
enough (20.04 → 7.4, 22.04 → 8.1, 24.04 → 8.3, any other major → the role's
Debian fallback 8.2).

## 3. Variables

`PhpVars` is a frozen pydantic model (`extra="forbid"`) with unprefixed field
names; the prefix passed to `resolve_vars` binds the inventory keys, so an
inventory written for the role needs no change. Precedence is
`model defaults < inventory extra < overrides`.

Values that reach a template verbatim are `str`, with `coerce_numbers_to_str`, so
an inventory writing `php_memory_limit: 512` renders `512` exactly as Jinja would
render the integer. Sequences are `tuple`, never `list`: the model is frozen, and
a value that reaches `op()` must hash.

### Packages and webserver

| Field | Type | Default | Inventory key |
| --- | --- | --- | --- |
| `default_version_debian` | `str \| None` | `null` → per-Ubuntu table, else `8.2` | `php_default_version_debian` |
| `packages` | `tuple[str, …] \| None` | `null` → the 13 version-specific packages (+ `php{v}-json` below 8.0) | `php_packages` |
| `packages_extra` | `tuple[str, …]` | `()` | `php_packages_extra` |
| `packages_state` | `Literal["present","latest","absent"]` | `present` | `php_packages_state` |
| `install_recommends` | `bool` | `true` | `php_install_recommends` |
| `enable_webserver` | `bool` | `true` — gates the handler, no request | `php_enable_webserver` |
| `webserver_daemon` | `str` | `null` → `apache2` | `php_webserver_daemon` |
| `enablerepo` | `str` | `""` — **inert** (RHEL-only) | `php_enablerepo` |
| `executable` | `str` | `"php"` — **inert** (source path) | `php_executable` |

### PHP-FPM

| Field | Type | Default | Inventory key |
| --- | --- | --- | --- |
| `enable_php_fpm` | `bool` | `false` | `php_enable_php_fpm` |
| `fpm_state` | `Literal["started","stopped","restarted","reloaded"]` | `started` | `php_fpm_state` |
| `fpm_handler_state` | `Literal["restarted","reloaded","started"]` | `restarted` | `php_fpm_handler_state` |
| `fpm_enabled_on_boot` | `bool` | `true` | `php_fpm_enabled_on_boot` |
| `fpm_daemon` | `str` | `null` → `php{v}-fpm` | `php_fpm_daemon` |
| `fpm_conf_path` | `str` | `null` → `/etc/php/{v}/fpm` | `php_fpm_conf_path` |
| `fpm_pool_conf_path` | `str` | `null` → `{fpm_conf_path}/pool.d/www.conf` | `php_fpm_pool_conf_path` |
| `fpm_pool_user` / `fpm_pool_group` | `str` | `null` → `www-data` | `php_fpm_pool_user`, `php_fpm_pool_group` |
| `fpm_listen` | `str` | `127.0.0.1:9000` | `php_fpm_listen` |
| `fpm_listen_allowed_clients` | `str` | `127.0.0.1` | `php_fpm_listen_allowed_clients` |
| `fpm_pm_max_children` / `_start_servers` / `_min_spare_servers` / `_max_spare_servers` | `int` | `50` / `5` / `5` / `5` | `php_fpm_pm_*` |
| `fpm_pm_max_requests` | `int` | `0` — renders the template's `500` (see §3.1) | `php_fpm_pm_max_requests` |
| `fpm_pm_status_path` | `str` | `""` — omits the line | `php_fpm_pm_status_path` |
| `fpm_pools` | `tuple[PhpFpmPool, …] \| None` | `null` → the one www pool above | `php_fpm_pools` |

### Paths

| Field | Default | Inventory key |
| --- | --- | --- |
| `conf_paths` | `null` → `/etc/php/{v}/{fpm,apache2,cli}` | `php_conf_paths` |
| `extension_conf_paths` | `null` → `/etc/php/{v}/{fpm,apache2,cli}/conf.d` | `php_extension_conf_paths` |
| `apc_conf_filename` | `null` → `20-apcu.ini` | `php_apc_conf_filename` |
| `opcache_conf_filename` | `null` → `10-opcache.ini` | `php_opcache_conf_filename` |
| `use_managed_ini` | `true` — when false, no `php.ini` is written at all | `php_use_managed_ini` |

The extension paths default from the *distribution* layout, exactly as
`vars/Debian.yml`'s `__php_extension_conf_paths` does — overriding `php_conf_paths`
moves where `php.ini` goes but not where the extension inis go, which is the
role's behaviour, not an oversight.

### php.ini and the extensions

`php.ini` is a flat set of `str` fields rendered verbatim: `short_open_tag`
(`Off`), `precision` (`14`), `output_buffering` (`4096`), `serialize_precision`
(`-1`), `disable_functions` (`()` → joined with `,`), `expose_php` (`On`),
`max_execution_time` / `max_input_time` (`60`), `max_input_vars` (`1000`),
`memory_limit` (`256M`), `error_reporting`
(`E_ALL & ~E_DEPRECATED & ~E_STRICT`), `display_errors` /
`display_startup_errors` (`Off`), `post_max_size` (`32M`), `realpath_cache_size`
(`32K`), `file_uploads` (`On`), `upload_max_filesize` (`64M`), `max_file_uploads`
(`20`), `allow_url_fopen` (`On`), `date_timezone` (`America/Chicago`),
`sendmail_path`, `session_save_handler` (`files`), `session_save_path` (`""`),
`session_cookie_lifetime` (`0`), `session_gc_probability` (`1`),
`session_gc_divisor` (`1000`), `session_gc_maxlifetime` (`1440`).

APCu: `enable_apc` (`true`), `apc_shm_size` (`96M`), `apc_enable_cli` (`0`).
OpCache: `opcache_zend_extension` (`opcache.so`), **`opcache_enable`**
(`"1"`), `opcache_enable_cli` (`0`), `opcache_memory_consumption` (`96`),
`opcache_interned_strings_buffer` (`16`), `opcache_max_accelerated_files`
(`4096`), `opcache_max_wasted_percentage` (`5`), `opcache_validate_timestamps`
(`1`), `opcache_revalidate_path` (`0`), `opcache_revalidate_freq` (`2`),
`opcache_max_file_size` (`0`), `opcache_blacklist_filename` (`""` — non-empty adds
the line).

`opcache_enable` is the one field declared `str | int | bool` rather than `str`
or `bool`: Ansible both gates tasks with it (`when: php_opcache_enable | bool`)
and renders it verbatim (`opcache.enable={{ … }}`), so the raw value's type has to
survive. A declared `bool` would render `True` where Jinja renders `1`; the gate
derives its own boolean through `opcache_enabled` / `_ansible_bool`.

### 3.1 Pool items

`php_fpm_pools` items are `PhpFpmPool` — the keys `www.conf.j2` reads.

| Field | Meaning |
| --- | --- |
| `pool_name`, `pool_listen` | **required**, non-empty (the template's `\| mandatory`) |
| `pool_template` | `www.conf.j2`; any other value **raises** (D7) |
| `pool_listen_allowed_clients`, `pool_pm`, `pool_pm_max_children`, `pool_pm_start_servers`, `pool_pm_min_spare_servers`, `pool_pm_max_spare_servers`, `pool_pm_max_requests`, `pool_pm_status_path` | optional, `null` → the template's own default |
| anything else | kept (`extra="allow"`) |

The optional fields stay `None` rather than value-defaulting, because the
template uses the falsy form `default(x, true)`: a pool that explicitly sets
`pool_pm_max_requests: 0` renders `500`, faithfully.

Invalid values — a bad enum, a non-boolean flag, a pool missing `pool_name`, a
custom `pool_template`, a typoed `php_` key — raise while the inventory is
resolved, before any host is contacted.

## 4. API

::: reemote.roles.php
    options:
      show_root_heading: false
      show_root_toc_entry: false

## 5. Run it

The role is library code; `examples/roles/php.py` is the runnable artifact — a
standalone program with its own `main()`, no shared runner. It loads and validates
`inventory.json` from the current working directory, resolves the role's variables
(the defaults overlaid with the entry's `php_`-prefixed keys), builds the deploy
from the `php` factory and drives it with `run_waves` — a `dry=True` one-wave
preview, then the real apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import PhpVars, php

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(PhpVars, "php_", inventory)
    deploy = php(vars_set)
    report, _hosts = await run_waves(inventory, deploy, dry=True)   # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)             # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/php.py
```

The `ubuntu` entry of the shipped `inventory.json` carries no `php_` keys, so the
role's defaults apply: FPM **off**, `memory_limit = 256M`, the three version-derived
conf paths, and `apache2` as the webserver the handler restarts. The `dry=True`
pass is a one-wave preview: the role's first item is the `LinuxDistribution`
barrier, so the run parks there (`barrier: LinuxDistribution → unknown until run`),
reads no state and applies nothing. A converged rerun is the real preview.

`main()` prints the wave tree and maps the role's failure modes (bad inventory,
`ValueError` from a rejected variable or a version that is not dotted digits,
`CommandError`, `HostConnectionError`, `PrivilegeError`, `OSError`, `TypeError`) to
a one-line stderr message and exit 1.

**Run the program twice.** The second run must report no change and queue no handler: the
packages are installed, the cache stamp is fresh, the packaged inis are already
gone, every rendered file is byte-identical, the pool is where it belongs, the unit
is running and enabled. That is the migration's idempotence claim, and the offline
suite asserts it — including that the three checkpoints then notify nobody.

> **Shared demo host.** The `ubuntu` demo host also runs the apache and nginx
> examples. With its default `php_webserver_daemon` this role's handler wave
> restarts `apache2.service`; set `php_webserver_daemon: "nginx"` (or
> `php_enable_webserver: false`) to leave the other role's unit alone. Enabling
> FPM additionally starts `php{v}-fpm.service`.

## 6. What it does

One deploy. Each barrier closes its own wave, and a wave with no records is not
appended — so the shape below is what the test pins:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `fact(LinuxDistribution)` | the major version selects the PHP version, which fixes the package list and every managed path. Nothing can be planned before it |
| 1 | `install`: `apt.update(cache_valid_time=86400)`, `apt.packages(…)` — closed by `checkpoint("install done")` | the only wave the **gate** reads: `package_changed` decides whether the cleanup stage runs at all |
| 2 | `packaged_cleanup`: the packaged APCu/OpCache ini removals — closed by `checkpoint("cleanup done")` | a converged host's install changed nothing, so this wave holds no requests; the checkpoint record alone keeps it alive |
| 3 … 8 | one `fact(FindInFiles, ext_path, pattern)` per extension conf path, APCu sweep then OpCache sweep | **after** the cleanup applied: a packaged ini the cleanup deleted must not still be reported as a stray |
| 9 | `fact(FileExists, pool_dir)` | the role's unguarded `stat`; its answer decides whether the pool directory is created |
| 10 | `configure`: the directories, the `php.ini` in every conf path, the stray removals, the role's extension inis, the pool dir, the pools and the FPM service — closed by `checkpoint("configure done")` | the third checkpoint also reads the pool task's own notification (`fpm_direct`), which the webserver set never sees |
| 11 | `handler`: `Restart <webserver> (handler)`, then the chained `Restart <fpm> (handler)` | only on the hosts those checkpoints selected; the wave disappears entirely once nothing notified |

Four rules are load-bearing:

* **A checkpoint follows the stage it reports on.** Three checkpoints after one
  combined wave would read two *empty* reports and lose every cleanup and
  configure notification — silently, because a handler that is never queued does
  not fail, it just never runs. This is the easiest bug to introduce in the whole
  migration, and `tests/roles/test_php.py` pins it from the other side: change
  something in one stage alone and that stage's checkpoint is the only record that
  can see it.
* **The three channels are not symmetric.** `package_changed` reads the install
  wave only (it is a gate, not a notification), `webserver_notified` is the
  **union of all three**, and `fpm_direct` reads the configure wave only. Hence
  four distinct name sets, with `PACKAGE_GATE_NAMES is INSTALL_HANDLER_NAMES`
  because that one task both gates the cleanup *and* notifies.
* **The handler sets are the union narrowed by this role's own gates** —
  `php_enable_webserver` for the webserver set, `php_enable_php_fpm and
  php_fpm_state == "started"` for `webserver_hosts | fpm_direct`. A host can reach
  the FPM restart through the pool task alone, without notifying the webserver.
* **Requests do not close waves**, so a host's own configure entries are always in
  the wave its own checkpoint reads. `service_handler` acts only when `host.name`
  is in the set it is given, which keeps a wave a host's own.

A host whose `family` is not `"Ubuntu"` returns before its first barrier: no
requests, no barriers, no report lines — but it still returns
`PhpOutputs(version=None)`, which is how a composing role learns to skip it too.

The return value is the whole composition story (§9 of the design doc).
`php_xdebug` runs the role inline and reads the version and the extension paths
back out of it instead of keeping its own copy; a role that restarts the webserver
(see [`apache`](apache.md)) consumes `webserver_notified` as an explicit relay.
There is no registry and no cross-role address: every channel is a value the
composing program passes.

## 7. Transcript

From `tests/roles/test_php.py::test_tree_snapshot`, against the simulated Ubuntu
24.04 host plus a RedHat host the role never touches. The inventory sets
`php_enable_php_fpm: true`, `php_memory_limit: "512M"`,
`php_fpm_pm_status_path: "/status"` and `php_opcache_blacklist_filename`, so both
conditional template lines and the FPM branch are exercised. Fresh host, first
pass:

```text
(root)
  wave 0
    barrier: LinuxDistribution → {'name': 'Ubuntu', 'family': 'Debian', 'major': 24, 'minor': 0, 'release_meta': {}}
  wave 1
    ubuntu: Update apt cache         [changed: apt update]
        $ sudo apt-get update
    ubuntu: Ensure PHP packages are installed [changed: 13 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install php8.3-common php8.3-cli php8.3-dev php8.3-fpm libpcre3-dev php8.3-gd php8.3-curl php8.3-imap php8.3-opcache php8.3-xml php8.3-mbstring php8.3-apcu php8.3-sqlite3
    barrier: install done → 1 host notified
  wave 2
    ubuntu: Delete APCu configuration file if this role will provide one [changed: /etc/php/8.3/fpm/conf.d/20-apcu.ini present, will remove]
        $ sudo rm -f /etc/php/8.3/fpm/conf.d/20-apcu.ini
    ubuntu: Delete APCu configuration file if this role will provide one [changed: /etc/php/8.3/apache2/conf.d/20-apcu.ini is not a file, will remove first; file /etc/php/8.3/apache2/conf.d/20-apcu.ini does not exist]
        $ sudo rm -rf /etc/php/8.3/apache2/conf.d/20-apcu.ini
    ubuntu: Delete APCu configuration file if this role will provide one [changed: /etc/php/8.3/cli/conf.d/20-apcu.ini is not a file, will remove first; file /etc/php/8.3/cli/conf.d/20-apcu.ini does not exist]
        $ sudo rm -rf /etc/php/8.3/cli/conf.d/20-apcu.ini
    ubuntu: Delete OpCache configuration file if this role will provide one [changed: /etc/php/8.3/fpm/conf.d/10-opcache.ini is not a file, will remove first; file /etc/php/8.3/fpm/conf.d/10-opcache.ini does not exist]
        $ sudo rm -rf /etc/php/8.3/fpm/conf.d/10-opcache.ini
    ubuntu: Delete OpCache configuration file if this role will provide one [changed: /etc/php/8.3/apache2/conf.d/10-opcache.ini present, will remove]
        $ sudo rm -f /etc/php/8.3/apache2/conf.d/10-opcache.ini
    ubuntu: Delete OpCache configuration file if this role will provide one [changed: /etc/php/8.3/cli/conf.d/10-opcache.ini is not a file, will remove first; file /etc/php/8.3/cli/conf.d/10-opcache.ini does not exist]
        $ sudo rm -rf /etc/php/8.3/cli/conf.d/10-opcache.ini
    barrier: cleanup done → 1 host notified
  wave 3
    barrier: FindInFiles → ['/etc/php/8.3/fpm/conf.d/20-apcu.ini']
  wave 4
    barrier: FindInFiles → []
  wave 5
    barrier: FindInFiles → []
  wave 6
    barrier: FindInFiles → ['/etc/php/8.3/apache2/conf.d/10-opcache.ini', '/etc/php/8.3/apache2/conf.d/stray-opcache.ini']
  wave 7
    barrier: FindInFiles → ['/etc/php/8.3/cli/conf.d/.hidden-apcu.ini', '/etc/php/8.3/cli/conf.d/nested/old-apcu.ini', '/etc/php/8.3/cli/conf.d/stray-apcu.ini', '/etc/php/8.3/cli/conf.d/zz-commented.ini']
  wave 8
    barrier: FindInFiles → []
  wave 9
    barrier: FileExists → True
  wave 10
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/fpm already exists]
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/apache2 already exists]
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/cli already exists]
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/fpm/conf.d already exists]
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/apache2/conf.d already exists]
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/cli/conf.d already exists]
    ubuntu: Place PHP configuration file in place [changed: content /etc/php/8.3/fpm/php.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-8d471761f3da1a8b/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/fpm && cp /tmp/reemote-install-8d471761f3da1a8b/content /etc/php/8.3/fpm/php.ini && chown root:root /etc/php/8.3/fpm/php.ini && chmod 644 /etc/php/8.3/fpm/php.ini && rm -rf /tmp/reemote-install-8d471761f3da1a8b'
    ubuntu: Place PHP configuration file in place [changed: content /etc/php/8.3/apache2/php.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-f4df07cbd5c858a9/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/apache2 && cp /tmp/reemote-install-f4df07cbd5c858a9/content /etc/php/8.3/apache2/php.ini && chown root:root /etc/php/8.3/apache2/php.ini && chmod 644 /etc/php/8.3/apache2/php.ini && rm -rf /tmp/reemote-install-f4df07cbd5c858a9'
    ubuntu: Place PHP configuration file in place [changed: content /etc/php/8.3/cli/php.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-7fcbaa98f7a007e5/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/cli && cp /tmp/reemote-install-7fcbaa98f7a007e5/content /etc/php/8.3/cli/php.ini && chown root:root /etc/php/8.3/cli/php.ini && chmod 644 /etc/php/8.3/cli/php.ini && rm -rf /tmp/reemote-install-7fcbaa98f7a007e5'
    ubuntu: Remove any non-role-supplied APCu config files [changed: /etc/php/8.3/cli/conf.d/stray-apcu.ini present, will remove]
        $ sudo rm -f /etc/php/8.3/cli/conf.d/stray-apcu.ini
    ubuntu: Remove any non-role-supplied APCu config files [changed: /etc/php/8.3/cli/conf.d/zz-commented.ini present, will remove]
        $ sudo rm -f /etc/php/8.3/cli/conf.d/zz-commented.ini
    ubuntu: Remove any non-role-supplied OpCache config files [changed: /etc/php/8.3/apache2/conf.d/stray-opcache.ini present, will remove]
        $ sudo rm -f /etc/php/8.3/apache2/conf.d/stray-opcache.ini
    ubuntu: Ensure APCu config file is present [changed: content /etc/php/8.3/fpm/conf.d/20-apcu.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-3b5aab3fe5a37fb1/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/fpm/conf.d && cp /tmp/reemote-install-3b5aab3fe5a37fb1/content /etc/php/8.3/fpm/conf.d/20-apcu.ini && chown root:root /etc/php/8.3/fpm/conf.d/20-apcu.ini && chmod 644 /etc/php/8.3/fpm/conf.d/20-apcu.ini && rm -rf /tmp/reemote-install-3b5aab3fe5a37fb1'
    ubuntu: Ensure APCu config file is present [changed: content /etc/php/8.3/apache2/conf.d/20-apcu.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-f8189c511495c305/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/apache2/conf.d && cp /tmp/reemote-install-f8189c511495c305/content /etc/php/8.3/apache2/conf.d/20-apcu.ini && chown root:root /etc/php/8.3/apache2/conf.d/20-apcu.ini && chmod 644 /etc/php/8.3/apache2/conf.d/20-apcu.ini && rm -rf /tmp/reemote-install-f8189c511495c305'
    ubuntu: Ensure APCu config file is present [changed: content /etc/php/8.3/cli/conf.d/20-apcu.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-58cf0d063c112510/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/cli/conf.d && cp /tmp/reemote-install-58cf0d063c112510/content /etc/php/8.3/cli/conf.d/20-apcu.ini && chown root:root /etc/php/8.3/cli/conf.d/20-apcu.ini && chmod 644 /etc/php/8.3/cli/conf.d/20-apcu.ini && rm -rf /tmp/reemote-install-58cf0d063c112510'
    ubuntu: Ensure OpCache config file is present [changed: content /etc/php/8.3/fpm/conf.d/10-opcache.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-b785ad1a5215c134/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/fpm/conf.d && cp /tmp/reemote-install-b785ad1a5215c134/content /etc/php/8.3/fpm/conf.d/10-opcache.ini && chown root:root /etc/php/8.3/fpm/conf.d/10-opcache.ini && chmod 644 /etc/php/8.3/fpm/conf.d/10-opcache.ini && rm -rf /tmp/reemote-install-b785ad1a5215c134'
    ubuntu: Ensure OpCache config file is present [changed: content /etc/php/8.3/apache2/conf.d/10-opcache.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-33945299ae856c39/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/apache2/conf.d && cp /tmp/reemote-install-33945299ae856c39/content /etc/php/8.3/apache2/conf.d/10-opcache.ini && chown root:root /etc/php/8.3/apache2/conf.d/10-opcache.ini && chmod 644 /etc/php/8.3/apache2/conf.d/10-opcache.ini && rm -rf /tmp/reemote-install-33945299ae856c39'
    ubuntu: Ensure OpCache config file is present [changed: content /etc/php/8.3/cli/conf.d/10-opcache.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-1e2b5a2439f9a37f/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/cli/conf.d && cp /tmp/reemote-install-1e2b5a2439f9a37f/content /etc/php/8.3/cli/conf.d/10-opcache.ini && chown root:root /etc/php/8.3/cli/conf.d/10-opcache.ini && chmod 644 /etc/php/8.3/cli/conf.d/10-opcache.ini && rm -rf /tmp/reemote-install-1e2b5a2439f9a37f'
    ubuntu: Create fpm pools         [changed: content /etc/php/8.3/fpm/pool.d/www.conf differs, will install]
        $ scp <files install> -> /tmp/reemote-install-996094595c368e8a/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/fpm/pool.d && cp /tmp/reemote-install-996094595c368e8a/content /etc/php/8.3/fpm/pool.d/www.conf && chown root:root /etc/php/8.3/fpm/pool.d/www.conf && chmod 644 /etc/php/8.3/fpm/pool.d/www.conf && rm -rf /tmp/reemote-install-996094595c368e8a'
    ubuntu: Ensure php-fpm is started and enabled at boot (if configured) [changed: php8.3-fpm.service not running (None), will start; php8.3-fpm.service not enabled, will enable]
        $ sudo systemctl start php8.3-fpm.service
        $ sudo systemctl enable php8.3-fpm.service
    barrier: configure done → 1 host notified
  wave 11
    ubuntu: Restart apache2 (handler) [changed: apache2.service not running (None), will start]
        $ sudo systemctl start apache2.service
    ubuntu: Restart php8.3-fpm (handler) [changed: php8.3-fpm.service already running; php8.3-fpm.service will restart]
        $ sudo systemctl restart php8.3-fpm.service
```

The six `FindInFiles` barrier lines are the sweeps' raw answers, and the seed
deliberately mixes the cases `stray_configs` sorts out: a packaged regular file, a
dotfile, a nested file, a commented line, and a stray. Only the direct, non-hidden,
non-role hits come back as removals. The `scp` spool directories are
content-addressed, so a live run shows different hashes for a different content;
the test pins these.

The rerun — every barrier re-read, the gate finds no install change so **wave 2
carries no requests**, and the handler wave is gone entirely:

```text
(root)
  wave 0
    barrier: LinuxDistribution → {'name': 'Ubuntu', 'family': 'Debian', 'major': 24, 'minor': 0, 'release_meta': {}}
  wave 1
    ubuntu: Update apt cache         [no change: apt update skipped, cache is fresh]
    ubuntu: Ensure PHP packages are installed [no change: all packages present]
    barrier: install done → 0 hosts notified
  wave 2
    barrier: cleanup done → 0 hosts notified
  wave 3
    barrier: FindInFiles → ['/etc/php/8.3/fpm/conf.d/20-apcu.ini']
  wave 4
    barrier: FindInFiles → []
  wave 5
    barrier: FindInFiles → []
  wave 6
    barrier: FindInFiles → ['/etc/php/8.3/apache2/conf.d/10-opcache.ini', '/etc/php/8.3/apache2/conf.d/stray-opcache.ini']
  wave 7
    barrier: FindInFiles → ['/etc/php/8.3/cli/conf.d/.hidden-apcu.ini', '/etc/php/8.3/cli/conf.d/nested/old-apcu.ini', '/etc/php/8.3/cli/conf.d/stray-apcu.ini', '/etc/php/8.3/cli/conf.d/zz-commented.ini']
  wave 8
    barrier: FindInFiles → []
  wave 9
    barrier: FileExists → True
  wave 10
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/fpm already exists]
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/apache2 already exists]
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/cli already exists]
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/fpm/conf.d already exists]
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/apache2/conf.d already exists]
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/cli/conf.d already exists]
    ubuntu: Place PHP configuration file in place [no change: content already up to date (/etc/php/8.3/fpm/php.ini)]
    ubuntu: Place PHP configuration file in place [no change: content already up to date (/etc/php/8.3/apache2/php.ini)]
    ubuntu: Place PHP configuration file in place [no change: content already up to date (/etc/php/8.3/cli/php.ini)]
    ubuntu: Remove any non-role-supplied APCu config files [no change: file /etc/php/8.3/cli/conf.d/stray-apcu.ini does not exist]
    ubuntu: Remove any non-role-supplied APCu config files [no change: file /etc/php/8.3/cli/conf.d/zz-commented.ini does not exist]
    ubuntu: Remove any non-role-supplied OpCache config files [no change: file /etc/php/8.3/apache2/conf.d/stray-opcache.ini does not exist]
    ubuntu: Ensure APCu config file is present [no change: content already up to date (/etc/php/8.3/fpm/conf.d/20-apcu.ini)]
    ubuntu: Ensure APCu config file is present [no change: content already up to date (/etc/php/8.3/apache2/conf.d/20-apcu.ini)]
    ubuntu: Ensure APCu config file is present [no change: content already up to date (/etc/php/8.3/cli/conf.d/20-apcu.ini)]
    ubuntu: Ensure OpCache config file is present [no change: content already up to date (/etc/php/8.3/fpm/conf.d/10-opcache.ini)]
    ubuntu: Ensure OpCache config file is present [no change: content already up to date (/etc/php/8.3/apache2/conf.d/10-opcache.ini)]
    ubuntu: Ensure OpCache config file is present [no change: content already up to date (/etc/php/8.3/cli/conf.d/10-opcache.ini)]
    ubuntu: Create fpm pools         [no change: content already up to date (/etc/php/8.3/fpm/pool.d/www.conf)]
    ubuntu: Ensure php-fpm is started and enabled at boot (if configured) [no change: php8.3-fpm.service already running; php8.3-fpm.service already enabled]
    barrier: configure done → 0 hosts notified
```

With `php_enable_php_fpm: false` the tree keeps **all eleven barriers** — the
pool-directory `stat` is unguarded in the role, and its answer is what gates the
directory task — and loses only requests: no `Create fpm pools`, no FPM service
task, and one handler request instead of two. The `web1` RedHat host appears in no
tree at all: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | A host outside the `"Ubuntu"` family emits **nothing at all** | the family guard is the body's opening `if`, not a `when=is_ubuntu` on every request. The old example logged each request as `[skipped: when guard …]`; a return emits no records — and still returns `PhpOutputs(version=None)` |
| D2 | `find: {paths, contains}` becomes **`FindInFiles`** (a recursive `grep -rlE`) | Ansible's `find` is shallow, skips dotfiles and anchors `contains` at the line start. The shallowness and dotfile rules are recovered control-side by `stray_configs`, which keeps only direct, non-hidden children and drops the role's own filename; the broader line match is kept (it only ever widens a *stray* cleanup), and the removals are sorted because grep's traversal order is filesystem-dependent. Symlinks agree: both searches skip them, which is why a stock `conf.d` gives the same answer |
| D3 | No `follow` on the conf directories | `files.directory` owns no such parameter. The role's `follow: true` is inert on stock Ubuntu, where every conf path is a real directory; a symlinked one fails fast rather than being followed |
| D4 | Removals of packaged inis ask `force=True` | those files are symlinks into `mods-available`, and the `File` fact reads a symlink as a non-file, so `files.file(present=False, force=True)` is what removes the link itself |
| D5 | The handler verbs are `restarted` / `reloaded` / `running` | `service_handler` offers only these; `php_fpm_handler_state: started` maps onto `running`. Ansible's `stopped` was never a handler verb here — the role documents `restarted`/`reloaded` |
| D6 | The chained handler collapses to an **upstream-first service listing** | yielding the webserver request before the FPM request on the same host is all the `notify` chain guaranteed. Neither the example's `compose` nor its whole-inventory `sorted` name lists survive: a per-host body needs neither |
| D7 | A custom `pool_template` **raises** | only the bundled `www.conf.j2` has a Python render, so a pool naming another template is refused rather than silently rendered with the bundled one. Pools omitting optional keys render the template's defaults, slightly more permissive than Ansible, whose `\|length` on an undefined `pool_pm_status_path` errors |
| D8 | The pool-directory read is `FileExists` (`test -e`), not `stat` | a *dangling symlink* at the path reads absent here, where `stat` reports `islnk` defined and the role skips creation. On a stock Ubuntu the path is a real directory or absent, so the edge is unreachable in practice |
| D9 | `ansible_managed` is frozen to `"Ansible managed"` | `www.conf.j2` renders it with the `comment(decoration='; ')` filter. A configurable `ansible.cfg` value has no analogue here, and the timestamped variant the role's default carries would rewrite the file — and fire the handler — on every run |
| D10 | The role's implicit gates become **pydantic validation** | the three enums are `Literal` fields, the bool gates are `bool` fields (Jinja's truthiness is what pydantic's lax coercion implements), a pool needs `pool_name` and `pool_listen`, and a bad value raises `ValidationError` — a `ValueError`, so the runner maps it — before any host is contacted |
| D11 | `php_enablerepo` and `php_executable` are **carried and inert**; check mode has no analogue | the repo knob belongs to the RHEL branch that is out of scope, `php_executable` to the source path. The role also skips its service tasks under `ansible_check_mode`; reemote's `dry` stops before that wave, so there is nothing to reproduce |

Two behaviours look like deviations and are not. `php_fpm_state: restarted` /
`reloaded` (and the handler's verbs) always report changed when reached — faithful
to Ansible's `service` module, and why a converged host that *did* change something
still shows a handler change. And the extension conf paths default from the
distribution layout rather than from an overridden `php_conf_paths` — that is what
`vars/Debian.yml` says.

## 9. Scoped out

* **Non-Ubuntu families.** The role's `setup-RedHat.yml` / `vars/RedHat.yml`
  branch is not migrated: a Phase-0 single-OS decision. `include_vars`' OS
  dispatch disappears into frozen constants, and a RedHat host is skipped, not
  mapped to `php-fpm` on `httpd` — that is a different role.
* **Installing PHP from source.** `install-from-source.yml` and its
  `php_source_*` variables are opt-in (`php_install_from_source` defaults false).
  Their `command:`/`shell:` steps would migrate as `act(Shell)`/`act(Script)`
  barriers — the documented build-from-source recipe — a separate workstream.
  `php_executable` (`command: which {{ php_executable }}`) belongs to it, so it is
  inert here.
* **Custom pool templates.** See D7; a template-engine workstream.
* **Managing the webserver.** The role only *restarts*
  `php_webserver_daemon`; installing or configuring it is
  [`apache`](apache.md)'s job (or the caller's own program).
* **Per-host privilege.** PHP needs root and the inventory says how a host
  elevates; there is no play-level `become` to reproduce, and one run has one
  privilege view per host.
* **The Ubuntu 16.04 `php_opcache_conf_filename` special case** — EOL, absent from
  the version table, and its value equals the default anyway.
