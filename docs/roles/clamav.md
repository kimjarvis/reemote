# clamav — ClamAV antivirus role

| | |
| --- | --- |
| Role | [`geerlingguy.clamav`](https://github.com/geerlingguy/ansible-role-clamav) |
| Module | [`reemote.roles.clamav`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/clamav.py) |
| Example | [`examples/roles/clamav.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/clamav.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `clamav` role mapping → `ClamavVars` |

<!---
| Waves | 4 on a fresh host — 2 on a converged rerun (the freshclam act and the handler wave are conditional) |
| Outputs | `None` — no composing role in the corpus consumes an output |
| Extra deps | none — the two config edits are pure text transforms, not templates |
| Offline test | [`tests/roles/test_clamav.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_clamav.py) |

`clamav` installs and configures ClamAV on Ubuntu hosts: the `clamav` /
`clamav-base` / `clamav-daemon` packages, the post-install `freshclam` run
gated on the install's own change, the two `lineinfile` edits of
`/etc/clamav/clamd.conf` (and the optional `freshclam.conf` ones), both units
started and enabled, and the role's single `restart clamav daemon` handler.

The role is the corpus's clearest **command-task-plus-notify** case: the play
registered the install, ran `freshclam` `when:` it changed, and *that run*
notified the restart handler — a read between steps that is not a request.
The barrier model keeps the whole play in one generator:

```python
yield from install(hv)(host)                       # wave 0
install_report = yield checkpoint("packages installed")

if host.name in changed_hosts(install_report, (INSTALL_NAME,)):
    result = yield act(Shell, command="freshclam", sudo=True)   # wave 1, gated
    ...  # the body interprets the Result as failed_when did

yield from configure(plan)(host)                   # wave 2
wave_report = yield checkpoint("configure done")
notified = set(changed_hosts(wave_report, notifying_names(plan)))
if ran_freshclam:
    notified.add(host.name)                        # the command's own notify
if hv.daemon_state != "stopped":
    yield from handler(plan, frozenset(notified))(host)          # wave 3
```

Two checkpoints and one `act`: the first closes the install wave and feeds the
freshclam gate (the role's `register` + `when: … changed` pair), the second is
the handler notification read, and the act only exists on a host whose
packages changed — so the wave count is host-dependent.

## 1. What it is, and where it came from

Status vocabulary: **covered** (a direct operation), **barrier** (a read the
body parks on), **recipe** (a documented shape change), **deviation** (a
deliberate porting choice).

| Ansible task | reemote request | Status |
| --- | --- | --- |
| `include_tasks: setup-vars.yml` → `include_vars vars/Debian.yml` | the frozen Debian constants at the top of the module; `setup-RedHat.yml` is out of scope | deviation (D1, D7) |
| `set_fact` fallbacks for `clamav_packages` / `clamav_daemon` / `clamav_freshclam_daemon` | `ClamavVars` field defaults (`None` → the Debian trio) | covered |
| `package: name={{ item }}` with `with_items: clamav_packages` | one `apt.packages(packages=("clamav", "clamav-base", "clamav-daemon"))` | covered (D2) |
| `register: clamav_packages_install` + `when: … changed` | `checkpoint("packages installed")` + `changed_hosts` | barrier |
| `command: freshclam` with the `failed_when` list | `act(Shell, command="freshclam", sudo=True)`, the `Result` interpreted in the body | recipe (D3) |
| `notify: restart clamav daemon` on the command task | the body adds the host to the notified set when the act ran | recipe (D3) |
| `lineinfile` loop on `clamd.conf` (`mode: 0644`, `create: yes`, `notify`) | `files.transform` per rule (named — the notification channel) + a paired `files.file` mode request | recipe (D4) |
| `lineinfile` loop on `freshclam.conf`, `when: … is defined` | `files.transform` per rule, no notifier; default `None` skips the loop | recipe (D4) |
| `service: name=clamav-daemon state/enabled`, `when: not ansible_check_mode` | `systemd.service(…, running=…, enabled=…)` | covered (D6) |
| `service: name=clamav-freshclam state/enabled` | the sibling request, same wave | covered (D6) |
| handler `service: state=restarted`, `when: clamav_daemon_state != 'stopped'` | `restart_handler(notified, plan.daemon)`, the `when:` a plain `if` in the body | recipe (D5) |
| `template: clamd-freshclam.service.j2` + `daemon_reload` (RedHat only) | not migrated | deviation (D1) |

No library gap blocked this migration, so none was filed: `apt.packages` is
fact-gated on `AptPackages` (the honest read for the freshclam gate),
`files.transform` reads-modifies-writes the whole file through the spool
family (the `lineinfile` shape), and `systemd.service` converges on the unit's
active/enabled state. Every task on the Ubuntu path maps to a shipped
operation or one of the two recipes above.

## 2. Install

```bash
pip install reemote
```

No extra: the role renders no templates and reads no YAML — the two
`clamd.conf` edits are pure functions in the role module. SSH access to each
Ubuntu host with the key named in its `connection`, and passwordless sudo (or
a `privilege` section in the inventory): every request carries `sudo=True`,
because reemote has no play-level `become`. The gated `freshclam` run downloads
a few hundred megabytes of virus databases into `/var/lib/clamav`, so the host
needs outbound HTTPS to the ClamAV CDN.

-->

::: reemote.roles.clamav
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - clamav

**Variables:**

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `packages` | `tuple[str, ...] \| None` | `None` → `("clamav", "clamav-base", "clamav-daemon")` | `clamav_packages` |
| `daemon` | `str` | `clamav-daemon` | `clamav_daemon` |
| `freshclam_daemon` | `str` | `clamav-freshclam` | `clamav_freshclam_daemon` |
| `daemon_state` | `"started" \| "stopped" \| "restarted" \| "reloaded"` | `started` | `clamav_daemon_state` |
| `daemon_enabled` | `bool` | `true` | `clamav_daemon_enabled` |
| `freshclam_daemon_state` | same union | `started` | `clamav_freshclam_daemon_state` |
| `freshclam_daemon_enabled` | `bool` | `true` | `clamav_freshclam_daemon_enabled` |
| `daemon_configuration_changes` | `tuple[ConfigChange, ...]` | the role's two default rules | `clamav_daemon_configuration_changes` |
| `freshclam_configuration_changes` | `tuple[ConfigChange, ...] \| None` | `None` — undefined upstream, so the loop is skipped | `clamav_freshclam_configuration_changes` |

<!---

Each `daemon_configuration_changes` item is a `ConfigChange(regexp, line="",
state="present")` — one `lineinfile` item, with the role's own defaults
(`item.line | default('')`, `item.state | default('present')`). The two
default rules drop the `Example` line (`state: "absent"`) and set
`LocalSocket /var/run/clamav/clamd.ctl`; overriding the tuple replaces them
wholesale, as in Ansible. Precedence is `model defaults < inventory role
mapping < overrides`, and sequences are `tuple`, never `list` — the models are
frozen and hashable, and a value that reaches `op()` must hash. The config
*paths* are not fields at all: the upstream README states they are
"hardcoded specifically for each OS family and cannot be overridden" (D7).

## 5. Run it

The role is library code; `examples/roles/clamav.py` is the runnable
artifact — a standalone program with its own `main()`, no shared runner. It
loads and validates `inventory.json` from the current working directory,
resolves the role's variables (the defaults overlaid with the entry's `clamav`
role mapping), builds the deploy from the `clamav` factory and drives it with
`run_waves` — a `dry=True` one-wave preview, then the real apply — printing
the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import ClamavVars, clamav

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(ClamavVars, "clamav", inventory)
    deploy = clamav(vars_set)
    report, _hosts = await run_waves(inventory, deploy, dry=True)   # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)             # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/clamav.py
```

The shipped `inventory.json` carries no `clamav` role mapping, so the model
defaults decide the site: the Debian package trio, both units started and
enabled, and the role's own two default `clamd.conf` rules. `main()` prints
the wave tree and maps the role's failure modes (bad inventory,
`ValueError`/`TypeError` from a rejected variable, `CommandError` — including
the role's freshclam gate, whose failure aborts exactly as the play's did —
`HostConnectionError`, `PrivilegeError`, `OSError`) to a one-line stderr
message and exit 1.

**Run the program twice.** The second run must report no change: the packages
are installed (so the freshclam act is never built), the `Example` line is
gone from `clamd.conf`, the socket directive is set, and both units are
running and enabled — no notification, no handler wave. That is the
migration's idempotence claim, and the offline suite asserts it.

> **Shared demo host.** The `ubuntu` demo host also runs the other role
> examples. This role leaves `clamav-daemon` listening on the
> `/var/run/clamav/clamd.ctl` unix socket (no TCP port is opened — the
> package default) and `clamav-freshclam` updating the databases a few times a
> day. To stop and disable both units again after a real run:
>
> ```bash
> ssh <user>@<ubuntu-host> 'sudo systemctl disable --now clamav-daemon clamav-freshclam'
> ```

## 6. What it does

One deploy. Each barrier closes its own wave, and a wave with no records is
not appended — so the wave count is **host-dependent**, which is the point of
the barrier model:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `install`: one `apt.packages` request for the trio — closed by `checkpoint("packages installed")` | the checkpoint **is** the role's `register: clamav_packages_install`: it reads the wave it just closed and names the hosts whose install changed |
| 1 | the `freshclam` `act` — only on a host the checkpoint named | a barrier never raises on a non-zero exit; the body interprets the `Result` exactly as the play's `failed_when` did, then queues the handler itself (D3) |
| 2 | `configure`: the `clamd.conf` transforms and their mode companion, the optional `freshclam.conf` transforms, both unit states — closed by `checkpoint("configure done")` | the second checkpoint is the handler notification read: `changed_hosts` over the *configure* wave, filtered to the notifying names |
| 3 | `handler`: `Restart clamav-daemon (handler)` — only on notified hosts and only while `daemon_state` is not `"stopped"` | the role's handler `when:` is a static-variable gate, so it lives in the body, not on a request (D5) |

Two ordering rules are load-bearing:

* **The freshclam gate reads the install wave, the handler reads the
  configure wave** — two checkpoints, each immediately after the stage it
  reports on (the barrier-placement rule). A converged host skips wave 1 and
  nobody is notified at wave 2, so the rerun is two waves.
* **Requests do not close waves.** The transforms, the mode companion and the
  two service tasks are all wave 2, applied per host in body order — the
  role's task order, edits before service states.

A host whose `host.family` is not `"Ubuntu"` returns before its first `yield`,
so it emits no requests, no barriers and no report lines at all.

## 7. Transcript

From `tests/roles/test_clamav.py::test_tree_snapshot`, against the simulated
Ubuntu host plus a RedHat host the role never touches. Nothing is installed
yet. Fresh host, first pass — four waves:

```text
(root)
  wave 0
    ubuntu: Ensure ClamAV packages are installed [changed: 3 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install clamav clamav-base clamav-daemon
    barrier: packages installed → 1 host notified
  wave 1
    barrier: Run freshclam after ClamAV packages change → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 2
    ubuntu: Change configuration for the ClamAV daemon (^.*Example$) [changed: content /etc/clamav/clamd.conf differs, will install]
        $ scp <files install> -> /tmp/reemote-install-ab07f171429272b1/content
        $ sudo sh -c 'mkdir -p /etc/clamav && cp /tmp/reemote-install-ab07f171429272b1/content /etc/clamav/clamd.conf && chmod 644 /etc/clamav/clamd.conf && rm -rf /tmp/reemote-install-ab07f171429272b1'
    ubuntu: Change configuration for the ClamAV daemon (^.*LocalSocket .*$) [changed: content /etc/clamav/clamd.conf differs, will install]
        $ scp <files install> -> /tmp/reemote-install-ab07f171429272b1/content
        $ sudo sh -c 'mkdir -p /etc/clamav && cp /tmp/reemote-install-ab07f171429272b1/content /etc/clamav/clamd.conf && chmod 644 /etc/clamav/clamd.conf && rm -rf /tmp/reemote-install-ab07f171429272b1'
    ubuntu: Set ClamAV daemon configuration mode [no change: file /etc/clamav/clamd.conf already exists]
    ubuntu: Ensure ClamAV daemon is running (if configured) [changed: clamav-daemon.service not running (None), will start; clamav-daemon.service not enabled, will enable]
        $ sudo systemctl start clamav-daemon.service
        $ sudo systemctl enable clamav-daemon.service
    ubuntu: Ensure ClamAV freshclam daemon is running (if configured) [changed: clamav-freshclam.service not running (None), will start; clamav-freshclam.service not enabled, will enable]
        $ sudo systemctl start clamav-freshclam.service
        $ sudo systemctl enable clamav-freshclam.service
    barrier: configure done → 1 host notified
  wave 3
    ubuntu: Restart clamav-daemon (handler) [changed: clamav-daemon.service already running; clamav-daemon.service will restart]
        $ sudo systemctl restart clamav-daemon.service
```

The wave 1 line is the `act` barrier's result printed as data — the freshclam
run only exists because wave 0 changed, and its success is what queues the
restart alongside the two config edits. The rerun — the act disappears (the
install changed nobody), the checkpoint names nobody, and **the handler wave
displays as absent**:

```text
(root)
  wave 0
    ubuntu: Ensure ClamAV packages are installed [no change: all packages present]
    barrier: packages installed → 0 hosts notified
  wave 1
    ubuntu: Change configuration for the ClamAV daemon (^.*Example$) [no change: content already up to date (/etc/clamav/clamd.conf)]
    ubuntu: Change configuration for the ClamAV daemon (^.*LocalSocket .*$) [no change: content already up to date (/etc/clamav/clamd.conf)]
    ubuntu: Set ClamAV daemon configuration mode [no change: file /etc/clamav/clamd.conf already exists]
    ubuntu: Ensure ClamAV daemon is running (if configured) [no change: clamav-daemon.service already running; clamav-daemon.service already enabled]
    ubuntu: Ensure ClamAV freshclam daemon is running (if configured) [no change: clamav-freshclam.service already running; clamav-freshclam.service already enabled]
    barrier: configure done → 0 hosts notified
```

The RedHat host appears in no tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | A host outside the `"Ubuntu"` family emits **nothing at all**; `setup-RedHat.yml` and its `clamd-freshclam.service` unit template are not migrated | the family guard is the body's opening `if`, not a `when=` on each request; the `include_vars` OS dispatch collapses to the frozen Debian constants |
| D2 | The `with_items` package loop collapses to **one `apt.packages` request** | the per-package `register.changed` becomes the union — freshclam runs when *any* package installed, the same effective gate for the play that followed the loop |
| D3 | `command: freshclam` is an **`act` barrier whose `Result` the body interprets**, and its run — not a request — queues the handler | a barrier result is data, so the body reproduces the `failed_when` list *literally*: a non-zero exit OR the `locked by another process` stderr message raises the typed `CommandError` (the upstream list is an `any`, inverted from the task comment's intent to tolerate the lock). "It ran" is `changed` for the `command` module, so the body adds the host to the notified set |
| D4 | The `lineinfile` loops become **`files.transform` requests** (pure fixpoint transforms) plus a paired `files.file` mode request | `create: yes` is free (`transform(None)` produces the file), but transform idempotence is content-only — the task's `mode: 0644` therefore rides both, and the mode request is a notifier because the Ansible task would have queued the handler on a mode-only change too |
| D5 | The handler's `when: clamav_daemon_state != 'stopped'` is a plain **`if` in the body** | a static-variable gate needs no request-level `when`; the handler deploy is simply never yielded for a stopped daemon |
| D6 | `when: not ansible_check_mode` on the two service tasks has **no analogue** | a `dry` preview parks on the *first* checkpoint, before the configure wave that holds them is ever built |
| D7 | The path knobs commented out in `defaults/main.yml` are **frozen module constants** | the upstream README states they are "hardcoded specifically for each OS family and cannot be overridden"; the default `LocalSocket` rule interpolates the frozen socket path, so no template engine is needed |

## 9. Scoped out

* **Non-Ubuntu families.** The role's RedHat branch — `setup-RedHat.yml`, the
  EPEL-adjacent package names, the `clamd-freshclam.service` unit template
  and its `daemon_reload` — is not migrated. A RedHat host is skipped, not
  mapped to the Debian layout; those are different roles.
* **The path knobs.** `clamav_daemon_config_path`,
  `clamav_freshclam_daemon_config_path` and `clamav_daemon_localsocket` are
  commented out even upstream and documented as non-overridable; they are
  module constants here (D7). Per-host privilege likewise comes from the
  inventory's `privilege` section, not from an operation.
* **Scanning.** The role installs, configures and runs the daemons; it never
  invokes `clamdscan`/`clamscan`, probes no database currency and defines no
  scheduled scans — so this migration does not either.
* **Check mode.** `ansible_check_mode` semantics end at D6: the one-wave
  `dry` preview is the whole reemote story, and a converged rerun is the
  honest preview (§16.6).

-->
