# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: run a bespoke shell command on two hosts and inspect the Results.

This is a *usage* example, not part of the library. It demonstrates
``action.Shell`` -- the escape hatch for commands the library knows nothing
about -- driven through ``gather_action``, the action counterpart of
``gather_fact``:

1. Load the target hosts from ``inventory.json`` in the current working directory
   (validated with ``validate_inventory``) and materialize each entry into a plain
   ``Host`` with the default ``Host.from_inventory`` factory.
2. ``gather_action(hosts, Shell, command=...)`` fans ``Shell.process`` out
   across the inventory with ``asyncio.gather`` and returns a host-name-keyed
   mapping of ``Result`` objects -- one per host -- in input order. The awaited
   call is a barrier: every host's command has finished before the next line
   runs.
3. Each ``Result`` is a tiny ``TypedDict`` -- ``exit_status``, ``stdout``,
   ``stderr``; success is ``exit_status == 0``.

The fan-out is family-guarded: ``is_redhat`` (``host.family == "RedHat"``)
filters the host list BEFORE ``gather_action``, because an action fan-out has
no ``when`` meta-parameter -- the action counterpart of guarding facts by
filtering before ``gather_fact``. Only the RedHat hosts are contacted;
the ``alpine`` and ``ubuntu`` entries are reported as skipped.

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
import json
import sys
from pathlib import Path

from reemote import (
    Host,
    HostConnectionError,
    InventoryError,
    PrivilegeError,
    Shell,
    gather_action,
    validate_inventory,
)


# The shared inventory in the current working directory -- the same inventory.json the
# library's other examples read.
def is_redhat(host: Host) -> bool:
    """Family guard: the host belongs to the ``"RedHat"`` OS family.

    Reads only the host's static ``family`` field. Actions have no ``when``
    meta-parameter, so the guard is applied by filtering the host list before
    the ``gather_action`` fan-out.
    """
    return host.family == "RedHat"


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
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file itself is missing or not valid JSON.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The file parsed, but an entry violates the inventory schema.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print("== Inventory loaded from inventory.json ==")
    all_hosts = [Host.from_inventory(entry) for entry in inventory]
    # The family guard: gather_action has no `when`, so filter the host list
    # before the fan-out -- only the RedHat hosts are ever contacted.
    hosts = [host for host in all_hosts if is_redhat(host)]
    skipped = [host.name for host in all_hosts if not is_redhat(host)]
    print(f"== Skipped (family guard is_redhat): {', '.join(skipped)} ==")
    try:
        # A command that succeeds: the kernel version, captured on stdout.
        # gather_action runs it on the RedHat hosts concurrently and returns
        # a {host.name: Result} mapping; the await is the barrier ensuring
        # both have finished before we move on.
        uname = await gather_action(hosts, Shell, command="uname -r")

        # A command that FAILS: cat of an absent file exits 1. An action does
        # not raise -- the non-zero exit and the captured stderr travel back in
        # the Result, and the script decides what they mean. This is the whole
        # point of the escape hatch: bespoke software whose failure modes the
        # library cannot anticipate.
        missing = await gather_action(
            hosts, Shell, command="cat /etc/reemote-does-not-exist"
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
