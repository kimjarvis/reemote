# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: manage ufw rules on real CentOS hosts.

This is a *usage* example, not part of the library. It drives reemote5's
two-phase execution model end to end over a **real SSH transport**
(asyncssh):

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with ``validate_inventory``
   so a malformed file fails with an ``InventoryError`` naming the
   offending entry, *before* any host is materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects. Every request is flagged ``sudo=True``: ufw mutations need
   root, so the commands ride under sudo.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``ufw.rule`` is the workhorse: it normalizes the desired rule (``port``
as an int, a ``"8000:8010"`` range or a tuple of ports for a multiport
rule; ``protocol``, ``action`` allow/deny/reject/limit, ``direction``
in/out, ``route``, ``from_ip``/``to_ip``, ``app`` profile, ``interface``,
``log``, ``comment``, ``insert`` position), reads the ``UfwRules`` fact
and yields ``ufw allow ...`` only when no matching rule exists. ufw
maintains IPv4 and IPv6 rules side by side, so one add creates both the
plain row and its ``(v6)`` twin and the operation matches either family
correctly. ``present=False`` deletes by rule specification (never by
number, which shifts), removing every match.

The deploy allows SSH first -- the lockout guard every firewall deploy
needs -- then opens HTTP/HTTPS from a trusted subnet with one multiport
rule (``port=(80, 443)``: a tuple, because request arguments must stay
hashable). The SSH request is yielded twice: in the prepare phase (where
nothing is sent) both plan as changes, but in the apply phase the second
request's fresh per-step fact cache sees the first add and reports the
rule already exists.

**This example is compile-checked by the test suite, not executed
against the shared demo hosts**: adding rules to a live firewall is an
operator decision, not a demo side effect. Run it only against hosts
whose intended ruleset you have verified.

The demo hosts ship without ufw (EPEL-only on RHEL-family), so the deploy
first installs ``epel-release`` and ``ufw`` via ``dnf.packages`` under
``sudo=True``. Installing ufw does not activate it, so the install step is
safe; the rule changes below remain compile-checked, not executed.

Every request carries ``when=is_centos``, the declarative eligibility
guard: the predicate reads only **static** inventory data --
``host.group``, set by the ``"group"`` key of the inventory entry -- and
is a stable module-level function, so the prepare and apply phases reach
the same skip decision. The driver skips a rejected request without
materializing it, so on hosts outside the ``"centos"`` group neither the
commands nor the facts behind them run, and plan and log render the step
as ``[skipped: when guard is_centos]`` in its lockstep slot. The path is
resolved from ``__file__``, so the example works from any working
directory; point ``INVENTORY_PATH`` at your own file to target your
machines.

Run it with::

    uv run python examples/modules/ufw/ufw_rule.py
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
    format_log,
    format_plan,
    op,
    run,
    ufw,
    validate_inventory,
)

# examples/modules/ufw/ufw_rule.py -> the project root, so the inventory
# is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries
    each carrying at least ``host`` and ``connection``. Returns the parsed
    list, ready to materialize into hosts; raises ``InventoryError`` (via
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
    so both deploy phases reach the same eligibility decision.
    """
    return host.group == "centos"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Allow SSH, then HTTP/HTTPS from the trusted subnet.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    ufw rule changes need root, so every request is flagged ``sudo=True``.
    """
    # ufw is EPEL-only on RHEL-family: install the repo then ufw first
    # (idempotent; the exit-masked ufw facts tolerate its absence in prepare).
    yield op(
        dnf.packages, name="Install EPEL repository", packages=("epel-release",), sudo=True,
        when=is_centos,
    )
    yield op(
        dnf.packages,
        name="Install ufw",
        packages=("ufw",),
        sudo=True,
        when=is_centos,
    )
    yield op(
        ufw.rule,
        name="Allow SSH inbound",
        port=22,
        protocol="tcp",
        comment="Allow SSH inbound",
        sudo=True,
        when=is_centos,
    )
    yield op(
        ufw.rule,
        name="Allow SSH inbound (again)",
        port=22,
        protocol="tcp",
        comment="Allow SSH inbound",
        sudo=True,
        when=is_centos,
    )
    yield op(
        ufw.rule,
        name="Allow HTTP/HTTPS from the internal subnet",
        port=(80, 443),
        protocol="tcp",
        from_ip="192.168.0.0/24",
        comment="Allow HTTP/HTTPS from internal subnet",
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
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