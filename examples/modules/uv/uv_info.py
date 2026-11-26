# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``uv.Info`` fact on a real Ubuntu server.

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
4. Gather ``Info`` on the guarded hosts at once with the driver's ``gather_fact``
   helper -- it fans the fact's ``process`` coroutine out across the inventory
   with ``asyncio.gather`` and returns a host-name-keyed mapping -- then print
   each host's ``InstallInfo`` record with ``json.dumps(..., indent=2)``.

``Info`` is the low-overhead installation fact: one ``&&``-chained shell asks uv
six questions in a single round trip -- ``uv --version``, ``uv tool dir``,
``uv tool dir --bin``, ``uv python dir``, ``uv cache dir`` and ``command -v uv``
-- and because each command prints exactly one stdout line the parse maps those
lines onto the record's fields positionally. Every directory therefore comes from
uv itself, reflecting the *effective* paths after the target shell's ``UV_*`` and
``XDG_*`` overrides -- the same environment a later uv operation runs in -- and
the executable is the one the chain just resolved. The version keeps only its
number: uv's leading ``uv `` token is stripped, so a Homebrew build reports
``"0.8.5 (Homebrew 2025-08-05)"`` instead of the whole line. The fact takes no
arguments.

pyinfra has no counterpart to this fact: it scatters the same questions over
``UvVersion``, ``UvToolDir``, ``UvPythonDir`` and a path lookup, each gated on its
own ``requires_command = "uv"``. That gate is deliberately dropped here and the
chain anchors on ``uv --version``, so on a host without uv the shell exits 127
and the gather raises ``CommandNotFoundError`` (a ``CommandError``) rather than
answering six empty scalars. An older uv that lacks one of the dir subcommands
short-circuits the chain instead, and the fields past that point simply read
``None``. To make the read a *meaningful* one, this script provisions uv first,
awaiting ``provision_uv`` from the sibling ``provision_uv.py`` (an idempotent
``snap.package`` install of the ``astral-uv`` snap -- Ubuntu's noble apt suite
ships no uv package) before the gather.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/uv/uv_info.py
"""

import asyncio
import json
import sys
from pathlib import Path

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    Info,
    InventoryError,
    PrivilegeError,
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
        # fans Info.process out with asyncio.gather and returns a host-name-keyed
        # mapping. process runs the one chained shell over SSH and maps its
        # stdout lines onto the InstallInfo record's fields in order; a host
        # whose chain stopped early reports None for the fields past that point.
        uv_info = await gather_fact(hosts, Info)
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
        # the host -- and a missing uv too: the chain heads with ``uv
        # --version``, so a 127 exit surfaces as ``CommandNotFoundError``, a
        # ``CommandError`` subclass caught here (it only reaches this if
        # provisioning failed). Print that message -- no traceback -- and exit
        # non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: uv.Info ==")
        print(json.dumps(uv_info[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
