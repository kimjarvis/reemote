# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Relabel a tree from the fcontext database with ``selinux.restorecon``.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key). The hosts must run SELinux (RHEL-family);
   point ``INVENTORY`` at your own machines to target them.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``). Each plain ``Host``
lazily opens an asyncssh connection from its ``connection`` data, gathers
facts and runs commands for real, and ``run`` closes those connections when
it finishes.

``run`` is a coroutine (both phases are async), so ``main`` is async and
driven by ``asyncio.run``.

``selinux.restorecon`` is a reemote5 original (pyinfra ships no restorecon
operation): it reapplies the persistent fcontext database to existing
files -- the documented remedy for ``selinux.file_context``'s limitation
that ``chcon`` labels are runtime-only and a relabel reverts them, and for
``selinux.file_context_mapping``'s limitation that a database rule never
touches labels files already carry. Here ``/var/www/html`` is relabelled
recursively (``restorecon -R``, the default ``recurse=True``);
``force=True`` would add ``-F``, resetting even the user-customizable
user/role/level components, not just the type.

The operation is deliberately **stateless**: reemote5 has no fact that
compares a whole tree's labels against the database (that would mean
walking every file), so every request yields its ``restorecon`` and
reports a change -- in the prepare plan *and* in the apply log, on the
first run and on every re-run. The script yields the request twice to
make that contract visible: unlike the fact-driven operations, the second
request is *not* a no-op. restorecon itself is harmless to re-run (it only
writes labels that differ), so the always-changed report is honest about
what the tool cannot know in advance. Pair it *after* a mapping step when
the relabel must reflect a rule the same deploy just wrote (see
``examples/modules/selinux/selinux_op_file_context_mapping.py``).

``restorecon`` writes root-owned labels, so both requests are flagged
``sudo=True`` and the driver elevates their commands over SSH.

Run it with::

    uv run python examples/modules/selinux/selinux_restorecon.py
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
    op,
    run,
    selinux,
)

# The tree to relabel: httpd's document root, recursively.
RELABEL_PATH = "/var/www/html"

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
    """Relabel /var/www/html -- twice, to show the stateless contract.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    Both requests yield their restorecon and report a change: the operation
    has no fact to compare against, so -- unlike the fact-driven operations
    -- the second request is *not* a no-op. Re-running restorecon is
    harmless (it only writes labels that differ).

    ``restorecon`` writes root-owned labels, so both requests are flagged
    ``sudo=True`` and the driver elevates their command over SSH.
    """
    yield op(
        selinux.restorecon,
        name="Relabel /var/www/html from the database",
        path=RELABEL_PATH,
        recurse=True,
        sudo=True,
    )
    yield op(
        selinux.restorecon,
        name="Relabel /var/www/html again (still changed)",
        path=RELABEL_PATH,
        recurse=True,
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