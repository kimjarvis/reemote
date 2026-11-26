# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``uv.Pythons`` fact on a real Ubuntu server.

This is a *usage* example, not part of the library. Like
``examples/modules/uv/uv_packages.py``, it reads a single **fact** directly over
the *real SSH transport* (asyncssh) instead of driving the two-phase execution
model -- the target hosts are not hard-coded: the inventory comes from
``inventory.json`` in the current working directory:

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
4. Gather ``Pythons`` on the guarded hosts at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out across
   the inventory with ``asyncio.gather`` and returns a host-name-keyed mapping --
   then print each host's list of ``PythonInfo`` entries (uv's key, the
   implementation, the version, the resolved path and the managed flag) with
   ``json.dumps(..., indent=2)``.

``Pythons`` answers the question an install decision needs -- which interpreters
are *already present* on this host, and which of them uv owns -- with one chained
shell that runs ``uv python dir`` and then
``uv python list --only-installed --output-format=json``. The
``--only-installed`` filter matters: without it uv
prints its whole downloadable catalogue, so a machine holding no interpreter at
all would appear to hold dozens. uv's JSON carries no managed flag, which is why
the directory is asked for in the same shell -- an interpreter whose path lies
under it is a uv download (``managed=True``), anything else (``/usr/bin/python3``)
is a system interpreter uv merely discovered. Entries come back unique on
``(key, path)`` and sorted by that pair, because uv repeats one key across every
path at which it found the same interpreter. The fact takes no arguments.

pyinfra splits the same data across ``UvInstalledPythonsByImplementation`` and
``UvInstalledPythonsByVersion``, both of which default to ``--managed-python`` --
a filter that hides every system interpreter, so the managed-versus-system split
cannot be read from its answer at all. Dropping that flag keeps both kinds here,
and the keyed dict becomes an entry list so one interpreter at two paths is not
silently overwritten. pyinfra's ``requires_command = "uv"`` gate is dropped too:
the chain heads with ``uv python dir``, so a host without uv exits 127 and the
gather raises ``CommandNotFoundError``. To make the read a *meaningful* one, this
script provisions uv first, awaiting ``provision_uv`` from the sibling
``provision_uv.py`` -- which installs the *tool* only, so a bare demo host answers
with the system interpreters uv discovers, none of them managed.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/uv/uv_pythons.py
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
    PrivilegeError,
    Pythons,
    gather_fact,
    validate_inventory,
)

# The uv provisioning helper sits beside this script; add its directory to
# sys.path so ``import provision_uv`` resolves whether the example is run by
# path or imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from provision_uv import provision_uv


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows so
    both deploy phases reach the same eligibility decision. The Ubuntu demo host
    is the one ``provision_uv.py`` installs uv on before this gather (see the
    module docstring), so the gather is guarded to that family.
    """
    return host.family == "Ubuntu"


async def main() -> None:
    hosts: list[Host] = []
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Install uv on the Ubuntu hosts before the fact is gathered, so the
        # chained shell runs against a real ``uv`` and never exits 127 (see
        # provision_uv.py).
        print("\n== Provisioning uv (provision_uv.py) ==")
        await provision_uv(inventory)

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
        # fans Pythons.process out with asyncio.gather and returns a
        # host-name-keyed mapping. process runs the chained shell over SSH, uses
        # its first line as the managed Python directory and parses the JSON
        # array beneath it, flagging each interpreter managed or system.
        uv_pythons = await gather_fact(hosts, Pythons)
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
        # the host -- and a missing uv too: the chain heads with a uv command, so
        # a 127 exit surfaces as ``CommandNotFoundError``, a ``CommandError``
        # subclass caught here (it only reaches this if provisioning failed).
        # Print that message -- no traceback -- and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: uv.Pythons ==")
        print(json.dumps(uv_pythons[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
