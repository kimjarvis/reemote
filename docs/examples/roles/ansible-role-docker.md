# Docker Ansible role → reemote

This example migrates the [geerlingguy.docker](https://github.com/geerlingguy/ansible-role-docker)
Ansible role to a Python program built on the reemote library
([`ansible-role-docker.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/ansible-role-docker/ansible-role-docker.py)).

The role installs and configures Docker CE from the official Docker
repository: the legacy key/source/package cleanup, the repository
dependencies, the Docker signing key and repository definition, the Docker
packages (engine, CLI, rootless extras, containerd, buildx and compose
plugins), the optional `/etc/docker/daemon.json` and `docker.service`
ExecStart override, the service's running/enabled state, a single
`restart docker` handler, and the `docker_users` group memberships.

**This migration targets Ubuntu only.** Hosts in the `"Ubuntu"` OS family execute the deploy; every request carries the static `when` guard
`is_ubuntu`, so the other hosts in the shared `inventory.json` (Alpine, RedHat) are skipped without being contacted. The role's `include_vars` OS
dispatch collapses to frozen Debian constants, and the RedHat/Suse branches
plus the opt-in standalone docker-compose binary install are scoped out
(see §8).

The migration is complete: every task on the Ubuntu default path maps to a
covered operation or a documented recipe, and no default-path feature was
dropped.

## 1. Prerequisites

### 1.1 Packages that must be loaded before executing

| Package | Where | Why |
| --- | --- | --- |
| `reemote` | control machine | the library itself; a repo checkout installed by `uv sync` provides it |
| `ca-certificates`, `python3-debian` | target host | installed **by the program itself** via `apt.packages` — nothing to pre-load |
| Docker packages | target host | installed **by the program itself** from the Docker apt repository it configures |

**No extra control-side package is needed.** Unlike the DataDog migration
(PyYAML for `to_nice_yaml`) or the Apache migration (Jinja2 for
`vhosts.conf.j2`), this role's only filter is `to_nice_json`, which maps
byte-for-byte to the standard library's
`json.dumps(options, indent=4, sort_keys=True)` — so the program runs with
plain `uv run python …`, no `--with`.

### 1.2 Control machine

- [uv](https://docs.astral.sh/uv/) and Python ≥ 3.13 (the project's
  `requires-python`).
- This repository, with dependencies synced: `uv sync`.
- Internet egress to `download.docker.com`: the signing key is fetched
  **control-side** by `apt.sources_file`'s URL `signed_by` form (the target
  never needs `curl`/`wget`). The key must be ASCII-armored text — a binary
  key URL raises `ValueError` — and is fetched once per phase with no
  checksum pin.

### 1.3 Target host

- Ubuntu with systemd, reachable over SSH as a user that can `sudo` (the
  inventory's `privilege` section carries any run-as user/password — never
  the program).
- `apt` ≥ 1.4 (every supported Ubuntu release): `Signed-By` accepts the
  ASCII-armored `.asc` keyring `apt.sources_file` installs.

### 1.4 Inventory: what you must add

The program loads the project-root `inventory.json`. A target entry needs
`"group": "ubuntu"`; every role variable rides beside `host`/`connection` as
an extra key — reemote's `host_vars` mechanism (`host.extra`):

```json
{
  "host": "ubuntu",
  "family": "Ubuntu",
  "connection": {
    "address": "192.168.0.181",
    "username": "supervisor",
    "key_path": "~/.ssh/id_ed25519"
  },
  "group": "ubuntu",
  "tags": ["ubuntu_host", "apt_package_manager"],
  "docker_users": ["supervisor"],
  "docker_daemon_options": {"log-driver": "json-file", "log-opts": {"max-size": "10m"}}
}
```

With no extra keys the role defaults apply (§3) — the entry above only
illustrates the shape. The shared `inventory.json` already carries a
matching `ubuntu` entry.

## 2. Running it against `inventory.json` with uv

From the repository root:

```bash
uv sync                                        # once: install reemote + dev deps
uv run python examples/roles/ansible-role-docker/ansible-role-docker.py
```

The program:

1. loads and validates `inventory.json`, prints the hosts, and names the
   hosts the `is_ubuntu` guard skips;
2. resolves each target's role variables (defaults overlaid with the entry's
   extra keys) and fails fast on an invalid value — before any host is
   contacted;
3. gathers `LinuxDistribution` for the release codename (`Suites`), runs
   **deploy 1** (cleanup + dependencies + signing key + deb822 repository,
   with the `apt update` handler folded into `update_cache=True`),
   **deploy 2** (Docker + compose-plugin packages) and **deploy 3**
   (daemon.json, ExecStart drop-in, service state);
4. reads `changed_hosts` over the notifying request names and runs
   **deploy 4** — the `restart docker` handler, the role's second
   `flush_handlers` barrier;
5. gathers `Groups` (the role's `getent` gate) and, when `docker_users` is
   set, runs **deploy 5** (group memberships);
6. prints each deploy twice: the `dry=True` plan (Ansible's `--check` as a
   return value — nothing is mutated) followed by the real apply log.

**Run the whole program a second time** to see the idempotence claim: the
second pass reports no changes and queues no handler.

### Offline verification (no hosts, no approval needed)

```bash
uv run pytest tests/test_roles_ansible_role_docker.py -q
```

The test replays the program's whole orchestration against the test
framework's `SimulatedHost` and proves plan → apply → **second apply is a
no-op** (§7).

## 3. Role variables

Every `defaults/main.yml` variable on the Ubuntu path is honoured under its
original `docker_`-prefixed name, read from the inventory entry's extra
keys. The `vars/Debian.yml` obsolete-package list is a frozen constant
(single-OS scope).

| Variable | Default | Meaning |
| --- | --- | --- |
| `docker_edition` | `"ce"` | package-name prefix (`docker-ce`, `docker-ce-cli`, …) |
| `docker_packages` | `null` → derived from the edition | package list; `null` reproduces the role's templated default |
| `docker_packages_state` | `"present"` | `present`/`latest`/`absent` for the Docker packages |
| `docker_obsolete_packages` | the nine distro packages | removed before the official packages install |
| `docker_service_manage` | `true` | manage the service state and fire the restart handler |
| `docker_service_state` | `"started"` | `started`/`stopped`/`restarted`/`reloaded` |
| `docker_service_enabled` | `true` | enable the service on boot |
| `docker_service_start_command` | `""` | when set, override the unit's `ExecStart` (drop-in, §6 D2) |
| `docker_restart_handler_state` | `"restarted"` | the handler's verb: `restarted`/`reloaded`/`started` |
| `docker_install_compose_plugin` | `true` | install the compose plugin package |
| `docker_compose_package` | `"docker-compose-plugin"` | the plugin package name |
| `docker_compose_package_state` | `"present"` | `present`/`latest`/`absent` for the plugin |
| `docker_install_compose` | `false` | **scoped out** — a truthy value raises (§8) |
| `docker_add_repo` | `true` | `false` removes the `.sources` file and the managed key |
| `docker_repo_url` | `"https://download.docker.com/linux"` | base for the derived repo/key URLs |
| `docker_apt_release_channel` | `"stable"` | the repository component (`stable`/`nightly`/…) |
| `docker_apt_repo_url` | `null` → `<repo_url>/ubuntu` | mirror override for the repository URI |
| `docker_apt_gpg_key` | `null` → `<repo_url>/ubuntu/gpg` | signing-key URL (follows the upstream repo, not the mirror — as in the role) |
| `docker_apt_filename` | `"docker"` | base name of the `.sources` file and the keyring |
| `docker_users` | `[]` | users appended to the `docker` group |
| `docker_daemon_options` | `{}` | rendered to `/etc/docker/daemon.json` with `to_nice_json` semantics |

Invalid values (a bad enum, a non-list package/user value, a non-mapping
`docker_daemon_options`) raise before any host is contacted.

## 4. Program shape: five deploys and their barriers

reemote runs a deploy twice (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence. The role
feeds `ansible_facts.distribution_release` into the repository's `Suites`
and gates the user tasks on a `getent` read — mutable reads a single deploy
may not branch on. Each becomes a **gather → snapshot → factory** step (or a
gate) between complete runs:

```
main()
 ├─ load + validate inventory.json; resolve role_settings per target
 ├─ gather_fact(LinuxDistribution)    release_meta.VERSION_CODENAME → codenames
 │                                    (hosts closed manually afterwards)
 ├─ deploy 1: build_setup(codenames)  legacy key/list/package cleanup,
 │                                    ca-certificates + python3-debian,
 │                                    apt.sources_file(signed_by=key URL,
 │                                    update_cache=True)
 ├─ deploy 2: deploy_packages         apt.packages(docker…, allow_downgrades=True)
 │                                    apt.packages(docker-compose-plugin)
 ├─ deploy 3: deploy_configure        /etc/docker + daemon.json (when options),
 │                                    systemd.dropin ExecStart (when command),
 │                                    systemd.service (when manage)
 ├─ changed_hosts(package + configure reports, notifying names)
 ├─ deploy 4: service_handler         restart docker, partitioned by
 │                                    docker_restart_handler_state
 │                                    (the role's flush_handlers barrier)
 ├─ gather_fact(Groups)               the getent gate: docker group must exist
 └─ deploy 5: deploy_users            server.user(groups=("docker",), append=True)
                                      per docker_user (when any)
```

The barriers are load-bearing: the codename is frozen **before** the setup
deploy so its request sequence is a pure function of frozen input; the
handler reads the two notifying deploys' apply reports and fires **before**
the user tasks, exactly as the role's `meta: flush_handlers` orders the
play; the `Groups` gather sits **after** the package install, when the
docker-ce postinst has created the group. The role's first flush (the
`apt update` handler) needs no barrier at all: `update_cache=True` chains
the index refresh onto the sources change itself (§6 D1).

## 5. Coverage table

Status vocabulary: **covered** (direct operation), **recipe** (documented
shape change).

### `tasks/setup-Debian.yml`

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 1 | `file: path=/etc/apt/trusted.gpg.d/docker.asc state=absent` | `files.file(present=False)` | covered |
| 2 | `file: path=…/download_docker_com_linux_ubuntu.list state=absent` | `files.file(present=False)` | covered |
| 3 | `package: name={{ docker_obsolete_packages }} state=absent` | `apt.packages(present=False)` | covered |
| 4 | `file: path=…/docker.list state=absent` | `files.file(present=False)` | covered |
| 5 | `apt: name=[ca-certificates, python3-debian] state=present` | `apt.packages` | covered |
| 6 | `deb822_repository: … signed_by={{ docker_apt_gpg_key }} (a URL)` | `apt.sources_file(signed_by=<URL>)` — fetches and installs `/etc/apt/keyrings/docker.asc` itself | covered |
| 7 | `suites: {{ ansible_facts.distribution_release }}` | `LinuxDistribution` gather → `release_meta["VERSION_CODENAME"]` snapshot → factory | recipe |
| 8 | `state: present/absent` (`docker_add_repo`) | `apt.sources_file(present=…)` — the URL form removes the managed keyring when absent | covered |
| 9 | `notify: apt update` + `meta: flush_handlers` | `apt.sources_file(update_cache=True)` — refresh chained on a real change only | covered |

### `tasks/main.yml`

| # | Ansible task | reemote target | Status |
| --- | --- | --- | --- |
| 10 | `include_vars: first_found` → `vars/Debian.yml` | frozen `DEBIAN_OBSOLETE_PACKAGES` constant (single-OS scope) | recipe |
| 11 | `package: name={{ docker_packages }} allow_downgrade=true` (the ≥ 2.12 Debian task) | `apt.packages(allow_downgrades=True)` | covered |
| 12 | `package: name={{ docker_compose_package }} allow_downgrade=true` | `apt.packages(allow_downgrades=True)` | covered |
| 13 | `file: path=/etc/docker state=directory mode=0755` | `files.directory(mode="0755")` | covered |
| 14 | `copy: content={{ docker_daemon_options \| to_nice_json }} dest=/etc/docker/daemon.json` | `json.dumps(indent=4, sort_keys=True)` + `files.install` | covered |
| 15 | `systemd_service` register + `replace: FragmentPath ^ExecStart=.*$` | `systemd.dropin` ExecStart override (§6 D2) | recipe |
| 16 | `service: daemon_reload=true` when the patch changed | `dropin(daemon_reload=True)` — chained on a real content change only | covered |
| 17 | `service: name=docker state=… enabled=…` when `docker_service_manage` | `systemd.service(running/restarted/reloaded, enabled)` | covered |
| 18 | `meta: flush_handlers` | deploy barrier + handler deploy (§4) | recipe |
| 19 | `include_tasks: docker-compose.yml` when `docker_install_compose` | — | scoped out (§8) |
| 20 | `getent: database=group key=docker` | `Groups` gather between runs; missing group → fail-fast | recipe |
| 21 | `set_fact: at_least_one_user_to_modify` | subsumed by `server.user(append=True)` membership idempotence (§6 D4) | deviation |
| 22 | `user: groups=docker append=true` loop (`docker-users.yml`) | `server.user(groups=("docker",), append=True)` per user | covered |
| 23 | `meta: reset_connection` | every `run()` builds fresh hosts — structural no-op | recipe |

### `handlers/main.yml`

| # | Ansible handler | reemote target | Status |
| --- | --- | --- | --- |
| 24 | `restart docker` (`state={{ docker_restart_handler_state }}`, when `docker_service_manage`) | `service_handler(notified ∩ manage, ("docker",), action=…)` | recipe |
| 25 | `apt update` (`update_cache: true`) | folded into `apt.sources_file(update_cache=True)` (row 9) | covered |

## 6. Deliberate deviations

- **D1 — the `apt update` handler is folded into the sources request.**
  Ansible notifies a separate handler and flushes it immediately; reemote's
  `apt.sources_file(update_cache=True)` chains the same `apt-get update`
  onto a real sources change (write *or* removal) in the same request. Same
  fire-on-change semantics, one fewer barrier.
- **D2 — ExecStart override is a drop-in, not an in-place patch.** The role
  `replace`s `^ExecStart=.*$` in the unit's packaged FragmentPath; this
  program writes `/etc/systemd/system/docker.service.d/reemote-exec-start.conf`
  with `[Service] / ExecStart= / ExecStart=<command>`. The empty assignment
  clears the packaged value (systemd override semantics), the packaged unit
  stays untouched (an in-place edit is reverted by the next package
  upgrade), and `daemon_reload=True` reproduces the conditional reload.
  The role's block also assumes the unit exists (its status query would
  fail otherwise); the drop-in write does not need the unit to exist.
- **D3 — `ignore_errors: {{ ansible_check_mode }}` is dropped.** It only
  relaxes errors in Ansible's check mode; reemote's `dry=True` preview stops
  after prepare and never applies, so there is no error to ignore.
- **D4 — the `set_fact` membership pre-check is subsumed.** The role computes
  `at_least_one_user_to_modify` from the getent member list (with a
  substring-match quirk) to decide whether to include the user tasks;
  `server.user(append=True)` already adds only missing memberships and
  reports no change for a user already in the group, so the program yields
  one request per listed user and converges identically. The getent
  *failure* on a missing docker group is reproduced as a fail-fast gate.
- **D5 — `ensure_home=False` on the user requests.** The role's task is a
  pure group-membership append; reemote's default would also converge
  home-directory ownership, which the task does not ask for.
- **D6 — `allow_downgrade` → `allow_downgrades`.** Ansible's singular flag
  is reemote's plural keyword; same `--allow-downgrades` semantics. The two
  version-split install tasks collapse to the modern (≥ 2.12) one.

## 7. Offline verification

`tests/test_roles_ansible_role_docker.py` imports the example by path and
replays its orchestration barrier-for-barrier against persistent
`SimulatedHost` objects: the signing-key fetch is answered by monkeypatching
`apt._fetch_url_bytes` (the sources operation's documented offline seam),
the codename comes from the `linux_distribution` seam, and one simulation
bridge is applied between passes — a simulated apt install records package
names only, so the docker group the real docker-ce postinst creates is
appended to `os_groups` by hand. The load-bearing assertion: the second pass
reports `changed=False` everywhere and queues no handler.

## 8. Scoped out

- **RedHat / Suse branches** (`setup-RedHat.yml`, `setup-Suse.yml`,
  `docker_yum_*`): a Phase 0 single-OS decision — the target is Ubuntu.
- **The standalone docker-compose binary** (`docker_install_compose`,
  default `false`): an opt-in branch whose `get_url` downloads a *binary*
  release artifact — outside the text-only `files.download` and the
  default-path scope. A truthy override raises a clear error naming the
  compose plugin as the supported alternative.