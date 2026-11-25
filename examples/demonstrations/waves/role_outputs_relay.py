# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: capture a child role's outputs and relay them to a sibling.

A role's **return value** is the only channel from a child deploy to its
parent: ``out = yield from scoped("child", role(v))(host)`` both sequences the
child's stages *and* binds what the child returned, because ``scoped`` forwards
``send()`` inward and the child's ``return`` outward (§10.4, §16.8). There is
no registry, no outputs bus — the parent consumes the dataclass and drives its
own next steps from it. This is how ``php_xdebug`` reads ``php``'s resolved
version and conf paths instead of copying the version table (§18.7).

Two traps, both named in the design and both cheap to hit:

- the child **must return its outputs on every path**, including the family
  guard's early return — a bare ``return`` hands the parent ``None`` and the
  first attribute access explodes far from the guard;
- the capture variable must not be shadowed by a later local assignment in the
  same function — ``out = yield from ...`` followed by an ``out = ...`` in a
  branch is fine, but *using* ``out`` before its only assignment on some path
  is the classic ``UnboundLocalError``. The body below assigns once, at the top.

Runs **entirely in memory** like the other wave demonstrations; the project
root is added to ``sys.path`` to import the test framework's ``SimulatedHost``.

Run it with::

    python examples/demonstrations/waves/role_outputs_relay.py
"""

import asyncio
import sys
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

from reemote import Host, dnf, files, op, run_waves
from reemote.modules.server import LinuxDistribution
from reemote.stages import Deploy, StageItem, fact, scoped

# examples/demonstrations/waves/role_outputs_relay.py -> the project root, so
# ``tests`` is importable (running a script by path puts its own folder, not
# the root, on sys.path).
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.simulated_host import SimulatedHost

INVENTORY = [
    {"host": "rh1", "family": "RedHat", "connection": {"host": "rh1.example"}},
    {"host": "ub1", "family": "Ubuntu", "connection": {"host": "ub1.example"}},
]


@dataclass(frozen=True)
class ChildOutputs:
    """What the child role publishes: one frozen record per host.

    A dataclass, not a dict — the parent accesses fields by name, so a typo is
    an ``AttributeError`` at the use site rather than a ``KeyError`` miles away
    (the ``<Role>Outputs`` convention of the shipped roles).
    """

    family: str
    extra_packages: tuple[str, ...]


def child() -> Deploy:
    """The child role factory: reads the family, publishes what it implies.

    A factory returning a per-host generator, exactly like a library role —
    the demonstration is the *channel*, so the body stays small.
    """

    def deploy(host: Host) -> Generator[StageItem, None, ChildOutputs]:
        distro = yield fact(LinuxDistribution, name="Which family")
        family = distro["family"]
        # Every path returns an Outputs — the RedHat branch, the fallback
        # branch, and (not shown) any eligibility guard's early return. A
        # bare ``return`` here would poison the parent's next line.
        if family == "RedHat":
            return ChildOutputs(family=family, extra_packages=("epel-release",))
        return ChildOutputs(family=family, extra_packages=("software-properties-common",))

    return deploy


def parent(host: Host) -> Generator[StageItem]:
    """Consume the child's outputs to drive the parent's own next request.

    ``yield from`` is the composition (``compose``/``sub_deploy`` would raise
    ``DeployRequestError`` on the child's barrier — rule 6), and ``scoped``
    labels the child's report block; the expression *value* is the child's
    return, bound once at the top so no path can read it unassigned.
    """
    out: ChildOutputs = yield from scoped("child", child())(host)

    # The relay: a sibling stage built purely from the captured data. The
    # parent never re-reads the fact the child read — that is the point of the
    # return-value channel.
    yield op(
        dnf.packages,
        name="Install relay packages",
        packages=out.extra_packages,
        sudo=(out.family == "RedHat"),
    )
    yield op(
        files.install,
        name="Record relay family",
        dest=f"/etc/deployed-{out.family.lower()}",
        content=f"{out.family}\n",
        mode="0644",
        sudo=True,
    )


def factory(entry: dict) -> SimulatedHost:
    """``host_factory`` seeding two distributions, so the relay differs."""
    host = SimulatedHost.from_inventory(entry)
    if host.name == "rh1":
        host.linux_distribution = {"name": "CentOS", "family": "RedHat", "major": "9"}
    else:
        host.linux_distribution = {"name": "Ubuntu", "family": "Debian", "major": "24.04"}
    return host


async def main() -> None:
    # One stream per host; the child's barrier closes its wave, the relay
    # stage lands in the next one — per-host asymmetry included (rule 7's
    # shape: the guard is data the child returns, not a skipped request).
    report, _hosts = await run_waves(INVENTORY, parent, host_factory=factory)
    assert report is not None
    print("== Wave tree: scoped child block, then the relay stage ==")
    print(report.tree())

    relayed = {e.host: e for e in report.merged().log if e.name == "Install relay packages"}
    assert set(relayed) == {"rh1", "ub1"}, relayed
    print("\nrelayed targets: epel-release on the RedHat host, "
          "software-properties-common on the Debian one")


if __name__ == "__main__":
    asyncio.run(main())
