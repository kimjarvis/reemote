# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``firewalld.FirewalldServices`` fact on real RedHat hosts.

This is a *usage* example, not part of the library. Where
``examples/modules/firewalld/firewalld_service.py`` drives reemote's
two-phase execution model (a deploy generator of operations run through
``run``), this one reads a single **fact** directly over the same *real
SSH transport* (asyncssh):

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
   the direct equivalent of a driver-side skip. Hosts outside the
   ``\"RedHat\"`` family are named on a
   ``== Skipped (when guard is_redhat): ... ==`` line and never connected.
4. Gather ``FirewalldServices`` on the guarded hosts at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's raw records with
   ``json.dumps(..., indent=2)``.

``FirewalldServices`` is a reemote original (the baseline ships no firewalld
module): one shell round-trip enumerates ``firewall-cmd --get-services``
and runs ``--info-service`` for every name behind echo markers, keeping
each definition's display text, ports, source ports, protocols, helper
modules and destination addresses. Built-in definitions (http, ssh,
dhcpv6-client, ...) and custom ones created with ``firewalld.service``
appear alike -- the fact is how a deploy checks that a custom bundle
exists with the right ports before attaching it to a zone by name with
``firewalld.zone_service``. The ``destination`` field maps address
families to addresses (``{"ipv4": "192.168.1.0/24", "ipv6": "::1"}``);
the parser splits each token on its first colon so an IPv6 address
survives intact.

Service definitions live in the **permanent** configuration only --
firewalld has no runtime service-definition store -- so the fact has no
``permanent`` argument; the runtime sees a definition after a reload.
The read is two-tier (the module's privilege note): a passwordless
``sudo -n`` retry, then an elevated re-read when the login is denied, so a
refusal no longer reads as the empty ``{}``; a stopped daemon still reads
``{}`` (real state, not a refusal).

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

The inventory is read from ``inventory.json`` in the current working directory; edit it
there to target your machines.

Run it with::

    uv run python examples/modules/firewalld/firewalld_firewalld_services.py
"""

import asyncio
import json
import sys
from pathlib import Path

from reemote import (
    CommandError,
    FirewalldServices,
    Host,
    HostConnectionError,
    InventoryError,
    PrivilegeError,
    gather_fact,
    validate_inventory,
)


def is_redhat(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"RedHat"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision.
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
        # fans FirewalldServices.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. process runs the marker-separated
        # --info-service loop and keeps one record per definition.
        services = await gather_fact(hosts, FirewalldServices)
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
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library reports an unreachable host or a failed fact command as a
        # typed error whose message already names the host. Print that message
        # -- no traceback -- and exit non-zero so the failure stays
        # script-visible. (A stopped daemon reads {}; only a refusal the
        # elevation cannot satisfy raises PrivilegeError.)
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: firewalld.FirewalldServices ==")
        print(json.dumps(services[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())