# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``files.FindInFiles`` fact on real CentOS hosts.

This is a *usage* example, not part of the library. Like
``examples/modules/files/files_find_in_file.py``, it reads a single **fact**
directly over the *real SSH transport* (asyncssh) instead of driving the
two-phase execution model:

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
4. Gather ``FindInFiles`` for the ``.repo`` files under ``/etc/yum.repos.d``
   whose *content* still names the end-of-life CentOS mirror endpoint on the
   guarded hosts at once with the driver's ``gather_fact`` helper -- it fans
   the fact's ``process`` coroutine out across the inventory with
   ``asyncio.gather`` and returns a host-name-keyed mapping -- then print
   each host's matching paths with ``json.dumps(..., indent=2)``.

``FindInFiles`` is a reemote original that closes the gap between the
*name*-selecting find family (``FindFiles``/``FindDirectories``/``FindLinks``)
and the *single-file* content grep of ``FindInFile``: one recursive
``grep -rlE <pattern> <path>`` returns the **paths** of every file under a
tree whose content matches the extended regex, so a whole directory is
scanned in a single round-trip. Unlike ``FindInFile`` it never reads as
``None`` -- the ``|| true`` guard masks no-match, an absent path and a
missing grep binary alike to ``[]`` -- because a recursive search over a tree
has no single "the file is missing" answer to distinguish. The pattern is an
extended regex (``-E``); ``interpolate_variables=True`` lets the remote shell
expand ``$VAR`` references in it instead of quoting it literally. Here the
question is the "delete unmanaged files matching content" cleanup idiom:
which repo files still point at ``mirrorlist.centos.org``, the endpoint retired
when CentOS 8 reached end of life. This one-fact read feeds the gather side
of a two-deploy split -- gather the offending paths in one deploy, then remove
the surplus with ``files.file`` in the next -- exactly the way ``FindFiles``
backs the conf.d cleanup.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

The path is resolved from ``__file__``, so the example works from any
working directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/files/files_find_in_files.py
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    FindInFiles,
    Host,
    HostConnectionError,
    InventoryError,
    gather_fact,
    validate_inventory,
)

# The CentOS repository drop-in directory and the retired mirror endpoint the
# read hunts for: every .repo file whose content still names it is a stale,
# unmanaged source a cleanup deploy would remove. The pattern is an extended
# regex (grep -E), so the dots are escaped to match literally.
REPOS_DIR = "/etc/yum.repos.d"
PATTERN = r"mirrorlist\.centos\.org"

# examples/modules/files/files_find_in_files.py -> the project root, so the
# inventory is found whatever the current working directory is.
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
        # Gather the fact across the guarded hosts concurrently: gather_fact
        # fans FindInFiles.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so the SSH round-trips overlap instead of
        # running one host at a time. process runs the recursive grep: the
        # paths of the files whose content matches, [] when nothing matches
        # (or the directory is absent).
        matches = await gather_fact(hosts, FindInFiles, REPOS_DIR, PATTERN)
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
        # The library reports an unreachable host or a failed fact command as a
        # typed error whose message already names the host. Print that message
        # -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: files.FindInFiles({REPOS_DIR}, {PATTERN!r}) ==")
        print(json.dumps(matches[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
