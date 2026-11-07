# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: independently written roles sharing a baseline, deduplicated.

This is a *usage* example, not part of the library. It builds on
``dnf_packages.py`` (same two-phase ``run`` flow over the real asyncssh
transport) and shows the first composition scenario: roles written in
isolation that each declare the same prerequisite.

The inventory is read from the project root's ``inventory.json`` (validated
up front with ``validate_inventory``), which holds one host in the
``"centos"`` group alongside hosts in other groups. The whole composed deploy
is wrapped with ``sub_deploy(..., when=is_centos)``, so the ``dnf`` steps are
eligible only on the CentOS hosts: the driver skips them on the Alpine and
Ubuntu hosts without materializing them, and neither the ``dnf`` commands nor
the ``DnfPackages`` fact behind them runs there. Because ``when`` composes by
conjunction and the wrapper prepends the *same* predicate tuple to every
request, the three baseline copies still share an effective identity and
dedupe collapses them exactly as before.

``role_httpd`` and ``role_monitoring`` both re-declare ``role_baseline``'s
request -- each under its *own* name ("Install baseline tools", "Install
httpd prerequisites", "Install monitoring prerequisites"), the way
independently written roles naturally would. ``compose(..., dedupe=True)``
still collapses them, by *effective identity* -- ``func``, args, kwargs and
``sudo``, but **not** ``name`` -- to their first occurrence, which keeps
its name. Names are excluded from the key because they never reach the
host: they only label plan and log entries. So despite the three differing
labels (and the differing ``sub_deploy`` name prefixes on top), the plan
holds a single "baseline: Install baseline tools" entry -- first name wins.
Without dedupe the duplicates would remain as separate plan entries
(harmless: ``dnf.packages`` re-reads the ``DnfPackages`` fact at each
lockstep step and reports no change).

The determinism guard is unaffected: it still compares *full* request
values (name included), since both phases flatten the same composed
deploy. Dedupe itself is deterministic too -- the ``seen`` set is created
per invocation, so the prepare and apply phases collapse identically.

Run it with::

    uv run python examples/demonstrations/composition/composed_baseline_dedupe.py
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

# examples/demonstrations/composition/composed_baseline_dedupe.py -> the
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
    """The prerequisite every role below assumes, stated once."""
    yield op(
        dnf.packages,
        name="Install baseline tools",
        packages=("ca-certificates", "curl", "vim-minimal"),
        sudo=True,
    )


def role_httpd(host: Host) -> Generator[OperationRequest]:
    """Web-server role: re-declares the baseline under its own name."""
    yield op(
        dnf.packages,
        name="Install httpd prerequisites",
        packages=("ca-certificates", "curl", "vim-minimal"),
        sudo=True,
    )
    yield op(dnf.packages, name="Install httpd", packages=("httpd",), sudo=True)
    yield op(
        files.file,
        name="Remove welcome configuration",
        src=HTTPD_WELCOME_CONF,
        present=False,
        sudo=True,
    )


def role_monitoring(host: Host) -> Generator[OperationRequest]:
    """Monitoring role: the same packages again, yet another name."""
    yield op(
        dnf.packages,
        name="Install monitoring prerequisites",
        packages=("ca-certificates", "curl", "vim-minimal"),
        sudo=True,
    )
    yield op(
        dnf.packages,
        name="Install troubleshooting tools",
        packages=("tcpdump", "wget"),
        sudo=True,
    )


# Prefixed per role for readable plans; the shared baseline request still
# collapses even though all three copies carry different names, because
# the dedupe key ignores names (first name wins). The outer ``sub_deploy``
# adds the ``is_centos`` eligibility guard to every request at once -- the
# same predicate tuple on each copy, so it does not disturb the dedupe.
deploy = sub_deploy(
    compose(
        sub_deploy(role_baseline, name_prefix="baseline"),
        sub_deploy(role_httpd, name_prefix="httpd"),
        sub_deploy(role_monitoring, name_prefix="monitoring"),
        dedupe=True,
    ),
    when=is_centos,
)


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The deduplicated plan shows one baseline
        # entry despite three roles yielding it; no mutating command is sent.
        # On the non-CentOS hosts every step is [skipped: when guard is_centos].
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
