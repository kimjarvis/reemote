# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: a health-check style deploy combining dnf, files.put and files.get.

This is a *usage* example, not part of the library. It is the reemote5 take on
a relatable pyinfra scenario -- a simple web-server health-check / log-rotation
deploy -- in one flat generator that crosses module boundaries:

1. ``dnf.packages`` installs a lightweight, harmless tool (``htop``) so the
   operator can inspect the host; installing a system package needs root, so
   the request is flagged ``sudo=True``.
2. ``files.put`` pushes a message-of-the-day to ``/tmp/remote_motd``,
   simulating a configuration or static-asset deployment. The transfer is
   unprivileged (scp rides the connection as the login user), and ``/tmp`` is
   writable by that user, so no escalation applies -- ``sudo``/``su`` are
   deliberately *not* options on a transfer.
3. ``files.get`` pulls ``/etc/os-release`` -- present and world-readable on
   every Fedora/RHEL host, so a safe stand-in for "fetch the logs" -- back
   into an in-memory stream the script prints afterwards.

Where the pyinfra original works with local *paths* (``src="files/motd"``,
``dest="local_os_release.txt"``, ``add_deploy_dir=True``), reemote5's transfer
operations take **IO-like objects only**: the put source is a module-level
``io.StringIO`` (no local ``files/`` directory needed) and the get destination
is a per-host ``io.BytesIO`` sink. ``files.put`` also has no ``mode`` option;
ownership/permissions would be a follow-up elevated shell operation if needed.
Both streams obey the usual rules: created once at module level so both phases
pass the identical object (``OperationRequest`` compares the stream
by identity), and -- because a get *writes* its destination -- one sink per
host so the two hosts never clobber each other in lockstep.

The deploy is branch-free, so the request sequence is identical in the prepare
and apply phases; the put and get idempotence checks are content-based (sha1),
so a second run only transfers what actually differs.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``): each plain ``Host``
lazily opens an asyncssh connection, gathers facts, runs the elevated dnf
command, and transfers content over scp for real.

``main`` also owns the error reporting, as every real-host example here does:
the library is CLI-free and *raises*, so ``main`` catches its typed errors
(``HostConnectionError``, ``PrivilegeError``, ``CommandError``) and prints the
message -- which already names the host and the failing command -- on stderr
before exiting non-zero. Installing ``htop`` against a host without
EPEL enabled therefore reports ``command failed on web1 (exit 1): sudo dnf
install -y htop`` plus dnf's own ``Error: Unable to find a match: htop``, never
a Python traceback.

Every request carries ``when=is_centos``, the declarative eligibility
guard: the predicate reads only **static** inventory data --
``host.group``, set by the ``"group"`` key of the inventory entry -- and
is a stable module-level function, so the prepare and apply phases reach
the same skip decision. The driver skips a rejected request without
materializing it, so on hosts outside the ``"centos"`` group neither the
commands nor the facts behind them run, and plan and log render the step
as ``[skipped: when guard is_centos]`` in its lockstep slot. The path is
resolved from ``__file__``, so the example works from any working
directory; point ``INVENTORY_PATH`` at your own file to target your
machines.

The path is resolved from ``__file__``, so the example works from any
working directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/files/dnf_put_get.py
"""

import asyncio
import io
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
    dnf,
    files,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# The put source, created once on the control machine: a stream, never a local
# path. The same object is passed to put in every phase, so the prepare and
# apply request sequences compare equal (OperationRequest compares it by
# identity), and it is rewound to position 0 before every read.
MOTD_SRC = io.StringIO("Managed by reemote5 -- report problems to the ops team.\n")

# The remote destination for the motd: under /tmp, so the unprivileged scp
# transfer can write it as the login user.
REMOTE_MOTD = "/tmp/remote_motd"

# The remote source to pull back: standard and world-readable on every
# Fedora/RHEL host, standing in for a log or diagnostic file.
REMOTE_OS_RELEASE = "/etc/os-release"

# One get sink per host, created once and reused across both phases: stable
# identity for the determinism guard, and per-host isolation because a get
# writes into its destination (two hosts sharing one stream would clobber
# each other in lockstep).
SINKS: dict[str, io.BytesIO] = {}

# examples/modules/files/dnf_put_get.py -> the project root, so the inventory
# is found whatever the current working directory is.
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


def deploy(host: Host) -> Generator[OperationRequest]:
    """Install htop, push the motd, and pull the remote OS release info.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    Only the dnf install is elevated; the two transfers ride the connection's
    scp channel as the login user. The per-host sink is fetched, not created
    fresh per call, so both phases pass the identical stream object.
    """
    yield op(
        dnf.packages,
        name="Install htop via dnf",
        packages=("htop",),
        sudo=True,
        when=is_centos,
    )
    yield op(
        files.put, name="Upload local motd to remote", src=MOTD_SRC, dest=REMOTE_MOTD,
        when=is_centos,
    )
    sink = SINKS.get(host.name)
    if sink is None:
        sink = SINKS[host.name] = io.BytesIO()
    yield op(
        files.get,
        name="Download remote OS release info",
        src=REMOTE_OS_RELEASE,
        dest=sink,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent and no content is transferred.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH. The
        # dnf install is elevated with sudo, the put uploads over scp when the
        # remote motd differs, and the get downloads /etc/os-release into each
        # host's sink.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))

        # The point of an IO-like destination: the downloaded bytes are right
        # here in a Python variable -- print each host's OS release info straight
        # from the stream, with no local file involved.
        print("\n== Remote OS release info per host ==")
        for name, sink in SINKS.items():
            print(f"--- {name} ---")
            print(sink.getvalue().decode().strip())
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
