# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: load a host-side SQL file through psql on two real hosts.

This is a *usage* example, not part of the library. It drives reemote5's
two-phase execution model end to end over a **real SSH transport**
(asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key). Each entry also carries a ``privilege``
   section: ``sudo_user="postgres"`` is the OS user the ``sudo=True``
   requests ride under, so ``psql`` connects through peer authentication
   -- no password travels through operation arguments, plans or logs
   (pyinfra's ``psql_password`` option is deliberately dropped
   namespace-wide).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``postgresql.load`` mirrors pyinfra's operation: it is *stateless* -- the
file always executes -- and ``src`` is a path on the TARGET host, not the
controller (spool controller-side bytes there with ``files.put`` first).
To keep the round-trip self-contained, this script first dumps the
postgres database to a host-side file with ``postgresql.dump``, then
loads that same file back through ``postgresql.load``; the apply phase
runs the two requests in lockstep order, so the dump is on disk before
the load reads it.

Run it with::

    uv run python examples/modules/postgresql/postgresql_load.py
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

# The host-side SQL file both requests share: the dump writes it, the load
# reads it, both under the postgres user's home on the TARGET host.
DUMP_PATH = "/var/lib/postgresql/postgres_roundtrip.sql"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Dump the postgres database, then load the dump back (both stateless).

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    Both requests are flagged ``sudo=True`` and ride under the inventory's
    postgres user, which can write and read under /var/lib/postgresql.
    """
    yield op(
        postgresql.dump,
        name="Dump the postgres database",
        dest=DUMP_PATH,
        psql_database="postgres",
        sudo=True,
    )
    yield op(
        postgresql.load,
        name="Load the dump back through psql",
        src=DUMP_PATH,
        psql_database="postgres",
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