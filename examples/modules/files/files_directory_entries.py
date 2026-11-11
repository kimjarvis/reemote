# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``files.DirectoryEntries`` fact on real CentOS hosts.

This is a *usage* example, not part of the library. Like
``examples/modules/files/files_find_files.py``, it reads a single **fact**
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
4. Gather ``DirectoryEntries`` for ``/etc/yum.repos.d`` on the guarded
   hosts at once with the driver's ``gather_fact`` helper -- it fans the
   fact's ``process`` coroutine out across the inventory with
   ``asyncio.gather`` and returns a host-name-keyed mapping -- then print
   each host's entry rows with ``json.dumps(..., indent=2)``.

``DirectoryEntries`` is a reemote *original* -- the baseline ships no
counterpart, and the stat-only ``Directory`` fact cannot answer this: it
describes the directory *itself* (owner, mode, timestamps), never the
names inside it. One shallow ``find <path> -mindepth 1 -maxdepth 1
-printf '%f\\t%y\\n'`` sweep reports each immediate entry's basename and
type without recursing (Ansible's ``find`` with ``recurse: false``, the
``with_fileglob`` parent-dir pattern): a ``DirectoryEntry`` dict with
``name`` and ``type`` -- "file", "directory", "link", "socket", "fifo",
"block" or "character", the File family's type vocabulary. Dotfiles are
included, the directory itself is not, entries come back sorted by name,
and the ``|| true`` guard masks a missing start path -- or a BSD find
without ``-printf``, the fact is GNU-only -- to ``[]``. Here the question
is the CentOS mirror of the apt legacy-``.list`` cleanup: which repo
definitions exist in ``/etc/yum.repos.d`` at all? The listing minus the
tracked ``.repo`` files is the set of stale definitions to remove --
names the deploy author does not know, discovered in one read. For
recursive enumeration with the same type distinction see the find
family (``files_find_files.py`` and siblings); for per-file metadata
``FindFilesInfo`` (``files_find_files_info.py``).

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

The path is resolved from ``__file__``, so the example works from any
working directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/files/files_directory_entries.py
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    DirectoryEntries,
    Host,
    HostConnectionError,
    InventoryError,
    gather_fact,
    validate_inventory,
)

# The CentOS package manager's repo-definition directory: every installed
# repository owns a .repo file here, and the shallow listing enumerates
# them (plus whatever else shares the directory) in one read.
REPOS_D = "/etc/yum.repos.d"

# examples/modules/files/files_directory_entries.py -> the project root,
# so the inventory is found whatever the current working directory is.
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
        # fans DirectoryEntries.process out with asyncio.gather and returns
        # a host-name-keyed mapping, so the SSH round-trips overlap instead
        # of running one host at a time. process runs the guarded shallow
        # -printf find and parses the name/type rows sorted by name.
        entries = await gather_fact(hosts, DirectoryEntries, REPOS_D)
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
        print(f"== {host.name}: files.DirectoryEntries({REPOS_D}) ==")
        print(json.dumps(entries[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())