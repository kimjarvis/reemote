# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``php`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.php`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``php`` role mapping (``memory_limit``, ``enable_php_fpm``,
   ``webserver_daemon`` and the rest of the
   :class:`~reemote.roles.php.PhpVars` fields) is bound under the bare field
   names, and a bad value raises before any host is contacted. No call-site
   ``overrides`` are passed here, so precedence is just model defaults < the
   inventory role mapping.
3. Build the deploy -- ``php(vars_set)`` is the role's whole task list, one
   per-host generator -- and drive it with ``run_waves``, the barrier-aware
   driver a role needs: its distribution read, its stray-config sweeps, its
   pool-directory read and its three checkpoints are barriers *inside* the body,
   not separate ``run()`` calls. ``run_waves`` owns the host connections and
   closes them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.

Installing and running
----------------------

Unlike the apache role, php renders every template (``php.ini``, the APCu and
OpCache inis, the FPM pool) as a plain Python f-string, so it needs no template
extra. Run it with::

    uv run python examples/roles/php.py

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass is one wave deep --
and because the role's very first item is the ``LinuxDistribution`` barrier, it
parks there at once (``barrier: LinuxDistribution -> unknown until run``),
gathering no state, planning nothing and applying nothing. A converged rerun is
the honest preview: run the whole program a second time and it reports no change
and queues no handler, because the packages are installed, the cache stamp is
fresh, the packaged inis are already gone, every rendered file is byte-identical,
the pool is where it belongs and the unit is running and enabled.

The ``ubuntu`` entry of the shipped ``inventory.json`` carries no ``php`` role
mapping, so the role's defaults apply: FPM **off**, ``memory_limit = 256M``,
the three version-derived conf paths, and ``apache2`` as the webserver the
handler restarts. Hosts outside the ``"Ubuntu"`` family emit nothing at all --
the role's family guard is its body's opening ``if``, so such a host returns
before its first ``yield`` and appears in no wave.
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
from reemote.roles import PhpVars, php


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's php role mappings over the model defaults. A rejected
        # value raises here -- a pydantic ValidationError, which is a ValueError
        # -- not mid-run.
        vars_set = resolve_vars(PhpVars, "php", inventory)
        # The role's whole task list: one per-host generator, barriers and all.
        # A plain (non-async) factory, so it is called, not awaited.
        deploy = php(vars_set)

        # Dry preview: one wave deep. The role's first item is the
        # LinuxDistribution barrier, so the run parks there before gathering any
        # state, plans nothing and applies nothing. run_waves owns the hosts it
        # materializes and closes them itself.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: every wave, planned and applied in lockstep across hosts
        # over SSH, each barrier closing its own wave.
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
        # A rejected role variable (pydantic's ValidationError is a ValueError),
        # or the role body's own hard error: the resolved PHP version is not
        # dotted digits. It is raised right after the distribution barrier and
        # before any request is built, so nothing is half-applied.
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
