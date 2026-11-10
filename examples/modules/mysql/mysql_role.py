# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: ensure a role and its membership on real CentOS hosts.

This is a *usage* example, not part of the library. It drives reemote5's
two-phase execution model end to end over a **real SSH transport**
(asyncssh):

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with ``validate_inventory``
   so a malformed file fails with an ``InventoryError`` naming the
   offending entry, *before* any host is materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects. Every request is flagged ``sudo=True``, so the ``mysql``
   client rides under root and connects through unix-socket
   authentication -- no password travels through operation arguments,
   plans or logs (the baseline's ``mysql_password`` option is deliberately
   dropped namespace-wide).
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``mysql.role`` is a reemote5-original operation managing roles
(MySQL 8.0+). Existence is idempotent against the ``MysqlUsers`` fact
keyed ``role@role_hostname``; membership is idempotent against the
role-grant extension of ``MysqlUserGrants``. ``members`` are ``user@host``
accounts the role is granted to -- memberships are only ADDED, never
revoked (list every member the role should keep), and the member
accounts must already exist (the ``mysql_user.py`` operation example
creates ``shop_app@localhost``). Grant privileges to the role itself
with ``mysql.privileges``. ``present=False`` drops the role, which
revokes every membership server-side. Container arguments are tuples:
``op()`` hashes every request value for deduplication.

The deploy yields the same create-and-grant request twice: in the
prepare phase (where nothing is sent) both plan as changes, but in the
apply phase the second request's fresh per-step fact cache sees the
first CREATE ROLE and GRANT and reports the role and membership already
exist.

**Host prerequisite.** This example needs a running MySQL server. The demo
CentOS hosts install and start it fine (``sudo dnf install -y mysql-server``,
``sudo systemctl start mysqld``), but their ``staff_t``-confined client
cannot reach the unix socket ``/var/lib/mysql/mysql.sock`` (``ERROR 2002
... (13)`` -- SELinux denies ``staff_t`` the ``mysqld_db_t`` socket the
no-password design needs). Compile-checked and exercised by the offline
harness, not run against shared hosts; relax the demo hosts' sudo or point
``INVENTORY_PATH`` at your own MySQL machines to run live.

Every request carries ``when=is_centos``, the declarative eligibility
guard: the predicate reads only **static** inventory data --
``host.group``, set by the ``"group"`` key of the inventory entry -- and
is a stable module-level function, so the prepare and apply phases reach
the same skip decision. The driver skips a rejected request without
materializing it, so on hosts outside the ``"centos"`` group neither the
commands nor the facts behind them run, and plan and log render the step
as ``[skipped: when guard is_centos]`` in its lockstep slot. The path is
resolved from ``__file__``, so the example works from any working
directory; point ``INVENTORY_PATH`` at your own file to target your
machines.

Run it with::

    uv run python examples/modules/mysql/mysql_role.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    format_log,
    format_plan,
    mysql,
    op,
    run,
    validate_inventory,
)

# examples/modules/mysql/mysql_role.py -> the project root, so the inventory
# is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries
    each carrying at least ``host`` and ``connection``. Returns the parsed
    list, ready to materialize into hosts; raises ``InventoryError`` (via
    ``validate_inventory``) when the document is not an array or an entry
    does not match the schema.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_centos(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"centos"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision.
    """
    return host.group == "centos"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Ensure the shop_readonly role and its membership twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    CREATE ROLE and GRANT role TO user need CREATE ROLE / ROLE_ADMIN, so
    every request is flagged ``sudo=True`` and rides under root's socket
    authentication.
    """
    yield op(
        mysql.role,
        name="Ensure shop_readonly role",
        role="shop_readonly",
        members=("shop_app@localhost",),
        sudo=True,
        when=is_centos,
    )
    yield op(
        mysql.role,
        name="Ensure shop_readonly role (again)",
        role="shop_readonly",
        members=("shop_app@localhost",),
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
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
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())