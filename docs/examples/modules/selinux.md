# Module examples: selinux

Scripts under `examples/modules/selinux/` — one per fact or operation of the
[`selinux`](../../modules/selinux.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The read-only fact-gathering examples (`selinux_se_boolean.py` reading
`SEBoolean`, `selinux_booleans.py` reading `Booleans`,
`selinux_file_context.py` reading `FileContext`,
`selinux_file_context_mapping.py` reading `FileContextMapping`,
`selinux_se_ports.py` reading `SEPorts` — once unfiltered, once narrowed
with the `protocol`/`se_type` filter arguments — `selinux_se_port.py`
reading `SEPort`, `selinux_mode.py` reading `Mode`, `selinux_se_login.py`
reading `SELogin`, `selinux_se_user.py` reading `SEUser`,
`selinux_modules.py` reading `Modules` and `selinux_permissive_domains.py`
reading `PermissiveDomains`, all over `gather_fact`) are not rendered here
— they read a single fact, and the facts themselves are documented on the
[`selinux` module page](../../modules/selinux.md). Like the dnf examples
they read the shared project-root `inventory.json` and guard on the
`"centos"` group: `is_centos` filters the host list before the
`gather_fact` fan-out, and the operation scripts below carry
`when=is_centos` on every request. Every target is an SELinux
(RHEL-family) host, so the `semanage`/`semodule`/`sestatus` shells are
meant to run on all of them — point `INVENTORY_PATH` at your own file of
machines before executing.

**Host-policy prerequisite for the mutating operations.** The tools these
scripts drive (`semanage`, `semodule`, `setsebool`, `chcon`, `restorecon`
from `policycoreutils`/`checkpolicy`) are already installed on the demo
hosts and every request is correctly flagged `sudo=True`. What the
mutations additionally need is an **unconfined sudo domain**: they rewrite
the root-owned policy store under `/var/lib/selinux` and relabel root-owned
files. On the demo hosts sudo lands in the confined `staff_t` domain, which
cannot access that store, so `semanage`/`semodule` fail with "SELinux policy
is not managed or store cannot be accessed" and `chcon`/`restorecon` with
"Permission denied" — even though `httpd` (and thus `/var/www/html`) is
installed. This is a host-policy prerequisite, not a script gap: the host
admin must map the login's sudo role to an unconfined domain (e.g.
`unconfined_t`) or run the target domain permissive before these examples
can apply. The read-only fact gathers and `selinux.mode` (which only reads
or calls `setenforce`) are unaffected. `selinux.module` also needs its
operator-supplied compiled package at `/tmp/myhttpd.pp` (upload it with
`files.put` first — the library ships no on-host policy compiler).

## Enabling a boolean persistently

The boolean operation: `selinux.boolean` sets one kernel security boolean
to `on`/`off`, idempotent on the
[`SEBoolean`][reemote5.modules.selinux.selinux.SEBoolean] fact — the
check-before-executing the enhancement asked for falls out of the
fact-driven model, sparing the expensive `setsebool -P` policy rewrite on
an unchanged boolean. The script enables `httpd_can_network_connect` (the
switch that lets httpd open outbound connections) with `persistent=True`
and yields the same request twice — the second is a guaranteed no-op in
the apply phase, because the first set is visible to it through the fresh
per-step fact cache (in the prepare phase, where nothing is sent, both
plan as changes). `setsebool` writes kernel and policy state, so the
requests are flagged `sudo=True`.

::: selinux_boolean
    options:
      members: []

## Relabelling a file's context components

The file-context operation: `selinux.file_context` ports pyinfra's
`chcon -t` scenario and extends it to the full context — each of
`se_type`, `se_user`, `se_role`, `se_level` is optional, at least one must
be given, and only the components differing from the
[`FileContext`][reemote5.modules.selinux.selinux.FileContext] fact are
relabelled. The script labels httpd's index page `httpd_sys_content_t`
twice (the second a guaranteed no-op in apply), then asks for the whole
context on the same file — a component-wise no-op once the stock
user/role/level already match. `chcon` changes the *runtime* label only; a
relabel reverts it to the persistent database rule, which is the mapping
operation's job below. The requests are flagged `sudo=True` (the path is
root-owned).

