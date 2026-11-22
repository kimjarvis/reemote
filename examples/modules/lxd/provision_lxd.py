# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Provision LXD on the Ubuntu demo hosts before the lxd examples run.

This is a *usage* example, not part of the library. It is the shared
prerequisite step for every script under ``examples/modules/lxd/``: LXD is
*not* set up out of the box on Ubuntu -- it ships as a snap, not a default
install -- so each example awaits ``provision_lxd`` from this module before
it reads or mutates any LXD state, and runs even on a host where LXD was
never installed.

Provisioning is the reemote equivalent of two shell commands::

    sudo snap install lxd --channel=6/stable
    sudo lxd init --auto

The first is a real operation: ``install_lxd`` yields a ``snap.package``
request (``packages=("lxd",)``, ``channel="6/stable"``, ``sudo=True``),
driven by ``run`` over the default ``Host.from_inventory`` factory, so it
installs over the real SSH transport and is idempotent on the
``SnapPackage`` fact -- a host that already has lxd on the requested
channel plans no change. The channel is pinned to ``6/stable``, the series
the demo Ubuntu host tracks: the ``latest/stable`` default would plan a
``snap refresh --channel=`` switch there, mutating the shared host's
tracking channel on every example run. The rendered command is reemote's
``snap install lxd --channel=6/stable``.

The second has no operation counterpart -- reemote's ``lxd`` module manages
instances, images, networks and storage, not the daemon's own
initialization -- so it rides a ``Shell`` *action* through ``gather_action``.
Actions act immediately and cannot run inside a deploy (prepare must never
send, and the generator is re-invoked per phase), so ``provision_lxd``
awaits the ``snap.package`` deploy to completion first and only then fans
the init command out. ``lxd init --auto`` is idempotent on the daemon's own
state: upstream's auto-init creates the ``default`` storage pool and the
default profile's root disk device only when no storage pool exists, and
the ``lxdbr0`` bridge only when no managed network or profile NIC exists --
an initialized daemon gets a no-op apply and exit 0, so a host that already
carries a configured LXD is left untouched. The action's exit status is
data, so a non-zero result is raised as a ``CommandError`` naming the host,
while an unreachable host or a rejected sudo credential still raises
``HostConnectionError`` / ``PrivilegeError``.

Both steps are scoped to the ``"Ubuntu"`` family: ``provision_lxd``
filters the inventory to that family before contacting anything, so the other
demo hosts are never touched. ``install_lxd`` also carries ``when=is_ubuntu``
-- the same static guard the sibling examples use -- so the deploy is safe to
drive against a full inventory as well.

One prerequisite provisioning deliberately does not cover: the login user's
membership in the ``lxd`` group, which the examples' unprivileged fact reads
need for daemon-socket access (the demo Ubuntu host's login user is already
a member; the operations themselves ride ``sudo=True`` and need no
membership). On a fresh host run ``sudo usermod -aG lxd <user>`` once --
every reemote run opens a fresh SSH session, which picks the new group up.

The module is importable (``from provision_lxd import provision_lxd``) and
also runnable on its own to provision the hosts without running an example::

    uv run python examples/modules/lxd/provision_lxd.py

The basename is deliberately not ``install.py`` or ``provision.py``: the
flatpak and server example directories already own those module names, and
the offline example harness imports every example in one process, where a
reused basename would resolve ``from ... import`` against the wrong
directory's cached helper.
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
    gather_action,
    op,
    run,
    snap,
    validate_inventory,
)

# The snap that provides LXD itself. A module-level tuple, so the request
# kwargs stay hashable and identical in the prepare and apply phases.
LXD_PACKAGES = ("lxd",)

# The tracking channel to install on: the series the demo Ubuntu host
# already tracks, so provisioning plans no change there. The latest/stable
# default would plan a snap refresh --channel= switch on that host.
LXD_CHANNEL = "6/stable"

