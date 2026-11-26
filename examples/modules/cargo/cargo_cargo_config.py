# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``cargo.CargoConfig`` fact on a real Ubuntu server.

This is a *usage* example, not part of the library. Like the sibling
``cargo_cargo_packages.py``, it reads a single **fact** directly over the
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
4. Gather ``CargoConfig`` on the guarded hosts at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out across
   the inventory with ``asyncio.gather`` and returns a host-name-keyed mapping --
   then print each host's parsed configuration mapping with
   ``json.dumps(..., indent=2)``.

``CargoConfig`` reads the configuration cargo itself consults: the shell
resolves ``$CARGO_HOME`` (falling back to ``$HOME/.cargo``) and prints
``config.toml`` inside it -- or the legacy ``config`` name when no
``config.toml`` exists -- each file behind an ``[ -f ]`` test rather than the
``cat ... 2>/dev/null || true`` shape: a failing ``cat`` keeps its non-zero
exit and its stderr instead of the shell lying success about a read it did
not perform (the unchecked fact read still tolerates that failed print as
empty output, which parses to the same ``{}`` an absent file reads -- the
form records the difference rather than erasing it). The printed document is
parsed control-side with the stdlib
``tomllib``, so the answer is the mapping of registries, build settings and
target paths the host has configured. An optional ``cargo_home`` argument
replaces the host-side resolution with one ``shlex``-quoted path; this gather
leaves it at the default, so each host answers for its own cargo home.

Unlike the sibling examples this one needs no ``provision_cargo`` await: the
chain never invokes the ``cargo`` binary -- it only tests and cats two files
-- so no host can answer 127 here and the design's ``requires_command`` gate
is dropped at no cost. That also makes the empty answer common and *honest*: a
host whose cargo home holds no config file (the usual state on a machine where
cargo was never configured, including the demo host) reads ``{}`` -- the
design's ``default = dict`` -- and unparseable content reads ``{}`` too, the
total-parser rule every fact here follows: an empty answer means "no readable
configuration to compare against", never a crash mid-gather. That is exactly
the check a config-writing operation performs before it writes.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/cargo/cargo_cargo_config.py
"""

import asyncio
import json
import sys
from pathlib import Path

from reemote import (
    CargoConfig,
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    PrivilegeError,
    gather_fact,
    validate_inventory,
)


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows so
    both deploy phases reach the same eligibility decision. The gather is
    guarded to the Ubuntu demo host (see the module docstring) to match the
    other cargo examples, even though the file read itself works on any host.
    """
    return host.family == "Ubuntu"


async def main() -> None:
    hosts: list[Host] = []
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Materialize every inventory entry up front, then guard the gather
        # below -- a skipped host never reaches the fan-out at all. No
        # provisioning step: the chain only tests and cats two files, it never
        # invokes the cargo binary (see the module docstring).
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
        # fans CargoConfig.process out with asyncio.gather and returns a
        # host-name-keyed mapping. process runs the config-read chain over SSH
        # and parses the printed TOML control-side into a dict; each host
        # reports its own cargo home, {} where no readable config lives.
        cargo_config = await gather_fact(hosts, CargoConfig)
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
        # rejected elevation as a typed error whose message already names the
        # host. Neither a missing cargo nor an unreadable config surfaces
        # here: the chain runs no cargo, so no host can answer 127, and the
        # fact read is unchecked -- a failing ``cat`` on a *present but
        # unreadable* config is tolerated as empty output and reads ``{}``
        # (see the module docstring). This catch stays for anything else the
        # transport reports. Print that message -- no
        # traceback -- and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: cargo.CargoConfig ==")
        print(json.dumps(cargo_config[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
