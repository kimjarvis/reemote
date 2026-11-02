# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Manage an apk virtual package group: create it, then tear it down.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``). Each plain ``Host``
lazily opens an asyncssh connection from its ``connection`` data, gathers
facts and runs commands for real, and ``run`` closes those connections when
it finishes.

``run`` is a coroutine (both phases are async), so ``main`` is async and
driven by ``asyncio.run``.

``apk.virtual`` is a reemote5 original -- pyinfra's ``apk`` namespace ships
only ``upgrade``, ``update`` and ``packages``, with no virtual-group
management. Alpine's ``apk add -t <tag> <packages>`` (``--virtual``) creates a
temporary *virtual package* ``<tag>`` that depends on ``<packages>`` and
records ``<tag>`` in ``/etc/apk/world``; a single ``apk del <tag>`` later
removes the group and the members nothing else needs. This is the classic
Alpine build-dependency pattern: pull a toolchain in for a compile step, then
tear it back out so it does not linger on the finished image -- which is
exactly what this script demonstrates, creating a ``.build-deps`` group and
then removing it.

Idempotence reads the ``ApkExplicitPackages`` fact (the world names): the
group is present exactly when its tag appears in ``/etc/apk/world``, so
``present=True`` creates it with ``apk add -t`` only when the tag is absent
and ``present=False`` removes it with ``apk del`` only when the tag is
present. The script yields each request twice to show that idempotence across
the two phases: in the prepare phase nothing is sent and one fact cache is
shared, so the world stays empty and both *create* requests plan as changes
while both *remove* requests plan as no-ops (the tag is never there yet); in
the apply phase the cache is recreated per step, so the first create installs
the group, the second sees it present and is a no-op, the first remove then
drops it, and the second remove sees it gone and is a no-op.

Creating and removing both write the root-owned apk database and world file,
so every request is flagged ``sudo=True`` and the driver elevates its command.

Run it with::

    uv run python examples/modules/apk/apk_virtual.py
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
    apk,
    format_log,
    format_plan,
    op,
    run,
)

# The virtual group's tag -- the name apk records in ``/etc/apk/world``. A
# leading dot is the Alpine convention for a temporary group (here the build
# toolchain a compile step wraps itself in).
VIRTUAL_TAG = ".build-deps"

# The member packages the virtual group depends on, as a tuple --
# OperationRequest kwargs must stay hashable. Shared by both create requests
# below so they carry identical operation kwargs (only the log ``name``
# differs).
BUILD_DEPS = ("gcc", "musl-dev", "make")

# The inventory: a real Alpine server and its SSH connection information,
# supplied by the application to the library (the same connection data as the
# inventory_alpine.json entry the read-only apk fact examples load). The
# default host_factory materializes each entry into a plain Host that connects
# over asyncssh.
INVENTORY = [
    {
        "host": "alpine",
        "connection": {
            "address": "192.168.0.197",
            "username": "alpine",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "alpine",
        "tags": ["alpine_host"],
    },
]


def deploy(host: Host) -> Generator[OperationRequest]:
    """Create the build-deps virtual group, then remove it -- each twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first create installs the group; the second carries the same arguments
    and is a guaranteed no-op in the apply phase, because the first create is
    already visible through the fresh per-step fact cache. The remove requests
    then tear the group back down, the second a no-op for the same reason.

    Creating and removing both write the root-owned apk database and world
    file, so every request is flagged ``sudo=True`` and the driver elevates
    its command over SSH.
    """
    yield op(
        apk.virtual,
        name="Create build-deps virtual group",
        tag=VIRTUAL_TAG,
        packages=BUILD_DEPS,
        sudo=True,
    )
    yield op(
        apk.virtual,
        name="Re-create build-deps virtual group (no-op)",
        tag=VIRTUAL_TAG,
        packages=BUILD_DEPS,
        sudo=True,
    )
    yield op(
        apk.virtual,
        name="Remove build-deps virtual group",
        tag=VIRTUAL_TAG,
        present=False,
        sudo=True,
    )
    yield op(
        apk.virtual,
        name="Re-remove build-deps virtual group (no-op)",
        tag=VIRTUAL_TAG,
        present=False,
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
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
