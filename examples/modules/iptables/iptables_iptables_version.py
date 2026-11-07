# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``iptables.IptablesVersion`` fact on two real hosts.

This is a *usage* example, not part of the library. It reads a single
**fact** directly over the *real SSH transport* (asyncssh) instead of
driving the two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``IptablesVersion`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's version info
   with ``json.dumps(..., indent=2)``.

The fact parses ``iptables --version`` (for example
``iptables v1.8.9 (nf_tables)``) into ``{"version": ..., "backend": ...}``.
The backend matters: ``nf_tables`` means the iptables-nft translation
layer, ``legacy`` the classic kernel tables, and the two behave
differently for some match extensions -- worth knowing before debugging
a rule that "should" work. A banner without the parenthesized suffix
reads as ``legacy``; unparseable output reads ``None``. Unlike the
table-reading facts this command needs no privilege and is *not*
exit-masked: a host without the binary raises ``CommandNotFoundError``
(a ``CommandError`` subclass), which the guard below catches and reports.

Every host's connection is closed with ``aclose`` once the facts are
gathered. ``gather_fact`` and ``process`` are coroutines, so ``main`` is
async and driven by ``asyncio.run``.

Run it with::

    uv run python examples/modules/iptables/iptables_iptables_version.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    IptablesVersion,
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
        # fans IptablesVersion.process out with asyncio.gather and returns a
        # host-name-keyed mapping. The fact takes no arguments -- it always
        # reads the binary's --version banner.
        versions = await gather_fact(hosts, IptablesVersion)
    except (CommandError, HostConnectionError) as exc:
        # The library reports an unreachable host or a failed fact command as a
        # typed error whose message already names the host. CommandError also
        # covers CommandNotFoundError: a host without iptables installed fails
        # here with exit 127 rather than returning None. Print that message
        # -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: iptables.IptablesVersion ==")
        print(json.dumps(versions[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())