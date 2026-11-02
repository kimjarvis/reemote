# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: edit a remote file in place with a pure transform via files.edit.

This is a *usage* example, not part of the library. It shows reemote5's
generic content-editing primitive, ``files.edit``: instead of a bespoke
operation for every config-file format (pyinfra ships ``server.etc_hosts``,
``files.line``, ``files.replace`` and more, each hard-coding one shape of
edit), ``files.edit`` reads a file's current text with the ``FileContents``
fact, applies a **caller-supplied pure transform**, and writes the result back
over the asyncssh connection with ``files.put``'s scp transfer -- but only when
the transformed content actually differs. Any "edit a file in place" task is
then just a transform.

The transform is a plain module-level function taking the current content
(``str``, or ``None`` when the file is absent) and returning the desired
content as a ``str``. Two rules matter:

* It must be **idempotent** -- ``transform(transform(x)) == transform(x)`` --
  so a second run is a no-op. Here ``ensure_marker`` appends a managed marker
  line only when it is not already present.
* It must be a **stable, hashable object** -- a module-level function, never a
  lambda or closure built inside the deploy generator. ``files.edit`` stores it
  in the ``OperationRequest`` kwargs, which the determinism guard compares by
  value across the prepare and apply phases; a fresh callable per phase would
  diverge and raise ``DeterminismError`` (exactly as a fresh IO stream would
  for ``files.put``/``files.get``).

Idempotence is content-equality based: the transform's result is compared
against the current content, and the write happens only when they differ. The
deploy edits the same file twice to prove it -- the second edit is a guaranteed
no-op because the *first* edit's upload is visible to it through the fresh
per-step fact cache, the "act, then read" property the lockstep apply phase
provides.

The write rides the connection as the login user (a transfer, so ``sudo``/``su``
do not apply to it), so the target must be **writable by that user** -- here a
path under the login user's home. A root-owned file such as ``/etc/hosts``
cannot be written this way; transform it into a writable path first and move it
into place with an elevated shell operation.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``): each plain ``Host`` lazily
opens an asyncssh connection, ``cat``s the file for real, and uploads the
transformed content over scp when it differs.

Run it with::

    uv run python examples/modules/files/files_edit.py
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

# The remote file to edit: under the login user's home, so the unprivileged scp
# transfer can write it.
REMOTE_PATH = "/home/kjarvis/reemote5-edit.txt"

# The managed line the transform ensures is present exactly once.
MARKER_LINE = "# managed by the reemote5 files.edit example"


def ensure_marker(current: str | None) -> str:
    """Pure, idempotent transform: return content with ``MARKER_LINE`` present.

    Takes the file's current text (or ``None`` when it is absent) and returns
    the desired text. If the marker is already a line, the content is returned
    unchanged -- so a second edit is a fixpoint and ``files.edit`` reports no
    change. Otherwise the marker is appended.
    """
    text = current or ""
    lines = text.splitlines()
    if MARKER_LINE in lines:
        return text
    lines.append(MARKER_LINE)
    return "\n".join(lines) + "\n"


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
    """Edit the same file twice: the second edit proves content idempotence.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases (a branch on a mutable fact would raise DeterminismError). The
    transform is a module-level function -- the same object in both phases --
    which is what keeps the two ``OperationRequest`` values equal. Neither
    request is elevated: the write goes over the connection's scp channel as
    the login user.
    """
    yield op(files.edit, name="Ensure marker", path=REMOTE_PATH, transform=ensure_marker)
    yield op(
        files.edit, name="Re-ensure marker", path=REMOTE_PATH, transform=ensure_marker
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. The file is still ``cat``'d over SSH
        # (read-only) so the plan reflects each host's real pre-deployment
        # state; no content is transferred.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. The first edit
        # uploads the transformed content over scp; the second sees it through
        # the fresh per-step fact cache and reports no change.
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
