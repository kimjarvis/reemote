# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``iptables.Ip6tablesRulesOrdered`` fact on two real hosts.

This is a *usage* example, not part of the library. It reads a single
**fact** directly over the *real SSH transport* (asyncssh) instead of
driving the two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``Ip6tablesRulesOrdered`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's numbered rule list
   with ``json.dumps(..., indent=2)``.

The fact is the IPv6 sibling of ``IptablesRulesOrdered``: it parses the
columnar ``ip6tables -t filter -L -n -v -x --line-numbers`` listing with
the same entry shape -- chain, 1-based ``number``, exact ``packets``/
``bytes`` counters, target, protocol, interfaces, addresses (IPv6 CIDRs
here) and the free-form ``options`` tail. Pass a table name and/or a
chain name to ``gather_fact`` to narrow the listing; the default reads
every chain of ``filter``. Reading the tables needs CAP_NET_ADMIN, so
the command retries through passwordless ``sudo -n``: a root login or a
NOPASSWD sudoers rule reads the full list, any other login reads ``[]``.

Every host's connection is closed with ``aclose`` once the facts are
gathered. ``gather_fact`` and ``process`` are coroutines, so ``main`` is
async and driven by ``asyncio.run``.

Run it with::

    uv run python examples/modules/iptables/iptables_ip6tables_rules_ordered.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    Ip6tablesRulesOrdered,
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
        # fans Ip6tablesRulesOrdered.process out with asyncio.gather and returns
        # a host-name-keyed mapping. The table argument defaults to "filter"
        # and the chain argument to None (every chain); pass "nat" and/or a
        # chain name here to narrow the listing.
        rules = await gather_fact(hosts, Ip6tablesRulesOrdered)
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
        print(f"== {host.name}: iptables.Ip6tablesRulesOrdered ==")
        print(json.dumps(rules[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())