# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Create and remove a virtualenv with ``uv venv``.

This is a *usage* example, not part of the library. It drives reemote's
two-phase execution model end to end over a **real SSH transport** (asyncssh):

1. Read the target hosts from ``inventory.json`` in the current working
   directory and validate it up front with ``validate_inventory``, so a
   malformed file fails with an ``InventoryError`` before any host is
   materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects ``SimulatedHost`` via ``run``'s
``host_factory`` and never touches the network -- this example uses the *default*
factory (``Host.from_inventory``); each ``Host`` lazily opens an asyncssh
connection and ``run`` closes it when the deploy finishes.

``uv.venv`` is reemote's port of pyinfra's ``uv.venv``: it creates (or, with
``present=False``, removes) a virtualenv with ``uv venv``. The operation is
idempotent on a private existence probe -- a guarded ``test -e <path>/bin/activate``
-- so a venv that already exists is simply converged and no command is sent. The
script yields the *same* create request twice and the *same* removal request
twice to show that idempotence across the two phases: prepare plans both halves of
each pair as changes, while apply runs the first and reports the second as a
no-op against the fresh per-step fact cache. Creating and then removing the venv
makes the example *self-restoring*.

``path`` is treated as the venv directory itself throughout -- the probe, the
``uv venv <path>`` create and the ``rm -rf <path>`` removal all point at the same
location -- which corrects pyinfra's own mismatch (it keyed existence on
``<path>/.venv/bin/activate`` yet created the venv *at* ``<path>``). The create
here passes ``seed=True`` (bootstrap ``pip`` into the venv) and a base
``python="3.13"``, rendered as ``uv venv --seed --python 3.13 <path>``.
``allow_existing`` and ``clear_existing`` are mutually exclusive and, together
with an empty ``path``, are rejected before the fact read so a bad request fails
without an SSH round trip; neither is used here.

The venv lives in the SSH user's own writable ``/tmp`` path, so both the create
and the removal are unprivileged -- no request carries ``sudo``; creating under a
root-owned parent would need it. Like the sibling uv examples, every request
carries ``when=is_ubuntu``, so the run acts only on the Ubuntu demo host and skips
the rest.

This example provisions uv itself before deploying -- ``main`` awaits
``provision_uv`` from the sibling ``provision_uv.py`` -- because the existence
probe's ``uv venv`` and the fact commands both need uv on the PATH, and Ubuntu's
noble apt suite ships no ``uv`` package.

Run it with::

    uv run python examples/modules/uv/uv_venv.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path

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
    uv,
    validate_inventory,
)

# The uv provisioning helper sits beside this script; add its directory to
# sys.path so ``import provision_uv`` resolves whether the example is run by
# path or imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from provision_uv import provision_uv

# The virtualenv directory to create and remove again -- a per-user ``/tmp`` path
# so the whole cycle is unprivileged and self-restoring. A module-level constant,
# so the request kwargs stay hashable and identical across the two phases.
VENV_PATH = "/tmp/reemote-uv-example-venv"

# The base interpreter to build the venv from, passed as ``uv venv --python``.
BASE_PYTHON = "3.13"


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``), never a mutable fact,
    and stays a stable module-level function -- the two rules every ``when``
    predicate follows so both deploy phases reach the same eligibility decision
    and the determinism guard holds. The Ubuntu demo host is the one
    ``provision_uv.py`` installs uv on before this example deploys.
    """
    return host.family == "Ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Create the venv -- twice, to show idempotence -- then remove it.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases. The first request creates the venv where the existence probe finds
    none; the second carries the same arguments and is a no-op in the apply phase
    once the first created it. The third removes the whole tree again
    (``present=False``) and the fourth is its no-op repeat, leaving the host as
    found so the shared-host sweep is not polluted.

    The venv sits in the user's own ``/tmp`` path, so no request needs elevation.
    """
    yield op(
        uv.venv,
        name="Create the venv",
        path=VENV_PATH,
        python=BASE_PYTHON,
        seed=True,
        when=is_ubuntu,
    )
    yield op(
        uv.venv,
        name="Re-run create (no-op)",
        path=VENV_PATH,
        python=BASE_PYTHON,
        seed=True,
        when=is_ubuntu,
    )
    yield op(
        uv.venv,
        name="Remove the venv again",
        path=VENV_PATH,
        present=False,
        when=is_ubuntu,
    )
    yield op(
        uv.venv,
        name="Re-run removal (no-op)",
        path=VENV_PATH,
        present=False,
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Install uv on the Ubuntu hosts before deploying, so the ``uv venv``
        # command and the existence probe both run against a real tool (see
        # provision_uv.py).
        print("\n== Provisioning uv (provision_uv.py) ==")
        await provision_uv(inventory)

        # --dry: prepare phase only. The existence probe is read-only, so the
        # plan reflects each host's real pre-deployment state; no create or
        # removal command is sent.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library reports every host-level failure as a typed error whose
        # message names the host and the failing command. Print it and exit
        # non-zero -- no traceback.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
