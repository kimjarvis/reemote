# supervisor — Supervisor process-control role

| | |
| --- | --- |
| Role | [`geerlingguy.supervisor`](https://github.com/geerlingguy/ansible-role-supervisor) |
| Module | [`reemote.roles.supervisor`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/supervisor.py) |
| Runner | [`examples/roles/supervisor.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/supervisor.py) |
| Target | **Ubuntu** (systemd) — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `supervisor` role mapping → `SupervisorVars` |

<!---
| Waves | `2` on a changing pass (requests, then handler), `1` once converged — one checkpoint, no facts |
| Outputs | none — the deploy returns `None`; no other role reads supervisor's results |
| Extra deps | **`reemote[templates]`** (jinja2 renders the config, program and unit templates) |
| Offline test | [`tests/roles/test_supervisor.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_supervisor.py) |
| Factory | plain (non-`async`) — the runner calls `supervisor(v)`; the three renders are control-side Python inside the body, not barriers |

`supervisor` installs the pip-managed `supervisor` package on Ubuntu hosts
(optionally version-pinned), creates the `0755` log directory and the `0755`
config paths and their `conf.d` child, writes `{{ config_path }}/supervisord.conf`
and one `conf.d/<name>.conf` per managed program from the role's Jinja2
templates (root:root, mode `0644`), writes the systemd `supervisord.service`
unit, and starts and enables the service; any config, program or unit change
queues the role's one handler, `restart supervisor`. The role has no
command/shell tasks and reads no facts at all.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with
`yield from scoped("supervisor", supervisor(v))(host)` and a run is one
`run_waves` pass. The Ansible role's whole task list *and* its handler are that
single deploy, and its `defaults/main.yml` is `SupervisorVars`.

Like memcached and redis, this is a **checkpoint-only role** (design doc
§18.13). It differs from redis only in what the single wave contains — the pip
install, three directories, the three rendered files (with an absent direction),
the unit, and one collapsed service request. The stream is:

```python
yield from configure(supervisor_plan(hv))(host)  # wave 0 — the task list, no facts
wave_report = yield checkpoint("configure done") # the role's only barrier
notified = changed_hosts(wave_report, notifying_names(plan))
if hv.started:                                   # the handler task's when: (D8)
    yield from handler(notified)(host)           # wave 1 — the restart, or nothing
```

## 1. What it is, and where it came from

The role's tasks — and its handler — map onto reemote requests and one barrier:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `include_vars: "{{ ansible_os_family }}.yml"` | frozen `vars/Debian.yml` constants (`BIN_PATH`, `CTL_BIN_PATH`); the single-OS scope needs no gather, the RedHat files are scoped out | recipe |
| `pip: name=supervisor state=present version={{ … \| default(omit) }}` | `pip.packages(packages=(supervisor, \| supervisor==<version>), pip=plan.pip, sudo=True)` — an empty `version` omits the pin (D7) | covered |
| `file: dest={{ log_dir }} state=directory mode=0755` | `files.directory(path=plan.log_dir, mode="0755", sudo=True)` | covered |
| `file:` the config path and its `conf.d` (`config.yml`) | two `files.directory(user="root", group="root", mode="0755")` requests | covered |
| `template: supervisord.conf.j2 mode=0644` `notify: restart supervisor` | control-side Jinja2 render (the `hash('sha1')` filter registered, D4) → `files.install(user="root", group="root", mode="0644")`, name notifies | recipe |
| `template: program.conf.j2 … state={{ item.state \| default('present') }}` (present loop) `notify` | one `files.install` per present program, `conf.d/<name>.conf`, name notifies | recipe |
| the same loop, `state=absent` direction | one `files.file(path=…, present=False, sudo=True)` per absent program, name notifies | recipe |
| `template: supervisord.unit.j2 → /etc/systemd/system/supervisord.service` (`init-setup.yml`, systemd branch) `notify` | control-side render → `files.install(UNIT_PATH, mode="0644")`, name notifies; only written when started *or* enabled (D3) | recipe |
| `service: name=supervisord state=started` (`when: supervisor_started`) **and** `service: enabled=true` (`when: supervisor_enabled`) | **one** `systemd.service(service="supervisord", running=True/None, enabled=True/None, daemon_reload=True, sudo=True)` — the `None` sentinel mirrors a skipped task (D5, issue #63); the implicit reload is state-gated (D6, #50) | recipe |
| `handlers/main.yml: restart supervisor` (`state=restarted`, `when: supervisor_started`) | `wave_report = yield checkpoint("configure done")` → `changed_hosts(…, notifying_names)` → `restart_handler(notified, "supervisord", daemon_reload=True)`, gated on `hv.started` in the body | barrier |

The task mapping needed no library extension — the two historical blockers
(`pip.packages` and a state-gated `daemon_reload`) are both shipped and closed
(issues #26 and #50), so every task landed on a shipped operation. The role
reads nothing mid-play, so there is no `fact`/`act` barrier to place and the
checkpoint is the entire barrier surface.

## 2. Install

```bash
pip install "reemote[templates]"   # or: uv add "reemote[templates]"
```

`jinja2` is the only extra: the three files are rendered from the role's
bundled templates (the `{% if %}` guards of `supervisord.conf.j2`, the
`is defined` test of `program.conf.j2`), which a plain f-string cannot
reproduce. The import enters only through `reemote.roles._render`, *inside* the
render functions, so `import reemote.roles` stays clean without the extra and a
missing `jinja2` surfaces at render time rather than at import time.

**Prerequisite — pip.** The role installs Supervisor *with pip*, exactly like
the Ansible role whose README says "this role requires pip". On a host without
`pip3`, run the shipped `pip` role first (`examples/roles/pip.py`): it installs
`python3-pip` and removes the PEP 668 `EXTERNALLY-MANAGED` marker.

SSH access to each Ubuntu host with the key named in its `connection`, and
passwordless sudo (or a `privilege` section in the inventory): every request
carries `sudo=True`, because reemote has no play-level `become`. The three
config writes are spool-and-install (`files.install`), so the transfer itself
never elevates; the root-owned `0644` files converge on the plain content
read-back (no elevated probe needed, unlike redis's setgid config).
--->

::: reemote.roles.supervisor
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - supervisor

**Variables:**

### Role variables (`defaults/main.yml`)

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `version` | `str` | `""` — empty installs `supervisor` unpinned (the role's `default(omit)`); a value pins `supervisor==<version>` | `supervisor_version` |
| `started` | `bool` | `True` — whether the unit runs, and whether the restart handler is eligible at all (D8) | `supervisor_started` |
| `enabled` | `bool` | `True` — the unit's boot-time state | `supervisor_enabled` |
| `config_path` | `str` | `/etc/supervisor` — where `supervisord.conf` and `conf.d` live | `supervisor_config_path` |
| `include_files` | `str` | `""` — empty derives `<config_path>/conf.d/*.conf`, as the Jinja default does | `supervisor_include_files` |
| `programs` | `tuple[Program, …]` | `()` — one `[program:<name>]` file each; a `Program` carries `name`, `command` (required when present), `state` (`present`/`absent`), optional `configuration` | `supervisor_programs` |
| `nodaemon` | `bool` | `False` (`[supervisord] nodaemon=`) | `supervisor_nodaemon` |
| `log_dir` | `str` | `/var/log/supervisor` — created `0755`, the `childlogdir` | `supervisor_log_dir` |
| `user` | `str` | `root` — `[supervisord] user=` and the http-server username | `supervisor_user` |
| `password` | `str` | `my_secret_password` — `{SHA}`-hashed into the server sections, plain for `[supervisorctl]` | `supervisor_password` |
| `unix_http_server_enable` | `bool` | `True` — the `[unix_http_server]`/`[supervisorctl]` block | `supervisor_unix_http_server_enable` |
| `unix_http_server_socket_path` | `str` | `/var/run/supervisor.sock` (the socket `file=` and `serverurl=`) | `supervisor_unix_http_server_socket_path` |
| `unix_http_server_password_protect` | `bool` | `True` — emits the `username`/`password` lines | `supervisor_unix_http_server_password_protect` |
| `inet_http_server_enable` | `bool` | `False` — the opt-in `[inet_http_server]` branch, carried by the verbatim template at no extra wave | `supervisor_inet_http_server_enable` |
| `inet_http_server_port` | `str` | `*:9001` (a `*`/empty host renders `localhost` in `serverurl`) | `supervisor_inet_http_server_port` |
| `inet_http_server_password_protect` | `bool` | `True` | `supervisor_inet_http_server_password_protect` |
| `pip` | `str` | `pip3` — the pip executable (D7; a virtualenv's `bin/pip` path also fits) | *(new)* |

<!---
Precedence is `model defaults < inventory role mapping < overrides`. Validation
is fail-fast at `resolve_vars`, before any host is contacted: `config_path`,
`log_dir`, `user` and `pip` must be non-empty strings, the unix socket path must
be non-empty whenever its server is on the default path, and a `Program` must
have a name — with a command if its `state` is `present` (the present template
renders `command=` unconditionally; an `absent` item needs only its name).
`state` is a `Literal` over `present`/`absent`. Sequences are `tuple`s (a JSON
list arrives normalized to one, the hashability rule). A wrongly typed value is
a pydantic `ValidationError` — a `ValueError`, so the runner maps it. A key
naming no field in the role mapping raises `RoleVarsError` naming the valid keys.

## 5. Run it

The role is library code; `examples/roles/supervisor.py` is the runnable
artifact — a standalone program with its own `main()`, no shared runner. It
loads and validates `inventory.json` from the current working directory,
resolves the role's variables (the defaults overlaid with the entry's
`supervisor` role mapping, then the call-site `overrides`), builds the deploy
from the `supervisor` factory and drives it with `run_waves` — a `dry=True`
preview, then the real apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import SupervisorVars, supervisor

SUPERVISOR_OVERRIDES = {"programs": ({"name": "demo", "command": "sleep 3600"},)}

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(SupervisorVars, "supervisor", inventory,
                            overrides=SUPERVISOR_OVERRIDES)
    deploy = supervisor(vars_set)                                              # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)             # parks at the checkpoint
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)                        # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/pip.py        # the pip prerequisite, once
uv run python examples/roles/supervisor.py
```

The call-site `SUPERVISOR_OVERRIDES` manages one `demo` program, so the
demonstration writes `conf.d/demo.conf` visibly and the `restart supervisor`
handler has something to fire on. The `dry=True` pass is *not* empty: the role's
first items are requests, so the dry run plans all eight configure requests and
then parks on the checkpoint barrier (`barrier: configure done → unknown until
run`) — the notification read needs a real apply report, so the handler wave
stays unplanned, exactly like Ansible's `--check` skipping handlers.

**Run the program twice.** The second offline pass must report no change: the
package is installed, the three directories and three files are in place, the
unit is running and enabled, and the state-gated daemon-reload emits no command —
and the checkpoint notifies nobody, so the handler wave never appears. That is
the migration's idempotence claim, and the offline suite asserts it.

## 6. What it does

One deploy. The checkpoint closes the request wave, and a wave with no records
is not appended — so the shape below is what the test pins:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `configure`: `pip.packages`, `files.directory(log_dir)`, two `files.directory` config paths, `files.install(supervisord.conf)`, one `files.install`/`files.file(present=False)` per program, `files.install(unit)` when written, `systemd.service(running=…, enabled=…, daemon_reload=True)` — then `checkpoint("configure done")` | every input is a resolved variable or a frozen constant, so the whole task list is one pure request wave in `tasks/main.yml`'s own order; the checkpoint reads its outcome |
| 1 | `handler`: `restart_handler(notified, "supervisord", daemon_reload=True)` — a request only on the hosts the checkpoint selected *and* while `started` is true | the role's one `notify:` channel; an empty notification set emits no request, so a converged pass has no wave 1 at all |

Five rules are load-bearing:

* **The checkpoint is the only barrier — and there are no facts at all.** The
  vars split by OS family, not release, and every template input is a resolved
  variable or a frozen constant, so all three renders are pure control-side
  Python inside the body (the memcached/redis precedent). A `fact`, `act` or
  `control` barrier here would be speculatively added.
* **The two `service:` tasks collapse to one request.** Ansible runs a
  `state: started` task gated on `supervisor_started` and an `enabled: true`
  task gated on `supervisor_enabled`; reemote spells both as the flags of one
  `systemd.service` request, using the `None` sentinel for whichever gate is
  false — the exact mirror of a skipped task, so the role never stops or
  disables the unit (D5, issue #63).
* **The unit file is written only when the service is managed.** `write_unit`
  reproduces `init-setup.yml`'s inclusion gate (`started or enabled`): a host
  with both flags false gets no unit request, and the unit name drops out of the
  notify channel (D3).
* **The daemon-reload is state-gated (D6, #50).** Writing the unit under
  `/etc/systemd/system` marks a reload pending, so the very pass that installs
  or rewrites the unit runs `systemctl daemon-reload`; a converged pass rewrites
  nothing, so no reload command appears — proven on the second tree.
* **The handler entry is the library's generated name.** The log line is
  `Restart supervisord (handler)` — `restart_handler`'s default label — and
  `changed_hosts` matches the *notifying* names (main config, program configs,
  unit) exactly; the pip, directory and service tasks carry no `notify` in the
  role, so their changes never restart the daemon.

A host whose `family` is not `"Ubuntu"` returns before its first `yield`: no
requests, no barriers, no report lines — RedHat and other entries in a shared
inventory are never contacted.

## 7. Transcript

From `tests/roles/test_supervisor.py`, against the simulated Ubuntu host (one
`demo` program) plus a RedHat host the role never touches. No between-pass
bridge is needed — the simulation's own mutators maintain every seam the second
pass reads. Dry preview first: every request is planned, the checkpoint is
unknown, nothing is applied.

```text
(root)
  wave 0
    ubuntu: Ensure Supervisor is installed [changed: 1 package(s) to install]
    ubuntu: Ensure Supervisor log dir exists [changed: /var/log/supervisor missing, will create]
    ubuntu: Ensure Supervisor config paths exist (/etc/supervisor) [changed: /etc/supervisor missing, will create]
    ubuntu: Ensure Supervisor config paths exist (/etc/supervisor/conf.d) [changed: /etc/supervisor/conf.d missing, will create]
    ubuntu: Ensure Supervisor configuration is present [changed: content /etc/supervisor/supervisord.conf is absent, will install]
    ubuntu: Ensure Supervisor program config is present (demo) [changed: content /etc/supervisor/conf.d/demo.conf is absent, will install]
    ubuntu: Ensure Supervisor systemd unit is present [changed: content /etc/systemd/system/supervisord.service is absent, will install]
    ubuntu: Ensure Supervisor is started and enabled [changed: supervisord.service not running (None), will start; supervisord.service not enabled, will enable]
    barrier: configure done → unknown until run
```

Fresh host, real apply (the pip install, the three directories, the three
spool-and-installs, the started/enabled service with its state-gated
daemon-reload, then the handler):

```text
(root)
  wave 0
    ubuntu: Ensure Supervisor is installed [changed: 1 package(s) to install]
        $ sudo pip3 install supervisor
    ubuntu: Ensure Supervisor log dir exists [changed: /var/log/supervisor missing, will create]
        $ sudo mkdir -p /var/log/supervisor
        $ sudo chmod 755 /var/log/supervisor
    ubuntu: Ensure Supervisor configuration is present [changed: content /etc/supervisor/supervisord.conf is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-<hash>/content
        $ sudo sh -c 'mkdir -p /etc/supervisor && cp … /etc/supervisor/supervisord.conf && chown root:root … && chmod 644 … && rm -rf …'
    ubuntu: Ensure Supervisor systemd unit is present [changed: content /etc/systemd/system/supervisord.service is absent, will install]
        $ sudo sh -c 'mkdir -p /etc/systemd/system && cp … /etc/systemd/system/supervisord.service && …'
    ubuntu: Ensure Supervisor is started and enabled [changed: daemon-reload; supervisord.service not running (None), will start; supervisord.service not enabled, will enable]
        $ sudo systemctl daemon-reload
        $ sudo systemctl start supervisord.service
        $ sudo systemctl enable supervisord.service
    barrier: configure done → 1 host notified
  wave 1
    ubuntu: Restart supervisord (handler) [changed: supervisord.service already running; supervisord.service will restart]
        $ sudo systemctl restart supervisord.service
```

The rerun — the checkpoint re-runs (it reads the wave, not a cached plan), every
request is a no-op, the reload is not pending so no `daemon-reload` command
appears, and **the handler wave disappears**:

```text
(root)
  wave 0
    ubuntu: Ensure Supervisor is installed [no change: all packages present]
    ubuntu: Ensure Supervisor log dir exists [no change: directory /var/log/supervisor already exists]
    ubuntu: Ensure Supervisor config paths exist (/etc/supervisor) [no change: directory /etc/supervisor already exists]
    ubuntu: Ensure Supervisor config paths exist (/etc/supervisor/conf.d) [no change: directory /etc/supervisor/conf.d already exists]
    ubuntu: Ensure Supervisor configuration is present [no change: content already up to date (/etc/supervisor/supervisord.conf)]
    ubuntu: Ensure Supervisor program config is present (demo) [no change: content already up to date (/etc/supervisor/conf.d/demo.conf)]
    ubuntu: Ensure Supervisor systemd unit is present [no change: content already up to date (/etc/systemd/system/supervisord.service)]
    ubuntu: Ensure Supervisor is started and enabled [no change: supervisord.service already running; supervisord.service already enabled]
    barrier: configure done → 0 hosts notified
```

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | The family guard is the body's opening `if`, not a `when=` on every request | an off-target host emits no requests, no barriers and no `[skipped]` lines at all — the corpus-wide rule |
| D2 | The `include_vars` OS dispatch collapses to frozen constants | a Phase 0 single-OS (Ubuntu) decision: `vars/Debian.yml` only sets the two `/usr/local/bin` paths (`BIN_PATH`, `CTL_BIN_PATH`); the RedHat files are scoped out (§9) |
| D3 | The non-systemd init-script branch is scoped out; the unit is only written when started *or* enabled | modern Ubuntu is always systemd, so the 173-line `supervisord.init-Debian.j2` SysV template is not ported; `write_unit` reproduces `init-setup.yml`'s inclusion gate |
| D4 | Ansible's `hash('sha1')` filter becomes a registered Jinja2 filter | the template stays verbatim and the `{SHA}<hexdigest>` password line is byte-identical to Ansible's |
| D5 | The two final `service:` tasks collapse to one `systemd.service` request | `state: started` (gated on `supervisor_started`) and `enabled: true` (gated on `supervisor_enabled`) become `running=`/`enabled=` with the `None` sentinel for a false gate — the exact mirror of a skipped task (issue #63); the role never stops or disables the unit |
| D6 | `daemon_reload=True` on the service and the handler, state-gated | Ansible's systemd modules reload implicitly after a unit change; reemote spells it out and gates it on `SystemdNeedDaemonReload` (issue #50), so a converged run emits no reload command |
| D7 | The pip executable is an explicit variable (`pip`, default `pip3`) | instead of the Ansible pip module's interpreter auto-detection — the shipped `pip` role's choice, and it lets a virtualenv's `bin/pip` be targeted by path |
| D8 | The handler's `when: supervisor_started` gate is a body-level check | a static, deterministic read of the resolved variable — not a request `when=` — so a `started: false` host configures but never restarts |
| D9 | `files.install` idempotence is content-only, no metadata companion | an owner/mode drift on unchanged content neither re-installs nor queues the restart; accepted for these root-owned `0644` files (the apache precedent — nothing here is service-owned, so no daemon can be locked out of its config, unlike redis's `0640` companion) |

## 9. Scoped out

* **RedHat branches** (`vars/RedHat.yml`, `vars/RedHat-7.yml`,
  `supervisord.init-RedHat.j2`): a Phase 0 single-OS decision — the target is
  Ubuntu.
* **The non-systemd init-script branch** (`supervisord.init-Debian.j2`): the
  same decision — modern Ubuntu is always systemd, so the `service_mgr` gate in
  `init-setup.yml` is always true and the SysV template is not ported (D3).
* **`geerlingguy.pip` as a hard dependency:** the role's README names it as a
  prerequisite; in reemote that is the shipped `pip` role
  (`examples/roles/pip.py`) run first, not something bundled into `supervisor`.
--->
