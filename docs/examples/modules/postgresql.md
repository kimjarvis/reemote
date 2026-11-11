# Module examples: postgresql

Scripts under `examples/modules/postgresql/` — one per fact or operation of
the [`postgresql`](../../modules/postgresql.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

Every script reads the shared project-root `inventory.json` and guards on
the `"centos"` group (`is_centos` on every operation request, a host-list
filter before each `gather_fact` fan-out). The shared file carries no
`privilege` section, so each operation script attaches
`{"sudo_user": "postgres"}` to the loaded `centos` entries and the
requests ride under `sudo`, so the `psql` client connects as the
`postgres` OS user through peer authentication — no password travels
through operation arguments, plans or logs (the baseline's `psql_password`
option and `postgresql.role`'s `password` parameter are deliberately
dropped namespace-wide).

The read-only fact-gathering examples (`postgresql_postgresql_roles.py`
reading `PostgresqlRoles`, `postgresql_postgresql_databases.py` reading
`PostgresqlDatabases`, `postgresql_postgresql_role_memberships.py` reading
`PostgresqlRoleMemberships`, `postgresql_postgresql_privileges.py` reading
`PostgresqlPrivileges`, `postgresql_postgresql_schemas.py` reading
`PostgresqlSchemas`, `postgresql_postgresql_tables.py` reading
`PostgresqlTables`, `postgresql_postgresql_indexes.py` reading
`PostgresqlIndexes`, `postgresql_postgresql_extensions.py` reading
`PostgresqlExtensions`, `postgresql_postgresql_settings.py` reading
`PostgresqlSettings`, `postgresql_postgresql_tablespaces.py` reading
`PostgresqlTablespaces`, `postgresql_postgresql_replication.py` reading
`PostgresqlReplication`, `postgresql_postgresql_connections.py` reading
`PostgresqlConnections`, `postgresql_postgresql_publications.py` reading
`PostgresqlPublications`, and `postgresql_postgresql_subscriptions.py`
reading `PostgresqlSubscriptions`, each over `gather_fact`) are not
rendered here — they read a single fact, and the facts themselves are
documented on the [`postgresql` module page](../../modules/postgresql.md).

**Host prerequisite.** Every script here needs a running PostgreSQL server.
On the demo CentOS hosts the packages install cleanly
(`sudo dnf install -y postgresql-server postgresql-contrib`), but the cluster
cannot be initialized under their `staff_t`-confined sudo:

- `sudo postgresql-setup --initdb` fails with `runuser: cannot set user id:
  Operation not permitted` — `staff_t` may not setuid to `postgres`.
- `sudo -u postgres initdb -D /var/lib/pgsql/data` fails with
  `/usr/bin/postgres: Permission denied` — `staff_t` may not execute the
  `postgresql_exec_t` binary (no transition into `postgresql_t`).

With no initialized data directory, `postgresql.service`'s
`ExecStartPre=postgresql-check-db-dir` fails ("Directory ... is missing or
empty") and the server never starts, so `/var/run/postgresql/.s.PGSQL.5432`
does not exist. Both the read-only fact scripts (which run `psql` as the login
user) and the operations (which ride `sudo -u postgres`) therefore have no
socket to reach. This is a stricter blocker than MySQL, where the server at
least starts and only the client socket connection is denied.

The scripts are compile-checked and exercised by the offline harness
(`tests/test_examples.py`), not run against the shared demo hosts. To run them
live, relax the demo hosts' sudo confinement (permissive/unconfined, then
`sudo postgresql-setup --initdb` and `sudo systemctl start postgresql`), or
point `INVENTORY_PATH` at your own file of PostgreSQL machines whose
`pg_hba.conf` admits
the login user (facts) and the `postgres` OS user (operations).

## Managing a database

The database operation: `postgresql.database` mirrors the baseline's
`postgresql.database` and extends it beyond creation. It reads
[`PostgresqlDatabases`][reemote.modules.postgresql.postgresql.PostgresqlDatabases]
to decide: an absent database yields one `CREATE DATABASE` carrying
`owner`, `template`, `encoding`, the `lc_*` options, `tablespace`,
`connection_limit` and `allow_connections`; an existing one reconciles
`OWNER`, `SET TABLESPACE`, `CONNECTION LIMIT` and `ALLOW_CONNECTIONS`
through `ALTER DATABASE` only where they differ, reporting the
creation-only options (template, encoding, locales) as skipped rather than
pretending to change them. Tablespaces resolve by NAME against
[`PostgresqlTablespaces`][reemote.modules.postgresql.postgresql.PostgresqlTablespaces]'
OIDs — fixing the baseline's comparison of the name against the OID column,
which never matched. `present=False` drops the database. The script yields
the same create request twice — the second is a guaranteed no-op in the
apply phase, because the first create is visible to it through the fresh
per-step fact cache (in the prepare phase, where nothing is sent, both plan
as changes).

::: postgresql_database
    options:
      members: []

## Managing a role

The role operation: `postgresql.role` mirrors the baseline's
`postgresql.role` and extends it beyond creation. It reads
[`PostgresqlRoles`][reemote.modules.postgresql.postgresql.PostgresqlRoles]
to decide: an absent role yields one `CREATE ROLE` rendering every flag
explicitly as X/NOX pairs (`SUPERUSER`, `CREATEDB`, `CREATEROLE`, `INHERIT`,
`LOGIN`, `REPLICATION`) plus `CONNECTION LIMIT`; an existing
one reconciles the flags and the connection limit through `ALTER ROLE`
only where they differ. The baseline's `password` parameter is deliberately
dropped — credentials never travel through operation arguments, plans or
logs. `present=False` drops the role. The script yields the same create
request twice — the second is a guaranteed no-op in the apply phase,
because the first create is visible to it through the fresh per-step fact
cache (in the prepare phase, where nothing is sent, both plan as changes).

::: postgresql_role
    options:
      members: []

## Executing arbitrary SQL

The SQL operation: `postgresql.sql` mirrors the baseline's `postgresql.sql`,
executing a statement through `psql -Ac`. It is stateless — the statement
always runs, in both the plan's apply phase and every subsequent run — so
it is the escape hatch for anything the typed operations do not cover;
prefer a fact-comparing operation when one exists, and a statement that is
naturally idempotent (`CREATE ... IF NOT EXISTS`, `ALTER ... SET`) when it
does not. The script yields the same idempotent statement twice.

::: postgresql_sql
    options:
      members: []

## Dumping a database

The dump operation: `postgresql.dump` mirrors the baseline's `postgresql.dump`,
writing a `pg_dump` of a database to a file on the TARGET host (`dest`).
It is stateless — the dump always runs, overwriting the destination — with
`pg_dump` carrying the connection options. The script yields one dump
request writing under `/var/lib/postgresql`, where the `postgres` user the
request rides under can write.

::: postgresql_dump
    options:
      members: []

## Loading a SQL file

The load operation: `postgresql.load` mirrors the baseline's `postgresql.load`,
feeding a SQL file through `psql`. It is stateless — the file always
executes — and `src` is a path on the TARGET host: spool controller-side
bytes there with `files.put` first. The script pairs a dump with a load of
its own output so the round-trip is self-contained.

::: postgresql_load
    options:
      members: []

## Granting or revoking privileges

The privilege operation: `postgresql.privilege` is a reemote-original
operation modeled on Ansible's `postgresql_privs`, granting or revoking
object privileges idempotently. It reads
[`PostgresqlPrivileges`][reemote.modules.postgresql.postgresql.PostgresqlPrivileges]
for the grantee's current grants and touches only the difference:
`present=True` grants just the missing privileges (`ALL` expands to the
object type's full set), `present=False` revokes just the held ones, and
`grant_option` upgrades to `WITH GRANT OPTION` or withdraws only the
option. Objects are named by `objects` (bare or `schema.name`) or by
`all_in_schema`, which addresses every object of the type the grantee
already holds grants on. The script yields the same grant request twice —
the second is a guaranteed no-op in the apply phase, because the first
grant is visible to it through the fresh per-step fact cache (in the
prepare phase, where nothing is sent, both plan as changes).

::: postgresql_privilege
    options:
      members: []

## Managing role membership

The membership operation: `postgresql.membership` is a reemote-original
operation modeled on Ansible's `postgresql_membership`, managing
`GRANT <target_role> TO <role>` idempotently. It reads
[`PostgresqlRoleMemberships`][reemote.modules.postgresql.postgresql.PostgresqlRoleMemberships]
for the pair's current state: `present=True` grants the membership (or
upgrades it to `WITH ADMIN OPTION` when `admin_option=True` and the option
is missing), `admin_option=False` on an existing admin grant withdraws
just the option (the membership is kept), and
`present=False` revokes the membership. The script yields the same grant
request twice — the second is a guaranteed no-op in the apply phase,
because the first grant is visible to it through the fresh per-step fact
cache (in the prepare phase, where nothing is sent, both plan as changes).

::: postgresql_membership
    options:
      members: []

## Managing a schema

The schema operation: `postgresql.schema` is a reemote-original operation
modeled on Ansible's `postgresql_schema`, managing a schema inside the
database named by `psql_database`. It reads
[`PostgresqlSchemas`][reemote.modules.postgresql.postgresql.PostgresqlSchemas]
to decide: an absent schema yields `CREATE SCHEMA` (with `AUTHORIZATION`
when `owner` is given), an existing one re-owns through
`ALTER SCHEMA ... OWNER TO` only when the owner differs, and
`present=False` drops it (`cascade=True` appends `CASCADE`). The script
yields the same create request twice — the second is a guaranteed no-op in
the apply phase, because the first create is visible to it through the
fresh per-step fact cache (in the prepare phase, where nothing is sent,
both plan as changes).

::: postgresql_schema
    options:
      members: []

## Managing an extension

The extension operation: `postgresql.extension` is a reemote-original
operation modeled on Ansible's `postgresql_ext`, installing an extension
into the database named by `psql_database`. It reads
[`PostgresqlExtensions`][reemote.modules.postgresql.postgresql.PostgresqlExtensions]
to decide: an absent extension yields `CREATE EXTENSION` (with `SCHEMA`
and `VERSION` when given), an installed one reconciles a schema move
(`ALTER EXTENSION ... SET SCHEMA`) and a version update
(`ALTER EXTENSION ... UPDATE TO`) only where they differ, and
`present=False` drops it (`cascade=True` appends `CASCADE`). The script
yields the same install request twice — the second is a guaranteed no-op
in the apply phase, because the first install is visible to it through the
fresh per-step fact cache (in the prepare phase, where nothing is sent,
both plan as changes).

::: postgresql_extension
    options:
      members: []

## Managing an index

The index operation: `postgresql.index` is a reemote-original operation
modeled on Ansible's `postgresql_idx`, managing an index in the database
named by `psql_database`. It reads
[`PostgresqlIndexes`][reemote.modules.postgresql.postgresql.PostgresqlIndexes]
to decide: an absent index yields `CREATE INDEX` (or
`CREATE UNIQUE INDEX`, `CONCURRENTLY` with `concurrent=True`, a named
`method`, `tablespace` and `where` clause), and `present=False` drops it.
An existing index whose table, uniqueness, method or named tablespace
differs raises `OperationError` rather than silently rebuilding a possibly
constraint-backing index — drop it first to rebuild. The script yields the
same create request twice — the second is a guaranteed no-op in the apply
phase, because the first create is visible to it through the fresh
per-step fact cache (in the prepare phase, where nothing is sent, both
plan as changes).

::: postgresql_index
    options:
      members: []

## Tuning a server setting

The setting operation: `postgresql.setting` is a reemote-original
operation modeled on Ansible's `postgresql_set`, tuning a GUC through
`ALTER SYSTEM SET`/`RESET` — which writes `postgresql.auto.conf`,
overriding the main configuration file (the config-file-rewrite apply
method is deliberately dropped). It reads
[`PostgresqlSettings`][reemote.modules.postgresql.postgresql.PostgresqlSettings]
to compare the requested value against the parameter's current `setting`
as text in the parameter's own unit — there is no unit normalization, so
pass `262144` rather than `2GB` for `shared_buffers` in 8kB blocks;
`present=False` resets the parameter, a no-op when its source is
already `default`. `reload=True` appends `SELECT pg_reload_conf()` after a
change (never after a no-op); parameters needing a restart carry
`pending_restart` in the fact until the server restarts. The script yields
the same set request twice — the second is a guaranteed no-op in the apply
phase, because the first write is visible to it through the fresh
per-step fact cache (in the prepare phase, where nothing is sent, both
plan as changes).

::: postgresql_setting
    options:
      members: []

## Managing a tablespace

The tablespace operation: `postgresql.tablespace` is a reemote-original
operation modeled on Ansible's `postgresql_tablespace`, managing a
tablespace's lifecycle. It reads
[`PostgresqlTablespaces`][reemote.modules.postgresql.postgresql.PostgresqlTablespaces]
to decide: an absent tablespace yields `CREATE TABLESPACE ... LOCATION`
(with `OWNER` when given — the directory must already exist, be empty and
be owned by the server user, so prepare it out of band first),
an existing one re-owns only when the owner differs, and `present=False`
drops it. A location change on an existing tablespace raises
`OperationError` — PostgreSQL cannot move one; drop it first. The script
yields the same create request twice — the second is a guaranteed no-op
in the apply phase, because the first create is visible to it through the
fresh per-step fact cache (in the prepare phase, where nothing is sent,
both plan as changes).

::: postgresql_tablespace
    options:
      members: []

## Managing a publication

The publication operation: `postgresql.publication` is a
reemote-original operation managing logical-replication publications in
the database named by `psql_database`. It reads
[`PostgresqlPublications`][reemote.modules.postgresql.postgresql.PostgresqlPublications]
to decide: an absent publication yields `CREATE PUBLICATION ... FOR TABLE`
(the table set sorted and schema-qualified) or `FOR ALL TABLES`, followed
by an owner `ALTER` when `owner` is given; an existing one reconciles its
table set and owner through `ALTER PUBLICATION` only where they differ.
Switching between `FOR TABLE` and `FOR ALL TABLES` raises
`OperationError` — drop the publication first. `present=False` drops it.
The script yields the same create request twice — the second is a
guaranteed no-op in the apply phase, because the first create is visible
to it through the fresh per-step fact cache (in the prepare phase, where
nothing is sent, both plan as changes).

::: postgresql_publication
    options:
      members: []

## Managing a subscription

The subscription operation: `postgresql.subscription` is a
reemote-original operation managing logical-replication subscriptions in
the database named by `psql_database`. It reads
[`PostgresqlSubscriptions`][reemote.modules.postgresql.postgresql.PostgresqlSubscriptions]
to decide: an absent subscription yields `CREATE SUBSCRIPTION ...
CONNECTION '<dsn>' PUBLICATION <names>` (with `WITH (enabled = false)`
when `enabled=False`), and an existing one reconciles its publication list
and enabled flag through `ALTER SUBSCRIPTION` only where they differ. The
connection string is never compared — the fact deliberately hides
`subconninfo` because it can embed credentials — so changing the DSN needs
a drop and re-create. `present=False` drops it. The script yields the same
create request twice with `enabled=False`, so the subscription's apply
worker stays stopped — the publisher must still be reachable at creation
time, because PostgreSQL opens the connection to create the replication
slot. The second is a guaranteed no-op in the apply phase, because
the first create is visible to it through the fresh per-step fact cache
(in the prepare phase, where nothing is sent, both plan as changes).

::: postgresql_subscription
    options:
      members: []