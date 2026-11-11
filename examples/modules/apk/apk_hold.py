# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Hold packages back from upgrades, then release the holds.

This is a *usage* example, not part of the library. It shows how an
application drives reemote's two-phase execution model end to end over a
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

``apk.hold`` is the apk counterpart of ``apt.mark`` and a reemote original --
the baseline ships no apk hold. apk has no ``apt-mark`` verb: it keeps a package
out of upgrades by writing a version *constraint* into ``/etc/apk/world``, so
``present=True`` re-adds each name with ``apk add <pkg>=<version>`` to pin it,
while ``present=False`` re-adds the bare ``apk add <pkg>``, rewriting the
world line without a bound. Releasing a hold therefore keeps the package
installed and drops only the pin -- the mirror image of ``apt-mark unhold``.

The ``version`` argument selects what to pin to. Omitted (as for the core
packages below), each name is pinned to its *currently installed* version --
the newest ``ApkInstalledPackages`` reports -- which is the usual "freeze these at what
is on the box now" patch-window move; a name that is not installed cannot be
pinned this way (there is no version to pin to) and is reported rather than
held. Given (as for ``openssl`` below), it is an explicit constraint: a bare
``1.2.3-r0`` is normalized to ``=1.2.3-r0``, while an operator-led ``>=3.0`` or
``<2.0`` is kept as written, and the pin is written whether or not the package
is installed yet.

Idempotence reads the ``ApkHeldPackages`` fact (a list of ``ApkPackageVersion``
entries -- the bare name with the world constraint, operator included, as its
``version``, the ``@tag`` dropped -- parsed from the same
``/etc/apk/world``, looked up by name and compared on the entry's
``version``): a name is held exactly when its
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
so every request is flagged ``doas=True`` and the driver elevates its
command. ``doas`` rather than ``sudo`` because the Alpine host has no
``sudo`` installed at all -- ``sudo apk add`` would die with
``sh: sudo: not found`` (exit 127) before reaching ``apk``. ``doas`` is
passwordless here: reemote always runs ``doas -n``, which fails rather than
prompts, so the host must grant the login user a ``permit nopass`` rule in
``/etc/doas.conf`` (see
``examples/demonstrations/privilege/privilege_doas.py``); without one the
driver raises ``PrivilegeError``. No target user is named, so each command
sent is a bare ``doas -n apk add ...`` (doas already targets root, so there
is no ``-u``).

Every request also carries ``when=is_alpine``, the declarative eligibility
guard (see
``examples/demonstrations/composition/composed_when_guard.py``). Two rules
make it deterministic: the predicate reads only **static** inventory data --
``host.group``, set by the ``"group"`` key of the inventory entry -- never a
mutable fact, and it is a stable module-level function, so the prepare and
apply phases reach the same skip decision. On a host the guard rejects, the
plan and the run log render the step as ``[skipped: when guard is_alpine]``
and none of its commands -- nor the ``ApkHeldPackages`` and ``ApkInstalledPackages``
facts behind them -- is materialized at all, which is exactly why an
apk-native operation must be guarded rather than run blind on a host without
``apk``. The project root's ``inventory.json`` holds one host in the
``"alpine"`` group alongside hosts in other groups, so the run visibly pins
and releases on the Alpine host and skips every other one.

Run it with::

    uv run python examples/modules/apk/apk_hold.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

from reemote import (
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

# Core packages to freeze at whatever version is installed now -- pinned with
# no explicit ``version``, so the operation reads each name's installed version
# from ``ApkInstalledPackages``. A tuple, since ``OperationRequest`` kwargs must stay
# hashable. Both are always present on an Alpine box.
HELD_PACKAGES = ("busybox", "musl")

# A single package pinned to an explicit constraint instead of its installed
# version: ``>=3.0`` holds openssl to any 3.x or newer, and the pin is written
# whether or not the package is installed yet (openssl is not installed on the
# demo host, so this also exercises the install-and-pin path). The constraint
# must be satisfiable by the repository: the demo Alpine host ships openssl
# 3.5.x, so the old ``~3.3`` pin failed with "unable to select packages" --
# ``>=3.0`` is satisfied by 3.5.x. Passed as a bare string to show the one-name
# shorthand (the operation normalizes it to a one-item tuple).
PINNED_PACKAGE = "openssl"
PINNED_VERSION = ">=3.0"

# Every name the release direction drops the pin from: the two installed-version
# holds plus the explicit openssl pin. Tuple concatenation keeps it hashable.
ALL_HELD = HELD_PACKAGES + (PINNED_PACKAGE,)

# examples/modules/apk/apk_hold.py -> the project root, so the
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
    """Hold the core packages and pin openssl, then release all three.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases (a branch on a mutable fact would raise DeterminismError). The first
    hold pins ``busybox`` and ``musl`` to their installed versions; the second
    carries the same arguments and is a guaranteed no-op in the apply phase,
    because the first pin is already visible through the fresh per-step fact
    cache. The openssl request pins one name to an explicit ``>=3.0``
    constraint. The two release requests then drop every pin, the second a
    no-op for the same reason.

    Holding and releasing both write the root-owned apk database and world
    file, so every request is flagged ``doas=True`` (the host has no ``sudo``)
    and the driver elevates its command over SSH with passwordless
    ``doas -n``. Branch-free means the two phases agree, and the
    ``when=is_alpine`` guard on every request keeps the schedule identical on
    every host: eligibility is the driver's decision, not an ``if`` in this
    body.
    """
    yield op(
        apk.hold,
        name="Hold core packages at their installed versions",
        packages=HELD_PACKAGES,
        doas=True,
        when=is_alpine,
    )
    yield op(
        apk.hold,
        name="Re-hold core packages (no-op)",
        packages=HELD_PACKAGES,
        doas=True,
        when=is_alpine,
    )
    yield op(
        apk.hold,
        name="Pin openssl to an explicit >=3.0 constraint",
        packages=PINNED_PACKAGE,
        version=PINNED_VERSION,
        doas=True,
        when=is_alpine,
    )
    yield op(
        apk.hold,
        name="Release every hold",
        packages=ALL_HELD,
        present=False,
        doas=True,
        when=is_alpine,
    )
    yield op(
        apk.hold,
        name="Re-release every hold (no-op)",
        packages=ALL_HELD,
        present=False,
        doas=True,
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
