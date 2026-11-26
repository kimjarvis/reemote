# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``security`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.security`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``security`` role mapping (``ssh_port``, ``ssh_password_authentication``,
   ``ssh_allowed_users``, the two sudoers lists, ``fail2ban_enabled``,
   ``autoupdate_enabled`` and the rest of the
   :class:`~reemote.roles.security.SecurityVars` fields) is bound under the bare
   field names, the call-site ``SECURITY_OVERRIDES`` below wins over them, and a
   bad value raises before any host is contacted.
3. Build the deploy -- ``security(vars_set)`` is the role's whole task list, one
   per-host generator -- and drive it with ``run_waves``, the barrier-aware
   driver a role needs: the handler notification is a ``checkpoint`` barrier
   *inside* the body, not a second ``run()`` call. ``run_waves`` owns the host
   connections and closes them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.

The call-site ``overrides`` channel
-----------------------------------

``SECURITY_OVERRIDES`` below is handed to ``resolve_vars`` and wins over the
inventory's ``security`` role mapping for every host. Overrides and the inventory
mapping both use the model's bare field names -- there is no key prefix anywhere
-- so ``roles.security.ssh_usedns`` in the inventory and ``ssh_usedns`` here set
the same field. Precedence is model defaults < inventory role mapping < these
overrides.

The shipped ``inventory.json`` carries no ``security`` role mapping, so these
overrides decide the run: ``UseDNS no`` (one hardening directive worth showing)
and ``AllowUsers supervisor`` -- the login user of the demo host, which is the
point of the variable and also the safe value, because an ``AllowUsers`` line
that omits the user you connect as locks you out. Everything else stays at the
upstream defaults: port ``22``, the four ``"no"`` authentication directives,
Fail2Ban and unattended-upgrades on, both sudoers lists empty.

Read that last sentence as the warning it is: this is the one role in the
corpus whose default settings can end your SSH session. Never change
``ssh_port`` through a role you are connected with, and keep at least one live
session open until you have logged in again from a fresh one. Leaving
``ssh_allowed_users`` and ``ssh_allowed_groups`` empty writes neither line.

Installing and running
----------------------

The role needs **no extra**: all four templated files (``jail.local``,
``fail2ban.local``, ``10periodic`` and ``50unattended-upgrades``) render as
plain f-strings, and the ``sshd_config`` and ``sudoers`` edits are pure Python
transforms of the file's text, so neither ``jinja2`` nor ``pyyaml`` is
imported (the bundled ``jail.local.j2`` is inlined; naming any other template in
``fail2ban_custom_configuration_template`` raises).

Run it with::

    uv run python examples/roles/security.py

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass is one wave deep --
``run_waves`` plans the whole configure stage (the Fail2Ban package and files,
the sshd state, the config edits, the sudoers edits, the apt files), parks on the
``checkpoint("configure done")`` barrier and stops (``barrier: configure done ->
unknown until run``), gathering nothing and applying nothing. A converged rerun
is the honest preview: run the whole program a second time and it reports no
change and queues no handler, because the packages are installed, every directive
line is present, both file modes are set, and every unit is running (and enabled,
for Fail2Ban).

Hosts outside the ``"Ubuntu"`` family emit nothing at all -- the role's family
guard is its body's opening ``if``, so such a host returns before its first
``yield`` and appears in no wave. The RedHat and Alpine entries of the shipped
inventory are therefore skipped, which is also how the role's
``autoupdate-RedHat.yml`` and EPEL-scoped Fail2Ban install are dropped.
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
from reemote.roles import SecurityVars, security

# Common role variables handed to ``resolve_vars`` as call-site ``overrides``.
# They use the model's bare field names (``ssh_usedns``, ``ssh_allowed_users``)
# -- the same names the inventory's ``security`` role mapping uses -- and win
# over it for every host. Values that reach a config file verbatim are strings,
# exactly as upstream defines them (``"no"``, ``"false"``), and sequences are
# tuples or mappings of them, which ``resolve_vars`` freezes.
SECURITY_OVERRIDES = {
    "ssh_usedns": "no",
    "ssh_allowed_users": ("supervisor",),
}


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's security role mapping, overlaid with the call-site
        # overrides. A rejected value raises here -- a pydantic ValidationError,
        # which is a ValueError -- not mid-run.
        vars_set = resolve_vars(
            SecurityVars, "security", inventory, overrides=SECURITY_OVERRIDES
        )
        # The role's whole task list: one per-host generator, barriers and all.
        # A plain (non-async) factory, so it is called, not awaited.
        deploy = security(vars_set)

        # Dry preview: one wave deep. run_waves plans the configure stage, parks
        # on the checkpoint("configure done") barrier and stops, applying nothing
        # -- so nothing here says whether sshd will be restarted.
        # run_waves owns the hosts it materializes and closes them itself.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: both waves, planned and applied in lockstep across hosts over
        # SSH -- the configure stage and the one checkpoint that selects which
        # hosts get ``Reload systemd``, ``Restart ssh`` and ``Reload fail2ban``.
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
        # an unknown service or handler state, a name that is not a plain token,
        # a custom jail template. All are raised while the variables resolve, so
        # no host has been touched yet.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command -- including a
        # rejected ``sshd -T -f`` or ``visudo -cf`` validation, which aborts that
        # edit before the file is touched.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
