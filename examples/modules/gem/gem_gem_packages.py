# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``gem.GemPackages`` fact on a real Ubuntu server.

This is a *usage* example, not part of the library. Like
``examples/modules/npm/npm_npm_packages.py``, it reads a single **fact**
directly over the *real SSH transport* (asyncssh) instead of driving the
two-phase execution model -- the target hosts are not hard-coded: the inventory
comes from ``inventory.json`` in the current working directory:

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
4. Gather ``GemPackages`` on the guarded hosts at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out across
   the inventory with ``asyncio.gather`` and returns a host-name-keyed mapping --
   then print each host's list of ``GemPackageInfo`` entries (a ``name`` plus one
   installed ``version``) with ``json.dumps(..., indent=2)``.

``GemPackages`` runs ``gem list --local`` and parses the ``name (versions)``
rows, so the listing reports exactly the tree ``gem install``/``gem uninstall``
mutate. Unlike the baseline's ``^([a-zA-Z0-9\\-\\+\\_]+)\\s\\(([0-9\\.]+)\\)$``
regex -- which matches only a single bare-numeric version and so silently drops a
multi-version row (``json (2.6.3, 2.6.1)``), a platform-suffixed one
(``nokogiri (1.16.5 x86_64-linux)``), a default-gem annotation
(``stringio (default: 3.0.1)``) and a name carrying a ``.`` -- reemote keeps
every row gem prints: the parenthesized versions are split on ``,``, a
``default:``/``default`` annotation is dropped (a default gem IS installed at
that version) and a platform tag is dropped by taking each version's first
whitespace token. One entry is reported per name/version pair, because gem keeps
every installed version side by side -- the counterpart of the baseline's
``dict[str, list[str]]``. Names are reported and compared verbatim (RubyGems
names are case-sensitive, so there is no normalization to share, unlike pip's PEP
503 folding); the entries come back sorted and de-duplicated. The
``*** LOCAL GEMS ***`` header and blank lines contribute nothing.

The fact takes optional arguments this script leaves at their defaults because
the demo host keeps one system tree: ``user_install`` reads the invoking user's
gem directory instead (``gem list`` has no ``--user-install`` flag, so the fact
pins ``GEM_HOME``/``GEM_PATH`` to ``gem env user_gemhome`` -- the tree a
``--user-install`` install writes), and ``gem_home``/``gem_path``/``gem_bin``
relocate the store or name a non-default ``gem`` (an rbenv/rvm shim). These are
the same arguments ``gem.packages`` passes when it reads the fact, so an
operation and this gather always describe the same store.

gem is *not* installed by default on the demo host, and the baseline's
``requires_command`` gating is deliberately dropped here: a bare ``gem`` that is
missing exits 127 and raises ``CommandNotFoundError`` rather than silently
answering an empty fact. So this script provisions the tooling first, awaiting
``provision_gem`` from the sibling ``provision_gem.py`` (an idempotent
``apt.packages`` install of the ``ruby-rubygems`` deb) before the gather. Run
``gem_packages.py`` first to see a system tree that actually holds gems.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/gem/gem_gem_packages.py
"""

import asyncio
import json
import sys
from pathlib import Path

from reemote import (
    CommandError,
    GemPackages,
    Host,
    HostConnectionError,
    InventoryError,
    PrivilegeError,
    gather_fact,
    validate_inventory,
)

# The gem provisioning helper sits beside this script; add its directory to
# sys.path so ``import provision_gem`` resolves whether the example is run by
# path or imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from provision_gem import provision_gem


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows so
    both deploy phases reach the same eligibility decision. The Ubuntu demo host
    is the one ``provision_gem.py`` installs gem on before this gather (see the
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

        # Install gem on the Ubuntu hosts before the fact is gathered, so the
        # example reads even on a host where gem was never installed (see
        # provision_gem.py).
        print("\n== Provisioning gem (provision_gem.py) ==")
        await provision_gem(inventory)

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
        # fans GemPackages.process out with asyncio.gather and returns a
        # host-name-keyed mapping. process runs ``gem list --local`` over SSH
        # and parses its rows into a sorted list of GemPackageInfo entries; the
        # default gather passes no scope argument, so each host reports its own
        # system store.
        gem_packages = await gather_fact(hosts, GemPackages)
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
        # The library reports an unreachable host, a failed fact command or a
        # rejected sudo credential as a typed error whose message already names
        # the host (a missing gem binary surfaces as CommandNotFoundError, a
        # CommandError subclass, since the baseline's requires_command gating is
        # dropped). Print that message -- no traceback -- and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: gem.GemPackages ==")
        print(json.dumps(gem_packages[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
