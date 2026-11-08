# server module

<!---

The systemd surface that used to live here — the `SystemdServiceState` and
`SystemdServiceEnabled` facts, the `unit_name` normalization helper and the
`service` operation — has been **superseded by the
[`systemd`](systemd.md) namespace** (`systemd.SystemdServiceState`,
`systemd.SystemdServiceEnabled`, `systemd.unit_name`, `systemd.service`).
`server` re-exports them for backward compatibility, and `server.service` is
now a deprecated alias of `systemd.service`; new code should use the `systemd`
namespace. `server.wait_service` and its `is_active` predicate stay here,
polling the relocated `SystemdServiceState`.

The `Sysctl` fact has likewise relocated — to the [`sysctl`](sysctl.md)
namespace (`sysctl.Sysctl`) — but **without** a backward-compatibility
re-export: `server` no longer exports it, and the top-level `reemote5.Sysctl`
binding now points at `sysctl.Sysctl`. The `sysctl` namespace also owns the
persistence facts (`SysctlFile`, `SysctlFiles`) and the `setting`/`conf`
operations that supersede the `server_op_sysctl.py` recipe for everyday use.

The `Path` fact has been **superseded by `Env`**: `Env` with the name
`PATH` issues the same `echo $PATH` shell and returns the same raw
colon-separated string, while any other environment variable can now be
read through the same fact. `Path` is no longer exported.

`server.script` is an **action**, not an operation: an alias of
[`Script`][reemote5.actions.Script] from `reemote5.actions`, driven by
`gather_action` outside the deploy model. It runs a script file already on
the host — stage it with `files.put` in a deploy first — executing an
executable file directly (shebang honoured) and a non-executable one through
`sh`, with an explicit `interpreter=` overriding both. A missing file comes
back as an error `Result` (exit 127), never an exception. See the
[Actions reference](../actions/actions.md).

--->

## server Facts

::: reemote5.modules.server.server
    options:
      show_root_heading: false
      members:
        - Arch
        - AuthorizedKeys
        - Date
        - Env
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
        - Port
        - Processes
        - Timezone
        - Uptime
        - User
        - Users
        - Which

## server Returns

::: reemote5.modules.server.server
    options:
      show_root_heading: false
      members:
        - PortInfo

## server Operations

::: reemote5.modules.server.server
    options:
      show_root_heading: false
      members:
        - group
        - hostname
        - kill
        - modprobe
        - mount
        - timezone
        - user
        - user_authorized_keys
        - wait
        - wait_service


<!---

