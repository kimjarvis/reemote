# pip — Python pip packages role

| | |
| --- | --- |
| Role | [`geerlingguy.pip`](https://github.com/geerlingguy/ansible-role-pip) |
| Module | [`reemote.roles.pip`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/pip.py) |
| Runner | [`examples/roles/pip.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/pip.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `pip` role mapping → `PipVars` |

<!---
| Waves | `2` — the `PythonVersion` barrier, then the whole role in one request wave |
| Outputs | none — the deploy returns `None`; no other role reads pip's results |
| Extra deps | **none** — the role ships no template and reads no YAML |
| Offline test | [`tests/roles/test_pip.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_pip.py) |
| Factory | plain (non-`async`) — the runner calls `pip(v)`; the interpreter read is a `fact` barrier, not factory IO |

`pip` installs pip on Ubuntu hosts and then ensures a list of pip packages: the
interpreter's pip package via apt, the removal of the distro's PEP 668
`EXTERNALLY-MANAGED` marker so pip may write the system site-packages, and the
`pip_install_packages` loop — version pins, bare names and virtualenv-targeted
items alike. The role has no handlers, no command/shell tasks, and exactly one
read of the machine: the interpreter version the marker path interpolates.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with `yield from scoped("pip", pip(v))(host)`
and a run is one `run_waves` pass. The Ansible role's whole task list is that
single deploy, and its `defaults/main.yml` is `PipVars`.

This is the `repo_epel` shape minus its control fetch — **one barrier, one
request wave**, no checkpoint and no handler. The only mid-play read in the
Ansible role was the `ansible_facts` interpolation into the marker path, and a
`fact` barrier carries it inside the body:

```python
python_version = yield fact(PythonVersion)    # wave 0 — the minor for the marker path
plan = pip_plan(hv, python_version["minor"])  # pure
yield from install(plan)(host)                # wave 1 — apt pip, marker, pip loop
```

## 1. What it is, and where it came from

The role's tasks map onto reemote operations and one barrier:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `ansible.builtin.package: name={{ pip_package }} state=present` | `apt.packages((plan.package,))` — the module picked for the Ubuntu target (D1) | covered (D1) |
| `ansible_facts['python']['version']['minor']` in the marker path | `yield fact(PythonVersion)` — read from the target, since the path varies by release (22.04 → `python3.10`, 24.04 → `python3.12`) | barrier (D2) |
| `file: path=/usr/lib/python3.{{ minor }}/EXTERNALLY-MANAGED state=absent` | `files.file(path, present=False)` — idempotent, an already-absent marker is a no-op | covered (D2) |
| `pip: name=…` looped over `pip_install_packages` | **one `pip.packages` request per item**, partitioned by the item's `state` (D3) | recipe (D3) |
| `pip: virtualenv=…` (the module's auto-created venv) | `pip.venv` create request, then `pip.packages(pip=<venv>/bin/pip)` (D4) | recipe (D4) |
| `pip_executable` default via `ternary`/`startswith('python3')` | `derive_executable` — a pure helper, overridden by an explicit `pip_executable` (D5) | covered (D5) |
| `state=forcereinstall` (README option only, never a default) | rejected by pydantic before any host is contacted (D6) | scoped out (D6) |

No library gap blocks this migration — both audit tickets are now shipped: the
`pip` module (#26) and `server.PythonVersion` (#65). Zero new tickets.

## 2. Install

```bash
pip install reemote           # no extra: no Jinja2, no PyYAML
```

This role needs **no** `reemote[templates]` extra: it ships no template and
reads no YAML (the `php`/`nodejs` precedent). SSH access with the key named in
each `connection`, and passwordless sudo (or a `privilege` section): every
request carries `sudo=True`. The `PythonVersion` barrier reads the target's own
`python3`, which the apt pip package depends on anyway.

## 3. Variables

`PipVars` is a frozen pydantic model (`extra="forbid"`) whose fields carry bare
names; the role name passed to `resolve_vars` binds the entry's `pip` role
mapping, so an inventory written for the role needs no key gymnastics.
Precedence is `model defaults < inventory role mapping < overrides`.

Validation is fail-fast, before any host is contacted: an empty `package` or
`executable`, an empty `name`, `version` or `extra_args` in an entry, and any
`state` beyond `present`/`absent`/`latest` all raise pydantic `ValidationError`
(a `ValueError`); a key naming no field in the role mapping, such as `packge`,
raises `RoleVarsError` naming the valid keys. A bare string entry (`- colorama`) is
coerced to `{"name": "colorama"}` by the `install_packages` before-validator,
exactly as the role's `item.name | default(item)` reads it.
--->

::: reemote.roles.pip
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - pip

**Variables:**

### Role variables (`defaults/main.yml`)

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `package` | `str` | `"python3-pip"` — the apt package providing pip | `pip_package` |
| `executable` | `str \| None` | `null` → derived from `package` (`python3-…` → `pip3`, else `pip`, D5); an explicit value wins | `pip_executable` |
| `install_packages` | `tuple[PipPackage, …]` | `()` — each a bare name or a `{name, version, virtualenv, state, extra_args}` mapping | `pip_install_packages` |

### `pip_install_packages` entries (`PipPackage`)

| Field | Type | Default | Ansible equivalent |
| --- | --- | --- | --- |
| `name` | `str` | required, non-empty | `item.name \| default(item)` |
| `version` | `str \| None` | `null` — rendered as pip's `<pkg>==<version>` spec | `item.version \| default(omit)` |
| `virtualenv` | `str \| None` | `null` — selects the `pip=<venv>/bin/pip` target and triggers a `pip.venv` create (D4) | `item.virtualenv \| default(omit)` |
| `state` | `"present" \| "absent" \| "latest"` | `"present"` — mapped onto the operation's `present`/`latest` flags | `item.state \| default('present')` |
| `extra_args` | `str \| None` | `null` — appended to the install command as `extra_install_args` | `item.extra_args \| default(omit)` |

<!---
## 5. Run it

The role is library code; `examples/roles/pip.py` is the runnable artifact —
a standalone program with its own `main()`, no shared runner. It loads and
validates `inventory.json` from the current working directory, resolves the
role's variables (the defaults overlaid with the entry's `pip` role mapping,
then the call-site `overrides`), builds the deploy from the `pip` factory and
drives it with `run_waves` — a `dry=True` one-wave preview, then the real
apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import PipVars, pip

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(
        PipVars, "pip", inventory,
        overrides={"install_packages": [
            {"name": "ipaddress", "version": "1.0.18"},
            "colorama",
            {"name": "six", "virtualenv": "/srv/app/venv"},
        ]},
    )
    deploy = pip(vars_set)                                             # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)       # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)                 # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/pip.py
```

The call-site `PIP_OVERRIDES` turn on the `install_packages` loop with a
version pin, a bare name and a virtualenv entry, so the demonstration exercises
the whole stream — the apt pip package, the marker removal and the loop — and
not just the package install.

**The dry pass parks at the barrier.** `pip`'s first item is the
`PythonVersion` `fact` barrier, so the `dry=True` preview stops there
(`barrier: PythonVersion → unknown until run`), reads no version and applies no
request. A converged rerun is the honest preview.

**Run the program twice.** The second run must report no change: the pip
package is installed, the `EXTERNALLY-MANAGED` marker is gone, and every pip
package — in its named virtualenv where it has one — is already present at its
requested version. That is the migration's idempotence claim, and the offline
suite asserts it.

## 6. What it does

One deploy. The barrier closes its own wave, and a wave with no records is not
appended — so the shape below is what the test pins:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `fact(PythonVersion)` | the interpreter `minor` the `EXTERNALLY-MANAGED` path interpolates — the only read a request needs before it can be built (D2) |
| 1 | `install`: `apt.packages((package,))`, `files.file(marker, present=False)`, and per loop item an optional `pip.venv` create then one `pip.packages` request (present/latest/absent by state) | every other value is frozen from the resolved variables, so the rest of the task list is one pure request wave |

Four rules are load-bearing:

* **The ordering holds inside the single wave.** The driver materializes and
  applies one request per host before pulling the next, so the apt install
  lands `pip` before the loop runs it, and the marker removal precedes the
  first system-pip install that PEP 668 would otherwise block — with **no
  barrier between them**, collapsing the old two-deploy split to one pass.
* **No checkpoint.** The role has no handler and no `register`/`when` outcome
  read, so nothing consumes a report — a checkpoint here would only add a
  barrier-only wave that reports nothing (§4.3).
* **One request per loop item (D3).** The per-item knobs (`version`,
  `virtualenv`, `extra_args`, `state`) are call-level scalars on
  `pip.packages`, so batching would be wrong; one request per item mirrors the
  role's one-task-per-loop-iteration exactly and keeps each item's log line
  distinct.
* **The marker path follows the barrier, not a constant.** The Ubuntu target's
  marker lives at `/usr/lib/python3.<minor>/EXTERNALLY-MANAGED`, and `<minor>`
  varies by release — which is why this role keeps a `fact` barrier that
  `nodejs` (zero barriers) does not.

A host whose `family` is not `"Ubuntu"` returns before its first `yield`: no
requests, no barriers, no report lines — the CentOS entries in a shared
inventory are never contacted.

## 7. Transcript

From `tests/roles/test_pip.py`, against the simulated Ubuntu host (interpreter
seeded at 3.12, marker present) plus a RedHat host the role never touches. The
simulation's own mutators maintain every seam the second pass reads — the apt
store, the pip store, the venv registry and the file table. Fresh host, first
pass:

```text
(root)
  wave 0
    barrier: PythonVersion → {'major': 3, 'minor': 12, 'micro': 3}
  wave 1
    web1: Ensure Pip is installed. [changed: 1 package(s) to install]
    web1: Remove EXTERNALLY-MANAGED. [changed: /usr/lib/python3.12/EXTERNALLY-MANAGED present, will remove]
    web1: Ensure pip package ipaddress is installed. [changed: 1 package(s) to install]
    web1: Ensure pip package colorama is installed. [changed: 1 package(s) to install]
    web1: Ensure virtualenv /srv/app/venv exists. [changed: virtualenv /srv/app/venv missing, will create]
    web1: Ensure pip package six is installed. [changed: 1 package(s) to install]
```

The rerun — the barrier re-runs (it is a read, not a cached plan value) and
every request is a no-op:

```text
(root)
  wave 0
    barrier: PythonVersion → {'major': 3, 'minor': 12, 'micro': 3}
  wave 1
    web1: Ensure Pip is installed. [no change: all packages present]
    web1: Remove EXTERNALLY-MANAGED. [no change: file /usr/lib/python3.12/EXTERNALLY-MANAGED does not exist]
    web1: Ensure pip package ipaddress is installed. [no change: all packages present]
    web1: Ensure pip package colorama is installed. [no change: all packages present]
    web1: Ensure virtualenv /srv/app/venv exists. [no change: virtualenv /srv/app/venv already exists]
    web1: Ensure pip package six is installed. [no change: all packages present]
```

The `six` line runs `$ sudo /srv/app/venv/bin/pip install six` — the virtualenv
item's own interpreter, created by the preceding request (D4). The RedHat host
appears in neither tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | `ansible.builtin.package` becomes `apt.packages` | the package module is picked for the Ubuntu target (the role was multi-OS); `state=present` maps to the operation's default `present=True` |
| D2 | The marker path's `ansible_facts['python']['version']['minor']` becomes a `fact(PythonVersion)` barrier, not a static constant | on Ubuntu the path varies by release (22.04 → `python3.10`, 24.04 → `python3.12`), so the version must be read from the target; the role's `file: state=absent` becomes `files.file(present=False)`, equally idempotent |
| D3 | The `pip_install_packages` loop runs as **one `pip.packages` request per item** rather than batching | the per-item knobs (`version`, `virtualenv`, `extra_args`, `state`) differ across items, and reemote's `pip.packages` takes them as call-level scalars; one request per item mirrors the role exactly and keeps each item's log line distinct |
| D4 | `item.virtualenv` becomes two requests — a `pip.venv` create, then `pip.packages(pip=<venv>/bin/pip)` | Ansible's `pip` module auto-created the virtualenv as a hidden side effect; reemote splits venv creation into its own idempotent operation. `state=absent` items skip the create — the role would not build a tree just to uninstall from it |
| D5 | The `pip3 if pip_package.startswith('python3') else pip` autodetect becomes `derive_executable`, overridden by an explicit `pip_executable` | the role's `defaults/main.yml` Jinja expression restated in Python; a virtualenv item ignores both and names `<venv>/bin/pip`, what the role's `ternary(omit, pip_executable)` implied |
| D6 | `state=forcereinstall` (and any state beyond present/absent/latest) is rejected up front | `pip.packages` has no native forced-reinstall direction; the README lists it only as an option, never a default, and a bad entry raises before any host is contacted |
| D7 | Every request carries `sudo=True` | the role relied on play-level `become`; reemote has no such thing, so each request elevates explicitly — always safe for a root-owned tree, and faithful for a venv outside the login user's home |
| D8 | The family guard is the body's opening `if`, variable checks become pydantic validation, and the marker read is an in-body barrier | wave-model shape changes (§17): an off-target host emits nothing at all; bad values raise before any host is contacted; no gather → snapshot → factory split survives |

## 9. Scoped out

* **RedHat / CentOS branches** (the `repo-epel` dependency the README mentions,
  `python3-pip` from EPEL): a Phase 0 single-OS decision — the target is
  Ubuntu. The `repo_epel` role already ships for RedHat families, so composing
  the two is a follow-up, not a gap.
* **Older-OS `set_fact: pip_package` variants** and the molecule pre_tasks
  (`apt: update_cache`, the CentOS-only `pip_package` overrides): the
  pre_tasks are not part of the role, and modern Ubuntu ships `python3-pip` in
  the main archive — the frozen default needs no repository prerequisite.
* **`state=forcereinstall`**: see D6.
--->
