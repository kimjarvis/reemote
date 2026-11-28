# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``supervisor`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.supervisor`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``supervisor`` role mapping (``version``, ``config_path``, ``programs``,
   ``started`` and the rest of the
   :class:`~reemote.roles.supervisor.SupervisorVars` fields) is bound under the
   bare field names, the call-site ``SUPERVISOR_OVERRIDES`` below wins over
   them, and a bad value raises before any host is contacted.
3. Build the deploy -- ``supervisor(vars_set)`` is the role's whole task list
   and its handler, one per-host generator -- and drive it with ``run_waves``,
   the barrier-aware driver a role needs: the ``checkpoint("configure done")``
   that reads the config changes and selects the ``restart supervisor`` hosts
   sits *inside* the body, not in a separate gather-then-run split.
   ``run_waves`` owns the host connections and closes them itself.

Prerequisite
------------

The role installs Supervisor *with pip* -- exactly like the Ansible role, whose
README says "this role requires pip", e.g. via ``geerlingguy.pip``. On a host
without ``pip3`` (PEP 668 Ubuntu included), run the shipped ``pip`` role first::

    uv run python examples/roles/pip.py

then this one. It installs ``python3-pip`` and removes the ``EXTERNALLY-MANAGED``
marker, which is what the Supervisor pip install needs.

Installing and running
----------------------

The role renders ``supervisord.conf``, the program configs and the systemd unit
with Jinja2, which reemote does not ship: install the ``reemote[templates]``
extra (or run inside this repo, whose ``dev`` group declares jinja2). Run it
with::

    uv run python examples/roles/supervisor.py

The call-site ``overrides`` channel
-----------------------------------

``SUPERVISOR_OVERRIDES`` pins nothing (an empty ``version`` installs
unpinned, the role's ``default(omit)``) but manages one demo program -- a
``sleep 3600`` with an extra configuration line -- so the demonstration writes
``conf.d/demo.conf`` visibly and the ``restart supervisor`` handler has
something to fire on. Overrides and the inventory mapping both use the model's
bare field names -- there is no key prefix anywhere -- and the overrides win
over the inventory's ``supervisor`` role mapping for every host. Everything
else stays default: the unix http server with its ``{SHA}``-hashed password,
the loopback-only posture (``inet_http_server_enable`` is false by default),
the ``/etc/supervisor`` config path and the ``0755`` ``/var/log/supervisor``
log directory.

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.
The factory itself is a plain (non-``async``) function -- the role reads no
facts, its only barrier is the checkpoint -- so ``supervisor(vars_set)`` is
*called*, not awaited.

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass plans the request
wave and parks on the ``checkpoint`` barrier (``run_waves`` stops there: the
notification read needs a real apply report), so nothing is applied.
Convergence is what the offline pytest suite pins: on a rerun against a
converged host every request reports no change, the notification set is empty,
and the handler wave never appears -- the package is installed, the directories
and the three rendered files are in place, the unit is running and enabled, and
the state-gated daemon-reload (issue #50) emits no command.

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
from reemote.roles import SupervisorVars, supervisor

# Common role variables handed to ``resolve_vars`` as call-site ``overrides``.
# The managed ``demo`` program makes the first apply demonstrably write
# ``conf.d/demo.conf`` and demonstrably restart the service through the
# checkpoint -> handler wave. Overrides use the model's bare field names -- the
# same names the inventory's ``supervisor`` role mapping uses -- and win over it
# for every host.
SUPERVISOR_OVERRIDES = {
    "programs": (
        {
            "name": "demo",
            "command": "sleep 3600",
            "configuration": "autostart=true\nautorestart=true",
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
        # inventory's supervisor role mapping, overlaid with the call-site
        # overrides. A rejected value raises here -- a pydantic ValidationError,
        # which is a ValueError -- not mid-run.
        vars_set = resolve_vars(
            SupervisorVars, "supervisor", inventory, overrides=SUPERVISOR_OVERRIDES
        )
        # The role's whole task list and handler: one per-host generator,
        # barrier and all. A plain (non-async) factory, so it is called, not
        # awaited.
        deploy = supervisor(vars_set)

        # Dry preview: the request wave is planned, then the run parks on the
        # checkpoint barrier -- the notification read needs a real apply report,
        # so the handler wave stays unplanned (like Ansible's --check skipping
        # handlers). Nothing is applied. run_waves owns the hosts and closes
        # them.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, parked at the checkpoint) ==")
        print(report.tree())

        # Real run: the configure wave (pip package, log directory, config
        # directories, rendered main config, program configs, rendered unit,
        # started/enabled service), the checkpoint, then the handler wave on the
        # notified hosts only.
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
        # A rejected role variable: pydantic's ValidationError is a ValueError,
        # raised by resolve_vars before any host is contacted. An ImportError
        # here would mean the reemote[templates] extra is missing -- the render
        # happens when the first configure wave is built.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command -- including the
        # pip install failing because the host has no pip3 yet (run the pip
        # role's example first). Print that message -- no traceback -- and exit
        # non-zero so the failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
