# swap — swap file role

| | |
| --- | --- |
| Role | [`geerlingguy.swap`](https://github.com/geerlingguy/ansible-role-swap) |
| Module | [`reemote.roles.swap`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/swap.py) |
| Example | [`examples/roles/swap.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/swap.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `swap` role mapping → `SwapVars` |

<!---
| Waves | 5 on a fresh `present` host — 7 on a resize of an active file, 4 for `absent` with one, 2 converged |
| Outputs | `None` — no composing role in the corpus consumes an output |
| Extra deps | none — the role templates nothing |
| Offline test | [`tests/roles/test_swap.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_swap.py) |

`swap` manages one swap file on Ubuntu hosts: its `/etc/fstab` entry, the file
itself at the wanted size (`dd`, mode `0600` root:root, `mkswap`), its
activation (`swapon`, and `swapoff` before a removal or a resize), and
`vm.swappiness` in `/etc/sysctl.d/90-swap.conf`.

Like [`nfs`](nfs.md), the role's whole `tasks/main.yml` — plus its
`check-size`/`disable`/`enable` includes — is one per-host generator, and the
read the play took *between* steps (the `stat` register the branch conditions
read) is a barrier inside that generator. swap is the corpus's clearest **fact
fork** case: one `fact(files.File)` barrier decides which of the two include
branches run, and the four `command:`/`shell:` tasks become `act(Shell)`
barriers whose results the body checks itself:

```python
yield op(server.fstab, …)                             # wave 0
stat = yield fact(files.File, path=hv.file_path)      # check-size.yml's register
if not present or existing_size_mb(stat) != hv.size_mb:
    yield from disable(hv, exists=stat is not None)(host)
if present:
    yield from enable(hv, created=…)(host)
```

Nothing is gathered into a module-level snapshot dict first. There is
deliberately **no checkpoint** — the role ships no `handlers/main.yml`, and no
later decision reads an earlier *request*'s outcome; the decisions read the
fact barrier and the act results.

## 1. What it is, and where it came from

Status vocabulary: **covered** (a direct operation), **barrier** (a read the
body parks on), **recipe** (a documented shape change), **deviation** (a
deliberate porting choice).

| Ansible task | reemote request | Status |
| --- | --- | --- |
| `include: check-size.yml` — `stat: path={{ swap_file_path }}`, `register: swap_file_stat`, the two `set_fact` size branches | `stat = yield fact(files.File, path=hv.file_path)` + the pure `existing_size_mb` | barrier |
| `mount: name=none src={{ swap_file_path }} fstype=swap opts=sw dump=0 passno=0 state={{ … }}` | `server.fstab(device=…, mountpoint="none", fs_type="swap", options=("sw",), present=…)` | covered |
| `command: swapoff {{ … }}`, `when: not swap_test_mode`, `removes:`-style gating | `fact(hardware.SwapEnabled)` → `act(Shell, "swapoff …")` only when any swap is active | barrier, deviation (D2) |
| `file: path={{ … }} state=absent` | `files.file(path=…, present=False, force=True)` | covered |
| `shell: {{ swap_file_create_command }}`, `creates: {{ swap_file_path }}`, `register: swap_file_create` | `act(Shell, dd)` gated on the same size comparison | recipe (D3) |
| `file: mode=0600 owner=root group=root`, `when: not ansible_check_mode` | `files.file(mode="0600", user="root", group="root")` | covered, deviation (D4) |
| `command: mkswap {{ … }}`, `when: swap_file_create is changed` | `act(Shell, "mkswap …")` on the `created` condition | recipe (D3) |
| `command: swapon {{ … }}`, `when: not swap_test_mode` + the create condition | `act(Shell, "swapon …")` on both | covered |
| `file: path={{ swap_sysctl_file dirname }} state=directory` | `files.directory(path=posixpath.dirname(…), mode="0755")` | covered |
| `sysctl: name=vm.swappiness value={{ … }} sysctl_file={{ … }} sysctl_set=true` | `sysctl.setting(key=…, value=…, sysctl_file=…, sysctl_set=True)` | covered |

No library gap blocked this migration, so none was filed: `server.fstab`
already carries the `mount` module's keyed-edit semantics (its docstring's own
use case is a swap file), `hardware.SwapEnabled` answers the `swapoff`/`swapon`
question, and `sysctl.setting` persists *and* applies in one operation. Both
`swap_file_state` values — the default `present` path and the `absent`
teardown — are implemented.

