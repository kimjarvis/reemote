# kibana — Kibana dashboard role

| | |
| --- | --- |
| Role | [`geerlingguy.kibana`](https://github.com/geerlingguy/ansible-role-kibana) |
| Module | [`reemote.roles.kibana`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/kibana.py) |
| Runner | [`examples/roles/kibana.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/kibana.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `kibana` role mapping → `KibanaVars` |

<!---
| Waves | `2` on a changing pass (requests, then handler), `1` once converged — one checkpoint, no facts |
| Outputs | none — the deploy returns `None`; no other role reads kibana's results |
| Extra deps | **`reemote[templates]`** (jinja2 renders `kibana.yml.j2`) |
| Offline test | [`tests/roles/test_kibana.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_kibana.py) |
| Factory | plain (non-`async`) — the runner calls `kibana(v)`; the render and the key fetch are control-side Python inside the body, not barriers |

`kibana` installs Kibana on Ubuntu hosts: the `apt-transport-https`/`gnupg2`
dependencies, the Elasticsearch signing key into `/etc/apt/trusted.gpg.d`, the
`artifacts.elastic.co` apt repository (with its change-gated index refresh),
the `kibana` package, the started-and-enabled service, and the rendered
`/etc/kibana/kibana.yml` from the role's Jinja2 template; a config change
queues the role's one handler, `restart kibana`. The role has no
command/shell tasks and no facts at all.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with `yield from scoped("kibana", kibana(v))(host)`
and a run is one `run_waves` pass. The Ansible role's whole task list *and* its
handler are that single deploy, and its `defaults/main.yml` is `KibanaVars`.

Like redis and memcached, this is a **checkpoint-only role** (design doc
§18.13) — and unlike redis it keeps the upstream order verbatim:
`setup-Debian.yml` (dependencies, key, repository) runs *before* the package,
so `apt-get install kibana` resolves against the freshly added Elastic repo.
The stream is:

```python
yield from configure(kibana_plan(hv))(host)     # wave 0 — seven requests, in the role's order
wave_report = yield checkpoint("configure done") # the role's only barrier
notified = changed_hosts(wave_report, NOTIFY_RESTART)
yield from handler(notified)(host)              # wave 1 — the restart, or nothing
```

## 1. What it is, and where it came from

The role's tasks — and its handler — map onto reemote requests and one barrier:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `include_tasks: setup-{{ ansible_os_family }}.yml` | frozen `tasks/setup-Debian.yml` request set, the single-OS scope needs no gather; the RedHat branch is out of scope (D5) | recipe |
| `apt: name=[apt-transport-https, gnupg2]` | `apt.packages(packages=DEPENDENCIES)` — one request, the role's own first task | covered |
| `get_url: url=GPG-KEY-elasticsearch dest=/etc/apt/trusted.gpg.d/elasticsearch.asc mode=0644 force=true` | `files.download(url, dest, mode="0644")` — control-side fetch, content-idempotent install of the *armored* bytes verbatim (D1) | recipe |
| `apt_repository: repo='deb https://artifacts.elastic.co/packages/{{ kibana_version }}/apt stable main' update_cache=true` | `apt.repo(src, filename="kibana-<version>", update_cache=True)` — explicit drop-in name (D2); the refresh rides the change | recipe |
| `apt: name={{ kibana_package }} state={{ kibana_package_state }}` | `apt.packages(packages=(plan.package,), present=…, latest=…)` | covered |
| `service: name=kibana state={{ kibana_service_state }} enabled={{ kibana_service_enabled }}` | `systemd.service(service="kibana", running=…, restarted=…, reloaded=…, enabled=…)` | covered |
| `template: src={{ kibana_config_template }} dest={{ kibana_config_file_path }}` `notify: restart kibana` | control-side Jinja2 render → `files.install(root:root, mode="0644")` + `files.file` metadata companion; both names notify | recipe |
| `handlers/main.yml: restart kibana` (`service: state=restarted`) | `wave_report = yield checkpoint("configure done")` → `changed_hosts(…, NOTIFY_RESTART)` → `restart_handler(notified, "kibana")` | barrier |

The task mapping needed no library extension — every task landed on a shipped
operation, and no `fact`/`act` barrier is needed because `files.download` and
`apt.repo` carry their own idempotence fact gates *inside* their commands: the
key comparison is content equality at the destination, the repository's is
line identity — neither feeds a later request, so nothing has to be read
between tasks. A prior audit of this role had flagged adjacent library limits;
all of them are closed on the live tracker (verified during this migration:
the open list held only issues #42 and #72, neither of which blocks any task
here), so the migration ships with **zero new gaps**.

## 2. Install

```bash
pip install "reemote[templates]"   # or: uv add "reemote[templates]"
```

`jinja2` is the only extra: the config is rendered from the role's bundled
`kibana.yml.j2` (two `if` guards over sixty commented lines), which a plain
f-string cannot reproduce. The import enters only through `reemote.roles._render`,
*inside* `render_kibana_conf`, so `import reemote.roles` stays clean without the
extra and a missing `jinja2` surfaces at render time rather than at import time.

SSH access to each Ubuntu host with the key named in its `connection`, and
passwordless sudo (or a `privilege` section in the inventory): every request
carries `sudo=True`, because reemote has no play-level `become`. The key and
config writes are spool-and-install (`files.download`, `files.install`), so the
transfers themselves never elevate; both files are login-readable `0644`, so
the idempotence probes read them back without any elevated fallback. The key
fetch is *control-side*: the controller needs egress to
`artifacts.elastic.co`, the host downloads nothing.
--->

::: reemote.roles.kibana
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - kibana

**Variables:**

### Role variables (`defaults/main.yml`)

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `version` | `str` | `7.x` — the Elastic repository channel; selects the repo line *and* the template's `elasticsearch.url`/`elasticsearch.hosts` branch | `kibana_version` |
| `package` | `str` | `kibana` — the apt package name; the README pins a version *through the name* (`kibana=7.10.2`) | `kibana_package` |
| `package_state` | `present` \| `latest` \| `absent` | `present` (`apt.state`) | `kibana_package_state` |
| `service_state` | `started` \| `stopped` \| `restarted` \| `reloaded` | `started` (`service.state`) | `kibana_service_state` |
| `enabled` | `bool` | `True` — the unit's boot-time state | `kibana_service_enabled` |
| `config_template` | `str` | `kibana.yml.j2` — the bundled sentinel renders the embedded template; any other value is a controller-side path (D4) | `kibana_config_template` |
| `config_file_path` | `str` | `/etc/kibana/kibana.yml` | `kibana_config_file_path` |
| `server_port` | `int` | `5601` (`server.port`) | `kibana_server_port` |
| `server_host` | `str` | `0.0.0.0` (`server.host`) | `kibana_server_host` |
| `elasticsearch_url` | `str` | `http://localhost:9200` (`elasticsearch.hosts`, or `elasticsearch.url` on `6.x`) | `kibana_elasticsearch_url` |
| `elasticsearch_username` | `str` | `""` — empty renders no auth pair, as the `{% if %}` falsiness gate requires | `kibana_elasticsearch_username` |
| `elasticsearch_password` | `str` | `""` — both must be non-empty for the pair to render | `kibana_elasticsearch_password` |

<!---
Precedence is `model defaults < inventory role mapping < overrides`. Validation is
fail-fast at `resolve_vars`, before any host is contacted: `version`, `package`,
`config_file_path`, `server_host` and `elasticsearch_url` must be non-empty
strings; `package_state` and `service_state` are `Literal` enums over exactly
the values the role's two tasks accept; every one of the twelve upstream
defaults is carried (the only renaming is the `kibana_*` prefix drop). A
wrongly typed value is a pydantic `ValidationError` — a `ValueError`, so the
runner maps it. A key naming no field in the role mapping, such as `packagez`,
raises `RoleVarsError` naming the valid keys. Plain `int` fields keep pydantic's
lax coercion — `"5602"` is accepted as `5602` — the repo-wide convention
(redis' `port` behaves the same) and the render is identical either way (D6).

## 5. Run it

The role is library code; `examples/roles/kibana.py` is the runnable
artifact — a standalone program with its own `main()`, no shared runner. It
loads and validates `inventory.json` from the current working directory,
resolves the role's variables (the defaults overlaid with the entry's
`kibana` role mapping, then the call-site `overrides`), builds the deploy
from the `kibana` factory and drives it with `run_waves` — a `dry=True`
preview, then the real apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import KibanaVars, kibana

KIBANA_OVERRIDES = {"server_host": "127.0.0.1"}

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(KibanaVars, "kibana", inventory, overrides=KIBANA_OVERRIDES)
    deploy = kibana(vars_set)                                                # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)            # parks at the checkpoint
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)                      # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/kibana.py
```

The call-site `KIBANA_OVERRIDES` bind the UI to the loopback instead of the
role's `0.0.0.0`, so the demonstration leaves Kibana off the network *and*
writes a config that visibly differs from the packaged default — which is what
gives the `restart kibana` handler something to fire on through the
checkpoint. The `dry=True` pass is *not* empty here: the role's first items
are requests, so the dry run plans all seven configure requests and then parks
on the checkpoint barrier (`barrier: configure done → unknown until run`) —
the notification read needs a real apply report, so the handler wave stays
unplanned, exactly like Ansible's `--check` skipping handlers. Nothing is
applied. (The dry plan does fetch the key bytes control-side once — the content
comparison needs them — but installs nothing.)

**Run the program twice.** The second offline pass must report no change: the
dependencies and the package are installed, the key and the repository line are
in place (so the change-gated `apt-get update` never runs again), the config is
byte-identical with root:root `0644` metadata, the service is running and
enabled — and the checkpoint notifies nobody, so the handler wave never
appears. That is the migration's idempotence claim, and the offline suite
asserts it. Unlike the redis role there is no elevated-read-back subtlety:
every managed file here is world-readable, so the plain login probe sees the
converged state directly.

## 6. What it does

One deploy. The checkpoint closes the request wave, and a wave with no records
is not appended — so the shape below is what the test pins:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `configure`: `apt.packages(DEPENDENCIES)`, `files.download(KEY_URL → KEY_PATH)`, `apt.repo(src, filename=…, update_cache=True)`, `apt.packages((package,))`, `systemd.service(…)`, `files.install(/etc/kibana/kibana.yml, root:root, mode="0644")` + `files.file(mode="0644")` companion — then `checkpoint("configure done")` | every input is a resolved variable or a frozen constant, so the whole task list is one pure request wave — in the role's own order, repository **before** package; the checkpoint reads its outcome |
| 1 | `handler`: `restart_handler(notified, "kibana")` — a request only on the hosts the checkpoint selected | the role's one `notify:` channel; an empty notification set emits no request, so a converged pass has no wave 1 at all |

Five rules are load-bearing:

* **The checkpoint is the only barrier — and there are no facts at all.** The
  vars split by OS family, not release, so the single-OS scope collapses
  `include_tasks` to frozen constants and no `LinuxDistribution` read survives
  (the redis precedent). Unlike redis, even the *content* reads are internal:
  `files.download` and `apt.repo` consult their fact gates inside their own
  commands, so no barrier feeds them. A `fact` or `act` barrier here would be
  speculatively added, and **three waves** on a changing pass are the same
  signal (§18.13).
* **The role's ordering is reproduced inside the wave.** dependencies → key →
  repository → package → service → config → companion: the Elastic repo must be
  in `sources.list.d` (and the index refreshed, via `apt.repo`'s own gated
  update) *before* `apt-get install kibana` can resolve the package — the
  opposite of redis's configure-first Debian quirk, and pinned request-by-request
  by the test.
* **Two waves total — one for the requests, one for the handler.** The
  checkpoint must immediately follow the stage it reports on (§4.3); the
  handler wave's entries would be invisible to a second checkpoint, and
  nothing after the restart reads a report.
* **The companion is a first-pass no-op by design, and it notifies.**
  `files.install` writes content *and* metadata, so `files.file` changes
  nothing until a later pass finds drift: content byte-identical but group
  `kibana`/mode `600` → install reports no change (its idempotence is
  content-only) and the companion re-applies root:root `0644` — and, like
  Ansible's template module reporting a change on owner/group/mode drift, its
  notification fires the restart too (D3). Conversely the *key* drift test
  isolates the other half: `files.download` rewrites stale key bytes and no
  name in that wave notifies, so the service is never restarted for it.
* **The handler entry is the library's generated name.** The log line is
  `Restart kibana (handler)` — `restart_handler`'s default label — not a
  request the role names, and `changed_hosts` matches the *notifying* names
  (`Copy Kibana configuration`, `Set Kibana configuration attributes`) exactly;
  the dependency, key, repository, package and service tasks carry no `notify`
  in the role, so their changes never restart the daemon.

A host whose `family` is not `"Ubuntu"` returns before its first `yield`: no
requests, no barriers, no report lines — RedHat entries in a shared inventory
are never contacted and the key is never even fetched for them.

## 7. Transcript

From `tests/roles/test_kibana.py`, against the simulated Ubuntu host (every
default) plus a RedHat host the role never touches. No between-pass bridge is
needed — the simulation's own mutators maintain every seam the second pass
reads, and the key fetch is stubbed at `files._fetch_url_bytes`. Dry preview
first: every request is planned, the checkpoint is unknown, nothing is applied.

```text
(root)
  wave 0
    ubuntu: Ensure dependencies are installed [changed: 2 package(s) to install]
    ubuntu: Add Elasticsearch apt key [changed: content /etc/apt/trusted.gpg.d/elasticsearch.asc is absent, will install]
    ubuntu: Add Kibana repository    [changed: apt repo "deb https://artifacts.elastic.co/packages/7.x/apt stable main" missing, will be added to /etc/apt/sources.list.d/kibana-7.x.list; apt update]
    ubuntu: Install Kibana           [changed: 1 package(s) to install]
    ubuntu: Ensure Kibana is started and enabled at boot [changed: kibana.service not running (None), will start; kibana.service not enabled, will enable]
    ubuntu: Copy Kibana configuration [changed: content /etc/kibana/kibana.yml is absent, will install]
    ubuntu: Set Kibana configuration attributes [changed: /etc/kibana/kibana.yml missing, will create; parent /etc/kibana missing, will create]
    barrier: configure done → unknown until run
