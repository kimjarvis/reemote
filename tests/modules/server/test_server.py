"""Tests for the server module: systemd facts, ``service`` and ``wait_service``.

Covers ``reemote5.modules.server.server``: the ``SystemdServiceState`` and
``SystemdServiceEnabled`` facts (over the real-transport path, parsing canned
``systemctl show`` output, and through the ``SimulatedHost`` seams), the
``service`` operation's option set and its ``SystemctlCommand`` descriptors,
and the waiting design: the ``WaitCommand`` base, the generic
``WaitForFactCommand`` poll loop (already met, met after several polls, and
timeout -- all without real sleeping), the ``send_command`` dispatch onto
``wait``, and the end-to-end ``wait_service`` behaviour through ``run`` with
``SimulatedHost``: the wait is planned unconditionally so the request
sequence is identical in both phases (determinism), it passes when the unit
is active and aborts the run with ``WaitTimeoutError`` when it is not.
"""

from collections.abc import Generator
from dataclasses import dataclass

import pytest

from reemote5.commands import WaitCommand, WaitTimeoutError
from reemote5.core import FactCache, OpContext, OperationRequest, op
from reemote5.drivers import (
    command_shell,
    format_log,
    format_plan,
    run,
    run_apply,
    run_prepare,
    send_command,
)
from reemote5.hosts import Host
from reemote5.modules import server
from reemote5.modules.server.server import (
    SystemctlCommand,
    SystemdServiceEnabled,
    SystemdServiceState,
    WaitForFactCommand,
    is_active,
    unit_name,
)
from tests.simulated_host import SimulatedHost

INVENTORY = [
    {"host": "web1", "connection": {"key_path": "~/.ssh/id_ed25519"}},
    {"host": "web2", "connection": {"key_path": "~/.ssh/id_ed25519"}},
]


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell`` instead of connecting."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout
        self.shells: list[str] = []

    async def run_shell(self, shell: str, *, check: bool = False) -> str:
        self.shells.append(shell)
        return self._stdout


async def materialize(host: Host, request: OperationRequest):
    """Run an operation against a fresh cache; return ``(ctx, commands)``."""
    ctx = OpContext(host=host, facts=FactCache(host, phase="test"))
    commands = [cmd async for cmd in request.func(ctx, **request.kwargs)]
    return ctx, commands


# --- Unit names and facts ----------------------------------------------------


def test_unit_name_appends_service_to_a_bare_name():
    assert unit_name("httpd") == "httpd.service"


def test_unit_name_keeps_a_known_suffix():
    assert unit_name("httpd.service") == "httpd.service"
    assert unit_name("logrotate.timer") == "logrotate.timer"


async def test_systemd_service_state_parses_systemctl_show():
    host = CannedShellHost("active\n")
    assert await SystemdServiceState.process(host, "httpd") == "active"
    assert host.shells == ["systemctl show -p ActiveState --value httpd.service"]


async def test_systemd_service_state_is_none_on_empty_output():
    assert await SystemdServiceState.process(CannedShellHost(""), "httpd") is None


async def test_systemd_service_state_reads_the_simulated_seam():
    host = SimulatedHost(name="h", services={"httpd.service": "activating"})
    assert await SystemdServiceState.process(host, "httpd") == "activating"
    assert await SystemdServiceState.process(host, "nginx") is None


async def test_systemd_service_enabled_parses_unit_file_state():
    assert await SystemdServiceEnabled.process(CannedShellHost("enabled\n"), "httpd")
    assert await SystemdServiceEnabled.process(CannedShellHost("static\n"), "httpd")
    assert not await SystemdServiceEnabled.process(
        CannedShellHost("disabled\n"), "httpd"
    )
    assert not await SystemdServiceEnabled.process(CannedShellHost(""), "httpd")


async def test_systemd_service_enabled_reads_the_simulated_seam():
    host = SimulatedHost(name="h", enabled_services={"httpd.service"})
    assert await SystemdServiceEnabled.process(host, "httpd")
    assert not await SystemdServiceEnabled.process(host, "nginx")


# --- server.service ----------------------------------------------------------


