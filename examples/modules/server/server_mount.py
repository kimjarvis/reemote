# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: mount a data filesystem and unmount a scratch one on real RedHat hosts.

This is a *usage* example, not part of the library. It shows reemote's
``server.mount`` operation, baseline ``server.mount`` parity for managing
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
located by device instead (the baseline's fallback for paths whose spelling
differs from the kernel's listing), and that found mount point is what an
unmount or remount acts on. Like the baseline, ``/etc/fstab`` is never
touched: a mount made this way does not survive a reboot unless an fstab
entry is managed separately (a ``files.edit`` recipe).

The operation itself still requires an existing mount target directory and
a present device -- it does not create ``/data`` or partition disks -- so
this example provisions its own prerequisites before deploying: ``main``
first awaits ``provision_mounts`` from the sibling ``provision.py``, which
creates ``/data`` and ``/scratch`` with ``files.directory`` and attaches a
64M loop-backed ext4 image as ``/dev/sdb1`` (the demo hosts have no spare
disk, only ``/dev/sda``). Both steps are idempotent and scoped to the
``\"RedHat\"`` family, so the example runs even on a host where none of this
storage exists. The loop attachment does not survive a reboot, but
provisioning re-attaches the existing image without reformatting it. A
``mount``/``umount`` that still exits non-zero (a busy filesystem on
unmount, for instance) fails the run with a typed ``CommandError``, which
``main`` reports as one line; an already-unmounted ``/scratch`` simply plans
no change.

Every failure mode of the run -- an unreachable host, a rejected elevation,
or a ``mount``/``umount`` that exits non-zero (a missing device, a busy
filesystem on unmount) -- reaches ``main`` as a typed error whose message
already names the host, so ``main`` catches them and prints one line each
instead of a traceback.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``): each plain ``Host``
lazily opens an asyncssh connection, reads ``/proc/self/mountinfo`` for real,
and runs ``mount``/``umount`` under sudo where needed.

Every request carries ``when=is_redhat``, the declarative eligibility
guard: the predicate reads only **static** inventory data --
``host.family``, set by the ``\"family\"`` key of the inventory entry -- and
is a stable module-level function, so the prepare and apply phases reach
the same skip decision. The driver skips a rejected request without
materializing it, so on hosts outside the ``"RedHat"`` family neither the
commands nor the facts behind them run, and plan and log render the step
as ``[skipped: when guard is_redhat]`` in its lockstep slot.

The inventory is read from ``inventory.json`` in the current working directory; edit it
there to target your machines.

Run it with::

    uv run python examples/modules/server/server_mount.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path

from reemote import (
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

# The mount provisioning helper sits beside this script; add its directory
# to sys.path so ``import provision`` resolves whether the example is run by
# path or imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from provision import provision_mounts


def is_redhat(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"RedHat"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision.
    """
    return host.family == "RedHat"


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
        when=is_redhat,
    )
    yield op(
        server.mount,
        name="Unmount scratch filesystem",
        path="/scratch",
        mounted=False,
        sudo=True,
        when=is_redhat,
    )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Provision the mount points and the loop-backed /dev/sdb1 on the
        # RedHat hosts before anything below reads or mutates mount state,
        # so the example runs even on a host with no spare disk (see
        # provision.py).
        print("\n== Provisioning mount prerequisites (provision.py) ==")
        await provision_mounts(inventory)

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