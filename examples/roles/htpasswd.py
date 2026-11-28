# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``htpasswd`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.htpasswd`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory and validate it up front with ``validate_inventory``, so a
   malformed file fails with an ``InventoryError`` naming the offending entry
   before any host is materialized or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``htpasswd`` role mapping (``credentials``, ``required_packages``,
   ``nolog`` -- the fields of
   :class:`~reemote.roles.htpasswd.HtpasswdVars`) is bound under the bare
   field names, and a bad value raises before any host is contacted. Here the
   demo credentials arrive as call-site ``overrides``, which sit above the
   inventory mapping in precedence; in production they would live in the
   inventory (or an operator's edit of this file).
3. Build the deploy -- ``htpasswd(vars_set)`` is the role's whole task list,
   one per-host generator -- and drive it with ``run_waves``, the
   barrier-aware driver a role needs: the read-back of each managed file is a
   ``fact(FileContents)`` barrier *inside* the body, not a separate ``run()``
   call. ``run_waves`` owns the host connections and closes them itself.

Unlike the pytest suite -- which injects the test framework's
``SimulatedHost`` and never touches the network -- this example uses the
*default* factory (``Host.from_inventory``): each plain ``Host`` lazily opens
an asyncssh connection from its ``connection`` data, gathers facts and runs
commands for real. Edit ``inventory.json`` in the current working directory to
target your machines.

``run_waves`` is a coroutine, so ``main`` is async and driven by
``asyncio.run``.

Installing and running
----------------------

The role needs no extra: the hashing (``$apr1$``, ``{SHA}``, bcrypt and DES
all supported) ships as plain standard-library Python in the ``files`` module
(``files.htpasswd``), done control-side, so no password ever crosses to the
host as a command argument and nothing needs installing on the controller.
Run it with::

    uv run python examples/roles/htpasswd.py

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass plans the
package request and parks at the first ``FileContents`` barrier
(``barrier: FileContents -> unknown until run``), reading no file and applying
no request. Run the whole program a second time and the apply pass reports no
change: the packages are installed, every managed line's stored hash verifies
against the desired password (so the file content is recomputed byte-identical
despite the random salt), and the ownership and mode already match.

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
from reemote.roles import HtpasswdVars, htpasswd

# The demo's two users in one file. In production these mappings live in the
# inventory entry's ``htpasswd`` role mapping; passing them as ``overrides``
# keeps this example runnable against any inventory.
DEMO_CREDENTIALS = (
    {
        "path": "/etc/apache2/.htpasswd",
        "name": "admin",
        "password": "change-me-admin",
        "owner": "root",
        "group": "www-data",
    },
    {
        "path": "/etc/apache2/.htpasswd",
        "name": "deploy",
        "password": "change-me-deploy",
        "owner": "root",
        "group": "www-data",
        "hash_scheme": "sha1",
    },
)


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # model defaults, overlaid with the inventory's htpasswd mappings,
        # overlaid with the demo credentials. A rejected value -- a relative
        # path, a colon in a name, a non-octal mode, an unsupported hash
        # scheme, or a key that names no field -- raises here, not mid-run.
        vars_set = resolve_vars(
            HtpasswdVars,
            "htpasswd",
            inventory,
            overrides={"credentials": DEMO_CREDENTIALS},
        )
        # The role's whole task list: one per-host generator, barriers and
        # all. A plain (non-async) factory, so it is called, not awaited.
        deploy = htpasswd(vars_set)

        # Dry preview: the run parks at the first FileContents barrier,
        # reading no remote file and applying no request. run_waves owns the
        # hosts it materializes and closes them itself.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview) ==")
        print(report.tree())

        # Real run: every wave, planned and applied in lockstep across hosts
        # over SSH, each barrier closing its own wave.
        report, _hosts = await run_waves(inventory, deploy)
        print("\n== Wave tree (apply phase) ==")
        print(report.tree())
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema; the
        # message names the offending entry, and no host was contacted.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (ValueError, TypeError) as exc:
        # A rejected role variable (pydantic's ValidationError and a
        # RoleVarsError for an unknown key are both a ValueError), or the
        # role body's own hard error. Each is raised before any request runs,
        # so nothing is half-applied.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed
        # error whose message names the host and the failing command.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
