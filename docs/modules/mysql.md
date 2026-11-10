# mysql module

::: reemote5.modules.mysql.mysql
    options:
      show_root_heading: false
      members:
        - MysqlBinaryLog
        - MysqlBinaryLogs
        - MysqlColumn
        - MysqlColumns
        - MysqlConnection
        - MysqlConnections
        - MysqlDatabase
        - MysqlDatabases
        - MysqlEngine
        - MysqlEngines
        - MysqlIndex
        - MysqlIndexes
        - MysqlPlugin
        - MysqlPlugins
        - MysqlReplicaStatus
        - MysqlReplication
        - MysqlStatus
        - MysqlTable
        - MysqlTables
        - MysqlUser
        - MysqlUserGrants
        - MysqlUsers
        - MysqlVariables
        - database
        - dump
        - index
        - kill
        - load
        - optimize
        - plugin
        - privileges
        - replica
        - role
        - sql
        - table
        - user
        - variable




<!---

`reemote5.modules.mysql.mysql` combines the MySQL facts and operations in a
single source, extending the baseline's three-fact/six-operation `mysql`
namespace toward complete control over and visibility into a server. Every
fact and operation runs through the `mysql` client on the target host
(`mysql [--defaults-extra-file=<file>] [database] [-u user] [-h host]
[-P port] -Be '<sql>'` — the option file first, because MySQL requires
`--defaults-extra-file` to be the very first option), so the connecting
account comes from the OS user the request rides under — flag requests
`sudo=True` for root socket authentication, or rely on a host-side
`~/.my.cnf` or the `mysql_defaults_file` option file for a TCP login.
the baseline's `mysql_password` option (and the `-p<password>` it renders into
the command line, visible in process lists and logs) is deliberately
dropped namespace-wide, as is `mysql.user`'s `password` parameter
(replaced by the comparable `auth_plugin`): credentials never travel
through operation arguments, plans or logs.

## Facts

A fact is an asynchronous, queryable resource — never yielded by a
generator — implemented as a `process` coroutine returning the raw value
(over asyncssh in the real implementation). Read facts through
[`FactCache.get`][reemote5.core.FactCache.get]. The server-wide facts take
only the five connection options (`mysql_user`, `mysql_host`, `mysql_port`,
`mysql_database`, `mysql_defaults_file`); the scoped facts additionally
take their target positionally: `MysqlTables(database)`,
`MysqlColumns(database, table)`, `MysqlIndexes(database, table=None)` and
`MysqlUserGrants(user, hostname="localhost")`.

`MysqlDatabases`, `MysqlUsers` and `MysqlUserGrants` mirror the baseline's
`mysql.MysqlDatabases` / `MysqlUsers` / `MysqlUserGrants`: the same
`information_schema.SCHEMATA` / `mysql.user` selects and the same
`SHOW GRANTS FOR` parsed with the baseline's grant regex (`WITH GRANT OPTION`
folded in as a `GRANT OPTION` privilege, the `|| true` guard standing in
for the baseline's `ignore_errors` — a missing user reads as `{}`).
`MysqlDatabases` keeps the name-keyed mapping of
character-set/collation pairs; `MysqlUsers` keys `user@host`, folds the
`*_priv` columns into a sorted `privileges` list, coerces the `max_*`
limits and the Y/N flags, and skips the anonymous account — the column
set follows the server version/flavour, so `MysqlUser` is a `total=False`
TypedDict carrying whatever columns the server reports. Two deliberate
deviations in `MysqlUserGrants`: the privilege values are sorted LISTS
rather than sets (JSON-printable, deterministic in reasons), and
role-grant lines — which the baseline's regex skips — are additionally
captured under their backticked ``role``@``host`` key with the single
privilege `ROLE`, so `mysql.role` can check membership idempotently.

The ten reemote5-original facts answer the visibility gaps the baseline
leaves: `MysqlBinaryLogs` (the binlog file list and sizes from
`SHOW BINARY LOGS`, `|| true`-guarded so a server with binary logging
disabled reads as `[]`), `MysqlColumns` (types, nullability, defaults,
keys and comments from `information_schema.COLUMNS`, ordered by ordinal
position), `MysqlConnections` (who is connected, from where, for how
long and in what state from `information_schema.PROCESSLIST` — the
`INFO` query-text column is deliberately NOT selected, it can embed
credentials), `MysqlEngines` (storage engines and their
transaction/XA/savepoint capabilities from `information_schema.ENGINES`),
`MysqlIndexes` (one entry per index with its column list, uniqueness and
access type, grouped from `information_schema.STATISTICS`),
`MysqlPlugins` (installed plugins, status, type and load library from
`information_schema.PLUGINS`), `MysqlReplication` (one entry per
configured channel from `SHOW REPLICA STATUS` — MySQL 8.0.22+ syntax, the
legacy `SHOW SLAVE STATUS` spelling is deliberately not targeted; a
non-replica reads as `[]`, and only the connection/thread/lag/error
columns are kept, the GTID sets and file positions are better read
through `mysql.sql` when needed), `MysqlStatus` (the
`SHOW GLOBAL STATUS` runtime counters as a name-keyed text map),
`MysqlTables` (engines, row estimates, data/index sizes, collations and
comments per database from `information_schema.TABLES` — views appear
with `None` engine/collation), and `MysqlVariables` (the
`SHOW GLOBAL VARIABLES` configuration as a name-keyed text map; values
stay in the server's own rendering — bytes, `ON`/`OFF` — because
`mysql.variable` compares against exactly this text, and session
variables are deliberately not exposed: a fresh CLI connection's session
IS the global value).

## Operations

