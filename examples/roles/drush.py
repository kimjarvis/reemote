# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``drush`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.drush`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``drush_``-prefixed keys (``drush_launcher_version``, ``drush_launcher_path``,
   ``drush_launcher_phar_url``, ``drush_launcher_install``) are bound and
   stripped, and a bad value -- or one of the two scoped-out opt-in flags set to
   true -- raises before any host is contacted.
3. Build the deploy -- ``drush(vars_set)`` is the role's whole task list, one
   per-host generator -- and drive it with ``run_waves``, the barrier-aware
   driver a role needs: the role's two ``fact`` barriers (the ``FileLink`` /
   ``File`` reads that answer the role's ``stat`` register) sit *inside* the
   body, not in separate ``run()`` calls. ``run_waves`` owns the host
   connections and closes them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.
The factory itself is a plain (non-``async``) function -- the phar's fetch
happens inside the ``files.download`` operation, on the control-side
``_fetch_url_bytes`` seam, not as factory or barrier IO -- so ``drush(vars_set)``
is *called*, not awaited.

Installing and running
----------------------

The role needs no extra: the launcher path has no templates at all (the role's
only Jinja2 interpolation is the phar URL's version substitution, which the
library rebuilds with a format template). Run it with::

    uv run python examples/roles/drush.py

The phar is fetched **control-side** (``files.download``) and spooled to the
host, so the machine running this script -- not the target -- needs egress to
``github.com``. The target needs PHP to *run* drush -- the launcher is a phar
that executes ``php`` -- but this role does not install PHP (the Ansible role
does not either; its README lists PHP as a prerequisite).

``main`` drives the deploy twice: a ``dry=True`` preview first, then the real
apply. The dry pass is one wave deep -- the role's first item is the
``FileLink`` fact barrier, so the run parks there (``barrier: FileLink ->
unknown until run``), reads no state and fetches nothing. This differs from the
old two-phase script, whose ``dry`` prepare pass fetched the phar control-side
to plan the sha1 comparison. A converged rerun is the honest preview: run the
whole program a second time and it reports no change -- the directory matches,
the phar's sha1 matches (``content already up to date``), the mode matches. The
phar is still fetched on every *real* pass, because the sha1 comparison must
read the body; the no-change claim is about the host, not the fetch count.

Hosts outside the ``"Ubuntu"`` family -- and hosts with
``drush_launcher_install`` false -- emit nothing at all: both gates are the
body's opening ``if``, so such a host returns before its first ``yield`` and
appears in no wave.
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
from reemote.roles import DrushVars, drush


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's drush_-prefixed keys over the model defaults. A rejected
        # value -- a relative path, an opt-in branch set true -- or a prefixed
        # key that names no field raises here, not mid-run.
        vars_set = resolve_vars(DrushVars, "drush_", inventory)

        # The role's whole task list: one per-host generator, barriers and all.
        # A plain (non-async) factory, so it is called, not awaited.
        deploy = drush(vars_set)

        # Dry preview: one wave deep. The role's first item is the FileLink
        # fact barrier on the launcher path, so the run parks there before
        # reading any state, fetching nothing and applying no request.
        # run_waves owns the hosts it materializes and closes them itself.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: the two fact barriers, then the one request wave -- the
        # symlink cleanup when needed, the directory, the phar download and the
        # executable bit. Three waves, every host, every pass.
        report, _hosts = await run_waves(inventory, deploy)
        print("\n== Wave tree (apply phase) ==")
        print(report.tree())
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON. Python's
        # own message already names the file and the reason, so print just that
        # -- no host was contacted and there is no traceback worth showing.
        # The control-side phar fetch also lands here: URLError/HTTPError are
        # OSError subclasses.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema. The
        # message names the offending entry by its list index, which is the
        # whole report -- and it fails before any host is materialized.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (ValueError, TypeError) as exc:
        # A rejected role variable: pydantic's ValidationError and a
        # RoleVarsError for an unknown drush_ key are both a ValueError, and
        # each is raised before any request runs, so nothing is half-applied.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # Every host-level failure is a typed error naming the host and the
        # failing command. Print that, no traceback, and exit non-zero so the
        # failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
