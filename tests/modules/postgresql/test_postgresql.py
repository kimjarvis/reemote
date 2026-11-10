# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the postgresql module: the fourteen ``Postgresql*`` facts and
the ``database``/``role``/``sql``/``dump``/``load``/``privilege``/
``membership``/``schema``/``extension``/``index``/``setting``/
``tablespace``/``publication``/``subscription`` operations.

Covers ``reemote5.modules.postgresql.postgresql``. ``PostgresqlRoles`` and
``PostgresqlDatabases`` mirror the baseline's ``postgres.PostgresRoles`` /
``PostgresDatabases``, so their real-transport parity reuses the baseline's own
fact fixtures verbatim (canned ``psql -Ac`` output plus the name-keyed
result), including the custom-connection command render (minus the
``PGPASSWORD`` prefix the namespace deliberately drops). The twelve
reemote5-original facts have no upstream fixture, so they are exercised
with realistic canned ``psql -Ac`` output plus their parsing edge cases
(empty output, the ``(N rows)`` trailer, t/f bool coercion, the
``text[]`` array parse, and the credential-free column omissions).

Every fact also reads its ``SimulatedHost`` seam (cluster-wide entry
lists / name-keyed dicts, or database-keyed entry lists for the
database-scoped facts) with copy-on-read semantics, and the shared
``psql`` command renderer is checked for flag order and ``shlex`` quoting.
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
from reemote5.modules import postgresql
from reemote5.modules.postgresql.postgresql import (
    PostgresqlConnections,
    PostgresqlDatabaseCommand,
    PostgresqlDatabases,
    PostgresqlDumpCommand,
    PostgresqlExtensionCommand,
    PostgresqlExtensions,
    PostgresqlIndexCommand,
    PostgresqlIndexes,
    PostgresqlLoadCommand,
    PostgresqlMembershipCommand,
    PostgresqlPrivilegeCommand,
    PostgresqlPrivileges,
    PostgresqlPublicationCommand,
    PostgresqlPublications,
    PostgresqlReplication,
    PostgresqlRoleCommand,
    PostgresqlRoleMemberships,
    PostgresqlRoles,
    PostgresqlSchemaCommand,
    PostgresqlSchemas,
    PostgresqlSettingCommand,
    PostgresqlSettings,
    PostgresqlSqlCommand,
    PostgresqlSubscriptionCommand,
    PostgresqlSubscriptions,
    PostgresqlTables,
    PostgresqlTablespaceCommand,
    PostgresqlTablespaces,
    _parse_array,
    _parse_rows,
    _psql_sql,
    _quote_ident,
    _quote_literal,
    _quote_qualified,
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


# --- shared helpers ----------------------------------------------------------


def test_parse_rows_empty_output_is_empty_list():
    # The hardened deviation from the baseline: empty stdout parses to [] rather
    # than raising on an unconditional last-line chop.
    assert _parse_rows([]) == []
    # A lone blank line is a header with no rows, still [].
    assert _parse_rows([""]) == []


def test_parse_rows_drops_the_row_count_trailer():
    # Both the plural ``(N rows)`` and singular ``(1 row)`` trailers are
    # dropped by pattern, not by chopping the last line unconditionally.
    lines = ["name|oid", "alice|10", "(1 row)"]
    assert _parse_rows(lines) == [{"name": "alice", "oid": "10"}]
    lines = ["name|oid", "alice|10", "bob|11", "(2 rows)"]
    assert _parse_rows(lines) == [
        {"name": "alice", "oid": "10"},
        {"name": "bob", "oid": "11"},
    ]


def test_parse_rows_strips_the_column_prefix():
    lines = ["rolname|rolsuper", "postgres|t", "(1 row)"]
    assert _parse_rows(lines, remove_prefix="rol") == [
        {"name": "postgres", "super": "t"}
    ]


def test_parse_rows_empty_field_reads_none():
    lines = ["a|b|c", "x||z"]
    assert _parse_rows(lines) == [{"a": "x", "b": None, "c": "z"}]


def test_parse_array_literals():
    assert _parse_array(None) == []
    assert _parse_array("{}") == []
    assert _parse_array("{a}") == ["a"]
    assert _parse_array("{a,b,c}") == ["a", "b", "c"]


def test_quote_ident_doubles_embedded_quotes():
    assert _quote_ident("plain") == '"plain"'
    assert _quote_ident('we"ird') == '"we""ird"'


def test_quote_qualified_splits_on_dots():
    assert _quote_qualified("public.mytable") == '"public"."mytable"'
    assert _quote_qualified("bare") == '"bare"'


def test_quote_literal_doubles_single_quotes():
    assert _quote_literal("plain") == "'plain'"
    assert _quote_literal("it's") == "'it''s'"


def test_psql_sql_flag_order_and_quoting():
    # The baseline's flag order: -d database, -U user, -h host, -p port. Plain
    # values quote to themselves, so ordinary names keep command parity.
    assert _psql_sql("SELECT 1") == "psql -Ac 'SELECT 1'"
    assert _psql_sql(
        "SELECT 1",
        psql_database="mydb",
        psql_user="myuser",
        psql_host="myhost",
        psql_port=5432,
    ) == "psql -d mydb -U myuser -h myhost -p 5432 -Ac 'SELECT 1'"
    # A value carrying shell metacharacters is quoted literally.
    assert _psql_sql("SELECT 1", psql_database="my db") == (
        "psql -d 'my db' -Ac 'SELECT 1'"
    )


# --- PostgresqlRoles (baseline parity) ----------------------------------------


ROLES_OUTPUT = """\
rolname|rolsuper|rolinherit|rolcreaterole|rolcreatedb|rolcanlogin|rolreplication|rolconnlimit|rolpassword|rolvaliduntil|rolbypassrls|rolconfig|oid
postgres|t|t|t|t|t|t|-1|********||t||10
reemote5|t|t|f|f|t|f|-1|********||f||16384
(7 rows)
"""

ROLES_FACT = {
    "reemote5": {
        "super": True,
        "inherit": True,
        "createrole": False,
        "createdb": False,
        "canlogin": True,
        "replication": False,
        "connlimit": -1,
        "password": "********",
        "validuntil": None,
        "bypassrls": False,
        "config": None,
        "oid": 16384,
    },
    "postgres": {
        "super": True,
        "inherit": True,
        "createrole": True,
        "createdb": True,
        "canlogin": True,
        "replication": True,
        "connlimit": -1,
        "password": "********",
        "validuntil": None,
        "bypassrls": True,
        "config": None,
        "oid": 10,
    },
}


async def test_roles_parses_baseline_fixture():
    # The baseline's own fact fixture (tests/facts/postgresql.PostgresqlRoles,
    # multiple.json): the rendered command matches verbatim and the
    # name-keyed result equals the baseline's fact dict.
    host = CannedShellHost(ROLES_OUTPUT)
    assert await PostgresqlRoles.process(host) == ROLES_FACT
    assert host.shells == ["psql -Ac 'SELECT * FROM pg_catalog.pg_roles'"]


async def test_roles_custom_connection_drops_the_password_prefix():
    # The baseline's multiple_custom_connection.json renders a PGPASSWORD=...
    # prefix; reemote5 drops it (the namespace credentials decision) but
    # keeps the -U/-h/-p flag order and parses the same result.
    host = CannedShellHost(ROLES_OUTPUT)
    assert (
        await PostgresqlRoles.process(
            host, psql_user="myuser", psql_host="myhost", psql_port=5432
        )
        == ROLES_FACT
    )
    assert host.shells == [
        "psql -U myuser -h myhost -p 5432 -Ac 'SELECT * FROM pg_catalog.pg_roles'"
    ]


def test_roles_parse_empty_output():
    assert PostgresqlRoles.parse([]) == {}


async def test_roles_reads_the_simulated_seam():
    host = SimulatedHost(name="h", postgres_roles=ROLES_FACT)
    assert await PostgresqlRoles.process(host) == ROLES_FACT
    # Copy-on-read: mutating the returned entry does not touch the seam.
    result = await PostgresqlRoles.process(host)
    result["postgres"]["super"] = False
    assert host.postgres_roles["postgres"]["super"] is True
    # The default seam is {} -- a simulated cluster with no roles listed.
    assert await PostgresqlRoles.process(SimulatedHost(name="h")) == {}


async def test_roles_gathers_across_hosts():
    hosts = [
        SimulatedHost(name="web1", postgres_roles={"a": ROLES_FACT["postgres"]}),
        SimulatedHost(name="web2", postgres_roles={"b": ROLES_FACT["reemote5"]}),
    ]
    result = await gather_fact(hosts, PostgresqlRoles)
    assert set(result) == {"web1", "web2"}
    assert result["web1"] == {"a": ROLES_FACT["postgres"]}


# --- PostgresqlDatabases (baseline parity) ------------------------------------


DATABASES_OUTPUT = """\
pg_encoding_to_char|datname|datdba|encoding|datcollate|datctype|datistemplate|datallowconn|datconnlimit|datlastsysoid|datfrozenxid|datminmxid|dattablespace|datacl|owner
UTF8|postgres|10|6|en_US.UTF-8|en_US.UTF-8|f|t|-1|13052|548|1|1663||postgres
UTF8|baseline_stuff|16384|6|en_US.UTF-8|en_US.UTF-8|f|t|-1|13052|548|1|1663||reemote5
(4 rows)
"""

DATABASES_FACT = {
    "postgres": {
        "dba": 10,
        "encoding": "UTF8",
        "collate": "en_US.UTF-8",
        "ctype": "en_US.UTF-8",
        "istemplate": False,
        "allowconn": True,
        "connlimit": -1,
        "lastsysoid": 13052,
        "frozenxid": 548,
        "minmxid": 1,
        "tablespace": 1663,
        "acl": None,
        "owner": "postgres",
    },
    "baseline_stuff": {
        "dba": 16384,
        "encoding": "UTF8",
        "collate": "en_US.UTF-8",
        "ctype": "en_US.UTF-8",
        "istemplate": False,
        "allowconn": True,
        "connlimit": -1,
        "lastsysoid": 13052,
        "frozenxid": 548,
        "minmxid": 1,
        "tablespace": 1663,
        "acl": None,
        "owner": "reemote5",
    },
}


async def test_databases_parses_baseline_fixture():
    host = CannedShellHost(DATABASES_OUTPUT)
    assert await PostgresqlDatabases.process(host) == DATABASES_FACT
    assert host.shells == [
        (
            "psql -Ac 'SELECT pg_catalog.pg_encoding_to_char(encoding), *, "
            "pg_catalog.pg_get_userbyid(datdba) AS owner FROM pg_catalog.pg_database'"
        )
    ]


def test_databases_parse_empty_output():
    assert PostgresqlDatabases.parse([]) == {}


async def test_databases_reads_the_simulated_seam():
    host = SimulatedHost(name="h", postgres_databases=DATABASES_FACT)
    assert await PostgresqlDatabases.process(host) == DATABASES_FACT
    result = await PostgresqlDatabases.process(host)
    result["postgres"]["owner"] = "someone"
    assert host.postgres_databases["postgres"]["owner"] == "postgres"
    assert await PostgresqlDatabases.process(SimulatedHost(name="h")) == {}


# --- PostgresqlConnections ---------------------------------------------------


CONNECTIONS_OUTPUT = """\
pid|datname|usename|client_addr|state
1234|mydb|alice|192.168.0.10|active
1235|mydb|bob||idle
9||||
(3 rows)
"""


async def test_connections_parses_canned_output():
    host = CannedShellHost(CONNECTIONS_OUTPUT)
    assert await PostgresqlConnections.process(host) == [
        {
            "pid": 1234,
            "database": "mydb",
            "username": "alice",
            "client_addr": "192.168.0.10",
            "state": "active",
        },
        {
            "pid": 1235,
            "database": "mydb",
            "username": "bob",
            "client_addr": None,
            "state": "idle",
        },
        # A background worker: no database/username/client_addr/state.
        {"pid": 9, "database": None, "username": None, "client_addr": None, "state": None},
    ]
    # The query text column is deliberately NOT selected.
    assert host.shells == [
        (
            "psql -Ac 'SELECT pid, datname, usename, client_addr, state "
            "FROM pg_catalog.pg_stat_activity'"
        )
    ]


def test_connections_parse_empty_output():
    assert PostgresqlConnections.parse([]) == []


async def test_connections_reads_the_simulated_seam():
    entries = [
        {
            "pid": 1,
            "database": "mydb",
            "username": "alice",
            "client_addr": None,
            "state": "active",
        }
    ]
    host = SimulatedHost(name="h", postgres_connections=entries)
    assert await PostgresqlConnections.process(host) == entries
    result = await PostgresqlConnections.process(host)
    result[0]["state"] = "idle"
    assert host.postgres_connections[0]["state"] == "active"
    assert await PostgresqlConnections.process(SimulatedHost(name="h")) == []


# --- PostgresqlExtensions ----------------------------------------------------


EXTENSIONS_OUTPUT = """\
extname|extversion|nspname
pg_stat_statements|1.10|public
uuid-ossp|1.1|public
(2 rows)
"""


async def test_extensions_parses_canned_output():
    host = CannedShellHost(EXTENSIONS_OUTPUT)
    assert await PostgresqlExtensions.process(host) == [
        {"name": "pg_stat_statements", "version": "1.10", "schema": "public"},
        {"name": "uuid-ossp", "version": "1.1", "schema": "public"},
    ]


def test_extensions_parse_empty_output():
    assert PostgresqlExtensions.parse([]) == []


async def test_extensions_seam_is_database_keyed():
    # Database-scoped: the seam keys on psql_database, defaulting to
    # "postgres" when unset.
    host = SimulatedHost(
        name="h",
        postgres_extensions={
            "postgres": [{"name": "plpgsql", "version": "1.0", "schema": "pg_catalog"}],
            "mydb": [{"name": "uuid-ossp", "version": "1.1", "schema": "public"}],
        },
    )
    assert await PostgresqlExtensions.process(host) == [
        {"name": "plpgsql", "version": "1.0", "schema": "pg_catalog"}
    ]
    assert await PostgresqlExtensions.process(host, psql_database="mydb") == [
        {"name": "uuid-ossp", "version": "1.1", "schema": "public"}
    ]
    # An absent database key reads as [].
    assert await PostgresqlExtensions.process(host, psql_database="other") == []


# --- PostgresqlIndexes -------------------------------------------------------


INDEXES_OUTPUT = """\
schema|name|table_name|owner|tablespace|indisunique|method
public|users_pkey|users|alice||t|btree
public|users_email_idx|users|alice|fastspace|f|btree
(2 rows)
"""


async def test_indexes_parses_canned_output():
    host = CannedShellHost(INDEXES_OUTPUT)
    assert await PostgresqlIndexes.process(host) == [
        {
            "schema": "public",
            "name": "users_pkey",
            "table": "users",
            "owner": "alice",
            "tablespace": None,
            "unique": True,
            "method": "btree",
        },
        {
            "schema": "public",
            "name": "users_email_idx",
            "table": "users",
            "owner": "alice",
            "tablespace": "fastspace",
            "unique": False,
            "method": "btree",
        },
    ]


def test_indexes_parse_empty_output():
    assert PostgresqlIndexes.parse([]) == []


async def test_indexes_seam_is_database_keyed():
    entry = {
        "schema": "public",
        "name": "idx",
        "table": "t",
        "owner": "alice",
        "tablespace": None,
        "unique": True,
        "method": "btree",
    }
    host = SimulatedHost(name="h", postgres_indexes={"mydb": [entry]})
    assert await PostgresqlIndexes.process(host, psql_database="mydb") == [entry]
    assert await PostgresqlIndexes.process(host) == []


# --- PostgresqlPrivileges ----------------------------------------------------


PRIVILEGES_OUTPUT = """\
object_type|schema_name|object_name|grantee|privilege_type|is_grantable
database||mydb|alice|CONNECT|f
schema||public|PUBLIC|USAGE|f
table|public|users|alice|SELECT|t
table|public|users|bob|INSERT|f
(4 rows)
"""


async def test_privileges_parses_canned_output():
    host = CannedShellHost(PRIVILEGES_OUTPUT)
    assert await PostgresqlPrivileges.process(host) == [
        {
            "object_type": "database",
            "schema": None,
            "name": "mydb",
            "grantee": "alice",
            "privilege": "CONNECT",
            "grantable": False,
        },
        {
            "object_type": "schema",
            "schema": None,
            "name": "public",
            "grantee": "PUBLIC",
            "privilege": "USAGE",
            "grantable": False,
        },
        {
            "object_type": "table",
            "schema": "public",
            "name": "users",
            "grantee": "alice",
            "privilege": "SELECT",
            "grantable": True,
        },
        {
            "object_type": "table",
            "schema": "public",
            "name": "users",
            "grantee": "bob",
            "privilege": "INSERT",
            "grantable": False,
        },
    ]


def test_privileges_parse_empty_output():
    # NULL ACLs (default owner-only privileges) expand to no rows.
    assert PostgresqlPrivileges.parse([]) == []


async def test_privileges_seam_is_database_keyed():
    entry = {
        "object_type": "table",
        "schema": "public",
        "name": "t",
        "grantee": "alice",
        "privilege": "SELECT",
        "grantable": False,
    }
    host = SimulatedHost(name="h", postgres_privileges={"mydb": [entry]})
    assert await PostgresqlPrivileges.process(host, psql_database="mydb") == [entry]
    assert await PostgresqlPrivileges.process(host) == []


# --- PostgresqlPublications --------------------------------------------------


PUBLICATIONS_OUTPUT = """\
pubname|owner|puballtables|tables
all_pub|alice|t|
some_pub|alice|f|public.orders,public.users
(2 rows)
"""


async def test_publications_parses_canned_output():
    host = CannedShellHost(PUBLICATIONS_OUTPUT)
    assert await PostgresqlPublications.process(host) == [
        {"name": "all_pub", "owner": "alice", "all_tables": True, "tables": []},
        {
            "name": "some_pub",
            "owner": "alice",
            "all_tables": False,
            "tables": ["public.orders", "public.users"],
        },
    ]


def test_publications_parse_empty_output():
    assert PostgresqlPublications.parse([]) == []


async def test_publications_seam_copies_the_tables_list():
    entry = {
        "name": "p",
        "owner": "alice",
        "all_tables": False,
        "tables": ["public.t"],
    }
    host = SimulatedHost(name="h", postgres_publications={"mydb": [entry]})
    result = await PostgresqlPublications.process(host, psql_database="mydb")
    assert result == [entry]
    # The inner tables list is copied too, not just the entry dict.
    result[0]["tables"].append("public.other")
    assert host.postgres_publications["mydb"][0]["tables"] == ["public.t"]


# --- PostgresqlReplication ---------------------------------------------------


REPLICATION_OUTPUT = """\
application_name|client_addr|state|sync_state|replay_lag
standby1|192.168.0.20|streaming|async|00:00:00.000512
standby2|192.168.0.21|streaming|sync|
(2 rows)
"""


async def test_replication_parses_canned_output():
    host = CannedShellHost(REPLICATION_OUTPUT)
    assert await PostgresqlReplication.process(host) == [
        {
            "application_name": "standby1",
            "client_addr": "192.168.0.20",
            "state": "streaming",
            "sync_state": "async",
            "replay_lag": "00:00:00.000512",
        },
        {
            "application_name": "standby2",
            "client_addr": "192.168.0.21",
            "state": "streaming",
            "sync_state": "sync",
            "replay_lag": None,
        },
    ]


def test_replication_parse_empty_output():
    # A standalone server (no connected standbys) reads as [].
    assert PostgresqlReplication.parse([]) == []


async def test_replication_reads_the_simulated_seam():
    entry = {
        "application_name": "s",
        "client_addr": None,
        "state": "streaming",
        "sync_state": "async",
        "replay_lag": None,
    }
    host = SimulatedHost(name="h", postgres_replication=[entry])
    assert await PostgresqlReplication.process(host) == [entry]
    assert await PostgresqlReplication.process(SimulatedHost(name="h")) == []


# --- PostgresqlRoleMemberships -----------------------------------------------


MEMBERSHIPS_OUTPUT = """\
role|member|admin_option
readonly|alice|f
admin|bob|t
(2 rows)
"""


async def test_role_memberships_parses_canned_output():
    host = CannedShellHost(MEMBERSHIPS_OUTPUT)
    assert await PostgresqlRoleMemberships.process(host) == [
        {"role": "readonly", "member": "alice", "admin_option": False},
        {"role": "admin", "member": "bob", "admin_option": True},
    ]


def test_role_memberships_parse_empty_output():
    assert PostgresqlRoleMemberships.parse([]) == []


async def test_role_memberships_reads_the_simulated_seam():
    entry = {"role": "g", "member": "m", "admin_option": False}
    host = SimulatedHost(name="h", postgres_role_memberships=[entry])
    assert await PostgresqlRoleMemberships.process(host) == [entry]
    assert await PostgresqlRoleMemberships.process(SimulatedHost(name="h")) == []


# --- PostgresqlSchemas -------------------------------------------------------


SCHEMAS_OUTPUT = """\
name|owner
app|alice
public|postgres
(2 rows)
"""


async def test_schemas_parses_canned_output():
    host = CannedShellHost(SCHEMAS_OUTPUT)
    assert await PostgresqlSchemas.process(host) == [
        {"name": "app", "owner": "alice"},
        {"name": "public", "owner": "postgres"},
    ]


def test_schemas_parse_empty_output():
    assert PostgresqlSchemas.parse([]) == []


async def test_schemas_seam_is_database_keyed():
    entry = {"name": "app", "owner": "alice"}
    host = SimulatedHost(name="h", postgres_schemas={"mydb": [entry]})
    assert await PostgresqlSchemas.process(host, psql_database="mydb") == [entry]
    assert await PostgresqlSchemas.process(host) == []


# --- PostgresqlSettings ------------------------------------------------------


SETTINGS_OUTPUT = """\
name|setting|unit|source|pending_restart
max_connections|100||configuration file|f
shared_buffers|16384|8kB|configuration file|t
wal_level|replica||default|f
(3 rows)
"""


async def test_settings_parses_canned_output():
    host = CannedShellHost(SETTINGS_OUTPUT)
    assert await PostgresqlSettings.process(host) == [
        {
            "name": "max_connections",
            "setting": "100",
            "unit": None,
            "source": "configuration file",
            "pending_restart": False,
        },
        {
            "name": "shared_buffers",
            "setting": "16384",
            "unit": "8kB",
            "source": "configuration file",
            "pending_restart": True,
        },
        {
            "name": "wal_level",
            "setting": "replica",
            "unit": None,
            "source": "default",
            "pending_restart": False,
        },
    ]


def test_settings_parse_empty_output():
    assert PostgresqlSettings.parse([]) == []


async def test_settings_reads_the_simulated_seam():
    entry = {
        "name": "max_connections",
        "setting": "100",
        "unit": None,
        "source": "default",
        "pending_restart": False,
    }
    host = SimulatedHost(name="h", postgres_settings=[entry])
    assert await PostgresqlSettings.process(host) == [entry]
    result = await PostgresqlSettings.process(host)
    result[0]["setting"] = "200"
    assert host.postgres_settings[0]["setting"] == "100"
    assert await PostgresqlSettings.process(SimulatedHost(name="h")) == []


# --- PostgresqlSubscriptions -------------------------------------------------


SUBSCRIPTIONS_OUTPUT = """\
name|owner|enabled|publications
sub1|alice|t|{pub_a,pub_b}
sub2|alice|f|{}
(2 rows)
"""


async def test_subscriptions_parses_canned_output():
    host = CannedShellHost(SUBSCRIPTIONS_OUTPUT)
    assert await PostgresqlSubscriptions.process(host) == [
        {
            "name": "sub1",
            "owner": "alice",
            "enabled": True,
            "publications": ["pub_a", "pub_b"],
        },
        {"name": "sub2", "owner": "alice", "enabled": False, "publications": []},
    ]
    # The credential-bearing subconninfo column is deliberately NOT selected.
    assert "subconninfo" not in host.shells[0]


def test_subscriptions_parse_empty_output():
    assert PostgresqlSubscriptions.parse([]) == []


async def test_subscriptions_seam_copies_the_publications_list():
    entry = {
        "name": "s",
        "owner": "alice",
        "enabled": True,
        "publications": ["pub_a"],
    }
    host = SimulatedHost(name="h", postgres_subscriptions={"mydb": [entry]})
    result = await PostgresqlSubscriptions.process(host, psql_database="mydb")
    assert result == [entry]
    result[0]["publications"].append("pub_b")
    assert host.postgres_subscriptions["mydb"][0]["publications"] == ["pub_a"]


# --- PostgresqlTables --------------------------------------------------------


TABLES_OUTPUT = """\
schema|name|owner|tablespace|rows
public|users|alice||1500
public|events|alice|fastspace|-1
(2 rows)
"""


async def test_tables_parses_canned_output():
    host = CannedShellHost(TABLES_OUTPUT)
    assert await PostgresqlTables.process(host) == [
        {
            "schema": "public",
            "name": "users",
            "owner": "alice",
            "tablespace": None,
            "rows": 1500,
        },
        {
            "schema": "public",
            "name": "events",
            "owner": "alice",
            "tablespace": "fastspace",
            "rows": -1,
        },
    ]


def test_tables_parse_empty_output():
    assert PostgresqlTables.parse([]) == []


async def test_tables_seam_is_database_keyed():
    entry = {
        "schema": "public",
        "name": "t",
        "owner": "alice",
        "tablespace": None,
        "rows": 0,
    }
    host = SimulatedHost(name="h", postgres_tables={"mydb": [entry]})
    assert await PostgresqlTables.process(host, psql_database="mydb") == [entry]
    assert await PostgresqlTables.process(host) == []


# --- PostgresqlTablespaces ---------------------------------------------------


TABLESPACES_OUTPUT = """\
oid|name|owner|location
1663|pg_default|postgres|
1664|pg_global|postgres|
16385|fastspace|alice|/mnt/fast
(3 rows)
"""


async def test_tablespaces_parses_canned_output():
    host = CannedShellHost(TABLESPACES_OUTPUT)
    assert await PostgresqlTablespaces.process(host) == [
        {"oid": 1663, "name": "pg_default", "owner": "postgres", "location": None},
        {"oid": 1664, "name": "pg_global", "owner": "postgres", "location": None},
        {
            "oid": 16385,
            "name": "fastspace",
            "owner": "alice",
            "location": "/mnt/fast",
        },
    ]


def test_tablespaces_parse_empty_output():
    assert PostgresqlTablespaces.parse([]) == []


async def test_tablespaces_reads_the_simulated_seam():
    entry = {"oid": 16385, "name": "fast", "owner": "alice", "location": "/mnt/fast"}
    host = SimulatedHost(name="h", postgres_tablespaces=[entry])
    assert await PostgresqlTablespaces.process(host) == [entry]
    result = await PostgresqlTablespaces.process(host)
    result[0]["location"] = "/elsewhere"
    assert host.postgres_tablespaces[0]["location"] == "/mnt/fast"
    assert await PostgresqlTablespaces.process(SimulatedHost(name="h")) == []


# --- operation helpers -------------------------------------------------------


async def materialize(host: Host, operation, **kwargs):
    """Run an operation against a fresh cache; return ``(ctx, commands)``."""
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [cmd async for cmd in operation(ctx, **kwargs)]
    return ctx, commands


# A role entry in the fact's catalog keys (canlogin/super/connlimit, the
# mapping the role operation's drift check uses), at the operation's own
# defaults so a seeded role reads as "no diffs" for a default request.
ROLE_ENTRY = {
    "super": False,
    "inherit": True,
    "createrole": False,
    "createdb": False,
    "canlogin": True,
    "replication": False,
    "connlimit": -1,
    "password": None,
    "validuntil": None,
    "bypassrls": False,
    "config": None,
    "oid": 16384,
}

# The built-in tablespaces a stock cluster reports (location None), plus a
# custom one the database/tablespace tests resolve names through.
PG_DEFAULT_SPACE = {"oid": 1663, "name": "pg_default", "owner": "postgres", "location": None}
FAST_SPACE = {"oid": 16385, "name": "fast", "owner": "postgres", "location": "/mnt/fast"}


def test_postgresql_namespace_exports_operations_and_descriptors():
    # The fourteen operations are reached through the namespace and the
    # package, never the top level; the fourteen descriptors are
    # re-exported at all three sites like every other descriptor.
    for name in (
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
    ):
        assert getattr(postgresql, name) is getattr(postgresql.postgresql, name)
        assert not hasattr(reemote5, name)
    for name in (
        "PostgresqlDatabaseCommand",
        "PostgresqlDumpCommand",
        "PostgresqlExtensionCommand",
        "PostgresqlIndexCommand",
        "PostgresqlLoadCommand",
        "PostgresqlMembershipCommand",
        "PostgresqlPrivilegeCommand",
        "PostgresqlPublicationCommand",
        "PostgresqlRoleCommand",
        "PostgresqlSchemaCommand",
        "PostgresqlSettingCommand",
        "PostgresqlSqlCommand",
        "PostgresqlSubscriptionCommand",
        "PostgresqlTablespaceCommand",
    ):
        module_cls = getattr(postgresql.postgresql, name)
        assert getattr(postgresql, name) is module_cls
        assert getattr(reemote5, name) is module_cls


# --- database operation (baseline parity, extended) ---------------------------


def test_database_command_renders_baseline_create_fixtures():
    # add_database.json / add_database_owner.json / remove_database.json:
    # identifiers are double-quoted exactly as the baseline renders them.
    assert PostgresqlDatabaseCommand(database="somedb").shell() == (
        "psql -Ac 'CREATE DATABASE \"somedb\"'"
    )
    assert PostgresqlDatabaseCommand(database="somedb", owner="someowner").shell() == (
        "psql -Ac 'CREATE DATABASE \"somedb\" OWNER \"someowner\"'"
    )
    assert PostgresqlDatabaseCommand(database="somedb", present=False).shell() == (
        "psql -Ac 'DROP DATABASE \"somedb\"'"
    )


def test_database_command_renders_locales_with_quoted_encoding():
    # add_database_locales.json deviation: reemote5 renders ENCODING as a
    # quoted literal (PostgreSQL accepts it identically to the baseline's bare
    # word); LC_COLLATE/LC_CTYPE keep the baseline's quoted-literal form. The
    # expected shell composes shlex.quote over the SQL text -- the quoting
    # itself is covered by the helper tests.
    sql = "CREATE DATABASE \"somedb\" ENCODING 'utf8' LC_COLLATE 'C' LC_CTYPE 'C'"
    assert PostgresqlDatabaseCommand(
        database="somedb", encoding="utf8", lc_collate="C", lc_ctype="C"
    ).shell() == f"psql -Ac {shlex.quote(sql)}"


def test_database_command_renders_the_alter_statements():
    # update_database.json: one ALTER statement per non-None field, in the
    # OWNER / SET TABLESPACE / CONNECTION LIMIT / ALLOW_CONNECTIONS order.
    sql = (
        'ALTER DATABASE "somedb" OWNER TO "someowner"; '
        'ALTER DATABASE "somedb" SET TABLESPACE "fast"; '
        'ALTER DATABASE "somedb" CONNECTION LIMIT 10; '
        'ALTER DATABASE "somedb" WITH ALLOW_CONNECTIONS false;'
    )
    assert PostgresqlDatabaseCommand(
        database="somedb",
        alter=True,
        owner="someowner",
        tablespace="fast",
        connection_limit=10,
        allow_connections=False,
    ).shell() == f"psql -Ac {shlex.quote(sql)}"


def test_database_command_elevates_as_a_whole_under_sudo():
    assert command_shell(PostgresqlDatabaseCommand(database="somedb"), sudo=True) == (
        "sudo psql -Ac 'CREATE DATABASE \"somedb\"'"
    )


async def test_database_creates_an_absent_database():
    # add_database.json: an empty database fact -> the create command.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(host, postgresql.database, database="somedb")
    assert ctx.changed
    assert ctx.reason == "postgresql database somedb will be created"
    assert commands == [PostgresqlDatabaseCommand(database="somedb")]


async def test_database_create_carries_owner_and_locales():
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize(
        host,
        postgresql.database,
        database="somedb",
        owner="someowner",
        template="template0",
        encoding="UTF8",
        lc_collate="C",
        lc_ctype="C",
    )
    assert commands == [
        PostgresqlDatabaseCommand(
            database="somedb",
            owner="someowner",
            template="template0",
            encoding="UTF8",
            lc_collate="C",
            lc_ctype="C",
        )
    ]


async def test_database_create_resolves_a_known_tablespace():
    host = SimulatedHost(name="h", postgres_tablespaces=[PG_DEFAULT_SPACE, FAST_SPACE])
    ctx, commands = await materialize(
        host, postgresql.database, database="somedb", tablespace="fast"
    )
    assert ctx.changed
    assert commands == [PostgresqlDatabaseCommand(database="somedb", tablespace="fast")]


async def test_database_unknown_tablespace_raises_in_prepare():
    # The reworked OID resolution: the baseline compared the NAME against the
    # fact's OID column (never matching); reemote5 fails fast instead.
    host = SimulatedHost(name="h", postgres_tablespaces=[PG_DEFAULT_SPACE])
    with pytest.raises(OperationError, match="tablespace fast does not exist"):
        await materialize(
            host, postgresql.database, database="somedb", tablespace="fast"
        )


async def test_database_alters_owner_and_tablespace_by_oid():
    # update_database.json reworked: the fact reports the tablespace OID
    # (1663 = pg_default), the request names "fast" (16385) -> both diffs
    # alter, where the baseline's name-vs-OID comparison always "matched".
    host = SimulatedHost(
        name="h",
        postgres_databases={
            "somedb": {
                **DATABASES_FACT["postgres"],
                "owner": "someotherowner",
                "tablespace": 1663,
            }
        },
        postgres_tablespaces=[PG_DEFAULT_SPACE, FAST_SPACE],
    )
    ctx, commands = await materialize(
        host,
        postgresql.database,
        database="somedb",
        owner="someowner",
        tablespace="fast",
    )
    assert ctx.changed
    assert ctx.reason.startswith("postgresql database somedb will be altered")
    assert commands == [
        PostgresqlDatabaseCommand(
            database="somedb", alter=True, owner="someowner", tablespace="fast"
        )
    ]


async def test_database_noop_when_the_parameters_match():
    # update_database_noop.json: the baseline's verbatim noop_description.
    host = SimulatedHost(
        name="h",
        postgres_databases={
            "somedb": {**DATABASES_FACT["postgres"], "owner": "someowner"}
        },
    )
    ctx, commands = await materialize(
        host, postgresql.database, database="somedb", owner="someowner"
    )
    assert not ctx.changed
    assert ctx.reason == (
        "postgresql database somedb already exists with the same parameters"
    )
    assert commands == []


async def test_database_reports_immutable_options_as_skipped():
    # TEMPLATE/ENCODING/LC_* are creation-only: a differing request on an
    # existing database is reported in the reason, never altered.
    host = SimulatedHost(
        name="h", postgres_databases={"somedb": DATABASES_FACT["postgres"]}
    )
    ctx, commands = await materialize(
        host,
        postgresql.database,
        database="somedb",
        template="template0",
        encoding="LATIN1",
        lc_collate="C",
    )
    assert not ctx.changed
    assert commands == []
    assert ctx.reason == (
        "postgresql database somedb already exists with the same parameters; "
        "TEMPLATE cannot be changed on an existing database; "
        "ENCODING cannot be changed on an existing database; "
        "LC_COLLATE cannot be changed on an existing database"
    )


async def test_database_encoding_comparison_is_case_insensitive():
    # pg_encoding_to_char reports UTF8; a lower-case "utf8" request on an
    # unchanged database must not read as an immutable-option change.
    host = SimulatedHost(
        name="h", postgres_databases={"somedb": DATABASES_FACT["postgres"]}
    )
    ctx, commands = await materialize(
        host, postgresql.database, database="somedb", encoding="utf8"
    )
    assert not ctx.changed
    assert commands == []
    assert ctx.reason == (
        "postgresql database somedb already exists with the same parameters"
    )


async def test_database_alters_connection_limit_and_allow_connections():
    # The reemote5 database-enhancement: ALLOW_CONNECTIONS joins the
    # alterable set alongside CONNECTION LIMIT.
    host = SimulatedHost(
        name="h", postgres_databases={"somedb": DATABASES_FACT["postgres"]}
    )
    ctx, commands = await materialize(
        host,
        postgresql.database,
        database="somedb",
        connection_limit=5,
        allow_connections=False,
    )
    assert ctx.changed
    assert commands == [
        PostgresqlDatabaseCommand(
            database="somedb",
            alter=True,
            connection_limit=5,
            allow_connections=False,
        )
    ]


async def test_database_drops_an_existing_database():
    # remove_database.json.
    host = SimulatedHost(
        name="h", postgres_databases={"somedb": DATABASES_FACT["postgres"]}
    )
    ctx, commands = await materialize(
        host, postgresql.database, database="somedb", present=False
    )
    assert ctx.changed
    assert ctx.reason == "postgresql database somedb will be dropped"
    assert commands == [PostgresqlDatabaseCommand(database="somedb", present=False)]


async def test_database_drop_noop_matches_baseline_description():
    # remove_database_noop.json.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, postgresql.database, database="somedb", present=False
    )
    assert not ctx.changed
    assert ctx.reason == "postgresql database somedb does not exist"
    assert commands == []


