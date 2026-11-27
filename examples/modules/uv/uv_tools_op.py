# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install and remove an isolated uv CLI tool with ``uv tool``.

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

``uv.tools`` is reemote's port of pyinfra's ``uv.tool_install``/``tool_uninstall``
family: it installs, upgrades or removes isolated CLI tools, one ``uv tool
install``/``uv tool uninstall`` command per tool. The operation is idempotent on
the ``uv.Tools`` fact (``uv tool list``), and -- unlike pyinfra, whose parser
discards a versionless tool header so a local-path or git install is seen as
absent and reinstalled forever -- reemote keeps such a tool with ``version=None``
and treats a bare name matching an installed-but-versionless tool as *present*,
so the operation converges. Presence is dict membership, not a truthy version.
The script yields the *same* install request twice and the *same* removal request
twice to show that idempotence across the two phases: prepare plans both halves
of each pair as changes, while apply runs the first and reports the second as a
no-op against the fresh per-step fact cache. Installing and then removing the
same tool makes the example *self-restoring*.

The pinned ``ruff==0.8.4`` rides through to the shell exactly as spelled for the
install (``uv tool install --no-progress 'ruff==0.8.4'``), while removal feeds
the *bare name* to ``uv tool uninstall --no-progress ruff`` (uv's uninstall takes
a name, not a pinned requirement), and ``latest=True`` would upgrade an installed
unpinned tool with ``uv tool install --upgrade``. Installing links the tool's
executables into uv's tool bin directory, a per-user change -- no request carries
``sudo``. Like the sibling uv examples, every request carries ``when=is_ubuntu``,
so the run acts only on the Ubuntu demo host and skips the rest.

A tool's isolated environment is declared, not guessed: ``python="3.12"`` picks
the interpreter uv builds the tool environment from and
``with_packages=("ansible-core",)`` adds runtime requirements, one repeatable
``--with`` each, so a plugin-loading CLI gets its plugins. Both ride onto the
install and onto the ``latest=True`` upgrade (which re-creates the environment),
and neither exists on ``uv tool uninstall``. Idempotence deliberately stays
keyed on name and version -- the gathered fact is the plain ``uv tool list``,
which reports neither flag -- so changing only the environment of a tool already
installed at the requested version does not reinstall it; converge on that drift
with ``latest=True`` or by removing the tool first. This script installs plain
``ruff`` rather than demoing the flags, because pinning an interpreter version
would make the run download that interpreter on the shared demo host.

This example provisions uv itself before deploying: ``main`` first awaits
``provision_uv`` from the sibling ``provision_uv.py`` (an idempotent
``snap.package`` install of Astral's ``astral-uv`` snap), because Ubuntu's noble
apt suite ships no ``uv`` package and a host without uv would exit 127 on the
fact read and raise ``CommandNotFoundError`` before a single change was planned.

Run it with::

    uv run python examples/modules/uv/uv_tools_op.py
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

# The tool to install and remove again, pinned so the idempotence is a version
# match rather than a bare name. A module-level tuple, so the request kwargs
# stay hashable and identical across the two phases. Removal feeds uv the bare
# name (``ruff``), which the operation derives from the pin.
TOOLS = ("ruff==0.8.4",)


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
    """Install the tool -- twice, to show idempotence -- then remove it.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases. The first request installs whatever the ``Tools`` listing does not
    carry yet; the second carries the same arguments and is a no-op in the apply
    phase once the first landed it. The third removes the tool again
    (``present=False``) and the fourth is its no-op repeat, leaving uv's tool
    directory as found so the shared-host sweep is not polluted.

    Installing links executables into the user's uv tool bin directory, so no
    request needs elevation.
    """
    yield op(
        uv.tools,
        name="Install ruff",
        tools=TOOLS,
        when=is_ubuntu,
    )
    yield op(
        uv.tools,
        name="Re-run install (no-op)",
        tools=TOOLS,
        when=is_ubuntu,
    )
    yield op(
        uv.tools,
        name="Remove ruff again",
        tools=TOOLS,
        present=False,
        when=is_ubuntu,
    )
    yield op(
        uv.tools,
        name="Re-run removal (no-op)",
        tools=TOOLS,
        present=False,
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Install uv on the Ubuntu hosts before anything below reads or mutates
        # tool state, so the example runs even on a host where uv was never
        # installed (see provision_uv.py).
        print("\n== Provisioning uv (provision_uv.py) ==")
        await provision_uv(inventory)

        # --dry: prepare phase only. Facts are gathered over SSH (the
        # ``uv tool list`` read is read-only) so the plan reflects each host's
        # real pre-deployment state; no mutating command is sent.
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
