# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: install text or binary content at a root-owned path with files.install.

This is a *usage* example, not part of the library. ``files.install`` is the
generic spool-and-install operation: it writes arbitrary text or binary
content to a path the login user cannot write directly (anything under
``/etc``, a launcher under ``/usr/local/bin``), the
task the namespace-specific operations (``apt.sources_file``, ``dnf.repo``,
``systemd.dropin``) each spell out for their own config paths.

Why not ``files.put``? The put/edit transfers ride **unprivileged** scp as
the login user, so a root-owned destination is out of reach. The old recipe
-- ``files.put``/``files.edit`` into ``/tmp`` then ``files.move(sudo=True)``
-- spans TWO runs, because ``files.move`` raises on the not-yet-created
spool file in the prepare phase. ``files.install`` yields the transfer and
the elevated install from ONE request, so the whole write fits a single
deploy:

1. Idempotence: the operation reads the destination's current content --
   the ``FileContents`` fact for a ``str``, the binary-safe ``RemoteFileSha1``
   fact for ``bytes``. When it already equals ``content``, nothing is
   yielded and the request reports no change.
2. Spool: an ``InstallSpoolCommand`` (text) or ``InstallBinarySpoolCommand``
   (bytes) transfer copies the content over scp to
   a dest-derived ``/tmp`` spool file inside a sha1-named directory
   (``/tmp/reemote-install-<sha1 of dest>/content``) -- unprivileged
   regardless of the request's ``sudo``, and named per destination so two
   installs never clobber one spool.
3. Validate (optional): with ``validate=`` set, an ``InstallValidateCommand``
   runs the caller's command against the just-spooled ``/tmp`` file *between*
   the transfer and the install -- the atomic validate-then-install hook
   Ansible's ``template``/``copy`` ``validate:`` provides. A non-zero exit
   raises ``CommandError``, so the install never runs and a bad config never
   reaches the protected path; the bytes checked are the very bytes about to
   be copied into place. The demo validates the staged content with
   ``grep -q '^managed = true' {spool}``; the real-world shape is
   ``nginx -t -c {spool}``, which needs root to read every file the config
   references, so the validator inherits the request's ``sudo`` and runs
   elevated. ``{spool}``/``{dest}`` are substituted with shell-quoted paths.
4. Install: an ``InstallCommand`` shell runs ``mkdir -p``/``cp``/optional
   ``chown``/``chmod``/``rm -rf`` wrapped in ``sh -c``, so the request's
   ``sudo=True`` elevates every step -- the file lands with the requested
   owner (root when none is named) and mode and the spool directory is cleaned up.

``content`` is a plain ``str`` or ``bytes`` (issue #23), so unlike
``files.put``'s IO source no identity-stable module-level stream is needed
-- both are hashable and equal across phases. The bytes path is the binary
spool-and-install: idempotence switches to ``RemoteFileSha1`` (no UTF-8
decode, so any bytes work -- a ``.phar`` launcher, a compiled artifact) and
the spool transfer carries the raw bytes, while the elevated install chain
is the same one the text path uses. The sha1 read-back is unprivileged, so
give a binary install a login-readable mode (the ``0755`` launcher shape
the demo below uses).

Ownership: the optional ``user``/``group`` parameters add a ``chown`` (or
``chgrp`` for a group alone) step inside the elevated install chain, before
the ``chmod`` -- the shape a service config needs (eg a Datadog-style
``dd-agent:dd-agent`` agent config). Like ``mode``, the owner is applied on
every write but never compared: idempotence stays content-only, and an
owner-only drift is ``files.file``'s job to converge. The named owner must
already exist -- the deploy below provisions the ``www`` group and system
user first (the same account the sibling ``files_op_file.py`` example
uses), so the ``chown`` never fails on an unknown user. The read-back is
two-tier: ``FileContents`` cats *unprivileged* first and re-reads elevated
with ``sudo`` when the login user cannot read the file (a ``0640``
service-account config), so a restrictive mode keeps content idempotence
too -- the owned demo uses the login-readable ``0644``.

The script installs a demo config, an owned demo config and a demo binary,
then runs the very same deploy a second time: the first run writes, the
second reports no change on every host.

Every request carries ``when=is_redhat``, the declarative eligibility guard: the
predicate reads only **static** inventory data -- ``host.family``, set by the
``\"family\"`` key of the inventory entry -- and is a stable module-level function, so
the prepare and apply phases reach the same skip decision. The driver skips a rejected
request without materializing it, so on hosts outside the ``"RedHat"`` family neither
the commands nor the facts behind them run, and plan and log render the step as
``[skipped: when guard is_redhat]`` in its lockstep slot. The inventory is read from
``inventory.json`` in the current working directory; edit it there to target your
machines.

The inventory is read from ``inventory.json`` in the current working directory; edit it
there to target your machines.

Run it with::

    uv run python examples/modules/files/files_install.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path

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
    server,
    validate_inventory,
)

