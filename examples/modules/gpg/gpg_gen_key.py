# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Generate an unprotected GPG key on the target host, idempotently.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``gpg.gen_key`` generates a key for ``"{real_name} <{email}>"`` in the
*executing user's* keychain from a gpg batch parameter block. One
``GpgGenKeyCommand`` renders a single ``sh -c`` that printf-pipes the
``Key-Type``/``Key-Length``/``Name-Real``/``Name-Email``/``Expire-Date``/
``%no-protection`` lines into ``gpg --batch --gen-key`` -- every field is
a hashable string, so no transfer is needed. The parameter is
``real_name``, not ``name``, because ``name`` is an ``op()``
meta-parameter (the request label). ``algo``, ``length`` and ``expire``
default to rsa/4096/never; passphrase-protected generation is
deliberately out of scope (``%no-protection`` is fixed -- secrets never
ride in plans or logs). The request is flagged ``sudo=True`` here to
generate into root's keychain.

The operation is idempotent against the ``GpgKeys`` fact: when a key with
that exact uid already exists the generation is skipped and no command
is sent.

**This example is compile-checked by the test suite, not executed
against the shared demo hosts**: key generation is slow (rsa4096 needs
entropy) and adds a permanent key to root's keychain on every host.
Adjust the identity before running it against your own hosts, and clean
up with ``gpg.delete_key`` afterwards.

The deploy yields the same generation request twice: in the prepare
phase (where nothing is sent) both plan as changes, but in the apply
phase the second request's fresh per-step fact read sees the uid the
first generation created and reports it already exists.

Run it with::

    uv run python examples/modules/gpg/gpg_gen_key.py
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

# The identity of the generated key: its uid reads "Reemote5 Demo
# <demo@example.com>", and that exact uid is the idempotence probe.
REAL_NAME = "Reemote5 Demo"
EMAIL = "demo@example.com"

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
    """Generate the demo key in root's keychain, twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    The second request demonstrates idempotence: its apply-phase fact
    read sees the uid the first generation created and reports no change.
    """
    yield op(
        gpg.gen_key,
        name="Generate demo GPG key",
        real_name=REAL_NAME,
        email=EMAIL,
        sudo=True,
    )
    yield op(
        gpg.gen_key,
        name="Generate demo GPG key (again)",
        real_name=REAL_NAME,
        email=EMAIL,
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