# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: ensure an SSH rule with typed match extensions on two real hosts.

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

``iptables.rule`` mirrors pyinfra's operation and extends it with
first-class match extensions (the typed-matcher decision): ``ctstate``
renders ``-m conntrack --ctstate``, ``destination_ports``/``source_ports``
render multiport matches, ``limit``/``limit_burst`` rate limiting,
``match_set`` an ipset match, and ``comment`` the comment match -- the
reliable idempotency and auditing marker. No raw ``extras`` string is
needed for any of them: each is folded into the same sorted-pair
comparison pyinfra's port shortcuts use, so the rule definition read back
from ``iptables-save`` compares order-independently and the operation
stays idempotent. Tuple arguments (not lists) keep every request hashable.

The rule here accepts established/related SSH traffic and carries a
comment, so a second identical request finds it verbatim. Adding an
ACCEPT rule only widens what is allowed -- it never drops the deploy's
own SSH session -- so this example is safe to run against a live host.

The deploy yields the same rule request twice: in the prepare phase
(where nothing is sent) both plan as changes, but in the apply phase the
second request's fresh per-step fact cache sees the first ``-A`` and
reports the rule exists.

The demo hosts ship without ``iptables`` or ``ipset``, so the deploy
opens with an idempotent ``dnf.packages`` install under ``sudo=True``.
The iptables facts are exit-masked, so the prepare phase reads them even
before the binary is present; in apply the install runs first and the
mutation that follows rides the freshly installed binary.

Run it with::

    uv run python examples/modules/iptables/iptables_rule.py
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
    """Ensure the established-SSH ACCEPT rule twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    iptables -A needs CAP_NET_ADMIN, so every request is flagged
    ``sudo=True`` and rides under root.
    """
    yield op(
        dnf.packages,
        name="Install iptables and ipset",
        packages=("iptables", "ipset"),
        sudo=True,
    )
    yield op(
        iptables.rule,
        name="Allow established and related SSH traffic",
        chain="INPUT",
        jump="ACCEPT",
        protocol="tcp",
        destination_port=22,
        ctstate=("ESTABLISHED", "RELATED"),
        comment="allow-established-ssh",
        sudo=True,
    )
    yield op(
        iptables.rule,
        name="Allow established and related SSH traffic (again)",
        chain="INPUT",
        jump="ACCEPT",
        protocol="tcp",
        destination_port=22,
        ctstate=("ESTABLISHED", "RELATED"),
        comment="allow-established-ssh",
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