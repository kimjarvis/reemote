# zabbix_agent — Zabbix agent role

| | |
| --- | --- |
| Role | [`dj-wasabi/ansible-zabbix-agent`](https://github.com/ansible-community/ansible-zabbix-agent) (4.4-era) |
| Module | [`reemote.roles.zabbix_agent`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/zabbix_agent.py) |
| Example | [`examples/roles/zabbix_agent.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/zabbix_agent.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `zabbix_agent` role mapping → `ZabbixAgentVars` |

<!---
| Waves | 5 on a fresh host, 4 once converged (the handler wave only exists after the checkpoint notified) |
| Outputs | none — the deploy returns `None` on every path |
| Extra deps | **`reemote[templates]`** (jinja2 renders `zabbix_agentd.conf`) |
| Offline test | [`tests/roles/test_zabbix_agent.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_zabbix_agent.py) |
| Factory | **`async def`** — the corpus's second; the runner must `await zabbix_agent(v)` |

`zabbix_agent` installs and configures the Zabbix 4.x agent on Ubuntu hosts: the
vendor apt keyring and repository lines (with the old-source purge), the agent
packages, the optional apt pin, the SELinux permissive gate, the rendered
`/etc/zabbix/zabbix_agentd.conf`, the TLS-PSK file when asked for, the include
directory, the service's enabled/running state, and the role's single
`restart zabbix-agent` handler.

It is the corpus's second **async factory** (after `datadog`): the signing-key
download is one inventory-wide constant URL, so it happens in the factory — once,
and even under `dry` — and the four reads the Ansible play took between its task
files live as barriers inside the one per-host generator:

```python
key_text = await files.fetch_url(REPO_KEY_URL)      # the factory, control-side

codename = hv.distribution_release or (yield fact(LsbRelease))["codename"]
found = yield fact(FindFiles, SOURCES_DIR, fname=SOURCES_PATTERN)   # the purge read
yield from install(plan, hv, stream)(host)
result = yield act(Shell, command=GETENFORCE_PROBE, sudo=True)      # exit status is data
yield from configure(hv, conf, selinux_active)(host)
notified = changed_hosts(yield checkpoint("configure done"), NOTIFYING_NAMES)
yield from service_handler(notified, (SERVICE_NAME,), action="restarted")(host)
```

## 1. What it is, and where it came from

| Ansible task | reemote request | Status |
| --- | --- | --- |
| `include_vars: vars/<OS>.yml`, the `zabbix_agent_os_family` dispatch | the frozen Debian constants, one family guard | deviation (D1) |
| `apt_key: url=... state=present` | factory `files.fetch_url` → `apt.key(src=stream, keyid=…)` | deviation (D3) |
| `find: repo_zabbix_com_zabbix*.list` + `file: state=absent` loop | `yield fact(FindFiles, …)` → one `files.file(present=False)` per stale path | barrier (D8) |
| `apt_repository: deb …/ubuntu focal main` (and `deb-src`, `update_cache: yes`) | `apt.repo(src=…, update_cache=True)` ×2 | covered (D2) |
| `copy: content=… dest=/etc/apt/preferences.d/…` (apt priority) | `files.directory` + `files.install(content=…)` | covered |
| `apt: name={{ zabbix_agent_packages }}` + `environment:` proxy | `apt.packages(…, env=…)` via `op`'s meta-parameters | covered (D5) |
| `service: enabled=yes use=service` (the Debian-stage enable) | `systemd.service(enabled=True, running=None)` | covered |
| `stat` + `command: getenforce` + `set_fact` chain | `yield act(Shell, "test -x … && getenforce")` | barrier (D6) |
| `selinux_permissive: name=zabbix_agent_t` | `selinux.permissive_domain(domain=…)` | covered (D9) |
| `copy: zabbix_agent_tlspskfile` (+ its directory task, `notify`) | `files.directory` + `files.install(mode="0400")` | covered |
| `template: zabbix_agentd.conf.j2` | control-side Jinja2 render → `files.install(content=…)` | recipe (D13) |
| `file: state=directory` (the include dir) | `files.directory(mode="0750")` | covered |
| `service: state=started enabled=yes` (final) | `systemd.service(running=True, enabled=True)` | covered |
| `handlers/main.yml: restart zabbix-agent` + the tasks' `notify` | `checkpoint("configure done")` → `changed_hosts` → `service_handler` | deviation (D7) |
| the Linux.yml IP `set_fact` chain (`listeninterface`, `listenip`) | `listenip` verbatim; `listeninterface` raises | deviation (D10) |
| `userparameters` copy + scripts-tree lookup | raises — no library operation (issue [#67](https://github.com/kimjarvis/reemote/issues/67)) | GAP (D11) |
| the Docker, TLS-PSK-auto, iptables, firewalld and Zabbix API branches | raise when their knob is set; the API has no operations at all (issue [#72](https://github.com/kimjarvis/reemote/issues/72)) | GAP (D11) |

One **new** gap came out of this migration and was filed rather than worked
around: the Zabbix API branch (`zabbix_api_create_hosts`/`hostgroup`, the whole
`zabbix_host` module surface) has no reemote operations — issue
[#72](https://github.com/kimjarvis/reemote/issues/72). It is default-off, so it
never blocked the Ubuntu default path.

## 2. Install

```bash
pip install "reemote[templates]"    # or: uv add "reemote[templates]"
```

`jinja2` is the only extra. The template renders through
`reemote/roles/_render.py`, imported *inside* the render function, so
`import reemote.roles` stays clean without the extra.

The **control** machine needs egress to `repo.zabbix.com` (the signing-key
download happens in the factory, before any host is contacted); each target
needs egress to the same host for `apt`. SSH access with passwordless sudo (or
a `privilege` section): every request carries `sudo=True`, because reemote has
no play-level `become`.

## 3. Variables

`ZabbixAgentVars` is a frozen pydantic model (`extra="forbid"`) whose fields
carry bare names; the role name passed to `resolve_vars` binds the entry's
`zabbix_agent` role mapping, so `zabbix_agent_server` in an existing inventory
sets `server` — no key gymnastics.

--->

::: reemote.roles.zabbix_agent
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - zabbix_agent

**Variables:**

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `version` | `str` | `4.4` | `zabbix_version` / `zabbix_agent_version` |
| `repo` | `str` | `zabbix` | `zabbix_repo` |
| `distribution_release` | `str \| None` | `null` → the `LsbRelease` read | `zabbix_agent_distribution_release` |
| `src_reinstall` | `bool` | `true` | `zabbix_agent_src_reinstall` |
| `packages` | `tuple[str, …]` | `("zabbix-agent", "zabbix-sender", "zabbix-get")` | `zabbix_agent_packages` |
| `package_state` | `Literal["present","latest","absent"]` | `present` | `zabbix_agent_package_state` |
| `install_recommends` | `bool` | `false` | `zabbix_apt_install_recommends` |
| `apt_priority` | `int \| None` | `null` | `zabbix_agent_apt_priority` |
| `http_proxy` / `https_proxy` | `str \| None` | `null` | `zabbix_http_proxy` / `zabbix_https_proxy` |
| `selinux` | `bool` | `false` | `zabbix_selinux` |
| `pidfile` | `str` | `/var/run/zabbix/zabbix_agentd.pid` | `zabbix_agent_pidfile` |
| `logfile` | `str` | `/var/log/zabbix/zabbix_agentd.log` | `zabbix_agent_logfile` |
| `logfilesize` | `int` | `100` | `zabbix_agent_logfilesize` |
| `debuglevel` | `int` | `3` | `zabbix_agent_debuglevel` |
| `sourceip` | `str \| None` | `null` | `zabbix_agent_sourceip` |
| `enableremotecommands` | `int` | `0` | `zabbix_agent_enableremotecommands` |
| `logremotecommands` | `int` | `0` | `zabbix_agent_logremotecommands` |
| `server` | `str` | `""` | `zabbix_agent_server` |
| `serveractive` | `str` | `""` | `zabbix_agent_serveractive` |
| `listenport` | `int` | `10050` | `zabbix_agent_listenport` |
| `listenip` | `str \| None` | `null` | `zabbix_agent_listenip` |
| `listeninterface` | `str \| None` | `null` — **raises if set** (D10) | `zabbix_agent_listeninterface` |
| `startagents` | `int` | `3` | `zabbix_agent_startagents` |
| `hostname` | `str \| None` | `null` → the host's inventory name | `zabbix_agent_hostname` |
| `hostnameitem` | `str \| None` | `null` | `zabbix_agent_hostnameitem` |
| `hostmetadata` | `str \| None` | `null` | `zabbix_agent_hostmetadata` |
| `hostmetadataitem` | `str \| None` | `null` | `zabbix_agent_hostmetadataitem` |
| `refreshactivechecks` | `int` | `120` | `zabbix_agent_refreshactivechecks` |
| `buffersend` | `int` | `5` | `zabbix_agent_buffersend` |
| `buffersize` | `int` | `100` | `zabbix_agent_buffersize` |
| `maxlinespersecond` | `int` | `100` | `zabbix_agent_maxlinespersecond` |
| `zabbix_alias` | `str \| None` | `null` | `zabbix_agent_zabbix_alias` |
| `timeout` | `int` | `3` | `zabbix_agent_timeout` |
| `allowroot` | `int` | `0` | `zabbix_agent_allowroot` |
| `runas_user` | `str \| None` | `null` | `zabbix_agent_runas_user` |
| `include` | `str` | `/etc/zabbix/zabbix_agentd.d` | `zabbix_agent_include` |
| `unsafeuserparameters` | `int` | `0` | `zabbix_agent_unsafeuserparameters` |
| `loadmodulepath` | `str` | `${libdir}/modules` | `zabbix_agent_loadmodulepath` |
| `loadmodule` | `str \| None` | `null` | `zabbix_agent_loadmodule` |
| `tlsconnect` / `tlsaccept` | `str \| None` | `null` | `zabbix_agent_tlsconnect` / `…tlsaccept` |
| `tlscafile` / `tlscrlfile` | `str \| None` | `null` | `zabbix_agent_tlscafile` / `…tlscrlfile` |
| `tlsservercertissuer` / `tlsservercertsubject` | `str \| None` | `null` | `zabbix_agent_tlsservercert…` |
| `tlscertfile` / `tlskeyfile` | `str \| None` | `null` | `zabbix_agent_tlscertfile` / `…tlskeyfile` |
| `tlspskidentity` | `str \| None` | `null` | `zabbix_agent_tlspskidentity` |
| `tlspskfile` | `str \| None` | `null` | `zabbix_agent_tlspskfile` |
| `tlspsk_secret` | `str \| None` | `null` | `zabbix_agent_tlspsk_secret` |
| `tlspsk_auto` | `bool` | `false` — **raises if true** (D11) | `zabbix_agent_tlspsk_auto` |
| `userparameters` | `tuple[Any, …]` | `()` — **raises if non-empty** (D11) | `zabbix_agent_userparameters` |
| `docker` | `bool` | `false` — **raises if true** (D11) | `zabbix_agent_docker` |
| `firewall_enable` / `firewalld_enable` | `bool` | `false` — **raise if true** (D11) | `zabbix_agent_firewall…_enable` |
| `api_create_hostgroup` / `api_create_hosts` | `bool` | `false` — **raise if true** (D11, #72) | `zabbix_api_create_…` |
| `force_apt_get`, `jmx_listenport`, `install_pip_packages`, `become_on_localhost`, `version_patch` | — | carried, **inert** | same names |

<!---
Precedence is `model defaults < inventory role mapping < overrides`. Sequences
are `tuple`, never `list`: the model is frozen and hashable, and a value that
reaches `op()` must hash. The role's *empty-string* defaults become `""` only
for the two keys the template renders unconditionally (`server`,
`serveractive`); everything else the template gates on `is defined and` is
`None`. The inert knobs are carried rather than dropped so an existing
inventory still binds — `force_apt_get` is already reemote's truth, and the
other four were only read by branches that are out of scope.

The API-only variables (`zabbix_url`, `zabbix_api_user`, `zabbix_host_groups`,
`zabbix_macros`, …) are **not** fields at all: the typo rule rejects them at
`resolve_vars` time, before any host is contacted, because nothing downstream
could honour them (#72).

## 5. Run it

The role is library code; `examples/roles/zabbix_agent.py` is the runnable
artifact — a standalone program with its own `main()`, no shared runner. It
loads and validates `inventory.json`, resolves the role's variables, **awaits**
the async factory (which downloads the signing key), and drives the deploy with
`run_waves` — a `dry=True` one-wave preview, then the real apply:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import ZabbixAgentVars, zabbix_agent

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(ZabbixAgentVars, "zabbix_agent", inventory)
    deploy = await zabbix_agent(vars_set)         # async factory: fetches the key
    report, _hosts = await run_waves(inventory, deploy, dry=True)
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/zabbix_agent.py
```

**The async factory's IO runs even under `dry`**: the one key download happens
for a preview. The `dry=True` pass then parks on the first barrier —
`barrier: LsbRelease → unknown until run` — and plans nothing further.

**Run the program twice.** The second pass must report no change anywhere and
queue no restart: the keyring and both repository lines exist, the packages are
installed, the rendered conf is byte-identical, the unit is running and enabled
— and the handler wave is simply *gone*, the checkpoint's notification read
proving itself. That is the migration's idempotence claim, and the offline
suite asserts it (with the `AptKeys` stub fingerprint bridged between passes,
the same documented seam datadog carries).

## 6. What it does

One deploy. Each barrier closes its own wave, and a wave with no records is not
appended — so the fresh host runs five waves and the converged one four:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `fact(LsbRelease)` | the codename feeding the repository line and the `sign_keys` row — skipped entirely when `distribution_release` overrides it |
| 1 | `fact(FindFiles, SOURCES_DIR, fname=SOURCES_PATTERN)` | the role's `find` before its removal loop; conditional — only hosts using the vendor repo park here |
| 2 | `install`: the keyring, the stale-source removals, both repository lines (the second carrying the refresh), the pin, the packages, the enable — closed by `act(Shell, "test -x /usr/sbin/getenforce && getenforce")` | the probe can only run after its wave's packages, so it *ends* the install wave instead of starting one |
| 3 | `configure`: the SELinux exemption, the PSK pair, the rendered conf, the include dir, the final service task — closed by `checkpoint("configure done")` | the checkpoint **is** the role's `notify`: it reads the wave it just closed and names the hosts whose `Configure zabbix-agent` / `Place TLS PSK File` entries changed |
| 4 | `service_handler(notified, ("zabbix-agent",), action="restarted")` | only on the hosts that checkpoint selected — the wave vanishes once converged |

Three ordering rules are load-bearing:

* **The purge read precedes the purge writes.** The `FindFiles` barrier ends
  wave 1, so the removal requests in wave 2 name paths the host actually
  reported — and the managed file is filtered out in Python (the role's
  `excludes`), never removed.
* **The probe rides the install wave, not the configure one.** D9's asymmetry
  falls out of this for free: `policycoreutils` installs on the *user variable*
  alone (the probe has not answered yet, exactly as in the role), while the
  permissive-domain task reads variable **or** probe.
* **The checkpoint immediately follows `configure`.** The final service task is
  deliberately *not* in `NOTIFYING_NAMES` — the role's service task carries no
  `notify`, so a start-state change alone never restarts the agent.

A host whose `host.family` is not `"Ubuntu"` returns before its first `yield`,
so it emits no requests, no barriers and no report lines at all.

## 7. Transcript

From `tests/roles/test_zabbix_agent.py::test_tree_snapshot`, against the
simulated Ubuntu host (stock focal, one stale 4.2 source) plus a RedHat host the
role never touches. Fresh host, first pass — the `scp` spool directories are
content-addressed, so `…` stands where the real run (and the test) show hashes:

```text
(root)
  wave 0
    barrier: LsbRelease → {'id': 'Ubuntu', 'description': 'Ubuntu 20.04.6 LTS', 'release': '20.04', 'codename': 'focal'}
  wave 1
    barrier: FindFiles → ['/etc/apt/sources.list.d/repo_zabbix_com_zabbix_4_2_ubuntu.list']
  wave 2
    ubuntu: Debian | Install gpg key [changed: install GPG key from <stream> to /etc/apt/trusted.gpg.d/zabbix-official-repo.gpg]
        $ scp <stream> -> /tmp/reemote-apt-key-…/key.gpg
        $ sudo sh -c 'mkdir -p /etc/apt/trusted.gpg.d && if grep -q ''…''BEGIN PGP PUBLIC KEY BLOCK''…'' …; then gpg --batch --yes --dearmor -o /etc/apt/trusted.gpg.d/zabbix-official-repo.gpg …; fi && chmod 0644 … && rm -rf …'
    ubuntu: Debian | Remove unecessary zabbix repository /etc/apt/sources.list.d/repo_zabbix_com_zabbix_4_2_ubuntu.list [changed: /etc/apt/sources.list.d/repo_zabbix_com_zabbix_4_2_ubuntu.list present, will remove]
        $ sudo rm -f /etc/apt/sources.list.d/repo_zabbix_com_zabbix_4_2_ubuntu.list
    ubuntu: Debian | Installing deb repository Ubuntu [changed: apt repo "deb http://repo.zabbix.com/zabbix/4.4/ubuntu focal main" missing, will be added to /etc/apt/sources.list.d/repo_zabbix_com_zabbix_4_4_ubuntu.list]
        $ scp <apt sources> -> /tmp/reemote-apt-repo-…/repo
        $ sudo sh -c 'mkdir -p /etc/apt/sources.list.d && cp … && chmod 0644 … && rm -rf …'
    ubuntu: Debian | Installing deb-src repository Ubuntu [changed: apt repo "deb-src http://repo.zabbix.com/zabbix/4.4/ubuntu focal main" missing, will be added to /etc/apt/sources.list.d/repo_zabbix_com_zabbix_4_4_ubuntu.list; apt update]
        $ scp <apt sources> -> /tmp/reemote-apt-repo-…/repo
        $ sudo sh -c '…'
        $ sudo apt-get update
    ubuntu: Debian | Installing zabbix-agent [changed: 3 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install --no-install-recommends zabbix-agent zabbix-sender zabbix-get
    ubuntu: Debian | Enable the service [changed: zabbix-agent.service active state left alone; zabbix-agent.service not enabled, will enable]
        $ sudo systemctl enable zabbix-agent.service
    barrier: Collect getenforce output → {'exit_status': 1, 'stdout': '', 'stderr': ''}
  wave 3
    ubuntu: Configure zabbix-agent   [changed: content /etc/zabbix/zabbix_agentd.conf is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-…/content
        $ sudo sh -c 'mkdir -p /etc/zabbix && cp … /etc/zabbix/zabbix_agentd.conf && chown root:root … && chmod 644 … && rm -rf …'
    ubuntu: Create include dir zabbix-agent [changed: /etc/zabbix/zabbix_agentd.d missing, will create]
        $ sudo mkdir -p /etc/zabbix/zabbix_agentd.d
        $ sudo chmod 750 /etc/zabbix/zabbix_agentd.d
        $ sudo chown root:zabbix /etc/zabbix/zabbix_agentd.d
    ubuntu: Make sure the zabbix-agent service is running [changed: zabbix-agent.service not running (None), will start; zabbix-agent.service already enabled]
        $ sudo systemctl start zabbix-agent.service
    barrier: configure done → 1 host notified
  wave 4
    ubuntu: Restart zabbix-agent (handler) [changed: zabbix-agent.service already running; zabbix-agent.service will restart]
        $ sudo systemctl restart zabbix-agent.service
```

The getenforce barrier prints its `Result` dict verbatim — exit 1 on Ubuntu is
*data*, not a failure (D6). The rerun — the reads still run (they are reads),
every request no-ops, and **the handler wave disappears entirely**:

```text
(root)
  wave 0
    barrier: LsbRelease → {'id': 'Ubuntu', …, 'codename': 'focal'}
  wave 1
    barrier: FindFiles → ['/etc/apt/sources.list.d/repo_zabbix_com_zabbix_4_4_ubuntu.list']
  wave 2
    ubuntu: Debian | Install gpg key [no change: GPG key A14FE591 already in an apt keyring]
    ubuntu: Debian | Installing deb repository Ubuntu [no change: apt repo "deb http://repo.zabbix.com/zabbix/4.4/ubuntu focal main" exists]
    ubuntu: Debian | Installing deb-src repository Ubuntu [no change: apt repo "deb-src http://repo.zabbix.com/zabbix/4.4/ubuntu focal main" exists]
    ubuntu: Debian | Installing zabbix-agent [no change: all packages present]
    ubuntu: Debian | Enable the service [no change: zabbix-agent.service active state left alone; zabbix-agent.service already enabled]
    barrier: Collect getenforce output → {'exit_status': 1, 'stdout': '', 'stderr': ''}
  wave 3
    ubuntu: Configure zabbix-agent   [no change: content already up to date (/etc/zabbix/zabbix_agentd.conf)]
    ubuntu: Create include dir zabbix-agent [no change: directory /etc/zabbix/zabbix_agentd.d already exists]
    ubuntu: Make sure the zabbix-agent service is running [no change: zabbix-agent.service already running; zabbix-agent.service already enabled]
    barrier: configure done → 0 hosts notified
```

The RedHat host appears in no tree: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | A host outside the `"Ubuntu"` family emits **nothing at all** | the family guard is the body's opening `if`; the role's Debian/Mint/SUSE/RedHat/XCP-ng/Sangoma/Windows/macOS branches are frozen away, and the template's three Windows gates never fire because the OS-family variable is frozen to `"Debian"` |
| D2 | The apt refresh rides `apt.repo(update_cache=True)` on the last repository line | the role's `cache_valid_time: 0` refreshed the index on *every* run — a permanently changed task; this shape refreshes only when a sources file actually changed |
| D3 | `apt_key:` becomes factory-side `files.fetch_url` + per-host `apt.key(stream, keyid=…)` | idempotent through the `AptKeys` fact (issue #53) instead of the deprecated `apt-key`; the keyring lands in `/etc/apt/trusted.gpg.d` under a fixed name — global trust, matching `apt_key`'s semantics, so the repository lines stay byte-identical to the role's |
| D4 | The `sign_keys` table is frozen from `vars/zabbix.yml`, and an unknown (version, codename) pair **raises** | the 4.4-era table has no jammy/noble rows; a `ValueError` naming the override beats Ansible's undefined-key failure inside the `apt_key` task |
| D5 | The tasks' `environment:` blocks become `op`'s `env=` meta-parameter | issue #66; omitted exactly where the role used `default(omit)` |
| D6 | The getenforce `stat` + `command` + `set_fact` chain becomes one `act` barrier | a non-zero exit is data — the missing binary and the disabled daemon both read as "no SELinux" — and `changed_when: false` is inherent to a barrier |
| D7 | The handler is the checkpoint pattern; its `enabled: yes` is not reproduced inside `service_handler` | the install stage's enable task and the final service task already converge `enabled` on every host; the `firewalld-reload` command handler is scoped out with its notifying tasks |
| D8 | The repository purge is `fact(FindFiles)` + a Python filter, one removal request per stale path | faithful to the role's `find` → `file: state=absent` loop with `excludes`; the managed file is never in the removal set |
| D9 | The SELinux ordering quirk is **preserved**, not smoothed over | the policycoreutils install in the Debian stage sees only the user-set `selinux` variable (the probe has not run yet, exactly as in the role), while the permissive-domain task runs on variable **or** probe verdict |
| D10 | The `Linux.yml` IP `set_fact` chain is out of scope | it only feeds the API branch; `listenip` is honoured verbatim and `listeninterface` raises rather than silently ignoring it |
| D11 | The scoped-out branches **raise**, never silently drop | `docker`, `tlspsk_auto`, `firewall_enable`, `firewalld_enable`, `api_create_hosts`/`hostgroup` and a non-empty `userparameters` each name their replacement path (#67, #72); the API-only variables are rejected outright by `extra="forbid"` |
| D12 | `hostname` defaults to the reemote host name; unset `server`/`serveractive` render `Server=` | the role's `inventory_hostname` default maps directly, and its literal empty-default rendering is what the template produced too |
| D13 | The template is carried **verbatim** and rendered control-side | through `reemote.roles._render` with Ansible's `comment` filter and a `version_compare` *test* registered — the systemd role precedent; `ansible_managed` is frozen to its default text |

## 9. Scoped out

* **Non-Ubuntu families.** The role's RedHat/SUSE/Debian-other/Windows/macOS
  task files are not migrated; `vars/Debian.yml` becomes frozen constants.
* **The Zabbix API branch.** `zabbix_api_create_hosts`/`hostgroup` and every
  `zabbix_url`-family variable — no operations exist (#72).
* **Docker containers, TLS-PSK auto-generation, iptables/firewalld opening
  port 10050, and `userparameters`** (whose scripts-tree lookup has no library
  pair, #67). Each knob raises when set (D11).
* **`src_reinstall` beyond the purge.** The variable gates the find-and-remove
  sweep; a full repo *re*-install (purge + hold + re-add) is not reproduced —
  the managed lines are simply rewritten idempotently.
* **Downgrading the agent.** `package_state: absent` removes the packages; the
  role has no downgrade orchestration and neither does this migration.

--->
