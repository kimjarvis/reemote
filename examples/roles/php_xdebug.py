# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``php_xdebug`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.php_xdebug`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and no
framework:

1. Read the target hosts from ``inventory.json`` in the current working directory
   and validate it up front with ``validate_inventory``, so a malformed file fails
   with an ``InventoryError`` naming the offending entry before any host is
   materialized or connected.
2. Resolve **two** roles' variables. ``php_xdebug`` depends on
   :mod:`reemote.roles.php` and takes its dependency's ``VarsSet`` as an argument,
   so both models are resolved from the same inventory here. The prefixes nest --
   ``php_`` contains ``php_xdebug_`` -- so the php side passes
   ``nested_prefixes=("php_xdebug_",)``: without it, ``php_xdebug_version`` carries
   the ``php_`` prefix, names no ``PhpVars`` field, and ``resolve_vars`` reports
   another role's variable as this role's typo.
3. Build the deploy -- ``php_xdebug(xdebug_v, php_v)`` -- and drive it with
   ``run_waves``. One call is enough: the role's dependency is a ``yield from``
   inside its own body, so php's distribution read, its three checkpoints and its
   handler wave run as part of this stream, ahead of xdebug's setup wave, its
   build gate and its own checkpoint. There is nothing to sequence here by hand.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost`` and
never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh connection
from its ``connection`` data, gathers facts and runs commands for real. Edit
``inventory.json`` in the current working directory to target your machines.

Two requirements the role cannot satisfy from the control machine alone:

* the **control** machine needs internet egress to ``xdebug.org`` -- the tarball is
  fetched control-side by ``files.unarchive`` and spooled to the host;
* the **host** needs ``tar``, ``phpize`` (from the dependency's
  ``php<version>-dev`` package, which php installs on this same stream) and a C
  toolchain (``build-essential``, which the role installs itself).

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.

Installing and running
----------------------

The role's one template is pure variable substitution, rendered as a Python
f-string, so no template extra is needed::

    uv run python examples/roles/php_xdebug.py

``main`` drives the deploy twice, exactly like an operation example: a ``dry=True``
preview first, then the real apply. The dry pass is one wave deep, and because the
role's very first item is php's ``LinuxDistribution`` barrier, it parks there at
once -- ``barrier: LinuxDistribution -> unknown until run`` -- gathering no state,
planning nothing and applying nothing. A converged rerun is the honest preview: run
the whole program a second time and the packages are installed, the tarball is
extracted, ``modules/xdebug.so`` exists so the build action is skipped, the module
and every ini are byte-identical and no handler is queued.

On a *fresh* host the tree shows ``Restart apache2 (handler)`` twice, in two
different waves: once from php's own handler wave, because php changed something,
and once from xdebug's, because the ini write notified it. That is faithful to the
roles' two separate handler sets and to the driver having no de-duplication --
php's notifications are deliberately not folded into xdebug's checkpoint, since
borrowing them would restart the webserver for php's reasons rather than Xdebug's.

Hosts outside the ``"Ubuntu"`` family emit nothing at all: php's family guard
returns a ``PhpOutputs`` with no version, and the role returns on that before its
first own request.
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
from reemote.roles import PhpVars, PhpXdebugVars, php_xdebug


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Two roles, one inventory. The dependency's variables are resolved with
        # the nested prefix declared, so `php_xdebug_*` keys stay the child role's
        # rather than being reported as the parent's typos.
        php_v = resolve_vars(PhpVars, "php_", inventory, nested_prefixes=("php_xdebug_",))
        xdebug_v = resolve_vars(PhpXdebugVars, "php_xdebug_", inventory)

        # The role's whole task list -- dependency included. A plain (non-async)
        # factory, so it is called, not awaited.
        deploy = php_xdebug(xdebug_v, php_v)

        # Dry preview: one wave deep. The stream's first item is php's
        # LinuxDistribution barrier, so the run parks there before gathering any
        # state, plans nothing and applies nothing. run_waves owns the hosts it
        # materializes and closes them itself.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: every wave, planned and applied in lockstep across hosts over
        # SSH, each barrier closing its own wave -- php's waves first, because the
        # body yields them before its own.
        report, _hosts = await run_waves(inventory, deploy)
        print("\n== Wave tree (apply phase) ==")
        print(report.tree())
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON. Python's own
        # message already names the file and the reason, so print just that -- no
        # host was contacted and there is no traceback worth showing.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema. The message
        # names the offending entry by its list index, which is the whole report --
        # and it fails before any host is materialized.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (ValueError, TypeError) as exc:
        # A rejected role variable (pydantic's ValidationError is a ValueError, and
        # a prefixed key that names no field is a RoleVarsError), or the role body's
        # own hard error -- the dependency's PHP version is not dotted digits. Both
        # are raised before anything of xdebug's is applied.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # Every host-level failure is a typed error naming the host and the failing
        # command -- here, most likely the ``phpize && ./configure && make`` build.
        # Print that message, no traceback, and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
