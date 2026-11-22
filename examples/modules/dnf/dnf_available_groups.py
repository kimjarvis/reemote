# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``dnf.DnfAvailableGroups`` fact on real RedHat hosts.

This is a *usage* example, not part of the library. Like
``examples/modules/dnf/dnf_groups.py``, it reads a single **fact** directly
over the *real SSH transport* (asyncssh) instead of driving the two-phase
execution model:

1. Read ``inventory.json`` from the current working directory and parse it as JSON -- an
   inventory is just a list of dictionaries, which is exactly what JSON expresses --
   then validate it up front with ``validate_inventory`` so a malformed file fails with
   an ``InventoryError`` naming the offending entry, *before* any host is materialized
   or connected.
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Guard the gather with a ``when`` predicate: ``is_redhat`` reads only
   **static** inventory data -- ``host.family`` -- and the fact is gathered
   on the hosts that pass it. ``gather_fact`` itself takes no ``when``
   parameter, so the predicate filters the host list before the fan-out --
   the direct equivalent of a driver-side skip. The shared
   ``inventory.json`` holds two hosts in the ``"RedHat"`` family alongside
   hosts in other families, so the run prints its RedHat results plus a
   ``== Skipped (when guard is_redhat): ... ==`` line naming every other
   one.
4. Gather ``DnfAvailableGroups`` on the guarded hosts at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a host-name-keyed
   mapping -- then print each host's sorted list of installable group entries
   (each a name) with ``json.dumps(..., indent=2)``.

The inventory is read from ``inventory.json`` in the current working directory (edit it
there to target your machines); hosts outside the ``\"RedHat\"`` family are skipped by
the ``when`` guard.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/dnf/dnf_available_groups.py
"""

import asyncio
import json
import sys
from pathlib import Path

from reemote import (
    CommandError,
    DnfAvailableGroups,
    Host,
    HostConnectionError,
    InventoryError,
    gather_fact,
    validate_inventory,
)


def is_redhat(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"RedHat"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision. ``dnf`` only
    exists on the RedHat hosts, so the guard keeps the gather off the Alpine
    and Ubuntu hosts, where the fact's command would fail.
    """
    return host.family == "RedHat"


async def main() -> None:
    hosts: list[Host] = []
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Materialize every inventory entry up front, then guard the gather
        # below -- a skipped host never reaches the fan-out at all.
        all_hosts = [Host.from_inventory(entry) for entry in inventory]
        # Apply the ``when`` guard before the fan-out: gather_fact has no
        # ``when`` parameter of its own, so filtering the host list with the
        # predicate is the equivalent of the driver skipping a guarded
        # request on ineligible hosts -- the skipped hosts are never
        # connected, exactly as a skipped request is never materialized.
        hosts = [host for host in all_hosts if is_redhat(host)]
        skipped = [host.name for host in all_hosts if host not in hosts]
        if skipped:
            print("\n== Skipped (when guard is_redhat): " + ", ".join(skipped) + " ==")
        # Gather the fact across the guarded hosts concurrently: gather_fact
        # fans DnfAvailableGroups.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. process runs ``dnf group list
        # --available`` and collects the group names indented under the
        # ``Available ... Groups:`` headers into a sorted, de-duplicated list
        # of ``DnfGroup`` entries (each a name) -- handy to check that a group
        # name is valid before installing it.
        available_groups = await gather_fact(hosts, DnfAvailableGroups)
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
    # The fact already returns a sorted list, so the listing is reproducible.
    for host in hosts:
        print(f"== {host.name}: dnf.DnfAvailableGroups ==")
        print(json.dumps(available_groups[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
