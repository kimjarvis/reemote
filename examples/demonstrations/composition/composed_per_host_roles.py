# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: per-host variation keyed on static inventory data.

This is a *usage* example, not part of the library. It builds on
``dnf_packages.py`` (same two-phase ``run`` flow over the real asyncssh
transport) and shows the fifth composition scenario: one deploy whose
request sequence differs per host -- extra canary tooling on web1 only --
without endangering the determinism guard.

The inventory is read from the project root's ``inventory.json`` (validated
up front with ``validate_inventory``), which holds hosts in the ``"centos"``
group alongside hosts in other groups. The whole deploy is wrapped with
``sub_deploy(..., when=is_centos)``, so the ``dnf`` steps are eligible only on
the CentOS hosts and the driver skips them -- without reading the
``DnfPackages`` fact -- on the Alpine and Ubuntu hosts. The per-host variation
below is a *second*, independent axis: within the eligible CentOS hosts the
canary branch still keys on ``host.name``.

The rule: never branch on *mutable* facts inside a deploy body (the
prepare and apply phases would flatten differently and ``run_apply`` would
raise ``DeterminismError``), but branching on **static** inputs is safe.
``host.name`` comes from the inventory entry and reads identically in both
phases, so ``if host.name == "web1"`` produces the same sequence per host
every time the driver re-invokes ``deploy(host)``.

The per-host roles are delegated with plain ``yield from`` -- the simplest
composition, needing no helper because nothing is renamed or elevated at
the composition site. The canary role is still written as a standalone
sub-deploy so it stays independently testable and reusable.

Run it with::

    uv run python examples/demonstrations/composition/composed_per_host_roles.py
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
    sub_deploy,
    validate_inventory,
)

HTTPD_WELCOME_CONF = "/etc/httpd/conf.d/welcome.conf"

# The canary host is inventory data, not a gathered fact: web1 receives the
# extra tooling role, web2 does not.
CANARY_HOST = "web1"

# examples/demonstrations/composition/composed_per_host_roles.py -> the
# project root, so the inventory is found whatever the current working
# directory is.
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
    so both deploy phases reach the same eligibility decision. The ``dnf``
    operations below only make sense on a CentOS host, so the guard keeps
    them (and the facts they read) off the Alpine and Ubuntu hosts.
    """
    return host.group == "centos"


def role_baseline(host: Host) -> Generator[OperationRequest]:
    yield op(
        dnf.packages,
        name="Install baseline tools",
        packages=("ca-certificates", "curl"),
        sudo=True,
    )


def role_httpd(host: Host) -> Generator[OperationRequest]:
    yield op(dnf.packages, name="Install httpd", packages=("httpd",), sudo=True)
    yield op(
        files.file,
        name="Remove welcome configuration",
        src=HTTPD_WELCOME_CONF,
        present=False,
        sudo=True,
    )


def role_canary_extras(host: Host) -> Generator[OperationRequest]:
    """Extra live-debugging tooling for the canary host only."""
    yield op(
        dnf.packages,
        name="Install canary tooling",
        packages=("strace", "tcpdump"),
        sudo=True,
    )


def deploy(host: Host) -> Generator[OperationRequest]:
    """Compose per host: shared roles, plus canary extras on web1.

    The branch reads ``host.name`` -- static inventory data, identical in
    the prepare and apply phases -- so each host's request sequence is
    stable across phases and lockstep pairing works even though the hosts'
    sequences differ in length (a finished host simply idles at the
    barrier while the other runs its extra step).
    """
    yield from role_baseline(host)
    yield from role_httpd(host)
    if host.name == CANARY_HOST:
        yield from role_canary_extras(host)


# The site policy: the whole deploy is centos-only. Wrapping the generator
# adds the same ``is_centos`` predicate to every request it yields, so the
# Alpine and Ubuntu hosts skip all steps while web1/web2 still differ by the
# canary name branch above -- eligibility and per-host variation compose.
deploy = sub_deploy(deploy, when=is_centos)


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. web1's plan holds one more entry than
        # web2's; no mutating command is sent. On the non-CentOS hosts every
        # step is [skipped: when guard is_centos].
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
