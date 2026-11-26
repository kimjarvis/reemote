# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Compose two shipped roles -- ``apache`` then ``php`` -- in one program.

The single-role examples (``examples/roles/apache.py``, ``examples/roles/php.py``)
answer "how do I *run* this role?". This one answers the other half: "how do I
*use* a role as a component inside my own deploy?" -- the reason a role factory
returns a plain ``Deploy`` (a callable ``host -> generator``) rather than driving
itself.

A role is library code, so a parent deploy composes it the same way it composes
any other deploy:

* ``apache(vars_set)`` and ``php(vars_set)`` are *sync* factories -- called, not
  awaited -- each returning a per-host generator that yields the role's whole
  task list, barriers and all, and then **returns its outputs**
  (:class:`~reemote.roles.apache.ApacheOutputs`,
  :class:`~reemote.roles.php.PhpOutputs`).
* ``yield from scoped("apache", apache(v))(host)`` embeds that generator in the
  parent's own body. :func:`~reemote.scoped` tags every item the child yields
  with a report path (so the wave tree shows which role emitted what), forwards
  the driver's ``send()`` values inward -- the child's barriers still receive
  their results -- and propagates the child's ``return`` value outward, which is
  what lets the parent capture the outputs with an ordinary assignment.
* ``run_waves`` returns a ``WaveReport`` and the hosts it materialized; it never
  surfaces a role's return value. So a composing program reads outputs the only
  way it can: the parent generator closes over a dict and stores what
  ``yield from`` hands back, per host.

Captured outputs are *data*, not a log line, and that is the payoff of composing:
the parent branches on them. Here it installs ``php`` only where ``apache``
actually published a version -- a host apache's family guard skipped returns
``ApacheOutputs(version="")`` before its first ``yield``, so the parent reads that
empty version and moves on instead of stacking a dependent role on a host that is
not serving.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for real.
Edit ``inventory.json`` in the current working directory to target your machines.

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.

Installing and running
----------------------

The ``apache`` role renders its ``vhosts.conf`` control-side with Jinja2, so this
program needs the template extra (``php`` renders its own templates as plain
f-strings and adds nothing)::

    pip install "reemote[templates]"

Run it with::

    uv run python examples/roles/composing_roles.py

``main`` drives the composed deploy twice, exactly like the single-role examples:
a ``dry=True`` preview first, then the real apply. The dry pass is one wave deep
-- the composed body parks on apache's first barrier (the ``apache2 -v`` probe)
*before* any capture line runs, so both output dicts stay empty. A dry run plans;
it does not publish outputs. Only the real apply populates them, which is why the
"Captured role outputs" block prints after the second ``run_waves``.

Hosts outside the ``"Ubuntu"`` family emit nothing from either role -- each
role's family guard is its body's opening ``if`` -- but they still get an entry in
each dict: apache's guard ``return``s ``ApacheOutputs(version="")``, and the
parent, reading that empty version, stores ``PhpOutputs(version=None)`` for the
host and skips php entirely.
"""

import asyncio
import json
import sys
from pathlib import Path

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    PrivilegeError,
    resolve_vars,
    run_waves,
    scoped,
    validate_inventory,
)
from reemote.roles import (
    ApacheOutputs,
    ApacheVars,
    PhpOutputs,
    PhpVars,
    apache,
    php,
)

# Each role's variables are resolved separately, from its own mapping in the
# entry's ``roles`` dict, with its own call-site ``overrides``. One inventory
# carries both roles' variables side by
# side without collision: the ``apache`` mapping binds ApacheVars, the ``php``
# mapping binds PhpVars, and neither sees the other's keys. Overrides use the
# same bare field names and win over the inventory for every host.
#
# The apache override moves it off port 80 onto 8080 with one vhost, matching
# examples/roles/apache.py, so a shared demo host does not collide with the nginx
# example. The php override raises its memory limit -- the in-code channel for a
# value the inventory does not carry.
APACHE_OVERRIDES = {
    "listen_port": 8080,
    "vhosts": (
        {"servername": "example.com", "documentroot": "/var/www/vhosts/example_com"},
    ),
}
PHP_OVERRIDES = {"memory_limit": "512M"}


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve both roles' variables before any host is contacted. A rejected
        # value raises here -- a pydantic ValidationError, which is a ValueError
        # -- not mid-run.
        apache_vars = resolve_vars(
            ApacheVars, "apache", inventory, overrides=APACHE_OVERRIDES
        )
        php_vars = resolve_vars(
            PhpVars, "php", inventory, overrides=PHP_OVERRIDES
        )

        # The parent captures each child's outputs here. run_waves never returns
        # a role's outputs, so the composing generator closes over these dicts
        # and stores what `yield from` hands back, keyed by host name.
        apache_outputs: dict[str, ApacheOutputs] = {}
        php_outputs: dict[str, PhpOutputs] = {}

        # One per-host generator that embeds both role factories -- the whole
        # lesson. A role is a Deploy, so a parent composes it with
        # `yield from scoped(...)` and captures its return value. The dict
        # assignments mutate the enclosing scope, so no `nonlocal` is needed.
        def site(host: Host):
            apache_out = yield from scoped("apache", apache(apache_vars))(host)
            apache_outputs[host.name] = apache_out

            # Consume the output as data: apache publishes version="" for a host
            # its family guard skipped, so php runs only where apache is serving.
            # That decision -- reading a child's return value to drive the parent
            # -- is why you compose instead of running two scripts.
            if not apache_out.version:
                php_outputs[host.name] = PhpOutputs(version=None)
                return

            php_out = yield from scoped("php", php(php_vars))(host)
            php_outputs[host.name] = php_out

        # Dry preview: one wave deep. The composed body parks on apache's first
        # barrier (the apache2 -v probe) before any capture line runs, so both
        # dicts stay empty -- a dry run plans, it does not publish outputs.
        # run_waves owns the hosts it materializes and closes them itself.
        report, _hosts = await run_waves(inventory, site, dry=True)
        print("\n== Wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: every wave of both roles, planned and applied in lockstep
        # across hosts over SSH, each barrier closing its own wave. The scope
        # labels ("apache", "php") now show which role emitted each line.
        report, _hosts = await run_waves(inventory, site)
        print("\n== Wave tree (apply phase) ==")
        print(report.tree())

        # The payoff: the outputs the two roles returned, read back as ordinary
        # Python values the parent can branch on, log or hand to a third role.
        print("\n== Captured role outputs ==")
        for entry in inventory:
            name = entry["host"]
            print(f"{name}: apache={apache_outputs.get(name)} php={php_outputs.get(name)}")
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
        # A rejected role variable from either resolve_vars (pydantic's
        # ValidationError is a ValueError), or a role body's own hard error:
        # apache's -v probe exited non-zero or its banner named an unsupported
        # version, or php resolved a version that is not dotted digits. Each is
        # raised before the offending role builds its configure wave, so nothing
        # is half-applied.
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
