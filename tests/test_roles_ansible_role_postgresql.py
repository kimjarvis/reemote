# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end test for the PostgreSQL Ansible-role migration example.

``examples/roles/ansible-role-postgresql/ansible-role-postgresql.py`` connects
for real when run as a script, so the suite never executes its ``main()``.
What it *can* do offline is replay the same orchestration against
``SimulatedHost``: the example is imported as a module, its factories are fed
the frozen plans gathered from simulated state, and every deploy goes through
the real driver (prepare + apply, plus a ``dry=True`` preview each).

The load-bearing assertion is the migration's idempotence claim: run the
gather -> plan -> deploy sequence, then run it *again* against the same
persistent hosts, and the second pass must report ``changed=False``
everywhere with an empty handler set -- the packages are present, ``PG_VERSION``
exists (the initdb action is skipped), the conf transforms are fixpoints, the
rendered templates match, the service is running/enabled, the role attributes
and grants match.

Simulation limits bridged explicitly (each is a *simulation* limit, not a
library bug):

* ``initdb`` is a ``Shell`` action answered from ``shell_results``; the
  simulation runs no real binary, so the cluster payload it writes
  (``PG_VERSION``, a stock ``postgresql.conf``/``pg_hba.conf``, the bootstrap
  ``postgres`` superuser role) is planted by ``bridge_initdb`` between the
  initialize deploy and the configure deploy -- what a real ``initdb`` does.
  The stat gate's ``shell_results`` answer is flipped to exit 0 at the same
  time, so the second pass's elevated ``test -e`` probe sees the cluster.
* the ``become_user: postgres`` inventory view: ``main()`` builds a second
  ``Host`` whose ``privilege.sudo_user`` is ``postgres``. The replay keeps ONE
  persistent host object (state must survive the barriers) and temporarily
  swaps ``host.privilege`` for the initdb action -- the only barrier where the
  privilege view changes a simulated observable (the action's rendered shell
  key, ``sudo -u postgres ...``). The catalog gather and the data deploy read
  typed seams that ignore the run-as user, so they ride the same object.
* ``files.install``'s ``install_file`` seam records a root:root owner when the
  request names none (the elevated ``cp`` model); a real ``cp`` over an existing
  file preserves its owner. The conf transform's convergence does not depend on
  ownership, so the tests assert content/mode, not the conf's owner.
* an ``ALL IN SCHEMA`` grant addresses the objects the grantee already holds
  grants on (the simulation has no object catalog to enumerate); the
  ``ALL_IN_SCHEMA`` test seeds a prior grant row so the target set is non-empty.
"""

import importlib.util
import posixpath
import shlex
from pathlib import Path
from typing import Any

import pytest

from reemote import (
    FipsEnabled,
    LinuxDistribution,
    Shell,
    changed_hosts,
    gather_action,
    gather_fact,
    run,
)
from tests.simulated_host import SimulatedHost

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = (
    REPO_ROOT
    / "examples"
    / "roles"
    / "ansible-role-postgresql"
    / "ansible-role-postgresql.py"
)


def load_example() -> Any:
    """Import the migration example by path (its name is not a valid identifier)."""
    spec = importlib.util.spec_from_file_location(
        "ansible_role_postgresql_example", EXAMPLE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pg = load_example()

MAJOR = 9
DATA_DIR = "/var/lib/pgsql/data"
CONF_PATH = DATA_DIR + "/postgresql.conf"
HBA_PATH = DATA_DIR + "/pg_hba.conf"
PG_VERSION_PATH = DATA_DIR + "/PG_VERSION"
PROFILE_PATH = "/etc/profile.d/postgres.sh"
LOG_DIR = DATA_DIR + "/log"
SOCKET_DIR = "/var/run/postgresql"
UNIT = "postgresql.service"

# The exact shells the action transport seam (#16) renders: the stat gate is
# elevated root (bare sudo), initdb rides the postgres privilege view.
STAT_SHELL = f"sudo test -e {PG_VERSION_PATH}"
INITDB_SHELL = f"sudo -u postgres /usr/bin/initdb -D {DATA_DIR}"

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


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one CentOS target, one Ubuntu host to be skipped.

    Role variables ride as extra keys (reemote's ``host_vars``). With no
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


def _cluster_files(host: SimulatedHost) -> None:
    """Plant the files a real initdb writes (postgres-owned, 0600)."""
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


def seed(
    host: SimulatedHost,
    *,
    initialized: bool = False,
    extra_roles: dict[str, Any] | None = None,
) -> None:
    """Pre-state: a stock CentOS Stream 9 machine, fresh or initialized.

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
    host.postgres_roles = dict(extra_roles or {})
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
        _cluster_files(host)
        host.postgres_roles.setdefault("postgres", dict(BOOTSTRAP_ROLE))


