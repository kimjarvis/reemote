# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The ``postgresql`` module: comprehensive PostgreSQL management.
#
# Fact side: ``PostgresqlConnections`` (active client connections from
# ``pg_stat_activity``, without the query text), ``PostgresqlDatabases``
# (the cluster's databases and their ``pg_database`` metadata),
# ``PostgresqlExtensions`` (installed extensions and versions),
# ``PostgresqlIndexes`` (user index metadata), ``PostgresqlPrivileges``
# (object-level grants exploded from the system-catalog ACLs),
# ``PostgresqlPublications`` (logical-replication publications and their
# tables), ``PostgresqlReplication`` (streaming-replication standbys from
# ``pg_stat_replication``), ``PostgresqlRoleMemberships`` (role-to-role
# grants from ``pg_auth_members``), ``PostgresqlRoles`` (the cluster's
# roles and their ``pg_roles`` attributes), ``PostgresqlSchemas``
# (non-system schemas and owners), ``PostgresqlSettings`` (the active GUC
# configuration from ``pg_settings``), ``PostgresqlSubscriptions``
# (logical-replication subscriptions, without the credential-bearing
# conninfo), ``PostgresqlTables`` (user table metadata and row estimates)
# and ``PostgresqlTablespaces`` (tablespaces and their locations).
# ``PostgresqlRoles`` and ``PostgresqlDatabases`` mirror pyinfra's
# ``postgres.PostgresRoles``/``PostgresDatabases`` (whose ``postgresql``
# aliases are deprecated upstream); every other fact is reemote5-original
# and closes a visibility gap pyinfra leaves open.
#
# Operation side: ``postgresql.database`` and ``postgresql.role`` manage
# databases and roles (both create/drop AND idempotently alter existing
# ones -- upstream's role-alter branch compares keys its own fact never
# produces, so reemote5 reworks it against the real fact keys);
# ``postgresql.sql`` executes arbitrary SQL; ``postgresql.dump`` and
# ``postgresql.load`` write/read host-side ``pg_dump`` files;
# ``postgresql.privilege`` grants/revokes object privileges;
# ``postgresql.membership`` manages role membership; ``postgresql.schema``,
# ``postgresql.extension`` and ``postgresql.index`` manage schema objects;
# ``postgresql.setting`` tunes GUCs via ALTER SYSTEM; ``postgresql.tablespace``
# manages tablespaces; and ``postgresql.publication``/``postgresql.subscription``
# manage logical replication. All operations talk to the server through the
# ``psql`` CLI on the target host.
#
# Credentials decision (developer-approved): pyinfra's ``psql_password``
# connection parameter and ``role``'s ``password`` option are deliberately
# dropped. reemote5 renders raw shell strings into the plan/apply log and
# takes credentials only from the inventory ``privilege`` section, so no
# operation accepts a password. Authenticate with peer auth (``sudo=True``
# with ``privilege.sudo_user = "postgres"`` in the inventory) or a host-side
# ``~/.pgpass``; ``postgresql.subscription``'s ``connection`` DSN should
# likewise reference a passfile/service rather than embed a password.
#
# Every fact and operation accepts the connection keyword arguments
# ``psql_user``/``psql_host``/``psql_port``/``psql_database`` (pyinfra's
# ``psql_*`` module arguments minus the password). For database-scoped
# facts/operations ``psql_database`` selects the database to inspect or
# change; cluster-wide ones default to psql's own default database.
# """

import shlex
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass
from typing import Any, TypedDict, cast

from ...commands import Command
from ...core import OpContext, OperationError
from ...hosts import Host


def _psql_base(
    *,
    psql_user: str | None = None,
    psql_host: str | None = None,
    psql_port: int | None = None,
    psql_database: str | None = None,
    executable: str = "psql",
) -> str:
    """Render the psql invocation prefix (pyinfra's ``make_psql_command``).

    Mirrors pyinfra's flag order -- ``-d`` database, ``-U`` user, ``-h``
    host, ``-p`` port -- with the ``PGPASSWORD`` prefix deliberately
    dropped (the credentials decision above). Every user-supplied operand
    is ``shlex``-quoted, a deliberate deviation from pyinfra's raw
    interpolation; plain values quote to themselves, so command parity
    holds for ordinary names.
    """
    parts = [executable]
    if psql_database:
        parts.extend(["-d", shlex.quote(psql_database)])
    if psql_user:
        parts.extend(["-U", shlex.quote(psql_user)])
    if psql_host:
        parts.extend(["-h", shlex.quote(psql_host)])
    if psql_port:
        parts.extend(["-p", str(psql_port)])
    return " ".join(parts)


def _psql_sql(
    sql: str,
    *,
    psql_user: str | None = None,
    psql_host: str | None = None,
    psql_port: int | None = None,
    psql_database: str | None = None,
) -> str:
    """Render ``psql ... -Ac '<sql>'`` (pyinfra's ``make_execute_psql_command``).

    ``-A`` is unaligned output and ``-c`` runs one command and exits, so
    the answer is a ``|``-separated header row, one row per record and a
    trailing ``(N rows)`` count -- the shape ``_parse_rows`` reads.
    """
    base = _psql_base(
        psql_user=psql_user,
        psql_host=psql_host,
        psql_port=psql_port,
        psql_database=psql_database,
    )
    return f"{base} -Ac {shlex.quote(sql)}"


def _quote_ident(name: str) -> str:
    """Quote a SQL identifier, doubling any embedded double quote.

    pyinfra interpolates ``f'"{name}"'`` raw, which breaks on a name
    containing a double quote; reemote5 escapes it the way PostgreSQL's
    own ``quote_ident`` does.
    """
    return '"' + name.replace('"', '""') + '"'


def _quote_qualified(name: str) -> str:
    """Quote a possibly schema-qualified object name part by part.

    ``public.mytable`` becomes ``"public"."mytable"``; a bare name quotes
    as a single identifier.
    """
    return ".".join(_quote_ident(part) for part in name.split("."))


def _quote_literal(value: str) -> str:
    """Quote a SQL string literal, doubling any embedded single quote."""
    return "'" + value.replace("'", "''") + "'"


def _try_int(value: str | None) -> int | str | None:
    """pyinfra's ``try_int``: an integer when the text is numeric, else raw."""
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def _parse_rows(
    output: Iterable[str], remove_prefix: str | None = None
) -> list[dict[str, str | None]]:
    """Parse ``psql -Ac`` output into header-keyed row dicts.

    Mirrors pyinfra's ``parse_columns_and_rows`` over ``|``-separated
    output: the first line names the columns (an optional ``rol``/``dat``
    prefix is stripped, as pyinfra does for pg_roles/pg_database), each
    following line is one row, and an empty field reads as None. The
    trailing ``(N rows)`` count line is dropped by pattern rather than
    pyinfra's unconditional last-line chop, so empty output parses to
    ``[]`` instead of raising -- a deliberate hardening deviation.
    """
    lines = list(output)
    if not lines:
        return []
    titles = lines[0].split("|")
    if remove_prefix:
        titles = [
            title.removeprefix(remove_prefix)
            for title in titles
        ]
    rows: list[dict[str, str | None]] = []
    for line in lines[1:]:
        if (line.startswith("(") and line.endswith(" rows)")) or line == "(1 row)":
            continue
        bits = line.split("|")
        rows.append(
            {titles[i]: bit or None for i, bit in enumerate(bits) if i < len(titles)}
        )
    return rows


def _parse_array(value: str | None) -> list[str]:
    """Parse a psql-rendered ``text[]`` literal (``{a,b}``) into a list.

    The empty array ``{}`` (and a NULL column) reads as ``[]``. Used for
    ``pg_subscription.subpublications``; publication/role names cannot
    contain commas or braces unquoted, which the subscription operation
    validates against.
    """
    if not value or value == "{}":
        return []
    return value.strip("{}").split(",")


class _PostgresqlFact:
    """Shared transport plumbing for the psql-backed facts.

    Holds the ``command`` renderer (the fact's ``PSQL`` query wrapped in
    the psql invocation) and the ``_gather`` real-transport tail, so each
    fact body is just its seam read plus ``parse``. Never exported or
    rendered; every concrete fact keeps its own ``process`` classmethod
    choosing between the SimulatedHost seam and ``_gather``.
    """

    PSQL: str

    @classmethod
    def command(
        cls,
        *,
        psql_user: str | None = None,
        psql_host: str | None = None,
        psql_port: int | None = None,
        psql_database: str | None = None,
    ) -> str:
        """Shell running this fact's query through ``psql -Ac``."""
        return _psql_sql(
            cls.PSQL,
            psql_user=psql_user,
            psql_host=psql_host,
            psql_port=psql_port,
            psql_database=psql_database,
        )

    @classmethod
    async def _gather(
        cls,
        host: Host,
        *,
        psql_user: str | None = None,
        psql_host: str | None = None,
        psql_port: int | None = None,
        psql_database: str | None = None,
    ) -> Any:
        # Real transport: run the query over asyncssh and parse stdout.
        stdout = await host.run_shell(
            cls.command(
                psql_user=psql_user,
                psql_host=psql_host,
                psql_port=psql_port,
                psql_database=psql_database,
            )
        )
        return cls.parse(stdout.splitlines())  # type: ignore[attr-defined]


# The schema filter every object-listing fact shares: everything outside
# the system schemas (pg_catalog, pg_toast, ...) and information_schema.
# ``pg\_%`` escapes the LIKE underscore so only the literal prefix matches.
_USER_SCHEMAS = (
    "n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema'"
)


# --- Facts -------------------------------------------------------------------


class PostgresqlConnection(TypedDict):
    """
    Attributes:
        pid: The backend process ID.
        database: The database the backend is connected to; None for
            background workers with no database.
        username: The role the backend authenticated as; None for
            background workers.
        client_addr: The client's IP address; None for local (Unix
            socket) connections.
        state: The backend state (``active``, ``idle``,
            ``idle in transaction``, ...); None for background workers.
    """

    pid: int
    database: str | None
    username: str | None
    client_addr: str | None
    state: str | None


class PostgresqlConnections(_PostgresqlFact):
    """The server's client connections, without the query text.

    **Commands:**

    ```bash
    psql -Ac 'SELECT pid, datname, usename, client_addr, state FROM pg_catalog.pg_stat_activity'
    ```

    **Returns:**

    ```python
    list[PostgresqlConnection]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/postgresql/postgresql_postgresql_connections.py``.
    """

    # reemote5-original: pyinfra has no connection-visibility fact. Reads
    # pg_stat_activity but deliberately omits the ``query`` column -- the
    # SQL text can carry literal credentials and personal data, so the
    # fact reports only who is connected, from where, and in what state.
    # Background workers (checkpointer, walwriter, ...) appear with None
    # database/username/state, exactly as the view reports them.

    PSQL = (
        "SELECT pid, datname, usename, client_addr, state "
        "FROM pg_catalog.pg_stat_activity"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[PostgresqlConnection]:
        """Parse pg_stat_activity rows into connection entries."""
        return [
            {
                "pid": int(row["pid"] or 0),
                "database": row["datname"],
                "username": row["usename"],
                "client_addr": row["client_addr"],
                "state": row["state"],
            }
            for row in _parse_rows(output)
        ]

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        psql_user: str | None = None,
        psql_host: str | None = None,
        psql_port: int | None = None,
        psql_database: str | None = None,
    ) -> list[PostgresqlConnection]:
        connections = getattr(host, "postgres_connections", None)
        if connections is not None:
            # SimulatedHost seam: the connection entries, copied per
            # entry so callers never mutate simulated state.
            return [cast(PostgresqlConnection, dict(entry)) for entry in connections]
        return await cls._gather(
            host,
            psql_user=psql_user,
            psql_host=psql_host,
            psql_port=psql_port,
            psql_database=psql_database,
        )


class PostgresqlDatabase(TypedDict):
    """
    Attributes:
        dba: The OID of the owning role (``pg_database.datdba``).
        encoding: The character encoding name, e.g. ``UTF8`` (resolved
            from the numeric encoding through ``pg_encoding_to_char``).
        collate: The ``LC_COLLATE`` setting; None when unset.
        ctype: The ``LC_CTYPE`` setting; None when unset.
        istemplate: Whether the database is a template database.
        allowconn: Whether connections are allowed (``datallowconn``).
        connlimit: The connection limit; -1 means no limit.
        lastsysoid: The last system OID (absent on PostgreSQL 12+).
        frozenxid: The transaction-ID freeze horizon.
        minmxid: The multixact-ID freeze horizon.
        tablespace: The OID of the default tablespace (not its name).
        acl: The raw ACL string; None when default privileges apply.
        owner: The owning role's name (resolved via ``pg_get_userbyid``).
        oid: The database's OID; present on PostgreSQL 12+ (where ``oid``
            became a regular ``pg_database`` column), None on older servers.
    """

    dba: int | str
    encoding: str | None
    collate: str | None
    ctype: str | None
    istemplate: bool
    allowconn: bool
    connlimit: int | str
    lastsysoid: int | str | None
    frozenxid: int | str
    minmxid: int | str
    tablespace: int | str
    acl: str | None
    owner: str | None
    oid: int | str | None


class PostgresqlDatabases(_PostgresqlFact):
    """The cluster's databases and their metadata, keyed by database name.

    **Commands:**

    ```bash
    psql -Ac 'SELECT pg_catalog.pg_encoding_to_char(encoding), *, pg_catalog.pg_get_userbyid(datdba) AS owner FROM pg_catalog.pg_database'
    ```

    **Returns:**

    ```python
    dict[str, PostgresqlDatabase]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/postgresql/postgresql_postgresql_databases.py``.
    """

    # Mirrors pyinfra's ``postgres.PostgresDatabases`` (the ``postgresql``
    # alias is deprecated upstream): ``SELECT *`` over pg_database with
    # the encoding resolved to its name and the owner OID resolved to a
    # role name, the ``dat`` column prefix stripped, OID/limit columns
    # coerced with try_int and istemplate/allowconn coerced from t/f.
    # The name-keyed outer dict is kept (the device-keyed-mapping
    # exception to the entry-list guideline) with a PostgresqlDatabase
    # entry per database. The column set follows the server version --
    # ``datlastsysoid`` disappeared in PostgreSQL 12 (when ``oid`` became
    # a regular column) -- and the
    # header-driven parse simply carries whatever columns the server
    # reports. pyinfra's ``requires_command`` gating is dropped: a
    # missing psql exits 127, reported as CommandNotFoundError.

    PSQL = (
        "SELECT pg_catalog.pg_encoding_to_char(encoding), *, "
        "pg_catalog.pg_get_userbyid(datdba) AS owner FROM pg_catalog.pg_database"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, PostgresqlDatabase]:
        """Parse pg_database rows into the name-keyed database mapping."""
        databases: dict[str, PostgresqlDatabase] = {}
        for row in _parse_rows(output, remove_prefix="dat"):
            details = dict(row)
            details["encoding"] = details.pop("pg_encoding_to_char")
            for key, value in list(details.items()):
                if key.endswith("id") or key in ("dba", "tablespace", "connlimit"):
                    details[key] = _try_int(value)
                if key in ("istemplate", "allowconn"):
                    details[key] = value == "t"
            name = details.pop("name")
            databases[cast(str, name)] = cast(PostgresqlDatabase, details)
        return databases

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        psql_user: str | None = None,
        psql_host: str | None = None,
        psql_port: int | None = None,
        psql_database: str | None = None,
    ) -> dict[str, PostgresqlDatabase]:
        databases = getattr(host, "postgres_databases", None)
        if databases is not None:
            # SimulatedHost seam: database name -> PostgresqlDatabase,
            # copied per entry. The connection kwargs address one
            # simulated cluster per host, so they do not key the seam.
            return {
                name: cast(PostgresqlDatabase, dict(entry))
                for name, entry in databases.items()
            }
        return await cls._gather(
            host,
            psql_user=psql_user,
            psql_host=psql_host,
            psql_port=psql_port,
            psql_database=psql_database,
        )


