# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: per-host roles selected by ``host.name``, ``host.group`` and ``host.tags``.

This is a *usage* example, not part of the library. It builds on
``composed_per_host_roles.py`` -- one deploy whose request sequence differs
per host -- and shows the same idea driven by the inventory's static
classifiers: a single ``deploy`` generator that branches on ``host.name``,
``host.group`` and ``host.tags`` to select which roles each machine gets.

The inventory is read from the project root's ``inventory.json`` (validated
up front with ``validate_inventory``), the same file the other composition
examples use. Its four hosts carry the classifiers the deploy branches on:
``web1`` and ``web2`` are in the ``"centos"`` group, ``ubuntu`` is in the
``"ubuntu"`` group, and ``alpine`` is in the ``"alpine"`` group and carries the
``apk_package_manager`` tag. Each branch selects the package manager that
matches its group -- ``dnf`` for the CentOS web role, ``apt`` for the Ubuntu
database role, ``apk`` for the tag-selected Alpine tooling -- so the example
also crosses module boundaries.

The rule that makes this safe is the one ``composed_per_host_roles.py``
states: never branch on a *mutable* fact inside a deploy body (the prepare and
apply phases would flatten differently and ``run_apply`` would raise
``DeterminismError``), but branching on **static** inventory data is fine.
``host.name``, ``host.group`` and ``host.tags`` all come from the inventory
entry and read identically in both phases, so ``deploy(host)`` produces the
same per-host sequence every time the driver re-invokes it -- even though the
sequences differ in length across hosts (a finished host simply idles at the
lockstep barrier while the others run their extra steps). The ``is_centos``
predicate used for the web branch is the same stable, static-data guard the
other examples pass as ``when``; here it drives an ``if`` because the point of
this example is per-host *sequences*, not a shared schedule with skips.

The roles are delegated with plain ``yield from`` -- the simplest composition,
needing no helper because nothing is renamed or elevated at the composition
site -- and each stays a standalone sub-deploy so it is independently reusable.

Unlike the other demonstrations, this one runs **entirely in memory** and opens
no SSH connection: it injects the test framework's ``SimulatedHost`` through
``run``'s ``host_factory`` argument, so the plan and the run log reflect a
simulated pre-deployment state rather than a real machine. That keeps the
example runnable anywhere, with nothing to point at your own hosts. ``SimulatedHost``
is test-framework scaffolding (it lives in ``tests/``, never in the library), so
the project root is added to ``sys.path`` to import it.

Run it with::

    uv run python examples/demonstrations/composition/composed_group_tag_roles.py
