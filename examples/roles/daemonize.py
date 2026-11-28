# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``daemonize`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.daemonize`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``daemonize`` role mapping (``version``, ``workspace`` and ``install_path``,
   the whole :class:`~reemote.roles.daemonize.DaemonizeVars` surface) is bound
   under the bare field names, the call-site ``DAEMONIZE_OVERRIDES`` below wins
   over them, and a bad value raises before any host is contacted.
3. Build the deploy -- ``daemonize(vars_set)`` is the role's whole task list,
   one per-host generator -- and drive it with ``run_waves``, the barrier-aware
   driver a role needs: the read that decides the build (``is daemonize
   already on PATH?``) is a ``fact`` barrier *inside* the body, not a separate
   ``run()`` call. ``run_waves`` owns the host connections and closes them
   itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

The call-site ``overrides`` channel
-----------------------------------

``DAEMONIZE_OVERRIDES`` is empty: the role's three defaults (``version``
``1.7.7``, ``workspace`` ``/root``, ``install_path`` ``/usr``) are the upstream
role's own ``defaults/main.yml``, so nothing needs pinning for the shipped
inventory. It shows where a site would set them -- overrides use the model's
bare field names and win over the inventory's ``daemonize`` role mapping for
every host. Precedence is model defaults < inventory role mapping < these
overrides.

Installing and running
----------------------

No extra is needed beyond the library itself -- the role renders nothing, it is
one tarball and one build::

    pip install reemote

Run it with::

    uv run python examples/roles/daemonize.py

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass is one wave deep
-- ``run_waves`` plans the setup requests (the ``build-essential`` toolchain,
the tarball extraction), parks on the ``fact(server.Which)`` barrier and stops
(``barrier: which daemonize -> unknown until run``), gathering nothing and
applying nothing. A converged rerun is the honest preview: run the whole
program a second time and the toolchain request is a no-op, the ``creates:``
gate skips the extraction, and the ``PATH`` probe finds ``daemonize``, so the
build barrier is never emitted at all.

The real run needs outbound HTTPS to ``github.com`` on the *controller* (the
tarball is fetched control-side and spooled to the host), and the build wave
compiles as root on the host. Hosts outside the ``"Ubuntu"`` family emit
nothing at all -- the role's family guard is its body's opening ``if``, so such
a host returns before its first ``yield`` and appears in no wave. The RedHat
and Alpine entries of the shipped inventory are therefore skipped.

Sharing the demo host
---------------------

The ``ubuntu`` demo host also runs the other role examples. This role leaves a
compiled ``/usr/bin/daemonize`` (plus the source tree under ``/root``) on the
host, which no other role reads. To undo a real run::

    ssh <user>@<ubuntu-host> 'sudo rm -f /usr/bin/daemonize && sudo rm -rf /root/daemonize-release-*'
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
from reemote.roles import DaemonizeVars, daemonize

# Common role variables handed to ``resolve_vars`` as call-site ``overrides``.
# They use the model's bare field names (``version``) -- the same names the
# inventory's ``daemonize`` role mapping uses -- and win over it for every
# host. See the docstring: the shipped defaults already match upstream, so
# there is nothing to pin here; this is where a site would change them.
DAEMONIZE_OVERRIDES: dict[str, str] = {}


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's daemonize role mapping, overlaid with the call-site
        # overrides. A rejected value raises here -- a pydantic ValidationError,
        # which is a ValueError -- not mid-run.
        vars_set = resolve_vars(
            DaemonizeVars, "daemonize", inventory, overrides=DAEMONIZE_OVERRIDES
        )
        # The role's whole task list: one per-host generator, barrier and all.
        # A plain (non-async) factory, so it is called, not awaited.
        deploy = daemonize(vars_set)

        # Dry preview: one wave deep. run_waves plans the setup requests, parks
        # on the fact(server.Which) barrier and stops, applying nothing.
        # run_waves owns the hosts it materializes and closes them itself.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: every wave, planned and applied in lockstep across hosts
        # over SSH -- the setup wave, the PATH probe it closes, then (only on a
        # host the probe found bare) the build wave.
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
        # or a typoed role-mapping key (RoleVarsError). Both are raised while
        # the inventory is resolved, so no host was contacted.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command -- including a
        # build whose ``configure``/``make`` chain exits non-zero, which the
        # role raises from the barrier's own ``Result``. Print that message --
        # no traceback -- and exit non-zero so the failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
