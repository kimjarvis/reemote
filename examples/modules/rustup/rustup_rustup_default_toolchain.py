# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``rustup.RustupDefaultToolchain`` fact on a real Ubuntu server.

This is a *usage* example, not part of the library. Like the sibling
``rustup_rustup_toolchains.py``, it reads a single **fact** directly over the
*real SSH transport* (asyncssh) instead of driving the two-phase execution
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
4. Gather ``RustupDefaultToolchain`` on the guarded hosts at once with the
   driver's ``gather_fact`` helper -- it fans the fact's ``process`` coroutine
   out across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's answer: the default
   toolchain's name, or ``None`` when no toolchain carries the marker.

``RustupDefaultToolchain`` is the module's convenience short fact: instead of
scanning ``RustupToolchains`` for the entry whose ``default`` flag is set, a
caller gets the name directly. It composes its sibling -- ``process`` delegates
to ``RustupToolchains.process`` (the ``hardware.DefaultIpv4Address`` /
``NetworkDevices`` precedent for a short fact) and projects the marked entry --
so it issues exactly one ``rustup toolchain list`` shell, never a second read,
and the two facts can never disagree about what the default is. A bare
``rustup toolchain list`` prints the default as ``<name> (default)`` or, when it
also answers in its own directory, ``<name> (active, default)``; an
``(active)``-only marker is *not* a default, so the answer stays ``None``.

The fact takes one optional argument, ``rustup_home``, forwarded to the
delegated listing as a ``RUSTUP_HOME=<quoted> `` env prefix; this gather leaves
it at the default, so each host's own rustup home decides.

The design's ``requires_command = "rustup"`` gate is deliberately dropped and
the delegated command is bare: on a host without rustup the gather raises
``CommandNotFoundError`` (a ``CommandError``). This script provisions the
toolchain first, awaiting ``provision_rustup`` from the sibling
``provision_rustup.py`` (an idempotent ``apt.packages`` install of the
``rustup`` deb); note that the deb installs the *installer* only, so until a
toolchain is downloaded the honest answer is ``None`` -- the design's absent
case, not an error.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/rustup/rustup_rustup_default_toolchain.py
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
    RustupDefaultToolchain,
    gather_fact,
    validate_inventory,
)

# The rustup provisioning helper sits beside this script; add its directory to
# sys.path so ``import provision_rustup`` resolves whether the example is run by
# path or imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from provision_rustup import provision_rustup


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows so
    both deploy phases reach the same eligibility decision. The Ubuntu demo host
    is the one ``provision_rustup.py`` installs the rustup toolchain on before
    this gather (see the module docstring), so the gather is guarded to that
    family.
    """
    return host.family == "Ubuntu"


async def main() -> None:
    hosts: list[Host] = []
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Install the rustup toolchain on the Ubuntu hosts before the fact is
        # gathered, so the example reads against a real ``rustup`` and the bare
        # delegated command never exits 127 (see provision_rustup.py).
        print("\n== Provisioning rustup (provision_rustup.py) ==")
        await provision_rustup(inventory)

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
        # fans RustupDefaultToolchain.process out with asyncio.gather and
        # returns a host-name-keyed mapping. process delegates to
        # RustupToolchains -- one ``rustup toolchain list`` over SSH -- and
        # projects the (active,) default-marked entry as a name or None.
        rustup_default_toolchain = await gather_fact(hosts, RustupDefaultToolchain)
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
        # the host -- and a missing rustup too: the delegated command is bare,
        # so a 127 exit surfaces as ``CommandNotFoundError``, a
        # ``CommandError`` subclass caught here (it only reaches this if
        # provisioning failed). Print that message -- no traceback -- and exit
        # non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    # A single-valued fact: the str | None answer prints directly, JSON-spelled
    # so the absent case is visibly null rather than an empty line.
    for host in hosts:
        print(f"== {host.name}: rustup.RustupDefaultToolchain ==")
        print(json.dumps(rustup_default_toolchain[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
