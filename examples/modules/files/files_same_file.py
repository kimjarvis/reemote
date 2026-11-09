# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``files.SameFile`` fact on real CentOS hosts.

This is a *usage* example, not part of the library. Like
``examples/modules/files/files_link.py``, it reads **facts** directly over
the *real SSH transport* (asyncssh) instead of driving the two-phase
execution model:

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with ``validate_inventory``
   so a malformed file fails with an ``InventoryError`` naming the
   offending entry, *before* any host is materialized or connected.
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Guard the gather with a ``when`` predicate: ``is_centos`` reads only
   **static** inventory data -- ``host.group`` -- and the fact is gathered
   on the hosts that pass it. ``gather_fact`` itself takes no ``when``
   parameter, so the predicate filters the host list before the fan-out --
   the direct equivalent of a driver-side skip. Hosts outside the
   ``"centos"`` group are named on a
   ``== Skipped (when guard is_centos): ... ==`` line and never connected.
4. Gather ``Link`` for ``/etc/localtime`` first -- the timezone symlink --
   to learn each host's zoneinfo target, then gather ``SameFile`` twice per
   host: once between ``/etc/localtime`` and that target, once between the
   unrelated pair ``/etc/hosts`` and ``/etc/hostname``, and print both
   verdicts with ``json.dumps(..., indent=2)``.

``SameFile`` mirrors pyinfra's ``files.SameFile``: one
``test <path> -ef <target> && echo true || echo false`` probe, always
exiting zero and returning the plain bool -- the two paths name one and
the same device *and* inode, or they do not. The ``-ef`` operator compares
*resolved* inodes, so the symlink and the zoneinfo file it points at
share one inode and answer ``True`` -- the same reading
``files.link`` uses to recognize an existing *hard* link
(``symbolic=False``), which the ``Link`` fact alone cannot see. An
unrelated pair, a missing path, or a directory answers the plain
``False``, never a failed read.

The link-target probe takes a per-host argument (each host lives in its
own timezone), so it calls ``SameFile.process`` directly per host under
``asyncio.gather``; the fixed unrelated pair goes through the
``gather_fact`` helper, which fans the fact out across the inventory and
returns a host-name-keyed mapping.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

The path is resolved from ``__file__``, so the example works from any
working directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/files/files_same_file.py
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    Link,
    SameFile,
    gather_fact,
    validate_inventory,
)

# The symlink whose target names the zoneinfo file, and the unrelated pair
# expected to answer False on any ordinary host.
LOCALTIME = "/etc/localtime"
UNRELATED = ("/etc/hosts", "/etc/hostname")

# examples/modules/files/files_same_file.py -> the project root, so the inventory
# is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries
    each carrying at least ``host`` and ``connection``. Returns the parsed
    list, ready to materialize into hosts; raises ``InventoryError`` (via
    ``validate_inventory``) when the document is not an array or an entry
    does not match the schema.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_centos(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"centos"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision.
    """
    return host.group == "centos"


async def same_as_localtime(host: Host, target: str | None) -> bool | None:
    """Compare ``/etc/localtime`` against its own link target on one host.

    ``None`` (the Link fact answered ``None`` absent or ``False`` non-link,
    so there is no target to compare) passes through as ``None`` instead of
    probing -- the fact itself never returns ``None``, this is the
    example's reading aid.
    """
    if target is None:
        return None
    return await SameFile.process(host, LOCALTIME, target)


async def main() -> None:
    hosts: list[Host] = []
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Materialize every inventory entry up front, then guard the gather
        # below -- a skipped host never reaches the fan-out at all.
        all_hosts = [Host.from_inventory(entry) for entry in inventory]
        # Apply the ``when`` guard before the fan-out: gather_fact has no
        # ``when`` parameter of its own, so filtering the host list with the
        # predicate is the equivalent of the driver skipping a guarded
        # request on ineligible hosts -- the skipped hosts are never
        # connected, exactly as a skipped request is never materialized.
        hosts = [host for host in all_hosts if is_centos(host)]
        skipped = [host.name for host in all_hosts if host not in hosts]
        if skipped:
            print("\n== Skipped (when guard is_centos): " + ", ".join(skipped) + " ==")
        # First reading: gather_fact fans Link.process out with
        # asyncio.gather and returns a host-name-keyed mapping, so both SSH
        # round-trips overlap. A dict carries link_target (the zoneinfo
        # file); False means the path is not a symlink and None it is
        # absent. datetime values are not needed here, only the target.
        links = await gather_fact(hosts, Link, LOCALTIME)
        targets: dict[str, str | None] = {}
        for host in hosts:
            info = links[host.name]
            targets[host.name] = (
                info.get("link_target") if isinstance(info, dict) else None
            )
        # Second reading: the symlink against its own target answers True
        # (same device and inode -- exactly what makes files.link's hard
        # links idempotent), gathered per host because the target differs.
        answers = await asyncio.gather(
            *(same_as_localtime(host, targets[host.name]) for host in hosts)
        )
        same_as_target = dict(zip((host.name for host in hosts), answers))
        # Third reading: the unrelated pair through the plain gather_fact
        # helper -- one fixed argument pair broadcast to every host, the
        # answer the probe gives for anything that is not one file.
        unrelated = await gather_fact(hosts, SameFile, *UNRELATED)
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
    except (CommandError, HostConnectionError) as exc:
        # The library reports an unreachable host or a failed fact command as
        # a typed error whose message already names the host. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection the fact gathering may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    verdicts: dict[str, dict[str, Any]] = {}
    for host in hosts:
        verdicts[host.name] = {
            f"{LOCALTIME} -ef {targets[host.name]}": same_as_target[host.name],
            f"{UNRELATED[0]} -ef {UNRELATED[1]}": unrelated[host.name],
        }
    # Print in inventory order (the gatherings key the results by host name).
    for host in hosts:
        print(f"== {host.name}: files.SameFile ==")
        print(json.dumps(verdicts[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
