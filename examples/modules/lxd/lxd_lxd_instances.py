# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``lxd.LxdInstances`` fact on real Ubuntu hosts.

This is a *usage* example, not part of the library. It reads a single
**fact** directly over the *real SSH transport* (asyncssh) instead of
driving the two-phase execution model:

1. Read ``inventory.json`` from the current working directory and parse it as JSON -- an
   inventory is just a list of dictionaries, which is exactly what JSON expresses --
   then validate it up front with ``validate_inventory`` so a malformed file fails with
   an ``InventoryError`` naming the offending entry, *before* any host is materialized
   or connected.
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Guard the gather with a ``when`` predicate: ``is_ubuntu`` reads only
   **static** inventory data -- ``host.family`` -- and the fact is gathered
   on the hosts that pass it. ``gather_fact`` itself takes no ``when``
   parameter, so the predicate filters the host list before the fan-out --
   the direct equivalent of a driver-side skip. Hosts outside the
   ``"Ubuntu"`` family are named on a
   ``== Skipped (when guard is_ubuntu): ... ==`` line and never connected.
4. Gather ``LxdInstances`` on the guarded hosts at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's list of instance
   entries with ``json.dumps(..., indent=2)``.

The fact runs ``lxc list --format json --project default`` as the login
user, who needs access to the LXD daemon socket (member of the ``lxd``
group). Unlike the parity ``LxdContainers`` (``--fast``, unscoped), this
is the FULL listing: every instance -- containers AND VMs -- with its
status, type, profiles, config (including the ``volatile.base_image``
fingerprint ``lxd.instance`` compares for image drift) and devices. Pass
``project`` (or ``remote``) to ``gather_fact`` to read another project's
or remote's instances.

Every host's connection is closed with ``aclose`` once the facts are
gathered. ``gather_fact`` and ``process`` are coroutines, so ``main`` is
async and driven by ``asyncio.run``.

LXD is *not* set up out of the box on Ubuntu -- it ships as a snap, not a
default install -- so this example provisions it itself before gathering:
``main`` first awaits ``provision_lxd`` from the sibling ``provision_lxd.py``,
which installs the ``lxd`` snap with ``snap.package`` (pinned to the
``6/stable`` channel) and runs ``lxd init --auto``. Both steps are
idempotent -- the install on the ``SnapPackage`` fact, the init on the
daemon's own already-configured checks -- so the fact reads even on a
host where LXD was never installed, and a re-run leaves an initialized
daemon untouched. The one prerequisite provisioning does not cover is
socket access for the fact reads: they run unprivileged as the login
user, who must be a member of the ``lxd`` group (the demo Ubuntu host
already is).

The inventory is read from ``inventory.json`` in the current working directory; edit it
there to target your machines.

Run it with::

    uv run python examples/modules/lxd/lxd_lxd_instances.py
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
    LxdInstances,
    PrivilegeError,
    gather_fact,
    validate_inventory,
)

# The LXD provisioning helper sits beside this script; add its directory
# to sys.path so ``import provision_lxd`` resolves whether the example is
# run by path or imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from provision_lxd import provision_lxd


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision. The Ubuntu
    demo host is the one ``provision_lxd.py`` provisions with LXD before
    this gather (see the module docstring), so the gather is guarded to that family.
    """
    return host.family == "Ubuntu"


async def main() -> None:
    hosts: list[Host] = []
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Provision LXD (the snap + ``lxd init --auto``) on the Ubuntu hosts
        # before the fact is gathered, so the example reads even on a host
        # where LXD was never installed (see provision_lxd.py).
        print("\n== Provisioning LXD (provision_lxd.py) ==")
        await provision_lxd(inventory)

        # Materialize every inventory entry up front, then guard the gather
        # below -- a skipped host never reaches the fan-out at all.
        all_hosts = [Host.from_inventory(entry) for entry in inventory]
        # Apply the ``when`` guard before the fan-out: gather_fact has no
        # ``when`` parameter of its own, so filtering the host list with the
        # predicate is the equivalent of the driver skipping a guarded
        # request on ineligible hosts -- the skipped hosts are never
        # connected, exactly as a skipped request is never materialized.
        hosts = [host for host in all_hosts if is_ubuntu(host)]
        skipped = [host.name for host in all_hosts if host not in hosts]
        if skipped:
            print("\n== Skipped (when guard is_ubuntu): " + ", ".join(skipped) + " ==")
        # Gather the fact across the guarded hosts concurrently: gather_fact
        # fans LxdInstances.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. process runs the full lxc list and parses
        # every instance entry (name, status, type, profiles, config, devices).
        instances = await gather_fact(hosts, LxdInstances)
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
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library reports an unreachable host, a failed fact command or a
        # rejected sudo credential as a typed error whose message already names
        # the host. A CommandError here means the connection broke or
        # provisioning's ``lxd init --auto`` failed; PrivilegeError means an
        # elevated provisioning step was rejected. Print that message -- no
        # traceback -- and exit non-zero so the failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: lxd.LxdInstances ==")
        print(json.dumps(instances[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())