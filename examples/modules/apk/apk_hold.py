# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Hold packages back from upgrades, then release the holds.

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

``apk.hold`` is the apk counterpart of ``apt.mark`` and a reemote5 original --
pyinfra ships no apk hold. apk has no ``apt-mark`` verb: it keeps a package
out of upgrades by writing a version *constraint* into ``/etc/apk/world``, so
``present=True`` re-adds each name with ``apk add <pkg>=<version>`` to pin it,
while ``present=False`` re-adds the bare ``apk add <pkg>``, rewriting the
world line without a bound. Releasing a hold therefore keeps the package
installed and drops only the pin -- the mirror image of ``apt-mark unhold``.

The ``version`` argument selects what to pin to. Omitted (as for the core
packages below), each name is pinned to its *currently installed* version --
the newest ``ApkPackages`` reports -- which is the usual "freeze these at what
is on the box now" patch-window move; a name that is not installed cannot be
pinned this way (there is no version to pin to) and is reported rather than
held. Given (as for ``openssl`` below), it is an explicit constraint: a bare
``1.2.3-r0`` is normalized to ``=1.2.3-r0``, while an operator-led ``~3.3`` or
``<2.0`` is kept as written, and the pin is written whether or not the package
is installed yet.

Idempotence reads the ``ApkHeldPackages`` fact (a name -> constraint map
parsed from the same ``/etc/apk/world``): a name is held exactly when its
world constraint already matches the wanted pin, so re-running is a no-op and
re-pinning to a *different* version is detected as a change. The script yields
each direction twice to show that idempotence across the two phases: in the
prepare phase nothing is sent and one fact cache is shared, so the world stays
as the host has it -- both core-package holds and the ``openssl`` pin plan as
changes (when the host does not already carry those exact pins) while both
releases plan as no-ops (nothing is held in the shared-cache view); in the
apply phase the cache is recreated per step, so the first hold writes the
pins, the second sees them present and is a no-op, the ``openssl`` pin is
written, the first release then drops all three, and the second release sees
them gone and is a no-op.

Holding and releasing both write the root-owned apk database and world file,
so every request is flagged ``sudo=True`` and the driver elevates its command.

Run it with::

    uv run python examples/modules/apk/apk_hold.py
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

# Core packages to freeze at whatever version is installed now -- pinned with
# no explicit ``version``, so the operation reads each name's installed version
# from ``ApkPackages``. A tuple, since ``OperationRequest`` kwargs must stay
# hashable. Both are always present on an Alpine box.
HELD_PACKAGES = ("busybox", "musl")

# A single package pinned to an explicit constraint instead of its installed
# version: ``~3.3`` holds openssl to any 3.3.x, and the pin is written whether
# or not that series is installed yet. Passed as a bare string to show the
# one-name shorthand (the operation normalizes it to a one-item tuple).
PINNED_PACKAGE = "openssl"
PINNED_VERSION = "~3.3"

# Every name the release direction drops the pin from: the two installed-version
# holds plus the explicit openssl pin. Tuple concatenation keeps it hashable.
ALL_HELD = HELD_PACKAGES + (PINNED_PACKAGE,)

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
    """Hold the core packages and pin openssl, then release all three.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases (a branch on a mutable fact would raise DeterminismError). The first
    hold pins ``busybox`` and ``musl`` to their installed versions; the second
    carries the same arguments and is a guaranteed no-op in the apply phase,
    because the first pin is already visible through the fresh per-step fact
    cache. The openssl request pins one name to an explicit ``~3.3``
    constraint. The two release requests then drop every pin, the second a
    no-op for the same reason.

    Holding and releasing both write the root-owned apk database and world
    file, so every request is flagged ``sudo=True`` and the driver elevates its
    command over SSH.
    """
    yield op(
        apk.hold,
        name="Hold core packages at their installed versions",
        packages=HELD_PACKAGES,
        sudo=True,
    )
    yield op(
        apk.hold,
        name="Re-hold core packages (no-op)",
        packages=HELD_PACKAGES,
        sudo=True,
    )
    yield op(
        apk.hold,
        name="Pin openssl to an explicit ~3.3 constraint",
        packages=PINNED_PACKAGE,
        version=PINNED_VERSION,
        sudo=True,
    )
    yield op(
        apk.hold,
        name="Release every hold",
        packages=ALL_HELD,
        present=False,
        sudo=True,
    )
    yield op(
        apk.hold,
        name="Re-release every hold (no-op)",
        packages=ALL_HELD,
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
