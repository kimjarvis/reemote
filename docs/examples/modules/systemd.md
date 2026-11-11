# Module examples: systemd

Scripts under `examples/modules/systemd/` — one per fact or operation of the
[`systemd`](../../modules/systemd.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The read-only fact-gathering examples (`systemd_systemd_status.py` reading
`SystemdStatus`, `systemd_systemd_enabled.py` reading `SystemdEnabled`,
`systemd_systemd_unit_properties.py` reading `SystemdUnitProperties`,
`systemd_systemd_failed_units.py` reading `SystemdFailedUnits`,
`systemd_systemd_timers.py` reading `SystemdTimers`,
`systemd_systemd_unit_files.py` reading `SystemdUnitFiles`,
`systemd_systemd_version.py` reading `SystemdVersion`,
`systemd_systemd_system_state.py` reading `SystemdSystemState` and
`systemd_systemd_dependencies.py` reading `SystemdDependencies`, all over
`gather_fact`) are not rendered here — they read a single fact, and the facts
themselves are documented on the
[`systemd` module page](../../modules/systemd.md).

Every operation example reads the project root's `inventory.json` (validated
up front with `validate_inventory`) and guards each request with
`when=is_systemd`, a stable module-level predicate reading only static
`host.group` so the OpenRC `alpine` host is skipped rather than failing on a
missing `systemctl`. The `systemctl` and file-writing operations need root, so
their requests are flagged `sudo=True`. `run_examples.py` runs every script
live, so the state-mutating examples are written to be safe on the shared demo
hosts: `mask` runs a full mask → no-op → unmask cycle that restores the unit
before the deploy finishes, and `service`/`preset` converge an already-installed
web server. The drop-in, tmpfiles and sysusers examples write root-owned
configuration and are additionally compile-checked by the offline example
harness; on the CentOS demo hosts their elevated install is blocked by SELinux
`staff_t` confinement (see the host-policy note below). Point `INVENTORY_PATH`
at your own throwaway machines to execute them for real.

## Managing a service

The service operation: `systemd.service` is the canonical home of the
operation formerly known as `server.service` (which remains a deprecated
alias). It ports the baseline's `systemd.service` and manages one unit along two
independent axes — `running` against the
[`SystemdServiceState`][reemote.modules.systemd.systemd.SystemdServiceState]
fact and `enabled` against
[`SystemdServiceEnabled`][reemote.modules.systemd.systemd.SystemdServiceEnabled]
— yielding a command only where the unit differs from the request. The script
also shows the reemote extensions: `reloaded=True` with `soft_reload=True`
issues `systemctl reload-or-try-restart`, gated on the unit actually running.
The managed unit is chosen per OS family by `web_server_unit` — `httpd` on the
RHEL-family `centos` hosts, `apache2` on the Debian-family `ubuntu` host — a
branch on static `host.group` that stays identical across both phases, so the
determinism guard holds even though the unit name differs per host.

::: systemd_service
    options:
      members: []

## Reloading the daemon

The daemon-reload operation: `systemd.daemon_reload` ports the baseline's stateless
`systemctl daemon-reload`. It reads no fact and always yields, so every planned
reload shows as a change — the step that makes systemd re-read unit files
after an edit.

::: systemd_daemon_reload
    options:
      members: []

## Masking a unit

The mask operation: `systemd.mask` is a reemote original, bidirectional and
idempotent through one present-flagged path. It reads the unit's
`UnitFileState` from
[`SystemdUnitProperties`][reemote.modules.systemd.systemd.SystemdUnitProperties]
and yields `systemctl mask` only when the unit is not already masked
(`masked=False` yields `unmask`). Masking symlinks the unit to `/dev/null` so
neither a manual start nor a dependency can activate it — stronger than
`disable`, which only clears the boot setting. The script runs a full
mask → no-op → unmask cycle (the same shape as `selinux.permissive_domain`), so
the unit is restored before the deploy finishes and a later `service`/`preset`
deploy is not blocked by a lingering mask — safe to run live.

