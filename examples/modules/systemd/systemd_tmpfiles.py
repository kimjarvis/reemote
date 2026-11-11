# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Declare runtime files and directories with ``systemd.tmpfiles``.

This is a *usage* example, not part of the library. It shows how an
application drives reemote's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Read the target hosts from ``inventory.json`` in the project root and
   validate it up front with ``validate_inventory``, so a malformed file
   fails with an ``InventoryError`` before any host is connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``), opening real asyncssh
connections that ``run`` closes when it finishes. The path is resolved from
``__file__``, so the example works from any working directory.

``systemd.tmpfiles`` is a reemote original. A ``tmpfiles.d`` fragment under
``/etc/tmpfiles.d/<name>.conf`` declares files, directories and their
ownership/permissions that systemd-tmpfiles creates at boot (and on demand).
The operation writes the fragment only when its content differs from what is
already there (idempotent through the in-namespace content fact), then applies
it with ``systemd-tmpfiles --create <path>`` so the declared runtime paths exist
immediately rather than at the next boot. ``present=False`` removes the config
file only -- already-created runtime files are not cleaned up, which is a
separate ``systemd-tmpfiles --remove`` concern.

The write follows the ``dnf.repo`` spool pattern: an **unprivileged** scp
transfer spools the text into a dest-derived ``/tmp`` directory, then an **elevated**
``sh -c`` install copies it into ``/etc/tmpfiles.d``, ``chmod 0644`` it and
removes the spool. Only the install and the apply are escalated -- the transfer
never is -- so the request is flagged ``sudo=True``.

The parameter is ``filename`` (not ``name``): ``name`` is a reserved ``op``
meta-parameter consumed by the driver and never forwarded to the operation.
``.conf`` is appended when missing. This operation is **system-scope only**
(``/etc/tmpfiles.d`` is the system manager's path).

The script yields the *same* request twice to show idempotence across the two
phases: the prepare phase plans both as changes when the config is absent
(nothing is sent in prepare), while the apply phase writes and applies on the
first and reports the second as a no-op, because the fresh per-step fact cache
now sees the content the first step installed.

**This operation writes root-owned configuration and creates runtime paths.**
It is compile-checked and exercised by the offline example harness rather than
run against shared hosts; point ``INVENTORY_PATH`` at your own throwaway
machines to execute it for real. Every request carries ``when=is_systemd``,
reading only static ``host.group`` so the OpenRC ``alpine`` host is skipped
rather than failing on a missing ``systemd-tmpfiles``.

Run it with::

    uv run python examples/modules/systemd/systemd_tmpfiles.py
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
    systemd,
    validate_inventory,
)

# examples/modules/systemd/systemd_tmpfiles.py -> the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The config name; the file becomes /etc/tmpfiles.d/myapp.conf.
FILENAME = "myapp"

# A tmpfiles.d row declaring a runtime directory. An immutable module-level
# constant, so the same bytes are compared and written in both phases (a
# mutable container would trip the determinism guard and the example harness).
TMPFILES_CONTENT = "d /run/myapp 0755 root root -\n"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it."""
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_systemd(host: Host) -> bool:
    """``when`` guard: the host runs systemd rather than OpenRC.

    Reads only static inventory data (``host.group``), never a mutable fact,
    and stays a stable module-level function so both deploy phases reach the
    same eligibility decision and the determinism guard holds.
    """
    return host.group != "alpine"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Declare the myapp runtime directory -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases; the ``when=is_systemd`` guard expresses eligibility, not an ``if``.
    """
    yield op(
        systemd.tmpfiles,
        name="Declare myapp runtime directory",
        filename=FILENAME,
        content=TMPFILES_CONTENT,
        sudo=True,
        when=is_systemd,
    )
    yield op(
        systemd.tmpfiles,
        name="Re-declare myapp runtime directory (no-op)",
        filename=FILENAME,
        content=TMPFILES_CONTENT,
        sudo=True,
        when=is_systemd,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Facts are gathered over SSH (read-only) so
        # the plan reflects each host's real state; no mutating command is sent.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())