"""Example: the same deploy as ``dnf_packages.py``, with the inventory in a JSON file.

This is a *usage* example, not part of the library. ``dnf_packages.py``
hard-codes its ``INVENTORY`` in the source; real applications usually keep
the target hosts in a data file instead, so the deployment script and the
machine list change independently. This example loads the inventory from
``inventory.json`` in the project root:

1. Read the file and parse it as JSON -- an inventory is just a list of
   dictionaries, which is exactly what JSON expresses, so no translation
   layer is needed between the file and the library.
2. Validate it up front with ``validate_inventory`` so a malformed file
   fails with an ``InventoryError`` naming the offending entry, *before*
   any host is materialized or connected. (``run`` validates too; doing it
   here simply moves the failure to the load step and keeps the error next
   to the file that caused it.)
3. Hand the plain list of dictionaries to ``run``, exactly as the
   hard-coded example does: the default ``host_factory``
   (``Host.from_inventory``) materializes each entry into a ``Host`` that
   lazily opens an asyncssh connection from its ``connection`` data.

The deploy itself is unchanged from ``dnf_packages.py`` -- install httpd,
then remove the welcome configuration the package drops -- and still runs
end to end over a **real SSH transport** in both phases.

The path is resolved from ``__file__``, so the example works from any
working directory. Point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/demonstrations/inventory/json_inventory_alpine.py
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
    dnf,
    files,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

HTTPD_WELCOME_CONF = "/etc/httpd/conf.d/welcome.conf"

# examples/demonstrations/inventory/json_inventory.py -> the project root,
# so the inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory_alpine.json"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries
    each carrying at least ``host`` and ``connection``. Returns the parsed
    list, ready to pass to ``run``; raises ``InventoryError`` (via
    ``validate_inventory``) when the document is not an array or an entry
    does not match the schema.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def deploy(host: Host) -> Generator[OperationRequest]:
    """Install httpd, then remove its default welcome configuration.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    Both operations need root, so both are flagged ``sudo=True`` and the
    driver elevates their commands over SSH.
    """
    yield op(dnf.packages, name="Install httpd", packages=("httpd",), sudo=True)
    yield op(
        files.file,
        name="Remove welcome configuration",
        src=HTTPD_WELCOME_CONF,
        present=False,
        sudo=True,
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
        print("\n== Plan (prepare phase, --dry) ==")
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
    except HostConnectionError as exc:
        # One or more hosts could not be reached over SSH. Report it plainly --
        # naming every unreachable host -- instead of dumping an asyncssh
        # traceback, and exit non-zero so the failure is script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except PrivilegeError as exc:
        # A host was reached but rejected the ``sudo=True`` elevation. One line
        # naming the host; the exit status and stderr stay on ``__cause__``.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except CommandError as exc:
        # The elevation was accepted but the command it ran exited non-zero
        # (dnf could not install the package, for instance). The typed message
        # already names the host, the command and its stderr, so print just
        # that one report -- no traceback.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
