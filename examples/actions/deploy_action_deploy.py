# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: a deploy, then a bespoke action, then a second deploy.

This is a *usage* example, not part of the library. It is the action analogue
of ``examples/demonstrations/composition/two_deploy_split.py`` and shows
where actions belong: BEFORE, BETWEEN and AFTER deploys, never inside one.
The scenario is "install nginx, probe it with a bespoke command, then
configure it only where the probe succeeded":

1. ``install_nginx`` -- a branch-free deploy that only installs the package.
   Both phases trivially yield the same single request. The ``is_centos``
   group guard is composed onto it with ``sub_deploy(..., when=is_centos)``,
   so the inner operation carries no ``when`` of its own.
2. An ACTION between the runs: ``gather_action(centos_hosts, Shell,
   command="nginx -v")`` runs a command the library knows nothing about and
   returns a ``{host.name: Result}`` mapping. ``nginx -v`` prints its version
   to stderr and exits 0 where nginx is installed (127 where it is not), so
   each ``Result``'s ``exit_status`` reports whether the install actually put
   nginx on the box. The host list is filtered by ``is_centos`` BEFORE the
   fan-out -- an action has no ``when`` meta-parameter -- so the probe only
   touches the ``centos`` hosts.
   The apply-phase join of run 1 is the barrier: every host has finished
   installing before any host's probe runs; the awaited ``gather_action`` is
   itself a barrier, so every probe has finished before run 2 is built.
3. ``build_configure_nginx(probe)`` -- a factory closing over that frozen
   ``Result`` dict, returning the second deploy. Its ``if`` consults
   ``probe.get(host.name)`` and the exit status -- an ordinary lookup on a
   snapshot taken BEFORE this deploy's prepare phase -- so the generator is a
   pure function of static input: prepare and apply resolve the branch
   identically and the determinism guard holds. The staged put additionally
   carries ``when=is_centos``, guarding the request (and its facts) on the
   hosts the probe never visited.

This is exactly the two-deploy split, with an action ``Result`` playing the
role the ``DnfPackages`` fact snapshot plays there. The action is the escape
hatch for a probe no fact models; because its outcome is frozen into a dict
between deploys, branching on it is determinism-safe. Branching on a LIVE
action inside a deploy is both illegal (the in-deploy guard raises
``ActionInDeployError``) and impossible (a sync generator cannot await).

The hosts come from the shared ``inventory.json`` in the project root, read
by ``load_inventory`` and validated, so the ``centos`` hosts run the deploys
and the probe while the ``alpine`` and ``ubuntu`` entries skip -- their
requests are never materialized and the probe never contacts them.

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
import json
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
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
    sub_deploy,
    validate_inventory,
)

# The config payload, created once on the control machine: a stream, never a
# local path. The same object is passed to put in every phase, so the prepare
# and apply request sequences compare equal.
NGINX_CONF = io.StringIO(
    "# Managed by reemote -- staged by the deploy/action/deploy example.\n"
    "worker_processes auto;\n"
)

# Staging destination under /tmp: the unprivileged scp transfer can write it
# as the login user. Moving it into /etc/nginx would need an elevated op.
STAGED_CONF = "/tmp/nginx.conf"

# The shared inventory in the project root -- the same inventory.json the
# library's other examples read; the default host_factory materializes each
# entry into a plain Host connecting over asyncssh.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    ``json.loads`` parses the file into a list of entry dicts, and
    ``validate_inventory`` checks every entry against the inventory schema --
    a malformed file raises before any connection is attempted.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_centos(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"centos"``.

    Reads only the host's static ``group`` field, so it is deterministic across
    the prepare and apply phases. The action probe between the deploys has no
    ``when`` meta-parameter, so it reuses this predicate as a host-list filter.
    """
    return host.group == "centos"


def install_nginx(host: Host) -> Generator[OperationRequest]:
    """Deploy 1: install only, branch-free, so both phases yield one request.

    The operation carries no ``when``: the ``is_centos`` group guard is
    composed onto the whole deploy below.
    """
    yield op(dnf.packages, name="Install nginx", packages=("nginx",), sudo=True)


# The group guard, composed once at the outermost level: the install runs only
# on a ``centos`` host, and a skipped request is never materialized, so its
# facts are never read on the other hosts either.
install_nginx = sub_deploy(install_nginx, when=is_centos)


def build_configure_nginx(
    probe: dict[str, Result],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 2 factory: close over the frozen post-install action Results.

    The returned generator branches on ``probe.get(host.name)`` and its
    ``exit_status`` -- an ordinary dict lookup on a snapshot taken BEFORE this
    deploy's prepare phase -- so both phases resolve the branch identically.
    The probe only visited the ``centos`` hosts, so ``.get`` yields ``None``
    for every other host and the branch stays closed there. Running the
    action live here instead would trip the in-deploy guard (and could not be
    awaited in a sync generator anyway).
    """

    def configure_nginx(host: Host) -> Generator[OperationRequest]:
        result = probe.get(host.name)
        if result is not None and result["exit_status"] == 0:
            yield op(
                files.put,
                name="Stage nginx.conf",
                src=NGINX_CONF,
                dest=STAGED_CONF,
                when=is_centos,
            )

    return configure_nginx


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file itself is missing or not valid JSON.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The file parsed, but an entry violates the inventory schema.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"== Inventory loaded from {INVENTORY_PATH} ==")
    try:
        # Run 1: prepare + apply the install. Its apply-phase join is the
        # barrier guaranteeing every host finished before the probe below.
        plans, report, hosts = await run(inventory, install_nginx)
        print("== Deploy 1: install nginx ==")
        print(format_plan(plans))
        assert report is not None
        print(format_log(report))

        # The ACTION between deploys: a bespoke probe no fact models.
        # gather_action re-connects the (closed) hosts, runs `nginx -v` on the
        # selected ones concurrently, returns {host.name: Result}, and closes
        # them again. The action fan-out has no `when`, so the group guard is
        # a host-list filter: only the centos hosts are probed. The await is
        # the barrier before run 2 is built.
        centos_hosts = [host for host in hosts if is_centos(host)]
        probe = await gather_action(centos_hosts, Shell, command="nginx -v")
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
        plans, _, _hosts = await run(inventory, configure, dry=True)
        print("\n== Deploy 2 plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run 2: configure the hosts whose probe shows nginx present.
        plans, report, _hosts = await run(inventory, configure)
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
