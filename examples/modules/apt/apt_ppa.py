# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Add an Ubuntu PPA repository with ``apt-add-repository``.

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

``apt.ppa`` adds (or with ``present=False`` removes) an Ubuntu PPA repository,
mirroring pyinfra's ``apt.ppa``. ``src`` is the full ``ppa:user/repo`` name --
here the Bitcoin PPA, pyinfra's documented example. The operation runs
``apt-add-repository -y <src>`` (``--remove`` for ``present=False``), which
requires ``software-properties-common`` on the target host.

The operation is **non-idempotent**, exactly as pyinfra marks it: it reads no
fact and always reports a change, yielding one command whether or not the PPA
is already configured. That is why this script yields the request only once --
unlike the idempotent ``apt.deb``/``apt.packages`` examples there is no state
for a second request to converge on, so a repeat would simply plan and run the
same command again (``apt-add-repository`` itself is idempotent on the host, so
re-adding an existing PPA is harmless). Both the prepare plan and the apply log
therefore show the request as a change.

Adding a repository writes to the root-owned apt sources, so the request is
flagged ``sudo=True`` and the driver elevates its command over SSH.

Run it with::

    uv run python examples/modules/apt/apt_ppa.py
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

# The PPA to add, in full ``ppa:user/repo`` format (pyinfra's documented
# example). Assumes software-properties-common is installed on the host.
BITCOIN_PPA = "ppa:bitcoin/bitcoin"

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
    """Add the Bitcoin PPA.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The operation is non-idempotent, so the single request plans and applies
    as a change; adding a repository writes to the root-owned apt sources, so
    it is flagged ``sudo=True`` and the driver elevates its command over SSH.
    """
    yield op(apt.ppa, name="Add the Bitcoin ppa", src=BITCOIN_PPA, sudo=True)


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