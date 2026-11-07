# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Export a GPG key from the target keychain to a file on the target host.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``gpg.export_key`` writes one key out of the *executing user's* keychain
to a ``dest`` path on the target host -- both ends stay on the target, no
transfer is involved. One ``GpgExportCommand`` renders a single ``sh -c``
that creates the destination's parent directory and redirects
``gpg --batch --yes --armor --export <keyid>`` into the file (drop
``armor=True`` for binary output). The request is flagged ``sudo=True``
here to export from root's keychain. ``secret=True`` would switch to
``--export-secret-keys`` behind ``--pinentry-mode loopback`` -- there is
deliberately no passphrase parameter, so a passphrase-protected secret
key fails fast instead of prompting.

The operation is stateless/non-idempotent: it always exports and always
reports a change, reading no fact -- both requests below stay changed
even in the apply log, and the second export overwrites the first
export's file.

**This example is compile-checked by the test suite, not executed
against the shared demo hosts**: it assumes the key id below already
exists in root's keychain on every host and overwrites the destination
file. Adjust the key id and destination before running it against your
own hosts.

Run it with::

    uv run python examples/modules/gpg/gpg_export_key.py
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

# The Docker CE release key's long id -- a realistic stand-in; the key must
# already be imported in root's keychain on the target hosts.
KEYID = "8D81803C0EBFCD88"

# The export destination on the *target* host (its parent is created).
DEST = "/tmp/reemote5-demo-export.asc"

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
    """Export the key to ``DEST`` as ASCII armor, twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    The export is stateless, so both requests export -- the second simply
    overwrites the file the first wrote.
    """
    yield op(
        gpg.export_key,
        name="Export demo GPG key",
        keyid=KEYID,
        dest=DEST,
        armor=True,
        sudo=True,
    )
    yield op(
        gpg.export_key,
        name="Export demo GPG key (again)",
        keyid=KEYID,
        dest=DEST,
        armor=True,
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