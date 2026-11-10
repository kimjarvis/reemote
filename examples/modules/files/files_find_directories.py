# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``files.FindDirectories`` fact on real CentOS hosts.

This is a *usage* example, not part of the library. Like
``examples/modules/files/files_find_files.py``, it reads a single **fact**
directly over the *real SSH transport* (asyncssh) instead of driving the
two-phase execution model:

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with ``validate_inventory``
   so a malformed file fails with an ``InventoryError`` naming the
   offending entry, *before* any host is materialized or connected.
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Guard the gather with a ``when`` predicate: ``is_centos`` reads only
   **static** inventory data -- ``host.group`` -- and the fact is gathered
   on the hosts that pass it. ``gather_fact`` itself takes no ``when``
   parameter, so the predicate filters the host list before the fan-out --
   the direct equivalent of a driver-side skip. Hosts outside the
   ``"centos"`` group are named on a
   ``== Skipped (when guard is_centos): ... ==`` line and never connected.
4. Gather ``FindDirectories`` for the immediate subdirectories of the
   DataDog agent's ``conf.d`` (``maxdepth=1`` -- one level, the check
   directories themselves) on the guarded hosts at once with the driver's
   ``gather_fact`` helper, which forwards the filter kwargs to every
   host's ``process`` -- then print each host's path list with
   ``json.dumps(..., indent=2)``.

``FindDirectories`` mirrors the baseline's ``files.FindDirectories`` -- the
find family's ``-type d`` member, sharing the family's mechanics: one
``find <path> -type d`` plus the filters (``fname``/``iname`` name globs,
``maxdepth``, size bounds, ``regex``, ``path_match``, the raw ``args``
escape hatch), always guarded with ``|| true`` so a missing start path (a
host without the agent) reads as ``[]`` rather than a failed read. The
answer is the matching directory paths verbatim in find's traversal
order -- the bare ``list[str]`` agreed exception to the TypedDict
guideline. Note the start path itself matches ``-type d`` and appears in
the unfiltered listing unless ``maxdepth``/name filters exclude it; here
``maxdepth=1`` yields the ``conf.d`` root plus each check directory, and
each check directory's basename is the name of an installed check --
the enumeration the DataDog role's cleanup compares against its tracked
set. The sibling ``FindFiles`` (``files_find_files.py``) lists the
``conf.yaml`` files one level deeper; ``FindLinks``
(``files_find_links.py``) the symlinks.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

The path is resolved from ``__file__``, so the example works from any
working directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/files/files_find_directories.py
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from reemote5 import (
    CommandError,
    FindDirectories,
    Host,
    HostConnectionError,
    InventoryError,
    gather_fact,
    validate_inventory,
)

# The DataDog agent's check-configuration root: each installed check owns
# an immediate subdirectory, so maxdepth=1 enumerates the check names.
CONF_D = "/etc/datadog-agent/conf.d"

# examples/modules/files/files_find_directories.py -> the project root, so
# the inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


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


def is_centos(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"centos"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision.
    """
    return host.group == "centos"


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
        hosts = [host for host in all_hosts if is_centos(host)]
        skipped = [host.name for host in all_hosts if host not in hosts]
        if skipped:
            print("\n== Skipped (when guard is_centos): " + ", ".join(skipped) + " ==")
        # Gather the fact across the guarded hosts concurrently: gather_fact
        # fans FindDirectories.process out with asyncio.gather, forwarding
        # the filter kwargs to every host, and returns a host-name-keyed
        # mapping. process runs the guarded find and returns the path list
        # verbatim ([] when the start path is absent -- no agent installed).
        found = await gather_fact(hosts, FindDirectories, CONF_D, maxdepth=1)
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
        # typed error whose message already names the host. Print that message
        # -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: files.FindDirectories({CONF_D}, maxdepth=1) ==")
        print(json.dumps(found[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())