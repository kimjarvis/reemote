"""Example: gather the ``files.Directory`` fact on two real hosts.

This is a *usage* example, not part of the library. Like
``examples/modules/files/files_file.py``, it reads a single **fact**
directly over the *real SSH transport* (asyncssh) instead of driving the
two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``Directory`` for ``/etc`` on every host at once with the
   driver's ``gather_fact`` helper -- it fans the fact's ``process``
   coroutine out across the inventory with ``asyncio.gather`` and returns
   a host-name-keyed mapping -- then print each host's metadata with
   ``json.dumps(..., indent=2)``.

``Directory`` mirrors pyinfra's ``files.Directory`` -- its ``File``
subclass with ``type = "directory"``: the same ``test -e``/``test -L``
guard and GNU ``stat -c`` / BSD ``stat -f`` / ``ls -ld`` chain, but the
path's type flag must be ``d``. The result is a metadata dict -- ``user``,
``group``, ``mode`` (the octal-looking int pyinfra returns, e.g. ``755``,
setuid/setgid/sticky bits included), ``size``, and ``atime``/``mtime``/
``ctime`` as *naive UTC* ``datetime``s (the ``ls -ld`` fallback provides
only an mtime, so its atime/ctime read as ``None``) -- so a deploy can
audit a directory's ownership or permissions before placing files inside
it. A path that does not exist reads as ``None``; one that exists but is
not a directory (a regular file, symlink, ...) reads as ``False``.
``datetime`` is not JSON-serializable, so the print step renders the
timestamps with ``isoformat()``.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/files/files_directory.py
"""

import asyncio
import json
import sys
from datetime import datetime
from typing import Any

from reemote5 import (
    CommandError,
    Directory,
    Host,
    HostConnectionError,
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


def jsonable(stats: dict[str, Any] | bool | None) -> dict[str, Any] | bool | None:
    """Render the fact's answer for JSON: datetimes become ISO 8601 strings.

    The False (non-directory) and None (absent) answers are already
    JSON-serializable and pass through untouched.
    """
    if not isinstance(stats, dict):
        return stats
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in stats.items()
    }


async def main() -> None:
    # Materialize every inventory entry up front so the fact can be gathered on
    # all hosts at once.
    hosts = [Host.from_inventory(entry) for entry in INVENTORY]
    try:
        # Gather the fact across the whole inventory concurrently: gather_fact
        # fans Directory.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time. process runs the stat/ls chain and
        # parses the metadata dict (False for a non-directory, None if absent).
        stats = await gather_fact(hosts, Directory, "/etc")
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
        print(f"== {host.name}: files.Directory(/etc) ==")
        print(json.dumps(jsonable(stats[host.name]), indent=2))


if __name__ == "__main__":
    asyncio.run(main())