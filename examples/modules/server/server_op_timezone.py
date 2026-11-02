# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: set the system timezone on two real hosts (systemd only).

This is a *usage* example, not part of the library. It shows reemote5's
``server.timezone`` operation, the pyinfra ``server.timezone`` parity for
the system timezone: the deploy ensures every host keeps ``Europe/Amsterdam``
(pyinfra's documented example), under ``sudo`` because ``timedatectl``
needs root.

The operation is idempotent: it reads the ``Timezone`` fact
(``readlink -f /etc/localtime``) and yields a command only where the
current zone differs from the request, so a host already on the right zone
plans no change and a second run is a no-op everywhere. It is supported on
**systemd systems only**: when a change is needed the ``Which`` fact probes
``timedatectl``, and the command it yields -- ``timedatectl set-timezone``
-- also persists the zone across reboots. A host without ``timedatectl``
raises ``OperationError`` during the prepare phase, before anything is
sent; pyinfra's non-systemd fallback (symlinking ``/etc/localtime`` and
writing ``/etc/timezone``) is deliberately dropped, so there is no silent
edit behind systemd's back. An already-set zone stays a no-op on any host,
like pyinfra, which also compares the fact before probing for the tool.

Every failure mode of the run -- an unreachable host, a rejected elevation,
a ``timedatectl`` that exits non-zero (an unknown zone, for instance), or a
non-systemd host missing the tool -- reaches ``main`` as a typed error whose
message already names the host, so ``main`` catches them and prints one line
each instead of a traceback.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``): each plain ``Host``
lazily opens an asyncssh connection, resolves ``/etc/localtime`` and probes
``command -v timedatectl`` for real, and sets the zone under sudo where
needed.

Run it with::

    uv run python examples/modules/server/server_op_timezone.py
"""

import asyncio
import sys
from collections.abc import Generator

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    OperationError,
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
    """Ensure every host keeps Europe/Amsterdam, under sudo.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases; whether the zone is already set -- and whether the host
    has timedatectl at all -- is decided inside the operation against the
    freshly gathered facts, per phase.
    """
    yield op(
        server.timezone,
        name="Set timezone",
        timezone="Europe/Amsterdam",
        sudo=True,
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. The timezone and the timedatectl probe
        # are still gathered over SSH (read-only) so the plan reflects each
        # host's real pre-deployment state; nothing is set.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. Each host
        # gets its zone set where it differs; a second run plans no change.
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
    except OperationError as exc:
        # A host needs a zone change but has no timedatectl (not systemd).
        # The prepare phase aborted before anything was sent; the message
        # names the host and the missing prerequisite.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except PrivilegeError as exc:
        # The host was reached but rejected the ``sudo=True`` elevation -- a
        # wrong ``sudo_password``, or a run-as user the login user may not
        # become. One line naming the host; the detail stays on ``__cause__``.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except CommandError as exc:
        # A ``timedatectl set-timezone`` invocation exited non-zero (an
        # unknown zone, for instance). The typed message already names the
        # host, the credential-free command and its stderr, so print just
        # that.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())