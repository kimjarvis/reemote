"""Example: install nginx and remove its default site on two hosts.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH connection/session (and optionally sudo)
   information -- this is the parameter the library accepts.
2. Build the deploy script as a generator yielding ``OperationRequest``\\ s.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``SimulatedHost`` is part of the *test framework*, not of the library API:
it lives in ``tests/simulated_host.py`` and appears here only so the example
can run without a real SSH transport, injected via ``run``'s
``host_factory`` argument.

``run`` is a coroutine (the apply path is async, in preparation for an
asyncssh transport), so ``main`` is async and driven by ``asyncio.run``.

The trace mirrors the design doc section 9 scenario:

- ``web1`` starts fresh: installing nginx creates the default-site link, and
  the *next* operation's fresh fact cache sees that link and removes it.
- ``web2`` already has nginx installed (install reports "no change"), while
  the pre-existing default-site link is still removed.

Run it with::

    uv run python examples/apt_install_nginx.py
"""

import asyncio
import sys
from collections.abc import Generator
from pathlib import Path

from reemote5 import (
    Host,
    OperationRequest,
    apt,
    files,
    format_log,
    format_plan,
    op,
    run,
)

# SimulatedHost is test-framework scaffolding and lives in tests/, not in
# the library: put the repository root on sys.path so this example can
# borrow it and run without a real SSH transport.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.simulated_host import SimulatedHost

NGINX_DEFAULT_SITE = "/etc/nginx/sites-enabled/default"
DEFAULT_TARGET = "/etc/nginx/sites-available/default"

# The inventory: hosts and their SSH connection/session information,
# supplied by the application to the library.
INVENTORY = [
    {
        "host": "web1",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
        "session": {"term_type": "xterm-color"},
    },
    {
        "host": "web2",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
        "session": {"term_type": "xterm-color", "term_size": 80},
    },
]


def deploy(host: Host) -> Generator[OperationRequest]:
    """Install nginx, then remove its default site.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    """
    yield op(apt.packages, name="Install nginx", packages=("nginx",))
    yield op(
        files.link,
        name="Remove default site",
        src=NGINX_DEFAULT_SITE,
        present=False,
    )


def simulated_host_factory(entry: dict) -> SimulatedHost:
    """Build the test-framework stand-in for one inventory entry.

    Only exists so this example can run without SSH: it materializes the
    inventory entry into a ``SimulatedHost`` (carrying the same
    connection/session data a real transport would use) and seeds the
    simulated pre-deployment state -- web1 fresh; web2 already has nginx
    and its default-site link.
    """
    host = SimulatedHost.from_inventory(entry)
    if host.name == "web1":
        host.installs_links = {"nginx": {NGINX_DEFAULT_SITE: DEFAULT_TARGET}}
    else:
        host.packages = {"nginx"}
        host.links = {NGINX_DEFAULT_SITE: DEFAULT_TARGET}
    return host


async def main() -> None:
    # --dry: prepare phase only, nothing is sent to the hosts.
    plans, _, _hosts = await run(
        INVENTORY, deploy, host_factory=simulated_host_factory, dry=True
    )
    print("== Plan (prepare phase, --dry) ==")
    print(format_plan(plans))

    # Real run: prepare, then apply in lockstep across hosts.
    plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=simulated_host_factory
    )
    print("\n== Run log (apply phase) ==")
    assert report is not None
    print(format_log(report))

    print("\n== Final host state ==")
    for host in hosts:
        assert isinstance(host, SimulatedHost)  # test-framework stand-in
        print(f"{host.name}: packages={sorted(host.packages)} links={host.links}")


if __name__ == "__main__":
    asyncio.run(main())
