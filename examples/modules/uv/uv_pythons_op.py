# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install and remove a uv-managed Python interpreter with ``uv python``.

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

``uv.pythons`` is reemote's port of pyinfra's ``uv.pythons``: it installs or
removes uv-managed interpreter versions, one ``uv python install``/``uv python
uninstall`` command per version. The operation is idempotent on the ``uv.Pythons``
fact (``uv python list --only-installed --output-format=json``), and -- unlike
pyinfra, which keys its comparison on the exact version string so a ``"3.13"``
request never matches an installed ``"3.13.4"`` and reinstalls forever -- reemote
compares dotted components: a request is satisfied when its components are a
prefix of some installed version, so ``"3.13"`` converges once any ``3.13.*``
interpreter is present. ``managed=True`` (the default) counts only the entries the
fact flagged uv-managed as installed; ``managed=False`` counts every interpreter
present. The script yields the *same* install request twice and the *same*
removal request twice to show that idempotence across the two phases: prepare
plans both halves of each pair as changes, while apply runs the first and reports
the second as a no-op against the fresh per-step fact cache. Installing and then
removing the interpreter makes the example *self-restoring*.

Each version rides through to the shell shell-quoted (``uv python install
--no-progress 3.13``), and installs/removals write to uv's own download
directory, so they are unprivileged -- no request carries ``sudo``. Like the
sibling uv examples, every request carries ``when=is_ubuntu``: the shared
``inventory.json`` holds one host in the ``"Ubuntu"`` family alongside hosts in
other families, so the run acts on the Ubuntu host and skips every other one --
a host outside the group has each request skipped without materializing it, so
neither the uv commands nor the ``Pythons`` fact behind them runs, and plan and
log render ``[skipped: when guard is_ubuntu]``.

This example provisions uv itself before deploying: ``main`` first awaits
``provision_uv`` from the sibling ``provision_uv.py`` (an idempotent
``snap.package`` install of Astral's ``astral-uv`` snap), because Ubuntu's noble
apt suite ships no ``uv`` package and the module drops pyinfra's
``requires_command`` gating -- a host without uv would exit 127 on the fact read
and raise ``CommandNotFoundError`` before a single change was planned.

Run it with::

    uv run python examples/modules/uv/uv_pythons_op.py
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

# The interpreter version to install and remove again -- a partial request that
# shows reemote's dotted-component match: it is present once any ``3.13.*``
# interpreter is installed. A module-level tuple, so the request kwargs stay
# hashable and identical across the two phases.
VERSIONS = ("3.13",)


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
    """Install the interpreter -- twice, to show idempotence -- then remove it.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases. The first request installs whatever uv does not yet list; the second
    carries the same arguments and is a no-op in the apply phase once the first
    download landed it. The third removes the interpreter again
    (``present=False``) and the fourth is its no-op repeat, leaving uv's download
    directory as found so the shared-host sweep is not polluted.

    uv installs to its own per-user download directory, so no request needs
    elevation.
    """
    yield op(
        uv.pythons,
        name="Install Python 3.13",
        versions=VERSIONS,
        when=is_ubuntu,
    )
    yield op(
        uv.pythons,
        name="Re-run install (no-op)",
        versions=VERSIONS,
        when=is_ubuntu,
    )
    yield op(
        uv.pythons,
        name="Remove Python 3.13 again",
        versions=VERSIONS,
        present=False,
        when=is_ubuntu,
    )
    yield op(
        uv.pythons,
        name="Re-run removal (no-op)",
        versions=VERSIONS,
        present=False,
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Install uv on the Ubuntu hosts before anything below reads or mutates
        # interpreter state, so the example runs even on a host where uv was
        # never installed (see provision_uv.py).
        print("\n== Provisioning uv (provision_uv.py) ==")
        await provision_uv(inventory)

        # --dry: prepare phase only. Facts are gathered over SSH (the
        # ``uv python list`` read is read-only) so the plan reflects each host's
        # real pre-deployment state; no mutating command is sent.
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
