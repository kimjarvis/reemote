# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Provision the uv package manager on the Ubuntu demo host before the uv examples run.

This is a *usage* example, not part of the library. It is the shared
prerequisite step for ``uv_packages.py``: the reemote ``uv`` module reads the
packages of uv's environment through the ``uv`` tool and deliberately does not
install it, and the shared Ubuntu demo host ships no ``uv`` at all -- Ubuntu's
noble apt suite has no ``uv`` package. The module drops pyinfra's
``requires_command`` gating, so — because the fact command is bare — a host
without uv exits 127 and the gather raises ``CommandNotFoundError``. The
module reports a missing tool as an error on purpose; this helper makes the
error go away by installing the tool, so the example demonstrates the real
``uv pip list --format json`` reading path.

Provisioning is the reemote equivalent of one shell command::

    sudo snap install astral-uv --classic

That single request is a real operation: ``install_uv`` yields a
``snap.package`` request (``packages=UV_SNAPS``, ``classic=True``,
``sudo=True``), driven by ``run`` over the default ``Host.from_inventory``
factory, so it installs over the real SSH transport and is idempotent on the
``SnapPackages`` fact -- a host that already has the snap plans no change and
sends nothing. The Snap Store publishes Astral's official uv build as
``astral-uv`` (classic confinement, so its binary lands directly on the
PATH), which is why the snap route stands in for the absent apt package.

The example is scoped to the ``"Ubuntu"`` family: ``provision_uv`` filters the
inventory to that family before contacting anything, so the other demo hosts
are never touched. ``install_uv`` also carries ``when=is_ubuntu`` -- the same
static guard the sibling example uses -- so the deploy is safe to drive
against a full inventory as well.

Note what the snap does and does not do: it puts the ``uv`` executable on the
PATH and nothing else. There is no project or virtualenv to create; the fact
honestly reports whatever environment ``uv pip list`` resolves on the
connecting user's account -- a discovered ``.venv`` or the fallback
interpreter -- so a bare host's answer is that fallback environment's own
package listing, possibly the empty ``[]`` for an environment with nothing
installed, exactly pyinfra's ``default = dict`` in reemote's list shape.

The module is importable (``from provision_uv import provision_uv``) and also
runnable on its own to provision the host without running an example::

    uv run python examples/modules/uv/provision_uv.py

The basename is deliberately ``provision_uv.py`` rather than ``install.py``
or ``provision.py``: the flatpak, gem, lxd, go, cargo, rustup and server
example directories already own those module names, and the offline example
harness imports every example in one process, where a reused basename would
resolve ``from ... import`` against the wrong directory's cached helper.
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
    op,
    run,
    snap,
    validate_inventory,
)

# The one Snap Store publication that puts ``uv`` (the package manager and
# every uv fact command's only dependency) on the PATH. Astral ships it as
# ``astral-uv`` with classic confinement; a module-level tuple, so the request
# kwargs stay hashable and identical in the prepare and apply phases.
UV_SNAPS = ("astral-uv",)


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``), never a mutable fact,
    and stays a stable module-level function -- the two rules every ``when``
    predicate follows so both deploy phases reach the same eligibility
    decision and the determinism guard holds.
    """
    return host.family == "Ubuntu"


def install_uv(host: Host) -> Generator[OperationRequest]:
    """Install the uv snap with ``snap.package``.

    A branch-free deploy yielding one request, so the sequence is identical in
    the prepare and apply phases. ``snap.package`` is idempotent on the
    ``SnapPackages`` fact: a host that already has the snap plans no change.
    Installing writes system state, so the request is flagged ``sudo=True`` and
    the driver elevates the rendered ``snap install astral-uv --classic``.
    """
    yield op(
        snap.package,
        name="Install uv",
        packages=UV_SNAPS,
        classic=True,
        sudo=True,
        when=is_ubuntu,
    )


async def provision_uv(inventory: list[dict[str, Any]]) -> None:
    """Install uv on the Ubuntu hosts.

    The shared prerequisite the uv examples await before their own logic.
    Scoped to the ``"Ubuntu"`` family: the inventory is filtered first, so no
    other demo host is materialized or contacted. Returns nothing and prints
    nothing -- it is a pure helper; the caller (the example's ``main`` or this
    module's) owns the output.

    One step only: ``run`` drives ``install_uv`` (a ``snap.package`` deploy) to
    completion over the real SSH transport and closes the hosts it opened. An
    unreachable host or a rejected sudo credential raises
    ``HostConnectionError`` / ``PrivilegeError``; a failed install raises
    ``CommandError`` naming the host and the snap command. With no Ubuntu host
    in the inventory this is a no-op.
    """
    ubuntu = [entry for entry in inventory if entry.get("family") == "Ubuntu"]
    if not ubuntu:
        return
    await run(ubuntu, install_uv)


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

    print(f"\n== Provisioning uv on: {', '.join(ubuntu)} ==")
    try:
        await provision_uv(inventory)
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library reports every host-level failure as a typed error whose
        # message names the host and the failing command. Print that message
        # -- no traceback -- and exit non-zero so the failure stays visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print("== uv ready (uv pip list runs on the host) ==")


if __name__ == "__main__":
    asyncio.run(main())
