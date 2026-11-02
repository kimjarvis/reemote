# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Enable a dnf module stream, with stream idempotence.

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

``dnf.module`` manages a dnf *application stream*: with ``enabled=True`` (the
default) it enables ``module:stream`` (``dnf module enable -y <module>:<stream>``),
and with ``enabled=False`` it disables the module outright (``dnf module
disable -y <module>``), making every one of its streams inactive. Application
streams let a host install one of several parallel versions of a component --
here the PostgreSQL 16 stream, matching pyinfra's documented example.

The operation is idempotent: enabling reads the ``DnfEnabledModules`` fact (a
name-to-stream map) and is a no-op when the module is already enabled on exactly
the requested stream -- re-enabling onto a new stream when it currently sits on
a different one -- while disabling reads ``DnfDisabledModules``. The script
yields the *same* enable request twice to show that idempotence across the two
phases: the prepare phase plans both as changes (nothing is enabled yet, so both
compare against the same pre-deployment state), while the apply phase enables on
the first and reports the second as a no-op, because the fresh per-step fact
cache now sees the stream the first step enabled.

Enabling a stream writes dnf's module state under ``/etc`` and the rpm
database, so the request is flagged ``sudo=True`` and the driver elevates its
``dnf module enable`` command. Note pyinfra's stream-switching caveat, inherited
unchanged: ``dnf module enable`` refuses to switch streams while packages from
the currently-active stream are installed, so remove those packages (or run
``dnf module reset``) before switching.

Run it with::

    uv run python examples/modules/dnf/dnf_module.py
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

# The module stream to enable. Shared by both requests below so they carry
# identical operation kwargs (only the log ``name`` differs), which is what
# makes the second a guaranteed no-op in the apply phase.
POSTGRESQL_MODULE = "postgresql"
POSTGRESQL_STREAM = "16"

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
    """Enable the PostgreSQL 16 stream -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request enables the stream; the second carries the same
    arguments and is a guaranteed no-op in the apply phase, because the
    first enable is already visible through the fresh per-step fact cache.

    Enabling a stream writes dnf's module state, so both requests are
    flagged ``sudo=True`` and the driver elevates their command over SSH.
    """
    yield op(
        dnf.module,
        name="Enable PostgreSQL 16 stream",
        module=POSTGRESQL_MODULE,
        stream=POSTGRESQL_STREAM,
        sudo=True,
    )
    yield op(
        dnf.module,
        name="Re-enable PostgreSQL 16 stream (no-op)",
        module=POSTGRESQL_MODULE,
        stream=POSTGRESQL_STREAM,
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
