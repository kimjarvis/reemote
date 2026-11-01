# server module

`reemote5.modules.server.server` combines the server facts and the server
operations in a single source, so the read side (`Arch`, `AuthorizedKeys`,
`Date`, `Groups`, `HasGui`, `Home`, `Hostname`, `Kernel`, `KernelModules`,
`KernelVersion`, `Last`, `LinuxDistribution`, `LinuxGui`, `LinuxName`,
`Locales`, `LsbRelease`, `Mounts`, `Path`, `Processes`, `Sysctl`,
`SystemdServiceState`,
`SystemdServiceEnabled`, `Timezone`, `Uptime`, `User`, `Users`, `Which`)
sits beside the `server.group`, `server.hostname`, `server.kill`,
`server.modprobe`, `server.mount`, `server.service`, `server.timezone` and
`server.wait_service`
operations that consume them. It is also where reemote5's
**waiting** design lives: `server.wait_service` is the first operation that
blocks until a condition on the host holds.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a generator —
implemented as a `process` coroutine returning the raw value (over asyncssh in
the real implementation). Read facts through
[`FactCache.get`][reemote5.core.FactCache.get], which is itself a coroutine,
so results are awaited and cached within a phase step. The two systemd facts
take a service name and normalize it with
[`unit_name`][reemote5.modules.server.server.unit_name] (`httpd` →
`httpd.service`), so the test framework's simulated state is keyed by full
unit names too. `SystemdServiceState` returns the raw `ActiveState` string
rather than a boolean so a predicate can decide what counts as ready —
`activating` is deliberately *not* `active`, which is what a wait polls
through. `Arch` takes no arguments: it runs `uname -m` and returns the
machine's architecture string (e.g. `x86_64`), mirroring pyinfra's
`server.Arch`. `AuthorizedKeys` takes a username (and an optional explicit
`path`) and returns the SSH public keys listed in that user's
`~/.ssh/authorized_keys` as full key strings in file order, skipping blank
lines and `#` comments; a missing file reads as `[]`. The default path
resolves the home through shell tilde expansion (`~user`), which cannot be
quoted, so an unsafe username raises `ValueError` instead of reaching the
shell — mirroring pyinfra's `server.AuthorizedKeys`. `Date` takes no
arguments: it runs `date +'%Y-%m-%dT%H:%M:%S%z'` and parses the ISO 8601
timestamp into a timezone-aware `datetime`, mirroring pyinfra's
`server.Date`; empty output (the command failed) reads as `None` — pyinfra's
`default = datetime.now` fallback to the control machine's clock is
deliberately dropped. `Groups` takes no arguments: it cats `/etc/group` and
returns the first `:`-separated field of every colon-bearing line — the
group names in file order, with no dedup or sorting — mirroring pyinfra's
`server.Groups`; lines without a colon are skipped and empty output reads
as `[]`. `HasGui` takes no arguments: it runs `ls /usr/bin/*session || true`
and returns `True` when any listed path is a known desktop-session binary
(GNOME, MATE, LXDE, KDE Plasma, XFCE 4), mirroring pyinfra's `server.HasGui`;
empty output reads as `False`. pyinfra implements it as a short fact over
`LinuxGui`; reemote5 keeps both facts, sharing the same shell and
known-binary map — `HasGui` is the boolean projection of `LinuxGui`'s list
of GUI names. `Home` takes an optional username
(an empty one means the login user) and runs `echo ~user`, letting the
shell's tilde expansion resolve the home directory (e.g. `/home/pyinfra`),
mirroring pyinfra's `server.Home`; the username is validated rather than
quoted, so an unsafe one raises `ValueError`. Empty output reads as `None`,
and so does an unexpanded `~user` echo — the shell's answer for an unknown
user, which pyinfra returns as the literal string; reemote5 deliberately
maps it to `None` so a missing user never masquerades as a path. `Hostname`
takes no arguments: it runs `uname -n` and returns the server's hostname
string (e.g. `my-host.net`), mirroring pyinfra's `server.Hostname`; the
first non-blank line is the answer and empty output (the command failed)
reads as `""`, just like `Arch`. `Kernel` takes no arguments: it runs
`uname -s` and returns the kernel name string (e.g. `Linux`, `Darwin`,
`FreeBSD`), mirroring pyinfra's `server.Kernel`; the first non-blank line is
the answer and empty output reads as `""`, just like `Arch`. pyinfra's
deprecated `server.Os` alias for the same fact is deliberately dropped.
`KernelModules` takes no arguments: it reads the `Kernel` fact to pick its
shell — FreeBSD lists `kldstat | tail +2`, every other kernel cats
`/proc/modules` — and returns a dict of module name → info dict, mirroring
pyinfra's `server.KernelModules`. On Linux each info dict holds `size`
(the raw string), `instances` (an int), `state` (e.g. `Live`) and, only
when the row lists dependencies, a `depends` list; on FreeBSD it holds
`id`, `refs`, `address` and `size`, all raw strings with hex `size` and
`address`. A failed read (no `/proc/modules`) maps to `{}`, pyinfra's
`default = dict`. `KernelVersion` takes no arguments: it runs `uname -r`
and returns the kernel release string (e.g. `6.1.0-26-amd64`), mirroring
pyinfra's `server.KernelVersion`; the first non-blank line is the answer
and empty output reads as `""`, just like `Arch`. pyinfra's deprecated
`server.OsVersion` alias for the same fact is deliberately dropped.
`Last` takes no arguments: it runs `last` (util-linux
`-Fwi` for full timestamps and IPs, falling back to `-w` on FreeBSD and the
bare command on busybox) and returns the login records as a list of
`{user, tty, host, time}` dicts, mirroring pyinfra's `server.Last`. Parsing
is intentionally light: `time` holds the raw trailing string (whitespace
collapsed to single spaces) so callers can re-parse their system's date
format; a `reboot` row's `system boot` counts as one tty and a row whose
remainder starts with a weekday has no host (`""`). Blank lines,
`wtmp`/`btmp begins` footers and header rows are skipped; empty output —
`last` missing or failed, swallowed by `|| true` — reads as `[]`, pyinfra's
`default = list`. `LinuxDistribution` takes no arguments: it dumps every
`/etc/*-release` file (each preceded by its path and followed by a `---`
separator) and replays the parts into a scratch `etc` directory that the
`distro` library parses — the same library pyinfra uses — returning
`{name, major, minor, release_meta}`, mirroring pyinfra's
`server.LinuxDistribution`. `major`/`minor` come from the most precise
source available (a distro-specific release file supplies the minor when
`/etc/os-release` exposes only a major version); `minor` is `None` when no
source provides it (e.g. CentOS Stream). `name` is the pretty form of the
distro id (`centos` → `CentOS`), `release_meta` holds the upper-cased
`/etc/os-release` key/values, empty release files (Arch's
`/etc/arch-release`) are skipped, and no release files at all read as
`{"name": None, "major": None, "minor": None, "release_meta": {}}`,
pyinfra's `default`. `LinuxGui` takes no arguments: it runs the same
`ls /usr/bin/*session || true` as `HasGui` and returns the GUI names of the
recognised session binaries (e.g. `["GNOME"]`) in listing order, mirroring
pyinfra's `server.LinuxGui`; lines are matched exactly, unknown lines are
ignored, and empty output reads as `[]`, pyinfra's `default = list`.
`LinuxName` takes no arguments: it returns just the distribution's pretty
name (e.g. `Ubuntu`), mirroring pyinfra's `server.LinuxName` — a shortcut
for the `name` key of `LinuxDistribution`. pyinfra implements it as a short
fact over `LinuxDistribution`; reemote5 likewise delegates to that fact's
`process`, sharing its shell, `distro` parsing and `linux_distribution`
simulated seam. No release files at all read as `None` (the `name` of
pyinfra's `default()`). `Locales` takes no arguments: it runs `locale -a`
and returns the installed locale names with lowercase `utf8` replaced by
`UTF-8` (to match the names in `/etc/locale.gen`), mirroring pyinfra's
`server.Locales`; lines are kept verbatim otherwise — the command's order
is preserved, with no dedup or sorting — and empty output (the command
failed) reads as `[]`, pyinfra's `default = list`; a missing `locale`
exits 127 and raises `CommandNotFoundError` instead.
`LsbRelease` takes no arguments: it runs `lsb_release -ca` and returns the
release information as a dict (e.g. `{"id": "Ubuntu", "description":
"Ubuntu 18.04.3 LTS", "release": "18.04", "codename": "bionic"}`),
mirroring pyinfra's `server.LsbRelease`. Keys are lower-cased and reduced
to their last word (`Distributor ID` → `id`), values are stripped, and
only the first colon splits — lines without one (the `No LSB modules are
available.` hint, blank lines) are skipped. Empty output (a failed read)
parses to `{}`; pyinfra's `requires_command` gating is deliberately
dropped. A *missing* `lsb_release` exits 127, which `run_shell` reports as
[`CommandNotFoundError`][reemote5.hosts.CommandNotFoundError] rather than
an empty dict. `Mounts` takes no arguments: it reads the `Kernel`
fact to pick its shell — FreeBSD lists `mount -p --libxo json`, OpenBSD
`mount -v`, every other kernel cats `/proc/self/mountinfo` — and returns a
dict of mount point → `{device, type, options}` dict, mirroring pyinfra's
`server.Mounts`. On Linux the octal escapes in mount points, devices and
super-block options (`\040` space, `\054` comma, `\134` backslash) are
decoded, the optional `shared`/`master`/`propagate_from`/`unbindable` tags
are skipped, and `options` combines the per-mount options with the
super-block ones — `ro`/`rw` are filtered from the super-block side
because the per-mount options override them. On OpenBSD the `ctime=...`
flag is dropped and non-matching lines are skipped. Empty output reads as
`{}` on every branch, pyinfra's `default = dict` (its FreeBSD JSON branch
would raise on empty output — a failed read here simply reports no mounts).
`Path` takes no arguments: it runs `echo $PATH` and returns the login
user's `PATH` environment variable as the raw colon-separated string
(e.g. `/usr/bin:/bin`), mirroring pyinfra's `server.Path` — the string is
not split into a list, so callers can `split(":")` themselves. The first
non-blank line is the answer and empty output — the command failed, or
`PATH` is unset or empty — reads as `""`, just like `Arch`.
`Processes` takes an optional `pid`: it reads the `Kernel` fact and probes
for BusyBox to pick its shell — Linux lists `ps -eo
pid,user,stat,%cpu,%mem,comm,args --no-headers`, FreeBSD the same `ps -eo`
without `--no-headers`, BusyBox (Alpine) the limited `ps -o
pid,user,vsz,stat,args` columns, and Darwin runs `ps` twice (`args` last,
then `comm` last, joined by a marker line) because macOS truncates any
non-last text column — and returns a dict of PID → `{user, state,
cpu_percent, mem_percent, command, args}` dict, mirroring pyinfra's
`server.Processes`. A `pid` restricts the listing to that one process (an
absent PID reads as `{}`); on BusyBox the percents are always `0.0` and
`command` is the basename of `args`' first word, and on Darwin a PID that
vanished between the two `ps` snapshots is dropped. Header rows, blank
lines, short rows and non-integer PIDs are skipped, and empty output reads
as `{}`, pyinfra's `default = dict`. The BusyBox detection reads the `Which`
fact (`command -v busybox || true`), exactly like pyinfra; one deliberate
deviation remains: a missing Darwin marker line raises `ValueError` rather
than pyinfra's bare `Exception`. A non-int `pid` raises `TypeError` on both
transports — the BusyBox branch interpolates it into an `awk` program,
where shell quoting cannot protect it. `Sysctl` takes an optional `keys`
list: it runs `sysctl -a 2>/dev/null || true` (or `sysctl <key> ...` for the
named keys, each shell-quoted) and returns a dict of setting name → value,
mirroring pyinfra's `server.Sysctl`. Rows separate on `=` (Linux) or `:`
(BSD); a row carrying neither is skipped. A value made only of
alphanumeric, `_`, `-`, `.` and whitespace characters is split on
whitespace and each token int-converted when possible, a single token
collapsing to a scalar (so `1024\t65535` reads as `[1024, 65535]`);
anything else stays the stripped raw string. A whitespace-only value
keeps `""` while a row with nothing after the separator is skipped
(pyinfra's truthiness check), and empty output — an unknown key or a
failed read, swallowed by `|| true` — reads as `{}`, pyinfra's
`default = dict`. `Timezone` takes no arguments: it
runs `readlink -f /etc/localtime | sed 's|.*/zoneinfo/||'` and returns the
current system timezone (e.g. `Europe/Amsterdam`), mirroring pyinfra's
`server.Timezone`; when `/etc/localtime` is a copied file rather than a
symlink into zoneinfo, the resolved path is returned verbatim, exactly like
pyinfra. The first non-blank line is the answer and empty output (the
command failed) reads as `""`, just like `Arch` — pyinfra's `output[0]`
would raise on empty output. `Uptime` takes no arguments: it reads the
`Kernel` fact to pick its shell — Darwin and FreeBSD print the current
epoch seconds followed by the boot-time epoch seconds (`date +%s` plus a
`sysctl kern.boottime` pipeline) and the uptime is their difference, every
other kernel runs `cut -d. -f1 /proc/uptime` — and returns the number of
seconds the system has been up (e.g. `86400` — one day), mirroring
pyinfra's `server.Uptime`. Blank lines are skipped; empty output (the
command failed) reads as `None`, pyinfra's `FactBase` default, so a failed
read never masquerades as a freshly booted host. A non-integer line raises
`ValueError`, and a BSD answer missing its second line raises
`IndexError`, exactly like pyinfra's parser. `User` takes no arguments: it
runs `echo $USER` and returns the current user's name (e.g. `root`),
mirroring pyinfra's `server.User`; the first non-blank line is the answer
and empty output — the command failed, or `USER` is unset or empty — reads
as `""`, just like `Path`. `Users` takes no arguments: it runs pyinfra's
shell loop joining each `/etc/passwd` entry with `id`'s primary and
secondary groups, the `lastlog` (`lastlogin` on BSD) row and the shadow
password hash, and returns a dict of username → `{home, comment, shell,
group, groups, uid, gid, lastlog, login_time, password}` dict, mirroring
pyinfra's `server.Users`. Empty passwd fields read as `None`, `groups`
holds only the secondary groups, `uid`/`gid` are ints, `lastlog` is the
raw matched timestamp (`None` when the user never logged in) and
`password` is the raw hash string (`""` when the shadow file cannot be
read). `login_time` is parsed with the two fixed `strptime` layouts —
Linux `lastlog` (aware `datetime`) and BSD `lastlogin` (naive) — instead
of pyinfra's fuzzy dateutil parse, a deliberate deviation that avoids the
extra dependency; an unmatched layout leaves `login_time` as `None`.
Empty output reads as `{}`, pyinfra's `default = dict`, and a row missing
its four `|` separators raises `ValueError`, like pyinfra's parser.
`Which` takes a command name: it runs `command -v <command> || true` and
returns the path the shell would run for that name (e.g.
`/usr/local/bin/python`), mirroring pyinfra's `server.Which`; the name is
`shlex`-quoted so shell metacharacters are looked up literally, never
executed, and a builtin or alias reads as its own name. The first non-blank
line is the answer; empty output — the command is unavailable, its non-zero
exit swallowed by `|| true` — reads as `None`, pyinfra's `FactBase` default,
so callers can branch on the answer directly. `Processes` reads this fact
for its BusyBox probe.

