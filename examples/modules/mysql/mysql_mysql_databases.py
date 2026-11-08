# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``mysql.MysqlDatabases`` fact on two real hosts.

This is a *usage* example, not part of the library. It reads a single
**fact** directly over the *real SSH transport* (asyncssh) instead of
driving the two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``MysqlDatabases`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's name-keyed dict of
   character-set/collation pairs with ``json.dumps(..., indent=2)``.

The fact runs ``mysql -Be 'SELECT * FROM information_schema.SCHEMATA'``
as the login user -- the pyinfra ``mysql.MysqlDatabases`` query -- so that
user must be able to authenticate: keep the credentials in a host-side
``~/.my.cnf``, or pass ``mysql_user`` / ``mysql_host`` /
``mysql_defaults_file`` to ``gather_fact`` for a TCP login backed by an
option file. No password option exists -- credentials never travel
through fact arguments.

Every host's connection is closed with ``aclose`` once the facts are
gathered. ``gather_fact`` and ``process`` are coroutines, so ``main`` is
async and driven by ``asyncio.run``.

**Host prerequisite.** This example needs a running MySQL server. The demo
CentOS hosts install and start it fine (``sudo dnf install -y mysql-server``,
``sudo systemctl start mysqld``), but their ``staff_t``-confined client
cannot reach the unix socket ``/var/lib/mysql/mysql.sock`` (``ERROR 2002
... (13)`` -- SELinux denies ``staff_t`` the ``mysqld_db_t`` socket the
no-password design needs). Compile-checked and exercised by the offline
harness, not run against shared hosts; relax the demo hosts' sudo or point
``INVENTORY`` at your own MySQL machines to run live.

Run it with::

    uv run python examples/modules/mysql/mysql_mysql_databases.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    MysqlDatabases,
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
        # fans MysqlDatabases.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. process runs the SCHEMATA select and
        # parses the rows into a dict keyed by database name -- the pyinfra
        # mysql.MysqlDatabases shape.
        databases = await gather_fact(hosts, MysqlDatabases)
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
        print(f"== {host.name}: mysql.MysqlDatabases ==")
        print(json.dumps(databases[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())