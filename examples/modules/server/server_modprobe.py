# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: load the nbd, loop and brd kernel modules on real CentOS hosts.

This is a *usage* example, not part of the library. It shows reemote5's
``server.modprobe`` operation, baseline ``server.modprobe`` parity for
loading and unloading kernel modules: the deploy first ensures the software
behind the request is installed, then loads the ``nbd`` module and ensures
the ``loop`` and ``brd`` modules are loaded, both under ``sudo`` because
``modprobe`` needs root.

**Prerequisite software.** A ``modprobe`` request needs two things on the
host: the ``modprobe`` binary itself (the ``kmod`` package) and the ``.ko``
file of every named module in the *running* kernel's ``/lib/modules`` tree.
The three modules requested here -- ``nbd``, ``loop`` and ``brd`` -- are all
shipped by ``kernel-modules-core``, the package every kernel install depends
on, so their module files are guaranteed present for the booting kernel.
That matters: a module living in ``kernel-modules-extra`` would make the
example version-sensitive, because a bare ``dnf install`` of that package
only populates ``/lib/modules`` for the *newest* installed kernel, leaving
``modprobe`` reporting "not found in directory" on a host that has not been
rebooted into it. ``main`` therefore drives a small *install deploy*
(``dnf.packages`` of ``kmod`` and ``kernel-modules-core``, idempotent) before
the modprobe deploy -- the same two-run split ``two_deploy_split.py``
demonstrates -- so the load step runs against a host whose module database
is really in place.

The operation is idempotent: it reads the ``KernelModules`` fact (the
loaded modules from ``/proc/modules``) and yields a ``modprobe -a`` only
for the names that are missing, so a host where ``loop`` is already loaded
re-loads nothing and a second run is a no-op everywhere. ``module`` accepts
a single name or a tuple of names (tuples keep ``op()`` requests hashable),
and ``force=True`` would render ``modprobe -f`` -- without ever forcing a
reload of an already-loaded module. Unloading is the mirror image:
``present=False`` yields ``modprobe -r -a`` only for the names that are
loaded.

Every failure mode of the run -- an unreachable host, a rejected elevation,
or a ``modprobe`` that exits non-zero (an unknown module, or a module in
use on removal) -- reaches ``main`` as a typed error whose message already
names the host, so ``main`` catches them and prints one line each instead
of a traceback.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``): each plain ``Host``
lazily opens an asyncssh connection, reads ``uname -s`` and
``/proc/modules`` for real, and runs ``modprobe`` under sudo where needed.

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

The path is resolved from ``__file__``, so the example works from any
working directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/server/server_modprobe.py
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
    op,
    run,
    server,
    validate_inventory,
)

# examples/modules/server/server_modprobe.py -> the project root, so the inventory
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
    """Install the software the ``modprobe`` requests depend on.

    ``kmod`` provides the ``modprobe`` binary and ``kernel-modules-core``
    provides the ``nbd``/``loop``/``brd`` module files for the running
    kernel. Branch-free and idempotent: packages already installed plan and
    apply as no change.
    """
    yield op(
        dnf.packages,
        name="Install kmod and kernel-modules-core",
        packages=("kmod", "kernel-modules-core"),
        sudo=True,
        when=is_centos,
    )


def deploy(host: Host) -> Generator[OperationRequest]:
    """Load nbd, then ensure loop and brd are loaded.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases; which modules are missing is decided inside the operation
    against the freshly gathered ``KernelModules`` fact, per phase.
    """
    yield op(
        server.modprobe,
        name="Load nbd module",
        module="nbd",
        sudo=True,
        when=is_centos,
    )
    yield op(
        server.modprobe,
        name="Load loop and brd modules",
        module=("loop", "brd"),
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Prerequisite run: modprobe needs the kmod binary and the module
        # files of the running kernel, so both packages are put in place --
        # idempotently -- before the deploy below gathers its facts and loads.
        await run(inventory, install)

        # --dry: prepare phase only. The module list is still gathered over
        # SSH (read-only) so the plan reflects each host's real pre-deployment
        # state; nothing is loaded.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. Step 1
        # loads nbd where missing; step 2 loads just the missing ones of
        # loop and brd.
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
    except HostConnectionError as exc:
        # A host could not be reached over SSH at all. Report it plainly --
        # naming every unreachable host -- instead of dumping an asyncssh
        # traceback, and exit non-zero so the failure is script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except PrivilegeError as exc:
        # The host was reached but rejected the ``sudo=True`` elevation -- a
        # wrong ``sudo_password``, or a run-as user the login user may not
        # become. One line naming the host; the detail stays on ``__cause__``.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except CommandError as exc:
        # A ``modprobe`` invocation exited non-zero (a module unknown to the
        # running kernel, or in use on removal). The typed message
        # already names the host, the credential-free command and its
        # stderr, so print just that.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())