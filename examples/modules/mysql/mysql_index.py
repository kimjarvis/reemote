# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: ensure a secondary index on two real hosts.

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

``mysql.index`` is a reemote5-original operation, the ``postgresql.index``
shape: it reads the ``MysqlIndexes`` fact keyed on the index name within
the table. An absent index yields CREATE INDEX over the ``columns`` list
(``unique=True`` renders CREATE UNIQUE INDEX, FULLTEXT/SPATIAL render as
the CREATE prefix); an existing index whose columns, uniqueness or type
differ raises ``OperationError`` rather than silently rebuilding a
possibly constraint-backing index -- drop it first to rebuild. The
``shop.orders`` table must already exist (the ``mysql_table.py``
operation example creates it). Container arguments are tuples: ``op()``
hashes every request value for deduplication.

The deploy yields the same create request twice: in the prepare phase
(where nothing is sent) both plan as changes, but in the apply phase the
second request's fresh per-step fact cache sees the first CREATE INDEX
and reports the index already exists.

Run it with::

    uv run python examples/modules/mysql/mysql_index.py
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
    """Ensure the idx_orders_total index twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    CREATE/DROP INDEX needs INDEX on the database, so every request is
    flagged ``sudo=True`` and rides under root's socket authentication.
    """
    yield op(
        mysql.index,
        name="Ensure orders total index",
        database="shop",
        table="orders",
        index="idx_orders_total",
        columns=("total",),
        sudo=True,
    )
    yield op(
        mysql.index,
        name="Ensure orders total index (again)",
        database="shop",
        table="orders",
        index="idx_orders_total",
        columns=("total",),
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
        # OperationError an existing index with a differing definition raises
        # in prepare. Print that message -- no traceback -- and exit non-zero
        # so the failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())