# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Manage a persistent fcontext rule, then relabel the tree it covers.

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

``selinux.file_context_mapping`` ports pyinfra's operation of the same
name: it manages one *persistent* rule in the local fcontext database --
here ``/web(/.*)?`` labelled ``httpd_sys_content_t``, so a custom web root
and everything under it keeps the httpd type across relabels. With
``present=True`` (the default) it adds the rule (``semanage fcontext -a``)
or changes an existing rule's type (``-m``); ``present=False`` deletes it
(``-d``). Idempotence reads the ``FileContextMapping`` fact: the first two
requests carry identical kwargs, so the prepare phase plans both as changes
when the rule is absent or mistyped, while the apply phase writes on the
first and reports the second as a no-op through the fresh per-step fact
cache.

The third request is the documented remedy for the operation's own
limitation: ``semanage fcontext`` changes the *database*, never the labels
existing files already carry. ``selinux.restorecon`` reapplies the database
to the tree (``restorecon -R /web``), and because it runs *after* the
mapping step in the same deploy, the fresh per-step state sees the new rule
-- the composition the fact-operation pairing table recommends. The ``/web``
tree must exist on the hosts (create it with ``files.directory`` first);
``restorecon`` is stateless and always reports a change, since reemote5 has
no fact that compares a whole tree against the database.

``semanage`` and ``restorecon`` write root-owned policy and labels, so every
request is flagged ``sudo=True`` and the driver elevates its commands over
SSH.

Run it with::

    uv run python examples/modules/selinux/selinux_op_file_context_mapping.py
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

# The persistent rule: a custom web root and everything under it keeps the
# httpd content type. WEB_ROOT is the tree restorecon relabels once the rule
# is in the database.
MAPPING_TARGET = "/web(/.*)?"
CONTENT_TYPE = "httpd_sys_content_t"
WEB_ROOT = "/web"

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
    """Ensure the /web fcontext rule -- twice -- then relabel the tree.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first two requests carry identical kwargs, making the second a
    guaranteed no-op in the apply phase; the third relabels /web against the
    database the first step just wrote.

    ``semanage fcontext`` and ``restorecon`` write root-owned state, so all
    requests are flagged ``sudo=True`` and the driver elevates their
    commands over SSH.
    """
    yield op(
        selinux.file_context_mapping,
        name="Map /web to httpd_sys_content_t",
        target=MAPPING_TARGET,
        se_type=CONTENT_TYPE,
        sudo=True,
    )
    yield op(
        selinux.file_context_mapping,
        name="Re-map /web (no-op)",
        target=MAPPING_TARGET,
        se_type=CONTENT_TYPE,
        sudo=True,
    )
    yield op(
        selinux.restorecon,
        name="Relabel /web from the database",
        path=WEB_ROOT,
        recurse=True,
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