An operation is an async generator: it reads facts through
[`FactCache.get`][reemote5.core.FactCache.get], decides whether a change
is needed, records `ctx.changed`/`ctx.reason`, and yields one typed
command descriptor per mutation — never touching the host itself. Reach
operations through the namespace (`mysql.database`), not the top-level
package.

The six the baseline-parity operations keep their names and shells:
`mysql.sql` executes arbitrary SQL (stateless, always yields — the
escape hatch for anything the typed operations do not cover),
`mysql.dump` writes a `mysqldump` to a host-side file and `mysql.load`
feeds a host-side SQL file back through `mysql` (both stateless; `load`'s
`src` is a path on the TARGET host — spool controller-side bytes with
`files.put` first; as in the baseline, `dump` with no database renders a bare
`mysqldump`, no `--all-databases` is injected). `mysql.privileges` diffs
the desired set against `MysqlUserGrants` for the exact `db.table`
target, revoking extras and granting missing ones in sorted order
(`USAGE` is never revoked when something is granted, the ALL +
GRANT OPTION pair uses the baseline's two-step forms, and role grants are
membership, excluded from the diff). `mysql.database` is extended beyond
the baseline: charset/collation are compared against `MysqlDatabases` and an
existing database is altered on drift (the `postgresql.database`
idempotent-alter precedent), while the `user`/`user_privileges` grant
delegation shares the `mysql.privileges` core. `mysql.user` keeps
the baseline's REQUIRE SSL/X509 validation, resource-limit comparisons and
global-privileges bundle, but replaces the creation-only `password`
(`IDENTIFIED BY`, whose hash cannot be compared) with `auth_plugin`
(`IDENTIFIED WITH`, e.g. `auth_socket`), which IS comparable against the
fact's plugin column and so also alters on drift.

The eight reemote5-original operations close the control gaps:
`mysql.table` creates/drops tables idempotent on EXISTENCE only — the
structure of an existing table is never diffed (column definitions are
free SQL text; inferring ALTERs would be a schema migration engine),
`MysqlColumns`/`MysqlIndexes` provide the visibility and `mysql.sql` the
migration escape hatch. `mysql.index` creates/drops indexes idempotent
against `MysqlIndexes`; an existing index whose columns, uniqueness or
type differ raises
[`OperationError`][reemote5.core.OperationError] rather than silently
rebuilding a possibly constraint-backing index (the `postgresql.index`
precedent), and FULLTEXT/SPATIAL render as the CREATE prefix.
`mysql.variable` tunes global system variables through
`SET GLOBAL`/`SET PERSIST` (MySQL 8.0+, surviving restarts via
`mysqld-auto.cnf`) with the value compared as TEXT in the server's own
rendering — pass `262144` rather than `"256K"`, `"1"`/`"0"` for booleans.
The variable is named by the `variable` option — `name` is `op()`'s
plan-label meta-parameter and never reaches an operation. An unknown
name raises `ValueError` in prepare, and `present=False`
renders a stateless `RESET PERSIST`. `mysql.plugin` installs/uninstalls
plugins against `MysqlPlugins`; a plugin loaded from a different library,
or uninstalling a built-in, raises `OperationError` in prepare.
`mysql.role` manages roles (MySQL 8.0+) against `MysqlUsers` and their
memberships against the role-grant extension of `MysqlUserGrants` —
members are only added, never revoked, and dropping a role revokes every
membership server-side. `mysql.replica` starts/stops replication
idempotent against `MysqlReplication` (an unconfigured channel raises
`OperationError` in prepare) and `reset=True` statelessly renders
`STOP REPLICA; RESET REPLICA ALL`; `CHANGE REPLICATION SOURCE TO` is
deliberately dropped — it requires `SOURCE_PASSWORD` inline and MySQL
has no passfile for replication, clashing with the namespace-wide
no-passwords rule, so configure the source through `mysql.sql` or server
tooling. `mysql.kill` terminates a connection (or just its query with
`query_only=True`) and `mysql.optimize` runs
OPTIMIZE/ANALYZE/CHECK/REPAIR TABLE over a table list — both stateless:
a connection ID is a snapshot value that would race the server, and
maintenance is a periodic action, not a desired state.

## Example

The read-only fact-gathering scripts `mysql_mysql_databases.py`,
`mysql_mysql_users.py`, `mysql_mysql_user_grants.py`,
`mysql_mysql_binary_logs.py`, `mysql_mysql_columns.py`,
`mysql_mysql_connections.py`, `mysql_mysql_engines.py`,
`mysql_mysql_indexes.py`, `mysql_mysql_plugins.py`,
`mysql_mysql_replication.py`, `mysql_mysql_status.py`,
`mysql_mysql_tables.py` and `mysql_mysql_variables.py` under
[`examples/modules/mysql/`](../examples/modules/mysql.md) gather the
thirteen facts respectively across an inventory with
[`gather_fact`][reemote5.drivers.gather_fact] and print each host's
result as JSON.

The operation scripts (`mysql_database.py`, `mysql_user.py`,
`mysql_privileges.py`, `mysql_sql.py`, `mysql_dump.py`, `mysql_load.py`,
`mysql_table.py`, `mysql_index.py`, `mysql_variable.py`,
`mysql_plugin.py`, `mysql_role.py`, `mysql_replica.py`, `mysql_kill.py`,
`mysql_optimize.py`) under the same directory drive each operation
through the two-phase [`run`][reemote5.drivers.run] — a dry-run plan then
a real apply — over an inventory. The idempotent scripts yield the same
request twice so the second is a guaranteed apply-phase no-op; the
stateless ones (sql, dump, load, kill, optimize) always run.

--->
