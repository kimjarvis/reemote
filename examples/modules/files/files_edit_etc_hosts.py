# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: manage an /etc/hosts entry with files.edit + files.move.

This is a *usage* example, not part of the library. It is reemote5's answer
to pyinfra's bespoke ``server.etc_hosts`` operation: no per-format editor is
ported, because the generic ``files.edit`` content-transform primitive plus
the elevated ``files.move`` cover the task.

The obstacle is privilege: ``files.edit`` writes through ``files.put``'s
**unprivileged** scp transfer, so it cannot write root-owned ``/etc/hosts``
directly. The recipe therefore spools and installs:

1. A fact SNAPSHOT: ``gather_fact`` reads ``FileContents("/etc/hosts")``
   from every guarded host (a read-only SSH ``cat``). On the control
   machine the pure ``ensure_db_host`` transform computes each host's
   desired content -- the same line-keyed-by-IP edit pyinfra's
   ``server.etc_hosts`` performs, comments on the edited line dropped,
   every other line preserved. Hosts whose content already matches are
   skipped entirely: the script-level idempotence pyinfra gets from its
   ``EtcHosts`` fact short-circuit lives here, in the gap between runs.
2. Run 1 spools: ``files.edit`` writes the desired content to ``/tmp/hosts``
   (login-user-writable). Each host's transform is built ONCE by the
   ``make_transform`` factory closing over the frozen snapshot -- a stable
   callable across both phases, exactly the determinism-safe split shown in
   ``examples/demonstrations/composition/two_deploy_split.py``.
3. Run 2 installs: ``files.move`` moves ``/tmp/hosts`` into ``/etc`` with
   ``overwrite=True``, flagged ``sudo=True`` -- ``rm -rf /etc/hosts`` then
   ``mv /tmp/hosts /etc``, both elevated shell commands. The run-as user and
   any password come from the inventory ``privilege`` data, never the op.

