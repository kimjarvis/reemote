# varnish — Varnish Cache role

| | |
| --- | --- |
| Role | [`geerlingguy.varnish`](https://github.com/geerlingguy/ansible-role-varnish) |
| Module | [`reemote.roles.varnish`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/varnish.py) |
| Runner | [`examples/roles/varnish.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/varnish.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `varnish` role mapping → `VarnishVars` |

<!---
| Waves | `3` on a changed host (0–2); `2` once converged — the handler wave disappears |
| Outputs | none — the deploy returns `None`; no other role reads varnish's results |
| Extra deps | **`reemote[templates]`** — three Jinja2 templates render through `reemote.roles._render` |
| Offline test | [`tests/roles/test_varnish.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_varnish.py) |
| Factory | plain (non-`async`) — the runner calls `varnish(v)`; the release read is a `fact` barrier and the signing key is fetched by `apt.sources_file` itself |

`varnish` installs and configures Varnish Cache on Ubuntu hosts from
packagecloud: the repository dependencies, the vendor key and deb822 repository,
the `varnish` package plus an optional VMOD package, the `/etc/systemd/system`
directory, the rendered sysvinit fragment `/etc/default/varnish`, the
`varnish.service` unit override, the optional default VCL, the admin secret, the
started/enabled services, and upstream's `reload systemd` + `restart varnish`
handlers, folded into one restart request (D4).

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with `yield from scoped("varnish", varnish(v))(host)`
and a run is one `run_waves` pass. The Ansible role's whole task list is that
single deploy, and its `defaults/main.yml` is `VarnishVars`.

This is the corpus's **one-checkpoint template role** — the shape the docker role
does *not* have, because upstream flushes its handlers nowhere mid-play: both of
them run at the end of the play, so one report is the faithful notification read.

```python
info = yield fact(LinuxDistribution)              # wave 0 — codename + URL segment
plan = varnish_plan(hv, info)                     # four renders, frozen
yield from setup(plan)(host)                      # wave 1 — deps, repo, packages
yield from configure(plan)(host)                  #          unit dir + four files
yield from services(plan)(host)                   #          the enabled-services loop
configure_report = yield checkpoint("configure done")
notified = changed_hosts(configure_report, plan.notify_names)
yield from restart_handler(notified, "varnish", daemon_reload=True)(host)  # wave 2
```

## 1. What it is, and where it came from

The role's tasks map onto reemote operations and barriers:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `include_vars: "{{ ansible_facts.os_family }}.yml"` | frozen `vars/Debian.yml` constants (`SYSVINIT_CONFIG_PATH`, `SYSTEMD_UNIT_DIR`) | recipe |
| `set_fact: varnish_packagecloud_repo` | pure `packagecloud_repo()` — `6.6` → `varnish66` | covered |
| `include_tasks setup-RedHat.yml` / `setup-Debian.yml` | the Ubuntu path only; the RedHat branch is scoped out | out of scope |
| `apt: name=[apt-transport-https, gnupg2]` | `apt.packages(packages=DEPENDENCY_PACKAGES)` | covered |
| `get_url: …/gpgkey → /etc/apt/trusted.gpg.d/…asc` + `apt_repository:` | one `apt.sources_file(signed_by=<URL>)` — fetches control-side, installs `/etc/apt/keyrings/varnishcache_<repo>.asc` | deviation (D1) |
| `apt_repository:` implicit `apt-get update` | `update_cache=True` chains the refresh on a real change | deviation (D2) |
| `apt: name={{ varnish_package_name }}` / `{{ varnish_modules_package_name }}` | two `apt.packages` requests, the VMOD one gated | covered |
| `file: path={{ varnish_systemd_override_path }} state=directory` | `files.directory(SYSTEMD_UNIT_DIR, mode="0755")` | covered |
| `template: varnish.j2 → /etc/default/varnish` (`notify`) | `render_sysvinit` → `files.install` + a `files.file` metadata pair | covered (D6) |
| `template: varnish.service.j2 → /etc/systemd/system/varnish.service` (`notify` ×2) | `render_unit` → `files.install` + pair; this override is what the handler's folded reload fires for | deviation (D4) |
| `template: varnish.params.j2` (xenial/RHEL 7 only) | **absent from the stream** — its `when:` excludes modern Ubuntu | out of scope |
| `template: {{ varnish_default_vcl_template_path }}` (`when: varnish_use_default_vcl`) | `render_default_vcl` (bundled text, or a controller-side path read) → `files.install` + pair | covered |
| `template: secret.j2` | `render_secret` — the value plus a newline, plain Python | covered |
| two `service:` loops over `varnish_enabled_services` (`when:` contradicts on Debian) | one `systemd.service(running=True, enabled=True)` per unit | deviation (D5) |
| `handlers/main.yml`: `reload systemd` + `restart varnish` | `checkpoint("configure done")` → one `changed_hosts` read → `restart_handler(daemon_reload=True)`, the reload inside the restart | barrier |

No library gap blocks this migration, and nothing here waits on one. The
prerequisites landed before it started: issue #17 (`apt.sources_file`'s URL
`signed_by` form) and issue #14 (the refresh chaining onto removals), plus both
halves of the reload — issue #50 made `systemd.service(daemon_reload=True)`
state-gated, and its follow-up issue #75 taught that gate to see an override a
unit had never loaded, which the live run turned up. The role therefore folds
upstream's `reload systemd` handler into its restart request instead of working
around the gate.

