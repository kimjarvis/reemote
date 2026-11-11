# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: patch-window deploys -- the same roles, different intent.

This is a *usage* example, not part of the library. It builds on
``dnf_packages.py`` (same two-phase ``run`` flow over the real asyncssh
transport) and shows the sixth composition scenario: one package list
driving two different deploys -- a normal "ensure installed" run and a
patch-window run that upgrades the same packages.

The inventory is read from the project root's ``inventory.json`` (validated
up front with ``validate_inventory``), which holds hosts in the ``"centos"``
group alongside hosts in other groups. Both composed deploys carry
``when=is_centos`` on their ``sub_deploy`` wrapper, so the ``dnf`` steps are
eligible only on the CentOS hosts; the driver skips them -- without reading
the ``DnfPackages`` fact -- on the Alpine and Ubuntu hosts. The guard lives at
the composition site, not inside ``package_role``, so the factory stays a
pure package-list-to-deploy transform.

``package_role`` is a *factory*: it takes a package set (as ``*packages``,
which naturally yields the tuple ``OperationRequest`` kwargs must stay
hashable with) plus the ``latest`` flag, and returns a deploy-shaped
callable. The intent difference lives entirely in ``dnf.packages``'
options: ``latest=False`` only installs missing packages, while
``latest=True`` additionally upgrades every installed, unpinned package
through ``dnf update -y <pkgs>``.

Because a factory produces a fresh callable per intent, patch Tuesday is a
different *composed* deploy over the same package list -- no duplicated
role code, and each composed deploy is a pure function of the host, so
both satisfy the determinism guard.

This example prepares both deploys read-only (``dry=True``) so the two
plans can be compared, then really runs only the patch deploy: on hosts
missing the packages it installs them, and on hosts that have them it
upgrades them.

Run it with::

    uv run python examples/demonstrations/composition/composed_patch_window.py
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
    format_log,
    format_plan,
    op,
    run,
    sub_deploy,
    validate_inventory,
)

WEB_PACKAGES = ("httpd", "mod_ssl")

# examples/demonstrations/composition/composed_patch_window.py -> the project
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


def package_role(
    *packages: str, latest: bool = False
) -> Callable[[Host], Generator[OperationRequest]]:
    """Factory: build a sub-deploy ensuring one fixed package set.

    ``*packages`` is a tuple, satisfying the hashability rule for
    ``OperationRequest`` kwargs; ``latest`` selects the intent -- install
    missing packages only, or also upgrade the installed ones.
    """

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            dnf.packages,
            name=f"Ensure {', '.join(packages)}",
            packages=packages,
            latest=latest,
            sudo=True,
        )

    return deploy


# Two intents over the same package list, each a composed deploy. The
# ``is_centos`` guard sits on the wrapper, so the inner factory request needs
# no guard of its own -- ``when`` composes down to every request it yields.
install_deploy = compose(
    sub_deploy(package_role(*WEB_PACKAGES), name_prefix="web", when=is_centos),
)
patch_deploy = compose(
    sub_deploy(
        package_role(*WEB_PACKAGES, latest=True),
        name_prefix="web-patch",
        when=is_centos,
    ),
)


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry both intents: prepare phase only, facts gathered read-only
        # over SSH, so the plans show what each would do to the real hosts.
        # On the non-CentOS hosts the step is [skipped: when guard is_centos].
        install_plans, _, _hosts = await run(inventory, install_deploy, dry=True)
        print("\n== Install plan (prepare phase, --dry) ==")
        print(format_plan(install_plans))

        patch_plans, _, _hosts = await run(inventory, patch_deploy, dry=True)
        print("\n== Patch plan (prepare phase, --dry) ==")
        print(format_plan(patch_plans))

        # Real run of the patch deploy only: installs where missing, upgrades
        # where present (``dnf update -y httpd mod_ssl``).
        _plans, report, _hosts = await run(inventory, patch_deploy)
        print("\n== Run log (patch deploy, apply phase) ==")
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
