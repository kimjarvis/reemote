# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: zero the filter table's packet/byte counters on two real hosts.

This is a *usage* example, not part of the library. It drives reemote5's
two-phase execution model end to end over a **real SSH transport**
(asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects. Every request is flagged ``sudo=True``: iptables mutations
   need CAP_NET_ADMIN, so the commands ride under root.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``iptables.zero`` is deliberately **stateless** (like
``selinux.restorecon``): counters grow with every packet, so any fact
comparison is stale before the command runs and "already zero" is never
a stable state on a live firewall. Every request yields ``-Z`` and plans
as changed -- including the second one, in both phases. That is the
correct contract for an audit reset, not an idempotence bug. The audit
workflow is: gather ``IptablesRulesOrdered`` (exact ``-x`` counters),
zero, let traffic flow, gather again -- the delta shows which rules
actually match. Pass ``chain=...`` to scope the reset to one chain.

Zeroing touches only counters, never the rule set, so this example is
safe to run against a live host; the ``IptablesPersistence`` facts (which
compare rules, not counters) stay synced across a zero.

Run it with::

    uv run python examples/modules/iptables/iptables_zero.py
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
    iptables,
    op,
    run,
)

# The inventory: real hosts and their SSH connection information, supplied
# by the application to the library. No privilege section is needed: sudo
# defaults to root, and the demo hosts grant the login user passwordless
# sudo.
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
    """Zero the filter table's counters twice -- both always change.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    iptables -Z needs CAP_NET_ADMIN, so every request is flagged
    ``sudo=True`` and rides under root.
    """
    yield op(
        iptables.zero,
        name="Zero filter table counters",
        table="filter",
        sudo=True,
    )
    yield op(
        iptables.zero,
        name="Zero filter table counters (again)",
        table="filter",
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