# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: mount a data filesystem and unmount a scratch one on two real hosts.

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

Run it with::

    uv run python examples/modules/server/server_mount.py
"""

import asyncio
import sys
from collections.abc import Generator

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    OperationRequest,
    PrivilegeError,
    format_log,
    format_plan,
    op,
    run,
    server,
)

# The inventory: real hosts and their SSH connection information, supplied
# by the application to the library. The default host_factory materializes
# each entry into a plain Host that connects over asyncssh.
INVENTORY = [
    {
        "host": "web1",
        "connection": {
            "address": "192.168.0.240",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
    {
        "host": "web2",
        "connection": {
            "address": "192.168.0.242",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
]


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
    )
    yield op(
        server.mount,
        name="Unmount scratch filesystem",
        path="/scratch",
        mounted=False,
        sudo=True,
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. The mount list is still gathered over
        # SSH (read-only) so the plan reflects each host's real pre-deployment
        # state; nothing is mounted or unmounted.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. Step 1
        # mounts /data where missing (or remounts it with the missing
        # options); step 2 unmounts /scratch where present.
        plans, report, _hosts = await run(INVENTORY, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
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