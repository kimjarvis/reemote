# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: build a container image with the docker connector.

This is a *usage* example, not part of the library. The inventory entry
selects the ``docker`` connector instead of SSH::

    {"host": "build1",
     "connection": {"connector": "docker", "identifier": "alpine:3.20",
                    "docker_cmd": "podman"}}

so the default ``host_factory`` (``Host.from_inventory``) materializes a
``DockerHost`` and the very same deploy -- a ``files.file`` marker plus a
``files.put`` upload -- runs *inside a container* on the control machine:

1. ``identifier`` is an image name, so reemote starts a throwaway container
   from it (``docker run -d alpine:3.20 tail -f /dev/null``).
2. Both phases drive it through ``docker exec`` (facts included -- the
   ``FileExists`` and ``RemoteFileSha1`` reads behind the two operations)
   and ``docker cp`` (the upload), exactly as the SSH transport would use
   asyncssh's shell and scp.
3. When ``run`` closes the host, the container is committed into a *new
   image* and removed; the short image ID is printed from ``host.image_id``
   at the end. Point ``identifier`` at an existing container ID instead and
   the container is left running, never committed or removed.

Unlike the SSH examples, this one needs **no remote host**: the prerequisite
is a local container CLI with a reachable daemon. ``docker_cmd`` is
required and names the binary explicitly -- this example hardcodes rootless
``podman`` (which needs no daemon at all); switch it to ``"docker"`` for a
Docker setup. Commands run as the container's default user (root for
alpine), so no ``sudo`` is needed -- and none is available in a stock alpine
image.

Note that the ``--dry`` run also starts (and commits) its own throwaway
container: facts are gathered for real inside it, and closing an owned
container always commits, so a dry run leaves an *unchanged* image behind.

Run it with::

    uv run python examples/demonstrations/connectors/docker_files.py
"""

import asyncio
import io
import sys
from collections.abc import Generator

from reemote import (
    CommandError,
    DockerHost,
    Host,
    HostConnectionError,
    OperationRequest,
    files,
    format_log,
    format_plan,
    op,
    run,
)

INVENTORY = [
    {
        "host": "build1",
        "connection": {
            "connector": "docker",
            "identifier": "alpine:3.20",
            "docker_cmd": "podman",
        },
    },
]

# The content source is defined once at module level: OperationRequest
# compares the IO object by identity, so both phases must see the same stream.
APP_CONF = io.BytesIO(
    b"# installed by reemote inside the container\nlisten = 0.0.0.0:8080\n"
)


def deploy(host: Host) -> Generator[OperationRequest]:
    """Mark the image as configured, then install a small configuration file.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases. Both operations are idempotent against the facts they read
    inside the container, and neither needs elevation: ``docker exec``
    already runs as root on a stock alpine image.
    """
    yield op(files.file, name="Mark image configured", path="/srv/.reemote-configured")
    yield op(files.put, name="Install app.conf", src=APP_CONF, dest="/srv/app.conf")


async def main() -> None:
    try:
        # --dry: prepare phase only. The throwaway container is still started
        # (facts are gathered inside it for real), but no mutating command is
        # sent; closing it commits an unchanged image.
        plans, _, _ = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: a fresh container from the same image, prepare then apply.
        plans, report, hosts = await run(INVENTORY, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))

        # run() closed the hosts, committing each owned container into a new
        # image and removing it; the short image ID is on the returned host.
        for host in hosts:
            assert isinstance(host, DockerHost)
            print(f"\n== Committed image for {host.name}: {host.image_id} ==")
    except HostConnectionError as exc:
        # The docker CLI is missing, the daemon is unreachable or the image
        # could not be started. One line naming every failed target -- no
        # subprocess traceback.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except CommandError as exc:
        # A command inside the container exited non-zero (or a docker cp
        # failed). The typed message names the host, the command and stderr.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())