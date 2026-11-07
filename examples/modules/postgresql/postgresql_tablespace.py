# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: ensure a tablespace on two real hosts.

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

``postgresql.tablespace`` is a reemote5-original operation modeled on
Ansible's ``postgresql_tablespace``: it reads the ``PostgresqlTablespaces``
fact -- an absent tablespace yields CREATE TABLESPACE ... LOCATION (with
OWNER when given), an existing one is re-owned only when the owner
differs, and ``present=False`` drops it (which fails server-side while
any object still lives there). A location change on an existing
tablespace raises ``OperationError`` -- PostgreSQL cannot move one; drop
it first.

Prerequisite: the LOCATION directory must already exist, be empty and be
owned by the server user. Prepare it out of band on each host once::

    ssh kjarvis@192.168.0.240 'sudo -u postgres mkdir -m 700 /var/lib/postgresql/tbs_fast'
    ssh kjarvis@192.168.0.242 'sudo -u postgres mkdir -m 700 /var/lib/postgresql/tbs_fast'

The deploy yields the same create request twice: in the prepare phase
(where nothing is sent) both plan as changes, but in the apply phase the
second request's fresh per-step fact cache sees the first CREATE
TABLESPACE and reports the tablespace already exists.

Run it with::

    uv run python examples/modules/postgresql/postgresql_tablespace.py
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

# The tablespace location: an empty directory owned by the postgres OS user,
# prepared out of band (see the module docstring).
TABLESPACE_LOCATION = "/var/lib/postgresql/tbs_fast"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Ensure the fast tablespace twice, to show apply-phase convergence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    CREATE TABLESPACE needs superuser, so both requests are flagged
    ``sudo=True`` and ride under the inventory's postgres user -- which
    also owns the location directory.
    """
    yield op(
        postgresql.tablespace,
        name="Ensure fast tablespace",
        tablespace="fast",
        location=TABLESPACE_LOCATION,
        sudo=True,
    )
    yield op(
        postgresql.tablespace,
        name="Ensure fast tablespace (again)",
        tablespace="fast",
        location=TABLESPACE_LOCATION,
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