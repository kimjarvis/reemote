# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Refresh GPG keys on the target hosts from the keyservers.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``gpg.refresh_keys`` is the gpg namespace's only keyserver touchpoint
(``gpg.key`` deliberately dropped its ``keyserver`` install). One
``GpgRefreshKeysCommand`` renders ``gpg --batch --refresh-keys`` with the
requested key ids appended (``0x`` prefixes accepted), or no ids to
refresh every key in the *executing user's* keychain. The ids ride as a
module-level *tuple* -- ``OperationRequest`` kwargs must stay hashable,
so a list would be rejected by ``op()``. The request is flagged
``sudo=True`` here to refresh root's keychain.

The operation is stateless/non-idempotent: whether a keyserver holds an
update is unknowable without contacting it, so the refresh always runs
and always reports a change -- both requests below stay changed even in
the apply log.

**This example is compile-checked by the test suite, not executed
against the shared demo hosts**: it contacts public keyservers from
every host -- a network operation that can be slow or fail when the
keyservers are unreachable (surfacing as ``CommandError``). Run it
against your own hosts when you actually want the refresh.

Run it with::

    uv run python examples/modules/gpg/gpg_refresh_keys.py
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

# The keys to refresh, as a tuple: OperationRequest kwargs must stay
# hashable (a list would raise UnhashableArgumentError). The Docker CE
# release key's long id is a realistic stand-in.
KEYIDS = ("8D81803C0EBFCD88",)

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
    """Refresh the demo key from the keyservers, twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    The refresh is stateless, so both requests contact the keyservers --
    the second is not a no-op even though the first just refreshed.
    """
    yield op(
        gpg.refresh_keys,
        name="Refresh demo GPG key",
        keyids=KEYIDS,
        sudo=True,
    )
    yield op(
        gpg.refresh_keys,
        name="Refresh demo GPG key (again)",
        keyids=KEYIDS,
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