async def test_database_absorbs_unknown_kwargs():
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize(
        host, postgresql.database, database="somedb", future_flag=True
    )
    assert commands == [PostgresqlDatabaseCommand(database="somedb")]


async def test_database_seam_create_alter_drop():
    # send_command maintains postgres_databases: the create seeds the full
    # catalog shape (tablespace resolved to pg_default's OID), the alter
    # applies only its non-None fields, the drop pops the entry -- and a
    # repeat materialize against the mutated seam is a no-op.
    host = SimulatedHost(name="h", postgres_tablespaces=[PG_DEFAULT_SPACE, FAST_SPACE])
    await send_command(
        host, PostgresqlDatabaseCommand(database="somedb", tablespace="fast")
    )
    entry = host.postgres_databases["somedb"]
    assert entry["owner"] == "postgres"
    assert entry["dba"] == 10
    assert entry["tablespace"] == 16385
    assert entry["encoding"] == "UTF8"
    assert entry["allowconn"] is True
    assert entry["connlimit"] == -1
    ctx, commands = await materialize(
        host, postgresql.database, database="somedb", tablespace="fast"
    )
    assert not ctx.changed
    assert commands == []
    await send_command(
        host,
        PostgresqlDatabaseCommand(
            database="somedb", alter=True, owner="alice", connection_limit=5
        ),
    )
    assert host.postgres_databases["somedb"]["owner"] == "alice"
    assert host.postgres_databases["somedb"]["connlimit"] == 5
    await send_command(host, PostgresqlDatabaseCommand(database="somedb", present=False))
    assert host.postgres_databases == {}


