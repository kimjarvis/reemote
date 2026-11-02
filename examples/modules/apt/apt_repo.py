"""Add an apt repository line, with fact-based idempotence.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Specify the target hosts as an *inventory*: a list of dictionaries with
   each host's name plus its SSH ``connection`` information (address,
   username and client key).
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``). Each plain ``Host``
lazily opens an asyncssh connection from its ``connection`` data, gathers
facts and runs commands for real, and ``run`` closes those connections when
it finishes.

``run`` is a coroutine (both phases are async), so ``main`` is async and
driven by ``asyncio.run``.

``apt.repo`` adds (or with ``present=False`` removes) one apt source line,
mirroring pyinfra's ``apt.repo``. ``src`` is the full source string -- here
the Canonical partner repository for Ubuntu noble -- and ``filename``
scopes it to ``/etc/apt/sources.list.d/canonical-partner.list`` instead of
the main ``/etc/apt/sources.list``. Unlike pyinfra -- whose ``apt.repo``
delegates the edit to ``files.line`` -- reemote5 has no line operation, so
the append rides a self-contained apt descriptor: ``sh -c 'echo <src> >>
<file>'``, the wrapper keeping the ``>>`` redirect inside the shell the
driver elevates. The removal direction uses a bare ``sed -i`` delete
without pyinfra's timestamped backup dance (a timestamp would differ
between the prepare and apply phases).

``apt.repo`` parses ``src`` and compares it against the ``AptSources``
fact, so a repository already configured in any sources file is a no-op.
The script yields the *same* request twice to show that idempotence across
the two phases: the prepare phase plans both as changes (nothing is written
yet, so both compare against the same pre-deployment sources), while the
apply phase appends on the first and reports the second as a no-op, because
the fresh per-step fact cache now sees the line the first step wrote.

Writing the apt sources needs root -- the files are root-owned -- so the
requests are flagged ``sudo=True`` and the driver elevates their command
over SSH.

Run it with::

    uv run python examples/modules/apt/apt_repo.py
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
    apt,
    format_log,
    format_plan,
    op,
    run,
)

# The source line to add: the Canonical partner repository for Ubuntu
# noble, managed in its own /etc/apt/sources.list.d/canonical-partner.list.
# Shared by both requests below so they carry identical operation kwargs
# (only the log ``name`` differs).
PARTNER_REPO = {
    "src": "deb http://archive.canonical.com/ubuntu noble partner",
    "filename": "canonical-partner",
}

# The inventory: a real Ubuntu server and its SSH connection information,
# supplied by the application to the library (the same connection data as the
# inventory_ubuntu.json entry the JSON-inventory demonstration loads). The
# default host_factory materializes each entry into a plain Host that connects
# over asyncssh.
INVENTORY = [
    {
        "host": "ubuntu",
        "connection": {
            "address": "192.168.0.221",
            "username": "kjarvis",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "ubuntu",
        "tags": ["ubuntu_host"],
    },
]


def deploy(host: Host) -> Generator[OperationRequest]:
    """Add the partner repo line -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request appends the line; the second carries the same
    arguments and is a guaranteed no-op in the apply phase, because the
    first append is already visible through the fresh per-step fact cache.

    Writing the root-owned apt sources needs root, so both requests are
    flagged ``sudo=True`` and the driver elevates their command over SSH.
    """
    yield op(apt.repo, name="Add the partner repo", sudo=True, **PARTNER_REPO)
    yield op(apt.repo, name="Re-add the partner repo (no-op)", sudo=True, **PARTNER_REPO)


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