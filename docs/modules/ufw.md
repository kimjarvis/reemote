# ufw module

## ufw Facts

::: reemote5.modules.ufw.ufw
    options:
      show_root_heading: false
      members:
        - UfwApplications
        - UfwRules
        - UfwStatus

## ufw Returns

::: reemote5.modules.ufw.ufw
    options:
      show_root_heading: false
      members:
        - UfwRule
        - UfwStatusInfo

## ufw Operations

::: reemote5.modules.ufw.ufw
    options:
      show_root_heading: false
      members:
        - default_policy
        - logging
        - reset
        - rule
        - state



<!---

`reemote5.modules.ufw.ufw` gives complete control over and visibility of
UFW (Uncomplicated Firewall) — the default firewall frontend on Ubuntu
and Debian-family hosts — through the `ufw` CLI. pyinfra ships **no ufw
module at all**, so every fact and operation here is a reemote5 original:
three facts (`UfwStatus`, `UfwRules`, `UfwApplications`), two entry
records (`UfwStatusInfo`, `UfwRule`) and five operations (`state`,
`default_policy`, `logging`, `rule`, `reset`). The transport is the CLI:
every read is a single shell round-trip and every mutation one `ufw`
invocation, so no Python dependency is needed on the target.

The privilege contract is uniform: reading ufw state needs root, so
every fact rides the passwordless retry
`cmd 2> /dev/null || sudo -n cmd 2> /dev/null || true` — a root login or
a NOPASSWD sudoers rule reads the full state, any other login reads the
empty value (the inactive-shaped status record, `[]`) rather than
failing, which is also how a host without ufw reads. The `|| true` guard
masks exit 127 too, so the facts list only `HostConnectionError`. Every
mutating operation needs root: flag the request `sudo=True`.

Deliberately shaped, with the resolution recorded: the Discussion
proposal's `ufw.DefaultPolicy` fact is folded into `UfwStatus` — the
three default policies (`default_incoming`/`default_outgoing`/
`default_routed`) come from the same `ufw status verbose` read as
`active` and `logging`, so a separate fact would duplicate the command
and the cache entry. The Proposal's `ufw.state(enabled, ipv6)` drops the
`ipv6` argument: the ufw CLI has no runtime IPv6 toggle (it is the
`IPV6=yes` line of `/etc/ufw/ufw.conf`, a file edit, and ufw already
manages both families together), and no `version=4|6` argument exists
either — one `ufw allow` creates the rule for both families when
`IPV6=yes`. The Proposal's separate `ufw.application` operation is
folded into `rule`'s `app=` parameter: a profile rule is the same
identity/matching problem as a port rule, and `UfwApplications` provides
the name visibility. `ufw.enable`/`ufw.disable` are one `state(enabled=)`
operation, mirroring the firewalld/iptables house style of one switch
per concept.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a
generator — implemented as a `process` coroutine returning the raw value
(over asyncssh in the real implementation). Read facts through
[`FactCache.get`][reemote5.core.FactCache.get], which is itself a
coroutine, so results are awaited and cached within a phase step.

`UfwStatus` parses `ufw status verbose` into one `UfwStatusInfo` record:
`active` plus the `logging` word (`off`/`low`/`medium`/`high`/`full`) and
the three default policies. An inactive firewall prints only
`Status: inactive`, so every other field reports `""` — the operations
compare against exactly this shape. `UfwRules` parses the `[ N]` rows of
`ufw status numbered` into `UfwRule` entries: the 1-based `number`,
`action` (ALLOW/DENY/REJECT/LIMIT), `direction` (IN/OUT/FWD), the
decomposed To column (`to_addr`/`port`/`protocol`/`app`), `from_addr`,
`interface`, the `(log)`/`(out)` attributes, the `(v6)` family marker and
the trailing `# comment`. The numbered form keeps application profiles
collapsed to one row per rule and carries the numbers inserts address;
ufw lists v4 rows first, then their v6 twins. `UfwApplications` keeps
the profile names `ufw app list` prints under its `Available
applications:` header — the names `rule(app=...)` references.

## Operations

Operations live in the namespace module so a deploy reads `ufw.rule`,
mirroring pyinfra's API shape. Every operation is an async generator with
the same fixed signature:

```python
async def operation(ctx: OpContext, *args, **kwargs) -> AsyncGenerator[Command, None]:
```

It awaits facts via `ctx.facts`, sets `ctx.changed` and `ctx.reason` where
it decides a change is needed, and yields one descriptor per command that
would mutate the host. Every operation here writes firewall state, so
flag the request `sudo=True`.

`ufw.state` reads `UfwStatus` and yields `ufw --force enable` only when
inactive (`--force` skips the interactive "may disrupt existing ssh
connections" prompt) or `ufw disable` only when active; enabling also
turns the service on at boot. `ufw.default_policy` validates the
direction (incoming/outgoing/routed) and policy word (allow/deny/reject)
and yields `ufw default <policy> <direction>` only when that direction's
current default differs. `ufw.logging` validates the level and compares
the `logging` word; `on` mirrors ufw's own semantics — it keeps the
current level when already logging and only changes anything when
logging is off.

`ufw.rule` is the workhorse: it normalizes the desired rule (a port int,
a `"8000:8010"` range or a tuple of ports for a multiport rule; protocol,
action allow/deny/reject/limit, direction in/out, `route`, source and
destination addresses, an `app` profile, an interface binding, `log`,
`comment`, an `insert` position), reads `UfwRules` and yields the add
form only when no matching rule exists — ufw happily accepts duplicates,
so the comparison is what keeps a ruleset clean. Address family is part
of the match: an address-less or IPv4 rule matches the v4 row (ufw
maintains the `(v6)` twin itself), while any colon address — including
`::/0` — is v6-specific and matches only v6 rows. `present=False`
deletes by rule *specification* (`ufw --force delete <spec>`), never by
number (numbers shift as rules are deleted), and yields one delete per
match so duplicates are fully removed within one step; a rule stored
without a comment matches a delete that names none, mirroring ufw.
`insert` positions NEW rules only — ufw evaluates first-match-wins, so a
deny that must precede an allow is inserted, not appended; moving an
existing rule is a delete-and-re-add, not an idempotent state.

`ufw.reset` is deliberately **stateless**: a reset does not map to a
persistent desired state, so it reads no fact, always plans as a change
and always yields `ufw --force reset`. Destructive — it backs up the
rules, deletes every user rule, restores installation defaults and
disables the firewall — it runs only when a deploy explicitly yields it
(follow it with `rule`/`default_policy`/`state` requests to rebuild the
baseline).

## Example

The read-only fact-gathering scripts under
[`examples/modules/ufw/`](../examples/modules/ufw.md) gather one fact
each over `gather_fact` on the shared `inventory.json`'s CentOS hosts
(the dnf convention — the `is_centos` guard filters the host list before
the fan-out): `ufw_ufw_status.py`, `ufw_ufw_rules.py`
and `ufw_ufw_applications.py`.

The operation scripts beside them demonstrate `ufw.state`
(`ufw_state.py`, the enable switch twice), `ufw.default_policy`
(`ufw_default_policy.py`, routed deny — the non-router-safe target),
`ufw.logging` (`ufw_logging.py`, the low level twice), `ufw.reset`
(`ufw_reset.py`, the stateless destructive contract, yielded once) and
`ufw.rule` (`ufw_rule.py`, the SSH lockout guard twice plus a multiport
HTTP/HTTPS tuple rule from a trusted subnet). All are compile-checked,
not executed against the shared demo hosts, and rendered on the
[ufw examples page](../examples/modules/ufw.md).

--->