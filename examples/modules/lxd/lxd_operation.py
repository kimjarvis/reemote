# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: wait on an LXD background operation on two real hosts.

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

``lxd.operation`` is the low-level primitive for LXD's asynchronous
mutations (image downloads, copies, migrations): ``wait=True`` polls the
``LxdOperations`` fact every ``interval`` seconds until the operation
leaves the running list or ``timeout`` expires (``WaitTimeoutError``);
``cancel=True`` cancels a still-running operation, idempotently. The
``OPERATION_ID`` constant below is a placeholder -- list live operations
with ``lxd_lxd_operations.py`` first. Waiting on an id that is no longer
registered succeeds at once, so the script runs even unedited.

The deploy yields the same wait request twice: waiting is STATELESS, so
BOTH requests run in the apply phase -- neither is a no-op; the second
simply finds the operation already finished (or unregistered) and
succeeds on its first poll.

Run it with::

    uv run python examples/modules/lxd/lxd_operation.py
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
    WaitTimeoutError,
    format_log,
    format_plan,
    lxd,
    op,
    run,
)

# Placeholder operation UUID: replace with a live operation's id -- see
# lxd_lxd_operations.py. An unregistered id succeeds on the first poll.
OPERATION_ID = "00000000-0000-0000-0000-000000000000"

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
    """Wait for OPERATION_ID to finish, twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    Querying background operations needs the LXD daemon socket, so every
    request is flagged ``sudo=True`` and rides under root.
    """
    yield op(
        lxd.operation,
        name="Wait for the background operation",
        operation_id=OPERATION_ID,
        wait=True,
        timeout=60.0,
        interval=5.0,
        sudo=True,
    )
    yield op(
        lxd.operation,
        name="Wait for the background operation (again)",
        operation_id=OPERATION_ID,
        wait=True,
        timeout=60.0,
        interval=5.0,
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
    except (
        CommandError,
        HostConnectionError,
        PrivilegeError,
        WaitTimeoutError,
    ) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command -- including the
        # WaitTimeoutError raised when the operation outlives the timeout.
        # Print that message -- no traceback -- and exit non-zero so the
        # failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())