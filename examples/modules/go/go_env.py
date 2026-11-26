# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Persist and clear ``go env`` settings with ``go env -w`` / ``go env -u``.

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

``go.env`` persists or removes go's own configuration -- the ``go env`` file a
host keeps across shells, where ``GOPATH``, ``GOPROXY`` or ``GOFLAGS`` actually
live. Its one option, ``settings``, is a tuple of ``(key, value)`` pairs (the
mapping spelled as pairs because ``op()`` rejects an unhashable dict), and a
``None`` value means *unset this key*. The operation is idempotent per key
against the ``GoEnv`` fact: an unset fires only for a key the host currently
holds a non-empty value for, a set only for a key whose value drifts, so a
converged request yields no command at all.

The script shows both directions and the no-op repeat, and it is
*self-restoring*: it sets ``GOPRIVATE`` to a demo value, re-asserts the same
request (a guaranteed no-op in the apply phase through the fresh per-step fact
cache), then unsets the key and re-runs the unset (its own no-op). That order
leaves the go env file as it found it -- as long as ``GOPRIVATE`` held no
persistent value beforehand, the caveat ``go env -u`` cannot undo. ``GOPRIVATE``
is the safest demo key: it only matches module paths that nothing else in the
example sweep fetches, so the temporary value disturbs no other example.

No request flags ``sudo``: ``go env -w``/``-u`` edit the *connecting user's* go
env file, and elevating them would edit root's instead -- a file the
unprivileged ``GoEnv`` read never sees.

Like the other examples, every request carries ``when=is_ubuntu``: the shared
``inventory.json`` holds one host in the ``"Ubuntu"`` family alongside hosts in
other families, so each run writes and clears on the Ubuntu host and skips every
other one -- plan and log render ``[skipped: when guard is_ubuntu]`` for the
rest. The guard is not cosmetic: the ``go env`` shells themselves are not
self-guarding, so on an unprovisioned host they would fail with
``CommandNotFoundError``. go is *not* installed by default on the demo host, so
``main`` first awaits ``provision_go`` from the sibling ``provision_go.py``,
which installs the ``golang-go`` deb with ``apt.packages`` (idempotent, so the
run is cheap on a host that already has it).

Run it with::

    uv run python examples/modules/go/go_env.py
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

# The settings to persist, then to clear again -- module-level tuples of
# ``(key, value)`` pairs so the request kwargs stay hashable and identical in
# the prepare and apply phases. A ``None`` value in the second constant is the
# operation's *unset* marker, not a missing value.
SETTINGS_SET = (("GOPRIVATE", "internal.example.com"),)
SETTINGS_UNSET = (("GOPRIVATE", None),)


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows so
    both deploy phases reach the same eligibility decision and the determinism
    guard holds. The Ubuntu demo host is the one ``provision_go.py`` installs
    the go toolchain on before this example deploys (see the module docstring),
    so the writes are guarded to that family; on a host outside it the bare
    ``go env`` shells would fail before doing anything, so the driver skips
    them all instead.
    """
    return host.family == "Ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Set the demo value -- twice -- then unset it -- twice.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases (a branch on a mutable fact would raise DeterminismError). The first
    request writes whatever the ``GoEnv`` listing does not carry yet; the second
    carries the same arguments and is a no-op in the apply phase once the first
    landed, because the fresh per-step fact cache re-reads the configuration
    against the updated state. The third unsets the key (``None`` value) and the
    fourth is its no-op repeat, leaving the go env file as found so the
    shared-host example sweep is not polluted.

    No request is elevated: both shells edit the connecting user's go env
    file, and ``sudo`` would touch root's instead.
    """
    yield op(
        go.env,
        name="Persist the demo GOPRIVATE",
        settings=SETTINGS_SET,
        when=is_ubuntu,
    )
    yield op(
        go.env,
        name="Re-assert the same settings (no-op)",
        settings=SETTINGS_SET,
        when=is_ubuntu,
    )
    yield op(
        go.env,
        name="Clear the demo GOPRIVATE",
        settings=SETTINGS_UNSET,
        when=is_ubuntu,
    )
    yield op(
        go.env,
        name="Re-run the unset (no-op)",
        settings=SETTINGS_UNSET,
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Install the go toolchain on the Ubuntu hosts before anything below
        # reads or mutates go state, so the example runs even on a host where
        # go was never installed (see provision_go.py).
        print("\n== Provisioning golang-go (provision_go.py) ==")
        await provision_go(inventory)

        # --dry: prepare phase only. Facts are still gathered over SSH (the
        # ``go env`` listing is read-only) so the plan reflects each host's real
        # pre-deployment state; no command that mutates a host is sent. A
        # request the ``when`` guard rejects is planned as
        # [skipped: when guard is_ubuntu] without materializing it.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH. The
        # first set writes the go env file; its no-op repeat, the unset and the
        # unset's repeat follow.
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
        # binary surfaces as CommandNotFoundError, a CommandError subclass --
        # only the *fact* read swallows it, never the ``go env -w``/``-u``
        # shells). Print that message -- no traceback -- and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
