# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Manage an apk virtual package group: create it, then tear it down.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Read the target hosts from ``inventory.json`` in the project root -- an
   inventory is just a list of dictionaries, each holding a host's name plus
   its SSH ``connection`` information (address, username and client key),
   which is exactly what JSON expresses -- and validate it up front with
   ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is
   materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``). Each plain ``Host``
lazily opens an asyncssh connection from its ``connection`` data, gathers
facts and runs commands for real, and ``run`` closes those connections when
it finishes. The path is resolved from ``__file__``, so the example works from
any working directory; point ``INVENTORY_PATH`` at your own file to target
your machines.

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

Every request also carries ``when=is_alpine``, the declarative eligibility
guard (see
``examples/demonstrations/composition/composed_when_guard.py``). Two rules
make it deterministic: the predicate reads only **static** inventory data --
``host.group``, set by the ``"group"`` key of the inventory entry -- never a
mutable fact, and it is a stable module-level function, so the prepare and
apply phases reach the same skip decision. On a host the guard rejects, the
plan and the run log render the step as ``[skipped: when guard is_alpine]``
and none of its commands -- nor the ``ApkExplicitPackages`` fact behind them,
the world read -- is materialized at all, which is exactly why an apk-native
operation must be guarded rather than run blind on a host without ``apk``.
The project root's ``inventory.json`` holds one host in the ``"alpine"``
group, so nothing is visibly skipped here; the same deploy against a mixed
inventory would build and tear down on the Alpine hosts only.

Run it with::

    uv run python examples/modules/apk/apk_virtual.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    apk,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
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

# examples/modules/apk/apk_virtual.py -> the project root, so the
# inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries
    each carrying at least ``host`` and ``connection``. Returns the parsed
    list, ready to hand to ``run``, which materializes it with the default
    ``Host.from_inventory`` factory; raises ``InventoryError`` (via
    ``validate_inventory``) when the document is not an array or an entry
    does not match the schema.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_alpine(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"alpine"".

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision and the
    determinism guard holds. apk itself is the reason for the guard: on a
    non-Alpine host every request below would fail on a missing ``apk``
    binary, so the driver skips them all instead.
    """
    return host.group == "alpine"


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
    its command over SSH. Branch-free means the two phases agree, and the
    ``when=is_alpine`` guard on every request keeps the schedule identical on
    every host: eligibility is the driver's decision, not an ``if`` in this
    body.
    """
    yield op(
        apk.virtual,
        name="Create build-deps virtual group",
        tag=VIRTUAL_TAG,
        packages=BUILD_DEPS,
        sudo=True,
        when=is_alpine,
    )
    yield op(
        apk.virtual,
        name="Re-create build-deps virtual group (no-op)",
        tag=VIRTUAL_TAG,
        packages=BUILD_DEPS,
        sudo=True,
        when=is_alpine,
    )
    yield op(
        apk.virtual,
        name="Remove build-deps virtual group",
        tag=VIRTUAL_TAG,
        present=False,
        sudo=True,
        when=is_alpine,
    )
    yield op(
        apk.virtual,
        name="Re-remove build-deps virtual group (no-op)",
        tag=VIRTUAL_TAG,
        present=False,
        sudo=True,
        when=is_alpine,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent. A request the ``when`` guard rejects is
        # planned as [skipped: when guard is_alpine] without materializing it,
        # so its fact is not read on that host either.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON. Python's
        # own message already names the file and the reason, so print just that
        # -- no host was contacted and there is no traceback worth showing.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema. The
        # message names the offending entry by its list index, which is the
        # whole report -- and it fails before any host is materialized.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