## 2. Install

```bash
pip install "reemote[templates]"
```

The role renders three upstream templates verbatim, so it imports Jinja2 only
through `reemote.roles._render` — inside the render function, never at module
scope, so `import reemote.roles` stays clean without the extra. SSH access with
the key named in each `connection`, and passwordless sudo (or a `privilege`
section): every request carries `sudo=True`.

## 3. Variables

`VarnishVars` is a frozen pydantic model (`extra="forbid"`) whose fields carry
bare names; the role name passed to `resolve_vars` binds the entry's `varnish`
role mapping, so an inventory written for the role needs no key gymnastics. The
three derived fields (`packagecloud_repo`, `apt_repo_url`, `apt_gpg_key`) default
to `None` — "compute it from `version` and the read distribution", exactly as the
role's own `set_fact` and `defaults/main.yml` templates them. Precedence is
`model defaults < inventory role mapping < overrides`.

--->

::: reemote.roles.varnish
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - varnish

**Variables:**

### Package and repository

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `package_name` | `str` | `varnish` | `varnish_package_name` |
| `modules_package_name` | `str` | `""` — empty skips the VMOD install | `varnish_modules_package_name` |
| `version` | `str` | `6.6` — derives the repo name **and** drives the templates' `version` gates | `varnish_version` |
| `apt_use_packagecloud` | `bool` | `true` — when false, neither deps nor repo are managed | `varnish_apt_use_packagecloud` |
| `packagecloud_repo` | `str \| None` | `null` → `varnish{version without dots}` | `varnish_packagecloud_repo` |
| `apt_repo_url` | `str \| None` | `null` → `https://packagecloud.io/varnishcache/<repo>/<distribution>` | `varnish_apt_repo` |
| `apt_gpg_key` | `str \| None` | `null` → `https://packagecloud.io/varnishcache/<repo>/gpgkey` — **no** distribution segment | `varnish_apt_gpg_key_url` |

### Listening and administration

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `listen_address` | `str` | `""` — every interface | `varnish_listen_address` |
| `listen_port` | `int` | `80` — the front end; `6081` on a host with a web server | `varnish_listen_port` |
| `admin_listen_host` | `str` | `127.0.0.1` | `varnish_admin_listen_host` |
| `admin_listen_port` | `int` | `6082` | `varnish_admin_listen_port` |
| `secret` | `str` | a fixed UUID — the `varnishadm` `-S` secret | `varnish_secret` |

### VCL and backends

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `use_default_vcl` | `bool` | `true` — when false, no `default.vcl` is written **or** notified | `varnish_use_default_vcl` |
| `default_vcl_template_path` | `str` | `default.vcl.j2` (bundled); anything else is a **controller-side** path | `varnish_default_vcl_template_path` |
| `default_backend_host` / `default_backend_port` | `str` / `int` | `127.0.0.1` / `8080` | `varnish_default_backend_*` |
| `backends` | `dict[str, VarnishBackend]` | `{}` — extra `backend <name> { … }` blocks | `varnish_backends` |
| `vhosts` | `dict[str, VarnishVHost]` | `{}` — name-based `req.http.host` chain in `sub vcl_recv` | `varnish_vhosts` |

