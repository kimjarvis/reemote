# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: a handler in one stream — ``checkpoint`` + ``changed_hosts`` + restart.

``handler_restart.py`` shows the barrier-free notification idiom: run the
configuring deploy, read its ``ApplyReport`` in the gap *between* two complete
``run()`` cycles, freeze the changed hosts with ``changed_hosts``, and let a
second deploy carry the restart. On the wave path the same Ansible
``notify:`` fan-in needs no gap at all — a ``checkpoint`` barrier *is* the
read: it closes the wave the notifying requests just ran in and sends back
that wave's ``ApplyReport``, sliced to the checkpoint's scope, with no host
contact (§4.3).

This file also makes the one silent failure of the model runnable: **check-
point placement**. A checkpoint reports only the wave that just ended, so a
second checkpoint placed straight after the first closes a barrier-only wave
and reads an empty report — every notification it meant to relay is lost, and
the run still exits 0. Both the correct placement and the misplaced twin are
asserted here, because a trap only described in prose gets rediscovered as a
bug report.

Like the other wave demonstrations this runs **entirely in memory**: the test
framework's ``SimulatedHost`` is injected through ``run_waves``'s
``host_factory``, so it needs no SSH connection; the project root is added to
``sys.path`` to import it.

Run it with::

    python examples/demonstrations/waves/checkpoint_handler.py
"""

import asyncio
import sys
from collections.abc import Generator
from pathlib import Path

from reemote import Host, dnf, files, op, run_waves
from reemote.handlers import changed_hosts, restart_handler
from reemote.stages import StageItem, checkpoint

# examples/demonstrations/waves/checkpoint_handler.py -> the project root, so
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

CONF = "/etc/nginx/conf.d/redirect.conf"
CONTENT = "server { return 301 https://$host$request_uri; }\n"

# The notification channel: the request names that queue the handler, exactly
# the ``notify:`` list an Ansible handler would carry. ``changed_hosts``
# matches these against ``LogEntry.name`` — the literal ``name=`` of the
# request, not the text rendered in the tree.
NOTIFYING = {"Install nginx", "Write nginx drop-in"}


def factory(entry: dict) -> SimulatedHost:
    """``host_factory``: web2 is converged (package installed, config landed).

    So the first pass notifies only ``web1`` — one host restarted, one not —
    and the second pass notifies nobody, the converged no-op Ansible's
    handler gives for free.
    """
    host = SimulatedHost.from_inventory(entry)
    if host.name == "web2":
        host.packages = {"nginx": ["1.24.0"]}
        # The content store ``files.install`` reads back for its idempotence
        # check: path -> bytes, plus the FileExists/stat seams the fact chain
        # consults.
        host.remote_files[CONF] = CONTENT.encode("utf-8")
        host.existing_files.add(CONF)
        host.file_stats[CONF] = {"exists": True, "mode": 0o644, "size": len(CONTENT)}
    return host


def deploy(host: Host) -> Generator[StageItem]:
    """Configure nginx and restart it only where the config actually changed.

    The stream: install, write, ``yield checkpoint(...)`` — the report the
    two-deploy split read in the gap between runs now arrives *inside* the
    body — and the handler deploy composed with plain ``yield from``. The
    checkpoint must immediately follow the stage it reports on; that is rule
    4 of the role model, and main() below proves what breaking it costs.
    """
    yield op(
        dnf.packages,
        name="Install nginx",
        packages=("nginx",),
        sudo=True,
    )
    yield op(
        files.install,
        name="Write nginx drop-in",
        dest=CONF,
        content=CONTENT,
        mode="0644",
        sudo=True,
    )

    # The in-stream notification read: closes the wave the two requests above
    # just ran in and returns its ApplyReport. No host contact, no second run.
    report = yield checkpoint("configure done")
    notified = changed_hosts(report, NOTIFYING)
    print(f"{host.name}: notified={sorted(notified) or 'nobody'}")

    # The handler is an ordinary request-only deploy, so it composes with
    # ``yield from``; run_waves gives it its own wave after the checkpoint.
    yield from restart_handler(notified, "nginx")(host)


def misplaced(host: Host) -> Generator[StageItem]:
    """The anti-pattern: a second checkpoint right after the first.

    Kept importable (it contains no ``op(...)`` of its own, so the example
    harness drains the correct body above, not this one) and run by main() to
    show the *silent* loss: the misplaced checkpoint closes a barrier-only
    wave, its report has no log entries, and the handler it feeds restarts
    nobody while the run still exits 0.
    """
    report = yield checkpoint("configure done")
    lost = yield checkpoint("configure done (again)")
    notified = changed_hosts(report, NOTIFYING)
    stale = changed_hosts(lost, NOTIFYING)
    print(f"{host.name}: correct={sorted(notified)} misplaced={sorted(stale) or 'nobody'}")
    yield from restart_handler(stale, "nginx")(host)


async def main() -> None:
    report, hosts = await run_waves(INVENTORY, deploy, host_factory=factory)
    assert report is not None
    print("== Wave tree: one stream, handler after the checkpoint ==")
    print(report.tree())

    # Pin the fan-in on the *record* fields, not the rendered tree: the
    # checkpoint's own report carried web1's change, and exactly one host was
    # restarted.
    restarts = [entry for entry in report.merged().log if "(handler)" in entry.name]
    assert {entry.host for entry in restarts} == {"web1"}, restarts

    # Converged replay: no write changes, so the checkpoint notifies nobody
    # and the handler wave empties itself.
    by_name = {host.name: host for host in hosts}
    again, _ = await run_waves(
        INVENTORY, deploy, host_factory=lambda entry: by_name[entry["host"]]
    )
    assert again is not None
    restarted = [entry for entry in again.merged().log if "(handler)" in entry.name]
    print("\n== Converged replay ==")
    print(f"handler entries: {len(restarted)} (nothing changed; nobody restarted)")

    # The trap, run for real: the second checkpoint closes a barrier-only
    # wave, reads an empty report, and silently notifies nobody — while the
    # first, correctly placed checkpoint in the same stream saw the change.
    print("\n== Misplaced checkpoint (the silent-loss anti-pattern) ==")
    trap, _ = await run_waves(INVENTORY, misplaced, host_factory=factory)
    assert trap is not None
    print(trap.tree())


if __name__ == "__main__":
    asyncio.run(main())
