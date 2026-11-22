# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``systemd.SystemdFailedUnits`` fact on the systemd hosts.

This is a *usage* example, not part of the library. It reads a single **fact**
directly over the *real SSH transport* (asyncssh) instead of driving the
two-phase execution model:

1. Read the target hosts from ``inventory.json`` in the current working directory and
   validate it with ``validate_inventory``.
2. Materialize each entry into a plain ``Host`` and keep only the systemd
   hosts -- ``gather_fact`` has no ``when`` guard, so the OpenRC ``alpine``
   host (which has no ``systemctl``) is filtered out of the host list before
   gathering rather than failing on a missing binary.
3. Gather ``SystemdFailedUnits`` on every remaining host at once with
   ``gather_fact``, then print each host's failed-unit list with
   ``json.dumps(..., indent=2)``.

``SystemdFailedUnits`` is a reemote original: it runs
``systemctl --failed --no-legend --no-pager`` and parses each row into a
``SystemdFailedUnit`` entry (``{unit, load, active, sub, description}``),
stripping the leading bullet and keeping the description's internal spaces.
A healthy host prints nothing and reads as ``[]`` -- the quick "is anything
broken?" probe before a deploy. The scope flags stay at their system-scope
defaults here.

Every host's connection is closed with ``aclose`` once the facts are gathered. The
inventory is read from ``inventory.json`` in the current working directory.

Run it with::

    uv run python examples/modules/systemd/systemd_systemd_failed_units.py
"""

import asyncio
import json
import sys
from pathlib import Path

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    SystemdFailedUnits,
    gather_fact,
    validate_inventory,
)


def is_systemd(host: Host) -> bool:
    """Host-list filter: the host runs systemd rather than OpenRC.

    Reads only static inventory data (``host.family``); the ``"Alpine"`` family
    uses OpenRC, where ``systemctl`` is absent.
    """
    return host.family != "Alpine"


async def main() -> None:
    hosts: list[Host] = []
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        hosts = [Host.from_inventory(entry) for entry in inventory]
        systemd_hosts = [host for host in hosts if is_systemd(host)]
        skipped = [host.name for host in hosts if not is_systemd(host)]
        if skipped:
            print(f"== Skipping non-systemd hosts: {', '.join(skipped)} ==")

        failed = await gather_fact(systemd_hosts, SystemdFailedUnits)
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
        print(f"== {host.name}: systemd.SystemdFailedUnits ==")
        print(json.dumps(failed[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())