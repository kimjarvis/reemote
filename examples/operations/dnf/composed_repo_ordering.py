"""Example: ordering guarantees across roles -- repositories before packages.

This is a *usage* example, not part of the library. It builds on
``dnf_packages.py`` (same inventory, same two-phase ``run`` flow over the
real asyncssh transport) and shows the fourth composition scenario:
``dnf install`` of an EPEL-provided package fails unless ``epel-release``
is installed first, and ``compose`` makes that ordering explicit and
reusable.

Composition order *is* execution order, and the driver executes it in
cross-host lockstep: every host runs the Nth request before any host pulls
request N+1. So composing the repository role first guarantees that both
web1 and web2 have ``epel-release`` installed before *either* host starts
installing ``htop`` from it -- an ordering property that would otherwise
have to be hand-maintained inside one flat generator.

The roles are wrapped with ``sub_deploy(name_prefix=...)`` so the plan
shows which layer each request came from. No dedupe is requested: the two
roles share no requests, and prefixing would rename any that they did
(``name`` participates in request equality).

Run it with::

    uv run python examples/operations/dnf/composed_repo_ordering.py
"""

import asyncio
from collections.abc import Generator

from reemote5 import (
    Host,
    OperationRequest,
    compose,
    dnf,
    format_log,
    format_plan,
    op,
    run,
    sub_deploy,
)

# Same real-host inventory as dnf_packages.py; the default host_factory
# materializes each entry into a plain Host connecting over asyncssh.
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


def role_epel_repo(host: Host) -> Generator[OperationRequest]:
    """The prerequisite layer: enable EPEL so the next role can resolve."""
    yield op(
        dnf.packages,
        name="Install epel-release",
        packages=("epel-release",),
        sudo=True,
    )


def role_monitoring(host: Host) -> Generator[OperationRequest]:
    """Packages that only resolve once EPEL is enabled on every host."""
    yield op(
        dnf.packages,
        name="Install monitoring tools",
        packages=("htop", "iotop"),
        sudo=True,
    )


# Argument order is the ordering statement: repos strictly before packages.
deploy = compose(
    sub_deploy(role_epel_repo, name_prefix="repos"),
    sub_deploy(role_monitoring, name_prefix="monitoring"),
)


async def main() -> None:
    # --dry: prepare phase only. The plan lists the repo request first on
    # each host; no mutating command is sent.
    plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
    print("== Plan (prepare phase, --dry) ==")
    print(format_plan(plans))

    # Real run: the lockstep barrier between steps is what guarantees both
    # hosts have epel-release before either installs htop/iotop.
    plans, report, _hosts = await run(INVENTORY, deploy)
    print("\n== Run log (apply phase) ==")
    assert report is not None
    print(format_log(report))


if __name__ == "__main__":
    asyncio.run(main())
