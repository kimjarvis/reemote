# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install a GPG keyring file from a controller-side key stream.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``gpg.key`` installs a GPG key as a keyring file at an explicit ``dest``
path on the target host. The ``src`` is the key *content* as a seekable
IO-like stream on the control machine, never a filename or URL (fetch a
URL on the controller and pass its bytes). One request yields two
commands at different privilege levels: a ``GpgKeyUploadCommand``
transfer spools the stream to a fixed ``/tmp`` path over scp as the login
user, then a ``GpgKeyInstallCommand`` runs one elevated ``sh -c`` that
creates the destination's parent directory, dearmors the key with
``gpg --batch --dearmor`` when the content is ASCII-armored (binary
content is copied verbatim), chmods the keyring ``0644`` and removes the
spool file. The request is flagged ``sudo=True`` for the install; the
transfer stays unprivileged regardless, so the log shows a plain ``scp``
line followed by ``sudo sh -c``.

Without a ``keyid`` the operation is idempotent on the destination's
*existence* (pyinfra's no-keyid semantics): an existing file is left
untouched, whatever keys it holds. Pass ``keyid=...`` to compare the
actual keys held in the keyring instead, or ``present=False`` to remove
the file. For the APT repository-keyring use case with its ``/etc/apt/
keyrings/`` defaults, ``apt.key`` is the specialized operation.

**This example is compile-checked by the test suite, not executed
against the shared demo hosts**: it writes a keyring file under
``/etc/apt/keyrings/`` on every host, and the placeholder bytes below are
not a real key -- ``gpg --dearmor`` would reject them. Replace the stream
with a real vendor key before running it against your own hosts.

The stream is compared *by identity* across the prepare and apply
phases, so it is defined once at module level below -- building a fresh
stream inside the deploy generator would diverge between the phases and
raise ``DeterminismError``.

The deploy yields the same install request twice: in the prepare phase
(where nothing is sent) both plan as changes, but in the apply phase the
second request's fresh per-step fact probe sees the file the first
install created and reports it already exists.

Run it with::

    uv run python examples/modules/gpg/gpg_key.py
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
    """Install the key stream as ``/etc/apt/keyrings/reemote5-demo.gpg``, twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    Writing under ``/etc/apt/keyrings/`` needs root, so every request is
    flagged ``sudo=True``; the driver elevates the install shell while the
    spool transfer rides scp as the login user.
    """
    yield op(
        gpg.key,
        name="Install demo GPG keyring",
        src=GPG_KEY,
        dest="/etc/apt/keyrings/reemote5-demo.gpg",
        sudo=True,
    )
    yield op(
        gpg.key,
        name="Install demo GPG keyring (again)",
        src=GPG_KEY,
        dest="/etc/apt/keyrings/reemote5-demo.gpg",
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