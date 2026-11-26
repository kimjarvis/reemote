# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``postgresql`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.postgresql`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework. It is the one role example that drives **two** ``run_waves``
passes, because the role is two factories:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables once with ``resolve_vars``: the inventory
   entry's ``postgresql`` role mapping (``user``, ``databases``,
   ``hba_entries`` and the rest of the
   :class:`~reemote.roles.postgresql.PostgresqlVars` fields) is bound under the
   bare field names, and a bad value raises before any host is contacted. Both
   passes share this one ``VarsSet``.
3. **Pass A** -- ``postgresql(vars_set)`` on the ordinary inventory view, running
   as root (``sudo=True``): the distribution and FIPS barriers, install,
   initialize, the ``stat`` gate and ``initdb``, configure, the service and the
   handler. ``run_waves`` owns the host connections and closes them itself.
4. **Pass B** -- ``postgresql_data(vars_set)`` on a *second* inventory view whose
   ``privilege.sudo_user`` names the OS ``postgres`` user, so the peer-authenticated
   catalog read and the users/databases/privileges operations run as ``postgres``.

Why two passes, and not one
---------------------------

Run-as users come from the inventory's ``privilege`` section, never from a
request, and ``run_waves`` builds one host list and closes it in its own
``finally`` -- so nothing inside a role can switch the privilege view mid-run.
The system tasks run as root but the data plane runs as ``postgres``; that is two
host views, which is two ``run_waves`` passes. Pass B must come *after* pass A
has the service listening: peer authentication over the unix socket needs a live
server, which is why the ``PostgresqlRoles`` catalog read is pass B's first
barrier, not pass A's last.

The second inventory is a copy of the first with one added key per entry::

    {**entry, "privilege": {**entry.get("privilege", {}), "sudo_user": <user>}}

where ``<user>`` is that host's resolved ``user`` (``postgres`` by
default). Everything else -- connection, family, tags -- is carried through
unchanged, so pass B talks to exactly the same machines as pass A.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.

Installing and running
----------------------

The role renders its ``postgres.sh`` profile and ``pg_hba.conf`` control-side
with Jinja2 (registering the Ansible ``comment`` filter), so it needs the
template extra::

    pip install "reemote[templates]"

Run it with::

    uv run python examples/roles/postgresql.py

``main`` drives each pass twice, exactly like an operation example: a ``dry=True``
preview first, then the real apply. Pass A's dry preview is one wave deep -- its
first item is the ``LinuxDistribution`` barrier, so the run parks there at once,
gathering no state, planning nothing and applying nothing. Pass B's dry preview
parks on the ``PostgresqlRoles`` catalog barrier. A converged rerun of the whole
program is the honest preview: run it a second time and both passes report no
change and queue no handler, because the packages are installed, the cluster is
already initialized (the ``stat`` gate finds ``PG_VERSION`` and skips ``initdb``),
every rendered file is byte-identical, the unit is running and enabled, and the
users, databases and privileges already exist.

The shipped ``inventory.json``'s ``web1`` and ``web2`` entries are in the
``"RedHat"`` family, so they are the ones this role acts on; their
``postgresql`` role mappings decide the databases, users and ``pg_hba.conf``
rows. Hosts outside the ``"RedHat"`` family emit nothing at all -- the role's
family guard is its body's opening ``if``, so such a host returns before its
first ``yield`` and appears in no wave of either pass.
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
from reemote.roles import PostgresqlVars, postgresql, postgresql_data


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's postgresql role mappings over the model defaults. A
        # rejected value raises here -- a pydantic ValidationError, which is a
        # ValueError -- not mid-run. Both passes share this one VarsSet.
        vars_set = resolve_vars(PostgresqlVars, "postgresql", inventory)

        # --- Pass A: the system half, on the ordinary (root) inventory view. ---
        # A plain (non-async) factory, so it is called, not awaited. run_waves
        # owns the hosts it materializes and closes them itself.
        deploy_a = postgresql(vars_set)

        # Dry preview: one wave deep. The role's first item is the
        # LinuxDistribution barrier, so the run parks there before gathering any
        # state, plans nothing and applies nothing.
        report, _hosts = await run_waves(inventory, deploy_a, dry=True)
        print("\n== Pass A wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: every wave, planned and applied in lockstep across hosts over
        # SSH, each barrier closing its own wave. This is the pass that installs
        # the packages, initializes the cluster and starts the service -- so it
        # must complete before pass B can peer-authenticate against a live server.
        report, _hosts = await run_waves(inventory, deploy_a)
        print("\n== Pass A wave tree (apply phase) ==")
        print(report.tree())

        # --- Pass B: the data half, on a postgres-privilege inventory view. ---
        # A copy of the inventory with one added key per entry: privilege.sudo_user
        # names the resolved user, so the catalog read and the
        # users/databases/privileges operations run as that OS user. Run-as users
        # come from the inventory, never from a request, which is exactly why this
        # is a second pass and not a mid-run switch.
        pg_inventory = [
            {
                **entry,
                "privilege": {
                    **entry.get("privilege", {}),
                    "sudo_user": vars_set[entry["host"]].user,
                },
            }
            for entry in inventory
        ]
        deploy_b = postgresql_data(vars_set)

        # Dry preview: parks on the PostgresqlRoles catalog barrier, its first
        # item, which is also the reachability gate proving pass A left a server
        # listening on the unix socket.
        report, _hosts = await run_waves(pg_inventory, deploy_b, dry=True)
        print("\n== Pass B wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: users, then databases (so an owning role exists), then the
        # attribute/privilege reconciliation. No handler -- nothing in the data
        # plane notifies a restart.
        report, _hosts = await run_waves(pg_inventory, deploy_b)
        print("\n== Pass B wave tree (apply phase) ==")
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
        # or a role body's own hard error: the distribution is not the declared
        # RedHat family, no RedHat vars are pinned for its major version, a
        # config option is malformed, initdb exited non-zero, or the distribution
        # major version could not be read (TypeError). Each is raised inside the
        # body before the affected wave is built, so nothing is half-applied.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. A PrivilegeError
        # here usually means the postgres-privilege view could not elevate to the
        # run-as user. Print that message -- no traceback -- and exit non-zero so
        # the failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
