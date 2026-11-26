# php_xdebug — PHP Xdebug role

| | |
| --- | --- |
| Role | [`geerlingguy.php-xdebug`](https://github.com/geerlingguy/ansible-role-php-xdebug) |
| Module | [`reemote.roles.php_xdebug`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/php_xdebug.py) |
| Runner | [`examples/roles/php_xdebug.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/php_xdebug.py) |
| Target | **Ubuntu** — inherited from [`php`](php.md)'s family guard, not restated |
| Variables | the entry's `php_xdebug` role mapping → `PhpXdebugVars` |

<!---
| Depends on | [`reemote.roles.php`](php.md) — run inside this role's own body |
| Waves | 15 fresh, 13 converged — php's twelve plus xdebug's three |
| Extra deps | none — the role's one template is pure substitution |
| Offline test | [`tests/roles/test_php_xdebug.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_php_xdebug.py) |

`php_xdebug` builds [Xdebug](https://xdebug.org/) from source and loads it into
PHP: the build toolchain, the source tarball extracted into a workspace, the
`phpize && ./configure && make` build, the module moved into PHP's extension
directory under its versioned name, a rendered `xdebug.ini` in every extension
conf path, the optional CLI disable, and the two chained handlers
(`restart webserver` → `restart php-fpm`) it shares with its dependency.

This page is the **composition** proof of the migration sequence. `php` had to
show that the barrier model carries Ansible's handler semantics; this role shows
that a dependency can be *part of the stream* instead of a program an operator
runs first and keeps in sync by hand. There is no registry, no `gather` phase
between two `run()` calls and no copied constant:

```python
hv = v.for_host(host)
if not hv.enable:
    return

php_out = yield from php(php_v)(host)      # waves 0..11: php's own stream
if php_out.version is None:
    return                                 # outside the family: php skipped us

rv = resolve_xdebug(hv, php_out, php_v.for_host(host))
yield from setup(rv)(host)                 # wave 11, with php's handlers

built = yield fact(FileExists, rv.built_so)   # closes wave 11
if not built:
    result = yield act(Shell, command=build_command(rv), name="build xdebug", sudo=True)
    if result["exit_status"] != 0:             # a barrier's exit status is data
        raise CommandError(host.name, ...)     # so the role turns it into an error

yield from install(rv)(host)               # wave 13
install_report = yield checkpoint("xdebug install done")
webserver_notified = changed_hosts(install_report, WEBSERVER_NOTIFY_NAMES)
fpm_direct = changed_hosts(install_report, FPM_NOTIFY_NAMES)
...
yield from handler(rv, webserver_hosts, fpm_hosts)(host)   # wave 14
```

`php_out` is the whole dependency story: an ordinary return value carrying the
resolved PHP version and the extension conf paths, so the ini destinations, the
CLI removal path and the FPM daemon name all follow the dependency instead of
being re-derived beside it.

## 1. What it is, and where it came from

| Ansible task | reemote request | Status |
| --- | --- | --- |
| `include_vars: "{{ os_family }}.yml"`, the `when: is_ubuntu` on every task | `yield from php(php_v)(host)` then `if php_out.version is None: return` — the family dispatch is the dependency's guard | recipe |
| the PHP version and `php_extension_conf_paths` the role inherits from `geerlingguy.php` | :attr:`PhpOutputs.version` / :attr:`PhpOutputs.extension_conf_paths` | recipe (D2) |
| `set_fact: php_xdebug_module_path` / `php_xdebug_config_filename` when undefined | `resolve_xdebug()` — the `__php_xdebug_*` fallbacks, once, control-side | covered |
| `yum: name='@development'` (RedHat) | the frozen Ubuntu path; a RedHat host emits nothing | scoped out (§9) |
| `apt: name=build-essential state=present` (Debian, **no** `update_cache`) | `apt.packages(packages=("build-essential",), present=True)` | covered (D7) |
| `file: path={{ module_path }} state=directory root:root 0755` | `files.directory(mode="0755", user="root", group="root")` | covered |
| `unarchive: src=<url> dest={{ workspace }} creates={{ src_dir }} copy=false mode=0755` | `files.unarchive(src=url, dest=…, creates=src_dir)` — the gate is in-operation, `mode` not passed | covered (D6) |
| `command: {{ item }} chdir={{ src_dir }} creates=…/modules/xdebug.so` over `[phpize, ./configure, make]` | `act(Shell)` inside the body, `&&`-chained, gated on `fact(FileExists, built_so)` | recipe (D4, D5) |
| `copy: src=built dest=installed remote_src=true mode=0644` `notify: restart webserver` | `files.copy(src, dest, overwrite=True, mode="0644", …)` — the rename form | covered (D10) |
| `include_tasks: configure.yml` → `template: xdebug.ini.j2` per ext path, with the CLI `when` | control-side `render_xdebug_ini` → one `files.install(content=…)` per filtered `ini_dest` | recipe |
| `file: /etc/php/{{ php_version }}/cli/conf.d/… state=absent` `when: Debian and cli_disable` | `files.file(present=False, force=True)` | covered (D9) |
| the two handlers, inherited from `geerlingguy.php` (this role ships no `handlers/main.yml`) | one `checkpoint("xdebug install done")` → `handler()` = two `service_handler`s | deviation (D11, D13) |
| the role's value gates (`\| bool`, the version shape) | pydantic validation on `PhpXdebugVars` | deviation (D12) |

No library gap blocked this migration, so none was filed: the build command that
the old example ran through `gather_action` is an ordinary `act(Shell)` barrier,
and `files.unarchive` already carries the `creates` gate. What the migration
contributed back is the `roles` binding channel itself: each entry carries a
per-role mapping, so the dependency and the child role resolve as sibling role
names under one `roles` dict instead of nesting key prefixes — see
[Role Variables](../rolevars.md) and design doc `role_vars.md` (D4).

## 2. Install

```bash
pip install reemote        # or: uv add reemote
```

No extra: unlike [`apache`](apache.md), this role's single template is pure
variable substitution, so `render_xdebug_ini` is a plain f-string, verified
byte-identical to `templates/xdebug.ini.j2` — trailing newline and the
directive-quoting `"` included.

Two requirements the library cannot satisfy from the control machine:

* the **control** machine needs internet egress to `xdebug.org`. The tarball is
  fetched control-side as binary bytes by `files.unarchive`, spooled to the
  host's `/tmp`, extracted and cleaned up; the host itself never downloads.
* the **host** needs `tar`, `phpize` and a C toolchain. `build-essential` is
  installed by this role; `phpize` comes from `php<version>-dev`, which the
  dependency installs on the same stream — which is exactly what the `yield from`
  bought: the old example told the operator to run the php migration first.

SSH access with the key named in each `connection`, and passwordless sudo (or a
`privilege` section): every request carries `sudo=True`, because reemote has no
play-level `become`.

## 3. Variables

`PhpXdebugVars` is a frozen pydantic model (`extra="forbid"`) whose fields carry
bare names, so `resolve_vars(PhpXdebugVars, "php_xdebug", inventory)` binds the
entry's `php_xdebug` role mapping: `version` there sets `version`. Precedence is
`model defaults < inventory role mapping < overrides`.

The **dependency's** variables are deliberately absent from this model.
`php_default_version_debian`, `php_extension_conf_paths`, `php_webserver_daemon`,
`php_fpm_daemon`, `php_enable_php_fpm`, `php_fpm_state` and
`php_fpm_handler_state` belong to `PhpVars` — read through
:class:`~reemote.roles.php.PhpOutputs` and the dependency's own `VarsSet`, so
this model cannot drift out of step with the role it extends.

--->

::: reemote.roles.php_xdebug
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - php_xdebug

**Variables:**

### Build and module install

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `enable` | `bool` | `true` — **new knob**, skips the role *and* the dependency (D1) | `php_xdebug_enable` |
| `version` | `str` | `"3.3.2"` — dotted digits; fixes the URL, the source dir and the `.so` name | `php_xdebug_version` |
| `workspace` | `str` | `"/root"` — non-empty; where the source is extracted and built | `php_xdebug_workspace` |
| `module_path` | `str \| None` | `null` → `"/usr/lib/php7/modules"` (D8) | `php_xdebug_module_path` |
| `config_filename` | `str \| None` | `null` → `"20-xdebug.ini"` | `php_xdebug_config_filename` |
| `cli_disable` | `bool` | `false` — skip the CLI conf.d write **and** remove the stock CLI entry | `php_xdebug_cli_disable` |

<!---

### `xdebug.ini` render values

These stay raw `str` with `coerce_numbers_to_str`, because Ansible interpolates
them into the ini verbatim: the YAML integer `1` renders `1`, and a JSON `true`
is refused rather than rendering `True` where the shipped default string renders
`false` (D12). The directive names are Xdebug 2's on a Xdebug 3 build — the
role's own quirk, kept verbatim (D7).

| Field | Default | Field | Default |
| --- | --- | --- | --- |
| `coverage_enable` | `"1"` | `default_enable` | `"1"` |
| `remote_enable` | `"false"` | `remote_connect_back` | `"false"` |
| `remote_host` | `"localhost"` | `remote_port` | `"9000"` |
| `remote_log` | `"/tmp/xdebug.log"` | `remote_autostart` | `"false"` |
| `idekey` | `"sublime.xdebug"` | `max_nesting_level` | `"256"` |

### Resolving both roles

The two roles resolve as sibling names under one entry's `roles` dict:

```python
php_v = resolve_vars(PhpVars, "php", inventory)
xdebug_v = resolve_vars(PhpXdebugVars, "php_xdebug", inventory)
deploy = php_xdebug(xdebug_v, php_v)
```

Each role reads only its own mapping, so `php` and `php_xdebug` never see each
other's keys — a `version` in the `php_xdebug` mapping is not the parent's typo.
A caller that runs php on its own passes the *same* `VarsSet` to both,
which is the whole de-duplication story: there is no registry to collide with.

Invalid values — a version that is not dotted digits, an empty workspace, a
boolified render value, a key naming no field in the role mapping — raise while
the inventory is resolved, before any host is contacted.

## 5. Run it

The role is library code; `examples/roles/php_xdebug.py` is the runnable
artifact — a standalone program with its own `main()`, no shared runner. It loads
and validates `inventory.json` from the current working directory, resolves
**two** roles' variables, builds the deploy and drives it with `run_waves` — a
`dry=True` preview, then the real apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import PhpVars, PhpXdebugVars, php_xdebug

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    php_v = resolve_vars(PhpVars, "php", inventory)
    xdebug_v = resolve_vars(PhpXdebugVars, "php_xdebug", inventory)
    deploy = php_xdebug(xdebug_v, php_v)
    report, _hosts = await run_waves(inventory, deploy, dry=True)   # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)             # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/php_xdebug.py
```

One `run_waves` call is enough — there is nothing to sequence by hand between the
dependency and the role, because the dependency is a `yield from` inside the body.

The `dry=True` pass is a one-wave preview. The stream's first item is php's
`LinuxDistribution` barrier, so it parks there immediately
(`barrier: LinuxDistribution → unknown until run`), reads no state and applies
nothing. **Run the program a second time** for the honest preview: the packages
are installed, the tarball is already extracted so the `creates` gate skips the
fetch, `modules/xdebug.so` exists so the build action is skipped, the module and
every ini are byte-identical, and no handler is queued.

On a **fresh** host the tree shows `Restart apache2 (handler)` twice, in two
different waves: once from php's handler wave, because php changed something, and
once from xdebug's, because the ini write notified it. That is faithful to the
two roles' separate handler sets, and the driver has no de-duplication — see D13.

> **Shared demo host.** The `ubuntu` demo host also runs the php, apache and
> nginx examples. This role's handler wave restarts `php_webserver_daemon`
> (default `apache2`) and, when `php_enable_php_fpm` is set, `php{v}-fpm`. The
> dependency's variables are the knobs: `webserver_daemon: "nginx"` or
> `enable_webserver: false` in the entry's `php` role mapping leaves another
> role's unit alone, and `enable: false` in the `php_xdebug` mapping skips both
> roles for that host.

`main()` maps the failure modes (bad inventory, `ValueError` from a rejected
variable, `CommandError` from a failed build, `HostConnectionError`,
`PrivilegeError`, `OSError`, `TypeError`) to a one-line stderr message and exit 1.

## 6. What it does

One deploy, one stream. Each barrier closes its own wave, and a wave with no
records is not appended — so the shape below is what the test pins:

| Wave | Item | Why |
| --- | --- | --- |
| 0 … 10 | php's own stream: the distribution read, `install done`, the packaged-ini cleanup and `cleanup done`, the six `FindInFiles` sweeps, the pool-directory read, `configure` and `configure done` | see [`php`](php.md); this role adds nothing to them and reads nothing out of their records |
| 11 | php's `handler` requests **plus** xdebug's `setup`: `apt.packages(build-essential)`, `files.directory(module_path)`, `files.unarchive(…)` — closed by `fact(FileExists, built_so)` | nothing separates php's last wave from xdebug's first, so they share one. The gate is read *here*, after the extraction is planned to apply in this wave, which is why the branch can live inside the body |
| 12 | `act(Shell, "build xdebug")` — only on a host whose module is absent | `files.copy` validates `src` in both phases, so the build must be a barrier before the install deploy |
| 13 | `install`: `files.copy` (the versioned module), one `files.install` per ini destination, the optional `files.file` CLI removal — closed by `checkpoint("xdebug install done")` | the only notifying stage of this role, hence its one checkpoint |
| 14 | `handler`: `Restart <webserver> (handler)`, then the chained `Restart <fpm> (handler)` | only on the hosts that checkpoint selected, and only past the dependency's own gates; the wave disappears entirely once nothing notified |

Four rules are load-bearing:

* **One checkpoint, two name sets.** Every notifying task is in the `install`
  deploy, so `WEBSERVER_NOTIFY_NAMES` (three task names) and
  `FPM_NOTIFY_NAMES` (the latter two) are both read out of *the same* report.
  Two checkpoints would read the second one an empty wave and lose the module
  move's notification — silently, because a handler that is never queued does not
  fail, it just never runs.
* **php's notifications are not borrowed.** A checkpoint can only read the wave it
  closes, and php's handler wave closed inside the `yield from`. Folding
  `PhpOutputs.webserver_notified` into the webserver set here would be
  faithful-looking and wrong: it would restart the webserver on php's account
  from Xdebug's handler wave. `tests/roles/test_php_xdebug.py` pins it from the
  other side — drift one file *php* manages and the tree shows one restart, in
  php's wave, with `barrier: xdebug install done → 0 hosts notified`.
* **The handler sets are the notification narrowed by the dependency's gates** —
  `php_enable_webserver` for the webserver set, `php_enable_php_fpm and
  php_fpm_state == "started"` for the FPM set, which unions the direct configure
  notifications with the webserver chain. The gates read `php_v`, not this role's
  variables, because they are the dependency's own conditions.
* **A non-Ubuntu host emits nothing at all.** php's guard returns
  `PhpOutputs(version=None)` before its first barrier and this body returns on
  that `None` — no request here needs a `when` guard, and the host appears in no
  wave of the tree.

The role returns `None`. What the dependency publishes travels *inside* the body,
not out of it: a composing role of its own would call `php` the same way.

## 7. Transcript

From `tests/roles/test_php_xdebug.py::test_tree_snapshot`, against a simulated
Ubuntu 24.04 host that has neither PHP nor Xdebug, plus a RedHat host the stream
never touches. The inventory sets `php_enable_php_fpm: true`, so the chained FPM
handler is exercised; everything else is the role's defaults. First pass — php's
twelve waves, then xdebug's:

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
    ubuntu: Delete APCu configuration file if this role will provide one [no change: file /etc/php/8.3/fpm/conf.d/20-apcu.ini does not exist]
    ubuntu: Delete APCu configuration file if this role will provide one [no change: file /etc/php/8.3/apache2/conf.d/20-apcu.ini does not exist]
    ubuntu: Delete APCu configuration file if this role will provide one [no change: file /etc/php/8.3/cli/conf.d/20-apcu.ini does not exist]
    ubuntu: Delete OpCache configuration file if this role will provide one [no change: file /etc/php/8.3/fpm/conf.d/10-opcache.ini does not exist]
    ubuntu: Delete OpCache configuration file if this role will provide one [no change: file /etc/php/8.3/apache2/conf.d/10-opcache.ini does not exist]
    ubuntu: Delete OpCache configuration file if this role will provide one [no change: file /etc/php/8.3/cli/conf.d/10-opcache.ini does not exist]
    barrier: cleanup done → 0 hosts notified
  wave 3
    barrier: FindInFiles → []
  wave 4
    barrier: FindInFiles → []
  wave 5
    barrier: FindInFiles → []
  wave 6
    barrier: FindInFiles → []
  wave 7
    barrier: FindInFiles → []
  wave 8
    barrier: FindInFiles → []
  wave 9
    barrier: FileExists → False
  wave 10
    ubuntu: Ensure configuration directories exist [changed: /etc/php/8.3/fpm missing, will create]
        $ sudo mkdir -p /etc/php/8.3/fpm
        $ sudo chmod 755 /etc/php/8.3/fpm
    ubuntu: Ensure configuration directories exist [changed: /etc/php/8.3/apache2 missing, will create]
        $ sudo mkdir -p /etc/php/8.3/apache2
        $ sudo chmod 755 /etc/php/8.3/apache2
    ubuntu: Ensure configuration directories exist [changed: /etc/php/8.3/cli missing, will create]
        $ sudo mkdir -p /etc/php/8.3/cli
        $ sudo chmod 755 /etc/php/8.3/cli
    ubuntu: Ensure configuration directories exist [changed: /etc/php/8.3/fpm/conf.d missing, will create]
        $ sudo mkdir -p /etc/php/8.3/fpm/conf.d
        $ sudo chmod 755 /etc/php/8.3/fpm/conf.d
    ubuntu: Ensure configuration directories exist [changed: /etc/php/8.3/apache2/conf.d missing, will create]
        $ sudo mkdir -p /etc/php/8.3/apache2/conf.d
        $ sudo chmod 755 /etc/php/8.3/apache2/conf.d
    ubuntu: Ensure configuration directories exist [changed: /etc/php/8.3/cli/conf.d missing, will create]
        $ sudo mkdir -p /etc/php/8.3/cli/conf.d
        $ sudo chmod 755 /etc/php/8.3/cli/conf.d
    ubuntu: Place PHP configuration file in place [changed: content /etc/php/8.3/fpm/php.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-8d471761f3da1a8b/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/fpm && cp /tmp/reemote-install-8d471761f3da1a8b/content /etc/php/8.3/fpm/php.ini && chown root:root /etc/php/8.3/fpm/php.ini && chmod 644 /etc/php/8.3/fpm/php.ini && rm -rf /tmp/reemote-install-8d471761f3da1a8b'
    ubuntu: Place PHP configuration file in place [changed: content /etc/php/8.3/apache2/php.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-f4df07cbd5c858a9/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/apache2 && cp /tmp/reemote-install-f4df07cbd5c858a9/content /etc/php/8.3/apache2/php.ini && chown root:root /etc/php/8.3/apache2/php.ini && chmod 644 /etc/php/8.3/apache2/php.ini && rm -rf /tmp/reemote-install-f4df07cbd5c858a9'
    ubuntu: Place PHP configuration file in place [changed: content /etc/php/8.3/cli/php.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-7fcbaa98f7a007e5/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/cli && cp /tmp/reemote-install-7fcbaa98f7a007e5/content /etc/php/8.3/cli/php.ini && chown root:root /etc/php/8.3/cli/php.ini && chmod 644 /etc/php/8.3/cli/php.ini && rm -rf /tmp/reemote-install-7fcbaa98f7a007e5'
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
    ubuntu: Ensure the default pool directory exists [changed: /etc/php/8.3/fpm/pool.d missing, will create]
        $ sudo mkdir -p /etc/php/8.3/fpm/pool.d
        $ sudo chmod 755 /etc/php/8.3/fpm/pool.d
        $ sudo chown root:root /etc/php/8.3/fpm/pool.d
    ubuntu: Create fpm pools         [changed: content /etc/php/8.3/fpm/pool.d/www.conf is absent, will install]
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
    ubuntu: Ensure dependencies for building from source are installed (Debian). [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install build-essential
    ubuntu: Ensure Xdebug module path exists. [changed: /usr/lib/php7/modules missing, will create]
        $ sudo mkdir -p /usr/lib/php7/modules
        $ sudo chmod 755 /usr/lib/php7/modules
        $ sudo chown root:root /usr/lib/php7/modules
    ubuntu: Untar Xdebug.            [changed: will extract https://xdebug.org/files/xdebug-3.3.2.tgz into /root]
        $ scp <files unarchive> -> /tmp/reemote-unarchive-8c3a55373f4b95b4-xdebug-3.3.2.tgz
        $ sudo tar -xz -f /tmp/reemote-unarchive-8c3a55373f4b95b4-xdebug-3.3.2.tgz -C /root
        $ sudo rm -rf /tmp/reemote-unarchive-8c3a55373f4b95b4-xdebug-3.3.2.tgz
    barrier: FileExists → False
  wave 12
    barrier: build xdebug → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 13
    ubuntu: Move Xdebug module into place. [changed: will copy /root/xdebug-3.3.2/modules/xdebug.so to /usr/lib/php7/modules/xdebug-3.3.2.so]
        $ sudo cp -r -f /root/xdebug-3.3.2/modules/xdebug.so /usr/lib/php7/modules/xdebug-3.3.2.so
        $ sudo chown root:root /usr/lib/php7/modules/xdebug-3.3.2.so
        $ sudo chmod 644 /usr/lib/php7/modules/xdebug-3.3.2.so
    ubuntu: Copy xdebug ini into main extension config folder. [changed: content /etc/php/8.3/fpm/conf.d/20-xdebug.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-d26f9734c2abb699/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/fpm/conf.d && cp /tmp/reemote-install-d26f9734c2abb699/content /etc/php/8.3/fpm/conf.d/20-xdebug.ini && chown root:root /etc/php/8.3/fpm/conf.d/20-xdebug.ini && chmod 644 /etc/php/8.3/fpm/conf.d/20-xdebug.ini && rm -rf /tmp/reemote-install-d26f9734c2abb699'
    ubuntu: Copy xdebug ini into main extension config folder. [changed: content /etc/php/8.3/apache2/conf.d/20-xdebug.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-ad7d92489b0c88bf/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/apache2/conf.d && cp /tmp/reemote-install-ad7d92489b0c88bf/content /etc/php/8.3/apache2/conf.d/20-xdebug.ini && chown root:root /etc/php/8.3/apache2/conf.d/20-xdebug.ini && chmod 644 /etc/php/8.3/apache2/conf.d/20-xdebug.ini && rm -rf /tmp/reemote-install-ad7d92489b0c88bf'
    ubuntu: Copy xdebug ini into main extension config folder. [changed: content /etc/php/8.3/cli/conf.d/20-xdebug.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-8a008775467a73e2/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/cli/conf.d && cp /tmp/reemote-install-8a008775467a73e2/content /etc/php/8.3/cli/conf.d/20-xdebug.ini && chown root:root /etc/php/8.3/cli/conf.d/20-xdebug.ini && chmod 644 /etc/php/8.3/cli/conf.d/20-xdebug.ini && rm -rf /tmp/reemote-install-8a008775467a73e2'
    barrier: xdebug install done → 1 host notified
  wave 14
    ubuntu: Restart apache2 (handler) [changed: apache2.service already running; apache2.service will restart]
        $ sudo systemctl restart apache2.service
    ubuntu: Restart php8.3-fpm (handler) [changed: php8.3-fpm.service already running; php8.3-fpm.service will restart]
        $ sudo systemctl restart php8.3-fpm.service
```

Waves 0–10 are exactly the [`php`](php.md) transcript: the version-specific
packages, the packaged-ini cleanup, the six sweeps, the rendered `php.ini` and
pools. What this role adds starts in wave 11, where its three `setup` requests
ride php's handler wave — no barrier separates them, so the driver does not
invent one. `barrier: FileExists → False` then closes that wave: the source tree
exists, `modules/xdebug.so` does not, so wave 12 runs the build. Wave 13 installs
the module and the three inis, and its checkpoint selects this host for both
handlers — which is why wave 14 restarts `apache2.service` a second time.

The rerun — the gate finds the built module, so **wave 12 disappears entirely**;
the fetch is skipped by the `creates` gate, and no handler is queued:

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
    barrier: FindInFiles → []
  wave 4
    barrier: FindInFiles → []
  wave 5
    barrier: FindInFiles → []
  wave 6
    barrier: FindInFiles → []
  wave 7
    barrier: FindInFiles → []
  wave 8
    barrier: FindInFiles → []
  wave 9
    barrier: FileExists → False
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
    ubuntu: Ensure APCu config file is present [no change: content already up to date (/etc/php/8.3/fpm/conf.d/20-apcu.ini)]
    ubuntu: Ensure APCu config file is present [no change: content already up to date (/etc/php/8.3/apache2/conf.d/20-apcu.ini)]
    ubuntu: Ensure APCu config file is present [no change: content already up to date (/etc/php/8.3/cli/conf.d/20-apcu.ini)]
    ubuntu: Ensure OpCache config file is present [no change: content already up to date (/etc/php/8.3/fpm/conf.d/10-opcache.ini)]
    ubuntu: Ensure OpCache config file is present [no change: content already up to date (/etc/php/8.3/apache2/conf.d/10-opcache.ini)]
    ubuntu: Ensure OpCache config file is present [no change: content already up to date (/etc/php/8.3/cli/conf.d/10-opcache.ini)]
    ubuntu: Ensure the default pool directory exists [no change: directory /etc/php/8.3/fpm/pool.d already exists]
    ubuntu: Create fpm pools         [no change: content already up to date (/etc/php/8.3/fpm/pool.d/www.conf)]
    ubuntu: Ensure php-fpm is started and enabled at boot (if configured) [no change: php8.3-fpm.service already running; php8.3-fpm.service already enabled]
    barrier: configure done → 0 hosts notified
  wave 11
    ubuntu: Ensure dependencies for building from source are installed (Debian). [no change: all packages present]
    ubuntu: Ensure Xdebug module path exists. [no change: directory /usr/lib/php7/modules already exists]
    ubuntu: Untar Xdebug.            [no change: archive already extracted (/root/xdebug-3.3.2 exists)]
    barrier: FileExists → True
  wave 12
    ubuntu: Move Xdebug module into place. [no change: /usr/lib/php7/modules/xdebug-3.3.2.so already exists]
    ubuntu: Copy xdebug ini into main extension config folder. [no change: content already up to date (/etc/php/8.3/fpm/conf.d/20-xdebug.ini)]
    ubuntu: Copy xdebug ini into main extension config folder. [no change: content already up to date (/etc/php/8.3/apache2/conf.d/20-xdebug.ini)]
    ubuntu: Copy xdebug ini into main extension config folder. [no change: content already up to date (/etc/php/8.3/cli/conf.d/20-xdebug.ini)]
    barrier: xdebug install done → 0 hosts notified
```

Wave 11 keeps its three requests (each reporting no change) and its barrier —
`barrier: FileExists → True` — while wave 12 in the first pass held the action.
The two handler waves are gone, and `barrier: xdebug install done → 0 hosts
notified` is the tree's own statement of the idempotence claim.

The suite (`tests/roles/test_php_xdebug.py`, 30 tests, no network) pins, among
others: the barrier sequence of the composed stream, in order, from one
`run_waves` call; that setting `php_default_version_debian` moves every Xdebug
path and the FPM daemon name, because they are read from the dependency's
resolution rather than re-derived beside it; that
`php_extension_conf_paths` moves the ini writes but not the CLI removal (D3);
that both notification sets come out of the single checkpoint's report; that a
converged pass fetches nothing, builds nothing and queues nothing; that
`php_xdebug_enable: false` emits no records at all; that a failing build raises
`CommandError` before anything after it is reached; and — mechanically, against
the module's own syntax tree — that the deleted version table and distribution
read never come back.

Seeding the simulation needs two documented choices, neither of them a bridge:
the `archive_members` listing carries the `xdebug-3.3.2/` **directory** member,
which is what makes the `creates` gate converge on the second pass; and the
`Shell` seam answers from `shell_results` without mutating the file store, so
`BuildHost` (a `SimulatedHost` subclass) plants the module that `make` would have
built *inside* the seam. The old suite repaired it between two `run()` calls;
under `run_waves` nothing runs between the barriers, which is the point.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | `php_xdebug_enable` is **new** | the role has no such variable — an operator who wanted Xdebug off removed the role from the play — so there is no Ansible name to match. It is the knob the body opens with, and its position is the claim: `false` returns before the dependency's first barrier, so a disabled host pays for none of php's reads either |
| D2 | php's Ubuntu version table and the `php_version` / `php_extension_conf_paths` reconstruction are **deleted** | this is the falsifier of the return-value channel (design doc section 15). The old example carried its own copy of `UBUNTU_PHP_VERSIONS` and rebuilt the conf paths from the distribution, so an operator's `php_default_version_debian` moved php's files and left Xdebug's behind. Now `resolve_xdebug` takes them from `PhpOutputs`, and `test_the_role_carries_no_copy_of_php_resolution` asserts against the syntax tree that neither the table nor a `LinuxDistribution` read returns |
| D3 | `cli_ini` is still `/etc/php/<version>/cli/conf.d/<filename>`, spelled from the PHP version | `configure.yml`'s removal task interpolates `{{ php_version }}` rather than reading `php_extension_conf_paths`, so an override of the paths moves the writes but not the removal. Preserved verbatim rather than corrected — "fixing" it would silently leave a symlinked ini in a redirected CLI conf.d |
| D4 | The build is an `act(Shell)` barrier inside the stream, not `gather_action` between two `run()` calls | the same command, the same `sudo`, the same non-zero exit → `CommandError`. A per-host body needs none of the example's machinery: no host grouping by rendered command, no frozen set, no manual `close()` after a gather |
| D5 | The three loop items collapse to one `&&` chain, wrapped in `sh -c` explicitly | Ansible runs `phpize`, `./configure`, `make` in order and stops on the first failure, which is what `cd … && phpize && ./configure && make` does. `Shell` with `sudo=True` renders `"sudo " + command` verbatim and adds no wrapper of its own, so the wrapper is spelled here — the command that reaches the host is `sudo sh -c 'cd /root/xdebug-3.3.2 && phpize && ./configure && make'` |
| D6 | `files.unarchive` carries the `creates` gate; the task's `mode=0755` is not passed | `creates=src_dir` is in-operation, so a converged host is a no-op and never fetches. The role's mode lands as `chmod -R` on `dest`, which is the **shared** `/root` workspace — it would re-mode everything under it, not only the archive's members. The installed module gets 0644 through the copy anyway. Extract into a dedicated directory if you want the `mode=` parity |
| D7 | Xdebug-2 directive names on a Xdebug-3 build, and the legacy `/usr/lib/php7/modules` default, are kept verbatim | `xdebug.ini.j2` writes `xdebug.remote_enable` though 3.3.2 renamed it to `xdebug.mode`; `vars/Debian.yml` ships a `php7` extension dir that a PHP 8.3 host does not use. Both are the role's own, and rewriting either would silently change every operator's tuned ini. The path default is why `php_xdebug_module_path` exists — override it with `php -i \| grep extension_dir` for a loadable module |
| D8 | The CLI removal asks `force=True` | the stock CLI conf.d entry is a symlink into `mods-available`, and the `File` fact reads a symlink as a non-file, so `files.file(present=False, force=True)` is what removes the link itself — what Ansible's `state: absent` does to a symlink |
| D9 | `files.copy(overwrite=True)` is sha1-gated | the copy compares `RemoteFileSha1`: an equal-content target is a no-op, a drifted one converges once. That is Ansible's `force: yes` idempotence, and it is why a repeat run does not re-copy the `.so` |
| D10 | No apt cache refresh | the role's `apt: build-essential` task has no `update_cache`, so neither does this role. php's own `apt.update(cache_valid_time=86400)` runs earlier in the same stream, which is a dependency's business, not a substitute |
| D11 | The handler chain collapses to an upstream-first service listing | yielding the webserver request before the FPM request on the same host is all the `notify` chain guaranteed. `service_handler` offers `restarted` / `reloaded` / `running`, so `php_fpm_handler_state: started` maps onto `running`. Neither the old example's `compose` of one handler per host nor its whole-inventory name lists survive: a per-host body needs neither |
| D12 | The role's implicit gates become pydantic validation | `version` must be dotted digits (it names a directory and a file), `workspace` non-empty, the render values `str` with `coerce_numbers_to_str` — so `php_xdebug_coverage_enable: 1` renders `1` while a JSON `true` is refused. All of it raises `ValidationError` before any host is contacted |
| D13 | php's `webserver_notified` is **not** folded into this role's handler sets | see §6. A checkpoint reads only the wave it closes, and merging the two reports would make Xdebug restart a webserver for a change it had nothing to do with. `PhpOutputs.webserver_notified` exists for a role that *is* the webserver's owner (see [`apache`](apache.md)), not for every composing role |
| D14 | Check mode has no analogue beyond `dry=True` | the old example planned each deploy with a separate `run(dry=True)`; `run_waves(..., dry=True)` stops at the first barrier of the whole stream, which is php's distribution read. A converged rerun is the preview that shows real commands |

## 9. Scoped out

* **The RHEL/CentOS branch.** `yum: name='@development'`, `vars/RedHat.yml`'s
  `/usr/lib64/php/modules` and `xdebug.ini` — a Phase-0 single-OS decision. The
  role's `include_vars` dispatch disappears into the frozen Ubuntu constants.
* **Installing PHP.** Not this role's job, and no longer a note to the operator:
  the dependency runs inside the stream. A host where php declines to install
  (wrong family, `php_enable_webserver` irrelevant here) simply gets no Xdebug.
* **The RPM/`modxdebug` path and Xdebug 3's modern directives.** See D7.
* **Managing the webserver or PHP itself.** This role restarts
  `php_webserver_daemon`; installing and configuring it is
  [`php`](php.md)'s and [`apache`](apache.md)'s job.
* **Per-host privilege.** Xdebug needs root and the inventory says how a host
  elevates; there is no play-level `become` to reproduce.

--->
