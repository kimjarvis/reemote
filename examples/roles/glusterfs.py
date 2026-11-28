# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``glusterfs`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.glusterfs`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``glusterfs`` role mapping (``daemon``, ``ppa_use``, ``ppa_version``,
   ``default_release`` and the rest of the
   :class:`~reemote.roles.glusterfs.GlusterfsVars` fields) is bound under the
   bare field names, the call-site ``GLUSTERFS_OVERRIDES`` below wins over them,
   and a bad value raises before any host is contacted.
3. Build the deploy -- ``glusterfs(vars_set)`` is the role's whole task list,
   one per-host generator -- and drive it with ``run_waves``, the barrier-aware
   driver a role needs: its reinstall gate is a ``checkpoint`` barrier *inside*
   the body, not a separate ``run()`` call. ``run_waves`` owns the host
   connections and closes them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

The call-site ``overrides`` channel
-----------------------------------

``GLUSTERFS_OVERRIDES`` pins ``ppa_version="11"``. The upstream role's default
(``glusterfs_ppa_version: "7"``) predates modern Ubuntu releases -- the
``glusterfs-7`` PPA publishes no noble suite, so ``apt-get update`` would fail
on it -- while ``ppa:gluster/glusterfs-11`` publishes jammy and noble.
Overrides use the model's bare field names and win over the inventory's
``glusterfs`` role mapping for every host. Precedence is model defaults <
inventory role mapping < these overrides. The shipped ``inventory.json`` carries
no ``glusterfs`` role mapping, so these overrides decide the site.

Installing and running
----------------------

No extra is needed beyond the library itself -- the role renders nothing, it is
packages and one unit::

    pip install reemote

Run it with::

    uv run python examples/roles/glusterfs.py

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass is one wave deep
-- ``run_waves`` plans the setup requests (the dependency packages, plus the
PPA when ``ppa_use`` is set), parks on the ``checkpoint("setup done")`` barrier
and stops (``barrier: setup done -> unknown until run``), gathering nothing and
applying nothing. A converged rerun is the honest preview: run the whole
program a second time and it reports no change anywhere, because the
dependencies and both GlusterFS packages are installed, the PPA already exists,
and the ``glusterd`` unit is running and enabled.

Hosts outside the ``"Ubuntu"`` family emit nothing at all -- the role's family
guard is its body's opening ``if``, so such a host returns before its first
``yield`` and appears in no wave. The RedHat entries of the shipped inventory
are therefore skipped.

Sharing the demo host
---------------------

The ``ubuntu`` demo host also runs the other role examples. This role leaves
``glusterd`` listening on its cluster ports (24007-24009 and the bricked
ports) with no volumes configured, which affects no other role. To stop and
disable it again after a real run::

    ssh <user>@<ubuntu-host> 'sudo systemctl disable --now glusterd'
"""

import asyncio
import json
import sys
from pathlib import Path

from reemote import (
    CommandError,
    HostConnectionError,
    InventoryError,
    PrivilegeError,
    resolve_vars,
    run_waves,
    validate_inventory,
)
from reemote.roles import GlusterfsVars, glusterfs

# Common role variables handed to ``resolve_vars`` as call-site ``overrides``.
# They use the model's bare field names (``ppa_version``) -- the same names the
# inventory's ``glusterfs`` role mapping uses -- and win over it for every
# host. See the docstring: the pinned version is the one whose PPA publishes
# suites current Ubuntu releases ship.
GLUSTERFS_OVERRIDES = {
    "ppa_version": "11",
}


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's glusterfs role mapping, overlaid with the call-site
        # overrides. A rejected value raises here -- a pydantic ValidationError,
        # which is a ValueError -- not mid-run.
        vars_set = resolve_vars(
            GlusterfsVars, "glusterfs", inventory, overrides=GLUSTERFS_OVERRIDES
        )
        # The role's whole task list: one per-host generator, barrier and all.
        # A plain (non-async) factory, so it is called, not awaited.
        deploy = glusterfs(vars_set)

        # Dry preview: one wave deep. run_waves plans the setup requests, parks
        # on the checkpoint("setup done") barrier and stops, applying nothing.
        # run_waves owns the hosts it materializes and closes them itself.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: every wave, planned and applied in lockstep across hosts over
        # SSH -- the setup wave, the checkpoint the reinstall gate reads, then
        # the install-and-service wave.
        report, _hosts = await run_waves(inventory, deploy)
        print("\n== Wave tree (apply phase) ==")
        print(report.tree())
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
    except (ValueError, TypeError) as exc:
        # A rejected role variable (pydantic's ValidationError is a ValueError)
        # or a typoed role-mapping key (RoleVarsError). Both are raised while
        # the inventory is resolved, so no host was contacted.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command -- including a
        # PPA whose archive has no suite for the host's release, which fails
        # inside the apt refresh the add rides. Print that message -- no
        # traceback -- and exit non-zero so the failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
