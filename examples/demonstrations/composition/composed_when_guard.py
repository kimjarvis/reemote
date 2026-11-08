# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: a shared schedule with per-host eligibility via ``when`` guards.

This is a *usage* example, not part of the library. It builds on
``composed_per_host_roles.py`` (same two-phase ``run`` flow over the real
asyncssh transport) and shows the declarative alternative to that example's
``if host.name == ...`` branching: a ``when`` guard on the request itself.

The inventory is read from the project root's ``inventory.json`` (validated
up front with ``validate_inventory``), which holds one Alpine host, two
CentOS hosts (web1, web2) and one Ubuntu host. Two guards at two levels
select who runs what, and they compose by conjunction:

- a **role-level** guard ``when=is_centos`` wraps the whole deploy at the
  composition site (``sub_deploy(deploy, when=is_centos)``), the analogue of
  Ansible's block-level ``when``. It makes every step centos-only, so the
  Alpine and Ubuntu hosts skip the entire schedule -- and because a skipped
  request is never materialized, the ``DnfPackages`` fact behind the ``dnf``
  steps is not read on them either;
- a **request-level** guard ``when=is_canary`` rides on the canary tooling
  request alone, opting web1 (the canary, pinned by its static inventory
  name) into the extra step while web2 skips just that one.

The difference from an ``if`` is the schedule. An ``if`` in the deploy body
changes the request *sequence* per host. A ``when`` guard keeps every host's
sequence identical -- all hosts share one schedule, and the driver skips the
guarded request on ineligible hosts, rendering it as
``[skipped: when guard is_centos]`` or ``[skipped: when guard is_canary]`` side
by side with the hosts that run it. The request still consumes its lockstep
slot, so the Nth operation on host A always pairs with the Nth operation on
host B. Because the guards compose by conjunction (both must pass) and a
wrapper can only *restrict* eligibility, never widen it, the skip reports the
*outermost* failing predicate: on the Alpine and Ubuntu hosts that is
``is_centos`` even for the canary step, while on web2 it is ``is_canary``.

``when`` is a meta-parameter like ``sudo``: captured on the request, never
forwarded to the operation. Two rules keep it deterministic:

- the predicate reads **static** inventory data only (here ``host.group``
  and ``host.name``), never mutable facts, so prepare and apply reach the
  same skip decision;
- the predicate is a **stable function object** defined at module level --
  an inline ``lambda`` in the deploy body would be a fresh object on each
  phase invocation and the determinism guard would (correctly) report the
  requests as divergent.

Run it with::

    uv run python examples/demonstrations/composition/composed_when_guard.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    dnf,
    files,
    format_log,
    format_plan,
    op,
    run,
    sub_deploy,
    validate_inventory,
)

HTTPD_WELCOME_CONF = "/etc/httpd/conf.d/welcome.conf"

# The canary host is inventory data, not a gathered fact: web1 opts into the
# extra tooling, web2 does not.
CANARY_HOST = "web1"

# examples/demonstrations/composition/composed_when_guard.py -> the project
# root, so the inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries
    each carrying at least ``host`` and ``connection``. Returns the parsed
    list, ready to hand to ``run``, which materializes it with the default
    ``Host.from_inventory`` factory; raises ``InventoryError`` (via
    ``validate_inventory``) when the document is not an array or an entry
    does not match the schema.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_centos(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"centos"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision. Applied to the
    whole deploy, it keeps the ``dnf`` steps (and the facts they read) off the
    Alpine and Ubuntu hosts.
    """
    return host.group == "centos"


def is_canary(host: Host) -> bool:
    """Static inventory predicate: the same function object in both phases.

    Keys on ``host.name`` -- the canary is web1 -- so it reads only static
    inventory data and stays a stable module-level function, exactly like
    ``is_centos``. It guards the canary tooling request alone.
    """
    return host.name == CANARY_HOST


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
        path=HTTPD_WELCOME_CONF,
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

    No branching here at all: every host yields the same four requests in
    both phases, so the determinism guard holds by construction and the
    plans stay comparable line by line across hosts.
    """
    yield from role_baseline(host)
    yield from role_httpd(host)
    yield from role_canary_extras(host)


# The role-level guard: the whole schedule is centos-only. It composes by
# conjunction with the request-level ``is_canary`` guard above -- both must
# pass, and a skip reports the outermost failing predicate (``is_centos`` on
# the Alpine/Ubuntu hosts, ``is_canary`` on web2).
deploy = sub_deploy(deploy, when=is_centos)


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Every plan holds the same four entries;
        # the Alpine/Ubuntu hosts skip all of them ([skipped: when guard
        # is_centos]) and web2 skips the canary step ([skipped: when guard
        # is_canary]). No mutating command is sent -- and a skipped operation
        # was never even materialized, so no facts were read for it there.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON. Python's
        # own message already names the file and the reason, so print just that
        # -- no host was contacted and there is no traceback worth showing.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema. The
        # message names the offending entry by its list index, which is the
        # whole report -- and it fails before any host is materialized.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