# --- role operation (baseline parity, reworked) -------------------------------


def test_role_command_renders_baseline_create_fixtures():
    # user_add_super.json / user_add_privileges.json / user_add_no_login.json
    # / user_add_replication.json, with the documented deviation: every
    # flag renders explicitly (X/NOX pairs) and inherit defaults to True,
    # so INHERIT/NOINHERIT always appears where the baseline omitted it.
    assert PostgresqlRoleCommand(
        role="testuser",
        login=True,
        superuser=True,
        inherit=True,
        createdb=False,
        createrole=False,
        replication=False,
    ).shell() == (
        "psql -Ac 'CREATE ROLE \"testuser\" LOGIN SUPERUSER INHERIT "
        "NOCREATEDB NOCREATEROLE NOREPLICATION'"
    )
    assert PostgresqlRoleCommand(
        role="testuser",
        login=True,
        superuser=True,
        inherit=True,
        createdb=True,
        createrole=True,
        replication=True,
    ).shell() == (
        "psql -Ac 'CREATE ROLE \"testuser\" LOGIN SUPERUSER INHERIT "
        "CREATEDB CREATEROLE REPLICATION'"
    )
    assert PostgresqlRoleCommand(
        role="testuser",
        login=False,
        superuser=False,
        inherit=True,
        createdb=False,
        createrole=False,
        replication=True,
    ).shell() == (
        "psql -Ac 'CREATE ROLE \"testuser\" NOLOGIN NOSUPERUSER INHERIT "
        "NOCREATEDB NOCREATEROLE REPLICATION'"
    )
    # user_remove.json / role_update.json (password dropped namespace-wide).
    assert PostgresqlRoleCommand(role="testuser", present=False).shell() == (
        "psql -Ac 'DROP ROLE \"testuser\"'"
    )
    assert PostgresqlRoleCommand(
        role="testuser", alter=True, connection_limit=15
    ).shell() == "psql -Ac 'ALTER ROLE \"testuser\" CONNECTION LIMIT 15'"