def test_systemctl_command_shell_rendering():
    assert SystemctlCommand(action="daemon-reload").shell() == "systemctl daemon-reload"
    assert (
        SystemctlCommand(action="start", unit="httpd.service").shell()
        == "systemctl start httpd.service"
    )
    assert command_shell(
        SystemctlCommand(action="enable", unit="httpd.service"), sudo=True
    ) == ("sudo systemctl enable httpd.service")


async def test_service_starts_an_inactive_unit():
    host = SimulatedHost(name="h", services={"httpd.service": "inactive"})
    ctx, commands = await materialize(host, op(server.service, service="httpd"))
    assert ctx.changed
    assert "will start" in ctx.reason
    assert commands == [SystemctlCommand(action="start", unit="httpd.service")]


async def test_service_treats_activating_as_not_running():
    host = SimulatedHost(name="h", services={"httpd.service": "activating"})
    _ctx, commands = await materialize(host, op(server.service, service="httpd"))
    assert commands == [SystemctlCommand(action="start", unit="httpd.service")]


async def test_service_no_change_when_already_running():
    host = SimulatedHost(name="h", services={"httpd.service": "active"})
    ctx, commands = await materialize(host, op(server.service, service="httpd"))
    assert not ctx.changed
    assert ctx.reason == "httpd.service already running"
    assert commands == []


async def test_service_stops_a_running_unit():
    host = SimulatedHost(name="h", services={"httpd.service": "active"})
    ctx, commands = await materialize(
        host, op(server.service, service="httpd", running=False)
    )
    assert ctx.changed
    assert commands == [SystemctlCommand(action="stop", unit="httpd.service")]


async def test_service_restart_only_when_running():
    running = SimulatedHost(name="h", services={"httpd.service": "active"})
    ctx, commands = await materialize(
        running, op(server.service, service="httpd", restarted=True)
    )
    assert ctx.changed
    assert commands == [SystemctlCommand(action="restart", unit="httpd.service")]

    stopped = SimulatedHost(name="h", services={"httpd.service": "inactive"})
    _, commands = await materialize(
        stopped, op(server.service, service="httpd", restarted=True)
    )
    # A stopped unit is started, not restarted (pyinfra's handle_service_control).
    assert commands == [SystemctlCommand(action="start", unit="httpd.service")]


async def test_service_enables_and_disables_on_boot():
    host = SimulatedHost(name="h", services={"httpd.service": "active"})
    ctx, commands = await materialize(
        host, op(server.service, service="httpd", enabled=True)
    )
    assert ctx.changed
    assert commands == [SystemctlCommand(action="enable", unit="httpd.service")]

    host.enabled_services.add("httpd.service")
    ctx, commands = await materialize(
        host, op(server.service, service="httpd", enabled=True)
    )
    assert not ctx.changed
    assert commands == []

    ctx, commands = await materialize(
        host, op(server.service, service="httpd", enabled=False)
    )
    assert commands == [SystemctlCommand(action="disable", unit="httpd.service")]


async def test_service_enabled_none_leaves_boot_setting_alone():
    host = SimulatedHost(name="h", services={"httpd.service": "active"})
    ctx, commands = await materialize(host, op(server.service, service="httpd"))
    assert commands == []
    assert "enabled" not in ctx.reason


async def test_service_daemon_reload_always_yields_first():
    host = SimulatedHost(
        name="h", services={"httpd.service": "inactive"}, enabled_services=set()
    )
    ctx, commands = await materialize(
        host,
        op(server.service, service="httpd", enabled=True, daemon_reload=True),
    )
    assert ctx.changed
    assert commands == [
        SystemctlCommand(action="daemon-reload"),
        SystemctlCommand(action="start", unit="httpd.service"),
        SystemctlCommand(action="enable", unit="httpd.service"),
    ]


async def test_service_commands_mutate_the_simulated_host():
    host = SimulatedHost(name="h", services={"httpd.service": "inactive"})
    for cmd in (
        SystemctlCommand(action="start", unit="httpd.service"),
        SystemctlCommand(action="enable", unit="httpd.service"),
    ):
        await send_command(host, cmd)
    assert host.services == {"httpd.service": "active"}
    assert host.enabled_services == {"httpd.service"}
    await send_command(host, SystemctlCommand(action="stop", unit="httpd.service"))
    await send_command(host, SystemctlCommand(action="disable", unit="httpd.service"))
    assert host.services == {"httpd.service": "inactive"}
    assert host.enabled_services == set()


