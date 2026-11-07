# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: copy a remote file into a remote directory with files.copy.

This is a *usage* example, not part of the library. It shows ``files.copy``
(pyinfra ``files.copy`` parity): ``src`` is a remote path, ``dest`` is the
remote *directory* to copy it into, and the copied path becomes
``dest/basename(src)`` -- with the source left in place, the difference from
``files.move``. With ``overwrite=True`` the copy is forced (``cp -r -f``);
without it an existing target with *equal* content is a no-op and one with
different content raises ``ValueError``.

Two properties shape the script:

* Unlike the stateless ``files.move``, ``files.copy`` has an idempotent
  branch: an existing regular-file target whose sha1 matches the source
  reports "already exists" with no command. This script uses
  ``overwrite=True`` so a target left by a previous run is simply
  overwritten; drop the flag and the second script run becomes a no-op.
* An absent source or an absent/non-directory destination raises
  ``ValueError`` in **both** phases -- reemote5 operations are phase-unaware,
  so pyinfra's soft plan-time logging of those cases is deliberately dropped
  (the same strictness as ``files.move``). A "create the file then copy it"
  flow therefore cannot share one deploy: the prepare phase would raise on
  the not-yet-created source. That is why this script spans two complete
  runs, the sanctioned split also shown in
  ``examples/demonstrations/composition/two_deploy_split.py``.

Run 1 stages the payload with ``files.put`` (an unprivileged scp transfer of
a module-level stream -- one stable identity across both phases) into
``/tmp``. Run 2 copies it into the login user's home, leaving the staging
file in ``/tmp``. Both the staging write and the destination are
login-user-writable, so nothing here is elevated; for a root-owned
destination directory such as ``/etc`` the copy request would carry
``sudo=True``.

Run it with::

    uv run python examples/modules/files/files_copy.py
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
PAYLOAD = io.StringIO("staged by the reemote5 files.copy example\n")

# Staging source under /tmp and the destination directory: its basename
# makes the copied path /home/kjarvis/reemote5-copy.txt.
STAGED = "/tmp/reemote5-copy.txt"
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
    """Run 2: copy the staged file into the home directory.

    ``overwrite=True`` forces the copy over a target left by a previous
    script run (``cp -r -f``); the staging file stays in /tmp.
    """
    yield op(
        files.copy,
        name="Copy payload home",
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

        # Run 2: the source now really exists, so the copy's validation
        # passes in the prepare phase too. --dry first, then the real copy.
        plans, _, _hosts = await run(INVENTORY, install, dry=True)
        print("\n== Run 2 plan: copy payload (--dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(INVENTORY, install)
        print("\n== Run 2 log: copy payload ==")
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