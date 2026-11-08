# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: grant table privileges to an application role on two real hosts.

This is a *usage* example, not part of the library. It drives reemote5's
two-phase execution model end to end over a **real SSH transport**
(asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key). Each entry also carries a ``privilege``
   section: ``sudo_user="postgres"`` is the OS user the ``sudo=True``
   requests ride under, so the ``psql`` client connects through peer
   authentication -- no password travels through operation arguments,
   plans or logs (pyinfra's ``psql_password`` option is deliberately
   dropped namespace-wide).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``postgresql.privilege`` is a reemote5-original operation modeled on
Ansible's ``postgresql_privs``: it reads the ``PostgresqlPrivileges`` fact
for the grantee's current grants and touches only the difference --
``present=True`` grants just the missing privileges (``ALL`` expands to
the object type's full set), ``present=False`` revokes just the held
ones, and ``grant_option`` upgrades to WITH GRANT OPTION or withdraws
only the option. Objects are named by ``objects`` (bare or
``schema.name``) or by ``all_in_schema``.

The deploy first provisions the grantee role, the database and the table
(the table through the ``postgresql.sql`` escape hatch with a naturally
idempotent statement), then yields the same grant request twice: in the
prepare phase (where nothing is sent) both plan as changes, but in the
apply phase the second request's fresh per-step fact cache sees the first
GRANT and reports the role already has the privileges.

**Host prerequisite.** This example needs a running PostgreSQL server. The
demo CentOS hosts install the packages fine (``sudo dnf install -y
postgresql-server``), but their ``staff_t``-confined sudo cannot initialize
the cluster: ``initdb`` cannot exec ``/usr/bin/postgres`` and
``postgresql-setup --initdb``'s ``runuser`` cannot setuid, so the server
never starts and no ``/var/run/postgresql`` socket exists for either the
login-user fact reads or the ``sudo -u postgres`` operations. Compile-checked
and exercised by the offline harness, not run against shared hosts; relax the
demo hosts' sudo or point ``INVENTORY`` at your own PostgreSQL machines to run
live.

Run it with::

    uv run python examples/modules/postgresql/postgresql_privilege.py
"""

import asyncio
import sys
from collections.abc import Generator

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    OperationRequest,
    PrivilegeError,
    format_log,
    format_plan,
    op,
    postgresql,
    run,
)

# The inventory: real hosts and their SSH connection information, supplied
# by the application to the library. The privilege section names the OS user
# elevation rides under -- postgres, so psql authenticates as the database
# superuser through peer auth. No password appears here or in any request.
INVENTORY = [
    {
        "host": "web1",
        "connection": {
            "address": "192.168.0.240",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
        "privilege": {"sudo_user": "postgres"},
    },
    {
        "host": "web2",
        "connection": {
            "address": "192.168.0.242",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
        "privilege": {"sudo_user": "postgres"},
    },
]

# The table the grant targets, created through the sql escape hatch with a
# naturally idempotent statement (postgresql has no typed table operation).
USERS_SQL = (
    "CREATE TABLE IF NOT EXISTS public.users (id integer, email text)"
)


def deploy(host: Host) -> Generator[OperationRequest]:
    """Provision role/database/table, then grant SELECT+INSERT twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    Every request is flagged ``sudo=True`` and rides under the inventory's
    postgres user: role/database creation needs superuser, and granting on
    a postgres-owned table needs its owner.
    """
    yield op(
        postgresql.role,
        name="Ensure app grantee role",
        role="app_user",
        login=True,
        sudo=True,
    )
    yield op(
        postgresql.database,
        name="Ensure app database",
        database="appdb",
        owner="app_user",
        sudo=True,
    )
    yield op(
        postgresql.sql,
        name="Ensure users table",
        sql=USERS_SQL,
        psql_database="appdb",
        sudo=True,
    )
    yield op(
        postgresql.privilege,
        name="Grant SELECT and INSERT on users",
        privileges=("SELECT", "INSERT"),
        role="app_user",
        object_type="table",
        objects=("public.users",),
        psql_database="appdb",
        sudo=True,
    )
    yield op(
        postgresql.privilege,
        name="Grant SELECT and INSERT on users (again)",
        privileges=("SELECT", "INSERT"),
        role="app_user",
        object_type="table",
        objects=("public.users",),
        psql_database="appdb",
        sudo=True,
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
        plans, report, _hosts = await run(INVENTORY, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())