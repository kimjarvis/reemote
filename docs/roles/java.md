# java — OpenJDK role

| | |
| --- | --- |
| Role | [`geerlingguy.java`](https://github.com/geerlingguy/ansible-role-java) |
| Module | [`reemote.roles.java`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/java.py) |
| Runner | [`examples/roles/java.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/java.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `java` role mapping → `JavaVars` |

<!---
| Waves | `2` — the `LinuxDistribution` barrier, then the whole role in one request wave |
| Outputs | none — the deploy returns `None`; no other role reads java's results |
| Extra deps | **none** — the one template is a single-variable f-string, no template engine |
| Offline test | [`tests/roles/test_java.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_java.py) |
| Factory | plain (non-`async`) — the runner calls `java(v)`; the distribution read is a `fact` barrier, not factory IO |

`java` installs the Ubuntu default OpenJDK package and optionally exports
`JAVA_HOME` through a `/etc/profile.d` script: the version-keyed
`vars/Ubuntu-<major>.yml` package selection, the `man1` directory workaround
(Debian bug 863199 / role issue #64), the `apt` install, and the optional
`java_home.sh` profile template. The role has no handlers, no loops and no
command/shell tasks.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with `yield from scoped("java", java(v))(host)`
and a run is one `run_waves` pass. The Ansible role's whole task list is that
single deploy, and its `defaults/main.yml` is `JavaVars`.

This is the corpus's **cheapest end-to-end role** (design doc §18.12): one
barrier, one request wave, no checkpoint, no handler. The stream is:

```python
info = yield fact(LinuxDistribution)   # wave 0 — the vars-table dispatch source
plan = java_plan(hv, info)             # raises here on a wrong/unsupported distro
yield from install(plan)(host)         # wave 1 — man1 dir, apt install, profile
```

## 1. What it is, and where it came from

The role's tasks map onto reemote operations and barriers:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `include_vars: Ubuntu-{{ major }}.yml` (the `vars/Ubuntu-<major>.yml` dispatch) | `yield fact(LinuxDistribution)` → `ubuntu_major` → frozen `UBUNTU_JAVA_PACKAGES` table in `java_plan` | barrier (recipe) |
| `set_fact: java_packages={{ __java_packages \| list }}` when not defined | `JavaVars.packages` defaults to `None` → the table selects; an explicit tuple wins | covered |
| `file: path=/usr/share/man/man1 state=directory mode=0755` when Ubuntu ≥ 18 | `files.directory(mode="0755")`, the gate frozen into the plan | covered |
| `apt: name={{ java_packages }} state=present` | `apt.packages(packages, update=True, cache_valid_time=600)` | covered (D1) |
| `template: java_home.sh.j2 dest=/etc/profile.d/java_home.sh mode=0644` | `render_java_home` (f-string) → `files.install(mode="0644")` + `files.file(mode="0644")` mode companion | covered (D2) |

No library gap blocks this migration — the audit filed zero tickets. The
role's one open-adjacent tracker item (#17, `apt.sources_file(signed_by=)`
key URLs) concerns repository management this role never does. The old
example's `build_java(plans)` gather → snapshot → factory split is gone: the
dispatch is now a barrier inside the body.

## 2. Install

```bash
pip install reemote           # no extra: no Jinja2, no PyYAML
```

This is the templating role that needs **no** `reemote[templates]` extra: its
one template is the single line `export JAVA_HOME={{ java_home }}`, which a
plain f-string reproduces byte-for-byte (D2). SSH access with the key named in
each `connection`, and passwordless sudo (or a `privilege` section): every
request carries `sudo=True`.

## 3. Variables

`JavaVars` is a frozen pydantic model (`extra="forbid"`) whose fields carry bare
names; the role name passed to `resolve_vars` binds the entry's `java` role
mapping, so an inventory written for the role needs no key gymnastics.
Precedence is `model defaults < inventory role mapping < overrides`.

Validation is fail-fast, before any host is contacted: `packages` accepts a
list or tuple (normalized to a `tuple`, the hashability rule) of non-empty
strings or `null`; `home` a string; a key naming no field in the role mapping,
such as `packagez`, raises `RoleVarsError` naming the valid keys. The old
example raised `TypeError` for several of these gates; here they are all pydantic
`ValidationError` — a `ValueError`.
--->

::: reemote.roles.java
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - java

**Variables:**

### Role variables (`defaults/main.yml`)

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `packages` | `tuple[str, …] \| None` | `null` → the `vars/Ubuntu-<major>.yml` list for the gathered release; an explicit tuple (even empty) wins | `java_packages` |
| `home` | `str` | `""` — when non-empty, rendered into `/etc/profile.d/java_home.sh` | `java_home` |

### Frozen release table (`vars/Ubuntu-<major>.yml`)

| Ubuntu major | Packages |
| --- | --- |
| 12, 14 | `openjdk-7-jdk` |
| 16 | `openjdk-8-jdk` |
| 18, 20 | `openjdk-11-jdk` |
| 22 | `openjdk-17-jdk` |
| 24 | `openjdk-21-jdk` |
| 26 | `openjdk-25-jdk` |

An unsupported major raises at the barrier — **even when `packages` is
overridden**, because the role's `include_vars` runs unconditionally (D4).

<!---
## 5. Run it

The role is library code; `examples/roles/java.py` is the runnable artifact —
a standalone program with its own `main()`, no shared runner. It loads and
validates `inventory.json` from the current working directory, resolves the
role's variables (the defaults overlaid with the entry's `java` role mapping,
then the call-site `overrides`), builds the deploy from the `java`
factory and drives it with `run_waves` — a `dry=True` one-wave preview, then
the real apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import JavaVars, java

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(
        JavaVars, "java", inventory,
        overrides={"home": "/usr/lib/jvm/java-21-openjdk-amd64"},
    )
    deploy = java(vars_set)                                            # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)       # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)                 # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/java.py
