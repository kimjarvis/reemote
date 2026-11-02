# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: a shared schedule with per-host eligibility via ``when`` guards.

This is a *usage* example, not part of the library. It builds on
``composed_per_host_roles.py`` (same inventory, same two-phase ``run`` flow
over the real asyncssh transport) and shows the declarative alternative to
that example's ``if host.name == ...`` branching: a ``when`` guard on the
request itself.

The difference is the schedule. An ``if`` in the deploy body changes the
request *sequence* per host (web1 simply yields more requests). A ``when``
guard keeps every host's sequence identical -- all hosts share one schedule,
and the driver skips the guarded request on ineligible hosts, rendering it
as ``[skipped: when guard is_canary]`` side by side with the hosts that run
it. The request still consumes its lockstep slot, so the Nth operation on
host A always pairs with the Nth operation on host B.

``when`` is a meta-parameter like ``sudo``: captured on the request, never
forwarded to the operation. Two rules keep it deterministic:

- the predicate reads **static** inventory data only (here ``host.tags``),
  never mutable facts, so prepare and apply reach the same skip decision;
- the predicate is a **stable function object** defined at module level --
  an inline ``lambda`` in the deploy body would be a fresh object on each
  phase invocation and the determinism guard would (correctly) report the
  requests as divergent.

The same guard can be applied to a whole role at the composition site --
``sub_deploy(role_canary_extras, when=is_canary)``, the analogue of Ansible's
block-level ``when``. Role-level and request-level guards compose by
conjunction: both must pass, and a wrapper can only restrict eligibility,
never widen it.

Run it with::

    uv run python examples/demonstrations/composition/composed_when_guard.py
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
    files,
    format_log,
    format_plan,
    op,
    run,
)

HTTPD_WELCOME_CONF = "/etc/httpd/conf.d/welcome.conf"


def is_canary(host: Host) -> bool:
    """Static inventory predicate: the same function object in both phases."""
    return "canary" in host.tags


# Same real-host inventory as dnf_packages.py; the default host_factory
# materializes each entry into a plain Host connecting over asyncssh. The
# canary tag is inventory data, not a gathered fact: web1 receives the extra
# tooling, web2 skips it.
INVENTORY = [
    {
        "host": "web1",
        "connection": {
            "address": "192.168.0.240",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
        "tags": ["canary"],
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
    """Extra live-debugging tooling, guarded for canary hosts only.

    The guard travels with the request, so this role yields the same single
    request on every host; the driver decides eligibility per host.
    """
    yield op(
        dnf.packages,
        name="Install canary tooling",
        packages=("strace", "tcpdump"),
        sudo=True,
        when=is_canary,
    )


def deploy(host: Host) -> Generator[OperationRequest]:
    """One schedule for all hosts; the guard selects who runs the last step.

    No branching here at all: every host yields the same three requests in
    both phases, so the determinism guard holds by construction and the
    plans stay comparable line by line across hosts.
    """
    yield from role_baseline(host)
    yield from role_httpd(host)
    yield from role_canary_extras(host)


async def main() -> None:
    try:
        # --dry: prepare phase only. Both plans hold the same three entries;
        # web2's canary entry is [skipped: when guard is_canary]. No mutating
        # command is sent -- and the skipped operation was never even
        # materialized, so no facts were read for it on web2.
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