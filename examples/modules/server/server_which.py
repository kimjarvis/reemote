# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``server.Which`` fact on two real hosts.

This is a *usage* example, not part of the library. Like
``examples/modules/server/server_arch.py``, it reads a single **fact**
directly over the *real SSH transport* (asyncssh) instead of driving the
two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``Which`` on every host at once with the driver's ``gather_fact``
   helper -- it fans the fact's ``process`` coroutine out across the
   inventory with ``asyncio.gather`` and returns a host-name-keyed mapping
   -- then print each host's answers with ``json.dumps(..., indent=2)``.

``Which`` mirrors pyinfra's ``server.Which``: it runs
``command -v <command> || true`` and returns the path the shell would run
for the name, or ``None`` when the command is unavailable (the ``|| true``
swallows the non-zero exit, so a missing binary is an answer, not an
error). The command name is passed to ``gather_fact`` as a fact argument,
forwarded to ``process`` verbatim; this example looks up ``python3`` (a
path on most servers) and ``httpd`` (``None`` on a host without Apache) to
show both outcomes.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/server/server_which.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    Which,
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
        # fans Which.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. The "python3" argument is forwarded to
        # process, which looks the name up with `command -v python3 || true`.
        pythons = await gather_fact(hosts, Which, "python3")
        # A second lookup for a command that may be absent: an unavailable
        # command prints nothing and reads as None, not an error.
        httpds = await gather_fact(hosts, Which, "httpd")
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
        print(f"== {host.name}: server.Which ==")
        print(
            json.dumps(
                {"python3": pythons[host.name], "httpd": httpds[host.name]}, indent=2
            )
        )


if __name__ == "__main__":
    asyncio.run(main())