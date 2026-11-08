# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: set ufw's logging level on two real hosts.

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

``ufw.logging`` validates the level (``off``/``on``/``low``/``medium``/
``high``/``full``), reads the ``logging`` word of the ``UfwStatus`` fact
and yields ``ufw logging <level>`` only when it differs. ``on`` mirrors
ufw's own semantics: it keeps the current level when already logging and
only turns logging on (at ``low``) when it is off. The target here is
``low`` -- ufw's installation default, enough to audit blocked packets
without flooding the kernel log.

**This example is compile-checked by the test suite, not executed
against the shared demo hosts**: changing a live firewall's logging is
an operator decision, not a demo side effect.

The deploy yields the same logging request twice: in the prepare phase
(where nothing is sent) both plan as changes, but in the apply phase the
second request's fresh per-step fact cache sees the first ``ufw logging``
and reports logging is already low.

The demo hosts ship without ufw (EPEL-only on RHEL-family), so the deploy
first installs ``epel-release`` and ``ufw`` via ``dnf.packages`` under
``sudo=True``. Installing ufw does not activate it, so the install step is
safe; the logging change below remains compile-checked, not executed.

Run it with::

    uv run python examples/modules/ufw/ufw_logging.py
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
    """Set the logging level to low, twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    ufw logging needs root, so every request is flagged ``sudo=True``.
    """
    # ufw is EPEL-only on RHEL-family: install the repo then ufw first
    # (idempotent; the exit-masked ufw facts tolerate its absence in prepare).
    yield op(
        dnf.packages, name="Install EPEL repository", packages=("epel-release",), sudo=True
    )
    yield op(dnf.packages, name="Install ufw", packages=("ufw",), sudo=True)
    yield op(
        ufw.logging,
        name="Log blocked packets at the low level",
        level="low",
        sudo=True,
    )
    yield op(
        ufw.logging,
        name="Log blocked packets at the low level (again)",
        level="low",
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