```

The call-site `JAVA_OVERRIDES` set `home` to a concrete JDK path, so the
demonstration exercises the profile write and not just the package install;
`packages` stays unset so the release table still drives the version. The
`dry=True` pass is a one-wave preview: java's first item is the
`LinuxDistribution` `fact` barrier, so the run parks there (`barrier:
LinuxDistribution → unknown until run`), reads no state and applies nothing.

**Run the program twice.** The second run must report no change: the package
is installed, the apt cache stamp is fresh (the folded refresh is gated on
it), the man1 directory exists, and the profile script is byte-identical
with mode `0644`. That is the migration's idempotence claim, and the offline
suite asserts it.

## 6. What it does

One deploy. The barrier closes its own wave, and a wave with no records is not
appended — so the shape below is what the test pins:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `fact(LinuxDistribution)` | the major version the vars table selects on and the ≥ 18 `man1` gate reads; also the re-check that the declared family matches the machine |
| 1 | `install`: `files.directory(/usr/share/man/man1, mode="0755")` (Ubuntu ≥ 18 only), `apt.packages(packages, update=True, cache_valid_time=600)`, `files.install(java_home.sh, mode="0644")` + `files.file(mode="0644")` companion (when `home` is set) | every read the role took mid-play was frozen at the barrier, so the whole task list is one pure request wave |

Four rules are load-bearing:

* **No checkpoint.** The role has no handler and no `register`/`when` outcome
  read, so nothing consumes a report — a checkpoint here would only add a
  barrier-only wave that reports nothing (§4.3). If a migrated version of this
  role ever grows one, that is the signal a barrier was added speculatively.
* **Both error paths raise at the barrier, before any request.** A host
  declared `Ubuntu` that reports another distribution (D3) and a release the
  role ships no vars file for (D4) fail in `java_plan`, immediately after the
  `fact` read — the role's only error paths, and neither leaves a half-installed
  host. A barrier result is data; the raises are the role's, not the driver's.
* **The mode companion is a first-pass no-op by design.** `files.install`
  writes content *and* mode, so the paired `files.file` changes nothing until
  a later pass finds drift: content byte-identical but mode `0640` → install
  reports no change (its idempotence is content-only) and the companion
  re-applies `0644` — exactly what the role's `template: mode=0644` enforces
  on every run (the apache `ports.conf` precedent). The offline test pins both
  sides.
* **The dispatch is version-keyed, not family-keyed.** A single-OS scope
  usually collapses `include_vars` to frozen constants, but this role's vars
  split by *major version*, so the read of the machine stays — it is the one
  fact the request wave is a pure function of.

A host whose `family` is not `"Ubuntu"` returns before its first `yield`: no
requests, no barriers, no report lines — Alpine and CentOS entries in a shared
inventory are never contacted.

## 7. Transcript

From `tests/roles/test_java.py`, against the simulated Ubuntu 24.04 host
(`home` set) plus a RedHat host the role never touches. No between-pass
bridge is needed — the simulation's own mutators maintain every seam the
second pass reads. Fresh host, first pass:

```text
(root)
  wave 0
    barrier: LinuxDistribution → {'name': 'Ubuntu', 'family': 'Debian', 'major': '24', ...}
  wave 1
    ubuntu: Ensure 'man' directory exists [changed: /usr/share/man/man1 missing, will create]
    ubuntu: Ensure Java is installed [changed: apt update; 1 package(s) to install]
    ubuntu: Set JAVA_HOME if configured [changed: content /etc/profile.d/java_home.sh is absent, will install]
    ubuntu: Set JAVA_HOME profile script mode [no change: file /etc/profile.d/java_home.sh already exists]
