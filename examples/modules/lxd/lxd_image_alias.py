# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: manage an LXD image alias on two real hosts.

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

``lxd.image_alias`` reads the ``LxdImageAliases`` fact: an alias already
targeting ``fingerprint`` is a no-op, one pointing elsewhere is REPOINTED
(delete then create), an absent one is created, and ``present=False``
deletes it. Creating or repointing without a ``fingerprint`` raises
``ValueError``. The ``FINGERPRINT`` constant below is a placeholder:
replace it with a cached image's fingerprint (inspect the store with
``lxd_lxd_images.py`` first) -- the daemon rejects an unknown fingerprint
at apply time.

The deploy yields the same create request twice: the second is a
guaranteed no-op in the apply phase, because the first alias is visible
to it through the fresh per-step fact cache (in the prepare phase, where
nothing is sent, both plan as changes).

Run it with::

    uv run python examples/modules/lxd/lxd_image_alias.py
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

# Placeholder fingerprint: replace with a cached image's 64-character hex
# fingerprint -- see lxd_lxd_images.py to inspect the image store.
FINGERPRINT = "0" * 64

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
    """Ensure the ubuntu-noble alias targets FINGERPRINT, twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    Aliasing an image needs the LXD daemon socket, so every request is
    flagged ``sudo=True`` and rides under root.
    """
    yield op(
        lxd.image_alias,
        name="Point ubuntu-noble at the cached image",
        alias="ubuntu-noble",
        fingerprint=FINGERPRINT,
        sudo=True,
    )
    yield op(
        lxd.image_alias,
        name="Point ubuntu-noble at the cached image (again)",
        alias="ubuntu-noble",
        fingerprint=FINGERPRINT,
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
        # CLI's rejection of an unknown fingerprint at apply time. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())