# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``selinux.SEPorts`` fact, with and without filters.

This is a *usage* example, not part of the library. Where
``examples/modules/selinux/selinux_port.py`` drives reemote's two-phase
execution model (a deploy generator of operations run through ``run``),
this one reads a single **fact** directly over the same *real SSH
transport* (asyncssh):

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
4. Gather ``SEPorts`` twice on every host with the driver's ``gather_fact``
   helper -- it fans the fact's ``process`` coroutine out across the
   inventory with ``asyncio.gather`` and returns a host-name-keyed mapping
   -- once unfiltered for the full audit table, once narrowed to
   ``protocol="tcp"`` and ``se_type="http_port_t"`` -- then print both with
   ``json.dumps(..., indent=2)``.

``SEPorts`` ports the baseline's fact of the same name and extends it with the
two filter arguments the port-audit scenario asked for: ``semanage port
-ln`` lists every defined port label, ranges (``9000-9010``) are expanded
to individual ports, and the fact keeps them as a protocol -> port -> type
map. The ``unreserved_port_t`` catch-all is skipped -- it labels no
specific port, so including it would bury the real answers. Passing
``protocol`` keeps only that protocol's table (empty protocols drop out);
passing ``se_type`` keeps only ports carrying that type label, so "which
tcp ports may httpd bind" is one filtered read instead of a client-side
scan of the whole map. For a *single* port's specific label use the
``SEPort`` fact (``examples/modules/selinux/selinux_se_port.py``).

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

The path is resolved from ``__file__``, so the example works from any
working directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/selinux/selinux_se_ports.py
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    SEPorts,
    gather_fact,
    validate_inventory,
)

# The filters for the second gather: every tcp port labelled http_port_t.
FILTER_PROTOCOL = "tcp"
FILTER_SE_TYPE = "http_port_t"

# examples/modules/selinux/selinux_se_ports.py -> the project root, so the inventory
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
        # First gather: the full protocol -> port -> type table, no filters.
        # gather_fact fans SEPorts.process out with asyncio.gather, so both
        # SSH round-trips overlap instead of running one host at a time.
        ports = await gather_fact(hosts, SEPorts)
        # Second gather: the same fact narrowed by its two optional filter
        # arguments -- only tcp ports carrying the http_port_t label survive.
        http_ports = await gather_fact(
            hosts, SEPorts, FILTER_PROTOCOL, FILTER_SE_TYPE
        )
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
        # typed error whose message already names the host (a missing semanage
        # surfaces as CommandNotFoundError, a CommandError subclass). Print
        # that message -- no traceback -- and exit non-zero so the failure
        # stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: selinux.SEPorts (all protocols and types) ==")
        print(json.dumps(ports[host.name], indent=2))
        print(
            f"== {host.name}: selinux.SEPorts("
            f"protocol={FILTER_PROTOCOL!r}, se_type={FILTER_SE_TYPE!r}) =="
        )
        print(json.dumps(http_ports[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())