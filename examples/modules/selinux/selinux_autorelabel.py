# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Schedule a full-system SELinux relabel with ``selinux.autorelabel``.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key). The hosts must run SELinux (RHEL-family);
   point ``INVENTORY`` at your own machines to target them.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``). Each plain ``Host``
lazily opens an asyncssh connection from its ``connection`` data, gathers
facts and runs commands for real, and ``run`` closes those connections when
it finishes.

``run`` is a coroutine (both phases are async), so ``main`` is async and
driven by ``asyncio.run``.

``selinux.autorelabel`` is a reemote5 original (pyinfra ships no
autorelabel operation): it creates the ``/.autorelabel`` marker file
(``touch /.autorelabel``) that tells the next boot to relabel *every* file
on the system from the fcontext database. **Warning -- this is a heavy,
disruptive remedy**: the relabel runs before services start and can take
many minutes on a large filesystem, and it only happens at the *next
reboot*, which this operation does not trigger (reboot deliberately, or
with the ``server.reboot`` operation, when you are ready). Use it when
SELinux is being switched from disabled to enabled -- files created while
it was off carry no labels at all -- or when the filesystem's contexts are
severely corrupted; for one tree, ``selinux.restorecon`` is the surgical
tool instead.

The operation is idempotent on the marker's existence: when
``/.autorelabel`` is already present a relabel is already pending and the
request is a no-op. The script yields the request twice to show that: the
prepare phase plans both as changes when the marker is absent (nothing is
sent in prepare), while the apply phase creates it on the first and
reports the second as a no-op ("a full relabel is already pending").

Creating a file at ``/`` needs root, so both requests are flagged
``sudo=True`` and the driver elevates their commands over SSH. The
disabled-to-enabling flow this operation belongs to is completed by the
``files.edit`` recipe in
``examples/modules/selinux/selinux_config.py`` (set ``SELINUX=enforcing``
for the next boot) followed by a reboot.

Run it with::

    uv run python examples/modules/selinux/selinux_autorelabel.py
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
    selinux,
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
    """Schedule a full relabel -- twice, to show the pending-marker no-op.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request creates /.autorelabel when it is absent; the second
    is a guaranteed no-op in the apply phase, because the marker the first
    step created is already visible through the fresh per-step state.

    Creating a file at / needs root, so both requests are flagged
    ``sudo=True`` and the driver elevates their command over SSH.
    """
    yield op(
        selinux.autorelabel,
        name="Schedule a full relabel at next boot",
        sudo=True,
    )
    yield op(
        selinux.autorelabel,
        name="Re-schedule the relabel (no-op, already pending)",
        sudo=True,
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
        plans, report, _hosts = await run(INVENTORY, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())