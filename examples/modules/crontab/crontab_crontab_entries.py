# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``crontab.CrontabEntries`` fact on real hosts.

This is a *usage* example, not part of the library. Like
``examples/modules/dnf/dnf_enabled_modules.py``, it reads a single **fact**
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
3. Gather ``CrontabEntries`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a host-name-keyed
   mapping -- then print each host's parsed entries with
   ``json.dumps(..., indent=2)``. No ``when`` guard filters the host list:
   every target speaks ``crontab``, and the guarded read
   (``crontab -l 2>/dev/null || true``, like the raw-text ``Crontab`` fact it
   reads through) simply yields ``[]`` for a user with no table, mirroring
   the operation example, which also runs unguarded on all hosts.

``CrontabEntries`` is the structured mirror of pyinfra's ``crontab.Crontab``
fact: one ``CrontabEntry`` dict per table line -- a schedule line with its
five fields (digits int-normalized, so ``05`` reads as ``5``), an
``@nickname`` line under ``special_time``, a ``NAME=value`` line under ``env``
(the shape that crashed pyinfra's old parser before issue #979), and the
comment lines immediately preceding an entry in ``comments``. It reads the
whole table through the ``Crontab`` raw-text fact, so one ``crontab -l``
answers both questions and the environment/comment lines need no special
transport. Which user's table is read follows the connection's login, not a
fact parameter (pyinfra's ``user=`` is deliberately dropped, as in the
operation).

The path is resolved from ``__file__``, so the example works from any
working directory. Point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/crontab/crontab_crontab_entries.py
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    CrontabEntries,
    Host,
    HostConnectionError,
    InventoryError,
    gather_fact,
    validate_inventory,
)

# examples/modules/crontab/crontab_crontab_entries.py -> the project root, so
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


async def main() -> None:
    hosts: list[Host] = []
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Materialize every inventory entry up front; the read is unguarded
        # because the guarded command degrades to an empty table everywhere.
        hosts = [Host.from_inventory(entry) for entry in inventory]
        # Gather the fact across all hosts concurrently: gather_fact fans
        # CrontabEntries.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so the SSH round-trips overlap instead of
        # running one host at a time. process reads the whole table through
        # the Crontab fact (``crontab -l 2>/dev/null || true``) and parses
        # each line into one CrontabEntry dict.
        crontabs = await gather_fact(hosts, CrontabEntries)
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
        print(f"== {host.name}: crontab.CrontabEntries ==")
        print(json.dumps(crontabs[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
