# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: an ``act`` probe inside one deploy — the Result is data, not control flow.

``deploy_action_deploy.py`` argues that actions belong *before, between and
after* deploys, never inside one — true of a direct ``gather_action`` call,
which the wave loop keeps rejecting with ``ActionInDeployError`` (§5.4). The
sanctioned in-stream form is different: ``yield act(Shell, ...)`` parks the
generator, the driver runs the action on that host in the rendezvous, and the
``Result`` resumes the body (§2). This file is exactly §10.3's apache version
probe, at nginx scale.

The trap it makes runnable: **a barrier result is data**. A non-zero exit
never raises — ``Shell.process`` returns a ``Result`` whatever the command
reports — so the body must inspect ``exit_status`` by hand. Every migrated
role that probes a binary (apache, php, java, php_xdebug) opens with this
check; a body that forgets it parses garbage out of an empty stdout and
selects a version table that never matched anything. Transport and privilege
failures, by contrast, *do* raise: the distinction is exit status vs. reach.

Runs **entirely in memory** like the other wave demonstrations: the shell
text is answered from ``SimulatedHost.shell_results`` (keyed by the exact
command string), so the probe is canned but the branching is real. The project
root is added to ``sys.path`` to import the test framework's host.

Run it with::

    python examples/demonstrations/waves/barrier_act.py
"""

import asyncio
import sys
from collections.abc import Generator
from pathlib import Path

from reemote import Host, files, op, run_waves
from reemote.actions import Shell
from reemote.stages import StageItem, act

# examples/demonstrations/waves/barrier_act.py -> the project root, so
# ``tests`` is importable (running a script by path puts its own folder, not
# the root, on sys.path).
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.simulated_host import SimulatedHost

INVENTORY = [
    {"host": "web1", "family": "RedHat", "connection": {"host": "web1.example"}},
    {"host": "web2", "family": "RedHat", "connection": {"host": "web2.example"}},
]

# The probe, sent verbatim (Shell escapes nothing), and the exact key the
# simulation answers it from.
PROBE = "nginx -v 2>&1"
VERSION_FILE = "/etc/nginx/deployed-version"

# What each host's shell reports: web1 has nginx, web2 does not — and the
# simulation answers a *missing command* the way a real shell does, with exit
# 127 on stderr, not with an exception. That failure is the demo's point.
SHELL_RESULTS = {
    "web1": {"exit_status": 0, "stdout": "nginx version: nginx/1.24.0", "stderr": ""},
    "web2": {
        "exit_status": 127,
        "stdout": "",
        "stderr": "bash: nginx: command not found",
    },
}


def factory(entry: dict) -> SimulatedHost:
    """``host_factory`` seeding each host's canned answer for the probe."""
    host = SimulatedHost.from_inventory(entry)
    host.shell_results[PROBE] = SHELL_RESULTS[host.name]
    return host


def parse_version(result) -> str | None:
    """Pull the version out of a probe ``Result`` — or refuse to guess.

    ``Result`` is a TypedDict — an ordinary dict with ``exit_status`` /
    ``stdout`` / ``stderr`` keys — so the gate reads fields, not attributes.
    Pure, so the family tables a real role would select on this value stay
    testable; returns ``None`` for any non-zero exit, which is the check the
    trap demands. Never assume the command succeeded because no exception
    was raised.
    """
    if result["exit_status"] != 0:
        return None
    _, _, declared = result["stdout"].partition("nginx/")
    return declared.strip() or None


def deploy(host: Host) -> Generator[StageItem]:
    """Probe the installed binary, then record its version — or say nothing.

    One stream, no pre-run gather: the stage *after* the probe is built from
    what the probe *actually printed on this host*, which is the read
    ``deploy_action_deploy.py`` needed two deploys and an action fan-out for.
    """
    yield op(
        files.directory,
        name="Ensure staging dir",
        path="/tmp/nginx-meta",
    )

    # The barrier: an action, run now, whose Result resumes the body. A
    # non-zero exit comes back as data — the body, not the driver, decides
    # what it means.
    result = yield act(Shell, command=PROBE, name="nginx -v")
    version = parse_version(result)
    if version is None:
        # Exit 127 is not an exception; had the body skipped this gate it
        # would write an empty version file and believe it.
        print(
            f"{host.name}: probe exited {result['exit_status']}; not recording a version"
        )
        return

    print(f"{host.name}: probe says {version} (exit {result['exit_status']})")
    yield op(
        files.install,
        name="Record version",
        dest=f"{VERSION_FILE}-{version.rsplit('.', 1)[0]}",
        content=f"{version}\n",
        mode="0644",
        sudo=True,
    )


async def main() -> None:
    # web1: exit 0 -> the version-stage is emitted.
    # web2: exit 127 -> data, not an exception; the stage is skipped by hand.
    report, _hosts = await run_waves(INVENTORY, deploy, host_factory=factory)
    assert report is not None
    print("== Wave tree: act probe, exit status checked by hand ==")
    print(report.tree())

    names = [entry.name for entry in report.merged().log]
    assert names.count("Record version") == 1, names
    print("\nhosts that recorded a version: 1 (the other's probe exited 127)")


if __name__ == "__main__":
    asyncio.run(main())
