# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: manage /etc/rc.conf with the files.edit spool-and-install recipe.

This is a *usage* example, not part of the library. It is reemote5's answer
to the proposed ``openrc.config`` operation: no bespoke operation exists,
because setting a variable in ``/etc/rc.conf`` (or a service's
``/etc/conf.d/<service>``) is a file edit fully expressible with the
generic ``files.edit`` content-transform primitive plus the elevated
``files.move`` -- the same spool-and-install recipe as
``examples/modules/server/server_op_sysctl.py`` and
``examples/modules/files/files_edit_etc_hosts.py``.

The variable ensured here is ``rc_parallel="YES"``, OpenRC's parallel
startup switch: it is read when a runlevel is entered, so the change takes
effect at the next boot (or ``openrc default`` re-entry) -- unlike a
sysctl there is no runtime half to apply, the file edit is the whole job.
Point ``RC_FILE`` at ``/etc/conf.d/<service>`` and ``RC_KEY``/``RC_VALUE``
at a service option to manage per-service configuration the same way.

The flow, over the *real SSH transport* (asyncssh):

1. Read ``inventory.json`` in the project root and validate it up front
   with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is
   materialized or connected. Materialize the entries into plain ``Host``
   objects and keep only the ``"alpine"`` group -- the ``is_alpine``
   predicate reads static inventory data (``host.group``), the same rule
   every ``when`` guard follows. Here the filter runs *before* the
   snapshot: ``/etc/rc.conf`` is OpenRC's file, so it must not even be
   read on the systemd hosts the shared inventory also holds. This
   controller-side filter is the recipe's equivalent of the deploy-side
   ``when=is_alpine`` guard the ``openrc_service.py`` example uses.
2. Snapshot: ``gather_fact`` reads ``FileContents`` of ``/etc/rc.conf``
   from every guarded host (read-only SSH; an absent file reads ``None``).
   The pure ``rc_conf_edit`` function computes the desired content --
   drop every existing ``rc_parallel`` assignment (commented ones
   included) and append ``rc_parallel="YES"`` -- and the controller-side
   gate compares it against the file's current text: a host whose content
   already matches is skipped entirely, so re-running the whole script is
   a no-op. ``FileContents`` plus local parsing is reemote5's content-read
   convention; there is no grep-style fact.
3. Run 1 spools: ``files.edit`` writes the desired content to
   ``/tmp/rc.conf`` (login-user-writable). Each pending host's transform
   is built ONCE by the ``make_transform`` factory closing over the frozen
   desired content -- a stable callable across both phases, the
   determinism-safe split shown in
   ``examples/demonstrations/composition/two_deploy_split.py``
   (``OperationRequest`` compares the transform by value; a fresh
   closure per phase raises ``DeterminismError``). The transfer is
   unprivileged: reemote5's transfers never elevate, which is *why* the
   root-owned ``/etc/rc.conf`` cannot be written directly.
4. Run 2 installs: ``files.move`` moves the spool file into ``/etc`` with
   ``overwrite=True``, flagged ``doas=True`` -- ``files.move`` appends the
   source basename to a directory destination, landing ``/etc/rc.conf``.
   The edit and the move cannot share one deploy: ``files.move`` validates
   ``src`` in the prepare phase too, and prepare never sends, so the spool
   file must really exist first (the two-run split
   ``examples/modules/files/files_move.py`` explains).

``doas`` rather than ``sudo`` because the Alpine host has no ``sudo``
installed at all -- ``sudo mv`` would die with ``sh: sudo: not found``
(exit 127). ``doas`` is passwordless here: reemote5 always runs ``doas -n``,
which fails rather than prompts, so the host must grant the login user a
``permit nopass`` rule in ``/etc/doas.conf`` (see
``examples/demonstrations/privilege/privilege_doas.py``); without one the
driver raises ``PrivilegeError``.

