"""Example: layered environments assembled from static configuration.

This is a *usage* example, not part of the library. It builds on
``dnf_packages.py`` (same inventory, same two-phase ``run`` flow over the
real asyncssh transport) and shows the second composition scenario:
staging and production deploys differing only in *which* roles are
assembled.

The deploy body must never branch on mutable facts -- the prepare and
apply phases would flatten differently and ``run_apply`` would raise
``DeterminismError``. The legitimate place to branch is *assembly time*:
``build_deploy(env)`` consults a static environment table and returns one
composed callable that both phases then use unchanged, so determinism is
structural rather than something to be careful about.

Each role is wrapped with ``sub_deploy(name_prefix=...)`` so the plan
output shows which role produced which request. Prefixing is safe here
because the roles do not share value-identical requests (no dedupe is
requested); see ``composed_baseline_dedupe.py`` for the interaction
between prefixes and deduplication.

Run it with::

    uv run python examples/operations/dnf/composed_environments.py
"""

import asyncio
from collections.abc import Callable, Generator

from reemote5 import (
    Host,
    OperationRequest,
    compose,
    dnf,
    files,
    format_log,
    format_plan,
    op,
    run,
    sub_deploy,
)

HTTPD_WELCOME_CONF = "/etc/httpd/conf.d/welcome.conf"

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


def role_debug_tools(host: Host) -> Generator[OperationRequest]:
    """Staging only: interactive troubleshooting packages."""
    yield op(
        dnf.packages,
        name="Install debug tools",
        packages=("strace", "lsof", "tcpdump"),
        sudo=True,
    )


def role_hardening(host: Host) -> Generator[OperationRequest]:
    """Production only: remove packages a hardened host should not carry."""
    yield op(
        dnf.packages,
        name="Remove telnet",
        packages=("telnet",),
        present=False,
        sudo=True,
    )


# Static table: the environment decides the role list, not any host fact.
ENVIRONMENTS: dict[str, tuple[Callable[[Host], Generator[OperationRequest]], ...]] = {
    "staging": (role_baseline, role_httpd, role_debug_tools),
    "prod": (role_baseline, role_httpd, role_hardening),
}


def build_deploy(env: str) -> Callable[[Host], Generator[OperationRequest]]:
    """Assemble one environment's deploy from its role list."""
    return compose(
        *(
            sub_deploy(role, name_prefix=role.__name__.removeprefix("role_"))
            for role in ENVIRONMENTS[env]
        )
    )


async def main() -> None:
    # The branch happens here, once, before either phase: both the dry run
    # and the real run below use the very same composed callable.
    deploy = build_deploy("prod")

    # --dry: prepare phase only, facts gathered read-only over SSH.
    plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
    print("== Plan for prod (prepare phase, --dry) ==")
    print(format_plan(plans))

    # Real run: prepare, then apply in lockstep across hosts over SSH.
    plans, report, _hosts = await run(INVENTORY, deploy)
    print("\n== Run log (apply phase) ==")
    assert report is not None
    print(format_log(report))


if __name__ == "__main__":
    asyncio.run(main())
