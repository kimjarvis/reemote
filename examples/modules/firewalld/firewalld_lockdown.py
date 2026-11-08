# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Manage firewalld lockdown mode with ``firewalld.lockdown``.

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

``firewalld.lockdown`` is a reemote5 original (pyinfra ships no firewalld
module): lockdown restricts which local applications may change the
firewall through D-Bus (only those whitelisted in the lockdown policy or
signed into it) -- turning it on without a whitelist can lock out
NetworkManager and even ``firewall-cmd`` itself for unprivileged callers,
so this script ensures the *normal* state, ``enabled=False``, and is safe
to re-run on shared hosts. Flip ``ENABLED`` to True only alongside a
curated lockdown whitelist. Lockdown is a **runtime-only** daemon switch
(it does not survive a restart), so the operation has no ``permanent``
argument; it is idempotent on the daemon's ``--query-lockdown`` answer.
The script yields the *same* request twice: on a locked-down host the
prepare phase plans both as changes, while the apply phase switches on
the first and reports the second as a no-op through the fresh per-step
fact cache; on a normal host both are no-ops in both phases.

``firewall-cmd`` writes daemon state, so both requests are flagged
``sudo=True`` and the driver elevates their commands over SSH.

The demo hosts ship without firewalld, so ``main`` first drives a small
idempotent *install deploy* (``dnf.packages`` + ``systemd.service`` under
``sudo=True``) to install firewalld and start/enable the daemon, then runs
the deploy below. firewalld's daemon performs the privileged kernel work,
so ``firewall-cmd`` mutations succeed even when sudo lands in a confined
SELinux domain.

Run it with::

    uv run python examples/modules/firewalld/firewalld_lockdown.py
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

# The lockdown state to ensure: off, the normal operating state. Shared by
# both requests below so they carry identical operation kwargs (only the
# log ``name`` differs), which makes the second a guaranteed no-op in the
# apply phase.
ENABLED = False

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
    """Ensure lockdown is off -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request disables lockdown if it is on; the second carries
    the same arguments and is a guaranteed no-op in the apply phase,
    because the first switch is already visible through the fresh
    per-step fact cache.

    ``firewall-cmd`` writes daemon state, so both requests are flagged
    ``sudo=True`` and the driver elevates their commands over SSH.
    """
    yield op(
        firewalld.lockdown,
        name="Ensure lockdown off",
        enabled=ENABLED,
        sudo=True,
    )
    yield op(
        firewalld.lockdown,
        name="Re-ensure lockdown off (no-op)",
        enabled=ENABLED,
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