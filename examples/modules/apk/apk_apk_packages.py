"""Example: gather the ``apk.ApkPackages`` fact on a real Alpine server.

This is a *usage* example, not part of the library. Like
``examples/modules/apt/apt_apt_packages.py``, it reads a single **fact**
directly over the *real SSH transport* (asyncssh) instead of driving the
two-phase execution model -- but the target host is not hard-coded: the
inventory comes from a JSON file, the way
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
3. Gather ``ApkPackages`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a host-name-keyed
   mapping -- then print each host's raw mapping of package name to sorted
   version-and-release list with ``json.dumps(..., indent=2)``.

``ApkPackages`` runs ``apk list --installed`` and matches each
``name-version arch {origin} (license) [installed]`` row with pyinfra's
apk regex: multi-dash names like ``alpine-baselayout-data`` split from
their ``3.6.5-r0``-style versions at the last ``-r<release>`` boundary,
names carrying punctuation (``libstdc++``, ``p11-kit``) stay intact, and
``WARNING: Ignoring APKINDEX...`` noise lines are ignored. Versions are
de-duplicated and sorted per name. The fact takes no arguments, so the
default gather needs none.

The path is resolved from ``__file__``, so the example works from any
working directory. Point ``INVENTORY_PATH`` at your own file (or edit
``inventory_alpine.json``) to target your machines.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/apk/apk_apk_packages.py
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from reemote5 import (
    ApkPackages,
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    gather_fact,
    validate_inventory,
)

# examples/modules/apk/apk_apk_packages.py -> the project root, so the
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
        # fans ApkPackages.process out with asyncio.gather and returns a
        # host-name-keyed mapping. process runs apk list --installed over SSH
        # and parses the rows into a {package name: sorted version list}
        # mapping.
        apk_packages = await gather_fact(hosts, ApkPackages)
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
        # Print that message -- no traceback -- and exit non-zero so the
        # failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: apk.ApkPackages ==")
        print(json.dumps(apk_packages[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
