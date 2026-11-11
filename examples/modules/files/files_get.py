# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: download a remote file into an in-memory stream with files.get.

This is a *usage* example, not part of the library. It shows the mirror of
``files.put``: ``files.get`` does not run a command string, it copies a remote
file's content down over each host's asyncssh connection using the scp
protocol, into an **IO-like object** on the control machine.

The destination is a stream, never a local filename -- the baseline's ``files.get``
writes to a local path (with ``add_deploy_dir``/``create_local_dir``/``force``
options reemote deliberately drops), but here the bytes land in a module-level
``io.BytesIO`` the script reads back afterwards. Two rules follow, mirroring
``files.put``. The stream must be *seekable* and *writable*: it is rewound and
truncated before the download, then rewound again so the caller reads the fresh
content from position 0. And because ``OperationRequest`` equality compares a
stream *by identity*, the deploy must pass the same object in both
phases.

There is one wrinkle the put example does not have: a get *writes* into its
destination, so two hosts must not share one stream -- in lockstep they would
overwrite each other, and each host's second get would see the other host's
bytes and wrongly re-download. The deploy therefore keeps a per-host sink in a
module-level dict, created once per host name and reused across both phases:
stable identity *and* per-host isolation.

The idempotence check is content-based, mirroring ``files.put``: the remote
file's sha1 (``RemoteFileSha1``, gathered with ``sha1sum`` over SSH) is
compared against the destination's current content sha1 (``LocalSha1``), and the
transfer only happens when they differ. The deploy gets the same file twice to
prove it: the second get is a guaranteed no-op because the *first* get's
download is visible to it through the fresh per-step fact cache -- exactly the
"act, then read" property the lockstep apply phase provides.

The transfer rides the connection as the login user, so the source must be
readable by that user (``sudo``/``su`` do not apply to transfers); here it is
``/etc/hostname``, present and world-readable on every host.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``): each plain ``Host`` lazily
opens an asyncssh connection, gathers the remote hash for real, and sends the
file's content over scp into the sink when it differs.

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

    uv run python examples/modules/files/files_get.py
"""

import asyncio
import io
import json
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    files,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# The remote source: present and readable by the login user on every host.
REMOTE_SRC = "/etc/hostname"

# One destination stream per host, created once and reused across both phases:
# the same object is passed to every get for a given host (so the prepare and
# apply request sequences compare equal -- OperationRequest compares it by
# identity), and distinct hosts get distinct sinks (so a get, which *writes*
# into its destination, never lets two hosts clobber each other in lockstep).
SINKS: dict[str, io.BytesIO] = {}

# examples/modules/files/files_get.py -> the project root, so the inventory
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
    """Get the same remote file twice: the second get proves sha1 idempotence.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases (a branch on a mutable fact would raise DeterminismError). Neither
    request is elevated: the copy goes over the connection's scp channel as the
    login user. The per-host sink is fetched, not created fresh per call, so
    both phases -- and both gets -- pass the identical stream object.
    """
    sink = SINKS.get(host.name)
    if sink is None:
        sink = SINKS[host.name] = io.BytesIO()
    yield op(
        files.get,
        name="Fetch hostname",
        src=REMOTE_SRC,
        dest=sink,
        when=is_centos,
    )
    yield op(
        files.get,
        name="Re-fetch hostname",
        src=REMOTE_SRC,
        dest=sink,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The remote sha1 is still gathered over SSH
        # (read-only) so the plan reflects each host's real pre-deployment state;
        # no content is transferred and the sinks stay empty.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. The first get
        # transfers over scp into each host's sink; the second sees the downloaded
        # content and reports no change.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))

        # The point of an IO-like destination: the downloaded bytes are right here
        # in a Python variable, with no local file involved.
        print("\n== Downloaded content per host ==")
        for name, sink in SINKS.items():
            print(f"{name}: {sink.getvalue().decode().strip()!r}")
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
