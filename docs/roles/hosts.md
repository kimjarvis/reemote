# hosts — hostname and hosts-file role

| | |
| --- | --- |
| Role | [`ajsalminen/ansible-role-hosts`](https://github.com/ajsalminen/ansible-role-hosts) |
| Module | [`reemote.roles.hosts`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/hosts.py) |
| Runner | [`examples/roles/hosts.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/hosts.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `hosts` role mapping → `HostsVars` |

<!---
| Waves | 2 (barrier + requests); 1 when `hosts_content` or the address is set |
| Outputs | none — the deploy returns `None` |
| Extra deps | none (`reemote[templates]` is not needed — the templates render in plain Python) |
| Offline test | [`tests/roles/test_hosts.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_hosts.py) |

`hosts` manages the two name files on Ubuntu/Debian hosts with the upstream
role's two templates: `/etc/hostname` holds the short hostname, and
`/etc/hosts` holds a managed header, one line per entry — the IPv4 loopback
line, the host's default-route line, then the operator's additional entries —
a two-blank-line gap and the fixed IPv6 localhost block.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with `yield from scoped("hosts", hosts(v))(host)`
and a run is one `run_waves` pass. The Ansible role's whole task list — two
template tasks behind an OS-dispatch `include_vars` — is that single deploy,
and its `defaults/main.yml` is `HostsVars`.

## 1. What it is, and where it came from

The role's task list maps onto a handful of reemote items; the two columns that
matter are the target and whether the mapping is direct:

| Ansible task | reemote request | Status |
| --- | --- | --- |
| `include_vars` of `vars/Darwin.yml` / `vars/FreeBSD.yml` (the `wheel` group) | frozen Ubuntu path + the body's family guard | deviation (D1/D6) |
| `template: src=hostname.j2 dest=/etc/hostname owner=root group=root mode=0644` | control-side render → `files.install` + companion `files.file` | recipe |
| `template: src=hosts.j2 dest=/etc/hosts …` | control-side render → `files.install` + companion `files.file` | recipe |
| `{{ ansible_default_ipv4.address }}` in `hosts_default_ipv4_hosts` | `yield fact(DefaultIpv4Address)` | barrier |
| `with_first_found` per-host template lookup | `hostname_content` / `hosts_content` verbatim override | deviation (D4) |
| `hosts_override_hosts_template` / `server_override_hosts` branch | scoped out — covered by D4's override | deviation (D5) |

No new library gaps surfaced: the fact (`hardware.DefaultIpv4Address`), the
spool-and-install write (`files.install`) and its metadata companion
(`files.file`) all already ship, so this migration files no issue.

## 2. Install

```bash
pip install reemote          # or: uv add reemote
```

Nothing else. No `reemote[templates]` — both files are rendered with plain
f-strings and a `str.join`, byte-identical to an Ansible-defaults Jinja render
of the upstream templates (verified against them; only the header text
differs, D3).

SSH access to each Ubuntu host with the key named in its `connection`, and
passwordless sudo (or a `privilege` section in the inventory): all four
requests carry `sudo=True`, because `/etc` is root-owned and transfers never
elevate.

## 3. Variables

`HostsVars` is a frozen pydantic model whose fields carry bare names; the role
name passed to `resolve_vars` binds the entry's `hosts` role mapping.

--->

::: reemote.roles.hosts
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - hosts

**Variables:**

| Field | Type | Default | Ansible name |
| --- | --- | --- | --- |
| `ipv4_loopback` | `HostsEntry` | `127.0.0.1 localhost.localdomain localhost` | `hosts_ipv4_loopback_hosts` |
| `default_ipv4_address` | `str \| None` | `null` → the `DefaultIpv4Address` barrier | `ansible_default_ipv4.address` |
| `default_ipv4_hostnames` | `tuple[str, ...] \| None` | `null` → `(host name, short name)` | the `hosts_default_ipv4_hosts` template |
| `additional_hosts` | `tuple[HostsEntry, ...]` | `()` | `hosts_additional_hosts` |
| `file_group` | `str` | `root` | `hosts_file_group` |
| `hostname_content` | `str \| None` | `null` → rendered short name | the `with_first_found` hostname template (D4) |
| `hosts_content` | `str \| None` | `null` → rendered entries | the `with_first_found` hosts template (D4/D5) |

<!---

Precedence is `model defaults < inventory role mapping < overrides`. A `null`
address keeps the fact read — the `ansible_default_ipv4.address` equivalent —
and a set one replaces it, skipping the barrier; `default_ipv4_hostnames`
replaces the whole name tuple of the default-route line. `HostsEntry` is
itself frozen (`address` plus ordered `hostnames`, the first canonical), so
the model stays hashable through the deploy. Validation is fail-fast: an
empty address or hostname group, an empty `default_ipv4_address` and an empty
`file_group` are all rejected, and `extra="forbid"` plus `resolve_vars`'
binding report a key that names no field (`additional_host` raises
`RoleVarsError` rather than silently defaulting).

## 5. Run it

The role is library code; `examples/roles/hosts.py` is the runnable artifact —
a standalone program with its own `main()`, no shared runner. It loads and
validates `inventory.json` from the current working directory, resolves the
role's variables (the defaults overlaid with the entry's `hosts` role
mapping), builds the deploy from the `hosts` factory and drives it with
`run_waves` — a `dry=True` one-wave preview, then the real apply — printing
the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import HostsVars, hosts

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(HostsVars, "hosts", inventory)
    deploy = hosts(vars_set)
    report, _hosts = await run_waves(inventory, deploy, dry=True)  # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)           # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/hosts.py
