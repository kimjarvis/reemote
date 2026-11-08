# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``iptables.Ip6tablesVersion`` fact on two real hosts.

This is a *usage* example, not part of the library. It reads a single
**fact** directly over the *real SSH transport* (asyncssh) instead of
driving the two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``Ip6tablesVersion`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's version info
   with ``json.dumps(..., indent=2)``.

The fact is the IPv6 sibling of ``IptablesVersion``: it parses
``ip6tables --version`` into ``{"version": ..., "backend": ...}``. The
two families can differ on one host (iptables-nft for IPv4 alongside a
legacy ip6tables, or vice versa), so check both before assuming a
``version=6`` operation behaves like its IPv4 twin. A banner without the
parenthesized suffix reads as ``legacy``; unparseable output reads
``None``. Unlike the table-reading facts this command needs no privilege
and is *not* exit-masked: a host without the binary raises
``CommandNotFoundError`` (a ``CommandError`` subclass), which the guard
below catches and reports.

The demo hosts ship without ``ip6tables``, and ``Ip6tablesVersion`` is an
unmasked read, so ``main`` first drives a tiny idempotent *install
deploy* (``dnf.packages`` under ``sudo=True``) to put the binary on every
host, then gathers the fact. The ``iptables`` package provides both the
IPv4 and IPv6 binaries, so installing it covers this fact too. This is
the same two-run split ``two_deploy_split.py`` demonstrates: a fact that
needs prerequisite software must be read after that software is installed.

Every host's connection is closed with ``aclose`` once the facts are
gathered. ``gather_fact`` and ``process`` are coroutines, so ``main`` is
async and driven by ``asyncio.run``.

Run it with::

    uv run python examples/modules/iptables/iptables_ip6tables_version.py
"""

import asyncio
import json
import sys
from collections.abc import Generator

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    Ip6tablesVersion,
    OperationRequest,
    PrivilegeError,
    dnf,
    gather_fact,
    op,
    run,
)

# The inventory: real hosts and their SSH connection information, supplied by
# the application to the library. Host.from_inventory materializes each entry
# into a plain Host that connects over asyncssh.
INVENTORY = [
    {
        "host": "web1",
        "connection": {
            "address": "192.168.0.240",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
    {
        "host": "web2",
        "connection": {
            "address": "192.168.0.242",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
]


def install(host: Host) -> Generator[OperationRequest]:
    """Install the ``iptables`` package, which provides ``ip6tables``.

    Branch-free and idempotent: an already-installed package plans and
    applies as no change.
    """
    yield op(dnf.packages, name="Install iptables", packages=("iptables",), sudo=True)


async def main() -> None:
    # Materialize every inventory entry up front so the fact can be gathered on
    # all hosts at once.
    hosts = [Host.from_inventory(entry) for entry in INVENTORY]
    try:
        # Prerequisite run: Ip6tablesVersion shells out to `ip6tables --version`,
        # an unmasked read that raises CommandNotFoundError on a host without
        # the binary. Install iptables first (idempotent, sudo=True) -- it
        # provides ip6tables -- so the fact read below succeeds on the demo
        # hosts, which ship without it.
        await run(INVENTORY, install)
        # Gather the fact across the whole inventory concurrently: gather_fact
        # fans Ip6tablesVersion.process out with asyncio.gather and returns a
        # host-name-keyed mapping. The fact takes no arguments -- it always
        # reads the binary's --version banner.
        versions = await gather_fact(hosts, Ip6tablesVersion)
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library reports an unreachable host or a failed fact command as a
        # typed error whose message already names the host. CommandError also
        # covers CommandNotFoundError: a host without ip6tables installed fails
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
        print(f"== {host.name}: iptables.Ip6tablesVersion ==")
        print(json.dumps(versions[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())