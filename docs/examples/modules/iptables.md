# Module examples: iptables

Scripts under `examples/modules/iptables/` — one per fact or operation of the
[`iptables`](../../modules/iptables.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The read-only fact-gathering examples (`iptables_iptables_chains.py` reading
`IptablesChains`, `iptables_ip6tables_chains.py` reading `Ip6tablesChains`,
`iptables_iptables_rules.py` reading `IptablesRules`,
`iptables_ip6tables_rules.py` reading `Ip6tablesRules`,
`iptables_iptables_rules_ordered.py` reading `IptablesRulesOrdered`,
`iptables_ip6tables_rules_ordered.py` reading `Ip6tablesRulesOrdered`,
`iptables_iptables_tables.py` reading `IptablesTables`,
`iptables_ip6tables_tables.py` reading `Ip6tablesTables`,
`iptables_iptables_version.py` reading `IptablesVersion`,
`iptables_ip6tables_version.py` reading `Ip6tablesVersion`,
`iptables_iptables_modules.py` reading `IptablesModules`,
`iptables_ip_sets.py` reading `IpSets`, `iptables_iptables_persistence.py`
reading `IptablesPersistence` and `iptables_ip6tables_persistence.py`
reading `Ip6tablesPersistence`, all over `gather_fact`) are not rendered
here — they read a single fact, and the facts themselves are documented on
the [`iptables` module page](../../modules/iptables.md). Like the dnf
examples they read the shared project-root `inventory.json` and guard on
the `"centos"` group: `is_centos` filters the host list before the
`gather_fact` fan-out, and the operation scripts below carry
`when=is_centos` on every request. The table-reading facts retry through
passwordless `sudo -n` and read the empty value on an unprivileged login
rather than failing, so every gather is safe on any Linux target — point
`INVENTORY_PATH` at your own file of machines before executing.

The eight operation scripts below open with an idempotent `dnf.packages`
install of `iptables` and `ipset` (under `sudo=True`): the demo hosts ship
without them, and the iptables facts are exit-masked, so the prepare phase
reads them even before the binary exists while apply installs first and
then mutates. The two version-fact scripts
(`iptables_iptables_version.py`, `iptables_ip6tables_version.py`) read an
*unmasked* `--version` banner, so they run a small install deploy before
gathering the fact (the `iptables` package provides both family binaries).
Note that on the CentOS demo hosts SELinux confines sudo to `staff_t`,
which blocks the kernel netlink socket (`/run/xtables.lock`) and `/etc`
writes: the operation scripts are correct but need an unconfined sudo
domain (or permissive SELinux) to apply, while the version facts only read
`--version` and pass as-is.

## Creating a custom chain

The chain operation: `iptables.chain` mirrors the baseline's operation, creating
a missing user chain with `-N` (or deleting an unwanted one with `-X`),
idempotent on the
[`IptablesChains`][reemote5.modules.iptables.iptables.IptablesChains] fact.
The script creates `LOG_DROP` — the classic custom chain a later rule fills
with a rate-limited LOG and a DROP — and yields the same request twice (the
second a guaranteed no-op in the apply phase, because the first `-N` is
visible to it through the fresh per-step fact cache; in the prepare phase,
where nothing is sent, both plan as changes). An unreferenced user chain
changes no packet flow, so the script is safe to run live. The requests are
flagged `sudo=True` (CAP_NET_ADMIN).

::: iptables_chain
    options:
      members: []

## Setting a built-in chain's policy

The policy operation: `iptables.policy` is the standalone policy surface
(the baseline couples policy setting to `chain`), validating the word against
ACCEPT/DROP/QUEUE/RETURN and yielding `-P` only when the
[`IptablesChains`][reemote5.modules.iptables.iptables.IptablesChains] fact
shows a different policy. The script targets FORWARD — the routed-traffic
chain a non-router host can safely default to DROP — twice; INPUT is
deliberately left alone, because dropping INPUT without first allowing SSH
would lock the deploy out of its own hosts. **Compile-checked by the test
suite, not executed against the shared demo hosts**: changing a live
firewall's default policy is an operator decision. The requests are flagged
`sudo=True`.

::: iptables_policy
    options:
      members: []

## Ensuring a rule with typed match extensions

The rule operation: `iptables.rule` mirrors the baseline's definition comparison
(named matches plus the sorted `extras` pair list against
[`IptablesRules`][reemote5.modules.iptables.iptables.IptablesRules]) and
extends it with first-class matchers — `ctstate` (conntrack),
`destination_ports`/`source_ports` (multiport), `limit`/`limit_burst`,
`match_set` (ipset), `log_level` and `comment` — each folded into the same
order-independent comparison, so no raw `extras` string is needed and
idempotence works unchanged. The script accepts established/related SSH
traffic with a `comment` marker (the reliable idempotency/auditing flag)
twice — the second a guaranteed no-op in apply. Tuple arguments keep every
request hashable. Adding an ACCEPT rule only widens what is allowed, so the
script is safe to run live. The requests are flagged `sudo=True`.

::: iptables_rule
    options:
      members: []

## Flushing a demo chain (idempotent scope)

The flush operation: `iptables.flush` empties one chain or a whole table,
idempotent on the
[`IptablesRules`][reemote5.modules.iptables.iptables.IptablesRules] fact — an
empty scope is a no-op, so a flush never shows changed twice. Policies and
user chains survive a flush; only rules go. The script composes the full
lifecycle on a dedicated `REEMOTE5_DEMO` chain: create it, seed one inert
rule (an RFC 5737 documentation-net source), flush the chain, then flush it
again — the second flush's fresh per-step fact cache sees the empty chain.
**Compile-checked by the test suite, not executed against the shared demo
hosts**: even a chain-scoped flush is a firewall mutation an operator
should opt into. The requests are flagged `sudo=True`.

::: iptables_flush
    options:
      members: []

## Zeroing counters (the stateless contract)

The zero operation: `iptables.zero` resets packet/byte counters and is
deliberately **stateless** — counters grow with every packet, so any fact
comparison is stale before the command runs and "already zero" is never a
stable state on a live firewall. Every request yields `-Z` and plans as
changed, in both phases, on every re-run. The script zeroes the filter
table twice to make that contract visible — unlike the fact-driven
operations, the second request is *not* a no-op. The audit workflow is:
gather `IptablesRulesOrdered` (exact `-x` counters), zero, let traffic flow,
gather again. Zeroing never touches the rule set, so the script is safe to
run live and the Persistence facts stay synced across it. The requests are
flagged `sudo=True`.

::: iptables_zero
    options:
      members: []

## Persisting the live rules

The save operation: `iptables.save` snapshots the live rules to a host-side
file, idempotent on the
[`IptablesPersistence`][reemote5.modules.iptables.iptables.IptablesPersistence]
fact — the live `iptables-save` output is compared against the file with
comment lines stripped (the save header's timestamp changes every run), and
the write is skipped when they match. The script passes
`path="/etc/sysconfig/iptables"` — the RHEL-family snapshot location whose
parent directory already exists (`iptables.save` redirects into the file
but does not create a missing parent, so the Debian-style default
`/etc/iptables/rules.v4` would fail on these hosts) — twice, the second a
guaranteed no-op in apply. The descriptor wraps the redirect in
`sh -c` so the `sudo=True` elevation covers the write itself; run the save
*after* the rule operations that should survive the reboot. Reloading at
boot is the distro service's job, not this operation's.

::: iptables_save
    options:
      members: []

## Atomically restoring an uploaded snapshot (the bulk recipe)

The restore operation: `iptables.restore` is the atomic bulk-apply surface —
`iptables-restore` applies the whole file or fails without partial state,
the resolution of the `bulk_rule(rules=[...])` proposal that `op()`'s
hashability rule blocks. The path is HOST-side (reemote5 transfers are
IO-only), so the script first uploads a module-level `io.BytesIO` snapshot
with [`files.put`][reemote5.modules.files.files.put] (unprivileged scp into
`/tmp`), then restores it twice under `sudo=True`, idempotent
on the same
[`IptablesPersistence`][reemote5.modules.iptables.iptables.IptablesPersistence]
fact. In prepare the file does not exist yet, so both restores plan as
changes; in apply the first loads it and the second reports no change.
**Compile-checked by the test suite, not executed against the shared demo
hosts**: a default `noflush=False` restore replaces the entire live rule
set (the snapshot keeps INPUT permissive and SSH explicitly allowed, but
replacing a production firewall is always an operator decision).

::: iptables_restore
    options:
      members: []

## Managing an ipset blacklist

The ipset operation: `iptables.ipset` manages the performance companion to
iptables rules — one `match_set` rule replaces thousands of per-address
rules — idempotent on the
[`IpSets`][reemote5.modules.iptables.iptables.IpSets] fact: a missing set is
created with the requested type (`-exist` tolerates a stale read), missing
entries are added in sorted order, and a type mismatch raises `ValueError`
rather than silently recreating the set. Membership is additive;
`present=False` with entries removes just those, and with no entries
destroys the set. The script ensures a `hash:ip` blacklist with two RFC 5737
documentation members twice — the second a guaranteed no-op in apply.
Creating a set changes no packet flow until a rule references it, so the
script is safe to run live. The requests are flagged `sudo=True`.

::: iptables_ipset
    options:
      members: []