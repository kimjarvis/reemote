# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Bind a source address to a firewalld zone with ``firewalld.source``.

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

``firewalld.source`` is a reemote5 original (pyinfra ships no firewalld
module): it binds a source -- an IP, a CIDR, a MAC or an ``ipset:name``
reference -- to a zone, so traffic *from* that source is filtered by the
zone's rules regardless of which interface it arrives on. Here the
management subnet ``192.168.0.0/24`` is bound to the built-in ``trusted``
zone: source bindings take precedence over interface bindings, which is
how a distinct policy is applied to one subnet without a dedicated NIC.
The operation is idempotent on the ``FirewalldZones`` fact per scope: it
compares the zone's source list, and with the default ``permanent=True``
dual-applies, permanent first. (An ``ipset:`` reference needs the set to
exist first -- manage it with ``firewalld.ipset``.) The script yields the
*same* request twice: on an unbound host the prepare phase plans both as
changes, while the apply phase binds on the first and reports the second
as a no-op through the fresh per-step fact cache.

``firewall-cmd`` writes daemon state, so both requests are flagged
``sudo=True`` and the driver elevates their commands over SSH.

The demo hosts ship without firewalld, so ``main`` first drives a small
idempotent *install deploy* (``dnf.packages`` + ``systemd.service`` under
``sudo=True``) to install firewalld and start/enable the daemon, then runs
the deploy below. firewalld's daemon performs the privileged kernel work,
so ``firewall-cmd`` mutations succeed even when sudo lands in a confined
SELinux domain.

Run it with::

    uv run python examples/modules/firewalld/firewalld_source.py
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

# The zone and source to bind. Shared by both requests below so they carry
# identical operation kwargs (only the log ``name`` differs), which makes
# the second a guaranteed no-op in the apply phase.
ZONE = "trusted"
SOURCE = "192.168.0.0/24"

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
    """Bind the management subnet to trusted -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request binds the source in both scopes if missing; the
    second carries the same arguments and is a guaranteed no-op in the
    apply phase, because the first binding is already visible through the
    fresh per-step fact cache.

    ``firewall-cmd`` writes daemon state, so both requests are flagged
    ``sudo=True`` and the driver elevates their commands over SSH.
    """
    yield op(
        firewalld.source,
        name="Bind management subnet to trusted",
        zone=ZONE,
        source=SOURCE,
        sudo=True,
    )
    yield op(
        firewalld.source,
        name="Re-ensure subnet in trusted (no-op)",
        zone=ZONE,
        source=SOURCE,
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