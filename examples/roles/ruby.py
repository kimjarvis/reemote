# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``ruby`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.ruby`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``ruby`` role mapping (``packages``, ``rubygems_package``,
   ``install_bundler``, ``install_gems`` -- the fields of
   :class:`~reemote.roles.ruby.RubyVars`) is bound under the bare field names,
   the call-site ``RUBY_OVERRIDES`` below wins over them, and a bad value raises
   before any host is contacted.
3. Build the deploy -- ``ruby(vars_set)`` is the role's whole task list, one
   per-host generator -- and drive it with ``run_waves``, the barrier-aware
   driver a role needs. The role yields one ``fact(FileExists, "/usr/bin/gem")``
   barrier between the provisioning requests and the gem loop, so the driver's
   wave sequencing is what keeps the loop's ``gem list`` probes from running
   before apt has landed the binary. ``run_waves`` owns the host connections
   and closes them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.
The factory itself is a plain (non-``async``) function, so ``ruby(vars_set)`` is
*called*, not awaited.

The call-site ``overrides`` channel
-----------------------------------

``RUBY_OVERRIDES`` turns on the ``install_gems`` loop with a version-pinned
system gem and a bare user-scope gem, so the demonstration exercises the whole
stream -- the apt ruby packages, the rubygems package, the profile script,
Bundler and the gem loop -- and not just the interpreter install. Overrides and
the inventory mapping both use the model's bare field names -- there is no key
prefix anywhere. Precedence is model defaults < inventory role mapping < these
overrides.

Installing and running
----------------------

The role needs no extra: it renders no template and reads no YAML (the
``php``/``pip`` precedent). Run it with::

    uv run python examples/roles/ruby.py

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. Because the role has a
barrier, the dry preview parks at it: wave 0 (the apt installs and the profile
script) is planned with its real change verdicts -- fact reads, no writes --
and the run stops at ``barrier: FileExists -> unknown until run`` without
reading anything. The real apply then runs wave 0, reads the barrier once
``gem`` is on the machine, and follows with the gem loop in wave 1. Run the
program a second time and it must report no change -- the apt packages are
installed, the cache stamp is fresh, the profile script already holds its
content, and every gem (in its chosen scope) is already present at its
requested version. That is the migration's idempotence claim, and the offline
suite asserts it.

Hosts outside the ``"Ubuntu"`` family emit nothing at all -- the role's family
guard is its body's opening ``if``, so such a host returns before its first
``yield`` and appears in no wave. The role's RedHat branch and its opt-in
build-from-source branch are out of scope, so the Alpine and CentOS entries in
the sample inventory are skipped rather than served by a second path, and an
inventory that sets ``install_from_source`` fails resolution outright.
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
from reemote.roles import RubyVars, ruby

# Common role variables handed to ``resolve_vars`` as call-site ``overrides``.
# ``install_gems`` turns on the gem loop so the demonstration covers the full
# stream: a version-pinned system-scope gem (installed with escalation) and a
# bare user-scope gem (installed into the login user's own gem directory, the
# Ansible module's default, no escalation). Overrides use the model's bare field
# names -- the same names the inventory's ``ruby`` role mapping uses -- and win
# over it for every host.
RUBY_OVERRIDES = {
    "install_gems": [
        {"name": "rake", "version": "13.2.1", "user_install": False},
        "colorize",
    ],
}


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's ruby role mappings, overlaid with the call-site overrides.
        # A rejected value raises here -- a pydantic ValidationError, which is a
        # ValueError -- not mid-run.
        vars_set = resolve_vars(RubyVars, "ruby", inventory, overrides=RUBY_OVERRIDES)
        # The role's whole task list: one per-host generator. A plain (non-async)
        # factory, so it is called, not awaited.
        deploy = ruby(vars_set)

        # Dry preview: the run plans wave 0 (the apt installs and the profile
        # script) with real change verdicts, then parks at the gem-binary
        # barrier -- nothing is read and nothing is applied. run_waves owns the
        # hosts it materializes and closes them itself.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, parked at the barrier) ==")
        print(report.tree())

        # Real run: wave 0 provisions over SSH -- the apt ruby packages with
        # the gated cache refresh, the rubygems package, the profile script.
        # The FileExists barrier then closes the wave, reads the freshly
        # landed ``gem``, and the gem loop (Bundler, then one request per
        # install_gems item) follows in wave 1.
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
        # A rejected role variable (pydantic's ValidationError, a RoleVarsError
        # for an unknown key or the scoped-out install_from_source in the ruby
        # mapping, are all a ValueError). The deploy body raises nothing: the
        # barrier's answer only steers which requests follow.
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
