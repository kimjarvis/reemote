# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``mysql`` module: comprehensive MySQL management.

Fact side: ``MysqlDatabases`` (the server's databases and their default
character set/collation), ``MysqlUsers`` (the ``user@host`` accounts and
their ``mysql.user`` attributes), ``MysqlUserGrants`` (one account's
grants keyed by ``database.table``, including granted roles),
``MysqlBinaryLogs`` (the binary-log file list and sizes),
``MysqlColumns`` (one table's column definitions), ``MysqlConnections``
(the active client connections from the processlist, without the query
text), ``MysqlEngines`` (the storage engines and their capabilities),
``MysqlIndexes`` (a database's index definitions), ``MysqlPlugins``
(the installed plugins and their status), ``MysqlReplication`` (the
replica channels from ``SHOW REPLICA STATUS``), ``MysqlStatus`` (the
``SHOW GLOBAL STATUS`` runtime counters), ``MysqlTables`` (one
database's table metadata and sizes) and ``MysqlVariables`` (the
``SHOW GLOBAL VARIABLES`` configuration). ``MysqlDatabases``,
``MysqlUsers`` and ``MysqlUserGrants`` are ports of the ``mysql`` facts;
every other fact is reemote5-original and closes a visibility gap that
set leaves open.

Operation side: ``mysql.database`` and ``mysql.user`` manage databases
and accounts (both create/drop AND idempotently alter existing ones);
``mysql.privileges`` grants/revokes privileges; ``mysql.sql`` executes
arbitrary SQL; ``mysql.dump`` and ``mysql.load`` write/read host-side
``mysqldump`` files; ``mysql.variable`` tunes global system variables
via SET GLOBAL / SET PERSIST; ``mysql.table`` and ``mysql.index``
manage schema objects; ``mysql.role`` manages roles and their
memberships; ``mysql.plugin`` installs/uninstalls server plugins;
``mysql.kill`` terminates a connection or its running query;
``mysql.optimize`` runs table maintenance (OPTIMIZE/ANALYZE/CHECK/
REPAIR); and ``mysql.replica`` starts/stops/resets replication. All
operations talk to the server through the ``mysql`` CLI on the target
host, targeting MySQL 8.0.22+ syntax (``SHOW REPLICA STATUS``,
``START REPLICA``).

Credentials decision (developer-approved, the postgresql precedent):
The ``mysql_password`` connection parameter and ``user``'s
``password`` option are deliberately dropped. reemote5 renders raw
shell strings into the plan/apply log, so no operation accepts a
password. Authenticate with sudo + the ``unix_socket``/``auth_socket``
plugin (``sudo=True`` as root) or a host-side ``~/.my.cnf``; the
optional ``mysql_defaults_file`` connection argument renders
``--defaults-extra-file`` for a host-side option file holding the
credentials. ``mysql.user`` takes an ``auth_plugin`` option (e.g.
``auth_socket``) instead of a password.

Every fact and operation accepts the connection keyword arguments
``mysql_user``/``mysql_host``/``mysql_port``/``mysql_database``/
``mysql_defaults_file`` (the ``mysql_*`` module arguments minus
the password, plus the defaults-file escape hatch).
"""

import re
import shlex
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass
from typing import Any, TypedDict, cast

from ...commands import Command
from ...core import OpContext, OperationError
from ...hosts import Host


def _mysql_base(
    *,
    mysql_user: str | None = None,
    mysql_host: str | None = None,
    mysql_port: int | None = None,
    mysql_database: str | None = None,
    mysql_defaults_file: str | None = None,
    executable: str = "mysql",
) -> str:
    """Render the mysql invocation prefix (the mysql prefix builder).

    The operand order -- the positional database, then
    ``-u`` user, ``-h`` host, ``-P`` port -- with the ``-p`` password
    flag deliberately dropped (the credentials decision above).
    ``--defaults-extra-file`` must be the FIRST option on a mysql
    command line, so it renders before everything else. Flags are
    space-separated and every user-supplied operand is ``shlex``-quoted,
    a deliberate deviation from the attached ``-u<user>`` form;
    plain values quote to themselves, so command parity holds for
    ordinary names.
    """
    parts = [executable]
    if mysql_defaults_file:
        parts.append(f"--defaults-extra-file={shlex.quote(mysql_defaults_file)}")
    if mysql_database:
        parts.append(shlex.quote(mysql_database))
    if mysql_user:
        parts.extend(["-u", shlex.quote(mysql_user)])
    if mysql_host:
        parts.extend(["-h", shlex.quote(mysql_host)])
    if mysql_port:
        parts.extend(["-P", str(mysql_port)])
    return " ".join(parts)


def _mysql_sql(
    sql: str,
    *,
    mysql_user: str | None = None,
    mysql_host: str | None = None,
    mysql_port: int | None = None,
    mysql_database: str | None = None,
    mysql_defaults_file: str | None = None,
    ignore_errors: bool = False,
) -> str:
    """Render ``mysql ... -Be '<sql>'`` (the statement executor).

    ``-B`` is batch (tab-separated, header row) output and ``-e`` runs
    the statement(s) and exits -- the shape ``_parse_rows`` reads.
    ``ignore_errors`` appends ``|| true`` (the MysqlUserGrants
    behaviour: SHOW GRANTS errors when the user does not exist).
    """
    base = _mysql_base(
        mysql_user=mysql_user,
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_database=mysql_database,
        mysql_defaults_file=mysql_defaults_file,
    )
    shell = f"{base} -Be {shlex.quote(sql)}"
    if ignore_errors:
        shell += " || true"
    return shell


def _quote_ident(name: str) -> str:
    """Quote a SQL identifier in backticks, doubling embedded backticks.

    Raw interpolation of ``f'`{name}`'`` breaks on a name
    containing a backtick; reemote5 escapes it the way MySQL's own
    identifier quoting does.
    """
    return "`" + name.replace("`", "``") + "`"


def _quote_literal(value: str) -> str:
    """Quote a SQL string literal for MySQL's default sql_mode.

    Backslashes are doubled first (MySQL treats them as escapes inside
    literals), then single quotes are doubled.
    """
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def _try_int(value: str | None) -> int | str | None:
    """``try_int``: an integer when the text is numeric, else raw."""
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def _parse_rows(
    output: Iterable[str], lower_titles: bool = False
) -> list[dict[str, str | None]]:
    """Parse ``mysql -B`` output into header-keyed row dicts.

    The column/row parser over tab-separated
    output: the first line names the columns (optionally lowercased,
    as for information_schema.SCHEMATA), each following
    line is one row, and an empty field reads as None. Two hardening
    deviations: empty output parses to ``[]`` instead of raising on
    an unconditional header read, and the literal ``NULL`` batch
    marker also reads as None (real ``mysql -B`` prints NULL columns as
    ``NULL``; the fixtures only exercise the empty form).
    """
    lines = list(output)
    if not lines:
        return []
    titles = lines[0].split("\t")
    if lower_titles:
        titles = [title.lower() for title in titles]
    rows: list[dict[str, str | None]] = []
    for line in lines[1:]:
        bits = line.split("\t")
        rows.append(
            {
                titles[i]: (None if bit in ("", "NULL") else bit)
                for i, bit in enumerate(bits)
                if i < len(titles)
            }
        )
    return rows


class _MysqlFact:
    """Shared transport plumbing for the mysql-backed facts.

    Holds the ``command`` renderer (the fact's ``MYSQL`` statement
    wrapped in the mysql invocation) and the ``_run`` real-transport
    tail, so each fact body is just its seam read plus ``parse``.
    Parameterized facts override ``command`` to build their statement
    from the positional arguments. Never exported or rendered; every
    concrete fact keeps its own ``process`` classmethod choosing
    between the SimulatedHost seam and ``_run``.
    """

    MYSQL: str
    IGNORE_ERRORS = False

    @classmethod
    def command(
        cls,
        *,
        mysql_user: str | None = None,
        mysql_host: str | None = None,
        mysql_port: int | None = None,
        mysql_database: str | None = None,
        mysql_defaults_file: str | None = None,
    ) -> str:
        """Shell running this fact's statement through ``mysql -Be``."""
        return _mysql_sql(
            cls.MYSQL,
            mysql_user=mysql_user,
            mysql_host=mysql_host,
            mysql_port=mysql_port,
            mysql_database=mysql_database,
            mysql_defaults_file=mysql_defaults_file,
            ignore_errors=cls.IGNORE_ERRORS,
        )

    @classmethod
    async def _run(cls, host: Host, shell: str) -> Any:
        # Real transport: run the statement over asyncssh and parse stdout.
        stdout = await host.run_shell(shell)
        return cls.parse(stdout.splitlines())  # type: ignore[attr-defined]


# --- Facts -------------------------------------------------------------------


class MysqlBinaryLog(TypedDict):
    """
    Attributes:
        name: The binary-log file name, e.g. ``binlog.000001``.
        size: The file size in bytes.
        encrypted: Whether the file is encrypted (MySQL 8.0.14+
            reports the ``Encrypted`` column); None on older servers.
    """

    name: str
    size: int
    encrypted: bool | None


class MysqlBinaryLogs(_MysqlFact):
    """The server's binary-log files and their sizes.

    **Commands:**

    ```bash
    mysql -Be 'SHOW BINARY LOGS' || true
    ```

    **Returns:**

    ```python
    list[MysqlBinaryLog]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_mysql_binary_logs.py``.
    """

    # reemote5-original: there is no other binary-log visibility fact. The
    # statement errors (1381) when binary logging is disabled, so the
    # command carries a MysqlUserGrants-style ``|| true`` guard
    # and a disabled/absent binlog reads as []. The ``Encrypted`` column
    # only exists on MySQL 8.0.14+; older servers report None.

    MYSQL = "SHOW BINARY LOGS"
    IGNORE_ERRORS = True

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[MysqlBinaryLog]:
        """Parse SHOW BINARY LOGS rows into binary-log entries."""
        logs: list[MysqlBinaryLog] = []
        for row in _parse_rows(output):
            name = row.get("Log_name")
            if name is None:
                continue
            encrypted = row.get("Encrypted")
            logs.append(
                {
                    "name": name,
                    "size": int(row.get("File_size") or 0),
                    "encrypted": None if encrypted is None else encrypted == "Yes",
                }
            )
        return logs

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        mysql_user: str | None = None,
        mysql_host: str | None = None,
        mysql_port: int | None = None,
        mysql_database: str | None = None,
        mysql_defaults_file: str | None = None,
    ) -> list[MysqlBinaryLog]:
        logs = getattr(host, "mysql_binary_logs", None)
        if logs is not None:
            # SimulatedHost seam: the binary-log entries, copied per
            # entry so callers never mutate simulated state.
            return [cast(MysqlBinaryLog, dict(entry)) for entry in logs]
        return await cls._run(
            host,
            cls.command(
                mysql_user=mysql_user,
                mysql_host=mysql_host,
                mysql_port=mysql_port,
                mysql_database=mysql_database,
                mysql_defaults_file=mysql_defaults_file,
            ),
        )


class MysqlColumn(TypedDict):
    """
    Attributes:
        name: The column name.
        type: The full column type, e.g. ``varchar(255)`` or
            ``int unsigned`` (``COLUMN_TYPE``).
        nullable: Whether the column accepts NULL.
        default: The default value as text; None when the column has
            no default (or defaults to NULL).
        key: The index role: ``PRI``, ``UNI``, ``MUL`` or empty.
        extra: Extra attributes, e.g. ``auto_increment`` or
            ``DEFAULT_GENERATED``; empty when none.
        comment: The column comment; empty when none.
        position: The 1-based ordinal position in the table.
    """

    name: str
    type: str
    nullable: bool
    default: str | None
    key: str
    extra: str
    comment: str
    position: int


class MysqlColumns(_MysqlFact):
    """One table's column definitions, in ordinal order.

    **Commands:**

    ```bash
    mysql -Be "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA, COLUMN_COMMENT, ORDINAL_POSITION FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = '<database>' AND TABLE_NAME = '<table>' ORDER BY ORDINAL_POSITION"
    ```

    **Returns:**

    ```python
    list[MysqlColumn]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_mysql_columns.py``.
    """

    # reemote5-original: there is no other column-level fact. Reads
    # information_schema.COLUMNS (not SHOW COLUMNS, whose header set
    # varies) filtered to one table and ordered by ORDINAL_POSITION so
    # the list is deterministic. An unknown database/table reads as [].

    @classmethod
    def command(
        cls,
        database: str,
        table: str,
        *,
        mysql_user: str | None = None,
        mysql_host: str | None = None,
        mysql_port: int | None = None,
        mysql_database: str | None = None,
        mysql_defaults_file: str | None = None,
    ) -> str:
        """Shell selecting one table's columns from information_schema."""
        return _mysql_sql(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, "
            "COLUMN_KEY, EXTRA, COLUMN_COMMENT, ORDINAL_POSITION "
            "FROM information_schema.COLUMNS "
            f"WHERE TABLE_SCHEMA = {_quote_literal(database)} "
            f"AND TABLE_NAME = {_quote_literal(table)} "
            "ORDER BY ORDINAL_POSITION",
            mysql_user=mysql_user,
            mysql_host=mysql_host,
            mysql_port=mysql_port,
            mysql_database=mysql_database,
            mysql_defaults_file=mysql_defaults_file,
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[MysqlColumn]:
        """Parse information_schema.COLUMNS rows into column entries."""
        return [
            {
                "name": row["COLUMN_NAME"] or "",
                "type": row["COLUMN_TYPE"] or "",
                "nullable": row["IS_NULLABLE"] == "YES",
                "default": row["COLUMN_DEFAULT"],
                "key": row["COLUMN_KEY"] or "",
                "extra": row["EXTRA"] or "",
                "comment": row["COLUMN_COMMENT"] or "",
                "position": int(row["ORDINAL_POSITION"] or 0),
            }
            for row in _parse_rows(output)
        ]

    @classmethod
    async def process(
        cls,
        host: Host,
        database: str,
        table: str,
        *,
        mysql_user: str | None = None,
        mysql_host: str | None = None,
        mysql_port: int | None = None,
        mysql_database: str | None = None,
        mysql_defaults_file: str | None = None,
    ) -> list[MysqlColumn]:
        columns = getattr(host, "mysql_columns", None)
        if columns is not None:
            # SimulatedHost seam: "database.table" -> column entries,
            # copied per entry. The connection kwargs address one
            # simulated server per host, so they do not key the seam.
            return [
                cast(MysqlColumn, dict(entry))
                for entry in columns.get(f"{database}.{table}", [])
            ]
        return await cls._run(
            host,
            cls.command(
                database,
                table,
                mysql_user=mysql_user,
                mysql_host=mysql_host,
                mysql_port=mysql_port,
                mysql_database=mysql_database,
                mysql_defaults_file=mysql_defaults_file,
            ),
        )


class MysqlConnection(TypedDict):
    """
    Attributes:
        id: The connection (thread) ID, the operand for ``mysql.kill``.
        user: The authenticated user; None for background threads.
        host: The client's ``host:port``; None for background threads.
        database: The default database; None when none is selected.
        command: The thread command, e.g. ``Query``, ``Sleep``,
            ``Binlog Dump GTID``.
        time: Seconds the thread has been in its current state.
        state: The thread state text, e.g. ``Sending data``; None when
            the thread reports no state.
    """

    id: int
    user: str | None
    host: str | None
    database: str | None
    command: str | None
    time: int
    state: str | None


class MysqlConnections(_MysqlFact):
    """The server's client connections, without the query text.

    **Commands:**

    ```bash
    mysql -Be 'SELECT ID, USER, HOST, DB, COMMAND, TIME, STATE FROM information_schema.PROCESSLIST'
    ```

    **Returns:**

    ```python
    list[MysqlConnection]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_mysql_connections.py``.
    """

    # reemote5-original: there is no other connection-visibility fact.
    # Reads information_schema.PROCESSLIST (the SHOW FULL PROCESSLIST
    # columns) but deliberately omits the ``INFO`` column -- the SQL
    # text can carry literal credentials and personal data, so the fact
    # reports only who is connected, from where, and in what state (the
    # PostgresqlConnections precedent).

    MYSQL = (
        "SELECT ID, USER, HOST, DB, COMMAND, TIME, STATE "
        "FROM information_schema.PROCESSLIST"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[MysqlConnection]:
        """Parse processlist rows into connection entries."""
        return [
            {
                "id": int(row["ID"] or 0),
                "user": row["USER"],
                "host": row["HOST"],
                "database": row["DB"],
                "command": row["COMMAND"],
                "time": int(row["TIME"] or 0),
                "state": row["STATE"],
            }
            for row in _parse_rows(output)
        ]

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        mysql_user: str | None = None,
        mysql_host: str | None = None,
        mysql_port: int | None = None,
        mysql_database: str | None = None,
        mysql_defaults_file: str | None = None,
    ) -> list[MysqlConnection]:
        connections = getattr(host, "mysql_connections", None)
        if connections is not None:
            # SimulatedHost seam: the connection entries, copied per
            # entry so callers never mutate simulated state.
            return [cast(MysqlConnection, dict(entry)) for entry in connections]
        return await cls._run(
            host,
            cls.command(
                mysql_user=mysql_user,
                mysql_host=mysql_host,
                mysql_port=mysql_port,
                mysql_database=mysql_database,
                mysql_defaults_file=mysql_defaults_file,
            ),
        )


class MysqlDatabase(TypedDict):
    """
    Attributes:
        character_set: The database's default character set, e.g.
            ``utf8mb4``; None when the server reports none.
        collation_name: The database's default collation, e.g.
            ``utf8mb4_0900_ai_ci``; None when the server reports none.
    """

    character_set: str | None
    collation_name: str | None


class MysqlDatabases(_MysqlFact):
    """The server's databases, keyed by database name.

    **Commands:**

    ```bash
    mysql -Be 'SELECT * FROM information_schema.SCHEMATA'
    ```

    **Returns:**

    ```python
    dict[str, MysqlDatabase]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_mysql_databases.py``.
    """

    # ``mysql.MysqlDatabases``: SELECT * over
    # information_schema.SCHEMATA with lowercased column titles, keyed
    # by schema_name and reduced to the character_set/collation_name
    # pair. The name-keyed outer dict is kept (the device-keyed-mapping
    # exception to the entry-list guideline) with a MysqlDatabase entry
    # per database. There is no command-existence gate: a
    # missing mysql exits 127, reported as CommandNotFoundError.

    MYSQL = "SELECT * FROM information_schema.SCHEMATA"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, MysqlDatabase]:
        """Parse SCHEMATA rows into the name-keyed database mapping."""
        databases: dict[str, MysqlDatabase] = {}
        for row in _parse_rows(output, lower_titles=True):
            name = row.get("schema_name")
            if name is None:
                continue
            databases[name] = {
                "character_set": row.get("default_character_set_name"),
                "collation_name": row.get("default_collation_name"),
            }
        return databases

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        mysql_user: str | None = None,
        mysql_host: str | None = None,
        mysql_port: int | None = None,
        mysql_database: str | None = None,
        mysql_defaults_file: str | None = None,
    ) -> dict[str, MysqlDatabase]:
        databases = getattr(host, "mysql_databases", None)
        if databases is not None:
            # SimulatedHost seam: database name -> MysqlDatabase, copied
            # per entry. The connection kwargs address one simulated
            # server per host, so they do not key the seam.
            return {
                name: cast(MysqlDatabase, dict(entry))
                for name, entry in databases.items()
            }
        return await cls._run(
            host,
            cls.command(
                mysql_user=mysql_user,
                mysql_host=mysql_host,
                mysql_port=mysql_port,
                mysql_database=mysql_database,
                mysql_defaults_file=mysql_defaults_file,
            ),
        )


