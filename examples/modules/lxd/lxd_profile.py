# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: manage an LXD profile on two real hosts.

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

``lxd.profile`` reads the ``LxdProfiles`` fact: an absent profile is
created empty, then only DRIFTED ``config`` keys are set and DRIFTED
``devices`` are detached and re-attached with the desired properties
(config keys are only SET, never unset). ``present=False`` deletes the
profile -- the daemon refuses while instances still use it.

The deploy yields the same create request twice: the second is a
guaranteed no-op in the apply phase, because the first profile is visible
to it through the fresh per-step fact cache (in the prepare phase, where
nothing is sent, both plan as changes).

**Host prerequisite.** This example needs the ``lxc`` CLI and a running,
initialized LXD daemon (login user in the ``lxd`` group for fact reads;
operations ride ``sudo=True``). The demo CentOS hosts have neither: LXD is
snap-only on EL9 and their ``staff_t``-confined sudo cannot create ``/snap``
or reach ``/run/snapd.socket``, so it cannot be installed. Compile-checked
and exercised by the offline harness, not run against shared hosts; point
``INVENTORY`` at LXD-enabled machines (or relax the demo hosts) to run live.

Run it with::

    uv run python examples/modules/lxd/lxd_profile.py
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
    """Ensure the web profile exists with its limit and root disk, twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    Managing a profile needs the LXD daemon socket, so every request is
    flagged ``sudo=True`` and rides under root.
    """
    yield op(
        lxd.profile,
        name="Ensure web profile",
        profile="web",
        config=("limits.memory=2GB",),
        devices=(("root", "disk", ("path=/", "pool=default")),),
        sudo=True,
    )
    yield op(
        lxd.profile,
        name="Ensure web profile (again)",
        profile="web",
        config=("limits.memory=2GB",),
        devices=(("root", "disk", ("path=/", "pool=default")),),
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
        # CLI's refusal to attach a device backed by a missing pool at apply
        # time. Print that message -- no traceback -- and exit non-zero so the
        # failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())