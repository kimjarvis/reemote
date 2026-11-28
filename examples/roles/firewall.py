# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``firewall`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.firewall`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``firewall`` role mapping (``allowed_tcp_ports``, ``additional_rules``,
   ``state`` and the rest of the :class:`~reemote.roles.firewall.FirewallVars`
   fields) is bound under the bare field names, the call-site
   ``FIREWALL_OVERRIDES`` below wins over them, and a bad value raises before
   any host is contacted.
3. Build the deploy -- ``firewall(vars_set)`` is the role's whole task list, one
   per-host generator -- and drive it with ``run_waves``, the barrier-aware
   driver a role needs: its ``creates=`` gate, its first-run flush and its
   configure checkpoint are barriers *inside* the body, not separate ``run()``
   calls. ``run_waves`` owns the host connections and closes them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.

The call-site ``overrides`` channel
-----------------------------------

``FIREWALL_OVERRIDES`` (a trimmed TCP port list and one ``8080 -> 80`` forward)
is handed to ``resolve_vars`` and wins over the inventory's ``firewall`` role
mapping for every host. Overrides and the inventory mapping both use the
model's bare field names -- there is no key prefix anywhere -- so
``roles.firewall.allowed_tcp_ports`` in the inventory and
``allowed_tcp_ports`` here set the same field. Precedence is model defaults <
inventory role mapping < these overrides. The shipped ``inventory.json`` carries
no ``firewall`` role mapping, so these overrides decide the rule set: ports 22,
80 and 443 stay open and host traffic to ``8080`` is redirected to ``80``.

Installing and running
----------------------

The role renders its ``firewall.bash`` script control-side with Jinja2, so it
needs the template extra::

    pip install "reemote[templates]"

Run it with::

    uv run python examples/roles/firewall.py

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass is one wave deep
-- ``run_waves`` plans the package request, parks on the ``FileExists`` barrier
and stops (``barrier: ... -> unknown until run``), gathering facts over SSH but
applying nothing. A converged rerun is the honest preview: run the whole
program a second time and it reports no change and queues no restart, because
the package is installed, the script and the unit file are byte-identical, the
mode matches, and the unit is running and enabled with no daemon reload pending.

One caution before you run it against a live machine: the role *is* a firewall.
On a host that has never run it, the first pass flushes the current rules and
installs one that drops everything except the ports you opened -- keep your SSH
port (``22`` by default) in ``allowed_tcp_ports``.

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
from reemote.roles import FirewallVars, firewall

# Common role variables handed to ``resolve_vars`` as call-site ``overrides``.
# They use the model's bare field names (``allowed_tcp_ports``,
# ``forwarded_tcp_ports``) -- the same names the inventory's ``firewall`` role
# mapping uses -- and win over it for every host. A forwarded port is the same
# ``{src, dest}`` mapping the role's own variable took; ``resolve_vars``
# validates it into a frozen ``ForwardedPort``, so the tuple stays hashable and
# an unquoted YAML int port becomes the string the script renders.
FIREWALL_OVERRIDES = {
    "allowed_tcp_ports": ("22", "80", "443"),
    "forwarded_tcp_ports": ({"src": 8080, "dest": 80},),
}


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's firewall role mappings, overlaid with the call-site
        # overrides. A rejected value raises here -- a pydantic ValidationError,
        # which is a ValueError -- not mid-run.
        vars_set = resolve_vars(
            FirewallVars, "firewall", inventory, overrides=FIREWALL_OVERRIDES
        )
        # The role's whole task list: one per-host generator, barriers and all.
        # A plain (non-async) factory, so it is called, not awaited.
        deploy = firewall(vars_set)

        # Dry preview: one wave deep. Facts are still gathered over SSH
        # (read-only) so the plan reflects each host's real pre-deployment
        # state, but the run parks on the FileExists barrier and applies nothing.
        # run_waves owns the hosts it materializes and closes them itself.
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
        # A rejected role variable (pydantic's ValidationError is a ValueError)
        # or a malformed custom template path. Both are raised before the
        # configure wave is built, so nothing is half-applied.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command -- including the
        # body's own raise when the first-run flush exits non-zero. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
