# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Provision Node.js and npm on the Ubuntu demo host before the npm examples run.

This is a *usage* example, not part of the library. It is the shared
prerequisite step for ``npm_packages.py`` and ``npm_npm_packages.py``: the
reemote ``npm`` module drives the ``npm`` client and deliberately does not
install it (the baseline's ``requires_command`` gating is dropped, so a missing
npm surfaces as ``CommandNotFoundError`` rather than a silently empty fact), and
the shared Ubuntu demo host ships neither ``node`` nor ``npm``. Every fact read
and every command the npm examples send would therefore fail before doing
anything, so this module installs the tooling first.

Provisioning is the reemote equivalent of one shell command::

    sudo apt-get install -y nodejs npm

That single request is a real operation: ``install_nodejs_and_npm`` yields an
``apt.packages`` request (``packages=NPM_PACKAGES``, ``sudo=True``), driven by
``run`` over the default ``Host.from_inventory`` factory, so it installs over
the real SSH transport and is idempotent on the ``AptPackages`` fact -- a host
that already has both debs plans no change and sends nothing. Ubuntu packages
Node.js as ``nodejs`` and the client as a separate ``npm`` deb that depends on
it, so both names are asked for: ``node`` alone would leave the ``npm`` binary
the module's commands and fact need missing.

The NodeSource repository the ``geerlingguy.nodejs`` role configures is
deliberately not reproduced here: the examples only need *an* npm, any version,
and Ubuntu's own deb is the shortest path to one. A deploy that must pin a
release would add an ``apt.repo`` request for the NodeSource ``.sources`` file
before this step, exactly as that role's migration does, and the npm operations
below are unchanged by whichever build is installed.

Both examples are scoped to the ``"ubuntu"`` inventory group:
``provision_npm`` filters the inventory to that group before contacting
anything, so the other demo hosts are never touched.
``install_nodejs_and_npm`` also carries ``when=is_ubuntu`` -- the same static
guard the sibling examples use -- so the deploy is safe to drive against a full
inventory as well.

The module is importable (``from provision_npm import provision_npm``) and also
runnable on its own to provision the host without running an example::

    uv run python examples/modules/npm/provision_npm.py

The basename is deliberately ``provision_npm.py`` rather than ``install.py`` or
``provision.py``: the flatpak and server example directories already own those
module names, and the offline example harness imports every example in one
process, where a reused basename would resolve ``from ... import`` against the
wrong directory's cached helper.
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
    op,
    run,
    validate_inventory,
)

# examples/modules/npm/provision_npm.py -> the project root, so the
# inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The two Ubuntu debs that put ``node`` and ``npm`` on the PATH. A module-level
# tuple, so the request kwargs stay hashable and identical in the prepare and
# apply phases.
NPM_PACKAGES = ("nodejs", "npm")


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries each
    carrying at least ``host`` and ``connection``. Returns the parsed list;
    raises ``InventoryError`` (via ``validate_inventory``) when the document is
    not an array or an entry does not match the schema.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"ubuntu"``.

    Reads only static inventory data (``host.group``), never a mutable fact,
    and stays a stable module-level function -- the two rules every ``when``
    predicate follows so both deploy phases reach the same eligibility
    decision and the determinism guard holds.
    """
    return host.group == "ubuntu"


def install_nodejs_and_npm(host: Host) -> Generator[OperationRequest]:
    """Install the Node.js runtime and the npm client with ``apt.packages``.

    A branch-free deploy yielding one request, so the sequence is identical in
    the prepare and apply phases. ``apt.packages`` is idempotent on the
    ``AptPackages`` fact: a host that already has both debs plans no change.
    Installing writes system state, so the request is flagged ``sudo=True`` and
    the driver elevates the rendered ``apt-get -y ... install nodejs npm``.
    """
    yield op(
        apt.packages,
        name="Install nodejs and npm",
        packages=NPM_PACKAGES,
        sudo=True,
        when=is_ubuntu,
    )


async def provision_npm(inventory: list[dict[str, Any]]) -> None:
    """Install Node.js and npm on the Ubuntu hosts.

    The shared prerequisite both npm examples await before their own logic.
    Scoped to the ``"ubuntu"`` group: the inventory is filtered first, so no
    other demo host is materialized or contacted. Returns nothing and prints
    nothing -- it is a pure helper; the caller (an example's ``main`` or this
    module's) owns the output.

    One step only: ``run`` drives ``install_nodejs_and_npm`` (an
    ``apt.packages`` deploy) to completion over the real SSH transport and
    closes the hosts it opened. An unreachable host or a rejected sudo
    credential raises ``HostConnectionError`` / ``PrivilegeError``; a failed
    install raises ``CommandError`` naming the host and the apt command. With
    no Ubuntu host in the inventory this is a no-op.
    """
    ubuntu = [entry for entry in inventory if entry.get("group") == "ubuntu"]
    if not ubuntu:
        return
    await run(ubuntu, install_nodejs_and_npm)


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The file parsed, but an entry violates the inventory schema.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"== Inventory loaded from {INVENTORY_PATH} ==")
    print(", ".join(entry["host"] for entry in inventory))

    ubuntu = [entry["host"] for entry in inventory if entry.get("group") == "ubuntu"]
    if not ubuntu:
        print("\n== No host in the 'ubuntu' group; nothing to provision ==")
        return

    print(f"\n== Provisioning nodejs and npm on: {', '.join(ubuntu)} ==")
    try:
        await provision_npm(inventory)
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library reports every host-level failure as a typed error whose
        # message names the host and the failing command. Print that message
        # -- no traceback -- and exit non-zero so the failure stays visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print("== npm ready (npm -v runs on the host) ==")


if __name__ == "__main__":
    asyncio.run(main())
