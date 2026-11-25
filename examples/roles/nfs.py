# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``nfs`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.nfs`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``nfs_``-prefixed keys (``nfs_exports`` -- the role variable of
   :class:`~reemote.roles.nfs.NfsVars`) are bound and stripped, and a bad value
   raises before any host is contacted.
3. Build the deploy -- ``nfs(vars_set)`` is the role's whole task list and its
   handler, one per-host generator -- and drive it with ``run_waves``, the
   barrier-aware driver a role needs: the ``checkpoint("exports done")`` that
   reads the exports change and selects the ``reload nfs`` hosts, and the
   ``exportfs -ra`` command itself, both sit *inside* the body rather than in a
   gather-then-run split. ``run_waves`` owns the host connections and closes
   them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

Installing and running
----------------------

No extra dependency: the role writes ``/etc/exports`` from a plain Python join
over ``nfs_exports`` (the template is a static comment header plus one line per
export), so neither Jinja2 nor PyYAML is needed -- unlike the memcached and
apache roles. Run it with::

    uv run python examples/roles/nfs.py

The role variable channel
-------------------------

``nfs_exports`` is the role's only meaningful variable, and it belongs in the
inventory entry, riding beside ``host``/``connection`` as an extra key --
reemote's ``host_vars`` mechanism::

    [
      {
        "host": "ubuntu",
        "family": "Ubuntu",
        "connection": {"address": "192.168.0.181",
                       "username": "supervisor",
                       "key_path": "~/.ssh/id_ed25519"},
        "group": "ubuntu",
        "nfs_exports": ["/srv/nfs 192.0.2.0/24(rw,sync,no_subtree_check)"]
      }
    ]

Each line begins with the directory to export, so a non-empty list also creates
those directories and starts and enables ``nfs-kernel-server``. This script
passes no call-site ``overrides``, so the role's own default applies
(``nfs_exports: []``): ``/etc/exports`` is written with nothing but the
template's comment header and the service task stays skipped -- exactly what
running the role with no variables does. Add the key above to see the export
directories, the gated service start and the same handler.

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.
The factory itself is a plain (non-``async``) function -- the role reads no
facts, its barriers are the checkpoint and the handler command -- so
``nfs(vars_set)`` is *called*, not awaited.

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass plans the request
wave and parks on the ``checkpoint`` barrier (``run_waves`` stops there: the
notification read needs a real apply report), so nothing is applied. A
converged rerun is the honest preview: run the whole program a second time and
it reports no change anywhere, the notification set is empty, and the handler
wave never appears -- the apt cache stamp is fresh, the packages are installed,
``/etc/exports`` is byte-identical with root:root ``0644`` metadata, and (with
exports configured) the service is running and enabled.

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
from reemote.roles import NfsVars, nfs


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's nfs_-prefixed keys over the model defaults. A rejected
        # value raises here -- a pydantic ValidationError, which is a ValueError
        # -- not mid-play, where Ansible's directory loop would have failed.
        vars_set = resolve_vars(NfsVars, "nfs_", inventory)
        # The role's whole task list and handler: one per-host generator,
        # barriers and all. A plain (non-async) factory, so it is called, not
        # awaited.
        deploy = nfs(vars_set)

        # Dry preview: the request wave is planned, then the run parks on the
        # checkpoint barrier -- the notification read needs a real apply report,
        # so the handler's exportfs stays unplanned (like Ansible's --check
        # skipping handlers). Nothing is applied. run_waves owns the hosts and
        # closes them.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, parked at the checkpoint) ==")
        print(report.tree())

        # Real run: the configure wave (install, export directories, exports
        # file plus its metadata companion, gated service start/enable), the
        # checkpoint, then `reload nfs` on the notified hosts only.
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
        # A rejected role variable: pydantic's ValidationError is a ValueError,
        # raised by resolve_vars before any host is contacted. An empty or
        # path-less export line lands here too, not in the directory loop.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library reports every host-level failure as a typed error naming
        # the host and the failing command -- which includes the handler's
        # `exportfs -ra`, whose non-zero exit the role raises itself. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
