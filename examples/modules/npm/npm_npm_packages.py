# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``npm.NpmPackages`` fact on a real Ubuntu server.

This is a *usage* example, not part of the library. Like
``examples/modules/apt/apt_apt_packages.py``, it reads a single **fact**
directly over the *real SSH transport* (asyncssh) instead of driving the
two-phase execution model -- the target hosts are not hard-coded: the
inventory comes from the shared ``inventory.json`` in the project root:

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with
   ``validate_inventory`` so a malformed file fails with an
   ``InventoryError`` naming the offending entry, *before* any host is
   materialized or connected.
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Guard the gather with a ``when`` predicate, the same style as the deploy
   meta-parameter (see ``examples/demonstrations/composition/
   composed_when_guard.py``): ``is_ubuntu`` reads only **static** inventory
   data -- ``host.group`` -- and the fact is gathered on the hosts that pass
   it. ``gather_fact`` itself takes no ``when`` parameter, so the predicate
   filters the host list before the fan-out -- the direct equivalent of a
   driver-side skip. The shared ``inventory.json`` holds one host in the
   ``"ubuntu"`` group alongside hosts in other groups, so the run prints its
   Ubuntu result plus a ``== Skipped (when guard is_ubuntu): ... ==`` line
   naming every other one.
4. Gather ``NpmPackages`` on the guarded hosts at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a host-name-keyed
   mapping -- then print each host's list of ``NpmPackageInfo`` entries (a
   ``name`` plus the ``version`` npm reports) with ``json.dumps(...,
   indent=2)``.

``NpmPackages`` runs ``npm ls -g --depth=0 --json`` and walks the top-level
``dependencies`` map of the answer, so the listing reports exactly the tree
``npm install``/``npm uninstall`` mutate: transitive dependencies are npm's own
business and never make a request look already satisfied. The JSON document is
read from the first ``{`` onwards, so the ``npm warn`` line a live npm prints
ahead of it is discarded; an entry flagged ``missing`` (a declared dependency
that is not installed) and one with no string version contribute nothing, and
output with no JSON at all reads as the empty list. Versions keep every shape
npm prints -- ``1.1.0-beta.4``, ``2.0.1+build.7``, even the ``latest`` dist-tag
-- because the reader is npm's own JSON rather than the baseline's box-drawing
tree regex, which could not express a scoped name or any of them. Names are
reported verbatim and compared verbatim: npm's registry names are
case-sensitive, so there is no normalization step to share (unlike pip's PEP 503
folding). The entries come back sorted and de-duplicated.

The fact takes two optional arguments that this script leaves at their defaults
because the demo host keeps one global tree: ``prefix`` relocates that tree
through ``NPM_CONFIG_PREFIX``, which is how a host with several npm prefixes is
queried about the one an operation will mutate, and ``directory`` asks for a
project's own tree instead -- ``! test -d <directory> || (cd <directory> && npm
ls --depth=0 --json)``, guarded so a directory that does not exist yet answers
``[]`` rather than failing. Both are the same arguments ``npm.packages`` passes
when it reads the fact, so an operation and this gather always describe the same
store.

npm is *not* installed by default on the demo host, and the baseline's
``requires_command`` gating is deliberately dropped here: a bare ``npm`` that is
missing exits 127 and raises ``CommandNotFoundError`` rather than silently
answering an empty fact. So this script provisions the tooling first, awaiting
``provision_npm`` from the sibling ``provision_npm.py`` (an idempotent
``apt.packages`` install of the ``nodejs`` and ``npm`` debs) before the gather.
Run ``npm_packages.py`` first to see a global tree that actually holds
packages.

The path is resolved from ``__file__``, so the example works from any
working directory. Point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines; hosts outside the
``"ubuntu"`` group are skipped by the ``when`` guard.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/npm/npm_npm_packages.py
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    NpmPackages,
    PrivilegeError,
    gather_fact,
    validate_inventory,
)

# The npm provisioning helper sits beside this script; add its directory to
# sys.path so ``import provision_npm`` resolves whether the example is run
# by path or imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from provision_npm import provision_npm

# examples/modules/npm/npm_npm_packages.py -> the project root, so the
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


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"ubuntu"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision. The Ubuntu demo
    host is the one ``provision_npm.py`` installs npm on before this gather
    (see the module docstring), so the gather is guarded to that group.
    """
    return host.group == "ubuntu"


async def main() -> None:
    hosts: list[Host] = []
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Install nodejs and npm on the Ubuntu hosts before the fact is
        # gathered, so the example reads even on a host where npm was never
        # installed (see provision_npm.py).
        print("\n== Provisioning npm (provision_npm.py) ==")
        await provision_npm(inventory)

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
        # fans NpmPackages.process out with asyncio.gather and returns a
        # host-name-keyed mapping. process runs npm ls -g --depth=0 --json
        # over SSH and parses its JSON dependency map into a sorted list of
        # NpmPackageInfo entries; the default gather passes no directory or
        # prefix, so each host reports its own default global tree.
        npm_packages = await gather_fact(hosts, NpmPackages)
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
        # the host (a missing npm binary surfaces as CommandNotFoundError, a
        # CommandError subclass, since the baseline's requires_command gating is
        # dropped). Print that message -- no traceback -- and exit non-zero so
        # the failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: npm.NpmPackages ==")
        print(json.dumps(npm_packages[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
