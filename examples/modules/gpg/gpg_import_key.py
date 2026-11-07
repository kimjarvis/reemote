# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Import a GPG key from a controller-side stream into the target keychain.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``gpg.import_key`` imports key content into the *executing user's*
keychain (unlike ``gpg.key``, which installs a standalone keyring file).
The ``src`` is the key *content* as a seekable IO-like stream on the
control machine, never a filename or URL. One request yields two
commands: a ``GpgImportUploadCommand`` transfer spools the stream to a
fixed ``/tmp`` path over scp as the login user, then a
``GpgImportCommand`` runs one ``sh -c`` that imports the spool with
``gpg --batch --yes --import`` and removes it. The request is flagged
``sudo=True`` here so the key lands in root's keychain; the transfer
stays unprivileged regardless.

Without a ``keyid`` the import is deliberately non-idempotent: the
fingerprint of a raw key stream is unknowable controller-side, so the
operation always imports and always reports a change -- both requests
below stay changed even in the apply log. Pass ``keyid=...`` to compare
against the ``GpgKeys`` fact instead and skip an already-imported key.

**This example is compile-checked by the test suite, not executed
against the shared demo hosts**: the placeholder bytes below are not a
real key -- ``gpg --import`` would reject them. Replace the stream with
a real vendor key before running it against your own hosts.

The stream is compared *by identity* across the prepare and apply
phases, so it is defined once at module level below -- building a fresh
stream inside the deploy generator would diverge between the phases and
raise ``DeterminismError``.

Run it with::

    uv run python examples/modules/gpg/gpg_import_key.py
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
    format_log,
    format_plan,
    gpg,
    op,
    run,
)

# The key content, as ONE module-level stream: OperationRequest compares the
# IO object by identity, so both phases must pass the same object (a stream
# built inside the generator would trip the determinism guard). Placeholder
# armored bytes -- replace with a real vendor key before deploying.
GPG_KEY = io.BytesIO(
    b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
    b"mQIN reemote5 example placeholder key material\n"
    b"-----END PGP PUBLIC KEY BLOCK-----\n"
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
    """Import the key stream into root's keychain, twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    Without a ``keyid`` both requests import -- gpg deduplicates the key
    material itself, but reemote5 cannot know that controller-side, so
    both stay changed in the apply log too.
    """
    yield op(
        gpg.import_key,
        name="Import demo GPG key",
        src=GPG_KEY,
        sudo=True,
    )
    yield op(
        gpg.import_key,
        name="Import demo GPG key (again)",
        src=GPG_KEY,
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