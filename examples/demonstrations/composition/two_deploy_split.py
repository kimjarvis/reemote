# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

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
   ``{host.name: list[str]}`` mapping of the real POST-install state (the
   fact's sorted name list). The apply-phase join of run 1 is the barrier:
   every host has finished installing before any host's snapshot is read.
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
``OperationRequest`` compares a stream by identity -- and its
sha1-based idempotence means re-running the whole script transfers nothing
once the staged content matches.

``run`` closes the hosts it owns, but ``aclose`` only resets the cached
connection and ``connect`` is lazy, so ``gather_fact`` on the returned hosts
transparently re-opens them; the script closes them again afterwards. Run 2
builds fresh hosts from the same inventory via the default factory.

The inventory is read from the ``inventory.json`` in the current working directory
(validated up front with ``validate_inventory``), which holds hosts in the
``\"RedHat\"`` family alongside hosts in other families. Both deploys carry
``when=is_redhat`` so the ``dnf``/``files`` steps run only on the RedHat hosts, and the
fact snapshot is guarded the same way: ``gather_fact`` takes no ``when`` parameter, so
the script filters the materialized host list to the RedHat family before the fan-out --
the ``DnfPackages`` read would fail on the Alpine and Ubuntu hosts, which have no
``dnf``. The snapshot is therefore keyed by RedHat host only, and deploy 2's lookup uses
a default for the hosts it never covers.

Run it with::

    uv run python examples/demonstrations/composition/two_deploy_split.py
"""

import asyncio
import io
import json
import sys
from collections.abc import Callable, Generator
from pathlib import Path

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    dnf,
    files,
    format_log,
    format_plan,
    gather_fact,
    op,
    run,
    validate_inventory,
)

# The config payload, created once on the control machine: a stream, never a
# local path. The same object is passed to put in every phase, so the prepare
# and apply request sequences compare equal.
NGINX_CONF = io.StringIO(
    "# Managed by reemote -- staged by the two-deploy split example.\n"
    "worker_processes auto;\n"
)

# Staging destination under /tmp: the unprivileged scp transfer can write it
# as the login user. Moving it into /etc/nginx would need an elevated op.
STAGED_CONF = "/tmp/nginx.conf"


def is_redhat(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"RedHat"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision. It guards both
    the requests and (as a host-list filter) the ``DnfPackages`` snapshot,
    keeping the ``dnf`` work off the Alpine and Ubuntu hosts.
    """
    return host.family == "RedHat"


def install_nginx(host: Host) -> Generator[OperationRequest]:
    """Deploy 1: install only, branch-free, so both phases yield one request."""
    yield op(
        dnf.packages,
        name="Install nginx",
        packages=("nginx",),
        sudo=True,
        when=is_redhat,
    )


def build_configure_nginx(
    installed: dict[str, list[str]],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 2 factory: close over the frozen post-install fact snapshot.

    The returned generator branches on ``installed.get(host.name, ())`` -- an
    ordinary dict lookup on a snapshot taken BEFORE this deploy's prepare
    phase -- so both phases resolve the branch identically. Reading the live
    ``DnfPackages`` fact here instead would re-introduce the phase-dependent
    branch and trip the determinism guard. The snapshot only covers the RedHat
    hosts (the gather was filtered by ``is_redhat``), so the lookup defaults to
    empty for the others and the request also carries ``when=is_redhat``.
    """

    def configure_nginx(host: Host) -> Generator[OperationRequest]:
        if "nginx" in installed.get(host.name, ()):
            yield op(
                files.put,
                name="Stage nginx.conf",
                src=NGINX_CONF,
                dest=STAGED_CONF,
                when=is_redhat,
            )

    return configure_nginx


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Run 1: prepare + apply the install. Its apply-phase join is the
        # barrier guaranteeing every host finished before the snapshot below.
        # The when=is_redhat guard skips the install on the non-RedHat hosts.
        plans, report, hosts = await run(inventory, install_nginx)
        print("\n== Deploy 1: install nginx ==")
        print(format_plan(plans))
        assert report is not None
        print(format_log(report))

        # The snapshot: one concurrent read of the real post-install state,
        # keyed by host name. gather_fact has no ``when`` parameter, so the
        # is_redhat predicate filters the host list before the fan-out -- the
        # DnfPackages fact would fail on the Alpine/Ubuntu hosts (no dnf).
        # run closed these hosts, but connect is lazy, so gather_fact re-opens
        # the eligible ones; close them all again when done.
        redhat_hosts = [host for host in hosts if is_redhat(host)]
        skipped = [host.name for host in hosts if not is_redhat(host)]
        if skipped:
            print("\n== Skipped (when guard is_redhat): " + ", ".join(skipped) + " ==")
        try:
            installed = await gather_fact(redhat_hosts, dnf.DnfPackages)
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
        plans, _, _hosts = await run(inventory, configure, dry=True)
        print("\n== Deploy 2 plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run 2: configure the hosts whose snapshot shows nginx present.
        plans, report, _hosts = await run(inventory, configure)
        print("\n== Deploy 2 run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON. Python's
        # own message already names the file and the reason, so print just that
        # -- no host was contacted and there is no traceback worth showing.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema. The
        # message names the offending entry by its list index, which is the
        # whole report -- and it fails before any host is materialized.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
