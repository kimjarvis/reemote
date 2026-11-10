[//]: # (## openrc module)

::: reemote5.modules.openrc.openrc
    options:
      show_root_heading: false
      members:
        - OpenrcConfigFile
        - OpenrcEnabled
        - OpenrcRunlevels
        - OpenrcServices
        - OpenrcStatus
        - OpenrcVersion
        - config
        - custom_runlevel
        - runlevel
        - service



<!---

`reemote5.modules.openrc.openrc` combines the OpenRC facts and the OpenRC
operations in a single source. OpenRC is the init system of Alpine Linux
(and Gentoo): services are `/etc/init.d` scripts managed at runtime with
`rc-service`, and *runlevels* are directories under `/etc/runlevels`
holding one symlink per assigned service, managed with `rc-update`. The
facts are `OpenrcStatus` and `OpenrcEnabled`, ports of the baseline's two OpenRC
facts — the per-runlevel name → running bool parsed from
`rc-status <runlevel>` rows, and the name → enabled-in-runlevel bool parsed
from `rc-update show -v`'s `name | runlevels` columns — plus four reemote5
originals widening the visibility the baseline's pair leaves missing:
`OpenrcServices`, the same `rc-status` rows kept as their *raw* state words
(`started`, `stopped`, `crashed`, ...) so a crashed service is
distinguishable from a cleanly stopped one, `OpenrcRunlevels`, the
runlevel → assigned-names mapping parsed from `ls -R /etc/runlevels`, and
`OpenrcVersion`, the installed OpenRC release parsed from
`openrc --version` (`None` on a host without OpenRC), and
`OpenrcConfigFile`, the verbatim text of `/etc/rc.conf` or a service's
`/etc/conf.d/<service>` — the raw-content fact behind `openrc.config`.
Beside the facts live the `openrc.service` operation (baseline parity:
running/restarted/reloaded/enabled/runlevel), and the `openrc.runlevel`,
`openrc.custom_runlevel` and `openrc.config` originals — standalone
runlevel-membership and runlevel-directory management (including
stacking), and spool-and-install management of the OpenRC configuration
files.

Deliberately absent: an `openrc.mask` operation — OpenRC has no masking
mechanism (no systemd-style symlink-to-/dev/null), so masking cannot be
honestly emulated; disable the service in every runlevel instead. A
combined install-and-enable `openrc.ensure` is likewise not an operation:
`apk.packages` followed by `openrc.service` composes in a deploy
(`examples/modules/openrc/openrc_ensure.py`).

## Facts

A fact is an asynchronous, queryable resource — never yielded by a
generator — implemented as a `process` coroutine returning the raw value
(over asyncssh in the real implementation). Read facts through
[`FactCache.get`][reemote5.core.FactCache.get], which is itself a
coroutine, so results are awaited and cached within a phase step.

`OpenrcStatus` takes an optional runlevel (default `"default"`) and runs
`rc-status <runlevel>`, mirroring the baseline's fact with the same row regex:
each `name [ state ]` row becomes a name → running-bool entry, where only
`started` reads True — `stopped`, `crashed` and every other state read
False. The optional uptime suffix a started service may carry
(`started 00:34:14 (10)`, `started 15 day(s) 00:34:14 (10)`) is matched and
ignored; the `Runlevel:` header and blank lines match no row. Deliberate
deviations from the baseline: the runlevel is `shlex`-quoted in the shell, and
`requires_command` gating is dropped — a host without `rc-status` exits
127, reported as
[`CommandNotFoundError`][reemote5.hosts.CommandNotFoundError].

`OpenrcEnabled` takes the same optional runlevel argument but runs one
fixed shell, `rc-update show -v`: the `-v` lists *every* service as a
`name | runlevel ...` row (an unassigned service has an empty right-hand
side), and the argument selects only the membership test — `crond |
default` reads True for `"default"`, False for `"boot"`. This is the
boot-persistence view, complementary to `OpenrcStatus`'s runtime view: a
service can be enabled but stopped, or running but not enabled. A line
carrying no `|` is skipped instead of crashing the split (the baseline's
parser raises on a stray header; this one stays total). The unguarded
command surfaces a missing `rc-update` as `CommandNotFoundError`.

`OpenrcServices` is the lossless sibling of `OpenrcStatus`: the same
`rc-status <runlevel>` shell and the same row regex, but the bracketed
state word is kept verbatim instead of projected to a bool, so the states
the baseline's fact collapses remain distinguishable — an audit can tell a
service that died (`crashed`) from one that was never started
(`stopped`). The uptime suffix is dropped exactly as `OpenrcStatus` drops
it, and a runlevel with no services reads as `{}`.

`OpenrcRunlevels` takes no arguments and lists the runlevel tree with
`ls -R /etc/runlevels 2>/dev/null || true` — a runlevel *is* a directory
of service symlinks (that is what `rc-update add`/`del` write), so the
listing is the whole mapping, busybox-compatible and needing no OpenRC
tooling. Unlike `rc-update show`, it reveals *empty* runlevels too, and a
*stacked* runlevel (`rc-update -s add async default`) appears as an
`async` entry inside `default`'s list while keeping its own section.
Entries keep listing order (ls sorts). The `|| true` guard reads a host
without OpenRC as `{}`, the missing-file tolerance the guarded apk facts
follow, so only
[`HostConnectionError`][reemote5.hosts.HostConnectionError] can escape the
gather.

`OpenrcVersion` takes no arguments and runs
`openrc --version 2>/dev/null || true`: the banner's last token is the
release (`openrc (OpenRC) 0.54.1` → `"0.54.1"`), and a host without OpenRC
prints nothing and reads `None`, the None-on-absent convention
`ApkPackage` and `server.Which` follow. The guarded command makes it a
safe capability probe — only `HostConnectionError` can escape it.

`OpenrcConfigFile` takes a path and returns its verbatim text as
`str | None`: `test -e <path> && { printf '<marker>\n'; cat -- <path>; }`
reads the file only when it exists, and the printed marker line separates
*absent* (`None`, no output) from *present but empty* (`""`) — the
distinction the unset direction needs so it can no-op on a missing file
yet still act on an empty leftover. It is the dedicated raw-content fact
of the spool-and-install family (`AptSourcesFile`, `ApkRepositoriesFile`,
`DnfRepoFile` follow the same shape): the transform edits the file's own
text, comments and ordering intact, never a parsed reconstruction. The
bare `&&` needs no `|| true` — a missing file exits non-zero with empty
stdout, which reads as `None`; only `CommandNotFoundError` (a shell
without `cat`) and `HostConnectionError` can escape the gather.

## Operations

Operations live in namespace modules so a deploy reads `openrc.service`,
mirroring the baseline's API. Every operation is an async generator with the
same fixed signature:

```python
async def operation(ctx: OpContext, *args, **kwargs) -> AsyncGenerator[Command, None]:
```

It awaits facts via `ctx.facts`, sets `ctx.changed` and `ctx.reason` where
it decides a change is needed, and yields one descriptor per command that
would mutate the host. All four operations write root-owned state, so
flag the request `sudo=True`, `doas=True` or `su=True` — stock Alpine
ships no `sudo`, so `doas` is usually the escalator on an OpenRC host:
reemote5 runs it passwordless as `doas -n`, which needs a `permit nopass`
rule for the login user in `/etc/doas.conf` and raises `PrivilegeError`
without one.

`openrc.service` manages one service along two independent axes (the baseline's
`openrc.service` parity). `running=True` compares the `OpenrcStatus` fact
and yields `rc-service <name> start` only when the service is not running;
`running=False` stops it, `running=None` leaves the runtime state alone.
`restarted=True` and `reloaded=True` are the non-idempotent verbs, each
gated on the service actually running — restarting or reloading a stopped
service is meaningless, so both yield nothing when the fact reads False.
`enabled=True`/`False` compares the `OpenrcEnabled` fact for `runlevel`
(default `"default"`) and yields `rc-update add`/`del` only on a
difference; `enabled=None` (the default) leaves the boot state alone.
the baseline's arbitrary `command` option is deliberately dropped — an escape
hatch for any rc-service verb defeats the typed-descriptor model;
`server.shell` covers the rare custom verb, and the keyword is absorbed by
`**kwargs` with no effect.

`openrc.runlevel` is a reemote5 original with no baseline counterpart:
the baseline folds the boot assignment into `openrc.service`'s `enabled`
option, so managing an assignment *without* touching the runtime state
needs the whole service operation with `running=None`. This standalone
operation is the dedicated runlevel-membership verb: idempotent on
`OpenrcEnabled`, `enabled=True` yields `rc-update add <service>
<runlevel>` only when the service is not assigned, `enabled=False` yields
`rc-update del` only when it is. There is no existence validation —
the baseline's enabled branch does not check the init script either; a service
without an `/etc/init.d` script, or an unknown runlevel, fails in
`rc-update` itself at apply time (`CommandError`).

`openrc.custom_runlevel` is the other original: it manages the runlevel
directories themselves. `present=True` yields `mkdir -p
/etc/runlevels/<name>` only when the `OpenrcRunlevels` fact does not list
the runlevel (rc-update(8) ships no verb that creates one), and
`stacked_to="<target>"` additionally yields `rc-update -s add <name>
<target>` only when the name is not already an entry of the target's
list — a stacked runlevel is a symlink inside the target directory, so
entering the target also enters the stacked runlevel. Removal is the
interesting direction: rc-update(8) documents no verb that *deletes* a
runlevel, and deleting the directory alone would leave dangling stack
symlinks, so `present=False` unstacks first — one `rc-update -s del` per
target whose list names the runlevel, in sorted target order so the
request sequence is stable across phases — then `rm -rf` of the
directory. `stacked_to` is ignored on removal: it always unstacks from
every target the facts report. Validation raises `ValueError` while
materializing (an empty name, stacking onto itself); `stacked_to` must
name an existing runlevel or `rc-update` fails at apply time. The standard
runlevels are not special-cased — removing `boot` or `default` is legal
OpenRC-wise and the operation does not guard against it.

`openrc.config` is the third original — a reemote5 operation with no
the baseline counterpart (the baseline's `openrc` ships only `service`) and a member
of the spool-and-install write family (`files.install`, `apt.repo`,
`apk.repository`, ...). It manages one shell-variable assignment in
`/etc/rc.conf` (`service=None`, the default) or `/etc/conf.d/<service>`:
`present=True` sets `key="value"`, `present=False` drops every assignment
of the key — commented-out ones included, so a stale `#rc_parallel=NO`
cannot shadow the intent. The `OpenrcConfigFile` fact supplies the file's
current text; a pure transform drops all matching assignment lines and
(set only) appends the double-quoted line, preserving every other line,
comment and ordering, so the result is a fixpoint — when the transformed
text equals the current text the operation is a no-op with a reason
naming the managed path. The write itself is two descriptors: an
unprivileged scp of the whole transformed text to a `/tmp` spool named
after the managed path (`/tmp/reemote5-openrc-config-<sha1(path)[:16]>`,
so two requests targeting different files can never share a spool), then
an elevated `sh -c 'mkdir -p ... && cp ... && chmod 0644 ... && rm -f
...'` install — transfers never elevate, so the request's `doas`/`sudo`
applies to the install half only, by design. Values are always rendered
double-quoted and validation raises `ValueError` while materializing for
a key that is not a shell variable name, a service name carrying path
characters, or a value containing shell-active characters (`"`, `\`,
`$`, backtick, newline) — no escaping scheme, the unsafe value is
rejected outright. Unset ignores `value` entirely. Like the runlevel
verbs there is no runtime half: `rc.conf` is read when a runlevel is
entered, so a change takes effect at the next boot.

--->


<!---

## Example

The read-only fact-gathering scripts under
[`examples/modules/openrc/`](../examples/modules/openrc.md) load the shared
project-root inventory `inventory.json` — validated with
`validate_inventory` before any host is materialized — and guard the
gather with an `is_alpine` `when` predicate that filters the host list
before `gather_fact`'s fan-out, since `gather_fact` takes no `when`
parameter of its own. They gather `OpenrcStatus`
(`openrc_openrc_status.py`), `OpenrcEnabled` (`openrc_openrc_enabled.py`),
`OpenrcServices` (`openrc_openrc_services.py`), `OpenrcRunlevels`
(`openrc_openrc_runlevels.py`) or `OpenrcVersion`
(`openrc_openrc_version.py`) on the Alpine hosts only with
[`gather_fact`][reemote5.drivers.gather_fact], printing each result as
JSON.

The operation scripts beside them demonstrate `openrc.service`
(`openrc_service.py`, start-and-enable twice plus a reload),
`openrc.runlevel` (`openrc_runlevel.py`, assign twice) and
`openrc.custom_runlevel` (`openrc_custom_runlevel.py`, a full
create-stack → no-op → remove cycle) and `openrc.config`
(`openrc_config.py`, set `rc_parallel="YES"` twice — spool-and-install on
the first, no-op on the second). `openrc_ensure.py` composes
`apk.packages` + `openrc.service` in one deploy, answering the proposed
`openrc.ensure`.
All are rendered on the [openrc examples page](../examples/modules/openrc.md).

--->