Why two runs and not one deploy: ``files.move`` raises ``ValueError`` on an
absent source in *both* phases (reemote5 operations are phase-unaware, so
pyinfra's soft plan-time logging is deliberately dropped). In a single
deploy the prepare phase would raise on the spool file the edit has not
created yet -- prepare never sends. Splitting the runs puts a real barrier
between "spool exists" and "move validates it".

Re-running the whole script is a no-op once every host's hosts file matches:
the snapshot comparison skips both runs. The ``mv`` itself is stateless
(pyinfra parity) -- it always plans a command when it runs at all.

The hosts come from the shared ``inventory.json`` in the project root,
read with ``load_inventory`` (parse + ``validate_inventory``) and resolved
from ``__file__`` so the example works from any working directory. The
snapshot gather is guarded by ``is_centos`` (``host.group == "centos"``):
``gather_fact`` takes no ``when`` parameter, so the predicate filters the
host list before the fan-out and hosts outside the ``"centos"`` group are
named on a ``== Skipped (when guard is_centos): ... ==`` line and never
connected. Every deploy request also carries ``when=is_centos``, and only
hosts with a snapshot entry can become pending.

Run it with::

    uv run python examples/modules/files/files_edit_etc_hosts.py
"""

import asyncio
import json
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from reemote5 import (
    CommandError,
    FileContents,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    files,
    format_log,
    format_plan,
    gather_fact,
    op,
    run,
    validate_inventory,
)

# The entry to ensure, keyed by IP exactly like pyinfra's server.etc_hosts.
DB_IP = "10.0.0.5"
DB_LINE = "10.0.0.5 db.internal db"

# The spool path: login-user-writable, and its basename ("hosts") is what
# files.move appends to the destination directory, landing /etc/hosts.
SPOOL_PATH = "/tmp/hosts"

# Per-host spool transforms, filled ONCE in main between the snapshot and
# run 1: each is a stable callable closing over frozen content, so both
# phases of run 1 pass the identical object (OperationRequest compares the
# transform by value; a fresh closure per phase would raise DeterminismError).
SPOOL_TRANSFORMS: dict[str, Callable[[str | None], str]] = {}

# examples/modules/files/files_edit_etc_hosts.py -> the project root, so
# the inventory is found whatever the current working directory is. No
# ``privilege`` entry is needed: the elevated move assumes passwordless
# sudo for the login user; a password would come from ``privilege`` in the
# inventory, never the op.
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


def ensure_db_host(current: str | None) -> str:
    """Pure, idempotent hosts-file edit: ensure the line for ``DB_IP``.

    Drops any existing line keyed by the IP (comments on that line are not
    preserved, pyinfra parity), appends the desired line, keeps every other
    line untouched. A fixpoint: applying it to its own output changes nothing.
    """
    text = current or ""
    kept = [
        line
        for line in text.splitlines()
        if line.split("#", 1)[0].split()[:1] != [DB_IP]
    ]
    kept.append(DB_LINE)
    return "\n".join(kept) + "\n"


def make_transform(content: str) -> Callable[[str | None], str]:
    """Build a spool transform returning the frozen desired ``content``.

    Called once per host between the snapshot and run 1; the returned
    callable ignores the spool file's current text (the desired content was
    already computed from the real /etc/hosts) and is trivially a fixpoint.
    """

    def transform(current: str | None) -> str:
        return content

    return transform


def spool(host: Host) -> Generator[OperationRequest]:
    """Run 1: write the desired content to the writable spool path."""
    yield op(
        files.edit,
        name="Spool hosts file",
        path=SPOOL_PATH,
        transform=SPOOL_TRANSFORMS[host.name],
        when=is_centos,
    )


def install(host: Host) -> Generator[OperationRequest]:
    """Run 2: move the spool file over /etc/hosts, elevated."""
    yield op(
        files.move,
        name="Install hosts file",
        src=SPOOL_PATH,
        dest="/etc",
        overwrite=True,
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # The snapshot: read every guarded host's real /etc/hosts
        # (read-only cat). gather_fact has no ``when`` parameter, so the
        # is_centos predicate filters the host list before the fan-out --
        # a skipped host is never connected.
        all_hosts = [Host.from_inventory(entry) for entry in inventory]
        hosts = [host for host in all_hosts if is_centos(host)]
        skipped = [host.name for host in all_hosts if host not in hosts]
        if skipped:
            print("\n== Skipped (when guard is_centos): " + ", ".join(skipped) + " ==")
        try:
            contents = await gather_fact(hosts, FileContents, "/etc/hosts")
        finally:
            for host in hosts:
                await host.aclose()

        # Controller-side idempotence: compute each host's desired content
        # from the frozen snapshot and skip hosts that already match.
        pending = []
        for entry in inventory:
            name = entry["host"]
            # Only hosts with a snapshot entry (the guarded ones) can
            # become pending; a skipped host has no contents key.
            if name not in contents:
                continue
            current = contents[name]
            desired = ensure_db_host(current)
            if desired != current:
                SPOOL_TRANSFORMS[name] = make_transform(desired)
                pending.append(entry)
        if not pending:
            print("All hosts already carry the desired /etc/hosts entry.")
            return

        # Run 1: spool the transformed content to /tmp/hosts, unprivileged.
        plans, _, _hosts = await run(pending, spool, dry=True)
        print("== Run 1 plan: spool hosts file (--dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(pending, spool)
        print("\n== Run 1 log: spool hosts file ==")
        assert report is not None
        print(format_log(report))

        # Run 2: the spool file now really exists, so the move's validation
        # passes in the prepare phase too. Elevated: /etc is root-owned.
        plans, _, _hosts = await run(pending, install, dry=True)
        print("\n== Run 2 plan: install hosts file (--dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(pending, install)
        print("\n== Run 2 log: install hosts file ==")
        assert report is not None
        print(format_log(report))
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema.
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