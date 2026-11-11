# iptables module

::: reemote.modules.iptables.iptables
    options:
      show_root_heading: false
      members:
        - Ip6tablesChains
        - Ip6tablesPersistence
        - Ip6tablesRules
        - Ip6tablesRulesOrdered
        - Ip6tablesTables
        - Ip6tablesVersion
        - IpSet
        - IpSets
        - IptablesChains
        - IptablesModules
        - IptablesOrderedRule
        - IptablesPersistence
        - IptablesRule
        - IptablesRules
        - IptablesRulesOrdered
        - IptablesTables
        - IptablesVersion
        - IptablesVersionInfo
        - chain
        - flush
        - ipset
        - policy
        - restore
        - rule
        - save
        - zero



<!---

`reemote.modules.iptables.iptables` combines the netfilter facts and the
firewall operations in a single source, covering the `iptables`/`ip6tables`
toolchain — `iptables-save` rule parsing, numbered/countered listings,
`-N/-X/-P/-A/-I/-D/-F/-Z` mutations, `iptables-save`/`iptables-restore`
atomic snapshots and the `ipset` companion — for complete control over and
visibility of Linux packet filtering. Four facts port the baseline's baseline
(`IptablesChains`, `IptablesRules` and their `Ip6tables*` siblings) and ten
are reemote originals filling the gaps the baseline leaves
(`IptablesRulesOrdered`/`Ip6tablesRulesOrdered`,
`IptablesTables`/`Ip6tablesTables`, `IptablesVersion`/`Ip6tablesVersion`,
`IptablesModules`, `IpSets`, `IptablesPersistence`/`Ip6tablesPersistence`);
two operations port the baseline's baseline (`chain`, `rule` — the latter
enhanced with typed match extensions) and six are originals (`policy`,
`flush`, `zero`, `save`, `restore`, `ipset`). Every operation takes
`version=4|6` (except `ipset`, which is family-agnostic at the container
level), so IPv6 parity is one argument, not a parallel module.

The privilege contract is uniform: reading the tables needs CAP_NET_ADMIN,
so every table-reading fact rides the passwordless retry
`cmd 2> /dev/null || sudo -n cmd 2> /dev/null || true` — a root login or a
NOPASSWD sudoers rule reads the full state, any other login reads the empty
value (`{}`/`[]`/`False`) rather than failing. The `IptablesTables` siblings
read world-readable `/proc` files and `IptablesModules` reads `lsmod`, so
they need no retry; the `IptablesVersion` siblings run an unguarded
`--version` (no privilege needed) and surface a missing binary as
[`CommandNotFoundError`][reemote.hosts.CommandNotFoundError]. Every
mutating operation needs root: flag the request `sudo=True`.

Deliberately absent, with the resolution recorded: a `bulk_rule(rules=[...])`
operation — `op()` arguments must be hashable, so a list of rule dicts
cannot be a request; the atomic bulk surface is `restore` over a host-side
snapshot file, uploaded with `files.put` when it lives on the controller
(see `examples/modules/iptables/iptables_restore.py`). Connection-tracking
*table* visibility (`conntrack -L`) — a different subsystem with its own
tooling, out of scope for the iptables surface. High-level `nat`/`log`
wrapper operations — `rule` already covers DNAT/SNAT/MASQUERADE/REDIRECT
(`to_destination`/`to_source`/`to_ports`, validated against `table="nat"`)
and LOG (`log_prefix` plus the typed `log_level`); a wrapper would duplicate
the comparison logic. An `exact` reordering flag and a `rule_at_position`
reconciler — `position` inserts a *new* rule at a line number, but moving an
existing rule is a flush-and-reapply workflow (`restore`), not an idempotent
single-rule state. `IptablesDefaultPolicies`/`IptablesNatRules` convenience
facts — `IptablesChains` already answers policies and every rule fact takes
a `table` argument. A standalone `match` helper operation — the typed
matcher parameters on `rule` are the first-class surface. Distro
auto-detection of the persistence path — the iptables-persistent default is
explicit and `save`/`restore` take any path. The hardware module's
`Ipv4Address` entry (of `NetworkDevices`) answers interface addressing; this
module cross-refers it rather than duplicating it.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a
generator — implemented as a `process` coroutine returning the raw value
(over asyncssh in the real implementation). Read facts through
[`FactCache.get`][reemote.core.FactCache.get], which is itself a
coroutine, so results are awaited and cached within a phase step.

