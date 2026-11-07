# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Switch the SELinux runtime mode with ``selinux.mode``.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key). The hosts must run SELinux (RHEL-family);
   point ``INVENTORY`` at your own machines to target them.
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

``selinux.mode`` is a reemote5 original (pyinfra ships no mode operation):
it sets the *runtime* enforcing state with ``setenforce`` -- here
``enforcing``, the secure default; ``permissive`` logs denials without
enforcing them, the troubleshooting half-step. The operation is idempotent
on the ``Mode`` fact: it compares the runtime mode and yields a
``setenforce`` only when it differs. The script yields the *same* request
twice: on a permissive host the prepare phase plans both as changes, while
the apply phase switches on the first and reports the second as a no-op
through the fresh per-step fact cache; on an already-enforcing host both
are no-ops in both phases.

Two boundaries are deliberate. The runtime switch does **not** touch
``/etc/selinux/config`` -- the boot-time mode is a file edit, managed by the
``files.edit`` spool-and-install recipe in
``examples/modules/selinux/selinux_config.py``. And a *disabled* subsystem
cannot be enabled at runtime at all: ``setenforce`` refuses, so the
operation raises a ``ValueError`` pointing at that recipe and at
``selinux.autorelabel`` (a disabled-to-enforcing transition needs a full
relabel and a reboot). The ``Mode`` fact's ``config_mode`` field is how a
deploy tells "enforcing now but permissive after reboot" apart from a fully
enforcing host before deciding to run this operation.

``setenforce`` writes kernel state, so both requests are flagged
``sudo=True`` and the driver elevates their commands over SSH.

Run it with::

    uv run python examples/modules/selinux/selinux_op_mode.py
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
    format_log,
    format_plan,
    op,
    run,
    selinux,
)

# The runtime mode to ensure. Shared by both requests below so they carry
# identical operation kwargs (only the log ``name`` differs), which makes
# the second a guaranteed no-op in the apply phase.
TARGET_MODE = "enforcing"

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
    """Ensure enforcing mode -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request switches the runtime mode if it differs; the second
    carries the same arguments and is a guaranteed no-op in the apply
    phase, because the first switch is already visible through the fresh
    per-step fact cache.

    ``setenforce`` writes kernel state, so both requests are flagged
    ``sudo=True`` and the driver elevates their command over SSH.
    """
    yield op(
        selinux.mode,
        name="Ensure SELinux enforcing",
        mode=TARGET_MODE,
        sudo=True,
    )
    yield op(
        selinux.mode,
        name="Re-ensure SELinux enforcing (no-op)",
        mode=TARGET_MODE,
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