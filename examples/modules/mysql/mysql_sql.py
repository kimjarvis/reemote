# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: execute an idempotent SQL statement on two real hosts.

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

``mysql.sql`` mirrors pyinfra's operation: it executes the statement
through ``mysql -Be`` and is STATELESS -- the statement always runs, in
the plan's apply phase and every subsequent run -- so it is the escape
hatch for anything the typed operations do not cover (schema migrations,
CHANGE REPLICATION SOURCE TO, ...). Prefer a fact-comparing operation
when one exists, and a naturally idempotent statement like the
``CREATE TABLE IF NOT EXISTS`` used here when it does not. The SQL text
appears verbatim in the plan and log, so never embed credentials in it.
The ``shop`` database must already exist (the ``mysql_database.py``
operation example creates it).

The deploy yields the same statement twice; both run, and the IF NOT
EXISTS clause keeps the second a server-side no-op.

Run it with::

    uv run python examples/modules/mysql/mysql_sql.py
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

# A naturally idempotent statement: the operation is stateless, so the
# idempotence lives in the SQL itself.
STATEMENT = "CREATE TABLE IF NOT EXISTS shop.healthcheck (id INT PRIMARY KEY)"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Execute the healthcheck CREATE TABLE IF NOT EXISTS twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    CREATE TABLE needs CREATE on the database, so every request is flagged
    ``sudo=True`` and rides under root's socket authentication.
    """
    yield op(
        mysql.sql,
        name="Ensure healthcheck table",
        sql=STATEMENT,
        sudo=True,
    )
    yield op(
        mysql.sql,
        name="Ensure healthcheck table (again)",
        sql=STATEMENT,
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