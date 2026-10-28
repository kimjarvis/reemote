"""Example: independently written roles sharing a baseline, deduplicated.

This is a *usage* example, not part of the library. It builds on
``dnf_packages.py`` (same inventory, same two-phase ``run`` flow over the
real asyncssh transport) and shows the first composition scenario: roles
written in isolation that each declare the same prerequisite.

``role_httpd`` and ``role_monitoring`` both re-declare ``role_baseline``'s
request -- each under its *own* name ("Install baseline tools", "Install
httpd prerequisites", "Install monitoring prerequisites"), the way
independently written roles naturally would. ``compose(..., dedupe=True)``
still collapses them, by *effective identity* -- ``func``, args, kwargs and
``sudo``, but **not** ``name`` -- to their first occurrence, which keeps
its name. Names are excluded from the key because they never reach the
host: they only label plan and log entries. So despite the three differing
labels (and the differing ``sub_deploy`` name prefixes on top), the plan
holds a single "baseline: Install baseline tools" entry -- first name wins.
Without dedupe the duplicates would remain as separate plan entries
(harmless: ``dnf.packages`` re-reads the ``DnfPackages`` fact at each
lockstep step and reports no change).

The determinism guard is unaffected: it still compares *full* request
values (name included), since both phases flatten the same composed
deploy. Dedupe itself is deterministic too -- the ``seen`` set is created
per invocation, so the prepare and apply phases collapse identically.

Run it with::

    uv run python examples/demonstrations/composition/composed_baseline_dedupe.py
"""

import asyncio
from collections.abc import Generator

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
    """The prerequisite every role below assumes, stated once."""
    yield op(
        dnf.packages,
        name="Install baseline tools",
        packages=("ca-certificates", "curl", "vim-minimal"),
        sudo=True,
    )


def role_httpd(host: Host) -> Generator[OperationRequest]:
    """Web-server role: re-declares the baseline under its own name."""
    yield op(
        dnf.packages,
        name="Install httpd prerequisites",
        packages=("ca-certificates", "curl", "vim-minimal"),
        sudo=True,
    )
    yield op(dnf.packages, name="Install httpd", packages=("httpd",), sudo=True)
    yield op(
        files.file,
        name="Remove welcome configuration",
        src=HTTPD_WELCOME_CONF,
        present=False,
        sudo=True,
    )


def role_monitoring(host: Host) -> Generator[OperationRequest]:
    """Monitoring role: the same packages again, yet another name."""
    yield op(
        dnf.packages,
        name="Install monitoring prerequisites",
        packages=("ca-certificates", "curl", "vim-minimal"),
        sudo=True,
    )
    yield op(
        dnf.packages,
        name="Install troubleshooting tools",
        packages=("tcpdump", "wget"),
        sudo=True,
    )


# Prefixed per role for readable plans; the shared baseline request still
# collapses even though all three copies carry different names, because
# the dedupe key ignores names (first name wins).
deploy = compose(
    sub_deploy(role_baseline, name_prefix="baseline"),
    sub_deploy(role_httpd, name_prefix="httpd"),
    sub_deploy(role_monitoring, name_prefix="monitoring"),
    dedupe=True,
)


async def main() -> None:
    # --dry: prepare phase only. The deduplicated plan shows one baseline
    # entry despite three roles yielding it; no mutating command is sent.
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