class PostgresqlExtension(TypedDict):
    """
    Attributes:
        name: The extension name, e.g. ``pg_stat_statements``.
        version: The installed version string, e.g. ``1.10``.
        schema: The schema the extension's objects live in.
    """

    name: str
    version: str
    schema: str


class PostgresqlExtensions(_PostgresqlFact):
    """The extensions installed in a database, with versions and schemas.

    **Commands:**

    ```bash
    psql -Ac 'SELECT e.extname, e.extversion, n.nspname FROM pg_catalog.pg_extension e JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace ORDER BY e.extname'
    ```

    **Returns:**

    ```python
    list[PostgresqlExtension]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/postgresql/postgresql_postgresql_extensions.py``.
    """

    # reemote5-original: pyinfra has no extension fact. Database-scoped
    # (extensions are per-database objects): ``psql_database`` selects the
    # database, defaulting to psql's own default. The always-present
    # ``plpgsql`` extension is included, as pg_extension lists it.

    PSQL = (
        "SELECT e.extname, e.extversion, n.nspname "
        "FROM pg_catalog.pg_extension e "
        "JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace "
        "ORDER BY e.extname"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[PostgresqlExtension]:
        """Parse pg_extension rows into extension entries."""
        return [
            {
                "name": cast(str, row["extname"]),
                "version": cast(str, row["extversion"]),
                "schema": cast(str, row["nspname"]),
            }
            for row in _parse_rows(output)
        ]

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        psql_user: str | None = None,
        psql_host: str | None = None,
        psql_port: int | None = None,
        psql_database: str | None = None,
    ) -> list[PostgresqlExtension]:
        extensions = getattr(host, "postgres_extensions", None)
        if extensions is not None:
            # SimulatedHost seam: database name -> extension entries,
            # keyed "postgres" when psql_database is unset (psql's
            # default database on a stock install).
            entries = extensions.get(psql_database or "postgres", [])
            return [cast(PostgresqlExtension, dict(entry)) for entry in entries]
        return await cls._gather(
            host,
            psql_user=psql_user,
            psql_host=psql_host,
            psql_port=psql_port,
            psql_database=psql_database,
        )


class PostgresqlIndex(TypedDict):
    """
    Attributes:
        schema: The schema the index lives in.
        name: The index name.
        table: The name of the table the index covers (unqualified).
        owner: The owning role's name.
        tablespace: The index's tablespace name; None when it uses the
            database default.
        unique: Whether the index enforces uniqueness.
        method: The access method, e.g. ``btree``, ``gin``, ``gist``.
    """

    schema: str
    name: str
    table: str
    owner: str
    tablespace: str | None
    unique: bool
    method: str


class PostgresqlIndexes(_PostgresqlFact):
    """The user indexes in a database, with table, owner and access method.

    **Commands:**

    ```bash
    psql -Ac 'SELECT n.nspname AS schema, c.relname AS name, t.relname AS table_name, pg_catalog.pg_get_userbyid(c.relowner) AS owner, ts.spcname AS tablespace, i.indisunique, am.amname AS method FROM pg_catalog.pg_class c JOIN pg_catalog.pg_index i ON i.indexrelid = c.oid JOIN pg_catalog.pg_class t ON t.oid = i.indrelid JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace JOIN pg_catalog.pg_am am ON am.oid = c.relam LEFT JOIN pg_catalog.pg_tablespace ts ON ts.oid = c.reltablespace WHERE n.nspname NOT LIKE '"'"'pg\\_%'"'"' AND n.nspname <> '"'"'information_schema'"'"' ORDER BY n.nspname, c.relname'
    ```

    **Returns:**

    ```python
    list[PostgresqlIndex]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/postgresql/postgresql_postgresql_indexes.py``.
    """

    # reemote5-original: pyinfra has no index fact. Database-scoped;
    # system schemas are filtered out. The indexed *columns* are
    # deliberately not reported (pg_index.indkey needs per-column
    # expansion); the ``postgresql.index`` operation therefore keys
    # idempotence on the index name and checks table/unique/method/
    # tablespace for drift, not the column list.

    PSQL = (
        "SELECT n.nspname AS schema, c.relname AS name, "
        "t.relname AS table_name, "
        "pg_catalog.pg_get_userbyid(c.relowner) AS owner, "
        "ts.spcname AS tablespace, i.indisunique, am.amname AS method "
        "FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_index i ON i.indexrelid = c.oid "
        "JOIN pg_catalog.pg_class t ON t.oid = i.indrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_catalog.pg_am am ON am.oid = c.relam "
        "LEFT JOIN pg_catalog.pg_tablespace ts ON ts.oid = c.reltablespace "
        f"WHERE {_USER_SCHEMAS} ORDER BY n.nspname, c.relname"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[PostgresqlIndex]:
        """Parse the index join rows into index entries."""
        return [
            {
                "schema": cast(str, row["schema"]),
                "name": cast(str, row["name"]),
                "table": cast(str, row["table_name"]),
                "owner": cast(str, row["owner"]),
                "tablespace": row["tablespace"],
                "unique": row["indisunique"] == "t",
                "method": cast(str, row["method"]),
            }
            for row in _parse_rows(output)
        ]

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        psql_user: str | None = None,
        psql_host: str | None = None,
        psql_port: int | None = None,
        psql_database: str | None = None,
    ) -> list[PostgresqlIndex]:
        indexes = getattr(host, "postgres_indexes", None)
        if indexes is not None:
            # SimulatedHost seam: database name -> index entries.
            entries = indexes.get(psql_database or "postgres", [])
            return [cast(PostgresqlIndex, dict(entry)) for entry in entries]
        return await cls._gather(
            host,
            psql_user=psql_user,
            psql_host=psql_host,
            psql_port=psql_port,
            psql_database=psql_database,
        )


class PostgresqlPrivilege(TypedDict):
    """
    Attributes:
        object_type: The granted-on object kind: ``database``, ``schema``,
            ``table``, ``view``, ``materialized_view``, ``foreign_table``,
            ``sequence`` or ``function``.
        schema: The object's schema; None for databases and schemas
            (which are not schema-qualified).
        name: The object's name (unqualified).
        grantee: The role the privilege is granted to; ``PUBLIC`` for the
            implicit grant to every role.
        privilege: The privilege name, e.g. ``SELECT``, ``USAGE``.
        grantable: Whether the grant carries WITH GRANT OPTION.
    """

    object_type: str
    schema: str | None
    name: str
    grantee: str
    privilege: str
    grantable: bool


class PostgresqlPrivileges(_PostgresqlFact):
    """The object-level privilege grants visible from a database.

    **Commands:**

    ```bash
    psql -Ac 'SELECT obj.object_type, obj.schema_name, obj.object_name, COALESCE(pg_catalog.pg_get_userbyid(acl.grantee), '"'"'PUBLIC'"'"') AS grantee, acl.privilege_type, acl.is_grantable FROM (...) obj, LATERAL pg_catalog.aclexplode(obj.acl) acl ORDER BY obj.object_type, obj.schema_name, obj.object_name, grantee, acl.privilege_type'
    ```

    **Returns:**

    ```python
    list[PostgresqlPrivilege]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/postgresql/postgresql_postgresql_privileges.py``.
    """

    # reemote5-original: pyinfra can create roles but cannot see who is
    # granted what -- the core permission blind spot this fact closes.
    # One aclexplode sweep over a UNION of pg_database.datacl,
    # pg_namespace.nspacl, pg_class.relacl (tables, views, materialized
    # views, foreign tables, sequences, partitioned tables) and
    # pg_proc.proacl yields one entry per (object, grantee, privilege).
    # A grantee OID of 0 resolves to PUBLIC via the COALESCE. Database
    # ACLs are cluster-wide but reported alongside the connected
    # database's object ACLs; the other catalogs are database-scoped, so
    # ``psql_database`` selects whose tables/schemas/functions are seen.
    # NULL ACLs (default privileges: owner-only) are NOT expanded --
    # aclexplode of NULL yields no rows, matching what GRANT would
    # change. System schemas are filtered out. The Commands block above
    # abbreviates the UNION subquery; the exact shell is PSQL below.

    PSQL = (
        "SELECT obj.object_type, obj.schema_name, obj.object_name, "
        "COALESCE(pg_catalog.pg_get_userbyid(acl.grantee), 'PUBLIC') AS grantee, "
        "acl.privilege_type, acl.is_grantable "
        "FROM (SELECT 'database' AS object_type, NULL AS schema_name, "
        "d.datname AS object_name, d.datacl AS acl "
        "FROM pg_catalog.pg_database d "
        "UNION ALL SELECT 'schema', NULL, n.nspname, n.nspacl "
        f"FROM pg_catalog.pg_namespace n WHERE {_USER_SCHEMAS} "
        "UNION ALL SELECT CASE c.relkind WHEN 'S' THEN 'sequence' "
        "WHEN 'v' THEN 'view' WHEN 'm' THEN 'materialized_view' "
        "WHEN 'f' THEN 'foreign_table' ELSE 'table' END, "
        "n.nspname, c.relname, c.relacl "
        "FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') "
        f"AND {_USER_SCHEMAS} "
        "UNION ALL SELECT 'function', n.nspname, p.proname, p.proacl "
        "FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
        f"WHERE {_USER_SCHEMAS}) obj, "
        "LATERAL pg_catalog.aclexplode(obj.acl) acl "
        "ORDER BY obj.object_type, obj.schema_name, obj.object_name, "
        "grantee, acl.privilege_type"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[PostgresqlPrivilege]:
        """Parse the aclexplode rows into privilege entries."""
        return [
            {
                "object_type": cast(str, row["object_type"]),
                "schema": row["schema_name"],
                "name": cast(str, row["object_name"]),
                "grantee": cast(str, row["grantee"]),
                "privilege": cast(str, row["privilege_type"]),
                "grantable": row["is_grantable"] == "t",
            }
            for row in _parse_rows(output)
        ]

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        psql_user: str | None = None,
        psql_host: str | None = None,
        psql_port: int | None = None,
        psql_database: str | None = None,
    ) -> list[PostgresqlPrivilege]:
        privileges = getattr(host, "postgres_privileges", None)
        if privileges is not None:
            # SimulatedHost seam: database name -> privilege entries.
            entries = privileges.get(psql_database or "postgres", [])
            return [cast(PostgresqlPrivilege, dict(entry)) for entry in entries]
        return await cls._gather(
            host,
            psql_user=psql_user,
            psql_host=psql_host,
            psql_port=psql_port,
            psql_database=psql_database,
        )


class PostgresqlPublication(TypedDict):
    """
    Attributes:
        name: The publication name.
        owner: The owning role's name.
        all_tables: Whether the publication covers ALL TABLES.
        tables: The schema-qualified table names the publication covers,
            sorted; empty for an ALL TABLES publication (or one created
            FOR TABLE with no tables yet).
    """

    name: str
    owner: str
    all_tables: bool
    tables: list[str]


class PostgresqlPublications(_PostgresqlFact):
    """The logical-replication publications in a database, with their tables.

    **Commands:**

    ```bash
    psql -Ac 'SELECT p.pubname, pg_catalog.pg_get_userbyid(p.pubowner) AS owner, p.puballtables, COALESCE(string_agg(t.schemaname || '"'"'.'"'"' || t.tablename, '"'"','"'"' ORDER BY t.schemaname, t.tablename), '"'"''"'"') AS tables FROM pg_catalog.pg_publication p LEFT JOIN pg_catalog.pg_publication_tables t ON t.pubname = p.pubname GROUP BY p.pubname, p.pubowner, p.puballtables ORDER BY p.pubname'
    ```

    **Returns:**

    ```python
    list[PostgresqlPublication]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/postgresql/postgresql_postgresql_publications.py``.
    """

    # reemote5-original: backs the idempotence of ``postgresql.publication``.
    # Database-scoped. pg_publication_tables is aggregated per publication
    # into a comma-joined ``schema.table`` list (sorted), so one row per
    # publication survives the GROUP BY; an ALL TABLES publication also
    # lists its tables there, but ``all_tables`` is the authoritative
    # flag the operation compares. Publication-level WITH options
    # (publish=...) are not reported.

    PSQL = (
        "SELECT p.pubname, pg_catalog.pg_get_userbyid(p.pubowner) AS owner, "
        "p.puballtables, "
        "COALESCE(string_agg(t.schemaname || '.' || t.tablename, ',' "
        "ORDER BY t.schemaname, t.tablename), '') AS tables "
        "FROM pg_catalog.pg_publication p "
        "LEFT JOIN pg_catalog.pg_publication_tables t ON t.pubname = p.pubname "
        "GROUP BY p.pubname, p.pubowner, p.puballtables ORDER BY p.pubname"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[PostgresqlPublication]:
        """Parse the grouped publication rows into publication entries."""
        return [
            {
                "name": cast(str, row["pubname"]),
                "owner": cast(str, row["owner"]),
                "all_tables": row["puballtables"] == "t",
                "tables": row["tables"].split(",") if row["tables"] else [],
            }
            for row in _parse_rows(output)
        ]

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        psql_user: str | None = None,
        psql_host: str | None = None,
        psql_port: int | None = None,
        psql_database: str | None = None,
    ) -> list[PostgresqlPublication]:
        publications = getattr(host, "postgres_publications", None)
        if publications is not None:
            # SimulatedHost seam: database name -> publication entries;
            # the tables list is copied along with the entry dict.
            entries = publications.get(psql_database or "postgres", [])
            return [
                cast(
                    PostgresqlPublication,
                    {**entry, "tables": list(entry["tables"])},
                )
                for entry in entries
            ]
        return await cls._gather(
            host,
            psql_user=psql_user,
            psql_host=psql_host,
            psql_port=psql_port,
            psql_database=psql_database,
        )


class PostgresqlReplicationClient(TypedDict):
    """
    Attributes:
        application_name: The standby's application name.
        client_addr: The standby's IP address; None for a Unix-socket
            connection.
        state: The WAL sender state (``streaming``, ``catchup``, ...).
        sync_state: The synchronization mode (``async``, ``sync``,
            ``potential``, ``quorum``).
        replay_lag: The interval between the last WAL flush locally and
            its replay on the standby, as text (e.g. ``00:00:00.000512``);
            None when the standby has not reported a replay position.
    """

    application_name: str
    client_addr: str | None
    state: str | None
    sync_state: str | None
    replay_lag: str | None


