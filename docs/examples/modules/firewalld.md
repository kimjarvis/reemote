# Module examples: firewalld

Scripts under `examples/modules/firewalld/` — one per fact or operation of the
[`firewalld`](../../modules/firewalld.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The read-only fact-gathering examples (`firewalld_firewalld_status.py`
reading `FirewalldStatus`, `firewalld_firewalld_default_zone.py` reading
`FirewalldDefaultZone`, `firewalld_firewalld_zones.py` reading
`FirewalldZones` in **both** configuration scopes side by side,
`firewalld_firewalld_services.py` reading `FirewalldServices`,
`firewalld_firewalld_ipsets.py` reading `FirewalldIpSets` and
`firewalld_firewalld_policies.py` reading `FirewalldPolicies`, both in the
permanent scope, all over `gather_fact`) are not rendered here — they read
a single fact, and the facts themselves are documented on the
[`firewalld` module page](../../modules/firewalld.md). Like the dnf
examples they carry an inline `INVENTORY` of the two web1/web2 hosts with
no `when` guard: every firewalld read retries through passwordless
`sudo -n` and reads the empty value on an unprivileged login (or a stopped
daemon, or a host without firewalld) rather than failing, so every gather
is safe on any Linux target — point `INVENTORY` at your own machines
before executing.

The demo hosts ship without firewalld, so every operation script below
first drives a small idempotent *install deploy* (`dnf.packages` for
`firewalld` plus `systemd.service` to start/enable the daemon, both under
`sudo=True`) before running its own deploy. firewalld's daemon performs
the privileged kernel work, so `firewall-cmd` mutations succeed even when
sudo lands in a confined SELinux domain — unlike the raw `iptables`
examples, whose netlink calls the confined domain blocks.

## Creating a custom zone (then reloading it into runtime)

The zone operation: `firewalld.zone` manages a zone's existence and
properties, idempotent on the
[`FirewalldZones`][reemote5.modules.firewalld.firewalld.FirewalldZones]
fact. Existence is permanent-only — `--new-zone` has no runtime form —
and so are the target/short/description properties: firewall-cmd rejects
a runtime `--set-target`/`--set-short`/`--set-description` ("Option can be
used only with --permanent"), so they are set in the permanent scope and
the runtime copy inherits them on the reload below. The
script ensures a `webapp` zone with an ACCEPT target twice (the second a
guaranteed no-op in the apply phase through the fresh per-step fact
cache) and then yields [`firewalld.reload`][reemote5.modules.firewalld.firewalld.reload]
— the stateless bridge that copies the new permanent zone into the
runtime configuration so later membership operations find it in both
scopes. An unbound zone changes no packet flow, so the composition is
safe to run live. The requests are flagged `sudo=True`.

::: firewalld_zone
    options:
      members: []

## Enabling a service in a zone

The zone membership operation: `firewalld.zone_service` attaches a service
by name — here the built-in `http` bundle to `public` — idempotent on the
[`FirewalldZones`][reemote5.modules.firewalld.firewalld.FirewalldZones]
fact per scope, dual-applying permanent first so the opening both
survives a reboot and takes effect immediately, without a reload. The
script yields the same request twice; a zone missing from a scope is
skipped with an explicit reason rather than a guaranteed-fail command.
**Compile-checked by the test suite, not executed against the shared demo
hosts**: opening a port on a live firewall is an operator decision. The
requests are flagged `sudo=True`.

::: firewalld_zone_service
    options:
      members: []

## Opening a raw port

The port operation: `firewalld.port` opens a single `port/protocol` (or a
range like `5000-5100/udp`) in a zone — here `8080/tcp` in `public` —
idempotent on the same
[`FirewalldZones`][reemote5.modules.firewalld.firewalld.FirewalldZones]
fact, with the shape and the 65535 upper bound validated statically
(`ValueError` before any command is planned). Prefer `zone_service` when
a named bundle exists; raw ports are the one-off opening. The script
yields the same request twice. **Compile-checked by the test suite, not
executed against the shared demo hosts.** The requests are flagged
`sudo=True`.

::: firewalld_port
    options:
      members: []

## Binding an interface (the rebind special case)

The interface operation: `firewalld.interface` binds an interface to a
zone — here `eth1` to `internal`, the classic two-NIC split. The present
direction renders `--change-interface`, not `--add-interface`: an
interface lives in exactly one zone, so binding it *moves* it out of
whatever zone holds it. Idempotent on the
[`FirewalldZones`][reemote5.modules.firewalld.firewalld.FirewalldZones]
fact per scope; NetworkManager may re-assert its connection-profile zone
on the next interface restart. **Compile-checked by the test suite, not
executed against the shared demo hosts**: rebinding the interface a
deploy is connected through can drop the SSH session. The requests are
flagged `sudo=True`.

::: firewalld_interface
    options:
      members: []

## Binding a source subnet

The source operation: `firewalld.source` binds an IP, CIDR, MAC or
`ipset:name` reference to a zone, so traffic *from* that source is
filtered by the zone's rules regardless of the arriving interface — here
the management subnet to `trusted` (source bindings take precedence over
interface bindings). Idempotent on the
[`FirewalldZones`][reemote5.modules.firewalld.firewalld.FirewalldZones]
fact per scope. **Compile-checked by the test suite, not executed against
the shared demo hosts**: a trusted-zone binding widens what the subnet
may reach. The requests are flagged `sudo=True`.

::: firewalld_source
    options:
      members: []

## Managing a rich rule (exact-string membership)

The rich rule operation: `firewalld.rich_rule` manages firewalld's rich
language without ever parsing or rewriting it — the *exact string* is
ensured present or absent in the zone's verbatim rule list, so a
reordered spelling is a different rule; keep it in one module-level
constant, as the script does. The descriptor quotes the rule with
`shlex` so the embedded double quotes survive the shell. Idempotent on
the [`FirewalldZones`][reemote5.modules.firewalld.firewalld.FirewalldZones]
fact per scope. **Compile-checked by the test suite, not executed against
the shared demo hosts**: the example rule narrows SSH to the management
subnet. The requests are flagged `sudo=True`.

::: firewalld_rich_rule
    options:
      members: []

## Allowing a portless protocol

The protocol operation: `firewalld.protocol` allows an IP protocol by
name — here `gre`, which has no port number at all, so neither a port
rule nor a service bundle's port list can express it (`esp`/`ah` for
IPsec are the other common cases). Idempotent on the
[`FirewalldZones`][reemote5.modules.firewalld.firewalld.FirewalldZones]
fact per scope. **Compile-checked by the test suite, not executed against
the shared demo hosts.** The requests are flagged `sudo=True`.

::: firewalld_protocol
    options:
      members: []

## Blocking an ICMP type

The ICMP block operation: `firewalld.icmp_block` blocks an ICMP type by
name — here `echo-request` in `public`, stopping ping answers. A zone
with `icmp-block-inversion` enabled treats the block list as an *allow*
list instead; the operation manages the list only, and the
[`FirewalldZones`][reemote5.modules.firewalld.firewalld.FirewalldZones]
fact's `icmp_block_inversion` field shows which interpretation is in
force. **Compile-checked by the test suite, not executed against the
shared demo hosts**: silencing ping breaks monitoring an operator may
rely on. The requests are flagged `sudo=True`.

::: firewalld_icmp_block
    options:
      members: []

## Forwarding a port

The forward operation: `firewalld.forward_port` redirects
`port`/`protocol` to `to_port` and/or `to_addr` — here 80/tcp to 8080 on
the same host, the unprivileged-app-behind-the-well-known-port setup. At
least one destination is required (`ValueError` otherwise); a `to_addr`
redirect to another machine additionally needs `firewalld.masquerade`.
The operation builds the canonical
`port=80:proto=tcp:toport=8080:toaddr=` spelling itself, so the
comparison against the
[`FirewalldZones`][reemote5.modules.firewalld.firewalld.FirewalldZones]
fact never depends on how the rule was typed. **Compile-checked by the
test suite, not executed against the shared demo hosts.** The requests
are flagged `sudo=True`.

::: firewalld_forward_port
    options:
      members: []

## Toggling masquerading

The masquerade operation: `firewalld.masquerade` toggles source NAT on a
zone — the prerequisite for cross-machine `forward_port` rules and for a
gateway host sharing its uplink. Idempotent on the
[`FirewalldZones`][reemote5.modules.firewalld.firewalld.FirewalldZones]
fact's `masquerade` boolean per scope; `enabled=False` renders the
`--remove-masquerade` pair. **Compile-checked by the test suite, not
executed against the shared demo hosts**: enabling NAT changes how
forwarded traffic leaves the host. The requests are flagged `sudo=True`.

::: firewalld_masquerade
    options:
      members: []

## Defining a custom service bundle

The service operation: `firewalld.service` manages the *definition* — a
named port/protocol bundle, here `webapp` with 8080/tcp and 8443/tcp —
that zones attach by name with `zone_service`. Definitions are
permanent-only (no runtime store), so there is no `permanent` argument;
the runtime picks the definition up on the next reload. Members are
additive and the tuples keep every request hashable. Idempotent on the
[`FirewalldServices`][reemote5.modules.firewalld.firewalld.FirewalldServices]
fact. An unreferenced definition changes no packet flow, so the script is
safe to run live. The requests are flagged `sudo=True`.

::: firewalld_service
    options:
      members: []

## Managing an ipset block list

The ipset operation: `firewalld.ipset` manages a named set — here
`blocked`, a `hash:ip` set with two RFC 5737 documentation members that a
zone source can reference as `ipset:blocked`. Existence is permanent-only;
entries dual-apply and are additive (an intrusion responder's runtime
additions survive); a type mismatch on an existing set raises
`ValueError` rather than silently rebuilding it. Idempotent on the
[`FirewalldIpSets`][reemote5.modules.firewalld.firewalld.FirewalldIpSets]
fact. An unreferenced set changes no packet flow, so the script is safe
to run live. The requests are flagged `sudo=True`.

::: firewalld_ipset
    options:
      members: []

## Ensuring the default zone

The default zone operation: `firewalld.default_zone` sets the zone that
unbound connections and interfaces land in — here `public`, the firewalld
baseline, so the script *ensures* rather than drifts (a custom default
must exist first: create it with `zone`). `--set-default-zone` writes
both configurations at once, so there is no `permanent` argument.
Idempotent on the
[`FirewalldDefaultZone`][reemote5.modules.firewalld.firewalld.FirewalldDefaultZone]
fact; on a stock host both requests are no-ops in both phases, which
makes the script safe to run live. The requests are flagged `sudo=True`.

::: firewalld_default_zone
    options:
      members: []

## Managing lockdown mode

The lockdown operation: `firewalld.lockdown` restricts which local
applications may change the firewall through D-Bus. Turning it on
without a whitelist can lock out NetworkManager and unprivileged
`firewall-cmd` callers, so the script ensures the *normal* state,
`enabled=False` — safe to run live. Runtime-only (no `permanent`
argument), idempotent on the daemon's `--query-lockdown` answer. The
requests are flagged `sudo=True`.

::: firewalld_lockdown
    options:
      members: []

## Managing panic mode (the recovery direction)

The panic operation: `firewalld.panic` is the emergency kill switch —
`--panic-on` drops *all* packets including the deploy's own SSH session,
which is why the script ensures `enabled=False`, the recovery direction
that turns panic off on a host someone panicked. Never enable it in a
remote deploy without out-of-band access. Runtime-only (no `permanent`
argument), idempotent on the daemon's `--query-panic` answer; ensuring
off is safe to run live. The requests are flagged `sudo=True`.

::: firewalld_panic
    options:
      members: []

## Reloading the firewall (the stateless contract)

The reload operation: `firewalld.reload` bridges firewalld's two
configurations — `reload` (permanent to runtime, keeping connection
state), `complete_reload` (the same, dropping connection state) and
`runtime_to_permanent` (the reverse); an unknown mode raises
`ValueError`. Unlike every other firewalld operation it is **stateless**:
a reload is an event, not a state, so it always yields and always reports
changed, in both phases — which is why the script yields it *once*
rather than the twice-for-idempotence pattern. It belongs after
permanent-only changes (see the zone example above); the dual-apply
membership operations do not need it. **Compile-checked by the test
suite, not executed against the shared demo hosts**: a reload discards
runtime-only drift an operator may be relying on. The request is flagged
`sudo=True`.

::: firewalld_reload
    options:
      members: []