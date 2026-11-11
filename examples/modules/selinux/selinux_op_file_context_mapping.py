# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Manage a persistent fcontext rule, then relabel the tree it covers.

This is a *usage* example, not part of the library. It shows how an
application drives reemote's two-phase execution model end to end over a
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

``selinux.file_context_mapping`` ports the baseline's operation of the same
name: it manages one *persistent* rule in the local fcontext database --
here ``/web(/.*)?`` labelled ``httpd_sys_content_t``, so a custom web root
and everything under it keeps the httpd type across relabels. With
``present=True`` (the default) it adds the rule (``semanage fcontext -a``)
or changes an existing rule's type (``-m``); ``present=False`` deletes it
(``-d``). Idempotence reads the ``FileContextMapping`` fact: the first two
requests carry identical kwargs, so the prepare phase plans both as changes
when the rule is absent or mistyped, while the apply phase writes on the
first and reports the second as a no-op through the fresh per-step fact
cache.

The third request is the documented remedy for the operation's own
limitation: ``semanage fcontext`` changes the *database*, never the labels
existing files already carry. ``selinux.restorecon`` reapplies the database
to the tree (``restorecon -R /web``), and because it runs *after* the
mapping step in the same deploy, the fresh per-step state sees the new rule
-- the composition the fact-operation pairing table recommends. The ``/web``
tree must exist on the hosts (create it with ``files.directory`` first);
``restorecon`` is stateless and always reports a change, since reemote has
no fact that compares a whole tree against the database.

``semanage`` and ``restorecon`` write root-owned policy and labels, so every
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

    uv run python examples/modules/selinux/selinux_op_file_context_mapping.py
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
    format_log,
    format_plan,
    op,
    run,
    selinux,
    validate_inventory,
)

# The persistent rule: a custom web root and everything under it keeps the
# httpd content type. WEB_ROOT is the tree restorecon relabels once the rule
# is in the database.
MAPPING_TARGET = "/web(/.*)?"
CONTENT_TYPE = "httpd_sys_content_t"
WEB_ROOT = "/web"

# examples/modules/selinux/selinux_op_file_context_mapping.py -> the project root, so the inventory
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
    """Ensure the /web fcontext rule -- twice -- then relabel the tree.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first two requests carry identical kwargs, making the second a
    guaranteed no-op in the apply phase; the third relabels /web against the
    database the first step just wrote.

    ``semanage fcontext`` and ``restorecon`` write root-owned state, so all
    requests are flagged ``sudo=True`` and the driver elevates their
    commands over SSH.
    """
    yield op(
        selinux.file_context_mapping,
        name="Map /web to httpd_sys_content_t",
        target=MAPPING_TARGET,
        se_type=CONTENT_TYPE,
        sudo=True,
        when=is_centos,
    )
    yield op(
        selinux.file_context_mapping,
        name="Re-map /web (no-op)",
        target=MAPPING_TARGET,
        se_type=CONTENT_TYPE,
        sudo=True,
        when=is_centos,
    )
    yield op(
        selinux.restorecon,
        name="Relabel /web from the database",
        path=WEB_ROOT,
        recurse=True,
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