# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Remove orphaned apk packages standalone, without an install.

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

``apk.autoremove`` is a reemote5 original: pyinfra ships no apk autoremove,
and apk itself has no ``autoremove`` verb -- it exposes orphan purge as the
``--purge`` flag of ``apk add``/``apk fix``, which reemote5 splits out into
this standalone operation so a deploy can clean up orphans without also
performing an install or a repair. The ``ApkAutoremovable`` fact simulates
the purge (``apk add --purge --simulate``, which never removes anything
and needs no root) and names exactly the orphaned packages: a non-empty
list runs the real ``apk add --purge``, an empty list is a no-op. The
script yields the *same* request twice to show that idempotence across the
two phases: on a machine with orphans the prepare phase plans both as
changes (nothing is sent yet), while the apply phase removes on the first
and reports the second as a no-op, because the fresh per-step fact cache
re-simulates against the cleaned-up state.

Purging writes to the root-owned apk database, so the requests are flagged
``sudo=True`` and the driver elevates their commands. Pair this with
``apk_packages.py``'s ``apk.packages`` (``present=False``) to compose the
remove-then-cleanup flow as two explicit, separately plannable steps -- the
same split ``apt.auto_remove`` makes against ``apt.dist_upgrade``'s
``auto_remove=True``.

Run it with::

    uv run python examples/modules/apk/apk_autoremove.py
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
    apk,
    format_log,
    format_plan,
    op,
    run,
)

# The inventory: a real Alpine server and its SSH connection information,
# supplied by the application to the library (the same connection data as the
# inventory_alpine.json entry the read-only apk fact examples load). The
# default host_factory materializes each entry into a plain Host that connects
# over asyncssh.
INVENTORY = [
    {
        "host": "alpine",
        "connection": {
            "address": "192.168.0.197",
            "username": "alpine",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "alpine",
        "tags": ["alpine_host"],
    },
]


def deploy(host: Host) -> Generator[OperationRequest]:
    """Autoremove orphaned packages -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request purges whatever the simulation names; the second
    carries the same arguments and is a no-op in the apply phase once the
    first cleanup left nothing purgable.

    Purging writes to the root-owned apk database, so both requests are
    flagged ``sudo=True`` and the driver elevates their commands over SSH.
    """
    yield op(apk.autoremove, name="Remove orphaned packages", sudo=True)
    yield op(apk.autoremove, name="Re-run autoremove (no-op)", sudo=True)


async def main() -> None:
    try:
        # --dry: prepare phase only. Facts are still gathered over SSH (the
        # purge simulation is read-only) so the plan reflects each host's
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
