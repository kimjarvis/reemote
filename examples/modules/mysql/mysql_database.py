# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: ensure an application database on two real hosts.

This is a *usage* example, not part of the library. It drives reemote5's
two-phase execution model end to end over a **real SSH transport**
(asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects. Every request is flagged ``sudo=True``, so the ``mysql``
   client rides under root and connects through unix-socket
   authentication -- no password travels through operation arguments,
   plans or logs (pyinfra's ``mysql_password`` option is deliberately
   dropped namespace-wide).
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``mysql.database`` mirrors pyinfra's operation and extends it beyond
creation: it reads the ``MysqlDatabases`` fact, creates an absent database
with the requested charset/collation, and reconciles an existing one's
character set and collation through ALTER DATABASE only where they differ
(pyinfra applies them only at CREATE time and never alters).

The deploy yields the same create request twice: in the prepare phase
(where nothing is sent) both plan as changes, but in the apply phase the
second request's fresh per-step fact cache sees the first CREATE DATABASE
and reports the database already exists with nothing to update.

**Host prerequisite.** This example needs a running MySQL server. The demo
CentOS hosts install and start it fine (``sudo dnf install -y mysql-server``,
``sudo systemctl start mysqld``), but their ``staff_t``-confined client
cannot reach the unix socket ``/var/lib/mysql/mysql.sock`` (``ERROR 2002
... (13)`` -- SELinux denies ``staff_t`` the ``mysqld_db_t`` socket the
no-password design needs). Compile-checked and exercised by the offline
harness, not run against shared hosts; relax the demo hosts' sudo or point
``INVENTORY`` at your own MySQL machines to run live.

Run it with::

    uv run python examples/modules/mysql/mysql_database.py
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
    mysql,
    op,
    run,
)

# The inventory: real hosts and their SSH connection information, supplied
# by the application to the library. No privilege section is needed: sudo
# defaults to root, whose unix-socket auth_socket login needs no password.
INVENTORY = [
    {
        "host": "web1",
        "connection": {
            "address": "192.168.0.240",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
    {
        "host": "web2",
        "connection": {
            "address": "192.168.0.242",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
]


def deploy(host: Host) -> Generator[OperationRequest]:
    """Ensure the shop database twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    CREATE/ALTER DATABASE needs the matching rights on the database, so
    every request is flagged ``sudo=True`` and rides under root's socket
    authentication.
    """
    yield op(
        mysql.database,
        name="Ensure shop database",
        database="shop",
        charset="utf8mb4",
        collate="utf8mb4_0900_ai_ci",
        sudo=True,
    )
    yield op(
        mysql.database,
        name="Ensure shop database (again)",
        database="shop",
        charset="utf8mb4",
        collate="utf8mb4_0900_ai_ci",
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