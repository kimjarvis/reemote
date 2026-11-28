# php_versions — PHP versions role

| | |
| --- | --- |
| Role | [`geerlingguy.php-versions`](https://github.com/geerlingguy/ansible-role-php-versions) |
| Module | [`reemote.roles.php_versions`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/php_versions.py) |
| Runner | [`examples/roles/php_versions.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/php_versions.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `php_versions` role mapping → `PhpVersionsVars` |

<!---
| Waves | `3` on a changed host (0–2); `2` once converged — the gated wave vanishes entirely |
| Outputs | `PhpVersionsOutputs` — the thirteen `set_fact` aliases, the composition channel to the `php` role |
| Extra deps | **none** — every derived path is an f-string, no template engine |
| Offline test | [`tests/roles/test_php_versions.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_php_versions.py) |
| Factory | plain (non-`async`) — the runner calls `php_versions(v)`; the release read is a `fact` barrier, and the binary signing key is fetched control-side by the role's own `files.download` request |

`php_versions` prepares an Ubuntu host for exactly one PHP version: the
repository dependencies, the Ondrej Sury PPA as a deb822 `.sources` file with
its downloaded keyring (D8), the purge of every *other* version's `php<X>-common`
package, and — only when that purge removed something — the purge of the shared
`php-common` package. It installs nothing: the chosen version is the
[`php`](php.md) role's job, and this role's whole reason to exist is the
version-keyed package names and paths it publishes for that role to install
from.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with
`yield from scoped("php_versions", php_versions(v))(host)` and a run is one
`run_waves` pass. The Ansible role's whole task list is that single deploy, and
its `defaults/main.yml` is `PhpVersionsVars`.

This is the corpus's **outputs-publisher** role — the upstream's soft
`geerlingguy.php` dependency becomes a return value — and the cleanest
one-checkpoint gate in the corpus:

```python
info = yield fact(LinuxDistribution)              # wave 0 — the repo's name + Suites
plan = resolve_plan(hv, info)
yield from setup(plan)(host)                      # wave 1 — deps, keyring, repo, purge
setup_report = yield checkpoint("setup done")     # the register: php_version_purge
if host.name in changed_hosts(setup_report, names):
    yield from purge_common(...)(host)            # wave 2 — the gated php-common purge
return published_outputs(hv, plan)                # the thirteen set_fact aliases
```

The checkpoint *is* the Ansible `register` + `when: php_version_purge.changed`
pair: `changed_hosts` reads the setup wave's report per host, so a converged
run queues no second purge and the empty wave is never appended. Setup's three
requests share one wave because no read sits between them: the driver applies
each before pulling the next, so the index refresh still lands before the purge
runs.

## 1. What it is, and where it came from

The role's tasks map onto reemote operations and barriers:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `include_vars vars/Debian.yml` (+ the empty `vars/Debian-php{{version}}.yml` glob) | frozen module constants + `php_packages` gates | deviation (D5) |
| `set_fact: php_opcache_conf_filename` | the `OPCACHE_CONF_FILENAME` constant, carried on the outputs | covered |
| `set_fact` opcache/deps pair | module constants | covered |
| `apt: name=[apt-transport-https, ca-certificates, python3-debian]` | `apt.packages(packages=DEPENDENCY_PACKAGES)` | covered |
| `deb822_repository: name=ondrej-php-{{distribution_release}} signed_by=https://packages.sury.org/php/apt.gpg` | `files.download` of the key to `/etc/apt/keyrings/ondrej-php-<codename>.gpg` **then** `apt.sources_file(signed_by=<that path>)` — the URL is a *binary* OpenPGP key, outside the URL `signed_by` form | deviation (D8) |
| `apt: update_cache=true` (`when: php_ondrej_debian_repo.changed`) | `apt.sources_file(update_cache=True)` chains the refresh on a real sources change | deviation (D1) |
| `apt: name={{ php_versions_debian \| reject('search', 'php'~php_version) }} state=absent purge=true force=true` | `apt.packages(packages=purge_packages(v), present=False, purge=True)` | covered, minus `force` (D2) |
| `apt: name=php-common state=absent purge=true` (`when: php_version_purge.changed \| bool`) | the gated wave, `changed_hosts(yield checkpoint("setup done"), names)` | barrier (D4) |
| `set_fact` "Define PHP variables" (thirteen aliases) | the `PhpVersionsOutputs` return value | deviation (D3) |
| `php_versions_install_recommends` → `php_install_recommends` alias | a `PhpVersionsVars` field carried onto the outputs | deviation (D7) |

No library gap blocked this migration, so none was filed: issues #14
(`update_cache` chains fire-on-change) and #17 (the URL `signed_by` form) were
the two prerequisites and both are closed. Sury's key being binary sends the
repository task through the *other* sanctioned key route (D8, `files.download`
issue #23) rather than the URL form.

## 2. Install

```bash
pip install reemote           # no extra: no Jinja2, no PyYAML
```

This role renders nothing — every path it derives is an f-string — so it needs
no `reemote[templates]` extra. SSH access with the key named in each
`connection`, and passwordless sudo (or a `privilege` section): every request
carries `sudo=True`.

## 3. Variables

`PhpVersionsVars` is a frozen pydantic model (`extra="forbid"`) with two bare
fields; the role name passed to `resolve_vars` binds the entry's `php_versions`
role mapping. Precedence is `model defaults < inventory role mapping <
overrides`.

--->

::: reemote.roles.php_versions
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - php_versions

**Variables:**

### The version

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `version` | `str` | `8.3` — must be dotted digits; decides the purge list and every published path | `php_version` |
| `install_recommends` | `bool` | `false` — reaches no request here, only the outputs (D7) | `php_versions_install_recommends` |

<!---

`version` is validated at resolution time by the model's own field validator —
a non-version string raises pydantic's `ValidationError` (a `ValueError`)
before any host is contacted, where Ansible only found out at the first
`is version(...)` test.

## 5. Run it

The role is library code; `examples/roles/php_versions.py` is the runnable
artifact — a standalone program with its own `main()`, no shared runner. It
loads and validates `inventory.json` from the current working directory,
resolves the role's variables (the defaults overlaid with the entry's
`php_versions` role mapping, then the call-site `overrides`), builds the deploy
from the `php_versions` factory and drives it with `run_waves` — a `dry=True`
one-wave preview, then the real apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import PhpVersionsVars, php_versions

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(
        PhpVersionsVars, "php_versions", inventory, overrides={"version": "8.3"},
    )
    deploy = php_versions(vars_set)                                 # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)   # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)             # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/php_versions.py
```

The call-site `PHP_VERSIONS_OVERRIDES` pin `version="8.3"`, so the
demonstration purges the 8.0/8.1/8.2/8.4 `-common` packages and publishes the
8.3-shaped outputs. The `dry=True` pass is a one-wave preview: the role's first
item is the `LinuxDistribution` `fact` barrier, so the run parks there
(`barrier: LinuxDistribution → unknown until run`), reads no state and applies
nothing.

**Run the program twice.** The second run must report no change: the
dependencies are present, the repo and keyring are byte-identical, the other
versions' `-common` packages are gone — so nothing changed at the checkpoint,
the gated `Also purge php-common package.` wave never runs and simply is not
there. That is the migration's idempotence claim, and the offline suite asserts
it. On a real host note the purge arm is the *mutating* half: this role removes
packages, and expects the chosen version to be installed afterwards by the
`php` role.

## 6. What it does

One deploy. Each barrier closes its own wave, and a wave with no records is not
appended — so the shape below is what the test pins:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `fact(LinuxDistribution)` | `release_meta.VERSION_CODENAME` (Ansible's `distribution_release`) feeds both the repository's name — `ondrej-php-<codename>` — and its `Suites`; the whole setup wave is a pure function of it |
| 1 | `setup`: dependencies, the keyring download, the deb822 repo referencing it, the purge of the other versions — closed by `checkpoint("setup done")` | no read sits between the four, so they share one wave in the plays' order; the key precedes the sources file so the chained refresh (D1) runs against a signed repository; removals resolve against the dpkg database, so the purge needs no barrier of its own |
| 2 | `purge_common`: the `php-common` removal — only where **this host** sits in `changed_hosts(setup_report, names)` | the `register` + `when` pair as an in-stream gate (D4); on a converged host the body yields nothing and the wave vanishes |

Three rules are load-bearing:

* **The checkpoint is the register.** `report = yield checkpoint("setup done")`
  sits immediately after the wave it reports on (§4.3's placement rule), and
  `changed_hosts(report, PURGE_GATE_NAMES)` reads it per host — no second
  pass, no stale `when`. Rigging a purge to change again (reinstalling a
  version behind the role's back) re-fires the gated wave on the very next
  pass, which is exactly what the offline test does.
* **The role publishes through its return value.** Every path — including the
  family guard's early `return PhpVersionsOutputs(version=None)` — returns the
  outputs, so a composing `yield from scoped("php_versions", ...)` never sees
  a missing value: the thirteen `set_fact` aliases as a frozen dataclass
  (D3).
* **The codename raise is the body's own.** A barrier result is data; if the
  distribution read carries no `VERSION_CODENAME`, `resolve_plan` raises
  `ValueError` right after the barrier and before any request is built, so an
  unreadable host fails without mutating anything.

A host whose `family` is not `"Ubuntu"` returns before its first `yield`: no
requests, no barriers, no report lines — Alpine and CentOS entries in a shared
inventory are never contacted.

## 7. Transcript

From `tests/roles/test_php_versions.py`, against the simulated Ubuntu 24.04
host (`version: "8.3"`, seeded with the 8.0/8.1/8.2/8.4 `-common` packages, the
chosen 8.3 one, and `php-common`) plus a RedHat host the role never touches.
The key fetch is patched offline. Fresh host, first pass:

```text
(root)
  wave 0
    barrier: LinuxDistribution → {'name': 'Ubuntu', 'family': 'Debian', 'major': '24', ...}
  wave 1
    ubuntu: Add dependencies for PHP versions. [changed: 3 package(s) to install]
    ubuntu: Install Ondrej's PHP repo key. [changed: content /etc/apt/keyrings/ondrej-php-noble.gpg is absent, will install]
    ubuntu: Add Ondrej's PHP repo.   [changed: apt sources file ... will be written; apt update]
    ubuntu: Purge other PHP versions. [changed: 4 package(s) to remove]
    barrier: setup done → 1 host notified
  wave 2
    ubuntu: Also purge php-common package. [changed: 1 package(s) to remove]
```

The rerun — every barrier re-runs (they are reads, not cached plan values), the
checkpoint finds nothing, and the **gated wave disappears entirely**:

```text
(root)
  wave 0
    barrier: LinuxDistribution → {...}
  wave 1
    ubuntu: Add dependencies for PHP versions. [no change: all packages present]
    ubuntu: Install Ondrej's PHP repo key. [no change: content already up to date (/etc/apt/keyrings/ondrej-php-noble.gpg)]
    ubuntu: Add Ondrej's PHP repo.   [no change: apt sources file ... is already configured]
    ubuntu: Purge other PHP versions. [no change: no packages to remove]
    barrier: setup done → 0 hosts notified
```

The RedHat host appears in neither tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | The separate `apt: update_cache=true` task (`when: repo.changed`) folds into `apt.sources_file(update_cache=True)` | the refresh chains onto a *real* sources change (issue #14) — same fire-on-change semantics, no separate request and no barrier |
| D2 | The purge tasks' `force: true` is **dropped** | `--force-yes` is deprecated in modern apt (the same boundary `apt.deb` records); `AptRemoveCommand` already runs non-interactively, so the flag is inert on the role's supported releases |
| D3 | The thirteen `set_fact` aliases become the `PhpVersionsOutputs` return value | Ansible published them into the play for the soft `geerlingguy.php` dependency; here the composition channel is a `yield from` return, and each consumer overrides its own role variables instead |
| D4 | The purge gate `when: php_version_purge.changed \| bool` is the in-stream checkpoint | `changed_hosts` reads the setup wave's report (§4.3), so a converged pass queues no `php-common` request — and a pass where the purge re-changes queues it again |
| D5 | The `with_fileglob` include pair — `vars/Debian.yml` plus the (nonexistent upstream) `vars/Debian-php<version>.yml` — collapses to frozen constants | the second glob arm matches nothing in the role, so there is nothing to reproduce |
| D6 | `reject('search', 'php' ~ php_version)` becomes a substring exclusion | the list entries differ only in digits and a literal dot, so the substring test is the regex search's exact result on every entry the list can hold |
| D7 | `php_versions_install_recommends` reaches no request | the Ansible role only aliases it to `php_install_recommends` for the downstream php role; here it is a vars field carried onto the outputs, where the composer maps it onto `PhpVars.install_recommends` |
| D8 | The key is installed by a separate `files.download` request, and `signed_by` takes the keyring **path** | Sury's `apt.gpg` is a *binary* OpenPGP key and `apt.sources_file`'s URL form installs armored text only (issue #17's documented boundary); `files.download`'s binary path (issue #23) is the sanctioned route, sha1-idempotent on content — one extra request in the same wave, no extra barrier |
| D9 | The family guard is the **body's opening `if`**; variable gates become **pydantic validation** | off-target hosts emit nothing at all; a bad `php_version` raises `ValidationError` before any host is contacted |

## 9. Scoped out

* **Non-Ubuntu families.** The role's RedHat branch (`setup-RedHat.yml`, the
  remi enablement) is not migrated; `include_vars` dispatch freezes to the
  Debian constants.
* **Installing the chosen version.** As upstream, this role only makes the
  version *available* (repo + purge of the others); the install and all
  configuration are the [`php`](php.md) role's job, fed by these outputs.
* **Per-version vars files.** The upstream `vars/Debian-php<version>.yml` glob
  arm does not exist in the role (D5); if an out-of-tree variant ships one,
  that is a caller-side `PhpVersionsVars`, not a hidden include.

--->
