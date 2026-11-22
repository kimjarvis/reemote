# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: copy a remote file to a new name with files.copy.

This is a *usage* example, not part of the library. It shows ``files.copy``
with the Ansible ``copy remote_src: true`` parity (issue #22): ``src`` is a
remote path, ``dest`` is either an existing remote *directory* (the copied
path becomes ``dest/basename(src)``) or -- as here -- a full file path, so
the copy **renames**. ``mode``/``user``/``group`` converge the target's
metadata in the same request, and ``overwrite=True`` is **sha1-gated**: an
existing target with equal content is a no-op, a drifted one is overwritten
once and then converges (Ansible's ``force: yes`` idempotence). Without
``overwrite`` an existing target with different content raises
``ValueError``.

Two properties shape the script:

* The idempotent branch is content *and* metadata: an existing regular-file
  target whose sha1 matches the source skips the copy, and only a
  ``chmod``/``chown`` whose requested value differs from the ``File`` fact's
  row is issued. Run 3 repeats run 2's deploy to show the literal no-op --
  every request reports no change.
* An absent source or an absent/non-directory rename parent raises
  ``ValueError`` in **both** phases -- reemote operations are phase-unaware,
  so the baseline's soft plan-time logging of those cases is deliberately dropped
  (the same strictness as ``files.move``). A "create the file then copy it"
  flow therefore cannot share one deploy: the prepare phase would raise on
  the not-yet-created source. That is why this script spans two complete
  runs, the sanctioned split also shown in
  ``examples/demonstrations/composition/two_deploy_split.py``.

Run 1 stages the payload with ``files.put`` (an unprivileged scp transfer of
a module-level stream -- one stable identity across both phases) into
``/tmp``. Run 2 copies it to a *renamed* file under ``/var/tmp`` with mode
``0644``, leaving the staging file in ``/tmp``; run 3 repeats the copy and
converges to a no-op. Both directories exist on every host and are writable
by any login user, so the example depends on no account's home directory
existing and nothing here is elevated; for a root-owned destination such as
a PHP module under ``/usr/lib`` the copy request would carry ``sudo=True``
(and ``user``/``group`` would name the owning account).

Every request carries ``when=is_redhat``, the declarative eligibility
guard: the predicate reads only **static** inventory data --
``host.family``, set by the ``\"family\"`` key of the inventory entry -- and
is a stable module-level function, so the prepare and apply phases reach
the same skip decision. The driver skips a rejected request without
materializing it, so on hosts outside the ``"RedHat"`` family neither the
commands nor the facts behind them run, and plan and log render the step
as ``[skipped: when guard is_redhat]`` in its lockstep slot. The path is
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
PAYLOAD = io.StringIO("staged by the reemote files.copy example\n")

# Staging source under /tmp and a full file path under /var/tmp: the dest
# is not an existing directory, so the copy renames the payload to
# /var/tmp/reemote-copy-renamed.txt (the Ansible copy remote_src shape).
# Two distinct world-writable directories that exist on every host, so the
# copy has a real destination other than its source and no home directory
# is assumed.
STAGED = "/tmp/reemote-copy.txt"
DEST = "/var/tmp/reemote-copy-renamed.txt"

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
    """Run 2 (and run 3): copy the staged file to its renamed target.

    One request renames, sets mode ``0644`` and converges on drift:
    ``overwrite=True`` is sha1-gated, so a target left by a previous script
    run with equal content is a no-op and a drifted one is overwritten
    once. The staging file stays in /tmp.
    """
    yield op(
        files.copy,
        name="Copy payload to renamed dest",
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

        # Run 2: the source now really exists, so the copy's validation
        # passes in the prepare phase too. --dry first, then the real copy.
        plans, _, _hosts = await run(inventory, install, dry=True)
        print("\n== Run 2 plan: copy payload (--dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(inventory, install)
        print("\n== Run 2 log: copy payload ==")
        assert report is not None
        print(format_log(report))

        # Run 3: the same deploy again -- the target's sha1 and mode now
        # match, so the sha1-gated overwrite reports no change.
        plans, _, _hosts = await run(inventory, install, dry=True)
        print("\n== Run 3 plan: repeat copy (--dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(inventory, install)
        print("\n== Run 3 log: repeat copy (no-op) ==")
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