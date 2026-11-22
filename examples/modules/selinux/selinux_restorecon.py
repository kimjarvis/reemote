# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Relabel a tree from the fcontext database with ``selinux.restorecon``.

This is a *usage* example, not part of the library. It shows how an
application drives reemote's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Read ``inventory.json`` from the current working directory and parse it as JSON -- an
   inventory is just a list of dictionaries, which is exactly what JSON expresses --
   then validate it up front with ``validate_inventory`` so a malformed file fails with
   an ``InventoryError`` naming the offending entry, *before* any host is materialized
   or connected.
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

``selinux.restorecon`` is a reemote original (the baseline ships no restorecon
operation): it reapplies the persistent fcontext database to existing
files -- the documented remedy for ``selinux.file_context``'s limitation
that ``chcon`` labels are runtime-only and a relabel reverts them, and for
``selinux.file_context_mapping``'s limitation that a database rule never
touches labels files already carry. Here ``/var/www/html`` is relabelled
recursively (``restorecon -R``, the default ``recurse=True``);
``force=True`` would add ``-F``, resetting even the user-customizable
user/role/level components, not just the type.

The operation is deliberately **stateless**: reemote has no fact that
compares a whole tree's labels against the database (that would mean
walking every file), so every request yields its ``restorecon`` and
reports a change -- in the prepare plan *and* in the apply log, on the
first run and on every re-run. The script yields the request twice to
make that contract visible: unlike the fact-driven operations, the second
request is *not* a no-op. restorecon itself is harmless to re-run (it only
writes labels that differ), so the always-changed report is honest about
what the tool cannot know in advance. Pair it *after* a mapping step when
the relabel must reflect a rule the same deploy just wrote (see
``examples/modules/selinux/selinux_op_file_context_mapping.py``).

``restorecon`` writes root-owned labels, so both requests are flagged
``sudo=True`` and the driver elevates their commands over SSH.

Every request carries ``when=is_redhat``, the declarative eligibility guard: the
predicate reads only **static** inventory data -- ``host.family``, set by the
``\"family\"`` key of the inventory entry -- and is a stable module-level function, so
the prepare and apply phases reach the same skip decision. The driver skips a rejected
request without materializing it, so on hosts outside the ``"RedHat"`` family neither
the commands nor the facts behind them run, and plan and log render the step as
``[skipped: when guard is_redhat]`` in its lockstep slot. The inventory is read from
``inventory.json`` in the current working directory; edit it there to target your
machines.

Run it with::

    uv run python examples/modules/selinux/selinux_restorecon.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path

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

# The tree to relabel: httpd's document root, recursively.
RELABEL_PATH = "/var/www/html"

def is_redhat(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"RedHat"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision.
    """
    return host.family == "RedHat"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Relabel /var/www/html -- twice, to show the stateless contract.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    Both requests yield their restorecon and report a change: the operation
    has no fact to compare against, so -- unlike the fact-driven operations
    -- the second request is *not* a no-op. Re-running restorecon is
    harmless (it only writes labels that differ).

    ``restorecon`` writes root-owned labels, so both requests are flagged
    ``sudo=True`` and the driver elevates their command over SSH.
    """
    yield op(
        selinux.restorecon,
        name="Relabel /var/www/html from the database",
        path=RELABEL_PATH,
        recurse=True,
        sudo=True,
        when=is_redhat,
    )
    yield op(
        selinux.restorecon,
        name="Relabel /var/www/html again (still changed)",
        path=RELABEL_PATH,
        recurse=True,
        sudo=True,
        when=is_redhat,
    )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
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