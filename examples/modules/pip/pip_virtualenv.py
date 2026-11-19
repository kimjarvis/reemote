# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Create and remove a virtualenv with the ``virtualenv`` tool.

This is a *usage* example, not part of the library. It shows how an
application drives reemote's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Read the target hosts from ``inventory.json`` in the project root -- an
   inventory is just a list of dictionaries, each holding a host's name plus
   its SSH ``connection`` information (address, username and client key),
   which is exactly what JSON expresses -- and validate it up front with
   ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is
   materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's
``SimulatedHost`` via ``run``'s ``host_factory`` and never touches the
network -- this example uses the *default* factory (``Host.from_inventory``).
Each plain ``Host`` lazily opens an asyncssh connection from its
``connection`` data, gathers facts and runs commands for real, and ``run``
closes those connections when it finishes. The path is resolved from
``__file__``, so the example works from any working directory; point
``INVENTORY_PATH`` at your own file to target your machines.

``pip.virtualenv`` is reemote's port of the baseline's ``pip.virtualenv``:
it creates, reconciles or removes a virtualenv through the standalone
``virtualenv`` tool -- ``virtualenv -p python3 <path>`` here, since
``python="python3"`` names the base interpreter (the tool takes it as
``-p``; the stdlib sibling ``pip.venv`` runs ``<python> -m venv``
instead). The two operations share one reconciliation: idempotence reads a
private guarded probe of the virtualenv's *own* interpreter, so a missing
tree yields the create, an existing tree matching the requested
interpreter at ``major.minor`` is converged, and a mismatch yields a
remove-then-create pair -- the deliberate step beyond the baseline's
existence-only check, which makes a later ``python=`` change a silent
no-op (upstream bug #2028). ``site_packages=True`` and
``always_copy=True`` would render the tool's ``--system-site-packages``
and ``--always-copy`` flags.

The script yields the *same* create request twice and the *same* removal
request twice to show that idempotence across the two phases: the prepare
phase plans both halves of each pair as changes (nothing is sent yet),
while the apply phase runs the first and reports the second as a no-op,
because the fresh per-step fact cache re-reads the probe against the
updated state. Creating and then removing the same path makes the example
*self-restoring*: it leaves the host as it found it, which matters because
``run_examples.py`` sweeps every example over the same shared hosts.

No request here needs ``sudo``: the path is the SSH user's own writable
``/tmp/reemote-pip-virtualenv-example`` tree, so the create, the probe
reads and the ``rm -rf`` removal all run unprivileged (creating under a
root-owned parent would be the case for flagging the request ``sudo=True``).
Like the other pip-facing examples, every request carries ``when=is_ubuntu``:
the shared ``inventory.json`` holds one host in the ``"ubuntu"`` group
alongside hosts in other groups, so each run reconciles the virtualenv on
the Ubuntu host and skips every other one -- on a host outside the group
the driver skips each request without materializing it, so neither the
``virtualenv`` command nor the probe behind it runs, and plan and log
render ``[skipped: when guard is_ubuntu]``.

The tool itself is the prerequisite: Ubuntu ships ``virtualenv`` in the
``python3-virtualenv`` deb, and a host without the binary fails the create
with a plain ``CommandNotFoundError`` at apply. So ``main`` first drives
its own idempotent, ``sudo``-flagged ``apt.packages`` install of that
package (the same prerequisite pattern the sibling ``provision_pip.py``
uses for the stdlib wheel) before the deploy below reads any facts.

Run it with::

    uv run python examples/modules/pip/pip_virtualenv.py
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
    apt,
    format_log,
    format_plan,
    op,
    pip,
    run,
    validate_inventory,
)

# examples/modules/pip/pip_virtualenv.py -> the project root, so the
# inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The example virtualenv: its own /tmp path, so it never disturbs the
# shared venv the sibling ``provision_pip.py`` maintains for the package
# example. A module-level string, so the request kwargs stay hashable and
# identical in the prepare and apply phases.
VENV_PATH = "/tmp/reemote-pip-virtualenv-example"

