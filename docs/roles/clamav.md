# clamav — ClamAV antivirus role

| | |
| --- | --- |
| Role | [`geerlingguy.clamav`](https://github.com/geerlingguy/ansible-role-clamav) |
| Module | [`reemote.roles.clamav`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/clamav.py) |
| Example | [`examples/roles/clamav.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/clamav.py) |
| Target | **Ubuntu and RedHat** — hosts in the `"Ubuntu"` or `"RedHat"` OS family |
| Variables | the entry's `clamav` role mapping → `ClamavVars` |

<!---
| Waves | Ubuntu: 4 on a fresh host — 2 on a converged rerun; RedHat: 5 fresh — 3 converged (the freshclam act, the gated reload and the handler wave are conditional) |
| Outputs | `None` — no composing role in the corpus consumes an output |
| Extra deps | none — the two config edits are pure text transforms, and the RedHat unit file carries no Jinja expression at all |
| Offline test | [`tests/roles/test_clamav.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_clamav.py) |

`clamav` installs and configures ClamAV on Ubuntu and RHEL-family hosts: the
family's package set (the `clamav` / `clamav-base` / `clamav-daemon` trio on
Debian, the `clamav` / `clamav-update` / `clamav-scanner-systemd` /
`clamav-data` quartet on RedHat), the post-install `freshclam` run gated on the
install's own change, the two `lineinfile` edits of the family's daemon config
(`/etc/clamav/clamd.conf`, or `/etc/clamd.d/scan.conf`; and the optional
`freshclam.conf` ones), both units started and enabled, and the role's single
`restart clamav daemon` handler. On RedHat the stream also carries
`setup-RedHat.yml`: the `clamd-freshclam.service` unit file and the change-gated
`daemon_reload`/stop behind it.

The role is the corpus's clearest **command-task-plus-notify** case: the play
registered the install, ran `freshclam` `when:` it changed, and *that run*
notified the restart handler — a read between steps that is not a request.
The barrier model keeps the whole play in one generator:

```python
yield from install(plan)(host)                       # wave 0
install_report = yield checkpoint("packages installed")

if host.name in changed_hosts(install_report, (INSTALL_NAME,)):
    result = yield act(Shell, command="freshclam", sudo=True)   # wave 1, gated
    ...  # the body interprets the Result as failed_when did

if host.family == "RedHat":                          # wave 2, RedHat only
    yield from setup_unit(plan)(host)                #   the unit file
    unit_report = yield checkpoint("unit installed")
    if host.name in changed_hosts(unit_report, (UNIT_NAME,)):
        yield from reload_unit(plan)(host)           #   reload + stop, gated

yield from configure(plan)(host)
wave_report = yield checkpoint("configure done")
notified = set(changed_hosts(wave_report, notifying_names(plan)))
if ran_freshclam:
    notified.add(host.name)                        # the command's own notify
if hv.daemon_state != "stopped":
    yield from handler(plan, frozenset(notified))(host)          # the last wave
```

Two checkpoints and one `act` on Ubuntu, three checkpoints on RedHat: the first
closes the install wave and feeds the freshclam gate (the role's `register` +
`when: … changed` pair), the unit checkpoint repeats that trick for
`setup-RedHat.yml`'s `register: freshclam_service_template`, and the last is the
handler notification read. The act only exists on a host whose packages changed
— so the wave count is host-dependent.

## 1. What it is, and where it came from

Status vocabulary: **covered** (a direct operation), **barrier** (a read the
body parks on), **recipe** (a documented shape change), **deviation** (a
deliberate porting choice).