class MysqlEngine(TypedDict):
    """
    Attributes:
        name: The engine name, e.g. ``InnoDB``, ``MyISAM``.
        support: The support level: ``DEFAULT``, ``YES``, ``NO`` or
            ``DISABLED``.
        comment: The engine description.
        transactions: Whether the engine supports transactions.
        xa: Whether the engine supports XA transactions.
        savepoints: Whether the engine supports savepoints.
    """

    name: str
    support: str
    comment: str
    transactions: bool
    xa: bool
    savepoints: bool


class MysqlEngines(_MysqlFact):
    """The server's storage engines and their capabilities.

    **Commands:**

    ```bash
    mysql -Be 'SELECT ENGINE, SUPPORT, COMMENT, TRANSACTIONS, XA, SAVEPOINTS FROM information_schema.ENGINES'
    ```

    **Returns:**

    ```python
    list[MysqlEngine]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_mysql_engines.py``.
    """

    # reemote5-original: there is no other engine-visibility fact. Reads
    # information_schema.ENGINES (the SHOW ENGINES columns); the YES/NO
    # capability columns coerce to booleans, with NULL reading as False.

    MYSQL = (
        "SELECT ENGINE, SUPPORT, COMMENT, TRANSACTIONS, XA, SAVEPOINTS "
        "FROM information_schema.ENGINES"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[MysqlEngine]:
        """Parse ENGINES rows into engine entries."""
        return [
            {
                "name": row["ENGINE"] or "",
                "support": row["SUPPORT"] or "",
                "comment": row["COMMENT"] or "",
                "transactions": row["TRANSACTIONS"] == "YES",
                "xa": row["XA"] == "YES",
                "savepoints": row["SAVEPOINTS"] == "YES",
            }
            for row in _parse_rows(output)
        ]

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        mysql_user: str | None = None,
        mysql_host: str | None = None,
        mysql_port: int | None = None,
        mysql_database: str | None = None,
        mysql_defaults_file: str | None = None,
    ) -> list[MysqlEngine]:
        engines = getattr(host, "mysql_engines", None)
        if engines is not None:
            # SimulatedHost seam: the engine entries, copied per entry
            # so callers never mutate simulated state.
            return [cast(MysqlEngine, dict(entry)) for entry in engines]
        return await cls._run(
            host,
            cls.command(
                mysql_user=mysql_user,
                mysql_host=mysql_host,
                mysql_port=mysql_port,
                mysql_database=mysql_database,
                mysql_defaults_file=mysql_defaults_file,
            ),
        )


class MysqlIndex(TypedDict):
    """
    Attributes:
        name: The index name, e.g. ``PRIMARY`` or ``idx_email``.
        table: The table the index belongs to (unqualified).
        columns: The indexed column names in key order.
        unique: Whether the index is UNIQUE (``PRIMARY`` included).
        index_type: The index method, e.g. ``BTREE``, ``HASH``,
            ``FULLTEXT``, ``SPATIAL``.
    """

    name: str
    table: str
    columns: list[str]
    unique: bool
    index_type: str


class MysqlIndexes(_MysqlFact):
    """A database's index definitions, one entry per index.

    **Commands:**

    ```bash
    mysql -Be "SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, INDEX_TYPE FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = '<database>' ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX"
    ```

    **Returns:**

    ```python
    list[MysqlIndex]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_mysql_indexes.py``.
    """

    # reemote5-original: there is no other index-level fact. Reads
    # information_schema.STATISTICS, which reports one row per index
    # COLUMN; rows are grouped by (table, index) in the ORDER BY order
    # so each entry's column list follows the key order. The optional
    # table argument narrows the WHERE clause (and filters the
    # SimulatedHost seam the same way).

    @classmethod
    def command(
        cls,
        database: str,
        table: str | None = None,
        *,
        mysql_user: str | None = None,
        mysql_host: str | None = None,
        mysql_port: int | None = None,
        mysql_database: str | None = None,
        mysql_defaults_file: str | None = None,
    ) -> str:
        """Shell selecting a database's index rows from information_schema."""
        where = f"WHERE TABLE_SCHEMA = {_quote_literal(database)}"
        if table is not None:
            where += f" AND TABLE_NAME = {_quote_literal(table)}"
        return _mysql_sql(
            "SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, "
            "COLUMN_NAME, INDEX_TYPE "
            f"FROM information_schema.STATISTICS {where} "
            "ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX",
            mysql_user=mysql_user,
            mysql_host=mysql_host,
            mysql_port=mysql_port,
            mysql_database=mysql_database,
            mysql_defaults_file=mysql_defaults_file,
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[MysqlIndex]:
        """Parse STATISTICS rows into per-index entries with ordered columns."""
        grouped: dict[tuple[str, str], MysqlIndex] = {}
        for row in _parse_rows(output):
            key = (row["TABLE_NAME"] or "", row["INDEX_NAME"] or "")
            entry = grouped.get(key)
            if entry is None:
                entry = {
                    "name": key[1],
                    "table": key[0],
                    "columns": [],
                    "unique": row["NON_UNIQUE"] == "0",
                    "index_type": row["INDEX_TYPE"] or "",
                }
                grouped[key] = entry
            column = row["COLUMN_NAME"]
            if column is not None:
                entry["columns"].append(column)
        return list(grouped.values())

    @classmethod
    async def process(
        cls,
        host: Host,
        database: str,
        table: str | None = None,
        *,
        mysql_user: str | None = None,
        mysql_host: str | None = None,
        mysql_port: int | None = None,
        mysql_database: str | None = None,
        mysql_defaults_file: str | None = None,
    ) -> list[MysqlIndex]:
        indexes = getattr(host, "mysql_indexes", None)
        if indexes is not None:
            # SimulatedHost seam: database -> index entries, copied per
            # entry (including the column list) and narrowed to the
            # named table exactly as the WHERE clause does.
            return [
                cast(MysqlIndex, {**entry, "columns": list(entry["columns"])})
                for entry in indexes.get(database, [])
                if table is None or entry["table"] == table
            ]
        return await cls._run(
            host,
            cls.command(
                database,
                table,
                mysql_user=mysql_user,
                mysql_host=mysql_host,
                mysql_port=mysql_port,
                mysql_database=mysql_database,
                mysql_defaults_file=mysql_defaults_file,
            ),
        )


class MysqlPlugin(TypedDict):
    """
    Attributes:
        name: The plugin name, e.g. ``validate_password``.
        status: The plugin status: ``ACTIVE``, ``INACTIVE``,
            ``DISABLED`` or ``DELETED``.
        type: The plugin type, e.g. ``AUTHENTICATION``, ``STORAGE
            ENGINE``, ``AUDIT``, ``REPLICATION``.
        library: The shared-library file name the plugin was loaded
            from; None for built-in plugins.
        license: The plugin license, e.g. ``GPL``; None when unreported.
    """

    name: str
    status: str
    type: str
    library: str | None
    license: str | None


class MysqlPlugins(_MysqlFact):
    """The server's installed plugins and their status.

    **Commands:**

    ```bash
    mysql -Be 'SELECT PLUGIN_NAME, PLUGIN_STATUS, PLUGIN_TYPE, PLUGIN_LIBRARY, PLUGIN_LICENSE FROM information_schema.PLUGINS'
    ```

    **Returns:**

    ```python
    list[MysqlPlugin]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_mysql_plugins.py``.
    """

    # reemote5-original: there is no other plugin-visibility fact. Reads
    # information_schema.PLUGINS (the SHOW PLUGINS columns plus the
    # license); built-in plugins report None for PLUGIN_LIBRARY, which
    # is exactly the distinction mysql.plugin's UNINSTALL branch needs
    # (only library-loaded plugins can be uninstalled at runtime).

    MYSQL = (
        "SELECT PLUGIN_NAME, PLUGIN_STATUS, PLUGIN_TYPE, PLUGIN_LIBRARY, "
        "PLUGIN_LICENSE FROM information_schema.PLUGINS"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[MysqlPlugin]:
        """Parse PLUGINS rows into plugin entries."""
        return [
            {
                "name": row["PLUGIN_NAME"] or "",
                "status": row["PLUGIN_STATUS"] or "",
                "type": row["PLUGIN_TYPE"] or "",
                "library": row["PLUGIN_LIBRARY"],
                "license": row["PLUGIN_LICENSE"],
            }
            for row in _parse_rows(output)
        ]

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        mysql_user: str | None = None,
        mysql_host: str | None = None,
        mysql_port: int | None = None,
        mysql_database: str | None = None,
        mysql_defaults_file: str | None = None,
    ) -> list[MysqlPlugin]:
        plugins = getattr(host, "mysql_plugins", None)
        if plugins is not None:
            # SimulatedHost seam: the plugin entries, copied per entry
            # so callers never mutate simulated state.
            return [cast(MysqlPlugin, dict(entry)) for entry in plugins]
        return await cls._run(
            host,
            cls.command(
                mysql_user=mysql_user,
                mysql_host=mysql_host,
                mysql_port=mysql_port,
                mysql_database=mysql_database,
                mysql_defaults_file=mysql_defaults_file,
            ),
        )


class MysqlReplicaStatus(TypedDict):
    """
    Attributes:
        channel: The replication channel name; empty for the default
            (unnamed) channel.
        source_host: The replication source's host.
        source_port: The replication source's port.
        source_user: The replication user the IO thread authenticates
            as (a user name, never a credential).
        io_running: The IO thread state: ``Yes``, ``No`` or
            ``Connecting``.
        sql_running: The applier thread state: ``Yes`` or ``No``.
        seconds_behind_source: The replication lag in seconds; None
            when the applier is stopped or the lag is unknown.
        last_error: The last replication error text; empty when none.
    """

    channel: str
    source_host: str
    source_port: int
    source_user: str
    io_running: str
    sql_running: str
    seconds_behind_source: int | None
    last_error: str


class MysqlReplication(_MysqlFact):
    """The server's replica channels and their replication state.

    **Commands:**

    ```bash
    mysql -Be 'SHOW REPLICA STATUS'
    ```

    **Returns:**

    ```python
    list[MysqlReplicaStatus]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_mysql_replication.py``.
    """

    # reemote5-original: there is no other replication-visibility fact.
    # SHOW REPLICA STATUS (MySQL 8.0.22+; the legacy SHOW SLAVE STATUS
    # spelling is deliberately not targeted) reports one row per
    # configured channel and an EMPTY result on a server that is not a
    # replica, which reads as []. Only the connection/thread/lag/error
    # columns are kept -- the GTID sets and file positions are long
    # opaque strings better read through mysql.sql when needed.

    MYSQL = "SHOW REPLICA STATUS"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[MysqlReplicaStatus]:
        """Parse SHOW REPLICA STATUS rows into per-channel entries."""
        entries: list[MysqlReplicaStatus] = []
        for row in _parse_rows(output):
            lag = row.get("Seconds_Behind_Source")
            entries.append(
                {
                    "channel": row.get("Channel_Name") or "",
                    "source_host": row.get("Source_Host") or "",
                    "source_port": int(row.get("Source_Port") or 0),
                    "source_user": row.get("Source_User") or "",
                    "io_running": row.get("Replica_IO_Running") or "",
                    "sql_running": row.get("Replica_SQL_Running") or "",
                    "seconds_behind_source": None if lag is None else int(lag),
                    "last_error": row.get("Last_Error") or "",
                }
            )
        return entries

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        mysql_user: str | None = None,
        mysql_host: str | None = None,
        mysql_port: int | None = None,
        mysql_database: str | None = None,
        mysql_defaults_file: str | None = None,
    ) -> list[MysqlReplicaStatus]:
        replication = getattr(host, "mysql_replication", None)
        if replication is not None:
            # SimulatedHost seam: the per-channel entries, copied per
            # entry so callers never mutate simulated state.
            return [cast(MysqlReplicaStatus, dict(entry)) for entry in replication]
        return await cls._run(
            host,
            cls.command(
                mysql_user=mysql_user,
                mysql_host=mysql_host,
                mysql_port=mysql_port,
                mysql_database=mysql_database,
                mysql_defaults_file=mysql_defaults_file,
            ),
        )