::: systemd_mask
    options:
      members: []

## Clearing a failed state

The reset-failed operation: `systemd.reset_failed` is a reemote original
covering the reset-only case (the enhanced `service` keeps a `reset_failed`
flag too). It reads
[`SystemdServiceState`][reemote.modules.systemd.systemd.SystemdServiceState]
and yields `systemctl reset-failed` only when the unit's `ActiveState` is
`failed` — the recovery step before a start, since a failed unit otherwise
refuses to start.

::: systemd_reset_failed
    options:
      members: []

## Host-policy prerequisite (drop-in, tmpfiles, sysusers)

The drop-in, tmpfiles and sysusers operations spool their fragment into a
dest-derived `/tmp` directory with an **unprivileged** scp transfer, then install
it with an **elevated** `sh -c` (`mkdir` + `cp` + `chmod`). The scripts are correct —
`sudo=True` is set and the transfer is never elevated — but on the CentOS demo
hosts (`web1`/`web2`) SELinux is `Enforcing` and `sudo` transitions to the
confined `staff_t` domain, which blocks the install two ways:

- the scp spool lands mode `0600` owned by the connecting user, and `staff_t`
  lacks `CAP_DAC_OVERRIDE`, so the elevated `cp` cannot read another user's
  private file (`cp: cannot open
  '/tmp/reemote-systemd-<digest>/systemd.conf' for reading:
  Permission denied`);
- creating a new `etc_t`-labelled directory such as `/etc/sysusers.d` is denied
  (`mkdir: cannot create directory '/etc/sysusers.d': Permission denied`),
  whereas the existing `systemd_unit_file_t`-labelled `/etc/systemd/system` is
  writable.

These are host-policy limits, not script defects: the same examples converge on
the unconfined `ubuntu` host. To run them on the CentOS hosts, give `sudo` an
unconfined domain (e.g. `unconfined_t`) or set SELinux permissive, then re-run.

## Installing a drop-in override

The drop-in operation: `systemd.dropin` is a reemote original that manages a
fragment under `/etc/systemd/system/<unit>.d/<filename>.conf`, overriding
selected settings of a vendor unit without editing the packaged file. It
writes only when the content differs (idempotent through the in-namespace
content fact), then chains `daemon-reload`; `restart=True` would also restart
the unit. The write follows the `dnf.repo` spool pattern — an unprivileged scp
transfer to `/tmp` then an elevated install — and the file-name parameter is
`filename`, not the reserved `op` meta-parameter `name`. System-scope only.

::: systemd_dropin
    options:
      members: []

## Declaring runtime files and directories

The tmpfiles operation: `systemd.tmpfiles` is a reemote original managing a
`/etc/tmpfiles.d/<filename>.conf` fragment. It writes only when the content
differs, then applies it with `systemd-tmpfiles --create` so the declared
runtime paths exist immediately rather than at the next boot. `present=False`
removes the config file only. Same spool-and-install pattern and `filename`
parameter as the drop-in; system-scope only.

::: systemd_tmpfiles
    options:
      members: []

## Declaring system users and groups

The sysusers operation: `systemd.sysusers` is a reemote original managing a
`/etc/sysusers.d/<filename>.conf` fragment — the declarative alternative to
imperative `useradd`. It writes only when the content differs, then applies it
with `systemd-sysusers` so the declared accounts exist immediately.
`present=False` removes the config file only; it does **not** delete the
users/groups it created. Same spool-and-install pattern and `filename`
parameter; system-scope only.

::: systemd_sysusers
    options:
      members: []

## Applying a preset policy

The preset operation: `systemd.preset` is a reemote original applying a unit's
vendor preset policy. It reads
[`SystemdUnitFiles`][reemote.modules.systemd.systemd.SystemdUnitFiles] and
yields `systemctl preset <unit>` only when the unit's current state differs
from its preset policy; a unit already matching, or with no policy, is a no-op.
`all_units=True` would instead issue a stateless `systemctl preset-all`.

::: systemd_preset
    options:
      members: []