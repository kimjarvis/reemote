# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Carve a per-domain SELinux exception with ``selinux.permissive_domain``.

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

``selinux.permissive_domain`` is a reemote5 original (pyinfra ships no
permissive-domain operation): it adds or removes one domain from the
permissive list -- here ``httpd_t``, the web server's domain -- so its
denials are logged but not enforced while the rest of the system stays
enforcing. When troubleshooting a service this is the safe alternative to
``selinux.mode(mode="permissive")``, which disables enforcement
*everywhere*: the exception is one domain wide, persistent (it lives in
the policy store, not the runtime mode), and auditable through the
``PermissiveDomains`` fact.

With ``present=True`` (the default) an enforcing domain is added
(``semanage permissive -a``); ``present=False`` removes it (``-d``),
returning the domain to full enforcement. Idempotence reads the
``PermissiveDomains`` fact: the first two requests carry identical kwargs,
so the prepare phase plans both as changes when the domain is enforcing,
while the apply phase adds on the first and reports the second as a no-op
through the fresh per-step fact cache. The third request removes the
exception again, showing the bidirectional pair -- leave it out (or keep
only the first request) to keep the domain permissive while you work.

``semanage permissive`` writes the root-owned policy store, so every
request is flagged ``sudo=True`` and the driver elevates its commands over
SSH.

Run it with::

    uv run python examples/modules/selinux/selinux_permissive_domain.py
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

# The domain to exempt while troubleshooting: the web server's.
PERMISSIVE_DOMAIN = "httpd_t"

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
    """Make httpd_t permissive -- twice -- then enforce it again.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first two requests carry identical kwargs, making the second a
    guaranteed no-op in the apply phase; the third removes the exception,
    demonstrating the present=False direction (in the dry-run plan it is a
    no-op when the domain started enforcing).

    ``semanage permissive`` writes the policy store, so all requests are
    flagged ``sudo=True`` and the driver elevates their commands over SSH.
    """
    yield op(
        selinux.permissive_domain,
        name="Make httpd_t permissive",
        domain=PERMISSIVE_DOMAIN,
        sudo=True,
    )
    yield op(
        selinux.permissive_domain,
        name="Re-make httpd_t permissive (no-op)",
        domain=PERMISSIVE_DOMAIN,
        sudo=True,
    )
    yield op(
        selinux.permissive_domain,
        name="Enforce httpd_t again",
        domain=PERMISSIVE_DOMAIN,
        present=False,
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