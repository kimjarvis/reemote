# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``git`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.git`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``git`` role mapping (``packages`` and the scoped-out ``install_from_source``
   of :class:`~reemote.roles.git.GitVars`) is bound under the bare field names,
   the call-site ``GIT_OVERRIDES`` below wins over them, and a bad value raises
   before any host is contacted.
3. Build the deploy -- ``git(vars_set)`` is the role's whole task list, one
   per-host generator -- and drive it with ``run_waves``, the barrier-aware
   driver a role needs. This role yields no barrier at all (no version probe,
   no ``register``/``when`` read, no handler), so the single install request
   lands in one wave; ``run_waves`` still owns the host connections and closes
   them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.
The factory itself is a plain (non-``async``) function -- there is no barrier, so
there is no factory IO either -- so ``git(vars_set)`` is *called*, not awaited.

The call-site ``overrides`` channel
-----------------------------------

``GIT_OVERRIDES`` widens ``packages`` to ``git`` plus ``git-svn``, exactly the
addition the upstream README suggests, so the demonstration exercises the
package-list variable and not just the default bare install. Overrides and the
inventory mapping both use the model's bare field names -- there is no key
prefix anywhere. Precedence is model defaults < inventory role mapping < these
overrides. ``install_from_source`` stays false on purpose: the role's
build-from-source branch is scoped out of this migration, and setting the knob
raises at resolution time before any host is contacted (deviation D2).

Installing and running
----------------------

The role templates and renders nothing, so no ``reemote[templates]`` extra is
needed. Run it with::

    uv run python examples/roles/git.py

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. Because this role yields no
barrier, the dry pass does not park early the way ``java``/``docker``/
``repo_epel`` do on their first ``fact`` barrier -- it instead *plans the whole
wave*, reading the installed-package and cache-stamp facts, and applies nothing
(deviation D1's dry shape). A converged rerun is the honest preview: run the
whole program a second time and it reports no change, because the packages are
installed and the apt cache stamp is fresh (the folded refresh is gated on it).

Hosts outside the ``"Ubuntu"`` family emit nothing at all -- the role's family
guard is its body's opening ``if``, so such a host returns before its first
``yield`` and appears in no wave. The role's RedHat branch (the ``package`` task
with its ``enablerepo``) is out of scope, so the CentOS hosts in the sample
inventory are skipped rather than served by a second path.
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
from reemote.roles import GitVars, git

# Common role variables handed to ``resolve_vars`` as call-site ``overrides``.
# ``packages`` widens the role's default bare ``git`` install with ``git-svn``,
# the addition the upstream README suggests, so the demonstration exercises the
# package-list variable end to end. Overrides use the model's bare field names
# -- the same names the inventory's ``git`` role mapping uses -- and win over it
# for every host.
GIT_OVERRIDES = {
    "packages": ["git", "git-svn"],
}


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's git role mapping, overlaid with the call-site overrides.
        # A rejected value raises here -- a pydantic ValidationError, which is a
        # ValueError -- not mid-run.
        vars_set = resolve_vars(GitVars, "git", inventory, overrides=GIT_OVERRIDES)
        # The role's whole task list: one per-host generator. A plain
        # (non-async) factory, so it is called, not awaited.
        deploy = git(vars_set)

        # Dry preview: no barrier to park on, so run_waves plans the whole wave
        # -- reading the package and cache-stamp facts -- and applies nothing.
        # run_waves owns the hosts and closes them.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, whole wave planned) ==")
        print(report.tree())

        # Real run: the single request planned and applied in lockstep across
        # hosts over SSH -- the gated apt-cache refresh folded into the
        # git-packages install.
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
        # A rejected role variable (pydantic's ValidationError is a ValueError):
        # a bad ``packages`` entry, or ``install_from_source`` set to true --
        # the scoped-out build-from-source branch, rejected before any host is
        # contacted, so nothing is half-applied.
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
