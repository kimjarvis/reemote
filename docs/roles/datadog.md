# datadog — Datadog Agent role

| | |
| --- | --- |
| Role | [`datadog.datadog`](https://github.com/DataDog/ansible-datadog) 5.6.0 |
| Module | [`reemote.roles.datadog`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/datadog.py) |
| Runner | [`examples/roles/datadog.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/datadog.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | `datadog_`-prefixed inventory keys → `DatadogVars` |

<!---
| Waves | up to 10 (fewer once converged; the conditional FindFiles barrier and handler wave disappear) |
| Outputs | none — the deploy returns `None` |
| Extra deps | `reemote[templates]` (PyYAML renders the four YAML templates) |
| Offline test | [`tests/roles/test_datadog.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_datadog.py) |
| Factory | **`async def`** — the first in the corpus; the runner must `await datadog(v)` |

`datadog` installs and configures the Datadog Agent on Ubuntu hosts: the apt
signing keys and repository, the agent package, `/etc/datadog-agent`
configuration, check configs under `conf.d`, custom Python checks under
`checks.d`, the `install_info` stamp, the systemd units, and the role's two
restart handlers.

It is a **library role**, not a script: one async factory that fetches the five
signing keys control-side and returns a resumable deploy, so a program composes
it with `yield from scoped("datadog", await datadog(v))(host)` and a run is one
`run_waves` pass.

## 1. What it is, and where it came from

The role's task files map onto reemote operations and barriers:

| Ansible task file | reemote stage | Status |
| --- | --- | --- |
| `main.yml` pre-tasks (`__pycache__`, `remove-legacy-installer.yml`) | `pre_cleanup` (gated on AptPackages + SystemdUnitFiles facts) | covered |
| `pkg-debian.yml` bootstrap (apt-transport-https, gnupg) | `bootstrap` | covered |
| `pkg-debian.yml` keys + repo (5× `_apt-key-import.yml`, 6× legacy `apt_repository`, 2× agent repo) | `repository` (gpg.import_key dest=, apt.repo) | covered |
| `pkg-debian.yml` agent install (pinned or latest) + signing-keys | `agent` (apt.packages) | covered |
| `_agent-linux-macos-shared.yml` + `agent-linux.yml` config | `configure` (files.install, files.directory, server.user, systemd.service) | covered |
| `agent-linux.yml` conf.d cleanup (3 loops) | `cleanup_conf_d` (gated on FindFiles fact) | covered |
| `handlers/main.yml` (restart agent, restart sysprobe) | two `restart_handler` channels, checkpoint-union | covered |

Five genuine library gaps were proven during the original migration. All five
are closed: #11 (additive multi-key import), #12 (elevated read-back of 0640
files), #13 (non-idempotent `latest`), #14 (`apt.repo` had no `update_cache`),
#15 (`apt.repo` removal was file-scoped with no source provenance).

## 2. Install

```bash
pip install "reemote[templates]"    # or: uv add "reemote[templates]"
```

The `templates` extra pulls PyYAML, which the four YAML renderers need. The
control machine needs outbound HTTPS to `s3.amazonaws.com` (the signing keys);
the target needs egress to `apt.datadoghq.com` (the agent repository).

SSH access to each Ubuntu host with passwordless sudo: every request carries
`sudo=True`.

## 3. Variables

`DatadogVars` is a frozen pydantic model with unprefixed field names; the prefix
passed to `resolve_vars` binds the inventory keys.

--->

::: reemote.roles.datadog
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - datadog

**Variables:**

| Field | Type | Default | Inventory key | Ansible name |
| --- | --- | --- | --- | --- |
| `api_key` | `str` | `""` | `datadog_api_key` | `datadog_api_key` |
| `site` | `str` | `""` | `datadog_site` | `datadog_site` |
| `url` | `str` | `""` | `datadog_url` | `datadog_url` |
| `infrastructure_mode` | `str` | `""` | `datadog_infrastructure_mode` | `datadog_infrastructure_mode` |
| `remote_updates` | `bool` | `true` | `datadog_remote_updates` | `datadog_remote_updates` |
| `config` | `dict` | `{}` | `datadog_config` | `datadog_config` |
| `checks` | `dict` | `{}` | `datadog_checks` | `datadog_checks` |
| `custom_checks` | `dict[str, str]` | `{}` | `datadog_custom_checks` | `datadog_custom_checks` |
| `additional_checks` | `tuple[str, ...]` | `()` | `datadog_additional_checks` | `datadog_additional_checks` |
| `disable_untracked_checks` | `bool` | `false` | `datadog_disable_untracked_checks` | `datadog_disable_untracked_checks` |
| `disable_default_checks` | `bool` | `false` | `datadog_disable_default_checks` | `datadog_disable_default_checks` |
| `disable_example_checks` | `bool` | `false` | `datadog_disable_example_checks` | `datadog_disable_example_checks` |
| `manage_config` | `bool` | `true` | `datadog_manage_config` | `datadog_manage_config` |
| `enabled` | `bool` | `true` | `datadog_enabled` | `datadog_enabled` |
| `skip_running_check` | `bool` | `false` | `datadog_skip_running_check` | `datadog_skip_running_check` |
| `user` | `str` | `"dd-agent"` | `datadog_user` | `datadog_user` |
| `group` | `str` | `"dd-agent"` | `datadog_group` | `datadog_group` |
| `additional_groups` | `dict` | `{}` | `datadog_additional_groups` | `datadog_additional_groups` |
| `agent_major_version` | `str` | `""` | `datadog_agent_major_version` | `datadog_agent_major_version` |
| `agent_version` | `str` | `""` | `datadog_agent_version` | `datadog_agent_version` |
| `agent_flavor` | `str` | `"datadog-agent"` | `datadog_agent_flavor` | `datadog_agent_flavor` |
| `agent_allow_downgrade` | `bool` | `false` | `datadog_agent_allow_downgrade` | `datadog_agent_allow_downgrade` |
| `apt_repo` | `str` | `""` | `datadog_apt_repo` | `datadog_apt_repo` |
| `apt_cache_valid_time` | `int` | `3600` | `datadog_apt_cache_valid_time` | `datadog_apt_cache_valid_time` |