class MysqlStatus(_MysqlFact):
    """The server's global runtime status counters, keyed by counter name.

    **Commands:**

    ```bash
    mysql -Be 'SHOW GLOBAL STATUS'
    ```

    **Returns:**

    ```python
    dict[str, str]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_mysql_status.py``.
    """

    # reemote5-original: there is no other runtime-status fact. Values stay
    # TEXT (MySQL reports counters, floats like Ssl_session_cache_hits
    # ratios and even empty values through the same two-column shape);
    # callers coerce the counters they care about. A NULL/empty value
    # reads as the empty string so the mapping is always str -> str.

    MYSQL = "SHOW GLOBAL STATUS"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, str]:
        """Parse SHOW GLOBAL STATUS rows into the name-keyed counter map."""
        status: dict[str, str] = {}
        for row in _parse_rows(output):
            name = row.get("Variable_name")
            if name is not None:
                status[name] = row.get("Value") or ""
        return status

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        mysql_user: str | None = None,
        mysql_host: str | None = None,
        mysql_port: int | None = None,
        mysql_database: str | None = None,
        mysql_defaults_file: str | None = None,
    ) -> dict[str, str]:
        status = getattr(host, "mysql_status", None)
        if status is not None:
            # SimulatedHost seam: counter name -> text value, a flat
            # str->str copy (the values are immutable).
            return dict(status)
        return await cls._run(
            host,
            cls.command(
                mysql_user=mysql_user,
                mysql_host=mysql_host,
                mysql_port=mysql_port,
                mysql_database=mysql_database,
                mysql_defaults_file=mysql_defaults_file,
            ),
        )


class MysqlTable(TypedDict):
    """
    Attributes:
        name: The table name (unqualified).
        engine: The storage engine, e.g. ``InnoDB``; None for views.
        rows: The row-count estimate (InnoDB statistics, not an exact
            count); 0 when unreported.
        data_length: The data size in bytes.
        index_length: The index size in bytes.
        collation: The table collation, e.g. ``utf8mb4_0900_ai_ci``;
            None for views.
        comment: The table comment; empty when none.
    """

    name: str
    engine: str | None
    rows: int
    data_length: int
    index_length: int
    collation: str | None
    comment: str


class MysqlTables(_MysqlFact):
    """One database's tables and their metadata, in name order.

    **Commands:**

    ```bash
    mysql -Be "SELECT TABLE_NAME, ENGINE, TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH, TABLE_COLLATION, TABLE_COMMENT FROM information_schema.TABLES WHERE TABLE_SCHEMA = '<database>' ORDER BY TABLE_NAME"
    ```

    **Returns:**

    ```python
    list[MysqlTable]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_mysql_tables.py``.
    """

    # reemote5-original: there is no other table-level fact. Reads
    # information_schema.TABLES filtered to one database and ordered by
    # name so the list is deterministic; TABLE_ROWS is the engine's
    # estimate (exact for MyISAM, approximate for InnoDB). Views appear
    # with None engine/collation, exactly as the view reports them.

    @classmethod
    def command(
        cls,
        database: str,
        *,
        mysql_user: str | None = None,
        mysql_host: str | None = None,
        mysql_port: int | None = None,
        mysql_database: str | None = None,
        mysql_defaults_file: str | None = None,
    ) -> str:
        """Shell selecting one database's tables from information_schema."""
        return _mysql_sql(
            "SELECT TABLE_NAME, ENGINE, TABLE_ROWS, DATA_LENGTH, "
            "INDEX_LENGTH, TABLE_COLLATION, TABLE_COMMENT "
            "FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = {_quote_literal(database)} "
            "ORDER BY TABLE_NAME",
            mysql_user=mysql_user,
            mysql_host=mysql_host,
            mysql_port=mysql_port,
            mysql_database=mysql_database,
            mysql_defaults_file=mysql_defaults_file,
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[MysqlTable]:
        """Parse information_schema.TABLES rows into table entries."""
        return [
            {
                "name": row["TABLE_NAME"] or "",
                "engine": row["ENGINE"],
                "rows": int(row["TABLE_ROWS"] or 0),
                "data_length": int(row["DATA_LENGTH"] or 0),
                "index_length": int(row["INDEX_LENGTH"] or 0),
                "collation": row["TABLE_COLLATION"],
                "comment": row["TABLE_COMMENT"] or "",
            }
            for row in _parse_rows(output)
        ]

    @classmethod
    async def process(
        cls,
        host: Host,
        database: str,
        *,
        mysql_user: str | None = None,
        mysql_host: str | None = None,
        mysql_port: int | None = None,
        mysql_database: str | None = None,
        mysql_defaults_file: str | None = None,
    ) -> list[MysqlTable]:
        tables = getattr(host, "mysql_tables", None)
        if tables is not None:
            # SimulatedHost seam: database -> table entries, copied per
            # entry so callers never mutate simulated state.
            return [
                cast(MysqlTable, dict(entry))
                for entry in tables.get(database, [])
            ]
        return await cls._run(
            host,
            cls.command(
                database,
                mysql_user=mysql_user,
                mysql_host=mysql_host,
                mysql_port=mysql_port,
                mysql_database=mysql_database,
                mysql_defaults_file=mysql_defaults_file,
            ),
        )


# The grant-line regex: the privilege list, the
# ``db`.`table`` target and the trailing extras (WITH GRANT OPTION).
MYSQL_GRANT_REGEX = (
    r"^GRANT ([A-Z,\s]+) ON ((?:\*|`[a-z_\\]+`)\.(?:\*|`[a-z_]+`)) "
    r"TO `[A-Z0-9a-z_\-]+`@`(?:%|[A-Z0-9a-z_\.\-]+)`(.*)"
)

# reemote5 extension: a role-grant line (``GRANT `role`@`%` TO ...``)
# has no ON clause, so the regex skips it. Capturing the granted
# role names lets mysql.role check membership idempotently; role keys
# carry an ``@`` where privilege keys carry a ``.`` so they never collide.
MYSQL_ROLE_GRANT_REGEX = r"^GRANT (`[^`]+`@`[^`]+`(?:,\s*`[^`]+`@`[^`]+`)*) TO "


class MysqlUserGrants(_MysqlFact):
    """One account's grants, keyed by ``database.table`` target.

    **Commands:**

    ```bash
    mysql -Be 'SHOW GRANTS FOR "<user>"@"<hostname>"' || true
    ```

    **Returns:**

    ```python
    dict[str, list[str]]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_mysql_user_grants.py``.
    """

    # ``mysql.MysqlUserGrants``: SHOW GRANTS FOR the
    # account, parsed with the grant regex, ``WITH GRANT OPTION``
    # folded in as a "GRANT OPTION" privilege and MySQL's pre-escaped
    # backslashes collapsed. The ``|| true`` guard is the
    # ignore_errors (SHOW GRANTS errors when the user does not exist,
    # which reads as {}). Two deliberate deviations: the values are
    # SORTED LISTS rather than sets (JSON-printable in the examples,
    # deterministic in reasons), and role-grant lines are additionally
    # captured under their backticked ``role``@``host`` key with the
    # single privilege "ROLE" (the grant regex skips them; mysql.role reads
    # them for membership idempotence).

    IGNORE_ERRORS = True

    @classmethod
    def command(
        cls,
        user: str,
        hostname: str = "localhost",
        *,
        mysql_user: str | None = None,
        mysql_host: str | None = None,
        mysql_port: int | None = None,
        mysql_database: str | None = None,
        mysql_defaults_file: str | None = None,
    ) -> str:
        """Shell showing one account's grants, guarded for absent users."""
        # The account is interpolated into double quotes raw;
        # embedded double quotes are doubled so the statement survives.
        user = user.replace('"', '""')
        hostname = hostname.replace('"', '""')
        return _mysql_sql(
            f'SHOW GRANTS FOR "{user}"@"{hostname}"',
            mysql_user=mysql_user,
            mysql_host=mysql_host,
            mysql_port=mysql_port,
            mysql_database=mysql_database,
            mysql_defaults_file=mysql_defaults_file,
            ignore_errors=True,
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, list[str]]:
        """Parse SHOW GRANTS lines into the target-keyed privilege lists."""
        grants: dict[str, set[str]] = {}
        for line in output:
            matches = re.match(MYSQL_GRANT_REGEX, line)
            if matches:
                privileges, database_table, extras = matches.groups()
                # MySQL outputs this pre-escaped.
                database_table = database_table.replace("\\\\", "\\")
                target = grants.setdefault(database_table, set())
                for privilege in privileges.split(","):
                    target.add(privilege.strip())
                if "WITH GRANT OPTION" in extras:
                    target.add("GRANT OPTION")
                continue
            role_matches = re.match(MYSQL_ROLE_GRANT_REGEX, line)
            if role_matches:
                for role in role_matches.group(1).split(","):
                    grants.setdefault(role.strip(), set()).add("ROLE")
        return {target: sorted(privileges) for target, privileges in grants.items()}

    @classmethod
    async def process(
        cls,
        host: Host,
        user: str,
        hostname: str = "localhost",
        *,
        mysql_user: str | None = None,
        mysql_host: str | None = None,
        mysql_port: int | None = None,
        mysql_database: str | None = None,
        mysql_defaults_file: str | None = None,
    ) -> dict[str, list[str]]:
        user_grants = getattr(host, "mysql_user_grants", None)
        if user_grants is not None:
            # SimulatedHost seam: "user@hostname" -> target-keyed
            # privilege lists, copied per target so callers never
            # mutate simulated state.
            return {
                target: list(privileges)
                for target, privileges in user_grants.get(
                    f"{user}@{hostname}", {}
                ).items()
            }
        return await cls._run(
            host,
            cls.command(
                user,
                hostname,
                mysql_user=mysql_user,
                mysql_host=mysql_host,
                mysql_port=mysql_port,
                mysql_database=mysql_database,
                mysql_defaults_file=mysql_defaults_file,
            ),
        )


class MysqlUser(TypedDict, total=False):
    """
    Attributes:
        privileges: The sorted global privileges parsed from the
            ``*_priv`` columns (which are removed from the entry).
        plugin: The authentication plugin, e.g.
            ``caching_sha2_password`` or ``auth_socket``; None when
            the server reports none.
        authentication_string: The password hash as stored (never a
            plaintext password); None when empty.
        ssl_type: The REQUIRE level: ``ANY`` (SSL), ``X509``,
            ``SPECIFIED``; None when no TLS is required.
        ssl_cipher: The required TLS cipher; None when unset.
        x509_issuer: The required certificate issuer; None when unset.
        x509_subject: The required certificate subject; None when unset.
        max_questions: The MAX_QUERIES_PER_HOUR limit; 0 means unlimited.
        max_updates: The MAX_UPDATES_PER_HOUR limit; 0 means unlimited.
        max_connections: The MAX_CONNECTIONS_PER_HOUR limit; 0 means
            unlimited.
        max_user_connections: The MAX_USER_CONNECTIONS limit; 0 means
            unlimited.
        password_expired: Whether the password is expired.
        is_role: Whether the account is a role rather than a user.
        account_locked: Whether the account is locked (MySQL 5.7+).
        default_role: The account's default role (MariaDB); None when
            unset.
        max_statement_time: The statement timeout (MariaDB); None when
            unreported.
        Password: The legacy password hash column (MariaDB / MySQL
            5.6); None when empty.
    """

    privileges: list[str]
    plugin: str | None
    authentication_string: str | None
    ssl_type: str | None
    ssl_cipher: str | None
    x509_issuer: str | None
    x509_subject: str | None
    max_questions: int | str
    max_updates: int | str
    max_connections: int | str
    max_user_connections: int | str
    password_expired: bool
    is_role: bool
    account_locked: bool
    default_role: str | None
    max_statement_time: str | None
    Password: str | None


class MysqlUsers(_MysqlFact):
    """The server's accounts, keyed by ``user@host``.

    **Commands:**

    ```bash
    mysql -Be 'SELECT * FROM mysql.user'
    ```

    **Returns:**

    ```python
    dict[str, MysqlUser]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_mysql_users.py``.
    """

    # ``mysql.MysqlUsers``: SELECT * over mysql.user,
    # keyed user@host, ``*_priv`` columns folded into the sorted
    # ``privileges`` list, ``max_*`` columns coerced with try_int and
    # password_expired/is_role coerced from Y/N. Rows with an empty
    # Host/User (the anonymous account) are skipped.
    # The column set follows the server version/flavour (the fixture
    # is MariaDB-shaped: Password, max_statement_time), so MysqlUser is
    # a total=False TypedDict and the header-driven parse carries
    # whatever columns the server reports. One extension: MySQL's
    # account_locked column is also coerced from Y/N.

    MYSQL = "SELECT * FROM mysql.user"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, MysqlUser]:
        """Parse mysql.user rows into the user@host-keyed account mapping."""
        users: dict[str, MysqlUser] = {}
        for row in _parse_rows(output):
            details: dict[str, Any] = dict(row)
            if details.get("Host") is None or details.get("User") is None:
                continue
            privileges = []
            for key, value in list(details.items()):
                if key.endswith("_priv") and details.pop(key) == "Y":
                    privileges.append(key.replace("_priv", ""))
                if key.startswith("max_"):
                    details[key] = _try_int(value)
                if key in ("password_expired", "is_role", "account_locked"):
                    details[key] = value == "Y"
            details["privileges"] = sorted(privileges)
            users[f"{details.pop('User')}@{details.pop('Host')}"] = cast(
                MysqlUser, details
            )
        return users

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        mysql_user: str | None = None,
        mysql_host: str | None = None,
        mysql_port: int | None = None,
        mysql_database: str | None = None,
        mysql_defaults_file: str | None = None,
    ) -> dict[str, MysqlUser]:
        users = getattr(host, "mysql_users", None)
        if users is not None:
            # SimulatedHost seam: user@host -> MysqlUser, copied per
            # entry (including the privileges list) so callers never
            # mutate simulated state.
            return {
                name: cast(
                    MysqlUser,
                    {**entry, "privileges": list(entry.get("privileges", []))},
                )
                for name, entry in users.items()
            }
        return await cls._run(
            host,
            cls.command(
                mysql_user=mysql_user,
                mysql_host=mysql_host,
                mysql_port=mysql_port,
                mysql_database=mysql_database,
                mysql_defaults_file=mysql_defaults_file,
            ),
        )


