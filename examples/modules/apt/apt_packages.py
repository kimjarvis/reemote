# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install packages by name with exact-version idempotence.

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

``run`` is a coroutine (both phases are async), so ``main`` is async and
driven by ``asyncio.run``.

``apt.packages`` installs (or with ``present=False`` removes) packages by
name -- here two small CLI utilities, the kind of baseline tooling a deploy
ensures on every machine. It reads the installed versions through the
``AptPackages`` fact and installs only what is missing, so re-running is a
no-op. A name can be pinned as ``<pkg>=<version>``: a pin counts as present
only when that *exact* version is installed, pyinfra's ``ensure_packages``
version match. ``latest=True`` would additionally upgrade every installed,
unpinned name (pyinfra's legacy blind upgrade); ``update=True`` runs a
stateless ``apt-get update`` first, and ``upgrade=True`` an
``apt-get upgrade`` gated by the ``SimulateOperationWillChange`` simulation,
exactly like ``apt.dist_upgrade``. Where pyinfra's ``cache_time`` skips the
update while its stamp file is fresh, reemote5 deliberately drops the
option -- update is always stateless. pyinfra's ``force`` option
(``--force-yes``) is dropped as deprecated, the same decision as ``apt.deb``.

The script yields the *same* install request twice to show that idempotence
across the two phases: the prepare phase plans both as changes (nothing is
installed yet, so both compare against the same pre-deployment state), while
the apply phase installs on the first and reports the second as a no-op,
because the fresh per-step fact cache now sees the packages the first step
installed.

Installing packages writes to the root-owned dpkg database, so the requests
are flagged ``sudo=True`` and the driver elevates their commands.

Run it with::

    uv run python examples/modules/apt/apt_packages.py
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

# The packages to ensure, as a tuple -- OperationRequest kwargs must stay
# hashable. Shared by both requests below so they carry identical operation
# kwargs (only the log ``name`` differs).
BASELINE_PACKAGES = ("htop", "tree")

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
    """Install the baseline packages -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request installs whatever is missing; the second carries the
    same arguments and is a guaranteed no-op in the apply phase, because the
    first install is already visible through the fresh per-step fact cache.

    Installing packages writes to the root-owned dpkg database, so both
    requests are flagged ``sudo=True`` and the driver elevates their commands
    over SSH.
    """
    yield op(
        apt.packages,
        name="Install baseline packages",
        packages=BASELINE_PACKAGES,
        sudo=True,
    )
    yield op(
        apt.packages,
        name="Re-install baseline packages (no-op)",
        packages=BASELINE_PACKAGES,
        sudo=True,
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent.
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