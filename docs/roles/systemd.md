# systemd — systemd unit role

| | |
| --- | --- |
| Role | [`0x0I/systemd`](https://github.com/0x0I/ansible-role-systemd) |
| Module | [`reemote.roles.systemd`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/systemd.py) |
| Example | [`examples/roles/systemd.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/systemd.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `systemd` role mapping → `SystemdVars` |

<!---
| Waves | 2 (files + checkpoint, then handlers + launch); **3** with a per-user unit |
| Outputs | none — the deploy returns `None` |
| Extra deps | **`reemote[templates]`** (jinja2 renders the two unit templates) |
| Offline test | [`tests/roles/test_systemd.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_systemd.py) |

`systemd` manages arbitrary systemd units on an Ubuntu host the way the upstream
role does: the unit load-path directory, the rendered `.service`/`.socket`/
`.mount`/`.target` unit file, the rendered `.conf` drop-in override, the two
reload handlers, each unit's running and enabled state, and the opt-in
uninstall of everything the role wrote.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with `yield from scoped("systemd", systemd(v))(host)`
and a run is one `run_waves` pass. The Ansible role's whole `tasks/` tree —
preflight, `common/config.yml`, `common/launch.yml` and its `meta: flush_handlers`
— is that single per-host generator, and its `defaults/main.yml` is `SystemdVars`.

The role is also the corpus's clearest case of **per-request notification**: its
two handlers loop over *registered* template results and gate on each item's own
`changed`, so the checkpoint's report is read by request name and not only by
host (D3).

## 1. What it is, and where it came from

| Ansible task | reemote request | Status |
| --- | --- | --- |
| `getent: passwd` + the `_unit_home` spelunking | `yield fact(Users)` — **only** when a unit names a non-root user or scope | barrier (D2) |
| `assert:` unit owners exist in passwd | `assert_users_exist` → `ValueError` in the body | recipe (D8) |
| `assert:` reject `scope=user` on PID 1 load paths | `assert_not_pid1_user_scope` → `ValueError`, before the read | recipe (D8) |
| `file: path={{ item.path }} state=directory mode=0755 follow=false` | `files.directory(path=…, mode="0755", sudo=True)` | covered (D6) |
| `template: src=systemd.unit.j2 … notify: Reload systemd units` | control-side render → `files.install(content=…, mode="0644")` | covered |
| `template: src=override.conf.j2 … notify: Reload systemd daemon` | control-side render → `files.install(content=…)` | covered |
| `handlers: systemd: name=… state=… daemon_reload=true` | `systemd.service(service=…, running=…, daemon_reload=True)` | covered |
| `handlers: systemd: daemon_reload=true` (no `name`) | `systemd.daemon_reload()` — the stateless whole-manager form | covered |
| `meta: flush_handlers` | `checkpoint("configure done")`, then both handler deploys in the next wave | deviation (D3) |
| `systemd: … scope=user` + `environment: XDG_RUNTIME_DIR` | `systemd.service(…, user_name=…)` with `op(env=(("XDG_RUNTIME_DIR", …),))` | deviation (D1) |
| `command: /bin/true` + `changed_when: true` + `notify: Uninstall units` | `perform_uninstall` read by the body → `uninstall(plans, users)` | recipe |
| `become_user: {{ item.user }}` | `files.install(user=…, group=…)` on the root-owned spool-and-install | deviation (D1) |
| `include_vars` / the `ansible_os_family` platform list | the frozen constants and one family guard | deviation |
| `systemd: scope=global` | — | scoped out (D7) |

No library gap blocked this migration, so none was filed: `files.install` already
carries the templated write with ownership and mode, `files.directory` the load
path, and `systemd.service` both axes the role's `systemd:` tasks assert —
including `running=None` for Ansible's `state: omit` (issue
[#63](https://github.com/kimjarvis/reemote/issues/63)) and the state-gated
`daemon_reload=True` (issue
[#50](https://github.com/kimjarvis/reemote/issues/50)), which is what lets the
reload handler converge instead of reloading on every run.

## 2. Install

```bash
pip install "reemote[templates]"     # or: uv add "reemote[templates]"
```

`jinja2` is the only extra. The base library imports neither `jinja2` nor
`pyyaml` — they enter through `reemote/roles/_render.py`, and `systemd` imports
that module *inside* `_environment()`, so `import reemote.roles` stays clean
without the extra and a missing `jinja2` surfaces as an `ImportError` at render
time rather than at import time. The `comment` filter the two templates'
`{{ ansible_managed | comment }}` headers ask for is registered there too, since
it is Ansible's filter rather than Jinja2's.

SSH access to each Ubuntu host with the key named in its `connection`, and
passwordless sudo (or a `privilege` section in the inventory): every request
carries `sudo=True`, because reemote has no play-level `become` and `/etc` is
root-owned. A per-user unit's `systemctl` runs **as root** against
`--machine=<user>@.host` rather than by becoming that user (D1).

## 3. Variables

`SystemdVars` is a frozen pydantic model (`extra="forbid"`) whose fields carry
bare names; the role name passed to `resolve_vars` binds the entry's `systemd`
role mapping, so an inventory written for the role needs no key gymnastics.

--->

::: reemote.roles.systemd
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - systemd

**Variables:**

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `unit_config` | `tuple[UnitSpec, …]` | `()` | `unit_config` |
| `perform_uninstall` | `bool` | `false` | `perform_uninstall` |
| `backup_files` | `bool` | `true` — **inert** (no timestamped copy) | `systemd_backup_files` |

<!---
Precedence is `model defaults < inventory role mapping < overrides`. Sequences are
`tuple`, never `list`: the model is frozen and hashable, and a value that reaches
`op()` must hash. An empty `unit_config` — the default — makes the role a no-op,
exactly as the role's `when: unit_config | length > 0` gates do, so the host emits
no requests, no barriers and no log lines.

### 3.1 `unit_config` entries

Each entry is a `UnitSpec`. `extra="allow"` is the point of that model: the keys
the *tasks* read are declared, and every other key — `Unit`, `Service`, `Socket`,
`Mount`, `Install`, or any unit-type section Systemd supports — survives to the
template untouched.

| Field | Type | Default | Upstream |
| --- | --- | --- | --- |
| `name` | **required**, non-empty | — | `unit_item.name` (a template unit ends in `@`) |
| `type` | `Literal[service, socket, target, device, mount, automount, timer, swap, path, slice, scope, conf]` | `null` → `service` | `unit_item.type`, `_default_unit_type` |
| `path` | `str \| None` | `null` → `/etc/systemd/system`, and **no** directory task | `unit_item.path`, `_default_unit_path` |
| `user` | `str \| None` | `null` → `root` | `_unit_user` |
| `group` | `str \| None` | `null` → `user` | `_unit_group` |
| `scope` | `Literal["system", "user"]` | `null` → `user` for a non-root `user`, else `system` | `_unit_scope` |
| `state` | `Literal["started", "stopped"] \| None` | `null` → `stopped` at launch, *omitted* by the reload handler | `unit_item.state` |
| `enabled` | `bool` | `false` | `unit_item.enabled` |
| `manage_state` | `bool` | `true` | `unit_item.manage_state` |
| `reload_on_change` | `bool` | `true` | `unit_item.reload_on_change` |
| any other key | kept, and rendered as INI sections | — | the entry's `config` dict |

The optional keys keep Jinja's *undefined vs None* distinction intact:
`resolve_units` dumps each entry with `model_dump(exclude_none=True)`, so a key
the entry left out is undefined for the template and its
`config.type|default('service')|title` still works. `type: conf` is the role's
own special case — a drop-in override, rendered by the second template and never
reloaded or activated as a unit.

## 5. Run it

The role is library code; `examples/roles/systemd.py` is the runnable artifact —
a standalone program with its own `main()`, no shared runner. It loads and
validates `inventory.json` from the current working directory, resolves the
role's variables (the defaults overlaid with the entry's `systemd` role mapping,
then the call-site `overrides`), builds the deploy from the `systemd` factory and
drives it with `run_waves` — a `dry=True` preview, then the real apply — printing
the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import SystemdVars, systemd

SYSTEMD_OVERRIDES = {
    "unit_config": (
        {
            "name": "reemote-demo",
            "state": "started",
            "enabled": True,
            "Unit": {"Description": "A service managed by the reemote systemd role"},
            "Service": {"ExecStart": "/usr/bin/sleep infinity"},
            "Install": {"WantedBy": "multi-user.target"},
        },
        {
            "name": "reemote-demo-override",
            "type": "conf",
            "path": "/etc/systemd/system/reemote-demo.service.d",
            "Service": {"Environment": ["REEMOTE_DEMO=1"]},
        },
    ),
}

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(SystemdVars, "systemd", inventory, overrides=SYSTEMD_OVERRIDES)
    deploy = systemd(vars_set)
    report, _hosts = await run_waves(inventory, deploy, dry=True)   # preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)             # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/systemd.py
```

The `dry=True` pass of a **root-owned** configuration is complete for wave 0 — no
barrier sits in front of the first request, so every file is planned against the
host's real pre-state and the run parks on the checkpoint
(`barrier: configure done → unknown until run`). A configuration that includes a
per-user unit stops one step earlier, at its `fact(Users)` read.

`main()` prints the wave tree and maps the role's failure modes (bad inventory,
`ValueError` from a rejected variable or one of the two preflight assertions,
`CommandError`, `HostConnectionError`, `PrivilegeError`, `OSError`, `TypeError`)
to a one-line stderr message and exit 1.

**Run the program twice.** The second run must report no change: both files are
byte-identical, so the checkpoint notifies neither handler, and the unit is
already running and enabled. That is the migration's idempotence claim, and the
offline suite asserts it.

> **Shared demo host.** The example's `reemote-demo.service` is a real unit on the
> demo Ubuntu host. To undo it, re-declare the same `unit_config` with
> `perform_uninstall: true` and run the program once more — that path stops and
> disables each unit and removes each file the role wrote.

## 6. What it does

One deploy. Each barrier closes its own wave, and a wave with no records is not
appended — so the wave count is **host-dependent**:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `fact(Users)` — **only** when some unit names a non-root `user` or a `scope: user` manager | the role's one `getent passwd`: the owner-existence assert, the home behind `_own_as_user`, and the uid behind `XDG_RUNTIME_DIR` all come from this read (D2) |
| 0 or 1 | `configure`: the load-path directory of every entry that named a `path`, then each rendered unit or drop-in — closed by `checkpoint("configure done")` | the checkpoint **is** the role's two `notify:` lines: it reads the wave it just closed, per request name |
| 1 or 2 | `reload_units` (per changed unit), `reload_daemon` (per changed drop-in), then `launch` (per non-drop-in, non-template unit) | `meta: flush_handlers` becomes in-stream ordering: the driver applies each request before pulling the next, so the `daemon-reload` still precedes the `systemctl start` that needs it |

Three ordering rules are load-bearing:

* **The checkpoint immediately follows `configure`.** A checkpoint placed after
  the handler wave would read the handlers' entries instead and notify nobody.
* **Both handlers precede the activation.** In one wave, in that order — which is
  what the role bought with `flush_handlers` before its launch task.
* **The notification is per request, not per host.** `changed_names` slices the
  checkpoint's report by host *and* by name, so a host with three units and one
  changed file reloads one unit (D3).

A host whose `host.family` is not `"Ubuntu"` — or whose `unit_config` is empty —
returns before its first `yield`, so it emits no requests, no barriers and no
report lines at all.

## 7. Transcript

From `tests/roles/test_systemd.py::test_tree_snapshot`, against the simulated
Ubuntu host plus a RedHat host the role never touches. The `systemd` role mapping
declares `reemote-service` (a service with `state: started`, `enabled: true`),
`reemote-socket` (a socket naming no state) and `reemote-override` (a `conf`
drop-in under `reemote-service.service.d`). Fresh host, first pass:

```text
(root)
  wave 0
    ubuntu: Render systemd unit file /etc/systemd/system/reemote-service.service [changed: content /etc/systemd/system/reemote-service.service is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-…/content
        $ sudo sh -c 'mkdir -p /etc/systemd/system && cp … /etc/systemd/system/reemote-service.service && chown root:root … && chmod 644 … && rm -rf …'
    ubuntu: Render systemd unit file /etc/systemd/system/reemote-socket.socket [changed: content /etc/systemd/system/reemote-socket.socket is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-…/content
        $ sudo sh -c 'mkdir -p /etc/systemd/system && cp … /etc/systemd/system/reemote-socket.socket && chown root:root … && chmod 644 … && rm -rf …'
    ubuntu: Create unit configuration path (/etc/systemd/system/reemote-service.service.d) [changed: /etc/systemd/system/reemote-service.service.d missing, will create]
        $ sudo mkdir -p /etc/systemd/system/reemote-service.service.d
        $ sudo chmod 755 /etc/systemd/system/reemote-service.service.d
        $ sudo chown root:root /etc/systemd/system/reemote-service.service.d
    ubuntu: Render systemd drop-in file /etc/systemd/system/reemote-service.service.d/reemote-override.conf [changed: content /etc/systemd/system/reemote-service.service.d/reemote-override.conf is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-…/content
        $ sudo sh -c 'mkdir -p /etc/systemd/system/reemote-service.service.d && cp … && chown root:root … && chmod 644 … && rm -rf …'
    barrier: configure done → 1 host notified
  wave 1
    ubuntu: Reload systemd unit reemote-service.service [changed: daemon-reload; reemote-service.service not running (None), will start]
        $ sudo systemctl daemon-reload
        $ sudo systemctl start reemote-service.service
    ubuntu: Reload systemd unit reemote-socket.socket [no change: reemote-socket.socket active state left alone]
    ubuntu: Reload systemd daemon (/etc/systemd/system/reemote-service.service.d/reemote-override.conf) [changed: daemon-reload]
        $ sudo systemctl daemon-reload
    ubuntu: Activate systemd unit reemote-service.service [changed: reemote-service.service already running; reemote-service.service not enabled, will enable]
        $ sudo systemctl enable reemote-service.service
    ubuntu: Activate systemd unit reemote-socket.socket [changed: reemote-socket.socket already stopped; reemote-socket.socket not enabled, will enable]
        $ sudo systemctl enable reemote-socket.socket
```

The two `scp` spool directories are content-addressed, so the real run shows their
hashes where `…` is above; the test pins them.

The rerun — nothing changed, so the checkpoint notifies neither handler and **the
handler lines disappear entirely**, while the activation stays (it is an assert, not
a notification):

```text
(root)
  wave 0
    ubuntu: Render systemd unit file /etc/systemd/system/reemote-service.service [no change: content already up to date (/etc/systemd/system/reemote-service.service)]
    ubuntu: Render systemd unit file /etc/systemd/system/reemote-socket.socket [no change: content already up to date (/etc/systemd/system/reemote-socket.socket)]
    ubuntu: Create unit configuration path (/etc/systemd/system/reemote-service.service.d) [no change: directory /etc/systemd/system/reemote-service.service.d already exists]
    ubuntu: Render systemd drop-in file /etc/systemd/system/reemote-service.service.d/reemote-override.conf [no change: content already up to date (/etc/systemd/system/reemote-service.service.d/reemote-override.conf)]
    barrier: configure done → 0 hosts notified
  wave 1
    ubuntu: Activate systemd unit reemote-service.service [no change: reemote-service.service already running; reemote-service.service already enabled]
    ubuntu: Activate systemd unit reemote-socket.socket [no change: reemote-socket.socket already stopped; reemote-socket.socket already enabled]
```

With a per-user unit the run gains its leading wave, and that unit's `systemctl`
lines address the user manager (D1):

```text
(root)
  wave 0
    barrier: Resolve passwd entries for unit owners → {'root': {…}, 'sduser': {'home': '/home/sduser', …, 'uid': 1234, …}}
  …
  wave 2
    ubuntu: Reload systemd unit reemote-job.service [changed: reemote-job.service not running (None), will start]
        $ sudo env XDG_RUNTIME_DIR=/run/user/1234 systemctl --machine=sduser@.host start reemote-job.service
```

The RedHat host appears in no tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | No `become_user` for a per-user unit | reemote's privilege comes from the inventory, never from an operation. The file is written by the root-owned spool-and-install and `chown`ed by the same request (`files.install(user=…, group=…)`), and its manager is addressed **by name** — `systemd.service(user_name=…)` renders `systemctl --machine=<user>@.host`, run as root — with the role's `environment:` riding `op`'s own `env` meta-parameter. Different command, same manager |
| D2 | The `getent passwd` read is **conditional** | the role ran it whenever any unit was configured; here `fact(Users)` is taken only when some unit names a non-root `user` or a `scope: user` manager, so the common root-owned configuration costs no wave |
| D3 | Notification is read **per request name**, not per host | both handlers loop over a registered result list and gate on each item's own `changed`, so `changed_names` slices the checkpoint's log by host *and* name. `changed_hosts` would reload every unit of a host where one file changed |
| D4 | No companion `files.file` metadata request | the install family's idempotence is *content*-only, so an owner or mode drift on an otherwise identical unit file reports no change — and queues no reload. Same trade-off the shipped `nginx` role makes |
| D5 | `backup: {{ systemd_backup_files }}` is **carried and inert** | the install family keeps no timestamped copy of the file it replaces. The variable still binds, so an existing inventory works |
| D6 | `follow: false` on the directory task has no analogue | `files.directory` manages the directory the path resolves to, so a symlink at a load path is followed rather than managed as a link |
| D7 | `scope: global` is refused | reemote's systemd descriptors reach the system manager, a named machine or one named user's manager — never `systemctl --global`. `UnitSpec.scope` is a two-value `Literal`, so such an entry is rejected by `ValidationError` while the inventory is resolved |
| D8 | The two `assert:` tasks raise **`ValueError`** in the body | the corpus idiom for a configuration the deploy refuses to apply. The load-path assert is pure and runs *first*, so a configuration violating both reports the path problem before the passwd read |
| — | The family guard is the body's opening `if`, and an empty `unit_config` returns immediately | every preflight task of the role is gated `when: unit_config \| length > 0`, so such a host is a no-op — with no log lines to prove it, rather than a list of skipped ones |

## 9. Scoped out

* **`scope: global`.** See D7. A `systemctl --global` unit (a per-user default
  written into `/etc/systemd/user`) needs the operation to grow the flag, which is
  a library change rather than a migration one.
* **Non-Ubuntu families.** The role's `EL`/`Debian`/`Fedora`/`Ubuntu` platform list
  needs no dispatch for this migration: systemd is systemd, and the constants are
  the load paths Ubuntu uses. A host outside the `"Ubuntu"` family is skipped, not
  mapped.
* **Timestamped backups.** See D5.
* **`follow: false` on a symlinked load path.** See D6.
* **Removing units the inventory no longer names.** `perform_uninstall` removes
  exactly the files `unit_config` names, as the role's handler does. A sweep of
  `/etc/systemd/system` is out — the role has no such task.
* **The accounts behind a per-user unit.** The role asserted they exist; creating
  them is the [`users`](users.md) role's job, or your own
  `op(server.user, …)` request.
* **`daemon-reload-execcommand`, `root-hash`, `reload-propagate`.** The upstream
  module supports further manager verbs that no task of this role ever calls, so
  they are not modelled.

--->
