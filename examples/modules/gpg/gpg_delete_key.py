# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Delete a GPG key from the target keychain, idempotently.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``gpg.delete_key`` removes a key from the *executing user's* keychain by
key id, long id or fingerprint. It is idempotent against the ``GpgKeys``
fact: a keyid matching nothing reports no change and sends nothing. When
the key is present, the matching entry's full fingerprint drives one
``GpgDeleteKeyCommand`` -- ``gpg --batch --yes --delete-keys <fpr>`` (gpg
refuses ambiguous short ids under ``--batch``, so the resolution happens
controller-side). ``secret=True`` would consult ``GpgSecretKeys`` and
delete both halves with ``--delete-secret-and-public-keys``. The request
is flagged ``sudo=True`` here to act on root's keychain.

**This example is compile-checked by the test suite, not executed
against the shared demo hosts**: it deletes a key from root's keychain
on every host -- a destructive change to shared state. Point the key id
at a disposable key before running it against your own hosts.

The deploy yields the same delete request twice: in the prepare phase
(where nothing is sent) both plan as changes when the key is present,
but in the apply phase the second request's fresh per-step fact read no
longer sees the key the first delete removed and reports it already
absent.

Run it with::

    uv run python examples/modules/gpg/gpg_delete_key.py
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

# The Docker CE release key's long id -- a realistic stand-in for the key
# to remove; the full fingerprint is resolved from the GpgKeys fact.
KEYID = "8D81803C0EBFCD88"

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
    """Delete the key from root's keychain, twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    The second request demonstrates idempotence: its apply-phase fact
    read sees the key already gone and reports no change.
    """
    yield op(
        gpg.delete_key,
        name="Delete demo GPG key",
        keyid=KEYID,
        sudo=True,
    )
    yield op(
        gpg.delete_key,
        name="Delete demo GPG key (again)",
        keyid=KEYID,
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