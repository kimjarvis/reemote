# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``server.LsbRelease`` fact on two real hosts.

This is a *usage* example, not part of the library. Like
``examples/modules/server/server_locales.py``, it reads a single **fact**
directly over the *real SSH transport* (asyncssh) instead of driving the
two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``LsbRelease`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's release info with
   ``json.dumps(..., indent=2)``.

``LsbRelease`` mirrors pyinfra's ``server.LsbRelease``: it runs
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
the script first installs it: a short ``run(INVENTORY, install)`` deploy
yields ``dnf.packages`` for the ``lsb_release`` package (provided by EPEL,
which the demo hosts already enable), flagged ``sudo=True``. The install is
idempotent -- a host that already has the binary plans no change -- and runs
before the fact is gathered, so the read never hits the missing-command
path. Point ``INVENTORY`` at hosts whose package manager provides
``lsb_release`` (or pre-install it) to target your own machines.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/server/server_lsb_release.py
"""

import asyncio
import json
import sys
from collections.abc import Generator

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    LsbRelease,
    OperationRequest,
    PrivilegeError,
    dnf,
    format_log,
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
    )


async def main() -> None:
    # Install the prerequisite first: lsb_release is not in the CentOS base
    # repos (EPEL provides it), so a short deploy puts the binary in place
    # before the fact is gathered. Idempotent -- an already-installed host
    # plans no change.
    try:
        _plans, report, _hosts = await run(INVENTORY, install)
        print("== Prerequisite: install lsb_release ==")
        assert report is not None
        print(format_log(report))
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    # Materialize every inventory entry up front so the fact can be gathered on
    # all hosts at once.
    hosts = [Host.from_inventory(entry) for entry in INVENTORY]
    try:
        # Gather the fact across the whole inventory concurrently: gather_fact
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