# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: allow ICMP/ICMPv6 with typed matchers on real RedHat hosts.

This is a *usage* example, not part of the library. It drives reemote's
two-phase execution model end to end over a **real SSH transport**
(asyncssh):

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with ``validate_inventory``
   so a malformed file fails with an ``InventoryError`` naming the
   offending entry, *before* any host is materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects. Every request is flagged ``sudo=True``: iptables mutations
   need CAP_NET_ADMIN, so the commands ride under root.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

ICMP is the one matcher class a firewall ruleset cannot do without --
ping, and on IPv6 the neighbour-discovery and router-advertisement types
SLAAC depends on -- and it is exactly where a naive ``-m <protocol>``
guess breaks idempotence (issue #49). ``iptables.rule`` now carries typed
ICMP matchers: ``icmp_type`` for IPv4 ``-p icmp`` and
``icmpv6_type``/``icmpv6_code`` for IPv6 ``-p ipv6-icmp``. A symbolic
type (``echo-request``) is normalized to the numeric form
``iptables-save`` prints (``8``), and the protocol match module is looked
up rather than guessed, so ``-p ipv6-icmp`` records ``-m icmp6`` -- the
rule definition read back from the fact compares equal and the operation
converges instead of re-appending on every deploy.

The script allows IPv4 ping and the IPv6 echo plus SLAAC types, each
request yielded twice: in the prepare phase (where nothing is sent) both
plan as changes, but in the apply phase the second request's fresh
per-step fact cache sees the first ``-A`` and reports the rule exists.
Adding ACCEPT rules only widens what is allowed -- it never drops the
deploy's own SSH session -- so this example is safe to run live.

The demo hosts ship without ``iptables``, so the deploy opens with an
idempotent ``dnf.packages`` install under ``sudo=True``. The iptables
facts are exit-masked, so the prepare phase reads them even before the
binary is present; in apply the install runs first and the mutations that
follow ride the freshly installed binary.

Every request carries ``when=is_redhat``, the declarative eligibility
guard: the predicate reads only **static** inventory data --
``host.family``, set by the ``\"family\"`` key of the inventory entry -- and
is a stable module-level function, so the prepare and apply phases reach
the same skip decision. The path is resolved from ``__file__``, so the
example works from any working directory; point ``INVENTORY_PATH`` at your
own file to target your machines.

Run it with::

    uv run python examples/modules/iptables/iptables_rule_icmp.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    dnf,
    format_log,
    format_plan,
    iptables,
    op,
    run,
    validate_inventory,
)

# examples/modules/iptables/iptables_rule_icmp.py -> the project root, so the
# inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The ICMPv6 types SLAAC and ping need, by their symbolic iptables names.
# Each normalizes to its numeric code (128..136) so the rule converges.
ICMPV6_SLAAC_TYPES = (
    "echo-request",
    "echo-reply",
    "router-solicitation",
    "router-advertisement",
    "neighbour-solicitation",
    "neighbour-advertisement",
)


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


def is_redhat(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"RedHat"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision.
    """
    return host.family == "RedHat"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Allow IPv4 ping and the IPv6 SLAAC/echo types, each twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    iptables -A needs CAP_NET_ADMIN, so every request is flagged
    ``sudo=True`` and rides under root.
    """
    yield op(
        dnf.packages,
        name="Install iptables",
        packages=("iptables",),
        sudo=True,
        when=is_redhat,
    )
    # IPv4 ping: -p icmp --icmp-type echo-request, normalized to 8.
    for _ in range(2):
        yield op(
            iptables.rule,
            name="Allow IPv4 ICMP echo-request (ping)",
            chain="INPUT",
            jump="ACCEPT",
            protocol="icmp",
            icmp_type="echo-request",
            sudo=True,
            when=is_redhat,
        )
    # IPv6: the echo plus neighbour-discovery/router types SLAAC needs.
    # -p ipv6-icmp loads the icmp6 match module, recorded so each rule
    # converges against ip6tables-save instead of re-appending.
    for icmpv6_type in ICMPV6_SLAAC_TYPES:
        for _ in range(2):
            yield op(
                iptables.rule,
                name=f"Allow IPv6 ICMPv6 {icmpv6_type}",
                chain="INPUT",
                jump="ACCEPT",
                version=6,
                protocol="ipv6-icmp",
                icmpv6_type=icmpv6_type,
                sudo=True,
                when=is_redhat,
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
