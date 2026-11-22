# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Reload the systemd daemon with ``systemd.daemon_reload``.

This is a *usage* example, not part of the library. It shows both halves of
systemd daemon-reload control over a **real SSH transport** (asyncssh), using
reemote's two-phase execution model:

1. Read the target hosts from ``inventory.json`` in the current working directory and
   validate it with ``validate_inventory``.
2. Build a branch-free deploy generator yielding two ``OperationRequest``s,
   guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``systemd.daemon_reload`` ports the baseline's operation of the same name and
keeps its contract in the no-unit form: it reads no fact and always yields a
single ``systemctl daemon-reload`` -- every run plans the reload as a change.
That is the right call when something *outside* reemote's knowledge touched a
unit file; a config-writing operation such as ``systemd.dropin`` therefore
chains its own reload only when it actually changed a file.

The ``unit`` form makes the reload **state-gated**: it reads
``SystemdNeedDaemonReload`` (systemd's own ``NeedDaemonReload`` property) and
yields the command only while that unit's cached fragment is stale. This is
the idempotent companion of a ``files.install`` of a unit file -- after the
reload runs, a converged pass reports no change, so a handler request built
with ``restart_handler(..., daemon_reload=True)`` never breaks convergence.
The script demonstrates the contrast: the stateless reload is planned as a
change, the gated reload of the (unchanged) web-server unit is planned as no
change.

Both requests are flagged ``sudo=True`` (a system manager's daemon-reload
needs root) and carry ``when=is_systemd`` so the OpenRC ``alpine`` host --
which has no ``systemctl`` -- is skipped rather than failed. The gated
request's unit comes from ``web_server_unit``, a branch on static
``host.family`` that stays identical across both phases, so the determinism
guard holds. The scope flags (``user_mode``/``machine``/``user_name``) stay
at their system-scope defaults.

Unlike the pytest suite -- which injects ``SimulatedHost`` via ``run``'s
``host_factory`` -- this example uses the default ``Host.from_inventory`` factory and
connects for real. The inventory is read from ``inventory.json`` in the current working
directory.

Run it with::

    uv run python examples/modules/systemd/systemd_daemon_reload.py
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
    systemd,
    validate_inventory,
)


def is_systemd(host: Host) -> bool:
    """``when`` guard: the host runs systemd rather than OpenRC.

    Reads only static inventory data (``host.family``) and stays a stable
    module-level function, so both deploy phases reach the same decision.
    """
    return host.family != "Alpine"


def web_server_unit(host: Host) -> str:
    """The installed web-server unit name, by OS family (static data only).

    ``httpd`` on the RHEL-family hosts, ``apache2`` on the Debian-family
    one -- the same static branch the ``systemd_service`` example makes.
    """
    return "httpd" if host.family == "RedHat" else "apache2"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Reload the daemon (stateless), then reload-if-stale for one unit."""
    # The baseline's stateless contract: no fact read, always a change.
    yield op(
        systemd.daemon_reload,
        name="Reload systemd daemon",
        sudo=True,
        when=is_systemd,
    )
    # The state-gated form: yielded only while the unit's cached fragment is
    # stale (NeedDaemonReload=yes), so a converged host plans no change.
    yield op(
        systemd.daemon_reload,
        name="Reload if the web server unit is stale",
        unit=web_server_unit(host),
        sudo=True,
        when=is_systemd,
    )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only; the reloads are planned, nothing is sent.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
