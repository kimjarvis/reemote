"""Hold apt packages back from upgrades with ``apt-mark hold``.

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

``apt.mark`` is a reemote5 original: pyinfra ships no operation managing
package hold states. A held package is skipped by ``apt upgrade`` /
``apt.dist_upgrade`` until it is released again, which is how a deploy
pins critical packages at their current version without uninstalling
anything. The operation is idempotent on the ``AptHeldPackages`` fact
(``apt-mark showhold``): ``held=True`` marks only the requested names not
already held, ``held=False`` releases only the names currently held
(``apt-mark unhold``), and a request whose names are all in the wanted
state is a no-op. The script yields the *same* hold request twice to show
that idempotence across the two phases: the prepare phase plans both as
changes (nothing is sent yet), while the apply phase holds on the first
and reports the second as a no-op, because the fresh per-step fact cache
re-reads ``showhold`` against the updated marks.

Marking writes to the root-owned dpkg database, so the requests are
flagged ``sudo=True`` and the driver elevates their commands. Pair this
with ``apt_upgrade.py``'s ``apt.upgrade`` to compose the
hold-then-patch flow: everything except the held packages upgrades, and a
later ``op(apt.mark, packages=CRITICAL_PACKAGES, held=False, sudo=True)``
releases them again.

Run it with::

    uv run python examples/modules/apt/apt_mark.py
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

# The packages to keep out of upgrades -- the same baseline names the
# apt_packages.py example installs. A module-level tuple, so the request
# kwargs are hashable and identical in the prepare and apply phases.
CRITICAL_PACKAGES = ("htop", "tree")


def deploy(host: Host) -> Generator[OperationRequest]:
    """Hold the critical packages -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request holds whatever names are not held yet; the second
    carries the same arguments and is a no-op in the apply phase once the
    first hold marked them all.

    Marking writes to the root-owned dpkg database, so both requests are
    flagged ``sudo=True`` and the driver elevates their commands over SSH.
    """
    yield op(
        apt.mark,
        name="Hold critical packages",
        packages=CRITICAL_PACKAGES,
        sudo=True,
    )
    yield op(
        apt.mark,
        name="Re-run hold (no-op)",
        packages=CRITICAL_PACKAGES,
        sudo=True,
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. Facts are still gathered over SSH (the
        # showhold read is read-only) so the plan reflects each host's real
        # pre-deployment state; no command that mutates a host is sent.
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