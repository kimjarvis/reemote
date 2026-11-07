# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Open a raw port in a firewalld zone with ``firewalld.port``.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key). The hosts must run firewalld (RHEL-family);
   point ``INVENTORY`` at your own machines to target them.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``). Each plain ``Host``
lazily opens an asyncssh connection from its ``connection`` data, gathers
facts and runs commands for real, and ``run`` closes those connections when
it finishes. ``run`` is a coroutine, so ``main`` is async and driven by
``asyncio.run``.

``firewalld.port`` is a reemote5 original (pyinfra ships no firewalld
module): it opens a single ``port/protocol`` (or a range like
``5000-5100/udp``) in a zone -- here ``8080/tcp`` in ``public``, the
one-off opening for an app that has no service bundle. (Prefer
``firewalld.zone_service`` when a named bundle exists; raw ports scatter
the port list across rules.) The ``port`` shape and the 65535 upper
bound are validated statically: a malformed value raises ``ValueError``
before any command is planned. The operation is idempotent on the
``FirewalldZones`` fact per scope: with the default ``permanent=True`` it
dual-applies, yielding the ``--permanent --add-port`` first and the
runtime one second, so the opening both survives a reboot and takes
effect immediately, without a reload. The script yields the *same*
request twice: on a closed host the prepare phase plans both as changes,
while the apply phase opens it on the first and reports the second as a
no-op through the fresh per-step fact cache; on an already-open host
both are no-ops in both phases.

``firewall-cmd`` writes daemon state, so both requests are flagged
``sudo=True`` and the driver elevates their commands over SSH.

Run it with::

    uv run python examples/modules/firewalld/firewalld_port.py
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
    firewalld,
    format_log,
    format_plan,
    op,
    run,
)

# The zone and port to open. Shared by both requests below so they carry
# identical operation kwargs (only the log ``name`` differs), which makes
# the second a guaranteed no-op in the apply phase.
ZONE = "public"
PORT = "8080/tcp"

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
    """Open 8080/tcp in public -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request opens the port in both scopes if missing; the second
    carries the same arguments and is a guaranteed no-op in the apply
    phase, because the first opening is already visible through the fresh
    per-step fact cache.

    ``firewall-cmd`` writes daemon state, so both requests are flagged
    ``sudo=True`` and the driver elevates their commands over SSH.
    """
    yield op(
        firewalld.port,
        name="Open 8080/tcp in public",
        zone=ZONE,
        port=PORT,
        sudo=True,
    )
    yield op(
        firewalld.port,
        name="Re-ensure 8080/tcp in public (no-op)",
        zone=ZONE,
        port=PORT,
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