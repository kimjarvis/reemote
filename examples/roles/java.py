# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``java`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.java`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``java_``-prefixed keys (``java_packages``, ``java_home`` -- the two role
   variables of :class:`~reemote.roles.java.JavaVars`) are bound and stripped,
   the call-site ``JAVA_OVERRIDES`` below wins over them, and a bad value raises
   before any host is contacted.
3. Build the deploy -- ``java(vars_set)`` is the role's whole task list, one
   per-host generator -- and drive it with ``run_waves``, the barrier-aware
   driver a role needs: the ``LinuxDistribution`` barrier (the
   ``vars/Ubuntu-<major>.yml`` dispatch, which selects the package list and
   gates the ``man1`` workaround) sits *inside* the body, not in a separate
   gather-then-run split. ``run_waves`` owns the host connections and closes
   them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.
The factory itself is a plain (non-``async``) function -- the distribution read
is a ``fact`` barrier, not factory IO -- so ``java(vars_set)`` is *called*, not
awaited.

The call-site ``overrides`` channel
-----------------------------------

``JAVA_OVERRIDES`` sets ``home`` (``java_home`` in the inventory's spelling) to
a concrete JDK path, so the demonstration exercises the
``/etc/profile.d/java_home.sh`` write and not just the package install.
Overrides use the model's UNPREFIXED field names -- the ``java_`` prefix is an
inventory-key binding, stripped before validation. Precedence is model
defaults < inventory extra < these overrides. The package list deliberately
stays unset so the vars table derives it from each host's Ubuntu major version.

Installing and running
----------------------

The role renders nothing with a template engine: its one template is the
single line ``export JAVA_HOME={{ java_home }}``, reproduced byte-for-byte by a
plain f-string, so no ``reemote[templates]`` extra is needed. Run it with::

    uv run python examples/roles/java.py

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass is one wave deep
-- ``run_waves`` parks on the ``LinuxDistribution`` fact barrier and stops
(``barrier: LinuxDistribution -> unknown until run``), reading no further state
and applying nothing. A converged rerun is the honest preview: run the whole
program a second time and it reports no change, because the package is
installed, the apt cache stamp is fresh (the role's folded cache refresh is
gated on it), the man1 directory exists, and the profile script is
byte-identical with the right mode.

Hosts outside the ``"Ubuntu"`` family emit nothing at all -- the role's family
guard is its body's opening ``if``, so such a host returns before its first
``yield`` and appears in no wave. A host *declared* Ubuntu that reports another
distribution, or an Ubuntu release the role ships no vars file for, raises at
the barrier -- before any request is built, so nothing is half-installed.
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
from reemote.roles import JavaVars, java

# Common role variables handed to ``resolve_vars`` as call-site ``overrides``.
# ``home`` turns on the JAVA_HOME profile write (the template task the role
# skips when ``java_home`` is empty), so the demonstration covers the full
# stream, not just the packages. ``packages`` stays unset on purpose: the
# ``vars/Ubuntu-<major>.yml`` table then selects the OpenJDK version from each
# host's gathered release. Overrides use the model's UNPREFIXED field names --
# the ``java_`` prefix only binds inventory keys, and is stripped before
# validation -- and win over any ``java_``-prefixed key in the inventory for
# every host.
JAVA_OVERRIDES = {
    "home": "/usr/lib/jvm/java-21-openjdk-amd64",
}


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's java_-prefixed keys, overlaid with the call-site
        # overrides. A rejected value raises here -- a pydantic ValidationError,
        # which is a ValueError -- not mid-run.
        vars_set = resolve_vars(JavaVars, "java_", inventory, overrides=JAVA_OVERRIDES)
        # The role's whole task list: one per-host generator, barrier and all.
        # A plain (non-async) factory, so it is called, not awaited.
        deploy = java(vars_set)

        # Dry preview: one wave deep. The run parks on the LinuxDistribution
        # fact barrier (the first item the body yields) and stops, reading no
        # further state and applying nothing. run_waves owns the hosts and
        # closes them.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: every wave, planned and applied in lockstep across hosts
        # over SSH -- the distribution read, then the man1 directory, the
        # OpenJDK install with the folded cache refresh, and the JAVA_HOME
        # profile install plus its mode convergence.
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
        # A rejected role variable (pydantic's ValidationError is a ValueError),
        # or the role body's own hard error at the LinuxDistribution barrier:
        # the host reports a non-Ubuntu distribution, carries no readable major
        # version, or names an Ubuntu release the role ships no vars file for.
        # Each raises before the request wave is built, so nothing is
        # half-applied.
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
