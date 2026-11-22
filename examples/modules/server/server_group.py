# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: pin the docker and lusers group ids on real RedHat hosts.

This is a *usage* example, not part of the library. It shows reemote's
``server.group`` operation, baseline ``server.group`` parity for adding
and removing system groups extended with the Ansible ``group`` module's
``gid`` reconciliation (issue #62): the deploy creates the ``docker``
group (the baseline's documented example), a ``lusers`` system group with
a fixed gid, and pins the ``developers`` group to a specific gid, all
under ``sudo`` because ``groupadd``/``groupmod`` need root.

The operation is idempotent: it reads the ``Groups`` fact (the group names
in ``/etc/group``) and yields a ``groupadd`` only where the group is
absent, so a host that already has ``docker`` plans no change and a second
run is a no-op everywhere. ``system=True`` renders ``groupadd -r`` and
``gid=1500`` renders ``--gid 1500``; ``system`` only applies at creation,
but ``gid`` now reconciles too -- when the group is already present, the
``GroupDetails`` fact supplies its current numeric id, and a different,
known one is repaired with a ``groupmod -g`` rather than left drifted. An
unknown current gid (a malformed ``/etc/group`` row) is left alone rather
than guessed at, so the reconciliation never plans a change it cannot
justify; note that repairing a gid does not ``chown`` files still owned by
the old numeric id, the same limitation Ansible's ``group`` module has.
Removing a group is the mirror image: ``present=False`` yields
``groupdel`` only when the group is present.

Every failure mode of the run -- an unreachable host, a rejected elevation,
or a ``groupadd`` that exits non-zero (a gid already taken, for instance) --
reaches ``main`` as a typed error whose message already names the host, so
``main`` catches them and prints one line each instead of a traceback.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``): each plain ``Host``
lazily opens an asyncssh connection, reads ``/etc/group`` for real, and runs
``groupadd`` under sudo where needed.

Every request carries ``when=is_redhat``, the declarative eligibility
guard: the predicate reads only **static** inventory data --
``host.family``, set by the ``\"family\"`` key of the inventory entry -- and
is a stable module-level function, so the prepare and apply phases reach
the same skip decision. The driver skips a rejected request without
materializing it, so on hosts outside the ``"RedHat"`` family neither the
commands nor the facts behind them run, and plan and log render the step
as ``[skipped: when guard is_redhat]`` in its lockstep slot. The path is
resolved from ``__file__``, so the example works from any working
directory; point ``INVENTORY_PATH`` at your own file to target your
machines.

The path is resolved from ``__file__``, so the example works from any
working directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/server/server_group.py
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
    server,
    validate_inventory,
)

# examples/modules/server/server_group.py -> the project root, so the inventory
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


def deploy(host: Host) -> Generator[OperationRequest]:
    """Create the docker group, the lusers system group, then pin developers' gid.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases; whether each group already exists is decided inside the
    operation against the freshly gathered ``Groups`` fact, and whether a
    present group's gid has drifted is decided against ``GroupDetails``,
    per phase.
    """
    yield op(
        server.group,
        name="Create docker group",
        group="docker",
        sudo=True,
        when=is_redhat,
    )
    yield op(
        server.group,
        name="Create lusers system group",
        group="lusers",
        system=True,
        gid=1500,
        sudo=True,
        when=is_redhat,
    )
    yield op(
        server.group,
        name="Pin developers group gid",
        group="developers",
        gid=10000,
        sudo=True,
        when=is_redhat,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The group list is still gathered over SSH
        # (read-only) so the plan reflects each host's real pre-deployment
        # state; nothing is created.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. Step 1
        # creates docker where absent; step 2 creates lusers where absent;
        # step 3 reconciles developers' gid to 10000 only where the group is
        # present and its current gid differs, and plans no change otherwise.
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
    except HostConnectionError as exc:
        # A host could not be reached over SSH at all. Report it plainly --
        # naming every unreachable host -- instead of dumping an asyncssh
        # traceback, and exit non-zero so the failure is script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except PrivilegeError as exc:
        # The host was reached but rejected the ``sudo=True`` elevation -- a
        # wrong ``sudo_password``, or a run-as user the login user may not
        # become. One line naming the host; the detail stays on ``__cause__``.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except CommandError as exc:
        # A ``groupadd`` invocation exited non-zero (a gid already taken by
        # another group, for instance). The typed message already names the
        # host, the credential-free command and its stderr, so print just that.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())