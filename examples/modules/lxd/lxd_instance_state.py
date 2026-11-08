# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: stop an LXD instance on two real hosts.

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

``lxd.instance_state`` drives started/stopped/frozen idempotently
against the ``LxdInstanceState`` fact -- the CLI verb (start/stop/pause)
is yielded only when the status differs from the target. ``restarted``
and ``unfrozen`` are STATELESS actions that always yield (a restart is
an action, not a desired state); ``force=True`` kills on stop, and
``timeout`` bounds the clean-shutdown wait. The instance must already
exist -- a missing one raises ``OperationError`` in prepare, so create
it with ``lxd_instance.py`` first (the CLI blocks on the daemon's
asynchronous state operation, so no explicit wait is needed).

The deploy yields the same stop request twice: on a running instance the
second is a guaranteed no-op in the apply phase, because the first stop
is visible to it through the fresh per-step fact cache; on an
already-stopped instance both plan as no-ops.

**Host prerequisite.** This example needs the ``lxc`` CLI and a running,
initialized LXD daemon (login user in the ``lxd`` group for fact reads;
operations ride ``sudo=True``). The demo CentOS hosts have neither: LXD is
snap-only on EL9 and their ``staff_t``-confined sudo cannot create ``/snap``
or reach ``/run/snapd.socket``, so it cannot be installed. Compile-checked
and exercised by the offline harness, not run against shared hosts; point
``INVENTORY`` at LXD-enabled machines (or relax the demo hosts) to run live.

Run it with::

    uv run python examples/modules/lxd/lxd_instance_state.py
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
    """Ensure the web1 instance is stopped, twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    Driving an instance's state needs the LXD daemon socket, so every
    request is flagged ``sudo=True`` and rides under root.
    """
    yield op(
        lxd.instance_state,
        name="Ensure web1 is stopped",
        instance="web1",
        state="stopped",
        sudo=True,
    )
    yield op(
        lxd.instance_state,
        name="Ensure web1 is stopped (again)",
        instance="web1",
        state="stopped",
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
        # OperationError a missing instance raises in prepare. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())