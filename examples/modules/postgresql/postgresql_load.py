# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: load a host-side SQL file through psql on real CentOS hosts.

This is a *usage* example, not part of the library. It drives reemote's
two-phase execution model end to end over a **real SSH transport**
(asyncssh):

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with ``validate_inventory``
   so a malformed file fails with an ``InventoryError`` naming the
   offending entry, *before* any host is materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``postgresql.load`` mirrors the baseline's operation: it is *stateless* -- the
file always executes -- and ``src`` is a path on the TARGET host, not the
controller (spool controller-side bytes there with ``files.put`` first).
To keep the round-trip self-contained, this script first dumps the
postgres database to a host-side file with ``postgresql.dump``, then
loads that same file back through ``postgresql.load``; the apply phase
runs the two requests in lockstep order, so the dump is on disk before
the load reads it.

**Host prerequisite.** This example needs a running PostgreSQL server. The
demo CentOS hosts install the packages fine (``sudo dnf install -y
postgresql-server``), but their ``staff_t``-confined sudo cannot initialize
the cluster: ``initdb`` cannot exec ``/usr/bin/postgres`` and
``postgresql-setup --initdb``'s ``runuser`` cannot setuid, so the server
never starts and no ``/var/run/postgresql`` socket exists for either the
login-user fact reads or the ``sudo -u postgres`` operations. Compile-checked
and exercised by the offline harness, not run against shared hosts; relax the
demo hosts' sudo or point ``INVENTORY_PATH`` at your own PostgreSQL machines to run
live.

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

The shared ``inventory.json`` carries no ``privilege`` section, so ``main``
attaches ``{"sudo_user": "postgres"}`` to the loaded ``centos`` entries:
the ``sudo=True`` requests ride under the ``postgres`` OS user, so the
``psql`` client connects through peer authentication -- no password
travels through operation arguments, plans or logs (the baseline's
``psql_password`` option is deliberately dropped namespace-wide).

Run it with::

    uv run python examples/modules/postgresql/postgresql_load.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    format_log,
    format_plan,
    op,
    postgresql,
    run,
    validate_inventory,
)

# examples/modules/postgresql/postgresql_load.py -> the project root, so the inventory
# is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


# The privilege section these scripts attach to the loaded ``centos``
# inventory entries (the shared file carries none): ``sudo_user="postgres"``
# is the OS user the ``sudo=True`` requests ride under, so the ``psql``
# client connects through peer authentication -- no password travels
# through operation arguments, plans or logs.
PRIVILEGE = {"sudo_user": "postgres"}

# The host-side SQL file both requests share: the dump writes it, the load
# reads it, both under the postgres user's home on the TARGET host.
DUMP_PATH = "/var/lib/postgresql/postgres_roundtrip.sql"


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
        when=is_centos,
    )
    yield op(
        postgresql.load,
        name="Load the dump back through psql",
        src=DUMP_PATH,
        psql_database="postgres",
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)

        # The shared inventory carries no privilege section; attach this
        # namespace's to the centos entries, so elevation stays an inventory
        # parameter and psql rides the postgres user's peer-authenticated
        # socket.
        for entry in inventory:
            if entry.get("group") == "centos":
                entry["privilege"] = dict(PRIVILEGE)
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