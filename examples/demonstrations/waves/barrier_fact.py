# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: branch on a ``fact`` barrier inside one deploy — one stream, no second run.

``two_deploy_split.py`` shows the barrier-free way to ask "did the install
actually put nginx on the box?": two complete ``run()`` cycles with a frozen
``gather_fact`` snapshot in the gap. This file is the same scenario on the
wave path, in a third of the machinery: one deploy body whose
``yield from``-able steps are separated by a single ``fact`` barrier, so the
read happens *between* the install and the step that depends on it, inside the
same stream (§6.2 row 3).

The rule that used to forbid this — never branch on a mutable fact inside a
deploy, because the prepare and apply phases would flatten differently and
``run_apply`` would raise ``DeterminismError`` — is a ``run()`` rule. There is
exactly **one pass** per host under ``run_waves``, so branching on a barrier
result is not tolerated, it is *the point*: the body suspends at
``yield fact(...)``, the driver reads the fact on that host, and the value
resumes the generator as if it had been computed locally.

The trap this makes runnable is the mirror of the ``run()`` one: because the
body runs once, the fact is **fresh** (no frozen-snapshot staleness) but
nothing after it is previewable — a ``dry=True`` run stops at the first
barrier (§6.3). "Fresher but unpreviewable" versus "stale but previewable"
is the real choice between this file and ``two_deploy_split.py``.

Like ``composed_group_tag_roles.py`` this runs **entirely in memory**: the
test framework's ``SimulatedHost`` is injected through ``run_waves``'s
``host_factory``, so it needs no SSH connection. ``SimulatedHost`` is
test-framework scaffolding (it lives in ``tests/``, never in the library), so
the project root is added to ``sys.path`` to import it.

Run it with::

    python examples/demonstrations/waves/barrier_fact.py
"""

import asyncio
import sys
from collections.abc import Generator
from pathlib import Path

from reemote import Host, dnf, files, op, run_waves, systemd
from reemote.stages import StageItem, fact

# examples/demonstrations/waves/barrier_fact.py -> the project root, so
# ``tests`` is importable (running a script by path puts its own folder, not
# the root, on sys.path).
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.simulated_host import SimulatedHost

# An inline inventory, like the docker connector demonstration: this demo
# never touches the shared ``inventory.json``, because nothing here connects.
# Every entry carries ``family``, so ``Host.from_inventory`` accepts it even
# though only ``SimulatedHost.from_inventory`` builds the hosts.
INVENTORY = [
    {"host": "web1", "family": "RedHat", "connection": {"host": "web1.example"}},
    {"host": "web2", "family": "RedHat", "connection": {"host": "web2.example"}},
]

CONF = "/etc/nginx/conf.d/redirect.conf"
CONTENT = "server { return 301 https://$host$request_uri; }\n"


def factory(entry: dict) -> SimulatedHost:
    """``host_factory`` seeding the simulated pre-deployment state.

    ``web2`` already has nginx *and* a running unit, so the fact barrier reads
    ``present`` there while ``web1`` starts bare — which is what makes the two
    hosts take different branches of the same body.
    """
    host = SimulatedHost.from_inventory(entry)
    if host.name == "web2":
        host.packages = {"nginx": ["1.24.0"]}
        host.services = {"nginx.service": "active"}
    return host


def configure(host: Host) -> Generator[StageItem]:
    """The dependent step: a drop-in plus the service, once nginx is there."""
    yield op(
        files.install,
        name="Write nginx drop-in",
        dest=CONF,
        content=CONTENT,
        mode="0644",
        sudo=True,
    )
    yield op(
        systemd.service,
        name="Run nginx",
        service="nginx",
        running=True,
        enabled=True,
        sudo=True,
    )


def deploy(host: Host) -> Generator[StageItem]:
    """Install nginx, read back what the install actually did, configure it.

    Two request stages and one barrier — no second ``run()``:

    1. the package install (plus the config directory the drop-in needs);
    2. ``yield fact(DnfPackages, "nginx")`` — the ``register`` read of an
       Ansible play, now *inside* the stream;
    3. the configure steps, emitted **only if** the fact says the binary is
       really installed.

    Under ``run()`` the branch in step 3 is the illegal move (the phases would
    re-flatten differently); under ``run_waves`` there is one pass, so the
    branch is a plain local decision made on a fresh read.
    """
    yield op(
        dnf.packages,
        name="Install nginx",
        packages=("nginx",),
        sudo=True,
    )
    yield op(
        files.directory,
        name="Ensure conf.d",
        path="/etc/nginx/conf.d",
        sudo=True,
    )

    # The barrier: the generator parks, the driver reads DnfPackages on this
    # host, and the fact value (a list of ``{"name", "versions"}`` entries)
    # resumes the body.
    installed = yield fact(dnf.DnfPackages, name="Check nginx")
    if not any(entry["name"] == "nginx" for entry in installed):
        # A failed install that the package manager swallowed: skip the
        # dependent steps but keep the host in the run (raising here would
        # abort every other host's lockstep wave).
        print(f"{host.name}: nginx is not installed after the dnf step; skipping configure")
        return

    yield from configure(host)


async def main() -> None:
    # Apply: one wave loop, per-host branches resolved by the fact barrier.
    # web1 starts bare -> install + directory + (fact: present) + configure.
    # web2 already has nginx -> the same requests, the same branch taken.
    report, _hosts = await run_waves(INVENTORY, deploy, host_factory=factory)
    assert report is not None
    print("== Wave tree (one stream per host, fact barrier in the middle) ==")
    print(report.tree())

    # The same stream replayed against the now-converged hosts: the barrier
    # reads the *post-apply* state, so nothing is previewed and nothing
    # changes — idempotence through the fresh read, not a frozen snapshot.
    # ``run_waves`` returns its hosts; re-serving them from the same objects
    # keeps the simulated state across the two passes.
    by_name = {host.name: host for host in _hosts}
    again, _ = await run_waves(
        INVENTORY, deploy, host_factory=lambda entry: by_name[entry["host"]]
    )
    assert again is not None
    changed = [entry for entry in again.merged().log if entry.changed]
    print("\n== Second run against the converged hosts ==")
    print(f"changed entries: {len(changed)} (the fact barrier re-read the live state)")


if __name__ == "__main__":
    asyncio.run(main())
