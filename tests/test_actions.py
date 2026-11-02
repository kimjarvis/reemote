# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for actions: immediate shell execution outside the deploy model.

Two fakes stand in for the real transport, both overriding the single
``_run_shell`` seam (the same one ``test_commands.py`` and
``test_privilege_errors.py`` override) so nothing opens a socket and asyncssh
is never imported:

- ``ActionHost`` records every shell it is asked to run and returns a canned
  completion record. It also counts ``connect``/``aclose`` calls, so the
  "each gather_action call re-connects" lifecycle is observable.
- ``UnreachableActionHost`` fails ``connect`` with ``HostConnectionError``,
  exercising the fan-out's connection-error aggregation.

Covered: the ``gather_action`` fan-out (host-keyed dict, verbatim forwarding,
non-zero and 127 exits as data, bytes decoding), privilege escalation through
the raw-result path (sudo/su rendering, askpass, ``PrivilegeError`` still
raised and aggregated), the per-call reconnect lifecycle, and the in-deploy
guard -- raised synchronously at the call site in both phases, reset
afterwards, and ``op(Shell, ...)`` rejected by ``materialize``.
"""

from collections.abc import Generator
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from reemote5 import (
    ActionInDeployError,
    Host,
    HostConnectionError,
    OperationError,
    OperationRequest,
    PrivilegeError,
    Result,
    Shell,
    gather_action,
    op,
)
from reemote5.core import FactCache, HostRuntime
from reemote5.drivers import run, run_apply

PASSWORD = "wr0ng-p@ssword"

SUDO_REJECTED = (
    "Sorry, try again.\n"
    "Sorry, try again.\n"
    "Sorry, try again.\n"
    "sudo: 3 incorrect password attempts\n"
)


class ActionHost(Host):
    """A real-transport fake overriding the single ``_run_shell`` seam.

    Records each shell verbatim and answers with a canned completion record.
    ``_run_shell`` goes through ``connect`` exactly as the real seam does, so
    the connect/aclose counters observe the per-call reconnect lifecycle.
    """

    def __init__(
        self,
        name: str,
        *,
        exit_status: int = 0,
        stdout: str = "",
        stderr: str = "",
        privilege: dict | None = None,
    ):
        super().__init__(
            name=name,
            connection={"address": "10.0.0.1"},
            privilege=privilege or {},
        )
        self.exit_status = exit_status
        self.stdout = stdout
        self.stderr = stderr
        self.shells: list[str] = []
        self.connects = 0
        self.closes = 0

    async def connect(self):
        self.connects += 1
        return SimpleNamespace()

    async def _run_shell(self, shell: str):
        await self.connect()
        self.shells.append(shell)
        return SimpleNamespace(
            exit_status=self.exit_status, stdout=self.stdout, stderr=self.stderr
        )

    async def aclose(self) -> None:
        self.closes += 1


class UnreachableActionHost(ActionHost):
    """An ``ActionHost`` whose transport can never be reached."""

    async def connect(self):
        raise HostConnectionError([(self.name, "10.0.0.1", OSError("unreachable"))])


INVENTORY = [
    {"host": "web1", "connection": {"address": "10.0.0.1"}},
    {"host": "web2", "connection": {"address": "10.0.0.2"}},
]


def action_factory(entry: dict) -> ActionHost:
    return ActionHost(entry["host"], stdout=f"hello from {entry['host']}\n")


# --- gather_action fan-out -----------------------------------------------------


async def test_gather_action_returns_a_result_per_host():
    hosts = [ActionHost("web1", stdout="one\n"), ActionHost("web2", stdout="two\n")]

    results = await gather_action(hosts, Shell, command="ls -ltr")

    assert list(results) == ["web1", "web2"]
    assert all(isinstance(r, Result) for r in results.values())
    assert results["web1"].stdout == "one\n"
    assert results["web2"].stdout == "two\n"
    assert all(r.exit_status == 0 and r.ok for r in results.values())


async def test_command_is_forwarded_verbatim():
    host = ActionHost("web1")

    await gather_action([host], Shell, command="cat /etc/hosts | grep web")

    assert host.shells == ["cat /etc/hosts | grep web"]


async def test_nonzero_exit_travels_back_as_data():
    host = ActionHost("web1", exit_status=2, stderr="boom")

    results = await gather_action([host], Shell, command="bespoke-tool --check")

    result = results["web1"]
    assert result.exit_status == 2
    assert result.stderr == "boom"
    assert not result.ok


async def test_exit_127_is_data_not_command_not_found():
    """Unlike a fact read, an action interprets nothing: a missing bespoke
    tool is the command's own outcome, reported in the Result."""
    host = ActionHost("web1", exit_status=127, stderr="bespoke-tool: not found")

    results = await gather_action([host], Shell, command="bespoke-tool")

    assert results["web1"].exit_status == 127
    assert not results["web1"].ok


