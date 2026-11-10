# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: copy a remote file into a remote directory with files.copy.

This is a *usage* example, not part of the library. It shows ``files.copy``
(baseline ``files.copy`` parity): ``src`` is a remote path, ``dest`` is the
remote *directory* to copy it into, and the copied path becomes
``dest/basename(src)`` -- with the source left in place, the difference from
``files.move``. With ``overwrite=True`` the copy is forced (``cp -r -f``);
without it an existing target with *equal* content is a no-op and one with
different content raises ``ValueError``.

Two properties shape the script:

* Unlike the stateless ``files.move``, ``files.copy`` has an idempotent
  branch: an existing regular-file target whose sha1 matches the source
  reports "already exists" with no command. This script uses
  ``overwrite=True`` so a target left by a previous run is simply
  overwritten; drop the flag and the second script run becomes a no-op.
* An absent source or an absent/non-directory destination raises
  ``ValueError`` in **both** phases -- reemote5 operations are phase-unaware,
  so the baseline's soft plan-time logging of those cases is deliberately dropped
  (the same strictness as ``files.move``). A "create the file then copy it"
  flow therefore cannot share one deploy: the prepare phase would raise on
  the not-yet-created source. That is why this script spans two complete
  runs, the sanctioned split also shown in
  ``examples/demonstrations/composition/two_deploy_split.py``.

Run 1 stages the payload with ``files.put`` (an unprivileged scp transfer of
a module-level stream -- one stable identity across both phases) into
``/tmp``. Run 2 copies it into ``/var/tmp``, leaving the staging file in
``/tmp``. Both directories exist on every host and are writable by any login
user, so the example depends on no account's home directory existing and
nothing here is elevated; for a root-owned destination directory such as
``/etc`` the copy request would carry ``sudo=True``.

Every request carries ``when=is_centos``, the declarative eligibility
guard: the predicate reads only **static** inventory data --
``host.group``, set by the ``"group"`` key of the inventory entry -- and
is a stable module-level function, so the prepare and apply phases reach
the same skip decision. The driver skips a rejected request without
materializing it, so on hosts outside the ``"centos"`` group neither the
commands nor the facts behind them run, and plan and log render the step
as ``[skipped: when guard is_centos]`` in its lockstep slot. The path is
resolved from ``__file__``, so the example works from any working
directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/files/files_copy.py
"""

import asyncio
import io
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
PAYLOAD = io.StringIO("staged by the reemote5 files.copy example\n")

# Staging source under /tmp and the destination directory /var/tmp: its
# basename makes the copied path /var/tmp/reemote5-copy.txt. Two distinct
# world-writable directories that exist on every host, so the copy has a real
# destination other than its source and no home directory is assumed.
STAGED = "/tmp/reemote5-copy.txt"
DEST_DIR = "/var/tmp"

# examples/modules/files/files_copy.py -> the project root, so the inventory
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


def stage(host: Host) -> Generator[OperationRequest]:
    """Run 1: stage the payload into /tmp, unprivileged, branch-free."""
    yield op(files.put, name="Stage payload", src=PAYLOAD, dest=STAGED, when=is_centos)


def install(host: Host) -> Generator[OperationRequest]:
    """Run 2: copy the staged file into the destination directory.

    ``overwrite=True`` forces the copy over a target left by a previous
    script run (``cp -r -f``); the staging file stays in /tmp.
    """
    yield op(
        files.copy,
        name="Copy payload to dest",
        src=STAGED,
        dest=DEST_DIR,
        overwrite=True,
        when=is_centos,
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

        # Run 2: the source now really exists, so the copy's validation
        # passes in the prepare phase too. --dry first, then the real copy.
        plans, _, _hosts = await run(inventory, install, dry=True)
        print("\n== Run 2 plan: copy payload (--dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(inventory, install)
        print("\n== Run 2 log: copy payload ==")
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