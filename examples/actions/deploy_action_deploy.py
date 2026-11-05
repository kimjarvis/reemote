# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: a deploy, then a bespoke action, then a second deploy.

This is a *usage* example, not part of the library. It is the action analogue
of ``examples/general/two_deploy_split.py`` and shows where actions belong:
BEFORE, BETWEEN and AFTER deploys, never inside one. The scenario is "install
nginx, probe it with a bespoke command, then configure it only where the probe
succeeded":

1. ``install_nginx`` -- a branch-free deploy that only installs the package.
   Both phases trivially yield the same single request.
2. An ACTION between the runs: ``gather_action(hosts, Shell, command="nginx
   -v")`` runs a command the library knows nothing about and returns a
   ``{host.name: Result}`` mapping. ``nginx -v`` prints its version to stderr
   and exits 0 where nginx is installed (127 where it is not), so each
   ``Result``'s ``exit_status`` reports whether the install actually put nginx
   on the box.
   The apply-phase join of run 1 is the barrier: every host has finished
   installing before any host's probe runs; the awaited ``gather_action`` is
   itself a barrier, so every probe has finished before run 2 is built.
3. ``build_configure_nginx(probe)`` -- a factory closing over that frozen
   ``Result`` dict, returning the second deploy. Its ``if`` consults
   ``probe[host.name]["exit_status"] == 0`` -- an ordinary lookup on a snapshot
   taken BEFORE this deploy's prepare phase -- so the generator is a pure
   function of static input: prepare and apply resolve the branch identically
   and the determinism guard holds.

This is exactly the two-deploy split, with an action ``Result`` playing the
role the ``DnfPackages`` fact snapshot plays there. The action is the escape
hatch for a probe no fact models; because its outcome is frozen into a dict
between deploys, branching on it is determinism-safe. Branching on a LIVE
action inside a deploy is both illegal (the in-deploy guard raises
``ActionInDeployError``) and impossible (a sync generator cannot await).

The configure step stages the config with ``files.put`` to ``/tmp``: transfers
ride the connection's scp channel as the login user and are never elevated.
The put source is a module-level ``io.StringIO`` -- one stable identity across
both phases, as ``OperationRequest`` compares a stream by identity.

Connection lifecycle: ``run`` closes the hosts it owns, and ``gather_action``
re-connects them for the probe then closes them again (each action call
re-connects). Run 2 builds fresh hosts from the same inventory via the default
factory, so no manual ``aclose`` is needed anywhere in this script.

Run it with::

    uv run python examples/actions/deploy_action_deploy.py
"""

import asyncio
import io
import sys
from collections.abc import Callable, Generator

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    OperationRequest,
    PrivilegeError,
    Result,
    Shell,
    dnf,
    files,
    format_log,
    format_plan,
    gather_action,
    op,
    run,
)

# The config payload, created once on the control machine: a stream, never a
# local path. The same object is passed to put in every phase, so the prepare
# and apply request sequences compare equal.
NGINX_CONF = io.StringIO(
    "# Managed by reemote5 -- staged by the deploy/action/deploy example.\n"
    "worker_processes auto;\n"
)

# Staging destination under /tmp: the unprivileged scp transfer can write it
# as the login user. Moving it into /etc/nginx would need an elevated op.
STAGED_CONF = "/tmp/nginx.conf"

# Same real-host inventory as two_deploy_split.py; the default host_factory
# materializes each entry into a plain Host connecting over asyncssh.
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


def install_nginx(host: Host) -> Generator[OperationRequest]:
    """Deploy 1: install only, branch-free, so both phases yield one request."""
    yield op(dnf.packages, name="Install nginx", packages=("nginx",), sudo=True)


def build_configure_nginx(
    probe: dict[str, Result],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 2 factory: close over the frozen post-install action Results.

    The returned generator branches on ``probe[host.name]["exit_status"] == 0``
    -- an ordinary dict lookup on a snapshot taken BEFORE this deploy's prepare
    phase -- so both phases resolve the branch identically. Running the action
    live here instead would trip the in-deploy guard (and could not be awaited
    in a sync generator anyway).
    """

    def configure_nginx(host: Host) -> Generator[OperationRequest]:
        if probe[host.name]["exit_status"] == 0:
            yield op(
                files.put,
                name="Stage nginx.conf",
                src=NGINX_CONF,
                dest=STAGED_CONF,
            )

    return configure_nginx


async def main() -> None:
    try:
        # Run 1: prepare + apply the install. Its apply-phase join is the
        # barrier guaranteeing every host finished before the probe below.
        plans, report, hosts = await run(INVENTORY, install_nginx)
        print("== Deploy 1: install nginx ==")
        print(format_plan(plans))
        assert report is not None
        print(format_log(report))

        # The ACTION between deploys: a bespoke probe no fact models.
        # gather_action re-connects the (closed) hosts, runs `nginx -v` on all
        # of them concurrently, returns {host.name: Result}, and closes them
        # again. The await is the barrier before run 2 is built.
        probe = await gather_action(hosts, Shell, command="nginx -v")
        print("\n== Between deploys: action probe (nginx -v) ==")
        for name, result in probe.items():
            # nginx -v writes its version to stderr; ok is exit_status == 0.
            version = result["stderr"].strip() or "(absent)"
            print(f"{name}: ok={result['exit_status'] == 0} {version}")

        # Build deploy 2 ONCE, before either of its phases: both the dry run
        # and the real run use the very same callable closing over the same
        # frozen Result dict -- determinism by construction.
        configure = build_configure_nginx(probe)

        # --dry for deploy 2: the plan is honest because the branch consults
        # the post-install probe, not pre-deployment state.
        plans, _, _hosts = await run(INVENTORY, configure, dry=True)
        print("\n== Deploy 2 plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run 2: configure the hosts whose probe shows nginx present.
        plans, report, _hosts = await run(INVENTORY, configure)
        print("\n== Deploy 2 run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library reports every host-level failure as a typed error whose
        # message names the host and the failing command. Print that message
        # -- no traceback -- and exit non-zero so the failure stays
        # script-visible. (A bespoke action's own non-zero exit is NOT this
        # path -- it travels back in the Result; only the deploys' elevated
        # commands raise CommandError here.)
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
