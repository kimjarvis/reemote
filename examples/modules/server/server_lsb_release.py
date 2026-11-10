# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``server.LsbRelease`` fact on real CentOS hosts.

This is a *usage* example, not part of the library. Like
``examples/modules/server/server_locales.py``, it reads a single **fact**
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
4. Gather ``LsbRelease`` on the guarded hosts at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's release info with
   ``json.dumps(..., indent=2)``.

``LsbRelease`` mirrors the baseline's ``server.LsbRelease``: it runs
``lsb_release -ca`` and returns the release information as a dict with
lower-cased, last-word keys (e.g. ``{"id": "Ubuntu", "description":
"Ubuntu 18.04.3 LTS", "release": "18.04", "codename": "bionic"}``), so a
deploy can branch on a distribution's id or release. A host where
``lsb_release`` is not installed exits 127, which the library raises as
``CommandNotFoundError`` (a ``CommandError`` subclass); ``gather_fact``
aggregates the per-host errors, so the guard below reports every host
missing the command in one stderr message -- no traceback -- and a
non-zero exit.

The CentOS demo hosts do not ship ``lsb_release`` in their base repos, so
the script first installs it: a short ``run(inventory, install)`` deploy
yields ``dnf.packages`` for the ``lsb_release`` package (provided by EPEL,
which the demo hosts already enable), flagged ``sudo=True`` and guarded by
``when=is_centos`` so only the CentOS hosts are touched. The install is
idempotent -- a host that already has the binary plans no change -- and runs
before the fact is gathered, so the read never hits the missing-command
path. Point ``INVENTORY_PATH`` at a file of hosts whose package manager
provides ``lsb_release`` (or pre-install it) to target your own machines.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

The path is resolved from ``__file__``, so the example works from any
working directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/server/server_lsb_release.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    LsbRelease,
    OperationRequest,
    PrivilegeError,
    dnf,
    format_log,
    gather_fact,
    op,
    run,
    validate_inventory,
)

# examples/modules/server/server_lsb_release.py -> the project root, so the
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


def install(host: Host) -> Generator[OperationRequest]:
    """Install the ``lsb_release`` binary the fact shells out to.

    Branch-free and identical on every host: ``dnf.packages`` is idempotent
    (it compares against the installed-package fact), so a host that already
    has ``lsb_release`` plans no change. ``sudo=True`` because package
    installation writes root-owned state.
    """
    yield op(
        dnf.packages,
        name="Install lsb_release",
        packages=("lsb_release",),
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
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
    print(f"== Inventory loaded from {INVENTORY_PATH} ==")
    print(", ".join(entry["host"] for entry in inventory))

    # Install the prerequisite first: lsb_release is not in the CentOS base
    # repos (EPEL provides it), so a short deploy puts the binary in place
    # before the fact is gathered. Idempotent -- an already-installed host
    # plans no change. The when guard skips the non-CentOS hosts entirely.
    try:
        _plans, report, _hosts = await run(inventory, install)
        print("== Prerequisite: install lsb_release ==")
        assert report is not None
        print(format_log(report))
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    # Materialize every inventory entry, then guard the gather below -- a
    # skipped host never reaches the fan-out at all. gather_fact has no
    # ``when`` parameter, so filtering the host list with the predicate is
    # the equivalent of a driver-side skip.
    hosts: list[Host] = []
    try:
        all_hosts = [Host.from_inventory(entry) for entry in inventory]
        hosts = [host for host in all_hosts if is_centos(host)]
        skipped = [host.name for host in all_hosts if host not in hosts]
        if skipped:
            print("\n== Skipped (when guard is_centos): " + ", ".join(skipped) + " ==")
        # Gather the fact across the guarded hosts concurrently: gather_fact
        # fans LsbRelease.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. process runs ``lsb_release -ca`` and
        # returns the release info with normalized keys.
        releases = await gather_fact(hosts, LsbRelease)
    except (CommandError, HostConnectionError) as exc:
        # The library reports an unreachable host, a failed fact command or a
        # command that is not installed (CommandNotFoundError, a CommandError
        # subclass) as a typed error whose message already names the host.
        # Print that message -- no traceback -- and exit non-zero so the
        # failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: server.LsbRelease ==")
        print(json.dumps(releases[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())