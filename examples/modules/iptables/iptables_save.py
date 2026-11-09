# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: persist the live iptables rules to disk on real CentOS hosts.

This is a *usage* example, not part of the library. It drives reemote5's
two-phase execution model end to end over a **real SSH transport**
(asyncssh):

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with ``validate_inventory``
   so a malformed file fails with an ``InventoryError`` naming the
   offending entry, *before* any host is materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects. Every request is flagged ``sudo=True``: writing under
   ``/etc`` needs root, and the descriptor wraps the redirect in
   ``sh -c`` so the elevation covers the write (a bare
   ``sudo iptables-save > /etc/...`` would open the redirect as the
   login user).
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``iptables.save`` is idempotent on the ``IptablesPersistence`` fact: it
compares the live ``iptables-save`` output against the snapshot file
with comment lines stripped (the save header's timestamp changes every
run, so raw equality would always drift) and writes only when they
differ. The default path is ``/etc/iptables/rules.v4`` -- the
iptables-persistent location, so a host running
netfilter-persistent reloads exactly these rules at boot. Pass
``path=...`` for another snapshot file, ``version=6`` for the IPv6
family (default ``rules.v6``). Reloading at boot is the distro service's
job, not this operation's; uploading a snapshot *from* the controller is
``files.put`` + ``iptables.restore`` (see ``iptables_restore.py``).

Note the save overwrites the snapshot with whatever is live *now* --
run it after the rule operations that should survive the reboot, as the
last step of a firewall deploy.

The deploy yields the same save request twice: in the prepare phase
(where nothing is sent) both plan as changes, but in the apply phase the
second request's fresh per-step fact cache sees the first write and
reports the rules already match the file.

The demo hosts ship without ``iptables``, so the deploy opens with an
idempotent ``dnf.packages`` install under ``sudo=True``. The snapshot
path is ``/etc/sysconfig/iptables`` -- the RHEL-family location whose
parent directory already exists. ``iptables.save`` redirects into the
file but does not create a missing parent, so the Debian-style default
``/etc/iptables/rules.v4`` would fail on these hosts; passing ``path=``
selects a snapshot directory that is already present.

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

    uv run python examples/modules/iptables/iptables_save.py
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
    iptables,
    op,
    run,
    validate_inventory,
)

# examples/modules/iptables/iptables_save.py -> the project root, so the inventory
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
    """Save the live rules to the RHEL snapshot twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    The write targets /etc/sysconfig/iptables, so every request is flagged
    ``sudo=True`` and the sh -c-wrapped redirect rides under root.
    """
    yield op(
        dnf.packages,
        name="Install iptables and ipset",
        packages=("iptables", "ipset"),
        sudo=True,
        when=is_centos,
    )
    yield op(
        iptables.save,
        name="Persist the live iptables rules",
        path="/etc/sysconfig/iptables",
        sudo=True,
        when=is_centos,
    )
    yield op(
        iptables.save,
        name="Persist the live iptables rules (again)",
        path="/etc/sysconfig/iptables",
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

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
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())