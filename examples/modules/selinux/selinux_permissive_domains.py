# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``selinux.PermissiveDomains`` fact on two real hosts.

This is a *usage* example, not part of the library. Where
``examples/modules/selinux/selinux_permissive_domain.py`` drives reemote5's
two-phase execution model (a deploy generator of operations run through
``run``), this one reads a single **fact** directly over the same *real
SSH transport* (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key). The hosts must run SELinux with
   ``policycoreutils-python-utils`` (semanage) installed; point
   ``INVENTORY`` at your own machines to target them.
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``PermissiveDomains`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a host-name-keyed
   mapping -- then print each host's raw list with
   ``json.dumps(..., indent=2)``.

``PermissiveDomains`` is a reemote5 original: pyinfra's selinux namespace
ships no permissive-domain fact. ``semanage permissive -l`` lists the
domains running in permissive mode *despite* a globally enforcing policy
-- denials are logged but not enforced for them -- and the fact keeps the
domain names as a deduplicated list in listing order (the builtin-types
heading is skipped). When troubleshooting a service denial this is the
refined answer: a domain here means someone already carved an exception
for it, which is safer than the global permissive mode the ``Mode`` fact
would report, and the ``selinux.permissive_domain`` operation compares
against this list before adding or removing an exception. A host without
semanage exits 127 and surfaces as ``CommandNotFoundError`` (a
``CommandError`` subclass).

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/selinux/selinux_permissive_domains.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    PermissiveDomains,
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
        # fans PermissiveDomains.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. process lists the permissive domains,
        # skipping the heading and deduplicating.
        domains = await gather_fact(hosts, PermissiveDomains)
    except (CommandError, HostConnectionError) as exc:
        # The library reports an unreachable host or a failed fact command as a
        # typed error whose message already names the host (a missing semanage
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
        print(f"== {host.name}: selinux.PermissiveDomains ==")
        print(json.dumps(domains[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())