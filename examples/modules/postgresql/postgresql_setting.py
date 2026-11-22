# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: tune a PostgreSQL configuration parameter on real Ubuntu hosts.

This is a *usage* example, not part of the library. It drives reemote's
two-phase execution model end to end over a **real SSH transport**
(asyncssh):

1. Read ``inventory.json`` from the current working directory and parse it as JSON -- an
   inventory is just a list of dictionaries, which is exactly what JSON expresses --
   then validate it up front with ``validate_inventory`` so a malformed file fails with
   an ``InventoryError`` naming the offending entry, *before* any host is materialized
   or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``postgresql.setting`` is a reemote-original operation modeled on
Ansible's ``postgresql_set``, tuning a GUC through ALTER SYSTEM SET/RESET
-- which writes ``postgresql.auto.conf``, overriding the main
configuration file (the config-file-rewrite apply method is deliberately
dropped). The GUC parameter is named ``setting`` (not Ansible's ``name``,
which ``op()`` consumes as the request's display name). It reads the
``PostgresqlSettings`` fact and compares the
requested value against the parameter's current ``setting`` as text in
the parameter's own unit -- there is no unit normalization, so pass
``262144`` rather than ``2GB`` for ``shared_buffers`` in 8kB blocks.
``present=False`` resets the parameter (a no-op when its source is
already ``default``), and ``reload=True`` follows a change with ``SELECT
pg_reload_conf()`` so reloadable parameters take effect without a
restart; restart-required parameters still carry ``pending_restart`` in
the fact until the server restarts. An unknown parameter name raises
``ValueError`` in prepare.

The script sets ``log_min_duration_statement`` (a reloadable,
superuser-settable logging parameter) and yields the same request twice:
in the prepare phase (where nothing is sent) both plan as changes, but in
the apply phase the second request's fresh per-step fact cache sees the
first ALTER SYSTEM SET and reports the setting already holds the value
-- with no reload command, which only ever follows a change.

**Host prerequisite.** This example needs a running PostgreSQL server. The demo Ubuntu
host runs PostgreSQL 18.6 as a snap whose local socket uses ``trust`` authentication, so
the ``postgres`` superuser role is reachable without a password -- but there is no
``postgres`` OS user. The ``sudo=True`` operations ride under root and connect as that
role through ``psql_user="postgres"``; the login-user fact reads need a role for the
login user (created out of band) or ``psql_user="postgres"`` on ``gather_fact``. A stock
apt install uses peer authentication instead, where root reaches the ``postgres`` role
only if ``pg_hba.conf`` allows it (or the inventory privilege sets
``sudo_user="postgres"``). Replace ``inventory.json`` in the current working directory
with your own file of PostgreSQL machines to run live.

Every request carries ``when=is_ubuntu``, the declarative eligibility guard: the
predicate reads only **static** inventory data -- ``host.family``, set by the
``"family"`` key of the inventory entry -- and is a stable module-level function, so the
prepare and apply phases reach the same skip decision. The driver skips a rejected
request without materializing it, so on hosts outside the ``"Ubuntu"`` family neither
the commands nor the facts behind them run, and plan and log render the step as
``[skipped: when guard is_ubuntu]`` in its lockstep slot. The inventory is read from
``inventory.json`` in the current working directory; edit it there to target your
machines.

The ``sudo=True`` requests ride under root, and every request passes
``psql_user="postgres"`` so the ``psql`` client connects as the ``postgres``
superuser role -- through the demo snap's trust-authenticated local socket,
or a host-side ``~/.pgpass`` / peer entry elsewhere. No password travels
through operation arguments, plans or logs (the baseline's
``psql_password`` option is deliberately dropped namespace-wide).

Run it with::

    uv run python examples/modules/postgresql/postgresql_setting.py
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
    postgresql,
    run,
    validate_inventory,
)


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision.
    """
    return host.family == "Ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Set log_min_duration_statement to 500ms twice, reloading on change.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    ALTER SYSTEM needs superuser, so both requests are flagged
    ``sudo=True`` (root) and connect as the ``postgres`` role through
    ``psql_user="postgres"``.
    """
    yield op(
        postgresql.setting,
        name="Log statements slower than 500ms",
        setting="log_min_duration_statement",
        value="500",
        reload=True,
        sudo=True,
        psql_user="postgres",
        when=is_ubuntu,
    )
    yield op(
        postgresql.setting,
        name="Log statements slower than 500ms (again)",
        setting="log_min_duration_statement",
        value="500",
        reload=True,
        sudo=True,
        psql_user="postgres",
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
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
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())