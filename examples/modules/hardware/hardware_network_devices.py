# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``hardware.NetworkDevices`` fact on two real hosts.

This is a *usage* example, not part of the library. Like
``examples/modules/hardware/hardware_ipv6_addrs.py``, it reads a single
**fact** directly over the *real SSH transport* (asyncssh) instead of driving
the two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``NetworkDevices`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a host-name-keyed
   mapping -- then print each host's device details with
   ``json.dumps(..., indent=2)``.

``NetworkDevices`` mirrors pyinfra's ``hardware.NetworkDevices``: it runs
``ip -j addr show 2> /dev/null || ip addr show 2> /dev/null || ifconfig -a``
and maps each interface to its ``NetworkDevice`` entry -- the state, the
optional ether MAC and mtu, and an ``Ipv4Address``/``Ipv6Address`` block
per address family with the
primary address, its mask/broadcast details and any ``additional_ips``
aliases (the ``Ipv4Addrs``/``Ipv6Addrs`` siblings are the address-only
projections of this dict). An unreachable host or a failed command is a
typed ``CommandError``/``HostConnectionError``, caught below.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/hardware/hardware_network_devices.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    NetworkDevices,
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
        # Gather the fact across the whole inventory concurrently: gather_fact
        # fans NetworkDevices.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time.
        network_devices = await gather_fact(hosts, NetworkDevices)
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
        print(f"== {host.name}: hardware.NetworkDevices ==")
        print(json.dumps(network_devices[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())