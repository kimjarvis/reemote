# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: run an elevated bespoke command with a sudo password.

This is a *usage* example, not part of the library. It is the action analogue
of ``examples/demonstrations/privilege/privilege_sudo_pw.py``: same inventory
``privilege`` section supplying the run-as user and password, but driving
``action.Shell`` through ``gather_action`` instead of a two-phase deploy.

The elevation contract is identical to an operation's:

- The call only *asks* to elevate with ``sudo=True``. It never names a user or
  a password -- those are privilege parameters and come from the inventory's
  ``privilege`` section alone (``sudo_user``, ``sudo_password``). Letting a
  caller choose them would be a security risk.
- With a ``sudo_password`` configured, the transport elevates through
  ``sudo -A`` against a small askpass helper it generates on the host, so the
  password is supplied without a TTY (asyncssh channels are non-interactive).
  Without one, elevation assumes passwordless sudo.

The script runs ``id`` twice on each ``centos`` host: once unprivileged
(reporting the login user) and once with ``sudo=True`` (reporting root). Both
come back as
``Result`` objects; the elevated one rides the raw-result privileged path
(``run_shell_privileged_result``), so the command's own exit status is data --
but a *rejected credential* is different. If sudo refuses the password, that is
sudo's OWN authentication failure, not the bespoke command's outcome, and it
raises ``PrivilegeError`` (the privilege counterpart of ``HostConnectionError``)
rather than travelling back as an exit status. ``main`` catches it and prints
one line naming the host, keeping the exit status and stderr as ``__cause__``.

The hosts come from the shared ``inventory.json`` in the project root, read by
``load_inventory`` and validated. ``is_centos`` (``host.group == "centos"``)
filters the host list before the ``gather_action`` fan-out -- an action has no
``when`` meta-parameter -- so only the ``centos`` hosts are contacted. The
``privilege`` section this demo needs is not in the shared file, so ``main``
attaches it to the loaded ``centos`` entries: privilege stays an *inventory*
parameter, never a caller one.

The password is plain text in the inventory -- the only supported form -- and
never reaches the ``Result`` or any log: it lives only in the execution string
sent over the encrypted channel. Each ``gather_action`` call re-connects and
closes the host (removing the askpass helper) when it finishes.

Run it with::

    uv run python examples/actions/action_shell_sudo.py
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from reemote5 import (
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

# The privilege section this demo attaches to the loaded ``centos`` inventory
# entries (the shared file carries none). The login user's ``sudo_password``
# authenticates elevation; ``sudo_user`` is the run-AS target -- ``root`` here,
# because only root may read /etc/shadow. ``root`` is sudo's own default
# target, so the command sent is a bare ``sudo id`` with no ``-u root``. The
# call supplies neither user nor password.
PRIVILEGE = {"sudo_user": "root", "sudo_password": "qsefth*4"}


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

    # The shared inventory carries no privilege section; attach this demo's to
    # the centos entries, so elevation stays an inventory parameter and the
    # credential is scoped to the group that actually elevates.
    for entry in inventory:
        if entry.get("group") == "centos":
            entry["privilege"] = dict(PRIVILEGE)

    print(f"== Inventory loaded from {INVENTORY_PATH} ==")
    all_hosts = [Host.from_inventory(entry) for entry in inventory]
    # The group guard: gather_action has no `when`, so filter the host list
    # before the fan-out -- only the centos hosts are ever contacted.
    hosts = [host for host in all_hosts if is_centos(host)]
    skipped = [host.name for host in all_hosts if not is_centos(host)]
    print(f"== Skipped (group guard is_centos): {', '.join(skipped)} ==")
    try:
        # Unprivileged: id reports the login user (kjarvis).
        plain = await gather_action(hosts, Shell, command="id")

        # Elevated: sudo=True asks for elevation; the inventory's sudo_user
        # and sudo_password supply the target and credential via askpass. id
        # now reports root. The Result carries the command's own outcome.
        elevated = await gather_action(hosts, Shell, command="id", sudo=True)
    except HostConnectionError as exc:
        # The host could not be reached over SSH at all.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except PrivilegeError as exc:
        # The host was reached but rejected the configured sudo credential.
        # One line naming the host -- not sudo's three "Sorry, try again."
        # retries; the exit status and stderr stay on exc.__cause__.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    for label, results in (("id (unprivileged)", plain), ("id (sudo=True)", elevated)):
        print(f"== {label} ==")
        for name, result in results.items():
            print(
                f"{name}: exit={result['exit_status']} ok={result['exit_status'] == 0}"
            )
            print(f"  {result['stdout'].strip()}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
