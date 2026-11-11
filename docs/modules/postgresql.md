# postgresql module

::: reemote.modules.postgresql.postgresql
    options:
      show_root_heading: false
      members:
        - PostgresqlConnection
        - PostgresqlConnections
        - PostgresqlDatabase
        - PostgresqlDatabases
        - PostgresqlExtension
        - PostgresqlExtensions
        - PostgresqlIndex
        - PostgresqlIndexes
        - PostgresqlPrivilege
        - PostgresqlPrivileges
        - PostgresqlPublication
        - PostgresqlPublications
        - PostgresqlReplication
        - PostgresqlReplicationClient
        - PostgresqlRole
        - PostgresqlRoleMembership
        - PostgresqlRoleMemberships
        - PostgresqlRoles
        - PostgresqlSchema
        - PostgresqlSchemas
        - PostgresqlSetting
        - PostgresqlSettings
        - PostgresqlSubscription
        - PostgresqlSubscriptions
        - PostgresqlTable
        - PostgresqlTables
        - PostgresqlTablespace
        - PostgresqlTablespaces
        - database
        - dump
        - extension
        - index
        - load
        - membership
        - privilege
        - publication
        - role
        - schema
        - setting
        - sql
        - subscription
        - tablespace




<!---

`reemote.modules.postgresql.postgresql` combines the PostgreSQL facts and
operations in a single source, extending the baseline's two-fact/five-operation
`postgresql` namespace toward complete control over and visibility into a
cluster. Every fact and operation runs through the `psql` client on the
target host (`psql [-d db] [-U user] [-h host] [-p port] -Ac '<sql>'`), so
the connecting role comes from the OS user the request rides under — flag
requests `sudo=True` with the inventory `privilege.sudo_user = "postgres"`
for peer authentication, or point `psql_host`/`psql_user` at a TCP login
backed by a host-side `.pgpass`. The baseline's `psql_password` option (and the
`PGPASSWORD=` prefix it renders) is deliberately dropped namespace-wide,
as is `postgresql.role`'s `password` parameter: credentials never travel
through operation arguments, plans or logs.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a
generator — implemented as a `process` coroutine returning the raw value
(over asyncssh in the real implementation). Read facts through
[`FactCache.get`][reemote.core.FactCache.get]. The cluster-wide facts
(`PostgresqlRoles`, `PostgresqlDatabases`, `PostgresqlRoleMemberships`,
`PostgresqlSettings`, `PostgresqlTablespaces`, `PostgresqlReplication`,
`PostgresqlConnections`) take only the four connection options; the
database-scoped facts (`PostgresqlSchemas`, `PostgresqlTables`,
`PostgresqlIndexes`, `PostgresqlExtensions`, `PostgresqlPrivileges`,
`PostgresqlPublications`, `PostgresqlSubscriptions`) read the database
named by `psql_database` (the server's default, `postgres`, when unset).

`PostgresqlRoles` and `PostgresqlDatabases` mirror the baseline's
`postgres.PostgresRoles` / `PostgresDatabases` verbatim: the same
`pg_catalog.pg_roles` / `pg_catalog.pg_database` selects (the database one
joined to `pg_get_userbyid(datdba)` for the owner name), the same
name-keyed dicts of catalog columns, with `t`/`f` coerced to bools and the
`(N rows)` trailer dropped by pattern rather than by chopping the last
line — so empty output reads as `{}` instead of raising.

The twelve reemote-original facts answer the visibility gaps the baseline
leaves: `PostgresqlRoleMemberships` (role/member/admin-option rows from
`pg_auth_members`), `PostgresqlPrivileges` (one ACL row per grant —
object type, schema, name, grantee, privilege, grantable — expanded from
the `aclitem[]` columns of databases, schemas, tables, sequences and
functions, so a NULL ACL expands to no rows), `PostgresqlSchemas`
(user schemas and owners from `pg_namespace`), `PostgresqlTables` and
`PostgresqlIndexes` (names, owners, tablespaces and row estimates /
uniqueness and access method from `pg_tables` / `pg_indexes` joined to
`pg_class`), `PostgresqlExtensions` (installed extension names, versions
and schemas from `pg_extension`), `PostgresqlSettings` (every GUC's
value, unit, source and pending-restart flag from `pg_settings`),
`PostgresqlTablespaces` (names, owners, OIDs and filesystem locations from
`pg_tablespace`), `PostgresqlReplication` (connected standbys, state,
sync mode and replay lag from `pg_stat_replication`),
`PostgresqlConnections` (pid, database, user, client address and state
from `pg_stat_activity` — the query text column is deliberately NOT
selected, it can embed credentials), `PostgresqlPublications` and
`PostgresqlSubscriptions` (logical-replication publications with their
table sets, subscriptions with their publication lists and enabled flag
— `subconninfo` is deliberately NOT selected for the same credential
reason).

## Operations

An operation is an async generator: it reads facts through
[`FactCache.get`][reemote.core.FactCache.get], decides whether a change
is needed, records `ctx.changed`/`ctx.reason`, and yields one typed
command descriptor per mutation — never touching the host itself. Reach
operations through the namespace (`postgresql.database`), not the
top-level package.

The five the baseline-parity operations keep their names and shells:
`postgresql.sql` executes arbitrary SQL (stateless, always yields — the
escape hatch for anything the typed operations do not cover),
`postgresql.dump` writes a `pg_dump` of a database to a host-side file
and `postgresql.load` feeds a host-side SQL file back through `psql`
(both stateless; `load`'s `src` is a path on the TARGET host — spool
controller-side bytes with `files.put` first). `postgresql.database` and
`postgresql.role` are reworked beyond the baseline: the database operation
resolves tablespaces by NAME against `PostgresqlTablespaces`' OIDs
(the baseline compared the name against the OID column, which never matched)
and alters an existing database's `OWNER`, `SET TABLESPACE`,
`CONNECTION LIMIT` and `ALLOW_CONNECTIONS` idempotently, reporting
creation-only options (TEMPLATE/ENCODING/LC_*) as skipped rather than
pretending to change them; the role operation renders every flag
explicitly as X/NOX pairs (so `INHERIT` appears where the baseline omitted
it) and reconciles an existing role's flags and connection limit through
`ALTER ROLE`.

The nine reemote-original operations close the control gaps, modeled on
the Ansible `community.postgresql` parameter shapes:
`postgresql.privilege` grants/revokes object privileges (only missing
privileges are granted, only held ones revoked; `grant_option` upgrades
to WITH GRANT OPTION or withdraws just the option; `ALL` expands to the
type's full privilege set; `all_in_schema` addresses every object of the
type the grantee holds grants on), `postgresql.membership` manages
`GRANT role TO member` with the admin option reconciled both ways,
`postgresql.schema` creates/re-owns/drops schemas,
`postgresql.extension` installs extensions and reconciles schema moves
and version updates, `postgresql.index` creates/drops indexes
(`CONCURRENTLY` supported; a differing definition raises
[`OperationError`][reemote.core.OperationError] rather than silently
rebuilding a possibly constraint-backing index), `postgresql.setting`
tunes GUCs through `ALTER SYSTEM SET`/`RESET` with an optional
`SELECT pg_reload_conf()` follow-up (the config-file-rewrite apply method
is deliberately dropped — ALTER SYSTEM writes postgresql.auto.conf,
which overrides the main file), `postgresql.tablespace` manages
tablespace locations (a location change on an existing tablespace raises
`OperationError` — PostgreSQL cannot move one), and
`postgresql.publication`/`postgresql.subscription` manage logical
replication (switching between FOR TABLE and FOR ALL TABLES raises
`OperationError`; subscription connection strings are never compared
because the fact hides conninfo).

## Example

The read-only fact-gathering scripts `postgresql_postgresql_roles.py`,
`postgresql_postgresql_databases.py`,
`postgresql_postgresql_role_memberships.py`,
`postgresql_postgresql_privileges.py`, `postgresql_postgresql_schemas.py`,
`postgresql_postgresql_tables.py`, `postgresql_postgresql_indexes.py`,
`postgresql_postgresql_extensions.py`,
`postgresql_postgresql_settings.py`,
`postgresql_postgresql_tablespaces.py`,
`postgresql_postgresql_replication.py`,
`postgresql_postgresql_connections.py`,
`postgresql_postgresql_publications.py` and
`postgresql_postgresql_subscriptions.py` under
[`examples/modules/postgresql/`](../examples/modules/postgresql.md)
gather the fourteen facts respectively across an inventory with
[`gather_fact`][reemote.drivers.gather_fact] and print each host's
result as JSON.

The operation scripts (`postgresql_database.py`, `postgresql_role.py`,
`postgresql_sql.py`, `postgresql_dump.py`, `postgresql_load.py`,
`postgresql_privilege.py`, `postgresql_membership.py`,
`postgresql_schema.py`, `postgresql_extension.py`, `postgresql_index.py`,
`postgresql_setting.py`, `postgresql_tablespace.py`,
`postgresql_publication.py`, `postgresql_subscription.py`) under the same
directory drive each operation through the two-phase
[`run`][reemote.drivers.run] — a dry-run plan then a real apply — over an
inventory, yielding the same request twice so the second is a guaranteed
apply-phase no-op.

--->