```

Fresh host, real apply:

```text
(root)
  wave 0
    ubuntu: Ensure dependencies are installed [changed: 2 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install apt-transport-https gnupg2
    ubuntu: Add Elasticsearch apt key [changed: content /etc/apt/trusted.gpg.d/elasticsearch.asc is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-68d37fbe5569c77c/content
        $ sudo sh -c 'mkdir -p /etc/apt/trusted.gpg.d && cp /tmp/reemote-install-68d37fbe5569c77c/content /etc/apt/trusted.gpg.d/elasticsearch.asc && chmod 644 /etc/apt/trusted.gpg.d/elasticsearch.asc && rm -rf /tmp/reemote-install-68d37fbe5569c77c'
    ubuntu: Add Kibana repository    [changed: apt repo "deb https://artifacts.elastic.co/packages/7.x/apt stable main" missing, will be added to /etc/apt/sources.list.d/kibana-7.x.list; apt update]
        $ scp <apt sources> -> /tmp/reemote-apt-repo-d79ed9dcca03861f/repo
        $ sudo sh -c 'mkdir -p /etc/apt/sources.list.d && cp /tmp/reemote-apt-repo-d79ed9dcca03861f/repo /etc/apt/sources.list.d/kibana-7.x.list && chmod 0644 /etc/apt/sources.list.d/kibana-7.x.list && rm -rf /tmp/reemote-apt-repo-d79ed9dcca03861f'
        $ sudo apt-get update
    ubuntu: Install Kibana           [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install kibana
    ubuntu: Ensure Kibana is started and enabled at boot [changed: kibana.service not running (None), will start; kibana.service not enabled, will enable]
        $ sudo systemctl start kibana.service
        $ sudo systemctl enable kibana.service
    ubuntu: Copy Kibana configuration [changed: content /etc/kibana/kibana.yml is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-310a4a5c1780e230/content
        $ sudo sh -c 'mkdir -p /etc/kibana && cp /tmp/reemote-install-310a4a5c1780e230/content /etc/kibana/kibana.yml && chown root:root /etc/kibana/kibana.yml && chmod 644 /etc/kibana/kibana.yml && rm -rf /tmp/reemote-install-310a4a5c1780e230'
    ubuntu: Set Kibana configuration attributes [no change: file /etc/kibana/kibana.yml already exists]
    barrier: configure done → 1 host notified
  wave 1
    ubuntu: Restart kibana (handler) [changed: kibana.service already running; kibana.service will restart]
        $ sudo systemctl restart kibana.service
```

The rerun — the checkpoint re-runs (it reads the wave, not a cached plan),
every request is a no-op, and **the handler wave disappears**:

```text
(root)
  wave 0
    ubuntu: Ensure dependencies are installed [no change: all packages present]
    ubuntu: Add Elasticsearch apt key [no change: content already up to date (/etc/apt/trusted.gpg.d/elasticsearch.asc)]
    ubuntu: Add Kibana repository    [no change: apt repo "deb https://artifacts.elastic.co/packages/7.x/apt stable main" exists]
    ubuntu: Install Kibana           [no change: all packages present]
    ubuntu: Ensure Kibana is started and enabled at boot [no change: kibana.service already running; kibana.service already enabled]
    ubuntu: Copy Kibana configuration [no change: content already up to date (/etc/kibana/kibana.yml)]
    ubuntu: Set Kibana configuration attributes [no change: file /etc/kibana/kibana.yml already exists]
    barrier: configure done → 0 hosts notified
```

Note the install order in the apply tree: the repository (and its gated
`apt-get update`) lands *before* `apt-get install kibana` runs — the package is
only resolvable after it. The mode-drift pass — content untouched, stat
metadata pushed to `kibana:600` — isolates the companion's purpose:
`Copy Kibana configuration` stays `[no change: content already up to date]`
while `Set Kibana configuration attributes [changed: …]` converges root:root
`0644` and notifies the handler.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | `get_url force: true` becomes a content-idempotent `files.download` | Ansible re-fetched and re-wrote the key on *every* run; reemote fetches the URL control-side, compares the bytes against the destination and installs only on a real difference, so a converged pass writes nothing. The key is deliberately not checksum-pinned — Elastic rotates the artifact, a pinned digest would fail the deploy on the first rotation. `apt.key` was rejected because it dearmors to a binary `.gpg` file; `files.download` preserves the armored `.asc` verbatim, exactly where the role put it |
| D2 | `apt_repository`'s derived `sources.list.d` file name becomes an explicit one | the module mangles the repo string into the file name; the role passes `filename="kibana-<version>"` so the drop-in is stable and self-describing. `update_cache: true` maps to `apt.repo(update_cache=True)`, whose refresh is change-gated — a converged repository never runs `apt-get update` (the stamp assertion pins it) |
| D3 | A `files.file` metadata companion is **added**, and its name **notifies** | `files.install`'s idempotence is content-only, so an owner/mode `0644` drift on unchanged content would never converge; Ansible's template module reports a change on owner/group/mode drift too, so the companion joins `NOTIFY_RESTART` (the java/redis companion precedent) |
| D4 | `kibana_config_template` keeps its swap knob in controller-side form | the literal `kibana.yml.j2` renders the bundled template text; any other value is a *controller* filesystem path read and rendered there (the apache vhosts-template precedent). Ansible read it from the role's own `templates/` directory on the controller, which is what this preserves |
| D5 | The RedHat path is scoped out | a Phase 0 single-OS decision. `kibana_version` still drives the Ubuntu repository line and template branch, so nothing is silently dropped from the variable surface |
| D6 | Type gates move to `KibanaVars`, and the family guard to the body's opening `if` | a wrongly typed variable is a pydantic rejection at `resolve_vars` time, before any host is contacted — but lax-mode int coercion (`"5602"` → `5602`) is *wider* than a strict `isinstance` gate, the repo-wide convention (redis' `port` behaves the same); an off-target host emits nothing at all, no `when:` and no `[skipped]` lines |

## 9. Scoped out

* **RedHat branch** (`tasks/setup-RedHat.yml`: `rpm_key` and the
  `kibana.repo.j2` yum template): a Phase 0 single-OS decision — the target is
  Ubuntu. reemote's `dnf` module ships a `repo` operation with the same
  shape, so the branch is migratable later without new library surface.
* **`kibana_package_state: absent` cleanup extras** (the role never stops or
  disables the service on removal, and the migration faithfully keeps the
  service task ungated): beyond the default path either way.
* **The role's galaxy CI scaffolding** (molecule, `.travis.yml`): never part of
  the default task path.
--->
