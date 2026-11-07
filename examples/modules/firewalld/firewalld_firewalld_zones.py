# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``firewalld.FirewalldZones`` fact on two real hosts.

This is a *usage* example, not part of the library. Where
``examples/modules/firewalld/firewalld_zone.py`` drives reemote5's two-phase
execution model (a deploy generator of operations run through ``run``),
this one reads a single **fact** directly over the same *real SSH
transport* (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key). The hosts must run firewalld (RHEL-family);
   point ``INVENTORY`` at your own machines to target them.
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``FirewalldZones`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's raw records with
   ``json.dumps(..., indent=2)``.

``FirewalldZones`` is a reemote5 original (pyinfra ships no firewalld
module): it parses ``firewall-cmd --list-all-zones`` into a mapping of
zone name to the zone's *complete* configuration -- target, display text,
interfaces, sources, services, ports, protocols, masquerade, forward
ports, source ports, ICMP blocks and the rich rules verbatim (the
tab-indented rule lines real daemons print survive). This is the fact
every zone-membership operation compares against, so a deploy can read
exactly what the operations see.

The ``permanent`` argument is the point of this example: firewalld keeps
two configurations, and the script gathers **both** -- the runtime
(default, what the kernel is enforcing right now, where the ``(active)``
marker is meaningful) and the permanent (``permanent=True``, what the
next reload or reboot will apply). A zone present in only one scope, or
a port open at runtime but missing on disk, is drift; reading the two
mappings side by side is how a deploy spots it before ``firewalld.reload``
either persists or discards it. The reads are guarded
(``2> /dev/null || sudo -n ... || true``): a stopped daemon reads ``{}``.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/firewalld/firewalld_firewalld_zones.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    FirewalldZones,
    Host,
    HostConnectionError,
    gather_fact,
)

# The inventory: real hosts and their SSH connection information, supplied by
# the application to the library. Host.from_inventory materializes each entry
# into a plain Host that connects over asyncssh.
INVENTORY = [
    {
        "host": "web1",
        "connection": {
            "address": "192.168.0.240",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
    {
        "host": "web2",
        "connection": {
            "address": "192.168.0.242",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
]


async def main() -> None:
    # Materialize every inventory entry up front so the fact can be gathered on
    # all hosts at once.
    hosts = [Host.from_inventory(entry) for entry in INVENTORY]
    try:
        # Two fan-outs, one per configuration scope: the runtime listing first
        # (the default), then the permanent listing with permanent=True. Each
        # gather_fact call overlaps the SSH round-trips across hosts; the two
        # calls share the already-open connections.
        runtime = await gather_fact(hosts, FirewalldZones)
        permanent = await gather_fact(hosts, FirewalldZones, permanent=True)
    except (CommandError, HostConnectionError) as exc:
        # The library reports an unreachable host or a failed fact command as a
        # typed error whose message already names the host. Print that message
        # -- no traceback -- and exit non-zero so the failure stays
        # script-visible. (The guarded read itself never raises for a stopped
        # daemon; it reads {}.)
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: firewalld.FirewalldZones (runtime) ==")
        print(json.dumps(runtime[host.name], indent=2))
        print(f"== {host.name}: firewalld.FirewalldZones (permanent) ==")
        print(json.dumps(permanent[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())