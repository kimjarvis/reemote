# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``firewalld.FirewalldPolicies`` fact on two real hosts.

This is a *usage* example, not part of the library. It reads a single
**fact** directly over the *real SSH transport* (asyncssh); the
operation examples in this directory (e.g.
``examples/modules/firewalld/firewalld_zone.py``) drive the two-phase
execution model instead:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key). The hosts must run firewalld 0.9 or newer
   (RHEL-family); point ``INVENTORY`` at your own machines to target them.
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``FirewalldPolicies`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's raw records with
   ``json.dumps(..., indent=2)``.

``FirewalldPolicies`` is a reemote5 original (pyinfra ships no firewalld
module): one shell round-trip enumerates ``firewall-cmd --get-policies``
and runs ``--info-policy`` for every name behind echo markers, keeping
each policy's target, ingress and egress zones, services, ports,
protocols, forward ports, masquerade flag and rich rules. Policies are
the modern firewalld object for filtering traffic *between* zones (an
ingress/egress zone pairing), increasingly replacing direct zone rules
for inter-zone flows; this fact is read-only visibility into them.

Version handling is built into the read: a daemon older than 0.9 does
not know ``--get-policies``, the ``|| true`` guard masks its error, the
marker loop never runs and the fact reads ``{}`` -- so the same script
is safe against older daemons, and an empty mapping beside a running
``FirewalldStatus`` is how a deploy detects "no policy support" versus
"no policies configured" (check the version field). The ``permanent``
argument selects the scope; this script reads the **permanent**
configuration (``permanent=True``), where policy objects are authored.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/firewalld/firewalld_firewalld_policies.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    FirewalldPolicies,
    Host,
    HostConnectionError,
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
        # fans FirewalldPolicies.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. permanent=True selects the on-disk
        # configuration; an old daemon without policy support reads {}.
        policies = await gather_fact(hosts, FirewalldPolicies, permanent=True)
    except (CommandError, HostConnectionError) as exc:
        # The library reports an unreachable host or a failed fact command as a
        # typed error whose message already names the host. Print that message
        # -- no traceback -- and exit non-zero so the failure stays
        # script-visible. (The guarded read itself never raises for a stopped
        # daemon; it reads {}.)
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: firewalld.FirewalldPolicies (permanent) ==")
        print(json.dumps(policies[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())