```

The rerun — the barrier re-runs (it is a read, not a cached plan value) and
every request is a no-op:

```text
(root)
  wave 0
    barrier: LinuxDistribution → {...}
  wave 1
    ubuntu: Ensure 'man' directory exists [no change: directory /usr/share/man/man1 already exists]
    ubuntu: Ensure Java is installed [no change: apt update skipped, cache is fresh; all packages present]
    ubuntu: Set JAVA_HOME if configured [no change: content already up to date (/etc/profile.d/java_home.sh)]
    ubuntu: Set JAVA_HOME profile script mode [no change: file /etc/profile.d/java_home.sh already exists]
```

The mode-drift pass — content untouched, stat mode pushed to `640` — isolates
the companion's purpose: `Set JAVA_HOME if configured` stays
`[no change: content already up to date]` while
`Set JAVA_HOME profile script mode [changed: mode 640 differs, will chmod 644]`.

The RedHat host appears in neither tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | The molecule converge playbook's apt-index refresh `pre_task` (`update_cache=true cache_valid_time=600 changed_when=false`) is folded into `apt.packages(update=True, cache_valid_time=600)` | the role's own `apt` task carries no refresh; folding the pre_task in makes the role self-contained on a fresh host. The refresh is gated on the `AptUpdateStamp` fact against the **controller's** UTC clock, and a stale-cache refresh reports `changed=True` where the pre_task forced `changed_when: false` |
| D2 | The template renders with an **f-string**, not Jinja2 | the body is exactly `export JAVA_HOME={{ java_home }}` plus a trailing newline; byte-identical with no dependency. The trailing newline is kept (the apache precedent's `keep_trailing_newline=True`) — Jinja2's *library* default would strip it |
| D3 | A non-Ubuntu distribution on a host declared `Ubuntu` **fails fast** | the role would dispatch to another OS's vars and tasks; this migration targets Ubuntu, so `ubuntu_major` raises naming the reported distribution instead of silently installing the wrong package list |
| D4 | An unsupported Ubuntu major **fails fast, even with an override** | faithful: `include_vars: Ubuntu-<major>.yml` runs unconditionally and errors with "file not found" before the override is ever consulted; the error names the supported majors |
| D5 | The family guard is the **body's opening `if`**, validation gates become **pydantic validation**, and the gather → snapshot → factory split becomes an in-body **`fact` barrier** | wave-model shape changes (§17): an off-target host emits nothing at all; bad values raise `ValidationError` before any host is contacted; the old example's frozen `plans` dict disappears with the two-phase model |

## 9. Scoped out

* **RedHat / CentOS / Rocky / Alma / OracleLinux branches**
  (`setup-RedHat.yml`, `vars/RedHat-*.yml`): a Phase 0 single-OS decision —
  the target is Ubuntu.
* **Amazon Linux branches** (`setup-Amazon.yml`, `vars/Amazon-*.yml`) and
  **FreeBSD branches** (`setup-FreeBSD.yml` with its pkgng and `/proc`
  mounts), **Fedora vars**: same decision. reemote's `dnf`, `apk` and
  `server` modules cover the operations those branches use; they are simply
  outside the agreed Ubuntu target.
--->
