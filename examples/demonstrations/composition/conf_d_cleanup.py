# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: the DataDog conf.d cleanup -- ensure tracked checks, remove the rest.

This is a *usage* example, not part of the library. It is the recipe the
directory-listing facts exist for: the DataDog Ansible role's cleanup
question, "delete every ``conf.d/*/conf.yaml`` that is NOT one of the
tracked checks" -- names the deploy author does not know, so no static
deploy can enumerate them. Written as ONE deploy it is doubly illegal:
the enumeration is a mutable-fact read, and branching on it after the
install steps would yield a different request sequence per phase and
trip ``DeterminismError``. Like ``two_deploy_split.py``, the fix moves
the read into the gap between two complete prepare/apply cycles:

1. ``ensure_tracked_checks`` -- a branch-free deploy that installs the
   tracked checks' ``conf.yaml`` files with ``files.install`` (the
   spool-and-install write: unprivileged scp to a ``/tmp`` spool, then an
   elevated ``sh -c`` install, so the root-owned ``/etc`` destination is
   one request). Both phases yield the same one request per check.
2. A fact SNAPSHOT taken between the runs: ``gather_fact`` fans
   ``FindFiles`` (``fname="conf.yaml"``, ``maxdepth=2`` -- exactly the
   ``conf.d/<check>/conf.yaml`` layer) out across the CentOS hosts and
   returns a ``{host.name: list[str]}`` mapping of what is REALLY on each
   box, tracked and legacy alike. Run 1's apply-phase join is the
   barrier: every host finished installing before any snapshot is read.
   A host without the agent's ``conf.d`` at all reads ``[]`` (the find
   is ``|| true``-guarded), not a failed read.
3. ``build_cleanup_conf_d(found)`` -- a factory closing over that frozen
   dict, returning the second deploy. For each host it subtracts the
   tracked paths from the snapshot's list and yields one ``files.file``
   ``present=False`` removal per stale path. The subtraction consults
   the snapshot, never a live fact, so the generator is a pure function
   of static input: prepare and apply resolve it identically and the
   determinism guard holds. The second run's ``dry=True`` plan is an
   honest preview of exactly what the cleanup would remove.

The trade-off is the same frozen-snapshot one as ``two_deploy_split.py``:
a check config created by something external between the snapshot and
run 2 is not seen, the no-mid-run-replanning guarantee with the boundary
at a point the script controls. Re-running the whole script converges:
run 1 re-installs the tracked files (no-ops once content matches), the
fresh snapshot no longer lists the removed legacy paths, and deploy 2
yields nothing.

The same shape answers the apt legacy-``.list`` cleanup (list
``/etc/apt/sources.list.d`` with ``DirectoryEntries`` or ``FindFiles``,
subtract the managed names) -- swap the directory, the filter and the
tracked set; the two-deploy skeleton is unchanged.

``run`` closes the hosts it owns, but ``aclose`` only resets the cached
connection and ``connect`` is lazy, so ``gather_fact`` on the returned
hosts transparently re-opens them; the script closes them again
afterwards. Run 2 builds fresh hosts from the same inventory.

The inventory is read from the project root's ``inventory.json``
(validated up front with ``validate_inventory``). Both deploys carry
``when=is_centos`` and the snapshot filters the host list the same way
(``gather_fact`` takes no ``when`` parameter), so the work stays on the
CentOS hosts and deploy 2's lookup defaults to empty for the others.

Run it with::

    uv run python examples/demonstrations/composition/conf_d_cleanup.py
"""

import asyncio
import json
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    FindFiles,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    files,
    format_log,
    format_plan,
    gather_fact,
    op,
    run,
    validate_inventory,
)

# The DataDog agent's check-configuration root: each check owns a
# subdirectory holding its conf.yaml, so maxdepth=2 addresses exactly the
# conf.d/<check>/conf.yaml layer the role's cleanup loop enumerates.
CONF_D = "/etc/datadog-agent/conf.d"

# The checks this deploy manages. Everything else the snapshot finds
# under conf.d is legacy -- a check disabled in a previous revision, or
# configured by hand -- and is what deploy 2 removes.
TRACKED_CHECKS = ("cpu", "memory", "network")

# The desired content per tracked check, built once on the control
# machine: plain strs, hashable and equal across phases, so files.install
# needs no identity-stable stream. A minimal instances stub is enough to
# demonstrate the convergence; a real deploy renders its check config.
CHECK_CONF = {
    check: (
        f"# Managed by reemote -- the {check} check, conf.d cleanup example.\n"
        "init_config:\n"
        "\n"
        "instances:\n"
        "  - {}\n"
    )
    for check in TRACKED_CHECKS
}

# examples/demonstrations/composition/conf_d_cleanup.py -> the project
# root, so the inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


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


def is_centos(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"centos"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision. It guards both
    deploys' requests and (as a host-list filter) the ``FindFiles`` snapshot.
    """
    return host.group == "centos"


