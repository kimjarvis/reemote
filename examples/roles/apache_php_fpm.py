# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``apache_php_fpm`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.apache_php_fpm`; this script is the runnable artifact -- a
plain ``main()`` that does what every reemote program does, with no shared
runner and no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``apache_php_fpm`` role mapping (``server_root``, ``service`` and
   ``restart_state`` -- the :class:`~reemote.roles.apache_php_fpm.ApachePhpFpmVars`
   fields) is bound under the bare field names, and a bad value raises before
   any host is contacted. The role ships no defaults of its own upstream, so
   the model defaults are the whole story unless the inventory overrides them.
3. Build the deploy -- ``apache_php_fpm(vars_set)`` is the role's whole task
   list, one per-host generator -- and drive it with ``run_waves``, the
   barrier-aware driver a role needs: its handler gate is a ``checkpoint``
   barrier *inside* the body, not a separate ``run()`` call. ``run_waves`` owns
   the host connections and closes them itself.

Prerequisite
------------

The role only symlinks ``mods-available/proxy.load`` and
``mods-available/proxy_fcgi.load`` into ``mods-enabled`` and restarts the
service, so Apache must already be installed -- upstream that guarantee is the
``geerlingguy.apache`` meta-dependency, and the faithful reemote form is to run
the migrated :func:`reemote.roles.apache` role first (or compose it with
``scoped``). On a host where ``apache2`` is absent the symlinks still land
(pointing at files that do not exist), and the handler restart fails.

Installing and running
----------------------

No extra is needed beyond the library itself -- the role renders nothing, it is
two symlinks and one handler::

    pip install reemote

Run it with::

    uv run python examples/roles/apache_php_fpm.py

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass is one wave deep
-- ``run_waves`` plans the two ``files.link`` requests, parks on the
``checkpoint("configure done")`` barrier and stops (``barrier: configure done
-> unknown until run``), applying nothing. A converged rerun is the honest
preview: run the whole program a second time and it reports no change anywhere
-- both symlinks exist and point at their ``mods-available`` targets, so the
checkpoint names nobody and the handler wave is absent.

Hosts outside the ``"Ubuntu"`` family emit nothing at all -- the role's family
guard is its body's opening ``if``, so such a host returns before its first
``yield`` and appears in no wave. The RedHat entries of the shipped inventory
are therefore skipped.

Sharing the demo host
---------------------

The ``ubuntu`` demo host also runs the other role examples, and its Apache
handler restarts (and thereby starts) ``apache2`` -- which binds ports 80/443
and fails with "Address already in use" while ``nginx`` owns them. Stop nginx
before the real run and restore it afterwards::

    ssh <user>@<ubuntu-host> 'sudo systemctl stop nginx'
    uv run python examples/roles/apache_php_fpm.py
    ssh <user>@<ubuntu-host> 'sudo systemctl stop apache2; sudo systemctl disable apache2; sudo systemctl reset-failed apache2 nginx; sudo systemctl start nginx; sudo systemctl enable nginx'

The role leaves the two proxy symlinks in ``mods-enabled``; removing them again
(``sudo rm /etc/apache2/mods-enabled/proxy.load
/etc/apache2/mods-enabled/proxy_fcgi.load``) and restarting apache2 returns the
host to its pre-run state.
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
from reemote.roles import ApachePhpFpmVars, apache_php_fpm


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's apache_php_fpm role mapping over the model defaults. A
        # rejected value raises here -- a pydantic ValidationError, which is a
        # ValueError -- not mid-run.
        vars_set = resolve_vars(ApachePhpFpmVars, "apache_php_fpm", inventory)
        # The role's whole task list: one per-host generator, barrier and all.
        # A plain (non-async) factory, so it is called, not awaited.
        deploy = apache_php_fpm(vars_set)

        # Dry preview: one wave deep. run_waves plans the two symlink
        # requests, parks on the checkpoint("configure done") barrier and
        # stops, applying nothing. run_waves owns the hosts it materializes
        # and closes them itself.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: every wave, planned and applied in lockstep across hosts
        # over SSH -- the symlink wave, the checkpoint the handler gate reads,
        # then the restart handler on the hosts it selected.
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
        # handler restart of an apache2 that cannot bind its ports or is not
        # installed at all. Print that message -- no traceback -- and exit
        # non-zero so the failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
