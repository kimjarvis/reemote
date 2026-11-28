# ruby — Ruby runtime and gems role

| | |
| --- | --- |
| Role | [`geerlingguy.ruby`](https://github.com/geerlingguy/ansible-role-ruby) |
| Module | [`reemote.roles.ruby`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/ruby.py) |
| Runner | [`examples/roles/ruby.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/ruby.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `ruby` role mapping → `RubyVars` |

<!---
| Waves | `0`–`1` — one `FileExists` barrier: wave 0 provisions, wave 1 runs the gem loop |
| Outputs | none — the deploy returns `None`; no other role reads ruby's results |
| Extra deps | **none** — the role ships no template and reads no YAML |
| Offline test | [`tests/roles/test_ruby.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_ruby.py) |
| Factory | plain (non-`async`) — the runner calls `ruby(v)`; the one barrier lives inside the deploy body, so the factory reads no machine state and there is no factory IO |

`ruby` installs Ruby on Ubuntu hosts and then ensures a list of gems: the
`ruby_packages` apt install with its gated cache refresh, the rubygems package,
the `/etc/profile.d` script that puts the user gem `bin` directory on `$PATH`,
Bundler, and the `ruby_install_gems` loop — version pins, scopes and bindirs
alike. The role has no handlers and no command/shell tasks; its one read of
the machine is the `gem`-binary probe that gates the loop, so the stream is
the `pip` shape: a `fact` barrier between the provisioning wave and the gems.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with `yield from scoped("ruby", ruby(v))(host)`
and a run is one `run_waves` pass. The Ansible role's whole task list is that
single deploy, and its `defaults/main.yml` is `RubyVars`.

```python
yield from install(hv)(host)  # wave 0 — apt ruby, rubygems, profile script
# barrier: FileExists("/usr/bin/gem") — then wave 1 — Bundler, gems
```

## 1. What it is, and where it came from

The role's tasks map onto reemote operations plus one barrier:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `include_vars: {{ os_family }}.yml` + `set_fact: ruby_packages` | frozen `RUBY_PACKAGES` constant, reachable through the `packages` variable (D2); the family dispatch is the body's opening guard | recipe (D2) |
| `apt: update_cache=true cache_valid_time=86400` (Debian) | folded into `apt.packages(update=True, cache_valid_time=86400)` — gated on the `AptUpdateStamp` fact | covered (D1) |
| `apt: name={{ ruby_packages }}` + `apt: name={{ ruby_rubygems_package_name }}` | two `apt.packages` requests, the second for `rubygems_package` (defaulted to `ruby-rubygems`, D3/D4) | covered (D3) |
| `set_fact: ruby_rubygems_package_name=rubygems-integration` (Ubuntu trusty) | dead branch — 14.04 is EOL; the override channel survives as the field | scoped out (D4) |
| `copy: src=rubygems.sh dest=/etc/profile.d/rubygems.sh mode=0644` | `files.install(content=RUBYGEMS_PROFILE_SH, …)` — the file's bytes ship as a constant | recipe (D5) |
| `gem: name=bundler user_install=false` (when `ruby_install_bundler`) | `gem.packages(("bundler",), user_install=False, sudo=True)`, gated by the `install_bundler` field | covered |
| `gem: …` looped over `ruby_install_gems` with `become_user` | **one `gem.packages` request per item**, scope and privilege per `user_install` (D6/D7), behind the `FileExists`-for-`gem` barrier (D9) | recipe (D6/D7/D9) |
| `include_tasks: install-from-source.yml` (default-false) | `install_from_source=True` raises at variable resolution; the sub-knobs are not fields (D8) | scoped out (D8) |
| `include_tasks: setup-RedHat.yml` | off-target family — the guard returns before the first `yield` | scoped out |

No library gap blocks this migration: the one gap the audit found — no `gem`
module — shipped as issue #57. Zero new tickets.

## 2. Install

```bash
pip install reemote           # no extra: no Jinja2, no PyYAML
```

This role needs **no** `reemote[templates]` extra: it ships no template and
reads no YAML (the `php`/`pip` precedent). SSH access with the key named in
each `connection`, and passwordless sudo (or a `privilege` section) for the
root-owned requests: the apt installs, the profile script and every
system-scope gem carry `sudo=True`; a user-scope gem install runs unprivileged
as the login user.
--->

::: reemote.roles.ruby
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - ruby

**Variables:**

### Role variables (`defaults/main.yml`)

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `packages` | `tuple[str, …]` | `ruby-full`, `ruby-dev`, `build-essential`, `autogen`, `autoconf`, `libtool` — `vars/Debian.yml`'s `__ruby_packages` (D2) | `ruby_packages` |
| `rubygems_package` | `str` | `"ruby-rubygems"` — re-defaulted from the role's `rubygems`, which has no candidate on a current Ubuntu (D3) | `ruby_rubygems_package_name` |
| `install_bundler` | `bool` | `true` | `ruby_install_bundler` |
| `install_gems` | `tuple[GemSpec, …]` | `()` — each a bare name or a `{name, version, user_install, bindir}` mapping | `ruby_install_gems` |
| `install_from_source` | `bool` | `false` — `true` raises before any host is contacted (D8) | `ruby_install_from_source` |

### `ruby_install_gems` entries (`GemSpec`)

| Field | Type | Default | Ansible equivalent |
| --- | --- | --- | --- |
| `name` | `str` | required, non-empty | `item.name \| default(item)` |
| `version` | `str \| None` | `null` — rendered in reemote's `<pkg>=<version>` pin spelling, translated to gem's `<pkg>:<version>` token (D6) | `item.version \| default(omit)` |
| `user_install` | `bool` | `true` — the Ansible `gem` module's own default; `false` installs system-wide with escalation (D7) | `item.user_install \| default(omit)` |
| `bindir` | `str \| None` | `null` — where gem places the gems' executables | `item.bindir \| default(omit)` |

<!---
## 5. Run it

The role is library code; `examples/roles/ruby.py` is the runnable artifact —
a standalone program with its own `main()`, no shared runner. It loads and
validates `inventory.json` from the current working directory, resolves the
role's variables (the defaults overlaid with the entry's `ruby` role mapping,
then the call-site `overrides`), builds the deploy from the `ruby` factory and
drives it with `run_waves` — a `dry=True` preview, then the real apply —
printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import RubyVars, ruby

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(
        RubyVars, "ruby", inventory,
        overrides={"install_gems": [
            {"name": "rake", "version": "13.2.1", "user_install": False},
            "colorize",
        ]},
    )
    deploy = ruby(vars_set)                                              # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)         # parks at the barrier
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)                   # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/ruby.py
```

The call-site `RUBY_OVERRIDES` turn on the `install_gems` loop with a
version-pinned system gem and a bare user-scope gem, so the demonstration
exercises the whole stream — the apt installs, the profile script, Bundler and
the loop — and not just the interpreter install.

**The dry preview parks at the barrier.** `dry=True` plans wave 0 — with the
real change verdicts, because planning reads facts — and then stops at
`barrier: FileExists → unknown until run`, reading nothing and applying
nothing (the `pip` precedent).

**Run the program twice.** The second run must report no change: the apt
packages are installed, the cache stamp is fresh, the profile script already
holds its content, and every gem — in its chosen scope — is already present at
its requested version. That is the migration's idempotence claim, and the
offline suite asserts it.

## 6. What it does

One deploy, two waves at most. The `FileExists` barrier closes wave 0, and the
gem loop only enters wave 1 when the answer is yes, so the shape below is what
the test pins:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `install`: `apt.packages(packages, update=True, cache_valid_time=86400)`, `apt.packages((rubygems_package,))`, `files.install(RUBYGEMS_PROFILE_SH, …)` | every value is frozen from the resolved variables — the role's other mid-play reads were OS facts the Ubuntu path does not need |
| — | `fact(FileExists, "/usr/bin/gem")` | gates the gem loop (D9): each `gem.packages` request reads the `GemPackages` fact *while being materialized*, and a `dry` pass materializes the whole wave before anything is applied, so on a host that starts without `gem` the loop's `gem list --local` would exit 127 |
| 1 | the optional `gem.packages(("bundler",), user_install=False)`, and per loop item one `gem.packages` request | only emitted when the barrier answers yes; with no handler and no outcome read a checkpoint would report to nobody (§4.3) |

Three rules are load-bearing:

* **The barrier, not task order, keeps `gem` provable.** Ansible sequenced the
tasks and trusted the binary to exist by the time the loop ran (D9). The
barrier's `no` answer defers the loop to the next invocation — on a live host
wave 0's own apt install has usually landed it by the time the barrier reads;
a `rubygems_package` override that ships no binary simply leaves the loop
unrun rather than crashing the run.
* **One request per loop item (D6).** The per-item knobs (`version`,
  `user_install`, `bindir`) are call-level scalars on `gem.packages`, so
  batching across different scopes would be wrong; one request per item mirrors
  the role's one-task-per-loop-iteration exactly and keeps each item's log line
  distinct.
* **Scope decides privilege (D7).** A `user_install` item runs unprivileged as
  the inventory's login user — the account the role's `become_user` defaulted
  to — and lands in that user's own gem directory; a system-scope item carries
  `sudo=True` because the system store is root-owned.

A host whose `family` is not `"Ubuntu"` returns before its first `yield`: no
requests, no barriers, no report lines — the Alpine and CentOS entries in a
shared inventory are never contacted.

## 7. Transcript

From `tests/roles/test_ruby.py`, against the simulated Ubuntu host (nothing
installed, cache stale, no `gem` binary yet) plus a RedHat host the role never
touches. Fresh host, provisioning pass — wave 0 lands, the barrier reads False
because the simulation's apt mutators create no payload files, and the loop
defers (D9):

```text
(root)
  wave 0
    web1: Install ruby and other required dependencies. [changed: apt update; 6 package(s) to install]
        $ sudo apt-get update
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install ruby-full ruby-dev build-essential autogen autoconf libtool
    web1: Install rubygems.        [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install ruby-rubygems
    web1: Add user installed RubyGems bin directory to global $PATH. [changed: content /etc/profile.d/rubygems.sh is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-df1e80eae4cae341/content
        $ sudo sh -c 'mkdir -p /etc/profile.d && cp /tmp/reemote-install-df1e80eae4cae341/content /etc/profile.d/rubygems.sh && chmod 644 /etc/profile.d/rubygems.sh && rm -rf /tmp/reemote-install-df1e80eae4cae341'
    barrier: FileExists → False
```

Once `/usr/bin/gem` exists — on a live host, wave 0's own `ruby-rubygems`
install has landed it by the time the barrier reads — the loop follows in
wave 1:

```text
(root)
  wave 0
    web1: Install ruby and other required dependencies. [no change: apt update skipped, cache is fresh; all packages present]
    web1: Install rubygems.        [no change: all packages present]
    web1: Add user installed RubyGems bin directory to global $PATH. [no change: content already up to date (/etc/profile.d/rubygems.sh)]
    barrier: FileExists → True
  wave 1
    web1: Install Bundler.         [changed: 1 package(s) to install]
        $ sudo gem install --no-user-install bundler
    web1: Ensure gem rake is installed. [changed: 1 package(s) to install]
        $ sudo gem install --no-user-install rake:13.2.1
    web1: Ensure gem colorize is installed. [changed: 1 package(s) to install]
        $ gem install --user-install colorize
```

The `colorize` line runs `$ gem install --user-install colorize` with no
escalation — the user-scope item's own privilege choice (D7) — while its `rake`
neighbour names gem's `rake:13.2.1` token, translated from the request's
`rake=13.2.1` pin (D6). A further rerun reports no change on either wave, and
the RedHat host appears in neither tree: no record at all.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | The `apt: update_cache` task (with `cache_valid_time: 86400`) folds into the first `apt.packages` as `update=True, cache_valid_time=86400` | the `git` precedent: the refresh exists only to serve the install, and the `AptUpdateStamp` gate reproduces Ansible's `cache_valid_time` semantics |
| D2 | `include_vars` + the `Define ruby_packages` `set_fact` collapse to frozen Ubuntu constants | a Phase 0 single-OS decision; the `ruby_packages` override channel survives as the `packages` field |
| D3 | The default `rubygems_package` is `ruby-rubygems`, not the role's `rubygems` | on a current Ubuntu (verified on 24.04) the `rubygems` name has **no installation candidate**, while `ruby-rubygems` is the deb that ships `/usr/bin/gem`; the field keeps the override for releases that still carry it |
| D4 | The Ubuntu-trusty `set_fact` (`rubygems-integration`) has no counterpart | 14.04 is long EOL and the default path on any supported release is what the field already defaults to |
| D5 | `copy: src=rubygems.sh` becomes `files.install` with the content frozen as a constant | reemote roles ship no `files/` tree; idempotence is content-only, exactly what the role's fixed content needs (a mode-only drift is `files.file`'s job, which the role never needed either) |
| D6 | The `gem` loop runs as **one `gem.packages` request per item**, pins spelled `<pkg>=<version>` | the per-item knobs (`version`, `user_install`, `bindir`) are call-level scalars; `gem.packages` translates reemote's shared pin spelling to gem's own `<pkg>:<version>` token |
| D7 | `become: true` + `become_user: ruby_install_gems_user` becomes scope plus privilege, and `ruby_install_gems_user` is not a variable | a `user_install` item runs unprivileged as the inventory's login user — the account the role's `become_user` defaulted to — and a system item carries `sudo=True`; reemote takes run-as users from the inventory, never from an operation |
| D8 | `ruby_install_from_source` is scoped out: the boolean fails fast, the sub-knobs are not fields | a Phase 0 default-path decision — setting `install_from_source` raises before any host is contacted, and a mapping with `ruby_version`/`workspace` gets a `RoleVarsError` naming the valid keys |
| D9 | The gem loop sits behind a `fact(FileExists, "/usr/bin/gem")` barrier the play did not have | reemote materializes each `gem.packages` request — running its `GemPackages` fact read — while planning, and a `dry` pass materializes the whole wave before anything is applied, so on a host that starts without `gem` the loop's `gem list --local` exits 127; the barrier defers the loop until the binary provably exists, and its `no` answer defers to the next invocation rather than dropping work |
| D10 | Every root-owned request carries `sudo=True`; the family guard is the body's opening `if`; variable validation is pydantic | wave-model shape changes (§17): the role relied on play-level `become`; an off-target host emits nothing at all; bad values raise before any host is contacted |

## 9. Scoped out

* **The build-from-source branch** (`install-from-source.yml`: `yum` build
  packages, the `stat` marker, `get_url`, `unarchive`, `command:` make loop,
  the symlinks): a Phase 0 decision — the default path is the package install.
  Setting the flag fails at resolution rather than silently skipping (D8).
* **RedHat / `setup-RedHat.yml`**: a Phase 0 single-OS decision — the target is
  Ubuntu; off-family hosts emit nothing at all.
* **`ruby_install_gems_user`** as a knob: the login user *is* the run-as
  account for a user-scope install (D7); pointing gems at some other account's
  tree is what the `gem_home`/`gem_path` overrides on `gem.packages` are for.
--->