The path is resolved from ``__file__``, so the example works from any
working directory. Point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/openrc/openrc_config.py
"""

import asyncio
import json
import re
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from reemote5 import (
    CommandError,
    FileContents,
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

# examples/modules/openrc/openrc_config.py -> the project root, so the
# inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The setting to ensure: OpenRC's parallel startup switch, read when a
# runlevel is entered (so the edit takes effect at the next boot).
RC_KEY = "rc_parallel"
RC_VALUE = "YES"

# OpenRC's global configuration file, and the spool path: the basename
# ("rc.conf") is what files.move appends to /etc, landing the target.
RC_FILE = "/etc/rc.conf"
SPOOL_PATH = "/tmp/rc.conf"

# Per-host spool transforms, filled ONCE in main between the snapshot and
# run 1: each is a stable callable closing over frozen desired content, so
# both phases of run 1 pass the identical object (OperationRequest compares
# the transform by value; a fresh closure per phase would raise
# DeterminismError).
SPOOL_TRANSFORMS: dict[str, Callable[[str | None], str]] = {}


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


def is_alpine(host: Host) -> bool:
    """Guard: the host belongs to the inventory group ``"alpine"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact. The recipe filters
    with it *before* the snapshot -- /etc/rc.conf is OpenRC's file and must
    not be read, let alone rewritten, on the systemd hosts the shared
    inventory also holds.
    """
    return host.group == "alpine"


def rc_conf_edit(content: str | None, key: str, value: str) -> str:
    """Pure rc.conf edit: ensure exactly one ``key="value"`` assignment.

    Drops every line assigning ``key`` -- commented (``#rc_parallel=...``)
    or not, with optional surrounding whitespace -- then appends
    ``key="value"`` (rc.conf is sourced by the shell, so the value is
    quoted). An absent file (``None`` content) reads as empty and gains
    just the one line. Unlike the sysctl recipe's unanchored search (which
    keeps pyinfra ``files.line`` semantics, stale values surviving), this
    transform is last-write-wins by *replacement*: OpenRC also sources
    rc.conf top to bottom, but leaving contradictory commented-out lines
    around invites confusion, so they are cleaned out. The result always
    ends in one trailing newline and the edit is a fixpoint: the appended
    line matches the drop pattern, so a second pass reproduces the same
    content.
    """
    pattern = re.compile(rf"^[ \t]*#?[ \t]*{re.escape(key)}[ \t]*=")
    lines = [line for line in (content or "").splitlines() if not pattern.match(line)]
    lines.append(f'{key}="{value}"')
    return "\n".join(lines) + "\n"


def make_transform(content: str) -> Callable[[str | None], str]:
    """Build a spool transform returning the frozen desired ``content``.

    Called once per host between the snapshot and run 1; the returned
    callable ignores the spool file's current text (the desired content was
    already computed from the real /etc/rc.conf) and is trivially a
    fixpoint.
    """

    def transform(current: str | None) -> str:
        return content

    return transform


def spool(host: Host) -> Generator[OperationRequest]:
    """Run 1: write the desired rc.conf content to the writable spool."""
    yield op(
        files.edit,
        name="Spool rc.conf",
        path=SPOOL_PATH,
        transform=SPOOL_TRANSFORMS[host.name],
    )


def install(host: Host) -> Generator[OperationRequest]:
    """Run 2: install the spool file over /etc/rc.conf, elevated."""
    yield op(
        files.move,
        name="Install rc.conf",
        src=SPOOL_PATH,
        dest="/etc",
        overwrite=True,
        doas=True,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Guard before anything is read: only "alpine"-group entries reach
        # the snapshot, so /etc/rc.conf is never even catted on a systemd
        # host. The skipped names are printed the way the fact examples
        # print their when-guard skips.
        alpine_entries = []
        skipped = []
        for entry in inventory:
            host = Host.from_inventory(entry)
            if is_alpine(host):
                alpine_entries.append(entry)
            else:
                skipped.append(host.name)
        if skipped:
            print("\n== Skipped (when guard is_alpine): " + ", ".join(skipped) + " ==")
        if not alpine_entries:
            print("No alpine-group hosts in the inventory; nothing to do.")
            return

        # The snapshot: read every guarded host's real /etc/rc.conf
        # (read-only cat; an absent file reads None).
        hosts = [Host.from_inventory(entry) for entry in alpine_entries]
        try:
            contents = await gather_fact(hosts, FileContents, RC_FILE)
        finally:
            for host in hosts:
                await host.aclose()

        # Controller-side idempotence: compute the desired content from the
        # frozen snapshot and keep only the hosts whose file differs. A
        # matching host is skipped entirely -- re-running the script after
        # a successful install is a no-op.
        pending = []
        for entry in alpine_entries:
            name = entry["host"]
            desired = rc_conf_edit(contents[name], RC_KEY, RC_VALUE)
            if desired != (contents[name] or ""):
                SPOOL_TRANSFORMS[name] = make_transform(desired)
                pending.append(entry)
            else:
                print(f'{name}: {RC_FILE} already sets {RC_KEY}="{RC_VALUE}"')
        if not pending:
            print(f'All hosts already set {RC_KEY}="{RC_VALUE}".')
            return

        # Run 1: spool the transformed content to /tmp/rc.conf,
        # unprivileged -- reemote5's transfers never elevate.
        plans, _, _hosts = await run(pending, spool, dry=True)
        print("\n== Run 1 plan: spool rc.conf (--dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(pending, spool)
        print("\n== Run 1 log: spool rc.conf ==")
        assert report is not None
        print(format_log(report))

        # Run 2: the spool file now really exists, so the move's validation
        # passes in the prepare phase too. Elevated: /etc is root-owned.
        plans, _, _hosts = await run(pending, install, dry=True)
        print("\n== Run 2 plan: install rc.conf (--dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(pending, install)
        print("\n== Run 2 log: install rc.conf ==")
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