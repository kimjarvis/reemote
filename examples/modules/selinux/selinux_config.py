# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: manage /etc/selinux/config with the files.edit spool-and-install recipe.

This is a *usage* example, not part of the library. It is reemote5's answer
to the proposed ``selinux.config`` operation: no bespoke operation exists,
because setting ``SELINUX``/``SELINUXTYPE`` in ``/etc/selinux/config`` is a
file edit fully expressible with the generic ``files.edit``
content-transform primitive plus the elevated ``files.move`` -- the same
spool-and-install recipe as
``examples/modules/openrc/openrc_config.py`` and
``examples/modules/files/files_edit_etc_hosts.py``.

The settings ensured here are ``SELINUX=enforcing`` and
``SELINUXTYPE=targeted``: the boot-time mode and policy type. They are read
when the system boots, so the edit takes effect at the *next reboot* --
the runtime half is ``selinux.mode``'s job (``setenforce``, see
``examples/modules/selinux/selinux_op_mode.py``), and the ``Mode`` fact's
``config_mode`` field is how a deploy reads the persistent setting back.
**Warning**: switching a *disabled* subsystem to enforcing additionally
requires a full relabel -- request ``selinux.autorelabel``
(``examples/modules/selinux/selinux_autorelabel.py``) before the reboot,
or the boot will relabel nothing and services will fail on unlabelled
files. Changing ``SELINUXTYPE`` requires the matching policy package to be
installed.

The flow, over the *real SSH transport* (asyncssh):

1. Load the target hosts from the shared ``inventory.json`` in the project
   root (parse + ``validate_inventory``). The hosts must run SELinux
   (RHEL-family); point ``INVENTORY_PATH`` at your own file of such
   machines to target them.
2. Snapshot: ``gather_fact`` reads ``FileContents`` of
   ``/etc/selinux/config`` from every guarded host (read-only SSH; an
   absent file reads ``None``). The pure ``config_edit`` function computes
   the desired content -- for each key, drop every existing assignment
   (commented ones included) and append ``KEY=value`` -- and the
   controller-side gate compares it against the file's current text: a
   host whose content already matches is skipped entirely, so re-running
   the whole script is a no-op. ``FileContents`` plus local parsing is
   reemote5's content-read convention; there is no grep-style fact.
3. Run 1 spools: ``files.edit`` writes the desired content to
   ``/tmp/config`` (login-user-writable). Each pending host's transform is
   built ONCE by the ``make_transform`` factory closing over the frozen
   desired content -- a stable callable across both phases, the
   determinism-safe split shown in
   ``examples/demonstrations/composition/two_deploy_split.py``
   (``OperationRequest`` compares the transform by value; a fresh closure
   per phase raises ``DeterminismError``). The transfer is unprivileged:
   reemote5's transfers never elevate, which is *why* the root-owned
   ``/etc/selinux/config`` cannot be written directly.
4. Run 2 installs: ``files.move`` moves the spool file into ``/etc/selinux``
   with ``overwrite=True``, flagged ``sudo=True`` -- ``files.move`` appends
   the source basename to a directory destination, so the spool file must
   be named ``config`` to land ``/etc/selinux/config``. The edit and the
   move cannot share one deploy: ``files.move`` validates ``src`` in the
   prepare phase too, and prepare never sends, so the spool file must
   really exist first (the two-run split
   ``examples/modules/files/files_move.py`` explains).

The hosts come from the shared ``inventory.json`` in the project root,
read with ``load_inventory`` (parse + ``validate_inventory``) and resolved
from ``__file__`` so the example works from any working directory. The
snapshot gather is guarded by ``is_centos`` (``host.group == "centos"``):
``gather_fact`` takes no ``when`` parameter, so the predicate filters the
host list before the fan-out and hosts outside the ``"centos"`` group are
named on a ``== Skipped (when guard is_centos): ... ==`` line and never
connected. Every deploy request also carries ``when=is_centos``, and only
hosts with a snapshot entry can become pending.

Run it with::

    uv run python examples/modules/selinux/selinux_config.py
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

# The settings to ensure: the boot-time mode and policy type, read when the
# system boots (so the edit takes effect at the next reboot). Values are
# unquoted -- the stock /etc/selinux/config convention.
CONFIG_SETTINGS = (("SELINUX", "enforcing"), ("SELINUXTYPE", "targeted"))

# SELinux's persistent configuration file, and the spool path: the basename
# ("config") is what files.move appends to /etc/selinux, landing the target.
CONFIG_FILE = "/etc/selinux/config"
SPOOL_PATH = "/tmp/config"

