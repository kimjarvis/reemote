"""Example: gather the ``dnf.DnfEnabledModules`` fact on two real hosts.

This is a *usage* example, not part of the library. Like
``examples/dnf/dnf_repositories.py``, it reads a single **fact** directly
over the *real SSH transport* (asyncssh) instead of driving the two-phase
execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key). There is no ``session`` information here.
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``DnfEnabledModules`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a host-name-keyed
   mapping -- then print each host's raw mapping of module name to enabled
   stream with ``json.dumps(..., indent=2)``.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/dnf/dnf_enabled_modules.py
"""

import asyncio
import json

from reemote5 import DnfEnabledModules, Host, gather_fact

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
    # Materialize every inventory entry up front so the fact can be gathered on
    # all hosts at once.
    hosts = [Host.from_inventory(entry) for entry in INVENTORY]
    try:
        # Gather the fact across the whole inventory concurrently: gather_fact
        # fans DnfEnabledModules.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. process runs ``dnf module list --enabled``
        # and parses the [e]-flagged rows into a {module name: stream} mapping.
        enabled_modules = await gather_fact(hosts, DnfEnabledModules)
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: dnf.DnfEnabledModules ==")
        print(json.dumps(enabled_modules[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