### Paths and daemon tuning

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `config_path` | `str` | `/etc/varnish` — absolute, no trailing slash; holds `default.vcl` and `secret` | `varnish_config_path` |
| `limit_nofile` | `int` | `131072` — the unit's `LimitNOFILE` and `NFILES` | `varnish_limit_nofile` |
| `storage` | `str` | `file,/var/lib/varnish/varnish_storage.bin,256M` | `varnish_storage` |
| `pidfile` | `str` | `/run/varnishd.pid` — empty drops `PIDFile=` and `-P` | `varnish_pid_file` |
| `varnishd_extra_options` | `str` | `""` — appended to `ExecStart`/`DAEMON_OPTS` | `varnishd_extra_options` |

### Services

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `enabled_services` | `tuple[str, …]` | `("varnish",)` — each one started **and** enabled (D5) | `varnish_enabled_services` |

<!---

Ports accept the `"80"` spelling upstream declares them with — a `before`
validator coerces a digit string, then the `1–65535` range check applies.
`backends` and `vhosts` are the model's only mappings, and they never reach
`op()` unrendered: `render_default_vcl` turns them into a `str` before the plan
freezes, so the hashability rule is untouched. Validation is fail-fast:
`config_path` must be absolute without a trailing slash (two managed paths
concatenate onto it), `limit_nofile` positive, the names and the secret
non-empty, every mapping key and backend host non-empty, and a list normalizes
to a `tuple`.

## 5. Run it

The role is library code; `examples/roles/varnish.py` is the runnable artifact —
a standalone program with its own `main()`, no shared runner. It loads and
validates `inventory.json` from the current working directory, resolves the
role's variables (the defaults overlaid with the entry's `varnish` role mapping,
then the call-site `overrides`), builds the deploy from the `varnish` factory and
drives it with `run_waves` — a `dry=True` one-wave preview, then the real apply —
printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import VarnishVars, varnish

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(
        VarnishVars, "varnish", inventory,
        overrides={"version": "7.6", "listen_port": 6081, "vhosts": {...}},
    )
    deploy = varnish(vars_set)                                       # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)     # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)               # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/varnish.py
