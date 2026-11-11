# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Clear a systemd unit's failed state with ``systemd.reset_failed``.

This is a *usage* example, not part of the library. It shows how an
application drives reemote's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Read the target hosts from ``inventory.json`` in the project root and
   validate it up front with ``validate_inventory``, so a malformed file
   fails with an ``InventoryError`` before any host is connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``), opening real asyncssh
connections that ``run`` closes when it finishes. The path is resolved from
``__file__``, so the example works from any working directory.

``systemd.reset_failed`` is a reemote original. The baseline folds reset-failed
into ``systemd.service`` (the enhanced ``service`` keeps a ``reset_failed``
flag); this standalone operation covers the reset-only case. It is idempotent:
it reads the ``SystemdServiceState`` fact and yields ``systemctl reset-failed``
only when the unit's ``ActiveState`` is ``failed`` -- any other state is left
alone, because the real command is a no-op then. A failed unit otherwise
refuses to start until its failure is cleared, so this is the recovery step
before a start.

The script yields the *same* request twice to show idempotence across the two
phases: the prepare phase plans both as changes when httpd is failed (nothing
is sent in prepare), while the apply phase resets on the first and reports the
second as a no-op, because the fresh per-step fact cache now sees the
``inactive`` state the reset produced.

``systemctl reset-failed`` writes root-owned state, so the requests are flagged
``sudo=True``. The scope flags stay at their system-scope defaults. Every
request carries ``when=is_systemd``, reading only static ``host.group`` so the
OpenRC ``alpine`` host is skipped rather than failing on a missing ``systemctl``.

Run it with::

    uv run python examples/modules/systemd/systemd_reset_failed.py
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
    run,
    systemd,
    validate_inventory,
)

# examples/modules/systemd/systemd_reset_failed.py -> the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The unit whose failed state to clear. A bare name gets ``.service`` appended.
UNIT = "httpd"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it."""
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_systemd(host: Host) -> bool:
    """``when`` guard: the host runs systemd rather than OpenRC.

    Reads only static inventory data (``host.group``), never a mutable fact,
    and stays a stable module-level function so both deploy phases reach the
    same eligibility decision and the determinism guard holds.
    """
    return host.group != "alpine"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Reset httpd's failed state -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases; the ``when=is_systemd`` guard expresses eligibility, not an ``if``.
    """
    yield op(
        systemd.reset_failed,
        name="Reset httpd failed state",
        unit=UNIT,
        sudo=True,
        when=is_systemd,
    )
    yield op(
        systemd.reset_failed,
        name="Re-reset httpd (no-op)",
        unit=UNIT,
        sudo=True,
        when=is_systemd,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Facts are gathered over SSH (read-only) so
        # the plan reflects each host's real state; no mutating command is sent.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())