"""Example: per-host variation keyed on static inventory data.

This is a *usage* example, not part of the library. It builds on
``dnf_packages.py`` (same inventory, same two-phase ``run`` flow over the
real asyncssh transport) and shows the fifth composition scenario: one
deploy whose request sequence differs per host -- extra canary tooling on
web1 only -- without endangering the determinism guard.

The rule: never branch on *mutable* facts inside a deploy body (the
prepare and apply phases would flatten differently and ``run_apply`` would
raise ``DeterminismError``), but branching on **static** inputs is safe.
``host.name`` comes from the inventory entry and reads identically in both
phases, so ``if host.name == "web1"`` produces the same sequence per host
every time the driver re-invokes ``deploy(host)``.

The per-host roles are delegated with plain ``yield from`` -- the simplest
composition, needing no helper because nothing is renamed or elevated at
the composition site. The canary role is still written as a standalone
sub-deploy so it stays independently testable and reusable.

Run it with::

    uv run python examples/demonstrations/composition/composed_per_host_roles.py
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

# The canary host is inventory data, not a gathered fact: web1 receives the
# extra tooling role, web2 does not.
CANARY_HOST = "web1"

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


def role_baseline(host: Host) -> Generator[OperationRequest]:
    yield op(
        dnf.packages,
        name="Install baseline tools",
        packages=("ca-certificates", "curl"),
        sudo=True,
    )


def role_httpd(host: Host) -> Generator[OperationRequest]:
    yield op(dnf.packages, name="Install httpd", packages=("httpd",), sudo=True)
    yield op(
        files.file,
        name="Remove welcome configuration",
        src=HTTPD_WELCOME_CONF,
        present=False,
        sudo=True,
    )


def role_canary_extras(host: Host) -> Generator[OperationRequest]:
    """Extra live-debugging tooling for the canary host only."""
    yield op(
        dnf.packages,
        name="Install canary tooling",
        packages=("strace", "tcpdump"),
        sudo=True,
    )


def deploy(host: Host) -> Generator[OperationRequest]:
    """Compose per host: shared roles, plus canary extras on web1.

    The branch reads ``host.name`` -- static inventory data, identical in
    the prepare and apply phases -- so each host's request sequence is
    stable across phases and lockstep pairing works even though the hosts'
    sequences differ in length (a finished host simply idles at the
    barrier while the other runs its extra step).
    """
    yield from role_baseline(host)
    yield from role_httpd(host)
    if host.name == CANARY_HOST:
        yield from role_canary_extras(host)


async def main() -> None:
    # --dry: prepare phase only. web1's plan holds one more entry than
    # web2's; no mutating command is sent.
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
