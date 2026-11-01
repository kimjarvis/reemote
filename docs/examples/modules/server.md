# Module examples: server

Scripts under `examples/modules/server/` — one per fact or operation of the
[`server`](../../modules/server.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The read-only fact-gathering examples (`server_arch.py`, reading `Arch`,
`server_authorized_keys.py`, reading `AuthorizedKeys` for the login user,
`server_date.py`, reading `Date`, `server_groups.py`, reading `Groups`,
`server_has_gui.py`, reading `HasGui`, `server_home.py`, reading `Home`
for the login user and for `root`, `server_hostname.py`, reading
`Hostname`, `server_kernel.py`, reading `Kernel`,
`server_kernel_modules.py`, reading `KernelModules`,
`server_kernel_version.py`, reading `KernelVersion`,
`server_last.py`, reading `Last`,
`server_linux_distribution.py`, reading `LinuxDistribution`,
`server_linux_gui.py`, reading `LinuxGui`,
`server_linux_name.py`, reading `LinuxName`,
`server_locales.py`, reading `Locales`,
`server_lsb_release.py`, reading `LsbRelease`,
`server_mounts.py`, reading `Mounts`,
`server_path.py`, reading `Path`,
`server_processes.py`, reading `Processes`,
`server_sysctl.py`, reading `Sysctl` for three named keys,
`server_timezone.py`, reading `Timezone`,
`server_uptime.py`, reading `Uptime`,
`server_user.py`, reading `User`,
`server_users.py`, reading `Users`, and
`server_which.py`, reading `Which` for `python3` and `httpd`,
all over `gather_fact`) live alongside
the operation examples in
`examples/modules/server/`. They are not rendered here — each reads a single
fact, and the facts themselves are documented on the
[`server` module page](../../modules/server.md).

## Ensuring system groups

`server.group` in action: the deploy creates the `docker` group (pyinfra's
documented example) and a `lusers` system group with a fixed gid, both under
`sudo`. The operation is idempotent — it compares each name against the
`Groups` fact and yields a `GroupAddCommand` only where the group is absent,
so a second run plans no change. `system=True` renders `groupadd -r` and
`gid=1500` renders `--gid 1500`; on a FreeBSD host the same deploy would
render `pw groupadd -n ...` instead (the `Kernel` fact picks the command
form, and `system` is ignored on BSD). Removing a group is the mirror image:
`present=False` yields a `GroupDelCommand` only when the group is present.

::: server_group
    options:
      members: []

## Setting the hostname

`server.hostname` in action: the deploy names each host after its inventory
label (`web1` becomes `web1.example.com`), under `sudo`. The operation is
idempotent — it compares the requested name against the `Hostname` fact and
yields a `SetHostnameCommand` only where they differ, so a second run plans
no change. The `Which` fact picks the command form: `hostnamectl
set-hostname` where `hostnamectl` is available (it persists the name across
reboots), the bare `hostname` command on older systems. pyinfra's
`hostname_file` upload branch is deliberately dropped — reemote5's
transfers cannot be elevated, so the root-owned `/etc/hostname` write has
no faithful equivalent; on a systemd host `hostnamectl` already persists
the name. The script is named `server_op_hostname.py` to keep it distinct
from the read-only `server_hostname.py` fact example.

::: server_op_hostname
    options:
      members: []

## Killing a process by PID

`server.kill` in action, and the determinism-safe way to "find the PID,
then kill it" in ONE deploy. Like pyinfra, the operation takes an explicit
`pid` (and an optional `signal`, default `TERM`), so the script first takes
a `Processes` snapshot via `gather_fact` across the fleet, then builds the
deploy from a factory closing over that frozen mapping: the generator loops
over the snapshot, never live state, so prepare and apply yield the
identical `op(server.kill, pid=...)` sequence. No first deploy is needed —
nothing mutates the process table before the read, so this is just the
snapshot half of the [two-deploy split](../general.md) pattern. The
operation is stateless and non-idempotent (every planned kill shows as a
change), the signal flag and PID are shell-quoted, and the requests carry
`sudo=True` because the nginx master runs as root. The frozen-snapshot
trade-off: a PID that exits before the apply makes `kill` exit non-zero
(`CommandError`); re-running the script re-snapshots and plans an empty
deploy once nginx is gone.

::: server_kill
    options:
      members: []

## Loading and unloading kernel modules

`server.modprobe` in action: the deploy loads the `floppy` module (pyinfra's
documented example) and then ensures `loop` and `brd` are loaded, both under
`sudo` because `modprobe` needs root. The operation is idempotent — it
compares the names against the `KernelModules` fact and yields a
`ModprobeCommand` only for the modules that differ from the request, so a
host where `loop` is already loaded re-loads nothing and a second run plans
no change. `module` accepts a single name or a tuple of names, `force=True`
would render `modprobe -f` (without ever forcing a reload of an in-state
module), and unloading is the mirror image: `present=False` yields
`modprobe -r -a` only for the names that are loaded. On modern hardware the
`floppy` module may not exist at all — `modprobe` then exits non-zero and
the run fails with a typed `CommandError`, which the script reports as one
line.

::: server_modprobe
    options:
      members: []

## Mounting and unmounting filesystems

`server.mount` in action: the deploy mounts `/data` from `/dev/sdb1` as
ext4 with the `rw` and `noatime` options, then ensures `/scratch` is
unmounted, both under `sudo` because `mount`/`umount` need root. The
operation is idempotent — it compares against the `Mounts` fact and yields
a `MountCommand` only where the mount differs from the request: a fresh
`mount` when the mount point is absent, an `umount` when `mounted=False`
finds it present, and a `mount -o remount,...` only when an already-mounted
filesystem is missing some of the requested options (a subset check, so
extra options already on the mount never trigger a remount). When `path`
is not found directly but `device` is given, the mount is located by device
instead, and that found mount point is what an unmount or remount acts on;
on a BSD host the remount takes the `mount -uo ... -t ...` form (the
`Kernel` fact picks it). Like pyinfra, `/etc/fstab` is never touched — a
mount made this way does not survive a reboot unless an fstab entry is
managed separately. The mount target directory must already exist and the
device must be present; a missing device or directory makes `mount` exit
non-zero and the run fails with a typed `CommandError`, which the script
reports as one line.

::: server_mount
    options:
      members: []

## Setting the timezone

`server.timezone` in action: the deploy ensures every host keeps
`Europe/Amsterdam` (pyinfra's documented example), under `sudo`. The
operation is idempotent — it compares the requested zone against the
`Timezone` fact and yields a `SetTimezoneCommand` (`timedatectl
set-timezone`) only where they differ, so a second run plans no change.
It is supported on **systemd systems only**: when a change is needed the
`Which` fact probes `timedatectl`, and a host without it raises
`OperationError` during the prepare phase — before anything is sent —
rather than falling back. pyinfra's non-systemd fallback (symlinking
`/etc/localtime` and writing `/etc/timezone`) is deliberately dropped; on
a systemd host `timedatectl` already persists the zone across reboots. The
script is named `server_op_timezone.py` to keep it distinct from the
read-only `server_timezone.py` fact example.

::: server_op_timezone
    options:
      members: []

## Managing locales (pyinfra server.locale parity)

`server.locale` is deliberately not ported as a library operation — this
recipe is reemote5's answer, built on the generic `files.edit` primitive.
The script snapshots `FileContents("/etc/locale.gen")` and `Locales` from
every host, applies pyinfra's change condition to the frozen snapshot
(enable only when the locale is absent from `locale -a`, disable only when
present), and computes the desired content with the pure `locale_edit`
function — pyinfra's definition-line lookup (comment out or uncomment the
one matching line; `ValueError` with pyinfra's messages on zero or multiple
matches) done with a Python regex instead of a `FindInFile` grep. Run 1
spools the content to `/tmp/locale.gen` through `files.edit` with a
closure-factory transform frozen between the snapshot and the run; run 2
installs it into `/etc` with `files.move` flagged `sudo=True` and
`overwrite=True`, then runs `locale-gen` — a demo-side stateless operation
yielding the public raw `StringCommand`, since the library ships no generic
shell operation. The lockstep barrier completes every host's move before
any host regenerates. Re-running the script is a no-op once `locale -a`
reports the requested state. The script is named `server_op_locale.py` to
keep it distinct from the read-only `server_locales.py` fact example.

