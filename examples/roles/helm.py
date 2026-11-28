# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install a pinned helm with the ``helm`` role.

Run it with::

    python examples/roles/helm.py

Demonstrates the ``helm`` role -- the migration of ``geerlingguy.helm`` --
against every host in ``inventory.json`` that lives in the ``"Ubuntu"`` OS
family. The role installs a version-pinned ``helm`` binary: it downloads the
release tarball (verified against a ``sha256`` checksum), extracts it and
copies the ``helm`` binary into place -- but only when the binary is absent or
reports a different version. The variables come from the inventory through
:func:`~reemote.resolve_vars` -- the ``roles.helm`` mapping below overrides the
``HelmVars`` defaults the way ``defaults/main.yml`` does in Ansible -- and the
deploy runs through :func:`~reemote.run_waves <reemote.drivers.run_waves>`,
which drives one resumable generator per host: a ``files.FileExists`` gate on
the binary path, then (when it exists) an ``act(Shell)`` ``helm version``
probe, then the two install requests -- ``files.unarchive`` (a control-side,
checksum-verified fetch and extract) and ``files.copy`` into place.

Two passes demonstrate convergence: the first installs the pinned binary; the
second reads ``helm version``, sees the wanted release and reports no change
anywhere -- it emits no install request at all. The dry pass previews only the
first wave: like ``ansible --check`` without facts (the binary's existence has
not been read), the ``FileExists`` gate is unanswered, so the requests that
depend on it cannot be planned yet.

The role's shipped checksum table covers the recent releases (the current
defaults). This demo installs one of them -- no ``checksums`` var needed. To
adopt a version outside the table, add a pin to the inventory mapping::

    "checksums": [{"version": "v3.1.0", "arch": "amd64",
                   "sha256": "<digest from get.helm.sh>"}]

which is exactly what the role's own test suite does offline against the
simulated driver (``tests/roles/test_helm.py``).
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
from reemote.roles import HelmVars, helm

# A second Ubuntu host so the fan-out is visible in the report tree.
INVENTORY: list[dict[str, Any]] = [
    {
        "host": "helm-dev",
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.10",
            "username": "admin",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "workstations",
        "tags": ["dev"],
        # The role's variables, exactly as a play's ``roles:`` entry would
        # carry them. ``resolve_vars`` binds these ``helm``-keyed fields to the
        # ``HelmVars`` model; the rest keep the defaults -- a modern release,
        # ``privilege_escalate=true`` and the shipped checksum.
        "roles": {
            "helm": {
                "version": "v4.3.2",
                "repo_path": "https://get.helm.sh",
                "bin_path": "/usr/local/bin/helm",
            },
        },
    },
    {
        # The same role, pinned to an earlier release of the shipped table.
        "host": "helm-ci",
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.11",
            "username": "admin",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "servers",
        "tags": ["ci"],
        "roles": {"helm": {"version": "v3.22.1"}},
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
        vars_set = resolve_vars(HelmVars, "helm", inventory)
    except (ValueError, TypeError) as exc:
        print(f"ERROR: invalid helm variables: {exc}")
        raise SystemExit(1) from exc

    deploy = helm(vars_set)  # a plain factory: called, not awaited

    try:
        # Dry pass: the plan stops at the ``FileExists`` gate it cannot answer
        # without contacting the host, so no request is evaluated.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("=== helm dry pass (wave preview) ===")
        print(report.tree())

        # First pass: the ``FileExists`` read closes the first wave; on a
        # fresh host the checksum-pinned extract and the copy follow.
        report, _hosts = await run_waves(inventory, deploy)
        print("=== helm pass 1 (install) ===")
        print(report.tree())

        # Second pass: the same stream, no change anywhere -- the binary is
        # present and reports the pinned version, so the install requests are
        # never emitted.
        report, _hosts = await run_waves(inventory, deploy)
        print("=== helm pass 2 (idempotence) ===")
        print(report.tree())
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        print(f"ERROR: deployment failed: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
