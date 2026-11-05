# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: run a bespoke shell command on two hosts and inspect the Results.

This is a *usage* example, not part of the library. It demonstrates
``action.Shell`` -- the escape hatch for commands the library knows nothing
about -- driven through ``gather_action``, the action counterpart of
``gather_fact``:

1. Specify the target hosts as an *inventory* and materialize each entry into
   a plain ``Host`` with the default ``Host.from_inventory`` factory.
2. ``gather_action(hosts, Shell, command=...)`` fans ``Shell.process`` out
   across the inventory with ``asyncio.gather`` and returns a host-name-keyed
   mapping of ``Result`` objects -- one per host -- in input order. The awaited
   call is a barrier: every host's command has finished before the next line
   runs.
3. Each ``Result`` is a tiny ``TypedDict`` -- ``exit_status``, ``stdout``,
   ``stderr``; success is ``exit_status == 0``.

The key difference from a fact or an operation: an action interprets NOTHING.
A non-zero exit is *data in the Result*, not an exception. The second command
below (``cat`` of an absent file) exits 1 on every host, and the script simply
reads that status and the captured stderr -- no ``CommandError`` is raised,
because the caller, not the library, decides what a bespoke command's failure
means. (Exit 127, command-not-found, is likewise data here, unlike a fact read
which raises ``CommandNotFoundError``.)

Library-level failures still raise and are caught in ``main``:
``HostConnectionError`` for an unreachable host, ``PrivilegeError`` for a
rejected elevation (see ``action_shell_sudo.py``). Each ``gather_action`` call
re-connects and closes the hosts when it finishes, so no manual ``aclose`` is
needed here.

Run it with::

    uv run python examples/actions/action_shell.py
"""

import asyncio
import sys

from reemote5 import (
    Host,
    HostConnectionError,
    PrivilegeError,
    Shell,
    gather_action,
)

# The inventory: real hosts and their SSH connection information, supplied by
# the application to the library. Host.from_inventory materializes each entry
# into a plain Host that connects over asyncssh.
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


def report(label: str, results: dict) -> None:
    """Print each host's Result for one action command, in inventory order."""
    print(f"== {label} ==")
    for name, result in results.items():
        print(f"{name}: exit={result['exit_status']} ok={result['exit_status'] == 0}")
        if result["stdout"]:
            print(f"  stdout: {result['stdout'].strip()}")
        if result["stderr"]:
            print(f"  stderr: {result['stderr'].strip()}")


async def main() -> None:
    hosts = [Host.from_inventory(entry) for entry in INVENTORY]
    try:
        # A command that succeeds: the kernel version, captured on stdout.
        # gather_action runs it on both hosts concurrently and returns a
        # {host.name: Result} mapping; the await is the barrier ensuring both
        # have finished before we move on.
        uname = await gather_action(hosts, Shell, command="uname -r")

        # A command that FAILS: cat of an absent file exits 1. An action does
        # not raise -- the non-zero exit and the captured stderr travel back in
        # the Result, and the script decides what they mean. This is the whole
        # point of the escape hatch: bespoke software whose failure modes the
        # library cannot anticipate.
        missing = await gather_action(
            hosts, Shell, command="cat /etc/reemote5-does-not-exist"
        )
    except (HostConnectionError, PrivilegeError) as exc:
        # A library-level failure (unreachable host, rejected elevation) is a
        # typed error naming the host. Print it -- no traceback -- and exit
        # non-zero. A bespoke command's own non-zero exit is NOT this path.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    report("uname -r (succeeds)", uname)
    print()
    report("cat absent file (exits 1, reported as data)", missing)


if __name__ == "__main__":
    asyncio.run(main())
