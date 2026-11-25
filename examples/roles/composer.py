# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``composer`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.composer`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``composer_``-prefixed keys (``composer_path``, ``composer_version``,
   ``composer_home_owner``, ``composer_global_packages`` and the rest of
   :class:`~reemote.roles.composer.ComposerVars`) are bound and stripped, and a
   bad value raises before any host is contacted. The role's one *unprefixed*
   variable, ``php_executable``, is left in ``host.extra`` and read raw by the
   deploy body -- giving it a ``composer_`` prefix would break the role's
   inventory contract. This script passes one call-site ``override`` so the
   global-require and ``$PATH`` halves are demonstrated; the library default for
   ``global_packages`` is the role's own empty list.
3. Build the deploy -- ``composer(vars_set)`` is the role's whole task list, one
   per-host generator -- and drive it with ``run_waves``, the barrier-aware
   driver a role needs: its ``stat`` gate, its ``~/.composer`` home lookup, the
   control-side signature fetch and the installer/require actions are barriers
   *inside* the body, not separate ``run()`` calls. ``run_waves`` owns the host
   connections and closes them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.
The factory itself is a plain (non-``async``) function -- the control-side
signature fetch is a per-host ``control`` barrier inside the body, not factory
IO -- so ``composer(vars_set)`` is *called*, not awaited.

Installing and running
----------------------

The role needs no extra: both templates (``auth.json`` and the ``$PATH`` entry)
are rendered with ``json.dumps`` and an f-string, so there is no Jinja2 and no
PyYAML. Run it with::

    uv run python examples/roles/composer.py

The signature and the installer are fetched **control-side**
(``files.fetch_url`` / ``files.download``), so the machine running this script --
not the target host -- needs egress to ``composer.github.io`` and
``getcomposer.org``. The target needs PHP >= 5.4 and ``git`` on its ``$PATH``
before the run, and -- for the demonstrated global-require step -- egress to
``packagist.org`` and ``github.com``, because that step is Composer itself
downloading on the host exactly as the Ansible role does.

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass is one wave deep --
and because the role's very first item is the ``FileExists`` stat gate on the
composer binary, it parks there at once (``barrier: FileExists -> unknown until
run``), reading no state, fetching nothing and applying no request. A converged
rerun is the honest preview: run the whole program a second time and it reports
no change, because the binary exists (so the whole installer half, the signature
fetch included, is skipped) and every configured package's ``vendor`` directory
exists (so no require action re-runs).

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
from reemote.roles import ComposerVars, composer


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's composer_-prefixed keys over the model defaults. A rejected
        # value -- a relative composer_path, a scoped-out branch, a malformed
        # package list -- or a prefixed key that names no field raises here, not
        # mid-run. The one override demonstrates the global-require and $PATH
        # halves; the library default for global_packages is the role's own [].
        vars_set = resolve_vars(
            ComposerVars,
            "composer_",
            inventory,
            overrides={"global_packages": (("phpunit/phpunit", "@stable"),)},
        )
        # The role's whole task list: one per-host generator, barriers and all.
        # A plain (non-async) factory, so it is called, not awaited.
        deploy = composer(vars_set)

        # Dry preview: one wave deep. The role's first item is the FileExists
        # stat gate on the composer binary, so the run parks there before
        # reading any state, fetches nothing and applies nothing. run_waves owns
        # the hosts it materializes and closes them itself.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: every wave, planned and applied in lockstep across hosts
        # over SSH, each barrier closing its own wave. The wave count grows with
        # the configured package list -- one require action per missing package.
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
        # for an unknown composer_ key are both a ValueError), or the role body's
        # own hard error: an empty php_executable in host.extra, or a
        # composer_home_owner whose home directory cannot be resolved on the
        # target. Each is raised before any request runs, so nothing is
        # half-applied.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command -- including the
        # installer or a composer global require action that exited non-zero,
        # which the role raises as a CommandError from the barrier's own Result.
        # Print that message -- no traceback -- and exit non-zero so the failure
        # stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
