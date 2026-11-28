# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``clamav`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.clamav`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``clamav`` role mapping (``packages``, ``daemon``, ``daemon_state``,
   ``daemon_configuration_changes``, ``freshclam_configuration_changes`` and the
   rest of the :class:`~reemote.roles.clamav.ClamavVars` fields) is bound under
   the bare field names and a bad value raises before any host is contacted.
   The shipped ``inventory.json`` carries no ``clamav`` role mapping, so the
   model defaults decide the site -- per family, from the role's frozen
   ``vars/<Family>.yml`` tables: the Debian package trio and the two
   ``clamav-*`` units on Ubuntu, the dnf quartet, the ``clamd@scan`` instance
   and the ``clamd-freshclam`` unit on RedHat. Both get the role's own two
   default ``clamd.conf`` rules (drop ``Example``, set the family's own
   ``LocalSocket`` line).
3. Build the deploy -- ``clamav(vars_set)`` is the role's whole task list, one
   per-host generator -- and drive it with ``run_waves``, the barrier-aware
   driver a role needs: the freshclam gate and the handler notification are
   ``checkpoint`` barriers *inside* the body, not separate ``run()`` calls.
   ``run_waves`` owns the host connections and closes them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

Installing and running
----------------------

No extra is needed beyond the library itself -- the role renders nothing, the
two config edits are pure text transforms::

    pip install reemote

Run it with::

    uv run python examples/roles/clamav.py

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass is one wave deep
-- ``run_waves`` plans the package request, parks on the
``checkpoint("packages installed")`` barrier and stops (``barrier: packages
installed -> unknown until run``), gathering nothing and applying nothing. A
converged rerun is the honest preview: run the whole program a second time and
it reports no change anywhere, because the packages are installed, the
``Example`` line is gone from the family's daemon config, the socket directive
is set, both units are running and enabled -- and on RedHat the
``clamd-freshclam.service`` file is in place, so its reload is never built.

Hosts outside the ``"Ubuntu"``/``"RedHat"`` families emit nothing at all -- the
role's family guard is its body's opening ``if``, so such a host returns
before its first ``yield`` and appears in no wave. The ``alpine`` entry of the
shipped inventory is therefore skipped: upstream ships no ``vars/Alpine.yml``,
so there is nothing to migrate for it.

Sharing the demo host
---------------------

The ``ubuntu`` demo host also runs the other role examples. This role leaves
``clamav-daemon`` listening on the ``/var/run/clamav/clamd.ctl`` unix socket
(no TCP port is opened -- the package default) and ``clamav-freshclam``
updating the virus databases a few times a day; the initial ``freshclam``
download is a couple of hundred megabytes into ``/var/lib/clamav``. To stop
and disable both units again after a real run::

    ssh <user>@<ubuntu-host> 'sudo systemctl disable --now clamav-daemon clamav-freshclam'

The RedHat hosts get the same treatment under their own names -- plus the unit
file this role installs, which a purge of the packages would leave behind::

    ssh <user>@<redhat-host> 'sudo systemctl disable --now clamd@scan clamd-freshclam'
    ssh <user>@<redhat-host> 'sudo rm /lib/systemd/system/clamd-freshclam.service && sudo systemctl daemon-reload'
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
from reemote.roles import ClamavVars, clamav


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's clamav role mapping over the model defaults. A rejected
        # value raises here -- a pydantic ValidationError, which is a ValueError
        # -- not mid-run.
        vars_set = resolve_vars(ClamavVars, "clamav", inventory)
        # The role's whole task list: one per-host generator, barrier and all.
        # A plain (non-async) factory, so it is called, not awaited.
        deploy = clamav(vars_set)

        # Dry preview: one wave deep. run_waves plans the package request,
        # parks on the checkpoint("packages installed") barrier and stops,
        # applying nothing. run_waves owns the hosts it materializes and
        # closes them itself.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: every wave, planned and applied in lockstep across hosts
        # over SSH -- the install wave, the freshclam act it gates, the unit
        # file (RedHat) and its gated reload, the configure-and-service wave,
        # then the handler wave if anything notified.
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
        # whose message names the host and the failing command -- including the
        # role's freshclam gate: a non-zero ``freshclam`` (or the database-lock
        # message) raises CommandError from inside the body, as the play's
        # abort did. Print that message -- no traceback -- and exit non-zero so
        # the failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
