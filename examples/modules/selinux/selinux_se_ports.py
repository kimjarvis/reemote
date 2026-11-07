# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``selinux.SEPorts`` fact, with and without filters.

This is a *usage* example, not part of the library. Where
``examples/modules/selinux/selinux_port.py`` drives reemote5's two-phase
execution model (a deploy generator of operations run through ``run``),
this one reads a single **fact** directly over the same *real SSH
transport* (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key). The hosts must run SELinux with
   ``policycoreutils-python-utils`` (semanage) installed; point
   ``INVENTORY`` at your own machines to target them.
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``SEPorts`` twice on every host with the driver's ``gather_fact``
   helper -- it fans the fact's ``process`` coroutine out across the
   inventory with ``asyncio.gather`` and returns a host-name-keyed mapping
   -- once unfiltered for the full audit table, once narrowed to
   ``protocol="tcp"`` and ``se_type="http_port_t"`` -- then print both with
   ``json.dumps(..., indent=2)``.

``SEPorts`` ports pyinfra's fact of the same name and extends it with the
two filter arguments the port-audit scenario asked for: ``semanage port
-ln`` lists every defined port label, ranges (``9000-9010``) are expanded
to individual ports, and the fact keeps them as a protocol -> port -> type
map. The ``unreserved_port_t`` catch-all is skipped -- it labels no
specific port, so including it would bury the real answers. Passing
``protocol`` keeps only that protocol's table (empty protocols drop out);
passing ``se_type`` keeps only ports carrying that type label, so "which
tcp ports may httpd bind" is one filtered read instead of a client-side
scan of the whole map. For a *single* port's specific label use the
``SEPort`` fact (``examples/modules/selinux/selinux_se_port.py``).

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/selinux/selinux_se_ports.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    SEPorts,
    gather_fact,
)

# The filters for the second gather: every tcp port labelled http_port_t.
FILTER_PROTOCOL = "tcp"
FILTER_SE_TYPE = "http_port_t"

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
        # First gather: the full protocol -> port -> type table, no filters.
        # gather_fact fans SEPorts.process out with asyncio.gather, so both
        # SSH round-trips overlap instead of running one host at a time.
        ports = await gather_fact(hosts, SEPorts)
        # Second gather: the same fact narrowed by its two optional filter
        # arguments -- only tcp ports carrying the http_port_t label survive.
        http_ports = await gather_fact(
            hosts, SEPorts, FILTER_PROTOCOL, FILTER_SE_TYPE
        )
    except (CommandError, HostConnectionError) as exc:
        # The library reports an unreachable host or a failed fact command as a
        # typed error whose message already names the host (a missing semanage
        # surfaces as CommandNotFoundError, a CommandError subclass). Print
        # that message -- no traceback -- and exit non-zero so the failure
        # stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: selinux.SEPorts (all protocols and types) ==")
        print(json.dumps(ports[host.name], indent=2))
        print(
            f"== {host.name}: selinux.SEPorts("
            f"protocol={FILTER_PROTOCOL!r}, se_type={FILTER_SE_TYPE!r}) =="
        )
        print(json.dumps(http_ports[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())