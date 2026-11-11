# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Mask a systemd unit so it cannot be started, with ``systemd.mask``.

This is a *usage* example, not part of the library. It shows how an
application drives reemote's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Read the target hosts from ``inventory.json`` in the project root and
   validate it up front with ``validate_inventory``, so a malformed file
   fails with an ``InventoryError`` naming the offending entry before any
   host is materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``). Each plain ``Host``
lazily opens an asyncssh connection, gathers facts and runs commands for real,
and ``run`` closes those connections when it finishes. The path is resolved
from ``__file__``, so the example works from any working directory.

``systemd.mask`` is a reemote original (the baseline has no ``systemd.mask``). It
is bidirectional and idempotent through one present-flagged path: it reads the
unit's ``UnitFileState`` from the ``SystemdUnitProperties`` fact and yields
``systemctl mask`` only when the unit is not already masked. Masking symlinks
the unit to ``/dev/null`` so neither a manual start nor a dependency can
activate it -- a stronger guarantee than ``disable``, which only clears the
boot setting. ``masked=False`` would instead yield ``systemctl unmask``.

The script runs a full mask -> no-op -> unmask cycle, the same shape the
``selinux.permissive_domain`` example uses to leave the hosts exactly as it
found them. The first request masks httpd and the second carries the same
arguments: the prepare phase plans both as changes when httpd is not yet
masked (nothing is sent in prepare, so both compare against the same
pre-deployment state), while the apply phase masks on the first and reports
the second as a no-op through the fresh per-step fact cache. The third
request (``masked=False``) then unmasks httpd again, restoring the original
state so a later ``systemd.service`` / ``systemd.preset`` deploy is not
blocked by a lingering mask -- in the prepare phase it plans as a no-op
against the unmasked pre-deployment state, while the apply log sees the mask
the first step added and removes it.

Masking httpd momentarily prevents it from starting, but the cycle restores
the unit before the deploy finishes, so the script is safe to run live; it is
also compile-checked and exercised by the offline example harness. Point
``INVENTORY_PATH`` at your own machines to target them.

``systemctl mask`` writes root-owned state, so the requests are flagged
``sudo=True``. The scope flags (``user_mode``/``machine``/``user_name``) stay at
their system-scope defaults. Every request carries ``when=is_systemd``, a
declarative guard reading only static inventory data (``host.group``) so the
OpenRC ``alpine`` host is skipped rather than failing on a missing ``systemctl``.

Run it with::

    uv run python examples/modules/systemd/systemd_mask.py
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

# examples/modules/systemd/systemd_mask.py -> the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The unit to mask. A bare name gets ``.service`` appended.
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
    """Mask httpd -- twice, to show idempotence -- then unmask to restore it.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases (a branch on a mutable fact would raise DeterminismError); the
    ``when=is_systemd`` guard expresses eligibility, not an ``if``. The final
    ``masked=False`` request restores the original state so the hosts are left
    as they were found and a later service/preset deploy is not blocked.
    """
    yield op(
        systemd.mask,
        name="Mask httpd",
        unit=UNIT,
        masked=True,
        sudo=True,
        when=is_systemd,
    )
    yield op(
        systemd.mask,
        name="Re-mask httpd (no-op)",
        unit=UNIT,
        masked=True,
        sudo=True,
        when=is_systemd,
    )
    yield op(
        systemd.mask,
        name="Unmask httpd (restore)",
        unit=UNIT,
        masked=False,
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