# The root-owned destination and the text to install there. A plain str is
# hashable and equal across phases, so it can be built at module level or
# inline -- no identity constraint like files.put's IO source.
DEST = "/etc/reemote-install-demo.conf"
CONTENT = "# Installed by reemote files.install\nmanaged = true\n"

# The owned variant: installed www:www by the install chain's chown step.
# The mode is login-readable (0644); a restrictive 0640 would stay idempotent
# too -- FileContents re-reads an unreadable file elevated with sudo.
OWNED_DEST = "/etc/reemote-install-owned-demo.conf"
OWNED_CONTENT = "# Installed by reemote files.install, owned by www\nmanaged = true\n"

# The binary variant (issue #23): raw bytes at a root-owned executable path,
# the drush-launcher shape. The content is deliberately not valid UTF-8 --
# the bytes path never decodes it. Plain bytes are hashable and equal across
# phases, so like a str they can be built at module level or inline.
BINARY_DEST = "/usr/local/bin/reemote-install-demo-bin"
BINARY_CONTENT = b"\xff\xfe\x00reemote demo binary\n"

def is_redhat(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"RedHat"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision.
    """
    return host.family == "RedHat"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Provision the www account, then install the plain and owned configs
    and the demo binary.

    ``sudo=True`` elevates only the install half -- the scp spool transfer
    always rides as the login user. The mode defaults to "0644"; pass
    ``mode="0600"`` for a secret-bearing config. The first two requests
    ensure the ``www`` group and system user the owned install chowns to
    (no-ops once the account exists); the owned install then names
    ``user``/``group`` so the file lands ``www:www`` in the same request
    that writes its content -- no companion ``files.file`` needed.
    """
    yield op(
        server.group,
        name="Ensure www group",
        group="www",
        system=True,
        sudo=True,
        when=is_redhat,
    )
    yield op(
        server.user,
        name="Ensure www user",
        user="www",
        group="www",
        system=True,
        shell="/sbin/nologin",
        ensure_home=False,
        sudo=True,
        when=is_redhat,
    )
    yield op(
        files.install,
        name="Install demo config",
        content=CONTENT,
        dest=DEST,
        # Validate the staged spool before it lands: a non-zero exit aborts the
        # install and leaves DEST untouched. ``grep -q`` is portable; the
        # real-world shape is ``nginx -t -c {spool}``.
        validate="grep -q '^managed = true' {spool}",
        sudo=True,
        when=is_redhat,
    )
    yield op(
        files.install,
        name="Install owned demo config",
        content=OWNED_CONTENT,
        dest=OWNED_DEST,
        mode="0644",
        user="www",
        group="www",
        sudo=True,
        when=is_redhat,
    )
    yield op(
        files.install,
        name="Install demo binary",
        content=BINARY_CONTENT,
        dest=BINARY_DEST,
        # A login-readable executable mode: the binary idempotence read
        # (RemoteFileSha1's sha1sum) runs unprivileged, so a login-unreadable
        # destination would re-install every deploy.
        mode="0755",
        sudo=True,
        when=is_redhat,
    )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Dry run: the plan materializes against pre-deployment state.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan: install demo config (--dry) ==")
        print(format_plan(plans))

        # First real run: spool + install on every host whose content differs.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Log: install demo config (first run) ==")
        assert report is not None
        print(format_log(report))

        # Second run of the SAME deploy: FileContents (text) and
        # RemoteFileSha1 (binary) now read back what the first run
        # installed, so every request reports no change.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Log: install demo config (second run, no-op) ==")
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