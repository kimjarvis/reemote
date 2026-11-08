# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: stage a script with files.put, then run it with server.script.

This is a *usage* example, not part of the library. It demonstrates
``server.script`` -- the namespace alias of the ``Script`` ACTION -- which
runs a script file that is ALREADY on the host. The action never transfers
anything: the transfer is a deploy's job (``files.put``), the execution is
an action's job, and the two meet outside the deploy model:

1. A deploy stages the script: ``files.put`` copies a module-level
   ``io.StringIO`` payload to ``/tmp/reemote5-script-demo.sh`` on the
   ``centos`` hosts (``when=is_centos``). The put source is one stable
   stream identity across both phases, as ``OperationRequest`` compares a
   stream by identity. scp writes the file as the login user WITHOUT an
   executable bit.
2. ``gather_action(centos_hosts, server.script, path=..., args=...)`` runs
   the staged file. Because it is not executable, the action's wrapper
   falls back to ``sh <path> <args>`` -- no chmod needed. Each argument is
   shell-quoted individually.
3. A ``Shell`` action adds the executable bit (``chmod +x``), and the same
   ``server.script`` call now executes the file DIRECTLY: the kernel
   honours its ``#!/bin/sh`` shebang. The caller does not choose the mode
   -- the wrapper tests ``-x`` per run. An explicit ``interpreter=``
   keyword (e.g. ``interpreter="python3 -u"``) would override both branches
   and run ``<interpreter> <path> <args>`` regardless of the bit.
4. A call against a path that does NOT exist returns an error RESULT --
   ``exit_status`` 127 with a ``server.script: no such file`` message on
   stderr -- not an exception. A missing file is the script's own outcome,
   and the caller decides what it means, exactly as for ``Shell``.

The fan-out is group-guarded: ``is_centos`` (``host.group == "centos"``)
filters the host list BEFORE each ``gather_action``, because an action
fan-out has no ``when`` meta-parameter. Only the ``centos`` hosts are
contacted; the ``alpine`` and ``ubuntu`` entries are reported as skipped.

Library-level failures still raise and are caught in ``main``:
``CommandError`` from the deploy's apply phase, ``HostConnectionError`` for
an unreachable host, ``PrivilegeError`` for a rejected elevation. Each
``gather_action`` call re-connects and closes the hosts when it finishes,
so no manual ``aclose`` is needed here.

Run it with::

    uv run python examples/actions/action_script.py
"""

import asyncio
import io
import json
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    Shell,
    files,
    format_log,
    gather_action,
    op,
    run,
    server,
    validate_inventory,
)

# The script payload, created once on the control machine: a stream, never a
# local path. The same object is passed to put in every phase, so the prepare
# and apply request sequences compare equal.
SCRIPT_BODY = io.StringIO(
    "#!/bin/sh\n"
    "# Staged by reemote5 -- the server.script action example.\n"
    'echo "hello $1"\n'
)

# Staging destination under /tmp: the unprivileged scp transfer can write it
# as the login user, and the action can run it from there.
STAGED_SCRIPT = "/tmp/reemote5-script-demo.sh"

# The shared inventory in the project root -- the same inventory.json the
# library's other examples read.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


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

    Reads only the host's static ``group`` field. The deploy carries it as
    ``when``; the action fan-outs have no ``when`` meta-parameter, so they
    reuse this predicate as a host-list filter.
    """
    return host.group == "centos"


def stage_script(host: Host) -> Generator[OperationRequest]:
    """The deploy: transfer only. Running the script is the action's job."""
    yield op(
        files.put,
        name="Stage the demo script",
        src=SCRIPT_BODY,
        dest=STAGED_SCRIPT,
        when=is_centos,
    )


def report(label: str, results: dict) -> None:
    """Print each host's Result for one action call, in inventory order."""
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
    try:
        # The deploy: files.put stages the script on the centos hosts. Its
        # apply-phase join is the barrier -- every host has the file before
        # any action runs.
        _plans, apply_report, hosts = await run(inventory, stage_script)
        print("== Deploy: stage the script (files.put) ==")
        assert apply_report is not None
        print(format_log(apply_report))

        # The group guard as a host-list filter: gather_action has no `when`.
        centos_hosts = [host for host in hosts if is_centos(host)]
        skipped = [host.name for host in hosts if not is_centos(host)]
        print(f"== Skipped (group guard is_centos): {', '.join(skipped)} ==")

        # Run 1: the staged file has NO executable bit, so the wrapper's
        # -x test falls back to `sh <path> <args>`. No chmod needed.
        via_sh = await gather_action(
            centos_hosts, server.script, path=STAGED_SCRIPT, args=("world",)
        )

        # Make it executable, then run the SAME call again: now the file
        # executes directly and the kernel honours its #!/bin/sh shebang.
        await gather_action(centos_hosts, Shell, command=f"chmod +x {STAGED_SCRIPT}")
        direct = await gather_action(
            centos_hosts, server.script, path=STAGED_SCRIPT, args=("world",)
        )

        # A missing file is an error RESULT, not an exception: exit 127
        # with a "server.script: no such file" message on stderr.
        missing = await gather_action(
            centos_hosts,
            server.script,
            path="/tmp/reemote5-script-does-not-exist.sh",
        )
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # A library-level failure is a typed error naming the host. Print
        # it -- no traceback -- and exit non-zero. The script's own non-zero
        # exit (including the missing-file 127) is NOT this path.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print()
    report("server.script, not executable (runs via sh)", via_sh)
    print()
    report("server.script, executable (runs directly via shebang)", direct)
    print()
    report("server.script, missing file (exit 127, reported as data)", missing)


if __name__ == "__main__":
    asyncio.run(main())
