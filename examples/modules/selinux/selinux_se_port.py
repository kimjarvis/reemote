# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``selinux.SEPort`` fact on two real hosts.

This is a *usage* example, not part of the library. Where
``examples/modules/selinux/selinux_port.py`` drives reemote5's two-phase
execution model (a deploy generator of operations run through ``run``),
this one reads a single **fact** directly over the same *real SSH
transport* (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key). The hosts must run SELinux; ``sepolicy``
   comes with ``policycoreutils-dev``, which is not stock -- without it
   the fact tolerantly reads the empty string. Point ``INVENTORY`` at
   your own machines to target them.
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``SEPort`` on every host at once with the driver's ``gather_fact``
   helper -- it fans the fact's ``process`` coroutine out across the
   inventory with ``asyncio.gather`` and returns a host-name-keyed mapping
   -- then print each host's raw type string with
   ``json.dumps(..., indent=2)``.

``SEPort`` ports pyinfra's fact of the same name: ``sepolicy network -p
<port>`` prints the specific label row first and the generic range row
second (``8080: tcp http_port_t 8080`` then ``8080: tcp
unreserved_port_t 1024-...``), and the fact keeps the type of the first
row only when *both* rows are present -- a port covered by a generic range
alone reads as the empty string, pyinfra's gate, because "inside a range"
is not a specific label. Here the question is whether tcp/8080 carries an
explicit type (the port the ``selinux.port`` operation example labels).
The ``|| true`` inside the command swallows sepolicy's own failure, so a
host without the tool reads "" rather than raising. For the *whole* port
table in one read use the ``SEPorts`` fact
(``examples/modules/selinux/selinux_se_ports.py``).

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/selinux/selinux_se_port.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    SEPort,
    gather_fact,
)

# The protocol/port pair whose specific label to read.
PORT_PROTOCOL = "tcp"
PORT_NUMBER = 8080

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
        # fans SEPort.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. process queries sepolicy for the one
        # port and keeps the specific type, or "" when none is defined.
        types = await gather_fact(hosts, SEPort, PORT_PROTOCOL, PORT_NUMBER)
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
        print(
            f"== {host.name}: selinux.SEPort("
            f"{PORT_PROTOCOL!r}, {PORT_NUMBER}) =="
        )
        print(json.dumps(types[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())