| Ansible task | reemote request | Status |
| --- | --- | --- |
| `include_tasks: setup-vars.yml` → `include_vars vars/Debian.yml` / `vars/RedHat.yml` | the frozen per-family constants at the top of the module (`FAMILIES`), chosen by the inventory family | deviation (D1, D7) |
| `set_fact` fallbacks for `clamav_packages` / `clamav_daemon` / `clamav_freshclam_daemon` | `ClamavVars` fields defaulting to `None` → the family's table in `configure_plan` | covered |
| `package: name={{ item }}` with `with_items: clamav_packages` | one `apt.packages(…)` / `dnf.packages(…)` request for the family's set | covered (D2) |
| `register: clamav_packages_install` + `when: … changed` | `checkpoint("packages installed")` + `changed_hosts` | barrier |
| `command: freshclam` with the `failed_when` list | `act(Shell, command="freshclam", sudo=True)`, the `Result` interpreted in the body | recipe (D3) |
| `notify: restart clamav daemon` on the command task | the body adds the host to the notified set when the act ran | recipe (D3) |
| `include_tasks: setup-RedHat.yml` (`when: os_family == 'RedHat'`) | the `setup_unit` stage plus its `checkpoint("unit installed")`, both inside `if host.family == "RedHat"` | covered (D8) |
| `template: clamd-freshclam.service.j2` + `register: freshclam_service_template` | `files.install(content=FRESHCLAM_UNIT, …)` — the template holds zero Jinja expressions, so it freezes into a string | covered (D8) |
| `systemd: state=stopped daemon_reload=true` (`when: … changed`) | one `systemd.service(running=False, daemon_reload=True)` request, checkpoint-selected; the reload half is fact-gated | covered (D8) |
| `lineinfile` loop on `clamd.conf` (`mode: 0644`, `create: yes`, `notify`) | `files.transform` per rule (named — the notification channel) + a paired `files.file` mode request | recipe (D4) |
| `lineinfile` loop on `freshclam.conf`, `when: … is defined` | `files.transform` per rule, no notifier; default `None` skips the loop | recipe (D4) |
| `service: name=clamav-daemon state/enabled`, `when: not ansible_check_mode` | `systemd.service(…, running=…, enabled=…)` — `clamd@scan` on RedHat | covered (D6) |
| `service: name=clamav-freshclam state/enabled` | the sibling request, same wave — `clamd-freshclam` on RedHat | covered (D6) |
| handler `service: state=restarted`, `when: clamav_daemon_state != 'stopped'` | `restart_handler(notified, plan.daemon)`, the `when:` a plain `if` in the body | recipe (D5) |

No library gap blocked this migration, so none was filed on either path:
`apt.packages` and `dnf.packages` are both fact-gated on their installed-package
read (the honest read for the freshclam gate), `files.transform` and
`files.install` read-modify-write through the spool family (the `lineinfile`
shape, and the unit file), `systemd.service` converges on a template-unit name
like `clamd@scan`, and its `daemon_reload=True` half is gated on
`SystemdNeedDaemonReload` since issues #50 and #75 closed — so `setup-RedHat.yml`
ports without an unconditional `systemctl daemon-reload` workaround.

## 2. Install

```bash
pip install reemote
```

No extra: the role renders no templates and reads no YAML — the two
daemon-config edits are pure functions in the role module, and the RedHat
systemd unit ships upstream as a template with no Jinja expression in it, so it
is a frozen string. SSH access to each Ubuntu or RedHat host with the key named
in its `connection`, and passwordless sudo (or a `privilege` section in the
inventory): every request carries `sudo=True`, because reemote has no play-level
`become`. The gated `freshclam` run downloads a few hundred megabytes of virus
databases into `/var/lib/clamav`, so the host needs outbound HTTPS to the
ClamAV CDN. On RHEL-family hosts the `clamav` / `clamav-scanner-systemd` RPMs
ship from EPEL — compose with the shipped `repo_epel` role or enable the
repository beforehand: upstream's `setup-RedHat.yml` has no repo task either.

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
| `packages` | `tuple[str, ...] \| None` | `None` → the family's set: apt trio / dnf quartet | `clamav_packages` |
| `daemon` | `str \| None` | `None` → `clamav-daemon` / `clamd@scan` | `clamav_daemon` |
| `freshclam_daemon` | `str \| None` | `None` → `clamav-freshclam` / `clamd-freshclam` | `clamav_freshclam_daemon` |
| `daemon_state` | `"started" \| "stopped" \| "restarted" \| "reloaded"` | `started` | `clamav_daemon_state` |
| `daemon_enabled` | `bool` | `true` | `clamav_daemon_enabled` |
| `freshclam_daemon_state` | same union | `started` | `clamav_freshclam_daemon_state` |
| `freshclam_daemon_enabled` | `bool` | `true` | `clamav_freshclam_daemon_enabled` |
| `daemon_configuration_changes` | `tuple[ConfigChange, ...] \| None` | `None` → the role's two default rules, with the family's socket line | `clamav_daemon_configuration_changes` |
| `freshclam_configuration_changes` | `tuple[ConfigChange, ...] \| None` | `None` — undefined upstream, so the loop is skipped | `clamav_freshclam_configuration_changes` |

