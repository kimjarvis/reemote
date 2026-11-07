# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install a ``.rpm`` file already on the host, with version idempotence.

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
it finishes. The path is resolved from ``__file__``, so the example works
from any working directory; point ``INVENTORY_PATH`` at your own file to
target your machines.

``run`` is a coroutine (both phases are async), so ``main`` is async and
driven by ``asyncio.run``.

``dnf.rpm`` installs (or with ``present=False`` removes) a ``.rpm`` file that
already exists on the *target host* -- here the EPEL release package, the kind
of standalone ``.rpm`` one commonly copies to a machine and installs by hand.
The ``src`` is therefore a **remote** path, not a controller-side one: upload
the file first with ``files.put`` (or place it there by any other means).
Unlike pyinfra -- whose ``dnf.rpm`` can also *download* a remote ``.rpm``
mid-deploy -- reemote5 has no download operation, so a ``src`` with a URL
scheme raises ``ValueError``.

``dnf.rpm`` reads the file's ``name``/``version`` through the ``RpmPackage``
fact, then reads the installed package of that name, and counts the package as
present only when the installed version matches the file's. It installs with
``rpm -i`` only when it does not, so re-running is a no-op. The script yields
the *same* install request twice to show that idempotence across the two
phases: the prepare phase plans both as changes (nothing is installed yet, so
both compare against the same pre-deployment state), while the apply phase
installs on the first and reports the second as a no-op, because the fresh
per-step fact cache now sees the package the first step installed.

Installing a package writes to the root-owned rpm database, so the request is
flagged ``sudo=True`` and the driver elevates its ``rpm -i`` command. Because
reemote5 re-materializes the operation at apply time against a fresh fact
cache, the command is a plain ``rpm -i`` -- not pyinfra's defensive
``rpm -q \\`rpm -qp <src>\\` ... || rpm -i <src>`` runtime check -- which also
keeps elevation correct (a ``sudo``-prefixed ``|| ...`` chain would leave its
fallback arm unprivileged).

Every request also carries ``when=is_centos``, the declarative eligibility
guard (see ``examples/demonstrations/composition/composed_when_guard.py``).
The predicate reads only **static** inventory data -- ``host.group``, set by
the ``"group"`` key of the inventory entry -- and is a stable module-level
function, so the prepare and apply phases reach the same skip decision. The
driver skips a rejected request without materializing it, so on the Alpine
and Ubuntu hosts neither the commands nor the ``RpmPackage`` fact reads
behind them run, and plan and log render the step as
``[skipped: when guard is_centos]`` in its lockstep slot. The shared
``inventory.json`` holds two hosts in the ``"centos"`` group alongside hosts
in other groups, so the run visibly installs on the CentOS hosts and skips
every other one.

Run it with::

    uv run python examples/modules/dnf/dnf_rpm.py
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
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# The ``.rpm`` file to install, as a path on the *target host* (copy it there
# with ``files.put`` first). Shared by both requests below so they carry
# identical operation kwargs (only the log ``name`` differs).
EPEL_RPM = "/tmp/epel-release-9-8.el9.noarch.rpm"

# examples/modules/dnf/dnf_rpm.py -> the project root, so the inventory is
# found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries
    each carrying at least ``host`` and ``connection``. Returns the parsed
    list, ready to hand to ``run``, which materializes it with the default
    ``Host.from_inventory`` factory; raises ``InventoryError`` (via
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
    so both deploy phases reach the same eligibility decision. ``rpm`` and
    ``dnf`` only exist on the CentOS hosts, so the guard keeps the installs
    (and the facts they read) off the Alpine and Ubuntu hosts.
    """
    return host.group == "centos"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Install the EPEL ``.rpm`` -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError),
    and identical on every host: eligibility is expressed with the
    ``when=is_centos`` guard each request carries, not with an ``if``.
    The first request installs the file's package; the second carries the
    same arguments and is a guaranteed no-op in the apply phase, because the
    first install is already visible through the fresh per-step fact cache.

    Installing a package writes to the root-owned rpm database, so both
    requests are flagged ``sudo=True`` and the driver elevates their command
    over SSH.
    """
    yield op(dnf.rpm, name="Install EPEL rpm", src=EPEL_RPM, sudo=True, when=is_centos)
    yield op(
        dnf.rpm,
        name="Re-install EPEL rpm (no-op)",
        src=EPEL_RPM,
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
        # that mutates a host is sent. A request the ``when`` guard rejects is
        # planned as [skipped: when guard is_centos] without materializing it,
        # so its fact is not read on that host either.
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
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