## 2. Install

```bash
pip install reemote
```

No extra: the role renders no templates and reads no YAML. SSH access to each
Ubuntu host with the key named in its `connection`, and passwordless sudo (or
a `privilege` section in the inventory): every write carries `sudo=True`,
because reemote has no play-level `become`. The `dd` of a `size_mb`-megabyte
file needs that much free space on the host's `/` — a common override is
`create_command: "fallocate -l …"`, which the role passes through verbatim.

-->

::: reemote.roles.swap
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - swap

**Variables:**

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `file_path` | `str` | `/swapfile` — must be absolute | `swap_file_path` |
| `size_mb` | `int` | `512` — the `'512'` string default coerces (D6) | `swap_file_size_mb` |
| `swappiness` | `int` | `60` (0–200) — the example pins `10` | `swap_swappiness` |
| `sysctl_file` | `str` | `/etc/sysctl.d/90-swap.conf` | `swap_sysctl_file` |
| `state` | `str` | `present` — or `absent` (D6) | `swap_file_state` |
| `create_command` | `str` | `null` → the generated quoted `dd` (D5) | `swap_file_create_command` |
| `test_mode` | `bool` | `false` — gates `swapon`/`swapoff` only | `swap_test_mode` |

<!---

Precedence is `model defaults < inventory role mapping < overrides`. The model
is frozen and hashable; there are no sequence fields — the fstab options tuple
is the role's own fixed `("sw",)`. The type gates are pydantic's: `size_mb` is
`gt=0`, `swappiness` is `ge=0, le=200`, `state` is a `Literal`, and the two
paths must be absolute — a rejected value fails at `resolve_vars` time, before
any host is contacted (D6).

## 5. Run it

The role is library code; `examples/roles/swap.py` is the runnable artifact —
a standalone program with its own `main()`, no shared runner. It loads and
validates `inventory.json` from the current working directory, resolves the
role's variables (the defaults overlaid with the entry's `swap` role mapping,
then the call-site `overrides`), builds the deploy from the `swap` factory and
drives it with `run_waves` — a `dry=True` one-wave preview, then the real
apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import SwapVars, swap

SWAP_OVERRIDES = {"swappiness": 10}

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(SwapVars, "swap", inventory, overrides=SWAP_OVERRIDES)
    deploy = swap(vars_set)
    report, _hosts = await run_waves(inventory, deploy, dry=True)   # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)             # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/swap.py
