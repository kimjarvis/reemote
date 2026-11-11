# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Generate an unprotected GPG key on the target host, idempotently.

This is a *usage* example, not part of the library. It shows how an
application drives reemote's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with ``validate_inventory``
   so a malformed file fails with an ``InventoryError`` naming the
   offending entry, *before* any host is materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``gpg.gen_key`` generates a key for ``"{real_name} <{email}>"`` in the
*executing user's* keychain from a gpg batch parameter block. One
``GpgGenKeyCommand`` renders a single ``sh -c`` that printf-pipes the
``Key-Type``/``Key-Length``/``Name-Real``/``Name-Email``/``Expire-Date``/
``%no-protection`` lines into ``gpg --batch --gen-key`` -- every field is
a hashable string, so no transfer is needed. The parameter is
``real_name``, not ``name``, because ``name`` is an ``op()``
meta-parameter (the request label). ``algo``, ``length`` and ``expire``
default to rsa/4096/never; passphrase-protected generation is
deliberately out of scope (``%no-protection`` is fixed -- secrets never
ride in plans or logs). The request is flagged ``sudo=True`` here to
generate into root's keychain.

The operation is idempotent against the ``GpgKeys`` fact: when a key with
that exact uid already exists the generation is skipped and no command
is sent.

**This example is compile-checked by the test suite, not executed
against the shared demo hosts**: key generation is slow (rsa4096 needs
entropy) and adds a permanent key to root's keychain on every host.
Adjust the identity before running it against your own hosts, and clean
up with ``gpg.delete_key`` afterwards.

The deploy yields the same generation request twice: in the prepare
phase (where nothing is sent) both plan as changes, but in the apply
phase the second request's fresh per-step fact read sees the uid the
first generation created and reports it already exists.

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

Run it with::

    uv run python examples/modules/gpg/gpg_gen_key.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    format_log,
    format_plan,
    gpg,
    op,
    run,
    validate_inventory,
)

# The identity of the generated key: its uid reads "Reemote Demo
# <demo@example.com>", and that exact uid is the idempotence probe.
REAL_NAME = "Reemote Demo"
EMAIL = "demo@example.com"

# examples/modules/gpg/gpg_gen_key.py -> the project root, so the inventory
# is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


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
    """Generate the demo key in root's keychain, twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    The second request demonstrates idempotence: its apply-phase fact
    read sees the uid the first generation created and reports no change.
    """
    yield op(
        gpg.gen_key,
        name="Generate demo GPG key",
        real_name=REAL_NAME,
        email=EMAIL,
        sudo=True,
        when=is_centos,
    )
    yield op(
        gpg.gen_key,
        name="Generate demo GPG key (again)",
        real_name=REAL_NAME,
        email=EMAIL,
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
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
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())