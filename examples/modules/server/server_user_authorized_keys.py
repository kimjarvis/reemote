# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: authorise SSH public keys in a user's ``authorized_keys`` file.

This is a *usage* example, not part of the library. It shows reemote5's
``server.user_authorized_keys`` operation, the pyinfra
``server.user_authorized_keys`` parity for SSH keys: the deploy makes sure a
user's ``~/.ssh`` directory (mode ``700``) and ``authorized_keys`` file
(mode ``600``) exist and are owned correctly, then appends the public keys
that are not already listed -- pyinfra's documented flow, minus its
controller-side key-file paths (every entry here is a literal key string).

The operation is idempotent and reads four facts. The ``Home`` fact resolves
the directory when no ``authorized_key_directory`` is passed, a private
``_KeysPathStat`` probe compares the directory and the file against the
shared ``files.Directory``/``files.File`` state, the ``AuthorizedKeys``
fact (read with the explicit key-file path) lists the keys already present,
and the ``AuthorizedKeysFile`` raw-content fact reads the file's current
text so the missing keys can be appended controller-side -- only the
missing ones ride a write. Two details are worth seeing in the
plan:

* creating a key file from nothing yields the whole setup -- ``mkdir -p`` +
  ``chmod 700`` + ``chown`` for ``~/.ssh``, then ``touch`` + ``chmod 600`` +
  ``chown`` for ``authorized_keys`` -- before the write, while a
  run where everything already matches yields nothing at all;
* the write is a spool-and-install pair: the transformed text rides an
  unprivileged scp to a ``/tmp`` spool named after the key file, then an
  ``sh -c 'mkdir -p ... && cp ... && chmod 600 ... && rm -f ...'`` install
  overlays the key file and removes the spool. The scp half stays
  unprivileged even under ``sudo=True`` -- transfers never elevate -- so a
  request for another user's keys shows two privilege levels in one log.

``public_keys`` is a tuple: every ``op()`` argument must stay hashable for
``compose(dedupe=True)``, so a list would be rejected with
``UnhashableArgumentError``. Here the keys are managed for the login user
themselves, so no elevation is needed -- writing into ``~/.ssh`` under your
own home is unprivileged. Managing keys for *another* user is the mirror
case: flag the request ``sudo=True``, since the directory and file sit in
that user's home. pyinfra's ``delete_keys`` option is supported as well --
a whole-file rewrite to exactly the requested keys, riding the same
spool-and-install pair -- though this example only appends.

Unlike the pytest suite -- which injects the test framework's
``SimulatedHost`` via ``run``'s ``host_factory`` and never touches the
network -- this example uses the *default* factory (``Host.from_inventory``):
each plain ``Host`` lazily opens an asyncssh connection, reads the home
directory, the ``.ssh``/``authorized_keys`` stat, the existing keys and the
key file's text for real, and runs the setup and spool-and-install commands
where they differ.

Every failure mode of the run -- an unreachable host, a rejected elevation,
an ``OperationError`` for a path that exists as the wrong type (an
``authorized_keys`` that is a directory, say), or a command that exits
non-zero -- reaches ``main`` as a typed error whose message already names the
host, so ``main`` catches them and prints one line each instead of a
traceback.

Note that this script mutates the target user's ``~/.ssh/authorized_keys``
for real: the appended keys stay in place. Re-running it is a no-op, because
the ``AuthorizedKeys`` fact already reports them.

Every request carries ``when=is_centos``, the declarative eligibility
guard: the predicate reads only **static** inventory data --
``host.group``, set by the ``"group"`` key of the inventory entry -- and
is a stable module-level function, so the prepare and apply phases reach
the same skip decision. The driver skips a rejected request without
materializing it, so on hosts outside the ``"centos"`` group neither the
commands nor the facts behind them run, and plan and log render the step
as ``[skipped: when guard is_centos]`` in its lockstep slot. The path is
resolved from ``__file__``, so the example works from any working
directory; point ``INVENTORY_PATH`` at your own file to target your
machines.

The path is resolved from ``__file__``, so the example works from any
working directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/server/server_user_authorized_keys.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    OperationError,
    OperationRequest,
    PrivilegeError,
    format_log,
    format_plan,
    op,
    run,
    server,
    validate_inventory,
)

# examples/modules/server/server_user_authorized_keys.py -> the project root, so the inventory
# is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The public keys to authorise, as literal key strings (never file paths --
# pyinfra's controller-side ``os.path.exists`` branch is deliberately
# dropped). A tuple, because ``op()`` arguments must stay hashable.
DESKTOP_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDemoDesktopKeyMaterial00 desktop@home"
LAPTOP_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDemoLaptopKeyMaterial0000 laptop@cafe"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries
    each carrying at least ``host`` and ``connection``. Returns the parsed
    list, ready to materialize into hosts; raises ``InventoryError`` (via
    ``validate_inventory``) when the document is not an array or an entry
    does not match the schema.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_centos(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"centos"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision.
    """
    return host.group == "centos"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Authorise the demo keys in the login user's authorized_keys file.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases; whether the ``.ssh`` directory, the key file and each key
    already exist is decided inside the operation against the freshly
    gathered facts, per phase.
    """
    # First request: create ``~/.ssh`` (mode 700) and ``authorized_keys``
    # (mode 600) if they are missing, then append both keys. For the login
    # user's own home no elevation is needed; managing another user's keys
    # would carry ``sudo=True`` instead.
    yield op(
        server.user_authorized_keys,
        name="Authorise the desktop and laptop keys",
        user="kjarvis",
        public_keys=(DESKTOP_KEY, LAPTOP_KEY),
        when=is_centos,
    )
    # Second, identical request demonstrates the per-phase fact cache: in the
    # *plan* both requests read the same pre-deployment state, so both show a
    # change; in the *apply* each step re-reads the facts, so this step sees
    # the keys the first step wrote and yields nothing -- a no-op.
    yield op(
        server.user_authorized_keys,
        name="Re-authorise the same keys (idempotence check)",
        user="kjarvis",
        public_keys=(DESKTOP_KEY, LAPTOP_KEY),
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The home directory, the ``.ssh``/
        # ``authorized_keys`` stat and the existing keys are still gathered
        # over SSH (read-only) so the plan reflects each host's real
        # pre-deployment state; nothing is created and no key is appended.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. Each step
        # re-reads the facts, so a second run of this script plans no change
        # anywhere.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON. Python's
        # own message already names the file and the reason, so print just that
        # -- no host was contacted and there is no traceback worth showing.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema. The
        # message names the offending entry by its list index, which is the
        # whole report -- and it fails before any host is materialized.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except HostConnectionError as exc:
        # A host could not be reached over SSH at all. Report it plainly --
        # naming every unreachable host -- instead of dumping an asyncssh
        # traceback, and exit non-zero so the failure is script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except OperationError as exc:
        # The ``.ssh`` path or the key file already exists as the wrong type
        # (an ``authorized_keys`` that is a directory, say). The prepare phase
        # aborted before anything was sent; the message names host and path.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except PrivilegeError as exc:
        # The host was reached but rejected the elevation. One line naming the
        # host; the detail stays on ``__cause__``. (Not requested here -- the
        # login user's own keys are unprivileged -- but kept for the
        # another-user ``sudo=True`` variant.)
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except CommandError as exc:
        # A setup or install command exited non-zero. The typed message already
        # names the host, the credential-free command and its stderr, so print
        # just that.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
