# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: set the system hostname on real CentOS hosts.

This is a *usage* example, not part of the library. It shows reemote5's
``server.hostname`` operation, baseline ``server.hostname`` parity for
naming a machine: the deploy gives each host a fully-qualified name derived
from its inventory label (``web1`` becomes ``web1.example.com``), under
``sudo`` because setting the hostname needs root.

The operation is idempotent: it reads the ``Hostname`` fact (``uname -n``)
and yields a command only where the current name differs from the request,
so a host already named correctly plans no change and a second run is a
no-op everywhere. The ``Which`` fact picks the command form: a host with
``hostnamectl`` (systemd) gets ``hostnamectl set-hostname``, which also
persists the name across reboots; an older system without it gets the bare
``hostname`` command. The baseline's ``hostname_file`` upload branch is
deliberately dropped -- reemote5's transfers cannot be elevated, so the
root-owned ``/etc/hostname`` write the baseline does through ``files.put`` has
no faithful equivalent; on a systemd host ``hostnamectl`` already persists
the name, so nothing is lost there.

Every failure mode of the run -- an unreachable host, a rejected elevation,
or a ``hostnamectl`` that exits non-zero (an invalid name, for instance) --
reaches ``main`` as a typed error whose message already names the host, so
``main`` catches them and prints one line each instead of a traceback.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``): each plain ``Host``
lazily opens an asyncssh connection, reads ``uname -n`` and probes
``command -v hostnamectl`` for real, and sets the name under sudo where
needed.

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

    uv run python examples/modules/server/server_op_hostname.py
"""

import asyncio
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
    format_log,
    format_plan,
    op,
    run,
    server,
    validate_inventory,
)

# examples/modules/server/server_op_hostname.py -> the project root, so the inventory
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
    """Name each host after its inventory label, under the example domain.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases; whether the name is already set is decided inside the
    operation against the freshly gathered ``Hostname`` fact, per phase.
    """
    yield op(
        server.hostname,
        name="Set hostname",
        hostname=f"{host.name}.example.com",
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The hostname and the hostnamectl probe
        # are still gathered over SSH (read-only) so the plan reflects each
        # host's real pre-deployment state; nothing is set.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. Each host
        # gets its name set where it differs; a second run plans no change.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Run log (apply phase) ==")
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
    except HostConnectionError as exc:
        # A host could not be reached over SSH at all. Report it plainly --
        # naming every unreachable host -- instead of dumping an asyncssh
        # traceback, and exit non-zero so the failure is script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except PrivilegeError as exc:
        # The host was reached but rejected the ``sudo=True`` elevation -- a
        # wrong ``sudo_password``, or a run-as user the login user may not
        # become. One line naming the host; the detail stays on ``__cause__``.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except CommandError as exc:
        # A ``hostnamectl set-hostname`` (or bare ``hostname``) invocation
        # exited non-zero (an invalid name, for instance). The typed message
        # already names the host, the credential-free command and its stderr,
        # so print just that.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())