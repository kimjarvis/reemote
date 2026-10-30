"""Update every dnf package on the hosts, non-idempotently.

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

``dnf.update`` runs ``dnf update -y`` over every installed package, bringing
the whole system up to date. Like pyinfra's ``dnf.update`` it is
**non-idempotent**: it reads no fact -- there is no cheap way to tell whether
any package actually has a pending upgrade -- so it always reports a change
and always runs, and dnf is itself a no-op when the system is already up to
date. That is why this script yields the request once rather than twice for an
idempotence demonstration: there is no state to converge on, so the dry-run
plan and the apply log report the very same single change.

Updating the whole system needs root, so the request is flagged ``sudo=True``
and the driver elevates its ``dnf update -y`` command (rendered in the apply
log as ``sudo dnf update -y``).

Run it with::

    uv run python examples/modules/dnf/dnf_update.py
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
    dnf,
    format_log,
    format_plan,
    op,
    run,
)

# The inventory: real hosts and their SSH connection information, supplied
# by the application to the library. The default host_factory materializes
# each entry into a plain Host that connects over asyncssh.
INVENTORY = [
    {
        "host": "web1",
        "connection": {
            "address": "192.168.0.240",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
    {
        "host": "web2",
        "connection": {
            "address": "192.168.0.242",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
]


def deploy(host: Host) -> Generator[OperationRequest]:
    """Update every package on the host.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    ``dnf.update`` reads no fact, so this single request is planned -- and
    applied -- as a change on every run regardless of the host's state.

    Updating the whole system needs root, so the request is flagged
    ``sudo=True`` and the driver elevates its ``dnf update -y`` command over
    SSH.
    """
    yield op(dnf.update, name="Update all packages", sudo=True)


async def main() -> None:
    try:
        # --dry: prepare phase only. The plan is materialized against each
        # host's real state, but no command that mutates a host is sent.
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
