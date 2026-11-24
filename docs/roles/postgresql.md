# postgresql — PostgreSQL server + data-plane role

| | |
| --- | --- |
| Role | [`geerlingguy.postgresql`](https://github.com/geerlingguy/ansible-role-postgresql) |
| Module | [`reemote.roles.postgresql`](https://github.com/kimjarvis/reemote/blob/main/src/reemote/roles/postgresql.py) |
| Example | [`examples/roles/postgresql.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/postgresql.py) |
| Target | **RedHat** — hosts in the `"RedHat"` OS family (CentOS Stream 9 or 10) |
| Variables | `postgresql_`-prefixed inventory keys → [`PostgresqlVars`](#3-variables) |
| Passes | **2** — `postgresql` on the root view, `postgresql_data` on the `postgres` view |
| Waves | pass A: 7 (6 on a converged rerun — the `initdb` wave disappears); pass B: 2 |
| Outputs | `None` — no composing role in the corpus consumes an output |
| Extra deps | **`reemote[templates]`** (jinja2 renders `postgres.sh` + `pg_hba.conf`, registering the Ansible `comment` filter) |
| Offline test | [`tests/roles/test_postgresql.py`](https://github.com/kimjarvis/reemote/blob/main/tests/roles/test_postgresql.py) |

`postgresql` installs PostgreSQL from the distribution packages, initializes the
cluster, writes `postgresql.conf` / `pg_hba.conf`, starts the service, then manages
databases, users and object privileges. It is the only role in the corpus that
cannot be one `run_waves` pass, and the reason is a hard engine limit (§13): **run-as
users come from the inventory, never from a request.** The system tasks run as root
(the normal inventory view, `sudo=True`), but the role's `become_user: postgres`
data plane — and the peer-authenticated catalog read it needs — run as the OS
`postgres` user, which only an inventory `privilege.sudo_user` view can select.
`run_waves` builds one host list and closes it in its own `finally`, so nothing
inside a role can switch the privilege view mid-run. The role is therefore **two
factories**, driven as two passes:

```python
# Pass A — the system half, on the ordinary (root) inventory view.
distro = yield fact(LinuxDistribution)                 # wave 0 — the include_vars key
fips = yield fact(FipsEnabled)                         # wave 1 — the auth ternary
plan = resolve_plan(hv, distro["major"], bool(fips))
yield from setup(plan)(host); yield from initialize(plan)(host)   # wave 2
init_report = yield checkpoint("initialize done")
stat = yield act(Shell, command="test -e …/PG_VERSION", sudo=True)  # wave 3
if stat["exit_status"] != 0:
    yield act(Shell, command="sudo -u postgres …/initdb -D …", sudo=True)  # wave 4
yield from configure(plan)(host)                       # wave 5
conf_report = yield checkpoint("configure done")
yield from service(plan)(host)                         # wave 6
notified = changed_hosts(init_report, NOTIFY_RESTART) | changed_hosts(conf_report, NOTIFY_RESTART)
yield from handler(plan, notified)(host)

# Pass B — the data half, on a postgres-privilege inventory view.
data = resolve_data(hv)
yield fact(PostgresqlRoles, psql_user=data.pg_user, psql_host=data.socket_dirs[0],
           name="read pg_roles catalog")               # wave 0 — the reachability gate
yield from users(data)(host); yield from databases(data)(host); yield from users_props(data)(host)
```

The three reads the pre-migration example took *between* its `run()` calls — the
distribution and FIPS gathers, the `stat` gate and the `initdb` command — are
barriers now, so the templates are rendered inside the body from the values those
barriers returned. Nothing is frozen in a module-level plan dict keyed by host name,
and no name set is unioned across the inventory: the per-host body *is* the plan.

## 1. What it is, and where it came from

Status vocabulary: **covered** (a direct operation), **recipe** (a documented shape
change), **barrier** (a read the body consumes), **dead** (never runs in the role
either).

| Ansible task | reemote request | Status |
| --- | --- | --- |
| `include_vars: RedHat-{{ … }}.yml`, skipped for Fedora/Amazon | the frozen `REDHAT_VARS` table, keyed by the `LinuxDistribution` major | deviation (D1) |
| `yum: name={{ postgresql_packages }} … exclude=… enablerepo=…` | `dnf.packages(packages=…, exclude=("python-unversioned-command",), enable_repos=…)` | covered (#27, #32) |
| `yum: name={{ postgresql_python_library }}` | `dnf.packages(packages=(python_library,))` | covered |
| `template: postgres.sh.j2` → `/etc/profile.d/`, notify restart | control-side render → `files.install(content=…)` | recipe (D6) |
| `file: state=directory` data dir, `0700 postgres:postgres` | `files.directory(mode="0700", user=…, group=…)` | covered |
| `command: test -e {{ … }}/PG_VERSION` (`stat` + `register`) | `act(Shell, command="test -e …", sudo=True)` — the exit status is the answer | barrier (D4) |
| `command: initdb …` (`become_user: postgres`, `when: not … exists`) | `act(Shell, command="sudo -u postgres …/initdb -D …", sudo=True)` | deviation (D2) |
| `lineinfile:` loop over `postgresql_global_config_options` | one `files.transform(path=postgresql.conf, transform=…)` **per option** | recipe (D6) |
| `template: pg_hba.conf.j2`, `0600 postgres:postgres`, notify restart | control-side render → `files.install(content=…, mode="0600", user=…, group=…)` | recipe (D6) |
| `file: state=directory` unix socket dirs | `files.directory(mode=socket_mode, user=…, group=…)` per dir | covered |
| `service: name=… state=… enabled=…` | `systemd.service(running=…, enabled=…)` | covered |
| `handlers: restart postgresql` (`sleep: 5`) | `checkpoint` → `changed_hosts` → `service_handler(action=…, sleep=5)` | recipe (D3, #44) |
| `postgresql_user:` presence loop (`users.yml`) | `postgresql.role(role=…)` — presence-only, no attribute listed | covered (#45) |
| `postgresql_db:` loop (`databases.yml`) | `postgresql.database(database=…, owner=…, template=…, encoding=…)` | covered |
| `postgresql_user:` `role_attr_flags` reconcile (`users_props.yml`) | `postgresql.role(role=…, **listed_flags)` — tri-state, unlisted left alone | covered (#31, #45) |
| `postgresql_privs:` loop (`users_props.yml`) | `postgresql.privilege(privileges=…, role=…, object_type=…, objects=…)` | covered (#29) |
| the catalog re-read between `run()` calls | `fact(PostgresqlRoles, psql_user=…, psql_host=…)` — pass B's first barrier | barrier (D5, #28) |
| the role's implicit gates (`name` required, the enums, the object types) | pydantic validation on `PostgresqlVars` / the item models | deviation (D8) |

Every library gap this migration originally found is closed on main (#27
`dnf.packages(exclude=)`, #28 the elevated catalog re-read, #29
`postgresql.privilege`, #30 `server.FipsEnabled`, #31/#45 the tri-state
`postgresql.role` attribute flags, #32 `dnf.packages(enable_repos=)`, #44
`systemd.service(sleep=)`), so the role drives the shipped module surface directly
and no new gap was filed.

## 2. Install

```bash
pip install "reemote[templates]"     # or: uv add "reemote[templates]"
```

`jinja2` is the only extra. The base library imports neither `jinja2` nor `pyyaml`
— they enter through `reemote/roles/_render.py`, and `postgresql` imports that
module *inside* its render helpers, so `import reemote.roles` stays clean without
the extra and a missing `jinja2` surfaces as an `ImportError` at render time rather
than at import time. `pg_hba.conf.j2` uses the Ansible `comment` filter, which the
shared environment registers, so the rendered header reproduces the role's output
byte for byte.

SSH access to each RedHat host with the key named in its `connection`, and
passwordless sudo (or a `privilege` section in the inventory): every pass-A request
carries `sudo=True`, because reemote has no play-level `become`. **Pass B needs a
second privilege view** whose `privilege.sudo_user` is the OS `postgres` user — the
example builds it by copying the inventory and adding that one key per entry.

## 3. Variables

`PostgresqlVars` is a frozen pydantic model (`extra="forbid"`) with unprefixed field
names; the `postgresql_` prefix passed to `resolve_vars` binds the inventory keys, so
an inventory written for the role needs no change. Both passes share one `VarsSet`.

| Field | Type | Default | Inventory key |
| --- | --- | --- | --- |
| `enablerepo` | `str` | `""` → no `--enablerepo` | `postgresql_enablerepo` |
| `restarted_state` | `Literal["restarted","reloaded","started"]` | `restarted` | `postgresql_restarted_state` |
| `user` | `str` | `postgres` | `postgresql_user` |
| `group` | `str` | `postgres` | `postgresql_group` |
| `auth_method` | `Literal["md5","scram-sha-256"] \| None` | `null` → `scram-sha-256` under FIPS else `md5` | `postgresql_auth_method` |
| `unix_socket_directories` | `tuple[str, …]` | `("/var/run/postgresql",)` | `postgresql_unix_socket_directories` |
| `service_state` | `Literal["started","stopped"]` | `started` | `postgresql_service_state` |
| `service_enabled` | `bool` | `true` | `postgresql_service_enabled` |
| `global_config_options` | `tuple[PgConfigOption, …] \| None` | `null` → the two defaults | `postgresql_global_config_options` |
| `hba_entries` | `tuple[PgHbaEntry, …] \| None` | `null` → the four defaults | `postgresql_hba_entries` |
| `databases` | `tuple[PgDatabase, …]` | `()` | `postgresql_databases` |
| `users` | `tuple[PgUser, …]` | `()` | `postgresql_users` |
| `privs` | `tuple[PgPriv, …]` | `()` | `postgresql_privs` |
| `version` | `str \| None` | `null` → `REDHAT_VARS` (informational on RedHat) | `postgresql_version` |
| `packages` | `tuple[str, …] \| None` | `null` → `REDHAT_VARS` | `postgresql_packages` |
| `python_library` | `str \| None` | `null` → `REDHAT_VARS` | `postgresql_python_library` |
| `daemon` | `str \| None` | `null` → `REDHAT_VARS` | `postgresql_daemon` |
| `data_dir` | `str \| None` | `null` → `REDHAT_VARS` | `postgresql_data_dir` |
| `bin_path` | `str \| None` | `null` → `REDHAT_VARS` | `postgresql_bin_path` |
| `config_path` | `str \| None` | `null` → `REDHAT_VARS` | `postgresql_config_path` |
| `unix_socket_directories_mode` | `str \| None` | `null` → `REDHAT_VARS` | `postgresql_unix_socket_directories_mode` |

Precedence is `model defaults < inventory extra < overrides`. Sequences are `tuple`,
never `list`: the model is frozen and hashable, and a value that reaches `op()` must
hash. The RedHat-overridable knobs are `None` by default and fall back to the frozen
`REDHAT_VARS[major]` table, which is the role's `vars/RedHat-<major>.yml` dispatch —
a major version outside the table raises `ValueError` before any host is mutated.
`unix_socket_directories` accepts a bare string (wrapped to a one-tuple) and requires
absolute paths, reproducing the role's own gate.

`auth_method` `None` reproduces the role's `ansible_facts.fips | ternary(…)` default:
the `FipsEnabled` barrier supplies the flag, and `resolve_plan` picks `scram-sha-256`
under FIPS else `md5`. An explicit `auth_method` wins.

### 3.1 Item mapping fields

`postgresql_global_config_options`, `postgresql_hba_entries`, `postgresql_users`,
`postgresql_databases` and `postgresql_privs` items are frozen models with
`extra="allow"`, so the Ansible `.get(key, default)` semantics survive: only the keys
a template or a normalizer reads unconditionally are declared, and an absent optional
key stays *undefined* rather than rendering as `None`.

| Model | Required | Notes |
| --- | --- | --- |
| `PgConfigOption` | — | `option`/`value`/`state` validated by `resolve_plan` (which needs the whole-inventory `log_directory` cross-check); `state: absent` removes the line |
| `PgHbaEntry` | `type`, `database`, `user`, `auth_method` | the address/mask/options keys stay `extra` for the template's `\|default('')` branches |
| `PgUser` | `name` (non-empty) | `state`, `role_attr_flags`, `login_db`/`db`, `priv`, `login_user`, `port` stay `extra` |
| `PgDatabase` | `name` (non-empty) | `owner`, `template`, `encoding`, `lc_collate`, `lc_ctype`, `state` stay `extra` |
| `PgPriv` | — | the database is `login_db` *or* the `db` alias; `normalize_privs` reproduces `community.postgresql.postgresql_privs` argument handling, including the #29 scope rejection |

A default `global_config_options` is two items — `unix_socket_directories` and
`log_directory` — so the default run emits **two** `Configure global settings.`
transforms, one per lineinfile item, exactly like the role. `log_directory` is
mandatory when the option list is overridden: the role derives
`postgresql_effective_log_dir` from it, and `resolve_plan` raises if it is missing.

## 4. API

::: reemote.roles.postgresql
    options:
      show_root_heading: false
      show_root_toc_entry: false

## 5. Run it

The role is library code; `examples/roles/postgresql.py` is the runnable artifact — a
standalone program with its own `main()`, no shared runner. It loads and validates
`inventory.json` from the current working directory, resolves the role's variables
once (shared by both passes), then drives **two** `run_waves` passes, each a `dry=True`
one-wave preview followed by the real apply, printing the wave tree every time:

```python
from reemote import resolve_vars, run_waves, validate_inventory
from reemote.roles import PostgresqlVars, postgresql, postgresql_data

async def main() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    validate_inventory(inventory)
    vars_set = resolve_vars(PostgresqlVars, "postgresql_", inventory)

    deploy_a = postgresql(vars_set)                        # Pass A — root view
    report, _hosts = await run_waves(inventory, deploy_a, dry=True)
    report, _hosts = await run_waves(inventory, deploy_a)

    pg_inventory = [                                       # Pass B — postgres view
        {**entry, "privilege": {**entry.get("privilege", {}),
                                "sudo_user": vars_set[entry["host"]].user}}
        for entry in inventory
    ]
    deploy_b = postgresql_data(vars_set)
    report, _hosts = await run_waves(pg_inventory, deploy_b, dry=True)
    report, _hosts = await run_waves(pg_inventory, deploy_b)
```

From the repository root:

```bash
uv run python examples/roles/postgresql.py
```

The second inventory is a copy of the first with one added key per entry —
`privilege.sudo_user` naming that host's resolved `postgresql_user` (`postgres` by
default) — so pass B talks to exactly the same machines as pass A but runs the
catalog read and the users/databases/privileges operations as the OS `postgres` user.
Pass B must come *after* pass A has the service listening: peer authentication over
the unix socket needs a live server, which is why the `PostgresqlRoles` catalog read
is pass B's first barrier, not pass A's last.

Each `dry=True` pass is a one-wave preview: pass A parks on the `LinuxDistribution`
barrier (`barrier: LinuxDistribution → unknown until run`), pass B on the catalog
barrier, gathering nothing and applying nothing. A converged rerun of the whole
program is the honest preview.

`main()` maps the role's failure modes to a one-line stderr message and exit 1: a
missing/unreadable/invalid inventory (`OSError`, `JSONDecodeError`), a schema
violation (`InventoryError`), a rejected variable or a body's own hard error
(`ValueError` — the distribution is not RedHat, no vars are pinned for its major, a
config option is malformed, `initdb` exited non-zero — and `TypeError` for an
unreadable major version), and a host-level failure (`CommandError`,
`HostConnectionError`, `PrivilegeError` — the last usually meaning the
postgres-privilege view could not elevate).

**Run the program twice.** The second run must report no change and queue no handler:
the packages are installed, the cluster is already initialized (the `stat` gate finds
`PG_VERSION` and skips `initdb`), every rendered file is byte-identical, the unit is
running and enabled, and the users, databases and privileges already exist. That is
the migration's idempotence claim, and the offline suite asserts it across both
passes.

> **Shared demo host.** The shipped `inventory.json`'s `web1` / `web2` entries are in
> the `"RedHat"` family and are the ones this role acts on; hosts outside it emit
> nothing at all. To stop and disable the service again after a real run:
>
> ```bash
> ssh <user>@<redhat-host> 'sudo systemctl disable --now postgresql'
> ```

## 6. What it does

Two deploys on two host views. Each barrier closes its own wave, and a wave with no
records is not appended — so the wave count is **host-dependent**, which is the point
of the barrier model.

**Pass A — `postgresql` (root view):**

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `fact(LinuxDistribution)` | the `include_vars` dispatch key: the major selects `REDHAT_VARS[major]`, and a non-RedHat / Fedora / Amazon distribution raises before any request is built |
| 1 | `fact(FipsEnabled)` | the `auth_method` ternary; both facts close their own wave because the plan is rendered from what they return |
| 2 | `setup` (the two `dnf.packages`) + `initialize` (the profile install, the data directory) — closed by `checkpoint("initialize done")` | the checkpoint reads the wave it just closed and names the hosts whose profile write changed |
| 3 | `act(Shell, "test -e …/PG_VERSION", sudo=True)` | the `stat` gate: an elevated action, because the `0700 postgres` data dir cannot be traversed by the unprivileged `FileExists` fact. A barrier never raises on a non-zero exit — the `Result` is data |
| 4 | `act(Shell, "sudo -u postgres …/initdb -D …", sudo=True)` — **only when the gate's exit ≠ 0** | `initdb` stays in pass A: the elevation to root comes from the inventory, the drop to `postgres` is inside the command. A non-zero exit raises `ValueError` |
| 5 | `configure` (the log dir, one `files.transform` per config option, the `pg_hba.conf` install, the socket dirs) — closed by `checkpoint("configure done")` | the second checkpoint reads exactly this wave |
| 6 | `service` then `handler` | the notification union is `initialize \| configure`, both already applied, so the handler runs here — **before** the data plane — instead of after it |

**Pass B — `postgresql_data` (postgres view):**

| Wave | Item | Why |
| --- | --- | --- |
| 0 | `fact(PostgresqlRoles, psql_user=…, psql_host=…)` | a *reachability gate*, not a value the body consumes: peer auth needs a listening socket, so this fact fails fast if pass A left the service down |
| 1 | `users` (presence-only `postgresql.role`) → `databases` (`postgresql.database`, so an owning role exists) → `users_props` (the `role_attr_flags` reconcile + the `postgresql.privilege` grants) | no checkpoint — nothing reads an outcome, and the data plane never notifies the handler |

Three ordering rules are load-bearing:

* **Two passes, two views (§13).** Run-as users come from the inventory, never a
  request, and `run_waves` closes its host list in its own `finally` — so the
  privilege-view change is a second `run_waves` pass, not a mid-run switch.
* **Two `changed_hosts` reads over two checkpoints (§18.3).** `initialize` and
  `configure` each close a wave; the handler's notification set is their union, so a
  profile write, a config transform or an `pg_hba.conf` change all restart the daemon.
  The pass-A barrier sequence is exactly
  `[FactRequest, FactRequest, CheckpointRequest, ActionRequest, ActionRequest, CheckpointRequest]`.
* **The handler moves earlier.** Its union is entirely inside pass A, so it now runs
  *before* the users and databases instead of after them. No data request notifies, so
  the reordering is safe.

A host whose `host.family` is not `"RedHat"` returns before its first `yield` in
*both* passes, so it emits no requests, no barriers and no report lines at all.

## 7. Transcript

From `tests/roles/test_postgresql.py::test_tree_snapshot`, against a simulated CentOS
Stream 9 host (`web1`) plus an Ubuntu host the role never touches. The inventory sets
one database (`exampledb`), one user (`jdoe` with `role_attr_flags: CREATEDB`) and one
table grant (`SELECT`, `INSERT` on `public.accounts` to `jdoe`). Fresh host, **pass A
first run** — seven waves, `initdb` in wave 4 and the handler in wave 6:

```text
(root)
  wave 0
    barrier: LinuxDistribution → {'name': 'CentOS Stream', 'family': 'RedHat', 'major': 9, 'minor': None, 'release_meta': {}}
  wave 1
    barrier: FipsEnabled → False
  wave 2
    web1: Ensure PostgreSQL packages are installed. [changed: 3 package(s) to install]
        $ sudo dnf install -y --exclude=python-unversioned-command postgresql postgresql-server postgresql-contrib
    web1: Ensure PostgreSQL Python libraries are installed. [changed: 1 package(s) to install]
        $ sudo dnf install -y python3-psycopg2
    web1: Set PostgreSQL environment variables. [changed: content /etc/profile.d/postgres.sh is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-1e2908dc13b91103/content
        $ sudo sh -c 'mkdir -p /etc/profile.d && cp /tmp/reemote-install-1e2908dc13b91103/content /etc/profile.d/postgres.sh && chmod 644 /etc/profile.d/postgres.sh && rm -rf /tmp/reemote-install-1e2908dc13b91103'
    web1: Ensure PostgreSQL data directory exists. [changed: /var/lib/pgsql/data missing, will create]
        $ sudo mkdir -p /var/lib/pgsql/data
        $ sudo chmod 700 /var/lib/pgsql/data
        $ sudo chown postgres:postgres /var/lib/pgsql/data
    barrier: initialize done → 1 host notified
  wave 3
    barrier: stat PG_VERSION → {'exit_status': 1, 'stdout': '', 'stderr': ''}
  wave 4
    barrier: initdb → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 5
    web1: Ensure PostgreSQL log directory exists. [changed: /var/lib/pgsql/data/log missing, will create]
        $ sudo mkdir -p /var/lib/pgsql/data/log
        $ sudo chmod 700 /var/lib/pgsql/data/log
        $ sudo chown postgres:postgres /var/lib/pgsql/data/log
    web1: Configure global settings. [changed: content /var/lib/pgsql/data/postgresql.conf differs, will install]
        $ scp <files install> -> /tmp/reemote-install-e8bbcade32f985fa/content
        $ sudo sh -c 'mkdir -p /var/lib/pgsql/data && cp /tmp/reemote-install-e8bbcade32f985fa/content /var/lib/pgsql/data/postgresql.conf && chmod 644 /var/lib/pgsql/data/postgresql.conf && rm -rf /tmp/reemote-install-e8bbcade32f985fa'
    web1: Configure global settings. [changed: content /var/lib/pgsql/data/postgresql.conf differs, will install]
        $ scp <files install> -> /tmp/reemote-install-e8bbcade32f985fa/content
        $ sudo sh -c 'mkdir -p /var/lib/pgsql/data && cp /tmp/reemote-install-e8bbcade32f985fa/content /var/lib/pgsql/data/postgresql.conf && chmod 644 /var/lib/pgsql/data/postgresql.conf && rm -rf /tmp/reemote-install-e8bbcade32f985fa'
    web1: Configure host based authentication. [changed: content /var/lib/pgsql/data/pg_hba.conf differs, will install]
        $ scp <files install> -> /tmp/reemote-install-efb8487d6cac7f7b/content
        $ sudo sh -c 'mkdir -p /var/lib/pgsql/data && cp /tmp/reemote-install-efb8487d6cac7f7b/content /var/lib/pgsql/data/pg_hba.conf && chown postgres:postgres /var/lib/pgsql/data/pg_hba.conf && chmod 600 /var/lib/pgsql/data/pg_hba.conf && rm -rf /tmp/reemote-install-efb8487d6cac7f7b'
    web1: Ensure PostgreSQL unix socket dirs exist. [changed: /var/run/postgresql missing, will create]
        $ sudo mkdir -p /var/run/postgresql
        $ sudo chmod 755 /var/run/postgresql
        $ sudo chown postgres:postgres /var/run/postgresql
    barrier: configure done → 1 host notified
  wave 6
    web1: Ensure PostgreSQL is started and enabled on boot. [changed: postgresql.service not running (None), will start; postgresql.service not enabled, will enable]
        $ sudo systemctl start postgresql.service
        $ sudo systemctl enable postgresql.service
    web1: Restart postgresql (handler) [changed: postgresql.service already running; postgresql.service will restart (stop, sleep 5s, start)]
        $ sudo systemctl stop postgresql.service
        $ sudo sleep 5
        $ sudo systemctl start postgresql.service
```

The two `Configure global settings.` lines are the two default
`postgresql_global_config_options` (`unix_socket_directories`, `log_directory`), each
one `files.transform` on `postgresql.conf` — the role's lineinfile loop, faithfully.
The handler renders stop / `sleep 5` / start rather than one `systemctl restart`,
because `systemd.service(sleep=5)` reproduces the role's own `sleep:` value (#44).

**Pass B first run** — the catalog gate in wave 0, the data plane in wave 1:

```text
(root)
  wave 0
    barrier: read pg_roles catalog → {'postgres': {'super': True, 'inherit': True, 'createrole': True, 'createdb': True, 'canlogin': True, 'replication': True, 'connlimit': -1, 'password': None, 'valid_until': None, 'bypassrls': True, 'config': None, 'oid': 10}}
  wave 1
    web1: Ensure PostgreSQL users are present. [changed: postgresql role jdoe will be created]
        $ sudo psql -U postgres -h /var/run/postgresql -Ac 'CREATE ROLE "jdoe" LOGIN NOSUPERUSER INHERIT NOCREATEDB NOCREATEROLE NOREPLICATION'
    web1: Ensure PostgreSQL databases are present. [changed: postgresql database exampledb will be created]
        $ sudo psql -U postgres -h /var/run/postgresql -Ac 'CREATE DATABASE "exampledb" OWNER "postgres" TEMPLATE "template0" ENCODING '"'"'UTF8'"'"' LC_COLLATE '"'"'en_US.UTF-8'"'"' LC_CTYPE '"'"'en_US.UTF-8'"'"''
    web1: Ensure PostgreSQL users are configured correctly. [changed: postgresql role jdoe will be altered (createdb)]
        $ sudo psql -U postgres -h /var/run/postgresql -Ac 'ALTER ROLE "jdoe" CREATEDB'
    web1: Ensure PostgreSQL users privileges are configured correctly. [changed: postgresql will grant INSERT, SELECT to jdoe]
        $ sudo psql -d exampledb -U postgres -h /var/run/postgresql -Ac 'GRANT INSERT, SELECT ON TABLE "public"."accounts" TO "jdoe"'
```

The converged rerun — pass A drops the `initdb` wave (six waves, not seven: the `stat`
gate now exits `0`) and the handler wave disappears because both checkpoints find
nothing; pass B still reads the catalog (a read, not a cached value) and reconciles to
no change:

```text
(root)
  wave 0
    barrier: LinuxDistribution → {'name': 'CentOS Stream', 'family': 'RedHat', 'major': 9, 'minor': None, 'release_meta': {}}
  wave 1
    barrier: FipsEnabled → False
  wave 2
    web1: Ensure PostgreSQL packages are installed. [no change: all packages present]
    web1: Ensure PostgreSQL Python libraries are installed. [no change: all packages present]
    web1: Set PostgreSQL environment variables. [no change: content already up to date (/etc/profile.d/postgres.sh)]
    web1: Ensure PostgreSQL data directory exists. [no change: directory /var/lib/pgsql/data already exists]
    barrier: initialize done → 0 hosts notified
  wave 3
    barrier: stat PG_VERSION → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 4
    web1: Ensure PostgreSQL log directory exists. [no change: directory /var/lib/pgsql/data/log already exists]
    web1: Configure global settings. [no change: content already up to date (/var/lib/pgsql/data/postgresql.conf)]
    web1: Configure global settings. [no change: content already up to date (/var/lib/pgsql/data/postgresql.conf)]
    web1: Configure host based authentication. [no change: content already up to date (/var/lib/pgsql/data/pg_hba.conf)]
    web1: Ensure PostgreSQL unix socket dirs exist. [no change: directory /var/run/postgresql already exists]
    barrier: configure done → 0 hosts notified
  wave 5
    web1: Ensure PostgreSQL is started and enabled on boot. [no change: postgresql.service already running; postgresql.service already enabled]
```

```text
(root)
  wave 0
    barrier: read pg_roles catalog → {'postgres': {'super': True, 'inherit': True, 'createrole': True, 'createdb': True, 'canlogin': True, 'replication': True, 'connlimit': -1, 'password': None, 'valid_until': None, 'bypassrls': True, 'config': None, 'oid': 10}, 'jdoe': {'super': False, 'inherit': True, 'createrole': False, 'createdb': True, 'canlogin': True, 'replication': False, 'connlimit': -1, 'password': None, 'valid_until': None, 'bypassrls': False, 'config': None, 'oid': 11}}
  wave 1
    web1: Ensure PostgreSQL users are present. [no change: postgresql role jdoe exists and does not need updates]
    web1: Ensure PostgreSQL databases are present. [no change: postgresql database exampledb already exists with the same parameters; TEMPLATE cannot be changed on an existing database]
    web1: Ensure PostgreSQL users are configured correctly. [no change: postgresql role jdoe exists and does not need updates]
    web1: Ensure PostgreSQL users privileges are configured correctly. [no change: postgresql role jdoe already has the privileges]
```

The Ubuntu host appears in no tree of either pass: no barrier record, no request line.

## 8. Deliberate deviations from the Ansible role

| # | Deviation | Why |
| --- | --- | --- |
| D1 | A host outside the `"RedHat"` family emits **nothing at all**, in both passes | the family guard is the body's opening `if`, not a `when=` on each request. `include_vars`' OS dispatch collapses to the frozen `REDHAT_VARS` table keyed by the `LinuxDistribution` major; Fedora and Amazon are rejected (`ValueError`) because the role's own vars dispatch skips them |
| D2 | `initdb` stays in **pass A** as `act(Shell, command="sudo -u postgres …/initdb -D …", sudo=True)` | the elevation to root comes from the inventory and the drop to `postgres` is inside the command, so the doubled `sudo` is intentional. It has to sit between `initialize` and `configure`; putting it in pass B would mean a third pass |
| D3 | **The handler moves earlier** — before the users and databases | its notification union is `initialize \| configure`, both entirely inside pass A, so it runs at the end of pass A. No data request notifies, so the reordering is safe |
| D4 | The `stat` gate is an elevated `Shell` **action**, not a `FileExists` fact | the data directory is `0700 postgres`, so the unprivileged fact cannot traverse it (Ansible's `stat` ran under play-level `become`). A barrier never raises on a non-zero exit — the `Result` is data, and the exit status is the "already initialized" answer |
| D5 | The catalog re-read is **pass B's first barrier**, a reachability gate | peer authentication over the unix socket needs a live server, which only exists after pass A. It is never pass A's last barrier, because pass A runs on the root view |
| D6 | `template:` becomes a **control-side render**; `files.install` / `files.transform` take the rendered `str` | reemote has no controller filesystem paths inside a deploy. `pg_hba.conf.j2` uses the Ansible `comment` filter, registered by the shared environment; each `lineinfile` item becomes one `files.transform` on `postgresql.conf` |
| D7 | Credentials are **out of scope** | `password`/`encrypted`/`login_password`/`VALID UNTIL` are dropped and every connection rides peer auth over the unix socket as the OS `postgres` user. `login_host` is ignored (Ansible's `login_unix_socket` wins in psycopg2). `PGOPTIONS` and `no_log` are moot with no password |
| D8 | The role's implicit gates become **pydantic validation**; a duplicate user **fails fast** | the enums are `Literal` fields, an item's `name` is required, and a bad value raises `ValidationError` (a `ValueError`) while the inventory is resolved — before any host is contacted. A duplicate user name raises instead of Ansible's last-item-wins, and a `state: absent` user is not created first. `postgresql_db`'s `UTF-8` default is normalized to the catalog spelling `UTF8`; an exotic `postgresql_privs` type is rejected control-side citing #29 |

## 9. Scoped out

* **Non-RedHat families.** The role's Debian/Ubuntu branch (a different package set,
  the `version`-interpolated daemon name, `pg_createcluster`) is not migrated:
  `vars/RedHat-<major>.yml` becomes the frozen `REDHAT_VARS` table and `include_vars`'
  OS dispatch disappears. An Ubuntu host is skipped, not mapped to `apt` — that is a
  different role.
* **Credentials.** No `password`, `encrypted`, `login_password` or `VALID UNTIL`;
  every connection is peer auth over the unix socket as the OS `postgres` user (D7).
* **`fail_on_role` and the deprecated `priv` warning.** Absorbed (out of scope per
  closed issue #29); a library role does not print progress.
* **Exotic `postgresql_privs` object types.** Anything outside `table`, `schema`,
  `sequence`, `function`, `database`, `tablespace`, `default_privs` is rejected
  control-side, citing #29's deliberate scope boundary.
* **Per-host privilege switching inside one pass.** Run-as users come from the
  inventory and `run_waves` closes its host list in its own `finally`, so the root and
  `postgres` views are two passes, never a mid-run switch (§13).
