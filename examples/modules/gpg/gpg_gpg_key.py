# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``gpg.GpgKey`` fact on two real hosts.

This is a *usage* example, not part of the library. Like
``examples/modules/git/git_git_branch.py``, it reads a single **fact**
directly over the *real SSH transport* (asyncssh) instead of driving the
two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``GpgKey`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather``, forwarding the key-file
   path argument verbatim, and returns a host-name-keyed mapping -- then
   print each host's key entries as JSON.

``GpgKey`` mirrors pyinfra's ``gpg.GpgKey``: it runs
``gpg --with-colons <src>`` against a key file already on the target host
(ASCII-armored or binary) and parses the colon records into ``GpgKeyInfo``
entries -- id, validity, length, plus any fingerprint, user id and
subkeys the records attach -- without importing the key. pyinfra's URL
download branch is deliberately dropped: ``src`` must be a path on the
target host, and a host without ``SRC`` simply answers ``[]`` (gpg exits
2 with empty stdout) instead of failing the read.

Every host's connection is closed with ``aclose`` once the facts are
gathered. ``gather_fact`` and ``process`` are coroutines, so ``main`` is
async and driven by ``asyncio.run``.

Run it with::

    uv run python examples/modules/gpg/gpg_gpg_key.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    GpgKey,
    Host,
    HostConnectionError,
    gather_fact,
)

# The key file to inspect on each host: a path on the TARGET host, read
# without importing. A missing or unreadable file answers [] rather than
# raising; a URL would raise ValueError (reemote5 never downloads keys
# mid-gather).
SRC = "/etc/apt/keyrings/docker.gpg"

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
    # Materialize every inventory entry up front so the fact can be gathered
    # on all hosts at once.
    hosts = [Host.from_inventory(entry) for entry in INVENTORY]
    try:
        # Gather the fact across the whole inventory concurrently:
        # gather_fact fans GpgKey.process out with asyncio.gather --
        # forwarding SRC to every host -- and returns a host-name-keyed
        # mapping, so both SSH round-trips overlap instead of running one
        # host at a time.
        keys = await gather_fact(hosts, GpgKey, SRC)
    except (CommandError, HostConnectionError) as exc:
        # The library reports an unreachable host or a failed fact command
        # as a typed error whose message already names the host. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: gpg.GpgKey({SRC}) ==")
        print(json.dumps(keys[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())