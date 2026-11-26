# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``uv.Tools`` fact on a real Ubuntu server.

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
4. Gather ``Tools`` on the guarded hosts at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out across
   the inventory with ``asyncio.gather`` and returns a host-name-keyed mapping --
   then print each host's list of ``ToolInfo`` entries (the tool name, its
   version and the binaries it linked) with ``json.dumps(..., indent=2)``.

``Tools`` answers the pipx-shaped question -- which isolated CLI tools has uv
installed, at what version, and which executables did each place on the PATH --
by running pyinfra's command, ``uv tool list``. uv has no JSON output for that
subcommand, so the plain-text grammar is parsed: a header line names the tool and
its version (``ruff 0.8.4``, the leading ``v`` of ``black v24.10.0`` stripped),
and the ``- black``/``- blackd`` bullets underneath it are the entrypoints. Each
tool comes back once, sorted by name, with its entrypoints sorted and deduped; a
header without a version -- a tool installed from a local path or a git URL --
keeps its record with ``version=None``. The fact takes no arguments.

Two pyinfra behaviours are deliberately corrected here, and one is kept. pyinfra
skips every bullet and warns a versionless header away, so it never reports the
installed binaries and answers ``{}`` for a tool uv listed without a version;
this fact keeps both. pyinfra also files uv's empty-state hint -- real uv prints
``No tools installed`` and exits 0 -- as a tool named ``No`` at version ``tools``;
here a row that is neither a header nor a bullet is noise, so an untouched host
honestly answers ``[]``. And pyinfra's ``requires_command = "uv"`` gate is
dropped: the bare command exits 127 on a host without uv and the gather raises
``CommandNotFoundError``. To make the read a *meaningful* one, this script
provisions uv first, awaiting ``provision_uv`` from the sibling
``provision_uv.py`` -- which installs the *tool* only, so unless the demo host was
used for one, the first honest answer is the empty list.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/uv/uv_tools.py
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
    Tools,
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
        # bare listing command runs against a real ``uv`` and never exits 127
        # (see provision_uv.py).
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
        # fans Tools.process out with asyncio.gather and returns a
        # host-name-keyed mapping. process runs ``uv tool list`` over SSH and
        # pairs each header line with the entrypoint bullets beneath it; uv's
        # "No tools installed" hint leaves the answer empty.
        uv_tools = await gather_fact(hosts, Tools)
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
        # the host -- and a missing uv too: the fact command is bare, so a 127
        # exit surfaces as ``CommandNotFoundError``, a ``CommandError``
        # subclass caught here (it only reaches this if provisioning failed).
        # Print that message -- no traceback -- and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: uv.Tools ==")
        print(json.dumps(uv_tools[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
