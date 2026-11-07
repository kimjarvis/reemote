# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``mysql.MysqlColumns`` fact on two real hosts.

This is a *usage* example, not part of the library. It reads a single
**fact** directly over the *real SSH transport* (asyncssh) instead of
driving the two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``MysqlColumns`` for one table on every host at once with the
   driver's ``gather_fact`` helper -- the database and table are
   positional arguments -- and print each host's column definition list
   (type, nullability, default, key, extra, comment, ordinal position)
   with ``json.dumps(..., indent=2)``.

The fact runs ``mysql -Be "SELECT ... FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'shop' AND TABLE_NAME = 'orders' ORDER BY
ORDINAL_POSITION"`` as the login user. Edit the database/table below to
one that exists on your hosts (the ``mysql_table.py`` operation example
creates ``shop.orders``); an absent table reads as an empty list. Keep
the credentials in a host-side ``~/.my.cnf``, or pass ``mysql_user`` /
``mysql_host`` / ``mysql_defaults_file`` to ``gather_fact`` for a TCP
login backed by an option file. No password option exists -- credentials
never travel through fact arguments.

Every host's connection is closed with ``aclose`` once the facts are
gathered. ``gather_fact`` and ``process`` are coroutines, so ``main`` is
async and driven by ``asyncio.run``.

Run it with::

    uv run python examples/modules/mysql/mysql_mysql_columns.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    MysqlColumns,
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
        # fans MysqlColumns.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. process runs the COLUMNS select and
        # parses the rows into column entries ordered by ordinal position.
        columns = await gather_fact(hosts, MysqlColumns, "shop", "orders")
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
        print(f"== {host.name}: mysql.MysqlColumns ==")
        print(json.dumps(columns[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())