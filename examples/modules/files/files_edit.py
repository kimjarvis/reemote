# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: edit a remote file in place with a pure transform via files.edit.

This is a *usage* example, not part of the library. It shows reemote5's
generic content-editing primitive, ``files.edit``: instead of a bespoke
operation for every config-file format (pyinfra ships ``server.etc_hosts``,
``files.line``, ``files.replace`` and more, each hard-coding one shape of
edit), ``files.edit`` reads a file's current text with the ``FileContents``
fact, applies a **caller-supplied pure transform**, and writes the result back
over the asyncssh connection with ``files.put``'s scp transfer -- but only when
the transformed content actually differs. Any "edit a file in place" task is
then just a transform -- a few common formats have dedicated operations built
on the same idea (``server.etc_hosts`` for the hosts file, ``sysctl.setting``
for sysctl keys), while the generic primitive covers the rest.

The transform is a plain module-level function taking the current content
(``str``, or ``None`` when the file is absent) and returning the desired
content as a ``str``. Two rules matter:

* It must be **idempotent** -- ``transform(transform(x)) == transform(x)`` --
  so a second run is a no-op. Here ``ensure_marker`` appends a managed marker
  line only when it is not already present.
* It must be a **stable, hashable object** -- a module-level function, never a
  lambda or closure built inside the deploy generator. ``files.edit`` stores it
  in the ``OperationRequest`` kwargs, which the determinism guard compares by
  value across the prepare and apply phases; a fresh callable per phase would
  diverge and raise ``DeterminismError`` (exactly as a fresh IO stream would
  for ``files.put``/``files.get``).

Idempotence is content-equality based: the transform's result is compared
against the current content, and the write happens only when they differ. The
deploy edits the same file twice to prove it -- the second edit is a guaranteed
no-op because the *first* edit's upload is visible to it through the fresh
per-step fact cache, the "act, then read" property the lockstep apply phase
provides.

The write rides the connection as the login user (a transfer, so ``sudo``/``su``
do not apply to it), so the target must be **writable by that user** -- here a
path in ``/tmp``, which any login user can write, so the example depends on no
account's home directory existing. A root-owned file such as ``/etc/hosts``
cannot be written this way; transform it into a writable path first and move it
into place with an elevated shell operation.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``): each plain ``Host`` lazily
opens an asyncssh connection, ``cat``s the file for real, and uploads the
transformed content over scp when it differs.

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

    uv run python examples/modules/files/files_edit.py
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
    files,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# The remote file to edit: a /tmp path, writable by whatever account the
# inventory logs in as, so the unprivileged scp transfer can create it.
REMOTE_PATH = "/tmp/reemote5-edit.txt"

# The managed line the transform ensures is present exactly once.
MARKER_LINE = "# managed by the reemote5 files.edit example"


def ensure_marker(current: str | None) -> str:
    """Pure, idempotent transform: return content with ``MARKER_LINE`` present.

    Takes the file's current text (or ``None`` when it is absent) and returns
    the desired text. If the marker is already a line, the content is returned
    unchanged -- so a second edit is a fixpoint and ``files.edit`` reports no
    change. Otherwise the marker is appended.
    """
    text = current or ""
    lines = text.splitlines()
    if MARKER_LINE in lines:
        return text
    lines.append(MARKER_LINE)
    return "\n".join(lines) + "\n"


# examples/modules/files/files_edit.py -> the project root, so the inventory
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
    """Edit the same file twice: the second edit proves content idempotence.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases (a branch on a mutable fact would raise DeterminismError). The
    transform is a module-level function -- the same object in both phases --
    which is what keeps the two ``OperationRequest`` values equal. Neither
    request is elevated: the write goes over the connection's scp channel as
    the login user.
    """
    yield op(
        files.edit,
        name="Ensure marker",
        path=REMOTE_PATH,
        transform=ensure_marker,
        when=is_centos,
    )
    yield op(
        files.edit, name="Re-ensure marker", path=REMOTE_PATH, transform=ensure_marker,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The file is still ``cat``'d over SSH
        # (read-only) so the plan reflects each host's real pre-deployment
        # state; no content is transferred.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. The first edit
        # uploads the transformed content over scp; the second sees it through
        # the fresh per-step fact cache and reports no change.
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
