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
non-zero and 127 exits as data, bytes decoding), the ``env`` prefix (shell-
quoted ``env K=V ...`` rendering, special characters, combination with
privilege escalation and ``Script``, real local-sh execution), privilege
escalation through the raw-result path (sudo/su rendering, askpass,
``PrivilegeError`` still raised and aggregated), the per-call reconnect
lifecycle, the in-deploy guard -- raised synchronously at the call site in
both phases, reset afterwards, and ``op(Shell, ...)`` rejected by
``materialize`` -- the ``group``/``tags`` host selection, ``WaitForConnection``
(retry until reachable, the fatal aggregated ``ConnectionTimeoutError``,
stale-connection discard and the attempt bound), and ``Script`` /
``server.script`` (the wrapper rendering, the missing-file error Result, and
real local-sh execution of the executable/non-executable/interpreter branches).
"""

import asyncio
import shlex
import sys
from collections.abc import Generator
from types import SimpleNamespace

import pytest

from reemote import (
    ActionInDeployError,
    ConnectionTimeoutError,
    Host,
    HostConnectionError,
    OperationError,
    OperationRequest,
    PrivilegeError,
    Result,
    Script,
    Shell,
    WaitForConnection,
    gather_action,
    op,
    server,
)
from reemote.core import FactCache, HostRuntime
from reemote.drivers import run, run_apply

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
    assert all(
        isinstance(r, dict) and set(r) == {"exit_status", "stdout", "stderr"}
        for r in results.values()
    )
    assert results["web1"]["stdout"] == "one\n"
    assert results["web2"]["stdout"] == "two\n"
    assert all(r["exit_status"] == 0 for r in results.values())


async def test_command_is_forwarded_verbatim():
    host = ActionHost("web1")

    await gather_action([host], Shell, command="cat /etc/hosts | grep web")

    assert host.shells == ["cat /etc/hosts | grep web"]


async def test_nonzero_exit_travels_back_as_data():
    host = ActionHost("web1", exit_status=2, stderr="boom")

    results = await gather_action([host], Shell, command="bespoke-tool --check")

    result = results["web1"]
    assert result["exit_status"] == 2
    assert result["stderr"] == "boom"


async def test_exit_127_is_data_not_command_not_found():
    """Unlike a fact read, an action interprets nothing: a missing bespoke
    tool is the command's own outcome, reported in the Result."""
    host = ActionHost("web1", exit_status=127, stderr="bespoke-tool: not found")

    results = await gather_action([host], Shell, command="bespoke-tool")

    assert results["web1"]["exit_status"] == 127


async def test_bytes_output_is_decoded_and_none_becomes_empty():
    host = ActionHost("web1")
    host.stdout = b"caf\xc3\xa9\n"
    host.stderr = None

    results = await gather_action([host], Shell, command="echo café")

    assert results["web1"]["stdout"] == "café\n"
    assert results["web1"]["stderr"] == ""


def test_result_is_a_plain_typed_dict():
    result = Result(exit_status=0, stdout="", stderr="")
    assert type(result) is dict
    assert result == {"exit_status": 0, "stdout": "", "stderr": ""}


# --- env prefix -----------------------------------------------------------------


async def test_env_renders_a_shell_quoted_prefix():
    host = ActionHost("web1")

    await gather_action([host], Shell, command="printenv FOO", env={"FOO": "bar"})

    assert host.shells == ["env FOO=bar printenv FOO"]


async def test_env_renders_multiple_variables():
    host = ActionHost("web1")

    await gather_action(
        [host], Shell, command="env", env={"FOO": "bar", "BAZ": "qux"}
    )

    assert host.shells == ["env FOO=bar BAZ=qux env"]


async def test_env_quotes_keys_and_values_with_special_characters():
    host = ActionHost("web1")

    await gather_action(
        [host], Shell, command="echo", env={"MY VAR": "it's a test"}
    )

    shell = host.shells[0]
    # shlex.quote wraps each key and value; the apostrophe becomes '"'"'.
    expected_key = shlex.quote("MY VAR")
    expected_value = shlex.quote("it's a test")
    assert shell == f"env {expected_key}={expected_value} echo"


async def test_env_none_leaves_the_command_unchanged():
    host = ActionHost("web1")

    await gather_action([host], Shell, command="ls", env=None)

    assert host.shells == ["ls"]


