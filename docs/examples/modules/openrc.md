# Module examples: openrc

Scripts under `examples/modules/openrc/` — one per fact or operation of the
[`openrc`](../../modules/openrc.md) module, plus two recipes that answer the
proposed `openrc.config` and `openrc.ensure` operations without adding
library surface. See the [Examples overview](../../examples.md) for the
shared "connects for real" warning and the error-reporting contract every
script follows.

The read-only fact-gathering examples (`openrc_openrc_status.py` reading
`OpenrcStatus`, `openrc_openrc_enabled.py` reading `OpenrcEnabled`,
`openrc_openrc_services.py` reading `OpenrcServices`,
`openrc_openrc_runlevels.py` reading `OpenrcRunlevels` and
`openrc_openrc_version.py` reading `OpenrcVersion`, all over `gather_fact`)
are not rendered here — they read a single fact, and the facts themselves
are documented on the
[`openrc` module page](../../modules/openrc.md). Like the apk fact
examples, they load the shared project-root `inventory.json` (validated up
front with `validate_inventory`) and filter the host list with the
`is_alpine` predicate *before* the fan-out, since `gather_fact` takes no
`when` parameter of its own — which also keeps the unguarded
`rc-status`/`rc-update` shells off the systemd hosts the inventory holds,
where they would fail with `CommandNotFoundError`.

## Managing a service

The service operation: `openrc.service` manages one service along two
independent axes — `running` compares the
[`OpenrcStatus`][reemote5.modules.openrc.openrc.OpenrcStatus] fact and
starts/stops with `rc-service`, `enabled` compares the
[`OpenrcEnabled`][reemote5.modules.openrc.openrc.OpenrcEnabled] fact and
assigns/unassigns the runlevel with `rc-update` — so one request can
converge runtime *and* boot state. The script targets `crond` (which every
stock Alpine install ships) and yields the same start-and-enable request
twice — the second is a guaranteed no-op in the apply phase, because the
first request's changes are visible to it through the fresh per-step fact
cache (in the prepare phase, where nothing is sent, both plan as changes)
— then a third request with `running=None, reloaded=True` that reloads the
service's configuration without touching its boot assignment. The reload
is gated on the service actually running: reloading a stopped service is
meaningless, so the flag yields nothing when the fact reads False at
materialization time. pyinfra's arbitrary `command` option is deliberately
dropped (`server.shell` covers the rare custom rc-service verb).
`rc-service`/`rc-update` write root-owned state, so the requests are
flagged `doas=True` and the driver elevates the commands — `doas` rather
than `sudo` because the Alpine host ships no `sudo` (a `sudo rc-service`
would fail with exit 127 before reaching OpenRC), and `doas` is
passwordless, so the host needs a `permit nopass` rule in `/etc/doas.conf`.

The inventory is read from the project root's `inventory.json` (validated
up front with `validate_inventory`), and every request carries
`when=is_alpine` — the request-level counterpart of the host-list filter
the fact scripts apply. The predicate reads only static inventory data
(`host.group`) and is a stable module-level function, so both phases reach
the same decision; the driver skips a rejected request without
materializing it, so on a non-OpenRC host neither the commands nor the
facts behind them run, and plan and log render
`[skipped: when guard is_alpine]` in the request's lockstep slot. The
shared inventory holds one Alpine host alongside hosts in other groups, so
the run manages crond on the Alpine host and skips every other one.

::: openrc_service
    options:
      members: []

## Assigning a service to a runlevel

The runlevel operation: `openrc.runlevel` is a reemote5 original with no
pyinfra counterpart — pyinfra folds the boot assignment into
`openrc.service`'s `enabled` option, so managing an assignment *without*
touching the runtime state needs the whole service operation with
`running=None`. This standalone operation is the dedicated
runlevel-membership verb: idempotent on the `OpenrcEnabled` fact,
`enabled=True` yields `rc-update add` only when the service is not
assigned to the runlevel, `enabled=False` yields `rc-update del` only when
it is. The script assigns `crond` to the `default` runlevel — the
boot-persistence half of what the service example does in one combined
request — yielding the same request twice so the second is a guaranteed
no-op in the apply phase. The removal direction is deliberately not
exercised: dropping crond from the only runlevel that starts it would
leave the shared host without cron after the example finishes. Like the
service example, the script reads the shared `inventory.json`, flags every
request `doas=True` (the runlevel tree is root-owned) and carries
`when=is_alpine` on each request, so the run assigns on the Alpine host
and skips every other one.