async def test_role_creates_an_absent_role():
    # user_add.json minus the dropped password option; the create carries
    # every flag explicitly (the X/NOX deviation).
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, postgresql.role, role="testuser", connection_limit=15
    )
    assert ctx.changed
    assert ctx.reason == "postgresql role testuser will be created"
    assert commands == [
        PostgresqlRoleCommand(
            role="testuser",
            login=True,
            superuser=False,
            inherit=True,
            createdb=False,
            createrole=False,
            replication=False,
            connection_limit=15,
        )
    ]


async def test_role_absorbs_the_dropped_password_option():
    # The baseline's ``password`` is deliberately dropped (the namespace
    # credentials decision); passed anyway it lands in **kwargs and is
    # ignored -- no PASSWORD clause ever renders.
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize(
        host, postgresql.role, role="testuser", password="abc"
    )
    (cmd,) = commands
    assert "PASSWORD" not in cmd.shell()


async def test_role_alters_only_the_differing_connection_limit():
    # role_update.json reworked: the fact's catalog key is "connlimit"
    # (the baseline compared "connection_limit", which never exists).
    host = SimulatedHost(
        name="h",
        postgres_roles={"testuser": {**ROLE_ENTRY, "connlimit": 10}},
    )
    ctx, commands = await materialize(
        host, postgresql.role, role="testuser", connection_limit=15
    )
    assert ctx.changed
    assert ctx.reason == "postgresql role testuser will be altered (connection_limit)"
    assert commands == [
        PostgresqlRoleCommand(role="testuser", alter=True, connection_limit=15)
    ]


async def test_role_alter_maps_login_onto_canlogin():
    # The catalog-key fix: a login=False request against a canlogin=True
    # role alters (the baseline's option-name comparison never fired).
    host = SimulatedHost(name="h", postgres_roles={"testuser": ROLE_ENTRY})
    ctx, commands = await materialize(
        host, postgresql.role, role="testuser", login=False
    )
    assert ctx.changed
    assert ctx.reason == "postgresql role testuser will be altered (login)"
    assert commands == [PostgresqlRoleCommand(role="testuser", alter=True, login=False)]
    assert commands[0].shell() == "psql -Ac 'ALTER ROLE \"testuser\" NOLOGIN'"


async def test_role_noop_matches_baseline_description():
    # role_update_noop.json: seeded on the catalog keys super/inherit.
    host = SimulatedHost(
        name="h",
        postgres_roles={"testuser": {**ROLE_ENTRY, "super": True, "inherit": True}},
    )
    ctx, commands = await materialize(
        host, postgresql.role, role="testuser", superuser=True, inherit=True
    )
    assert not ctx.changed
    assert ctx.reason == "postgresql role testuser exists and does not need updates"
    assert commands == []


async def test_role_drops_an_existing_role():
    # user_remove.json.
    host = SimulatedHost(name="h", postgres_roles={"testuser": ROLE_ENTRY})
    ctx, commands = await materialize(
        host, postgresql.role, role="testuser", present=False
    )
    assert ctx.changed
    assert ctx.reason == "postgresql role testuser will be dropped"
    assert commands == [PostgresqlRoleCommand(role="testuser", present=False)]


async def test_role_drop_noop_matches_baseline_description():
    # role_remove_noop.json.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, postgresql.role, role="testuser", present=False
    )
    assert not ctx.changed
    assert ctx.reason == "postgresql role testuser does not exist"
    assert commands == []


async def test_role_seam_create_alter_drop():
    host = SimulatedHost(name="h")
    await send_command(
        host,
        PostgresqlRoleCommand(
            role="testuser",
            login=True,
            superuser=False,
            inherit=True,
            createdb=False,
            createrole=False,
            replication=False,
        ),
    )
    entry = host.postgres_roles["testuser"]
    assert entry["canlogin"] is True
    assert entry["super"] is False
    assert entry["connlimit"] == -1
    assert entry["password"] is None
    assert entry["oid"] == 16384
    # A repeat create request reads the mutated seam and is a no-op.
    ctx, commands = await materialize(host, postgresql.role, role="testuser")
    assert not ctx.changed
    assert commands == []
    await send_command(
        host, PostgresqlRoleCommand(role="testuser", alter=True, login=False)
    )
    assert host.postgres_roles["testuser"]["canlogin"] is False
    await send_command(host, PostgresqlRoleCommand(role="testuser", present=False))
    assert host.postgres_roles == {}


# --- sql / dump / load operations (stateless baseline parity) -----------------


async def test_sql_renders_baseline_execute_fixture():
    # execute.json: the custom-connection flag order, no PGPASSWORD prefix.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host,
        postgresql.sql,
        sql="SELECT 1",
        psql_user="postgres",
        psql_host="127.0.0.1",
        psql_port=5432,
    )
    assert ctx.changed
    assert ctx.reason == "postgresql SQL will be executed"
    assert commands == [
        PostgresqlSqlCommand(
            sql="SELECT 1", psql_user="postgres", psql_host="127.0.0.1", psql_port=5432
        )
    ]
    assert commands[0].shell() == "psql -U postgres -h 127.0.0.1 -p 5432 -Ac 'SELECT 1'"


async def test_sql_is_stateless_and_always_yields():
    # The baseline marks sql is_idempotent=False: two identical requests both
    # report changed, and the simulated seam executes no SQL.
    host = SimulatedHost(name="h")
    ctx1, commands1 = await materialize(host, postgresql.sql, sql="SELECT 1")
    ctx2, commands2 = await materialize(host, postgresql.sql, sql="SELECT 1")
    assert ctx1.changed and ctx2.changed
    assert commands1 == commands2 == [PostgresqlSqlCommand(sql="SELECT 1")]
    await send_command(host, commands1[0])
    assert host.postgres_roles == {}
    assert host.postgres_databases == {}


async def test_dump_renders_baseline_fixture_without_the_password_prefix():
    # dump.json minus PGPASSWORD (the namespace credentials decision).
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host,
        postgresql.dump,
        dest="somefile",
        psql_user="root",
        psql_host="localhost",
        psql_port=3306,
        psql_database="somedb",
    )
    assert ctx.changed
    assert ctx.reason == "postgresql database will be dumped to somefile"
    assert commands == [
        PostgresqlDumpCommand(
            dest="somefile",
            psql_user="root",
            psql_host="localhost",
            psql_port=3306,
            psql_database="somedb",
        )
    ]
    assert commands[0].shell() == (
        "pg_dump -d somedb -U root -h localhost -p 3306 > somefile"
    )
    # A dest with shell metacharacters is quoted literally.
    assert PostgresqlDumpCommand(dest="my file").shell() == "pg_dump > 'my file'"


async def test_dump_seam_writes_the_marker_file():
    host = SimulatedHost(name="h")
    await send_command(
        host, PostgresqlDumpCommand(dest="/tmp/db.sql", psql_database="somedb")
    )
    assert host.remote_files["/tmp/db.sql"] == b"-- simulated pg_dump of somedb\n"


async def test_load_renders_baseline_fixture_without_the_password_prefix():
    # load.json minus PGPASSWORD.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host,
        postgresql.load,
        src="somefile",
        psql_user="root",
        psql_database="somedb",
    )
    assert ctx.changed
    assert ctx.reason == "somefile will be loaded into the postgresql database"
    assert commands == [
        PostgresqlLoadCommand(src="somefile", psql_user="root", psql_database="somedb")
    ]
    assert commands[0].shell() == "psql -d somedb -U root < somefile"
    assert PostgresqlLoadCommand(src="my file").shell() == "psql < 'my file'"


async def test_load_seam_changes_no_catalog_state():
    # The simulation executes no SQL, so a load is observable only in the
    # log -- the seam arm is a deliberate no-op.
    host = SimulatedHost(name="h", remote_files={"/tmp/db.sql": b"SELECT 1;"})
    await send_command(host, PostgresqlLoadCommand(src="/tmp/db.sql"))
    assert host.remote_files == {"/tmp/db.sql": b"SELECT 1;"}
    assert host.postgres_databases == {}


# --- privilege operation (reemote5-original) ---------------------------------


def _grant_entry(
    privilege: str,
    *,
    name: str = "users",
    schema: str | None = "public",
    object_type: str = "table",
    grantee: str = "alice",
    grantable: bool = False,
) -> dict:
    """One PostgresqlPrivileges entry (the fact's ACL-row shape)."""
    return {
        "object_type": object_type,
        "schema": schema,
        "name": name,
        "grantee": grantee,
        "privilege": privilege,
        "grantable": grantable,
    }


def test_privilege_command_renders_grant_and_revoke():
    assert PostgresqlPrivilegeCommand(
        privileges=("SELECT", "UPDATE"), role="alice", objects=("public.users",)
    ).shell() == (
        "psql -Ac 'GRANT SELECT, UPDATE ON TABLE \"public\".\"users\" TO \"alice\"'"
    )
    assert PostgresqlPrivilegeCommand(
        privileges=("SELECT",),
        role="alice",
        objects=("public.users",),
        grant_option=True,
    ).shell() == (
        "psql -Ac 'GRANT SELECT ON TABLE \"public\".\"users\" TO \"alice\" "
        "WITH GRANT OPTION'"
    )
    assert PostgresqlPrivilegeCommand(
        privileges=("SELECT",), role="alice", objects=("public.users",), present=False
    ).shell() == (
        "psql -Ac 'REVOKE SELECT ON TABLE \"public\".\"users\" FROM \"alice\"'"
    )
    assert PostgresqlPrivilegeCommand(
        privileges=("SELECT",),
        role="alice",
        objects=("public.users",),
        present=False,
        revoke_grant_option=True,
    ).shell() == (
        "psql -Ac 'REVOKE GRANT OPTION FOR SELECT ON TABLE \"public\".\"users\" "
        "FROM \"alice\"'"
    )
    assert PostgresqlPrivilegeCommand(
        privileges=("USAGE",), role="alice", object_type="schema", objects=("app",)
    ).shell() == "psql -Ac 'GRANT USAGE ON SCHEMA \"app\" TO \"alice\"'"
    assert PostgresqlPrivilegeCommand(
        privileges=("SELECT",), role="alice", all_in_schema="public"
    ).shell() == (
        "psql -Ac 'GRANT SELECT ON ALL TABLES IN SCHEMA \"public\" TO \"alice\"'"
    )


