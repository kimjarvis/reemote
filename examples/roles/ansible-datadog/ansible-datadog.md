# DataDog Ansible role → reemote

| | |
| --- | --- |
| Role | [`datadog.datadog`](https://github.com/DataDog/ansible-datadog) 5.6.0 |
| Program | [`ansible-datadog.py`](ansible-datadog.py) |
| Target | **Ubuntu** — hosts in the inventory group `"ubuntu"` |
| Inventory | [`inventory.json`](../../../inventory.json) at the project root |
| Scope | the role's Debian/Ubuntu **default path** (see [§9](#9-scoped-out)) |
| Offline test | [`tests/test_roles_ansible_datadog.py`](../../../tests/test_roles_ansible_datadog.py) |

This program installs and configures the Datadog Agent the way the Ansible role
does: the apt signing keys and repository, the agent package, `/etc/datadog-agent`
configuration, the per-check configs under `conf.d`, the custom Python checks under
`checks.d`, the `install_info` stamp, the systemd units, and the role's two restart
handlers. It is a reemote **deploy program**, not a CLI wrapper — reemote is a
library, so the role's task graph becomes an explicit sequence of deploys with
fact gathers between them.

Every role variable is read from the inventory entry's extra keys (reemote's
`host_vars` mechanism), so `datadog_api_key` and friends live beside
`host`/`connection` in `inventory.json`.

---

## 1. Prerequisites

### 1.1 Packages that must be loaded before executing

| Package | Why | How it is supplied |
| --- | --- | --- |
| **reemote** | the library itself | `uv run` resolves the project environment (reemote is built from this source tree). `uv sync` if you want it prepared up front. |
| **PyYAML** (`pyyaml>=6.0.2`) | renders the role's `to_nice_yaml` templates (`datadog.yaml`, `checks.yaml`, `system-probe.yaml`, `security-agent.yaml`) | **`--with pyyaml` on the run command.** Already in the project's `dev` dependency group, so the offline test suite has it without the flag. |
| Python ≥ 3.13 | `requires-python` in `pyproject.toml` | uv picks the project's interpreter (`.venv`). |

reemote's own runtime dependencies — `asyncssh`, `distro`, `pydantic` — are
installed automatically. **PyYAML is deliberately *not* a reemote runtime
dependency**: the library has no template engine and no YAML emitter, so a program
that needs Ansible's `to_nice_yaml` must bring its own. `ansible-datadog.py` imports
`yaml` *inside* `to_nice_yaml()`, never at module scope, so importing the module
(the offline test does) never requires it.

Nothing else is needed on the control machine: no Jinja2, no Ansible, no `gh`.

### 1.2 Control machine

- **Outbound HTTPS to `s3.amazonaws.com`.** `fetch_keys()` downloads the five
  Datadog signing keys control-side (`urllib`, 60 s timeout each) and keeps them
  as five separate armored bodies, one per import. This happens *before* any host
  is contacted, so a network failure here aborts the run with no target touched.
- **SSH access** to each `ubuntu` host with the key named in its `connection`
  (`~/.ssh/id_ed25519` for the shipped `ubuntu` entry).

### 1.3 Target host

