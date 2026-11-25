# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: the in-deploy guard rejects an action called inside a deploy.

This is a *usage* example, not part of the library. It demonstrates the rule
that makes actions safe to keep beside the deploy model: an action CANNOT run
inside a deploy, and the library enforces it rather than trusting convention.

``illegal_deploy`` below calls ``gather_action`` from inside its generator
body. The moment that call executes, ``gather_action`` raises
``ActionInDeployError`` -- synchronously, at the call site, before any
coroutine is created. Three properties of ``run()``'s two-phase model demand
this:

1. A deploy generator is re-invoked once per phase (prepare and apply), so an
   action inside it would run TWICE.
2. The prepare phase must never send anything, so an action there would mutate
   the host mid-plan and poison every later fact read.
3. The determinism guard pairs requests by value, and an immediate action has
   no plan-phase counterpart to pair with.

Only the *behaviour* is universal; these three *reasons* are two-phase
artifacts. ``run_waves`` pulls a body once, so reason 1's re-invocation and
reason 3's plan-phase pairing do not exist on the wave path, and reason 2
inverts -- each wave is applied as it is planned. The guard still fires all the
same: the wave loop keeps the ``ContextVar`` set around every pull, so a direct
``gather_action(...)`` in any body, ``run_waves`` included, raises
``ActionInDeployError``. What the wave path adds is a sanctioned *in-body*
action -- ``yield act(Shell, ...)`` -- so an action is no longer confined to
before, between and after deploys there.

The guard is a ``ContextVar`` the drivers set while ``run_prepare`` /
``run_apply`` run, so the check fires in BOTH phases and resets cleanly
afterwards. ``op(Shell, ...)`` -- wrapping the action class as if it were an
operation -- is refused too: ``materialize`` requires an async-generator
operation and raises ``OperationError`` for anything else.

This script needs NO real host: the guard fires during prepare, before any fact is
gathered or any connection opened, so it runs offline. The inventory is still read from
``inventory.json`` in the current working directory and validated with
``validate_inventory``, and the ``is_redhat`` family guard is composed onto the deploy
with ``sub_deploy(..., when=is_redhat)`` like every other example -- but the in-deploy
guard fires while the generator is drained, before any ``when`` skip is even considered,
so the demonstration is unchanged. ``main`` treats the ``ActionInDeployError`` as the
EXPECTED outcome (the demonstration succeeded) and exits zero. The correct pattern --
calling ``gather_action`` before, between or after deploys -- is shown in
``deploy_action_deploy.py``.

Run it with::

    uv run python examples/actions/action_in_deploy_guard.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path

from reemote import (
    ActionInDeployError,
    Host,
    InventoryError,
    OperationRequest,
    Shell,
    dnf,
    gather_action,
    op,
    run,
    sub_deploy,
    validate_inventory,
)


# The shared inventory in the current working directory. No connection is ever
# opened: the guard fires in the prepare phase before any fact read, so this
# example runs offline.
def is_redhat(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"RedHat"`` OS family.

    Reads only the host's static ``family`` field. It never gets to act here:
    the in-deploy guard aborts the generator before any request is skipped or
    materialized.
    """
    return host.family == "RedHat"


def illegal_deploy(host: Host) -> Generator[OperationRequest]:
    """A deploy that misuses an action -- the call below raises immediately.

    The ``yield`` is never reached: ``gather_action`` raises
    ``ActionInDeployError`` the instant it is called inside the running deploy,
    so the generator body aborts before producing any request.
    """
    gather_action([host], Shell, command="uname -r")
    yield op(dnf.packages, name="Install htop", packages=("htop",), sudo=True)


# The family guard, composed as in every other example -- the in-deploy guard
# still fires first, while the generator is drained.
illegal_deploy = sub_deploy(illegal_deploy, when=is_redhat)


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file itself is missing or not valid JSON.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The file parsed, but an entry violates the inventory schema.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    try:
        # dry=True stops after prepare -- but the guard fires during prepare
        # itself, before anything is sent or even connected.
        await run(inventory, illegal_deploy, dry=True)
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