::: server_op_locale
    options:
      members: []

## Ensuring a sysctl setting (pyinfra server.sysctl parity)

`server.sysctl` is deliberately not ported as a library operation — this
recipe is reemote5's answer, built on the generic `files.edit` primitive
(the `server.Sysctl` fact it compares against *is* part of the library).
The script snapshots `Sysctl` for `fs.file-max` and
`FileContents("/etc/sysctl.conf")` from every host, then applies pyinfra's
two independent decisions to the frozen snapshots: the runtime gate is
pyinfra's fact comparison on the normalized value (lists join with spaces
for the shell, digits become ints so `"2000"` and `2000` compare equal),
and the persist gate compares the desired content — computed by the pure
`sysctl_persist_edit` function with pyinfra's `files.line` semantics
(match `key = value` with flexible spacing, commented lines included,
normalize every match, append when absent, stale `key = other` lines
survive) — against the file's current text. Run 1 applies the live value
through the demo-side stateless `sysctl_set` operation (a shell-quoted
`sysctl key=value` `StringCommand`, `sudo=True`); run 2 spools the content
to `/tmp/sysctl.conf` through `files.edit` with a closure-factory transform
frozen between the snapshot and the run; run 3 installs it into `/etc` with
`files.move` flagged `sudo=True` and `overwrite=True`. The spool and the
move cannot share one deploy — `files.move` validates its source in the
prepare phase too, and prepare never sends. Re-running the script is a
no-op once the live value and the persist line both match. The script is
named `server_op_sysctl.py` to keep it distinct from the read-only
`server_sysctl.py` fact example.

::: server_op_sysctl
    options:
      members: []

## Starting a service and waiting for it

The waiting design in action: `server.service` starts and enables httpd under
`sudo` (idempotently — it reads the unit's `ActiveState`/`UnitFileState` and
yields `systemctl` commands only where the unit differs), then
`server.wait_service` yields a `WaitForFactCommand` that the driver *polls*
over SSH every two seconds until `SystemdServiceState` reports `active`, or
raises `WaitTimeoutError` after sixty. The wait is always planned as a change
— like pyinfra's `server.wait` it reads no facts, so the request sequence is
identical in both phases; whether the unit is already active is discovered by
the first poll. Both hosts finish the start step before either polls, and the
two poll loops then run concurrently.

::: wait_for_httpd
    options:
      members: []
