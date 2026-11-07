# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Dearmor an ASCII-armored key file already on the target hosts.

This is a *usage* example, not part of the library. It drives reemote5's
two-phase execution model end to end over a **real SSH transport**
(asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``gpg.dearmor`` converts an armored key file to binary keyring form with
``gpg --batch --dearmor``. Both ``src`` and ``dest`` are paths on the
*target* host -- no bytes move over the connection (to install a key from
the control machine, use ``gpg.key``, which dearmors armored streams
itself). One request yields one ``sh -c`` that creates the destination's
parent directory, dearmors ``src`` into ``dest`` and chmods the result
``0644``. Like pyinfra's ``gpg.dearmor`` the operation is deliberately
**stateless**: it always converts and always reports a change, on every
run, even when ``dest`` already exists.

**This example is compile-checked by the test suite, not executed
against the shared demo hosts**: it writes a keyring file under
``/etc/apt/keyrings/`` on every host, and the armored source file must
already exist there (a missing ``src`` makes ``gpg --dearmor`` fail with
a ``CommandError``). Place a real armored key at ``src`` before running
it against your own hosts.

Run it with::

    uv run python examples/modules/gpg/gpg_dearmor.py
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

# The armored key file already on each target host, and the binary keyring
# to write beside it. Both are remote paths -- dearmor moves no bytes over
# the connection.
SRC = "/etc/apt/keyrings/reemote5-demo.asc"
DEST = "/etc/apt/keyrings/reemote5-demo.gpg"

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
    """Dearmor the armored key file into a binary keyring.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases. Writing under ``/etc/apt/keyrings/`` needs root, so the
    request is flagged ``sudo=True``.
    """
    yield op(
        gpg.dearmor,
        name="Dearmor demo GPG key",
        src=SRC,
        dest=DEST,
        sudo=True,
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. The operation reads no fact (it is
        # stateless), so the plan always shows the conversion as a change;
        # no command that mutates a host is sent.
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