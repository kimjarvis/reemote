# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: extract a source tarball with files.unarchive (issue #21).

This is a *usage* example, not part of the library. ``files.unarchive`` is the
pyinfra ``files.unarchive`` migration that closes issue #21 -- Ansible's
``unarchive`` parity for the build-from-source path: it extracts an archive
into a remote directory, idempotently gated by ``creates``.

The geerlingguy.php-xdebug role is the canonical case. Its first task on the
default (and only) install path is::

    - name: Untar Xdebug.
      unarchive:
        src: "https://xdebug.org/files/xdebug-{{ php_xdebug_version }}.tgz"
        dest: "{{ workspace }}"
        creates: "{{ workspace }}/xdebug-{{ php_xdebug_version }}"
        copy: false

``copy: false`` means the source is a URL, not a controller-side file pushed
first; ``creates:`` skips the whole task once the extracted directory exists.
Before issue #21 that task needed a three-piece workaround -- a control-side
``urllib`` fetch, a ``files.put`` of the tarball into a ``/tmp`` spool, and a
``tar`` ``Shell`` action *outside* the deploys whose ``creates:`` idempotence
was reconstructed control-side from a ``FileExists`` snapshot. Here the whole
task collapses to ONE in-deploy request, restoring Ansible's one-task shape.

How it works:

* **URL or remote path.** ``src`` is auto-detected: an ``http(s)`` URL is
  fetched *control-side* as binary bytes (the ``_fetch_url_bytes`` seam
  ``files.fetch_url`` rides, without its text-only decode -- a ``.tgz`` body
  never raises), spooled to a URL-derived ``/tmp`` name over unprivileged scp,
  extracted, and the spool removed. Any other ``src`` is a remote path already
  on the target, extracted in place and left alone (pyinfra's
  ``remote_src=True``). The control-side fetch works when only the control
  machine has internet egress; the host needs ``tar`` (or ``unzip`` for a
  ``.zip``), never ``curl``/``wget``.
* **The creates gate.** ``creates`` is Ansible's idempotence, resolved
  in-operation through the ``FileExists`` fact (``test -e``, so it sees the
  extracted *directory* Ansible names). A converged host reports
  ``changed=False`` with zero commands and skips the fetch entirely. Without
  ``creates`` the operation is stateless like pyinfra's: extraction always
  yields.
* **dest created when absent.** The workspace directory is created with its
  parents when missing (the issue's requirement -- pyinfra raises instead).
* **Two privilege levels.** The extraction, mkdir, spool cleanup and any
  ownership step are shell text, so ``sudo=True`` elevates them for a
  root-owned workspace; the scp spool transfer stays unprivileged regardless.

The body runs once per phase (prepare, then apply), so the URL is fetched
twice per host. Pass ``checksum="sha256:<hexdigest>"`` (the
``files.download`` contract) to pin the bytes -- both phases then verify the
same digest and stage byte-identical content. This example omits it so it
stays runnable against the live xdebug.org URL; add it in production from the
vendor's published value.

The script extracts the tarball, then runs the very same deploy a second
time: the first run extracts, the second reports no change on every host (the
``creates`` directory now exists, so the gate short-circuits before any
fetch).

Every request carries ``when=is_ubuntu``, the declarative eligibility guard:
the predicate reads only **static** inventory data -- ``host.family``, set by the ``"family"`` key of the inventory entry -- and is a stable module-level
function, so the prepare and apply phases reach the same skip decision. On
hosts outside the ``"Ubuntu"`` family neither the fetch nor the extraction
runs, and plan and log render the step as ``[skipped: when guard is_ubuntu]``
in its lockstep slot.

The path is resolved from ``__file__``, so the example works from any working
directory; point ``INVENTORY_PATH`` at your own file (or edit ``inventory.json``)
to target your machines.

Run it with::

    uv run python examples/modules/files/files_unarchive.py
"""

import asyncio
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

# The php-xdebug source tarball: the role's build-from-source first step. The
# URL, workspace and creates path are plain hashable strs, equal across phases.
XDEBUG_VERSION = "3.3.2"
TARBALL_URL = f"https://xdebug.org/files/xdebug-{XDEBUG_VERSION}.tgz"
WORKSPACE = "/usr/local/src"
SRC_DIR = f"{WORKSPACE}/xdebug-{XDEBUG_VERSION}"

# examples/modules/files/files_unarchive.py -> the project root, so the
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


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision.
    """
    return host.family == "Ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Untar Xdebug into the workspace, gated by the extracted source directory.

    One request replaces the role-migration workaround's fetch helper, spool
    deploy and out-of-deploy ``tar`` action. ``sudo=True`` elevates the
    extraction (and the workspace mkdir / spool cleanup) for the root-owned
    ``/usr/local/src``; the scp spool transfer always rides as the login user,
    and the fetch happens control-side before either. ``creates=SRC_DIR`` is
    Ansible's ``creates:`` -- once the extracted directory exists the request
    is a no-op and the tarball is never fetched. Add
    ``checksum="sha256:<hex>"`` to pin the tarball in production.
    """
    yield op(
        files.unarchive,
        name="Untar Xdebug",
        src=TARBALL_URL,
        dest=WORKSPACE,
        creates=SRC_DIR,
        sudo=True,
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Dry run: the plan materializes against pre-deployment state. The
        # fetch still happens (prepare runs the body) but nothing is sent.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan: untar Xdebug (--dry) ==")
        print(format_plan(plans))

        # First real run: fetch control-side, spool + extract on every host
        # whose creates path is absent.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Log: untar Xdebug (first run) ==")
        assert report is not None
        print(format_log(report))

        # Second run of the SAME deploy: the creates directory now exists, so
        # the FileExists gate short-circuits -- no fetch, no commands, every
        # request reports no change.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Log: untar Xdebug (second run, no-op) ==")
        assert report is not None
        print(format_log(report))
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing/unreadable/not valid JSON, or the
        # control-side fetch failed (an unreachable URL or a non-2xx response
        # raises a URLError/HTTPError, both OSError subclasses). Python's own
        # message already names the file or URL and the reason, so print just
        # that -- no traceback worth showing.
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