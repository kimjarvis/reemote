# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``git.GitTrackingBranch`` fact on two real hosts.

This is a *usage* example, not part of the library. Like
``examples/modules/hardware/hardware_block_devices.py``, it reads a single
**fact** directly over the *real SSH transport* (asyncssh) instead of
driving the two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``GitTrackingBranch`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather``, forwarding the repository
   path argument verbatim, and returns a host-name-keyed mapping -- then
   print each host's upstream branch.

``GitTrackingBranch`` mirrors pyinfra's ``git.GitTrackingBranch``: it runs
``! test -d <repo> || (cd <repo> && git status --branch --porcelain)`` and
reads the token after the ``...`` separator in the first header line
(``## <branch>...<upstream>``). A branch without an upstream -- or a
missing repository directory short-circuiting the guard -- reads as
``None``, so the read never fails.

Every host's connection is closed with ``aclose`` once the facts are
gathered. ``gather_fact`` and ``process`` are coroutines, so ``main`` is
async and driven by ``asyncio.run``.

Run it with::

    uv run python examples/modules/git/git_git_tracking_branch.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    GitTrackingBranch,
    Host,
    HostConnectionError,
    gather_fact,
)

# The repository to inspect on each host: the command guards on the directory,
# so a host without it answers None rather than raising.
REPO = "/home/kjarvis/app"

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
        # gather_fact fans GitTrackingBranch.process out with
        # asyncio.gather -- forwarding REPO to every host -- and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead
        # of running one host at a time.
        tracking = await gather_fact(hosts, GitTrackingBranch, REPO)
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
        print(f"== {host.name}: git.GitTrackingBranch({REPO}) ==")
        print(json.dumps(tracking[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
