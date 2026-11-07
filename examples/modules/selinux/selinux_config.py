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

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information. The hosts must
   run SELinux (RHEL-family); point ``INVENTORY`` at your own machines to
   target them.
2. Snapshot: ``gather_fact`` reads ``FileContents`` of
   ``/etc/selinux/config`` from every host (read-only SSH; an absent file
   reads ``None``). The pure ``config_edit`` function computes the desired
   content -- for each key, drop every existing assignment (commented ones
   included) and append ``KEY=value`` -- and the controller-side gate
   compares it against the file's current text: a host whose content
   already matches is skipped entirely, so re-running the whole script is
   a no-op. ``FileContents`` plus local parsing is reemote5's content-read
   convention; there is no grep-style fact.
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

Run it with::

    uv run python examples/modules/selinux/selinux_config.py
"""

import asyncio
import re
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

# The inventory: real hosts and their SSH connection information, supplied
# by the application to the library. Host.from_inventory materializes each
# entry into a plain Host that connects over asyncssh.
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
    )


async def main() -> None:
    try:
        # The snapshot: read every host's real /etc/selinux/config
        # (read-only cat; an absent file reads None).
        hosts = [Host.from_inventory(entry) for entry in INVENTORY]
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
        for entry in INVENTORY:
            name = entry["host"]
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
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())