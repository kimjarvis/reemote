# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``apk.ApkAutoremovable`` fact on real Alpine servers.

This is a *usage* example, not part of the library. Like
``examples/modules/apk/apk_apk_explicit_packages.py``, it reads a single
**fact** directly over the *real SSH transport* (asyncssh) instead of
driving the two-phase execution model -- but the target host is not
hard-coded: the inventory comes from a JSON file, the way
``examples/demonstrations/inventory/json_inventory_alpine.py`` loads it:

1. Read ``inventory_alpine.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with
   ``validate_inventory`` so a malformed file fails with an
   ``InventoryError`` naming the offending entry, *before* any host is
   materialized or connected.
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``ApkAutoremovable`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a host-name-keyed
   mapping -- then print each host's raw sorted list of purgable package
   names with ``json.dumps(..., indent=2)``.

``ApkAutoremovable`` runs ``apk add --purge --simulate 2>/dev/null`` --
apk's own orphan-purge simulation, which never removes anything (the
``2>/dev/null`` suppresses the index-noise warnings an offline simulation
writes to stderr) -- and collects only the machine-readable
``(N/M) Purging <name>-<version>-r<release>`` rows into a sorted,
de-duplicated name list (the version-and-release tail is dropped at the
last ``-<version>-r<release>`` boundary, the same split ``ApkPackages``
uses, so multi-dash names like ``alpine-baselayout-data`` stay intact).
``apk fix --purge`` is deliberately not used: it also reinstalls damaged
packages, so its simulation output mixes a repair plan with the purge
plan, while ``apk add --purge --simulate`` names exactly the orphan set.
This is the cleanup view ``ApkExplicitPackages`` cannot see on its own:
the world names it parses cannot tell a legacy dependency from one still
in use, so the installed names missing from the world are candidates a
cleanup would consider removing -- this fact names exactly those
``apk add --purge`` would purge. The fact only collects the list --
removal stays an explicit operator decision, never a fact side effect --
and nothing purgable reads as ``[]``. The fact takes no arguments, so the
default gather needs none.

The path is resolved from ``__file__``, so the example works from any
working directory. Point ``INVENTORY_PATH`` at your own file (or edit
``inventory_alpine.json``) to target your machines.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/apk/apk_apk_autoremovable.py
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from reemote5 import (
    ApkAutoremovable,
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    gather_fact,
    validate_inventory,
)

# examples/modules/apk/apk_apk_autoremovable.py -> the project root, so the
# inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory_alpine.json"


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


async def main() -> None:
    hosts: list[Host] = []
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Materialize every inventory entry up front so the fact can be gathered
        # on all hosts at once.
        hosts = [Host.from_inventory(entry) for entry in inventory]
        # Gather the fact across the whole inventory concurrently: gather_fact
        # fans ApkAutoremovable.process out with asyncio.gather and returns a
        # host-name-keyed mapping. process simulates the purge over SSH and
        # parses the Purging rows into a sorted list of orphaned names.
        apk_autoremovable = await gather_fact(hosts, ApkAutoremovable)
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
    except (CommandError, HostConnectionError) as exc:
        # The library reports an unreachable host or a failed fact command as a
        # typed error whose message already names the host (a missing apk
        # binary surfaces as CommandNotFoundError, a CommandError subclass).
        # The simulation itself never removes anything and needs no root, so
        # a CommandError here means the connection broke. Print that message --
        # no traceback -- and exit non-zero so the failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: apk.ApkAutoremovable ==")
        print(json.dumps(apk_autoremovable[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
