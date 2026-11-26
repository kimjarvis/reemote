# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``rustup.RustupToolchains`` fact on a real Ubuntu server.

This is a *usage* example, not part of the library. Like
``examples/modules/cargo/cargo_cargo_packages.py``, it reads a single **fact**
directly over the *real SSH transport* (asyncssh) instead of driving the
two-phase execution model -- the target hosts are not hard-coded: the
inventory comes from ``inventory.json`` in the current working directory:

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
4. Gather ``RustupToolchains`` on the guarded hosts at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out across
   the inventory with ``asyncio.gather`` and returns a host-name-keyed mapping --
   then print each host's toolchain-name -> ``RustupToolchainInfo`` mapping
   (whether each toolchain is the default, plus its release channel) with
   ``json.dumps(..., indent=2)``.

``RustupToolchains`` runs ``rustup toolchain list`` and detaches rustup's
trailing status marker from each row: the toolchain name is what precedes the
parenthesized part, and the toolchain is the default when the marker's flags --
``(default)``, ``(active)`` or ``(active, default)`` -- mention ``default``. The
channel is derived from the name: the whole leading field ``stable``, ``beta``
or ``nightly`` names those channels, everything else (a versioned install like
``1.83.0-x86_64-unknown-linux-gnu`` included) is ``custom``. The mapping comes
out sorted by toolchain name, the deterministic answer shape the module's
collection facts share.

The fact takes one optional argument, ``rustup_home``: with it the shell is
prefixed ``RUSTUP_HOME=<quoted> `` so a non-default install tree is listed;
without it the connecting user's own rustup home is read. This gather leaves it
at the default, so each host reports its own toolchains.

The design's ``requires_command = "rustup"`` gate is deliberately dropped, and
the fact command is bare: on a host without rustup it exits 127 and the gather
raises ``CommandNotFoundError`` (a ``CommandError``), because an environment
without the toolchain is an error the deployer hears about, not a silently
empty fact. To make the read a *meaningful* one, this script provisions the
toolchain first, awaiting ``provision_rustup`` from the sibling
``provision_rustup.py`` (an idempotent ``apt.packages`` install of the
``rustup`` deb) before the gather. Note that the deb installs the *installer*
only: a freshly provisioned host has downloaded no toolchain yet, so the
honest first answer is the empty ``{}`` -- exactly the design's
``default = dict`` -- until ``rustup default stable`` or a sibling provisioning
step adds one.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/rustup/rustup_rustup_toolchains.py
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
    RustupToolchains,
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
        # fact command never exits 127 (see provision_rustup.py).
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
        # fans RustupToolchains.process out with asyncio.gather and returns a
        # host-name-keyed mapping. process runs ``rustup toolchain list`` over
        # SSH and parses its marker rows into a sorted name -> info mapping;
        # each host reports its own installed toolchains.
        rustup_toolchains = await gather_fact(hosts, RustupToolchains)
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
        # the host -- and a missing rustup too: the fact command is bare, so a
        # 127 exit surfaces as ``CommandNotFoundError``, a ``CommandError``
        # subclass caught here (it only reaches this if provisioning failed).
        # Print that message -- no traceback -- and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: rustup.RustupToolchains ==")
        print(json.dumps(rustup_toolchains[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
