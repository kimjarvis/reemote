# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: the in-deploy guard rejects an action called inside a deploy.

This is a *usage* example, not part of the library. It demonstrates the rule
that makes actions safe to keep beside the deploy model: an action CANNOT run
inside a deploy, and the library enforces it rather than trusting convention.

``illegal_deploy`` below calls ``gather_action`` from inside its generator
body. The moment that call executes, ``gather_action`` raises
``ActionInDeployError`` -- synchronously, at the call site, before any
coroutine is created. Three properties of the deploy model demand this:

1. A deploy generator is re-invoked once per phase (prepare and apply), so an
   action inside it would run TWICE.
2. The prepare phase must never send anything, so an action there would mutate
   the host mid-plan and poison every later fact read.
3. The determinism guard pairs requests by value, and an immediate action has
   no plan-phase counterpart to pair with.

The guard is a ``ContextVar`` the drivers set while ``run_prepare`` /
``run_apply`` run, so the check fires in BOTH phases and resets cleanly
afterwards. ``op(Shell, ...)`` -- wrapping the action class as if it were an
operation -- is refused too: ``materialize`` requires an async-generator
operation and raises ``OperationError`` for anything else.

This script needs NO real host: the guard fires during prepare, before any
fact is gathered or any connection opened, so it runs offline. ``main`` treats
the ``ActionInDeployError`` as the EXPECTED outcome (the demonstration
succeeded) and exits zero. The correct pattern -- calling ``gather_action``
before, between or after deploys -- is shown in ``deploy_action_deploy.py``.

Run it with::

    uv run python examples/actions/action_in_deploy_guard.py
"""

import asyncio
import sys
from collections.abc import Generator

from reemote5 import (
    ActionInDeployError,
    Host,
    OperationRequest,
    Shell,
    dnf,
    gather_action,
    op,
    run,
)

# A real-looking inventory. No connection is ever opened: the guard fires in
# the prepare phase before any fact read, so this example runs offline.
INVENTORY = [
    {
        "host": "web1",
        "connection": {
            "address": "192.168.0.240",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
]


def illegal_deploy(host: Host) -> Generator[OperationRequest]:
    """A deploy that misuses an action -- the call below raises immediately.

    The ``yield`` is never reached: ``gather_action`` raises
    ``ActionInDeployError`` the instant it is called inside the running deploy,
    so the generator body aborts before producing any request.
    """
    gather_action([host], Shell, command="uname -r")
    yield op(dnf.packages, name="Install htop", packages=("htop",), sudo=True)


async def main() -> None:
    try:
        # dry=True stops after prepare -- but the guard fires during prepare
        # itself, before anything is sent or even connected.
        await run(INVENTORY, illegal_deploy, dry=True)
    except ActionInDeployError as exc:
        # The EXPECTED outcome: the guard rejected the in-deploy action.
        print("The guard correctly rejected the in-deploy action:")
        print(f"  {exc}")
        print("\nCall gather_action before, between or after deploys instead.")
        return

    # Reaching here means the guard failed to fire -- a bug, not a demo.
    print("ERROR: expected ActionInDeployError was not raised", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
