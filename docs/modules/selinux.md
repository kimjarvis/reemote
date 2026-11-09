# selinux module

## selinux Facts

::: reemote5.modules.selinux.selinux
    options:
      show_root_heading: false
      members:
        - Booleans
        - FileContext
        - FileContextMapping
        - Mode
        - Modules
        - PermissiveDomains
        - SEBoolean
        - SELogin
        - SEPort
        - SEPorts
        - SEUser

## selinux Returns

::: reemote5.modules.selinux.selinux
    options:
      show_root_heading: false
      members:
        - ModeInfo
        - Module
        - SeContext
        - SeLogin
        - SeUser

## selinux Operations

::: reemote5.modules.selinux.selinux
    options:
      show_root_heading: false
      members:
        - autorelabel
        - boolean
        - file_context
        - file_context_mapping
        - login
        - mode
        - module
        - permissive_domain
        - port
        - restorecon



<!---

`reemote5.modules.selinux.selinux` combines the SELinux facts and the SELinux
operations in a single source, covering the core toolchain of
`getenforce`/`setenforce`, `semanage` (fcontext/login/user/port/permissive),
`semodule` and `restorecon` for complete control over and visibility of
SELinux on RHEL-family hosts. Five facts port pyinfra's baseline
(`SEBoolean`, `FileContext`, `FileContextMapping`, `SEPorts`, `SEPort`) and
six are reemote5 originals filling the gaps pyinfra leaves (`Mode`,
`Booleans`, `SELogin`, `SEUser`, `Modules`, `PermissiveDomains`); four
operations port pyinfra's baseline (`boolean`, `file_context`,
`file_context_mapping`, `port` — the latter three enhanced) and six are
originals (`mode`, `login`, `module`, `permissive_domain`, `restorecon`,
`autorelabel`).

Deliberately absent: an `audit2allow` operation — auto-generating policy
from audit denials is a troubleshooting workflow, not a declarative state,
and belongs in the `Shell` action; a `selinux.config` operation — the
persistent `/etc/selinux/config` is a file edit fully expressible with the
generic `files.edit` primitive plus the elevated `files.move`, see the
spool-and-install recipe in `examples/modules/selinux/selinux_config.py`;
and SELinux *user definition* management (`semanage user -a/-m/-d`) — the
`SEUser` fact is read-only visibility, the rare definition edit is left to
the `Shell` action.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a
generator — implemented as a `process` coroutine returning the raw value
(over asyncssh in the real implementation). Read facts through
[`FactCache.get`][reemote5.core.FactCache.get], which is itself a
coroutine, so results are awaited and cached within a phase step.

`SEBoolean` takes a boolean name and runs `getsebool <name>`, keeping the
`on`/`off` value word of the first `--> ` row (pyinfra parity); empty or
malformed output reads as the empty string. `Booleans` is the original
whole-table sibling: `getsebool -a` parsed into a name → bool mapping
(True only for `on`), one read for a full boolean audit.