# Non-interactive daemon initialization: creates the default storage pool,
# the profile's root disk device and the lxdbr0 bridge -- each only when
# absent, so an initialized daemon is left untouched. Elevated, because
# configuring the daemon is root-owned.
INIT_COMMAND = "lxd init --auto"



def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``), never a mutable fact,
    and stays a stable module-level function -- the two rules every ``when``
    predicate follows so both deploy phases reach the same eligibility
    decision and the determinism guard holds.
    """
    return host.family == "Ubuntu"


def install_lxd(host: Host) -> Generator[OperationRequest]:
    """Install the lxd snap with ``snap.package`` -- the first prerequisite.

    A branch-free deploy yielding one request, so the sequence is identical in
    the prepare and apply phases. ``snap.package`` is idempotent on the
    ``SnapPackage`` fact: a host that already has lxd on the ``6/stable``
    channel plans no change. Installing writes system state, so the request
    is flagged ``sudo=True`` and the driver elevates the rendered
    ``snap install lxd --channel=6/stable``.
    """
    yield op(
        snap.package,
        name="Install lxd",
        packages=LXD_PACKAGES,
        channel=LXD_CHANNEL,
        sudo=True,
        when=is_ubuntu,
    )


async def provision_lxd(inventory: list[dict[str, Any]]) -> None:
    """Provision the lxd snap + an initialized daemon on the Ubuntu hosts.

    The shared prerequisite every lxd example awaits before its own logic.
    Scoped to the ``"Ubuntu"`` family: the inventory is filtered first, so no
    other demo host is materialized or contacted. Returns nothing and prints
    nothing -- it is a pure helper; the caller (an example's ``main`` or this
    module's) owns the output.

    Two barriers, in order:

    1. ``run`` drives ``install_lxd`` (a ``snap.package`` deploy) to
       completion over the real SSH transport, closing its own hosts.
    2. ``gather_action`` fans the ``Shell`` init out across freshly built
       hosts and closes them when it finishes. It runs only after the deploy
       barrier, because an action cannot run inside a deploy.

    A non-zero init exit is raised as a ``CommandError`` naming the host
    (the action's exit status is otherwise data); an unreachable host or a
    rejected sudo credential raises ``HostConnectionError`` / ``PrivilegeError``
    from either step. With no Ubuntu host in the inventory this is a no-op.
    """
    ubuntu = [entry for entry in inventory if entry.get("family") == "Ubuntu"]
    if not ubuntu:
        return

    # 1. Install the snap. ``run`` owns and closes the hosts it materializes.
    await run(ubuntu, install_lxd)

    # 2. Initialize the daemon. ``gather_action`` re-connects (the deploy
    #    closed its hosts) and closes them again when it finishes, so build
    #    fresh ones.
    hosts = [Host.from_inventory(entry) for entry in ubuntu]
    results = await gather_action(hosts, Shell, command=INIT_COMMAND, sudo=True)
    for name, result in results.items():
        if result["exit_status"] != 0:
            raise CommandError(
                name,
                f"sudo {INIT_COMMAND}",
                result["exit_status"],
                result["stderr"],
            )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The file parsed, but an entry violates the inventory schema.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print("== Inventory loaded from inventory.json ==")
    print(", ".join(entry["host"] for entry in inventory))

    ubuntu = [entry["host"] for entry in inventory if entry.get("family") == "Ubuntu"]
    if not ubuntu:
        print("\n== No host in the 'Ubuntu' family; nothing to provision ==")
        return

    print(f"\n== Provisioning LXD on: {', '.join(ubuntu)} ==")
    try:
        await provision_lxd(inventory)
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library reports every host-level failure as a typed error whose
        # message names the host and the failing command. Print that message
        # -- no traceback -- and exit non-zero so the failure stays visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print("== lxd snap installed and the daemon initialized ==")


if __name__ == "__main__":
    asyncio.run(main())
