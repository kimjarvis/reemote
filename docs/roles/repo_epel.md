# repo_epel — EPEL repository role

| | |
| --- | --- |
| Role | [`geerlingguy.repo-epel`](https://github.com/geerlingguy/ansible-role-repo-epel) |
| Module | [`reemote.roles.repo_epel`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/repo_epel.py) |
| Runner | [`examples/roles/repo_epel.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/repo_epel.py) |
| Target | **RedHat** — hosts in the `"RedHat"` OS family |
| Variables | `epel_`-prefixed inventory keys → `EpelVars` |

<!---
| Waves | 3–4 (wave 2, the control fetch, only when the repo file is absent) |
| Outputs | none — the deploy returns `None` |
| Extra deps | none (`reemote[templates]` is not needed) |
| Offline test | [`tests/roles/test_repo_epel.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_repo_epel.py) |

`repo_epel` installs the EPEL (Extra Packages for Enterprise Linux) repository on
RHEL-family hosts: import the EPEL GPG key, install the `epel-release` RPM, then
set `enabled=` in the `[epel]` section of `/etc/yum.repos.d/epel.repo` — leaving
the RPM's other sections, comments and options byte-identical.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with `yield from scoped("epel", repo_epel(v))(host)`
and a run is one `run_waves` pass. The Ansible role's whole task list — four
tasks, two `when`-guarded — is that single deploy, and its `defaults/main.yml` is
`EpelVars`.

## 1. What it is, and where it came from

The role's four tasks map onto reemote operations one-to-one; the two columns
that matter are the target and whether the mapping is direct:

| Ansible task | reemote request | Status |
| --- | --- | --- |
| `stat: path={{ epel_repofile_path }}` + `register` | `yield fact(FileExists, path=…)` | barrier |
| `rpm_key: key={{ epel_repo_gpg_key_url }} state=present` | `dnf.key(src=stream, keyid=…)` | covered |
| `yum: name={{ epel_repo_url }} state=present` | `files.download(url, spool)` + `dnf.rpm(src=spool)` + `files.file(spool, present=False)` | deviation (D3) |
| `ini_file: section=epel option=enabled no_extra_spaces=true mode=0644` | `files.ini_option(…)` + `files.file(mode="0644")` | covered |
| `until` / `retries: 5` / `delay: 10` | dropped — fail-fast | deviation (D4) |
| URL defaults interpolating `distribution_major_version` | `yield fact(LinuxDistribution)` + `resolve_epel` | recipe |

The migration surfaced two genuine library gaps, both fixed before this role
landed, so no workaround code remains:

| Issue | Gap | Fix |
| --- | --- | --- |
| [#24](https://github.com/kimjarvis/reemote/issues/24) | no section-scoped INI option edit: `dnf.repo` writes a whole single-section file (it would clobber `[epel-debuginfo]`/`[epel-source]`), `files.edit` is line-scoped | `files.ini_option` — pure section-scoped edit over `FileContents`, spool-and-install, content-idempotent |
| [#25](https://github.com/kimjarvis/reemote/issues/25) | `dnf.key(present=True)` was stateless (always changed), where Ansible's `rpm_key state=present` is idempotent | `dnf.key(keyid=…)` gates the import on the `RpmGpgKeys` fact |

## 2. Install

```bash
pip install reemote          # or: uv add reemote
```

Nothing else. No `reemote[templates]` — the role renders no template and reads no
YAML. Its two URL fetches use the standard-library control-side helpers
(`files.fetch_url` for the armored key, `files.download` for the binary RPM), so
the control machine needs outbound HTTPS to `dl.fedoraproject.org` and the target
needs no egress at all.

SSH access to each RedHat host with the key named in its `connection`, and
passwordless sudo (or a `privilege` section in the inventory): every request
carries `sudo=True`, because reemote has no play-level `become`.

## 3. Variables

`EpelVars` is a frozen pydantic model with unprefixed field names; the prefix
passed to `resolve_vars` binds the inventory keys.

--->

::: reemote.roles.repo_epel
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - repo_epel

**Variables:**

| Field | Type | Default | Inventory key | Ansible name |
| --- | --- | --- | --- | --- |
| `repofile_path` | `str` | `/etc/yum.repos.d/epel.repo` | `epel_repofile_path` | `epel_repofile_path` |
| `repo_disable` | `bool` | `false` | `epel_repo_disable` | `epel_repo_disable` |
| `repo_url` | `str \| None` | `null` → templated | `epel_repo_url` | `epel_repo_url` |
| `repo_gpg_key_url` | `str \| None` | `null` → templated | `epel_repo_gpg_key_url` | `epel_repo_gpg_key_url` |

<!---

Precedence is `model defaults < inventory extra < overrides`. A `null` URL keeps
the version-derived template (`…/epel-release-latest-{major}.noarch.rpm`,
`…/RPM-GPG-KEY-EPEL-{major}`), which is how the role interpolates
`distribution_major_version`; an explicit URL wins. Validation is fail-fast:
`repofile_path` must be absolute, a URL override must be non-empty, and
`extra="forbid"` plus `resolve_vars`' binding report a prefixed key that names no
field (`epel_repo_disble` raises `RoleVarsError` rather than silently defaulting).

The signing keys' rpm short ids are pinned per EPEL major (`EPEL_KEY_IDS`) so the
key import can be gated: 7 → `352c64e5`, 8 → `2f86d6a1`, 9 → `3228467c`,
10 → `e37ed158`. A major outside that table is rejected before any request runs.

## 5. Run it

The role is library code; `examples/roles/repo_epel.py` is the runnable artifact —
a standalone program with its own `main()`, no shared runner. It loads and validates
`inventory.json` from the current working directory, resolves the role's variables
(the defaults overlaid with the entry's `epel_`-prefixed keys), builds the deploy
from the `repo_epel` factory and drives it with `run_waves` — a `dry=True` one-wave
preview, then the real apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import EpelVars, repo_epel

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(EpelVars, "epel_", inventory)
    deploy = repo_epel(vars_set)
    report, _hosts = await run_waves(inventory, deploy, dry=True)   # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)             # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/repo_epel.py
```

The `dry=True` pass is a one-wave preview: the role's first item is the
`LinuxDistribution` barrier, so the run parks there (`barrier: LinuxDistribution →
unknown until run`), reads no state, fetches nothing and applies no request — the
converged rerun below is the real preview, and it is cheap.

`main()` prints the wave tree and maps the role's failure modes (bad inventory,
`ValueError` from a rejected family or major, `CommandError`, `HostConnectionError`,
`PrivilegeError`, `OSError`, `TypeError`) to a one-line stderr message and exit 1.

**Run the program twice.** The second run must report no change: the repo file exists (so
the install stage yields nothing), the key id is in the rpm keyring, the `[epel]`
enabled line matches and the mode matches. That is the migration's idempotence
claim, and the offline suite asserts it.

## 6. What it does

One deploy, three or four waves — one per barrier, then one holding the requests.
Everything before the requests is a barrier the driver runs for all hosts at once:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `fact(LinuxDistribution)` | the major version the URLs interpolate, and the only proof that the declared family matches the machine |
| 1 | `fact(FileExists, path=repofile)` | the role's `stat` register — the install guard |
| 2 | `control(files.fetch_url, key_url)` | only when the repo file is absent; the URL came from wave 0, so it cannot be hoisted into a factory |
| 3 | `install(...)` then `configure(...)`: `dnf.key`, `files.download`, `dnf.rpm`, `files.file` (spool cleanup), `files.ini_option`, `files.file` (mode) | the role's four tasks, in one stream |

There is **no barrier between install and configure**. The driver materializes and
applies one request per host before pulling the next, so `epel-release` is
installed — and ships `/etc/yum.repos.d/epel.repo` — before `configure`'s read of
that file. That is what collapses the old example's two deploys into one pass.

There is **no checkpoint**: nothing in the role reads an outcome, and a
barrier-only wave would report nothing. A host whose `host.family` is not
`"RedHat"` returns before its first `yield`, so it emits no requests and no
report lines at all.

## 7. Transcript

From `tests/roles/test_repo_epel.py::test_tree_snapshot`, against the simulated
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
    web1: Import EPEL GPG key.     [changed: import GPG key from <stream>]
        $ scp <stream> -> /tmp/reemote-dnf-key-1ded2fb6ffd2b7f2/key.gpg
        $ sudo sh -c 'rpm --import /tmp/reemote-dnf-key-1ded2fb6ffd2b7f2/key.gpg && rm -rf /tmp/reemote-dnf-key-1ded2fb6ffd2b7f2'
    web1: Stage the EPEL release RPM. [changed: content /tmp/epel-release-latest-9.noarch.rpm is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-8092ad521b8deba3/content
        $ sudo sh -c 'mkdir -p /tmp && cp /tmp/reemote-install-8092ad521b8deba3/content /tmp/epel-release-latest-9.noarch.rpm && chmod 644 /tmp/epel-release-latest-9.noarch.rpm && rm -rf /tmp/reemote-install-8092ad521b8deba3'
    web1: Install the EPEL release RPM. [changed: rpm /tmp/epel-release-latest-9.noarch.rpm not installed, will install]
        $ sudo rpm -i /tmp/epel-release-latest-9.noarch.rpm
    web1: Remove the staged EPEL release RPM. [changed: /tmp/epel-release-latest-9.noarch.rpm present, will remove]
        $ sudo rm -f /tmp/epel-release-latest-9.noarch.rpm
    web1: Ensure EPEL repo is enabled / disabled. [no change: content already up to date (/etc/yum.repos.d/epel.repo)]
    web1: Ensure EPEL repo file mode is 0644. [no change: file /etc/yum.repos.d/epel.repo already exists]
```

The `fetch_url` line prints the barrier's raw `str` outcome, so a real run
interpolates the whole armored key there; it is elided above and flagged in
[§9](#9-scoped-out).

The rerun — wave 1 answers `True`, so the install stage yields nothing, the
control fetch never happens, and no line reports a change:

```text
(root)
  wave 0
    barrier: LinuxDistribution → {'name': 'CentOS Stream', 'family': 'RedHat', 'major': 9, 'minor': None, 'release_meta': {}}
  wave 1
    barrier: FileExists → True
  wave 2
    web1: Ensure EPEL repo is enabled / disabled. [no change: content already up to date (/etc/yum.repos.d/epel.repo)]
    web1: Ensure EPEL repo file mode is 0644. [no change: file /etc/yum.repos.d/epel.repo already exists]
```

The Ubuntu host appears in neither tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | A host outside the `"RedHat"` family emits **nothing at all** | the family guard is the body's opening `if`, not a `when=` on each request. The old example logged every request as `[skipped: when guard …]`; a return emits no records |
| D2 | The key and the RPM are read **control-side** (`files.fetch_url` / `files.download`) | `rpm_key` and `yum` download on the target. reemote's URL operations fetch control-side by design: the control machine needs egress, the host needs none |
| D3 | `yum: name=<URL>.rpm` becomes a staged download plus `dnf.rpm(src=spool)` plus a spool cleanup | `dnf.rpm` installs an `.rpm` already on the host and rejects a URL. The cleanup (`files.file(present=False)`) has no Ansible counterpart — yum leaves no local file, and removing the spool keeps a later pass from installing an aged RPM |
| D4 | `until` / `retries` / `delay` are dropped | reemote is fail-fast: a transient fetch aborts the run and the operator re-runs it |
| D5 | The GPG key id is **pinned** per major rather than derived from the key material | `rpm_key` reads the id host-side; reemote's gate takes an explicit `keyid` and the library does no OpenPGP parsing. A custom `repo_gpg_key_url` still imports — its id simply matches nothing |
| D6 | `ini_option` idempotence is **content-only**; the companion `files.file(mode="0644")` converges mode drift | the install family's contract; the role's `ini_file: mode=0644` re-applied the mode on every run, so the pair reproduces it |
| D7 | `dnf.rpm` is version-aware: a staged RPM whose version differs from the installed one would re-run `rpm -i` (and fail), where `yum state=present` no-ops | contained by the role's own guard — the install stage only runs when the repo file is absent, and an installed `epel-release` ships that file |

## 9. Scoped out

* **One EPEL repo per host.** The role manages `[epel]` in one file; additional
  sections, other `.repo` files and EPEL next/Testing repos are out of scope.
* **Removal.** There is no `state=absent`: the role installs and configures, and
  un-installing `epel-release` is a normal `dnf.rpm`/`dnf.packages` request in the
  caller's own program.
* **Non-RHEL families.** A Debian/SUSE host is skipped rather than mapped to its
  own backports repository — that is a different role.
* **Retrying a failed fetch.** See D4.
* **A multi-line barrier outcome in the tree.** `format_waves` prints a `control`
  barrier's `str` result verbatim, so the armored key body lands inside the tree
  (see [§7](#7-transcript)). The rendering rule for that is an engine concern, not
  a role one.

--->
