# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``supervisor`` role (§17.5).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, supervisor(v), host_factory=...)``. The factory is plain
(non-``async``) -- every input is a resolved variable or a frozen constant --
so it is called, not awaited.

The load-bearing assertion is the migration's shape claim: the stream is **two
waves** and the checkpoint is the **only barrier** -- the configure requests,
then ``checkpoint("configure done")`` closing that wave, then the handler wave
for the notified hosts. No ``fact``, no ``act``, no ``control``: the plan is
entirely static (every template input is a resolved variable or a frozen
constant, so all three renders are pure control-side Python inside the body),
and a speculative barrier would break the shape test, which reads the records
straight out of ``report.records``. On a converged rerun nothing notifies, so
the second request wave never appears at all (one wave, not two), and the
state-gated daemon-reload (issue #50) emits no command either.

**No between-pass bridge is needed**: the simulation's own mutators maintain
every seam the second pass reads -- ``install_pip_packages`` records the
package, ``make_directory``/``chmod_path`` the ``0755`` directory rows,
``install_file`` the three rendered files' content and full stat rows and marks
the unit-file install reload-pending, and the ``SystemctlCommand`` arms the
service state and enablement (and clears the pending set). The managed files are
all root:root ``0644`` -- readable by the unprivileged login -- so unlike the
setgid Redis config the idempotence read-back needs no elevated traversal gate.

The three rendered files need no pre-seed: ``files.install``'s spool-and-install
lands the content and the content-idempotent write converges on the second pass.
The drift tests do pre-seed them (stale content) to drive the notification paths
the main config, the program config and the unit file each own.
"""

import pytest
from pydantic import ValidationError

from reemote import run_waves
from reemote.roles import SupervisorVars, supervisor
from reemote.roles.supervisor import (
    BIN_PATH,
    CONF_DIR_NAME,
    CONF_FILE_NAME,
    CONF_MODE,
    CTL_BIN_PATH,
    DIR_MODE,
    SERVICE,
    UNIT_PATH,
    Program,
    SupervisorPlan,
    ansible_hash,
    conf_d_path,
    derive_include_files,
    notifying_names,
    render_program_conf,
    render_supervisord_conf,
    render_unit_conf,
    supervisor_plan,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from reemote.stages import CheckpointRequest, FactRequest
from tests.simulated_host import SimulatedHost

#: The handler request keeps ``restart_handler``'s default label, not a name of
#: the role's own -- the notification is read from the notifying request names,
#: the handler logs under the library's generated one.
HANDLER_NAME = "Restart supervisord (handler)"

#: ``templates/supervisord.conf.j2`` rendered with every default, byte-for-byte:
#: the ``[supervisord]`` block, the enabled unix http server with its
#: ``{SHA}``-hashed password and its ``[supervisorctl]`` partner, the derived
#: ``conf.d`` include glob, and the rpc interface. The false inet-server guard
#: contributes nothing. Verified against the module's own render (so a template
#: or environment drift cannot hide).
DEFAULT_CONFIG = """\
[supervisord]
nodaemon = False
childlogdir = /var/log/supervisor
user = root
pidfile = /var/run/supervisord.pid

[unix_http_server]
file = /var/run/supervisor.sock
chown = root
username = root
password = {SHA}33a5c3270038edc370f609b9917575b9342e3286

[supervisorctl]
serverurl = unix:///var/run/supervisor.sock
username = root
password = my_secret_password

[include]
files = /etc/supervisor/conf.d/*.conf

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface
"""

#: ``templates/supervisord.unit.j2`` rendered with the frozen Debian bin paths
#: (D2) and the default config path.
DEFAULT_UNIT = """\
# supervisord service for systemd
# Originally by ET-CS (https://github.com/ET-CS)
[Unit]
Description=Supervisor daemon

[Service]
Type=forking
ExecStart=/usr/local/bin/supervisord -c /etc/supervisor/supervisord.conf
ExecStop=/usr/local/bin/supervisorctl $OPTIONS shutdown
ExecReload=/usr/local/bin/supervisorctl $OPTIONS reload
KillMode=process
Restart=on-failure
RestartSec=42s

[Install]
WantedBy=multi-user.target
"""

CONF_PATH = "/etc/supervisor/supervisord.conf"
CONF_D = "/etc/supervisor/conf.d"
DEMO_CONF = f"{CONF_D}/demo.conf"


def make_inventory(**overrides) -> list[dict]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride in the entry's ``roles`` mapping under
    ``"supervisor"``, bound by ``resolve_vars``. With no overrides every
    ``defaults/main.yml`` value applies, but the one ``demo`` program makes the
    first pass demonstrably write ``conf.d/demo.conf`` and demonstrably notify.
    """
    entry = {
        "host": "ubuntu",
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.10",
            "username": "supervisor",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "ubuntu",
        "tags": ["ubuntu_host", "apt_package_manager"],
        "roles": {
            "supervisor": {
                "programs": [
                    {
                        "name": "demo",
                        "command": "sleep 3600",
                        "configuration": "autostart=true\nautorestart=true",
                    },
                ],
            },
        },
    }
    for key, value in overrides.items():
        if key == "programs":
            entry["roles"]["supervisor"]["programs"] = list(value)
        else:
            entry["roles"]["supervisor"][key] = value
    return [
        entry,
        {
            "host": "web1",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
        },
    ]


def seed(host: SimulatedHost) -> None:
    """Pre-state: a stock minimal Ubuntu machine with no Supervisor installed.

    Nothing supervisor-related exists yet: no pip package, no directory rows, no
    config files, the service is neither running nor enabled, and no reload is
    pending. The pip store is zeroed (the role installs through ``pip.packages``,
    D7) so a stray pip command shows up; the apt cache is irrelevant here.
    """
    host.pip_packages = {}
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.directory_stats = {}
    host.services = {}
    host.enabled_services = set()
    host.systemd_reload_pending = set()
    host.os_groups = ["root", "sudo"]
    host.os_users = {}


def make_hosts(inventory: list[dict]) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(host)
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict], hosts: dict[str, SimulatedHost], *, dry: bool = False
):
    """Resolve variables, build the sync factory's deploy, and run one wave pass."""
    v = resolve_vars(SupervisorVars, "supervisor", inventory)

    def factory(entry: dict) -> SimulatedHost:
        # The *same* host object every pass, so state survives the barriers.
        return hosts[entry["host"]]

    # A plain (non-async) factory: called, not awaited.
    deploy = supervisor(v)
    report, _hosts = await run_waves(
        inventory, deploy, host_factory=factory, dry=dry
    )
    return report


def changed_entries(report) -> list:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def ubuntu_names(report) -> set[str]:
    """Every Ubuntu log entry name of one pass."""
    return {entry.name for entry in report.merged().log if entry.host == "ubuntu"}


def ubuntu_log(report) -> dict[str, object]:
    """The Ubuntu host's log entries of one pass, keyed by name."""
    return {
        entry.name: entry for entry in report.merged().log if entry.host == "ubuntu"
    }


def _kind(barrier) -> tuple:
    """Classify one barrier record for the shape assertion.

    A ``fact`` barrier names the fact class it reads; a ``checkpoint`` names
    itself. An ``act``/``control`` barrier would fall through to a distinct
    label, so a speculatively-added barrier breaks the shape test rather than
    hiding.
    """
    if isinstance(barrier, FactRequest):
        return ("fact", barrier.fact_cls)
    if isinstance(barrier, CheckpointRequest):
        return ("checkpoint", barrier.name)
    return ("other", type(barrier).__name__)


def _barrier_shape(report):
    """The ubuntu host's ordered ``(wave, kind)`` barrier list for one pass."""
    ubuntu = [r for r in report.records if r.host == "ubuntu"]
    return [(r.wave, _kind(r.barrier)) for r in ubuntu if r.barrier is not None]


def _request_waves(report) -> set[int]:
    """The set of wave numbers that carried at least one ubuntu request."""
    return {
        r.wave
        for r in report.records
        if r.host == "ubuntu" and r.log is not None
    }


# --- the stream shape ---------------------------------------------------------


async def test_stream_is_one_checkpoint_and_two_waves():
    """The role's shape: the requests, one checkpoint, then the handler wave.

    Exactly one ``checkpoint`` barrier and **no fact/act/control at all** -- the
    plan is entirely static, so anything else would be a barrier added
    speculatively. Requests land in two waves (configure 0, handler 1).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert _barrier_shape(report) == [(0, ("checkpoint", "configure done"))]
    assert _request_waves(report) == {0, 1}

    # The rerun re-derives the same barrier; nothing notifies, so no second
    # request wave appears at all.
    second = await run_role(inventory, hosts)
    assert _barrier_shape(second) == _barrier_shape(report)
    assert _request_waves(second) == {0}


async def test_configure_requests_follow_tasks_order():
    """The single wave reproduces ``tasks/main.yml`` exactly, then the handler.

    package, log dir, the two config paths, the main config, the program config,
    the unit file, the service -- then the ``restart supervisor`` handler on the
    notified host.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    wave0 = [entry.name for entry in report.merged().log if entry.host == "ubuntu"]
    assert wave0 == [
        "Ensure Supervisor is installed",
        "Ensure Supervisor log dir exists",
        "Ensure Supervisor config paths exist (/etc/supervisor)",
        f"Ensure Supervisor config paths exist ({CONF_D})",
        "Ensure Supervisor configuration is present",
        "Ensure Supervisor program config is present (demo)",
        "Ensure Supervisor systemd unit is present",
        "Ensure Supervisor is started and enabled",
        HANDLER_NAME,
    ]


# --- the first pass really deploys the role -----------------------------------


async def test_first_pass_installs_configures_and_notifies():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    # The pip package (D7: through the pip executable, not apt).
    assert "supervisor" in host.pip_packages

    # The three directories, root:root 0755.
    assert host.directory_stats["/var/log/supervisor"] == {
        "mode": 755,
        "user": "root",
        "group": "root",
    }
    assert host.directory_stats["/etc/supervisor"] == {
        "mode": 755,
        "user": "root",
        "group": "root",
    }
    assert host.directory_stats[CONF_D] == {
        "mode": 755,
        "user": "root",
        "group": "root",
    }

    # The three rendered files: default content, root:root 0644, full stat rows.
    assert host.remote_files[CONF_PATH] == DEFAULT_CONFIG.encode()
    assert host.file_stats[CONF_PATH] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(DEFAULT_CONFIG.encode()),
    }
    assert host.remote_files[DEMO_CONF] == (
        b"[program:demo]\ncommand=sleep 3600\nautostart=true\nautorestart=true\n"
    )
    assert host.remote_files[UNIT_PATH] == DEFAULT_UNIT.encode()

    # The service is started and enabled (the final request, one request D5).
    assert host.services["supervisord.service"] == "active"
    assert "supervisord.service" in host.enabled_services

    # A freshly written unit file marks the reload pending, so the service
    # request runs the state-gated daemon-reload (D6, #50) which clears it.
    log = ubuntu_log(report)
    assert "daemon-reload" in log["Ensure Supervisor is started and enabled"].reason
    assert host.systemd_reload_pending == set()

    # The checkpoint read the config change, so the handler fired on this host.
    assert log["Ensure Supervisor configuration is present"].changed
    assert log[HANDLER_NAME].changed
    assert log[HANDLER_NAME].host == "ubuntu"

    # The whole migration is operations: no shell action ever ran.
    assert host.ran_shells == []


async def test_second_pass_is_a_noop_with_no_handler_wave():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    # The migration's idempotence claim: nothing changed anywhere, no handler
    # ran, and the state-gated daemon-reload emitted no command -- package
    # present, directories 0755, all three files content-equal with the right
    # metadata, service running and enabled, so nobody notified.
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert HANDLER_NAME not in ubuntu_names(second)
    assert hosts["ubuntu"].ran_shells == []
    # No second reload either.
    assert hosts["ubuntu"].systemd_reload_pending == set()


async def test_non_ubuntu_host_is_never_touched():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    skipped = hosts["web1"]

    # The family guard returns before the first yield, so the off-target host is
    # in no wave, emits no log line and its stores never move.
    assert not any(entry.host == "web1" for entry in report.merged().log)
    assert not any(r.host == "web1" for r in report.records)
    assert skipped.remote_files == {}
    assert skipped.pip_packages == {}
    assert skipped.services == {}
    assert skipped.ran_shells == []


async def test_dry_preview_plans_the_requests_and_parks_at_the_checkpoint():
    """``dry=True`` never runs a barrier, so the handler wave stays unplanned.

    The role's first barrier is the checkpoint *after* the request wave, so the
    dry preview plans all eight configure requests (visible in the tree) and
    stops there. Nothing is applied and no notification is reported.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts, dry=True)

    assert host.pip_packages == {}
    assert host.remote_files == {}
    assert host.directory_stats == {}
    assert host.services == {}
    assert changed_entries(report) == []
    assert _barrier_shape(report) == [(0, ("checkpoint", "configure done"))]
    assert _request_waves(report) == set()
    assert report.merged().log == []


# --- the notification paths ---------------------------------------------------


async def test_main_config_drift_reinstalls_and_notifies():
    """Stale main config: the install request rewrites it and queues the restart."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    _preconverge(host)
    host.remote_files[CONF_PATH] = b"# stale\n"
    host.existing_files.add(CONF_PATH)
    host.file_stats[CONF_PATH] = {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(b"# stale\n"),
    }

    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)

    assert log["Ensure Supervisor configuration is present"].changed
    assert log[HANDLER_NAME].changed
    assert host.remote_files[CONF_PATH] == DEFAULT_CONFIG.encode()

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_program_config_drift_notifies():
    """A drifted present-program config re-installs and queues the restart."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    _preconverge(host)
    host.remote_files[DEMO_CONF] = b"[program:demo]\ncommand=old\n"
    host.existing_files.add(DEMO_CONF)
    host.file_stats[DEMO_CONF] = {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(b"[program:demo]\ncommand=old\n"),
    }

    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)

    assert log["Ensure Supervisor program config is present (demo)"].changed
    assert log[HANDLER_NAME].changed
    assert host.remote_files[DEMO_CONF].decode().endswith("autorestart=true\n")


async def test_unit_drift_notifies_and_reloads():
    """The unit file is a notifier (when written) and its change re-gates reload.

    A stale ``supervisord.service`` re-installs (which re-marks the reload
    pending, so the service request daemon-reloads again) and queues the restart.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    _preconverge(host)
    host.remote_files[UNIT_PATH] = b"[Unit]\nDescription=stale\n"
    host.existing_files.add(UNIT_PATH)
    host.file_stats[UNIT_PATH] = {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(b"[Unit]\nDescription=stale\n"),
    }

    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)

    assert log["Ensure Supervisor systemd unit is present"].changed
    # The install re-marked the reload pending, so the service request reloads.
    assert "daemon-reload" in log["Ensure Supervisor is started and enabled"].reason
    assert log[HANDLER_NAME].changed
    assert host.remote_files[UNIT_PATH] == DEFAULT_UNIT.encode()


async def test_absent_program_removes_and_notifies():
    """The ``absent`` direction: ``files.file present=False`` removes and notifies."""
    inventory = make_inventory(
        programs=[{"name": "ghost", "state": "absent"}],
    )
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    # A stale conf.d/ghost.conf to be removed.
    ghost = f"{CONF_D}/ghost.conf"
    host.remote_files[ghost] = b"[program:ghost]\ncommand=x\n"
    host.existing_files.add(ghost)
    host.file_stats[ghost] = {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": 2,
    }

    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)

    assert log["Ensure Supervisor program config is absent (ghost)"].changed
    assert ghost not in host.remote_files
    assert log[HANDLER_NAME].changed


async def test_directory_drift_converges_without_notifying():
    """A ``0750`` log-dir drift is repaired, but directories carry no ``notify``.

    Only the main config, the program configs and the unit file queue
    ``restart supervisor``; the changed directory entry must not reach it.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    _preconverge(host)
    host.directory_stats["/var/log/supervisor"] = {
        "mode": 750,
        "user": "root",
        "group": "root",
    }

    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)

    assert log["Ensure Supervisor log dir exists"].changed
    assert host.directory_stats["/var/log/supervisor"]["mode"] == 755
    assert HANDLER_NAME not in ubuntu_names(report)

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the gate branches --------------------------------------------------------


async def test_version_pin_rides_the_package_spec():
    """``supervisor_version`` pins ``supervisor==<version>`` (the ``default(omit)``)."""
    inventory = make_inventory(version="4.2.5")
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    plan = supervisor_plan(SupervisorVars(version="4.2.5"))
    assert plan.package_specs == ("supervisor==4.2.5",)
    # The install command carries the pin.
    entry = ubuntu_log(report)["Ensure Supervisor is installed"]
    assert entry.changed

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_started_false_disables_the_handler_but_writes_the_unit():
    """``supervisor_started: false`` -> ``running=None`` and the handler gate (D8)."""
    inventory = make_inventory(started=False, enabled=True)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    report = await run_role(inventory, hosts)

    # The unit is still written (write_unit = started or enabled), but the
    # service request only enables, never starts, and the handler is skipped.
    assert host.remote_files[UNIT_PATH] == DEFAULT_UNIT.encode()
    assert "supervisord.service" in host.enabled_services
    assert host.services.get("supervisord.service") != "active"
    assert HANDLER_NAME not in ubuntu_names(report)

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_enabled_false_leaves_the_unit_disabled_but_running():
    """``supervisor_enabled: false`` -> ``enabled=None``, the service still runs."""
    inventory = make_inventory(enabled=False)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    await run_role(inventory, hosts)

    assert host.services["supervisord.service"] == "active"
    assert "supervisord.service" not in host.enabled_services

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_no_gates_scopes_out_the_unit_request():
    """``started`` and ``enabled`` both false -> ``write_unit`` false -> no unit."""
    inventory = make_inventory(started=False, enabled=False)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    report = await run_role(inventory, hosts)

    assert UNIT_PATH not in host.remote_files
    assert "Ensure Supervisor systemd unit is present" not in ubuntu_names(report)
    # And the unit name is not a notifier when it is not written.
    plan = supervisor_plan(SupervisorVars(started=False, enabled=False))
    assert "Ensure Supervisor systemd unit is present" not in notifying_names(plan)


# --- the notification channel -------------------------------------------------


def test_notifying_names_enumerate_the_four_tasks():
    """Only the config, program and unit names notify; pip/dir/service do not."""
    plan = supervisor_plan(
        SupervisorVars(
            programs=(
                Program(name="keep", command="run"),
                Program(name="drop", state="absent"),
            )
        )
    )
    names = notifying_names(plan)
    assert names == (
        "Ensure Supervisor configuration is present",
        "Ensure Supervisor program config is present (keep)",
        "Ensure Supervisor program config is absent (drop)",
        "Ensure Supervisor systemd unit is present",
    )
    assert "Ensure Supervisor is installed" not in names
    assert "Ensure Supervisor log dir exists" not in names
    assert "Ensure Supervisor is started and enabled" not in names


def test_frozen_constants_are_the_debian_vars_table():
    """``vars/Debian.yml`` collapsed to constants (D2), plus the task modes."""
    assert BIN_PATH == "/usr/local/bin/supervisord"
    assert CTL_BIN_PATH == "/usr/local/bin/supervisorctl"
    assert SERVICE == "supervisord"
    assert UNIT_PATH == "/etc/systemd/system/supervisord.service"
    assert CONF_DIR_NAME == "conf.d"
    assert CONF_FILE_NAME == "supervisord.conf"
    assert DIR_MODE == "0755"
    assert CONF_MODE == "0644"


# --- the pure helpers, unit-tested directly -----------------------------------


def test_render_is_the_template_defaults():
    """The Jinja2 render is the template source with every default filled."""
    assert render_supervisord_conf(SupervisorVars()) == DEFAULT_CONFIG
    assert render_unit_conf(SupervisorVars()) == DEFAULT_UNIT


def test_render_inet_branch_and_both_server_quirk():
    """Each http-server guard fills its branch; both keeps two ``[supervisorctl]``."""
    inet_only = render_supervisord_conf(
        SupervisorVars(unix_http_server_enable=False, inet_http_server_enable=True)
    )
    assert "[inet_http_server]\nport = *:9001\n" in inet_only
    assert "serverurl = http://localhost:9001\n" in inet_only
    assert "[unix_http_server]" not in inet_only

    both = render_supervisord_conf(
        SupervisorVars(
            inet_http_server_enable=True, inet_http_server_port="127.0.0.1:9001"
        )
    )
    # The upstream quirk: two ``[supervisorctl]`` sections, one per server.
    assert both.count("[supervisorctl]") == 2
    assert "serverurl = http://127.0.0.1:9001\n" in both


def test_render_program_config_defined_gate():
    """An unset ``configuration`` stays undefined so the guard drops the block."""
    with_cfg = render_program_conf(
        Program(name="demo", command="sleep 3600",
                configuration="autostart=true\nautorestart=true")
    )
    assert with_cfg == (
        "[program:demo]\ncommand=sleep 3600\nautostart=true\nautorestart=true\n"
    )
    bare = render_program_conf(Program(name="x", command="true"))
    assert bare == "[program:x]\ncommand=true\n"


def test_ansible_hash_matches_hashlib_sha1():
    """The registered ``hash`` filter is ``hashlib``'s hexdigest (D4)."""
    assert ansible_hash("my_secret_password", "sha1") == (
        "33a5c3270038edc370f609b9917575b9342e3286"
    )
    assert "{SHA}" + ansible_hash("pw", "sha1") in render_supervisord_conf(
        SupervisorVars(password="pw")
    )


def test_derive_include_files_follows_config_path():
    """An empty include derives from the config path; an override wins."""
    assert derive_include_files(SupervisorVars()) == "/etc/supervisor/conf.d/*.conf"
    assert (
        derive_include_files(SupervisorVars(config_path="/opt/sup"))
        == "/opt/sup/conf.d/*.conf"
    )
    assert (
        derive_include_files(SupervisorVars(include_files="/x/*.conf"))
        == "/x/*.conf"
    )


def test_conf_d_path_and_plan_is_frozen_hashable():
    """The plan is the three renders plus the gates, hashable (design §8.1)."""
    plan = supervisor_plan(SupervisorVars(config_path="/opt/sup"))
    assert isinstance(plan, SupervisorPlan)
    assert conf_d_path(plan) == "/opt/sup/conf.d"
    assert plan.config_content == render_supervisord_conf(
        SupervisorVars(config_path="/opt/sup")
    )
    assert plan.write_unit is True
    assert hash(plan) is not None


def test_model_is_frozen_and_hashable():
    v = SupervisorVars()
    assert hash(v) is not None
    with pytest.raises(ValidationError):
        v.config_path = "/other"


def test_present_program_requires_a_command():
    """``state: present`` (the default) fails fast without a command; ``absent`` is exempt."""
    with pytest.raises(ValidationError, match="command"):
        Program(name="bad")
    # An absent item needs only its name.
    assert Program(name="gone", state="absent").command == ""


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"config_path": ""}, "non-empty"),
        ({"log_dir": ""}, "non-empty"),
        ({"user": ""}, "non-empty"),
        ({"pip": ""}, "non-empty"),
        ({"unix_http_server_socket_path": ""}, "socket path"),
        ({"version": 4.2}, "version"),
    ],
)
def test_invalid_role_values_fail_fast(overrides, match):
    """The role's implicit type gates are pydantic rejections now."""
    inventory = make_inventory(**overrides)
    with pytest.raises(ValidationError, match=match):
        resolve_vars(SupervisorVars, "supervisor", inventory)


def test_inventory_role_variables_bind_and_typos_raise():
    """The nested mapping binds bare names; a typo raises RoleVarsError."""
    inventory = make_inventory(version="4.2.5", started=False)
    v = resolve_vars(SupervisorVars, "supervisor", inventory)
    assert v["ubuntu"].version == "4.2.5"
    assert v["ubuntu"].started is False
    # The programs list arrives as a tuple of hashable Program models.
    progs = resolve_vars(SupervisorVars, "supervisor", make_inventory())["ubuntu"].programs
    assert isinstance(progs, tuple)
    assert progs[0].name == "demo"
    assert hash(progs[0]) is not None

    with pytest.raises(RoleVarsError, match="supervisor_binpath"):
        resolve_vars(
            SupervisorVars, "supervisor", make_inventory(supervisor_binpath="x")
        )


# --- test-local seeding helpers -------------------------------------------------


def _preconverge(host: SimulatedHost) -> None:
    """Bring a fresh host to the fully-converged state without running the role.

    Used by the drift tests so exactly one file/directory is out of date and its
    notification is the only signal in the pass.
    """
    host.pip_packages = {"supervisor": ""}
    for path in ("/var/log/supervisor", "/etc/supervisor", CONF_D):
        host.directory_stats[path] = {"mode": 755, "user": "root", "group": "root"}
    for path, content in (
        (CONF_PATH, DEFAULT_CONFIG),
        (UNIT_PATH, DEFAULT_UNIT),
        (DEMO_CONF, "[program:demo]\ncommand=sleep 3600\nautostart=true\nautorestart=true\n"),
    ):
        host.remote_files[path] = content.encode()
        host.existing_files.add(path)
        host.file_stats[path] = {
            "user": "root",
            "group": "root",
            "mode": 644,
            "size": len(content.encode()),
        }
    host.services["supervisord.service"] = "active"
    host.enabled_services = {"supervisord.service"}
    host.systemd_reload_pending = set()
