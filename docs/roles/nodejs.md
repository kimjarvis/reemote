# nodejs — Node.js + npm role

| | |
| --- | --- |
| Role | [`geerlingguy.nodejs`](https://github.com/geerlingguy/ansible-role-nodejs) |
| Module | [`reemote.roles.nodejs`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/nodejs.py) |
| Runner | [`examples/roles/nodejs.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/nodejs.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `nodejs` role mapping → `NodejsVars`; `npm_config_*` read raw from `host.extra` (D5) |

<!---
| Waves | `1` — no barrier at all: the whole role is one request wave |
| Outputs | none — the deploy returns `None`; no other role reads nodejs's results |
| Extra deps | **none** — the one template is three pure substitutions, an f-string |
| Offline test | [`tests/roles/test_nodejs.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_nodejs.py) |
| Factory | plain (non-`async`) — the runner calls `nodejs(v)`; there is no barrier, so no factory IO either |

`nodejs` installs Node.js and npm from the NodeSource apt repository on Ubuntu
hosts, then wires up npm's global-prefix workflow: the repository dependencies,
the NodeSource signing key and deb822 `.sources` file (with the folded apt-cache
refresh), the version-pinned `nodejs` package, the root-owned global npm
directory owned by the install user, the optional `/etc/profile.d/npm.sh`
environment script, the global npm packages and the optional `package.json`
install. The role has no handlers, no `register`/`when` outcome reads and no
command/shell tasks.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with `yield from scoped("nodejs", nodejs(v))(host)`
and a run is one `run_waves` pass. The Ansible role's whole task list is that
single deploy, and its `defaults/main.yml` is `NodejsVars`.

This is the corpus's **cheapest shape** (design doc §18.12) — cheaper even than
`java`, which keeps a `fact` barrier for its version-keyed package table. Here
nothing needs a read of the machine before a request can be built: the version is
a plain variable, the repository's `Suites` is the literal `nodistro`, the npm
install user comes from the resolved variables or the inventory's own
`connection` data. So the body resolves a pure plan and yields the whole task
list as requests — **zero barriers, one wave**:

```python
npm_user = resolve_npm_user(hv.install_npm_user, host.connection)   # raises on neither
prefix, unsafe = resolve_npm_config(host.extra)                     # the npm_ knobs (D5)
plan = nodejs_plan(hv, npm_user, prefix, unsafe)                    # pure
yield from install(plan)(host)                                      # wave 0 — the whole role
```

## 1. What it is, and where it came from

The role's tasks map onto reemote operations:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `apt: name=[apt-transport-https, python3-debian, gnupg2]` | `apt.packages(DEPENDENCY_PACKAGES)` | covered |
| `get_url:` the NodeSource signing key | folded into `apt.sources_file(signed_by=<URL>)` — the URL form fetches the armored key control-side and installs `/etc/apt/keyrings/nodesource_<version>.asc` | recipe (D1/D2/D3) |
| `deb822_repository:` the NodeSource `.sources` | `apt.sources_file(types="deb", uris=…, suites="nodistro", components="main")` | covered (D1) |
| `apt: update_cache=yes when=node_repo is changed` | `apt.sources_file(update_cache=True)` chains one `apt-get update` on a real sources change | recipe (D1) |
| `apt: name=nodejs state=present` with the `regex_replace('x','')+'*'` pin | `apt.packages(("nodejs=<major>.*",))` — the glob pin converges via `fnmatchcase` (issue #41) | covered |
| `file: path={{ npm_config_prefix }} owner={{ nodejs_install_npm_user }}` | `files.directory(path, user, group, mode="0755")` | covered (D4) |
| `set_fact: nodejs_install_npm_user = ansible_user \| default(env USER)` | `resolve_npm_user` — the explicit var, else the connection's login name | recipe (D4) |
| `template: npm.sh.j2 dest=/etc/profile.d/npm.sh mode=0644` | `render_npm_profile` (f-string) → `files.install(mode="0644")` + `files.file(mode="0644")` companion | covered (D8) |
| `npm: name=… global=yes state=…` looped over `nodejs_npm_global_packages` | `npm.packages` partitioned by per-item `state` into present/latest/absent requests | recipe (D7) |
| `npm: path={{ nodejs_package_json_path }}` when non-empty | `npm.packages(package_json=…)` | covered |

No library gap blocks this migration — the audit's two tickets are now shipped:
the `npm` module (#40) and glob-aware `apt` version pins (#41), and the URL
`signed_by` form (#17) ships too. Zero new tickets.

## 2. Install

```bash
pip install reemote           # no extra: no Jinja2, no PyYAML
```

This role needs **no** `reemote[templates]` extra: `templates/npm.sh.j2` is three
pure `{{ npm_config_prefix }}` substitutions, which a plain f-string reproduces
byte-for-byte (D8). SSH access with the key named in each `connection`, and
passwordless sudo (or a `privilege` section): every request carries `sudo=True`.
The control-side signing-key fetch needs the control machine to reach
`deb.nodesource.com`.

## 3. Variables

`NodejsVars` is a frozen pydantic model (`extra="forbid"`) whose fields carry bare
names; the role name passed to `resolve_vars` binds the entry's `nodejs` role
mapping, so an inventory written for the role needs no key gymnastics.
Precedence is `model defaults < inventory role mapping < overrides`.

The two `npm_config_*` knobs are the exception (D5): they carry an `npm_` prefix
upstream, outside the `nodejs` role mapping, so they are **not** `NodejsVars`
fields — the role reads them raw from each host's `extra` channel to preserve the
existing inventory spelling byte-for-byte. Renaming them `nodejs_npm_config_*`
would break every inventory that already sets `npm_config_prefix` (the datadog
role's six unprefixed probe-config variables are the same precedent).
--->

::: reemote.roles.nodejs
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - nodejs

**Variables:**

### Role variables (`defaults/main.yml`)

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `version` | `str` | `"16.x"` — interpolated into the repository name/URI and, with `x`→`*`, the `nodejs=<major>.*` apt pin | `nodejs_version` |
| `install_npm_user` | `str` | `""` → the inventory connection's login name; owner of the global npm directory (D4) | `nodejs_install_npm_user` |
| `npm_global_packages` | `tuple[NpmGlobalPackage, …]` | `()` — each a bare name or a `{name, version, state}` mapping | `nodejs_npm_global_packages` |
| `package_json_path` | `str` | `""` → the task is skipped, as the role's `when: … != ""` | `nodejs_package_json_path` |
| `generate_etc_profile` | `bool` | `True` → write `/etc/profile.d/npm.sh` | `nodejs_generate_etc_profile` |

### Raw `host.extra` knobs (not `NodejsVars` fields, D5)

| Key | Type | Default | Read by |
| --- | --- | --- | --- |
| `npm_config_prefix` | `str` | `/usr/local/lib/npm` — npm's relocated global prefix | `resolve_npm_config(host.extra)` |
| `npm_config_unsafe_perm` | `bool` | `False` — suppress npm's UID/GID switch as root | `resolve_npm_config(host.extra)` |

An `npm_global_packages` entry with an empty `name`, or an empty `version`,
raises pydantic `ValidationError` (a `ValueError`) before any host is contacted;
a key naming no field in the role mapping, such as `verson`, raises
`RoleVarsError` naming the valid keys. A wrong-typed or empty `npm_config_*`
extra key raises in the body (`TypeError` / `ValueError`) before the request wave
is built.

<!---
## 5. Run it

The role is library code; `examples/roles/nodejs.py` is the runnable artifact —
a standalone program with its own `main()`, no shared runner. It loads and
validates `inventory.json` from the current working directory, resolves the
role's variables (the defaults overlaid with the entry's `nodejs` role mapping,
then the call-site `overrides`), builds the deploy from the `nodejs`
factory and drives it with `run_waves` — a `dry=True` preview, then the real
apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import NodejsVars, nodejs

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(
        NodejsVars, "nodejs", inventory,
        overrides={"version": "20.x", "npm_global_packages": ["npm-check-updates"]},
    )
    deploy = nodejs(vars_set)                                          # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)       # whole-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)                 # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/nodejs.py
```

The call-site `NODEJS_OVERRIDES` pin `version` and hand `npm_global_packages` a
couple of entries, so the demonstration exercises the whole stream and not just
the repository.

**The dry pass has no barrier to park on (D9).** `java`/`docker`/`repo_epel`
park on their first `fact` barrier, so a `dry=True` preview stops there having
planned nothing. This role yields requests from its first item, so `dry=True`
instead *plans the entire wave* — reading each fact and firing the control-side
signing-key fetch — and applies nothing. The preview is the whole wave, not a
one-barrier stub.

**Run the program twice.** The second run must report no change: the
dependencies and `nodejs` package are installed at the pinned version, the
repository and its keyring are byte-identical, the global npm directory exists
with the right owner, the profile script matches with mode `0644`, and the
global npm packages are already present. That is the migration's idempotence
claim, and the offline suite asserts it.

## 6. What it does

One deploy, one wave, zero barriers. Every value the role read from its
variables is frozen into a pure plan before the wave, and the driver applies
each request before pulling the next — so the ordering the role relies on holds
(dependencies → repository + chained cache refresh → `nodejs` install → npm
wiring). The shape the test pins:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `install`: `apt.packages(DEPENDENCY_PACKAGES)`, `apt.sources_file(signed_by=<URL>, update_cache=True)`, `apt.packages(("nodejs=<major>.*",))`, `files.directory(npm prefix)`, `files.install(npm.sh)` + `files.file(mode="0644")` companion (when `generate_etc_profile`), up to three `npm.packages` state partitions, `npm.packages(package_json=…)` (when set) | nothing needs a read of the machine first, so the whole task list is one pure request wave |

Four rules are load-bearing:

* **No barrier, so no checkpoint and no early dry park.** The role has no
  handler, no version probe and no `register`/`when` read, so nothing consumes a
  report and nothing forces a wave split. The dry run plans everything and
  applies nothing (D9).
* **Both error paths raise before any request.** A host with neither
  `install_npm_user` nor a connection login name (D4), and a wrong-typed
  or empty `npm_config_*` extra key (D5), fail while the plan resolves — the
  role's only error paths, and neither leaves a half-installed host.
* **The glob pin converges (issue #41).** `nodejs=16.*` is recorded verbatim by
  the simulation and satisfied on the rerun via `fnmatchcase`, so the second
  pass reports no change rather than re-installing forever.
* **The mode companion is a first-pass no-op by design.** `files.install` writes
  content *and* mode, so the paired `files.file` changes nothing until a later
  pass finds drift: content byte-identical but mode `0640` → install reports no
  change (its idempotence is content-only) and the companion re-applies `0644` —
  exactly what the role's `template: mode=0644` enforces on every run (the
  java/apache precedent).

A host whose `family` is not `"Ubuntu"` returns before its first `yield`: no
requests, no barriers, no report lines — Alpine and CentOS entries in a shared
inventory are never contacted.

## 7. Transcript

From `tests/roles/test_nodejs.py`, against the simulated Ubuntu host plus a
RedHat host the role never touches. The control-side signing-key fetch is
monkeypatched, so no network is touched. Fresh host, first pass:

```text
(root)
  wave 0
    ubuntu: Ensure NodeSource dependencies are present [changed: …]
    ubuntu: Add NodeSource repository for Node.js [changed: …]
    ubuntu: Ensure Node.js and npm are installed [changed: …]
    ubuntu: Create npm global directory [changed: …]
    ubuntu: Add npm prefix bin directory to the global PATH [changed: …]
    ubuntu: Set npm profile script mode [no change: …]
    ubuntu: Ensure npm global packages are installed [changed: …]
```

The rerun — every request a no-op, the glob pin satisfied, the profile script
byte-identical:

```text
(root)
  wave 0
    ubuntu: Ensure NodeSource dependencies are present [no change: …]
    ubuntu: Add NodeSource repository for Node.js [no change: …]
    ubuntu: Ensure Node.js and npm are installed [no change: …]
    ubuntu: Create npm global directory [no change: …]
    ubuntu: Add npm prefix bin directory to the global PATH [no change: …]
    ubuntu: Set npm profile script mode [no change: …]
    ubuntu: Ensure npm global packages are installed [no change: …]
```

The RedHat host appears in neither tree: no request line, no report record.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | The role's three repository tasks — `get_url` (the signing key), `deb822_repository` and the `apt: update_cache=yes when node_repo is changed` refresh — fold into ONE `apt.sources_file(signed_by=<URL>, update_cache=True)` | the URL `signed_by` form (#17) fetches the ASCII-armored key control-side and installs the repo-named keyring itself; `update_cache=True` chains one `apt-get update` on a real sources change, reproducing the fire-on-change refresh with no separate task and no barrier |
| D2 | The keyring path differs: `/etc/apt/keyrings/nodesource_<version>.asc`, not the role's `/etc/apt/signing-key-nodesource-repo.asc` | `apt.sources_file`'s fixed convention; functionally identical, the sources file references wherever the key lands. The role's `get_url` User-Agent workaround is dropped — reemote's fetch is a plain control-side GET; if NodeSource rejects it, fetch the key yourself and pass `signed_by` a local path |
| D3 | `signed_by=<URL>` accepts ASCII-armored text ONLY and raises `ValueError` on a binary `.gpg` body | if NodeSource serves a binary key, install it with `apt.key` from a control-side fetch and pass `signed_by` the keyring path — the documented fallback in the operation's own error message |
| D4 | `set_fact: nodejs_install_npm_user = ansible_user \| default(env USER)` becomes `resolve_npm_user` — the explicit var, else the connection's login name | reemote has no `ansible_user` magic variable and the *controller's* `$USER` is not a meaningful per-host owner; a host with neither raises before any request is built |
| D5 | `npm_config_prefix` and `npm_config_unsafe_perm` are read raw from `host.extra`, not modeled as `NodejsVars` fields | they carry an `npm_` prefix upstream, and `resolve_vars` binds only the `nodejs` role mapping; renaming them would break every existing inventory that sets `npm_config_prefix` (the datadog role's six unprefixed probe-config variables are the same precedent) |
| D6 | `NODE_PATH` on the npm task's environment is dropped | the `npm` module scopes it out deliberately — it steers Node's *runtime* module resolution, not what `npm install` records as installed. The rendered `/etc/profile.d/npm.sh` still exports it verbatim, so the login environment is preserved where it matters |
| D7 | The global-package loop's per-item `state` partitions into up to three `npm.packages` requests (present / latest / absent) | `present` and `latest` are request-level flags applying to every package in one call; the role ran one npm task per item. A version pin becomes reemote's shared `<pkg>=<version>` spelling, translated to npm's `<pkg>@<version>` |
| D8 | The npm requests carry `sudo=True`, and the profile `template: mode=0644` gets a paired `files.file(mode="0644")` companion | the root-owned global prefix needs escalation to write, where the role relied on play-level `become`; `files.install` idempotence is content-only and would never re-converge a drifted mode (the java/apache precedent) |
| D9 | The `dry=True` preview plans the ENTIRE wave instead of parking on a first barrier | there is no barrier to park on, so `run_waves`' `if parked and dry:` early-stop never triggers; the dry run reads facts and fires the control-side key fetch but applies nothing |

## 9. Scoped out

* **RedHat / CentOS / Rocky / Fedora branches** (`setup-RedHat.yml`, the
  `nodejs` yum module and the NodeSource rpm): a Phase 0 single-OS decision —
  the target is Ubuntu. reemote's `dnf` module covers the operations those
  branches use; they are simply outside the agreed target.
* **Build-from-source and other opt-in branches**: the role's default path is
  the NodeSource package install; any default-false branch is scoped out early.
--->
