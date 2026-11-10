# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install packages by name with exact-version idempotence.

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

``apk.packages`` installs (or with ``present=False`` removes) packages by
name -- here two small CLI utilities, the kind of baseline tooling a deploy
ensures on every Alpine machine. It reads the installed versions through the
``ApkInstalledPackages`` fact and installs only what is missing with ``apk add``, so
re-running is a no-op. A name can be pinned as ``<pkg>=<version>``: a pin
counts as present only when that *exact* version is installed, the baseline's
``ensure_packages`` version match. ``latest=True`` would additionally upgrade
every installed, unpinned name with ``apk upgrade`` (the baseline's legacy blind
upgrade). apk never prompts, so the commands carry no ``-y`` flag -- unlike
the apt and dnf examples. Where the baseline's ``apk.packages`` also takes
``update`` and ``upgrade`` options that prefix the operation with a
whole-system ``apk update`` / ``apk upgrade``, reemote5 deliberately drops
both -- the whole-system refresh/upgrade stays out of the package operation
-- so those keywords are absorbed with no effect.

Two apk-native install modifiers are demonstrated on a third request:
``no_cache=True`` adds ``--no-cache`` (bypass the package cache entirely)
and ``no_scripts=True`` adds ``--no-scripts`` (skip post-install triggers
and scriptlets). Both flow into the ``latest`` upgrade path too and are
ignored by the removal direction.

The script yields the *same* baseline install request twice to show
idempotence across the two phases: the prepare phase plans both as changes
(nothing is installed yet, so both compare against the same pre-deployment
state), while the apply phase installs on the first and reports the second
as a no-op, because the fresh per-step fact cache now sees the packages the
first step installed. A third request then demonstrates both modifiers
together on a different package.

Installing packages writes to the root-owned apk database, so the requests
are flagged ``doas=True`` and the driver elevates their commands. ``doas``
rather than ``sudo`` because the Alpine host has no ``sudo`` installed at
all -- ``sudo apk add`` would die with ``sh: sudo: not found`` (exit 127)
before reaching ``apk``. ``doas`` is passwordless here: reemote5 always runs
``doas -n``, which fails rather than prompts, so the host must grant the
login user a ``permit nopass`` rule in ``/etc/doas.conf`` (see
``examples/demonstrations/privilege/privilege_doas.py``); without one the
driver raises ``PrivilegeError``. No target user is named, so the command
sent is a bare ``doas -n apk add ...`` (doas already targets root, so there
is no ``-u``).

Every request also carries ``when=is_alpine``, the declarative eligibility
guard (see
``examples/demonstrations/composition/composed_when_guard.py``). Two rules
make it deterministic: the predicate reads only **static** inventory data --
``host.group``, set by the ``"group"`` key of the inventory entry -- never a
mutable fact, and it is a stable module-level function, so the prepare and
apply phases reach the same skip decision. ``when`` is a meta-parameter like
``doas``: captured on the request, never forwarded to the operation, and
skipped by the *driver*, so every host keeps the identical three-request
schedule (no ``if`` in the deploy body) and each request still consumes its
lockstep slot. On a host the guard rejects, the plan and the run log render
the step as ``[skipped: when guard is_alpine]`` and none of its commands --
nor the ``ApkInstalledPackages`` fact behind it -- is materialized at all, which is
exactly why a package manager's operations must be guarded rather than run
blind on a host without ``apk``. The project root's ``inventory.json`` holds
one host in the ``"alpine"`` group alongside hosts in other groups, so the
run visibly installs on the Alpine host and skips every other one.

Guarding per request is the operation-side counterpart of what the read-only
fact example ``apk_apk_packages.py`` does for a gather: ``gather_fact`` takes
no ``when`` parameter, so there the predicate filters the host list before
the fan-out, while a deploy request carries the guard itself and the driver
skips it per host.

Run it with::

    uv run python examples/modules/apk/apk_packages.py
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

# examples/modules/apk/apk_packages.py -> the project root, so the
# inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The packages to ensure, as a tuple -- OperationRequest kwargs must stay
# hashable. Shared by both baseline requests below so they carry identical
# operation kwargs (only the log ``name`` differs).
BASELINE_PACKAGES = ("htop", "tree")

# A single package installed with the apk-native modifiers to demonstrate
# ``no_cache`` and ``no_scripts``.
MODIFIER_PACKAGE = ("curl",)


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
    """Install baseline packages -- twice, to show idempotence -- then
    demonstrate the apk-native install modifiers on a third request.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError),
    and identical on every host: eligibility is expressed with the
    ``when=is_alpine`` guard each request carries, not with an ``if``. The
    first request installs whatever is missing; the second carries the
    same arguments and is a guaranteed no-op in the apply phase, because the
    first install is already visible through the fresh per-step fact cache.
    The third installs a different package with ``no_cache=True`` and
    ``no_scripts=True``, rendering ``apk add --no-cache --no-scripts curl``.

    Installing packages writes to the root-owned apk database, so all
    requests are flagged ``doas=True`` (the host has no ``sudo``) and the
    driver elevates their commands over SSH with passwordless ``doas -n``.
    """
    yield op(
        apk.packages,
        name="Install baseline packages",
        packages=BASELINE_PACKAGES,
        doas=True,
        when=is_alpine,
    )
    yield op(
        apk.packages,
        name="Re-install baseline packages (no-op)",
        packages=BASELINE_PACKAGES,
        doas=True,
        when=is_alpine,
    )
    yield op(
        apk.packages,
        name="Install with --no-cache --no-scripts",
        packages=MODIFIER_PACKAGE,
        no_cache=True,
        no_scripts=True,
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
        print("\n== Plan (prepare phase, --dry) ==")
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
