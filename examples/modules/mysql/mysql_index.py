# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: ensure a secondary index on real Ubuntu hosts.

This is a *usage* example, not part of the library. It drives reemote's
two-phase execution model end to end over a **real SSH transport**
(asyncssh):

1. Read ``inventory.json`` from the current working directory and parse it as JSON -- an
   inventory is just a list of dictionaries, which is exactly what JSON expresses --
   then validate it up front with ``validate_inventory`` so a malformed file fails with
   an ``InventoryError`` naming the offending entry, *before* any host is materialized
   or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects. Every request is flagged ``sudo=True``, so the ``mysql``
   client rides under root and connects through unix-socket
   authentication -- no password travels through operation arguments,
   plans or logs (the baseline's ``mysql_password`` option is deliberately
   dropped namespace-wide).
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``mysql.index`` is a reemote-original operation, the ``postgresql.index``
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

**Host prerequisite.** This example needs a running MySQL server. The demo Ubuntu host
runs MySQL 8.4.10 as a snap whose ``root@localhost`` account uses ``auth_socket``, so
the ``sudo=True`` operations connect through the unix socket as root without a password.
Fact reads ride the login user first and are re-read elevated with ``sudo`` when the
server denies them (the ``FileContents`` fallback precedent), so the login OS user needs
no MySQL account of its own. Replace ``inventory.json`` in the current working directory
with your own file of MySQL machines to run live.

Every request carries ``when=is_ubuntu``, the declarative eligibility guard: the
predicate reads only **static** inventory data -- ``host.family``, set by the
``"family"`` key of the inventory entry -- and is a stable module-level function, so the
prepare and apply phases reach the same skip decision. The driver skips a rejected
request without materializing it, so on hosts outside the ``"Ubuntu"`` family neither
the commands nor the facts behind them run, and plan and log render the step as
``[skipped: when guard is_ubuntu]`` in its lockstep slot. The inventory is read from
``inventory.json`` in the current working directory; edit it there to target your
machines.

Run it with::

    uv run python examples/modules/mysql/mysql_index.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    OperationError,
    OperationRequest,
    PrivilegeError,
    format_log,
    format_plan,
    mysql,
    op,
    run,
    validate_inventory,
)


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision.
    """
    return host.family == "Ubuntu"


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
        when=is_ubuntu,
    )
    yield op(
        mysql.index,
        name="Ensure orders total index (again)",
        database="shop",
        table="orders",
        index="idx_orders_total",
        columns=("total",),
        sudo=True,
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON. Python's
        # own message already names the file and the reason, so print just that
        # -- no host was contacted and there is no traceback worth showing.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema. The
        # message names the offending entry by its list index, which is the
        # whole report -- and it fails before any host is materialized.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
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