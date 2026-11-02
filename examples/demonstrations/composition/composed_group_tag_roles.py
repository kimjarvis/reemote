# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: per-host roles selected by ``host.name``, ``host.group`` and ``host.tags``.

This is a *usage* example, not part of the library. It builds on
``composed_per_host_roles.py`` -- one deploy whose request sequence differs
per host -- and shows the same idea driven by the inventory's static
classifiers: a single ``deploy`` generator that branches on ``host.name``,
``host.group`` and ``host.tags`` to select which roles each machine gets.

The rule that makes this safe is the one ``composed_per_host_roles.py``
states: never branch on a *mutable* fact inside a deploy body (the prepare and
apply phases would flatten differently and ``run_apply`` would raise
``DeterminismError``), but branching on **static** inventory data is fine.
``host.name``, ``host.group`` and ``host.tags`` all come from the inventory
entry and read identically in both phases, so ``deploy(host)`` produces the
same per-host sequence every time the driver re-invokes it -- even though the
sequences differ in length across hosts (a finished host simply idles at the
lockstep barrier while the others run their extra steps).

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
import sys
from collections.abc import Generator
from pathlib import Path

from reemote5 import (
    Host,
    OperationRequest,
    dnf,
    format_log,
    format_plan,
    op,
    run,
)

# examples/demonstrations/composition/composed_group_tag_roles.py -> the
# project root, so ``tests`` is importable whatever the working directory is
# (running a script by path puts its own folder, not the root, on sys.path).
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.simulated_host import SimulatedHost

# Inventory carrying the static classifiers the deploy branches on: ``group``
# (a single label) and ``tags`` (an unordered list of labels). web1 and web2
# share the ``webservers`` group; only web1 carries the ``canary`` tag; db1 is
# the lone ``databases`` host.
INVENTORY = [
    {
        "host": "web1",
        "connection": {},
        "group": "webservers",
        "tags": ["web", "canary"],
    },
    {
        "host": "web2",
        "connection": {},
        "group": "webservers",
        "tags": ["web"],
    },
    {
        "host": "db1",
        "connection": {},
        "group": "databases",
        "tags": ["db"],
    },
]

# Simulated pre-deployment state per host name (test-framework data, NOT
# inventory): web2 already has nginx, so the web role's install is a no-op
# there while web1's is a real change -- which is what makes the plan show
# both ``changed`` and ``no change`` for the same role.
INITIAL_STATE: dict[str, dict[str, object]] = {
    "web2": {"packages": {"nginx"}},
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
    """The web-server role, applied to every host in the ``webservers`` group."""
    yield op(dnf.packages, name="Install nginx", packages=("nginx",), sudo=True)


def role_canary(host: Host) -> Generator[OperationRequest]:
    """Extra live-debugging tooling, applied to hosts tagged ``canary``."""
    yield op(
        dnf.packages,
        name="Install canary tooling",
        packages=("strace", "tcpdump"),
        sudo=True,
    )


def role_database(host: Host) -> Generator[OperationRequest]:
    """The database role, applied to every host in the ``databases`` group."""
    yield op(
        dnf.packages,
        name="Install postgresql",
        packages=("postgresql-server",),
        sudo=True,
    )


def role_backup(host: Host) -> Generator[OperationRequest]:
    """Backup tooling for one named host, selected by ``host.name``."""
    yield op(dnf.packages, name="Install backup agent", packages=("rsync",), sudo=True)


def deploy(host: Host) -> Generator[OperationRequest]:
    """Compose per host from the inventory's static classifiers.

    Three independent branch kinds, all reading data that is identical in the
    prepare and apply phases: ``host.group`` selects the webservers/databases
    roles, ``host.tags`` opts a host into the canary role, and ``host.name``
    pins the backup role to db1. Because the branches are static, each host's
    request sequence is stable across phases and lockstep pairing holds even
    though the sequences differ in length.
    """
    if host.group == "webservers":
        yield from role_web(host)
    if host.group == "databases":
        yield from role_database(host)
    if "canary" in host.tags:
        yield from role_canary(host)
    if host.name == "db1":
        yield from role_backup(host)


async def main() -> None:
    # --dry: prepare phase only. web1 plans two operations (web + canary),
    # web2 one (web, already installed -> no change), db1 two (database +
    # backup). Nothing is executed.
    plans, _, _hosts = await run(
        INVENTORY, deploy, host_factory=simulated_factory, dry=True
    )
    print("== Plan (prepare phase, --dry) ==")
    print(format_plan(plans))

    # Apply phase: the SimulatedHost seam mutates in-memory state in lockstep.
    _plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=simulated_factory
    )
    print("\n== Run log (apply phase) ==")
    assert report is not None
    print(format_log(report))

    print("\n== Final simulated state ==")
    for host in hosts:
        assert isinstance(host, SimulatedHost)
        print(
            f"{host.name}: group={host.group!r} tags={host.tags} "
            f"packages={sorted(host.packages)}"
        )


if __name__ == "__main__":
    asyncio.run(main())