async def test_bytes_output_is_decoded_and_none_becomes_empty():
    host = ActionHost("web1")
    host.stdout = b"caf\xc3\xa9\n"
    host.stderr = None

    results = await gather_action([host], Shell, command="echo café")

    assert results["web1"].stdout == "café\n"
    assert results["web1"].stderr == ""


def test_result_is_frozen():
    result = Result(exit_status=0, stdout="", stderr="")
    with pytest.raises(FrozenInstanceError):
        result.exit_status = 1


# --- privilege escalation through the raw-result path --------------------------


async def test_sudo_prefixes_the_command():
    host = ActionHost("web1", privilege={"sudo_user": "root"})

    await gather_action([host], Shell, command="cat /etc/shadow", sudo=True)

    # root is sudo's own default target: a bare sudo, no -u root.
    assert host.shells == ["sudo cat /etc/shadow"]


async def test_sudo_user_names_a_non_root_target():
    host = ActionHost("web1", privilege={"sudo_user": "deploy"})

    await gather_action([host], Shell, command="id", sudo=True)

    assert host.shells == ["sudo -u deploy id"]


async def test_su_wraps_the_command():
    host = ActionHost("web1", privilege={"su_user": "deploy"})

    await gather_action([host], Shell, command="id", su=True)

    assert host.shells == ["su deploy -c id"]


async def test_doas_prefixes_the_command():
    host = ActionHost("web1", privilege={"doas_user": "root"})

    await gather_action([host], Shell, command="cat /etc/shadow", doas=True)

    # doas is passwordless and non-interactive; root is its own default target,
    # so a bare ``doas -n`` with no ``-u root``.
    assert host.shells == ["doas -n cat /etc/shadow"]


async def test_doas_user_names_a_non_root_target():
    host = ActionHost("web1", privilege={"doas_user": "deploy"})

    await gather_action([host], Shell, command="id", doas=True)

    assert host.shells == ["doas -n -u deploy id"]


async def test_doas_then_sudo_then_su_is_the_escalation_order():
    host = ActionHost(
        "web1",
        privilege={"doas_user": "root", "sudo_user": "root", "su_user": "deploy"},
    )

    await gather_action([host], Shell, command="id", doas=True, sudo=True, su=True)

    assert host.shells == ["doas -n sudo su deploy -c id"]


async def test_rejected_doas_raises_privilege_error_aggregated():
    """A denied doas rule (passwordless) is a ``PrivilegeError``, aggregated
    across hosts, with the credential-free ``doas -n`` rendering."""
    hosts = [
        ActionHost(
            name,
            exit_status=1,
            stderr="doas: Operation not permitted",
            privilege={"doas_user": "root"},
        )
        for name in ("web1", "web2")
    ]

    with pytest.raises(PrivilegeError) as excinfo:
        await gather_action(hosts, Shell, command="whoami", doas=True)

    err = excinfo.value
    assert err.host_names == ("web1", "web2")
    assert err.failures[0][1] == "doas -n whoami"
    assert all(host.closes == 1 for host in hosts)


async def test_sudo_password_rides_the_askpass_helper():
    host = ActionHost(
        "web1",
        stdout="root\n",
        privilege={"sudo_user": "root", "sudo_password": PASSWORD},
    )
    host._askpass_path = "/tmp/reemote5-askpass-cached"

    results = await gather_action([host], Shell, command="whoami", sudo=True)

    assert results["web1"].stdout == "root\n"
    assert "SUDO_ASKPASS=/tmp/reemote5-askpass-cached" in host.shells[0]
    assert "sudo -A -k" in host.shells[0]
    # The password lives only in the execution string, never in the Result.
    assert PASSWORD not in results["web1"].stdout
    assert PASSWORD not in results["web1"].stderr


