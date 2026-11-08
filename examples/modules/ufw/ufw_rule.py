# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: manage ufw rules on two real hosts.

This is a *usage* example, not part of the library. It drives reemote5's
two-phase execution model end to end over a **real SSH transport**
(asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects. Every request is flagged ``sudo=True``: ufw mutations need
   root, so the commands ride under sudo.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``ufw.rule`` is the workhorse: it normalizes the desired rule (``port``
as an int, a ``"8000:8010"`` range or a tuple of ports for a multiport
rule; ``protocol``, ``action`` allow/deny/reject/limit, ``direction``
in/out, ``route``, ``from_ip``/``to_ip``, ``app`` profile, ``interface``,
``log``, ``comment``, ``insert`` position), reads the ``UfwRules`` fact
and yields ``ufw allow ...`` only when no matching rule exists. ufw
maintains IPv4 and IPv6 rules side by side, so one add creates both the
plain row and its ``(v6)`` twin and the operation matches either family
correctly. ``present=False`` deletes by rule specification (never by
number, which shifts), removing every match.

The deploy allows SSH first -- the lockout guard every firewall deploy
needs -- then opens HTTP/HTTPS from a trusted subnet with one multiport
rule (``port=(80, 443)``: a tuple, because request arguments must stay
hashable). The SSH request is yielded twice: in the prepare phase (where
nothing is sent) both plan as changes, but in the apply phase the second
request's fresh per-step fact cache sees the first add and reports the
rule already exists.

**This example is compile-checked by the test suite, not executed
against the shared demo hosts**: adding rules to a live firewall is an
operator decision, not a demo side effect. Run it only against hosts
whose intended ruleset you have verified.

The demo hosts ship without ufw (EPEL-only on RHEL-family), so the deploy
first installs ``epel-release`` and ``ufw`` via ``dnf.packages`` under
``sudo=True``. Installing ufw does not activate it, so the install step is
safe; the rule changes below remain compile-checked, not executed.

Run it with::

    uv run python examples/modules/ufw/ufw_rule.py
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
    op,
    run,
    ufw,
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
    """Allow SSH, then HTTP/HTTPS from the trusted subnet.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    ufw rule changes need root, so every request is flagged ``sudo=True``.
    """
    # ufw is EPEL-only on RHEL-family: install the repo then ufw first
    # (idempotent; the exit-masked ufw facts tolerate its absence in prepare).
    yield op(
        dnf.packages, name="Install EPEL repository", packages=("epel-release",), sudo=True
    )
    yield op(dnf.packages, name="Install ufw", packages=("ufw",), sudo=True)
    yield op(
        ufw.rule,
        name="Allow SSH inbound",
        port=22,
        protocol="tcp",
        comment="Allow SSH inbound",
        sudo=True,
    )
    yield op(
        ufw.rule,
        name="Allow SSH inbound (again)",
        port=22,
        protocol="tcp",
        comment="Allow SSH inbound",
        sudo=True,
    )
    yield op(
        ufw.rule,
        name="Allow HTTP/HTTPS from the internal subnet",
        port=(80, 443),
        protocol="tcp",
        from_ip="192.168.0.0/24",
        comment="Allow HTTP/HTTPS from internal subnet",
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