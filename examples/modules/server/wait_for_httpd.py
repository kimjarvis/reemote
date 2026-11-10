# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: start httpd on real CentOS hosts, then wait until systemd reports it active.

This is a *usage* example, not part of the library. It shows reemote5's
second kind of **non-shell** operation: ``server.wait_service`` does not run
a command string, it yields a ``WaitForFactCommand`` that the driver *polls*
-- re-reading the ``SystemdServiceState`` fact over each host's asyncssh
connection every ``interval`` seconds until the ``is_active`` predicate holds
or ``timeout`` expires (raising ``WaitTimeoutError`` and aborting the run).

The deploy is the two-step shape from the baseline's ``systemd.service`` +
``server.wait`` idiom. ``server.service`` is the idempotent half: it reads the
unit's ``ActiveState`` and ``UnitFileState`` and yields ``systemctl start`` /
``systemctl enable`` only when the unit differs from the request (so the
second run plans no change). It needs root, hence ``sudo=True``.
``server.wait_service`` is the non-idempotent half: like the baseline's
``server.wait`` it reads no facts and is always planned as a change --
whether the unit is *already* active is discovered at apply time by the
driver's poll loop, never at plan time. Deciding at plan time would read
pre-deployment state in the prepare phase and post-start state in the apply
phase, and branching on that would trip the determinism guard. The wait
itself is unprivileged (``systemctl show`` works as the login user), so it
carries no ``sudo`` flag.

Every failure mode of the run -- an unreachable host, a rejected elevation, a
``systemctl`` that exits non-zero, or a wait that times out -- reaches ``main``
as a typed error whose message already names the host, so ``main`` catches them
and prints one line each instead of a traceback.

Because the apply phase runs each step on every host concurrently and joins
before the next one, both hosts have *finished* ``systemctl start`` before
either begins polling, and the two poll loops then run in parallel; a slow
host only delays the step barrier, not the other host's polling.

The example assumes ``httpd`` is installed on the hosts (the dnf base example
``examples/modules/dnf/dnf_packages.py`` installs it).

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``): each plain ``Host``
lazily opens an asyncssh connection, reads the unit state for real, runs
``systemctl`` under sudo, and polls until httpd is active.

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

    uv run python examples/modules/server/wait_for_httpd.py
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
    WaitTimeoutError,
    format_log,
    format_plan,
    op,
    run,
    server,
    validate_inventory,
)

# examples/modules/server/wait_for_httpd.py -> the project root, so the inventory
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
    """Start and enable httpd, then wait for systemd to report it active.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases. Only the service operation is elevated; the wait polls as
    the login user.
    """
    yield op(
        server.service,
        name="Start httpd",
        service="httpd",
        running=True,
        enabled=True,
        sudo=True,
        when=is_centos,
    )
    yield op(
        server.wait_service,
        name="Wait for httpd to become active",
        service="httpd",
        timeout=60.0,
        interval=2.0,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The unit state is still gathered over SSH
        # (read-only) so the plan reflects each host's real pre-deployment state;
        # nothing is started and nothing is polled -- the wait is simply planned.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. Step 1 starts
        # httpd where needed; step 2 polls each host until it is active (or
        # raises WaitTimeoutError after 60s).
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
        # A ``systemctl`` invocation exited non-zero (an unknown or masked
        # unit, for instance). The typed message already names the host, the
        # credential-free command and its stderr, so print just that.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except WaitTimeoutError as exc:
        # httpd never became active within the timeout. The message carries
        # the host, the wait descriptor and the last polled state, which is
        # everything worth printing -- no traceback.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
