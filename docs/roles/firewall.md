# firewall — iptables firewall role

| | |
| --- | --- |
| Role | [`geerlingguy.firewall`](https://github.com/geerlingguy/ansible-role-firewall) |
| Module | [`reemote.roles.firewall`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/firewall.py) |
| Example | [`examples/roles/firewall.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/firewall.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `firewall` role mapping → `FirewallVars` |

<!---
| Waves | 2 converged / 3 changed / 4 first run (the flush and handler waves are conditional) |
| Outputs | none — the body returns `None` on every path |
| Extra deps | **`reemote[templates]`** (jinja2 renders `firewall.bash.j2`) |
| Offline test | [`tests/roles/test_firewall.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_firewall.py) |

`firewall` installs and configures an iptables firewall: the `iptables` package,
the once-per-machine `iptables -F` the role gated on `creates=/etc/firewall.bash`,
the rendered `/etc/firewall.bash` script (`0744`, root-owned) and its
`firewall.service` systemd unit, the unit's running/enabled state, and the
role's single `restart firewall` handler.

This is the **script role** of the migration sequence: the managed state is a
bash script that rebuilds the whole rule set, and the migration keeps that
mechanism rather than translating rules into per-port operations. The three
reads the role took between tasks — the `creates=` stat, the flush outcome and
the handler notification — are barriers inside one per-host generator:

```python
yield from install()(host)                        # wave 0
present = yield fact(FileExists, path=FIREWALL_SCRIPT)   # the creates= gate
if not present:
    yield act(Shell, command="iptables -F", sudo=True)   # wave 1, first run
yield from configure(render_firewall_script(hv), hv.state, hv.enabled)(host)
notified = changed_hosts(yield checkpoint("configure done"), NOTIFYING)
yield from handler(notified)(host)
```

Nothing is gathered into a module-level snapshot first, and the script text is
rendered control-side from the variables alone: the per-host body *is* the plan.

## 1. What it is, and where it came from

| Ansible task | reemote request | Status |
| --- | --- | --- |
| `package: name=iptables state=present` | `apt.packages(packages=("iptables",), sudo=True)` | covered |
| `command: iptables -F` + `creates=/etc/firewall.bash` | `yield fact(FileExists, path=…)` gate → `yield act(Shell, "iptables -F", sudo=True)` | barrier (D4) |
| `template: firewall.bash.j2 dest=/etc/firewall.bash mode=0744` + `notify` | control-side render → `files.install(content=…)` + `files.file(mode=…)` | covered |
| `template: firewall.init.j2` (`when: service_mgr != systemd`) | — | scoped out (D2) |
| `template: firewall.unit.j2 dest=/etc/systemd/system/firewall.service` | `files.install(content=UNIT_FILE)` (the template has no variables) | covered |
| `service: name=firewall state={{ firewall_state }} enabled={{ firewall_enabled_at_boot }}` | `systemd.service(running=…, enabled=…, daemon_reload=True)` | deviation (D5) |
| `handlers/main.yml: restart firewall` + the script task's `notify` | `checkpoint("configure done")` → `changed_hosts` → `service_handler(action="restarted", daemon_reload=True)` | recipe |
| `import_tasks: disable-other-firewalls.yml` (`when:` opt-in) | — | scoped out (D3) |
| the role's implicit value gates (port lists, the state string) | pydantic validation on `FirewallVars` | deviation |

No library gap blocked this migration, so none was filed: `FileExists` is the
`creates=` stat, the `act` barrier (issue
[#16](https://github.com/kimjarvis/reemote/issues/16)) runs the flush as a
command whose exit status is data, `files.install` writes the root-owned script
and unit in one request, and `systemd.service(daemon_reload=True)` is
state-gated (issue
[#50](https://github.com/kimjarvis/reemote/issues/50)), so the implicit reload
Ansible's systemd module performed converges instead of re-firing.

## 2. Install

```bash
pip install "reemote[templates]"     # or: uv add "reemote[templates]"
```

`jinja2` is the only extra. The base library imports neither `jinja2` nor
`pyyaml` — they enter through `reemote/roles/_render.py`, and `firewall` imports
that module *inside* `render_firewall_script`, so `import reemote.roles` stays
clean without the extra and a missing `jinja2` surfaces as an `ImportError` at
render time rather than at import time.

SSH access to each Ubuntu host with the key named in its `connection`, and
passwordless sudo (or a `privilege` section in the inventory): every request
carries `sudo=True`, because reemote has no play-level `become` — the role's
README runs the whole play with it.

## 3. Variables

`FirewallVars` is a frozen pydantic model (`extra="forbid"`) whose fields carry
bare names; the role name passed to `resolve_vars` binds the entry's `firewall`
role mapping, so an inventory written for the role needs no key gymnastics.

--->

::: reemote.roles.firewall
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - firewall

**Variables:**

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `state` | `Literal["started","stopped","restarted","reloaded"]` | `started` | `firewall_state` |
| `enabled` | `bool` | `true` | `firewall_enabled_at_boot` |
| `flush_rules_and_chains` | `bool` | `true` | `firewall_flush_rules_and_chains` |
| `template` | `str` | `firewall.bash.j2` | `firewall_template` |
| `allowed_tcp_ports` | `tuple[str, …]` | `("22", "25", "80", "443")` | `firewall_allowed_tcp_ports` |
| `allowed_udp_ports` | `tuple[str, …]` | `()` | `firewall_allowed_udp_ports` |
| `forwarded_tcp_ports` | `tuple[ForwardedPort, …]` | `()` | `firewall_forwarded_tcp_ports` |
| `forwarded_udp_ports` | `tuple[ForwardedPort, …]` | `()` | `firewall_forwarded_udp_ports` |
| `additional_rules` | `tuple[str, …]` | `()` | `firewall_additional_rules` |
| `enable_ipv6` | `bool` | `true` | `firewall_enable_ipv6` |
| `ip6_additional_rules` | `tuple[str, …]` | `()` | `firewall_ip6_additional_rules` |
| `log_dropped_packets` | `bool` | `true` | `firewall_log_dropped_packets` |
| `disable_firewalld` | `bool` | `false` — **inert** (scoped out, D3) | `firewall_disable_firewalld` |
| `disable_ufw` | `bool` | `false` — **inert** (scoped out, D3) | `firewall_disable_ufw` |

<!---

Precedence is `model defaults < inventory role mapping < overrides>`. Sequences
are `tuple`, never `list`: the model is frozen and hashable, and a value that
reaches `op()` must hash. Ports bind from ints or strings — an unquoted YAML
`22` becomes the string the script renders. The two inert knobs are carried
rather than dropped so an existing inventory still binds — a test pins that
setting them changes no request.

`template` follows the role's own convention: `firewall.bash.j2` is the bundled
template inlined in the module, and **any other value is a controller-side
path**, read by `render_firewall_script` at build time (D6). That read is
ordinary Python inside the body, not a `control` barrier, so a custom template
costs no extra wave.

### 3.1 Forwarded-port mapping fields

| Field | `ForwardedPort` |
| --- | --- |
| `src` | **required**, non-empty (int or string) |
| `dest` | **required**, non-empty (int or string) |
| anything else | kept (`extra="allow"`) |

Each entry renders the role's two `REDIRECT` rules (the `PREROUTING` one and the
loopback `OUTPUT` one) for its protocol.

## 5. Run it

The role is library code; `examples/roles/firewall.py` is the runnable artifact
— a standalone program with its own `main()`, no shared runner. It loads and
validates `inventory.json` from the current working directory, resolves the
role's variables (the defaults overlaid with the entry's `firewall` role
mapping, then the call-site `overrides`), builds the deploy from the `firewall`
factory and drives it with `run_waves` — a `dry=True` one-wave preview, then the
real apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import FirewallVars, firewall

FIREWALL_OVERRIDES = {
    "allowed_tcp_ports": ("22", "80", "443"),
    "forwarded_tcp_ports": ({"src": 8080, "dest": 80},),
}

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(FirewallVars, "firewall", inventory, overrides=FIREWALL_OVERRIDES)
    deploy = firewall(vars_set)
    report, _hosts = await run_waves(inventory, deploy, dry=True)   # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)             # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/firewall.py
```

The `dry=True` pass is a one-wave preview: it plans the package request, parks
on the `FileExists` barrier and stops (`barrier: FileExists → unknown until
run`), gathering facts over SSH but applying nothing. A converged rerun is the
real preview.

**Read the warning before the first live run.** The role *is* a firewall: on a
host that has never run it, the first pass flushes the current rules and
installs one that drops everything except the ports you opened. Keep your SSH
port in `allowed_tcp_ports` — the role's defaults keep `22`.

**Run the program twice.** The second run must report no change and queue no
restart: the package is installed, the script and the unit are byte-identical,
the modes match, the unit is running and enabled, and no daemon reload is
pending. That is the migration's idempotence claim, and the offline suite
asserts it.

### 5.1 Composing this role

`examples/roles/firewall.py` runs `firewall` on its own. To use it as a
*component* of a larger deploy, see
[`examples/roles/composing_roles.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/composing_roles.py):
a parent per-host generator embeds the role with
`yield from scoped("firewall", firewall(v))(host)`. The role publishes no
outputs — nothing in the corpus consumes any — so it returns `None` on every
path, the family-guard one included, and a composing parent simply awaits the
deploy's effects, not a value.

## 6. What it does

One deploy. Each barrier closes its own wave, and a wave with no records is not
appended — so the wave count is **host-dependent**, which is the point of the
barrier model:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `install`: `apt.packages(("iptables",))` — closed by `fact(FileExists, path=/etc/firewall.bash)` | the gate is the role's `creates=` stat; as a barrier it reads the *previous* converge's file, never one this wave is about to write |
| 1 | the flush: `act(Shell, "iptables -F", sudo=True)` — **first run only** | the gate answered `False`; a converged host parks on nothing here, so its stream skips the wave entirely |
| 2 | `configure`: the rendered script and its chmod companion, the unit file, the service state — closed by `checkpoint("configure done")` | the checkpoint **is** the role's `notify`: it reads the wave it just closed and names the hosts whose notifying requests changed |
| 3 | `handler`: `service_handler(notified, ("firewall",), action="restarted", daemon_reload=True)` | only on the hosts that checkpoint selected |

Three ordering rules are load-bearing:

* **The unit file precedes the service request in the same wave.** The driver
  applies each request before pulling the next, so the state-gated
  `daemon_reload` (D5) sees this wave's write — the reload and the start land in
  one pass (the systemd role's proven sequencing).
* **The checkpoint immediately follows `configure`.** A checkpoint placed after
  the handler wave would read the handler's entries instead and notify nobody.
* **`restarted` is stateless and always reports changed** — faithful to
  Ansible's service module, and why a converged host that *did* change the
  script still shows a handler change.

A host whose `host.family` is not `"Ubuntu"` returns before its first `yield`,
so it emits no requests, no barriers and no report lines at all.

## 7. Transcript

From `tests/roles/test_firewall.py::test_tree_snapshot`, against a simulated
Ubuntu host with the role's default variables plus a RedHat host the role never
touches. Fresh host, first pass:

```text
(root)
  wave 0
    ubuntu: Ensure iptables is present [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install iptables
    barrier: FileExists → False
  wave 1
    barrier: Flush iptables the first time playbook runs → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 2
    ubuntu: Copy firewall script into place [changed: content /etc/firewall.bash is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-…/content
        $ sudo sh -c 'mkdir -p /etc && cp … /etc/firewall.bash && chown root:root /etc/firewall.bash && chmod 744 /etc/firewall.bash && rm -rf …'
    ubuntu: Set firewall script mode [no change: file /etc/firewall.bash already exists]
    ubuntu: Copy firewall systemd unit file into place [changed: content /etc/systemd/system/firewall.service is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-…/content
        $ sudo sh -c 'mkdir -p /etc/systemd/system && cp … /etc/systemd/system/firewall.service && chown root:root … && chmod 644 … && rm -rf …'
    ubuntu: Configure the firewall service [changed: daemon-reload; firewall.service not running (None), will start; firewall.service not enabled, will enable]
        $ sudo systemctl daemon-reload
        $ sudo systemctl start firewall.service
        $ sudo systemctl enable firewall.service
    barrier: configure done → 1 host notified
  wave 3
    ubuntu: Restart firewall (handler) [changed: firewall.service already running; firewall.service will restart]
        $ sudo systemctl restart firewall.service
```

The gate barrier prints the fact's bare answer (`False` on a never-configured
host) and the flush barrier prints its raw `Result` dict; the two `scp` spool
directories are content-addressed, so the real run shows their hashes where `…`
is above — the test pins them.

The rerun — the gate still reads (it is a read, not a cached plan value), the
flush wave is gone because the script now exists, the checkpoint finds nothing,
and **the handler wave disappears too**: two waves, no change:

```text
(root)
  wave 0
    ubuntu: Ensure iptables is present [no change: all packages present]
    barrier: FileExists → True
  wave 1
    ubuntu: Copy firewall script into place [no change: content already up to date (/etc/firewall.bash)]
    ubuntu: Set firewall script mode [no change: file /etc/firewall.bash already exists]
    ubuntu: Copy firewall systemd unit file into place [no change: content already up to date (/etc/systemd/system/firewall.service)]
    ubuntu: Configure the firewall service [no change: firewall.service already running; firewall.service already enabled]
    barrier: configure done → 0 hosts notified
```

The RedHat host appears in no tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | Ubuntu only, guard-first | the role's generic `package:`/`service:` and its RedHat/firewalld and Arch branches collapse to apt and systemd, which is what they resolve to on the target. A host outside the family returns before its first `yield` and emits no records |
| D2 | The init-script fork (`/etc/init.d/firewall`) is dropped | the target family ships systemd, so the unit-file task is unconditional. A multi-init target would select between the two with `fact(server.InitSystem)` |
| D3 | `disable_firewalld`/`disable_ufw` are **carried and inert** | the opt-in `disable-other-firewalls.yml` import was scoped out by agreement with the operator before the migration started. Kept so such a key still binds |
| D4 | `creates=` is an in-stream **`fact(FileExists)` barrier**, the flush an **`act` barrier** | the role's command task and its gate are exactly the reads the barrier model exists for. A barrier's `Result` is data, so the body checks `exit_status` itself and raises `CommandError` — the role ran the play with `become:`, hence `sudo=True` |
| D5 | `daemon_reload=True` is **explicit** on the service request and the handler | Ansible's systemd module reloads the daemon implicitly when unit files changed; reemote asks. The flag is state-gated (issue #50), so a converged run issues no reload and the pass stays a no-op |
| D6 | A custom `template` is a **controller-side path** | reemote roles carry their bundled template as a module constant and have no `templates/` directory; anything else is read on the controller, like apache's `vhosts_template` |
| D7 | The rule set stays a **rendered script**, not per-port `iptables.rule` operations | `firewall_additional_rules` are raw shell lines only the script can splice, and an operation-level `iptables.flush` would wipe a converged firewall on every run. The handler therefore re-executes the script via `systemctl restart firewall`, exactly as the role's does |
| D8 | The role's implicit gates become **pydantic validation** | `state` is a `Literal`, forwarded ports require non-empty `src`/`dest` and bind from ints, and a bad value raises `ValidationError` (a `ValueError`) while the inventory is resolved — before any host is contacted |

## 9. Scoped out

* **Non-Ubuntu families.** The role's RedHat (firewalld-adjacent) and Arch
  branches are not migrated; `package:`/`service:` become apt and systemd.
* **The opt-in software disable.** `firewall_disable_firewalld` and
  `firewall_disable_ufw` would be `apt.packages(present=False, purge=True)` (or
  `dnf`, off-target) requests behind an `if` — deliberately excluded from this
  migration's scope, not expressible as a gap.
* **Sysctl tuning.** The role's README shows a companion `sysctl` play
  (`net.ipv4.ip_forward`); it was never part of `tasks/main.yml`. reemote ships
  `sysctl.sysctl` for it in a caller's own program.
* **Removing stale rules.** A rule that left `allowed_tcp_ports` converges
  because the whole script is rewritten and the unit restarted — but a
  hand-added rule *outside* the script is untouched until the next
  `iptables -F`-equivalent, which the role (correctly) only ever runs once.
* **Per-host privilege.** The firewall needs root and the inventory says how a
  host elevates; there is no play-level `become` to reproduce.

--->
