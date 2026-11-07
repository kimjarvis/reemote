# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Map a Linux login to a confined SELinux user with ``selinux.login``.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key). The hosts must run SELinux with
   ``policycoreutils-python-utils`` (semanage) installed; point
   ``INVENTORY`` at your own machines to target them.
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

``selinux.login`` is a reemote5 original (pyinfra ships no login
operation): it manages the Linux-user -> SELinux-user mapping, the core of
least-privilege confined-user management. Here the login ``kjarvis`` is
mapped to ``staff_u`` with range ``s0`` -- the confined-administrator user
whose roles (``staff_r``, ``sysadm_r``) the ``SEUser`` fact reports -- so
the account stops landing in the unconfined domain the ``__default__``
fallback assigns. With ``present=True`` (the default) an unmapped login is
added (``semanage login -a``) and an existing mapping is modified in place
(``-m``) when its user or range differs; ``present=False`` removes the
mapping (``-d``), returning the login to the fallback.

Idempotence reads the ``SELogin`` fact and compares both the SELinux user
*and* the MLS/MCS range (the Service column newer semanage releases print
is ignored -- the operation never manages it). The script yields the *same*
request twice: the prepare phase plans both as changes when the mapping is
absent or different, while the apply phase writes on the first and reports
the second as a no-op through the fresh per-step fact cache. The mapping
takes effect at the user's *next* login -- existing sessions keep the
context they started with.

``semanage login`` writes the root-owned policy store, so both requests are
flagged ``sudo=True`` and the driver elevates their commands over SSH.

Run it with::

    uv run python examples/modules/selinux/selinux_login.py
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

# The mapping to ensure: the login user confined to staff_u at range s0.
# Shared by both requests below so they carry identical operation kwargs
# (only the log ``name`` differs), which makes the second a guaranteed
# no-op in the apply phase.
LOGIN_USER = "kjarvis"
LOGIN_SEUSER = "staff_u"
LOGIN_RANGE = "s0"

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
    """Map kjarvis to staff_u -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request adds (or modifies) the mapping; the second carries
    the same arguments and is a guaranteed no-op in the apply phase,
    because the first mapping is already visible through the fresh
    per-step fact cache.

    ``semanage login`` writes the policy store, so both requests are
    flagged ``sudo=True`` and the driver elevates their command over SSH.
    """
    yield op(
        selinux.login,
        name="Map kjarvis to staff_u",
        user=LOGIN_USER,
        seuser=LOGIN_SEUSER,
        mls_range=LOGIN_RANGE,
        sudo=True,
    )
    yield op(
        selinux.login,
        name="Re-map kjarvis to staff_u (no-op)",
        user=LOGIN_USER,
        seuser=LOGIN_SEUSER,
        mls_range=LOGIN_RANGE,
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