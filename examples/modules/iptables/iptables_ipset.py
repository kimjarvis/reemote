# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: manage an ipset blacklist on two real hosts.

This is a *usage* example, not part of the library. It drives reemote5's
two-phase execution model end to end over a **real SSH transport**
(asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects. Every request is flagged ``sudo=True``: ipset mutations need
   CAP_NET_ADMIN, so the commands ride under root.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``iptables.ipset`` manages the performance companion to iptables rules:
one ``-m set --match-set`` rule (``iptables.rule``'s ``match_set``
argument) replaces thousands of per-address rules. It is idempotent on
the ``IpSets`` fact: a missing set is created with the requested type
(``-exist`` tolerates a stale fact read), missing entries are added in
sorted order, and an existing set of a *different* type raises
``ValueError`` rather than silently recreating it. Membership is
additive -- entries not listed are left alone; ``present=False`` with
entries removes just those, and with no entries destroys the whole set.
Tuple arguments (not lists) keep every request hashable.

Creating a set changes no packet flow until a rule references it, and
the members here are documentation addresses (RFC 5737 TEST-NET-2/3),
so this example is safe to run against a live host.

The deploy yields the same request twice: in the prepare phase (where
nothing is sent) both plan as changes, but in the apply phase the second
request's fresh per-step fact cache sees the created set and both
entries, and reports everything already present.

Run it with::

    uv run python examples/modules/iptables/iptables_ipset.py
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
    """Ensure the blacklist set and its two members twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    ipset create/add need CAP_NET_ADMIN, so every request is flagged
    ``sudo=True`` and rides under root.
    """
    yield op(
        iptables.ipset,
        name="Ensure the blacklist ipset and its entries",
        set_name="blacklist",
        set_type="hash:ip",
        entries=("192.0.2.10", "198.51.100.23"),
        sudo=True,
    )
    yield op(
        iptables.ipset,
        name="Ensure the blacklist ipset and its entries (again)",
        set_name="blacklist",
        set_type="hash:ip",
        entries=("192.0.2.10", "198.51.100.23"),
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