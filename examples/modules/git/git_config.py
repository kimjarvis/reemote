# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: ensure the login user's global git identity on two real hosts.

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

``git.config`` mirrors pyinfra's ``git.config``: it ensures a configuration
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

Run it with::

    uv run python examples/modules/git/git_config.py
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
    """Install the ``git`` binary the config deploy (and its fact) needs.

    Branch-free and idempotent: an already-installed package plans and
    applies as no change.
    """
    yield op(dnf.packages, name="Install git", packages=("git",), sudo=True)


def deploy(host: Host) -> Generator[OperationRequest]:
    """Ensure the login user's global git ``user.name``.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The same request is yielded twice: the apply phase's fresh per-step fact
    cache sees the first write, so the second converges to a no-op.
    """
    yield op(git.config, name="Set the global user name", key="user.name", value="Kim Jarvis")
    yield op(git.config, name="Set the global user name", key="user.name", value="Kim Jarvis")


async def main() -> None:
    try:
        # Prerequisite run: the GitConfig fact shells out to git, so the
        # binary must exist before the config deploy's prepare phase reads
        # it. Installing first keeps that fact read from raising
        # CommandNotFoundError on a host that ships without git.
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
