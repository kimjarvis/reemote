# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``zabbix_agent`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.zabbix_agent`; this script is the runnable artifact -- a
plain ``main()`` that does what every reemote program does, with no shared
runner and no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory and validate it up front with ``validate_inventory``.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``zabbix_agent`` role mapping (``server``, ``serveractive``, ``version``
   and the rest of the :class:`~reemote.roles.zabbix_agent.ZabbixAgentVars`
   fields) is bound under the bare field names -- ``zabbix_agent_server`` in
   the inventory sets ``server``.
3. **Await** the factory -- ``zabbix_agent(vars_set)`` is ``async def``
   because it downloads the vendor signing key control-side before returning
   the deploy. That IO runs even under ``dry`` (§6.3).
4. Drive the deploy with ``run_waves``: a ``dry=True`` preview first, then
   the real apply.

Installing and running
----------------------

The role renders ``zabbix_agentd.conf.j2`` with Jinja2, so the ``templates``
extra is required::

    uv run python examples/roles/zabbix_agent.py

Or with an explicit extra::

    pip install "reemote[templates]"
    python examples/roles/zabbix_agent.py

The control machine needs outbound HTTP to ``repo.zabbix.com`` (the signing
key), and each target needs egress to ``repo.zabbix.com`` too (the apt
repository). Hosts outside the ``"Ubuntu"`` family are skipped silently by the
role's guard.

Give the entry its role mapping before running::

    "roles": {
        "zabbix_agent": {
            "server": "192.168.1.10",
            "serveractive": "192.168.1.10",
            "version": "4.4"
        }
    }

``main`` drives the deploy twice: a ``dry=True`` preview first, then the real
apply. The dry pass still fetches the signing key (async factory IO is
unconditional) but parks at the ``fact(LsbRelease)`` barrier, so it plans one
wave deep. A converged rerun is the honest preview: run the whole program a
second time and every line reports no change, the checkpoint notifies nobody,
and the restart-handler wave disappears.
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
from reemote.roles import ZabbixAgentVars, zabbix_agent


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted.
        vars_set = resolve_vars(ZabbixAgentVars, "zabbix_agent", inventory)

        # The factory is async: it fetches the vendor signing key control-side
        # before returning the deploy. This IO runs even under dry.
        deploy = await zabbix_agent(vars_set)

        # Dry preview: one wave deep (parks at the LsbRelease barrier).
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: every wave, planned and applied in lockstep across hosts.
        report, _hosts = await run_waves(inventory, deploy)
        print("\n== Wave tree (apply phase) ==")
        print(report.tree())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (ValueError, TypeError) as exc:
        # A rejected role variable (pydantic's ValidationError is a ValueError),
        # or the role body's own gate: a scoped-out knob (D11) or a (version,
        # codename) pair with no frozen signing key (D4).
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
