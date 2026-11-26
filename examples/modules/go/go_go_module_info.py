# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``go.GoModuleInfo`` fact on a real Ubuntu server.

This is a *usage* example, not part of the library. Like
``examples/modules/gem/gem_gem_packages.py``, it reads a single **fact**
directly over the *real SSH transport* (asyncssh) instead of driving the
two-phase execution model -- the target hosts are not hard-coded: the inventory
comes from ``inventory.json`` in the current working directory:

1. Read ``inventory.json`` from the current working directory and parse it as
   JSON -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with ``validate_inventory`` so a
   malformed file fails with an ``InventoryError`` naming the offending entry,
   *before* any host is materialized or connected.
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Guard the gather with a ``when`` predicate: ``is_ubuntu`` reads only
   **static** inventory data -- ``host.family`` -- and the fact is gathered on
   the hosts that pass it. ``gather_fact`` itself takes no ``when`` parameter, so
   the predicate filters the host list before the fan-out -- the direct
   equivalent of a driver-side skip. The shared ``inventory.json`` holds one host
   in the ``"Ubuntu"`` family alongside hosts in other families, so the run
   prints its Ubuntu result plus a ``== Skipped (when guard is_ubuntu): ... ==``
   line naming every other one.
4. Gather ``GoModuleInfo`` for one module directory on the guarded hosts at once
   with the driver's ``gather_fact`` helper -- it fans the fact's ``process``
   coroutine out across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's ``GoModInfo`` answer (or
   ``None``) with ``json.dumps(..., indent=2)``.

``GoModuleInfo`` reports a project tree's ``go.mod`` metadata -- the ``module``
path, the ``go`` directive, and the ``require`` mapping of *direct* dependencies
(indirect ones deliberately dropped) -- so an operation can decide
``go mod download``/``go mod verify`` or a rebuild from the source tree already
on the host. Rather than excerpting the file with awk (which cannot reproduce
block-form ``require ( ... )`` or the ``// indirect`` markers faithfully), the
guarded command asks go itself for the authoritative machine view:
``cd '<path>' && go mod edit -json``, which performs no network access.

The demo directory is a module-level constant, the conventional checkout path
``/srv/app``; the fact keys its command by that path, so the answer is the same
question on every host. A directory without a parsable ``go.mod`` -- missing
directory, missing file, invalid syntax, or no ``go`` at all, since the command
leads with ``command -v go || exit 0`` and hides every stderr -- reads ``None``,
not an empty dict: the single-valued absence rule (the ``Which`` precedent),
where the design proposed ``{}``. The script therefore *shows* that contract: on
a freshly provisioned demo host the checkout does not exist yet, and printing
``null`` is the fact answering honestly rather than failing. To read a real
module instead, place a Go project (with its ``go.mod``) at ``MODULE_DIR`` on
the demo host before the run -- or serve a deployment that checks one out.
``provision_go`` from the sibling ``provision_go.py`` still runs first (an
idempotent ``apt.packages`` install of the ``golang-go`` deb), because without
the toolchain even an existing ``go.mod`` has nothing to parse it.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/go/go_go_module_info.py
"""

import asyncio
import json
import sys
from pathlib import Path

from reemote import (
    CommandError,
    GoModuleInfo,
    Host,
    HostConnectionError,
    InventoryError,
    PrivilegeError,
    gather_fact,
    validate_inventory,
)

# The go provisioning helper sits beside this script; add its directory to
# sys.path so ``import provision_go`` resolves whether the example is run by
# path or imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from provision_go import provision_go

# The module directory read on every host: the conventional checkout path. A
# module-level constant so both gather passes (and the fact cache) see the
# same question -- see the module docstring for the absent-directory contract.
MODULE_DIR = "/srv/app"


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows so
    both deploy phases reach the same eligibility decision. The Ubuntu demo host
    is the one ``provision_go.py`` installs the Go toolchain on before this
    gather (see the module docstring), so the gather is guarded to that family.
    """
    return host.family == "Ubuntu"


async def main() -> None:
    hosts: list[Host] = []
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Install the Go toolchain on the Ubuntu hosts before the fact is
        # gathered, so go itself can read the module directory (see
        # provision_go.py).
        print("\n== Provisioning golang-go (provision_go.py) ==")
        await provision_go(inventory)

        # Materialize every inventory entry up front, then guard the gather
        # below -- a skipped host never reaches the fan-out at all.
        all_hosts = [Host.from_inventory(entry) for entry in inventory]
        # Apply the ``when`` guard before the fan-out: gather_fact has no
        # ``when`` parameter of its own, so filtering the host list with the
        # predicate is the equivalent of the driver skipping a guarded request
        # on ineligible hosts -- the skipped hosts are never connected.
        hosts = [host for host in all_hosts if is_ubuntu(host)]
        skipped = [host.name for host in all_hosts if host not in hosts]
        if skipped:
            print("\n== Skipped (when guard is_ubuntu): " + ", ".join(skipped) + " ==")
        # Gather the fact across the guarded hosts concurrently: gather_fact
        # forwards the directory path (shlex-quoted into the guarded cd/go
        # shell) and keys the fact cache by it -- each host answers its own
        # GoModInfo entry, or None where no module lives at the path.
        modules = await gather_fact(hosts, GoModuleInfo, MODULE_DIR)
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema. The
        # message names the offending entry by its list index, and it fails
        # before any host is materialized.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library reports an unreachable host, a failed command or a
        # rejected sudo credential as a typed error whose message already names
        # the host. A missing directory or go.mod is *not* one of them -- the
        # command's guards swallow it as absence -- so the errors reaching here
        # are the connection, privilege and apt-provisioning ones. Print that
        # message -- no traceback -- and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: go.GoModuleInfo({MODULE_DIR}) ==")
        print(json.dumps(modules[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