# --- WaitCommand / WaitForFactCommand ----------------------------------------


class FlippingHost(Host):
    """A host whose simulated unit becomes active after ``flip_after`` reads.

    Drives the real poll loop without a real transport: ``SystemdServiceState``
    reads the ``services`` seam, and each read is counted so the test can
    assert how many polls the loop made.
    """

    def __init__(self, flip_after: int):
        super().__init__(name="flipping")
        self.flip_after = flip_after
        self.reads = 0

    @property
    def services(self) -> dict[str, str]:
        self.reads += 1
        state = "active" if self.reads >= self.flip_after else "activating"
        return {"httpd.service": state}


def wait_cmd(timeout: float = 0.0, interval: float = 0.0) -> WaitForFactCommand:
    return WaitForFactCommand(
        fact_cls=SystemdServiceState,
        args=("httpd.service",),
        predicate=is_active,
        timeout=timeout,
        interval=interval,
    )


async def test_wait_command_base_is_abstract():
    with pytest.raises(NotImplementedError, match="wait()"):
        await WaitCommand().wait(Host(name="h"))


def test_wait_for_fact_command_renders_without_shell():
    cmd = wait_cmd(timeout=60.0, interval=2.0)
    assert not hasattr(cmd, "shell")
    assert (
        command_shell(cmd)
        == "wait for SystemdServiceState('httpd.service') <is_active> every 2.0s up to 60.0s"
    )


def test_wait_for_fact_command_is_hashable_and_value_equal():
    assert wait_cmd() == wait_cmd()
    assert hash(wait_cmd()) == hash(wait_cmd())


async def test_wait_returns_on_the_first_poll_when_already_met():
    host = FlippingHost(flip_after=1)
    assert await wait_cmd().wait(host) == "condition met after 1 poll(s)"
    assert host.reads == 1


async def test_wait_polls_until_the_condition_is_met():
    # A generous deadline with a zero interval: the loop yields to the event
    # loop between polls but never actually sleeps.
    host = FlippingHost(flip_after=3)
    assert await wait_cmd(timeout=60.0).wait(host) == "condition met after 3 poll(s)"
    assert host.reads == 3


async def test_wait_times_out_with_the_last_value():
    # A zero timeout still polls once (an already-met condition would pass),
    # then the deadline has passed and the loop raises instead of sleeping.
    host = FlippingHost(flip_after=99)
    with pytest.raises(WaitTimeoutError, match=r"not met within 0.0s.*'activating'"):
        await wait_cmd().wait(host)
    assert host.reads == 1


async def test_wait_bypasses_the_fact_cache():
    # Reading through a FactCache would pin the first value; the poll loop
    # must observe the flip even though a cache holding 'activating' exists.
    host = FlippingHost(flip_after=2)
    cache = FactCache(host, phase="apply")
    assert await cache.get(SystemdServiceState, "httpd.service") == "activating"
    await wait_cmd(timeout=60.0).wait(host)
    assert await cache.get(SystemdServiceState, "httpd.service") == "activating"


@dataclass(frozen=True)
class RecordingWait(WaitCommand):
    """A WaitCommand recording the host ``send_command`` hands it."""

    calls: list = None  # type: ignore[assignment]

    async def wait(self, host: Host) -> str:
        self.calls.append(host)
        return "recorded"


async def test_send_command_dispatches_wait_commands_on_the_real_path():
    # A plain Host has no execute_command seam, so send_command takes the
    # real-transport branch; a WaitCommand must reach ``wait`` and never a
    # shell, even when the request is flagged sudo/su.
    host = Host(name="real")
    calls: list = []
    await send_command(host, RecordingWait(calls=calls), sudo=True, su=True)
    assert calls == [host]


async def test_simulated_host_evaluates_a_wait_once():
    met = SimulatedHost(name="h", services={"httpd.service": "active"})
    await send_command(met, wait_cmd(timeout=60.0))

    unmet = SimulatedHost(name="h", services={"httpd.service": "inactive"})
    with pytest.raises(WaitTimeoutError, match="'inactive'"):
        await send_command(unmet, wait_cmd(timeout=60.0))


