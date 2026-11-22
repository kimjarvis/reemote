# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Provision the mount prerequisites on the RedHat demo hosts before ``server_mount.py`` runs.

This is a *usage* example, not part of the library. It is the prerequisite
step for ``server_mount.py``: ``server.mount`` manages mounted filesystems,
not the storage behind them -- the mount target directory must already exist
and the device must be present -- and the demo hosts ship neither ``/data``
nor ``/scratch`` nor a spare ``/dev/sdb1`` (only ``/dev/sda``). So the mount
example awaits ``provision_mounts`` from this module before it reads or
mutates any mount state, and runs even on a host with no spare disk.

Provisioning is the reemote equivalent of two shell steps::

    sudo mkdir -p /data /scratch
    sudo sh -c 'truncate -s 64M /var/lib/reemote-data.img && ...losetup/mkfs.ext4... && ln -sfn /dev/loopN /dev/sdb1'

The first is a real operation: ``create_mount_points`` yields one
``files.directory`` request per mount point (``sudo=True``), driven by ``run``
over the default ``Host.from_inventory`` factory, so the directories are
created over the real SSH transport and are idempotent on the ``Directory``
fact -- a host that already carries them plans no change.

The second has no operation counterpart -- reemote's ``server`` module
manages mounts, not loop devices or filesystems -- so it rides a ``Shell``
*action* through ``gather_action``. Actions act immediately and cannot run
inside a deploy (prepare must never send, and the generator is re-invoked
per phase), so ``provision_mounts`` awaits the directory deploy to
completion first and only then fans the loop-device setup out. The compound
command is idempotent: the backing image is created and ``mkfs.ext4`` run
only when the image does not exist yet; an existing image is re-attached
without reformatting (loop attachments do not survive a reboot, the image
does), and the ``/dev/sdb1`` symlink is refreshed to whichever loop device
carries the image. The whole command rides an explicit ``sh -c`` because
sudo execs its command directly rather than through a shell. The action's
exit status is data, so a non-zero result is raised as a ``CommandError``
naming the host, while an unreachable host or a rejected sudo credential
still raises ``HostConnectionError`` / ``PrivilegeError``.

Both steps are scoped to the ``"RedHat"`` inventory family: ``provision_mounts``
filters the inventory to that family before contacting anything, so the other
demo hosts are never touched. ``create_mount_points`` also carries
``when=is_redhat`` -- the same static guard the mount example uses -- so the
deploy is safe to drive against a full inventory as well.

The module is importable (``from provision import provision_mounts``) and
also runnable on its own to provision the hosts without running the example::

    uv run python examples/modules/server/provision.py
"""

import asyncio
import json
import shlex
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
    files,
    gather_action,
    op,
    run,
    validate_inventory,
)

# examples/modules/server/provision.py -> the project root, so the inventory
# is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The mount points server_mount.py manages: /data is mounted, /scratch is
# ensured unmounted. A module-level tuple, so the request kwargs stay
# hashable and identical in the prepare and apply phases.
MOUNT_POINTS = ("/data", "/scratch")

# The loop-backed stand-in for the spare disk the demo hosts lack: a 64M
# image attached to a free loop device, formatted ext4, and exposed under
# the device name the mount example requests.
BACKING_IMAGE = "/var/lib/reemote-data.img"
DEVICE_LINK = "/dev/sdb1"
IMAGE_SIZE = "64M"

# The idempotent loop-device setup. The image is created and formatted only
# when absent; an existing image is re-attached without reformatting (a
# reboot detaches loop devices but keeps the image), and the symlink is
# refreshed to whichever loop device carries the image. ``losetup -j`` lists
# the attachments of the backing file, so the second run finds the first
# run's device instead of leaking another one.
_LOOP_SETUP = (
    f"if [ ! -e {BACKING_IMAGE} ]; then "
    f"truncate -s {IMAGE_SIZE} {BACKING_IMAGE} && "
    f'L=$(losetup -f --show {BACKING_IMAGE}) && mkfs.ext4 -q -F "$L"; fi; '
    f"L=$(losetup -j {BACKING_IMAGE} | cut -d: -f1); "
    f'if [ -z "$L" ]; then L=$(losetup -f --show {BACKING_IMAGE}); fi; '
    f'ln -sfn "$L" {DEVICE_LINK}'
)

# sudo execs its command directly rather than through a shell, so the
# compound setup rides an explicit ``sh -c`` carrier (the Script action's
# precedent), keeping the rendering identical under any escalation.
LOOP_COMMAND = f"sh -c {shlex.quote(_LOOP_SETUP)}"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries each
    carrying at least ``host`` and ``connection``. Returns the parsed list;
    raises ``InventoryError`` (via ``validate_inventory``) when the document
    is not an array or an entry does not match the schema.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_redhat(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"RedHat"`` OS family.

    Reads only static inventory data (``host.family``), never a mutable fact,
    and stays a stable module-level function -- the two rules every ``when``
    predicate follows so both deploy phases reach the same eligibility
    decision and the determinism guard holds.
    """
    return host.family == "RedHat"


