# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: atomically restore an uploaded rule snapshot on real CentOS hosts.

This is a *usage* example, not part of the library. It drives reemote's
two-phase execution model end to end over a **real SSH transport**
(asyncssh):

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with ``validate_inventory``
   so a malformed file fails with an ``InventoryError`` naming the
   offending entry, *before* any host is materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects: first ``files.put`` uploads the snapshot (unprivileged scp
   into ``/tmp``), then ``iptables.restore`` loads it under
   ``sudo=True``.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``iptables.restore`` is the atomic bulk-apply surface: iptables-restore
applies the whole file or fails without partial state -- the resolution
of the ``bulk_rule(rules=[...])`` proposal, which cannot exist because
``op()`` arguments must be hashable. The path is HOST-side (reemote
transfers are IO-only), so a controller-side snapshot rides ``files.put``
first: the source here is a module-level ``io.BytesIO``, seekable and
identity-stable across phases (a fresh stream inside the generator would
trip the determinism guard). The restore is idempotent on the
``IptablesPersistence`` fact: when the file already reproduces the live
rules, loading it changes nothing and is skipped.

**This example is compile-checked by the test suite, not executed
against the shared demo hosts**: a restore with the default
``noflush=False`` replaces the entire live rule set. The snapshot below
keeps INPUT's policy ACCEPT and an explicit SSH ACCEPT rule so a real
run could not lock the deploy out, but replacing a production firewall
is always an operator decision.

The deploy yields the restore twice. In the prepare phase the snapshot
file does not exist yet (the put has not run), so both restores plan as
changes; in the apply phase the first restore loads the uploaded file
and the second's fresh per-step fact cache sees live rules matching it
and reports no change.

The demo hosts ship without ``iptables``, so the deploy opens with an
idempotent ``dnf.packages`` install under ``sudo=True``; the
``IptablesPersistence`` fact is exit-masked, so prepare reads it even
before the binary is present and the restore rides the freshly installed
``iptables-restore``.

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

    uv run python examples/modules/iptables/iptables_restore.py
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
    dnf,
    files,
    format_log,
    format_plan,
    iptables,
    op,
    run,
    validate_inventory,
)

# The snapshot in iptables-restore format, created once on the control
# machine: the same BytesIO object is passed to the put in every phase
# (OperationRequest compares streams by identity). The rule set keeps
# INPUT permissive and SSH explicitly allowed.
RULES_V4 = io.BytesIO(
    b"*filter\n"
    b":INPUT ACCEPT [0:0]\n"
    b":FORWARD ACCEPT [0:0]\n"
    b":OUTPUT ACCEPT [0:0]\n"
    b"-A INPUT -p tcp -m tcp --dport 22 -j ACCEPT\n"
    b"COMMIT\n"
)

# The host-side snapshot path: under /tmp, writable by whatever account
# the inventory logs in as, so the unprivileged scp transfer can write it;
# iptables-restore then reads it elevated through the sh -c wrapper.
REMOTE_RULES = "/tmp/reemote-rules.v4"

# examples/modules/iptables/iptables_restore.py -> the project root, so the inventory
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
    """Upload the snapshot, then restore it twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    The put is an unprivileged transfer (sudo/su do not apply to
    transfers); the restores need CAP_NET_ADMIN and ride ``sudo=True``.
    """
    yield op(
        dnf.packages,
        name="Install iptables and ipset",
        packages=("iptables", "ipset"),
        sudo=True,
        when=is_centos,
    )
    yield op(
        files.put,
        name="Upload the rule snapshot",
        src=RULES_V4,
        dest=REMOTE_RULES,
        when=is_centos,
    )
    yield op(
        iptables.restore,
        name="Restore the rule snapshot",
        path=REMOTE_RULES,
        sudo=True,
        when=is_centos,
    )
    yield op(
        iptables.restore,
        name="Restore the rule snapshot (again)",
        path=REMOTE_RULES,
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no content
        # is transferred and no command that mutates a host is sent.
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
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())