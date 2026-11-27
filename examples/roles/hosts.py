# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``hosts`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.hosts`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``hosts`` role mapping (``additional_hosts``, ``file_group``,
   ``default_ipv4_address``, ``default_ipv4_hostnames``, ``hostname_content``,
   ``hosts_content`` -- the fields of :class:`~reemote.roles.hosts.HostsVars`)
   is bound under the bare field names, and a bad value raises before any host
   is contacted. No call-site ``overrides`` are passed here, so precedence is
   just model defaults < the inventory role mapping.
3. Build the deploy -- ``hosts(vars_set)`` is the role's whole task list, one
   per-host generator -- and drive it with ``run_waves``, the barrier-aware
   driver a role needs: the default-route address read the ``/etc/hosts`` entry
   interpolates is a barrier *inside* the body, not a separate ``run()`` call.
   ``run_waves`` owns the host connections and closes them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.

Installing and running
----------------------

The role needs no extra: it renders its two file bodies in plain Python (no
Jinja2, no YAML) and writes them with the spool-and-install family. Run it
with::

    uv run python examples/roles/hosts.py

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass is one wave deep
-- and because the role's first item on the default path is the
``DefaultIpv4Address`` barrier, it parks there at once (``barrier:
DefaultIpv4Address -> unknown until run``), reading no state and applying no
request. A converged rerun is the honest preview: run the whole program a
second time and it reports no change, because both files already hold the
rendered content and their owner/group/mode already match.

What the role manages is the two *files*; the live kernel hostname is not
changed (the new name applies on reboot), exactly like the Ansible role. Hosts
outside the ``"Ubuntu"`` family emit nothing at all -- the role's family guard
is its body's opening ``if``, so such a host returns before its first ``yield``
and appears in no wave.
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
from reemote.roles import HostsVars, hosts


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's hosts role mappings over the model defaults. A rejected
        # value -- or a key that names no field -- raises here, not mid-run.
        vars_set = resolve_vars(HostsVars, "hosts", inventory)
        # The role's whole task list: one per-host generator, barrier and all.
        # A plain (non-async) factory, so it is called, not awaited.
        deploy = hosts(vars_set)

        # Dry preview: one wave deep. On the default path the role's first item
        # is the DefaultIpv4Address barrier, so the run parks there before
        # reading any address and applies no request. run_waves owns the hosts
        # it materializes and closes them itself.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: every wave, planned and applied in lockstep across hosts
        # over SSH, each barrier closing its own wave.
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
        # A rejected role variable (pydantic's ValidationError and a RoleVarsError
        # for an unknown key in the hosts mapping are both a ValueError), or the
        # role body's own hard error: a host with no default route and no
        # default_ipv4_address/hosts_content override. The latter is raised
        # after the address barrier and before any request runs, so nothing is
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
