# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``apt.AptSources`` fact on a real Ubuntu server.

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
3. Gather ``AptSources`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a host-name-keyed
   mapping -- then print each host's list of repository dicts with
   ``json.dumps(..., indent=2)``.

``AptSources`` cats ``/etc/apt/sources.list``, every
``/etc/apt/sources.list.d/*.list`` (legacy one-line format) and every
``/etc/apt/sources.list.d/*.sources`` (modern deb822 format), each behind
a ``#reemote5-aptsource`` marker line naming the file. Legacy lines and
deb822 stanzas both expand into plain ``AptSource`` TypedDict-shaped
dicts -- ``{type, url, distribution, components, options}`` -- with every
``options`` value a list of strings (``options["arch"]`` is always e.g.
``["amd64"]``, never a bare string), so the entries print as JSON without
any reshaping. Disabled stanzas (``Enabled: no``) are skipped. The fact
takes no arguments, so the default gather needs none.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/apt/apt_apt_sources.py
"""

import asyncio
import json
import sys

from reemote5 import (
    AptSources,
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
        # fans AptSources.process out with asyncio.gather and returns a
        # host-name-keyed mapping. process cats the sources files over SSH
        # and parses them into a list of {type, url, distribution,
        # components, options} repository dicts.
        apt_sources = await gather_fact(hosts, AptSources)
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
        print(f"== {host.name}: apt.AptSources ==")
        print(json.dumps(apt_sources[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())