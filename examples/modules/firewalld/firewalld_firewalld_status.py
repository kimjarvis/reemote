# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``firewalld.FirewalldStatus`` fact on two real hosts.

This is a *usage* example, not part of the library. Where
``examples/modules/firewalld/firewalld_zone.py`` drives reemote5's two-phase
execution model (a deploy generator of operations run through ``run``),
this one reads a single **fact** directly over the same *real SSH
transport* (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key). The hosts must run firewalld (RHEL-family);
   point ``INVENTORY`` at your own machines to target them.
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``FirewalldStatus`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's raw record with
   ``json.dumps(..., indent=2)``.

``FirewalldStatus`` is a reemote5 original: pyinfra ships no firewalld
module at all, so every firewalld deploy starts with a manual
``firewall-cmd --state``. The fact answers both halves of the daemon
question in one shell round-trip (``--state`` and ``--version`` behind
echo markers) and keeps two fields -- ``running`` (whether the daemon
answers) and ``version`` (the daemon's version string, e.g. "2.1.1").
The read is guarded (``2> /dev/null || sudo -n ... || true``), so a
stopped daemon or a host without firewalld reads ``running=False`` with
an empty ``version`` instead of raising: the fact is how a deploy tells
a firewalld host apart from a bare one before deciding to manage it.
The ``version`` field is also the capability probe -- policies, for
example, only exist on firewalld 0.9 and newer.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/firewalld/firewalld_firewalld_status.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    FirewalldStatus,
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
        # fans FirewalldStatus.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. process runs the marked state/version
        # shell and keeps the running flag and the daemon version.
        statuses = await gather_fact(hosts, FirewalldStatus)
    except (CommandError, HostConnectionError) as exc:
        # The library reports an unreachable host or a failed fact command as a
        # typed error whose message already names the host. Print that message
        # -- no traceback -- and exit non-zero so the failure stays
        # script-visible. (The guarded read itself never raises for a stopped
        # daemon; it reads running=False.)
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: firewalld.FirewalldStatus ==")
        print(json.dumps(statuses[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())