async def test_privilege_validates_object_type_and_targets():
    host = SimulatedHost(name="h")
    with pytest.raises(ValueError, match="unknown postgresql object_type 'widget'"):
        await materialize(
            host,
            postgresql.privilege,
            privileges="SELECT",
            role="alice",
            object_type="widget",
            objects=["users"],
        )
    # Neither target form, and both at once, are rejected alike.
    with pytest.raises(ValueError, match="exactly one of objects or all_in_schema"):
        await materialize(host, postgresql.privilege, privileges="SELECT", role="alice")
    with pytest.raises(ValueError, match="exactly one of objects or all_in_schema"):
        await materialize(
            host,
            postgresql.privilege,
            privileges="SELECT",
            role="alice",
            objects=["users"],
            all_in_schema="public",
        )
    with pytest.raises(ValueError, match="databases cannot be addressed ALL IN SCHEMA"):
        await materialize(
            host,
            postgresql.privilege,
            privileges="CONNECT",
            role="alice",
            object_type="database",
            all_in_schema="public",
        )


async def test_privilege_rejects_privileges_invalid_for_the_type():
    host = SimulatedHost(name="h")
    with pytest.raises(ValueError, match="privileges EXECUTE are not valid on a schema"):
        await materialize(
            host,
            postgresql.privilege,
            privileges=["USAGE", "EXECUTE"],
            role="alice",
            object_type="schema",
            objects=["app"],
        )


async def test_privilege_all_expands_to_the_types_sorted_set():
    # ALL on a table expands to PostgreSQL's seven table privileges, sorted
    # (the Ansible postgresql_privs vocabulary).
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, postgresql.privilege, privileges="ALL", role="alice", objects=["users"]
    )
    assert ctx.changed
    assert ctx.reason == (
        "postgresql will grant DELETE, INSERT, REFERENCES, SELECT, TRIGGER, "
        "TRUNCATE, UPDATE to alice"
    )
    assert commands == [
        PostgresqlPrivilegeCommand(
            privileges=(
                "DELETE", "INSERT", "REFERENCES", "SELECT", "TRIGGER",
                "TRUNCATE", "UPDATE",
            ),
            role="alice",
            objects=("public.users",),
        )
    ]


async def test_privilege_grants_only_the_missing_privileges():
    # alice already holds SELECT -> only INSERT is granted; the bare table
    # name normalizes to public.users on both the compare and the render.
    host = SimulatedHost(
        name="h", postgres_privileges={"postgres": [_grant_entry("SELECT")]}
    )
    ctx, commands = await materialize(
        host,
        postgresql.privilege,
        privileges=["SELECT", "INSERT"],
        role="alice",
        objects=["users"],
    )
    assert ctx.changed
    assert ctx.reason == "postgresql will grant INSERT to alice"
    assert commands == [
        PostgresqlPrivilegeCommand(
            privileges=("INSERT",), role="alice", objects=("public.users",)
        )
    ]


async def test_privilege_noop_when_all_privileges_are_held():
    host = SimulatedHost(
        name="h",
        postgres_privileges={
            "postgres": [_grant_entry("SELECT"), _grant_entry("INSERT")]
        },
    )
    ctx, commands = await materialize(
        host,
        postgresql.privilege,
        privileges=["select", "insert"],
        role="alice",
        objects=["public.users"],
    )
    assert not ctx.changed
    assert ctx.reason == "postgresql role alice already has the privileges"
    assert commands == []


async def test_privilege_grant_option_upgrade_regrants_the_full_set():
    # The privilege is held but not grantable: a grant_option request
    # re-grants the whole desired set WITH GRANT OPTION, not just the
    # missing privileges (there are none).
    host = SimulatedHost(
        name="h", postgres_privileges={"postgres": [_grant_entry("SELECT")]}
    )
    ctx, commands = await materialize(
        host,
        postgresql.privilege,
        privileges="SELECT",
        role="alice",
        objects=["users"],
        grant_option=True,
    )
    assert ctx.changed
    assert ctx.reason == "postgresql will grant SELECT to alice"
    assert commands == [
        PostgresqlPrivilegeCommand(
            privileges=("SELECT",),
            role="alice",
            objects=("public.users",),
            grant_option=True,
        )
    ]


async def test_privilege_revokes_only_the_held_privileges():
    host = SimulatedHost(
        name="h", postgres_privileges={"postgres": [_grant_entry("SELECT")]}
    )
    ctx, commands = await materialize(
        host,
        postgresql.privilege,
        privileges=["SELECT", "INSERT"],
        role="alice",
        objects=["users"],
        present=False,
    )
    assert ctx.changed
    assert ctx.reason == "postgresql will revoke SELECT from alice"
    assert commands == [
        PostgresqlPrivilegeCommand(
            privileges=("SELECT",), role="alice", objects=("public.users",),
            present=False,
        )
    ]


async def test_privilege_revoke_noop_when_none_are_held():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host,
        postgresql.privilege,
        privileges="SELECT",
        role="alice",
        objects=["users"],
        present=False,
    )
    assert not ctx.changed
    assert ctx.reason == "postgresql role alice has none of the privileges"
    assert commands == []


async def test_privilege_revoke_grant_option_keeps_the_privilege():
    # grant_option on a revoke withdraws only the option, and only from
    # privileges that actually carry it (INSERT is held plain).
    host = SimulatedHost(
        name="h",
        postgres_privileges={
            "postgres": [
                _grant_entry("SELECT", grantable=True),
                _grant_entry("INSERT"),
            ]
        },
    )
    ctx, commands = await materialize(
        host,
        postgresql.privilege,
        privileges=["SELECT", "INSERT"],
        role="alice",
        objects=["users"],
        present=False,
        grant_option=True,
    )
    assert ctx.changed
    assert ctx.reason == "postgresql will revoke grant option for SELECT from alice"
    assert commands == [
        PostgresqlPrivilegeCommand(
            privileges=("SELECT",),
            role="alice",
            objects=("public.users",),
            present=False,
            revoke_grant_option=True,
        )
    ]


async def test_privilege_table_request_matches_view_grants():
    # GRANT ON TABLE also addresses views: an existing "view" ACL row counts
    # as held, so the table request is a no-op (the shared alias set).
    host = SimulatedHost(
        name="h",
        postgres_privileges={
            "postgres": [
                _grant_entry("SELECT", name="active_users", object_type="view")
            ]
        },
    )
    ctx, commands = await materialize(
        host,
        postgresql.privilege,
        privileges="SELECT",
        role="alice",
        objects=["active_users"],
    )
    assert not ctx.changed
    assert commands == []


async def test_privilege_all_in_schema_targets_the_grantees_objects():
    # The simulation has no object catalog: ALL IN SCHEMA addresses the
    # objects the grantee already holds grants on in that schema.
    host = SimulatedHost(
        name="h",
        postgres_privileges={
            "postgres": [
                _grant_entry("SELECT"),
                _grant_entry("SELECT", name="orders"),
                _grant_entry("SELECT", name="elsewhere", schema="other"),
            ]
        },
    )
    ctx, commands = await materialize(
        host,
        postgresql.privilege,
        privileges="INSERT",
        role="alice",
        all_in_schema="public",
    )
    assert ctx.changed
    assert commands == [
        PostgresqlPrivilegeCommand(
            privileges=("INSERT",), role="alice", all_in_schema="public"
        )
    ]


async def test_privilege_all_in_schema_with_no_objects_is_a_noop():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host,
        postgresql.privilege,
        privileges="SELECT",
        role="alice",
        all_in_schema="app",
    )
    assert not ctx.changed
    assert ctx.reason == "no tables in schema app to manage"
    assert commands == []


async def test_privilege_database_grant_is_never_schema_qualified():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host,
        postgresql.privilege,
        privileges="CONNECT",
        role="alice",
        object_type="database",
        objects=["mydb"],
    )
    assert ctx.changed
    assert commands == [
        PostgresqlPrivilegeCommand(
            privileges=("CONNECT",), role="alice", object_type="database",
            objects=("mydb",),
        )
    ]
    assert commands[0].shell() == (
        "psql -Ac 'GRANT CONNECT ON DATABASE \"mydb\" TO \"alice\"'"
    )


async def test_privilege_absorbs_unknown_kwargs():
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize(
        host,
        postgresql.privilege,
        privileges="SELECT",
        role="alice",
        objects=["users"],
        future_flag=True,
    )
    assert commands == [
        PostgresqlPrivilegeCommand(
            privileges=("SELECT",), role="alice", objects=("public.users",)
        )
    ]


async def test_privilege_seam_grant_upgrade_and_revoke():
    # send_command maintains postgres_privileges: a grant upserts one entry
    # per (object, privilege), a re-grant ORs the grant option in, REVOKE
    # GRANT OPTION FOR only clears grantable, a plain revoke removes -- and
    # a repeat materialize against the mutated seam is a no-op.
    host = SimulatedHost(name="h")
    await send_command(
        host,
        PostgresqlPrivilegeCommand(
            privileges=("SELECT",), role="alice", objects=("users",)
        ),
    )
    assert host.postgres_privileges == {"postgres": [_grant_entry("SELECT")]}
    ctx, commands = await materialize(
        host,
        postgresql.privilege,
        privileges="SELECT",
        role="alice",
        objects=["users"],
    )
    assert not ctx.changed
    assert commands == []
    await send_command(
        host,
        PostgresqlPrivilegeCommand(
            privileges=("SELECT",), role="alice", objects=("users",),
            grant_option=True,
        ),
    )
    assert host.postgres_privileges["postgres"][0]["grantable"] is True
    await send_command(
        host,
        PostgresqlPrivilegeCommand(
            privileges=("SELECT",), role="alice", objects=("users",),
            present=False, revoke_grant_option=True,
        ),
    )
    assert host.postgres_privileges == {"postgres": [_grant_entry("SELECT")]}
    await send_command(
        host,
        PostgresqlPrivilegeCommand(
            privileges=("SELECT",), role="alice", objects=("users",), present=False
        ),
    )
    assert host.postgres_privileges == {"postgres": []}


# --- membership operation (reemote5-original) --------------------------------


def test_membership_command_renders_grant_revoke_and_admin_forms():
    assert PostgresqlMembershipCommand(member="alice", role="readonly").shell() == (
        "psql -Ac 'GRANT \"readonly\" TO \"alice\"'"
    )
    assert PostgresqlMembershipCommand(
        member="alice", role="readonly", admin_option=True
    ).shell() == "psql -Ac 'GRANT \"readonly\" TO \"alice\" WITH ADMIN OPTION'"
    assert PostgresqlMembershipCommand(
        member="alice", role="readonly", present=False
    ).shell() == "psql -Ac 'REVOKE \"readonly\" FROM \"alice\"'"
    assert PostgresqlMembershipCommand(
        member="alice", role="readonly", present=False, admin_only=True
    ).shell() == "psql -Ac 'REVOKE ADMIN OPTION FOR \"readonly\" FROM \"alice\"'"


async def test_membership_grants_an_absent_membership():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, postgresql.membership, role="alice", target_role="readonly"
    )
    assert ctx.changed
    assert ctx.reason == "postgresql role alice will be granted readonly"
    assert commands == [PostgresqlMembershipCommand(member="alice", role="readonly")]


async def test_membership_reconciles_the_admin_option_both_ways():
    held = {"role": "readonly", "member": "alice", "admin_option": False}
    host = SimulatedHost(name="h", postgres_role_memberships=[held])
    ctx, commands = await materialize(
        host, postgresql.membership, role="alice", target_role="readonly",
        admin_option=True,
    )
    assert ctx.changed
    assert ctx.reason == "postgresql role alice will gain admin option on readonly"
    assert commands == [
        PostgresqlMembershipCommand(member="alice", role="readonly", admin_option=True)
    ]
    # The downgrade keeps the membership: REVOKE ADMIN OPTION FOR only.
    host = SimulatedHost(
        name="h",
        postgres_role_memberships=[{**held, "admin_option": True}],
    )
    ctx, commands = await materialize(
        host, postgresql.membership, role="alice", target_role="readonly"
    )
    assert ctx.changed
    assert ctx.reason == "postgresql role alice will lose admin option on readonly"
    assert commands == [
        PostgresqlMembershipCommand(
            member="alice", role="readonly", present=False, admin_only=True
        )
    ]


async def test_membership_noop_when_already_a_member():
    host = SimulatedHost(
        name="h",
        postgres_role_memberships=[
            {"role": "readonly", "member": "alice", "admin_option": False}
        ],
    )
    ctx, commands = await materialize(
        host, postgresql.membership, role="alice", target_role="readonly"
    )
    assert not ctx.changed
    assert ctx.reason == "postgresql role alice is already a member of readonly"
    assert commands == []


