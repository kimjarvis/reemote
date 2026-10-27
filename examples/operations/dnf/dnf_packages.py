"""Example: install httpd and remove its welcome page on two real hosts.

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

The deploy mirrors the design doc section 9 scenario, adapted to CentOS 9
(where nginx no longer ships a default site): install httpd, then remove
the default welcome configuration the package drops in
``/etc/httpd/conf.d/`` -- the *next* operation's fresh fact cache sees the
file over SSH and removes it.

Run it with::

    uv run python examples/dnf_packages.py
"""

import asyncio
from collections.abc import Generator

from reemote5 import (
    Host,
    OperationRequest,
    dnf,
    files,
    format_log,
    format_plan,
    op,
    run,
)

HTTPD_WELCOME_CONF = "/etc/httpd/conf.d/welcome.conf"

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
    """Install httpd, then remove its default welcome configuration.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    Both operations need root -- installing a system package and removing
    a root-owned file under ``/etc/httpd/conf.d/`` -- so both are flagged
    ``sudo=True`` and the driver elevates their commands over SSH.
    """
    yield op(dnf.packages, name="Install httpd", packages=("httpd",), sudo=True)
    yield op(
        files.file,
        name="Remove welcome configuration",
        src=HTTPD_WELCOME_CONF,
        present=False,
        sudo=True,
    )


async def main() -> None:
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


if __name__ == "__main__":
    asyncio.run(main())
