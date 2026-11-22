# PostgreSQL Ansible role → reemote

| | |
| --- | --- |
| Role | [`geerlingguy.postgresql`](https://github.com/geerlingguy/ansible-role-postgresql) |
| Program | [`ansible-role-postgresql.py`](https://github.com/kimjarvis/reemote/blob/main/examples/roles/ansible-role-postgresql/ansible-role-postgresql.py) |
| Target | **RedHat** — hosts in the `"RedHat"` OS family (CentOS Stream 9 or 10) |
| Inventory | [`inventory.json`](https://github.com/kimjarvis/reemote/blob/main/inventory.json) at the project root |
| Scope | the role's whole default RedHat path — setup, initialize, configure, service, users, databases, privileges, handler |
| Offline test | [`tests/test_roles_ansible_role_postgresql.py`](https://github.com/kimjarvis/reemote/blob/main/tests/test_roles_ansible_role_postgresql.py) |

This program installs and configures PostgreSQL the way the Ansible role
does: install the distribution packages (excluding
`python-unversioned-command`), write `/etc/profile.d/postgres.sh`, prepare
the data directory, run `initdb` as the `postgres` OS user when the cluster
is fresh, reconcile `postgresql.conf` options line-by-line, render
`pg_hba.conf`, ensure the unix socket directories, start and enable the
service, then manage databases, users (with partial `role_attr_flags`
reconciliation) and object privileges — restarting the service only on hosts
whose configuration actually changed. It is a reemote **deploy program**:
five deploys, two `Shell` actions, three fact gathers and one handler, with
the role's mutable-state reads moved into the barriers between runs.

Every role variable is read from the inventory entry's extra keys (reemote's
`host_vars` mechanism), so `postgresql_users`, `postgresql_databases`,
`postgresql_privs` and friends live beside `host`/`connection` in
`inventory.json`. The role's `include_vars` dispatch
(`vars/RedHat-<major>.yml`, skipped for Fedora/Amazon) collapses to a frozen
table resolved from a `LinuxDistribution` gather; the FIPS-dependent
auth-method default (`md5` vs `scram-sha-256`) resolves from a
`FipsEnabled` gather.

---

## 1. Prerequisites

### 1.1 Packages that must be loaded before executing

| Package | Why | How it is supplied |
| --- | --- | --- |
| **reemote** | the library itself | `uv run` resolves the project environment (reemote is built from this source tree). `uv sync` if you want it prepared up front. |
| **Jinja2** | the role's two templates (`postgres.sh.j2`, `pg_hba.conf.j2`) are pre-rendered control-side | `uv run --with jinja2 …` (see §2). It is also in the project's `dev` dependency group, so plain `uv run` inside a synced checkout works too. |
| Python ≥ 3.13 | `requires-python` in `pyproject.toml` | uv picks the project's interpreter (`.venv`). |

No PyYAML, no Ansible, no psycopg2 on the control machine: the templates are
rendered with Jinja2 alone (Ansible's `| comment` filter is registered by
hand), and reemote drives the target's `psql` CLI, not psycopg2. reemote's
own runtime dependencies — `asyncssh`, `distro`, `pydantic` — are installed
automatically.

### 1.2 Control machine

- **SSH access** to each RedHat host with the key named in its `connection`
  (`~/.ssh/id_ed25519` for the shipped `web1`/`web2` entries).
- No other egress is needed: the packages install from the target's own dnf
  repositories.

### 1.3 Target host

- CentOS Stream 9 or 10 (the pinned `REDHAT_VARS` table; the role's
  `postgresql_version` is informational on RedHat — 13 on EL9, 18 on EL10).
  `main()` verifies the gathered `LinuxDistribution` family is `RedHat`
  (and not Fedora/Amazon) and fails fast otherwise.
- **Elevation.** Every request carries `sudo=True`. The login user needs
  passwordless sudo (`NOPASSWD`), or the inventory entry must supply a
  password via a `privilege` section. The `initdb` action, the role-catalog
  gather and the users/databases/privileges deploy additionally run **as the
  `postgres` OS user**: the program derives a second inventory view with
  `privilege.sudo_user = "postgres"` (the role's `become_user`), so the login
  user must be allowed to `sudo -u postgres` as well.
- The `postgres` OS user/group exist once `postgresql-server` is installed
  (the package creates them); the data-directory request runs after the
  package deploy for that reason.
- `en_US.UTF-8` locale availability: the role's `postgresql_db` defaults
  (`lc_collate`/`lc_ctype`) need the locale present in glibc — stock on
  RedHat. Override per database item if yours differs.

### 1.4 Inventory: what you must add

**Nothing is mandatory.** The shipped `inventory.json` already has two
RedHat-family hosts (`web1`, `web2`); the role defaults apply as-is (install,
initialize, configure, start — no databases/users/privileges). Role variables
are optional extra keys on a RedHat-family entry, e.g.:

```json
{
  "host": "web1",
  "family": "RedHat",
  "connection": {
    "address": "192.168.0.159",
    "username": "supervisor",
    "key_path": "~/.ssh/id_ed25519"
  },
  "group": "centos",
  "postgresql_databases": [{ "name": "exampledb" }],
  "postgresql_users": [{ "name": "jdoe", "role_attr_flags": "CREATEDB" }],
  "postgresql_privs": [
    { "db": "exampledb", "roles": "jdoe", "privs": "ALL",
      "type": "table", "objs": "ALL_IN_SCHEMA" }
  ]
}
```

The other entries (`alpine`, `ubuntu`) need no changes. They are not in the
RedHat family, so `when=is_redhat` skips them and **they are never
contacted** — a skipped request is not materialized at all.

---

## 2. Running it against `inventory.json` with uv

From the project root:

```bash
uv run --with jinja2 python examples/roles/ansible-role-postgresql/ansible-role-postgresql.py
```

`uv run` builds/refreshes the project environment (reemote from source);
`--with jinja2` layers the template renderer on top (in a checkout synced
with the `dev` group, plain `uv run python …` also works). The program reads
`inventory.json` from the project root by path resolution
(`Path(__file__).resolve().parents[3]`), so it works from any working
directory as long as the file lives in the repository.

What you get, per deploy:

```
== Inventory loaded from /home/kim/reemote/inventory.json ==
alpine, web1, web2, ubuntu

== Skipped (when guard is_redhat): alpine, ubuntu ==
== Frozen PostgreSQL plans ==
web1: PostgreSQL 13 (EL9) -> /var/lib/pgsql/data (fresh, will initdb), auth=md5
web2: PostgreSQL 13 (EL9) -> /var/lib/pgsql/data (fresh, will initdb), auth=md5

== Deploy 1: install the PostgreSQL packages -- plan (prepare phase, --dry) ==
…format_plan…
== Deploy 1: install the PostgreSQL packages -- run log (apply phase) ==
…format_log…
…deploys 2–5, the initdb action and the handler…

== Done: N changed request(s) across 5 deploys + handler ==
```

Each deploy is **previewed then applied** in the same invocation:
`run(..., dry=True)` stops after the prepare phase (Ansible's `--check` as a
return value) and prints `format_plan`, then the real `run` prints
`format_log`. On a fresh host the packages install, `initdb` runs once as
postgres, the configuration is written and the handler restarts the service;
the databases/users/privileges deploy then talks to the running server over
the unix socket as postgres (peer auth).

**Run it twice.** The second invocation must report `0 changed request(s)`:
the packages are present, `PG_VERSION` exists (the initdb action is skipped),
the transforms are fixpoints, the rendered templates match, the service is
running/enabled, the role attributes and grants match — and the handler set
is empty. That is the migration's idempotence claim.

Failures are typed and printed to stderr with exit status 1 — no traceback:

| Error | Meaning |
| --- | --- |
| `OSError` / `JSONDecodeError` | `inventory.json` missing/unreadable/not JSON |
| `ValueError` | a role variable is invalid, the distribution is not RedHat/RHEL (or is Fedora/Amazon), the major has no pinned vars, an exotic `postgresql_privs` type (issue #29 boundary), or `initdb` exited non-zero |
| `TypeError` | a role variable has the wrong type, or the gathered facts carry no integer major version |
| `ImportError` | Jinja2 is missing — run with `--with jinja2` |
| `InventoryError` | the document parsed but violates the inventory schema (names the entry index) |
| `HostConnectionError` | SSH could not be established |
| `PrivilegeError` | sudo (including `sudo -u postgres`) rejected the credential |
| `CommandError` | a command exited non-zero (fail-fast, names host + command + stderr) |

### Offline verification (no hosts, no approval needed)

```bash
uv run pytest tests/test_roles_ansible_role_postgresql.py -q   # plan → apply → no-op re-apply
uv run pytest tests/ -q                                        # whole suite
uv run ruff check .
```

---

## 3. Role variables

`role_settings(host)` freezes `ROLE_DEFAULTS ⊕ host.extra`; `resolved_vars`
adds the frozen RedHat vars table and the gathered facts:

| Variable | Default | Effect |
| --- | --- | --- |
| `postgresql_enablerepo` | `""` (omit) | additive `--enablerepo` on both package installs (`dnf.packages(enable_repos=…)`, issue #32) |
| `postgresql_restarted_state` | `restarted` | the handler verb: `restarted` / `reloaded` / `started` |
| `postgresql_user` / `postgresql_group` | `postgres` | OS owner of the data/log/socket dirs and pg_hba.conf; the `become_user` run-as user |
| `postgresql_auth_method` | FIPS ternary | `md5`, or `scram-sha-256` when `FipsEnabled` (issue #30); an explicit value wins |
| `postgresql_unix_socket_directories` | `["/var/run/postgresql"]` | socket dirs to ensure (postgres:postgres, mode from the vars table); the first is the psql connection socket |
| `postgresql_service_state` / `postgresql_service_enabled` | `started` / `true` | the service task |
| `postgresql_global_config_options` | `unix_socket_directories` + `log_directory=log` | one `lineinfile`-equivalent transform per item; per-item `state: absent` removes every matching line |
| `postgresql_hba_entries` | the role's four fresh-install rows | rendered byte-exactly (including the template's extra spaces); empty list skips the pg_hba write |
| `postgresql_databases` | `[]` | `postgresql.database` per item (`lc_collate`/`lc_ctype` `en_US.UTF-8`, `encoding` `UTF-8`→`UTF8`, `template0`, owner `postgres`) |
| `postgresql_users` | `[]` | create-only pass + partial `role_attr_flags` reconcile (GAP-2 workaround); `state: absent` drops |
| `postgresql_privs` | `[]` | `postgresql.privilege` per item; `login_db`/`db` required, `ALL_IN_SCHEMA`/`ALL_DEFAULT` supported |
| `postgresql_packages`, `postgresql_daemon`, `postgresql_data_dir`, `postgresql_bin_path`, `postgresql_config_path`, `postgresql_version`, `postgresql_python_library`, `postgresql_unix_socket_directories_mode` | from `REDHAT_VARS` | the `vars/RedHat-<major>.yml` values, user-pinnable |

The pinned vars table (identical but for the informational version):

| EL major | version | data/config dir | bin | daemon | packages | socket mode | python lib |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 9 | 13 | `/var/lib/pgsql/data` | `/usr/bin` | `postgresql` | `postgresql`, `postgresql-server`, `postgresql-contrib` | `0755` | `python3-psycopg2` |
| 10 | 18 | `/var/lib/pgsql/data` | `/usr/bin` | `postgresql` | same | `0755` | `python3-psycopg2` |

A major outside this table is rejected before any host is mutated.

---

## 4. Program shape: five deploys and their barriers

reemote runs a deploy **twice** (prepare against pre-state, apply against live
state) and both passes must yield the identical request sequence. The role's
`stat` + `register` + `when` initdb gate, its `become_user: postgres` tasks
and its handler all read mutable state, so they move into the barriers:

```
load_inventory + validate ──► targets = family "RedHat" ──► role_settings (no host contacted)
│
├─ gather: LinuxDistribution (vars dispatch + family guard)
│          FipsEnabled (auth-method ternary, #30)
│          → resolved_vars + Jinja2 renders + conf transforms, frozen per host
├─ action: Shell "test -e <data_dir>/PG_VERSION" (sudo)   ← the stat register,
│          frozen as plan["initialized"]                     elevated (0700 dir)
│
├─ run 1  build_setup(plans)        dnf.packages ×2 (exclude=…, enable_repos=…)
├─ run 2  build_initialize(plans)   files.install(postgres.sh) [notify]
│                                   files.directory(data_dir, 0700 postgres)
├─ action: Shell initdb -D <data_dir>  (sudo -u postgres, only where fresh)
├─ run 3  build_configure(plans)    files.directory(log_dir)
│                                   files.transform(postgresql.conf) ×N [notify]
│                                   files.install(pg_hba.conf, 0600) [notify]
│                                   files.directory(socket dirs)
├─ run 4  build_service(plans)      systemd.service(running, enabled)
├─ gather: PostgresqlRoles (as postgres, peer-auth re-read #28)
│          → frozen role catalog (the GAP-2 fill source)
├─ run 5  compose(build_users, build_databases, build_users_props)(plans)
│          on the postgres-privilege inventory view:
│          postgresql.role (create) → postgresql.database → postgresql.role
│          (partial reconcile) + postgresql.privilege
└─ handler  changed_hosts(run2 ∪ run3, NOTIFY_RESTART)
           → service_handler(notified, ("postgresql",), action=restarted_state, sleep=5)
```

Why this order:

- **packages before the data directory** — the `postgres` OS user exists only
  after `postgresql-server` installs.
- **initdb between initialize and configure** — the conf file the transforms
  edit does not exist until `initdb` writes it; the log directory is created
  after initdb, exactly where the role creates it.
- **the catalog gather after the service deploy** — the server must answer;
  the frozen catalog is what makes the GAP-2 fill deterministic.
- **the handler last** — Ansible flushes handlers at the end of the play; the
  notified set is the union of the profile/conf/pg_hba changes.

---

## 5. Coverage table

Status vocabulary: **covered** (direct operation) · **recipe** (documented
shape change) · **workaround** (possible with a recorded deviation).

| Ansible task | reemote target | Status |
| --- | --- | --- |
| `include_vars: RedHat-<major>.yml` + `set_fact` defaults | frozen `REDHAT_VARS` + `resolved_vars` from `LinuxDistribution` | recipe |
| `yum: name={{ postgresql_packages }} exclude=python-unversioned-command enablerepo=…` | `dnf.packages(packages=…, exclude=…, enable_repos=…)` (#27, #32) | covered |
| `yum: name={{ postgresql_python_library }} enablerepo=…` | `dnf.packages(packages=(lib,), enable_repos=…)` | covered |
| `template: postgres.sh.j2 → /etc/profile.d/postgres.sh` `notify` | Jinja2 pre-render + `files.install` + named notify | recipe |
| `file: data_dir state=directory owner/group=postgres mode=0700` | `files.directory` | covered |
| `stat: PG_VERSION` + `register` | elevated `Shell` action (`test -e`), frozen (D3) | workaround |
| `command: initdb -D …` `when: not exists` `become_user: postgres` | `gather_action(Shell, sudo=True)` on the postgres-privilege view, gated on the frozen stat | recipe |
| `file: effective_log_dir state=directory 0700` | `files.directory` (leads the configure deploy) | covered |
| `lineinfile: postgresql.conf regexp=^#?opt.+$ line=opt='v' state=…` loop `notify` | one `files.transform` per option (last-match replace / absent removes all) | recipe |
| `template: pg_hba.conf.j2 0600 postgres:postgres` `when: entries` `notify` | Jinja2 pre-render (byte-exact) + `files.install` | recipe |
| `file: socket dirs owner/group=postgres mode=0755` loop | `files.directory` per dir | covered |
| `service: started enabled` | `systemd.service(running=…, enabled=…)` | covered |
| `postgresql_user` create pass (users.yml, no flags) | `postgresql.role` with CREATE USER defaults, skipped for catalog-present roles (GAP-2) | workaround |
| `postgresql_db` loop | `postgresql.database` (encoding normalized, D5) | covered |
| `postgresql_user` reconcile pass (`role_attr_flags`, `state`) | `postgresql.role` with the frozen-catalog fill (GAP-2) | workaround |
| `debug:` deprecated `priv` warning | control-side print | covered |
| `postgresql_privs` loop | `postgresql.privilege` (#29); exotic types rejected citing #29 | covered |
| handler `service: state=restarted sleep=5` | `service_handler(action=…, sleep=5)` — `systemd.service(sleep=)` splits the restart into stop / `sleep 5` / start (#44) | covered |
| `no_log`, `PGOPTIONS`, `ansible_ssh_pipelining`, `login_password` | dropped — credentials boundary / moot (D1, D2) | deviation |

---

## 6. Implementation gaps

Seven gaps this migration family found are **closed on `main`** — it uses the
native operations directly:
[#27](https://github.com/kimjarvis/reemote/issues/27) (`dnf.packages`
`exclude`), [#28](https://github.com/kimjarvis/reemote/issues/28) (PostgreSQL
facts' elevated peer-auth re-read),
[#29](https://github.com/kimjarvis/reemote/issues/29) (`postgresql.privilege`
parity), [#30](https://github.com/kimjarvis/reemote/issues/30)
(`server.FipsEnabled`), [#31](https://github.com/kimjarvis/reemote/issues/31)
(`postgresql.role` `bypassrls`),
[#32](https://github.com/kimjarvis/reemote/issues/32) (`dnf.packages`
`enable_repos`), and
[#44](https://github.com/kimjarvis/reemote/issues/44) (`systemd.service`
`sleep`) — closed after this program first landed, so the handler now passes
the role's own `sleep: 5` natively.

One genuine gap remains **open**; it is feature-only (the default path
migrates without it) and its workaround is tagged in the code for the second
pass:

| Issue | Gap | Workaround in this program |
| --- | --- | --- |
| [#45](https://github.com/kimjarvis/reemote/issues/45) — GAP-2 | `postgresql.role` reconciles all six boolean flags unconditionally; Ansible's `role_attr_flags` adjusts **only the listed flags** (an unlisted SUPERUSER survives a `CREATEDB`-only change) | gather `PostgresqlRoles` after the service deploy, freeze the catalog, fill every unlisted flag from the frozen current value (existing role) or CREATE USER's server defaults (absent role); presence-only items skip the attribute machinery |

The exotic `postgresql_privs` object types (procedure, language, group,
foreign_data_wrapper, foreign_server, type, parameter) are the **deliberate
scope boundary of closed #29**, not a new gap: an item naming one is rejected
control-side with a message citing #29.

---

## 7. Deliberate deviations from Ansible semantics

Every one is recorded in a comment at its site in the program.

| # | Deviation | Why |
| --- | --- | --- |
| D1 | `password` / `encrypted` / `login_password` / `VALID UNTIL` are dropped; `no_log` and `PGOPTIONS` are moot | the namespace credentials decision: the server cannot reveal a stored password, so a password option could never be idempotent. Every connection rides peer auth over the unix socket as the OS postgres user; provision credentials out of band (`postgresql.sql`) if needed |
| D2 | `login_host` is ignored; the connection is always the unix socket | Ansible's `login_unix_socket` wins over `login_host` in psycopg2, so the socket is the effective connection anyway; `psql_host=<socket dir>` reproduces it |
| D3 | The `stat` gate rides an elevated `Shell` action, not the `FileExists` fact | the data directory is `0700 postgres`: the unprivileged `FileExists` read cannot traverse it (Ansible's stat ran under play-level `become`). The action's exit status is frozen into the plan exactly like a register |
| D4 | `role_attr_flags` partial reconciliation is reproduced control-side (GAP-2, #45); duplicate user names fail fast; a `state: absent` user is not created first | the frozen-catalog fill makes the ALTER diff exactly the listed flags; Ansible's last-item-wins on duplicates is a configuration error here; the role's create-then-drop churn for absent users is pointless |
| D5 | `encoding: UTF-8` is normalized to `UTF8` | PostgreSQL accepts both on CREATE; the fact reports the catalog spelling `UTF8` |
| D6 | `fail_on_role` is absorbed; the deprecated `priv` warning still prints | explicitly out of scope per #29 |
| D7 | `become_user: postgres` becomes a second inventory view with `privilege.sudo_user` | run-as users come from the inventory, never from an operation — the deliberate reemote boundary |
| D8 | `ansible_ssh_pipelining` is moot | reemote owns its transport |
| D9 | The psycopg2 Python library is still installed | parity with the role (Ansible's modules need it); reemote itself drives the `psql` CLI |
| D10 | Non-RedHat-family hosts are skipped, not failed | the migration targets one OS; the shared inventory holds Alpine and Ubuntu hosts that must not be touched |

---

## 8. Offline verification

[`tests/test_roles_ansible_role_postgresql.py`](https://github.com/kimjarvis/reemote/blob/main/tests/test_roles_ansible_role_postgresql.py)
imports the example by path and replays `main()` barrier-for-barrier against
persistent `SimulatedHost`s — the real driver, prepare + apply, plus a
`dry=True` preview per deploy. It proves: the first pass installs the
packages, writes the profile/conf/pg_hba files, runs `initdb` exactly once as
postgres, creates the database/user/grants, and restarts the service only on
notified hosts; non-RedHat hosts are never touched; **the second pass reports
`changed=False` everywhere, runs no `initdb` and queues no handler** (the
elevated `test -e` stat probe still runs each pass — it is a read, not a
mutation); the GAP-2 fill leaves unlisted flags alone (a pre-existing
SUPERUSER survives a `CREATEDB`-only reconcile); and the transforms are
fixpoints.

The simulation limits bridged between passes (each a simulation limit, not a
library bug): the `initdb` `Shell` action mutates no simulated store, so
`bridge_initdb` plants `PG_VERSION`, a stock `postgresql.conf`/`pg_hba.conf`
and the bootstrap `postgres` catalog role after the action, and flips the stat
probe's `shell_results` answer to exit 0 so the second pass sees an initialized
cluster; the `become_user: postgres` view is replayed by temporarily swapping
`host.privilege` onto the one persistent host for the initdb action (the only
barrier where the run-as user changes a simulated observable — the rendered
shell key `sudo -u postgres …`); `install_file` records a root:root owner when
a request names none (a real `cp` over an existing file preserves its owner),
so the conf assertions check content/mode, not ownership; and an
`ALL IN SCHEMA` grant addresses the objects the grantee already holds grants
on (the simulation enumerates no object catalog), so that test seeds a prior
grant row. The `shell_results` seam is keyed by the exact rendered shell
(`sudo test -e …`, `sudo -u postgres /usr/bin/initdb -D …`), with `ran_shells`
asserting exactly-once.

`tests/test_examples.py` also scans the program statically (no
mutable-container literals in `op()`) and drains every single-snapshot
factory with `SNAPSHOT_ARGS` fixtures.

**Not yet run against a real host.** Doing so mutates the target and needs
explicit approval; the recipe is [§2](#2-running-it-against-inventoryjson-with-uv)
— run it, inspect the plans, then run it again and confirm
`0 changed request(s)`.