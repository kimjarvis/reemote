# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: ensure the docker and lusers system groups exist on two real hosts.

This is a *usage* example, not part of the library. It shows reemote5's
``server.group`` operation, the pyinfra ``server.group`` parity for adding
and removing system groups: the deploy creates the ``docker`` group
(pyinfra's documented example) and a ``lusers`` system group with a fixed
gid, both under ``sudo`` because ``groupadd`` needs root.

The operation is idempotent: it reads the ``Groups`` fact (the group names
in ``/etc/group``) and yields a ``groupadd`` only where the group is absent,
so a host that already has ``docker`` plans no change and a second run is a
no-op everywhere. ``system=True`` renders ``groupadd -r`` and ``gid=1500``
renders ``--gid 1500``; both options only apply at creation -- an existing
group is never modified. The ``Kernel`` fact picks the command form: on a
FreeBSD host the same deploy renders ``pw groupadd -n ...`` (and ``system``
is ignored, BSD having no system groups), every other kernel gets
``groupadd``/``groupdel``. Removing a group is the mirror image:
``present=False`` yields ``groupdel`` only when the group is present.

Every failure mode of the run -- an unreachable host, a rejected elevation,
or a ``groupadd`` that exits non-zero (a gid already taken, for instance) --
reaches ``main`` as a typed error whose message already names the host, so
``main`` catches them and prints one line each instead of a traceback.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``): each plain ``Host``
lazily opens an asyncssh connection, reads ``/etc/group`` and ``uname -s``
for real, and runs ``groupadd`` under sudo where needed.

Run it with::

    uv run python examples/modules/server/server_group.py
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
    server,
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
    """Create the docker group, then the lusers system group with a fixed gid.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases; whether each group already exists is decided inside the
    operation against the freshly gathered ``Groups`` fact, per phase.
    """
    yield op(
        server.group,
        name="Create docker group",
        group="docker",
        sudo=True,
    )
    yield op(
        server.group,
        name="Create lusers system group",
        group="lusers",
        system=True,
        gid=1500,
        sudo=True,
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. The group list is still gathered over SSH
        # (read-only) so the plan reflects each host's real pre-deployment
        # state; nothing is created.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. Step 1
        # creates docker where absent; step 2 creates lusers where absent.
        plans, report, _hosts = await run(INVENTORY, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except HostConnectionError as exc:
        # A host could not be reached over SSH at all. Report it plainly --
        # naming every unreachable host -- instead of dumping an asyncssh
        # traceback, and exit non-zero so the failure is script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except PrivilegeError as exc:
        # The host was reached but rejected the ``sudo=True`` elevation -- a
        # wrong ``sudo_password``, or a run-as user the login user may not
        # become. One line naming the host; the detail stays on ``__cause__``.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except CommandError as exc:
        # A ``groupadd`` invocation exited non-zero (a gid already taken by
        # another group, for instance). The typed message already names the
        # host, the credential-free command and its stderr, so print just that.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())