# docker — Docker CE role

| | |
| --- | --- |
| Role | [`geerlingguy.docker`](https://github.com/geerlingguy/ansible-role-docker) |
| Module | [`reemote.roles.docker`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/docker.py) |
| Runner | [`examples/roles/docker.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/docker.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `docker` role mapping → `DockerVars` |

<!---
| Waves | `5` on a changed host with users (0–4); one less without `users`; the handler wave disappears once converged |
| Outputs | none — the deploy returns `None`; no other role reads docker's results |
| Extra deps | **none** — `daemon.json` renders with `json.dumps`, no template engine |
| Offline test | [`tests/roles/test_docker.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_docker.py) |
| Factory | plain (non-`async`) — the runner calls `docker(v)`; the distribution and group reads are `fact` barriers, and the signing-key fetch is `apt.sources_file`'s own control-side step |

`docker` installs and configures Docker CE on Ubuntu hosts from the official
Docker apt repository: the legacy key/source/package cleanup, the repository
dependencies, the deb822 `.sources` file with its URL-signed keyring, the
edition-derived packages plus the compose plugin, the optional
`/etc/docker/daemon.json` and `docker.service` ExecStart override, the service's
running/enabled state, the `restart docker` handler, and the `docker_users`
group memberships.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with `yield from scoped("docker", docker(v))(host)`
and a run is one `run_waves` pass. The Ansible role's whole task list is that
single deploy, and its `defaults/main.yml` is `DockerVars`.

This is the corpus's **union-of-two-checkpoints** role (design doc §18.11) and
the role where one Ansible behaviour changes *meaning* rather than shape:
`meta: reset_connection`. The stream is:

```python
info = yield fact(LinuxDistribution)              # wave 0 — the repo's Suites
plan = docker_plan(hv, info)
yield from setup(plan)(host)                      # wave 1 — cleanup, deps, repo,
yield from packages(plan)(host)                   #          packages, plugin
packages_report = yield checkpoint("packages done")
yield from configure(plan)(host)                  # wave 2 — daemon.json, dropin, service
configure_report = yield checkpoint("configure done")
notified = changed_hosts(packages_report, names) | changed_hosts(configure_report, names)
if host.name in notified and plan.service_manage:  # wave 3 — restart docker
    yield from service_handler(frozenset({host.name}), ("docker",), action=...)(host)
if plan.users:
    groups = yield fact(Groups)                   # the getent gate, the role's own raise
    yield from users(plan)(host)                  # wave 4 — memberships (append, ensure_home=False)
```

The single handler's notification set **spans both reports** — the package
installs and the daemon configuration both notify `restart docker` — so the two
`changed_hosts` reads are unioned, the php §18.6 shape; a single shared
checkpoint would silently drop one channel. Setup and packages *share* one wave
because no read sits between them: the driver applies each request before
pulling the next, so yield order is apply order.

## 1. What it is, and where it came from

The role's tasks map onto reemote operations and barriers:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `include_tasks setup-Debian.yml` (legacy key/list/file cleanup) | three `files.file(present=False)` + one `apt.packages(present=False)` in `setup` | covered |
| `apt: name=[ca-certificates, python3-debian]` | `apt.packages(packages=DEPENDENCY_PACKAGES)` | covered |
| `deb822_repository: ... signed_by_file` + `get_url` key download | `apt.sources_file(signed_by=<URL>)` — fetches control-side, installs `/etc/apt/keyrings/docker.asc` | covered (issue #17) |
| `apt: update_cache` + `notify: apt update` + `meta: flush_handlers` | `apt.sources_file(update_cache=True)` chains the refresh on a real sources change | deviation (D1) |
| `apt: name={{ docker_packages }} allow_downgrade: true` (two version-split arms) | one `apt.packages(packages, allow_downgrades=True)` (modern arm only) | covered (D6) |
| `apt: name=docker-compose-plugin` | second `apt.packages`, both notify the handler | covered |
| `copy: content={{ docker_daemon_options \| to_nice_json }} dest=/etc/docker/daemon.json` | `render_daemon_json` (stdlib `json.dumps(indent=4, sort_keys=True)`) → `files.install` | covered |
| `replace`/`lineinfile` ExecStart patch of the packaged unit | `systemd.dropin(unit="docker", filename="reemote-exec-start", daemon_reload=True)` | deviation (D2) |
| `systemd: name=docker state={{...}} enabled={{...}}` | `systemd.service(running/restarted/reloaded, enabled)` | covered |
| `handlers/main.yml`: `restart docker` (`when: docker_service_manage`) | in-body `service_handler(frozenset({host.name}), ("docker",), action=...)`, gated on the union + flag | deviation (§18.11) |
| `getent: ansible_facts getent.docker` gate before users | `yield fact(Groups)` + the role's own `ValueError` on a missing group | barrier (D4) |
| `user: name={{ item }} groups=docker append=yes` (`loop: docker_users`) | one `server.user(groups=("docker",), append=True, ensure_home=False)` per user | covered (D5) |
| `meta: reset_connection` (so new group members apply mid-play) | **meaning changes** — see §6; the one unreachable behaviour | deviation |
| `ignore_errors: ansible_check_mode` | **dropped** — `dry` never applies | deviation (D3) |
| the standalone `docker-compose` binary (`get_url` + `mode=0755`) | **scoped out** — a truthy `install_compose` raises | out of scope |

No library gap blocked this migration, so none was filed: issues #14
(`update_cache` chains on removals too) and #17 (the URL `signed_by` form) were
the two prerequisites and both are closed. The old example's
`build_setup(codenames)` two-phase factory and its `by_action` handler
partition are gone — barriers and the per-host body absorbed them.

## 2. Install

```bash
pip install reemote           # no extra: no Jinja2, no PyYAML
```

This is the templating role that needs **no** `reemote[templates]` extra: its
only rendered file is `daemon.json`, and Ansible's `to_nice_json` filter is
byte-for-byte the standard library's `json.dumps(indent=4, sort_keys=True)` (no
trailing newline). SSH access with the key named in each `connection`, and
passwordless sudo (or a `privilege` section): every request carries `sudo=True`.

## 3. Variables

`DockerVars` is a frozen pydantic model (`extra="forbid"`) whose fields carry
bare names; the role name passed to `resolve_vars` binds the entry's `docker`
role mapping, so an inventory written for the role needs no key gymnastics.
The three derived fields (`packages`, `apt_repo_url`, `apt_gpg_key`) default to
`None` — "compute it from `edition` / `repo_url`", exactly as the role's own
`defaults/main.yml` templates them. Precedence is
`model defaults < inventory role mapping < overrides`.

--->

::: reemote.roles.docker
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - docker

**Variables:**

### Repository

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `add_repo` | `bool` | `true` — when false, the sources file and keyring are **removed** | `docker_add_repo` |
| `repo_url` | `str` | `https://download.docker.com/linux` — base for both derived URLs | `docker_repo_url` |
| `apt_repo_url` | `str \| None` | `null` → `{repo_url}/ubuntu`; a mirror changes the URI only | `docker_apt_repo_url` |
| `apt_gpg_key` | `str \| None` | `null` → `{repo_url}/ubuntu/gpg` — follows the **upstream**, not a mirror | `docker_apt_gpg_key` |
| `apt_release_channel` | `str` | `stable` | `docker_apt_release_channel` |
| `apt_filename` | `str` | `docker` — names both the `.sources` file and the keyring | `docker_apt_filename` |

### Packages and compose

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `edition` | `str` | `ce` — the package-name prefix | `docker_edition` |
| `packages` | `tuple[str, …] \| None` | `null` → the edition's five packages | `docker_packages` |
| `packages_state` | `Literal["present","latest","absent"]` | `present` | `docker_packages_state` |
| `obsolete_packages` | `tuple[str, …]` | the nine distro Docker packages the repo replaces | `docker_obsolete_packages` |
| `install_compose_plugin` | `bool` | `true` | `docker_install_compose_plugin` |
| `compose_package` / `compose_package_state` | `str` / `Literal[…]` | `docker-compose-plugin` / `present` | `docker_compose_package[_state]` |
| `install_compose` | `bool` | `false` — a truthy value **raises** (scoped out) | `docker_install_compose` |

### Service and handler

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `service_manage` | `bool` | `true` — gates the service task **and** the handler | `docker_service_manage` |
| `service_state` | `Literal["started","stopped","restarted","reloaded"]` | `started` | `docker_service_state` |
| `service_enabled` | `bool` | `true` | `docker_service_enabled` |
| `service_start_command` | `str` | `""` — when set, the ExecStart drop-in (D2) | `docker_service_start_command` |
| `restart_handler_state` | `Literal["restarted","reloaded","started"]` | `restarted` | `docker_restart_handler_state` |

### Users and daemon options

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `users` | `tuple[str, …]` | `()` — appended to the `docker` group behind the getent gate | `docker_users` |
| `daemon_options` | `dict[str, Any]` | `{}` — non-empty renders `/etc/docker/daemon.json` | `docker_daemon_options` |

<!---

`daemon_options` is the one mapping on the model, and the one value that never
reaches `op()` unrendered: `render_daemon_json` turns it into a `str` before
the plan freezes, so the hashability rule is untouched. Validation is
fail-fast: the three state enums are `Literal`s, `install_compose` truthy
raises (the standalone binary install is scoped out), `repo_url` must not end
in a slash (the derived URLs concatenate a segment directly), names are
non-empty, and every `users`/`packages`/`obsolete_packages` entry is a
non-empty string. The old example raised `TypeError` for several of these
gates; here they are all pydantic `ValidationError` — a `ValueError` — before
any host is contacted.

## 5. Run it

The role is library code; `examples/roles/docker.py` is the runnable artifact —
a standalone program with its own `main()`, no shared runner. It loads and
validates `inventory.json` from the current working directory, resolves the
role's variables (the defaults overlaid with the entry's `docker` role
mapping, then the call-site `overrides`), builds the deploy from the `docker`
factory and drives it with `run_waves` — a `dry=True` one-wave preview, then the
real apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import DockerVars, docker

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(
        DockerVars, "docker", inventory,
        overrides={"users": ("supervisor",), "daemon_options": {...}},
    )
    deploy = docker(vars_set)                                       # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)    # one-wave preview
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)              # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/docker.py
```

The call-site `DOCKER_OVERRIDES` set `users=("supervisor",)` and two
`daemon_options`, so the demonstration exercises the daemon.json write, the
handler it notifies, and the group-membership tail — not just the package
install. The `dry=True` pass is a one-wave preview: docker's first item is the
`LinuxDistribution` `fact` barrier, so the run parks there (`barrier:
LinuxDistribution → unknown until run`), reads no state and applies nothing.

**Run the program twice.** The second run must report no change and fire no
handler: the legacy artifacts are gone, the repo and keyring are in place, the
packages are installed, `daemon.json` is byte-identical, the service is running
and enabled, and the user is already in the group — so neither checkpoint
notifies and the handler wave is empty. That is the migration's idempotence
claim, and the offline suite asserts it.

## 6. What it does

One deploy. Each barrier closes its own wave, and a wave with no records is not
appended — so the shape below is what the test pins:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `fact(LinuxDistribution)` | `release_meta.VERSION_CODENAME` feeds the repository's `Suites`; the whole setup wave is a pure function of it |
| 1 | `setup` (three legacy removals, obsolete purge, dependencies, the deb822 repo with its URL-signed keyring) **then** `packages` (edition five + compose plugin) — closed by `checkpoint("packages done")` | no read sits between the two deploys, so they share one wave in yield order; the role ran them as separate plays' tasks, which was a two-phase artifact, not a dependency |
| 2 | `configure`: `/etc/docker` + `daemon.json` (when options set), the ExecStart drop-in (when a command is set), the service state — closed by `checkpoint("configure done")` | the daemon.json and drop-in writes both notify the handler |
| 3 | `service_handler("Restart docker (handler)")` — only where **this host** sits in the union of the two reports and `service_manage` is on; then `fact(Groups)` (only with users) | the role's second `flush_handlers`, which runs *before* the user tasks; the getent gate closes the handler wave |
| 4 | `users`: one `server.user(append=True, ensure_home=False)` per listed user | only after the gate passed; a missing `docker` group raises instead |

Four rules are load-bearing:

* **Two checkpoints, unioned.** One handler, two notifying stages: the
  notification set is `changed_hosts(packages_report, names) |
  changed_hosts(configure_report, names)`. Folding both stages under one
  checkpoint is fine here; folding the *reads* into only the second report is
  not — a packages-only change (the test rigs exactly that case) would notify
  nobody and silently skip the restart.
* **The per-host body deletes two artifacts.** The old example needed a
  whole-inventory `names` union (one `compose`d handler deploy served every
  host) and a `by_action` partition (hosts could want different restart verbs);
  a per-host body reads its own `restart_handler_state` through
  `HANDLER_ACTIONS` and names only `host.name`.
* **`meta: reset_connection` changes meaning — this is the corpus's one
  unreachable behaviour.** Ansible dropped the open SSH connection after the
  `docker_users` tasks so the *next* task's session carried the new group; the
  old script could treat it as a no-op because every `run()` built fresh
  hosts. Under `run_waves` the connection stays open for the whole run (§13),
  so the membership takes effect on the **next pass** — no barrier can emulate
  a reconnect mid-run, because the driver owns the connection. A program that
  needs the membership *during* the run must drive two `run_waves` passes.
  Everything else in this migration changes shape; this is the only change of
  reachability, and users of the demo host should log out and back in after
  the first pass that adds them.
* **The getent gate is the role's raise, not the driver's.** A barrier result
  is data: `fact(Groups)` returns names, and the body itself raises
  `ValueError` when `docker` is missing while `docker_users` is set — the
  faithful analogue of the role's `getent.docker` failure mid-play.

A host whose `family` is not `"Ubuntu"` returns before its first `yield`: no
requests, no barriers, no report lines — Alpine and CentOS entries in a shared
inventory are never contacted.

## 7. Transcript

From `tests/roles/test_docker.py`, against the simulated Ubuntu 24.04 host
(`users: ["alice"]`, `daemon_options` set in the `docker` role mapping) plus a
RedHat host the role never touches. `os_groups` is seeded to the intended
post-install state (the simulated apt install records package names only, never the group the
real postinst creates) and the key fetch is patched offline. Fresh host, first
pass:

```text
(root)
  wave 0
    barrier: LinuxDistribution → {'name': 'Ubuntu', 'family': 'Debian', 'major': '24', ...}
  wave 1
    ubuntu: Remove Docker key from trusted.gpg.d [changed: ...]
    ubuntu: Remove obsolete Docker packages [changed: 2 package(s) to remove]
    ubuntu: Install Docker repository dependencies [changed: 2 package(s) to install]
    ubuntu: Add or remove Docker repository [changed: wrote /etc/apt/sources.list.d/docker.sources]
    ubuntu: Install Docker packages [changed: 5 package(s) to install]
    ubuntu: Install docker-compose-plugin [changed: 1 package(s) to install]
    barrier: packages done → 1 host notified
  wave 2
    ubuntu: Ensure /etc/docker directory exists
    ubuntu: Configure Docker daemon options [changed: content /etc/docker/daemon.json is absent, will install]
    ubuntu: Ensure Docker is started and enabled at boot [changed: docker.service not running, will start; will enable]
    barrier: configure done → 1 host notified
  wave 3
    ubuntu: Restart docker (handler) [changed]
    barrier: Groups → ['sudo', 'wheel', 'docker']
  wave 4
    ubuntu: Add alice to the docker group [changed]