<!---

Each `daemon_configuration_changes` item is a `ConfigChange(regexp, line="",
state="present")` — one `lineinfile` item, with the role's own defaults
(`item.line | default('')`, `item.state | default('present')`). The two
default rules drop the `Example` line (`state: "absent"`) and set
`LocalSocket <the family's socket>`; overriding the tuple replaces them
wholesale, as in Ansible. The `None` defaults are the role's `set_fact`
fallbacks: an operator override wins, an unset value resolves per family in
`configure_plan`, so the same inventory mapping works on both sides. Precedence
is `model defaults < inventory role mapping < overrides`, and sequences are
`tuple`, never `list` — the models are frozen and hashable, and a value that
reaches `op()` must hash. The config *paths* are not fields at all: the upstream
README states they are "hardcoded specifically for each OS family and cannot be
overridden" (D7).

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
defaults decide the site — per family: the Ubuntu host gets the apt trio and the
two `clamav-*` units, the two RedHat hosts get the dnf quartet, `clamd@scan`, and
the `clamd-freshclam.service` file this role installs; all of them get the role's
own two default rules. `main()` prints the wave tree and maps the role's failure
modes (bad inventory, `ValueError`/`TypeError` from a rejected variable,
`CommandError` — including the role's freshclam gate, whose failure aborts
exactly as the play's did — `HostConnectionError`, `PrivilegeError`, `OSError`)
to a one-line stderr message and exit 1.

**Run the program twice.** The second run must report no change: the packages are
installed (so the freshclam act is never built), the `Example` line is gone from
the family's daemon config, the socket directive is set, both units are running
and enabled — and on RedHat the unit file is in place, so its reload request is
never built. No notification, no handler wave. That is the migration's
idempotence claim, and the offline suite asserts it on both families.

> **Shared demo host.** The `ubuntu` demo host also runs the other role
> examples. This role leaves `clamav-daemon` listening on the
> `/var/run/clamav/clamd.ctl` unix socket (no TCP port is opened — the
> package default) and `clamav-freshclam` updating the databases a few times a
> day. To stop and disable both units again after a real run:
>
> ```bash
> ssh <user>@<ubuntu-host> 'sudo systemctl disable --now clamav-daemon clamav-freshclam'
> ```
>
> The RedHat hosts take the same pair under their own names, plus the unit file
> the role owns (`setup-RedHat.yml`'s first task):
>
> ```bash
> ssh <user>@<redhat-host> 'sudo systemctl disable --now clamd@scan clamd-freshclam'
> ssh <user>@<redhat-host> 'sudo rm /lib/systemd/system/clamd-freshclam.service && sudo systemctl daemon-reload'
> ```

## 6. What it does

One deploy. Each barrier closes its own wave, and a wave with no records is
not appended — so the wave count is **host-dependent and family-dependent**,
which is the point of the barrier model:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `install`: one `apt.packages` (Ubuntu) / `dnf.packages` (RedHat) request for the family's set — closed by `checkpoint("packages installed")` | the checkpoint **is** the role's `register: clamav_packages_install`: it reads the wave it just closed and names the hosts whose install changed |
| 1 | the `freshclam` `act` — only on a host the checkpoint named | a barrier never raises on a non-zero exit; the body interprets the `Result` exactly as the play's `failed_when` did, then queues the handler itself (D3) |
| 2 | (RedHat only) `setup_unit`: the `clamd-freshclam.service` file through `files.install` — closed by `checkpoint("unit installed")`, whose changed set gates `reload_unit` (`daemon_reload` + stop) | the second copy of the `register` + `when: … changed` shape (D8); on Ubuntu this slot does not exist, so configure is wave 2 there |
| 2 / 3 | `configure`: the daemon-config transforms and their mode companion, the optional `freshclam.conf` transforms, both unit states — closed by `checkpoint("configure done")` | the last checkpoint is the handler notification read: `changed_hosts` over the *configure* wave, filtered to the notifying names |
| 3 / 4 | `handler`: `Restart clamav-daemon (handler)` / `Restart clamd@scan (handler)` — only on notified hosts and only while `daemon_state` is not `"stopped"` | the role's handler `when:` is a static-variable gate, so it lives in the body, not on a request (D5) |

Two ordering rules are load-bearing:

* **Every gate reads the wave it just closed** — the freshclam gate reads the
  install wave, the reload gate the unit wave, the handler the configure wave
  (the barrier-placement rule). A converged host skips the act wave and nobody
  is notified later, so the rerun is two waves on Ubuntu and three on RedHat:
  the unit request and its checkpoint stay, nothing behind them fires.
* **Requests do not close waves.** The transforms, the mode companion and the
  two service tasks share one wave, applied per host in body order — the role's
  task order, edits before service states, and on RedHat the gated reload ahead
  of them because `setup-RedHat.yml` is included ahead of them.

A host whose `host.family` is neither `"Ubuntu"` nor `"RedHat"` — Alpine, the
family upstream has no `vars/` file for — returns before its first `yield`, so it
emits no requests, no barriers and no report lines at all.

## 7. Transcript

From `tests/roles/test_clamav.py::test_tree_snapshot`, against the simulated
Ubuntu host plus an Alpine host the role never touches. Nothing is installed
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

The same inventory's Rocky host, first pass — five waves, `setup-RedHat.yml`
sitting between the freshclam gate and the config edits exactly where the
include sits in the task list (D8):

```text
(root)
  wave 0
    rocky: Ensure ClamAV packages are installed [changed: 4 package(s) to install]
        $ sudo dnf install -y clamav clamav-update clamav-scanner-systemd clamav-data
    barrier: packages installed → 1 host notified
  wave 1
    barrier: Run freshclam after ClamAV packages change → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 2
    rocky: Ensure Freshclam service is available [changed: content /lib/systemd/system/clamd-freshclam.service is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-64eeb2c58dd82b68/content
        $ sudo sh -c 'mkdir -p /lib/systemd/system && cp /tmp/reemote-install-64eeb2c58dd82b68/content /lib/systemd/system/clamd-freshclam.service && chown root:root /lib/systemd/system/clamd-freshclam.service && chmod 644 /lib/systemd/system/clamd-freshclam.service && rm -rf /tmp/reemote-install-64eeb2c58dd82b68'
    barrier: unit installed → 1 host notified
  wave 3
    rocky: Reload systemd after adding service [changed: daemon-reload; clamd-freshclam.service already stopped]
        $ sudo systemctl daemon-reload
    rocky: Change configuration for the ClamAV daemon (^.*Example$) [changed: content /etc/clamd.d/scan.conf differs, will install]
        $ scp <files install> -> /tmp/reemote-install-9a2f6baaa308cba3/content
        $ sudo sh -c 'mkdir -p /etc/clamd.d && cp /tmp/reemote-install-9a2f6baaa308cba3/content /etc/clamd.d/scan.conf && chmod 644 /etc/clamd.d/scan.conf && rm -rf /tmp/reemote-install-9a2f6baaa308cba3'
    rocky: Change configuration for the ClamAV daemon (^.*LocalSocket .*$) [changed: content /etc/clamd.d/scan.conf differs, will install]
        $ scp <files install> -> /tmp/reemote-install-9a2f6baaa308cba3/content
        $ sudo sh -c 'mkdir -p /etc/clamd.d && cp /tmp/reemote-install-9a2f6baaa308cba3/content /etc/clamd.d/scan.conf && chmod 644 /etc/clamd.d/scan.conf && rm -rf /tmp/reemote-install-9a2f6baaa308cba3'
    rocky: Set ClamAV daemon configuration mode [no change: file /etc/clamd.d/scan.conf already exists]
    rocky: Ensure ClamAV daemon is running (if configured) [changed: clamd@scan.service not running (None), will start; clamd@scan.service not enabled, will enable]
        $ sudo systemctl start clamd@scan.service
        $ sudo systemctl enable clamd@scan.service
    rocky: Ensure ClamAV freshclam daemon is running (if configured) [changed: clamd-freshclam.service not running (None), will start; clamd-freshclam.service not enabled, will enable]
        $ sudo systemctl start clamd-freshclam.service
        $ sudo systemctl enable clamd-freshclam.service
    barrier: configure done → 1 host notified
  wave 4
    rocky: Restart clamd@scan (handler) [changed: clamd@scan.service already running; clamd@scan.service will restart]
        $ sudo systemctl restart clamd@scan.service
```

The rerun keeps the unit request and its checkpoint — an empty *gating* wave is
still a wave with records — but **the reload line is gone**, because the
checkpoint named nobody, and with it the handler:

```text
(root)
  wave 0
    rocky: Ensure ClamAV packages are installed [no change: all packages present]
    barrier: packages installed → 0 hosts notified
  wave 1
    rocky: Ensure Freshclam service is available [no change: content already up to date (/lib/systemd/system/clamd-freshclam.service)]
    barrier: unit installed → 0 hosts notified
  wave 2
    rocky: Change configuration for the ClamAV daemon (^.*Example$) [no change: content already up to date (/etc/clamd.d/scan.conf)]
    rocky: Change configuration for the ClamAV daemon (^.*LocalSocket .*$) [no change: content already up to date (/etc/clamd.d/scan.conf)]
    rocky: Set ClamAV daemon configuration mode [no change: file /etc/clamd.d/scan.conf already exists]
    rocky: Ensure ClamAV daemon is running (if configured) [no change: clamd@scan.service already running; clamd@scan.service already enabled]
    rocky: Ensure ClamAV freshclam daemon is running (if configured) [no change: clamd-freshclam.service already running; clamd-freshclam.service already enabled]
    barrier: configure done → 0 hosts notified
```

Where the unit file *did* drift against a systemd that has it loaded and running
(`test_rocky_unit_drift_reloads_and_stops`), the one reload request reports both
halves of the upstream task, and the configure wave below starts the unit again:

```text
    rocky: Reload systemd after adding service [changed: daemon-reload; clamd-freshclam.service running, will stop]
        $ sudo systemctl daemon-reload
        $ sudo systemctl stop clamd-freshclam.service
```

> **Offline tree vs live host.** The fresh RedHat pass above prints its
> `daemon-reload` because `SimulatedHost` marks a unit with no loaded state
> modelled as stale. A real host's freshly written, never-loaded unit is not
> pending — systemd loads it on the first `start`/`enable` — so the live first
> pass starts and enables `clamd-freshclam` without the reload line. The
> converged pass is identical on both sides: the checkpoint gate means no reload
> request is ever built.

The Alpine host appears in no tree: no barrier record, no request line.

> **Live-run note (2026-11-28).** The first live pass exercised the role's
> abort path exactly as ported: apt's postinst starts `clamav-freshclam`, and
> that daemon holds the lock on `/var/log/clamav/freshclam.log`, so the gated
> foreground `sudo freshclam` exits 2 with `Problem with internal logger …
> Resource temporarily unavailable` — and the body turns the `Result` into the
> typed `CommandError` (D3). The upstream Ansible play fails the same way on a
> fresh install; this race is what the task's lock-message comment
> anticipated. Stopping the unit first, `sudo freshclam` runs clean (rc 0,
> databases up to date), and rerunning the program after the abort skips the
> gate entirely — the install changed nobody — and converges to the rerun
> tree above.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | A host outside the `"Ubuntu"`/`"RedHat"` families emits **nothing at all** — Alpine has no `vars/Alpine.yml` upstream, so it has no port here either | the family guard is the body's opening `if`, not a `when=` on each request; the `include_vars` OS dispatch collapses to the frozen per-family constant tables, and unlike `certbot` nothing upstream keys on the *release*, so no `fact(LinuxDistribution)` barrier is needed |
| D2 | The `with_items` package loop collapses to **one request on the family's manager** — `apt.packages` on Ubuntu, `dnf.packages` on RedHat | the generic Ansible `package` module dispatches the same way; the per-package `register.changed` becomes the union — freshclam runs when *any* package installed, the same effective gate for the play that followed the loop |
| D3 | `command: freshclam` is an **`act` barrier whose `Result` the body interprets**, and its run — not a request — queues the handler | a barrier result is data, so the body reproduces the `failed_when` list *literally*: a non-zero exit OR the `locked by another process` stderr message raises the typed `CommandError` (the upstream list is an `any`, inverted from the task comment's intent to tolerate the lock). "It ran" is `changed` for the `command` module, so the body adds the host to the notified set |
| D4 | The `lineinfile` loops become **`files.transform` requests** (pure fixpoint transforms) plus a paired `files.file` mode request | `create: yes` is free (`transform(None)` produces the file), but transform idempotence is content-only — the task's `mode: 0644` therefore rides both, and the mode request is a notifier because the Ansible task would have queued the handler on a mode-only change too |
| D5 | The handler's `when: clamav_daemon_state != 'stopped'` is a plain **`if` in the body** | a static-variable gate needs no request-level `when`; the handler deploy is simply never yielded for a stopped daemon |
| D6 | `when: not ansible_check_mode` on the two service tasks has **no analogue** | a `dry` preview parks on the *first* checkpoint, before the configure wave that holds them is ever built |
| D7 | The path knobs commented out in `defaults/main.yml` are **frozen per-family module constants** (`FAMILIES`) | the upstream README states they are "hardcoded specifically for each OS family and cannot be overridden"; the default `LocalSocket` rule interpolates its family's frozen socket path, so no template engine is needed and the same two rules edit `/etc/clamav/clamd.conf` on Ubuntu and `/etc/clamd.d/scan.conf` on RedHat |
| D8 | `setup-RedHat.yml` maps **task-for-task**: the template becomes a frozen string through `files.install`, and its `register` + `when: … changed` pair becomes `checkpoint("unit installed")` gating one `systemd.service(running=False, daemon_reload=True)` request | the upstream `clamd-freshclam.service.j2` holds zero Jinja expressions, so no renderer and no `reemote[templates]` extra; the reload half stays fact-gated on `SystemdNeedDaemonReload` (issues #50 and #75) instead of Ansible's unconditional `systemctl daemon-reload`, and the request never notifies because the role's reload task carries no `notify` |

## 9. Scoped out

* **Non-Ubuntu/RedHat families.** Upstream ships `vars/` files and setup tasks
  for Debian and RedHat only — no `vars/Alpine.yml`, no apk package names, no
  OpenRC service — so an Alpine (or any other family) host is skipped rather
  than mapped onto the Debian layout (D1).
* **The EPEL repository itself.** On RHEL-family hosts the ClamAV RPMs come from
  EPEL; upstream has no repo task and neither does this port — compose with the
  shipped `repo_epel` role or enable the repository beforehand.
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
