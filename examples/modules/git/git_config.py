# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: ensure the login user's global git identity on real CentOS hosts.

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

``git.config`` mirrors the baseline's ``git.config``: it ensures a configuration
item at the repository, user or system level. This script sets the login
user's *global* ``user.name`` -- no ``repo``, so the ``--global`` scope --
comparing against the ``GitConfig`` fact and writing only when the current
value differs, so an already-correct config is a no-op reporting
``git config user.name is set to Kim Jarvis``. The deploy yields the *same*
request twice: in the prepare phase (where nothing is sent) both plan as
changes, while in the apply phase the first write is visible to the second
through the fresh per-step fact cache, so the second is a guaranteed no-op --
the idempotence the fact comparison provides.

The demo hosts ship without ``git``, and the ``GitConfig`` fact itself
shells out to the binary -- an unmasked read that raises
``CommandNotFoundError`` during prepare. So ``main`` first drives a tiny
*install deploy* (``dnf.packages`` under ``sudo=True``, idempotent) to put
``git`` on every host, and only then runs the config deploy below. This is
the same two-run split ``two_deploy_split.py`` demonstrates: facts that
need prerequisite software must be read after that software is installed.

Writing the login user's own ``--global`` config needs no root, so the
config requests are unprivileged. A ``repo``-scoped ``--local`` write into a
root-owned checkout, or a ``system=True`` write into the machine-wide config,
would instead be flagged ``sudo=True`` so the driver elevates the command;
``multi_value=True`` would add a value with ``--add`` for a key that carries
several (such as ``safe.directory``).

``run`` is a coroutine (both phases are async), so ``main`` is async and
driven by ``asyncio.run``.

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

    uv run python examples/modules/git/git_config.py
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
    git,
    op,
    run,
    validate_inventory,
)

# examples/modules/git/git_config.py -> the project root, so the inventory
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


def install(host: Host) -> Generator[OperationRequest]:
    """Install the ``git`` binary the config deploy (and its fact) needs.

    Branch-free and idempotent: an already-installed package plans and
    applies as no change.
    """
    yield op(
        dnf.packages,
        name="Install git",
        packages=("git",),
        sudo=True,
        when=is_centos,
    )


def deploy(host: Host) -> Generator[OperationRequest]:
    """Ensure the login user's global git ``user.name``.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The same request is yielded twice: the apply phase's fresh per-step fact
    cache sees the first write, so the second converges to a no-op.
    """
    yield op(
        git.config,
        name="Set the global user name",
        key="user.name",
        value="Kim Jarvis",
        when=is_centos,
    )
    yield op(
        git.config,
        name="Set the global user name",
        key="user.name",
        value="Kim Jarvis",
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Prerequisite run: the GitConfig fact shells out to git, so the
        # binary must exist before the config deploy's prepare phase reads
        # it. Installing first keeps that fact read from raising
        # CommandNotFoundError on a host that ships without git.
        await run(inventory, install)

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
