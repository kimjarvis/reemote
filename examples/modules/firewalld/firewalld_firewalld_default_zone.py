# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``firewalld.FirewalldDefaultZone`` fact on two real hosts.

This is a *usage* example, not part of the library. Where
``examples/modules/firewalld/firewalld_default_zone.py`` drives reemote5's
two-phase execution model (a deploy generator of operations run through
``run``), this one reads a single **fact** directly over the same *real
SSH transport* (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key). The hosts must run firewalld (RHEL-family);
   point ``INVENTORY`` at your own machines to target them.
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``FirewalldDefaultZone`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's zone name.

``FirewalldDefaultZone`` is a reemote5 original (pyinfra ships no
firewalld module): it answers ``firewall-cmd --get-default-zone`` -- the
zone that connections and interfaces land in when nothing binds them
explicitly. Unlike the other firewalld facts there is no ``permanent``
argument: the default zone is a single daemon-wide setting that
``--set-default-zone`` writes to both configurations at once, so runtime
and permanent never disagree. The read is guarded
(``2> /dev/null || sudo -n ... || true``): a stopped daemon or a host
without firewalld prints nothing and the fact reads the empty string,
which is how a deploy tells "no default zone readable" apart from a real
zone name before deciding to set one.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/firewalld/firewalld_firewalld_default_zone.py
"""

import asyncio
import sys

from reemote5 import (
    CommandError,
    FirewalldDefaultZone,
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
        # Gather the fact across the whole inventory concurrently: gather_fact
        # fans FirewalldDefaultZone.process out with asyncio.gather and returns
        # a host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. process runs --get-default-zone and keeps
        # the first non-empty line (a plain string, not a JSON record).
        zones = await gather_fact(hosts, FirewalldDefaultZone)
    except (CommandError, HostConnectionError) as exc:
        # The library reports an unreachable host or a failed fact command as a
        # typed error whose message already names the host. Print that message
        # -- no traceback -- and exit non-zero so the failure stays
        # script-visible. (The guarded read itself never raises for a stopped
        # daemon; it reads "".)
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: firewalld.FirewalldDefaultZone ==")
        print(zones[host.name] or "(no default zone readable)")


if __name__ == "__main__":
    asyncio.run(main())