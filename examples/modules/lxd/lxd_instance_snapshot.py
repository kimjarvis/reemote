# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: ensure an LXD instance snapshot on two real hosts.

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

``lxd.instance_snapshot`` reads the ``LxdInstanceSnapshots`` fact: an
absent snapshot is created (``stateful=True`` includes the runtime
state), an unwanted one deleted through the ``instance/snapshot`` name
form (``present=False``). ``restore=True`` is STATELESS and always yields
``lxc restore`` -- a restore is an action, not a desired state; stop the
instance first for a non-stateful restore. The instance must already
exist (the CLI fails the snapshot at apply time otherwise), so create it
with ``lxd_instance.py`` first.

The deploy yields the same create request twice: the second is a
guaranteed no-op in the apply phase, because the first snapshot is
visible to it through the fresh per-step fact cache (in the prepare
phase, where nothing is sent, both plan as changes).

Run it with::

    uv run python examples/modules/lxd/lxd_instance_snapshot.py
"""

import asyncio
import sys
from collections.abc import Generator

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
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
    """Ensure web1's snap0 snapshot exists, twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    Snapshotting an instance needs the LXD daemon socket, so every request
    is flagged ``sudo=True`` and rides under root.
    """
    yield op(
        lxd.instance_snapshot,
        name="Ensure web1 snap0",
        instance="web1",
        snapshot="snap0",
        sudo=True,
    )
    yield op(
        lxd.instance_snapshot,
        name="Ensure web1 snap0 (again)",
        instance="web1",
        snapshot="snap0",
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
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command -- including the
        # CLI's failure to snapshot a missing instance at apply time. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())