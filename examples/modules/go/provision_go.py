# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Provision the Go toolchain on the Ubuntu demo host before the go example runs.

This is a *usage* example, not part of the library. It is the shared
prerequisite step for ``go_go_packages.py``: the reemote ``go`` module reads the
``go install`` binaries through the ``go`` toolchain and deliberately does not
install it, and the shared Ubuntu demo host ships neither ``go`` nor a Go
workspace. Without the tooling the fact's compound command resolves an empty
bin directory and answers ``[]`` -- a valid but uninteresting read -- so this
module installs the compiler first, letting the example demonstrate the real
``go version -m`` listing path (and any ``go install`` binaries already there).

Provisioning is the reemote equivalent of one shell command::

    sudo apt-get install -y golang-go

That single request is a real operation: ``install_golang`` yields an
``apt.packages`` request (``packages=GO_PACKAGES``, ``sudo=True``), driven by
``run`` over the default ``Host.from_inventory`` factory, so it installs over the
real SSH transport and is idempotent on the ``AptPackages`` fact -- a host that
already has the deb plans no change and sends nothing. Debian and Ubuntu package
the ``go`` toolchain as ``golang-go`` (which provides ``/usr/bin/go``), so the
one name puts the executable the fact's command relies on on the PATH.

The example is scoped to the ``"Ubuntu"`` family: ``provision_go`` filters the
inventory to that family before contacting anything, so the other demo hosts are
never touched. ``install_golang`` also carries ``when=is_ubuntu`` -- the same
static guard the sibling example uses -- so the deploy is safe to drive against
a full inventory as well.

The module is importable (``from provision_go import provision_go``) and also
runnable on its own to provision the host without running an example::

    uv run python examples/modules/go/provision_go.py

The basename is deliberately ``provision_go.py`` rather than ``install.py`` or
``provision.py``: the flatpak, gem, lxd and server example directories already
own those module names, and the offline example harness imports every example
in one process, where a reused basename would resolve ``from ... import``
against the wrong directory's cached helper.
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

# The one Ubuntu deb that puts ``go`` (the compiler and the module's fact
# command's only dependency) on the PATH. A module-level tuple, so the request
# kwargs stay hashable and identical in the prepare and apply phases.
GO_PACKAGES = ("golang-go",)


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``), never a mutable fact,
    and stays a stable module-level function -- the two rules every ``when``
    predicate follows so both deploy phases reach the same eligibility
    decision and the determinism guard holds.
    """
    return host.family == "Ubuntu"


def install_golang(host: Host) -> Generator[OperationRequest]:
    """Install the Go toolchain with ``apt.packages``.

    A branch-free deploy yielding one request, so the sequence is identical in
    the prepare and apply phases. ``apt.packages`` is idempotent on the
    ``AptPackages`` fact: a host that already has the deb plans no change.
    Installing writes system state, so the request is flagged ``sudo=True`` and
    the driver elevates the rendered ``apt-get -y ... install golang-go``.
    """
    yield op(
        apt.packages,
        name="Install golang-go",
        packages=GO_PACKAGES,
        sudo=True,
        when=is_ubuntu,
    )


async def provision_go(inventory: list[dict[str, Any]]) -> None:
    """Install the Go toolchain on the Ubuntu hosts.

    The shared prerequisite the go example awaits before its own logic. Scoped
    to the ``"Ubuntu"`` family: the inventory is filtered first, so no other
    demo host is materialized or contacted. Returns nothing and prints nothing
    -- it is a pure helper; the caller (the example's ``main`` or this module's)
    owns the output.

    One step only: ``run`` drives ``install_golang`` (an ``apt.packages``
    deploy) to completion over the real SSH transport and closes the hosts it
    opened. An unreachable host or a rejected sudo credential raises
    ``HostConnectionError`` / ``PrivilegeError``; a failed install raises
    ``CommandError`` naming the host and the apt command. With no Ubuntu host in
    the inventory this is a no-op.
    """
    ubuntu = [entry for entry in inventory if entry.get("family") == "Ubuntu"]
    if not ubuntu:
        return
    await run(ubuntu, install_golang)


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

    print(f"\n== Provisioning golang-go on: {', '.join(ubuntu)} ==")
    try:
        await provision_go(inventory)
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library reports every host-level failure as a typed error whose
        # message names the host and the failing command. Print that message
        # -- no traceback -- and exit non-zero so the failure stays visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print("== go ready (go version runs on the host) ==")


if __name__ == "__main__":
    asyncio.run(main())
