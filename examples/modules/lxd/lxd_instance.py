# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: ensure an LXD instance on two real hosts.

This is a *usage* example, not part of the library. It drives reemote5's
two-phase execution model end to end over a **real SSH transport**
(asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects. Every request is flagged ``sudo=True``, so the ``lxc`` client
   talks to the LXD daemon socket as root -- no password travels through
   operation arguments, plans or logs.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``lxd.instance`` is the reemote5-original successor to pyinfra's
``lxd.container``. It reads the ``LxdInstances`` fact: an absent instance
yields one ``lxc launch`` carrying the type (``instance_type="vm"`` for
a virtual machine), ``profiles``, ``config`` (``"key=value"`` strings),
``devices`` (``(name, type, ("key=value", ...))`` triples) and the
creation-only ``ephemeral`` flag. An existing instance is reconciled
NON-DESTRUCTIVELY -- profiles assigned, drifted config keys set, drifted
devices removed and re-added. Base-image drift is resolved through the
``LxdImages`` fact and REPORTED in the reason only, never recreated; a
container/VM type mismatch raises ``OperationError`` (delete it first).
``present=False`` stops a running instance then deletes it.

The deploy yields the same create request twice: on a host without the
instance the second is a guaranteed no-op in the apply phase, because
the first launch is visible to it through the fresh per-step fact cache
(in the prepare phase, where nothing is sent, both plan as changes).

Run it with::

    uv run python examples/modules/lxd/lxd_instance.py
"""

import asyncio
import sys
from collections.abc import Generator

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    OperationError,
    OperationRequest,
    PrivilegeError,
    format_log,
    format_plan,
    lxd,
    op,
    run,
)

# The inventory: real hosts and their SSH connection information, supplied by
# the application to the library. No privilege section is needed: sudo
# defaults to root, whose access to the LXD daemon socket needs no password.
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


def deploy(host: Host) -> Generator[OperationRequest]:
    """Ensure the web1 container exists as configured, twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    Launching an instance needs the LXD daemon socket, so every request is
    flagged ``sudo=True`` and rides under root.
    """
    yield op(
        lxd.instance,
        name="Ensure web1 instance",
        instance="web1",
        image="images:ubuntu/24.04",
        profiles=("default",),
        config=("limits.cpu=2", "limits.memory=2GB"),
        sudo=True,
    )
    yield op(
        lxd.instance,
        name="Ensure web1 instance (again)",
        instance="web1",
        image="images:ubuntu/24.04",
        profiles=("default",),
        config=("limits.cpu=2", "limits.memory=2GB"),
        sudo=True,
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
        plans, report, _hosts = await run(INVENTORY, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (CommandError, HostConnectionError, OperationError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command -- including the
        # OperationError a container/VM type mismatch raises in prepare.
        # Print that message -- no traceback -- and exit non-zero so the
        # failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())