async def test_simulated_host_rejects_a_wait_on_an_unmodelled_fact():
    host = SimulatedHost(name="h")
    with pytest.raises(TypeError, match="unsupported simulated wait fact"):
        await send_command(
            host, WaitForFactCommand(fact_cls=SystemdServiceEnabled, args=("x",))
        )


# --- server.wait_service end to end ------------------------------------------


async def test_wait_service_always_yields_and_reads_no_facts():
    host = SimulatedHost(name="h", services={"httpd.service": "active"})
    cache = FactCache(host, phase="test")
    ctx = OpContext(host=host, facts=cache)
    request = op(server.wait_service, service="httpd", timeout=60.0, interval=2.0)
    commands = [cmd async for cmd in request.func(ctx, **request.kwargs)]
    assert ctx.changed
    assert ctx.reason == "wait for httpd.service to be active"
    assert commands == [wait_cmd(timeout=60.0, interval=2.0)]
    assert cache._data == {}


def start_and_wait(host: Host) -> Generator[OperationRequest]:
    """The example deploy: start httpd (elevated), then wait for it."""
    yield op(
        server.service,
        name="Start httpd",
        service="httpd",
        running=True,
        enabled=True,
        sudo=True,
    )
    yield op(
        server.wait_service,
        name="Wait for httpd to become active",
        service="httpd",
        timeout=60.0,
        interval=2.0,
    )


def stopped_factory(entry: dict) -> Host:
    """``host_factory`` building SimulatedHosts whose httpd is stopped."""
    host = SimulatedHost.from_inventory(entry)
    host.services = {"httpd.service": "inactive"}  # fresh dict per host
    return host


async def test_wait_service_runs_after_start_across_hosts():
    plans, report, hosts = await run(
        INVENTORY, start_and_wait, host_factory=stopped_factory
    )
    # The plan always shows the wait as a change, on every host.
    assert format_plan(plans).splitlines() == [
        "web1: Start httpd              [changed: httpd.service not running (inactive), will start; httpd.service not enabled, will enable]",
        "web1: Wait for httpd to become active [changed: wait for httpd.service to be active]",
        "web2: Start httpd              [changed: httpd.service not running (inactive), will start; httpd.service not enabled, will enable]",
        "web2: Wait for httpd to become active [changed: wait for httpd.service to be active]",
    ]
    # The apply phase started the unit on both hosts, then the wait passed.
    assert report is not None
    for host in hosts:
        assert host.services == {"httpd.service": "active"}
    log = format_log(report)
    assert "$ sudo systemctl start httpd.service" in log
    assert (
        "$ wait for SystemdServiceState('httpd.service') <is_active> "
        "every 2.0s up to 60.0s"
    ) in log
    assert [e.name for e in report.log] == [
        "Start httpd",
        "Start httpd",
        "Wait for httpd to become active",
        "Wait for httpd to become active",
    ]


async def test_wait_service_is_deterministic_across_phases():
    # The wait is planned unconditionally, so even when the unit is already
    # active (and the service op is a no-op) the apply-phase request sequence
    # matches the plan and the determinism guard stays quiet.
    host = SimulatedHost(
        name="h",
        services={"httpd.service": "active"},
        enabled_services={"httpd.service"},
    )
    runtimes, plans = await run_prepare([host], start_and_wait)
    assert [p.changed for p in plans["h"]] == [False, True]
    report = await run_apply(runtimes, plans=plans)
    assert [e.changed for e in report.log] == [False, True]


async def test_wait_service_dry_run_sends_nothing():
    _plans, report, hosts = await run(
        INVENTORY, start_and_wait, host_factory=stopped_factory, dry=True
    )
    assert report is None
    for host in hosts:
        assert host.services == {"httpd.service": "inactive"}


async def test_wait_service_times_out_and_aborts_the_run():
    def wait_only(host: Host) -> Generator[OperationRequest]:
        yield op(server.wait_service, name="Wait", service="httpd", timeout=1.0)

    with pytest.raises(WaitTimeoutError, match=r"host web1: .*'inactive'"):
        await run(INVENTORY, wait_only, host_factory=stopped_factory)