```

The `ubuntu` entry of the shipped `inventory.json` carries no `swap` role
mapping, so the program's call-site `overrides` decide the site: `swappiness`
is pinned to `10` — the value this repo's `sysctl.setting` example tunes
`vm.swappiness` to, where the upstream default is `60` — while the file stays
the default `/swapfile` at 512 MB. `main()` prints the wave tree and maps the
role's failure modes (bad inventory, `ValueError` from a
rejected variable, `CommandError` from a failing `dd`/`mkswap`/`swapon`/
`swapoff`, `HostConnectionError`, `PrivilegeError`, `OSError`, `TypeError`) to
a one-line stderr message and exit 1.

**Run the program twice.** The second run must report no change *and run no
command*: the fstab line is there, the file exists at the wanted size at
`0600` root:root, and `vm.swappiness` already reads `10` — so the size barrier
says "match" and the whole `dd`/`mkswap`/`swapon` chain is not merely
idempotent, it is absent. That is the migration's idempotence claim, and the
offline suite asserts it.

> **Shared demo host.** The `ubuntu` demo host also runs the other role
> examples. This role adds one `/swapfile` line to `/etc/fstab`, writes
> `/etc/sysctl.d/90-swap.conf` and activates 512 MB of swap — none of which
> affects any other role. To undo it after a real run, rerun the program with
> `"state": "absent"` added to `SWAP_OVERRIDES`: the role deactivates, deletes
> the file and drops the fstab line. The swappiness drop-in survives the
> teardown, exactly as upstream leaves it.

## 6. What it does

One deploy. Each barrier closes its own wave, and a wave with no records is
not appended — so the wave count is **host-dependent**, which is the point of
the barrier model:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `server.fstab` (present or absent with `state`) — closed by `fact(files.File)` | the fact barrier **is** the role's `stat` register plus `disable.yml`'s `removes:` gate: one read decides the size comparison and whether there is a file to deactivate |
| 1 | `files.file(present=False)` removal — closed by `act(Shell, dd)` | on a fresh host the removal is the no-op it looks like and `dd` builds the file; when a file *is* there to replace, two extra waves precede these — `fact(hardware.SwapEnabled)` (D2), then `act(Shell, swapoff)` only if that read says swap is active |
| 2 | `files.file` mode request — closed by `act(Shell, mkswap)` | the mode request is emitted even on a pass that does not build: it is what converges `dd`'s umask-default `644` to `0600` root:root; `mkswap` runs only on the building pass (D3) |
| 3 | `act(Shell, swapon)` alone closes this wave | the one command `test_mode` gates on the build path; the next request opens a fresh wave |
| 4 | `files.directory` for `sysctl.d`, then `sysctl.setting` | no barrier separates the pair — the driver applies each request before pulling the next, so the drop-in is written only after its parent directory is ensured |

A fresh `present` host is five waves — the tree pinned below. A wrongly sized
*active* file is seven: the `SwapEnabled` read and the `swapoff` it gates add
one wave each before the build chain resumes. `absent` with an active file is
four; `absent` with no file, two. Converged: two — the fstab wave closed by
the size read, then one wave of no-change requests, no commands at all.

A host whose `host.family` is not `"Ubuntu"` returns before its first `yield`,
so it emits no requests, no barriers and no report lines at all.

## 7. Transcript

From `tests/roles/test_swap.py::test_tree_snapshot`, against the simulated
Ubuntu host plus a RedHat host the role never touches. Nothing is set up yet:
the file is missing, the fstab line is not there. Fresh host, first pass:

```text
(root)
  wave 0
    ubuntu: Manage swap file entry in fstab. [changed: /swapfile entry will be written to /etc/fstab]
        $ scp <fstab file> -> /tmp/reemote-server-fstab-451675132d5b96cc/fstab
        $ sudo sh -c 'mkdir -p /etc && cp /tmp/reemote-server-fstab-451675132d5b96cc/fstab /etc/fstab && chmod 0644 /etc/fstab && rm -rf /tmp/reemote-server-fstab-451675132d5b96cc'
    barrier: Check if swap file exists → None
  wave 1
    ubuntu: Ensure swap file doesn't exist (if configured). [no change: file /swapfile does not exist]
    barrier: Ensure swap file exists. → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 2
    ubuntu: Set permissions on swap file. [changed: mode 644 differs, will chmod 600]
        $ sudo chmod 600 /swapfile
    barrier: Make swap file if necessary. → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 3
    barrier: Run swapon on the swap file. → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 4
    ubuntu: Ensure sysctl config directory exists. [changed: /etc/sysctl.d missing, will create]
        $ sudo mkdir -p /etc/sysctl.d
        $ sudo chmod 755 /etc/sysctl.d
        $ sudo chown root:root /etc/sysctl.d
    ubuntu: Set swappiness.          [changed: vm.swappiness not set to 60 at runtime, will apply; /etc/sysctl.d/90-swap.conf missing or differs for vm.swappiness, will write]
        $ sudo sysctl -w vm.swappiness=60
        $ scp <sysctl conf> -> /tmp/reemote-sysctl-4e728f06e70ce371/sysctl.conf
        $ sudo sh -c 'mkdir -p /etc/sysctl.d && cp /tmp/reemote-sysctl-4e728f06e70ce371/sysctl.conf /etc/sysctl.d/90-swap.conf && chmod 0644 /etc/sysctl.d/90-swap.conf && rm -rf /tmp/reemote-sysctl-4e728f06e70ce371'
        $ sudo sysctl -p /etc/sysctl.d/90-swap.conf
