# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Provision the rustup toolchain on the Ubuntu demo host before the rustup examples run.

This is a *usage* example, not part of the library. It is the shared
prerequisite step for ``rustup_rustup_toolchains.py``,
``rustup_rustup_default_toolchain.py``, ``rustup_rustup_targets.py`` and
``rustup_rustup_components.py``: the reemote ``rustup`` module reads installed
toolchains, targets and components through the ``rustup`` toolchain and
deliberately does not install it, and the shared Ubuntu demo host ships no
``rustup`` at all. The module drops the design note's ``requires_command``
gating, so — because the fact commands are bare — a host without rustup exits
127 and the gather raises ``CommandNotFoundError``. The module reports a
missing toolchain as an error on purpose; this helper makes the error go away
by installing the toolchain, so the examples demonstrate the real
``rustup toolchain list`` / ``target list --installed`` /
``component list --installed`` reading paths.

Provisioning is the reemote equivalent of one shell command::

    sudo apt-get install -y rustup

That single request is a real operation: ``install_rustup`` yields an
``apt.packages`` request (``packages=RUSTUP_PACKAGES``, ``sudo=True``), driven
by ``run`` over the default ``Host.from_inventory`` factory, so it installs
over the real SSH transport and is idempotent on the ``AptPackages`` fact -- a
host that already has the deb plans no change and sends nothing. Ubuntu
packages the Rust toolchain installer as ``rustup`` (which provides
``/usr/bin/rustup``), so the one name puts the executable every rustup fact's
command relies on on the PATH.

The example is scoped to the ``"Ubuntu"`` family: ``provision_rustup`` filters
the inventory to that family before contacting anything, so the other demo
hosts are never touched. ``install_rustup`` also carries ``when=is_ubuntu`` --
the same static guard the sibling examples use -- so the deploy is safe to
drive against a full inventory as well.

Note that the deb installs the *installer* only: a freshly provisioned host
has no toolchains downloaded yet (fetching one is a network operation the
package never performs), so the facts honestly answer their empty shapes --
``{}`` and ``None`` and ``[]`` -- until a toolchain is added, and every rustup
example says so in its own docstring.

The module is importable (``from provision_rustup import provision_rustup``)
and also runnable on its own to provision the host without running an
example::

    uv run python examples/modules/rustup/provision_rustup.py

The basename is deliberately ``provision_rustup.py`` rather than
``install.py`` or ``provision.py``: the flatpak, gem, lxd, go, cargo and
server example directories already own those module names, and the offline
example harness imports every example in one process, where a reused basename
would resolve ``from ... import`` against the wrong directory's cached helper.
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

# The one Ubuntu deb that puts ``rustup`` (the toolchain installer and every
# rustup fact command's only dependency) on the PATH. A module-level tuple, so
# the request kwargs stay hashable and identical in the prepare and apply
# phases.
RUSTUP_PACKAGES = ("rustup",)


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``), never a mutable fact,
    and stays a stable module-level function -- the two rules every ``when``
    predicate follows so both deploy phases reach the same eligibility
    decision and the determinism guard holds.
    """
    return host.family == "Ubuntu"


def install_rustup(host: Host) -> Generator[OperationRequest]:
    """Install the rustup toolchain with ``apt.packages``.

    A branch-free deploy yielding one request, so the sequence is identical in
    the prepare and apply phases. ``apt.packages`` is idempotent on the
    ``AptPackages`` fact: a host that already has the deb plans no change.
    Installing writes system state, so the request is flagged ``sudo=True`` and
    the driver elevates the rendered ``apt-get -y ... install rustup``.
    """
    yield op(
        apt.packages,
        name="Install rustup",
        packages=RUSTUP_PACKAGES,
        sudo=True,
        when=is_ubuntu,
    )


async def provision_rustup(inventory: list[dict[str, Any]]) -> None:
    """Install the rustup toolchain on the Ubuntu hosts.

    The shared prerequisite the rustup examples await before their own logic.
    Scoped to the ``"Ubuntu"`` family: the inventory is filtered first, so no
    other demo host is materialized or contacted. Returns nothing and prints
    nothing -- it is a pure helper; the caller (the example's ``main`` or this
    module's) owns the output.

    One step only: ``run`` drives ``install_rustup`` (an ``apt.packages``
    deploy) to completion over the real SSH transport and closes the hosts it
    opened. An unreachable host or a rejected sudo credential raises
    ``HostConnectionError`` / ``PrivilegeError``; a failed install raises
    ``CommandError`` naming the host and the apt command. With no Ubuntu host in
    the inventory this is a no-op.
    """
    ubuntu = [entry for entry in inventory if entry.get("family") == "Ubuntu"]
    if not ubuntu:
        return
    await run(ubuntu, install_rustup)


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

    print(f"\n== Provisioning rustup on: {', '.join(ubuntu)} ==")
    try:
        await provision_rustup(inventory)
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library reports every host-level failure as a typed error whose
        # message names the host and the failing command. Print that message
        # -- no traceback -- and exit non-zero so the failure stays visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print("== rustup ready (rustup toolchain list runs on the host) ==")


if __name__ == "__main__":
    asyncio.run(main())
