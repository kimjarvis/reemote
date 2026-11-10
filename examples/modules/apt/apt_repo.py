# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Add an apt repository line by spool-and-install, with fact-based idempotence.

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

``run`` is a coroutine (both phases are async), so ``main`` is async and
driven by ``asyncio.run``.

``apt.repo`` adds (or with ``present=False`` removes) one apt source line,
mirroring the baseline's ``apt.repo``. ``src`` is the full source string -- here
the Canonical partner repository for Ubuntu noble -- and ``filename``
scopes it to ``/etc/apt/sources.list.d/canonical-partner.list`` instead of
the main ``/etc/apt/sources.list``. Unlike the baseline -- whose ``apt.repo``
delegates the edit to ``files.line``, patching the file in place with an
``echo >>`` or a ``sed -i`` -- reemote5 *rewrites* the managed file through
the spool-and-install pair ``apt.key`` uses: the file's current text is
read with the ``AptSourcesFile`` content fact, a pure transform adds or
deletes the line, the result is scp'd to a ``/tmp`` spool named after the
managed file, and an elevated ``sh -c`` install overlays the protected file
and removes the spool. Because the transform works on the real content, the
file's other lines, comments and blank lines come back exactly as they were
and the baseline's timestamped sed backup -- non-deterministic between the two
phases -- is gone.

``apt.repo`` parses ``src`` and compares it against the ``AptSources``
fact, so a repository already configured in any sources file is a no-op,
and compares the transformed text against the file it manages, so a line
the file already lists is never written twice. The script yields the *same*
add request twice to show that idempotence across the two phases: the
prepare phase plans both as changes (nothing is written yet, so both
compare against the same pre-deployment sources), while the apply phase
installs the line on the first and reports the second as a no-op, because
the fresh per-step fact cache now sees the file the first step wrote. A
third request then removes the line again (``present=False``), so the
example is *self-restoring*: it leaves the host's apt sources as it found
them. That matters because ``run_examples.py`` sweeps every example over the
same shared hosts -- the Canonical partner archive is unreachable from the
demo Ubuntu host, so leaving it configured makes every later ``apt-get
update`` (the ``apt_update.py`` example) stall on connection timeouts and
emit a warning. The add-then-remove cycle keeps the sweep clean and fast.

Writing the apt sources needs root -- the files are root-owned -- so the
requests are flagged ``sudo=True`` and the driver elevates the install
command over SSH; the spool copy always rides scp as the login user,
because transfers never elevate.

Like the other apt examples, every request carries ``when=is_ubuntu``: the
predicate reads only static inventory data (``host.group``) and is a stable
module-level function, so both phases reach the same skip decision. The
driver skips a rejected request without materializing it -- neither the
spool-and-install pair nor the ``AptSources`` and ``AptSourcesFile`` facts
behind it runs on a non-Ubuntu host, whose package manager would never read
the apt sources -- and plan and log render
``[skipped: when guard is_ubuntu]``. The shared ``inventory.json`` holds one
host in the ``"ubuntu"`` group alongside hosts in other groups, so each run
rewrites the sources on the Ubuntu host and skips every other one.

Run it with::

    uv run python examples/modules/apt/apt_repo.py
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
    apt,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# examples/modules/apt/apt_repo.py -> the project root, so the inventory is
# found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The source line to add: the Canonical partner repository for Ubuntu
# noble, managed in its own /etc/apt/sources.list.d/canonical-partner.list.
# Shared by the add requests below so they carry identical operation kwargs
# (only the log ``name`` differs).
PARTNER_REPO = {
    "src": "deb http://archive.canonical.com/ubuntu noble partner",
    "filename": "canonical-partner",
}


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
    determinism guard holds. apt itself is the reason for the guard: the
    sources the requests write under ``/etc/apt`` are only read by apt,
    which a non-Ubuntu host does not run, so the driver skips them there.
    """
    return host.group == "ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Add the partner repo line -- twice, to show idempotence -- then remove it.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request installs the line; the second carries the same
    arguments and is a guaranteed no-op in the apply phase, because the
    first install is already visible through the fresh per-step fact cache.
    The third removes the line again (``present=False``), leaving the host's
    sources as found so the shared-host example sweep is not polluted -- the
    partner archive is unreachable from the demo host, so a lingering entry
    would stall the later ``apt_update.py`` step on connection timeouts.

    Writing the root-owned apt sources needs root, so all three requests are
    flagged ``sudo=True`` and the driver elevates their install command over
    SSH.
    """
    yield op(
        apt.repo,
        name="Add the partner repo",
        sudo=True,
        when=is_ubuntu,
        **PARTNER_REPO,
    )
    yield op(
        apt.repo,
        name="Re-add the partner repo (no-op)",
        sudo=True,
        when=is_ubuntu,
        **PARTNER_REPO,
    )
    yield op(
        apt.repo,
        name="Remove the partner repo",
        present=False,
        sudo=True,
        when=is_ubuntu,
        **PARTNER_REPO,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent. A request the ``when`` guard rejects is
        # planned as [skipped: when guard is_ubuntu] without materializing it,
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