async def test_membership_removes_an_existing_membership():
    host = SimulatedHost(
        name="h",
        postgres_role_memberships=[
            {"role": "readonly", "member": "alice", "admin_option": True}
        ],
    )
    ctx, commands = await materialize(
        host, postgresql.membership, role="alice", target_role="readonly",
        present=False,
    )
    assert ctx.changed
    assert ctx.reason == "postgresql role alice will be removed from readonly"
    assert commands == [
        PostgresqlMembershipCommand(member="alice", role="readonly", present=False)
    ]


async def test_membership_remove_noop_when_not_a_member():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, postgresql.membership, role="alice", target_role="readonly",
        present=False,
    )
    assert not ctx.changed
    assert ctx.reason == "postgresql role alice is not a member of readonly"
    assert commands == []


async def test_membership_absorbs_unknown_kwargs():
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize(
        host, postgresql.membership, role="alice", target_role="readonly",
        future_flag=True,
    )
    assert commands == [PostgresqlMembershipCommand(member="alice", role="readonly")]


async def test_membership_seam_grant_upgrade_downgrade_remove():
    host = SimulatedHost(name="h")
    await send_command(
        host, PostgresqlMembershipCommand(member="alice", role="readonly")
    )
    assert host.postgres_role_memberships == [
        {"role": "readonly", "member": "alice", "admin_option": False}
    ]
    await send_command(
        host,
        PostgresqlMembershipCommand(member="alice", role="readonly", admin_option=True),
    )
    assert host.postgres_role_memberships[0]["admin_option"] is True
    await send_command(
        host,
        PostgresqlMembershipCommand(
            member="alice", role="readonly", present=False, admin_only=True
        ),
    )
    assert host.postgres_role_memberships == [
        {"role": "readonly", "member": "alice", "admin_option": False}
    ]
    await send_command(
        host,
        PostgresqlMembershipCommand(member="alice", role="readonly", present=False),
    )
    assert host.postgres_role_memberships == []


# --- schema operation (reemote5-original) ------------------------------------


def test_schema_command_renders_create_alter_and_drop():
    assert PostgresqlSchemaCommand(schema="app").shell() == (
        "psql -Ac 'CREATE SCHEMA \"app\"'"
    )
    assert PostgresqlSchemaCommand(schema="app", owner="alice").shell() == (
        "psql -Ac 'CREATE SCHEMA \"app\" AUTHORIZATION \"alice\"'"
    )
    assert PostgresqlSchemaCommand(schema="app", owner="bob", alter=True).shell() == (
        "psql -Ac 'ALTER SCHEMA \"app\" OWNER TO \"bob\"'"
    )
    assert PostgresqlSchemaCommand(schema="app", present=False, cascade=True).shell() == (
        "psql -Ac 'DROP SCHEMA \"app\" CASCADE'"
    )


async def test_schema_creates_an_absent_schema():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, postgresql.schema, schema="app", owner="alice"
    )
    assert ctx.changed
    assert ctx.reason == "postgresql schema app will be created"
    assert commands == [PostgresqlSchemaCommand(schema="app", owner="alice")]


async def test_schema_reowns_an_existing_schema():
    host = SimulatedHost(
        name="h", postgres_schemas={"postgres": [{"name": "app", "owner": "alice"}]}
    )
    ctx, commands = await materialize(
        host, postgresql.schema, schema="app", owner="bob"
    )
    assert ctx.changed
    assert ctx.reason == "postgresql schema app owner will become bob"
    assert commands == [PostgresqlSchemaCommand(schema="app", owner="bob", alter=True)]


async def test_schema_noop_when_present_with_matching_owner():
    host = SimulatedHost(
        name="h", postgres_schemas={"postgres": [{"name": "app", "owner": "alice"}]}
    )
    ctx, commands = await materialize(
        host, postgresql.schema, schema="app", owner="alice"
    )
    assert not ctx.changed
    assert ctx.reason == "postgresql schema app already exists"
    assert commands == []


async def test_schema_drops_and_reports_an_absent_schema():
    host = SimulatedHost(
        name="h", postgres_schemas={"postgres": [{"name": "app", "owner": "alice"}]}
    )
    ctx, commands = await materialize(
        host, postgresql.schema, schema="app", present=False, cascade=True
    )
    assert ctx.changed
    assert ctx.reason == "postgresql schema app will be dropped"
    assert commands == [
        PostgresqlSchemaCommand(schema="app", present=False, cascade=True)
    ]
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, postgresql.schema, schema="app", present=False
    )
    assert not ctx.changed
    assert ctx.reason == "postgresql schema app does not exist"
    assert commands == []


async def test_schema_seam_create_reown_drop():
    host = SimulatedHost(name="h")
    await send_command(host, PostgresqlSchemaCommand(schema="app"))
    assert host.postgres_schemas == {"postgres": [{"name": "app", "owner": "postgres"}]}
    ctx, commands = await materialize(host, postgresql.schema, schema="app")
    assert not ctx.changed
    assert commands == []
    await send_command(
        host, PostgresqlSchemaCommand(schema="app", owner="bob", alter=True)
    )
    assert host.postgres_schemas["postgres"][0]["owner"] == "bob"
    await send_command(host, PostgresqlSchemaCommand(schema="app", present=False))
    assert host.postgres_schemas == {"postgres": []}


# --- extension operation (reemote5-original) ---------------------------------


def test_extension_command_renders_create_alter_and_drop():
    assert PostgresqlExtensionCommand(extension="uuid-ossp").shell() == (
        "psql -Ac 'CREATE EXTENSION \"uuid-ossp\"'"
    )
    sql = "CREATE EXTENSION \"uuid-ossp\" WITH SCHEMA \"public\" VERSION '1.1'"
    assert PostgresqlExtensionCommand(
        extension="uuid-ossp", schema="public", version="1.1"
    ).shell() == f"psql -Ac {shlex.quote(sql)}"
    assert PostgresqlExtensionCommand(
        extension="uuid-ossp", schema="ext", alter_schema=True
    ).shell() == "psql -Ac 'ALTER EXTENSION \"uuid-ossp\" SET SCHEMA \"ext\"'"
    sql = "ALTER EXTENSION \"uuid-ossp\" UPDATE TO '1.2'"
    assert PostgresqlExtensionCommand(
        extension="uuid-ossp", version="1.2", alter_version=True
    ).shell() == f"psql -Ac {shlex.quote(sql)}"
    assert PostgresqlExtensionCommand(
        extension="uuid-ossp", present=False, cascade=True
    ).shell() == "psql -Ac 'DROP EXTENSION \"uuid-ossp\" CASCADE'"


async def test_extension_creates_an_absent_extension():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, postgresql.extension, extension="uuid-ossp", schema="public",
        version="1.1",
    )
    assert ctx.changed
    assert ctx.reason == "postgresql extension uuid-ossp will be created"
    assert commands == [
        PostgresqlExtensionCommand(extension="uuid-ossp", schema="public", version="1.1")
    ]


async def test_extension_moves_schema_and_updates_version():
    # Both drifts yield one command each, reasons joined with "; ".
    host = SimulatedHost(
        name="h",
        postgres_extensions={
            "postgres": [{"name": "uuid-ossp", "version": "1.1", "schema": "public"}]
        },
    )
    ctx, commands = await materialize(
        host, postgresql.extension, extension="uuid-ossp", schema="ext", version="1.2"
    )
    assert ctx.changed
    assert ctx.reason == (
        "postgresql extension uuid-ossp will move to schema ext; "
        "postgresql extension uuid-ossp will update to version 1.2"
    )
    assert commands == [
        PostgresqlExtensionCommand(
            extension="uuid-ossp", schema="ext", alter_schema=True
        ),
        PostgresqlExtensionCommand(
            extension="uuid-ossp", version="1.2", alter_version=True
        ),
    ]


async def test_extension_noop_when_installed_with_matching_parameters():
    host = SimulatedHost(
        name="h",
        postgres_extensions={
            "postgres": [{"name": "uuid-ossp", "version": "1.1", "schema": "public"}]
        },
    )
    ctx, commands = await materialize(
        host, postgresql.extension, extension="uuid-ossp", schema="public",
        version="1.1",
    )
    assert not ctx.changed
    assert ctx.reason == "postgresql extension uuid-ossp is already installed"
    assert commands == []


async def test_extension_drops_and_reports_an_absent_extension():
    host = SimulatedHost(
        name="h",
        postgres_extensions={
            "postgres": [{"name": "uuid-ossp", "version": "1.1", "schema": "public"}]
        },
    )
    ctx, commands = await materialize(
        host, postgresql.extension, extension="uuid-ossp", present=False
    )
    assert ctx.changed
    assert ctx.reason == "postgresql extension uuid-ossp will be dropped"
    assert commands == [PostgresqlExtensionCommand(extension="uuid-ossp", present=False)]
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, postgresql.extension, extension="uuid-ossp", present=False
    )
    assert not ctx.changed
    assert ctx.reason == "postgresql extension uuid-ossp does not exist"
    assert commands == []


async def test_extension_seam_create_update_move_drop():
    host = SimulatedHost(name="h")
    await send_command(host, PostgresqlExtensionCommand(extension="uuid-ossp"))
    # An unset version gets the "" placeholder -- the simulation has no
    # control files to resolve a default version from.
    assert host.postgres_extensions == {
        "postgres": [{"name": "uuid-ossp", "version": "", "schema": "public"}]
    }
    await send_command(
        host,
        PostgresqlExtensionCommand(
            extension="uuid-ossp", version="1.2", alter_version=True
        ),
    )
    assert host.postgres_extensions["postgres"][0]["version"] == "1.2"
    await send_command(
        host,
        PostgresqlExtensionCommand(extension="uuid-ossp", schema="ext", alter_schema=True),
    )
    assert host.postgres_extensions["postgres"][0]["schema"] == "ext"
    ctx, commands = await materialize(
        host, postgresql.extension, extension="uuid-ossp", schema="ext", version="1.2"
    )
    assert not ctx.changed
    assert commands == []
    await send_command(
        host, PostgresqlExtensionCommand(extension="uuid-ossp", present=False)
    )
    assert host.postgres_extensions == {"postgres": []}


# --- index operation (reemote5-original) -------------------------------------


INDEX_ENTRY = {
    "schema": "public",
    "name": "users_email_idx",
    "table": "users",
    "owner": "alice",
    "tablespace": None,
    "unique": True,
    "method": "btree",
}


def test_index_command_renders_create_and_drop():
    assert PostgresqlIndexCommand(
        index="users_email_idx", table="users", columns=("email",)
    ).shell() == "psql -Ac 'CREATE INDEX \"users_email_idx\" ON \"users\" (email)'"
    # UNIQUE + CONCURRENTLY + a non-btree method + tablespace all stack;
    # expressions render verbatim inside the parentheses.
    assert PostgresqlIndexCommand(
        index="users_email_idx",
        table="users",
        columns=("lower(email)",),
        unique=True,
        method="gin",
        tablespace="fast",
        concurrent=True,
    ).shell() == (
        "psql -Ac 'CREATE UNIQUE INDEX CONCURRENTLY \"users_email_idx\" "
        "ON \"users\" USING gin (lower(email)) TABLESPACE \"fast\"'"
    )
    assert PostgresqlIndexCommand(
        index="app.users_email_idx", present=False, concurrent=True, cascade=True
    ).shell() == (
        "psql -Ac 'DROP INDEX CONCURRENTLY \"app\".\"users_email_idx\" CASCADE'"
    )


async def test_index_creates_an_absent_index():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, postgresql.index, index="users_email_idx", table="users",
        columns=["email"], unique=True,
    )
    assert ctx.changed
    assert ctx.reason == "postgresql index users_email_idx will be created"
    assert commands == [
        PostgresqlIndexCommand(
            index="users_email_idx", table="users", columns=("email",), unique=True
        )
    ]


async def test_index_requires_table_and_columns_when_present():
    host = SimulatedHost(name="h")
    with pytest.raises(ValueError, match="requires table and columns when present=True"):
        await materialize(host, postgresql.index, index="users_email_idx")


async def test_index_rejects_an_unknown_access_method():
    # method is whitelist-validated so a caller string never reaches the
    # SQL unquoted (the descriptor renders USING <method> bare).
    host = SimulatedHost(name="h")
    with pytest.raises(ValueError, match="unknown postgresql index method"):
        await materialize(
            host, postgresql.index, index="users_email_idx", table="users",
            columns=["email"], method="btree; DROP TABLE users",
        )


