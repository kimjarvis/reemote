# nginx — nginx virtual-host role

| | |
| --- | --- |
| Role | [`geerlingguy.nginx`](https://github.com/geerlingguy/ansible-role-nginx) |
| Module | [`reemote.roles.nginx`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/nginx.py) |
| Example | [`examples/roles/nginx.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/nginx.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | `nginx_`-prefixed inventory keys → `NginxVars` |

<!---
| Waves | 4 (3 on a converged rerun — the handler wave disappears) |
| Outputs | `None` — no composing role in the corpus consumes an output |
| Extra deps | **`reemote[templates]`** (jinja2 renders `nginx.conf.j2` + `vhost.j2`) |
| Offline test | [`tests/roles/test_nginx.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_nginx.py) |

`nginx` installs and configures nginx on Debian-family hosts: the `dirmngr`
prerequisite, the optional official nginx PPA (with the reinstall-on-PPA-add
split), the apt cache refresh and the `nginx` package, the `sites-enabled` vhost
files rendered from the role's `vhost.j2`, the optional default-vhost removal, the
legacy `vhosts.conf` cleanup, the rendered `nginx.conf` installed behind `nginx -t`
validation, the service's running/enabled state, and the role's `restart nginx` /
`reload nginx` handlers.

Like [`apache`](apache.md), the role's whole `tasks/main.yml` — the OS dispatch,
the two Ubuntu setup files, the vhost loop, the `nginx.conf` template, the service
task and the handlers — is one per-host generator, and the reads the role took
*between* play steps are barriers inside that generator. nginx is the migration's
clearest **two-checkpoint** case: the PPA gate reads the *setup* report, and both
handler channels read the *configure* report — three `changed_hosts` reads over
two checkpoints, never a third:

```python
yield from setup(hv)(host)                              # wave 0
setup_report = yield checkpoint("setup done")
ppa_added = changed_hosts(setup_report, (PPA_NAME,))    # register + when: … is changed

yield from install(hv, ppa_added)(host)                 # wave 1
cpus = yield fact(hardware.Cpus)                        # the worker_processes default

plan = configure_plan(hv, cpus)                         # render nginx.conf + vhosts
yield from configure(plan)(host)                        # wave 2
configure_report = yield checkpoint("configure done")
restart = changed_hosts(configure_report, restart_notifying_names(plan))
reload = changed_hosts(configure_report, reload_notifying_names(plan))
yield from handler(plan, restart, reload)(host)         # wave 3
```

Nothing is gathered into a module-level snapshot dict first, and no notifying-name
set is unioned across the inventory: the per-host body *is* the plan, and the two
handler name sets are two disjoint slices of the one configure report.

## 1. What it is, and where it came from

Status vocabulary: **covered** (a direct operation), **recipe** (a documented shape
change), **barrier** (a read the body consumes), **dead** (never runs in the role
either).

| Ansible task | reemote request | Status |
| --- | --- | --- |
| `include_vars: {{ ansible_facts.os_family }}.yml`, `when: … == "Debian"` | the frozen `DEBIAN_VARS` constants, one family guard | deviation (D1) |
| `set_fact: nginx_user={{ __nginx_user }}` when undefined | `render_nginx_conf`'s `v.user or "www-data"` | covered |
| `apt: name=dirmngr state=present` | `apt.packages(packages=("dirmngr",))` | covered |
| `apt_repository: ppa:nginx/{{ nginx_ppa_version }}`, `register: nginx_ppa_added` | `apt.ppa(src=…, update_cache=True)` behind `ppa_use` | covered |
| `apt: name=nginx state=absent` when `nginx_ppa_added is changed` | `changed_hosts(setup report, (PPA_NAME,))` → the conditional removal | recipe (D7) |
| `apt: update_cache=yes cache_valid_time=86400`, `changed_when: false` | `apt.update(cache_valid_time=86400)` | covered (D3) |
| `apt: name=nginx state=present default_release=…` | `apt.packages(packages=…, default_release=… or None)` | covered |
| `{{ ansible_facts.processor_vcpus }}` in `nginx.conf.j2` | `cpus = yield fact(hardware.Cpus)` | barrier (D5) |
| `file: state=absent` default vhost, notify restart | `files.file(present=False, force=True)` | deviation (D2) |
| `file: state=directory mode=0755` `sites-enabled`, notify reload | `files.directory(mode="0755")` | covered |
| `template:` vhost loop, `root:root 0644`, notify reload | control-side `render_vhost` → `files.install(content=…)` | recipe (D6) |
| `file: state=absent` vhost loop (`state == 'absent'`), notify reload | `files.file(present=False, force=True)` per filename | covered |
| `file: state=absent` legacy `vhosts.conf`, notify reload | `files.file(present=False, force=True)` | covered |
| `template: nginx.conf.j2`, `validate: nginx -t -c %s`, notify reload | control-side render → `files.install(validate="nginx -t -c {spool}")` | recipe (D6) |
| `service: name=nginx state=… enabled=…` (no notify) | `systemd.service(running=…, restarted=…, reloaded=…, enabled=…)` | covered (D8) |
| `handlers: restart nginx` (notified by the default-vhost removal) | `checkpoint("configure done")` → `changed_hosts` → `service_handler(restarted)` | recipe (D7) |
| `handlers: reload nginx`, `when: nginx_service_state == "started"` | the same checkpoint, a second `changed_hosts` read, a static per-host filter | recipe (D7) |
| `handlers: validate nginx configuration` | never notified by any task | dead (D4) |
| the role's implicit gates (`server_name`/`filename`, the state enum, the upstream shape) | pydantic validation on `NginxVars` / `VHost` / `Upstream` | deviation (D9) |

No library gap blocked this migration, so none was filed: `apt.ppa` already carries
the `apt_repository` + `register` semantics (it reads `AptSources` and reports
changed only on a real add), `files.install`'s `validate` hook is the role's
`nginx -t -c %s`, and `hardware.Cpus` is the `processor_vcpus` fact. Both Ubuntu
opt-in toggles (`nginx_ppa_use`, `nginx_remove_default_vhost`) **are** implemented —
unlike a build-from-source branch, every task they gate maps to a covered operation.

## 2. Install

```bash
pip install "reemote[templates]"     # or: uv add "reemote[templates]"
```

`jinja2` is the only extra. The base library imports neither `jinja2` nor `pyyaml`
— they enter through `reemote/roles/_render.py`, and `nginx` imports that module
*inside* `_template_environment`, so `import reemote.roles` stays clean without the
extra and a missing `jinja2` surfaces as an `ImportError` at render time rather
than at import time. Both templates use only built-in Jinja2 filters (`indent`,
`default`), so the shared `jinja2_environment` (Ansible's whitespace defaults plus
`StrictUndefined`) reproduces the role's output byte for byte.

SSH access to each Ubuntu host with the key named in its `connection`, and
passwordless sudo (or a `privilege` section in the inventory): every request
carries `sudo=True`, because reemote has no play-level `become`. With
`nginx_ppa_use` the host also needs outbound HTTPS to `ppa.launchpad.net`.

## 3. Variables

`NginxVars` is a frozen pydantic model (`extra="forbid"`,
`coerce_numbers_to_str=True`) with unprefixed field names; the prefix passed to
`resolve_vars` binds the inventory keys, so an inventory written for the role needs
no change.

--->

::: reemote.roles.nginx
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - nginx

**Variables:**

| Field | Type | Default | Inventory key |
| --- | --- | --- | --- |
| `yum_repo_enabled` | `bool` | `true` — **inert** (RedHat-only) | `nginx_yum_repo_enabled` |
| `zypper_repo_enabled` | `bool` | `true` — **inert** (Suse-only) | `nginx_zypper_repo_enabled` |
| `default_release` | `str` | `""` → no apt pin | `nginx_default_release` |
| `ppa_use` | `bool` | `false` | `nginx_ppa_use` |
| `ppa_version` | `str` | `stable` | `nginx_ppa_version` |
| `package_name` | `str` | `nginx` | `nginx_package_name` |
| `service_state` | `Literal["started","stopped","restarted","reloaded"]` | `started` | `nginx_service_state` |
| `service_enabled` | `bool` | `true` | `nginx_service_enabled` |
| `conf_template` | `str` | `nginx.conf.j2` | `nginx_conf_template` |
| `vhost_template` | `str` | `vhost.j2` | `nginx_vhost_template` |
| `user` | `str \| None` | `null` → `www-data` | `nginx_user` |
| `worker_processes` | `str \| None` | `null` → `"<cpus>"` (quoted) | `nginx_worker_processes` |
| `worker_connections` | `str` | `1024` | `nginx_worker_connections` |
| `multi_accept` | `str` | `off` | `nginx_multi_accept` |
| `error_log` | `str` | `/var/log/nginx/error.log warn` | `nginx_error_log` |
| `access_log` | `str` | `/var/log/nginx/access.log main buffer=16k flush=2m` | `nginx_access_log` |
| `sendfile` | `str` | `on` | `nginx_sendfile` |
| `tcp_nopush` | `str` | `on` | `nginx_tcp_nopush` |
| `tcp_nodelay` | `str` | `on` | `nginx_tcp_nodelay` |
| `keepalive_timeout` | `str` | `75` | `nginx_keepalive_timeout` |
| `keepalive_requests` | `str` | `600` | `nginx_keepalive_requests` |
| `server_tokens` | `str` | `on` | `nginx_server_tokens` |
| `client_max_body_size` | `str` | `64m` | `nginx_client_max_body_size` |
| `server_names_hash_bucket_size` | `str` | `64` | `nginx_server_names_hash_bucket_size` |
| `proxy_cache_path` | `str` | `""` → directive omitted | `nginx_proxy_cache_path` |
| `extra_conf_options` | `str` | `""` | `nginx_extra_conf_options` |
| `extra_http_options` | `str` | `""` | `nginx_extra_http_options` |
| `log_format` | `str` | the role's `main` format | `nginx_log_format` |
| `remove_default_vhost` | `bool` | `false` | `nginx_remove_default_vhost` |
| `listen_ipv6` | `bool` | `true` | `nginx_listen_ipv6` |
| `vhosts` | `tuple[VHost, …]` | `()` | `nginx_vhosts` |
| `upstreams` | `tuple[Upstream, …]` | `()` | `nginx_upstreams` |

<!---

Precedence is `model defaults < inventory extra < overrides`. Sequences are
`tuple`, never `list`: the model is frozen and hashable, and a value that reaches
`op()` must hash. Values that reach a template verbatim are `str`, with
`coerce_numbers_to_str` so an inventory writing `nginx_keepalive_timeout: 75`
renders `75` exactly as Jinja would render the integer. The two inert knobs are
carried rather than dropped so an existing inventory still binds — a test pins that
setting them changes no request.

Two sentinels keep the role's non-default resolution shape: `user` `None`
reproduces the `set_fact: nginx_user={{ __nginx_user }}` fallback, and
`worker_processes` `None` reproduces the fact-fed default
`"{{ ansible_facts.processor_vcpus }}"` — the `fact(Cpus)` barrier supplies the
count, **quotes included** (`worker_processes  "4";`), because the role's folded
scalar carries literal double quotes and dropping them makes `nginx.conf` drift on
every run. An explicit `worker_processes` override (`"auto"`) renders verbatim.

`conf_template` / `vhost_template` follow the role's own convention: the bundled
`nginx.conf.j2` / `vhost.j2` are inlined in the module, and **any other value is a
controller-side path**, read at render time. That read is ordinary Python inside the
body, not a `control` barrier, so a custom template costs no extra wave. The bundled
templates keep their `{% block %}` tags and a `DictLoader` maps their names, so a
custom template can `{% extends "nginx.conf.j2" %}` exactly as the role README
documents.

### 3.1 Vhost and upstream mapping fields

`nginx_vhosts` items are `VHost`; `nginx_upstreams` items are `Upstream`. Both are
frozen with `extra="allow"`, so any further key the template might learn to read is
kept.

| `VHost` field | Default | Notes |
| --- | --- | --- |
| `server_name` | `null` | **required with `filename`** — the model validator enforces one of the two |
| `filename` | `null` | the install/removal dest; defaults to the first `server_name` token + `.conf` |
| `listen` | `80` | |
| `root`, `error_page`, `access_log`, `error_log`, `extra_parameters`, `server_name_redirect` | `null` | dumped with `exclude_none=True`, so an unset key stays *undefined* for the template's `is defined` branches |
| `index` | `index.html index.htm` | |
| `return` | `null` | a Python keyword, so the field is `return_code` with an alias; `model_dump(by_alias=True)` restores the name the template reads |
| `state` | `present` | deliberately **not** enum-gated — the role only tests `!= 'absent'`, so any other spelling installs, faithfully |
| `template` | `null` | a per-vhost custom template; `null` falls back to `vhost_template` |

| `Upstream` field | Default | Notes |
| --- | --- | --- |
| `name` | — | **required**, non-empty |
| `servers` | — | **required**, a non-empty `tuple[str, …]` |
| `strategy` | `null` | the template's `is defined` branch (`ip_hash`, `least_conn`, …) |
| `keepalive` | `null` | `str \| int`; `null` omits the line |

The file name is computed control-side by `vhost_filename`, so Jinja2's
eager-evaluation trap — `default()` evaluating the `server_name` fallback even when
`filename` is set — never fires.

## 5. Run it

The role is library code; `examples/roles/nginx.py` is the runnable artifact — a
standalone program with its own `main()`, no shared runner. It loads and validates
`inventory.json` from the current working directory, resolves the role's variables
(the defaults overlaid with the entry's `nginx_`-prefixed keys, then the call-site
`overrides`), builds the deploy from the `nginx` factory and drives it with
`run_waves` — a `dry=True` one-wave preview, then the real apply — printing the wave
tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import NginxVars, nginx

NGINX_OVERRIDES = {
    "remove_default_vhost": True,
    "vhosts": ({"server_name": "example.com", "root": "/var/www/example",
                "index": "index.html index.htm"},),
}

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(NginxVars, "nginx_", inventory, overrides=NGINX_OVERRIDES)
    deploy = nginx(vars_set)
    report, _hosts = await run_waves(inventory, deploy, dry=True)   # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)             # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/nginx.py
```

The `ubuntu` entry of the shipped `inventory.json` carries no `nginx_` keys, so the
program's call-site `overrides` decide the site: nginx drops the packaged `default`
vhost and serves one `example.com` vhost out of `/var/www/example` on port `80`.
Overrides use the model's **unprefixed** field names — the `nginx_` prefix only binds
inventory keys — and win over both the defaults and any `nginx_`-prefixed inventory
key. The `dry=True` pass is a one-wave preview: it plans the setup requests
(`dirmngr`, plus the PPA when `ppa_use` is set), parks on the
`checkpoint("setup done")` barrier and stops (`barrier: setup done → unknown until
run`), gathering nothing and applying nothing. A converged rerun is the real preview.

`main()` prints the wave tree and maps the role's failure modes (bad inventory,
`ValueError` from a rejected variable or an undeterminable CPU count, `CommandError`
— including a rejected `nginx -t` validation, `HostConnectionError`, `PrivilegeError`,
`OSError`, `TypeError`) to a one-line stderr message and exit 1.

**Run the program twice.** The second run must report no change and queue no handler:
`dirmngr` and `nginx` are installed, the PPA already exists, the cache stamp is fresh,
every vhost file is byte-identical, `nginx.conf` matches, and the unit is running and
enabled. That is the migration's idempotence claim, and the offline suite asserts it.

> **Shared demo host.** The `ubuntu` demo host also runs the Apache example. This
> role leaves nginx listening on `80` and enabled, which collides with Apache's
> default port; the Apache example moves to `8080` to avoid it. To stop and disable
> nginx again after a real run:
>
> ```bash
> ssh <user>@<ubuntu-host> 'sudo systemctl disable --now nginx'
> ```

## 6. What it does

One deploy. Each barrier closes its own wave, and a wave with no records is not
appended — so the wave count is **host-dependent**, which is the point of the barrier
model:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `setup`: `apt.packages(("dirmngr",))`, plus `apt.ppa(…)` when `ppa_use` — closed by `checkpoint("setup done")` | the checkpoint **is** the role's `register: nginx_ppa_added`: it reads the wave it just closed and names the hosts whose PPA request changed |
| 1 | `install`: the conditional nginx removal (only on a host the checkpoint named), `apt.update(cache_valid_time=86400)`, `apt.packages((nginx,))` — closed by `fact(hardware.Cpus)` | the count can only be read once the package exists, so the fact *ends* the wave instead of starting one |
| 2 | `configure`: the default-vhost removal, the `sites-enabled` directory, the vhost installs and removals, the legacy cleanup, the validated `nginx.conf`, the service state — closed by `checkpoint("configure done")` | the checkpoint reads the wave it just closed; **both** handler channels draw from this one report |
| 3 | `handler`: `service_handler(restart_notified, ("nginx",), action="restarted")` then `…reloaded` | only on the hosts each channel selected, in handler-definition order |

Three ordering rules are load-bearing, and the layout above is what makes them hold
without any extra machinery:

* **Three `changed_hosts` reads over two checkpoints (§18.3).** The PPA gate reads the
  *setup* report; restart and reload both read the *configure* report, as two disjoint
  name sets (`restart_notifying_names` / `reload_notifying_names`). A third checkpoint
  is deliberately absent — the second one is read twice. So the barrier sequence is
  exactly `[CheckpointRequest, FactRequest, CheckpointRequest]`.
* **Requests do not close waves.** A host's own configure entries are therefore always
  in the wave its own checkpoint reads — no cross-host leakage of *outcomes*.
* **The handler's verb comes from the same plan.** `restart` fires only where the
  default-vhost removal changed; `reload` fires where any config file changed, and is
  narrowed to nothing when `service_state != "started"` (the role's static `when`).
  `restarted`/`reloaded` always report changed — faithful to Ansible's service module.

A host whose `host.family` is not `"Ubuntu"` returns before its first `yield`, so it
emits no requests, no barriers and no report lines at all.

## 7. Transcript

From `tests/roles/test_nginx.py::test_tree_snapshot`, against the simulated Ubuntu
host plus a RedHat host the role never touches. The inventory sets
`nginx_ppa_use: true`, `nginx_remove_default_vhost: true`, one present vhost with a
redirect and `extra_parameters`, one absent vhost, an `ip_hash` upstream and the two
extra-option blocks. Fresh host, first pass:

```text
(root)
  wave 0
    ubuntu: Ensure dirmngr is installed [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install dirmngr
    ubuntu: Add PPA for Nginx        [changed: ppa ppa:nginx/stable will be added; apt update]
        $ sudo apt-add-repository -y ppa:nginx/stable
        $ sudo apt-get update
    barrier: setup done → 1 host notified
  wave 1
    ubuntu: Ensure nginx will reinstall if the PPA was just added [no change: no packages to remove]
    ubuntu: Update apt cache         [no change: apt update skipped, cache is fresh]
    ubuntu: Ensure nginx is installed [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install nginx
    barrier: Cpus → 4
  wave 2
    ubuntu: Remove default nginx vhost config file [changed: /etc/nginx/sites-enabled/default is not a file, will remove first; file /etc/nginx/sites-enabled/default does not exist]
        $ sudo rm -rf /etc/nginx/sites-enabled/default
    ubuntu: Ensure nginx_vhost_path exists [no change: directory /etc/nginx/sites-enabled already exists]
    ubuntu: Add managed vhost config file example.com.conf [changed: content /etc/nginx/sites-enabled/example.com.conf is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-acd100c770d86a8c/content
        $ sudo sh -c 'mkdir -p /etc/nginx/sites-enabled && cp /tmp/reemote-install-acd100c770d86a8c/content /etc/nginx/sites-enabled/example.com.conf && chown root:root /etc/nginx/sites-enabled/example.com.conf && chmod 644 /etc/nginx/sites-enabled/example.com.conf && rm -rf /tmp/reemote-install-acd100c770d86a8c'
    ubuntu: Remove managed vhost config file retired.example.com.conf [changed: /etc/nginx/sites-enabled/retired.example.com.conf present, will remove]
        $ sudo rm -f /etc/nginx/sites-enabled/retired.example.com.conf
    ubuntu: Remove legacy vhosts.conf file [changed: /etc/nginx/sites-enabled/vhosts.conf present, will remove]
        $ sudo rm -f /etc/nginx/sites-enabled/vhosts.conf
    ubuntu: Copy nginx configuration in place [changed: content /etc/nginx/nginx.conf differs, will install]
        $ scp <files install> -> /tmp/reemote-install-8df6515b1e83040a/content
        $ sudo nginx -t -c /tmp/reemote-install-8df6515b1e83040a/content
        $ sudo sh -c 'mkdir -p /etc/nginx && cp /tmp/reemote-install-8df6515b1e83040a/content /etc/nginx/nginx.conf && chown root:root /etc/nginx/nginx.conf && chmod 644 /etc/nginx/nginx.conf && rm -rf /tmp/reemote-install-8df6515b1e83040a'
    ubuntu: Ensure nginx service is running as configured [changed: nginx.service not running (None), will start; nginx.service not enabled, will enable]
        $ sudo systemctl start nginx.service
        $ sudo systemctl enable nginx.service
    barrier: configure done → 1 host notified
  wave 3
    ubuntu: Restart nginx (handler)  [changed: nginx.service already running; nginx.service will restart]
        $ sudo systemctl restart nginx.service
    ubuntu: Reload nginx (handler)   [changed: nginx.service already running; nginx.service will reload]
        $ sudo systemctl reload nginx.service
```

The two `scp` spool directories are content-addressed (`8df6515b1e83040a` for
`nginx.conf`, `acd100c770d86a8c` for the vhost), and the `sudo nginx -t -c …` line is
the install's `validate` hook running against the spool *before* the elevated `cp`
lands it — a rejected config never reaches `/etc/nginx/nginx.conf`. The default vhost
is a symlink, so its removal is `rm -rf` on the link (D2).

The rerun — the CPU fact still runs (it is a read, not a cached plan value), both
checkpoints find nothing, and **the handler wave disappears entirely** (three waves,
not four):

```text
(root)
  wave 0
    ubuntu: Ensure dirmngr is installed [no change: all packages present]
    ubuntu: Add PPA for Nginx        [no change: ppa ppa:nginx/stable exists]
    barrier: setup done → 0 hosts notified
  wave 1
    ubuntu: Update apt cache         [no change: apt update skipped, cache is fresh]
    ubuntu: Ensure nginx is installed [no change: all packages present]
    barrier: Cpus → 4
  wave 2
    ubuntu: Remove default nginx vhost config file [no change: file /etc/nginx/sites-enabled/default does not exist]
    ubuntu: Ensure nginx_vhost_path exists [no change: directory /etc/nginx/sites-enabled already exists]
    ubuntu: Add managed vhost config file example.com.conf [no change: content already up to date (/etc/nginx/sites-enabled/example.com.conf)]
    ubuntu: Remove managed vhost config file retired.example.com.conf [no change: file /etc/nginx/sites-enabled/retired.example.com.conf does not exist]
    ubuntu: Remove legacy vhosts.conf file [no change: file /etc/nginx/sites-enabled/vhosts.conf does not exist]
    ubuntu: Copy nginx configuration in place [no change: content already up to date (/etc/nginx/nginx.conf)]
    ubuntu: Ensure nginx service is running as configured [no change: nginx.service already running; nginx.service already enabled]
    barrier: configure done → 0 hosts notified
```

The reinstall-removal request is gone too: the setup checkpoint named nobody, so
`install` never emitted it. The RedHat host appears in no tree: no barrier record, no
request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | A host outside the `"Ubuntu"` family emits **nothing at all** | the family guard is the body's opening `if`, not a `when=` on each request. The old example logged every request as `[skipped: when guard …]`; a return emits no records. `include_vars`' OS dispatch collapses to the frozen `DEBIAN_VARS` |
| D2 | `file: state=absent` becomes `files.file(present=False, force=True)` | reemote's `File` fact reads a symlink as "exists and is not a file", and the default `force=False` raises on it — Ubuntu's default vhost **is** a symlink. `force=True` reproduces Ansible's type-agnostic removal (`rm -rf` on a symlink removes only the link, never its target) |
| D3 | The cache task's `changed_when: false` has no analogue | reemote's log reports the real fact-resolved delta. Moot: the task notifies nothing, so its `changed` flag has no downstream effect (its name is in no notifying set) |
| D4 | The dead `validate nginx configuration` handler stays dead | it is never notified by any task in the role, so Ansible never runs it. The *live* validation is the `nginx.conf` install's `validate: nginx -t -c {spool}` hook |
| D5 | The fact-fed `worker_processes` default is a **`fact(Cpus)` barrier**, quoted | `ansible_facts.processor_vcpus` becomes `cpus = yield fact(hardware.Cpus)`, which closes the install wave so the count is read after the package applied. `None → f'"{cpus}"'` keeps the role's folded-scalar double quotes; drop them and `nginx.conf` drifts every run. An undeterminable count raises `ValueError` before the configure wave is built |
| D6 | `template:` becomes a **control-side render**; `files.install` takes the rendered `str` | reemote has no controller filesystem paths inside a deploy. The bundled templates keep their `{% block %}` tags and a `DictLoader` maps their names, so a custom template can `{% extends "vhost.j2" %}`; a custom path is read at render time, not through a `control` barrier |
| D7 | **Three `changed_hosts` reads over two checkpoints**; one handler body, no `by_action` partition | the PPA gate reads the setup report, restart and reload both read the configure report as two disjoint name sets — a third checkpoint is deliberately absent. A per-host body has exactly one `service_state`, so each channel yields at most one request. `service_handler` keeps `running=True`: a restart **starts a stopped unit**, like Ansible's `service: state=restarted` |
| D8 | Stateless service verbs | `service_state: restarted/reloaded` (and the handlers' verbs) always report changed when reached, faithful to Ansible's `service` module; the default `started` is fact-gated and converges. The reload handler's `when: nginx_service_state == "started"` is a static per-host filter that narrows the notified set before the handler is built |
| D9 | The role's implicit gates become **pydantic validation**; the RedHat/Suse knobs are **carried and inert** | `service_state` is a `Literal`, a vhost needs `server_name` or `filename`, an upstream needs `name` and a non-empty `servers` list, and a bad value raises `ValidationError` (a `ValueError`) while the inventory is resolved — before any host is contacted. `yum_repo_enabled` / `zypper_repo_enabled` belong to out-of-scope branches, documented rather than silently dropped so such a key still binds |

## 9. Scoped out

* **Non-Ubuntu families.** The role's RedHat/Rocky/AlmaLinux, Suse, Archlinux,
  FreeBSD and OpenBSD branches (`setup-RedHat.yml` and siblings, with their repo/key
  tasks) are not migrated: `vars/Debian.yml` becomes frozen constants and
  `include_vars`' OS dispatch disappears. A RedHat host is skipped, not mapped to a
  different package manager — that is a different role.
* **The `nginx_yum_repo_enabled` / `nginx_zypper_repo_enabled` knobs.** RedHat/Suse
  only; carried inert so an existing inventory binds (D9).
* **The dead `validate nginx configuration` handler.** Never notified in the role
  itself (D4).
* **Obtaining certificates.** nginx serves TLS from files the inventory's
  `extra_parameters` reference; the role neither requests nor renews a certificate,
  and neither does this migration.
* **Removing stale vhosts.** Files left in `sites-enabled` that the inventory no
  longer names are untouched — the role removes only the vhosts it manages plus the
  one legacy `vhosts.conf`, and reemote has no directory-sweep fact pair for more.
* **Per-host privilege.** nginx needs root and the inventory says how a host
  elevates; there is no play-level `become` to reproduce, and one run has one
  privilege view per host.

--->
