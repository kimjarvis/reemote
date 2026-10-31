"""Example: splitting a fact-dependent deploy into two deterministic deploys.

This is a *usage* example, not part of the library. It demonstrates the
 sanctioned workaround for a task the determinism rule otherwise forbids:
"install nginx, then configure it only if the install actually put it on
the box". Written as ONE deploy, that branch is illegal -- the deploy
generator runs twice (prepare against pre-deployment state, apply against
live state), so a generator body branching on ``dnf.DnfPackages`` after a
``dnf.packages`` install yields a different request sequence per phase and
``run_apply`` raises ``DeterminismError``.

The fix is to move the mutable-fact read OUT of the generator and into the
gap between two complete prepare/apply cycles:

1. ``install_nginx`` -- a branch-free deploy that only installs the package.
   Both phases trivially yield the same single request.
2. A fact SNAPSHOT taken between the runs: ``gather_fact`` fans the
   ``DnfPackages`` read out across all hosts and returns a
   ``{host.name: set[str]}`` mapping of the real POST-install state. The
   apply-phase join of run 1 is the barrier: every host has finished
   installing before any host's snapshot is read.
3. ``build_configure_nginx(installed)`` -- a factory closing over that
   frozen dict, returning the second deploy. Its ``if`` consults the
   snapshot, never a live fact, so the generator is a pure function of
   static input: prepare and apply resolve the branch identically and the
   determinism guard holds. The second run's ``dry=True`` plan is therefore
   an honest preview computed against the state run 1 actually left behind.

The trade-off: the snapshot is frozen. If something external uninstalled
nginx between the snapshot and run 2, the second deploy would not re-plan --
the same no-mid-run-replanning guarantee the determinism rule always makes,
now with the boundary at a point the script controls.

The configure step stages the config with ``files.put`` to ``/tmp``:
transfers ride the connection's scp channel as the login user and are never
elevated, so writing into root-owned ``/etc/nginx`` would be a follow-up
elevated shell operation (out of scope here). The put source is a
module-level ``io.StringIO`` -- one stable identity across both phases, as
``OperationRequest`` compares the unhashable stream by identity -- and its
sha1-based idempotence means re-running the whole script transfers nothing
once the staged content matches.

``run`` closes the hosts it owns, but ``aclose`` only resets the cached
connection and ``connect`` is lazy, so ``gather_fact`` on the returned hosts
transparently re-opens them; the script closes them again afterwards. Run 2
builds fresh hosts from the same inventory via the default factory.

Run it with::

    uv run python examples/general/two_deploy_split.py
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
    dnf,
    files,
    format_log,
    format_plan,
    gather_fact,
    op,
    run,
)

# The config payload, created once on the control machine: a stream, never a
# local path. The same object is passed to put in every phase, so the prepare
# and apply request sequences compare equal.
NGINX_CONF = io.StringIO(
    "# Managed by reemote5 -- staged by the two-deploy split example.\n"
    "worker_processes auto;\n"
)

# Staging destination under /tmp: the unprivileged scp transfer can write it
# as the login user. Moving it into /etc/nginx would need an elevated op.
STAGED_CONF = "/tmp/nginx.conf"

# Same real-host inventory as dnf_put_get.py; the default host_factory
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
    installed: dict[str, set[str]],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 2 factory: close over the frozen post-install fact snapshot.

    The returned generator branches on ``installed[host.name]`` -- an
    ordinary dict lookup on a snapshot taken BEFORE this deploy's prepare
    phase -- so both phases resolve the branch identically. Reading the live
    ``DnfPackages`` fact here instead would re-introduce the phase-dependent
    branch and trip the determinism guard.
    """

    def configure_nginx(host: Host) -> Generator[OperationRequest]:
        if "nginx" in installed[host.name]:
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
        # barrier guaranteeing every host finished before the snapshot below.
        plans, report, hosts = await run(INVENTORY, install_nginx)
        print("== Deploy 1: install nginx ==")
        print(format_plan(plans))
        assert report is not None
        print(format_log(report))

        # The snapshot: one concurrent read of the real post-install state,
        # keyed by host name. run closed these hosts, but connect is lazy, so
        # gather_fact re-opens them; close them again when done.
        try:
            installed = await gather_fact(hosts, dnf.DnfPackages)
        finally:
            for host in hosts:
                await host.aclose()
        print("\n== Post-install snapshot (DnfPackages) ==")
        for name, packages in installed.items():
            print(f"{name}: nginx {'installed' if 'nginx' in packages else 'ABSENT'}")

        # Build deploy 2 ONCE, before either of its phases: both the dry run
        # and the real run use the very same callable closing over the same
        # frozen dict -- determinism by construction, as in composed_environments.
        configure = build_configure_nginx(installed)

        # --dry for deploy 2: the plan is honest because the branch consults
        # the post-install snapshot, not pre-deployment state.
        plans, _, _hosts = await run(INVENTORY, configure, dry=True)
        print("\n== Deploy 2 plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run 2: configure the hosts whose snapshot shows nginx present.
        plans, report, _hosts = await run(INVENTORY, configure)
        print("\n== Deploy 2 run log (apply phase) ==")
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