`IptablesChains` parses the `:NAME POLICY [pkts:bytes]` lines of
`iptables-save -t <table>` into a chain → policy mapping (baseline parity);
`IptablesRules` parses the `-A` lines into `IptablesRule` definition dicts —
named matches (`chain`, `jump`, `protocol`, `source`, `destination`, the
interfaces), their `not_` negations, the NAT target arguments and every
unmapped flag as sorted `extras` pairs. The `Ip6tables*` siblings flip only
the binary and the SimulatedHost seam field; both families take a `table`
argument (default `filter`), so nat/mangle/raw/security are one argument
away — the baseline's "filter-only emphasis" was never a parsing limitation.

`IptablesRulesOrdered` answers the rule-order *and* rule-counters proposals
with one fact: `iptables -t <table> -L [chain] -n -v -x --line-numbers`
parsed into `IptablesOrderedRule` entries — the 1-based `number` that
`-I`/`-D` address beside the exact `packets`/`bytes` counters (`-x` defeats
the K/M scaling that would make auditing lossy). `-n` keeps addresses
numeric (a fact never blocks on DNS); `chain` narrows the listing and the
`Chain NAME (...)` headers attribute each row.

`IptablesTables` reads `/proc/net/ip_tables_names` — the tables actually
instantiated in the kernel, for conditional logic before touching a table
that may not exist. `IptablesVersion` parses the `--version` banner into an
`IptablesVersionInfo` record (`version`, `backend`): `nf_tables` means the
iptables-nft translation layer, `legacy` the classic kernel tables (the
default when the banner carries no suffix) — the two behave differently for
some extensions, and the two families can differ on one host.
`IptablesModules` lists the loaded `xt_*` match/target modules from
`lsmod`, answering "is this `-m` matcher available here?".

`IpSets` parses `ipset list` into a name → `IpSet` mapping — `type`,
`revision`, `header`, `size_in_memory`, `references` (how many rules use
the set) and the current `members` — the visibility half of the ipset
companion. `IptablesPersistence` compares the live `iptables-save` output
against a snapshot file (default `/etc/iptables/rules.v4`, the
iptables-persistent location) with comment lines stripped on both sides —
the save header's timestamp changes every run, so raw equality would always
drift — and returns a synced bool; an absent file or an empty live read
reads False. `save` and `restore` both read it for idempotence.

## Operations

Operations live in namespace modules so a deploy reads `iptables.rule`,
mirroring the baseline's API. Every operation is an async generator with the
same fixed signature:

```python
async def operation(ctx: OpContext, *args, **kwargs) -> AsyncGenerator[Command, None]:
```

It awaits facts via `ctx.facts`, sets `ctx.changed` and `ctx.reason` where
it decides a change is needed, and yields one descriptor per command that
would mutate the host. Every operation here writes kernel firewall state
(or a root-owned snapshot file), so flag the request `sudo=True`.

`iptables.chain` (baseline parity) creates (`-N`) or deletes (`-X`) a chain,
idempotent on `IptablesChains`, and optionally sets a built-in chain's
policy in the same request (the baseline's coupled form; the kernel rejects
`-P` on user chains). `iptables.policy` (original) is the standalone
policy surface: it validates the word against ACCEPT/DROP/QUEUE/RETURN
(`ValueError` otherwise) and yields `-P` only when the current policy
differs — a missing chain still plans, failing at apply with
`CommandError`, because an unprivileged fact read must not mask a wanted
change.

`iptables.rule` (baseline parity, enhanced) compares the rule definition —
named matches plus the sorted `extras` pair list — against `IptablesRules`
and yields `-A` (append), `-I` (insert, `append=False` or an explicit
1-based `position`) or `-D` (remove). The typed-matcher extension makes
`ctstate`, `destination_ports`/`source_ports` (multiport), `limit`/
`limit_burst`, `match_set` and `log_level` first-class parameters folded
into the same comparison the baseline's port/comment shortcuts use, so
idempotence works unchanged — `iptables-save` prints the extensions as
ordinary flags and the pair list compares order-independently. `comment`
is the reliable idempotency/auditing marker; `extras` remains the escape
hatch for anything untyped. NAT target arguments are validated against
`table="nat"` and the jump, the baseline's guards preserved verbatim.