class MysqlVariables(_MysqlFact):
    """The server's global system variables, keyed by variable name.

    **Commands:**

    ```bash
    mysql -Be 'SHOW GLOBAL VARIABLES'
    ```

    **Returns:**

    ```python
    dict[str, str]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_mysql_variables.py``.
    """

    # reemote5-original: there is no other configuration-visibility fact.
    # Values stay TEXT in the server's own rendering (bytes for
    # innodb_buffer_pool_size, ON/OFF for booleans) -- mysql.variable
    # compares against exactly this text, so callers pass "262144"
    # rather than "256K". A NULL/empty value reads as the empty string
    # so the mapping is always str -> str. Session variables are
    # deliberately not exposed: a fresh CLI connection's session IS
    # the global value, so a session fact would only duplicate this one.

    MYSQL = "SHOW GLOBAL VARIABLES"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, str]:
        """Parse SHOW GLOBAL VARIABLES rows into the name-keyed value map."""
        variables: dict[str, str] = {}
        for row in _parse_rows(output):
            name = row.get("Variable_name")
            if name is not None:
                variables[name] = row.get("Value") or ""
        return variables

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        mysql_user: str | None = None,
        mysql_host: str | None = None,
        mysql_port: int | None = None,
        mysql_database: str | None = None,
        mysql_defaults_file: str | None = None,
    ) -> dict[str, str]:
        variables = getattr(host, "mysql_variables", None)
        if variables is not None:
            # SimulatedHost seam: variable name -> text value, a flat
            # str->str copy (the values are immutable).
            return dict(variables)
        return await cls._run(
            host,
            cls.command(
                mysql_user=mysql_user,
                mysql_host=mysql_host,
                mysql_port=mysql_port,
                mysql_database=mysql_database,
                mysql_defaults_file=mysql_defaults_file,
            ),
        )


# --- Operations --------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class _MysqlCommand(Command):
    """Shared mysql connection fields for every mysql descriptor.

    ``kw_only`` so concrete descriptors can declare their required
    operands positionally while the connection tail keeps its defaults.
    Never exported or rendered; the concrete descriptors beside each
    operation carry the SQL semantics.
    """

    mysql_user: str | None = None
    mysql_host: str | None = None
    mysql_port: int | None = None
    mysql_database: str | None = None
    mysql_defaults_file: str | None = None

    def _base(self, executable: str = "mysql") -> str:
        return _mysql_base(
            mysql_user=self.mysql_user,
            mysql_host=self.mysql_host,
            mysql_port=self.mysql_port,
            mysql_database=self.mysql_database,
            mysql_defaults_file=self.mysql_defaults_file,
            executable=executable,
        )

    def _sql_shell(self, sql: str) -> str:
        return f"{self._base()} -Be {shlex.quote(sql)}"

    def _conn(self) -> dict[str, Any]:
        return {
            "mysql_user": self.mysql_user,
            "mysql_host": self.mysql_host,
            "mysql_port": self.mysql_port,
            "mysql_database": self.mysql_database,
            "mysql_defaults_file": self.mysql_defaults_file,
        }


def _account(user: str, hostname: str) -> str:
    """Render a ``"user"@"hostname"`` account, doubling embedded quotes."""
    user = user.replace('"', '""')
    hostname = hostname.replace('"', '""')
    return f'"{user}"@"{hostname}"'


async def _privilege_plan(
    ctx: OpContext,
    *,
    user: str,
    privileges: str | list[str] | set[str],
    user_hostname: str,
    database: str,
    table: str,
    flush: bool,
    with_grant_option: bool,
    conn: dict[str, Any],
) -> tuple[list[Command], list[str]]:
    """Compute the GRANT/REVOKE descriptors for one account and target.

    The shared idempotence core of ``mysql.privileges`` and the
    ``privileges`` delegation inside ``mysql.database``/``mysql.user``
    (the shared privilege-plan core). Sets ``ctx.changed`` when any
    descriptor is planned; returns the descriptors plus their reasons
    so the caller can join them into its own ``ctx.reason``.
    """
    desired = {privileges} if isinstance(privileges, str) else set(privileges)
    if with_grant_option:
        desired.add("GRANT OPTION")

    if database != "*":
        database = _quote_ident(database)
    if table != "*":
        # Quote first so the error message names the target exactly as
        # it should (*.`mytable`).
        table = _quote_ident(table)
        if database == "*":
            raise OperationError(
                f"Cannot apply MySQL privileges on {database}.{table}, "
                "no database provided"
            )
    database_table = f"{database}.{table}"

    user_grants = await ctx.facts.get(
        MysqlUserGrants, user, user_hostname, **conn
    )
    existing = {
        "ALL" if privilege == "ALL PRIVILEGES" else privilege
        for privilege in user_grants.get(database_table, [])
    }
    # Role grants are membership, not privileges; never revoke them here.
    existing.discard("ROLE")

    to_revoke = existing - desired
    to_grant = desired - existing
    if to_grant:
        # Granting something overrides USAGE; no need to revoke it.
        to_revoke.discard("USAGE")

    commands: list[Command] = []
    reasons: list[str] = []

    def plan(action: str, applied: set[str], with_statement: str | None = None):
        commands.append(
            MysqlPrivilegesCommand(
                action=action,
                privileges=tuple(sorted(applied)),
                database=database,
                table=table,
                user=user,
                user_hostname=user_hostname,
                with_statement=with_statement,
                **conn,
            )
        )

    if to_revoke:
        if to_revoke == {"ALL", "GRANT OPTION"}:
            # REVOKE ALL PRIVILEGES, GRANT OPTION FROM is the alternative
            # (no ON) syntax; revoke in two steps to keep the ON form.
            plan("REVOKE", {"GRANT OPTION"})
            plan("REVOKE", {"ALL PRIVILEGES"})
        else:
            plan("REVOKE", to_revoke)
        reasons.append(
            f"privileges will be revoked: {', '.join(sorted(to_revoke))}"
        )
    if to_grant:
        if to_grant == {"ALL", "GRANT OPTION"}:
            # ALL must be named alone; GRANT OPTION rides a WITH clause.
            plan("GRANT", {"ALL PRIVILEGES"}, "GRANT OPTION")
        else:
            plan("GRANT", to_grant)
        reasons.append(
            f"privileges will be granted: {', '.join(sorted(to_grant))}"
        )
    if commands:
        ctx.changed = True
        if flush:
            commands.append(MysqlSqlCommand(sql="FLUSH PRIVILEGES", **conn))
    else:
        reasons.append("mysql privileges are already correct")
    return commands, reasons


@dataclass(frozen=True)
class MysqlDatabaseCommand(_MysqlCommand):
    """``mysql -Be 'CREATE/ALTER/DROP DATABASE ...'``: manage one database.

    One present/alter-flagged descriptor for all three directions (the
    ``PostgresqlDatabaseCommand`` precedent): ``present=False`` renders
    DROP DATABASE, ``alter=True`` renders ALTER DATABASE with the
    non-None charset/collation, and the create form renders CREATE
    DATABASE with the established clause order (COLLATE then CHARSET).
    """

    database: str
    present: bool = True
    alter: bool = False
    charset: str | None = None
    collate: str | None = None

    def shell(self) -> str:
        name = _quote_ident(self.database)
        if not self.present:
            return self._sql_shell(f"DROP DATABASE {name}")
        bits = [f"{'ALTER' if self.alter else 'CREATE'} DATABASE {name}"]
        if self.collate:
            bits.append(f"COLLATE {_quote_ident(self.collate)}")
        if self.charset:
            bits.append(f"CHARSET {_quote_ident(self.charset)}")
        return self._sql_shell(" ".join(bits))


