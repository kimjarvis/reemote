"""Example: gather the ``hardware.DmiInfo`` fact on two real hosts.

This is a *usage* example, not part of the library. Like
``examples/modules/hardware/hardware_disk_stats.py``, it reads a single
**fact** directly over the *real SSH transport* (asyncssh) instead of
driving the two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Materialize each entry into a plain ``Host`` with the default
   ``Host.from_inventory`` factory. Each ``Host`` lazily opens an asyncssh
   connection from its ``connection`` data when a fact is gathered.
3. Gather ``DmiInfo`` on every host at once with the driver's
   ``gather_fact`` helper -- it fans the fact's ``process`` coroutine out
   across the inventory with ``asyncio.gather`` and returns a
   host-name-keyed mapping -- then print each host's identity map with
   ``json.dumps(..., indent=2)``.

``DmiInfo`` is a reemote5 original (pyinfra has no counterpart): it dumps
the kernel-exposed DMI identity files under ``/sys/class/dmi/id/`` and
maps each file name to its raw value (``sys_vendor``, ``product_name``,
``product_serial``, ``bios_vendor``, ``bios_version``, ``bios_date``,
``chassis_type``, ...) -- exactly the fields asset tracking and
conditional execution branch on. Reading sysfs needs no ``dmidecode``
binary and no root for most fields; the root-only serial/UUID files are
simply absent for an unprivileged login. ``chassis_type`` is the numeric
SMBIOS code (e.g. ``3`` desktop, ``10`` notebook, ``23`` rack mount); the
privileged ``SmbiosInfo`` sibling reports the decoded label. A machine
without DMI (most containers, many ARM boards and VMs) reports no fields
at all (``{}``).

Every host's connection is closed with ``aclose`` once the facts are gathered.
``gather_fact`` and ``process`` are coroutines, so ``main`` is async and driven
by ``asyncio.run``.

Run it with::

    uv run python examples/modules/hardware/hardware_dmi_info.py
"""

import asyncio
import json
import sys

from reemote5 import (
    CommandError,
    DmiInfo,
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


async def main() -> None:
    # Materialize every inventory entry up front so the fact can be gathered on
    # all hosts at once.
    hosts = [Host.from_inventory(entry) for entry in INVENTORY]
    try:
        # Gather the fact across the whole inventory concurrently: gather_fact
        # fans DmiInfo.process out with asyncio.gather and returns a
        # host-name-keyed mapping, so both SSH round-trips overlap instead of
        # running one host at a time.
        info = await gather_fact(hosts, DmiInfo)
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
        print(f"== {host.name}: hardware.DmiInfo ==")
        print(json.dumps(info[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())