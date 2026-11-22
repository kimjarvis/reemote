# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Provision RubyGems on the Ubuntu demo host before the gem examples run.

This is a *usage* example, not part of the library. It is the shared
prerequisite step for ``gem_packages.py`` and ``gem_gem_packages.py``: the
reemote ``gem`` module drives the ``gem`` client and deliberately does not
install it (the baseline's ``requires_command`` gating is dropped, so a missing
gem surfaces as ``CommandNotFoundError`` rather than a silently empty fact), and
the shared Ubuntu demo host ships neither ``ruby`` nor ``gem``. Every fact read
and every command the gem examples send would therefore fail before doing
anything, so this module installs the tooling first.

Provisioning is the reemote equivalent of one shell command::

    sudo apt-get install -y ruby-rubygems

That single request is a real operation: ``install_rubygems`` yields an
``apt.packages`` request (``packages=GEM_PACKAGES``, ``sudo=True``), driven by
``run`` over the default ``Host.from_inventory`` factory, so it installs over
the real SSH transport and is idempotent on the ``AptPackages`` fact -- a host
that already has the deb plans no change and sends nothing. Debian and Ubuntu
package the ``gem`` client as ``ruby-rubygems`` (the package that ships
``/usr/bin/gem``), which depends on the ``ruby`` interpreter, so the one name
brings both the runtime and the client the module's commands and fact need.

Both examples are scoped to the ``"Ubuntu"`` family: ``provision_gem`` filters
the inventory to that family before contacting anything, so the other demo hosts
are never touched. ``install_rubygems`` also carries ``when=is_ubuntu`` -- the
same static guard the sibling examples use -- so the deploy is safe to drive
against a full inventory as well.

The module is importable (``from provision_gem import provision_gem``) and also
runnable on its own to provision the host without running an example::

    uv run python examples/modules/gem/provision_gem.py

The basename is deliberately ``provision_gem.py`` rather than ``install.py`` or
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

# The one Ubuntu deb that puts ``gem`` (and the ``ruby`` interpreter it depends
# on) on the PATH. A module-level tuple, so the request kwargs stay hashable
# and identical in the prepare and apply phases.
GEM_PACKAGES = ("ruby-rubygems",)


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``), never a mutable fact,
    and stays a stable module-level function -- the two rules every ``when``
    predicate follows so both deploy phases reach the same eligibility
    decision and the determinism guard holds.
    """
    return host.family == "Ubuntu"


def install_rubygems(host: Host) -> Generator[OperationRequest]:
    """Install the Ruby interpreter and the ``gem`` client with ``apt.packages``.

    A branch-free deploy yielding one request, so the sequence is identical in
    the prepare and apply phases. ``apt.packages`` is idempotent on the
    ``AptPackages`` fact: a host that already has the deb plans no change.
    Installing writes system state, so the request is flagged ``sudo=True`` and
    the driver elevates the rendered ``apt-get -y ... install ruby-rubygems``.
    """
    yield op(
        apt.packages,
        name="Install ruby-rubygems",
        packages=GEM_PACKAGES,
        sudo=True,
        when=is_ubuntu,
    )


async def provision_gem(inventory: list[dict[str, Any]]) -> None:
    """Install RubyGems on the Ubuntu hosts.

    The shared prerequisite both gem examples await before their own logic.
    Scoped to the ``"Ubuntu"`` family: the inventory is filtered first, so no
    other demo host is materialized or contacted. Returns nothing and prints
    nothing -- it is a pure helper; the caller (an example's ``main`` or this
    module's) owns the output.

    One step only: ``run`` drives ``install_rubygems`` (an ``apt.packages``
    deploy) to completion over the real SSH transport and closes the hosts it
    opened. An unreachable host or a rejected sudo credential raises
    ``HostConnectionError`` / ``PrivilegeError``; a failed install raises
    ``CommandError`` naming the host and the apt command. With no Ubuntu host in
    the inventory this is a no-op.
    """
    ubuntu = [entry for entry in inventory if entry.get("family") == "Ubuntu"]
    if not ubuntu:
        return
    await run(ubuntu, install_rubygems)


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

    print(f"\n== Provisioning ruby-rubygems on: {', '.join(ubuntu)} ==")
    try:
        await provision_gem(inventory)
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library reports every host-level failure as a typed error whose
        # message names the host and the failing command. Print that message
        # -- no traceback -- and exit non-zero so the failure stays visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print("== gem ready (gem --version runs on the host) ==")


if __name__ == "__main__":
    asyncio.run(main())
