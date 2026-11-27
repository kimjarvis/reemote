# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Put uv's tool bin directory on the PATH with ``uv tool update-shell``.

This is a *usage* example, not part of the library. It drives reemote's
two-phase execution model end to end over a **real SSH transport** (asyncssh):

1. Read the target hosts from ``inventory.json`` in the current working
   directory and validate it up front with ``validate_inventory``, so a
   malformed file fails with an ``InventoryError`` before any host is
   materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects ``SimulatedHost`` via ``run``'s
``host_factory`` and never touches the network -- this example uses the *default*
factory (``Host.from_inventory``); each ``Host`` lazily opens an asyncssh
connection and ``run`` closes it when the deploy finishes.

``uv.tools_update_shell`` is reemote's port of pyinfra's ``uv.tools_update_shell``:
it runs ``uv tool update-shell`` to add uv's tool executable directory to the
login shell's PATH, so installed tools are runnable by name. The operation is
idempotent on two facts: it reads the tool bin directory from the consolidated
``uv.Info`` record's ``tool_bin_dir`` (pyinfra's ``UvToolDir``) and the ``PATH``
from ``server.Env("PATH")`` -- reemote's generalization of pyinfra's ``Path``
fact, colon-split by the operation -- and yields the command only when the
directory is *missing* from the PATH. The script yields the same request twice to
show that idempotence across the two phases: prepare plans both as changes, while
apply runs the first and reports the second as a no-op once the directory is on
the PATH. Two edge cases matter: after ``provision_uv`` the snapped binary's tool
bin directory is often already on the PATH, so the operation may simply converge
with no command at all; and when uv is *not* installed the ``Info`` chain
short-circuits and an unknown ``tool_bin_dir`` is treated as "nothing to add" --
not a perpetual re-yield.

Updating the shell configuration is a per-user change, so no request carries
``sudo``. Like the sibling uv examples, every request carries ``when=is_ubuntu``:
the run acts only on the Ubuntu demo host and skips the rest.

This example provisions uv itself before deploying -- ``main`` awaits
``provision_uv`` from the sibling ``provision_uv.py`` -- because the ``Info`` read
that resolves the tool bin directory runs a chain anchored on ``uv`` and a host
without uv would exit 127 and raise ``CommandNotFoundError``.

Run it with::

    uv run python examples/modules/uv/uv_tools_update_shell.py
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
    format_log,
    format_plan,
    op,
    run,
    uv,
    validate_inventory,
)

# The uv provisioning helper sits beside this script; add its directory to
# sys.path so ``import provision_uv`` resolves whether the example is run by
# path or imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from provision_uv import provision_uv


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``), never a mutable fact,
    and stays a stable module-level function -- the two rules every ``when``
    predicate follows so both deploy phases reach the same eligibility decision
    and the determinism guard holds. The Ubuntu demo host is the one
    ``provision_uv.py`` installs uv on before this example deploys.
    """
    return host.family == "Ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Ensure the uv tool bin directory is on the PATH -- twice.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases. The first request yields ``uv tool update-shell`` only if the
    ``Info`` tool bin directory is absent from the ``Env`` PATH; the second
    carries the same (empty) arguments and converges to a no-op in the apply
    phase against the fresh per-step fact cache. Updating the user's shell config
    needs no elevation.
    """
    yield op(
        uv.tools_update_shell,
        name="Add uv tools to PATH",
        when=is_ubuntu,
    )
    yield op(
        uv.tools_update_shell,
        name="Re-run update-shell (no-op)",
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Install uv on the Ubuntu hosts before deploying, so the ``Info`` chain
        # resolves a real tool bin directory (see provision_uv.py).
        print("\n== Provisioning uv (provision_uv.py) ==")
        await provision_uv(inventory)

        # --dry: prepare phase only. Both facts (Info, Env PATH) are gathered
        # over SSH and are read-only, so the plan reflects each host's real
        # pre-deployment state; no command is sent.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library reports every host-level failure as a typed error whose
        # message names the host and the failing command. Print it and exit
        # non-zero -- no traceback.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
