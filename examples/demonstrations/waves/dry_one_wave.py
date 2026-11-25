# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: what ``dry=True`` can and cannot preview once a barrier is present.

A barrier-free ``run(dry=True)`` previews the whole deploy, because prepare
materialises every request without applying it. ``run_waves`` has no second
pass to plan against: it pulls each body until it *parks*, applies the wave,
executes the barrier — so a dry run stops at the first barrier and marks the
barrier itself, and everything downstream of it, as unknown (§6.3). §16.6
decided deliberately *against* a read-only dry mode that would execute
barriers optimistically, so this limit is a choice, not a bug — and an
unpreviewed plan *looks* like a bug unless somebody runs it and sees the
``unknown until run`` line. This file is that somebody.

The comparison is the lesson:

- ``dry=True``: wave 0 planned for real (facts gathered, nothing mutated),
  then the barrier and everything after it marked unknown;
- the full run: the same wave 0, the barrier answered, and the stages the
  dry run could never have shown.

Runs **entirely in memory**; the project root is added to ``sys.path`` to
import the test framework's ``SimulatedHost``.

Run it with::

    python examples/demonstrations/waves/dry_one_wave.py
"""

import asyncio
import sys
from collections.abc import Generator
from pathlib import Path

from reemote import Host, dnf, files, op, run_waves
from reemote.stages import StageItem, fact

# examples/demonstrations/waves/dry_one_wave.py -> the project root, so
# ``tests`` is importable (running a script by path puts its own folder, not
# the root, on sys.path).
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.simulated_host import SimulatedHost

INVENTORY = [
    {"host": "web1", "family": "RedHat", "connection": {"host": "web1.example"}},
]

CONF = "/etc/motd"
TEXT = "written by the stage the dry run could not preview\n"


def factory(entry: dict) -> SimulatedHost:
    """``host_factory``: a bare simulated host (nothing pre-converged)."""
    return SimulatedHost.from_inventory(entry)


def deploy(host: Host) -> Generator[StageItem]:
    """One stage, a fact barrier, a second stage — the minimal wave shape.

    The second stage is what no dry run can promise: its *existence* depends
    on the fact the barrier has not read yet. A preview that invented it would
    be forecasting; §16.6 would rather print the truth.
    """
    yield op(
        dnf.packages,
        name="Install cronie",
        packages=("cronie",),
        sudo=True,
    )

    installed = yield fact(dnf.DnfPackages, name="Check cronie")
    names = {entry["name"] for entry in installed}

    # Downstream of the barrier: built from the read, invisible to a preview.
    if "cronie" in names:
        yield op(
            files.install,
            name="Write motd",
            dest=CONF,
            content=TEXT,
            mode="0644",
            sudo=True,
        )


async def main() -> None:
    # The one-wave preview: wave 0 is planned honestly, the barrier is marked.
    dry, _ = await run_waves(INVENTORY, deploy, host_factory=factory, dry=True)
    assert dry is not None
    print("== dry=True: wave 0 previewed, then unknown until run ==")
    print(dry.tree())

    # The same body for real: the barrier answers, and a wave the preview
    # never showed now appears.
    report, _hosts = await run_waves(INVENTORY, deploy, host_factory=factory)
    assert report is not None
    print("\n== the full run: the unpreviewable wave, executed ==")
    print(report.tree())

    previewed = "unknown until run" in dry.tree()
    wrote = any(e.name == "Write motd" and e.changed for e in report.merged().log)
    assert previewed and wrote, (dry.tree(), report.tree())
    print("\nthe dry tree said 'unknown until run'; the apply produced the wave anyway — §16.6's choice, visible")


if __name__ == "__main__":
    asyncio.run(main())