class PostgresqlReplication(_PostgresqlFact):
    """The streaming-replication standbys connected to this server.

    **Commands:**

    ```bash
    psql -Ac 'SELECT application_name, client_addr, state, sync_state, replay_lag FROM pg_catalog.pg_stat_replication'
    ```

    **Returns:**

    ```python
    list[PostgresqlReplicationClient]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/postgresql/postgresql_postgresql_replication.py``.
    """

    # reemote5-original: pyinfra has no replication visibility. Reads
    # pg_stat_replication on the PRIMARY (an empty list on a server with
    # no connected standbys -- a standalone or a replica). LSN columns
    # are deliberately not reported; ``replay_lag`` (a PostgreSQL 10+
    # interval) is the lag measure. The interval stays text so no
    # timedelta parsing dependency is introduced (the dateutil-free
    # parsing precedent).

    PSQL = (
        "SELECT application_name, client_addr, state, sync_state, replay_lag "
        "FROM pg_catalog.pg_stat_replication"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[PostgresqlReplicationClient]:
        """Parse pg_stat_replication rows into standby entries."""
        return [
            {
                "application_name": cast(str, row["application_name"] or ""),
                "client_addr": row["client_addr"],
                "state": row["state"],
                "sync_state": row["sync_state"],
                "replay_lag": row["replay_lag"],
            }
            for row in _parse_rows(output)
        ]

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        psql_user: str | None = None,
        psql_host: str | None = None,
        psql_port: int | None = None,
        psql_database: str | None = None,
    ) -> list[PostgresqlReplicationClient]:
        replication = getattr(host, "postgres_replication", None)
        if replication is not None:
            # SimulatedHost seam: the standby entries, copied per entry.
            return [
                cast(PostgresqlReplicationClient, dict(entry))
                for entry in replication
            ]
        return await cls._gather(
            host,
            psql_user=psql_user,
            psql_host=psql_host,
            psql_port=psql_port,
            psql_database=psql_database,
        )


class PostgresqlRoleMembership(TypedDict):
    """
    Attributes:
        role: The granted (group) role's name.
        member: The member role's name.
        admin_option: Whether the membership carries WITH ADMIN OPTION.
    """

    role: str
    member: str
    admin_option: bool


class PostgresqlRoleMemberships(_PostgresqlFact):
    """The role-to-role membership grants in the cluster.

    **Commands:**

    ```bash
    psql -Ac 'SELECT r.rolname AS role, m.rolname AS member, am.admin_option FROM pg_catalog.pg_auth_members am JOIN pg_catalog.pg_roles r ON r.oid = am.roleid JOIN pg_catalog.pg_roles m ON m.oid = am.member ORDER BY r.rolname, m.rolname'
    ```

    **Returns:**

    ```python
    list[PostgresqlRoleMembership]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/postgresql/postgresql_postgresql_role_memberships.py``.
    """

    # reemote5-original: backs the idempotence of ``postgresql.membership``
    # (GRANT role TO member). Cluster-wide. PostgreSQL 16 added per-grant
    # inherit_option/set_option columns to pg_auth_members; only the
    # long-stable admin_option is reported, so the fact works across
    # supported server versions.

    PSQL = (
        "SELECT r.rolname AS role, m.rolname AS member, am.admin_option "
        "FROM pg_catalog.pg_auth_members am "
        "JOIN pg_catalog.pg_roles r ON r.oid = am.roleid "
        "JOIN pg_catalog.pg_roles m ON m.oid = am.member "
        "ORDER BY r.rolname, m.rolname"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[PostgresqlRoleMembership]:
        """Parse pg_auth_members rows into membership entries."""
        return [
            {
                "role": cast(str, row["role"]),
                "member": cast(str, row["member"]),
                "admin_option": row["admin_option"] == "t",
            }
            for row in _parse_rows(output)
        ]

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        psql_user: str | None = None,
        psql_host: str | None = None,
        psql_port: int | None = None,
        psql_database: str | None = None,
    ) -> list[PostgresqlRoleMembership]:
        memberships = getattr(host, "postgres_role_memberships", None)
        if memberships is not None:
            # SimulatedHost seam: the membership entries, copied per entry.
            return [
                cast(PostgresqlRoleMembership, dict(entry)) for entry in memberships
            ]
        return await cls._gather(
            host,
            psql_user=psql_user,
            psql_host=psql_host,
            psql_port=psql_port,
            psql_database=psql_database,
        )


class PostgresqlRole(TypedDict):
    """
    Attributes:
        super: Whether the role is a superuser (``rolsuper``).
        inherit: Whether the role inherits granted roles' privileges.
        createrole: Whether the role may create roles.
        createdb: Whether the role may create databases.
        canlogin: Whether the role may log in (``rolcanlogin``).
        replication: Whether the role may initiate replication.
        connlimit: The role's connection limit; -1 means no limit.
        password: The stored password hash marker (``********``); None
            when the role has no password. Never the password itself.
        validuntil: The password expiry timestamp text; None when unset.
        bypassrls: Whether the role bypasses row-level security.
        config: The role's per-role configuration overrides; None when
            unset.
        oid: The role's OID.
    """

    super: bool
    inherit: bool
    createrole: bool
    createdb: bool
    canlogin: bool
    replication: bool
    connlimit: int | str
    password: str | None
    validuntil: str | None
    bypassrls: bool
    config: str | None
    oid: int | str


class PostgresqlRoles(_PostgresqlFact):
    """The cluster's roles and their attributes, keyed by role name.

    **Commands:**

    ```bash
    psql -Ac 'SELECT * FROM pg_catalog.pg_roles'
    ```

    **Returns:**

    ```python
    dict[str, PostgresqlRole]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/postgresql/postgresql_postgresql_roles.py``.
    """

    # Mirrors pyinfra's ``postgres.PostgresRoles`` (the ``postgresql``
    # alias is deprecated upstream): ``SELECT *`` over pg_roles, the
    # ``rol`` column prefix stripped, oid/connlimit coerced with try_int
    # and the t/f attribute columns coerced to bool. The name-keyed outer
    # dict is kept (the device-keyed-mapping exception) with a
    # PostgresqlRole entry per role. Note the fact keys are the *catalog*
    # names (``super``, ``canlogin``, ``connlimit``) -- the
    # ``postgresql.role`` operation maps its option names onto them.
    # ``password`` is only ever the server's ``********`` hash marker,
    # never a usable credential. pyinfra's ``requires_command`` gating
    # is dropped: a missing psql exits 127 (CommandNotFoundError).

    PSQL = "SELECT * FROM pg_catalog.pg_roles"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, PostgresqlRole]:
        """Parse pg_roles rows into the name-keyed role mapping."""
        roles: dict[str, PostgresqlRole] = {}
        for row in _parse_rows(output, remove_prefix="rol"):
            details = dict(row)
            for key, value in list(details.items()):
                if key in ("oid", "connlimit"):
                    details[key] = _try_int(value)
                if key in (
                    "super",
                    "inherit",
                    "createrole",
                    "createdb",
                    "canlogin",
                    "replication",
                    "bypassrls",
                ):
                    details[key] = value == "t"
            name = details.pop("name")
            roles[cast(str, name)] = cast(PostgresqlRole, details)
        return roles

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        psql_user: str | None = None,
        psql_host: str | None = None,
        psql_port: int | None = None,
        psql_database: str | None = None,
    ) -> dict[str, PostgresqlRole]:
        roles = getattr(host, "postgres_roles", None)
        if roles is not None:
            # SimulatedHost seam: role name -> PostgresqlRole, copied per
            # entry. One simulated cluster per host, so the connection
            # kwargs do not key the seam.
            return {
                name: cast(PostgresqlRole, dict(entry))
                for name, entry in roles.items()
            }
        return await cls._gather(
            host,
            psql_user=psql_user,
            psql_host=psql_host,
            psql_port=psql_port,
            psql_database=psql_database,
        )


class PostgresqlSchema(TypedDict):
    """
    Attributes:
        name: The schema name.
        owner: The owning role's name.
    """

    name: str
    owner: str


class PostgresqlSchemas(_PostgresqlFact):
    """The non-system schemas in a database, with their owners.

    **Commands:**

    ```bash
    psql -Ac 'SELECT n.nspname AS name, pg_catalog.pg_get_userbyid(n.nspowner) AS owner FROM pg_catalog.pg_namespace n WHERE n.nspname NOT LIKE '"'"'pg\\_%'"'"' AND n.nspname <> '"'"'information_schema'"'"' ORDER BY n.nspname'
    ```

    **Returns:**

    ```python
    list[PostgresqlSchema]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/postgresql/postgresql_postgresql_schemas.py``.
    """

    # reemote5-original: backs the idempotence of ``postgresql.schema``.
    # Database-scoped; the system schemas (pg_catalog, pg_toast, ...) and
    # information_schema are filtered out, but ``public`` is included --
    # it is a user schema an operator may re-own or drop.

    PSQL = (
        "SELECT n.nspname AS name, "
        "pg_catalog.pg_get_userbyid(n.nspowner) AS owner "
        "FROM pg_catalog.pg_namespace n "
        f"WHERE {_USER_SCHEMAS} ORDER BY n.nspname"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[PostgresqlSchema]:
        """Parse pg_namespace rows into schema entries."""
        return [
            {
                "name": cast(str, row["name"]),
                "owner": cast(str, row["owner"]),
            }
            for row in _parse_rows(output)
        ]

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        psql_user: str | None = None,
        psql_host: str | None = None,
        psql_port: int | None = None,
        psql_database: str | None = None,
    ) -> list[PostgresqlSchema]:
        schemas = getattr(host, "postgres_schemas", None)
        if schemas is not None:
            # SimulatedHost seam: database name -> schema entries.
            entries = schemas.get(psql_database or "postgres", [])
            return [cast(PostgresqlSchema, dict(entry)) for entry in entries]
        return await cls._gather(
            host,
            psql_user=psql_user,
            psql_host=psql_host,
            psql_port=psql_port,
            psql_database=psql_database,
        )


class PostgresqlSetting(TypedDict):
    """
    Attributes:
        name: The configuration parameter name, e.g. ``max_connections``.
        setting: The active value as text, in the parameter's ``unit``
            (e.g. ``262144`` in 8kB blocks for shared_buffers).
        unit: The value's unit (``8kB``, ``ms``, ...); None when unitless.
        source: Where the active value comes from (``default``,
            ``configuration file``, ``override``, ...).
        pending_restart: Whether a changed value is waiting for a server
            restart to take effect.
    """

    name: str
    setting: str
    unit: str | None
    source: str
    pending_restart: bool


class PostgresqlSettings(_PostgresqlFact):
    """The server's active configuration parameters (GUCs).

    **Commands:**

    ```bash
    psql -Ac 'SELECT name, setting, unit, source, pending_restart FROM pg_catalog.pg_settings ORDER BY name'
    ```

    **Returns:**

    ```python
    list[PostgresqlSetting]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/postgresql/postgresql_postgresql_settings.py``.
    """

    # reemote5-original: the configuration-drift visibility pyinfra
    # lacks; backs ``postgresql.setting``. Cluster-wide (GUCs are
    # server-scoped). ``setting`` is the raw pg_settings text in the
    # parameter's own unit -- the fact does NOT normalize units, and
    # neither does the operation (a deliberate simplification over
    # Ansible's postgresql_set: pass values in the canonical unit, e.g.
    # "262144" for shared_buffers in 8kB blocks, or accept that a
    # differently-spelled value re-issues ALTER SYSTEM each run).

    PSQL = (
        "SELECT name, setting, unit, source, pending_restart "
        "FROM pg_catalog.pg_settings ORDER BY name"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[PostgresqlSetting]:
        """Parse pg_settings rows into setting entries."""
        return [
            {
                "name": cast(str, row["name"]),
                "setting": cast(str, row["setting"] or ""),
                "unit": row["unit"],
                "source": cast(str, row["source"] or ""),
                "pending_restart": row["pending_restart"] == "t",
            }
            for row in _parse_rows(output)
        ]

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        psql_user: str | None = None,
        psql_host: str | None = None,
        psql_port: int | None = None,
        psql_database: str | None = None,
    ) -> list[PostgresqlSetting]:
        settings = getattr(host, "postgres_settings", None)
        if settings is not None:
            # SimulatedHost seam: the setting entries, copied per entry.
            return [cast(PostgresqlSetting, dict(entry)) for entry in settings]
        return await cls._gather(
            host,
            psql_user=psql_user,
            psql_host=psql_host,
            psql_port=psql_port,
            psql_database=psql_database,
        )


class PostgresqlSubscription(TypedDict):
    """
    Attributes:
        name: The subscription name.
        owner: The owning role's name.
        enabled: Whether the subscription is enabled.
        publications: The publication names on the publisher, in
            catalog order; empty when none are set.
    """

    name: str
    owner: str
    enabled: bool
    publications: list[str]


class PostgresqlSubscriptions(_PostgresqlFact):
    """The logical-replication subscriptions in a database.

    **Commands:**

    ```bash
    psql -Ac 'SELECT s.subname AS name, pg_catalog.pg_get_userbyid(s.subowner) AS owner, s.subenabled AS enabled, s.subpublications AS publications FROM pg_catalog.pg_subscription s ORDER BY s.subname'
    ```

    **Returns:**

    ```python
    list[PostgresqlSubscription]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/postgresql/postgresql_postgresql_subscriptions.py``.
    """

    # reemote5-original: backs the idempotence of
    # ``postgresql.subscription``. Database-scoped. ``subconninfo`` is
    # deliberately NOT selected -- the connection string can embed a
    # password, and reemote5 facts never surface credentials (the same
    # reason pg_stat_activity's query text is dropped). The operation
    # therefore cannot detect connection-string drift, only publication
    # and enabled-state drift. Reading pg_subscription requires
    # superuser (or pg_read_all_stats on newer servers); a
    # permission-denied psql exits non-zero and surfaces as CommandError
    # at gather time, not a silent empty list.

    PSQL = (
        "SELECT s.subname AS name, "
        "pg_catalog.pg_get_userbyid(s.subowner) AS owner, "
        "s.subenabled AS enabled, s.subpublications AS publications "
        "FROM pg_catalog.pg_subscription s ORDER BY s.subname"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[PostgresqlSubscription]:
        """Parse pg_subscription rows into subscription entries."""
        return [
            {
                "name": cast(str, row["name"]),
                "owner": cast(str, row["owner"]),
                "enabled": row["enabled"] == "t",
                "publications": _parse_array(row["publications"]),
            }
            for row in _parse_rows(output)
        ]

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        psql_user: str | None = None,
        psql_host: str | None = None,
        psql_port: int | None = None,
        psql_database: str | None = None,
    ) -> list[PostgresqlSubscription]:
        subscriptions = getattr(host, "postgres_subscriptions", None)
        if subscriptions is not None:
            # SimulatedHost seam: database name -> subscription entries;
            # the publications list is copied along with the entry dict.
            entries = subscriptions.get(psql_database or "postgres", [])
            return [
                cast(
                    PostgresqlSubscription,
                    {**entry, "publications": list(entry["publications"])},
                )
                for entry in entries
            ]
        return await cls._gather(
            host,
            psql_user=psql_user,
            psql_host=psql_host,
            psql_port=psql_port,
            psql_database=psql_database,
        )


class PostgresqlTable(TypedDict):
    """
    Attributes:
        schema: The schema the table lives in.
        name: The table name.
        owner: The owning role's name.
        tablespace: The table's tablespace name; None when it uses the
            database default.
        rows: The planner's row estimate (``reltuples``); -1 when the
            table has never been vacuumed or analyzed.
    """

    schema: str
    name: str
    owner: str
    tablespace: str | None
    rows: int


