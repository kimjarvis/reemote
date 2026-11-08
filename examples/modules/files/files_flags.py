# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: set and clear an ext2 attribute flag with files.flags.

This is a *usage* example, not part of the library. It shows ``files.flags``
(pyinfra ``files.flags`` parity, in the developer-chosen Linux adaptation):
pyinfra's BSD verb pair -- ``chflags`` reading ``stat -f %Sf`` flag *names*
like ``hidden`` -- does not exist on Linux, so the same set/clear capability
is expressed over ``lsattr -d`` attribute *letters* (read back through the
``FileFlags`` fact) and ``chattr +flag``/``-flag``. Here the ``i`` letter is
the immutable attribute: with it set the file cannot be modified, renamed or
removed even by its owner, until the flag is cleared again.

Two properties shape the script:

* The idempotence is verbatim pyinfra's. A set yields only the requested
  letters *missing* from the current set and a clear only those *present*, so
  a request whose flags already match the file reports "already has 'i'
  set"/"clear" and sends nothing. ``flags`` is a tuple of one-letter strings
  (never a list) because request arguments must stay hashable across the two
  phases; each letter is validated against the ext2 vocabulary before it
  reaches the host.
* ``chattr`` needs ``CAP_LINUX_IMMUTABLE``, so every flags request carries
  ``sudo=True`` regardless of who owns the file. The deploy is deliberately
  **self-reversing** -- it sets ``i`` and then clears it in the same run --
  so a real execution leaves the marker exactly as it found it (not
  permanently immutable). It first creates the marker with ``files.file``
  because an absent path reads no flags, and a ``chattr`` on a missing file
  would fail at apply.

The marker lives in the login user's home, so the create is unprivileged;
only the two ``chattr`` steps are elevated. ``files.file`` here is a
prerequisite, not a second demonstration of that operation.

Run it with::

    uv run python examples/modules/files/files_flags.py
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

# The marker file the attribute is toggled on. Its home-directory location
# keeps the create unprivileged; chattr is elevated no matter the owner.
MARKER = "/home/kjarvis/reemote5-flags.txt"

# The immutable attribute letter, as a *tuple* -- op() hashes every argument,
# so a list would be rejected. Each element is validated against the ext2
# vocabulary (``_LINUX_FLAG_LETTERS``) before any command is planned.
IMMUTABLE = ("i",)

# The inventory: real hosts and their SSH connection information. The default
# host_factory materializes each entry into a plain Host that connects over
# asyncssh. web1/web2 are the CentOS fleet, where lsattr/chattr exist.
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
    """Create the marker, set the immutable flag, then clear it again.

    Branch-free and hashable: every request carries the same ``IMMUTABLE``
    tuple in both phases, so the prepare plan and the apply log pair step for
    step. The final clear makes the run self-reversing, so nothing stays
    immutable on the host once the deploy finishes.
    """
    yield op(
        files.file,
        name="Create marker",
        path=MARKER,
    )
    yield op(
        files.flags,
        name="Set immutable",
        path=MARKER,
        flags=IMMUTABLE,
        present=True,
        sudo=True,
    )
    yield op(
        files.flags,
        name="Clear immutable (restore)",
        path=MARKER,
        flags=IMMUTABLE,
        present=False,
        sudo=True,
    )


async def main() -> None:
    try:
        # --dry plan first: the whole sequence computed against the current
        # host state, nothing sent.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Then the real apply: create, chattr +i, chattr -i. Each step's
        # fresh fact cache sees the previous step's effect, so the clear
        # really does find the flag set (in prepare it reads the not-yet-
        # created file and is planned as a no-op -- the documented
        # prepare-versus-apply difference).
        plans, report, _hosts = await run(INVENTORY, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed
        # error whose message names the host and the failing command. Print
        # that message -- no traceback -- and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
