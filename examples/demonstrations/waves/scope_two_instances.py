# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: one role factory, two instances — ``scoped`` labels, nothing collapses.

``scoped("eu", role(v))`` stamps ``("eu",)`` onto the ``scope`` of every item
the child yields, so the report tree shows one labelled block per *call site*
— while the request's ``name`` stays untouched, because ``changed_hosts``
matches names and rule 5 forbids rewriting them for scoping. This file runs
the same factory twice, ``scoped("eu", ...)`` and ``scoped("us", ...)`` in one
body, and prints the tree.

The traps it makes visible are the §13 limits, as output rather than prose:

- **No instance identity.** Two invocations of one factory are two report
  paths, not one role instance — nothing in the engine knows they are "the
  same role", and ``scope`` is manual and never collapses (§7.2).
- **No de-duplication.** The second instance re-issues byte-identical
  requests. On a converged host each one resolves to *no change* (the
  operations are idempotent), but the commands still round-trip: a shared
  prerequisite must be hoisted to the composition site, not trusted to merge.

Like the other wave demonstrations this runs **entirely in memory** through
``run_waves``'s ``host_factory``; the project root is added to ``sys.path`` to
import the test framework's ``SimulatedHost``.

Run it with::

    python examples/demonstrations/waves/scope_two_instances.py
"""

import asyncio
import sys
from collections.abc import Generator
from pathlib import Path

from reemote import Host, dnf, files, op, run_waves
from reemote.stages import StageItem, scoped

# examples/demonstrations/waves/scope_two_instances.py -> the project root, so
# ``tests`` is importable (running a script by path puts its own folder, not
# the root, on sys.path).
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.simulated_host import SimulatedHost

INVENTORY = [
    {"host": "web1", "family": "RedHat", "connection": {"host": "web1.example"}},
]

MARKER = "/etc/motd"
TEXT = "managed by two scoped role instances\n"
PACKAGES = ("rsyslog",)


def base_role(host: Host) -> Generator[StageItem]:
    """The role: one package stage, one file stage, no barriers.

    Deliberately instance-blind — it never learns whether it is running as
    ``eu`` or ``us``. Identity, if any is wanted, lives at the composition
    site in the ``scoped`` label.
    """
    yield op(
        dnf.packages,
        name="Install base packages",
        packages=PACKAGES,
        sudo=True,
    )
    yield op(
        files.install,
        name="Write marker",
        dest=MARKER,
        content=TEXT,
        mode="0644",
        sudo=True,
    )


def deploy(host: Host) -> Generator[StageItem]:
    """Run one factory twice, labelled as two instances.

    ``scoped`` forwards ``send()`` values inward and the child's ``return``
    value outward, so barriers would survive the wrap; here the children are
    request-only and the point is the *report*: each block is contiguous and
    in wave order, without the wave counter being sorted globally (§7.2).
    """
    yield from scoped("eu", base_role)(host)
    yield from scoped("us", base_role)(host)


async def main() -> None:
    report, hosts = await run_waves(INVENTORY, deploy, host_factory=SimulatedHost.from_inventory)
    assert report is not None
    print("== Wave tree: two scope groups, one host, identical requests ==")
    print(report.tree())

    # The no-collapse proof, on records not rendering: every request name
    # appears twice in the log, once per scope path — the duplicate was
    # executed, never merged away.
    entries = report.merged().log
    eu = [e.name for e in entries if "eu" in e.scope]
    us = [e.name for e in entries if "us" in e.scope]
    assert eu == us == ["Install base packages", "Write marker"], (eu, us)
    print(f"\nrequests per instance: {len(eu)} (issued twice — no dedupe across instances)")

    # The idempotence half: replaying the double-scoped stream against the
    # converged host issues the same requests again and changes nothing.
    by_name = {h.name: h for h in hosts}
    again, _ = await run_waves(
        INVENTORY, deploy, host_factory=lambda entry: by_name[entry["host"]]
    )
    assert again is not None
    changed = [e for e in again.merged().log if e.changed]
    print(f"changed entries on replay: {len(changed)} (duplicate, but each a no-op)")


if __name__ == "__main__":
    asyncio.run(main())
