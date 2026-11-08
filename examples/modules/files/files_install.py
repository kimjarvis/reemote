# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: install text content at a root-owned path with files.install.

This is a *usage* example, not part of the library. ``files.install`` is the
generic spool-and-install operation: it writes arbitrary text content to a
path the login user cannot write directly (anything under ``/etc``), the
task the namespace-specific operations (``apt.sources_file``, ``dnf.repo``,
``systemd.dropin``) each spell out for their own config paths.

Why not ``files.put``? The put/edit transfers ride **unprivileged** scp as
the login user, so a root-owned destination is out of reach. The old recipe
-- ``files.put``/``files.edit`` into ``/tmp`` then ``files.move(sudo=True)``
-- spans TWO runs, because ``files.move`` raises on the not-yet-created
spool file in the prepare phase. ``files.install`` yields the transfer and
the elevated install from ONE request, so the whole write fits a single
deploy:

1. Idempotence: the operation reads the destination's current text with the
   ``FileContents`` fact. When it already equals ``content``, nothing is
   yielded and the request reports no change.
2. Spool: an ``InstallSpoolCommand`` transfer copies the content over scp to
   a dest-derived ``/tmp`` spool path (``/tmp/reemote5-install-<sha1 of
   dest>``) -- unprivileged regardless of the request's ``sudo``, and named
   per destination so two installs never clobber one spool file.
3. Install: an ``InstallCommand`` shell runs ``mkdir -p``/``cp``/``chmod``/
   ``rm -f`` wrapped in ``sh -c``, so the request's ``sudo=True`` elevates
   every step -- the file lands root-owned with the requested mode and the
   spool is cleaned up.

``content`` is a plain ``str`` (text is what ``FileContents`` compares), so
unlike ``files.put``'s IO source no identity-stable module-level stream is
needed -- the same string in both phases compares equal. Binary content
stays ``files.put``'s job, into a login-user-writable path.

The script installs a demo config, then runs the very same deploy a second
time: the first run writes, the second reports no change on every host.

Run it with::

    uv run python examples/modules/files/files_install.py
"""

import asyncio
import sys
from collections.abc import Generator

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    OperationRequest,
    PrivilegeError,
    files,
    format_log,
    format_plan,
    op,
    run,
)

# The root-owned destination and the text to install there. A plain str is
# hashable and equal across phases, so it can be built at module level or
# inline -- no identity constraint like files.put's IO source.
DEST = "/etc/reemote5-install-demo.conf"
CONTENT = "# Installed by reemote5 files.install\nmanaged = true\n"

# The inventory: real hosts and their SSH connection information. No
# ``privilege`` entry: the elevated install assumes passwordless sudo for
# the login user; a password would come from ``privilege`` here, never the
# op.
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


def deploy(host: Host) -> Generator[OperationRequest]:
    """One spool-and-install request: transfer + elevated install together.

    ``sudo=True`` elevates only the install half -- the scp spool transfer
    always rides as the login user. The mode defaults to "0644"; pass
    ``mode="0600"`` for a secret-bearing config.
    """
    yield op(
        files.install,
        name="Install demo config",
        content=CONTENT,
        dest=DEST,
        sudo=True,
    )


async def main() -> None:
    try:
        # Dry run: the plan materializes against pre-deployment state.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan: install demo config (--dry) ==")
        print(format_plan(plans))

        # First real run: spool + install on every host whose content differs.
        plans, report, _hosts = await run(INVENTORY, deploy)
        print("\n== Log: install demo config (first run) ==")
        assert report is not None
        print(format_log(report))

        # Second run of the SAME deploy: FileContents now reads back what the
        # first run installed, so every request reports no change.
        plans, report, _hosts = await run(INVENTORY, deploy)
        print("\n== Log: install demo config (second run, no-op) ==")
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