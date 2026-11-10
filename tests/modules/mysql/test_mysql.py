# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the mysql module: the thirteen ``Mysql*`` facts and the
``database``/``dump``/``load``/``privileges``/``sql``/``user``/``index``/
``kill``/``optimize``/``plugin``/``replica``/``role``/``table``/
``variable`` operations.

Covers ``reemote5.modules.mysql.mysql``. ``MysqlDatabases``, ``MysqlUsers``
and ``MysqlUserGrants`` mirror the baseline's ``mysql`` facts, so their
real-transport parity reuses the baseline's own fact fixtures verbatim (canned
``mysql -Be`` output plus the name-keyed result), including the
custom-connection command render (minus the ``-p`` password flag the
namespace deliberately drops, with space-separated shlex-quoted flags).
The operation parity reuses the baseline's own operation-test fixtures
(``mysql.privileges`` add/delete and the ALL+GRANT OPTION special cases,
``mysql.user`` resource limits and REQUIRE SSL alter, ``mysql.database``'s
privileges bundle). The ten reemote5-original facts and eight original
operations have no upstream fixture, so they are exercised with realistic
canned ``mysql -B`` output plus their parsing edge cases (empty output,
the NULL batch marker, grouped index columns, view rows, replica lag).

Every fact also reads its ``SimulatedHost`` seam (cluster-wide entry
lists / name-keyed dicts, or database-keyed entry lists for the
database-scoped facts) with copy-on-read semantics, every descriptor's
``shell()`` rendering is pinned, and the shared ``mysql`` command
renderer is checked for flag order, the first-position
``--defaults-extra-file`` and ``shlex`` quoting.
"""

import shlex

import pytest

import reemote5
from reemote5.core import FactCache, OpContext, OperationError, op
from reemote5.drivers import (
    DeterminismError,
    command_shell,
    format_log,
    format_plan,
    gather_fact,
    run,
    run_apply,
    run_prepare,
    send_command,
)
from reemote5.hosts import Host
from reemote5.modules import mysql
from reemote5.modules.mysql.mysql import (
    MysqlBinaryLogs,
    MysqlColumns,
    MysqlConnections,
    MysqlDatabaseCommand,
    MysqlDatabases,
    MysqlDumpCommand,
    MysqlEngines,
    MysqlIndexCommand,
    MysqlIndexes,
    MysqlKillCommand,
    MysqlLoadCommand,
    MysqlOptimizeCommand,
    MysqlPluginCommand,
    MysqlPlugins,
    MysqlPrivilegesCommand,
    MysqlReplicaCommand,
    MysqlReplication,
    MysqlRoleCommand,
    MysqlSqlCommand,
    MysqlStatus,
    MysqlTableCommand,
    MysqlTables,
    MysqlUserCommand,
    MysqlUserGrants,
    MysqlUsers,
    MysqlVariableCommand,
    MysqlVariables,
    _account,
    _mysql_sql,
    _parse_rows,
    _quote_ident,
    _quote_literal,
    _try_int,
)
from tests.simulated_host import SimulatedHost


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell`` instead of connecting."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout
        self.shells: list[str] = []

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        self.shells.append(shell)
        return self._stdout


async def materialize(host: Host, operation, **kwargs):
    """Run an operation against a fresh cache; return ``(ctx, commands)``."""
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in operation(ctx, **kwargs)]
    return ctx, commands


# --- shared helpers ----------------------------------------------------------


def test_parse_rows_empty_output_is_empty_list():
    # The hardened deviation from the baseline: empty stdout parses to [] rather
    # than raising on an unconditional header read (a guarded fact whose
    # command errors reads as empty output).
    assert _parse_rows([]) == []
    # A lone blank line is a header with no rows, still [].
    assert _parse_rows([""]) == []


def test_parse_rows_header_keyed_and_null_marker():
    # Tab-separated mysql -B output: the first line names the columns,
    # each following line is one row. Both the empty field and the
    # literal NULL batch marker read as None (real mysql -B prints NULL
    # columns as NULL; the baseline's fixtures only exercise the empty form).
    lines = ["A\tB\tC", "x\tNULL\tz", "y\t\tw"]
    assert _parse_rows(lines) == [
        {"A": "x", "B": None, "C": "z"},
        {"A": "y", "B": None, "C": "w"},
    ]


def test_parse_rows_lower_titles_and_extra_fields():
    lines = ["SCHEMA_NAME\tX", "shop\textra\tfields"]
    assert _parse_rows(lines, lower_titles=True) == [{"schema_name": "shop", "x": "extra"}]


def test_quote_ident_doubles_embedded_backticks():
    assert _quote_ident("plain") == "`plain`"
    assert _quote_ident("we`ird") == "`we``ird`"


def test_quote_literal_doubles_quotes_and_backslashes():
    assert _quote_literal("plain") == "'plain'"
    assert _quote_literal("it's") == "'it''s'"
    assert _quote_literal("a\\b") == "'a\\\\b'"


def test_try_int_coerces_numeric_text_only():
    assert _try_int(None) is None
    assert _try_int("5") == 5
    assert _try_int("0") == 0
    assert _try_int("0.000000") == "0.000000"


def test_account_doubles_embedded_double_quotes():
    assert _account("app", "localhost") == '"app"@"localhost"'
    assert _account('we"ird', "%") == '"we""ird"@"%"'


def test_mysql_sql_flag_order_and_quoting():
    # The baseline's operand order: the positional database, then -u user,
    # -h host, -P port. Flags are space-separated and shlex-quoted (the
    # documented deviation from the baseline's attached -u<user> form); plain
    # values quote to themselves, so ordinary names keep command parity.
    assert _mysql_sql("SELECT 1") == "mysql -Be 'SELECT 1'"
    assert _mysql_sql(
        "SELECT 1",
        mysql_database="mydb",
        mysql_user="myuser",
        mysql_host="myhost",
        mysql_port=3306,
    ) == "mysql mydb -u myuser -h myhost -P 3306 -Be 'SELECT 1'"
    # A value carrying shell metacharacters is quoted literally.
    assert _mysql_sql("SELECT 1", mysql_database="my db") == (
        "mysql 'my db' -Be 'SELECT 1'"
    )


def test_mysql_sql_defaults_file_is_first_and_guard_appends():
    # --defaults-extra-file must be the FIRST option on a mysql command
    # line; the ignore_errors guard appends || true (MysqlUserGrants /
    # MysqlBinaryLogs behaviour).
    assert _mysql_sql(
        "SELECT 1", mysql_defaults_file="/etc/my.cnf.d/x.cnf", mysql_user="u"
    ) == "mysql --defaults-extra-file=/etc/my.cnf.d/x.cnf -u u -Be 'SELECT 1'"
    assert _mysql_sql("SHOW GRANTS FOR \"u\"@\"h\"", ignore_errors=True) == (
        "mysql -Be 'SHOW GRANTS FOR \"u\"@\"h\"' || true"
    )


# --- descriptor rendering ----------------------------------------------------


def test_database_command_rendering():
    assert MysqlDatabaseCommand(database="shop").shell() == (
        "mysql -Be 'CREATE DATABASE `shop`'"
    )
    # The baseline's clause order: COLLATE then CHARSET.
    assert MysqlDatabaseCommand(
        database="shop", charset="utf8mb4", collate="utf8mb4_bin"
    ).shell() == "mysql -Be 'CREATE DATABASE `shop` COLLATE `utf8mb4_bin` CHARSET `utf8mb4`'"
    assert MysqlDatabaseCommand(
        database="shop", alter=True, charset="utf8mb4"
    ).shell() == "mysql -Be 'ALTER DATABASE `shop` CHARSET `utf8mb4`'"
    assert MysqlDatabaseCommand(database="shop", present=False).shell() == (
        "mysql -Be 'DROP DATABASE `shop`'"
    )


def test_dump_command_rendering():
    assert MysqlDumpCommand(dest="/tmp/d b.sql", mysql_database="shop").shell() == (
        "mysqldump shop > '/tmp/d b.sql'"
    )
    # baseline parity: database=None renders a bare mysqldump (no
    # --all-databases is injected).
    assert MysqlDumpCommand(dest="/tmp/all.sql").shell() == "mysqldump > /tmp/all.sql"


def test_index_command_rendering():
    assert MysqlIndexCommand(
        database="shop", table="t", index="idx", columns=("a", "b"), unique=True
    ).shell() == "mysql -Be 'CREATE UNIQUE INDEX `idx` USING BTREE ON `shop`.`t` (a, b)'"
    assert MysqlIndexCommand(
        database="shop", table="t", index="ft", columns=("body",), index_type="FULLTEXT"
    ).shell() == "mysql -Be 'CREATE FULLTEXT INDEX `ft` ON `shop`.`t` (body)'"
    assert MysqlIndexCommand(
        database="shop", table="t", index="h", columns=("a",), index_type="HASH"
    ).shell() == "mysql -Be 'CREATE INDEX `h` USING HASH ON `shop`.`t` (a)'"
    assert MysqlIndexCommand(
        database="shop", table="t", index="idx", present=False
    ).shell() == "mysql -Be 'DROP INDEX `idx` ON `shop`.`t`'"


def test_kill_command_rendering():
    assert MysqlKillCommand(process_id=7).shell() == "mysql -Be 'KILL 7'"
    assert MysqlKillCommand(process_id=7, query_only=True).shell() == (
        "mysql -Be 'KILL QUERY 7'"
    )


def test_load_command_rendering():
    assert MysqlLoadCommand(src="/tmp/d b.sql", mysql_database="shop").shell() == (
        "mysql shop < '/tmp/d b.sql'"
    )


def test_optimize_command_rendering():
    assert MysqlOptimizeCommand(database="shop", tables=("a", "b")).shell() == (
        "mysql -Be 'OPTIMIZE TABLE `shop`.`a`, `shop`.`b`'"
    )
    assert MysqlOptimizeCommand(
        database="shop", tables=("a",), action="analyze"
    ).shell() == "mysql -Be 'ANALYZE TABLE `shop`.`a`'"


def test_plugin_command_rendering():
    sql = "INSTALL PLUGIN `validate_password` SONAME 'validate_password.so'"
    assert MysqlPluginCommand(
        plugin="validate_password", soname="validate_password.so"
    ).shell() == "mysql -Be " + shlex.quote(sql)
    assert MysqlPluginCommand(plugin="validate_password", present=False).shell() == (
        "mysql -Be 'UNINSTALL PLUGIN `validate_password`'"
    )


def test_privileges_command_rendering():
    assert MysqlPrivilegesCommand(
        action="GRANT",
        privileges=("SELECT", "INSERT"),
        database="`shop`",
        table="*",
        user="app",
        user_hostname="localhost",
    ).shell() == 'mysql -Be \'GRANT SELECT, INSERT ON `shop`.* TO "app"@"localhost"\''
    assert MysqlPrivilegesCommand(
        action="GRANT",
        privileges=("ALL PRIVILEGES",),
        database="`shop`",
        table="*",
        user="app",
        user_hostname="localhost",
        with_statement="GRANT OPTION",
    ).shell() == (
        'mysql -Be \'GRANT ALL PRIVILEGES ON `shop`.* TO "app"@"localhost" '
        "WITH GRANT OPTION'"
    )
    assert MysqlPrivilegesCommand(
        action="REVOKE",
        privileges=("UPDATE",),
        database="*",
        table="*",
        user="app",
        user_hostname="localhost",
    ).shell() == 'mysql -Be \'REVOKE UPDATE ON *.* FROM "app"@"localhost"\''


def test_replica_command_rendering():
    assert MysqlReplicaCommand().shell() == "mysql -Be 'START REPLICA'"
    assert MysqlReplicaCommand(running=False, channel="ch1").shell() == (
        "mysql -Be " + shlex.quote("STOP REPLICA FOR CHANNEL 'ch1'")
    )
    assert MysqlReplicaCommand(reset=True).shell() == (
        "mysql -Be 'STOP REPLICA; RESET REPLICA ALL'"
    )


def test_role_command_rendering():
    assert MysqlRoleCommand(role="ro").shell() == "mysql -Be 'CREATE ROLE `ro`@`%`'"
    assert MysqlRoleCommand(role="ro", present=False).shell() == (
        "mysql -Be 'DROP ROLE `ro`@`%`'"
    )
    assert MysqlRoleCommand(role="ro", member="app").shell() == (
        'mysql -Be \'GRANT `ro`@`%` TO "app"@"localhost"\''
    )
    assert MysqlRoleCommand(role="ro", member="app", member_present=False).shell() == (
        'mysql -Be \'REVOKE `ro`@`%` FROM "app"@"localhost"\''
    )


def test_sql_command_rendering_and_connection_tail():
    assert MysqlSqlCommand(sql="SELECT 1").shell() == "mysql -Be 'SELECT 1'"
    assert MysqlSqlCommand(sql="SELECT 1", mysql_database="shop").shell() == (
        "mysql shop -Be 'SELECT 1'"
    )
    # The full connection tail: defaults-file first, then database,
    # user, host, port.
    assert MysqlSqlCommand(
        sql="SELECT 1",
        mysql_user="u",
        mysql_host="h",
        mysql_port=3306,
        mysql_defaults_file="/etc/my.cnf.d/x.cnf",
    ).shell() == (
        "mysql --defaults-extra-file=/etc/my.cnf.d/x.cnf -u u -h h -P 3306 "
        "-Be 'SELECT 1'"
    )


def test_table_command_rendering():
    sql = (
        "CREATE TABLE `shop`.`t` (id INT PRIMARY KEY, name VARCHAR(50)) "
        "ENGINE = `InnoDB` DEFAULT CHARSET = `utf8mb4` COMMENT = 'items'"
    )
    assert MysqlTableCommand(
        database="shop",
        table="t",
        columns=("id INT PRIMARY KEY", "name VARCHAR(50)"),
        engine="InnoDB",
        charset="utf8mb4",
        comment="items",
    ).shell() == "mysql -Be " + shlex.quote(sql)
    assert MysqlTableCommand(database="shop", table="t", present=False).shell() == (
        "mysql -Be 'DROP TABLE `shop`.`t`'"
    )


def test_user_command_rendering():
    assert MysqlUserCommand(user="app").shell() == (
        'mysql -Be \'CREATE USER "app"@"localhost"\''
    )
    sql = (
        'CREATE USER "app"@"localhost" IDENTIFIED WITH auth_socket '
        "REQUIRE CIPHER 'CIPH' WITH MAX_USER_CONNECTIONS 5"
    )
    assert MysqlUserCommand(
        user="app",
        auth_plugin="auth_socket",
        require="X509",
        require_cipher="CIPH",
        max_connections=5,
    ).shell() == "mysql -Be " + shlex.quote(sql)
    assert MysqlUserCommand(user="app", alter=True, require="SSL").shell() == (
        'mysql -Be \'ALTER USER "app"@"localhost" REQUIRE SSL\''
    )
    assert MysqlUserCommand(user="app", require="X509").shell() == (
        'mysql -Be \'CREATE USER "app"@"localhost" REQUIRE X509\''
    )
    assert MysqlUserCommand(user="app", present=False).shell() == (
        'mysql -Be \'DROP USER "app"@"localhost"\''
    )


def test_variable_command_rendering():
    assert MysqlVariableCommand(name="max_connections", value="500").shell() == (
        "mysql -Be 'SET GLOBAL `max_connections` = 500'"
    )
    assert MysqlVariableCommand(
        name="sql_mode", value="STRICT_TRANS_TABLES", persist=True
    ).shell() == (
        "mysql -Be " + shlex.quote("SET PERSIST `sql_mode` = 'STRICT_TRANS_TABLES'")
    )
    # A negative numeric renders bare too.
    assert MysqlVariableCommand(name="x", value="-5").shell() == (
        "mysql -Be 'SET GLOBAL `x` = -5'"
    )
    assert MysqlVariableCommand(name="max_connections", present=False).shell() == (
        "mysql -Be 'RESET PERSIST `max_connections`'"
    )


def test_command_shell_elevated_forms():
    cmd = MysqlDatabaseCommand(database="shop")
    assert command_shell(cmd, sudo=True) == "sudo mysql -Be 'CREATE DATABASE `shop`'"
    assert command_shell(cmd, sudo=True, sudo_user="mysql") == (
        "sudo -u mysql mysql -Be 'CREATE DATABASE `shop`'"
    )
    assert command_shell(cmd, su=True) == (
        "su -c 'mysql -Be '\"'\"'CREATE DATABASE `shop`'\"'\"''"
    )


# --- MysqlDatabases (baseline parity) -----------------------------------------


DATABASES_OUTPUT = """\
CATALOG_NAME\tSCHEMA_NAME\tDEFAULT_CHARACTER_SET_NAME\tDEFAULT_COLLATION_NAME\tSQL_PATH
def\tinformation_schema\tutf8\tutf8_general_ci\tNULL
def\tmysql\tutf8mb4\tutf8mb4_general_ci\tNULL
def\tperformance_schema\tutf8\tutf8_general_ci\tNULL
def\tbaseline_stuff\tutf8\tutf8_general_ci\tNULL
"""

DATABASES_FACT = {
    "information_schema": {"character_set": "utf8", "collation_name": "utf8_general_ci"},
    "mysql": {"character_set": "utf8mb4", "collation_name": "utf8mb4_general_ci"},
    "performance_schema": {"character_set": "utf8", "collation_name": "utf8_general_ci"},
    "baseline_stuff": {"character_set": "utf8", "collation_name": "utf8_general_ci"},
}


async def test_mysql_databases_baseline_parity():
    host = CannedShellHost(DATABASES_OUTPUT)
    assert await MysqlDatabases.process(host) == DATABASES_FACT
    assert host.shells == ["mysql -Be 'SELECT * FROM information_schema.SCHEMATA'"]


def test_mysql_databases_custom_connection_command():
    # The baseline's fixture args minus the password, with the documented
    # space-separated shlex-quoted flags (metacharacters quote literally).
    assert MysqlDatabases.command(
        mysql_user="x$(reboot)", mysql_host="h; rm -rf /", mysql_port=3306
    ) == (
        "mysql -u 'x$(reboot)' -h 'h; rm -rf /' -P 3306 "
        "-Be 'SELECT * FROM information_schema.SCHEMATA'"
    )


async def test_mysql_databases_seam_copy_on_read():
    host = SimulatedHost(name="h", mysql_databases={"shop": dict(DATABASES_FACT["mysql"])})
    result = await MysqlDatabases.process(host)
    assert result == {"shop": DATABASES_FACT["mysql"]}
    result["shop"]["character_set"] = "latin1"
    assert host.mysql_databases["shop"]["character_set"] == "utf8mb4"


# --- MysqlUsers (baseline parity) ---------------------------------------------


USERS_OUTPUT = """\
Host\tUser\tPassword\tSelect_priv\tInsert_priv\tUpdate_priv\tDelete_priv\tCreate_priv\tDrop_priv\tReload_priv\tShutdown_priv\tProcess_priv\tFile_priv\tGrant_priv\tReferences_priv\tIndex_priv\tAlter_priv\tShow_db_priv\tSuper_priv\tCreate_tmp_table_priv\tLock_tables_priv\tExecute_priv\tRepl_slave_priv\tRepl_client_priv\tCreate_view_priv\tShow_view_priv\tCreate_routine_priv\tAlter_routine_priv\tCreate_user_priv\tEvent_priv\tTrigger_priv\tCreate_tablespace_priv\tssl_type\tssl_cipher\tx509_issuer\tx509_subject\tmax_questions\tmax_updates\tmax_connections\tmax_user_connections\tplugin\tauthentication_string\tpassword_expired\tis_role\tdefault_role\tmax_statement_time
localhost\troot\t\tY\tY\tY\tY\tY\tY\tY\tY\tY\tY\tY\tY\tY\tY\tY\tY\tY\tY\tY\tY\tY\tY\tY\tY\tY\tY\tY\tY\tY\t\t\t\t\t0\t0\t0\t0\tunix_socket\t\tN\tN\t\t0.000000
localhost\tbaseline\t*DAABDB4081CCE333168409A6DB119E18D8EAA073\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\t\t\t\t\t0\t0\t0\t0\t\t\tN\tN\t\t0.000000
\t\t*DAABDB4081CCE333168409A6DB119E18D8EAA073\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\tN\t\t\t\t\t0\t0\t0\t0\t\t\tN\tN\t\t0.000000
"""

ROOT_PRIVILEGES = [
    "Alter", "Alter_routine", "Create", "Create_routine", "Create_tablespace",
    "Create_tmp_table", "Create_user", "Create_view", "Delete", "Drop",
    "Event", "Execute", "File", "Grant", "Index", "Insert", "Lock_tables",
    "Process", "References", "Reload", "Repl_client", "Repl_slave", "Select",
    "Show_db", "Show_view", "Shutdown", "Super", "Trigger", "Update",
]

USERS_FACT = {
    "root@localhost": {
        "Password": None,
        "ssl_type": None,
        "ssl_cipher": None,
        "x509_issuer": None,
        "x509_subject": None,
        "max_questions": 0,
        "max_updates": 0,
        "max_connections": 0,
        "max_user_connections": 0,
        "plugin": "unix_socket",
        "authentication_string": None,
        "password_expired": False,
        "is_role": False,
        "default_role": None,
        "max_statement_time": "0.000000",
        "privileges": ROOT_PRIVILEGES,
    },
    "baseline@localhost": {
        "Password": "*DAABDB4081CCE333168409A6DB119E18D8EAA073",
        "ssl_type": None,
        "ssl_cipher": None,
        "x509_issuer": None,
        "x509_subject": None,
        "max_questions": 0,
        "max_updates": 0,
        "max_connections": 0,
        "max_user_connections": 0,
        "plugin": None,
        "authentication_string": None,
        "password_expired": False,
        "is_role": False,
        "default_role": None,
        "max_statement_time": "0.000000",
        "privileges": [],
    },
}


async def test_mysql_users_baseline_parity():
    # The MariaDB-shaped fixture: the anonymous (empty Host/User) row is
    # skipped, *_priv columns fold into the sorted privileges list,
    # max_* coerce with try_int and password_expired/is_role from Y/N.
    host = CannedShellHost(USERS_OUTPUT)
    assert await MysqlUsers.process(host) == USERS_FACT
    assert host.shells == ["mysql -Be 'SELECT * FROM mysql.user'"]


async def test_mysql_users_seam_copy_on_read():
    entry = dict(USERS_FACT["root@localhost"])
    host = SimulatedHost(name="h", mysql_users={"root@localhost": entry})
    result = await MysqlUsers.process(host)
    assert result == {"root@localhost": USERS_FACT["root@localhost"]}
    result["root@localhost"]["privileges"].append("Extra")
    assert host.mysql_users["root@localhost"]["privileges"] == ROOT_PRIVILEGES


# --- MysqlUserGrants (baseline parity) ----------------------------------------


GRANTS_OUTPUT = """\
GRANT USAGE ON *.* TO `someuser`@`localhost` IDENTIFIED BY PASSWORD '*THISISSECRET'
GRANT ALL PRIVILEGES ON `some_database`.* TO `someuser`@`localhost` WITH GRANT OPTION
"""

GRANTS_FACT = {
    "*.*": ["USAGE"],
    "`some_database`.*": ["ALL PRIVILEGES", "GRANT OPTION"],
}


async def test_mysql_user_grants_baseline_parity():
    host = CannedShellHost(GRANTS_OUTPUT)
    assert await MysqlUserGrants.process(host, "someuser") == GRANTS_FACT
    # The || true guard is the baseline's ignore_errors (SHOW GRANTS errors
    # when the user does not exist, which reads as {}).
    assert host.shells == ['mysql -Be \'SHOW GRANTS FOR "someuser"@"localhost"\' || true']


async def test_mysql_user_grants_absent_user_reads_empty():
    host = CannedShellHost("")
    assert await MysqlUserGrants.process(host, "someuser") == {}


def test_mysql_user_grants_role_grant_extension():
    # reemote5 extension: role-grant lines (no ON clause) are captured
    # under the backticked role@host key with the single ROLE privilege.
    lines = [
        "GRANT `ro`@`%` TO `app`@`localhost`",
        "GRANT `r1`@`%`, `r2`@`%` TO `app`@`localhost`",
    ]
    assert MysqlUserGrants.parse(lines) == {
        "`ro`@`%`": ["ROLE"],
        "`r1`@`%`": ["ROLE"],
        "`r2`@`%`": ["ROLE"],
    }


async def test_mysql_user_grants_seam_copy_on_read():
    host = SimulatedHost(
        name="h",
        mysql_user_grants={"someuser@localhost": {"*.*": ["USAGE"]}},
    )
    result = await MysqlUserGrants.process(host, "someuser")
    assert result == {"*.*": ["USAGE"]}
    result["*.*"].append("SELECT")
    assert host.mysql_user_grants["someuser@localhost"]["*.*"] == ["USAGE"]
    assert await MysqlUserGrants.process(host, "other") == {}


# --- MysqlBinaryLogs (original) ----------------------------------------------


BINARY_LOGS_OUTPUT = """\
Log_name\tFile_size\tEncrypted
binlog.000001\t157\tYes
binlog.000002\t2049\tNo
"""


async def test_mysql_binary_logs_parse_and_command():
    host = CannedShellHost(BINARY_LOGS_OUTPUT)
    assert await MysqlBinaryLogs.process(host) == [
        {"name": "binlog.000001", "size": 157, "encrypted": True},
        {"name": "binlog.000002", "size": 2049, "encrypted": False},
    ]
    # Binary logging disabled errors (1381): the guard masks it to [].
    assert host.shells == ["mysql -Be 'SHOW BINARY LOGS' || true"]


def test_mysql_binary_logs_edge_cases():
    # No Encrypted column (pre-8.0.14) reads None; empty output is [].
    assert MysqlBinaryLogs.parse(["Log_name\tFile_size", "binlog.000001\t157"]) == [
        {"name": "binlog.000001", "size": 157, "encrypted": None}
    ]
    assert MysqlBinaryLogs.parse([]) == []
    assert MysqlBinaryLogs.parse(["Log_name\tFile_size", "\t157"]) == []


async def test_mysql_binary_logs_seam_copy_on_read():
    entry = {"name": "binlog.000001", "size": 157, "encrypted": True}
    host = SimulatedHost(name="h", mysql_binary_logs=[entry])
    result = await MysqlBinaryLogs.process(host)
    assert result == [entry]
    result[0]["size"] = 1
    assert host.mysql_binary_logs[0]["size"] == 157


# --- MysqlColumns (original) -------------------------------------------------


COLUMNS_OUTPUT = """\
COLUMN_NAME\tCOLUMN_TYPE\tIS_NULLABLE\tCOLUMN_DEFAULT\tCOLUMN_KEY\tEXTRA\tCOLUMN_COMMENT\tORDINAL_POSITION
id\tint unsigned\tNO\tNULL\tPRI\tauto_increment\t\t1
email\tvarchar(255)\tYES\tNULL\tUNI\t\tThe email\t2
"""

COLUMNS_FACT = [
    {
        "name": "id",
        "type": "int unsigned",
        "nullable": False,
        "default": None,
        "key": "PRI",
        "extra": "auto_increment",
        "comment": "",
        "position": 1,
    },
    {
        "name": "email",
        "type": "varchar(255)",
        "nullable": True,
        "default": None,
        "key": "UNI",
        "extra": "",
        "comment": "The email",
        "position": 2,
    },
]


async def test_mysql_columns_parse_and_command():
    host = CannedShellHost(COLUMNS_OUTPUT)
    assert await MysqlColumns.process(host, "shop", "t") == COLUMNS_FACT
    sql = (
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, "
        "COLUMN_KEY, EXTRA, COLUMN_COMMENT, ORDINAL_POSITION "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = 'shop' "
        "AND TABLE_NAME = 't' ORDER BY ORDINAL_POSITION"
    )
    assert host.shells == ["mysql -Be " + shlex.quote(sql)]


def test_mysql_columns_empty_table_reads_empty():
    assert MysqlColumns.parse([]) == []


async def test_mysql_columns_seam_copy_on_read():
    host = SimulatedHost(name="h", mysql_columns={"shop.t": [dict(COLUMNS_FACT[0])]})
    result = await MysqlColumns.process(host, "shop", "t")
    assert result == [COLUMNS_FACT[0]]
    result[0]["name"] = "changed"
    assert host.mysql_columns["shop.t"][0]["name"] == "id"
    # An unknown database/table reads as [].
    assert await MysqlColumns.process(host, "shop", "u") == []


# --- MysqlConnections (original) ---------------------------------------------


CONNECTIONS_OUTPUT = """\
ID\tUSER\tHOST\tDB\tCOMMAND\tTIME\tSTATE
7\tapp\tlocalhost:51234\tshop\tQuery\t12\tSending data
8\tevent_scheduler\tlocalhost\tNULL\tDaemon\t999\tWaiting on empty queue
"""


async def test_mysql_connections_parse_and_command():
    host = CannedShellHost(CONNECTIONS_OUTPUT)
    assert await MysqlConnections.process(host) == [
        {
            "id": 7,
            "user": "app",
            "host": "localhost:51234",
            "database": "shop",
            "command": "Query",
            "time": 12,
            "state": "Sending data",
        },
        {
            "id": 8,
            "user": "event_scheduler",
            "host": "localhost",
            "database": None,
            "command": "Daemon",
            "time": 999,
            "state": "Waiting on empty queue",
        },
    ]
    # The INFO column is deliberately omitted (query text can carry
    # credentials): the SELECT lists exactly seven columns.
    assert host.shells == [
        (
            "mysql -Be 'SELECT ID, USER, HOST, DB, COMMAND, TIME, STATE "
            "FROM information_schema.PROCESSLIST'"
        )
    ]


async def test_mysql_connections_seam_copy_on_read():
    entry = {
        "id": 7, "user": "app", "host": "localhost:51234", "database": "shop",
        "command": "Query", "time": 12, "state": "Sending data",
    }
    host = SimulatedHost(name="h", mysql_connections=[entry])
    result = await MysqlConnections.process(host)
    assert result == [entry]
    result[0]["time"] = 0
    assert host.mysql_connections[0]["time"] == 12


# --- MysqlEngines (original) -------------------------------------------------


ENGINES_OUTPUT = """\
ENGINE\tSUPPORT\tCOMMENT\tTRANSACTIONS\tXA\tSAVEPOINTS
InnoDB\tDEFAULT\tSupports transactions, row-level locking\tYES\tYES\tYES
MyISAM\tYES\tMyISAM storage engine\tNO\tNO\tNO
"""


async def test_mysql_engines_parse_and_command():
    host = CannedShellHost(ENGINES_OUTPUT)
    assert await MysqlEngines.process(host) == [
        {
            "name": "InnoDB",
            "support": "DEFAULT",
            "comment": "Supports transactions, row-level locking",
            "transactions": True,
            "xa": True,
            "savepoints": True,
        },
        {
            "name": "MyISAM",
            "support": "YES",
            "comment": "MyISAM storage engine",
            "transactions": False,
            "xa": False,
            "savepoints": False,
        },
    ]
    assert host.shells == [
        (
            "mysql -Be 'SELECT ENGINE, SUPPORT, COMMENT, TRANSACTIONS, XA, SAVEPOINTS "
            "FROM information_schema.ENGINES'"
        )
    ]


async def test_mysql_engines_seam_copy_on_read():
    entry = {
        "name": "InnoDB", "support": "DEFAULT", "comment": "",
        "transactions": True, "xa": True, "savepoints": True,
    }
    host = SimulatedHost(name="h", mysql_engines=[entry])
    result = await MysqlEngines.process(host)
    assert result == [entry]
    result[0]["support"] = "NO"
    assert host.mysql_engines[0]["support"] == "DEFAULT"


# --- MysqlIndexes (original) -------------------------------------------------


INDEXES_OUTPUT = """\
TABLE_NAME\tINDEX_NAME\tNON_UNIQUE\tSEQ_IN_INDEX\tCOLUMN_NAME\tINDEX_TYPE
t\tPRIMARY\t0\t1\tid\tBTREE
t\tidx_ab\t1\t1\ta\tBTREE
t\tidx_ab\t1\t2\tb\tBTREE
u\tft\t1\t1\tbody\tFULLTEXT
"""

INDEXES_FACT = [
    {"name": "PRIMARY", "table": "t", "columns": ["id"], "unique": True, "index_type": "BTREE"},
    {"name": "idx_ab", "table": "t", "columns": ["a", "b"], "unique": False, "index_type": "BTREE"},
    {"name": "ft", "table": "u", "columns": ["body"], "unique": False, "index_type": "FULLTEXT"},
]


async def test_mysql_indexes_groups_columns_in_key_order():
    # One row per index COLUMN: grouped by (table, index) with the
    # column list following SEQ_IN_INDEX order.
    host = CannedShellHost(INDEXES_OUTPUT)
    assert await MysqlIndexes.process(host, "shop") == INDEXES_FACT
    sql = (
        "SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, "
        "COLUMN_NAME, INDEX_TYPE FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = 'shop' ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX"
    )
    assert host.shells == ["mysql -Be " + shlex.quote(sql)]


def test_mysql_indexes_command_narrows_to_table():
    sql = (
        "SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, "
        "COLUMN_NAME, INDEX_TYPE FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = 'shop' AND TABLE_NAME = 't' "
        "ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX"
    )
    assert MysqlIndexes.command("shop", "t") == "mysql -Be " + shlex.quote(sql)


async def test_mysql_indexes_seam_copy_on_read_and_narrowing():
    host = SimulatedHost(name="h", mysql_indexes={"shop": [dict(e) for e in INDEXES_FACT]})
    result = await MysqlIndexes.process(host, "shop")
    assert result == INDEXES_FACT
    result[1]["columns"].append("c")
    assert host.mysql_indexes["shop"][1]["columns"] == ["a", "b"]
    # The table argument narrows the seam exactly as the WHERE clause.
    assert await MysqlIndexes.process(host, "shop", "u") == [INDEXES_FACT[2]]
    assert await MysqlIndexes.process(host, "other") == []


# --- MysqlPlugins (original) -------------------------------------------------


PLUGINS_OUTPUT = """\
PLUGIN_NAME\tPLUGIN_STATUS\tPLUGIN_TYPE\tPLUGIN_LIBRARY\tPLUGIN_LICENSE
InnoDB\tACTIVE\tSTORAGE ENGINE\tNULL\tGPL
validate_password\tACTIVE\tVALIDATE PASSWORD\tvalidate_password.so\tGPL
"""


async def test_mysql_plugins_parse_and_command():
    host = CannedShellHost(PLUGINS_OUTPUT)
    assert await MysqlPlugins.process(host) == [
        {
            "name": "InnoDB",
            "status": "ACTIVE",
            "type": "STORAGE ENGINE",
            "library": None,
            "license": "GPL",
        },
        {
            "name": "validate_password",
            "status": "ACTIVE",
            "type": "VALIDATE PASSWORD",
            "library": "validate_password.so",
            "license": "GPL",
        },
    ]
    assert host.shells == [
        (
            "mysql -Be 'SELECT PLUGIN_NAME, PLUGIN_STATUS, PLUGIN_TYPE, PLUGIN_LIBRARY, "
            "PLUGIN_LICENSE FROM information_schema.PLUGINS'"
        )
    ]


async def test_mysql_plugins_seam_copy_on_read():
    entry = {
        "name": "InnoDB", "status": "ACTIVE", "type": "STORAGE ENGINE",
        "library": None, "license": "GPL",
    }
    host = SimulatedHost(name="h", mysql_plugins=[entry])
    result = await MysqlPlugins.process(host)
    assert result == [entry]
    result[0]["status"] = "DISABLED"
    assert host.mysql_plugins[0]["status"] == "ACTIVE"


# --- MysqlReplication (original) ---------------------------------------------


REPLICATION_OUTPUT = """\
Channel_Name\tSource_Host\tSource_Port\tSource_User\tReplica_IO_Running\tReplica_SQL_Running\tSeconds_Behind_Source\tLast_Error
\t192.168.0.10\t3306\trepl\tYes\tYes\t0\t
ch1\t192.168.0.11\t3307\trepl\tConnecting\tNo\tNULL\tError text
"""

REPLICATION_FACT = [
    {
        "channel": "",
        "source_host": "192.168.0.10",
        "source_port": 3306,
        "source_user": "repl",
        "io_running": "Yes",
        "sql_running": "Yes",
        "seconds_behind_source": 0,
        "last_error": "",
    },
    {
        "channel": "ch1",
        "source_host": "192.168.0.11",
        "source_port": 3307,
        "source_user": "repl",
        "io_running": "Connecting",
        "sql_running": "No",
        "seconds_behind_source": None,
        "last_error": "Error text",
    },
]


async def test_mysql_replication_parse_and_command():
    host = CannedShellHost(REPLICATION_OUTPUT)
    assert await MysqlReplication.process(host) == REPLICATION_FACT
    assert host.shells == ["mysql -Be 'SHOW REPLICA STATUS'"]


async def test_mysql_replication_not_a_replica_reads_empty():
    # SHOW REPLICA STATUS reports an EMPTY result on a non-replica.
    host = CannedShellHost("")
    assert await MysqlReplication.process(host) == []


async def test_mysql_replication_seam_copy_on_read():
    host = SimulatedHost(name="h", mysql_replication=[dict(REPLICATION_FACT[0])])
    result = await MysqlReplication.process(host)
    assert result == [REPLICATION_FACT[0]]
    result[0]["io_running"] = "No"
    assert host.mysql_replication[0]["io_running"] == "Yes"


# --- MysqlStatus / MysqlVariables (original) ---------------------------------


STATUS_OUTPUT = """\
Variable_name\tValue
Uptime\t12345
Threads_connected\t3
Ssl_session_cache_hits\t
"""

VARIABLES_OUTPUT = """\
Variable_name\tValue
max_connections\t151
sql_mode\tSTRICT_TRANS_TABLES
innodb_buffer_pool_size\t134217728
"""


async def test_mysql_status_parse_and_command():
    host = CannedShellHost(STATUS_OUTPUT)
    # Values stay TEXT; a NULL/empty value reads as the empty string.
    assert await MysqlStatus.process(host) == {
        "Uptime": "12345",
        "Threads_connected": "3",
        "Ssl_session_cache_hits": "",
    }
    assert host.shells == ["mysql -Be 'SHOW GLOBAL STATUS'"]


async def test_mysql_variables_parse_and_command():
    host = CannedShellHost(VARIABLES_OUTPUT)
    assert await MysqlVariables.process(host) == {
        "max_connections": "151",
        "sql_mode": "STRICT_TRANS_TABLES",
        "innodb_buffer_pool_size": "134217728",
    }
    assert host.shells == ["mysql -Be 'SHOW GLOBAL VARIABLES'"]


async def test_mysql_status_and_variables_seam_copy_on_read():
    host = SimulatedHost(
        name="h",
        mysql_status={"Uptime": "12345"},
        mysql_variables={"max_connections": "151"},
    )
    status = await MysqlStatus.process(host)
    variables = await MysqlVariables.process(host)
    status["Uptime"] = "0"
    variables["max_connections"] = "500"
    assert host.mysql_status == {"Uptime": "12345"}
    assert host.mysql_variables == {"max_connections": "151"}


# --- MysqlTables (original) --------------------------------------------------


TABLES_OUTPUT = """\
TABLE_NAME\tENGINE\tTABLE_ROWS\tDATA_LENGTH\tINDEX_LENGTH\tTABLE_COLLATION\tTABLE_COMMENT
t\tInnoDB\t100\t16384\t8192\tutf8mb4_0900_ai_ci\t
v\tNULL\tNULL\tNULL\tNULL\tNULL\tVIEW
"""

TABLES_FACT = [
    {
        "name": "t",
        "engine": "InnoDB",
        "rows": 100,
        "data_length": 16384,
        "index_length": 8192,
        "collation": "utf8mb4_0900_ai_ci",
        "comment": "",
    },
    {
        "name": "v",
        "engine": None,
        "rows": 0,
        "data_length": 0,
        "index_length": 0,
        "collation": None,
        "comment": "VIEW",
    },
]


async def test_mysql_tables_parse_and_command():
    # Views appear with None engine/collation; sizes coerce to int.
    host = CannedShellHost(TABLES_OUTPUT)
    assert await MysqlTables.process(host, "shop") == TABLES_FACT
    sql = (
        "SELECT TABLE_NAME, ENGINE, TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH, "
        "TABLE_COLLATION, TABLE_COMMENT FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = 'shop' ORDER BY TABLE_NAME"
    )
    assert host.shells == ["mysql -Be " + shlex.quote(sql)]


async def test_mysql_tables_seam_copy_on_read():
    host = SimulatedHost(name="h", mysql_tables={"shop": [dict(TABLES_FACT[0])]})
    result = await MysqlTables.process(host, "shop")
    assert result == [TABLES_FACT[0]]
    result[0]["rows"] = 0
    assert host.mysql_tables["shop"][0]["rows"] == 100
    assert await MysqlTables.process(host, "other") == []# --- operations: shared seam entries -----------------------------------------


# A database entry at the simulation's create defaults, so a seeded
# database reads as "no diffs" for a default request.
DB_ENTRY = {"character_set": "utf8mb4", "collation_name": "utf8mb4_0900_ai_ci"}

# An account entry at the simulation's create defaults (the mysql.user
# columns the user operation's drift check uses).
USER_ENTRY = {
    "privileges": [],
    "plugin": "caching_sha2_password",
    "authentication_string": None,
    "ssl_type": None,
    "ssl_cipher": None,
    "x509_issuer": None,
    "x509_subject": None,
    "max_user_connections": 0,
    "max_questions": 0,
    "max_updates": 0,
    "max_connections": 0,
    "password_expired": False,
    "is_role": False,
    "account_locked": False,
}

INDEX_ENTRY = {
    "name": "idx", "table": "t", "columns": ["a", "b"],
    "unique": False, "index_type": "BTREE",
}

PLUGIN_ENTRY = {
    "name": "validate_password", "status": "ACTIVE", "type": "VALIDATE PASSWORD",
    "library": "validate_password.so", "license": "GPL",
}

REPLICA_STOPPED = {
    **REPLICATION_FACT[0],
    "io_running": "No",
    "sql_running": "No",
    "seconds_behind_source": None,
}


# --- mysql.database ----------------------------------------------------------


async def test_database_creates_and_reports_an_existing_database():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(host, mysql.database, database="shop")
    assert ctx.changed
    assert ctx.reason == "mysql database shop will be created"
    assert commands == [MysqlDatabaseCommand(database="shop")]
    host = SimulatedHost(name="h", mysql_databases={"shop": dict(DB_ENTRY)})
    ctx, commands = await materialize(
        host, mysql.database, database="shop",
        charset="utf8mb4", collate="utf8mb4_0900_ai_ci",
    )
    assert not ctx.changed
    assert ctx.reason == "mysql database shop already exists"
    assert commands == []


async def test_database_creates_with_charset_and_collate():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, mysql.database, database="shop", charset="utf8mb4", collate="utf8mb4_bin"
    )
    assert ctx.changed
    assert commands == [
        MysqlDatabaseCommand(database="shop", charset="utf8mb4", collate="utf8mb4_bin")
    ]


async def test_database_alters_on_charset_drift():
    # The reemote5 extension over the baseline: charset/collate compare
    # against MysqlDatabases and render ALTER DATABASE on drift.
    host = SimulatedHost(
        name="h",
        mysql_databases={
            "shop": {"character_set": "utf8", "collation_name": "utf8_general_ci"}
        },
    )
    ctx, commands = await materialize(
        host, mysql.database, database="shop", charset="utf8mb4"
    )
    assert ctx.changed
    assert ctx.reason == "mysql database shop will be altered"
    assert commands == [
        MysqlDatabaseCommand(database="shop", alter=True, charset="utf8mb4")
    ]


async def test_database_drops_and_reports_an_absent_database():
    host = SimulatedHost(name="h", mysql_databases={"shop": dict(DB_ENTRY)})
    ctx, commands = await materialize(host, mysql.database, database="shop", present=False)
    assert ctx.changed
    assert ctx.reason == "mysql database shop will be dropped"
    assert commands == [MysqlDatabaseCommand(database="shop", present=False)]
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(host, mysql.database, database="shop", present=False)
    assert not ctx.changed
    assert ctx.reason == "mysql database shop does not exist"
    assert commands == []


async def test_database_add_privileges_baseline_parity():
    # The baseline's add_privileges fixture: CREATE DATABASE, GRANT ALL on the
    # database target, FLUSH PRIVILEGES.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, mysql.database, database="somedb", user="someuser", user_privileges="ALL"
    )
    assert ctx.changed
    assert ctx.reason == (
        "mysql database somedb will be created; privileges will be granted: ALL"
    )
    assert [cmd.shell() for cmd in commands] == [
        "mysql -Be 'CREATE DATABASE `somedb`'",
        'mysql -Be \'GRANT ALL ON `somedb`.* TO "someuser"@"localhost"\'',
        "mysql -Be 'FLUSH PRIVILEGES'",
    ]


# --- mysql.privileges (baseline parity) ---------------------------------------


async def test_privileges_add_delete_baseline_parity():
    # The baseline's add_delete fixture: extras revoked, missing granted,
    # sorted, then FLUSH PRIVILEGES.
    host = SimulatedHost(
        name="h",
        mysql_user_grants={"someuser@localhost": {"*.*": ["DELETE", "UPDATE"]}},
    )
    ctx, commands = await materialize(
        host, mysql.privileges, user="someuser", privileges=["SELECT", "INSERT", "DELETE"]
    )
    assert ctx.changed
    assert ctx.reason == (
        "privileges will be revoked: UPDATE; privileges will be granted: INSERT, SELECT"
    )
    assert [cmd.shell() for cmd in commands] == [
        'mysql -Be \'REVOKE UPDATE ON *.* FROM "someuser"@"localhost"\'',
        'mysql -Be \'GRANT INSERT, SELECT ON *.* TO "someuser"@"localhost"\'',
        "mysql -Be 'FLUSH PRIVILEGES'",
    ]


async def test_privileges_delete_all_with_grant_option_baseline_parity():
    # The ALL+GRANT OPTION pair revokes in two steps to keep the ON form.
    host = SimulatedHost(
        name="h",
        mysql_user_grants={
            "someuser@localhost": {"*.*": ["ALL PRIVILEGES", "GRANT OPTION"]}
        },
    )
    ctx, commands = await materialize(host, mysql.privileges, user="someuser", privileges=[])
    assert ctx.changed
    assert [cmd.shell() for cmd in commands] == [
        'mysql -Be \'REVOKE GRANT OPTION ON *.* FROM "someuser"@"localhost"\'',
        'mysql -Be \'REVOKE ALL PRIVILEGES ON *.* FROM "someuser"@"localhost"\'',
        "mysql -Be 'FLUSH PRIVILEGES'",
    ]


async def test_privileges_add_all_with_grant_option_baseline_parity():
    host = SimulatedHost(
        name="h", mysql_user_grants={"someuser@localhost": {"*.*": ["USAGE"]}}
    )
    ctx, commands = await materialize(
        host, mysql.privileges, user="someuser", privileges=["ALL"], with_grant_option=True
    )
    assert ctx.changed
    assert [cmd.shell() for cmd in commands] == [
        (
            'mysql -Be \'GRANT ALL PRIVILEGES ON *.* TO "someuser"@"localhost" '
            "WITH GRANT OPTION'"
        ),
        "mysql -Be 'FLUSH PRIVILEGES'",
    ]


async def test_privileges_table_without_database_is_an_operation_error():
    host = SimulatedHost(name="h")
    with pytest.raises(
        OperationError,
        match=r"Cannot apply MySQL privileges on \*\.`mytable`, no database provided",
    ):
        await materialize(
            host, mysql.privileges, user="someuser",
            privileges=["SELECT", "INSERT"], table="mytable",
        )


async def test_privileges_noop_when_already_correct():
    host = SimulatedHost(
        name="h",
        mysql_user_grants={"someuser@localhost": {"*.*": ["INSERT", "SELECT"]}},
    )
    ctx, commands = await materialize(
        host, mysql.privileges, user="someuser", privileges=["SELECT", "INSERT"]
    )
    assert not ctx.changed
    assert ctx.reason == "mysql privileges are already correct"
    assert commands == []


async def test_privileges_usage_is_not_revoked_when_granting():
    host = SimulatedHost(
        name="h", mysql_user_grants={"someuser@localhost": {"*.*": ["USAGE"]}}
    )
    ctx, commands = await materialize(
        host, mysql.privileges, user="someuser", privileges=["SELECT"]
    )
    assert ctx.changed
    assert [cmd.shell() for cmd in commands] == [
        'mysql -Be \'GRANT SELECT ON *.* TO "someuser"@"localhost"\'',
        "mysql -Be 'FLUSH PRIVILEGES'",
    ]


async def test_privileges_role_grants_are_excluded_from_the_diff():
    # Role membership captured by the fact is not a privilege: it is
    # never revoked by mysql.privileges.
    host = SimulatedHost(
        name="h",
        mysql_user_grants={"someuser@localhost": {"*.*": ["ROLE", "SELECT"]}},
    )
    ctx, commands = await materialize(
        host, mysql.privileges, user="someuser", privileges=["SELECT"]
    )
    assert not ctx.changed
    assert commands == []


async def test_privileges_flush_false_and_database_scope():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, mysql.privileges, user="app", privileges=["SELECT", "INSERT"],
        database="shop", flush=False,
    )
    assert ctx.changed
    assert commands == [
        MysqlPrivilegesCommand(
            action="GRANT", privileges=("INSERT", "SELECT"), database="`shop`",
            table="*", user="app", user_hostname="localhost",
        )
    ]


# --- mysql.user --------------------------------------------------------------


async def test_user_add_resources_baseline_parity():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, mysql.user, user="someuser", user_hostname="localwhat",
        max_connections=500, max_queries_per_hour=50,
        max_updates_per_hour=10, max_connections_per_hour=1,
    )
    assert ctx.changed
    assert ctx.reason == "mysql user someuser@localwhat will be created"
    assert [cmd.shell() for cmd in commands] == [
        (
            'mysql -Be \'CREATE USER "someuser"@"localwhat" WITH MAX_USER_CONNECTIONS 500 '
            "MAX_QUERIES_PER_HOUR 50 MAX_UPDATES_PER_HOUR 10 MAX_CONNECTIONS_PER_HOUR 1'"
        )
    ]


async def test_user_update_ssl_baseline_parity():
    host = SimulatedHost(
        name="h", mysql_users={"someuser@localhost": {"ssl_type": None}}
    )
    ctx, commands = await materialize(
        host, mysql.user, user="someuser", require="SSL"
    )
    assert ctx.changed
    assert ctx.reason == "mysql user someuser@localhost will be altered"
    assert [cmd.shell() for cmd in commands] == [
        'mysql -Be \'ALTER USER "someuser"@"localhost" REQUIRE SSL\''
    ]


async def test_user_creates_with_auth_plugin_instead_of_password():
    # The credentials decision: auth_plugin (IDENTIFIED WITH) replaces
    # The baseline's password option and IS comparable, so it alters on drift.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, mysql.user, user="app", auth_plugin="auth_socket"
    )
    assert ctx.changed
    assert commands == [MysqlUserCommand(user="app", auth_plugin="auth_socket")]
    host = SimulatedHost(name="h", mysql_users={"app@localhost": dict(USER_ENTRY)})
    ctx, commands = await materialize(
        host, mysql.user, user="app", auth_plugin="auth_socket"
    )
    assert ctx.changed
    assert ctx.reason == "mysql user app@localhost will be altered"
    assert commands == [
        MysqlUserCommand(user="app", alter=True, auth_plugin="auth_socket")
    ]


async def test_user_noop_when_all_options_match():
    host = SimulatedHost(name="h", mysql_users={"app@localhost": dict(USER_ENTRY)})
    ctx, commands = await materialize(host, mysql.user, user="app")
    assert not ctx.changed
    assert ctx.reason == "mysql user app@localhost already exists"
    assert commands == []
    # Resource limits compare against their mysql.user columns too.
    entry = {
        **USER_ENTRY, "max_user_connections": 5, "max_questions": 50,
        "max_updates": 10, "max_connections": 1,
    }
    host = SimulatedHost(name="h", mysql_users={"app@localhost": entry})
    ctx, commands = await materialize(
        host, mysql.user, user="app", max_connections=5, max_queries_per_hour=50,
        max_updates_per_hour=10, max_connections_per_hour=1,
    )
    assert not ctx.changed
    assert commands == []


async def test_user_drops_and_reports_an_absent_user():
    host = SimulatedHost(name="h", mysql_users={"app@localhost": dict(USER_ENTRY)})
    ctx, commands = await materialize(host, mysql.user, user="app", present=False)
    assert ctx.changed
    assert ctx.reason == "mysql user app@localhost will be dropped"
    assert commands == [MysqlUserCommand(user="app", present=False)]
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(host, mysql.user, user="app", present=False)
    assert not ctx.changed
    assert ctx.reason == "mysql user app@localhost does not exist"
    assert commands == []


async def test_user_require_validation():
    host = SimulatedHost(name="h")
    with pytest.raises(OperationError, match='Invalid `require` value'):
        await materialize(host, mysql.user, user="app", require="TLS")
    with pytest.raises(OperationError, match="require_cipher"):
        await materialize(host, mysql.user, user="app", require_cipher="CIPH")
    with pytest.raises(OperationError, match="require_issuer"):
        await materialize(host, mysql.user, user="app", require_issuer="ISS")
    with pytest.raises(OperationError, match="require_subject"):
        await materialize(host, mysql.user, user="app", require_subject="SUB")


async def test_user_privileges_bundle():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, mysql.user, user="app", privileges=["SELECT"]
    )
    assert ctx.changed
    assert ctx.reason == (
        "mysql user app@localhost will be created; privileges will be granted: SELECT"
    )
    assert [cmd.shell() for cmd in commands] == [
        'mysql -Be \'CREATE USER "app"@"localhost"\'',
        'mysql -Be \'GRANT SELECT ON *.* TO "app"@"localhost"\'',
        "mysql -Be 'FLUSH PRIVILEGES'",
    ]


# --- mysql.index -------------------------------------------------------------


async def test_index_creates_and_noops_on_match():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, mysql.index, database="shop", table="t", index="idx", columns=["a", "b"]
    )
    assert ctx.changed
    assert ctx.reason == "mysql index idx will be created"
    assert commands == [
        MysqlIndexCommand(database="shop", table="t", index="idx", columns=("a", "b"))
    ]
    host = SimulatedHost(name="h", mysql_indexes={"shop": [dict(INDEX_ENTRY)]})
    ctx, commands = await materialize(
        host, mysql.index, database="shop", table="t", index="idx", columns=["a", "b"]
    )
    assert not ctx.changed
    assert ctx.reason == "mysql index idx already exists"
    assert commands == []


async def test_index_drift_is_an_operation_error():
    # A drop/recreate of a possibly constraint-backing index is too
    # destructive to infer (the postgresql.index precedent).
    host = SimulatedHost(name="h", mysql_indexes={"shop": [dict(INDEX_ENTRY)]})
    with pytest.raises(OperationError, match="columns differ"):
        await materialize(
            host, mysql.index, database="shop", table="t", index="idx", columns=["a"]
        )
    with pytest.raises(OperationError, match="unique differ"):
        await materialize(
            host, mysql.index, database="shop", table="t", index="idx",
            columns=["a", "b"], unique=True,
        )


async def test_index_drops_and_reports_an_absent_index():
    host = SimulatedHost(name="h", mysql_indexes={"shop": [dict(INDEX_ENTRY)]})
    ctx, commands = await materialize(
        host, mysql.index, database="shop", table="t", index="idx", present=False
    )
    assert ctx.changed
    assert ctx.reason == "mysql index idx will be dropped"
    assert commands == [
        MysqlIndexCommand(database="shop", table="t", index="idx", present=False)
    ]
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, mysql.index, database="shop", table="t", index="idx", present=False
    )
    assert not ctx.changed
    assert ctx.reason == "mysql index idx does not exist"
    assert commands == []


async def test_index_validation_and_fulltext():
    host = SimulatedHost(name="h")
    with pytest.raises(ValueError, match="index_type must be"):
        await materialize(
            host, mysql.index, database="shop", table="t", index="idx",
            columns=["a"], index_type="RTREE",
        )
    with pytest.raises(ValueError, match="requires columns"):
        await materialize(host, mysql.index, database="shop", table="t", index="idx")
    ctx, commands = await materialize(
        host, mysql.index, database="shop", table="t", index="ft",
        columns=["body"], index_type="FULLTEXT",
    )
    assert ctx.changed
    assert commands == [
        MysqlIndexCommand(
            database="shop", table="t", index="ft", columns=("body",),
            index_type="FULLTEXT",
        )
    ]


# --- mysql.table -------------------------------------------------------------


async def test_table_creates_and_noops_on_existence_only():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, mysql.table, database="shop", table="t",
        columns=["id INT PRIMARY KEY"], engine="InnoDB",
    )
    assert ctx.changed
    assert ctx.reason == "mysql table shop.t will be created"
    assert commands == [
        MysqlTableCommand(
            database="shop", table="t", columns=("id INT PRIMARY KEY",), engine="InnoDB"
        )
    ]
    # Existence only: a different column list is NOT a drift (structure
    # is never compared -- mysql.sql is the migration escape hatch).
    host = SimulatedHost(name="h", mysql_tables={"shop": [dict(TABLES_FACT[0])]})
    ctx, commands = await materialize(
        host, mysql.table, database="shop", table="t", columns=["other TEXT"]
    )
    assert not ctx.changed
    assert ctx.reason == "mysql table shop.t already exists (structure is not compared)"
    assert commands == []


async def test_table_drops_and_reports_an_absent_table():
    host = SimulatedHost(name="h", mysql_tables={"shop": [dict(TABLES_FACT[0])]})
    ctx, commands = await materialize(
        host, mysql.table, database="shop", table="t", present=False
    )
    assert ctx.changed
    assert ctx.reason == "mysql table shop.t will be dropped"
    assert commands == [MysqlTableCommand(database="shop", table="t", present=False)]
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, mysql.table, database="shop", table="t", present=False
    )
    assert not ctx.changed
    assert ctx.reason == "mysql table shop.t does not exist"
    assert commands == []


async def test_table_requires_columns_when_present():
    host = SimulatedHost(name="h")
    with pytest.raises(ValueError, match="requires columns"):
        await materialize(host, mysql.table, database="shop", table="t")


# --- mysql.variable ----------------------------------------------------------


async def test_variable_sets_and_noops_on_text_match():
    host = SimulatedHost(name="h", mysql_variables={"max_connections": "151"})
    ctx, commands = await materialize(
        host, mysql.variable, variable="max_connections", value=500
    )
    assert ctx.changed
    assert ctx.reason == "mysql variable max_connections will be set to 500"
    assert commands == [MysqlVariableCommand(name="max_connections", value="500")]
    ctx, commands = await materialize(
        host, mysql.variable, variable="max_connections", value="151"
    )
    assert not ctx.changed
    assert ctx.reason == "mysql variable max_connections is already 151"
    assert commands == []


async def test_variable_persist_and_reset():
    host = SimulatedHost(name="h", mysql_variables={"sql_mode": ""})
    ctx, commands = await materialize(
        host, mysql.variable, variable="sql_mode", value="STRICT_TRANS_TABLES", persist=True
    )
    assert ctx.changed
    assert commands == [
        MysqlVariableCommand(name="sql_mode", value="STRICT_TRANS_TABLES", persist=True)
    ]
    # RESET PERSIST is stateless (the persisted-override set has no fact).
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, mysql.variable, variable="max_connections", present=False
    )
    assert ctx.changed
    assert ctx.reason == "mysql variable max_connections persisted override will be reset"
    assert commands == [MysqlVariableCommand(name="max_connections", present=False)]


async def test_variable_validation():
    host = SimulatedHost(name="h", mysql_variables={"max_connections": "151"})
    with pytest.raises(ValueError, match="unknown mysql system variable"):
        await materialize(host, mysql.variable, variable="not_a_variable", value="1")
    with pytest.raises(ValueError, match="requires value"):
        await materialize(host, mysql.variable, variable="max_connections")


async def test_variable_op_request_keeps_name_a_meta_parameter():
    # The option is ``variable``, not ``name``: op() consumes ``name`` as
    # the plan label and never forwards it, so an op()-built request must
    # still reach the operation's required ``variable`` parameter.
    host = SimulatedHost(name="h", mysql_variables={"max_connections": "151"})
    request = op(
        mysql.variable,
        name="Ensure max_connections is 300",
        variable="max_connections",
        value=300,
        persist=True,
        sudo=True,
    )
    assert request.name == "Ensure max_connections is 300"
    assert "name" not in request.kwargs
    ctx, commands = await materialize(host, request.func, **request.kwargs)
    assert ctx.changed
    assert commands == [
        MysqlVariableCommand(name="max_connections", value="300", persist=True)
    ]


# --- mysql.plugin ------------------------------------------------------------


async def test_plugin_installs_and_noops_on_same_library():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, mysql.plugin, plugin="validate_password", soname="validate_password.so"
    )
    assert ctx.changed
    assert ctx.reason == "mysql plugin validate_password will be installed"
    assert commands == [
        MysqlPluginCommand(plugin="validate_password", soname="validate_password.so")
    ]
    host = SimulatedHost(name="h", mysql_plugins=[dict(PLUGIN_ENTRY)])
    ctx, commands = await materialize(
        host, mysql.plugin, plugin="validate_password", soname="validate_password.so"
    )
    assert not ctx.changed
    assert ctx.reason == "mysql plugin validate_password is already installed"
    assert commands == []


async def test_plugin_library_drift_and_builtin_uninstall_are_errors():
    host = SimulatedHost(name="h", mysql_plugins=[dict(PLUGIN_ENTRY)])
    with pytest.raises(OperationError, match="already installed from validate_password.so"):
        await materialize(
            host, mysql.plugin, plugin="validate_password", soname="other.so"
        )
    builtin = {**PLUGIN_ENTRY, "name": "InnoDB", "library": None}
    host = SimulatedHost(name="h", mysql_plugins=[builtin])
    with pytest.raises(OperationError, match="built-in and cannot be uninstalled"):
        await materialize(host, mysql.plugin, plugin="InnoDB", present=False)


async def test_plugin_uninstalls_and_reports_an_absent_plugin():
    host = SimulatedHost(name="h", mysql_plugins=[dict(PLUGIN_ENTRY)])
    ctx, commands = await materialize(
        host, mysql.plugin, plugin="validate_password", present=False
    )
    assert ctx.changed
    assert ctx.reason == "mysql plugin validate_password will be uninstalled"
    assert commands == [MysqlPluginCommand(plugin="validate_password", present=False)]
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, mysql.plugin, plugin="validate_password", present=False
    )
    assert not ctx.changed
    assert ctx.reason == "mysql plugin validate_password is not installed"
    assert commands == []


async def test_plugin_requires_soname_when_present():
    host = SimulatedHost(name="h")
    with pytest.raises(ValueError, match="requires soname"):
        await materialize(host, mysql.plugin, plugin="validate_password")


# --- mysql.replica -----------------------------------------------------------


async def test_replica_starts_stopped_threads_and_noops_running():
    host = SimulatedHost(name="h", mysql_replication=[dict(REPLICA_STOPPED)])
    ctx, commands = await materialize(host, mysql.replica)
    assert ctx.changed
    assert ctx.reason == "mysql replica threads will be started"
    assert commands == [MysqlReplicaCommand(running=True)]
    host = SimulatedHost(name="h", mysql_replication=[dict(REPLICATION_FACT[0])])
    ctx, commands = await materialize(host, mysql.replica)
    assert not ctx.changed
    assert ctx.reason == "mysql replica threads are already running"
    assert commands == []


async def test_replica_stops_running_threads_and_noops_stopped():
    host = SimulatedHost(name="h", mysql_replication=[dict(REPLICATION_FACT[0])])
    ctx, commands = await materialize(host, mysql.replica, running=False)
    assert ctx.changed
    assert ctx.reason == "mysql replica threads will be stopped"
    assert commands == [MysqlReplicaCommand(running=False)]
    host = SimulatedHost(name="h", mysql_replication=[dict(REPLICA_STOPPED)])
    ctx, commands = await materialize(host, mysql.replica, running=False)
    assert not ctx.changed
    assert ctx.reason == "mysql replica threads are already stopped"
    assert commands == []


async def test_replica_unconfigured_channel_is_an_operation_error():
    host = SimulatedHost(name="h")
    with pytest.raises(OperationError, match="not configured on the default channel"):
        await materialize(host, mysql.replica)
    with pytest.raises(OperationError, match="not configured on channel ch1"):
        await materialize(host, mysql.replica, channel="ch1")


async def test_replica_addresses_a_named_channel_and_resets_statelessly():
    ch1 = {**REPLICA_STOPPED, "channel": "ch1"}
    host = SimulatedHost(name="h", mysql_replication=[ch1])
    ctx, commands = await materialize(host, mysql.replica, channel="ch1")
    assert ctx.changed
    assert commands == [MysqlReplicaCommand(running=True, channel="ch1")]
    # reset is stateless: it yields even with no configured channels.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(host, mysql.replica, reset=True)
    assert ctx.changed
    assert ctx.reason == "mysql replication will be stopped and reset"
    assert commands == [MysqlReplicaCommand(reset=True)]


# --- mysql.role --------------------------------------------------------------


async def test_role_creates_and_noops_when_present():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(host, mysql.role, role="ro")
    assert ctx.changed
    assert ctx.reason == "mysql role ro@% will be created"
    assert commands == [MysqlRoleCommand(role="ro")]
    host = SimulatedHost(name="h", mysql_users={"ro@%": {"is_role": True}})
    ctx, commands = await materialize(host, mysql.role, role="ro")
    assert not ctx.changed
    assert ctx.reason == "mysql role ro@% already exists"
    assert commands == []


async def test_role_drops_and_reports_an_absent_role():
    host = SimulatedHost(name="h", mysql_users={"ro@%": {"is_role": True}})
    ctx, commands = await materialize(host, mysql.role, role="ro", present=False)
    assert ctx.changed
    assert ctx.reason == "mysql role ro@% will be dropped"
    assert commands == [MysqlRoleCommand(role="ro", present=False)]
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(host, mysql.role, role="ro", present=False)
    assert not ctx.changed
    assert ctx.reason == "mysql role ro@% does not exist"
    assert commands == []


async def test_role_grants_missing_memberships_only():
    host = SimulatedHost(
        name="h",
        mysql_users={"ro@%": {"is_role": True}, "app@localhost": dict(USER_ENTRY)},
        mysql_user_grants={"app@localhost": {}},
    )
    ctx, commands = await materialize(
        host, mysql.role, role="ro", members=["app@localhost"]
    )
    assert ctx.changed
    assert ctx.reason == (
        "mysql role ro@% already exists; mysql role ro@% will be granted to app@localhost"
    )
    assert commands == [MysqlRoleCommand(role="ro", member="app")]
    # An existing membership (the fact's ROLE key) is a no-op.
    host = SimulatedHost(
        name="h",
        mysql_users={"ro@%": {"is_role": True}},
        mysql_user_grants={"app@localhost": {"`ro`@`%`": ["ROLE"]}},
    )
    ctx, commands = await materialize(
        host, mysql.role, role="ro", members=["app@localhost"]
    )
    assert not ctx.changed
    assert ctx.reason == "mysql role ro@% already exists"
    assert commands == []


async def test_role_create_with_members_and_sorted_order():
    # A freshly created role cannot have been granted yet: no grants
    # fact read is needed, and members render in sorted order.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, mysql.role, role="ro", members=["z@h", "a@h"]
    )
    assert ctx.changed
    assert commands == [
        MysqlRoleCommand(role="ro"),
        MysqlRoleCommand(role="ro", member="a", member_hostname="h"),
        MysqlRoleCommand(role="ro", member="z", member_hostname="h"),
    ]


async def test_role_members_must_be_user_at_host():
    host = SimulatedHost(name="h")
    with pytest.raises(ValueError, match="must be user@host"):
        await materialize(host, mysql.role, role="ro", members=["app"])


# --- mysql.kill / mysql.optimize / mysql.sql / mysql.dump / mysql.load -------


async def test_kill_is_stateless_in_both_forms():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(host, mysql.kill, process_id=7)
    assert ctx.changed
    assert ctx.reason == "mysql connection 7 will be killed"
    assert commands == [MysqlKillCommand(process_id=7)]
    ctx, commands = await materialize(host, mysql.kill, process_id=7, query_only=True)
    assert ctx.changed
    assert ctx.reason == "mysql query on connection 7 will be killed"
    assert commands == [MysqlKillCommand(process_id=7, query_only=True)]


async def test_optimize_is_stateless_and_validates():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, mysql.optimize, database="shop", tables=["a", "b"]
    )
    assert ctx.changed
    assert ctx.reason == "mysql optimize will run on 2 table(s) in shop"
    assert commands == [MysqlOptimizeCommand(database="shop", tables=("a", "b"))]
    with pytest.raises(ValueError, match="action must be"):
        await materialize(host, mysql.optimize, database="shop", tables=["a"], action="vacuum")
    with pytest.raises(ValueError, match="at least one table"):
        await materialize(host, mysql.optimize, database="shop", tables=[])


async def test_sql_dump_load_are_stateless():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(host, mysql.sql, sql="SELECT 1", database="shop")
    assert ctx.changed
    assert ctx.reason == "mysql SQL will be executed"
    assert commands == [MysqlSqlCommand(sql="SELECT 1", mysql_database="shop")]
    ctx, commands = await materialize(
        host, mysql.dump, dest="/tmp/shop.sql", database="shop"
    )
    assert ctx.changed
    assert ctx.reason == "mysql dump will be written to /tmp/shop.sql"
    assert commands == [MysqlDumpCommand(dest="/tmp/shop.sql", mysql_database="shop")]
    ctx, commands = await materialize(
        host, mysql.load, src="/tmp/shop.sql", database="shop"
    )
    assert ctx.changed
    assert ctx.reason == "mysql load will read /tmp/shop.sql"
    assert commands == [MysqlLoadCommand(src="/tmp/shop.sql", mysql_database="shop")]


async def test_operations_absorb_extra_global_arguments():
    # **kwargs absorbs the baseline's global arguments (op() already strips
    # name/sudo/su); a dropped credential never reaches the shell.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, mysql.sql, sql="SELECT 1", mysql_password="secret", unknown_global=True
    )
    assert ctx.changed
    assert commands[0].shell() == "mysql -Be 'SELECT 1'"
    assert "secret" not in commands[0].shell()# --- SimulatedHost seam mutations (send_command) ------------------------------


async def test_seam_database_create_alter_drop_cascades():
    host = SimulatedHost(
        name="h",
        mysql_user_grants={
            "app@localhost": {"`shop`.*": ["SELECT"], "*.*": ["USAGE"]}
        },
    )
    await send_command(host, MysqlDatabaseCommand(database="shop"))
    assert host.mysql_databases == {"shop": DB_ENTRY}
    await send_command(
        host, MysqlDatabaseCommand(database="shop", alter=True, collate="utf8mb4_bin")
    )
    assert host.mysql_databases["shop"] == {
        "character_set": "utf8mb4", "collation_name": "utf8mb4_bin"
    }
    # DROP DATABASE destroys what the server destroys: the database's
    # tables, indexes, columns and every grant target scoped to it.
    host.mysql_tables["shop"] = [dict(TABLES_FACT[0])]
    host.mysql_indexes["shop"] = [dict(INDEX_ENTRY)]
    host.mysql_columns["shop.t"] = [dict(COLUMNS_FACT[0])]
    await send_command(host, MysqlDatabaseCommand(database="shop", present=False))
    assert host.mysql_databases == {}
    assert "shop" not in host.mysql_tables
    assert "shop" not in host.mysql_indexes
    assert "shop.t" not in host.mysql_columns
    assert host.mysql_user_grants["app@localhost"] == {"*.*": ["USAGE"]}


async def test_seam_user_create_alter_drop():
    host = SimulatedHost(name="h")
    await send_command(
        host,
        MysqlUserCommand(user="app", auth_plugin="auth_socket", require="SSL", max_connections=5),
    )
    entry = host.mysql_users["app@localhost"]
    assert entry["plugin"] == "auth_socket"
    assert entry["ssl_type"] == "ANY"
    assert entry["max_user_connections"] == 5
    assert entry["is_role"] is False
    await send_command(host, MysqlUserCommand(user="app", alter=True, require="X509"))
    assert host.mysql_users["app@localhost"]["ssl_type"] == "X509"
    # DROP USER pops the account and its grants.
    host.mysql_user_grants["app@localhost"] = {"*.*": ["SELECT"]}
    await send_command(host, MysqlUserCommand(user="app", present=False))
    assert "app@localhost" not in host.mysql_users
    assert "app@localhost" not in host.mysql_user_grants


async def test_seam_privileges_grant_revoke_normalization():
    host = SimulatedHost(
        name="h", mysql_user_grants={"app@localhost": {"*.*": ["USAGE"]}}
    )
    await send_command(
        host,
        MysqlPrivilegesCommand(
            action="GRANT", privileges=("SELECT",), database="*", table="*",
            user="app", user_hostname="localhost",
        ),
    )
    # Granting real privileges overrides USAGE, exactly as the server does.
    assert host.mysql_user_grants["app@localhost"]["*.*"] == ["SELECT"]
    await send_command(
        host,
        MysqlPrivilegesCommand(
            action="GRANT", privileges=("ALL PRIVILEGES",), database="*", table="*",
            user="app", user_hostname="localhost", with_statement="GRANT OPTION",
        ),
    )
    assert host.mysql_user_grants["app@localhost"]["*.*"] == [
        "ALL PRIVILEGES", "GRANT OPTION", "SELECT"
    ]
    # REVOKE ALL PRIVILEGES keeps GRANT OPTION (and role memberships).
    await send_command(
        host,
        MysqlPrivilegesCommand(
            action="REVOKE", privileges=("ALL PRIVILEGES",), database="*", table="*",
            user="app", user_hostname="localhost",
        ),
    )
    assert host.mysql_user_grants["app@localhost"]["*.*"] == ["GRANT OPTION"]
    await send_command(
        host,
        MysqlPrivilegesCommand(
            action="REVOKE", privileges=("GRANT OPTION",), database="*", table="*",
            user="app", user_hostname="localhost",
        ),
    )
    # An empty target is popped, not stored as [].
    assert host.mysql_user_grants["app@localhost"] == {}


async def test_seam_role_membership_round_trip():
    host = SimulatedHost(name="h", mysql_users={"app@localhost": dict(USER_ENTRY)})
    await send_command(host, MysqlRoleCommand(role="ro"))
    role = host.mysql_users["ro@%"]
    assert role["is_role"] is True
    assert role["account_locked"] is True
    assert role["password_expired"] is True
    await send_command(host, MysqlRoleCommand(role="ro", member="app"))
    assert host.mysql_user_grants["app@localhost"]["`ro`@`%`"] == ["ROLE"]
    # The operation's idempotence check reads the same key back.
    ctx, commands = await materialize(
        host, mysql.role, role="ro", members=["app@localhost"]
    )
    assert not ctx.changed
    assert commands == []
    # DROP ROLE revokes every membership server-side.
    await send_command(host, MysqlRoleCommand(role="ro", present=False))
    assert "ro@%" not in host.mysql_users
    assert "`ro`@`%`" not in host.mysql_user_grants["app@localhost"]


async def test_seam_table_create_drop_cascades():
    host = SimulatedHost(name="h")
    await send_command(
        host,
        MysqlTableCommand(database="shop", table="t", columns=("id INT",), comment="items"),
    )
    assert host.mysql_tables["shop"] == [
        {
            "name": "t", "engine": "InnoDB", "rows": 0, "data_length": 0,
            "index_length": 0, "collation": "utf8mb4_0900_ai_ci", "comment": "items",
        }
    ]
    # The column definitions are free SQL text the simulation does not
    # parse, so mysql_columns is NOT seeded by a create.
    assert "shop.t" not in host.mysql_columns
    host.mysql_indexes["shop"] = [dict(INDEX_ENTRY)]
    host.mysql_columns["shop.t"] = [dict(COLUMNS_FACT[0])]
    await send_command(host, MysqlTableCommand(database="shop", table="t", present=False))
    assert host.mysql_tables["shop"] == []
    assert host.mysql_indexes["shop"] == []
    assert "shop.t" not in host.mysql_columns


async def test_seam_index_create_drop():
    host = SimulatedHost(name="h")
    await send_command(
        host,
        MysqlIndexCommand(database="shop", table="t", index="idx", columns=("a", "b")),
    )
    assert host.mysql_indexes["shop"] == [INDEX_ENTRY]
    await send_command(
        host, MysqlIndexCommand(database="shop", table="t", index="idx", present=False)
    )
    assert host.mysql_indexes["shop"] == []


async def test_seam_variable_set_and_reset():
    host = SimulatedHost(name="h", mysql_variables={"max_connections": "151"})
    await send_command(host, MysqlVariableCommand(name="max_connections", value="500"))
    assert host.mysql_variables["max_connections"] == "500"
    # RESET PERSIST leaves the runtime value untouched (it keeps until
    # restart either way; the persisted-override set is not modelled).
    await send_command(host, MysqlVariableCommand(name="max_connections", present=False))
    assert host.mysql_variables["max_connections"] == "500"


async def test_seam_plugin_install_uninstall():
    host = SimulatedHost(name="h")
    await send_command(
        host, MysqlPluginCommand(plugin="validate_password", soname="validate_password.so")
    )
    assert host.mysql_plugins == [
        {
            "name": "validate_password", "status": "ACTIVE", "type": "",
            "library": "validate_password.so", "license": "GPL",
        }
    ]
    await send_command(host, MysqlPluginCommand(plugin="validate_password", present=False))
    assert host.mysql_plugins == []


async def test_seam_replica_start_stop_reset():
    errored = {**REPLICA_STOPPED, "last_error": "Error text"}
    host = SimulatedHost(name="h", mysql_replication=[dict(errored)])
    await send_command(host, MysqlReplicaCommand(running=True))
    entry = host.mysql_replication[0]
    assert entry["io_running"] == "Yes"
    assert entry["sql_running"] == "Yes"
    assert entry["last_error"] == ""
    assert entry["seconds_behind_source"] == 0
    await send_command(host, MysqlReplicaCommand(running=False))
    assert host.mysql_replication[0]["io_running"] == "No"
    assert host.mysql_replication[0]["seconds_behind_source"] is None
    await send_command(host, MysqlReplicaCommand(reset=True))
    assert host.mysql_replication == []


async def test_seam_kill_connection_and_query():
    conn = {
        "id": 7, "user": "app", "host": "localhost:51234", "database": "shop",
        "command": "Query", "time": 12, "state": "Sending data",
    }
    host = SimulatedHost(name="h", mysql_connections=[dict(conn)])
    await send_command(host, MysqlKillCommand(process_id=7, query_only=True))
    # KILL QUERY keeps the connection, idling.
    assert host.mysql_connections == [
        {**conn, "command": "Sleep", "time": 0, "state": None}
    ]
    await send_command(host, MysqlKillCommand(process_id=7))
    assert host.mysql_connections == []


async def test_seam_dump_marker_and_stateless_pass_arms():
    host = SimulatedHost(name="h")
    await send_command(host, MysqlDumpCommand(dest="/tmp/shop.sql", mysql_database="shop"))
    assert host.remote_files["/tmp/shop.sql"] == b"-- simulated mysqldump of shop\n"
    await send_command(host, MysqlDumpCommand(dest="/tmp/all.sql"))
    assert host.remote_files["/tmp/all.sql"] == (
        b"-- simulated mysqldump of an unspecified database\n"
    )
    # load/sql/optimize execute no modelled SQL: they pass without a
    # state change (and an unknown descriptor stays a hard TypeError).
    await send_command(host, MysqlLoadCommand(src="/tmp/shop.sql", mysql_database="shop"))
    await send_command(host, MysqlSqlCommand(sql="FLUSH PRIVILEGES"))
    await send_command(host, MysqlOptimizeCommand(database="shop", tables=("t",)))
    assert host.mysql_databases == {}


# --- end to end (run / run_prepare + run_apply) -------------------------------


async def test_mysql_database_end_to_end_create_then_noop():
    # Through run(): the first apply creates the database into the
    # simulated state (elevated -- mysql runs as root via socket auth);
    # a second run reads it back and is a no-op.
    def deploy(host):
        yield op(mysql.database, name="Create the shop database", database="shop", sudo=True)

    host = SimulatedHost(name="h")

    def factory(entry):
        return host  # reuse ONE host so the second run sees the first's create

    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], deploy, host_factory=factory
    )
    assert plans["h"][0].changed
    (entry,) = report.log
    assert entry.changed
    assert "shop" in host.mysql_databases
    assert "$ sudo mysql -Be 'CREATE DATABASE `shop`'" in format_log(report)

    _plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], deploy, host_factory=factory
    )
    (entry2,) = report2.log
    assert not entry2.changed
    assert entry2.reason == "mysql database shop already exists"


async def test_mysql_dry_run_sends_nothing():
    def deploy(host):
        yield op(mysql.database, name="Create the shop database", database="shop")

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        deploy,
        dry=True,
        host_factory=lambda entry: host,
    )
    assert report is None
    assert plans["h"][0].changed
    assert plans["h"][0].commands == (MysqlDatabaseCommand(database="shop"),)
    assert host.mysql_databases == {}
    assert "changed" in format_plan(plans)


async def test_mysql_create_then_noop_in_one_deploy():
    # The prepare plan (which never sends) shows both identical creates
    # as changes; the apply phase's fresh per-step fact cache sees the
    # first create, so the second is a no-op in the log.
    def deploy(host):
        yield op(mysql.database, name="create", database="shop")
        yield op(mysql.database, name="re-create", database="shop")

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], deploy)
    assert plans["h"][0].changed
    assert plans["h"][1].changed  # prepare never sends, so still absent
    report = await run_apply(runtimes, plans=plans)
    first, second = report.log
    assert first.changed
    assert not second.changed
    assert second.reason == "mysql database shop already exists"
    assert host.mysql_databases == {"shop": DB_ENTRY}


async def test_mysql_determinism_violation_is_a_hard_error():
    # Branching the deploy on a mutable seam field diverges between the
    # prepare and apply request sequences -> DeterminismError.
    def nondeterministic_deploy(host):
        yield op(mysql.database, name="Create shop", database="shop")
        if "shop" in host.mysql_databases:  # mutable-fact branch: forbidden
            yield op(
                mysql.table, name="t", database="shop", table="t", columns=("id INT",)
            )
        else:
            yield op(mysql.sql, name="noop", sql="SELECT 1")

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], nondeterministic_deploy)
    with pytest.raises(DeterminismError, match="diverges"):
        await run_apply(runtimes, plans=plans)


async def test_mysql_provisioning_pipeline_in_lockstep():
    # user -> database (with the privileges bundle) -> table -> index,
    # each step's apply seeing the previous step's mutation through the
    # fresh fact cache: every step reports a change.
    def deploy(host):
        yield op(
            mysql.user, name="Create the app user", user="app",
            auth_plugin="auth_socket", sudo=True,
        )
        yield op(
            mysql.database, name="Create the shop database", database="shop",
            user="app", user_privileges=("SELECT", "INSERT"), sudo=True,
        )
        yield op(
            mysql.table, name="Create the items table", database="shop", table="t",
            columns=("id INT PRIMARY KEY", "name VARCHAR(50)"), sudo=True,
        )
        yield op(
            mysql.index, name="Index the name column", database="shop", table="t",
            index="idx_name", columns=("name",), sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], deploy)
    assert [p.changed for p in plans["h"]] == [True, True, True, True]
    report = await run_apply(runtimes, plans=plans)
    assert [entry.changed for entry in report.log] == [True, True, True, True]
    assert host.mysql_users["app@localhost"]["plugin"] == "auth_socket"
    assert host.mysql_databases["shop"] == DB_ENTRY
    assert host.mysql_user_grants["app@localhost"]["`shop`.*"] == ["INSERT", "SELECT"]
    assert host.mysql_tables["shop"][0]["name"] == "t"
    assert host.mysql_indexes["shop"][0]["name"] == "idx_name"


async def test_gather_fact_fans_out_across_hosts():
    hosts = [
        SimulatedHost(name="web1", mysql_variables={"max_connections": "151"}),
        SimulatedHost(name="web2", mysql_variables={"max_connections": "300"}),
    ]
    result = await gather_fact(hosts, MysqlVariables)
    assert set(result) == {"web1", "web2"}
    assert result["web1"] == {"max_connections": "151"}
    assert result["web2"] == {"max_connections": "300"}


# --- exports ------------------------------------------------------------------


MYSQL_EXPORTS = (
    "MysqlBinaryLog", "MysqlBinaryLogs", "MysqlColumn", "MysqlColumns",
    "MysqlConnection", "MysqlConnections", "MysqlDatabase", "MysqlDatabaseCommand",
    "MysqlDatabases", "MysqlDumpCommand", "MysqlEngine", "MysqlEngines",
    "MysqlIndex", "MysqlIndexCommand", "MysqlIndexes", "MysqlKillCommand",
    "MysqlLoadCommand", "MysqlOptimizeCommand", "MysqlPlugin", "MysqlPluginCommand",
    "MysqlPlugins", "MysqlPrivilegesCommand", "MysqlReplicaCommand",
    "MysqlReplicaStatus", "MysqlReplication", "MysqlRoleCommand", "MysqlSqlCommand",
    "MysqlStatus", "MysqlTable", "MysqlTableCommand", "MysqlTables", "MysqlUser",
    "MysqlUserCommand", "MysqlUserGrants", "MysqlUsers", "MysqlVariableCommand",
    "MysqlVariables",
)


def test_mysql_top_level_exports():
    # Facts, entry TypedDicts and descriptors re-export at the top level
    # (13 + 10 + 14 = 37 names); operations are reached through the
    # mysql namespace only.
    assert len(MYSQL_EXPORTS) == 37
    for name in MYSQL_EXPORTS:
        assert name in reemote5.__all__
        assert getattr(reemote5, name) is getattr(mysql, name)
    assert "mysql" in reemote5.__all__
    for op_name in (
        "database", "dump", "index", "kill", "load", "optimize", "plugin",
        "privileges", "replica", "role", "sql", "table", "user", "variable",
    ):
        assert callable(getattr(mysql, op_name))