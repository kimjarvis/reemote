# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Manage an ipset and its entries with ``firewalld.ipset``.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key). The hosts must run firewalld (RHEL-family);
   point ``INVENTORY`` at your own machines to target them.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``). Each plain ``Host``
lazily opens an asyncssh connection from its ``connection`` data, gathers
facts and runs commands for real, and ``run`` closes those connections when
it finishes. ``run`` is a coroutine, so ``main`` is async and driven by
``asyncio.run``.

``firewalld.ipset`` is a reemote5 original (pyinfra ships no firewalld
module): it manages a named IP set -- here ``blocked``, a ``hash:ip``
set holding two addresses -- the dynamic block/allow list a zone rule
references as ``ipset:blocked`` (bind it with ``firewalld.source``).
Existence is permanent-only (``--new-ipset`` has no runtime form; the
runtime copy appears after a reload), while entries dual-apply with the
default ``permanent=True``. Entries are additive: the operation ensures
each listed entry is present and never removes unlisted ones, so an
intrusion responder's runtime additions survive a config-management
run. An existing set whose ``type`` differs from ``ipset_type`` raises
``ValueError`` rather than silently rebuilding it. The entry tuples are
module-level constants: ``op()`` requires hashable arguments, so lists
are rejected by design. The script yields the *same* request twice: on
a host without the set the prepare phase plans both as changes, while
the apply phase creates it on the first and reports the second as a
no-op through the fresh per-step fact cache.

``firewall-cmd`` writes daemon state, so both requests are flagged
``sudo=True`` and the driver elevates their commands over SSH.

Run it with::

    uv run python examples/modules/firewalld/firewalld_ipset.py
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
    firewalld,
    format_log,
    format_plan,
    op,
    run,
)

# The ipset to ensure. Shared by both requests below so they carry
# identical operation kwargs (only the log ``name`` differs), which makes
# the second a guaranteed no-op in the apply phase. Tuples, not lists:
# op() arguments must be hashable.
IPSET_NAME = "blocked"
IPSET_TYPE = "hash:ip"
ENTRIES = ("203.0.113.50", "198.51.100.25")

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
    """Ensure the blocked ipset -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request creates the set (permanent) and adds the entries to
    the scopes where the set exists; the second carries the same
    arguments and is a guaranteed no-op in the apply phase, because the
    first change is already visible through the fresh per-step fact
    cache.

    ``firewall-cmd`` writes daemon state, so both requests are flagged
    ``sudo=True`` and the driver elevates their commands over SSH.
    """
    yield op(
        firewalld.ipset,
        name="Ensure blocked ipset",
        ipset_name=IPSET_NAME,
        ipset_type=IPSET_TYPE,
        entries=ENTRIES,
        sudo=True,
    )
    yield op(
        firewalld.ipset,
        name="Re-ensure blocked ipset (no-op)",
        ipset_name=IPSET_NAME,
        ipset_type=IPSET_TYPE,
        entries=ENTRIES,
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