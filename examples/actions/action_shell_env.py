# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: run a bespoke command with environment variables set.

This is a *usage* example, not part of the library. It demonstrates the ``env``
keyword on ``action.Shell`` -- a mapping of environment variable names to
values rendered as a shell-quoted ``env K=V ...`` prefix on the command.

The escape hatch this serves: bespoke software whose configuration travels in
environment variables rather than command-line flags or config files. The
DataDog APM single-step instrumentation, for instance, reads
``DD_APM_INSTRUMENTATION_ENABLED``, ``DD_APM_INSTRUMENTATION_LIBRARIES`` and
``DD_INSTALLER_*`` from the environment of the script it runs. Without ``env``,
the only workaround was inlining ``VAR=val cmd`` into the command string --
unquoted and fragile -- or smuggling ``env`` through ``Script(interpreter=...)``.

Each key and value is shell-quoted individually, so spaces, quotes and
metacharacters are safe. An empty or ``None`` mapping leaves the command
unchanged. The prefix combines with privilege escalation: ``sudo env K=V ...``
sets the variables for the elevated command.

Actions bypass ``op()`` and the plan layer's hashing, so a plain ``dict`` is
legal here -- no ``UnhashableArgumentError`` concern.

The script runs ``printenv`` on each ``centos`` host with ``DD_SERVICE`` and
``DD_ENV`` set, then reads the captured stdout from the ``Result``. The hosts
come from the shared ``inventory.json`` in the project root; ``is_centos``
filters the host list before the ``gather_action`` fan-out -- an action has no
``when`` meta-parameter -- so only the ``centos`` hosts are contacted.

Run it with::

    uv run python examples/actions/action_shell_env.py
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from reemote import (
    Host,
    HostConnectionError,
    InventoryError,
    PrivilegeError,
    Shell,
    gather_action,
    validate_inventory,
)

# The shared inventory in the project root -- the same inventory.json the
# library's other examples read.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The environment variables this demo sets. In a real migration these would be
# the DataDog APM instrumentation knobs; here we use two simple ones to prove
# the mechanism.
DD_ENV_VARS = {
    "DD_SERVICE": "my-app",
    "DD_ENV": "production",
}


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    ``json.loads`` parses the file into a list of entry dicts, and
    ``validate_inventory`` checks every entry against the inventory schema --
    a malformed file raises before any connection is attempted.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_centos(host: Host) -> bool:
    """Group guard: the host belongs to the inventory group ``"centos"``.

    Reads only the host's static ``group`` field. Actions have no ``when``
    meta-parameter, so the guard is applied by filtering the host list before
    the ``gather_action`` fan-out.
    """
    return host.group == "centos"


def report(label: str, results: dict) -> None:
    """Print each host's Result for one action command, in inventory order."""
    print(f"== {label} ==")
    for name, result in results.items():
        print(f"{name}: exit={result['exit_status']} ok={result['exit_status'] == 0}")
        if result["stdout"]:
            print(f"  stdout: {result['stdout'].strip()}")
        if result["stderr"]:
            print(f"  stderr: {result['stderr'].strip()}")


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file itself is missing or not valid JSON.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The file parsed, but an entry violates the inventory schema.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"== Inventory loaded from {INVENTORY_PATH} ==")
    all_hosts = [Host.from_inventory(entry) for entry in inventory]
    # The group guard: gather_action has no `when`, so filter the host list
    # before the fan-out -- only the centos hosts are ever contacted.
    hosts = [host for host in all_hosts if is_centos(host)]
    skipped = [host.name for host in all_hosts if not is_centos(host)]
    print(f"== Skipped (group guard is_centos): {', '.join(skipped)} ==")
    try:
        # printenv with no arguments dumps the whole environment; grep for
        # the two variables we set to prove the env prefix worked.
        results = await gather_action(
            hosts,
            Shell,
            command="printenv | grep -E '^DD_(SERVICE|ENV)='",
            env=DD_ENV_VARS,
        )
    except (HostConnectionError, PrivilegeError) as exc:
        # A library-level failure (unreachable host, rejected elevation) is a
        # typed error naming the host. Print it -- no traceback -- and exit
        # non-zero. A bespoke command's own non-zero exit is NOT this path.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    report("printenv DD_SERVICE/DD_ENV (env prefix)", results)


if __name__ == "__main__":
    asyncio.run(main())
