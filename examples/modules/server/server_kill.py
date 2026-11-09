# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: find a process's PID, then kill it -- in ONE deterministic deploy.

This is a *usage* example, not part of the library. It shows reemote5's
``server.kill`` operation, the pyinfra ``server.kill`` parity for sending a
signal to a running process. Like pyinfra, the operation takes an explicit
``pid`` (and an optional ``signal``, default ``TERM``): discovering *which*
PID to kill is the caller's job, and this script demonstrates the
determinism-safe way to do it.

The deploy generator cannot read the ``Processes`` fact itself: it is
synchronous (there is nothing to ``await`` inside it), and branching it on
live process state would break the determinism guard -- the generator runs
twice (prepare against pre-deployment state, apply against live state), so
a state-dependent branch can yield a different request sequence per phase
and ``run_apply`` raises ``DeterminismError``. The sanctioned move is the
snapshot half of the ``two_deploy_split`` pattern:

1. A fact SNAPSHOT taken BEFORE the run: ``gather_fact`` fans the
   ``Processes`` read out across the guarded hosts and returns a
   ``{host.name: {pid: info}}`` mapping of the real process table.
2. ``build_kill(snapshot)`` -- a factory closing over that frozen mapping,
   returning the deploy. Its loop consults the snapshot, never live state,
   so the generator is a pure function of static input: prepare and apply
   yield the identical ``op(server.kill, pid=...)`` sequence and the
   determinism guard holds.

Unlike ``two_deploy_split`` no FIRST deploy is needed here: nothing mutates
the process table before the snapshot is read, so one snapshot plus one
complete prepare/apply cycle is the whole story. (The full two-deploy
split earns its keep when an earlier deploy step changes the fact the
second deploy branches on -- install nginx, then configure it.)

The operation itself is stateless and non-idempotent, exactly like
pyinfra: it reads no fact and always yields one ``kill -<signal> <pid>``
command, so every planned kill shows as a change. The signal flag and the
PID are shell-quoted, so a signal string carrying metacharacters reaches
``kill`` literally. The requests carry ``sudo=True`` because the nginx
master process runs as root; the run-as user and any password come from
the inventory ``privilege`` section, never from the operation.

The trade-off: the snapshot is frozen. If nginx exits between the snapshot
and the apply, ``kill`` targets a dead PID, exits non-zero and the run
fails with ``CommandError`` (pyinfra behaves the same). Re-running the
whole script is naturally safe: it re-snapshots, finds no nginx PID and
plans an empty deploy.

Every failure mode of the run -- an unreachable host, a rejected elevation,
or a ``kill`` that exits non-zero -- reaches ``main`` as a typed error whose
message already names the host, so ``main`` catches them and prints one
line each instead of a traceback.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``): each plain ``Host``
lazily opens an asyncssh connection, reads the process table for real, and
runs ``kill`` under sudo where needed.

The hosts come from the shared ``inventory.json`` in the project root,
read with ``load_inventory`` (parse + ``validate_inventory``) and resolved
from ``__file__`` so the example works from any working directory. The
snapshot gather is guarded by ``is_centos`` (``host.group == "centos"``):
``gather_fact`` takes no ``when`` parameter, so the predicate filters the
host list before the fan-out and hosts outside the ``"centos"`` group are
named on a ``== Skipped (when guard is_centos): ... ==`` line and never
connected. Every kill request also carries ``when=is_centos``, and a
skipped host has no snapshot entry, so its deploy yields nothing.

Run it with::

    uv run python examples/modules/server/server_kill.py
"""

import asyncio
import json
import sys
from collections.abc import Callable, Generator
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
    gather_fact,
    op,
    run,
    server,
    validate_inventory,
)

# The process to kill, matched against the ``command`` column of the
# ``Processes`` fact (the ps ``comm`` field), and the signal to send.
PROCESS_NAME = "nginx"
SIGNAL = "TERM"

# examples/modules/server/server_kill.py -> the project root, so the
# inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries
    each carrying at least ``host`` and ``connection``. Returns the parsed
    list, ready to materialize into hosts; raises ``InventoryError`` (via
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
    so both deploy phases reach the same eligibility decision.
    """
    return host.group == "centos"


def build_kill(
    snapshot: dict[str, dict[int, dict]],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy factory: close over the frozen pre-run Processes snapshot.

    The returned generator loops over ``snapshot.get(host.name, {})`` --
    an ordinary dict taken BEFORE this deploy's prepare phase -- so both
    phases resolve the loop identically, and a host the ``is_centos``
    guard kept out of the snapshot simply yields nothing. Reading the
    live ``Processes`` fact here instead is impossible (the generator is
    synchronous) and branching on live host state would re-introduce the
    phase-dependent request sequence the determinism guard forbids. PIDs
    are visited in sorted order so multi-match hosts plan a stable
    sequence.
    """

    def deploy(host: Host) -> Generator[OperationRequest]:
        for pid, info in sorted(snapshot.get(host.name, {}).items()):
            if info["command"] == PROCESS_NAME:
                yield op(
                    server.kill,
                    name=f"Kill {PROCESS_NAME} ({pid})",
                    pid=pid,
                    signal=SIGNAL,
                    sudo=True,
                    when=is_centos,
                )

    return deploy


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # The snapshot: one concurrent Processes read across the guarded
        # hosts, keyed by host name, before any deploy runs. gather_fact has
        # no ``when`` parameter, so the is_centos predicate filters the host
        # list before the fan-out -- a skipped host is never connected. These
        # hosts are ours to close; run() below builds fresh ones from the
        # same inventory.
        all_hosts = [Host.from_inventory(entry) for entry in inventory]
        hosts = [host for host in all_hosts if is_centos(host)]
        skipped = [host.name for host in all_hosts if host not in hosts]
        if skipped:
            print("\n== Skipped (when guard is_centos): " + ", ".join(skipped) + " ==")
        try:
            snapshot = await gather_fact(hosts, server.Processes)
        finally:
            for host in hosts:
                await host.aclose()
        print("== Pre-run snapshot (Processes) ==")
        for name, processes in snapshot.items():
            pids = [
                pid
                for pid, info in sorted(processes.items())
                if info["command"] == PROCESS_NAME
            ]
            print(f"{name}: {PROCESS_NAME} pids {pids or 'NONE'}")

        # Build the deploy ONCE, before either of its phases: both the dry
        # run and the real run use the very same callable closing over the
        # same frozen snapshot -- determinism by construction.
        deploy = build_kill(snapshot)

        # --dry: prepare phase only. The plan is honest because the loop
        # consults the pre-run snapshot, not some later state.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. Each
        # matched PID is killed under sudo; a host with no match idles.
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
    except HostConnectionError as exc:
        # A host could not be reached over SSH at all. Report it plainly --
        # naming every unreachable host -- instead of dumping an asyncssh
        # traceback, and exit non-zero so the failure is script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except PrivilegeError as exc:
        # The host was reached but rejected the ``sudo=True`` elevation -- a
        # wrong ``sudo_password``, or a run-as user the login user may not
        # become. One line naming the host; the detail stays on ``__cause__``.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except CommandError as exc:
        # A ``kill`` invocation exited non-zero -- most likely the PID
        # exited between the snapshot and the apply (the frozen-snapshot
        # trade-off). The typed message already names the host, the
        # credential-free command and its stderr, so print just that.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())