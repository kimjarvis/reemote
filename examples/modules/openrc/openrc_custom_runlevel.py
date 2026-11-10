# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Create, stack and remove a custom runlevel with ``openrc.custom_runlevel``.

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

``openrc.custom_runlevel`` is a reemote5 original with no baseline
counterpart: it manages the runlevel *directories themselves* under
``/etc/runlevels``, where ``openrc.runlevel`` only manages one service's
membership in an existing runlevel. A runlevel is just a directory of
service symlinks -- rc-update(8) ships no verb that creates one -- so
``present=True`` yields ``mkdir -p /etc/runlevels/<name>`` when the
``OpenrcRunlevels`` fact does not list it. ``stacked_to="default"`` then
yields ``rc-update -s add async default``, making ``async`` a *stacked*
runlevel: entering ``default`` also enters ``async``, so services assigned
to ``async`` (with ``openrc.runlevel``) start alongside the default set.
The stack shows up in the fact as an ``async`` entry inside ``default``'s
list, which is how the operation knows it is already stacked.

Removal is the interesting direction: rc-update(8) documents no verb that
*deletes* a runlevel, and deleting the directory alone would leave
dangling stack symlinks behind. ``present=False`` therefore unstacks
first -- one ``rc-update -s del async <target>`` per target whose list
names ``async``, in sorted target order -- then ``rm -rf
/etc/runlevels/async``. ``stacked_to`` is ignored on that direction:
removal always unstacks from *every* target the facts report.

The script runs a full create -> no-op -> remove cycle so the shared host
is left exactly as it was found. The two phases tell the story
differently, which is worth watching in the output: the *plan* is computed
against the pre-deployment state where ``async`` does not exist, so it
shows the creation as a change, the identical second request also as a
change (prepare never sends, so its fact cache still sees the original
state), and the removal as "runlevel async does not exist" -- no change.
The *apply log* re-materializes each request against a fresh per-step fact
cache: the first creates and stacks, the second reports both as no-ops,
and the third now *sees* the runlevel the first step created, so it
unstacks from default and removes the directory. A plan is a prediction
from one snapshot; the apply phase decides each step from live state.

Validation happens while materializing: an empty runlevel name, or
stacking a runlevel onto itself, raises ``ValueError`` in the prepare
phase before anything is sent. ``stacked_to`` must name an *existing*
runlevel -- ``default`` always exists on a booted OpenRC host; a missing
target fails in ``rc-update`` at apply time as a ``CommandError``. The
standard runlevels are not special-cased: removing ``boot`` or ``default``
is legal OpenRC-wise and the operation does not guard against it, so keep
``present=False`` requests aimed at custom names like this example's.

``mkdir``/``rm`` under ``/etc/runlevels`` and ``rc-update -s`` write
root-owned state, so the requests are flagged ``doas=True`` and the driver
elevates their commands. ``doas`` rather than ``sudo`` because the Alpine
host has no ``sudo`` installed at all -- ``sudo rc-update`` would die with
``sh: sudo: not found`` (exit 127) before reaching OpenRC. ``doas`` is
passwordless here: reemote5 always runs ``doas -n``, which fails rather
than prompts, so the host must grant the login user a ``permit nopass``
rule in ``/etc/doas.conf`` (see ``examples/demonstrations/privilege/
privilege_doas.py``); without one the driver raises ``PrivilegeError``. No
target user is named, so the commands sent are bare ``doas -n mkdir -p
/etc/runlevels/async`` and friends (doas already targets root, so there is
no ``-u``).

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
nor the ``OpenrcRunlevels`` fact behind it -- is materialized at all. The
project root's ``inventory.json`` holds one host in the ``"alpine"`` group
alongside hosts in other groups, so the run visibly cycles the runlevel on
the Alpine host and skips every other one.

Run it with::

    uv run python examples/modules/openrc/openrc_custom_runlevel.py
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

# examples/modules/openrc/openrc_custom_runlevel.py -> the project root, so
# the inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The custom runlevel to cycle. "async" is OpenRC's own documentation
# example of a stacked runlevel; the create -> remove cycle leaves no
# trace of it on the shared host.
RUNLEVEL = "async"

# The existing runlevel to stack onto: entering "default" (the runlevel a
# stock Alpine boot enters) will also enter "async" while the stack holds.
STACKED_TO = "default"


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
    non-OpenRC host the runlevel tree and ``rc-update`` do not exist, so the
    driver skips every request instead.
    """
    return host.group == "alpine"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Create and stack the async runlevel -- twice, to show idempotence --
    then remove it again, leaving the host as it was found.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError),
    and identical on every host: eligibility is expressed with the
    ``when=is_alpine`` guard each request carries, not with an ``if``. In
    the apply phase the first request creates and stacks, the second is a
    guaranteed no-op (the fresh per-step fact cache sees both), and the
    third unstacks and removes. The plan tells the prepare-phase story
    instead -- see the module docstring.

    The runlevel tree is root-owned, so all requests are flagged
    ``doas=True`` (the host has no ``sudo``) and the driver elevates their
    commands over SSH with passwordless ``doas -n``.
    """
    yield op(
        openrc.custom_runlevel,
        name="Create async runlevel stacked on default",
        runlevel=RUNLEVEL,
        present=True,
        stacked_to=STACKED_TO,
        doas=True,
        when=is_alpine,
    )
    yield op(
        openrc.custom_runlevel,
        name="Re-create async runlevel (no-op)",
        runlevel=RUNLEVEL,
        present=True,
        stacked_to=STACKED_TO,
        doas=True,
        when=is_alpine,
    )
    yield op(
        openrc.custom_runlevel,
        name="Remove async runlevel (unstack first)",
        runlevel=RUNLEVEL,
        present=False,
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