`iptables.flush` (original) flushes one chain or a whole table, idempotent
on `IptablesRules` — an empty scope is a no-op, so a flush never shows
changed twice; policies and user chains survive (only `-P`/`-X` touch
those). `iptables.zero` (original) is deliberately **stateless**: counters
grow with every packet, so "already zero" is never a stable state — every
request yields `-Z`, the audit-reset contract (read `IptablesRulesOrdered`,
zero, read again). Zeroing does not change the rule set, so the
Persistence facts stay synced across it.

`iptables.save` (original) snapshots the live rules to a host-side file
(`sh -c 'iptables-save > path'`, so the elevation covers the redirect),
idempotent on `IptablesPersistence`. It deliberately stays **in-place**,
outside the spool-and-install write family (`files.install`, `apt.repo`,
`apk.repository`): its payload is the redirected output of
`iptables-save`, generated on the host from live kernel state, not
controller-computed file text — there is nothing to transform or upload,
and the save header's fresh-per-run timestamp would break the
determinism guard if the snapshot were captured and spooled back (the
same reason `mysql.dump` and `postgresql.dump` stay in place).
`iptables.restore` (original) is the
atomic bulk-apply surface: `iptables-restore` applies the whole file or
fails without partial state, idempotent on the same fact (`noflush=True`
merges instead of replacing). Both take a HOST-side path — reemote
transfers are IO-only, so a controller-side snapshot rides `files.put`
first. Reloading at boot is the distro's netfilter-persistent service, not
an operation's job.

`iptables.ipset` (original — the baseline has no ipset support) manages a set
and its members, idempotent on `IpSets`: `present=True` creates a missing
set (`-exist` tolerates a stale read) and adds the missing entries in
sorted order; an existing set of a different type raises `ValueError`
rather than silently recreating it. Membership is additive;
`present=False` with entries removes just those, and with no entries
destroys the set (a referenced set fails at apply — the kernel rejects
it). Pair it with `rule`'s `match_set` to replace thousands of per-address
rules with one set match.

## Example

The read-only fact-gathering scripts under
[`examples/modules/iptables/`](../examples/modules/iptables.md) gather one
fact each over `gather_fact` on the shared `inventory.json`'s CentOS
hosts (the dnf convention — the `is_centos` guard filters the host list
before the fan-out): `iptables_iptables_chains.py`,
`iptables_ip6tables_chains.py`, `iptables_iptables_rules.py`,
`iptables_ip6tables_rules.py`, `iptables_iptables_rules_ordered.py`,
`iptables_ip6tables_rules_ordered.py`, `iptables_iptables_tables.py`,
`iptables_ip6tables_tables.py`, `iptables_iptables_version.py`,
`iptables_ip6tables_version.py`, `iptables_iptables_modules.py`,
`iptables_ip_sets.py`, `iptables_iptables_persistence.py` and
`iptables_ip6tables_persistence.py`.

The operation scripts beside them demonstrate `iptables.chain`
(`iptables_chain.py`, the LOG_DROP user chain twice), `iptables.policy`
(`iptables_policy.py`, FORWARD DROP — compile-checked, not executed on
the shared hosts), `iptables.rule` (`iptables_rule.py`, the typed
conntrack/comment matchers twice), `iptables.flush`
(`iptables_flush.py`, a create → seed → flush → no-op composition on a
demo chain), `iptables.zero` (`iptables_zero.py`, the stateless contract),
`iptables.save` (`iptables_save.py`, the idempotent default-path save),
`iptables.restore` (`iptables_restore.py`, the `files.put` + restore bulk
recipe) and `iptables.ipset` (`iptables_ipset.py`, the blacklist set and
its entries twice). All are rendered on the
[iptables examples page](../examples/modules/iptables.md).

--->
