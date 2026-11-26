# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``pip`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.pip`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``pip_``-prefixed keys (``pip_package``, ``pip_executable``,
   ``pip_install_packages`` -- the three fields of
   :class:`~reemote.roles.pip.PipVars`) are bound and stripped, the call-site
   ``PIP_OVERRIDES`` below wins over them, and a bad value raises before any host
   is contacted.
3. Build the deploy -- ``pip(vars_set)`` is the role's whole task list, one
   per-host generator -- and drive it with ``run_waves``, the barrier-aware
   driver a role needs: the interpreter-version read is a ``fact`` barrier
   *inside* the body, not a separate ``run()`` call. ``run_waves`` owns the host
   connections and closes them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.
The factory itself is a plain (non-``async``) function, so ``pip(vars_set)`` is
*called*, not awaited.

The call-site ``overrides`` channel
-----------------------------------

``PIP_OVERRIDES`` turns on the ``pip_install_packages`` loop with a version pin,
a bare name and a virtualenv entry, so the demonstration exercises the whole
stream -- the apt pip package, the ``EXTERNALLY-MANAGED`` removal and the pip
loop -- and not just the package install. Overrides use the model's UNPREFIXED
field names -- the ``pip_`` prefix is an inventory-key binding, stripped before
validation. Precedence is model defaults < inventory extra < these overrides.

Installing and running
----------------------

The role needs no extra: it renders no template and reads no YAML (the
``php``/``nodejs`` precedent). Run it with::

    uv run python examples/roles/pip.py

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass is one wave deep --
and because the role's very first item is the ``PythonVersion`` barrier, it parks
there at once (``barrier: PythonVersion -> unknown until run``), reading no
version and applying no request. A converged rerun is the honest preview: run the
whole program a second time and it reports no change, because the pip package is
installed, the ``EXTERNALLY-MANAGED`` marker is gone, and every pip package (in
its named virtualenv where it has one) is already present at its requested
version.

Hosts outside the ``"Ubuntu"`` family emit nothing at all -- the role's family
guard is its body's opening ``if``, so such a host returns before its first
``yield`` and appears in no wave. The role's RedHat branch (the EPEL dependency,
``python3-pip`` from a different repository) is out of scope, so the CentOS hosts
in the sample inventory are skipped rather than served by a second path.
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
from reemote.roles import PipVars, pip

# Common role variables handed to ``resolve_vars`` as call-site ``overrides``.
# ``install_packages`` turns on the pip loop so the demonstration covers the full
# stream, not just the package install: a version pin, a bare name and a
# virtualenv entry. Overrides use the model's UNPREFIXED field names -- the
# ``pip_`` prefix only binds inventory keys, and is stripped before validation --
# and win over any ``pip_``-prefixed key in the inventory for every host.
PIP_OVERRIDES = {
    "install_packages": [
        {"name": "ipaddress", "version": "1.0.18"},
        "colorama",
        {"name": "six", "virtualenv": "/srv/app/venv"},
    ],
}


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's pip_-prefixed keys, overlaid with the call-site overrides.
        # A rejected value raises here -- a pydantic ValidationError, which is a
        # ValueError -- not mid-run.
        vars_set = resolve_vars(PipVars, "pip_", inventory, overrides=PIP_OVERRIDES)
        # The role's whole task list: one per-host generator. A plain (non-async)
        # factory, so it is called, not awaited.
        deploy = pip(vars_set)

        # Dry preview: one wave deep. The role's first item is the PythonVersion
        # barrier, so the run parks there before reading the interpreter version
        # and applies nothing. run_waves owns the hosts it materializes and
        # closes them itself.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: every wave, planned and applied in lockstep across hosts over
        # SSH -- the interpreter-version barrier, then the apt pip package, the
        # EXTERNALLY-MANAGED removal and the pip-package loop (each virtualenv
        # item creating its venv first).
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
        # A rejected role variable (pydantic's ValidationError and a RoleVarsError
        # for an unknown pip_ key are both a ValueError), or the role body's own
        # hard error after the barrier and before any request runs -- the
        # interpreter version cannot be read. Nothing is half-applied.
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
