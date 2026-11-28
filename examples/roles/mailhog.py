# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install and run MailHog with the ``mailhog`` role.

Run it with::

    python examples/roles/mailhog.py

Demonstrates the ``mailhog`` role -- the migration of
``geerlingguy.mailhog`` -- against every host in ``inventory.json`` that
lives in the ``"Ubuntu"`` OS family. The role downloads the pinned MailHog
and mhsendmail release binaries into ``/opt/mailhog`` (control-side, the
``files.download`` stance) and installs the ``mailhog.service`` systemd unit
it then starts and enables. The variables come from the inventory through
:func:`~reemote.resolve_vars` -- the ``roles.mailhog`` mapping below
overrides the ``MailhogVars`` defaults the way ``defaults/main.yml`` does in
Ansible -- and the deploy runs through
:func:`~reemote.run_waves <reemote.drivers.run_waves>`, which drives one
resumable generator per host: a ``server.Arch`` fact read that picks the
release archives' architecture suffix, then five requests, in the task order
``files.directory``, ``files.download`` x2, ``files.install`` (the rendered
unit), ``systemd.service``.

Two passes demonstrate convergence: the first creates the directory, installs
both binaries and the unit file, and starts and enables the service; the
second plans the same stream and reports no change anywhere -- including no
``daemon-reload``, because the reload is state-gated. The dry pass previews
only the first wave: like ``ansible --check`` without facts (setup has not
run), the host's architecture is unread, so the requests whose URLs depend on
it cannot be planned yet.

The binaries are fetched without a checksum pin -- exactly what the Ansible
role's ``get_url`` tasks do. To ship a different release, set ``version`` /
``mhsendmail_version`` in the inventory mapping; to mirror the archives
elsewhere, set the full ``binary_url`` / ``mhsendmail_binary_url``.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    HostConnectionError,
    InventoryError,
    PrivilegeError,
    resolve_vars,
    run_waves,
    validate_inventory,
)
from reemote.roles import MailhogVars, mailhog

# The shape ``inventory.json`` entries take for this role: role variables in
# the ``mailhog`` mapping, keyed by the bare ``MailhogVars`` field names.
# Recognized entry keys: ``host``, ``family``, ``connection``, ``privilege``,
# ``group``, ``tags``; everything else -- here ``roles`` -- is preserved as
# host extra for ``resolve_vars`` to bind.
INVENTORY: list[dict[str, Any]] = [
    {
        "host": "dev-mail",
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.10",
            "username": "admin",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "workstations",
        "tags": ["dev"],
        # The role's variables: the upstream defaults, spelled out. Leaving
        # any key out keeps the model default; ``install_dir`` also lands in
        # the unit's ``ExecStart`` line.
        "roles": {
            "mailhog": {
                "install_dir": "/opt/mailhog",
                "version": "1.0.0",
                "mhsendmail_version": "0.2.0",
            },
        },
    },
    {
        # The same role on a second Ubuntu host, with nothing in the mapping:
        # every default applies.
        "host": "staging-mail",
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.11",
            "username": "admin",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "servers",
        "tags": ["staging"],
        "roles": {"mailhog": {}},
    },
    {
        # Outside the target family: the role's opening guard returns before
        # its first yield, so this host emits no request, no barrier and no
        # log line at all.
        "host": "legacy-db",
        "family": "RedHat",
        "connection": {
            "address": "192.0.2.12",
            "username": "admin",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "legacy",
    },
]


def load_inventory(path: str = "inventory.json") -> list[dict[str, Any]]:
    """Read the inventory JSON dumped by ``reemote hosts show``."""
    return json.loads(Path(path).read_text())


async def main() -> None:
    try:
        inventory = load_inventory()
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot load inventory: {exc}")
        raise SystemExit(1) from exc

    try:
        problems = validate_inventory(inventory)
    except InventoryError as exc:
        print(f"ERROR: invalid inventory: {exc}")
        raise SystemExit(1) from exc
    for problem in problems:
        print(f"WARNING: {problem}")

    try:
        vars_set = resolve_vars(MailhogVars, "mailhog", inventory)
    except (ValueError, TypeError) as exc:
        print(f"ERROR: invalid mailhog variables: {exc}")
        raise SystemExit(1) from exc

    deploy = mailhog(vars_set)  # a plain factory: called, not awaited

    try:
        # Dry pass: the plan stops at the ``Arch`` gate it cannot answer
        # without contacting the host, so no request is evaluated.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("=== mailhog dry pass (wave preview) ===")
        print(report.tree())

        # First pass: the ``Arch`` read closes the first wave; the directory,
        # the two binary downloads, the unit file and the service request
        # follow in the next.
        report, _hosts = await run_waves(inventory, deploy)
        print("=== mailhog pass 1 (install) ===")
        print(report.tree())

        # Second pass: the same stream, no change anywhere -- the binaries'
        # content matches what the URLs serve, the unit file is already the
        # rendered text, and the service is up and enabled.
        report, _hosts = await run_waves(inventory, deploy)
        print("=== mailhog pass 2 (idempotence) ===")
        print(report.tree())
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        print(f"ERROR: deployment failed: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