def make_hosts(
    inventory: list[dict[str, Any]],
    *,
    initialized: bool = False,
    extra_roles: dict[str, Any] | None = None,
) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if pg.is_redhat(host):
            seed(host, initialized=initialized, extra_roles=extra_roles)
        hosts[host.name] = host
    return hosts


def bridge_initdb(hosts: dict[str, SimulatedHost], plans: dict[str, Any]) -> None:
    """Seed the cluster payload a real initdb writes (simulation limit).

    The initdb ``Shell`` action only records a ran_shells entry; the files and
    the bootstrap role it creates must be planted by hand between the initialize
    deploy and the configure deploy, and the stat gate's answer flipped to
    exit 0 so the second pass sees an initialized cluster.
    """
    for name, plan in plans.items():
        host = hosts[name]
        if plan["initialized"] or INITDB_SHELL not in host.ran_shells:
            continue
        _cluster_files(host)
        host.postgres_roles.setdefault("postgres", dict(BOOTSTRAP_ROLE))
        host.shell_results[STAT_SHELL]["exit_status"] = 0


async def migrate(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
    *,
    dry: bool = True,
    apply: bool = True,
) -> dict[str, Any]:
    """Replay the example's whole orchestration once, against persistent hosts.

    Mirrors ``main()`` barrier for barrier: variable resolution, the
    LinuxDistribution/FipsEnabled gathers, the elevated stat gate, the package
    and initialize deploys, the initdb action (privilege view swapped onto the
    one persistent host), the configure/service deploys, the composed data
    deploy on the postgres inventory view, and the notified handler. The data
    deploy's postgresql operations read the role catalog themselves (#45), so
    there is no separate catalog gather. ``apply=False`` stops after deploy 1's
    dry preview (reports is empty).
    """
    targets = [host for host in hosts.values() if pg.is_redhat(host)]
    settings = {host.name: pg.role_settings(host) for host in targets}

    plans: dict[str, Any] = {}
    try:
        distributions = await gather_fact(targets, LinuxDistribution)
        fips = await gather_fact(targets, FipsEnabled)
        for host in targets:
            info = distributions[host.name]
            name = str(info.get("name") or "")
            if info.get("family") != "RedHat" or name.startswith(("Fedora", "Amazon")):
                raise ValueError(f"{host.name}: distribution {name!r} is not CentOS/RHEL")
            major = info.get("major")
            if not isinstance(major, int):
                raise TypeError(f"{host.name}: no integer major version in {info!r}")
            rv = pg.resolved_vars(settings[host.name], major, bool(fips[host.name]))
            plans[host.name] = {
                **rv,
                "major": major,
                "profile_content": pg.render_profile(rv["data_dir"], rv["bin_path"]),
                "hba_content": pg.render_pg_hba(rv["hba_entries"])
                if rv["hba_entries"]
                else None,
                "conf_path": posixpath.join(rv["config_path"], "postgresql.conf"),
                "hba_path": posixpath.join(rv["config_path"], "pg_hba.conf"),
                "conf_transforms": tuple(
                    pg.make_conf_transform(option, value, present)
                    for option, value, present in rv["options"]
                ),
                "pg_version_path": posixpath.join(rv["data_dir"], "PG_VERSION"),
                "initdb_command": f"{rv['bin_path']}/initdb -D {rv['data_dir']}",
            }
    finally:
        for host in targets:
            await host.aclose()

    # The elevated stat gate (the data dir is 0700 postgres -- FileExists cannot
    # traverse it, so the example rides a Shell action).
    for host in targets:
        plan = plans[host.name]
        result = (
            await gather_action(
                [host],
                Shell,
                command=f"test -e {shlex.quote(plan['pg_version_path'])}",
                sudo=True,
            )
        )[host.name]
        plan["initialized"] = result["exit_status"] == 0

    # The become_user: postgres inventory view (run-as user from inventory).
    target_names = {host.name for host in targets}
    pg_inventory = [
        {
            **entry,
            "privilege": {
                **entry.get("privilege", {}),
                "sudo_user": settings[entry["host"]]["postgresql_user"],
            },
        }
        for entry in inventory
        if entry["host"] in target_names
    ]

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        # The *same* host object every run, so state survives the barriers.
        return hosts[entry["host"]]

    async def stage(inv: list[dict[str, Any]], deploy: Any) -> Any:
        if dry:
            await run(inv, deploy, host_factory=factory, dry=True)
        if not apply:
            return None
        _plans, report, _hosts = await run(inv, deploy, host_factory=factory)
        return report

    setup = pg.build_setup(plans)
    report_setup = await stage(inventory, setup)
    if not apply:
        return {"reports": [], "plans": plans, "notified": frozenset()}
    report_initialize = await stage(inventory, pg.build_initialize(plans))

    # initdb as postgres, only where the frozen stat gate says fresh. The
    # privilege view is swapped onto the persistent host for this action so the
    # rendered shell key carries "sudo -u postgres".
    for host in targets:
        plan = plans[host.name]
        if plan["initialized"]:
            continue
        saved = host.privilege
        host.privilege = {**saved, "sudo_user": plan["pg_user"]}
        try:
            result = (
                await gather_action(
                    [host], Shell, command=plan["initdb_command"], sudo=True
                )
            )[host.name]
        finally:
            host.privilege = saved
        if result["exit_status"] != 0:
            raise ValueError(f"{host.name}: initdb failed (exit {result['exit_status']})")
    bridge_initdb(hosts, plans)

    report_configure = await stage(inventory, pg.build_configure(plans))
    report_service = await stage(inventory, pg.build_service(plans))

    report_data = await stage(
        pg_inventory,
        pg.compose(
            pg.build_users(plans), pg.build_databases(plans), pg.build_users_props(plans)
        ),
    )

    notified = changed_hosts(report_initialize, pg.NOTIFY_RESTART) | changed_hosts(
        report_configure, pg.NOTIFY_RESTART
    )
    report_handler = await stage(inventory, pg.build_handler(plans, notified))

    return {
        "reports": [
            report_setup,
            report_initialize,
            report_configure,
            report_service,
            report_data,
            report_handler,
        ],
        "plans": plans,
        "notified": notified,
    }


