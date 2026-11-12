# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``files.Link`` fact on real CentOS hosts.

This is a *usage* example, not part of the library. Like
``examples/modules/files/files_directory.py``, it reads a single **fact**
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
4. Gather ``Link`` for ``/etc/localtime`` on the guarded hosts at once with the
   driver's ``gather_fact`` helper -- it fans the fact's ``process``
   coroutine out across the inventory with ``asyncio.gather`` and returns
   a host-name-keyed mapping -- then print each host's metadata with
   ``json.dumps(..., indent=2)``.

``Link`` mirrors the baseline's ``files.Link`` -- its ``File`` subclass with
``type = "link"``: the same ``test -e``/``test -L`` guard and GNU
``stat -c`` / ``ls -ld`` chain, but the path's type flag
must be ``l``. The result is a metadata dict -- ``user``, ``group``,
``mode``, ``size``, ``atime``/``mtime``/``ctime`` as *naive UTC*
``datetime``s -- plus ``link_target``: the target path with the stat/ls
quoting stripped, exactly as the link records it. ``/etc/localtime`` is
the classic timezone symlink, so its ``link_target`` names the zoneinfo
file the host is set to (e.g. ``/usr/share/zoneinfo/Europe/Amsterdam``).
A path that does not exist reads as ``None``; one that exists but is not
a symlink (a regular file, directory, ...) reads as ``False``.
``datetime`` is not JSON-serializable, so the print step renders the
timestamps with ``isoformat()``.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

The path is resolved from ``__file__``, so the example works from any
working directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/files/files_link.py
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    Link,
    gather_fact,
    validate_inventory,
)

# examples/modules/files/files_link.py -> the project root, so the inventory
# is found whatever the current working directory is.
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


def jsonable(stats: dict[str, Any] | bool | None) -> dict[str, Any] | bool | None:
    """Render the fact's answer for JSON: datetimes become ISO 8601 strings.

    The False (non-link) and None (absent) answers are already
    JSON-serializable and pass through untouched.
    """
    if not isinstance(stats, dict):
        return stats
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in stats.items()
    }


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
        # fans Link.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. process runs the stat/ls chain and
        # parses the metadata dict plus link_target (False for a non-link,
        # None if absent).
        stats = await gather_fact(hosts, Link, "/etc/localtime")
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
        print(f"== {host.name}: files.Link(/etc/localtime) ==")
        print(json.dumps(jsonable(stats[host.name]), indent=2))


if __name__ == "__main__":
    asyncio.run(main())