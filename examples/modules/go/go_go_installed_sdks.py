# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``go.GoInstalledSDKs`` fact on a real Ubuntu server.

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
4. Gather ``GoInstalledSDKs`` with its ``search_dirs`` argument on the guarded
   hosts at once with the driver's ``gather_fact`` helper -- it fans the fact's
   ``process`` coroutine out across the inventory with ``asyncio.gather`` and
   returns a host-name-keyed mapping -- then print each host's version ->
   binary-path mapping with ``json.dumps(..., indent=2)``.

``GoInstalledSDKs`` lists the side-by-side Go SDKs a host keeps: a POSIX loop
probes every executable ``bin/go`` under a ``go*``-prefixed or any other
subdirectory of each search directory and prints its version line, and the
parser maps the ``go``-prefixed version field (its prefix stripped) to the
binary path. This script scans the tarball-install locations -- the fact's
default ``/usr/local`` and ``/opt``, where ``/usr/local/go`` and versioned
``/usr/local/go1.x`` copies live -- plus the deb layout's ``/usr/lib``, so the
``apt``-provisioned ``golang-go`` toolchain appears in the answer too. One
path matching both glob shapes lands twice and collapses in the mapping; two
SDKs reporting the same version share the key, the later scan winning.

Unlike the sibling go facts this one needs no toolchain guard at all: the loop
never invokes ``go`` itself, only the SDK binaries it finds, so it works even
on a host with no ``go`` on ``PATH`` -- and a host with none of the scan
directories answers ``{}``. The search directories are a module-level tuple so
both gather passes build the same hashable fact-cache entry. To make the read
a *meaningful* one, this script still provisions the toolchain first, awaiting
``provision_go`` from the sibling ``provision_go.py`` (an idempotent
``apt.packages`` install of the ``golang-go`` deb), so at least one SDK binary
stands on the demo host.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/go/go_go_installed_sdks.py
"""

import asyncio
import json
import sys
from pathlib import Path

from reemote import (
    CommandError,
    GoInstalledSDKs,
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

# The directories scanned: the fact's default tarball-install roots plus
# /usr/lib, where the deb layout ships ``/usr/lib/go/bin/go`` -- the shape
# provision_go.py's golang-go install produces. A tuple, so gather_fact
# hashes it into a stable fact-cache entry.
SEARCH_DIRS = ("/usr/local", "/opt", "/usr/lib")


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
        # gathered, so at least one SDK binary stands on the demo host (see
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
        # forwards the search_dirs argument, renders the scan loop with each
        # directory shlex-quoted, and keys the fact cache by the arguments --
        # every host answers its own version -> path mapping.
        sdks = await gather_fact(hosts, GoInstalledSDKs, SEARCH_DIRS)
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
        # the host. The scan itself cannot fail on a missing toolchain -- it
        # never runs ``go`` -- so the errors reaching here are the connection,
        # privilege and apt-provisioning ones. Print that message -- no
        # traceback -- and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: go.GoInstalledSDKs ==")
        print(json.dumps(sdks[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
