# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``server.Port`` fact on two real hosts.

This is a *usage* example, not part of the library. Like
``examples/modules/server/server_mounts.py``, it reads a single **fact**
directly over the *real SSH transport* (asyncssh) instead of driving the
two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``Port`` on every host at once with the driver's ``gather_fact``
   helper -- it fans the fact's ``process`` coroutine out across the
   inventory with ``asyncio.gather`` and returns a host-name-keyed mapping
   -- then print each host's record with ``json.dumps(..., indent=2)``.

``Port`` mirrors pyinfra's ``server.Port``: it reads the ``Kernel`` fact to
pick the tool (FreeBSD lists ``sockstat -l -p <port> -P <protocol>``, every
other kernel prefers Linux ``ss`` and falls back to ``netstat`` when the
``Which`` fact reports no ``ss``), filters the listing down to one port and
returns a ``PortInfo`` record -- ``listening``, ``process`` and ``pid``.
``listening`` is decided by any socket row that survives the filter, while
the owner pair is best-effort: an unprivileged read sees the socket but not
the process behind it, so ``process``/``pid`` come back ``None`` even though
the port is plainly up. That distinction is the reason reemote5 answers with
a record rather than with pyinfra's ``(process, pid)`` tuple, and the reason
``server.wait`` polls ``listening`` alone.

The port and protocol are passed to ``gather_fact`` as fact arguments,
forwarded to ``process`` verbatim; a port outside 1-65535 or an unknown
protocol is rejected with ``ValueError`` before anything is sent. This
example reads port 80 (usually up on a web server), port 53 over ``udp``
(the second protocol family the fact supports) and port 8765 (nothing
listens, so the record answers ``listening=False``) to show all three
outcomes.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/server/server_port.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    Port,
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
        # fans Port.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. The 80 is forwarded to process as the
        # port argument; process reads the Kernel fact to choose ss, netstat
        # or sockstat, then greps that tool's output for the port.
        http = await gather_fact(hosts, Port, 80)
        # A second protocol family: the "udp" argument selects the -lu/-up
        # variant of the listing, and the fact argument order is
        # ``process(host, port, protocol)``.
        dns = await gather_fact(hosts, Port, 53, "udp")
        # A port nothing listens on: the listing matches no row, which reads
        # as listening=False rather than as an error or an empty answer.
        closed = await gather_fact(hosts, Port, 8765)
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
        print(f"== {host.name}: server.Port ==")
        print(
            json.dumps(
                {
                    "tcp/80": http[host.name],
                    "udp/53": dns[host.name],
                    "tcp/8765": closed[host.name],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
