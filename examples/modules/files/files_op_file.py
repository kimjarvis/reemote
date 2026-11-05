# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: ensure a regular file on two real hosts with files.file.

This is a *usage* example, not part of the library. It drives the
``files.file`` operation through reemote5's two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Write a *deploy generator* that yields ``op(files.file, ...)`` requests.
   The generator is plain sync code -- it only describes work, it never
   touches a host.
3. Call ``run(INVENTORY, deploy, dry=True)`` for the prepare phase: the
   operation is materialized against each host's real pre-deployment state
   (read-only fact gathering over SSH) and ``format_plan`` shows what would
   change. Nothing is sent.
4. Call ``run(INVENTORY, deploy)`` for the real run: prepare again, then
   apply in lockstep across hosts, with ``format_log`` showing every
   command and whether it changed anything.

The operation is idempotent: it reads existence through the boolean
``FileExists`` fact (``test -e`` over SSH) and yields a ``FileCommand`` only
on a difference -- ``touch`` creates a missing file, while ``present=False``
removes a present one with ``rm -f``. Unlike ``files.link``'s readlink-based
check, this probe also sees *regular files*, which makes ``files.file`` the
removal primitive for them. The deploy touches the same marker file twice
to prove idempotence: the second request is a guaranteed no-op because the
first request's ``touch`` is visible to it through the fresh per-step fact
cache -- exactly the "act, then read" property the lockstep apply phase
provides.

The marker lives under the login user's home directory, so no ``sudo``/
``su`` elevation is needed; a root-owned path (a config file under
``/etc``) would carry ``sudo=True`` on the request instead.

Unlike the pytest suite -- which injects the test framework's
``SimulatedHost`` via ``run``'s ``host_factory`` and never touches the
network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection, probes existence for real, and runs ``touch`` when the file is
missing.

Run it with::

    uv run python examples/modules/files/files_op_file.py
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
    files,
    format_log,
    format_plan,
    op,
    run,
)

# The marker file to ensure: under the login user's home, so the
# unprivileged shell can create it.
MARKER_PATH = "/home/kjarvis/reemote5-marker"

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
    """Touch the same marker twice: the second request proves idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    Neither request is elevated: the marker path is login-user writable.
    """
    yield op(files.file, name="Ensure marker", src=MARKER_PATH)
    yield op(files.file, name="Re-ensure marker", src=MARKER_PATH)


async def main() -> None:
    try:
        # --dry: prepare phase only. Existence is still probed over SSH
        # (read-only) so the plan reflects each host's real pre-deployment
        # state; no command is sent.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. The first
        # request runs touch when the marker is missing; the second sees it
        # present and reports no change.
        plans, report, _hosts = await run(INVENTORY, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed
        # error whose message names the host and the failing command. Print
        # that message -- no traceback -- and exit non-zero so the failure
        # stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
