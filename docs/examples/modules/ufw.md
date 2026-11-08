# Module examples: ufw

Scripts under `examples/modules/ufw/` — one per fact or operation of the
[`ufw`](../../modules/ufw.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The read-only fact-gathering examples (`ufw_ufw_status.py` reading
`UfwStatus`, `ufw_ufw_rules.py` reading `UfwRules` and
`ufw_ufw_applications.py` reading `UfwApplications`, all over
`gather_fact`) are not rendered here — they read a single fact, and the
facts themselves are documented on the
[`ufw` module page](../../modules/ufw.md). Like the dnf examples they
carry an inline `INVENTORY` of the two web1/web2 hosts with no `when`
guard: every ufw read retries through passwordless `sudo -n` and reads
the empty value on an unprivileged login (or a host without ufw) rather
than failing, so every gather is safe on any Linux target — point
`INVENTORY` at your own machines before executing.

ufw is a Debian-family frontend that RHEL-family ships only through EPEL,
so every operation script below first installs `epel-release` and `ufw`
with `dnf.packages` (under `sudo=True`) as the opening requests of its
deploy. The install is idempotent and the ufw facts are exit-masked, so
the prepare phase tolerates ufw being absent until the apply phase
installs it. Installing ufw does **not** activate it, so the install step
is safe; the firewall mutations themselves stay compile-checked by the
test suite, not executed against the shared demo hosts (which already run
firewalld — enabling a second firewall, or resetting it, is an operator
decision).

## Enabling the firewall

The state operation: `ufw.state` ensures the firewall is active (and on
at boot), idempotent on the
[`UfwStatus`][reemote5.modules.ufw.ufw.UfwStatus] fact — `ufw --force
enable` only when inactive (the `--force` skips the interactive SSH
disruption prompt), `ufw disable` only when active. The script yields
the same enable request twice (the second a guaranteed no-op in the
apply phase through the fresh per-step fact cache). **Compile-checked by
the test suite, not executed against the shared demo hosts**: activating
a live firewall is an operator decision — allow SSH first or the deploy
locks itself out. The requests are flagged `sudo=True`.

::: ufw_state
    options:
      members: []

## Setting a default policy

The default policy operation: `ufw.default_policy` sets one direction's
fallback verdict (incoming/outgoing/routed × allow/deny/reject),
idempotent on the same
[`UfwStatus`][reemote5.modules.ufw.ufw.UfwStatus] fact's
`default_<direction>` field. The script targets `routed` deny — the
forwarded-traffic default a non-router host can safely deny, leaving
`incoming` alone so SSH stays reachable — and yields the request twice.
**Compile-checked by the test suite, not executed against the shared
demo hosts.** The requests are flagged `sudo=True`.

::: ufw_default_policy
    options:
      members: []

## Setting the logging level

The logging operation: `ufw.logging` sets the audit level
(off/on/low/medium/high/full), idempotent on the
[`UfwStatus`][reemote5.modules.ufw.ufw.UfwStatus] fact's `logging` word.
`on` mirrors ufw's own semantics — it keeps the current level when
already logging — so the script targets the concrete `low` level (ufw's
installation default) and yields it twice. **Compile-checked by the test
suite, not executed against the shared demo hosts.** The requests are
flagged `sudo=True`.

::: ufw_logging
    options:
      members: []

## Resetting to installation defaults (stateless)

The reset operation: `ufw.reset` is deliberately **stateless** — a reset
does not map to a persistent desired state, so it reads no fact and
always yields `ufw --force reset`. Destructive: the real command backs up
the rules, deletes every user rule and disables the firewall. Unlike the
idempotent operations there is no "already in state" second pass, so the
script yields it exactly once and documents the rebuild-with-`rule` /
`default_policy` / `state` follow-up. **Compile-checked by the test
suite, not executed against the shared demo hosts**: a reset wipes a
live ruleset. The request is flagged `sudo=True`.

::: ufw_reset
    options:
      members: []

## Managing rules (ports, multiports, subnets)

The rule operation: `ufw.rule` adds or removes one rule, idempotent on
the [`UfwRules`][reemote5.modules.ufw.ufw.UfwRules] fact — ufw accepts
duplicates, so the comparison is what keeps a ruleset clean. The script
allows SSH first (the lockout guard, yielded twice) then opens HTTP/HTTPS
from a trusted subnet with one multiport rule (`port=(80, 443)` — a
tuple, because request arguments must stay hashable). One add covers
both address families (ufw maintains the `(v6)` twin); `present=False`
deletes by specification, never by shifting rule number. **Compile-checked
by the test suite, not executed against the shared demo hosts**: adding
rules to a live firewall is an operator decision. The requests are
flagged `sudo=True`.

::: ufw_rule
    options:
      members: []