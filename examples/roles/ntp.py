# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``ntp`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.ntp`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``ntp`` role mapping (``timezone``, ``enabled``, ``manage_config``,
   ``servers`` and the rest of the :class:`~reemote.roles.ntp.NtpVars` fields)
   is bound under the bare field names, the call-site ``NTP_OVERRIDES`` below
   wins over them, and a bad value raises before any host is contacted.
3. Build the deploy -- ``ntp(vars_set)`` is the role's whole task list, one
   per-host generator -- and drive it with ``run_waves``, the barrier-aware
   driver a role needs: its two ``fact`` barriers (the ``LinuxDistribution``
   release read and the post-install ``SystemdUnitFiles`` timesyncd probe) and its
   two checkpoints sit *inside* the body, not in separate ``run()`` calls.
   ``run_waves`` owns the host connections and closes them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.
The factory itself is a plain (non-``async``) function -- the distribution and
unit-file reads are ``fact`` barriers, not factory IO -- so ``ntp(vars_set)`` is
*called*, not awaited.

The call-site ``overrides`` channel
-----------------------------------

``NTP_OVERRIDES`` (``manage_config=True``) is handed to ``resolve_vars`` and wins
over the inventory's ``ntp`` role mapping for every host, so the demonstration
exercises the config template (rendered control-side) and the ``restart ntp``
handler rather than only the package install. Overrides and the inventory
mapping both use the model's bare field names -- there is no key prefix
anywhere. Precedence is model defaults < inventory role mapping < these
overrides.

Installing and running
----------------------

The role renders its ``ntp.conf`` (or ``chrony.conf``) control-side with Jinja2,
so it needs the template extra::

    pip install "reemote[templates]"

Run it with::

    uv run python examples/roles/ntp.py

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass is one wave deep --
``run_waves`` parks on the ``LinuxDistribution`` fact barrier and stops
(``barrier: LinuxDistribution -> unknown until run``), reading no further state
and applying nothing. A converged rerun is the honest preview: run the whole
program a second time and it reports no change and queues no restart, because the
packages are installed, the cache stamp is fresh, the timezone is set, the daemon
is running and enabled, and the rendered config file is byte-identical -- so
neither ``Set timezone.`` nor ``Generate ntp configuration file.`` changes, and
with no notification the handler wave is empty.

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
from reemote.roles import NtpVars, ntp

# Common role variables handed to ``resolve_vars`` as call-site ``overrides``.
# ``manage_config=True`` turns on the config template and its ``restart ntp``
# handler so the demonstration covers the full stream, not just the packages.
# Overrides use the model's bare field names -- the same names the inventory's
# ``ntp`` role mapping uses -- and win over it for every host.
NTP_OVERRIDES = {"manage_config": True}


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's ntp role mappings, overlaid with the call-site overrides.
        # A rejected value raises here -- a pydantic ValidationError, which is a
        # ValueError -- not mid-run.
        vars_set = resolve_vars(NtpVars, "ntp", inventory, overrides=NTP_OVERRIDES)
        # The role's whole task list: one per-host generator, barriers and all.
        # A plain (non-async) factory, so it is called, not awaited.
        deploy = ntp(vars_set)

        # Dry preview: one wave deep. The run parks on the LinuxDistribution fact
        # barrier (the first item the body yields) and stops, reading no further
        # state and applying nothing. run_waves owns the hosts and closes them.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: every wave, planned and applied in lockstep across hosts over
        # SSH -- the release read, install, install checkpoint, the post-install
        # unit-files read, configure, configure checkpoint, then the handlers.
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
        # or the role body's own hard error: the gathered distribution is not
        # Ubuntu, no shipped vars file matches the chain, or a managed config
        # file has a basename with no bundled template. Each is raised after the
        # LinuxDistribution barrier and before the install wave is built, so
        # nothing is half-applied.
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
