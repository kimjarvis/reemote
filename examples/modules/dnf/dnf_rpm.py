# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install a ``.rpm`` file already on the host, with version idempotence.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``). Each plain ``Host``
lazily opens an asyncssh connection from its ``connection`` data, gathers
facts and runs commands for real, and ``run`` closes those connections when
it finishes.

``run`` is a coroutine (both phases are async), so ``main`` is async and
driven by ``asyncio.run``.

``dnf.rpm`` installs (or with ``present=False`` removes) a ``.rpm`` file that
already exists on the *target host* -- here the EPEL release package, the kind
of standalone ``.rpm`` one commonly copies to a machine and installs by hand.
The ``src`` is therefore a **remote** path, not a controller-side one: upload
the file first with ``files.put`` (or place it there by any other means).
Unlike pyinfra -- whose ``dnf.rpm`` can also *download* a remote ``.rpm``
mid-deploy -- reemote5 has no download operation, so a ``src`` with a URL
scheme raises ``ValueError``.

``dnf.rpm`` reads the file's ``name``/``version`` through the ``RpmPackage``
fact, then reads the installed package of that name, and counts the package as
present only when the installed version matches the file's. It installs with
``rpm -i`` only when it does not, so re-running is a no-op. The script yields
the *same* install request twice to show that idempotence across the two
phases: the prepare phase plans both as changes (nothing is installed yet, so
both compare against the same pre-deployment state), while the apply phase
installs on the first and reports the second as a no-op, because the fresh
per-step fact cache now sees the package the first step installed.

Installing a package writes to the root-owned rpm database, so the request is
flagged ``sudo=True`` and the driver elevates its ``rpm -i`` command. Because
reemote5 re-materializes the operation at apply time against a fresh fact
cache, the command is a plain ``rpm -i`` -- not pyinfra's defensive
``rpm -q \\`rpm -qp <src>\\` ... || rpm -i <src>`` runtime check -- which also
keeps elevation correct (a ``sudo``-prefixed ``|| ...`` chain would leave its
fallback arm unprivileged).

Run it with::

    uv run python examples/modules/dnf/dnf_rpm.py
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
    dnf,
    format_log,
    format_plan,
    op,
    run,
)

# The ``.rpm`` file to install, as a path on the *target host* (copy it there
# with ``files.put`` first). Shared by both requests below so they carry
# identical operation kwargs (only the log ``name`` differs).
EPEL_RPM = "/tmp/epel-release-9-8.el9.noarch.rpm"

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
    """Install the EPEL ``.rpm`` -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request installs the file's package; the second carries the
    same arguments and is a guaranteed no-op in the apply phase, because the
    first install is already visible through the fresh per-step fact cache.

    Installing a package writes to the root-owned rpm database, so both
    requests are flagged ``sudo=True`` and the driver elevates their command
    over SSH.
    """
    yield op(dnf.rpm, name="Install EPEL rpm", src=EPEL_RPM, sudo=True)
    yield op(dnf.rpm, name="Re-install EPEL rpm (no-op)", src=EPEL_RPM, sudo=True)


async def main() -> None:
    try:
        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
        plans, report, _hosts = await run(INVENTORY, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
