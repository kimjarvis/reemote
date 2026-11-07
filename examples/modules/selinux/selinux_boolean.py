# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Enable an SELinux boolean persistently, with check-before-executing.

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

``selinux.boolean`` ports pyinfra's operation of the same name: it sets one
kernel security boolean -- here ``httpd_can_network_connect``, the switch
that lets httpd open outbound network connections (needed by every reverse
proxy and web app that talks to a backend) -- to ``on`` or ``off``.
``persistent=True`` writes the updated policy (``setsebool -P``) so the
value survives reboot; the default ``persistent=False`` changes only the
running policy.

The operation is idempotent on the ``SEBoolean`` fact: it reads the current
value word and yields a ``setsebool`` only when it differs -- the
"check if change is needed before executing" enhancement the boolean
proposal asked for falls out of the fact-driven model, sparing the
expensive ``-P`` policy rewrite on an unchanged boolean. The script yields
the *same* request twice to show that idempotence across the two phases:
the prepare phase plans both as changes when the boolean is off (nothing
is sent in prepare, so both compare against the same pre-deployment
state), while the apply phase sets it on the first and reports the second
as a no-op, because the fresh per-step fact cache now sees the value the
first step produced.

``setsebool`` writes kernel and policy state, so both requests are flagged
``sudo=True`` and the driver elevates their commands over SSH.

Run it with::

    uv run python examples/modules/selinux/selinux_boolean.py
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

# The boolean to enable and the value word. Shared by both requests below so
# they carry identical operation kwargs (only the log ``name`` differs),
# which is what makes the second a guaranteed no-op in the apply phase.
BOOLEAN_NAME = "httpd_can_network_connect"
BOOLEAN_VALUE = "on"

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
    """Enable httpd_can_network_connect -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request sets the boolean persistently; the second carries the
    same arguments and is a guaranteed no-op in the apply phase, because
    the first set is already visible through the fresh per-step fact cache.

    ``setsebool -P`` rewrites the policy store, so both requests are flagged
    ``sudo=True`` and the driver elevates their command over SSH.
    """
    yield op(
        selinux.boolean,
        name="Enable httpd_can_network_connect",
        bool_name=BOOLEAN_NAME,
        value=BOOLEAN_VALUE,
        persistent=True,
        sudo=True,
    )
    yield op(
        selinux.boolean,
        name="Re-enable httpd_can_network_connect (no-op)",
        bool_name=BOOLEAN_NAME,
        value=BOOLEAN_VALUE,
        persistent=True,
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