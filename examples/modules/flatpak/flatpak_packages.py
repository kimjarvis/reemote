# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install and remove flatpak applications with ``flatpak install``/``uninstall``.

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
it finishes. The path is resolved from ``__file__``, so the example works
from any working directory; point ``INVENTORY_PATH`` at your own file to
target your machines.

``flatpak.packages`` is reemote5's port of pyinfra's ``flatpak.packages``:
it installs (or with ``present=False`` removes) flatpak applications by
their reverse-domain IDs, optionally from a named ``remote``. The operation
is idempotent on the ``FlatpakPackages`` fact (``flatpak list
--columns=application``): a requested ID absent from the installed list is
queued for install, one present under ``present=False`` for removal, and
flatpak application IDs carry no version to compare, so a listed ID is
present. The script yields the *same* install request twice and the *same*
removal request twice to show that idempotence across the two phases: the
prepare phase plans both halves of each pair as changes (nothing is sent
yet), while the apply phase runs the first and reports the second as a
no-op, because the fresh per-step fact cache re-reads the listing against
the updated state. Installing and then removing the same application makes
the example *self-restoring*: it leaves the host's flatpak state as it found
it (as long as the application was not installed beforehand), which matters
because ``run_examples.py`` sweeps every example over the same shared hosts.

Installing from a ``remote`` names the source flatpak resolves the
application ID against -- ``flathub`` here, the repository behind pyinfra's
own documented example -- and the remote is applied to the install direction
only, as in pyinfra: an uninstall needs no source because the application is
already resolved. The remote and every application ID are shell-quoted in
the rendered commands (``flatpak install --noninteractive flathub
org.videolan.VLC``, then ``flatpak uninstall --noninteractive
org.videolan.VLC``).

Installing and removing both write system state, so the requests are flagged
``sudo=True`` and the driver elevates their commands; the ``flatpak list``
fact read is unprivileged. Like the other flatpak examples, every request
carries ``when=is_ubuntu``: flatpak is set up out of the box on Ubuntu, and
on a host outside that group the driver skips each request without
materializing it -- neither the ``flatpak`` commands nor the ``FlatpakPackages``
fact behind them runs -- so plan and log render
``[skipped: when guard is_ubuntu]``. The shared ``inventory.json`` holds one
host in the ``"ubuntu"`` group alongside hosts in other groups, so each run
installs and removes on the Ubuntu host and skips every other one.

Run it with::

    uv run python examples/modules/flatpak/flatpak_packages.py
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
    flatpak,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# examples/modules/flatpak/flatpak_packages.py -> the project root, so the
# inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The application to install and remove again -- pyinfra's own documented
# example application. A module-level tuple, so the request kwargs stay
# hashable and identical in the prepare and apply phases.
APPLICATIONS = ("org.videolan.VLC",)

# The remote to install from: flathub, the application repository pyinfra's
# flatpak documentation uses. Ignored by the removal direction.
REMOTE = "flathub"


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


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"ubuntu"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision and the
    determinism guard holds. flatpak is set up out of the box on Ubuntu, so
    the install/removal is guarded to that group; on a host without the
    ``flatpak`` binary every request below would fail before doing anything,
    so the driver skips them all instead.
    """
    return host.group == "ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Install the application -- twice, to show idempotence -- then remove it.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request installs whatever IDs the ``FlatpakPackages`` listing
    does not carry yet; the second carries the same arguments and is a
    no-op in the apply phase once the first install listed them. The third
    removes the application again (``present=False``) and the fourth is its
    no-op repeat, leaving the host's flatpak state as found so the
    shared-host example sweep is not polluted.

    Installing and removing both write system state, so all four requests
    are flagged ``sudo=True`` and the driver elevates their commands over
    SSH; only the fact reads stay unprivileged.
    """
    yield op(
        flatpak.packages,
        name="Install VLC from flathub",
        packages=APPLICATIONS,
        remote=REMOTE,
        sudo=True,
        when=is_ubuntu,
    )
    yield op(
        flatpak.packages,
        name="Re-run install (no-op)",
        packages=APPLICATIONS,
        remote=REMOTE,
        sudo=True,
        when=is_ubuntu,
    )
    yield op(
        flatpak.packages,
        name="Remove VLC again",
        packages=APPLICATIONS,
        present=False,
        sudo=True,
        when=is_ubuntu,
    )
    yield op(
        flatpak.packages,
        name="Re-run removal (no-op)",
        packages=APPLICATIONS,
        present=False,
        sudo=True,
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Facts are still gathered over SSH (the
        # flatpak list read is read-only) so the plan reflects each host's
        # real pre-deployment state; no command that mutates a host is sent.
        # A request the ``when`` guard rejects is planned as
        # [skipped: when guard is_ubuntu] without materializing it, so its
        # fact is not read on that host either.
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
