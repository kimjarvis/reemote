# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: layered environments assembled from static configuration.

This is a *usage* example, not part of the library. It builds on
``dnf_packages.py`` (same two-phase ``run`` flow over the real asyncssh
transport) and shows the second composition scenario: staging and
production deploys differing only in *which* roles are assembled.

The inventory is read from the project root's ``inventory.json`` (validated
up front with ``validate_inventory``), which holds hosts in the ``"centos"``
group alongside hosts in other groups. Each environment's composed deploy is
wrapped with ``sub_deploy(..., when=is_centos)``, so the ``dnf`` steps are
eligible only on the CentOS hosts and the driver skips them -- without
reading the ``DnfPackages`` fact -- on the Alpine and Ubuntu hosts.

The deploy body must never branch on mutable facts -- the prepare and
apply phases would flatten differently and ``run_apply`` would raise
``DeterminismError``. The legitimate place to branch is *assembly time*:
``build_deploy(env)`` consults a static environment table and returns one
composed callable that both phases then use unchanged, so determinism is
structural rather than something to be careful about.

Each role is wrapped with ``sub_deploy(name_prefix=...)`` so the plan
output shows which role produced which request. Prefixing is safe here
because the roles do not share value-identical requests (no dedupe is
requested); see ``composed_baseline_dedupe.py`` for the interaction
between prefixes and deduplication.

Run it with::

    uv run python examples/demonstrations/composition/composed_environments.py
"""

import asyncio
import json
import sys
from collections.abc import Callable, Generator
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
    files,
    format_log,
    format_plan,
    op,
    run,
    sub_deploy,
    validate_inventory,
)

HTTPD_WELCOME_CONF = "/etc/httpd/conf.d/welcome.conf"

# examples/demonstrations/composition/composed_environments.py -> the project
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
        path=HTTPD_WELCOME_CONF,
        present=False,
        sudo=True,
    )


def role_debug_tools(host: Host) -> Generator[OperationRequest]:
    """Staging only: interactive troubleshooting packages."""
    yield op(
        dnf.packages,
        name="Install debug tools",
        packages=("strace", "lsof", "tcpdump"),
        sudo=True,
    )


def role_hardening(host: Host) -> Generator[OperationRequest]:
    """Production only: remove packages a hardened host should not carry."""
    yield op(
        dnf.packages,
        name="Remove telnet",
        packages=("telnet",),
        present=False,
        sudo=True,
    )


# Static table: the environment decides the role list, not any host fact.
ENVIRONMENTS: dict[str, tuple[Callable[[Host], Generator[OperationRequest]], ...]] = {
    "staging": (role_baseline, role_httpd, role_debug_tools),
    "prod": (role_baseline, role_httpd, role_hardening),
}


def build_deploy(env: str) -> Callable[[Host], Generator[OperationRequest]]:
    """Assemble one environment's deploy from its role list.

    The outer ``sub_deploy`` adds the ``is_centos`` eligibility guard to every
    request the environment composes, so the whole deploy is centos-only
    without touching the individual roles.
    """
    return sub_deploy(
        compose(
            *(
                sub_deploy(role, name_prefix=role.__name__.removeprefix("role_"))
                for role in ENVIRONMENTS[env]
            )
        ),
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # The branch happens here, once, before either phase: both the dry run
        # and the real run below use the very same composed callable.
        deploy = build_deploy("prod")

        # --dry: prepare phase only, facts gathered read-only over SSH.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan for prod (prepare phase, --dry) ==")
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