::: openrc_runlevel
    options:
      members: []

## Creating and stacking a custom runlevel

The custom-runlevel operation: `openrc.custom_runlevel` manages the
runlevel *directories themselves* under `/etc/runlevels`, where
`openrc.runlevel` only manages membership in an existing runlevel. A
runlevel is a directory of service symlinks — rc-update(8) ships no verb
that creates one — so `present=True` yields `mkdir -p` when the
[`OpenrcRunlevels`][reemote5.modules.openrc.openrc.OpenrcRunlevels] fact
does not list it, and `stacked_to="default"` yields `rc-update -s add` so
entering `default` also enters the custom runlevel. Removal unstacks
first (one `rc-update -s del` per target whose list names the runlevel,
in sorted order) then `rm -rf`s the directory: rc-update(8) documents no
runlevel-deletion verb, and a bare directory delete would leave dangling
stack symlinks. The script runs a full create → no-op → remove cycle on
an `async` runlevel, so the shared host is left exactly as it was found —
and the two phases tell the story differently: the plan, computed against
the pre-deployment snapshot, shows the removal as "does not exist", while
the apply log's fresh per-step fact cache *sees* the runlevel the first
step created and unstacks and removes it. A plan is a prediction from one
snapshot; the apply phase decides each step from live state. Validation
(an empty name, stacking onto itself) raises `ValueError` in the prepare
phase before anything is sent. The requests are flagged `doas=True` and
carry `when=is_alpine`, like the other openrc operation examples.

::: openrc_custom_runlevel
    options:
      members: []

## Managing rc.conf (the openrc.config recipe)

No bespoke `openrc.config` operation exists: setting a variable in
`/etc/rc.conf` (or a service's `/etc/conf.d/<service>`) is a file edit fully
expressible with the generic
[`files.edit`][reemote5.modules.files.files.edit] content-transform
primitive plus the elevated
[`files.move`][reemote5.modules.files.files.move] — the same
spool-and-install recipe as the sysctl and hosts-file examples. The script
ensures `rc_parallel="YES"`, OpenRC's parallel startup switch, read when a
runlevel is entered (so the edit takes effect at the next boot — there is
no runtime half). It filters the inventory to the `alpine` group *before*
the `FileContents` snapshot, so `/etc/rc.conf` is never even read on a
systemd host; a pure transform drops every existing `rc_parallel`
assignment (commented ones included) and appends the wanted line; a
controller-side gate compares the desired content against the frozen
snapshot, so re-running the script after a successful install is a no-op.
Run 1 spools the content to `/tmp/rc.conf` with `files.edit`
(unprivileged — reemote5's transfers never elevate); run 2 installs it
with `files.move` (`dest="/etc"`, `overwrite=True`, `doas=True`). The two
runs cannot merge: `files.move` validates `src` in the prepare phase too,
and prepare never sends, so the spool file must really exist first. Each
pending host's transform is built once by a closure factory between the
snapshot and run 1, keeping it stable across both phases.

::: openrc_config
    options:
      members: []

## Install and enable in one deploy (the openrc.ensure composition)

No combined `openrc.ensure` operation exists either: the two halves
already compose in a deploy —
[`apk.packages`][reemote5.modules.apk.apk.packages] installs the package,
[`openrc.service`][reemote5.modules.openrc.openrc.service] enables and
starts the service it ships — and the driver's lockstep execution gives
the composition its ordering guarantee: every host finishes the install
step before any host begins the service step, so the service request
always sees the init script the install just delivered, through its fresh
per-step fact cache. The script demonstrates the pair on `nginx` (Alpine's
package ships `/etc/init.d/nginx`), then yields the same pair again — both
repeats are guaranteed no-ops in the apply phase, and re-running the whole
script later is likewise a no-op, each half idempotent against its own
facts with no combined state machine to maintain. Every request is
flagged `doas=True` and carries `when=is_alpine`: neither apk nor OpenRC
exists on the systemd hosts the shared inventory also holds, so the guard
keeps both halves off them.

::: openrc_ensure
    options:
      members: []