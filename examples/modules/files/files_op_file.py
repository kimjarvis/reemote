# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: manage a regular file on two real hosts with files.file.

This is a *usage* example, not part of the library. It drives the
``files.file`` operation through reemote5's two-phase execution model:

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Write a *deploy generator* that yields ``op(files.file, ...)`` requests.
   The generator is plain sync code -- it only describes work, it never
   touches a host.
3. Call ``run(INVENTORY, deploy, dry=True)`` for the prepare phase: the
   operation is materialized against each host's real pre-deployment state
   (read-only fact gathering over SSH) and ``format_plan`` shows what would
   change. Nothing is sent.
4. Call ``run(INVENTORY, deploy)`` for the real run: prepare again, then
   apply in lockstep across hosts, with ``format_log`` showing every
   command and whether it changed anything.

The operation is idempotent in exactly the ``files.directory`` shape: it
stats the path once through the ``File`` fact and yields only the commands
that close a gap. An absent file is created with ``touch``, then
``chmod``/``chown`` when ``mode``/``user``/``group`` are requested, and a
missing parent directory is ``mkdir -p``'d first (``create_remote_dir``,
on by default -- an *existing* parent is left alone, so manage its own
mode/owner with ``files.directory``). An existing file re-runs only the
``chmod``/``chown`` whose values differ from the fact's metadata, and
``present=False`` removes a regular file with ``rm -f`` -- the primitive
``files.link``'s readlink-based check cannot provide.

The deploy ensures the same file twice to prove idempotence: the second
request is a guaranteed no-op because the first request's
``mkdir``/``touch``/``chmod``/``chown`` are visible to it through the fresh
per-step fact cache -- exactly the "act, then read" property the lockstep
apply phase provides. A third request removes a stale marker to show the
``rm -f`` branch.

``/srv/web`` and the ``www`` ownership are root-owned, so those requests
carry ``sudo=True``: the driver wraps every shell in ``sudo`` (the log shows
``$ sudo touch ...``). ``www`` must exist as a user and group on the hosts
-- otherwise the ``chown`` fails with a ``CommandError``. The marker lives
under the login user's home, so its removal needs no elevation. pyinfra
also offers ``force``/``force_backup`` here; reemote5 keeps ``force`` (a
non-file is removed with ``rm -rf`` and recreated; without it such a path
raises ``ValueError``) but deliberately drops the timestamped backup ``mv``,
whose ``datetime.now()`` would differ between the phases. The ``touch``
option is *not* used below: like pyinfra's, it is deliberately
non-idempotent and would re-run the ``touch`` on every pass.

Unlike the pytest suite -- which injects the test framework's
``SimulatedHost`` via ``run``'s ``host_factory`` and never touches the
network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection, stats the file for real, and runs the shells when a gap needs
closing.

Run it with::

    uv run python examples/modules/files/files_op_file.py
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
    files,
    format_log,
    format_plan,
    op,
    run,
)

# The configuration file to ensure: a root-owned path and ownership, so the
# requests are elevated. ``www`` must exist as a user and group. The parent
# directory is created by the operation when it is missing.
CONFIG_PATH = "/srv/web/conf.d/app.conf"

# A stale marker under the login user's home: removal needs no elevation,
# and files.file is the operation that can remove a *regular* file.
MARKER_PATH = "/home/kjarvis/reemote5-marker"

# The inventory: real hosts and their SSH connection information, supplied
# by the application to the library. The default host_factory materializes
# each entry into a plain Host that connects over asyncssh.
INVENTORY = [
    {
        "host": "web1",
        "connection": {
            "address": "192.168.0.240",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
    {
        "host": "web2",
        "connection": {
            "address": "192.168.0.242",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
]


def deploy(host: Host) -> Generator[OperationRequest]:
    """Ensure the same file twice, then remove a marker.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first two requests are elevated: ``/srv/web`` and the ``www``
    ownership are root-owned. The octal mode spelling ``"640"`` normalises
    to the int the ``File`` fact reports, so a repeat reads as already in
    state. The removal is unprivileged -- the marker is login-user owned.
    """
    yield op(
        files.file,
        name="Ensure app config",
        path=CONFIG_PATH,
        mode="640",
        user="www",
        group="www",
        sudo=True,
    )
    yield op(
        files.file,
        name="Re-ensure app config",
        path=CONFIG_PATH,
        mode="640",
        user="www",
        group="www",
        sudo=True,
    )
    yield op(
        files.file,
        name="Remove stale marker",
        path=MARKER_PATH,
        present=False,
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. The file is still stat'ed over SSH
        # (read-only) so the plan reflects each host's real pre-deployment
        # state; no command is sent.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. The first
        # request runs mkdir/touch/chmod/chown as needed; the second sees
        # the file in the desired state and reports no change; the third
        # removes the marker only when it is there.
        plans, report, _hosts = await run(INVENTORY, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed
        # error whose message names the host and the failing command. Print
        # that message -- no traceback -- and exit non-zero so the failure
        # stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