def changed_entries(result: dict[str, Any]) -> list[Any]:
    """Every changed log entry across the deploys."""
    return [
        entry for report in result["reports"] for entry in report.log if entry.changed
    ]


def entries_named(result: dict[str, Any], name: str, host: str = "web1") -> list[Any]:
    """Every log entry with *name* on *host* (across the deploys)."""
    return [
        entry
        for report in result["reports"]
        for entry in report.log
        if entry.name == name and entry.host == host
    ]


def entry_named(result: dict[str, Any], name: str, host: str = "web1") -> Any:
    """The one log entry with *name* on *host*."""
    matches = entries_named(result, name, host)
    assert matches, f"no entry named {name!r} on {host}"
    return matches[0]


# --- the first pass really deploys PostgreSQL ---------------------------------


async def test_first_pass_deploys_everything():
    inventory = make_inventory(**data_overrides())
    hosts = make_hosts(inventory)
    result = await migrate(inventory, hosts)
    host = hosts["web1"]

    assert result["plans"]["web1"]["initialized"] is False
    assert result["plans"]["web1"]["auth_method"] == "md5"

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
    hba = host.remote_files[HBA_PATH].decode()
    assert hba == pg.render_pg_hba(result["plans"]["web1"]["hba_entries"])
    assert "local all postgres    peer \n" in hba
    assert host.file_stats[HBA_PATH] == {
        "user": "postgres",
        "group": "postgres",
        "mode": 600,
        "size": len(host.remote_files[HBA_PATH]),
    }

    # Service started and enabled.
    assert host.services[UNIT] == "active"
    assert UNIT in host.enabled_services

    # Users / databases / privileges.
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

    # The notifying requests changed, so the handler restarted web1.
    assert entry_named(result, "Ensure PostgreSQL packages are installed.").changed
    assert entry_named(result, "Set PostgreSQL environment variables.").changed
    assert entry_named(result, "Configure host based authentication.").changed
    assert entry_named(result, "Ensure PostgreSQL is started and enabled on boot.").changed
    assert entry_named(result, "Ensure PostgreSQL users are present.").changed
    assert entry_named(result, "Ensure PostgreSQL databases are present.").changed
    assert entry_named(result, "Ensure PostgreSQL users privileges are configured correctly.").changed
    assert result["notified"] == frozenset({"web1"})
    assert entry_named(result, "Restart postgresql (handler)").changed
    assert changed_entries(result)


