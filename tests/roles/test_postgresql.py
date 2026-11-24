# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``postgresql`` role (design doc §17.7, T9).

The role is library code now, so it is imported (``from reemote.roles import
postgresql, postgresql_data``) and replayed against ``SimulatedHost`` through the
real wave driver. It is the only role driven as **two** ``run_waves`` passes,
because it is two factories on two inventory views (design doc section 13):

* **pass A** -- ``postgresql(v)`` on the ordinary (root) view: the distribution
  and FIPS barriers, install, initialize, the ``stat`` gate and ``initdb`` as
  ``act`` barriers, configure, the service and the handler.
* **pass B** -- ``postgresql_data(v)`` on a second inventory whose
  ``privilege.sudo_user`` names the OS ``postgres`` user: the ``PostgresqlRoles``
  catalog barrier (a reachability gate), then users, databases and privileges.

Two assertions carry the migration's claims, exactly as in the
``tests/roles/test_apache.py`` precedent:

* **convergence** -- run both passes, then run them again against the same
  persistent hosts, and the second run reports no change anywhere. Because the
  rerun re-derives the distribution, the FIPS state, the ``stat`` gate and the
  catalog read through ``run_waves``, this proves the barriers are honest rather
  than that a frozen plan was reused.
* **handler gating** -- the ``Restart postgresql (handler)`` line exists only on
  the pass-A run whose checkpoints found a changed notifying request, and it runs
  in pass A (its union is ``initialize | configure``), *before* the data plane.
  The converged pass has no handler record at all.