```

The rerun — every barrier re-runs (they are reads, not cached plan values),
both checkpoints find nothing, and the **handler wave disappears entirely**
while the `Groups` gate and the already-satisfied membership stay:

```text
(root)
  wave 0
    barrier: LinuxDistribution → {...}
  wave 1
    ubuntu: Install Docker packages [no change: all packages present]
    ...
    barrier: packages done → 0 hosts notified
  wave 2
    ubuntu: Configure Docker daemon options [no change: content already up to date]
    ...
    barrier: configure done → 0 hosts notified
  wave 3
    barrier: Groups → ['sudo', 'wheel', 'docker', ...]
  wave 4
    ubuntu: Add alice to the docker group [no change: already in docker]
```

The RedHat host appears in neither tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | The `apt update` handler + immediate `flush_handlers` become `apt.sources_file(update_cache=True)` | the refresh chains onto a *real* sources change (write **or** removal, issue #14) — same fire-on-change semantics, one fewer barrier |
| D2 | The ExecStart override is a **drop-in**, not an in-place patch of the packaged unit | `docker.service.d/reemote-exec-start.conf` with the empty `ExecStart=` line clears the packaged value per systemd override semantics; the unit file survives package upgrades, and `daemon_reload=True` reproduces the conditional reload |
| D3 | `ignore_errors: ansible_check_mode` is **dropped** | reemote's `dry` never applies, so there is no check-mode failure to tolerate |
| D4 | The `at_least_one_user_to_modify` pre-check is **subsumed**, the getent failure **kept** | `server.user(append=True)` reports no change for an existing membership; the missing-group case still raises, at the `fact(Groups)` barrier |
| D5 | `ensure_home=False` on the user requests | reemote's default also converges home-directory ownership; the role's task is a pure membership append |
| D6 | The two version-split install arms collapse to the modern (`allow_downgrade`) one | reemote's `allow_downgrades` carries the same `--allow-downgrades` semantics |
| D7 | `meta: reset_connection` becomes **next-pass semantics** | the driver owns connections for the whole run; this is the corpus's one behaviour that is unreachable rather than merely different — see §6 |
| D8 | The family guard is the **body's opening `if`**; variable gates become **pydantic validation** | off-target hosts emit nothing at all (the old example logged every request as skipped); bad values — including the old `TypeError` gates — all raise `ValidationError` before any host is contacted |

## 9. Scoped out

* **Non-Ubuntu families.** The role's RedHat/Suse branches (yum vars, the
  `docker-ce-stable` repo file) are not migrated; `include_vars` dispatch
  freezes to the Debian constants.
* **The standalone `docker-compose` binary.** `docker_install_compose: true`
  downloads a *binary* release artifact, outside the text-only transfer
  family; a truthy value raises rather than being silently dropped. Use the
  compose **plugin** package (the default).
* **Rootless Docker and docker.socket management.** The role's defaults do not
  manage them; neither does this migration.
* **Mid-run group membership.** No `reset_connection` analogue exists under one
  `run_waves` pass (D7); needing the membership within the same program means
  two passes, which is a caller-side decision this role documents rather than
  emulates.

--->
