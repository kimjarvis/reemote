"""Example: gather the ``dnf.DnfDisabledModules`` fact on two real hosts.

This is a *usage* example, not part of the library. Like
``examples/dnf/dnf_enabled_modules.py``, it reads a single **fact** directly
over the *real SSH transport* (asyncssh) instead of driving the two-phase
execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key). There is no ``session`` information here.
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``DnfDisabledModules`` on each host by awaiting its ``process``
   coroutine -- the same entry point the driver's ``FactCache`` uses -- and
   print the raw sorted list of disabled module names with
   ``json.dumps(..., indent=2)``.

Each host's connection is closed with ``aclose`` once its fact is gathered.
``process`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.

Run it with::

    uv run python examples/dnf/dnf_disabled_modules.py
"""

import asyncio
import json

from reemote5 import DnfDisabledModules, Host

# The inventory: real hosts and their SSH connection information, supplied by
# the application to the library. Host.from_inventory materializes each entry
# into a plain Host that connects over asyncssh. There is no session
# information for these hosts.
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
    for entry in INVENTORY:
        host = Host.from_inventory(entry)
        try:
            # Gather the fact over SSH: DnfDisabledModules.process runs
            # ``dnf module list --disabled`` and parses the [x]-flagged rows
            # into a sorted, de-duplicated list of disabled module names.
            disabled_modules = await DnfDisabledModules.process(host)
        finally:
            await host.aclose()
        print(f"== {host.name}: dnf.DnfDisabledModules ==")
        print(json.dumps(disabled_modules, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
