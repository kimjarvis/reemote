# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: set and clear an ext2 attribute flag with files.flags.

This is a *usage* example, not part of the library. It shows ``files.flags``
(baseline ``files.flags`` parity, in the developer-chosen Linux adaptation):
the baseline's BSD verb pair -- ``chflags`` reading ``stat -f %Sf`` flag *names*
like ``hidden`` -- does not exist on Linux, so the same set/clear capability
is expressed over ``lsattr -d`` attribute *letters* (read back through the
``FileFlags`` fact) and ``chattr +flag``/``-flag``. Here the ``i`` letter is
the immutable attribute: with it set the file cannot be modified, renamed or
removed even by its owner, until the flag is cleared again.

Two properties shape the script:

* The idempotence is verbatim the baseline's. A set yields only the requested
  letters *missing* from the current set and a clear only those *present*, so
  a request whose flags already match the file reports "already has 'i'
  set"/"clear" and sends nothing. ``flags`` is a tuple of one-letter strings
  (never a list) because request arguments must stay hashable across the two
  phases; each letter is validated against the ext2 vocabulary before it
  reaches the host.
* ``chattr`` needs ``CAP_LINUX_IMMUTABLE``, so every flags request carries
  ``sudo=True`` regardless of who owns the file. The deploy is deliberately
  **self-reversing** -- it sets ``i`` and then clears it in the same run --
  so a real execution leaves the marker exactly as it found it (not
  permanently immutable). It first creates the marker with ``files.file``
  because an absent path reads no flags, and a ``chattr`` on a missing file
  would fail at apply.

The marker lives in ``/tmp``, writable by any login user, so the create is
unprivileged; only the two ``chattr`` steps are elevated. ``files.file`` here is
a prerequisite, not a second demonstration of that operation.

Every request carries ``when=is_centos``, the declarative eligibility
guard: the predicate reads only **static** inventory data --
``host.group``, set by the ``"group"`` key of the inventory entry -- and
is a stable module-level function, so the prepare and apply phases reach
the same skip decision. The driver skips a rejected request without
materializing it, so on hosts outside the ``"centos"`` group neither the
commands nor the facts behind them run, and plan and log render the step
as ``[skipped: when guard is_centos]`` in its lockstep slot. The path is
resolved from ``__file__``, so the example works from any working
directory; point ``INVENTORY_PATH`` at your own file to target your
machines.

The path is resolved from ``__file__``, so the example works from any
working directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/files/files_flags.py
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
    files,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# The marker file the attribute is toggled on. Its /tmp location keeps the
# create unprivileged whatever account the inventory logs in as; chattr is
# elevated no matter the owner.
MARKER = "/tmp/reemote-flags.txt"

# The immutable attribute letter, as a *tuple* -- op() hashes every argument,
# so a list would be rejected. Each element is validated against the ext2
# vocabulary (``_LINUX_FLAG_LETTERS``) before any command is planned.
IMMUTABLE = ("i",)

# examples/modules/files/files_flags.py -> the project root, so the inventory
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


def is_centos(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"centos"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision.
    """
    return host.group == "centos"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Create the marker, set the immutable flag, then clear it again.

    Branch-free and hashable: every request carries the same ``IMMUTABLE``
    tuple in both phases, so the prepare plan and the apply log pair step for
    step. The final clear makes the run self-reversing, so nothing stays
    immutable on the host once the deploy finishes.
    """
    yield op(
        files.file,
        name="Create marker",
        path=MARKER,
        when=is_centos,
    )
    yield op(
        files.flags,
        name="Set immutable",
        path=MARKER,
        flags=IMMUTABLE,
        present=True,
        sudo=True,
        when=is_centos,
    )
    yield op(
        files.flags,
        name="Clear immutable (restore)",
        path=MARKER,
        flags=IMMUTABLE,
        present=False,
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry plan first: the whole sequence computed against the current
        # host state, nothing sent.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Then the real apply: create, chattr +i, chattr -i. Each step's
        # fresh fact cache sees the previous step's effect, so the clear
        # really does find the flag set (in prepare it reads the not-yet-
        # created file and is planned as a no-op -- the documented
        # prepare-versus-apply difference).
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
        # The library already reports every host-level failure as a typed
        # error whose message names the host and the failing command. Print
        # that message -- no traceback -- and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
