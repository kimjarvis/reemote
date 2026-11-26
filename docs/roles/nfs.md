# nfs — NFS export server role

| | |
| --- | --- |
| Role | [`geerlingguy.nfs`](https://github.com/geerlingguy/ansible-role-nfs) |
| Module | [`reemote.roles.nfs`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/nfs.py) |
| Runner | [`examples/roles/nfs.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/nfs.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `nfs` role mapping → `NfsVars` |

<!---
| Waves | `2` on a changing pass (requests, then the handler command), `1` once converged — one checkpoint, one `act`, no facts |
| Outputs | none — the deploy returns `None`; no other role reads nfs's results |
| Extra deps | none — `/etc/exports` is rendered by a Python join, so no `reemote[templates]` |
| Offline test | [`tests/roles/test_nfs.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_nfs.py) |
| Factory | plain (non-`async`) — the runner calls `nfs(v)`; the render is control-side Python inside the body, not a barrier |

`nfs` installs `nfs-common` and `nfs-kernel-server` on Ubuntu hosts, creates the
directories named by `nfs_exports`, writes `/etc/exports` from the role's
template (root:root, mode `0644`), starts and enables the service when any
export is configured, and re-exports the filesystems — the role's one handler,
`reload nfs: command: 'exportfs -ra'` — whenever the exports file changes.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with `yield from scoped("nfs", nfs(v))(host)`
and a run is one `run_waves` pass. The Ansible role's whole task list *and* its
handler are that single deploy, and its `defaults/main.yml` is `NfsVars`.

This is the corpus's **command-handler role** (design doc §18.14): the only
migrated role whose handler is a bare command rather than a service action, so
the handler is an `act` barrier and its wave holds a barrier line instead of a
request line. The old example needed two phases for it — one `run()`, then
`changed_hosts` on its report, then `gather_action(Shell, …)` *outside* any
deploy, because an action cannot be yielded from a `run()` body. Under
`run_waves` both reads are in-stream:

```python
yield from configure(nfs_plan(hv))(host)   # wave 0 — the requests
wave_report = yield checkpoint("exports done")
notified = changed_hosts(wave_report, NOTIFYING)
yield from handler(notified)(host)         # wave 1 — exportfs -ra, or nothing
```

## 1. What it is, and where it came from

The role's tasks — and its handler — map onto reemote requests and two barriers:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `include_vars: "{{ ansible_facts.os_family }}.yml"` | frozen `vars/Debian.yml` / `setup-Debian.yml` constants (`NFS_SERVER_DAEMON`, `NFS_PACKAGES`); the single-OS collapse needs no gather | recipe |
| `include_vars: Fedora.yml` when RedHat+Fedora | scoped out (§9) | — |
| `include_tasks: setup-RedHat.yml` when RedHat | scoped out (§9) | — |
| `include_tasks: setup-Debian.yml` when Debian | the Ubuntu path's install request (below) | covered |
| `apt: name=[nfs-common, nfs-kernel-server] state=present` | `apt.packages(packages=NFS_PACKAGES, update=True, cache_valid_time=600)` (+ folded molecule refresh, D1) | covered |
| `file: path={{ item }} state=directory` over `nfs_exports \| map('split') \| map('first') \| unique` | `files.directory(path=dir)` per unique directory, mode deliberately unset (the role's `ansible-lint` noqa 208 exemption): create when absent, leave existing permissions untouched | covered |
| `template: exports.j2 dest=/etc/exports owner=root group=root mode=0644` `notify: reload nfs` | `render_exports` join → `files.install(mode="0644", user="root", group="root")` + `files.file` metadata companion; both names notify | recipe |
| `service: name={{ nfs_server_daemon }} state=started enabled=yes` when `nfs_exports\|length > 0` | `systemd.service(service=NFS_SERVER_DAEMON, running=True, enabled=True)`, emitted only when the frozen gate is true | covered |
| `handlers/main.yml: reload nfs` (`command: 'exportfs -ra'`) | `checkpoint("exports done")` → `changed_hosts(…, NOTIFYING)` → `act(Shell, command="exportfs -ra", sudo=True)`, with the body raising `CommandError` on a non-zero exit | barrier |

No library gap blocked the mapping — every task and the handler had a faithful
target, so the task audit filed no tickets. The role reads nothing mid-play, so
the only barriers are the notification read and the handler command itself.
One live-run finding is worth naming because it sits in this role's neighbourhood
without changing its shape: `files.install`'s elevated content read-back of a
file the login user cannot read elevates only the `test`, so its `cat` still runs
unprivileged and a restrictive mode makes the content request report a change on
every run ([issue #69](https://github.com/kimjarvis/reemote/issues/69)). The role
manages `/etc/exports` at `0644`, so its converged path — the one the transcripts
below show — never takes that branch.

## 2. Install

```bash
pip install reemote   # or, in a checkout: uv sync
```

**No extra dependency.** The role's one template is a ten-line static comment
header plus `{% for export in nfs_exports %}{{ export }}{% endfor %}`, which a
plain Python join reproduces byte-for-byte (verified against Jinja2 with
Ansible's `trim_blocks=True`, `lstrip_blocks=False`,
`keep_trailing_newline=True` environment), so neither `jinja2` nor `pyyaml` is
imported and the `reemote[templates]` extra is not needed — unlike memcached or
apache. Nothing is read from a template file at run time either: the render
happens inside the deploy body, control-side.

SSH access to each Ubuntu host with the key named in its `connection`, and
passwordless sudo (or a `privilege` section in the inventory): every request
carries `sudo=True`, because reemote has no play-level `become` (the role's
molecule playbook leaves `become` commented out). The exports write is a
spool-and-install (`files.install`), so the transfer itself never elevates.
--->

::: reemote.roles.nfs
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - nfs

**Variables:**

### Role variables (`defaults/main.yml`)

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `exports` | `tuple[str, …]` | `()` — one line per export, each beginning with the directory to export, e.g. `"/srv/nfs 192.0.2.0/24(rw,sync,no_subtree_check)"`. The directories are created (mode untouched), the lines are rendered into `/etc/exports`, and a non-empty list starts and enables `nfs-kernel-server` | `nfs_exports` |
| `rpcbind_state` | `str` | `started` — **inert**: read only by `setup-RedHat.yml`, which is scoped out (§9); carried so an inventory that sets it still binds | `nfs_rpcbind_state` |
| `rpcbind_enabled` | `bool` | `true` — **inert**, the same RedHat-only rpcbind gate | `nfs_rpcbind_enabled` |

<!---
### Precedence and validation

Precedence is `model defaults < inventory role mapping < overrides`. Validation is
fail-fast at `resolve_vars`, before any host is contacted: an entry that is not
a string, or one whose first whitespace-delimited token is empty, is rejected —
Ansible's `map('first')` turns the same value into `Undefined` and fails the
`file` task instead (D6). An `exports` list written as JSON binds to a
`tuple`, because every value that reaches `op()` has to hash (§8.1). A key
naming no field in the role mapping, such as `exportss`, raises `RoleVarsError`
naming the valid keys.

## 5. Run it

The role is library code; `examples/roles/nfs.py` is the runnable artifact — a
standalone program with its own `main()`, no shared runner. It loads and
validates `inventory.json` from the current working directory, resolves the
role's variables (the model defaults overlaid with the entry's `nfs` role
mapping), builds the deploy from the `nfs` factory and drives it with `run_waves` —
a `dry=True` preview, then the real apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import NfsVars, nfs

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(NfsVars, "nfs", inventory)          # no overrides
    deploy = nfs(vars_set)                                       # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)   # parks at the checkpoint
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)             # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/nfs.py
```

The runner passes no call-site `overrides`, so the role's own default applies: an
empty `exports` writes `/etc/exports` with nothing but the template's comment
header and leaves the service task skipped, exactly as the role does with no
variables. To demonstrate the export path, add the key to the inventory entry's
`nfs` role mapping — reemote's `host_vars` mechanism:

```json
{
  "host": "ubuntu",
  "family": "Ubuntu",
  "connection": {"address": "192.168.0.181", "username": "supervisor",
                 "key_path": "~/.ssh/id_ed25519"},
  "group": "ubuntu",
  "roles": {"nfs": {"exports": ["/srv/nfs 192.0.2.0/24(rw,sync,no_subtree_check)"]}}
}
```

The `dry=True` pass is *not* empty: the role's first items are requests, so the
dry run plans the whole configure wave and then parks on the checkpoint barrier
(`barrier: exports done → unknown until run`) — the notification read needs a
real apply report, so the handler command stays unplanned, exactly like
Ansible's `--check` skipping handlers. Nothing is applied.

**Run the program twice.** The second run must report no change: the apt cache
stamp is fresh, both packages are installed, `/etc/exports` is byte-identical
with root:root `0644` metadata — and the checkpoint notifies nobody, so the
handler wave never appears and `exportfs` is not re-run. That is the
migration's idempotence claim, and the offline suite asserts it.

## 6. What it does

One deploy. The checkpoint closes the request wave, and a wave with no records
is not appended — so the shape below is what the test pins:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `configure`: `apt.packages(NFS_PACKAGES, update=True, cache_valid_time=600)`, one `files.directory(dir)` per unique export path, `files.install(/etc/exports, mode="0644")` + `files.file(mode="0644")` companion, `systemd.service(running=True, enabled=True)` when exports exist — then `checkpoint("exports done")` | every input is a resolved variable or a frozen constant, so the whole task list is one pure request wave; the checkpoint reads its outcome |
| 1 | `handler`: `act(Shell, command="exportfs -ra", name="reload nfs", sudo=True)` — only on the hosts the checkpoint selected | the role's one handler is a **command**; an empty notification set yields no barrier, so a converged pass has no wave 1 at all |

Five rules are load-bearing:

* **The handler is an `act` barrier, not a handler factory.**
  `handlers/main.yml` is `command: 'exportfs -ra'`. `reload_handler` and
  `service_handler(action="reloaded")` would be *unfaithful*:
  `systemd.service(reloaded=True)` acts only on an already-running unit, and a
  stopped one is *started* instead (the default `running=True`) with the reload
  verb skipped. On the default path (empty `exports`) the role's service task
  is skipped, so a service handler would start `nfs-kernel-server` that the play
  deliberately left stopped and still never run `exportfs`. The command runs
  regardless of the unit's state, exactly as the role wrote it.
* **A barrier result is data, so the role raises its own error.** A non-zero
  `exit_status` never raises; `handler` checks it and re-raises the typed
  `CommandError` naming the host, the command and stderr. A silently
  un-reloaded `exportfs` — an `/etc/exports` that disagrees with the kernel's
  export table — is the one failure this role cannot tolerate.
* **The request waves stay `{0}` on every pass.** This is the signature that
  separates nfs from every service-handler role: the handler wave carries a
  `barrier: reload nfs → {'exit_status': 0, …}` line and *no* request line. The
  test asserts `_request_waves(report) == {0}` alongside the two-wave shape.
* **The checkpoint is the only report read, and it must come first.** A
  checkpoint only sees the wave it closes (§4.3), so it sits immediately after
  `configure`; the handler's own barrier wave has nothing to report and needs no
  second checkpoint. Unnotified hosts simply do not yield the `act`, which is
  legal (§10.3) — the two-Ubuntu-host test pins that asymmetry.
* **The dry preview over-reports the metadata companion.** Planning reads
  pre-state, so a host with no `/etc/exports` yet shows
  `Set exports file mode and owner [changed: /etc/exports missing, will create…]`
  in the dry tree, while the apply shows it as a no-op — `files.install` created
  the file *and* its metadata earlier in the same wave. The companion exists for
  drift on an already-converged file (D3), not for a first-pass double write.

A host whose `family` is not `"Ubuntu"` returns before its first `yield`: no
requests, no barriers, no report lines — Alpine and RedHat entries in a shared
inventory are never contacted.

## 7. Transcript

From `tests/roles/test_nfs.py`, against the simulated Ubuntu host plus a RedHat
host the role never touches. No between-pass bridge is needed — the simulation's
own mutators maintain every seam the second pass reads. Default path
(`exports` at its empty model default), dry preview first:

```text
(root)
  wave 0
    ubuntu: Ensure NFS utilities are installed [changed: apt update; 2 package(s) to install]
    ubuntu: Copy exports file        [changed: content /etc/exports is absent, will install]
    ubuntu: Set exports file mode and owner [changed: /etc/exports missing, will create; parent /etc missing, will create]
    barrier: exports done → unknown until run
```

Then the apply — the exports write lands, the checkpoint sees one changed
notifying request, and wave 1 holds the handler *command*:

```text
(root)
  wave 0
    ubuntu: Ensure NFS utilities are installed [changed: apt update; 2 package(s) to install]
        $ sudo apt-get update
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install nfs-common nfs-kernel-server
    ubuntu: Copy exports file        [changed: content /etc/exports is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-f42846992f622b1f/content
        $ sudo sh -c 'mkdir -p /etc && cp /tmp/reemote-install-f42846992f622b1f/content /etc/exports && chown root:root /etc/exports && chmod 644 /etc/exports && rm -rf /tmp/reemote-install-f42846992f622b1f'
    ubuntu: Set exports file mode and owner [no change: file /etc/exports already exists]
    barrier: exports done → 1 host notified
  wave 1
    barrier: reload nfs → {'exit_status': 0, 'stdout': '', 'stderr': ''}
```

The rerun — the checkpoint re-runs (it reads the wave, not a cached plan), every
request is a no-op, and **the handler wave disappears** (`exportfs` ran exactly
once across both passes):

```text
(root)
  wave 0
    ubuntu: Ensure NFS utilities are installed [no change: apt update skipped, cache is fresh; all packages present]
    ubuntu: Copy exports file        [no change: content already up to date (/etc/exports)]
    ubuntu: Set exports file mode and owner [no change: file /etc/exports already exists]
    barrier: exports done → 0 hosts notified
```

With `exports` configured, the same single wave additionally carries the
deduplicated directory loop (`/srv/nfs` appears twice in the exports, once in
the loop) and the gated service request — and the handler still fires once:

```text
(root)
  wave 0
    ubuntu: Ensure NFS utilities are installed [changed: apt update; 2 package(s) to install]
        $ sudo apt-get update
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install nfs-common nfs-kernel-server
    ubuntu: Ensure export directory /srv/nfs exists [changed: /srv/nfs missing, will create]
        $ sudo mkdir -p /srv/nfs
    ubuntu: Ensure export directory /srv/nfs/projects exists [changed: /srv/nfs/projects missing, will create]
        $ sudo mkdir -p /srv/nfs/projects
    ubuntu: Copy exports file        [changed: content /etc/exports is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-f42846992f622b1f/content
        $ sudo sh -c 'mkdir -p /etc && cp /tmp/reemote-install-f42846992f622b1f/content /etc/exports && chown root:root /etc/exports && chmod 644 /etc/exports && rm -rf /tmp/reemote-install-f42846992f622b1f'
    ubuntu: Set exports file mode and owner [no change: file /etc/exports already exists]
    ubuntu: Ensure nfs is running    [changed: nfs-kernel-server.service not running (None), will start; nfs-kernel-server.service not enabled, will enable]
        $ sudo systemctl start nfs-kernel-server.service
        $ sudo systemctl enable nfs-kernel-server.service
    barrier: exports done → 1 host notified
  wave 1
    barrier: reload nfs → {'exit_status': 0, 'stdout': '', 'stderr': ''}
```

On the live Ubuntu demo host the role met a machine that had already been through
the play: `nfs-common` and `nfs-kernel-server` installed, `nfs-kernel-server`
active, and an `/etc/exports` **byte-identical** to the role's default-path render
(`390` bytes, md5 `f491b2bc2943df61fab9a6bb835755fd` on both sides) — the template
reproduces Ubuntu's stock file, so D2 holds on a real host and the content request
no-ops. The first run's only work was the folded cache refresh (D1), and the
handler wave stayed absent because no *notifying* request changed:

```text
(root)
  wave 0
    ubuntu: Ensure NFS utilities are installed [changed: apt update; all packages present]
        $ sudo apt-get update
    ubuntu: Copy exports file        [no change: content already up to date (/etc/exports)]
    ubuntu: Set exports file mode and owner [no change: file /etc/exports already exists]
    barrier: exports done → 1 host notified
```

The second run of the same program — the idempotence claim, exit status `0`:

```text
(root)
  wave 0
    ubuntu: Ensure NFS utilities are installed [no change: apt update skipped, cache is fresh; all packages present]
    ubuntu: Copy exports file        [no change: content already up to date (/etc/exports)]
    ubuntu: Set exports file mode and owner [no change: file /etc/exports already exists]
    barrier: exports done → 0 hosts notified
```

That first tree reads `1 host notified` and still has no wave 1, and both halves
are correct: the barrier line counts every changed entry in the wave the
checkpoint closed — the driver cannot know `NOTIFYING` — while `changed_hosts`
filters on the names, so the apt refresh shows in the display and not in the
notification.

Forcing the drift the metadata companion exists for (`sudo chmod 664
/etc/exports`) is what puts the command handler on a real host: the content
request no-ops, the companion converges `0644` alone, and wave 1 carries the
barrier line rather than a request line:

```text
(root)
  wave 0
    ubuntu: Ensure NFS utilities are installed [no change: apt update skipped, cache is fresh; all packages present]
    ubuntu: Copy exports file        [no change: content already up to date (/etc/exports)]
    ubuntu: Set exports file mode and owner [changed: mode 664 differs, will chmod 644]
        $ sudo chmod 644 /etc/exports
    barrier: exports done → 1 host notified
  wave 1
    barrier: reload nfs → {'exit_status': 0, 'stdout': '', 'stderr': ''}
```

The run left `/etc/exports` at `644 root:root` with the md5 it was found with, and
`exportfs -v` empty, so `exportfs -ra` re-read a header-only file and exported
nothing — which is what the role's own defaults say.

The RedHat host appears in no tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | The molecule `pre_task`'s apt index refresh is folded into the install request | the role's own `apt` task carries no `update_cache`, but `molecule/default/converge.yml` refreshes with `update_cache=yes cache_valid_time=600 changed_when=false` before the role runs; folding it (`apt.packages(update=True, cache_valid_time=600)`) keeps the role self-contained on a fresh host. The refresh is gated on the `AptUpdateStamp` fact, so a converged host with a fresh cache yields no update command. Two nuances: the stamp age is computed against the *controller's* UTC clock (reemote's documented approximation of Ansible's target-side check), and a stale-cache refresh reports `changed=True` on the install request where the pre_task forced `changed_when: false` — the refresh itself is identical |
| D2 | The template renders with a Python join, not Jinja2 | the body is a ten-line static comment header plus one `{{ export }}` per loop iteration; with Ansible's `trim_blocks=True` the block tags' own newlines vanish, so the join is byte-for-byte the same text (verified against a Jinja2 environment reproducing Ansible's whitespace settings) — and the role then needs no template file, no `templates` extra and no control-side dependency |
| D3 | The template task becomes **two** notifying requests | `files.install`'s idempotence is content-only, so a `0644`/owner drift on unchanged content would never converge; the paired `files.file` re-applies the metadata, and because Ansible's template module reports a change on owner/group/mode drift its name joins `NOTIFYING` (the apache migration's `ports.conf` precedent) |
| D4 | The handler is an `act` barrier that raises its own `CommandError` — no `reload_handler` | the handler is `command: 'exportfs -ra'`, so no `reemote.handlers` factory covers it, and a service handler would be unfaithful on the default path (§6). Under `run_waves` the command is in-stream rather than a post-run `gather_action`, which is what deleted the old example's two-phase split; a non-zero exit is data, so the body turns it into the typed error |
| D5 | The RedHat/Fedora path is **scoped out**, and its two variables are carried inert | a Phase 0 single-OS decision (§9). `nfs_rpcbind_state` and `nfs_rpcbind_enabled` are fields on `NfsVars` so an existing inventory still binds under `extra="forbid"`, but they change no request |
| D6 | Type gates move to `NfsVars`, and the family guard to the body's opening `if` | a wrongly typed or blank export line is a pydantic rejection at `resolve_vars` time, before any host is contacted, rather than Ansible's `Undefined` failing the `file` task mid-play; an off-target host emits nothing at all, no `when:` and no `[skipped]` lines |

## 9. Scoped out

* **RedHat / CentOS / Fedora branches** (`tasks/setup-RedHat.yml`,
  `vars/RedHat.yml`, `vars/Fedora.yml`, the Fedora override include, and the
  `nfs_rpcbind_state` / `nfs_rpcbind_enabled` defaults only they consume): a
  Phase 0 single-OS decision — the target is Ubuntu. reemote's `dnf` and
  `systemd` modules cover the package and service operations those branches
  use; they are simply outside the agreed target.
--->
