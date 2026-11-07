# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``iptables.IptablesRulesOrdered`` fact on two real hosts.

This is a *usage* example, not part of the library. It reads a single
**fact** directly over the *real SSH transport* (asyncssh) instead of
driving the two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``IptablesRulesOrdered`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's numbered rule list
   with ``json.dumps(..., indent=2)``.

Unlike the definition-dict ``IptablesRules`` fact, this one parses the
columnar ``iptables -t filter -L -n -v -x --line-numbers`` listing: each
entry carries the chain, the 1-based ``number`` that ``iptables -I`` and
``-D`` address, and the exact ``packets``/``bytes`` counters (``-x``
defeats the K/M scaling, so "is this DROP rule actually matching?" is
answerable without loss). ``-n`` keeps addresses numeric -- a fact never
blocks on DNS. Pass a table name and/or a chain name to ``gather_fact``
(``gather_fact(hosts, IptablesRulesOrdered, "nat", "PREROUTING")``) to
narrow the listing; the default reads every chain of ``filter``. Reading
the tables needs CAP_NET_ADMIN, so the command retries through
passwordless ``sudo -n``: a root login or a NOPASSWD sudoers rule reads
the full list, any other login reads ``[]``.

Every host's connection is closed with ``aclose`` once the facts are
gathered. ``gather_fact`` and ``process`` are coroutines, so ``main`` is
async and driven by ``asyncio.run``.

Run it with::

    uv run python examples/modules/iptables/iptables_iptables_rules_ordered.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    IptablesRulesOrdered,
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
        # fans IptablesRulesOrdered.process out with asyncio.gather and returns
        # a host-name-keyed mapping. The table argument defaults to "filter"
        # and the chain argument to None (every chain); pass "nat" and/or a
        # chain name here to narrow the listing.
        rules = await gather_fact(hosts, IptablesRulesOrdered)
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
        print(f"== {host.name}: iptables.IptablesRulesOrdered ==")
        print(json.dumps(rules[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())