# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Relabel a file's SELinux context components with ``selinux.file_context``.

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

``selinux.file_context`` ports pyinfra's operation of the same name and
extends it to the *full* context: pyinfra only sets the type (``chcon -t``),
while here each of ``se_type``, ``se_user``, ``se_role`` and ``se_level`` is
optional, at least one must be given, and only the components that differ
from the ``FileContext`` fact are relabelled. The first two requests are
pyinfra's exact scenario -- label httpd's index page ``httpd_sys_content_t``
so the daemon may serve it -- yielded twice to show idempotence: the prepare
phase plans both as changes when the type differs (nothing is sent in
prepare), while the apply phase relabels on the first and reports the second
as a no-op through the fresh per-step fact cache. The third request asks for
the whole context on the same file; after the first request the remaining
components already match the stock ``system_u:object_r:...:s0`` label, so it
demonstrates the component-wise no-op ("already had the requested context").

``chcon`` changes the *runtime* label of existing files only -- a relabel
(``restorecon`` or a boot-time autorelabel) reverts it to whatever the
persistent fcontext database says. The durable rule is
``selinux.file_context_mapping``'s job
(``examples/modules/selinux/selinux_op_file_context_mapping.py``). Relabelling
a root-owned system path needs root, so every request is flagged ``sudo=True``
and the driver elevates its ``chcon`` command over SSH.

Run it with::

    uv run python examples/modules/selinux/selinux_op_file_context.py
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

# The file to relabel and the type httpd may serve. The file must exist --
# chcon relabels existing paths only (create it with files.file or files.put
# first).
CONTEXT_PATH = "/var/www/html/index.html"
CONTENT_TYPE = "httpd_sys_content_t"

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
    """Set the index page's type -- twice -- then ask for its full context.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first two requests carry identical kwargs, making the second a
    guaranteed no-op in the apply phase; the third names all four context
    components and is a no-op once the stock user/role/level already match.

    Relabelling a root-owned path needs root, so all requests are flagged
    ``sudo=True`` and the driver elevates their ``chcon`` commands.
    """
    yield op(
        selinux.file_context,
        name="Label index.html httpd_sys_content_t",
        path=CONTEXT_PATH,
        se_type=CONTENT_TYPE,
        sudo=True,
    )
    yield op(
        selinux.file_context,
        name="Re-label index.html (no-op)",
        path=CONTEXT_PATH,
        se_type=CONTENT_TYPE,
        sudo=True,
    )
    yield op(
        selinux.file_context,
        name="Ensure the full context of index.html",
        path=CONTEXT_PATH,
        se_type=CONTENT_TYPE,
        se_user="system_u",
        se_role="object_r",
        se_level="s0",
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