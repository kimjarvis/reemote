# git — Git role

| | |
| --- | --- |
| Role | [`geerlingguy.git`](https://github.com/geerlingguy/ansible-role-git) |
| Module | [`reemote.roles.git`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/git.py) |
| Runner | [`examples/roles/git.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/git.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `git` role mapping → `GitVars` |

<!---
| Waves | `1` — no barrier at all: the whole role is one request wave |
| Outputs | none — the deploy returns `None`; no other role reads git's results |
| Extra deps | **none** — the role templates nothing |
| Offline test | [`tests/roles/test_git.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_git.py) |
| Factory | plain (non-`async`) — the runner calls `git(v)`; there is no barrier, so no factory IO either |

`git` installs Git from the Ubuntu archive: the apt-cache refresh and the
`git_packages` install, and nothing else — the role has no handlers, no
templates and no config files.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with `yield from scoped("git", git(v))(host)`
and a run is one `run_waves` pass. The Ansible role's whole task list is that
single request, and its `defaults/main.yml` is `GitVars`. The role *installs*
git; cloning repositories is the separate [`reemote.modules.git`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/modules/git/git.py)
namespace, reached at the top level as `reemote.git` — the role factory lives
only under `reemote.roles` and shadows nothing (the `postgresql` rule, §17.1).

This is the corpus's **cheapest shape** (design doc §18.12) — one notch below
even `nodejs`, which yields several requests: git yields exactly **one**.
Nothing needs a read of the machine before the request can be built —
`git_packages` is a plain variable, not a version-keyed `include_vars` table
(that dispatch belongs to the scoped-out source branch) — and the role ships no
handlers, no `register`/`when` outcome reads and no command tasks, so there is
no `fact`, no `act`, no `checkpoint` and no `control`:

```python
yield op(apt.packages, packages=hv.packages,   # wave 0 — the whole role
         update=True, cache_valid_time=86400, sudo=True)
```

## 1. What it is, and where it came from

The role's tasks map onto reemote operations:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `apt: update_cache=yes cache_valid_time={{ git_cache_valid_time }}` (Debian) | folded into `apt.packages(update=True, cache_valid_time=86400)` — gated on the `AptUpdateStamp` fact | covered (D1) |
| `apt: name={{ git_packages }} state=present` | `apt.packages(packages, sudo=True)`, the request above | covered |
| `package: name={{ git_packages }} enablerepo=…` (RedHat) | not migrated — the Ubuntu target skips the branch, and `git_enablerepo` is not a field | out of scope (D3) |
| `include_tasks: install-from-source.yml` when `git_install_from_source` | not migrated — setting the knob raises at resolution | out of scope (D2) |

No library gap blocks this migration — the audit filed zero tickets. Every
construct the Ubuntu default path uses (an apt install, a cache refresh with a
validity window) already ships, and the source branch's sub-tasks — dependency
installs, a `git --version` probe, a kernel.org download, an `unarchive`, a
`make` loop — are each covered by `apt`/`server`/`files` operations too; they
are simply outside the agreed scope.

## 2. Install

```bash
pip install reemote           # no extra: no Jinja2, no PyYAML
```

This is the simplest install in the corpus: the role templates and renders
nothing, so no `reemote[templates]` extra. SSH access with the key named in each
`connection`, and passwordless sudo (or a `privilege` section): the one request
carries `sudo=True`.

## 3. Variables

`GitVars` is a frozen pydantic model (`extra="forbid"`) whose fields carry bare
names; the role name passed to `resolve_vars` binds the entry's `git` role
mapping, so an inventory written for the role needs no key gymnastics.
Precedence is `model defaults < inventory role mapping < overrides`.

Validation is fail-fast, before any host is contacted: `packages` accepts a
list or tuple (normalized to a `tuple`, the hashability rule) of non-empty
strings — an empty tuple installs nothing but still refreshes the cache, as an
empty `git_packages` list does upstream; `install_from_source` a bool, and
`true` raises (D2). A key naming no field in the role mapping, such as
`enablerepo` or `version`, raises `RoleVarsError` naming the valid keys.
--->

::: reemote.roles.git
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - git

**Variables:**

### Role variables (`defaults/main.yml`)

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `packages` | `tuple[str, …]` | `("git",)` — the packages to install; `git-svn`-style additions are exactly what the README recommends carrying here | `git_packages` |
| `install_from_source` | `bool` | `False` — and it must stay `False`: setting it raises at resolution (D2) | `git_install_from_source` |

The cache-refresh window is a frozen role constant, not a variable:
`APT_CACHE_VALID_TIME = 86400` is the role's own `cache_valid_time` default,
the only value the Ubuntu path ever passes.

<!---
## 5. Run it

The role is library code; `examples/roles/git.py` is the runnable artifact — a
standalone program with its own `main()`, no shared runner. It loads and
validates `inventory.json` from the current working directory, resolves the
role's variables (the defaults overlaid with the entry's `git` role mapping,
then the call-site `overrides`), builds the deploy from the `git` factory and
drives it with `run_waves` — a `dry=True` preview, then the real apply —
printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import GitVars, git

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(GitVars, "git", inventory,
                            overrides={"packages": ["git", "git-svn"]})
    deploy = git(vars_set)                                            # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)      # whole-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)                # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/git.py