def ensure_tracked_checks(host: Host) -> Generator[OperationRequest]:
    """Deploy 1: install the tracked checks' configs, branch-free.

    One ``files.install`` per tracked check -- the same request sequence
    in both phases. The spool transfer rides the connection unprivileged
    regardless of ``sudo=True``; the elevated install lands the content
    at the root-owned ``/etc`` destination.
    """
    for check in TRACKED_CHECKS:
        yield op(
            files.install,
            name=f"Ensure {check} check config",
            content=CHECK_CONF[check],
            dest=f"{CONF_D}/{check}/conf.yaml",
            sudo=True,
            when=is_centos,
        )


def build_cleanup_conf_d(
    found: dict[str, list[str]],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 2 factory: close over the frozen post-install find snapshot.

    The returned generator subtracts the tracked paths from
    ``found.get(host.name, ())`` -- an ordinary dict lookup on a snapshot
    taken BEFORE this deploy's prepare phase -- and yields one removal
    per stale path, sorted for a stable request order. Reading the live
    ``FindFiles`` fact here instead would re-introduce the phase-dependent
    branch (deploy 1's installs change what it returns) and trip the
    determinism guard. The snapshot only covers the CentOS hosts, so the
    lookup defaults to empty for the others and every request also
    carries ``when=is_centos``.
    """
    tracked = frozenset(f"{CONF_D}/{check}/conf.yaml" for check in TRACKED_CHECKS)

    def cleanup_conf_d(host: Host) -> Generator[OperationRequest]:
        for stale in sorted(found.get(host.name, ())):
            if stale not in tracked:
                yield op(
                    files.file,
                    name=f"Remove stale check config {stale}",
                    path=stale,
                    present=False,
                    sudo=True,
                    when=is_centos,
                )

    return cleanup_conf_d


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Run 1: prepare + apply the tracked-check installs. Its apply-phase
        # join is the barrier guaranteeing every host finished before the
        # snapshot below. The when=is_centos guard skips the non-CentOS hosts.
        plans, report, hosts = await run(inventory, ensure_tracked_checks)
        print("\n== Deploy 1: ensure tracked check configs ==")
        print(format_plan(plans))
        assert report is not None
        print(format_log(report))

        # The snapshot: one concurrent read of the real conf.d contents,
        # keyed by host name. gather_fact has no ``when`` parameter, so the
        # is_centos predicate filters the host list before the fan-out.
        # run closed these hosts, but connect is lazy, so gather_fact
        # re-opens the eligible ones; close them all again when done.
        centos_hosts = [host for host in hosts if is_centos(host)]
        skipped = [host.name for host in hosts if not is_centos(host)]
        if skipped:
            print("\n== Skipped (when guard is_centos): " + ", ".join(skipped) + " ==")
        try:
            found = await gather_fact(
                centos_hosts, FindFiles, CONF_D, fname="conf.yaml", maxdepth=2
            )
        finally:
            for host in hosts:
                await host.aclose()
        print("\n== Post-install snapshot (FindFiles conf.d/*/conf.yaml) ==")
        tracked = frozenset(f"{CONF_D}/{check}/conf.yaml" for check in TRACKED_CHECKS)
        for name, paths in found.items():
            stale_paths = [path for path in paths if path not in tracked]
            print(f"{name}: {len(paths)} found, {len(stale_paths)} stale")
            for path in stale_paths:
                print(f"  stale: {path}")

        # Build deploy 2 ONCE, before either of its phases: both the dry run
        # and the real run use the very same callable closing over the same
        # frozen dict -- determinism by construction, as in two_deploy_split.
        cleanup = build_cleanup_conf_d(found)

        # --dry for deploy 2: the plan is honest because the removals are
        # computed from the post-install snapshot, not pre-deployment state.
        plans, _, _hosts = await run(inventory, cleanup, dry=True)
        print("\n== Deploy 2 plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run 2: remove the stale configs the snapshot enumerated.
        plans, report, _hosts = await run(inventory, cleanup)
        print("\n== Deploy 2 run log (apply phase) ==")
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