# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``server.Processes`` fact on two real hosts.

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
3. Gather ``Processes`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's process dict with
   ``json.dumps(..., indent=2)``.

``Processes`` mirrors pyinfra's ``server.Processes``: it first reads the
``Kernel`` fact and probes for BusyBox to pick its shell -- Linux lists
``ps -eo pid,user,stat,%cpu,%mem,comm,args --no-headers``, FreeBSD the
same ``ps -eo`` without ``--no-headers``, BusyBox (Alpine) the limited
``ps -o pid,user,vsz,stat,args`` columns, and Darwin runs ``ps`` twice
(``args`` last, then ``comm`` last, joined by a marker line) because
macOS truncates any non-last text column -- and returns a dict of PID ->
``{user, state, cpu_percent, mem_percent, command, args}`` dict. An
optional ``pid`` argument restricts the listing to a single process;
here the whole table is gathered, so the output shows every running
process keyed by its PID (rendered as a string by ``json.dumps``).

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/server/server_processes.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    Processes,
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
        # fans Processes.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. process reads the Kernel fact and the
        # BusyBox probe to pick the shell, then parses the ps listing into
        # PID -> info dicts.
        processes = await gather_fact(hosts, Processes)
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
        print(f"== {host.name}: server.Processes ==")
        print(json.dumps(processes[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())