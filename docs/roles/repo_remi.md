# repo_remi — Remi repository role

| | |
| --- | --- |
| Role | [`geerlingguy.repo-remi`](https://github.com/geerlingguy/ansible-role-repo-remi) |
| Module | [`reemote.roles.repo_remi`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/repo_remi.py) |
| Runner | [`examples/roles/repo_remi.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/repo_remi.py) |
| Target | **RedHat** — hosts in the `"RedHat"` OS family |
| Variables | the entry's `remi` role mapping → `RemiVars` |

<!---
| Waves | 3–4 (wave 2, the control fetch, only when the repo file is absent) |
| Outputs | none — the deploy returns `None` |
| Extra deps | none (`reemote[templates]` is not needed) |
| Offline test | [`tests/roles/test_repo_remi.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_repo_remi.py) |

`repo_remi` installs Remi's RPM repository on RHEL-family hosts: import the Remi
GPG key, then install the `remi-release` RPM from the vendor URL. Unlike its EPEL
sibling, the role never edits a repo file: Remi ships `[remi]` **disabled** and
is consumed per-transaction with `--enablerepo=remi`, so a converged host emits
no request at all — the guard is the whole idempotence story.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with `yield from scoped("remi", repo_remi(v))(host)`
and a run is one `run_waves` pass. The Ansible role's whole task list — two
tasks — is that single deploy, and its `defaults/main.yml` is `RemiVars`.

## 1. What it is, and where it came from

The role's two tasks map onto reemote operations directly; the columns that
matter are the target and whether the mapping is direct:

| Ansible task | reemote request | Status |
| --- | --- | --- |
| URL defaults interpolating `distribution_major_version` | `yield fact(LinuxDistribution)` + `resolve_remi` | recipe |
| (implicit per-run convergence of `yum state=present`) | `yield fact(FileExists, path=/etc/yum.repos.d/remi.repo)` — the file the RPM ships | barrier |
| `rpm_key: key={{ remi_repo_gpg_key_url }} state=present` | `control(files.fetch_url, …)` + `dnf.key(src=stream, keyid=…)` | covered |
| `yum: name={{ remi_repo_url }} state=present` | `files.download(url, spool)` + `dnf.rpm(src=spool, package="remi-release")` + `files.file(spool, present=False)` | deviation (D3) |

This role is the structural twin of [`repo_epel`](repo_epel.md) and surfaced
**no new library gaps**: the `rpm_key` idempotence it needs is the
[#25](https://github.com/kimjarvis/reemote/issues/25) `dnf.key(keyid=…)` gate,
and the URL staging is the settled stage-then-install shape (EPEL's
`epel-release` run). It needs neither `files.ini_option` (the
[#24](https://github.com/kimjarvis/reemote/issues/24) gap EPEL filed) nor a
checkpoint — there is nothing to toggle and no handler to notify.

## 2. Install

```bash
pip install reemote          # or: uv add reemote
```

Nothing else. No `reemote[templates]` — the role renders no template and reads no
YAML. Its two URL fetches use the standard-library control-side helpers
(`files.fetch_url` for the armored key, `files.download` for the binary RPM), so
the control machine needs outbound HTTPS to `rpms.remirepo.net` and the target
needs no egress at all.

SSH access to each RedHat host with the key named in its `connection`, and
passwordless sudo (or a `privilege` section in the inventory): every request
carries `sudo=True`, because reemote has no play-level `become`.

## 3. Variables

`RemiVars` is a frozen pydantic model whose fields carry bare names; the role
name passed to `resolve_vars` binds the entry's `remi` role mapping.

--->

::: reemote.roles.repo_remi
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - repo_remi

**Variables:**

| Field | Type | Default | Ansible name |
| --- | --- | --- | --- |
| `repo_url` | `str \| None` | `null` → templated | `remi_repo_url` |
| `repo_gpg_key_url` | `str \| None` | `null` → templated | `remi_repo_gpg_key_url` |

<!---

Precedence is `model defaults < inventory role mapping < overrides`. A `null` URL
keeps the version-derived template (`…/remi-release-{major}.rpm`,
`…/enterprise/{major}/RPM-GPG-KEY-remi`), which is how the role interpolates
`distribution_major_version`; an explicit URL wins. Validation is fail-fast: a
URL override must be non-empty, and `extra="forbid"` plus `resolve_vars`'
binding report a key that names no field (`repo_urll` raises `RoleVarsError`
rather than silently defaulting).

## 4. The pinned key ids

The signing keys' rpm short ids are pinned per enterprise major
(`REMI_KEY_IDS`) so the key import can be gated: 7 → `00f97f56`, 8 →
`5f11735a`, 9 → `478f8947`, 10 → `2a0948e4` — verified against the published
keys, and the EL9 entry is also the signature id of the published
`remi-release-9.rpm`. A major outside that table is rejected before any request
runs.

## 5. Run it

The role is library code; `examples/roles/repo_remi.py` is the runnable artifact
— a standalone program with its own `main()`, no shared runner. It loads and
validates `inventory.json` from the current working directory, resolves the
role's variables (the defaults overlaid with the entry's `remi` role mapping),
builds the deploy from the `repo_remi` factory and drives it with `run_waves` —
a `dry=True` one-wave preview, then the real apply — printing the wave tree each
time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import RemiVars, repo_remi

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(RemiVars, "remi", inventory)
    deploy = repo_remi(vars_set)
    report, _hosts = await run_waves(inventory, deploy, dry=True)   # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)             # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/repo_remi.py
```

The `dry=True` pass is a one-wave preview: the role's first item is the
`LinuxDistribution` barrier, so the run parks there (`barrier: LinuxDistribution →
unknown until run`), reads no state, fetches nothing and applies no request — the
converged rerun below is the real preview, and it is cheap.

`main()` prints the wave tree and maps the role's failure modes (bad inventory,
`ValueError` from a rejected family or major, `CommandError`, `HostConnectionError`,
`PrivilegeError`, `OSError`, `TypeError`) to a one-line stderr message and exit 1.

**Run the program twice.** The second run must report no change *and no request*:
the repo file the RPM ships exists, so the install stage yields nothing. That is
the migration's idempotence claim, and the offline suite asserts it.

## 6. What it does

One deploy, three or four waves — one per barrier, then one holding the requests.
Everything before the requests is a barrier the driver runs for all hosts at once:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `fact(LinuxDistribution)` | the major version the URLs interpolate, and the only proof that the declared family matches the machine |
| 1 | `fact(FileExists, path=/etc/yum.repos.d/remi.repo)` | the shipped repo file — the install guard |
| 2 | `control(files.fetch_url, key_url)` | only when the repo file is absent; the URL came from wave 0, so it cannot be hoisted into a factory |
| 3 | `install(...)`: `dnf.key`, `files.download`, `dnf.rpm`, `files.file` (spool cleanup) | the role's two tasks, in one stream |

There is **no configure stage** and **no checkpoint**: the Ansible role never
edits a repo file (Remi ships `[remi]` disabled and is used with
`--enablerepo=remi`), nothing reads an outcome, and a converged host's pass ends
at wave 1 with an empty final wave that the driver does not even append. Only
`[remi]` is off, though: the same RPM ships `[remi-modular]` and `[remi-safe]`
with `enabled=1`, so a successful install does leave those two repositories
enabled while the role itself changes no file contents anywhere. A host whose
`host.family` is not `"RedHat"` returns before its first `yield`, so it
emits no requests and no report lines at all.

## 7. Transcript

From `tests/roles/test_repo_remi.py::test_tree_snapshot`, against the simulated
CentOS Stream 9 host (`web1`) plus an Ubuntu host the role never touches. Fresh
host, first pass:

```text
(root)
  wave 0
    barrier: LinuxDistribution → {'name': 'CentOS Stream', 'family': 'RedHat', 'major': 9, 'minor': None, 'release_meta': {}}
  wave 1
    barrier: FileExists → False
  wave 2
    barrier: fetch_url → -----BEGIN PGP PUBLIC KEY BLOCK-----
    … armored key body …
  wave 3
    web1: Import remi GPG key.     [changed: import GPG key from <stream>]
        $ scp <stream> -> /tmp/reemote-dnf-key-1ded2fb6ffd2b7f2/key.gpg
        $ sudo sh -c 'rpm --import /tmp/reemote-dnf-key-1ded2fb6ffd2b7f2/key.gpg && rm -rf /tmp/reemote-dnf-key-1ded2fb6ffd2b7f2'
    web1: Stage the remi release RPM. [changed: content /tmp/remi-release-9.rpm is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-c552a640c348b09b/content
        $ sudo sh -c 'mkdir -p /tmp && cp /tmp/reemote-install-c552a640c348b09b/content /tmp/remi-release-9.rpm && chmod 644 /tmp/remi-release-9.rpm && rm -rf /tmp/reemote-install-c552a640c348b09b'
    web1: Install the remi release RPM. [changed: rpm /tmp/remi-release-9.rpm not installed, will install]
        $ sudo rpm -i /tmp/remi-release-9.rpm
    web1: Remove the staged remi release RPM. [changed: /tmp/remi-release-9.rpm present, will remove]
        $ sudo rm -f /tmp/remi-release-9.rpm
```

The `fetch_url` line prints the barrier's raw `str` outcome, so a real run
interpolates the whole armored key there; it is elided above and flagged in
[§9](#9-scoped-out).

The rerun — wave 1 answers `True`, so the install stage yields nothing, the
control fetch never happens, and the final wave is empty:

```text
(root)
  wave 0
    barrier: LinuxDistribution → {'name': 'CentOS Stream', 'family': 'RedHat', 'major': 9, 'minor': None, 'release_meta': {}}
  wave 1
    barrier: FileExists → True
```

The Ubuntu host appears in neither tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | A host outside the `"RedHat"` family emits **nothing at all** | the family guard is the body's opening `if`, not a `when=` on each request. The old example logged every request as `[skipped: when guard …]`; a return emits no records |
| D2 | The key and the RPM are read **control-side** (`files.fetch_url` / `files.download`) | `rpm_key` and `yum` download on the target. reemote's URL operations fetch control-side by design: the control machine needs egress, the host needs none |
| D3 | `yum: name=<URL>.rpm` becomes a staged download plus `dnf.rpm(src=spool, package="remi-release")` plus a spool cleanup | `dnf.rpm` installs an `.rpm` already on the host and rejects a URL. The cleanup (`files.file(present=False)`) has no Ansible counterpart — yum leaves no local file, and removing the spool keeps a later pass from installing an aged RPM. The `package=` identity hint reproduces Ansible's installed-database presence test once the staged artifact is gone |
| D4 | The GPG key id is **pinned** per major rather than derived from the key material | `rpm_key` reads the id host-side; reemote's gate takes an explicit `keyid` and the library does no OpenPGP parsing. A custom `repo_gpg_key_url` still imports — its id simply matches nothing |
| D5 | Convergence guards on the **repo file**, not on the installed package | `yum state=present` tests the rpm database every run; reemote's guard is `FileExists` on `/etc/yum.repos.d/remi.repo`, the file the release RPM ships (verified against the published EL9 package). The window the file leaves open — package installed, repo file hand-deleted — still converges through D3's `package=` hint, and the offline suite pins it |

## 9. Scoped out

* **EPEL is not a dependency.** The Ansible README recommends installing EPEL
  first (and molecule plays `geerlingguy.repo-epel` before the role), but the
  role's own `dependencies` are empty — so this one is too. Compose it:
  `yield from scoped("epel", repo_epel(v))(host)` before `repo_remi`'s call.
* **Enabling the repository.** The role installs; it never flips `enabled=`.
  Remi's model is per-transaction `--enablerepo=remi`, and the Ansible role
  mirrors that. Toggling it is a `files.ini_option` request in the caller's own
  program — the EPEL role shows the shape.
* **Removal.** There is no `state=absent`: un-installing `remi-release` is a
  normal `dnf.rpm`/`dnf.packages` request in the caller's own program.
* **Non-RHEL families.** Remi publishes RPMs for the RHEL family only; a
  Debian/SUSE host is skipped rather than mapped to some other vendor
  repository — that is a different role.
* **A multi-line barrier outcome in the tree.** `format_waves` prints a `control`
  barrier's `str` result verbatim, so the armored key body lands inside the tree
  (see [§7](#7-transcript)). The rendering rule for that is an engine concern, not
  a role one.

--->
