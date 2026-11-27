# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install a pinned kubectl with the ``kubectl`` role.

Run it with::

    python examples/roles/kubectl.py

Demonstrates the ``kubectl`` role -- the migration of
``andrewrothstein.kubectl`` -- against every host in ``inventory.json`` that
lives in the ``"Ubuntu"`` OS family. The role installs a version-pinned
``kubectl`` binary, verified against a ``sha256`` checksum, and links the
bare ``kubectl`` name beside it. The variables come from the inventory
through :func:`~reemote.resolve_vars` -- the ``roles.kubectl`` mapping below
overrides the ``KubectlVars`` defaults the way ``defaults/main.yml`` does in
Ansible -- and the deploy runs through
:func:`~reemote.run_waves <reemote.drivers.run_waves>`, which drives one
resumable generator per host: a ``server.Arch`` fact read that picks the
mirror's architecture name, then three requests, in the task order
``files.directory``, ``files.download`` (a control-side, checksum-verified
fetch), ``files.link``.

Two passes demonstrate convergence: the first creates the directory,
installs the pinned binary and creates the symlink; the second plans the
same stream and reports no change anywhere. The dry pass previews only the
first wave: like ``ansible --check`` without facts (setup has not run), the
host's architecture is unread, so the requests that depend on it cannot be
planned yet.

The role's shipped checksum table covers the recent releases (the current
defaults). This demo installs one of them -- no ``checksums`` var needed.
To adopt a version outside the table, add a pin to the inventory mapping::

    "checksums": [{"version": "v1.20.0", "arch": "amd64",
                   "sha256": "<digest from dl.k8s.io>"}]

which is exactly what the role's own test suite does offline against the
simulated driver (``tests/roles/test_kubectl.py``).
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
from reemote.roles import KubectlVars, kubectl

# A second Ubuntu host so the fan-out is visible in the report tree.
INVENTORY: list[dict[str, Any]] = [
    {
        "host": "k8s-dev",
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.10",
            "username": "admin",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "workstations",
        "tags": ["dev"],
        # The role's variables, exactly as a play's ``roles:`` entry would
        # carry them. ``resolve_vars`` binds these ``kubectl_``-prefixed keys
        # to the ``KubectlVars`` fields; the rest keep the model defaults --
        # ``privilege_escalate=true`` and the current release's shipped
        # checksum.
        "roles": {
            "kubectl": {
                "ver": "v1.37.2",
                "mirror": "https://dl.k8s.io/release",
                "install_dir": "/usr/local/bin",
            },
        },
    },
    {
        # The same role, pinned to an earlier release of the shipped table.
        "host": "k8s-ci",
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.11",
            "username": "admin",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "servers",
        "tags": ["ci"],
        "roles": {"kubectl": {"ver": "v1.36.6"}},
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
        vars_set = resolve_vars(KubectlVars, "kubectl", inventory)
    except (ValueError, TypeError) as exc:
        print(f"ERROR: invalid kubectl variables: {exc}")
        raise SystemExit(1) from exc

    deploy = kubectl(vars_set)  # a plain factory: called, not awaited

    try:
        # Dry pass: the plan stops at the ``Arch`` gate it cannot answer
        # without contacting the host, so no request is evaluated.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("=== kubectl dry pass (wave preview) ===")
        print(report.tree())

        # First pass: the ``Arch`` read closes the first wave; the directory,
        # the checksum-pinned download and the link follow in the next.
        report, _hosts = await run_waves(inventory, deploy)
        print("=== kubectl pass 1 (install) ===")
        print(report.tree())

        # Second pass: the same stream, no change anywhere -- the version-
        # suffixed binary's content matches the pin and the link already
        # points at it.
        report, _hosts = await run_waves(inventory, deploy)
        print("=== kubectl pass 2 (idempotence) ===")
        print(report.tree())
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        print(f"ERROR: deployment failed: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
