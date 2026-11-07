# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: gather the ``sysctl.SysctlFile`` fact on two real hosts.

This is a *usage* example, not part of the library. Like
``examples/modules/sysctl/sysctl_sysctl.py``, it reads a single **fact**
directly over the *real SSH transport* (asyncssh) instead of driving the
two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``SysctlFile`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's file text.

``SysctlFile`` is a reemote5 original: it returns the **raw text** of one
sysctl config file, or ``None`` when the file is absent. The shell prints
a marker line before the ``cat``'d content only when ``test -e`` passes,
so an existing-but-empty file reads as ``""`` rather than being confused
with a missing one. This is the same fact the ``sysctl.setting`` and
``sysctl.conf`` operations compare their desired content against, and a
public debugging/templating read for any single sysctl config path -- the
path is a plain argument, not restricted to the standard locations. For
the parsed settings across *every* standard file at once, see the
companion ``sysctl_sysctl_files.py`` example.

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/sysctl/sysctl_sysctl_file.py
"""

import asyncio
import sys

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    SysctlFile,
    gather_fact,
)

# The legacy persistence file. A /etc/sysctl.d/*.conf drop-in path works the
# same way -- the fact takes any single path.
SYSCTL_FILE = "/etc/sysctl.conf"

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
        # fans SysctlFile.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap. The path
        # argument is forwarded to process verbatim (and shell-quoted there).
        contents = await gather_fact(hosts, SysctlFile, SYSCTL_FILE)
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
    # The raw text is printed as-is; None means the file does not exist.
    for host in hosts:
        print(f"== {host.name}: sysctl.SysctlFile({SYSCTL_FILE}) ==")
        content = contents[host.name]
        print("<absent>" if content is None else content, end="")


if __name__ == "__main__":
    asyncio.run(main())