def create_mount_points(host: Host) -> Generator[OperationRequest]:
    """Create the mount target directories -- the first prerequisite.

    A branch-free deploy yielding one ``files.directory`` request per mount
    point, so the sequence is identical in the prepare and apply phases.
    ``files.directory`` is idempotent on the ``Directory`` fact: a host that
    already carries the directory plans no change. Creating root-owned
    directories writes system state, so each request is flagged ``sudo=True``
    and the driver elevates the rendered ``mkdir -p``.
    """
    for path in MOUNT_POINTS:
        yield op(
            files.directory,
            name=f"Ensure mount point {path}",
            path=path,
            sudo=True,
            when=is_redhat,
        )


async def provision_mounts(inventory: list[dict[str, Any]]) -> None:
    """Provision the mount points and the loop-backed device on the RedHat hosts.

    The prerequisite ``server_mount.py`` awaits before its own logic. Scoped
    to the ``"RedHat"`` family: the inventory is filtered first, so no other
    demo host is materialized or contacted. Returns nothing and prints
    nothing -- it is a pure helper; the caller (the example's ``main`` or
    this module's) owns the output.

    Two barriers, in order:

    1. ``run`` drives ``create_mount_points`` (a ``files.directory`` deploy)
       to completion over the real SSH transport, closing its own hosts.
    2. ``gather_action`` fans the ``Shell`` loop-device setup out across
       freshly built hosts and closes them when it finishes. It runs only
       after the deploy barrier, because an action cannot run inside a
       deploy.

    A non-zero setup exit is raised as a ``CommandError`` naming the host
    (the action's exit status is otherwise data); an unreachable host or a
    rejected sudo credential raises ``HostConnectionError`` /
    ``PrivilegeError`` from either step. With no RedHat host in the
    inventory this is a no-op.
    """
    redhat = [entry for entry in inventory if entry.get("family") == "RedHat"]
    if not redhat:
        return

    # 1. Create the mount points. ``run`` owns and closes the hosts it
    #    materializes.
    await run(redhat, create_mount_points)

    # 2. Provision the loop-backed device. ``gather_action`` re-connects
    #    (the deploy closed its hosts) and closes them again when it
    #    finishes, so build fresh ones.
    hosts = [Host.from_inventory(entry) for entry in redhat]
    results = await gather_action(hosts, Shell, command=LOOP_COMMAND, sudo=True)
    for name, result in results.items():
        if result["exit_status"] != 0:
            raise CommandError(
                name,
                f"sudo {LOOP_COMMAND}",
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

    redhat = [entry["host"] for entry in inventory if entry.get("family") == "RedHat"]
    if not redhat:
        print("\n== No host in the RedHat family; nothing to provision ==")
        return

    print(f"\n== Provisioning mount prerequisites on: {', '.join(redhat)} ==")
    try:
        await provision_mounts(inventory)
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library reports every host-level failure as a typed error whose
        # message names the host and the failing command. Print that message
        # -- no traceback -- and exit non-zero so the failure stays visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print("== Mount points created and /dev/sdb1 provisioned ==")


if __name__ == "__main__":
    asyncio.run(main())
