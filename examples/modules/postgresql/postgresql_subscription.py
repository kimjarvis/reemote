# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: ensure a logical-replication subscription on two real hosts.

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

``postgresql.subscription`` is a reemote5-original operation managing
logical-replication subscriptions: it reads the
``PostgresqlSubscriptions`` fact for the database named by
``psql_database`` -- an absent subscription yields CREATE SUBSCRIPTION
... CONNECTION '<dsn>' PUBLICATION <names> (with WITH (enabled = false)
when ``enabled=False``), and an existing one reconciles its publication
list and enabled flag through ALTER SUBSCRIPTION only where they differ.
The connection string is never compared -- the fact deliberately hides
``subconninfo`` because it can embed credentials -- so changing the DSN
needs a drop and re-create. ``present=False`` drops it.

The script creates the subscription disabled, so no apply worker starts;
the publisher must still be reachable at creation time (PostgreSQL opens
the connection to create the replication slot) and needs
``wal_level=logical`` plus a ``pg_hba.conf`` replication entry for the
connecting role. The DSN here points at web1's appdb and carries no
password -- authentication rides the host's own pg_hba/trust setup.

The deploy first ensures the local database exists, then yields the same
subscription request twice: in the prepare phase (where nothing is sent)
both plan as changes, but in the apply phase the second request's fresh
per-step fact cache sees the first CREATE SUBSCRIPTION and reports
nothing to update.

Run it with::

    uv run python examples/modules/postgresql/postgresql_subscription.py
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

# The publisher DSN: web1's appdb, no password embedded -- the fact never
# reads conninfo back, and credentials stay out of plans and logs.
PUBLISHER_DSN = "host=192.168.0.240 dbname=appdb user=postgres"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Ensure appdb, then the disabled app_sub subscription twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    Every request is flagged ``sudo=True`` and rides under the inventory's
    postgres user: CREATE SUBSCRIPTION needs superuser.
    """
    yield op(
        postgresql.database,
        name="Ensure app database",
        database="appdb",
        sudo=True,
    )
    yield op(
        postgresql.subscription,
        name="Ensure app subscription (disabled)",
        subscription="app_sub",
        connection=PUBLISHER_DSN,
        publications=("app_pub",),
        enabled=False,
        psql_database="appdb",
        sudo=True,
    )
    yield op(
        postgresql.subscription,
        name="Ensure app subscription (disabled, again)",
        subscription="app_sub",
        connection=PUBLISHER_DSN,
        publications=("app_pub",),
        enabled=False,
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