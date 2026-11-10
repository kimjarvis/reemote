# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Define a custom firewalld service with ``firewalld.service``.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with ``validate_inventory``
   so a malformed file fails with an ``InventoryError`` naming the
   offending entry, *before* any host is materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``). Each plain ``Host``
lazily opens an asyncssh connection from its ``connection`` data, gathers
facts and runs commands for real, and ``run`` closes those connections when
it finishes. ``run`` is a coroutine, so ``main`` is async and driven by
``asyncio.run``.

``firewalld.service`` is a reemote5 original (the baseline ships no firewalld
module): it manages the service *definition* -- a named port/protocol
bundle, here ``webapp`` with 8080/tcp and 8443/tcp -- so zones can open
the app by name (``firewalld.zone_service``) instead of scattering raw
port rules. Definitions live in the **permanent** configuration only
(firewalld has no runtime definition store), so this operation has no
``permanent`` argument; the runtime picks a new definition up on the
next reload. The listed members are additive -- the operation ensures
each port/source-port/protocol/module and the display text is present,
and never removes unlisted members from an existing definition. It is
idempotent on the ``FirewalldServices`` fact. The member tuples are
module-level constants: ``op()`` requires hashable arguments, so lists
are rejected by design. The script yields the *same* request twice: on
a host without the definition the prepare phase plans both as changes,
while the apply phase creates it on the first and reports the second as
a no-op through the fresh per-step fact cache.

``firewall-cmd`` writes daemon state, so both requests are flagged
``sudo=True`` and the driver elevates their commands over SSH.

The demo hosts ship without firewalld, so ``main`` first drives a small
idempotent *install deploy* (``dnf.packages`` + ``systemd.service`` under
``sudo=True``) to install firewalld and start/enable the daemon, then runs
the deploy below. firewalld's daemon performs the privileged kernel work,
so ``firewall-cmd`` mutations succeed even when sudo lands in a confined
SELinux domain.

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

    uv run python examples/modules/firewalld/firewalld_service.py
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
    firewalld,
    format_log,
    format_plan,
    op,
    run,
    systemd,
    validate_inventory,
)

# The service definition to ensure. Shared by both requests below so they
# carry identical operation kwargs (only the log ``name`` differs), which
# makes the second a guaranteed no-op in the apply phase. Tuples, not
# lists: op() arguments must be hashable.
SERVICE = "webapp"
PORTS = ("8080/tcp", "8443/tcp")
SHORT = "WebApp"
DESCRIPTION = "Application tier HTTP and HTTPS ports"

# examples/modules/firewalld/firewalld_service.py -> the project root, so the inventory
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


def install(host: Host) -> Generator[OperationRequest]:
    """Install firewalld and start/enable the daemon the deploy needs.

    Branch-free and idempotent: an already-installed package and an
    already-running, enabled service plan and apply as no change.
    """
    yield op(
        dnf.packages,
        name="Install firewalld",
        packages=("firewalld",),
        sudo=True,
        when=is_centos,
    )
    yield op(
        systemd.service,
        name="Start and enable firewalld",
        service="firewalld",
        running=True,
        enabled=True,
        sudo=True,
        when=is_centos,
    )


def deploy(host: Host) -> Generator[OperationRequest]:
    """Define the webapp service -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request creates the definition and its members if missing;
    the second carries the same arguments and is a guaranteed no-op in the
    apply phase, because the first change is already visible through the
    fresh per-step fact cache.

    ``firewall-cmd`` writes daemon state, so both requests are flagged
    ``sudo=True`` and the driver elevates their commands over SSH.
    """
    yield op(
        firewalld.service,
        name="Define webapp service",
        service=SERVICE,
        ports=PORTS,
        short=SHORT,
        description=DESCRIPTION,
        sudo=True,
        when=is_centos,
    )
    yield op(
        firewalld.service,
        name="Re-ensure webapp service (no-op)",
        service=SERVICE,
        ports=PORTS,
        short=SHORT,
        description=DESCRIPTION,
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Prerequisite run: the demo hosts ship without firewalld, and every
        # firewall-cmd command (and the FirewalldZones fact) needs the daemon
        # running. Install firewalld and start/enable it first (idempotent,
        # sudo=True) so the deploy below reads real zones and its mutations
        # reach a live daemon. firewalld's daemon does the privileged kernel
        # work, so these succeed even under a confined sudo domain.
        await run(inventory, install)

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