# Per-host spool transforms, filled ONCE in main between the snapshot and
# run 1: each is a stable callable closing over frozen desired content, so
# both phases of run 1 pass the identical object (OperationRequest compares
# the transform by value; a fresh closure per phase would raise
# DeterminismError).
SPOOL_TRANSFORMS: dict[str, Callable[[str | None], str]] = {}

# examples/modules/selinux/selinux_config.py -> the project root, so the
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


def config_edit(content: str | None, key: str, value: str) -> str:
    """Pure config edit: ensure exactly one ``key=value`` assignment.

    Drops every line assigning ``key`` -- commented (``#SELINUX=...``) or
    not, with optional surrounding whitespace -- then appends ``key=value``
    (unquoted, the stock /etc/selinux/config convention). An absent file
    (``None`` content) reads as empty and gains just the one line. The
    transform is last-write-wins by *replacement*: leaving contradictory
    commented-out lines around invites confusion, so they are cleaned out.
    The result always ends in one trailing newline and the edit is a
    fixpoint: the appended line matches the drop pattern, so a second pass
    reproduces the same content.
    """
    pattern = re.compile(rf"^[ \t]*#?[ \t]*{re.escape(key)}[ \t]*=")
    lines = [line for line in (content or "").splitlines() if not pattern.match(line)]
    lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def make_transform(content: str) -> Callable[[str | None], str]:
    """Build a spool transform returning the frozen desired ``content``.

    Called once per host between the snapshot and run 1; the returned
    callable ignores the spool file's current text (the desired content was
    already computed from the real /etc/selinux/config) and is trivially a
    fixpoint.
    """

    def transform(current: str | None) -> str:
        return content

    return transform


def spool(host: Host) -> Generator[OperationRequest]:
    """Run 1: write the desired config content to the writable spool."""
    yield op(
        files.edit,
        name="Spool selinux config",
        path=SPOOL_PATH,
        transform=SPOOL_TRANSFORMS[host.name],
        when=is_centos,
    )


def install(host: Host) -> Generator[OperationRequest]:
    """Run 2: install the spool file over /etc/selinux/config, elevated."""
    yield op(
        files.move,
        name="Install selinux config",
        src=SPOOL_PATH,
        dest="/etc/selinux",
        overwrite=True,
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # The snapshot: read every guarded host's real /etc/selinux/config
        # (read-only cat; an absent file reads None). gather_fact has no
        # ``when`` parameter, so the is_centos predicate filters the host
        # list before the fan-out -- a skipped host is never connected.
        all_hosts = [Host.from_inventory(entry) for entry in inventory]
        hosts = [host for host in all_hosts if is_centos(host)]
        skipped = [host.name for host in all_hosts if host not in hosts]
        if skipped:
            print("\n== Skipped (when guard is_centos): " + ", ".join(skipped) + " ==")
        try:
            contents = await gather_fact(hosts, FileContents, CONFIG_FILE)
        finally:
            for host in hosts:
                await host.aclose()

        # Controller-side idempotence: compute the desired content from the
        # frozen snapshot (both keys, in order) and keep only the hosts whose
        # file differs. A matching host is skipped entirely -- re-running the
        # script after a successful install is a no-op.
        pending = []
        for entry in inventory:
            name = entry["host"]
            # Only hosts with a snapshot entry (the guarded ones) can
            # become pending; a skipped host has no contents key.
            if name not in contents:
                continue
            desired = contents[name]
            for key, value in CONFIG_SETTINGS:
                desired = config_edit(desired, key, value)
            if desired != (contents[name] or ""):
                SPOOL_TRANSFORMS[name] = make_transform(desired)
                pending.append(entry)
            else:
                print(f"{name}: {CONFIG_FILE} already sets the desired values")
        if not pending:
            print("All hosts already set SELINUX=enforcing, SELINUXTYPE=targeted.")
            return

        # Run 1: spool the transformed content to /tmp/config,
        # unprivileged -- reemote5's transfers never elevate.
        plans, _, _hosts = await run(pending, spool, dry=True)
        print("\n== Run 1 plan: spool selinux config (--dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(pending, spool)
        print("\n== Run 1 log: spool selinux config ==")
        assert report is not None
        print(format_log(report))

        # Run 2: the spool file now really exists, so the move's validation
        # passes in the prepare phase too. Elevated: /etc/selinux is
        # root-owned.
        plans, _, _hosts = await run(pending, install, dry=True)
        print("\n== Run 2 plan: install selinux config (--dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(pending, install)
        print("\n== Run 2 log: install selinux config ==")
        assert report is not None
        print(format_log(report))
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema.
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