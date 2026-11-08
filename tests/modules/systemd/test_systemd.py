# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the systemd module: facts, descriptors and operations.

Covers ``reemote5.modules.systemd.systemd``: the ``unit_name`` normalization
helper; the per-unit ``SystemdServiceState``/``SystemdServiceEnabled`` probes
and the ``service``/``daemon_reload`` operations (idempotence against the
seams, seam mutation, the pyinfra operation-fixture parity and the
backward-compatible ``server.service`` alias); the fleet-wide
``SystemdStatus``/``SystemdEnabled`` ports (pyinfra fact-fixture parity over
canned shell output, plus the seam projections); and the seven reemote5
originals -- ``SystemdUnitProperties``, ``SystemdFailedUnits``,
``SystemdTimers``, ``SystemdUnitFiles``, ``SystemdVersion``,
``SystemdSystemState`` and ``SystemdDependencies`` (command rendering, pure
parse over canned realistic output, copy-on-read seam reads and
``gather_fact`` argument forwarding); and the six original operations --
``mask``, ``reset_failed``, ``dropin``, ``tmpfiles``, ``sysusers`` and
``preset`` (descriptor rendering + elevation, idempotence against the seams,
option branches, seam mutation and the end-to-end plan/apply/dry/lockstep and
determinism guards), with the private ``_SystemdConfFile`` content fact the
file operations read. The per-unit/``service`` tests were
relocated from ``tests/modules/server/test_server.py`` alongside the symbols
themselves; ``server.wait_service``'s tests stay there.
"""

from collections.abc import Callable, Generator

import pytest

from reemote5.core import FactCache, OpContext, OperationRequest, op
from reemote5.drivers import (
    DeterminismError,
    command_shell,
    format_log,
    format_plan,
    gather_fact,
    run,
    run_apply,
    run_prepare,
    send_command,
)
from reemote5.hosts import Host
from reemote5.modules import server, systemd
from reemote5.modules.systemd.systemd import (
    SystemctlCommand,
    SystemdDependencies,
    SystemdEnabled,
    SystemdFailedUnits,
    SystemdFileInstallCommand,
    SystemdFileRemoveCommand,
    SystemdFileUploadCommand,
    SystemdServiceEnabled,
    SystemdServiceState,
    SystemdStatus,
    SystemdSystemState,
    SystemdSysusersCommand,
    SystemdTimers,
    SystemdTmpfilesCommand,
    SystemdUnitFiles,
    SystemdUnitProperties,
    SystemdVersion,
    _SystemdConfFile,
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


# --- service -----------------------------------------------------------------


def test_systemctl_command_shell_rendering():
    assert SystemctlCommand(action="daemon-reload").shell() == "systemctl daemon-reload"
    assert (
        SystemctlCommand(action="start", unit="httpd.service").shell()
        == "systemctl start httpd.service"
    )
    assert command_shell(
        SystemctlCommand(action="enable", unit="httpd.service"), sudo=True
    ) == ("sudo systemctl enable httpd.service")


def test_server_service_is_a_deprecated_alias():
    # The relocation keeps ``server.service`` as the very same function object.
    assert server.service is systemd.service
    assert server.SystemctlCommand is SystemctlCommand
    assert server.SystemdServiceState is SystemdServiceState
    assert server.SystemdServiceEnabled is SystemdServiceEnabled


async def test_service_starts_an_inactive_unit():
    host = SimulatedHost(name="h", services={"httpd.service": "inactive"})
    ctx, commands = await materialize(host, op(systemd.service, service="httpd"))
    assert ctx.changed
    assert "will start" in ctx.reason
    assert commands == [SystemctlCommand(action="start", unit="httpd.service")]


async def test_service_treats_activating_as_not_running():
    host = SimulatedHost(name="h", services={"httpd.service": "activating"})
    _ctx, commands = await materialize(host, op(systemd.service, service="httpd"))
    assert commands == [SystemctlCommand(action="start", unit="httpd.service")]


async def test_service_no_change_when_already_running():
    host = SimulatedHost(name="h", services={"httpd.service": "active"})
    ctx, commands = await materialize(host, op(systemd.service, service="httpd"))
    assert not ctx.changed
    assert ctx.reason == "httpd.service already running"
    assert commands == []


async def test_service_stops_a_running_unit():
    host = SimulatedHost(name="h", services={"httpd.service": "active"})
    ctx, commands = await materialize(
        host, op(systemd.service, service="httpd", running=False)
    )
    assert ctx.changed
    assert commands == [SystemctlCommand(action="stop", unit="httpd.service")]


async def test_service_restart_only_when_running():
    running = SimulatedHost(name="h", services={"httpd.service": "active"})
    ctx, commands = await materialize(
        running, op(systemd.service, service="httpd", restarted=True)
    )
    assert ctx.changed
    assert commands == [SystemctlCommand(action="restart", unit="httpd.service")]

    stopped = SimulatedHost(name="h", services={"httpd.service": "inactive"})
    _, commands = await materialize(
        stopped, op(systemd.service, service="httpd", restarted=True)
    )
    # A stopped unit is started, not restarted (pyinfra's handle_service_control).
    assert commands == [SystemctlCommand(action="start", unit="httpd.service")]


async def test_service_enables_and_disables_on_boot():
    host = SimulatedHost(name="h", services={"httpd.service": "active"})
    ctx, commands = await materialize(
        host, op(systemd.service, service="httpd", enabled=True)
    )
    assert ctx.changed
    assert commands == [SystemctlCommand(action="enable", unit="httpd.service")]

    host.enabled_services.add("httpd.service")
    ctx, commands = await materialize(
        host, op(systemd.service, service="httpd", enabled=True)
    )
    assert not ctx.changed
    assert commands == []

    ctx, commands = await materialize(
        host, op(systemd.service, service="httpd", enabled=False)
    )
    assert commands == [SystemctlCommand(action="disable", unit="httpd.service")]


async def test_service_enabled_none_leaves_boot_setting_alone():
    host = SimulatedHost(name="h", services={"httpd.service": "active"})
    ctx, commands = await materialize(host, op(systemd.service, service="httpd"))
    assert commands == []
    assert "enabled" not in ctx.reason


async def test_service_daemon_reload_always_yields_first():
    host = SimulatedHost(
        name="h", services={"httpd.service": "inactive"}, enabled_services=set()
    )
    ctx, commands = await materialize(
        host,
        op(systemd.service, service="httpd", enabled=True, daemon_reload=True),
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


# --- SystemctlCommand rendering (scope + now) --------------------------------


def test_systemctl_command_renders_scope_and_now():
    # Defaults stay the plain system-scope rendering.
    assert SystemctlCommand(action="start", unit="httpd.service").shell() == (
        "systemctl start httpd.service"
    )
    assert SystemctlCommand(action="daemon-reload").shell() == "systemctl daemon-reload"
    # user / machine / user@machine / user@.host bases (pyinfra _make_systemctl_cmd).
    assert SystemctlCommand(action="start", unit="httpd.service", user_mode=True).shell() == (
        "systemctl --user start httpd.service"
    )
    assert SystemctlCommand(
        action="start", unit="httpd.service", machine="testmachine"
    ).shell() == "systemctl --machine=testmachine start httpd.service"
    assert SystemctlCommand(
        action="start", unit="httpd.service", user_name="testuser"
    ).shell() == "systemctl --machine=testuser@.host start httpd.service"
    assert SystemctlCommand(
        action="start",
        unit="httpd.service",
        user_mode=True,
        machine="testmachine",
        user_name="testuser",
    ).shell() == "systemctl --user --machine=testuser@testmachine start httpd.service"
    # --now sits between the action and the unit.
    assert SystemctlCommand(action="enable", unit="httpd.service", now=True).shell() == (
        "systemctl enable --now httpd.service"
    )
    assert SystemctlCommand(action="disable", unit="httpd.service", now=True).shell() == (
        "systemctl disable --now httpd.service"
    )
    # The unit is shell-quoted; the scope base is not (pyinfra interpolates it raw).
    assert SystemctlCommand(action="enable", unit="my-service; whoami.service").shell() == (
        "systemctl enable 'my-service; whoami.service'"
    )
    # Elevation prefixes the whole rendering.
    assert command_shell(
        SystemctlCommand(action="enable", unit="httpd.service", now=True), sudo=True
    ) == "sudo systemctl enable --now httpd.service"


def test_systemctl_command_is_hashable_and_value_equal_with_scope():
    a = SystemctlCommand(action="start", unit="httpd.service", user_mode=True, now=True)
    b = SystemctlCommand(action="start", unit="httpd.service", user_mode=True, now=True)
    assert a == b
    assert hash(a) == hash(b)
    assert a != SystemctlCommand(action="start", unit="httpd.service")


# --- service: pyinfra operation fixture parity -------------------------------


@pytest.mark.parametrize(
    ("service", "kwargs", "state", "enabled_units", "expected", "changed"),
    [
        # pyinfra's own operation fixtures (tests/operations/systemd.service/),
        # replayed over the SimulatedHost seams. SystemdStatus's running bool
        # maps to the ActiveState string the seam stores (true -> "active",
        # false -> "inactive"); SystemdEnabled's bool maps to membership in
        # enabled_services. The expected strings are pyinfra's verbatim.
        ("redis-server.service", {}, "inactive", [], ["systemctl start redis-server.service"], True),
        ("redis-server.service", {"running": False}, "inactive", [], [], False),
        ("redis-server.service", {}, "active", [], [], False),
        ("some-timer.timer", {}, "inactive", [], ["systemctl start some-timer.timer"], True),
        ("logrotate.timer", {}, "inactive", [], ["systemctl start logrotate.timer"], True),
        ("redis-server", {}, "inactive", [], ["systemctl start redis-server.service"], True),
        ("redis-server.service", {"daemon_reload": True}, "active", [], ["systemctl daemon-reload"], True),
        ("redis-server.service", {"user_mode": True}, "inactive", [], ["systemctl --user start redis-server.service"], True),
        ("redis-server.service", {"machine": "testmachine"}, "inactive", [], ["systemctl --machine=testmachine start redis-server.service"], True),
        ("redis-server.service", {"user_mode": True, "machine": "testmachine", "user_name": "testuser"}, "inactive", [], ["systemctl --user --machine=testuser@testmachine start redis-server.service"], True),
        ("redis-server.service", {"daemon_reload": True, "user_mode": True}, "active", [], ["systemctl --user daemon-reload"], True),
        ("redis-server.service", {"daemon_reload": True, "machine": "testmachine"}, "active", [], ["systemctl --machine=testmachine daemon-reload"], True),
        ("redis-server.service", {"daemon_reload": True, "user_mode": True, "machine": "testmachine", "user_name": "testuser"}, "active", [], ["systemctl --user --machine=testuser@testmachine daemon-reload"], True),
        ("redis-server.service", {"enabled": True}, "active", [], ["systemctl enable redis-server.service"], True),
        ("redis-server.service", {"enabled": False}, "active", ["redis-server.service"], ["systemctl disable redis-server.service"], True),
        ("my-service; whoami", {"enabled": True}, "active", [], ["systemctl enable 'my-service; whoami.service'"], True),
        ("redis-server.service", {"enabled": True, "user_mode": True}, "active", [], ["systemctl --user enable redis-server.service"], True),
        ("redis-server.service", {"enabled": True, "user_mode": True, "machine": "testmachine", "user_name": "testuser"}, "active", [], ["systemctl --user --machine=testuser@testmachine enable redis-server.service"], True),
        ("redis-server.service", {"enabled": True, "machine": "testmachine"}, "active", [], ["systemctl --machine=testmachine enable redis-server.service"], True),
        ("redis-server.service", {"enabled": False, "user_mode": True}, "active", ["redis-server.service"], ["systemctl --user disable redis-server.service"], True),
        ("redis-server.service", {"enabled": False, "user_mode": True, "machine": "testmachine", "user_name": "testuser"}, "active", ["redis-server.service"], ["systemctl --user --machine=testuser@testmachine disable redis-server.service"], True),
        ("redis-server.service", {"enabled": False, "machine": "testmachine"}, "active", ["redis-server.service"], ["systemctl --machine=testmachine disable redis-server.service"], True),
        ("my-service; whoami", {"enabled": False}, "active", ["my-service; whoami.service"], ["systemctl disable 'my-service; whoami.service'"], True),
    ],
)
async def test_service_matches_pyinfra_operation_fixtures(
    service, kwargs: dict, state: str, enabled_units: list, expected: list, changed: bool
):
    host = SimulatedHost(
        name="h",
        services={unit_name(service): state},
        enabled_services=set(enabled_units),
    )
    ctx, commands = await materialize(host, op(systemd.service, service=service, **kwargs))
    assert ctx.changed is changed
    assert [command_shell(cmd) for cmd in commands] == expected


# --- service: reemote5 option extensions -------------------------------------


async def test_service_reloaded_only_when_running():
    running = SimulatedHost(name="h", services={"httpd.service": "active"})
    ctx, commands = await materialize(
        running, op(systemd.service, service="httpd", reloaded=True)
    )
    assert ctx.changed
    assert commands == [SystemctlCommand(action="reload", unit="httpd.service")]
    assert "will reload" in ctx.reason

    stopped = SimulatedHost(name="h", services={"httpd.service": "inactive"})
    _, commands = await materialize(
        stopped, op(systemd.service, service="httpd", reloaded=True)
    )
    # A stopped unit is started, not reloaded.
    assert commands == [SystemctlCommand(action="start", unit="httpd.service")]


async def test_service_soft_reload_uses_reload_or_try_restart():
    host = SimulatedHost(name="h", services={"httpd.service": "active"})
    ctx, commands = await materialize(
        host, op(systemd.service, service="httpd", reloaded=True, soft_reload=True)
    )
    assert ctx.changed
    assert commands == [
        SystemctlCommand(action="reload-or-try-restart", unit="httpd.service")
    ]


async def test_service_reset_failed_clears_a_failed_unit_before_start():
    host = SimulatedHost(name="h", services={"httpd.service": "failed"})
    ctx, commands = await materialize(
        host, op(systemd.service, service="httpd", reset_failed=True)
    )
    assert ctx.changed
    assert commands == [
        SystemctlCommand(action="reset-failed", unit="httpd.service"),
        SystemctlCommand(action="start", unit="httpd.service"),
    ]
    assert "will reset-failed" in ctx.reason


async def test_service_reset_failed_is_a_noop_when_not_failed():
    host = SimulatedHost(name="h", services={"httpd.service": "active"})
    ctx, commands = await materialize(
        host, op(systemd.service, service="httpd", reset_failed=True)
    )
    assert not ctx.changed
    assert commands == []


async def test_service_now_starts_and_stops_with_the_boot_setting():
    host = SimulatedHost(name="h", services={"httpd.service": "inactive"})
    ctx, commands = await materialize(
        host, op(systemd.service, service="httpd", enabled=True, now=True)
    )
    assert ctx.changed
    # running=True (default) starts it, then enable --now is yielded too.
    assert commands == [
        SystemctlCommand(action="start", unit="httpd.service"),
        SystemctlCommand(action="enable", unit="httpd.service", now=True),
    ]

    host = SimulatedHost(
        name="h", services={"httpd.service": "active"}, enabled_services={"httpd.service"}
    )
    _, commands = await materialize(
        host, op(systemd.service, service="httpd", running=False, enabled=False, now=True)
    )
    assert commands == [
        SystemctlCommand(action="stop", unit="httpd.service"),
        SystemctlCommand(action="disable", unit="httpd.service", now=True),
    ]


async def test_service_now_without_enabled_change_has_no_effect():
    host = SimulatedHost(name="h", services={"httpd.service": "active"})
    ctx, commands = await materialize(host, op(systemd.service, service="httpd", now=True))
    assert not ctx.changed
    assert commands == []


async def test_service_scope_is_forwarded_to_the_descriptors():
    host = SimulatedHost(name="h", services={"httpd.service": "inactive"})
    _ctx, commands = await materialize(
        host,
        op(
            systemd.service,
            service="httpd",
            user_mode=True,
            machine="testmachine",
            user_name="testuser",
        ),
    )
    assert commands == [
        SystemctlCommand(
            action="start",
            unit="httpd.service",
            user_mode=True,
            machine="testmachine",
            user_name="testuser",
        )
    ]


# --- facts: scope command rendering + forwarding -----------------------------


def test_fact_command_renders_scope():
    assert SystemdServiceState.command("httpd") == (
        "systemctl show -p ActiveState --value httpd.service"
    )
    assert SystemdServiceState.command("httpd", user_mode=True) == (
        "systemctl --user show -p ActiveState --value httpd.service"
    )
    assert SystemdServiceState.command("httpd", machine="m", user_name="u") == (
        "systemctl --machine=u@m show -p ActiveState --value httpd.service"
    )
    assert SystemdServiceEnabled.command("httpd", user_name="u") == (
        "systemctl --machine=u@.host show -p UnitFileState --value httpd.service"
    )


async def test_fact_process_forwards_scope_to_the_shell():
    host = CannedShellHost("active\n")
    assert await SystemdServiceState.process(host, "httpd", user_mode=True) == "active"
    assert host.shells == ["systemctl --user show -p ActiveState --value httpd.service"]


async def test_service_state_gather_fact_forwards_the_unit():
    hosts = [
        SimulatedHost(name="web1", services={"httpd.service": "active"}),
        SimulatedHost(name="web2", services={"httpd.service": "inactive"}),
    ]
    assert await gather_fact(hosts, SystemdServiceState, "httpd") == {
        "web1": "active",
        "web2": "inactive",
    }


# --- daemon_reload -----------------------------------------------------------


def test_daemon_reload_command_rendering():
    assert command_shell(SystemctlCommand(action="daemon-reload"), sudo=True) == (
        "sudo systemctl daemon-reload"
    )


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, "systemctl daemon-reload"),
        ({"user_mode": True}, "systemctl --user daemon-reload"),
        ({"machine": "testmachine"}, "systemctl --machine=testmachine daemon-reload"),
        (
            {"user_mode": True, "machine": "testmachine", "user_name": "testuser"},
            "systemctl --user --machine=testuser@testmachine daemon-reload",
        ),
    ],
)
async def test_daemon_reload_always_yields_with_scope(kwargs: dict, expected: str):
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(host, op(systemd.daemon_reload, **kwargs))
    # Stateless and non-idempotent: changed even with no unit state at all.
    assert ctx.changed
    assert ctx.reason == "daemon-reload"
    assert [command_shell(cmd) for cmd in commands] == [expected]


# --- seam mutation for the extended actions ----------------------------------


async def test_extended_actions_mutate_the_simulated_host():
    host = SimulatedHost(name="h", services={"httpd.service": "failed"})
    await send_command(host, SystemctlCommand(action="reset-failed", unit="httpd.service"))
    assert host.services == {"httpd.service": "inactive"}
    # reset-failed on a non-failed unit leaves the state alone.
    await send_command(host, SystemctlCommand(action="reset-failed", unit="httpd.service"))
    assert host.services == {"httpd.service": "inactive"}

    await send_command(host, SystemctlCommand(action="reload", unit="httpd.service"))
    assert host.services == {"httpd.service": "active"}
    await send_command(
        host, SystemctlCommand(action="reload-or-try-restart", unit="httpd.service")
    )
    assert host.services == {"httpd.service": "active"}

    # enable --now starts the unit as well as recording the boot setting.
    host = SimulatedHost(name="h", services={"httpd.service": "inactive"})
    await send_command(
        host, SystemctlCommand(action="enable", unit="httpd.service", now=True)
    )
    assert host.enabled_services == {"httpd.service"}
    assert host.services == {"httpd.service": "active"}
    await send_command(
        host, SystemctlCommand(action="disable", unit="httpd.service", now=True)
    )
    assert host.enabled_services == set()
    assert host.services == {"httpd.service": "inactive"}


# --- service end to end ------------------------------------------------------


def manage_httpd(host: Host) -> Generator[OperationRequest]:
    """The example deploy: start + enable httpd, elevated."""
    yield op(
        systemd.service,
        name="Manage httpd",
        service="httpd",
        running=True,
        enabled=True,
        sudo=True,
    )


def stopped_factory(entry: dict) -> Host:
    """``host_factory`` building SimulatedHosts whose httpd is stopped."""
    host = SimulatedHost.from_inventory(entry)
    host.services = {"httpd.service": "inactive"}
    return host


async def test_service_runs_across_hosts_in_lockstep():
    plans, report, hosts = await run(INVENTORY, manage_httpd, host_factory=stopped_factory)
    assert format_plan(plans).splitlines() == [
        "web1: Manage httpd             [changed: httpd.service not running (inactive), will start; httpd.service not enabled, will enable]",
        "web2: Manage httpd             [changed: httpd.service not running (inactive), will start; httpd.service not enabled, will enable]",
    ]
    assert report is not None
    assert [e.name for e in report.log] == ["Manage httpd", "Manage httpd"]
    log = format_log(report)
    assert log.count("$ sudo systemctl start httpd.service") == 2
    assert log.count("$ sudo systemctl enable httpd.service") == 2
    for host in hosts:
        assert host.services == {"httpd.service": "active"}
        assert host.enabled_services == {"httpd.service"}


async def test_service_is_idempotent_across_runs():
    host = stopped_factory({"host": "h", "connection": {}})
    runtimes, plans = await run_prepare([host], manage_httpd)
    assert [p.changed for p in plans["h"]] == [True]
    report = await run_apply(runtimes, plans=plans)
    assert [e.changed for e in report.log] == [True]
    # A fresh prepare against the now-running, enabled unit plans no change.
    _runtimes, plans = await run_prepare([host], manage_httpd)
    assert [p.changed for p in plans["h"]] == [False]


async def test_service_dry_run_sends_nothing():
    _plans, report, hosts = await run(
        INVENTORY, manage_httpd, host_factory=stopped_factory, dry=True
    )
    assert report is None
    for host in hosts:
        assert host.services == {"httpd.service": "inactive"}
        assert host.enabled_services == set()


async def test_service_branching_on_live_state_raises_determinism_error():
    def live_branch(host: Host) -> Generator[OperationRequest]:
        yield op(systemd.service, name="Start httpd", service="httpd", sudo=True)
        # The illegal branch: consult live state the first start mutates.
        if host.services.get("httpd.service") == "active":
            yield op(systemd.service, name="Start nginx", service="nginx", sudo=True)

    host = SimulatedHost(name="h", services={"httpd.service": "inactive"})
    runtimes, plans = await run_prepare([host], live_branch)
    assert [p.request.name for p in plans["h"]] == ["Start httpd"]
    with pytest.raises(DeterminismError):
        await run_apply(runtimes, plans=plans)


def reload_factory(snapshot: dict[str, str]) -> Callable[[Host], Generator[OperationRequest]]:
    """The example factory: close over a frozen ActiveState snapshot.

    Branching only on the snapshot taken before either phase keeps the request
    sequence identical across phases -- the determinism-safe shape.
    """

    def deploy(host: Host) -> Generator[OperationRequest]:
        if snapshot[host.name] == "active":
            yield op(systemd.service, name="Reload httpd", service="httpd", reloaded=True, sudo=True)

    return deploy


async def test_service_snapshot_factory_runs_deterministically():
    def factory(entry: dict) -> Host:
        host = SimulatedHost.from_inventory(entry)
        host.services = {"httpd.service": "active"}
        return host

    snapshot = await gather_fact([factory(e) for e in INVENTORY], SystemdServiceState, "httpd")
    deploy = reload_factory(snapshot)
    plans, report, hosts = await run(INVENTORY, deploy, host_factory=factory)
    assert report is not None
    assert [p.request.name for p in plans["web1"]] == ["Reload httpd"]
    log = format_log(report)
    assert log.count("$ sudo systemctl reload httpd.service") == 2
    for host in hosts:
        assert host.services == {"httpd.service": "active"}


# --- SystemdStatus / SystemdEnabled: pyinfra fact-fixture parity -------------

# pyinfra's own fact fixtures (tests/facts/systemd.SystemdStatus/ and
# systemd.SystemdEnabled/), replayed verbatim through CannedShellHost. The
# four-unit output is shared by the user/machine scope fixtures; the
# eight-unit output and the invalid-line output are SystemdStatus-only.
_STATUS_OUTPUT_4 = (
    "Id=lvm2-activation.service\nSubState=dead\n\n"
    "Id=lvm2-lvmetad.service\nSubState=running\n\n"
    "Id=lvm2-lvmpolld.service\nSubState=dead\n\n"
    "Id=ebtables.timer\nSubState=exited"
)
_STATUS_FACT_4 = {
    "lvm2-activation.service": False,
    "lvm2-lvmetad.service": True,
    "lvm2-lvmpolld.service": False,
    "ebtables.timer": True,
}
_STATUS_OUTPUT_8 = (
    _STATUS_OUTPUT_4 + "\n\n"
    "Id=mnt-foo.mount\nSubState=mounted\n\n"
    "Id=multi-user.target\nSubState=active\n\n"
    "Id=system.slice\nSubState=active\n\n"
    "Id=dev-sda1.device\nSubState=plugged"
)
_STATUS_FACT_8 = {
    **_STATUS_FACT_4,
    "mnt-foo.mount": True,
    "multi-user.target": True,
    "system.slice": True,
    "dev-sda1.device": True,
}
_STATUS_OUTPUT_INVALID = (
    "Id=lvm2-activation.service\nSubState=dead\nnoblanklineinserthere\n"
    "Id=lvm2-lvmetad.service\nSubState=running\n\n\n\n"
    "Id=lvm2-lvmpolld.service\nSubState=dead\n-invalid-line==,yes\n"
    "Id=ebtables.timer\nSubState=exited"
)
_ENABLED_OUTPUT = (
    "Id=vboxadd.service\nUnitFileState=enabled\n\n"
    "Id=vgauth.timer\nUnitFileState=enabled\n\n"
    "Id=x11-common.service\nUnitFileState=masked"
)
_ENABLED_FACT = {
    "vboxadd.service": True,
    "vgauth.timer": True,
    "x11-common.service": False,
}


@pytest.mark.parametrize(
    ("fact_cls", "kwargs", "output", "expected_command", "expected_fact"),
    [
        # SystemdStatus: default, user, machine, user@machine, root@machine.
        (SystemdStatus, {}, _STATUS_OUTPUT_8,
         "systemctl show --all --property Id --property SubState '*'", _STATUS_FACT_8),
        (SystemdStatus, {"user_mode": True}, _STATUS_OUTPUT_4,
         "( systemctl --user show --all --property Id --property SubState '*' 2>/dev/null || true )",
         _STATUS_FACT_4),
        (SystemdStatus, {"machine": "testmachine", "user_name": "testuser"}, _STATUS_OUTPUT_4,
         "systemctl --machine=testuser@testmachine show --all --property Id --property SubState '*'",
         _STATUS_FACT_4),
        (SystemdStatus, {"machine": "testmachine"}, _STATUS_OUTPUT_4,
         "systemctl --machine=testmachine show --all --property Id --property SubState '*'",
         _STATUS_FACT_4),
        (SystemdStatus, {"user_mode": True, "machine": "testmachine", "user_name": "testuser"},
         _STATUS_OUTPUT_4,
         "( systemctl --user --machine=testuser@testmachine show --all --property Id --property SubState '*' 2>/dev/null || true )",
         _STATUS_FACT_4),
        # The invalid-line fixture: a no-'=' line resets the current unit, a
        # stray '-invalid-line==,yes' is ignored, blank lines never mispair.
        (SystemdStatus, {}, _STATUS_OUTPUT_INVALID,
         "systemctl show --all --property Id --property SubState '*'", _STATUS_FACT_4),
        # SystemdEnabled: the same scope matrix on UnitFileState.
        (SystemdEnabled, {}, _ENABLED_OUTPUT,
         "systemctl show --all --property Id --property UnitFileState '*'", _ENABLED_FACT),
        (SystemdEnabled, {"user_mode": True}, _ENABLED_OUTPUT,
         "( systemctl --user show --all --property Id --property UnitFileState '*' 2>/dev/null || true )",
         _ENABLED_FACT),
        (SystemdEnabled, {"machine": "testmachine", "user_name": "testuser"}, _ENABLED_OUTPUT,
         "systemctl --machine=testuser@testmachine show --all --property Id --property UnitFileState '*'",
         _ENABLED_FACT),
        (SystemdEnabled, {"machine": "testmachine"}, _ENABLED_OUTPUT,
         "systemctl --machine=testmachine show --all --property Id --property UnitFileState '*'",
         _ENABLED_FACT),
        (SystemdEnabled, {"user_mode": True, "machine": "testmachine", "user_name": "testuser"},
         _ENABLED_OUTPUT,
         "( systemctl --user --machine=testuser@testmachine show --all --property Id --property UnitFileState '*' 2>/dev/null || true )",
         _ENABLED_FACT),
    ],
)
async def test_status_enabled_match_pyinfra_fact_fixtures(
    fact_cls, kwargs: dict, output: str, expected_command: str, expected_fact: dict
):
    host = CannedShellHost(output)
    assert await fact_cls.process(host, **kwargs) == expected_fact
    assert host.shells == [expected_command]


async def test_status_projects_the_services_seam_to_active_bools():
    host = SimulatedHost(
        name="h", services={"httpd.service": "active", "nginx.service": "inactive"}
    )
    assert await SystemdStatus.process(host) == {
        "httpd.service": True,
        "nginx.service": False,
    }


async def test_enabled_projects_membership_over_the_known_units():
    host = SimulatedHost(
        name="h",
        services={"httpd.service": "active", "nginx.service": "inactive"},
        enabled_services={"httpd.service"},
    )
    assert await SystemdEnabled.process(host) == {
        "httpd.service": True,
        "nginx.service": False,
    }


async def test_status_gather_fact_fans_out_across_hosts():
    hosts = [
        SimulatedHost(name="web1", services={"httpd.service": "active"}),
        SimulatedHost(name="web2", services={"httpd.service": "inactive"}),
    ]
    assert await gather_fact(hosts, SystemdStatus) == {
        "web1": {"httpd.service": True},
        "web2": {"httpd.service": False},
    }


# --- SystemdUnitProperties ---------------------------------------------------

_PROPERTIES_OUTPUT = (
    "Id=httpd.service\n"
    "Description=The Apache HTTP Server\n"
    "LoadState=loaded\n"
    "ActiveState=active\n"
    "SubState=running\n"
    "UnitFileState=enabled\n"
    "FragmentPath=/usr/lib/systemd/system/httpd.service\n"
    "MainPID=1234\n"
)
_PROPERTIES_FACT = {
    "Id": "httpd.service",
    "Description": "The Apache HTTP Server",
    "LoadState": "loaded",
    "ActiveState": "active",
    "SubState": "running",
    "UnitFileState": "enabled",
    "FragmentPath": "/usr/lib/systemd/system/httpd.service",
    "MainPID": "1234",
}


def test_unit_properties_command_rendering():
    props = "Id,Description,LoadState,ActiveState,SubState,UnitFileState,FragmentPath,MainPID"
    assert SystemdUnitProperties.command("httpd") == (
        f"systemctl show httpd.service --property={props}"
    )
    assert SystemdUnitProperties.command("httpd", user_mode=True) == (
        f"systemctl --user show httpd.service --property={props}"
    )
    assert SystemdUnitProperties.command("logrotate.timer", machine="m", user_name="u") == (
        f"systemctl --machine=u@m show logrotate.timer --property={props}"
    )


async def test_unit_properties_parses_raw_strings():
    host = CannedShellHost(_PROPERTIES_OUTPUT)
    assert await SystemdUnitProperties.process(host, "httpd") == _PROPERTIES_FACT


async def test_unit_properties_empty_output_is_empty_dict():
    assert await SystemdUnitProperties.process(CannedShellHost(""), "httpd") == {}


async def test_unit_properties_seam_read_is_a_copy():
    host = SimulatedHost(name="h", systemd_unit_properties={"httpd.service": _PROPERTIES_FACT})
    result = await SystemdUnitProperties.process(host, "httpd")
    assert result == _PROPERTIES_FACT
    result["ActiveState"] = "inactive"
    assert host.systemd_unit_properties["httpd.service"]["ActiveState"] == "active"
    assert await SystemdUnitProperties.process(host, "nginx") == {}


async def test_unit_properties_gather_fact_forwards_the_unit():
    hosts = [
        SimulatedHost(name="web1", systemd_unit_properties={"httpd.service": _PROPERTIES_FACT}),
        SimulatedHost(name="web2", systemd_unit_properties={}),
    ]
    assert await gather_fact(hosts, SystemdUnitProperties, "httpd") == {
        "web1": _PROPERTIES_FACT,
        "web2": {},
    }


# --- SystemdFailedUnits ------------------------------------------------------

_FAILED_OUTPUT = (
    "\u25cf httpd.service loaded failed failed The Apache HTTP Server\n"
    "nginx.service loaded failed failed A high performance web server\n"
)
_FAILED_FACT = [
    {"unit": "httpd.service", "load": "loaded", "active": "failed", "sub": "failed",
     "description": "The Apache HTTP Server"},
    {"unit": "nginx.service", "load": "loaded", "active": "failed", "sub": "failed",
     "description": "A high performance web server"},
]


def test_failed_units_command_rendering():
    assert SystemdFailedUnits.command() == "systemctl --failed --no-legend --no-pager"
    assert SystemdFailedUnits.command(user_mode=True) == (
        "systemctl --user --failed --no-legend --no-pager"
    )


async def test_failed_units_parses_rows_and_strips_the_bullet():
    assert await SystemdFailedUnits.process(CannedShellHost(_FAILED_OUTPUT)) == _FAILED_FACT


async def test_failed_units_empty_and_short_rows():
    assert await SystemdFailedUnits.process(CannedShellHost("")) == []
    # A row with fewer than four columns is skipped; a description-less row
    # keeps an empty description.
    output = "foo.service loaded failed\nbar.service loaded failed failed\n"
    assert await SystemdFailedUnits.process(CannedShellHost(output)) == [
        {"unit": "bar.service", "load": "loaded", "active": "failed", "sub": "failed",
         "description": ""},
    ]


async def test_failed_units_seam_read_copies_each_entry():
    host = SimulatedHost(name="h", systemd_failed_units=_FAILED_FACT)
    result = await SystemdFailedUnits.process(host)
    assert result == _FAILED_FACT
    result[0]["active"] = "active"
    assert host.systemd_failed_units[0]["active"] == "failed"


# --- SystemdTimers -----------------------------------------------------------

_TIMERS_OUTPUT = (
    "Id=logrotate.timer\nActiveState=active\nUnitFileState=enabled\n"
    "NextElapseUSecRealtime=1700000000000000\nLastTriggerUSec=1699990000000000\n\n"
    "Id=fstrim.timer\nActiveState=inactive\nUnitFileState=enabled\n"
    "NextElapseUSecRealtime=0\nLastTriggerUSec=0\n"
)
_TIMERS_FACT = {
    "logrotate.timer": {"active_state": "active", "unit_file_state": "enabled",
                        "next_elapse_usec": 1700000000000000, "last_trigger_usec": 1699990000000000},
    "fstrim.timer": {"active_state": "inactive", "unit_file_state": "enabled",
                     "next_elapse_usec": None, "last_trigger_usec": None},
}


def test_timers_command_rendering():
    props = "Id,ActiveState,UnitFileState,NextElapseUSecRealtime,LastTriggerUSec"
    assert SystemdTimers.command() == f"systemctl show '*.timer' --property={props}"
    assert SystemdTimers.command(machine="m") == (
        f"systemctl --machine=m show '*.timer' --property={props}"
    )


async def test_timers_parses_blocks_and_maps_zero_to_none():
    assert await SystemdTimers.process(CannedShellHost(_TIMERS_OUTPUT)) == _TIMERS_FACT


async def test_timers_maps_a_formatted_timestamp_string_to_none():
    # Some systemd versions render LastTriggerUSec/NextElapseUSecRealtime as a
    # locale-formatted date string rather than the raw microsecond integer;
    # the parser must map that non-machine-readable form to None, not raise
    # ValueError (regression: a live `systemctl show '*.timer'` crashed int()).
    output = (
        "Id=logrotate.timer\nActiveState=active\nUnitFileState=enabled\n"
        "NextElapseUSecRealtime=Sun 2026-11-08 00:00:00 CET\n"
        "LastTriggerUSec=Sat 2026-11-07 05:52:09 CET\n"
    )
    assert await SystemdTimers.process(CannedShellHost(output)) == {
        "logrotate.timer": {"active_state": "active", "unit_file_state": "enabled",
                            "next_elapse_usec": None, "last_trigger_usec": None},
    }


async def test_timers_empty_output_is_empty_dict():
    assert await SystemdTimers.process(CannedShellHost("")) == {}


async def test_timers_seam_read_is_a_copy():
    host = SimulatedHost(name="h", systemd_timers=_TIMERS_FACT)
    result = await SystemdTimers.process(host)
    assert result == _TIMERS_FACT
    result["logrotate.timer"]["active_state"] = "inactive"
    assert host.systemd_timers["logrotate.timer"]["active_state"] == "active"


# --- SystemdUnitFiles --------------------------------------------------------

_UNIT_FILES_OUTPUT = (
    "httpd.service enabled enabled\n"
    "nginx.service disabled disabled\n"
    "getty@.service enabled enabled\n"
    "systemd-tmpfiles-clean.timer static -\n"
    "foo.service generated\n"
    "5 unit files listed.\n"
)
_UNIT_FILES_FACT = {
    "httpd.service": {"state": "enabled", "preset": "enabled"},
    "nginx.service": {"state": "disabled", "preset": "disabled"},
    "getty@.service": {"state": "enabled", "preset": "enabled"},
    "systemd-tmpfiles-clean.timer": {"state": "static", "preset": "-"},
    "foo.service": {"state": "generated", "preset": ""},
}


def test_unit_files_command_rendering():
    assert SystemdUnitFiles.command() == "systemctl list-unit-files --no-legend --no-pager"
    assert SystemdUnitFiles.command(user_mode=True, user_name="u") == (
        "systemctl --user --machine=u@.host list-unit-files --no-legend --no-pager"
    )


async def test_unit_files_parses_rows_and_skips_the_footer():
    assert await SystemdUnitFiles.process(CannedShellHost(_UNIT_FILES_OUTPUT)) == _UNIT_FILES_FACT


async def test_unit_files_empty_output_is_empty_dict():
    assert await SystemdUnitFiles.process(CannedShellHost("")) == {}


async def test_unit_files_seam_read_is_a_copy():
    host = SimulatedHost(name="h", systemd_unit_files=_UNIT_FILES_FACT)
    result = await SystemdUnitFiles.process(host)
    assert result == _UNIT_FILES_FACT
    result["httpd.service"]["state"] = "masked"
    assert host.systemd_unit_files["httpd.service"]["state"] == "enabled"


# --- SystemdVersion ----------------------------------------------------------

_VERSION_OUTPUT = (
    "systemd 255 (255.4-1ubuntu8.6)\n"
    "+PAM +AUDIT +SELINUX +APPARMOR +IMA +SMACK +SECCOMP\n"
)
_VERSION_FACT = {
    "version": 255,
    "release": "255.4-1ubuntu8.6",
    "features": "+PAM +AUDIT +SELINUX +APPARMOR +IMA +SMACK +SECCOMP",
}


def test_version_command_is_global():
    assert SystemdVersion.COMMAND == "systemctl --version"


async def test_version_parses_the_banner():
    host = CannedShellHost(_VERSION_OUTPUT)
    assert await SystemdVersion.process(host) == _VERSION_FACT
    assert host.shells == ["systemctl --version"]


async def test_version_empty_output_is_version_none():
    assert await SystemdVersion.process(CannedShellHost("")) == {
        "version": None, "release": "", "features": ""
    }


async def test_version_seam_default_and_seeded():
    assert await SystemdVersion.process(SimulatedHost(name="h")) == {
        "version": None, "release": "", "features": ""
    }
    host = SimulatedHost(name="h", systemd_version=_VERSION_FACT)
    assert await SystemdVersion.process(host) == _VERSION_FACT


# --- SystemdSystemState ------------------------------------------------------


def test_system_state_command_rendering():
    assert SystemdSystemState.command() == "systemctl is-system-running || true"
    assert SystemdSystemState.command(user_mode=True) == (
        "systemctl --user is-system-running || true"
    )


async def test_system_state_parses_the_health_word():
    assert await SystemdSystemState.process(CannedShellHost("degraded\n")) == "degraded"
    assert await SystemdSystemState.process(CannedShellHost("running\n")) == "running"


async def test_system_state_empty_output_is_unknown():
    assert await SystemdSystemState.process(CannedShellHost("")) == "unknown"
    assert await SystemdSystemState.process(CannedShellHost("\n\n")) == "unknown"


async def test_system_state_seam_default_and_seeded():
    assert await SystemdSystemState.process(SimulatedHost(name="h")) == "unknown"
    host = SimulatedHost(name="h", systemd_system_state="running")
    assert await SystemdSystemState.process(host) == "running"


# --- SystemdDependencies -----------------------------------------------------

_DEPENDENCIES_OUTPUT = (
    "Requires=network.target remote-fs.target\n"
    "Wants=\n"
    "After=network.target remote-fs.target basic.target\n"
    "Before=multi-user.target\n"
    "Conflicts=\n"
)
_DEPENDENCIES_FACT = {
    "Requires": ["network.target", "remote-fs.target"],
    "Wants": [],
    "After": ["network.target", "remote-fs.target", "basic.target"],
    "Before": ["multi-user.target"],
    "Conflicts": [],
}


def test_dependencies_command_rendering():
    props = "Requires,Wants,After,Before,Conflicts"
    assert SystemdDependencies.command("httpd") == (
        f"systemctl show httpd.service --property={props}"
    )
    assert SystemdDependencies.command("httpd", machine="m", user_name="u") == (
        f"systemctl --machine=u@m show httpd.service --property={props}"
    )


async def test_dependencies_parses_unit_lists():
    assert await SystemdDependencies.process(CannedShellHost(_DEPENDENCIES_OUTPUT), "httpd") == (
        _DEPENDENCIES_FACT
    )


async def test_dependencies_ignores_stray_keys_and_empty_output():
    assert await SystemdDependencies.process(CannedShellHost(""), "httpd") == {}
    assert await SystemdDependencies.process(CannedShellHost("Foo=bar\n"), "httpd") == {}


async def test_dependencies_seam_read_copies_each_list():
    host = SimulatedHost(name="h", systemd_dependencies={"httpd.service": _DEPENDENCIES_FACT})
    result = await SystemdDependencies.process(host, "httpd")
    assert result == _DEPENDENCIES_FACT
    result["Requires"].append("extra.target")
    assert "extra.target" not in host.systemd_dependencies["httpd.service"]["Requires"]
    assert await SystemdDependencies.process(host, "nginx") == {}


async def test_dependencies_gather_fact_forwards_the_unit():
    hosts = [
        SimulatedHost(name="web1", systemd_dependencies={"httpd.service": _DEPENDENCIES_FACT}),
        SimulatedHost(name="web2", systemd_dependencies={}),
    ]
    assert await gather_fact(hosts, SystemdDependencies, "httpd") == {
        "web1": _DEPENDENCIES_FACT,
        "web2": {},
    }


# --- namespace + top-level exports -------------------------------------------


def test_new_facts_are_exported_from_the_namespace_and_top_level():
    import reemote5

    for fact in (
        SystemdStatus, SystemdEnabled, SystemdUnitProperties, SystemdFailedUnits,
        SystemdTimers, SystemdUnitFiles, SystemdVersion, SystemdSystemState,
        SystemdDependencies,
    ):
        assert getattr(systemd, fact.__name__) is fact
        assert getattr(reemote5, fact.__name__) is fact
    # The entry TypedDicts are exported too (facts return them).
    for entry in ("SystemdFailedUnit", "SystemdTimer", "SystemdUnitFile", "SystemdVersionInfo"):
        assert entry in systemd.__all__
        assert entry in reemote5.__all__


# --- Phase D descriptors: rendering + elevation ------------------------------


def test_systemctl_command_mask_unmask_preset_rendering():
    assert (
        SystemctlCommand(action="mask", unit="httpd.service").shell()
        == "systemctl mask httpd.service"
    )
    assert (
        SystemctlCommand(action="unmask", unit="httpd.service").shell()
        == "systemctl unmask httpd.service"
    )
    assert (
        SystemctlCommand(action="preset", unit="httpd.service").shell()
        == "systemctl preset httpd.service"
    )
    assert SystemctlCommand(action="preset-all").shell() == "systemctl preset-all"
    assert (
        SystemctlCommand(action="mask", unit="httpd.service", user_mode=True).shell()
        == "systemctl --user mask httpd.service"
    )


def test_systemd_file_upload_command_is_an_unprivileged_transfer():
    cmd = SystemdFileUploadCommand(content="[Service]\n", dest="/tmp/reemote5-systemd.conf")
    # A transfer has no shell(); command_shell falls back to __str__ and never
    # adds the sudo prefix (transfers ride as the login user).
    assert str(cmd) == "scp <systemd conf> -> /tmp/reemote5-systemd.conf"
    assert command_shell(cmd, sudo=True) == "scp <systemd conf> -> /tmp/reemote5-systemd.conf"


def test_systemd_file_install_command_renders_the_elevated_sequence():
    cmd = SystemdFileInstallCommand(
        spool="/tmp/reemote5-systemd.conf",
        dest="/etc/systemd/system/httpd.service.d/override.conf",
    )
    inner = (
        "mkdir -p /etc/systemd/system/httpd.service.d && "
        "cp /tmp/reemote5-systemd.conf /etc/systemd/system/httpd.service.d/override.conf && "
        "chmod 0644 /etc/systemd/system/httpd.service.d/override.conf && "
        "rm -f /tmp/reemote5-systemd.conf"
    )
    assert cmd.shell() == f"sh -c '{inner}'"
    assert command_shell(cmd, sudo=True) == f"sudo sh -c '{inner}'"


def test_systemd_file_remove_and_apply_commands_render_and_elevate():
    remove = SystemdFileRemoveCommand(path="/etc/tmpfiles.d/myapp.conf")
    assert remove.shell() == "rm -f /etc/tmpfiles.d/myapp.conf"
    assert command_shell(remove, sudo=True) == "sudo rm -f /etc/tmpfiles.d/myapp.conf"
    tmpfiles = SystemdTmpfilesCommand(path="/etc/tmpfiles.d/myapp.conf")
    assert tmpfiles.shell() == "systemd-tmpfiles --create /etc/tmpfiles.d/myapp.conf"
    assert (
        command_shell(tmpfiles, sudo=True)
        == "sudo systemd-tmpfiles --create /etc/tmpfiles.d/myapp.conf"
    )
    sysusers = SystemdSysusersCommand(path="/etc/sysusers.d/myapp.conf")
    assert sysusers.shell() == "systemd-sysusers /etc/sysusers.d/myapp.conf"
    assert (
        command_shell(sysusers, sudo=True)
        == "sudo systemd-sysusers /etc/sysusers.d/myapp.conf"
    )


# --- _SystemdConfFile: the private content fact ------------------------------


def test_systemd_conf_file_command_guards_with_a_marker():
    assert _SystemdConfFile.command("/etc/tmpfiles.d/myapp.conf") == (
        "test -e /etc/tmpfiles.d/myapp.conf && "
        "{ printf '#reemote5-systemd-conf\\n'; cat -- /etc/tmpfiles.d/myapp.conf; }"
    )


async def test_systemd_conf_file_parses_content_absent_and_empty():
    # Present with content: the marker prefix is stripped.
    host = CannedShellHost("#reemote5-systemd-conf\nd /run/myapp 0755\n")
    assert await _SystemdConfFile.process(host, "/x") == "d /run/myapp 0755\n"
    # Present but empty: marker only -> "" (distinguishable from absent).
    assert await _SystemdConfFile.process(CannedShellHost("#reemote5-systemd-conf\n"), "/x") == ""
    # Absent: no marker -> None.
    assert await _SystemdConfFile.process(CannedShellHost(""), "/x") is None


async def test_systemd_conf_file_reads_the_remote_files_seam():
    host = SimulatedHost(name="h", remote_files={"/etc/tmpfiles.d/myapp.conf": b"d /run/x\n"})
    assert await _SystemdConfFile.process(host, "/etc/tmpfiles.d/myapp.conf") == "d /run/x\n"
    assert await _SystemdConfFile.process(host, "/etc/tmpfiles.d/absent.conf") is None


# --- mask --------------------------------------------------------------------


async def test_mask_masks_an_unmasked_unit():
    host = SimulatedHost(
        name="h", systemd_unit_properties={"httpd.service": {"UnitFileState": "disabled"}}
    )
    ctx, commands = await materialize(host, op(systemd.mask, unit="httpd"))
    assert ctx.changed
    assert "will mask" in ctx.reason
    assert commands == [SystemctlCommand(action="mask", unit="httpd.service")]


async def test_mask_no_change_when_already_masked():
    host = SimulatedHost(
        name="h", systemd_unit_properties={"httpd.service": {"UnitFileState": "masked"}}
    )
    ctx, commands = await materialize(host, op(systemd.mask, unit="httpd"))
    assert not ctx.changed
    assert ctx.reason == "httpd.service already masked"
    assert commands == []


async def test_mask_unmasks_a_masked_unit():
    host = SimulatedHost(
        name="h", systemd_unit_properties={"httpd.service": {"UnitFileState": "masked"}}
    )
    ctx, commands = await materialize(host, op(systemd.mask, unit="httpd", masked=False))
    assert ctx.changed
    assert commands == [SystemctlCommand(action="unmask", unit="httpd.service")]


async def test_mask_unmask_no_change_when_not_masked():
    host = SimulatedHost(name="h", systemd_unit_properties={})
    ctx, commands = await materialize(host, op(systemd.mask, unit="httpd", masked=False))
    assert not ctx.changed
    assert ctx.reason == "httpd.service already unmasked"
    assert commands == []


async def test_mask_forwards_scope_to_fact_and_descriptor():
    host = SimulatedHost(
        name="h", systemd_unit_properties={"httpd.service": {"UnitFileState": "disabled"}}
    )
    _ctx, commands = await materialize(host, op(systemd.mask, unit="httpd", user_mode=True))
    assert commands == [SystemctlCommand(action="mask", unit="httpd.service", user_mode=True)]
    assert command_shell(commands[0]) == "systemctl --user mask httpd.service"


async def test_mask_mutates_both_unit_file_stores():
    host = SimulatedHost(
        name="h", systemd_unit_properties={"httpd.service": {"UnitFileState": "disabled"}}
    )
    host.execute_command(SystemctlCommand(action="mask", unit="httpd.service"))
    assert host.systemd_unit_properties["httpd.service"]["UnitFileState"] == "masked"
    assert host.systemd_unit_files["httpd.service"]["state"] == "masked"
    # A second mask is now a no-op (the idempotence read sees the mutation).
    ctx, commands = await materialize(host, op(systemd.mask, unit="httpd"))
    assert not ctx.changed
    assert commands == []
    host.execute_command(SystemctlCommand(action="unmask", unit="httpd.service"))
    assert host.systemd_unit_files["httpd.service"]["state"] == "disabled"


# --- reset_failed ------------------------------------------------------------


async def test_reset_failed_clears_a_failed_unit():
    host = SimulatedHost(name="h", services={"httpd.service": "failed"})
    ctx, commands = await materialize(host, op(systemd.reset_failed, unit="httpd"))
    assert ctx.changed
    assert "will reset-failed" in ctx.reason
    assert commands == [SystemctlCommand(action="reset-failed", unit="httpd.service")]


async def test_reset_failed_no_change_when_not_failed():
    host = SimulatedHost(name="h", services={"httpd.service": "active"})
    ctx, commands = await materialize(host, op(systemd.reset_failed, unit="httpd"))
    assert not ctx.changed
    assert commands == []


async def test_reset_failed_mutates_the_services_seam():
    host = SimulatedHost(name="h", services={"httpd.service": "failed"})
    host.execute_command(SystemctlCommand(action="reset-failed", unit="httpd.service"))
    assert host.services["httpd.service"] == "inactive"
    ctx, commands = await materialize(host, op(systemd.reset_failed, unit="httpd"))
    assert not ctx.changed
    assert commands == []


# --- dropin / tmpfiles / sysusers --------------------------------------------

_SPOOL = "/tmp/reemote5-systemd.conf"
_DROPIN_CONTENT = "[Service]\nEnvironment=FOO=bar\n"
_DROPIN_PATH = "/etc/systemd/system/httpd.service.d/override.conf"
_TMPFILES_CONTENT = "d /run/myapp 0755 root root -\n"
_TMPFILES_PATH = "/etc/tmpfiles.d/myapp.conf"
_SYSUSERS_CONTENT = 'u myapp - "My App" /nonexistent /sbin/nologin\n'
_SYSUSERS_PATH = "/etc/sysusers.d/myapp.conf"


async def test_dropin_writes_when_absent_then_reloads():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host,
        op(systemd.dropin, unit="httpd", filename="override", content=_DROPIN_CONTENT),
    )
    assert ctx.changed
    assert commands == [
        SystemdFileUploadCommand(content=_DROPIN_CONTENT, dest=_SPOOL),
        SystemdFileInstallCommand(spool=_SPOOL, dest=_DROPIN_PATH),
        SystemctlCommand(action="daemon-reload"),
    ]


async def test_dropin_appends_conf_and_normalizes_the_unit():
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize(
        host,
        op(systemd.dropin, unit="httpd", filename="override.conf", content=_DROPIN_CONTENT),
    )
    # An explicit .conf is not doubled; the bare unit gains .service.
    assert commands[1] == SystemdFileInstallCommand(spool=_SPOOL, dest=_DROPIN_PATH)


async def test_dropin_no_change_when_content_matches():
    host = SimulatedHost(name="h", remote_files={_DROPIN_PATH: _DROPIN_CONTENT.encode()})
    ctx, commands = await materialize(
        host,
        op(systemd.dropin, unit="httpd", filename="override", content=_DROPIN_CONTENT),
    )
    assert not ctx.changed
    assert ctx.reason == f"drop-in already up to date ({_DROPIN_PATH})"
    assert commands == []


async def test_dropin_restart_and_daemon_reload_options():
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize(
        host,
        op(
            systemd.dropin, unit="httpd", filename="override", content=_DROPIN_CONTENT,
            restart=True,
        ),
    )
    assert commands[-1] == SystemctlCommand(action="restart", unit="httpd.service")
    assert SystemctlCommand(action="daemon-reload") in commands

    _ctx, commands = await materialize(
        host,
        op(
            systemd.dropin, unit="httpd", filename="override", content=_DROPIN_CONTENT,
            daemon_reload=False,
        ),
    )
    assert commands == [
        SystemdFileUploadCommand(content=_DROPIN_CONTENT, dest=_SPOOL),
        SystemdFileInstallCommand(spool=_SPOOL, dest=_DROPIN_PATH),
    ]


async def test_dropin_removes_when_absent_requested():
    host = SimulatedHost(name="h", remote_files={_DROPIN_PATH: _DROPIN_CONTENT.encode()})
    ctx, commands = await materialize(
        host,
        op(systemd.dropin, unit="httpd", filename="override", content="", present=False),
    )
    assert ctx.changed
    assert commands == [
        SystemdFileRemoveCommand(path=_DROPIN_PATH),
        SystemctlCommand(action="daemon-reload"),
    ]


async def test_dropin_absent_no_change_when_already_missing():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host,
        op(systemd.dropin, unit="httpd", filename="override", content="", present=False),
    )
    assert not ctx.changed
    assert commands == []


async def test_dropin_mutates_remote_files_and_is_idempotent():
    host = SimulatedHost(name="h")
    _ctx, commands = await materialize(
        host,
        op(systemd.dropin, unit="httpd", filename="override", content=_DROPIN_CONTENT),
    )
    for cmd in commands:
        host.execute_command(cmd)
    assert host.remote_files[_DROPIN_PATH] == _DROPIN_CONTENT.encode()
    assert _SPOOL not in host.remote_files  # the install removed the spool
    assert _DROPIN_PATH in host.existing_files
    # A fresh materialization now reads the written content and plans no change.
    ctx, commands = await materialize(
        host,
        op(systemd.dropin, unit="httpd", filename="override", content=_DROPIN_CONTENT),
    )
    assert not ctx.changed
    assert commands == []


async def test_tmpfiles_writes_and_applies():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, op(systemd.tmpfiles, filename="myapp", content=_TMPFILES_CONTENT)
    )
    assert ctx.changed
    assert commands == [
        SystemdFileUploadCommand(content=_TMPFILES_CONTENT, dest=_SPOOL),
        SystemdFileInstallCommand(spool=_SPOOL, dest=_TMPFILES_PATH),
        SystemdTmpfilesCommand(path=_TMPFILES_PATH),
    ]


async def test_tmpfiles_no_change_when_content_matches():
    host = SimulatedHost(name="h", remote_files={_TMPFILES_PATH: _TMPFILES_CONTENT.encode()})
    ctx, commands = await materialize(
        host, op(systemd.tmpfiles, filename="myapp", content=_TMPFILES_CONTENT)
    )
    assert not ctx.changed
    assert commands == []


async def test_tmpfiles_removes_the_file_only():
    host = SimulatedHost(name="h", remote_files={_TMPFILES_PATH: _TMPFILES_CONTENT.encode()})
    ctx, commands = await materialize(
        host, op(systemd.tmpfiles, filename="myapp", content="", present=False)
    )
    assert ctx.changed
    # No apply step on removal -- already-created runtime files are untouched.
    assert commands == [SystemdFileRemoveCommand(path=_TMPFILES_PATH)]


async def test_sysusers_writes_and_applies():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(
        host, op(systemd.sysusers, filename="myapp", content=_SYSUSERS_CONTENT)
    )
    assert ctx.changed
    assert commands == [
        SystemdFileUploadCommand(content=_SYSUSERS_CONTENT, dest=_SPOOL),
        SystemdFileInstallCommand(spool=_SPOOL, dest=_SYSUSERS_PATH),
        SystemdSysusersCommand(path=_SYSUSERS_PATH),
    ]


async def test_sysusers_no_change_when_content_matches():
    host = SimulatedHost(name="h", remote_files={_SYSUSERS_PATH: _SYSUSERS_CONTENT.encode()})
    ctx, commands = await materialize(
        host, op(systemd.sysusers, filename="myapp", content=_SYSUSERS_CONTENT)
    )
    assert not ctx.changed
    assert commands == []


async def test_sysusers_removes_the_file_only_not_the_users():
    host = SimulatedHost(name="h", remote_files={_SYSUSERS_PATH: _SYSUSERS_CONTENT.encode()})
    ctx, commands = await materialize(
        host, op(systemd.sysusers, filename="myapp", content="", present=False)
    )
    assert ctx.changed
    assert commands == [SystemdFileRemoveCommand(path=_SYSUSERS_PATH)]


# --- preset ------------------------------------------------------------------


async def test_preset_all_is_stateless():
    host = SimulatedHost(name="h")
    ctx, commands = await materialize(host, op(systemd.preset, all_units=True))
    assert ctx.changed
    assert ctx.reason == "preset-all"
    assert commands == [SystemctlCommand(action="preset-all")]


async def test_preset_applies_when_state_differs_from_policy():
    host = SimulatedHost(
        name="h", systemd_unit_files={"httpd.service": {"state": "disabled", "preset": "enabled"}}
    )
    ctx, commands = await materialize(host, op(systemd.preset, unit="httpd"))
    assert ctx.changed
    assert commands == [SystemctlCommand(action="preset", unit="httpd.service")]


async def test_preset_no_change_when_state_matches_policy():
    host = SimulatedHost(
        name="h", systemd_unit_files={"httpd.service": {"state": "enabled", "preset": "enabled"}}
    )
    ctx, commands = await materialize(host, op(systemd.preset, unit="httpd"))
    assert not ctx.changed
    assert commands == []


async def test_preset_no_policy_is_a_no_change():
    host = SimulatedHost(
        name="h", systemd_unit_files={"httpd.service": {"state": "disabled", "preset": ""}}
    )
    ctx, commands = await materialize(host, op(systemd.preset, unit="httpd"))
    assert not ctx.changed
    assert commands == []


async def test_preset_absent_unit_is_reported_not_preset_blindly():
    host = SimulatedHost(name="h", systemd_unit_files={})
    ctx, commands = await materialize(host, op(systemd.preset, unit="httpd"))
    assert not ctx.changed
    assert ctx.reason == "httpd.service not found in unit files"
    assert commands == []


async def test_preset_requires_a_unit_unless_all_units():
    host = SimulatedHost(name="h")
    with pytest.raises(ValueError):
        await materialize(host, op(systemd.preset))


async def test_preset_mutates_the_unit_file_stores_and_enable_set():
    host = SimulatedHost(
        name="h", systemd_unit_files={"httpd.service": {"state": "disabled", "preset": "enabled"}}
    )
    host.execute_command(SystemctlCommand(action="preset", unit="httpd.service"))
    assert host.systemd_unit_files["httpd.service"]["state"] == "enabled"
    assert host.systemd_unit_properties["httpd.service"]["UnitFileState"] == "enabled"
    assert "httpd.service" in host.enabled_services
    ctx, commands = await materialize(host, op(systemd.preset, unit="httpd"))
    assert not ctx.changed
    assert commands == []


async def test_preset_all_applies_every_units_policy():
    host = SimulatedHost(
        name="h",
        systemd_unit_files={
            "httpd.service": {"state": "disabled", "preset": "enabled"},
            "nginx.service": {"state": "enabled", "preset": "disabled"},
        },
    )
    host.execute_command(SystemctlCommand(action="preset-all"))
    assert host.systemd_unit_files["httpd.service"]["state"] == "enabled"
    assert host.systemd_unit_files["nginx.service"]["state"] == "disabled"
    assert host.enabled_services == {"httpd.service"}


# --- Phase D end-to-end: plan / apply / dry / lockstep / determinism ---------


def install_dropin(host: Host) -> Generator[OperationRequest]:
    """The example deploy: write an httpd drop-in, elevated."""
    yield op(
        systemd.dropin,
        name="Override httpd",
        unit="httpd",
        filename="override",
        content=_DROPIN_CONTENT,
        sudo=True,
    )


def empty_factory(entry: dict) -> Host:
    """``host_factory`` building SimulatedHosts with no config files yet."""
    return SimulatedHost.from_inventory(entry)


async def test_dropin_runs_across_hosts_in_lockstep():
    plans, report, hosts = await run(INVENTORY, install_dropin, host_factory=empty_factory)
    assert [p.changed for p in plans["web1"]] == [True]
    assert [p.changed for p in plans["web2"]] == [True]
    assert report is not None
    assert [e.name for e in report.log] == ["Override httpd", "Override httpd"]
    log = format_log(report)
    # The transfer is unprivileged (no sudo); the install + reload are elevated.
    assert log.count("scp <systemd conf> -> /tmp/reemote5-systemd.conf") == 2
    assert log.count("$ sudo sh -c 'mkdir -p /etc/systemd/system/httpd.service.d") == 2
    assert log.count("$ sudo systemctl daemon-reload") == 2
    for host in hosts:
        assert host.remote_files[_DROPIN_PATH] == _DROPIN_CONTENT.encode()
        assert _SPOOL not in host.remote_files
    # A fresh prepare against the now-written drop-in plans no change.
    for host in hosts:
        _runtimes, plans2 = await run_prepare([host], install_dropin)
        assert [p.changed for p in plans2[host.name]] == [False]


async def test_dropin_dry_run_sends_nothing():
    _plans, report, hosts = await run(
        INVENTORY, install_dropin, host_factory=empty_factory, dry=True
    )
    assert report is None
    for host in hosts:
        assert host.remote_files == {}


async def test_dropin_branching_on_live_state_raises_determinism_error():
    def live_branch(host: Host) -> Generator[OperationRequest]:
        yield op(
            systemd.dropin, name="Override", unit="httpd", filename="override",
            content=_DROPIN_CONTENT, sudo=True,
        )
        # The illegal branch: consult live state the first write mutates.
        if _DROPIN_PATH in host.remote_files:
            yield op(
                systemd.tmpfiles, name="Extra", filename="extra",
                content=_TMPFILES_CONTENT, sudo=True,
            )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], live_branch)
    assert [p.request.name for p in plans["h"]] == ["Override"]
    with pytest.raises(DeterminismError):
        await run_apply(runtimes, plans=plans)


def mask_httpd(host: Host) -> Generator[OperationRequest]:
    """The example deploy: mask httpd, elevated."""
    yield op(systemd.mask, name="Mask httpd", unit="httpd", sudo=True)


async def test_mask_runs_elevated_in_lockstep():
    def factory(entry: dict) -> Host:
        host = SimulatedHost.from_inventory(entry)
        host.systemd_unit_properties = {"httpd.service": {"UnitFileState": "disabled"}}
        return host

    _plans, report, hosts = await run(INVENTORY, mask_httpd, host_factory=factory)
    assert report is not None
    log = format_log(report)
    assert log.count("$ sudo systemctl mask httpd.service") == 2
    for host in hosts:
        assert host.systemd_unit_files["httpd.service"]["state"] == "masked"


# --- Phase D exports ---------------------------------------------------------


def test_phase_d_descriptors_and_operations_exports():
    import reemote5

    # Descriptors are exported from the namespace and the top level.
    for descriptor in (
        SystemdFileUploadCommand, SystemdFileInstallCommand, SystemdFileRemoveCommand,
        SystemdTmpfilesCommand, SystemdSysusersCommand,
    ):
        assert getattr(systemd, descriptor.__name__) is descriptor
        assert getattr(reemote5, descriptor.__name__) is descriptor
    # Operations stay namespace-only (never re-exported at the top level).
    for operation in ("mask", "reset_failed", "dropin", "tmpfiles", "sysusers", "preset"):
        assert operation in systemd.__all__
        assert hasattr(systemd, operation)
        assert operation not in reemote5.__all__
    # The private content fact is an internal helper, not part of the surface.
    assert "_SystemdConfFile" not in systemd.__all__
    assert not hasattr(reemote5, "_SystemdConfFile")