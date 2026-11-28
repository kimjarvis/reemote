# git — Git role

| | |
| --- | --- |
| Role | [`geerlingguy.git`](https://github.com/geerlingguy/ansible-role-git) |
| Module | [`reemote.roles.git`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/git.py) |
| Runner | [`examples/roles/git.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/git.py) |
| Target | **Ubuntu, RedHat** — the two families the Ansible role branches on; Alpine has no upstream path |
| Variables | the entry's `git` role mapping → `GitVars` |

<!---
| Waves | `1` — no barrier at all: the whole role is one request wave per host |
| Outputs | none — the deploy returns `None`; no other role reads git's results |
| Extra deps | **none** — the role templates nothing |
| Offline test | [`tests/roles/test_git.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_git.py) |
| Factory | plain (non-`async`) — the runner calls `git(v)`; there is no barrier, so no factory IO either |

`git` installs Git from the OS package archive: the apt-cache refresh and the
`git_packages` install on Ubuntu, the `git_packages` install with its optional
`enablerepo` on RedHat — and nothing else; the role has no handlers, no
templates and no config files.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with `yield from scoped("git", git(v))(host)`
and a run is one `run_waves` pass. The Ansible role's whole task list is that
single request per host, and its `defaults/main.yml` is `GitVars`. The role
*installs* git; cloning repositories is the separate
[`reemote.modules.git`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/modules/git/git.py)
namespace, reached at the top level as `reemote.git` — the role factory lives
only under `reemote.roles` and shadows nothing (the `postgresql` rule, §17.1).

The target families mirror the role: its `tasks/main.yml` branches on
`ansible_facts.os_family` between exactly Debian/Ubuntu and RedHat (its README
installs "on any RHEL/CentOS or Debian/Ubuntu" system, its `meta/main.yml`
declares Fedora, Debian and Ubuntu), and it has no Alpine path at all — no apk
task, no `vars/Alpine.yml`, no Alpine platform in `meta`. So Alpine is out of
scope here too, and an off-target host returns before its first `yield`.

This is the corpus's **cheapest shape** (design doc §18.12) — one notch below
even `nodejs`, which yields several requests: git yields exactly **one request
per host**. Nothing needs a read of the machine before the request can be
built — `git_packages` is a plain variable, not a version-keyed `include_vars`
table (that dispatch belongs to the scoped-out source branch) — and the role
ships no handlers, no `register`/`when` outcome reads and no command tasks, so
there is no `fact`, no `act`, no `checkpoint` and no `control`:

```python
yield op(apt.packages, packages=hv.packages,   # wave 0 on Ubuntu — the whole role
         update=True, cache_valid_time=86400, sudo=True)
yield op(dnf.packages, packages=hv.packages,   # wave 0 on RedHat — likewise
         enable_repos=(hv.enablerepo,) if hv.enablerepo else (), sudo=True)
```

## 1. What it is, and where it came from

The role's tasks map onto reemote operations:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `apt: update_cache=yes cache_valid_time=86400` (Debian) | folded into `apt.packages(update=True, cache_valid_time=86400)` — gated on the `AptUpdateStamp` fact | covered (D1) |
| `apt: name={{ git_packages }} state=present` | `apt.packages(packages, sudo=True)`, the request above | covered |
| `package: name={{ git_packages }} enablerepo={{ git_enablerepo }}` (RedHat) | `dnf.packages(packages, enable_repos=…, sudo=True)` — the additive `enable_repos` spelling of Ansible's `enablerepo=` | covered (D3) |
| `include_tasks: install-from-source.yml` when `git_install_from_source` | not migrated — setting the knob raises at resolution | out of scope (D2) |

No library gap blocks this migration — the audit filed zero tickets. Every
construct both default paths use (an apt install with a cache-refresh validity
window, a dnf install with an additive `enablerepo` scope) already ships, and
the source branch's sub-tasks — dependency installs, a `git --version` probe, a
kernel.org download, an `unarchive`, a `make` loop — are each covered by
`apt`/`dnf`/`server`/`files` operations too; they are simply outside the agreed
scope.

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
`true` raises (D2); `enablerepo` a string, carried only by the RedHat request.
A key naming no field in the role mapping, such as `version` or `workspace`,
raises `RoleVarsError` naming the valid keys.
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
| `enablerepo` | `str` | `""` — the configured-but-disabled repo id the RedHat install enables additively; empty omits the flag, and apt ignores the field (D3) | `git_enablerepo` |

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
are installed and (on Ubuntu) the apt cache stamp is fresh, so the folded
refresh skips. That is the migration's idempotence claim, and the offline suite
asserts it on both families.

## 6. What it does

One deploy, one wave, zero barriers, one request per host. The shape the test
pins:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | Ubuntu: `apt.packages(packages, update=True, cache_valid_time=86400)`; RedHat: `dnf.packages(packages, enable_repos=…)` | the packages are plain variables, so nothing needs a read of the machine before the request is built — and with no handler and no outcome read, a checkpoint would report to nobody (§4.3) |

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
  re-check (D4): each request is a pure function of the variables plus the
  family, so a speculative barrier would only add a wave (the `nodejs`
  precedent; `java` reads the distribution because its package table is
  version-keyed, which this role has no equivalent of).

A host whose `family` is neither `"Ubuntu"` nor `"RedHat"` returns before its
first `yield`: no requests, no barriers, no report lines — the Alpine entries in
a shared inventory are never contacted, because the Ansible role has no Alpine
path either.

## 7. Transcript

From `tests/roles/test_git.py`, against the simulated Ubuntu host (packages:
`git, git-svn`) and the simulated RedHat host (same mapping); the Alpine host
records nothing. No between-pass bridge is needed — the simulation's own
mutators maintain every seam the second pass reads: `install_apt_packages` and
the dnf `install_packages` record the packages, and the `AptUpdateCommand` arm
refreshes `apt_update_stamp`. Fresh hosts, first pass:

```text
(root)
  wave 0
    ubuntu: Ensure git is installed  [changed: apt update; 2 package(s) to install]
        $ sudo apt-get update
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install git git-svn
    web1: Ensure git is installed (RedHat) [changed: 2 package(s) to install]
        $ sudo dnf install -y git git-svn
```

The rerun — each request is a no-op: on Ubuntu the packages are installed and
the cache stamp is fresh, on RedHat the dnf store already carries every
package:

```text
(root)
  wave 0
    ubuntu: Ensure git is installed  [no change: apt update skipped, cache is fresh; all packages present]
    web1: Ensure git is installed (RedHat) [no change: all packages present]
```

The `dry=True` pass on virgin hosts plans the same requests with the same
verdicts and shows no command lines:

```text
(root)
  wave 0
    ubuntu: Ensure git is installed  [changed: apt update; 2 package(s) to install]
    web1: Ensure git is installed (RedHat) [changed: 2 package(s) to install]
```

The Alpine host appears in neither tree: no request line, no report record.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | The role's two Debian tasks — `apt: update_cache=true cache_valid_time=86400` and the `apt: name={{ git_packages }}` install — fold into ONE `apt.packages(update=True, cache_valid_time=86400)` | on the default path the refresh exists only to serve the install (the `java` precedent). The refresh is gated on the `AptUpdateStamp` fact against the **controller's** UTC clock, and a stale-cache refresh reports `changed=True`, as Ansible's separate update task would |
| D2 | `git_install_from_source: true` **fails fast at variable resolution** on either family | the build-from-source branch — dependencies, version probe, kernel.org tarball, `make` — is out of the agreed package-install scope; rejecting beats ignoring (the `drush` precedent). Its sub-knobs (`git_version`, `git_install_path`, `workspace`, `git_install_from_source_force_update`, `git_reinstall_from_source`) are not fields either, so `extra="forbid"` rejects them the same way |
| D3 | `git_enablerepo` is carried as `enablerepo` and is **live only on the RedHat request** | it maps onto `dnf.packages`' additive `enable_repos` spelling (the `apache` precedent) — never `repos=`, which would replace the host's repository set — and an empty value omits the flag exactly like Ansible's `default(omit, true)`. apt has no such option, so the Ubuntu branch ignores the field, faithfully to the role whose Debian task never reads the variable |
| D4 | The family guard is the body's opening `if` over `TARGET_FAMILIES = ("Ubuntu", "RedHat")` and the declared inventory `family` is trusted — **no `fact(LinuxDistribution)` barrier** | each request is a pure function of the variables plus the family (the `nodejs` precedent); a re-check would add a wave for a read nothing consumes (design doc §18.9). The RedHat target covers the role's RedHat *and* Fedora branches — upstream splits them only inside the scoped-out source path (`vars/RedHat.yml` vs `vars/Fedora.yml`) |
| D5 | Every request carries `sudo=True` explicitly, and variable validation becomes pydantic validation | the role relies on play-level `become`; run-as users/passwords come from the inventory `privilege` section, never from an operation. A bad `packages` entry raises `ValidationError` — a `ValueError`, so the runner maps it — before any host is contacted |

## 9. Scoped out

* **The build-from-source branch** (`tasks/install-from-source.yml`, default
  `git_install_from_source: false`): the opt-in path its own README flags as
  slow — dependency installs, a `git --version` probe, a version-pinned
  kernel.org tarball, `make install`. Every sub-task maps onto shipped
  operations (`apt.packages`/`dnf.packages`, `act(Shell)`, `files.download`,
  `files.unarchive`, `server.shell`), so it is a scope decision, not a gap;
  setting the knob raises (D2). Its `include_vars` OS dispatch
  (`vars/Debian.yml`, `vars/RedHat.yml`, `vars/Fedora.yml` — the dependency
  tables) belongs wholly to this branch.
* **The Alpine family**: the Ansible role has no Alpine path — no apk task, no
  `vars/Alpine.yml`, no Alpine platform in `meta/main.yml`, no CI instance —
  so mirroring the role means *not* supporting Alpine; an Alpine host hits the
  family guard's early return.
--->
