# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``iptables.IptablesVersion`` fact on real CentOS hosts.

This is a *usage* example, not part of the library. It reads a single
**fact** directly over the *real SSH transport* (asyncssh) instead of
driving the two-phase execution model:

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
4. Gather ``IptablesVersion`` on the guarded hosts at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's version info
   with ``json.dumps(..., indent=2)``.

The fact parses ``iptables --version`` (for example
``iptables v1.8.9 (nf_tables)``) into ``{"version": ..., "backend": ...}``.
The backend matters: ``nf_tables`` means the iptables-nft translation
layer, ``legacy`` the classic kernel tables, and the two behave
differently for some match extensions -- worth knowing before debugging
a rule that "should" work. A banner without the parenthesized suffix
reads as ``legacy``; unparseable output reads ``None``. Unlike the
table-reading facts this command needs no privilege and is *not*
exit-masked: a host without the binary raises ``CommandNotFoundError``
(a ``CommandError`` subclass), which the guard below catches and reports.

The demo hosts ship without ``iptables``, and ``IptablesVersion`` is an
unmasked read, so ``main`` first drives a tiny idempotent *install
deploy* (``dnf.packages`` under ``sudo=True``, guarded by
``when=is_centos``) to put the binary on every CentOS host, then gathers
the fact. This is the same two-run split
``two_deploy_split.py`` demonstrates: a fact that needs prerequisite
software must be read after that software is installed.

Every host's connection is closed with ``aclose`` once the facts are
gathered. ``gather_fact`` and ``process`` are coroutines, so ``main`` is
async and driven by ``asyncio.run``.

The path is resolved from ``__file__``, so the example works from any
working directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/iptables/iptables_iptables_version.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    IptablesVersion,
    OperationRequest,
    PrivilegeError,
    dnf,
    gather_fact,
    op,
    run,
    validate_inventory,
)

# examples/modules/iptables/iptables_iptables_version.py -> the project root,
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


def install(host: Host) -> Generator[OperationRequest]:
    """Install the ``iptables`` binary the version fact reads.

    Branch-free and idempotent: an already-installed package plans and
    applies as no change.
    """
    yield op(
        dnf.packages,
        name="Install iptables",
        packages=("iptables",),
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    hosts: list[Host] = []
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Prerequisite run: IptablesVersion shells out to `iptables --version`,
        # an unmasked read that raises CommandNotFoundError on a host without
        # the binary. Install iptables first (idempotent, sudo=True) so the
        # fact read below succeeds on the demo hosts, which ship without it.
        # The when guard keeps the install off the non-CentOS hosts.
        await run(inventory, install)
        # Materialize every inventory entry, then guard the gather below -- a
        # skipped host never reaches the fan-out at all. gather_fact has no
        # ``when`` parameter, so filtering the host list with the predicate is
        # the equivalent of a driver-side skip.
        all_hosts = [Host.from_inventory(entry) for entry in inventory]
        hosts = [host for host in all_hosts if is_centos(host)]
        skipped = [host.name for host in all_hosts if host not in hosts]
        if skipped:
            print("\n== Skipped (when guard is_centos): " + ", ".join(skipped) + " ==")
        # Gather the fact across the guarded hosts concurrently: gather_fact
        # fans IptablesVersion.process out with asyncio.gather and returns a
        # host-name-keyed mapping. The fact takes no arguments -- it always
        # reads the binary's --version banner.
        versions = await gather_fact(hosts, IptablesVersion)
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
        # The library reports an unreachable host or a failed fact command as a
        # typed error whose message already names the host. CommandError also
        # covers CommandNotFoundError: a host without iptables installed fails
        # here with exit 127 rather than returning None. Print that message
        # -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: iptables.IptablesVersion ==")
        print(json.dumps(versions[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())