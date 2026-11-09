# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: mount a data filesystem and unmount a scratch one on real CentOS hosts.

This is a *usage* example, not part of the library. It shows reemote5's
``server.mount`` operation, the pyinfra ``server.mount`` parity for managing
mounted filesystems: the deploy mounts ``/data`` from ``/dev/sdb1`` as ext4
with the ``rw`` and ``noatime`` options, then ensures the ``/scratch``
filesystem is unmounted, both under ``sudo`` because ``mount``/``umount``
need root.

The operation is idempotent: it reads the ``Mounts`` fact (the mounted
filesystems from ``/proc/self/mountinfo``) and yields a ``mount`` only when
the mount point is absent, an ``umount`` only when it is present, and a
``mount -o remount,...`` only when an already-mounted filesystem is missing
some of the requested options -- so a second run is a no-op everywhere.
When ``path`` is not found directly but ``device`` is given, the mount is
located by device instead (pyinfra's fallback for paths whose spelling
differs from the kernel's listing), and that found mount point is what an
unmount or remount acts on. On a BSD host the remount takes the
``mount -uo ... -t ...`` form instead (the ``Kernel`` fact picks it). Like
pyinfra, ``/etc/fstab`` is never touched: a mount made this way does not
survive a reboot unless an fstab entry is managed separately (a
``files.edit`` recipe).

The mount target directory must already exist and the device must be
present -- this example does not create ``/data`` or partition disks. A
missing device or directory makes ``mount`` exit non-zero and the run fails
with a typed ``CommandError``, which ``main`` reports as one line; an
already-unmounted ``/scratch`` simply plans no change.

**Host-policy and storage prerequisite.** The demo hosts have no
``/dev/sdb1`` (only ``/dev/sda``), and on the CentOS hosts SELinux
``staff_t`` confinement additionally denies creating the ``/data`` mount
point and mounting filesystems. The script is correct (``sudo=True``); it
needs a real block device provisioned by the operator, an existing mount
point, and an unconfined ``sudo`` domain. Point ``INVENTORY_PATH`` at a host
with the target device and directory -- or relax SELinux and attach a
disk -- to run it for real.

Every failure mode of the run -- an unreachable host, a rejected elevation,
or a ``mount``/``umount`` that exits non-zero (a missing device, a busy
filesystem on unmount) -- reaches ``main`` as a typed error whose message
already names the host, so ``main`` catches them and prints one line each
instead of a traceback.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``): each plain ``Host``
lazily opens an asyncssh connection, reads ``uname -s`` and
``/proc/self/mountinfo`` for real, and runs ``mount``/``umount`` under sudo
where needed.

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

    uv run python examples/modules/server/server_mount.py
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
    format_log,
    format_plan,
    op,
    run,
    server,
    validate_inventory,
)

# examples/modules/server/server_mount.py -> the project root, so the inventory
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


def deploy(host: Host) -> Generator[OperationRequest]:
    """Mount /data with the requested options, then unmount /scratch.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases; whether a mount is missing (or needs an options remount)
    is decided inside the operation against the freshly gathered ``Mounts``
    fact, per phase.
    """
    yield op(
        server.mount,
        name="Mount data filesystem",
        path="/data",
        device="/dev/sdb1",
        fs_type="ext4",
        options=("rw", "noatime"),
        sudo=True,
        when=is_centos,
    )
    yield op(
        server.mount,
        name="Unmount scratch filesystem",
        path="/scratch",
        mounted=False,
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The mount list is still gathered over
        # SSH (read-only) so the plan reflects each host's real pre-deployment
        # state; nothing is mounted or unmounted.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. Step 1
        # mounts /data where missing (or remounts it with the missing
        # options); step 2 unmounts /scratch where present.
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
        # A ``mount``/``umount`` invocation exited non-zero (the device does
        # not exist on this host, or the filesystem is busy on unmount, for
        # instance). The typed message already names the host, the
        # credential-free command and its stderr, so print just that.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())