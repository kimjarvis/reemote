# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Forward a port in a firewalld zone with ``firewalld.forward_port``.

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

``firewalld.forward_port`` is a reemote5 original (pyinfra ships no
firewalld module): it redirects traffic arriving at ``port``/``protocol``
to ``to_port`` and/or ``to_addr`` -- here port 80/tcp to 8080 on the same
host, the classic "unprivileged app behind the well-known port" setup.
At least one of ``to_port``/``to_addr`` is required (a ``ValueError``
otherwise); ``to_addr`` alone redirects to another machine and needs
masquerading enabled on the zone (``firewalld.masquerade``) -- a
same-host port redirect as shown here does not. The operation is
idempotent on the ``FirewalldZones`` fact's ``forward_ports`` list, whose
canonical ``port=80:proto=tcp:toport=8080:toaddr=`` spelling the
operation builds itself, so the comparison never depends on how the
rule was typed. With the default ``permanent=True`` it dual-applies,
permanent first. The script yields the *same* request twice: the prepare
phase plans both as changes when the forward is missing, while the apply
phase adds it on the first and reports the second as a no-op through the
fresh per-step fact cache.

``firewall-cmd`` writes daemon state, so both requests are flagged
``sudo=True`` and the driver elevates their commands over SSH.

The demo hosts ship without firewalld, so ``main`` first drives a small
idempotent *install deploy* (``dnf.packages`` + ``systemd.service`` under
``sudo=True``) to install firewalld and start/enable the daemon, then runs
the deploy below. firewalld's daemon performs the privileged kernel work,
so ``firewall-cmd`` mutations succeed even when sudo lands in a confined
SELinux domain.

Run it with::

    uv run python examples/modules/firewalld/firewalld_forward_port.py
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
    dnf,
    firewalld,
    format_log,
    format_plan,
    op,
    run,
    systemd,
)

# The zone and forward to ensure. Shared by both requests below so they
# carry identical operation kwargs (only the log ``name`` differs), which
# makes the second a guaranteed no-op in the apply phase.
ZONE = "public"
PORT = "80"
PROTOCOL = "tcp"
TO_PORT = "8080"

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


def install(host: Host) -> Generator[OperationRequest]:
    """Install firewalld and start/enable the daemon the deploy needs.

    Branch-free and idempotent: an already-installed package and an
    already-running, enabled service plan and apply as no change.
    """
    yield op(dnf.packages, name="Install firewalld", packages=("firewalld",), sudo=True)
    yield op(
        systemd.service,
        name="Start and enable firewalld",
        service="firewalld",
        running=True,
        enabled=True,
        sudo=True,
    )


def deploy(host: Host) -> Generator[OperationRequest]:
    """Forward 80/tcp to 8080 -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request adds the forward to both scopes if missing; the
    second carries the same arguments and is a guaranteed no-op in the
    apply phase, because the first addition is already visible through
    the fresh per-step fact cache.

    ``firewall-cmd`` writes daemon state, so both requests are flagged
    ``sudo=True`` and the driver elevates their commands over SSH.
    """
    yield op(
        firewalld.forward_port,
        name="Forward 80/tcp to 8080",
        zone=ZONE,
        port=PORT,
        protocol=PROTOCOL,
        to_port=TO_PORT,
        sudo=True,
    )
    yield op(
        firewalld.forward_port,
        name="Re-ensure 80 to 8080 forward (no-op)",
        zone=ZONE,
        port=PORT,
        protocol=PROTOCOL,
        to_port=TO_PORT,
        sudo=True,
    )


async def main() -> None:
    try:
        # Prerequisite run: the demo hosts ship without firewalld, and every
        # firewall-cmd command (and the FirewalldZones fact) needs the daemon
        # running. Install firewalld and start/enable it first (idempotent,
        # sudo=True) so the deploy below reads real zones and its mutations
        # reach a live daemon. firewalld's daemon does the privileged kernel
        # work, so these succeed even under a confined sudo domain.
        await run(INVENTORY, install)

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