::: selinux_op_file_context
    options:
      members: []

## Managing a persistent fcontext rule, then relabelling

The mapping operation: `selinux.file_context_mapping` manages one
persistent rule in the local fcontext database — `present=True` adds
(`semanage fcontext -a`) or modifies (`-m`) the rule for a target
expression, `present=False` deletes it (`-d`) — idempotent on the
[`FileContextMapping`][reemote5.modules.selinux.selinux.FileContextMapping]
fact. The script ensures `/web(/.*)?` carries `httpd_sys_content_t` twice
(the second a guaranteed no-op in apply), then composes the documented
remedy for the operation's own limitation: a database rule never touches
labels existing files carry, so a third request runs
[`selinux.restorecon`][reemote5.modules.selinux.selinux.restorecon] over
`/web` *after* the mapping step, seeing the new rule through the fresh
per-step state — the fact-operation pairing the design recommends. The
`/web` tree must exist on the hosts. All requests are flagged `sudo=True`.

::: selinux_op_file_context_mapping
    options:
      members: []

## Labelling a network port with modify idempotence

The port operation: `selinux.port` labels a protocol/port pair and adds
the *modify* capability pyinfra's add/remove-only operation lacked —
`present=True` adds an unlabelled port (`semanage port -a`) or changes a
differently-labelled one in place (`-m`), `present=False` deletes the
label (`-d`). Idempotence dispatches on tooling: the
[`SEPort`][reemote5.modules.selinux.selinux.SEPort] fact when `sepolicy`
is installed, otherwise the
[`SEPorts`][reemote5.modules.selinux.selinux.SEPorts] table. The script
labels tcp/8080 `http_port_t` (httpd's alternate port) twice — the second
a guaranteed no-op in apply. `semanage port` writes the policy store, so
the requests are flagged `sudo=True`.

::: selinux_port
    options:
      members: []

## Switching the runtime mode

The mode operation: `selinux.mode` is a reemote5 original — pyinfra ships
no mode operation — setting the *runtime* enforcing state with
`setenforce`, idempotent on the
[`Mode`][reemote5.modules.selinux.selinux.Mode] fact. The script ensures
`enforcing` twice (both no-ops on an already-enforcing host; on a
permissive host the first switches and the second is the apply-phase
no-op). Two boundaries are deliberate: the boot-time mode in
`/etc/selinux/config` is the config operation's job below, and a *disabled*
subsystem raises `ValueError` — `setenforce` cannot enable SELinux, the
transition needs the config edit, `autorelabel` and a reboot. The
requests are flagged `sudo=True`.

::: selinux_op_mode
    options:
      members: []

## Confining a login to an SELinux user

The login operation: `selinux.login` is a reemote5 original managing the
Linux-user → SELinux-user mapping (`semanage login -a/-m/-d`), the core of
least-privilege confined-user management, idempotent on the
[`SELogin`][reemote5.modules.selinux.selinux.SELogin] fact comparing both
the SELinux user and the MLS/MCS range. The script maps the login
`kjarvis` to `staff_u` at range `s0` — the confined-administrator user
whose roles the `SEUser` fact reports — twice (the second a guaranteed
no-op in apply). The mapping takes effect at the user's *next* login.
`semanage login` writes the policy store, so the requests are flagged
`sudo=True`.

::: selinux_login
    options:
      members: []

## Installing a custom policy module

The module operation: `selinux.module` is a reemote5 original managing
compiled policy modules through a four-state machine — `present` installs
(`semodule -X <priority> -i <source>`) or re-enables a disabled instance,
`absent` removes (`-r`), `enabled`/`disabled` flip an installed instance
(`-e`/`-d`) — idempotent on the
[`Modules`][reemote5.modules.selinux.selinux.Modules] fact. The parameter
is named `module_name` because `op()` reserves `name` for the request
label (pyinfra's `bool_name` precedent). The script installs a custom
`myhttpd` package twice (the second a guaranteed no-op in apply); the
compiled `.pp` must already exist on the hosts at `/tmp/myhttpd.pp`
(upload it with `files.put` first). The `absent`/`enabled`/`disabled`
states belong in their own deploys against hosts where the module is
already installed: prepare compares every request against the
pre-deployment state, and flipping an uninstalled module raises
`ValueError` there. `semodule` rewrites the policy store, so the requests
are flagged `sudo=True`.

::: selinux_module
    options:
      members: []

## Carving a per-domain permissive exception

The permissive-domain operation: `selinux.permissive_domain` is a
reemote5 original adding or removing one domain from the permissive list
(`semanage permissive -a/-d`), idempotent on the
[`PermissiveDomains`][reemote5.modules.selinux.selinux.PermissiveDomains]
fact — the safe alternative to global permissive mode when
troubleshooting one service: the exception is one domain wide, persistent
and auditable. The script runs a full add → no-op → remove cycle on
`httpd_t`, so the hosts are left exactly as they were found — and the two
phases tell the story differently: the plan, computed against the
pre-deployment snapshot, shows the removal as a no-op when the domain
started enforcing, while the apply log's fresh per-step fact cache sees
the exception the first step added and removes it. The requests are
flagged `sudo=True`.

::: selinux_permissive_domain
    options:
      members: []

## Relabelling a tree (the stateless contract)

The restorecon operation: `selinux.restorecon` is a reemote5 original
reapplying the persistent fcontext database to existing files
(`restorecon [-R] [-F] <path>`) — the remedy for `chcon` labels being
runtime-only and database rules never touching existing labels. It is
deliberately **stateless**: no fact compares a whole tree's labels
against the database, so every request yields and reports a change, in
the plan and the log, on the first run and every re-run. The script
relabels `/var/www/html` twice to make that contract visible — unlike the
fact-driven operations, the second request is *not* a no-op; restorecon
itself is harmless to re-run (it only writes labels that differ). The
requests are flagged `sudo=True`.

::: selinux_restorecon
    options:
      members: []

## Scheduling a full-system relabel

The autorelabel operation: `selinux.autorelabel` is a reemote5 original
creating the `/.autorelabel` marker (`touch /.autorelabel`) that tells the
next boot to relabel *every* file from the database — the heavy remedy
for a disabled-to-enabled transition (files created while SELinux was off
carry no labels) or severely corrupted contexts. **Warning**: the relabel
runs before services start, can take many minutes, and only happens at
the next reboot, which the operation does not trigger; for one tree,
`restorecon` is the surgical tool. Idempotent on the marker's existence,
the script schedules the relabel twice — the second a guaranteed no-op in
apply ("a full relabel is already pending"). Creating a file at `/` needs
root, so the requests are flagged `sudo=True`.

::: selinux_autorelabel
    options:
      members: []

## Managing /etc/selinux/config at boot

The config operation: `selinux.config` is a reemote5 original — pyinfra
ships no config operation — ensuring the boot-time settings in
`/etc/selinux/config`, and it supersedes the `files.edit` + `files.move`
two-run recipe this example used to demonstrate: the whole
spool-and-install dance is now inside
[`config`][reemote5.modules.selinux.selinux.config], so one request in one
deploy replaces the snapshot gather, the controller-side gate and both
runs. The script ensures `SELINUX=enforcing` and `SELINUXTYPE=targeted`
(the runtime half is `selinux.mode`'s job); each setting is optional —
`None` leaves that key untouched. Internally the operation reads the
file's text with the
[`SelinuxConfigFile`][reemote5.modules.selinux.selinux.SelinuxConfigFile]
content fact, drops every existing assignment of each requested key
(commented ones included) and appends `KEY=value` unquoted; a file that
already holds exactly the wanted assignments is a no-op. When the content
differs, the request sends an unprivileged `scp` spooling the new text to
a deterministic `/tmp` path (transfers never elevate), then an elevated
`sh -c` that copies it over the file, chmods it `0644` and removes the
spool — the request's `sudo=True` covers the install only. The settings
take effect at the *next reboot*; switching a *disabled* subsystem to
enforcing additionally needs `selinux.autorelabel` before that reboot.

::: selinux_config
    options:
      members: []