- Ubuntu with `apt` and `systemd`.
- **Elevation.** Every request carries `sudo=True`. The login user needs
  passwordless sudo (`NOPASSWD`), or the inventory entry must supply a password —
  see [§1.4](#14-inventory-what-you-must-add).
- Outbound HTTPS to `apt.datadoghq.com` (the agent repository).
- The `dd-agent` user and group are created by the agent package; the program
  relies on that (it never creates them itself, exactly as the role does).

### 1.4 Inventory: what you must add

**The shipped `inventory.json` has no `datadog_api_key`.** The role fails on a
missing key when `datadog_manage_config` is true, and this program reproduces that
gate *before contacting any host*:

```
ERROR: ubuntu: datadog_api_key is mandatory when using managed config. Add it to the host's inventory entry.
```

Add the key (and any other role variables) as extra keys on the `ubuntu` entry:

```json
{
  "host": "ubuntu",
  "connection": {
    "address": "192.168.0.221",
    "username": "kjarvis",
    "key_path": "~/.ssh/id_ed25519"
  },
  "group": "ubuntu",
  "tags": ["ubuntu_host", "apt_package_manager"],
  "datadog_api_key": "YOUR_DATADOG_API_KEY",
  "datadog_site": "datadoghq.eu",
  "datadog_checks": {
    "disk": { "instances": [{ "use_mount": true }] }
  },
  "datadog_disable_untracked_checks": true,
  "datadog_disable_default_checks": true,
  "datadog_disable_example_checks": true
}
```

If sudo is not passwordless, add a `privilege` section to the same entry —
privilege settings come from the inventory, never from the program:

```json
"privilege": { "sudo_password": "…" }
```

The other three entries (`alpine`, `web1`, `web2`) need no changes. They are not in
the `ubuntu` group, so `when=is_ubuntu` skips them and **they are never contacted** —
a skipped request is not materialized at all.

> The API key is written to `/etc/datadog-agent/datadog.yaml` (mode `0640`) and
> appears in the rendered plan/log. Treat `inventory.json` as a secret-bearing file.

---

## 2. Running it against `inventory.json` with uv

From the project root:

```bash
uv run --with pyyaml python examples/roles/ansible-datadog/ansible-datadog.py
```

`uv run` builds/refreshes the project environment (reemote from source) and
`--with pyyaml` adds the YAML emitter for this invocation only. The program reads
`inventory.json` from the project root by path resolution
(`Path(__file__).resolve().parents[3]`), so it works from any working directory as
long as the file lives in the repository.

What you get, per deploy:

```
== Inventory loaded from /home/kim/reemote/inventory.json ==
alpine, web1, web2, ubuntu

== Skipped (when guard is_ubuntu): alpine, web1, web2 ==
== Fetching the Datadog apt signing keys (control-side) ==
5 keys, 12345 armored bytes
== Pre-state snapshot ==
ubuntu: 812 installed packages, 6 apt sources, 1 legacy repo file(s), 940 systemd units

== Deploy 1: legacy installer cleanup -- plan (prepare phase, --dry) ==
…format_plan…
== Deploy 1: legacy installer cleanup -- run log (apply phase) ==
…format_log…
…
== Handler notification set ==
restart datadog-agent-sysprobe: (none)
restart datadog-agent: ['ubuntu']
== Done: 16 changed request(s) across 7 deploys ==
```

Each deploy is **previewed then applied** in the same invocation: `run(..., dry=True)`
stops after the prepare phase (Ansible's `--check` as a return value) and prints
`format_plan`, then the real `run` prints `format_log`. There is no dry-run-only
flag — for a rehearsal that touches nothing, use the offline test ([§8](#8-offline-verification)).

**Run it twice.** The second invocation must report `0 changed request(s)` and an
empty handler notification set. That is the migration's idempotence claim, and it
is what the remaining gap workarounds exist to preserve.

Failures are typed and printed to stderr with exit status 1 — no traceback:

| Error | Meaning |
| --- | --- |
| `OSError` / `JSONDecodeError` | `inventory.json` missing, unreadable or not JSON |
| `ValueError` | a role variable is invalid (missing API key, major/version mismatch, a scoped-out override) |
| `TypeError` | `datadog_checks` is not a mapping (`sanitize-checks.yml`'s assertion) |
| `InventoryError` | the document parsed but violates the inventory schema (names the entry index) |
| `HostConnectionError` | SSH could not be established |
| `PrivilegeError` | sudo rejected the credential |
| `CommandError` | a command exited non-zero (fail-fast, names host + command + stderr) |

### Offline verification (no hosts, no approval needed)

```bash
uv run pytest tests/test_roles_ansible_datadog.py -q   # 4 tests: plan → apply → no-op re-apply
uv run pytest tests/ -q                                # whole suite
uv run ruff check .
```

---

## 3. Role variables

`role_settings(host)` freezes `ROLE_DEFAULTS ⊕ host.extra`. These are the variables
this migration honours:

| Variable | Default | Effect |
| --- | --- | --- |
| `datadog_api_key` | `""` | **required** when `datadog_manage_config`; written to `datadog.yaml` |
| `datadog_site` | `""` | `site:` in `datadog.yaml` unless `datadog_config` sets it |
| `datadog_url` | `""` | `dd_url:` likewise |
| `datadog_infrastructure_mode` | `""` | `infrastructure_mode:` likewise |
| `datadog_remote_updates` | `true` | `remote_updates:` (always emitted) |
| `datadog_config` | `{}` | extra `datadog.yaml` keys, rendered with `to_nice_yaml` |
| `datadog_checks` | `{}` | `conf.d/<check>.d/conf.yaml` per entry; must be a mapping |
| `datadog_custom_checks` | `{}` | `<name>: <controller path>` → `checks.d/<name>.py`, read control-side |
| `datadog_additional_checks` | `[]` | names treated as tracked by the `conf.d` sweep |
| `datadog_disable_untracked_checks` | `false` | delete untracked `conf.yaml` |
| `datadog_disable_default_checks` | `false` | delete `conf.yaml.default` |
| `datadog_disable_example_checks` | `false` | delete `conf.yaml.example` |
| `datadog_manage_config` | `true` | gates all config writes and the API-key requirement |
| `datadog_enabled` | `true` | start/enable vs stop/disable the units |
| `datadog_skip_running_check` | `false` | skips every service-state task |
| `datadog_user` / `datadog_group` | `dd-agent` | owner/group of the config files |
| `datadog_additional_groups` | `{}` | supplementary groups for `datadog_user` (notifies restart) |
| `datadog_agent_major_version` | `""` → `7` | selects the `stable 6` / `stable 7` repository |
| `datadog_agent_version` | `""` | a pin; parsed with the role's own `agent_regexp` |
| `datadog_agent_flavor` | `datadog-agent` | package name (`datadog-dogstatsd`, …) |
| `datadog_agent_allow_downgrade` | `false` | `allow_downgrades` on a pinned install |
| `datadog_apt_cache_valid_time` | `3600` | the apt index freshness window |
| `system_probe_config`, `network_config`, `service_monitoring_config`, `system_probe_other_config` | `{}` | `system-probe.yaml` blocks; `enabled: true` in the first three starts the sysprobe unit |
| `runtime_security_config`, `compliance_config` | *undefined* | `security-agent.yaml`; the file is written only when `runtime_security_config` is non-empty |

Rejected with a clear message before any host is contacted:
`datadog_apt_repo` (custom repository — scoped out), `datadog_yum_gpgkey` and
`datadog_zypper_gpgkey` (removed from the role; `check-removed-config.yml`).

---

## 4. Program shape: seven deploys and their barriers

reemote runs a deploy **twice** (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence. The role is full
of `register:` + `when:` reads, `service_facts` probes, `find` enumerations and
fact-fed templates — all mutable reads a single deploy may not branch on. Each
becomes **gather → snapshot → factory**: `gather_fact` between two complete runs,
freeze the answer into a per-host dict, close a deploy factory over it. The
barriers are load-bearing, not stylistic.

```
load_inventory + validate_inventory ──► Host.from_inventory ──► targets = group "ubuntu"
role_settings(host)  ──► API-key / custom-repo / removed-variable gates   (no host contacted)
fetch_keys(5 URLs)                                                        (control-side HTTPS)
│
├─ gather: AptPackages, AptSources, FindInFiles(/etc/apt), FindFiles(sources.list.d),
│          SystemdUnitFiles                          ← pre-state snapshot
│
├─ run 1  build_pre_cleanup      __pycache__ + legacy datadog-installer package/units/files
├─ run 2  deploy_bootstrap       apt-transport-https, gnupg                (branch-free)
├─ run 3  build_repository       keyring, 5 × gpg.import_key(dest=…), legacy repo sweep,
│                                agent repo, custom-file removal, gated apt.update
│
├─ gather: AptUpgradable                             ← after the index refresh (GAP-3)
├─ run 4  build_agent            datadog-agent (pinned or latest), datadog-signing-keys
│
├─ gather: SystemdUnitFiles, SystemdServiceState(datadog-agent-exp.service)
│          + render every template control-side      ← the role's service_facts
├─ run 5  build_configure        additional groups, config dir, datadog.yaml, conf.d/*,
│                                checks.d/*, install_info, system-probe.yaml,
│                                security-agent.yaml, units
│
├─ gather: FindFiles(conf.d, "conf.yaml*", maxdepth=2)   ← only if a disable_* flag is set
├─ run 6  build_cleanup_conf_d   untracked / default / example check sweep
│
├─ changed_hosts(union of all 7 reports, notifying names) → split by notify target
└─ run 7  compose(restart_handler(sysprobe), restart_handler(agent))
```

Why this order:

- **repos before packages** — the agent install must see the new repository.
- **package before group membership** — `dd-agent` is created by the package, so
  the `datadog_additional_groups` request runs in the configure deploy after it.
- **installs before config** — `systemd.service` on a nonexistent unit raises
  `CommandError`, so the unit list is read after the install.
- **the `conf.d` sweep after the installs** — the enumeration must observe them.
- **handlers last** — Ansible flushes notifications at the end of the play;
  `notified_hosts()` unions `changed_hosts` across every report to reproduce that.

Streams are built **once**, in the factory body outside the generator: a stream is
hashable but compared by *identity*, so a fresh `io.BytesIO` per phase would trip
`DeterminismError`. `Host.upload_file` rewinds before every transfer, so one stream
serves the dry run and the real run alike.

---

## 5. Coverage table

Status vocabulary: **covered** (direct operation) · **recipe** (documented shape
change) · **workaround** (possible with a deviation) · **GAP** (library gap, ticket
filed) · **out** (scoped out).

### `tasks/main.yml`

| Ansible task | reemote target | Status |
| --- | --- | --- |
| Gather Ansible Facts (`setup`) | `gather_fact` in `main`: `AptPackages`, `AptSources`, `AptUpgradable`, `SystemdUnitFiles`, `SystemdServiceState`, `FindFiles`, `FindInFiles` | recipe |
| Initialize internal `datadog_config` | `role_settings()` — defaults ⊕ `host.extra` | covered |
| Check if OS is supported (`os-check.yml`) | `is_ubuntu` guard; non-Ubuntu hosts are **skipped, not failed** | covered (deviation D1) |
| Windows remote update check | — | out |
| Fail if API key is missing | `ValueError` gate in `main`, before any host contact | covered |
| `sanitize-checks.yml` (null-safe checks, tracked list, `assert … is mapping`) | `role_settings()`: `or {}`, `frozenset(checks) \| frozenset(additional)`, mapping assertion | covered |
| `set-parse-version.yml` / `parse-version.yml` | `parse_agent_version()` (the role's `agent_regexp` verbatim), `debian_pin()`, `before_version()`, major/version compatibility `ValueError` | covered |
| Remove `/opt/datadog-agent/python-scripts/__pycache__` | `files.directory(present=False)` in deploy 1, gated on `skip_install` | covered (D2, D3) |
| Remove legacy package-based `datadog-installer` | `remove-legacy-installer.yml` → deploy 1 | covered |
| Debian Install Tasks | `pkg-debian.yml` → deploys 2–4 | covered |
| `_remove_rpm_keys.yml` (RedHat/Rocky/Alma/Suse) | — | out |
| `check-removed-config.yml` (yum/zypper gpgkey) | `ValueError` gates in `main` | covered |
| RedHat / Suse / Windows / MacOS install + config | — | out |
| Linux Configuration Tasks | `agent-linux.yml` → deploys 5–6 | covered |
| Integrations Tasks (`datadog_integration`) | — | out |
| APM Single Step Instrumentation (validate + install) | — | out |

### `tasks/remove-legacy-installer.yml` → deploy 1

| Ansible task | reemote target | Status |
| --- | --- | --- |
| Remove legacy installer package (`package state=absent`) | `apt.packages(packages=("datadog-installer",), present=False)` | covered |
| Stop legacy systemd services (2 units, `failed_when: false`) | `systemd.service(running=False, enabled=False)` per unit **present in `SystemdUnitFiles`** | workaround (D2) |
| Remove legacy systemd services (6 paths) | `files.file(present=False)` × 6 | covered |
| Reload systemd units (`daemon_reload`) | `systemd.daemon_reload`, gated on a unit having been forgotten (stateless otherwise) | workaround (D4) |

### `tasks/pkg-debian.yml` → deploys 2–4

| Ansible task | reemote target | Status |
| --- | --- | --- |
| Install `apt-transport-https` / `gnupg` (`update_cache: true`) | `apt.packages(update=True, cache_valid_time=3600)` × 2 | covered (D5) |
| Check if keyring exists with correct mode (`stat`) | folded into `files.file(present=True, mode, user, group)` — content/attr idempotence needs no probe | recipe |
| Ensure keyring exists (`file state=touch mode=0644 root:root`) | `files.file(present=True, mode="0644", user="root", group="root")` | workaround (D6) |
| Install apt keys from default URLs (5 × `_apt-key-import.yml`) | `fetch_keys()` control-side + **five** `gpg.import_key(src=stream, dest=USR_SHARE_KEYRING, keyid=fingerprint)` requests | covered (GAP-1 closed by [#11](https://github.com/kimjarvis/reemote/issues/11)) |
| Install apt keys from custom URL (`datadog_apt_key_url_new`) | — | out |
| Copy keyring to `trusted.gpg.d` (Debian < 9 / Ubuntu < 16) | — | out |
| Deprecate non-https / non-`signed-by` repositories (6 × `apt_repository state=absent`, **no filename**, `update_cache: true`) | `FindInFiles("/etc/apt", LEGACY_REPO_PATTERN)` → `legacy_repo_targets()` → `apt.repo(present=False, filename=stem)` per line × per file | **GAP-5 → [#15](https://github.com/kimjarvis/reemote/issues/15)** |
| Ensure Datadog repository is up-to-date (`with_dict {6,7}`, `filename=ansible_datadog_<major>`) | `apt.repo(src=AGENT_REPOS[major], present=(major == chosen), filename=…)` × 2 | covered |
| Custom repo: init flag / `stat` / `slurp` / compare | — | out (`datadog_apt_repo` rejected) |
| (Custom) Remove Datadog custom repository file | `files.file(CUSTOM_REPO_FILE, present=False)` — the `datadog_apt_repo | length == 0` half | covered |
| (Custom) Ensure Datadog repository is up-to-date | — | out |
| `install-pinned.yml` | `apt.packages(packages=("<flavor>=<pin>",), allow_downgrades=…, update=True, cache_valid_time=…)` | covered |
| `install-latest.yml` | `apt.packages(packages=(flavor,), latest=flavor in upgradable, …)` | **GAP-3 → [#13](https://github.com/kimjarvis/reemote/issues/13)** |
| Install latest `datadog-signing-keys` (`state=latest`, no `update_cache`) | `apt.packages(latest=pkg in upgradable)` | **GAP-3 → [#13](https://github.com/kimjarvis/reemote/issues/13)** |
| `update_cache: true` on every `apt_repository` | snapshot-gated stateless `apt.update()` at the end of deploy 3 | **GAP-4 → [#14](https://github.com/kimjarvis/reemote/issues/14)** |

### `tasks/_apt-key-import.yml` (the 5-key loop)

| Ansible task | reemote target | Status |
| --- | --- | --- |
| `gpg --no-default-keyring --keyring … --list-keys \| grep <fpr>` | `gpg.import_key`'s own `keyid` idempotence check (`GpgKeyrings` on the dest's directory) | covered |
| tempfile + `get_url force=true mode=600` | `fetch_keys()` control-side (`urllib`) | recipe (D7) |
| `cat … \| gpg --dearmor` | not needed — `gpg --import` reads armored input natively | recipe |
| export the single key / `copy` for CURRENT | not needed — each URL's body imports as-is; CURRENT is fingerprint-pinned (D7) | recipe |
| `cat single.gpg \| gpg --no-default-keyring --keyring … --import --batch` | `gpg.import_key(dest=USR_SHARE_KEYRING)` — **additive merge**, keys already in the file stay | covered (GAP-1 closed) |
| remove tempdir | nothing to clean — no host-side tempdir is created | covered |

### `tasks/agent-linux.yml` → deploy 5

| Ansible task | reemote target | Status |
| --- | --- | --- |
| Populate service facts | `gather_fact(SystemdUnitFiles)` + `SystemdServiceState("datadog-agent-exp.service")` between runs 4 and 5 | recipe |
| Set before 6/7.40.0 / 7.24.1 / 7.18.0 flags | `before_version(parsed, …)` | covered |
| Set before 6/7.61.0 flag | **corrected** — the role tests undefined `datadog_major/minor/bugfix`, so the flag is always false | workaround (D8) |
| Add user to additional groups (`notify: restart datadog-agent`) | `server.user(groups=…, append=True, ensure_home=False)`, named request | covered |
| Include configuration setup tasks | `_agent-linux-macos-shared.yml` → deploy 5 | covered |
| Set system probe installed | `"datadog-agent-sysprobe.service" in units` | covered |
| Set system probe enabled (4 version-gated blocks) | `sysprobe_is_enabled()` — the intended `(any enabled flag or other config) and installed` | workaround (D8, D9) |
| Create system-probe configuration file (`0640 root:dd-agent`, notify) | `render_system_probe_yaml()` + `files.install`, named | covered (GAP-2 closed) |
| Remove system-probe configuration file | `files.file(present=False)`, named | covered |
| Ensure `datadog-agent` is running | `systemd.service(running=True, enabled=True)` gated on enabled ∧ exp not running ∧ unit exists | covered |
| Ensure `datadog-agent-sysprobe` is running | `systemd.service(...)` gated on enabled ∧ sysprobe_enabled ∧ unit exists | covered |
| Ensure agent/process/trace are not running (3 units) | `systemd.service(running=False, enabled=False)` per existing unit | covered |
| Ensure sysprobe is stopped (before 6/7.18.0) | `systemd.service(running=False, enabled=False)` gated on `before_7180` ∧ (¬enabled ∨ ¬sysprobe_enabled) ∧ installed | covered |
| Ensure `datadog-agent-security` is not running (`failed_when: false`) | `systemd.service(...)` gated on the unit existing | workaround (D2) |
| Create / Remove security-agent configuration file | `render_security_agent_yaml()` + `files.install` / `files.file(present=False)`, named | covered |

### `tasks/_agent-linux-macos-shared.yml` → deploys 5–6

| Ansible task | reemote target | Status |
| --- | --- | --- |
| Create Datadog agent config directory (`0755`) | `files.directory(mode="0755", user, group)` | covered |
| Create main Datadog agent configuration file (`0640`, notify) | `render_datadog_yaml()` + `files.install`, named | covered (GAP-2 closed) |
| Register all check configs (`find`, gated on untracked **or** default) | `gather_fact(FindFiles, CONF_D, fname="conf.yaml*", maxdepth=2)` between runs 5 and 6, gated on **all three** flags | recipe (D10) |
| Delete checks not in tracked / all default / all example (3 loops, notify) | `files.file(present=False)` per stale path, named, in deploy 6 | recipe (D11) |
| Ensure configuration directories per check (`0755`) | `files.directory` per check | covered |
| Create a configuration file per check (`checks.yaml.j2`, `0640`, notify) | `render_check_yaml()` + `files.install`, named | covered (GAP-2 closed) |
| Remove old configuration file per check (`conf.d/<check>.yaml`, notify) | `files.file(present=False)`, named | covered |
| Create custom check file per custom check (`copy`, `0755`, notify) | controller-side `Path.read_text()` + `files.install(content=…)`, named | recipe (D12) |
| Create installation information file (`install_info.j2`, `0644`) | `render_install_info()` + `files.install` — **`tool: reemote`** | covered (D13) |
| `install.json`: stat / slurp / debug / parse / uuid (`command: cat /proc/…`) / rescue `uuidgen` / signature / template | — | out (opt-in: `datadog_apm_instrumentation_enabled`) |

### `handlers/main.yml` → deploy 7

| Ansible handler | reemote target | Status |
| --- | --- | --- |
| `restart datadog-agent-sysprobe` | `restart_handler(sysprobe_restart, ("datadog-agent-sysprobe",), when=is_ubuntu)` | recipe |
| Gather service facts | the `SystemdUnitFiles` / `SystemdServiceState` gather before deploy 5 | recipe |
| `restart datadog-agent` | `restart_handler(agent_restart, ("datadog-agent",), when=is_ubuntu)` | recipe |
| `restart datadog-agent-win` / `-macos` | — | out |

Notification channel: every notifying request carries a stable `name=`;
`notifying_names()` enumerates them from the same frozen inputs the deploys were
built from; `notified_hosts()` unions `changed_hosts(report, names)` over all seven
reports; the set is split by the role's `before_7180` notify switch and filtered by
each handler's own `when:`.

---

## 6. Implementation gaps

Five genuine library gaps were proven (full module surface enumerated, then
corroborated by identifier greps), classified and ranked. **All five are
bridgeable, so the migration completes.** G1 and G2 have since been closed by #11
and #12 and their workarounds removed from the program ([§10](#10-second-pass)).
Each gap is marked `GAP-n (#issue)` at its point of use in the program, and each
ticket carries the evidence, the workaround, the acceptance criteria and the
second-pass plan.

| Gap | Ticket | Rank | Workaround | Deviation it costs |
| --- | --- | --- | --- | --- |
| **G1** `gpg.import_key` had no `dest`/`keyring` — **closed** | [#11](https://github.com/kimjarvis/reemote/issues/11) | core | none left — five additive `gpg.import_key(dest=USR_SHARE_KEYRING, keyid=…)` requests | none left; the retired bundle workaround overwrote the whole file, left CURRENT unpinned and failed all five on one bad URL |
| **G2** `files.install` read the destination back **unprivileged**, so `0640` service-owned files re-installed every deploy — **closed** | [#12](https://github.com/kimjarvis/reemote/issues/12) | core | none left — `FileContents` re-reads a present-but-unreadable file elevated (`run_shell_privileged`), so `install`/`transform`/`download` converge on service-owned configs | none left; the retired workaround added the login user to `dd-agent` in an earlier run — an extra privileged change the role never makes, widening API-key readability |
| **G3** `apt.packages(latest=True)` upgrades unconditionally — non-idempotent | [#13](https://github.com/kimjarvis/reemote/issues/13) | core | gather `AptUpgradable` after the repo deploy; `latest = pkg in upgradable_names` | the decision is pre-state, so a candidate appearing between gather and apply waits for the next run |
| **G4** `apt.repo` has no `update_cache` (`**kwargs` absorbs it silently) | [#14](https://github.com/kimjarvis/reemote/issues/14) | core (ordering) | snapshot boolean `needs_update` gates a **stateless** `apt.update()` | the refresh decision is frozen one barrier early; whole-index rather than scoped |
| **G5** `apt.repo` removal is file-scoped and `AptSources` discards provenance | [#15](https://github.com/kimjarvis/reemote/issues/15) | core for the deprecation path | one `FindInFiles("/etc/apt", LEGACY_REPO_PATTERN)` grep → fan the six lines out across every file that holds one | hand-written ERE to keep in sync; `6 × files` requests; deb822 / `.list.save` hits cannot be edited and are dropped; an extra barrier |

Evidence highlights (line numbers in `src/reemote/modules/`):

- **G1** — closed: `gpg/gpg.py` `import_key` now takes `dest` and renders
  `gpg --batch --yes --no-default-keyring --keyring <dest> --import <spool>`
  (additive), with idempotence decided against `GpgKeyrings` on the dest's parent
  directory. `GpgKeyDeleteKeysCommand`'s `--no-default-keyring --keyring`
  rendering was the in-repo precedent.
- **G2** — closed: `files/files.py` `FileContents` now distinguishes *present but
  unreadable* (the `cat` fails and prints an `UNREADABLE` marker) from absent and
  empty, and `process` re-reads it with `run_shell_privileged(..., sudo=True)` —
  elevation from the host's inventory `privilege`, no API change. `install`,
  `transform` and `download` inherit the fix. The `SimulatedHost` seam gained a
  mode-aware `login_can_read`, so the offline suite proves the fallback.
- **G3** — `apt/apt.py` L2688-L2720: the `latest` candidate set is *installed ∧
  unpinned*, with no upgrade check; the comment at L2592-L2596 says so explicitly.
  The `upgrade=True` branch **is** fact-gated (L2652-L2667), and `AptUpgradable`
  (L1428) already ships.
- **G4** — `grep update_cache apt/apt.py` → **0 matches**; `repo` L3171-L3270,
  `sources_file` L3395, `key` L2213 all absorb it. `dnf/dnf.py`
  `packages(update_cache=…)` L1323/L1452-L1454 is the cross-namespace precedent.
- **G5** — `apt.repo` computes one `path` (L3238), decides presence globally
  (L3239-L3241) and silently no-ops when the line lives elsewhere (L3253-L3260).
  `AptSource` L951-L982 has no filename; `AptSources.COMMAND` L1055-L1060 prints
  `#reemote-aptsource <path>` and `parse` L1171-L1208 throws it away.

**Not ticketed** (deliberate boundaries, not gaps): no per-request `become_user`;
`files.file(touch=True)` being stateless (it matches Ansible); the host-side
`install.json` UUID (opt-in feature, scoped out).

**A stale ticket:** [#9](https://github.com/kimjarvis/reemote/issues/9) ("apt
`cache_valid_time` is dropped") is **factually wrong** — `AptUpdateStamp`
(`apt.py` L1302-L1400) plus the gate at L2802-L2810 and the update prefix at
L2635-L2650 implement it. This migration uses `cache_valid_time` successfully.

---

## 7. Deliberate deviations from Ansible semantics

Every one is recorded in a comment at its site in the program.

| # | Deviation | Why |
| --- | --- | --- |
| D1 | Non-Ubuntu hosts are **skipped**, not failed (`os-check.yml` fails) | the migration targets one OS; the shared inventory holds Alpine and CentOS hosts that must not be touched |
| D2 | `failed_when: false` is dropped; the legacy-unit and security-agent tasks are **preconditioned on the unit existing** instead | reemote is fail-fast and has no per-request `failed_when`; `systemd.service` on a missing unit raises `CommandError`. On Ubuntu the removals are no-ops when the installer is absent, so the tolerance is unnecessary |
| D3 | The `__pycache__` removal reports `changed` (the role sets `changed_when: false`) | a real removal *is* a change; suppressing it would make the log dishonest |
| D4 | `daemon_reload` is gated on a unit having been forgotten | it is stateless (always `changed`), so an unconditional request would break the no-op second pass |
| D5 | The bootstrap installs honour `cache_valid_time=3600` (the role passes `update_cache: true` with no window) | reemote's `apt.update` prefix is stateless; without the window every run reports a change |
| D6 | `file state=touch` → `files.file(present=True, mode, user, group)` | Ansible's `touch` bumps mtime every run and reemote reproduces that faithfully; `present=True` converges to the same end state without the churn |
| D7 | Keys are fetched **control-side**, one stream per key, and CURRENT is **fingerprint-pinned** like the other four | no controller filesystem paths inside a deploy; the role's host-side tempdir/`get_url`/`cat` chain disappears. The role imports CURRENT without a fingerprint check; pinning it makes every import idempotent and turns a rotation into a one-line constant update (CURRENT currently serves the same key as `C0962C7D`, so the two deduplicate) |
| D8 | `agent_datadog_before_7610` is **corrected** | the role tests `datadog_major/minor/bugfix`, which it never defines (`parse-version.yml` sets `agent_datadog_*`). The flag is always false, so the "since 6/7.61.0" block always runs and overwrites the three earlier ones — they are dead code. The migration implements the 7610 expression as the effective one |
| D9 | The sysprobe-enabled expression's `and`/`or` precedence is **corrected** to `(any enabled flag or other config) and installed` | as written, a non-empty `system_probe_other_config` bypasses `sysprobe_installed`, and reemote would then start a nonexistent unit and raise |
| D10 | The `conf.d` `find` is gated on **all three** disable flags | the role gates it on `disable_untracked or disable_default` only, so `disable_example_checks` alone leaves the example loop with nothing to iterate — a role oversight |
| D11 | The sweep uses the found path directly instead of reconstructing it from `dirname \| basename \| regex_replace`, and runs as a **post-configure deploy** | the reconstruction turns a stray `conf.d/conf.yaml` into a bogus `conf.d/conf.d/conf.yaml` removal; the enumeration is a mutable read so it cannot share a deploy with the installs. Only *untracked* checks are removed while only *tracked* ones are written, so the order is immaterial |
| D12 | Custom checks are read control-side as **text** | `files.install` takes `content: str`; the role's `checks.d` entries are Python source. A binary check would need `files.put` into a login-writable path |
| D13 | `install_info` stamps **`tool: reemote`** and reemote's version | the file exists to tell Datadog how the agent got there; `installer_version` still carries `datadog_role-5.6.0` |
| D14 | `ansible_managed` → a **static** `MANAGED_HEADER` | Ansible's string is timestamped, which a deterministic deploy may not render |
| D15 | `skip_install` gates only the `__pycache__` cleanup, not the install request | `apt.packages` is version-aware for a `name=version` pin, so an already-installed pin is a no-op by construction |
| D16 | `exp_state` is compared to systemd's raw `ActiveState == "active"` | Ansible's `service_facts` reports `running`/`stopped`; `active` is the equivalent test |

---

## 8. Offline verification

[`tests/test_roles_ansible_datadog.py`](../../../tests/test_roles_ansible_datadog.py)
imports the example by path and replays `main()` barrier-for-barrier against
persistent `SimulatedHost`s — the real driver, prepare + apply, plus a `dry=True`
preview. Four tests:

1. the first pass deploys (legacy installer removed, keyring installed, repo
   rewritten, agent upgraded, config written, handler queued);
2. non-Ubuntu hosts are never touched;
3. **the second pass reports `changed=False` everywhere and queues no handler**;
4. a dry preview mutates nothing.

Three simulation limits are bridged explicitly between passes, because the
in-memory host models no real subsystem: `apt_upgradable` is not cleared by a
simulated install; `gpg_keyrings` entries hold content-hash stub keys (the
simulation parses no OpenPGP), so the real imported fingerprints are seeded by
hand; `systemd_unit_files` is a static table, so a removed unit is forgotten by
hand — what `daemon-reload` does for real. The `find`/`grep` seams are recomputed
from the simulated filesystem after every pass, so the second pass really
observes the first pass's removals.

`tests/test_examples.py` also scans the program statically (no mutable-container
literals in `op()`, import-time-constructible module constants) and drives every
factory with a `SNAPSHOT_ARGS` fixture.

Gates: `uv run pytest tests/ -q` → **4277 passed**; `uv run ruff check .` → clean.
The docs build is unaffected (`examples/roles` is not in `mkdocs.yml`'s example
scan paths).

**Not yet run against a real host.** Doing so mutates the target and needs explicit
approval; the recipe is [§2](#2-running-it-against-inventoryjson-with-uv) — run it,
inspect the plans, then run it again and confirm `0 changed request(s)`.

---

## 9. Scoped out

Agreed in Phase 0: one target OS, the role's default path, opt-in branches excluded.

- **Other platforms** — Windows, macOS, RedHat/Rocky/AlmaLinux, Suse. Their tasks,
  handlers and `_remove_rpm_keys.yml` are not migrated.
- **APM Single Step Instrumentation** — including the whole `install.json` chain
  (`stat`/`slurp`/`from_json`, the host-side `cat /proc/sys/kernel/random/uuid` with
  its `uuidgen` rescue, `install_signature`, `install.json.j2`). Opt-in via
  `datadog_apm_instrumentation_enabled`.
- **Integrations** (`datadog_integration` → `integration.yml`).
- **Custom apt repository** (`datadog_apt_repo`) and **custom key URL**
  (`datadog_apt_key_url_new`). The custom-repo block needs a per-host remote content
  read (`slurp` + compare) this program does not implement; `datadog_apt_repo` is
  rejected with an explicit message rather than silently ignored. Only the block's
  cleanup half runs (remove the leftover `ansible_datadog_custom.list`).
- **Debian < 9 / Ubuntu < 16** — the `copy remote_src` of the keyring into
  `trusted.gpg.d`. Both are far below any supported Ubuntu target.

---

## 10. Second pass

As the tickets land, the workarounds come out and the program gets closer to the
role's own shape. #11 and #12 have landed:

| Ticket | What disappears from the program |
| --- | --- |
| [#11](https://github.com/kimjarvis/reemote/issues/11) — **done** | `fetch_key_bundle()`'s concatenation and the single-bundle `gpg.key` are gone: `fetch_keys()` keeps the five bodies separate and deploy 3 issues five additive `gpg.import_key(dest=…)` imports, CURRENT fingerprint-pinned |
| [#12](https://github.com/kimjarvis/reemote/issues/12) — **done** | the login-user group-membership deploy is gone (and its deviation): `FileContents` re-reads unreadable files elevated, so the `0640` configs stay service-owned and converge. The eight-deploy sequence is now seven, with `datadog_additional_groups` folded into the configure deploy |
| [#13](https://github.com/kimjarvis/reemote/issues/13) | the `AptUpgradable` gather and its snapshot; `latest=True` directly — one barrier fewer |
| [#14](https://github.com/kimjarvis/reemote/issues/14) | `repository_snapshot`'s `needs_update` and the gated `apt.update`; `update_cache=True` on each `apt.repo` request |
| [#15](https://github.com/kimjarvis/reemote/issues/15) | `LEGACY_REPO_PATTERN`, `legacy_repo_targets()`, the `FindInFiles` gather and the per-file fan-out; six `apt.repo(present=False)` requests exactly as the role has them |

The offline test's between-pass bridges shrink accordingly (no `apt_upgradable`
retirement once `latest` is fact-gated in-namespace).