# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Reload the systemd daemon with ``systemd.daemon_reload``.

This is a *usage* example, not part of the library. It shows the stateless,
non-idempotent half of systemd control over a **real SSH transport**
(asyncssh), using reemote5's two-phase execution model:

1. Read the target hosts from ``inventory.json`` in the project root and
   validate it with ``validate_inventory``.
2. Build a branch-free deploy generator yielding one ``OperationRequest``,
   guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``systemd.daemon_reload`` ports the baseline's operation of the same name. It reads
no fact and always yields a single ``systemctl daemon-reload`` -- the command
that makes systemd re-read unit files after they change on disk. Because it
compares against nothing, it is planned as a change on every run (the baseline
marks it ``is_idempotent=False``); that is exactly why a config-writing
operation such as ``systemd.dropin`` chains a daemon-reload only when it
actually changed a file, rather than calling this unconditionally.

The request is flagged ``sudo=True`` (a system manager's daemon-reload needs
root) and carries ``when=is_systemd`` so the OpenRC ``alpine`` host -- which
has no ``systemctl`` -- is skipped rather than failed. The scope flags
(``user_mode``/``machine``/``user_name``) stay at their system-scope defaults.

Unlike the pytest suite -- which injects ``SimulatedHost`` via ``run``'s
``host_factory`` -- this example uses the default ``Host.from_inventory``
factory and connects for real. The inventory path is resolved from
``__file__`` so it works from any working directory.

Run it with::

    uv run python examples/modules/systemd/systemd_daemon_reload.py
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
    OperationRequest,
    PrivilegeError,
    format_log,
    format_plan,
    op,
    run,
    systemd,
    validate_inventory,
)

# examples/modules/systemd/systemd_daemon_reload.py -> the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it."""
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_systemd(host: Host) -> bool:
    """``when`` guard: the host runs systemd rather than OpenRC.

    Reads only static inventory data (``host.group``) and stays a stable
    module-level function, so both deploy phases reach the same decision.
    """
    return host.group != "alpine"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Reload the systemd daemon -- always planned as a change."""
    yield op(
        systemd.daemon_reload,
        name="Reload systemd daemon",
        sudo=True,
        when=is_systemd,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only; the reload is planned, nothing is sent.
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