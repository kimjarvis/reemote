"""Example: move a remote file into a remote directory with files.move.

This is a *usage* example, not part of the library. It shows ``files.move``
(pyinfra ``files.move`` parity): ``src`` is a remote path, ``dest`` is the
remote *directory* to move it into, and the moved path becomes
``dest/basename(src)``. With ``overwrite=True`` an existing target is removed
first (``rm -rf``) before the ``mv``; without it an existing target raises
``ValueError``.

Two properties shape the script:

* ``files.move`` is **stateless** like pyinfra: a valid move always yields a
  command, there is no idempotent no-op branch. Re-running a move whose
  source is gone fails fail-fast (here ``overwrite=True`` keeps the *target*
  side re-runnable; the source is re-staged by run 1 each time).
* An absent source or an absent/non-directory destination raises
  ``ValueError`` in **both** phases -- reemote5 operations are phase-unaware,
  so pyinfra's soft plan-time logging of those cases is deliberately dropped.
  A "create the file then move it" flow therefore cannot share one deploy:
  the prepare phase would raise on the not-yet-created source. That is why
  this script spans two complete runs, the sanctioned split also shown in
  ``examples/general/two_deploy_split.py``.

Run 1 stages the payload with ``files.put`` (an unprivileged scp transfer of
a module-level stream -- one stable identity across both phases) into
``/tmp``. Run 2 moves it into the login user's home. Both the staging write
and the destination are login-user-writable, so nothing here is elevated;
for a root-owned destination directory such as ``/etc`` the move request
would carry ``sudo=True`` (see ``files_edit_etc_hosts.py``).

Run it with::

    uv run python examples/modules/files/files_move.py
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
    files,
    format_log,
    format_plan,
    op,
    run,
)

# The payload, created once on the control machine: a stream, never a local
# path. The same object is passed to put in every phase, so the prepare and
# apply request sequences compare equal.
PAYLOAD = io.StringIO("staged by the reemote5 files.move example\n")

# Staging source under /tmp and the destination directory: its basename
# makes the moved path /home/kjarvis/reemote5-move.txt.
STAGED = "/tmp/reemote5-move.txt"
DEST_DIR = "/home/kjarvis"

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


def stage(host: Host) -> Generator[OperationRequest]:
    """Run 1: stage the payload into /tmp, unprivileged, branch-free."""
    yield op(files.put, name="Stage payload", src=PAYLOAD, dest=STAGED)


def install(host: Host) -> Generator[OperationRequest]:
    """Run 2: move the staged file into the home directory.

    ``overwrite=True`` removes a target left by a previous script run before
    the mv; the move itself is stateless and always plans a command.
    """
    yield op(
        files.move,
        name="Move payload home",
        src=STAGED,
        dest=DEST_DIR,
        overwrite=True,
    )


async def main() -> None:
    try:
        # Run 1: --dry plan, then the real staging transfer.
        plans, _, _hosts = await run(INVENTORY, stage, dry=True)
        print("== Run 1 plan: stage payload (--dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(INVENTORY, stage)
        print("\n== Run 1 log: stage payload ==")
        assert report is not None
        print(format_log(report))

        # Run 2: the source now really exists, so the move's validation
        # passes in the prepare phase too. --dry first, then the real move.
        plans, _, _hosts = await run(INVENTORY, install, dry=True)
        print("\n== Run 2 plan: move payload (--dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(INVENTORY, install)
        print("\n== Run 2 log: move payload ==")
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