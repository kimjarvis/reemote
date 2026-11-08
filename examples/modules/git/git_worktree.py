# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: ensure a linked git worktree on two real hosts.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``). Each plain ``Host``
lazily opens an asyncssh connection from its ``connection`` data, gathers
facts and runs commands for real, and ``run`` closes those connections when
it finishes.

``git.worktree`` mirrors pyinfra's ``git.worktree``: it manages a linked
working tree -- creating it from a main repository, pulling it, or removing it
(``present=False``). This script ensures a ``hotfix`` worktree checked out on
a new ``hotfix`` branch from the login user's ``/home/kjarvis/app`` repository
into ``/home/kjarvis/app-hotfix``. The demo hosts ship neither ``git`` nor
that repository, so ``main`` first drives a tiny *install deploy*
(``dnf.packages`` under ``sudo=True``, idempotent), and the deploy itself
opens with a ``git.repo`` request cloning the public
``https://github.com/octocat/Hello-World.git`` demo repository into
``/home/kjarvis/app`` -- the worktree's main repository. The operation
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
inventory ``privilege``, never the operation. pyinfra's ``user``/``group``
options and the ``chown`` commands they emit are deliberately dropped for the
same reason.

``run`` is a coroutine (both phases are async), so ``main`` is async and
driven by ``asyncio.run``.

Run it with::

    uv run python examples/modules/git/git_worktree.py
"""

import asyncio
import sys
from collections.abc import Generator

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    OperationRequest,
    PrivilegeError,
    dnf,
    format_log,
    format_plan,
    git,
    op,
    run,
)

# The inventory: real hosts and their SSH connection information, supplied
# by the application to the library. The default host_factory materializes
# each entry into a plain Host that connects over asyncssh.
INVENTORY = [
    {
        "host": "web1",
        "connection": {
            "address": "192.168.0.240",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
    {
        "host": "web2",
        "connection": {
            "address": "192.168.0.242",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
]


def install(host: Host) -> Generator[OperationRequest]:
    """Install the ``git`` binary the worktree deploy needs.

    Branch-free and idempotent: an already-installed package plans and
    applies as no change.
    """
    yield op(dnf.packages, name="Install git", packages=("git",), sudo=True)


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
        dest="/home/kjarvis/app",
        branch="master",
        pull=False,
    )
    yield op(
        git.worktree,
        name="Add the hotfix worktree",
        worktree="/home/kjarvis/app-hotfix",
        repo="/home/kjarvis/app",
        new_branch="hotfix",
        assume_repo_exists=True,
        pull=False,
    )
    yield op(
        git.worktree,
        name="Add the hotfix worktree",
        worktree="/home/kjarvis/app-hotfix",
        repo="/home/kjarvis/app",
        new_branch="hotfix",
        assume_repo_exists=True,
        pull=False,
    )


async def main() -> None:
    try:
        # Prerequisite run: put git on every host before the worktree deploy
        # clones the main repository or shells out to the binary.
        await run(INVENTORY, install)

        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
        plans, report, _hosts = await run(INVENTORY, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
