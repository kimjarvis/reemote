# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Manage /etc/selinux/config with the ``selinux.config`` operation.

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

``selinux.config`` is a reemote original (the baseline ships no config
operation): it ensures the boot-time settings ``SELINUX=enforcing`` and
``SELINUXTYPE=targeted`` in ``/etc/selinux/config``. It supersedes the
``files.edit`` + ``files.move`` two-run recipe this example used to
demonstrate -- the whole spool-and-install dance is now inside the
operation, so one request in one deploy replaces the snapshot gather, the
controller-side gate and both runs. Internally the operation reads the
file's current text with the ``SelinuxConfigFile`` content fact, drops
every existing assignment of each requested key (commented ones included)
and appends ``KEY=value`` unquoted, the stock convention; a file that
already holds exactly the wanted assignments is a no-op, so re-running
the script changes nothing. When the content differs, the request sends
*two* commands: an unprivileged ``scp`` spooling the new text to a
deterministic ``/tmp`` path (reemote's transfers never elevate, which is
*why* the root-owned file cannot be written directly), then an elevated
``sh -c`` that copies it over ``/etc/selinux/config``, chmods it ``0644``
and removes the spool. The request's ``sudo=True`` applies to the install
only. A ``None`` setting is left untouched: pass just ``SELINUX`` or just
``SELINUXTYPE`` to manage one key alone.

The settings are read when the system boots, so the edit takes effect at
the *next reboot* -- the runtime half is ``selinux.mode``'s job
(``setenforce``, see ``examples/modules/selinux/selinux_op_mode.py``), and
the ``Mode`` fact's ``config_mode`` field is how a deploy reads the
persistent setting back. **Warning**: switching a *disabled* subsystem to
enforcing additionally requires a full relabel -- request
``selinux.autorelabel`` (``examples/modules/selinux/selinux_autorelabel.py``)
before the reboot, or the boot will relabel nothing and services will fail
on unlabelled files. Changing ``SELINUXTYPE`` requires the matching policy
package to be installed.

Every request carries ``when=is_centos``, the declarative eligibility
guard: the predicate reads only **static** inventory data --
``host.group``, set by the ``"group"`` key of the inventory entry -- and
is a stable module-level function, so the prepare and apply phases reach
the same skip decision. The driver skips a rejected request without
materializing it, so on hosts outside the ``"centos"`` group neither the
commands nor the facts behind them run, and plan and log render the step
as ``[skipped: when guard is_centos]`` in its lockstep slot. The path is
resolved from ``__file__``, so the example works from any working
directory; point ``INVENTORY_PATH`` at your own file of SELinux hosts
(RHEL-family) to target your machines.

Run it with::

    uv run python examples/modules/selinux/selinux_config.py
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
    selinux,
    validate_inventory,
)

# examples/modules/selinux/selinux_config.py -> the project root, so the
# inventory is found whatever the current working directory is.
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
    """Ensure the boot-time mode and policy type in one request.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The operation is idempotent on the file's own text: a host already
    setting both values plans a no-op, and a changed host spools and
    installs exactly once.

    Writing /etc/selinux/config needs root, so the request is flagged
    ``sudo=True`` -- the escalation covers the install command; the scp
    spool transfer always rides the connection as the login user.
    """
    yield op(
        selinux.config,
        name="Ensure SELINUX=enforcing, SELINUXTYPE=targeted at boot",
        SELINUX="enforcing",
        SELINUXTYPE="targeted",
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