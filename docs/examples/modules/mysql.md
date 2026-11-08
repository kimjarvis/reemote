# Module examples: mysql

Scripts under `examples/modules/mysql/` — one per fact or operation of
the [`mysql`](../../modules/mysql.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

Every operation script's requests ride under `sudo`, so the `mysql`
client connects as `root` through unix-socket authentication — no
password travels through operation arguments, plans or logs (pyinfra's
`mysql_password` option is deliberately dropped namespace-wide, and
`mysql.user`'s `password` parameter is replaced by the comparable
`auth_plugin`). The read-only fact scripts gather as the login user, so
that user needs a host-side `~/.my.cnf` (or pass `mysql_defaults_file` /
`mysql_user` / `mysql_host` to `gather_fact` for a TCP login backed by an
option file).

The read-only fact-gathering examples (`mysql_mysql_databases.py`
reading `MysqlDatabases`, `mysql_mysql_users.py` reading `MysqlUsers`,
`mysql_mysql_user_grants.py` reading `MysqlUserGrants`,
`mysql_mysql_binary_logs.py` reading `MysqlBinaryLogs`,
`mysql_mysql_columns.py` reading `MysqlColumns`,
`mysql_mysql_connections.py` reading `MysqlConnections`,
`mysql_mysql_engines.py` reading `MysqlEngines`,
`mysql_mysql_indexes.py` reading `MysqlIndexes`,
`mysql_mysql_plugins.py` reading `MysqlPlugins`,
`mysql_mysql_replication.py` reading `MysqlReplication`,
`mysql_mysql_status.py` reading `MysqlStatus`,
`mysql_mysql_tables.py` reading `MysqlTables`, and
`mysql_mysql_variables.py` reading `MysqlVariables`, each over
`gather_fact`) are not rendered here — they read a single fact, and the
facts themselves are documented on the
[`mysql` module page](../../modules/mysql.md).

**Host prerequisite.** These examples need a running MySQL server and the
`mysql`/`mysqldump` client. On the demo CentOS Stream 9 hosts (web1/web2)
the server **installs and starts** under the confined sudo —
`sudo dnf install -y mysql-server` then `sudo systemctl start mysqld`
both succeed and `mysqld` runs active — but **no client can connect**:
`sudo mysql -Be 'SELECT 1'` (and the login-user fact reads) fail with
`ERROR 2002 (HY000): Can't connect to local MySQL server through socket
'/var/lib/mysql/mysql.sock' (13)`. Errno 13 is `EACCES`: SELinux denies the
`staff_t`-confined sudo/login domain access to the `mysqld_db_t` socket
(the datadir is `0755`, so this is MAC, not DAC). TCP to `127.0.0.1:3306`
is reachable but auth-denied, and the namespace's no-password design
requires unix-socket `auth_socket` as root, so TCP is not a workaround.
This is the same `staff_t` confinement the `selinux`, `sysctl`, `systemd`,
`lxd`, `dnf.key` and `gpg` examples document — a host-policy prerequisite,
not a script gap: every request is correctly flagged `sudo=True` and rides
root's socket auth. The scripts are therefore compile-checked and
exercised by the offline example harness rather than run against the
shared hosts. To run them for real, relax the demo hosts' sudo to an
unconfined domain (or set SELinux permissive), install + start
`mysql-server`, and provision the demo schema the later examples assume —
`mysql_database.py` creates `shop`, `mysql_table.py` its `orders` table,
`mysql_user.py` the `shop_app`@`localhost` account (run those first, in
that order) — or point each script's `INVENTORY` at your own MySQL
machines.

## Managing a database

The database operation: `mysql.database` mirrors pyinfra's
`mysql.database` and extends it beyond creation. It reads
[`MysqlDatabases`][reemote5.modules.mysql.mysql.MysqlDatabases]
to decide: an absent database yields one `CREATE DATABASE` carrying
`charset` and `collate`; an existing one whose character set or collation
differs is reconciled through `ALTER DATABASE` (pyinfra applies them only
at CREATE time and never alters). The `user`/`user_privileges` grant
delegation is pyinfra's, sharing the `mysql.privileges` core.
`present=False` drops the database. The script yields the same create
request twice — the second is a guaranteed no-op in the apply phase,
because the first create is visible to it through the fresh per-step fact
cache (in the prepare phase, where nothing is sent, both plan as changes).

::: mysql_database
    options:
      members: []

## Managing a user

The user operation: `mysql.user` mirrors pyinfra's `mysql.user`, minus
the credential: `password` (`IDENTIFIED BY`, creation-only because the
hash cannot be compared) is deliberately dropped and replaced by
`auth_plugin` (`IDENTIFIED WITH`, e.g. `auth_socket`), which IS comparable
against [`MysqlUsers`][reemote5.modules.mysql.mysql.MysqlUsers]' plugin
column and so also alters on drift. It reads the same fact to decide
existence, reconciles the REQUIRE SSL/X509 options and the `max_*`
resource limits through `ALTER USER` only where they differ, and
delegates the global `privileges` bundle to the `mysql.privileges` core.
`present=False` drops the account. The script yields the same create
request twice — the second is a guaranteed no-op in the apply phase,
because the first create is visible to it through the fresh per-step fact
cache (in the prepare phase, where nothing is sent, both plan as changes).

::: mysql_user
    options:
      members: []

## Granting or revoking privileges

The privileges operation: `mysql.privileges` mirrors pyinfra's
`mysql.privileges`, diffing the desired set against
[`MysqlUserGrants`][reemote5.modules.mysql.mysql.MysqlUserGrants] for the
exact `database.table` target: only missing privileges are granted, only
extra ones revoked (sorted, so both phases render identically), `USAGE`
is never revoked when something is granted, and `with_grant_option=True`
uses the two-step ALL + `WITH GRANT OPTION` forms pyinfra special-cases.
The account must already exist — create it with `mysql.user` first (the
`mysql_user.py` script). The script yields the same grant request twice —
the second is a guaranteed no-op in the apply phase, because the first
grant is visible to it through the fresh per-step fact cache (in the
prepare phase, where nothing is sent, both plan as changes).

::: mysql_privileges
    options:
      members: []

## Executing arbitrary SQL

The SQL operation: `mysql.sql` mirrors pyinfra's `mysql.sql`, executing a
statement through `mysql -Be`. It is stateless — the statement always
runs, in both the plan's apply phase and every subsequent run — so it is
the escape hatch for anything the typed operations do not cover; prefer
a fact-comparing operation when one exists, and a statement that is
naturally idempotent (`CREATE ... IF NOT EXISTS`) when it does not. The
SQL text appears verbatim in the plan and log, so never embed credentials
in it. The script yields the same idempotent statement twice.

::: mysql_sql
    options:
      members: []

## Dumping a database

The dump operation: `mysql.dump` mirrors pyinfra's `mysql.dump`, writing
a `mysqldump` of a database to a file on the TARGET host (`dest`). It is
stateless — the dump always runs, overwriting the destination — and, as
in pyinfra, `database=None` renders a bare `mysqldump` (no
`--all-databases` is injected). The redirect runs under `sudo`, so `dest`
must be writable by root. The script yields one dump request writing
under `/var/backups`.

::: mysql_dump
    options:
      members: []

## Loading a SQL file

The load operation: `mysql.load` mirrors pyinfra's `mysql.load`, feeding
a SQL file through `mysql`. It is stateless — the file always executes —
and `src` is a path on the TARGET host: spool controller-side bytes there
with `files.put` first. The script pairs a dump with a load of its own
output so the round-trip is self-contained.

::: mysql_load
    options:
      members: []

## Managing a table

The table operation: `mysql.table` is a reemote5-original operation
creating or dropping a table. It reads
[`MysqlTables`][reemote5.modules.mysql.mysql.MysqlTables] and is
idempotent on EXISTENCE only: an absent table yields `CREATE TABLE` with
the verbatim `columns` definitions plus `engine`, `charset`, `collate`
and `comment`; an existing one is left alone — its structure is never
diffed (column definitions are free SQL text; inferring ALTERs would be
a schema migration engine), `MysqlColumns`/`MysqlIndexes` provide the
visibility and `mysql.sql` the migration escape hatch. `present=False`
drops it. The script yields the same create request twice — the second
is a guaranteed no-op in the apply phase, because the first create is
visible to it through the fresh per-step fact cache (in the prepare
phase, where nothing is sent, both plan as changes).

::: mysql_table
    options:
      members: []

## Managing an index

The index operation: `mysql.index` is a reemote5-original operation, the
`postgresql.index` shape. It reads
[`MysqlIndexes`][reemote5.modules.mysql.mysql.MysqlIndexes] keyed on the
index name within the table: an absent index yields `CREATE INDEX`
(`CREATE UNIQUE INDEX` with `unique=True`, a named `index_type`, and
FULLTEXT/SPATIAL as the CREATE prefix), and `present=False` drops it. An
existing index whose columns, uniqueness or type differ raises
`OperationError` rather than silently rebuilding a possibly
constraint-backing index — drop it first to rebuild. The table must
already exist (the `mysql_table.py` script creates it). The script
yields the same create request twice — the second is a guaranteed no-op
in the apply phase, because the first create is visible to it through
the fresh per-step fact cache (in the prepare phase, where nothing is
sent, both plan as changes).

::: mysql_index
    options:
      members: []

## Tuning a global variable

The variable operation: `mysql.variable` is a reemote5-original
operation, the `postgresql.setting` shape, tuning a global system
variable through `SET GLOBAL` — or `SET PERSIST` with `persist=True`
(MySQL 8.0+), which applies at runtime AND survives restarts via
`mysqld-auto.cnf`. It reads
[`MysqlVariables`][reemote5.modules.mysql.mysql.MysqlVariables] and
compares the value as TEXT in the server's own rendering — there is no
unit normalization, so pass `262144` rather than `"256K"` for a
byte-sized variable and `"1"`/`"0"` for booleans; an unknown variable
name raises `ValueError` in prepare. `present=False` renders a stateless
`RESET PERSIST`, removing the persisted override. The script yields the
same set request twice — the second is a guaranteed no-op in the apply
phase, because the first write is visible to it through the fresh
per-step fact cache (in the prepare phase, where nothing is sent, both
plan as changes).

::: mysql_variable
    options:
      members: []

## Managing a plugin

The plugin operation: `mysql.plugin` is a reemote5-original operation
installing or uninstalling a server plugin. It reads
[`MysqlPlugins`][reemote5.modules.mysql.mysql.MysqlPlugins] keyed on the
plugin name: an absent plugin yields `INSTALL PLUGIN ... SONAME` (the
library must exist in the server's plugin directory), and
`present=False` yields `UNINSTALL PLUGIN`. A plugin loaded from a
DIFFERENT library, or uninstalling a built-in (library `None`), raises
`OperationError` in prepare. The script ensures the common `auth_socket`
plugin is installed — on most servers it already is, so both requests
plan as no-ops. The same request is yielded twice; a genuinely missing
plugin is installed by the first and seen by the second through the
fresh per-step fact cache.

::: mysql_plugin
    options:
      members: []

## Managing a role

The role operation: `mysql.role` is a reemote5-original operation
managing roles (MySQL 8.0+) and their memberships. Existence is
idempotent against
[`MysqlUsers`][reemote5.modules.mysql.mysql.MysqlUsers] keyed
`role@role_hostname`; membership is idempotent against the role-grant
extension of
[`MysqlUserGrants`][reemote5.modules.mysql.mysql.MysqlUserGrants] (the
backticked ``role``@``host`` key with the `ROLE` privilege). `members`
are `user@host` accounts the role is granted to — memberships are only
added, never revoked (list every member the role should keep), and the
member accounts must already exist. `present=False` drops the role,
which revokes every membership server-side. The script yields the same
create-and-grant request twice — the second is a guaranteed no-op in the
apply phase, because the first create and grant are visible to it
through the fresh per-step fact cache (in the prepare phase, where
nothing is sent, both plan as changes).

::: mysql_role
    options:
      members: []

## Controlling replication

The replica operation: `mysql.replica` is a reemote5-original operation
scoped to START/STOP/RESET REPLICA (MySQL 8.0.22+ syntax).
`CHANGE REPLICATION SOURCE TO` is deliberately dropped — it requires
`SOURCE_PASSWORD` inline and MySQL has no passfile for replication, so
configure the source through `mysql.sql` or server tooling first. It
reads [`MysqlReplication`][reemote5.modules.mysql.mysql.MysqlReplication]:
both threads `Yes` means running, both `No` means stopped, and a channel
that is not configured raises `OperationError` in prepare — the target
hosts must already be replicas. `reset=True` statelessly renders
`STOP REPLICA; RESET REPLICA ALL`, discarding every channel's
configuration. The script yields the same start request twice — on a
stopped replica the second is a guaranteed no-op in the apply phase
through the fresh per-step fact cache; on an already-running one both
plan as no-ops.

::: mysql_replica
    options:
      members: []

## Killing a connection

The kill operation: `mysql.kill` is a reemote5-original operation, the
`server.kill` shape: stateless, always yields — a connection ID is a
snapshot value, so comparing it against `MysqlConnections` inside the
operation would race the server anyway. `query_only=True` renders
`KILL QUERY` (cancel the running statement, keep the connection);
otherwise `KILL` closes the connection. Killing an already-gone ID
errors server-side (`Unknown thread id`), surfaced as `CommandError` at
apply time. The script targets a placeholder ID — pick a live one from
the `mysql_mysql_connections.py` fact example before running it for
real.

::: mysql_kill
    options:
      members: []

## Maintaining tables

The optimize operation: `mysql.optimize` is a reemote5-original
operation running `OPTIMIZE TABLE` (or `ANALYZE`/`CHECK`/`REPAIR` through
`action`) over a list of tables in one database. It is stateless like
pyinfra's dump/load: maintenance is a periodic action, not a desired
state, so there is nothing to compare a fact against — every request
runs. InnoDB maps OPTIMIZE to a recreate+analyze and does not support
REPAIR (the server reports a note, not an error). The script yields one
analyze request over the `orders` table the `mysql_table.py` script
creates.

::: mysql_optimize
    options:
      members: []