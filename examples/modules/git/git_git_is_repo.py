# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``git.GitIsRepo`` fact on two real hosts.

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
3. Gather ``GitIsRepo`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather``, forwarding the repository
   path argument verbatim, and returns a host-name-keyed mapping -- then
   print each host's is-a-repository answer.

``GitIsRepo`` is a reemote5-original fact -- pyinfra has no equivalent. It
runs ``! test -d <repo> || (cd <repo> && git rev-parse --is-inside-work-tree
2>/dev/null)`` and answers ``True`` only when git prints ``true``, so it
validates a work tree precisely instead of trusting the existence of a
``.git`` directory (which a corrupt or half-cloned repository also has). A
bare repository, a missing directory or a non-repo all read as ``False``,
letting a deployment gate a clone-or-pull on a real check rather than a
directory listing.

Every host's connection is closed with ``aclose`` once the facts are
gathered. ``gather_fact`` and ``process`` are coroutines, so ``main`` is
async and driven by ``asyncio.run``.

Run it with::

    uv run python examples/modules/git/git_git_is_repo.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    GitIsRepo,
    Host,
    HostConnectionError,
    gather_fact,
)

# The repository to inspect on each host: every git fact takes the repo path
# it reads, and the command guards on the directory, so a host without it
# answers False rather than raising.
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
        # gather_fact fans GitIsRepo.process out with asyncio.gather --
        # forwarding REPO to every host -- and returns a host-name-keyed
        # mapping, so both SSH round-trips overlap instead of running one
        # host at a time.
        is_repo = await gather_fact(hosts, GitIsRepo, REPO)
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
        print(f"== {host.name}: git.GitIsRepo({REPO}) ==")
        print(json.dumps(is_repo[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
