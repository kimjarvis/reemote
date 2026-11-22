# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: move a remote file to a new name with files.move.

This is a *usage* example, not part of the library. It shows ``files.move``
with the issue #22 rename/metadata parity: ``src`` is a remote path, ``dest``
is either an existing remote *directory* (the moved path becomes
``dest/basename(src)``) or -- as here -- a full file path, so the move
**renames**. ``mode``/``user``/``group`` are applied to the moved path in
the same request. With ``overwrite=True`` an existing target is removed
first (``rm -rf``) before the ``mv``; without it an existing target raises
``ValueError``.

Two properties shape the script:

* ``files.move`` is **stateless** like the baseline: a valid move always yields a
  command, there is no idempotent no-op branch (for an idempotent
  remote-to-remote install use ``files.copy`` with ``overwrite=True``, whose
  overwrite is sha1-gated). Re-running a move whose source is gone fails
  fail-fast (here ``overwrite=True`` keeps the *target* side re-runnable;
  the source is re-staged by run 1 each time).
* An absent source or an absent/non-directory rename parent raises
  ``ValueError`` in **both** phases -- reemote operations are phase-unaware,
  so the baseline's soft plan-time logging of those cases is deliberately dropped.
  A "create the file then move it" flow therefore cannot share one deploy:
  the prepare phase would raise on the not-yet-created source. That is why
  this script spans two complete runs, the sanctioned split also shown in
  ``examples/demonstrations/composition/two_deploy_split.py``.

Run 1 stages the payload with ``files.put`` (an unprivileged scp transfer of
a module-level stream -- one stable identity across both phases) into
``/tmp``. Run 2 moves it to a *renamed* file under ``/var/tmp`` with mode
``0644``. Both directories exist on every host and are writable by any login
user, so the example depends on no account's home directory existing and
nothing here is elevated; for a root-owned destination such as ``/etc`` the
move request would carry ``sudo=True`` (and ``user``/``group`` would name
the owning account); for whole-content root-owned writes ``files.install``
covers the same ground in one deploy (see ``files_install.py``).

Every request carries ``when=is_redhat``, the declarative eligibility
guard: the predicate reads only **static** inventory data --
``host.family``, set by the ``\"family\"`` key of the inventory entry -- and
is a stable module-level function, so the prepare and apply phases reach
the same skip decision. The driver skips a rejected request without
materializing it, so on hosts outside the ``"RedHat"`` family neither the
commands nor the facts behind them run, and plan and log render the step
as ``[skipped: when guard is_redhat]`` in its lockstep slot.

The path is resolved from ``__file__``, so the example works from any
working directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/files/files_move.py
"""

import asyncio
import io
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

# The payload, created once on the control machine: a stream, never a local
# path. The same object is passed to put in every phase, so the prepare and
# apply request sequences compare equal.
PAYLOAD = io.StringIO("staged by the reemote files.move example\n")

# Staging source under /tmp and a full file path under /var/tmp: the dest
# is not an existing directory, so the move renames the payload to
# /var/tmp/reemote-move-renamed.txt. Two distinct world-writable directories
# that exist on every host, so the move has a real destination other than
# its source and no home directory is assumed.
STAGED = "/tmp/reemote-move.txt"
DEST = "/var/tmp/reemote-move-renamed.txt"

# examples/modules/files/files_move.py -> the project root, so the inventory
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


def is_redhat(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"RedHat"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision.
    """
    return host.family == "RedHat"


def stage(host: Host) -> Generator[OperationRequest]:
    """Run 1: stage the payload into /tmp, unprivileged, branch-free."""
    yield op(files.put, name="Stage payload", src=PAYLOAD, dest=STAGED, when=is_redhat)


def install(host: Host) -> Generator[OperationRequest]:
    """Run 2: move the staged file to its renamed target with mode 0644.

    ``overwrite=True`` removes a target left by a previous script run before
    the mv, and the requested ``chmod`` rides the same request; the move
    itself is stateless and always plans a command.
    """
    yield op(
        files.move,
        name="Move payload to renamed dest",
        src=STAGED,
        dest=DEST,
        mode="0644",
        overwrite=True,
        when=is_redhat,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Run 1: --dry plan, then the real staging transfer.
        plans, _, _hosts = await run(inventory, stage, dry=True)
        print("== Run 1 plan: stage payload (--dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(inventory, stage)
        print("\n== Run 1 log: stage payload ==")
        assert report is not None
        print(format_log(report))

        # Run 2: the source now really exists, so the move's validation
        # passes in the prepare phase too. --dry first, then the real move.
        plans, _, _hosts = await run(inventory, install, dry=True)
        print("\n== Run 2 plan: move payload (--dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(inventory, install)
        print("\n== Run 2 log: move payload ==")
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