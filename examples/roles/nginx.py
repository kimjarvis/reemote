# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``nginx`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.nginx`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``nginx`` role mapping (``ppa_use``, ``vhosts``,
   ``remove_default_vhost``, ``service_state`` and the rest of the
   :class:`~reemote.roles.nginx.NginxVars` fields) is bound under the bare
   field names, the call-site
   ``NGINX_OVERRIDES`` below wins over them, and a bad value raises before any
   host is contacted.
3. Build the deploy -- ``nginx(vars_set)`` is the role's whole task list, one
   per-host generator -- and drive it with ``run_waves``, the barrier-aware
   driver a role needs: its PPA gate, its CPU-count read and its two checkpoints
   are barriers *inside* the body, not separate ``run()`` calls. ``run_waves``
   owns the host connections and closes them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.

The call-site ``overrides`` channel
-----------------------------------

``NGINX_OVERRIDES`` (``remove_default_vhost=True`` and one ``example.com`` vhost)
is handed to ``resolve_vars`` and wins over the inventory's ``nginx`` role
mapping for every host. Overrides and the inventory mapping both use the
model's bare field names -- there is no key prefix anywhere -- so
``roles.nginx.remove_default_vhost`` in the inventory and
``remove_default_vhost`` here set the same field. Precedence is model defaults
< inventory role mapping < these overrides. The shipped ``inventory.json``
carries no ``nginx`` role mapping, so these overrides decide the site: nginx
drops the packaged
``default`` vhost and serves one ``example.com`` vhost out of ``/var/www/example``
on port ``80``.

Installing and running
----------------------

The role renders both ``nginx.conf.j2`` and ``vhost.j2`` control-side with
Jinja2, so it needs the template extra::

    pip install "reemote[templates]"

Run it with::

    uv run python examples/roles/nginx.py

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass is one wave deep --
``run_waves`` plans the setup requests (``dirmngr``, plus the PPA when
``ppa_use`` is set), parks on the ``checkpoint("setup done")`` barrier and
stops (``barrier: setup done -> unknown until run``), gathering nothing and
applying nothing. A converged rerun is the honest preview: run the whole program
a second time and it reports no change and queues no handler, because the
packages are installed, the cache stamp is fresh, every vhost file is
byte-identical, ``nginx.conf`` matches, and the unit is running and enabled.

Hosts outside the ``"Ubuntu"`` family emit nothing at all -- the role's family
guard is its body's opening ``if``, so such a host returns before its first
``yield`` and appears in no wave. The RedHat and Alpine entries of the shipped
inventory are therefore skipped.

Sharing the demo host with ``apache``
-------------------------------------

The ``ubuntu`` demo host also runs the Apache example. This role leaves nginx
listening on ``80`` and enabled, which collides with Apache's default port; the
Apache example moves to ``8080`` to avoid it. To stop and disable nginx again
after a real run::

    ssh <user>@<ubuntu-host> 'sudo systemctl disable --now nginx'
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
from reemote.roles import NginxVars, nginx

# Common role variables handed to ``resolve_vars`` as call-site ``overrides``.
# They use the model's bare field names (``remove_default_vhost``,
# ``vhosts``) -- the same names the inventory's ``nginx`` role mapping uses --
# and win over it for every host. A vhost is the same mapping the inventory's
# ``roles.nginx.vhosts`` carries; ``resolve_vars`` validates it into a frozen
# ``VHost``, so the tuple stays hashable.
NGINX_OVERRIDES = {
    "remove_default_vhost": True,
    "vhosts": (
        {
            "server_name": "example.com",
            "root": "/var/www/example",
            "index": "index.html index.htm",
        },
    ),
}


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's nginx role mapping, overlaid with the call-site
        # overrides. A rejected value raises here -- a pydantic ValidationError,
        # which is a ValueError -- not mid-run.
        vars_set = resolve_vars(
            NginxVars, "nginx", inventory, overrides=NGINX_OVERRIDES
        )
        # The role's whole task list: one per-host generator, barriers and all.
        # A plain (non-async) factory, so it is called, not awaited.
        deploy = nginx(vars_set)

        # Dry preview: one wave deep. run_waves plans the setup requests, parks
        # on the checkpoint("setup done") barrier and stops, applying nothing.
        # run_waves owns the hosts it materializes and closes them itself.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: every wave, planned and applied in lockstep across hosts over
        # SSH, each barrier closing its own wave -- the PPA gate, the CPU-count
        # read, the configure checkpoint and the two handler channels.
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
        # or the role body's own hard error: the CPU count for the
        # worker_processes default could not be determined. Each is raised
        # before the configure wave is built, so nothing is half-applied.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command -- including a
        # rejected ``nginx -t`` validation, which aborts the config install.
        # Print that message -- no traceback -- and exit non-zero so the failure
        # stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
