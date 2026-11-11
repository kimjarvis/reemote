# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install a systemd unit drop-in override with ``systemd.dropin``.

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

``systemd.dropin`` is a reemote original. A drop-in is a fragment under
``/etc/systemd/system/<unit>.d/<name>.conf`` that overrides selected settings of
a vendor unit without editing the packaged file. The operation writes the
fragment only when its content differs from what is already there (idempotent
through the in-namespace content fact), then chains ``systemctl daemon-reload``
so systemd re-reads the unit; ``restart=True`` would additionally restart the
unit so the running process picks up the override. ``present=False`` removes the
fragment (and reloads) instead.

The write follows the ``dnf.repo`` spool pattern: an **unprivileged** scp
transfer spools the text into a dest-derived ``/tmp`` directory, then an **elevated**
``sh -c`` install creates the ``.d`` directory, copies the file into place,
``chmod 0644`` it and removes the spool. Only the install is escalated -- the
transfer itself never is -- so the request is flagged ``sudo=True`` while the
file lands root-owned with predictable permissions.

The parameter is ``filename`` (not ``name``): ``name`` is a reserved ``op``
meta-parameter consumed by the driver and never forwarded to the operation, so
a file operation exposes its file name under a distinct keyword. ``.conf`` is
appended when missing. This operation is **system-scope only**
(``/etc/systemd/system`` is the system manager's path); a per-user drop-in is a
``files.edit`` recipe instead.

The script yields the *same* request twice to show idempotence across the two
phases: the prepare phase plans both as changes when the override is absent
(nothing is sent in prepare), while the apply phase writes on the first and
reports the second as a no-op, because the fresh per-step fact cache now sees
the content the first step installed.

**This operation writes root-owned configuration and reloads systemd.** It is
compile-checked and exercised by the offline example harness rather than run
against shared hosts; point ``INVENTORY_PATH`` at your own throwaway machines to
execute it for real. Every request carries ``when=is_systemd``, reading only
static ``host.group`` so the OpenRC ``alpine`` host is skipped rather than
failing on a missing ``systemctl``.

Run it with::

    uv run python examples/modules/systemd/systemd_dropin.py
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

# examples/modules/systemd/systemd_dropin.py -> the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The unit the drop-in extends. A bare name gets ``.service`` appended.
UNIT = "httpd"

# The drop-in name; the file becomes
# /etc/systemd/system/httpd.service.d/override.conf (``.conf`` appended).
FILENAME = "override"

# The override fragment. An immutable module-level constant, so the same bytes
# are compared and written in both phases (a mutable container would trip the
# determinism guard and the offline example harness).
DROPIN_CONTENT = "[Service]\nEnvironment=FOO=bar\n"


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
    """Install the httpd drop-in -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases; the ``when=is_systemd`` guard expresses eligibility, not an ``if``.
    """
    yield op(
        systemd.dropin,
        name="Install httpd drop-in",
        unit=UNIT,
        filename=FILENAME,
        content=DROPIN_CONTENT,
        sudo=True,
        when=is_systemd,
    )
    yield op(
        systemd.dropin,
        name="Re-install httpd drop-in (no-op)",
        unit=UNIT,
        filename=FILENAME,
        content=DROPIN_CONTENT,
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