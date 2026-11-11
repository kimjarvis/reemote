# firewalld module

::: reemote.modules.firewalld.firewalld
    options:
      show_root_heading: false
      members:
        - FirewalldDefaultZone
        - FirewalldIpSet
        - FirewalldIpSets
        - FirewalldPolicies
        - FirewalldPolicy
        - FirewalldService
        - FirewalldServices
        - FirewalldStatus
        - FirewalldStatusInfo
        - FirewalldZone
        - FirewalldZones
        - default_zone
        - forward_port
        - icmp_block
        - interface
        - ipset
        - lockdown
        - masquerade
        - panic
        - port
        - protocol
        - reload
        - rich_rule
        - service
        - source
        - zone
        - zone_service



<!---

`reemote.modules.firewalld.firewalld` gives complete control over and
visibility of the firewalld daemon — the default firewall on RHEL, CentOS,
Fedora, openSUSE and friends — through the `firewall-cmd` CLI. The baseline
ships **no firewalld module at all**, so every fact and operation here is a
reemote original: six facts (`FirewalldStatus`, `FirewalldDefaultZone`,
`FirewalldZones`, `FirewalldServices`, `FirewalldIpSets`,
`FirewalldPolicies`), five entry records and sixteen operations (`zone`,
`zone_service`, `port`, `interface`, `source`, `rich_rule`, `protocol`,
`icmp_block`, `forward_port`, `masquerade`, `service`, `ipset`,
`default_zone`, `lockdown`, `panic`, `reload`). The transport is the CLI,
not D-Bus: no `dbus-python` dependency on the target, and every read is a
single shell round-trip (the multi-object facts loop `--info-service` /
`--info-ipset` / `--info-policy` behind echo markers inside one `sh -c`).

The daemon keeps TWO configurations — runtime (what the kernel enforces
now) and permanent (what the next reload or reboot applies) — and the
module makes that split explicit instead of hiding it. The `FirewalldZones`,
`FirewalldIpSets` and `FirewalldPolicies` facts take a `permanent` argument
(default `False`, the runtime scope); the operations default to
`permanent=True` and **dual-apply**: the permanent command is yielded
first and the runtime one second, so a change both survives a reboot and
takes effect immediately without a reload, and a failed runtime command
still leaves the change persisted. `permanent=False` is runtime-only.
Existence is the exception firewalld itself dictates: zones, service
definitions and ipsets are created permanent-only (`--new-zone`,
`--new-service`, `--new-ipset` have no runtime form) and appear at runtime
after `firewalld.reload` — which is still shipped as the explicit bridge
(`reload`, `complete_reload`, `runtime_to_permanent`), deliberately
stateless because a reload is an event, not a state. A zone's
`target`/`short`/`description` properties are permanent-only for the same
reason: firewall-cmd rejects a runtime `--set-target`/`--set-short`/
`--set-description`, so `zone` manages them in the permanent scope alone
(`permanent=False` skips them with a reason) and the runtime copy
inherits them on the next reload. `default_zone` writes
both scopes in one command; `lockdown` and `panic` are runtime-only daemon
switches with no permanent form.

The privilege contract is uniform: every fact read rides the passwordless
retry `cmd 2> /dev/null || sudo -n cmd 2> /dev/null || true` — a root login
or a NOPASSWD sudoers rule reads the full state, any other login reads the
empty value (`{}`/`""`/`False`) rather than failing, which is also how a
stopped daemon or a host without firewalld reads. The `|| true` guard masks
exit 127 too, so the facts list only `HostConnectionError`. Every mutating
operation needs root: flag the request `sudo=True`.

Version handling is a read, not a raise: `FirewalldStatus` keeps the
daemon `version` beside `running` (the capability probe), and
`FirewalldPolicies` reads `{}` on a daemon older than 0.9 — the guard
masks the unknown `--get-policies` option and the marker loop never runs —
so the same deploy is safe against older daemons and can branch on the
version field in its own logic.

