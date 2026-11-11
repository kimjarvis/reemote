# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Manage an OpenRC service's runtime and boot state with ``openrc.service``.

This is a *usage* example, not part of the library. It shows how an
application drives reemote's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Read the target hosts from ``inventory.json`` in the project root -- an
   inventory is just a list of dictionaries, each holding a host's name plus
   its SSH ``connection`` information (address, username and client key),
   which is exactly what JSON expresses -- and validate it up front with
   ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is
   materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``). Each plain ``Host``
lazily opens an asyncssh connection from its ``connection`` data, gathers
facts and runs commands for real, and ``run`` closes those connections when
it finishes. The path is resolved from ``__file__``, so the example works from
any working directory; point ``INVENTORY_PATH`` at your own file to target
your machines.

``run`` is a coroutine (both phases are async), so ``main`` is async and
driven by ``asyncio.run``.

``openrc.service`` ports the baseline's operation of the same name: it manages
one service -- here ``crond``, the cron daemon every stock Alpine install
ships -- along two independent axes. ``running=True`` compares the
``OpenrcStatus`` fact and starts the service with ``rc-service <name>
start`` only when it is not running (``running=False`` stops it, and
``running=None`` leaves the runtime state alone); ``enabled=True`` compares
the ``OpenrcEnabled`` fact and assigns the service to the runlevel with
``rc-update add`` only when it is not assigned (``enabled=False`` removes
it, ``enabled=None`` -- the default -- leaves the boot state alone). The
first request sets both, so it converges runtime *and* boot state in one
operation. The baseline's arbitrary ``command`` option is deliberately dropped
(an escape hatch for any rc-service verb defeats the typed-descriptor
model -- ``server.shell`` covers the rare custom verb), so that keyword is
absorbed with no effect.

The script yields the *same* start-and-enable request twice to show
idempotence across the two phases: the prepare phase plans both as changes
when crond is not yet running (nothing is sent in prepare, so both compare
against the same pre-deployment state), while the apply phase starts on
the first and reports the second as a no-op, because the fresh per-step
fact cache now sees the state the first step produced.

A third request demonstrates the non-idempotent side: ``reloaded=True``
with ``running=None`` reloads the service (``rc-service crond reload``,
re-reading configuration without a restart) without touching its boot
assignment. Like ``restarted=True``, the reload is gated on the service
actually running -- reloading a stopped service is meaningless, so both
flags yield nothing when the ``OpenrcStatus`` fact reads False at the
moment the request is materialized. In the apply phase the first request
has already started crond, so the reload fires even on a host where crond
began stopped; in the dry-run plan it is only planned if crond was already
running before the deploy.

``rc-service`` and ``rc-update`` write root-owned state, so the requests
are flagged ``doas=True`` and the driver elevates their commands. ``doas``
rather than ``sudo`` because the Alpine host has no ``sudo`` installed at
all -- ``sudo rc-service`` would die with ``sh: sudo: not found`` (exit 127)
before reaching OpenRC. ``doas`` is passwordless here: reemote always runs
``doas -n``, which fails rather than prompts, so the host must grant the
login user a ``permit nopass`` rule in ``/etc/doas.conf`` (see
``examples/demonstrations/privilege/privilege_doas.py``); without one the
driver raises ``PrivilegeError``. No target user is named, so the command
sent is a bare ``doas -n rc-service crond start`` (doas already targets
root, so there is no ``-u``).

Every request also carries ``when=is_alpine``, the declarative eligibility
guard (see
``examples/demonstrations/composition/composed_when_guard.py``). Two rules
make it deterministic: the predicate reads only **static** inventory data --
``host.group``, set by the ``"group"`` key of the inventory entry -- never a
mutable fact, and it is a stable module-level function, so the prepare and
apply phases reach the same skip decision. ``when`` is a meta-parameter like
``doas``: captured on the request, never forwarded to the operation, and
skipped by the *driver*, so every host keeps the identical three-request
schedule (no ``if`` in the deploy body) and each request still consumes its
lockstep slot. On a host the guard rejects, the plan and the run log render
the step as ``[skipped: when guard is_alpine]`` and none of its commands --
nor the ``OpenrcStatus``/``OpenrcEnabled`` facts behind it -- is materialized
at all, which is exactly why an init system's operations must be guarded
rather than run blind on a host without OpenRC. The project root's
``inventory.json`` holds one host in the ``"alpine"`` group alongside hosts
in other groups, so the run visibly manages crond on the Alpine host and
skips every other one.

Run it with::

    uv run python examples/modules/openrc/openrc_service.py
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
    openrc,
    run,
    validate_inventory,
)

# examples/modules/openrc/openrc_service.py -> the project root, so the
# inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The service to manage: crond ships with every stock Alpine install, so the
# example needs no package installation first (see openrc_ensure.py for the
# install-then-enable composition).
SERVICE = "crond"


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


def is_alpine(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"alpine"".

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision and the
    determinism guard holds. OpenRC itself is the reason for the guard: on a
    non-OpenRC host every request below would fail on a missing
    ``rc-service``/``rc-update`` binary, so the driver skips them all instead.
    """
    return host.group == "alpine"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Start and enable crond -- twice, to show idempotence -- then reload
    it without touching its boot assignment.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError),
    and identical on every host: eligibility is expressed with the
    ``when=is_alpine`` guard each request carries, not with an ``if``. The
    first request starts crond if it is not running and enables it in the
    default runlevel if it is not assigned; the second carries the same
    arguments and is a guaranteed no-op in the apply phase, because the
    first request's changes are already visible through the fresh per-step
    fact cache. The third reloads the running service (``running=None``
    leaves the runtime decision to the reload gate alone).

    ``rc-service``/``rc-update`` write root-owned state, so all requests are
    flagged ``doas=True`` (the host has no ``sudo``) and the driver elevates
    their commands over SSH with passwordless ``doas -n``.
    """
    yield op(
        openrc.service,
        name="Ensure crond running and enabled",
        service=SERVICE,
        running=True,
        enabled=True,
        doas=True,
        when=is_alpine,
    )
    yield op(
        openrc.service,
        name="Re-ensure crond (no-op)",
        service=SERVICE,
        running=True,
        enabled=True,
        doas=True,
        when=is_alpine,
    )
    yield op(
        openrc.service,
        name="Reload crond configuration",
        service=SERVICE,
        running=None,
        reloaded=True,
        doas=True,
        when=is_alpine,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent. A request the ``when`` guard rejects is
        # planned as [skipped: when guard is_alpine] without materializing it,
        # so its facts are not read on that host either.
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