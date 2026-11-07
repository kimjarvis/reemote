# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``selinux.Modules`` fact on two real hosts.

This is a *usage* example, not part of the library. Where
``examples/modules/selinux/selinux_module.py`` drives reemote5's two-phase
execution model (a deploy generator of operations run through ``run``),
this one reads a single **fact** directly over the same *real SSH
transport* (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key). The hosts must run SELinux (RHEL-family);
   point ``INVENTORY`` at your own machines to target them.
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``Modules`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a host-name-keyed
   mapping -- then print each host's raw list with
   ``json.dumps(..., indent=2)``.

``Modules`` is a reemote5 original: pyinfra's selinux namespace ships no
module fact, so custom-policy management has no visibility. ``semodule
-lfull`` lists *every* module instance in the store as a ``priority name
format [disabled]`` row -- unlike ``semodule -l``, which hides disabled
instances and lower-priority duplicates -- and the fact keeps them as a
list of ``name``/``priority``/``format``/``enabled`` entries in listing
order. A name may appear at several priorities (higher wins when names
collide), which is why the fact returns instances rather than a
name-keyed dict. Deliberate deviation from the original proposal's
name-and-version shape: modern semodule no longer prints module versions;
priority, format and enabled state are the modern equivalents and exactly
what the ``selinux.module`` operation's state machine compares. A host
without semodule exits 127 and surfaces as ``CommandNotFoundError`` (a
``CommandError`` subclass).

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/selinux/selinux_modules.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    Modules,
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
        # fans Modules.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. process lists every module instance with
        # its priority, format and enabled state.
        modules = await gather_fact(hosts, Modules)
    except (CommandError, HostConnectionError) as exc:
        # The library reports an unreachable host or a failed fact command as a
        # typed error whose message already names the host (a missing semodule
        # surfaces as CommandNotFoundError, a CommandError subclass). Print
        # that message -- no traceback -- and exit non-zero so the failure
        # stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: selinux.Modules ==")
        print(json.dumps(modules[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())