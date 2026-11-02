"""Upgrade all apt packages, gated by a dry-run simulation.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``). Each plain ``Host``
lazily opens an asyncssh connection from its ``connection`` data, gathers
facts and runs commands for real, and ``run`` closes those connections when
it finishes.

``apt.upgrade`` mirrors pyinfra's ``apt.upgrade``: the
``SimulateOperationWillChange`` fact first runs ``upgrade --dry-run``
(under ``LC_ALL=C`` and the non-interactive prefix) and parses the
``N upgraded, N newly installed, N to remove and N not upgraded.`` summary
line. When every count is zero the operation is a no-op; otherwise it runs
the real ``upgrade`` -- which, unlike ``dist-upgrade``, never installs new
dependencies or removes conflicting packages. A failed simulation (no
summary line) runs the real command anyway, pyinfra's fallback. The script
yields the *same* request twice to show that idempotence across the two
phases: on a machine with pending upgrades the prepare phase plans both as
changes (nothing is sent yet), while the apply phase upgrades on the first
and reports the second as a no-op, because the fresh per-step fact cache
re-simulates against the upgraded state.

Where pyinfra's ``apt.upgrade`` also takes an ``auto_remove`` parameter
(appending ``--autoremove`` to the same command), reemote5 deliberately
drops it in favour of the standalone ``apt.auto_remove`` operation -- see
``apt_auto_remove.py`` beside this script -- so orphaned dependencies can be
cleaned up without also performing an upgrade. This operation never passes
``--autoremove``.

Upgrading writes to the root-owned dpkg database, so the requests are
flagged ``sudo=True`` and the driver elevates their commands. The dry-run
simulation itself needs no root.

Run it with::

    uv run python examples/modules/apt/apt_upgrade.py
"""

import asyncio
import sys
from collections.abc import Generator

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    OperationRequest,
    PrivilegeError,
    apt,
    format_log,
    format_plan,
    op,
    run,
)

# The inventory: a real Ubuntu server and its SSH connection information,
# supplied by the application to the library (the same connection data as the
# inventory_ubuntu.json entry the JSON-inventory demonstration loads). The
# default host_factory materializes each entry into a plain Host that connects
# over asyncssh.
INVENTORY = [
    {
        "host": "ubuntu",
        "connection": {
            "address": "192.168.0.221",
            "username": "kjarvis",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "ubuntu",
        "tags": ["ubuntu_host"],
    },
]


def deploy(host: Host) -> Generator[OperationRequest]:
    """Upgrade all packages -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request upgrades whatever the simulation proposes; the second
    carries the same arguments and is a no-op in the apply phase once the
    first upgrade left nothing more to do.

    Upgrading writes to the root-owned dpkg database, so both requests are
    flagged ``sudo=True`` and the driver elevates their commands over SSH.
    """
    yield op(apt.upgrade, name="Upgrade apt packages", sudo=True)
    yield op(apt.upgrade, name="Re-run upgrade (no-op)", sudo=True)


async def main() -> None:
    try:
        # --dry: prepare phase only. Facts are still gathered over SSH (the
        # dry-run simulation is read-only) so the plan reflects each host's
        # real pre-deployment state; no command that mutates a host is sent.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
        plans, report, _hosts = await run(INVENTORY, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())