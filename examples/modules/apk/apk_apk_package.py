# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``apk.ApkPackage`` fact for one package on real Alpine servers.

This is a *usage* example, not part of the library. Like
``examples/modules/apk/apk_apk_explicit_packages.py``, it reads a single **fact**
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
3. Gather ``ApkPackage`` for the package ``curl`` on every host at once with
   the driver's ``gather_fact`` helper -- it fans the fact's ``process``
   coroutine out across the inventory with ``asyncio.gather``, forwarding the
   package name as the fact's argument, and returns a host-name-keyed mapping
   -- then print each host's raw metadata dict with ``json.dumps(..., indent=2)``.

``ApkPackage`` runs ``apk info -a curl`` and parses its labelled field
sections into the package's version-and-release, one-line description,
upstream webpage, license, installed size and its ``depends on``/``provides``
lists. An absent package (or a host without apk) prints no field header -- the
command's ``2>/dev/null || true`` swallows the non-zero exit -- so it reports
``None`` outright, the ``apt.DebPackage`` tolerance. This is the detail view
``ApkPackages`` cannot give: its ``apk list --installed`` rows report only
whether a name is installed and at which version, never its dependencies, size
or description, so a deploy validating a specific package's version or
dependency set reads it here. One deliberate gap against the sibling
``apt.AptPolicy``: apk's ``apk info`` prints no repository field, so the
package's own metadata is all this fact reports -- the ``{origin}`` source
package of every installed name is what ``ApkPackages`` parses and the
configured repositories are what ``ApkRepositories`` reports.

The path is resolved from ``__file__``, so the example works from any
working directory. Point ``INVENTORY_PATH`` at your own file (or edit
``inventory_alpine.json``) to target your machines, and ``PACKAGE`` at the
package you want to inspect.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/apk/apk_apk_package.py
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from reemote5 import (
    ApkPackage,
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    gather_fact,
    validate_inventory,
)

# examples/modules/apk/apk_apk_package.py -> the project root, so the
# inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory_alpine.json"

# The package whose detail to inspect: version, description, size, license and
# its dependency set.
PACKAGE = "curl"


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
        # fans ApkPackage.process out with asyncio.gather, forwarding PACKAGE as
        # the fact's argument, and returns a host-name-keyed mapping. process
        # runs apk info -a over SSH and parses the labelled field sections into
        # the package's metadata; an absent package reads as None.
        apk_package = await gather_fact(hosts, ApkPackage, PACKAGE)
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
        # typed error whose message already names the host. The info gather
        # itself tolerates an absent package (the command ends in ``|| true``),
        # so a CommandError here means the connection broke. Print that message
        # -- no traceback -- and exit non-zero so the failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: apk.ApkPackage({PACKAGE!r}) ==")
        print(json.dumps(apk_package[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
