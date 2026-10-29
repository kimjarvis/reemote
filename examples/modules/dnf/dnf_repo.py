"""Add a dnf repository ``.repo`` file, with content-based idempotence.

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

The deploy adds the Docker CE repository by constructing its
``/etc/yum.repos.d/DockerCE.repo`` file from ``dnf.repo``'s arguments
(``baseurl``, ``description``, ``gpgkey``). Unlike pyinfra -- whose
``dnf.repo`` can also download a remote ``.repo`` file -- reemote5 has no
download operation, so ``dnf.repo`` takes a repository *name* and builds the
file itself; a URL ``src`` raises ``ValueError``. The ``$releasever`` and
``$basearch`` in the baseurl are dnf's own substitutions: the content is
shell-quoted, so they reach the file literally and dnf expands them when it
reads the repo.

``dnf.repo`` reads the current file back through the ``DnfRepoFile`` fact and
writes only when the content differs, so re-running is a no-op. The script
yields the *same* repository request twice to show that idempotence across
the two phases: the prepare phase plans both as changes (nothing is written
yet, so both compare against the same pre-deployment file), while the apply
phase writes on the first and reports the second as a no-op, because the
fresh per-step fact cache now sees the file the first step wrote.

Writing under ``/etc/yum.repos.d`` needs root -- the directory is root-owned
-- so the request is flagged ``sudo=True`` and the driver elevates its
command; ``DnfRepoCommand`` wraps the write in ``sh -c`` so the ``>`` redirect
happens inside the elevated shell rather than as the login user.

Run it with::

    uv run python examples/modules/dnf/dnf_repo.py
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
    dnf,
    format_log,
    format_plan,
    op,
    run,
)

# The Docker CE repository, described by the arguments dnf.repo turns into an
# /etc/yum.repos.d/DockerCE.repo file. Shared by both requests below so they
# carry identical operation kwargs (only the log ``name`` differs).
DOCKER_REPO = {
    "src": "DockerCE",
    "description": "Docker CE Stable",
    "baseurl": "https://download.docker.com/linux/centos/$releasever/$basearch/stable",
    "gpgkey": "https://download.docker.com/linux/centos/gpg",
}

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
    """Add the Docker CE repo file -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request writes the file; the second carries the same arguments
    and is a guaranteed no-op in the apply phase, because the first write is
    already visible through the fresh per-step fact cache.

    Writing a ``.repo`` file under root-owned ``/etc/yum.repos.d`` needs root,
    so both requests are flagged ``sudo=True`` and the driver elevates their
    command over SSH.
    """
    yield op(dnf.repo, name="Add Docker CE repo", sudo=True, **DOCKER_REPO)
    yield op(dnf.repo, name="Re-add Docker CE repo (no-op)", sudo=True, **DOCKER_REPO)


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
