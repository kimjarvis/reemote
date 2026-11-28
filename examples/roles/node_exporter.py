# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install and run Prometheus' node_exporter with the ``node_exporter`` role.

Run it with::

    python examples/roles/node_exporter.py

Demonstrates the ``node_exporter`` role -- the migration of
``geerlingguy.node_exporter`` -- against every host in ``inventory.json`` that
lives in the ``"Ubuntu"`` OS family. The role unpacks the release tarball in
``/tmp``, moves the binary to ``/usr/local/bin``, creates the
``node_exporter`` service account, installs the ``node_exporter.service``
unit, ensures the unit is running and enabled, and verifies the metrics
endpoint answers. The variables come from the inventory through
:func:`~reemote.resolve_vars` -- the ``roles.node_exporter`` mapping below
overrides the ``NodeExporterVars`` defaults the way ``defaults/main.yml`` does
in Ansible -- and the deploy runs through
:func:`~reemote.run_waves <reemote.drivers.run_waves>`, which drives one
resumable generator per host: a ``--version`` probe (an ``act`` barrier), the
download pair (``files.unarchive`` then ``files.copy``) when the binary is
missing or stale, the account/unit/service requests, a ``checkpoint`` that
flushes the restart handler, and a ``server.HttpEndpoint`` fact that fails the
run if the exporter does not serve metrics.

Two passes demonstrate convergence: the first installs and starts everything;
the second probes the installed version, skips both download requests, and
reports no change anywhere -- and its metrics check now reads a running
exporter. The dry pass previews only the first wave: the body parks on the
``--version`` probe, which ``--check`` semantics cannot answer without
contacting the host, so no request is planned yet.

The override below pins a release, which is the deterministic choice for a
demo run. Drop the ``version`` key to reproduce the role's default
``version: latest``: the body then resolves the current release from the
GitHub API with a control-side ``files.fetch_url`` fetch before deciding
whether to download (see ``tests/roles/test_node_exporter.py`` for the same
stream exercised offline).
"""

import asyncio
import json
import sys
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
from reemote.roles import NodeExporterVars, node_exporter

# A second Ubuntu host so the fan-out is visible in the report tree.
INVENTORY: list[dict[str, Any]] = [
    {
        "host": "monitor1",
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.10",
            "username": "admin",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "servers",
        "tags": ["monitoring"],
        # The role's variables, exactly as a play's ``roles:`` entry would
        # carry them. ``resolve_vars`` binds these ``node_exporter``-prefixed
        # keys to the ``NodeExporterVars`` fields; the rest keep the
        # ``defaults/main.yml`` values the model reproduces.
        "roles": {
            "node_exporter": {
                "version": "1.8.2",
                "port": 9100,
                "options": "--collector.textfile.directory=/var/lib/node_exporter",
            },
        },
    },
    {
        # The same role on a second host, with a fixed service uid/gid --
        # the commented ``node_exporter_uid``/``node_exporter_gid`` knobs.
        "host": "monitor2",
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.11",
            "username": "admin",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "servers",
        "tags": ["monitoring"],
        "roles": {"node_exporter": {"version": "1.8.2", "uid": 940, "gid": 940}},
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

# Common role variables handed to ``resolve_vars`` as call-site ``overrides``.
# They use the model's bare field names -- the same names the inventory's
# ``node_exporter`` role mapping uses -- and win over it for every host.
# Pinning the version here keeps the demo run deterministic; remove it to let
# each host resolve ``latest`` through the GitHub releases API.
NODE_EXPORTER_OVERRIDES = {
    "version": "1.8.2",
}


def load_inventory(path: str = "inventory.json") -> list[dict[str, Any]]:
    """Read the inventory JSON dumped by ``reemote hosts show``."""
    return json.loads(Path(path).read_text())


async def main() -> None:
    try:
        inventory = load_inventory()
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot load inventory: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    try:
        problems = validate_inventory(inventory)
    except InventoryError as exc:
        print(f"ERROR: invalid inventory: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    for problem in problems:
        print(f"WARNING: {problem}")

    try:
        vars_set = resolve_vars(
            NodeExporterVars, "node_exporter", inventory,
            overrides=NODE_EXPORTER_OVERRIDES,
        )
    except (ValueError, TypeError) as exc:
        print(f"ERROR: invalid node_exporter variables: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    deploy = node_exporter(vars_set)  # a plain factory: called, not awaited

    try:
        # Dry pass: the plan stops at the ``--version`` probe it cannot answer
        # without contacting the host, so no request is evaluated.
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("=== node_exporter dry pass (wave preview) ===")
        print(report.tree())

        # First pass: the probe closes the first wave; the download pair (when
        # needed), the account requests, the unit file and the service request
        # follow; the checkpoint flushes the restart handler; the endpoint fact
        # verifies the exporter.
        report, _hosts = await run_waves(inventory, deploy)
        print("=== node_exporter pass 1 (install) ===")
        print(report.tree())

        # Second pass: the probe now reports the pinned version, both download
        # requests are skipped, and nothing else changed -- no handler runs, so
        # the only traffic is the reads and the converged checks.
        report, _hosts = await run_waves(inventory, deploy)
        print("=== node_exporter pass 2 (idempotence) ===")
        print(report.tree())
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        print(f"ERROR: deployment failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except ValueError as exc:
        # The role body's own hard error: the metrics endpoint did not answer
        # 200 with the ``Metrics`` link -- the migration of the role's
        # ``failed_when``. Every wave was already applied when it raised.
        print(f"ERROR: node_exporter verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
