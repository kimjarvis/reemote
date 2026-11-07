# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: execute arbitrary SQL on two real hosts.

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

``postgresql.sql`` mirrors pyinfra's operation: it is *stateless* -- the
statement always runs, in every apply -- the escape hatch for anything
the typed operations do not cover. Prefer a fact-comparing operation when
one exists, and a naturally idempotent statement (``CREATE ... IF NOT
EXISTS``, ``ALTER ... SET``) when it does not; this script uses ``CREATE
TABLE IF NOT EXISTS`` so re-running it converges even though the
operation itself never reports a no-op. The SQL text appears verbatim in
the plan and apply log, so never embed credentials in it.

Run it with::

    uv run python examples/modules/postgresql/postgresql_sql.py
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

# A naturally idempotent statement: the second execution is a server-side
# no-op even though the stateless operation always reports a change.
MARKER_SQL = "CREATE TABLE IF NOT EXISTS public.deploy_marker (id integer)"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Execute the same idempotent statement twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    Creating a table in the postgres database needs superuser here, so
    both requests are flagged ``sudo=True`` and ride under the inventory's
    postgres user.
    """
    yield op(
        postgresql.sql,
        name="Ensure deploy marker table",
        sql=MARKER_SQL,
        sudo=True,
    )
    yield op(
        postgresql.sql,
        name="Ensure deploy marker table (again)",
        sql=MARKER_SQL,
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