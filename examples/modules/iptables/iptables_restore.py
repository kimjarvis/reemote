# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: atomically restore an uploaded rule snapshot on two real hosts.

This is a *usage* example, not part of the library. It drives reemote5's
two-phase execution model end to end over a **real SSH transport**
(asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects: first ``files.put`` uploads the snapshot (unprivileged scp
   into the login user's home), then ``iptables.restore`` loads it under
   ``sudo=True``.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``iptables.restore`` is the atomic bulk-apply surface: iptables-restore
applies the whole file or fails without partial state -- the resolution
of the ``bulk_rule(rules=[...])`` proposal, which cannot exist because
``op()`` arguments must be hashable. The path is HOST-side (reemote5
transfers are IO-only), so a controller-side snapshot rides ``files.put``
first: the source here is a module-level ``io.BytesIO``, seekable and
identity-stable across phases (a fresh stream inside the generator would
trip the determinism guard). The restore is idempotent on the
``IptablesPersistence`` fact: when the file already reproduces the live
rules, loading it changes nothing and is skipped.

**This example is compile-checked by the test suite, not executed
against the shared demo hosts**: a restore with the default
``noflush=False`` replaces the entire live rule set. The snapshot below
keeps INPUT's policy ACCEPT and an explicit SSH ACCEPT rule so a real
run could not lock the deploy out, but replacing a production firewall
is always an operator decision.

The deploy yields the restore twice. In the prepare phase the snapshot
file does not exist yet (the put has not run), so both restores plan as
changes; in the apply phase the first restore loads the uploaded file
and the second's fresh per-step fact cache sees live rules matching it
and reports no change.

Run it with::

    uv run python examples/modules/iptables/iptables_restore.py
"""

import asyncio
import io
import sys
from collections.abc import Generator

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    OperationRequest,
    PrivilegeError,
    files,
    format_log,
    format_plan,
    iptables,
    op,
    run,
)

# The snapshot in iptables-restore format, created once on the control
# machine: the same BytesIO object is passed to the put in every phase
# (OperationRequest compares streams by identity). The rule set keeps
# INPUT permissive and SSH explicitly allowed.
RULES_V4 = io.BytesIO(
    b"*filter\n"
    b":INPUT ACCEPT [0:0]\n"
    b":FORWARD ACCEPT [0:0]\n"
    b":OUTPUT ACCEPT [0:0]\n"
    b"-A INPUT -p tcp -m tcp --dport 22 -j ACCEPT\n"
    b"COMMIT\n"
)

# The host-side snapshot path: under the login user's home, so the
# unprivileged scp transfer can write it; iptables-restore then reads it
# elevated through the sh -c wrapper.
REMOTE_RULES = "/home/kjarvis/reemote5-rules.v4"

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
    """Upload the snapshot, then restore it twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    The put is an unprivileged transfer (sudo/su do not apply to
    transfers); the restores need CAP_NET_ADMIN and ride ``sudo=True``.
    """
    yield op(
        files.put,
        name="Upload the rule snapshot",
        src=RULES_V4,
        dest=REMOTE_RULES,
    )
    yield op(
        iptables.restore,
        name="Restore the rule snapshot",
        path=REMOTE_RULES,
        sudo=True,
    )
    yield op(
        iptables.restore,
        name="Restore the rule snapshot (again)",
        path=REMOTE_RULES,
        sudo=True,
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no content
        # is transferred and no command that mutates a host is sent.
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