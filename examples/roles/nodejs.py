# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``nodejs`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.nodejs`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``nodejs`` role mapping (``version``, ``npm_global_packages``,
   ``package_json_path`` and the rest of the
   :class:`~reemote.roles.nodejs.NodejsVars` fields) is bound under the bare
   field names, the call-site ``NODEJS_OVERRIDES`` below wins over them, and a
   bad value raises before any host is contacted.
3. Build the deploy -- ``nodejs(vars_set)`` is the role's whole task list, one
   per-host generator -- and drive it with ``run_waves``, the barrier-aware
   driver a role needs. This role happens to yield no barrier at all (no version
   probe, no ``register``/``when`` read, no handler), so the whole task list
   lands in one wave; ``run_waves`` still owns the host connections and closes
   them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.
The factory itself is a plain (non-``async``) function -- there is no barrier, so
there is no factory IO either -- so ``nodejs(vars_set)`` is *called*, not
awaited.

The call-site ``overrides`` channel
-----------------------------------

``NODEJS_OVERRIDES`` pins ``version`` to a NodeSource major release and hands
``npm_global_packages`` a couple of entries, so the demonstration exercises the
whole stream -- the repository, the version-pinned ``nodejs`` install, the global
npm directory, the profile script and the global-package loop -- and not just the
repository. Overrides and the inventory mapping both use the model's bare field
names -- there is no key prefix anywhere. Precedence is model defaults <
inventory role mapping < these overrides.

Two knobs deliberately stay out of the overrides: ``npm_config_prefix`` and
``npm_config_unsafe_perm`` carry an ``npm_`` prefix upstream and are *not*
:class:`NodejsVars` fields -- the role reads them raw from each host's
``extra`` channel to preserve the existing inventory spelling (deviation D5,
the datadog precedent). Set ``npm_config_prefix`` in an inventory entry's top
level -- not its ``nodejs`` role mapping -- to relocate npm's global prefix.

Installing and running
----------------------

The role renders nothing with a template engine: ``templates/npm.sh.j2`` is three
pure ``{{ npm_config_prefix }}`` substitutions, reproduced byte-for-byte by a
plain f-string, so no ``reemote[templates]`` extra is needed. Run it with::

    uv run python examples/roles/nodejs.py

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. Because this role yields no
barrier, the dry pass does not park early the way ``java``/``docker``/``repo_epel``
do on their first ``fact`` barrier -- it instead *plans the entire wave*, reading
each fact and firing the control-side NodeSource signing-key fetch, and applies
nothing (deviation D9). A converged rerun is the honest preview: run the whole
program a second time and it reports no change, because the dependencies and
``nodejs`` package are installed at the pinned version, the repository and its
keyring are byte-identical, the global npm directory exists with the right owner,
the profile script matches with mode ``0644``, and the global npm packages are
already present.

Hosts outside the ``"Ubuntu"`` family emit nothing at all -- the role's family
guard is its body's opening ``if``, so such a host returns before its first
``yield`` and appears in no wave. The role's RedHat branch (the ``nodejs`` yum
module and the NodeSource rpm) is out of scope, so the CentOS hosts in the sample
inventory are skipped rather than served by a second path.
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
from reemote.roles import NodejsVars, nodejs

# Common role variables handed to ``resolve_vars`` as call-site ``overrides``.
# ``version`` selects the NodeSource major release (interpolated into the
# repository name/URI and the ``nodejs=<major>.*`` apt pin); the two
# ``npm_global_packages`` entries turn on the global-package loop so the
# demonstration covers the full stream, not just the repository and install.
# Overrides use the model's bare field names -- the same names the inventory's
# ``nodejs`` role mapping uses -- and win over it for every host.
# ``npm_config_prefix`` is intentionally absent: it is read from each host's raw
# ``extra``, not from the typed model (D5).
NODEJS_OVERRIDES = {
    "version": "20.x",
    "npm_global_packages": [
        "npm-check-updates",
        {"name": "jslint", "version": "0.9.3"},
    ],
}


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's nodejs role mappings, overlaid with the call-site
        # overrides. A rejected value raises here -- a pydantic ValidationError,
        # which is a ValueError -- not mid-run.
        vars_set = resolve_vars(
            NodejsVars, "nodejs", inventory, overrides=NODEJS_OVERRIDES
        )
        # The role's whole task list: one per-host generator. A plain (non-async)
        # factory, so it is called, not awaited.
        deploy = nodejs(vars_set)

        # Dry preview: no barrier to park on, so run_waves plans the ENTIRE wave
        # -- reading each fact and firing the control-side signing-key fetch --
        # and applies nothing (D9). run_waves owns the hosts and closes them.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, whole wave planned) ==")
        print(report.tree())

        # Real run: every request planned and applied in lockstep across hosts
        # over SSH -- the dependencies, the NodeSource repository and its chained
        # cache refresh, the version-pinned nodejs install, the global npm
        # directory, the profile script plus its mode convergence, and the global
        # npm packages.
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
        # or the role body's own hard error before the request wave is built: the
        # npm install user cannot be resolved (no install_npm_user in the role
        # mapping and no connection login name), or an npm_config_* extra key is
        # empty or of the wrong type. Each raises before any request is applied,
        # so nothing is half-installed.
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
