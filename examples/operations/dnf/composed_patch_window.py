"""Example: patch-window deploys -- the same roles, different intent.

This is a *usage* example, not part of the library. It builds on
``dnf_packages.py`` (same inventory, same two-phase ``run`` flow over the
real asyncssh transport) and shows the sixth composition scenario: one
package list driving two different deploys -- a normal "ensure installed"
run and a patch-window run that upgrades the same packages.

``package_role`` is a *factory*: it takes a package set (as ``*packages``,
which naturally yields the tuple ``OperationRequest`` kwargs must stay
hashable with) plus the ``latest`` flag, and returns a deploy-shaped
callable. The intent difference lives entirely in ``dnf.packages``'
options: ``latest=False`` only installs missing packages, while
``latest=True`` additionally upgrades every installed, unpinned package
through ``dnf update -y <pkgs>``.

Because a factory produces a fresh callable per intent, patch Tuesday is a
different *composed* deploy over the same package list -- no duplicated
role code, and each composed deploy is a pure function of the host, so
both satisfy the determinism guard.

This example prepares both deploys read-only (``dry=True``) so the two
plans can be compared, then really runs only the patch deploy: on hosts
missing the packages it installs them, and on hosts that have them it
upgrades them.

Run it with::

    uv run python examples/operations/dnf/composed_patch_window.py
"""

import asyncio
from collections.abc import Callable, Generator

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

WEB_PACKAGES = ("httpd", "mod_ssl")

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


def package_role(
    *packages: str, latest: bool = False
) -> Callable[[Host], Generator[OperationRequest]]:
    """Factory: build a sub-deploy ensuring one fixed package set.

    ``*packages`` is a tuple, satisfying the hashability rule for
    ``OperationRequest`` kwargs; ``latest`` selects the intent -- install
    missing packages only, or also upgrade the installed ones.
    """

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(
            dnf.packages,
            name=f"Ensure {', '.join(packages)}",
            packages=packages,
            latest=latest,
            sudo=True,
        )

    return deploy


# Two intents over the same package list, each a composed deploy.
install_deploy = compose(
    sub_deploy(package_role(*WEB_PACKAGES), name_prefix="web"),
)
patch_deploy = compose(
    sub_deploy(package_role(*WEB_PACKAGES, latest=True), name_prefix="web-patch"),
)


async def main() -> None:
    # --dry both intents: prepare phase only, facts gathered read-only
    # over SSH, so the plans show what each would do to the real hosts.
    install_plans, _, _hosts = await run(INVENTORY, install_deploy, dry=True)
    print("== Install plan (prepare phase, --dry) ==")
    print(format_plan(install_plans))

    patch_plans, _, _hosts = await run(INVENTORY, patch_deploy, dry=True)
    print("\n== Patch plan (prepare phase, --dry) ==")
    print(format_plan(patch_plans))

    # Real run of the patch deploy only: installs where missing, upgrades
    # where present (``dnf update -y httpd mod_ssl``).
    _plans, report, _hosts = await run(INVENTORY, patch_deploy)
    print("\n== Run log (patch deploy, apply phase) ==")
    assert report is not None
    print(format_log(report))


if __name__ == "__main__":
    asyncio.run(main())