async def database(
    ctx: OpContext,
    database: str,
    present: bool = True,
    collate: str | None = None,
    charset: str | None = None,
    user: str | None = None,
    user_hostname: str = "localhost",
    user_privileges: str | list[str] = "ALL",
    mysql_user: str | None = None,
    mysql_host: str | None = None,
    mysql_port: int | None = None,
    mysql_database: str | None = None,
    mysql_defaults_file: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add, alter or remove a MySQL database.

    Args:
        ctx (OpContext): Execution context for the operation.
        database (str): Name of the database to manage.
        present (bool, optional): Whether the database should exist.
            Defaults to True.
        collate (str | None, optional): Default collation; altered on
            an existing database when it differs. Defaults to None.
        charset (str | None, optional): Default character set; altered
            on an existing database when it differs. Defaults to None.
        user (str | None, optional): MySQL user to grant privileges on
            this database to. Defaults to None.
        user_hostname (str, optional): Hostname part of the user to
            grant to. Defaults to "localhost".
        user_privileges (str | list[str], optional): Privileges to
            grant to any specified user. Defaults to "ALL".
        mysql_user (str | None, optional): User to connect as.
            Defaults to None.
        mysql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        mysql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        mysql_database (str | None, optional): Database to open the
            connection with. Defaults to None.
        mysql_defaults_file (str | None, optional): Host-side option
            file rendered as --defaults-extra-file (credentials never
            come from the operation). Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated MySQL database commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, OperationError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_database.py``.
    """

    # ``mysql.database``, extended: the baseline applies
    # charset/collate only at CREATE time and never alters an existing
    # database; reemote5 compares them against MysqlDatabases and
    # renders ALTER DATABASE on drift (the postgresql.database
    # idempotent-alter precedent). The user/user_hostname/user_privileges
    # grant delegation is shared, using the mysql.privileges core.
    # The ``mysql_password`` is deliberately dropped (credentials
    # come from sudo socket auth / ~/.my.cnf / mysql_defaults_file,
    # never the operation). Needs CREATE/ALTER/DROP on the database:
    # flag the request ``sudo=True`` for root socket auth. ``**kwargs``
    # absorbs any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    conn = {
        "mysql_user": mysql_user,
        "mysql_host": mysql_host,
        "mysql_port": mysql_port,
        "mysql_database": mysql_database,
        "mysql_defaults_file": mysql_defaults_file,
    }
    current = await ctx.facts.get(MysqlDatabases, **conn)
    reasons: list[str] = []

    if not present:
        if database in current:
            ctx.changed = True
            reasons.append(f"mysql database {database} will be dropped")
            yield MysqlDatabaseCommand(database=database, present=False, **conn)
        else:
            reasons.append(f"mysql database {database} does not exist")
        ctx.reason = "; ".join(reasons)
        return

    if database not in current:
        ctx.changed = True
        reasons.append(f"mysql database {database} will be created")
        yield MysqlDatabaseCommand(
            database=database, charset=charset, collate=collate, **conn
        )
    else:
        entry = current[database]
        changed_charset = (
            charset if charset and entry.get("character_set") != charset else None
        )
        changed_collate = (
            collate if collate and entry.get("collation_name") != collate else None
        )
        if changed_charset or changed_collate:
            ctx.changed = True
            reasons.append(f"mysql database {database} will be altered")
            yield MysqlDatabaseCommand(
                database=database,
                alter=True,
                charset=changed_charset,
                collate=changed_collate,
                **conn,
            )
        else:
            reasons.append(f"mysql database {database} already exists")

    # Ensure any user privileges for this database (the shared privilege bundle).
    if user and user_privileges:
        commands, grant_reasons = await _privilege_plan(
            ctx,
            user=user,
            privileges=user_privileges,
            user_hostname=user_hostname,
            database=database,
            table="*",
            flush=True,
            with_grant_option=False,
            conn=conn,
        )
        reasons.extend(grant_reasons)
        for command in commands:
            yield command
    ctx.reason = "; ".join(reasons)


@dataclass(frozen=True)
class MysqlDumpCommand(_MysqlCommand):
    """``mysqldump ... > <dest>``: dump a database to a host-side file.

    The stateless mutation ``mysql.dump`` yields. ``dest`` is a path on
    the TARGET host (a shell redirect, not a transfer); to pull the
    bytes controller-side, follow up with ``files.get``.
    """

    dest: str

    def shell(self) -> str:
        return f"{self._base(executable='mysqldump')} > {shlex.quote(self.dest)}"


async def dump(
    ctx: OpContext,
    dest: str,
    database: str | None = None,
    mysql_user: str | None = None,
    mysql_host: str | None = None,
    mysql_port: int | None = None,
    mysql_defaults_file: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Dump a MySQL database into a host-side ``.sql`` file.

    Args:
        ctx (OpContext): Execution context for the operation.
        dest (str): Path on the target host to dump the SQL to.
        database (str | None, optional): Name of the database to dump.
            Defaults to None.
        mysql_user (str | None, optional): User to connect as.
            Defaults to None.
        mysql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        mysql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        mysql_defaults_file (str | None, optional): Host-side option
            file rendered as --defaults-extra-file. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The mysqldump command, always (the operation is
            stateless).

    **Raises:**

    ```python
    None
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_dump.py``.
    """

    # ``mysql.dump``: non-idempotent, always yields
    # (it is non-idempotent by design). Requires ``mysqldump`` on
    # the target host. database=None renders a bare
    # mysqldump (no --all-databases is injected), so name a database or
    # supply one through the option file. The redirect runs in the
    # connection user's shell, so ``dest`` must be writable by that
    # user (or flag the request ``sudo=True``). ``mysql_password`` is
    # deliberately dropped. ``**kwargs`` absorbs any further global
    # arguments.
    ctx.changed = True
    ctx.reason = f"mysql dump will be written to {dest}"
    yield MysqlDumpCommand(
        dest=dest,
        mysql_user=mysql_user,
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_database=database,
        mysql_defaults_file=mysql_defaults_file,
    )


@dataclass(frozen=True)
class MysqlIndexCommand(_MysqlCommand):
    """``mysql -Be 'CREATE/DROP INDEX ...'``: manage one index.

    The create form renders CREATE [UNIQUE|FULLTEXT|SPATIAL] INDEX with
    the optional USING BTREE/HASH method and the column list;
    ``present=False`` renders DROP INDEX ... ON the qualified table.
    Column entries render verbatim inside the parentheses so
    expressions (``(lower(email))`` functional indexes) work.
    """

    database: str
    table: str
    index: str
    columns: tuple[str, ...] = ()
    unique: bool = False
    index_type: str = "BTREE"
    present: bool = True

    def shell(self) -> str:
        name = _quote_ident(self.index)
        target = f"{_quote_ident(self.database)}.{_quote_ident(self.table)}"
        if not self.present:
            return self._sql_shell(f"DROP INDEX {name} ON {target}")
        bits = ["CREATE"]
        if self.index_type in ("FULLTEXT", "SPATIAL"):
            bits.append(self.index_type)
        elif self.unique:
            bits.append("UNIQUE")
        bits.append(f"INDEX {name}")
        if self.index_type in ("BTREE", "HASH"):
            bits.append(f"USING {self.index_type}")
        bits.append(f"ON {target} ({', '.join(self.columns)})")
        return self._sql_shell(" ".join(bits))


async def index(
    ctx: OpContext,
    database: str,
    table: str,
    index: str,
    columns: list[str] | None = None,
    unique: bool = False,
    index_type: str = "BTREE",
    present: bool = True,
    mysql_user: str | None = None,
    mysql_host: str | None = None,
    mysql_port: int | None = None,
    mysql_database: str | None = None,
    mysql_defaults_file: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Create or drop an index on a MySQL table.

    Args:
        ctx (OpContext): Execution context for the operation.
        database (str): Database holding the table.
        table (str): Table to index.
        index (str): Name of the index to manage.
        columns (list[str] | None, optional): Column names or
            parenthesized expressions to index, in key order; required
            when present is True. Defaults to None.
        unique (bool, optional): Create a UNIQUE index. Defaults to
            False.
        index_type (str, optional): Index method: BTREE, HASH, FULLTEXT
            or SPATIAL. Defaults to "BTREE".
        present (bool, optional): Whether the index should exist.
            Defaults to True.
        mysql_user (str | None, optional): User to connect as.
            Defaults to None.
        mysql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        mysql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        mysql_database (str | None, optional): Database to open the
            connection with. Defaults to None.
        mysql_defaults_file (str | None, optional): Host-side option
            file rendered as --defaults-extra-file. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated MySQL index commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, OperationError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_index.py``.
    """

    # reemote5-original (there is no other index operation), the
    # postgresql.index shape. Idempotent against MysqlIndexes keyed on
    # the index name within the table. An existing index whose columns/
    # unique/index_type differ raises OperationError rather than
    # silently rebuilding it -- a drop/recreate of a possibly UNIQUE
    # constraint-backing index is too destructive to infer (the
    # postgresql.index precedent). FULLTEXT/SPATIAL render as the
    # CREATE prefix (they cannot combine with UNIQUE or USING);
    # ``columns`` entries are SQL rendered verbatim, never shell (the
    # whole statement is one shlex-quoted mysql argument). ``**kwargs``
    # absorbs any further global arguments.
    if index_type not in ("BTREE", "HASH", "FULLTEXT", "SPATIAL"):
        raise ValueError(
            "mysql.index index_type must be BTREE, HASH, FULLTEXT or SPATIAL"
        )
    if present and not columns:
        raise ValueError("mysql.index requires columns when present=True")
    conn = {
        "mysql_user": mysql_user,
        "mysql_host": mysql_host,
        "mysql_port": mysql_port,
        "mysql_database": mysql_database,
        "mysql_defaults_file": mysql_defaults_file,
    }
    indexes = await ctx.facts.get(MysqlIndexes, database, table, **conn)
    current = next((e for e in indexes if e["name"] == index), None)

    if not present:
        if current is not None:
            ctx.changed = True
            ctx.reason = f"mysql index {index} will be dropped"
            yield MysqlIndexCommand(
                database=database, table=table, index=index, present=False, **conn
            )
        else:
            ctx.reason = f"mysql index {index} does not exist"
        return

    if current is None:
        ctx.changed = True
        ctx.reason = f"mysql index {index} will be created"
        yield MysqlIndexCommand(
            database=database,
            table=table,
            index=index,
            columns=tuple(columns or ()),
            unique=unique,
            index_type=index_type,
            **conn,
        )
        return

    drift = [
        label
        for label, want, have in (
            ("columns", list(columns or ()), current["columns"]),
            ("unique", unique, current["unique"]),
            ("index_type", index_type, current["index_type"]),
        )
        if want != have
    ]
    if drift:
        raise OperationError(
            f"mysql index {index} exists with a different definition "
            f"({', '.join(sorted(drift))} differ); drop it first to rebuild"
        )
    ctx.reason = f"mysql index {index} already exists"


@dataclass(frozen=True)
class MysqlKillCommand(_MysqlCommand):
    """``mysql -Be 'KILL [QUERY] <id>'``: terminate a connection or query.

    The stateless mutation ``mysql.kill`` yields. ``query_only=True``
    renders KILL QUERY (abort the running statement, keep the
    connection); otherwise KILL closes the connection itself.
    """

    process_id: int
    query_only: bool = False

    def shell(self) -> str:
        verb = "KILL QUERY" if self.query_only else "KILL"
        return self._sql_shell(f"{verb} {self.process_id}")


async def kill(
    ctx: OpContext,
    process_id: int,
    query_only: bool = False,
    mysql_user: str | None = None,
    mysql_host: str | None = None,
    mysql_port: int | None = None,
    mysql_database: str | None = None,
    mysql_defaults_file: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Terminate a MySQL connection or its running query.

    Args:
        ctx (OpContext): Execution context for the operation.
        process_id (int): The connection (thread) ID to kill, as
            reported by MysqlConnections.
        query_only (bool, optional): Abort only the running statement
            (KILL QUERY), keeping the connection open. Defaults to
            False.
        mysql_user (str | None, optional): User to connect as.
            Defaults to None.
        mysql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        mysql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        mysql_database (str | None, optional): Database to open the
            connection with. Defaults to None.
        mysql_defaults_file (str | None, optional): Host-side option
            file rendered as --defaults-extra-file. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The KILL command, always (the operation is stateless).

    **Raises:**

    ```python
    None
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_kill.py``.
    """

    # reemote5-original (there is no other kill operation), the server.kill
    # shape: stateless, always yields -- a connection ID is a snapshot
    # value, so comparing it against MysqlConnections inside the
    # operation would race the server anyway. Killing an already-gone
    # ID errors server-side (Unknown thread id), surfaced as
    # CommandError at apply time. Needs PROCESS/SUPER (or ownership of
    # the target thread): flag the request ``sudo=True`` for root
    # socket auth. ``**kwargs`` absorbs any further global arguments.
    ctx.changed = True
    verb = "query on connection" if query_only else "connection"
    ctx.reason = f"mysql {verb} {process_id} will be killed"
    yield MysqlKillCommand(
        process_id=process_id,
        query_only=query_only,
        mysql_user=mysql_user,
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_database=mysql_database,
        mysql_defaults_file=mysql_defaults_file,
    )


@dataclass(frozen=True)
class MysqlLoadCommand(_MysqlCommand):
    """``mysql ... < <src>``: load a host-side SQL file into a database.

    The stateless mutation ``mysql.load`` yields. ``src`` is a path on
    the TARGET host (a shell redirect, not a transfer); to load
    controller-side bytes, spool them with ``files.put`` first.
    """

    src: str

    def shell(self) -> str:
        return f"{self._base()} < {shlex.quote(self.src)}"


async def load(
    ctx: OpContext,
    src: str,
    database: str | None = None,
    mysql_user: str | None = None,
    mysql_host: str | None = None,
    mysql_port: int | None = None,
    mysql_defaults_file: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Load a host-side ``.sql`` file into a MySQL database.

    Args:
        ctx (OpContext): Execution context for the operation.
        src (str): Path on the target host to read the SQL from.
        database (str | None, optional): Name of the database to
            import into. Defaults to None.
        mysql_user (str | None, optional): User to connect as.
            Defaults to None.
        mysql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        mysql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        mysql_defaults_file (str | None, optional): Host-side option
            file rendered as --defaults-extra-file. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The load command, always (the operation is stateless).

    **Raises:**

    ```python
    None
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_load.py``.
    """

    # ``mysql.load``: non-idempotent, always yields
    # (it is non-idempotent by design). ``src`` is a host-side
    # path read by the connection user's shell redirect -- reemote5
    # transfers take IO streams, so controller-side bytes go through
    # files.put first (the postgresql.load precedent).
    # ``mysql_password`` is deliberately dropped. ``**kwargs`` absorbs
    # any further global arguments.
    ctx.changed = True
    ctx.reason = f"mysql load will read {src}"
    yield MysqlLoadCommand(
        src=src,
        mysql_user=mysql_user,
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_database=database,
        mysql_defaults_file=mysql_defaults_file,
    )


@dataclass(frozen=True)
class MysqlOptimizeCommand(_MysqlCommand):
    """``mysql -Be 'OPTIMIZE/ANALYZE/CHECK/REPAIR TABLE ...'``: maintenance.

    The stateless mutation ``mysql.optimize`` yields: one statement
    over the comma-separated qualified table list, in the caller's
    order.
    """

    database: str
    tables: tuple[str, ...]
    action: str = "optimize"

    def shell(self) -> str:
        targets = ", ".join(
            f"{_quote_ident(self.database)}.{_quote_ident(table)}"
            for table in self.tables
        )
        return self._sql_shell(f"{self.action.upper()} TABLE {targets}")


async def optimize(
    ctx: OpContext,
    database: str,
    tables: list[str],
    action: str = "optimize",
    mysql_user: str | None = None,
    mysql_host: str | None = None,
    mysql_port: int | None = None,
    mysql_database: str | None = None,
    mysql_defaults_file: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Run maintenance on MySQL tables (OPTIMIZE/ANALYZE/CHECK/REPAIR).

    Args:
        ctx (OpContext): Execution context for the operation.
        database (str): Database holding the tables.
        tables (list[str]): Table names to maintain, in one statement.
        action (str, optional): Maintenance verb: optimize, analyze,
            check or repair. Defaults to "optimize".
        mysql_user (str | None, optional): User to connect as.
            Defaults to None.
        mysql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        mysql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        mysql_database (str | None, optional): Database to open the
            connection with. Defaults to None.
        mysql_defaults_file (str | None, optional): Host-side option
            file rendered as --defaults-extra-file. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The maintenance command, always (the operation is
            stateless).

    **Raises:**

    ```python
    ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_optimize.py``.
    """

    # reemote5-original (there is no other maintenance operation).
    # Stateless like dump/load: maintenance is a periodic
    # action, not a desired state, so there is nothing to compare a
    # fact against. InnoDB maps OPTIMIZE to a recreate+analyze and
    # REPAIR is unsupported (the server reports a note, not an error).
    # Needs SELECT plus INSERT (and for repair, the file-level rights
    # the engine needs): flag the request ``sudo=True`` for root
    # socket auth. ``**kwargs`` absorbs any further global arguments.
    if action not in ("optimize", "analyze", "check", "repair"):
        raise ValueError(
            "mysql.optimize action must be optimize, analyze, check or repair"
        )
    if not tables:
        raise ValueError("mysql.optimize requires at least one table")
    ctx.changed = True
    ctx.reason = f"mysql {action} will run on {len(tables)} table(s) in {database}"
    yield MysqlOptimizeCommand(
        database=database,
        tables=tuple(tables),
        action=action,
        mysql_user=mysql_user,
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_database=mysql_database,
        mysql_defaults_file=mysql_defaults_file,
    )


@dataclass(frozen=True)
class MysqlPluginCommand(_MysqlCommand):
    """``mysql -Be 'INSTALL/UNINSTALL PLUGIN ...'``: manage one plugin.

    ``present=True`` renders INSTALL PLUGIN <name> SONAME '<library>';
    ``present=False`` renders UNINSTALL PLUGIN <name>.
    """

    plugin: str
    soname: str | None = None
    present: bool = True

    def shell(self) -> str:
        name = _quote_ident(self.plugin)
        if not self.present:
            return self._sql_shell(f"UNINSTALL PLUGIN {name}")
        return self._sql_shell(
            f"INSTALL PLUGIN {name} SONAME {_quote_literal(self.soname or '')}"
        )


async def plugin(
    ctx: OpContext,
    plugin: str,
    present: bool = True,
    soname: str | None = None,
    mysql_user: str | None = None,
    mysql_host: str | None = None,
    mysql_port: int | None = None,
    mysql_database: str | None = None,
    mysql_defaults_file: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Install or uninstall a MySQL server plugin.

    Args:
        ctx (OpContext): Execution context for the operation.
        plugin (str): Name of the plugin to manage, e.g.
            ``validate_password``.
        present (bool, optional): Whether the plugin should be
            installed. Defaults to True.
        soname (str | None, optional): Shared-library file name the
            plugin loads from, e.g. ``validate_password.so``; required
            when present is True. Defaults to None.
        mysql_user (str | None, optional): User to connect as.
            Defaults to None.
        mysql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        mysql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        mysql_database (str | None, optional): Database to open the
            connection with. Defaults to None.
        mysql_defaults_file (str | None, optional): Host-side option
            file rendered as --defaults-extra-file. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated MySQL plugin commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, OperationError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_plugin.py``.
    """

    # reemote5-original (there is no other plugin operation). Idempotent
    # against MysqlPlugins keyed on the plugin name. An installed
    # plugin loaded from a DIFFERENT library raises OperationError
    # (uninstall/reinstall is too disruptive to infer), and uninstalling
    # a BUILT-IN plugin (library None) raises OperationError in prepare
    # rather than failing server-side mid-apply -- built-ins can only
    # be disabled through configuration. INSTALL PLUGIN persists in
    # mysql.plugin across restarts. Needs INSERT/DELETE on mysql.plugin
    # (SUPER): flag the request ``sudo=True`` for root socket auth.
    # ``**kwargs`` absorbs any further global arguments.
    if present and not soname:
        raise ValueError("mysql.plugin requires soname when present=True")
    conn = {
        "mysql_user": mysql_user,
        "mysql_host": mysql_host,
        "mysql_port": mysql_port,
        "mysql_database": mysql_database,
        "mysql_defaults_file": mysql_defaults_file,
    }
    plugins = await ctx.facts.get(MysqlPlugins, **conn)
    current = next((e for e in plugins if e["name"] == plugin), None)

    if not present:
        if current is None:
            ctx.reason = f"mysql plugin {plugin} is not installed"
            return
        if current["library"] is None:
            raise OperationError(
                f"mysql plugin {plugin} is built-in and cannot be uninstalled; "
                "disable it through server configuration instead"
            )
        ctx.changed = True
        ctx.reason = f"mysql plugin {plugin} will be uninstalled"
        yield MysqlPluginCommand(plugin=plugin, present=False, **conn)
        return

    if current is None:
        ctx.changed = True
        ctx.reason = f"mysql plugin {plugin} will be installed"
        yield MysqlPluginCommand(plugin=plugin, soname=soname, **conn)
        return

    if current["library"] != soname:
        raise OperationError(
            f"mysql plugin {plugin} is already installed from "
            f"{current['library']}; uninstall it first to change the library"
        )
    ctx.reason = f"mysql plugin {plugin} is already installed"


@dataclass(frozen=True)
class MysqlPrivilegesCommand(_MysqlCommand):
    """``mysql -Be 'GRANT/REVOKE ... ON db.table TO/FROM ...'``.

    One privilege statement: the sorted privilege list, the already
    quoted ``database``/``table`` target (``*`` passes through bare)
    and the account; ``with_statement`` appends a WITH clause (the
    GRANT OPTION special case).
    """

    action: str
    privileges: tuple[str, ...]
    database: str
    table: str
    user: str
    user_hostname: str
    with_statement: str | None = None

    def shell(self) -> str:
        target = "TO" if self.action == "GRANT" else "FROM"
        sql = (
            f"{self.action} {', '.join(self.privileges)} "
            f"ON {self.database}.{self.table} "
            f"{target} {_account(self.user, self.user_hostname)}"
        )
        if self.with_statement:
            sql += f" WITH {self.with_statement}"
        return self._sql_shell(sql)


async def privileges(
    ctx: OpContext,
    user: str,
    privileges: str | list[str] | set[str],
    user_hostname: str = "localhost",
    database: str = "*",
    table: str = "*",
    flush: bool = True,
    with_grant_option: bool = False,
    mysql_user: str | None = None,
    mysql_host: str | None = None,
    mysql_port: int | None = None,
    mysql_database: str | None = None,
    mysql_defaults_file: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add or remove MySQL privileges for a user on a target.

    Args:
        ctx (OpContext): Execution context for the operation.
        user (str): Name of the user to manage privileges for.
        privileges (str | list[str] | set[str]): The exact privileges
            the user should have on the target; anything else currently
            granted on it is revoked.
        user_hostname (str, optional): Hostname part of the user.
            Defaults to "localhost".
        database (str, optional): Database to scope the privileges to;
            all databases when "*". Defaults to "*".
        table (str, optional): Table to scope the privileges to; all
            tables when "*" (a named table requires a named database).
            Defaults to "*".
        flush (bool, optional): Follow any change with FLUSH
            PRIVILEGES. Defaults to True.
        with_grant_option (bool, optional): Include GRANT OPTION in
            the desired privileges. Defaults to False.
        mysql_user (str | None, optional): User to connect as.
            Defaults to None.
        mysql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        mysql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        mysql_database (str | None, optional): Database to open the
            connection with. Defaults to None.
        mysql_defaults_file (str | None, optional): Host-side option
            file rendered as --defaults-extra-file. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated MySQL privilege commands.

    **Raises:**

    ```python
    HostConnectionError, OperationError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_privileges.py``.
    """

    # ``mysql.privileges``: the desired set is diffed
    # against MysqlUserGrants for the exact db.table target, extras are
    # revoked and missing ones granted (sorted, so both phases render
    # identically), USAGE is never revoked when something is granted,
    # and the ALL+GRANT OPTION pair uses the two-step REVOKE / WITH
    # GRANT OPTION special-cased forms. Role grants captured by
    # the fact are membership, not privileges, and are excluded from
    # the diff. ``mysql_password`` is deliberately dropped. Needs GRANT
    # OPTION on the target: flag the request ``sudo=True`` for root
    # socket auth. ``**kwargs`` absorbs any further global arguments.
    conn = {
        "mysql_user": mysql_user,
        "mysql_host": mysql_host,
        "mysql_port": mysql_port,
        "mysql_database": mysql_database,
        "mysql_defaults_file": mysql_defaults_file,
    }
    commands, reasons = await _privilege_plan(
        ctx,
        user=user,
        privileges=privileges,
        user_hostname=user_hostname,
        database=database,
        table=table,
        flush=flush,
        with_grant_option=with_grant_option,
        conn=conn,
    )
    ctx.reason = "; ".join(reasons)
    for command in commands:
        yield command


@dataclass(frozen=True)
class MysqlReplicaCommand(_MysqlCommand):
    """``mysql -Be 'START/STOP/RESET REPLICA ...'``: replication control.

    ``reset=True`` renders the two-statement STOP REPLICA; RESET
    REPLICA ALL form (channel-independent); otherwise START/STOP
    REPLICA with the optional FOR CHANNEL clause.
    """

    running: bool = True
    reset: bool = False
    channel: str | None = None

    def shell(self) -> str:
        if self.reset:
            return self._sql_shell("STOP REPLICA; RESET REPLICA ALL")
        verb = "START" if self.running else "STOP"
        sql = f"{verb} REPLICA"
        if self.channel:
            sql += f" FOR CHANNEL {_quote_literal(self.channel)}"
        return self._sql_shell(sql)


async def replica(
    ctx: OpContext,
    running: bool = True,
    reset: bool = False,
    channel: str | None = None,
    mysql_user: str | None = None,
    mysql_host: str | None = None,
    mysql_port: int | None = None,
    mysql_database: str | None = None,
    mysql_defaults_file: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Start, stop or reset MySQL replication on a replica channel.

    Args:
        ctx (OpContext): Execution context for the operation.
        running (bool, optional): Whether the replica threads should
            be running. Defaults to True.
        reset (bool, optional): Stop replication and discard ALL
            channel configuration (RESET REPLICA ALL), always yielded;
            overrides running and channel. Defaults to False.
        channel (str | None, optional): The replication channel to
            address; the default (unnamed) channel when None.
            Defaults to None.
        mysql_user (str | None, optional): User to connect as.
            Defaults to None.
        mysql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        mysql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        mysql_database (str | None, optional): Database to open the
            connection with. Defaults to None.
        mysql_defaults_file (str | None, optional): Host-side option
            file rendered as --defaults-extra-file. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated MySQL replication commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, OperationError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_replica.py``.
    """

    # reemote5-original (there is no other replication operation), scoped
    # by the developer-approved replication decision: START/STOP/RESET
    # only. CHANGE REPLICATION SOURCE TO is deliberately dropped --
    # it requires SOURCE_PASSWORD inline and MySQL has no passfile for
    # replication, which clashes with the namespace-wide no-passwords
    # rule; configure the source through mysql.sql or server tooling.
    # Idempotent against MysqlReplication: both threads Yes means
    # running, both No means stopped; a channel that is not configured
    # raises OperationError in prepare (there is nothing to start).
    # reset is stateless (STOP REPLICA; RESET REPLICA ALL). MySQL
    # 8.0.22+ syntax only. Needs REPLICATION_SLAVE_ADMIN: flag the
    # request ``sudo=True`` for root socket auth. ``**kwargs`` absorbs
    # any further global arguments.
    conn = {
        "mysql_user": mysql_user,
        "mysql_host": mysql_host,
        "mysql_port": mysql_port,
        "mysql_database": mysql_database,
        "mysql_defaults_file": mysql_defaults_file,
    }
    if reset:
        ctx.changed = True
        ctx.reason = "mysql replication will be stopped and reset"
        yield MysqlReplicaCommand(reset=True, **conn)
        return

    channels = await ctx.facts.get(MysqlReplication, **conn)
    current = next(
        (e for e in channels if e["channel"] == (channel or "")), None
    )
    if current is None:
        label = f"channel {channel}" if channel else "the default channel"
        raise OperationError(
            f"mysql replication is not configured on {label}; "
            "configure the source before starting the replica"
        )
    threads_running = (
        current["io_running"] == "Yes" and current["sql_running"] == "Yes"
    )
    threads_stopped = (
        current["io_running"] == "No" and current["sql_running"] == "No"
    )
    if running:
        if threads_running:
            ctx.reason = "mysql replica threads are already running"
            return
        ctx.changed = True
        ctx.reason = "mysql replica threads will be started"
        yield MysqlReplicaCommand(running=True, channel=channel, **conn)
    else:
        if threads_stopped:
            ctx.reason = "mysql replica threads are already stopped"
            return
        ctx.changed = True
        ctx.reason = "mysql replica threads will be stopped"
        yield MysqlReplicaCommand(running=False, channel=channel, **conn)


@dataclass(frozen=True)
class MysqlRoleCommand(_MysqlCommand):
    """``mysql -Be 'CREATE/DROP ROLE ...'`` or ``'GRANT/REVOKE role ...'``.

    Without ``member`` the descriptor manages the role account itself
    (CREATE/DROP ROLE); with ``member`` it grants/revokes the role
    to/from that account (``member_present`` picks the direction).
    """

    role: str
    role_hostname: str = "%"
    present: bool = True
    member: str | None = None
    member_hostname: str = "localhost"
    member_present: bool = True

    def shell(self) -> str:
        role = f"{_quote_ident(self.role)}@{_quote_ident(self.role_hostname)}"
        if self.member is not None:
            member = _account(self.member, self.member_hostname)
            if self.member_present:
                return self._sql_shell(f"GRANT {role} TO {member}")
            return self._sql_shell(f"REVOKE {role} FROM {member}")
        verb = "CREATE" if self.present else "DROP"
        return self._sql_shell(f"{verb} ROLE {role}")


async def role(
    ctx: OpContext,
    role: str,
    present: bool = True,
    role_hostname: str = "%",
    members: list[str] | None = None,
    mysql_user: str | None = None,
    mysql_host: str | None = None,
    mysql_port: int | None = None,
    mysql_database: str | None = None,
    mysql_defaults_file: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Create or drop a MySQL role and manage its memberships.

    Args:
        ctx (OpContext): Execution context for the operation.
        role (str): Name of the role to manage (MySQL 8.0+).
        present (bool, optional): Whether the role should exist;
            dropping a role revokes all its memberships server-side.
            Defaults to True.
        role_hostname (str, optional): Hostname part of the role
            account. Defaults to "%".
        members (list[str] | None, optional): Accounts the role should
            be granted to, each as ``user@host``; memberships are only
            added, never revoked (list every member the role should
            keep). Defaults to None.
        mysql_user (str | None, optional): User to connect as.
            Defaults to None.
        mysql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        mysql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        mysql_database (str | None, optional): Database to open the
            connection with. Defaults to None.
        mysql_defaults_file (str | None, optional): Host-side option
            file rendered as --defaults-extra-file. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated MySQL role commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_role.py``.
    """

    # reemote5-original (there is no other role operation). Existence is
    # idempotent against MysqlUsers keyed role@role_hostname; membership
    # is idempotent against the role-grant extension of MysqlUserGrants
    # (the backticked role@host key with the "ROLE" privilege). Members
    # are processed in sorted order so both phases render identically;
    # a member of a freshly created role needs no fact read (the role
    # cannot have been granted yet). Dropping ignores ``members`` --
    # DROP ROLE revokes every membership server-side. Roles are
    # accounts, so they carry no password here either (the credentials
    # decision). Needs CREATE ROLE / CREATE USER: flag the request
    # ``sudo=True`` for root socket auth. ``**kwargs`` absorbs any
    # global arguments.
    conn = {
        "mysql_user": mysql_user,
        "mysql_host": mysql_host,
        "mysql_port": mysql_port,
        "mysql_database": mysql_database,
        "mysql_defaults_file": mysql_defaults_file,
    }
    parsed_members: list[tuple[str, str]] = []
    for member in members or []:
        member_user, sep, member_host = member.rpartition("@")
        if not sep or not member_user or not member_host:
            raise ValueError(
                f"mysql.role members must be user@host accounts, got {member!r}"
            )
        parsed_members.append((member_user, member_host))

    users = await ctx.facts.get(MysqlUsers, **conn)
    role_key = f"{role}@{role_hostname}"
    reasons: list[str] = []

    if not present:
        if role_key in users:
            ctx.changed = True
            reasons.append(f"mysql role {role_key} will be dropped")
            yield MysqlRoleCommand(
                role=role, role_hostname=role_hostname, present=False, **conn
            )
        else:
            reasons.append(f"mysql role {role_key} does not exist")
        ctx.reason = "; ".join(reasons)
        return

    created = role_key not in users
    if created:
        ctx.changed = True
        reasons.append(f"mysql role {role_key} will be created")
        yield MysqlRoleCommand(role=role, role_hostname=role_hostname, **conn)
    else:
        reasons.append(f"mysql role {role_key} already exists")

    grant_key = f"{_quote_ident(role)}@{_quote_ident(role_hostname)}"
    for member_user, member_host in sorted(parsed_members):
        if not created:
            grants = await ctx.facts.get(
                MysqlUserGrants, member_user, member_host, **conn
            )
            if grant_key in grants:
                continue
        ctx.changed = True
        reasons.append(f"mysql role {role_key} will be granted to {member_user}@{member_host}")
        yield MysqlRoleCommand(
            role=role,
            role_hostname=role_hostname,
            member=member_user,
            member_hostname=member_host,
            **conn,
        )
    ctx.reason = "; ".join(reasons)


@dataclass(frozen=True)
class MysqlSqlCommand(_MysqlCommand):
    """``mysql -Be '<sql>'``: run arbitrary SQL statement(s).

    The stateless mutation ``mysql.sql`` yields, also reused by
    ``mysql.privileges`` for its FLUSH PRIVILEGES follow-up.
    """

    sql: str

    def shell(self) -> str:
        return self._sql_shell(self.sql)


async def sql(
    ctx: OpContext,
    sql: str,
    database: str | None = None,
    mysql_user: str | None = None,
    mysql_host: str | None = None,
    mysql_port: int | None = None,
    mysql_defaults_file: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Execute arbitrary SQL against MySQL.

    Args:
        ctx (OpContext): Execution context for the operation.
        sql (str): SQL command(s) to execute.
        database (str | None, optional): Database to open the
            connection with. Defaults to None.
        mysql_user (str | None, optional): User to connect as.
            Defaults to None.
        mysql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        mysql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        mysql_defaults_file (str | None, optional): Host-side option
            file rendered as --defaults-extra-file. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The SQL command, always (the operation is stateless).

    **Raises:**

    ```python
    None
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_sql.py``.
    """

    # ``mysql.sql``: non-idempotent, always yields
    # (it is non-idempotent by design) -- the escape hatch for
    # anything the typed operations do not cover (CHANGE REPLICATION
    # SOURCE TO, schema migrations, ...). The SQL text appears verbatim
    # in the plan/apply log, so never embed credentials in it
    # (``mysql_password`` is deliberately dropped namespace-wide).
    # ``**kwargs`` absorbs any further global arguments.
    ctx.changed = True
    ctx.reason = "mysql SQL will be executed"
    yield MysqlSqlCommand(
        sql=sql,
        mysql_user=mysql_user,
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_database=database,
        mysql_defaults_file=mysql_defaults_file,
    )


@dataclass(frozen=True)
class MysqlTableCommand(_MysqlCommand):
    """``mysql -Be 'CREATE/DROP TABLE ...'``: manage one table.

    The create form renders CREATE TABLE with the verbatim column
    definition list and the optional ENGINE / DEFAULT CHARSET /
    COLLATE / COMMENT clauses; ``present=False`` renders DROP TABLE.
    """

    database: str
    table: str
    columns: tuple[str, ...] = ()
    engine: str | None = None
    charset: str | None = None
    collate: str | None = None
    comment: str | None = None
    present: bool = True

    def shell(self) -> str:
        target = f"{_quote_ident(self.database)}.{_quote_ident(self.table)}"
        if not self.present:
            return self._sql_shell(f"DROP TABLE {target}")
        bits = [f"CREATE TABLE {target} ({', '.join(self.columns)})"]
        if self.engine:
            bits.append(f"ENGINE = {_quote_ident(self.engine)}")
        if self.charset:
            bits.append(f"DEFAULT CHARSET = {_quote_ident(self.charset)}")
        if self.collate:
            bits.append(f"COLLATE = {_quote_ident(self.collate)}")
        if self.comment:
            bits.append(f"COMMENT = {_quote_literal(self.comment)}")
        return self._sql_shell(" ".join(bits))


async def table(
    ctx: OpContext,
    database: str,
    table: str,
    present: bool = True,
    columns: list[str] | None = None,
    engine: str | None = None,
    charset: str | None = None,
    collate: str | None = None,
    comment: str | None = None,
    mysql_user: str | None = None,
    mysql_host: str | None = None,
    mysql_port: int | None = None,
    mysql_database: str | None = None,
    mysql_defaults_file: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Create or drop a MySQL table.

    Args:
        ctx (OpContext): Execution context for the operation.
        database (str): Database to create the table in.
        table (str): Name of the table to manage.
        present (bool, optional): Whether the table should exist.
            Defaults to True.
        columns (list[str] | None, optional): Column (and constraint)
            definitions as SQL text, e.g. ``id INT PRIMARY KEY
            AUTO_INCREMENT``; required when present is True.
            Defaults to None.
        engine (str | None, optional): Storage engine, e.g. InnoDB;
            creation-only. Defaults to None.
        charset (str | None, optional): Default character set;
            creation-only. Defaults to None.
        collate (str | None, optional): Default collation;
            creation-only. Defaults to None.
        comment (str | None, optional): Table comment; creation-only.
            Defaults to None.
        mysql_user (str | None, optional): User to connect as.
            Defaults to None.
        mysql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        mysql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        mysql_database (str | None, optional): Database to open the
            connection with. Defaults to None.
        mysql_defaults_file (str | None, optional): Host-side option
            file rendered as --defaults-extra-file. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated MySQL table commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_table.py``.
    """

    # reemote5-original (there is no other table operation). Idempotent
    # against MysqlTables on EXISTENCE only: the structure of an
    # existing table is never altered or diffed (column definitions are
    # free SQL text, and inferring ALTERs from them would be a schema
    # migration engine) -- MysqlColumns/MysqlIndexes provide the
    # visibility, mysql.sql the migration escape hatch. ``columns``
    # entries are SQL rendered verbatim, never shell (the whole
    # statement is one shlex-quoted mysql argument). Needs CREATE/DROP
    # on the database: flag the request ``sudo=True`` for root socket
    # auth. ``**kwargs`` absorbs any further global arguments.
    if present and not columns:
        raise ValueError("mysql.table requires columns when present=True")
    conn = {
        "mysql_user": mysql_user,
        "mysql_host": mysql_host,
        "mysql_port": mysql_port,
        "mysql_database": mysql_database,
        "mysql_defaults_file": mysql_defaults_file,
    }
    tables = await ctx.facts.get(MysqlTables, database, **conn)
    exists = any(entry["name"] == table for entry in tables)

    if not present:
        if exists:
            ctx.changed = True
            ctx.reason = f"mysql table {database}.{table} will be dropped"
            yield MysqlTableCommand(
                database=database, table=table, present=False, **conn
            )
        else:
            ctx.reason = f"mysql table {database}.{table} does not exist"
        return

    if exists:
        ctx.reason = (
            f"mysql table {database}.{table} already exists "
            "(structure is not compared)"
        )
        return

    ctx.changed = True
    ctx.reason = f"mysql table {database}.{table} will be created"
    yield MysqlTableCommand(
        database=database,
        table=table,
        columns=tuple(columns or ()),
        engine=engine,
        charset=charset,
        collate=collate,
        comment=comment,
        **conn,
    )


@dataclass(frozen=True)
class MysqlUserCommand(_MysqlCommand):
    """``mysql -Be 'CREATE/ALTER/DROP USER ...'``: manage one account.

    One present/alter-flagged descriptor for all three directions:
    ``present=False`` renders DROP USER, ``alter=True`` renders ALTER
    USER with only the changed clauses, and the create form renders
    CREATE USER with the clause order IDENTIFIED WITH, REQUIRE, WITH
    resource limits (the established order, with IDENTIFIED BY replaced by
    the passwordless IDENTIFIED WITH plugin form).
    """

    user: str
    user_hostname: str = "localhost"
    present: bool = True
    alter: bool = False
    auth_plugin: str | None = None
    require: str | None = None
    require_cipher: str | None = None
    require_issuer: str | None = None
    require_subject: str | None = None
    max_connections: int | None = None
    max_queries_per_hour: int | None = None
    max_updates_per_hour: int | None = None
    max_connections_per_hour: int | None = None

    def shell(self) -> str:
        account = _account(self.user, self.user_hostname)
        if not self.present:
            return self._sql_shell(f"DROP USER {account}")
        verb = "ALTER" if self.alter else "CREATE"
        bits = [f"{verb} USER {account}"]
        if self.auth_plugin:
            bits.append(f"IDENTIFIED WITH {self.auth_plugin}")
        if self.require == "SSL":
            bits.append("REQUIRE SSL")
        if self.require == "X509":
            require_bits = []
            if self.require_cipher:
                require_bits.append(f"CIPHER {_quote_literal(self.require_cipher)}")
            if self.require_issuer:
                require_bits.append(f"ISSUER {_quote_literal(self.require_issuer)}")
            if self.require_subject:
                require_bits.append(f"SUBJECT {_quote_literal(self.require_subject)}")
            if not require_bits:
                require_bits.append("X509")
            bits.append("REQUIRE")
            bits.extend(require_bits)
        resource_bits = []
        if self.max_connections:
            resource_bits.append(f"MAX_USER_CONNECTIONS {self.max_connections}")
        if self.max_queries_per_hour:
            resource_bits.append(f"MAX_QUERIES_PER_HOUR {self.max_queries_per_hour}")
        if self.max_updates_per_hour:
            resource_bits.append(f"MAX_UPDATES_PER_HOUR {self.max_updates_per_hour}")
        if self.max_connections_per_hour:
            resource_bits.append(
                f"MAX_CONNECTIONS_PER_HOUR {self.max_connections_per_hour}"
            )
        if resource_bits:
            bits.append("WITH")
            bits.append(" ".join(resource_bits))
        return self._sql_shell(" ".join(bits))


async def user(
    ctx: OpContext,
    user: str,
    present: bool = True,
    user_hostname: str = "localhost",
    auth_plugin: str | None = None,
    privileges: str | list[str] | None = None,
    require: str | None = None,
    require_cipher: str | None = None,
    require_issuer: str | None = None,
    require_subject: str | None = None,
    max_connections: int | None = None,
    max_queries_per_hour: int | None = None,
    max_updates_per_hour: int | None = None,
    max_connections_per_hour: int | None = None,
    mysql_user: str | None = None,
    mysql_host: str | None = None,
    mysql_port: int | None = None,
    mysql_database: str | None = None,
    mysql_defaults_file: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add, alter or remove a MySQL user account.

    Args:
        ctx (OpContext): Execution context for the operation.
        user (str): Name of the user to manage.
        present (bool, optional): Whether the user should exist.
            Defaults to True.
        user_hostname (str, optional): Hostname part of the account;
            changing it addresses a different user@host account.
            Defaults to "localhost".
        auth_plugin (str | None, optional): Authentication plugin,
            e.g. ``auth_socket``; applied at creation and altered on an
            existing account when it differs. Replaces the
            password option (the credentials decision). Defaults to
            None.
        privileges (str | list[str] | None, optional): Global
            privileges the user should have, delegated to the
            mysql.privileges core. Defaults to None.
        require (str | None, optional): TLS requirement: "SSL" or
            "X509". Defaults to None.
        require_cipher (str | None, optional): Required TLS cipher
            (X509 only). Defaults to None.
        require_issuer (str | None, optional): Required certificate
            issuer (X509 only). Defaults to None.
        require_subject (str | None, optional): Required certificate
            subject (X509 only). Defaults to None.
        max_connections (int | None, optional): MAX_USER_CONNECTIONS
            limit. Defaults to None.
        max_queries_per_hour (int | None, optional):
            MAX_QUERIES_PER_HOUR limit. Defaults to None.
        max_updates_per_hour (int | None, optional):
            MAX_UPDATES_PER_HOUR limit. Defaults to None.
        max_connections_per_hour (int | None, optional):
            MAX_CONNECTIONS_PER_HOUR limit. Defaults to None.
        mysql_user (str | None, optional): User to connect as.
            Defaults to None.
        mysql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        mysql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        mysql_database (str | None, optional): Database to open the
            connection with. Defaults to None.
        mysql_defaults_file (str | None, optional): Host-side option
            file rendered as --defaults-extra-file. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated MySQL user commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, OperationError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_user.py``.
    """

    # ``mysql.user``, minus the credential: the baseline
    # ``password`` option (IDENTIFIED BY, applied only at creation
    # because the hash cannot be compared) is deliberately dropped and
    # replaced by ``auth_plugin`` (IDENTIFIED WITH, e.g. auth_socket),
    # which IS comparable against the fact's plugin column and so also
    # alters on drift. The REQUIRE SSL/X509 validation and alter
    # comparisons, the WITH resource-limit comparisons and the global
    # ``privileges`` delegation follow the fact keys (ssl_type/ssl_cipher/
    # x509_issuer/x509_subject and max_user_connections/max_questions/
    # max_updates/max_connections fact keys). Needs CREATE USER (and
    # GRANT OPTION for the privileges bundle): flag the request
    # ``sudo=True`` for root socket auth. ``**kwargs`` absorbs any
    # global arguments.
    if require and require not in ("SSL", "X509"):
        raise OperationError('Invalid `require` value, must be: "SSL" or "X509"')
    if require != "X509":
        if require_cipher:
            raise OperationError('Cannot set `require_cipher` if `require` is not "X509"')
        if require_issuer:
            raise OperationError('Cannot set `require_issuer` if `require` is not "X509"')
        if require_subject:
            raise OperationError('Cannot set `require_subject` if `require` is not "X509"')

    conn = {
        "mysql_user": mysql_user,
        "mysql_host": mysql_host,
        "mysql_port": mysql_port,
        "mysql_database": mysql_database,
        "mysql_defaults_file": mysql_defaults_file,
    }
    current_users = await ctx.facts.get(MysqlUsers, **conn)
    user_host = f"{user}@{user_hostname}"
    reasons: list[str] = []

    if not present:
        if user_host in current_users:
            ctx.changed = True
            reasons.append(f"mysql user {user_host} will be dropped")
            yield MysqlUserCommand(
                user=user, user_hostname=user_hostname, present=False, **conn
            )
        else:
            reasons.append(f"mysql user {user_host} does not exist")
        ctx.reason = "; ".join(reasons)
        return

    if user_host not in current_users:
        ctx.changed = True
        reasons.append(f"mysql user {user_host} will be created")
        yield MysqlUserCommand(
            user=user,
            user_hostname=user_hostname,
            auth_plugin=auth_plugin,
            require=require,
            require_cipher=require_cipher,
            require_issuer=require_issuer,
            require_subject=require_subject,
            max_connections=max_connections,
            max_queries_per_hour=max_queries_per_hour,
            max_updates_per_hour=max_updates_per_hour,
            max_connections_per_hour=max_connections_per_hour,
            **conn,
        )
    else:
        entry = current_users[user_host]
        alter_plugin = (
            auth_plugin
            if auth_plugin and entry.get("plugin") != auth_plugin
            else None
        )
        alter_require: str | None = None
        alter_cipher: str | None = None
        alter_issuer: str | None = None
        alter_subject: str | None = None
        if require == "SSL" and entry.get("ssl_type") != "ANY":
            alter_require = "SSL"
        if require == "X509":
            if require_cipher and entry.get("ssl_cipher") != require_cipher:
                alter_cipher = require_cipher
            if require_issuer and entry.get("x509_issuer") != require_issuer:
                alter_issuer = require_issuer
            if require_subject and entry.get("x509_subject") != require_subject:
                alter_subject = require_subject
            if not (alter_cipher or alter_issuer or alter_subject):
                if entry.get("ssl_type") != "X509":
                    alter_require = "X509"
            else:
                alter_require = "X509"
        alter_max_connections = (
            max_connections
            if max_connections
            and entry.get("max_user_connections") != max_connections
            else None
        )
        alter_max_queries = (
            max_queries_per_hour
            if max_queries_per_hour
            and entry.get("max_questions") != max_queries_per_hour
            else None
        )
        alter_max_updates = (
            max_updates_per_hour
            if max_updates_per_hour
            and entry.get("max_updates") != max_updates_per_hour
            else None
        )
        alter_max_connections_per_hour = (
            max_connections_per_hour
            if max_connections_per_hour
            and entry.get("max_connections") != max_connections_per_hour
            else None
        )
        if any(
            value is not None
            for value in (
                alter_plugin,
                alter_require,
                alter_max_connections,
                alter_max_queries,
                alter_max_updates,
                alter_max_connections_per_hour,
            )
        ):
            ctx.changed = True
            reasons.append(f"mysql user {user_host} will be altered")
            yield MysqlUserCommand(
                user=user,
                user_hostname=user_hostname,
                alter=True,
                auth_plugin=alter_plugin,
                require=alter_require,
                require_cipher=alter_cipher,
                require_issuer=alter_issuer,
                require_subject=alter_subject,
                max_connections=alter_max_connections,
                max_queries_per_hour=alter_max_queries,
                max_updates_per_hour=alter_max_updates,
                max_connections_per_hour=alter_max_connections_per_hour,
                **conn,
            )
        else:
            reasons.append(f"mysql user {user_host} already exists")

    # Ensure any global privileges (the shared privilege bundle).
    if privileges:
        commands, grant_reasons = await _privilege_plan(
            ctx,
            user=user,
            privileges=privileges,
            user_hostname=user_hostname,
            database="*",
            table="*",
            flush=True,
            with_grant_option=False,
            conn=conn,
        )
        reasons.extend(grant_reasons)
        for command in commands:
            yield command
    ctx.reason = "; ".join(reasons)


@dataclass(frozen=True)
class MysqlVariableCommand(_MysqlCommand):
    """``mysql -Be 'SET GLOBAL/PERSIST ...'`` or ``'RESET PERSIST ...'``.

    ``present=True`` renders SET GLOBAL (or SET PERSIST when ``persist``
    is set) with a bare numeric value or a quoted literal;
    ``present=False`` renders RESET PERSIST, removing the persisted
    override from mysqld-auto.cnf.
    """

    name: str
    value: str | None = None
    persist: bool = False
    present: bool = True

    def shell(self) -> str:
        name = _quote_ident(self.name)
        if not self.present:
            return self._sql_shell(f"RESET PERSIST {name}")
        scope = "PERSIST" if self.persist else "GLOBAL"
        text = self.value or ""
        rendered = text if text.lstrip("-").isdigit() else _quote_literal(text)
        return self._sql_shell(f"SET {scope} {name} = {rendered}")


async def variable(
    ctx: OpContext,
    variable: str,
    value: str | int | None = None,
    present: bool = True,
    persist: bool = False,
    mysql_user: str | None = None,
    mysql_host: str | None = None,
    mysql_port: int | None = None,
    mysql_database: str | None = None,
    mysql_defaults_file: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Set or reset a MySQL global system variable.

    Args:
        ctx (OpContext): Execution context for the operation.
        variable (str): The system variable name, e.g. ``max_connections``.
        value (str | int | None, optional): The desired value in the
            server's own text rendering; required when present is True.
            Defaults to None.
        present (bool, optional): Set the value when True; remove the
            persisted override (RESET PERSIST) when False. Defaults
            to True.
        persist (bool, optional): Use SET PERSIST (MySQL 8.0+), which
            applies at runtime AND survives restarts via
            mysqld-auto.cnf, instead of the runtime-only SET GLOBAL.
            Defaults to False.
        mysql_user (str | None, optional): User to connect as.
            Defaults to None.
        mysql_host (str | None, optional): Server host to connect to.
            Defaults to None.
        mysql_port (int | None, optional): Server port to connect to.
            Defaults to None.
        mysql_database (str | None, optional): Database to open the
            connection with. Defaults to None.
        mysql_defaults_file (str | None, optional): Host-side option
            file rendered as --defaults-extra-file. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated MySQL variable commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/mysql/mysql_variable.py``.
    """

    # reemote5-original (there is no other configuration operation), the
    # postgresql.setting shape. The option is ``variable``, not ``name``:
    # ``name`` is a meta-parameter consumed by op() and never reaches the
    # operation (the postgresql.setting ``setting`` precedent). Idempotent
    # against MysqlVariables: the value is compared as TEXT in the
    # server's own rendering (no unit normalization -- pass 262144
    # rather than "256K" for a byte-sized variable, and "1"/"0" for
    # booleans). An unknown variable name
    # raises ValueError in prepare (the fact lists every variable the
    # server knows). present=False renders RESET PERSIST and is
    # stateless: the persisted-override set (performance_schema.
    # persisted_variables) is not modelled by a fact, and the runtime
    # value keeps until restart either way. Read-only variables error
    # server-side at apply time. Needs SYSTEM_VARIABLES_ADMIN (and
    # PERSIST_RO_VARIABLES_ADMIN for persisted read-only ones): flag
    # the request ``sudo=True`` for root socket auth. ``**kwargs``
    # absorbs any further global arguments.
    if present and value is None:
        raise ValueError("mysql.variable requires value when present=True")
    conn = {
        "mysql_user": mysql_user,
        "mysql_host": mysql_host,
        "mysql_port": mysql_port,
        "mysql_database": mysql_database,
        "mysql_defaults_file": mysql_defaults_file,
    }
    if not present:
        ctx.changed = True
        ctx.reason = f"mysql variable {variable} persisted override will be reset"
        yield MysqlVariableCommand(name=variable, present=False, **conn)
        return

    variables = await ctx.facts.get(MysqlVariables, **conn)
    if variable not in variables:
        raise ValueError(f"unknown mysql system variable {variable}")
    text = str(value)
    if variables[variable] == text:
        ctx.reason = f"mysql variable {variable} is already {text}"
        return
    ctx.changed = True
    ctx.reason = f"mysql variable {variable} will be set to {text}"
    yield MysqlVariableCommand(name=variable, value=text, persist=persist, **conn)


__all__ = [
    "MysqlBinaryLog",
    "MysqlBinaryLogs",
    "MysqlColumn",
    "MysqlColumns",
    "MysqlConnection",
    "MysqlConnections",
    "MysqlDatabase",
    "MysqlDatabaseCommand",
    "MysqlDatabases",
    "MysqlDumpCommand",
    "MysqlEngine",
    "MysqlEngines",
    "MysqlIndex",
    "MysqlIndexCommand",
    "MysqlIndexes",
    "MysqlKillCommand",
    "MysqlLoadCommand",
    "MysqlOptimizeCommand",
    "MysqlPlugin",
    "MysqlPluginCommand",
    "MysqlPlugins",
    "MysqlPrivilegesCommand",
    "MysqlReplicaCommand",
    "MysqlReplicaStatus",
    "MysqlReplication",
    "MysqlRoleCommand",
    "MysqlSqlCommand",
    "MysqlStatus",
    "MysqlTable",
    "MysqlTableCommand",
    "MysqlTables",
    "MysqlUser",
    "MysqlUserCommand",
    "MysqlUserGrants",
    "MysqlUsers",
    "MysqlVariableCommand",
    "MysqlVariables",
    "database",
    "dump",
    "index",
    "kill",
    "load",
    "optimize",
    "plugin",
    "privileges",
    "replica",
    "role",
    "sql",
    "table",
    "user",
    "variable",
]