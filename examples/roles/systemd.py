# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``systemd`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.systemd`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``systemd`` role mapping (``unit_config``, ``perform_uninstall``) is bound
   under the bare field names, the call-site ``SYSTEMD_OVERRIDES`` below wins over
   them, and a bad value raises before any host is contacted -- a unit with no
   ``name``, an unknown ``type``, a ``scope`` outside ``system``/``user``.
3. Build the deploy -- ``systemd(vars_set)`` is the role's whole task list, one
   per-host generator -- and drive it with ``run_waves``, the barrier-aware driver
   a role needs: the passwd read a per-user unit requires and the
   ``configure done`` checkpoint that decides which units to reload are barriers
   *inside* the body, not separate ``run()`` calls. ``run_waves`` owns the host
   connections and closes them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.

What it writes
--------------

``SYSTEMD_OVERRIDES`` manages two things on each target host: a
``reemote-demo.service`` unit under ``/etc/systemd/system``, rendered from its
``Unit``/``Service``/``Install`` sections and then started and enabled, and a
``reemote-demo-override.conf`` drop-in in the unit's own ``.d`` directory. Each
rendered file is the notification channel of the role's two handlers, so writing
either one reloads the manager -- and reloads the unit, whose new
``Environment`` line only takes effect on a restart.

The same variables are what an inventory entry carries, so a unit can just as
well be declared there::

    "roles": {"systemd": {"unit_config": [{"name": "my-service", ...}]}}

A per-user unit adds ``user`` (and, for a non-system load path, the ``path`` the
unit belongs under); its file is installed owned by that account and every
``systemctl`` for it addresses that user's manager::

    {"name": "my-job", "path": "/home/alice/.config/systemd/user",
     "user": "alice", "Service": {"ExecStart": "/usr/bin/sleep infinity"},
     "Install": {"WantedBy": "default.target"}}

Re-declaring the same entries with ``perform_uninstall: true`` stops and disables
each unit and removes each file the role would have written, and writes nothing.

Installing and running
----------------------

The role renders its unit and drop-in files control-side with Jinja2, so it needs
the template extra::

    pip install "reemote[templates]"

Run it with::

    uv run python examples/roles/systemd.py

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass of a root-owned
configuration is *complete* -- no barrier sits before the first request, so the
preview plans the whole run and applies nothing (a per-user unit's preview stops
at its ``fact(Users)`` barrier instead). Run the whole program a second time and
it reports no change: both files are byte-identical, the unit is running and
enabled, so the checkpoint notifies no handler and nothing is reloaded.

Hosts outside the ``"Ubuntu"`` family emit nothing at all -- the role's family
guard is its body's opening ``if``, so such a host returns before its first
``yield`` and appears in no wave.
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
from reemote.roles import SystemdVars, systemd

# Common role variables handed to ``resolve_vars`` as call-site ``overrides``.
# They use the model's bare field names (``unit_config``, ``perform_uninstall``)
# -- the same names the inventory's ``systemd`` role mapping uses -- and win over
# it for every host. Each entry is the same mapping the inventory's
# ``roles.systemd.unit_config`` carries; ``resolve_vars`` validates it into a
# frozen ``UnitSpec``, so the tuple stays hashable and the section dicts survive
# for the template to read.
SYSTEMD_OVERRIDES = {
    "unit_config": (
        {
            "name": "reemote-demo",
            "state": "started",
            "enabled": True,
            "Unit": {
                "Description": "A service managed by the reemote systemd role",
                "After": "network-online.target",
            },
            "Service": {
                "User": "root",
                "Group": "root",
                "ExecStart": "/usr/bin/sleep infinity",
                "ExecReload": "/bin/kill -s HUP $MAINPID",
            },
            "Install": {"WantedBy": "multi-user.target"},
        },
        {
            "name": "reemote-demo-override",
            "type": "conf",
            "path": "/etc/systemd/system/reemote-demo.service.d",
            "Service": {"Environment": ["REEMOTE_DEMO=1"]},
        },
    ),
}


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's systemd role mappings, overlaid with the call-site
        # overrides. A rejected value raises here -- a pydantic ValidationError,
        # which is a ValueError -- not mid-run.
        vars_set = resolve_vars(
            SystemdVars, "systemd", inventory, overrides=SYSTEMD_OVERRIDES
        )
        # The role's whole task list: one per-host generator, barriers and all.
        # A plain (non-async) factory, so it is called, not awaited.
        deploy = systemd(vars_set)

        # Dry preview: nothing is applied. A host whose units are all root-owned
        # system units parks on no barrier at all, so the preview plans every
        # wave of its run; a per-user unit stops at the passwd read.
        # run_waves owns the hosts it materializes and closes them itself.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview) ==")
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
        # or the role body's own preflight failure: a ``scope: user`` unit naming
        # a system load path, or a unit whose owner has no account on the host.
        # Both are raised before the first request is built, so nothing is
        # half-written.
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
