# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: wait on an LXD background operation on real Ubuntu hosts.

This is a *usage* example, not part of the library. It drives reemote's
two-phase execution model end to end over a **real SSH transport**
(asyncssh):

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with ``validate_inventory``
   so a malformed file fails with an ``InventoryError`` naming the
   offending entry, *before* any host is materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects. Every request is flagged ``sudo=True``, so the ``lxc`` client
   talks to the LXD daemon socket as root -- no password travels through
   operation arguments, plans or logs.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``lxd.operation`` is the low-level primitive for LXD's asynchronous
mutations (image downloads, copies, migrations): ``wait=True`` polls the
``LxdOperations`` fact every ``interval`` seconds until the operation
leaves the running list or ``timeout`` expires (``WaitTimeoutError``);
``cancel=True`` cancels a still-running operation, idempotently. The
``OPERATION_ID`` constant below is a placeholder -- list live operations
with ``lxd_lxd_operations.py`` first. Waiting on an id that is no longer
registered succeeds at once, so the script runs even unedited.

The deploy yields the same wait request twice: waiting is STATELESS, so
BOTH requests run in the apply phase -- neither is a no-op; the second
simply finds the operation already finished (or unregistered) and
succeeds on its first poll.

LXD is *not* set up out of the box on Ubuntu -- it ships as a snap, not a
default install -- so this example provisions it itself before deploying:
``main`` first awaits ``provision_lxd`` from the sibling ``provision_lxd.py``,
which installs the ``lxd`` snap with ``snap.package`` (pinned to the
``6/stable`` channel) and runs ``lxd init --auto``. Both steps are
idempotent -- the install on the ``SnapPackage`` fact, the init on the
daemon's own already-configured checks -- so the example runs even on a
host where LXD was never installed, and a re-run leaves an initialized
daemon untouched. The one prerequisite provisioning does not cover is
socket access for the unprivileged fact reads: the login user must be a
member of the ``lxd`` group (the demo Ubuntu host already is); the
operations themselves ride ``sudo=True`` and need no group membership.

Every request carries ``when=is_ubuntu``, the declarative eligibility
guard: the predicate reads only **static** inventory data --
``host.family``, set by the ``"family"`` key of the inventory entry -- and
is a stable module-level function, so the prepare and apply phases reach
the same skip decision. The driver skips a rejected request without
materializing it, so on hosts outside the ``"Ubuntu"`` family neither the
commands nor the facts behind them run, and plan and log render the step
as ``[skipped: when guard is_ubuntu]`` in its lockstep slot. The path is
resolved from ``__file__``, so the example works from any working
directory; point ``INVENTORY_PATH`` at your own file to target your
machines.

Run it with::

    uv run python examples/modules/lxd/lxd_operation.py
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
    WaitTimeoutError,
    format_log,
    format_plan,
    lxd,
    op,
    run,
    validate_inventory,
)

# The LXD provisioning helper sits beside this script; add its directory
# to sys.path so ``import provision_lxd`` resolves whether the example is
# run by path or imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from provision_lxd import provision_lxd

# Placeholder operation UUID: replace with a live operation's id -- see
# lxd_lxd_operations.py. An unregistered id succeeds on the first poll.
OPERATION_ID = "00000000-0000-0000-0000-000000000000"

# examples/modules/lxd/lxd_operation.py -> the project root, so the inventory
# is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries
    each carrying at least ``host`` and ``connection``. Returns the parsed
    list, ready to materialize into hosts; raises ``InventoryError`` (via
    ``validate_inventory``) when the document is not an array or an entry
    does not match the schema.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision. The Ubuntu
    demo host is the one ``provision_lxd.py`` provisions with LXD before
    this example deploys (see the module docstring), so the deploy is
    guarded to that family.
    """
    return host.family == "Ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Wait for OPERATION_ID to finish, twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    Querying background operations needs the LXD daemon socket, so every
    request is flagged ``sudo=True`` and rides under root.
    """
    yield op(
        lxd.operation,
        name="Wait for the background operation",
        operation_id=OPERATION_ID,
        wait=True,
        timeout=60.0,
        interval=5.0,
        sudo=True,
        when=is_ubuntu,
    )
    yield op(
        lxd.operation,
        name="Wait for the background operation (again)",
        operation_id=OPERATION_ID,
        wait=True,
        timeout=60.0,
        interval=5.0,
        sudo=True,
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Provision LXD (the snap + ``lxd init --auto``) on the Ubuntu hosts
        # before anything below reads or mutates LXD state, so the example
        # runs even on a host where LXD was never installed (see
        # provision_lxd.py).
        print("\n== Provisioning LXD (provision_lxd.py) ==")
        await provision_lxd(inventory)

        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent.
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
    except (
        CommandError,
        HostConnectionError,
        PrivilegeError,
        WaitTimeoutError,
    ) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command -- including the
        # WaitTimeoutError raised when the operation outlives the timeout.
        # Print that message -- no traceback -- and exit non-zero so the
        # failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())