class PostgresqlTables(_PostgresqlFact):
    """The user tables in a database, with owners and row estimates.

    **Commands:**

    ```bash
    psql -Ac 'SELECT n.nspname AS schema, c.relname AS name, pg_catalog.pg_get_userbyid(c.relowner) AS owner, ts.spcname AS tablespace, c.reltuples::bigint AS rows FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace LEFT JOIN pg_catalog.pg_tablespace ts ON ts.oid = c.reltablespace WHERE c.relkind IN ('"'"'r'"'"', '"'"'p'"'"') AND n.nspname NOT LIKE '"'"'pg\\_%'"'"' AND n.nspname <> '"'"'information_schema'"'"' ORDER BY n.nspname, c.relname'
    ```

    **Returns:**

    ```python
    list[PostgresqlTable]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/postgresql/postgresql_postgresql_tables.py``.
    """

    # reemote5-original: table-level visibility pyinfra lacks.
    # Database-scoped; ordinary and partitioned tables (relkind r/p),
    # system schemas filtered out. ``rows`` is the planner ESTIMATE
    # (reltuples cast to bigint so a fractional estimate never carries a
    # decimal point), not a COUNT(*) -- counting every table would be a
    # full scan per fact read. Views/sequences are covered by
    # PostgresqlPrivileges' object sweep instead.

    PSQL = (
        "SELECT n.nspname AS schema, c.relname AS name, "
        "pg_catalog.pg_get_userbyid(c.relowner) AS owner, "
        "ts.spcname AS tablespace, c.reltuples::bigint AS rows "
        "FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "LEFT JOIN pg_catalog.pg_tablespace ts ON ts.oid = c.reltablespace "
        f"WHERE c.relkind IN ('r', 'p') AND {_USER_SCHEMAS} "
        "ORDER BY n.nspname, c.relname"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[PostgresqlTable]:
        """Parse the table join rows into table entries."""
        return [
            {
                "schema": cast(str, row["schema"]),
                "name": cast(str, row["name"]),
                "owner": cast(str, row["owner"]),
                "tablespace": row["tablespace"],
                "rows": int(row["rows"] or -1),
            }
            for row in _parse_rows(output)
        ]

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        psql_user: str | None = None,
        psql_host: str | None = None,
        psql_port: int | None = None,
        psql_database: str | None = None,
    ) -> list[PostgresqlTable]:
        tables = getattr(host, "postgres_tables", None)
        if tables is not None:
            # SimulatedHost seam: database name -> table entries.
            entries = tables.get(psql_database or "postgres", [])
            return [cast(PostgresqlTable, dict(entry)) for entry in entries]
        return await cls._gather(
            host,
            psql_user=psql_user,
            psql_host=psql_host,
            psql_port=psql_port,
            psql_database=psql_database,
        )


class PostgresqlTablespace(TypedDict):
    """
    Attributes:
        oid: The tablespace's OID (what ``pg_database.dattablespace``
            stores -- the database operation resolves names through it).
        name: The tablespace name.
        owner: The owning role's name.
        location: The filesystem directory; None for the built-in
            ``pg_default``/``pg_global`` tablespaces, whose location
            lives inside the data directory.
    """

    oid: int
    name: str
    owner: str
    location: str | None


class PostgresqlTablespaces(_PostgresqlFact):
    """The cluster's tablespaces and their filesystem locations.

    **Commands:**

    ```bash
    psql -Ac 'SELECT oid, spcname AS name, pg_catalog.pg_get_userbyid(spcowner) AS owner, pg_catalog.pg_tablespace_location(oid) AS location FROM pg_catalog.pg_tablespace ORDER BY spcname'
    ```

    **Returns:**

    ```python
    list[PostgresqlTablespace]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/postgresql/postgresql_postgresql_tablespaces.py``.
    """

    # reemote5-original: backs ``postgresql.tablespace`` and lets
    # ``postgresql.database`` resolve a tablespace NAME to the OID its
    # fact reports (pyinfra's database operation compares the OID column
    # against a name string, which never matches -- a bug this OID
    # column exists to avoid). Cluster-wide.
    # pg_tablespace_location answers '' for pg_default/pg_global, which
    # reads as None.

    PSQL = (
        "SELECT oid, spcname AS name, "
        "pg_catalog.pg_get_userbyid(spcowner) AS owner, "
        "pg_catalog.pg_tablespace_location(oid) AS location "
        "FROM pg_catalog.pg_tablespace ORDER BY spcname"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[PostgresqlTablespace]:
        """Parse pg_tablespace rows into tablespace entries."""
        return [
            {
                "oid": int(row["oid"] or 0),
                "name": cast(str, row["name"]),
                "owner": cast(str, row["owner"]),
                "location": row["location"],
            }
            for row in _parse_rows(output)
        ]

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        psql_user: str | None = None,
        psql_host: str | None = None,
        psql_port: int | None = None,
        psql_database: str | None = None,
    ) -> list[PostgresqlTablespace]:
        tablespaces = getattr(host, "postgres_tablespaces", None)
        if tablespaces is not None:
            # SimulatedHost seam: the tablespace entries, copied per entry.
            return [cast(PostgresqlTablespace, dict(entry)) for entry in tablespaces]
        return await cls._gather(
            host,
            psql_user=psql_user,
            psql_host=psql_host,
            psql_port=psql_port,
            psql_database=psql_database,
        )


# --- Operations --------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class _PsqlCommand(Command):
    """Shared psql connection fields for every postgresql descriptor.

    ``kw_only`` so concrete descriptors can declare their required
    operands positionally while the connection tail keeps its defaults.
    Never exported or rendered; the concrete descriptors beside each
    operation carry the SQL semantics.
    """

    psql_user: str | None = None
    psql_host: str | None = None
    psql_port: int | None = None
    psql_database: str | None = None

    def _base(self, executable: str = "psql") -> str:
        return _psql_base(
            psql_user=self.psql_user,
            psql_host=self.psql_host,
            psql_port=self.psql_port,
            psql_database=self.psql_database,
            executable=executable,
        )

    def _sql_shell(self, sql: str) -> str:
        return f"{self._base()} -Ac {shlex.quote(sql)}"


@dataclass(frozen=True)
class PostgresqlDatabaseCommand(_PsqlCommand):
    """``psql -Ac 'CREATE/ALTER/DROP DATABASE ...'``: manage one database.

    One present/alter-flagged descriptor for all three directions (the
    ``server.ModprobeCommand`` precedent): ``present=False`` renders DROP
    DATABASE, ``alter=True`` renders one ALTER DATABASE statement per
    non-None field (OWNER TO / SET TABLESPACE / CONNECTION LIMIT / WITH
    ALLOW_CONNECTIONS), and the create form renders CREATE DATABASE with
    the pyinfra clause order (OWNER, TEMPLATE, ENCODING, LC_COLLATE,
    LC_CTYPE, TABLESPACE, CONNECTION LIMIT) plus ALLOW_CONNECTIONS.
    Identifiers are double-quote-escaped and literals single-quote-
    escaped (pyinfra interpolates raw); ENCODING is rendered as a quoted
    literal, which PostgreSQL accepts identically to pyinfra's bare word.
    """

    database: str
    present: bool = True
    alter: bool = False
    owner: str | None = None
    template: str | None = None
    encoding: str | None = None
    lc_collate: str | None = None
    lc_ctype: str | None = None
    tablespace: str | None = None
    connection_limit: int | None = None
    allow_connections: bool | None = None

    def shell(self) -> str:
        name = _quote_ident(self.database)
        if not self.present:
            return self._sql_shell(f"DROP DATABASE {name}")
        if self.alter:
            statements = []
            if self.owner is not None:
                statements.append(
                    f"ALTER DATABASE {name} OWNER TO {_quote_ident(self.owner)};"
                )
            if self.tablespace is not None:
                statements.append(
                    f"ALTER DATABASE {name} "
                    f"SET TABLESPACE {_quote_ident(self.tablespace)};"
                )
            if self.connection_limit is not None:
                statements.append(
                    f"ALTER DATABASE {name} "
                    f"CONNECTION LIMIT {self.connection_limit};"
                )
            if self.allow_connections is not None:
                flag = "true" if self.allow_connections else "false"
                statements.append(
                    f"ALTER DATABASE {name} WITH ALLOW_CONNECTIONS {flag};"
                )
            return self._sql_shell(" ".join(statements))
        bits = [f"CREATE DATABASE {name}"]
        if self.owner:
            bits.append(f"OWNER {_quote_ident(self.owner)}")
        if self.template:
            bits.append(f"TEMPLATE {_quote_ident(self.template)}")
        if self.encoding:
            bits.append(f"ENCODING {_quote_literal(self.encoding)}")
        if self.lc_collate:
            bits.append(f"LC_COLLATE {_quote_literal(self.lc_collate)}")
        if self.lc_ctype:
            bits.append(f"LC_CTYPE {_quote_literal(self.lc_ctype)}")
        if self.tablespace:
            bits.append(f"TABLESPACE {_quote_ident(self.tablespace)}")
        if self.connection_limit is not None:
            bits.append(f"CONNECTION LIMIT {self.connection_limit}")
        if self.allow_connections is not None:
            flag = "true" if self.allow_connections else "false"
            bits.append(f"ALLOW_CONNECTIONS {flag}")
        return self._sql_shell(" ".join(bits))


