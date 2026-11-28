# minio — MinIO object-storage role

| | |
| --- | --- |
| Role | [`wezhai/minio`](https://github.com/wezhai/minio) |
| Module | [`reemote.roles.minio`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/minio.py) |
| Example | [`examples/roles/minio.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/minio.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `minio` role mapping → `MinioVars` |

<!---
| Waves | 8 changed / 8 converged on the full feature set (`1 + H + one per `mc` act + trailing sync wave`) |
| Outputs | `MinioOutputs(service, install_dir, endpoint)` — read by a composing role |
| Extra deps | **`reemote[templates]`** (jinja2 renders the env file, the unit, the sync units) |
| Offline test | [`tests/roles/test_minio.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_minio.py) |

`minio` installs and runs MinIO on Ubuntu hosts from the release tarball
(`minio` + `mc`): the service group and user, the five managed directories,
the unpacked binaries, the rendered `/etc/default/minio` and
`/etc/systemd/system/minio.service`, the started unit with its restart →
console-port-wait handler chain, and the three features the role keeps behind
`tags: never` — bucket creation, admin policy/user provisioning, and
`mc mirror` bucket-sync units — as ordinary variables here.

This is the **`act`-heavy** role of the migration sequence. The template roles
before it read the machine between steps; this one *commands* it: every
imperative `mc` shell in `bucket.yml`, `user.yml` and `sync.yml` becomes an
`act(Shell, sudo=True)` barrier whose `Result` is data, so the stream carries
one wave per command and the wave count grows with the provisioning lists:

```python
yield from install(plan)(host)                  # wave 0
yield from configure(plan)(host)
wave_report = yield checkpoint("install done")  # the role's notify
notified = changed_hosts(wave_report, NOTIFYING)
started = changed_hosts(wave_report, (START_NAME,))
yield from handler(plan, notified, started)(host)   # restart, then port wait
result = yield act(Shell, command=alias_set_script(plan), …)  # one wave each
for bucket in hv.buckets:
    yield act(Shell, command=bucket_script(plan, bucket), …)
```

Nothing about the `mc` state is gathered first: the alias set, the `mb | true`
gates and the `"already in effect"` tolerance are reproduced exactly where
Ansible ran them.

## 1. What it is, and where it came from

| Ansible task | reemote request | Status |
| --- | --- | --- |
| `group: name={{ minio_sys_group }}` | `server.group(group=…, sudo=True)` | covered |
| `user: name=… home=/home/minio createhome=yes` | `server.user(user=…, home=f"/home/{sys_user}", create_home=True)` | deviation (D11) |
| `file: path={{ item }} state=directory mode=0755` ×5 | `files.directory(path=…, user=…, group=…, mode="0755")` | covered |
| `file: /home/minio/.ansible/tmp` | — not migrated | deviation (D10) |
| `unarchive: src=minio.tar.gz (role files/)` | controller read → `files.install` spool → `files.unarchive(creates=…)`, or `package_url` fetched control-side | deviation (D5) |
| `template: minio_env.j2` / `minio.service.j2` + `notify` | control-side render → `files.install(content=…)` → `checkpoint("install done")` | barrier |
| `systemd: daemon_reload=yes state=started` + `notify` | `systemd.service(running=True, daemon_reload=True, name="Start minio service")` | covered |
| `shell: mc alias set …` (`become_user: minio`) | `act(Shell, command="sudo -u minio -H …", sudo=True)` | deviation (D6) |
| `shell: mc mb … \| true` / `mc ilm rule add` per bucket | one `act(Shell, …)` per bucket, non-zero raises | barrier (D9) |
| `shell: mc admin policy add` / `user add` + `failed_when: "already in effect"` | `act(Shell, …)`; the body reads the `Result` dict and tolerates that one stderr | covered (D9) |
| `copy: src={{ item.file }}` (policy JSON from `files/`) | controller read (bundled constant first) → `files.install(user=…, group=…)` | recipe |
| `run_once: true` (bucket cluster branch, users, sync) | the per-host `primary` variable gate | deviation (D4) |
| `loop: play_hosts` in the env template | `node_address` enumerated over the `VarsSet`, sorted | deviation (D3) |
| handlers: `systemd: restarted` → `wait_for: port=… timeout=60 delay=5` | one handler wave: `service_handler(…, action="restarted")` then `server.wait(port=…, timeout=60)` | deviations (D7, D8) |
| `template: sync.service.j2` per mapping + `systemd: started daemon_reload` | render → `files.install` → `systemd.service(running=True, daemon_reload=True)` | covered |
| `tags: never` on buckets/users/sync/uninstall | empty-list gates; uninstall scoped out | deviation (D2) |

No library gap blocked this migration, so none was filed: `files.unarchive`
already carries the tarball route (its `creates` gate the `mk | true`-style
idempotence the role lacks), the `act` barrier runs every `mc` command as a
command whose exit status is data, and `service_handler` carries `daemon_reload`
through to the restart — all three landed for earlier roles. What is new here
is only *shape*: the chained handler (restart notifies the wait) and the
`run_once` placement both read as in-stream choices, not as gaps.

## 2. Install

```bash
pip install "reemote[templates]"     # or: uv add "reemote[templates]"
```

`jinja2` is the only extra. The three templates render control-side through
`reemote/roles/_render.py`, imported *inside* each render function, so
`import reemote.roles` stays clean without the extra.

SSH access to each Ubuntu host with the key named in its `connection`, and
passwordless sudo (or a `privilege` section in the inventory): every request
carries `sudo=True`, because reemote has no play-level `become`. The `mc`
shells additionally embed `sudo -u minio -H` (D6) — the run-as user the role
set with `become_user` — and the service user's own home is created by the
`server.user` request before any shell runs.

You also need the release bundle itself (D5): either a controller-side tarball
(`package_path`) or a mirror URL (`package_url`) — the 44 MB
`files/minio.tar.gz` the Ansible role shipped cannot live in a library.

--->

::: reemote.roles.minio
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - minio

**Variables:**

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `cluster_mode` | `bool` | `true` | `minio_cluster_mode` |
| `node_address` | `str \| None` | `null` → the `connection` address | — (D3's knob, for `hostvars[…].ansible_host`) |
| `primary` | `bool` | `true` | — (D4's `run_once` stand-in) |
| `sys_user` | `str` | `minio` | `minio_sys_user` |
| `sys_group` | `str` | `minio` | `minio_sys_group` |
| `admin_user` | `str` | `minioadmin` | `minio_admin_user` |
| `admin_pass` | `str` | `minioadmin` | `minio_admin_pass` |
| `service_port` | `int` | `9000` | `minio_service_port` |
| `console_port` | `int` | `9001` | `minio_console_port` |
| `basedir` | `str` | `/data/minio` | `minio_basedir` |
| `install_dir` | `str \| None` | `null` → `<basedir>/bin` | `minio_install_dir` |
| `config_dir` | `str \| None` | `null` → `<basedir>/cfg` | `minio_config_dir` |
| `data_dir` | `str \| None` | `null` → `<basedir>/data` | `minio_data_dir` |
| `log_dir` | `str \| None` | `null` → `<basedir>/log` | `minio_log_dir` |
| `backup_dir` | `str` | `/opt/minio` | `minio_backup_dir` |
| `package_url` | `str \| None` | `null` | — (D5; the role's bundled `files/minio.tar.gz`) |
| `package_path` | `str \| None` | `null` | — (D5) |
| `policies` | `tuple[PolicySpec, …]` | one `getput` | the `policies` YAML anchor |
| `users` | `tuple[MinioUserSpec, …]` | `()` | `users` |
| `buckets` | `tuple[BucketSpec, …]` | `()` | `bucket_list` |
| `sync` | `tuple[SyncSpec, …]` | `()` | `sync_service` |

<!---

Precedence is `model defaults < inventory role mapping < overrides`. Sequences
are `tuple`, never `list`: the model is frozen and hashable, and a value that
reaches `op()` must hash. Exactly one of `package_url`/`package_path` must be
set — `build_plan` raises before the first request otherwise, so a run fails
with a message, not half-installed.

### 3.1 Feature-spec fields

`buckets` items are `BucketSpec`, `policies` items `PolicySpec`, `users` items
`MinioUserSpec`, `sync` items `SyncSpec` (with `SyncSite` sides and
`BucketMapping` pairs). All are frozen `extra="forbid"` models, so a typo in an
inventory mapping raises `ValidationError` while the variables resolve.

| Model | Field | Rule |
| --- | --- | --- |
| `BucketSpec` | `name` | required, non-empty |
| | `expire_days` | `null` → no `mc ilm rule add` |
| `PolicySpec` | `name` | required, non-empty |
| | `file` | `getput_policy.json` → the bundled constant; any other value is a controller-side path |
| | `content` | set → used verbatim, no read |
| `MinioUserSpec` | `username`, `password` | required, non-empty |
| | `policies` | `null` → the role-wide `policies`, the role's per-user `set_fact` |
| `SyncSpec` | `upload_limit` | `10MiB` |
| | `source` / `target` | `SyncSite`; the target needs `area_label`, `host`, `access_key` and `secret_key` |
| | `bucket_mapping` | one unit per `(local, remote)` pair |

The bundled `getput_policy.json` — the role's only shipped policy file — is
inlined verbatim as `GETPUT_POLICY_JSON`, because a library role has no
`files/` directory to copy from.

## 5. Run it

The role is library code; `examples/roles/minio.py` is the runnable artifact —
a standalone program with its own `main()`, no shared runner. It loads and
validates `inventory.json` from the current working directory, resolves the
role's variables (the defaults overlaid with the entry's `minio` role mapping,
then the call-site `overrides`), builds the deploy from the `minio` factory and
drives it with `run_waves` — a `dry=True` one-wave preview, then the real
apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import MinioVars, minio

MINIO_OVERRIDES = {
    "cluster_mode": False,
    "package_path": os.environ.get("REEMOTE_MINIO_PACKAGE", "minio.tar.gz"),
    "buckets": ({"name": "bucket01", "expire_days": 365},),
    "users": ({"username": "miniouser", "password": "miniouser1"},),
}

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(MinioVars, "minio", inventory, overrides=MINIO_OVERRIDES)
    deploy = minio(vars_set)
    report, _hosts = await run_waves(inventory, deploy, dry=True)   # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)             # the real apply
    print(report.tree())
```

From the repository root:

```bash
REEMOTE_MINIO_PACKAGE=/path/to/minio.tar.gz uv run python examples/roles/minio.py
```

The `dry=True` pass is a one-wave preview: it plans the install and configure
requests, parks on the `install done` checkpoint (`barrier: install done →
unknown until run`) and stops, gathering facts over SSH but applying nothing.
**Run the program twice.** On a converged host every request reports no change
— the binaries are unpacked (`creates` gate), the rendered files are
byte-identical, the unit is running — so the handler wave disappears entirely.
The `mc` shells still re-run: their results are data, not state (D9), exactly
like the role's non-idempotent `shell` tasks. That is the migration's
idempotence claim, and the offline suite pins both trees.

> **Shared demo host.** The `ubuntu` demo host also runs the apache and nginx
> examples. This role owns ports `9000`/`9001` and needs `/data/minio` (or a
> `basedir` override); nothing else in the corpus touches them.

### 5.1 Composing this role

`examples/roles/minio.py` runs `minio` on its own. To use it as a *component*
of a larger deploy, see
[`examples/roles/composing_roles.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/composing_roles.py):
a parent per-host generator embeds the role with
`out = yield from scoped("minio", minio(v))(host)` and reads the returned
`MinioOutputs` — `service`, `install_dir` and the `mc` alias `endpoint` — as
data. A host the family guard skipped still returns the dataclass, with empty
strings, so a parent can gate on `out.service` instead of re-deriving the
family check.

## 6. What it does

One deploy. Each barrier closes its own wave, and a wave with no records is
not appended — so the wave count is **feature-dependent**:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `install` (group, user, five directories, spool + unarchive) then `configure` (the two rendered files, `Start minio service`) — closed by `checkpoint("install done")` | the checkpoint **is** the role's `notify`: it reads the wave it just closed, and the body slices it twice — once for the two template names (`notified`), once for `Start minio service` (`started`) |
| 1 | `handler`: `Restart minio service (handler)` (only where `notified`) then `Waiting for minio console port …` (where either set names the host); the alias-set `act` closes the wave | D8's chained handlers as one wave; the wait rides *inside* it because a poll always reports changed |
| 2 … *n* | one wave per bucket, policy-create and user `mc` act; policy *copies* ride the first create's wave | every `act` barrier closes its own wave, so the wave count is `1 + H + #mc + (T)`, not a constant |
| last | the sync units' `files.install` + `systemd.service` requests | they follow the final `Add synchronize config` act; with no barrier left, they form the trailing wave |

Three ordering rules are load-bearing:

* **The unarchive follows the user.** The package lands as
  `<install_dir>/{minio,mc}` owned by `sys_user`; the extraction requests
  `user=`/`group=` that account, so the `server.user` request must be in the
  same wave *before* it — which the role's own task order already gives.
* **The checkpoint immediately follows `configure`.** Both `changed_hosts`
  reads slice the same report; a checkpoint placed after the handler wave
  would read the handler's entries instead and notify nobody.
* **Every `mc` act runs after the handler wave.** Buckets, policies and mirror
  units all dial `localhost:9000` — the port the restart handler was just
  waiting for. The barrier order *is* the dependency order; no `when=` needed.

A host whose `host.family` is not `"Ubuntu"` returns before its first `yield`,
so it emits no requests, no barriers and no report lines at all — and still
returns `MinioOutputs`, with empty strings (D1).

## 7. Transcript

From `tests/roles/test_minio.py::test_tree_snapshot`, against two simulated
Ubuntu hosts (`ubuntu` the primary, `node2` `primary: False`) plus a RedHat
host the role never touches, with buckets, users and one sync pair configured
in cluster mode. The `node2` duplicates of each request line and the
content-addressed spool directories are elided as `…` here; the test pins both
byte-for-byte. Fresh hosts, first pass:

```text
(root)
  wave 0
    ubuntu: Create group if not exists [changed: group minio absent, will create]
        $ sudo groupadd minio
    ubuntu: Create user if not exists [changed: user minio absent, will create; home /home/minio missing, will create]
        $ sudo useradd -d /home/minio -g minio -m minio
        $ sudo mkdir -p /home/minio
        $ sudo chown minio:minio /home/minio
    ubuntu: Create dirs. /data/minio/bin [changed: /data/minio/bin missing, will create]
        $ sudo mkdir -p /data/minio/bin
        $ sudo chmod 755 /data/minio/bin
        $ sudo chown minio:minio /data/minio/bin
    …      (the cfg/data/log/backup dirs, same three commands each; node2 pairs every line above and below)
    ubuntu: Spool minio.tar.gz       [changed: content /tmp/minio.tar.gz is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-…/content
        $ sudo sh -c 'mkdir -p /tmp && cp … /tmp/minio.tar.gz && chmod 644 /tmp/minio.tar.gz && rm -rf …'
    ubuntu: Unpack minio.tar.gz      [changed: will extract /tmp/minio.tar.gz into /data/minio/bin; will set ownership of /data/minio/bin recursively; will set mode 755 of /data/minio/bin recursively]
        $ sudo tar -xz -f /tmp/minio.tar.gz -C /data/minio/bin
        $ sudo chown -R minio:minio /data/minio/bin
        $ sudo chmod -R 755 /data/minio/bin
    ubuntu: Add environment file     [changed: content /etc/default/minio is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-…/content
        $ sudo sh -c 'mkdir -p /etc/default && cp … /etc/default/minio && chown root:root … && chmod 644 … && rm -rf …'
    ubuntu: Add systemd file         [changed: content /etc/systemd/system/minio.service is absent, will install]
        …
    ubuntu: Start minio service      [changed: daemon-reload; minio.service not running (None), will start]
        $ sudo systemctl daemon-reload
        $ sudo systemctl start minio.service
    barrier: install done → 2 hosts notified
    barrier: install done → 2 hosts notified
  wave 1
    ubuntu: Restart minio service (handler) [changed: minio.service already running; minio.service will restart]
        $ sudo systemctl restart minio.service
    ubuntu: Waiting for minio console port 9001 [changed: wait for tcp port 9001 to be listening]
        $ sudo wait for Port(9001, 'tcp') <is_listening> every 1.0s up to 60s
    barrier: Add minio config → {'exit_status': 0, 'stdout': 'Done.\n', 'stderr': ''}
  wave 2
    barrier: Create bucket bucket01 → {'exit_status': 0, 'stdout': 'Done.\n', 'stderr': ''}
  wave 3
    barrier: Create bucket bucket02 → {'exit_status': 0, 'stdout': 'Done.\n', 'stderr': ''}
  wave 4
    ubuntu: Copy policy file getput_policy.json [changed: content /tmp/getput_policy.json is absent, will install]
        …
    barrier: Create policy getput → {'exit_status': 0, 'stdout': 'Done.\n', 'stderr': ''}
  wave 5
    barrier: Create user miniouser → {'exit_status': 0, 'stdout': 'Done.\n', 'stderr': ''}
  wave 6
    barrier: Add synchronize config → {'exit_status': 0, 'stdout': 'Done.\n', 'stderr': ''}
  wave 7
    ubuntu: Render systemd file minio_sh_bucket01_to_wx_bucket03.service [changed: content /etc/systemd/system/minio_sh_bucket01_to_wx_bucket03.service is absent, will install]
        …
    ubuntu: Start synchronize service minio_sh_bucket01_to_wx_bucket03.service [changed: daemon-reload; minio_sh_bucket01_to_wx_bucket03.service not running (None), will start]
        $ sudo systemctl daemon-reload
        $ sudo systemctl start minio_sh_bucket01_to_wx_bucket03.service
```

Only `mc` barrier lines print a raw `Result` dict, and the `sudo ` prefix on
them is the elevated Shell action's own — the `sudo -u minio -H` inside the
script is D6's embedding. Note what the D4 gate already did: `node2` paired
every request line of waves 0 and 1, but none of the `mc` barriers belong to
it — its pass is two waves, `0` and `1`, and nothing else.

The rerun — every request converges on its own store, the checkpoint notifies
nobody, and **the handler wave disappears**; the six `mc` acts still run:

```text
(root)
  wave 0
    ubuntu: Create group if not exists [no change: group minio already present]
    ubuntu: Create user if not exists [no change: user minio already as requested; directory /home/minio already exists]
    …      (all ten install/configure requests no-change; node2 again)
    ubuntu: Unpack minio.tar.gz      [no change: archive already extracted (/data/minio/bin/minio exists)]
    ubuntu: Start minio service      [no change: minio.service already running]
    barrier: install done → 0 hosts notified
    barrier: install done → 0 hosts notified
  wave 1
    barrier: Add minio config → {'exit_status': 0, 'stdout': 'Done.\n', 'stderr': ''}
  …        (waves 2–6: the other five mc acts, unchanged from the first pass)
  wave 7
    ubuntu: Render systemd file minio_sh_bucket01_to_wx_bucket03.service [no change: content already up to date (…)]
    ubuntu: Start synchronize service minio_sh_bucket01_to_wx_bucket03.service [no change: … already running]
```

The RedHat host appears in no tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | A host outside the `"Ubuntu"` family emits **nothing at all** | the family guard is the body's opening `if`, not a `when=` on each request; the guard still returns `MinioOutputs` (empty strings), because a composing role reads a return value, not a log |
| D2 | `tags: never` opt-ins become **vars gates** | `buckets`, `users` and `sync` default to empty, so a plain deploy is exactly the role's default install + start path; the `uninstall`/`stop`/`restart` tag branches are scoped out |
| D3 | `play_hosts` / `hostvars[host].ansible_host` become **`node_address`** | a per-host body has no play to enumerate; the env template's endpoint loop walks the `VarsSet`'s `node_address` values in sorted order, falling back to each host's `connection` address |
| D4 | `run_once` becomes the per-host **`primary`** variable | bucket ops run only on the primary in cluster mode (and per host standalone, as the role branches); user and sync ops always only on the primary — a non-primary host runs waves 0–1 and nothing else |
| D5 | The bundled 44 MB `files/minio.tar.gz` becomes **`package_url` / `package_path`** | a library role ships no blobs; `package_url` lets `files.unarchive` fetch control-side, `package_path` reads the controller file and spools it to `/tmp/minio.tar.gz` before the `creates`-gated extraction (replacing the version means removing `<install_dir>/minio` first) |
| D6 | `become_user: minio` has no per-request analogue | every `mc` command embeds `sudo -u minio -H` inside one elevated `act(Shell, sudo=True)` — the composer/nodejs precedent; run-as identity from the operation, elevation from the inventory |
| D7 | `wait_for`'s `delay: 5` is dropped | `server.wait` polls from once, with the role's `timeout: 60` kept |
| D8 | The chained handlers collapse into **one handler wave** | restart, then the port wait, sequential; the single checkpoint report is read twice (`NOTIFYING`, then `Start minio service`) because the start task notifies the wait directly |
| D9 | The `mc` shells **re-run on every pass** | an `act` result is data, exactly like the role's non-idempotent `shell` tasks — including the `mb | true` gate and the `"already in effect"` tolerance on the user attach |
| D10 | `Create ansible remote_tmp dir` is dropped | `/home/minio/.ansible/tmp` is Ansible's own plumbing, not MinIO state |
| D11 | The user's hardcoded `home: /home/minio` becomes `/home/<sys_user>` | renaming the service user stays coherent |
| D12 | The templates' default `mode: 0644` is carried **explicitly** | `files.install` does not imply a mode; the two template installs (and the spool/policy installs) name `mode="0644"` so the rendered files land exactly as Ansible left them |

## 9. Scoped out

* **Non-Ubuntu families.** The role ships no OS dispatch at all (its
  `vars/main.yml` is empty), so its defaults are simply the frozen constants —
  but SUSE/RHEL installs would be a different role's job, and a RedHat host is
  skipped, not mapped.
* **The `uninstall`, `stop` and `restart` tag branches.** `tasks/main.yml`
  hangs them on `tags: never` beside the three features; only the feature
  halves are migrated (D2). Stopping and removing MinIO is a caller's own
  `systemd.service(running=False)` and `files.file(present=False)` program.
* **Shipping the tarball.** See D5 — the example and the tests take a package
  source as a variable. Replacing an installed version is likewise manual:
  remove `<install_dir>/minio` so the `creates` gate re-extracts.
* **Play-order fidelity.** `play_hosts` order has no analogue in a per-host
  body; the endpoint list is sorted (D3). MinIO does not care about endpoint
  order in `MINIO_OPTS`, so the difference is cosmetic.
* **Per-task privilege.** `become: true` is everywhere in the role and lands
  as `sudo=True` everywhere here; the run-as user for `mc` comes from the
  command itself (D6), and passwords/privilege views from the inventory.

--->
