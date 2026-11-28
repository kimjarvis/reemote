# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``swap`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.swap`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``swap`` role mapping (``file_path``, ``size_mb``, ``swappiness``,
   ``sysctl_file``, ``state``, ``create_command`` and ``test_mode`` -- the
   :class:`~reemote.roles.swap.SwapVars` fields, under their bare names) is
   bound there, the call-site ``SWAP_OVERRIDES`` below wins over them, and a
   bad value raises before any host is contacted.
3. Build the deploy -- ``swap(vars_set)`` is the role's whole task list, one
   per-host generator -- and drive it with ``run_waves``, the barrier-aware
   driver a role needs: the resize/teardown branch is decided by a
   ``fact(files.File)`` barrier *inside* the body, not by a separate ``run()``
   call. ``run_waves`` owns the host connections and closes them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

The call-site ``overrides`` channel
-----------------------------------

``SWAP_OVERRIDES`` pins ``swappiness=10`` -- the value reemote's own
``sysctl.setting`` example tunes ``vm.swappiness`` to, and the usual
database-host tuning -- instead of the role default of ``60``. Overrides
use the model's bare field names (``swappiness``) -- the same names the
inventory's ``swap`` role mapping uses -- and win over it for every host.
Precedence is model defaults < inventory role mapping < these overrides. The
shipped ``inventory.json`` carries no ``swap`` role mapping, so these overrides
decide the site.

Installing and running
----------------------

No extra is needed beyond the library itself -- the role templates nothing::

    pip install reemote

Run it with::

    uv run python examples/roles/swap.py

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass is one wave deep
-- ``run_waves`` plans the ``server.fstab`` request, parks on the
``fact(files.File)`` barrier and stops (``barrier: Check if swap file exists
-> unknown until run``), gathering nothing and applying nothing. Whether the
build commands (``dd``/``mkswap``/``swapon``) run at all is exactly what that
read decides, so the honest preview is the rerun: run the whole program a
second time and it reports no change anywhere, because the fstab line is
there, the file exists at the wanted size at ``0600`` root:root, and
``vm.swappiness`` already reads ``10``.

Hosts outside the ``"Ubuntu"`` family emit nothing at all -- the role's family
guard is its body's opening ``if``, so such a host returns before its first
``yield`` and appears in no wave. The Alpine and RedHat entries of the shipped
inventory are therefore skipped.

Sharing the demo host
---------------------

The ``ubuntu`` demo host also runs the other role examples. This role adds one
``/swapfile`` line to ``/etc/fstab``, writes ``/etc/sysctl.d/90-swap.conf``
and activates 512 MB of swap -- none of which affects any other role. To undo
it after a real run, rerun this program with ``"state": "absent"`` added to
``SWAP_OVERRIDES``: the role deactivates (``swapoff``), deletes the file and
drops the fstab line. The swappiness drop-in is deliberately left in place,
exactly as upstream leaves it.
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
from reemote.roles import SwapVars, swap

# Common role variables handed to ``resolve_vars`` as call-site ``overrides``.
# They use the model's bare field names (``swappiness``) -- the same names the
# inventory's ``swap`` role mapping uses -- and win over it for every host.
# See the docstring: 10 is the value reemote's sysctl.setting example tunes
# vm.swappiness to; the role default of 60 is the kernel's own default.
SWAP_OVERRIDES = {
    "swappiness": 10,
}


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's swap role mapping, overlaid with the call-site
        # overrides. A rejected value raises here -- a pydantic ValidationError,
        # which is a ValueError -- not mid-run.
        vars_set = resolve_vars(SwapVars, "swap", inventory, overrides=SWAP_OVERRIDES)
        # The role's whole task list: one per-host generator, barrier and all.
        # A plain (non-async) factory, so it is called, not awaited.
        deploy = swap(vars_set)

        # Dry preview: one wave deep. run_waves plans the fstab request, parks
        # on the fact(files.File) barrier and stops, applying nothing -- the
        # size decision and every command after it stay unknown until run.
        # run_waves owns the hosts it materializes and closes them itself.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: every wave, planned and applied in lockstep across hosts
        # over SSH -- the fstab wave closed by the size read, then the build
        # commands each closing their own wave, then the sysctl wave.
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
        # four the body raises itself when dd, mkswap, swapon or swapoff exits
        # non-zero. Print that message -- no traceback -- and exit non-zero so
        # the failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
