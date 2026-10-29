"""Example: upload an in-memory stream to two real hosts with files.put.

This is a *usage* example, not part of the library. It shows reemote5's
first **non-shell** operation: ``files.put`` does not run a command string,
it copies content from the control machine over each host's asyncssh
connection using the scp protocol.

The source is an **IO-like object only** -- pyinfra's ``files.put`` accepts
``str | IO[Any]``, but reemote5 deliberately supports just the stream form:
here a module-level ``io.BytesIO``. Two rules follow from that. The stream
must be *seekable*: it is rewound to position 0 before every read, so the
prepare-phase hash, the apply-phase hash and the copy itself all see the
same content. And because ``OperationRequest`` equality compares the
unhashable stream *by identity*, the deploy must pass the same object in
both phases -- hence the module-level constant rather than a fresh
``BytesIO`` inside the generator (a new object per phase would trip the
determinism guard).

The idempotence check is content-based, mirroring pyinfra's ``files.put``:
the source's sha1 (``LocalSha1``, read here on the control machine) is
compared against the remote file's (``RemoteFileSha1``, gathered with
``sha1sum`` over SSH), and the transfer only happens when the two differ.
The deploy puts the same stream twice to prove it: the second put is a
guaranteed no-op because the *first* put's upload is visible to it through
the fresh per-step fact cache -- exactly the "act, then read" property the
lockstep apply phase provides.

The transfer rides the connection as the login user, so the destination
must be writable by that user (``sudo``/``su`` do not apply to transfers);
here it is a path under the login user's home directory.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``): each plain ``Host``
lazily opens an asyncssh connection, gathers the remote hash for real, and
receives the stream's content over scp when it differs.

Run it with::

    uv run python examples/modules/files/files_put.py
"""

import asyncio
import io
from collections.abc import Generator

from reemote5 import (
    Host,
    OperationRequest,
    files,
    format_log,
    format_plan,
    op,
    run,
)

# The IO-like source, created once on the control machine: the same object
# is passed to every put in every phase, so the prepare and apply request
# sequences compare equal (OperationRequest compares it by identity).
LOCAL_SRC = io.BytesIO(b"hello from the reemote5 files.put example\n")

# The remote destination: under the login user's home, so the unprivileged
# scp transfer can write it.
REMOTE_DEST = "/home/kjarvis/reemote5-put.txt"

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
    """Put the same stream twice: the second put proves sha1 idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    Neither request is elevated: the copy goes over the connection's scp
    channel as the login user.
    """
    yield op(files.put, name="Upload message", src=LOCAL_SRC, dest=REMOTE_DEST)
    yield op(files.put, name="Re-upload message", src=LOCAL_SRC, dest=REMOTE_DEST)


async def main() -> None:
    # --dry: prepare phase only. The remote sha1 is still gathered over SSH
    # (read-only) so the plan reflects each host's real pre-deployment state;
    # no content is transferred.
    plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
    print("== Plan (prepare phase, --dry) ==")
    print(format_plan(plans))

    # Real run: prepare, then apply in lockstep across hosts. The first put
    # transfers over scp; the second sees the uploaded hash and reports no
    # change.
    plans, report, _hosts = await run(INVENTORY, deploy)
    print("\n== Run log (apply phase) ==")
    assert report is not None
    print(format_log(report))


if __name__ == "__main__":
    asyncio.run(main())
