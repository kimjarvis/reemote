# Module examples: server

Scripts under `examples/modules/server/` — one per fact or operation of the
[`server`](../../modules/server.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The read-only fact-gathering examples (`server_arch.py`, reading `Arch`,
`server_authorized_keys.py`, reading `AuthorizedKeys` for the login user,
`server_date.py`, reading `Date`,
`server_env.py`, reading `Env` for `PATH` and `HOME`,
`server_groups.py`, reading `Groups`,
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
`server_port.py`, reading `Port` for TCP 80, UDP 53 and a closed port,
`server_processes.py`, reading `Processes`,
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

`server_lsb_release.py` first installs its prerequisite: the CentOS demo hosts
do not ship `lsb_release` in their base repos (EPEL provides it), so a short
`dnf.packages` deploy flagged `sudo=True` puts the binary in place before the
fact is gathered. The install is idempotent, and the read never hits the
missing-command path.

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
snapshot half of the
[two-deploy split](../demonstrations/composition.md#splitting-a-fact-dependent-deploy-in-two)
pattern. The operation is stateless and non-idempotent (every planned kill
shows as a change), the signal flag and PID are shell-quoted, and the
requests carry `sudo=True` because the nginx master runs as root. The frozen-snapshot
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
line. On the CentOS demo hosts this is also a **host-policy limit**: SELinux
`staff_t` confinement denies module loading, so `modprobe` reports even a
present, indexed module (`loop`, `brd`) as "not found in directory" despite
`kernel-modules` being installed and the `depmod` database listing them. The
requests already carry `sudo=True`; run it with an unconfined `sudo` domain
(e.g. `unconfined_t`) or SELinux permissive.

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
reports as one line. The demo hosts have no `/dev/sdb1` (only `/dev/sda`), and
CentOS `staff_t` confinement additionally denies creating `/data` and mounting
filesystems — a **host-policy and storage prerequisite**, not a script defect:
it needs an operator-provisioned device, an existing mount point and an
unconfined `sudo` domain.

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

## Creating and removing user accounts

`server.user` in action: the deploy creates a `deploy` service account with a
login shell and a GECOS comment in the `apps` group, appends the `staff`
group to it, then removes a `legacy` account — all under `sudo`, because
`useradd`, `usermod`, `userdel` and the home-directory pair need root. The
two groups come from `server.group` requests first, since the lockstep barrier
finishes those steps before any host touches an account. The operation is
idempotent — it compares the request against the `Users` fact and yields
`useradd` for an absent account, a `usermod` carrying only the differing
fields for a present one, and `userdel` for `present=False`, so a second run
plans no change. `groups` is a tuple because `op()` arguments must stay
hashable, and `append=True` adds memberships without removing any. The default
`ensure_home` is pyinfra's `files.directory` delegation, inlined: with
`create_home=False` (the default) `useradd` builds nothing from the skeleton,
so the plan shows the `mkdir -p /home/deploy` plus `chown deploy:deploy
/home/deploy` pair that actually creates the directory. The append step also
makes the per-phase fact cache visible: in the plan both user requests read
the same pre-deployment state and both show a change, while in the apply each
step re-reads the facts, so the fourth step sees the groups the third wrote
and sends only `usermod -a -G staff deploy`. pyinfra's
`public_keys`/`delete_keys` options are deliberately dropped (reemote5 has no
`files.line` operation and only unprivileged transfers), as are its plan-time
`Which` guards, so a missing `useradd` surfaces at apply as
`CommandNotFoundError`. The script mutates the shared demo inventory for
real: the account, its home directory and the two groups stay on the hosts.
The script is named `server_op_user.py` to keep it distinct from the
read-only `server_user.py` fact example.

::: server_op_user
    options:
      members: []

## Authorising SSH public keys

`server.user_authorized_keys` in action: the deploy ensures the login user's
`~/.ssh` directory (mode `700`) and `authorized_keys` file (mode `600`) exist
and are owned correctly, then appends the public keys not already listed —
pyinfra's documented flow with its controller-side key-file paths dropped
(every `public_keys` entry here is a literal key string, and it is a tuple
because `op()` arguments must stay hashable). The operation is idempotent —
it reads the `Home` fact to resolve the directory, a private `_KeysPathStat`
probe for the directory/file owner and mode, and the `AuthorizedKeys` fact
(read with the explicit key-file path) for which keys are present, yielding a
command only where they differ, so a second run plans no change. Creating a
key file from nothing shows the whole setup (`mkdir -p` + `chmod 700` +
`chown` for `~/.ssh`, then `touch` + `chmod 600` + `chown` for the file)
before the appends; the append command wraps its `>>` redirect in `sh -c` so
the open happens inside the elevated shell. Here the keys are managed for the
login user themselves, so no `sudo` is needed; managing another user's keys
would flag `sudo=True`. pyinfra's `delete_keys` option is deliberately dropped
(it rewrites the whole file through an elevated `files.put`, which reemote5
cannot elevate), so this operation only ever adds keys. The script is named
`server_user_authorized_keys.py` to keep it distinct from the read-only
`server_authorized_keys.py` fact example.

::: server_user_authorized_keys
    options:
      members: []

## Managing locales (pyinfra server.locale parity)

`server.locale` is deliberately not ported as a library operation — this
recipe is reemote5's answer, built on the generic `files.edit` primitive.
It is a **Debian-family recipe**: `/etc/locale.gen` and `locale-gen` exist only
on Debian/Ubuntu (the `locales` package), so it targets the `ubuntu` host, not
the CentOS `web1`/`web2` servers — RHEL-family hosts install locales through
`glibc-langpack-*` and `localedef` and have no `/etc/locale.gen`.
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

`server.sysctl` is superseded by the native
[`sysctl`](../../modules/sysctl.md) module (`sysctl.setting` /
`sysctl.conf`) — this recipe stays as the strict-parity demonstration,
built on the generic `files.edit` primitive (the `Sysctl` fact it compares
against relocated to that namespace as `sysctl.Sysctl`; the top-level
`reemote5.Sysctl` binding still resolves).
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
named `server_op_sysctl.py`; the read-only fact example relocated to
[`sysctl_sysctl.py`](sysctl.md), and everyday use should prefer the native
[`sysctl.setting`](../../modules/sysctl.md#sysctl-operations) operation.

::: server_op_sysctl
    options:
      members: []

## Ensuring a resource limit (pyinfra server.security_limit parity)

`server.security_limit` is deliberately not ported as a library operation —
like `server.etc_hosts` and `server.locale` it is a thin wrapper over
pyinfra's `files.line`, and reemote5 ships one generic editor instead:
this recipe, built on the `files.edit` primitive. The script snapshots
`FileContents("/etc/security/limits.conf")` from every host and computes
the desired content with the pure `security_limit_edit` function —
pyinfra's `files.line` lookup (anchored
`^{domain}\s+{limit_type}\s+{item}.*$`, the value *not* part of the
pattern, so a stale `* soft nofile 65535` line is corrected in place and
every match becomes the tab-formatted replacement; with no match the line
appends) done with a Python regex instead of a `FindInFile` grep, the
fields escaped literally where pyinfra interpolates them raw into a BRE.
Hosts whose content already matches are skipped in `main` — the script-level
idempotence pyinfra gets from its fact short-circuit. One deploy then
spools the content to `/tmp/limits.conf` through `files.edit` with a
closure-factory transform frozen between the snapshot and the run, and
installs it with a demo-side stateless operation: an elevated
`sh -c 'mv ... && chown root:root ... && chmod 0644 ...'` raw
`StringCommand`. `files.move` is deliberately *not* the install vehicle
here — `mv` preserves the spool's owner and mode, and a stock
`pam_limits`-read system file must land `root:root 0644` — and because
that step reads no facts and validates nothing in the prepare phase, the
spool and the install share one deploy instead of the two-run
`files.move` shape of the `etc_hosts` recipe.

::: server_security_limit
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

## Waiting for a port to listen

The same waiting design, asked of the kernel instead of systemd: `server.wait`
yields the very same `WaitForFactCommand` — its `port` argument is pyinfra's
only option — but polls the `Port` fact until its `listening` flag turns
true. The deploy starts httpd under `sudo`, then waits up to forty-five
seconds for TCP port 80 at a one second interval — a port on its way up
usually appears within a second, where a unit's start takes longer, which is
why the wait's default `interval` is `1.0` rather than `wait_service`'s
`5.0`. Reading the socket table is the stronger claim of the two: a
unit can report `active` while its daemon is still binding, and a service
that systemd does not manage opens its port all the same. Like every wait
the request reads no facts and is always planned as
a change, so both phases yield it identically. The poll runs unprivileged
(`ss -ltn` needs no root), and `Port` therefore keeps presence in its own
`listening` flag instead of inferring it from the owning process — an
unprivileged listing shows the socket but not `process`/`pid`, and
`is_listening` reads the flag alone. `protocol="udp"` waits the other family;
a port outside 1-65535 or an unknown protocol raises at plan time, before
anything is sent. The script is named `server_op_wait.py` to keep it distinct
from the read-only `server_port.py` fact example.

::: server_op_wait
    options:
      members: []
