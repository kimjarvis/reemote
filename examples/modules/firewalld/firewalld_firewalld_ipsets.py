# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``firewalld.FirewalldIpSets`` fact on two real hosts.

This is a *usage* example, not part of the library. Where
``examples/modules/firewalld/firewalld_ipset.py`` drives reemote5's
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
3. Gather ``FirewalldIpSets`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's raw records with
   ``json.dumps(..., indent=2)``.

``FirewalldIpSets`` is a reemote5 original (pyinfra ships no firewalld
module): one shell round-trip enumerates ``firewall-cmd --get-ipsets``
and runs ``--info-ipset`` for every name behind echo markers, keeping
each set's ``type`` (e.g. hash:ip or hash:net), its ``options`` as a
key/value mapping (family, maxelem, timeout, ...) and its member
``entries``. This is the visibility half of dynamic block/allow lists:
a zone rule referencing ``ipset:blocked`` (via ``firewalld.source``) is
only as good as the set behind it, and this fact shows exactly which
addresses that name currently resolves to.

Like the zones fact, ``FirewalldIpSets`` takes a ``permanent`` argument
because firewalld keeps two configurations: this script reads the
**permanent** scope (``permanent=True``), matching what
``firewalld.ipset`` manages by default -- the on-disk definition and
entries that survive a reboot. Drop the argument to read the runtime
scope instead; entries added at runtime only (e.g. by an intrusion
responder) show up there until the next reload. The read is guarded
(``2> /dev/null || sudo -n ... || true``): a stopped daemon reads ``{}``.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/firewalld/firewalld_firewalld_ipsets.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    FirewalldIpSets,
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
        # fans FirewalldIpSets.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. permanent=True selects the on-disk
        # configuration -- the scope firewalld.ipset manages by default.
        ipsets = await gather_fact(hosts, FirewalldIpSets, permanent=True)
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
        print(f"== {host.name}: firewalld.FirewalldIpSets (permanent) ==")
        print(json.dumps(ipsets[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())