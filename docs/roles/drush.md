# drush — Drush role

| | |
| --- | --- |
| Role | [`geerlingguy.drush`](https://github.com/geerlingguy/ansible-role-drush) |
| Module | [`reemote.roles.drush`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/drush.py) |
| Runner | [`examples/roles/drush.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/drush.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | `drush_`-prefixed inventory keys → `DrushVars` |

<!---
| Waves | exactly `3` on every host and every pass (`fact(FileLink)`, `fact(File)`, the requests) |
| Outputs | none — the deploy returns `None` |
| Extra deps | none (the launcher path has no templates — the phar URL's version interpolation is rebuilt with a format template) |
| Offline test | [`tests/roles/test_drush.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_drush.py) |
| Factory | plain (non-`async`) — the runner calls `drush(v)`; the phar's fetch lives inside `files.download`, not as a barrier |

`drush` installs the [Drush launcher](https://www.drush.org/), the Drupal
command-line shell's version-delegating phar, on Ubuntu hosts: the old-symlink
cleanup, the `/usr/local/bin` directory, the launcher phar download and the
`0755` executable bit.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with `yield from scoped("drush", drush(v))(host)`
and a run is one `run_waves` pass. The Ansible role's whole task list is that single
deploy, and its `defaults/main.yml` is `DrushVars`.

This is the smallest role in the corpus and the shape check for the design (design
doc §18.9): **two `fact` barriers then `yield from` — and nothing else.** No
checkpoint, no handler, no `act`, no `control`. The role ships no
`handlers/main.yml`, nothing reads an outcome, and the phar's fetch is *inside* the
`files.download` operation, not a barrier of the body.

## 1. What it is, and where it came from

The role's tasks map onto reemote operations and barriers:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `stat: path={{ drush_launcher_path }}` + `register: drush_path_state` | two barriers: `yield fact(FileLink, path)` then `yield fact(File, path)` | barrier |
| `file: …state=absent` when `drush_path_state.stat.islnk` | `files.file(path, present=False, force=True)` (guarded on the `FileLink` barrier) | covered (D5) |
| `file: path={{ path \| dirname }} state=directory mode=0755` | `files.directory(dirname(path), mode="0755")` | covered |
| `get_url: url={{ drush_launcher_phar_url }} dest=… mode=0755` | `files.download(url, dest, mode="0755")` | deviation (D1/D2/D3/D4) |
| `file: path={{ drush_launcher_path }} mode=0755` (executable) | `files.file(path, mode="0755")` (guarded on the `File` barrier) | covered (D3) |
| `include_tasks` gated on `when: drush_launcher_install` | body-opening `if host.family != "Ubuntu" or not launcher_install: return` | recipe |
| `composer global require` + symlink branch (`drush_composer_global_install`) | **rejected** at resolution time | scoped out |
| `git clone` + composer install branch (`drush_install_from_source`) | **rejected** at resolution time | scoped out |

The migration surfaced **no new library gaps**. The one gap this role originally
found — no binary spool-and-install to a root-owned path — was filed as issue #23
and is closed on main: `files.download` fetches bytes control-side and a non-UTF-8
body installs through `files.install`'s binary path, sha1-idempotent.

## 2. Install

```bash
pip install reemote          # or: uv add reemote
```

No `reemote[templates]` — the launcher path has no templates at all; the role's
only Jinja2 interpolation is the phar URL's version substitution, which the
library rebuilds with a format template. The phar is fetched **control-side**
(`files.download`), so the machine running the program needs outbound HTTPS to
`github.com`; the target downloads nothing.

The target needs PHP to *run* drush — the launcher is a phar that executes `php` —
but this role does not install PHP (the Ansible role does not either; its README
lists PHP as a prerequisite). SSH access to each Ubuntu host with passwordless
sudo (or a `privilege` section in the inventory): every elevated request carries
`sudo=True`, because reemote has no play-level `become`.

## 3. Variables

`DrushVars` is a frozen pydantic model with unprefixed field names; the prefix
passed to `resolve_vars` binds the inventory keys.

--->

::: reemote.roles.drush
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - drush

**Variables:**

| Field | Type | Default | Inventory key | Ansible name |
| --- | --- | --- | --- | --- |
| `launcher_install` | `bool` | `true` | `drush_launcher_install` | `drush_launcher_install` |
| `launcher_version` | `str` | `0.10.1` | `drush_launcher_version` | `drush_launcher_version` |
| `launcher_phar_url` | `str \| None` | `none` | `drush_launcher_phar_url` | `drush_launcher_phar_url` |
| `launcher_path` | `str` | `/usr/local/bin/drush` | `drush_launcher_path` | `drush_launcher_path` |
| `composer_global_install` | `bool` | `false` | `drush_composer_global_install` | `drush_composer_global_install` |
| `install_from_source` | `bool` | `false` | `drush_install_from_source` | `drush_install_from_source` |

<!---

Precedence is `model defaults < inventory extra < overrides`. An explicit
`launcher_phar_url` wins over `launcher_version` (Ansible's variable precedence);
when it is `null` the release URL is rebuilt from the version, exactly as
`defaults/main.yml`'s Jinja2 does. Validation is fail-fast: `launcher_path` must be
absolute and non-empty, `launcher_version` must be a non-empty string, an explicit
`launcher_phar_url` must be non-empty, and `extra="forbid"` plus `resolve_vars`'
binding report a prefixed key that names no field (`drush_launcher_pathh` raises
`RoleVarsError` rather than silently defaulting). The two opt-in branches are
rejected here too, before any host is contacted: `composer_global_install` and
`install_from_source` (D8).

## 5. Run it

The role is library code; `examples/roles/drush.py` is the runnable artifact — a
standalone program with its own `main()`, no shared runner. It loads and validates
`inventory.json` from the current working directory, resolves the role's variables,
builds the deploy from the `drush` factory and drives it with `run_waves` — a
`dry=True` one-wave preview, then the real apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import DrushVars, drush

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(DrushVars, "drush_", inventory)
    deploy = drush(vars_set)                                       # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)   # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)             # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/drush.py
```

The `dry=True` pass is a one-wave preview: the role's first item is the `FileLink`
fact barrier on the launcher path, so the run parks there (`barrier: FileLink →
unknown until run`), reads no state, fetches nothing and applies no request — unlike
the old two-phase script, whose prepare pass fetched the phar control-side to plan
the sha1 comparison, the wave driver's dry pass never reaches a request.

**Run the program twice.** The second run must report no change: the directory
matches, the phar's sha1 matches (`content already up to date`), the mode matches.
The phar is still fetched on every *real* pass, because the sha1 comparison must
read the body — so the no-change claim is about changed entries, not fetch counts.
That is the migration's idempotence claim, and the offline suite asserts it.

## 6. What it does

One deploy, exactly three waves on every host and every pass — the wave count is
fixed, so a fourth would mean a barrier had been added speculatively:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `fact(FileLink, path=launcher_path)` | the role's `stat` register — answers the `islnk` gate (the symlink target, or `None`) |
| 1 | `fact(File, path=launcher_path)` | whether a regular file already sits at the path (its metadata is what the executable-bit task converges) |
| 2 | `files.file(present=False)` (when a symlink) + `files.directory` + `files.download` + `files.file(mode=…)` (when a file existed) requests | the whole launcher task list, in role order |

There is **no checkpoint and no handler**: the role ships no `handlers/main.yml`
and nothing reads an outcome. A host whose `host.family` is not `"Ubuntu"` — or one
with `launcher_install` false — returns before its first `yield`, so it emits no
requests, no barriers and no report lines at all.

## 7. Transcript

From `tests/roles/test_drush.py`, against the simulated Ubuntu host (`ubuntu`)
plus a CentOS host the role never touches. Fresh host, first pass (the destination
is absent, so the executable-bit task is not even requested):

```text
(root)
  wave 0
    barrier: FileLink → None
  wave 1
    barrier: File → None
  wave 2
    ubuntu: Ensure Drush path directory exists. [no change: directory /usr/local/bin already exists]
    ubuntu: Install Drush.           [changed: content /usr/local/bin/drush is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-…/content
        $ sudo sh -c 'mkdir -p /usr/local/bin && cp /tmp/reemote-install-…/content /usr/local/bin/drush && chmod 755 /usr/local/bin/drush && rm -rf /tmp/reemote-install-…'
```

The rerun — the `File` barrier now answers a metadata row, so the executable-bit
task *is* requested but is a no-op, and the download's sha1 read-back finds the
content already in place. No line reports a change:

```text
(root)
  wave 0
    barrier: FileLink → None
  wave 1
    barrier: File → {'user': 'root', 'group': 'root', 'mode': 755, 'size': 53}
  wave 2
    ubuntu: Ensure Drush path directory exists. [no change: directory /usr/local/bin already exists]
    ubuntu: Install Drush.           [no change: content already up to date (/usr/local/bin/drush)]
    ubuntu: Ensure Drush is executable. [no change: file /usr/local/bin/drush already exists]
```

The CentOS host appears in neither tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | The phar is fetched **control-side** (`files.download`) and spooled to the host | `get_url` downloads on the target; reemote's URL operations fetch control-side by design, so the control machine needs egress to `github.com` and the host downloads nothing |
| D2 | `files.download` is **sha1-idempotent**, not exists-skip | `get_url` with the default `force: no` skips an *existing* destination regardless of content; here equal bytes no-op and a drifted binary converges to the release content once |
| D3 | The `0755` mode is applied **at install time** (`files.download(mode=…)`), and the role's final `Ensure Drush is executable.` task is kept as a `files.file(mode=…)` convergence request | install/download idempotence is content-only and never compares mode, so the metadata task is what converges a pre-existing destination; a binary install's sha1 read-back is unprivileged, so the launcher stays login-readable |
| D4 | **No checksum pin** | the role sets none, and GitHub release assets are immutable; the request sequence is deterministic without a pin |
| D5 | The symlink cleanup uses `force=True` | the `File` fact reads a symlink as a non-file, so `files.file(present=False)` would raise without it |
| D6 | The old script's one optimistic edge is **gone** on the wave path | a symlink whose target already held the exact phar bytes planned a no-op in prepare but installed in apply; `run_waves` materializes once, after the cleanup applied, so the download's read-back sees the real post-cleanup state |
| D7 | Every elevated request carries `sudo=True` explicitly | the role relies on play-level `become`; run-as users/passwords come from the inventory `privilege` section, never from an operation |
| D8 | The two opt-in branches are **rejected** at variable-resolution time | `drush_composer_global_install` (a global `composer require` + symlink) and `drush_install_from_source` (`git clone` + composer install) are default-false and out of scope; a bad setting fails before any host is contacted |

## 9. Scoped out

* **`drush_composer_global_install`** (the composer-global branch). Opt-in and
  default-false; rejected at resolution time (D8).
* **`drush_install_from_source`** (the build-from-source branch). Opt-in and
  default-false; rejected at resolution time (D8).
* **Non-Ubuntu families.** The role is generic-Linux upstream, but this migration
  fixes one target; a non-Ubuntu host is skipped silently rather than mapped.
* **Installing PHP.** The launcher is a phar that runs `php`, but the Ansible role
  lists PHP only as a prerequisite and never installs it; neither does this role.

--->
