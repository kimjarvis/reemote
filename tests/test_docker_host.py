# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the docker connector (``DockerHost``), without a docker daemon.

Every local CLI call goes through the single ``_run_local`` seam, so the
tests install a scripted ``FakeDockerCli`` in its place -- the same pattern
the SSH tests use for ``_run_shell``. The fake records each argv, serves
canned lifecycle results and *actually copies bytes* for ``cp``, so the
transfer roundtrips and the end-to-end ``run()`` exercise real content
through the real drivers with zero docker installed.
"""

import hashlib
import io
import json
import shlex
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace

import pytest

from reemote5 import (
    CommandError,
    CommandNotFoundError,
    DockerHost,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    files,
    op,
    run,
)
from reemote5.drivers import run_prepare
from tests.simulated_host import SimulatedHost


def _result(exit_status: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    """One canned CLI result, the shape ``_run_local`` returns."""
    return SimpleNamespace(exit_status=exit_status, stdout=stdout, stderr=stderr)


class FakeDockerCli:
    """A scripted stand-in for the local docker CLI, installed as ``_run_local``.

    ``existing``/``running`` decide what ``container inspect`` reports (an
    existing container vs an image that must be started with ``run``);
    ``container_files`` is the fake container filesystem backing ``cp`` and
    the ``sha1sum`` shell the ``RemoteFileSha1`` fact runs. The ``*_result``
    overrides let a test fail one lifecycle step.
    """

    def __init__(
        self,
        *,
        existing: bool = False,
        running: bool = True,
        container_id: str = "c0ffee123456",
    ) -> None:
        self.calls: list[list[str]] = []
        self.existing = existing
        self.running = running
        self.container_id = container_id
        self.container_files: dict[str, bytes] = {}
        self.shell_results: dict[str, SimpleNamespace] = {}
        self.run_result: SimpleNamespace | None = None
        self.start_result: SimpleNamespace | None = None
        self.commit_result: SimpleNamespace | None = None
        self.cp_result: SimpleNamespace | None = None
        self.missing_binary = False

    async def run(self, argv: list[str]) -> SimpleNamespace:
        self.calls.append(list(argv))
        if self.missing_binary:
            raise FileNotFoundError(2, "No such file or directory: 'docker'")
        sub = argv[1]
        if sub == "container" and argv[2] == "inspect":
            if self.existing:
                info = [{"State": {"Running": self.running}}]
                return _result(stdout=json.dumps(info))
            return _result(1, stderr="Error: No such object")
        if sub == "container" and argv[2] == "start":
            return self.start_result or _result()
        if sub == "run":
            return self.run_result or _result(stdout=f"{self.container_id}\n")
        if sub == "exec":
            return self._exec(argv[-1])
        if sub == "cp":
            return self._cp(argv[2], argv[3])
        if sub == "commit":
            return self.commit_result or _result(stdout=f"sha256:{'0123456789ab' * 6}\n")
        if sub == "rm":
            return _result()
        raise AssertionError(f"unexpected CLI argv: {argv}")

    def _exec(self, shell: str) -> SimpleNamespace:
        if shell in self.shell_results:
            return self.shell_results[shell]
        if shell.startswith("sha1sum "):
            path = shlex.split(shell)[1]
            data = self.container_files.get(path)
            if data is None:
                return _result(1, stderr=f"sha1sum: {path}: No such file or directory")
            return _result(stdout=f"{hashlib.sha1(data).hexdigest()}  {path}\n")
        return _result()

    def _cp(self, src: str, dest: str) -> SimpleNamespace:
        if self.cp_result is not None:
            return self.cp_result
        if ":" in src:  # download: container path -> local temp file
            # rsplit: the identifier itself may contain a colon (a tag).
            path = src.rsplit(":", 1)[1]
            data = self.container_files.get(path)
            if data is None:
                return _result(1, stderr=f"no such file: {path}")
            Path(dest).write_bytes(data)
            return _result()
        # upload: local temp file -> container path
        path = dest.rsplit(":", 1)[1]
        self.container_files[path] = Path(src).read_bytes()
        return _result()

    def subcommands(self) -> list[tuple[str, ...]]:
        """The CLI subcommands invoked, without the binary name (for asserts)."""
        return [tuple(argv[1:]) for argv in self.calls]


def make_host(
    connection: dict | None = None, **cli_kwargs
) -> tuple[DockerHost, FakeDockerCli]:
    """A ``DockerHost`` with a scripted CLI installed on its ``_run_local``."""
    host = DockerHost(
        name="build1",
        connection={
            "connector": "docker",
            "identifier": "alpine:3.20",
            "docker_cmd": "docker",
        }
        | (connection or {}),
    )
    cli = FakeDockerCli(**cli_kwargs)
    host._run_local = cli.run
    return host, cli


# --- Inventory dispatch and validation ----------------------------------------


def test_from_inventory_dispatches_docker():
    host = Host.from_inventory(
        {
            "host": "build1",
            "connection": {
                "connector": "docker",
                "identifier": "alpine",
                "docker_cmd": "docker",
            },
        }
    )
    assert type(host) is DockerHost
    assert host.name == "build1"
    assert host.image_id is None


def test_from_inventory_default_stays_ssh():
    for connection in ({}, {"connector": "ssh"}, {"address": "10.0.0.1"}):
        host = Host.from_inventory({"host": "web1", "connection": connection})
        assert type(host) is Host


def test_unknown_connector_is_rejected():
    with pytest.raises(InventoryError, match="unknown connector"):
        Host.from_inventory(
            {"host": "web1", "connection": {"connector": "podman"}}
        )


def test_docker_connection_requires_identifier():
    with pytest.raises(InventoryError, match="identifier"):
        Host.from_inventory(
            {
                "host": "build1",
                "connection": {"connector": "docker", "docker_cmd": "docker"},
            }
        )


def test_docker_connection_requires_docker_cmd():
    with pytest.raises(InventoryError, match="docker_cmd"):
        Host.from_inventory(
            {
                "host": "build1",
                "connection": {"connector": "docker", "identifier": "alpine"},
            }
        )


def test_docker_connection_rejects_unknown_keys():
    with pytest.raises(InventoryError, match="'port'"):
        Host.from_inventory(
            {
                "host": "build1",
                "connection": {
                    "connector": "docker",
                    "identifier": "alpine",
                    "docker_cmd": "docker",
                    "port": 22,
                },
            }
        )


def test_docker_connection_rejects_non_string_values():
    for option in ({"identifier": 123}, {"docker_cmd": ""}, {"user": None}):
        with pytest.raises(InventoryError, match="non-empty string"):
            Host.from_inventory(
                {
                    "host": "build1",
                    "connection": {
                        "connector": "docker",
                        "identifier": "alpine",
                        "docker_cmd": "docker",
                    }
                    | option,
                }
            )


def test_subclass_factory_ignores_the_connector_key():
    host = SimulatedHost.from_inventory(
        {"host": "sim1", "connection": {"connector": "docker", "identifier": "alpine"}}
    )
    assert type(host) is SimulatedHost


# --- connect: the container lifecycle ------------------------------------------


async def test_connect_uses_a_running_container_as_is():
    host, cli = make_host(existing=True, running=True)

    assert await host.connect() == "alpine:3.20"
    assert cli.subcommands() == [("container", "inspect", "alpine:3.20")]

    # Idempotent: a second connect resolves from the cache, no further CLI.
    assert await host.connect() == "alpine:3.20"
    assert len(cli.calls) == 1


async def test_connect_starts_a_stopped_container():
    host, cli = make_host(existing=True, running=False)

    assert await host.connect() == "alpine:3.20"
    assert cli.subcommands() == [
        ("container", "inspect", "alpine:3.20"),
        ("container", "start", "alpine:3.20"),
    ]


async def test_connect_runs_an_image_with_platform():
    host, cli = make_host({"platform": "linux/amd64"}, container_id="beefcafe9999")
    # The last stdout line is the container ID; earlier lines are noise.
    cli.run_result = _result(stdout="Unable to find image locally\nbeefcafe9999\n")

    assert await host.connect() == "beefcafe9999"
    assert cli.subcommands() == [
        ("container", "inspect", "alpine:3.20"),
        (
            "run",
            "-d",
            "--platform",
            "linux/amd64",
            "alpine:3.20",
            "tail",
            "-f",
            "/dev/null",
        ),
    ]


async def test_connect_wraps_a_missing_cli_binary():
    host, cli = make_host()
    cli.missing_binary = True

    with pytest.raises(HostConnectionError) as excinfo:
        await host.connect()

    err = excinfo.value
    assert err.host_names == ("build1",)
    assert "docker connectivity error" in str(err)
    assert isinstance(err.__cause__, FileNotFoundError)


async def test_connect_wraps_a_failed_run():
    host, cli = make_host()
    cli.run_result = _result(125, stderr="daemon not running")

    with pytest.raises(HostConnectionError) as excinfo:
        await host.connect()

    cause = excinfo.value.__cause__
    assert isinstance(cause, CommandError)
    assert cause.exit_status == 125
    assert "daemon not running" in cause.stderr


async def test_connect_wraps_a_failed_start():
    host, cli = make_host(existing=True, running=False)
    cli.start_result = _result(1, stderr="cannot start")

    with pytest.raises(HostConnectionError) as excinfo:
        await host.connect()

    assert isinstance(excinfo.value.__cause__, CommandError)


async def test_connect_rejects_an_empty_run_stdout():
    host, cli = make_host()
    cli.run_result = _result(stdout="\n")

    with pytest.raises(HostConnectionError, match="docker connectivity error"):
        await host.connect()


async def test_run_prepare_aggregates_docker_connection_errors():
    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(files.file, path="/etc/motd")

    hosts = []
    for name in ("build1", "build2"):
        host, cli = make_host()
        host.name = name
        cli.missing_binary = True
        hosts.append(host)

    with pytest.raises(HostConnectionError) as excinfo:
        await run_prepare(hosts, deploy)

    err = excinfo.value
    assert err.host_names == ("build1", "build2")
    assert "docker connectivity error on 2 hosts" in str(err)


# --- _run_shell: exec wrapping and failure classification -----------------------


async def test_run_shell_wraps_exec_with_user_and_podman():
    host, cli = make_host({"user": "app", "docker_cmd": "podman"}, existing=True)
    cli.shell_results["echo hi"] = _result(stdout="hi\n")

    assert await host.run_shell("echo hi") == "hi\n"
    assert cli.calls[-1] == [
        "podman",
        "exec",
        "-i",
        "--user",
        "app",
        "alpine:3.20",
        "sh",
        "-c",
        "echo hi",
    ]


async def test_run_shell_exit_127_raises_command_not_found():
    host, cli = make_host(existing=True)
    cli.shell_results["lsb_release -ca"] = _result(
        127, stderr="sh: lsb_release: not found"
    )

    with pytest.raises(CommandNotFoundError):
        await host.run_shell("lsb_release -ca")


async def test_run_shell_checked_failure_raises_command_error():
    host, cli = make_host(existing=True)
    cli.shell_results["false"] = _result(1, stderr="nope")

    with pytest.raises(CommandError) as excinfo:
        await host.run_shell("false", check=True)

    assert excinfo.value.exit_status == 1


async def test_privileged_shell_elevates_inside_the_container():
    host, cli = make_host(existing=True)
    host.privilege = {"sudo_user": "deploy", "su_user": "postgres"}

    await host.run_shell_privileged("id", sudo=True)
    assert cli.calls[-1][-1] == "sudo -u deploy id"

    await host.run_shell_privileged("id", su=True)
    assert cli.calls[-1][-1] == "su postgres -c id"

    # The elevation is container-side: the local CLI argv stays a plain exec.
    assert cli.calls[-1][:3] == ["docker", "exec", "-i"]


# --- Transfers: docker cp roundtrips --------------------------------------------


async def test_upload_file_copies_bytes_and_cleans_up():
    host, cli = make_host(existing=True)

    await host.upload_file(io.BytesIO(b"payload"), "/etc/motd")

    assert cli.container_files["/etc/motd"] == b"payload"
    cp = cli.calls[-1]
    assert cp[1] == "cp"
    assert cp[3] == "alpine:3.20:/etc/motd"
    assert not Path(cp[2]).exists()  # the temp spill file was removed


async def test_upload_file_encodes_a_text_stream():
    host, cli = make_host(existing=True)

    await host.upload_file(io.StringIO("héllo"), "/etc/motd")

    assert cli.container_files["/etc/motd"] == "héllo".encode()


async def test_download_file_fills_bytes_and_text_destinations():
    host, cli = make_host(existing=True)
    cli.container_files["/etc/motd"] = "héllo".encode()

    dest = io.BytesIO()
    await host.download_file("/etc/motd", dest)
    assert dest.getvalue() == "héllo".encode()
    assert dest.tell() == 0

    text_dest = io.StringIO()
    await host.download_file("/etc/motd", text_dest)
    assert text_dest.getvalue() == "héllo"
    assert text_dest.tell() == 0

    cp = cli.calls[-1]
    assert cp[2] == "alpine:3.20:/etc/motd"
    assert not Path(cp[3]).exists()  # the temp fetch file was removed


async def test_failed_download_raises_command_error():
    host, _cli = make_host(existing=True)

    with pytest.raises(CommandError) as excinfo:
        await host.download_file("/absent", io.BytesIO())

    err = excinfo.value
    assert err.exit_status == 1
    assert "docker cp /absent ->" in err.command


async def test_failed_upload_raises_command_error():
    host, cli = make_host(existing=True)
    cli.cp_result = _result(1, stderr="permission denied")

    with pytest.raises(CommandError) as excinfo:
        await host.upload_file(io.BytesIO(b"x"), "/etc/motd")

    assert excinfo.value.exit_status == 1
    assert "permission denied" in excinfo.value.stderr


# --- aclose: commit and remove an owned container only --------------------------


async def test_aclose_commits_and_removes_an_owned_container():
    host, cli = make_host(container_id="beefcafe9999")
    await host.connect()

    await host.aclose()

    assert cli.subcommands()[-2:] == [
        ("commit", "beefcafe9999"),
        ("rm", "-f", "beefcafe9999"),
    ]
    assert host.image_id == "0123456789ab"

    # Idempotent: a second close is a no-op.
    calls = len(cli.calls)
    await host.aclose()
    assert len(cli.calls) == calls


async def test_aclose_accepts_a_bare_podman_digest():
    host, cli = make_host({"docker_cmd": "podman"})
    cli.commit_result = _result(stdout="fedcba9876543210\n")
    await host.connect()

    await host.aclose()

    assert host.image_id == "fedcba987654"


async def test_aclose_leaves_an_existing_container_running():
    for running in (True, False):
        host, cli = make_host(existing=True, running=running)
        await host.connect()

        await host.aclose()

        subs = [sub[0] for sub in cli.subcommands()]
        assert "commit" not in subs
        assert "rm" not in subs
        assert host.image_id is None


async def test_aclose_removes_even_when_the_commit_failed():
    host, cli = make_host()
    cli.commit_result = _result(1, stderr="commit exploded")
    await host.connect()

    await host.aclose()

    assert cli.subcommands()[-1] == ("rm", "-f", "c0ffee123456")
    assert host.image_id is None


async def test_aclose_without_connect_is_a_noop():
    host, cli = make_host()

    await host.aclose()

    assert cli.calls == []


# --- End to end through the real drivers ----------------------------------------

PAYLOAD = io.BytesIO(b"docker connector payload\n")
SINK = io.BytesIO()


async def test_run_put_and_get_through_the_docker_transport():
    cli = FakeDockerCli()

    def docker_factory(entry: dict) -> Host:
        host = Host.from_inventory(entry)
        host._run_local = cli.run
        return host

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(files.put, name="Upload motd", src=PAYLOAD, dest="/etc/motd")
        yield op(files.get, name="Fetch motd", src="/etc/motd", dest=SINK)

    inventory = [
        {
            "host": "build1",
            "connection": {
                "connector": "docker",
                "identifier": "alpine:3.20",
                "docker_cmd": "docker",
            },
        }
    ]
    plans, report, hosts = await run(inventory, deploy, host_factory=docker_factory)

    # Both phases ran through the real driver: the plan materialized facts
    # over the exec seam and the apply log shows both transfers.
    assert [planned.changed for planned in plans["build1"]] == [True, True]
    assert report is not None
    assert [entry.changed for entry in report.log] == [True, True]

    # The bytes travelled: cp stored them in the container and get read them
    # back into the local sink.
    assert cli.container_files["/etc/motd"] == PAYLOAD.getvalue()
    assert SINK.getvalue() == PAYLOAD.getvalue()

    # run()'s finally committed the owned container and removed it.
    assert cli.subcommands()[-2:] == [
        ("commit", "c0ffee123456"),
        ("rm", "-f", "c0ffee123456"),
    ]
    assert hosts[0].image_id == "0123456789ab"