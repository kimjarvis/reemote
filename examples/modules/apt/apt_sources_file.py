# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Manage a deb822 apt sources file, with fact-based idempotence.

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

``apt.sources_file`` writes (or with ``present=False`` removes) a complete
deb822 ``.sources`` file, mirroring pyinfra's ``apt.sources_file`` -- here
the Docker repository as ``/etc/apt/sources.list.d/docker.sources``. The
repository is described structurally (``types``, ``uris``, ``suites``,
``components``, ``architectures``, ``signed_by``) rather than as a source
line, and each list option expands into one entry per combination. Unlike
pyinfra -- whose ``apt.sources_file`` delegates the write to ``files.put``
-- reemote5 keeps the namespaces separate: the generated deb822 text is
spooled to ``/tmp`` by an unprivileged scp upload (a transfer never
elevates), then an elevated ``sh -c`` installs it into ``sources.list.d``
with mode ``0644`` and removes the spool file -- the same privilege split as
``apt.key``. The removal direction is idempotent too: the ``AptSourcesFile``
fact reads the file's content (None when absent), so ``present=False`` yields
``rm -f`` only when the file exists -- the same in-namespace existence gate
``dnf.repo`` uses, without importing another namespace's fact.

``apt.sources_file`` compares every desired entry against the ``AptSources``
fact, so a file whose repositories are all already configured is a no-op.
The script yields the *same* request twice to show that idempotence across
the two phases: the prepare phase plans both as changes (nothing is written
yet, so both compare against the same pre-deployment sources), while the
apply phase writes on the first and reports the second as a no-op, because
the fresh per-step fact cache now sees the entries the first step wrote.

Installing under ``/etc/apt`` needs root -- the files are root-owned -- so
the requests are flagged ``sudo=True``; the driver elevates the install
shell over SSH, while the scp upload stays unprivileged either way.

Run it with::

    uv run python examples/modules/apt/apt_sources_file.py
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

# The Docker repository as a deb822 sources file:
# /etc/apt/sources.list.d/docker.sources, signed by the keyring apt.key (or
# a manual step) placed at /etc/apt/keyrings/docker.gpg. Shared by both
# requests below so they carry identical operation kwargs (only the log
# ``name`` differs). Tuples keep the containers hashable and stable across
# the prepare and apply phases.
DOCKER_SOURCES = {
    "filename": "docker",
    "types": ("deb",),
    "uris": ("https://download.docker.com/linux/debian",),
    "suites": ("bookworm",),
    "components": ("stable",),
    "architectures": ("amd64",),
    "signed_by": "/etc/apt/keyrings/docker.gpg",
}

# The inventory: a real server and its SSH connection information, supplied
# by the application to the library (the same connection data as the
# inventory_ubuntu.json entry the JSON-inventory demonstration loads). The
# default host_factory materializes each entry into a plain Host that
# connects over asyncssh.
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
    """Write the Docker deb822 sources file -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request spools and installs the file; the second carries the
    same arguments and is a guaranteed no-op in the apply phase, because
    the first write is already visible through the fresh per-step fact
    cache.

    Installing under the root-owned /etc/apt needs root, so both requests
    are flagged ``sudo=True``; only the install shell is elevated -- the
    scp spool upload is a transfer and always runs unprivileged.
    """
    yield op(
        apt.sources_file,
        name="Add Docker apt repository (deb822)",
        sudo=True,
        **DOCKER_SOURCES,
    )
    yield op(
        apt.sources_file,
        name="Re-add Docker apt repository (no-op)",
        sudo=True,
        **DOCKER_SOURCES,
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