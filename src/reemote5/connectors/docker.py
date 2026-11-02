# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The docker connector: run deploys inside local Docker/Podman containers.

Ported from pyinfra's ``@docker`` connector (``connectors/docker.py``), as a
``DockerHost`` subclass overriding exactly the transport seams a plain
``Host`` uses for asyncssh -- ``connect``, ``_run_shell``, ``upload_file``,
``download_file`` and ``aclose`` -- so the drivers, every fact, every
operation and the privilege machinery work unchanged.

An inventory entry selects it with ``connection.connector``::

    [{"host": "build1",
      "connection": {"connector": "docker", "identifier": "alpine:3.20",
                     "docker_cmd": "docker"}}]

``identifier`` is either an *image name* or an *existing container ID*
(pyinfra's ``docker_identifier``):

+ Image -- ``docker run -d <image> tail -f /dev/null`` starts a throwaway
  container, the deploy executes against it, and ``aclose`` commits it into
  a new image (its short ID lands on ``DockerHost.image_id``) and removes
  the container. This is the image-build workflow.
+ Existing container -- inspected, started if stopped, and left running on
  close: an existing container is NEVER committed or removed. (pyinfra
  removes a stopped container it started itself; reemote5 deliberately does
  not touch containers it did not create.)

Every call shells out to the CLI on the *control machine* via
``asyncio.create_subprocess_exec`` -- there is no docker SDK dependency, but
the ``docker`` binary (and a reachable daemon, usually the local socket or
whatever ``DOCKER_HOST`` names) must be installed locally. The ``docker_cmd``
connection key names the binary and is REQUIRED -- there is no default and
no auto-detection -- so ``"podman"`` works through the same class, mirroring
pyinfra's ``PodmanConnector`` (rootless podman needs no daemon at all).
Commands run inside the container through ``docker exec -i
<container> sh -c <shell>``, so container-side exit codes -- including the
shell's 127 for a missing command -- propagate verbatim and the inherited
``run_shell``/``run_shell_privileged`` logic applies unchanged.
"""

import asyncio
import contextlib
import json
import os
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import IO, Any

from ..hosts import CommandError, Host, HostConnectionError, InventoryError

# The connection keys the docker connector understands. Unlike the SSH
# option surface (curated forwarding plus a passthrough), this set is closed:
# an unknown or SSH-only key is rejected outright rather than silently
# ignored, so a typo or a misplaced ``port`` fails fast with an
# ``InventoryError`` -- the same policy as ``hosts._FORBIDDEN_OPTIONS``.
_DOCKER_CONNECTION_KEYS: frozenset[str] = frozenset(
    {"connector", "identifier", "user", "platform", "docker_cmd"}
)


def validate_docker_connection(conn: dict[str, Any]) -> None:
    """Validate a docker ``connection`` mapping, raising ``InventoryError``.

    ``identifier`` (an image name or existing container ID) and
    ``docker_cmd`` (the CLI binary, e.g. ``docker`` or ``podman``) are
    required; ``user`` (the ``docker exec --user`` identity) and
    ``platform`` (the ``docker run --platform`` selector) are optional.
    Every value must be a non-empty string, and any key outside
    ``_DOCKER_CONNECTION_KEYS`` is rejected -- there is no passthrough, so
    an SSH option on a docker entry is an error, not a no-op. pyinfra's
    ``docker_architecture`` is deliberately dropped: ``--arch`` is not a
    ``docker run`` flag and ``platform`` covers it.
    """
    unknown = sorted(set(conn) - _DOCKER_CONNECTION_KEYS)
    if unknown:
        names = ", ".join(repr(key) for key in unknown)
        raise InventoryError(
            f"docker connection option(s) {names} are not supported: the "
            f"docker connector accepts only {sorted(_DOCKER_CONNECTION_KEYS)}"
        )
    for key in ("identifier", "user", "platform", "docker_cmd"):
        if key in conn and (not isinstance(conn[key], str) or not conn[key]):
            raise InventoryError(
                f"docker connection option {key!r} must be a non-empty string"
            )
    if not conn.get("identifier"):
        raise InventoryError(
            "docker connection requires an 'identifier': an image name or "
            "an existing container ID"
        )
    if not conn.get("docker_cmd"):
        raise InventoryError(
            "docker connection requires 'docker_cmd': the CLI binary to "
            "invoke, e.g. 'docker' or 'podman'"
        )


@dataclass
class DockerHost(Host):
    """One container target, driven through the local docker/podman CLI.

    Carries the same inventory fields as ``Host``; all docker options live
    in the inherited ``connection`` mapping (see
    ``validate_docker_connection``). The container is resolved lazily on
    first use and cached in ``_container_id`` (a non-field attribute, like
    ``Host._conn``); ``_owns_container`` records whether reemote5 created it
    from an image, which decides the ``aclose`` behaviour:

    + Owned (image workflow) -- ``commit`` the container into a new image,
      expose its short ID as ``image_id``, then ``rm -f`` the container.
      Teardown is best-effort (it runs in ``run``'s ``finally`` and must not
      mask an in-flight error); a failed commit leaves ``image_id`` None.
    + Existing container -- left running, never committed or removed.

    Privilege escalation is inherited unchanged: ``sudo``/``su`` wrap the
    shell text INSIDE the container, so the escalation binary must exist in
    the image. The ``user`` connection key is orthogonal -- it is the
    ``docker exec --user`` identity applied to every command, including
    fact gathering. Transfers (``docker cp``) are unprivileged, exactly like
    the scp seams they replace.

    Docker daemon-level failures (``docker exec`` exit 125) surface as
    ordinary non-zero exits: ``CommandError`` on the apply path, tolerated
    on fact gathering -- the transport cannot distinguish them from a
    container-side failure without parsing stderr prose.
    """

    @property
    def image_id(self) -> str | None:
        """The short ID of the image ``aclose`` committed, or None.

        Only an owned container (one started from an image identifier) is
        committed; the ID is readable on the hosts ``drivers.run`` returns.
        None until close, for an existing container, or if the commit failed.
        """
        return getattr(self, "_image_id", None)

    @property
    def _docker_cmd(self) -> str:
        """The CLI binary to invoke (the required ``docker_cmd`` key)."""
        return self.connection["docker_cmd"]

    async def _run_local(self, argv: list[str]) -> SimpleNamespace:
        """Run one CLI command on the control machine and capture its result.

        The single local-subprocess seam every other method builds on (the
        analogue of ``Host._run_shell``; test fakes override THIS, returning
        a ``SimpleNamespace`` with ``exit_status``/``stdout``/``stderr`` --
        the same shape ``run_shell`` already consumes). The argv list is
        executed directly, without a local shell, so nothing here needs
        shell-quoting; the container-side command travels as one argv
        element to ``sh -c``.
        """
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return SimpleNamespace(
            exit_status=proc.returncode,
            stdout=stdout.decode("utf-8"),
            stderr=stderr.decode("utf-8"),
        )

    async def _checked_local(self, argv: list[str], identifier: str) -> SimpleNamespace:
        """Run a lifecycle CLI command, wrapping a non-zero exit as a failure.

        Used by ``connect`` for ``container start`` and ``run``: a non-zero
        exit becomes a ``HostConnectionError`` whose cause is the
        ``CommandError`` carrying the command text, exit status and stderr,
        so the aggregate report names the host while the detail stays
        reachable for diagnosis.
        """
        result = await self._run_local(argv)
        if result.exit_status != 0:
            failure = CommandError(
                self.name, shlex.join(argv), result.exit_status, result.stderr
            )
            raise HostConnectionError(
                [(self.name, identifier, failure)], transport="docker"
            ) from failure
        return result

    async def connect(self) -> str:
        """Lazily resolve (and cache) this host's container ID.

        ``container inspect`` decides the workflow: an existing container is
        started if stopped and left running on close; a failed inspect means
        the identifier is an image, so ``run -d [--platform ...] <image>
        tail -f /dev/null`` starts a throwaway container (the last stdout
        line is its ID) that ``aclose`` commits and removes. A missing CLI
        binary (``FileNotFoundError``), any other ``OSError`` or a failed
        start/run is wrapped in ``HostConnectionError`` (labelled ``docker``)
        so ``run_prepare`` aggregates unreachable container targets exactly
        like unreachable SSH hosts.
        """
        container_id = getattr(self, "_container_id", None)
        if container_id is not None:
            return container_id
        validate_docker_connection(self.connection)
        identifier = self.connection["identifier"]
        cli = self._docker_cmd
        try:
            inspected = await self._run_local([cli, "container", "inspect", identifier])
            if inspected.exit_status == 0:
                info = json.loads(inspected.stdout)[0]
                if not info["State"]["Running"]:
                    await self._checked_local(
                        [cli, "container", "start", identifier], identifier
                    )
                self._owns_container = False
                self._container_id = identifier
            else:
                argv = [cli, "run", "-d"]
                platform = self.connection.get("platform")
                if platform:
                    argv += ["--platform", platform]
                argv += [identifier, "tail", "-f", "/dev/null"]
                started = await self._checked_local(argv, identifier)
                lines = started.stdout.splitlines()
                if not lines or not lines[-1].strip():
                    failure = RuntimeError(
                        f"{cli} run printed no container ID: {started.stdout!r}"
                    )
                    raise HostConnectionError(
                        [(self.name, identifier, failure)], transport="docker"
                    ) from failure
                self._owns_container = True
                self._container_id = lines[-1].strip()
        except OSError as exc:
            raise HostConnectionError(
                [(self.name, identifier, exc)], transport="docker"
            ) from exc
        return self._container_id

    async def _run_shell(self, shell: str) -> Any:
        """Run one shell command inside the container via ``exec ... sh -c``.

        The override of the raw SHELL seam: ``docker exec -i [--user ...]
        <container> sh -c <shell>``. No ``-t`` -- the transport is
        non-interactive, exactly like asyncssh's channels, which is why the
        inherited askpass-based sudo/su elevation works here unchanged. The
        result carries the container-side ``exit_status``/``stdout``/
        ``stderr``, so exit 127 still means a missing command inside the
        image and ``run_shell``/``run_shell_privileged`` classify failures
        exactly as over SSH.
        """
        container_id = await self.connect()
        argv = [self._docker_cmd, "exec", "-i"]
        user = self.connection.get("user")
        if user:
            argv += ["--user", user]
        argv += [container_id, "sh", "-c", shell]
        return await self._run_local(argv)

    async def upload_file(self, src: IO[Any], remote: str) -> None:
        """Copy an IO-like source to ``remote`` in the container via ``cp``.

        The docker mirror of ``Host.upload_file``: the stream is rewound and
        read once (a text payload is UTF-8-encoded), spilled to a temporary
        local file because ``docker cp`` copies between *paths*, copied with
        ``<cli> cp <tmp> <container>:<remote>``, and the temporary file is
        removed. Unprivileged, like the scp seam it replaces. A failed copy
        raises ``CommandError`` with the CLI's real exit status (unlike scp,
        which reports an exception) and the ``cp <source> -> <remote>``
        rendering.
        """
        container_id = await self.connect()
        src.seek(0)
        data = src.read()
        if isinstance(data, str):
            data = data.encode("utf-8")
        fd, local = tempfile.mkstemp(prefix="reemote5-upload-")
        try:
            with os.fdopen(fd, "wb") as tmp:
                tmp.write(data)
            result = await self._run_local(
                [self._docker_cmd, "cp", local, f"{container_id}:{remote}"]
            )
            if result.exit_status != 0:
                raise CommandError(
                    self.name,
                    f"{self._docker_cmd} cp {getattr(src, 'name', '<stream>')} -> {remote}",
                    result.exit_status,
                    result.stderr,
                )
        finally:
            with contextlib.suppress(OSError):
                os.unlink(local)

    async def download_file(self, remote: str, dest: IO[Any]) -> None:
        """Copy ``remote`` from the container into an IO-like destination.

        The mirror of ``upload_file``: ``<cli> cp <container>:<remote>
        <tmp>`` fetches into a temporary local file, whose bytes are read
        off the event loop (ruff ASYNC230 forbids a blocking ``open`` in an
        async function) and written into ``dest`` -- decoded as UTF-8 for a
        text stream -- before the stream is rewound and the temporary file
        removed. Unprivileged. A failed copy raises ``CommandError`` with
        the CLI's real exit status and the ``cp <remote> -> <dest>``
        rendering.
        """
        container_id = await self.connect()
        fd, local = tempfile.mkstemp(prefix="reemote5-download-")
        os.close(fd)  # cp writes the destination file itself; only the path matters
        try:
            result = await self._run_local(
                [self._docker_cmd, "cp", f"{container_id}:{remote}", local]
            )
            if result.exit_status != 0:
                raise CommandError(
                    self.name,
                    f"{self._docker_cmd} cp {remote} -> {getattr(dest, 'name', '<stream>')}",
                    result.exit_status,
                    result.stderr,
                )
            data = await asyncio.to_thread(Path(local).read_bytes)
            dest.seek(0)
            dest.truncate()
            try:
                dest.write(data)
            except TypeError:
                # A text stream rejects bytes; decode as UTF-8 (write_dest_bytes).
                dest.write(data.decode("utf-8"))
            dest.seek(0)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(local)

    async def aclose(self) -> None:
        """Tear down the container target: commit and remove an owned one.

        An existing container is left running (never committed or removed).
        An owned one is committed into a new image -- the last stdout line,
        with docker's ``sha256:`` prefix stripped (podman prints the bare
        digest), truncated to the short 12-character ID and exposed as
        ``image_id`` -- then removed with ``rm -f``. Both steps are
        best-effort: this runs in ``run``'s ``finally``, so a teardown
        failure must not mask an in-flight error, and the removal is
        attempted even when the commit failed (which leaves ``image_id``
        None).
        """
        container_id = getattr(self, "_container_id", None)
        if container_id is None:
            return
        self._container_id = None
        if not getattr(self, "_owns_container", False):
            return
        cli = self._docker_cmd
        with contextlib.suppress(Exception):
            committed = await self._run_local([cli, "commit", container_id])
            lines = committed.stdout.splitlines()
            if committed.exit_status == 0 and lines:
                self._image_id = (
                    lines[-1].strip().removeprefix("sha256:")[:12]
                )
        with contextlib.suppress(Exception):
            await self._run_local([cli, "rm", "-f", container_id])


__all__ = ["DockerHost", "validate_docker_connection"]