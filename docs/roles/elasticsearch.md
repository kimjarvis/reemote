# elasticsearch — Elasticsearch search engine role

| | |
| --- | --- |
| Role | [`geerlingguy.elasticsearch`](https://github.com/geerlingguy/ansible-role-elasticsearch) |
| Module | [`reemote.roles.elasticsearch`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/elasticsearch.py) |
| Runner | [`examples/roles/elasticsearch.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/elasticsearch.py) |
| Target | **Ubuntu** — hosts in the `"Ubuntu"` OS family |
| Variables | the entry's `elasticsearch` role mapping → `ElasticsearchVars` |

<!---
| Waves | `2` on **both** passes — one checkpoint, no facts; the post-flush tail (restart, service, wait) always follows it |
| Outputs | none — the deploy returns `None`; no other role reads elasticsearch's results |
| Extra deps | **`reemote[templates]`** (jinja2 renders `elasticsearch.yml.j2` and the two heap templates) |
| Offline test | [`tests/roles/test_elasticsearch.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_elasticsearch.py) |
| Factory | plain (non-`async`) — the runner calls `elasticsearch(v)`; the renders and the version branch are control-side Python inside the body, not barriers |

`elasticsearch` installs Elasticsearch on Ubuntu hosts: the
`apt-transport-https`/`gnupg2` dependencies, the Elastic signing key downloaded
into `/etc/apt/trusted.gpg.d/elasticsearch.asc`, the `artifacts.elastic.co`
apt repository (appended to `/etc/apt/sources.list`, with its change-gated
index refresh), the `elasticsearch` package, the rendered
`/etc/elasticsearch/elasticsearch.yml` and the version-appropriate JVM file —
the `jvm.options.d/heap.options` drop-in from major 7 on, the full
`jvm.options` below it (both `root:elasticsearch`, mode `0660`, each with its
metadata companion) — then the started/enabled service and a TCP wait for the
HTTP port; a config change queues the role's one handler, `restart
elasticsearch`. The role has no command/shell tasks and reads no facts.

It is a **library role**, not a script: one factory that returns a resumable
deploy, so a program composes it with
`yield from scoped("elasticsearch", elasticsearch(v))(host)` and a run is one
`run_waves` pass. The Ansible role's whole task list *and* its handler are
that single deploy, and its `defaults/main.yml` is `ElasticsearchVars`.

Also like memcached and redis, this is a **checkpoint-only role** (design doc
§18.13) — with a tail the kibana role does not have: the play's
`meta: flush_handlers` sat *between* the template tasks and the service task,
so the restart, the state enforcement and the port wait all live after the
checkpoint, in one post-checkpoint wave. The stream is:

```python
yield from install(plan)(host)               # wave 0 — deps, key, repo, package
yield from configure(plan)(host)             #         + two install/companion pairs
wave_report = yield checkpoint("configure done")  # the role's only barrier
notified = changed_hosts(wave_report, NOTIFY_RESTART)
yield from handler(notified)(host)           # wave 1 — restart (notified hosts) …
yield from start(plan)(host)                 #         … then service, then the wait
```

## 1. What it is, and where it came from

The role's tasks — and its handler — map onto reemote requests and one barrier:

| Ansible task | reemote stage | Status |
| --- | --- | --- |
| `include_tasks: setup-{{ ansible_os_family }}.yml` | the `Ubuntu`-only family guard and `setup-Debian.yml`'s requests; the RedHat `yum` repo task is scoped out (D7) — no `LinuxDistribution` read survives the single-OS scope | recipe |
| `apt: name=[apt-transport-https, gnupg2]` | `apt.packages(packages=DEPENDENCIES, sudo=True)` — one request, the role's own order | covered |
| `get_url: url=…/GPG-KEY-elasticsearch dest=/etc/apt/trusted.gpg.d/elasticsearch.asc force=yes` | `files.download(url=KEY_URL, dest=KEY_PATH, mode="0644")` — control-side fetch, content-idempotent install (D1) | recipe |
| `apt_repository: repo="deb …/{{ version }}/apt stable main" update_cache=true` (no `name`) | `apt.repo(src=…, update_cache=True)` with **no `filename`** — the same `/etc/apt/sources.list` the unnamed task appended to; the refresh rides the change (issue #14) (D2) | covered |
| `package: name={{ elasticsearch_package }} state={{ elasticsearch_package_state }}` | `apt.packages(packages=(plan.package,), latest=plan.package_state == "latest")` | covered |
| `template: elasticsearch.yml.j2 … owner=root group=elasticsearch mode=0660` `notify: restart elasticsearch` | control-side Jinja2 render → `files.install` ("Configure Elasticsearch") + `files.file` metadata companion ("Set Elasticsearch configuration attributes"); both names notify (D4) | recipe |
| the two `when=`-gated JVM `template` tasks (`elasticsearch_version[0] \| int` < 7 / >= 7) | `version_major(hv.version)` decides `plan.jvm_path` (`jvm.options` vs `jvm.options.d/heap.options`) and which template renders — a plain control-side `if` inside the plan (D5) | recipe |
| `meta: flush_handlers` | `wave_report = yield checkpoint("configure done")` → `changed_hosts(…, NOTIFY_RESTART)` → `restart_handler(notified, "elasticsearch")` | barrier |
| `service: name=elasticsearch state={{ … }} enabled={{ … }}` | `systemd.service(service="elasticsearch", running=…, restarted=…, reloaded=…, enabled=…)` | covered |
| `wait_for: host={{ network_host }} port={{ http_port }} delay=3 timeout=300` | `server.wait(host=plan.network_host, port=plan.http_port, timeout=300.0)` (D6) | covered |

The task mapping needed no library extension. The `wait_for` task had once
been the role's only gap — `server.wait` could poll only a local listening
`Port`, not an arbitrary address — and [issue #56](https://github.com/kimjarvis/reemote/issues/56)
("server: wait has no `host=` variant") is **closed and shipped**: `host=None`
or `"localhost"` polls the `Port` fact, any other address polls the
`TcpEndpoint` fact from the target, exactly the `wait_for host=` axis. The
Phase 3 sweep over the open issues found nothing else new. The key's
`force: yes` re-download is the one faithfulness choice *not* copied (D1), and
the elevated install's own `mkdir -p` makes the drop-in directory's parent a
side effect rather than a task (D4).

## 2. Install

```bash
pip install "reemote[templates]"   # or: uv add "reemote[templates]"
```

`jinja2` is the only extra: `elasticsearch.yml.j2` interpolates three
variables and the two heap templates two more, and the sibling files carry
literal `${ES_TMPDIR}` / `%p` free text, so the renders stay Jinja2 rather
than f-strings. The import enters only through `reemote.roles._render`,
*inside* the render functions, so `import reemote.roles` stays clean without
the extra and a missing `jinja2` surfaces at render time rather than at import
time.

SSH access to each Ubuntu host with the key named in its `connection`, and
passwordless sudo (or a `privilege` section in the inventory): every request
carries `sudo=True`, because reemote has no play-level `become`. The key and
config writes are spool-and-install (`files.download`, `files.install`), so
the transfers themselves never elevate. Elasticsearch also needs a **Java
runtime** — the README's `geerlingguy.java` prerequisite; the migrated answer
is to compose the shipped `java` role in the parent deploy (D8). The
controller needs egress to `artifacts.elastic.co` for the key fetch; the host
downloads nothing.
--->

::: reemote.roles.elasticsearch
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members:
        - elasticsearch

**Variables:**

### Role variables (`defaults/main.yml`)

| Field | Type | Default | Upstream var |
| --- | --- | --- | --- |
| `version` | `str` | `7.x` — the Elastic repository channel; its **first character** must be a digit (D5) and decides the JVM file | `elasticsearch_version` |
| `package` | `str` | `elasticsearch` — the README pins a concrete version *through the name* (`elasticsearch=7.10.2`) | `elasticsearch_package` |
| `package_state` | `present` \| `latest` | `present` | `elasticsearch_package_state` |
| `service_state` | `started` \| `stopped` \| `restarted` \| `reloaded` | `started` | `elasticsearch_service_state` |
| `service_enabled` | `bool` | `True` — the unit's boot-time state | `elasticsearch_service_enabled` |
| `network_host` | `str` | `localhost` — `network.host`, and the address the final wait probes | `elasticsearch_network_host` |
| `http_port` | `int` | `9200` — `http.port`, and the wait's port | `elasticsearch_http_port` |
| `heap_size_min` | `str` | `1g` — the `-Xms` argument | `elasticsearch_heap_size_min` |
| `heap_size_max` | `str` | `2g` — the `-Xmx` argument | `elasticsearch_heap_size_max` |
| `extra_options` | `str` | `""` — raw lines appended at the end of the config | `elasticsearch_extra_options` |

<!---
Precedence is `model defaults < inventory role mapping < overrides`. Validation
is fail-fast at `resolve_vars`, before any host is contacted: `version` must
start with a digit — Ansible's `elasticsearch_version[0] | int` coerced
`"x.y"` to `0` and silently took the `< 7` branch, this model rejects it (D5) —
`package`, `network_host` and the two heap sizes must be non-empty strings,
`package_state` and `service_state` are `Literal` enums over the values the
README documents, and `http_port` keeps pydantic's lax int coercion (`"9201"`
is accepted as `9201`, the repo-wide convention). A wrongly typed value is a
pydantic `ValidationError` — a `ValueError`, so the runner maps it. A key
naming no field in the role mapping, such as `packagez`, raises `RoleVarsError`
naming the valid keys.

## 5. Run it

The role is library code; `examples/roles/elasticsearch.py` is the runnable
artifact — a standalone program with its own `main()`, no shared runner. It
loads and validates `inventory.json` from the current working directory,
resolves the role's variables (the defaults overlaid with the entry's
`elasticsearch` role mapping, then the call-site `overrides`), builds the
deploy from the `elasticsearch` factory and drives it with `run_waves` — a
`dry=True` preview, then the real apply — printing the wave tree each time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import ElasticsearchVars, elasticsearch

ELASTICSEARCH_OVERRIDES = {"network_host": "127.0.0.1", "heap_size_min": "2g"}

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(ElasticsearchVars, "elasticsearch", inventory, overrides=ELASTICSEARCH_OVERRIDES)
    deploy = elasticsearch(vars_set)                                        # plain factory
    report, _hosts = await run_waves(inventory, deploy, dry=True)           # parks at the checkpoint
    print(report.tree())
    report, _hosts = await run_waves(inventory, deploy)                     # the real apply
    print(report.tree())
```

From the repository root:

```bash
uv run python examples/roles/elasticsearch.py
```

The call-site `ELASTICSEARCH_OVERRIDES` bind the node to `127.0.0.1` instead
of `localhost` and lift the heap floor to `2g`, so the demonstration writes
both config files visibly different from the packaged defaults — the restart
handler has something to fire on — and moves the final wait from the `Port`
fact to the `TcpEndpoint` fact. The `dry=True` pass is *not* empty: the role's
first items are requests, so the dry run plans all eight configure requests
and then parks on the checkpoint barrier (`barrier: configure done → unknown
until run`) — the notification read needs a real apply report, so the
handler/service/wait tail stays unplanned, exactly like Ansible's `--check`
skipping handlers. Nothing is applied.

**Run the program twice.** The second pass must report no state change: the
dependencies and the package are installed, the key and the repository line
are in place (so the change-gated `apt-get update` never re-runs), both
config files are byte-identical with `root:elasticsearch` `0660` metadata, and
the service is running and enabled — the checkpoint notifies nobody, so no
restart appears. The one line that still reports `changed` is the port wait:
a poll is not a state gate, and its request name is deliberately absent from
`NOTIFY_RESTART` so it cannot restart the service forever (D6). That is the
migration's idempotence claim, and the offline suite asserts it.

## 6. What it does

One deploy. The checkpoint closes the configure wave; the tail — restart,
service, wait — is a second wave *even once converged*, because Ansible ran
`meta: flush_handlers` before the service task, not after it:

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `install`: `apt.packages(DEPENDENCIES)`, `files.download(key)`, `apt.repo(src, update_cache=True)`, `apt.packages((package,))` — then `configure`: two `files.install` + `files.file` companion pairs — then `checkpoint("configure done")` | every input is a resolved variable or a frozen constant, so the whole task list is one pure request wave, in the role's own order; the checkpoint reads its outcome |
| 1 | `handler`: `restart_handler(notified, "elasticsearch")` (request only on notified hosts), then `start`: `systemd.service(running=…, enabled=…)`, `server.wait(host=…, port=…, timeout=300.0)` | the post-`flush_handlers` tail: restart first, then state enforcement, then the readiness check — in yield order within the wave |

Five rules are load-bearing:

* **The checkpoint is the only barrier — and there are no facts at all.** The
  version branch is decided by the `version` *string* alone, and the
  `get_url`/`apt_repository` pair are operations whose own fact gates (content
  equality, repository identity) live inside their commands. A `fact` or `act`
  barrier here would be speculatively added.
* **Two waves on both passes.** Unlike redis — where a converged pass has no
  handler and the second wave disappears — the service task and the wait
  follow the checkpoint unconditionally, because `flush_handlers` preceded
  them. On the converged pass the only *changed* entry is the wait.
* **The wait's name is not a notifier (D6).** `server.wait` is stateless —
  always `changed` — so admitting `"Wait for Elasticsearch to accept
  connections"` into `NOTIFY_RESTART` would restart the service on every run.
  The role's `notify:` sits on the two version-gated template tasks; the two
  metadata companions join them because Ansible's template module reports a
  change on owner/group/mode drift too (D4).
* **The elevated install creates the drop-in directory (D4).**
  `jvm.options.d` needs no `file: state=directory` task in either role —
  `files.install`'s `mkdir -p` creates the destination's parent as a side
  effect, which the offline test pins via the `directory_stats` seam.
* **The handler entry is the library's generated name.** The log line is
  `Restart elasticsearch (handler)` — `restart_handler`'s default label — and
  `changed_hosts` matches the *notifying* names exactly; the dependency, key,
  repository, package and service tasks carry no `notify` in the role, so
  their changes never restart the daemon.

A host whose `family` is not `"Ubuntu"` returns before its first `yield`: no
requests, no barriers, no report lines — RedHat entries in a shared inventory
are never contacted.

## 7. Transcript

From `tests/roles/test_elasticsearch.py`, against the simulated Ubuntu host
(`network_host` overridden to `127.0.0.1`, `heap_size_min` to `2g`, exactly
the runner's pair) plus a RedHat host the role never touches. The key fetch is
stubbed at the control-side seam and the port/endpoint wait seams are seeded
— the simulation never starts a daemon. Dry preview first: all eight
configure requests are planned, the checkpoint is unknown, nothing is
applied.

```text
(root)
  wave 0
    ubuntu: Ensure dependencies are installed [changed: 2 package(s) to install]
    ubuntu: Add Elasticsearch apt key [changed: content /etc/apt/trusted.gpg.d/elasticsearch.asc is absent, will install]
    ubuntu: Add Elasticsearch repository [changed: apt repo "deb https://artifacts.elastic.co/packages/7.x/apt stable main" missing, will be added to /etc/apt/sources.list; apt update]
    ubuntu: Install Elasticsearch    [changed: 1 package(s) to install]
    ubuntu: Configure Elasticsearch  [changed: content /etc/elasticsearch/elasticsearch.yml is absent, will install]
    ubuntu: Set Elasticsearch configuration attributes [changed: /etc/elasticsearch/elasticsearch.yml missing, will create; parent /etc/elasticsearch missing, will create]
    ubuntu: Configure Elasticsearch JVM options [changed: content /etc/elasticsearch/jvm.options.d/heap.options is absent, will install]
    ubuntu: Set Elasticsearch JVM options attributes [changed: /etc/elasticsearch/jvm.options.d/heap.options missing, will create; parent /etc/elasticsearch/jvm.options.d missing, will create]
    barrier: configure done → unknown until run
```

Fresh host, real apply (commands abbreviated):

```text
(root)
  wave 0
    ubuntu: Ensure dependencies are installed [changed: 2 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y … install apt-transport-https gnupg2
    ubuntu: Add Elasticsearch apt key [changed: content /etc/apt/trusted.gpg.d/elasticsearch.asc is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-68d37fbe5569c77c/content
        $ sudo sh -c 'mkdir -p /etc/apt/trusted.gpg.d && cp …/content /etc/apt/trusted.gpg.d/elasticsearch.asc && chmod 644 … && rm -rf …'
    ubuntu: Add Elasticsearch repository [changed: apt repo "deb https://artifacts.elastic.co/packages/7.x/apt stable main" missing, will be added to /etc/apt/sources.list; apt update]
        $ sudo apt-get update
    ubuntu: Install Elasticsearch    [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y … install elasticsearch
    ubuntu: Configure Elasticsearch  [changed: content /etc/elasticsearch/elasticsearch.yml is absent, will install]
        $ sudo sh -c 'mkdir -p /etc/elasticsearch && cp …/content /etc/elasticsearch/elasticsearch.yml && chown root:elasticsearch … && chmod 660 …'
    ubuntu: Set Elasticsearch configuration attributes [no change: file /etc/elasticsearch/elasticsearch.yml already exists]
    ubuntu: Configure Elasticsearch JVM options [changed: content /etc/elasticsearch/jvm.options.d/heap.options is absent, will install]
        $ sudo sh -c 'mkdir -p /etc/elasticsearch/jvm.options.d && cp …/content …/heap.options && chown root:elasticsearch … && chmod 660 …'
    ubuntu: Set Elasticsearch JVM options attributes [no change: file /etc/elasticsearch/jvm.options.d/heap.options already exists]
    barrier: configure done → 1 host notified
  wave 1
    ubuntu: Restart elasticsearch (handler) [changed: elasticsearch.service not running (None), will start]
        $ sudo systemctl start elasticsearch.service
    ubuntu: Start Elasticsearch      [changed: elasticsearch.service already running; elasticsearch.service not enabled, will enable]
        $ sudo systemctl enable elasticsearch.service
    ubuntu: Wait for Elasticsearch to accept connections [changed: wait for tcp endpoint 127.0.0.1:9200 to accept connections]
```

The rerun — every *state* request is a no-op, the checkpoint notifies nobody,
and **the handler never appears**; only the stateless wait still reports
changed (D6), and wave 1 exists because the service task and the wait follow
the checkpoint:

```text
(root)
  wave 0
    ubuntu: Ensure dependencies are installed [no change: all packages present]
    ubuntu: Add Elasticsearch apt key [no change: content already up to date (/etc/apt/trusted.gpg.d/elasticsearch.asc)]
    ubuntu: Add Elasticsearch repository [no change: apt repo "deb https://artifacts.elastic.co/packages/7.x/apt stable main" exists]
    ubuntu: Install Elasticsearch    [no change: all packages present]
    ubuntu: Configure Elasticsearch  [no change: content already up to date (/etc/elasticsearch/elasticsearch.yml)]
    ubuntu: Set Elasticsearch configuration attributes [no change: file /etc/elasticsearch/elasticsearch.yml already exists]
    ubuntu: Configure Elasticsearch JVM options [no change: content already up to date (/etc/elasticsearch/jvm.options.d/heap.options)]
    ubuntu: Set Elasticsearch JVM options attributes [no change: file /etc/elasticsearch/jvm.options.d/heap.options already exists]
    barrier: configure done → 0 hosts notified
  wave 1
    ubuntu: Start Elasticsearch      [no change: elasticsearch.service already running; elasticsearch.service already enabled]
    ubuntu: Wait for Elasticsearch to accept connections [changed: wait for tcp endpoint 127.0.0.1:9200 to accept connections]
```

Note the handler's rendering on the fresh host: `restart_handler`'s
`restarted=True` starts a *stopped* unit (Ansible's `state: restarted`), so on
a never-started machine the restart logs as a start and the following service
task finds it already running. The mode-drift pass — content untouched, stat
mode pushed to `0640` — isolates the companion's purpose: `Configure
Elasticsearch` stays `[no change: content already up to date]` while `Set
Elasticsearch configuration attributes [changed: …]` converges `0660` and
notifies the handler.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | `get_url force: yes` becomes a content-idempotent `files.download` | Ansible re-fetched and re-wrote the key on every run; reemote fetches the URL *control-side*, compares the bytes against the destination and installs only on a real difference, so a converged pass writes nothing. The key is deliberately not checksum-pinned — Elastic rotates the artifact and a pinned digest would fail the deploy on the first rotation |
| D2 | The unnamed `apt_repository` maps to `apt.repo` with **no `filename`** | `apt_repository` without a `name` appended its line to `/etc/apt/sources.list`, and `apt.repo(filename=None)` manages exactly that file — a faithful mapping (contrast the kibana role's derived drop-in name); `update_cache: true` becomes the change-gated `apt.repo(update_cache=True)` refresh (issue #14), so a converged pass runs no `apt-get update` |
| D3 | `ansible_managed` is frozen to the literal `Ansible managed` | Ansible's ansible.cfg default is a *timestamped* string, which would rewrite `elasticsearch.yml` on every run and fire the restart handler forever; the frozen header keeps it byte-stable (the redis/memcached precedent) |
| D4 | `files.file` metadata companions are **added**, and their names **notify** | `files.install`'s idempotence is content-only, so a `root:elasticsearch` `0660` drift on unchanged content would never converge, while Ansible's template module reports a change on owner/group/mode drift too (the java/redis/kibana companion precedent). The elevated install's own `mkdir -p` creates `jvm.options.d`, so no directory task exists in either role |
| D5 | The `elasticsearch_version[0] \| int` branch becomes a control-side `if` over a parsed major | `ElasticsearchVars` rejects a version whose first character is not a digit at `resolve_vars` time — where Ansible's `\| int` coerced such a string to `0` and silently took the `< 7` branch — and the parse stays faithful to the `[0]` index: `"10.x"` reads as major `1` and still selects the `< 7` branch, preserving the role's own quirk |
| D6 | `wait_for host=… port=… delay=3 timeout=300` becomes `server.wait(host=…, port=…, timeout=300.0)`, and the wait's name never notifies | there is no initial-`delay` knob — the poll starts immediately at `interval` spacing, which only makes the converged check earlier, never later — and because a wait is stateless (always `changed`), admitting its name into `NOTIFY_RESTART` would restart the service forever |
| D7 | The RedHat path is scoped out | a Phase 0 single-OS decision; `setup-RedHat.yml`'s `yum` repository task simply has no place in the Ubuntu request set |
| D8 | The Java prerequisite stays with the caller | the Ansible README defers it to `geerlingguy.java` and the role itself never installed Java — the migrated answer is to compose the shipped `java` role in the parent deploy |

## 9. Scoped out

* **RedHat branch** (`tasks/setup-RedHat.yml`: the `yum` repository task with
  its `gpgcheck`/`enabled` INI keys, `logstash`-adjacent package naming): a
  Phase 0 single-OS decision — the target is Ubuntu. reemote's `dnf` module
  covers the operations that path uses; it is simply outside the agreed target.
* **`elasticsearch_package: elasticsearch-oss`** and version pinning through
  the package name: both ride `ElasticsearchVars.package` verbatim — the
  README documents them — so nothing is excluded, but no offline test changes:
  it is the same `apt.packages` request.
* **OpenSSL/JDK tuning, x-pack settings** (`elasticsearch_extra_options`
  content): raw lines through the template's final substitution, already
  covered by the model's `extra_options` field.
* **The role's molecule/galaxy CI scaffolding**: never part of the task path.
--->
