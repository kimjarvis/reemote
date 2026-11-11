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
   a dest-derived ``/tmp`` spool file inside a sha1-named directory
   (``/tmp/reemote-install-<sha1 of dest>/content``) -- unprivileged
   regardless of the request's ``sudo``, and named per destination so two
   installs never clobber one spool.
3. Install: an ``InstallCommand`` shell runs ``mkdir -p``/``cp``/optional
   ``chown``/``chmod``/``rm -rf`` wrapped in ``sh -c``, so the request's
   ``sudo=True`` elevates every step -- the file lands with the requested
   owner (root when none is named) and mode and the spool directory is cleaned up.

``content`` is a plain ``str`` (text is what ``FileContents`` compares), so
unlike ``files.put``'s IO source no identity-stable module-level stream is
needed -- the same string in both phases compares equal. Binary content
stays ``files.put``'s job, into a login-user-writable path.

Ownership: the optional ``user``/``group`` parameters add a ``chown`` (or
``chgrp`` for a group alone) step inside the elevated install chain, before
the ``chmod`` -- the shape a service config needs (eg a Datadog-style
``dd-agent:dd-agent`` agent config). Like ``mode``, the owner is applied on
every write but never compared: idempotence stays content-only, and an
owner-only drift is ``files.file``'s job to converge. The named owner must
already exist -- the deploy below provisions the ``www`` group and system
user first (the same account the sibling ``files_op_file.py`` example
uses), so the ``chown`` never fails on an unknown user. One caveat the
owned demo deliberately sidesteps: ``FileContents`` cats *unprivileged*,
so a file installed with a mode the login user cannot read (a ``0640``
service-account config) reads back as empty content and re-installs on
every deploy -- the owned demo therefore uses the login-readable ``0644``.

The script installs a demo config and an owned demo config, then runs the
very same deploy a second time: the first run writes, the second reports
no change on every host.

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

    uv run python examples/modules/files/files_install.py
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
    server,
    validate_inventory,
)

# The root-owned destination and the text to install there. A plain str is
# hashable and equal across phases, so it can be built at module level or
# inline -- no identity constraint like files.put's IO source.
DEST = "/etc/reemote-install-demo.conf"
CONTENT = "# Installed by reemote files.install\nmanaged = true\n"

# The owned variant: installed www:www by the install chain's chown step.
# The mode stays login-readable (0644) so the unprivileged FileContents
# read-back still sees the content and the second run is a genuine no-op.
OWNED_DEST = "/etc/reemote-install-owned-demo.conf"
OWNED_CONTENT = "# Installed by reemote files.install, owned by www\nmanaged = true\n"

# examples/modules/files/files_install.py -> the project root, so the inventory
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
    """Provision the www account, then install the plain and owned configs.

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
        when=is_centos,
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
        when=is_centos,
    )
    yield op(
        files.install,
        name="Install demo config",
        content=CONTENT,
        dest=DEST,
        sudo=True,
        when=is_centos,
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
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
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

        # Second run of the SAME deploy: FileContents now reads back what the
        # first run installed, so every request reports no change.
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