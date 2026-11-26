# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``docker`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.docker`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner and
no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``docker`` role mapping (``edition``, ``users``,
   ``daemon_options``, ``restart_handler_state`` and the rest of the
   :class:`~reemote.roles.docker.DockerVars` fields) is bound under the bare
   field names, the
   call-site ``DOCKER_OVERRIDES`` below wins over them, and a bad value raises
   before any host is contacted.
3. Build the deploy -- ``docker(vars_set)`` is the role's whole task list, one
   per-host generator -- and drive it with ``run_waves``, the barrier-aware
   driver a role needs: its ``fact`` barriers (the ``LinuxDistribution``
   release read, and the ``Groups`` getent gate before the user memberships),
   its two checkpoints and the ``restart docker`` handler sit *inside* the
   body, not in separate ``run()`` calls. ``run_waves`` owns the host
   connections and closes them itself.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection from its ``connection`` data, gathers facts and runs commands for
real. Edit ``inventory.json`` in the current working directory to target your
machines.

``run_waves`` is a coroutine, so ``main`` is async and driven by ``asyncio.run``.
The factory itself is a plain (non-``async``) function -- the distribution and
group reads are ``fact`` barriers, not factory IO, and the signing key is fetched
by ``apt.sources_file`` itself -- so ``docker(vars_set)`` is *called*, not
awaited.

The call-site ``overrides`` channel
-----------------------------------

``DOCKER_OVERRIDES`` (``users=("supervisor",)`` plus two ``daemon_options``) is
handed to ``resolve_vars`` and wins over the inventory's ``docker`` role
mapping for every host, so the demonstration exercises the ``/etc/docker/
daemon.json`` write, the ``restart docker`` handler it notifies, and the
``docker`` group membership -- not just the package install. Overrides and the
inventory mapping both use the model's bare field names -- there is no key
prefix anywhere. Precedence is model defaults < inventory role mapping < these
overrides.

One behaviour to watch on a real host: because ``run_waves`` keeps each
connection open for the whole run, a *new* group membership lands on the next
pass -- the Ansible role's ``meta: reset_connection`` reconnected the open SSH
session so ``supervisor`` could run ``docker`` within the same play. Here, log
out and back in (or rerun) after the first pass that adds a user.

Installing and running
----------------------

The role renders nothing: its only template is ``daemon.json``, produced with
the standard library's ``json.dumps``, so no ``reemote[templates]`` extra is
needed. Run it with::

    uv run python examples/roles/docker.py

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. The dry pass is one wave deep
-- ``run_waves`` parks on the ``LinuxDistribution`` fact barrier and stops
(``barrier: LinuxDistribution -> unknown until run``), reading no further state
and applying nothing. A converged rerun is the honest preview: run the whole
program a second time and it reports no change and queues no restart, because
the repository and packages are in place, the daemon.json is byte-identical,
the service is running and enabled, and the user is already in the group -- so
neither ``Install Docker packages.`` nor ``Configure Docker daemon options.``
changes, and with no notification the handler wave is empty.

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
from reemote.roles import DockerVars, docker

# Common role variables handed to ``resolve_vars`` as call-site ``overrides``.
# ``users`` turns on the getent gate and the membership wave for the inventory's
# own SSH user, and ``daemon_options`` turns on the daemon.json write and the
# ``restart docker`` handler it notifies, so the demonstration covers the full
# stream, not just the packages. Overrides use the model's bare field
# names -- the same names the inventory's ``docker`` role mapping uses -- and
# win over it for every host.
DOCKER_OVERRIDES = {
    "users": ("supervisor",),
    "daemon_options": {
        "log-driver": "json-file",
        "log-opts": {"max-size": "10m"},
    },
}


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's docker role mapping, overlaid with the call-site
        # overrides. A rejected value raises here -- a pydantic ValidationError,
        # which is a ValueError -- not mid-run.
        vars_set = resolve_vars(DockerVars, "docker", inventory, overrides=DOCKER_OVERRIDES)
        # The role's whole task list: one per-host generator, barriers and all.
        # A plain (non-async) factory, so it is called, not awaited.
        deploy = docker(vars_set)

        # Dry preview: one wave deep. The run parks on the LinuxDistribution
        # fact barrier (the first item the body yields) and stops, reading no
        # further state and applying nothing. run_waves owns the hosts and
        # closes them.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview, one wave deep) ==")
        print(report.tree())

        # Real run: every wave, planned and applied in lockstep across hosts
        # over SSH -- the release read, the repo setup and packages, the
        # packages checkpoint, the daemon configure and its checkpoint, the
        # restart handler, then the Groups gate and the memberships.
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
        # or the role body's own hard error: the gathered distribution carries
        # no VERSION_CODENAME for the repository's Suites, or a non-empty users
        # list while the docker group does not exist after the install. Each
        # is raised at its barrier, before the requests it gates are built, so
        # nothing is half-applied.
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
