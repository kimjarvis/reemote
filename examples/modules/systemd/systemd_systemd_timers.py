# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``systemd.SystemdTimers`` fact on the systemd hosts.

This is a *usage* example, not part of the library. It reads a single **fact**
directly over the *real SSH transport* (asyncssh) instead of driving the
two-phase execution model:

1. Read the target hosts from ``inventory.json`` in the project root and
   validate it with ``validate_inventory``.
2. Materialize each entry into a plain ``Host`` and keep only the systemd
   hosts -- ``gather_fact`` has no ``when`` guard, so the OpenRC ``alpine``
   host (which has no ``systemctl``) is filtered out of the host list before
   gathering rather than failing on a missing binary.
3. Gather ``SystemdTimers`` on every remaining host at once with
   ``gather_fact``, then print each host's timer map with ``json.dumps``.

``SystemdTimers`` is a reemote5 original: it runs
``systemctl show '*.timer' --property=Id,ActiveState,UnitFileState,
NextElapseUSecRealtime,LastTriggerUSec`` and maps each timer unit to a
``SystemdTimer`` entry (``{active_state, unit_file_state, next_elapse_usec,
last_trigger_usec}``). The machine-readable ``show`` is used rather than the
human-readable ``list-timers`` table (whose relative, locale-formatted columns
are fragile to parse); the two ``...USec`` timestamps are integer microseconds
since the epoch, with ``0``/empty mapped to None ("not scheduled"/"never
fired"). The scope flags stay at their system-scope defaults here.

Every host's connection is closed with ``aclose`` once the facts are gathered.
The inventory path is resolved from ``__file__`` so the example works from any
working directory.

Run it with::

    uv run python examples/modules/systemd/systemd_systemd_timers.py
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    SystemdTimers,
    gather_fact,
    validate_inventory,
)

# examples/modules/systemd/systemd_systemd_timers.py -> the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it."""
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_systemd(host: Host) -> bool:
    """Host-list filter: the host runs systemd rather than OpenRC.

    Reads only static inventory data (``host.group``); the ``alpine`` group
    uses OpenRC, where ``systemctl`` is absent.
    """
    return host.group != "alpine"


async def main() -> None:
    hosts: list[Host] = []
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        hosts = [Host.from_inventory(entry) for entry in inventory]
        systemd_hosts = [host for host in hosts if is_systemd(host)]
        skipped = [host.name for host in hosts if not is_systemd(host)]
        if skipped:
            print(f"== Skipping non-systemd hosts: {', '.join(skipped)} ==")

        timers = await gather_fact(systemd_hosts, SystemdTimers)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        await asyncio.gather(*(host.aclose() for host in hosts))

    for host in systemd_hosts:
        print(f"== {host.name}: systemd.SystemdTimers ==")
        print(json.dumps(timers[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())