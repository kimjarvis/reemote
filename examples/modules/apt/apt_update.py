# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Update apt repositories -- the standalone, stateless package-list refresh.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``). Each plain ``Host``
lazily opens an asyncssh connection from its ``connection`` data, gathers
facts and runs commands for real, and ``run`` closes those connections when
it finishes.

``apt.update`` mirrors pyinfra's ``apt.update``: it refreshes the package
lists with the bare ``apt-get update`` -- no non-interactive wrapper, since
``update`` asks no questions. It is **non-idempotent**: it reads no fact and
always reports a change, exactly like the stateless ``update=True`` prefix
of ``apt.packages``. The script therefore yields the request only once --
there is no state for a second request to converge on, so both the prepare
plan and the apply log show it as a change. ``wait_for_lock=True`` (or an
int) makes apt wait for its lock instead of failing instantly, useful when
unattended-upgrades may hold it right after boot.

Where pyinfra's ``apt.update`` also takes a ``cache_time`` parameter (skip
the update while the ``/var/lib/apt/periodic/update-success-stamp`` file is
fresh, and ``touch`` it afterwards), reemote5 deliberately drops the option
-- the same decision ``apt.packages`` made -- so the update is always
stateless and a ``cache_time`` keyword is absorbed with no effect.

Refreshing the lists writes to the root-owned ``/var/lib/apt`` state, so
the request is flagged ``sudo=True`` and the driver elevates its command.

Run it with::

    uv run python examples/modules/apt/apt_update.py
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
    apt,
    format_log,
    format_plan,
    op,
    run,
)

# The inventory: a real Ubuntu server and its SSH connection information,
# supplied by the application to the library (the same connection data as the
# inventory_ubuntu.json entry the JSON-inventory demonstration loads). The
# default host_factory materializes each entry into a plain Host that connects
# over asyncssh.
INVENTORY = [
    {
        "host": "ubuntu",
        "connection": {
            "address": "192.168.0.221",
            "username": "kjarvis",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "ubuntu",
        "tags": ["ubuntu_host"],
    },
]


def deploy(host: Host) -> Generator[OperationRequest]:
    """Update the package lists -- once, since the operation is stateless.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The operation is non-idempotent: there is no state for a second request
    to converge on, so the request is yielded only once.

    Refreshing the lists writes to the root-owned /var/lib/apt state, so the
    request is flagged ``sudo=True`` and the driver elevates its command
    over SSH.
    """
    yield op(apt.update, name="Update apt repositories", sudo=True)


async def main() -> None:
    try:
        # --dry: prepare phase only. No fact is read (the operation is
        # stateless), so the plan simply shows the pending update; no
        # command that mutates a host is sent.
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