Deliberately shaped, with the resolution recorded: the Discussion
proposal's `firewalld.config` umbrella (default zone + lockdown) is split
into the single-purpose `default_zone` and `lockdown` operations; its
`rich_rules` fact is folded into `FirewalldZones` (each zone record keeps
its `rich_rules` verbatim, beside `forward_ports` and `icmp_blocks`), so
one fact answers everything the membership operations compare against.
The `service` name collision between the two proposals is resolved by
shipping BOTH: `service` manages the permanent-only definition (a named
port/protocol bundle) and `zone_service` attaches a service by name to a
zone. An `immediate` flag is not needed — the dual-apply default already
covers permanent + runtime, and `permanent=False` is the runtime-only
half. Rich rules are never parsed or rewritten: `rich_rule` ensures the
exact string is present or absent, because a reordered spelling is a
different rule to firewalld. Membership lists are additive on `service`
and `ipset` entries (unlisted members are never removed, so an intrusion
responder's runtime additions survive); a type mismatch on an existing
ipset raises `ValueError` rather than silently rebuilding it. Static
validation guards the plan: port shape and the 65535 bound, zone targets,
`forward_port`'s at-least-one-of `to_port`/`to_addr`, reload modes and a
non-empty default zone all raise `ValueError` before any command is
planned. A zone missing from a scope is skipped with an explicit reason
(`zone X not found in permanent configuration`), never a guaranteed-fail
command — create it with `zone` first. Policy *management* operations are
out of scope for now (the fact provides the visibility; policy objects are
authored between zone pairs and change rarely); direct rules
(`--direct`) are deliberately absent — they bypass the zone model the
module manages. The iptables module remains the surface for raw netfilter
rules and the `ipset` kernel tool itself; `firewalld.ipset` manages the
daemon's own ipset objects that zone sources reference as `ipset:name`.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a
generator — implemented as a `process` coroutine returning the raw value
(over asyncssh in the real implementation). Read facts through
[`FactCache.get`][reemote.core.FactCache.get], which is itself a
coroutine, so results are awaited and cached within a phase step.

`FirewalldStatus` answers `--state` and `--version` behind echo markers in
one shell: `running` plus the `version` string. `FirewalldDefaultZone`
answers `--get-default-zone` (a plain string, `""` when unreadable).
`FirewalldZones` parses `--list-all-zones` into a name → `FirewalldZone`
mapping with all sixteen fields — the `(active)` marker, target, display
text, interfaces, sources, services, ports, protocols, forward,
masquerade, forward-ports, source-ports, icmp-block-inversion, icmp-blocks
and the rich rules verbatim (real daemons print rule lines TAB-indented;
the parser accepts both indents). `FirewalldServices`,
`FirewalldIpSets` and `FirewalldPolicies` enumerate their objects and run
one `--info-*` per name behind markers, parsing each block into a
`FirewalldService` / `FirewalldIpSet` / `FirewalldPolicy` record; the
service `destination` tokens split on the FIRST colon so an IPv6 address
survives.

## Operations

Operations live in the namespace module so a deploy reads `firewalld.port`,
mirroring the baseline's API shape. Every operation is an async generator with
the same fixed signature:

```python
async def operation(ctx: OpContext, *args, **kwargs) -> AsyncGenerator[Command, None]:
```

It awaits facts via `ctx.facts`, sets `ctx.changed` and `ctx.reason` where
it decides a change is needed, and yields one descriptor per command that
would mutate the host. Every operation here writes daemon state, so flag
the request `sudo=True`.

`zone` manages existence and the target/short/description properties,
both permanent-only (firewall-cmd has no runtime form for either). The eight membership operations — `zone_service`,
`port`, `interface`, `source`, `rich_rule`, `protocol`, `icmp_block`,
`forward_port` — share one comparison helper over the zone record's
member lists, per scope, permanent first. `interface` renders
`--change-interface` for the present direction (an interface lives in
exactly one zone, so binding moves it); `forward_port` builds the canonical
`port=…:proto=…:toport=…:toaddr=…` spelling itself so the comparison never
depends on how the rule was typed. `masquerade` toggles the zone boolean.
`service` and `ipset` manage the permanent-only definitions plus their
additive members; `default_zone`, `lockdown` and `panic` are the
single-setting daemon switches; `reload` is the stateless bridge between
the scopes.

## Example

The read-only fact-gathering scripts under
[`examples/modules/firewalld/`](../examples/modules/firewalld.md) gather
one fact each over `gather_fact` on the shared `inventory.json`'s CentOS
hosts (the `is_centos` guard filters the host list before the fan-out):
`firewalld_firewalld_status.py`, `firewalld_firewalld_default_zone.py`,
`firewalld_firewalld_zones.py` (both scopes side by side),
`firewalld_firewalld_services.py`, `firewalld_firewalld_ipsets.py`
(permanent) and `firewalld_firewalld_policies.py` (permanent).

The operation scripts beside them demonstrate `firewalld.zone`
(`firewalld_zone.py`, the webapp zone twice then a reload),
`firewalld.zone_service` (`firewalld_zone_service.py`, http in public),
`firewalld.port` (`firewalld_port.py`, 8080/tcp), `firewalld.interface`
(`firewalld_interface.py`, the eth1 rebind), `firewalld.source`
(`firewalld_source.py`, the management subnet to trusted),
`firewalld.rich_rule` (`firewalld_rich_rule.py`, the exact-string SSH
rule), `firewalld.protocol` (`firewalld_protocol.py`, gre),
`firewalld.icmp_block` (`firewalld_icmp_block.py`, echo-request),
`firewalld.forward_port` (`firewalld_forward_port.py`, 80 to 8080),
`firewalld.masquerade` (`firewalld_masquerade.py`), `firewalld.service`
(`firewalld_service.py`, the webapp bundle), `firewalld.ipset`
(`firewalld_ipset.py`, the blocked set), `firewalld.default_zone`
(`firewalld_default_zone.py`, ensuring the public baseline),
`firewalld.lockdown` (`firewalld_lockdown.py`, ensuring off),
`firewalld.panic` (`firewalld_panic.py`, ensuring off — panic-on would
drop the deploy's own SSH session) and `firewalld.reload`
(`firewalld_reload.py`, the stateless contract). All are rendered on the
[firewalld examples page](../examples/modules/firewalld.md).

--->
