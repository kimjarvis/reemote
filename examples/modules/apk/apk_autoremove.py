# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Remove orphaned apk packages standalone, without an install.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Read the target hosts from ``inventory.json`` in the project root -- an
   inventory is just a list of dictionaries, each holding a host's name plus
   its SSH ``connection`` information (address, username and client key),
   which is exactly what JSON expresses -- and validate it up front with
   ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is
   materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``). Each plain ``Host``
lazily opens an asyncssh connection from its ``connection`` data, gathers
facts and runs commands for real, and ``run`` closes those connections when
it finishes. The path is resolved from ``__file__``, so the example works from
any working directory; point ``INVENTORY_PATH`` at your own file to target
your machines.

``apk.autoremove`` is a reemote5 original: pyinfra ships no apk autoremove,
and apk itself has no ``autoremove`` verb -- it exposes orphan purge as the
``--purge`` flag of ``apk add``/``apk fix``, which reemote5 splits out into
this standalone operation so a deploy can clean up orphans without also
performing an install or a repair. The ``ApkAutoremovable`` fact simulates
the purge (``apk add --purge --simulate``, which never removes anything
and needs no root) and names exactly the orphaned packages: a non-empty
list runs the real ``apk add --purge``, an empty list is a no-op. The
script yields the *same* request twice to show that idempotence across the
two phases: on a machine with orphans the prepare phase plans both as
changes (nothing is sent yet), while the apply phase removes on the first
and reports the second as a no-op, because the fresh per-step fact cache
re-simulates against the cleaned-up state.

Purging writes to the root-owned apk database, so the requests are flagged
``sudo=True`` and the driver elevates their commands. Pair this with
``apk_packages.py``'s ``apk.packages`` (``present=False``) to compose the
remove-then-cleanup flow as two explicit, separately plannable steps -- the
same split ``apt.auto_remove`` makes against ``apt.dist_upgrade``'s
``auto_remove=True``.

Every request also carries ``when=is_alpine``, the declarative eligibility
guard (see
``examples/demonstrations/composition/composed_when_guard.py``). Two rules
make it deterministic: the predicate reads only **static** inventory data --
``host.group``, set by the ``"group"`` key of the inventory entry -- never a
mutable fact, and it is a stable module-level function, so the prepare and
apply phases reach the same skip decision. On a host the guard rejects, the
plan and the run log render the step as ``[skipped: when guard is_alpine]``
and none of its commands -- nor the ``ApkAutoremovable`` fact behind it, the
purge simulation -- is materialized at all, which is exactly why an
apk-native operation must be guarded rather than run blind on a host without
``apk``. The project root's ``inventory.json`` holds one host in the
``"alpine"`` group, so nothing is visibly skipped here; the same deploy
against a mixed inventory would clean up on the Alpine hosts only.

Run it with::

    uv run python examples/modules/apk/apk_autoremove.py
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
    apk,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# examples/modules/apk/apk_autoremove.py -> the project root, so the
# inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries
    each carrying at least ``host`` and ``connection``. Returns the parsed
    list, ready to hand to ``run``, which materializes it with the default
    ``Host.from_inventory`` factory; raises ``InventoryError`` (via
    ``validate_inventory``) when the document is not an array or an entry
    does not match the schema.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_alpine(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"alpine"".

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision and the
    determinism guard holds. apk itself is the reason for the guard: on a
    non-Alpine host every request below would fail on a missing ``apk``
    binary, so the driver skips them all instead.
    """
    return host.group == "alpine"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Autoremove orphaned packages -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request purges whatever the simulation names; the second
    carries the same arguments and is a no-op in the apply phase once the
    first cleanup left nothing purgable.

    Purging writes to the root-owned apk database, so both requests are
    flagged ``sudo=True`` and the driver elevates their commands over SSH.
    Branch-free means the two phases agree, and the ``when=is_alpine`` guard
    on both requests keeps the schedule identical on every host: eligibility
    is the driver's decision, not an ``if`` in this body.
    """
    yield op(apk.autoremove, name="Remove orphaned packages", sudo=True, when=is_alpine)
    yield op(apk.autoremove, name="Re-run autoremove (no-op)", sudo=True, when=is_alpine)


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Facts are still gathered over SSH (the
        # purge simulation is read-only) so the plan reflects each host's
        # real pre-deployment state; no command that mutates a host is sent.
        # A request the ``when`` guard rejects is planned as
        # [skipped: when guard is_alpine] without materializing it, so its
        # fact is not read on that host either.
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
