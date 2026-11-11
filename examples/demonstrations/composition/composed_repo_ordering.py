# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: ordering guarantees across roles -- repositories before packages.

This is a *usage* example, not part of the library. It builds on
``dnf_packages.py`` (same two-phase ``run`` flow over the real asyncssh
transport) and shows the fourth composition scenario: ``dnf install`` of an
EPEL-provided package fails unless ``epel-release`` is installed first, and
``compose`` makes that ordering explicit and reusable.

The inventory is read from the project root's ``inventory.json`` (validated
up front with ``validate_inventory``), which holds hosts in the ``"centos"``
group alongside hosts in other groups. The composed deploy is wrapped with
``sub_deploy(..., when=is_centos)``, so both roles are eligible only on the
CentOS hosts; the driver skips them -- without reading the ``DnfPackages``
fact -- on the Alpine and Ubuntu hosts, where EPEL and ``dnf`` do not apply.

Composition order *is* execution order, and the driver executes it in
cross-host lockstep: every host runs the Nth request before any host pulls
request N+1. So composing the repository role first guarantees that both
web1 and web2 have ``epel-release`` installed before *either* host starts
installing ``htop`` from it -- an ordering property that would otherwise
have to be hand-maintained inside one flat generator.

The roles are wrapped with ``sub_deploy(name_prefix=...)`` so the plan
shows which layer each request came from. No dedupe is requested: the two
roles share no requests, and prefixing would rename any that they did
(``name`` participates in request equality).

Run it with::

    uv run python examples/demonstrations/composition/composed_repo_ordering.py
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
    compose,
    dnf,
    format_log,
    format_plan,
    op,
    run,
    sub_deploy,
    validate_inventory,
)

# examples/demonstrations/composition/composed_repo_ordering.py -> the project
# root, so the inventory is found whatever the current working directory is.
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
    so both deploy phases reach the same eligibility decision. EPEL and the
    ``dnf`` operations below only make sense on a CentOS host, so the guard
    keeps them (and the facts they read) off the Alpine and Ubuntu hosts.
    """
    return host.group == "centos"


def role_epel_repo(host: Host) -> Generator[OperationRequest]:
    """The prerequisite layer: enable EPEL so the next role can resolve."""
    yield op(
        dnf.packages,
        name="Install epel-release",
        packages=("epel-release",),
        sudo=True,
    )


def role_monitoring(host: Host) -> Generator[OperationRequest]:
    """Packages that only resolve once EPEL is enabled on every host."""
    yield op(
        dnf.packages,
        name="Install monitoring tools",
        packages=("htop", "iotop"),
        sudo=True,
    )


# Argument order is the ordering statement: repos strictly before packages.
# The outer ``sub_deploy`` adds the ``is_centos`` eligibility guard to both
# roles at once, so the ordering only ever plays out on the CentOS hosts.
deploy = sub_deploy(
    compose(
        sub_deploy(role_epel_repo, name_prefix="repos"),
        sub_deploy(role_monitoring, name_prefix="monitoring"),
    ),
    when=is_centos,
)


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The plan lists the repo request first on
        # each host; no mutating command is sent. On the non-CentOS hosts every
        # step is [skipped: when guard is_centos].
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: the lockstep barrier between steps is what guarantees both
        # hosts have epel-release before either installs htop/iotop.
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
