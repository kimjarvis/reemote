# PHP Xdebug Ansible role → reemote

This example migrates the [geerlingguy.php-xdebug](https://github.com/geerlingguy/ansible-role-php-xdebug)
Ansible role to a Python program built on the reemote library
([`ansible-role-php-xdebug.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/ansible-role-php-xdebug/ansible-role-php-xdebug.py)).

The role builds [Xdebug](https://xdebug.org/) from source and loads it into PHP
on RHEL/CentOS and Debian/Ubuntu: the build toolchain, the source tarball
extraction, the `phpize && ./configure && make` build, the versioned module
install (`xdebug.so` → `xdebug-<version>.so`), a rendered `xdebug.ini` in every
extension conf path, the optional CLI disable, and the two chained handlers
(`restart webserver` → `restart php-fpm`) inherited from its
[`geerlingguy.php`](https://github.com/geerlingguy/ansible-role-php) dependency.

**This migration targets Ubuntu only.** Hosts in the `"Ubuntu"` OS family execute the deploy; every request carries the static `when` guard
`is_ubuntu`, so the other hosts in the shared `inventory.json` (Alpine, RedHat) are skipped without being contacted. The role's `include_vars` OS
dispatch collapses to the frozen `vars/Debian.yml` constants, and the
RHEL/CentOS branch (`yum: '@development'`) is scoped out (§8). Unlike the
sibling `geerlingguy.php` migration — where build-from-source is an opt-in
`php_install_from_source` branch — **here the build *is* the role's default and
only path**, so it is fully migrated, not scoped out.

The migration is complete: every task on the Ubuntu path maps to a covered
operation or a documented recipe.

## 1. Prerequisites

### 1.1 Packages that must be loaded before executing

| Package | Where | Why |
| --- | --- | --- |
| `reemote` | control machine | the library itself; a repo checkout installed by `uv sync` provides it |
| `build-essential` | target host | the C toolchain (`gcc`, `make`, …); installed **by the program itself** via `apt.packages` — nothing to pre-load |
| `tar` | target host | extracts the source tarball; present on every stock Ubuntu |
| PHP + `php<version>-dev` (`phpize`) | target host | provided by the **`geerlingguy.php` dependency**, *not* by this program — see §1.3 |

**reemote is the only control-side package.** No template engine is needed:
`xdebug.ini.j2` is pure variable substitution (no block tags, no filters), so
the program renders it with a plain Python f-string, verified byte-identical to
the role's template. No Jinja2, no PyYAML — the inventory is read with the
standard library's `json`, and the role defaults are frozen module constants.

The control machine needs **internet egress to `xdebug.org`**: the tarball is
fetched control-side as binary bytes by `files.unarchive`,
spooled to the host's `/tmp`, extracted and cleaned up. The host itself never
downloads anything.

### 1.2 Control machine

- [uv](https://docs.astral.sh/uv/) and Python ≥ 3.13 (the project's
  `requires-python`).
- This repository, with dependencies synced: `uv sync`.

### 1.3 Target host

- Ubuntu with systemd, reachable over SSH as a user that can `sudo` (the
  inventory's `privilege` section carries any run-as user/password — never
  the program).
- **PHP must already be installed**, with `phpize` available. This role's
  `meta/main.yml` declares a dependency on `geerlingguy.php`, which installs
  PHP, the `php<version>-dev` package (providing `phpize`), the extension conf
  directories and the two handlers. Run the sibling
  [`ansible-role-php.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/ansible-role-php/ansible-role-php.py)
  migration first (or otherwise install PHP + `php<version>-dev`); this program
  resolves the dependency's `php_version`, `php_extension_conf_paths` and
  daemon names but does **not** install PHP itself.
- The build needs a C toolchain — the program installs `build-essential`.

### 1.4 Inventory: what you must add

The program loads the project-root
[`inventory.json`](https://github.com/kimjarvis/reemote/blob/main/inventory.json).
A target entry needs `"group": "ubuntu"`; every role variable rides beside
`host`/`connection` as an extra key — reemote's `host_vars` mechanism
(`host.extra`):

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
  "php_xdebug_version": "3.3.2",
  "php_xdebug_module_path": "/usr/lib/php/20230831",
  "php_enable_php_fpm": true
}
```

With no extra keys the role defaults apply (§3) — the entry above only
illustrates the shape. Note `php_xdebug_module_path`: the role's Debian default
is the legacy `/usr/lib/php7/modules` (D5); on a modern PHP 8.3 host, override
it with the real extension dir (`php -i | grep extension_dir`) for a loadable
module.

## 2. Running it against `inventory.json` with uv

From the repository root:

```bash
uv sync                                        # once: install reemote + dev deps
uv run python examples/roles/ansible-role-php-xdebug/ansible-role-php-xdebug.py
```

No `--with` is needed — reemote is the only dependency (§1.1). The program:

1. loads and validates `inventory.json`, prints the hosts, and names the
   hosts the `is_ubuntu` guard skips;
2. resolves each target's role variables (defaults overlaid with the entry's
   extra keys) and fails fast on an invalid value — before any host is
   contacted;
3. gathers `LinuxDistribution` (the major version selects the PHP version
   table, which fixes the conf paths and handler daemons), then runs
   **deploy 1** (`build-essential`, the module directory, the source tarball
   extraction via `files.unarchive`);
4. gathers `FileExists` on the build's `creates:` path
   (`<src_dir>/modules/xdebug.so`) **after** the extraction, freezing the
   `need_build` set, then runs the **build action** (`phpize && ./configure &&
   make` via `gather_action(Shell)`) on those hosts only — a non-zero exit
   raises `CommandError`;
5. runs **deploy 2** (the versioned module copy via `files.copy`, the rendered
   `xdebug.ini` installs, the optional CLI disable), reads `changed_hosts`
   over the notifying request names, applies the handlers' `when` guards, and
   runs **deploy 3** (the chained `restart webserver` → `restart php-fpm`
   handlers);
6. prints each deploy twice: the `dry=True` plan (Ansible's `--check` as a
   return value — nothing is mutated) followed by the real apply log, then
   the handler notification sets and the total changed count.

**Run the whole program a second time** to see the idempotence claim: the
second pass reports no changes and queues no handler — the tarball is not
re-fetched (`creates` gate), the build is skipped (`need_build` empty), the
module copy is a sha1-equal no-op, and the ini writes are content-idempotent.

### Offline verification (no hosts, no approval needed)

```bash
uv run pytest tests/test_roles_ansible_role_php_xdebug.py -q
```

The test replays the program's whole orchestration against the test
framework's `SimulatedHost` and proves plan → apply → **second apply is a
no-op** (§7).

## 3. Role variables

Every `defaults/main.yml` variable is honoured under its original name, read
from the inventory entry's extra keys. The `vars/Debian.yml` values
(`__php_xdebug_module_path`, `__php_xdebug_config_filename`) are resolved by
`resolved_vars()` when the user leaves them undefined — the role's two
`set_fact` fallback tasks. The `geerlingguy.php` dependency variables the
handlers and conf paths need are resolved the same way.

### Build and module install

| Variable | Default | Meaning |
| --- | --- | --- |
| `workspace` | `"/root"` | where the tarball is extracted and built |
| `php_xdebug_version` | `"3.3.2"` | Xdebug release; fixes the tarball URL, the source dir and the installed `.so` name |
| `php_xdebug_module_path` | `null` → `"/usr/lib/php7/modules"` | the extension dir; the legacy Debian default (D5) — override on PHP 8.x |
| `php_xdebug_config_filename` | `null` → `"20-xdebug.ini"` | the ini filename placed in each conf.d |
| `php_xdebug_cli_disable` | `false` | skip the CLI conf.d ini **and** remove the stock CLI entry (Debian/Ubuntu only) |

### `xdebug.ini` render values (interpolated verbatim)

These stay **raw** — Ansible interpolates them straight into the ini, so
boolifying would render `True` where the role's string `"false"` renders
`false` (D11). The directive names are Xdebug 2's, even though the default
version 3.3.2 is Xdebug 3 — the role's own quirk, reproduced verbatim (D4).

| Variable | Default | Variable | Default |
| --- | --- | --- | --- |
| `php_xdebug_coverage_enable` | `1` | `php_xdebug_default_enable` | `1` |
| `php_xdebug_remote_enable` | `"false"` | `php_xdebug_remote_connect_back` | `"false"` |
| `php_xdebug_remote_host` | `"localhost"` | `php_xdebug_remote_port` | `"9000"` |
| `php_xdebug_remote_log` | `"/tmp/xdebug.log"` | `php_xdebug_remote_autostart` | `"false"` |
| `php_xdebug_idekey` | `"sublime.xdebug"` | `php_xdebug_max_nesting_level` | `256` |

### `geerlingguy.php` dependency variables (inherited)

| Variable | Default | Meaning |
| --- | --- | --- |
| `php_default_version_debian` | `null` → per-Ubuntu table | overrides the PHP version selection (7.4 / 8.1 / 8.3, else 8.2) |
| `php_extension_conf_paths` | `null` → each `/etc/php/{v}/{fpm,apache2,cli}/conf.d` | where the ini is installed |
| `php_enable_webserver` | `true` | whether the `restart webserver` handler fires |
| `php_webserver_daemon` | `null` → `"apache2"` | the webserver service the handler restarts |
| `php_enable_php_fpm` | `false` | whether the FPM handler is eligible |
| `php_fpm_state` | `"started"` | FPM service state; the handler fires only when `started` |
| `php_fpm_handler_state` | `"restarted"` | the chained handler's verb: `restarted`/`reloaded`/`started` (→ `running`, D6) |
| `php_fpm_daemon` | `null` → `"php{v}-fpm"` | the FPM service name |

Invalid values (a bad enum, a non-boolean flag, a non-string path, a
non-dotted-digit version) raise before any host is contacted.

## 4. Program shape: three deploys, one build action, and their barriers

reemote runs a deploy twice (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence. The role
reads the distribution major version (`include_vars` dispatch) and gates the
build on a `creates:` path — mutable reads a single deploy may not branch on.
Each becomes a **gather → snapshot → factory** step, and the build itself is a
`Shell` action *between* deploys (the build-from-source recipe: commands
never live inside a deploy):

```
main()
 ├─ load + validate inventory.json; resolve role_settings per target
 ├─ gather_fact(LinuxDistribution)     major → PHP version table (7.4/8.1/8.3,
 │                                     else 8.2) fixes conf paths + daemons
 │                                     (hosts closed manually afterwards)
 ├─ deploy 1: build_setup              apt.packages(build-essential)
 │                                     files.directory(module_path, 0755)
 │                                     files.unarchive(url, dest=workspace,
 │                                       creates=src_dir)
 ├─ gather_fact(FileExists, built_so)  the build's creates: gate, read AFTER
 │                                     the extraction → frozen need_build set
 │                                     (hosts closed manually afterwards)
 ├─ ACTION: gather_action(Shell)       cd src_dir && phpize && ./configure
 │                                     && make, on need_build hosts only (sudo;
 │                                     non-zero exit → CommandError)
 ├─ deploy 2: build_install            files.copy(built_so → installed_so,
 │                                       rename, 0644, overwrite)
 │                                     files.install(xdebug.ini) per ini_dest
 │                                     files.file(cli_ini, present=False)
 │                                       when cli_disable
 ├─ changed_hosts(install report, WEBSERVER_NOTIFY_NAMES / FPM_NOTIFY_NAMES)
 │                                     + the handlers' when guards
 └─ deploy 3: build_handler            service_handler per host: webserver
                                       first, then the chained FPM restart
                                       (compose)
```

The barriers are load-bearing:

- the distribution read happens **before** deploy 1 because it fixes the conf
  paths and handler daemon names;
- `FileExists(built_so)` is read **after** the extraction — the source tree
  exists now, but `modules/xdebug.so` does not until the build runs;
- the build action is a barrier **before** `build_install` because
  `files.copy` validates `src` in *both* phases;
- the module directory is created in `build_setup` (an earlier barrier)
  because the copy's rename form validates the target's *parent* directory in
  both phases too;
- the handler reads the install apply report — the only notifying deploy —
  exactly as Ansible flushes handlers at the end of the play.

## 5. Coverage table

Status vocabulary: **covered** (direct operation), **recipe** (documented
shape change).

### `tasks/main.yml`

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 1 | `include_vars: "{{ os_family }}.yml"` | frozen `vars/Debian.yml` constants (single-OS scope) | recipe |
| 2 | `set_fact: php_xdebug_module_path` when undefined | `resolved_vars()` — the `__php_xdebug_module_path` fallback | covered |
| 3 | `set_fact: php_xdebug_config_filename` when undefined | `resolved_vars()` — the `__php_xdebug_config_filename` fallback | covered |
| 4 | `yum: name='@development'` (RedHat) | scoped out (§8) | out of scope |
| 5 | `apt: name=build-essential state=present` (Debian, **no** `update_cache`) | `apt.packages(packages=("build-essential",), present=True)` — no cache refresh (D7) | covered |
| 6 | `unarchive: src=<url> dest=workspace creates=src_dir copy=false mode=0755` | `files.unarchive(src=url, dest=workspace, creates=src_dir)` — `mode` dropped (D2) | covered |
| 7 | `command: {{ item }} chdir=src_dir creates=…/modules/xdebug.so` loop `[phpize, ./configure, make]` | `gather_action(Shell)` between deploys, `&&` chain; `creates` gate via `FileExists` snapshot (D3) | recipe |
| 8 | `file: path=module_path state=directory root:root 0755` | `files.directory(mode="0755", user="root", group="root")` | covered |
| 9 | `copy: src=built_so dest=installed_so remote_src=true mode=0644` `notify: restart webserver` | `files.copy(src, dest, overwrite=True, mode="0644", user="root", group="root")` — rename form (D9) | covered |
| 10 | `include_tasks: configure.yml` | deploy 2 (`build_install`) | covered |

### `tasks/configure.yml` → deploy 2

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 11 | `template: xdebug.ini.j2 → {{ item }}/{{ config_filename }}` `root:root 0644`, `when: 'cli' not in item or (… not cli_disable)`, `with_items: php_extension_conf_paths`, `notify: [restart webserver, restart php-fpm]` | control-side `render_xdebug_ini` + one `files.install(content, mode="0644", …)` per filtered `ini_dest` | recipe |
| 12 | `file: /etc/php/{{ php_version }}/cli/conf.d/{{ config_filename }} state=absent`, `when: Debian and cli_disable`, `notify: both` | `files.file(present=False, force=True)` (D8) | covered |

### `handlers/main.yml` (inherited from `geerlingguy.php`) → deploy 3

This role ships **no** `handlers/main.yml`; the `notify:` targets resolve to
the dependency's handlers.

| # | Ansible handler | reemote target | Status |
| --- | --- | --- | --- |
| 13 | `restart webserver`: `service: state=restarted`, `when: php_enable_webserver`, `notify: restart php-fpm` | `changed_hosts` over `WEBSERVER_NOTIFY_NAMES` → `service_handler(action="restarted")` per host | recipe |
| 14 | `restart php-fpm`: `service: state={{ php_fpm_handler_state }}`, `when: php_enable_php_fpm and php_fpm_state == 'started'` | the chain collapses to an upstream-first `compose`: the webserver set (chained) ∪ the direct configure notifications, filtered by the guards → `service_handler(action=…)` (D6) | recipe |

## 6. Deliberate deviations from Ansible semantics

- **D1 — OS dispatch frozen, version table still gathered.** `include_vars`
  per OS family becomes module constants (Ubuntu-only scope); the PHP version
  is still selected at run time by `LinuxDistribution.major`
  (7.4 / 8.1 / 8.3, else the `vars/Debian.yml` 8.2 fallback), exactly as the
  `geerlingguy.php` dependency's `with_first_found` does. The dependency's
  `php_version`, `php_extension_conf_paths` and daemon names are resolved by
  `resolved_vars()`.
- **D2 — `unarchive: mode=0755` dropped.** `files.unarchive` has no `mode`
  parameter. The extracted *source tree's* modes are not converged to 0755;
  this is inert — the build does not depend on them, and the installed module
  gets 0644 through the copy.
- **D3 — build `creates:` gate reconstructed control-side.** A `Shell` action
  has no in-operation `creates`, so the gate is a `FileExists(built_so)`
  snapshot read after the extraction, freezing the `need_build` set. The three
  loop items (`phpize`, `./configure`, `make`) collapse to one `&&` chain —
  Ansible runs them in order and stops on the first failure, exactly what the
  chain does. Passwordless `sudo` renders `"sudo " + command` verbatim, so the
  chain is wrapped explicitly: `sudo sh -c 'cd … && phpize && …'`.
- **D4 — Xdebug-2 directive names preserved.** `xdebug.ini.j2` uses
  `xdebug.remote_enable` and friends even though the default version 3.3.2 is
  Xdebug 3 (which renamed them to `xdebug.mode`, `xdebug.client_host`, …).
  This is the role's own quirk; the render reproduces the template verbatim
  rather than "fixing" the directive names.
- **D5 — legacy module path preserved.** `vars/Debian.yml`'s
  `__php_xdebug_module_path` is `/usr/lib/php7/modules`. It is kept verbatim
  (overridable via `php_xdebug_module_path`); on a modern PHP 8.3 host the real
  extension dir differs, so override it in the inventory for a loadable module.
- **D6 — handler chain collapses to an upstream-first `compose`.** The
  `restart webserver` → `restart php-fpm` chain becomes a `compose` of
  `service_handler` deploys, webserver first (the documented handler-chaining
  recipe). `service_handler` offers
  `restarted`/`reloaded`/`running`; `php_fpm_handler_state: started` maps to
  `running`. Each host gets its own `service_handler` call so per-host daemon
  names resolve correctly.
- **D7 — no apt cache refresh.** The role's `apt: build-essential` task has no
  `update_cache`; neither does this migration.
- **D8 — CLI disable uses `force=True`.** The CLI `conf.d` entry is a symlink
  on a stock host; the `File` fact reads a symlink as a non-file, so
  `files.file(present=False, force=True)` removes the link itself — what
  Ansible's `state: absent` does to a symlink.
- **D9 — `overwrite=True` is sha1-gated.** `files.copy` compares
  `RemoteFileSha1`: an equal-content target is a no-op, a
  drifted target converges once. This is Ansible's `force: yes` idempotence,
  so a repeat run does not re-copy.
- **D10 — check mode.** reemote's `run(dry=True)` is Ansible's `--check` as a
  return value; the program performs a dry preview before every apply. A dry
  preview of the handler deploy is honest (it consults the frozen outcome); a
  dry preview of a notifying deploy cannot know whether handlers will fire.
- **D11 — render values stay raw.** `coverage_enable=1`, `remote_enable="false"`
  and friends are interpolated verbatim, not boolified — Ansible renders the
  role's string `"false"` as `false`, where a Python `False` would render
  `False`.

## 7. Offline verification

[`tests/test_roles_ansible_role_php_xdebug.py`](https://github.com/kimjarvis/reemote/blob/main/tests/test_roles_ansible_role_php_xdebug.py)
imports the example by path (its file name is not a valid module identifier),
never calls its `main()`, and replays the orchestration barrier-for-barrier
against persistent `SimulatedHost` objects (`host_factory` returns the *same*
host every run, so pass 2 observes pass 1). It proves:

- the first pass installs `build-essential`, creates the module directory,
  fetches and extracts the tarball (the `creates` gate fires once), runs the
  build action on the `need_build` host, copies the built module to its
  versioned name with `root:root 0644`, installs the rendered `xdebug.ini` in
  every conf path, and fires both chained handlers;
- a non-Ubuntu host in the same inventory is never touched;
- **the second pass changes nothing and queues no handler** — the
  idempotence claim: the tarball is not re-fetched (the `creates` gate
  converges), `need_build` is empty (no build action), the module copy is a
  sha1-equal no-op, the ini writes are content-idempotent, and the handler
  sets are empty;
- the `dry=True` preview mutates nothing;
- the `cli_disable` branch removes the CLI ini and skips the CLI conf.d write;
- the FPM handler gates (`php_enable_php_fpm`, `php_fpm_state == 'started'`)
  hold;
- the pure helpers (`render_xdebug_ini` byte-identity, `resolved_vars`,
  `role_settings` validation, `build_command`) are unit-tested directly.

The tarball fetch is answered offline by monkeypatching
`files._fetch_url_bytes`; the extraction by seeding the `archive_members` seam
keyed on the spool path (a directory member `xdebug-3.3.2/` makes the `creates`
gate converge); the build action by seeding `shell_results` with the exact
rendered key `sudo sh -c 'cd /root/xdebug-3.3.2 && phpize && ./configure &&
make'`; the distribution read by seeding `linux_distribution`. One
between-pass bridge repairs a *simulation* limit (not a library bug): the
`Shell` action seam does not mutate the file stores, so the test seeds
`built_so` (`remote_files` + `existing_files` + `file_stats`) after the build
action — what `make` produces for real. The remaining seeding choices are
documented in the test docstring.

## 8. Scoped out

- The RHEL/CentOS branch (`yum: '@development'`, `vars/RedHat.yml`'s
  `/usr/lib64/php/modules` + `xdebug.ini`) — a Phase 0 single-OS decision.
- Installing PHP itself — the `geerlingguy.php` dependency's job (§1.3); this
  program resolves the dependency's variables but assumes PHP + `phpize` are
  present.

The build-from-source path is **not** scoped out: it is this role's default
and only path, fully migrated as the `Shell` action between deploys 1 and 2.