One simulation limit is bridged explicitly (it is a *simulation* limit, not a
library bug): ``initdb`` is a raw ``Shell`` action, and a raw shell has no typed
command seam, so the cluster payload a real ``initdb`` writes (``PG_VERSION``, a
stock ``postgresql.conf``/``pg_hba.conf``, the bootstrap ``postgres`` superuser)
and the ``stat`` gate's flip to exit 0 must be planted the moment the action runs
-- there is no between-deploy gap under ``run_waves``. :class:`PgSimulatedHost`
adds exactly that side effect; the action's *answer* still comes from
``shell_results`` through ``super()._run_shell``, so nothing is faked.
"""

import posixpath
from typing import Any

import pytest
from pydantic import ValidationError

from reemote import run_waves
from reemote.roles import PostgresqlVars, postgresql, postgresql_data
from reemote.roles.postgresql import (
    REDHAT_VARS,
    make_conf_transform,
    normalize_privs,
    parse_role_flags,
    render_pg_hba,
    resolve_data,
    resolve_plan,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from tests.simulated_host import SimulatedHost

MAJOR = 9
DATA_DIR = "/var/lib/pgsql/data"
CONF_PATH = posixpath.join(DATA_DIR, "postgresql.conf")
HBA_PATH = posixpath.join(DATA_DIR, "pg_hba.conf")
PG_VERSION_PATH = posixpath.join(DATA_DIR, "PG_VERSION")
PROFILE_PATH = "/etc/profile.d/postgres.sh"
LOG_DIR = posixpath.join(DATA_DIR, "log")
SOCKET_DIR = "/var/run/postgresql"
UNIT = "postgresql.service"

# The exact shells the action transport seam (#16) renders on the *normal* view
# (no privilege.sudo_user, so ``sudo=True`` is a bare ``sudo``): the stat gate is
# elevated root, and initdb embeds its own ``sudo -u postgres`` drop inside that.
STAT_SHELL = f"sudo test -e {PG_VERSION_PATH}"
INITDB_SHELL = f"sudo sudo -u postgres /usr/bin/initdb -D {DATA_DIR}"

CENTOS_DISTRO = {
    "name": "CentOS Stream",
    "family": "RedHat",
    "major": MAJOR,
    "minor": None,
    "release_meta": {},
}

# A stock initdb postgresql.conf snippet: the two options the role's default
# lineinfile loop manages sit commented-out, the shape the transform's
# "^#?option.+$" regexp replaces (last match wins).
STOCK_CONF = (
    "# -----------------------------\n"
    "# PostgreSQL configuration file\n"
    "# -----------------------------\n"
    "#listen_addresses = 'localhost'\n"
    "#unix_socket_directories = '/tmp'\n"
    "#log_directory = 'log'\n"
    "#logging_collector = off\n"
)
STOCK_HBA = (
    "# PostgreSQL Client Authentication Configuration File\n"
    "local   all             all                                     peer\n"
    "host    all             all             127.0.0.1/32            ident\n"
)

# The bootstrap superuser initdb creates (the pg_roles catalog shape).
BOOTSTRAP_ROLE = {
    "super": True,
    "inherit": True,
    "createrole": True,
    "createdb": True,
    "canlogin": True,
    "replication": True,
    "connlimit": -1,
    "password": None,
    "validuntil": None,
    "bypassrls": True,
    "config": None,
    "oid": 10,
}

SETUP_NAMES = (
    "Ensure PostgreSQL packages are installed.",
    "Ensure PostgreSQL Python libraries are installed.",
)
INITIALIZE_NAMES = (
    "Set PostgreSQL environment variables.",
    "Ensure PostgreSQL data directory exists.",
)
HANDLER_NAME = "Restart postgresql (handler)"
CATALOG_BARRIER = "read pg_roles catalog"


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one CentOS target, one Ubuntu host to be skipped.

    The role variables ride on the entry as ``postgresql_``-prefixed extra keys,
    which is what ``resolve_vars(PostgresqlVars, "postgresql_", inventory)``
    binds, so a test changes a variable exactly the way an operator does. With no
    overrides the role defaults apply (no databases/users/privileges).
    """
    entry: dict[str, Any] = {
        "host": "web1",
        "family": "RedHat",
        "connection": {
            "address": "192.0.2.10",
            "username": "supervisor",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "centos",
    }
    entry.update(overrides)
    return [
        entry,
        {
            "host": "ubuntu",
            "family": "Ubuntu",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "ubuntu",
        },
    ]


def data_overrides() -> dict[str, Any]:
    """The doc's example inventory: one database, one user, one table grant."""
    return {
        "postgresql_databases": [{"name": "exampledb"}],
        "postgresql_users": [{"name": "jdoe", "role_attr_flags": "CREATEDB"}],
        "postgresql_privs": [
            {
                "db": "exampledb",
                "roles": "jdoe",
                "privs": "SELECT,INSERT",
                "type": "table",
                "objs": "accounts",
            }
        ],
    }


def _regular_file(
    host: SimulatedHost,
    path: str,
    data: bytes,
    mode: int,
    *,
    user: str = "root",
    group: str = "root",
) -> None:
    """Seed an existing regular file: content, existence and stat metadata."""
    host.remote_files[path] = data
    host.existing_files.add(path)
    host.file_stats[path] = {
        "user": user,
        "group": group,
        "mode": mode,
        "size": len(data),
    }


def _plant_cluster(host: SimulatedHost) -> None:
    """Plant the files and role a real ``initdb`` writes (postgres-owned, 0600).

    This is the simulation-limit bridge: a raw ``Shell`` action mutates no file
    store, so ``PgSimulatedHost`` calls this the moment ``initdb`` succeeds. It is
    idempotent -- the caller guards on ``PG_VERSION`` already being present.
    """
    _regular_file(host, PG_VERSION_PATH, b"13\n", 600, user="postgres", group="postgres")
    _regular_file(
        host, CONF_PATH, STOCK_CONF.encode(), 600, user="postgres", group="postgres"
    )
    _regular_file(
        host, HBA_PATH, STOCK_HBA.encode(), 600, user="postgres", group="postgres"
    )
    host.directory_stats.setdefault(
        DATA_DIR, {"mode": 700, "user": "postgres", "group": "postgres"}
    )
    host.existing_files.add(DATA_DIR)
    host.postgres_roles.setdefault("postgres", dict(BOOTSTRAP_ROLE))


class PgSimulatedHost(SimulatedHost):
    """``SimulatedHost`` with an in-stream ``initdb`` side-effect bridge.

    The action's *answer* still comes from ``shell_results`` through ``super()``;
    this only adds what a real ``initdb`` leaves behind (the cluster payload and
    the ``stat`` gate flipping to exit 0), which no typed command seam models. See
    the module docstring.
    """

    async def _run_shell(self, shell: str) -> Any:
        result = await super()._run_shell(shell)
        if (
            "/initdb " in shell
            and result.exit_status == 0
            and PG_VERSION_PATH not in self.remote_files
        ):
            _plant_cluster(self)
            self.shell_results[STAT_SHELL]["exit_status"] = 0
        return result


def seed(host: SimulatedHost, *, initialized: bool = False) -> None:
    """Pre-state: a stock CentOS Stream 9 machine, fresh or already initialized.

    ``initialized`` seeds a cluster that already ran initdb (PG_VERSION present,
    stock conf/hba, the bootstrap postgres role, the stat probe answers exit 0);
    the default fresh host has none of it and the stat probe answers exit 1.
    """
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.directory_stats = {}
    host.packages = {}
    host.services = {}
    host.enabled_services = set()
    host.os_users = {}
    host.linux_distribution = dict(CENTOS_DISTRO)
    host.fips_enabled = False
    host.postgres_roles = {}
    host.postgres_databases = {}
    host.postgres_privileges = {}
    host.ran_shells = []
    host.shell_results = {
        STAT_SHELL: {
            "exit_status": 0 if initialized else 1,
            "stdout": "",
            "stderr": "",
        },
        INITDB_SHELL: {"exit_status": 0, "stdout": "", "stderr": ""},
    }
    if initialized:
        _plant_cluster(host)


def make_hosts(
    inventory: list[dict[str, Any]],
    *,
    initialized: bool = False,
) -> dict[str, PgSimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded target.

    Only the RedHat entry is seeded, so the Ubuntu host keeps its stock empty
    stores and an accidental request on it is caught by the untouched-store
    assertions.
    """
    hosts: dict[str, PgSimulatedHost] = {}
    for entry in inventory:
        host = PgSimulatedHost.from_inventory(entry)
        if entry.get("family") == "RedHat":
            seed(host, initialized=initialized)
        hosts[host.name] = host
    return hosts


def _factory(hosts: dict[str, PgSimulatedHost]) -> Any:
    def factory(entry: dict[str, Any]) -> PgSimulatedHost:
        # The *same* host object every pass, so state survives the barriers.
        return hosts[entry["host"]]

    return factory


async def run_pass_a(
    inventory: list[dict[str, Any]],
    hosts: dict[str, PgSimulatedHost],
    *,
    dry: bool = False,
) -> Any:
    """Run :func:`postgresql` (the system half) on the ordinary inventory view."""
    v = resolve_vars(PostgresqlVars, "postgresql_", inventory)
    report, _hosts = await run_waves(
        inventory, postgresql(v), host_factory=_factory(hosts), dry=dry
    )
    return report


async def run_pass_b(
    inventory: list[dict[str, Any]],
    hosts: dict[str, PgSimulatedHost],
    *,
    dry: bool = False,
) -> Any:
    """Run :func:`postgresql_data` (the data half) on the postgres-privilege view."""
    v = resolve_vars(PostgresqlVars, "postgresql_", inventory)
    pg_inventory = [
        {
            **entry,
            "privilege": {
                **entry.get("privilege", {}),
                "sudo_user": v[entry["host"]].user,
            },
        }
        for entry in inventory
    ]
    report, _hosts = await run_waves(
        pg_inventory, postgresql_data(v), host_factory=_factory(hosts), dry=dry
    )
    return report


async def run_role(
    inventory: list[dict[str, Any]],
    hosts: dict[str, PgSimulatedHost],
    *,
    dry: bool = False,
) -> tuple[Any, Any]:
    """Both passes in order: pass A (system) must run before pass B (data)."""
    report_a = await run_pass_a(inventory, hosts, dry=dry)
    report_b = await run_pass_b(inventory, hosts, dry=dry)
    return report_a, report_b


def changed_entries(report: Any) -> list[Any]:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def entry_named(report: Any, name: str, host: str = "web1") -> Any:
    """The one log entry with *name* on *host*."""
    return next(
        entry
        for entry in report.merged().log
        if entry.name == name and entry.host == host
    )


def entries_named(report: Any, name: str, host: str = "web1") -> list[Any]:
    """Every log entry with *name* on *host*."""
    return [
        entry
        for entry in report.merged().log
        if entry.name == name and entry.host == host
    ]


def host_records(report: Any, host: str) -> list[Any]:
    """Every record (request step or barrier) attributed to *host*."""
    return [record for record in report.records if record.host == host]


def barriers(report: Any, host: str = "web1") -> list[Any]:
    """The barrier objects *host* parked on, in wave order."""
    return [record.barrier for record in host_records(report, host) if record.barrier]


def request_kwargs(report: Any, name: str) -> dict:
    """The forwarded keyword arguments of the one planned request called *name*."""
    record = next(
        record
        for record in report.records
        if record.planned is not None and record.planned.request.name == name
    )
    return record.planned.request.kwargs


# --- the first pass really deploys PostgreSQL ---------------------------------


async def test_first_pass_deploys_everything():
    inventory = make_inventory(**data_overrides())
    hosts = make_hosts(inventory)
    report_a, report_b = await run_role(inventory, hosts)
    host = hosts["web1"]

    # Packages (the dnf seam), profile, directories.
    for package in ("postgresql", "postgresql-server", "postgresql-contrib"):
        assert package in host.packages
    assert "python3-psycopg2" in host.packages
    assert host.remote_files[PROFILE_PATH] == (
        b"export PGDATA=/var/lib/pgsql/data\nexport PATH=$PATH:/usr/bin\n"
    )
    assert host.file_stats[PROFILE_PATH]["mode"] == 644
    assert host.directory_stats[DATA_DIR] == {
        "mode": 700,
        "user": "postgres",
        "group": "postgres",
    }
    assert host.directory_stats[LOG_DIR]["mode"] == 700
    assert host.directory_stats[SOCKET_DIR] == {
        "mode": 755,
        "user": "postgres",
        "group": "postgres",
    }

    # initdb ran exactly once, as postgres; the bridge planted the cluster.
    assert host.ran_shells.count(INITDB_SHELL) == 1
    assert STAT_SHELL in host.ran_shells
    assert host.remote_files[PG_VERSION_PATH] == b"13\n"
    assert "postgres" in host.postgres_roles

    # The conf transforms replaced the commented stock lines (last match).
    conf = host.remote_files[CONF_PATH].decode()
    assert "unix_socket_directories = '/var/run/postgresql'\n" in conf
    assert "log_directory = 'log'\n" in conf
    assert "#unix_socket_directories" not in conf
    assert "#log_directory" not in conf
    assert "#listen_addresses = 'localhost'\n" in conf  # untouched
    assert host.file_stats[CONF_PATH]["mode"] == 644

    # pg_hba rendered byte-exactly (the template's extra/trailing spaces).
    v = resolve_vars(PostgresqlVars, "postgresql_", inventory)
    plan = resolve_plan(v["web1"], MAJOR, False)
    hba = host.remote_files[HBA_PATH].decode()
    assert hba == plan.hba_content
    assert "local all postgres    peer \n" in hba
    assert host.file_stats[HBA_PATH] == {
        "user": "postgres",
        "group": "postgres",
        "mode": 600,
        "size": len(host.remote_files[HBA_PATH]),
    }

    # Service started and enabled, and the handler restarted (notifying changed).
    assert host.services[UNIT] == "active"
    assert UNIT in host.enabled_services
    assert entry_named(report_a, "Ensure PostgreSQL packages are installed.").changed
    assert entry_named(report_a, "Set PostgreSQL environment variables.").changed
    assert entry_named(report_a, "Configure host based authentication.").changed
    assert entry_named(report_a, HANDLER_NAME).changed

    # The data plane (pass B) created the user, database and grants.
    jdoe = host.postgres_roles["jdoe"]
    assert jdoe["createdb"] is True
    assert jdoe["super"] is False
    assert jdoe["canlogin"] is True
    db = host.postgres_databases["exampledb"]
    assert db["owner"] == "postgres"
    assert db["encoding"] == "UTF8"
    assert db["collate"] == "en_US.UTF-8"
    grants = host.postgres_privileges["exampledb"]
    assert {(e["name"], e["privilege"]) for e in grants} == {
        ("accounts", "SELECT"),
        ("accounts", "INSERT"),
    }
    assert all(e["grantee"] == "jdoe" and e["schema"] == "public" for e in grants)
    assert entry_named(report_b, "Ensure PostgreSQL users are present.").changed
    assert entry_named(report_b, "Ensure PostgreSQL databases are present.").changed


# --- the two-pass architecture ------------------------------------------------


async def test_the_role_is_two_passes_on_two_views():
    """Pass A parks on six barriers (fresh), pass B on the single catalog gate.

    The barrier sequence is the proof of the split: the distribution and FIPS
    reads, the two checkpoints, and the stat/initdb actions all live in pass A on
    the root view; pass B's only barrier is the ``PostgresqlRoles`` catalog read,
    which is a reachability gate that must run *after* pass A has the service
    listening.
    """
    inventory = make_inventory(**data_overrides())
    hosts = make_hosts(inventory)
    report_a, report_b = await run_role(inventory, hosts)

    assert [type(b).__name__ for b in barriers(report_a)] == [
        "FactRequest",  # LinuxDistribution
        "FactRequest",  # FipsEnabled
        "CheckpointRequest",  # initialize done
        "ActionRequest",  # stat PG_VERSION
        "ActionRequest",  # initdb (fresh only)
        "CheckpointRequest",  # configure done
    ]
    assert [type(b).__name__ for b in barriers(report_b)] == ["FactRequest"]
    # The catalog gate is pass B's first barrier, and it ran on the postgres view.
    catalog = barriers(report_b)[0]
    assert catalog.name == CATALOG_BARRIER


async def test_the_handler_runs_in_pass_a_before_the_data_plane():
    """The notification union is ``initialize | configure``, both in pass A.

    So the handler fires in pass A -- before pass B touches users and databases --
    where the two-pass example ran it last. No data request notifies, so the
    reordering is safe, and the handler line appears in the pass-A log only.
    """
    inventory = make_inventory(**data_overrides())
    hosts = make_hosts(inventory)
    report_a, report_b = await run_role(inventory, hosts)

    assert entries_named(report_a, HANDLER_NAME)
    assert not entries_named(report_b, HANDLER_NAME)
    assert "initialize done → 1 host notified" in report_a.tree()
    assert "configure done → 1 host notified" in report_a.tree()


async def test_non_redhat_host_emits_nothing_in_either_pass():
    """The family guard is the body's opening ``if``, not a ``when`` on each op."""
    inventory = make_inventory(**data_overrides())
    hosts = make_hosts(inventory)
    report_a, report_b = await run_role(inventory, hosts)

    skipped = hosts["ubuntu"]
    assert host_records(report_a, "ubuntu") == []
    assert host_records(report_b, "ubuntu") == []
    assert [e for e in report_a.merged().log if e.host == "ubuntu"] == []
    assert [e for e in report_b.merged().log if e.host == "ubuntu"] == []
    assert skipped.remote_files == {}
    assert skipped.packages == {}
    assert skipped.postgres_roles == {}
    assert skipped.ran_shells == []


# --- convergence --------------------------------------------------------------


async def test_second_pass_changes_nothing():
    inventory = make_inventory(**data_overrides())
    hosts = make_hosts(inventory)
    first_a, first_b = await run_role(inventory, hosts)
    assert changed_entries(first_a), "pass A must actually deploy something"
    assert changed_entries(first_b), "pass B must actually deploy something"
    host = hosts["web1"]

    second_a, second_b = await run_role(inventory, hosts)
    assert changed_entries(second_a) == []
    assert changed_entries(second_b) == []
    # initdb ran once total; the stat probe ran once per pass-A run.
    assert host.ran_shells.count(INITDB_SHELL) == 1
    assert host.ran_shells.count(STAT_SHELL) == 2
    # The converged pass A has no initdb barrier and no handler record.
    assert [type(b).__name__ for b in barriers(second_a)] == [
        "FactRequest",
        "FactRequest",
        "CheckpointRequest",
        "ActionRequest",  # stat only
        "CheckpointRequest",
    ]
    assert not entries_named(second_a, HANDLER_NAME)
    assert "configure done → 0 hosts notified" in second_a.tree()


async def test_preinitialized_cluster_skips_initdb():
    inventory = make_inventory(**data_overrides())
    hosts = make_hosts(inventory, initialized=True)
    report_a, _ = await run_role(inventory, hosts)
    host = hosts["web1"]

    assert INITDB_SHELL not in host.ran_shells
    assert STAT_SHELL in host.ran_shells
    # The configure stage still reconciles the stock conf/hba.
    assert entry_named(report_a, "Configure host based authentication.").changed
    assert "unix_socket_directories = '/var/run/postgresql'\n" in (
        host.remote_files[CONF_PATH].decode()
    )

    second_a, second_b = await run_role(inventory, hosts)
    assert changed_entries(second_a) == []
    assert changed_entries(second_b) == []
    assert INITDB_SHELL not in host.ran_shells


async def test_dry_preview_mutates_nothing():
    """``dry`` is a one-wave preview: pass A parks on its very first barrier.

    The role's first item is the ``LinuxDistribution`` fact, so the dry pass plans
    no request, gathers nothing and applies nothing; pass B parks on the catalog
    gate. The stat gate does not even run -- a dry pass never runs a barrier.
    """
    inventory = make_inventory(**data_overrides())
    hosts = make_hosts(inventory)
    host = hosts["web1"]

    report_a, report_b = await run_role(inventory, hosts, dry=True)

    assert report_a.merged().log == []
    assert report_b.merged().log == []
    assert host.ran_shells == []
    assert host.remote_files == {}
    assert host.packages == {}
    assert host.postgres_roles == {}
    assert "unknown until run" in report_a.tree()


# --- the FIPS auth-method ternary (#30) ---------------------------------------


async def test_fips_enabled_selects_scram():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["web1"].fips_enabled = True
    await run_role(inventory, hosts)

    assert "scram-sha-256" in hosts["web1"].remote_files[HBA_PATH].decode()


# --- fail-fast gates ----------------------------------------------------------


async def test_unsupported_major_fails_fast():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["web1"].linux_distribution = {**CENTOS_DISTRO, "major": 6}
    with pytest.raises(ValueError, match="no RedHat vars pinned"):
        await run_pass_a(inventory, hosts)
    assert hosts["web1"].ran_shells == []


async def test_fedora_is_rejected():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["web1"].linux_distribution = {**CENTOS_DISTRO, "name": "Fedora"}
    with pytest.raises(ValueError, match="excludes Fedora and Amazon"):
        await run_pass_a(inventory, hosts)


# --- #45: partial role_attr_flags reconciliation ------------------------------


async def test_unlisted_flags_survive_reconcile():
    """An existing SUPERUSER jdoe; the item lists only CREATEDB.

    ``postgresql.role``'s tri-state options reconcile ONLY the listed flag (#45),
    so the superuser bit survives with no frozen-catalog fill.
    """
    superuser_jdoe = {
        **BOOTSTRAP_ROLE,
        "super": True,
        "createdb": False,
        "createrole": False,
        "replication": False,
        "bypassrls": False,
        "oid": 16384,
    }
    inventory = make_inventory(
        postgresql_users=[{"name": "jdoe", "role_attr_flags": "CREATEDB"}]
    )
    hosts = make_hosts(inventory, initialized=True)
    hosts["web1"].postgres_roles["jdoe"] = dict(superuser_jdoe)
    _, report_b = await run_role(inventory, hosts)
    jdoe = hosts["web1"].postgres_roles["jdoe"]

    assert jdoe["super"] is True  # the unlisted flag survived
    assert jdoe["createdb"] is True  # the listed flag applied
    assert jdoe["bypassrls"] is False  # unlisted bypassrls left alone (#31)
    # The create pass is presence-only on the existing role: a no-op.
    assert not entry_named(report_b, "Ensure PostgreSQL users are present.").changed
    assert entry_named(report_b, "Ensure PostgreSQL users are configured correctly.").changed

    _, second_b = await run_role(inventory, hosts)
    assert changed_entries(second_b) == []
    assert hosts["web1"].postgres_roles["jdoe"]["super"] is True


async def test_absent_user_is_dropped_and_converges():
    inventory = make_inventory(postgresql_users=[{"name": "jdoe", "state": "absent"}])
    hosts = make_hosts(inventory, initialized=True)
    hosts["web1"].postgres_roles["jdoe"] = {**BOOTSTRAP_ROLE, "oid": 16384}
    _, report_b = await run_role(inventory, hosts)

    assert "jdoe" not in hosts["web1"].postgres_roles
    assert entry_named(report_b, "Ensure PostgreSQL users are configured correctly.").changed
    # Not created first (the deviation from the role's create-then-drop loop).
    assert entries_named(report_b, "Ensure PostgreSQL users are present.") == []

    _, second_b = await run_role(inventory, hosts)
    assert changed_entries(second_b) == []


# --- privileges: ALL_IN_SCHEMA and the empty-schema boundary ------------------


async def test_all_in_schema_grant_converges():
    prior = {
        "object_type": "table",
        "schema": "public",
        "name": "accounts",
        "grantee": "jdoe",
        "privilege": "SELECT",
        "grantable": False,
    }
    inventory = make_inventory(
        postgresql_users=[{"name": "jdoe"}],
        postgresql_privs=[
            {
                "db": "exampledb",
                "roles": "jdoe",
                "privs": "UPDATE",
                "type": "table",
                "objs": "ALL_IN_SCHEMA",
            }
        ],
    )
    hosts = make_hosts(inventory, initialized=True)
    hosts["web1"].postgres_privileges["exampledb"] = [dict(prior)]
    _, report_b = await run_role(inventory, hosts)

    grants = hosts["web1"].postgres_privileges["exampledb"]
    assert ("accounts", "UPDATE") in {(e["name"], e["privilege"]) for e in grants}
    assert entry_named(
        report_b, "Ensure PostgreSQL users privileges are configured correctly."
    ).changed

    _, second_b = await run_role(inventory, hosts)
    assert changed_entries(second_b) == []


async def test_all_in_schema_empty_schema_is_noop():
    inventory = make_inventory(
        postgresql_users=[{"name": "jdoe"}],
        postgresql_privs=[
            {
                "db": "exampledb",
                "roles": "jdoe",
                "privs": "ALL",
                "type": "table",
                "objs": "ALL_IN_SCHEMA",
            }
        ],
    )
    hosts = make_hosts(inventory, initialized=True)
    _, report_b = await run_role(inventory, hosts)

    entry = entry_named(
        report_b, "Ensure PostgreSQL users privileges are configured correctly."
    )
    assert not entry.changed
    assert "no tables in schema public to manage" in entry.reason
    assert hosts["web1"].postgres_privileges.get("exampledb", []) == []


# --- the variables ------------------------------------------------------------


async def test_packages_variable_reaches_the_install_request():
    inventory = make_inventory(postgresql_packages=["postgresql", "postgresql-libs"])
    hosts = make_hosts(inventory)
    report_a = await run_pass_a(inventory, hosts)

    assert request_kwargs(report_a, SETUP_NAMES[0])["packages"] == (
        "postgresql",
        "postgresql-libs",
    )


async def test_service_state_and_enabled_reach_the_service_task():
    inventory = make_inventory(
        postgresql_service_state="stopped", postgresql_service_enabled=False
    )
    hosts = make_hosts(inventory)
    report_a = await run_pass_a(inventory, hosts)

    kwargs = request_kwargs(
        report_a, "Ensure PostgreSQL is started and enabled on boot."
    )
    assert kwargs["running"] is False
    assert kwargs["enabled"] is False
    assert kwargs["service"] == "postgresql"


# --- the pure half, unit-tested directly --------------------------------------


def test_conf_transform_is_a_fixpoint():
    transform = make_conf_transform("log_directory", "log")
    once = transform(STOCK_CONF)
    assert transform(once) == once
    assert "log_directory = 'log'\n" in once
    absent = make_conf_transform("log_directory", "log", present=False)
    assert "log_directory" not in absent(once)
    assert absent(absent(once)) == absent(once)
    assert absent("#listen_addresses = 'localhost'\n") == "#listen_addresses = 'localhost'\n"
    assert transform(None) == "log_directory = 'log'\n"


def test_conf_transform_replaces_last_match():
    text = "log_directory = 'old'\n#log_directory = 'commented'\n"
    out = make_conf_transform("log_directory", "log")(text)
    assert out == "log_directory = 'old'\nlog_directory = 'log'\n"


def test_parse_role_flags():
    assert parse_role_flags("CREATEDB, NOLOGIN") == (
        ("createdb", True),
        ("login", False),
    )
    assert parse_role_flags(None) is None
    assert parse_role_flags("") == ()
    assert parse_role_flags(["BYPASSRLS"]) == (("bypassrls", True),)
    with pytest.raises(ValueError, match="contradictory"):
        parse_role_flags("LOGIN,NOLOGIN")
    with pytest.raises(ValueError, match="unknown role_attr_flags"):
        parse_role_flags("WAT")
    with pytest.raises(TypeError):
        parse_role_flags(42)


def test_resolve_plan_defaults_and_fips():
    v = resolve_vars(PostgresqlVars, "postgresql_", make_inventory())["web1"]
    plan = resolve_plan(v, MAJOR, False)
    # auth_method is folded into the rendered hba content, not a plan field.
    assert plan.version == REDHAT_VARS[MAJOR]["version"]
    assert plan.enable_repos is None
    assert plan.log_dir == LOG_DIR
    assert plan.packages == ("postgresql", "postgresql-server", "postgresql-contrib")
    assert "md5" in (plan.hba_content or "")
    fips = resolve_plan(v, MAJOR, True)
    assert "scram-sha-256" in (fips.hba_content or "")
    el10 = resolve_plan(v, 10, False)
    assert el10.version == REDHAT_VARS[10]["version"]


def test_resolve_plan_enablerepo_and_absolute_log_dir():
    v = resolve_vars(
        PostgresqlVars,
        "postgresql_",
        make_inventory(
            postgresql_enablerepo="crb, extras",
            postgresql_global_config_options=[
                {"option": "log_directory", "value": "/var/log/pgsql"}
            ],
        ),
    )["web1"]
    plan = resolve_plan(v, MAJOR, False)
    assert plan.enable_repos == ("crb", "extras")
    assert plan.log_dir == "/var/log/pgsql"


def test_normalize_privs_exotic_type_cites_29():
    v = resolve_vars(
        PostgresqlVars,
        "postgresql_",
        make_inventory(
            postgresql_privs=[
                {"db": "exampledb", "roles": "jdoe", "privs": "ALL", "type": "language"}
            ]
        ),
    )["web1"]
    with pytest.raises(ValueError, match="#29"):
        normalize_privs(v)


def test_normalize_privs_mappings():
    v = resolve_vars(
        PostgresqlVars,
        "postgresql_",
        make_inventory(
            postgresql_privs=[
                {
                    "db": "exampledb",
                    "roles": "jdoe",
                    "privs": "ALL",
                    "type": "function",
                    "objs": "f(int:text)",
                },
                {"db": "exampledb", "roles": "jdoe", "privs": "CONNECT", "type": "database"},
            ]
        ),
    )["web1"]
    func, db = normalize_privs(v)
    assert func["objects"] == ("public.f(int,text)",)
    assert db["objects"] == ("exampledb",)


def test_render_pg_hba_byte_exact():
    entries = [
        {"type": "local", "database": "all", "user": "postgres", "auth_method": "peer"}
    ]
    out = render_pg_hba(entries)
    assert out.startswith("# Ansible managed\n")
    assert "local all postgres    peer \n" in out


def test_resolve_data_normalizes_users_and_databases():
    v = resolve_vars(
        PostgresqlVars, "postgresql_", make_inventory(**data_overrides())
    )["web1"]
    data = resolve_data(v)
    assert data.pg_user == "postgres"
    assert data.socket_dirs == ("/var/run/postgresql",)
    (user,) = data.users
    assert user["name"] == "jdoe"
    assert user["flags"] == (("createdb", True),)
    (database,) = data.databases
    assert database["encoding"] == "UTF8"
    assert database["template"] == "template0"


def test_duplicate_user_fails_fast():
    v = resolve_vars(
        PostgresqlVars,
        "postgresql_",
        make_inventory(postgresql_users=[{"name": "jdoe"}, {"name": "jdoe"}]),
    )["web1"]
    with pytest.raises(ValueError, match="duplicate postgresql_users name"):
        resolve_data(v)


# --- the variables model ------------------------------------------------------


def test_vars_are_frozen_and_reject_unknown_fields():
    v = PostgresqlVars()
    with pytest.raises(ValidationError):
        v.user = "pg"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        # The prefixed inventory spelling is not a field name.
        PostgresqlVars(postgresql_user="pg")


def test_socket_dirs_accepts_a_bare_string_and_requires_absolute():
    assert PostgresqlVars(unix_socket_directories="/var/run/pg").unix_socket_directories == (
        "/var/run/pg",
    )
    with pytest.raises(ValueError, match="absolute path"):
        PostgresqlVars(unix_socket_directories=["relative/dir"])
    with pytest.raises(ValueError, match="non-empty"):
        PostgresqlVars(unix_socket_directories=[])


def test_vars_reject_bad_enum_values():
    with pytest.raises(ValidationError, match="restarted_state"):
        PostgresqlVars(restarted_state="rebooted")
    with pytest.raises(ValidationError, match="service_state"):
        PostgresqlVars(service_state="restarted")
    with pytest.raises(ValidationError, match="auth_method"):
        PostgresqlVars(auth_method="password")


def test_inventory_binds_the_prefixed_variables():
    v = resolve_vars(
        PostgresqlVars, "postgresql_", make_inventory(postgresql_user="pg")
    )
    assert v["web1"].user == "pg"
    # The other role's host keeps the defaults: no postgresql_ keys, nothing bound.
    assert v["ubuntu"] == PostgresqlVars()


def test_prefixed_typo_is_reported_not_ignored():
    inventory = make_inventory(postgresql_usr="pg")
    with pytest.raises(RoleVarsError, match="postgresql_usr"):
        resolve_vars(PostgresqlVars, "postgresql_", inventory)


# --- the wave tree (design doc section 7.2) -----------------------------------

# Pinned from a real simulated run (``pytest tests/roles/test_postgresql.py -k
# snapshot`` prints the diff against them). The install spool
# directories are content-addressed, so the hashes are stable. Pass A parks on
# six barriers (two facts, two checkpoints, the stat gate and initdb); pass B on
# the one catalog fact. The converged pass A drops the initdb wave and the
# handler line, which is the preview ``dry`` deliberately refuses to fake.

PASS_A_FIRST_TREE = """(root)
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
        $ sudo systemctl start postgresql.service"""

PASS_A_CONVERGED_TREE = """(root)
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
    web1: Ensure PostgreSQL is started and enabled on boot. [no change: postgresql.service already running; postgresql.service already enabled]"""

PASS_B_FIRST_TREE = """(root)
  wave 0
    barrier: read pg_roles catalog → {'postgres': {'super': True, 'inherit': True, 'createrole': True, 'createdb': True, 'canlogin': True, 'replication': True, 'connlimit': -1, 'password': None, 'validuntil': None, 'bypassrls': True, 'config': None, 'oid': 10}}
  wave 1
    web1: Ensure PostgreSQL users are present. [changed: postgresql role jdoe will be created]
        $ sudo psql -U postgres -h /var/run/postgresql -Ac 'CREATE ROLE "jdoe" LOGIN NOSUPERUSER INHERIT NOCREATEDB NOCREATEROLE NOREPLICATION'
    web1: Ensure PostgreSQL databases are present. [changed: postgresql database exampledb will be created]
        $ sudo psql -U postgres -h /var/run/postgresql -Ac 'CREATE DATABASE "exampledb" OWNER "postgres" TEMPLATE "template0" ENCODING '"'"'UTF8'"'"' LC_COLLATE '"'"'en_US.UTF-8'"'"' LC_CTYPE '"'"'en_US.UTF-8'"'"''
    web1: Ensure PostgreSQL users are configured correctly. [changed: postgresql role jdoe will be altered (createdb)]
        $ sudo psql -U postgres -h /var/run/postgresql -Ac 'ALTER ROLE "jdoe" CREATEDB'
    web1: Ensure PostgreSQL users privileges are configured correctly. [changed: postgresql will grant INSERT, SELECT to jdoe]
        $ sudo psql -d exampledb -U postgres -h /var/run/postgresql -Ac 'GRANT INSERT, SELECT ON TABLE "public"."accounts" TO "jdoe"'"""

PASS_B_CONVERGED_TREE = """(root)
  wave 0
    barrier: read pg_roles catalog → {'postgres': {'super': True, 'inherit': True, 'createrole': True, 'createdb': True, 'canlogin': True, 'replication': True, 'connlimit': -1, 'password': None, 'validuntil': None, 'bypassrls': True, 'config': None, 'oid': 10}, 'jdoe': {'super': False, 'inherit': True, 'createrole': False, 'createdb': True, 'canlogin': True, 'replication': False, 'connlimit': -1, 'password': None, 'validuntil': None, 'bypassrls': False, 'config': None, 'oid': 11}}
  wave 1
    web1: Ensure PostgreSQL users are present. [no change: postgresql role jdoe exists and does not need updates]
    web1: Ensure PostgreSQL databases are present. [no change: postgresql database exampledb already exists with the same parameters; TEMPLATE cannot be changed on an existing database]
    web1: Ensure PostgreSQL users are configured correctly. [no change: postgresql role jdoe exists and does not need updates]
    web1: Ensure PostgreSQL users privileges are configured correctly. [no change: postgresql role jdoe already has the privileges]"""


async def test_tree_snapshot():
    """Both passes as four trees: pass A first and converged, pass B first and
    converged.

    The converged pass A is one wave shorter (no initdb) and queues no handler;
    pass B still reads the catalog (a read, not a cached value) and reconciles to
    no change. The Ubuntu host appears in no tree of either pass.
    """
    inventory = make_inventory(**data_overrides())
    hosts = make_hosts(inventory)

    first_a, first_b = await run_role(inventory, hosts)
    assert first_a.tree() == PASS_A_FIRST_TREE
    assert first_b.tree() == PASS_B_FIRST_TREE

    second_a, second_b = await run_role(inventory, hosts)
    assert second_a.tree() == PASS_A_CONVERGED_TREE
    assert second_b.tree() == PASS_B_CONVERGED_TREE
