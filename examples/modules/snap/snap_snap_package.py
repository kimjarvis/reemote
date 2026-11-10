# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``snap.SnapPackage`` fact for one package on real Ubuntu servers.

This is a *usage* example, not part of the library. Like
``examples/modules/apk/apk_apk_package.py``, it reads a single **fact** directly
over the *real SSH transport* (asyncssh) instead of driving the two-phase
execution model -- but the target host is not hard-coded: the inventory comes
from the shared ``inventory.json`` in the project root:

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with
   ``validate_inventory`` so a malformed file fails with an
   ``InventoryError`` naming the offending entry, *before* any host is
   materialized or connected.
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Guard the gather with a ``when`` predicate, the same style as the deploy
   meta-parameter (see ``examples/demonstrations/
   composed_when_guard.py``): ``is_ubuntu`` reads only **static** inventory
   data -- ``host.group`` -- and the fact is gathered on the hosts that pass
   it. ``gather_fact`` itself takes no ``when`` parameter, so the predicate
   filters the host list before the fan-out -- the direct equivalent of a
   driver-side skip. The shared ``inventory.json`` holds one host in the
   ``"ubuntu"`` group alongside hosts in other groups, so the run prints its
   Ubuntu result plus a ``== Skipped (when guard is_ubuntu): ... ==`` line
   naming every other one.
4. Gather ``SnapPackage`` for the package ``lxd`` on the guarded hosts at once
   with the driver's ``gather_fact`` helper -- it fans the fact's ``process``
   coroutine out across the inventory with ``asyncio.gather``, forwarding the
   package name as the fact's argument, and returns a host-name-keyed mapping
   -- then print each host's raw metadata dict with ``json.dumps(...,
   indent=2)``.

``SnapPackage`` runs ``snap info lxd`` and parses its labelled field rows into
the package's name, publisher, store id, tracked channel and installed version.
An absent package (or a host without ``snap``) prints no field row -- the
command's ``2>/dev/null || true`` swallows the non-zero exit -- so it reports
``None`` outright, the ``apt.DebPackage`` tolerance rather than the baseline's empty
``{}``. The baseline's ``requires_command`` gating on ``snap`` is deliberately
dropped for the same reason. The tracked ``channel`` is narrowed to
``<track>/<risk>`` (``4.0/stable/ubuntu-20.04`` reads as ``"4.0/stable"``) and
the ``version`` taken from the ``installed`` row, exactly as the baseline's capture
groups do; the store id lands on the ``snap_id`` key, the one renames the baseline's
hyphenated ``snap-id`` onto a valid identifier.

The path is resolved from ``__file__``, so the example works from any
working directory. Point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines, and ``PACKAGE`` at the
snap you want to inspect; hosts outside the ``"ubuntu"`` group are skipped by
the ``when`` guard.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/snap/snap_snap_package.py
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
    SnapPackage,
    gather_fact,
    validate_inventory,
)

# examples/modules/snap/snap_snap_package.py -> the project root, so the
# inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The snap whose identity to inspect: name, publisher, store id, tracked
# channel and installed version.
PACKAGE = "lxd"


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


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"ubuntu"".

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision. snap is
    installed by default on Ubuntu, so the gather is guarded to that group.
    """
    return host.group == "ubuntu"


async def main() -> None:
    hosts: list[Host] = []
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Materialize every inventory entry up front, then guard the gather
        # below -- a skipped host never reaches the fan-out at all.
        all_hosts = [Host.from_inventory(entry) for entry in inventory]
        # Apply the ``when`` guard before the fan-out: gather_fact has no
        # ``when`` parameter of its own, so filtering the host list with the
        # predicate is the equivalent of the driver skipping a guarded
        # request on ineligible hosts -- the skipped hosts are never
        # connected, exactly as a skipped request is never materialized.
        hosts = [host for host in all_hosts if is_ubuntu(host)]
        skipped = [host.name for host in all_hosts if host not in hosts]
        if skipped:
            print("\n== Skipped (when guard is_ubuntu): " + ", ".join(skipped) + " ==")
        # Gather the fact across the guarded hosts concurrently: gather_fact
        # fans SnapPackage.process out with asyncio.gather, forwarding PACKAGE
        # as the fact's argument, and returns a host-name-keyed mapping.
        # process runs snap info over SSH and parses the labelled field rows
        # into the package's identity; an absent package reads as None.
        snap_package = await gather_fact(hosts, SnapPackage, PACKAGE)
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
        print(f"== {host.name}: snap.SnapPackage({PACKAGE!r}) ==")
        print(json.dumps(snap_package[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
