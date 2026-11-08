# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: load the floppy, loop and brd kernel modules on two real hosts.

This is a *usage* example, not part of the library. It shows reemote5's
``server.modprobe`` operation, the pyinfra ``server.modprobe`` parity for
loading and unloading kernel modules: the deploy loads the ``floppy``
module (pyinfra's documented example) and then ensures the ``loop`` and
``brd`` modules are loaded, both under ``sudo`` because ``modprobe`` needs
root.

The operation is idempotent: it reads the ``KernelModules`` fact (the
loaded modules from ``/proc/modules``) and yields a ``modprobe -a`` only
for the names that are missing, so a host where ``loop`` is already loaded
re-loads nothing and a second run is a no-op everywhere. ``module`` accepts
a single name or a tuple of names (tuples keep ``op()`` requests hashable),
and ``force=True`` would render ``modprobe -f`` -- without ever forcing a
reload of an already-loaded module. Unloading is the mirror image:
``present=False`` yields ``modprobe -r -a`` only for the names that are
loaded. On modern hardware the ``floppy`` module may not exist at all;
``modprobe`` then exits non-zero and the run fails with a typed
``CommandError``, which ``main`` reports as one line.

**Host-policy prerequisite.** On the CentOS demo hosts (``web1``/``web2``)
SELinux is ``Enforcing`` and ``sudo`` runs in the confined ``staff_t``
domain, which cannot load kernel modules: ``modprobe`` reports even a
present, indexed module (``loop``, ``brd``) as "not found in directory".
This is a host-policy limit, not a script defect or a missing package --
``kernel-modules`` is installed and the ``depmod`` database lists the
modules, yet the confined domain is denied the module-loading path. The
requests already carry ``sudo=True``; run the script on a host whose
``sudo`` is unconfined (e.g. ``unconfined_t``) or with SELinux permissive.

Every failure mode of the run -- an unreachable host, a rejected elevation,
or a ``modprobe`` that exits non-zero (an unknown module, or a module in
use on removal) -- reaches ``main`` as a typed error whose message already
names the host, so ``main`` catches them and prints one line each instead
of a traceback.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``): each plain ``Host``
lazily opens an asyncssh connection, reads ``uname -s`` and
``/proc/modules`` for real, and runs ``modprobe`` under sudo where needed.

Run it with::

    uv run python examples/modules/server/server_modprobe.py
"""

import asyncio
import sys
from collections.abc import Generator

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    OperationRequest,
    PrivilegeError,
    format_log,
    format_plan,
    op,
    run,
    server,
)

# The inventory: real hosts and their SSH connection information, supplied
# by the application to the library. The default host_factory materializes
# each entry into a plain Host that connects over asyncssh.
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


def deploy(host: Host) -> Generator[OperationRequest]:
    """Load floppy, then ensure loop and brd are loaded.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases; which modules are missing is decided inside the operation
    against the freshly gathered ``KernelModules`` fact, per phase.
    """
    yield op(
        server.modprobe,
        name="Load floppy module",
        module="floppy",
        sudo=True,
    )
    yield op(
        server.modprobe,
        name="Load loop and brd modules",
        module=("loop", "brd"),
        sudo=True,
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. The module list is still gathered over
        # SSH (read-only) so the plan reflects each host's real pre-deployment
        # state; nothing is loaded.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. Step 1
        # loads floppy where missing; step 2 loads just the missing ones of
        # loop and brd.
        plans, report, _hosts = await run(INVENTORY, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except HostConnectionError as exc:
        # A host could not be reached over SSH at all. Report it plainly --
        # naming every unreachable host -- instead of dumping an asyncssh
        # traceback, and exit non-zero so the failure is script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except PrivilegeError as exc:
        # The host was reached but rejected the ``sudo=True`` elevation -- a
        # wrong ``sudo_password``, or a run-as user the login user may not
        # become. One line naming the host; the detail stays on ``__cause__``.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except CommandError as exc:
        # A ``modprobe`` invocation exited non-zero (the floppy module does
        # not exist on this hardware, for instance). The typed message
        # already names the host, the credential-free command and its
        # stderr, so print just that.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())