async def test_index_noop_when_the_definition_matches():
    # tablespace=None means "don't care": the seeded None is not a drift.
    host = SimulatedHost(name="h", postgres_indexes={"postgres": [INDEX_ENTRY]})
    ctx, commands = await materialize(
        host, postgresql.index, index="users_email_idx", table="public.users",
        columns=["email"], unique=True,
    )
    assert not ctx.changed
    assert ctx.reason == "postgresql index users_email_idx already exists"
    assert commands == []


async def test_index_definition_drift_raises_in_prepare():
    # unique and method differ -> sorted labels; an index is never silently
    # rebuilt (it may back a UNIQUE constraint).
    host = SimulatedHost(name="h", postgres_indexes={"postgres": [INDEX_ENTRY]})
    with pytest.raises(OperationError, match=r"\(method, unique differ\)"):
        await materialize(
            host, postgresql.index, index="users_email_idx", table="users",
            columns=["email"], method="gin",
        )


async def test_index_named_tablespace_participates_in_the_drift_check():
    host = SimulatedHost(name="h", postgres_indexes={"postgres": [INDEX_ENTRY]})
    with pytest.raises(OperationError, match=r"\(tablespace differ\)"):
        await materialize(
            host, postgresql.index, index="users_email_idx", table="users",
            columns=["email"], unique=True, tablespace="fast",
        )


async def test_index_drops_and_reports_an_absent_index():
    host = SimulatedHost(name="h", postgres_indexes={"postgres": [INDEX_ENTRY]})
    ctx, commands = await materialize(
        host, postgresql.index, index="public.users_email_idx", present=False,
        concurrent=True,
    )
    assert ctx.changed
    assert ctx.reason == "postgresql index public.users_email_idx will be dropped"
    assert commands == [
        PostgresqlIndexCommand(
            index="public.users_email_idx", present=False, concurrent=True
        )
    ]
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, postgresql.index, index="users_email_idx", present=False
    )
    assert not ctx.changed
    assert ctx.reason == "postgresql index users_email_idx does not exist"
    assert commands == []


async def test_index_seam_create_then_noop_then_drop():
    host = SimulatedHost(name="h")
    await send_command(
        host,
        PostgresqlIndexCommand(
            index="app.users_email_idx", table="public.users", columns=("email",),
            unique=True,
        ),
    )
    assert host.postgres_indexes == {
        "postgres": [
            {
                "schema": "app",
                "name": "users_email_idx",
                "table": "users",
                "owner": "postgres",
                "tablespace": None,
                "unique": True,
                "method": "btree",
            }
        ]
    }
    ctx, commands = await materialize(
        host, postgresql.index, index="app.users_email_idx", table="users",
        columns=["email"], unique=True,
    )
    assert not ctx.changed
    assert commands == []
    await send_command(
        host, PostgresqlIndexCommand(index="app.users_email_idx", present=False)
    )
    assert host.postgres_indexes == {"postgres": []}


# --- setting operation (reemote5-original) -----------------------------------


MAX_CONNECTIONS_ENTRY = {
    "name": "max_connections",
    "setting": "100",
    "unit": None,
    "source": "configuration file",
    "pending_restart": False,
}


def test_setting_command_renders_set_and_reset():
    sql = "ALTER SYSTEM SET \"max_connections\" = '200'"
    assert PostgresqlSettingCommand(
        name="max_connections", value="200"
    ).shell() == f"psql -Ac {shlex.quote(sql)}"
    assert PostgresqlSettingCommand(name="max_connections", present=False).shell() == (
        "psql -Ac 'ALTER SYSTEM RESET \"max_connections\"'"
    )


async def test_setting_requires_a_value_and_a_known_parameter():
    host = SimulatedHost(name="h", postgres_settings=[MAX_CONNECTIONS_ENTRY])
    with pytest.raises(ValueError, match="requires value when present=True"):
        await materialize(host, postgresql.setting, setting="max_connections")
    with pytest.raises(ValueError, match="unknown postgresql configuration parameter"):
        await materialize(host, postgresql.setting, setting="not_a_guc", value="1")


async def test_setting_sets_a_differing_value():
    host = SimulatedHost(name="h", postgres_settings=[MAX_CONNECTIONS_ENTRY])
    ctx, commands = await materialize(
        host, postgresql.setting, setting="max_connections", value="200"
    )
    assert ctx.changed
    assert ctx.reason == "postgresql setting max_connections will be set to 200"
    assert commands == [PostgresqlSettingCommand(name="max_connections", value="200")]


async def test_setting_reload_follows_a_change_only():
    host = SimulatedHost(name="h", postgres_settings=[MAX_CONNECTIONS_ENTRY])
    ctx, commands = await materialize(
        host, postgresql.setting, setting="max_connections", value="200", reload=True
    )
    assert ctx.changed
    assert commands == [
        PostgresqlSettingCommand(name="max_connections", value="200"),
        PostgresqlSqlCommand(sql="SELECT pg_reload_conf()"),
    ]
    # No change -> no reload either.
    host = SimulatedHost(
        name="h", postgres_settings=[{**MAX_CONNECTIONS_ENTRY, "setting": "200"}]
    )
    ctx, commands = await materialize(
        host, postgresql.setting, setting="max_connections", value="200", reload=True
    )
    assert not ctx.changed
    assert ctx.reason == "postgresql setting max_connections is already 200"
    assert commands == []


async def test_setting_resets_a_non_default_source():
    host = SimulatedHost(name="h", postgres_settings=[MAX_CONNECTIONS_ENTRY])
    ctx, commands = await materialize(
        host, postgresql.setting, setting="max_connections", present=False
    )
    assert ctx.changed
    assert ctx.reason == "postgresql setting max_connections will be reset"
    assert commands == [PostgresqlSettingCommand(name="max_connections", present=False)]


async def test_setting_reset_noop_when_already_the_default():
    host = SimulatedHost(
        name="h", postgres_settings=[{**MAX_CONNECTIONS_ENTRY, "source": "default"}]
    )
    ctx, commands = await materialize(
        host, postgresql.setting, setting="max_connections", present=False
    )
    assert not ctx.changed
    assert ctx.reason == "postgresql setting max_connections is already the default"
    assert commands == []


async def test_setting_absorbs_unknown_kwargs():
    host = SimulatedHost(name="h", postgres_settings=[MAX_CONNECTIONS_ENTRY])
    _ctx, commands = await materialize(
        host, postgresql.setting, setting="max_connections", value="200", future_flag=True
    )
    assert commands == [PostgresqlSettingCommand(name="max_connections", value="200")]


async def test_setting_seam_set_then_noop_then_reset():
    host = SimulatedHost(name="h", postgres_settings=[MAX_CONNECTIONS_ENTRY])
    await send_command(
        host, PostgresqlSettingCommand(name="max_connections", value="200")
    )
    entry = host.postgres_settings[0]
    assert entry["setting"] == "200"
    assert entry["source"] == "configuration file"
    ctx, commands = await materialize(
        host, postgresql.setting, setting="max_connections", value="200"
    )
    assert not ctx.changed
    assert commands == []
    await send_command(
        host, PostgresqlSettingCommand(name="max_connections", present=False)
    )
    assert host.postgres_settings[0]["source"] == "default"
    ctx, commands = await materialize(
        host, postgresql.setting, setting="max_connections", present=False
    )
    assert not ctx.changed
    assert commands == []


# --- tablespace operation (reemote5-original) --------------------------------


def test_tablespace_command_renders_create_alter_and_drop():
    sql = "CREATE TABLESPACE \"fast\" OWNER \"alice\" LOCATION '/mnt/fast'"
    assert PostgresqlTablespaceCommand(
        tablespace="fast", location="/mnt/fast", owner="alice"
    ).shell() == f"psql -Ac {shlex.quote(sql)}"
    assert PostgresqlTablespaceCommand(
        tablespace="fast", owner="bob", alter=True
    ).shell() == "psql -Ac 'ALTER TABLESPACE \"fast\" OWNER TO \"bob\"'"
    assert PostgresqlTablespaceCommand(tablespace="fast", present=False).shell() == (
        "psql -Ac 'DROP TABLESPACE \"fast\"'"
    )


async def test_tablespace_creates_an_absent_tablespace():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, postgresql.tablespace, tablespace="fast", location="/mnt/fast",
        owner="alice",
    )
    assert ctx.changed
    assert ctx.reason == "postgresql tablespace fast will be created"
    assert commands == [
        PostgresqlTablespaceCommand(
            tablespace="fast", location="/mnt/fast", owner="alice"
        )
    ]


async def test_tablespace_requires_a_location_when_creating():
    host = SimulatedHost(name="h")
    with pytest.raises(ValueError, match="requires location when creating"):
        await materialize(host, postgresql.tablespace, tablespace="fast")


async def test_tablespace_reowns_and_noops_on_an_existing_one():
    host = SimulatedHost(name="h", postgres_tablespaces=[FAST_SPACE])
    ctx, commands = await materialize(
        host, postgresql.tablespace, tablespace="fast", location="/mnt/fast",
        owner="bob",
    )
    assert ctx.changed
    assert ctx.reason == "postgresql tablespace fast owner will become bob"
    assert commands == [
        PostgresqlTablespaceCommand(tablespace="fast", owner="bob", alter=True)
    ]
    ctx, commands = await materialize(
        host, postgresql.tablespace, tablespace="fast", location="/mnt/fast"
    )
    assert not ctx.changed
    assert ctx.reason == "postgresql tablespace fast already exists"
    assert commands == []


async def test_tablespace_location_drift_raises_in_prepare():
    # PostgreSQL cannot move a tablespace: a differing location fails fast
    # rather than pretending to reconcile it.
    host = SimulatedHost(name="h", postgres_tablespaces=[FAST_SPACE])
    with pytest.raises(
        OperationError,
        match=r"cannot change location \(/mnt/fast -> /mnt/other\); drop it first",
    ):
        await materialize(
            host, postgresql.tablespace, tablespace="fast", location="/mnt/other"
        )


async def test_tablespace_drops_and_reports_an_absent_tablespace():
    host = SimulatedHost(name="h", postgres_tablespaces=[FAST_SPACE])
    ctx, commands = await materialize(
        host, postgresql.tablespace, tablespace="fast", present=False
    )
    assert ctx.changed
    assert ctx.reason == "postgresql tablespace fast will be dropped"
    assert commands == [PostgresqlTablespaceCommand(tablespace="fast", present=False)]
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, postgresql.tablespace, tablespace="fast", present=False
    )
    assert not ctx.changed
    assert ctx.reason == "postgresql tablespace fast does not exist"
    assert commands == []


async def test_tablespace_seam_create_reown_drop():
    host = SimulatedHost(name="h")
    await send_command(
        host, PostgresqlTablespaceCommand(tablespace="fast", location="/mnt/fast")
    )
    # A synthetic OID one past the highest seeded one (none -> 16385).
    assert host.postgres_tablespaces == [
        {"oid": 16385, "name": "fast", "owner": "postgres", "location": "/mnt/fast"}
    ]
    ctx, commands = await materialize(
        host, postgresql.tablespace, tablespace="fast", location="/mnt/fast"
    )
    assert not ctx.changed
    assert commands == []
    await send_command(
        host, PostgresqlTablespaceCommand(tablespace="fast", owner="bob", alter=True)
    )
    assert host.postgres_tablespaces[0]["owner"] == "bob"
    await send_command(host, PostgresqlTablespaceCommand(tablespace="fast", present=False))
    assert host.postgres_tablespaces == []


# --- publication operation (reemote5-original) -------------------------------


PUBLICATION_ENTRY = {
    "name": "pub",
    "owner": "postgres",
    "all_tables": False,
    "tables": ["public.users"],
}


def test_publication_command_renders_create_alter_and_drop():
    assert PostgresqlPublicationCommand(
        publication="pub", tables=("public.a", "public.b")
    ).shell() == (
        "psql -Ac 'CREATE PUBLICATION \"pub\" FOR TABLE \"public\".\"a\", "
        "\"public\".\"b\"'"
    )
    assert PostgresqlPublicationCommand(
        publication="pub", all_tables=True
    ).shell() == "psql -Ac 'CREATE PUBLICATION \"pub\" FOR ALL TABLES'"
    assert PostgresqlPublicationCommand(
        publication="pub", tables=("public.a",), owner="alice", alter=True
    ).shell() == (
        "psql -Ac 'ALTER PUBLICATION \"pub\" SET TABLE \"public\".\"a\"; "
        "ALTER PUBLICATION \"pub\" OWNER TO \"alice\";'"
    )
    assert PostgresqlPublicationCommand(publication="pub", present=False).shell() == (
        "psql -Ac 'DROP PUBLICATION \"pub\"'"
    )


async def test_publication_requires_tables_or_all_tables():
    host = SimulatedHost(name="h")
    with pytest.raises(ValueError, match="requires tables or all_tables when present=True"):
        await materialize(host, postgresql.publication, publication="pub")


