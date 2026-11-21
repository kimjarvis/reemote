# Module examples: server

Scripts under `examples/modules/server/` — one per fact or operation of the
[`server`](../../modules/server.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The read-only fact-gathering examples (`server_arch.py`, reading `Arch`,
`server_authorized_keys.py`, reading `AuthorizedKeys` for the login user,
`server_date.py`, reading `Date`,
`server_env.py`, reading `Env` for `PATH` and `HOME`,
`server_group_details.py`, reading `GroupDetails`,
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
`server_python_version.py`, reading `PythonVersion` for the default
`python3` interpreter,
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

Like the dnf examples, every script reads the shared project-root
`inventory.json` and guards on the `"centos"` group by default
(`when=is_centos` on every request, a host-list filter before each
`gather_fact` fan-out) — the one exception is the locale example below,
which guards on `is_ubuntu` because it is Debian-family only.

## Ensuring system groups

`server.group` in action: the deploy creates the `docker` group (the baseline's
documented example), a `lusers` system group with a fixed gid, and pins the
`developers` group to a specific gid, all under `sudo`. The operation is
idempotent — it compares each name against the `Groups` fact and yields a
`GroupAddCommand` only where the group is absent, so a second run plans no
change. `system=True` renders `groupadd -r` and `gid=1500` renders
`--gid 1500`; `system` only applies at creation, but `gid` reconciles too
(issue #62) — for a group that is already present, the `GroupDetails` fact
supplies its current numeric id, and a different, known one is repaired with
a `GroupModCommand` (`groupmod -g`) rather than left drifted. An unknown
current gid (a malformed `/etc/group` row) is left alone rather than guessed
at, and repairing a gid never `chown`s files still owned by the old numeric
id — the same limitation Ansible's `group` module has. Removing a group is
the mirror image: `present=False` yields a `GroupDelCommand` only when the
group is present.

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
reboots), the bare `hostname` command on older systems. The baseline's
`hostname_file` upload branch is deliberately dropped — reemote's
transfers cannot be elevated, so the root-owned `/etc/hostname` write has
no faithful equivalent; on a systemd host `hostnamectl` already persists
the name. The script is named `server_op_hostname.py` to keep it distinct
from the read-only `server_hostname.py` fact example.

::: server_op_hostname
    options:
      members: []

## Killing a process by PID

`server.kill` in action, and the determinism-safe way to "find the PID,
then kill it" in ONE deploy. Like the baseline, the operation takes an explicit
`pid` (and an optional `signal`, default `TERM`), so the script first takes
a `Processes` snapshot via `gather_fact` across the guarded hosts, then builds the
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

`server.modprobe` in action: the deploy loads the `nbd` module and then
ensures `loop` and `brd` are loaded, both under `sudo` because `modprobe`
needs root. The operation is idempotent — it compares the names against the
`KernelModules` fact and yields a `ModprobeCommand` only for the modules that
differ from the request, so a host where `loop` is already loaded re-loads
nothing and a second run plans no change. `module` accepts a single name or a
tuple of names, `force=True` would render `modprobe -f` (without ever forcing
a reload of an in-state module), and unloading is the mirror image:
`present=False` yields `modprobe -r -a` only for the names that are loaded. A
request needs the `modprobe` binary (the `kmod` package) and the `.ko` file of
each named module under the *running* kernel, so the script first drives an
idempotent `dnf.packages` install of `kmod` and `kernel-modules-core` — the
package shipping `nbd`, `loop` and `brd` — before the load deploy reads its
facts. The dependency on the running kernel's tree is why these modules are
chosen over anything in `kernel-modules-extra`: installing that package only
populates `/lib/modules` for the newest installed kernel, so on a host not yet
rebooted into it `modprobe` would report the module as "not found in
directory" — a real `CommandError`, which the script reports as one line (the
same path an unknown module or a module still in use on removal takes).

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
instead, and that found mount point is what an unmount or remount acts on.
Like the baseline, `/etc/fstab` is never touched — a
mount made this way does not survive a reboot unless an fstab entry is
managed separately, which is `server.fstab`'s job (the next section), not
a `files.edit` recipe: transfers never elevate, so the root-owned fstab
file is not writable that way. The operation itself requires an existing
mount target
directory and a present device, so the script provisions its own
prerequisites before deploying: `main` first awaits `provision_mounts` from
the sibling `provision.py`, which creates `/data` and `/scratch` with
`files.directory` and attaches a 64M loop-backed ext4 image as `/dev/sdb1`
(the demo hosts have no spare disk, only `/dev/sda`). Both steps are
idempotent and scoped to the `centos` group; the loop attachment does not
survive a reboot, but provisioning re-attaches the existing image without
reformatting it. A `mount`/`umount` that still exits non-zero (a busy
filesystem on unmount, for instance) fails the run with a typed
`CommandError`, which the script reports as one line.

::: server_mount
    options:
      members: []

## Setting the timezone

`server.timezone` in action: the deploy ensures every host keeps
`Europe/Amsterdam` (the baseline's documented example), under `sudo`. The
operation is idempotent — it compares the requested zone against the
`Timezone` fact and yields a `SetTimezoneCommand` (`timedatectl
set-timezone`) only where they differ, so a second run plans no change.
It is supported on **systemd systems only**: when a change is needed the
`Which` fact probes `timedatectl`, and a host without it raises
`OperationError` during the prepare phase — before anything is sent —
rather than falling back. The baseline's non-systemd fallback (symlinking
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
`ensure_home` is the baseline's `files.directory` delegation, inlined: with
`create_home=False` (the default) `useradd` builds nothing from the skeleton,
so the plan shows the `mkdir -p /home/deploy` plus `chown deploy:deploy
/home/deploy` pair that actually creates the directory. The append step also
makes the per-phase fact cache visible: in the plan both user requests read
the same pre-deployment state and both show a change, while in the apply each
step re-reads the facts, so the fourth step sees the groups the third wrote
and sends only `usermod -a -G staff deploy`. The baseline's
`public_keys`/`delete_keys` options are deliberately dropped (key files are
managed through the separate `server.user_authorized_keys` operation), as
are its plan-time
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
and are owned correctly, then writes the public keys not already listed —
the baseline's documented flow with its controller-side key-file paths dropped
(every `public_keys` entry here is a literal key string, and it is a tuple
because `op()` arguments must stay hashable). The operation is idempotent —
it reads the `Home` fact to resolve the directory, a private `_KeysPathStat`
probe for the directory/file owner and mode, and the `AuthorizedKeys` fact
(read with the explicit key-file path) for which keys are present, yielding a
command only where they differ, so a second run plans no change. Creating a
key file from nothing shows the whole setup (`mkdir -p` + `chmod 700` +
`chown` for `~/.ssh`, then `touch` + `chmod 600` + `chown` for the file)
before the write. The write itself is a **spool-and-install** pair: the
operation reads the file's current text through the `AuthorizedKeysFile`
raw-content fact, appends the missing keys controller-side (comments and
ordering preserved), scps the result to a `/tmp` spool named after the key
file, then installs it with `sh -c 'mkdir -p … && cp … && chmod 600 … &&
rm -f …'`. The scp half stays unprivileged even under `sudo=True` — transfers
never elevate — so the log shows two privilege levels in one request. Here
the keys are managed for the login user themselves, so no `sudo` is needed;
managing another user's keys would flag `sudo=True`. The baseline's `delete_keys`
option is supported too (a whole-file rewrite to exactly the requested keys,
riding the same spool-and-install pair), though this example only appends.
The script is named
`server_user_authorized_keys.py` to keep it distinct from the read-only
`server_authorized_keys.py` fact example.

::: server_user_authorized_keys
    options:
      members: []

## Managing /etc/hosts entries

`server.etc_hosts` in action: the deploy ensures the line keyed by
`10.0.0.5` holds exactly `db.internal db`, under `sudo` because
`/etc/hosts` is root-owned. It replaces the earlier two-run
`files.edit` → `files.move` recipe that used to live in the files
examples — the fact read, the pure hosts-file transform and the install
now live in one operation, so a single deploy suffices. The operation is
idempotent: it compares the parsed `EtcHosts` fact first (the baseline's
no-op reasons verbatim, order-sensitive on the hostnames), and only a
differing entry triggers the write — the `AuthorizedKeysFile`
raw-content fact reads the file's current text, the transform rewrites
just the IP's line (every other line, comment and blank line preserved),
and the result rides a **spool-and-install** pair: an unprivileged scp to
a `/tmp` spool named after the managed path, then an elevated
`sh -c 'mkdir -p … && cp … && chmod 0644 … && rm -f …'` install. The scp
half stays unprivileged even under `sudo=True` — transfers never elevate
— so the log shows two privilege levels in one request. `hostnames` is a
tuple because `op()` arguments must stay hashable; `present=False` with
names drops only those, without names removes every line for the IP. The
deploy yields the same request twice, so the apply log makes the
per-phase fact cache visible: the second step sees the entry the first
wrote and reports no change.

::: server_etc_hosts
    options:
      members: []

## Persisting an fstab mount entry

`server.fstab` in action: the deploy ensures the entry keyed by
`/swapfile` reads exactly `/swapfile none swap sw 0 0` — the geerlingguy
swap role's central declaration, Ansible `mount`'s `name: none` form,
under `sudo` because `/etc/fstab` is root-owned. This is the persistent
half `server.mount` deliberately does not touch, and the surface the
earlier docs wrongly called a `files.edit` recipe: a transfer never
elevates, so the root-owned file could never be written that way. The
operation is idempotent: it compares the parsed `Fstab` fact first (an
equivalent line in another spacing — tabs for spaces — reads as no
change, not a duplicate), unrequested `options`/`dump`/`passno` columns
merge from the current entry on an update like Ansible rewriting only
the differing fields, and only a differing entry triggers the write —
the `AuthorizedKeysFile` raw-content fact reads the file's current text,
the transform rewrites just the matching line (every other line,
comment and blank line preserved), and the result rides a
**spool-and-install** pair: an unprivileged scp to a `/tmp` spool named
after the managed path, then an elevated `sh -c 'mkdir -p … && cp … &&
chmod 0644 … && rm -rf …'` install. The scp half stays unprivileged
even under `sudo=True`, so the log shows the family's two privilege
levels in one request. `options` is a tuple because `op()` arguments
must stay hashable (a comma-separated string works too); `present=False`
with the device removes the entry again, the role's `state: absent`
path. Activating the swap (`swapon`) is out of scope (issue #47), so the
entry itself is inert until something runs `swapon` — managing the line
is all fstab persistence requires. The deploy yields the same request
twice, so the apply log makes the per-phase fact cache visible: the
second step sees the entry the first wrote and reports no change. The
script mutates the shared demo inventory for real: the entry stays in
each guarded host's `/etc/fstab`, and re-running it is a no-op.

::: server_fstab
    options:
      members: []

## Managing locales (baseline server.locale parity)

`server.locale` in action: the deploy ensures `fr_FR.UTF-8` is enabled
under `sudo`, the baseline parity for `/etc/locale.gen`. It is a
**Debian-family operation**: `/etc/locale.gen` and `locale-gen` exist only
on Debian/Ubuntu (the `locales` package), so it targets the `ubuntu` host, not
the CentOS `web1`/`web2` servers — RHEL-family hosts install locales through
`glibc-langpack-*` and `localedef` and have no `/etc/locale.gen`, and a
request there raises `OperationError` in the prepare phase.
The operation is **spool-and-install**: in one request it reads the
`Locales` fact (`locale -a`) and the raw `/etc/locale.gen` text, applies
the baseline's change condition (enable only when the locale is absent from
`locale -a`, disable only when present) and definition-line lookup — a
Python regex instead of a `FindInFile` grep, `OperationError` with
the baseline's messages on zero or multiple matches — then scp's the
transformed text to a deterministic `/tmp` spool as the login user,
installs it over `/etc/locale.gen` with an elevated `sh -c` (`chmod 0644`,
spool removed) and finishes with one elevated `locale-gen`. The log shows
the family's two privilege levels in one request: an unprivileged
`$ scp <locale.gen> -> ...` line followed by `$ sudo sh -c '...'` and
`$ sudo locale-gen`. Re-running the script is a no-op once `locale -a`
reports the requested state, and a definition line already in the desired
state skips only the write, never the regeneration. The script replaces
the earlier two-run `files.edit` -> `files.move` recipe: the fact reads,
edit, install and regeneration now live inside the operation, so a single
deploy suffices. It is named `server_op_locale.py` to
keep it distinct from the read-only `server_locales.py` fact example.

::: server_op_locale
    options:
      members: []

## Ensuring a sysctl setting (baseline server.sysctl parity)

`server.sysctl` is superseded by the native
[`sysctl`](../../modules/sysctl.md) module (`sysctl.setting` /
`sysctl.conf`) — this recipe stays as the strict-parity demonstration,
built on the generic `files.edit` primitive (the `Sysctl` fact it compares
against relocated to that namespace as `sysctl.Sysctl`; the top-level
`reemote.Sysctl` binding still resolves).
The script snapshots `Sysctl` for `fs.file-max` and
`FileContents("/etc/sysctl.conf")` from every guarded host, then applies the baseline's
two independent decisions to the frozen snapshots: the runtime gate is
the baseline's fact comparison on the normalized value (lists join with spaces
for the shell, digits become ints so `"2000"` and `2000` compare equal),
and the persist gate compares the desired content — computed by the pure
`sysctl_persist_edit` function with the baseline's `files.line` semantics
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
[`sysctl.setting`](../../modules/sysctl.md#reemote.modules.sysctl.sysctl.setting) operation.

::: server_op_sysctl
    options:
      members: []

## Ensuring a resource limit (baseline server.security_limit parity)

`server.security_limit` in action: the deploy ensures `* soft nofile 1024`
is set under `sudo`, the baseline parity for `/etc/security/limits.conf`. It
is a **spool-and-install** operation, and it replaces the earlier
`files.edit` + demo-side install recipe that used to live in this file: the
fact read, the line edit and the install now live inside one operation, so a
single deploy suffices. In one request it reads the raw
`/etc/security/limits.conf` text through the `AuthorizedKeysFile` content
fact and applies the baseline's anchored `files.line` lookup — a Python regex
instead of a `FindInFile` grep, the value *not* part of the pattern, so a
stale `* soft nofile 65535` line is corrected in place and every match
becomes the tab-formatted replacement; with no match the line appends — the
fields escaped literally where the baseline interpolates them raw into a BRE.
It then scp's the transformed text to a deterministic `/tmp` spool as the
login user and installs it over `/etc/security/limits.conf` with an elevated
`sh -c` (`chmod 0644`, spool removed). The log shows the family's two
privilege levels in one request: an unprivileged `$ scp <limits.conf> -> ...`
line followed by `$ sudo sh -c '...'`. The `chmod 0644` reset matters —
`pam_limits` reads the file as every user, so a login-user mode would
silently stop the limits applying. Re-running the script is a no-op once the
file carries the line (the content-level fixpoint), and `value` accepts both
an `int` and a symbolic string such as `"unlimited"`. Every request carries
`when=is_centos`, the static-inventory guard, so hosts outside the `"centos"`
group are skipped without connecting.

::: server_security_limit
    options:
      members: []

## Generating an SSH key pair

`server.SshKeyPair` and `server.ssh_keypair` in action: the script first
gathers the fingerprint of the login user's conventional `~/.ssh/id_rsa.pub`
across the guarded hosts with `gather_fact` — the read-only half of the
surface, two-tier so a key inside another user's `700` `.ssh` is retried
elevated, and `None` means there is no pair yet — then deploys three requests,
all under `sudo`: an unprotected `ed25519` pair commented with `user@host`,
the identical request again (the per-phase fact cache, so the plan shows both
as changes while the apply log makes the second a no-op), and an `rsa` pair at
the default `~/.ssh/id_rsa` with the conventional 4096 bits. The operation
is idempotent — it reads `Home` to resolve the directory, a private
`_KeysPathStat` probe for that directory's owner and mode, and the
`SshKeyPair` fact for the public half — so a user with no `~/.ssh` yet gets
`mkdir -p` + `chmod 700` + `chown` before the pair, while a host that already
has one plans nothing at all. The generation is one `ssh-keygen` command
wrapped in `sh -c` and run as the target user through `su -s /bin/sh <user>`,
so the pair is born owned by them (`600` private, `644` public) with no
follow-up chown — and that su is why the requests carry `sudo=True` even for
the login user's own home. The type drives the default file name
(`id_ed25519` here) and only `rsa`/`rsa1` carry `size` as `-b`, so the
ed25519 line in the log has no `-b` in it. No request passes a `passphrase`:
passphrase-protected generation is out of scope — following `gpg.gen_key`, no
secret may ride a plan, a log or a `/tmp` spool on the target — so a non-empty
one raises `ValueError` while the request materializes, in the prepare phase,
before a fact is read or a command sent. Every request carries `when=is_centos`, the
static-inventory guard, applied by hand to the `gather_fact` fan-out too,
which takes no `when` of its own. The script mutates the demo hosts for real —
the key pairs stay in place — and re-running it is a no-op; `force=True` is the
only thing that would regenerate one.

::: server_ssh_keypair
    options:
      members: []

## Starting a service and waiting for it

The waiting design in action: `server.service` starts and enables httpd under
`sudo` (idempotently — it reads the unit's `ActiveState`/`UnitFileState` and
yields `systemctl` commands only where the unit differs), then
`server.wait_service` yields a `WaitForFactCommand` that the driver *polls*
over SSH every two seconds until `SystemdServiceState` reports `active`, or
raises `WaitTimeoutError` after sixty. The wait is always planned as a change
— like the baseline's `server.wait` it reads no facts, so the request sequence is
identical in both phases; whether the unit is already active is discovered by
the first poll. Both hosts finish the start step before either polls, and the
two poll loops then run concurrently.

::: wait_for_httpd
    options:
      members: []

## Waiting for a port to listen

The same waiting design, asked of the kernel instead of systemd: `server.wait`
yields the very same `WaitForFactCommand` — its `port` argument is the baseline's
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
