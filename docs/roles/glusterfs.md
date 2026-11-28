# glusterfs — GlusterFS server role

| | |
| --- | --- |
| Role | [`geerlingguy.glusterfs`](https://github.com/geerlingguy/ansible-role-glusterfs) |
| Module | [`reemote.roles.glusterfs`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/glusterfs.py) |
| Example | [`examples/roles/glusterfs.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/glusterfs.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `glusterfs` role mapping → `GlusterfsVars` |

<!---
| Waves | 2 (2 on a converged rerun — the checkpoint still closes wave 0) |
| Outputs | `None` — no composing role in the corpus consumes an output |
| Extra deps | none — the role renders nothing, it is packages and one unit |
| Offline test | [`tests/roles/test_glusterfs.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_glusterfs.py) |

`glusterfs` installs and starts GlusterFS on Ubuntu hosts: the `dirmngr` (and
`software-properties-common`) prerequisites, the optional
`ppa:gluster/glusterfs-<version>` with the reinstall-on-PPA-add split, the
`glusterfs-server` / `glusterfs-client` packages, and the `glusterd` unit
started and enabled at boot.

Like [`nginx`](nginx.md), the role's whole `tasks/main.yml` — the
`include: setup-{{ ansible_distribution }}.yml` dispatch, the Ubuntu setup
tasks and the service task — is one per-host generator, and the read the role
took *between* steps (the `register: glusterfs_ppa_added` change flag) is a
barrier inside that generator. glusterfs is the corpus's clearest **single
gate, no fact** case: one `checkpoint` feeds the reinstall split, and nothing
in the role needs a read of the machine beyond it:

```python
yield from setup(hv)(host)                              # wave 0
setup_report = yield checkpoint("setup done")
ppa_added = changed_hosts(setup_report, (PPA_NAME,))    # register + when: … is changed

yield from install(hv, ppa_added)(host)                 # wave 1
yield from service(hv)(host)                            # same wave, applied in order
```

Nothing is gathered into a module-level snapshot dict first. There is
deliberately **no second checkpoint** after the service task — the role ships
no `handlers/main.yml`, so nothing would ever read that report.

## 1. What it is, and where it came from

Status vocabulary: **covered** (a direct operation), **recipe** (a documented
shape change), **deviation** (a deliberate porting choice).

| Ansible task | reemote request | Status |
| --- | --- | --- |
| `set_fact: glusterfs_daemon=glusterfs-server`, `when: …version == "14.04"` | `GlusterfsVars.daemon` defaults to `glusterd`; the old spelling is a variable | deviation (D2) |
| `include: setup-RedHat.yml`, `when: ansible_os_family == 'RedHat'` | out of scope — the family guard skips the host | deviation (D1) |
| `include: setup-{{ ansible_distribution }}.yml`, `when: … == 'Debian'` | the Ubuntu path only; the manual `apt_key` + `dpkg --print-architecture` + `deb`-line branch is not migrated | deviation (D1, D3) |
| `apt: name=dirmngr state=present` | `apt.packages(packages=("dirmngr", "software-properties-common"))` | covered (D4) |
| `apt_repository: ppa:gluster/glusterfs-{{ version }}`, `update_cache: true`, `when: glusterfs_ppa_use`, `register: glusterfs_ppa_added` | `apt.ppa(src=…, update_cache=True)` behind `if hv.ppa_use` | covered |
| `apt: name=… state=absent`, `when: glusterfs_ppa_added is changed` | `changed_hosts(setup report, (PPA_NAME,))` → the conditional removal | recipe (D5) |
| `apt: name=glusterfs-server,glusterfs-client state=present default_release=…` | `apt.packages(packages=GLUSTERFS_PACKAGES, default_release=… or None)` | covered |
| `service: name={{ glusterfs_daemon }} state=started enabled=yes` | `systemd.service(service=hv.daemon, running=True, enabled=True)` | covered |

No library gap blocked this migration, so none was filed: `apt.ppa` already
carries the `apt_repository` + `register` semantics (it reads `AptSources` and
reports changed only on a real add, refreshing the index on the add because
`update_cache=True`), and `systemd.service` is fact-gated on the unit's
active/enabled state. Both Ubuntu branches of the role — the default
`glusterfs_ppa_use: true` path and the plain-distro `false` path — are
implemented; every task they gate maps to a covered operation.

## 2. Install

```bash
pip install reemote
```

No extra: the role renders no templates and reads no YAML. SSH access to each
Ubuntu host with the key named in its `connection`, and passwordless sudo (or
a `privilege` section in the inventory): every request carries `sudo=True`,
because reemote has no play-level `become`. With `ppa_use` set the host also
needs outbound HTTPS to `ppa.launchpad.net` (and Launchpad's APT archive) —
and `add-apt-repository`, which the dependency request itself installs via
`software-properties-common` (D4).

-->

::: reemote.roles.glusterfs
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - glusterfs

**Variables:**

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `daemon` | `str` | `glusterd` — the 14.04 rename is a variable, not a `set_fact` (D2) | `glusterfs_daemon` |
| `default_release` | `str` | `""` → no apt pin | `glusterfs_default_release` |
| `ppa_use` | `bool` | `true` | `glusterfs_ppa_use` |
| `ppa_version` | `str` | `7` — the example pins `11` for modern releases | `glusterfs_ppa_version` |
| `gpg_key_version` | `str` | `7` — **inert** (Debian-only) | `glusterfs_gpg_key_version` |
| `deb_version` | `str` | `LATEST` — **inert** (Debian-only) | `glusterfs_deb_version` |

<!---

Precedence is `model defaults < inventory role mapping < overrides`. Sequences
are `tuple`, never `list` — the model is frozen and hashable, and a value that
reaches `op()` must hash; this role has no sequence field, the package pair is
the module constant `GLUSTERFS_PACKAGES`. The two inert knobs belong to the
out-of-scope Debian branch and are carried rather than dropped so an existing
inventory still binds — a test pins that setting them changes no request
(D3).

## 5. Run it

The role is library code; `examples/roles/glusterfs.py` is the runnable
artifact — a standalone program with its own `main()`, no shared runner. It
loads and validates `inventory.json` from the current working directory,
resolves the role's variables (the defaults overlaid with the entry's
`glusterfs` role mapping, then the call-site `overrides`), builds the deploy
from the `glusterfs` factory and drives it with `run_waves` — a `dry=True`
one-wave preview, then the real apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import GlusterfsVars, glusterfs

GLUSTERFS_OVERRIDES = {"ppa_version": "11"}

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(GlusterfsVars, "glusterfs", inventory, overrides=GLUSTERFS_OVERRIDES)
    deploy = glusterfs(vars_set)
    report, _hosts = await run_waves(inventory, deploy, dry=True)   # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)             # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/glusterfs.py
```

The `ubuntu` entry of the shipped `inventory.json` carries no `glusterfs` role
mapping, so the program's call-site `overrides` decide the PPA: the upstream
default `glusterfs_ppa_version: "7"` predates modern releases — the
`glusterfs-7` PPA publishes no suite for current Ubuntu — while
`ppa:gluster/glusterfs-11` publishes jammy and noble. `main()` prints the wave
tree and maps the role's failure modes (bad inventory, `ValueError` from a
rejected variable, `CommandError` — including a PPA whose archive has no suite
for the host's release, `HostConnectionError`, `PrivilegeError`, `OSError`,
`TypeError`) to a one-line stderr message and exit 1.

**Run the program twice.** The second run must report no change: the
dependencies and both GlusterFS packages are installed, the PPA already exists
(so the setup checkpoint names nobody and the reinstall removal is never
emitted), and the `glusterd` unit is running and enabled. That is the
migration's idempotence claim, and the offline suite asserts it.

> **Shared demo host.** The `ubuntu` demo host also runs the other role
> examples. This role leaves `glusterd` listening on its cluster ports with no
> volumes configured, which affects no other role. To stop and disable it
> again after a real run:
>
> ```bash
> ssh <user>@<ubuntu-host> 'sudo systemctl disable --now glusterd'
> ```

## 6. What it does

One deploy. Each barrier closes its own wave, and a wave with no records is
not appended — so the wave count is **host-dependent**, which is the point of
the barrier model:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `setup`: `apt.packages(("dirmngr", "software-properties-common"))`, plus `apt.ppa(…)` when `ppa_use` — closed by `checkpoint("setup done")` | the checkpoint **is** the role's `register: glusterfs_ppa_added`: it reads the wave it just closed and names the hosts whose PPA request changed |
| 1 | `install` then `service`: the conditional removal (only on a host the checkpoint named), `apt.packages(GLUSTERFS_PACKAGES)`, then `systemd.service(…, running=True, enabled=True)` | no barrier separates them — the driver applies each request before pulling the next, so the unit starts only after its packages installed |

Two ordering rules are load-bearing, and the layout above is what makes them
hold without any extra machinery:

* **One `changed_hosts` read over one checkpoint.** The reinstall gate reads
  the *setup* report; there is no handler channel, so no second checkpoint.
  The barrier sequence is exactly `[CheckpointRequest]`.
* **Requests do not close waves.** The removal, the install and the service
  task are all wave 1, applied per host in body order — the role's task order,
  removal before install before start.

A host whose `host.family` is not `"Ubuntu"` returns before its first `yield`,
so it emits no requests, no barriers and no report lines at all.

## 7. Transcript

From `tests/roles/test_glusterfs.py::test_tree_snapshot`, against the
simulated Ubuntu host plus a RedHat host the role never touches. The
inventory's `glusterfs` role mapping sets `ppa_use: true`; nothing is installed
yet. Fresh host, first pass:

```text
(root)
  wave 0
    ubuntu: Ensure dependencies are installed [changed: 2 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install dirmngr software-properties-common
    ubuntu: Add PPA for GlusterFS    [changed: ppa ppa:gluster/glusterfs-7 will be added; apt update]
        $ sudo apt-add-repository -y ppa:gluster/glusterfs-7
        $ sudo apt-get update
    barrier: setup done → 1 host notified
  wave 1
    ubuntu: Ensure GlusterFS will reinstall if the PPA was just added [no change: no packages to remove]
    ubuntu: Ensure GlusterFS is installed [changed: 2 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install glusterfs-server glusterfs-client
    ubuntu: Ensure GlusterFS is started and enabled at boot [changed: glusterd.service not running (None), will start; glusterd.service not enabled, will enable]
        $ sudo systemctl start glusterd.service
        $ sudo systemctl enable glusterd.service
```

The `; apt update` in the PPA line is the `update_cache=True` refresh riding
the real add — no separate cache task exists in this role (the upstream role
has none either; the Debian branch's `apt_key` sibling does not run on
Ubuntu). The reinstall removal is present but a no-op: nothing was installed
when the PPA went in. With the packages *pre*installed from the distro
archive, the same request instead reports `changed: 2 package(s) to remove`
and the install following it reinstalls from the PPA — the trick, pinned by
its own test.

The rerun — the checkpoint still runs (it is a read of the wave's real
outcomes), names nobody, and **the reinstall request disappears entirely**:

```text
(root)
  wave 0
    ubuntu: Ensure dependencies are installed [no change: all packages present]
    ubuntu: Add PPA for GlusterFS    [no change: ppa ppa:gluster/glusterfs-7 exists]
    barrier: setup done → 0 hosts notified
  wave 1
    ubuntu: Ensure GlusterFS is installed [no change: all packages present]
    ubuntu: Ensure GlusterFS is started and enabled at boot [no change: glusterd.service already running; glusterd.service already enabled]
```

The RedHat host appears in no tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | A host outside the `"Ubuntu"` family emits **nothing at all** | the family guard is the body's opening `if`, not a `when=` on each request. The `include: setup-{{ ansible_distribution }}.yml` dispatch and the `RedHat` include collapse to the Ubuntu path alone |
| D2 | The 14.04 `set_fact` daemon rename is **dropped**; `daemon` is a variable | the rename fires only on Ubuntu 14.04, whose upstart hosts `systemd.service` cannot manage anyway. `daemon` defaults to `glusterd`; the legacy spelling is one inventory key away, not a version probe |
| D3 | The Debian knobs `gpg_key_version` / `deb_version` are **carried and inert** | they belong to `setup-Debian.yml` (the manual `apt_key` + `dpkg --print-architecture` + `deb`-line branch), which the Ubuntu target leaves out of scope. Documented rather than silently dropped so such keys still bind; a test pins that setting them changes no request |
| D4 | `software-properties-common` joins the dependency install when `ppa_use` is set | Ansible's `apt_repository` auto-installs the package that ships `add-apt-repository`; reemote's `apt.ppa` raises `CommandNotFoundError` without it. One extra name in one existing request — the role's own `dirmngr` task is otherwise unmoved |
| D5 | The reinstall split is a **`checkpoint` + `changed_hosts` read**, not a register variable | `apt.ppa` reports changed only on a real add (it reads `AptSources`), so the conditional removal rides only the pass that added the PPA and is not even emitted after — the honest form of the role's `when: glusterfs_ppa_added is changed` |

## 9. Scoped out

* **Non-Ubuntu families.** The role's RedHat branch (`setup-RedHat.yml`,
  `centos-release-gluster…`) and its Debian branch (`setup-Debian.yml`, the
  `download.gluster.org` key + repo with the `dpkg` architecture probe) are
  not migrated. A RedHat or Debian host is skipped, not mapped to a different
  repository layout — those are different roles.
* **Ubuntu 14.04.** Upstart predates systemd, so `systemd.service` cannot
  manage it; the only 14.04-specific behavior in the role — the daemon rename —
  survives as the `daemon` variable (D2).
* **Volumes and bricks.** The role installs and starts `glusterd` and stops
  there; it probes no pool, creates no volume and mounts no brick, and neither
  does this migration. Peer probing and `gluster volume` management have no
  Ansible counterpart in the role to map.
* **Per-host privilege.** GlusterFS needs root and the inventory says how a
  host elevates; there is no play-level `become` to reproduce.

-->
