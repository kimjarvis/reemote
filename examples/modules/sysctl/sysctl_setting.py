# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Ensure one kernel parameter with ``sysctl.setting`` -- runtime and disk.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information.
2. Build the deploy script as a sync, branch-free generator yielding
   ``OperationRequest`` objects via ``op``.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``), opening real asyncssh
connections that ``run`` closes when it finishes.

``sysctl.setting`` is a reemote5 original superseding pyinfra's basic
``server.sysctl``: two independent idempotent halves, each gated by its own
fact. The **runtime half** compares the desired value against the ``Sysctl``
fact (pyinfra's normalization: digits compare as ints, a multi-word value as
a list) and applies ``sysctl -w`` only on a difference; ``sysctl_set=False``
skips it. The **persist half** performs a keyed line replacement in
``sysctl_file`` (default ``/etc/sysctl.conf``) -- replace the key's line in
place, append when absent, never touch comments -- gated by the
``SysctlFile`` content fact; ``persist=False`` skips it and ``present=False``
removes the key's lines instead (the runtime value is left alone). A changed
write is followed by a scoped ``sysctl -p <sysctl_file>`` reload unless
``reload=False``, and ``ignore_errors=True`` guards the runtime command with
``|| true`` for cross-platform key sets.

The write follows the ``systemd.dropin`` spool pattern: an **unprivileged**
scp transfer spools the edited text to a fixed ``/tmp`` path, then an
**elevated** ``sh -c`` install copies it into place ``0644`` and removes the
spool. Only the shell steps are escalated -- the transfer itself never is --
so the request is flagged ``sudo=True`` while the file lands root-owned.

The script yields the *same* request twice to show idempotence across the two
phases: the prepare phase plans both as changes when the setting is absent
(nothing is sent in prepare), while the apply phase converges on the first and
reports the second as a no-op, because the fresh per-step fact cache now sees
the value the first step applied.

**This operation tunes the running kernel and writes root-owned
configuration.** It is compile-checked and exercised by the offline example
harness rather than run against shared hosts; point ``INVENTORY`` at your own
throwaway machines to execute it for real.

Run it with::

    uv run python examples/modules/sysctl/sysctl_setting.py
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
    sysctl,
)

# The parameter and value to ensure. A multi-word value like "1024 65535"
# (net.ipv4.ip_local_port_range) is the op()-safe spelling of pyinfra's list:
# request kwargs must be hashable, and the string normalizes identically.
KEY = "vm.swappiness"
VALUE = 10

# The inventory: real hosts and their SSH connection information, supplied by
# the application to the library. Host.from_inventory materializes each entry
# into a plain Host that connects over asyncssh.
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
    """Ensure vm.swappiness -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases; the operation's own fact comparisons decide what changes.
    """
    yield op(
        sysctl.setting,
        name="Tune vm.swappiness",
        key=KEY,
        value=VALUE,
        sudo=True,
    )
    yield op(
        sysctl.setting,
        name="Re-ensure vm.swappiness (no-op)",
        key=KEY,
        value=VALUE,
        sudo=True,
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. Facts are gathered over SSH (read-only) so
        # the plan reflects each host's real state; no mutating command is sent.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
        plans, report, _hosts = await run(INVENTORY, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())