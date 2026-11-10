# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Manage a deb822 apt sources file, with fact-based idempotence.

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

``apt.sources_file`` writes (or with ``present=False`` removes) a complete
deb822 ``.sources`` file, mirroring the baseline's ``apt.sources_file`` -- here
the Docker repository as ``/etc/apt/sources.list.d/docker.sources``. The
repository is described structurally (``types``, ``uris``, ``suites``,
``components``, ``architectures``, ``signed_by``) rather than as a source
line, and each list option expands into one entry per combination. Unlike
the baseline -- whose ``apt.sources_file`` delegates the write to ``files.put``
-- reemote5 keeps the namespaces separate: the generated deb822 text is
spooled to ``/tmp`` by an unprivileged scp upload (a transfer never
elevates), then an elevated ``sh -c`` installs it into ``sources.list.d``
with mode ``0644`` and removes the spool file -- the same privilege split as
``apt.key``. The removal direction is idempotent too: the ``AptSourcesFile``
fact reads the file's content (None when absent), so ``present=False`` yields
``rm -f`` only when the file exists -- the same in-namespace existence gate
``dnf.repo`` uses, without importing another namespace's fact.

``apt.sources_file`` compares every desired entry against the ``AptSources``
fact, so a file whose repositories are all already configured is a no-op.
The script yields the *same* write request twice to show that idempotence
across the two phases: the prepare phase plans both as changes (nothing is
written yet, so both compare against the same pre-deployment sources), while
the apply phase writes on the first and reports the second as a no-op, because
the fresh per-step fact cache now sees the entries the first step wrote. A
third request then removes the file again (``present=False``), so the example
is *self-restoring*: it leaves the host's apt sources exactly as it found them.
That matters because ``run_examples.py`` sweeps every example over the same
shared hosts -- a lingering ``docker.sources`` whose ``signed-by`` keyring is
not installed would make the later ``apt_update.py`` example fail (``apt-get
update`` exits 100 on an unsigned repository). The write-then-remove cycle
keeps the sweep clean.

Installing under ``/etc/apt`` needs root -- the files are root-owned -- so
the requests are flagged ``sudo=True``; the driver elevates the install
shell over SSH, while the scp upload stays unprivileged either way.

Like the other apt examples, every request carries ``when=is_ubuntu``: the
predicate reads only static inventory data (``host.group``) and is a stable
module-level function, so both phases reach the same skip decision. The
driver skips a rejected request without materializing it -- neither the
scp spool and install nor the ``AptSources`` fact behind them runs on a
non-Ubuntu host, whose package manager would never read the apt sources --
and plan and log render ``[skipped: when guard is_ubuntu]``. The shared
``inventory.json`` holds one host in the ``"ubuntu"`` group alongside hosts
in other groups, so each run writes on the Ubuntu host and skips every
other one.

Run it with::

    uv run python examples/modules/apt/apt_sources_file.py
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

# examples/modules/apt/apt_sources_file.py -> the project root, so the
# inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The Docker repository as a deb822 sources file:
# /etc/apt/sources.list.d/docker.sources, for Ubuntu noble (the target host's
# suite), signed by the keyring a manual step (or apt.key) places at
# /etc/apt/keyrings/docker.gpg. Shared by the write requests below so they
# carry identical operation kwargs (only the log ``name`` differs). Tuples
# keep the containers hashable and stable across the prepare and apply phases.
DOCKER_SOURCES = {
    "filename": "docker",
    "types": ("deb",),
    "uris": ("https://download.docker.com/linux/ubuntu",),
    "suites": ("noble",),
    "components": ("stable",),
    "architectures": ("amd64",),
    "signed_by": "/etc/apt/keyrings/docker.gpg",
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
    sources file the requests write under ``/etc/apt`` is only read by apt,
    which a non-Ubuntu host does not run, so the driver skips them there.
    """
    return host.group == "ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Write the Docker deb822 sources file -- twice, then remove it.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request spools and installs the file; the second carries the
    same arguments and is a guaranteed no-op in the apply phase, because
    the first write is already visible through the fresh per-step fact
    cache. The third removes the file again (``present=False``), leaving the
    host's sources as found so the shared-host example sweep is not polluted
    -- a lingering ``docker.sources`` with no installed ``signed-by`` keyring
    would break the later ``apt_update.py`` step.

    Installing under the root-owned /etc/apt needs root, so all three requests
    are flagged ``sudo=True``; only the install shell is elevated -- the
    scp spool upload is a transfer and always runs unprivileged.
    """
    yield op(
        apt.sources_file,
        name="Add Docker apt repository (deb822)",
        sudo=True,
        when=is_ubuntu,
        **DOCKER_SOURCES,
    )
    yield op(
        apt.sources_file,
        name="Re-add Docker apt repository (no-op)",
        sudo=True,
        when=is_ubuntu,
        **DOCKER_SOURCES,
    )
    yield op(
        apt.sources_file,
        name="Remove Docker apt repository",
        filename=DOCKER_SOURCES["filename"],
        present=False,
        sudo=True,
        when=is_ubuntu,
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