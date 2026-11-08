# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``files.SameFile`` fact on two real hosts.

This is a *usage* example, not part of the library. Like
``examples/modules/files/files_link.py``, it reads **facts** directly over
the *real SSH transport* (asyncssh) instead of driving the two-phase
execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``Link`` for ``/etc/localtime`` first -- the timezone symlink --
   to learn each host's zoneinfo target, then gather ``SameFile`` twice per
   host: once between ``/etc/localtime`` and that target, once between the
   unrelated pair ``/etc/hosts`` and ``/etc/hostname``, and print both
   verdicts with ``json.dumps(..., indent=2)``.

``SameFile`` mirrors pyinfra's ``files.SameFile``: one
``test <path> -ef <target> && echo true || echo false`` probe, always
exiting zero and returning the plain bool -- the two paths name one and
the same device *and* inode, or they do not. The ``-ef`` operator compares
*resolved* inodes, so the symlink and the zoneinfo file it points at
share one inode and answer ``True`` -- the same reading
``files.link`` uses to recognize an existing *hard* link
(``symbolic=False``), which the ``Link`` fact alone cannot see. An
unrelated pair, a missing path, or a directory answers the plain
``False``, never a failed read.

The link-target probe takes a per-host argument (each host lives in its
own timezone), so it calls ``SameFile.process`` directly per host under
``asyncio.gather``; the fixed unrelated pair goes through the
``gather_fact`` helper, which fans the fact out across the inventory and
returns a host-name-keyed mapping.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/files/files_same_file.py
"""

import asyncio
import json
import sys
from typing import Any

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    Link,
    SameFile,
    gather_fact,
)

# The symlink whose target names the zoneinfo file, and the unrelated pair
# expected to answer False on any ordinary host.
LOCALTIME = "/etc/localtime"
UNRELATED = ("/etc/hosts", "/etc/hostname")

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


async def same_as_localtime(host: Host, target: str | None) -> bool | None:
    """Compare ``/etc/localtime`` against its own link target on one host.

    ``None`` (the Link fact answered ``None`` absent or ``False`` non-link,
    so there is no target to compare) passes through as ``None`` instead of
    probing -- the fact itself never returns ``None``, this is the
    example's reading aid.
    """
    if target is None:
        return None
    return await SameFile.process(host, LOCALTIME, target)


async def main() -> None:
    # Materialize every inventory entry up front so facts can be gathered on
    # all hosts at once.
    hosts = [Host.from_inventory(entry) for entry in INVENTORY]
    try:
        # First reading: gather_fact fans Link.process out with
        # asyncio.gather and returns a host-name-keyed mapping, so both SSH
        # round-trips overlap. A dict carries link_target (the zoneinfo
        # file); False means the path is not a symlink and None it is
        # absent. datetime values are not needed here, only the target.
        links = await gather_fact(hosts, Link, LOCALTIME)
        targets: dict[str, str | None] = {}
        for host in hosts:
            info = links[host.name]
            targets[host.name] = (
                info.get("link_target") if isinstance(info, dict) else None
            )
        # Second reading: the symlink against its own target answers True
        # (same device and inode -- exactly what makes files.link's hard
        # links idempotent), gathered per host because the target differs.
        answers = await asyncio.gather(
            *(same_as_localtime(host, targets[host.name]) for host in hosts)
        )
        same_as_target = dict(zip((host.name for host in hosts), answers))
        # Third reading: the unrelated pair through the plain gather_fact
        # helper -- one fixed argument pair broadcast to every host, the
        # answer the probe gives for anything that is not one file.
        unrelated = await gather_fact(hosts, SameFile, *UNRELATED)
    except (CommandError, HostConnectionError) as exc:
        # The library reports an unreachable host or a failed fact command as
        # a typed error whose message already names the host. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection the fact gathering may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    verdicts: dict[str, dict[str, Any]] = {}
    for host in hosts:
        verdicts[host.name] = {
            f"{LOCALTIME} -ef {targets[host.name]}": same_as_target[host.name],
            f"{UNRELATED[0]} -ef {UNRELATED[1]}": unrelated[host.name],
        }
    # Print in inventory order (the gatherings key the results by host name).
    for host in hosts:
        print(f"== {host.name}: files.SameFile ==")
        print(json.dumps(verdicts[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
