# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: tune a PostgreSQL configuration parameter on two real hosts.

This is a *usage* example, not part of the library. It drives reemote5's
two-phase execution model end to end over a **real SSH transport**
(asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key). Each entry also carries a ``privilege``
   section: ``sudo_user="postgres"`` is the OS user the ``sudo=True``
   requests ride under, so the ``psql`` client connects through peer
   authentication -- no password travels through operation arguments,
   plans or logs (pyinfra's ``psql_password`` option is deliberately
   dropped namespace-wide).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``postgresql.setting`` is a reemote5-original operation modeled on
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

Run it with::

    uv run python examples/modules/postgresql/postgresql_setting.py
"""

import asyncio
import sys
from collections.abc import Generator

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    OperationRequest,
    PrivilegeError,
    format_log,
    format_plan,
    op,
    postgresql,
    run,
)

# The inventory: real hosts and their SSH connection information, supplied
# by the application to the library. The privilege section names the OS user
# elevation rides under -- postgres, so psql authenticates as the database
# superuser through peer auth. No password appears here or in any request.
INVENTORY = [
    {
        "host": "web1",
        "connection": {
            "address": "192.168.0.240",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
        "privilege": {"sudo_user": "postgres"},
    },
    {
        "host": "web2",
        "connection": {
            "address": "192.168.0.242",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
        "privilege": {"sudo_user": "postgres"},
    },
]


def deploy(host: Host) -> Generator[OperationRequest]:
    """Set log_min_duration_statement to 500ms twice, reloading on change.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    ALTER SYSTEM needs superuser, so both requests are flagged
    ``sudo=True`` and ride under the inventory's postgres user.
    """
    yield op(
        postgresql.setting,
        name="Log statements slower than 500ms",
        setting="log_min_duration_statement",
        value="500",
        reload=True,
        sudo=True,
    )
    yield op(
        postgresql.setting,
        name="Log statements slower than 500ms (again)",
        setting="log_min_duration_statement",
        value="500",
        reload=True,
        sudo=True,
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
        plans, report, _hosts = await run(INVENTORY, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())