"""

import asyncio
import copy
import json
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

from reemote5 import (
    Host,
    InventoryError,
    OperationRequest,
    apk,
    apt,
    dnf,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# examples/demonstrations/composition/composed_group_tag_roles.py -> the
# project root, so ``tests`` is importable and the inventory is found whatever
# the working directory is (running a script by path puts its own folder, not
# the root, on sys.path).
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.simulated_host import SimulatedHost

INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries
    each carrying at least ``host`` and ``connection``. Returns the parsed
    list, ready to hand to ``run`` (here materialized by the simulated
    factory); raises ``InventoryError`` (via ``validate_inventory``) when the
    document is not an array or an entry does not match the schema.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_centos(host: Host) -> bool:
    """Static predicate: the host belongs to the inventory group ``"centos"``.

    Reads only static inventory data (``host.group``), never a mutable fact,
    and stays a stable module-level function. The other composition examples
    pass it as a ``when`` guard; here it drives the web-role ``if`` branch,
    which is safe for the same reason -- it reads identically in both phases.
    """
    return host.group == "centos"


# Simulated pre-deployment state per host name (test-framework data, NOT
# inventory): web2 already has nginx, so the web role's install is a no-op
# there while web1's is a real change -- which is what makes the plan show
# both ``changed`` and ``no change`` for the same role.
INITIAL_STATE: dict[str, dict[str, object]] = {
    "web2": {"packages": {"nginx": [""]}},
}


def simulated_factory(entry: dict) -> SimulatedHost:
    """``host_factory`` building SimulatedHosts with the seeded initial state.

    Injected into ``run`` in place of the default ``Host.from_inventory``, so
    the whole example executes against the in-memory stand-in and never opens
    an SSH connection. ``SimulatedHost.from_inventory`` maps the same
    ``group``/``tags`` keys a real ``Host`` does; the seeding only adds the
    simulated package/link state the facts read.
    """
    host = SimulatedHost.from_inventory(entry)
    # Deep-copy: send_command mutates the host's collections in place, which
    # must not leak back into the shared INITIAL_STATE fixture.
    for attr, value in INITIAL_STATE.get(host.name, {}).items():
        setattr(host, attr, copy.deepcopy(value))
    return host


def role_web(host: Host) -> Generator[OperationRequest]:
    """The web-server role, applied to every host in the ``centos`` group."""
    yield op(dnf.packages, name="Install nginx", packages=("nginx",), sudo=True)


def role_apk_tools(host: Host) -> Generator[OperationRequest]:
    """Extra tooling, applied to hosts tagged ``apk_package_manager`` (Alpine)."""
    yield op(
        apk.packages,
        name="Install apk tooling",
        packages=("strace", "tcpdump"),
        sudo=True,
    )


def role_database(host: Host) -> Generator[OperationRequest]:
    """The database role, applied to every host in the ``ubuntu`` group."""
    yield op(
        apt.packages,
        name="Install postgresql",
        packages=("postgresql",),
        sudo=True,
    )


def role_backup(host: Host) -> Generator[OperationRequest]:
    """Backup tooling for one named host, selected by ``host.name`` (web1)."""
    yield op(dnf.packages, name="Install backup agent", packages=("rsync",), sudo=True)


def deploy(host: Host) -> Generator[OperationRequest]:
    """Compose per host from the inventory's static classifiers.

    Three independent branch kinds, all reading data that is identical in the
    prepare and apply phases: ``host.group`` selects the centos web role (via
    ``is_centos``) and the ubuntu database role, ``host.tags`` opts a host into
    the apk tooling role, and ``host.name`` pins the backup role to web1.
    Because the branches are static, each host's request sequence is stable
    across phases and lockstep pairing holds even though the sequences differ
    in length.
    """
    if is_centos(host):
        yield from role_web(host)
    if host.group == "ubuntu":
        yield from role_database(host)
    if "apk_package_manager" in host.tags:
        yield from role_apk_tools(host)
    if host.name == "web1":
        yield from role_backup(host)


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON. Python's
        # own message already names the file and the reason; no host is
        # contacted (the run is in memory), so there is no traceback to show.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema. The
        # message names the offending entry by its list index.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"== Inventory loaded from {INVENTORY_PATH} ==")
    print(", ".join(entry["host"] for entry in inventory))

    # --dry: prepare phase only. web1 plans two operations (web + backup),
    # web2 one (web, already installed -> no change), ubuntu one (database),
    # alpine one (apk tooling). Nothing is executed.
    plans, _, _hosts = await run(
        inventory, deploy, host_factory=simulated_factory, dry=True
    )
    print("\n== Plan (prepare phase, --dry) ==")
    print(format_plan(plans))

    # Apply phase: the SimulatedHost seam mutates in-memory state in lockstep.
    _plans, report, hosts = await run(inventory, deploy, host_factory=simulated_factory)
    print("\n== Run log (apply phase) ==")
    assert report is not None
    print(format_log(report))

    print("\n== Final simulated state ==")
    for host in hosts:
        assert isinstance(host, SimulatedHost)
        # Each group's packages live in its own manager's seam: dnf in
        # ``packages``, apt in ``apt_packages``, apk in ``apk_packages``.
        if is_centos(host):
            installed = sorted(host.packages)
        elif host.group == "ubuntu":
            installed = sorted(host.apt_packages)
        else:
            installed = sorted(host.apk_packages)
        print(
            f"{host.name}: group={host.group!r} tags={host.tags} packages={installed}"
        )


if __name__ == "__main__":
    asyncio.run(main())
