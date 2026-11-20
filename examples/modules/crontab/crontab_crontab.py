# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Ensure one scheduled cron entry with ``crontab.crontab``.

This is a *usage* example, not part of the library. It shows how an
application drives reemote's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with ``validate_inventory``
   so a malformed file fails with an ``InventoryError`` naming the
   offending entry, *before* any host is materialized or connected.
2. Build the deploy script as a sync, branch-free generator yielding
   ``OperationRequest`` objects via ``op``.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``), opening real asyncssh
connections that ``run`` closes when it finishes.

``crontab.crontab`` migrates pyinfra's ``crontab.crontab``: it adds, updates
or removes a single scheduled entry, keyed either by its command or -- as here
-- by a ``cron_name`` marker comment (``# reemote-name=backup_etc``) so two
identical commands can coexist and later changes to ``command`` overwrite the
same entry. The five schedule fields (``minute``/``hour``/``day_of_month``/
``month``/``day_of_week``) default to ``*``, a list is comma-joined, and
``special_time`` (``@reboot``, ``@daily``, ...) replaces them with a cron
nickname. ``present=False`` removes the entry.

Unlike pyinfra, which dumps the table to a temp file and edits it in place with
``sed``, reemote follows the ``sysctl`` spool-and-install model: the operation
reads the whole table through the ``Crontab`` fact (``crontab -l``), applies a
pure, control-side keyed edit that preserves every unrelated line, then -- only
on a difference -- spools the new text with an **unprivileged** scp into a
deterministic ``/tmp`` directory and reinstalls it with an elevated
``crontab``. The idempotence gate is the whole table, so a repeat that is
already in the desired state yields nothing.

The crontab writes the invoking user's *own* table, so ``sudo`` is not needed
to manage the login user's schedule; flag it only to manage another user's
crontab through the inventory ``privilege`` (which table to edit follows the
request's privilege, never an operation parameter -- pyinfra's ``user`` option
is deliberately dropped, as is ``interpolate_variables``, since the table is
computed in Python rather than rendered into a shell).

The script yields the *same* request twice to show idempotence across the two
phases: the prepare phase plans both as changes when the entry is absent
(nothing is sent in prepare), while the apply phase installs on the first and
reports the second as a no-op, because the fresh per-step fact cache now sees
the entry the first step installed.

The path is resolved from ``__file__``, so the example works from any working
directory; point ``INVENTORY_PATH`` at your own file to target your machines.

Run it with::

    uv run python examples/modules/crontab/crontab_crontab.py
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
    crontab,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# The entry to ensure: the documented weekly /etc backup. Every schedule field
# is a scalar, so each request argument stays hashable across the two phases.
COMMAND = "/bin/tar cf /tmp/etc_bup.tar /etc"
CRON_NAME = "backup_etc"

# examples/modules/crontab/crontab_crontab.py -> the project root, so the
# inventory is found whatever the current working directory is.
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


def deploy(host: Host) -> Generator[OperationRequest]:
    """Ensure the weekly backup cron -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases; the operation's own whole-table comparison decides what changes.
    """
    yield op(
        crontab.crontab,
        name="Backup /etc weekly",
        command=COMMAND,
        cron_name=CRON_NAME,
        minute=0,
        hour=1,
        day_of_week=0,
    )
    yield op(
        crontab.crontab,
        name="Re-ensure the weekly backup (no-op)",
        command=COMMAND,
        cron_name=CRON_NAME,
        minute=0,
        hour=1,
        day_of_week=0,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The crontab is read over SSH (read-only)
        # so the plan reflects each host's real state; no install is sent.
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
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