async def database(
    ctx: OpContext,
    database: str,
    present: bool = True,
    owner: str | None = None,
    template: str | None = None,
    encoding: str | None = None,
    lc_collate: str | None = None,
    lc_ctype: str | None = None,
    tablespace: str | None = None,
    connection_limit: int | None = None,
    allow_connections: bool | None = None,
    psql_user: str | None = None,
    psql_host: str | None = None,
    psql_port: int | None = None,
    psql_database: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add, alter or remove a PostgreSQL database.

    Args:
        ctx (OpContext): Execution context for the operation.
        database (str): Name of the database to manage.
        present (bool, optional): Whether the database should exist.
            Defaults to True.
        owner (str | None, optional): The role that should own the
            database; altered on an existing database when it differs.
            Defaults to None.
        template (str | None, optional): Template database to create
            from; creation-only (immutable afterwards). Defaults to None.
        encoding (str | None, optional): Character encoding, e.g. UTF8;
            creation-only. Defaults to None.
        lc_collate (str | None, optional): Collation locale;
            creation-only. Defaults to None.
        lc_ctype (str | None, optional): Character-classification locale;
            creation-only. Defaults to None.
        tablespace (str | None, optional): Default tablespace name;
            moved (SET TABLESPACE) on an existing database when it
            differs. Defaults to None.
        connection_limit (int | None, optional): Connection limit (-1
            for unlimited); altered when it differs. Defaults to None.
        allow_connections (bool | None, optional): Whether connections
            are allowed; altered (WITH ALLOW_CONNECTIONS) when it
            differs. Defaults to None.
        psql_user (str | None, optional): Role to connect as.
            Defaults to None.
        psql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        psql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        psql_database (str | None, optional): Database to connect to for
            issuing the command. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated PostgreSQL database commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, OperationError
    ```

    Examples:
        Examples can be found in ``examples/modules/postgresql/postgresql_database.py``.
    """

    # pyinfra ``postgresql.database`` parity, extended: pyinfra already
    # alters an existing database's OWNER/TABLESPACE/CONNECTION LIMIT,
    # but compares the tablespace NAME against the fact's tablespace OID
    # column (which never matches); reemote5 resolves the name through
    # PostgresqlTablespaces and compares OIDs, raising OperationError
    # when the named tablespace does not exist (fail-fast in prepare
    # rather than a server error mid-apply). ALLOW_CONNECTIONS support
    # is a reemote5 addition (the requested database-enhancement).
    # TEMPLATE/ENCODING/LC_COLLATE/LC_CTYPE are creation-only: on an
    # existing database a requested value that differs is reported as
    # skipped in the reason, never altered (pyinfra's noop behaviour).
    # pyinfra's ``psql_password`` is deliberately dropped (credentials
    # come from inventory privilege / peer auth / .pgpass, never the
    # operation). Needs a superuser or the database owner: flag the
    # request ``sudo=True`` with ``privilege.sudo_user = "postgres"``.
    # ``**kwargs`` absorbs pyinfra's global arguments (``op()`` already
    # strips ``name``/``sudo``/``su``).
    conn = {
        "psql_user": psql_user,
        "psql_host": psql_host,
        "psql_port": psql_port,
        "psql_database": psql_database,
    }
    current = await ctx.facts.get(PostgresqlDatabases, **conn)
    reasons: list[str] = []

    if not present:
        if database in current:
            ctx.changed = True
            reasons.append(f"postgresql database {database} will be dropped")
            yield PostgresqlDatabaseCommand(database=database, present=False, **conn)
        else:
            reasons.append(f"postgresql database {database} does not exist")
        ctx.reason = "; ".join(reasons)
        return

    tablespace_oid: int | None = None
    if tablespace:
        spaces = await ctx.facts.get(PostgresqlTablespaces, **conn)
        by_name = {space["name"]: space for space in spaces}
        if tablespace not in by_name:
            raise OperationError(
                f"postgresql tablespace {tablespace} does not exist; "
                "create it with postgresql.tablespace first"
            )
        tablespace_oid = by_name[tablespace]["oid"]

    if database not in current:
        ctx.changed = True
        yield PostgresqlDatabaseCommand(
            database=database,
            owner=owner,
            template=template,
            encoding=encoding,
            lc_collate=lc_collate,
            lc_ctype=lc_ctype,
            tablespace=tablespace,
            connection_limit=connection_limit,
            allow_connections=allow_connections,
            **conn,
        )
        ctx.reason = f"postgresql database {database} will be created"
        return

    entry = current[database]
    current_encoding = entry.get("encoding")
    for label, value, current_value in (
        ("TEMPLATE", template, None),
        # pg_encoding_to_char reports upper-case names (UTF8); compare
        # case-insensitively so "utf8" does not read as a change.
        (
            "ENCODING",
            encoding.upper() if encoding else None,
            current_encoding.upper() if current_encoding else None,
        ),
        ("LC_COLLATE", lc_collate, entry.get("collate")),
        ("LC_CTYPE", lc_ctype, entry.get("ctype")),
    ):
        if value and (current_value is None or current_value != value):
            reasons.append(f"{label} cannot be changed on an existing database")

    changed_owner = owner if owner and entry.get("owner") != owner else None
    changed_tablespace = (
        tablespace
        if tablespace and entry.get("tablespace") != tablespace_oid
        else None
    )
    changed_limit = (
        connection_limit
        if connection_limit is not None
        and entry.get("connlimit") != connection_limit
        else None
    )
    changed_allow = (
        allow_connections
        if allow_connections is not None
        and entry.get("allowconn") != allow_connections
        else None
    )
    if (
        changed_owner is not None
        or changed_tablespace is not None
        or changed_limit is not None
        or changed_allow is not None
    ):
        ctx.changed = True
        yield PostgresqlDatabaseCommand(
            database=database,
            alter=True,
            owner=changed_owner,
            tablespace=changed_tablespace,
            connection_limit=changed_limit,
            allow_connections=changed_allow,
            **conn,
        )
        reasons.insert(0, f"postgresql database {database} will be altered")
    else:
        reasons.insert(
            0,
            f"postgresql database {database} already exists "
            "with the same parameters",
        )
    ctx.reason = "; ".join(reasons)


@dataclass(frozen=True)
class PostgresqlDumpCommand(_PsqlCommand):
    """``pg_dump ... > <dest>``: dump a database to a host-side file.

    The stateless mutation ``postgresql.dump`` yields. ``dest`` is a path
    on the TARGET host (a shell redirect, not a transfer -- reemote5's
    IO-only transfer rule concerns controller-side paths, which pyinfra's
    dump never takes either). Under ``sudo=True`` the driver's
    ``run_shell_privileged`` keeps the redirect inside the elevated
    shell, so the dump file is written as the run-as user.
    """

    dest: str

    def shell(self) -> str:
        return f"{self._base('pg_dump')} > {shlex.quote(self.dest)}"


async def dump(
    ctx: OpContext,
    dest: str,
    psql_user: str | None = None,
    psql_host: str | None = None,
    psql_port: int | None = None,
    psql_database: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Dump a PostgreSQL database into a host-side ``.sql`` file.

    Args:
        ctx (OpContext): Execution context for the operation.
        dest (str): Path on the target host to write the dump to.
        psql_user (str | None, optional): Role to connect as.
            Defaults to None.
        psql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        psql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        psql_database (str | None, optional): Database to dump.
            Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The pg_dump command, always (the operation is stateless).

    **Raises:**

    ```python
    None
    ```

    Examples:
        Examples can be found in ``examples/modules/postgresql/postgresql_dump.py``.
    """

    # pyinfra ``postgresql.dump`` parity: non-idempotent, always yields
    # (pyinfra marks it is_idempotent=False). Requires ``pg_dump`` on the
    # target host. ``psql_password`` is deliberately dropped (the
    # namespace credentials decision). ``**kwargs`` absorbs pyinfra's
    # global arguments.
    ctx.changed = True
    ctx.reason = f"postgresql database will be dumped to {dest}"
    yield PostgresqlDumpCommand(
        dest=dest,
        psql_user=psql_user,
        psql_host=psql_host,
        psql_port=psql_port,
        psql_database=psql_database,
    )


@dataclass(frozen=True)
class PostgresqlExtensionCommand(_PsqlCommand):
    """``psql -Ac 'CREATE/ALTER/DROP EXTENSION ...'``: manage one extension.

    ``present=False`` renders DROP EXTENSION (with CASCADE when asked);
    ``alter_schema``/``alter_version`` render ALTER EXTENSION SET SCHEMA
    / UPDATE TO; the create form renders CREATE EXTENSION with optional
    WITH SCHEMA and VERSION clauses.
    """

    extension: str
    present: bool = True
    schema: str | None = None
    version: str | None = None
    alter_schema: bool = False
    alter_version: bool = False
    cascade: bool = False

    def shell(self) -> str:
        name = _quote_ident(self.extension)
        if not self.present:
            sql = f"DROP EXTENSION {name}"
            if self.cascade:
                sql += " CASCADE"
            return self._sql_shell(sql)
        if self.alter_schema:
            return self._sql_shell(
                f"ALTER EXTENSION {name} SET SCHEMA {_quote_ident(self.schema or '')}"
            )
        if self.alter_version:
            return self._sql_shell(
                f"ALTER EXTENSION {name} UPDATE TO {_quote_literal(self.version or '')}"
            )
        bits = [f"CREATE EXTENSION {name}"]
        if self.schema:
            bits.append(f"WITH SCHEMA {_quote_ident(self.schema)}")
        if self.version:
            bits.append(f"VERSION {_quote_literal(self.version)}")
        return self._sql_shell(" ".join(bits))


async def extension(
    ctx: OpContext,
    extension: str,
    schema: str | None = None,
    version: str | None = None,
    present: bool = True,
    cascade: bool = False,
    psql_user: str | None = None,
    psql_host: str | None = None,
    psql_port: int | None = None,
    psql_database: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Install, update or remove a PostgreSQL extension in a database.

    Args:
        ctx (OpContext): Execution context for the operation.
        extension (str): Name of the extension, e.g. ``pg_stat_statements``.
        schema (str | None, optional): Schema to install the extension's
            objects into; moved (SET SCHEMA) when it differs and the
            extension is relocatable. Defaults to None.
        version (str | None, optional): Extension version to install or
            update to. Defaults to None.
        present (bool, optional): Whether the extension should be
            installed. Defaults to True.
        cascade (bool, optional): DROP EXTENSION ... CASCADE on removal.
            Defaults to False.
        psql_user (str | None, optional): Role to connect as.
            Defaults to None.
        psql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        psql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        psql_database (str | None, optional): Database to manage the
            extension in. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated PostgreSQL extension commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/postgresql/postgresql_extension.py``.
    """

    # reemote5-original (pyinfra has no extension operation): idempotent
    # against PostgresqlExtensions -- absent -> CREATE EXTENSION (the
    # server enforces availability of the control file), present with a
    # differing schema -> ALTER EXTENSION SET SCHEMA, differing version
    # -> ALTER EXTENSION UPDATE TO, unwanted -> DROP EXTENSION. The
    # Ansible community.postgresql postgresql_ext parameter shape,
    # minus its ``trust``/version-downgrade handling: a requested version
    # older than the installed one still renders UPDATE TO and the
    # server decides. Needs superuser for most extensions: flag the
    # request ``sudo=True`` with ``privilege.sudo_user = "postgres"``.
    # ``**kwargs`` absorbs pyinfra's global arguments.
    conn = {
        "psql_user": psql_user,
        "psql_host": psql_host,
        "psql_port": psql_port,
        "psql_database": psql_database,
    }
    extensions = await ctx.facts.get(PostgresqlExtensions, **conn)
    current = next((e for e in extensions if e["name"] == extension), None)
    reasons: list[str] = []

    if not present:
        if current is not None:
            ctx.changed = True
            reasons.append(f"postgresql extension {extension} will be dropped")
            yield PostgresqlExtensionCommand(
                extension=extension, present=False, cascade=cascade, **conn
            )
        else:
            reasons.append(f"postgresql extension {extension} does not exist")
        ctx.reason = "; ".join(reasons)
        return

    if current is None:
        ctx.changed = True
        yield PostgresqlExtensionCommand(
            extension=extension, schema=schema, version=version, **conn
        )
        ctx.reason = f"postgresql extension {extension} will be created"
        return

    if schema and current["schema"] != schema:
        ctx.changed = True
        reasons.append(f"postgresql extension {extension} will move to schema {schema}")
        yield PostgresqlExtensionCommand(
            extension=extension, schema=schema, alter_schema=True, **conn
        )
    if version and current["version"] != version:
        ctx.changed = True
        reasons.append(
            f"postgresql extension {extension} will update to version {version}"
        )
        yield PostgresqlExtensionCommand(
            extension=extension, version=version, alter_version=True, **conn
        )
    if not reasons:
        reasons.append(f"postgresql extension {extension} is already installed")
    ctx.reason = "; ".join(reasons)


# The built-in access methods CREATE INDEX ... USING accepts (the pg_am
# set every server ships). The operation whitelist-validates ``method``
# against this tuple so a caller string never reaches the SQL unquoted.
_INDEX_METHODS = ("btree", "hash", "gin", "gist", "spgist", "brin")


@dataclass(frozen=True)
class PostgresqlIndexCommand(_PsqlCommand):
    """``psql -Ac 'CREATE/DROP INDEX ...'``: manage one index.

    The create form renders CREATE [UNIQUE] INDEX [CONCURRENTLY] with
    the access method, column list and tablespace; ``present=False``
    renders DROP INDEX [CONCURRENTLY] [CASCADE]. CONCURRENTLY runs
    outside a transaction block, which ``psql -c`` single-command mode
    satisfies.
    """

    index: str
    table: str = ""
    columns: tuple[str, ...] = ()
    unique: bool = False
    method: str = "btree"
    tablespace: str | None = None
    concurrent: bool = False
    present: bool = True
    cascade: bool = False

    def shell(self) -> str:
        name = _quote_qualified(self.index)
        if not self.present:
            sql = "DROP INDEX"
            if self.concurrent:
                sql += " CONCURRENTLY"
            sql += f" {name}"
            if self.cascade:
                sql += " CASCADE"
            return self._sql_shell(sql)
        bits = ["CREATE"]
        if self.unique:
            bits.append("UNIQUE")
        bits.append("INDEX")
        if self.concurrent:
            bits.append("CONCURRENTLY")
        bits.append(f"{name} ON {_quote_qualified(self.table)}")
        if self.method and self.method != "btree":
            # Rendered bare (USING "gin" is not idiomatic); the operation
            # whitelist-validates method against _INDEX_METHODS first.
            bits.append(f"USING {self.method}")
        bits.append(f"({', '.join(self.columns)})")
        if self.tablespace:
            bits.append(f"TABLESPACE {_quote_ident(self.tablespace)}")
        return self._sql_shell(" ".join(bits))


async def index(
    ctx: OpContext,
    index: str,
    table: str | None = None,
    columns: list[str] | None = None,
    unique: bool = False,
    method: str = "btree",
    tablespace: str | None = None,
    concurrent: bool = False,
    present: bool = True,
    cascade: bool = False,
    psql_user: str | None = None,
    psql_host: str | None = None,
    psql_port: int | None = None,
    psql_database: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Create or drop an index on a PostgreSQL table.

    Args:
        ctx (OpContext): Execution context for the operation.
        index (str): Name of the index (may be schema-qualified).
        table (str | None, optional): Table to index; required when
            present is True. Defaults to None.
        columns (list[str] | None, optional): Column names or
            expressions to index, in order; required when present is
            True. Defaults to None.
        unique (bool, optional): Create a UNIQUE index. Defaults to False.
        method (str, optional): Access method; one of btree, hash, gin,
            gist, spgist, brin (validated). Defaults to "btree".
        tablespace (str | None, optional): Tablespace for the index.
            Defaults to None.
        concurrent (bool, optional): CREATE/DROP INDEX CONCURRENTLY (no
            write lock on the table). Defaults to False.
        present (bool, optional): Whether the index should exist.
            Defaults to True.
        cascade (bool, optional): DROP INDEX ... CASCADE on removal.
            Defaults to False.
        psql_user (str | None, optional): Role to connect as.
            Defaults to None.
        psql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        psql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        psql_database (str | None, optional): Database holding the
            table. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated PostgreSQL index commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, OperationError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/postgresql/postgresql_index.py``.
    """

    # reemote5-original (pyinfra has no index operation), the Ansible
    # postgresql_idx shape. Idempotent against PostgresqlIndexes keyed on
    # the index name (looked up by its unqualified part in the named or
    # public schema). An existing index whose table/unique/method/
    # tablespace differs raises OperationError rather than silently
    # rebuilding it -- index definitions are not altered in place, and a
    # drop/recreate of a possibly UNIQUE constraint-backing index is too
    # destructive to infer. The column list is not compared (the fact
    # does not report indkey expansion); column drift is out of scope.
    # The table comparison is unqualified (the fact reports t.relname
    # without its schema), so a same-named table in a different schema
    # is not detected as table drift. ``columns`` entries are rendered
    # verbatim inside the parentheses so expressions (``lower(email)``)
    # work; they are SQL, not shell, and never reach the shell unquoted
    # (the whole statement is one shlex-quoted psql argument). ``method``
    # is whitelist-validated against _INDEX_METHODS so it too never
    # reaches the SQL unquoted. ``**kwargs`` absorbs pyinfra's global
    # arguments.
    if present and (not table or not columns):
        raise ValueError(
            "postgresql.index requires table and columns when present=True"
        )
    if method not in _INDEX_METHODS:
        raise ValueError(
            f"unknown postgresql index method {method!r}; expected one "
            f"of {', '.join(_INDEX_METHODS)}"
        )
    conn = {
        "psql_user": psql_user,
        "psql_host": psql_host,
        "psql_port": psql_port,
        "psql_database": psql_database,
    }
    indexes = await ctx.facts.get(PostgresqlIndexes, **conn)
    schema, _, bare = index.rpartition(".")
    current = next(
        (
            e
            for e in indexes
            if e["name"] == bare and (not schema or e["schema"] == schema)
        ),
        None,
    )

    if not present:
        if current is not None:
            ctx.changed = True
            ctx.reason = f"postgresql index {index} will be dropped"
            yield PostgresqlIndexCommand(
                index=index, concurrent=concurrent, present=False,
                cascade=cascade, **conn,
            )
        else:
            ctx.reason = f"postgresql index {index} does not exist"
        return

    if current is None:
        ctx.changed = True
        ctx.reason = f"postgresql index {index} will be created"
        yield PostgresqlIndexCommand(
            index=index,
            table=table or "",
            columns=tuple(columns or ()),
            unique=unique,
            method=method,
            tablespace=tablespace,
            concurrent=concurrent,
            **conn,
        )
        return

    drift = [
        label
        for label, want, have in (
            ("table", (table or "").rpartition(".")[2], current["table"]),
            ("unique", unique, current["unique"]),
            ("method", method, current["method"]),
        )
        if want != have
    ]
    # tablespace=None means "don't care", so it only participates in the
    # drift check when the caller named one.
    if tablespace is not None and tablespace != current["tablespace"]:
        drift.append("tablespace")
    if drift:
        raise OperationError(
            f"postgresql index {index} exists with a different definition "
            f"({', '.join(sorted(drift))} differ); drop it first to rebuild"
        )
    ctx.reason = f"postgresql index {index} already exists"


@dataclass(frozen=True)
class PostgresqlLoadCommand(_PsqlCommand):
    """``psql ... < <src>``: load a host-side SQL file into a database.

    The stateless mutation ``postgresql.load`` yields. ``src`` is a path
    on the TARGET host (a shell redirect, not a transfer); to load
    controller-side bytes, spool them with ``files.put`` first.
    """

    src: str

    def shell(self) -> str:
        return f"{self._base()} < {shlex.quote(self.src)}"


async def load(
    ctx: OpContext,
    src: str,
    psql_user: str | None = None,
    psql_host: str | None = None,
    psql_port: int | None = None,
    psql_database: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Load a host-side ``.sql`` file into a PostgreSQL database.

    Args:
        ctx (OpContext): Execution context for the operation.
        src (str): Path on the target host to read the SQL from.
        psql_user (str | None, optional): Role to connect as.
            Defaults to None.
        psql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        psql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        psql_database (str | None, optional): Database to load into.
            Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The psql load command, always (the operation is stateless).

    **Raises:**

    ```python
    None
    ```

    Examples:
        Examples can be found in ``examples/modules/postgresql/postgresql_load.py``.
    """

    # pyinfra ``postgresql.load`` parity: non-idempotent, always yields
    # (pyinfra marks it is_idempotent=False). ``psql_password`` is
    # deliberately dropped (the namespace credentials decision).
    # ``**kwargs`` absorbs pyinfra's global arguments.
    ctx.changed = True
    ctx.reason = f"{src} will be loaded into the postgresql database"
    yield PostgresqlLoadCommand(
        src=src,
        psql_user=psql_user,
        psql_host=psql_host,
        psql_port=psql_port,
        psql_database=psql_database,
    )


@dataclass(frozen=True)
class PostgresqlMembershipCommand(_PsqlCommand):
    """``psql -Ac 'GRANT/REVOKE <role> TO/FROM <member>'``: role membership.

    ``present=True`` renders GRANT "role" TO "member" (WITH ADMIN OPTION
    when asked); ``present=False`` renders REVOKE "role" FROM "member",
    or REVOKE ADMIN OPTION FOR when only the admin option is withdrawn
    (``admin_only``).
    """

    member: str
    role: str
    present: bool = True
    admin_option: bool = False
    admin_only: bool = False

    def shell(self) -> str:
        group = _quote_ident(self.role)
        member = _quote_ident(self.member)
        if self.present:
            sql = f"GRANT {group} TO {member}"
            if self.admin_option:
                sql += " WITH ADMIN OPTION"
            return self._sql_shell(sql)
        if self.admin_only:
            return self._sql_shell(
                f"REVOKE ADMIN OPTION FOR {group} FROM {member}"
            )
        return self._sql_shell(f"REVOKE {group} FROM {member}")


async def membership(
    ctx: OpContext,
    role: str,
    target_role: str,
    present: bool = True,
    admin_option: bool = False,
    psql_user: str | None = None,
    psql_host: str | None = None,
    psql_port: int | None = None,
    psql_database: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Grant or revoke a role's membership in another (group) role.

    Args:
        ctx (OpContext): Execution context for the operation.
        role (str): The member role.
        target_role (str): The group role the member should belong to.
        present (bool, optional): Whether the membership should exist.
            Defaults to True.
        admin_option (bool, optional): Whether the membership carries
            WITH ADMIN OPTION (the member may grant it on); reconciled
            in both directions on an existing membership. Defaults to
            False.
        psql_user (str | None, optional): Role to connect as.
            Defaults to None.
        psql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        psql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        psql_database (str | None, optional): Database to connect to for
            issuing the command. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated PostgreSQL membership commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/postgresql/postgresql_membership.py``.
    """

    # reemote5-original (pyinfra has no membership operation), the
    # Ansible postgresql_membership shape with a single group per call.
    # Idempotent against PostgresqlRoleMemberships: absent membership ->
    # GRANT target_role TO role; admin-option drift -> GRANT WITH ADMIN
    # OPTION (upgrade) or REVOKE ADMIN OPTION FOR (downgrade, membership
    # kept); unwanted membership -> REVOKE. PostgreSQL 16's per-grant
    # INHERIT/SET options are not managed (the fact reports only
    # admin_option). Needs CREATEROLE/admin on the group: flag the
    # request ``sudo=True`` with ``privilege.sudo_user = "postgres"``.
    # ``**kwargs`` absorbs pyinfra's global arguments.
    conn = {
        "psql_user": psql_user,
        "psql_host": psql_host,
        "psql_port": psql_port,
        "psql_database": psql_database,
    }
    memberships = await ctx.facts.get(PostgresqlRoleMemberships, **conn)
    current = next(
        (
            m
            for m in memberships
            if m["role"] == target_role and m["member"] == role
        ),
        None,
    )

    if not present:
        if current is not None:
            ctx.changed = True
            ctx.reason = (
                f"postgresql role {role} will be removed from {target_role}"
            )
            yield PostgresqlMembershipCommand(
                member=role, role=target_role, present=False, **conn
            )
        else:
            ctx.reason = (
                f"postgresql role {role} is not a member of {target_role}"
            )
        return

    if current is None:
        ctx.changed = True
        ctx.reason = f"postgresql role {role} will be granted {target_role}"
        yield PostgresqlMembershipCommand(
            member=role, role=target_role, admin_option=admin_option, **conn
        )
    elif admin_option and not current["admin_option"]:
        ctx.changed = True
        ctx.reason = (
            f"postgresql role {role} will gain admin option on {target_role}"
        )
        yield PostgresqlMembershipCommand(
            member=role, role=target_role, admin_option=True, **conn
        )
    elif not admin_option and current["admin_option"]:
        ctx.changed = True
        ctx.reason = (
            f"postgresql role {role} will lose admin option on {target_role}"
        )
        yield PostgresqlMembershipCommand(
            member=role, role=target_role, present=False, admin_only=True, **conn
        )
    else:
        ctx.reason = (
            f"postgresql role {role} is already a member of {target_role}"
        )


# The privileges each object type accepts; ALL expands to the whole row
# (PostgreSQL's own per-type privilege sets, the Ansible postgresql_privs
# vocabulary).
_PRIVILEGES_BY_TYPE = {
    "table": ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"),
    "sequence": ("USAGE", "SELECT", "UPDATE"),
    "schema": ("USAGE", "CREATE"),
    "function": ("EXECUTE",),
    "database": ("CREATE", "CONNECT", "TEMPORARY"),
}

# GRANT ON ALL <plural> IN SCHEMA wording per type.
_TYPE_PLURAL = {
    "table": "TABLES",
    "sequence": "SEQUENCES",
    "function": "FUNCTIONS",
    "schema": "SCHEMAS",
}

# GRANT ON TABLE also addresses views and foreign tables, so a "table"
# request matches those catalog kinds when reading current grants.
_PRIVILEGE_ALIASES = {
    "table": {"table", "view", "materialized_view", "foreign_table"},
    "sequence": {"sequence"},
    "schema": {"schema"},
    "function": {"function"},
    "database": {"database"},
}


def _normalize_object(object_type: str, name: str) -> tuple[str | None, str]:
    """Split an object name into (schema, name) the fact entries use.

    Databases and schemas are not schema-qualified (None schema); other
    kinds default to the ``public`` schema when unqualified -- the
    simulation's search_path convention, documented on the operation.
    """
    if object_type in ("database", "schema"):
        return None, name
    schema, _, bare = name.rpartition(".")
    return schema or "public", bare


@dataclass(frozen=True)
class PostgresqlPrivilegeCommand(_PsqlCommand):
    """``psql -Ac 'GRANT/REVOKE ... ON ... TO/FROM ...'``: object privileges.

    One present-flagged descriptor for both directions (the
    ``server.ModprobeCommand`` precedent). The target is either an
    explicit object list (``ON TABLE a, b``) or ``ALL <plural> IN SCHEMA
    s``; ``grant_option`` appends WITH GRANT OPTION on a grant and
    ``revoke_grant_option`` renders REVOKE GRANT OPTION FOR on a revoke.
    """

    privileges: tuple[str, ...]
    role: str
    object_type: str = "table"
    objects: tuple[str, ...] = ()
    all_in_schema: str | None = None
    present: bool = True
    grant_option: bool = False
    revoke_grant_option: bool = False

    def shell(self) -> str:
        privs = ", ".join(self.privileges)
        if self.all_in_schema:
            target = (
                f"ALL {_TYPE_PLURAL[self.object_type]} IN SCHEMA "
                f"{_quote_ident(self.all_in_schema)}"
            )
        else:
            names = ", ".join(_quote_qualified(o) for o in self.objects)
            target = f"{self.object_type.upper()} {names}"
        role = _quote_ident(self.role)
        if self.present:
            sql = f"GRANT {privs} ON {target} TO {role}"
            if self.grant_option:
                sql += " WITH GRANT OPTION"
            return self._sql_shell(sql)
        option_for = "GRANT OPTION FOR " if self.revoke_grant_option else ""
        return self._sql_shell(
            f"REVOKE {option_for}{privs} ON {target} FROM {role}"
        )


async def privilege(
    ctx: OpContext,
    privileges: str | list[str],
    role: str,
    object_type: str = "table",
    objects: list[str] | None = None,
    all_in_schema: str | None = None,
    present: bool = True,
    grant_option: bool = False,
    psql_user: str | None = None,
    psql_host: str | None = None,
    psql_port: int | None = None,
    psql_database: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Grant or revoke object privileges for a PostgreSQL role.

    Args:
        ctx (OpContext): Execution context for the operation.
        privileges (str | list[str]): Privilege names (SELECT, INSERT,
            USAGE, ...) or the string "ALL" for the type's full set.
        role (str): The grantee role (or PUBLIC).
        object_type (str, optional): One of table, schema, sequence,
            function, database. Defaults to "table".
        objects (list[str] | None, optional): Object names, schema-
            qualified or defaulting to the public schema (databases and
            schemas are never qualified). Mutually exclusive with
            all_in_schema. Defaults to None.
        all_in_schema (str | None, optional): Apply to ALL objects of the
            type IN SCHEMA this schema (not valid for databases).
            Defaults to None.
        present (bool, optional): Grant when True, revoke when False.
            Defaults to True.
        grant_option (bool, optional): WITH GRANT OPTION on a grant; on
            a revoke, withdraw only the grant option (REVOKE GRANT
            OPTION FOR), keeping the privilege. Defaults to False.
        psql_user (str | None, optional): Role to connect as.
            Defaults to None.
        psql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        psql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        psql_database (str | None, optional): Database whose objects are
            addressed. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated PostgreSQL GRANT/REVOKE commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/postgresql/postgresql_privilege.py``.
    """

    # reemote5-original (pyinfra has no privilege operation -- the most
    # urgent gap in its PostgreSQL support), the Ansible postgresql_privs
    # parameter shape. Idempotent against PostgresqlPrivileges: only
    # privileges the grantee lacks are granted, only ones it holds are
    # revoked, and a grant_option upgrade re-grants the full requested
    # set WITH GRANT OPTION. Grants held via PUBLIC or via a group role
    # are NOT counted (the fact reports direct grants only) -- the
    # operation manages the named role's own ACL entries. Unqualified
    # table/sequence/function names are treated as ``public`` (the
    # simulation's search_path convention); qualify names to address
    # other schemas. ALL-in-schema with no matching objects is a no-op.
    # Needs the object owner or a superuser: flag the request
    # ``sudo=True`` with ``privilege.sudo_user = "postgres"``.
    # ``**kwargs`` absorbs pyinfra's global arguments.
    if object_type not in _PRIVILEGES_BY_TYPE:
        raise ValueError(
            f"unknown postgresql object_type {object_type!r}; expected one "
            f"of {', '.join(sorted(_PRIVILEGES_BY_TYPE))}"
        )
    if bool(objects) == bool(all_in_schema):
        raise ValueError(
            "postgresql.privilege requires exactly one of objects or "
            "all_in_schema"
        )
    if all_in_schema and object_type == "database":
        raise ValueError("databases cannot be addressed ALL IN SCHEMA")
    valid = _PRIVILEGES_BY_TYPE[object_type]
    if isinstance(privileges, str):
        requested = (
            list(valid) if privileges.upper() == "ALL" else [privileges.upper()]
        )
    else:
        requested = [p.upper() for p in privileges]
    unknown = sorted(set(requested) - set(valid))
    if unknown:
        raise ValueError(
            f"privileges {', '.join(unknown)} are not valid on a "
            f"{object_type} (valid: {', '.join(valid)})"
        )
    desired = sorted(set(requested))

    conn = {
        "psql_user": psql_user,
        "psql_host": psql_host,
        "psql_port": psql_port,
        "psql_database": psql_database,
    }
    entries = await ctx.facts.get(PostgresqlPrivileges, **conn)
    aliases = _PRIVILEGE_ALIASES[object_type]
    mine = [e for e in entries if e["grantee"] == role and e["object_type"] in aliases]
    current = {(e["schema"], e["name"], e["privilege"]): e["grantable"] for e in mine}

    if all_in_schema:
        targets = sorted(
            {(e["schema"], e["name"]) for e in mine if e["schema"] == all_in_schema}
        )
        if not targets:
            ctx.reason = (
                f"no {object_type}s in schema {all_in_schema} to manage"
            )
            return
        target_args: dict[str, Any] = {"all_in_schema": all_in_schema}
    else:
        targets = sorted(
            {_normalize_object(object_type, name) for name in objects or []}
        )
        target_args = {
            "objects": tuple(
                name if schema is None else f"{schema}.{name}"
                for schema, name in targets
            )
        }

    if present:
        missing = sorted(
            {
                privilege
                for schema, name in targets
                for privilege in desired
                if (schema, name, privilege) not in current
            }
        )
        need_option = grant_option and any(
            not current.get((schema, name, privilege), False)
            for schema, name in targets
            for privilege in desired
        )
        if missing or need_option:
            grant_privs = desired if need_option else missing
            ctx.changed = True
            ctx.reason = (
                f"postgresql will grant {', '.join(grant_privs)} to {role}"
            )
            yield PostgresqlPrivilegeCommand(
                privileges=tuple(grant_privs),
                role=role,
                object_type=object_type,
                grant_option=grant_option,
                **target_args,
                **conn,
            )
        else:
            ctx.reason = f"postgresql role {role} already has the privileges"
        return

    if grant_option:
        removable = sorted(
            {
                privilege
                for schema, name in targets
                for privilege in desired
                if current.get((schema, name, privilege), False)
            }
        )
    else:
        removable = sorted(
            {
                privilege
                for schema, name in targets
                for privilege in desired
                if (schema, name, privilege) in current
            }
        )
    if removable:
        ctx.changed = True
        what = "grant option for " if grant_option else ""
        ctx.reason = (
            f"postgresql will revoke {what}{', '.join(removable)} from {role}"
        )
        yield PostgresqlPrivilegeCommand(
            privileges=tuple(removable),
            role=role,
            object_type=object_type,
            present=False,
            revoke_grant_option=grant_option,
            **target_args,
            **conn,
        )
    else:
        ctx.reason = f"postgresql role {role} has none of the privileges"


@dataclass(frozen=True)
class PostgresqlPublicationCommand(_PsqlCommand):
    """``psql -Ac 'CREATE/ALTER/DROP PUBLICATION ...'``: logical replication.

    The create form renders CREATE PUBLICATION ... FOR TABLE / FOR ALL
    TABLES; ``alter=True`` renders ALTER PUBLICATION SET TABLE and/or
    OWNER TO clauses; ``present=False`` renders DROP PUBLICATION.
    """

    publication: str
    tables: tuple[str, ...] = ()
    all_tables: bool = False
    owner: str | None = None
    present: bool = True
    alter: bool = False

    def shell(self) -> str:
        name = _quote_ident(self.publication)
        if not self.present:
            return self._sql_shell(f"DROP PUBLICATION {name}")
        if self.alter:
            statements = []
            if self.tables:
                listed = ", ".join(_quote_qualified(t) for t in self.tables)
                statements.append(f"ALTER PUBLICATION {name} SET TABLE {listed};")
            if self.owner is not None:
                statements.append(
                    f"ALTER PUBLICATION {name} OWNER TO {_quote_ident(self.owner)};"
                )
            return self._sql_shell(" ".join(statements))
        sql = f"CREATE PUBLICATION {name}"
        if self.all_tables:
            sql += " FOR ALL TABLES"
        elif self.tables:
            listed = ", ".join(_quote_qualified(t) for t in self.tables)
            sql += f" FOR TABLE {listed}"
        return self._sql_shell(sql)


def _qualified_table(name: str) -> str:
    """Default an unqualified table name to the public schema.

    The fact reports schema-qualified names, so the operation compares
    on that footing (the same search_path convention as
    ``postgresql.privilege``).
    """
    return name if "." in name else f"public.{name}"


async def publication(
    ctx: OpContext,
    publication: str,
    tables: list[str] | None = None,
    all_tables: bool = False,
    owner: str | None = None,
    present: bool = True,
    psql_user: str | None = None,
    psql_host: str | None = None,
    psql_port: int | None = None,
    psql_database: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Create, alter or drop a logical-replication publication.

    Args:
        ctx (OpContext): Execution context for the operation.
        publication (str): Name of the publication.
        tables (list[str] | None, optional): Tables to publish, schema-
            qualified or defaulting to public; reconciled (SET TABLE)
            on an existing publication. Defaults to None.
        all_tables (bool, optional): Publish ALL TABLES instead of a
            table list; immutable after creation. Defaults to False.
        owner (str | None, optional): The role that should own the
            publication; altered when it differs. Defaults to None.
        present (bool, optional): Whether the publication should exist.
            Defaults to True.
        psql_user (str | None, optional): Role to connect as.
            Defaults to None.
        psql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        psql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        psql_database (str | None, optional): Database to publish from.
            Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated PostgreSQL publication commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, OperationError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/postgresql/postgresql_publication.py``.
    """

    # reemote5-original (pyinfra has no publication operation).
    # Idempotent against PostgresqlPublications: absent -> CREATE
    # PUBLICATION (plus an OWNER TO alter when an owner is named, since
    # CREATE PUBLICATION has no OWNER clause); table-set drift -> ALTER
    # PUBLICATION SET TABLE; owner drift -> ALTER PUBLICATION OWNER TO;
    # unwanted -> DROP PUBLICATION. Switching an existing publication
    # between FOR TABLE and FOR ALL TABLES is impossible in PostgreSQL,
    # so a mismatch raises OperationError (fail-fast in prepare).
    # Requires wal_level=logical (see postgresql.setting) and
    # superuser/CREATE: flag the request ``sudo=True`` with
    # ``privilege.sudo_user = "postgres"``. ``**kwargs`` absorbs
    # pyinfra's global arguments.
    if present and not all_tables and not tables:
        raise ValueError(
            "postgresql.publication requires tables or all_tables when "
            "present=True"
        )
    conn = {
        "psql_user": psql_user,
        "psql_host": psql_host,
        "psql_port": psql_port,
        "psql_database": psql_database,
    }
    publications = await ctx.facts.get(PostgresqlPublications, **conn)
    current = next((p for p in publications if p["name"] == publication), None)

    if not present:
        if current is not None:
            ctx.changed = True
            ctx.reason = f"postgresql publication {publication} will be dropped"
            yield PostgresqlPublicationCommand(
                publication=publication, present=False, **conn
            )
        else:
            ctx.reason = f"postgresql publication {publication} does not exist"
        return

    desired_tables = tuple(sorted({_qualified_table(t) for t in tables or []}))
    if current is None:
        ctx.changed = True
        yield PostgresqlPublicationCommand(
            publication=publication,
            tables=desired_tables,
            all_tables=all_tables,
            **conn,
        )
        if owner:
            yield PostgresqlPublicationCommand(
                publication=publication, owner=owner, alter=True, **conn
            )
        ctx.reason = f"postgresql publication {publication} will be created"
        if owner:
            ctx.reason += f"; owner will become {owner}"
        return

    if current["all_tables"] != all_tables:
        raise OperationError(
            f"postgresql publication {publication} cannot switch between "
            "FOR TABLE and FOR ALL TABLES; drop it first"
        )
    reasons: list[str] = []
    changed_tables = (
        desired_tables
        if not all_tables and tables is not None
        and desired_tables != tuple(sorted(current["tables"]))
        else ()
    )
    changed_owner = owner if owner and owner != current["owner"] else None
    if changed_tables or changed_owner:
        ctx.changed = True
        yield PostgresqlPublicationCommand(
            publication=publication,
            tables=changed_tables,
            owner=changed_owner,
            alter=True,
            **conn,
        )
        if changed_tables:
            reasons.append("tables will be updated")
        if changed_owner:
            reasons.append(f"owner will become {changed_owner}")
        ctx.reason = (
            f"postgresql publication {publication}: {'; '.join(reasons)}"
        )
    else:
        ctx.reason = (
            f"postgresql publication {publication} already exists "
            "with the same parameters"
        )


@dataclass(frozen=True)
class PostgresqlRoleCommand(_PsqlCommand):
    """``psql -Ac 'CREATE/ALTER/DROP ROLE ...'``: manage one role.

    One present/alter-flagged descriptor for all three directions. Each
    attribute field is tri-state: None means "not part of this command",
    so the create form carries every flag (rendered as X/NOX pairs) and
    the alter form carries only the differing ones. ``present=False``
    renders DROP ROLE.
    """

    role: str
    present: bool = True
    alter: bool = False
    login: bool | None = None
    superuser: bool | None = None
    inherit: bool | None = None
    createdb: bool | None = None
    createrole: bool | None = None
    replication: bool | None = None
    connection_limit: int | None = None

    def _clauses(self) -> list[str]:
        clauses = []
        for keyword, value in (
            ("LOGIN", self.login),
            ("SUPERUSER", self.superuser),
            ("INHERIT", self.inherit),
            ("CREATEDB", self.createdb),
            ("CREATEROLE", self.createrole),
            ("REPLICATION", self.replication),
        ):
            if value is not None:
                clauses.append(keyword if value else f"NO{keyword}")
        if self.connection_limit is not None:
            clauses.append(f"CONNECTION LIMIT {self.connection_limit}")
        return clauses

    def shell(self) -> str:
        name = _quote_ident(self.role)
        if not self.present:
            return self._sql_shell(f"DROP ROLE {name}")
        verb = "ALTER ROLE" if self.alter else "CREATE ROLE"
        bits = [f"{verb} {name}", *self._clauses()]
        return self._sql_shell(" ".join(bits))


async def role(
    ctx: OpContext,
    role: str,
    present: bool = True,
    login: bool = True,
    superuser: bool = False,
    inherit: bool = True,
    createdb: bool = False,
    createrole: bool = False,
    replication: bool = False,
    connection_limit: int | None = None,
    psql_user: str | None = None,
    psql_host: str | None = None,
    psql_port: int | None = None,
    psql_database: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add, alter or remove a PostgreSQL role.

    Args:
        ctx (OpContext): Execution context for the operation.
        role (str): Name of the role to manage.
        present (bool, optional): Whether the role should exist.
            Defaults to True.
        login (bool, optional): Whether the role may log in; reconciled
            on an existing role. Defaults to True.
        superuser (bool, optional): Whether the role is a superuser;
            reconciled on an existing role. Defaults to False.
        inherit (bool, optional): Whether the role inherits granted
            roles' privileges; reconciled on an existing role. Defaults
            to True (PostgreSQL's own default).
        createdb (bool, optional): Whether the role may create
            databases; reconciled on an existing role. Defaults to False.
        createrole (bool, optional): Whether the role may create roles;
            reconciled on an existing role. Defaults to False.
        replication (bool, optional): Whether the role may initiate
            replication; reconciled on an existing role. Defaults to
            False.
        connection_limit (int | None, optional): Connection limit (-1
            for unlimited); altered when it differs, left alone when
            unset. Defaults to None.
        psql_user (str | None, optional): Role to connect as.
            Defaults to None.
        psql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        psql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        psql_database (str | None, optional): Database to connect to for
            issuing the command. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated PostgreSQL role commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/postgresql/postgresql_role.py``.
    """

    # pyinfra ``postgresql.role`` parity, reworked: upstream's alter
    # branch compares option names ("login", "superuser",
    # "connection_limit") against fact keys that never exist (the fact
    # reports "canlogin", "super", "connlimit"), so it never actually
    # alters anything; reemote5 maps the options onto the real catalog
    # keys and alters exactly the differing attributes (the requested
    # role-enhancement). Two further deliberate deviations: every flag
    # is rendered explicitly on CREATE (X/NOX pairs, so the created role
    # matches the declared state rather than relying on server defaults
    # -- pyinfra omits false flags), and ``inherit`` defaults to True,
    # PostgreSQL's own default, instead of pyinfra's False.
    # pyinfra's ``password`` option is deliberately dropped (the
    # namespace credentials decision): the server cannot reveal a
    # stored password, so a password option could never be idempotent
    # anyway; provision credentials via peer auth/.pgpass or a
    # out-of-band ALTER ROLE ... PASSWORD through postgresql.sql.
    # Needs CREATEROLE or superuser: flag the request ``sudo=True`` with
    # ``privilege.sudo_user = "postgres"``. ``**kwargs`` absorbs
    # pyinfra's global arguments.
    conn = {
        "psql_user": psql_user,
        "psql_host": psql_host,
        "psql_port": psql_port,
        "psql_database": psql_database,
    }
    roles = await ctx.facts.get(PostgresqlRoles, **conn)

    if not present:
        if role in roles:
            ctx.changed = True
            ctx.reason = f"postgresql role {role} will be dropped"
            yield PostgresqlRoleCommand(role=role, present=False, **conn)
        else:
            ctx.reason = f"postgresql role {role} does not exist"
        return

    desired = {
        "login": login,
        "superuser": superuser,
        "inherit": inherit,
        "createdb": createdb,
        "createrole": createrole,
        "replication": replication,
        "connection_limit": connection_limit,
    }
    if role not in roles:
        ctx.changed = True
        ctx.reason = f"postgresql role {role} will be created"
        yield PostgresqlRoleCommand(role=role, **desired, **conn)
        return

    # The fact's catalog keys for each option (pyinfra's alter branch
    # compared the option names themselves -- the bug reworked here).
    fact_keys = {
        "login": "canlogin",
        "superuser": "super",
        "inherit": "inherit",
        "createdb": "createdb",
        "createrole": "createrole",
        "replication": "replication",
        "connection_limit": "connlimit",
    }
    current = roles[role]
    diffs = {
        option: value
        for option, value in desired.items()
        if value is not None and current.get(fact_keys[option]) != value
    }
    if diffs:
        ctx.changed = True
        ctx.reason = (
            f"postgresql role {role} will be altered "
            f"({', '.join(sorted(diffs))})"
        )
        yield PostgresqlRoleCommand(role=role, alter=True, **diffs, **conn)
    else:
        ctx.reason = (
            f"postgresql role {role} exists and does not need updates"
        )


@dataclass(frozen=True)
class PostgresqlSchemaCommand(_PsqlCommand):
    """``psql -Ac 'CREATE/ALTER/DROP SCHEMA ...'``: manage one schema.

    ``present=False`` renders DROP SCHEMA (CASCADE when asked);
    ``alter=True`` renders ALTER SCHEMA OWNER TO; the create form
    renders CREATE SCHEMA with an optional AUTHORIZATION clause.
    """

    schema: str
    present: bool = True
    owner: str | None = None
    alter: bool = False
    cascade: bool = False

    def shell(self) -> str:
        name = _quote_ident(self.schema)
        if not self.present:
            sql = f"DROP SCHEMA {name}"
            if self.cascade:
                sql += " CASCADE"
            return self._sql_shell(sql)
        if self.alter:
            return self._sql_shell(
                f"ALTER SCHEMA {name} OWNER TO {_quote_ident(self.owner or '')}"
            )
        sql = f"CREATE SCHEMA {name}"
        if self.owner:
            sql += f" AUTHORIZATION {_quote_ident(self.owner)}"
        return self._sql_shell(sql)


async def schema(
    ctx: OpContext,
    schema: str,
    owner: str | None = None,
    present: bool = True,
    cascade: bool = False,
    psql_user: str | None = None,
    psql_host: str | None = None,
    psql_port: int | None = None,
    psql_database: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Create, re-own or drop a schema in a PostgreSQL database.

    Args:
        ctx (OpContext): Execution context for the operation.
        schema (str): Name of the schema to manage.
        owner (str | None, optional): The role that should own the
            schema; altered on an existing schema when it differs.
            Defaults to None.
        present (bool, optional): Whether the schema should exist.
            Defaults to True.
        cascade (bool, optional): DROP SCHEMA ... CASCADE on removal
            (drops every object in the schema). Defaults to False.
        psql_user (str | None, optional): Role to connect as.
            Defaults to None.
        psql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        psql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        psql_database (str | None, optional): Database to manage the
            schema in. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated PostgreSQL schema commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/postgresql/postgresql_schema.py``.
    """

    # reemote5-original (pyinfra has no schema operation), the Ansible
    # postgresql_schema shape. Idempotent against PostgresqlSchemas:
    # absent -> CREATE SCHEMA (AUTHORIZATION when an owner is named),
    # owner drift -> ALTER SCHEMA OWNER TO, unwanted -> DROP SCHEMA.
    # Needs CREATE on the database: flag the request ``sudo=True`` with
    # ``privilege.sudo_user = "postgres"``. ``**kwargs`` absorbs
    # pyinfra's global arguments.
    conn = {
        "psql_user": psql_user,
        "psql_host": psql_host,
        "psql_port": psql_port,
        "psql_database": psql_database,
    }
    schemas = await ctx.facts.get(PostgresqlSchemas, **conn)
    current = next((s for s in schemas if s["name"] == schema), None)

    if not present:
        if current is not None:
            ctx.changed = True
            ctx.reason = f"postgresql schema {schema} will be dropped"
            yield PostgresqlSchemaCommand(
                schema=schema, present=False, cascade=cascade, **conn
            )
        else:
            ctx.reason = f"postgresql schema {schema} does not exist"
        return

    if current is None:
        ctx.changed = True
        ctx.reason = f"postgresql schema {schema} will be created"
        yield PostgresqlSchemaCommand(schema=schema, owner=owner, **conn)
    elif owner and current["owner"] != owner:
        ctx.changed = True
        ctx.reason = f"postgresql schema {schema} owner will become {owner}"
        yield PostgresqlSchemaCommand(
            schema=schema, owner=owner, alter=True, **conn
        )
    else:
        ctx.reason = f"postgresql schema {schema} already exists"


@dataclass(frozen=True)
class PostgresqlSettingCommand(_PsqlCommand):
    """``psql -Ac 'ALTER SYSTEM SET/RESET ...'``: tune one GUC.

    ``present=True`` renders ALTER SYSTEM SET <name> = '<value>'
    (writing postgresql.auto.conf); ``present=False`` renders ALTER
    SYSTEM RESET <name>. The value is a quoted literal so unit-suffixed
    settings (``'2GB'``) pass through verbatim.
    """

    name: str
    value: str | None = None
    present: bool = True

    def shell(self) -> str:
        name = _quote_ident(self.name)
        if not self.present:
            return self._sql_shell(f"ALTER SYSTEM RESET {name}")
        return self._sql_shell(
            f"ALTER SYSTEM SET {name} = {_quote_literal(self.value or '')}"
        )


@dataclass(frozen=True)
class PostgresqlSqlCommand(_PsqlCommand):
    """``psql -Ac '<sql>'``: run one arbitrary SQL statement.

    The stateless mutation ``postgresql.sql`` yields, also reused by
    ``postgresql.setting`` for its optional ``SELECT pg_reload_conf()``
    follow-up.
    """

    sql: str

    def shell(self) -> str:
        return self._sql_shell(self.sql)


async def setting(
    ctx: OpContext,
    setting: str,
    value: str | None = None,
    present: bool = True,
    reload: bool = False,
    psql_user: str | None = None,
    psql_host: str | None = None,
    psql_port: int | None = None,
    psql_database: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Set or reset a PostgreSQL configuration parameter via ALTER SYSTEM.

    Args:
        ctx (OpContext): Execution context for the operation.
        setting (str): The configuration parameter name, e.g.
            ``max_connections``.
        value (str | None, optional): The desired value as text;
            required when present is True. Defaults to None.
        present (bool, optional): Set the value when True; RESET the
            parameter to its default when False. Defaults to True.
        reload (bool, optional): Follow a change with
            ``SELECT pg_reload_conf()`` so reloadable parameters take
            effect without a restart (restart-required parameters still
            need a server restart). Defaults to False.
        psql_user (str | None, optional): Role to connect as.
            Defaults to None.
        psql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        psql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        psql_database (str | None, optional): Database to connect to for
            issuing the command. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated PostgreSQL configuration commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/postgresql/postgresql_setting.py``.
    """

    # reemote5-original (pyinfra has no configuration operation), the
    # Ansible postgresql_set capability via ALTER SYSTEM only -- the
    # config-file-rewrite apply_method is deliberately dropped (ALTER
    # SYSTEM writes postgresql.auto.conf, which overrides the main
    # file, so a second write path would only create drift). The
    # parameter is named ``setting``, not Ansible's ``name``: ``name``
    # is op()'s display-name meta-parameter and never reaches the
    # operation. Idempotent
    # against PostgresqlSettings: the value is compared as TEXT against
    # pg_settings.setting in the parameter's own unit (no unit
    # normalization -- pass "262144" rather than "2GB" for
    # shared_buffers in 8kB blocks, or accept a re-issued ALTER SYSTEM
    # each run). The name is matched case-sensitively against
    # pg_settings.name (always lower-case): pass "max_connections", not
    # "Max_Connections". An unknown parameter name raises ValueError in
    # prepare (the fact lists every GUC the server knows). RESET is a
    # no-op when the parameter's source is already "default". Needs
    # superuser: flag the request ``sudo=True`` with
    # ``privilege.sudo_user = "postgres"``. ``**kwargs`` absorbs
    # pyinfra's global arguments.
    if present and value is None:
        raise ValueError("postgresql.setting requires value when present=True")
    conn = {
        "psql_user": psql_user,
        "psql_host": psql_host,
        "psql_port": psql_port,
        "psql_database": psql_database,
    }
    settings = await ctx.facts.get(PostgresqlSettings, **conn)
    current = next((s for s in settings if s["name"] == setting), None)
    if current is None:
        raise ValueError(f"unknown postgresql configuration parameter {setting}")

    if present:
        if current["setting"] == value:
            ctx.reason = f"postgresql setting {setting} is already {value}"
            return
        ctx.changed = True
        ctx.reason = f"postgresql setting {setting} will be set to {value}"
        yield PostgresqlSettingCommand(name=setting, value=value, **conn)
    else:
        if current["source"] == "default":
            ctx.reason = f"postgresql setting {setting} is already the default"
            return
        ctx.changed = True
        ctx.reason = f"postgresql setting {setting} will be reset"
        yield PostgresqlSettingCommand(name=setting, present=False, **conn)
    if reload:
        yield PostgresqlSqlCommand(sql="SELECT pg_reload_conf()", **conn)


async def sql(
    ctx: OpContext,
    sql: str,
    psql_user: str | None = None,
    psql_host: str | None = None,
    psql_port: int | None = None,
    psql_database: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Execute arbitrary SQL against PostgreSQL.

    Args:
        ctx (OpContext): Execution context for the operation.
        sql (str): SQL command(s) to execute.
        psql_user (str | None, optional): Role to connect as.
            Defaults to None.
        psql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        psql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        psql_database (str | None, optional): Database to run against.
            Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The SQL command, always (the operation is stateless).

    **Raises:**

    ```python
    None
    ```

    Examples:
        Examples can be found in ``examples/modules/postgresql/postgresql_sql.py``.
    """

    # pyinfra ``postgresql.sql`` parity: non-idempotent, always yields
    # (pyinfra marks it is_idempotent=False) -- the escape hatch for
    # anything the typed operations do not cover. The SQL text appears
    # verbatim in the plan/apply log, so never embed credentials in it
    # (``psql_password`` is deliberately dropped namespace-wide).
    # ``**kwargs`` absorbs pyinfra's global arguments.
    ctx.changed = True
    ctx.reason = "postgresql SQL will be executed"
    yield PostgresqlSqlCommand(
        sql=sql,
        psql_user=psql_user,
        psql_host=psql_host,
        psql_port=psql_port,
        psql_database=psql_database,
    )


@dataclass(frozen=True)
class PostgresqlSubscriptionCommand(_PsqlCommand):
    """``psql -Ac 'CREATE/ALTER/DROP SUBSCRIPTION ...'``: logical replication.

    The create form renders CREATE SUBSCRIPTION ... CONNECTION '...'
    PUBLICATION ... (WITH (enabled = false) when created disabled);
    ``alter=True`` renders ALTER SUBSCRIPTION SET PUBLICATION and/or
    ENABLE/DISABLE; ``present=False`` renders DROP SUBSCRIPTION.
    """

    subscription: str
    connection: str | None = None
    publications: tuple[str, ...] = ()
    enabled: bool | None = None
    present: bool = True
    alter: bool = False

    def shell(self) -> str:
        name = _quote_ident(self.subscription)
        if not self.present:
            return self._sql_shell(f"DROP SUBSCRIPTION {name}")
        if self.alter:
            statements = []
            if self.publications:
                listed = ", ".join(_quote_ident(p) for p in self.publications)
                statements.append(
                    f"ALTER SUBSCRIPTION {name} SET PUBLICATION {listed};"
                )
            if self.enabled is not None:
                verb = "ENABLE" if self.enabled else "DISABLE"
                statements.append(f"ALTER SUBSCRIPTION {name} {verb};")
            return self._sql_shell(" ".join(statements))
        listed = ", ".join(_quote_ident(p) for p in self.publications)
        sql = (
            f"CREATE SUBSCRIPTION {name} "
            f"CONNECTION {_quote_literal(self.connection or '')} "
            f"PUBLICATION {listed}"
        )
        if self.enabled is False:
            sql += " WITH (enabled = false)"
        return self._sql_shell(sql)


async def subscription(
    ctx: OpContext,
    subscription: str,
    connection: str | None = None,
    publications: list[str] | None = None,
    present: bool = True,
    enabled: bool = True,
    psql_user: str | None = None,
    psql_host: str | None = None,
    psql_port: int | None = None,
    psql_database: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Create, alter or drop a logical-replication subscription.

    Args:
        ctx (OpContext): Execution context for the operation.
        subscription (str): Name of the subscription.
        connection (str | None, optional): libpq connection string to
            the publisher; required when creating. Never embed a
            password -- reference a host-side passfile or service
            definition instead. Not compared on an existing
            subscription (the fact deliberately hides conninfo).
            Defaults to None.
        publications (list[str] | None, optional): Publication names on
            the publisher; required when creating, reconciled (SET
            PUBLICATION) on an existing subscription. Defaults to None.
        present (bool, optional): Whether the subscription should exist.
            Defaults to True.
        enabled (bool, optional): Whether the subscription should be
            enabled; reconciled (ENABLE/DISABLE) when it differs.
            Defaults to True.
        psql_user (str | None, optional): Role to connect as.
            Defaults to None.
        psql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        psql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        psql_database (str | None, optional): Database to subscribe in.
            Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated PostgreSQL subscription commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/postgresql/postgresql_subscription.py``.
    """

    # reemote5-original (pyinfra has no subscription operation).
    # Idempotent against PostgresqlSubscriptions: absent -> CREATE
    # SUBSCRIPTION; publication-set drift -> ALTER SUBSCRIPTION SET
    # PUBLICATION; enabled drift -> ENABLE/DISABLE; unwanted -> DROP
    # SUBSCRIPTION (which fails server-side while the replication slot
    # is still active -- disable first, the enabled=False reconcile
    # does that). Connection-string drift is NOT detected: the fact
    # never reads subconninfo because it can embed a password (the
    # namespace credentials decision). Publication names may not
    # contain commas or braces (the fact parses the text[] rendering).
    # Needs superuser: flag the request ``sudo=True`` with
    # ``privilege.sudo_user = "postgres"``. ``**kwargs`` absorbs
    # pyinfra's global arguments.
    if present and (not connection or not publications):
        raise ValueError(
            "postgresql.subscription requires connection and publications "
            "when present=True"
        )
    for publication_name in publications or []:
        if any(char in publication_name for char in ",{}"):
            raise ValueError(
                f"publication name {publication_name!r} may not contain "
                "commas or braces"
            )
    conn = {
        "psql_user": psql_user,
        "psql_host": psql_host,
        "psql_port": psql_port,
        "psql_database": psql_database,
    }
    subscriptions = await ctx.facts.get(PostgresqlSubscriptions, **conn)
    current = next((s for s in subscriptions if s["name"] == subscription), None)

    if not present:
        if current is not None:
            ctx.changed = True
            ctx.reason = (
                f"postgresql subscription {subscription} will be dropped"
            )
            yield PostgresqlSubscriptionCommand(
                subscription=subscription, present=False, **conn
            )
        else:
            ctx.reason = (
                f"postgresql subscription {subscription} does not exist"
            )
        return

    desired_pubs = tuple(sorted(set(publications or [])))
    if current is None:
        ctx.changed = True
        ctx.reason = f"postgresql subscription {subscription} will be created"
        yield PostgresqlSubscriptionCommand(
            subscription=subscription,
            connection=connection,
            publications=desired_pubs,
            enabled=enabled,
            **conn,
        )
        return

    changed_pubs = (
        desired_pubs
        if desired_pubs != tuple(sorted(current["publications"]))
        else ()
    )
    changed_enabled = enabled if enabled != current["enabled"] else None
    if changed_pubs or changed_enabled is not None:
        ctx.changed = True
        reasons = []
        if changed_pubs:
            reasons.append("publications will be updated")
        if changed_enabled is not None:
            reasons.append("enabled" if changed_enabled else "disabled")
        ctx.reason = (
            f"postgresql subscription {subscription} will be altered "
            f"({', '.join(reasons)})"
        )
        yield PostgresqlSubscriptionCommand(
            subscription=subscription,
            publications=changed_pubs,
            enabled=changed_enabled,
            alter=True,
            **conn,
        )
    else:
        ctx.reason = (
            f"postgresql subscription {subscription} already exists "
            "with the same parameters"
        )


@dataclass(frozen=True)
class PostgresqlTablespaceCommand(_PsqlCommand):
    """``psql -Ac 'CREATE/ALTER/DROP TABLESPACE ...'``: manage one tablespace.

    ``present=False`` renders DROP TABLESPACE; ``alter=True`` renders
    ALTER TABLESPACE OWNER TO; the create form renders CREATE TABLESPACE
    with the optional OWNER clause and the required LOCATION literal.
    """

    tablespace: str
    location: str | None = None
    owner: str | None = None
    present: bool = True
    alter: bool = False

    def shell(self) -> str:
        name = _quote_ident(self.tablespace)
        if not self.present:
            return self._sql_shell(f"DROP TABLESPACE {name}")
        if self.alter:
            return self._sql_shell(
                f"ALTER TABLESPACE {name} OWNER TO {_quote_ident(self.owner or '')}"
            )
        bits = [f"CREATE TABLESPACE {name}"]
        if self.owner:
            bits.append(f"OWNER {_quote_ident(self.owner)}")
        bits.append(f"LOCATION {_quote_literal(self.location or '')}")
        return self._sql_shell(" ".join(bits))


async def tablespace(
    ctx: OpContext,
    tablespace: str,
    location: str | None = None,
    owner: str | None = None,
    present: bool = True,
    psql_user: str | None = None,
    psql_host: str | None = None,
    psql_port: int | None = None,
    psql_database: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Create, re-own or drop a PostgreSQL tablespace.

    Args:
        ctx (OpContext): Execution context for the operation.
        tablespace (str): Name of the tablespace to manage.
        location (str | None, optional): Absolute filesystem directory
            for the tablespace (must exist, be empty and be owned by the
            server user -- prepare it out of band); required when
            creating, immutable afterwards. Defaults to None.
        owner (str | None, optional): The role that should own the
            tablespace; altered when it differs. Defaults to None.
        present (bool, optional): Whether the tablespace should exist.
            Defaults to True.
        psql_user (str | None, optional): Role to connect as.
            Defaults to None.
        psql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        psql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        psql_database (str | None, optional): Database to connect to for
            issuing the command. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated PostgreSQL tablespace commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, OperationError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/postgresql/postgresql_tablespace.py``.
    """

    # reemote5-original (pyinfra has no tablespace operation), the
    # Ansible postgresql_tablespace shape. Idempotent against
    # PostgresqlTablespaces: absent -> CREATE TABLESPACE ... LOCATION;
    # owner drift -> ALTER TABLESPACE OWNER TO; unwanted -> DROP
    # TABLESPACE (which fails server-side while any object still lives
    # there). PostgreSQL cannot move a tablespace's location, so a
    # differing location on an existing tablespace raises
    # OperationError (fail-fast in prepare) rather than pretending to
    # reconcile it. Needs superuser: flag the request ``sudo=True``
    # with ``privilege.sudo_user = "postgres"``. ``**kwargs`` absorbs
    # pyinfra's global arguments.
    conn = {
        "psql_user": psql_user,
        "psql_host": psql_host,
        "psql_port": psql_port,
        "psql_database": psql_database,
    }
    tablespaces = await ctx.facts.get(PostgresqlTablespaces, **conn)
    current = next((t for t in tablespaces if t["name"] == tablespace), None)

    if not present:
        if current is not None:
            ctx.changed = True
            ctx.reason = f"postgresql tablespace {tablespace} will be dropped"
            yield PostgresqlTablespaceCommand(
                tablespace=tablespace, present=False, **conn
            )
        else:
            ctx.reason = f"postgresql tablespace {tablespace} does not exist"
        return

    if current is None:
        if not location:
            raise ValueError(
                "postgresql.tablespace requires location when creating"
            )
        ctx.changed = True
        ctx.reason = f"postgresql tablespace {tablespace} will be created"
        yield PostgresqlTablespaceCommand(
            tablespace=tablespace, location=location, owner=owner, **conn
        )
        return

    if location and current["location"] != location:
        raise OperationError(
            f"postgresql tablespace {tablespace} cannot change location "
            f"({current['location']} -> {location}); drop it first"
        )
    if owner and current["owner"] != owner:
        ctx.changed = True
        ctx.reason = (
            f"postgresql tablespace {tablespace} owner will become {owner}"
        )
        yield PostgresqlTablespaceCommand(
            tablespace=tablespace, owner=owner, alter=True, **conn
        )
    else:
        ctx.reason = f"postgresql tablespace {tablespace} already exists"


__all__ = [
    "PostgresqlConnection",
    "PostgresqlConnections",
    "PostgresqlDatabase",
    "PostgresqlDatabaseCommand",
    "PostgresqlDatabases",
    "PostgresqlDumpCommand",
    "PostgresqlExtension",
    "PostgresqlExtensionCommand",
    "PostgresqlExtensions",
    "PostgresqlIndex",
    "PostgresqlIndexCommand",
    "PostgresqlIndexes",
    "PostgresqlLoadCommand",
    "PostgresqlMembershipCommand",
    "PostgresqlPrivilege",
    "PostgresqlPrivilegeCommand",
    "PostgresqlPrivileges",
    "PostgresqlPublication",
    "PostgresqlPublicationCommand",
    "PostgresqlPublications",
    "PostgresqlReplication",
    "PostgresqlReplicationClient",
    "PostgresqlRole",
    "PostgresqlRoleCommand",
    "PostgresqlRoleMembership",
    "PostgresqlRoleMemberships",
    "PostgresqlRoles",
    "PostgresqlSchema",
    "PostgresqlSchemaCommand",
    "PostgresqlSchemas",
    "PostgresqlSetting",
    "PostgresqlSettingCommand",
    "PostgresqlSettings",
    "PostgresqlSqlCommand",
    "PostgresqlSubscription",
    "PostgresqlSubscriptionCommand",
    "PostgresqlSubscriptions",
    "PostgresqlTable",
    "PostgresqlTables",
    "PostgresqlTablespace",
    "PostgresqlTablespaceCommand",
    "PostgresqlTablespaces",
    "database",
    "dump",
    "extension",
    "index",
    "load",
    "membership",
    "privilege",
    "publication",
    "role",
    "schema",
    "setting",
    "sql",
    "subscription",
    "tablespace",
]