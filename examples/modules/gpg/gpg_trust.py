# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Assign ownertrust to a GPG key on the target host, idempotently.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``gpg.trust`` assigns ownertrust to a full fingerprint in the *executing
user's* trust database. One ``GpgTrustCommand`` renders a single
``sh -c`` that printf-pipes one ``<fpr>:<n>:`` line into
``gpg --batch --import-ownertrust``. ``level`` is one of ``unknown``,
``never``, ``marginal``, ``full`` or ``ultimate`` (anything else raises
``ValueError`` before a command is sent) and maps to gpg's numeric
values, ultimate=6. The request is flagged ``sudo=True`` here to act on
root's trust database.

The operation is idempotent against the ``GpgTrust`` fact, compared by
level *name*: a trustdb entry of 5 or 6 both satisfy ``ultimate`` (gpg
itself exports a freshly generated own key as 6). A fingerprint already
at the requested level reports no change and sends nothing.

The deploy yields the same assignment request twice: in the prepare
phase (where nothing is sent) both plan as changes when the level
differs, but in the apply phase the second request's fresh per-step fact
read sees the assignment the first import wrote and reports it already
at that level.

Run it with::

    uv run python examples/modules/gpg/gpg_trust.py
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
    gpg,
    op,
    run,
)

# The Docker CE release key's full fingerprint -- ownertrust is always
# assigned by fingerprint, never by a short id.
FINGERPRINT = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"

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
    """Trust the demo key ultimately in root's trust database, twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    The second request demonstrates idempotence: its apply-phase fact
    read sees the assignment the first import wrote and reports no
    change.
    """
    yield op(
        gpg.trust,
        name="Trust demo GPG key ultimately",
        fingerprint=FINGERPRINT,
        level="ultimate",
        sudo=True,
    )
    yield op(
        gpg.trust,
        name="Trust demo GPG key ultimately (again)",
        fingerprint=FINGERPRINT,
        level="ultimate",
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