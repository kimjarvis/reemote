# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Import a GPG key into the target host's rpm keyring, from a stream.

This is a *usage* example, not part of the library. It shows reemote5's
``dnf.key`` operation -- the one non-idempotent dnf operation -- driven end to
end over a **real SSH transport** (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``). Each plain ``Host``
lazily opens an asyncssh connection from its ``connection`` data, receives the
key over scp and runs the elevated import for real, and ``run`` closes those
connections when it finishes.

``run`` is a coroutine (both phases are async), so ``main`` is async and
driven by ``asyncio.run``.

The source is an **IO-like object only** -- pyinfra's ``dnf.key`` takes a
``src`` that is a *filename or URL* the target host reads directly
(``rpm --import <src>``), but reemote5 deliberately has no controller-side
file locations, so a ``str`` ``src`` raises ``ValueError`` and the key content
must be handed over as a stream: here a module-level ``io.BytesIO``. Two rules
follow. The stream must be *seekable*: it is rewound to position 0 before it
is read. And because ``OperationRequest`` equality compares the unhashable
stream *by identity*, the deploy must pass the same object in both phases --
hence the module-level constant rather than a fresh ``BytesIO`` inside the
generator (a new object per phase would trip the determinism guard).

``dnf.key`` is **non-idempotent** -- like pyinfra's it reads no fact, always
reports a change and always imports (rpm itself deduplicates an
already-imported key), so the plan and the apply log agree. The one request
yields *two* commands at different privilege levels: a transfer that spools
the stream to a ``/tmp`` path over scp as the login user (transfers are never
elevated), then an elevated ``rpm --import`` of that spool file -- wrapped in
``sh -c`` so the import *and* its cleanup ``rm`` both run as root. That import
writes to the root-owned rpm keyring, so the request is flagged ``sudo=True``
and the driver elevates only the shell half, never the scp half.

The key below is an **illustrative placeholder**, not a valid armored GPG key:
``rpm --import`` rejects malformed material, so a real apply needs a genuine
public key in ``GPG_KEY``. The dry run plans regardless (nothing is sent), and
the pytest suite never touches the network.

Run it with::

    uv run python examples/modules/dnf/dnf_key.py
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
    dnf,
    format_log,
    format_plan,
    op,
    run,
)

# The IO-like key content, created once on the control machine: the same object
# is passed to the request in every phase, so the prepare and apply request
# sequences compare equal (OperationRequest compares it by identity). This is a
# placeholder armored block -- replace it with a real public GPG key before a
# real apply, since ``rpm --import`` rejects invalid material.
GPG_KEY = io.BytesIO(
    b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
    b"\n"
    b"mQIN EXAMPLE -- replace with a real armored public GPG key before use.\n"
    b"=reemote5-placeholder\n"
    b"-----END PGP PUBLIC KEY BLOCK-----\n"
)

# The inventory: real hosts and their SSH connection information, supplied
# by the application to the library. The default host_factory materializes
# each entry into a plain Host that connects over asyncssh.
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
    """Import the GPG key stream into each host's rpm keyring.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The single request is non-idempotent -- it always imports -- and yields
    two commands at different privilege levels: an unprivileged scp spool of
    ``GPG_KEY`` to a ``/tmp`` path, then the elevated ``rpm --import`` of that
    path. Importing writes to the root-owned rpm keyring, so the request is
    flagged ``sudo=True``; the scp transfer stays unprivileged regardless.
    """
    yield op(dnf.key, name="Import GPG key", src=GPG_KEY, sudo=True)


async def main() -> None:
    try:
        # --dry: prepare phase only. Nothing is transferred or imported, so the
        # plan shows the change (the operation is non-idempotent) without
        # touching a host.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH. Each
        # host spools the key over scp, then runs the elevated rpm --import and
        # removes the spool file.
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