```

The `dry=True` pass is a one-wave preview: the role's first item is the
`DefaultIpv4Address` barrier (unless a variable supplies the address or the
whole body), so the run parks there (`barrier: DefaultIpv4Address → unknown
until run`), reads no state and applies no request — the converged rerun
below is the real preview, and it is cheap.

`main()` prints the wave tree and maps the role's failure modes (bad
inventory, `ValueError` from a host with no default route and no override,
`CommandError`, `HostConnectionError`, `PrivilegeError`, `OSError`,
`TypeError`) to a one-line stderr message and exit 1.

**Run the program twice.** The second run must report no change: both files
already hold the rendered content, and both companions find owner, group and
mode already right. That is the migration's idempotence claim, and the offline
suite asserts it. Note that reemote — like the upstream role — writes
`/etc/hostname` without calling `hostnamectl`: the live kernel hostname
changes on reboot (or with your own `op(server.shell, ...)` request).

## 6. What it does

One deploy, two waves — the barrier wave, then the wave holding the four
requests:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `fact(DefaultIpv4Address)` | the role's `{{ ansible_default_ipv4.address }}` — the interface carrying the default route, per host, so the read is a barrier; skipped when `default_ipv4_address` or `hosts_content` supplies the value |
| 1 | `install(...)` then `configure(...)`: `files.install(/etc/hostname)`, `files.file(/etc/hostname)`, `files.install(/etc/hosts)`, `files.file(/etc/hosts)` | the role's two template tasks, in the role's order (`hostname.yml` before `hosts.yml`) |

There is **no checkpoint**: the role has no handlers and nothing reads an
outcome mid-play. A host whose `host.family` is not `"Ubuntu"` returns before
its first `yield`, so it emits no requests, no barriers and no report lines
at all.

A host with no default route fails **before the first request**: the barrier
answers nothing, and the body raises `ValueError` telling you to set
`default_ipv4_address` (or a full `hosts_content`), so the machine is never
half-deployed.

## 7. Transcript

From `tests/roles/test_hosts.py::test_tree_snapshot`, against the simulated
Ubuntu host `web1.example.com` (default route `192.168.1.100`) plus a RedHat
host the role never touches. Fresh host, first pass:

```text
(root)
  wave 0
    barrier: DefaultIpv4Address → {'address': '192.168.1.100', 'mask_bits': 24, 'interface': 'eth0', 'gateway': '192.168.1.1'}
  wave 1
    web1.example.com: Set up /etc/hostname.    [changed: content /etc/hostname is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-d9c5f609a163521e/content
        $ sudo sh -c 'mkdir -p /etc && cp /tmp/reemote-install-d9c5f609a163521e/content /etc/hostname && chown root:root /etc/hostname && chmod 644 /etc/hostname && rm -rf /tmp/reemote-install-d9c5f609a163521e'
    web1.example.com: Ensure /etc/hostname ownership and mode. [no change: file /etc/hostname already exists]
    web1.example.com: Set up /etc/hosts.       [changed: content /etc/hosts is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-77dbc373a1fc690f/content
        $ sudo sh -c 'mkdir -p /etc && cp /tmp/reemote-install-77dbc373a1fc690f/content /etc/hosts && chown root:root /etc/hosts && chmod 644 /etc/hosts && rm -rf /tmp/reemote-install-77dbc373a1fc690f'
    web1.example.com: Ensure /etc/hosts ownership and mode. [no change: file /etc/hosts already exists]
```

The rerun — the installs find the content equal, the companions find the
metadata equal, and no line reports a change:

```text
(root)
  wave 0
    barrier: DefaultIpv4Address → {'address': '192.168.1.100', 'mask_bits': 24, 'interface': 'eth0', 'gateway': '192.168.1.1'}
  wave 1
    web1.example.com: Set up /etc/hostname.    [no change: content already up to date (/etc/hostname)]
    web1.example.com: Ensure /etc/hostname ownership and mode. [no change: file /etc/hostname already exists]
    web1.example.com: Set up /etc/hosts.       [no change: content already up to date (/etc/hosts)]
    web1.example.com: Ensure /etc/hosts ownership and mode. [no change: file /etc/hosts already exists]
```

With `hosts_content` set there is no barrier wave at all — the requests start
the tree at wave 0. The RedHat host appears in no tree: no barrier record, no
request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | A host outside the `"Ubuntu"` family emits **nothing at all** | the family guard is the body's opening `if`, not a `when=` on each request |
| D2 | The two `.j2` templates render as **plain Python**, not Jinja2 | each is pure substitution and a for-loop; `render_hosts` was verified byte-identical to an Ansible-defaults render of `templates/hosts.j2`. No `reemote[templates]` extra |
| D3 | `# {{ ansible_managed }}` becomes the constant `# Reemote managed` | reemote has no `ansible_managed` equivalent, and claiming Ansible authorship would be wrong |
| D4 | The `with_first_found` per-host template lookup has no on-disk analogue | a library role ships no template directory; `hosts_content` / `hostname_content` are the verbatim per-host override, bindable through the inventory's `hosts` role mapping |
| D5 | The opt-in `hosts_override_hosts_template` / `server_override_hosts` branch is scoped out | D4's content override covers the same need without a controller-side file search |
| D6 | `vars/Darwin.yml` / `vars/FreeBSD.yml` (group `wheel`) are out of target | `file_group` carries the knob and defaults to `"root"`, the Ubuntu path's value |
| D7 | `hosts_default_hosts \| union(hosts_additional_hosts)` becomes plain concatenation | the defaults are structurally unique; a duplicate an operator adds is rendered as asked |
| D8 | The template task's `owner`/`group`/`mode` are re-applied by a companion `files.file` | `files.install` idempotence is content-only, so without the pair an owner/mode drift would never converge — where Ansible's `template` fixes it on the next run |

## 9. Scoped out

* **The live kernel hostname.** The upstream role only manages the two files
  (the name applies on reboot); the migration changes nothing a
  config-management run should. Set it yourself with an
  `op(server.shell, command="hostnamectl set-hostname …")` request if you
  want it immediately.
* **The override-template branch.** See D5.
* **Non-Ubuntu families.** Darwin/FreeBSD's `wheel` group and any
  other-OS `/etc` quirks are out; a RedHat host is skipped rather than mapped
  — `file_group="wheel"` on a Debian-family host is the only cross-OS knob
  carried.
* **Managing more files.** The role writes exactly `/etc/hostname` and
  `/etc/hosts`. Additional static hosts-file fragments belong in
  `additional_hosts`; anything beyond that is your own `files.install`.

--->
