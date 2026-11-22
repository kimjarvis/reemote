# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Provision flatpak on the Ubuntu demo hosts before the flatpak examples run.

This is a *usage* example, not part of the library. It is the shared
prerequisite step for ``flatpak_packages.py`` and ``flatpak_flatpak_package.py``:
flatpak is *not* set up out of the box on Ubuntu -- Ubuntu ships snap, not
flatpak -- so both examples await ``provision_flatpak`` from this module before
they read or mutate any flatpak state, and run even on a host where flatpak was
never installed.

Provisioning is the reemote equivalent of two shell commands::

    sudo apt-get install -y flatpak
    sudo flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo

The first is a real operation: ``install_flatpak`` yields an ``apt.packages``
request (``packages=("flatpak",)``, ``sudo=True``), driven by ``run`` over the
default ``Host.from_inventory`` factory, so it installs over the real SSH
transport and is idempotent on the ``AptPackages`` fact -- a host that already
has flatpak plans no change. The rendered command is reemote's non-interactive
``apt-get -y ... install flatpak``.

The second has no operation counterpart -- reemote's ``flatpak`` module manages
applications, not remotes -- so it rides a ``Shell`` *action* through
``gather_action``. Actions act immediately and cannot run inside a deploy
(prepare must never send, and the generator is re-invoked per phase), so
``provision_flatpak`` awaits the ``apt.packages`` deploy to completion first and
only then fans the ``remote-add`` command out. ``--if-not-exists`` makes the
remote add idempotent too: a host that already carries ``flathub`` is left
untouched. The action's exit status is data, so a non-zero result is raised as a
``CommandError`` naming the host, while an unreachable host or a rejected sudo
credential still raises ``HostConnectionError`` / ``PrivilegeError``.

Both steps are scoped to the ``"Ubuntu"`` family: ``provision_flatpak``
filters the inventory to that family before contacting anything, so the other
demo hosts are never touched. ``install_flatpak`` also carries ``when=is_ubuntu``
-- the same static guard the sibling examples use -- so the deploy is safe to
drive against a full inventory as well.

The module is importable (``from install import provision_flatpak``) and also
runnable on its own to provision the hosts without running an example::

    uv run python examples/modules/flatpak/install.py
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
    Shell,
    apt,
    gather_action,
    op,
    run,
    validate_inventory,
)

# examples/modules/flatpak/install.py -> the project root, so the inventory is
# found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The package that provides the flatpak tool itself. A module-level tuple, so
# the request kwargs stay hashable and identical in the prepare and apply
# phases.
FLATPAK_PACKAGES = ("flatpak",)

# The remote the flatpak examples resolve their application IDs against, and
# the URL ``flatpak remote-add`` fetches its configuration from.
FLATHUB_REMOTE = "flathub"
FLATHUB_URL = "https://flathub.org/repo/flathub.flatpakrepo"

# ``--if-not-exists`` keeps the add idempotent: a host that already carries the
# remote is left untouched. Elevated, because a system remote is root-owned.
REMOTE_ADD_COMMAND = f"flatpak remote-add --if-not-exists {FLATHUB_REMOTE} {FLATHUB_URL}"


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
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``), never a mutable fact,
    and stays a stable module-level function -- the two rules every ``when``
    predicate follows so both deploy phases reach the same eligibility
    decision and the determinism guard holds.
    """
    return host.family == "Ubuntu"


def install_flatpak(host: Host) -> Generator[OperationRequest]:
    """Install the flatpak tool with ``apt.packages`` -- the first prerequisite.

    A branch-free deploy yielding one request, so the sequence is identical in
    the prepare and apply phases. ``apt.packages`` is idempotent on the
    ``AptPackages`` fact: a host that already has flatpak plans no change.
    Installing writes system state, so the request is flagged ``sudo=True`` and
    the driver elevates the rendered ``apt-get -y ... install flatpak``.
    """
    yield op(
        apt.packages,
        name="Install flatpak",
        packages=FLATPAK_PACKAGES,
        sudo=True,
        when=is_ubuntu,
    )


async def provision_flatpak(inventory: list[dict[str, Any]]) -> None:
    """Provision flatpak + the flathub remote on the Ubuntu hosts, in order.

    The shared prerequisite both flatpak examples await before their own logic.
    Scoped to the ``"Ubuntu"`` family: the inventory is filtered first, so no
    other demo host is materialized or contacted. Returns nothing and prints
    nothing -- it is a pure helper; the caller (an example's ``main`` or this
    module's) owns the output.

    Two barriers, in order:

    1. ``run`` drives ``install_flatpak`` (an ``apt.packages`` deploy) to
       completion over the real SSH transport, closing its own hosts.
    2. ``gather_action`` fans the ``Shell`` remote-add out across freshly built
       hosts and closes them when it finishes. It runs only after the deploy
       barrier, because an action cannot run inside a deploy.

    A non-zero remote-add exit is raised as a ``CommandError`` naming the host
    (the action's exit status is otherwise data); an unreachable host or a
    rejected sudo credential raises ``HostConnectionError`` / ``PrivilegeError``
    from either step. With no Ubuntu host in the inventory this is a no-op.
    """
    ubuntu = [entry for entry in inventory if entry.get("family") == "Ubuntu"]
    if not ubuntu:
        return

    # 1. Install the tool. ``run`` owns and closes the hosts it materializes.
    await run(ubuntu, install_flatpak)

    # 2. Add the remote. ``gather_action`` re-connects (the deploy closed its
    #    hosts) and closes them again when it finishes, so build fresh ones.
    hosts = [Host.from_inventory(entry) for entry in ubuntu]
    results = await gather_action(hosts, Shell, command=REMOTE_ADD_COMMAND, sudo=True)
    for name, result in results.items():
        if result["exit_status"] != 0:
            raise CommandError(
                name,
                f"sudo {REMOTE_ADD_COMMAND}",
                result["exit_status"],
                result["stderr"],
            )


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

    ubuntu = [entry["host"] for entry in inventory if entry.get("family") == "Ubuntu"]
    if not ubuntu:
        print("\n== No host in the 'Ubuntu' family; nothing to provision ==")
        return

    print(f"\n== Provisioning flatpak on: {', '.join(ubuntu)} ==")
    try:
        await provision_flatpak(inventory)
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library reports every host-level failure as a typed error whose
        # message names the host and the failing command. Print that message
        # -- no traceback -- and exit non-zero so the failure stays visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print("== flatpak installed and the flathub remote added ==")


if __name__ == "__main__":
    asyncio.run(main())