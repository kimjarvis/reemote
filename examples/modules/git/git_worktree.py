# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: ensure a linked git worktree on real CentOS hosts.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
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

``git.worktree`` mirrors the baseline's ``git.worktree``: it manages a linked
working tree -- creating it from a main repository, pulling it, or removing it
(``present=False``). This script ensures a ``hotfix`` worktree checked out on
a new ``hotfix`` branch from the ``/tmp/reemote5-app`` repository
into ``/tmp/reemote5-app-hotfix`` -- /tmp paths, writable by whatever
account the inventory logs in as. The demo hosts ship neither ``git`` nor
that repository, so ``main`` first drives a tiny *install deploy*
(``dnf.packages`` under ``sudo=True``, idempotent), and the deploy itself
opens with a ``git.repo`` request cloning the public
``https://github.com/octocat/Hello-World.git`` demo repository into
``/tmp/reemote5-app`` -- the worktree's main repository. The operation
branches on the tree's existence, read through the git-local
``_DirectoryExists`` fact, so an absent tree yields one
``cd <repo> && git worktree add -b hotfix <worktree>`` command
while an already-present tree does nothing. ``assume_repo_exists=True`` skips
the ``repo/.git`` validity probe (the repository is known to be there);
``new_branch`` creates the branch alongside the tree.

The deploy yields the *same* request twice, with ``pull=False``. In the prepare
phase (where nothing is sent) both plan as the add, while in the apply phase
the first add is visible to the second through the fresh per-step fact cache:
the tree now exists, and with pulling disabled the second reports ``pull is
disabled`` -- a guaranteed no-op. This is the idempotence the existence
comparison provides. (Left at the default ``pull=True``, the second request
would instead try to update the tree from its tracking branch, short-circuiting
only when the local commit already equals the remote tip.)

Creating a worktree writes metadata under the repository's ``.git/worktrees``;
when the login user cannot write there, flag the request ``sudo=True`` so the
driver elevates the command -- the run-as user and its password come from the
inventory ``privilege``, never the operation. The baseline's ``user``/``group``
options and the ``chown`` commands they emit are deliberately dropped for the
same reason.

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

    uv run python examples/modules/git/git_worktree.py
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
    git,
    op,
    run,
    validate_inventory,
)

# examples/modules/git/git_worktree.py -> the project root, so the inventory
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
    """Install the ``git`` binary the worktree deploy needs.

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
    """Clone the main repository, then ensure the ``hotfix`` worktree.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The worktree request is yielded twice: the apply phase's fresh per-step
    fact cache sees the first add, so with ``pull=False`` the second
    converges to a no-op.
    """
    yield op(
        git.repo,
        name="Clone the main app repository",
        src="https://github.com/octocat/Hello-World.git",
        dest="/tmp/reemote5-app",
        branch="master",
        pull=False,
        when=is_centos,
    )
    yield op(
        git.worktree,
        name="Add the hotfix worktree",
        worktree="/tmp/reemote5-app-hotfix",
        repo="/tmp/reemote5-app",
        new_branch="hotfix",
        assume_repo_exists=True,
        pull=False,
        when=is_centos,
    )
    yield op(
        git.worktree,
        name="Add the hotfix worktree",
        worktree="/tmp/reemote5-app-hotfix",
        repo="/tmp/reemote5-app",
        new_branch="hotfix",
        assume_repo_exists=True,
        pull=False,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Prerequisite run: put git on every host before the worktree deploy
        # clones the main repository or shells out to the binary.
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
