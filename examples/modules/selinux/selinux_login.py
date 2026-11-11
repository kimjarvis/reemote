# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Map a Linux login to a confined SELinux user with ``selinux.login``.

This is a *usage* example, not part of the library. It shows how an
application drives reemote's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with ``validate_inventory``
   so a malformed file fails with an ``InventoryError`` naming the
   offending entry, *before* any host is materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``). Each plain ``Host``
lazily opens an asyncssh connection from its ``connection`` data, gathers
facts and runs commands for real, and ``run`` closes those connections when
it finishes.

``run`` is a coroutine (both phases are async), so ``main`` is async and
driven by ``asyncio.run``.

``selinux.login`` is a reemote original (the baseline ships no login
operation): it manages the Linux-user -> SELinux-user mapping, the core of
least-privilege confined-user management. Here the login ``kjarvis`` is
mapped to ``staff_u`` with range ``s0`` -- the confined-administrator user
whose roles (``staff_r``, ``sysadm_r``) the ``SEUser`` fact reports -- so
the account stops landing in the unconfined domain the ``__default__``
fallback assigns. With ``present=True`` (the default) an unmapped login is
added (``semanage login -a``) and an existing mapping is modified in place
(``-m``) when its user or range differs; ``present=False`` removes the
mapping (``-d``), returning the login to the fallback.

Idempotence reads the ``SELogin`` fact and compares both the SELinux user
*and* the MLS/MCS range (the Service column newer semanage releases print
is ignored -- the operation never manages it). The script yields the *same*
request twice: the prepare phase plans both as changes when the mapping is
absent or different, while the apply phase writes on the first and reports
the second as a no-op through the fresh per-step fact cache. The mapping
takes effect at the user's *next* login -- existing sessions keep the
context they started with.

``semanage login`` writes the root-owned policy store, so both requests are
flagged ``sudo=True`` and the driver elevates their commands over SSH.

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

    uv run python examples/modules/selinux/selinux_login.py
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
    selinux,
    validate_inventory,
)

# The mapping to ensure: the login user confined to staff_u at range s0.
# Shared by both requests below so they carry identical operation kwargs
# (only the log ``name`` differs), which makes the second a guaranteed
# no-op in the apply phase.
LOGIN_USER = "kjarvis"
LOGIN_SEUSER = "staff_u"
LOGIN_RANGE = "s0"

# examples/modules/selinux/selinux_login.py -> the project root, so the inventory
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
    """Map kjarvis to staff_u -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request adds (or modifies) the mapping; the second carries
    the same arguments and is a guaranteed no-op in the apply phase,
    because the first mapping is already visible through the fresh
    per-step fact cache.

    ``semanage login`` writes the policy store, so both requests are
    flagged ``sudo=True`` and the driver elevates their command over SSH.
    """
    yield op(
        selinux.login,
        name="Map kjarvis to staff_u",
        user=LOGIN_USER,
        seuser=LOGIN_SEUSER,
        mls_range=LOGIN_RANGE,
        sudo=True,
        when=is_centos,
    )
    yield op(
        selinux.login,
        name="Re-map kjarvis to staff_u (no-op)",
        user=LOGIN_USER,
        seuser=LOGIN_SEUSER,
        mls_range=LOGIN_RANGE,
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