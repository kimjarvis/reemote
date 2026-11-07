# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: manage an LXD storage pool on two real hosts.

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

``lxd.storage_pool`` reads the ``LxdStoragePools`` fact: an absent pool is
created with ``driver`` and the desired ``config``, an existing one has
only its DRIFTED keys set (config keys are only SET, never unset), and
``present=False`` deletes it. A pool whose driver differs from ``driver``
raises ``OperationError`` in prepare -- drivers cannot be changed in
place. Pools are daemon-wide, so there is no ``project`` scope parameter.

The deploy yields the same dir pool create request twice: the second is a
guaranteed no-op in the apply phase, because the first pool is visible to
it through the fresh per-step fact cache (in the prepare phase, where
nothing is sent, both plan as changes).

Run it with::

    uv run python examples/modules/lxd/lxd_storage_pool.py
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
    """Ensure the fast dir pool exists, twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    Managing a storage pool needs the LXD daemon socket, so every request
    is flagged ``sudo=True`` and rides under root.
    """
    yield op(
        lxd.storage_pool,
        name="Ensure fast dir pool",
        pool="fast",
        driver="dir",
        sudo=True,
    )
    yield op(
        lxd.storage_pool,
        name="Ensure fast dir pool (again)",
        pool="fast",
        driver="dir",
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
        # OperationError a driver mismatch raises in prepare. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())