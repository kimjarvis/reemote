# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: edit a root-owned file with a pure transform via files.transform.

This is a *usage* example, not part of the library. ``files.transform`` is the
root-owned sibling of ``files.edit``: it applies the same caller-supplied
*pure transform* to a file's current text, but it writes the result back
through the spool-and-install path instead of ``files.edit``'s unprivileged
scp. That is what lets it converge a protected file -- a config under ``/etc``
such as ``/etc/httpd/conf/httpd.conf`` -- in a single deploy.

Why not ``files.edit``? The put/edit transfers ride **unprivileged** scp as
the login user, so ``sudo``/``su`` do not apply to them and the target must be
writable by that user; a root-owned file is out of reach. The old recipe --
``files.edit`` into a writable path, then ``files.move(sudo=True)`` into place
-- spans TWO runs, because ``files.move`` raises on the not-yet-created spool
in the prepare phase. ``files.transform`` reads the file, transforms it and
installs the result from ONE request:

1. Read + transform: the operation reads the target's current text with the
   ``FileContents`` fact (``None`` when the file is absent) and hands it to
   ``transform``.
2. Compare: the transformed text is compared against the file's content; when
   they already match, nothing is yielded and the request reports no change.
3. Spool + install: when they differ, the write is delegated to
   ``files.install`` -- an ``InstallSpoolCommand`` transfer copies the text
   over scp to a dest-derived ``/tmp`` spool (unprivileged regardless of the
   request's ``sudo``), then an ``InstallCommand`` shell runs
   ``mkdir -p``/``cp``/optional ``chown``/``chmod``/``rm -rf`` wrapped in
   ``sh -c`` so ``sudo=True`` elevates every step.

The transform is a plain module-level function taking the current content
(``str``, or ``None`` when the file is absent) and returning the desired
content as a ``str``. Two rules matter, exactly as for ``files.edit``:

* It must be **idempotent** -- ``transform(transform(x)) == transform(x)`` --
  so a second run is a no-op. Here ``ensure_listen_directive`` sets the
  ``Listen`` directive's port only when it is not already the wanted value,
  preserving every other line, comment and blank line in the file.
* It must be a **stable, hashable object** -- a module-level function, never a
  lambda or closure built inside the deploy generator. ``files.transform``
  stores it in the ``OperationRequest`` kwargs, which the determinism guard
  compares by value across the prepare and apply phases; a fresh callable per
  phase would diverge and raise ``DeterminismError``.

``docs/migrating.md`` describes the transform contract and how to write one in
detail. The optional ``mode``/``user``/``group`` parameters are forwarded to
the install, so the same request that lands the edit can also set the file's
permissions and owner; idempotence stays content-only, so a mode- or owner-only
drift does not re-install (``files.file`` converges metadata).

The script transforms a demo config under ``/etc`` twice with the very same
deploy: the first run writes, the second reads the content back and reports no
change on every host. Unlike the pytest suite -- which injects the test
framework's ``SimulatedHost`` via ``run``'s ``host_factory`` and never touches
the network -- this example uses the *default* factory (``Host.from_inventory``):
each plain ``Host`` lazily opens an asyncssh connection, ``cat``s the file for
real, and spools + installs the transformed content when it differs.

Every request carries ``when=is_centos``, the declarative eligibility
guard: the predicate reads only **static** inventory data --
``host.group``, set by the ``"group"`` key of the inventory entry -- and
is a stable module-level function, so the prepare and apply phases reach
the same skip decision. The driver skips a rejected request without
materializing it, so on hosts outside the ``"centos"`` group neither the
commands nor the facts behind them run, and plan and log render the step
as ``[skipped: when guard is_centos]`` in its lockstep slot.

The path is resolved from ``__file__``, so the example works from any
working directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/files/files_transform.py
"""

import asyncio
import json
import re
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

# The root-owned file to edit: a demo config under /etc, which the login user
# cannot write directly -- only the elevated install half can. Point this at a
# real protected config (eg /etc/httpd/conf/httpd.conf) on your own hosts.
REMOTE_PATH = "/etc/reemote-transform-demo.conf"

# The port the transform converges the Listen directive to.
LISTEN_PORT = "8080"


def ensure_listen_directive(current: str | None) -> str:
    """Pure, idempotent transform: return content with ``Listen <port>`` set.

    Takes the file's current text (or ``None`` when it is absent) and returns
    the desired text. Any existing ``Listen`` line is rewritten to the wanted
    port; when the file already listens on it -- or has no ``Listen`` line and
    one is appended -- a second pass returns the same text, so the transform is
    a fixpoint and ``files.transform`` reports no change. Every other line,
    comment and blank line is preserved verbatim.
    """
    wanted = f"Listen {LISTEN_PORT}"
    text = current or ""
    lines = text.splitlines()
    replaced = False
    for index, line in enumerate(lines):
        if re.match(r"^\s*Listen\b", line):
            lines[index] = wanted
            replaced = True
    if not replaced:
        lines.append(wanted)
    return "\n".join(lines) + "\n"


# examples/modules/files/files_transform.py -> the project root, so the
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


def deploy(host: Host) -> Generator[OperationRequest]:
    """Transform the same root-owned file twice: the second proves idempotence.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases (a branch on a mutable fact would raise DeterminismError). The
    transform is a module-level function -- the same object in both phases --
    which is what keeps the two ``OperationRequest`` values equal. Each request
    is flagged ``sudo=True``: the scp spool transfer always rides as the login
    user, and only the install half elevates to overlay the ``/etc`` file.
    """
    yield op(
        files.transform,
        name="Ensure Listen directive",
        path=REMOTE_PATH,
        transform=ensure_listen_directive,
        sudo=True,
        when=is_centos,
    )
    yield op(
        files.transform,
        name="Re-ensure Listen directive",
        path=REMOTE_PATH,
        transform=ensure_listen_directive,
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The file is still ``cat``'d over SSH
        # (read-only) so the plan reflects each host's real pre-deployment
        # state; no content is spooled or installed.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # First real run: spool + install on every host whose content differs.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Log: transform config (first run) ==")
        assert report is not None
        print(format_log(report))

        # Second run of the SAME deploy: FileContents now reads back what the
        # first run installed, so the transform is a fixpoint and every request
        # reports no change.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Log: transform config (second run, no-op) ==")
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
