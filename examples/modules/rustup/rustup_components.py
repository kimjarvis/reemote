# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Add and remove toolchain components with ``rustup component``.

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

``rustup.components`` adds or removes components -- ``clippy``, ``rustfmt``,
``llvm-tools-preview``, ``rust-src`` -- of one toolchain. It is the exact sibling
of ``rustup.targets``: the idempotence reads the ``RustupComponents`` fact
(``rustup [+<toolchain>] component list --installed``) for the toolchain the
commands mutate, ``present=True`` adds only the missing names in one
``rustup component add`` batch, and ``present=False`` removes only the ones it
reports installed. Component names carry their target suffix verbatim
(``rustfmt-x86_64-unknown-linux-gnu``) -- the fact reports what rustup prints and
the comparison is name-for-name -- so the bare ``clippy``/``rustfmt`` names this
example uses are exactly what the read returns for a host toolchain.

The requests cover setup, add, idempotence and removal. The first is a
``rustup.toolchains`` request that *installs stable with the minimal profile* --
minimal deliberately omits ``clippy``/``rustfmt``, so the add below has real work
to do and the demo is meaningful on a fresh host. The second adds the two lint
components to ``stable``; the third repeats the same add and is the honest no-op
the apply phase reports; the fourth removes them again (``present=False``), so the
toolchain's component list ends where the setup step left it -- fully
self-restoring.

No request flags ``sudo``: ``rustup component`` writes the *connecting user's*
``~/.rustup`` tree, and the fact reads that same tree, so elevating would manage
root's components instead. The ``+<toolchain>`` selector is rustup's top-level
argument and rides *before* the ``component`` subcommand (``rustup +stable
component add ...``), the grammar the shipped ``RustupComponents`` fact follows.

Like the other examples, every request carries ``when=is_ubuntu``: the shared
``inventory.json`` holds one host in the ``"Ubuntu"`` family alongside hosts in
other families, so each run adds and removes on the Ubuntu host and skips every
other one -- plan and log render ``[skipped: when guard is_ubuntu]`` for the rest.
The guard is not cosmetic: the ``rustup`` commands are not self-guarding, so on an
unprovisioned host they fail with ``CommandNotFoundError``. rustup is *not*
installed by default on the demo host, so ``main`` first awaits
``provision_rustup`` from the sibling ``provision_rustup.py``, which installs the
``rustup`` deb with ``apt.packages`` (idempotent, so the run is cheap on a host
that already has it).

Run it with::

    uv run python examples/modules/rustup/rustup_components.py
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
    op,
    run,
    rustup,
    validate_inventory,
)

# The rustup provisioning helper sits beside this script; add its directory to
# sys.path so ``import provision_rustup`` resolves whether the example is run by
# path or imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from provision_rustup import provision_rustup

# The two lint components the run adds to ``stable`` and removes again, and the
# toolchain they are added to. Module-level tuples, so the request kwargs stay
# hashable and identical in the prepare and apply phases.
COMPONENTS = ("clippy", "rustfmt")
TOOLCHAIN = "stable"


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows so
    both deploy phases reach the same eligibility decision and the determinism
    guard holds. The Ubuntu demo host is the one ``provision_rustup.py`` installs
    the rustup installer on before this example deploys (see the module
    docstring), so the component management is guarded to that family; on a host
    outside it the bare ``rustup`` commands -- and the fact read behind them --
    would fail before doing anything, so the driver skips them all instead.
    """
    return host.family == "Ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Ensure a minimal stable, add two components twice, then remove them.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases (a branch on a mutable fact would raise DeterminismError). The first
    request is setup (a ``rustup.toolchains`` install of ``stable`` with the
    ``minimal`` profile, which leaves clippy/rustfmt absent so the add is real);
    the second adds the two components, the third is its no-op repeat once they
    are present, and the fourth removes them again (``present=False``).

    No request is elevated: the components live in the connecting user's
    ``~/.rustup`` tree, and ``sudo`` would manage root's instead.
    """
    yield op(
        rustup.toolchains,
        name="Ensure minimal stable toolchain (setup)",
        toolchains=(TOOLCHAIN,),
        profile="minimal",
        default=TOOLCHAIN,
        when=is_ubuntu,
    )
    yield op(
        rustup.components,
        name="Add clippy and rustfmt",
        components=COMPONENTS,
        toolchain=TOOLCHAIN,
        when=is_ubuntu,
    )
    yield op(
        rustup.components,
        name="Re-run add (no-op)",
        components=COMPONENTS,
        toolchain=TOOLCHAIN,
        when=is_ubuntu,
    )
    yield op(
        rustup.components,
        name="Remove them again",
        components=COMPONENTS,
        toolchain=TOOLCHAIN,
        present=False,
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Install the rustup installer on the Ubuntu hosts before anything below
        # reads or mutates rustup state, so the example runs even on a host where
        # rustup was never installed (see provision_rustup.py).
        print("\n== Provisioning rustup (provision_rustup.py) ==")
        await provision_rustup(inventory)

        # --dry: prepare phase only. Facts are still gathered over SSH (the
        # installed-component listing is read-only) so the plan reflects each
        # host's real pre-deployment state; no command that mutates a host is
        # sent. A request the ``when`` guard rejects is planned as [skipped:
        # when guard is_ubuntu] without materializing it.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
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
        # whose message names the host and the failing command (a missing rustup
        # binary surfaces as CommandNotFoundError, a CommandError subclass --
        # and here even the *fact* read raises it, the command being bare).
        # Print that message -- no traceback -- and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
