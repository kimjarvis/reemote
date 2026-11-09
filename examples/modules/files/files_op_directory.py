# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: ensure a web-root directory on real CentOS hosts with files.directory.

This is a *usage* example, not part of the library. It drives the
``files.directory`` operation through reemote5's two-phase execution model:

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with ``validate_inventory``
   so a malformed file fails with an ``InventoryError`` naming the
   offending entry, *before* any host is materialized or connected.
2. Write a *deploy generator* that yields ``op(...)`` requests -- the
   ``www`` account steps, then the ``files.directory`` ones. The generator
   is plain sync code -- it only describes work, it never touches a host.
3. Call ``run(inventory, deploy, dry=True)`` for the prepare phase: the
   operation is materialized against each host's real pre-deployment state
   (read-only fact gathering over SSH) and ``format_plan`` shows what would
   change. Nothing is sent.
4. Call ``run(inventory, deploy)`` for the real run: prepare again, then
   apply in lockstep across hosts, with ``format_log`` showing every
   command and whether it changed anything.

The operation is idempotent: it reads the ``Directory`` fact once and
yields only the commands that close a gap. An absent path is created with
``mkdir -p``, then ``chmod``/``chown`` when ``mode``/``user``/``group`` are
requested; an existing directory re-runs only the ``chmod``/``chown`` whose
values differ from the fact's metadata. The deploy first ensures the
``www`` group and user, then ensures the same directory twice to prove
idempotence: the second directory request is a guaranteed no-op because
the first request's ``mkdir``/``chmod``/``chown`` are visible
to it through the fresh per-step fact cache -- exactly the "act, then read"
property the lockstep apply phase provides.

``/srv/web`` and the ``www`` ownership are root-owned, so both requests
carry ``sudo=True``: the driver wraps every shell in ``sudo`` (the log
shows ``$ sudo mkdir -p ...``). The ``www`` account is not assumed to
exist: the deploy's first two requests ensure it with ``server.group`` and
``server.user`` (a system account with a nologin shell and no home), so
the ``chown`` never fails on an unknown user -- both steps are no-ops once
the account is there. The mode is ``755``, not ``750``: the facts read
*unprivileged*, so the web root must stay traversable by the login user
(and by the ``files.file`` sibling example's ``File`` fact, which manages
a config file inside it). pyinfra also offers ``force``/``force_backup`` here;
reemote5 keeps ``force`` (a non-directory is removed with ``rm -rf`` and
recreated; without it such a path raises ``ValueError``) but deliberately
drops the timestamped backup ``mv``, whose ``datetime.now()`` would differ
between the phases.

Unlike the pytest suite -- which injects the test framework's
``SimulatedHost`` via ``run``'s ``host_factory`` and never touches the
network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection, stats the directory for real, and runs the elevated shells when
a gap needs closing.

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

    uv run python examples/modules/files/files_op_directory.py
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
    server,
    validate_inventory,
)

# The web-root directory to ensure: root-owned path and ownership, so the
# requests are elevated. The ``www`` owner is provisioned by the deploy's
# first two requests. The mode is 755 so the unprivileged facts (and the
# files_op_file.py sibling) can traverse it.
WEB_DIR = "/srv/web"

# examples/modules/files/files_op_directory.py -> the project root, so the inventory
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
    """Ensure the www account, then the same directory twice for idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first two requests provision the ``www`` group and system user the
    directory requests chown to -- elevated, and no-ops once the account
    exists. The directory requests are elevated too: ``/srv/web`` and the
    ``www`` ownership are root-owned. The octal mode spelling ``"755"``
    normalises to the int the ``Directory`` fact reports, so a repeat reads
    as already in state; ``755`` (not ``750``) keeps the directory
    traversable by the login user, whose unprivileged fact reads need it.
    """
    yield op(
        server.group,
        name="Ensure www group",
        group="www",
        system=True,
        sudo=True,
        when=is_centos,
    )
    yield op(
        server.user,
        name="Ensure www user",
        user="www",
        group="www",
        system=True,
        shell="/sbin/nologin",
        ensure_home=False,
        sudo=True,
        when=is_centos,
    )
    yield op(
        files.directory,
        name="Ensure web dir",
        path=WEB_DIR,
        mode="755",
        user="www",
        group="www",
        sudo=True,
        when=is_centos,
    )
    yield op(
        files.directory,
        name="Re-ensure web dir",
        path=WEB_DIR,
        mode="755",
        user="www",
        group="www",
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The directory is still stat'ed over
        # SSH (read-only) so the plan reflects each host's real
        # pre-deployment state; no command is sent.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. The account
        # steps create www when missing; the first directory request runs
        # mkdir/chmod/chown as needed; the second sees the directory in the
        # desired state and reports no change.
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