```

The three `barrier: … → {'exit_status': 0, …}` lines are the `act(Shell)`
results — data, not log entries; the body raises `CommandError` itself if any
exits non-zero. The missing file answered `None`, so the disable branch
emitted only its no-op removal and skipped the `swapoff` gate entirely — a
resize of an *active* file adds the two deactivation waves before wave 1, as
§6 describes. The `644 → 600` mode fix-up converges the permissions `dd`
leaves behind.

The rerun — the size barrier now says "already the wanted size", so **the
build commands disappear from the stream entirely**:

```text
(root)
  wave 0
    ubuntu: Manage swap file entry in fstab. [no change: /swapfile entry already present in /etc/fstab]
    barrier: Check if swap file exists → {'user': 'root', 'group': 'root', 'mode': 600, 'size': 536870912}
  wave 1
    ubuntu: Set permissions on swap file. [no change: file /swapfile already exists]
    ubuntu: Ensure sysctl config directory exists. [no change: directory /etc/sysctl.d already exists]
    ubuntu: Set swappiness.          [no change: vm.swappiness already set to 60 at runtime; /etc/sysctl.d/90-swap.conf already up to date for vm.swappiness]
```

The RedHat host appears in no tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | A host outside the `"Ubuntu"` family emits **nothing at all** | the family guard is the body's opening `if`, not a `when=` on each request. Upstream carries no `include_vars` dispatch — its tasks are Linux-generic — so scoping to Ubuntu costs nothing in shape |
| D2 | The `swapoff` gate is `fact(hardware.SwapEnabled)`, not `ansible_facts.memory_mb['swap']['total'] > 0` | the same question — is any swap active? — asked without reading the whole memory layout |
| D3 | `creates:` and `when: swap_file_create is changed` collapse onto the size comparison | the disable branch removes the file whenever the sizes differ, so `dd` runs exactly when upstream's `creates:` gate would have let it, and `mkswap`/`swapon` share that one condition |
| D4 | `when: not ansible_check_mode` is dropped | a `run_waves(dry=True)` pass applies nothing at all, so there is no half-created file to protect |
| D5 | `create_command` defaults to `None`, meaning the generated `dd` | the generated command shell-quotes the path, where upstream's Jinja default interpolated it bare; an explicit override runs verbatim, as upstream's does. The README documents only this one of the role's seven defaults — `test_mode` and the rest are carried from `defaults/main.yml` alone |
| D6 | Type gates move to `SwapVars` | the role's `'512'`/`'60'` string defaults coerce to `int`; anything else is a pydantic rejection at `resolve_vars` time — before any host is contacted — not a mid-play `dd count='abc'` failure |
| D7 | A non-regular file at `file_path` reads as size `0` and is removed with `force=True` | the shape of Ansible's `file: state=absent` for any path type; upstream would compare the inode size of a directory and fail later, inside `dd` |

## 9. Scoped out

* **Non-Ubuntu families.** Upstream's tasks run on any Linux, but the
  migration targets the Ubuntu family; other hosts are skipped, not mapped —
  paths like `/etc/sysctl.d/90-swap.conf` and the `sudo` privilege model are
  spelled per family elsewhere in the corpus, and this role does not pretend
  otherwise.
* **More than one swap file.** The role manages the one `swap_file_path` —
  one fstab line, one file, one swappiness value — and so does this migration.
  A second swap file is a second `swap(...)` deploy over a second
  `SwapVars.site(...)`, which composition (scoped labels) handles, not the
  role.
* **Swap partitions and zram.** `mkswap` on a block device, `resume` offsets
  and compressed-in-RAM swap have no counterpart in the upstream role to map.
* **Removing the swappiness drop-in.** `state: absent` tears down the file and
  the fstab line but leaves `/etc/sysctl.d/90-swap.conf` and the live kernel
  value alone — upstream does the same, so the migration deliberately does not
  "improve" the teardown.
* **Per-host privilege.** The swap commands need root and the inventory says
  how a host elevates; there is no play-level `become` to reproduce.

-->
