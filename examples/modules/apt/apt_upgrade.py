# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Upgrade all apt packages, gated by a dry-run simulation.

This is a *usage* example, not part of the library. It shows how an
application drives reemote's two-phase execution model end to end over a
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
it finishes. The path is resolved from ``__file__``, so the example works
from any working directory; point ``INVENTORY_PATH`` at your own file to
target your machines.

``apt.upgrade`` mirrors the baseline's ``apt.upgrade``: the
``SimulateOperationWillChange`` fact first runs ``upgrade --dry-run``
(under ``LC_ALL=C`` and the non-interactive prefix) and parses the
``N upgraded, N newly installed, N to remove and N not upgraded.`` summary
line. When every count is zero the operation is a no-op; otherwise it runs
the real ``upgrade`` -- which, unlike ``dist-upgrade``, never installs new
dependencies or removes conflicting packages. A failed simulation (no
summary line) runs the real command anyway, the baseline's fallback. The script
yields the *same* request twice to show that idempotence across the two
phases: on a machine with pending upgrades the prepare phase plans both as
changes (nothing is sent yet), while the apply phase upgrades on the first
and reports the second as a no-op, because the fresh per-step fact cache
re-simulates against the upgraded state.

Where the baseline's ``apt.upgrade`` also takes an ``auto_remove`` parameter
(appending ``--autoremove`` to the same command), reemote deliberately
drops it in favour of the standalone ``apt.auto_remove`` operation -- see
``apt_auto_remove.py`` beside this script -- so orphaned dependencies can be
cleaned up without also performing an upgrade. This operation never passes
``--autoremove``.

Upgrading writes to the root-owned dpkg database, so the requests are
flagged ``sudo=True`` and the driver elevates their commands. The dry-run
simulation itself needs no root.

Like the other apt examples, every request carries ``when=is_ubuntu``: the
predicate reads only static inventory data (``host.group``) and is a stable
module-level function, so both phases reach the same skip decision. The
driver skips a rejected request without materializing it -- neither the
``upgrade`` command nor the ``SimulateOperationWillChange`` dry-run behind
it runs on a non-Ubuntu host -- and plan and log render
``[skipped: when guard is_ubuntu]``. The shared ``inventory.json`` holds one
host in the ``"ubuntu"`` group alongside hosts in other groups, so each run
upgrades the Ubuntu host and skips every other one.

Run it with::

    uv run python examples/modules/apt/apt_upgrade.py
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
    apt,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# examples/modules/apt/apt_upgrade.py -> the project root, so the inventory
# is found whatever the current working directory is.
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


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"ubuntu"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision and the
    determinism guard holds. apt itself is the reason for the guard: on a
    non-Ubuntu host every request below would fail on a missing ``apt-get``
    binary, so the driver skips them all instead.
    """
    return host.group == "ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Upgrade all packages -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request upgrades whatever the simulation proposes; the second
    carries the same arguments and is a no-op in the apply phase once the
    first upgrade left nothing more to do.

    Upgrading writes to the root-owned dpkg database, so both requests are
    flagged ``sudo=True`` and the driver elevates their commands over SSH.
    """
    yield op(
        apt.upgrade,
        name="Upgrade apt packages",
        sudo=True,
        when=is_ubuntu,
    )
    yield op(
        apt.upgrade,
        name="Re-run upgrade (no-op)",
        sudo=True,
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Facts are still gathered over SSH (the
        # dry-run simulation is read-only) so the plan reflects each host's
        # real pre-deployment state; no command that mutates a host is sent.
        # A request the ``when`` guard rejects is planned as
        # [skipped: when guard is_ubuntu] without materializing it, so its
        # fact is not read on that host either.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan (prepare phase, --dry) ==")
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