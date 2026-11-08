# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``postgresql.PostgresqlTablespaces`` fact on two real hosts.

This is a *usage* example, not part of the library. It reads a single
**fact** directly over the *real SSH transport* (asyncssh) instead of
driving the two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``PostgresqlTablespaces`` on every host at once with the
   driver's ``gather_fact`` helper -- it fans the fact's ``process``
   coroutine out across the inventory with ``asyncio.gather`` and returns
   a host-name-keyed mapping -- then print each host's list of
   ``PostgresqlTablespace`` entries (``oid``, ``name``, ``owner``,
   ``location``) with ``json.dumps(..., indent=2)``.

The fact is a reemote5-original visibility fact (pyinfra has no tablespace
fact): it selects ``pg_tablespace`` with ``pg_tablespace_location(oid)``
for the filesystem path (the built-in ``pg_default``/``pg_global`` report
an empty location as None). Both ``postgresql.tablespace`` and
``postgresql.database`` read it -- the database operation resolves a
tablespace NAME to its OID through this fact, fixing pyinfra's comparison
of the name against the OID column. It runs ``psql -Ac '<select>'`` as the
login user, so the host's ``pg_hba.conf`` must let that OS user connect (a
matching database role, or a trust entry); pass ``psql_user`` /
``psql_host`` to ``gather_fact`` for a TCP login backed by ``~/.pgpass``
when peer authentication is not available.

Every host's connection is closed with ``aclose`` once the facts are
gathered. ``gather_fact`` and ``process`` are coroutines, so ``main`` is
async and driven by ``asyncio.run``.

**Host prerequisite.** This example needs a running PostgreSQL server. The
demo CentOS hosts install the packages fine (``sudo dnf install -y
postgresql-server``), but their ``staff_t``-confined sudo cannot initialize
the cluster: ``initdb`` cannot exec ``/usr/bin/postgres`` and
``postgresql-setup --initdb``'s ``runuser`` cannot setuid, so the server
never starts and no ``/var/run/postgresql`` socket exists for either the
login-user fact reads or the ``sudo -u postgres`` operations. Compile-checked
and exercised by the offline harness, not run against shared hosts; relax the
demo hosts' sudo or point ``INVENTORY`` at your own PostgreSQL machines to run
live.

Run it with::

    uv run python examples/modules/postgresql/postgresql_postgresql_tablespaces.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    PostgresqlTablespaces,
    gather_fact,
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


async def main() -> None:
    # Materialize every inventory entry up front so the fact can be gathered on
    # all hosts at once.
    hosts = [Host.from_inventory(entry) for entry in INVENTORY]
    try:
        # Gather the fact across the whole inventory concurrently: gather_fact
        # fans PostgresqlTablespaces.process out with asyncio.gather and
        # returns a host-name-keyed mapping, so both SSH round-trips overlap
        # instead of running one host at a time. process runs the psql select
        # over pg_tablespace and parses the rows into entries.
        tablespaces = await gather_fact(hosts, PostgresqlTablespaces)
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
        print(f"== {host.name}: postgresql.PostgresqlTablespaces ==")
        print(json.dumps(tablespaces[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())