async def test_env_empty_mapping_leaves_the_command_unchanged():
    host = ActionHost("web1")

    await gather_action([host], Shell, command="ls", env={})

    assert host.shells == ["ls"]


async def test_env_with_sudo_prefixes_before_the_command():
    host = ActionHost("web1", privilege={"sudo_user": "root"})

    await gather_action(
        [host], Shell, command="cat /etc/shadow", env={"FOO": "bar"}, sudo=True
    )

    assert host.shells == ["sudo env FOO=bar cat /etc/shadow"]


async def test_env_with_doas_prefixes_before_the_command():
    host = ActionHost("web1", privilege={"doas_user": "root"})

    await gather_action(
        [host], Shell, command="id", env={"FOO": "bar"}, doas=True
    )

    assert host.shells == ["doas -n env FOO=bar id"]


async def test_env_with_script_prefixes_the_sh_c_carrier():
    host = ActionHost("web1")

    await gather_action([host], Script, path="/tmp/setup.sh", env={"FOO": "bar"})

    shell = host.shells[0]
    assert shell.startswith("env FOO=bar sh -c ")
    assert "if [ ! -f /tmp/setup.sh ]" in shell


async def test_env_with_script_and_sudo():
    host = ActionHost("web1", privilege={"sudo_user": "root"})

    await gather_action(
        [host], Script, path="/tmp/setup.sh", env={"FOO": "bar"}, sudo=True
    )

    shell = host.shells[0]
    assert shell.startswith("sudo env FOO=bar sh -c ")


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
    host._askpass_path = "/tmp/reemote-askpass-cached"

    results = await gather_action([host], Shell, command="whoami", sudo=True)

    assert results["web1"]["stdout"] == "root\n"
    assert "SUDO_ASKPASS=/tmp/reemote-askpass-cached" in host.shells[0]
    assert "sudo -A -k" in host.shells[0]
    # The password lives only in the execution string, never in the Result.
    assert PASSWORD not in results["web1"]["stdout"]
    assert PASSWORD not in results["web1"]["stderr"]


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
        host._askpass_path = "/tmp/reemote-askpass-cached"

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

    assert results["web1"]["exit_status"] == 1
    assert "licence expired" in results["web1"]["stderr"]


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

    assert results["web1"]["exit_status"] == 0


