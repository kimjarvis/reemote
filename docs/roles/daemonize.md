# daemonize — build the `daemonize` utility from source

| | |
| --- | --- |
| Role | [`geerlingguy.daemonize`](https://github.com/geerlingguy/ansible-role-daemonize) |
| Module | [`reemote.roles.daemonize`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/daemonize.py) |
| Example | [`examples/roles/daemonize.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/daemonize.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `daemonize` role mapping → `DaemonizeVars` |

<!---
| Waves | 2 fresh / 1 converged (a host that already has the binary never emits the build wave) |
| Outputs | `None` — no composing role in the corpus consumes an output |
| Extra deps | none — the role renders nothing, it is one tarball and one build |
| Offline test | [`tests/roles/test_daemonize.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_daemonize.py) |

`daemonize` builds and installs the `daemonize` utility from its GitHub release
tarball on Ubuntu hosts: the `build-essential` toolchain the role's README names
as a prerequisite, the `release-<version>` tarball extracted into the workspace
behind Ansible's `creates:` gate, and — only on a host where the binary is not
already on `PATH` — the `./configure` / `make` / `make install` build, as root.

The role's whole `tasks/main.yml` — four tasks, no handlers, no templates — is
one per-host generator, and the read it took *between* steps (the
`register: daemonize_installed`, then `when: … rc != 0`) is a barrier inside
that generator. daemonize is the corpus's clearest **single fact, no checkpoint**
case: one `fact(server.Which)` read gates the imperative build, and there is no
`handlers/main.yml` for a checkpoint to feed:

```python
yield from setup(plan)(host)                       # wave 0: apt toolchain + unarchive
installed = yield fact(server.Which, command="daemonize")   # the register + when
if installed is None:
    yield from build(plan)(host)                   # wave 1: the act(Shell) compile
```

Nothing is gathered into a module-level snapshot dict first, and there is
deliberately **no `checkpoint`** — the role ships no handlers, so no report would
ever be read, and no stateless operation (a restart, a reload) needs gating
behind one.

## 1. What it is, and where it came from

Status vocabulary: **covered** (a direct operation), **barrier** (a fact/act read
the body needs), **recipe** (a documented shape change), **deviation** (a
deliberate porting choice).

| Ansible task | reemote request | Status |
| --- | --- | --- |
| `include_vars` OS dispatch, `meta/main.yml` Ubuntu/Debian/GenericUNIX | no `vars/` file exists; the family guard skips a host outside `"Ubuntu"` | deviation (D1) |
| (README prerequisite: `gcc`/`make`, not a task) | `apt.packages(packages=("build-essential",))`, fact-gated on `AptPackages` | deviation (D5) |
| `get_url: url=…release-{{ version }}.tar.gz dest={{ workspace }}/…` + `unarchive: src=…{{ workspace }}/….tar.gz copy: false creates=…/INSTALL` | one `files.unarchive(src=<url>, dest=workspace, creates=<src>/INSTALL)` — the URL is fetched control-side | recipe (D2) |
| `unarchive: mode: 0755` | dropped: `files.unarchive(mode=…)` renders a `chmod -R` of the shared workspace | deviation (D3) |
| `command: which daemonize`, `changed_when: false`, `failed_when: false`, `register: daemonize_installed` | `installed = yield fact(server.Which, command="daemonize")` → `str \| None` | barrier (D7) |
| `command:` loop (`./configure --prefix=…` / `make` / `make install`), `chdir=`, `become: true`, `when: daemonize_installed.rc != 0` | `if installed is None:` → one `act(Shell, command="sh -c 'cd … && ./configure && make && make install'", sudo=True)` | recipe (D4) |
| `ignore_errors: "{{ ansible_check_mode }}"` | dropped: `dry=True` parks on the probe, so the build barrier is never reached in a preview | deviation (D6) |

No library gap blocked this migration, so none was filed: `files.unarchive`
already carries the `get_url` + `unarchive` + `creates` semantics (it fetches an
`http(s)` source control-side, spools it, extracts it and answers the gate
in-operation), `server.Which` is the shipped `command -v … || true` read, and the
build is the corpus's standard in-stream `act(Shell)` (the `php_xdebug` precedent).
The default `tasks/main.yml` path is fully mapped; the only opt-in the role ships
is a version bump, which moves the URL and every derived path together.

## 2. Install

```bash
pip install reemote
```

No extra: the role renders no templates and reads no YAML. SSH access to each
Ubuntu host with the key named in its `connection`, and passwordless sudo (or a
`privilege` section in the inventory): every request and the build action carry
`sudo=True`, because reemote has no play-level `become`. Because the tarball is
fetched on the **controller** and spooled to the host, it is the controller that
needs outbound HTTPS to `github.com` — the host itself needs only the apt
archive for the toolchain.

-->

::: reemote.roles.daemonize
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - daemonize

**Variables:**

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `version` | `str` | `1.7.7` — dotted digits; names the tag, the archive and the extracted directory | `daemonize_version` |
| `workspace` | `str` | `/root` — where the source tree is unpacked, the unarchive `dest` | `workspace` |
| `install_path` | `str` | `/usr` — the build `--prefix`, so `make install` writes `<path>/sbin/daemonize` (the upstream Makefile's installdir) | `daemonize_install_path` |

<!---

Precedence is `model defaults < inventory role mapping < overrides`. The model is
frozen and every field is a scalar string; the one sequence the deploy carries is
the module constant `BUILD_PACKAGES = ("build-essential",)`, never a var, so it is
already a hashable `tuple`. The validators are the reason a typo fails before any
host is contacted: `version` must be dotted digits (the paths interpolate it),
`workspace` non-empty (an empty one extracts into `/daemonize-release-…`), and
`install_path` absolute (a relative `--prefix` installs under the build tree).

## 5. Run it

The role is library code; `examples/roles/daemonize.py` is the runnable
artifact — a standalone program with its own `main()`, no shared runner. It
loads and validates `inventory.json` from the current working directory,
resolves the role's variables (the defaults overlaid with the entry's
`daemonize` role mapping, then the call-site `overrides`), builds the deploy from
the `daemonize` factory and drives it with `run_waves` — a `dry=True` one-wave
preview, then the real apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import DaemonizeVars, daemonize

DAEMONIZE_OVERRIDES = {}

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(DaemonizeVars, "daemonize", inventory, overrides=DAEMONIZE_OVERRIDES)
    deploy = daemonize(vars_set)
    report, _hosts = await run_waves(inventory, deploy, dry=True)   # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)             # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/daemonize.py
```

The role's three defaults are the upstream role's own `defaults/main.yml`, so
`DAEMONIZE_OVERRIDES` is empty — it shows where a site would pin a different
`version` or `install_path`. `main()` prints the wave tree and maps the role's
failure modes (bad inventory, `ValueError` from a rejected variable,
`CommandError` — including a build whose `configure`/`make` chain exits non-zero,
which the role raises from the barrier's own `Result` — `HostConnectionError`,
`PrivilegeError`, `OSError`, `TypeError`) to a one-line stderr message and exit 1.

**Run the program twice.** The second run must report no change: the toolchain is
installed (`apt.packages` reads it back), the `creates:` marker extracted on the
first pass skips the fetch entirely, and the probe now finds `daemonize` on
`PATH` — so the build wave is never emitted at all. That is the migration's
idempotence claim, and the offline suite asserts it.

> **Shared demo host.** The `ubuntu` demo host also runs the other role
> examples. This role leaves a compiled `/usr/sbin/daemonize` plus the source tree
> under `/root`, which no other role reads. To undo a real run:
>
> ```bash
> ssh <user>@<ubuntu-host> 'sudo rm -f /usr/sbin/daemonize && sudo rm -rf /root/daemonize-release-*'
> ```

## 6. What it does

One deploy. Each barrier closes its own wave, and a wave with no records is not
appended — so the wave count is **host-dependent**, which is the point of the
barrier model:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `setup`: `apt.packages(("build-essential",))` then `files.unarchive(src=<url>, creates=<src>/INSTALL)` — closed by `fact(server.Which, command="daemonize")` | the fact barrier **is** the role's `register: daemonize_installed` + `when: rc != 0`: it reads the host after the setup wave applied, so a host that built the binary on an earlier pass reads it back and queues nothing |
| 1 | `build`: the `act(Shell, sudo=True)` `&&` chain, **only when the probe found nothing** | a converged host never reaches this wave — the in-stream read means the branch runs on the machine's real state, not a plan frozen before contact |

Two ordering rules are load-bearing, and the layout above is what makes them
hold without any extra machinery:

* **The probe sits after the extraction, not before.** The source tree must be
  on the host before `configure` can run, and the `PATH` read decides whether to
  compile it — so it closes wave 0 (it sees the freshly extracted tree) and the
  build lands in wave 1. A `fact` result cannot be hoisted above the requests it
  depends on.
* **Requests do not close waves.** The toolchain install and the extraction are
  both wave 0, applied per host in body order — the toolchain before the build
  that needs it — with no barrier between them.

A host whose `host.family` is not `"Ubuntu"` returns before its first `yield`, so
it emits no requests, no barriers and no report lines at all.

## 7. Transcript

From `tests/roles/test_daemonize.py`, against the simulated Ubuntu host plus a
RedHat host the role never touches. Nothing is installed yet. Fresh host, first
pass (the two setup requests, the probe reading `None`, then the build):

```text
(root)
  wave 0
    ubuntu: Ensure build tools are installed. [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install build-essential
    ubuntu: Expand daemonize archive. [changed: will extract https://github.com/bmc/daemonize/archive/release-1.7.7.tar.gz into /root]
        $ scp <files unarchive> -> /tmp/reemote-unarchive-e12c2d786bf0d486-release-1.7.7.tar.gz
        $ sudo tar -xz -f /tmp/reemote-unarchive-e12c2d786bf0d486-release-1.7.7.tar.gz -C /root
        $ sudo rm -rf /tmp/reemote-unarchive-e12c2d786bf0d486-release-1.7.7.tar.gz
    barrier: Which → None
  wave 1
    barrier: Build daemonize. → {'exit_status': 0, 'stdout': '', 'stderr': ''}
```

The rerun — the toolchain reads back installed, the `creates:` marker skips the
fetch, the probe now resolves the binary, and **the build wave disappears
entirely** (a converged host ends in wave 0):

```text
(root)
  wave 0
    ubuntu: Ensure build tools are installed. [no change: all packages present]
    ubuntu: Expand daemonize archive. [no change: archive already extracted (/root/daemonize-release-1.7.7/INSTALL exists)]
    barrier: Which → /usr/sbin/daemonize
```

The RedHat host appears in no tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | A host outside the `"Ubuntu"` family emits **nothing at all** | the family guard is the body's opening `if`, not a `when=` on each request. The role ships no `vars/` dispatch; its `meta/main.yml` names Ubuntu, Debian and GenericUNIX, but the `apt` toolchain install (D5) makes the Debian family the only one migrated, so a host outside it is skipped, not mapped onto another package manager |
| D2 | `get_url` + `unarchive` collapse into **one** `files.unarchive` request | the role downloaded the tarball into the workspace then unpacked that remote file (`copy: false`); reemote fetches an `http(s)` `src` **control-side**, spools it, extracts it and removes the spool. Same end state (the tree under the workspace); the workspace no longer holds a leftover `.tar.gz` |
| D3 | `unarchive`'s `mode: 0755` is **not passed** | `files.unarchive` renders a mode as a recursive `chmod -R` of `dest`, and this role's `dest` is the shared workspace (`/root` by default), so passing it would re-mode the operator's home. The role's own `chmod` only mattered to the build, which reads the tree as root anyway (the `php_xdebug` precedent drops the same `mode`) |
| D4 | The build loop becomes **one `act(Shell)`** `&&` chain with an inline `cd` | `Shell` has no `chdir` parameter, and the chain stops on the first failure exactly like Ansible's ordered loop does; the whole chain runs as one root shell. The barrier's `Result` is data, so the body reads the exit status and raises `CommandError` itself |
| D5 | The `build-essential` install is **new** | the role documents the toolchain only as a README prerequisite — an operator who lacked `gcc` got a failing `configure` — so naming it in the deploy is strictly more faithful to the role's stated requirement. It is fact-gated (`apt.packages` reads `AptPackages`), so a host that already has it reports no change |
| D6 | `ignore_errors: "{{ ansible_check_mode }}"` is **dropped** | it exists so an Ansible `--check` run (which applies nothing, so the tree is absent) does not fail on the build; reemote's `dry=True` preview parks on the probe and applies nothing beyond it, so the build barrier is never reached in a preview |
| D7 | The `which` probe becomes a `fact(server.Which)` barrier, not an `act` | it is a read whose only purpose is to branch the body: `command -v daemonize \|\| true` mapped to `str \| None`. The role's `changed_when: false` is free (facts never record a change) and its `failed_when: false` is built in (an unknown command reads as `None`, not an error) |

## 9. Scoped out

* **Non-Ubuntu families.** The role's `meta/main.yml` also claims Debian and
  GenericUNIX, but there is no per-OS task dispatch to migrate — the whole
  `tasks/main.yml` is family-neutral apart from the implicit `apt` toolchain,
  which is Debian-family-specific. A RedHat or Alpine host is skipped, not mapped
  onto `dnf`/`apk` build groups or a different tarball source — those are
  different roles.
* **The Debian family proper.** `build-essential` is installed with `apt`; the
  identical package group on a `Debian` (not `Ubuntu`) host would need a family
  the guard does not currently admit. Extending the target is a role decision, not
  a gap.
* **A `daemonize` config.** The upstream utility is a single-`exec` wrapper with
  no configuration file; the role installs the binary and stops there, and so
  does this migration — no unit, no service, no handler.

-->
