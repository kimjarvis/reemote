# ntp — NTP time-daemon role

| | |
| --- | --- |
| Role | [`geerlingguy.ntp`](https://github.com/geerlingguy/ansible-role-ntp) |
| Module | [`reemote.roles.ntp`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/ntp.py) |
| Runner | [`examples/roles/ntp.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/ntp.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `ntp` role mapping → `NtpVars` |

<!---
| Waves | `5` on a changed host (0–4); `4` once converged — the handler wave disappears |
| Outputs | none — the deploy returns `None`; no other role reads ntp's results |
| Extra deps | **`reemote[templates]`** (jinja2 renders `ntp.conf.j2` / `chrony.conf.j2`) |
| Offline test | [`tests/roles/test_ntp.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_ntp.py) |
| Factory | plain (non-`async`) — the runner calls `ntp(v)`; the distribution and unit-file reads are `fact` barriers, not factory IO |

`ntp` installs and configures an NTP daemon on Ubuntu hosts: the `ntpsec` (or
legacy `ntp`) package and its apt cache refresh, `tzdata`, the system timezone,
the post-install stop/disable of a surviving `systemd-timesyncd`, the daemon's
running/enabled state, the optional templated `ntp.conf`/`chrony.conf`, and the
role's two handlers — `restart ntp` and `restart cron`.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with `yield from scoped("ntp", ntp(v))(host)` and
a run is one `run_waves` pass. The Ansible role's whole task list is that single
deploy, and its `defaults/main.yml` is `NtpVars`.

This is the corpus's **two-checkpoint** role (design doc §18.10) and the shape
that claim rests on. The stream is five waves, and the load-bearing part is that
the two handler channels read **two different reports**:

```python
info = yield fact(LinuxDistribution)              # wave 0 — the vars-table release
plan = ntp_plan(hv, info)
yield from install(plan)(host)                    # wave 1 — packages, tzdata, timezone
install_report = yield checkpoint("install done")
cron_notified = changed_hosts(install_report, NOTIFY_CRON)
unit_files = yield fact(SystemdUnitFiles)         # wave 2 — post-install timesyncd probe
timesyncd = TIMESYNCD_UNIT in unit_files
yield from configure(plan, timesyncd)(host)       # wave 3 — timesyncd, daemon, config
configure_report = yield checkpoint("configure done")
ntp_notified = changed_hosts(configure_report, NOTIFY_NTP)
yield from handlers(plan, ntp_notified, cron_notified)(host)  # wave 4
```

`cron_notified` (the timezone task) reads the **install** checkpoint; `ntp_notified`
(the config template) reads the **configure** checkpoint. Two reports, so unlike
nginx — whose two channels both read the one configure checkpoint — these cannot
share a single barrier. The three reads the old example took *between* its three
`run()` calls (the distribution gather, the post-install unit-files gather, the two
`changed_hosts` reads) are barriers now, so nothing is frozen in a module-level plan
dict and the per-host body *is* the plan.

## 1. What it is, and where it came from

The role's tasks map onto reemote operations and barriers:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `include_vars` `with_first_found` (OS/version vars files) | `fact(LinuxDistribution)` → `select_vars_table(info)` over frozen tables | barrier + recipe |
| `set_fact: ntp_daemon/package/config_file/driftfile` when not defined | `NtpVars` `None` fields → `ntp_plan` derives from the vars table | covered |
| `apt: name={{ ntp_package }}` (+ molecule `cache_valid_time: 600`) | `apt.packages(packages=(package,), update=True, cache_valid_time=600)` | covered (D2) |
| `apt: name={{ tzdata_package }}` (`when: ...system == "Linux"`) | `apt.packages(packages=tzdata_packages)` (unconditional — statically true) | covered |
| `timezone: name={{ ntp_timezone }}` + `notify: restart cron` | `server.timezone(timezone=…)` named `Set timezone.`, read via `checkpoint("install done")` | covered |
| `service_facts` + `when: ...status != "not-found"` | `fact(SystemdUnitFiles)` after install; `TIMESYNCD_UNIT in unit_files` | barrier (D4) |
| `systemd: name={{ ntp_daemon }} state=... enabled=...` | `systemd.service(running=…, enabled=…)` (and the `ntp_enabled: false` stop/disable arm) | covered |
| `template: src={{ ntp_config_file \| basename }}.j2` | control-side render (`render_config`) → `files.install(content=…, mode="0644")` | covered (D6) |
| `handlers/main.yml`: `restart ntp` (`when: ntp_enabled`), `restart cron` (`when: ntp_cron_handler_enabled`) | a per-host `handlers(...)` body, each gated on its own notification set + flag | deviation (D1) |
| `ignore_errors: ansible_check_mode` on the daemon task | **dropped** — reemote's `dry` never applies commands | deviation (D3) |
| the multi-OS `include_vars` dispatch (RedHat/Suse/Arch/FreeBSD) | narrowed to Debian, with a fail-fast raise on a non-Ubuntu gathered name | deviation (D5) |

No library gap blocked this migration, so none was filed: `apt.packages` carries the
`cache_valid_time` gate, `server.timezone` the `Timezone` fact, `SystemdUnitFiles`
the `service_facts` membership, `files.install` the templated-config write, and the
`fact`/`checkpoint` barriers (issues #15/#17) run the reads and the notify inside the
body. The role's dead `clock.j2` template has no task that uses it and is not
migrated.

## 2. Install

```bash
pip install "reemote[templates]"     # or: uv add "reemote[templates]"
```

`jinja2` is the only extra. The role renders `ntp.conf` (or `chrony.conf`)
**control-side**, and imports `reemote/roles/_render.py` *inside* `_render`, so
`import reemote.roles` stays clean without the extra and a missing `jinja2` surfaces
as an `ImportError` at render time rather than at import time.

SSH access to each Ubuntu host with the key named in its `connection`, and
passwordless sudo (or a `privilege` section in the inventory): every request carries
`sudo=True`, because reemote has no play-level `become`. Both fact barriers
(`LinuxDistribution`, `SystemdUnitFiles`) run on the host — they are reads over SSH,
not controller-side calls.

## 3. Variables

`NtpVars` is a frozen pydantic model (`extra="forbid"`) whose fields carry bare
names; the role name passed to `resolve_vars` binds the entry's `ntp` role
mapping, so an inventory written for the role needs no key gymnastics. The four
`set_fact` overrides (`daemon`, `package`,
`config_file`, `driftfile`) default to `None` — "derive from the gathered vars table",
exactly as the role's `when: ... is not defined` guards.

--->

::: reemote.roles.ntp
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - ntp

**Variables:**

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `enabled` | `bool` | `true` | `ntp_enabled` |
| `timezone` | `str` | `Etc/UTC` | `ntp_timezone` |
| `manage_config` | `bool` | `false` | `ntp_manage_config` |
| `area` | `str` | `""` — worldwide pool | `ntp_area` |
| `servers` | `tuple[str, …] \| None` | `null` → four pool lines from `area` | `ntp_servers` |
| `restrict` | `tuple[str, …]` | `("127.0.0.1", "::1")` | `ntp_restrict` |
| `cron_handler_enabled` | `bool` | `false` | `ntp_cron_handler_enabled` |
| `tinker_panic` | `bool` | `false` | `ntp_tinker_panic` |
| `chrony_maxdistance` | `str` | `3` | `ntp_chrony_maxdistance` |
| `daemon` | `str \| None` | `null` → vars table (`ntpsec`) | `ntp_daemon` |
| `package` | `str \| None` | `null` → vars table (`ntpsec`) | `ntp_package` |
| `config_file` | `str \| None` | `null` → vars table (`/etc/ntpsec/ntp.conf`) | `ntp_config_file` |
| `driftfile` | `str \| None` | `null` → vars table (`/var/lib/ntpsec/ntp.drift`) | `ntp_driftfile` |

<!---

Precedence is `model defaults < inventory role mapping < overrides`. Sequences are `tuple`,
never `list`: the model is frozen and hashable, and a value that reaches `op()` must
hash — a JSON list from the inventory is normalized to a tuple by a `before`
validator. Validation is fail-fast: `timezone` and `chrony_maxdistance` must be
non-empty, every `servers`/`restrict` entry a non-empty string, and each `set_fact`
override — when given — non-empty. `extra="forbid"` plus `resolve_vars`' binding
report a key that names no field in the role mapping, and the two handler flags gate the restarts
rather than the requests (as `handlers/main.yml`'s `when:` does), before any host is
contacted.

## 5. Run it

The role is library code; `examples/roles/ntp.py` is the runnable artifact — a
standalone program with its own `main()`, no shared runner. It loads and validates
`inventory.json` from the current working directory, resolves the role's variables (the
defaults overlaid with the entry's `ntp` role mapping, then the call-site
`overrides`), builds the deploy from the `ntp` factory and drives it with `run_waves` —
a `dry=True` one-wave preview, then the real apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import NtpVars, ntp

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(NtpVars, "ntp", inventory, overrides={"manage_config": True})
    deploy = ntp(vars_set)                                          # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)    # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)              # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/ntp.py
```

The call-site `NTP_OVERRIDES` turn on `manage_config` so the demonstration exercises
the config template and its `restart ntp` handler, not just the package install. The
`dry=True` pass is a one-wave preview: ntp's first item is the `LinuxDistribution`
`fact` barrier, so the run parks there (`barrier: LinuxDistribution → unknown until
run`) before reading any further state or applying a request. A converged rerun is the
honest preview — run the whole program a second time and it reports no change and
queues no restart.

**Run the program twice.** The second run must report no change and fire no handler:
the packages are installed, the cache stamp is fresh, the timezone is set, the daemon
is running and enabled, and the rendered config file is byte-identical — so neither
`Set timezone.` nor `Generate ntp configuration file.` changes, both checkpoints
notify nobody, and the handler wave is empty. That is the migration's idempotence
claim, and the offline suite asserts it.

## 6. What it does

One deploy. Each barrier closes its own wave, and a wave with no records is not
appended — so the wave count is **state-dependent**: the handler wave only exists when
a channel notified.

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `fact(LinuxDistribution)` | the release the `with_first_found` vars chain dispatches on; the install set is a pure function of it |
| 1 | `install`: `apt.packages(ntpsec, update=True, cache_valid_time=600)`, `apt.packages(tzdata)`, `server.timezone` — closed by `checkpoint("install done")` | the **cron** channel (`Set timezone.`) reads *this* report |
| 2 | `fact(SystemdUnitFiles)` | the `service_facts` probe runs *after* the install — ntpsec can remove `systemd-timesyncd` (both conflict through the virtual `time-daemon`) |
| 3 | `configure`: timesyncd stop/disable (when present + enabled), the daemon running/enabled (or stopped/disabled), `files.install(config)` — closed by `checkpoint("configure done")` | the **ntp** channel (`Generate ntp configuration file.`) reads *this* report — a different one from wave 1 |
| 4 | `handlers`: `restart ntp` (when enabled and ntp-notified), `restart cron` (when cron-flagged and cron-notified) | DEFINITION order, each gated on its own notification set + role flag |

Three ordering rules are load-bearing:

* **Two checkpoints, read separately.** `changed_hosts(install_report, NOTIFY_CRON)`
  and `changed_hosts(configure_report, NOTIFY_NTP)` consume two different reports, so
  one shared checkpoint would silently drop a channel. Each checkpoint must
  immediately follow the stage it reports on.
* **The timesyncd gate is a post-install fact.** `configure` cannot be a pure function
  of `install`'s inputs: whether the unit survives belongs to the wave *after* the
  install applied, hence `fact(SystemdUnitFiles)` sits between the two checkpoints.
* **The handler verbs come from the plan.** `restart ntp` uses `restarted=True` (Ansible
  `state: restarted`), so a host that changed its config but already runs the daemon
  still shows the restart, and an empty notification set yields nothing — the
  converged no-op.

A host whose `host.family` is not `"Ubuntu"` returns before its first `yield`, so it
emits no requests, no barriers and no report lines at all.

## 7. Transcript

From `tests/roles/test_ntp.py`, against the simulated Ubuntu host (`manage_config`
on) plus a RedHat host the role never touches. The `systemd_unit_files` store is seeded
to the intended post-install state (the daemon's own unit + `cron.service`, timesyncd
gone — the conflict removed it). Fresh host, first pass:

```text
(root)
  wave 0
    barrier: LinuxDistribution → {'name': 'Ubuntu', 'family': 'Debian', 'major': '24', 'minor': '04', ...}
  wave 1
    ubuntu: Ensure NTP package is installed. [changed: apt update; 1 package(s) to install]
        $ sudo apt-get update
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y ... install ntpsec
    ubuntu: Ensure tzdata package is installed (Linux). [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y ... install tzdata
    ubuntu: Set timezone.            [no change: timezone is set]
    barrier: install done → 1 host notified
  wave 2
    barrier: SystemdUnitFiles → {'ntpsec.service': {...}, 'cron.service': {...}}
  wave 3
    ubuntu: Ensure NTP is running and enabled as configured. [changed: ntpsec.service not running (None), will start; ntpsec.service not enabled, will enable]
        $ sudo systemctl start ntpsec.service
        $ sudo systemctl enable ntpsec.service
    ubuntu: Generate ntp configuration file. [changed: content /etc/ntpsec/ntp.conf is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-…/content
        $ sudo sh -c 'mkdir -p /etc/ntpsec && cp … /etc/ntpsec/ntp.conf && chmod 644 … && rm -rf …'
    barrier: configure done → 1 host notified
  wave 4
    ubuntu: restart ntp              [changed: ntpsec.service already running; ntpsec.service will restart]
        $ sudo systemctl restart ntpsec.service
```

The install checkpoint reports the apt change ("1 host notified" counts changed hosts in
the wave), but the timezone task no-oped, so the **cron** channel is empty and no
`restart cron` runs; the config change fills the **ntp** channel, so `restart ntp` runs
in wave 4.

The rerun — every barrier re-runs (they are reads, not cached plan values), both
checkpoints find nothing, and **the handler wave disappears entirely**:

```text
(root)
  wave 0
    barrier: LinuxDistribution → {'name': 'Ubuntu', 'family': 'Debian', 'major': '24', 'minor': '04', ...}
  wave 1
    ubuntu: Ensure NTP package is installed. [no change: apt update skipped, cache is fresh; all packages present]
    ubuntu: Ensure tzdata package is installed (Linux). [no change: all packages present]
    ubuntu: Set timezone.            [no change: timezone is set]
    barrier: install done → 0 hosts notified
  wave 2
    barrier: SystemdUnitFiles → {'ntpsec.service': {...}, 'cron.service': {...}}
  wave 3
    ubuntu: Ensure NTP is running and enabled as configured. [no change: ntpsec.service already running; ntpsec.service already enabled]
    ubuntu: Generate ntp configuration file. [no change: content already up to date (/etc/ntpsec/ntp.conf)]
    barrier: configure done → 0 hosts notified
```

The RedHat host appears in neither tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | Handlers are a **per-host body**, not a `compose`d partition | the example had to split the notified set across two deploys because one `compose` served every host; a per-host body gates each restart on `host.name in <notified>` and the role flag, exactly as `handlers/main.yml`'s `when:` |
| D2 | The molecule `cache_valid_time: 600` refresh is **folded into** the NTP install | the role itself only ships the package task; folding the pre-task refresh makes the role self-contained on a fresh host, and `apt.packages` gates the update on `AptUpdateStamp`, so a converged host yields no `apt-get update` |
| D3 | `ignore_errors: ansible_check_mode` on the daemon task is **dropped** | reemote's `dry` never applies commands, so there is no check-mode failure to tolerate; a real failure raises `CommandError` |
| D4 | `service_facts` `status != "not-found"` becomes **`SystemdUnitFiles` membership** | a unit absent from `list-unit-files` is exactly `not-found`; the gate is presence-only, and the runtime stop/disable still reads the `services`/`enabled_services` state |
| D5 | `ansible_managed` is **frozen** to `# Ansible managed`, and the multi-OS `include_vars` narrows to Debian with a fail-fast raise | a timestamped header would rewrite every config each run and break both idempotence and the `restart ntp` notification; a gathered name that is not `Ubuntu` (the inventory said so) raises `ValueError`, following the java precedent |
| D6 | The **chrony renderer is reachable only via a `config_file` override** to a `chrony.conf` path | the Ubuntu default selects `ntp.conf`/`ntp.conf.j2`; the basename dispatch picks `chrony.conf.j2` only when the path names `chrony.conf`, and an unknown basename raises (the role's dead `clock.j2` is not migrated) |
| D7 | The family guard is the **body's opening `if`**, not a `when=` per request | an off-target host returns before its first `yield` and emits no `[skipped]` lines — the old example logged every request as skipped |
| D8 | The role's value gates become **pydantic validation** | non-empty timezone/maxdistance, non-empty sequence entries, `None`-or-non-empty overrides, and YAML-boolean coercion all raise `ValidationError` (a `ValueError`) at resolve time — before any host is contacted |

## 9. Scoped out

* **Non-Ubuntu families.** The role's RedHat, Suse, Archlinux and FreeBSD branches are
  not migrated; `include_vars`' OS dispatch narrows to the Debian family, still split
  by gathered release major (`ntp` on legacy Debian-11 vs `ntpsec` on the modern line).
* **Managing the pool servers' freshness.** `ntp_servers` is either the derived pool or
  an explicit list; the role never validates reachability, and neither does this one.
* **`chrony` as a first-class target.** chrony renders only through a `config_file`
  override (D6); there is no `ntp_daemon: chrony` vars file, matching the role.
* **The `clock.j2` template.** The role ships it but no task templates it; it is dead
  and not migrated.
* **Per-host privilege.** NTP needs root and the inventory says how a host elevates;
  there is no play-level `become` to reproduce.

--->
