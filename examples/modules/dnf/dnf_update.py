# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Update every dnf package on the hosts, non-idempotently.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with ``validate_inventory``
   so a malformed file fails with an ``InventoryError`` naming the
   offending entry, *before* any host is materialized or connected.
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

``run`` is a coroutine (both phases are async), so ``main`` is async and
driven by ``asyncio.run``.

``dnf.update`` runs ``dnf update -y`` over every installed package, bringing
the whole system up to date. Like the baseline's ``dnf.update`` it is
**non-idempotent**: it reads no fact -- there is no cheap way to tell whether
any package actually has a pending upgrade -- so it always reports a change
and always runs, and dnf is itself a no-op when the system is already up to
date. That is why this script yields the request once rather than twice for an
idempotence demonstration: there is no state to converge on, so the dry-run
plan and the apply log report the very same single change.

Updating the whole system needs root, so the request is flagged ``sudo=True``
and the driver elevates its ``dnf update -y`` command (rendered in the apply
log as ``sudo dnf update -y``).

The request also carries ``when=is_centos``, the declarative eligibility
guard (see ``examples/demonstrations/composition/composed_when_guard.py``).
The predicate reads only **static** inventory data -- ``host.group``, set by
the ``"group"`` key of the inventory entry -- and is a stable module-level
function, so the prepare and apply phases reach the same skip decision. The
driver skips a rejected request without materializing it, so no ``dnf``
command reaches a host without the binary, and plan and log render the step
as ``[skipped: when guard is_centos]`` in its lockstep slot. The shared
``inventory.json`` holds two hosts in the ``"centos"`` group alongside hosts
in other groups, so the run visibly updates the CentOS hosts and skips every
other one.

Run it with::

    uv run python examples/modules/dnf/dnf_update.py
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
    dnf,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# examples/modules/dnf/dnf_update.py -> the project root, so the inventory
# is found whatever the current working directory is.
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


def is_centos(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"centos"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision. ``dnf`` only
    exists on the CentOS hosts, so the guard keeps the update off the Alpine
    and Ubuntu hosts, where the command would fail.
    """
    return host.group == "centos"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Update every package on the host.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError),
    and identical on every host: eligibility is expressed with the
    ``when=is_centos`` guard the request carries, not with an ``if``.
    ``dnf.update`` reads no fact, so this single request is planned -- and
    applied -- as a change on every eligible host regardless of its state.

    Updating the whole system needs root, so the request is flagged
    ``sudo=True`` and the driver elevates its ``dnf update -y`` command over
    SSH.
    """
    yield op(dnf.update, name="Update all packages", sudo=True, when=is_centos)


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The plan is materialized against each
        # host's real state, but no command that mutates a host is sent. A
        # request the ``when`` guard rejects is planned as
        # [skipped: when guard is_centos] without materializing it.
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