async def test_non_redhat_host_is_never_touched():
    inventory = make_inventory(**data_overrides())
    hosts = make_hosts(inventory)
    result = await migrate(inventory, hosts)

    skipped = hosts["ubuntu"]
    assert skipped.remote_files == {}
    assert skipped.packages == {}
    assert skipped.postgres_roles == {}
    assert skipped.ran_shells == []
    for report in result["reports"]:
        for entry in report.log:
            if entry.host == "ubuntu":
                assert entry.skipped
                assert not entry.changed


# --- the second pass is a no-op -----------------------------------------------


async def test_second_pass_changes_nothing():
    inventory = make_inventory(**data_overrides())
    hosts = make_hosts(inventory)
    first = await migrate(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"
    host = hosts["web1"]

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []
    assert second["notified"] == frozenset()
    assert second["plans"]["web1"]["initialized"] is True
    # initdb ran once total; the stat probe ran once per pass.
    assert host.ran_shells.count(INITDB_SHELL) == 1
    assert host.ran_shells.count(STAT_SHELL) == 2
    # The handler deploy yielded nothing for the converged host.
    assert [e for e in second["reports"][5].log if e.host == "web1"] == []


async def test_preinitialized_cluster_skips_initdb():
    inventory = make_inventory(**data_overrides())
    hosts = make_hosts(inventory, initialized=True)
    result = await migrate(inventory, hosts)
    host = hosts["web1"]

    assert result["plans"]["web1"]["initialized"] is True
    assert INITDB_SHELL not in host.ran_shells
    # The configure deploy still reconciles the stock conf/hba.
    assert entry_named(result, "Configure host based authentication.").changed
    assert "unix_socket_directories = '/var/run/postgresql'\n" in (
        host.remote_files[CONF_PATH].decode()
    )
    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []
    assert INITDB_SHELL not in host.ran_shells


async def test_dry_preview_mutates_nothing():
    inventory = make_inventory(**data_overrides())
    hosts = make_hosts(inventory)
    host = hosts["web1"]

    result = await migrate(inventory, hosts, apply=False)
    assert result["reports"] == []
    assert result["plans"]["web1"]["initialized"] is False
    # The stat gate is a read action (it runs); initdb and every deploy do not.
    assert host.ran_shells == [STAT_SHELL]
    assert host.remote_files == {}
    assert host.packages == {}
    assert host.directory_stats == {}
    assert host.postgres_roles == {}


# --- #45: partial role_attr_flags reconciliation (now native) -----------------


async def test_unlisted_flags_survive_reconcile():
    # An existing SUPERUSER jdoe; the item lists only CREATEDB. Ansible adjusts
    # ONLY the listed flag, and postgresql.role's tri-state options now match
    # that natively (#45) -- the superuser bit survives with no frozen-catalog
    # fill.
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
    hosts = make_hosts(
        inventory, initialized=True, extra_roles={"jdoe": superuser_jdoe}
    )
    result = await migrate(inventory, hosts)
    jdoe = hosts["web1"].postgres_roles["jdoe"]

    assert jdoe["super"] is True  # the unlisted flag survived
    assert jdoe["createdb"] is True  # the listed flag applied
    assert jdoe["bypassrls"] is False  # unlisted bypassrls left alone (#31)
    # The create pass is presence-only on the existing role: a no-op.
    assert not entry_named(result, "Ensure PostgreSQL users are present.").changed
    assert entry_named(result, "Ensure PostgreSQL users are configured correctly.").changed

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []
    assert hosts["web1"].postgres_roles["jdoe"]["super"] is True


async def test_absent_user_is_dropped_and_converges():
    inventory = make_inventory(postgresql_users=[{"name": "jdoe", "state": "absent"}])
    hosts = make_hosts(
        inventory,
        initialized=True,
        extra_roles={"jdoe": {**BOOTSTRAP_ROLE, "oid": 16384}},
    )
    result = await migrate(inventory, hosts)
    assert "jdoe" not in hosts["web1"].postgres_roles
    assert entry_named(result, "Ensure PostgreSQL users are configured correctly.").changed
    # Not created first (the deviation from the role's create-then-drop loop).
    assert entries_named(result, "Ensure PostgreSQL users are present.") == []

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


async def test_no_flags_is_presence_only():
    # An existing role with a non-default flag and an item without
    # role_attr_flags: presence-only, nothing reconciled.
    role = {**BOOTSTRAP_ROLE, "super": True, "createdb": True, "oid": 16384}
    inventory = make_inventory(postgresql_users=[{"name": "jdoe"}])
    hosts = make_hosts(inventory, initialized=True, extra_roles={"jdoe": role})
    result = await migrate(inventory, hosts)
    # The create pass is presence-only on the existing role: a no-op; the
    # reconcile pass skips a flagless item entirely.
    assert not entry_named(result, "Ensure PostgreSQL users are present.").changed
    assert entries_named(result, "Ensure PostgreSQL users are configured correctly.") == []
    assert hosts["web1"].postgres_roles["jdoe"]["super"] is True
    assert changed_entries(result) == [] or all(
        e.name not in {"Ensure PostgreSQL users are configured correctly."}
        for e in changed_entries(result)
    )


# --- privileges: ALL_IN_SCHEMA and the empty-schema boundary ------------------


async def test_all_in_schema_grant_converges():
    # A prior SELECT grant on public.accounts gives the ALL_IN_SCHEMA grant a
    # non-empty target set (the simulation enumerates no object catalog).
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
    result = await migrate(inventory, hosts)

    grants = hosts["web1"].postgres_privileges["exampledb"]
    assert ("accounts", "UPDATE") in {(e["name"], e["privilege"]) for e in grants}
    assert entry_named(
        result, "Ensure PostgreSQL users privileges are configured correctly."
    ).changed
    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


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
    result = await migrate(inventory, hosts)
    entry = entry_named(
        result, "Ensure PostgreSQL users privileges are configured correctly."
    )
    assert not entry.changed
    assert "no tables in schema public to manage" in entry.reason
    assert hosts["web1"].postgres_privileges.get("exampledb", []) == []


# --- the FIPS auth-method ternary (#30) ---------------------------------------


async def test_fips_enabled_selects_scram():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["web1"].fips_enabled = True
    result = await migrate(inventory, hosts)
    assert result["plans"]["web1"]["auth_method"] == "scram-sha-256"
    assert "scram-sha-256" in hosts["web1"].remote_files[HBA_PATH].decode()


# --- fail-fast gates ----------------------------------------------------------


async def test_unsupported_major_fails_fast():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["web1"].linux_distribution = {**CENTOS_DISTRO, "major": 6}
    with pytest.raises(ValueError, match="no RedHat vars pinned"):
        await migrate(inventory, hosts)
    assert hosts["web1"].ran_shells == []


async def test_fedora_is_rejected():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["web1"].linux_distribution = {**CENTOS_DISTRO, "name": "Fedora"}
    with pytest.raises(ValueError, match="not CentOS/RHEL"):
        await migrate(inventory, hosts)


# --- the pure helpers, unit-tested directly -----------------------------------


def test_conf_transform_is_a_fixpoint():
    transform = pg.make_conf_transform("log_directory", "log")
    once = transform(STOCK_CONF)
    assert transform(once) == once
    assert "log_directory = 'log'\n" in once
    # absent removes every matching line; a no-match absent is identity.
    absent = pg.make_conf_transform("log_directory", "log", present=False)
    assert "log_directory" not in absent(once)
    assert absent(absent(once)) == absent(once)
    assert absent("#listen_addresses = 'localhost'\n") == "#listen_addresses = 'localhost'\n"
    # None content (an absent file) appends the line.
    assert transform(None) == "log_directory = 'log'\n"


def test_conf_transform_replaces_last_match():
    text = "log_directory = 'old'\n#log_directory = 'commented'\n"
    transform = pg.make_conf_transform("log_directory", "log")
    out = transform(text)
    assert out == "log_directory = 'old'\nlog_directory = 'log'\n"


def test_parse_role_flags():
    assert pg.parse_role_flags("CREATEDB, NOLOGIN") == (
        ("createdb", True),
        ("login", False),
    )
    assert pg.parse_role_flags(None) is None
    assert pg.parse_role_flags("") == ()
    assert pg.parse_role_flags(["BYPASSRLS"]) == (("bypassrls", True),)
    with pytest.raises(ValueError, match="contradictory"):
        pg.parse_role_flags("LOGIN,NOLOGIN")
    with pytest.raises(ValueError, match="unknown role_attr_flags"):
        pg.parse_role_flags("WAT")
    with pytest.raises(TypeError):
        pg.parse_role_flags(42)


def test_resolved_vars_defaults_and_fips():
    host = SimulatedHost.from_inventory(make_inventory()[0])
    settings = pg.role_settings(host)
    rv = pg.resolved_vars(settings, MAJOR, False)
    assert rv["auth_method"] == "md5"
    assert rv["version"] == "13"
    assert rv["enable_repos"] is None
    assert rv["log_dir"] == LOG_DIR
    assert rv["packages"] == ("postgresql", "postgresql-server", "postgresql-contrib")
    fips = pg.resolved_vars(settings, MAJOR, True)
    assert fips["auth_method"] == "scram-sha-256"
    el10 = pg.resolved_vars(settings, 10, False)
    assert el10["version"] == "18"


def test_resolved_vars_enablerepo_and_absolute_log_dir():
    host = SimulatedHost.from_inventory(
        make_inventory(
            postgresql_enablerepo="crb, extras",
            postgresql_global_config_options=[
                {"option": "log_directory", "value": "/var/log/pgsql"}
            ],
        )[0]
    )
    rv = pg.resolved_vars(pg.role_settings(host), MAJOR, False)
    assert rv["enable_repos"] == ("crb", "extras")
    assert rv["log_dir"] == "/var/log/pgsql"


def test_normalize_users_duplicate_fails_fast():
    host = SimulatedHost.from_inventory(
        make_inventory(
            postgresql_users=[{"name": "jdoe"}, {"name": "jdoe"}]
        )[0]
    )
    with pytest.raises(ValueError, match="duplicate postgresql_users name"):
        pg.resolved_vars(pg.role_settings(host), MAJOR, False)


def test_normalize_privs_exotic_type_cites_29():
    host = SimulatedHost.from_inventory(
        make_inventory(
            postgresql_privs=[
                {"db": "exampledb", "roles": "jdoe", "privs": "ALL", "type": "language"}
            ]
        )[0]
    )
    with pytest.raises(ValueError, match="#29"):
        pg.resolved_vars(pg.role_settings(host), MAJOR, False)


def test_normalize_privs_mappings():
    host = SimulatedHost.from_inventory(
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
        )[0]
    )
    rv = pg.resolved_vars(pg.role_settings(host), MAJOR, False)
    func, db = rv["privs"]
    assert func["objects"] == ("public.f(int,text)",)
    assert db["objects"] == ("exampledb",)


def test_render_pg_hba_byte_exact():
    entries = [
        {"type": "local", "database": "all", "user": "postgres", "auth_method": "peer"}
    ]
    out = pg.render_pg_hba(entries)
    assert out.startswith("# Ansible managed\n")
    assert "local all postgres    peer \n" in out


def test_role_settings_rejects_bad_service_state():
    host = SimulatedHost.from_inventory(
        make_inventory(postgresql_service_state="restarted")[0]
    )
    with pytest.raises(ValueError, match="postgresql_service_state"):
        pg.role_settings(host)


def test_is_redhat_reads_the_static_family():
    redhat = SimulatedHost.from_inventory(make_inventory()[0])
    ubuntu = SimulatedHost.from_inventory(make_inventory()[1])
    assert pg.is_redhat(redhat)
    assert not pg.is_redhat(ubuntu)