`reemote5.modules.server.server` combines the server facts and the server
operations in a single source, so the read side (`Arch`, `AuthorizedKeys`,
`Date`, `Env`, `Groups`, `HasGui`, `Home`, `Hostname`, `Kernel`, `KernelModules`,
`KernelVersion`, `Last`, `LinuxDistribution`, `LinuxGui`, `LinuxName`,
`Locales`, `LsbRelease`, `Mounts`, `Port`, `Processes`,
`Timezone`, `Uptime`, `User`, `Users`, `Which`)
sits beside the `server.group`, `server.hostname`, `server.kill`,
`server.modprobe`, `server.mount`, `server.timezone`, `server.user`,
`server.user_authorized_keys`,
`server.wait` and
`server.wait_service`
operations that consume them. It is also where reemote5's
**waiting** design lives: `server.wait_service` is the first operation that
blocks until a condition on the host holds, and `server.wait` is the second
-- it asks the same question of the kernel's socket table through the `Port`
fact.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a generator —
implemented as a `process` coroutine returning the raw value (over asyncssh in
the real implementation). Read facts through
[`FactCache.get`][reemote5.core.FactCache.get], which is itself a coroutine,
so results are awaited and cached within a phase step. `Arch` takes no arguments: it runs `uname -m` and returns the
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
deliberately dropped. `Env` takes a variable name: it runs `echo $<name>`
and returns that environment variable's value from the login shell as the
raw string (e.g. `/usr/bin:/bin` for `PATH`) — an original reemote5 fact
that replaces the migrated `server.Path` (`Env` with the name `PATH`
issues the same shell and returns the same string, not split into a list,
so callers can `split(":")` themselves). The name is validated rather than
quoted — `$<name>` must stay a bare word for the shell to expand it — so
an unsafe name raises `ValueError` instead of reaching the shell. The
first non-blank line is the answer and empty output — the variable is
unset or empty — reads as `""`, just like `Arch`. `Groups` takes no arguments: it cats `/etc/group` and
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
`Port` takes a port and an optional protocol (default `tcp`): it reads the
`Kernel` fact to pick its tool — FreeBSD runs `sockstat -l -p <port> -P
<protocol>`, every other kernel prefers Linux `ss -lp<flag>n | grep
':<port> ' || true` and falls back to `netstat -<flag>lnp ... | awk ...`
when the `Which` fact reports no `ss` — and returns a
[`PortInfo`][reemote5.modules.server.server.PortInfo] record: `listening`
says whether the port has at least one listening socket, `process` and `pid`
name the owner of the first row that carries one. `listening` is decided by
any row surviving the port filter, so it is tool- and
privilege-independent, while the owner pair is best-effort: an unprivileged
listing shows the socket but not the process behind it, so `process`/`pid`
come back `None` even though the port is up. That is the deliberate deviation
from pyinfra: its `(process, pid)` tuple cannot tell "absent" from "present
but unowned" — which is why its own `ss-tcp-no-process-info` fixture (two
LISTEN rows, no process information) answers `[null, null]` — and it is the
shape `server.wait` needs, since a wait is never elevated. The port and
protocol are validated before anything is sent (`_check_port`): a non-int
port raises `TypeError`, one outside 1-65535 or a protocol other than
`tcp`/`udp` raises `ValueError`, and both values are shell-quoted after the
check, so they can never reach the command line as syntax. Empty output — no
matching row — reads as `listening=False`, and a malformed PID cell reads as
no PID where pyinfra's parser would raise.
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
where shell quoting cannot protect it. `Sysctl` has relocated to the
`sysctl` namespace (`sysctl.Sysctl`, keeping pyinfra's `server.Sysctl`
parse verbatim); `server` no longer exports it, and the persistence facts
and management operations live there too. `Timezone` takes no arguments: it
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
as `""`, just like `Env`. `Users` takes no arguments: it runs pyinfra's
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


## Operations

Operations live in namespace modules so a deploy reads `server.group`,
`server.hostname`, `server.kill`, `server.modprobe`, `server.mount`,
`server.service`, `server.timezone`, `server.user`,
`server.user_authorized_keys`,
`server.wait` and
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

`server.service` is a deprecated alias of `systemd.service`, re-exported here
for backward compatibility; the operation now lives in the `systemd` namespace
with the enhanced option set (`reloaded`, `reset_failed`, `soft_reload`, `now`
and the `user_mode`/`machine`/`user_name` scope flags). See the
[`systemd` module page](systemd.md).

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