```

The call-site `VARNISH_OVERRIDES` pick `version="7.6"` and `listen_port=6081` for
practical reasons rather than effect: the vendor publishes `varnish66` (the role's
default) for `focal` only, so a current Ubuntu release needs a version its
packagecloud repository actually carries, and `80` is taken on any host already
running a web server. The `backends`/`vhosts` entries turn on the optional
`default.vcl` loops, so the demonstration covers the whole four-file configure
stage and the handler it notifies.

The `dry=True` pass is a one-wave preview: varnish's first item is the
`LinuxDistribution` `fact` barrier, and without the codename neither the
repository URL nor its `Suites` can be built, so the run parks there
(`barrier: LinuxDistribution → unknown until run`) and applies nothing.

**Run the program twice.** The second run must report no change and fire no
handler: the repo and keyring are in place, the package is installed, all four
rendered files are byte-identical with their `root:root 0644` metadata, and the
unit is running and enabled — so the one checkpoint notifies nobody and the
handler wave is empty. That is the migration's idempotence claim, and the offline
suite asserts it.

## 6. What it does

One deploy. Each barrier closes its own wave, and a wave with no records is not
appended — so the shape below is what the test pins:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `fact(LinuxDistribution)` | `release_meta.VERSION_CODENAME` feeds the repository's `Suites`, `name` its URL segment; the whole request wave is a pure function of the frozen plan |
| 1 | `setup` (dependencies, the deb822 repo with its URL-signed keyring, the package, the VMOD package) **then** `configure` (the unit directory, then the sysvinit fragment, unit override, VCL and secret, each an install plus a metadata pair) **then** `services` (one started/enabled request per unit) — closed by `checkpoint("configure done")` | no read sits between the three stages, so they share one wave in yield order — which is what lets the repository precede the package install inside a single wave |
| 2 | `Restart varnish (handler)` (where any of the eight names changed), with `daemon_reload=True` so a `systemctl daemon-reload` precedes the restart while the loaded fragment is stale or outranked by the override on disk | the role's two handlers as one request, in `handlers/main.yml`'s order; an unnotified host yields nothing, so the wave disappears once converged |

Three rules are load-bearing:

* **One checkpoint, because upstream flushed nowhere.** Ansible ran both handlers
  at the end of the play, so the notification set is a single
  `changed_hosts(configure_report, plan.notify_names)` read. The docker role
  needs two reports unioned only because of its mid-play `flush_handlers`.
* **The eight notifying names.** `tasks/main.yml` marks exactly the four
  `template` tasks with `notify:`; each one becomes a name here, plus the name of
  the metadata companion paired with it (D6). The directory, the packages, the
  repository and the service loop carry no `notify:` upstream, so their names stay
  out of the set — a test rigs a package-only change to prove the restart does
  **not** fire for it.
* **The reload rides inside the restart.** Upstream notifies `reload systemd`
  from the unit template task alone and `restart varnish` from all four, so a host
  that reloads is always a host that restarts — the two handlers collapse into one
  `restart_handler(..., daemon_reload=True)` driven by a single
  `changed_hosts(configure_report, plan.notify_names)` read. The keyword is
  state-gated (issues #50 and #75): systemd is asked whether the unit's loaded
  fragment is stale *or* outranked by a fragment on disk, so the `daemon-reload`
  lands for the freshly written `/etc/systemd/system/varnish.service`, does not
  land for a change to a non-unit file (the secret, say), and lands nowhere on a
  converged host. Before #75 the gate saw only staleness — a real systemd answered
  `no` for an override it had never loaded, and the folded form restarted the unit
  from the packaged fragment — so the role carried the reload as its own
  unconditional `systemd.daemon_reload` request; that workaround is gone.

A host whose `family` is not `"Ubuntu"` returns before its first `yield`: no
requests, no barriers, no report lines — Alpine and CentOS entries in a shared
inventory are never contacted.

## 7. Transcript

From `tests/roles/test_varnish.py`, against the simulated Ubuntu 24.04 host
(`version: "7.6"`, `listen_port: 6081`, one backend and one vhost in the `varnish`
role mapping) plus a RedHat host the role never touches. The key fetch is patched
offline. Fresh host, first pass:

```text
(root)
  wave 0
    barrier: LinuxDistribution → {'name': 'Ubuntu', 'family': 'Debian', 'major': '24', ...}
  wave 1
    ubuntu: Ensure dependencies are present [changed: 2 package(s) to install]
    ubuntu: Add packagecloud.io Varnish apt repository [changed: signing key from https://packagecloud.io/varnishcache/varnish76/gpgkey will be installed at "/etc/apt/keyrings/varnishcache_varnish76.asc"; apt sources file "/etc/apt/sources.list.d/varnishcache_varnish76.sources" missing or outdated, will be written; apt update]
    ubuntu: Ensure Varnish is installed [changed: 1 package(s) to install]
    ubuntu: Ensure Varnish config path exists [changed: /etc/systemd/system missing, will create]
    ubuntu: Copy Varnish configuration (sysvinit) [changed: content /etc/default/varnish is absent, will install]
    ubuntu: Set Varnish configuration attributes (sysvinit) [no change: file /etc/default/varnish already exists]
    ubuntu: Copy override for systemd unit [changed: content /etc/systemd/system/varnish.service is absent, will install]
    ubuntu: Set Varnish systemd unit attributes [no change: file /etc/systemd/system/varnish.service already exists]
    ubuntu: Copy Varnish default VCL [changed: content /etc/varnish/default.vcl is absent, will install]
    ubuntu: Set Varnish default VCL attributes [no change: file /etc/varnish/default.vcl already exists]
    ubuntu: Copy varnish secret      [changed: content /etc/varnish/secret is absent, will install]
    ubuntu: Set varnish secret attributes [no change: file /etc/varnish/secret already exists]
    ubuntu: Ensure varnish is started and enabled at boot [changed: varnish.service not running (None), will start; varnish.service not enabled, will enable]
    barrier: configure done → 1 host notified
  wave 2
    ubuntu: Restart varnish (handler) [changed: daemon-reload; varnish.service already running; varnish.service will restart]
        $ sudo systemctl daemon-reload
        $ sudo systemctl restart varnish.service
```

The rerun — every barrier re-runs (they are reads, not cached plan values), the
checkpoint finds nothing, and the **handler wave disappears entirely**:

```text
(root)
  wave 0
    barrier: LinuxDistribution → {...}
  wave 1
    ubuntu: Ensure dependencies are present [no change: all packages present]
    ubuntu: Add packagecloud.io Varnish apt repository [no change: apt sources file "…" is already configured]
    ...
    ubuntu: Ensure varnish is started and enabled at boot [no change: varnish.service already running; varnish.service already enabled]
    barrier: configure done → 0 hosts notified
```

The RedHat host appears in neither tree: no barrier record, no request line.

The live two-pass run against a real Ubuntu 24.04 host (192.168.0.181, Varnish
`7.6`, `listen_port: 6081`) reproduced the shape: three waves, one
`Restart varnish (handler)` line in the last one carrying both the reload and the
restart, and a rerun of thirteen no-change requests with no handler wave. Two
details are worth recording. `default.vcl` *differed* rather than being absent,
because the vendor package ships its own file, and `apt-transport-https` was
already installed.

The reload is what makes the override real, and that is the part issue #75 fixed.
An earlier pass on that host — reload folded in, gate still staleness-only — wrote
`/etc/systemd/system/varnish.service`, asked systemd whether the unit was
reload-pending, heard `no`, and restarted the unit from
`/usr/lib/systemd/system/varnish.service`: `systemctl show -p FragmentPath` still
named the packaged file. Now that the gate also weighs the unit against the load
roots, that same single request reloads first, so `FragmentPath` becomes the
override and `ExecStart` is the rendered `-a :6081 -T 127.0.0.1:6082
-P /run/varnishd.pid -f /etc/varnish/default.vcl -S /etc/varnish/secret
-s file,...,256M`, which `varnishadm status` confirms as `Child in state running`.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | `get_url` into `/etc/apt/trusted.gpg.d` + `apt_repository` become **one** `apt.sources_file(signed_by=<URL>)` | the key lands as `/etc/apt/keyrings/varnishcache_<repo>.asc` referenced by a deb822 `Signed-By:`; `trusted.gpg.d` is the mechanism upstream's own comment calls deprecated, and the deb822 form is what this migration was scoped with |
| D2 | The index refresh rides `update_cache=True` | it chains onto a *real* sources change (write or removal, issue #14) instead of `apt_repository`'s unconditional `apt-get update` |
| D3 | The templates' `is version(…)` test is **registered** on the render environment | plain Jinja2 ships no `version` test; `VERSION_COMPARATORS` reproduces Ansible's numeric comparison, the `zabbix_agent` precedent — a template detail, not a library gap |
| D4 | The role's two handlers are **one** request, `restart_handler(notified, SERVICE, daemon_reload=True)` | upstream notifies `reload systemd` from the unit task alone and `restart varnish` from all four, so any host that reloads also restarts; the keyword's `SystemdNeedDaemonReload` gate (issues #50 and #75) reloads exactly while the loaded fragment is stale or outranked by the override on disk, where an unconditional `systemd.daemon_reload` — which this role carried until #75 landed — reloaded on every notifying change, a secret-only one included |
| D5 | `varnish_enabled_services` is **honored** | both upstream tasks gate on `ansible_facts.os_family != 'Debian'`, a contradiction for every Debian-family host, so as written the loop is dead code on Ubuntu; the migration implements what the variable says it means |
| D6 | Each rendered file gets a paired `files.file` metadata companion | `files.install` is idempotent on *content only*, while Ansible's `template` reports owner/group/mode drift too; the companion converges those bits and carries its own notifying name |
| D7 | The family guard is the **body's opening `if`**; variable gates become **pydantic validation** | off-target hosts emit nothing at all (no skipped-request log); bad values — ports out of range, an empty name, a relative `config_path` — all raise `ValidationError` before any host is contacted |
| — | `set_fact` and `include_vars` **freeze** | the repo name becomes `packagecloud_repo()`, the two OS-dispatch files become `SYSVINIT_CONFIG_PATH` / `SYSTEMD_UNIT_DIR`, and the rendered texts become module constants proven byte-identical to `templates/*.j2` |

## 9. Scoped out

* **Non-Ubuntu families.** The role's RedHat branch (`vars/RedHat.yml`,
  `setup-RedHat.yml`, the `varnish.params` template for RHEL 7, the yum
  `varnish_repo_baseurl`/`packagecloud_repo_yum_priority` variables) is not
  migrated and its variables are deliberately absent from `VarnishVars`.
* **The xenial arms.** `varnish.params.j2` and the `use: service` loop are gated
  on Ubuntu 16.04, out of a modern-Ubuntu migration; both drop out of the stream
  rather than being emulated.
* **`varnish_default_vcl_template_path` as a *remote* template.** Upstream resolves
  the name against the role's `templates/` directory; here the bundled text is
  inlined and any other value is read **controller-side**, so a host-side template
  directory is not consulted.
* **VCL beyond the bundled default.** The role ships one VCL; a real deployment
  brings its own. Set `use_default_vcl=False` and manage the VCL with your own
  `files.install` requests, or point `default_vcl_template_path` at a local file.

--->
