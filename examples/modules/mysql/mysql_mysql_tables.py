# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``mysql.MysqlTables`` fact on two real hosts.

This is a *usage* example, not part of the library. It reads a single
**fact** directly over the *real SSH transport* (asyncssh) instead of
driving the two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``MysqlTables`` for one database on every host at once with the
   driver's ``gather_fact`` helper -- the database is a positional
   argument -- and print each host's table entry list (name, engine, row
   estimate, data/index sizes, collation, comment) with
   ``json.dumps(..., indent=2)``.

The fact runs ``mysql -Be "SELECT TABLE_NAME, ENGINE, TABLE_ROWS,
DATA_LENGTH, INDEX_LENGTH, TABLE_COLLATION, TABLE_COMMENT FROM
information_schema.TABLES WHERE TABLE_SCHEMA = 'shop' ORDER BY
TABLE_NAME"`` as the login user. ``TABLE_ROWS`` is the engine's estimate
(exact for MyISAM, approximate for InnoDB), and views appear with None
engine/collation. Edit the database below to one that exists on your
hosts (the ``mysql_database.py`` operation example creates ``shop``).
Keep the credentials in a host-side ``~/.my.cnf``, or pass ``mysql_user``
/ ``mysql_host`` / ``mysql_defaults_file`` to ``gather_fact`` for a TCP
login backed by an option file. No password option exists -- credentials
never travel through fact arguments.

Every host's connection is closed with ``aclose`` once the facts are
gathered. ``gather_fact`` and ``process`` are coroutines, so ``main`` is
async and driven by ``asyncio.run``.

Run it with::

    uv run python examples/modules/mysql/mysql_mysql_tables.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    MysqlTables,
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
        # fans MysqlTables.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. process runs the TABLES select and
        # parses the rows into table entries ordered by name.
        tables = await gather_fact(hosts, MysqlTables, "shop")
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
        print(f"== {host.name}: mysql.MysqlTables ==")
        print(json.dumps(tables[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())