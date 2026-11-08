# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Manage a custom firewalld zone with ``firewalld.zone``.

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

``firewalld.zone`` is a reemote5 original (pyinfra ships no firewalld
module): it manages the zone's *existence and properties* -- target,
short and description -- not the rules inside it (those are the
membership operations, e.g. ``firewalld.zone_service``). It is idempotent
on the ``FirewalldZones`` fact: existence is permanent-only (a new zone
appears at runtime after a reload), and its properties (target, short,
description) are permanent-only too -- firewall-cmd rejects a runtime
``--set-target``/``--set-short``/``--set-description`` ("Option can be used
only with --permanent"), so they are set in the permanent scope and the
runtime copy inherits them on the reload below. The script yields the
*same* request twice and then a
``firewalld.reload``: on a host without the zone the prepare phase plans
both zone requests as changes, while the apply phase creates it on the
first and reports the second as a no-op through the fresh per-step fact
cache; the reload (stateless -- it always runs) then copies the new zone
into the runtime configuration so later membership operations find it
in both scopes.

``firewall-cmd`` writes daemon state, so every request is flagged
``sudo=True`` and the driver elevates its commands over SSH.

The demo hosts ship without firewalld, so ``main`` first drives a small
idempotent *install deploy* (``dnf.packages`` + ``systemd.service`` under
``sudo=True``) to install firewalld and start/enable the daemon, then runs
the deploy below. firewalld's daemon performs the privileged kernel work,
so ``firewall-cmd`` mutations succeed even when sudo lands in a confined
SELinux domain.

Run it with::

    uv run python examples/modules/firewalld/firewalld_zone.py
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

# The custom zone to ensure, with its properties. Shared by both zone
# requests below so they carry identical operation kwargs (only the log
# ``name`` differs), which makes the second a guaranteed no-op in the
# apply phase.
ZONE = "webapp"
ZONE_TARGET = "ACCEPT"
ZONE_SHORT = "WebApp"
ZONE_DESCRIPTION = "Application tier zone"

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
    """Ensure the webapp zone -- twice, to show idempotence -- then reload.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request creates the zone and sets its properties if needed;
    the second carries the same arguments and is a guaranteed no-op in the
    apply phase, because the first change is already visible through the
    fresh per-step fact cache. The reload is stateless (it always runs)
    and copies the new permanent zone into the runtime configuration.

    ``firewall-cmd`` writes daemon state, so every request is flagged
    ``sudo=True`` and the driver elevates its commands over SSH.
    """
    yield op(
        firewalld.zone,
        name="Ensure webapp zone",
        zone=ZONE,
        target=ZONE_TARGET,
        short=ZONE_SHORT,
        description=ZONE_DESCRIPTION,
        sudo=True,
    )
    yield op(
        firewalld.zone,
        name="Re-ensure webapp zone (no-op)",
        zone=ZONE,
        target=ZONE_TARGET,
        short=ZONE_SHORT,
        description=ZONE_DESCRIPTION,
        sudo=True,
    )
    yield op(
        firewalld.reload,
        name="Apply permanent zone to runtime",
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