# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``iptables.Ip6tablesPersistence`` fact on two real hosts.

This is a *usage* example, not part of the library. It reads a single
**fact** directly over the *real SSH transport* (asyncssh) instead of
driving the two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``Ip6tablesPersistence`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's synced/drifted bool
   with ``json.dumps(..., indent=2)``.

The fact is the IPv6 sibling of ``IptablesPersistence``: it compares the
live ``ip6tables-save`` output against the snapshot file (default
``/etc/iptables/rules.v6``; pass another path to ``gather_fact``) with
comment lines stripped on both sides, so the save header's changing
timestamp never causes a false drift. ``True`` means synced; an absent
file, any difference, or an empty live read (a login granted neither
root nor passwordless sudo) reads ``False``. The probe is read-only and
fully exit-masked; a meaningful ``True`` needs a root login or a
NOPASSWD sudoers rule. ``iptables.save`` with ``version=6`` reads this
same fact to skip the write when the snapshot already matches.

Every host's connection is closed with ``aclose`` once the facts are
gathered. ``gather_fact`` and ``process`` are coroutines, so ``main`` is
async and driven by ``asyncio.run``.

Run it with::

    uv run python examples/modules/iptables/iptables_ip6tables_persistence.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    Ip6tablesPersistence,
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
        # fans Ip6tablesPersistence.process out with asyncio.gather and returns
        # a host-name-keyed mapping. The path argument defaults to
        # /etc/iptables/rules.v6; pass another snapshot path here to compare
        # against a different persistence file.
        synced = await gather_fact(hosts, Ip6tablesPersistence)
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
        print(f"== {host.name}: iptables.Ip6tablesPersistence ==")
        print(json.dumps(synced[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())