async def test_op_shell_is_rejected_by_materialize():
    """The other misuse path: wrapping an action class in op(). It is not an
    async-generator operation, so materialize refuses it explicitly."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(Shell, command="ls")

    with pytest.raises(OperationError) as excinfo:
        await run(INVENTORY, deploy, host_factory=action_factory, dry=True)

    assert "async-generator operation" in str(excinfo.value)


# --- WaitForConnection ----------------------------------------------------------


class RebootingHost(ActionHost):
    """A host that refuses ``connect`` ``failures`` times, then comes back.

    Models a rebooting machine: the first attempts land while sshd is down,
    a later one succeeds. ``attempts`` counts every connect try.
    """

    def __init__(self, name: str, *, failures: int = 2):
        super().__init__(name)
        self.failures = failures
        self.attempts = 0

    async def connect(self):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise HostConnectionError([(self.name, "10.0.0.1", OSError("rebooting"))])
        return SimpleNamespace()


class HangingHost(ActionHost):
    """A host whose connect attempt never completes on its own."""

    async def connect(self):
        await asyncio.sleep(30)


class CachingHost(ActionHost):
    """Mimics ``Host.connect``'s cache so a stale connection is observable.

    Like the real transport, a cached ``_conn`` is handed back without any
    network attempt, and ``aclose`` clears it.
    """

    async def connect(self):
        conn = getattr(self, "_conn", None)
        if conn is not None:
            return conn
        self.connects += 1
        self._conn = SimpleNamespace(label="fresh")
        return self._conn

    async def aclose(self) -> None:
        self.closes += 1
        self._conn = None


async def test_wait_returns_none_per_host_on_first_success():
    hosts = [ActionHost("web1"), ActionHost("web2")]

    results = await gather_action(hosts, WaitForConnection, timeout=1, retry=0.01)

    # No command runs: the key's presence is the success signal.
    assert results == {"web1": None, "web2": None}
    assert all(host.connects == 1 for host in hosts)
    assert all(host.shells == [] for host in hosts)
    assert all(host.closes >= 1 for host in hosts)


async def test_wait_retries_until_the_host_comes_back():
    host = RebootingHost("web1", failures=2)

    results = await gather_action([host], WaitForConnection, timeout=5, retry=0.01)

    assert results == {"web1": None}
    assert host.attempts == 3


async def test_wait_timeout_raises_connection_timeout_error():
    host = UnreachableActionHost("web1")

    with pytest.raises(ConnectionTimeoutError) as excinfo:
        await gather_action([host], WaitForConnection, timeout=0.05, retry=0.01)

    err = excinfo.value
    assert err.host_names == ("web1",)
    assert "0.05" in str(err)
    # The last connection failure stays reachable for diagnosis.
    assert isinstance(err.__cause__, HostConnectionError)
    # Teardown still ran despite the failure.
    assert host.closes >= 1


async def test_wait_timeouts_are_aggregated_across_hosts():
    hosts = [UnreachableActionHost("web1"), UnreachableActionHost("web2")]

    with pytest.raises(ConnectionTimeoutError) as excinfo:
        await gather_action(hosts, WaitForConnection, timeout=0.05, retry=0.01)

    assert excinfo.value.host_names == ("web1", "web2")
    assert all(host.closes >= 1 for host in hosts)


async def test_wait_only_the_timed_out_host_is_named():
    hosts = [RebootingHost("web1", failures=0), UnreachableActionHost("web2")]

    with pytest.raises(ConnectionTimeoutError) as excinfo:
        await gather_action(hosts, WaitForConnection, timeout=0.05, retry=0.01)

    assert excinfo.value.host_names == ("web2",)


async def test_wait_bounds_a_hanging_attempt_by_the_timeout():
    """A handshake that never completes must not outlive the overall timeout."""
    host = HangingHost("web1")
    loop = asyncio.get_running_loop()
    started = loop.time()

    with pytest.raises(ConnectionTimeoutError):
        await gather_action([host], WaitForConnection, timeout=0.05, retry=0.01)

    assert loop.time() - started < 5


async def test_wait_discards_a_stale_cached_connection():
    """A pre-reboot cached connection must not satisfy the wait: connect would
    hand it back without touching the network, so the wait closes it first."""
    host = CachingHost("web1")
    stale = SimpleNamespace(label="stale")
    host._conn = stale

    results = await gather_action([host], WaitForConnection, timeout=1, retry=0.01)

    assert results == {"web1": None}
    assert host.connects == 1  # a real attempt happened despite the cache
    assert host._conn is None  # the fan-out's teardown closed the fresh one


async def test_wait_rejects_non_positive_timeout_and_retry():
    host = ActionHost("web1")

    with pytest.raises(ValueError, match="timeout"):
        await gather_action([host], WaitForConnection, timeout=0)
    with pytest.raises(ValueError, match="retry"):
        await gather_action([host], WaitForConnection, retry=0)


async def test_wait_in_deploy_raises_immediately():
    def deploy(host: Host) -> Generator[OperationRequest]:
        gather_action([host], WaitForConnection, timeout=1)
        yield from ()

    with pytest.raises(ActionInDeployError) as excinfo:
        await run(INVENTORY, deploy, host_factory=action_factory, dry=True)

    assert "WaitForConnection" in str(excinfo.value)


# --- group/tags host selection ---------------------------------------------------


def classified_hosts() -> list[ActionHost]:
    web1 = ActionHost("web1")
    web1.group = "webservers"
    web1.tags = ["web", "ubuntu"]
    web2 = ActionHost("web2")
    web2.group = "webservers"
    web2.tags = ["web"]
    db1 = ActionHost("db1")
    db1.group = "databases"
    db1.tags = ["db", "ubuntu"]
    return [web1, web2, db1]


async def test_group_filter_selects_only_matching_hosts():
    hosts = classified_hosts()

    results = await gather_action(
        hosts, WaitForConnection, timeout=1, retry=0.01, group="webservers"
    )

    assert list(results) == ["web1", "web2"]
    assert hosts[2].connects == 0  # db1 was never attempted


async def test_tags_filter_requires_every_listed_tag():
    hosts = classified_hosts()

    results = await gather_action(
        hosts, WaitForConnection, timeout=1, retry=0.01, tags=("web", "ubuntu")
    )

    assert list(results) == ["web1"]
    assert hosts[1].connects == 0
    assert hosts[2].connects == 0


async def test_group_and_tags_combine():
    hosts = classified_hosts()

    results = await gather_action(
        hosts,
        WaitForConnection,
        timeout=1,
        retry=0.01,
        group="webservers",
        tags=("ubuntu",),
    )

    assert list(results) == ["web1"]


async def test_empty_selection_runs_nothing():
    hosts = classified_hosts()

    results = await gather_action(
        hosts, WaitForConnection, timeout=1, retry=0.01, group="nosuchgroup"
    )

    assert results == {}
    assert all(host.connects == 0 for host in hosts)


async def test_filters_apply_to_shell_too():
    hosts = classified_hosts()

    results = await gather_action(hosts, Shell, command="id", group="databases")

    assert list(results) == ["db1"]
    assert hosts[0].shells == []
    assert hosts[1].shells == []
    assert hosts[2].shells == ["id"]


async def test_filters_are_not_forwarded_to_process():
    """group/tags are consumed by the fan-out: Shell.process never sees them."""
    host = ActionHost("web1")
    host.group = "webservers"

    results = await gather_action(
        [host], Shell, command="id", group="webservers", tags=()
    )

    assert results["web1"]["exit_status"] == 0
    assert host.shells == ["id"]


# --- Script (server.script) -------------------------------------------------------


def test_server_script_is_the_script_action():
    """server.script is a namespace alias, not a second implementation."""
    assert server.script is Script


async def test_script_renders_the_check_and_executable_branch():
    host = ActionHost("web1")

    await gather_action([host], Script, path="/tmp/setup.sh")

    wrapper = (
        "if [ ! -f /tmp/setup.sh ]; then "
        "echo 'server.script: no such file: /tmp/setup.sh' >&2; exit 127; fi; "
        "if [ -x /tmp/setup.sh ]; then /tmp/setup.sh; "
        "else sh /tmp/setup.sh; fi"
    )
    # The compound wrapper always rides an explicit sh -c so sudo/doas,
    # which exec directly rather than through a shell, can carry it.
    assert host.shells == [f"sh -c {shlex.quote(wrapper)}"]


async def test_script_quotes_the_path_and_each_argument():
    host = ActionHost("web1")

    await gather_action([host], Script, path="/tmp/a b.sh", args=("x y", "it's"))

    shell = host.shells[0]
    assert shlex.quote("/tmp/a b.sh") in shell
    assert "'x y'" in shell
    assert shell.count("'x y'") == 2  # once per branch of the -x test


async def test_script_interpreter_replaces_the_executable_branch():
    host = ActionHost("web1")

    await gather_action(
        [host], Script, path="/tmp/s.py", args=("--flag",), interpreter="python3 -u"
    )

    wrapper = (
        "if [ ! -f /tmp/s.py ]; then "
        "echo 'server.script: no such file: /tmp/s.py' >&2; exit 127; fi; "
        "python3 -u /tmp/s.py --flag"
    )
    assert host.shells == [f"sh -c {shlex.quote(wrapper)}"]
    assert "[ -x" not in host.shells[0]


async def test_script_sudo_wraps_the_sh_c_carrier():
    host = ActionHost("web1", privilege={"sudo_user": "root"})

    await gather_action([host], Script, path="/tmp/setup.sh", sudo=True)

    assert host.shells[0].startswith("sudo sh -c ")


async def test_script_rejects_a_single_string_args():
    host = ActionHost("web1")

    with pytest.raises(TypeError, match="iterable of strings"):
        await gather_action([host], Script, path="/tmp/s.sh", args="one two")


async def test_script_rejects_an_empty_interpreter():
    host = ActionHost("web1")

    with pytest.raises(ValueError, match="interpreter"):
        await gather_action([host], Script, path="/tmp/s.sh", interpreter="  ")


async def test_script_in_deploy_raises_immediately():
    def deploy(host: Host) -> Generator[OperationRequest]:
        gather_action([host], server.script, path="/tmp/s.sh")
        yield from ()

    with pytest.raises(ActionInDeployError) as excinfo:
        await run(INVENTORY, deploy, host_factory=action_factory, dry=True)

    assert "Script" in str(excinfo.value)


class LocalShellHost(ActionHost):
    """An ``ActionHost`` that really executes each shell with the local sh.

    Still offline -- no socket, no asyncssh -- but the wrapper's existence
    check, executable-bit branch and interpreter override run for real, so
    the ``Result`` reflects genuine shell behaviour.
    """

    async def _run_shell(self, shell: str):
        await self.connect()
        self.shells.append(shell)
        proc = await asyncio.create_subprocess_shell(
            shell,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return SimpleNamespace(
            exit_status=proc.returncode,
            stdout=stdout.decode(),
            stderr=stderr.decode(),
        )


async def test_script_missing_file_is_an_error_result_not_an_exception(tmp_path):
    missing = tmp_path / "absent.sh"
    host = LocalShellHost("web1")

    results = await gather_action([host], server.script, path=str(missing))

    result = results["web1"]
    assert result["exit_status"] == 127
    assert f"server.script: no such file: {missing}" in result["stderr"]
    assert result["stdout"] == ""


async def test_script_directory_is_reported_as_missing(tmp_path):
    host = LocalShellHost("web1")

    results = await gather_action([host], Script, path=str(tmp_path))

    assert results["web1"]["exit_status"] == 127


async def test_script_non_executable_file_runs_through_sh(tmp_path):
    script = tmp_path / "plain.sh"
    script.write_text('echo "hello $1"\n', encoding="utf-8")
    script.chmod(0o644)  # readable, NOT executable
    host = LocalShellHost("web1")

    results = await gather_action([host], Script, path=str(script), args=("world",))

    assert results["web1"]["exit_status"] == 0
    assert results["web1"]["stdout"] == "hello world\n"


async def test_script_executable_file_runs_directly_via_its_shebang(tmp_path):
    # A Python shebang proves DIRECT execution: the sh fallback could not
    # interpret the Python body, so exit 0 with this stdout means the
    # kernel honoured the shebang on the executable file.
    script = tmp_path / "direct.py"
    script.write_text(f"#!{sys.executable}\nprint('direct')\n", encoding="utf-8")
    script.chmod(0o755)
    host = LocalShellHost("web1")

    results = await gather_action([host], Script, path=str(script))

    assert results["web1"]["exit_status"] == 0
    assert results["web1"]["stdout"] == "direct\n"


async def test_script_explicit_interpreter_ignores_the_executable_bit(tmp_path):
    script = tmp_path / "interp.py"
    script.write_text("import sys\nprint(' '.join(sys.argv[1:]))\n", encoding="utf-8")
    script.chmod(0o644)  # no executable bit, and no shebang either
    host = LocalShellHost("web1")

    results = await gather_action(
        [host],
        Script,
        path=str(script),
        args=("a b", "c"),
        interpreter=sys.executable,
    )

    assert results["web1"]["exit_status"] == 0
    assert results["web1"]["stdout"] == "a b c\n"


async def test_script_own_failure_travels_back_as_data(tmp_path):
    script = tmp_path / "failing.sh"
    script.write_text("echo boom >&2\nexit 3\n", encoding="utf-8")
    script.chmod(0o644)
    host = LocalShellHost("web1")

    results = await gather_action([host], Script, path=str(script))

    assert results["web1"]["exit_status"] == 3
    assert results["web1"]["stderr"] == "boom\n"


async def test_env_reaches_the_script_via_real_execution(tmp_path):
    """The env prefix really sets the variable for the script's process."""
    script = tmp_path / "printenv.sh"
    script.write_text('echo "$MY_VAR"\n', encoding="utf-8")
    script.chmod(0o644)
    host = LocalShellHost("web1")

    results = await gather_action(
        [host], Script, path=str(script), env={"MY_VAR": "hello world"}
    )

    assert results["web1"]["exit_status"] == 0
    assert results["web1"]["stdout"] == "hello world\n"


async def test_env_reaches_a_shell_command_via_real_execution():
    """The env prefix really sets the variable for a Shell command."""
    host = LocalShellHost("web1")

    # printenv reads the environment directly, without shell expansion, so
    # the env prefix's variable is visible to it.
    results = await gather_action(
        [host], Shell, command="printenv GREETING", env={"GREETING": "hi there"}
    )

    assert results["web1"]["exit_status"] == 0
    assert results["web1"]["stdout"] == "hi there\n"
