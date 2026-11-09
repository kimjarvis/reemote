# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: manage a symbolic link on real CentOS hosts with files.link.

This is a *usage* example, not part of the library. It drives the
``files.link`` operation through reemote5's two-phase execution model:

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with ``validate_inventory``
   so a malformed file fails with an ``InventoryError`` naming the
   offending entry, *before* any host is materialized or connected.
2. Write a *deploy generator* that yields ``op(files.link, ...)`` requests.
   The generator is plain sync code -- it only describes work, it never
   touches a host.
3. Call ``run(inventory, deploy, dry=True)`` for the prepare phase: the
   operation is materialized against each host's real pre-deployment state
   (read-only fact gathering over SSH) and ``format_plan`` shows what would
   change. Nothing is sent.
4. Call ``run(inventory, deploy)`` for the real run: prepare again, then
   apply in lockstep across hosts, with ``format_log`` showing every
   command and whether it changed anything.

The operation is idempotent: it reads the link's current target and
metadata through the ``Link`` fact (the stat/ls chain over SSH) and
yields commands only where they differ -- ``ln -s`` (plain ``ln`` with
``symbolic=False``) creates a missing link, a link pointing at another
target is removed (``rm -f``) and recreated (never ``ln -sf``),
``user``/``group`` ownership is edited with ``chown -h`` (the link
itself, never its target), and ``present=False`` removes it with
``rm -f``. A missing parent directory is ``mkdir -p``'d first unless
``create_remote_dir=False``, and a path held by a regular file or
directory raises unless ``force=True`` removes it first (pyinfra's
timestamped ``force_backup`` is deliberately dropped -- the
``datetime.now()`` in the ``mv`` would differ between the phases).
The deploy ensures the same link twice to prove it: the second request
is a guaranteed no-op because the first request's ``ln -s`` is visible
to it through the fresh per-step fact cache -- exactly the "act, then
read" property the lockstep apply phase provides.

Hard links (``symbolic=False``) need a second reading: the ``Link``
fact answers ``False`` for one, so the operation re-reads the path
through ``File`` and asks ``SameFile`` whether that file already *is*
the target. Link removals of regular files need ``force=True`` or
``files.file`` (see ``files_op_file.py``). The link lives in ``/tmp``,
writable by any login user, so no ``sudo``/``su`` elevation is needed;
``/tmp`` already exists, so ``create_remote_dir`` probes it and leaves it
alone. A root-owned link path would instead
carry ``sudo=True`` on the request.

Unlike the pytest suite -- which injects the test framework's
``SimulatedHost`` via ``run``'s ``host_factory`` and never touches the
network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection, reads the link for real, and runs ``ln -s`` (or the
``rm -f`` + ``ln -s`` relink pair) when it differs.

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

    uv run python examples/modules/files/files_op_link.py
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
    files,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# The link to manage: a /tmp path, writable by whatever account the inventory
# logs in as, so the unprivileged shell can create it, pointing at the classic
# timezone symlink target.
LINK_PATH = "/tmp/reemote5-link"
LINK_TARGET = "/etc/localtime"

# examples/modules/files/files_op_link.py -> the project root, so the inventory
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
    """Ensure the same link twice: the second request proves idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    Neither request is elevated: the link path is login-user writable.
    """
    yield op(
        files.link,
        name="Ensure link",
        path=LINK_PATH,
        target=LINK_TARGET,
        when=is_centos,
    )
    yield op(
        files.link,
        name="Re-ensure link",
        path=LINK_PATH,
        target=LINK_TARGET,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The current link target is still read
        # over SSH (read-only) so the plan reflects each host's real
        # pre-deployment state; no command is sent.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. The first
        # request runs ln -s -- or the rm -f + ln -s relink pair -- when
        # the link is missing or wrong; the second sees the corrected link
        # and reports no change.
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
        # The library already reports every host-level failure as a typed
        # error whose message names the host and the failing command. Print
        # that message -- no traceback -- and exit non-zero so the failure
        # stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
