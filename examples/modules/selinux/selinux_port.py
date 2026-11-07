# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Label a network port for SELinux, with add/modify/delete idempotence.

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

``selinux.port`` ports pyinfra's operation of the same name and adds the
*modify* capability the port proposal asked for: it labels tcp/8080
``http_port_t`` so httpd may bind the alternate port. With ``present=True``
(the default) an unlabelled port is added (``semanage port -a``) and a port
already carrying a *different* type is modified in place (``-m``) -- the
update scenario pyinfra's add/remove-only operation could not express;
``present=False`` deletes the label (``-d``).

Idempotence dispatches on tooling: when ``sepolicy`` is installed the
operation compares the ``SEPort`` fact (the port's specific label), and
otherwise falls back to the ``SEPorts`` table (``semanage port -ln``) --
both answer "what type does this port carry now", so the check-before-
executing holds either way. The script yields the *same* request twice:
the prepare phase plans both as changes when the label is absent (nothing
is sent in prepare), while the apply phase labels on the first and reports
the second as a no-op through the fresh per-step fact cache.

``semanage port`` writes the root-owned policy store, so both requests are
flagged ``sudo=True`` and the driver elevates their commands over SSH.

Run it with::

    uv run python examples/modules/selinux/selinux_port.py
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

# The port to label: httpd's alternate listening port. Shared by both
# requests below so they carry identical operation kwargs (only the log
# ``name`` differs), which makes the second a guaranteed no-op in apply.
PORT_PROTOCOL = "tcp"
PORT_NUMBER = 8080
PORT_TYPE = "http_port_t"

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
    """Label tcp/8080 http_port_t -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request adds (or modifies) the label; the second carries the
    same arguments and is a guaranteed no-op in the apply phase, because
    the first label is already visible through the fresh per-step fact
    cache.

    ``semanage port`` writes the policy store, so both requests are flagged
    ``sudo=True`` and the driver elevates their command over SSH.
    """
    yield op(
        selinux.port,
        name="Label tcp/8080 http_port_t",
        protocol=PORT_PROTOCOL,
        port_num=PORT_NUMBER,
        se_type=PORT_TYPE,
        sudo=True,
    )
    yield op(
        selinux.port,
        name="Re-label tcp/8080 (no-op)",
        protocol=PORT_PROTOCOL,
        port_num=PORT_NUMBER,
        se_type=PORT_TYPE,
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