async def test_rejected_sudo_raises_privilege_error_aggregated():
    hosts = [
        ActionHost(
            name,
            exit_status=1,
            stderr=SUDO_REJECTED,
            privilege={"sudo_user": "root", "sudo_password": PASSWORD},
        )
        for name in ("web1", "web2")
    ]
    for host in hosts:
        host._askpass_path = "/tmp/reemote5-askpass-cached"

    with pytest.raises(PrivilegeError) as excinfo:
        await gather_action(hosts, Shell, command="whoami", sudo=True)

    err = excinfo.value
    assert err.host_names == ("web1", "web2")
    assert PASSWORD not in str(err)
    # The credential-free rendering, not the askpass string.
    assert err.failures[0][1] == "sudo whoami"
    # Teardown still ran for every host despite the failure.
    assert all(host.closes == 1 for host in hosts)


async def test_command_failure_under_sudo_stays_data():
    """Once sudo authenticated, the bespoke command's own failure is the
    caller's to interpret -- a Result, not a CommandError."""
    host = ActionHost(
        "web1",
        exit_status=1,
        stderr="bespoke-tool: licence expired",
        privilege={"sudo_user": "root"},
    )

    results = await gather_action([host], Shell, command="bespoke-tool", sudo=True)

    assert results["web1"].exit_status == 1
    assert "licence expired" in results["web1"].stderr


# --- connection errors and the per-call reconnect lifecycle --------------------


async def test_unreachable_hosts_are_aggregated():
    hosts = [UnreachableActionHost("web1"), UnreachableActionHost("web2")]

    with pytest.raises(HostConnectionError) as excinfo:
        await gather_action(hosts, Shell, command="ls")

    assert "web1" in str(excinfo.value)
    assert "web2" in str(excinfo.value)
    assert all(host.closes == 1 for host in hosts)


async def test_unexpected_errors_propagate_unchanged():
    class BrokenHost(ActionHost):
        async def _run_shell(self, shell: str):
            raise ValueError("bug")

    host = BrokenHost("web1")

    with pytest.raises(ValueError):
        await gather_action([host], Shell, command="ls")

    assert host.closes == 1


async def test_each_call_reconnects():
    hosts = [ActionHost("web1"), ActionHost("web2")]

    await gather_action(hosts, Shell, command="first")
    await gather_action(hosts, Shell, command="second")

    for host in hosts:
        assert host.connects == 2
        assert host.closes == 2
        assert host.shells == ["first", "second"]


# --- the in-deploy guard --------------------------------------------------------


def deploy_calls_action(host: Host) -> Generator[OperationRequest]:
    """A deploy that misuses an action: the call is not (and cannot be)
    awaited inside a sync generator -- the guard must fire at call time."""
    gather_action([host], Shell, command="ls")
    yield from ()


def empty_deploy(host: Host) -> Generator[OperationRequest]:
    yield from ()


async def test_action_in_deploy_raises_immediately_in_prepare():
    with pytest.raises(ActionInDeployError) as excinfo:
        await run(INVENTORY, deploy_calls_action, host_factory=action_factory, dry=True)

    assert "Shell" in str(excinfo.value)
    assert "before, between or after" in str(excinfo.value)


async def test_action_in_deploy_raises_in_apply_too():
    host = ActionHost("web1")
    runtime = HostRuntime(
        host, deploy_calls_action(host), FactCache(host, phase="apply")
    )

    with pytest.raises(ActionInDeployError):
        await run_apply([runtime])


async def test_guard_resets_after_a_successful_run():
    _plans, _report, hosts = await run(
        INVENTORY, empty_deploy, host_factory=action_factory
    )

    results = await gather_action(hosts, Shell, command="ls")

    assert set(results) == {"web1", "web2"}


async def test_guard_resets_after_a_guarded_run():
    with pytest.raises(ActionInDeployError):
        await run(INVENTORY, deploy_calls_action, host_factory=action_factory, dry=True)

    # The ContextVar was reset even though the phase raised.
    results = await gather_action([ActionHost("web1")], Shell, command="ls")

    assert results["web1"].ok


async def test_op_shell_is_rejected_by_materialize():
    """The other misuse path: wrapping an action class in op(). It is not an
    async-generator operation, so materialize refuses it explicitly."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(Shell, command="ls")

    with pytest.raises(OperationError) as excinfo:
        await run(INVENTORY, deploy, host_factory=action_factory, dry=True)

    assert "async-generator operation" in str(excinfo.value)