```

The call-site `GIT_OVERRIDES` widen `packages` to `git` plus `git-svn`, the
addition the upstream README suggests, so the demonstration exercises the
package-list variable and not just the default bare install.

**The dry pass has no barrier to park on.** `java`/`docker`/`repo_epel` park on
their first `fact` barrier, so a `dry=True` preview stops there having planned
nothing. This role yields its only request from its first item, so `dry=True`
instead *plans the whole wave* — reading the installed-package and cache-stamp
facts — and applies nothing. The preview is the wave, not a stub.

**Run the program twice.** The second run must report no change: the packages
are installed and the apt cache stamp is fresh, so the folded refresh skips.
That is the migration's idempotence claim, and the offline suite asserts it.

## 6. What it does

One deploy, one wave, zero barriers, one request. The shape the test pins:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `apt.packages(packages, update=True, cache_valid_time=86400)` | the packages are plain variables, so nothing needs a read of the machine before the request is built — and with no handler and no outcome read, a checkpoint would report to nobody (§4.3) |

Three rules are load-bearing:

* **No barrier, so no checkpoint and no early dry park.** The role has no
  handler, no version probe and no `register`/`when` read, so nothing consumes a
  report and nothing forces a wave split. The dry run plans the request and
  applies nothing.
* **Every error path raises at resolution, before any host is contacted.** A bad
  `packages` entry and `install_from_source: true` are both pydantic
  `ValidationError` (a `ValueError`) in `resolve_vars` — the body itself never
  raises, and nothing is ever half-applied.
* **The declared `family` is trusted.** There is no `fact(LinuxDistribution)`
  re-check (D4): the request is a pure function of the variables, so a
  speculative barrier would only add a wave (the `nodejs` precedent; `java`
  reads the distribution because its package table is version-keyed, which this
  role has no equivalent of).

A host whose `family` is not `"Ubuntu"` returns before its first `yield`: no
requests, no barriers, no report lines — Alpine and CentOS entries in a shared
inventory are never contacted.

## 7. Transcript

From `tests/roles/test_git.py`, against the simulated Ubuntu host (`packages:
git, git-svn`) plus a RedHat host the role never touches. No between-pass
bridge is needed — the simulation's own mutators maintain every seam the second
pass reads: `install_apt_packages` records the packages, and the update
arm refreshes `apt_update_stamp`. Fresh host, first pass:

```text
(root)
  wave 0
    ubuntu: Ensure git is installed  [changed: apt update; 2 package(s) to install]
        $ sudo apt-get update
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install git git-svn
```

The rerun — the single request is a no-op, because the packages are installed
and the cache stamp is fresh:

```text
(root)
  wave 0
    ubuntu: Ensure git is installed  [no change: apt update skipped, cache is fresh; all packages present]
```

The `dry=True` pass on a virgin host plans the same request with the same
verdict and shows no command lines:

```text
(root)
  wave 0
    ubuntu: Ensure git is installed  [changed: apt update; 2 package(s) to install]
```

The RedHat host appears in neither tree: no request line, no report record.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | The role's two Debian tasks — `apt: update_cache=true cache_valid_time=86400` and the `apt: name={{ git_packages }}` install — fold into ONE `apt.packages(update=True, cache_valid_time=86400)` | on the default path the refresh exists only to serve the install (the `java` precedent). The refresh is gated on the `AptUpdateStamp` fact against the **controller's** UTC clock, and a stale-cache refresh reports `changed=True`, as Ansible's separate update task would |
| D2 | `git_install_from_source: true` **fails fast at variable resolution** | the build-from-source branch — dependencies, version probe, kernel.org tarball, `make` — is out of the agreed Ubuntu package-install scope; rejecting beats ignoring (the `drush` precedent). Its sub-knobs (`git_version`, `git_install_path`, `workspace`, `git_install_from_source_force_update`, `git_reinstall_from_source`) are not fields either, so `extra="forbid"` rejects them the same way |
| D3 | `git_enablerepo` is **not modelled** | it feeds the RedHat `package` task's `enablerepo` only, and apt has no such option. Declaring it in the `git` role mapping raises `RoleVarsError` naming the real fields — fail fast rather than carry an inert knob |
| D4 | The family guard is the body's opening `if` and the declared inventory `family` is trusted — **no `fact(LinuxDistribution)` barrier** | the request is a pure function of the variables (the `nodejs` precedent); a re-check would add a wave for a read nothing consumes (design doc §18.9). `java` keeps its barrier only because of its version-keyed vars table |
| D5 | Every request carries `sudo=True` explicitly, and variable validation becomes pydantic validation | the role relies on play-level `become`; run-as users/passwords come from the inventory `privilege` section, never from an operation. A bad `packages` entry raises `ValidationError` — a `ValueError`, so the runner maps it — before any host is contacted |

## 9. Scoped out

* **The RedHat branch** (`package` with `git_enablerepo`, the `yum` vars): a
  Phase 0 single-OS decision — the target is Ubuntu. reemote's `dnf` module
  covers the operation; it is simply outside the agreed target.
* **The build-from-source branch** (`tasks/install-from-source.yml`, default
  `git_install_from_source: false`): the opt-in path its own README flags as
  slow — dependency installs, a `git --version` probe, a version-pinned
  kernel.org tarball, `make install`. Every sub-task maps onto shipped
  operations (`apt.packages`, `act(Shell)`, `files.download`, `files.unarchive`,
  `server.shell`), so it is a scope decision, not a gap; setting the knob
  raises (D2).
--->