::: reemote5.modules.server.server
    options:
      members:
        - Arch
        - AuthorizedKeys
        - Date
        - Groups
        - HasGui
        - Home
        - Hostname
        - Kernel
        - KernelModules
        - KernelVersion
        - Last
        - LinuxDistribution
        - LinuxGui
        - LinuxName
        - Locales
        - LsbRelease
        - Mounts
        - Path
        - Processes
        - Sysctl
        - unit_name
        - SystemdServiceState
        - SystemdServiceEnabled
        - Timezone
        - Uptime
        - User
        - Users
        - Which

## Operations

Operations live in namespace modules so a deploy reads `server.group`,
`server.hostname`, `server.kill`, `server.modprobe`, `server.mount`,
`server.service`, `server.timezone` and
`server.wait_service`,
mirroring pyinfra's API. Every
operation is an async
generator taking an [`OpContext`][reemote5.core.OpContext], awaiting facts via
`ctx.facts`, setting `ctx.changed`/`ctx.reason` where a change is needed, and
yielding one [`Command`][reemote5.commands.Command] descriptor per mutation.

`server.group` mirrors pyinfra's `server.group` for `group`, `present`,
`system` and `gid`: it compares the name against the `Groups` fact and yields
a `GroupAddCommand` or `GroupDelCommand` only when the group differs from the
request. The `Kernel` fact picks the command form — FreeBSD gets
`pw groupadd`/`pw groupdel -n`, every other kernel `groupadd`/`groupdel` —
the same dispatch `KernelModules` uses (pyinfra reads its deprecated `Os`
fact, renamed `Kernel` upstream). `system` renders `groupadd -r` but is
ignored on BSD kernels, which have no system groups, and `gid` renders
`--gid` (`-g` on FreeBSD); a falsy gid (`None` or `0`) is skipped, exactly
like pyinfra's `if gid:` check. Both options only apply at creation — an
existing group is never modified, so a gid or system change on a present
group plans no command. The commands need root, so flag the request
`sudo=True`.

