# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install and remove crates with ``cargo install`` and ``cargo uninstall``.

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

``cargo.packages`` is reemote's port of the baseline's ``cargo.packages``: it
installs, upgrades or removes the ``cargo install`` crates on a host, given as
bare crate names or ``<pkg>=<version>`` pins. The operation is idempotent on
the ``CargoPackages`` fact (the ``cargo install --list`` header rows): a
pinned request is present only when that *exact* version is installed, while an
unpinned one -- and one pinned to the special ``latest`` -- is present when the
crate is installed at any version, so re-running is a no-op. The script yields
the *same* install request twice and the *same* removal request twice to show
that idempotence across the two phases: the prepare phase plans both halves of
each pair as changes (nothing is sent yet), while the apply phase runs the first
and reports the second as a no-op, because the fresh per-step fact cache
re-reads the listing against the updated state. Installing and then removing the
same crates makes the example *self-restoring*: it leaves the host's
``~/.cargo`` tree as it found it (as long as the crates were not installed
beforehand), which matters because ``run_examples.py`` sweeps every example over
the same shared hosts.

The two crates show both presence rules. ``cargo-edit=0.11.6`` is reemote's one
user-facing pin spelling -- shared with apt, dnf, apk, npm and go -- and rides
through to the install shell translated into cargo's own grammar as
``cargo-edit@0.11.6`` (the baseline's ``@`` input spelling is deliberately not
accepted); ``ripgrep`` is unpinned, so it renders as the bare crate name --
cargo resolves the newest version from crates.io, which has no ``latest``
version to name (deliberately unlike go, whose shell demands ``@latest``).

Removal is version-aware, the baseline verbatim and deliberately unlike go's
name-based one: cargo tracks its global installs, so a bare name removes the
crate at any version while a pin fires only for that *exact* installed version,
and each token keeps its pin in the command -- the unpinned ``ripgrep`` leaves
nothing behind and ``cargo-edit=0.11.6`` uninstalls as ``cargo-edit@0.11.6``,
cargo selecting the version through its own package-ID spec.

No request flags ``sudo``: ``cargo install`` and ``cargo uninstall`` both act
on the *connecting user's* ``~/.cargo`` tree, and elevating them would install
into root's tree -- one the unprivileged ``CargoPackages`` read never sees. One
behavior is demonstrated by the pytest suite rather than here: ``latest=True``
keeps the baseline's documented non-idempotence, re-issuing
``cargo install <crate>`` for every installed unpinned request and reporting
changed on every run, because there is no registry to compare against offline.

Like the other examples, every request carries ``when=is_ubuntu``: the shared
``inventory.json`` holds one host in the ``"Ubuntu"`` family alongside hosts in
other families, so each run installs and removes on the Ubuntu host and skips
every other one -- on a host outside the group the driver skips each request
without materializing it, so plan and log render ``[skipped: when guard
is_ubuntu]``. That guard is not cosmetic: unlike the go module's self-guarded
fact command, this module runs its commands bare, so on an unprovisioned host
even the *fact* gather exits 127 and raises ``CommandNotFoundError``. cargo is
*not* installed by default on the demo host -- Ubuntu ships no ``cargo`` until
you ask -- so this example provisions the tooling itself before deploying:
``main`` first awaits ``provision_cargo`` from the sibling
``provision_cargo.py``, which installs the ``cargo`` deb with ``apt.packages``
(idempotent, so the run is cheap on a host that already has it). The example
therefore runs even on a host where cargo was never installed.

Run it with::

    uv run python examples/modules/cargo/cargo_packages.py
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
    cargo,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# The cargo provisioning helper sits beside this script; add its directory to
# sys.path so ``import provision_cargo`` resolves whether the example is run by
# path or imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from provision_cargo import provision_cargo

# The crates to install and remove again -- one ``<pkg>=<version>`` pin and one
# bare crate name, so the run shows both of the fact's presence rules. A
# module-level tuple, so the request kwargs stay hashable and identical in the
# prepare and apply phases.
PACKAGES = ("cargo-edit=0.11.6", "ripgrep")


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows so
    both deploy phases reach the same eligibility decision and the determinism
    guard holds. The Ubuntu demo host is the one ``provision_cargo.py`` installs
    the cargo toolchain on before this example deploys (see the module
    docstring), so the install/removal is guarded to that family; on a host
    outside it the bare ``cargo`` commands -- and the fact read behind them --
    would fail before doing anything, so the driver skips them all instead.
    """
    return host.family == "Ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Install the crates -- twice, to show idempotence -- then remove them.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases (a branch on a mutable fact would raise DeterminismError). The first
    request installs whatever the ``cargo install --list`` reading does not
    carry yet; the second carries the same arguments and is a no-op in the apply
    phase once the first install landed. The third removes the crates again
    (``present=False``) and the fourth is its no-op repeat, leaving the host's
    ``~/.cargo`` tree as found so the shared-host example sweep is not polluted.

    No request is elevated: both commands work on the connecting user's cargo
    tree, and ``sudo`` would install into root's ``~/.cargo`` instead.
    """
    yield op(
        cargo.packages,
        name="Install cargo-edit and ripgrep",
        packages=PACKAGES,
        when=is_ubuntu,
    )
    yield op(
        cargo.packages,
        name="Re-run install (no-op)",
        packages=PACKAGES,
        when=is_ubuntu,
    )
    yield op(
        cargo.packages,
        name="Remove them again",
        packages=PACKAGES,
        present=False,
        when=is_ubuntu,
    )
    yield op(
        cargo.packages,
        name="Re-run removal (no-op)",
        packages=PACKAGES,
        present=False,
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Install the cargo toolchain on the Ubuntu hosts before anything below
        # reads or mutates cargo state, so the example runs even on a host where
        # cargo was never installed (see provision_cargo.py).
        print("\n== Provisioning cargo (provision_cargo.py) ==")
        await provision_cargo(inventory)

        # --dry: prepare phase only. Facts are still gathered over SSH (the
        # crate listing is read-only) so the plan reflects each host's real
        # pre-deployment state; no command that mutates a host is sent. A
        # request the ``when`` guard rejects is planned as [skipped: when guard
        # is_ubuntu] without materializing it.
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
        # whose message names the host and the failing command (a missing cargo
        # binary surfaces as CommandNotFoundError, a CommandError subclass --
        # and here even the *fact* read raises it, the command being bare).
        # Print that message -- no traceback -- and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