async def test_publication_creates_with_a_separate_owner_alter():
    # CREATE PUBLICATION has no OWNER clause: naming an owner yields a
    # second OWNER TO alter after the create. Table names default to the
    # public schema and are sorted/deduped.
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, postgresql.publication, publication="pub", tables=["users", "orders"],
        owner="alice",
    )
    assert ctx.changed
    assert ctx.reason == (
        "postgresql publication pub will be created; owner will become alice"
    )
    assert commands == [
        PostgresqlPublicationCommand(
            publication="pub", tables=("public.orders", "public.users")
        ),
        PostgresqlPublicationCommand(publication="pub", owner="alice", alter=True),
    ]


async def test_publication_mode_switch_raises_in_prepare():
    host = SimulatedHost(
        name="h",
        postgres_publications={
            "postgres": [{**PUBLICATION_ENTRY, "all_tables": True, "tables": []}]
        },
    )
    with pytest.raises(OperationError, match="cannot switch between FOR TABLE and FOR ALL"):
        await materialize(
            host, postgresql.publication, publication="pub", tables=["users"]
        )


async def test_publication_alters_tables_and_owner():
    host = SimulatedHost(name="h", postgres_publications={"postgres": [PUBLICATION_ENTRY]})
    ctx, commands = await materialize(
        host, postgresql.publication, publication="pub", tables=["orders"],
        owner="alice",
    )
    assert ctx.changed
    assert ctx.reason == (
        "postgresql publication pub: tables will be updated; owner will become alice"
    )
    assert commands == [
        PostgresqlPublicationCommand(
            publication="pub", tables=("public.orders",), owner="alice", alter=True
        )
    ]


async def test_publication_noop_when_the_parameters_match():
    host = SimulatedHost(name="h", postgres_publications={"postgres": [PUBLICATION_ENTRY]})
    ctx, commands = await materialize(
        host, postgresql.publication, publication="pub", tables=["public.users"]
    )
    assert not ctx.changed
    assert ctx.reason == (
        "postgresql publication pub already exists with the same parameters"
    )
    assert commands == []


async def test_publication_drops_and_reports_an_absent_publication():
    host = SimulatedHost(name="h", postgres_publications={"postgres": [PUBLICATION_ENTRY]})
    ctx, commands = await materialize(
        host, postgresql.publication, publication="pub", present=False
    )
    assert ctx.changed
    assert ctx.reason == "postgresql publication pub will be dropped"
    assert commands == [PostgresqlPublicationCommand(publication="pub", present=False)]
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, postgresql.publication, publication="pub", present=False
    )
    assert not ctx.changed
    assert ctx.reason == "postgresql publication pub does not exist"
    assert commands == []


async def test_publication_seam_create_own_then_noop_then_drop():
    host = SimulatedHost(name="h")
    await send_command(
        host,
        PostgresqlPublicationCommand(publication="pub", tables=("public.users",)),
    )
    await send_command(
        host, PostgresqlPublicationCommand(publication="pub", owner="alice", alter=True)
    )
    assert host.postgres_publications == {
        "postgres": [{**PUBLICATION_ENTRY, "owner": "alice"}]
    }
    ctx, commands = await materialize(
        host, postgresql.publication, publication="pub", tables=["users"], owner="alice"
    )
    assert not ctx.changed
    assert commands == []
    await send_command(host, PostgresqlPublicationCommand(publication="pub", present=False))
    assert host.postgres_publications == {"postgres": []}


# --- subscription operation (reemote5-original) ------------------------------


SUBSCRIPTION_ENTRY = {
    "name": "sub",
    "owner": "postgres",
    "enabled": True,
    "publications": ["pub_a"],
}


def test_subscription_command_renders_create_alter_and_drop():
    sql = (
        "CREATE SUBSCRIPTION \"sub\" CONNECTION 'host=192.168.0.10 dbname=mydb' "
        "PUBLICATION \"pub_a\", \"pub_b\""
    )
    assert PostgresqlSubscriptionCommand(
        subscription="sub",
        connection="host=192.168.0.10 dbname=mydb",
        publications=("pub_a", "pub_b"),
    ).shell() == f"psql -Ac {shlex.quote(sql)}"
    sql += " WITH (enabled = false)"
    assert PostgresqlSubscriptionCommand(
        subscription="sub",
        connection="host=192.168.0.10 dbname=mydb",
        publications=("pub_a", "pub_b"),
        enabled=False,
    ).shell() == f"psql -Ac {shlex.quote(sql)}"
    assert PostgresqlSubscriptionCommand(
        subscription="sub", publications=("pub_b",), enabled=False, alter=True
    ).shell() == (
        "psql -Ac 'ALTER SUBSCRIPTION \"sub\" SET PUBLICATION \"pub_b\"; "
        "ALTER SUBSCRIPTION \"sub\" DISABLE;'"
    )
    assert PostgresqlSubscriptionCommand(subscription="sub", present=False).shell() == (
        "psql -Ac 'DROP SUBSCRIPTION \"sub\"'"
    )


async def test_subscription_validates_connection_and_publication_names():
    host = SimulatedHost(name="h")
    with pytest.raises(
        ValueError, match="requires connection and publications when present=True"
    ):
        await materialize(host, postgresql.subscription, subscription="sub")
    with pytest.raises(ValueError, match="may not contain commas or braces"):
        await materialize(
            host, postgresql.subscription, subscription="sub",
            connection="host=192.168.0.10", publications=["pub,a"],
        )


async def test_subscription_creates_with_sorted_publications():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, postgresql.subscription, subscription="sub",
        connection="host=192.168.0.10", publications=["pub_b", "pub_a", "pub_b"],
    )
    assert ctx.changed
    assert ctx.reason == "postgresql subscription sub will be created"
    assert commands == [
        PostgresqlSubscriptionCommand(
            subscription="sub",
            connection="host=192.168.0.10",
            publications=("pub_a", "pub_b"),
            enabled=True,
        )
    ]


async def test_subscription_alters_publications_and_enabled():
    host = SimulatedHost(
        name="h", postgres_subscriptions={"postgres": [SUBSCRIPTION_ENTRY]}
    )
    ctx, commands = await materialize(
        host, postgresql.subscription, subscription="sub",
        connection="host=192.168.0.10", publications=["pub_b"], enabled=False,
    )
    assert ctx.changed
    assert ctx.reason == (
        "postgresql subscription sub will be altered "
        "(publications will be updated, disabled)"
    )
    assert commands == [
        PostgresqlSubscriptionCommand(
            subscription="sub", publications=("pub_b",), enabled=False, alter=True
        )
    ]


async def test_subscription_noop_ignores_connection_drift():
    # The fact deliberately hides subconninfo (it can embed a password), so
    # a different connection string is never a drift.
    host = SimulatedHost(
        name="h", postgres_subscriptions={"postgres": [SUBSCRIPTION_ENTRY]}
    )
    ctx, commands = await materialize(
        host, postgresql.subscription, subscription="sub",
        connection="host=elsewhere", publications=["pub_a"],
    )
    assert not ctx.changed
    assert ctx.reason == (
        "postgresql subscription sub already exists with the same parameters"
    )
    assert commands == []


async def test_subscription_drops_and_reports_an_absent_subscription():
    host = SimulatedHost(
        name="h", postgres_subscriptions={"postgres": [SUBSCRIPTION_ENTRY]}
    )
    ctx, commands = await materialize(
        host, postgresql.subscription, subscription="sub", present=False
    )
    assert ctx.changed
    assert ctx.reason == "postgresql subscription sub will be dropped"
    assert commands == [
        PostgresqlSubscriptionCommand(subscription="sub", present=False)
    ]
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, postgresql.subscription, subscription="sub", present=False
    )
    assert not ctx.changed
    assert ctx.reason == "postgresql subscription sub does not exist"
    assert commands == []


async def test_subscription_seam_create_disable_drop():
    host = SimulatedHost(name="h")
    await send_command(
        host,
        PostgresqlSubscriptionCommand(
            subscription="sub", connection="host=192.168.0.10",
            publications=("pub_a",),
        ),
    )
    # The connection string is never stored (the fact hides conninfo).
    assert host.postgres_subscriptions == {"postgres": [SUBSCRIPTION_ENTRY]}
    await send_command(
        host,
        PostgresqlSubscriptionCommand(
            subscription="sub", enabled=False, alter=True
        ),
    )
    assert host.postgres_subscriptions["postgres"][0]["enabled"] is False
    ctx, commands = await materialize(
        host, postgresql.subscription, subscription="sub",
        connection="host=192.168.0.10", publications=["pub_a"], enabled=False,
    )
    assert not ctx.changed
    assert commands == []
    await send_command(
        host, PostgresqlSubscriptionCommand(subscription="sub", present=False)
    )
    assert host.postgres_subscriptions == {"postgres": []}


# --- end to end (run / run_prepare + run_apply) -------------------------------


async def test_postgresql_role_end_to_end_create_then_noop():
    # Through run(): the first apply creates the role into the simulated
    # state (elevated -- psql runs as the postgres OS user via peer auth);
    # a second run reads it back and is a no-op.
    def deploy(host):
        yield op(postgresql.role, name="Create the app role", role="app", sudo=True)

    host = SimulatedHost(name="h")

    def factory(entry):
        return host  # reuse ONE host so the second run sees the first's create

    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}], deploy, host_factory=factory
    )
    assert plans["h"][0].changed
    (entry,) = report.log
    assert entry.changed
    assert "app" in host.postgres_roles
    assert "$ sudo psql -Ac 'CREATE ROLE \"app\"" in format_log(report)

    _plans2, report2, _hosts2 = await run(
        [{"host": "h", "connection": {}}], deploy, host_factory=factory
    )
    (entry2,) = report2.log
    assert not entry2.changed
    assert entry2.reason == "postgresql role app exists and does not need updates"


async def test_postgresql_dry_run_sends_nothing():
    def deploy(host):
        yield op(postgresql.schema, name="Create the app schema", schema="app")

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        deploy,
        dry=True,
        host_factory=lambda entry: host,
    )
    assert report is None
    assert plans["h"][0].changed
    assert plans["h"][0].commands == (PostgresqlSchemaCommand(schema="app"),)
    assert host.postgres_schemas == {}
    assert "changed" in format_plan(plans)


async def test_postgresql_create_then_noop_in_one_deploy():
    # The prepare plan (which never sends) shows both identical creates as
    # changes; the apply phase's fresh per-step fact cache sees the first
    # create, so the second is a no-op in the log.
    def deploy(host):
        yield op(postgresql.schema, name="create", schema="app")
        yield op(postgresql.schema, name="re-create", schema="app")

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], deploy)
    assert plans["h"][0].changed
    assert plans["h"][1].changed  # prepare never sends, so still absent
    report = await run_apply(runtimes, plans=plans)
    first, second = report.log
    assert first.changed
    assert not second.changed
    assert second.reason == "postgresql schema app already exists"
    assert host.postgres_schemas == {"postgres": [{"name": "app", "owner": "postgres"}]}


async def test_postgresql_determinism_violation_is_a_hard_error():
    # Branching the deploy on a mutable seam field diverges between the
    # prepare and apply request sequences -> DeterminismError.
    def nondeterministic_deploy(host):
        yield op(postgresql.role, name="Create the app role", role="app")
        if "app" in host.postgres_roles:  # mutable-fact branch: forbidden
            yield op(postgresql.membership, name="join", role="app", target_role="admins")
        else:
            yield op(postgresql.schema, name="schema", schema="app")

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], nondeterministic_deploy)
    with pytest.raises(DeterminismError, match="diverges"):
        await run_apply(runtimes, plans=plans)


async def test_postgresql_provisioning_pipeline_in_lockstep():
    # role -> database (owned by the role) -> membership -> schema, each
    # step's apply seeing the previous step's mutation through the fresh
    # fact cache: the database's owner OID resolves to the just-created
    # role, and every step reports a change.
    def deploy(host):
        yield op(postgresql.role, name="Create the app role", role="app", sudo=True)
        yield op(
            postgresql.database, name="Create the app database",
            database="appdb", owner="app", sudo=True,
        )
        yield op(
            postgresql.membership, name="Grant admins to app",
            role="app", target_role="admins", sudo=True,
        )
        yield op(
            postgresql.schema, name="Create the app schema",
            schema="app", owner="app", psql_database="appdb", sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], deploy)
    assert [p.changed for p in plans["h"]] == [True, True, True, True]
    report = await run_apply(runtimes, plans=plans)
    assert [entry.changed for entry in report.log] == [True, True, True, True]
    assert host.postgres_databases["appdb"]["owner"] == "app"
    assert host.postgres_databases["appdb"]["dba"] == host.postgres_roles["app"]["oid"]
    assert host.postgres_role_memberships == [
        {"role": "admins", "member": "app", "admin_option": False}
    ]
    assert host.postgres_schemas == {"appdb": [{"name": "app", "owner": "app"}]}