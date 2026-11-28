# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``php_versions`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.php_versions`; this script is the runnable artifact -- a
plain ``main()`` that does what every reemote program does, with no shared
runner and no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``php_versions`` role mapping (``version``, ``install_recommends`` -- the
   :class:`~reemote.roles.php_versions.PhpVersionsVars` fields) is bound under
   the bare field names, the call-site ``PHP_VERSIONS_OVERRIDES`` below wins
   over them, and a bad value raises before any host is contacted.
3. Build the deploy -- ``php_versions(vars_set)`` is the role's whole task list,
   one per-host generator -- and drive it with ``run_waves``, the barrier-aware
   driver a role needs: its ``fact`` barrier (the ``LinuxDistribution`` release
   read that supplies the Ubuntu codename the Sury repository is named for) and
   the ``setup done`` checkpoint that gates the ``php-common`` purge sit *inside*
   the body, not in separate ``run()`` calls. ``run_waves`` owns the host
   connections and closes them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.
The factory itself is a plain (non-``async``) function -- the distribution read
is a ``fact`` barrier, not factory IO, and the repository's (binary) signing key
is fetched control-side by the role's own ``files.download`` request -- so
``php_versions(vars_set)`` is *called*, not awaited.

What the role does -- and does not -- change
-------------------------------------------

Exactly like the Ansible role, this prepares a host for *one* PHP version: it
installs the Sury keyring with a ``files.download`` request (the URL serves a
binary OpenPGP key), adds the ``packages.sury.org/php`` repository referring to
that keyring (with its index refresh chained onto the sources change), purges
every *other* version's ``php<X>-common`` package from the list
``vars/Debian.yml`` carries, and -- only when that purge removed something --
purges the shared ``php-common`` package too. It installs
nothing: the chosen version is the :func:`~reemote.roles.php` role's job, which
consumes this role's :class:`~reemote.roles.php_versions.PhpVersionsOutputs`
(package names, conf paths, FPM locations) through a ``yield from
scoped("php_versions", ...)`` capture. On a real host the purge arm is the
mutating half: expect it to *remove* PHP ``-common`` packages the host had.

The call-site ``overrides`` channel
-----------------------------------

``PHP_VERSIONS_OVERRIDES`` pins ``version`` to ``"8.3"``, so the demonstration
purges the ``-common`` packages of 8.0, 8.1, 8.2 and 8.4 and publishes the
8.3-shaped outputs. Overrides and the inventory mapping both use the model's
bare field names -- there is no key prefix anywhere. Precedence is model
defaults < inventory role mapping < these overrides.

Installing and running
----------------------

The role renders nothing -- every path it derives is an f-string -- so no
``reemote[templates]`` extra is needed. Run it with::

    uv run python examples/roles/php_versions.py

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass is one wave deep
-- ``run_waves`` parks on the ``LinuxDistribution`` fact barrier and stops
(``barrier: LinuxDistribution -> unknown until run``), reading no further state
and applying nothing. A converged rerun is the honest preview: run the whole
program a second time and it reports no change and queues no second purge,
because the dependencies and repository are already in place, the other
versions' ``-common`` packages are gone -- so ``Purge other PHP versions.``
changes nothing, and with nothing changed at its checkpoint the gated
``Also purge php-common package.`` wave is empty and never appears.

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
from reemote.roles import PhpVersionsVars, php_versions

# Common role variables handed to ``resolve_vars`` as call-site ``overrides``.
# Pinning ``version`` decides which ``-common`` packages the purge removes and
# what shape the published outputs carry, so the demonstration covers the full
# stream -- repository, purge, and the checkpoint-gated ``php-common`` removal.
# Overrides use the model's bare field names -- the same names the inventory's
# ``php_versions`` role mapping uses -- and win over it for every host.
PHP_VERSIONS_OVERRIDES = {"version": "8.3"}


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's php_versions role mapping, overlaid with the call-site
        # overrides. A rejected value raises here -- a pydantic ValidationError,
        # which is a ValueError -- not mid-run.
        vars_set = resolve_vars(
            PhpVersionsVars, "php_versions", inventory, overrides=PHP_VERSIONS_OVERRIDES
        )
        # The role's whole task list: one per-host generator, barriers and all.
        # A plain (non-async) factory, so it is called, not awaited.
        deploy = php_versions(vars_set)

        # Dry preview: one wave deep. The run parks on the LinuxDistribution
        # fact barrier (the first item the body yields) and stops, reading no
        # further state and applying nothing. run_waves owns the hosts and
        # closes them.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: every wave, planned and applied in lockstep across hosts
        # over SSH -- the release read, the dependencies and repository and the
        # version purge, the setup checkpoint, then the gated php-common purge
        # on the hosts whose version purge actually removed something.
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
        # -- a ``version`` that is not dotted digits -- or the role body's own
        # hard error: the gathered distribution carries no VERSION_CODENAME, so
        # the repository could not be named. Each is raised before the requests
        # it gates are built, so nothing is half-applied.
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
