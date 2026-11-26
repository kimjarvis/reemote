# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``go.GoBinaryBuildInfo`` fact on a real Ubuntu server.

This is a *usage* example, not part of the library. Like
``examples/modules/gem/gem_gem_packages.py``, it reads a single **fact**
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
4. Gather ``GoBinaryBuildInfo`` for one binary path on the guarded hosts at once
   with the driver's ``gather_fact`` helper -- it fans the fact's ``process``
   coroutine out across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's ``GoBuildInfo`` answer (or
   ``None``) with ``json.dumps(..., indent=2)``.

``GoBinaryBuildInfo`` inspects a compiled Go binary on disk through
``go version -m <path>`` and answers which compiler built it (``go_version``,
the ``go`` prefix kept verbatim) from which module (``module_path`` and
``module_version``) and with which ``settings`` -- so a deployment can verify a
service binary's provenance, or whether an on-disk binary was built with the
expected Go version and flags, without relying on mtimes or checksums. The
``build`` rows arrive in two printed forms (``-flag=value`` inside one column,
the long ``vcs.*`` fields as a separate tab column) and both map into the same
setting -> value dictionary. Unlike the ``GoPackages`` install listing, a
``(devel)`` module version is *kept* here: a ``go build`` artifact still reports
where it came from, which is exactly the question this fact answers.

The demo binary is a module-level constant, and the deb-layout toolchain path
``/usr/lib/go/bin/go`` is the one this script's own ``provision_go`` step puts
on the host -- the toolchain binary is itself a Go binary, so the read answers
with the compiler's own embedded build info; adjust the constant to a deployed
service binary to check provenance instead. A missing file or a non-Go binary
-- and a host with no ``go``, whose guarded command exits 0 on empty output --
all read ``None``, the single-valued absence rule (the ``Which`` precedent),
where the design proposed ``{}``. ``provision_go`` from the sibling
``provision_go.py`` (an idempotent ``apt.packages`` install of the
``golang-go`` deb) therefore runs first, so both the reader and the read
binary stand on the demo host.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/go/go_go_binary_build_info.py
"""

import asyncio
import json
import sys
from pathlib import Path

from reemote import (
    CommandError,
    GoBinaryBuildInfo,
    Host,
    HostConnectionError,
    InventoryError,
    PrivilegeError,
    gather_fact,
    validate_inventory,
)

# The go provisioning helper sits beside this script; add its directory to
# sys.path so ``import provision_go`` resolves whether the example is run by
# path or imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from provision_go import provision_go

# The binary read on every host: the deb-layout toolchain binary provision_go
# installs -- itself a Go binary, so go's own build info comes back. A
# module-level constant so both gather passes (and the fact cache) see the
# same question.
BINARY_PATH = "/usr/lib/go/bin/go"


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows so
    both deploy phases reach the same eligibility decision. The Ubuntu demo host
    is the one ``provision_go.py`` installs the Go toolchain on before this
    gather (see the module docstring), so the gather is guarded to that family.
    """
    return host.family == "Ubuntu"


async def main() -> None:
    hosts: list[Host] = []
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Install the Go toolchain on the Ubuntu hosts before the fact is
        # gathered -- the reader *and* the demo binary both come from the
        # golang-go deb (see provision_go.py).
        print("\n== Provisioning golang-go (provision_go.py) ==")
        await provision_go(inventory)

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
        # forwards the binary path (shlex-quoted into the guarded
        # ``go version -m`` shell) and keys the fact cache by it -- each host
        # answers its own GoBuildInfo entry, or None where the file is
        # unreadable or not a Go binary.
        binaries = await gather_fact(hosts, GoBinaryBuildInfo, BINARY_PATH)
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
        # the host. A missing or unreadable binary is *not* one of them -- the
        # command's guards swallow it as absence -- so the errors reaching here
        # are the connection, privilege and apt-provisioning ones. Print that
        # message -- no traceback -- and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: go.GoBinaryBuildInfo({BINARY_PATH}) ==")
        print(json.dumps(binaries[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
