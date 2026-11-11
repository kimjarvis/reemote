# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: run a bespoke shell command on two hosts and inspect the Results.

This is a *usage* example, not part of the library. It demonstrates
``action.Shell`` -- the escape hatch for commands the library knows nothing
about -- driven through ``gather_action``, the action counterpart of
``gather_fact``:

1. Load the target hosts from the shared ``inventory.json`` in the project
   root with ``load_inventory`` (parse + ``validate_inventory``) and
   materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory.
2. ``gather_action(hosts, Shell, command=...)`` fans ``Shell.process`` out
   across the inventory with ``asyncio.gather`` and returns a host-name-keyed
   mapping of ``Result`` objects -- one per host -- in input order. The awaited
   call is a barrier: every host's command has finished before the next line
   runs.
3. Each ``Result`` is a tiny ``TypedDict`` -- ``exit_status``, ``stdout``,
   ``stderr``; success is ``exit_status == 0``.

The fan-out is group-guarded: ``is_centos`` (``host.group == "centos"``)
filters the host list BEFORE ``gather_action``, because an action fan-out has
no ``when`` meta-parameter -- the action counterpart of guarding facts by
filtering before ``gather_fact``. Only the ``centos`` hosts are contacted;
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
from typing import Any

from reemote import (
    Host,
    HostConnectionError,
    InventoryError,
    PrivilegeError,
    Shell,
    gather_action,
    validate_inventory,
)

# The shared inventory in the project root -- the same inventory.json the
# library's other examples read.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    ``json.loads`` parses the file into a list of entry dicts, and
    ``validate_inventory`` checks every entry against the inventory schema --
    a malformed file raises before any connection is attempted.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_centos(host: Host) -> bool:
    """Group guard: the host belongs to the inventory group ``"centos"``.

    Reads only the host's static ``group`` field. Actions have no ``when``
    meta-parameter, so the guard is applied by filtering the host list before
    the ``gather_action`` fan-out.
    """
    return host.group == "centos"


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
        inventory = load_inventory(INVENTORY_PATH)
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file itself is missing or not valid JSON.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The file parsed, but an entry violates the inventory schema.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"== Inventory loaded from {INVENTORY_PATH} ==")
    all_hosts = [Host.from_inventory(entry) for entry in inventory]
    # The group guard: gather_action has no `when`, so filter the host list
    # before the fan-out -- only the centos hosts are ever contacted.
    hosts = [host for host in all_hosts if is_centos(host)]
    skipped = [host.name for host in all_hosts if not is_centos(host)]
    print(f"== Skipped (group guard is_centos): {', '.join(skipped)} ==")
    try:
        # A command that succeeds: the kernel version, captured on stdout.
        # gather_action runs it on the centos hosts concurrently and returns
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