`server.user` mirrors pyinfra's `server.user` for `user`, `present`, `home`,
`shell`, `group`, `groups`, `append`, `ensure_home`, `create_home`, `system`,
`uid`, `comment`, `unique` and `password`: the requested state is compared
against the `Users` fact and a descriptor is yielded only where they differ —
a `UserDelCommand` when `present=False` finds the user, a `UserAddCommand`
when the user is absent, and a `UserModCommand` carrying just the differing
fields when it is present (pyinfra's per-field `mod_args`). The `Kernel` fact
picks the command form — FreeBSD gets `pw useradd`/`pw usermod`/`pw userdel
-n`, every other kernel the shadow-suite commands — the same dispatch
`server.group` and `KernelModules` use. `uid`, `system` and `unique` only
apply at creation: a present user is never re-id'd or turned into a system
account, a falsy `uid` (`None` or `0`) renders nothing, exactly like
pyinfra's `if uid:` check, and `system` is ignored on BSD kernels, which have
no system accounts. `create_home` renders `-m` and the opposite default `-M`
carries over only to kernels that have the skeleton flag at all — neither
FreeBSD nor OpenBSD does. `home` defaults to `/home/<user>` for an absent
user and to the recorded home for a present one, so omitting the option never
plans a spurious change, and a creation with no primary `group` reuses an
existing group of the user's name when there is one (pyinfra's "tell
`useradd` to use it" fix). `groups` is compared as a set, and with
`append=True` only the memberships the user lacks are written (`-a -G`) —
appending never removes one. Pass `groups` as a tuple rather than a list:
`op()` requests must stay hashable, so a list raises
[`UnhashableArgumentError`][reemote5.core.UnhashableArgumentError].
`password` is the *encrypted* hash. On Linux it rides the same `usermod -p`,
while FreeBSD `pw` has no modify flag for it and gets a separate
`echo '<hash>' | pw usermod -n <user> -H 0` pipe command, which is the only
command a password-only change plans there. Removal is the one direction
with no extras: `present=False` removes the user and nothing
else, since neither `userdel` nor pyinfra's branch touches the home directory
or any files.

`ensure_home` (on by default) is pyinfra's delegation to `files.directory`,
inlined: the server-local `_HomeDirectory` and `_HomeIsLink` probes decide
whether the home still needs work, and `_HomeMkdirCommand` and
`_HomeChownCommand` render the `mkdir -p` plus `chown <user>:<group>` pair.
An absent home is created and owned, a wrong owner is chowned, a symlinked
home is accepted as-is (pyinfra's `_no_fail_on_link=True`), and a home that
exists as something other than a directory raises
[`OperationError`][reemote5.core.OperationError] during the prepare phase —
before anything is sent. Like every other check in the operation, the home
probes are awaited in the generator body, so the request sequence stays
identical in both phases. Every command here needs root, so flag the request
`sudo=True`.

pyinfra's `public_keys`/`delete_keys` options are deliberately dropped: that
branch delegates to `server.user_authorized_keys`, which mixes an elevated
`files.directory` mode change, `files.line` appends and a `files.put`
rewrite of `~/.ssh/authorized_keys`, and reemote5 has no `files.line`
operation and only unprivileged transfers. Manage key files from the caller,
with the `AuthorizedKeys` fact and the `files` operations. pyinfra's
plan-time `Which` guards ("`useradd` is not installed", with its Alpine
shadow-package note) are dropped as well: a missing binary surfaces at apply
as `CommandNotFoundError` from the exit status, the convention every other
reemote5 operation follows.

`server.user_authorized_keys` mirrors pyinfra's `server.user_authorized_keys`
for `user`, `public_keys`, `group`, `authorized_key_directory` and
`authorized_key_filename`: it is the append half of the key-file branch
`server.user` above deliberately drops, extracted into its own operation.
Idempotent, it ensures the `.ssh` directory first (mode `700`, owned by
`user`/`group`, the `group` option defaulting to the user's own name exactly
as pyinfra's `group or user` does) and the `authorized_keys` file second
(mode `600`), each compared against the private `_KeysPathStat` in-namespace
probe of the shared `directory_stats`/`file_stats` seams — the
`files.Directory`/`files.File` facts it stands in for cannot be imported
across namespaces. A missing directory is `mkdir -p`'d, chmod'd and chown'd
(reusing `_HomeMkdirCommand`/`_HomeChownCommand` plus the private
`_KeysChmodCommand`/`_KeysTouchCommand` stand-ins for the chmod/touch verbs
that pair has no twin for); a wrong mode or owner re-runs just that half; a
path that exists as the wrong type raises
[`OperationError`][reemote5.core.OperationError] during the prepare phase.
Key idempotence then rides the `AuthorizedKeys` fact read with an explicit
`path` (never tilde-resolved): every requested key not already listed is
appended with one `AuthorizedKeysAppendCommand` in the caller's own order,
while keys already present are skipped. That descriptor renders pyinfra's
`files.line` add idiom — a newline guard so a key never glues onto a
newline-less last line — wrapped in `sh -c` so the `>>` redirect happens
*inside* the elevated shell; a bare `sudo echo ... >> <path>` would open the
redirect as the login user and fail on another user's `600` file. Writing
into another user's `~/.ssh` needs root, so flag the request `sudo=True`.

Two pyinfra behaviors are deliberately dropped (developer decisions). The
`delete_keys` option is not ported: that branch rewrites the whole file
through an *elevated* `files.put`, and reemote5 transfers are always
unprivileged, so only the append direction is this operation's deliverable —
it never removes a key already present (inspect with `AuthorizedKeys` and
prune with a file-rewriting recipe of your own). And a `public_keys` entry is
always a *literal* key string, never a controller-side file path: pyinfra's
`read_any_pub_key_file` `os.path.exists` branch clashes with the same
IO-only/no-controller-path rule `files.put`/`files.get`/`apt.key`/`dnf.key`
follow, and reading a controller file in both phases would risk a
[`DeterminismError`][reemote5.drivers.DeterminismError] if it changed in
between.

pyinfra's `server.locale` is deliberately not ported: the `/etc/locale.gen`
edit is fully expressible with the generic `files.edit` transform primitive,
and its `locale-gen` step is a raw shell command. The
[locale recipe](../examples/modules/server.md#managing-locales-pyinfra-serverlocale-parity)
snapshots `Locales` and `FileContents`, applies pyinfra's change condition
and definition-line lookup controller-side (a Python regex over the content
instead of a `FindInFile` grep), spools the desired content to a writable
path, then installs it with an elevated `files.move` and regenerates — no
bespoke operation and no new descriptor.

pyinfra's `server.sysctl` is superseded by the native [`sysctl`](sysctl.md)
namespace: `sysctl.setting` manages one parameter (runtime apply plus a
keyed persistence edit) and `sysctl.conf` manages a whole drop-in file.
The
[sysctl recipe](../examples/modules/server.md#ensuring-a-sysctl-setting-pyinfra-serversysctl-parity)
stays as the strict-parity demonstration: it snapshots `Sysctl` (now
`sysctl.Sysctl`) and `FileContents`, applies pyinfra's value
normalization and `files.line` semantics controller-side, applies the live
value with an elevated raw command, spools the desired config to a
writable path and installs it with an elevated `files.move` in a second
deploy — no bespoke operation and no new descriptor.

pyinfra's `server.security_limit` is deliberately not ported: like
`server.etc_hosts` it is a thin wrapper over `files.line`, and the
`/etc/security/limits.conf` edit is fully expressible with the generic
`files.edit` transform primitive. The
[security-limit recipe](../examples/modules/server.md#ensuring-a-resource-limit-pyinfra-serversecurity_limit-parity)
snapshots `FileContents`, applies pyinfra's anchored line lookup
controller-side (a Python regex over the content, fields escaped
literally), spools the desired content to a writable path and installs
it with an elevated `sh -c` `mv` plus `chown root:root`/`chmod 0644` —
no bespoke operation and no new descriptor.


## Waiting for a condition

pyinfra waits in two ways: a remote-side shell loop (`server.wait` polls
`netstat` in a `while` loop *on the host*) or a control-side Python callback
(`server.reboot` yields a `FunctionCommand` that sleeps and re-reads the
`Uptime` fact until the host is back). reemote5 takes the second shape and
makes it a descriptor: `server.wait` and `server.wait_service` each yield a
`WaitForFactCommand`,
the second kind of *non-shell* descriptor after the transfers. It has no
`shell()`; [`send_command`][reemote5.drivers.send_command] dispatches on the
[`WaitCommand`][reemote5.commands.WaitCommand] base and awaits the
descriptor's `wait`, which polls the fact every `interval` seconds until the
predicate holds or `timeout` expires and
[`WaitTimeoutError`][reemote5.commands.WaitTimeoutError] aborts the run. Like
a transfer it is unprivileged — `sudo`/`su` do not apply to a poll.

`server.wait` mirrors pyinfra's `server.wait` for its one option, `port`, and
deviates in how the polling happens. pyinfra ships a `while true` shell loop
over `netstat` with a fixed one second sleep and *no* timeout, so a port that
never opens hangs the run forever; here the wait is described rather than
scripted, so the driver bounds it with `timeout` and `interval` is adjustable.
`wait` defaults to a 1.0s interval where `wait_service` uses 5.0s — a port on
its way up usually appears within a second, where a unit's start takes longer
— and takes `protocol` (`tcp` or `udp`) because the socket table is read per
family. The polled fact is `Port`, the predicate
[`is_listening`][reemote5.modules.server.server.is_listening], which reads the
`listening` flag alone: because the poll is unprivileged it may well see the
socket without seeing its owner, and the wait is about the port, not about who
owns it. `server.wait_service` is the same descriptor over
`SystemdServiceState` and `is_active`; which of the two a deploy uses depends
on whether "ready" means *the unit reports active* or *the socket accepts
connections* — the port is the stronger claim, since a unit can report
`active` while its daemon is still binding, and a service systemd does not
manage opens its port all the same. A bad port or protocol raises at plan
time, before the wait is ever sent.

Three constraints follow from the execution model:

- **The operation body never polls.** The prepare phase never sends, and its
  `FactCache` persists across a host's requests, so a loop inside the
  generator would spin forever against a value that never changes. The
  operation only *describes* the wait; the driver performs it at send time.
- **The wait is planned unconditionally.** `server.wait` and
  `server.wait_service` read no facts and always set `changed=True`, like
  pyinfra's non-idempotent `server.wait`. Gating them on a fact would read
  pre-deployment state in
  prepare and post-start state in apply, and branching on that would raise
  [`DeterminismError`][reemote5.drivers.DeterminismError]. Whether the unit is
  *already* active (or the port *already* open) is discovered by the first
  poll — a zero `timeout` still
  polls once, so an already-met condition always succeeds.
- **The poll bypasses the cache.** `WaitForFactCommand.wait` calls
  `fact_cls.process` directly, never `ctx.facts.get`: the per-step cache would
  pin the first value and the loop would never observe the change.

`WaitForFactCommand` is generic — any fact class, any arguments, any predicate
— so a module can wait for a file hash or a dnf lock with the same
descriptor; `server.wait` is precisely that reuse over `Port`. Its fields end
up compared by value in plans, so pass module-level
fact classes and predicates (such as
[`is_active`][reemote5.modules.server.server.is_active] and
[`is_listening`][reemote5.modules.server.server.is_listening]), never a lambda
built inside the generator: the deploy is re-invoked per phase and a fresh
lambda per invocation would not compare equal.

In the apply phase the lockstep barrier guarantees every host has finished
the preceding step (the `systemctl start`) before any host begins polling,
and the per-host poll loops then run concurrently; a slow host delays only
the step barrier, not the other hosts' polling.

The test framework's `SimulatedHost` holds `services` (unit → `ActiveState`)
and `enabled_services` as the seams the facts read, mutates them on
`SystemctlCommand`, and holds `listening_ports` (the `(protocol, port)` →
`PortInfo` table `Port.process` reads) as a read-only seam — nothing in the
library's operation set opens a socket, so no command ever mutates it. Either
way the simulated host evaluates a `WaitForFactCommand` *once* without
sleeping: in-memory state never changes on its own, so the condition either
already holds or the simulated wait raises `WaitTimeoutError` immediately.

::: reemote5.modules.server.server
    options:
      show_root_heading: false
      members:
        - is_active
        - is_listening

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

--->