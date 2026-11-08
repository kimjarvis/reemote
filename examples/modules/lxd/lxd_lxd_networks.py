# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``lxd.LxdNetworks`` fact on two real hosts.

This is a *usage* example, not part of the library. It reads a single
**fact** directly over the *real SSH transport* (asyncssh) instead of
driving the two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``LxdNetworks`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's list of network
   entries with ``json.dumps(..., indent=2)``.

The fact runs ``lxc network list --format json --project default`` as
the login user, who needs access to the LXD daemon socket (member of
the ``lxd`` group). The listing covers MANAGED networks (bridges, ovn,
macvlan, with their config) and UNMANAGED ones (host NICs LXD merely
knows about, ``managed=False``) -- the view the ``lxd.network``
operation reconciles against, refusing to adopt or delete an unmanaged
interface. Pass ``project`` (or ``remote``) to ``gather_fact`` to read
another project's or remote's networks.

Every host's connection is closed with ``aclose`` once the facts are
gathered. ``gather_fact`` and ``process`` are coroutines, so ``main`` is
async and driven by ``asyncio.run``.

**Host prerequisite.** This example needs the ``lxc`` CLI and a running,
initialized LXD daemon (login user in the ``lxd`` group for fact reads;
operations ride ``sudo=True``). The demo CentOS hosts have neither: LXD is
snap-only on EL9 and their ``staff_t``-confined sudo cannot create ``/snap``
or reach ``/run/snapd.socket``, so it cannot be installed. Compile-checked
and exercised by the offline harness, not run against shared hosts; point
``INVENTORY`` at LXD-enabled machines (or relax the demo hosts) to run live.

Run it with::

    uv run python examples/modules/lxd/lxd_lxd_networks.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    LxdNetworks,
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
        # fans LxdNetworks.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. process runs lxc network list and parses
        # the entries (name, type, managed flag, config).
        networks = await gather_fact(hosts, LxdNetworks)
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
        print(f"== {host.name}: lxd.LxdNetworks ==")
        print(json.dumps(networks[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())