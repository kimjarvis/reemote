# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Assign a service to an OpenRC runlevel with ``openrc.runlevel``.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
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

``openrc.runlevel`` is a reemote5 original with no baseline counterpart:
the baseline folds the boot assignment into ``openrc.service``'s ``enabled``
option, so managing an assignment *without* touching the runtime state
needs the whole service operation with ``running=None``. This standalone
operation is the dedicated runlevel-membership verb: ``enabled=True``
(default) compares the ``OpenrcEnabled`` fact for the requested ``runlevel``
(default ``"default"``) and yields ``rc-update add <service> <runlevel>``
only when the service is not assigned; ``enabled=False`` yields
``rc-update del`` only when it is. The runtime state is never touched --
assigning a service to a runlevel only means it *will* start when that
runlevel is entered (at boot, or via ``openrc <runlevel>``).

The example assigns ``crond`` -- which every stock Alpine install ships --
to the ``default`` runlevel, the boot-persistence half of what
``openrc_service.py`` does in one combined request. The *same* request is
yielded twice to show idempotence across the two phases: the prepare phase
plans both as changes when crond is not yet assigned (nothing is sent in
prepare, so both compare against the same pre-deployment state), while the
apply phase adds on the first and reports the second as a no-op, because
the fresh per-step fact cache now sees the assignment the first step made.
The removal direction (``enabled=False``, rendering ``rc-update del crond
default``) is deliberately not exercised here: dropping crond from the only
runlevel that starts it would leave the shared host without cron after the
example finishes. Flip ``enabled`` to False on a scratch host to see it.

There is no existence validation: the baseline's enabled branch does not check
the init script either, so a service without an ``/etc/init.d`` script --
or a runlevel that does not exist -- fails in ``rc-update`` itself at apply
time as a ``CommandError``.

``rc-update`` writes the root-owned ``/etc/runlevels`` tree, so the
requests are flagged ``doas=True`` and the driver elevates their commands.
``doas`` rather than ``sudo`` because the Alpine host has no ``sudo``
installed at all -- ``sudo rc-update`` would die with ``sh: sudo: not
found`` (exit 127) before reaching OpenRC. ``doas`` is passwordless here:
reemote5 always runs ``doas -n``, which fails rather than prompts, so the
host must grant the login user a ``permit nopass`` rule in
``/etc/doas.conf`` (see ``examples/demonstrations/privilege/
privilege_doas.py``); without one the driver raises ``PrivilegeError``. No
target user is named, so the command sent is a bare ``doas -n rc-update add
crond default`` (doas already targets root, so there is no ``-u``).

Every request also carries ``when=is_alpine``, the declarative eligibility
guard (see
``examples/demonstrations/composition/composed_when_guard.py``). Two rules
make it deterministic: the predicate reads only **static** inventory data --
``host.group``, set by the ``"group"`` key of the inventory entry -- never a
mutable fact, and it is a stable module-level function, so the prepare and
apply phases reach the same skip decision. ``when`` is a meta-parameter like
``doas``: captured on the request, never forwarded to the operation, and
skipped by the *driver*, so every host keeps the identical two-request
schedule (no ``if`` in the deploy body) and each request still consumes its
lockstep slot. On a host the guard rejects, the plan and the run log render
the step as ``[skipped: when guard is_alpine]`` and none of its commands --
nor the ``OpenrcEnabled`` fact behind it -- is materialized at all. The
project root's ``inventory.json`` holds one host in the ``"alpine"`` group
alongside hosts in other groups, so the run visibly assigns crond on the
Alpine host and skips every other one.

Run it with::

    uv run python examples/modules/openrc/openrc_runlevel.py
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
    openrc,
    run,
    validate_inventory,
)

# examples/modules/openrc/openrc_runlevel.py -> the project root, so the
# inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The service to assign: crond ships with every stock Alpine install, so the
# example needs no package installation first.
SERVICE = "crond"

# The runlevel to assign it to: "default" is the multi-user runlevel a stock
# Alpine boot enters, so membership here is what starts crond at boot.
RUNLEVEL = "default"


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
    non-OpenRC host every request below would fail on a missing ``rc-update``
    binary, so the driver skips them all instead.
    """
    return host.group == "alpine"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Assign crond to the default runlevel -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError),
    and identical on every host: eligibility is expressed with the
    ``when=is_alpine`` guard each request carries, not with an ``if``. The
    first request adds the assignment if it is missing; the second carries
    the same arguments and is a guaranteed no-op in the apply phase,
    because the first assignment is already visible through the fresh
    per-step fact cache. Neither request touches whether crond is running.

    ``rc-update`` writes the root-owned runlevel tree, so both requests are
    flagged ``doas=True`` (the host has no ``sudo``) and the driver elevates
    their commands over SSH with passwordless ``doas -n``.
    """
    yield op(
        openrc.runlevel,
        name="Assign crond to the default runlevel",
        service=SERVICE,
        runlevel=RUNLEVEL,
        enabled=True,
        doas=True,
        when=is_alpine,
    )
    yield op(
        openrc.runlevel,
        name="Re-assign crond (no-op)",
        service=SERVICE,
        runlevel=RUNLEVEL,
        enabled=True,
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
        # so its fact is not read on that host either.
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