`FileContext` takes a path and runs `stat -c %C <path> || exit 0`,
splitting the `user:role:type:level` label into the shared `SeContext`
TypedDict; an unlabelled filesystem (`?`) or a missing path reads `None`
(pyinfra's None-on-absent). `FileContextMapping` takes a target expression
and greps the *local-only* fcontext database
(`set -o pipefail && semanage fcontext -n -l | (grep '^<target>' || true)`)
for the single matching row's trailing context, returning the same
`SeContext` shape — the persistent rule a relabel would apply, not the
label a file carries now. Deliberate deviation from pyinfra: no mapping
reads `None` rather than `{}`, the house None-on-absent convention. A
missing `semanage` exits 127, reported as
[`CommandNotFoundError`][reemote5.hosts.CommandNotFoundError].

`SEPorts` runs `semanage port -ln` and keeps a protocol → port → type map,
expanding ranges (`9000-9010`) to individual ports and skipping the
`unreserved_port_t` catch-all (pyinfra parity); the two optional filter
arguments are the reemote5 enhancement — `protocol` keeps one protocol's
table, `se_type` keeps only ports carrying one type label, so a port audit
is one filtered read. `SEPort` takes a protocol and port and runs
`(sepolicy network -p <port> 2>/dev/null || true) | grep <protocol>`,
keeping the first row's type only when *both* the specific and the generic
range row are present — a port covered by a generic range alone reads as
the empty string (pyinfra's gate). The `|| true` tolerates a host without
`sepolicy` (policycoreutils-dev is not stock).

`Mode` is the original foundation every SELinux decision starts from:
`sestatus` parsed into a `ModeInfo` record — `mode` (runtime: enforcing,
permissive or disabled), `config_mode` (the boot setting from
`/etc/selinux/config`) and `policy` (the loaded policy name, e.g.
targeted). Keeping runtime beside config tells "enforcing now but
permissive after reboot" apart from a fully enforcing host; a disabled
subsystem reads `mode="disabled"` with empty config/policy fields.

`SELogin` runs `semanage login -ln` and keys the rows by Linux login name,
keeping `seuser`, `mls_range` and the `service` column newer releases add
(`*` when absent) as `SeLogin` entries; the `__default__` fallback row is
kept like any other. `SEUser` runs `semanage user -ln` and keys the rows by
SELinux user name, keeping `prefix`, `mls_level`, `mls_range` and the
variable-length `roles` tail as `SeUser` entries — read it beside
`SELogin` to audit the full confined-user chain. `Modules` runs
`semodule -lfull` (which, unlike `-l`, shows disabled instances and
lower-priority duplicates) and keeps a list of `Module` instances —
`name`, `priority`, `format` (pp/cil), `enabled` — in listing order;
modern semodule no longer prints versions, so priority/format/state are
the modern equivalents of the proposal's name-and-version shape.
`PermissiveDomains` runs `semanage permissive -l` and keeps the domain
names as a deduplicated list, skipping the builtin-types heading — the
domains exempted from enforcement despite a globally enforcing policy.

## Operations

Operations live in namespace modules so a deploy reads `selinux.boolean`,
mirroring pyinfra's API. Every operation is an async generator with the
same fixed signature:

```python
async def operation(ctx: OpContext, *args, **kwargs) -> AsyncGenerator[Command, None]:
```

It awaits facts via `ctx.facts`, sets `ctx.changed` and `ctx.reason` where
it decides a change is needed, and yields one descriptor per command that
would mutate the host. Every operation here writes root-owned kernel or
policy state, so flag the request `sudo=True` (or `su=True`/`doas=True`).

`selinux.boolean` (pyinfra parity) sets one boolean to `on`/`off`,
idempotent on `SEBoolean` — the check-before-executing the enhancement
asked for falls out of the fact-driven model, sparing the expensive
`setsebool -P` policy rewrite on an unchanged boolean. `persistent=True`
adds `-P`. pyinfra's `Boolean` enum is dropped: `value` is the plain
`on`/`off` string, anything else raises `ValueError`.

`selinux.file_context` (pyinfra parity, enhanced to the full context)
relabels one existing path with `chcon`: each of `se_type`, `se_user`,
`se_role`, `se_level` is optional, at least one must be given, and only
the components differing from the `FileContext` fact are yielded. The
label is runtime-only — a relabel reverts it to the database rule, which
is `selinux.file_context_mapping`'s job (pyinfra parity): `present=True`
adds (`semanage fcontext -a`) or modifies (`-m`) the persistent rule for a
target expression, `present=False` deletes it (`-d`), idempotent on
`FileContextMapping`. `selinux.restorecon` (original) is the documented
remedy for both limitations: it reapplies the database to a tree
(`restorecon [-R] [-F] <path>`). It is deliberately **stateless** — no
fact compares a whole tree against the database — so every request yields
and reports a change; restorecon itself only writes labels that differ.

`selinux.port` (pyinfra parity, enhanced with modify) labels a
protocol/port pair: `present=True` adds an unlabelled port
(`semanage port -a`) or modifies a differently-labelled one in place
(`-m`) — the update scenario pyinfra's add/remove-only operation could not
express — and `present=False` deletes the label (`-d`). Idempotence
dispatches on tooling: the `SEPort` fact when `sepolicy` is installed,
otherwise the `SEPorts` table.

`selinux.mode` (original) sets the runtime enforcing state with
`setenforce`, idempotent on `Mode`; it never touches
`/etc/selinux/config` (the boot setting is the `files.edit` recipe's job)
and raises `ValueError` on a disabled subsystem — `setenforce` cannot
enable SELinux, the transition needs the config edit, `autorelabel` and a
reboot. `selinux.login` (original) manages the Linux-user → SELinux-user
mapping (`semanage login -a/-m/-d`), idempotent on `SELogin` comparing
both user and range (the service column is never managed).
`selinux.module` (original) manages policy modules through a four-state
machine — `present` installs (`semodule -X <priority> -i <source>`) or
re-enables, `absent` removes (`-r`), `enabled`/`disabled` flip an
installed instance (`-e`/`-d`) — idempotent on `Modules`; the parameter is
named `module_name` because `op()` reserves `name` for the request label
(pyinfra's `bool_name` precedent), and enabling/disabling an uninstalled
module raises `ValueError` in prepare. `selinux.permissive_domain`
(original) adds or removes one domain from the permissive list
(`semanage permissive -a/-d`), idempotent on `PermissiveDomains` — the
surgical alternative to global permissive mode when troubleshooting one
service. `selinux.autorelabel` (original) creates the `/.autorelabel`
marker (`touch /.autorelabel`) scheduling a full-system relabel at the
next boot, idempotent on the marker's existence — the heavy remedy for a
disabled-to-enabled transition or severely corrupted contexts, with a
clear warning: the relabel can take many minutes and needs a reboot this
operation does not trigger.

## Example

The read-only fact-gathering scripts under
[`examples/modules/selinux/`](../examples/modules/selinux.md) gather one
fact each over `gather_fact` on the shared `inventory.json`'s CentOS
hosts (the dnf convention — every target is an SELinux host, and the
`is_centos` guard filters the host list before the fan-out):
`selinux_se_boolean.py`, `selinux_booleans.py`,
`selinux_file_context.py`, `selinux_file_context_mapping.py`,
`selinux_se_ports.py` (full table plus a protocol/type-filtered gather),
`selinux_se_port.py`, `selinux_mode.py`, `selinux_se_login.py`,
`selinux_se_user.py`, `selinux_modules.py` and
`selinux_permissive_domains.py`.

The operation scripts beside them demonstrate `selinux.boolean`
(`selinux_boolean.py`), `selinux.file_context`
(`selinux_op_file_context.py`, type-only twice plus a full-context
request), `selinux.file_context_mapping`
(`selinux_op_file_context_mapping.py`, composed with `restorecon`),
`selinux.port` (`selinux_port.py`), `selinux.mode` (`selinux_op_mode.py`),
`selinux.login` (`selinux_login.py`), `selinux.module`
(`selinux_module.py`), `selinux.permissive_domain`
(`selinux_permissive_domain.py`, a full add → no-op → remove cycle),
`selinux.restorecon` (`selinux_restorecon.py`, the stateless contract) and
`selinux.autorelabel` (`selinux_autorelabel.py`). `selinux_config.py` is
the `files.edit` spool-and-install recipe answering the proposed
`selinux.config`. All are rendered on the
[selinux examples page](../examples/modules/selinux.md).

--->