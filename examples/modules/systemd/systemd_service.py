# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Manage a systemd unit's runtime and boot state with ``systemd.service``.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Read the target hosts from ``inventory.json`` in the project root and
   validate it up front with ``validate_inventory``, so a malformed file
   fails with an ``InventoryError`` naming the offending entry before any
   host is materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``). Each plain ``Host``
lazily opens an asyncssh connection from its ``connection`` data, gathers
facts and runs commands for real, and ``run`` closes those connections when
it finishes. The path is resolved from ``__file__``, so the example works from
any working directory; point ``INVENTORY_PATH`` at your own file to target
your machines.

``systemd.service`` is the canonical home of the operation formerly known as
``server.service`` (which remains a deprecated alias). It ports the baseline's
``systemd.service`` and manages one unit -- here the Apache web server, which
RHEL-family hosts ship as ``httpd`` and Debian-family hosts ship as ``apache2``
-- along two independent axes. ``running=True`` compares the
``SystemdServiceState`` fact
(a unit counts as running only when its ``ActiveState`` is ``active``) and
yields ``systemctl start`` only when it differs; ``enabled=True`` compares the
``SystemdServiceEnabled`` fact and yields ``systemctl enable`` only when the
unit is not already enabled on boot. The first request sets both, converging
runtime *and* boot state in one operation.

The script yields the *same* start-and-enable request twice to show
idempotence across the two phases: the prepare phase plans both as changes
when httpd is not yet running (nothing is sent in prepare, so both compare
against the same pre-deployment state), while the apply phase starts on the
first and reports the second as a no-op, because the fresh per-step fact cache
now sees the state the first step produced.

A third request demonstrates the reemote5 extensions over the baseline's option
set: ``reloaded=True`` with ``soft_reload=True`` issues
``systemctl reload-or-try-restart`` (re-reading configuration without a full
restart, falling back to a restart when the unit has no reload handler),
gated on the unit actually running. ``reset_failed=True`` would first clear a
``failed`` state with ``reset-failed``, and ``now=True`` would add ``--now``
to the enable/disable so the boot change also starts/stops the unit
immediately; both are off here so the reload is the only extra command.

``systemctl`` writes root-owned state, so the requests are flagged
``sudo=True`` and the driver elevates their commands. The scope flags
(``user_mode``/``machine``/``user_name``) are left at their system-scope
defaults; a per-user manager would set ``user_mode=True`` and usually need no
escalation at all.

Every request also carries ``when=is_systemd``, the declarative eligibility
guard. Two rules make it deterministic: the predicate reads only **static**
inventory data -- ``host.group`` -- never a mutable fact, and it is a stable
module-level function, so the prepare and apply phases reach the same skip
decision. The project root's ``inventory.json`` holds an ``alpine`` host
(which uses OpenRC, not systemd) alongside systemd hosts, so the run visibly
manages the Apache unit on the systemd hosts and skips the Alpine one.

The unit name itself is chosen per OS family by ``web_server_unit``, which
reads only static ``host.group`` (``apache2`` for the Debian-family ``ubuntu``
group, ``httpd`` otherwise). Like the ``when`` guard, that branch is stable
across both phases, so each host's request sequence stays deterministic even
though the unit name differs between hosts -- the same shape the
``composed_per_host_roles`` demonstration uses for per-host variation.

Run it with::

    uv run python examples/modules/systemd/systemd_service.py
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

# examples/modules/systemd/systemd_service.py -> the project root, so the
# inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries
    each carrying at least ``host`` and ``connection``. Returns the parsed
    list, ready to hand to ``run``; raises ``InventoryError`` (via
    ``validate_inventory``) when the document is not an array or an entry
    does not match the schema.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_systemd(host: Host) -> bool:
    """``when`` guard: the host runs systemd rather than OpenRC.

    Reads only static inventory data (``host.group``), never a mutable fact,
    and stays a stable module-level function -- the two rules every ``when``
    predicate follows so both deploy phases reach the same eligibility
    decision and the determinism guard holds. The ``alpine`` group uses
    OpenRC, where ``systemctl`` is absent, so the driver skips it instead of
    failing on a missing binary.
    """
    return host.group != "alpine"


def web_server_unit(host: Host) -> str:
    """Return the Apache web server's unit name for the host's OS family.

    RHEL-family hosts (the ``centos`` group) ship ``httpd``; Debian-family
    hosts (the ``ubuntu`` group) ship the same server under ``apache2``. A
    bare name gets ``.service`` appended by the operation; any other systemd
    suffix (``logrotate.timer``) would be kept as is. The branch reads only
    static inventory data (``host.group``), never a mutable fact, so the
    choice is identical in the prepare and apply phases and the determinism
    guard holds even though the unit name differs per host.
    """
    return "apache2" if host.group == "ubuntu" else "httpd"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Start and enable the Apache unit -- twice, to show idempotence -- then soft-reload it.

    The only branch reads static ``host.group`` (via ``web_server_unit``), so
    the request sequence is identical in the prepare and apply phases (a
    branch on a mutable fact would raise DeterminismError). Eligibility is
    expressed with the ``when=is_systemd`` guard each request carries, not
    with an ``if``.
    """
    service = web_server_unit(host)
    yield op(
        systemd.service,
        name="Ensure httpd running and enabled",
        service=service,
        running=True,
        enabled=True,
        sudo=True,
        when=is_systemd,
    )
    yield op(
        systemd.service,
        name="Re-ensure httpd (no-op)",
        service=service,
        running=True,
        enabled=True,
        sudo=True,
        when=is_systemd,
    )
    yield op(
        systemd.service,
        name="Soft-reload httpd configuration",
        service=service,
        reloaded=True,
        soft_reload=True,
        sudo=True,
        when=is_systemd,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent. A request the ``when`` guard rejects is
        # planned as [skipped: when guard is_systemd] without materializing it.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library reports every host-level failure as a typed error whose
        # message names the host and the failing command. Print that message
        # -- no traceback -- and exit non-zero so the failure stays visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())