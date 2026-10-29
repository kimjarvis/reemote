"""Example: download a remote file into an in-memory stream with files.get.

This is a *usage* example, not part of the library. It shows the mirror of
``files.put``: ``files.get`` does not run a command string, it copies a remote
file's content down over each host's asyncssh connection using the scp
protocol, into an **IO-like object** on the control machine.

The destination is a stream, never a local filename -- pyinfra's ``files.get``
writes to a local path (with ``add_deploy_dir``/``create_local_dir``/``force``
options reemote5 deliberately drops), but here the bytes land in a module-level
``io.BytesIO`` the script reads back afterwards. Two rules follow, mirroring
``files.put``. The stream must be *seekable* and *writable*: it is rewound and
truncated before the download, then rewound again so the caller reads the fresh
content from position 0. And because ``OperationRequest`` equality compares the
unhashable stream *by identity*, the deploy must pass the same object in both
phases.

There is one wrinkle the put example does not have: a get *writes* into its
destination, so two hosts must not share one stream -- in lockstep they would
overwrite each other, and each host's second get would see the other host's
bytes and wrongly re-download. The deploy therefore keeps a per-host sink in a
module-level dict, created once per host name and reused across both phases:
stable identity *and* per-host isolation.

The idempotence check is content-based, mirroring ``files.put``: the remote
file's sha1 (``RemoteFileSha1``, gathered with ``sha1sum`` over SSH) is
compared against the destination's current content sha1 (``LocalSha1``), and the
transfer only happens when they differ. The deploy gets the same file twice to
prove it: the second get is a guaranteed no-op because the *first* get's
download is visible to it through the fresh per-step fact cache -- exactly the
"act, then read" property the lockstep apply phase provides.

The transfer rides the connection as the login user, so the source must be
readable by that user (``sudo``/``su`` do not apply to transfers); here it is
``/etc/hostname``, present and world-readable on every host.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``): each plain ``Host`` lazily
opens an asyncssh connection, gathers the remote hash for real, and sends the
file's content over scp into the sink when it differs.

Run it with::

    uv run python examples/modules/files/files_get.py
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

# The remote source: present and readable by the login user on every host.
REMOTE_SRC = "/etc/hostname"

# One destination stream per host, created once and reused across both phases:
# the same object is passed to every get for a given host (so the prepare and
# apply request sequences compare equal -- OperationRequest compares it by
# identity), and distinct hosts get distinct sinks (so a get, which *writes*
# into its destination, never lets two hosts clobber each other in lockstep).
SINKS: dict[str, io.BytesIO] = {}

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
    """Get the same remote file twice: the second get proves sha1 idempotence.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases (a branch on a mutable fact would raise DeterminismError). Neither
    request is elevated: the copy goes over the connection's scp channel as the
    login user. The per-host sink is fetched, not created fresh per call, so
    both phases -- and both gets -- pass the identical stream object.
    """
    sink = SINKS.get(host.name)
    if sink is None:
        sink = SINKS[host.name] = io.BytesIO()
    yield op(files.get, name="Fetch hostname", src=REMOTE_SRC, dest=sink)
    yield op(files.get, name="Re-fetch hostname", src=REMOTE_SRC, dest=sink)


async def main() -> None:
    # --dry: prepare phase only. The remote sha1 is still gathered over SSH
    # (read-only) so the plan reflects each host's real pre-deployment state;
    # no content is transferred and the sinks stay empty.
    plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
    print("== Plan (prepare phase, --dry) ==")
    print(format_plan(plans))

    # Real run: prepare, then apply in lockstep across hosts. The first get
    # transfers over scp into each host's sink; the second sees the downloaded
    # content and reports no change.
    plans, report, _hosts = await run(INVENTORY, deploy)
    print("\n== Run log (apply phase) ==")
    assert report is not None
    print(format_log(report))

    # The point of an IO-like destination: the downloaded bytes are right here
    # in a Python variable, with no local file involved.
    print("\n== Downloaded content per host ==")
    for name, sink in SINKS.items():
        print(f"{name}: {sink.getvalue().decode().strip()!r}")


if __name__ == "__main__":
    asyncio.run(main())
