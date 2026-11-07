# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``selinux.FileContextMapping`` fact on two real hosts.

This is a *usage* example, not part of the library. Where
``examples/modules/selinux/selinux_op_file_context_mapping.py`` drives
reemote5's two-phase execution model (a deploy generator of operations run
through ``run``), this one reads a single **fact** directly over the same
*real SSH transport* (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key). The hosts must run SELinux with
   ``policycoreutils-python-utils`` (semanage) installed; point
   ``INVENTORY`` at your own machines to target them.
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``FileContextMapping`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a host-name-keyed
   mapping -- then print each host's raw context (or ``null``) with
   ``json.dumps(..., indent=2)``.

``FileContextMapping`` ports pyinfra's fact of the same name: the
*local-only* fcontext database (``semanage fcontext -n -l``) is grepped for
rows starting with the target expression -- here ``/web(/.*)?``, a custom
web-root rule -- and the trailing ``user:role:type:level`` of the single
matching row is split into the same ``SeContext`` shape ``FileContext``
returns. This is the *persistent* rule a relabel would apply, not the
context any file carries right now. Deliberate deviation from pyinfra: no
mapping reads as ``None`` rather than pyinfra's ``{}`` -- the house
None-on-absent convention -- so an absent rule stays distinguishable from
a context with empty components. A host without semanage exits 127 and
surfaces as ``CommandNotFoundError`` (a ``CommandError`` subclass).

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/selinux/selinux_file_context_mapping.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    FileContextMapping,
    Host,
    HostConnectionError,
    gather_fact,
)

# The target expression whose persistent mapping to read: a custom web
# root and everything under it. Change it to inspect any fcontext rule.
MAPPING_TARGET = "/web(/.*)?"

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
        # fans FileContextMapping.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. process greps the local fcontext database
        # for the target and splits the single matching row's context, or
        # reads None when no rule exists.
        mappings = await gather_fact(hosts, FileContextMapping, MAPPING_TARGET)
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
        print(f"== {host.name}: selinux.FileContextMapping({MAPPING_TARGET!r}) ==")
        print(json.dumps(mappings[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())