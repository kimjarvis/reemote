# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: clone a git repository on two real hosts.

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

``git.repo`` mirrors pyinfra's ``git.repo``: it clones a fresh repository or
reconciles an existing one. This script ensures the login user's
``/home/kjarvis/deploy.git`` repository cloned from
``git@github.com:acme/deploy.git`` on the ``main`` branch. The operation
branches on whether ``dest/.git`` is a directory (read through the git-local
``_DirectoryExists`` fact), so a missing repository yields one
``git clone --branch main <src> <dest>`` command, while an existing one walks
the reconcile path -- comparing ``origin`` against ``src`` via ``GitConfig``,
the current branch against the requested one via ``GitBranch`` and, when the
two match, the local commit against the remote tip via ``GitLocalCommit``/
``GitRemoteBranchCommit`` -- and issues no commands at all.

The deploy yields the *same* request twice, with ``pull=False`` so the
idempotence is purely the clone/existence comparison. In the prepare phase
(where nothing is sent) both plan as the clone, while in the apply phase the
first clone is visible to the second through the fresh per-step fact cache:
``dest/.git`` now exists, ``origin`` matches ``src`` and the branch is already
``main``, so the second reports no commands -- a guaranteed no-op. (Left at
the default ``pull=True``, the second request would additionally compare the
local and remote commits for the effective branch and only short-circuit when
they already match.)

Cloning writes under ``dest``, which is often owned by another user; flag the
request ``sudo=True`` when the login user cannot write there -- the run-as
user and its password come from the inventory ``privilege``, never the
operation. pyinfra's ``user``/``group`` options, its ``ssh_keyscan``
preflight and its leading ``files.directory`` ensure-directory step are
deliberately dropped: reemote5 has no ``chown`` primitive or ``ssh``
namespace, and there is no ``files.directory`` operation to reach across
namespaces, so the clone names ``dest`` itself instead of cloning into ``.``
from inside a pre-created directory.

``run`` is a coroutine (both phases are async), so ``main`` is async and
driven by ``asyncio.run``.

Run it with::

    uv run python examples/modules/git/git_repo.py
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


def deploy(host: Host) -> Generator[OperationRequest]:
    """Ensure the ``deploy`` repository is cloned into the login user's home.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The same request is yielded twice: the apply phase's fresh per-step fact
    cache sees the first clone, so with ``pull=False`` the second converges
    to a no-op -- ``dest/.git`` present, ``origin`` reconciled and ``main``
    already checked out.
    """
    yield op(
        git.repo,
        name="Clone the deploy repository",
        src="git@github.com:acme/deploy.git",
        dest="/home/kjarvis/deploy",
        branch="main",
        pull=False,
    )
    yield op(
        git.repo,
        name="Clone the deploy repository",
        src="git@github.com:acme/deploy.git",
        dest="/home/kjarvis/deploy",
        branch="main",
        pull=False,
    )


async def main() -> None:
    try:
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
