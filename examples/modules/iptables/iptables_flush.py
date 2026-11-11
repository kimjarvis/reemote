# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: flush a demo chain's rules on real CentOS hosts.

This is a *usage* example, not part of the library. It drives reemote's
two-phase execution model end to end over a **real SSH transport**
(asyncssh):

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with ``validate_inventory``
   so a malformed file fails with an ``InventoryError`` naming the
   offending entry, *before* any host is materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects. Every request is flagged ``sudo=True``: iptables mutations
   need CAP_NET_ADMIN, so the commands ride under root.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``iptables.flush`` is idempotent on the ``IptablesRules`` fact: a chain
(or, with ``chain=None``, a whole table) that already holds no rules is
a no-op, so a flush never shows changed twice. Only rules are flushed --
chain policies survive (``iptables.policy`` manages those) and the user
chain itself survives (``iptables.chain`` with ``present=False`` deletes
it). The deploy composes the full lifecycle on a dedicated demo chain:
create ``REEMOTE_DEMO``, seed one rule into it, flush the chain, then
flush it again -- the second flush's fresh per-step fact cache sees an
empty chain and reports no rules to flush.

**This example is compile-checked by the test suite, not executed
against the shared demo hosts**: even a chain-scoped flush is a firewall
mutation an operator should opt into. The documentation-net source
(``192.0.2.0/24``, RFC 5737) keeps the seeded rule inert.

The demo hosts ship without ``iptables`` or ``ipset``, so the deploy
opens with an idempotent ``dnf.packages`` install under ``sudo=True``.
The iptables facts are exit-masked, so the prepare phase reads them even
before the binary is present; in apply the install runs first and the
mutations that follow ride the freshly installed binary.

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

    uv run python examples/modules/iptables/iptables_flush.py
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
    dnf,
    format_log,
    format_plan,
    iptables,
    op,
    run,
    validate_inventory,
)

# examples/modules/iptables/iptables_flush.py -> the project root, so the inventory
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
    """Create a demo chain, seed a rule, then flush the chain twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    Every iptables mutation needs CAP_NET_ADMIN, so every request is
    flagged ``sudo=True`` and rides under root.
    """
    yield op(
        dnf.packages,
        name="Install iptables and ipset",
        packages=("iptables", "ipset"),
        sudo=True,
        when=is_centos,
    )
    yield op(
        iptables.chain,
        name="Create the REEMOTE_DEMO chain",
        chain="REEMOTE_DEMO",
        sudo=True,
        when=is_centos,
    )
    yield op(
        iptables.rule,
        name="Seed a demo rule",
        chain="REEMOTE_DEMO",
        jump="DROP",
        source="192.0.2.0/24",
        comment="reemote-demo",
        sudo=True,
        when=is_centos,
    )
    yield op(
        iptables.flush,
        name="Flush the REEMOTE_DEMO chain",
        chain="REEMOTE_DEMO",
        sudo=True,
        when=is_centos,
    )
    yield op(
        iptables.flush,
        name="Flush the REEMOTE_DEMO chain (again)",
        chain="REEMOTE_DEMO",
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