# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Upgrade every installed uv tool with ``uv tool upgrade --all``.

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

``uv.tools_upgrade_all`` is reemote's port of pyinfra's
``uv.tools_upgrade_all``: it runs ``uv tool upgrade --all`` to bring every
installed tool to its latest version. The operation is deliberately *stateless*
and *non-idempotent* -- like pyinfra's -- so it reads no fact, always reports a
change and always yields its one command; uv itself decides per tool whether an
upgrade exists. Because it never compares state, the plan shows the request as a
change and the apply phase sends it on every run -- there is no second no-op
request here, unlike the sibling ``uv.packages``/``uv.tools`` examples. The
optional ``extra_args`` (a single free-form string) rides unquoted onto the end
of the command; it is left off here.

Upgrading writes to uv's own tool directory, so the request is unprivileged --
no ``sudo``. Like the sibling uv examples, the request carries ``when=is_ubuntu``:
the shared ``inventory.json`` holds one host in the ``"Ubuntu"`` family alongside
hosts in other families, so the run acts on the Ubuntu host and skips the rest --
a skipped host has its request dropped without materializing, and plan and log
render ``[skipped: when guard is_ubuntu]``.

This example provisions uv itself before deploying -- ``main`` awaits
``provision_uv`` from the sibling ``provision_uv.py`` -- because Ubuntu's noble
apt suite ships no ``uv`` package and the module drops pyinfra's
``requires_command`` gating, so a host without uv would fail the command.

Run it with::

    uv run python examples/modules/uv/uv_tools_upgrade_all.py
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
    """Upgrade every installed uv tool -- once.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases. ``uv.tools_upgrade_all`` is stateless, so a single request is the
    whole deploy; unlike the idempotent ``uv.tools``/``uv.packages`` examples it
    is sent in the apply phase whatever the host already holds. Upgrading writes
    to uv's own tool directory, so the request needs no elevation.
    """
    yield op(
        uv.tools_upgrade_all,
        name="Upgrade all uv tools",
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Install uv on the Ubuntu hosts before deploying, so the bare
        # ``uv tool upgrade --all`` runs against a real tool (see
        # provision_uv.py).
        print("\n== Provisioning uv (provision_uv.py) ==")
        await provision_uv(inventory)

        # --dry: prepare phase only. The stateless operation still plans as a
        # change (it never reads a fact to compare against); nothing is sent.
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
