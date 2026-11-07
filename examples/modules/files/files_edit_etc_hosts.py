# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: manage an /etc/hosts entry with files.edit + files.move.

This is a *usage* example, not part of the library. It is reemote5's answer
to pyinfra's bespoke ``server.etc_hosts`` operation: no per-format editor is
ported, because the generic ``files.edit`` content-transform primitive plus
the elevated ``files.move`` cover the task.

The obstacle is privilege: ``files.edit`` writes through ``files.put``'s
**unprivileged** scp transfer, so it cannot write root-owned ``/etc/hosts``
directly. The recipe therefore spools and installs:

1. A fact SNAPSHOT: ``gather_fact`` reads ``FileContents("/etc/hosts")``
   from every host (a read-only SSH ``cat``). On the control machine the
   pure ``ensure_db_host`` transform computes each host's desired content --
   the same line-keyed-by-IP edit pyinfra's ``server.etc_hosts`` performs,
   comments on the edited line dropped, every other line preserved. Hosts
   whose content already matches are skipped entirely: the script-level
   idempotence pyinfra gets from its ``EtcHosts`` fact short-circuit lives
   here, in the gap between runs.
2. Run 1 spools: ``files.edit`` writes the desired content to ``/tmp/hosts``
   (login-user-writable). Each host's transform is built ONCE by the
   ``make_transform`` factory closing over the frozen snapshot -- a stable
   callable across both phases, exactly the determinism-safe split shown in
   ``examples/demonstrations/composition/two_deploy_split.py``.
3. Run 2 installs: ``files.move`` moves ``/tmp/hosts`` into ``/etc`` with
   ``overwrite=True``, flagged ``sudo=True`` -- ``rm -rf /etc/hosts`` then
   ``mv /tmp/hosts /etc``, both elevated shell commands. The run-as user and
   any password come from the inventory ``privilege`` data, never the op.

Why two runs and not one deploy: ``files.move`` raises ``ValueError`` on an
absent source in *both* phases (reemote5 operations are phase-unaware, so
pyinfra's soft plan-time logging is deliberately dropped). In a single
deploy the prepare phase would raise on the spool file the edit has not
created yet -- prepare never sends. Splitting the runs puts a real barrier
between "spool exists" and "move validates it".

Re-running the whole script is a no-op once every host's hosts file matches:
the snapshot comparison skips both runs. The ``mv`` itself is stateless
(pyinfra parity) -- it always plans a command when it runs at all.

Run it with::

    uv run python examples/modules/files/files_edit_etc_hosts.py
"""

import asyncio
import sys
from collections.abc import Callable, Generator

from reemote5 import (
    CommandError,
    FileContents,
    Host,
    HostConnectionError,
    OperationRequest,
    PrivilegeError,
    files,
    format_log,
    format_plan,
    gather_fact,
    op,
    run,
)

# The entry to ensure, keyed by IP exactly like pyinfra's server.etc_hosts.
DB_IP = "10.0.0.5"
DB_LINE = "10.0.0.5 db.internal db"

# The spool path: login-user-writable, and its basename ("hosts") is what
# files.move appends to the destination directory, landing /etc/hosts.
SPOOL_PATH = "/tmp/hosts"

# Per-host spool transforms, filled ONCE in main between the snapshot and
# run 1: each is a stable callable closing over frozen content, so both
# phases of run 1 pass the identical object (OperationRequest compares the
# transform by value; a fresh closure per phase would raise DeterminismError).
SPOOL_TRANSFORMS: dict[str, Callable[[str | None], str]] = {}

# The inventory: real hosts and their SSH connection information. No
# ``privilege`` entry: the elevated move assumes passwordless sudo for the
# login user; a password would come from ``privilege`` here, never the op.
INVENTORY = [
    {
        "host": "web1",
        "connection": {
            "address": "192.168.0.240",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
    {
        "host": "web2",
        "connection": {
            "address": "192.168.0.242",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
]


def ensure_db_host(current: str | None) -> str:
    """Pure, idempotent hosts-file edit: ensure the line for ``DB_IP``.

    Drops any existing line keyed by the IP (comments on that line are not
    preserved, pyinfra parity), appends the desired line, keeps every other
    line untouched. A fixpoint: applying it to its own output changes nothing.
    """
    text = current or ""
    kept = [
        line
        for line in text.splitlines()
        if line.split("#", 1)[0].split()[:1] != [DB_IP]
    ]
    kept.append(DB_LINE)
    return "\n".join(kept) + "\n"


def make_transform(content: str) -> Callable[[str | None], str]:
    """Build a spool transform returning the frozen desired ``content``.

    Called once per host between the snapshot and run 1; the returned
    callable ignores the spool file's current text (the desired content was
    already computed from the real /etc/hosts) and is trivially a fixpoint.
    """

    def transform(current: str | None) -> str:
        return content

    return transform


def spool(host: Host) -> Generator[OperationRequest]:
    """Run 1: write the desired content to the writable spool path."""
    yield op(
        files.edit,
        name="Spool hosts file",
        path=SPOOL_PATH,
        transform=SPOOL_TRANSFORMS[host.name],
    )


def install(host: Host) -> Generator[OperationRequest]:
    """Run 2: move the spool file over /etc/hosts, elevated."""
    yield op(
        files.move,
        name="Install hosts file",
        src=SPOOL_PATH,
        dest="/etc",
        overwrite=True,
        sudo=True,
    )


async def main() -> None:
    try:
        # The snapshot: read every host's real /etc/hosts (read-only cat).
        hosts = [Host.from_inventory(entry) for entry in INVENTORY]
        try:
            contents = await gather_fact(hosts, FileContents, "/etc/hosts")
        finally:
            for host in hosts:
                await host.aclose()

        # Controller-side idempotence: compute each host's desired content
        # from the frozen snapshot and skip hosts that already match.
        pending = []
        for entry in INVENTORY:
            name = entry["host"]
            current = contents[name]
            desired = ensure_db_host(current)
            if desired != current:
                SPOOL_TRANSFORMS[name] = make_transform(desired)
                pending.append(entry)
        if not pending:
            print("All hosts already carry the desired /etc/hosts entry.")
            return

        # Run 1: spool the transformed content to /tmp/hosts, unprivileged.
        plans, _, _hosts = await run(pending, spool, dry=True)
        print("== Run 1 plan: spool hosts file (--dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(pending, spool)
        print("\n== Run 1 log: spool hosts file ==")
        assert report is not None
        print(format_log(report))

        # Run 2: the spool file now really exists, so the move's validation
        # passes in the prepare phase too. Elevated: /etc is root-owned.
        plans, _, _hosts = await run(pending, install, dry=True)
        print("\n== Run 2 plan: install hosts file (--dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(pending, install)
        print("\n== Run 2 log: install hosts file ==")
        assert report is not None
        print(format_log(report))
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())