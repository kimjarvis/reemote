# apache — Apache virtual-host role

| | |
| --- | --- |
| Role | [`geerlingguy.apache`](https://github.com/geerlingguy/ansible-role-apache) |
| Module | [`reemote.roles.apache`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/apache.py) |
| Example | [`examples/roles/apache.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/apache.py) |
| Target | **Ubuntu, RedHat** — hosts in the `"Ubuntu"` and `"RedHat"` OS families |
| Variables | the entry's `apache` role mapping → `ApacheVars` |

<!---
| Waves | Ubuntu: 3 **+ one per SSL vhost** (4 with a single certificate); RedHat: +1–3 ahead of configure for the distribution/cert/gencerts reads |
| Outputs | `ApacheOutputs(version, service)` — read by a composing role |
| Extra deps | **`reemote[templates]`** (jinja2 renders `vhosts.conf.j2`) |
| Offline test | [`tests/roles/test_apache.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_apache.py) |

`apache` installs and configures Apache 2.x on both target families: the apt
cache refresh and the `apache2` packages, `ports.conf`'s listen lines, the
`mods-enabled`/`conf-enabled` symlinks, a rendered `sites-available/vhosts.conf`
and its `sites-enabled` symlink, the optional default-vhost removal, the service's
running/enabled state, and the role's single `restart apache` handler — or, on
RedHat, the plain `dnf` install of the `httpd` quartet, the listen lines in
`conf/httpd.conf`, `LoadModule` stubs written into `conf.modules.d`, the same
rendered vhosts file landing in `conf.d`, the RHEL 8+ `httpd-ssl-gencerts` step,
and a `restart httpd` handler.

This is the **template role** of the migration sequence: it is where a role stops
being a list of requests and starts being a program that reads. The role's whole
`tasks/main.yml` — setup, the version probe, the two `include_vars` tables, the
certificate `stat` loop, configure, the service task, one handler — is one
per-host generator, and the three reads it took between play steps are barriers
inside that generator:

```python
yield from install(hv)(host)                       # wave 0
result = yield act(Shell, command="/usr/sbin/apache2 -v", name="apache -v")
version = parse_apache_version(result["stdout"])   # the table to include_vars
...  one yield fact(FileExists, path=…) per SSL vhost
plan = configure_plan(hv, version, ssl_exists)
yield from configure(plan)(host)                   # wave n+1
notified = changed_hosts(yield checkpoint("configure done"), notifying_names(plan))
yield from handler(plan, notified)(host)           # wave n+2
```

Nothing is gathered into a module-level snapshot dict first, and no notifying-name
set is unioned across the inventory: the per-host body *is* the plan. On RedHat
the body inserts up to three more reads ahead of `configure_plan` — the
`fact(LinuxDistribution)` behind the RHEL 8 gate, the localhost-cert
`fact(FileExists)` and, only while that cert is missing, the
`act(Shell, "/usr/libexec/httpd-ssl-gencerts", sudo=True)` barrier — and the
probe command is `/usr/sbin/httpd -v`.

## 1. What it is, and where it came from

| Ansible task | reemote request | Status |
| --- | --- | --- |
| `apt: update_cache=yes cache_valid_time=3600` | `apt.update(cache_valid_time=3600)` | covered |
| `apt: name={{ __apache_packages }} state={{ apache_packages_state }}` | `apt.packages(packages=…, present=…, latest=…)` | covered |
| `command: {{ apache_daemon_path }}{{ apache_daemon }} -v` + `register` | `yield act(Shell, command="/usr/sbin/apache2 -v")` | barrier (D2) |
| `include_vars: vars/apache-24.yml` / `apache-22.yml` | `configure_plan(hv, version, …)` picks the frozen table | recipe |
| `stat: path={{ item.certificate_file }}` + `register` | one `yield fact(FileExists, path=…)` per SSL vhost | barrier (D3) |
| `lineinfile: {{ apache_ports_configuration_items }}` | `files.transform(path, transform=…)` + `files.file(mode="0644")` | covered |
| `file: src=mods-available/… state=link mode=0644` | `files.link(path=…, target=…)` | deviation (D4) |
| `template: src=vhosts.conf.j2 dest=sites-available/…` | control-side render → `files.install(content=…)` | covered |
| `file: sites-enabled/… state=link force={{ ansible_check_mode }}` | `files.link(...)` (`force=False`) | deviation (D6) |
| `file: sites-enabled/{{ default_vhost_filename }} state=absent` | `files.link(present=False, force=True)` | covered |
| `service: name=apache2 state={{ apache_state }} enabled={{ apache_enabled }}` | `systemd.service(running=…, enabled=…, …)` | covered |
| `handlers/main.yml: restart apache` + the tasks' `notify` | `checkpoint("configure done")` → `changed_hosts` → `service_handler` | deviation (D7) |
| `include_vars: vars/Debian.yml`, `when: ansible_os_family == "Debian"` | the frozen constants, one family guard | deviation (D1) |
| the role's implicit value gates (`item.servername is defined`, the state strings) | pydantic validation on `ApacheVars` | deviation (D8) |
| `yum: name={{ __apache_packages }} enablerepo=… state=…` | `dnf.packages(packages=…, enable_repos=…)` | covered (D5) |
| `include_vars: vars/RedHat.yml`, `setup/configure-RedHat.yml` | the frozen RedHat constants, `install`/`configure_redhat` | deviation (D1) |
| `command: distribution_major_version >= 8` gate | `yield fact(LinuxDistribution)` + `supports_httpd_ssl_gencerts` | barrier (D10) |
| `stat: /etc/pki/tls/certs/localhost.crt` + `register` | `yield fact(FileExists, path=LOCALHOST_CERT)` | barrier (D10) |
| `command: /usr/libexec/httpd-ssl-gencerts` | `yield act(Shell, …, sudo=True)` before the configure wave | barrier (D10) |
| `copy: content=LoadModule …_module … dest=conf.modules.d/99-ansible-….conf` | `files.install(content=…)` | covered |
| `file: conf.modules.d/99-ansible-….conf state=absent` | `files.file(present=False)` | covered |
| `template: dest={{ apache_conf_path }}/vhosts.conf` (no enable link) | same control-side render → `files.install` into `conf.d` | covered |
| `service: name=httpd …` | `systemd.service(service="httpd", …)` | covered |

No library gap blocked either branch of this migration, so none was filed:
`files.transform` already carries the `lineinfile` semantics, `FileExists` the
`stat`, the `act` barrier (issue
[#16](https://github.com/kimjarvis/reemote/issues/16)) runs `Shell` as a command
whose exit status is data, and `dnf.packages(enable_repos=…)` is the additive
`yum: enablerepo=` spelling that leaves every other repo enabled. One
**design-sketch**
correction did come out of writing it: `yield` inside a generator expression is a
`SyntaxError`, and so is `list.append(yield …)`, so the certificate loop is an
explicit `for` (see D3).

## 2. Install

```bash
pip install "reemote[templates]"     # or: uv add "reemote[templates]"
```

`jinja2` is the only extra. The base library imports neither `jinja2` nor
`pyyaml` — they enter through `reemote/roles/_render.py`, and `apache` imports
that module *inside* `render_vhosts`, so `import reemote.roles` stays clean
without the extra and a missing `jinja2` surfaces as an `ImportError` at render
time rather than at import time.

SSH access to each Ubuntu host with the key named in its `connection`, and
passwordless sudo (or a `privilege` section in the inventory): every request
carries `sudo=True`, because reemote has no play-level `become`. The version
probe runs on the host too — it is an action barrier, not a controller-side call.

## 3. Variables

`ApacheVars` is a frozen pydantic model (`extra="forbid"`) whose fields carry bare
names; the role name passed to `resolve_vars` binds the entry's `apache` role
mapping, so an inventory written for the role needs no key gymnastics.

--->

::: reemote.roles.apache
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - apache

**Variables:**

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `listen_ip` | `str` | `*` | `apache_listen_ip` |
| `listen_port` | `int` | `80` | `apache_listen_port` |
| `listen_port_ssl` | `int` | `443` | `apache_listen_port_ssl` |
| `state` | `Literal["started","stopped","restarted","reloaded"]` | `started` | `apache_state` |
| `enabled` | `bool` | `true` | `apache_enabled` |
| `restart_state` | `Literal["restarted","reloaded","started"]` | `restarted` | `apache_restart_state` |
| `packages` | `tuple[str, …] \| None` | `null` → per family: `(apache2, apache2-utils)` / `(httpd, httpd-devel, mod_ssl, openssh)` | `apache_packages` |
| `packages_state` | `Literal["present","latest","absent"]` | `present` | `apache_packages_state` |
| `create_vhosts` | `bool` | `true` | `apache_create_vhosts` |
| `vhosts_filename` | `str` | `vhosts.conf` | `apache_vhosts_filename` |
| `vhosts_template` | `str` | `vhosts.conf.j2` | `apache_vhosts_template` |
| `vhosts` | `tuple[VHost, …]` | one `local.dev` | `apache_vhosts` |
| `vhosts_ssl` | `tuple[SslVHost, …]` | `()` | `apache_vhosts_ssl` |
| `global_vhost_settings` | `str` | `DirectoryIndex index.php index.html\n` | `apache_global_vhost_settings` |
| `remove_default_vhost` | `bool` | `false` — **RedHat-inert** | `apache_remove_default_vhost` |
| `allow_override` | `str` | `All` | `apache_allow_override` |
| `options` | `str` | `-Indexes +FollowSymLinks` | `apache_options` |
| `mods_enabled` | `tuple[str, …]` | `("rewrite", "ssl")` | `apache_mods_enabled` |
| `mods_disabled` | `tuple[str, …]` | `()` | `apache_mods_disabled` |
| `conf_enabled` | `tuple[str, …]` | `()` — **RedHat-inert** | `apache_conf_enabled` |
| `conf_disabled` | `tuple[str, …]` | `()` — **RedHat-inert** | `apache_conf_disabled` |
| `ignore_missing_ssl_certificate` | `bool` | `true` | `apache_ignore_missing_ssl_certificate` |
| `ssl_protocol` | `str` | `All -SSLv2 -SSLv3` | `apache_ssl_protocol` |
| `ssl_cipher_suite` | `str` | `AES256+EECDH:AES256+EDH` | `apache_ssl_cipher_suite` |
| `enablerepo` | `str` | `""` — Ubuntu-inert, **live on RedHat** (D5) | `apache_enablerepo` |
| `ssl_no_log` | `bool` | `true` — **inert** (no log redaction) | `apache_ssl_no_log` |

<!---

Precedence is `model defaults < inventory role mapping < overrides>. Sequences
are `tuple`, never `list`: the model is frozen and hashable, and a value that
reaches `op()` must hash. The inert knobs are carried rather than dropped so an
existing inventory still binds — tests pin that `ssl_no_log` changes no request on
either family, that `enablerepo` changes none on Ubuntu, and that
`conf_enabled`/`conf_disabled`/`remove_default_vhost` emit no RedHat request.

`vhosts_template` follows the role's own convention: `vhosts.conf.j2` is the
bundled template inlined in the module, and **any other value is a controller-side
path**, read by `render_vhosts` at build time. That read is ordinary Python inside
the body, not a `control` barrier, so a custom template costs no extra wave.

### 3.1 Vhost mapping fields

`vhosts` items are `VHost`; `vhosts_ssl` items are `SslVHost` (the
same fields plus the certificate paths).

| Field | `VHost` | `SslVHost` |
| --- | --- | --- |
| `servername` | **required**, non-empty | inherited |
| `serveralias`, `documentroot`, `serveradmin`, `allow_override`, `options`, `extra_parameters` | optional, `null` → *undefined* for the template | inherited |
| `certificate_file`, `certificate_key_file` | — | **required**, non-empty |
| `certificate_chain_file` | — | optional |
| anything else | kept (`extra="allow"`) | inherited |

The optional fields default to `null` and are dumped with
`model_dump(exclude_none=True)`, which is what keeps the template's
`{% if vhost.documentroot is defined %}` faithful: a vhost with no documentroot
renders no `DocumentRoot ""` and no `<Directory>` block. `allow_override` and
`options` are the per-vhost overrides of the role's own `allow_override` /
`options` defaults, applied by the template's `default()`.


## 5. Run it

The role is library code; `examples/roles/apache.py` is the runnable artifact — a
standalone program with its own `main()`, no shared runner. It loads and validates
`inventory.json` from the current working directory, resolves the role's variables
(the defaults overlaid with the entry's `apache` role mapping, then the call-site
`overrides`), builds the deploy from the `apache` factory and drives it with
`run_waves` — a `dry=True` one-wave preview, then the real apply — printing the
wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import ApacheVars, apache

APACHE_OVERRIDES = {
    "listen_port": 8080,
    "vhosts": ({"servername": "example.com", "documentroot": "/var/www/vhosts/example_com"},),
}

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(ApacheVars, "apache", inventory, overrides=APACHE_OVERRIDES)
    deploy = apache(vars_set)
    report, _hosts = await run_waves(inventory, deploy, dry=True)   # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)             # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/apache.py
```

The `ubuntu` entry of the shipped `inventory.json` carries no `apache` role
mapping, so the program's call-site `overrides` decide the listen port and the
vhosts: Apache listens on `8080` and serves one `example.com` vhost out of
`/var/www/vhosts/example_com`. Overrides use the model's bare field names and win
over both the defaults and any key in the entry's `apache` role mapping
(precedence: model defaults < inventory role mapping < overrides). The `dry=True`
pass is a one-wave preview: it plans the install requests, parks on the
`apache -v` barrier and stops (`barrier:
apache -v → unknown until run`), gathering facts over SSH but applying nothing. A
converged rerun is the real preview. Note that `web1`/`web2` in the shipped
inventory declare the `"RedHat"` family: since the httpd extension a live run
**installs and configures `httpd` on them**, where the Ubuntu-only role used to
skip them.

`main()` prints the wave tree and maps the role's failure modes (bad inventory,
`ValueError` from a rejected variable, a failed probe or an unsupported version,
`CommandError`, `HostConnectionError`, `PrivilegeError`, `OSError`, `TypeError`) to
a one-line stderr message and exit 1.

**Run the program twice.** The second run must report no change and queue no
handler: the packages are installed, the cache stamp is fresh (Ubuntu),
`ports.conf`/`httpd.conf` matches, every symlink and mod stub is where it
belongs, the rendered vhosts file is byte-identical, the unit is running and
enabled. That is the migration's idempotence claim, and the offline suite asserts
it on both families.

> **Shared demo host.** The `ubuntu` demo host also runs the nginx example. This
> example's `overrides` move Apache to port `8080`, so it no longer collides with
> nginx's `80`/`443`; an inventory that restores the role's default
> `listen_port: 80` in the `apache` mapping recreates the conflict, because this
> role leaves `apache2` enabled and active. See [the nginx page](nginx.md) for
> the stop/disable command to run afterwards.

### 5.1 Composing this role

`examples/roles/apache.py` runs `apache` on its own. To use it as a *component*
of a larger deploy, see
[`examples/roles/composing_roles.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/composing_roles.py):
a parent per-host generator embeds the role with
`out = yield from scoped("apache", apache(v))(host)` — tagging every item apache
emits with an `"apache"` report path, feeding its barriers, and handing back the
returned `ApacheOutputs`. The parent reads that output as data: it runs `php`
only where apache published a non-empty `version`, so a host the family guard
skipped (`version=""`) never stacks a dependent role. Because `run_waves` never
surfaces a role's return value, the parent captures outputs by closing over a
dict and storing what `yield from` hands back.

## 6. What it does

One deploy. Each barrier closes its own wave, and a wave with no records is not
appended — so the wave count is **host-dependent**, which is the point of the
barrier model:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `install`: `apt.update(cache_valid_time=3600)` + `apt.packages(…)` on Ubuntu, the single `dnf.packages(…)` on RedHat — closed by the version-probe `act(Shell)` barrier (`…/apache2 -v` / `…/httpd -v`) | the probe can only answer once the package exists, so it *ends* the wave instead of starting one |
| (RedHat) +1 | `fact(LinuxDistribution)` | the role's `distribution_major_version` read: the RHEL 8 gate below |
| (RedHat, RHEL 8+) +1 | `fact(FileExists, path=/etc/pki/tls/certs/localhost.crt)` | the cert `stat`; skipped on RHEL 7 |
| (RedHat, cert missing) +1 | `act(Shell, "/usr/libexec/httpd-ssl-gencerts", sudo=True)` | the `command:` task; a non-zero exit raises `ValueError` before configure is built — and it sits *ahead* of the configure wave, never inside it (D10) |
| 1 … *n* | `fact(FileExists, path=vhost.certificate_file)` — **one wave each** | the role's `stat` loop over `apache_vhosts_ssl`; a host with no SSL vhost parks on nothing here |
| *n*+1 | `configure`: on Ubuntu the ports transform and its chmod, the mod/conf symlinks, the rendered `vhosts.conf` and its symlink, the optional default-vhost removal; on RedHat the ports pair on `httpd.conf`, the `LoadModule` stubs, the `conf.d` vhosts file; then the service state — closed by `checkpoint("configure done")` | the checkpoint **is** the role's `notify`: it reads the wave it just closed and names the hosts whose notifying requests changed |
| *n*+2 | `handler`: `service_handler(notified, (plan.service,), action=…)` — `apache2` or `httpd` | only on the hosts that checkpoint selected |

Three ordering rules are load-bearing, and the layout above is what makes them hold
without any extra machinery:

* **Requests do not close waves.** A host's own configure entries are therefore
  always in the wave its own checkpoint reads — no cross-host leakage of *outcomes*.
  (`notified` is a host-name set that may name machines this body did not act on;
  `service_handler` branches on `host.name` itself, which keeps the wave a host's
  own.)
* **The checkpoint immediately follows `configure`.** A checkpoint placed after the
  handler wave would read the handler's entries instead and notify nobody.
* **The handler's verb comes from the same plan.** `restart_state` maps onto
  `service_handler`'s `restarted`/`reloaded`/`running` verbs, and
  `restarted`/`reloaded` always report changed — faithful to Ansible's service
  module, and why a converged host that *did* change something still shows a
  handler change.

A host whose `host.family` is neither `"Ubuntu"` nor `"RedHat"` returns before its
first `yield`, so it emits no requests, no barriers and no report lines at all.

## 7. Transcript

From `tests/roles/test_apache.py::test_tree_snapshot`, against the simulated Ubuntu
host plus an Alpine host the role never touches. The inventory sets
`listen_port: 8080`, `remove_default_vhost: true`, `mods_disabled: [autoindex]`,
`conf_enabled: [security]` in the `apache` role mapping and one vhost
with an alias, a documentroot and `extra_parameters`. Fresh host, first pass:

```text
(root)
  wave 0
    ubuntu: Update apt cache         [changed: apt update]
        $ sudo apt-get update
    ubuntu: Ensure Apache is installed on Debian [changed: 2 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install apache2 apache2-utils
    barrier: apache -v → {'exit_status': 0, 'stdout': 'Server version: Apache/2.4.58 (Ubuntu)\nServer built:   2026-01-01T00:00:00\n', 'stderr': ''}
  wave 1
    ubuntu: Configure Apache ports (^Listen ) [changed: content /etc/apache2/ports.conf differs, will install]
        $ scp <files install> -> /tmp/reemote-install-…/content
        $ sudo sh -c 'mkdir -p /etc/apache2 && cp … /etc/apache2/ports.conf && chmod 644 /etc/apache2/ports.conf && rm -rf …'
    ubuntu: Set Apache ports.conf mode [no change: file /etc/apache2/ports.conf already exists]
    ubuntu: Enable Apache mod rewrite [changed: /etc/apache2/mods-enabled/rewrite.load missing, will create]
        $ sudo ln -s /etc/apache2/mods-available/rewrite.load /etc/apache2/mods-enabled/rewrite.load
    ubuntu: Enable Apache mod ssl    [changed: /etc/apache2/mods-enabled/ssl.load missing, will create]
        $ sudo ln -s /etc/apache2/mods-available/ssl.load /etc/apache2/mods-enabled/ssl.load
    ubuntu: Disable Apache mod autoindex [changed: /etc/apache2/mods-enabled/autoindex.load present, will remove]
        $ sudo rm -f /etc/apache2/mods-enabled/autoindex.load
    ubuntu: Enable Apache config security [changed: /etc/apache2/conf-enabled/security.conf missing, will create]
        $ sudo ln -s /etc/apache2/conf-available/security.conf /etc/apache2/conf-enabled/security.conf
    ubuntu: Add apache vhosts configuration [changed: content /etc/apache2/sites-available/vhosts.conf is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-…/content
        $ sudo sh -c 'mkdir -p /etc/apache2/sites-available && cp … /etc/apache2/sites-available/vhosts.conf && chown root:root … && chmod 644 … && rm -rf …'
    ubuntu: Add vhost symlink in sites-enabled [changed: /etc/apache2/sites-enabled/vhosts.conf missing, will create]
        $ sudo ln -s /etc/apache2/sites-available/vhosts.conf /etc/apache2/sites-enabled/vhosts.conf
    ubuntu: Remove default vhost in sites-enabled [changed: /etc/apache2/sites-enabled/000-default.conf present, will remove]
        $ sudo rm -f /etc/apache2/sites-enabled/000-default.conf
    ubuntu: Ensure Apache has selected state and enabled on boot [changed: apache2.service not running (None), will start; apache2.service not enabled, will enable]
        $ sudo systemctl start apache2.service
        $ sudo systemctl enable apache2.service
    barrier: configure done → 1 host notified
  wave 2
    ubuntu: Restart apache2 (handler) [changed: apache2.service already running; apache2.service will restart]
        $ sudo systemctl restart apache2.service
```

Only the `apache -v` barrier line prints a real command here: an `act` barrier's
outcome is a `Result` dict, printed verbatim. The two `scp` spool directories are
content-addressed, so the real run shows their hashes where `…` is above; the test
pins them.

The rerun — the probe still runs (it is a read, not a cached plan value), the
checkpoint finds nothing, and **the handler wave disappears entirely**:

```text
(root)
  wave 0
    ubuntu: Update apt cache         [no change: apt update skipped, cache is fresh]
    ubuntu: Ensure Apache is installed on Debian [no change: all packages present]
    barrier: apache -v → {'exit_status': 0, 'stdout': 'Server version: Apache/2.4.58 (Ubuntu)\nServer built:   2026-01-01T00:00:00\n', 'stderr': ''}
  wave 1
    ubuntu: Configure Apache ports (^Listen ) [no change: content already up to date (/etc/apache2/ports.conf)]
    ubuntu: Set Apache ports.conf mode [no change: file /etc/apache2/ports.conf already exists]
    ubuntu: Enable Apache mod rewrite [no change: link /etc/apache2/mods-enabled/rewrite.load already exists]
    ubuntu: Enable Apache mod ssl    [no change: link /etc/apache2/mods-enabled/ssl.load already exists]
    ubuntu: Disable Apache mod autoindex [no change: link /etc/apache2/mods-enabled/autoindex.load does not exist]
    ubuntu: Enable Apache config security [no change: link /etc/apache2/conf-enabled/security.conf already exists]
    ubuntu: Add apache vhosts configuration [no change: content already up to date (/etc/apache2/sites-available/vhosts.conf)]
    ubuntu: Add vhost symlink in sites-enabled [no change: link /etc/apache2/sites-enabled/vhosts.conf already exists]
    ubuntu: Remove default vhost in sites-enabled [no change: link /etc/apache2/sites-enabled/000-default.conf does not exist]
    ubuntu: Ensure Apache has selected state and enabled on boot [no change: apache2.service already running; apache2.service already enabled]
    barrier: configure done → 0 hosts notified
```

With one SSL vhost the run is four waves, wave 1 being the certificate read:

```text
  wave 1
    barrier: FileExists → True
```

The Alpine host appears in no tree: no barrier record, no request line.

The RedHat branch, from `test_redhat_tree_snapshot` against a simulated Rocky 9
host with the same port and mod knobs plus `remove_default_vhost` and
`conf_enabled` (both inert here). Fresh host, first pass — five barrier waves,
the stub-writing configure wave, and the `httpd` handler:

```text
(root)
  wave 0
    rocky: Ensure Apache is installed on RHEL [changed: 4 package(s) to install]
        $ sudo dnf install -y httpd httpd-devel mod_ssl openssh
    barrier: apache -v → {'exit_status': 0, 'stdout': 'Server version: Apache/2.4.62 (Red Hat Enterprise Linux)\nServer built:   2026-01-01T00:00:00\n', 'stderr': ''}
  wave 1
    barrier: LinuxDistribution → {'name': 'Rocky', 'family': 'RedHat', 'major': 9, 'minor': 6, 'release_meta': {}}
  wave 2
    barrier: FileExists → False
  wave 3
    barrier: Ensure httpd certs are installed → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 4
    rocky: Configure Apache ports (^Listen ) [changed: content /etc/httpd/conf/httpd.conf differs, will install]
    rocky: Set Apache httpd.conf mode [no change: file /etc/httpd/conf/httpd.conf already exists]
    rocky: Enable Apache mod rewrite [changed: content /etc/httpd/conf.modules.d/99-ansible-rewrite.conf is absent, will install]
    rocky: Enable Apache mod ssl    [changed: content /etc/httpd/conf.modules.d/99-ansible-ssl.conf is absent, will install]
    rocky: Disable Apache mod autoindex [changed: /etc/httpd/conf.modules.d/99-ansible-autoindex.conf present, will remove]
    rocky: Add apache vhosts configuration [changed: content /etc/httpd/conf.d/vhosts.conf is absent, will install]
    rocky: Ensure Apache has selected state and enabled on boot [changed: httpd.service not running (None), will start; httpd.service not enabled, will enable]
    barrier: configure done → 1 host notified
  wave 5
    rocky: Restart httpd (handler)  [changed: httpd.service already running; httpd.service will restart]
        $ sudo systemctl restart httpd.service
```

(The `scp`/`sh -c` command lines of the spool-installs are elided here; the test
pins them, content-addressed spool hashes included.) On the rerun every request
no-ops and the handler wave is gone; the four read barriers — probe,
distribution, cert stat, gencerts command — still run, since the simulated
command creates no cert, exactly like the role's plain `command:` task with no
`creates:` either.

## 8. Deliberate deviations from the Ansible role


| # | Deviation | Why |
| --- | --- | --- |
| D1 | A host outside the `"Ubuntu"`/`"RedHat"` families emits **nothing at all** | the family guard is the body's opening `if`, not a `when=` on each request. The old example logged every request as `[skipped: when guard …]`; a return emits no records |
| D2 | The version probe is an **`act` barrier**, not a fact | `apache2 -v` is a command, and its answer depends on the install in the same wave. A barrier never raises on a non-zero exit — the `Result` is data — so the body checks `exit_status` itself and raises `ValueError`, the error `main()` maps |
| D3 | Each certificate read is **its own wave**, gathered by an explicit loop | faithful to the role's `stat` loop, and it makes the wave count host-dependent (3 + *n*). The design sketch wrote this as a generator expression, which Python forbids: `yield` inside one is a `SyntaxError`, and so is `list.append(yield …)` |
| D4 | No `mode` on a **symlink** | the role asks for `mode: 0644` on `mods-enabled`/`sites-enabled` links. `files.link` owns no mode, and a Linux symlink's own mode is always `0777` and meaningless — Ansible cannot set it either |
| D5 | `apache_enablerepo` is **Ubuntu-inert, RedHat-live**; `apache_ssl_no_log` is inert everywhere | on RedHat the knob renders as `dnf.packages(enable_repos=…)` — the additive `yum: enablerepo=` spelling, omitted when empty like Ansible's `default(omit)`; reemote has no per-request log redaction, so `ssl_no_log` stays carried and documented rather than silently dropped |
| D6 | `force={{ ansible_check_mode }}` becomes the default `force=False` | in a real run `ansible_check_mode` is false; reemote's `dry` stops before that wave, so check mode has no analogue to reproduce |
| D7 | One handler body, no `by_action` partition and no `compose` | the example had to split the notified set across actions and compose several deploys, because a shared deploy served every host. A per-host body has exactly one `restart_state`, so it yields one request |
| D8 | The role's implicit gates become **pydantic validation** | the three enums are `Literal` fields, `servername` and both certificate paths are required, and a bad value raises `ValidationError` (a `ValueError`) while the inventory is resolved — before any host is contacted |
| D9 | `conf_enabled`/`conf_disabled`/`remove_default_vhost` are **RedHat-inert** | `configure-RedHat.yml` simply has no such tasks — there is no `conf-enabled`/`sites-enabled` pair on that root. The knobs still bind, and a test pins that they emit no RedHat request |
| D10 | The RHEL 8+ gencerts step is **three barriers ahead of configure** | reemote has no command *operation*, so the `command:` task is an `act(Shell, sudo=True)` whose non-zero exit the body raises as `ValueError`; and it must sit before the configure wave: a barrier between configure's requests and its checkpoint would split the wave and silently drop the earlier notifications |
| D11 | `packages_state: latest` **always reports a change on RedHat** | dnf has no upgradable fact yet, so unlike `apt.packages(latest=True)` — which the `AptUpgradable` fact gates on a pending delta — the upgrade is not fact-gated. The default `present` converges; both passes still install |
| D12 | The Amazon Linux branch is **out of scope** | `vars/AmazonLinux.yml` targets Amazon Linux 1 (its `init`-era paths and package quartet); an AL2 host reports family `RedHat` and takes the httpd branch, with `packages` covering any list it needs |

## 9. Scoped out

* **Non-target families.** The role's SLES, Solaris and Amazon Linux 1 branches
  are not migrated: `vars/Debian.yml` and `vars/RedHat.yml` become the two frozen
  constant tables and `include_vars`' OS dispatch disappears into the body's
  `host.family` switch. Alpine and SLES hosts are skipped, not mapped.
* **`httpd` from source, mod packages.** The RedHat branch manages the stock
  distro `httpd` and writes `LoadModule` stubs exactly as the role does; a mod
  that needs a package is a `dnf.packages` request in the caller's own program.
* **Installing modules.** `apache_mods_enabled`/`conf_enabled` manage symlinks only,
  exactly as the role does. A mod that needs a package (`libapache2-mod-php`) is an
  `apt.packages` request in the caller's own program.
* **Managing certificates.** The role only *reads* whether each
  `certificate_file` exists; obtaining one (ACME, a copied bundle) is out of scope.
* **Removing stale vhosts.** Files left in `sites-enabled` that the inventory no
  longer names are untouched — the role has no such task, and reemote has no
  directory-sweep fact pair for it here.
* **Redacting the handler's log.** See D5.
* **Per-host privilege.** Apache needs root and the inventory says how a host
  elevates; there is no play-level `become` to reproduce, and one run has one
  privilege view per host.

--->