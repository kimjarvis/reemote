# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``datadog`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.datadog`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory and validate it up front with ``validate_inventory``.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``datadog_``-prefixed keys (``datadog_api_key``, ``datadog_site``,
   ``datadog_checks`` and the rest of
   :class:`~reemote.roles.datadog.DatadogVars`) are bound and stripped. The six
   unprefixed probe-config variables (``system_probe_config``,
   ``network_config``, etc.) stay in ``host.extra`` and are read raw by the
   deploy body.
3. **Await** the factory -- ``datadog(vars_set)`` is ``async def`` because it
   fetches the five signing keys control-side before returning the deploy. That
   IO runs even under ``dry`` (§6.3): this is the one role where ``--dry``
   touches the network.
4. Drive the deploy with ``run_waves``: a ``dry=True`` preview first, then the
   real apply.

Installing and running
----------------------

The role renders YAML with PyYAML, so the ``templates`` extra is required::

    uv run python examples/roles/datadog.py

Or with an explicit extra::

    pip install "reemote[templates]"
    python examples/roles/datadog.py

The control machine needs outbound HTTPS to ``s3.amazonaws.com`` (the signing
keys) and the target needs egress to ``apt.datadoghq.com`` (the agent repo).

**The shipped ``inventory.json`` has no ``datadog_api_key``.** The role fails on
a missing key when ``datadog_manage_config`` is true (the default). Add your own
key to the inventory entry before running::

    "datadog_api_key": "YOUR_DATADOG_API_KEY",
    "datadog_site": "datadoghq.eu"

``main`` drives the deploy twice: a ``dry=True`` preview first, then the real
apply. The dry pass still fetches the five keys (async factory IO is
unconditional) but parks at the first barrier for host state, so it plans one
wave deep. A converged rerun is the honest preview: run the whole program a
second time and it reports no change and queues no handler.
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
from reemote.roles import DatadogVars, datadog


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted.
        vars_set = resolve_vars(DatadogVars, "datadog_", inventory)

        # The factory is async: it fetches the five signing keys control-side
        # before returning the deploy. This IO runs even under dry.
        deploy = await datadog(vars_set)

        # Dry preview: one wave deep (parks at the first fact barrier).
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
        # or the role body's own validation gate (missing api_key, scoped-out
        # apt_repo, removed gpgkey variable).
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
