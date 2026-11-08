# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: converge to a fixed file state (literal no-op on rerun).

This is a *usage* example, not part of the library. Where its sibling
``files_flags.py`` is **self-reversing** (it sets the immutable flag and
clears it again in the same run, so a rerun re-applies the same ``chattr +i``
then ``chattr -i`` cycle), this one is a plain **converge-to-state** deploy:
every request declares a desired end state and never undoes it, so the second
invocation of the exact same script reports *no change on every step*. That is
the literal idempotence guarantee reemote5 operations are built around, shown
across two different converge dimensions at once:

* ``files.file`` with a fixed ``mode``. It reads the ``File`` fact and yields
  only the gaps: a ``touch`` for the missing file plus a ``chmod`` for the
  requested octal mode (``600`` here, chosen to *differ* from a plain
  ``touch``'s default ``644`` so the first run really does issue the ``chmod``).
  A rerun reads the mode already equal and sends nothing.
* ``files.flags`` with ``present=True`` and **no** restore step. It reads the
  ``FileFlags`` fact (``lsattr -d`` behind the ``FileFlags`` fact) and yields a
  ``chattr +i`` only for the requested letters missing from the current set. A
  rerun finds ``i`` already set and is a no-op.

Both properties hinge on the *fresh per-step fact cache* in the apply phase:
the second run materializes against the state the first run left behind, so
every comparison closes. ``flags`` is a one-letter tuple (never a list) because
request arguments must stay hashable across the two phases, and the mode is an
octal-digit int. ``chattr`` needs ``CAP_LINUX_IMMUTABLE``, so the flags request
carries ``sudo=True``.

Unlike the self-reversing sibling, a real execution of *this* script leaves the
marker behind as it converged it: present, mode ``600`` and immutable. That
residue is exactly the point of a converge deploy -- nothing is undone. To
unwind it by hand, clear the flag first (``chattr -i`` -- an immutable file
cannot be removed even by root until the bit is off) and then remove the file.
The marker lives in the login user's home so only the ``chattr`` is elevated.

Run it with::

    uv run python examples/modules/files/files_flags_converge.py
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

# The marker file the desired state is converged onto. Its home-directory
# location keeps the create unprivileged; only the chattr is elevated. A
# distinct path from the self-reversing sibling so the two never collide.
MARKER = "/home/kjarvis/reemote5-flags-converge.txt"

# The octal mode to converge onto. Deliberately *not* 644 (what a bare
# ``touch`` leaves), so the create run genuinely issues a ``chmod`` and a
# rerun's equality check closes. An int is hashable, so it survives op().
DESIRED_MODE = 600

# The immutable attribute letter, as a *tuple* -- op() hashes every argument,
# so a list would be rejected. Validated against the ext2 vocabulary before
# any command is planned.
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
    """Converge the marker to: exists, mode 600, immutable.

    Branch-free and hashable: every request carries the same ``DESIRED_MODE``
    int and ``IMMUTABLE`` tuple in both phases, so the prepare plan and the
    apply log pair step for step. Neither request undoes the other -- this is a
    pure converge deploy, so a second run over the state the first left finds
    nothing to change.
    """
    yield op(
        files.file,
        name="Ensure marker with fixed mode",
        path=MARKER,
        mode=DESIRED_MODE,
    )
    yield op(
        files.flags,
        name="Ensure immutable flag",
        path=MARKER,
        flags=IMMUTABLE,
        present=True,
        sudo=True,
    )


async def main() -> None:
    try:
        # First invocation: --dry plan, then the real apply. Both steps plan a
        # command against a not-yet-existing marker.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(INVENTORY, deploy)
        print("\n== Run 1 log (apply phase) ==")
        assert report is not None
        print(format_log(report))

        # Second invocation of the SAME deploy: run against the state run 1
        # left behind (fresh per-step fact cache in prepare too). Every step
        # now reads back exactly what it requested, so the plan and the log
        # both report no change -- the literal idempotence this example is
        # demonstrating.
        plans, report, _hosts = await run(INVENTORY, deploy)
        print("\n== Run 2 log (rerun, expect all no-change) ==")
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
