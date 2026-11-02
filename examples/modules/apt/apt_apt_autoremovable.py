"""Example: gather the ``apt.AptAutoremovable`` fact on a real Ubuntu server.

This is a *usage* example, not part of the library. Like
``examples/modules/apt/apt_apt_packages.py``, it reads a single **fact**
directly over the *real SSH transport* (asyncssh) instead of driving the
two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key). The entry below is modeled on
   ``examples/demonstrations/inventory/json_inventory_ubuntu.py`` -- it
   carries the connection information of the same Ubuntu server that
   ``inventory_ubuntu.json`` describes, hard-coded here instead of loaded
   from the file.
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``AptAutoremovable`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a host-name-keyed
   mapping -- then print each host's raw sorted list of removable package
   names with ``json.dumps(..., indent=2)``.

``AptAutoremovable`` runs ``apt-get --just-print autoremove 2>/dev/null``
-- a simulation that never removes anything -- and collects only the
machine-readable ``Remv <name> [<version>]`` rows into a sorted,
de-duplicated name list (the bracketed versions are dropped, and the
progress lines and indented removal summary are never mistaken for
names). ``apt-mark showauto`` is deliberately not used: it lists every
auto-installed package, including ones something still requires, while
the simulation reports exactly the no-longer-needed set. This is the
legacy-dependency cleanup view ``AptPackages`` cannot see: its
``apt list --installed`` rows cannot tell an orphaned dependency from
one still in use. The fact only collects the list -- removal stays an
explicit operator decision, never a fact side effect -- and nothing
autoremovable reads as ``[]``. The fact takes no arguments, so the
default gather needs none.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/apt/apt_apt_autoremovable.py
"""

import asyncio
import json
import sys

from reemote5 import (
    AptAutoremovable,
    CommandError,
    Host,
    HostConnectionError,
    gather_fact,
)

# The inventory: a real Ubuntu server and its SSH connection information,
# supplied by the application to the library (the same connection data as
# the inventory_ubuntu.json entry the JSON-inventory demonstration loads).
# Host.from_inventory materializes each entry into a plain Host that
# connects over asyncssh.
INVENTORY = [
    {
        "host": "ubuntu",
        "connection": {
            "address": "192.168.0.221",
            "username": "kjarvis",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "ubuntu",
        "tags": ["ubuntu_host"],
    },
]


async def main() -> None:
    # Materialize every inventory entry up front so the fact can be gathered on
    # all hosts at once.
    hosts = [Host.from_inventory(entry) for entry in INVENTORY]
    try:
        # Gather the fact across the whole inventory concurrently: gather_fact
        # fans AptAutoremovable.process out with asyncio.gather and returns a
        # host-name-keyed mapping. process simulates the autoremove over SSH
        # and parses the Remv rows into a sorted list of removable names.
        autoremovable = await gather_fact(hosts, AptAutoremovable)
    except (CommandError, HostConnectionError) as exc:
        # The library reports an unreachable host or a failed fact command as a
        # typed error whose message already names the host (a missing apt-get
        # binary surfaces as CommandNotFoundError, a CommandError subclass).
        # Print that message -- no traceback -- and exit non-zero so the
        # failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: apt.AptAutoremovable ==")
        print(json.dumps(autoremovable[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())