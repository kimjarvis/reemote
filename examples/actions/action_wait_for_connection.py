# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: wait for hosts to become reachable with ``WaitForConnection``.

This is a *usage* example, not part of the library. It demonstrates
``WaitForConnection`` -- the action that waits for SSH connections to be
(re-)established, driven through ``gather_action`` exactly like ``Shell``:

1. Specify the target hosts as an *inventory* (with ``group`` and ``tags``
   classifiers) and materialize each entry with ``Host.from_inventory``.
2. ``gather_action(hosts, WaitForConnection, timeout=..., retry=...,
   group=..., tags=...)`` waits for every SELECTED host concurrently:
   ``group`` keeps hosts whose ``host.group`` matches, ``tags`` keeps hosts
   carrying every listed tag. Each host is polled -- a full SSH connect,
   retried every ``retry`` seconds -- until it succeeds or its ``timeout``
   (seconds) is exhausted.
3. The awaited call is the barrier: it returns only once every selected host
   has connected, mapping ``host.name`` to ``None`` (the key's presence is
   the success signal -- no command is run). A host that never comes back
   raises the fatal ``ConnectionTimeoutError``, aggregated across hosts.

The purpose is the reboot sequence between deploys::

    from reemote5 import (
        Host, Shell, WaitForConnection, gather_action, run,
    )

    # Deploy 1: configure the hosts (kernel args, packages, ...).
    plans, report, hosts = await run(INVENTORY, configure)

    # Reboot them with a Shell action -- immediate, outside any deploy.
    await gather_action(hosts, Shell, command="reboot", sudo=True)

    # Wait for every host to accept SSH again before continuing. The wait
    # discards the stale pre-reboot connections and retries until each host
    # is back; a host still down after `timeout` seconds aborts the script
    # with ConnectionTimeoutError rather than letting deploy 2 proceed.
    hosts = [Host.from_inventory(entry) for entry in INVENTORY]
    await gather_action(hosts, WaitForConnection, timeout=300, retry=5)

    # Deploy 2: the hosts are reachable again, so this is safe to start.
    plans, report, hosts = await run(INVENTORY, finish)

This script deliberately does NOT reboot the shared demo hosts. Instead it
runs the wait itself twice: once against the real inventory (the hosts are
up, so it succeeds at the first attempt), and once against a deliberately
unreachable TEST-NET address with a short timeout, to show the fatal
``ConnectionTimeoutError`` being caught and reported -- the same error the
reboot sequence would raise for a host that never came back.

Run it with::

    uv run python examples/actions/action_wait_for_connection.py
"""

import asyncio
import sys

from reemote5 import (
    ConnectionTimeoutError,
    Host,
    HostConnectionError,
    WaitForConnection,
    gather_action,
)

# The inventory: real hosts and their SSH connection information, supplied by
# the application to the library. The group/tags classifiers are what the
# wait's `group=`/`tags=` keywords select on.
INVENTORY = [
    {
        "host": "web1",
        "connection": {
            "address": "192.168.0.240",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
        "group": "webservers",
        "tags": ["web", "ubuntu"],
    },
    {
        "host": "web2",
        "connection": {
            "address": "192.168.0.242",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
        "group": "webservers",
        "tags": ["web", "ubuntu"],
    },
]

# A TEST-NET-1 address (RFC 5737): guaranteed unroutable, so the wait below
# demonstrates the timeout path without touching any real machine.
DEAD_INVENTORY = [
    {
        "host": "dead1",
        "connection": {"address": "192.0.2.1", "known_hosts": None},
    },
]


async def main() -> None:
    hosts = [Host.from_inventory(entry) for entry in INVENTORY]
    try:
        # Wait for the webservers carrying both tags. The hosts are up, so
        # each connects at the first attempt; the await is the barrier --
        # every selected host is reachable before the next line runs, which
        # is exactly the guarantee the next deploy needs after a reboot.
        connected = await gather_action(
            hosts,
            WaitForConnection,
            timeout=60,
            retry=5,
            group="webservers",
            tags=("web", "ubuntu"),
        )
    except (HostConnectionError, ConnectionTimeoutError) as exc:
        # A library-level failure is a typed error naming the host(s). Print
        # it -- no traceback -- and exit non-zero. In the reboot sequence this
        # is the fatal path: a host that never came back aborts the script.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print("== wait for the webservers (all reachable) ==")
    for name in connected:
        print(f"{name}: connected")

    # The timeout path: a host that never becomes reachable. Each attempt is
    # bounded by the remaining timeout, so even a hanging connect cannot
    # outlive the 3-second budget; the retry period spaces the attempts.
    print("\n== wait for an unreachable host (expected timeout) ==")
    dead = [Host.from_inventory(entry) for entry in DEAD_INVENTORY]
    try:
        await gather_action(dead, WaitForConnection, timeout=3, retry=1)
    except ConnectionTimeoutError as exc:
        # The expected outcome of this demonstration, so the script still
        # exits 0. A real script would let this abort the run (SystemExit(1)).
        print(f"fatal, as designed: {exc}")
    else:
        print("ERROR: the unreachable host unexpectedly connected", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
