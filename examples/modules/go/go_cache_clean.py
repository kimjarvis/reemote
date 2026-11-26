# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Empty the Go build and module caches with ``go clean``.

This is a *usage* example, not part of the library. It shows how an application
drives reemote's two-phase execution model end to end over a **real SSH
transport** (asyncssh):

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information (address, username and
   client key), which is exactly what JSON expresses -- and validate it up front
   with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Build the deploy script as a generator yielding ``OperationRequest`` objects,
   each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example uses
the *default* factory (``Host.from_inventory``). Each plain ``Host`` lazily opens
an asyncssh connection from its ``connection`` data, gathers facts and runs
commands for real, and ``run`` closes those connections when it finishes. The
inventory is read from ``inventory.json`` in the current working directory; edit
it there to target your machines.

``go.cache_clean`` empties the caches ``go`` keeps under the user's
``$GOCACHE`` (compiled packages) and ``$GOMODCACHE`` (downloaded modules). The
operation is deliberately **stateless**: unlike the design sketch -- which
wanted to gate the clean on a cache-size fact that reemote does not have -- it
always fires when a cache is selected, exactly like ``dnf.clean``, so any
size or schedule gate belongs in the deploy, not the operation. The plan shows
every request as changed; the run log shows the shells actually sent.

The three requests cover the whole option set. The first cleans the build cache
(``clear_build_cache`` is on by default); the second selects only the module
cache (``clear_build_cache=False, clear_mod_cache=True``), so the log shows the
distinct ``go clean -modcache`` shell; the third selects *neither* and is the
one honest no-op the operation has -- the plan and the log both report
``[no change: no caches selected]``. Clearing the module cache is the disruptive
half: the next ``go install`` or ``go build`` on that host re-downloads every
dependency, which costs a shared demo host some time but breaks nothing, since
go refills the cache on demand.

No request flags ``sudo``: ``go clean`` empties the *connecting user's* caches,
and elevating it would empty root's instead.

Like the other examples, every request carries ``when=is_ubuntu``: the shared
``inventory.json`` holds one host in the ``"Ubuntu"`` family alongside hosts in
other families, so each run cleans on the Ubuntu host and skips every other one
-- plan and log render ``[skipped: when guard is_ubuntu]`` for the rest. The
guard is not cosmetic: the ``go clean`` shells are not self-guarding, so on an
unprovisioned host they would fail with ``CommandNotFoundError``. go is *not*
installed by default on the demo host, so ``main`` first awaits ``provision_go``
from the sibling ``provision_go.py``, which installs the ``golang-go`` deb with
``apt.packages`` (idempotent, so the run is cheap on a host that already has
it).

Run it with::

    uv run python examples/modules/go/go_cache_clean.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    format_log,
    format_plan,
    go,
    op,
    run,
    validate_inventory,
)

# The go provisioning helper sits beside this script; add its directory to
# sys.path so ``import provision_go`` resolves whether the example is run by
# path or imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from provision_go import provision_go


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows so
    both deploy phases reach the same eligibility decision and the determinism
    guard holds. The Ubuntu demo host is the one ``provision_go.py`` installs
    the go toolchain on before this example deploys (see the module docstring),
    so the cleans are guarded to that family; on a host outside it the bare
    ``go clean`` shells would fail before doing anything, so the driver skips
    them all instead.
    """
    return host.family == "Ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Clean the build cache, then the module cache, then nothing.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases (a branch on a mutable fact would raise DeterminismError). The first
    two requests each select one cache and -- because the operation is stateless
    -- report changed on *every* run, dry or applied; that is the whole point of
    the port, so the caller owns any gating. The third selects neither cache and
    is the deploy-visible no-op that names itself in the reason.

    No request is elevated: both caches belong to the connecting user, and a
    ``sudo`` clean would empty root's caches instead.
    """
    yield op(
        go.cache_clean,
        name="Clean the build cache",
        when=is_ubuntu,
    )
    yield op(
        go.cache_clean,
        name="Clean the module cache",
        clear_build_cache=False,
        clear_mod_cache=True,
        when=is_ubuntu,
    )
    yield op(
        go.cache_clean,
        name="Select no cache (no-op)",
        clear_build_cache=False,
        clear_mod_cache=False,
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Install the go toolchain on the Ubuntu hosts before anything below
        # shells out to go, so the example runs even on a host where go was
        # never installed (see provision_go.py).
        print("\n== Provisioning golang-go (provision_go.py) ==")
        await provision_go(inventory)

        # --dry: prepare phase only. The stateless operation consults no fact,
        # so the plan is pure request reading: both selected-cache requests show
        # as changes and nothing is sent. A request the ``when`` guard rejects
        # is planned as [skipped: when guard is_ubuntu] without materializing
        # it.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH. The
        # two cleans send their shells; the neither-caches request logs as a
        # no-op.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema. The
        # message names the offending entry by its list index, and it fails
        # before any host is materialized.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command (a missing go
        # binary surfaces as CommandNotFoundError, a CommandError subclass).
        # Print that message -- no traceback -- and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
