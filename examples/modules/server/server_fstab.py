# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: persist a swap entry in /etc/fstab with server.fstab.

This is a *usage* example, not part of the library. It shows reemote's
``server.fstab`` operation, the Ansible ``mount`` module's fstab half: the
deploy ensures the entry keyed by ``/swapfile`` reads exactly
``/swapfile none swap sw 0 0`` -- geerlingguy.swap's central declaration,
Ansible's ``name: none`` swap form -- appending it to ``/etc/fstab`` when
the file has no such entry and rewriting it in place when a column
drifts. It corrects the old docs claim that fstab persistence was a
``files.edit`` recipe: a transfer never elevates, so the root-owned
``/etc/fstab`` was never writable that way.

The operation is idempotent and reads two facts. The parsed ``Fstab``
fact maps each mount point to its entry -- device-keyed for a ``none``
(swap) line, the Ansible identification rule -- and when the entry
already matches every requested column the request is a no-op with the
house reasons ("entry already present in", "already absent from"); an
equivalent line in another spacing (tabs) reads as no change, not a
duplicate. When a change is due, the ``AuthorizedKeysFile`` raw-content
fact supplies the file's current text and a pure controller-side
transform rewrites just the matching line (appending when absent,
dropping it on removal), preserving every other line, comment and blank
line verbatim; unrequested columns merge from the current entry, like
Ansible rewriting only the differing fields.

The write is a spool-and-install pair: the transformed text rides an
unprivileged scp to a ``/tmp`` spool whose name is derived from the
managed path, then an ``sh -c 'mkdir -p ... && cp ... && chmod 0644 ... && rm -rf ...'``
install overlays the fstab file and removes the spool directory. The scp
half stays unprivileged even under ``sudo=True`` -- transfers never
elevate -- so the log shows two privilege levels in one request: a
``$ scp <fstab file> -> ...`` line followed by a ``$ sudo sh -c '...'``
line. ``/etc/fstab`` is root-owned, so both requests here carry
``sudo=True``.

``options`` is a tuple (a comma-separated string works too): every
``op()`` argument must stay hashable for ``compose(dedupe=True)``, so a
list would be rejected with ``UnhashableArgumentError``. This is the
persistent half only: activating the swap file (``swapon``) is issue #47,
and the live table stays ``server.mount``'s. The entry itself is inert
while the file is missing, so running this on a host without a
``/swapfile`` just manages the line.

Unlike the pytest suite -- which injects the test framework's
``SimulatedHost`` via ``run``'s ``host_factory`` and never touches the
network -- this example uses the *default* factory (``Host.from_inventory``):
each plain ``Host`` lazily opens an asyncssh connection, reads the real
``/etc/fstab`` for the fact comparison and the content transform, and
runs the spool-and-install pair only where the entry differs.

Every failure mode of the run -- an unreachable host, a rejected
elevation, a ``ValueError`` for a missing required column, or a command
that exits non-zero -- reaches ``main`` as a typed error whose message
already names the host (or the argument), so ``main`` catches them and
prints one line each instead of a traceback.

Note that this script mutates the target hosts' ``/etc/fstab`` for real:
the entry stays in place. Re-running it is a no-op, because the
``Fstab`` fact already reports the requested entry.

Every request carries ``when=is_centos``, the declarative eligibility
guard: the predicate reads only **static** inventory data --
``host.group``, set by the ``"group"`` key of the inventory entry -- and
is a stable module-level function, so the prepare and apply phases reach
the same skip decision. The driver skips a rejected request without
materializing it, so on hosts outside the ``"centos"`` group neither the
commands nor the facts behind them run, and plan and log render the step
as ``[skipped: when guard is_centos]`` in its lockstep slot.

The path is resolved from ``__file__``, so the example works from any
working directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/server/server_fstab.py
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
    op,
    run,
    server,
    validate_inventory,
)

# examples/modules/server/server_fstab.py -> the project root, so the
# inventory is found whatever the current working directory is. No
# ``privilege`` entry is needed: the elevated install assumes passwordless
# sudo for the login user; a password would come from ``privilege`` in the
# inventory, never the op.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The entry to ensure, exactly geerlingguy.swap's fstab declaration
# (Ansible mount: name none, src, fstype swap, opts sw, state present).
# options is a tuple, because ``op()`` arguments must stay hashable.
SWAP_DEVICE = "/swapfile"
SWAP_KWARGS = {
    "device": SWAP_DEVICE,
    "mountpoint": "none",
    "fs_type": "swap",
    "options": ("sw",),
    "dump": 0,
    "passno": 0,
}


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
    """Ensure the swap entry in /etc/fstab, elevated.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases; whether the entry already matches is decided inside the
    operation against the freshly gathered facts, per phase.
    """
    # First request: append or rewrite the entry keyed by SWAP_DEVICE (the
    # ``none`` mountpoint means the device keys it, Ansible's name: none
    # rule). /etc/fstab is root-owned, so sudo=True -- the scp spool half
    # stays unprivileged regardless, only the install shell elevates.
    yield op(
        server.fstab,
        name="Persist the swap file in /etc/fstab",
        sudo=True,
        when=is_centos,
        **SWAP_KWARGS,
    )
    # Second, identical request demonstrates the per-phase fact cache: in the
    # *plan* both requests read the same pre-deployment state, so both show a
    # change; in the *apply* each step re-reads the facts, so this step sees
    # the entry the first step wrote and yields nothing -- a no-op.
    yield op(
        server.fstab,
        name="Ensure the same entry again (idempotence check)",
        sudo=True,
        when=is_centos,
        **SWAP_KWARGS,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The fstab file is still gathered over
        # SSH (read-only cat) so the plan reflects each host's real
        # pre-deployment state; nothing is spooled and nothing is installed.
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
    except ValueError as exc:
        # Parameter validation: a missing device/mountpoint/fs_type column
        # for the requested state. Raised in the prepare phase before
        # anything is sent.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except PrivilegeError as exc:
        # The host was reached but rejected the elevation the install
        # needs. One line naming the host; the detail stays on __cause__.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except CommandError as exc:
        # The install command exited non-zero. The typed message already
        # names the host, the credential-free command and its stderr, so print
        # just that.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
