# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Carve a per-domain SELinux exception with ``selinux.permissive_domain``.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with ``validate_inventory``
   so a malformed file fails with an ``InventoryError`` naming the
   offending entry, *before* any host is materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``). Each plain ``Host``
lazily opens an asyncssh connection from its ``connection`` data, gathers
facts and runs commands for real, and ``run`` closes those connections when
it finishes.

``run`` is a coroutine (both phases are async), so ``main`` is async and
driven by ``asyncio.run``.

``selinux.permissive_domain`` is a reemote5 original (the baseline ships no
permissive-domain operation): it adds or removes one domain from the
permissive list -- here ``httpd_t``, the web server's domain -- so its
denials are logged but not enforced while the rest of the system stays
enforcing. When troubleshooting a service this is the safe alternative to
``selinux.mode(mode="permissive")``, which disables enforcement
*everywhere*: the exception is one domain wide, persistent (it lives in
the policy store, not the runtime mode), and auditable through the
``PermissiveDomains`` fact.

With ``present=True`` (the default) an enforcing domain is added
(``semanage permissive -a``); ``present=False`` removes it (``-d``),
returning the domain to full enforcement. Idempotence reads the
``PermissiveDomains`` fact: the first two requests carry identical kwargs,
so the prepare phase plans both as changes when the domain is enforcing,
while the apply phase adds on the first and reports the second as a no-op
through the fresh per-step fact cache. The third request removes the
exception again, showing the bidirectional pair -- leave it out (or keep
only the first request) to keep the domain permissive while you work.

``semanage permissive`` writes the root-owned policy store, so every
request is flagged ``sudo=True`` and the driver elevates its commands over
SSH.

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

    uv run python examples/modules/selinux/selinux_permissive_domain.py
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
    format_log,
    format_plan,
    op,
    run,
    selinux,
    validate_inventory,
)

# The domain to exempt while troubleshooting: the web server's.
PERMISSIVE_DOMAIN = "httpd_t"

# examples/modules/selinux/selinux_permissive_domain.py -> the project root, so the inventory
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
    """Make httpd_t permissive -- twice -- then enforce it again.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first two requests carry identical kwargs, making the second a
    guaranteed no-op in the apply phase; the third removes the exception,
    demonstrating the present=False direction (in the dry-run plan it is a
    no-op when the domain started enforcing).

    ``semanage permissive`` writes the policy store, so all requests are
    flagged ``sudo=True`` and the driver elevates their commands over SSH.
    """
    yield op(
        selinux.permissive_domain,
        name="Make httpd_t permissive",
        domain=PERMISSIVE_DOMAIN,
        sudo=True,
        when=is_centos,
    )
    yield op(
        selinux.permissive_domain,
        name="Re-make httpd_t permissive (no-op)",
        domain=PERMISSIVE_DOMAIN,
        sudo=True,
        when=is_centos,
    )
    yield op(
        selinux.permissive_domain,
        name="Enforce httpd_t again",
        domain=PERMISSIVE_DOMAIN,
        present=False,
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