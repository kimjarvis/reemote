# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: ensure replication is running on two real replica hosts.

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

``mysql.replica`` is a reemote5-original operation scoped to
START/STOP/RESET REPLICA (MySQL 8.0.22+ syntax). ``CHANGE REPLICATION
SOURCE TO`` is deliberately dropped -- it requires ``SOURCE_PASSWORD``
inline and MySQL has no passfile for replication, so configure the
source through ``mysql.sql`` or server tooling first. The operation
reads the ``MysqlReplication`` fact: both threads ``Yes`` means running,
both ``No`` means stopped, and a channel that is NOT configured raises
``OperationError`` in prepare -- the target hosts must already be
replicas (inspect them with the ``mysql_mysql_replication.py`` fact
example first). ``reset=True`` would statelessly render
``STOP REPLICA; RESET REPLICA ALL``, discarding every channel's
configuration.

The deploy yields the same start request twice: on a stopped replica
the second is a guaranteed no-op in the apply phase, because the first
START REPLICA is visible to it through the fresh per-step fact cache;
on an already-running replica both plan as no-ops.

Run it with::

    uv run python examples/modules/mysql/mysql_replica.py
"""

import asyncio
import sys
from collections.abc import Generator

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    OperationError,
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
    """Ensure the default replica channel is running, twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    START/STOP REPLICA needs REPLICATION_SLAVE_ADMIN, so every request is
    flagged ``sudo=True`` and rides under root's socket authentication.
    """
    yield op(
        mysql.replica,
        name="Ensure replication is running",
        running=True,
        sudo=True,
    )
    yield op(
        mysql.replica,
        name="Ensure replication is running (again)",
        running=True,
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
    except (CommandError, HostConnectionError, OperationError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command -- including the
        # OperationError an unconfigured replica channel raises in prepare.
        # Print that message -- no traceback -- and exit non-zero so the
        # failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())