# systemd module

<!---

The `systemd` namespace is the canonical home for systemd visibility and
control. The per-unit `SystemdServiceState`/`SystemdServiceEnabled` facts, the
`service` operation and the `unit_name` normalization helper were relocated
here from `server`, which re-exports them for backward compatibility (its
`service` is now a deprecated alias) and keeps `server.wait_service`, polling
the relocated `SystemdServiceState`.

Beyond the two pyinfra ports (`SystemdStatus`/`SystemdEnabled` facts and the
`service`/`daemon_reload` operations), the namespace adds seven original facts
and six original operations. Every `systemctl`-based fact and operation
accepts the scope flags `user_mode`/`machine`/`user_name` to target the
per-user manager, a named machine, or a specific user's session. Operations
are idempotent through facts: they read the current state and yield a command
descriptor only when it differs from the request.

The three file-based operations (`dropin`/`tmpfiles`/`sysusers`) are
**system-scope only** — they write root-owned `/etc/...` paths through an
unprivileged spool transfer followed by an elevated install (the `dnf.repo`
pattern), and need `sudo=True`. User-scope config file management is a
`files.edit` recipe instead. Their file-name parameter is `filename`, not
`name`: `name` is a reserved `op` meta-parameter consumed by the driver and
never forwarded to the operation.

--->

## systemd Facts

::: reemote5.modules.systemd.systemd
    options:
      show_root_heading: false
      members:
        - SystemdDependencies
        - SystemdEnabled
        - SystemdFailedUnits
        - SystemdServiceEnabled
        - SystemdServiceState
        - SystemdStatus
        - SystemdSystemState
        - SystemdTimers
        - SystemdUnitFiles
        - SystemdUnitProperties
        - SystemdVersion

## systemd Returns

::: reemote5.modules.systemd.systemd
    options:
      show_root_heading: false
      members:
        - SystemdFailedUnit
        - SystemdTimer
        - SystemdUnitFile
        - SystemdVersionInfo

## systemd Operations

::: reemote5.modules.systemd.systemd
    options:
      show_root_heading: false
      members:
        - daemon_reload
        - dropin
        - mask
        - preset
        - reset_failed
        - service
        - sysusers
        - tmpfiles

## Helpers

::: reemote5.modules.systemd.systemd
    options:
      show_root_heading: false
      members:
        - unit_name