<!---

Six further variables carry **no** `datadog_` prefix upstream and are read raw
from `host.extra`, preserving the existing inventory contract:

| Inventory key | Type | Default |
| --- | --- | --- |
| `system_probe_config` | `dict` | `{}` |
| `network_config` | `dict` | `{}` |
| `service_monitoring_config` | `dict` | `{}` |
| `runtime_security_config` | `dict` | `{}` (absent by default) |
| `system_probe_other_config` | `dict` | `{}` |
| `compliance_config` | `dict` | `{}` (absent by default) |

## 5. Run it

The role is library code; `examples/roles/datadog.py` is the runnable artifact —
a standalone program with its own `main()`. It loads and validates
`inventory.json`, resolves the role's variables, **awaits** the async factory
(which fetches the five signing keys), and drives the deploy with `run_waves`:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import DatadogVars, datadog

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(DatadogVars, "datadog_", inventory)
    deploy = await datadog(vars_set)            # async factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/datadog.py
```

**The async factory's IO runs even under `dry`** (§6.3): five key downloads
happen for a preview. This is the one role where `--dry` touches the network.

## 6. What it does

One deploy, up to ten waves — one per barrier, then the requests between them:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `fact(AptPackages)` | the installed-package list the pre-cleanup gate reads |
| 1 | `fact(SystemdUnitFiles)` | the unit list the legacy-installer cleanup needs |
| 2 | `pre_cleanup` + `bootstrap` + `repository` + `agent` | four stages, no barrier between them |
| 3 | `fact(SystemdUnitFiles)` | post-install unit list for the configure plan |
| 4 | `fact(SystemdServiceState, "datadog-agent-exp.service")` | the experimental agent's state |
| 5 | `configure` → `checkpoint("configure done")` | config files, checks, units |
| 6 | `fact(FindFiles, CONF_D, ...)` | conditional: only when manage_config and a disable flag |
| 7 | `cleanup_conf_d` → `checkpoint("conf.d cleanup done")` | the untracked/default/example sweep |
| 8 | two `restart_handler` channels | gated on the checkpoint union |

Two checkpoints, unioned in the body — the same shape as php (§18.6). The
notification split: requests whose name is in `PROBE_NOTIFY_NAMES` on a
`before_7180` host notify `datadog-agent-sysprobe`; everything else notifies
`datadog-agent`.

## 7. Transcript

From `tests/roles/test_datadog.py`, against the simulated Ubuntu host. The tree
is too large to reproduce in full here; run the test suite to see it. The
converged second pass reports no change and queues no handler.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | A host outside the `"Ubuntu"` family emits **nothing at all** | the family guard is the body's opening `if`, not a `when=` on each request |
| D2 | The five signing keys are fetched **control-side** (`files.fetch_url`) | the role fetches host-side into a tempdir; reemote's URL operations are control-side by design |
| D3 | `CURRENT` is fingerprint-pinned | the role leaves it unpinned; pinning makes a rotation a one-line constant update |
| D4 | `install_info` stamps `tool: reemote` | the role stamps `tool: ansible`; lying about the installer defeats the file's purpose |
| D5 | `datadog_apt_repo` is **rejected** | its stat/slurp/compare block needs a per-host remote content read this module does not carry |
| D6 | `datadog_yum_gpgkey` / `datadog_zypper_gpgkey` are **rejected** | removed from the role upstream |
| D7 | The `conf.d` sweep runs after the whole configure stage | the two never touch the same file, so the order is immaterial |
| D8 | The six probe-config variables stay unprefixed in `host.extra` | renaming them would break every existing inventory |
| D9 | `failed_when: false` tolerance becomes preconditions on gathered facts | reemote is fail-fast; on Ubuntu the removals are no-ops when the legacy installer is absent |
| D10 | **`async def` factory** — `--dry` touches the network | the five URLs are inventory-wide constants (§16.3); the IO is unconditional |

## 9. Scoped out

* **Non-Ubuntu families.** RHEL/CentOS/SUSE paths exist in the Ansible role but
  are not migrated; a non-Ubuntu host is skipped silently.
* **`datadog_apt_repo`** (a custom repository). See D5.
* **Windows / macOS agents.** Entirely different task files.
* **The experimental agent installer** (`datadog-installer`). Only its legacy
  removal is covered; installing it is out of scope.
* **Agent downgrade.** `agent_allow_downgrade` is carried as a variable and
  forwarded to `apt.packages`, but the role does not orchestrate a downgrade
  sequence (stop agent, downgrade, restart).

--->
