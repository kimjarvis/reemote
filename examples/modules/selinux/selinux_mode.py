# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``selinux.Mode`` fact on two real hosts.

This is a *usage* example, not part of the library. Where
``examples/modules/selinux/selinux_op_mode.py`` drives reemote5's two-phase
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
3. Gather ``Mode`` on every host at once with the driver's ``gather_fact``
   helper -- it fans the fact's ``process`` coroutine out across the
   inventory with ``asyncio.gather`` and returns a host-name-keyed mapping
   -- then print each host's raw record with ``json.dumps(..., indent=2)``.

``Mode`` is a reemote5 original: pyinfra's selinux namespace ships no mode
fact, so every SELinux decision starts with a manual getenforce.
``sestatus`` answers the whole question in one command and the fact keeps
three fields -- ``mode`` (the runtime state: enforcing, permissive or
disabled), ``config_mode`` (the mode ``/etc/selinux/config`` selects for
the next boot) and ``policy`` (the loaded policy name, e.g. targeted).
Keeping runtime beside config is the point: "enforcing now but permissive
after reboot" is a different host from a fully enforcing one, and only
this fact tells them apart. A disabled subsystem prints only ``SELinux
status: disabled`` -- the mode reads "disabled" and the config/policy
fields stay empty (sestatus reports neither in that state). A host without
sestatus exits 127 and surfaces as ``CommandNotFoundError`` (a
``CommandError`` subclass).

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/selinux/selinux_mode.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    Mode,
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
        # fans Mode.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. process runs sestatus and keeps the
        # runtime mode, the config-file mode and the loaded policy name.
        modes = await gather_fact(hosts, Mode)
    except (CommandError, HostConnectionError) as exc:
        # The library reports an unreachable host or a failed fact command as a
        # typed error whose message already names the host (a missing sestatus
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
        print(f"== {host.name}: selinux.Mode ==")
        print(json.dumps(modes[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())