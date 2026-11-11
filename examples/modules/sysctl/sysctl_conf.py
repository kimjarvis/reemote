# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Manage a whole sysctl drop-in file with ``sysctl.conf``.

This is a *usage* example, not part of the library. It shows how an
application drives reemote's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with ``validate_inventory``
   so a malformed file fails with an ``InventoryError`` naming the
   offending entry, *before* any host is materialized or connected.
2. Build the deploy script as a sync, branch-free generator yielding
   ``OperationRequest`` objects via ``op``.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``), opening real asyncssh
connections that ``run`` closes when it finishes.

``sysctl.conf`` is a reemote original for **bulk persistence**: the whole
file's content is the rendered ``settings`` pairs, one ``key = value`` line
each in the given order, written only when it differs from what the
``SysctlFile`` fact reads back (the ``systemd.dropin`` whole-file model, not
``sysctl.setting``'s keyed edit -- stale lines are replaced, not kept). The
default target is the ``/etc/sysctl.d/99-reemote.conf`` drop-in, so the
legacy ``/etc/sysctl.conf`` and other packages' drop-ins stay untouched.
``settings`` is a **tuple of pairs**, not a dict: ``op()`` rejects unhashable
request arguments. A changed write is followed by a scoped
``sysctl -p <sysctl_file>`` reload so the settings apply at runtime
immediately (``reload=False`` defers them to the next boot);
``present=False`` removes the file instead, leaving runtime values alone.

The write follows the ``systemd.dropin`` spool pattern: an **unprivileged**
scp transfer spools the rendered text into a dest-derived ``/tmp`` directory, then an
**elevated** ``sh -c`` install creates ``/etc/sysctl.d`` if needed, copies
the file into place ``0644`` and removes the spool. Only the shell steps are
escalated -- the transfer itself never is -- so the request is flagged
``sudo=True`` while the file lands root-owned.

The script yields the *same* request twice to show idempotence across the two
phases: the prepare phase plans both as changes when the file is absent
(nothing is sent in prepare), while the apply phase writes on the first and
reports the second as a no-op, because the fresh per-step fact cache now sees
the content the first step installed.

**This operation writes root-owned configuration and reloads kernel
settings.** It is compile-checked and exercised by the offline example
harness rather than run against shared hosts; point ``INVENTORY_PATH`` at your own
throwaway machines to execute it for real.

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

Run it with::

    uv run python examples/modules/sysctl/sysctl_conf.py
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
    format_log,
    format_plan,
    op,
    run,
    sysctl,
    validate_inventory,
)

# The settings the drop-in holds: an immutable module-level tuple of pairs,
# so the same hashable value is compared and written in both phases (a dict
# or list would be rejected at op() construction).
SETTINGS = (
    ("vm.swappiness", 10),
    ("net.ipv4.ip_forward", 1),
    ("fs.inotify.max_user_watches", 524288),
)

# examples/modules/sysctl/sysctl_conf.py -> the project root, so the inventory
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
    """Manage the kernel-tuning drop-in -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases; the operation's own content comparison decides what changes.
    """
    yield op(
        sysctl.conf,
        name="Install kernel tuning drop-in",
        settings=SETTINGS,
        sudo=True,
        when=is_centos,
    )
    yield op(
        sysctl.conf,
        name="Re-install kernel tuning drop-in (no-op)",
        settings=SETTINGS,
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Facts are gathered over SSH (read-only) so
        # the plan reflects each host's real state; no mutating command is sent.
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
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())