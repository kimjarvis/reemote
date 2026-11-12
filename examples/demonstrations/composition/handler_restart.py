# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: Ansible-style handlers -- restart a service only on change.

It demonstrates the sanctioned answer to a gap every Ansible role closes
with ``notify:`` / handlers: "restart nginx only when a package install or
a config write actually changed something" -- built from the reusable
helpers ``reemote.changed_hosts`` and ``reemote.restart_handler`` (shipped
in ``reemote.handlers``) so a role migration does not hand-roll a bespoke
``notifying_hosts``/``build_restart_*`` pair. ``systemd.service(restarted=True)``
cannot express that by itself -- ``restarted`` is deliberately stateless, so it
restarts a running unit on EVERY deploy that reaches it, and nothing feeds
an earlier operation's outcome back into a later request:

- ``when`` guards read static inventory data only, never run outcomes;
- ``ctx.changed`` flows one way (operation -> driver -> ``ApplyReport``),
  so a deploy body cannot observe its own earlier operations' results;
- branching on "did the config write change?" inside ONE deploy would
  yield different request sequences per phase (prepare sees pre-state,
  apply sees live state) and ``run_apply`` raises ``DeterminismError``.

The fix is the **two-deploy outcome split**, a sibling of the fact split
in ``two_deploy_split.py``: the outcome read moves into the gap between
two complete prepare/apply cycles.

1. ``configure_nginx`` -- a branch-free deploy that installs nginx and
   writes a comment-only ``conf.d`` drop-in with ``files.install`` (the
   spool-and-install family, content-idempotent). Both requests are named;
   their names form the NOTIFYING set, the analogue of Ansible's
   ``notify: restart datadog-agent`` on every config task.
2. The notification read: ``changed_hosts(report, NOTIFYING)`` filters run
   1's ``ApplyReport.log`` for entries that are ``changed`` AND named in
   NOTIFYING, and freezes the host names into a ``frozenset``. A
   ``changed`` entry is trustworthy: the driver is fail-fast (a non-zero
   exit raises ``CommandError``), so it means the commands really ran.
   The apply-phase join of run 1 is the barrier -- every config write has
   landed before any restart is planned.
3. ``restart_handler(changed, "nginx", when=is_centos)`` -- the library
   factory closing over that frozen set, returning the handler deploy. Its
   ``if`` consults the snapshot, never a live fact or report, so the
   generator is a pure function of static input and the determinism guard
   holds. Deduplication and end-of-run flushing come free: at most one
   restart request per host, after ALL notifying operations, exactly like
   an Ansible handler.

``systemd.service`` semantics inside the handler: ``restarted=True`` only
restarts a unit that is already running; the default ``running=True``
starts a stopped one. So the first pass (fresh install, unit stopped)
STARTS nginx, and a converged re-run of the whole script does nothing:
run 1 reports no change, the frozen set is empty, and the handler deploy
yields no request at all.

The trade-offs, mirroring Ansible: the restart happens in a second full
prepare/apply cycle, and a ``dry=True`` preview of run 1 cannot know
whether the handler will fire (Ansible check mode skips handlers for the
same reason). The dry preview of run 2 below IS honest -- it is computed
against the frozen outcome of run 1.

The inventory is read from the project root's ``inventory.json``
(validated up front with ``validate_inventory``); every request carries
``when=is_centos`` so the ``dnf``/``systemd`` work runs only on the CentOS
hosts, and the run-2 factory builds fresh hosts from the same inventory
via the default factory.

Run it with::

    uv run python examples/demonstrations/composition/handler_restart.py
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
    changed_hosts,
    dnf,
    files,
    format_log,
    format_plan,
    op,
    restart_handler,
    run,
    validate_inventory,
)

# The config payload: a comment-only drop-in, valid nginx configuration.
# files.install takes a plain str (hashable, equal across phases) and is
# idempotent on content, so a re-run transfers and installs nothing.
NGINX_CONF_CONTENT = (
    "# Managed by reemote -- handler demonstration drop-in.\n"
    "# Editing this text is what notifies the nginx restart handler.\n"
)
CONF_DEST = "/etc/nginx/conf.d/reemote-handler-demo.conf"

# The notification channel: request names whose `changed` outcome queues
# the restart handler -- the analogue of Ansible's `notify:` list.
NOTIFYING = frozenset({"Install nginx", "Install demo config"})

# examples/demonstrations/composition/handler_restart.py -> the project
# root, so the inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries
    each carrying at least ``host`` and ``connection``. Returns the parsed
    list, ready to hand to ``run``; raises ``InventoryError`` (via
    ``validate_inventory``) when the document does not match the schema.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_centos(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"centos"``.

    Reads only static inventory data and stays a stable module-level
    function -- the two rules every ``when`` predicate follows so both
    deploy phases reach the same eligibility decision.
    """
    return host.group == "centos"


def configure_nginx(host: Host) -> Generator[OperationRequest]:
    """Deploy 1: the notifying tasks, branch-free so both phases agree.

    Both requests are named after their NOTIFYING entries; whether they
    report ``changed`` is decided by the operations themselves against
    real host state (package present? content identical?), never here.
    """
    yield op(
        dnf.packages,
        name="Install nginx",
        packages=("nginx",),
        sudo=True,
        when=is_centos,
    )
    yield op(
        files.install,
        name="Install demo config",
        content=NGINX_CONF_CONTENT,
        dest=CONF_DEST,
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Run 1: the notifying tasks. Its apply-phase join is the barrier
        # guaranteeing every config write landed before the handler plans.
        plans, report, _hosts = await run(inventory, configure_nginx)
        print("\n== Deploy 1: install + configure nginx ==")
        print(format_plan(plans))
        assert report is not None
        print(format_log(report))

        # The notification read: freeze which hosts the handler must visit,
        # with the library helper -- one pass over run 1's apply log selecting
        # the entries whose name is in NOTIFYING and that reported a change.
        changed = changed_hosts(report, NOTIFYING)
        print("\n== Handler notifications ==")
        if changed:
            print("restart queued for: " + ", ".join(sorted(changed)))
        else:
            print("no notifying operation changed -- handler idle")

        # Build the handler deploy ONCE, before either of its phases: the
        # dry run and the real run use the very same callable closing over
        # the same frozen set -- determinism by construction. ``when=is_centos``
        # keeps the shared schedule aligned across the whole inventory.
        restart = restart_handler(changed, "nginx", when=is_centos)

        # --dry for the handler: an honest preview, because the branch
        # consults run 1's frozen outcome, not pre-deployment state.
        plans, _, _hosts = await run(inventory, restart, dry=True)
        print("\n== Handler plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real handler run: restart (or start) nginx on notified hosts only.
        plans, report, _hosts = await run(inventory, restart)
        print("\n== Handler run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