# The base interpreter to reconcile against: naming one is what activates
# the interpreter check -- the probe's answer for the existing tree is
# compared with this interpreter's own version at major.minor. The tool
# takes it as ``-p``.
PYTHON = "python3"

# The deb that ships the ``virtualenv`` binary on Ubuntu. A module-level
# tuple, so the prerequisite request's kwargs stay hashable.
VIRTUALENV_PACKAGES = ("python3-virtualenv",)


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries
    each carrying at least ``host`` and ``connection``. Returns the parsed
    list, ready to hand to ``run``, which materializes it with the default
    ``Host.from_inventory`` factory; raises ``InventoryError`` (via
    ``validate_inventory``) when the document is not an array or an entry
    does not match the schema.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"ubuntu"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision and the
    determinism guard holds. The Ubuntu demo host is the one the
    prerequisite deploy below installs the ``virtualenv`` tool on, so both
    deploys are guarded to that group; on a host outside it every request
    would fail before doing anything, so the driver skips them all instead.
    """
    return host.group == "ubuntu"


def install_virtualenv(host: Host) -> Generator[OperationRequest]:
    """Install the ``virtualenv`` tool with ``apt.packages`` -- the prerequisite.

    A branch-free deploy yielding one request, so the sequence is identical
    in the prepare and apply phases. ``apt.packages`` is idempotent on the
    ``AptPackages`` fact: a host that already has ``python3-virtualenv``
    plans no change. Installing writes system state, so the request is
    flagged ``sudo=True`` and the driver elevates the rendered
    ``apt-get -y ... install python3-virtualenv``.
    """
    yield op(
        apt.packages,
        name="Install python3-virtualenv",
        packages=VIRTUALENV_PACKAGES,
        sudo=True,
        when=is_ubuntu,
    )


def deploy(host: Host) -> Generator[OperationRequest]:
    """Create the virtualenv -- twice, to show idempotence -- then remove it.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request creates whatever the ``_VirtualenvPython`` probe does
    not find yet (``virtualenv -p python3 <path>``); the second carries the
    same arguments and is a no-op in the apply phase once the first tree
    reports the requested interpreter at major.minor. The third removes the
    tree again (``present=False``, the whole-tree ``rm -rf`` the baseline
    delegates to ``files.directory``) and the fourth is its no-op repeat,
    leaving the host as found so the shared-host example sweep is not
    polluted.

    The virtualenv lives in the SSH user's own /tmp path, so no request
    needs elevation; creating under a root-owned parent would flag them
    ``sudo=True``.
    """
    yield op(
        pip.virtualenv,
        name="Create the example virtualenv",
        path=VENV_PATH,
        python=PYTHON,
        when=is_ubuntu,
    )
    yield op(
        pip.virtualenv,
        name="Re-run create (no-op)",
        path=VENV_PATH,
        python=PYTHON,
        when=is_ubuntu,
    )
    yield op(
        pip.virtualenv,
        name="Remove the example virtualenv again",
        path=VENV_PATH,
        present=False,
        when=is_ubuntu,
    )
    yield op(
        pip.virtualenv,
        name="Re-run removal (no-op)",
        path=VENV_PATH,
        present=False,
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Install the virtualenv tool before the deploy below creates the
        # tree: an idempotent apt deploy, so a host that already has the
        # deb plans no change -- and the non-Ubuntu demo hosts are skipped
        # by its when guard.
        print("\n== Provisioning python3-virtualenv ==")
        await run(inventory, install_virtualenv)

        # --dry: prepare phase only. Facts are still gathered over SSH (the
        # guarded interpreter probe is read-only) so the plan reflects each
        # host's real pre-deployment state; no command that mutates a host
        # is sent. A request the ``when`` guard rejects is planned as
        # [skipped: when guard is_ubuntu] without materializing it, so its
        # fact is not read on that host either.
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
        # Python's own message already names the file and the reason, so
        # print just that -- no host was contacted and there is no traceback
        # worth showing.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema. The
        # message names the offending entry by its list index, which is the
        # whole report -- and it fails before any host is materialized.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed
        # error whose message names the host and the failing command. Print
        # that message -- no traceback -- and exit non-zero so the failure
        # stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
