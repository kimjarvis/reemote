# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``flatpak.FlatpakPackage`` fact for one package on real Ubuntu servers.

This is a *usage* example, not part of the library. Like
``examples/modules/snap/snap_snap_package.py``, it reads a single **fact** directly over
the *real SSH transport* (asyncssh) instead of driving the two-phase execution model --
but the target host is not hard-coded: the inventory comes from ``inventory.json`` in
the current working directory:

1. Read ``inventory.json`` from the current working directory and parse it as JSON -- an
   inventory is just a list of dictionaries, which is exactly what JSON expresses --
   then validate it up front with ``validate_inventory`` so a malformed file fails with
   an ``InventoryError`` naming the offending entry, *before* any host is materialized
   or connected.
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Guard the gather with a ``when`` predicate, the same style as the deploy
   meta-parameter (see ``examples/demonstrations/
   composed_when_guard.py``): ``is_ubuntu`` reads only **static** inventory
   data -- ``host.family`` -- and the fact is gathered on the hosts that pass
   it. ``gather_fact`` itself takes no ``when`` parameter, so the predicate
   filters the host list before the fan-out -- the direct equivalent of a
   driver-side skip. The shared ``inventory.json`` holds one host in the
   ``"Ubuntu"`` family alongside hosts in other families, so the run prints its
   Ubuntu result plus a ``== Skipped (when guard is_ubuntu): ... ==`` line
   naming every other one.
4. Gather ``FlatpakPackage`` for the package ``org.videolan.VLC`` on the
   guarded hosts at once with the driver's ``gather_fact`` helper -- it fans
   the fact's ``process`` coroutine out across the inventory with
   ``asyncio.gather``, forwarding the package name as the fact's argument,
   and returns a host-name-keyed mapping -- then print each host's raw
   metadata dict with ``json.dumps(..., indent=2)``.

``FlatpakPackage`` runs ``flatpak info org.videolan.VLC`` and parses the three
tracked indented label rows of its output into the application's reverse-domain
``id``, its full install ``ref`` and its installed ``version``. Only the
indented label rows are read, so the unindented title line, the blank
separators and every other label (``Arch``, ``Branch``, ``License``,
``Origin``, ``Installation``, ``Installed`` and the commit block) are ignored --
exactly as the baseline's three capture groups do. An absent package (or a host
without ``flatpak``) prints no tracked row -- the command's
``2>/dev/null || true`` swallows the non-zero exit and the ``error: ... not
installed`` line goes to the suppressed stderr -- so it reports ``None``
outright, the ``apt.DebPackage`` tolerance rather than the baseline's empty ``{}``.
the baseline's ``requires_command`` gating on ``flatpak`` is deliberately dropped for
the same reason. A present package that simply prints no ``Version:`` row (a
runtime) keeps the key with its empty-string default, so the entry always
carries all three fields.

The inventory is read from ``inventory.json`` in the current working directory (edit it
there to target your machines, and point ``PACKAGE`` at the flatpak application ID you
want to inspect); hosts outside the ``"Ubuntu"`` family are skipped by the ``when``
guard.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

flatpak is *not* set up out of the box on Ubuntu -- Ubuntu ships snap, not
flatpak -- so this example provisions the tool itself before gathering:
``main`` first awaits ``provision_flatpak`` from the sibling ``install.py``,
which installs the ``flatpak`` package with ``apt.packages`` and adds the
``flathub`` remote. The fact therefore reads on a host where flatpak was never
installed. Provisioning installs only the tool and the remote, not any
application, so the ``org.videolan.VLC`` ID this example inspects still reads
as ``None`` unless it was installed beforehand -- run ``flatpak_packages.py``
(which installs and then removes VLC) to see a present package's metadata.

Run it with::

    uv run python examples/modules/flatpak/flatpak_flatpak_package.py
"""

import asyncio
import json
import sys
from pathlib import Path

from reemote import (
    CommandError,
    FlatpakPackage,
    Host,
    HostConnectionError,
    InventoryError,
    PrivilegeError,
    gather_fact,
    validate_inventory,
)

# The flatpak provisioning helper sits beside this script; add its directory
# to sys.path so ``import install`` resolves whether the example is run by
# path or imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from install import provision_flatpak

# The flatpak application whose identity to inspect: its ID, its install ref
# and its installed version.
PACKAGE = "org.videolan.VLC"



def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision. The Ubuntu demo
    host is the one ``install.py`` provisions with flatpak before this gather
    (see the module docstring), so the gather is guarded to that family.
    """
    return host.family == "Ubuntu"


async def main() -> None:
    hosts: list[Host] = []
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Provision flatpak + the flathub remote on the Ubuntu hosts before
        # the fact is gathered, so the example reads even on a host where
        # flatpak was never installed (see install.py).
        print("\n== Provisioning flatpak (install.py) ==")
        await provision_flatpak(inventory)

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
        # fans FlatpakPackage.process out with asyncio.gather, forwarding
        # PACKAGE as the fact's argument, and returns a host-name-keyed
        # mapping. process runs flatpak info over SSH and parses the tracked
        # label rows into the package's identity; an absent package reads as
        # None.
        flatpak_package = await gather_fact(hosts, FlatpakPackage, PACKAGE)
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
        # the host. The info gather itself tolerates an absent package (the
        # command ends in ``|| true``), so a CommandError here means the
        # connection broke or provisioning's remote-add failed; PrivilegeError
        # means the elevated provisioning step was rejected. Print that message
        # -- no traceback -- and exit non-zero so the failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: flatpak.FlatpakPackage({PACKAGE!r}) ==")
        print(json.dumps(flatpak_package[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
