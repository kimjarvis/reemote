# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: start httpd on two real hosts, then wait until port 80 is listening.

This is a *usage* example, not part of the library. It shows the port half of
reemote5's second kind of **non-shell** operation: ``server.wait`` runs no
command string, it yields a ``WaitForFactCommand`` that the driver *polls* --
re-gathering the ``Port`` fact over each host's asyncssh connection every
``interval`` seconds until the ``is_listening`` predicate holds or ``timeout``
expires (raising ``WaitTimeoutError`` and aborting the run).

``server.wait`` mirrors pyinfra's ``server.wait(port=...)``, the only option
upstream has. pyinfra polls remotely: a ``while true`` shell loop over
``netstat`` with a fixed one second sleep and *no* timeout at all, so a port
that never opens hangs the run forever. Here the wait is described, not
scripted: the driver polls the fact and ``timeout`` bounds it, and ``interval``
is adjustable. The default interval is 1s rather than ``wait_service``'s 5s --
a port on its way up usually appears within a second, where a unit's start
takes longer.

The deploy is the two-step shape from pyinfra's ``systemd.service`` +
``server.wait`` idiom, and it pairs with
``examples/modules/server/wait_for_httpd.py``: same start, two different
readiness questions. ``server.wait_service`` there asks systemd whether the
unit reports ``active``; this asks the kernel's socket table whether anything
is *listening* -- which is the stronger claim, since a unit can be active
while its service is still binding, and a non-systemd service opens its port
all the same. ``server.service`` is the idempotent half: it reads the unit's
state and yields ``systemctl start`` only when the unit differs from the
request (so the second run plans no change). It needs root, hence
``sudo=True``. ``server.wait`` is the non-idempotent half: like every wait it
reads no facts and is always planned as a change -- whether the port is
*already* open is discovered at apply time by the driver's poll loop, never at
plan time. Deciding at plan time would read pre-deployment state in the prepare
phase and post-start state in the apply phase, and branching on that would trip
the determinism guard.

The wait itself is unprivileged (``ss -ltn`` works as the login user), so it
carries no ``sudo`` flag -- and that is deliberate, because it also decides
*what* the fact reports. An unprivileged listing shows the socket but not the
process owning it, so ``Port`` keeps presence in its own ``listening`` flag
instead of inferring it from an owner, and ``is_listening`` reads that flag
alone. A port owned by a daemon the login user cannot inspect still satisfies
the wait. A bad port or protocol (outside 1-65535, or not ``tcp``/``udp``) is
rejected by ``ValueError``/``TypeError`` at plan time, before anything is sent.

Every failure mode of the run -- an unreachable host, a rejected elevation, a
``systemctl`` that exits non-zero, or a wait that times out -- reaches ``main``
as a typed error whose message already names the host, so ``main`` catches them
and prints one line each instead of a traceback.

Because the apply phase runs each step on every host concurrently and joins
before the next one, both hosts have *finished* ``systemctl start`` before
either begins polling, and the two poll loops then run in parallel; a slow
host only delays the step barrier, not the other host's polling.

The example assumes ``httpd`` is installed on the hosts (the dnf base example
``examples/modules/dnf/dnf_packages.py`` installs it).

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory``, seeds its ``listening_ports`` seam and never
touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection, reads the unit state for real, runs ``systemctl`` under sudo, and
polls the socket table until port 80 answers.

Run it with::

    uv run python examples/modules/server/server_op_wait.py
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
    WaitTimeoutError,
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
    """Start httpd, then wait until its port answers connections.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases. Only the service operation is elevated; the wait polls as
    the login user.
    """
    yield op(
        server.service,
        name="Start httpd",
        service="httpd",
        running=True,
        sudo=True,
    )
    # The wait takes the port positionally (pyinfra's only option) plus the
    # two bounds it does not have: "udp" would be the other protocol family,
    # and an already-open port is satisfied by the very first poll -- a zero
    # timeout still polls once.
    yield op(
        server.wait,
        name="Wait for httpd to listen on port 80",
        port=80,
        timeout=45.0,
        interval=1.0,
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. The unit state is still gathered over SSH
        # (read-only) so the plan reflects each host's real pre-deployment
        # state; nothing is started and nothing is polled -- the wait is simply
        # planned, always as a change, and its loggable form names the fact the
        # apply phase will poll.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. Step 1 starts
        # httpd where needed; step 2 polls each host's socket table until port
        # 80 is listening (or raises WaitTimeoutError after 45s).
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
        # A ``systemctl`` invocation exited non-zero (an unknown or masked
        # unit, for instance). The typed message already names the host, the
        # credential-free command and its stderr, so print just that.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except WaitTimeoutError as exc:
        # Port 80 never started listening within the timeout. The message
        # carries the host, the wait descriptor and the last polled ``Port``
        # record -- including the ``listening`` flag that stayed false --
        # which is everything worth printing. No traceback.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
