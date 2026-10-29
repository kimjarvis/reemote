"""Example: a health-check style deploy combining dnf, files.put and files.get.

This is a *usage* example, not part of the library. It is the reemote5 take on
a relatable pyinfra scenario -- a simple web-server health-check / log-rotation
deploy -- in one flat generator that crosses module boundaries:

1. ``dnf.packages`` installs a lightweight, harmless tool (``htop``) so the
   operator can inspect the host; installing a system package needs root, so
   the request is flagged ``sudo=True``.
2. ``files.put`` pushes a message-of-the-day to ``/tmp/remote_motd``,
   simulating a configuration or static-asset deployment. The transfer is
   unprivileged (scp rides the connection as the login user), and ``/tmp`` is
   writable by that user, so no escalation applies -- ``sudo``/``su`` are
   deliberately *not* options on a transfer.
3. ``files.get`` pulls ``/etc/os-release`` -- present and world-readable on
   every Fedora/RHEL host, so a safe stand-in for "fetch the logs" -- back
   into an in-memory stream the script prints afterwards.

Where the pyinfra original works with local *paths* (``src="files/motd"``,
``dest="local_os_release.txt"``, ``add_deploy_dir=True``), reemote5's transfer
operations take **IO-like objects only**: the put source is a module-level
``io.StringIO`` (no local ``files/`` directory needed) and the get destination
is a per-host ``io.BytesIO`` sink. ``files.put`` also has no ``mode`` option;
ownership/permissions would be a follow-up elevated shell operation if needed.
Both streams obey the usual rules: created once at module level so both phases
pass the identical object (``OperationRequest`` compares the unhashable stream
by identity), and -- because a get *writes* its destination -- one sink per
host so the two hosts never clobber each other in lockstep.

The deploy is branch-free, so the request sequence is identical in the prepare
and apply phases; the put and get idempotence checks are content-based (sha1),
so a second run only transfers what actually differs.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``): each plain ``Host``
lazily opens an asyncssh connection, gathers facts, runs the elevated dnf
command, and transfers content over scp for real.

``main`` also owns the error reporting, as every real-host example here does:
the library is CLI-free and *raises*, so ``main`` catches its typed errors
(``HostConnectionError``, ``PrivilegeError``, ``CommandError``) and prints the
message -- which already names the host and the failing command -- on stderr
before exiting non-zero. Installing ``htop`` against a host without
EPEL enabled therefore reports ``command failed on web1 (exit 1): sudo dnf
install -y htop`` plus dnf's own ``Error: Unable to find a match: htop``, never
a Python traceback.

Run it with::

    uv run python examples/general/dnf_put_get.py
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
    dnf,
    files,
    format_log,
    format_plan,
    op,
    run,
)

# The put source, created once on the control machine: a stream, never a local
# path. The same object is passed to put in every phase, so the prepare and
# apply request sequences compare equal (OperationRequest compares it by
# identity), and it is rewound to position 0 before every read.
MOTD_SRC = io.StringIO("Managed by reemote5 -- report problems to the ops team.\n")

# The remote destination for the motd: under /tmp, so the unprivileged scp
# transfer can write it as the login user.
REMOTE_MOTD = "/tmp/remote_motd"

# The remote source to pull back: standard and world-readable on every
# Fedora/RHEL host, standing in for a log or diagnostic file.
REMOTE_OS_RELEASE = "/etc/os-release"

# One get sink per host, created once and reused across both phases: stable
# identity for the determinism guard, and per-host isolation because a get
# writes into its destination (two hosts sharing one stream would clobber
# each other in lockstep).
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
    """Install htop, push the motd, and pull the remote OS release info.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    Only the dnf install is elevated; the two transfers ride the connection's
    scp channel as the login user. The per-host sink is fetched, not created
    fresh per call, so both phases pass the identical stream object.
    """
    yield op(dnf.packages, name="Install htop via dnf", packages=("htop",), sudo=True)
    yield op(
        files.put, name="Upload local motd to remote", src=MOTD_SRC, dest=REMOTE_MOTD
    )
    sink = SINKS.get(host.name)
    if sink is None:
        sink = SINKS[host.name] = io.BytesIO()
    yield op(
        files.get,
        name="Download remote OS release info",
        src=REMOTE_OS_RELEASE,
        dest=sink,
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent and no content is transferred.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH. The
        # dnf install is elevated with sudo, the put uploads over scp when the
        # remote motd differs, and the get downloads /etc/os-release into each
        # host's sink.
        plans, report, _hosts = await run(INVENTORY, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))

        # The point of an IO-like destination: the downloaded bytes are right
        # here in a Python variable -- print each host's OS release info straight
        # from the stream, with no local file involved.
        print("\n== Remote OS release info per host ==")
        for name, sink in SINKS.items():
            print(f"--- {name} ---")
            print(sink.getvalue().decode().strip())
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
