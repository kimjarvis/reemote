# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: upload an in-memory stream to two real hosts with files.put.

This is a *usage* example, not part of the library. It shows reemote5's
first **non-shell** operation: ``files.put`` does not run a command string,
it copies content from the control machine over each host's asyncssh
connection using the scp protocol.

The source is an **IO-like object only** -- the baseline's ``files.put`` accepts
``str | IO[Any]``, but reemote5 deliberately supports just the stream form:
here a module-level ``io.BytesIO``. Two rules follow from that. The stream
must be *seekable*: it is rewound to position 0 before every read, so the
prepare-phase hash, the apply-phase hash and the copy itself all see the
same content. And because ``OperationRequest`` equality compares a stream
*by identity* -- an ``IO`` object hashes by identity too, so it satisfies the
request hashability rule -- the deploy must pass the same object in
both phases -- hence the module-level constant rather than a fresh
``BytesIO`` inside the generator (a new object per phase would trip the
determinism guard).

The idempotence check is content-based, mirroring the baseline's ``files.put``:
the source's sha1 (``LocalSha1``, read here on the control machine) is
compared against the remote file's (``RemoteFileSha1``, gathered with
``sha1sum`` over SSH), and the transfer only happens when the two differ.
The deploy puts the same stream twice to prove it: the second put is a
guaranteed no-op because the *first* put's upload is visible to it through
the fresh per-step fact cache -- exactly the "act, then read" property the
lockstep apply phase provides.

The transfer rides the connection as the login user, so the destination
must be writable by that user (``sudo``/``su`` do not apply to transfers);
here it is a path in ``/tmp``, which any login user can write, so the example
depends on no account's home directory existing.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``): each plain ``Host``
lazily opens an asyncssh connection, gathers the remote hash for real, and
receives the stream's content over scp when it differs.

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

    uv run python examples/modules/files/files_put.py
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
    files,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# The IO-like source, created once on the control machine: the same object
# is passed to every put in every phase, so the prepare and apply request
# sequences compare equal (OperationRequest compares it by identity).
LOCAL_SRC = io.BytesIO(b"hello from the reemote5 files.put example\n")

# The remote destination: a /tmp path, writable by whatever account the
# inventory logs in as, so the unprivileged scp transfer can create it.
REMOTE_DEST = "/tmp/reemote5-put.txt"

# examples/modules/files/files_put.py -> the project root, so the inventory
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
    """Put the same stream twice: the second put proves sha1 idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    Neither request is elevated: the copy goes over the connection's scp
    channel as the login user.
    """
    yield op(
        files.put,
        name="Upload message",
        src=LOCAL_SRC,
        dest=REMOTE_DEST,
        when=is_centos,
    )
    yield op(
        files.put,
        name="Re-upload message",
        src=LOCAL_SRC,
        dest=REMOTE_DEST,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The remote sha1 is still gathered over SSH
        # (read-only) so the plan reflects each host's real pre-deployment state;
        # no content is transferred.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. The first put
        # transfers over scp; the second sees the uploaded hash and reports no
        # change.
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
