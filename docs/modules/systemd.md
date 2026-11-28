# systemd module

<!---

The `systemd` namespace is the canonical home for systemd visibility and
control. The per-unit `SystemdServiceState`/`SystemdServiceEnabled` facts, the
`service` operation and the `unit_name` normalization helper were relocated
here from `server`, which re-exports them for backward compatibility (its
`service` is now a deprecated alias) and keeps `server.wait_service`, polling
the relocated `SystemdServiceState`.

Beyond the two the baseline ports (`SystemdStatus`/`SystemdEnabled` facts and the
`service`/`daemon_reload` operations), the namespace adds eight original facts
and six original operations. Every `systemctl`-based fact and operation
accepts the scope flags `user_mode`/`machine`/`user_name` to target the
per-user manager, a named machine, or a specific user's session. Operations
are idempotent through facts: they read the current state and yield a command
descriptor only when it differs from the request — `daemon_reload` with a
`unit` included, gated on the `SystemdNeedDaemonReload` read (the no-unit
form stays the baseline's stateless reload). That read answers two different
kinds of pending, because `NeedDaemonReload` alone answers only one: the
*staleness* of the fragment systemd loaded, and the load roots compared
against `FragmentPath`/`DropInPaths` for a *newly shadowing* unit file or
drop-in — an `/etc/systemd/system` override of a unit the manager still runs
from `/usr/lib`, which systemd reports as not reload-pending until the search
path is re-resolved (issue #75). A fresh unit name is pending neither way:
systemd loads it lazily on `start`/`enable`, so no reload is issued. After the
reload the answers all go back to `no`, which is what lets a handler request
carry `daemon_reload=True` and still converge. In `service`, each of the two
managed axes has an opt-out sentinel: `enabled=None` leaves the boot setting
alone and `running=None` (Ansible's `state: omit`) leaves the active state
alone, so one request can reconcile exactly the half the operator declared.

The three file-based operations (`dropin`/`tmpfiles`/`sysusers`) are
**system-scope only** — they write root-owned `/etc/...` paths through an
unprivileged spool transfer followed by an elevated install (the `dnf.repo`
pattern), and need `sudo=True`. User-scope config file management is a
`files.edit` recipe instead. Their file-name parameter is `filename`, not
`name`: `name` is a reserved `op` meta-parameter consumed by the driver and
never forwarded to the operation.

--->

::: reemote.modules.systemd.systemd
    options:
      show_root_heading: false
      members:
        - SystemdDependencies
        - SystemdEnabled
        - SystemdFailedUnit
        - SystemdFailedUnits
        - SystemdNeedDaemonReload
        - SystemdServiceEnabled
        - SystemdServiceState
        - SystemdStatus
        - SystemdSystemState
        - SystemdTimer
        - SystemdTimers
        - SystemdUnitFile
        - SystemdUnitFiles
        - SystemdUnitProperties
        - SystemdVersion
        - SystemdVersionInfo
        - daemon_reload
        - dropin
        - mask
        - preset
        - reset_failed
        - service
        - sysusers
        - tmpfiles
        - unit_name
