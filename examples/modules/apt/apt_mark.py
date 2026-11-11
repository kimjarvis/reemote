# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Hold apt packages back from upgrades with ``apt-mark hold``.

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
it finishes. The path is resolved from ``__file__``, so the example works
from any working directory; point ``INVENTORY_PATH`` at your own file to
target your machines.

``apt.mark`` is a reemote original: the baseline ships no operation managing
package hold states. A held package is skipped by ``apt upgrade`` /
``apt.dist_upgrade`` until it is released again, which is how a deploy
pins critical packages at their current version without uninstalling
anything. The operation is idempotent on the ``AptHeldPackages`` fact
(``apt-mark showhold``): ``held=True`` marks only the requested names not
already held, ``held=False`` releases only the names currently held
(``apt-mark unhold``), and a request whose names are all in the wanted
state is a no-op. The script yields the *same* hold request twice to show
that idempotence across the two phases: the prepare phase plans both as
changes (nothing is sent yet), while the apply phase holds on the first
and reports the second as a no-op, because the fresh per-step fact cache
re-reads ``showhold`` against the updated marks. A third request then
releases the holds again (``held=False``), so the example is
*self-restoring*: it leaves the host's marks exactly as it found them.
That matters because ``run_examples.py`` sweeps every example over the
same shared hosts -- a lingering hold on ``tree`` would make the later
``apt_packages.py`` example fail to install it (``apt-get install`` refuses
a held package), so the hold-then-release cycle keeps the sweep clean.

Marking writes to the root-owned dpkg database, so the requests are
flagged ``sudo=True`` and the driver elevates their commands. Pair this
with ``apt_upgrade.py``'s ``apt.upgrade`` to compose the
hold-then-patch flow: everything except the held packages upgrades, and
the closing ``held=False`` request releases them again.

Like the other apt examples, every request carries ``when=is_ubuntu``: the
predicate reads only static inventory data (``host.group``) and is a stable
module-level function, so both phases reach the same skip decision. The
driver skips a rejected request without materializing it -- neither the
``apt-mark hold`` command nor the ``AptHeldPackages`` fact behind it runs
on a non-Ubuntu host -- and plan and log render
``[skipped: when guard is_ubuntu]``. The shared ``inventory.json`` holds one
host in the ``"ubuntu"`` group alongside hosts in other groups, so each run
holds on the Ubuntu host and skips every other one.

Run it with::

    uv run python examples/modules/apt/apt_mark.py
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
    apt,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# examples/modules/apt/apt_mark.py -> the project root, so the inventory is
# found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The packages to keep out of upgrades -- the same baseline names the
# apt_packages.py example installs. A module-level tuple, so the request
# kwargs are hashable and identical in the prepare and apply phases.
CRITICAL_PACKAGES = ("htop", "tree")


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


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"ubuntu"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision and the
    determinism guard holds. apt itself is the reason for the guard: on a
    non-Ubuntu host every request below would fail on a missing ``apt-mark``
    binary, so the driver skips them all instead.
    """
    return host.group == "ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Hold the critical packages -- twice, to show idempotence -- then release.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request holds whatever names are not held yet; the second
    carries the same arguments and is a no-op in the apply phase once the
    first hold marked them all. The third releases the holds again
    (``held=False``), leaving the host's marks as found so the shared-host
    example sweep is not polluted -- a held ``tree`` would break the later
    ``apt_packages.py`` install.

    Marking writes to the root-owned dpkg database, so all three requests
    are flagged ``sudo=True`` and the driver elevates their commands over
    SSH.
    """
    yield op(
        apt.mark,
        name="Hold critical packages",
        packages=CRITICAL_PACKAGES,
        sudo=True,
        when=is_ubuntu,
    )
    yield op(
        apt.mark,
        name="Re-run hold (no-op)",
        packages=CRITICAL_PACKAGES,
        sudo=True,
        when=is_ubuntu,
    )
    yield op(
        apt.mark,
        name="Release critical packages",
        packages=CRITICAL_PACKAGES,
        held=False,
        sudo=True,
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Facts are still gathered over SSH (the
        # showhold read is read-only) so the plan reflects each host's real
        # pre-deployment state; no command that mutates a host is sent.
        # A request the ``when`` guard rejects is planned as
        # [skipped: when guard is_ubuntu] without materializing it, so its
        # fact is not read on that host either.
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