`server.hostname` mirrors pyinfra's `server.hostname` for the hostname
itself: it compares the requested name against the `Hostname` fact and
yields a `SetHostnameCommand` only when they differ. The `Which` fact picks
the command form — `hostnamectl set-hostname` when `hostnamectl` is
available (the systemd tool persists the name itself), the bare `hostname`
command on older systems without it — and the name is shell-quoted, so a
hostname carrying metacharacters is set literally. The command needs root,
so flag the request `sudo=True`. pyinfra's `hostname_file` option is
deliberately dropped: on systems without `hostnamectl` pyinfra also uploads
the name to a persistent file (`/etc/hostname` on Linux, `/etc/myname` on
OpenBSD) through `files.put`, which pyinfra can elevate — reemote5's
transfers are always unprivileged, so that root-owned write cannot ride a
transfer and the whole file branch is omitted rather than silently run as
the login user. On a systemd host nothing is lost, since `hostnamectl`
already persists the name.

`server.kill` mirrors pyinfra's `server.kill` for `pid` and `signal`
(default `TERM`): stateless and non-idempotent, it reads no fact and always
yields one `KillCommand` rendering `kill -<signal> <pid>`, so every planned
kill shows as a change and a repeat request kills again. The signal flag
and the PID are shell-quoted, so a signal string carrying metacharacters
reaches `kill` literally. Discovering the PID is the caller's job: read the
`Processes` fact via `gather_fact` before the run and close over the frozen
snapshot in a deploy factory (see the
[kill example](../examples/modules/server.md#killing-a-process-by-pid)) —
the synchronous deploy generator cannot await a fact, and branching it on
live process state would raise
[`DeterminismError`][reemote5.drivers.DeterminismError]. Killing another
user's process needs root, so flag the request `sudo=True`. A PID that
exited between the snapshot and the apply makes `kill` exit non-zero, which
fails the run with `CommandError` — pyinfra behaves the same.

`server.modprobe` mirrors pyinfra's `server.modprobe` for `module`,
`present` and `force`: it compares the requested names against the
`KernelModules` fact and yields one `ModprobeCommand` only for the modules
that differ from the request — `modprobe -a` loads just the missing ones,
`modprobe -r -a` unloads just the loaded ones (pyinfra's partition).
`module` accepts a single string or a list/tuple of names (prefer tuples in
`op()` requests, which must stay hashable), `present=False` picks the
unload direction, and `force=True` renders `-f` without ever turning an
in-state request into a reload. Each name is shell-quoted, so a name
carrying metacharacters reaches `modprobe` literally. Names are compared
verbatim — the kernel treats `-` and `_` as equivalent in module names,
but neither pyinfra nor reemote5 normalizes the spelling. The commands
need root, so flag the request `sudo=True`.

`server.mount` mirrors pyinfra's `server.mount` for `path`, `mounted`,
`options`, `device` and `fs_type`: it compares against the `Mounts` fact
and yields one `MountCommand` only where the mount differs from the
request — a fresh `mount [-t fs_type] [-o options] [device] path` when the
mount point is absent, an `umount` when `mounted=False` finds it present,
and a `mount -o remount,<options> <path>` when an already-mounted
filesystem is missing some of the requested options. When `path` is not
found directly but `device` is given, the mount is located by device
instead (pyinfra's fallback for paths whose spelling differs from
`/proc/self/mountinfo`), and that found mount point is what an unmount or
remount acts on. The options check is a subset test, not equality: extra
options already on the mount never trigger a remount, and a remount always
carries the full requested option set. On a BSD kernel the remount takes
the `mount -uo <options> -t <type> <device> <path>` form instead — the
`Kernel` fact picks it, and the type/device come from the fact. `options`
accepts a list/tuple of option strings (prefer tuples in `op()` requests,
which must stay hashable), and every path, device and option string is
shell-quoted, so a value carrying metacharacters reaches `mount` literally.
Like pyinfra, `/etc/fstab` is never touched — persistence across reboots is
a `files.edit` recipe. The commands need root, so flag the request
`sudo=True`.

`server.service` is the idempotent half, mirroring pyinfra's `systemd.service`
for `service`, `running`, `restarted`, `enabled` and `daemon_reload`: it
compares the unit against the two facts and yields a
`SystemctlCommand` only
where the unit differs from the request (`daemon_reload` and `restarted` are
stateless and always yield when set). The `systemctl` commands need root, so
flag the request `sudo=True`. pyinfra's `reloaded`, `command`, `user_mode`,
`machine` and `user_name` options are not supported.

`server.timezone` mirrors pyinfra's `server.timezone` for `timezone`, on
**systemd systems only**: it compares the requested zone against the
`Timezone` fact and yields a `SetTimezoneCommand` (`timedatectl
set-timezone`, which also persists the zone across reboots) only when they
differ. The zone is shell-quoted, so a name carrying metacharacters is set
literally. When a change is needed the `Which` fact probes `timedatectl`,
and a host without it raises
[`OperationError`][reemote5.core.OperationError] during the prepare phase
— before anything is sent — rather than falling back; an already-set zone
is a no-op on any host, like pyinfra, which also compares the fact before
probing for the tool. The command needs root, so flag the request
`sudo=True`. pyinfra's non-systemd fallback — symlinking
`/usr/share/zoneinfo/<timezone>` onto `/etc/localtime` and echoing the zone
into `/etc/timezone` — is deliberately dropped: reemote5 supports this
operation on systemd systems only, where `timedatectl` already persists
the zone.

pyinfra's `server.locale` is deliberately not ported: the `/etc/locale.gen`
edit is fully expressible with the generic `files.edit` transform primitive,
and its `locale-gen` step is a raw shell command. The
[locale recipe](../examples/modules/server.md#managing-locales-pyinfra-serverlocale-parity)
snapshots `Locales` and `FileContents`, applies pyinfra's change condition
and definition-line lookup controller-side (a Python regex over the content
instead of a `FindInFile` grep), spools the desired content to a writable
path, then installs it with an elevated `files.move` and regenerates — no
bespoke operation and no new descriptor.

pyinfra's `server.sysctl` is deliberately not ported either: its runtime
half is a raw `sysctl key=value` shell command gated by the `Sysctl` fact,
and its `persist` half edits root-owned `/etc/sysctl.conf` through
`files.line` — fully expressible with the generic `files.edit` transform
primitive. The
[sysctl recipe](../examples/modules/server.md#ensuring-a-sysctl-setting-pyinfra-serversysctl-parity)
snapshots `Sysctl` and `FileContents`, applies pyinfra's value
normalization and `files.line` semantics controller-side, applies the live
value with an elevated raw command, spools the desired config to a
writable path and installs it with an elevated `files.move` in a second
deploy — no bespoke operation and no new descriptor.

::: reemote5.modules.server.server
    options:
      members:
        - group
        - hostname
        - kill
        - modprobe
        - mount
        - service
        - timezone

## Waiting for a condition

pyinfra waits in two ways: a remote-side shell loop (`server.wait` polls
`netstat` in a `while` loop *on the host*) or a control-side Python callback
(`server.reboot` yields a `FunctionCommand` that sleeps and re-reads the
`Uptime` fact until the host is back). reemote5 takes the second shape and
makes it a descriptor: `server.wait_service` yields a
`WaitForFactCommand`,
the second kind of *non-shell* descriptor after the transfers. It has no
`shell()`; [`send_command`][reemote5.drivers.send_command] dispatches on the
[`WaitCommand`][reemote5.commands.WaitCommand] base and awaits the
descriptor's `wait`, which polls the fact every `interval` seconds until the
predicate holds or `timeout` expires and
[`WaitTimeoutError`][reemote5.commands.WaitTimeoutError] aborts the run. Like
a transfer it is unprivileged — `sudo`/`su` do not apply to a poll.

Three constraints follow from the execution model:

- **The operation body never polls.** The prepare phase never sends, and its
  `FactCache` persists across a host's requests, so a loop inside the
  generator would spin forever against a value that never changes. The
  operation only *describes* the wait; the driver performs it at send time.
- **The wait is planned unconditionally.** `server.wait_service` reads no
  facts and always sets `changed=True`, like pyinfra's non-idempotent
  `server.wait`. Gating it on a fact would read pre-deployment state in
  prepare and post-start state in apply, and branching on that would raise
  [`DeterminismError`][reemote5.drivers.DeterminismError]. Whether the unit is
  *already* active is discovered by the first poll — a zero `timeout` still
  polls once, so an already-met condition always succeeds.
- **The poll bypasses the cache.** `WaitForFactCommand.wait` calls
  `fact_cls.process` directly, never `ctx.facts.get`: the per-step cache would
  pin the first value and the loop would never observe the change.

`WaitForFactCommand` is generic — any fact class, any arguments, any predicate
— so a module can wait for a port, a file hash or a dnf lock with the same
descriptor. Its fields end up compared by value in plans, so pass module-level
fact classes and predicates (such as
[`is_active`][reemote5.modules.server.server.is_active]), never a lambda built
inside the generator: the deploy is re-invoked per phase and a fresh lambda
per invocation would not compare equal.

In the apply phase the lockstep barrier guarantees every host has finished
the preceding step (the `systemctl start`) before any host begins polling,
and the per-host poll loops then run concurrently; a slow host delays only
the step barrier, not the other hosts' polling.

The test framework's `SimulatedHost` holds `services` (unit → `ActiveState`)
and `enabled_services` as the seams the facts read, mutates them on
`SystemctlCommand`, and evaluates a `WaitForFactCommand` *once* without
sleeping: in-memory state never changes on its own, so the condition either
already holds or the simulated wait raises `WaitTimeoutError` immediately.

::: reemote5.modules.server.server
    options:
      members:
        - wait_service
        - is_active

## Example

The deploy from
[`wait_for_httpd`](../examples/modules/server.md#starting-a-service-and-waiting-for-it):

```python
def deploy(host: Host) -> Generator[OperationRequest]:
    yield op(
        server.service,
        name="Start httpd",
        service="httpd",
        running=True,
        enabled=True,
        sudo=True,
    )
    yield op(
        server.wait_service,
        name="Wait for httpd to become active",
        service="httpd",
        timeout=60.0,
        interval=2.0,
    )
```

On a host where httpd is stopped the plan reads:

```text
web1: Start httpd              [changed: httpd.service not running (inactive), will start; httpd.service not enabled, will enable]
web1: Wait for httpd to become active [changed: wait for httpd.service to be active]
```

and the apply log shows the commands that ran, the wait rendered in its
loggable form:

```text
web1: Start httpd              [changed: ...]
    $ sudo systemctl start httpd.service
    $ sudo systemctl enable httpd.service
web1: Wait for httpd to become active [changed: wait for httpd.service to be active]
    $ wait for SystemdServiceState('httpd.service') <is_active> every 2.0s up to 60.0s
```

On a second run `Start httpd` reports no change while the wait is planned
again and passes on its first poll.
